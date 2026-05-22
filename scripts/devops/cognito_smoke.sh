#!/usr/bin/env bash
# cognito_smoke.sh — read-only verification that cognito_bootstrap.sh succeeded.
#
# Performs ONLY read-only AWS calls (list/describe/get).  Safe to run by anyone
# with `cognito-idp:Describe*` and `ssm:GetParameter*` permissions.
#
# Checks:
#   1. SSM params under /saa/cognito are all present (5 keys).
#   2. The user_pool_id in SSM resolves to a real pool with the expected name.
#   3. The pool exposes the `organization` custom attribute.
#   4. The app_client_id in SSM resolves and is configured for OAuth code flow.
#   5. The hosted_ui_domain in SSM matches the pool's domain.
#
# Usage:
#   bash scripts/devops/cognito_smoke.sh
#   bash scripts/devops/cognito_smoke.sh --region us-east-2 --profile qpress

set -euo pipefail

REGION="us-east-2"
PROFILE="qpress"
SSM_PREFIX="/saa/cognito"
EXPECTED_POOL_NAME="saa-users"
EXPECTED_CLIENT_NAME="saa-spa"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)  REGION="$2";  shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
cognito_smoke.sh — read-only verification of SAA Cognito bootstrap.

OPTIONS:
  --region REGION    (default: us-east-2)
  --profile PROFILE  (default: qpress)
EOF
      exit 0
      ;;
    *) echo "ERR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

ok()    { printf '  ✓ %s\n' "$*"; }
fail()  { printf '  ✗ %s\n' "$*" >&2; FAILED=1; }
note()  { printf '    %s\n' "$*"; }

FAILED=0

aws_q() {
  aws --profile "$PROFILE" --region "$REGION" "$@"
}

# --- check 1: SSM keys present ---
echo "[1/5] SSM params under ${SSM_PREFIX}"
EXPECTED_KEYS="region user_pool_id app_client_id hosted_ui_domain app_client_secret"
for key in $EXPECTED_KEYS; do
  full="${SSM_PREFIX}/${key}"
  if val="$(aws_q ssm get-parameter --name "$full" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null)" \
     && [[ -n "$val" && "$val" != "None" ]]; then
    if [[ "$key" == "app_client_secret" ]]; then
      ok "${full} present (value redacted, length=${#val})"
    else
      ok "${full} = ${val}"
    fi
  else
    fail "${full} MISSING"
  fi
done

# Bail early if any SSM key is missing — downstream checks require them.
if [[ "$FAILED" -ne 0 ]]; then
  echo
  echo "RESULT: FAIL — SSM params incomplete; cannot continue."
  exit 1
fi

USER_POOL_ID="$(aws_q ssm get-parameter --name "${SSM_PREFIX}/user_pool_id"  --query 'Parameter.Value' --output text)"
APP_CLIENT_ID="$(aws_q ssm get-parameter --name "${SSM_PREFIX}/app_client_id" --query 'Parameter.Value' --output text)"
HOSTED_UI_DOMAIN="$(aws_q ssm get-parameter --name "${SSM_PREFIX}/hosted_ui_domain" --query 'Parameter.Value' --output text)"
SSM_REGION="$(aws_q ssm get-parameter --name "${SSM_PREFIX}/region" --query 'Parameter.Value' --output text)"

# --- check 2: pool exists with expected name ---
echo "[2/5] Cognito User Pool resolves"
POOL_NAME="$(aws_q cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
  --query 'UserPool.Name' --output text 2>/dev/null || echo MISSING)"
if [[ "$POOL_NAME" == "$EXPECTED_POOL_NAME" ]]; then
  ok "pool ${USER_POOL_ID} → name='${POOL_NAME}'"
else
  fail "pool ${USER_POOL_ID} name='${POOL_NAME}' (expected '${EXPECTED_POOL_NAME}')"
fi

# --- check 3: organization custom attr ---
echo "[3/5] Custom attribute 'organization' present"
ORG_ATTR="$(aws_q cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
  --query "UserPool.SchemaAttributes[?Name=='custom:organization' || Name=='organization'].Name | [0]" \
  --output text 2>/dev/null || echo MISSING)"
if [[ "$ORG_ATTR" != "MISSING" && "$ORG_ATTR" != "None" && -n "$ORG_ATTR" ]]; then
  ok "attribute present: ${ORG_ATTR}"
else
  fail "custom attribute 'organization' NOT FOUND on pool"
fi

# --- check 4: app client OAuth config ---
echo "[4/5] App client OAuth config"
CLIENT_NAME="$(aws_q cognito-idp describe-user-pool-client \
  --user-pool-id "$USER_POOL_ID" --client-id "$APP_CLIENT_ID" \
  --query 'UserPoolClient.ClientName' --output text 2>/dev/null || echo MISSING)"
if [[ "$CLIENT_NAME" == "$EXPECTED_CLIENT_NAME" ]]; then
  ok "client ${APP_CLIENT_ID} → name='${CLIENT_NAME}'"
else
  fail "client ${APP_CLIENT_ID} name='${CLIENT_NAME}' (expected '${EXPECTED_CLIENT_NAME}')"
fi

OAUTH_FLOWS="$(aws_q cognito-idp describe-user-pool-client \
  --user-pool-id "$USER_POOL_ID" --client-id "$APP_CLIENT_ID" \
  --query 'UserPoolClient.AllowedOAuthFlows' --output text 2>/dev/null || echo MISSING)"
case "$OAUTH_FLOWS" in
  *code*) ok "OAuth flows: ${OAUTH_FLOWS}" ;;
  *)      fail "OAuth flows '${OAUTH_FLOWS}' missing 'code'" ;;
esac

# --- check 5: hosted UI domain ---
echo "[5/5] Hosted UI domain matches pool"
POOL_DOMAIN="$(aws_q cognito-idp describe-user-pool --user-pool-id "$USER_POOL_ID" \
  --query 'UserPool.Domain' --output text 2>/dev/null || echo MISSING)"
if [[ -n "$POOL_DOMAIN" && "$POOL_DOMAIN" != "None" ]]; then
  EXPECTED_FQDN="${POOL_DOMAIN}.auth.${SSM_REGION}.amazoncognito.com"
  if [[ "$HOSTED_UI_DOMAIN" == "$EXPECTED_FQDN" ]]; then
    ok "domain matches: ${HOSTED_UI_DOMAIN}"
  else
    fail "SSM hosted_ui_domain='${HOSTED_UI_DOMAIN}' but pool says '${EXPECTED_FQDN}'"
  fi
else
  fail "pool has no Hosted UI domain"
fi

echo
if [[ "$FAILED" -eq 0 ]]; then
  echo "RESULT: PASS — Cognito bootstrap is healthy."
  exit 0
else
  echo "RESULT: FAIL — fix above issues and re-run cognito_bootstrap.sh."
  exit 1
fi
