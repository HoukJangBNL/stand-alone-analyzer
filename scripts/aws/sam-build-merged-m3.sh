#!/usr/bin/env bash
# sam-build-merged-m3.sh — Owner-runnable build step that merges the M3
# LoRA bundle into a single `merged_m3.<sha8>.pt` artifact and uploads it
# to S3 alongside its `.sha256` sidecar and the matching SAM2 config yaml.
#
# Why this script exists:
#   The vendor multi-GPU path (`run_amg_v2.run_multi_process`) goes through
#   `build_sam2_finetuned`, which applies the LoRA adapter at runtime on
#   every forward pass. The 2026-05-28 8-GPU measurement (docs/sam-ops.md
#   §15) measured 12.16 s/card-img — ~3.06× slower than the single-GPU
#   `merged.pt` baseline (3.98 s/img). Pre-merging LoRA into the base
#   weights produces a single .pt that `build_sam2(...)` can load directly,
#   recovering the missing ~3× scaling so 3648 images run in ~30 min on
#   a g6e.48xlarge instead of the failed ~90 min trajectory.
#
# How this script complements existing tooling:
#   - `sam-stage-lora-to-s3.sh` uploads only the raw LoRA adapter
#     (`best_model.pth`) to `internal/sam/m3/sam2_lora/` — keep using it
#     when staging a fresh LoRA from `hal.cfn.bnl.gov`.
#   - This script *consumes* the M3 bundle (4 assets) and *produces* the
#     merged artifact under `internal/sam/merged_m3/`. Run it after each
#     new LoRA stage, before launching a fresh GPU instance.
#
# What it does NOT do (separate, PM-tracked TaskCreate items):
#   - #209: rewire `_run_sam_multi_gpu` to prefer `merged_m3` + `build_sam2`
#   - #210: patch userdata Step 5c to discover/download merged_m3
#   - #211: re-run the 8-GPU measurement against merged_m3
#
# Usage:
#   ./scripts/aws/sam-build-merged-m3.sh [--dry-run] [--keep-tmp]
#
# Optional env overrides:
#   S3_BUCKET=qpress-uploads
#   M3_PREFIX=internal/sam/m3/
#   OUT_PREFIX=internal/sam/merged_m3/
#   AWS_REGION=us-east-2
#
# Requirements (any host with):
#   - AWS credentials with read access to s3://${S3_BUCKET}/${M3_PREFIX}*
#     and write access to s3://${S3_BUCKET}/${OUT_PREFIX}*
#   - Python 3.11 with `torch` (CPU is fine; merge math is element-wise)
#   - ~3 GB free disk for the tmp workspace
#
# shellcheck disable=SC2016  # JMESPath inside aws --query needs literal backticks

set -euo pipefail

# --- Defaults / args ----------------------------------------------------------
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
M3_PREFIX="${M3_PREFIX:-internal/sam/m3/}"
OUT_PREFIX="${OUT_PREFIX:-internal/sam/merged_m3/}"
AWS_REGION="${AWS_REGION:-us-east-2}"

DRY_RUN=0
KEEP_TMP=0

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--keep-tmp] [-h|--help]

Merge the M3 LoRA bundle into a single merged_m3.<sha8>.pt and upload it
to s3://\${S3_BUCKET}/\${OUT_PREFIX} alongside .sha256 + co-located yaml.

Flags:
  --dry-run    Walk every step except the final S3 upload. Prints what
               would be uploaded and the local artifact paths.
  --keep-tmp   Do not delete the working directory on exit (debugging).
  -h, --help   Show this message.

Defaults (override via env):
  S3_BUCKET=${S3_BUCKET}
  M3_PREFIX=${M3_PREFIX}
  OUT_PREFIX=${OUT_PREFIX}
  AWS_REGION=${AWS_REGION}

See docs/sam-ops.md "M3 LoRA Merge Build Step" for the full runbook.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --keep-tmp) KEEP_TMP=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2;;
  esac
done

