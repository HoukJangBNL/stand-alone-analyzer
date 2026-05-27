#!/usr/bin/env bash
# sam-eventbridge.sh — Spot-interrupt audit trail (P4.4). Idempotent.
#
# Why: the IMDS-poll handler on the worker (sam-gpu-worker-userdata.sh's
# spot-monitor timer) is the primary mitigation — it sees the 2-min
# warning locally and SIGTERMs the worker so procrastinate can drain
# gracefully. The EventBridge rule here is belt-and-suspenders: it
# captures every spot-interrupt event for instances tagged
# Project=qpress-sam to an SNS topic for telemetry / debugging /
# subscribed alerts.
#
# Resources (idempotent — re-runnable):
#   - SNS topic: qpress-sam-spot-interrupt-notify
#   - EventBridge rule: qpress-sam-spot-interrupt
#   - EventBridge → SNS target (with required resource-policy on the topic)
#
# Tunables (override via env):
#   AWS_REGION   default: us-east-2
#   RULE_NAME    default: qpress-sam-spot-interrupt
#   TOPIC_NAME   default: qpress-sam-spot-interrupt-notify
#   ACCOUNT_ID   default: aws sts get-caller-identity

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
RULE_NAME="${RULE_NAME:-qpress-sam-spot-interrupt}"
TOPIC_NAME="${TOPIC_NAME:-qpress-sam-spot-interrupt-notify}"
ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"

echo "Region:   ${AWS_REGION}"
echo "Account:  ${ACCOUNT_ID}"
echo "Rule:     ${RULE_NAME}"
echo "Topic:    ${TOPIC_NAME}"
echo ""

TOPIC_ARN="arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:${TOPIC_NAME}"
RULE_ARN="arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}"

# --- 1. SNS topic --------------------------------------------------------
EXISTING_TOPIC=$(aws sns list-topics \
  --region "${AWS_REGION}" \
  --query "Topics[?TopicArn=='${TOPIC_ARN}'].TopicArn" \
  --output text)

if [[ -z "${EXISTING_TOPIC}" || "${EXISTING_TOPIC}" == "None" ]]; then
  echo "[create] SNS topic ${TOPIC_NAME}"
  aws sns create-topic \
    --region "${AWS_REGION}" \
    --name "${TOPIC_NAME}" \
    --tags Key=Project,Value=qpress-sam Key=Role,Value=audit \
    > /dev/null
else
  echo "[skip] SNS topic ${TOPIC_NAME} already exists"
fi

# --- 2. SNS access policy (allow EventBridge to publish) -----------------
# Without this, EventBridge → SNS target fails with AccessDenied. Setting
# the policy is idempotent — overwrites in place.
SNS_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEventBridgePublish",
      "Effect": "Allow",
      "Principal": {"Service": "events.amazonaws.com"},
      "Action": "SNS:Publish",
      "Resource": "${TOPIC_ARN}",
      "Condition": {
        "ArnEquals": {"aws:SourceArn": "${RULE_ARN}"}
      }
    },
    {
      "Sid": "AllowOwnerFullControl",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::${ACCOUNT_ID}:root"},
      "Action": "SNS:*",
      "Resource": "${TOPIC_ARN}"
    }
  ]
}
EOF
)

echo "[set-policy] SNS topic policy"
aws sns set-topic-attributes \
  --region "${AWS_REGION}" \
  --topic-arn "${TOPIC_ARN}" \
  --attribute-name Policy \
  --attribute-value "${SNS_POLICY}"

# --- 3. EventBridge rule -------------------------------------------------
# Pattern: any EC2 Spot Instance Interruption Warning. We don't filter on
# the Project tag at the pattern level because EventBridge event patterns
# can't directly inspect EC2 instance tags. Instead, we wire a Lambda or
# rely on the worker's IMDS-poll for the active SIGTERM. The rule's
# value is auditing — every interrupt is delivered to SNS regardless of
# instance, and the topic policy + filter on subscription side can scope
# further if needed.
EVENT_PATTERN=$(cat <<'EOF'
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 Spot Instance Interruption Warning"],
  "detail": {
    "instance-id": [{"exists": true}]
  }
}
EOF
)

echo "[put] EventBridge rule ${RULE_NAME}"
aws events put-rule \
  --region "${AWS_REGION}" \
  --name "${RULE_NAME}" \
  --description "Audit-trail: every EC2 spot interrupt (qpress-sam workers handled locally via IMDS poll)" \
  --event-pattern "${EVENT_PATTERN}" \
  --state ENABLED \
  --tags Key=Project,Value=qpress-sam Key=Role,Value=audit \
  > /dev/null

# --- 4. Wire SNS target --------------------------------------------------
# put-targets is idempotent for the same Id — replaces the target in place.
echo "[put-targets] SNS target on ${RULE_NAME}"
aws events put-targets \
  --region "${AWS_REGION}" \
  --rule "${RULE_NAME}" \
  --targets "[{\"Id\":\"sns\",\"Arn\":\"${TOPIC_ARN}\"}]" \
  > /dev/null

# --- 5. Summary ----------------------------------------------------------
echo ""
echo "Done."
echo "  SNS topic:    ${TOPIC_ARN}"
echo "  EventBridge:  ${RULE_ARN}"
echo ""
echo "To subscribe owner email (manual, optional):"
echo "  aws sns subscribe --region ${AWS_REGION} \\"
echo "    --topic-arn ${TOPIC_ARN} \\"
echo "    --protocol email --notification-endpoint owner@example.com"
