#!/usr/bin/env bash
# sam-bake-ami.sh — Reproducible AMI builder for the qpress-sam GPU worker.
#
# Authored in response to RCA #221 (claudedocs/sam-211-rca.md), which found
# that the production AMI ami-0b7ec5ff47a1eff11 was hand-baked on
# 2026-05-28T02:06Z with no script in repo, no audit trail, and 4 BLOCKER
# mismatches against the current sam-gpu-worker-userdata.sh.
#
# This script bakes a NEW AMI from stock Ubuntu 22.04 such that, when a fresh
# instance launches with sam-gpu-worker-userdata.sh as its user-data, every
# step that should be skipped on second-boot is skipped legitimately (i.e.
# the work was actually done at bake time and stamped at userdata-time on
# the real instance) — never via a baked-in done_stamp that lies about state
# on a different machine.
#
# === Approach: launch + provision + create-image + terminate =============
# 1. Launch a small g6.xlarge spot instance from AWS Deep Learning Base
#    GPU AMI (Ubuntu 22.04) — NVIDIA driver + CUDA preinstalled by AWS
#    against a kernel they've validated (#228 RCA fix; replaces stock
#    Canonical Ubuntu 22.04 which had a kernel/driver DKMS mismatch).
# 2. Provision via SSM RunCommand using sam-bake-ami-provision.sh
#    (templated, lives next to this script). Installs apt base,
#    Python 3.11, uv, repo + submodule (root-owned, safe.directory baked),
#    uv sync worker deps + vendor inference deps + peft. Scrubs state.
#    CUDA install is NO LONGER part of the bake — the DLAMI already has it.
# 3. Stop, create-image (no-reboot), wait for state=available, terminate
#    builder.
# 4. Self-validate the resulting AMI (spot a t3.small from it, run SSM
#    checks, terminate). Tag Status=ready or Status=validation-failed.
#
# === Hard constraints ====================================================
# - Idempotent: same-day re-runs produce distinct AMIs without conflict.
#   Each AMI gets a UTC-second timestamp in its Name and a unique BakeUUID
#   tag. Multiple runs do NOT clobber.
# - Pre-flight: terminates orphan instances tagged Purpose=ami-bake-#222
#   from prior runs. Prevents zombie spend.
# - Self-test: if validation fails, AMI is preserved (not deregistered) but
#   tagged Status=validation-failed and the script exits non-zero.
# - Cost: builder ~$1.20/hr g6.xlarge spot x ~30 min ~= $0.60. Validator
#   t3.small ~$0.02/hr x 5 min ~= negligible. Plus ~$0.20 EBS snapshot.
#   RCA estimated ~$3 total — confirmed.
#
# === Tunables (override via env) =========================================
#   AWS_REGION         default: us-east-2
#   AWS_PROFILE        default: qpress
#   REPO_URL           default: HoukJangBNL fork (matches userdata)
#   REPO_REF           default: feat/migration-cutover  (override via --ref)
#   BUILDER_TYPE       default: g6.xlarge
#   VALIDATOR_TYPE     default: t3.small
#   SUBNET_ID          default: discovered from VPC + AZ us-east-2a
#   SG_NAME            default: qpress-sam-gpu-sg
#   ROLE_NAME          default: qpress-sam-gpu-role
#   GITHUB_PAT_SSM     default: /qpress-sam/github_pat  (only consumed
#                                                        in-instance via
#                                                        SSM, never on
#                                                        bastion or AMI)
#   AMI_NAME_PREFIX    default: qpress-saa-sam-warmup
#   PURPOSE_TAG        default: ami-bake-#222
#
# === Flags ==============================================================
#   --ref <git-ref>       override REPO_REF (default: feat/migration-cutover HEAD)
#   --skip-validation     skip post-bake validation launch (NOT recommended)
#   --keep-builder        on bake failure, keep the builder for forensics
#   --no-fallback         disable spot->on-demand auto-fallback (default ON).
#                         With fallback ON: try spot in 2a/2b/2c; on capacity
#                         exhaustion across all AZs, retry once per AZ as
#                         on-demand. Estimated cost ceiling: g6.xlarge
#                         on-demand ~$0.91/hr; bake budget ~$2 (<2.2 hr).
#   --dry-run             resolve all inputs, print plan, exit 0 (no AWS writes)
#   -h / --help           this help
#
# === Outputs =============================================================
# stdout:
#   final AMI ID + summary block (timestamp, repo SHA, vendor SHA, etc.)
# in-AMI:
#   /etc/flake-analysis-bootstrap-info.json with manifest (baked_at_utc,
#   baked_from_sha, baked_vendor_sha, peft/torch/CUDA/driver versions)
# AMI tags:
#   Project=qpress-sam, Phase=P4.4, BakedFrom=<sha8>, BakedAt=<ISO-utc>,
#   Builder=sam-bake-ami.sh, RCAFix=#221, Status=ready|validation-failed
#
# === RCA #221 BLOCKER fixes addressed ===================================
# §1 (Repo state)   — repo cloned as root, .git root-owned, safe.directory
#                     baked into root global gitconfig.
# §3 (State stamps) — STATE_DIR scrubbed at end of provisioning. No
#                     done-stamps baked. Userdata creates them on first boot.
# §4 (Env file)     — /etc/flake-analysis-worker.env NOT created at bake.
#                     Userdata writes it from SSM on first boot.
# §7 (peft missing) — peft installed at bake (same incantation as userdata).
# =========================================================================

