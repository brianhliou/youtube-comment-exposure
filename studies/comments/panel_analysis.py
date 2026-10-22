"""Pre-registered analysis for the comment panel (V2.md, Study A).

Written on day 0, before any second observation existed, so that the endpoints below
are computed the way they were registered rather than the way the data turns out to
lean. Everything is in **hours since upload at fetch time**, never period index.

Endpoints (V2.md "Endpoints"):

0. Reliability floor — overlap between a page and its immediate repeat fetch.
1. Lock-in hour — first observation whose top-10 has Jaccard ≥ 0.8 with the endpoint
   top-10 (plus a stricter "sustained" variant that must stay ≥ 0.8 to the endpoint).
2. Persistence curve — mean top-20 Jaccard vs the endpoint, by age bin.
3. Entry age — how soon after upload the endpoint top-20 were posted.
4. Late entrants — comments first seen in the top-20 at ≥ 72 h.
5. Feedback loop — within-comment like gain per hour at rank ≤ 10 vs > 50.

Inclusion: ≥ 100 top-level comments at the endpoint (commentCount × 0.7). Creator
comments (author == channel) are dropped from all sets. Marquee channels are reported
in the per-channel table and excluded from pooled estimates. Standard errors on the
pooled numbers come from a cluster bootstrap over channels.

Usage::

    python -m studies.comments.panel_analysis --db data/runs/comments/panel-v2.sqlite \\
        [--endpoint-hours 168] [--json results/v2/panel.json]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ENDPOINT_HOURS = 168  # day 7, amended from day 14 on day 0 (V2.md Amendments)
LOCKIN_THRESHOLD = 0.8
MIN_TOP_LEVEL = 100
THREAD_SHARE = 0.7  # v1: top-level threads ≈ 70% of videos.list commentCount
LATE_HOURS = 72
SHORT_SECONDS = 60
AGE_BINS = [0, 6, 12, 24, 48, 72, 96, 120, 144, 168, 240, 336, 504]
BOOTSTRAP = 500


# --------------------------------------------------------------------------- helpers


def _hours(later: str, earlier: str) -> float:
    a = datetime.fromisoformat(later.replace("Z", "+00:00"))
    b = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
    return (a - b).total_seconds() / 3600


def parse_duration(iso: str | None) -> int | None:
    """ISO-8601 duration (PT1H2M3S) → seconds."""
    if not iso:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def jaccard(a: set, b: set) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Spearman rho over (x, y) pairs; None below 3 pairs."""
    if len(pairs) < 3:
        return None

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            r = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = r
            i = j + 1
        return out

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def quantiles(vals: list[float]) -> dict[str, float | None]:
    if not vals:
        return {"n": 0, "median": None, "q1": None, "q3": None}
    s = sorted(vals)
    if len(s) < 4:
        return {"n": len(s), "median": statistics.median(s), "q1": s[0], "q3": s[-1]}
    q = statistics.quantiles(s, n=4)
    return {"n": len(s), "median": statistics.median(s), "q1": q[0], "q3": q[2]}


def _bin_label(age: float) -> str:
    for lo, hi in zip(AGE_BINS, AGE_BINS[1:], strict=True):
        if lo <= age < hi:
            return f"{lo}-{hi}h"
    return f"{AGE_BINS[-1]}h+"


# --------------------------------------------------------------------------- load


@dataclass
class Obs:
    period: str
    age_h: float  # hours since upload at fetch
    fetched_at: str
    ranked: list[tuple[int, str, int, str]]  # (rank, comment_id, likes, comment_published_at)

    def top(self, k: int) -> set[str]:
        return {cid for rank, cid, _, _ in self.ranked if rank <= k}


