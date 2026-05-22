#!/usr/bin/env bash
# cognito_bootstrap.sh — idempotent AWS Cognito bootstrap for SAA (W6.1)
#
# Creates (or skips if present): User Pool + custom attribute, App Client (with
# secret), Hosted UI Domain, and writes 5 SSM Parameter Store entries.
#
# REQUIRES OWNER APPROVAL BEFORE RUN.  Until the W6 approval gate fires, this
# script must be invoked with --dry-run only.
#
# Constraints (sub-plan W6.1):
#   - Region:      us-east-2
#   - AWS profile: qpress (overridable via --profile)
#   - Password policy: 8+ chars, upper+lower+digit, no symbol required
#   - MFA: OFF (optional later via console)
#   - Email verify: CODE flow (simpler UX than link)
#   - Email sender: default Cognito (no SES domain in v1)
#   - App client: Authorization Code Grant + PKCE, refresh TTL 30 days
#   - Custom attribute: organization (string, mutable)
#
# Idempotency:
#   - User Pool detected by Name == saa-users (within --region).
#   - App Client detected by ClientName == saa-spa within the pool.
#   - Hosted UI Domain detected by exact prefix; conflicts surface a clear error.
#   - SSM params: if a key already exists with a different value, the script
#     PROMPTS before overwriting (skip with --yes for non-interactive reruns).
#
# Macports/Homebrew compatibility:
#   - No GNU-only sed/grep flags.
#   - No bashisms beyond `set -euo pipefail`, arrays, and `[[`.
#   - awscli v2 required (uses --query / --output text).
#
# Usage:
#   bash scripts/devops/cognito_bootstrap.sh --dry-run
#   bash scripts/devops/cognito_bootstrap.sh --region us-east-2 --account-id 931886963315
#
# Exit codes:
#   0  success (or dry-run completed)
#   1  generic failure
#   2  bad arguments / missing prerequisites
#   3  AWS API call failed mid-run (state may be partial — re-run is safe)

set -euo pipefail

# -------- defaults --------
REGION="us-east-2"
PROFILE="qpress"
ACCOUNT_ID=""
POOL_NAME="saa-users"
CLIENT_NAME="saa-spa"
HOSTED_UI_PREFIX_DEFAULT="saa-prod"
HOSTED_UI_PREFIX="${HOSTED_UI_PREFIX_DEFAULT}"
DRY_RUN=0
ASSUME_YES=0
REFRESH_DAYS=30
SSM_PREFIX="/saa/cognito"

usage() {
  cat <<'EOF'
cognito_bootstrap.sh — bootstrap AWS Cognito User Pool + App Client + SSM params.

OPTIONS:
  --region REGION           AWS region (default: us-east-2)
  --profile PROFILE         AWS profile (default: qpress)
  --account-id ID           Expected AWS account id; verified via sts:GetCallerIdentity
  --hosted-ui-prefix STR    Cognito Hosted UI domain prefix (default: saa-prod)
  --dry-run                 Print every command that would run; do NOT call AWS write APIs
  --yes                     Non-interactive: overwrite existing SSM params without prompting
  -h, --help                Show this help

EXAMPLES:
  # Print the plan without touching AWS:
  bash scripts/devops/cognito_bootstrap.sh --dry-run

  # Real run after owner approval:
  bash scripts/devops/cognito_bootstrap.sh --region us-east-2 --account-id 931886963315
EOF
}

# -------- arg parse --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)            REGION="$2"; shift 2 ;;
    --profile)           PROFILE="$2"; shift 2 ;;
    --account-id)        ACCOUNT_ID="$2"; shift 2 ;;
    --hosted-ui-prefix)  HOSTED_UI_PREFIX="$2"; shift 2 ;;
    --dry-run)           DRY_RUN=1; shift ;;
    --yes)               ASSUME_YES=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    *) echo "ERR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

# -------- helpers --------
log()  { printf '[cognito-bootstrap] %s\n' "$*" >&2; }
warn() { printf '[cognito-bootstrap][WARN] %s\n' "$*" >&2; }
die()  { printf '[cognito-bootstrap][ERR ] %s\n' "$*" >&2; exit 1; }

# run_aws: print + (unless dry-run) execute an aws command. Captures stdout for
# the caller via the global RUN_OUT variable.
RUN_OUT=""
run_aws() {
  local desc="$1"; shift
  log "→ ${desc}"
  # Display the planned invocation. Args with spaces (none in our usage) would
  # word-wrap, but the actual call below uses "$@" so quoting is preserved.
  printf '  $ aws --profile %s --region %s' "$PROFILE" "$REGION" >&2
  for _arg in "$@"; do printf ' %s' "$_arg" >&2; done
  printf '\n' >&2
  if [[ "$DRY_RUN" -eq 1 ]]; then
    RUN_OUT=""
    return 0
  fi
  RUN_OUT="$(aws --profile "$PROFILE" --region "$REGION" "$@")" || {
    warn "command failed (see stderr above); aborting"
    exit 3
  }
}

