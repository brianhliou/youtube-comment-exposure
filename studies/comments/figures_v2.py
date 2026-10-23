"""Draw the v2 post's two in-post figures as standalone SVG.

Same approach as figures.py (v1): hand-written SVG on a white panel, so one file reads
in both site themes (the blog backs post images with #fff in dark mode). Unlike v1, the
numbers are read from the committed result files rather than typed in, so the figure
cannot drift from the run that produced it.

    adjacent-overlap.svg  mean top-10 Jaccard between consecutive looks (~12 h apart),
                          by video age; final/panel-supplement.json
                          `exploratory_adjacent_overlap`. Bins with fewer than 50 pairs
                          (0-6 h n=3, 6-12 h n=15) are left out: they are the few videos
                          enrolled within 12 h of upload and would draw a dip that is noise.
    lag-overlap.svg       mean top-10 Jaccard between two looks by the gap between them,
                          from day 4 on; final/lag-overlap.json `mean_top10_jaccard_by_lag_h`.

    python3 studies/comments/figures_v2.py --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

FINAL = Path(__file__).resolve().parent / "results/v2/final"

INK = "#1F2328"
MUTED = "#6B7280"
GRID = "#E5E7EB"
LINE = "#1a7f37"  # the site's tile green; passes the dataviz chroma and contrast checks
SURFACE = "#FFFFFF"

W, H = 680, 300
PAD_L, PAD_R, PAD_T, PAD_B = 46, 24, 34, 58
PLOT_W, PLOT_H = W - PAD_L - PAD_R, H - PAD_T - PAD_B
MIN_PAIRS = 50

STYLE = f"""<style>
  text {{ font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; }}
  .ttl {{ font-size: 12px; font-weight: 600; fill: {INK}; }}
  .ax  {{ font-size: 10px; fill: {MUTED}; }}
  .val {{ font-size: 10px; font-weight: 600; fill: {INK}; }}
  .grid {{ stroke: {GRID}; stroke-width: 1; }}
  .thr {{ stroke: {MUTED}; stroke-width: 1.5; stroke-dasharray: 5 4; }}
  .ln  {{ fill: none; stroke: {LINE}; stroke-width: 2; stroke-linejoin: round; }}
  .pt  {{ fill: {LINE}; stroke: {SURFACE}; stroke-width: 2; }}
  .cap {{ font-size: 10px; fill: {MUTED}; }}
