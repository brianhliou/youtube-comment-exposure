"""Generate the paper's figures as standalone SVG.

Hand-written SVG rather than a plotting library: the figure is two small line panels,
and adding matplotlib to the analysis extra to draw ten points would cost more than it
returns. Numbers are passed in explicitly so the figure can never drift from the text
without someone editing both.

Both panels plot the same measure (mean log1p(likes)) on a shared y-scale, which is the
whole point — two different groupings of the same 1.35M comments produce the same decline.
Plotting them on separate scales, or one in log space and one in raw likes, would make
agreement look like contrast.

    python -m studies.comments.figures --out paper/figures/arrival.svg
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

INK = "#1F2328"
MUTED = "#6B7280"
GRID = "#E5E7EB"
LINE = "#1C6C68"
SURFACE = "#FFFFFF"

DECILES = [0.721, 0.405, 0.302, 0.254, 0.224, 0.198, 0.185, 0.172, 0.163, 0.133]
AGE_LABELS = ["<1h", "1-6h", "6-24h", "1-7d", "1-30d", ">30d"]
AGE_VALUES = [0.764, 0.387, 0.230, 0.181, 0.163, 0.129]

Y_MAX = 0.8
PANEL_W, PANEL_H = 300, 200
PAD_L, PAD_T, PAD_B, GAP = 46, 34, 40, 54


def panel(x0: int, title: str, values: list[float], labels: list[str], show_y: bool) -> str:
    """One panel: recessive grid, a 2px line, 8px markers, endpoints labelled."""
    out = [f'<text x="{x0}" y="{PAD_T - 14}" class="ttl">{title}</text>']

    for i in range(5):
        v = Y_MAX * i / 4
        y = PAD_T + PANEL_H - (v / Y_MAX) * PANEL_H
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + PANEL_W}" y2="{y:.1f}" class="grid"/>')
        if show_y:
            out.append(
                f'<text x="{x0 - 10}" y="{y + 4:.1f}" class="ax" '
                f'text-anchor="end">{v:.1f}</text>'
            )

    n = len(values)
    step = PANEL_W / (n - 1)
    pts = [(x0 + i * step, PAD_T + PANEL_H - (v / Y_MAX) * PANEL_H) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    out.append(f'<path d="{path}" class="ln"/>')

    for i, (x, y) in enumerate(pts):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="pt"/>')
        # Selective direct labels: endpoints only. A number on every point is noise.
        if i in (0, n - 1):
            dy = -12 if i == 0 else -12
            out.append(
                f'<text x="{x:.1f}" y="{y + dy:.1f}" class="val" text-anchor="middle">'
                f"{values[i]:.3f}</text>"
            )
        if labels:
            # Bucket labels carry "<1h" and ">30d"; unescaped they break the SVG.
            out.append(
                f'<text x="{x:.1f}" y="{PAD_T + PANEL_H + 18:.1f}" class="ax" '
                f'text-anchor="middle">{escape(labels[i])}</text>'
            )
    return "\n".join(out)


def build() -> str:
    w = PAD_L + PANEL_W + GAP + PANEL_W + 16
    h = PAD_T + PANEL_H + PAD_B + 14
    left = panel(PAD_L, "By position within its video (decile)", DECILES,
                 [str(i) for i in range(1, 11)], True)
    right = panel(PAD_L + PANEL_W + GAP, "By absolute age at posting", AGE_VALUES,
                  AGE_LABELS, False)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" \
viewBox="0 0 {w} {h}" role="img" aria-label="Mean log1p likes declines with arrival time \
under both groupings">
<style>
  text {{ font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; }}
  .ttl {{ font-size: 12px; font-weight: 600; fill: {INK}; }}
  .ax  {{ font-size: 10px; fill: {MUTED}; }}
  .val {{ font-size: 10px; font-weight: 600; fill: {LINE}; }}
  .grid {{ stroke: {GRID}; stroke-width: 1; }}
  .ln  {{ fill: none; stroke: {LINE}; stroke-width: 2; stroke-linejoin: round; }}
  .pt  {{ fill: {LINE}; stroke: {SURFACE}; stroke-width: 2; }}
  .cap {{ font-size: 10px; fill: {MUTED}; }}
</style>
<rect width="{w}" height="{h}" fill="{SURFACE}"/>
{left}
{right}
<text x="{PAD_L}" y="{h - 6}" class="cap">Mean log1p(likes), 1,345,353 comments. \
Shared y-axis. Both groupings decline by roughly 5.5x.</text>
</svg>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="paper/figures/arrival.svg")
    a = p.parse_args(argv)
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build())
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
