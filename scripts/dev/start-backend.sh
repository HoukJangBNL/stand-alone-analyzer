#!/usr/bin/env bash
# Start FastAPI backend for local manual testing.
# Reads .env at repo root (loaded by pydantic-settings + the explicit `set -a` below).
# Usage: ./scripts/dev/start-backend.sh

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "[start-backend] .env not found — copy .env.example or create one." >&2
  exit 1
fi

# Export every VAR=VALUE in .env into the shell environment so subprocesses
# (uvicorn, boto3, etc.) see them. pydantic-settings reads .env on its own,
# but boto3/AWS_* and SAA_AUTH_DEV_BYPASS are read directly from os.environ.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

echo "[start-backend] DB=${SAA_DB_NAME}@${SAA_DB_HOST}:${SAA_DB_PORT}"
echo "[start-backend] S3=${SAA_S3_BUCKET} (profile=${AWS_PROFILE}, region=${AWS_DEFAULT_REGION})"
echo "[start-backend] AUTH=dev-bypass (SAA_ENV=${SAA_ENV})"
echo "[start-backend] uvicorn http://0.0.0.0:8000 (reload)"

exec uv run uvicorn flake_analysis.api.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
