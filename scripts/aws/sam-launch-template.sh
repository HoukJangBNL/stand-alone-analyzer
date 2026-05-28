#!/usr/bin/env bash
# sam-launch-template.sh — Create or update the qpress-sam-gpu-worker EC2
# launch template (P4.4). Idempotent.
#
# First run:    aws ec2 create-launch-template
# Subsequent:   aws ec2 create-launch-template-version + modify default
#
# What this template captures:
#   - Latest Ubuntu 22.04 amd64 us-east-2 AMI (resolved at script run time)
#   - g6e.xlarge spot
#   - IAM instance profile qpress-sam-gpu-role
#   - Security group qpress-sam-gpu-sg (HTTPS-egress-only)
#   - Bastion VPC public subnet (auto-public-IP)
#   - 100 GB gp3 root, delete-on-termination
#   - Tag spec: Project=qpress-sam, Role=worker, AutoTerminate=true
#   - IMDSv2 required, hop limit 2
#   - UserData: base64 of scripts/aws/sam-gpu-worker-userdata.sh
#
# Tunables (override via env):
#   AWS_REGION       default: us-east-2
#   VPC_ID           default: vpc-053a4df895c279c84  (bastion VPC)
#   SUBNET_ID        default: discovered from VPC + AZ us-east-2a (auto-public)
#   SG_NAME          default: qpress-sam-gpu-sg
#   ROLE_NAME        default: qpress-sam-gpu-role  (instance-profile)
#   INSTANCE_TYPE    default: g6e.xlarge
#   TEMPLATE_NAME    default: qpress-sam-gpu-worker
#   USERDATA_PATH    default: scripts/aws/sam-gpu-worker-userdata.sh
#                              (relative to repo root)

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
VPC_ID="${VPC_ID:-vpc-053a4df895c279c84}"
SG_NAME="${SG_NAME:-qpress-sam-gpu-sg}"
ROLE_NAME="${ROLE_NAME:-qpress-sam-gpu-role}"
INSTANCE_TYPE="${INSTANCE_TYPE:-g6e.xlarge}"
TEMPLATE_NAME="${TEMPLATE_NAME:-qpress-sam-gpu-worker}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
USERDATA_PATH="${USERDATA_PATH:-${REPO_ROOT}/scripts/aws/sam-gpu-worker-userdata.sh}"

echo "Region:        ${AWS_REGION}"
echo "VPC:           ${VPC_ID}"
echo "Template:      ${TEMPLATE_NAME}"
echo "Instance type: ${INSTANCE_TYPE}"
echo "Role/Profile:  ${ROLE_NAME}"
echo "User-data:     ${USERDATA_PATH}"
echo ""

if [[ ! -f "${USERDATA_PATH}" ]]; then
  echo "ERROR: user-data script not found: ${USERDATA_PATH}" >&2
  exit 2
fi

# --- 1. Resolve AMI (latest Ubuntu 22.04 amd64, or use override) ---------
if [[ -n "${IMAGE_ID_OVERRIDE:-}" ]]; then
  IMAGE_ID="${IMAGE_ID_OVERRIDE}"
  echo "[override] IMAGE_ID_OVERRIDE set — skipping describe-images lookup"
  echo "AMI: ${IMAGE_ID} (override)"
else
  echo "[resolve] latest Ubuntu 22.04 amd64 AMI in ${AWS_REGION}"
  IMAGE_ID=$(aws ec2 describe-images \
    --region "${AWS_REGION}" \
    --owners 099720109477 \
    --filters \
      'Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*' \
      'Name=state,Values=available' \
      'Name=architecture,Values=x86_64' \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)
  if [[ -z "${IMAGE_ID}" || "${IMAGE_ID}" == "None" ]]; then
    echo "ERROR: failed to resolve Ubuntu 22.04 AMI" >&2
    exit 1
  fi
  echo "AMI: ${IMAGE_ID}"
fi

