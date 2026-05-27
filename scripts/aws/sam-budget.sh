#!/usr/bin/env bash
# sam-budget.sh — create/update AWS Budgets + SNS topic for the qpress-sam GPU stack.
#
# Idempotent: re-runs are safe — SNS topic is `create-topic` (returns existing
# ARN if already there), and budgets are upserted via `create-budget` first,
# falling back to `update-budget` if the budget already exists.
#
# Resources created:
#   - SNS topic:       qpress-sam-budget-alerts          (us-east-1)
#   - Budget:          qpress-sam-monthly-budget         ($600/mo, 50/80/100% actual + 100% forecast)
#   - Budget:          qpress-sam-daily-budget           ($20/day, 100% actual)
#   - Tag activation:  cost-allocation tag `Project` set to Active in CE
#
# All cost filters scope to user-defined tag `Project=qpress-sam` so budgets
# only count resources tagged by P4.3 / P4.4 (IAM, SG, EC2 spot workers,
# launch template, EventBridge rule, etc.).
#
# IMPORTANT — owner action required AFTER first run:
#   1. The email address in OWNER_EMAIL is a placeholder. Re-run with
#        OWNER_EMAIL=you@example.com bash scripts/aws/sam-budget.sh
#      to subscribe the real address. AWS will then send a confirmation
#      link to that mailbox; the owner must click it for alerts to fire.
#   2. AWS Billing > Cost allocation tags console may take ~24h to flip the
#      `Project` tag to ACTIVE even after this script's update call succeeds.
#      Verify with the `list-cost-allocation-tags` invocation below.
#
# Tunables (override via env):
#   AWS_REGION_BUDGETS  default: us-east-1   (Budgets + SNS for budgets live in us-east-1 by AWS convention)
#   AWS_REGION_RESOURCES default: us-east-2   (only used in echoed reminders — script doesn't actually touch us-east-2)
#   ACCOUNT_ID          default: auto-detected via `aws sts get-caller-identity`
#   PROJECT_TAG         default: qpress-sam
#   OWNER_EMAIL         default: OWNER_EMAIL_REQUIRED@example.com  (placeholder — must be replaced)
#   MONTHLY_USD         default: 600
#   DAILY_USD           default: 20
#   SNS_TOPIC_NAME      default: qpress-sam-budget-alerts
#   MONTHLY_BUDGET_NAME default: qpress-sam-monthly-budget
#   DAILY_BUDGET_NAME   default: qpress-sam-daily-budget

set -euo pipefail

AWS_REGION_BUDGETS="${AWS_REGION_BUDGETS:-us-east-1}"
AWS_REGION_RESOURCES="${AWS_REGION_RESOURCES:-us-east-2}"
PROJECT_TAG="${PROJECT_TAG:-qpress-sam}"
OWNER_EMAIL="${OWNER_EMAIL:-OWNER_EMAIL_REQUIRED@example.com}"
MONTHLY_USD="${MONTHLY_USD:-600}"
DAILY_USD="${DAILY_USD:-20}"
SNS_TOPIC_NAME="${SNS_TOPIC_NAME:-qpress-sam-budget-alerts}"
MONTHLY_BUDGET_NAME="${MONTHLY_BUDGET_NAME:-qpress-sam-monthly-budget}"
DAILY_BUDGET_NAME="${DAILY_BUDGET_NAME:-qpress-sam-daily-budget}"