set -euo pipefail

# --- Defaults ------------------------------------------------------------
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_PROFILE="${AWS_PROFILE:-qpress}"
REPO_URL="${REPO_URL:-https://github.com/HoukJangBNL/stand-alone-analyzer.git}"
REPO_REF_DEFAULT="feat/migration-cutover"
BUILDER_TYPE="${BUILDER_TYPE:-g6.xlarge}"
VALIDATOR_TYPE="${VALIDATOR_TYPE:-t3.small}"
SG_NAME="${SG_NAME:-qpress-sam-gpu-sg}"
ROLE_NAME="${ROLE_NAME:-qpress-sam-gpu-role}"
GITHUB_PAT_SSM="${GITHUB_PAT_SSM:-/qpress-sam/github_pat}"
AMI_NAME_PREFIX="${AMI_NAME_PREFIX:-qpress-saa-sam-warmup}"
PURPOSE_TAG="${PURPOSE_TAG:-ami-bake-#222}"
PY_VERSION="${PY_VERSION:-3.11}"

REPO_REF=""
SKIP_VALIDATION=0
KEEP_BUILDER=0
DRY_RUN=0
NO_FALLBACK=0
S3_LOG_BUCKET="${S3_LOG_BUCKET:-qpress-uploads}"
S3_LOG_PREFIX="${S3_LOG_PREFIX:-internal/sam/bake-logs}"

aws_() { aws --profile "${AWS_PROFILE}" --region "${AWS_REGION}" "$@"; }
log()  { printf '[sam-bake-ami] %s\n' "$*" >&2; }
fail() { log "FATAL: $*"; exit 1; }

usage() {
  sed -n '2,75p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- Parse flags ---------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)              REPO_REF="${2:?--ref needs a git ref}"; shift 2 ;;
    --skip-validation)  SKIP_VALIDATION=1; shift ;;
    --keep-builder)     KEEP_BUILDER=1; shift ;;
    --no-fallback)      NO_FALLBACK=1; shift ;;
    --dry-run)          DRY_RUN=1; shift ;;
    -h|--help)          usage; exit 0 ;;
    *)                  fail "unknown flag: $1 (see --help)" ;;
  esac
done

# --- Resolve REPO_REF + commit SHA ---------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROVISION_TEMPLATE="${SCRIPT_DIR}/sam-bake-ami-provision.sh"

if [[ ! -f "${PROVISION_TEMPLATE}" ]]; then
  fail "missing companion script: ${PROVISION_TEMPLATE}"
fi

if [[ -z "${REPO_REF}" ]]; then
  REPO_REF="${REPO_REF_DEFAULT}"
fi

if ! REPO_SHA="$(git -C "${REPO_ROOT}" rev-parse "${REPO_REF}" 2>/dev/null)"; then
  fail "cannot resolve git ref ${REPO_REF} in ${REPO_ROOT}"
fi
REPO_SHA8="${REPO_SHA:0:8}"

VENDOR_LINE="$(git -C "${REPO_ROOT}" ls-tree "${REPO_SHA}" vendor/QPress-SAM-Flake 2>/dev/null || true)"
VENDOR_SHA="$(echo "${VENDOR_LINE}" | awk '{print $3}')"
if [[ -z "${VENDOR_SHA}" ]]; then
  fail "cannot resolve vendor/QPress-SAM-Flake gitlink at ${REPO_SHA8}"
fi
VENDOR_SHA8="${VENDOR_SHA:0:8}"

BAKE_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BAKE_TS_COMPACT="$(date -u +%Y-%m-%dT%H%M%SZ)"
BAKE_UUID="$(date -u +%s)-$$"
AMI_NAME="${AMI_NAME_PREFIX}-${BAKE_TS_COMPACT}-${REPO_SHA8}"

log "REPO_REF        = ${REPO_REF}"
log "REPO_SHA        = ${REPO_SHA}"
log "VENDOR_SHA      = ${VENDOR_SHA}"
log "BAKE_TS         = ${BAKE_TS}"
log "BAKE_UUID       = ${BAKE_UUID}"
log "AMI_NAME        = ${AMI_NAME}"
log "BUILDER_TYPE    = ${BUILDER_TYPE}"
log "PURPOSE_TAG     = ${PURPOSE_TAG}"

