# Phase 4 IAM Grant — Owner How-to

> Status: Phase 4 코드/스크립트/launch template/머지 완료. 잔여는 SSM DB password 저장 (e2e 워커 부팅용) — hjang IAM에 `ssm:PutParameter` 권한 부족.
> hjang은 자기 자신에게 IAM 정책 부여 불가 (`iam:AttachUserPolicy` 없음). 루트 또는 IAM 관리자 계정에서 부여 필요.

## 두 가지 옵션

### Option B (recommended, 최소 path) — SSM만

**PM 권장.** Budgets/SNS/EventBridge는 비용 폭주 안전망인데, spot 워커는 작업 끝나면 자동 종료라 정상 운용에선 안 울림. 셋업 부담 큰 비해 보상이 적음.

- **부여 정책**: `scripts/aws/iam-policy-phase4-hjang-minimal.json` (SSM 한 줄만)
- **클로즈하는 항목**: #190 P4.5-e2e (워커 부팅 → SAM 실행)
- **스킵하는 항목**: AWS Budgets 알람, SNS 이메일, EventBridge spot-interrupt audit
- **대체 안전망**: AWS 콘솔 EC2 instances 페이지를 북마크하고 작업 후 1회 확인

### Option A (full) — 5개 Sid

원안. 비용 폭주 조기경보까지 활성화.

- **부여 정책**: `scripts/aws/iam-policy-phase4-hjang.json`
- **클로즈하는 항목**: #190 e2e + Budgets/SNS/Events apply
- **트레이드오프**: 5개 Sid 부여 + sam-budget.sh 실행 + 이메일 confirm-link 클릭 (오너 액션 3단계)

## What this unlocks

| Action | Option | Closes |
|---|---|---|
| Owner가 SSM `/qpress-sam/db_password` (SecureString) 저장 | A & B | #190 P4.5-e2e (real worker boot) |
| `./scripts/aws/sam-budget.sh` 실행 | A only | Budget alert + SNS topic |
| `./scripts/aws/sam-eventbridge.sh` 실행 | A only | spot interrupt audit telemetry |

## Method 1 — Inline policy (recommended, 콘솔)

1. AWS 콘솔 로그인 (루트 or IAM admin).
2. **IAM → Users → hjang → Add permissions → Create inline policy**.
3. **JSON 탭** 클릭.
4. 파일 내용 붙여넣기:
   - **Option B**: `scripts/aws/iam-policy-phase4-hjang-minimal.json`
   - **Option A**: `scripts/aws/iam-policy-phase4-hjang.json`
5. Review → 정책명 `qpress-sam-phase4-extras` → Create policy.

## Method 2 — CLI (관리자 계정 credentials로)

```bash
# Option B (recommended)
aws iam put-user-policy \
  --user-name hjang \
  --policy-name qpress-sam-phase4-extras \
  --policy-document file://scripts/aws/iam-policy-phase4-hjang-minimal.json

# Option A (full)
aws iam put-user-policy \
  --user-name hjang \
  --policy-name qpress-sam-phase4-extras \
  --policy-document file://scripts/aws/iam-policy-phase4-hjang.json
```

## Verification

### Option B
```bash
aws ssm describe-parameters --region us-east-2 \
  --filters "Key=Name,Values=/qpress-sam/" || echo "empty — OK before owner stores password"
```

### Option A (추가)
```bash
# Budgets read
aws budgets describe-budgets --account-id <ACCOUNT_ID> --region us-east-1

# SNS topic create dry-check
aws sns list-topics --region us-east-1 | grep qpress-sam || echo "no topic yet — OK"

# Events
aws events list-rules --region us-east-2 --name-prefix qpress-sam || echo "no rule yet — OK"
```

부여 실패하면 access denied → AWS 콘솔에서 IAM admin 계정으로 재시도.

## After grant — 시퀀스

### Option B
1. **Owner**가 콘솔 → Systems Manager → Parameter Store → Create parameter
   - Name: `/qpress-sam/db_password`
   - Type: SecureString
   - Region: us-east-2
   - Value: RDS `qpress` 사용자 비번 (오너만 알고 있음)
2. **PM**이 `tests/e2e/test_sam_pipeline.py` (Playwright) 실행 → 워커 부팅 → 비번 fetch → SAM 실행 → #190 클로즈.

### Option A (B에 추가)
3. **PM**이 `OWNER_EMAIL=<email>` 환경변수 세팅 후 `./scripts/aws/sam-budget.sh` 실행 → SNS topic + budget 생성.
4. **Owner**가 메일로 도착한 SNS confirm 링크 클릭.
5. **PM**이 `./scripts/aws/sam-eventbridge.sh` 실행 → spot interrupt audit rule 생성.

## Why owner-only steps remain

- **SSM `/qpress-sam/db_password`**: RDS DB password는 disk-write 금지(운영 룰), PM 메모리에 없음. 콘솔에서 owner가 SecureString으로 직접 PUT.
- **SNS confirm link**: AWS 보안상 메일 수신자만 클릭 가능. CLI 우회 불가.

## Scope rationale (Option A 전체)

- `Resource` 가 모두 `qpress-sam-*` / `/qpress-sam/*` 로 제한 — hjang가 다른 프로젝트 리소스 건드릴 수 없음.
- `ce:GetCostAndUsage` 만 `*` (Cost Explorer API의 resource-level granularity 한계).
- `iam:*` 없음 — 권한 escalation 불가.

## Why Budgets는 nice-to-have지 must-have가 아닌가

- spot g6e.xlarge $0.30/h. 정상 작업은 분 단위. 작업 끝나면 launch template + spot interrupt handler가 자동 종료.
- 위험 시나리오: 워커 부팅 후 무한루프로 stop 명령 안 받음 → 며칠 돌면 ~$50 청구. **Budgets는 이걸 첫날에 잡아주는 cap 알람**.
- 대체: 콘솔 EC2 instances 페이지에서 작업 후 5분 내 1회 확인. 한 명 쓰는 시스템에선 충분.
- **Phase 4 D6 결정은 "안전망 추가" 의미였지 "차단" 아님**. SSM만으로도 운용 가능.
