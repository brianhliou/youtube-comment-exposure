"""Exploratory (not in V2.md): does the 12-hour top-10 movement accumulate?

On the 250 videos the noise-floor test uses (closed, non-marquee, inclusion rule at the
last look, >= 20 ranked comments), from 96 h on: mean top-10 Jaccard between two looks
by lag, and where comments that leave the top 10 in one 12-hour step go next.

    .venv/bin/python3 studies/comments/results/v2/final/lag_overlap.py
"""

import json
import statistics as st
from pathlib import Path

from studies.comments import panel_analysis as pa
from studies.comments import v2_noise_floor as nf

SNAPSHOT = "data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite"
OUT = Path(__file__).parent / "lag-overlap.json"
LAGS = (1, 2, 4, 6, 10, 14, 20)  # in 12-hour periods


def main() -> None:
    vids = nf.eligible(SNAPSHOT)
    by_lag: dict[int, list[float]] = {}
    left = back_next = back_two = 0
    new_rank: list[int] = []
    for v in vids:
        obs = [o for o in v.obs if o.age_h >= 96]
        for i, o in enumerate(obs):
            for later in obs[i + 1 :]:
                lag = round((later.age_h - o.age_h) / 12)
                if lag in LAGS:
                    j = pa.jaccard(o.top(10), later.top(10))
                    if j is not None:
                        by_lag.setdefault(lag, []).append(j)
        for i in range(len(obs) - 3):
            a, b, c, d = obs[i : i + 4]
            if not 11 < b.age_h - a.age_h < 13:
                continue
            rank_b = {cid: r for r, cid, _, _ in b.ranked}
            for cid in a.top(10) - b.top(10):
                left += 1
                new_rank.append(rank_b.get(cid, 999))  # 999: off the 100-comment page
                if cid in c.top(10):
                    back_next += 1
                elif cid in d.top(10):
                    back_two += 1
    out = {
        "videos": len(vids),
        "from_age_h": 96,
        "mean_top10_jaccard_by_lag_h": {
            str(lag * 12): {"n": len(js), "mean": round(st.mean(js), 4)}
            for lag, js in sorted(by_lag.items())
        },
        "leavers": {
            "n": left,
            "median_rank_after": st.median(new_rank),
            "share_rank_le_15_after": round(sum(r <= 15 for r in new_rank) / left, 4),
            "share_back_next_look": round(back_next / left, 4),
            "share_back_within_two_looks": round((back_next + back_two) / left, 4),
        },
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