# --- Render the provisioning script with placeholder substitution --------
# Replace @@VAR@@ tokens. Use a helper that rejects values containing the
# literal substitution sigil (defensive against weird PATs / refs).
render_provision() {
  local tpl
  tpl="$(cat "${PROVISION_TEMPLATE}")"
  for pair in \
    "REPO_URL=${REPO_URL}" \
    "REPO_REF=${REPO_REF}" \
    "REPO_SHA=${REPO_SHA}" \
    "VENDOR_SHA=${VENDOR_SHA}" \
    "PY_VERSION=${PY_VERSION}" \
    "GITHUB_PAT_SSM=${GITHUB_PAT_SSM}" \
    "AWS_REGION=${AWS_REGION}" \
    "BAKE_TS=${BAKE_TS}" \
    "BAKE_UUID=${BAKE_UUID}" \
    "S3_LOG_BUCKET=${S3_LOG_BUCKET}" \
    "S3_LOG_PREFIX=${S3_LOG_PREFIX}"; do
    local k="${pair%%=*}"
    local v="${pair#*=}"
    if [[ "${v}" == *"@@"* ]]; then
      fail "value for ${k} contains '@@' sigil — refusing to substitute"
    fi
    # Pure bash substitution avoids escaping headaches that would plague
    # sed (URLs contain slashes; SHAs are plain but PATHs may contain /).
    tpl="${tpl//@@${k}@@/${v}}"
  done
  printf '%s' "${tpl}"
}

PROVISION_SCRIPT="$(render_provision)"

# Sanity: no unsubstituted KNOWN keys left. (We don't reject every @@*@@
# match because the template uses literal @@VAR@@ in its own doc-comments
# as a meta-example.)
for k in REPO_URL REPO_REF REPO_SHA VENDOR_SHA PY_VERSION GITHUB_PAT_SSM AWS_REGION BAKE_TS BAKE_UUID S3_LOG_BUCKET S3_LOG_PREFIX; do
  if printf '%s' "${PROVISION_SCRIPT}" | grep -q "@@${k}@@"; then
    fail "provisioning template still contains unsubstituted @@${k}@@"
  fi
done

# --- Resolve base AMI ---------------------------------------------------
# Bake #228 RCA-driven switch: stock Ubuntu 22.04 cloud image (Canonical
# 099720109477) ships kernel 6.8.0-1055-aws, against which NVIDIA driver
# 610.43.02 from the cuda-keyring apt repo fails DKMS module build (#227
# log lines 1379-1383). Switch base to the AWS Deep Learning Base GPU AMI
# (Ubuntu 22.04), which ships a kernel/driver pair AWS has already
# validated. We pick the OSS Nvidia Driver variant for licensing.
#
# AMI ID is resolved dynamically (no hardcode) so future re-bakes
# automatically pick up newer DLAMI publishes. Override the name pattern
# via BASE_AMI_NAME_PATTERN if AWS renames the family in the future.
BASE_AMI_NAME_PATTERN="${BASE_AMI_NAME_PATTERN:-Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*}"
log "[resolve] latest DLAMI matching '${BASE_AMI_NAME_PATTERN}' in ${AWS_REGION}"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  BASE_AMI="ami-DRYRUN-base"
  BASE_AMI_NAME="DRYRUN-DLAMI"
  BASE_AMI_DATE="DRYRUN"
else
  BASE_INFO="$(aws_ ec2 describe-images \
    --owners amazon \
    --filters \
      "Name=name,Values=${BASE_AMI_NAME_PATTERN}" \
      'Name=state,Values=available' \
      'Name=architecture,Values=x86_64' \
      'Name=root-device-type,Values=ebs' \
    --query 'sort_by(Images, &CreationDate)[-1].[ImageId,Name,CreationDate]' \
    --output text)"
  BASE_AMI="$(echo "${BASE_INFO}" | awk '{print $1}')"
  BASE_AMI_NAME="$(echo "${BASE_INFO}" | awk '{$1=""; $NF=""; sub(/^[ \t]+/,""); sub(/[ \t]+$/,""); print}')"
  BASE_AMI_DATE="$(echo "${BASE_INFO}" | awk '{print $NF}')"
  if [[ -z "${BASE_AMI}" || "${BASE_AMI}" == "None" ]]; then
    fail "could not resolve DLAMI matching '${BASE_AMI_NAME_PATTERN}'"
  fi
fi
log "BASE_AMI        = ${BASE_AMI}"
log "BASE_AMI_NAME   = ${BASE_AMI_NAME}"
log "BASE_AMI_DATE   = ${BASE_AMI_DATE}"

# --- Resolve SG + subnets (multi-AZ) + instance profile ------------------
# Bake #223 RCA: g6.xlarge spot capacity rotates between us-east-2a/2b/2c
# within seconds. A single-AZ retry strategy never converges under drought.
# Resolve a public subnet in EACH AZ up-front; the launch loop iterates
# them in order and falls back to on-demand if all spot capacity exhausts.
#
# SUBNET_ID env override (single-subnet mode): if the operator pins a
# subnet, that subnet wins and fallback is restricted to that AZ only.
SUBNETS=()  # ordered list of "az subnet-id" pairs
if [[ "${DRY_RUN}" -eq 1 ]]; then
  VPC_ID="vpc-DRYRUN"
  SG_ID="sg-DRYRUN"
  SUBNETS=("us-east-2a subnet-DRYRUN-2a" "us-east-2b subnet-DRYRUN-2b" "us-east-2c subnet-DRYRUN-2c")
