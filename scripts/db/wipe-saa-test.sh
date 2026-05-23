#!/usr/bin/env bash
# scripts/db/wipe-saa-test.sh
#
# Pre-flight wipe before applying alembic 0004 (W10) on a LOCAL test database.
# Refuses to run unless the target DB name starts with "saa_test" — never
# pointable at RDS / production accidentally.
#
# Usage:
#   bash scripts/db/wipe-saa-test.sh saa_test [host] [user]
#
# Defaults: host=127.0.0.1 user=houkjang
set -euo pipefail

DB_NAME="${1:?Usage: $0 <db_name> [host] [user]}"
DB_HOST="${2:-127.0.0.1}"
DB_USER="${3:-houkjang}"

if [[ ! "$DB_NAME" =~ ^saa_test ]]; then
  echo "REFUSING: db name '$DB_NAME' must start with 'saa_test' (got: $DB_NAME)" >&2
  echo "   This script is for local test DBs only. RDS / qpress wipes are not allowed." >&2
  exit 2
fi

SQL_FILE="$(dirname "$0")/wipe-saa-test-pre-w10.sql"
if [[ ! -f "$SQL_FILE" ]]; then
  echo "REFUSING: SQL file not found at $SQL_FILE" >&2
  exit 3
fi

echo "Pre-flight wipe target: $DB_USER@$DB_HOST/$DB_NAME"
echo "   Using $SQL_FILE"
echo
read -r -p "Continue? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$SQL_FILE"
echo "Wipe complete. Now run: uv run alembic upgrade head"