confirm_or_exit() {
  local msg="$1"
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    log "[--yes] auto-confirmed: ${msg}"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[--dry-run] would prompt: ${msg}"
    return 0
  fi
  printf 'CONFIRM: %s [y/N] ' "$msg" >&2
  local ans=""
  read -r ans || true
  case "$ans" in
    y|Y|yes|YES) return 0 ;;
    *) die "user declined: ${msg}" ;;
  esac
}

# -------- prerequisite checks --------
command -v aws >/dev/null 2>&1 || die "aws CLI not found in PATH"

log "configuration:"
log "  region          = ${REGION}"
log "  profile         = ${PROFILE}"
log "  pool name       = ${POOL_NAME}"
log "  client name     = ${CLIENT_NAME}"
log "  hosted UI       = ${HOSTED_UI_PREFIX}"
log "  refresh TTL     = ${REFRESH_DAYS} days"
log "  ssm prefix      = ${SSM_PREFIX}"
log "  dry-run         = ${DRY_RUN}"

if [[ -n "$ACCOUNT_ID" ]]; then
  log "verifying caller identity matches --account-id ${ACCOUNT_ID}"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    actual="$(aws --profile "$PROFILE" --region "$REGION" sts get-caller-identity --query Account --output text)"
    [[ "$actual" == "$ACCOUNT_ID" ]] || die "account mismatch: expected ${ACCOUNT_ID}, got ${actual}"
    log "  ok — account ${actual}"
  else
    log "  [dry-run] skipping sts:GetCallerIdentity"
  fi
fi

# ============================================================
# Step 1: Detect or create the User Pool
# ============================================================
USER_POOL_ID=""
log "step 1: locate or create User Pool '${POOL_NAME}'"

# list-user-pools is paginated; --max-results 60 covers our needs (we expect <5).
if [[ "$DRY_RUN" -eq 0 ]]; then
  USER_POOL_ID="$(aws --profile "$PROFILE" --region "$REGION" cognito-idp list-user-pools \
    --max-results 60 \
    --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" \
    --output text 2>/dev/null || true)"
  if [[ "$USER_POOL_ID" == "None" || -z "$USER_POOL_ID" ]]; then
    USER_POOL_ID=""
  fi
fi

if [[ -n "$USER_POOL_ID" ]]; then
  log "  → existing pool found: ${USER_POOL_ID} (skipping create)"
else
  run_aws "create-user-pool ${POOL_NAME}" cognito-idp create-user-pool \
    --pool-name "${POOL_NAME}" \
    --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":false,"TemporaryPasswordValidityDays":7}}' \
    --auto-verified-attributes email \
    --username-attributes email \
    --mfa-configuration OFF \
    --account-recovery-setting 'RecoveryMechanisms=[{Priority=1,Name=verified_email}]' \
    --verification-message-template 'DefaultEmailOption=CONFIRM_WITH_CODE' \
    --schema 'Name=email,AttributeDataType=String,Required=true,Mutable=true' \
              'Name=organization,AttributeDataType=String,Required=false,Mutable=true' \
    --query 'UserPool.Id' \
    --output text
  if [[ "$DRY_RUN" -eq 0 ]]; then
    USER_POOL_ID="$RUN_OUT"
    log "  → created pool: ${USER_POOL_ID}"
  else
    USER_POOL_ID="<pool-id-placeholder>"
  fi
fi

# ============================================================
# Step 2: Detect or create the App Client
# ============================================================
APP_CLIENT_ID=""
APP_CLIENT_SECRET=""
log "step 2: locate or create App Client '${CLIENT_NAME}' in pool '${USER_POOL_ID}'"

if [[ "$DRY_RUN" -eq 0 && -n "$USER_POOL_ID" ]]; then
  APP_CLIENT_ID="$(aws --profile "$PROFILE" --region "$REGION" cognito-idp list-user-pool-clients \
    --user-pool-id "$USER_POOL_ID" --max-results 60 \
    --query "UserPoolClients[?ClientName=='${CLIENT_NAME}'].ClientId | [0]" \
    --output text 2>/dev/null || true)"
  if [[ "$APP_CLIENT_ID" == "None" || -z "$APP_CLIENT_ID" ]]; then
    APP_CLIENT_ID=""
  fi
fi

if [[ -n "$APP_CLIENT_ID" ]]; then
  log "  → existing client found: ${APP_CLIENT_ID} (skipping create)"
  run_aws "describe-user-pool-client (read existing secret)" cognito-idp describe-user-pool-client \
    --user-pool-id "$USER_POOL_ID" --client-id "$APP_CLIENT_ID" \
    --query 'UserPoolClient.ClientSecret' --output text
  if [[ "$DRY_RUN" -eq 0 ]]; then
    APP_CLIENT_SECRET="$RUN_OUT"
  fi
