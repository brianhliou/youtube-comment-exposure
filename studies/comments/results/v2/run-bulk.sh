#!/bin/bash
# Study B chain (V2.md), started 2026-10-09. Each step is independent (';'), so a
# failure (e.g. memory on the pooled run) does not stop the per-language steps.
cd "$(git rev-parse --show-toplevel)" || exit 1
P="nice -n 10 .venv/bin/python3 -m studies.comments.v2_bulk"
echo "start $(date)"
$P tfidf --lang zh --seeds 1 2 3 4 --char
$P tfidf --lang ja --char
$P tfidf --lang en
$P embed --lang zh ja en
$P ladder-embed --lang zh ja en
$P tfidf --lang pooled
$P summary
echo "DONE $(date)"
