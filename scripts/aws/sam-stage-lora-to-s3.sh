#!/usr/bin/env bash
# sam-stage-lora-to-s3.sh — One-time owner-driven upload of the prod LoRA adapter
# to s3://qpress-uploads/internal/sam/lora-source/best_model.pth
#
# Why this script: the GPU EC2 bootstrap (sam-gpu-bootstrap.sh) needs the prod
# LoRA adapter to merge into base SAM2 weights. The adapter currently lives on
# `qpress@hal.cfn.bnl.gov:~/sam2_lora/best_model.pth` and is not in S3.
# This script lets the owner stage it from their local machine (after pulling
# from hal) into S3 so the bootstrap instance can fetch it.
#
# Run this ONCE on the owner's laptop (the laptop that has S3 PutObject creds
# for the bucket). After that, the bootstrap instance reads it via the
# qpress-sam-gpu-role IAM role.
#
# Usage:
#   ./scripts/aws/sam-stage-lora-to-s3.sh /path/to/best_model.pth
#
# Defaults (override via env):
#   S3_BUCKET=qpress-uploads
#   S3_KEY=internal/sam/lora-source/best_model.pth
#   AWS_REGION=us-east-2

set -euo pipefail

LOCAL_PATH="${1:-}"
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
S3_KEY="${S3_KEY:-internal/sam/lora-source/best_model.pth}"
AWS_REGION="${AWS_REGION:-us-east-2}"

if [[ -z "${LOCAL_PATH}" ]]; then
  echo "ERROR: Local path to best_model.pth is required." >&2
  echo "Usage: $0 /path/to/best_model.pth" >&2
  exit 2
fi

if [[ ! -f "${LOCAL_PATH}" ]]; then
  echo "ERROR: File not found: ${LOCAL_PATH}" >&2
  exit 2
fi

# Sanity: file should be a torch checkpoint (a few hundred MB typical).
SIZE_BYTES=$(stat -f%z "${LOCAL_PATH}" 2>/dev/null || stat -c%s "${LOCAL_PATH}")
SIZE_MB=$((SIZE_BYTES / 1024 / 1024))
echo "Local file:  ${LOCAL_PATH}"
echo "Size:        ${SIZE_MB} MiB (${SIZE_BYTES} bytes)"
echo "Destination: s3://${S3_BUCKET}/${S3_KEY}"
echo "Region:      ${AWS_REGION}"
echo ""

if [[ "${SIZE_MB}" -lt 10 ]]; then
  echo "WARNING: file is < 10 MiB — that's unusually small for a SAM2 LoRA adapter." >&2
  echo "         Verify this is the right file before continuing." >&2
fi

# Quick read of the torch header to fail fast on obvious mistakes.
HEAD_MAGIC=$(head -c 2 "${LOCAL_PATH}" | xxd -p || true)
case "${HEAD_MAGIC}" in
  504b)  echo "Detected: ZIP magic (likely a torch.save checkpoint)";;
  8095)  echo "Detected: pickle protocol >=4 magic (legacy torch.save)";;
  *)     echo "WARNING: unrecognized header bytes '${HEAD_MAGIC}'. Continuing anyway." >&2;;
esac

read -r -p "Upload now? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

aws s3 cp "${LOCAL_PATH}" "s3://${S3_BUCKET}/${S3_KEY}" \
  --region "${AWS_REGION}" \
  --no-progress

echo ""
echo "Verifying upload..."
aws s3api head-object \
  --bucket "${S3_BUCKET}" \
  --key "${S3_KEY}" \
  --region "${AWS_REGION}" \
  --query '{Size:ContentLength,LastModified:LastModified,ETag:ETag}' \
  --output table

echo ""
echo "Computing SHA256 of local file (record this for verification)..."
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${LOCAL_PATH}"
else
  shasum -a 256 "${LOCAL_PATH}"
fi

echo ""
echo "Done. The GPU bootstrap (sam-gpu-bootstrap.sh) will GET this object via the"
echo "qpress-sam-gpu-role IAM role at instance launch time."
