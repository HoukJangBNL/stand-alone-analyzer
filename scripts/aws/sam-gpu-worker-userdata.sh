#!/usr/bin/env bash
# sam-gpu-worker-userdata.sh — Production GPU worker user-data (P4.4).
# Full doc + tunables: docs/sam-ops.md §3 / "M3 Asset Bootstrap".

set -euo pipefail

# --- Force IPv4 for apt + system DNS resolution --------------------------
# Default VPC has no IPv6 egress (verified 2026-05-27 across us-east-2a/b/c:
# single Main RT, IPv4-only default route, no IPv6 CIDR on subnets), but
# archive.ubuntu.com (Cloudflare CDN), security.ubuntu.com, and
# ppa.launchpadcontent.net all return AAAA records that glibc prefers under
# RFC 6724. apt-get update / add-apt-repository then hang on AAAA connect
# attempts and `set -euo pipefail` kills the whole bootstrap. Two-layer fix:
#   1. /etc/gai.conf precedence override flips glibc to prefer IPv4 (covers
#      curl, wget, dpkg, add-apt-repository — anything that resolves via
#      the system resolver). Live-verified on a t3.micro probe in -2b.
#   2. apt's own ForceIPv4 belt-and-suspenders.
# Both must run BEFORE any apt-get / apt update / add-apt-repository call.
echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
resolvectl flush-caches || true
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# --- Configurable tunables ------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/HoukJangBNL/stand-alone-analyzer.git}"
REPO_REF="${REPO_REF:-main}"
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
S3_MERGED_PFX="${S3_MERGED_PFX:-internal/sam/}"
S3_M3_PFX="${S3_M3_PFX:-internal/sam/m3/}"
S3_MERGED_M3_PFX="${S3_MERGED_M3_PFX:-internal/sam/merged_m3/}"
AWS_REGION="${AWS_REGION:-us-east-2}"
PY_VERSION="${PY_VERSION:-3.11}"
IDLE_TIMEOUT_S="${IDLE_TIMEOUT_S:-600}"
ABS_CAP_MIN="${ABS_CAP_MIN:-60}"

# --- Paths ----------------------------------------------------------------
LOG_FILE="/var/log/sam-gpu-worker-userdata.log"
WORK_ROOT="/opt/sam"
WEIGHTS_DIR="${WORK_ROOT}/weights"
M3_DIR="${WORK_ROOT}/m3"
REPO_DIR="${WORK_ROOT}/stand-alone-analyzer"
STATE_DIR="${WORK_ROOT}/state"
RUN_USER="ubuntu"
MERGED_PT="${WEIGHTS_DIR}/merged.pt"
MERGED_M3_PT="${WEIGHTS_DIR}/merged_m3.pt"
ENV_FILE="/etc/flake-analysis-worker.env"

# --- Tee everything to log + console -------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "=== sam-gpu-worker-userdata start: $(date -u +%FT%TZ) ==="
echo "REPO=${REPO_URL}@${REPO_REF}"
echo "S3=s3://${S3_BUCKET}/${S3_MERGED_PFX}"
echo "S3_M3=s3://${S3_BUCKET}/${S3_M3_PFX}"
echo "S3_MERGED_M3=s3://${S3_BUCKET}/${S3_MERGED_M3_PFX}"
echo "REGION=${AWS_REGION} IDLE_TIMEOUT_S=${IDLE_TIMEOUT_S}"

# --- Helper: idempotency stamps ------------------------------------------
mkdir -p "${STATE_DIR}"
stamp() { echo "$(date -u +%FT%TZ) $1" >> "${STATE_DIR}/$1.done"; }
done_stamp() { [[ -f "${STATE_DIR}/$1.done" ]]; }

