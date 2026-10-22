"""Twice-daily top-100 panel — watch a video's displayed comment ranking form.

v1 (`collect.py`) took one snapshot per video after the fact and found that exposure
(post time + video scale) explains most of the like distribution. It could not see the
ranking *form*: whether the comments on the first day's top page are the ones on the
top page three weeks later, or how fast the page stops moving. This module observes
that directly.

Shape: each run, enroll videos published in the last ~30 h from the configured
channels (until the panel is full), then fetch every active panel video's
``order=relevance`` first page — rank, like count, reply count, posted time, text —
plus its view/like/comment totals. Runs twice a day (periods ``<day>a`` before noon
Pacific, ``<day>b`` after), because if the page locks in within days most of the
movement is in the first 48 hours and one point a day cannot see it. Each video is
observed for ``window_days`` from enrollment. Every run also re-fetches a small random
sample of pages it just fetched (period ``<day>ar`` / ``<day>br``): ``order=relevance``
is nondeterministic across calls, and that pair is the noise floor every "the page
changed" claim is measured against.

Costs ~1 unit per channel per day for discovery, ~1 unit per active video per period
for the rank page, plus the reliability sample — a 500-video panel over 330 channels
runs ~1,400 units/day.

Run shape matches `collect.py`: spend what the budget allows, exit cleanly, be
re-launched on schedule. Every write is idempotent on ``(video_id, period, ...)`` so a
re-run within the same period updates rather than duplicates.

Usage::

    python -m studies.comments.panel init \\
        --db data/runs/comments/panel-v2.sqlite \\
        --channels studies/comments/config/panel-channels.json \\
        --target 500 --window-days 21 --max-enroll-per-day 80
    python -m studies.comments.panel run --db data/runs/comments/panel-v2.sqlite
    python -m studies.comments.panel status --db data/runs/comments/panel-v2.sqlite

``YOUTUBE_API_KEY`` comes from the environment and is never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studies.comments.client import (
    QuotaExceeded,
    ResourceUnavailable,
    TransientError,
    YouTubeClient,
)
from studies.comments.quota import QUOTA_TZ, QuotaLedger, quota_day

SCHEMA_VERSION = "panel-2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    handle           TEXT PRIMARY KEY,
    lang             TEXT NOT NULL,
    genre            TEXT,
    shorts_heavy     INTEGER NOT NULL DEFAULT 0,
    marquee          INTEGER NOT NULL DEFAULT 0,  -- over the sub cap; per-channel table only
    channel_id       TEXT,
    title            TEXT,
    uploads_playlist TEXT,
    resolved_at      TEXT,
    resolve_error    TEXT,
    last_discovery   TEXT,
    last_upload_at   TEXT
);

-- One row per enrolled video. `enrolled_day` is the Pacific quota-day of enrollment;
-- observation day 0 is that day, and the window closes after `window_days` days.
CREATE TABLE IF NOT EXISTS panel_videos (
    video_id       TEXT PRIMARY KEY,
    channel_handle TEXT NOT NULL,
    channel_id     TEXT,
    lang           TEXT NOT NULL,
    published_at   TEXT NOT NULL,
    enrolled_day   TEXT NOT NULL,
    enrolled_at    TEXT NOT NULL,
    title          TEXT,
    duration       TEXT,
    category_id    TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    status_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_panel_status ON panel_videos(status, enrolled_day);

-- Per-period totals from videos.list. commentCount includes replies (v1 measured
-- top-level threads at ~70% of it), so it is a scale covariate, not a thread count.
CREATE TABLE IF NOT EXISTS video_observations (
    video_id      TEXT NOT NULL,
    period        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    view_count    INTEGER,
    like_count    INTEGER,
    comment_count INTEGER,
    PRIMARY KEY (video_id, period)
);

-- The displayed top page, one row per (video, period, rank). `text` is stored so the
-- rank-vs-content question can be asked later without a second fetch. Analysis
-- works in age-at-fetch (fetched_at - panel_videos.published_at), not period index.
CREATE TABLE IF NOT EXISTS rank_observations (
    video_id          TEXT NOT NULL,
    period            TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    comment_id        TEXT NOT NULL,
    author_channel_id TEXT,
    like_count        INTEGER NOT NULL,
    reply_count       INTEGER,
    published_at      TEXT NOT NULL,
    updated_at        TEXT,
    text              TEXT NOT NULL,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (video_id, period, rank)
);
CREATE INDEX IF NOT EXISTS idx_rank_comment ON rank_observations(comment_id, period);

-- One row per video per period the rank page was attempted, including the empty and
-- failed ones, so "no row" always means "not attempted" rather than "no comments".
-- Reliability re-fetches land here too, under period '<period>r' with outcome 'repeat'.
CREATE TABLE IF NOT EXISTS rank_fetches (
    video_id   TEXT NOT NULL,
    period     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    n_items    INTEGER NOT NULL,
    outcome    TEXT NOT NULL,
    PRIMARY KEY (video_id, period)
);

CREATE TABLE IF NOT EXISTS quota_ledger (
    day_pt     TEXT PRIMARY KEY,
    units_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

ACTIVE = "active"
CLOSED = "closed"  # window elapsed
DROPPED = "dropped"  # deleted / private / comments disabled mid-window

DEFAULT_BUDGET = 2_000  # per Pacific day across both runs; shares the key with the bulk collector
MAX_PER_CHANNEL_PER_DAY = 2  # a daily Shorts poster cannot take a fifth of the panel
MAX_PER_CHANNEL_TOTAL = 5  # ...nor dominate it over the enrollment week
DEFAULT_RELIABILITY_SAMPLE = 20  # pages re-fetched per run for the noise floor
QUIET_CHANNEL_DAYS = 30  # no upload in this long → flagged in status


def _now() -> str:
    return datetime.now(UTC).isoformat()


def period_now(now: datetime | None = None) -> str:
    """``<quota_day>a`` before noon Pacific, ``<quota_day>b`` after."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(QUOTA_TZ)
    return local.date().isoformat() + ("a" if local.hour < 12 else "b")