else
  SG_LOOKUP="$(aws_ ec2 describe-security-groups \
    --filters "Name=group-name,Values=${SG_NAME}" \
    --query 'SecurityGroups[0].[GroupId,VpcId]' --output text 2>/dev/null || echo "None None")"
  SG_ID="$(echo "${SG_LOOKUP}" | awk '{print $1}')"
  VPC_ID="$(echo "${SG_LOOKUP}" | awk '{print $2}')"
  if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
    fail "security group ${SG_NAME} not found"
  fi

  if [[ -n "${SUBNET_ID:-}" ]]; then
    # Operator-pinned subnet — discover its AZ for logging/tagging.
    OVERRIDE_AZ="$(aws_ ec2 describe-subnets \
      --subnet-ids "${SUBNET_ID}" \
      --query 'Subnets[0].AvailabilityZone' --output text 2>/dev/null || echo "unknown")"
    SUBNETS=("${OVERRIDE_AZ} ${SUBNET_ID}")
  else
    for az in us-east-2a us-east-2b us-east-2c; do
      sn="$(aws_ ec2 describe-subnets \
        --filters \
          "Name=vpc-id,Values=${VPC_ID}" \
          "Name=availability-zone,Values=${az}" \
          "Name=map-public-ip-on-launch,Values=true" \
        --query 'Subnets[0].SubnetId' --output text)"
      if [[ -n "${sn}" && "${sn}" != "None" ]]; then
        SUBNETS+=("${az} ${sn}")
      fi
    done
    if [[ ${#SUBNETS[@]} -eq 0 ]]; then
      fail "no public subnets discovered across us-east-2{a,b,c} in ${VPC_ID}"
    fi
  fi
fi
log "VPC_ID          = ${VPC_ID}"
log "SG_ID           = ${SG_ID}"
log "ROLE_NAME       = ${ROLE_NAME}"
log "SUBNETS         = ${SUBNETS[*]}"
if [[ ${NO_FALLBACK} -eq 1 ]]; then
  log "FALLBACK        = disabled (--no-fallback) — spot only, single attempt per AZ"
else
  log "FALLBACK        = enabled — spot[2a,2b,2c] then on-demand[2a,2b,2c]"
fi

# --- Pre-flight: terminate orphan ami-bake-#222 instances ----------------
# A previous failed run may have left a builder behind. Find anything
# tagged Purpose=${PURPOSE_TAG} in a pre-terminated state and shoot it.
log "[pre-flight] sweeping orphan instances tagged Purpose=${PURPOSE_TAG}"
if [[ "${DRY_RUN}" -eq 0 ]]; then
  ORPHANS="$(aws_ ec2 describe-instances \
    --filters \
      "Name=tag:Purpose,Values=${PURPOSE_TAG}" \
      'Name=instance-state-name,Values=pending,running,stopping,stopped' \
    --query 'Reservations[].Instances[].InstanceId' --output text)"
  if [[ -n "${ORPHANS}" && "${ORPHANS}" != "None" ]]; then
    log "[pre-flight] terminating orphans: ${ORPHANS}"
    # shellcheck disable=SC2086 # word-split is intentional — multi-id list
    aws_ ec2 terminate-instances --instance-ids ${ORPHANS} >/dev/null
  else
    log "[pre-flight] no orphans"
  fi
fi

# --- Validation script (runs on validator t3.small from the new AMI) -----
# This is short enough to inline; quoted heredoc keeps it free of bastion-
# side substitution gotchas.
VALIDATE_SCRIPT="$(cat <<'VALIDEOF'
#!/usr/bin/env bash
set -euo pipefail
INFO_FILE="/etc/flake-analysis-bootstrap-info.json"
REPO_DIR="/opt/sam/stand-alone-analyzer"
STATE_DIR="/opt/sam/state"
ENV_FILE="/etc/flake-analysis-worker.env"

echo "=== validate ==="

# A. manifest exists + non-empty + has required fields
test -s "${INFO_FILE}" || { echo "FAIL: missing ${INFO_FILE}"; exit 1; }
jq -e '.baked_from_sha and .peft_version and .torch_version' "${INFO_FILE}" >/dev/null \
  || { echo "FAIL: manifest missing required fields"; cat "${INFO_FILE}"; exit 1; }
cat "${INFO_FILE}"

# B. NO baked done-stamps (RCA #221 §3)
if [[ -d "${STATE_DIR}" ]]; then
  STAMPS="$(find "${STATE_DIR}" -name '*.done' -type f 2>/dev/null || true)"
  if [[ -n "${STAMPS}" ]]; then
    echo "FAIL: baked done-stamps present (RCA §3 violation):"
    echo "${STAMPS}"
    exit 1
  fi
fi

# C. NO baked env-file (RCA #221 §4)
if [[ -e "${ENV_FILE}" ]]; then
  echo "FAIL: ${ENV_FILE} should not be baked into AMI"
  exit 1
fi

# D. .git root-owned (RCA #221 §1)
GIT_OWNER="$(stat -c '%U' "${REPO_DIR}/.git")"
if [[ "${GIT_OWNER}" != "root" ]]; then
  echo "FAIL: ${REPO_DIR}/.git owner=${GIT_OWNER}, expected root"
  exit 1
fi

# E. peft importable (RCA #221 §7)
"${REPO_DIR}/.venv/bin/python" -c "import peft; print(peft.__version__)"

# F. vendor submodule populated
test -s "${REPO_DIR}/vendor/QPress-SAM-Flake/run_amg_v2.py" \
  || { echo "FAIL: vendor submodule not populated"; exit 1; }

echo "=== validate OK: SAM-BAKE-VALIDATE-PASS ==="
VALIDEOF
)"

# --- Dry-run early exit --------------------------------------------------
if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "[dry-run] would launch ${BUILDER_TYPE} from ${BASE_AMI}"
  log "[dry-run] provisioning script $(printf '%s' "${PROVISION_SCRIPT}" | wc -l | tr -d ' ') lines, $(printf '%s' "${PROVISION_SCRIPT}" | wc -c | tr -d ' ') bytes"
  log "[dry-run] would create AMI named ${AMI_NAME}"
  log "[dry-run] would validate via ${VALIDATOR_TYPE}"
  log "[dry-run] decision tree:"
  if [[ ${NO_FALLBACK} -eq 1 ]]; then
    for entry in "${SUBNETS[@]}"; do
      az="${entry%% *}"; sn="${entry#* }"
      log "[dry-run]   1. spot   ${BUILDER_TYPE} in ${az} (${sn}) — fail-fast, no fallback"
    done
  else
    step=1
    for entry in "${SUBNETS[@]}"; do
      az="${entry%% *}"; sn="${entry#* }"
      log "[dry-run]   ${step}. spot   ${BUILDER_TYPE} in ${az} (${sn})"
      step=$((step+1))
    done
    for entry in "${SUBNETS[@]}"; do
      az="${entry%% *}"; sn="${entry#* }"
      log "[dry-run]   ${step}. on-demand ${BUILDER_TYPE} in ${az} (${sn})  [~\$0.91/hr cap ~\$2 = 2.2 hr]"
      step=$((step+1))
    done
  fi
  log "[dry-run] complete (no AWS state changes)"
  exit 0
fi

# --- try_launch_builder: one (subnet, market) RunInstances attempt -------
# Returns 0 with BUILDER_ID set on success.
# Returns 10 (capacity error) if AWS reports
#   InsufficientInstanceCapacity / SpotMaxPriceTooLow / MaxSpotInstanceCountExceeded
#   — caller should try next AZ or fall back to on-demand.
# Returns 1 on any other RunInstances error — caller must abort.
LAUNCH_ERR_FILE=""
try_launch_builder() {
  local market="$1" sn="$2" az="$3"
  local market_args=()
  if [[ "${market}" == "spot" ]]; then
    market_args=(--instance-market-options 'MarketType=spot')
  fi
  local tag_spec
  tag_spec="ResourceType=instance,Tags=[\
{Key=Project,Value=qpress-sam},\
{Key=Purpose,Value=${PURPOSE_TAG}},\
{Key=BakeUUID,Value=${BAKE_UUID}},\
{Key=Market,Value=${market}},\
{Key=AvailabilityZone,Value=${az}},\
{Key=Name,Value=${AMI_NAME_PREFIX}-builder-${BAKE_TS_COMPACT}}]"

  LAUNCH_ERR_FILE="$(mktemp)"
  # bash 3.2 (macOS default) errors on "${empty_array[@]}" under set -u.
  # Use ${var[@]+...} expansion to guard the empty-array case.
  if BUILDER_ID="$(aws_ ec2 run-instances \
      --image-id "${BASE_AMI}" \
      --instance-type "${BUILDER_TYPE}" \
      ${market_args[@]+"${market_args[@]}"} \
      --iam-instance-profile "Name=${ROLE_NAME}" \
      --network-interfaces "DeviceIndex=0,AssociatePublicIpAddress=true,Groups=${SG_ID},SubnetId=${sn}" \
      --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3,DeleteOnTermination=true}' \
      --metadata-options 'HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled' \
      --tag-specifications "${tag_spec}" \
      --query 'Instances[0].InstanceId' \
      --output text 2>"${LAUNCH_ERR_FILE}")"; then
    rm -f "${LAUNCH_ERR_FILE}"
    return 0
  fi
  local err
  err="$(cat "${LAUNCH_ERR_FILE}")"
  rm -f "${LAUNCH_ERR_FILE}"
  BUILDER_ID=""
  if echo "${err}" | grep -qE 'InsufficientInstanceCapacity|SpotMaxPriceTooLow|MaxSpotInstanceCountExceeded|Unsupported.*spot'; then
    log "[launch] ${market} in ${az}: capacity error — $(echo "${err}" | head -n1)"
    return 10
  fi
  log "[launch] ${market} in ${az}: non-capacity error — ${err}"
  return 1
}

# Iterate spot[2a,2b,2c] -> on-demand[2a,2b,2c] (capped by --no-fallback).
BUILDER_ID=""
BUILDER_MARKET=""
BUILDER_AZ=""
log "[launch] start: ${BUILDER_TYPE} from ${BASE_AMI}"
for entry in "${SUBNETS[@]}"; do
  az="${entry%% *}"; sn="${entry#* }"
  log "[launch] try spot in ${az} (${sn})"
  rc=0
  try_launch_builder spot "${sn}" "${az}" || rc=$?
  case "${rc}" in
    0)  BUILDER_MARKET=spot; BUILDER_AZ="${az}"; break ;;
    10) continue ;;
    *)  fail "spot launch in ${az} failed with non-capacity error (see above)" ;;
  esac