# --- Force re-run of fix-affected steps ----------------------------------
# AMI ami-0b7ec5ff47a1eff11 was baked before commits 196824a (sslmode) and
# d68a9a0 (env-quote). Until AMI re-bake (Task #220), invalidate these
# stamps so userdata re-runs the corrected env-file write and pulls the
# post-fix repo HEAD. See docs/sam-ops.md §17.1 for the root cause.
rm -f "${STATE_DIR}/env.done" "${STATE_DIR}/repo.done"

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
# When running on the AWS DLAMI base (post-#228), nvidia-smi already lists
# all visible GPUs and the AWS-validated driver is in place. Installing
# `cuda-drivers` from upstream NVIDIA repo would replace it with a stock
# 610-series driver that only enumerates 2 of 8 L40S cards on g6e.48xlarge
# (T13 attempt 4: i-0a326075c2fc624d3, nvidia-smi reported 2 / expected 8).
# Detect a working driver and skip the cuda-drivers install if present.
if ! done_stamp cuda; then
  echo "[2/8] CUDA 12.4 toolkit + driver"
  if command -v nvidia-smi >/dev/null && nvidia-smi -L 2>/dev/null | grep -q "^GPU"; then
    GPU_COUNT=$(nvidia-smi -L 2>/dev/null | wc -l)
    echo "  pre-installed NVIDIA driver detected (visible GPUs=${GPU_COUNT}); skipping cuda-drivers install"
    # toolkit-only — keep nvcc + headers without disturbing the kernel module.
    CUDA_KEYRING_DEB="/tmp/cuda-keyring.deb"
    wget -qO "${CUDA_KEYRING_DEB}" \
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i "${CUDA_KEYRING_DEB}"
    apt-get update -y
    apt-get install -y --no-install-recommends cuda-toolkit-12-4 || true
    apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true
  else
    echo "  no working NVIDIA driver detected; installing cuda-toolkit-12-4 + cuda-drivers from NVIDIA repo"
    CUDA_KEYRING_DEB="/tmp/cuda-keyring.deb"
    wget -qO "${CUDA_KEYRING_DEB}" \
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i "${CUDA_KEYRING_DEB}"
    apt-get update -y
    apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers
    apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true
  fi
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

  # AMI ami-0b7ec5ff47a1eff11 was baked with the repo owned by `ubuntu`
  # (the bake-time RUN_USER), but cloud-init's user-data runs as root
  # with an empty environment (no $HOME). git 2.35+ refuses cross-user
  # repo access with:
  #   fatal: detected dubious ownership in repository at '/opt/sam/...'
  # Two-belt fix:
  # (1) chown the repo dir to root before any git op so ownership
  #     matches the running uid.
  # (2) `git config --system` (writes /etc/gitconfig — no $HOME
  #     dependency, applies to ALL users including root and ubuntu).
  #     T7c: the prior `--global` form crashed cloud-init with
  #     `fatal: $HOME not set` because cloud-init scripts_user runs
  #     as root with an empty env block.
  # Both are idempotent — safe to run on every cold launch.
  git config --system --add safe.directory '*'
  if [[ -d "${REPO_DIR}/.git" ]]; then
    chown -R "$(id -u):$(id -g)" "${REPO_DIR}"
  fi

  if [[ ! -d "${REPO_DIR}/.git" ]]; then
    git clone "${REPO_URL}" "${REPO_DIR}"
  fi
  pushd "${REPO_DIR}" > /dev/null
  git fetch --all --tags
  # Reset hard to remote ref — handles the case where AMI was baked from
  # an older SHA on the same branch and remote has advanced (T13 attempt 3:
  # AMI baked at 01ceb7f, main 341 commits ahead, vendor gitlink shifted).
  # Also pull submodule URL/SHA changes via `submodule sync` before update.
  git reset --hard "origin/${REPO_REF}" 2>/dev/null \
    || git reset --hard "${REPO_REF}"
  git submodule sync --recursive
  git submodule update --init --recursive --force vendor/QPress-SAM-Flake
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
  # M3 multi-GPU path needs `peft` (vendor `lora.apply_lora_to_sam2_components`
  # → `from peft import LoraConfig, get_peft_model` at lora.py:11). The vendor
  # `requirements-inference.txt` intentionally excludes peft (its top comment:
  # "peft / bitsandbytes intentionally excluded — merged weights are used") —
  # i.e. peft is out of scope for the merged.pt single-GPU path. Installing
  # it here scopes the dep to the AMI/runtime where the M3 finetuned path is
  # exercised, without contradicting vendor's design boundary. See
  # docs/sam-ops.md §15.3 (item 2) and §15.6 (item 1) for the missing-dep
  # failure mode that motivated this.
  sudo -u "${RUN_USER}" -H \
    env PATH="/usr/local/bin:/usr/bin:/bin" \
    /usr/local/bin/uv pip install \
      --python "${REPO_DIR}/.venv/bin/python" \
      "peft>=0.8.0,<0.20"
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

