#!/usr/bin/env bash
# Build paper/comment-exposure.pdf from the markdown source, via the house LaTeX
# preamble so this paper matches calibration-audit.tex and semantic-subset.tex.
#
# The engine is tectonic, not MacTeX. Tectonic is self-contained and fetches
# packages on demand, which is why `pdflatex` is absent from PATH on this machine
# even though the other papers are real LaTeX.
#
# Pipeline: markdown -> pandoc (latex body, natbib citations) -> house preamble
# -> tectonic. The figure is rasterless: rsvg-convert turns the generated SVG into
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
rsvg-convert -f pdf -o figures/arrival.pdf figures/arrival.svg

# Strip the H1 (the title comes from \title) and let pandoc emit natbib citations.
tail -n +2 "$SRC" > .body.md
# The markdown points at the SVG so the HTML render can use it. Pandoc picks
# \includesvg off that extension, which LaTeX has no engine for, so swap the
# command and the path together for the rasterless PDF.
pandoc .body.md -t latex --top-level-division=section --natbib --wrap=preserve \
  | python3 -c "
import re, sys
s = sys.stdin.read()
s = re.sub(r'\\\\includesvg\[[^]]*\]\{figures/arrival\.svg\}',
           r'\\\\includegraphics[width=\\\\linewidth]{figures/arrival.pdf}', s)
sys.stdout.write(s)
" > .body.tex
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

% Latin Modern has no glyph for these, and XeTeX drops them silently rather than
% failing, so "Shorts (<=60s)" would print as "Shorts (60s)". Map them to math.
\usepackage{newunicodechar}
\newunicodechar{≤}{\ensuremath{\leq}}
\newunicodechar{≥}{\ensuremath{\geq}}
\newunicodechar{∈}{\ensuremath{\in}}
\newunicodechar{×}{\ensuremath{\times}}
\newunicodechar{−}{\ensuremath{-}}

\title{YouTube Comment Exposure:\\
1{,}345{,}353 comments with post timestamps}
\author{Brian Liou}
\date{August 2026}

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
