"""Exploratory: how many comments in each video's final top 3 were posted late (2026-10-21).

Final top 3 = ranks 1-3 on each closed video's last regular fetch (not a repeat),
creator's own comments and the marquee channel excluded. Snapshot DB, read-only.

    .venv/bin/python3 studies/comments/results/v2/final/late_top3.py
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
DB = ROOT / "data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite"
CHANNELS = ROOT / "studies/comments/config/panel-channels.json"
OUT = Path(__file__).with_name("late-top3.json")


def main() -> None:
    marquee = {c["handle"] for c in json.loads(CHANNELS.read_text())["channels"] if c.get("marquee")}
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    vids = {r[0]: r for r in c.execute(
        "select video_id, channel_handle, channel_id, lang, published_at from panel_videos where status='closed'")}
    rows = c.execute("""
        with last as (select video_id, max(period) p from rank_fetches
                      where period not like '%r' and outcome='ok' group by video_id)
        select o.video_id, o.rank, o.comment_id, o.author_channel_id, o.published_at, l.p
        from rank_observations o join last l on o.video_id=l.video_id and o.period=l.p
        where o.rank<=3""").fetchall()
    slots, by = 0, {"24h": [], "48h": [], "7d": []}
    entry = []
    for vid, rank, cid, auth, pub, period in rows:
        v = vids.get(vid)
        if not v or v[1] in marquee or auth == v[2]:
            continue
        slots += 1
        age = c.execute("select (julianday(?)-julianday(?))*24", (pub, v[4])).fetchone()[0]
        for k, h in (("24h", 24), ("48h", 48), ("7d", 168)):
            if age >= h:
                by[k].append(vid)
        if age >= 48:
            first = c.execute("""select (julianday(fetched_at)-julianday(?))*24, rank, like_count
                                 from rank_observations where comment_id=? and period not like '%r'
                                 order by fetched_at limit 1""", (v[4], cid)).fetchone()
            entry.append(first[1])
    out = {
        "db": str(DB.relative_to(ROOT)),
        "final_top3_slots": slots,
        "videos": len({r[0] for r in rows if r[0] in vids and vids[r[0]][1] not in marquee}),
        **{f"posted_after_{k}": {"slots": len(v), "share": round(len(v) / slots, 4),
                                 "videos": len(set(v))} for k, v in by.items()},
        "posted_after_48h_first_seen_rank_median": sorted(entry)[len(entry) // 2] if entry else None,
        "posted_after_48h_first_seen_rank_gt10": sum(1 for r in entry if r > 10),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