done

if [[ -z "${BUILDER_ID}" ]]; then
  if [[ ${NO_FALLBACK} -eq 1 ]]; then
    fail "all spot launches exhausted across ${#SUBNETS[@]} AZ(s); --no-fallback set, refusing on-demand. Status=ALL-AZ-EXHAUSTED"
  fi
  log "[launch] all spot AZs exhausted — falling back to on-demand (g6.xlarge ~\$0.91/hr; budget cap ~\$2)"
  for entry in "${SUBNETS[@]}"; do
    az="${entry%% *}"; sn="${entry#* }"
    log "[launch] try on-demand in ${az} (${sn})"
    rc=0
    try_launch_builder on-demand "${sn}" "${az}" || rc=$?
    case "${rc}" in
      0)  BUILDER_MARKET=on-demand; BUILDER_AZ="${az}"; break ;;
      10) continue ;;
      *)  fail "on-demand launch in ${az} failed with non-capacity error (see above)" ;;
    esac
  done
fi

if [[ -z "${BUILDER_ID}" ]]; then
  fail "all spot AND on-demand launches exhausted across ${#SUBNETS[@]} AZ(s). Status=ALL-AZ-EXHAUSTED"
fi
log "BUILDER_ID      = ${BUILDER_ID}"
log "BUILDER_MARKET  = ${BUILDER_MARKET}"
log "BUILDER_AZ      = ${BUILDER_AZ}"

