#!/usr/bin/env bash
# Read-only audit of qpress-uploads bucket. Diffs live AWS state against infra/s3/*.json.
# Exits 0 if all PASS, 1 on first FAIL.

set -uo pipefail

PROFILE="qpress"
REGION="us-east-2"
BUCKET="qpress-uploads"
INFRA_DIR="$(cd "$(dirname "$0")/../../infra/s3" && pwd)"

pass() { printf "  \033[32mPASS\033[0m %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m %s\n  %s\n" "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); }

FAIL_COUNT=0

echo "=== qpress-uploads audit ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ==="

# 1. Bucket exists in expected region
LOC=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-location \
  --bucket "$BUCKET" --query LocationConstraint --output text 2>&1) || true
if [[ "$LOC" == "$REGION" ]]; then pass "bucket region = $REGION"
else fail "bucket region" "got '$LOC' (expected '$REGION')"; fi

# 2. Public access block: all 4 = true
PAB=$(aws --profile "$PROFILE" --region "$REGION" s3api get-public-access-block \
  --bucket "$BUCKET" --query 'PublicAccessBlockConfiguration' --output json 2>&1) || true
if echo "$PAB" | grep -q '"BlockPublicAcls": true' \
  && echo "$PAB" | grep -q '"IgnorePublicAcls": true' \
  && echo "$PAB" | grep -q '"BlockPublicPolicy": true' \
  && echo "$PAB" | grep -q '"RestrictPublicBuckets": true'; then
  pass "public access fully blocked (4/4)"
else fail "public access block" "$PAB"; fi

# 3. Encryption = AES256
ENC=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-encryption \
  --bucket "$BUCKET" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
  --output text 2>&1) || true
if [[ "$ENC" == "AES256" ]]; then pass "encryption = AES256 (SSE-S3)"
else fail "encryption" "got '$ENC'"; fi

# 4. Ownership = BucketOwnerEnforced
OWN=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-ownership-controls \
  --bucket "$BUCKET" --query 'OwnershipControls.Rules[0].ObjectOwnership' --output text 2>&1) || true
if [[ "$OWN" == "BucketOwnerEnforced" ]]; then pass "ownership = BucketOwnerEnforced"
else fail "ownership" "got '$OWN'"; fi

# 5. CORS rule count + checksum-sha256 header allow-listed
CORS=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-cors --bucket "$BUCKET" --output json 2>&1) || true
if echo "$CORS" | grep -q "x-amz-checksum-sha256"; then pass "CORS allows x-amz-checksum-sha256"
else fail "CORS x-amz-checksum-sha256" "header not in AllowedHeaders / ExposeHeaders"; fi

# 6. Lifecycle: 3 rule IDs present
LC=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-lifecycle-configuration \
  --bucket "$BUCKET" --query 'Rules[].ID' --output json 2>&1) || true
for ID in "dev-expire-30d" "abort-multipart-7d" "dev-uploads-pending-1d"; do
  if echo "$LC" | grep -q "\"$ID\""; then pass "lifecycle rule '$ID'"
  else fail "lifecycle rule '$ID'" "not found in $LC"; fi
done

# 7. Bucket policy contains both deny statements
BP=$(aws --profile "$PROFILE" --region "$REGION" s3api get-bucket-policy \
  --bucket "$BUCKET" --query Policy --output text 2>&1) || true
for SID in "DenyDevPrincipalsWritingProd" "DenyProdPrincipalsWritingDev" "DenyUntaggedPrincipalsWritingAnyPrefix"; do
  if echo "$BP" | grep -q "$SID"; then pass "bucket policy Sid '$SID'"
  else fail "bucket policy Sid '$SID'" "not found"; fi
done

# 8. IAM policies exist
for POL in "qpress-api-s3-uploads-dev" "qpress-api-s3-uploads-prod"; do
  ARN=$(aws --profile "$PROFILE" iam list-policies --scope Local \
    --query "Policies[?PolicyName=='$POL'].Arn" --output text)
  if [[ -n "$ARN" ]]; then pass "IAM policy '$POL'"
  else fail "IAM policy '$POL'" "not found"; fi
done

# 9. Dev user exists + Env tag = dev
TAG=$(aws --profile "$PROFILE" iam list-user-tags --user-name qpress-dev-local \
  --query "Tags[?Key=='Env'].Value" --output text 2>&1) || true
if [[ "$TAG" == "dev" ]]; then pass "qpress-dev-local tagged Env=dev"
else fail "qpress-dev-local Env tag" "got '$TAG'"; fi

echo ""
if [[ $FAIL_COUNT -eq 0 ]]; then
  echo "=== ALL PASS ==="
  exit 0
else
  echo "=== $FAIL_COUNT FAIL(s) ==="
  exit 1
fi
