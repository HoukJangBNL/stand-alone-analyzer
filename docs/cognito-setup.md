# Cognito Setup Runbook (W6.1)

> **Status**: bootstrap script + runbook ready. **No AWS resources have been created yet.** Pool ID / Client ID / Hosted UI domain in §1 are placeholders until §3 is run.
>
> **Audience**: 프로젝트 오너 (혼자 운영). 필요한 디테일 다 적어둠 — 잊어도 다시 따라 할 수 있게.
>
> **Scope**: AWS Cognito User Pool + App Client + Hosted UI Domain + SSM Parameter Store entries for the W6 auth/session feature. Pairs with `scripts/devops/cognito_bootstrap.sh` (idempotent) and `scripts/devops/cognito_smoke.sh` (read-only verify).

---

## 🚨 STOP — REQUIRES OWNER APPROVAL BEFORE RUN 🚨

This runbook describes commands that **CREATE BILLED AWS RESOURCES** in the production Qpress AWS account (`931886963315`, `us-east-2`).

**Before running anything in §3 (Apply), the owner MUST explicitly approve.** A devops agent acting on behalf of the owner MUST surface the approval prompt at the bottom of this section verbatim and wait for `yes` / `dry-run-first` / `no`.

A `--dry-run` of the bootstrap script is **safe** to run at any time — it makes only `sts:GetCallerIdentity` (read-only, optional) calls and prints the planned commands.

### Approval prompt (paste to owner)

```
About to create AWS resources in account 931886963315 (us-east-2):
  - Cognito User Pool: saa-users
      • Required attribute: email
      • Custom attribute:   organization (string, mutable)
      • Email verify:       CONFIRM_WITH_CODE (6-digit code)
      • Password policy:    8+ chars, upper + lower + digit (no symbol)
      • MFA:                OFF (optional — flip later via console)
      • Email sender:       default Cognito (no SES domain in v1)
  - App Client: saa-spa
      • Authorization Code Grant + PKCE
      • Scopes: openid email profile
      • Refresh token TTL: 30 days
      • Client SECRET generated (stored in SSM SecureString)
  - Hosted UI Domain: saa-prod.auth.us-east-2.amazoncognito.com
      (pool prefix "saa-prod" — globally-unique within Cognito; if taken,
       script fails and operator picks an alternative such as
       "saa-qpress" or "saa-${ACCOUNT_ID}".)
  - SSM SecureString: /saa/cognito/app_client_secret
  - SSM String x4:    /saa/cognito/{user_pool_id, app_client_id,
                                    hosted_ui_domain, region}

Cost: Cognito free tier = 50,000 MAU, then $0.0055/MAU.
      SSM Parameter Store standard tier = free up to 10,000 params.

Approve? (yes / dry-run-first / no)
```

---

## 1. Inventory (placeholders — filled by §3)

| 항목 | 값 |
|---|---|
| AWS profile / region | `qpress` / `us-east-2` |
| AWS account | `931886963315` |
| User Pool name | `saa-users` |
| User Pool ID | _TBD by §3 — written to `/saa/cognito/user_pool_id`_ |
| App Client name | `saa-spa` |
| App Client ID | _TBD by §3 — written to `/saa/cognito/app_client_id`_ |
| App Client SECRET | _SecureString in SSM at `/saa/cognito/app_client_secret`_ |
| Hosted UI domain prefix | `saa-prod` (proposed; final pick at §3) |
| Hosted UI FQDN | `<prefix>.auth.us-east-2.amazoncognito.com` |
| Email sender | Default Cognito sender (50/day limit) |
| MFA | OFF (optional — see §5 to enable) |
| Refresh token TTL | 30 days |
| ID/Access token TTL | 1 hour (Cognito default) |
| OAuth scopes | `openid email profile` |
| Allowed flows | `code` (Authorization Code Grant + PKCE) |
| Custom attribute | `organization` (string, mutable, optional) |

### SSM keys created by the bootstrap