def _api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        sys.exit("YOUTUBE_API_KEY is not set in the environment")
    return key


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


# --------------------------------------------------------------------------- init


def init(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.channels).read_text())
    channels = spec["channels"]
    conn = connect(args.db)
    existing = get_meta(conn, "target")
    if existing is not None and not args.force:
        sys.exit(
            f"{args.db} is already initialised (target={existing}); "
            "pass --force to re-seed channels"
        )
    for ch in channels:
        conn.execute(
            """
            INSERT INTO channels (handle, lang, genre, shorts_heavy, marquee)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                lang = excluded.lang, genre = excluded.genre,
                shorts_heavy = excluded.shorts_heavy, marquee = excluded.marquee
            """,
            (
                ch["handle"],
                ch["lang"],
                ch.get("genre"),
                int(bool(ch.get("shorts_heavy"))),
                int(bool(ch.get("marquee"))),
            ),
        )
    set_meta(conn, "target", args.target)
    set_meta(conn, "window_days", args.window_days)
    set_meta(conn, "max_enroll_per_day", args.max_enroll_per_day)
    set_meta(conn, "max_video_age_hours", args.max_video_age_hours)
    set_meta(conn, "reliability_sample", args.reliability_sample)
    set_meta(conn, "channels_file", str(args.channels))
    set_meta(conn, "channels_name", spec.get("name", ""))
    set_meta(conn, "initialised_at", _now())
    n = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    by_lang = conn.execute(
        "SELECT lang, COUNT(*) FROM channels GROUP BY lang ORDER BY lang"
    ).fetchall()
    print(f"initialised {args.db}: {n} channels " + ", ".join(f"{r[0]}={r[1]}" for r in by_lang))
    print(
        f"target={args.target} window_days={args.window_days} "
        f"max_enroll_per_day={args.max_enroll_per_day} "
        f"max_video_age_hours={args.max_video_age_hours} "
        f"reliability_sample={args.reliability_sample}"
    )
    return 0


# --------------------------------------------------------------------------- run


