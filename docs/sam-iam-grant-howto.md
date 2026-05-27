# Phase 4 IAM Grant — Owner How-to

> Status: Phase 4 코드/스크립트/launch template/머지 완료. 잔여는 AWS Budgets/SNS/EventBridge apply + SSM DB password 저장 — hjang IAM에 권한 부족.
> hjang은 자기 자신에게 IAM 정책 부여 불가 (`iam:AttachUserPolicy` 없음). 루트 또는 IAM 관리자 계정에서 부여 필요.

## What this unlocks

| Action | Closes |
|---|---|
| `./scripts/aws/sam-budget.sh` 실행 | P4.5 budget infra apply |
| `./scripts/aws/sam-eventbridge.sh` 실행 | P4.4 spot interrupt audit telemetry |
| Owner가 SSM `/qpress-sam/db_password` (SecureString) 저장 | #190 P4.5-e2e (real worker boot) |

## Method A — Inline policy (recommended, 최소권한)

1. AWS 콘솔 로그인 (루트 or IAM admin).
2. **IAM → Users → hjang → Add permissions → Create inline policy**.
3. **JSON 탭** 클릭.
4. `scripts/aws/iam-policy-phase4-hjang.json` 내용 붙여넣기.
5. Review → 정책명 `qpress-sam-phase4-extras` → Create policy.

## Method B — CLI (관리자 계정 credentials로)

```bash
aws iam put-user-policy \
  --user-name hjang \
  --policy-name qpress-sam-phase4-extras \
  --policy-document file://scripts/aws/iam-policy-phase4-hjang.json
```

## Verification

부여 후 hjang credentials로 다음 명령이 성공해야 함:

```bash
# Budgets read (sam-budget.sh가 이걸 먼저 호출)
aws budgets describe-budgets --account-id <ACCOUNT_ID> --region us-east-1

# SNS topic create dry-check
aws sns list-topics --region us-east-1 | grep qpress-sam || echo "no topic yet — OK"

# Events
aws events list-rules --region us-east-2 --name-prefix qpress-sam || echo "no rule yet — OK"

# SSM
aws ssm describe-parameters --region us-east-2 --filters "Key=Name,Values=/qpress-sam/" || echo "empty — OK"
```

부여 실패하면 access denied → AWS 콘솔에서 IAM admin 계정으로 재시도.

## After grant — PM이 자동 실행할 시퀀스

1. `./scripts/aws/sam-budget.sh` (OWNER_EMAIL 환경변수 필요) → SNS topic + budget 생성 → 오너 메일로 confirm 링크 도착 → **오너 클릭 필요**.
2. `./scripts/aws/sam-eventbridge.sh` → spot interrupt audit rule 생성.
3. 오너가 콘솔 → SSM Parameter Store → `/qpress-sam/db_password` (SecureString, us-east-2)에 RDS qpress 사용자 비번 저장. **PM은 비번 모르므로 콘솔 직접 작업 필수.**
4. PM이 `_ensure_gpu_worker` 트리거하는 통합 e2e 테스트로 #190 클로즈.

## Why owner-only at step 3

RDS DB password는 disk-write 금지 (운영 룰). PM 메모리에도 없음. 콘솔에서 owner가 SecureString으로 직접 PUT.

## Scope rationale

- `Resource` 가 모두 `qpress-sam-*` / `/qpress-sam/*` 로 제한 — hjang가 다른 프로젝트 리소스 건드릴 수 없음.
- `ce:GetCostAndUsage` 만 `*` (Cost Explorer API의 resource-level granularity 한계).
- `iam:*` 없음 — 권한 escalation 불가.