| Key | Type | Description |
|---|---|---|
| `/saa/cognito/region` | String | `us-east-2` |
| `/saa/cognito/user_pool_id` | String | Pool ID, e.g. `us-east-2_AbCdEf123` |
| `/saa/cognito/app_client_id` | String | App client ID, e.g. `7abc...xyz` |
| `/saa/cognito/hosted_ui_domain` | String | FQDN, e.g. `saa-prod.auth.us-east-2.amazoncognito.com` |
| `/saa/cognito/app_client_secret` | **SecureString** | App client secret. Read with `--with-decryption`. |

---

## 2. Prerequisites

### 2.1 AWS CLI v2

```bash
aws --version
# Expected: aws-cli/2.x  (v1 is NOT supported — JSON arg parsing differs)
```

### 2.2 AWS profile `qpress`

The same profile used for `qpressdb` RDS ops (see `docs/db-ops.md`).

```bash
aws --profile qpress sts get-caller-identity
```

Expected: `"Account": "931886963315"`. If you get `Could not connect to the endpoint` or `InvalidClientTokenId`, check `~/.aws/credentials`.

### 2.3 IAM permissions

The owner's IAM user/role needs:

| Action | Resource | Why |
|---|---|---|
| `cognito-idp:CreateUserPool` | `*` | bootstrap |
| `cognito-idp:DescribeUserPool` | the pool | idempotency check + smoke test |
| `cognito-idp:ListUserPools` | `*` | idempotency check |
| `cognito-idp:CreateUserPoolClient` | the pool | bootstrap |
| `cognito-idp:DescribeUserPoolClient` | the client | idempotency check + smoke test |
| `cognito-idp:ListUserPoolClients` | the pool | idempotency check |
| `cognito-idp:CreateUserPoolDomain` | the pool | bootstrap |
| `ssm:PutParameter` | `arn:aws:ssm:us-east-2:931886963315:parameter/saa/cognito/*` | bootstrap |
| `ssm:GetParameter` / `GetParameters` / `GetParametersByPath` | same | smoke test + runtime |
| `sts:GetCallerIdentity` | `*` | bootstrap account-id verification |

**Teammate onboarding:** see §6 — minimal IAM policy that grants only the above.

### 2.4 Local sanity checks

```bash
bash -n scripts/devops/cognito_bootstrap.sh   # syntax check
bash -n scripts/devops/cognito_smoke.sh
```

(`shellcheck` 추천이지만 macOS 기본엔 없음. `brew install shellcheck` 하면 더 좋음.)

---

## 3. Bootstrap — dry-run, then apply

### 3.1 Dry-run (always run this first)

```bash
bash scripts/devops/cognito_bootstrap.sh \
  --region us-east-2 \
  --account-id 931886963315 \
  --dry-run
```

Output is the exact list of `aws ...` commands the script will run if you re-execute without `--dry-run`. **Read every line.** No AWS state-changing API is called in dry-run.

### 3.2 Apply (post-approval)

> **Owner approval required.** See the prompt at the top of this doc.

```bash
bash scripts/devops/cognito_bootstrap.sh \
  --region us-east-2 \
  --account-id 931886963315
```

Behavior:
- If a pool named `saa-users` already exists in `us-east-2`, the script reuses it (no new pool created).
- If an app client named `saa-spa` already exists in that pool, the script reuses it and reads its existing secret from `describe-user-pool-client`.
- If a Hosted UI domain is already attached to the pool, the script reuses it; otherwise it tries to create `saa-prod.auth.us-east-2.amazoncognito.com`. **Hosted UI prefixes are globally unique across all Cognito.** If `saa-prod` is taken, re-run with `--hosted-ui-prefix saa-qpress` (or another sensible alternative).
- For each SSM key: if it already exists with the same value, it is skipped. If it differs, the script **prompts** before overwriting. Use `--yes` for unattended reruns.

Exit code:
- `0` — success (or dry-run completed)
- `2` — bad arguments / missing AWS CLI
- `3` — an AWS API call failed mid-run; safe to re-run (idempotent)

### 3.3 Verify with the smoke test

```bash
bash scripts/devops/cognito_smoke.sh --region us-east-2 --profile qpress
```

