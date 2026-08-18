"""Resumable, quota-bounded collector for the frozen comment population.

Run shape: the process works the queue until the day's quota is spent, then exits.
It is designed to be re-launched daily (cron/LaunchAgent) for as long as the corpus
takes — data here is calendar-bound, not attention-bound, so the useful thing a run
can do is spend today's units and stop cleanly.

Resume discipline follows the corpus-work rule that has bitten this portfolio before:
checkpoint after each *page* so an interrupt costs at most one page, mark a video
terminal only when it is *fully* enumerated, and make every comment write idempotent
so replaying an interrupted page cannot double-count.

Usage::

    python -m studies.comments.collect freeze \\
        --config studies/comments/config/pilot.json \\
        --out data/runs/comments/pilot-population.json
    python -m studies.comments.collect run \\
        --population data/runs/comments/pilot-population.json \\
        --db data/runs/comments/pilot.sqlite
    python -m studies.comments.collect status --db data/runs/comments/pilot.sqlite

``YOUTUBE_API_KEY`` comes from the environment and is never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from studies.comments import store
from studies.comments.client import (
    QuotaExceeded,
    ResourceUnavailable,
    TransientError,
    YouTubeClient,
)
from studies.comments.population import (
    PopulationConfig,
    freeze,
    load_population,
    write_population,
)
from studies.comments.quota import DEFAULT_DAILY_UNITS, QuotaLedger, quota_day

# `order=time` is mandatory, not a preference: `relevance` is nondeterministic across
# calls and returns a winner-biased slice, which would silently destroy the
# within-video comparisons the study is built on.
COLLECTION_ORDER = "time"


def _api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        sys.exit("YOUTUBE_API_KEY is not set in the environment")
    return key


def parse_thread(item: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one ``commentThreads`` item into a comment row.

    Top-level comments only. Replies are a different exposure regime — they are
    ranked under their parent rather than against the video — so ``reply_count`` is
    kept as a covariate and reply bodies are left uncollected.
    """
    try:
        top = item["snippet"]["topLevelComment"]
        snip = top["snippet"]
    except (KeyError, TypeError):
        return None
    text = snip.get("textDisplay") or snip.get("textOriginal")
    if text is None or snip.get("publishedAt") is None:
        return None
    author = snip.get("authorChannelId") or {}
    return {
        "comment_id": top["id"],
        "author_channel_id": author.get("value") if isinstance(author, dict) else None,
        "text": text,
        "like_count": int(snip.get("likeCount") or 0),
        "reply_count": int(item["snippet"].get("totalReplyCount") or 0),
        "published_at": snip["publishedAt"],
        "updated_at": snip.get("updatedAt"),
    }


def collect_video(
    conn: Any, client: YouTubeClient, ledger: QuotaLedger, video: Any
) -> tuple[str, int]:
    """Enumerate one video to completion (or until quota runs out).

    Returns ``(outcome, comments_added)`` where outcome is one of
    ``done`` / ``skipped`` / ``failed`` / ``quota`` / ``paused``.
    """
    video_id = video["video_id"]
    token = video["page_token"]
    added = 0

    while True:
        if not ledger.can_spend(1):
            return "paused", added
        try:
            body = client.get(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "order": COLLECTION_ORDER,
                    "maxResults": 100,
                    "textFormat": "plainText",
                    "pageToken": token,
                },
            )
        except QuotaExceeded:
            ledger.exhaust()
            return "quota", added
        except ResourceUnavailable as exc:
            store.finish_video(conn, video_id, store.SKIPPED, exc.reason)
            return "skipped", added
        except TransientError as exc:
            store.finish_video(conn, video_id, store.FAILED, str(exc)[:200])
            return "failed", added

        # Charged only now: the project bills for responses, not attempts.
        ledger.spend(1)
        fetched_at = datetime.now(UTC).isoformat()
        rows = [r for r in (parse_thread(i) for i in body.get("items", [])) if r]
        new = store.store_comments(conn, video_id, rows, fetched_at)
        added += new
        token = body.get("nextPageToken")
        store.checkpoint_page(conn, video_id, token, new)

        if not token:
            _probe_relevance(conn, client, ledger, video_id)
            store.finish_video(conn, video_id, store.DONE)
            return "done", added


