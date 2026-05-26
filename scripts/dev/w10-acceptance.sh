#!/usr/bin/env bash
# scripts/dev/w10-acceptance.sh
#
# W10 merge gate. Run after every W10-* PR lands; the gate must be green
# before merging the W10 branch into main.
#
# Assumes:
#   - `uv sync` completed
#   - `cd web && npm ci` completed
#   - saa_test database is wiped + alembic at head (Task 1 wrapper ran)
#   - SAA_AUTH_DEV_BYPASS=1 is acceptable for this environment
#
# Does NOT install deps. Does NOT mutate the DB beyond what pytest does.
set -euo pipefail

cd "$(dirname "$0")/../.."

REPO="$(pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="${REPO}/.w10-acceptance.${TS}.log"

echo "W10 acceptance gate — $(date -Iseconds)"
echo "   repo: $REPO"
echo "   log : $LOG"
echo

step() {
  printf "==> %s\n" "$*"
  printf "==> %s\n" "$*" >> "$LOG"
}

run() {
  step "$1"
  shift
  ( "$@" ) 2>&1 | tee -a "$LOG"
}

# 1) Alembic at head — proves migrations apply cleanly.
run "alembic upgrade head" \
  env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
      uv run alembic upgrade head

# 2) Backend tests — full suite, both PG and non-PG marks.
run "pytest tests/ (full suite)" \
  env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
      SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
      uv run pytest -q tests/

# 3) Frontend vitest — full sweep.
run "vitest (full sweep)" \
  bash -c "cd web && npm test -- --run"

# 4) Frontend type-check + production build.
run "tsc + vite build" \
  bash -c "cd web && npm run build"

echo
echo "W10 acceptance gate PASSED"
echo "   Full log: $LOG"
