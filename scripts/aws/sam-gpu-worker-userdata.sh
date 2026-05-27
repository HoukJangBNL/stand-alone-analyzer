#!/usr/bin/env bash
# sam-gpu-worker-userdata.sh — Production GPU worker user-data (P4.4).
#
# Distinct from sam-gpu-bootstrap.sh:
#   - bootstrap: builds + uploads merged.pt to S3 (one-time, owner-driven)
#   - worker:    downloads merged.pt from S3 + runs the procrastinate worker
#                that drains the `gpu` queue. Self-terminates after 10 min idle.
#
# Runs as user-data on a stock Ubuntu 22.04 amd64 g6e.xlarge spot launched
# by the qpress-sam-gpu-worker launch template.
#
# === What success looks like ============================================
#   1. CUDA 12.4 + Python 3.11 + uv installed.
#   2. Repo cloned at REPO_REF; submodule init.
#   3. uv sync --frozen + SAM2 inference deps installed.
#   4. merged.pt fetched from S3 with SHA256 verified, cached at
#      /opt/sam/weights/merged.pt.
#   5. systemd unit flake-analysis-worker.service running
#      `python -m flake_analysis.worker --queue gpu --concurrency 1`.
#   6. systemd timer flake-analysis-spot-monitor.timer polls IMDS every 5s
#      for spot-interrupt notice; on detection, SIGTERM the worker.
#   7. systemd timer flake-analysis-idle-shutdown.timer checks every 60s
#      whether the worker is idle for >10 min; if so, self-terminate.
#   8. /var/log/sam-gpu-worker-userdata.log captures every step.
# =========================================================================
#
# Tunables (override at launch-template-version creation time via env):
#   REPO_URL        Git URL (default: HoukJangBNL fork)
#   REPO_REF        SHA the launch template was built against (default: main)
#   S3_BUCKET       Bucket holding merged weights (default: qpress-uploads)
#   S3_MERGED_PFX   Prefix where merged weights live (default: internal/sam/)
#   AWS_REGION      Region (default: us-east-2)
#   PY_VERSION      Python series (default: 3.11)
#   IDLE_TIMEOUT_S  Idle seconds before self-terminate (default: 600 = 10 min)
#
# DB credentials: pulled from SSM Parameter Store at boot:
#   /qpress-sam/db_host        (String)
#   /qpress-sam/db_port        (String)
#   /qpress-sam/db_user        (String)
#   /qpress-sam/db_name        (String)
#   /qpress-sam/db_password    (SecureString)
#
# Owner must populate these BEFORE the launch template will produce a
# working worker. See docs/sam-ops.md §3.

set -euo pipefail

# --- Force IPv4 for apt --------------------------------------------------
# Some AZs have flaky IPv6 egress to archive.ubuntu.com which causes apt-get
# update to hang/timeout under `set -e`. Pinning apt to IPv4 makes bootstrap
# deterministic across AZs. Must run BEFORE any apt-get / apt update call.
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# --- Configurable tunables ------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/HoukJangBNL/stand-alone-analyzer.git}"
REPO_REF="${REPO_REF:-main}"
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
S3_MERGED_PFX="${S3_MERGED_PFX:-internal/sam/}"
AWS_REGION="${AWS_REGION:-us-east-2}"
PY_VERSION="${PY_VERSION:-3.11}"
IDLE_TIMEOUT_S="${IDLE_TIMEOUT_S:-600}"

# --- Paths ----------------------------------------------------------------
LOG_FILE="/var/log/sam-gpu-worker-userdata.log"
WORK_ROOT="/opt/sam"
WEIGHTS_DIR="${WORK_ROOT}/weights"
REPO_DIR="${WORK_ROOT}/stand-alone-analyzer"
STATE_DIR="${WORK_ROOT}/state"
RUN_USER="ubuntu"
MERGED_PT="${WEIGHTS_DIR}/merged.pt"
ENV_FILE="/etc/flake-analysis-worker.env"

# --- Tee everything to log + console -------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "=== sam-gpu-worker-userdata start: $(date -u +%FT%TZ) ==="
echo "REPO=${REPO_URL}@${REPO_REF}"
echo "S3=s3://${S3_BUCKET}/${S3_MERGED_PFX}"
echo "REGION=${AWS_REGION} IDLE_TIMEOUT_S=${IDLE_TIMEOUT_S}"

# --- Helper: idempotency stamps ------------------------------------------
mkdir -p "${STATE_DIR}"
stamp() { echo "$(date -u +%FT%TZ) $1" >> "${STATE_DIR}/$1.done"; }
done_stamp() { [[ -f "${STATE_DIR}/$1.done" ]]; }

