#!/usr/bin/env bash
# deploy.sh — atomic deploy for stand-alone-analyzer (Plan 5 Task 6).
#
# Usage: sudo bash deploy.sh <release-tag>
#   <release-tag> must already exist as /opt/saa/releases/<release-tag>/
#   containing the freshly-built virtualenv + the React dist/ bundle.
#
# Symlink layout:
#   /opt/saa/current        -> /opt/saa/releases/<release-tag>
#   /usr/share/stand-alone-analyzer/web -> /opt/saa/current/web
#
# Rollback: re-run with the previous tag.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <release-tag>" >&2
    exit 2
fi
RELEASE_TAG="$1"
RELEASE_DIR="/opt/saa/releases/${RELEASE_TAG}"

if [[ ! -d "${RELEASE_DIR}" ]]; then
    echo "release dir not found: ${RELEASE_DIR}" >&2
    exit 1
fi
if [[ ! -d "${RELEASE_DIR}/web" ]]; then
    echo "release missing web/ bundle: ${RELEASE_DIR}/web" >&2
    exit 1
fi
if [[ ! -x "${RELEASE_DIR}/.venv/bin/uvicorn" ]]; then
    echo "release missing venv: ${RELEASE_DIR}/.venv/bin/uvicorn" >&2
    exit 1
fi

echo "[deploy] rotating /opt/saa/current -> ${RELEASE_DIR}"
ln -sfn "${RELEASE_DIR}" /opt/saa/current

echo "[deploy] rotating /usr/share/stand-alone-analyzer/web -> /opt/saa/current/web"
mkdir -p /usr/share/stand-alone-analyzer
ln -sfn /opt/saa/current/web /usr/share/stand-alone-analyzer/web

echo "[deploy] reloading systemd unit saa-api"
systemctl daemon-reload
systemctl restart saa-api

echo "[deploy] reloading nginx (site stand-alone-analyzer)"
nginx -t
systemctl reload nginx

echo "[deploy] OK — release ${RELEASE_TAG} live"