# --- Pre-flight checks --------------------------------------------------------
command -v aws >/dev/null    || { echo "ERROR: aws CLI not found" >&2; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found" >&2; exit 2; }
command -v jq >/dev/null      || { echo "ERROR: jq not found (brew install jq)" >&2; exit 2; }

# Locate the merge_lora.py script via the repo root.
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
MERGE_PY="${REPO_ROOT}/vendor/QPress-SAM-Flake/scripts/merge_lora.py"
if [[ ! -f "${MERGE_PY}" ]]; then
  echo "ERROR: merge_lora.py not found at ${MERGE_PY}" >&2
  echo "       Did you forget to init the vendor submodule?" >&2
  echo "       Run: git submodule update --init --recursive vendor/QPress-SAM-Flake" >&2
  exit 2
fi

# --- Working directory --------------------------------------------------------
TMP_DIR="$(mktemp -d -t sam-build-merged-m3.XXXXXX)"
cleanup() {
  if [[ "${KEEP_TMP}" == "1" ]]; then
    echo "Tmp preserved at: ${TMP_DIR}"
  else
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

echo "=== sam-build-merged-m3 ==="
echo "Bucket:      s3://${S3_BUCKET}"
echo "M3 prefix:   ${M3_PREFIX}"
echo "Out prefix:  ${OUT_PREFIX}"
echo "Region:      ${AWS_REGION}"
echo "Tmp dir:     ${TMP_DIR}"
echo "Dry-run:     $([[ ${DRY_RUN} == 1 ]] && echo yes || echo no)"
echo ""

# --- Step 1: pull the M3 bundle ----------------------------------------------
M3_LOCAL="${TMP_DIR}/m3"
mkdir -p "${M3_LOCAL}"
echo "[1/5] aws s3 sync ${M3_PREFIX} -> ${M3_LOCAL}"
aws s3 sync "s3://${S3_BUCKET}/${M3_PREFIX}" "${M3_LOCAL}/" \
  --region "${AWS_REGION}" \
  --no-progress

BASE_PT="${M3_LOCAL}/sam2.1/sam2.1_hiera_l.pt"
LORA_PT="${M3_LOCAL}/sam2_lora/best_model.pth"
ARGS_JSON="${M3_LOCAL}/sam2_lora/args.json"
CONFIG_YAML="${M3_LOCAL}/sam2.1/configs/sam2.1_hiera_l.yaml"
for f in "${BASE_PT}" "${LORA_PT}" "${ARGS_JSON}" "${CONFIG_YAML}"; do
  [[ -s "${f}" ]] || { echo "ERROR: missing or empty M3 asset: ${f}" >&2; exit 1; }
done

# --- Step 2: extract alpha from args.json ------------------------------------
# args.json carries three ranks (image_encoder=16, memory_attention=32,
# memory_encoder=32) and a single lora_alpha (32.0). The vendor merge CLI
# now derives rank per-tensor from a.shape[0] when --alpha is used, so we
# only need to pass alpha. See vendor commit f1764c7.
ALPHA="$(jq -r '.lora_alpha' "${ARGS_JSON}")"
if [[ -z "${ALPHA}" || "${ALPHA}" == "null" ]]; then
  echo "ERROR: lora_alpha missing from ${ARGS_JSON}" >&2
  exit 1
fi
echo ""
echo "[2/5] LoRA alpha (from args.json): ${ALPHA}"
IE_RANK="$(jq -r '.lora_image_encoder_rank' "${ARGS_JSON}")"
MA_RANK="$(jq -r '.lora_memory_attention_rank' "${ARGS_JSON}")"
ME_RANK="$(jq -r '.lora_memory_encoder_rank' "${ARGS_JSON}")"
echo "      Per-tensor ranks (info): image_encoder=${IE_RANK}, memory_attention=${MA_RANK}, memory_encoder=${ME_RANK} — derived from tensor shape at merge time"

# --- Step 3: run the merge ----------------------------------------------------
MERGED_PT="${TMP_DIR}/merged_m3.pt"
echo ""
echo "[3/5] python ${MERGE_PY} --alpha ${ALPHA}"
python3 "${MERGE_PY}" \
  --base "${BASE_PT}" \
  --lora "${LORA_PT}" \
  --alpha "${ALPHA}" \
  --out "${MERGED_PT}"

[[ -s "${MERGED_PT}" ]] || { echo "ERROR: merge output is empty: ${MERGED_PT}" >&2; exit 1; }
MERGED_BYTES="$(stat -f%z "${MERGED_PT}" 2>/dev/null || stat -c%s "${MERGED_PT}")"
MERGED_MB=$((MERGED_BYTES / 1024 / 1024))
echo "      Merged size: ${MERGED_MB} MiB"

# --- Step 4: SHA256 + idempotency check --------------------------------------
if command -v sha256sum >/dev/null 2>&1; then
  FULL_SHA="$(sha256sum "${MERGED_PT}" | awk '{print $1}')"
else
  FULL_SHA="$(shasum -a 256 "${MERGED_PT}" | awk '{print $1}')"
fi
SHORT_SHA="${FULL_SHA:0:8}"
OUT_KEY="${OUT_PREFIX}sam2.1_hiera_large.merged_m3.${SHORT_SHA}.pt"
SHA_KEY="${OUT_KEY}.sha256"
YAML_KEY="${OUT_PREFIX}sam2.1_hiera_l.yaml"

echo ""
echo "[4/5] SHA256: ${FULL_SHA}"
echo "      Short:  ${SHORT_SHA}"
echo "      Target: s3://${S3_BUCKET}/${OUT_KEY}"

# Idempotency: if any existing merged_m3 sidecar matches our SHA, skip upload.
LATEST_SHA_KEY=$(aws s3api list-objects-v2 \
  --region "${AWS_REGION}" \
  --bucket "${S3_BUCKET}" \
  --prefix "${OUT_PREFIX}sam2.1_hiera_large.merged_m3." \
  --query 'sort_by(Contents, &LastModified)[?ends_with(Key, `.sha256`)] | [-1].Key' \
  --output text 2>/dev/null || true)
if [[ -n "${LATEST_SHA_KEY}" && "${LATEST_SHA_KEY}" != "None" ]]; then
  EXISTING_SHA=$(aws s3 cp "s3://${S3_BUCKET}/${LATEST_SHA_KEY}" - \
    --region "${AWS_REGION}" --no-progress 2>/dev/null | awk '{print $1}' || true)
  if [[ "${EXISTING_SHA}" == "${FULL_SHA}" ]]; then
    echo ""
    echo "Idempotent: s3://${S3_BUCKET}/${LATEST_SHA_KEY%.sha256} already has SHA ${SHORT_SHA}."
    echo "Skipping upload. Done."
    exit 0
  fi
fi

# --- Step 5: confirm + upload -------------------------------------------------
echo ""
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[5/5] --dry-run: would upload"
  echo "      ${MERGED_PT}    -> s3://${S3_BUCKET}/${OUT_KEY}"
  echo "      <sha256 sidecar> -> s3://${S3_BUCKET}/${SHA_KEY}"
  echo "      ${CONFIG_YAML}   -> s3://${S3_BUCKET}/${YAML_KEY}"
  echo ""
  echo "Dry-run done. Tmp at ${TMP_DIR} (use --keep-tmp to preserve)."
  exit 0
fi

read -r -p "[5/5] Upload to s3://${S3_BUCKET}/${OUT_PREFIX}? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

aws s3 cp "${MERGED_PT}" "s3://${S3_BUCKET}/${OUT_KEY}" \
  --region "${AWS_REGION}" --no-progress
echo "${FULL_SHA}  $(basename "${OUT_KEY}")" > "${TMP_DIR}/sidecar.sha256"
aws s3 cp "${TMP_DIR}/sidecar.sha256" "s3://${S3_BUCKET}/${SHA_KEY}" \
  --region "${AWS_REGION}" --no-progress
aws s3 cp "${CONFIG_YAML}" "s3://${S3_BUCKET}/${YAML_KEY}" \
  --region "${AWS_REGION}" --no-progress

echo ""
echo "Uploaded:"
echo "  s3://${S3_BUCKET}/${OUT_KEY}"
echo "  s3://${S3_BUCKET}/${SHA_KEY}"
echo "  s3://${S3_BUCKET}/${YAML_KEY}"
echo ""
echo "SHA256: ${FULL_SHA}"
echo ""
echo "Next steps (PM-tracked, owner-gated):"
echo "  TaskCreate #209 — wire _run_sam_multi_gpu to prefer merged_m3 + build_sam2"
echo "  TaskCreate #210 — patch sam-gpu-worker-userdata.sh Step 5c for merged_m3 discovery"
echo "  TaskCreate #211 — re-run 8-GPU measurement on merged_m3"