# --- Helper: IMDSv2 token ------------------------------------------------
imds_token() {
  curl -sS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
    http://169.254.169.254/latest/api/token
}

# --- Step 1: apt base packages -------------------------------------------
# (Mirrors sam-gpu-bootstrap.sh step 1 — duplicated for two reasons:
#   1. Keep the two user-data scripts independently auditable.
#   2. Avoid a shared helper that would have to live on the AMI itself.)
if ! done_stamp apt-base; then
  echo "[1/8] apt update + base packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    software-properties-common \
    unzip \
    wget
  stamp apt-base
fi

# --- Step 2: NVIDIA driver + CUDA 12.4 toolkit ---------------------------
if ! done_stamp cuda; then
  echo "[2/8] CUDA 12.4 toolkit + driver"
  CUDA_KEYRING_DEB="/tmp/cuda-keyring.deb"
  wget -qO "${CUDA_KEYRING_DEB}" \
    https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i "${CUDA_KEYRING_DEB}"
  apt-get update -y
  apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers
  apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true
  stamp cuda
fi

# --- Step 3: Python 3.11 + uv --------------------------------------------
if ! done_stamp python; then
  echo "[3/8] Python ${PY_VERSION} via deadsnakes"
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y --no-install-recommends \
    "python${PY_VERSION}" \
    "python${PY_VERSION}-dev" \
    "python${PY_VERSION}-venv"
  stamp python
fi

if ! done_stamp uv; then
  echo "[3b/8] uv installer"
  curl -LsSf https://astral.sh/uv/install.sh | \
    env UV_INSTALL_DIR="/usr/local/bin" sh
  /usr/local/bin/uv --version
  stamp uv
fi

# --- Step 4: clone repo + submodule + uv sync ----------------------------
if ! done_stamp repo; then
  echo "[4/8] clone repo + submodule"
  mkdir -p "${WORK_ROOT}"
  if [[ ! -d "${REPO_DIR}/.git" ]]; then
    git clone "${REPO_URL}" "${REPO_DIR}"
  fi
  pushd "${REPO_DIR}" > /dev/null
  git fetch --all --tags
  git checkout "${REPO_REF}"
  git submodule update --init --recursive vendor/QPress-SAM-Flake
  popd > /dev/null
  chown -R "${RUN_USER}:${RUN_USER}" "${WORK_ROOT}"
  stamp repo
fi

if ! done_stamp deps; then
  echo "[5/8] uv sync + SAM2 inference deps"
  pushd "${REPO_DIR}" > /dev/null
  sudo -u "${RUN_USER}" -H \
    env PATH="/usr/local/bin:/usr/bin:/bin" \
    /usr/local/bin/uv sync --frozen --python "python${PY_VERSION}"
  sudo -u "${RUN_USER}" -H \
    env PATH="/usr/local/bin:/usr/bin:/bin" \
    /usr/local/bin/uv pip install \
      --python "${REPO_DIR}/.venv/bin/python" \
      --index-strategy unsafe-best-match \
      --extra-index-url https://download.pytorch.org/whl/cu124 \
      -r vendor/QPress-SAM-Flake/requirements-inference.txt
  popd > /dev/null
  stamp deps
fi

# --- Step 5: download merged.pt from S3 + verify SHA256 ------------------
# The bootstrap (P4.3 Phase 2) PUTs both the merged.pt and a .sha256
# sidecar. We discover the latest merged.pt via S3 list (sorted by
# LastModified), then verify before caching. If verification fails the
# instance halts here — better to never start serving than to serve from
# a corrupted weights file.
mkdir -p "${WEIGHTS_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${WEIGHTS_DIR}"