# --- 2. Resolve security group -------------------------------------------
SG_ID=$(aws ec2 describe-security-groups \
  --region "${AWS_REGION}" \
  --filters "Name=group-name,Values=${SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [[ "${SG_ID}" == "None" || -z "${SG_ID}" ]]; then
  echo "ERROR: security group ${SG_NAME} not found in ${VPC_ID}" >&2
  echo "       Run scripts/aws/sam-iam-bootstrap.sh first." >&2
  exit 1
fi
echo "SG:  ${SG_ID}"

# --- 3. Resolve subnet ---------------------------------------------------
# Pick the same public subnet the bastion lives in (us-east-2a). The user-data
# needs egress 443 to S3, GitHub, NVIDIA repo, SSM — egress-only via NAT
# would also work but the bastion subnet is already proven public so we
# inherit its routing.
if [[ -z "${SUBNET_ID:-}" ]]; then
  SUBNET_ID=$(aws ec2 describe-subnets \
    --region "${AWS_REGION}" \
    --filters \
      "Name=vpc-id,Values=${VPC_ID}" \
      "Name=availability-zone,Values=us-east-2a" \
      "Name=map-public-ip-on-launch,Values=true" \
    --query 'Subnets[0].SubnetId' \
    --output text)
fi
if [[ -z "${SUBNET_ID}" || "${SUBNET_ID}" == "None" ]]; then
  echo "ERROR: no public subnet found in ${VPC_ID} us-east-2a" >&2
  exit 1
fi
echo "Subnet: ${SUBNET_ID}"

# --- 4. Encode user-data -------------------------------------------------
USERDATA_B64=$(base64 < "${USERDATA_PATH}" | tr -d '\n')

# --- 5. Build LaunchTemplateData JSON ------------------------------------
TPL_DATA_JSON=$(cat <<EOF
{
  "ImageId": "${IMAGE_ID}",
  "InstanceType": "${INSTANCE_TYPE}",
  "InstanceMarketOptions": {"MarketType": "spot"},
  "IamInstanceProfile": {"Name": "${ROLE_NAME}"},
  "NetworkInterfaces": [{
    "DeviceIndex": 0,
    "AssociatePublicIpAddress": true,
    "Groups": ["${SG_ID}"],
    "SubnetId": "${SUBNET_ID}"
  }],
  "BlockDeviceMappings": [{
    "DeviceName": "/dev/sda1",
    "Ebs": {"VolumeSize": 100, "VolumeType": "gp3", "DeleteOnTermination": true}
  }],
  "MetadataOptions": {
    "HttpTokens": "required",
    "HttpPutResponseHopLimit": 2,
    "HttpEndpoint": "enabled"
  },
  "TagSpecifications": [
    {
      "ResourceType": "instance",
      "Tags": [
        {"Key": "Project", "Value": "qpress-sam"},
        {"Key": "Role", "Value": "worker"},
        {"Key": "AutoTerminate", "Value": "true"},
        {"Key": "Name", "Value": "qpress-sam-gpu-worker"}
      ]
    },
    {
      "ResourceType": "volume",
      "Tags": [
        {"Key": "Project", "Value": "qpress-sam"},
        {"Key": "Role", "Value": "worker"}
      ]
    }
  ],
  "UserData": "${USERDATA_B64}"
}
EOF
)

# --- 6. Create or update -------------------------------------------------
EXISTING_ID=$(aws ec2 describe-launch-templates \
  --region "${AWS_REGION}" \
  --launch-template-names "${TEMPLATE_NAME}" \
  --query 'LaunchTemplates[0].LaunchTemplateId' \
  --output text 2>/dev/null || echo "None")

if [[ "${EXISTING_ID}" == "None" || -z "${EXISTING_ID}" ]]; then
  echo "[create] launch template ${TEMPLATE_NAME}"
  TPL_ID=$(aws ec2 create-launch-template \
    --region "${AWS_REGION}" \
    --launch-template-name "${TEMPLATE_NAME}" \
    --version-description "P4.4 initial" \
    --tag-specifications "ResourceType=launch-template,Tags=[{Key=Project,Value=qpress-sam},{Key=Role,Value=worker}]" \
    --launch-template-data "${TPL_DATA_JSON}" \
    --query 'LaunchTemplate.LaunchTemplateId' \
    --output text)
  echo "Created template: ${TPL_ID}"
else
  echo "[update] launch template ${TEMPLATE_NAME} (${EXISTING_ID}) — adding new version"
  NEW_VERSION=$(aws ec2 create-launch-template-version \
    --region "${AWS_REGION}" \
    --launch-template-id "${EXISTING_ID}" \
    --version-description "Updated $(date -u +%FT%TZ)" \
    --launch-template-data "${TPL_DATA_JSON}" \
    --query 'LaunchTemplateVersion.VersionNumber' \
    --output text)
  echo "Created version: v${NEW_VERSION}"

  echo "[set-default] v${NEW_VERSION} for ${TEMPLATE_NAME}"
  aws ec2 modify-launch-template \
    --region "${AWS_REGION}" \
    --launch-template-id "${EXISTING_ID}" \
    --default-version "${NEW_VERSION}" > /dev/null
  TPL_ID="${EXISTING_ID}"
fi

# --- 7. Summary ----------------------------------------------------------
echo ""
echo "Done."
echo "  Template ID:   ${TPL_ID}"
echo "  Template Name: ${TEMPLATE_NAME}"
echo "  AMI:           ${IMAGE_ID}"
echo "  Instance:      ${INSTANCE_TYPE} (spot)"
echo "  SG:            ${SG_ID}"
echo "  Subnet:        ${SUBNET_ID}"
echo ""
echo "Validate (no actual launch):"
echo "  aws ec2 run-instances --dry-run \\"
echo "    --region ${AWS_REGION} \\"
echo "    --launch-template LaunchTemplateName=${TEMPLATE_NAME},Version='\$Default'"
