#!/usr/bin/env bash
# sam-bake-ami-provision.sh — In-instance provisioning for the AMI builder.
#
# Runs as root via SSM RunCommand on a stock Ubuntu 22.04 g6.xlarge launched
# by sam-bake-ami.sh. Templated placeholders (@@VAR@@) are substituted by the
# orchestrator before this script is sent over the wire.
#
# Why a separate file (not an inline heredoc):
#   - Avoids bash heredoc-inside-$() quoting fragility (apostrophes in
#     comments tripping the outer parser).
#   - Independently shellcheckable.
#   - Reviewable as a normal shell script.
#
# Templated placeholders the orchestrator MUST substitute:
#   @@REPO_URL@@        upstream repo URL
#   @@REPO_REF@@        branch/ref name (for the manifest only — clone uses SHA)
#   @@REPO_SHA@@        explicit commit SHA the orchestrator resolved
#   @@VENDOR_SHA@@      vendor submodule SHA (for the manifest)
#   @@PY_VERSION@@      e.g. 3.11
#   @@GITHUB_PAT_SSM@@  SSM SecureString name (e.g. /qpress-sam/github_pat)
#   @@AWS_REGION@@      e.g. us-east-2
#   @@BAKE_TS@@         ISO-8601 UTC timestamp recorded in the manifest
#   @@BAKE_UUID@@       unique bake id (used for S3 log path)
#   @@S3_LOG_BUCKET@@   S3 bucket for persisted provision log (e.g. qpress-uploads)
#   @@S3_LOG_PREFIX@@   key prefix under bucket (e.g. internal/sam/bake-logs)
#
# The orchestrator passes this as a base64-encoded blob through SSM
# send-command; SSM RunCommand decodes + executes. Total wire size after
# substitution: ~5 KB (well under SSM's 64 KB document limit).

set -euo pipefail

# Persist the entire provisioning run to a local logfile. SSM RunCommand
# truncates StandardOutputContent at 24 KB, which previously hid the apt
# RCA on bake #223 attempt 4. The full log is mirrored to /var/log AND
# uploaded to S3 on both success and failure paths so the orchestrator can
# fetch the unredacted log.
PROVISION_LOG="/var/log/sam-bake-provision.log"
exec > >(tee -a "${PROVISION_LOG}") 2>&1
echo "=== sam-bake-ami-provision start: $(date -u +%FT%TZ) ==="

# --- Substituted by orchestrator -----------------------------------------
REPO_URL="@@REPO_URL@@"
REPO_REF="@@REPO_REF@@"
REPO_SHA="@@REPO_SHA@@"
VENDOR_SHA="@@VENDOR_SHA@@"
PY_VERSION="@@PY_VERSION@@"
GITHUB_PAT_SSM="@@GITHUB_PAT_SSM@@"
AWS_REGION="@@AWS_REGION@@"
BAKE_TS="@@BAKE_TS@@"
BAKE_UUID="@@BAKE_UUID@@"
S3_LOG_BUCKET="@@S3_LOG_BUCKET@@"
S3_LOG_PREFIX="@@S3_LOG_PREFIX@@"

# --- S3 log uploader (success AND failure paths) -------------------------
# Instance profile qpress-sam-gpu-role has s3:PutObject on
# arn:aws:s3:::qpress-uploads/internal/sam/* — confirmed pre-bake.
# This trap fires on script exit (any cause). Best-effort: do not let an
# upload failure mask the original exit code.
#
# Bake #227 RCA: stock Ubuntu 22.04 cloud image does NOT preinstall
# awscli. If step 1 (apt base) hasn't finished yet OR fails before this
# trap fires, `aws` is `command not found` and the upload silently
# disappears. So install awscli inline at the top of the script (before
# step 1), as a single self-contained apt install. If that itself fails,
# we still tee to /var/log on the instance — but the instance gets
# terminated, so we additionally try a snap install as a fallback inside
# the trap.
upload_provision_log() {
  local rc=$?
  local key="${S3_LOG_PREFIX}/${BAKE_UUID}/provision.log"
  # Use stderr for these diagnostics so they survive the upload pipeline.
  echo "=== sam-bake-ami-provision end: $(date -u +%FT%TZ) rc=${rc} ===" >&2
  if ! command -v aws >/dev/null 2>&1; then
    echo "[log] aws CLI not found; attempting emergency apt install..." >&2
    apt-get install -y --no-install-recommends awscli >&2 || \
      echo "[log] emergency apt install failed; log will not reach S3" >&2
  fi
  if command -v aws >/dev/null 2>&1 && [[ -n "${S3_LOG_BUCKET}" && -n "${BAKE_UUID}" ]]; then
    if aws s3 cp "${PROVISION_LOG}" "s3://${S3_LOG_BUCKET}/${key}" \
        --region "${AWS_REGION}" >&2; then
      echo "[log] uploaded to s3://${S3_LOG_BUCKET}/${key}" >&2
    else
      echo "[log] WARNING: S3 upload failed (rc unchanged=${rc})" >&2
    fi
  fi
  return "${rc}"
}
trap upload_provision_log EXIT