@dataclass
class Video:
    video_id: str
    channel: str
    channel_id: str | None
    lang: str
    marquee: bool
    published_at: str
    duration_s: int | None
    obs: list[Obs] = field(default_factory=list)  # main series, sorted by age
    repeats: dict[str, Obs] = field(default_factory=dict)  # period → repeat fetch
    comment_count_at: dict[str, int] = field(default_factory=dict)  # period → reported count

    @property
    def is_short(self) -> bool | None:
        return None if self.duration_s is None else self.duration_s <= SHORT_SECONDS

    def endpoint(self, hours: float) -> Obs | None:
        """First main observation at or past ``hours`` since upload."""
        for o in self.obs:
            if o.age_h >= hours:
                return o
        return None


def load(conn: sqlite3.Connection) -> list[Video]:
    conn.row_factory = sqlite3.Row
    marquee_col = "marquee" in {r[1] for r in conn.execute("PRAGMA table_info(channels)")}
    vids: dict[str, Video] = {}
    for r in conn.execute(
        f"""
        SELECT v.video_id, v.channel_handle, v.channel_id, v.lang, v.published_at, v.duration,
               {"c.marquee" if marquee_col else "0"} AS marquee
        FROM panel_videos v JOIN channels c ON c.handle = v.channel_handle
        """
    ):
        vids[r["video_id"]] = Video(
            video_id=r["video_id"],
            channel=r["channel_handle"],
            channel_id=r["channel_id"],
            lang=r["lang"],
            marquee=bool(r["marquee"]),
            published_at=r["published_at"],
            duration_s=parse_duration(r["duration"]),
        )
    for r in conn.execute("SELECT video_id, period, comment_count FROM video_observations"):
        if r["video_id"] in vids and r["comment_count"] is not None:
            vids[r["video_id"]].comment_count_at[r["period"]] = r["comment_count"]

    # Rank rows, grouped per (video, period); creator comments dropped here.
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT video_id, period, rank, comment_id, author_channel_id, like_count,
               published_at, fetched_at
        FROM rank_observations ORDER BY video_id, period, rank
        """
    ):
        grouped[(r["video_id"], r["period"])].append(r)
    for (vid, period), rows in grouped.items():
        v = vids.get(vid)
        if v is None:
            continue
        rows = [r for r in rows if not (v.channel_id and r["author_channel_id"] == v.channel_id)]
        if not rows:
            continue
        fetched = rows[0]["fetched_at"]
        # Re-rank after dropping creator comments so top-k is k real comments.
        ranked = [
            (i, r["comment_id"], r["like_count"], r["published_at"])
            for i, r in enumerate(rows, start=1)
        ]
        obs = Obs(
            period=period, age_h=_hours(fetched, v.published_at), fetched_at=fetched, ranked=ranked
        )
        if period.endswith("r"):
            v.repeats[period[:-1]] = obs
        else:
            v.obs.append(obs)
    for v in vids.values():
        v.obs.sort(key=lambda o: o.age_h)
    return list(vids.values())


# --------------------------------------------------------------------------- endpoints


def reliability_floor(videos: list[Video]) -> dict[str, Any]:
    j10: list[float] = []
    j20: list[float] = []
    rho: list[float] = []
    for v in videos:
        main = {o.period: o for o in v.obs}
        for period, rep in v.repeats.items():
            base = main.get(period)
            if base is None:
                continue
            a, b = jaccard(base.top(10), rep.top(10)), jaccard(base.top(20), rep.top(20))
            if a is not None:
                j10.append(a)
            if b is not None:
                j20.append(b)
            pos_a = {cid: rank for rank, cid, _, _ in base.ranked}
            pos_b = {cid: rank for rank, cid, _, _ in rep.ranked}
            shared = [(pos_a[c], pos_b[c]) for c in pos_a.keys() & pos_b.keys()]
            r = spearman(shared)
            if r is not None:
                rho.append(r)
    return {
        "pairs": len(j10),
        "top10_jaccard": quantiles(j10),
        "top20_jaccard": quantiles(j20),
        "spearman_shared": quantiles(rho),
        "threshold_clear": bool(j10) and statistics.median(j10) >= 0.9,
    }


def _eligible(v: Video, hours: float) -> Obs | None:
    """Endpoint observation if the video qualifies for lock-in analyses, else None."""
    if v.marquee:
        return None
    end = v.endpoint(hours)
    if end is None:
        return None
    count = v.comment_count_at.get(end.period)
    if count is None or count * THREAD_SHARE < MIN_TOP_LEVEL:
        return None
    return end


def lockin(videos: list[Video], hours: float) -> dict[str, Any]:
    per_video: list[dict[str, Any]] = []
    excluded = {"marquee": 0, "no_endpoint_yet": 0, "under_min_comments": 0}
    for v in videos:
        if v.marquee:
            excluded["marquee"] += 1
            continue
        end = v.endpoint(hours)
        if end is None:
            excluded["no_endpoint_yet"] += 1
            continue
        count = v.comment_count_at.get(end.period)
        if count is None or count * THREAD_SHARE < MIN_TOP_LEVEL:
            excluded["under_min_comments"] += 1
            continue
        target = end.top(10)
        series = [o for o in v.obs if o.age_h <= end.age_h]
        first: float | None = None
        sustained: float | None = None
        for i, o in enumerate(series):
            ov = jaccard(o.top(10), target)
            if ov is None:
                continue
            if first is None and ov >= LOCKIN_THRESHOLD:
                first = o.age_h
            if sustained is None and all(
                (jaccard(p.top(10), target) or 0) >= LOCKIN_THRESHOLD for p in series[i:]
            ):
                sustained = o.age_h
        per_video.append(
            {
                "video_id": v.video_id,
                "channel": v.channel,
                "lang": v.lang,
                "short": v.is_short,
                "endpoint_age_h": end.age_h,
                "first_obs_age_h": series[0].age_h if series else None,
                "lockin_first_h": first,
                "lockin_sustained_h": sustained,
                "n_obs": len(series),
            }
        )

    def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
        firsts = [r["lockin_first_h"] for r in rows if r["lockin_first_h"] is not None]
        sus = [r["lockin_sustained_h"] for r in rows if r["lockin_sustained_h"] is not None]
        return {
            "videos": len(rows),
            "first_crossing_h": quantiles(firsts),
            "sustained_h": quantiles(sus),
            "already_locked_at_first_obs": sum(
                1
                for r in rows
                if r["lockin_first_h"] is not None and r["lockin_first_h"] == r["first_obs_age_h"]
            ),
            "median_ci95_cluster": cluster_bootstrap_median(
                [
                    (r["channel"], r["lockin_first_h"])
                    for r in rows
                    if r["lockin_first_h"] is not None
                ]
            ),
        }

    by_lang = {
        lang: summarise([r for r in per_video if r["lang"] == lang])
        for lang in sorted({r["lang"] for r in per_video})
    }
    by_format = {
        "short": summarise([r for r in per_video if r["short"] is True]),
        "long": summarise([r for r in per_video if r["short"] is False]),
    }
    by_channel = defaultdict(list)
    for r in per_video:
        by_channel[r["channel"]].append(r)
    return {
        "endpoint_hours": hours,
        "excluded": excluded,
        "pooled": summarise(per_video),
        "by_lang": by_lang,
        "by_format": by_format,
        "per_channel": {
            ch: {
                "n": len(rows),
                "lang": rows[0]["lang"],
                "median_first_h": statistics.median(
                    [r["lockin_first_h"] for r in rows if r["lockin_first_h"] is not None]
                )
                if any(r["lockin_first_h"] is not None for r in rows)
                else None,
            }
            for ch, rows in sorted(by_channel.items())
        },
        "per_video": per_video,
    }


def cluster_bootstrap_median(pairs: list[tuple[str, float]], seed: int = 0) -> list[float] | None:
    """95% CI for the median, resampling channels (clusters) with replacement."""
    if len(pairs) < 5:
        return None
    by = defaultdict(list)
    for ch, val in pairs:
        by[ch].append(val)
    clusters = list(by.values())
    rng = random.Random(seed)
    meds = []
    for _ in range(BOOTSTRAP):
        sample = [v for c in rng.choices(clusters, k=len(clusters)) for v in c]
        meds.append(statistics.median(sample))
    meds.sort()
    return [meds[int(0.025 * len(meds))], meds[int(0.975 * len(meds)) - 1]]


def persistence_curve(videos: list[Video], hours: float) -> dict[str, Any]:
    bins: dict[str, list[float]] = defaultdict(list)
    rhos: dict[str, list[float]] = defaultdict(list)
    for v in videos:
        end = _eligible(v, hours)
        if end is None:
            continue
        target20 = end.top(20)
        pos_end = {cid: rank for rank, cid, _, _ in end.ranked}
        for o in v.obs:
            if o.age_h > end.age_h:
                break
            ov = jaccard(o.top(20), target20)
            if ov is None:
                continue
            label = _bin_label(o.age_h)
            bins[label].append(ov)
            pos_o = {cid: rank for rank, cid, _, _ in o.ranked}
            r = spearman([(pos_o[c], pos_end[c]) for c in pos_o.keys() & pos_end.keys()])
            if r is not None:
                rhos[label].append(r)
    order = sorted(bins, key=lambda b: int(b.split("-")[0].rstrip("h+")))
    return {
        b: {
            "n": len(bins[b]),
            "mean_top20_jaccard": statistics.fmean(bins[b]),
            "mean_spearman_shared": statistics.fmean(rhos[b]) if rhos[b] else None,
        }
        for b in order
    }


def entry_age(videos: list[Video], hours: float) -> dict[str, Any]:
    ages: list[float] = []
    by_lang: dict[str, list[float]] = defaultdict(list)
    for v in videos:
        end = _eligible(v, hours)
        if end is None:
            continue
        for rank, _cid, _likes, posted in end.ranked:
            if rank > 20 or not posted:
                continue
            a = _hours(posted, v.published_at)
            ages.append(a)
            by_lang[v.lang].append(a)

    def shares(vals: list[float]) -> dict[str, Any]:
        n = len(vals)
        if not n:
            return {"n": 0}
        return {
            "n": n,
            "within_1h": sum(a <= 1 for a in vals) / n,
            "within_6h": sum(a <= 6 for a in vals) / n,
            "within_24h": sum(a <= 24 for a in vals) / n,
            "median_h": statistics.median(vals),
        }

    return {"pooled": shares(ages), "by_lang": {k: shares(v) for k, v in sorted(by_lang.items())}}


def late_entrants(videos: list[Video], hours: float) -> dict[str, Any]:
    counts: list[int] = []
    post_ages: list[float] = []
    for v in videos:
        end = _eligible(v, hours)
        if end is None:
            continue
        first_seen: dict[str, tuple[float, str]] = {}
        for o in v.obs:
            if o.age_h > end.age_h:
                break
            for rank, cid, _likes, posted in o.ranked:
                if rank <= 20 and cid not in first_seen:
                    first_seen[cid] = (o.age_h, posted)
        late = [(age, posted) for age, posted in first_seen.values() if age >= LATE_HOURS]
        counts.append(len(late))
        post_ages.extend(_hours(p, v.published_at) for _, p in late if p)
    return {
        "videos": len(counts),
        "late_entrants_per_video": quantiles([float(c) for c in counts]),
        "share_videos_with_any": (sum(1 for c in counts if c) / len(counts)) if counts else None,
        "late_entrant_post_age_h": quantiles(post_ages),
        "late_posts_share": (sum(1 for a in post_ages if a >= LATE_HOURS) / len(post_ages))
        if post_ages
        else None,
    }


def feedback_loop(videos: list[Video]) -> dict[str, Any]:
    """Like gain per hour between consecutive observations, by rank at the earlier one.

    Within-comment: only comments that have at least one interval starting at rank ≤ 10
    and one starting at rank > 50. Pooled (all intervals) is reported as secondary.
    """
    per_comment_hi: dict[str, list[float]] = defaultdict(list)
    per_comment_lo: dict[str, list[float]] = defaultdict(list)
    pooled_hi: list[float] = []
    pooled_lo: list[float] = []
    for v in videos:
        if v.marquee:
            continue
        for prev, cur in zip(v.obs, v.obs[1:], strict=False):
            dt = cur.age_h - prev.age_h
            if dt <= 0:
                continue
            likes_now = {cid: likes for _, cid, likes, _ in cur.ranked}
            for rank, cid, likes, _ in prev.ranked:
                if cid not in likes_now:
                    continue  # fell off the page; gain unobserved
                rate = (likes_now[cid] - likes) / dt
                key = f"{v.video_id}:{cid}"
                if rank <= 10:
                    per_comment_hi[key].append(rate)
                    pooled_hi.append(rate)
                elif rank > 50:
                    per_comment_lo[key].append(rate)
                    pooled_lo.append(rate)
    movers = per_comment_hi.keys() & per_comment_lo.keys()
    diffs = [
        statistics.fmean(per_comment_hi[k]) - statistics.fmean(per_comment_lo[k]) for k in movers
    ]
    hi_means = [statistics.fmean(per_comment_hi[k]) for k in movers]
    lo_means = [statistics.fmean(per_comment_lo[k]) for k in movers]
    ratio = None
    if lo_means and statistics.fmean(lo_means) > 0:
        ratio = statistics.fmean(hi_means) / statistics.fmean(lo_means)
    return {
        "within_comment": {
            "movers": len(movers),
            "mean_gain_per_h_at_top10": statistics.fmean(hi_means) if hi_means else None,
            "mean_gain_per_h_at_51plus": statistics.fmean(lo_means) if lo_means else None,
            "ratio": ratio,
            "median_paired_diff": statistics.median(diffs) if diffs else None,
            "share_positive_diff": (sum(d > 0 for d in diffs) / len(diffs)) if diffs else None,
        },
        "pooled_secondary": {
            "intervals_top10": len(pooled_hi),
            "intervals_51plus": len(pooled_lo),
            "mean_gain_per_h_at_top10": statistics.fmean(pooled_hi) if pooled_hi else None,
            "mean_gain_per_h_at_51plus": statistics.fmean(pooled_lo) if pooled_lo else None,
        },
    }


# --------------------------------------------------------------------------- hypotheses


def hypotheses(res: dict[str, Any]) -> dict[str, Any]:
    lock = res["lockin"]
    floor = res["reliability"]
    med = lock["pooled"]["first_crossing_h"]["median"]
    h1 = None if med is None else (med <= 72 and floor["threshold_clear"])
    ea = res["entry_age"]["pooled"]
    h2 = None if not ea.get("n") else (ea["within_24h"] >= 0.70 and ea["within_1h"] >= 0.30)
    fb = res["feedback"]["within_comment"]
    h3 = None if fb["ratio"] is None else fb["ratio"] >= 3
    h4: dict[str, bool | None] = {}
    for lang, s in lock["by_lang"].items():
        m = s["first_crossing_h"]["median"]
        e = res["entry_age"]["by_lang"].get(lang, {})
        if m is None or med is None or not e.get("n"):
            h4[lang] = None
        else:
            h4[lang] = abs(m - med) <= 24 and abs(e["within_24h"] - ea["within_24h"]) <= 0.10
    sh = lock["by_format"]["short"]["first_crossing_h"]["median"]
    lg = lock["by_format"]["long"]["first_crossing_h"]["median"]
    h5 = None if sh is None or lg is None else (lg - sh) >= 24
    gate_ok = lock["pooled"]["videos"] >= 200
    return {
        "H1_lockin_le_72h_and_floor_clear": h1,
        "H2_entry_age_70pct_24h_30pct_1h": h2,
        "H3_within_comment_ratio_ge_3": h3,
        "H4_by_lang_within_tolerance": h4,
        "H5_shorts_faster_by_24h": h5,
        "gate_200_videos_with_endpoint": gate_ok,
    }


# --------------------------------------------------------------------------- report


def analyse(conn: sqlite3.Connection, hours: float = ENDPOINT_HOURS) -> dict[str, Any]:
    videos = load(conn)
    res: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "endpoint_hours": hours,
        "videos_loaded": len(videos),
        "videos_with_any_obs": sum(1 for v in videos if v.obs),
        "reliability": reliability_floor(videos),
        "lockin": lockin(videos, hours),
        "persistence": persistence_curve(videos, hours),
        "entry_age": entry_age(videos, hours),
        "late_entrants": late_entrants(videos, hours),
        "feedback": feedback_loop(videos),
    }
    res["hypotheses"] = hypotheses(res)
    return res


def _fmt(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def report(res: dict[str, Any]) -> str:
    out: list[str] = []
    a = out.append
    a(
        f"panel analysis · endpoint {res['endpoint_hours']:.0f} h · "
        f"{res['videos_loaded']} videos loaded, {res['videos_with_any_obs']} observed"
    )
    r = res["reliability"]
    j10, j20 = r["top10_jaccard"], r["top20_jaccard"]
    a(
        f"reliability floor: {r['pairs']} pairs · top-10 Jaccard median {_fmt(j10['median'], 2)} "
        f"(IQR {_fmt(j10['q1'], 2)}–{_fmt(j10['q3'], 2)}) · top-20 {_fmt(j20['median'], 2)} · "
        f"threshold clear: {r['threshold_clear']}"
    )
    lk = res["lockin"]
    p = lk["pooled"]
    fc = p["first_crossing_h"]
    a(
        f"lock-in (first crossing ≥0.8 with endpoint top-10): {p['videos']} videos · "
        f"median {_fmt(fc['median'])} h (IQR {_fmt(fc['q1'])}–{_fmt(fc['q3'])}) · "
        f"cluster CI95 {p['median_ci95_cluster']} · "
        f"sustained median {_fmt(p['sustained_h']['median'])} h · "
        f"already locked at first obs: {p['already_locked_at_first_obs']}"
    )
    a(f"  excluded: {lk['excluded']}")
    for lang, s_ in lk["by_lang"].items():
        a(
            f"  {lang}: n={s_['videos']} median {_fmt(s_['first_crossing_h']['median'])} h "
            f"CI {s_['median_ci95_cluster']}"
        )
    for fmt_, s_ in lk["by_format"].items():
        a(f"  {fmt_}: n={s_['videos']} median {_fmt(s_['first_crossing_h']['median'])} h")
    a("persistence (mean top-20 Jaccard vs endpoint, by age):")
    for b, s_ in res["persistence"].items():
        a(
            f"  {b:>10} n={s_['n']:<4} J20 {s_['mean_top20_jaccard']:.2f}  "
            f"rho {_fmt(s_['mean_spearman_shared'], 2)}"
        )
    e = res["entry_age"]["pooled"]
    if e.get("n"):
        a(
            f"entry age of endpoint top-20: n={e['n']} · within 1h {e['within_1h']:.0%} · "
            f"6h {e['within_6h']:.0%} · 24h {e['within_24h']:.0%} · median {e['median_h']:.1f} h"
        )
    le = res["late_entrants"]
    a(
        f"late entrants (first in top-20 at ≥{LATE_HOURS}h): per video median "
        f"{_fmt(le['late_entrants_per_video']['median'])} · "
        f"videos with any {_fmt(le['share_videos_with_any'], 2)} · "
        f"late *posts* share {_fmt(le['late_posts_share'], 2)}"
    )
    fb = res["feedback"]["within_comment"]
    a(
        f"feedback loop (within-comment): movers {fb['movers']} · "
        f"gain/h at top-10 {_fmt(fb['mean_gain_per_h_at_top10'], 2)} "
        f"vs 51+ {_fmt(fb['mean_gain_per_h_at_51plus'], 2)} · ratio {_fmt(fb['ratio'], 2)} · "
        f"share positive {_fmt(fb['share_positive_diff'], 2)}"
    )
    a("hypotheses: " + json.dumps(res["hypotheses"]))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", required=True)
    ap.add_argument("--endpoint-hours", type=float, default=ENDPOINT_HOURS)
    ap.add_argument("--json", default=None, help="write full results (incl. per-video rows) here")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    res = analyse(conn, args.endpoint_hours)
    print(report(res))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