if ! done_stamp weights; then
  echo "[6/8] discover + download merged.pt from S3"
  # List the merged.pt objects and pick the most-recently uploaded one.
  LATEST_KEY=$(aws s3api list-objects-v2 \
    --region "${AWS_REGION}" \
    --bucket "${S3_BUCKET}" \
    --prefix "${S3_MERGED_PFX}sam2.1_hiera_large.merged." \
    --query 'sort_by(Contents, &LastModified)[?ends_with(Key, `.pt`)] | [-1].Key' \
    --output text)
  if [[ -z "${LATEST_KEY}" || "${LATEST_KEY}" == "None" ]]; then
    echo "FATAL: no merged.pt found in s3://${S3_BUCKET}/${S3_MERGED_PFX}" >&2
    echo "       Owner must run P4.3 Phase 2 (bootstrap) first." >&2
    exit 1
  fi
  echo "Latest merged.pt: s3://${S3_BUCKET}/${LATEST_KEY}"

  # Fetch the .sha256 sidecar first.
  SHA_KEY="${LATEST_KEY}.sha256"
  EXPECTED_SHA=$(aws s3 cp "s3://${S3_BUCKET}/${SHA_KEY}" - \
    --region "${AWS_REGION}" \
    --no-progress \
    | awk '{print $1}')
  if [[ -z "${EXPECTED_SHA}" ]]; then
    echo "FATAL: sidecar ${SHA_KEY} is empty or missing" >&2
    exit 1
  fi
  echo "Expected SHA256: ${EXPECTED_SHA}"

  # Download merged.pt.
  aws s3 cp "s3://${S3_BUCKET}/${LATEST_KEY}" "${MERGED_PT}" \
    --region "${AWS_REGION}" \
    --no-progress
  ls -lh "${MERGED_PT}"

  # Verify SHA256 BEFORE we mark the stamp.
  ACTUAL_SHA=$(sha256sum "${MERGED_PT}" | awk '{print $1}')
  echo "Actual SHA256:   ${ACTUAL_SHA}"
  if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "FATAL: SHA256 mismatch on merged.pt — refusing to serve" >&2
    rm -f "${MERGED_PT}"
    exit 1
  fi
  echo "${LATEST_KEY}" > "${STATE_DIR}/active_weights_key"
  stamp weights
fi

# --- Step 6: pull DB creds from SSM + write env file ---------------------
# Worker reads SAA_DB_* from /etc/flake-analysis-worker.env via
# EnvironmentFile= in the systemd unit. SSM SecureString for password
# avoids any plaintext on disk in the user-data history.
if ! done_stamp env; then
  echo "[7/8] fetch DB params from SSM into ${ENV_FILE}"
  fetch_ssm() {
    local name="$1"
    local with_decryption="${2:-false}"
    local args=(--region "${AWS_REGION}" --name "${name}" --query 'Parameter.Value' --output text)
    if [[ "${with_decryption}" == "true" ]]; then
      args+=(--with-decryption)
    fi
    aws ssm get-parameter "${args[@]}"
  }

  DB_HOST=$(fetch_ssm /qpress-sam/db_host)
  DB_PORT=$(fetch_ssm /qpress-sam/db_port)
  DB_USER=$(fetch_ssm /qpress-sam/db_user)
  DB_NAME=$(fetch_ssm /qpress-sam/db_name)
  DB_PASSWORD=$(fetch_ssm /qpress-sam/db_password true)

  umask 077
  cat > "${ENV_FILE}" <<EOF
SAA_DB_HOST=${DB_HOST}
SAA_DB_PORT=${DB_PORT}
SAA_DB_USER=${DB_USER}
SAA_DB_NAME=${DB_NAME}
SAA_DB_PASSWORD=${DB_PASSWORD}
EOF
  chmod 0600 "${ENV_FILE}"
  chown root:root "${ENV_FILE}"
  stamp env
fi

# --- Step 7: install systemd units ---------------------------------------
echo "[8/8] install systemd units"

# 7a. Worker service.
cat > /etc/systemd/system/flake-analysis-worker.service <<UNIT
[Unit]
Description=Flake Analysis procrastinate GPU worker (P4.4)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
Environment=SAM_WEIGHTS_PATH=${MERGED_PT}
ExecStart=/usr/local/bin/uv run python -m flake_analysis.worker --queue gpu --concurrency 1 --name %H
Restart=on-failure
RestartSec=10
# SIGTERM lets procrastinate finish the in-flight job (bounded by its
# shutdown_graceful_timeout). The worker module relies on this for both
# normal shutdown and spot-interrupt-driven shutdown.
KillSignal=SIGTERM
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
UNIT

