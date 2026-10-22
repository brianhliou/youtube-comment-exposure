"""Study A (V2.md) items the plan requires that ``panel_analysis.py`` does not print.

Point estimates come from ``panel_analysis`` functions unchanged; this file adds the
reporting the plan asks for around them, plus a few exploratory checks, each labelled.

Plan-required (V2.md text, not new analysis):
- inclusion-rule drops **by language** ("Report how many videos the rule drops, by language");
- channel-clustered intervals on every headline number ("Standard errors are clustered by
  channel in every analysis"); the 09-19 script only bootstraps the lock-in median;
- lock-in **per genre** ("Report per genre");
- the marquee per-channel rows ("They appear in the per-channel table");
- the kill gate as written ("videos with ≥ 7 observation days");
- quiet and unresolved channels ("reported, not silently carried");
- H4 on both H2 thresholds (the script checks the 24-hour share only).

Exploratory (labelled in the output): adjacent-period top-10 overlap by age, and the
feedback-loop ratio per period rather than per hour and as a median.

    python -m studies.comments.v2_panel_supplement --db <panel snapshot> --json <out>
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from studies.comments import panel_analysis as pa

B = 1000


def _boot(groups: dict[str, list], stat, seed: int = 0) -> list[float] | None:
    """Percentile 95% CI of ``stat(flat list)``, resampling channels with replacement."""
    clusters = [v for v in groups.values() if v]
    if len(clusters) < 5:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(B):
        sample = [x for c in rng.choices(clusters, k=len(clusters)) for x in c]
        s = stat(sample)
        if s is not None:
            vals.append(s)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)]


def drops_by_lang(videos: list[pa.Video], hours: float) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for v in videos:
        d = out[v.lang]
        d["enrolled"] += 1
        if v.marquee:
            d["marquee"] += 1
            continue
        end = v.endpoint(hours)
        if end is None:
            d["no_endpoint_yet"] += 1
            continue
        c = v.comment_count_at.get(end.period)
        if c is None or c * pa.THREAD_SHARE < pa.MIN_TOP_LEVEL:
            d["under_min_comments"] += 1
        else:
            d["included"] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def entry_age_ci(videos: list[pa.Video], hours: float) -> dict[str, Any]:
    by_ch: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for v in videos:
        end = pa._eligible(v, hours)
        if end is None:
            continue
        for rank, _c, _l, posted in end.ranked:
            if rank <= 20 and posted:
                by_ch[v.lang][v.channel].append(pa._hours(posted, v.published_at))

    def share(th):
        return lambda xs: (sum(a <= th for a in xs) / len(xs)) if xs else None

    out = {}
    pooled: dict[str, list[float]] = defaultdict(list)
    for lang, chs in sorted(by_ch.items()):
        for ch, xs in chs.items():
            pooled[ch].extend(xs)
        out[lang] = {f"within_{t}h_ci95": _boot(chs, share(t)) for t in (1, 6, 24)}
        flat = [x for xs in chs.values() for x in xs]
        out[lang].update({f"within_{t}h": round(share(t)(flat), 4) for t in (1, 6, 24)})
        out[lang]["channels"] = len(chs)
    flat = [x for xs in pooled.values() for x in xs]
    out["pooled"] = {f"within_{t}h": round(share(t)(flat), 4) for t in (1, 6, 24)}
    out["pooled"].update({f"within_{t}h_ci95": _boot(pooled, share(t)) for t in (1, 6, 24)})
    out["pooled"]["channels"] = len(pooled)
    return out


def lockin_extras(videos: list[pa.Video], hours: float, lk: dict[str, Any]) -> dict[str, Any]:
    rows = lk["per_video"]
    genre = _genres()
    by_genre: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["lockin_first_h"] is not None:
            by_genre[genre.get(r["channel"], "?")].append(r["lockin_first_h"])
    per_genre = {g: pa.quantiles(v) for g, v in sorted(by_genre.items())}

    # Shorts minus long-form difference in median, clustered by channel (H5).
    groups: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for r in rows:
        if r["lockin_first_h"] is not None and r["short"] is not None:
            groups[r["channel"]].append((r["short"], r["lockin_first_h"]))

    def diff(xs):
        s = [h for is_s, h in xs if is_s]
        lg = [h for is_s, h in xs if not is_s]
        return statistics.median(lg) - statistics.median(s) if s and lg else None

    flat = [x for xs in groups.values() for x in xs]
    # H1 per language (the pooled H1 test applied in each language) and the share of
    # videos already within the threshold at 72 h.
    h1_lang = {lang: (s["first_crossing_h"]["median"] or 1e9) <= 72
               for lang, s in lk["by_lang"].items()}
    first_obs = [r["first_obs_age_h"] for r in rows if r["first_obs_age_h"] is not None]
    endpoint_lag = [r["endpoint_age_h"] - hours for r in rows]
    return {
        "per_genre_first_crossing_h": per_genre,
        "long_minus_short_median_h": diff(flat),
        "long_minus_short_ci95": _boot(groups, diff),
        "h1_median_le_72h_by_lang": h1_lang,
        "share_locked_by_72h": round(
            sum(1 for r in rows if r["lockin_first_h"] is not None and r["lockin_first_h"] <= 72)
            / len(rows), 4),
        "first_obs_age_h": pa.quantiles(first_obs),
        "endpoint_obs_minus_target_h": pa.quantiles(endpoint_lag),
        "never_crossed_before_endpoint": sum(
            1 for r in rows if r["lockin_first_h"] == r["endpoint_age_h"]),
    }


def feedback_extras(videos: list[pa.Video]) -> dict[str, Any]:
    """Within-comment ratio with a channel-clustered CI, plus exploratory variants."""
    hi: dict[str, list[float]] = defaultdict(list)
    lo: dict[str, list[float]] = defaultdict(list)
    hi_p: dict[str, list[float]] = defaultdict(list)
    lo_p: dict[str, list[float]] = defaultdict(list)
    chan: dict[str, str] = {}
    for v in videos:
        if v.marquee:
            continue
        for prev, cur in zip(v.obs, v.obs[1:], strict=False):
            dt = cur.age_h - prev.age_h
            if dt <= 0:
                continue
            now = {cid: likes for _, cid, likes, _ in cur.ranked}
            for rank, cid, likes, _ in prev.ranked:
                if cid not in now:
                    continue
                key = f"{v.video_id}:{cid}"
                chan[key] = v.channel
                gain = now[cid] - likes
                if rank <= 10:
                    hi[key].append(gain / dt)
                    hi_p[key].append(gain)
                elif rank > 50:
                    lo[key].append(gain / dt)
                    lo_p[key].append(gain)
    movers = sorted(hi.keys() & lo.keys())
    per_ch: dict[str, list[tuple[float, float]]] = defaultdict(list)
    per_ch_p: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for k in movers:
        per_ch[chan[k]].append((statistics.fmean(hi[k]), statistics.fmean(lo[k])))
        per_ch_p[chan[k]].append((statistics.fmean(hi_p[k]), statistics.fmean(lo_p[k])))

    def ratio(xs):
        if not xs:
            return None
        lo_m = statistics.fmean(b for _, b in xs)
        return statistics.fmean(a for a, _ in xs) / lo_m if lo_m > 0 else None

    def med_ratio(xs):
        r = [a / b for a, b in xs if b > 0]
        return statistics.median(r) if r else None

    flat = [x for xs in per_ch.values() for x in xs]
    flat_p = [x for xs in per_ch_p.values() for x in xs]
    zero_lo = sum(1 for _, b in flat if b <= 0)
    return {
        "movers": len(movers),
        "movers_channels": len(per_ch),
        "ratio_per_hour": ratio(flat),
        "ratio_per_hour_ci95": _boot(per_ch, ratio),
        "exploratory_ratio_per_period": ratio(flat_p),
        "exploratory_median_of_per_comment_ratios": med_ratio(flat),
        "movers_with_zero_or_negative_gain_at_51plus": zero_lo,
    }


def adjacent_overlap(videos: list[pa.Video]) -> dict[str, Any]:
    """EXPLORATORY. Top-10 Jaccard between consecutive observations (~12 h apart), by the
    later one's age: does the page stop moving, independent of any endpoint?"""
    bins: dict[str, list[float]] = defaultdict(list)
    for v in videos:
        if v.marquee:
            continue
        last = v.obs[-1] if v.obs else None
        if last is None:
            continue
        c = v.comment_count_at.get(last.period)
        if c is None or c * pa.THREAD_SHARE < pa.MIN_TOP_LEVEL:
            continue
        for a, b in zip(v.obs, v.obs[1:], strict=False):
            j = pa.jaccard(a.top(10), b.top(10))
            if j is not None:
                bins[_bin(b.age_h)].append(j)
    order = sorted(bins, key=lambda s: int(s.split("-")[0].rstrip("h+")))
    return {b: {"n": len(bins[b]), "mean_top10_jaccard": round(statistics.fmean(bins[b]), 3),
                "share_ge_0.8": round(sum(x >= 0.8 for x in bins[b]) / len(bins[b]), 3)}
            for b in order}