def _resolve_channels(conn: sqlite3.Connection, client: YouTubeClient, ledger: QuotaLedger) -> None:
    rows = conn.execute(
        "SELECT handle FROM channels WHERE channel_id IS NULL AND resolve_error IS NULL"
    ).fetchall()
    for row in rows:
        if not ledger.can_spend(1):
            return
        handle = row["handle"]
        params: dict[str, Any] = {"part": "contentDetails,snippet"}
        if handle.startswith("@"):
            params["forHandle"] = handle
        else:
            params["id"] = handle
        try:
            body = client.get("channels", params)
        except QuotaExceeded:
            ledger.exhaust()
            return
        except (ResourceUnavailable, TransientError) as exc:
            conn.execute(
                "UPDATE channels SET resolve_error = ? WHERE handle = ?",
                (type(exc).__name__, handle),
            )
            conn.commit()
            continue
        ledger.spend(1)
        items = body.get("items") or []
        if not items:
            conn.execute(
                "UPDATE channels SET resolve_error = 'unresolved' WHERE handle = ?", (handle,)
            )
        else:
            item = items[0]
            conn.execute(
                """
                UPDATE channels SET channel_id = ?, title = ?, uploads_playlist = ?,
                    resolved_at = ?, resolve_error = NULL
                WHERE handle = ?
                """,
                (
                    item["id"],
                    item["snippet"].get("title"),
                    item["contentDetails"]["relatedPlaylists"]["uploads"],
                    _now(),
                    handle,
                ),
            )
        conn.commit()


def _active_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM panel_videos WHERE status = ?", (ACTIVE,)).fetchone()[
        0
    ]


def _enrolled_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM panel_videos").fetchone()[0]


def _discover(
    conn: sqlite3.Connection,
    client: YouTubeClient,
    ledger: QuotaLedger,
    *,
    today: str,
    target: int,
    max_enroll: int,
    max_age_hours: int,
) -> int:
    """Enroll fresh uploads until the panel or the day's enrollment cap is full.

    Channels are walked in round-robin across languages, and each language holds at
    most ``ceil(target / n_languages)`` panel videos — round-robin alone is not enough,
    because languages differ in upload rate (day 0: ja channels filled 36 of 80 slots to
    en's 19) and the languages have to compare on equal n. A channel that already holds
    ``MAX_PER_CHANNEL_TOTAL`` panel videos is skipped. One `playlistItems` page per channel
    per day: the uploads playlist is newest-first, so page 1 holds everything from the
    last 24 h for any channel that posts less than 50 times a day. The newest upload seen
    is recorded as ``last_upload_at`` so a quiet channel is visible in `status` instead
    of silently contributing nothing.
    """
    remaining_total = target - _enrolled_count(conn)
    enrolled_today = conn.execute(
        "SELECT COUNT(*) FROM panel_videos WHERE enrolled_day = ?", (today,)
    ).fetchone()[0]
    budget = min(remaining_total, max_enroll - enrolled_today)
    if budget <= 0:
        return 0

    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    channels = conn.execute(
        """
        SELECT c.handle, c.lang, c.channel_id, c.uploads_playlist,
               (SELECT COUNT(*) FROM panel_videos p WHERE p.channel_handle = c.handle) AS held
        FROM channels c
        WHERE c.channel_id IS NOT NULL
          AND (c.last_discovery IS NULL OR c.last_discovery < ?)
        ORDER BY c.lang, RANDOM()
        """,
        (today,),
    ).fetchall()
    n_langs = conn.execute("SELECT COUNT(DISTINCT lang) FROM channels").fetchone()[0] or 1
    lang_cap = -(-target // n_langs)
    held_by_lang: dict[str, int] = dict(
        conn.execute("SELECT lang, COUNT(*) FROM panel_videos GROUP BY lang").fetchall()
    )
    # Interleave languages: en, zh, ja, en, zh, ja, ...
    by_lang: dict[str, list[sqlite3.Row]] = {}
    for ch in channels:
        by_lang.setdefault(ch["lang"], []).append(ch)
    order: list[sqlite3.Row] = []
    while any(by_lang.values()):
        for lang in sorted(by_lang):
            if by_lang[lang]:
                order.append(by_lang[lang].pop())

    enrolled = 0
    for ch in order:
        if enrolled >= budget or not ledger.can_spend(1):
            break
        if ch["held"] >= MAX_PER_CHANNEL_TOTAL:
            continue
        if held_by_lang.get(ch["lang"], 0) >= lang_cap:
            continue
        try:
            body = client.get(
                "playlistItems",
                {"part": "contentDetails", "playlistId": ch["uploads_playlist"], "maxResults": 50},
            )
        except QuotaExceeded:
            ledger.exhaust()
            break
        except (ResourceUnavailable, TransientError):
            conn.execute(
                "UPDATE channels SET last_discovery = ? WHERE handle = ?", (today, ch["handle"])
            )
            conn.commit()
            continue
        ledger.spend(1)
        fresh: list[tuple[str, str]] = []
        newest: str | None = None
        for item in body.get("items", []):
            details = item["contentDetails"]
            published = details.get("videoPublishedAt")
            if not published:
                continue
            newest = max(newest or "", published)
            if published >= cutoff:
                fresh.append((details["videoId"], published))
        # Newest-first page, so take the oldest of today's fresh uploads first — they have
        # had the longest to gather comments — and cap per channel per day.
        room = min(
            MAX_PER_CHANNEL_PER_DAY,
            MAX_PER_CHANNEL_TOTAL - ch["held"],
            lang_cap - held_by_lang.get(ch["lang"], 0),
        )
        for video_id, published in sorted(fresh, key=lambda f: f[1])[:room]:
            if enrolled >= budget:
                break
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO panel_videos
                    (video_id, channel_handle, channel_id, lang, published_at,
                     enrolled_day, enrolled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, ch["handle"], ch["channel_id"], ch["lang"], published, today, _now()),
            )
            enrolled += cur.rowcount
            held_by_lang[ch["lang"]] = held_by_lang.get(ch["lang"], 0) + cur.rowcount
        conn.execute(
            "UPDATE channels SET last_discovery = ?, last_upload_at = ? WHERE handle = ?",
            (today, newest, ch["handle"]),
        )
        conn.commit()
    return enrolled