# --- Step 5b: download M3 4-asset bundle from S3 -------------------------
# The vendor multi-GPU path (run_amg_v2.run_multi_process) needs the full
# 4-asset bundle, not the single-file merged.pt. Layout under ${M3_DIR}:
#   sam2.1/sam2.1_hiera_l.pt          (~898 MB, base SAM2.1 ckpt)
#   sam2.1/configs/sam2.1_hiera_l.yaml (~3.8 KB)
#   sam2_lora/best_model.pth          (~962 MB, LoRA weights)
#   sam2_lora/args.json               (~1.5 KB, LoRA hyperparams)
# `aws s3 sync` is natively idempotent (compares size + mtime), so reboots
# / re-runs skip already-present files. M3 is additive — single-GPU
# merged.pt path above is untouched.
mkdir -p "${M3_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${M3_DIR}"

if ! done_stamp m3-assets; then
  echo "[6b/8] sync M3 4-asset bundle from s3://${S3_BUCKET}/${S3_M3_PFX}"
  aws s3 sync "s3://${S3_BUCKET}/${S3_M3_PFX}" "${M3_DIR}/" \
    --region "${AWS_REGION}" \
    --no-progress
  # Sanity: all 4 expected files present.
  for f in \
    "${M3_DIR}/sam2.1/sam2.1_hiera_l.pt" \
    "${M3_DIR}/sam2.1/configs/sam2.1_hiera_l.yaml" \
    "${M3_DIR}/sam2_lora/best_model.pth" \
    "${M3_DIR}/sam2_lora/args.json"; do
    if [[ ! -s "${f}" ]]; then
      echo "FATAL: M3 asset missing or empty: ${f}" >&2
      exit 1
    fi
  done
  ls -la "${M3_DIR}/sam2.1/" "${M3_DIR}/sam2.1/configs/" "${M3_DIR}/sam2_lora/"
  du -sh "${M3_DIR}"
  stamp m3-assets
fi