def reliability_extras(videos: list[pa.Video]) -> dict[str, Any]:
    gaps, j10 = [], []
    for v in videos:
        main = {o.period: o for o in v.obs}
        for p, rep in v.repeats.items():
            if p in main:
                gaps.append(pa._hours(rep.fetched_at, main[p].fetched_at) * 3600)
                j = pa.jaccard(main[p].top(10), rep.top(10))
                if j is not None:
                    j10.append(j)
    return {"repeat_gap_seconds": pa.quantiles(gaps), "pairs_below_1": sum(x < 1 for x in j10),
            "min_top10_jaccard": min(j10) if j10 else None}


def gate_and_channels(conn: sqlite3.Connection, videos: list[pa.Video]) -> dict[str, Any]:
    seven = sum(1 for v in videos if not v.marquee and v.obs and v.obs[-1].age_h >= 168)
    conn.row_factory = sqlite3.Row
    last_disc = conn.execute("SELECT max(last_discovery) FROM channels").fetchone()[0]
    cutoff = (datetime.fromisoformat(last_disc.replace("Z", "+00:00")) - timedelta(days=30)
              ).isoformat() if last_disc else None
    quiet = [dict(r) for r in conn.execute(
        "SELECT handle, lang, last_upload_at FROM channels WHERE marquee = 0 AND "
        "(last_upload_at IS NULL OR last_upload_at < ?)", (cutoff,))]
    unresolved = conn.execute(
        "SELECT count(*) FROM channels WHERE resolve_error IS NOT NULL").fetchone()[0]
    enroll = [dict(r) for r in conn.execute(
        "SELECT enrolled_day, count(*) AS n FROM panel_videos GROUP BY 1 ORDER BY 1")]
    return {
        "videos_with_ge_7_observation_days_nonmarquee": seven,
        "last_discovery": last_disc,
        "quiet_channels_30d_before_last_discovery": len(quiet),
        "quiet_by_lang": {lg: sum(1 for q in quiet if q["lang"] == lg) for lg in ("en", "ja", "zh")},
        "unresolved_channels": unresolved,
        "enrollment_by_day": enroll,
    }