# Install awscli BEFORE step 1 (independent of step 1's apt install) so
# the trap always has a working aws binary even if step 1 itself hangs
# or aborts mid-stream.
echo "[bake 0/9] ensure awscli for S3 log shipping"
if ! command -v aws >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends awscli || true
fi
command -v aws >/dev/null 2>&1 || echo "WARNING: awscli still not available — S3 log shipping will fail" >&2

# --- Constants -----------------------------------------------------------
WORK_ROOT="/opt/sam"
REPO_DIR="${WORK_ROOT}/stand-alone-analyzer"
STATE_DIR="${WORK_ROOT}/state"
ENV_FILE="/etc/flake-analysis-worker.env"
INFO_FILE="/etc/flake-analysis-bootstrap-info.json"

export DEBIAN_FRONTEND=noninteractive
# SSM RunCommand executes as root with a minimal env; HOME is sometimes
# unset, which makes `git config --global` fail with "fatal: $HOME not set".
# Pin it to /root so global git config writes land predictably.
export HOME="${HOME:-/root}"

# --- Step 1: apt base packages (mirror userdata step 1) ------------------
echo "[bake 1/9] apt base packages"
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4
apt-get update -y
apt-get install -y --no-install-recommends \
  build-essential ca-certificates curl git gnupg jq \
  software-properties-common unzip wget gzip \
  postgresql-client

# --- Step 2: CUDA sanity check (DLAMI ships driver+toolkit) --------------
# Bake #228 RCA fix: We no longer install cuda-toolkit-12-4 / cuda-drivers
# at bake. The base AMI is now the AWS Deep Learning Base GPU AMI
# (Ubuntu 22.04), which AWS ships with a kernel/driver/toolkit triple
# they've already validated. Bake #227 proved that installing NVIDIA
# driver 610.43.02 from the cuda-keyring apt repo fails DKMS module build
# against Canonical's HWE kernel 6.8.0-1055-aws, so we delegate that
# concern to AWS.
#
# Sanity check: nvidia-smi must report at least 1 GPU, and nvcc must
# report a CUDA 12.x toolkit. If either fails, abort the provision —
# the EXIT trap will still upload the log to S3.
echo "[bake 2/9] CUDA sanity check (DLAMI-provided)"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "FATAL: nvidia-smi not found on DLAMI base — wrong AMI?" >&2
  exit 1
fi
if ! nvidia-smi >/dev/null 2>&1; then
  echo "FATAL: nvidia-smi runs but reports failure" >&2
  nvidia-smi || true
  exit 1
fi
GPU_COUNT="$(nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null | head -n1 || echo 0)"
if [[ -z "${GPU_COUNT}" || "${GPU_COUNT}" == "0" ]]; then
  echo "FATAL: nvidia-smi reports 0 GPUs (expected >=1 on g6.xlarge)" >&2
  nvidia-smi || true
  exit 1
fi
echo "[bake 2/9] nvidia-smi OK, GPU count=${GPU_COUNT}"

NVCC_BIN=""
if command -v nvcc >/dev/null 2>&1; then
  NVCC_BIN="$(command -v nvcc)"