# --- Step 5c: vendor base-ckpt prod-path symlinks ------------------------
# The M3 LoRA bundle's `args.json` was saved on the trainer host with
# absolute paths baked in:
#   model_dir   = "/home2/qpress/qpress/models/"
#   checkpoint  = "/home2/qpress/qpress/models/sam2.1/sam2.1_hiera_l.pt"
#   config      = "/home2/qpress/qpress/models/sam2.1/configs/sam2.1_hiera_l.yaml"
# (see tests/fixtures/sam_m3_args.json). Vendor `build_sam2_finetuned`
# (vendor/QPress-SAM-Flake/run_amg_v2.py:539) reads `train_args["checkpoint"]`
# verbatim and `Path(...).exists()`-checks it before calling `build_sam2`.
#
# Our adapter `_load_and_patch_args` (src/flake_analysis/core/pipeline/sam.py:153)
# rewrites those paths in memory via a monkeypatch on
# `run_amg_v2.load_training_args`. **However**, vendor `run_multi_process`
# (run_amg_v2.py:1113) uses `mp.get_context("spawn").Pool` — spawn workers
# re-import `run_amg_v2` in fresh interpreters and never see the parent's
# monkeypatch. They call the unpatched `load_training_args` and read the
# raw prod paths. Hence the filesystem symlinks: the only way to satisfy
# the spawn-worker subprocesses without touching `args.json` on disk or
# rewriting vendor.
#
# See docs/sam-ops.md §15.3 (item 1) and §15.6 (item 2). Permanent fix
# (rewrite vendor's `args.json` model_dir at AMI/asset bake time, or have
# the worker entry-point materialize a patched args.json in a per-run
# scratch dir) is filed for a separate task.
if ! done_stamp m3-prod-symlinks; then
  echo "[6c/8] create vendor prod-path symlinks for spawn workers"
  PROD_MODELS_ROOT="/home2/qpress/qpress/models"
  mkdir -p "${PROD_MODELS_ROOT}/sam2.1"
  ln -sfn "${M3_DIR}/sam2.1/sam2.1_hiera_l.pt" \
    "${PROD_MODELS_ROOT}/sam2.1/sam2.1_hiera_l.pt"
  ln -sfn "${M3_DIR}/sam2.1/configs" \
    "${PROD_MODELS_ROOT}/sam2.1/configs"
  ln -sfn "${M3_DIR}/sam2_lora" \
    "${PROD_MODELS_ROOT}/sam2_lora"
  # Sanity: the file the spawn worker will stat() must resolve.
  test -f "${PROD_MODELS_ROOT}/sam2.1/sam2.1_hiera_l.pt"
  test -d "${PROD_MODELS_ROOT}/sam2.1/configs"
  ls -la "${PROD_MODELS_ROOT}/sam2.1/" "${PROD_MODELS_ROOT}/sam2_lora/" || true
  stamp m3-prod-symlinks
fi

# --- Step 5d: download merged_m3.<sha8>.pt from S3 (soft-miss OK) --------
# Producer: scripts/aws/sam-build-merged-m3.sh (docs/sam-ops.md §16). The
# multi-GPU dual-mode pipeline (#209) prefers this single-file artifact via
# SAM_MERGED_M3_PATH so vendor `build_sam2(...)` can load it directly,
# skipping the per-forward-pass LoRA application that bottlenecked the
# 2026-05-28 8-GPU run (12.16 s/card-img vs 3.98 s/img baseline; §15).
#
# Soft-miss is intentional: if no merged_m3 has been published yet, the
# worker falls back to the M3 4-asset LoRA-runtime path (steps 5b + 5c).
# This step ONLY hard-fails on SHA256 corruption, mirroring step 5's
# refusal-to-serve policy for any artifact that IS present.
if ! done_stamp merged-m3-weights; then
  echo "[6d/8] discover + download merged_m3.<sha8>.pt from S3 (soft-miss OK)"
  LATEST_M3_KEY=$(aws s3api list-objects-v2 \
    --region "${AWS_REGION}" \
    --bucket "${S3_BUCKET}" \
    --prefix "${S3_MERGED_M3_PFX}sam2.1_hiera_large.merged_m3." \
    --query 'sort_by(Contents, &LastModified)[?ends_with(Key, `.pt`)] | [-1].Key' \
    --output text)
  if [[ -z "${LATEST_M3_KEY}" || "${LATEST_M3_KEY}" == "None" ]]; then
    echo "no merged_m3 found in s3://${S3_BUCKET}/${S3_MERGED_M3_PFX} — worker will use LoRA-runtime path"
    stamp merged-m3-skipped
  else
    echo "Latest merged_m3: s3://${S3_BUCKET}/${LATEST_M3_KEY}"

    # Fetch the .sha256 sidecar first.
    M3_SHA_KEY="${LATEST_M3_KEY}.sha256"
    EXPECTED_M3_SHA=$(aws s3 cp "s3://${S3_BUCKET}/${M3_SHA_KEY}" - \
      --region "${AWS_REGION}" \
      --no-progress \
      | awk '{print $1}')
    if [[ -z "${EXPECTED_M3_SHA}" ]]; then
      echo "FATAL: sidecar ${M3_SHA_KEY} is empty or missing" >&2
      exit 1
    fi
    echo "Expected SHA256: ${EXPECTED_M3_SHA}"

    # Download merged_m3.pt.
    aws s3 cp "s3://${S3_BUCKET}/${LATEST_M3_KEY}" "${MERGED_M3_PT}" \
      --region "${AWS_REGION}" \
      --no-progress
    ls -lh "${MERGED_M3_PT}"

    # Verify SHA256 BEFORE we mark the stamp.
    ACTUAL_M3_SHA=$(sha256sum "${MERGED_M3_PT}" | awk '{print $1}')
    echo "Actual SHA256:   ${ACTUAL_M3_SHA}"
    if [[ "${ACTUAL_M3_SHA}" != "${EXPECTED_M3_SHA}" ]]; then
      echo "FATAL: SHA256 mismatch on merged_m3.pt — refusing to serve" >&2
      rm -f "${MERGED_M3_PT}"
      exit 1
    fi
    echo "${LATEST_M3_KEY}" > "${STATE_DIR}/active_merged_m3_key"
    stamp merged-m3-weights
  fi
