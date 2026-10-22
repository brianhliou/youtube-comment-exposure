#!/bin/bash
# Noise-floor test (V2.md amendment 2026-10-22). Detached: ~2.5 h wall time, ~1,250 units.
# `.env` is sourced in this subshell and never echoed, as in run-daily.sh.
set -euo pipefail
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
S=data/runs/comments/panel-v2.snapshot-2026-10-21.sqlite
DB=${NOISE_DB:-data/runs/comments/noise-floor.sqlite}
O=studies/comments/results/v2/final
echo "start $(date)"
.venv/bin/python3 -m studies.comments.v2_noise_floor run --snapshot $S --db "$DB" --budget "${NOISE_BUDGET:-3000}"
echo "run exit $? $(date)"
.venv/bin/python3 -m studies.comments.v2_noise_floor summarize --snapshot $S --db "$DB" \
  --supplement $O/panel-supplement.json --day20 $O/panel-day20.json --json "${NOISE_JSON:-$O/noise-floor.json}" > /dev/null
echo "summarize exit $? $(date)"
echo "DONE $(date)"