else
  run_aws "create-user-pool-client ${CLIENT_NAME}" cognito-idp create-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-name "${CLIENT_NAME}" \
    --generate-secret \
    --refresh-token-validity "${REFRESH_DAYS}" \
    --token-validity-units 'AccessToken=hours,IdToken=hours,RefreshToken=days' \
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --supported-identity-providers COGNITO \
    --allowed-o-auth-flows code \
    --allowed-o-auth-scopes openid email profile \
    --allowed-o-auth-flows-user-pool-client \
    --prevent-user-existence-errors ENABLED \
    --enable-token-revocation \
    --query 'UserPoolClient.{Id:ClientId,Secret:ClientSecret}' \
    --output text
  if [[ "$DRY_RUN" -eq 0 ]]; then
    # `text` output for the dict is "<id>\t<secret>"
    APP_CLIENT_ID="$(printf '%s\n' "$RUN_OUT" | awk '{print $1}')"
    APP_CLIENT_SECRET="$(printf '%s\n' "$RUN_OUT" | awk '{print $2}')"
    log "  → created client: ${APP_CLIENT_ID}"
  else
    APP_CLIENT_ID="<client-id-placeholder>"
    APP_CLIENT_SECRET="<client-secret-placeholder>"
  fi
fi

# ============================================================
# Step 3: Detect or create the Hosted UI Domain
# ============================================================
HOSTED_UI_DOMAIN_VALUE=""
log "step 3: ensure Hosted UI domain prefix '${HOSTED_UI_PREFIX}' on pool"

if [[ "$DRY_RUN" -eq 0 && -n "$USER_POOL_ID" ]]; then
  EXISTING_DOMAIN="$(aws --profile "$PROFILE" --region "$REGION" cognito-idp describe-user-pool \
    --user-pool-id "$USER_POOL_ID" \
    --query 'UserPool.Domain' --output text 2>/dev/null || true)"
  if [[ "$EXISTING_DOMAIN" != "None" && -n "$EXISTING_DOMAIN" ]]; then
    HOSTED_UI_DOMAIN_VALUE="${EXISTING_DOMAIN}.auth.${REGION}.amazoncognito.com"
    log "  → pool already has domain: ${HOSTED_UI_DOMAIN_VALUE} (skipping create)"
  fi
fi

if [[ -z "$HOSTED_UI_DOMAIN_VALUE" ]]; then
  run_aws "create-user-pool-domain ${HOSTED_UI_PREFIX}" cognito-idp create-user-pool-domain \
    --domain "${HOSTED_UI_PREFIX}" \
    --user-pool-id "$USER_POOL_ID"
  HOSTED_UI_DOMAIN_VALUE="${HOSTED_UI_PREFIX}.auth.${REGION}.amazoncognito.com"
  log "  → created Hosted UI: ${HOSTED_UI_DOMAIN_VALUE}"
fi

# ============================================================
# Step 4: Write SSM Parameter Store entries
# ============================================================
log "step 4: write SSM parameters under ${SSM_PREFIX}"

# put_param NAME VALUE TYPE DESC
put_param() {
  local name="$1" value="$2" ptype="$3" desc="$4"

  local existing=""
  if [[ "$DRY_RUN" -eq 0 ]]; then
    existing="$(aws --profile "$PROFILE" --region "$REGION" ssm get-parameter \
      --name "$name" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || true)"
  fi

  if [[ -n "$existing" && "$existing" != "None" ]]; then
    if [[ "$existing" == "$value" ]]; then
      log "  • ${name} already up-to-date — skip"
      return 0
    fi
    confirm_or_exit "overwrite SSM param ${name} (existing differs)?"
  fi

  # Mask the secret in logs
  local printed_value="$value"
  if [[ "$ptype" == "SecureString" ]]; then
    printed_value="<redacted>"
  fi
  log "  • put-parameter ${name} (${ptype}) = ${printed_value}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi

  aws --profile "$PROFILE" --region "$REGION" ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type "$ptype" \
    --description "$desc" \
    --overwrite \
    --output text >/dev/null
}

put_param "${SSM_PREFIX}/region"            "${REGION}"                   "String"       "SAA Cognito region"
put_param "${SSM_PREFIX}/user_pool_id"      "${USER_POOL_ID}"             "String"       "SAA Cognito User Pool ID"
put_param "${SSM_PREFIX}/app_client_id"     "${APP_CLIENT_ID}"            "String"       "SAA Cognito App Client ID"
put_param "${SSM_PREFIX}/hosted_ui_domain"  "${HOSTED_UI_DOMAIN_VALUE}"   "String"       "SAA Cognito Hosted UI domain"
put_param "${SSM_PREFIX}/app_client_secret" "${APP_CLIENT_SECRET}"        "SecureString" "SAA Cognito App Client secret"

log "done."
log "summary:"
log "  USER_POOL_ID      = ${USER_POOL_ID}"
log "  APP_CLIENT_ID     = ${APP_CLIENT_ID}"
log "  HOSTED_UI_DOMAIN  = ${HOSTED_UI_DOMAIN_VALUE}"
log "  REGION            = ${REGION}"
log "next: run scripts/devops/cognito_smoke.sh to verify (read-only)."