Expected output ends with `RESULT: PASS`. The smoke test only makes read-only AWS calls (`ssm:GetParameter`, `cognito-idp:DescribeUserPool*`).

### 3.4 Update §1 with real values

After §3.2 succeeds, paste the output into §1's "TBD" rows and commit the doc update:

```bash
git add docs/cognito-setup.md
git commit -m "docs(devops): record cognito user pool + app client IDs"
```

### 3.5 Smoke-test sign-up (manual — operator)

Once the pool exists, sanity-check the email-verify path with a Gmail `+test` alias:

```bash
USER_POOL_ID="$(aws --profile qpress --region us-east-2 ssm get-parameter \
  --name /saa/cognito/user_pool_id --query 'Parameter.Value' --output text)"
APP_CLIENT_ID="$(aws --profile qpress --region us-east-2 ssm get-parameter \
  --name /saa/cognito/app_client_id --query 'Parameter.Value' --output text)"

aws --profile qpress --region us-east-2 cognito-idp sign-up \
  --client-id "$APP_CLIENT_ID" \
  --username 'youremail+saatest@gmail.com' \
  --password 'TempPassA1' \
  --user-attributes Name=email,Value='youremail+saatest@gmail.com'

# Cognito emails a 6-digit code; check inbox.
aws --profile qpress --region us-east-2 cognito-idp confirm-sign-up \
  --client-id "$APP_CLIENT_ID" \
  --username 'youremail+saatest@gmail.com' \
  --confirmation-code 123456

# (USER_PASSWORD_AUTH requires the client to have ALLOW_USER_PASSWORD_AUTH;
#  bootstrap enables ALLOW_USER_SRP_AUTH instead, which uses SRP. For the
#  smoke check, use admin-initiate-auth with USER_PASSWORD_AUTH on the pool
#  side, OR enable ALLOW_USER_PASSWORD_AUTH temporarily and revert afterwards.
#  Easiest: use the Hosted UI from a browser to verify login round-trip.)
```

Browser smoke (preferred, no CLI auth gymnastics):

```
https://<HOSTED_UI_FQDN>/login?client_id=<APP_CLIENT_ID>&response_type=code&scope=openid+email+profile&redirect_uri=http://localhost:5173/auth/callback
```

Sign in → Cognito redirects to the redirect_uri with `?code=...`. The code exchange itself is the W6.2 backend's job.

---

## 4. Parameter rotation policy

### 4.1 App client secret — rotate every 90 days

Cognito does not auto-rotate app client secrets. Set a calendar reminder for **90 days** after each apply.

```bash
USER_POOL_ID="$(aws --profile qpress --region us-east-2 ssm get-parameter \
  --name /saa/cognito/user_pool_id --query 'Parameter.Value' --output text)"
APP_CLIENT_ID="$(aws --profile qpress --region us-east-2 ssm get-parameter \
  --name /saa/cognito/app_client_id --query 'Parameter.Value' --output text)"

# Trigger Cognito to regenerate the secret.
NEW_SECRET="$(aws --profile qpress --region us-east-2 cognito-idp update-user-pool-client \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$APP_CLIENT_ID" \
  --refresh-token-validity 30 \
  --token-validity-units 'AccessToken=hours,IdToken=hours,RefreshToken=days' \
  --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --supported-identity-providers COGNITO \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client \
  --prevent-user-existence-errors ENABLED \
  --enable-token-revocation \
  --query 'UserPoolClient.ClientSecret' --output text)"

# Update SSM SecureString
aws --profile qpress --region us-east-2 ssm put-parameter \
  --name /saa/cognito/app_client_secret \
  --value "$NEW_SECRET" \
  --type SecureString \
  --overwrite

# Restart the FastAPI service so it re-reads SSM at startup.
sudo systemctl restart saa-api
```

> ⚠️ **Live secret rotation has a small auth blip.** The old secret is invalidated immediately on `update-user-pool-client`. If the API server is still holding the old value in memory, OAuth code-exchange POSTs will fail until restart. Schedule rotations during low-traffic windows.

