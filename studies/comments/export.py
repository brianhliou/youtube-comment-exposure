"""Export the corpus to a release format.

Writes two gzipped JSONL files (comments, videos) plus a checksum manifest.

On author identifiers: ``author_channel_id`` is replaced by a salted hash, with the
salt generated per export and never written to disk. That blocks linking an author
across this dataset and any other, which is the realistic harm. It does not make the
data anonymous, and the dataset card says so plainly: comment text is public, indexed,
and searchable, so a determined reader can re-identify an author from the text alone.
Claiming otherwise would be worse than not hashing at all.

    python -m studies.comments.export --db data/runs/comments/stratified.sqlite \\
        --outdir data/release/youtube-comment-exposure-v1
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import secrets
import sqlite3
from pathlib import Path

COMMENT_FIELDS = [
    "comment_id", "video_id", "author_hash", "text", "like_count", "reply_count",
    "published_at", "fetched_at", "age_hours", "relevance_rank",
]
VIDEO_FIELDS = [
    "video_id", "channel_handle", "published_at", "title", "category_id",
    "duration_seconds", "is_short", "view_count", "like_count", "comment_count",
    "stats_fetched_at", "collected_comments",
]


def duration_seconds(iso: str | None) -> int | None:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    h, mi, s = (int(v) if v else 0 for v in m.groups())
    return h * 3600 + mi * 60 + s


def author_hash(raw: str | None, salt: str) -> str | None:
    if not raw:
        return None
    return hashlib.sha256((salt + raw).encode()).hexdigest()[:20]


def write_jsonl_gz(path: Path, rows) -> tuple[int, str]:
    """Write gzipped JSONL; return (row count, sha256 of the file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return n, digest


def export(db: str, outdir: str) -> dict:
    salt = secrets.token_hex(16)  # never persisted
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    out = Path(outdir)

    def comments():
        q = conn.execute(
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
                "age_hours": round(r["age_h"], 4) if r["age_h"] is not None else None,
                "relevance_rank": r["relevance_rank"],
            }

    def videos():
        q = conn.execute(
            """
            SELECT v.*, (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.video_id) AS n
            FROM videos v WHERE v.status = 'done' ORDER BY v.video_id
            """
        )
        for r in q:
            secs = duration_seconds(r["duration"])
            yield {
                "video_id": r["video_id"],
                "channel_handle": r["channel_handle"],
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

    n_comments, c_digest = write_jsonl_gz(out / "comments.jsonl.gz", comments())
    n_videos, v_digest = write_jsonl_gz(out / "videos.jsonl.gz", videos())
    conn.close()

    manifest = {
        "name": "youtube-comment-exposure",
        "version": "1.0",
        "comments": {"rows": n_comments, "file": "comments.jsonl.gz", "sha256": c_digest},
        "videos": {"rows": n_videos, "file": "videos.jsonl.gz", "sha256": v_digest},
        "comment_fields": COMMENT_FIELDS,
        "video_fields": VIDEO_FIELDS,
        "author_ids": "salted sha256, salt discarded after export",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/runs/comments/stratified.sqlite")
    p.add_argument("--outdir", default="data/release/youtube-comment-exposure-v1")
    a = p.parse_args(argv)
    print(json.dumps(export(a.db, a.outdir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