# Cleanup trap — if anything below fails, terminate the builder unless
# --keep-builder.
# shellcheck disable=SC2329 # invoked indirectly via trap
cleanup_builder() {
  local rc=$?
  if [[ ${rc} -ne 0 && ${KEEP_BUILDER} -eq 1 ]]; then
    log "[cleanup] --keep-builder set; leaving ${BUILDER_ID} in place for forensics (rc=${rc})"
    return "${rc}"
  fi
  if [[ -n "${BUILDER_ID:-}" ]]; then
    log "[cleanup] terminating builder ${BUILDER_ID}"
    aws_ ec2 terminate-instances --instance-ids "${BUILDER_ID}" >/dev/null 2>&1 || true
  fi
  return "${rc}"
}
trap cleanup_builder EXIT

log "[wait] builder running + SSM-online"
aws_ ec2 wait instance-running --instance-ids "${BUILDER_ID}"

PING_STATUS="None"
for _i in $(seq 1 30); do
  PING_STATUS="$(aws_ ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${BUILDER_ID}" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "None")"
  if [[ "${PING_STATUS}" == "Online" ]]; then
    break
  fi
  sleep 10
done
[[ "${PING_STATUS}" == "Online" ]] || fail "builder ${BUILDER_ID} did not register with SSM within 5 min"

# --- Run the provisioning payload via SSM --------------------------------
# Send as base64 to avoid quoting nightmares. With DLAMI base (#228), CUDA
# is preinstalled and the bake is dominated by uv sync + torch wheels;
# typical 12-20 min. SSM hard timeout kept at 5400 s (90 min) headroom.
log "[ssm] sending provisioning payload"
PROVISION_B64="$(printf '%s' "${PROVISION_SCRIPT}" | base64 | tr -d '\n')"
SSM_PARAMS_FILE="$(mktemp)"
trap 'rm -f "${SSM_PARAMS_FILE}"; cleanup_builder' EXIT
cat > "${SSM_PARAMS_FILE}" <<JSON
{
  "commands": [
    "set -e",
    "echo '${PROVISION_B64}' | base64 -d > /tmp/sam-bake-provision.sh",
    "chmod +x /tmp/sam-bake-provision.sh",
    "bash /tmp/sam-bake-provision.sh"
  ],
  "executionTimeout": ["5400"]
}
JSON

PROVISION_CMD_ID="$(aws_ ssm send-command \
  --instance-ids "${BUILDER_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "sam-bake-ami provision (${BAKE_UUID})" \
  --timeout-seconds 5400 \
  --parameters "file://${SSM_PARAMS_FILE}" \
  --query 'Command.CommandId' --output text)"
log "PROVISION_CMD   = ${PROVISION_CMD_ID}"

log "[wait] provisioning... (typical 25-40 min, hard cap 90 min)"
PROV_STATUS="Pending"
for _i in $(seq 1 180); do
  PROV_STATUS="$(aws_ ssm get-command-invocation \
    --command-id "${PROVISION_CMD_ID}" \
    --instance-id "${BUILDER_ID}" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")"
  case "${PROV_STATUS}" in
    Success)
      log "[provision] Success"
      break
      ;;
    Cancelled|Failed|TimedOut|Cancelling)
      aws_ ssm get-command-invocation \
        --command-id "${PROVISION_CMD_ID}" \
        --instance-id "${BUILDER_ID}" \
        --query '[Status,StandardErrorContent]' --output text >&2 || true
      break
      ;;
    *) sleep 30 ;;
  esac
