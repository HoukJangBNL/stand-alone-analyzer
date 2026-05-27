#!/usr/bin/env bash
# sam-gpu-bootstrap.sh — Bootstrap a fresh Ubuntu 22.04 g6e.xlarge for SAM2 inference.
#
# This script runs as EC2 user-data on a stock Ubuntu 22.04 amd64 AMI.
# Idempotent: re-runnable on the same box without reinstalling everything.
#
# === What success looks like ============================================
#   1. CUDA 12.x toolkit + cuDNN installed; `nvidia-smi` reports the L40S.
#   2. Python 3.11 + uv installed.
#   3. Repo cloned + `vendor/QPress-SAM-Flake` submodule initialized.
#   4. `uv sync --frozen` completes; SAM2 inference deps installed.
#   5. Base SAM2 weights downloaded to /opt/sam/weights/sam2.1_hiera_large.pt.
#   6. Prod LoRA adapter downloaded from S3 to /opt/sam/weights/best_model.pth.
#   7. merge_lora.py produces /opt/sam/weights/sam2.1_hiera_large.merged.pt
#      with the LoRA absorbed into base weights.
#   8. SHA256 computed; merged.pt + .sha256 uploaded to
#      s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha8>.pt
#   9. /var/log/sam-gpu-bootstrap.log captures every step.
# =========================================================================
#
# Tunables (override at instance launch via user-data env injection or by
# editing this file before --user-data upload):
#
#   REPO_URL        Git URL for stand-alone-analyzer (default: HoukJangBNL fork)
#   REPO_REF        Branch/SHA to check out (default: main)
#   S3_BUCKET       Bucket holding lora-source + merged outputs
#   S3_LORA_KEY     Object key for the source LoRA adapter
#   S3_MERGED_PFX   Prefix where merged weights are uploaded
#   AWS_REGION      AWS region (default: us-east-2)
#   LORA_RANK       LoRA rank used at training time (default: 16)
#   LORA_ALPHA      LoRA alpha used at training time (default: 32)
#   SAM2_BASE_URL   Base SAM2 hiera_large checkpoint URL
#   PY_VERSION      Python series (default: 3.11)

set -euo pipefail

# --- Force IPv4 for apt + system DNS resolution --------------------------
# Same fix as sam-gpu-worker-userdata.sh. Default VPC has no IPv6 egress,
# but archive.ubuntu.com / ppa.launchpadcontent.net / security.ubuntu.com
# return AAAA records that glibc prefers under RFC 6724 — causing apt and
# add-apt-repository to hang on AAAA connect attempts. Layer 1 fixes glibc
# resolver, layer 2 covers apt directly. Run BEFORE any apt call.
echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
resolvectl flush-caches || true
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# --- Configurable tunables ------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/HoukJangBNL/stand-alone-analyzer.git}"
REPO_REF="${REPO_REF:-main}"
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
S3_LORA_KEY="${S3_LORA_KEY:-internal/sam/lora-source/best_model.pth}"
S3_MERGED_PFX="${S3_MERGED_PFX:-internal/sam/}"
AWS_REGION="${AWS_REGION:-us-east-2}"
LORA_RANK="${LORA_RANK:-16}"
LORA_ALPHA="${LORA_ALPHA:-32}"
SAM2_BASE_URL="${SAM2_BASE_URL:-https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt}"
PY_VERSION="${PY_VERSION:-3.11}"

# --- Paths ----------------------------------------------------------------
LOG_FILE="/var/log/sam-gpu-bootstrap.log"
WORK_ROOT="/opt/sam"
WEIGHTS_DIR="${WORK_ROOT}/weights"
REPO_DIR="${WORK_ROOT}/stand-alone-analyzer"
STATE_DIR="${WORK_ROOT}/state"
RUN_USER="ubuntu"

# --- Tee everything to log + console -------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "=== sam-gpu-bootstrap start: $(date -u +%FT%TZ) ==="
echo "REPO_URL=${REPO_URL} REPO_REF=${REPO_REF}"
echo "S3=s3://${S3_BUCKET}/${S3_MERGED_PFX} LORA=s3://${S3_BUCKET}/${S3_LORA_KEY}"
echo "RANK=${LORA_RANK} ALPHA=${LORA_ALPHA}"

# --- Helper: idempotency stamps ------------------------------------------
mkdir -p "${STATE_DIR}"
stamp() { echo "$(date -u +%FT%TZ) $1" >> "${STATE_DIR}/$1.done"; }
done_stamp() { [[ -f "${STATE_DIR}/$1.done" ]]; }

# --- Step 1: apt base packages -------------------------------------------
if ! done_stamp apt-base; then
  echo "[1/9] apt update + base packages"
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
# Use NVIDIA's apt keyring for Ubuntu 22.04. Pin to CUDA 12.4 (compatible with
# torch 2.x prebuilt wheels via the cu124 index).
if ! done_stamp cuda; then
  echo "[2/9] CUDA 12.4 toolkit + driver"
  CUDA_KEYRING_DEB="/tmp/cuda-keyring.deb"
  wget -qO "${CUDA_KEYRING_DEB}" \
    https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i "${CUDA_KEYRING_DEB}"
  apt-get update -y
  # cuda-toolkit-12-4 pulls toolkit only; the open-kernel driver is bundled.
  # cuda-drivers gets the proprietary driver matched to the toolkit.
  apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers
  # cuDNN 9 series ships in the same repo as libcudnn9-cuda-12.
  apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true
  stamp cuda
