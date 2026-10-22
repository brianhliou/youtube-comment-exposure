#!/bin/bash
# Study A final analysis on the 2026-10-21 snapshot (panel finished: last period 2026-10-17b).
cd "$(git rev-parse --show-toplevel)" || exit 1
S=data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite
O=studies/comments/results/v2/final
echo "start $(date)"
for h in 168 336 480; do
  d=$((h/24))
  .venv/bin/python3 -m studies.comments.panel_analysis --db $S --endpoint-hours $h --json $O/panel-day$d.json > $O/panel-day$d.txt 2>&1
  echo "day$d exit $? $(date)"
done
.venv/bin/python3 -m studies.comments.v2_panel_supplement --db $S --json $O/panel-supplement.json > $O/panel-supplement.txt 2>&1
echo "supplement exit $? $(date)"
echo "DONE $(date)"
