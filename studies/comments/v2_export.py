"""Export the v2 corpora (V2.md) to a release format, following v1's ``export.py``.

Two corpora, one salt, so an ``author_hash`` means the same author in both:

- ``bulk/``: the three-language replication (``v2.sqlite``), every video enumerated
  completely. Same rows and fields as v1's export, plus ``lang`` and ``marquee`` on
  each video, taken from ``config/panel-channels.json``.
- ``panel/``: the twice-daily top-100 panel (a snapshot of ``panel-v2.sqlite``): every
  displayed-page observation, the per-period video totals, and one row per attempted
  page fetch (so a missing page means "not attempted", not "no comments").

Dropped, as in v1: raw ``author_channel_id`` (replaced by a salted hash; the salt is
generated per export and never written down), ``updated_at``, and the collector's
bookkeeping (page tokens, quota ledger, resolve errors). Added: ``is_creator`` on panel
pages, because the analysis drops the creator's own comments and the raw author id that
would let a reader do so is not released.

    python -m studies.comments.v2_export \\
        --bulk-db data/runs/comments/v2.sqlite \\
        --panel-db data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite \\
        --outdir data/release/youtube-comment-exposure-v2
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
from pathlib import Path

from studies.comments.export import author_hash, duration_seconds, write_jsonl_gz

CHANNELS = Path(__file__).parent / "config" / "panel-channels.json"

BULK_COMMENT_FIELDS = [
    "comment_id", "video_id", "author_hash", "text", "like_count", "reply_count",
    "published_at", "fetched_at", "age_hours", "relevance_rank",
]  # fmt: skip
BULK_VIDEO_FIELDS = [
    "video_id", "channel_handle", "lang", "marquee", "published_at", "title", "category_id",
    "duration_seconds", "is_short", "view_count", "like_count", "comment_count",
    "stats_fetched_at", "collected_comments",
]  # fmt: skip
PANEL_PAGE_FIELDS = [
    "video_id", "period", "is_repeat", "rank", "comment_id", "author_hash", "is_creator",
    "text", "like_count", "reply_count", "published_at", "fetched_at", "comment_age_hours",
    "video_age_hours",
]  # fmt: skip
PANEL_VIDEO_FIELDS = [
    "video_id", "channel_handle", "lang", "marquee", "published_at", "enrolled_day",
    "enrolled_at", "title", "category_id", "duration_seconds", "is_short", "status",
    "status_reason",
]  # fmt: skip
PANEL_TOTALS_FIELDS = [
    "video_id", "period", "fetched_at", "view_count", "like_count", "comment_count",
]  # fmt: skip
PANEL_FETCH_FIELDS = ["video_id", "period", "fetched_at", "n_items", "outcome"]


def _ro(db: str) -> sqlite3.Connection:
    # immutable: the collector is finished, and a read-only WAL open fails without a
    # writable -shm. Never point this at a database something is still writing.
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _channels() -> dict[str, dict]:
    return {c["handle"]: c for c in json.loads(CHANNELS.read_text())["channels"]}


def _round(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def export(bulk_db: str, panel_db: str, outdir: str) -> dict:
    salt = secrets.token_hex(16)  # never persisted
    chans = _channels()
    out = Path(outdir)
    files: dict[str, dict] = {}

    def lang_of(handle: str | None) -> tuple[str | None, bool]:
        c = chans.get(handle or "")
        return (c["lang"], bool(c.get("marquee"))) if c else (None, False)

    # ------------------------------------------------------------------ bulk (as v1)
    bulk = _ro(bulk_db)

    def bulk_comments():
        q = bulk.execute(
            """
            SELECT cm.comment_id, cm.video_id, cm.author_channel_id, cm.text,
                   cm.like_count, cm.reply_count, cm.published_at, cm.fetched_at,
                   (julianday(cm.published_at) - julianday(v.published_at)) * 24.0 AS age_h,
                   rr.rank AS relevance_rank
            FROM comments cm
            JOIN videos v USING(video_id)
            LEFT JOIN relevance_ranks rr
                   ON rr.video_id = cm.video_id AND rr.comment_id = cm.comment_id
            WHERE v.status = 'done'
            ORDER BY cm.video_id, cm.published_at
            """
        )
        for r in q:
            yield {
                "comment_id": r["comment_id"],
                "video_id": r["video_id"],
                "author_hash": author_hash(r["author_channel_id"], salt),
                "text": r["text"],
                "like_count": r["like_count"],
                "reply_count": r["reply_count"],
                "published_at": r["published_at"],
                "fetched_at": r["fetched_at"],
                "age_hours": _round(r["age_h"]),
                "relevance_rank": r["relevance_rank"],
            }

    def bulk_videos():
        q = bulk.execute(
            """
            SELECT v.*, (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.video_id) AS n
            FROM videos v WHERE v.status = 'done' ORDER BY v.video_id
            """
        )
        for r in q:
            secs = duration_seconds(r["duration"])
            lang, marquee = lang_of(r["channel_handle"])
            yield {
                "video_id": r["video_id"],
                "channel_handle": r["channel_handle"],
                "lang": lang,
                "marquee": marquee,
                "published_at": r["published_at"],
                "title": r["title"],
                "category_id": r["category_id"],
                "duration_seconds": secs,
                "is_short": bool(secs is not None and secs <= 60),
                "view_count": r["view_count"],
                "like_count": r["like_count"],
                "comment_count": r["comment_count"],
                "stats_fetched_at": r["stats_fetched_at"],
                "collected_comments": r["n"],
            }

    for name, rows in (("bulk/comments.jsonl.gz", bulk_comments()),
                       ("bulk/videos.jsonl.gz", bulk_videos())):  # fmt: skip
        n, digest = write_jsonl_gz(out / name, rows)
        files[name] = {"rows": n, "sha256": digest}
    bulk.close()

    # ------------------------------------------------------------------ panel
    panel = _ro(panel_db)
    panel_lang = {r["handle"]: (r["lang"], bool(r["marquee"])) for r in
                  panel.execute("SELECT handle, lang, marquee FROM channels")}  # fmt: skip

    def panel_videos():
        for r in panel.execute("SELECT * FROM panel_videos ORDER BY video_id"):
            secs = duration_seconds(r["duration"])
            lang, marquee = panel_lang.get(r["channel_handle"], (r["lang"], False))
            yield {
                "video_id": r["video_id"],
                "channel_handle": r["channel_handle"],
                "lang": lang,
                "marquee": marquee,
                "published_at": r["published_at"],
                "enrolled_day": r["enrolled_day"],
                "enrolled_at": r["enrolled_at"],
                "title": r["title"],
                "category_id": r["category_id"],
                "duration_seconds": secs,
                "is_short": bool(secs is not None and secs <= 60),
                "status": r["status"],
                "status_reason": r["status_reason"],
            }

    def panel_pages():
        q = panel.execute(
            """
            SELECT ro.*, v.channel_id,
                   (julianday(ro.published_at) - julianday(v.published_at)) * 24.0 AS c_age,
                   (julianday(ro.fetched_at) - julianday(v.published_at)) * 24.0 AS v_age
            FROM rank_observations ro JOIN panel_videos v USING(video_id)
            ORDER BY ro.video_id, ro.period, ro.rank
            """
        )
        for r in q:
            yield {
                "video_id": r["video_id"],
                "period": r["period"],
                "is_repeat": r["period"].endswith("r"),
                "rank": r["rank"],
                "comment_id": r["comment_id"],
                "author_hash": author_hash(r["author_channel_id"], salt),
                "is_creator": bool(r["channel_id"] and r["author_channel_id"] == r["channel_id"]),
                "text": r["text"],
                "like_count": r["like_count"],
                "reply_count": r["reply_count"],
                "published_at": r["published_at"],
                "fetched_at": r["fetched_at"],
                "comment_age_hours": _round(r["c_age"]),
                "video_age_hours": _round(r["v_age"]),
            }

    def simple(sql: str, fields: list[str]):
        for r in panel.execute(sql):
            yield {f: r[f] for f in fields}

    for name, rows in (
        ("panel/videos.jsonl.gz", panel_videos()),
        ("panel/pages.jsonl.gz", panel_pages()),
        ("panel/totals.jsonl.gz", simple(
            "SELECT * FROM video_observations ORDER BY video_id, period", PANEL_TOTALS_FIELDS)),
        ("panel/fetches.jsonl.gz", simple(
            "SELECT * FROM rank_fetches ORDER BY video_id, period", PANEL_FETCH_FIELDS)),
    ):  # fmt: skip
        n, digest = write_jsonl_gz(out / name, rows)
        files[name] = {"rows": n, "sha256": digest}
    panel.close()

    for name, f in files.items():
        f["bytes"] = (out / name).stat().st_size
    manifest = {
        "name": "youtube-comment-exposure",
        "version": "2.0",
        "files": files,
        "fields": {
            "bulk/comments": BULK_COMMENT_FIELDS,
            "bulk/videos": BULK_VIDEO_FIELDS,
            "panel/pages": PANEL_PAGE_FIELDS,
            "panel/videos": PANEL_VIDEO_FIELDS,
            "panel/totals": PANEL_TOTALS_FIELDS,
            "panel/fetches": PANEL_FETCH_FIELDS,
        },
        "author_ids": "salted sha256, one salt for both corpora, discarded after export",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bulk-db", default="data/runs/comments/v2.sqlite")
    p.add_argument("--panel-db", default="data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite")
    p.add_argument("--outdir", default="data/release/youtube-comment-exposure-v2")
    a = p.parse_args(argv)
    print(json.dumps(export(a.bulk_db, a.panel_db, a.outdir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