elif [[ -x /usr/local/cuda/bin/nvcc ]]; then
  NVCC_BIN="/usr/local/cuda/bin/nvcc"
  export PATH="/usr/local/cuda/bin:${PATH}"
fi
if [[ -z "${NVCC_BIN}" ]]; then
  echo "FATAL: nvcc not found on DLAMI base (looked in PATH and /usr/local/cuda/bin)" >&2
  exit 1
fi
NVCC_CUDA_VER="$("${NVCC_BIN}" --version 2>/dev/null | awk -F, '/release/{print $2}' | awk '{print $2}')"
if [[ ! "${NVCC_CUDA_VER}" =~ ^12\. ]]; then
  echo "FATAL: nvcc reports CUDA ${NVCC_CUDA_VER:-unknown}, expected 12.x" >&2
  "${NVCC_BIN}" --version || true
  exit 1
fi
echo "[bake 2/9] nvcc OK, CUDA=${NVCC_CUDA_VER}"

# --- Step 3: Python 3.11 + uv --------------------------------------------
echo "[bake 3/9] Python ${PY_VERSION} + uv"
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y --no-install-recommends \
  "python${PY_VERSION}" "python${PY_VERSION}-dev" "python${PY_VERSION}-venv"
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
/usr/local/bin/uv --version

# --- Step 4: clone repo as ROOT, root-owned (RCA #221 §1 fix) ------------
# RCA #221 §1: if .git is owned by anyone other than the EUID, modern git
# refuses to operate (dubious-ownership abort). Fix at bake time by:
#   (a) cloning as root so .git is root-owned from inception
#   (b) baking safe.directory into root global gitconfig as belt-and-suspenders
echo "[bake 4/9] clone repo as root, root-owned"
mkdir -p "${WORK_ROOT}"
git config --global --add safe.directory "${REPO_DIR}"
git config --global --add safe.directory "${REPO_DIR}/vendor/QPress-SAM-Flake"

# Submodule auth: vendor repo is private. Pull a GitHub PAT from SSM
# SecureString. Instance profile (qpress-sam-gpu-role) has SSM read for
# /qpress-sam/* — the PAT never sits on the bastion or in the AMI.
GH_PAT=""
if GH_PAT_RAW="$(aws ssm get-parameter \
    --region "${AWS_REGION}" \
    --name "${GITHUB_PAT_SSM}" \
    --with-decryption \
    --query 'Parameter.Value' --output text 2>/dev/null)"; then
  GH_PAT="${GH_PAT_RAW}"
fi
if [[ -z "${GH_PAT}" ]]; then
  echo "WARNING: SSM ${GITHUB_PAT_SSM} not found — submodule clone may fail if vendor repo is private" >&2
fi

# Main repo (public). Pin to the exact SHA the bastion resolved.
git clone "${REPO_URL}" "${REPO_DIR}"
( cd "${REPO_DIR}" && git fetch --all --tags && git checkout "${REPO_SHA}" )

# Submodule init+update with PAT-injected URL if available.
#
# We pass the credential rewrite via `git -c` (in-process only) instead
# of writing to a config file. Reasons:
#   1. `--local` (in parent repo's .git/config) is NOT inherited by
#      submodule sub-invocations — bake #228 attempt 3 confirmed:
#      submodule clone still prompted for a username with --local set.
#   2. `--global` would persist the PAT to /root/.gitconfig which the
#      AMI snapshot would bake in. Security regression.
#   3. `-c url.X.insteadOf=Y` is process-scoped and inherited by git's
#      child processes (including submodule clones).
GH_CFG=()
if [[ -n "${GH_PAT}" ]]; then
  GH_REWRITE="https://x-access-token:${GH_PAT}@github.com/"
  GH_CFG=(-c "url.${GH_REWRITE}.insteadOf=https://github.com/")
fi
git ${GH_CFG[@]+"${GH_CFG[@]}"} -C "${REPO_DIR}" \
  submodule update --init --recursive vendor/QPress-SAM-Flake
unset GH_CFG GH_REWRITE

# Confirm root ownership end-to-end (defensive).
chown -R root:root "${WORK_ROOT}"