def _close_windows(conn: sqlite3.Connection, today: str, window_days: int) -> int:
    """Close videos whose observation window has elapsed.

    Day 0 is the enrollment day, so a 21-day window yields observations on days 0..20.
    """
    last_day = (datetime.fromisoformat(today) - timedelta(days=window_days)).date().isoformat()
    cur = conn.execute(
        "UPDATE panel_videos SET status = ?, status_reason = 'window elapsed' "
        "WHERE status = ? AND enrolled_day <= ?",
        (CLOSED, ACTIVE, last_day),
    )
    conn.commit()
    return cur.rowcount


def _observe_totals(
    conn: sqlite3.Connection, client: YouTubeClient, ledger: QuotaLedger, period: str
) -> int:
    """videos.list for every active video not yet observed this period, 50 per unit.

    Also fills title/duration/category for newly enrolled videos, and drops videos the
    API no longer returns (deleted or private).
    """
    rows = conn.execute(
        """
        SELECT v.video_id FROM panel_videos v
        LEFT JOIN video_observations o ON o.video_id = v.video_id AND o.period = ?
        WHERE v.status = ? AND o.video_id IS NULL
        ORDER BY v.enrolled_day DESC
        """,
        (period, ACTIVE),
    ).fetchall()
    ids = [r["video_id"] for r in rows]
    observed = 0
    for start in range(0, len(ids), 50):
        batch = ids[start : start + 50]
        if not ledger.can_spend(1):
            break
        try:
            body = client.get(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
            )
        except QuotaExceeded:
            ledger.exhaust()
            break
        except (ResourceUnavailable, TransientError):
            continue
        ledger.spend(1)
        fetched_at = _now()
        seen: set[str] = set()
        for item in body.get("items", []):
            vid = item["id"]
            seen.add(vid)
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            conn.execute(
                """
                INSERT INTO video_observations
                    (video_id, period, fetched_at, view_count, like_count, comment_count)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id, period) DO UPDATE SET
                    fetched_at = excluded.fetched_at, view_count = excluded.view_count,
                    like_count = excluded.like_count, comment_count = excluded.comment_count
                """,
                (
                    vid,
                    period,
                    fetched_at,
                    _as_int(stats.get("viewCount")),
                    _as_int(stats.get("likeCount")),
                    _as_int(stats.get("commentCount")),
                ),
            )
            conn.execute(
                """
                UPDATE panel_videos SET
                    title = COALESCE(title, ?), duration = COALESCE(duration, ?),
                    category_id = COALESCE(category_id, ?)
                WHERE video_id = ?
                """,
                (
                    snippet.get("title"),
                    item.get("contentDetails", {}).get("duration"),
                    snippet.get("categoryId"),
                    vid,
                ),
            )
            observed += 1
        for vid in batch:
            if vid not in seen:
                conn.execute(
                    "UPDATE panel_videos SET status = ?, "
                    "status_reason = 'not returned by videos.list' "
                    "WHERE video_id = ? AND status = ?",
                    (DROPPED, vid, ACTIVE),
                )
        conn.commit()
    return observed