def marquee_rows(videos: list[pa.Video], hours: float) -> list[dict[str, Any]]:
    out = []
    for v in videos:
        if not v.marquee:
            continue
        end = v.endpoint(hours)
        row: dict[str, Any] = {"channel": v.channel, "video_id": v.video_id,
                               "n_obs": len(v.obs)}
        if end is not None:
            target = end.top(10)
            first = next((o.age_h for o in v.obs if o.age_h <= end.age_h
                          and (pa.jaccard(o.top(10), target) or 0) >= pa.LOCKIN_THRESHOLD), None)
            row.update(lockin_first_h=first, comment_count=v.comment_count_at.get(end.period))
        out.append(row)
    return out


def _bin(age: float) -> str:
    # pa._bin_label raises on ages >= 504 h (zip strict over unequal lists); the
    # registered outputs never reach that age, this exploratory one does.
    return pa._bin_label(age) if age < pa.AGE_BINS[-1] else f"{pa.AGE_BINS[-1]}h+"


def _genres() -> dict[str, str]:
    p = Path(__file__).parent / "config/panel-channels.json"
    return {c["handle"]: c.get("genre", "?") for c in json.loads(p.read_text())["channels"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--hours", type=float, nargs="+", default=[168, 336])
    ap.add_argument("--json", required=True)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db)
    videos = pa.load(conn)
    res: dict[str, Any] = {
        "reliability_extras": reliability_extras(videos),
        "gate_and_channels": gate_and_channels(conn, videos),
        "feedback": feedback_extras(videos),
        "exploratory_adjacent_overlap": adjacent_overlap(videos),
    }
    for h in a.hours:
        lk = pa.lockin(videos, h)
        res[f"endpoint_{int(h)}h"] = {
            "drops_by_lang": drops_by_lang(videos, h),
            "entry_age": entry_age_ci(videos, h),
            "lockin": lockin_extras(videos, h, lk),
            "marquee": marquee_rows(videos, h),
        }
    Path(a.json).write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
