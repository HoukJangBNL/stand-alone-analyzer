# Qpress DB — Operations Runbook

> **Status**: Living doc. RDS + bastion + alembic 셋업이 끝난 시점 기준.
>
> **Audience**: 프로젝트 오너 (혼자 작업, 디테일은 잊는다는 가정).
>
> **Scope**: 일상적으로 RDS에 붙어서 psql/alembic 돌리는 절차. 스키마 자체는 [`docs/db-schema-v6.md`](./db-schema-v6.md) 참고.

---

## 1. 인프라 인벤토리

| 항목 | 값 |
|---|---|
| AWS profile / region | `qpress` / `us-east-2` |
| Default VPC | `vpc-053a4df895c279c84` |
| Subnet (bastion) | `subnet-0fe8558512beea68a` (us-east-2a, public) |
| RDS instance ID | `qpressdb` |
| RDS engine | PostgreSQL 17.4, `db.m7g.large`, Multi-AZ, private-only |
| RDS endpoint | `qpressdb.ch08y4ooqgmq.us-east-2.rds.amazonaws.com:5432` |
| RDS master user | `houk` |
| RDS master password | AWS Secrets Manager (auto-rotates, `--manage-master-user-password`) |
| RDS secret ARN | `arn:aws:secretsmanager:us-east-2:931886963315:secret:rds!db-beb90dd0-feef-45a5-b8b5-81af8d02e0d6-Cwxa1w` |
| App database | `qpress` (외 `postgres`/`rdsadmin`/`template0`/`template1`은 RDS 기본) |
| RDS SG | `sg-0972cbd26138773b5` (default) — 5432 inbound from bastion SG, rule `sgr-014d8b085d17d950a` |
| Bastion EC2 ID | `i-063165d449976b2e4` |
| Bastion type / AMI | `t4g.nano` / `ami-03834b8550547b809` (Amazon Linux 2023 ARM) |
| Bastion AZ / public IP | us-east-2a / `16.59.125.132` (⚠️ stop/start 시 변경됨) |
| Bastion SG | `sg-027b44698d395c3a3` (qpress-bastion-sg) — SSH 22 inbound from `130.199.243.190/32` |
| Bastion key | `~/.ssh/qpress-bastion.pem` (오너 Mac에만 존재, perms `600`) |
| Bastion keypair name | `qpress-bastion` (AWS 등록명) |

---

## 2. 일상 작업 — 터널 켜고 psql/alembic 쓰기

### 2.1 Bastion start

```bash
aws --profile qpress --region us-east-2 ec2 start-instances \
  --instance-ids i-063165d449976b2e4
```

### 2.2 새 public IP 확인

```bash
aws --profile qpress --region us-east-2 ec2 describe-instances \
  --instance-ids i-063165d449976b2e4 \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

### 2.3 (필요 시) 내 IP 바뀌었으면 bastion SG ingress 갱신

내 현재 IP:

```bash
curl -s https://api.ipify.org
```

기존 룰 revoke → 새 룰 authorize (CIDR `<NEW_IP>/32`):

```bash
aws --profile qpress --region us-east-2 ec2 revoke-security-group-ingress \
  --group-id sg-027b44698d395c3a3 \
  --protocol tcp --port 22 --cidr 130.199.243.190/32

aws --profile qpress --region us-east-2 ec2 authorize-security-group-ingress \
  --group-id sg-027b44698d395c3a3 \
  --protocol tcp --port 22 --cidr <NEW_IP>/32
```

> 갱신 후 위 표 `Bastion SG` CIDR도 같이 고쳐두기.

### 2.4 SSH 터널 (background)

`<new-public-ip>` 자리에 2.2 결과 넣기.

```bash
ssh -f -N -i ~/.ssh/qpress-bastion.pem \
  -L 5432:qpressdb.ch08y4ooqgmq.us-east-2.rds.amazonaws.com:5432 \
  ec2-user@<new-public-ip>
```

### 2.5 비밀번호를 env로만 (디스크에 쓰지 않기)

```bash
export PGPASSWORD=$(aws --profile qpress --region us-east-2 secretsmanager get-secret-value \
  --secret-id 'arn:aws:secretsmanager:us-east-2:931886963315:secret:rds!db-beb90dd0-feef-45a5-b8b5-81af8d02e0d6-Cwxa1w' \
  --query SecretString --output text | python3 -c "import sys, json; print(json.load(sys.stdin)['password'])")
```

### 2.6 psql

```bash
psql -h localhost -p 5432 -U houk -d qpress
```

### 2.7 alembic (프로젝트 루트, venv 활성화 상태)

```bash
SAA_DB_HOST=localhost SAA_DB_PORT=5432 SAA_DB_USER=houk SAA_DB_PASSWORD="$PGPASSWORD" SAA_DB_NAME=qpress \
  alembic current