if [[ -z "${ACCOUNT_ID:-}" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)
fi

echo "Account:               ${ACCOUNT_ID}"
echo "Budgets/SNS region:    ${AWS_REGION_BUDGETS}"
echo "Resource region:       ${AWS_REGION_RESOURCES}  (informational only)"
echo "Project tag:           ${PROJECT_TAG}"
echo "Monthly cap:           \$${MONTHLY_USD} USD"
echo "Daily cap:             \$${DAILY_USD} USD"
echo "SNS topic:             ${SNS_TOPIC_NAME}"
echo "Owner email:           ${OWNER_EMAIL}"
echo ""

if [[ "${OWNER_EMAIL}" == "OWNER_EMAIL_REQUIRED@example.com" ]]; then
  echo "[warn] OWNER_EMAIL is a placeholder — alerts will fire only after you"
  echo "       re-run with OWNER_EMAIL=<real-address> AND click the AWS"
  echo "       confirmation email AWS sends to that address."
  echo ""
fi

# --- 1. Activate cost-allocation tag `Project` ----------------------------
# Cost Explorer ('ce') manages the activation flag for user-defined tags.
# The Billing console UI calls this same API. Activation typically takes a
# few minutes to flip Status=Active in `list-cost-allocation-tags`, but the
# tag becomes usable in Budgets cost filters immediately.
#
# Permission note: the IAM principal running this script needs
# `ce:UpdateCostAllocationTagsStatus`. If the call fails with AccessDenied,
# the script continues — budgets still work via tag filter, but the owner
# must flip the tag to Active manually in the Billing console:
#   AWS Console -> Billing -> Cost allocation tags -> User-defined ->
#     check "Project" -> Activate.
echo "[ce] activating cost-allocation tag 'Project'"
if aws ce update-cost-allocation-tags-status \
     --region "${AWS_REGION_BUDGETS}" \
     --cost-allocation-tags-status TagKey=Project,Status=Active \
     >/dev/null 2>&1; then
  echo "[ce] activation request submitted"
else
  echo "[ce] WARN: activation API call failed (likely missing"
  echo "          ce:UpdateCostAllocationTagsStatus IAM permission)."
  echo "          Budgets will still work — owner must flip the tag to"
  echo "          Active manually in AWS Billing console (one-time)."
  echo "          Not blocking — continuing."
fi

# --- 2. SNS topic ---------------------------------------------------------
# create-topic is idempotent — returns the existing topic ARN if already created.
echo "[sns] ensure topic ${SNS_TOPIC_NAME}"
SNS_ARN=$(aws sns create-topic \
  --region "${AWS_REGION_BUDGETS}" \
  --name "${SNS_TOPIC_NAME}" \
  --tags Key=Project,Value="${PROJECT_TAG}" \
  --query 'TopicArn' --output text)
echo "[sns] topic ARN: ${SNS_ARN}"

# Allow the AWS Budgets service to publish to this topic.
# Without this policy, Budgets silently fails to deliver notifications.
SNS_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBudgetsPublish",
      "Effect": "Allow",
      "Principal": {"Service": "budgets.amazonaws.com"},
      "Action": "SNS:Publish",
      "Resource": "${SNS_ARN}"
    }
  ]
}
EOF
)
echo "[sns] set policy allowing budgets.amazonaws.com to publish"
aws sns set-topic-attributes \
  --region "${AWS_REGION_BUDGETS}" \
  --topic-arn "${SNS_ARN}" \
  --attribute-name Policy \
  --attribute-value "${SNS_POLICY}"

# Email subscription. AWS dedupes by (TopicArn, Protocol, Endpoint) so
# re-running with the same email is a no-op. Confirmation is owner-side.
if [[ "${OWNER_EMAIL}" != "OWNER_EMAIL_REQUIRED@example.com" ]]; then
  EXISTING_SUB=$(aws sns list-subscriptions-by-topic \
    --region "${AWS_REGION_BUDGETS}" \
    --topic-arn "${SNS_ARN}" \
    --query "Subscriptions[?Endpoint=='${OWNER_EMAIL}'].SubscriptionArn | [0]" \
    --output text 2>/dev/null || echo "None")
  if [[ "${EXISTING_SUB}" == "None" || -z "${EXISTING_SUB}" || "${EXISTING_SUB}" == "PendingConfirmation" ]]; then
    echo "[sns] subscribe email ${OWNER_EMAIL}"
    aws sns subscribe \
      --region "${AWS_REGION_BUDGETS}" \
      --topic-arn "${SNS_ARN}" \
      --protocol email \
      --notification-endpoint "${OWNER_EMAIL}" \
      >/dev/null
    echo "[sns] confirmation email sent — owner must click the link"
  else
    echo "[sns] email ${OWNER_EMAIL} already subscribed (${EXISTING_SUB})"
  fi