### 4.2 SSM param values

Rotation cadence:

| Param | Rotate when | How |
|---|---|---|
| `/saa/cognito/app_client_secret` | every 90 days | §4.1 |
| `/saa/cognito/user_pool_id` | never (immutable) | n/a |
| `/saa/cognito/app_client_id` | never (immutable) | n/a |
| `/saa/cognito/hosted_ui_domain` | only if you change the prefix | redeploy via bootstrap with `--hosted-ui-prefix` |
| `/saa/cognito/region` | never | n/a |

### 4.3 IAM access audit

Quarterly: list everyone who has `cognito-idp:Update*` or `Delete*` on this pool. Trim aggressively.

```bash
aws --profile qpress iam list-policies --scope Local \
  --query "Policies[?contains(PolicyName,'cognito') || contains(PolicyName,'saa')].PolicyName"
```

---

## 5. Future toggles (intentionally OFF in v1)

Document these so the owner can flip them later without re-reading the SDK.

### 5.1 Enable MFA (optional → required)

```bash
aws --profile qpress --region us-east-2 cognito-idp set-user-pool-mfa-config \
  --user-pool-id "$USER_POOL_ID" \
  --mfa-configuration OPTIONAL \
  --software-token-mfa-configuration Enabled=true
```

(`OPTIONAL` means users can self-enroll TOTP. Switch to `ON` to make it mandatory.)

### 5.2 Switch to SES (custom From: address)

Default Cognito sender has a 50 emails/day limit and a `no-reply@verificationemail.com` From address. To upgrade:

1. Verify a domain in SES (separate runbook — out of scope here).
2. Update the user pool email config:

```bash
aws --profile qpress --region us-east-2 cognito-idp update-user-pool \
  --user-pool-id "$USER_POOL_ID" \
  --email-configuration \
    SourceArn=arn:aws:ses:us-east-2:931886963315:identity/your-domain.com,\
EmailSendingAccount=DEVELOPER,\
From='SAA <noreply@your-domain.com>'
```

### 5.3 Enable `ALLOW_USER_PASSWORD_AUTH`

Useful for backend-driven `/auth/callback` that needs `InitiateAuth` with username+password (instead of SRP). Adds `ALLOW_USER_PASSWORD_AUTH` to the client's explicit auth flows. The W6.2 plan uses Authorization Code Grant via Hosted UI, so this is **not** needed for v1; add only if the SPA's `/login` posts directly to `/auth/callback` and the backend chooses `USER_PASSWORD_AUTH` for the exchange.

---

## 6. Teammate onboarding — minimal IAM policy

Attach this customer-managed policy to a new IAM user/role to let them manage Cognito for SAA without giving full admin rights.

