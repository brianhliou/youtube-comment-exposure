"""Study B outcome distribution (H7) per language, re-run 2026-10-21 so the numbers have a file.

Replaces the 10-09 scratch script described in V2-RESULTS.md (same definitions):
videos with status='done', language from config/panel-channels.json, marquee excluded.
Also writes v1's arrival table (mean log1p(likes) by age at posting) per language,
labelled exploratory.

    .venv/bin/python3 studies/comments/results/v2/final/bulk_outcome.py
"""

import json
import math
import sqlite3
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
DB = ROOT / "data/runs/comments/v2.sqlite"
CHANNELS = ROOT / "studies/comments/config/panel-channels.json"
OUT = Path(__file__).with_name("bulk-outcome.json")
BUCKETS = [(0, 1, "<1h"), (1, 6, "1-6h"), (6, 24, "6-24h"), (24, 168, "1-7d"),
           (168, 720, "7-30d"), (720, math.inf, ">30d")]


def main() -> None:
    chans = json.loads(CHANNELS.read_text())["channels"]
    lang_of = {c["handle"]: c["lang"] for c in chans if not c.get("marquee")}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT v.channel_handle, cm.video_id, cm.like_count,
               (julianday(cm.published_at) - julianday(v.published_at)) * 24.0
        FROM comments cm JOIN videos v USING(video_id)
        WHERE v.status = 'done'
        """
    )
    likes: dict[str, list[int]] = {}
    ages: dict[str, list[float]] = {}
    per_video: dict[str, dict[str, list[int]]] = {}
    channels: dict[str, set] = {}
    for handle, vid, lk, age in rows:
        lang = lang_of.get(handle)
        if lang is None:
            continue
        likes.setdefault(lang, []).append(lk)
        ages.setdefault(lang, []).append(age if age is not None else float("nan"))
        per_video.setdefault(lang, {}).setdefault(vid, []).append(lk)
        channels.setdefault(lang, set()).add(handle)
    conn.close()

    out: dict = {"db": str(DB.relative_to(ROOT)), "by_lang": {}}
    groups = dict(likes)
    groups["pooled"] = [x for lang in ("en", "ja", "zh") for x in likes.get(lang, [])]
    for lang, lk in groups.items():
        a = np.array(lk)
        n = len(a)
        k = max(1, round(0.01 * n))
        top = np.sort(a)[::-1][:k].sum()
        row = {
            "comments": n,
            "zero_like_share": round(float((a == 0).mean()), 4),
            "le1_like_share": round(float((a <= 1).mean()), 4),
            "top1pct_like_share": round(float(top / max(1, a.sum())), 4),
        }
        if lang != "pooled":
            vids = per_video[lang]
            row["videos"] = len(vids)
            row["channels"] = len(channels[lang])
            row["median_comments_per_video"] = float(np.median([len(v) for v in vids.values()]))
            row["median_video_zero_like_share"] = round(
                float(np.median([np.mean(np.array(v) == 0) for v in vids.values()])), 4)
            ag = np.array(ages[lang])
            ll = np.log1p(a)
            row["exploratory_mean_log1p_likes_by_age"] = {
                name: round(float(ll[(ag >= lo) & (ag < hi)].mean()), 4)
                for lo, hi, name in BUCKETS if ((ag >= lo) & (ag < hi)).any()
            }
        else:
            row["videos"] = sum(len(per_video[x]) for x in ("en", "ja", "zh"))
            row["channels"] = sum(len(channels[x]) for x in ("en", "ja", "zh"))
        out["by_lang"][lang] = row
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