done

# Pull the persisted provision log from S3 regardless of success/failure.
# The provision script's EXIT trap uploads /var/log/sam-bake-provision.log
# on every exit path, so this is our authoritative diagnostic source.
S3_PROV_KEY="${S3_LOG_PREFIX}/${BAKE_UUID}/provision.log"
LOCAL_PROV_LOG="${REPO_ROOT}/claudedocs/sam-211-bake-${BAKE_UUID}.provision.log"
mkdir -p "${REPO_ROOT}/claudedocs"
log "[log] fetching s3://${S3_LOG_BUCKET}/${S3_PROV_KEY}"
if aws_ s3 cp "s3://${S3_LOG_BUCKET}/${S3_PROV_KEY}" "${LOCAL_PROV_LOG}" >/dev/null 2>&1; then
  log "[log] provision log saved to ${LOCAL_PROV_LOG} ($(wc -c < "${LOCAL_PROV_LOG}" | tr -d ' ') bytes)"
else
  log "[log] WARNING: could not fetch S3 provision log (UUID=${BAKE_UUID}). May not have been uploaded yet."
fi

if [[ "${PROV_STATUS}" != "Success" ]]; then
  fail "provisioning ${PROV_STATUS}; see ${LOCAL_PROV_LOG} for full RCA (S3-persisted, NOT 24KB-truncated)"
fi

# --- Stop instance + create-image ----------------------------------------
# One-time spot instances cannot be stopped (only terminated). On-demand
# instances can. Either way, --no-reboot create-image works directly from
# the running instance — the stop step is purely cosmetic for snapshot
# consistency. Skip the stop for spot, do it for on-demand.
if [[ "${BUILDER_MARKET}" == "spot" ]]; then
  log "[stop] skipped — spot instances cannot be stopped, --no-reboot create-image suffices"
else
  log "[stop] ${BUILDER_ID}"
  aws_ ec2 stop-instances --instance-ids "${BUILDER_ID}" >/dev/null
  aws_ ec2 wait instance-stopped --instance-ids "${BUILDER_ID}"
fi

log "[create-image] ${AMI_NAME}"
# CreateImage Description has 255-char cap. Keep it concise; AMI tags
# carry the structured metadata (BakeUUID, BakedFrom, RCAFix, etc.).
IMAGE_DESC="qpress-sam GPU AMI ${REPO_SHA8}/${VENDOR_SHA8} @ ${BAKE_TS}; base=${BASE_AMI}; RCAFix=#221,#228"
NEW_AMI="$(aws_ ec2 create-image \
  --instance-id "${BUILDER_ID}" \
  --name "${AMI_NAME}" \
  --description "${IMAGE_DESC}" \
  --no-reboot \
  --query 'ImageId' --output text)"
log "NEW_AMI         = ${NEW_AMI}"

# Tag immediately so the AMI is identifiable even if the wait-loop times
# out below.
aws_ ec2 create-tags --resources "${NEW_AMI}" --tags \
  "Key=Project,Value=qpress-sam" \
  "Key=Phase,Value=P4.4" \
  "Key=BakedFrom,Value=${REPO_SHA8}" \
  "Key=BakedAt,Value=${BAKE_TS}" \
  "Key=Builder,Value=sam-bake-ami.sh" \
  "Key=RCAFix,Value=#221" \
  "Key=BakeUUID,Value=${BAKE_UUID}" \
  "Key=Status,Value=baking"

log "[wait] AMI available (typical 5-10 min)"
aws_ ec2 wait image-available --image-ids "${NEW_AMI}"
log "[ami] ${NEW_AMI} state=available"

# Builder no longer needed — terminate now (separate from EXIT trap so
# trap becomes no-op for the success path).
log "[cleanup] terminating builder ${BUILDER_ID} (post-image)"
aws_ ec2 terminate-instances --instance-ids "${BUILDER_ID}" >/dev/null
BUILDER_ID=""
rm -f "${SSM_PARAMS_FILE}"
trap - EXIT

# --- Post-bake validation ------------------------------------------------
if [[ "${SKIP_VALIDATION}" -eq 1 ]]; then
  log "[validation] SKIPPED (--skip-validation)"
  aws_ ec2 create-tags --resources "${NEW_AMI}" --tags "Key=Status,Value=ready-unvalidated" >/dev/null
  echo "${NEW_AMI}"
  exit 0
fi

# Validator uses the same subnet the builder ran in (proven capacity for
# this account/region right now; t3.small is unlikely to hit capacity
# anyway). On --no-fallback / single-subnet override, that's the only
# subnet we ever resolved.
VAL_SUBNET_ID=""
for entry in "${SUBNETS[@]}"; do
  vaz="${entry%% *}"; vsn="${entry#* }"
  if [[ "${vaz}" == "${BUILDER_AZ}" ]]; then
    VAL_SUBNET_ID="${vsn}"; break
  fi
done
if [[ -z "${VAL_SUBNET_ID}" ]]; then
  # Fallback: take the first known subnet.
  first_entry="${SUBNETS[0]}"
  VAL_SUBNET_ID="${first_entry#* }"