**Policy name suggestion:** `saa-cognito-manager`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CognitoIdpManage",
      "Effect": "Allow",
      "Action": [
        "cognito-idp:CreateUserPool",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:UpdateUserPool",
        "cognito-idp:ListUserPools",
        "cognito-idp:CreateUserPoolClient",
        "cognito-idp:DescribeUserPoolClient",
        "cognito-idp:UpdateUserPoolClient",
        "cognito-idp:ListUserPoolClients",
        "cognito-idp:CreateUserPoolDomain",
        "cognito-idp:DescribeUserPoolDomain",
        "cognito-idp:DeleteUserPoolDomain",
        "cognito-idp:SetUserPoolMfaConfig",
        "cognito-idp:SignUp",
        "cognito-idp:ConfirmSignUp",
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminUpdateUserAttributes",
        "cognito-idp:AdminDisableUser",
        "cognito-idp:ListUsers"
      ],
      "Resource": "arn:aws:cognito-idp:us-east-2:931886963315:userpool/*"
    },
    {
      "Sid": "SsmCognitoParams",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
        "ssm:PutParameter",
        "ssm:DescribeParameters"
      ],
      "Resource": "arn:aws:ssm:us-east-2:931886963315:parameter/saa/cognito/*"
    },
    {
      "Sid": "SsmDescribeAll",
      "Effect": "Allow",
      "Action": ["ssm:DescribeParameters"],
      "Resource": "*"
    },
    {
      "Sid": "StsWhoAmI",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

Note: `ssm:DescribeParameters` does not support resource-level conditions; the second `Sid` is needed even though the first restricts `PutParameter`.

---

## 7. Rollback / tear-down

> **DESTRUCTIVE.** Only run if you intend to delete the pool and all users in it.

```bash
USER_POOL_ID="$(aws --profile qpress --region us-east-2 ssm get-parameter \
  --name /saa/cognito/user_pool_id --query 'Parameter.Value' --output text)"

# 1. Detach the Hosted UI domain (must be deleted before the pool).
DOMAIN="$(aws --profile qpress --region us-east-2 cognito-idp describe-user-pool \
  --user-pool-id "$USER_POOL_ID" --query 'UserPool.Domain' --output text)"
if [[ -n "$DOMAIN" && "$DOMAIN" != "None" ]]; then
  aws --profile qpress --region us-east-2 cognito-idp delete-user-pool-domain \
    --domain "$DOMAIN" --user-pool-id "$USER_POOL_ID"
fi

# 2. Delete the pool (cascades to clients).
aws --profile qpress --region us-east-2 cognito-idp delete-user-pool \
  --user-pool-id "$USER_POOL_ID"

# 3. Tear down SSM params.
for k in region user_pool_id app_client_id hosted_ui_domain app_client_secret; do
  aws --profile qpress --region us-east-2 ssm delete-parameter \
    --name "/saa/cognito/$k" || true
done
```

To re-create from scratch: re-run §3 from the top.

---

## 8. Tradeoffs and choices (record for posterity)

| Choice | Alternative | Why we picked this |
|---|---|---|
| Email verify = **CONFIRM_WITH_CODE** | CONFIRM_WITH_LINK | Simpler UX in a custom `/login` SPA — no need to handle a verification redirect. Code can be entered into the existing `SignupPage` confirm-code field (W6.5). |
| Password policy = **8/upper/lower/digit, no symbol** | 12+ with symbol (the older sketch suggested) | Matches the explicit W6.1 brief override. Lower friction for non-technical users; offset by future MFA. **Tradeoff:** weaker against brute force. Mitigation: Cognito locks after repeated failures; consider raising to 12 + symbol if we ever expose the pool publicly. |
| MFA = **OFF** | OPTIONAL or ON | v1 has a single internal user base; mandatory MFA is friction. Owner can flip to OPTIONAL via §5.1 without code changes. |
| Email sender = **default Cognito** | SES with verified domain | 50/day default cap is enough for a small team. SES setup is a separate runbook; defer until needed. |
| Hosted UI prefix = **saa-prod** | `saa-${ACCOUNT_ID}` | Shorter, more memorable. Globally unique across Cognito — if taken, override with `--hosted-ui-prefix`. |
| OAuth flow = **Authorization Code + PKCE** | Implicit grant | Implicit grant is deprecated in OAuth 2.1. Code+PKCE is the standard for SPAs and supports refresh tokens. |
| Refresh token TTL = **30 days** | 1 day / 1 year | Balances "user logs in monthly" with "compromised token has limited blast radius". `users.deactivated_at` (W6.3) provides immediate revocation. |
| App client SECRET in **SSM SecureString** | Secrets Manager | SSM is free; Secrets Manager rotates automatically but adds $0.40/secret/month. Manual rotation per §4.1 is acceptable for a single-secret deployment. |

---

## 9. Cross-references

- Plan: `docs/superpowers/plans/2026-05-21-W6-auth-session-v2.md` §"Sub-plan W6.1"
- Bootstrap script: `scripts/devops/cognito_bootstrap.sh`
- Smoke test: `scripts/devops/cognito_smoke.sh`
- Schema migration consuming the user identity: `docs/db-schema-v7.md` (W6.0)
- Backend dependency that reads the SSM params: W6.2 (forthcoming, `src/flake_analysis/api/auth/`)
- Existing AWS ops conventions: `docs/db-ops.md` §1
