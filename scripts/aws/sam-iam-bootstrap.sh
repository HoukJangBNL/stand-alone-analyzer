#!/usr/bin/env bash
# sam-iam-bootstrap.sh — create IAM role + instance profile + SG for SAM GPU boxes.
#
# Idempotent: safe to re-run. Creates only what's missing.
#
# Resources created:
#   - IAM role:                 qpress-sam-gpu-role
#   - IAM inline policy:        qpress-sam-gpu-s3 (attached to role)
#   - IAM managed policy attach: AmazonSSMManagedInstanceCore
#   - IAM instance profile:     qpress-sam-gpu-role (same name as role)
#   - EC2 security group:       qpress-sam-gpu-sg (in $VPC_ID)
#
# Tunables (override via env):
#   AWS_REGION    default: us-east-2
#   VPC_ID        default: vpc-053a4df895c279c84  (bastion VPC)
#   S3_BUCKET     default: qpress-uploads
#   S3_PREFIX     default: internal/sam/
#   ROLE_NAME     default: qpress-sam-gpu-role
#   SG_NAME       default: qpress-sam-gpu-sg

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
VPC_ID="${VPC_ID:-vpc-053a4df895c279c84}"
S3_BUCKET="${S3_BUCKET:-qpress-uploads}"
S3_PREFIX="${S3_PREFIX:-internal/sam/}"
ROLE_NAME="${ROLE_NAME:-qpress-sam-gpu-role}"
INLINE_POLICY="qpress-sam-gpu-s3"
SG_NAME="${SG_NAME:-qpress-sam-gpu-sg}"

echo "Region:    ${AWS_REGION}"
echo "VPC:       ${VPC_ID}"
echo "Bucket:    s3://${S3_BUCKET}/${S3_PREFIX}"
echo "Role:      ${ROLE_NAME}"
echo "SG:        ${SG_NAME}"
echo ""

# --- 1. IAM role ---------------------------------------------------------
TRUST_POLICY=$(cat <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "[skip] role ${ROLE_NAME} already exists"
else
  echo "[create] role ${ROLE_NAME}"
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --description "Role for short-lived SAM2 GPU EC2 instances (us-east-2 spot)" \
    --tags Key=Project,Value=qpress-sam \
    > /dev/null
fi

# --- 2. Inline S3 policy -------------------------------------------------
S3_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadWriteSamPrefix",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::${S3_BUCKET}/${S3_PREFIX}*"
    },
    {
      "Sid": "ListSamPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${S3_BUCKET}",
      "Condition": {
        "StringLike": {"s3:prefix": ["${S3_PREFIX}*", "${S3_PREFIX}"]}
      }
    },
    {
      "Sid": "ReadWriteScansPrefix",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::${S3_BUCKET}/scans/*",
        "arn:aws:s3:::${S3_BUCKET}/dev/scans/*"
      ]
    },
    {
      "Sid": "ListScansPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${S3_BUCKET}",
      "Condition": {
        "StringLike": {"s3:prefix": ["scans/*", "dev/scans/*"]}
      }
    }
  ]
}
EOF
)

echo "[put] inline policy ${INLINE_POLICY} on role ${ROLE_NAME}"
aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "${INLINE_POLICY}" \
  --policy-document "${S3_POLICY}"

# --- 3. Attach AWS-managed SSM policy ------------------------------------
echo "[attach] AmazonSSMManagedInstanceCore on role ${ROLE_NAME}"
aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# --- 4. Instance profile -------------------------------------------------
if aws iam get-instance-profile --instance-profile-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "[skip] instance profile ${ROLE_NAME} already exists"
else
  echo "[create] instance profile ${ROLE_NAME}"
  aws iam create-instance-profile --instance-profile-name "${ROLE_NAME}" > /dev/null
fi

# Add role to profile (idempotent: API errors if already added — swallow gracefully)
ROLES_IN_PROFILE=$(aws iam get-instance-profile \
  --instance-profile-name "${ROLE_NAME}" \
  --query 'InstanceProfile.Roles[].RoleName' --output text)
if [[ "${ROLES_IN_PROFILE}" != *"${ROLE_NAME}"* ]]; then
  echo "[add] role to instance profile"
  aws iam add-role-to-instance-profile \
    --instance-profile-name "${ROLE_NAME}" \
    --role-name "${ROLE_NAME}"
else
  echo "[skip] role already attached to instance profile"
fi

# --- 5. Security group ---------------------------------------------------
SG_ID=$(aws ec2 describe-security-groups \
  --region "${AWS_REGION}" \
  --filters "Name=group-name,Values=${SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "${SG_ID}" == "None" || -z "${SG_ID}" ]]; then
  echo "[create] security group ${SG_NAME}"
  SG_ID=$(aws ec2 create-security-group \
    --region "${AWS_REGION}" \
    --vpc-id "${VPC_ID}" \
    --group-name "${SG_NAME}" \
    --description "SAM2 GPU EC2 - SSM-only, no inbound, HTTPS egress" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=qpress-sam},{Key=Name,Value=${SG_NAME}}]" \
    --query 'GroupId' --output text)

  # Default SG comes with allow-all-egress. Replace with HTTPS-only egress.
  echo "[revoke] default allow-all egress on ${SG_ID}"
  aws ec2 revoke-security-group-egress \
    --region "${AWS_REGION}" \
    --group-id "${SG_ID}" \
    --ip-permissions '[{"IpProtocol":"-1","IpRanges":[{"CidrIp":"0.0.0.0/0"}]}]' \
    >/dev/null 2>&1 || true

  echo "[authorize] HTTPS egress on ${SG_ID}"
  aws ec2 authorize-security-group-egress \
    --region "${AWS_REGION}" \
    --group-id "${SG_ID}" \
    --ip-permissions '[{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"IpRanges":[{"CidrIp":"0.0.0.0/0","Description":"HTTPS for S3, package mirrors, GitHub, SSM"}]}]'
else
  echo "[skip] security group ${SG_NAME} already exists: ${SG_ID}"
fi

echo ""
echo "Done."
echo "  Role:         ${ROLE_NAME}"
echo "  InstanceProf: ${ROLE_NAME}"
echo "  SG ID:        ${SG_ID}"
