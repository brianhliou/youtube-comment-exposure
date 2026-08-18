"""SQLite store for the comment corpus — schema, idempotent writes, checkpoints.

Design notes that the analysis depends on:

- **Every comment row records ``fetched_at``.** A like count is an observation at a
  moment, not a property of the text; the gap between ``video.published_at``,
  ``comment.published_at``, and ``fetched_at`` is the exposure variable the whole
  study turns on. Dropping it would make the corpus unanalysable.
- **Writes are idempotent by comment id.** A video interrupted mid-pagination is
  resumed from its stored page token, and re-fetching a page that was already stored
  must not duplicate or double-count anything.
- **Videos checkpoint only when completely enumerated.** Partial enumeration under
  ``order=time`` (newest first) would silently drop the *earliest* comments — exactly
  the high-exposure ones — so a video is either fully collected or not counted.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    video_id         TEXT PRIMARY KEY,
    channel_id       TEXT NOT NULL,
    channel_handle   TEXT,
    published_at     TEXT,
    title            TEXT,
    category_id      TEXT,
    duration         TEXT,
    view_count       INTEGER,
    like_count       INTEGER,
    comment_count    INTEGER,
    stats_fetched_at TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    status_reason    TEXT,
    page_token       TEXT,
    pages_fetched    INTEGER NOT NULL DEFAULT 0,
    comments_stored  INTEGER NOT NULL DEFAULT 0,
    completed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS comments (
    comment_id        TEXT PRIMARY KEY,
    video_id          TEXT NOT NULL,
    author_channel_id TEXT,
    text              TEXT NOT NULL,
    like_count        INTEGER NOT NULL,
    reply_count       INTEGER,
    published_at      TEXT NOT NULL,
    updated_at        TEXT,
    fetched_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id, published_at);

-- One page of `order=relevance` per completed video: the displayed ranking that
-- drives the like feedback loop. Without it, rank and quality are inseparable.
CREATE TABLE IF NOT EXISTS relevance_ranks (
    video_id     TEXT NOT NULL,
    comment_id   TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (video_id, comment_id)
);

CREATE TABLE IF NOT EXISTS quota_ledger (
    day_pt     TEXT PRIMARY KEY,
    units_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

# Statuses a video can hold. `skipped` records a durable fact (comments disabled,
# video deleted); `failed` records our own inability to finish and stays retryable.
PENDING = "pending"
IN_PROGRESS = "in_progress"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the corpus database with the schema applied."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def bind_population(conn: sqlite3.Connection, population_hash: str) -> None:
    """Pin this database to one frozen population, refusing a silent swap.

    Re-pointing a half-collected corpus at a different video list would redefine the
    experimental sample midway and leave no record that it happened.
    """
    existing = get_meta(conn, "population_hash")
    if existing and existing != population_hash:
        raise ValueError(
            f"database is bound to population {existing[:12]}, refusing to collect "
            f"against {population_hash[:12]} — use a fresh database file"
        )
    set_meta(conn, "population_hash", population_hash)
    conn.commit()


def upsert_videos(conn: sqlite3.Connection, videos: Iterable[dict[str, Any]]) -> int:
    """Insert the frozen video population. Existing rows keep their collection state."""
    n = 0
    for v in videos:
        conn.execute(
            """
            INSERT INTO videos (
                video_id, channel_id, channel_handle, published_at, title,
                category_id, duration, view_count, like_count, comment_count,
                stats_fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO NOTHING
            """,
            (
                v["video_id"],
                v["channel_id"],
                v.get("channel_handle"),
                v.get("published_at"),
                v.get("title"),
                v.get("category_id"),
                v.get("duration"),
                v.get("view_count"),
                v.get("like_count"),
                v.get("comment_count"),
                v.get("stats_fetched_at"),
            ),
        )
        n += 1
    conn.commit()
    return n


def next_video(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The next video to work on: resume anything in progress before starting new work.

    Ordered deterministically so a restart picks up the same queue.
    """
    return conn.execute(
        """
        SELECT * FROM videos
        WHERE status IN (?, ?)
        ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, comment_count, video_id
        LIMIT 1
        """,
        (IN_PROGRESS, PENDING, IN_PROGRESS),
    ).fetchone()


def store_comments(
    conn: sqlite3.Connection, video_id: str, comments: Iterable[dict[str, Any]], fetched_at: str
) -> int:
    """Write a page of comments. Returns the number of rows that were new.

    Re-storing a page is a no-op on counts: the row is refreshed with the newer
    observation, but ``comments_stored`` only moves for ids not already present.
    """
    new = 0
    for c in comments:
        cur = conn.execute(
            "SELECT 1 FROM comments WHERE comment_id = ?", (c["comment_id"],)
        ).fetchone()
        if cur is None:
            new += 1
        conn.execute(
            """
            INSERT INTO comments (
                comment_id, video_id, author_channel_id, text, like_count,
                reply_count, published_at, updated_at, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comment_id) DO UPDATE SET
                like_count  = excluded.like_count,
                reply_count = excluded.reply_count,
                updated_at  = excluded.updated_at,
                fetched_at  = excluded.fetched_at
            """,
            (
                c["comment_id"],
                video_id,
                c.get("author_channel_id"),
                c["text"],
                c["like_count"],
                c.get("reply_count"),
                c["published_at"],
                c.get("updated_at"),
                fetched_at,
            ),
        )
    return new


def checkpoint_page(
    conn: sqlite3.Connection, video_id: str, page_token: str | None, new_comments: int
) -> None:
    """Advance a video's resume point by one page and commit."""
    conn.execute(
        """
        UPDATE videos SET
            status = ?,
            page_token = ?,
            pages_fetched = pages_fetched + 1,
            comments_stored = comments_stored + ?
        WHERE video_id = ?
        """,
        (IN_PROGRESS, page_token, new_comments, video_id),
    )
    conn.commit()


def finish_video(
    conn: sqlite3.Connection, video_id: str, status: str, reason: str | None = None
) -> None:
    """Mark a video terminal. Only ``DONE`` means fully enumerated."""
    conn.execute(
        """
        UPDATE videos SET status = ?, status_reason = ?, page_token = NULL, completed_at = ?
        WHERE video_id = ?
        """,
        (status, reason, datetime.now(UTC).isoformat(), video_id),
    )
    conn.commit()


def store_relevance_ranks(
    conn: sqlite3.Connection, video_id: str, comment_ids: list[str], fetched_at: str
) -> None:
    """Record the displayed `order=relevance` ranking for one video."""
    for rank, comment_id in enumerate(comment_ids, start=1):
        conn.execute(
            """
            INSERT INTO relevance_ranks (video_id, comment_id, rank, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id, comment_id) DO UPDATE SET
                rank = excluded.rank, fetched_at = excluded.fetched_at
            """,
            (video_id, comment_id, rank, fetched_at),
        )
    conn.commit()


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Counts for the ``status`` command and for run logs."""
    by_status = {
        row["status"]: row["n"]
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM videos GROUP BY status")
    }
    totals = conn.execute(
        "SELECT COUNT(*) AS comments, COUNT(DISTINCT video_id) AS videos FROM comments"
    ).fetchone()
    return {
        "videos_by_status": by_status,
        "comments": totals["comments"],
        "videos_with_comments": totals["videos"],
        "population_hash": get_meta(conn, "population_hash"),
    }