def _fetch_rank_page(
    conn: sqlite3.Connection,
    client: YouTubeClient,
    ledger: QuotaLedger,
    video_id: str,
    period: str,
    *,
    outcome: str = "ok",
) -> bool:
    """One `order=relevance` page for one video, stored under ``period``.

    Returns False when the day's quota is gone (caller stops) — any other failure is
    recorded or left for retry and returns True so the caller moves on.
    """
    try:
        body = client.get(
            "commentThreads",
            {
                "part": "snippet",
                "videoId": video_id,
                "order": "relevance",
                "maxResults": 100,
                "textFormat": "plainText",
            },
        )
    except QuotaExceeded:
        ledger.exhaust()
        return False
    except ResourceUnavailable as exc:
        # commentsDisabled / videoNotFound are durable: drop the video, keep the row.
        fetched_at = _now()
        conn.execute(
            "INSERT OR REPLACE INTO rank_fetches VALUES (?, ?, ?, 0, ?)",
            (video_id, period, fetched_at, f"unavailable:{exc.args[0]}"),
        )
        conn.execute(
            "UPDATE panel_videos SET status = ?, status_reason = ? WHERE video_id = ?",
            (DROPPED, f"commentThreads: {exc.args[0]}", video_id),
        )
        conn.commit()
        return True
    except TransientError:
        return True  # no rank_fetches row: retried on the next run this period
    ledger.spend(1)
    fetched_at = _now()
    items = body.get("items", [])
    for rank, item in enumerate(items, start=1):
        top = item.get("snippet", {}).get("topLevelComment")
        if not top:
            continue
        s = top["snippet"]
        conn.execute(
            """
            INSERT INTO rank_observations
                (video_id, period, rank, comment_id, author_channel_id, like_count,
                 reply_count, published_at, updated_at, text, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, period, rank) DO UPDATE SET
                comment_id = excluded.comment_id,
                author_channel_id = excluded.author_channel_id,
                like_count = excluded.like_count, reply_count = excluded.reply_count,
                published_at = excluded.published_at, updated_at = excluded.updated_at,
                text = excluded.text, fetched_at = excluded.fetched_at
            """,
            (
                video_id,
                period,
                rank,
                top["id"],
                (s.get("authorChannelId") or {}).get("value"),
                int(s.get("likeCount", 0)),
                _as_int(item.get("snippet", {}).get("totalReplyCount")),
                s.get("publishedAt", ""),
                s.get("updatedAt"),
                s.get("textDisplay", ""),
                fetched_at,
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO rank_fetches VALUES (?, ?, ?, ?, ?)",
        (video_id, period, fetched_at, len(items), outcome),
    )
    conn.commit()
    return True


def _observe_ranks(
    conn: sqlite3.Connection, client: YouTubeClient, ledger: QuotaLedger, period: str
) -> int:
    """One rank page per active video per period, newest cohort first.

    Newest-first because the early days are where the page moves most; if the budget
    runs out, the day-15 observations are the cheap ones to lose.
    """
    rows = conn.execute(
        """
        SELECT v.video_id FROM panel_videos v
        LEFT JOIN rank_fetches f ON f.video_id = v.video_id AND f.period = ?
        WHERE v.status = ? AND f.video_id IS NULL
        ORDER BY v.enrolled_day DESC, v.published_at DESC
        """,
        (period, ACTIVE),
    ).fetchall()
    observed = 0
    for row in rows:
        if not ledger.can_spend(1):
            break
        before = ledger.used()
        if not _fetch_rank_page(conn, client, ledger, row["video_id"], period):
            break
        observed += int(ledger.used() > before)
    return observed


def _reliability(
    conn: sqlite3.Connection, client: YouTubeClient, ledger: QuotaLedger, period: str, n: int
) -> int:
    """Re-fetch ``n`` random pages fetched this period; store under ``<period>r``.

    Two consecutive calls for the same video minutes apart differ only by API
    nondeterminism (and the odd new like), so the overlap between them is the floor
    below which "the ranking moved" means nothing.
    """
    already = conn.execute(
        "SELECT COUNT(*) FROM rank_fetches WHERE period = ?", (period + "r",)
    ).fetchone()[0]
    n -= already  # a re-run within the period tops up to n, it does not add n more
    if n <= 0:
        return 0
    rows = conn.execute(
        """
        SELECT f.video_id FROM rank_fetches f
        LEFT JOIN rank_fetches r ON r.video_id = f.video_id AND r.period = ?
        WHERE f.period = ? AND f.outcome = 'ok' AND f.n_items >= 10 AND r.video_id IS NULL
        ORDER BY RANDOM() LIMIT ?
        """,
        (period + "r", period, n),
    ).fetchall()
    done = 0
    for row in rows:
        if not ledger.can_spend(1):
            break
        before = ledger.used()
        if not _fetch_rank_page(
            conn, client, ledger, row["video_id"], period + "r", outcome="repeat"
        ):
            break
        done += int(ledger.used() > before)
    return done


def run(args: argparse.Namespace) -> int:
    client = YouTubeClient(_api_key())
    conn = connect(args.db)
    target = int(get_meta(conn, "target") or 0)
    if not target:
        sys.exit(f"{args.db} is not initialised — run `panel init` first")
    window_days = int(get_meta(conn, "window_days") or 21)
    max_enroll = int(get_meta(conn, "max_enroll_per_day") or 80)
    max_age_hours = int(get_meta(conn, "max_video_age_hours") or 30)
    reliability_n = int(get_meta(conn, "reliability_sample") or DEFAULT_RELIABILITY_SAMPLE)
    ledger = QuotaLedger(conn, budget=args.daily_budget)
    today = quota_day()
    period = args.period or period_now()

    closed = _close_windows(conn, today, window_days)
    _resolve_channels(conn, client, ledger)
    enrolled = _discover(
        conn,
        client,
        ledger,
        today=today,
        target=target,
        max_enroll=max_enroll,
        max_age_hours=max_age_hours,
    )
    totals = _observe_totals(conn, client, ledger, period)
    ranks = _observe_ranks(conn, client, ledger, period)
    repeats = _reliability(conn, client, ledger, period, reliability_n)

    set_meta(conn, "last_run_period", period)
    set_meta(conn, "last_run_at", _now())
    print(
        f"{period}: enrolled +{enrolled} (total {_enrolled_count(conn)}/{target}, "
        f"active {_active_count(conn)}, closed today {closed}); "
        f"totals {totals}, rank pages {ranks}, repeats {repeats}; "
        f"units {ledger.used(today)}/{ledger.budget}"
    )
    pending = conn.execute(
        """
        SELECT COUNT(*) FROM panel_videos v
        LEFT JOIN rank_fetches f ON f.video_id = v.video_id AND f.period = ?
        WHERE v.status = ? AND f.video_id IS NULL
        """,
        (period, ACTIVE),
    ).fetchone()[0]
    if pending:
        print(f"WARNING: {pending} active videos have no rank observation for {period} (budget)")
        return 2
    return 0


# --------------------------------------------------------------------------- status


def status(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    target = get_meta(conn, "target")
    if target is None:
        sys.exit(f"{args.db} is not initialised")
    print(
        f"target {target} · window {get_meta(conn, 'window_days')}d · "
        f"last run {get_meta(conn, 'last_run_period')} at {get_meta(conn, 'last_run_at')}"
    )
    ch = conn.execute(
        """
        SELECT lang, COUNT(*) AS n, SUM(channel_id IS NOT NULL) AS resolved,
               SUM(resolve_error IS NOT NULL) AS failed
        FROM channels GROUP BY lang ORDER BY lang
        """
    ).fetchall()
    print(
        "channels: "
        + " · ".join(
            f"{r['lang']} {r['resolved']}/{r['n']} resolved ({r['failed']} failed)" for r in ch
        )
    )
    failed = conn.execute(
        "SELECT handle, resolve_error FROM channels WHERE resolve_error IS NOT NULL ORDER BY handle"
    ).fetchall()
    if failed:
        print("  unresolved: " + ", ".join(f"{r['handle']} ({r['resolve_error']})" for r in failed))
    quiet_before = (datetime.now(UTC) - timedelta(days=QUIET_CHANNEL_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    quiet = conn.execute(
        """
        SELECT handle, lang, last_upload_at FROM channels
        WHERE channel_id IS NOT NULL AND last_discovery IS NOT NULL
          AND (last_upload_at IS NULL OR last_upload_at < ?)
        ORDER BY lang, handle
        """,
        (quiet_before,),
    ).fetchall()
    if quiet:
        print(
            f"  quiet (no upload in {QUIET_CHANNEL_DAYS}d): "
            + ", ".join(f"{r['handle']} ({(r['last_upload_at'] or 'never')[:10]})" for r in quiet)
        )
    vids = conn.execute(
        "SELECT lang, status, COUNT(*) AS n FROM panel_videos "
        "GROUP BY lang, status ORDER BY lang, status"
    ).fetchall()
    print(
        "videos: " + " · ".join(f"{r['lang']}/{r['status']} {r['n']}" for r in vids)
        if vids
        else "videos: none"
    )
    cohorts = conn.execute(
        "SELECT enrolled_day, COUNT(*) AS n FROM panel_videos "
        "GROUP BY enrolled_day ORDER BY enrolled_day"
    ).fetchall()
    for c in cohorts:
        print(f"  {c['enrolled_day']}  +{c['n']}")
    top = conn.execute(
        "SELECT channel_handle, COUNT(*) AS n FROM panel_videos "
        "GROUP BY channel_handle ORDER BY n DESC LIMIT 5"
    ).fetchall()
    if top:
        print("  most enrolled: " + ", ".join(f"{r['channel_handle']} {r['n']}" for r in top))
    obs = conn.execute(
        "SELECT COUNT(*) AS pages, SUM(n_items) AS items FROM rank_fetches WHERE outcome = 'ok'"
    ).fetchone()
    periods = conn.execute(
        "SELECT COUNT(DISTINCT period) FROM rank_fetches WHERE outcome = 'ok'"
    ).fetchone()[0]
    repeats = conn.execute("SELECT COUNT(*) FROM rank_fetches WHERE outcome = 'repeat'").fetchone()[
        0
    ]
    print(
        f"rank pages: {obs['pages'] or 0} over {periods} periods, {obs['items'] or 0} ranked rows; "
        f"{repeats} reliability repeats"
    )
    q = conn.execute(
        "SELECT day_pt, units_used FROM quota_ledger ORDER BY day_pt DESC LIMIT 7"
    ).fetchall()
    print("quota: " + ", ".join(f"{r['day_pt']} {r['units_used']}" for r in q))
    if args.json:
        print(
            json.dumps(
                {
                    "target": int(target),
                    "enrolled": _enrolled_count(conn),
                    "active": _active_count(conn),
                    "rank_pages": obs["pages"] or 0,
                    "periods": periods,
                    "repeats": repeats,
                    "unresolved": len(failed),
                    "quiet": len(quiet),
                }
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--daily-budget",
        type=int,
        default=DEFAULT_BUDGET,
        help="units this process may spend per Pacific day",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the panel database and seed channels")
    p_init.add_argument("--db", required=True)
    p_init.add_argument(
        "--channels",
        required=True,
        help="JSON: {name, channels:[{handle, lang, genre, shorts_heavy}]}",
    )
    p_init.add_argument("--target", type=int, default=500, help="panel size; enrollment stops here")
    p_init.add_argument(
        "--window-days",
        type=int,
        default=21,
        help="days each video is observed after enrollment",
    )
    p_init.add_argument("--max-enroll-per-day", type=int, default=80)
    p_init.add_argument(
        "--max-video-age-hours",
        type=int,
        default=30,
        help="a video older than this at discovery is not enrolled",
    )
    p_init.add_argument(
        "--reliability-sample",
        type=int,
        default=DEFAULT_RELIABILITY_SAMPLE,
        help="pages re-fetched per run to measure API rank nondeterminism",
    )
    p_init.add_argument(
        "--force", action="store_true", help="re-seed channels into an existing database"
    )
    p_init.set_defaults(func=init)

    p_run = sub.add_parser("run", help="one pass: enroll, observe, repeat-sample, exit")
    p_run.add_argument("--db", required=True)
    p_run.add_argument("--period", default=None, help="override the period key (tests / backfill)")
    p_run.set_defaults(func=run)

    p_status = sub.add_parser("status")
    p_status.add_argument("--db", required=True)
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