</style>"""


def common_of_10(j: float) -> float:
    """Comments two top-10 lists share at a given Jaccard: k = 2nJ / (1 + J)."""
    return 20 * j / (1 + j)


def frame(title: str, aria: str, x_max: float, x_ticks: list[tuple[float, str]],
          y_ticks: list[float], caption: str) -> tuple[list[str], callable, callable]:
    def px(x: float) -> float:
        return PAD_L + PLOT_W * x / x_max

    def py(y: float) -> float:
        return PAD_T + PLOT_H * (1 - y)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{aria}">',
        STYLE,
        f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
        f'<text x="{PAD_L}" y="{PAD_T - 14}" class="ttl">{title}</text>',
    ]
    for v in y_ticks:
        out.append(f'<line x1="{PAD_L}" y1="{py(v):.1f}" x2="{W - PAD_R}" y2="{py(v):.1f}" class="grid"/>')
        out.append(f'<text x="{PAD_L - 10}" y="{py(v) + 4:.1f}" class="ax" text-anchor="end">{v:.1f}</text>')
    for x, lab in x_ticks:
        out.append(f'<text x="{px(x):.1f}" y="{PAD_T + PLOT_H + 18:.1f}" class="ax" '
                   f'text-anchor="middle">{lab}</text>')
    out.append(f'<text x="{PAD_L}" y="{H - 8}" class="cap">{caption}</text>')
    return out, px, py


def series(out: list[str], pts: list[tuple[float, float]]) -> None:
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    out.append(f'<path d="{path}" class="ln"/>')
    for x, y in pts:
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="pt"/>')


def bin_mid_days(label: str, last_age_h: float) -> float:
    lo, _, hi = label.rstrip("h+").partition("-")
    lo_h = float(lo)
    hi_h = float(hi.rstrip("h")) if hi else last_age_h
    return (lo_h + hi_h) / 2 / 24


def adjacent_overlap() -> str:
    sup = json.loads((FINAL / "panel-supplement.json").read_text())["exploratory_adjacent_overlap"]
    # The last bin is open-ended ("504h+"); the panel's last look is at ~509 h, so its
    # midpoint is placed at 506 h.
    rows = [(bin_mid_days(k, 508.0), v["mean_top10_jaccard"], v["n"]) for k, v in sup.items()
            if v["n"] >= MIN_PAIRS]
    out, px, py = frame(
        "Top-10 overlap between looks 12 hours apart, by video age",
        "Mean top-10 Jaccard between consecutive looks rises from 0.55 at 12 to 24 hours to "
        "about 0.80 by day 4 and stays near 0.80 through day 21",
        21.5, [(d, f"day {d}") for d in (1, 4, 7, 14, 21)], [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        f"Mean top-10 Jaccard, 250 videos, {sum(n for *_, n in rows):,} pairs of consecutive "
        "looks. Dashed: the 0.8 lock-in threshold (about 9 of 10 comments shared).",
    )
    out.insert(4, f'<line x1="{PAD_L}" y1="{py(0.8):.1f}" x2="{W - PAD_R}" y2="{py(0.8):.1f}" class="thr"/>')
    pts = [(px(d), py(j)) for d, j, _ in rows]
    series(out, pts)
    first = rows[0]
    out.append(f'<text x="{px(first[0]) + 8:.1f}" y="{py(first[1]) + 16:.1f}" class="val">'
               f"{first[1]:.2f}</text>")
    out.append(f'<text x="{px(rows[-1][0]):.1f}" y="{py(rows[-1][1]) - 12:.1f}" class="val" '
               f'text-anchor="middle">{rows[-1][1]:.2f}</text>')
    out.append(f'<text x="{W - PAD_R}" y="{py(0.8) + 16:.1f}" class="ax" text-anchor="end">'
               "lock-in 0.8</text>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def lag_overlap() -> str:
    lag = json.loads((FINAL / "lag-overlap.json").read_text())
    rows = sorted((int(h) / 24, v["mean"]) for h, v in lag["mean_top10_jaccard_by_lag_h"].items())
    names = {0.5: "12 h", 1: "1 d", 2: "2 d", 3: "3 d", 5: "5 d", 7: "7 d", 10: "10 d"}
    out, px, py = frame(
        "Top-10 overlap between two looks, by the gap between them",
        f"From day 4 on, mean top-10 Jaccard is {rows[0][1]:.2f} for looks 12 hours apart, "
        f"{rows[1][1]:.2f} a day apart, {rows[3][1]:.2f} three days apart and {rows[-1][1]:.2f} "
        f"ten days apart: {common_of_10(rows[0][1]):.1f}, {common_of_10(rows[1][1]):.1f}, "
        f"{common_of_10(rows[3][1]):.1f} and {common_of_10(rows[-1][1]):.1f} of 10 comments in common",
        10.5, [(d, names[d]) for d, _ in rows], [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        f"Mean top-10 Jaccard, {lag['videos']} videos, pairs of looks from day 4 on. "
        "Labels: comments the two top 10s share.",
    )
    series(out, [(px(d), py(j)) for d, j in rows])
    for d, j in rows:
        if d in (0.5, 1, 3, 10):
            dy = 22 if d == 1 else -12  # the 1 d label goes under its point, clear of 12 h
            out.append(f'<text x="{px(d):.1f}" y="{py(j) + dy:.1f}" class="val" '
                       f'text-anchor="middle">{common_of_10(j):.1f} of 10</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", required=True)
    a = p.parse_args(argv)
    d = Path(a.out_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    for name, svg in (("adjacent-overlap.svg", adjacent_overlap()), ("lag-overlap.svg", lag_overlap())):
        (d / name).write_text(svg)
        print(f"wrote {d / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
