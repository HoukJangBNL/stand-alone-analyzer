#!/usr/bin/env bash
# restart-backend.sh — kill any running local API and start a single fresh
# instance pointed at PROD RDS (via the bastion tunnel on :5433).
#
# Why this exists: the web→remote-GPU SAM flow needs the local backend to
# share the SAME procrastinate queue + scan data the GPU worker sees, which
# lives in RDS — not the local dev PG. And stale uvicorn processes (from
# repeated restarts) caused port-8000 nondeterminism. This script guarantees
# ONE backend, on RDS, with current code. Invoked manually or by the
# pre-push hook (.git/hooks/pre-push) so a push always lands fresh code.
#
# Prereqs (not created here):
#   - bastion SSH tunnel up on 127.0.0.1:5433 → RDS (see docs/db-ops.md)
#   - AWS profile `qpress` with secretsmanager:GetSecretValue on the RDS secret
#   - local .venv with the project installed
#
# Usage: scripts/dev/restart-backend.sh   (logs to /tmp/saa-backend.log)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AWS_PROFILE_NAME="${AWS_PROFILE:-qpress}"
AWS_REGION_NAME="${AWS_REGION:-us-east-2}"
RDS_SECRET_ARN="arn:aws:secretsmanager:us-east-2:931886963315:secret:rds!db-beb90dd0-feef-45a5-b8b5-81af8d02e0d6-Cwxa1w"
PORT=8000
LOG=/tmp/saa-backend.log

echo "[restart-backend] verifying RDS tunnel on :5433"
if ! lsof -iTCP:5433 -sTCP:LISTEN -n >/dev/null 2>&1; then
  echo "[restart-backend] FATAL: no listener on :5433 — start the bastion tunnel first (see docs/db-ops.md)" >&2
  exit 1
fi

echo "[restart-backend] fetching RDS password from Secrets Manager"
RDS_PW="$(aws --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
  secretsmanager get-secret-value --secret-id "$RDS_SECRET_ARN" \
  --query SecretString --output text \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['password'])")"
if [[ -z "$RDS_PW" ]]; then
  echo "[restart-backend] FATAL: could not read RDS password" >&2
  exit 1
fi

echo "[restart-backend] killing any existing uvicorn (guarantees single instance)"
pkill -9 -f "uvicorn flake_analysis.api.main:app" 2>/dev/null || true
sleep 2

echo "[restart-backend] starting backend on RDS (:$PORT), log → $LOG"
SAA_PW="$RDS_PW" nohup bash -c "
  source '$REPO_ROOT/.venv/bin/activate' &&
  SAA_AUTH_DEV_BYPASS=1 \
  SAA_S3_BUCKET=qpress-uploads \
  SAA_ANALYSIS_FOLDER=/tmp/saa-analysis \
  SAA_DB_HOST=127.0.0.1 SAA_DB_PORT=5433 SAA_DB_USER=houk \
  SAA_DB_PASSWORD='$RDS_PW' SAA_DB_NAME=qpress \
  AWS_PROFILE='$AWS_PROFILE_NAME' \
  exec uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port $PORT
" > "$LOG" 2>&1 &

sleep 6
if lsof -iTCP:$PORT -sTCP:LISTEN -n >/dev/null 2>&1; then
  echo "[restart-backend] OK — backend listening on :$PORT (RDS)"
else
  echo "[restart-backend] WARN: backend not listening yet; check $LOG" >&2
  exit 1
fi