fi

# --- Step 3: Python 3.11 via deadsnakes PPA ------------------------------
# Ubuntu 22.04 ships 3.10 by default; deadsnakes provides 3.11 reliably.
if ! done_stamp python; then
  echo "[3/9] Python ${PY_VERSION} via deadsnakes"
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y --no-install-recommends \
    "python${PY_VERSION}" \
    "python${PY_VERSION}-dev" \
    "python${PY_VERSION}-venv"
  stamp python
fi

# --- Step 4: uv (astral) -------------------------------------------------
# Pinned: 0.4.x compatible with this project's pyproject (no uv version pin
# in pyproject as of 2026-05-27; >=0.4 is the floor we support).
if ! done_stamp uv; then
  echo "[4/9] uv installer"
  # System-wide install at /usr/local/bin so both root + ubuntu user can use it.
  curl -LsSf https://astral.sh/uv/install.sh | \
    env UV_INSTALL_DIR="/usr/local/bin" sh
  /usr/local/bin/uv --version
  stamp uv
fi

# --- Step 5: clone repo + init submodule ---------------------------------
if ! done_stamp repo; then
  echo "[5/9] clone repo + submodule"
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

# --- Step 6: uv sync + SAM2 inference deps -------------------------------
if ! done_stamp deps; then
  echo "[6/9] uv sync + SAM2 inference deps"
  pushd "${REPO_DIR}" > /dev/null
  # Project deps (frozen lock).
  sudo -u "${RUN_USER}" -H \
    env PATH="/usr/local/bin:/usr/bin:/bin" \
    /usr/local/bin/uv sync --frozen --python "python${PY_VERSION}"
  # SAM2 + torch wheels for inference. Use the project venv .venv created by uv.
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

# --- Step 7: download base SAM2 weights ----------------------------------
mkdir -p "${WEIGHTS_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${WEIGHTS_DIR}"
BASE_PT="${WEIGHTS_DIR}/sam2.1_hiera_large.pt"
LORA_PT="${WEIGHTS_DIR}/best_model.pth"
MERGED_PT="${WEIGHTS_DIR}/sam2.1_hiera_large.merged.pt"
CONFIG_JSON="${WEIGHTS_DIR}/lora_config.json"

if ! done_stamp base-weights; then
  echo "[7/9] download base SAM2 weights"
  wget -qO "${BASE_PT}" "${SAM2_BASE_URL}"
  ls -lh "${BASE_PT}"
  stamp base-weights
fi

# --- Step 8: download LoRA from S3 + merge -------------------------------
if ! done_stamp merge; then
  echo "[8/9] download LoRA + merge"
  aws s3 cp "s3://${S3_BUCKET}/${S3_LORA_KEY}" "${LORA_PT}" \
    --region "${AWS_REGION}" \
    --no-progress
  ls -lh "${LORA_PT}"

  # Write the merge config (rank + alpha from training).
  cat > "${CONFIG_JSON}" <<EOF
{"rank": ${LORA_RANK}, "alpha": ${LORA_ALPHA}}
EOF

  # Run the merge script from the fork.
  pushd "${REPO_DIR}" > /dev/null
  sudo -u "${RUN_USER}" -H \
    env PATH="/usr/local/bin:/usr/bin:/bin" \
    "${REPO_DIR}/.venv/bin/python" \
    vendor/QPress-SAM-Flake/scripts/merge_lora.py \
      --base "${BASE_PT}" \
      --lora "${LORA_PT}" \
      --config "${CONFIG_JSON}" \
      --out "${MERGED_PT}"
  popd > /dev/null
  ls -lh "${MERGED_PT}"
  stamp merge
fi

# --- Step 9: SHA256 + upload ---------------------------------------------
if ! done_stamp upload; then
  echo "[9/9] SHA256 + upload to S3"
  SHA_FULL=$(sha256sum "${MERGED_PT}" | awk '{print $1}')
  SHA_8="${SHA_FULL:0:8}"
  echo "SHA256=${SHA_FULL}"
  echo "SHA8=${SHA_8}"

  REMOTE_NAME="sam2.1_hiera_large.merged.${SHA_8}.pt"
  REMOTE_KEY="${S3_MERGED_PFX}${REMOTE_NAME}"
  REMOTE_SHA_KEY="${REMOTE_KEY}.sha256"

  # Two-line .sha256 file: "<hash>  <filename>" (sha256sum format) + extra blank
  SHA_FILE="${WEIGHTS_DIR}/${REMOTE_NAME}.sha256"
  printf '%s  %s\n' "${SHA_FULL}" "${REMOTE_NAME}" > "${SHA_FILE}"

  aws s3 cp "${MERGED_PT}" "s3://${S3_BUCKET}/${REMOTE_KEY}" \
    --region "${AWS_REGION}" --no-progress
  aws s3 cp "${SHA_FILE}" "s3://${S3_BUCKET}/${REMOTE_SHA_KEY}" \
    --region "${AWS_REGION}" --no-progress

  echo "UPLOADED: s3://${S3_BUCKET}/${REMOTE_KEY}"
  echo "UPLOADED: s3://${S3_BUCKET}/${REMOTE_SHA_KEY}"
  echo "${SHA_FULL}" > "${STATE_DIR}/last_merged_sha256"
  echo "${REMOTE_KEY}" > "${STATE_DIR}/last_merged_s3key"
  stamp upload
fi

echo "=== sam-gpu-bootstrap done: $(date -u +%FT%TZ) ==="