def _probe_relevance(conn: Any, client: YouTubeClient, ledger: QuotaLedger, video_id: str) -> None:
    """Record one page of the displayed `order=relevance` ranking.

    This is the feedback-loop variable: YouTube's own ranking drives visibility, which
    drives likes, which drives ranking. Without observing the displayed order, rank and
    text quality are not separable in the analysis.
    """
    if not ledger.can_spend(1):
        return
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
        return
    except (ResourceUnavailable, TransientError):
        return
    ledger.spend(1)
    ids = [
        item["snippet"]["topLevelComment"]["id"]
        for item in body.get("items", [])
        if item.get("snippet", {}).get("topLevelComment")
    ]
    store.store_relevance_ranks(conn, video_id, ids, datetime.now(UTC).isoformat())


def run(args: argparse.Namespace) -> int:
    # Resolve the credential before touching disk so a missing key cannot leave a
    # half-initialised database behind.
    client = YouTubeClient(_api_key())
    population = load_population(args.population)
    conn = store.connect(args.db)
    store.bind_population(conn, population["config"]["population_hash"])
    store.upsert_videos(conn, population["videos"])

    ledger = QuotaLedger(conn, budget=args.daily_budget)
    day = quota_day()
    # Line-buffered explicitly: a multi-hour run is normally launched detached with
    # stdout to a log, where Python's default block buffering hides all progress until
    # the process exits — which is exactly when the progress stops being useful.
    print(
        f"[{day}] quota {ledger.used(day)}/{ledger.budget} used; "
        f"{len(population['videos'])} videos in population "
        f"{population['config']['population_hash'][:12]}",
        flush=True,
    )

    videos_done = 0
    while args.max_videos is None or videos_done < args.max_videos:
        video = store.next_video(conn)
        if video is None:
            print("population fully collected")
            break
        outcome, added = collect_video(conn, client, ledger, video)
        if outcome in {"done", "skipped", "failed"}:
            videos_done += 1
        print(
            f"  {video['video_id']} {outcome:<8} +{added} comments "
            f"(quota {ledger.used()}/{ledger.budget})",
            flush=True,
        )
        if outcome in {"quota", "paused"}:
            print("daily quota spent — exiting; re-run after the Pacific-midnight reset")
            break

    print(json.dumps(store.summary(conn), indent=2))
    conn.close()
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    client = YouTubeClient(_api_key())
    config = PopulationConfig.load(args.config)
    conn = store.connect(args.db)
    ledger = QuotaLedger(conn, budget=args.daily_budget)

    result = freeze(client, ledger, config)
    path = write_population(args.out, config, result)
    estimated = sum(v["comment_count"] for v in result.videos)
    print(f"froze {len(result.videos)} videos from {len(result.channels)} channels -> {path}")
    print(f"  excluded: {result.excluded}")
    print(f"  ~{estimated:,} comments, ~{sum(1 for _ in result.videos)} videos to enumerate")
    print(f"  freeze cost {result.units_spent} units")
    conn.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = store.connect(args.db)
    ledger = QuotaLedger(conn, budget=args.daily_budget)
    info = store.summary(conn)
    info["quota_day"] = quota_day()
    info["quota_used_today"] = ledger.used()
    info["quota_budget"] = ledger.budget
    print(json.dumps(info, indent=2))
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studies.comments.collect", description=__doc__)
    parser.add_argument(
        "--daily-budget",
        type=int,
        default=DEFAULT_DAILY_UNITS - 500,
        help="units to spend per Pacific day (default leaves headroom under the 10k cap)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_freeze = sub.add_parser("freeze", help="resolve channels into a frozen video population")
    p_freeze.add_argument("--config", required=True)
    p_freeze.add_argument("--out", required=True)
    p_freeze.add_argument("--db", default="data/runs/comments/corpus.sqlite")
    p_freeze.set_defaults(func=cmd_freeze)

    p_run = sub.add_parser("run", help="collect until the day's quota is spent")
    p_run.add_argument("--population", required=True)
    p_run.add_argument("--db", default="data/runs/comments/corpus.sqlite")
    p_run.add_argument("--max-videos", type=int, default=None)
    p_run.set_defaults(func=run)

    p_status = sub.add_parser("status", help="print corpus and quota counters")
    p_status.add_argument("--db", default="data/runs/comments/corpus.sqlite")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
