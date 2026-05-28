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
#
# The orchestrator passes this as a base64-encoded blob through SSM
# send-command; SSM RunCommand decodes + executes. Total wire size after
# substitution: ~5 KB (well under SSM's 64 KB document limit).

set -euo pipefail
exec > >(tee -a /var/log/sam-bake-ami-provision.log) 2>&1
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

# --- Constants -----------------------------------------------------------
WORK_ROOT="/opt/sam"
REPO_DIR="${WORK_ROOT}/stand-alone-analyzer"
STATE_DIR="${WORK_ROOT}/state"
ENV_FILE="/etc/flake-analysis-worker.env"
INFO_FILE="/etc/flake-analysis-bootstrap-info.json"

export DEBIAN_FRONTEND=noninteractive

# --- Step 1: apt base packages (mirror userdata step 1) ------------------
echo "[bake 1/9] apt base packages"
mkdir -p /etc/apt/apt.conf.d
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4
apt-get update -y
apt-get install -y --no-install-recommends \
  build-essential ca-certificates curl git gnupg jq \
  software-properties-common unzip wget gzip \
  postgresql-client

# --- Step 2: NVIDIA driver + CUDA 12.4 -----------------------------------
echo "[bake 2/9] CUDA 12.4 toolkit + driver"
wget -qO /tmp/cuda-keyring.deb \
  https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i /tmp/cuda-keyring.deb
apt-get update -y
apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers
apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true

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
GH_REWRITE=""
if [[ -n "${GH_PAT}" ]]; then
  GH_REWRITE="https://x-access-token:${GH_PAT}@github.com/"
  git -C "${REPO_DIR}" config --local "url.${GH_REWRITE}.insteadOf" "https://github.com/"
fi
git -C "${REPO_DIR}" submodule update --init --recursive vendor/QPress-SAM-Flake
# Strip the credential rewrite so the cloned config carries no PAT into AMI.
if [[ -n "${GH_REWRITE}" ]]; then
  git -C "${REPO_DIR}" config --local --unset-all "url.${GH_REWRITE}.insteadOf" 2>/dev/null || true
fi

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
