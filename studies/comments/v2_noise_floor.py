"""Noise floor for the panel's "the ranking moved" claims (V2.md amendment 2026-10-22).

The panel's reliability repeats were a median 46 s after the page they repeat and matched
it almost always (median top-10 Jaccard 1.00). A repeat that close may be served from a
cache, in which case it measures the cache, not the ranking. This test re-fetches the
closed panel videos (25-33 days old, so an hour of real movement is small) under three
conditions and asks how far apart two fetches are when nothing much has happened:

- ``A``: the panel's exact request (``part=snippet``, ``order=relevance``,
  ``maxResults=100``, ``textFormat=plainText``).
- ``B``: the same request again, back to back with ``C`` in random order.
- ``C``: a cache-busted request: ``part=id,snippet``, ``textFormat=html`` and a random
  ``quotaUser``, so no response cache keyed on the URL can answer it.
- ``D``/``E``: a standard and a cache-busted page 15, 60 or 120 minutes after ``A``
  (gap assigned round-robin).

Creator comments are dropped and pages re-ranked, as in ``panel_analysis.py``.

    python -m studies.comments.v2_noise_floor run --snapshot <panel snapshot> --db <out.sqlite>
    python -m studies.comments.v2_noise_floor summarize --snapshot <panel snapshot> \\
        --db <out.sqlite> --supplement <panel-supplement.json> --day20 <panel-day20.json> \\
        --json <noise-floor.json>

``YOUTUBE_API_KEY`` comes from the environment and is never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studies.comments import panel_analysis as pa
from studies.comments.client import (
    QuotaExceeded,
    ResourceUnavailable,
    TransientError,
    YouTubeClient,
)
from studies.comments.quota import QuotaLedger

GAPS_MIN = (15, 60, 120)
STANDARD = {"part": "snippet", "order": "relevance", "maxResults": 100, "textFormat": "plainText"}
BUSTED = {"part": "id,snippet", "order": "relevance", "maxResults": 100, "textFormat": "html"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
    video_id   TEXT NOT NULL,
    label      TEXT NOT NULL,
    gap_min    INTEGER,
    fetched_at TEXT NOT NULL,
    n_items    INTEGER NOT NULL,
    etag       TEXT,
    busted     INTEGER NOT NULL,
    outcome    TEXT NOT NULL,
    PRIMARY KEY (video_id, label)
);
CREATE TABLE IF NOT EXISTS items (
    video_id          TEXT NOT NULL,
    label             TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    comment_id        TEXT NOT NULL,
    author_channel_id TEXT,
    like_count        INTEGER NOT NULL,
    PRIMARY KEY (video_id, label, rank)
);
CREATE TABLE IF NOT EXISTS quota_ledger (
    day_pt     TEXT PRIMARY KEY,
    units_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def eligible(snapshot: str) -> list[pa.Video]:
    """Closed, non-marquee panel videos that pass the lock-in inclusion rule at their
    last observation and had at least 20 non-creator comments on that page."""
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    closed = {r[0] for r in conn.execute("SELECT video_id FROM panel_videos WHERE status='closed'")}
    out = []
    for v in pa.load(conn):
        if v.marquee or v.video_id not in closed or not v.obs:
            continue
        last = v.obs[-1]
        count = v.comment_count_at.get(last.period)
        if count is None or count * pa.THREAD_SHARE < pa.MIN_TOP_LEVEL:
            continue
        if len(last.ranked) < 20:
            continue
        out.append(v)
    return out


def fetch(conn, client, ledger, video_id: str, label: str, busted: bool, gap: int | None) -> bool:
    """Store one page under ``label``. False means the quota is gone: stop."""
    params = dict(BUSTED if busted else STANDARD)
    params["videoId"] = video_id
    if busted:
        params["quotaUser"] = f"{random.getrandbits(64):016x}"
    try:
        body = client.get("commentThreads", params)
    except QuotaExceeded:
        ledger.exhaust()
        return False
    except ResourceUnavailable as exc:
        conn.execute(
            "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,0,NULL,?,?)",
            (video_id, label, gap, _now(), int(busted), f"unavailable:{exc.args[0]}"),
        )
        conn.commit()
        return True
    except TransientError:
        conn.execute(
            "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,0,NULL,?,'transient')",
            (video_id, label, gap, _now(), int(busted)),
        )
        conn.commit()
        return True
    ledger.spend(1)
    fetched_at = _now()
    items = body.get("items", [])
    for rank, item in enumerate(items, start=1):
        top = item.get("snippet", {}).get("topLevelComment")
        if not top:
            continue
        s = top["snippet"]
        conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)",
            (
                video_id,
                label,
                rank,
                top["id"],
                (s.get("authorChannelId") or {}).get("value"),
                int(s.get("likeCount", 0)),
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?,?,'ok')",
        (video_id, label, gap, fetched_at, len(items), body.get("etag"), int(busted)),
    )
    conn.commit()
    return True


def run(args: argparse.Namespace) -> int:
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        sys.exit("YOUTUBE_API_KEY is not set in the environment")
    client = YouTubeClient(key)
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    ledger = QuotaLedger(conn, budget=args.budget)
    rng = random.Random(0)
    random.seed(1)
    vids = sorted(v.video_id for v in eligible(args.snapshot))
    rng.shuffle(vids)
    gap_of = {vid: GAPS_MIN[i % len(GAPS_MIN)] for i, vid in enumerate(vids)}
    print(f"{_now()} {len(vids)} videos, budget {args.budget}", flush=True)

    done_a = {
        r[0]: r[1] for r in conn.execute("SELECT video_id, fetched_at FROM fetches WHERE label='A'")
    }
    for vid in vids:
        if vid in done_a:
            continue
        if not ledger.can_spend(3):
            print("budget reached in round 1", flush=True)
            break
        g = gap_of[vid]
        if not fetch(conn, client, ledger, vid, "A", False, g):
            print("quota exceeded in round 1", flush=True)
            return 2
        order = [("B", False), ("C", True)]
        rng.shuffle(order)
        for label, busted in order:
            if not fetch(conn, client, ledger, vid, label, busted, g):
                return 2
    print(f"{_now()} round 1 done, units {ledger.used()}", flush=True)

    due = []
    for vid, a_at, g in conn.execute(
        "SELECT video_id, fetched_at, gap_min FROM fetches WHERE label='A' AND outcome='ok'"
    ):
        t = datetime.fromisoformat(a_at).timestamp() + g * 60
        due.append((t, vid, g))
    have_e = {r[0] for r in conn.execute("SELECT video_id FROM fetches WHERE label='E'")}
    for t, vid, g in sorted(due):
        if vid in have_e:
            continue
        if not ledger.can_spend(2):
            print("budget reached in round 2", flush=True)
            break
        wait = t - time.time()
        if wait > 0:
            time.sleep(wait)
        order = [("D", False), ("E", True)]
        rng.shuffle(order)
        for label, busted in order:
            if not fetch(conn, client, ledger, vid, label, busted, g):
                return 2
    print(f"{_now()} DONE units {ledger.used()}", flush=True)
    return 0


# ----------------------------------------------------------------------------- summary


def _pages(conn, channel_of: dict[str, str | None]) -> dict[tuple[str, str], list[str]]:
    rows = defaultdict(list)
    for vid, label, _rank, cid, author in conn.execute(
        "SELECT i.video_id, i.label, i.rank, i.comment_id, i.author_channel_id FROM items i "
        "JOIN fetches f ON f.video_id=i.video_id AND f.label=i.label AND f.outcome='ok' "
        "ORDER BY i.video_id, i.label, i.rank"
    ):
        ch = channel_of.get(vid)
        if ch and author == ch:
            continue  # creator comment: dropped and re-ranked, as in panel_analysis
        rows[(vid, label)].append(cid)
    return rows


def _boot_mean(groups: dict[str, list[float]], seed: int = 0, b: int = 1000) -> list[float] | None:
    clusters = [g for g in groups.values() if g]
    if len(clusters) < 5:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        flat = [x for c in rng.choices(clusters, k=len(clusters)) for x in c]
        vals.append(statistics.fmean(flat))
    vals.sort()
    return [round(vals[int(0.025 * b)], 4), round(vals[int(0.975 * b) - 1], 4)]


def summarize(args: argparse.Namespace) -> int:
    videos = {v.video_id: v for v in eligible(args.snapshot)}
    channel_of = {vid: v.channel_id for vid, v in videos.items()}
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    pages = _pages(conn, channel_of)
    meta = {
        (r[0], r[1]): {"at": r[2], "etag": r[3], "gap": r[4]}
        for r in conn.execute(
            "SELECT video_id, label, fetched_at, etag, gap_min FROM fetches WHERE outcome='ok'"
        )
    }
    units = conn.execute("SELECT COALESCE(SUM(units_used),0) FROM quota_ledger").fetchone()[0]
    outcomes = dict(conn.execute("SELECT outcome, COUNT(*) FROM fetches GROUP BY 1").fetchall())

    def pair_stats(x: str, y: str, gap: int | None = None) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k in (10, 20):
            by_ch: dict[str, list[float]] = defaultdict(list)
            js, secs, etag_same = [], [], []
            for vid, v in videos.items():
                if gap is not None and meta.get((vid, "A"), {}).get("gap") != gap:
                    continue
                a, b = pages.get((vid, x)), pages.get((vid, y))
                if not a or not b or len(a) < k or len(b) < k:
                    continue
                j = len(set(a[:k]) & set(b[:k])) / len(set(a[:k]) | set(b[:k]))
                js.append(j)
                by_ch[v.channel].append(j)
                ta = datetime.fromisoformat(meta[(vid, x)]["at"])
                tb = datetime.fromisoformat(meta[(vid, y)]["at"])
                secs.append(abs((tb - ta).total_seconds()))
                etag_same.append(meta[(vid, x)]["etag"] == meta[(vid, y)]["etag"])
            if not js:
                continue
            res[f"top{k}"] = {
                "n": len(js),
                "mean_jaccard": round(statistics.fmean(js), 4),
                "mean_ci95_cluster": _boot_mean(by_ch),
                "median_jaccard": round(statistics.median(js), 4),
                "share_below_1": round(sum(j < 1 for j in js) / len(js), 4),
                "share_below_0.8": round(sum(j < 0.8 for j in js) / len(js), 4),
            }
            if k == 10:
                res["median_gap_s"] = round(statistics.median(secs), 1)
                res["share_same_etag"] = round(sum(etag_same) / len(etag_same), 4)
        return res

    pairs = {
        "A-B standard, back to back": pair_stats("A", "B"),
        "A-C standard vs cache-busted, back to back": pair_stats("A", "C"),
        "B-C": pair_stats("B", "C"),
    }
    for g in GAPS_MIN:
        pairs[f"A-D standard, {g} min"] = pair_stats("A", "D", g)
        pairs[f"C-E cache-busted, {g} min"] = pair_stats("C", "E", g)
        pairs[f"A-E standard vs cache-busted, {g} min"] = pair_stats("A", "E", g)
    pairs["A-D standard, all gaps"] = pair_stats("A", "D")
    pairs["C-E cache-busted, all gaps"] = pair_stats("C", "E")

    uncached = [
        "A-C standard vs cache-busted, back to back",
        "A-D standard, all gaps",
        "C-E cache-busted, all gaps",
    ]
    floor = {}
    for k in ("top10", "top20"):
        cands = {name: pairs[name][k]["mean_jaccard"] for name in uncached if k in pairs[name]}
        name = min(cands, key=cands.get)
        floor[k] = {"mean_jaccard": cands[name], "condition": name}

    # Observed movement in the panel (10-21 snapshot outputs).
    sup = json.loads(Path(args.supplement).read_text())["exploratory_adjacent_overlap"]
    late = {b: d for b, d in sup.items() if int(b.split("-")[0].rstrip("h+")) >= 96}
    n_late = sum(d["n"] for d in late.values())
    adj = sum(d["n"] * d["mean_top10_jaccard"] for d in late.values()) / n_late
    d20 = json.loads(Path(args.day20).read_text())["persistence"]["144-168h"]["mean_top20_jaccard"]

    def share(f: float, obs: float) -> float:
        return round((1 - f) / (1 - obs), 3)

    # Real drift since the panel's last page (day ~20.5) to now (day ~25-33), top 10.
    drift = []
    for vid, v in videos.items():
        a = pages.get((vid, "A"))
        if not a or len(a) < 10:
            continue
        last = sorted(v.obs[-1].ranked)[:10]
        drift.append(
            len({c for _, c, _, _ in last} & set(a[:10]))
            / len({c for _, c, _, _ in last} | set(a[:10]))
        )
    ages = []
    for vid, v in videos.items():
        if (vid, "A") in meta:
            ages.append(
                (
                    datetime.fromisoformat(meta[(vid, "A")]["at"])
                    - datetime.fromisoformat(v.published_at.replace("Z", "+00:00"))
                ).total_seconds()
                / 86400
            )

    out = {
        "design": "V2.md amendment 2026-10-22; see module docstring",
        "units_spent": units,
        "fetch_outcomes": outcomes,
        "videos_eligible": len(videos),
        "videos_fetched": len({vid for vid, lab in meta if lab == "A"}),
        "video_age_days_at_A": {
            "min": round(min(ages), 1),
            "median": round(statistics.median(ages), 1),
            "max": round(max(ages), 1),
        }
        if ages
        else None,
        "pairs": pairs,
        "floor": floor,
        "observed": {
            "adjacent_12h_top10_jaccard_from_96h": round(adj, 4),
            "adjacent_n": n_late,
            "day7_vs_day20_top20_jaccard": d20,
        },
        "share_of_movement_inside_floor": {
            "adjacent_12h_top10": share(floor["top10"]["mean_jaccard"], adj),
            "day7_vs_day20_top20": share(floor["top20"]["mean_jaccard"], d20),
        },
        "drift_last_panel_page_to_A_top10": {
            "n": len(drift),
            "mean_jaccard": round(statistics.fmean(drift), 4) if drift else None,
        },
    }
    Path(args.json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="v2_noise_floor")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshot", required=True)
    r.add_argument("--db", required=True)
    r.add_argument("--budget", type=int, default=3000)
    r.add_argument("--list-only", action="store_true")
    s = sub.add_parser("summarize")
    s.add_argument("--snapshot", required=True)
    s.add_argument("--db", required=True)
    s.add_argument("--supplement", required=True)
    s.add_argument("--day20", required=True)
    s.add_argument("--json", required=True)
    args = p.parse_args(argv)
    if args.cmd == "run" and args.list_only:
        vids = eligible(args.snapshot)
        print(len(vids), "eligible;", 5 * len(vids), "units at 5 per video")
        return 0
    return run(args) if args.cmd == "run" else summarize(args)


if __name__ == "__main__":
    raise SystemExit(main())