# --- Step 5: uv sync + vendor inference deps + peft (RCA #221 §7 fix) ----
# peft must be installed at bake. We install as root (matches bake-time
# ownership). On first userdata boot, userdata re-runs uv sync because
# deps.done is absent at bake — that re-run is idempotent and peft survives.
echo "[bake 5/9] uv sync + vendor inference deps + peft"
pushd "${REPO_DIR}" >/dev/null
/usr/local/bin/uv sync --frozen --python "python${PY_VERSION}"
/usr/local/bin/uv pip install \
  --python "${REPO_DIR}/.venv/bin/python" \
  --index-strategy unsafe-best-match \
  --extra-index-url https://download.pytorch.org/whl/cu124 \
  -r vendor/QPress-SAM-Flake/requirements-inference.txt
/usr/local/bin/uv pip install \
  --python "${REPO_DIR}/.venv/bin/python" \
  "peft>=0.8.0,<0.20"
popd >/dev/null

# --- Step 6: validate Python imports BEFORE we snapshot ------------------
echo "[bake 6/9] validate import surface"
"${REPO_DIR}/.venv/bin/python" - <<'PYCHK'
import peft, torch, sqlalchemy, asyncpg, psycopg, procrastinate
print("peft", peft.__version__)
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
PYCHK

# --- Step 7: capture bootstrap-info manifest -----------------------------
echo "[bake 7/9] write ${INFO_FILE}"
PEFT_VER="$("${REPO_DIR}/.venv/bin/python" -c 'import peft;print(peft.__version__)')"
TORCH_VER="$("${REPO_DIR}/.venv/bin/python" -c 'import torch;print(torch.__version__)')"
CUDA_VER="$(/usr/local/cuda/bin/nvcc --version 2>/dev/null | awk -F, '/release/{print $2}' | awk '{print $2}' || echo 'unknown')"
DRIVER_VER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 || echo 'unknown')"

cat > "${INFO_FILE}" <<JSON
{
  "baked_at_utc": "${BAKE_TS}",
  "baked_from_repo": "${REPO_URL}",
  "baked_from_ref": "${REPO_REF}",
  "baked_from_sha": "${REPO_SHA}",
  "baked_vendor_sha": "${VENDOR_SHA}",
  "peft_version": "${PEFT_VER}",
  "torch_version": "${TORCH_VER}",
  "cuda_version": "${CUDA_VER}",
  "nvidia_driver_version": "${DRIVER_VER}",
  "builder": "scripts/aws/sam-bake-ami.sh",
  "rca_fix": "#221"
}
JSON
chmod 0644 "${INFO_FILE}"
cat "${INFO_FILE}"

# --- Step 8: scrub all transient state (RCA #221 §3, §4 fixes) -----------
# Userdata must see a clean slate so its done-stamps reflect THIS instance
# work, not the builder.
echo "[bake 8/9] scrub transient state"
# §3 — empty state dir; userdata re-creates as needed
rm -rf "${STATE_DIR}"
# §4 — no env file; userdata writes from SSM on first boot
rm -f "${ENV_FILE}"
# Cloud-init seed cleanup so cloud-init treats first boot as fresh
cloud-init clean --logs --seed || true
# Machine-id reset so log streams attribute correctly per instance
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
# SSH host keys regenerated on first boot
rm -f /etc/ssh/ssh_host_*
# History hygiene — never bake a shell history into a snapshot
rm -f /root/.bash_history /home/ubuntu/.bash_history
apt-get clean
rm -rf /var/lib/apt/lists/*
# Drop credential helpers (just-in-case — already unset above)
rm -f /root/.git-credentials
unset GH_PAT GH_REWRITE GH_PAT_RAW

echo "[bake 9/9] provision complete: $(date -u +%FT%TZ)"

# Force kernel page cache flush before AMI snapshot. With --no-reboot
# create-image AWS takes a crash-consistent (not quiesced) snapshot, so
# any unflushed writes from steps 7-8 (notably /etc/flake-analysis-
# bootstrap-info.json) can land 0-byte on the AMI even though the
# in-instance filesystem shows them populated. Bake #228 attempt 8
# verified this — the JSON content was correct in /var/log but the
# resulting AMI had a 0-byte manifest.
sync
sync
echo "[bake 9/9] sync complete; safe to snapshot"