else
  echo "[sns] skipping email subscription (placeholder OWNER_EMAIL)"
fi

# --- 3. Budgets -----------------------------------------------------------
# Helper: write a budget JSON to a temp file and upsert.
TMPDIR_BUDGET=$(mktemp -d)
trap 'rm -rf "${TMPDIR_BUDGET}"' EXIT

upsert_budget() {
  local BUDGET_FILE="$1"
  local NOTIFY_FILE="$2"
  local BUDGET_NAME="$3"

  if aws budgets describe-budget \
       --region "${AWS_REGION_BUDGETS}" \
       --account-id "${ACCOUNT_ID}" \
       --budget-name "${BUDGET_NAME}" \
       >/dev/null 2>&1; then
    echo "[budgets] update ${BUDGET_NAME}"
    aws budgets update-budget \
      --region "${AWS_REGION_BUDGETS}" \
      --account-id "${ACCOUNT_ID}" \
      --new-budget "file://${BUDGET_FILE}"
    # Notifications can't be updated atomically with the budget. Iterate and
    # try to create-notification for each — AWS returns DuplicateRecordException
    # if it already exists, which we swallow.
    local NOTIF_COUNT
    NOTIF_COUNT=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "${NOTIFY_FILE}")
    local i
    for ((i = 0; i < NOTIF_COUNT; i++)); do
      local NOTIFICATION SUBSCRIBERS
      NOTIFICATION=$(python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
print(json.dumps(data[int(sys.argv[2])]["Notification"]))
' "${NOTIFY_FILE}" "${i}")
      SUBSCRIBERS=$(python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
print(json.dumps(data[int(sys.argv[2])]["Subscribers"]))
' "${NOTIFY_FILE}" "${i}")
      aws budgets create-notification \
        --region "${AWS_REGION_BUDGETS}" \
        --account-id "${ACCOUNT_ID}" \
        --budget-name "${BUDGET_NAME}" \
        --notification "${NOTIFICATION}" \
        --subscribers "${SUBSCRIBERS}" \
        >/dev/null 2>&1 || true
    done
  else
    echo "[budgets] create ${BUDGET_NAME}"
    # Convert notifications JSON list to the inline arg format expected by create-budget.
    local NOTIF_ARG
    NOTIF_ARG=$(python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
print(json.dumps(data))
' "${NOTIFY_FILE}")
    aws budgets create-budget \
      --region "${AWS_REGION_BUDGETS}" \
      --account-id "${ACCOUNT_ID}" \
      --budget "file://${BUDGET_FILE}" \
      --notifications-with-subscribers "${NOTIF_ARG}"
  fi
}

# --- 3a. Monthly $600 budget ---------------------------------------------
MONTHLY_BUDGET_FILE="${TMPDIR_BUDGET}/monthly_budget.json"
MONTHLY_NOTIF_FILE="${TMPDIR_BUDGET}/monthly_notif.json"

cat > "${MONTHLY_BUDGET_FILE}" <<EOF
{
  "BudgetName": "${MONTHLY_BUDGET_NAME}",
  "BudgetLimit": {"Amount": "${MONTHLY_USD}", "Unit": "USD"},
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST",
  "CostFilters": {
    "TagKeyValue": ["user:Project\$${PROJECT_TAG}"]
  },
  "CostTypes": {
    "IncludeTax": true,
    "IncludeSubscription": true,
    "UseBlended": false,
    "IncludeRefund": false,
    "IncludeCredit": false,
    "IncludeUpfront": true,
    "IncludeRecurring": true,
    "IncludeOtherSubscription": true,
    "IncludeSupport": true,
    "IncludeDiscount": true,
    "UseAmortized": false
  }
}
EOF

cat > "${MONTHLY_NOTIF_FILE}" <<EOF
[
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 50,
      "ThresholdType": "PERCENTAGE",
      "NotificationState": "ALARM"
    },
    "Subscribers": [
      {"SubscriptionType": "SNS", "Address": "${SNS_ARN}"}
    ]
  },
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80,
      "ThresholdType": "PERCENTAGE",
      "NotificationState": "ALARM"
    },
    "Subscribers": [
      {"SubscriptionType": "SNS", "Address": "${SNS_ARN}"}
    ]
  },
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 100,
      "ThresholdType": "PERCENTAGE",
      "NotificationState": "ALARM"
    },
    "Subscribers": [
      {"SubscriptionType": "SNS", "Address": "${SNS_ARN}"}
    ]
  },
  {
    "Notification": {
      "NotificationType": "FORECASTED",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 100,
      "ThresholdType": "PERCENTAGE",
      "NotificationState": "ALARM"
    },
    "Subscribers": [
      {"SubscriptionType": "SNS", "Address": "${SNS_ARN}"}
    ]
  }
]
EOF

