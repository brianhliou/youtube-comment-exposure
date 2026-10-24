"""Exploratory: do comments posted long after upload reach the top page of older videos?

Prompted by a counterexample seen by hand: a 7-year-old video whose top page held a
comment posted four weeks earlier with 3k+ likes. Neither snapshot corpus has a video
that old, so this asks the nearest question the data can answer: on the oldest videos
we have, what share of top-page slots go to comments posted 30+ or 90+ days after
upload, against those comments' share of all comments on the same videos?

Top page = the stored order=relevance page (relevance_ranks), creator's own comments
dropped and the page re-ranked. Video age = rank fetch minus video publish. Each corpus
is reported separately (386 videos are in both). v2 excludes the marquee channel.

    .venv/bin/python3 studies/comments/results/v2/final/late_old_videos.py
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
CORPORA = {
    "v1": ROOT / "data/runs/comments/stratified.sqlite",
    "v2": ROOT / "data/runs/comments/v2.sqlite",
}
CHANNELS = ROOT / "studies/comments/config/panel-channels.json"
OUT = Path(__file__).with_name("late-old-videos.json")
AGE_BANDS = [(0, 90, "<3mo"), (90, 180, "3-6mo"), (180, 365, "6-12mo"), (365, 10_000, ">12mo")]
LATE = (30, 90)
TOPS = (3, 20)


def band(days: float) -> str:
    return next(name for lo, hi, name in AGE_BANDS if lo <= days < hi)


def run(db: Path, marquee: set[str]) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    vids = {}
    for vid, handle, chan, pub in c.execute(
            """select video_id, channel_handle, channel_id, published_at
               from videos where status='done'"""):
        if handle not in marquee:
            vids[vid] = (chan, pub)

    # Video age at the time its top page was read.
    age_at_rank = {vid: d for vid, d in c.execute(
        """select r.video_id, julianday(max(r.fetched_at)) - julianday(v.published_at)
           from relevance_ranks r join videos v using(video_id) group by r.video_id""")
        if vid in vids}

    # All comments: count by posting lag, per video, excluding the creator.
    total = defaultdict(int)
    late_total = {d: defaultdict(int) for d in LATE}
    for vid, auth, lag in c.execute(
            """select cm.video_id, cm.author_channel_id,
                      julianday(cm.published_at) - julianday(v.published_at)
               from comments cm join videos v using(video_id) where v.status='done'"""):
        if vid not in age_at_rank or auth == vids[vid][0]:
            continue
        total[vid] += 1
        for d in LATE:
            if lag >= d:
                late_total[d][vid] += 1

    # Top page, creator dropped, re-ranked.
    pages = defaultdict(list)
    for vid, rank, auth, lag, likes in c.execute(
            """select r.video_id, r.rank, cm.author_channel_id,
                      julianday(cm.published_at) - julianday(v.published_at), cm.like_count
               from relevance_ranks r join comments cm using(comment_id)
               join videos v on v.video_id = r.video_id"""):
        if vid in age_at_rank and auth != vids[vid][0]:
            pages[vid].append((rank, lag, likes))

    out = {"db": str(db.relative_to(ROOT)), "bands": {}}
    examples = []
    for _, _, bname in AGE_BANDS:
        bvids = [v for v, a in age_at_rank.items() if band(a) == bname and v in pages]
        if not bvids:
            continue
        ages = sorted(age_at_rank[v] for v in bvids)
        b = {"videos": len(bvids),
             "video_age_days_median": round(ages[len(ages) // 2], 1),
             "comments": sum(total[v] for v in bvids)}
        for d in LATE:
            all_share = sum(late_total[d][v] for v in bvids) / max(1, b["comments"])
            b[f"posted_after_{d}d_share_of_all_comments"] = round(all_share, 4)
            for k in TOPS:
                slots = late = 0
                vids_with = 0
                for v in bvids:
                    top = sorted(pages[v])[:k]
                    n_late = sum(1 for _, lag, _ in top if lag >= d)
                    slots += len(top)
                    late += n_late
                    vids_with += n_late > 0
                share = late / max(1, slots)
                b[f"top{k}_posted_after_{d}d"] = {
                    "slots": slots, "late": late, "share": round(share, 4),
                    "videos_with_one": vids_with,
                    "ratio_to_all": round(share / all_share, 2) if all_share else None,
                }
        out["bands"][bname] = b
        for v in bvids:
            for rank, lag, likes in sorted(pages[v])[:3]:
                if lag >= 30:
                    examples.append({"video_id": v, "page_rank": rank,
                                     "posted_days_after_upload": round(lag, 1),
                                     "video_age_days": round(age_at_rank[v], 1), "likes": likes})
    out["late_top3_examples_by_likes"] = sorted(examples, key=lambda e: -e["likes"])[:10]
    return out


def main() -> None:
    channels = json.loads(CHANNELS.read_text())["channels"]
    marquee = {c["handle"] for c in channels if c.get("marquee")}
    out = {"note": "exploratory, not pre-registered", "late_days": list(LATE), "tops": list(TOPS),
           **{name: run(db, marquee if name == "v2" else set()) for name, db in CORPORA.items()}}
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