fi

# --- Step 5e: stage measurement dataset (idempotent) ---------------------
# GPU Measurement Harness (Task 12). Cold launch syncs the smoke dataset
# from S3 so scripts/sam/measure-defer.py can enqueue runs against a known
# local path without per-run S3 I/O. `aws s3 sync` is natively idempotent
# (size + mtime), and the done_stamp guard short-circuits re-runs after
# first success. Operators can override DATASET_PFX at LT version create
# time to point a future run at a different dataset prefix.
DATASET_PFX="${DATASET_PFX:-internal/sam/scan6-100/}"
DATASET_DIR="${WORK_ROOT}/dataset/$(basename "${DATASET_PFX%/}")"
if ! done_stamp dataset; then
  echo "[6e/8] stage measurement dataset s3://${S3_BUCKET}/${DATASET_PFX} → ${DATASET_DIR}"
  mkdir -p "${DATASET_DIR}"
  aws s3 sync "s3://${S3_BUCKET}/${DATASET_PFX}" "${DATASET_DIR}/" \
    --region "${AWS_REGION}" \
    --no-progress
  chown -R "${RUN_USER}:${RUN_USER}" "${DATASET_DIR}"
  du -sh "${DATASET_DIR}"
  stamp dataset
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
SAA_DB_HOST="${DB_HOST}"
SAA_DB_PORT="${DB_PORT}"
SAA_DB_USER="${DB_USER}"
SAA_DB_NAME="${DB_NAME}"
SAA_DB_PASSWORD="${DB_PASSWORD}"
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
Environment=SAM_M3_DIR=${M3_DIR}
Environment=SAM_MERGED_M3_PATH=${MERGED_M3_PT}
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

# --- abs-cap self-terminate ---------------------------------------------
# Belt-and-suspenders against operator-session death (#229 §20). Fires
# ABS_CAP_MIN minutes after boot and unconditionally terminates the
# instance, regardless of measure-run.sh's polling state.
install -m 0755 \
    "${REPO_DIR}/scripts/aws/abs-cap-terminate.sh" \
    /usr/local/bin/abs-cap-terminate.sh

cat > /etc/systemd/system/flake-analysis-abs-cap.service <<'UNIT'
[Unit]
Description=Absolute wall-clock cap — terminate this instance
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/abs-cap-terminate.sh
UNIT

cat > /etc/systemd/system/flake-analysis-abs-cap.timer <<UNIT
[Unit]
Description=Fire abs-cap.service ${ABS_CAP_MIN} min after boot

[Timer]
OnBootSec=${ABS_CAP_MIN}min
Unit=flake-analysis-abs-cap.service
AccuracySec=10s

[Install]
WantedBy=timers.target
UNIT

# Reload + enable + start.
systemctl daemon-reload
systemctl enable --now flake-analysis-worker.service
systemctl enable --now flake-analysis-spot-monitor.timer
systemctl enable --now flake-analysis-idle-shutdown.timer
systemctl enable --now flake-analysis-abs-cap.timer

echo "=== sam-gpu-worker-userdata done: $(date -u +%FT%TZ) ==="