upsert_budget "${MONTHLY_BUDGET_FILE}" "${MONTHLY_NOTIF_FILE}" "${MONTHLY_BUDGET_NAME}"

# --- 3b. Daily $20 budget -------------------------------------------------
DAILY_BUDGET_FILE="${TMPDIR_BUDGET}/daily_budget.json"
DAILY_NOTIF_FILE="${TMPDIR_BUDGET}/daily_notif.json"

cat > "${DAILY_BUDGET_FILE}" <<EOF
{
  "BudgetName": "${DAILY_BUDGET_NAME}",
  "BudgetLimit": {"Amount": "${DAILY_USD}", "Unit": "USD"},
  "TimeUnit": "DAILY",
  "BudgetType": "COST",
  "CostFilters": {
    "TagKeyValue": ["user:Project\$${PROJECT_TAG}"]
  },
  "CostTypes": {
    "IncludeTax": true,
    "IncludeSubscription": true,
    "UseBlended": false,
    "IncludeRefund": false,
    "IncludeCredit": false,
    "IncludeUpfront": true,
    "IncludeRecurring": true,
    "IncludeOtherSubscription": true,
    "IncludeSupport": true,
    "IncludeDiscount": true,
    "UseAmortized": false
  }
}
EOF

cat > "${DAILY_NOTIF_FILE}" <<EOF
[
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 100,
      "ThresholdType": "PERCENTAGE",
      "NotificationState": "ALARM"
    },
    "Subscribers": [
      {"SubscriptionType": "SNS", "Address": "${SNS_ARN}"}
    ]
  }
]
EOF

upsert_budget "${DAILY_BUDGET_FILE}" "${DAILY_NOTIF_FILE}" "${DAILY_BUDGET_NAME}"

# --- 4. Verify cost-allocation tag is Active ------------------------------
echo ""
echo "[verify] cost-allocation tag 'Project' status:"
aws ce list-cost-allocation-tags \
  --region "${AWS_REGION_BUDGETS}" \
  --status Active \
  --output json \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
tags = data.get("CostAllocationTags", [])
project = next((t for t in tags if t.get("TagKey") == "Project"), None)
if project is None:
    print("  Project: NOT YET ACTIVE — billing console may take ~24h to flip status")
    print("  (the activation request was submitted; budgets still work via tag filter)")
else:
    print(f"  Project: Status={project.get(\"Status\")}, Type={project.get(\"Type\")}")
'

echo ""
echo "Done."
echo ""
echo "  SNS topic:         ${SNS_ARN}"
echo "  Monthly budget:    ${MONTHLY_BUDGET_NAME}  (\$${MONTHLY_USD} USD, 50/80/100% actual + 100% forecast)"
echo "  Daily budget:      ${DAILY_BUDGET_NAME}  (\$${DAILY_USD} USD, 100% actual)"
echo ""
echo "Owner action required:"
echo "  1. Re-run with OWNER_EMAIL=<your-address> if not already done"
echo "  2. Click the AWS confirmation link sent to that mailbox"
echo "  3. Note: AWS Budgets has a ~24h evaluation lag — alerts won't fire until tomorrow"