```

### 2.8 작업 종료 — 터널 내리고 bastion 정지

```bash
pkill -f 'ssh.*qpressdb.ch08y4ooqgmq'

aws --profile qpress --region us-east-2 ec2 stop-instances \
  --instance-ids i-063165d449976b2e4
```

---

## 3. 마이그레이션 워크플로

- 파일 위치: `alembic/versions/`. 첫 리비전: `0001_initial_v6.py` (적용 완료).
- **Source of truth = [`docs/db-schema-v6.md`](./db-schema-v6.md).** 적용된 마이그레이션은 절대 수정하지 말고 새 revision으로 추가.
- **`--autogenerate` 쓰지 말 것.** GENERATED columns / composite FKs / partial indexes / ENUM types를 제대로 못 뽑는다. DDL은 손으로 적는다.
- 각 마이그레이션은 자체 트랜잭션 + 단일 commit 유지 (PG는 DDL in tx 지원).

### 명령어

```bash
# 새 리비전 (수동 DDL)
alembic revision -m "add foo"
# → upgrade()/downgrade() 직접 채우기

# 적용
alembic upgrade head

# 한 단계 롤백
alembic downgrade -1

# 현재 리비전
alembic current

# DB에 쓰지 않고 SQL만 출력
alembic upgrade head --sql
```

> alembic 명령은 모두 §2.5 `PGPASSWORD` + §2.7 env 변수 prefix가 필요.

> **CI guard.** PRs against `main` run [`alembic-drift.yml`](../.github/workflows/alembic-drift.yml)
> which spins up an ephemeral Postgres, applies migrations, then calls
> `scripts/check_alembic_drift.py`. If a model change ships without a
> migration, CI fails before merge.

---

## 4. 비용 / 자원 cleanup

- Bastion **stopped** → EBS root 8 GB gp3만 과금, 약 **$0.80/month**.
- Bastion **running** → t4g.nano on-demand, 약 **$3/month**.
- RDS `db.m7g.large` Multi-AZ가 비용 대부분을 차지 — 이 문서 범위 밖.

### Bastion 완전 제거 순서 (필요 시)

순서 중요: RDS SG inbound 먼저 제거 → bastion SG 삭제 → EC2 terminate.

```bash
# 1) RDS SG에서 bastion SG inbound 룰 제거
aws --profile qpress --region us-east-2 ec2 revoke-security-group-ingress \
  --group-id sg-0972cbd26138773b5 \
  --security-group-rule-ids sgr-014d8b085d17d950a

# 2) Bastion 종료
aws --profile qpress --region us-east-2 ec2 terminate-instances \
  --instance-ids i-063165d449976b2e4

# 3) Bastion SG 삭제 (terminate가 끝난 뒤)
aws --profile qpress --region us-east-2 ec2 delete-security-group \
  --group-id sg-027b44698d395c3a3
```

> Keypair `qpress-bastion`은 무료라 그냥 둬도 됨.

---

## 5. Trouble-shooting

- **`psql: connection refused on localhost:5432`** → 터널이 안 떠 있음. §2.4 다시 실행. 포트 확인은 zsh `/dev/tcp` 안 되니 bash로:
  ```bash
  bash -c 'exec 3<>/dev/tcp/127.0.0.1/5432 && echo OK'
  ```
- **`FATAL: password authentication failed`** → AWS가 비밀번호 자동 회전한 것. §2.5 다시 fetch. 시크릿 자체를 잃었다면:
  ```bash
  aws --profile qpress --region us-east-2 rds modify-db-instance \
    --db-instance-identifier qpressdb \
    --manage-master-user-password --apply-immediately
  ```
- **SSH `Permission denied (publickey)`** → `~/.ssh/qpress-bastion.pem` 권한 확인:
  ```bash
  chmod 600 ~/.ssh/qpress-bastion.pem
  ```
- **`alembic upgrade head` 가 멈춤** → 터널이 도중에 끊김. §2.4 재실행 후 다시.
- **`Operation not permitted` (EC2/RDS 호출 시)** → AWS profile이 default로 빠졌을 가능성. 모든 명령에 `--profile qpress` 붙었는지 확인.

---

## 6. Future-proofing notes

- Bastion public IP는 stop/start마다 바뀜 — CI에서 SSH 필요해지면 Elastic IP 붙이는 것 검토.
- 오너 집/사무실 IP가 변하면 §2.3 절차 그대로. 자주 바뀌면 스크립트화.
- 팀 합류 시: SSH key 배포 대신 **AWS SSM Session Manager** 또는 VPN 검토 (SG inbound 0개로 운영 가능). _아직 안 함, future option._
- 스키마 버전 = **v6**. Breaking change 생기면 `db-schema-v7.md` + 새 alembic revision으로 진행. `db-schema-v6.md`는 동결.