fi

log "[validate] launching ${VALIDATOR_TYPE} from ${NEW_AMI} in ${BUILDER_AZ} (${VAL_SUBNET_ID})"
VAL_TAG_SPEC="ResourceType=instance,Tags=[\
{Key=Project,Value=qpress-sam},\
{Key=Purpose,Value=${PURPOSE_TAG}-validate},\
{Key=BakeUUID,Value=${BAKE_UUID}}]"

VAL_ID="$(aws_ ec2 run-instances \
  --image-id "${NEW_AMI}" \
  --instance-type "${VALIDATOR_TYPE}" \
  --iam-instance-profile "Name=${ROLE_NAME}" \
  --network-interfaces "DeviceIndex=0,AssociatePublicIpAddress=true,Groups=${SG_ID},SubnetId=${VAL_SUBNET_ID}" \
  --metadata-options 'HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled' \
  --tag-specifications "${VAL_TAG_SPEC}" \
  --query 'Instances[0].InstanceId' --output text)"
log "VAL_ID          = ${VAL_ID}"

# shellcheck disable=SC2329 # invoked indirectly via trap
cleanup_validator() {
  if [[ -n "${VAL_ID:-}" ]]; then
    log "[cleanup] terminating validator ${VAL_ID}"
    aws_ ec2 terminate-instances --instance-ids "${VAL_ID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup_validator EXIT

aws_ ec2 wait instance-running --instance-ids "${VAL_ID}"
VAL_PING="None"
for _i in $(seq 1 30); do
  VAL_PING="$(aws_ ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${VAL_ID}" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "None")"
  if [[ "${VAL_PING}" == "Online" ]]; then
    break
  fi
  sleep 10
done
[[ "${VAL_PING}" == "Online" ]] || fail "validator ${VAL_ID} did not register with SSM within 5 min"

VALIDATE_B64="$(printf '%s' "${VALIDATE_SCRIPT}" | base64 | tr -d '\n')"
VAL_PARAMS_FILE="$(mktemp)"
cat > "${VAL_PARAMS_FILE}" <<JSON
{
  "commands": [
    "set -e",
    "echo '${VALIDATE_B64}' | base64 -d > /tmp/sam-bake-validate.sh",
    "chmod +x /tmp/sam-bake-validate.sh",
    "bash /tmp/sam-bake-validate.sh"
  ]
}
JSON

VAL_CMD_ID="$(aws_ ssm send-command \
  --instance-ids "${VAL_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "sam-bake-ami validate (${BAKE_UUID})" \
  --timeout-seconds 600 \
  --parameters "file://${VAL_PARAMS_FILE}" \
  --query 'Command.CommandId' --output text)"
rm -f "${VAL_PARAMS_FILE}"

VAL_STATUS="Pending"
for _i in $(seq 1 30); do
  VAL_STATUS="$(aws_ ssm get-command-invocation \
    --command-id "${VAL_CMD_ID}" \
    --instance-id "${VAL_ID}" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")"
  case "${VAL_STATUS}" in
    Success|Cancelled|Failed|TimedOut|Cancelling) break ;;
    *) sleep 10 ;;
  esac
done

VAL_STDOUT="$(aws_ ssm get-command-invocation \
  --command-id "${VAL_CMD_ID}" \
  --instance-id "${VAL_ID}" \
  --query 'StandardOutputContent' --output text 2>/dev/null || echo "")"
VAL_STDERR="$(aws_ ssm get-command-invocation \
  --command-id "${VAL_CMD_ID}" \
  --instance-id "${VAL_ID}" \
  --query 'StandardErrorContent' --output text 2>/dev/null || echo "")"

echo "--- validation stdout ---"
echo "${VAL_STDOUT}"
if [[ -n "${VAL_STDERR}" ]]; then
  echo "--- validation stderr ---"
  echo "${VAL_STDERR}"
fi

if [[ "${VAL_STATUS}" == "Success" ]] && echo "${VAL_STDOUT}" | grep -q "SAM-BAKE-VALIDATE-PASS"; then
  log "[validate] PASS"
  aws_ ec2 create-tags --resources "${NEW_AMI}" --tags "Key=Status,Value=ready" >/dev/null
  cleanup_validator
  trap - EXIT

  echo ""
  echo "=== sam-bake-ami SUCCESS ==="
  echo "AMI:          ${NEW_AMI}"
  echo "Name:         ${AMI_NAME}"
  echo "BakedFrom:    ${REPO_REF} @ ${REPO_SHA}"
  echo "VendorSHA:    ${VENDOR_SHA}"
  echo "BakedAt:      ${BAKE_TS}"
  echo "RCAFix:       #221"
  echo "Status:       ready"
  exit 0
else
  log "[validate] FAIL (status=${VAL_STATUS}) - preserving AMI for forensics, tagging Status=validation-failed"
  aws_ ec2 create-tags --resources "${NEW_AMI}" --tags "Key=Status,Value=validation-failed" >/dev/null
  cleanup_validator
  trap - EXIT
  fail "validation failed; AMI ${NEW_AMI} preserved with Status=validation-failed"
fi