# 7b. Spot-interrupt monitor — polls IMDSv2 every 5s.
cat > /usr/local/sbin/flake-analysis-spot-monitor.sh <<'MONITOR'
#!/usr/bin/env bash
# Polls IMDSv2 for the spot-interrupt notice. On detection, SIGTERM the
# worker so procrastinate can mark the in-flight run failed and re-enqueue.
# AWS gives a 2-minute warning before the spot reclaim — plenty of time
# for the worker's graceful shutdown to flush.
set -euo pipefail
TOKEN=$(curl -sS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" \
  http://169.254.169.254/latest/api/token)
HTTP_STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "X-aws-ec2-metadata-token: ${TOKEN}" \
  http://169.254.169.254/latest/meta-data/spot/instance-action || echo "000")
if [[ "${HTTP_STATUS}" == "200" ]]; then
  logger -t spot-monitor "spot interruption notice received — terminating worker"
  systemctl kill -s SIGTERM flake-analysis-worker.service || true
fi
MONITOR
chmod +x /usr/local/sbin/flake-analysis-spot-monitor.sh

cat > /etc/systemd/system/flake-analysis-spot-monitor.service <<'UNIT'
[Unit]
Description=Poll IMDS for spot interruption notice (P4.4)
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/flake-analysis-spot-monitor.sh
UNIT

cat > /etc/systemd/system/flake-analysis-spot-monitor.timer <<'UNIT'
[Unit]
Description=Run spot-monitor every 5 seconds
[Timer]
OnBootSec=10s
OnUnitActiveSec=5s
AccuracySec=1s
Unit=flake-analysis-spot-monitor.service
[Install]
WantedBy=timers.target
UNIT

# 7c. Idle-timeout monitor — every 60s, if the worker is idle for
# >IDLE_TIMEOUT_S, terminate the instance.
#
# "Idle" = no in-flight (state='doing') jobs in procrastinate_jobs that
# this worker name owns AND the worker hasn't had an in-flight job for
# the past IDLE_TIMEOUT_S seconds.
cat > /usr/local/sbin/flake-analysis-idle-shutdown.sh <<IDLESH
#!/usr/bin/env bash
set -euo pipefail
IDLE_TIMEOUT_S=${IDLE_TIMEOUT_S}
STATE_FILE=/var/lib/flake-analysis-idle-since
ENV_FILE=${ENV_FILE}
REGION=${AWS_REGION}
. "\${ENV_FILE}"

# Count in-flight jobs (state='doing') across the gpu queue.
# psql is in /usr/bin/psql via apt - we need it; install lazily on first run.
if ! command -v psql >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends postgresql-client > /dev/null
fi

INFLIGHT=\$(PGPASSWORD="\${SAA_DB_PASSWORD}" psql \
  -h "\${SAA_DB_HOST}" -p "\${SAA_DB_PORT}" -U "\${SAA_DB_USER}" -d "\${SAA_DB_NAME}" \
  -tAc "SELECT count(*) FROM procrastinate_jobs WHERE queue_name='gpu' AND status='doing'" \
  2>/dev/null || echo "0")
INFLIGHT=\${INFLIGHT//[!0-9]/}
INFLIGHT=\${INFLIGHT:-0}

NOW=\$(date +%s)
if [[ "\${INFLIGHT}" -gt 0 ]]; then
  # Active — clear the idle marker.
  rm -f "\${STATE_FILE}"
  exit 0
fi

# No in-flight jobs. Mark idle-since if not already, then check timeout.
if [[ ! -f "\${STATE_FILE}" ]]; then
  echo "\${NOW}" > "\${STATE_FILE}"
  exit 0
fi
IDLE_SINCE=\$(cat "\${STATE_FILE}")
ELAPSED=\$((NOW - IDLE_SINCE))
if [[ "\${ELAPSED}" -ge "\${IDLE_TIMEOUT_S}" ]]; then
  TOKEN=\$(curl -sS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" \
    http://169.254.169.254/latest/api/token)
  IID=\$(curl -sS -H "X-aws-ec2-metadata-token: \${TOKEN}" \
    http://169.254.169.254/latest/meta-data/instance-id)
  logger -t idle-shutdown "idle for \${ELAPSED}s >= \${IDLE_TIMEOUT_S}s — terminating \${IID}"
  aws ec2 terminate-instances --region "\${REGION}" --instance-ids "\${IID}" || true
fi
IDLESH
chmod +x /usr/local/sbin/flake-analysis-idle-shutdown.sh

cat > /etc/systemd/system/flake-analysis-idle-shutdown.service <<'UNIT'
[Unit]
Description=Self-terminate this spot worker after idle timeout (P4.4)
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/flake-analysis-idle-shutdown.sh
UNIT

cat > /etc/systemd/system/flake-analysis-idle-shutdown.timer <<'UNIT'
[Unit]
Description=Run idle-shutdown check every 60 seconds
[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=5s
Unit=flake-analysis-idle-shutdown.service
[Install]
WantedBy=timers.target
UNIT

# Reload + enable + start.
systemctl daemon-reload
systemctl enable --now flake-analysis-worker.service
systemctl enable --now flake-analysis-spot-monitor.timer
systemctl enable --now flake-analysis-idle-shutdown.timer

echo "=== sam-gpu-worker-userdata done: $(date -u +%FT%TZ) ==="
