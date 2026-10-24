#!/usr/bin/env bash
# Build paper/comment-exposure.pdf from the markdown source, via the house LaTeX
# preamble so this paper matches calibration-audit.tex and semantic-subset.tex.
#
# The engine is tectonic, not MacTeX. Tectonic is self-contained and fetches
# packages on demand, which is why `pdflatex` is absent from PATH on this machine
# even though the other papers are real LaTeX.
#
# Pipeline: markdown -> pandoc (latex body, natbib citations) -> house preamble
# -> tectonic. The figures are rasterless: rsvg-convert turns each SVG into
# a PDF that \includegraphics can take.
set -euo pipefail
cd "$(dirname "$0")"

SRC="comment-exposure.md"
TEX="comment-exposure.tex"
OUT="comment-exposure.pdf"

for t in pandoc tectonic rsvg-convert; do
  command -v "$t" >/dev/null || { echo "$t not found"; exit 1; }
done

(cd .. && .venv/bin/python -m studies.comments.figures --out paper/figures/arrival.svg)
# arrival.svg is generated above; lag-overlap.svg and adjacent-overlap.svg are
# copied from the v2 blog post's assets (studies/comments/figures_v2.py made them).
for fig in arrival lag-overlap adjacent-overlap; do
  rsvg-convert -f pdf -o "figures/$fig.pdf" "figures/$fig.svg"
done

# Strip the H1 (the title comes from \title) and let pandoc emit natbib citations.
# Sections are written as ##, so shift them up a level: otherwise every heading
# is a subsection of no section and numbers as 0.1, 0.2, ...
# The markdown's closing "## References" heading is for the HTML render; natbib
# prints its own, so drop it here or the PDF shows the heading twice.
tail -n +2 "$SRC" | sed '/^## References[[:space:]]*$/d' > .body.md
# postprocess_tex.py swaps the SVG figures for PDFs and spaces wrapped table rows.
pandoc .body.md -t latex --top-level-division=section --shift-heading-level-by=-1 --natbib --wrap=preserve \
  | python3 postprocess_tex.py > .body.tex
rm -f .body.md
trap 'rm -f .body.tex' EXIT

cat > "$TEX" <<'PREAMBLE'
\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{calc}
\usepackage{graphicx}
\usepackage[numbers,sort&compress]{natbib}
\usepackage[hidelinks]{hyperref}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\providecommand{\pandocbounded}[1]{#1}

% Let a line with an unbreakable name (texttt field lists, long citations)
% stretch its spaces instead of running past the margin.
\setlength{\emergencystretch}{3em}

% Break the page here if less than #1 is left, and otherwise do nothing.
% needspace's \needspace also adds \penalty-100 every time, a standing
% invitation to break: before "Changes from version 1" TeX took it over a
% break inside the table that follows, and longtable, whose output routine
% was by then in charge, printed its header alone above the heading on page 2.
\makeatletter
\newcommand{\reservespace}[1]{\par\begingroup
  \setlength{\dimen@}{#1}\dimen@ii\pagegoal \advance\dimen@ii-\pagetotal
  \ifdim\dimen@>\dimen@ii \ifdim\dimen@ii>\z@ \vfil\fi \penalty-\@M \fi
  \endgroup}
\makeatother

% Latin Modern has no glyph for these, and XeTeX drops them silently rather than
% failing, so "Shorts (<=60s)" would print as "Shorts (60s)". Map them to math.
\usepackage{newunicodechar}
\newunicodechar{≤}{\ensuremath{\leq}}
\newunicodechar{≥}{\ensuremath{\geq}}
\newunicodechar{∈}{\ensuremath{\in}}
\newunicodechar{×}{\ensuremath{\times}}
\newunicodechar{−}{\ensuremath{-}}

\title{YouTube Comment Exposure:\\
timestamped comments and a 21-day panel of the ranking}
\author{Brian H. Liou}
\date{October 2026 (version 2.1; version 1 August 2026)}

\begin{document}
\maketitle
PREAMBLE

cat .body.tex >> "$TEX"

cat >> "$TEX" <<'POSTAMBLE'

\bibliographystyle{plainnat}
\bibliography{comment-exposure}

\end{document}
POSTAMBLE

# Two passes so natbib resolves \cite and the bibliography renders.
tectonic -X compile "$TEX" --outdir . --keep-intermediates

echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
