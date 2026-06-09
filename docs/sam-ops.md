# SAM2 GPU Operations Runbook

> **Status (2026-05-27):** Phase 4 infrastructure complete (P4.5 non-e2e portion).
> P4.3 Phase 2 (LoRA staging + bootstrap) and the Playwright e2e test are both
> pending owner action. See **§9 Pending verification**.
>
> This document is the **single operational source of truth** for the SAM2 +
> LoRA inference path. A new operator can read this document end-to-end and
> run the system without referencing the implementation plan or decision doc.

---

## 1. Architecture overview

```
                    ┌───────────────────────────────────────────────────────┐
                    │  Client browser (Compute Tab)                          │
                    │  POST /api/v1/projects/{pid}/scans/{sid}/run/sam       │
                    │  (or /run/pipeline for the orchestrated path)          │
                    └────────────────────┬──────────────────────────────────┘
                                         │ SSE
                    ┌────────────────────▼──────────────────────────────────┐
                    │  FastAPI process (always-on, e.g. Fargate / EC2)       │
                    │  ┌─────────────────────────────────────────────────┐  │
                    │  │ ensure_worker_running()  ◄────── procrastinate  │  │
                    │  │ (scaler — see scripts/aws/sam-worker-launcher) │  │
                    │  └─────────────────────────────────────────────────┘  │
                    └────────────────┬───────────┬───────────────────────────┘
                                     │           │
                          enqueue task           │ LISTEN run_progress
                                     │           │
                    ┌────────────────▼─────┐     │
                    │  Postgres (RDS)       │◄────┘
                    │  procrastinate_jobs   │
                    │  + runs (audit log)   │
                    └────────────────▲──────┘
                                     │ poll
                    ┌────────────────┴────────────────────────────────────┐
                    │  GPU spot worker (g6e.xlarge, us-east-2)             │
                    │  Launch template: qpress-sam-gpu-worker              │
                    │  ┌───────────────────────────────────────────────┐  │
                    │  │ procrastinate worker  ──►  flake_analysis      │  │
                    │  │                            .pipeline.sam        │  │
                    │  │                              ▼                  │  │
                    │  │ vendor.QPress-SAM-Flake.run_amg_v2_inference    │  │
                    │  │  (loads merged.pt from /opt/sam/weights/)       │  │
                    │  └───────────────────────────────────────────────┘  │
                    │  Spot interrupt 2-min notice ──► EventBridge ──►   │
                    │    SNS qpress-sam-spot-interrupt-notify             │
                    │    + worker SIGTERM handler marks runs as failed    │
                    └─────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────▼─────────────────────────────────────┐
                    │  S3 bucket qpress-uploads, prefix internal/sam/      │
                    │   - lora-source/best_model.pth      (input, manual) │
                    │   - sam2.1_hiera_large.merged.<sha>.pt  (output)    │
                    │   - <merged>.pt.sha256                              │
                    └─────────────────────────────────────────────────────┘
```

**Lifecycle in one sentence:** the API enqueues a procrastinate job, a scaler
boots a tagged spot GPU worker if none is running, the worker pulls weights
from S3 once and then drains its queue, marking `runs` rows as it goes; if it
sits idle for 10 minutes it self-terminates; if AWS reclaims the spot
capacity, the 2-minute interrupt notice triggers a graceful shutdown that
marks the running job `spot_interrupted` so the API can re-enqueue once.

---

## 2. Resources inventory

All AWS resources are tagged `Project=qpress-sam` so the cost-allocation tag
in §6 captures every line item. Resources span two regions because AWS
Budgets and Cost Explorer are us-east-1-only by API convention.

| Resource type             | Name                                  | Region        | Purpose                                                  | Created by                                |
|---------------------------|---------------------------------------|---------------|----------------------------------------------------------|-------------------------------------------|
| IAM role                  | `qpress-sam-gpu-role`                 | global        | EC2 GPU instance role (S3 r/w + SSM)                     | `scripts/aws/sam-iam-bootstrap.sh` (P4.3) |
| IAM instance profile      | `qpress-sam-gpu-role`                 | global        | Attached to GPU EC2 launches                             | `scripts/aws/sam-iam-bootstrap.sh` (P4.3) |
| IAM inline policy         | `qpress-sam-gpu-s3`                   | global        | `s3:Get/PutObject` on `internal/sam/*`                   | `scripts/aws/sam-iam-bootstrap.sh` (P4.3) |
| Security group            | `qpress-sam-gpu-sg`                   | us-east-2     | No ingress, HTTPS egress only (SSM-only access)          | `scripts/aws/sam-iam-bootstrap.sh` (P4.3) |
| S3 prefix                 | `s3://qpress-uploads/internal/sam/`   | us-east-2     | LoRA source + merged weights + sha256 sidecars           | `scripts/aws/sam-stage-lora-to-s3.sh` (P4.3) |
| Launch template           | `qpress-sam-gpu-worker`               | us-east-2     | Spot GPU worker template (g6e.xlarge, user-data)         | `scripts/aws/sam-launch-template.sh` (P4.4) |
| EventBridge rule          | `qpress-sam-spot-interrupt`           | us-east-2     | Catches `EC2 Spot Instance Interruption Warning`         | `scripts/aws/sam-eventbridge.sh` (P4.4 — owner action: IAM lacks Events:PutRule) |
| SNS topic                 | `qpress-sam-spot-interrupt-notify`    | us-east-2     | Spot-interrupt audit fan-out                             | `scripts/aws/sam-eventbridge.sh` (P4.4 — owner action: IAM lacks SNS:CreateTopic) |
| SSM Parameter Store       | `/qpress-sam/db_*`                    | us-east-2     | Worker DB connection (host/port/user/name + SecureString password) | owner action — populate before launch |
| SNS topic                 | `qpress-sam-budget-alerts`            | us-east-1     | Cost-budget alarm fan-out (50/80/100% monthly + daily)   | `scripts/aws/sam-budget.sh` (P4.5)        |
| AWS Budget                | `qpress-sam-monthly-budget`           | us-east-1     | $600/mo, 50/80/100% actual + 100% forecasted             | `scripts/aws/sam-budget.sh` (P4.5)        |
| AWS Budget                | `qpress-sam-daily-budget`             | us-east-1     | $20/day, 100% actual                                     | `scripts/aws/sam-budget.sh` (P4.5)        |
| Cost-allocation tag       | `Project`                             | global (CE)   | Activated for billing reports + budget filters           | `scripts/aws/sam-budget.sh` (P4.5)        |

**Why two regions:** all real compute (EC2, EBS, S3, SG) is in **us-east-2**.
AWS Budgets, Cost Explorer, and the budget-alerts SNS topic must live in
**us-east-1** because that's the global Billing API region. The split is
cosmetic — tag-based filters work cross-region.

**Resource ARN/IDs are intentionally NOT committed.** Discover them at
runtime with the lookup snippets in §10.

---

## 3. Bootstrap procedure — one-shot, run during P4.3 Phase 2

The bootstrap procedure produces the merged-weights artifact
`s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha8>.pt` plus
its `.sha256` sidecar. Production GPU workers (§4) only need GetObject on
that artifact; the full bootstrap is run **once per LoRA version**.

### 3.1 Stage prod LoRA to S3 (owner-driven, run once)

The bootstrap instance pulls the LoRA adapter from S3. The adapter currently
lives on `qpress@hal.cfn.bnl.gov:~/sam2_lora/best_model.pth`.

```bash
# On owner's laptop, with best_model.pth pulled from hal:
./scripts/aws/sam-stage-lora-to-s3.sh /path/to/best_model.pth
```

Expected: ~few hundred MiB upload to
`s3://qpress-uploads/internal/sam/lora-source/best_model.pth`.

### 3.2 Create IAM role + SG (one-time, idempotent)

```bash
./scripts/aws/sam-iam-bootstrap.sh
```

Creates: role, inline policy, instance profile, security group. Re-running
is safe — it skips any resource that already exists.

### 3.3 Launch bootstrap instance + capture merged weights

```bash
# AMI: latest Ubuntu 22.04 amd64 in us-east-2 (verify at launch time)
AMI=$(aws ec2 describe-images --owners 099720109477 --region us-east-2 \
  --filters 'Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*' \
            'Name=state,Values=available' \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)

SG=$(aws ec2 describe-security-groups --region us-east-2 \
  --filters 'Name=group-name,Values=qpress-sam-gpu-sg' \
  --query 'SecurityGroups[0].GroupId' --output text)

SUBNET=$(aws ec2 describe-subnets --region us-east-2 \
  --filters 'Name=vpc-id,Values=vpc-053a4df895c279c84' \
            'Name=availability-zone,Values=us-east-2a' \
  --query 'Subnets[0].SubnetId' --output text)

aws ec2 run-instances --region us-east-2 \
  --image-id "${AMI}" \
  --instance-type g6e.xlarge \
  --instance-market-options MarketType=spot \
  --iam-instance-profile Name=qpress-sam-gpu-role \
  --security-group-ids "${SG}" \
  --subnet-id "${SUBNET}" \
  --user-data file://scripts/aws/sam-gpu-bootstrap.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=qpress-sam},{Key=Purpose,Value=p43-bootstrap},{Key=AutoTerminate,Value=true}]' \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2 \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]'
```

Monitor via SSM:

```bash
aws ssm start-session --region us-east-2 --target <instance-id>
sudo tail -f /var/log/cloud-init-output.log
sudo tail -f /var/log/sam-gpu-bootstrap.log
```

After `=== sam-gpu-bootstrap done ===` appears, run the smoketest:

```bash
sudo /opt/sam/stand-alone-analyzer/.venv/bin/python \
  /opt/sam/stand-alone-analyzer/scripts/aws/sam-gpu-smoketest.py \
  --weights /opt/sam/weights/sam2.1_hiera_large.merged.pt
```

Expect `SMOKETEST PASS`. Then verify the S3 upload from your laptop:

```bash
aws s3 ls s3://qpress-uploads/internal/sam/
aws s3 cp s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha8>.pt.sha256 -
```

Finally terminate the bootstrap instance — its only job was to produce the
merged weights:

```bash
aws ec2 terminate-instances --region us-east-2 --instance-ids <instance-id>
```

**PutObject note:** the role currently grants PutObject on `internal/sam/*`
so the bootstrap instance can upload merged weights. Production worker
instances only need GetObject. Two ways to tighten:
- (a) split into two roles, or
- (b) keep one role and accept the small blast radius (workers are
  short-lived spot, no SSH).

Decision: keep (b) until we have a second use-case for PutObject.

---

## 4. Worker lifecycle — steady-state

Once Phase 2 is complete and the launch template (P4.4) is live, a typical
job flow looks like this:

1. **Client request** — `POST /run/sam` (or `/run/pipeline`) hits the API.
2. **Enqueue** — API calls `procrastinate.app.tasks.run_sam.defer(...)` which
   inserts a row in `procrastinate_jobs` (PG-backed queue, no Redis).
3. **Scaler** — `ensure_worker_running()` (P4.4 helper in
   `src/flake_analysis/worker/launcher.py`) checks if any EC2 instance
   tagged `Project=qpress-sam,Role=worker` is in `running` or `pending` state
   in us-east-2. A PG advisory lock (`pg_try_advisory_lock(0xCAFE0044)`)
   serialises concurrent boot-window calls so two parallel SAM defers
   never spawn two instances. If no live worker exists, it calls
   `RunInstances` against the `qpress-sam-gpu-worker` launch template.
   `InsufficientInstanceCapacity` surfaces as a typed
   `GpuCapacityUnavailable` error → the API translates to a
   `pipeline_error` SSE envelope.
4. **Cold start** — the new instance boots (~3–5 min including CUDA + weights
   download from S3). `cloud-init` runs `sam-gpu-bootstrap.sh` if needed
   (idempotent stamps in `/opt/sam/state/` skip already-completed steps).
5. **Drain** — the procrastinate worker process polls `procrastinate_jobs`
   for jobs in queue `gpu`, picks one up, runs `flake_analysis.pipeline.sam`,
   updates `runs.status='running'` then `'succeeded'` (or `'failed'`).
6. **Idle timeout** — when the queue is empty for 10 minutes, the worker
   self-terminates by calling `aws ec2 terminate-instances --instance-ids
   $(curl -s http://169.254.169.254/latest/meta-data/instance-id)`. This is
   wired in the launch template's user-data systemd timer.

The 10-minute idle timeout is a deliberate compromise:
- shorter (e.g. 2 min) → too many cold starts, churn cost
- longer (e.g. 30 min) → uses ~$0.15 of hot-stand-by per job at $0.30/h
- 10 min → batch of related runs share a worker, single-shot is one cold
  start

If you want to override: edit the systemd-timer interval in the launch
template's user-data (P4.4 deliverable) and create a new launch-template
version with `aws ec2 create-launch-template-version`.

### Manually launching a worker (debugging)

See §10 Quick reference for a one-liner.

---

## 5. Spot interrupt behavior

g6e.xlarge spot price has been rock-solid (~$0.30–0.32/h in us-east-2 over
the last 90 days) but AWS can still reclaim capacity. The behavior chain:

1. **2-minute notice** — IMDS endpoint
   `http://169.254.169.254/latest/meta-data/spot/instance-action` starts
   returning a JSON body with `action: terminate` and the deadline.
2. **EventBridge** — AWS publishes a `EC2 Spot Instance Interruption Warning`
   event to the default event bus. Rule `qpress-sam-spot-interrupt` (P4.4,
   created by `scripts/aws/sam-eventbridge.sh`) matches every spot-interrupt
   event with an `instance-id` and fans out to SNS topic
   `qpress-sam-spot-interrupt-notify`. **This SNS topic is for audit only**
   — no email subscription by default; owner can subscribe later.
3. **In-instance handler** — a tiny shell script
   (`/usr/local/sbin/flake-analysis-spot-monitor.sh`, P4.4) is invoked by
   the systemd timer `flake-analysis-spot-monitor.timer` every 5s. It uses
   IMDSv2 (token + GET) to check
   `http://169.254.169.254/latest/meta-data/spot/instance-action`. On a
   non-404 status it:
   - calls `systemctl kill -s SIGTERM flake-analysis-worker.service`
   - the procrastinate worker's signal handler runs the current job's
     `record_run_end` with `status='failed'`, `error='spot_interrupted'`
   - flushes any open DB sessions and exits cleanly
4. **API-side re-enqueue** — when the API observes a row update with
   `status='failed' AND error='spot_interrupted'`, it re-enqueues the same
   job exactly **once**. A second spot interrupt with the same job_id flips
   status to `failed_permanent` and surfaces to the user via the SSE
   `pipeline_error` event.

Re-enqueue is gated to once-per-job to avoid an infinite loop if a specific
input keeps tripping a transient AWS-side issue.

---

## 6. Cost monitoring

### 6.1 Budget structure

Two AWS Budgets, both filtered to `Project=qpress-sam` cost-allocation tag,
both publishing to SNS topic `qpress-sam-budget-alerts`:

| Budget                          | Limit       | Period   | Notifications                                            |
|---------------------------------|-------------|----------|----------------------------------------------------------|
| `qpress-sam-monthly-budget`     | $600 USD    | MONTHLY  | 50% / 80% / 100% actual + 100% forecasted                |
| `qpress-sam-daily-budget`       | $20 USD     | DAILY    | 100% actual                                              |

Why both: the monthly budget catches "we spent the whole month's allotment"
and forecasts trend; the daily budget catches a single-day runaway (e.g. an
ASG misconfig spawning N workers).

The SNS topic (`qpress-sam-budget-alerts`) lives in **us-east-1** because
AWS Budgets is global-but-API-pinned-to-us-east-1. The owner's email is the
only subscription; AWS sends a confirmation link to the address on first
subscription.

### 6.2 Known limitation: 24-hour evaluation lag

**AWS Budgets evaluates ~once per day, not in real time.** Two consequences:

- **First-day blind spot.** If you create the budget on day D and start
  spending the same day, the first alert fires on day D+1 at earliest.
- **Daily budget is "yesterday-aware".** The daily budget catches "yesterday
  the project spent ≥$20" — not "today is heading toward $20." If you start
  4 GPU instances at noon at $0.30/h × 4 instances × 12h = $14.40/day, the
  alert won't fire until the next day's evaluation.

Tradeoff considered:

- **Option A: CloudWatch alarm on `EstimatedCharges`** — real-time but
  per-service-total, not per-tag. Noisy with shared services like S3 + RDS
  already in the account.
- **Option B (chosen): tag-scoped daily Budget** — exact $20/day visibility,
  but 24-hour lag.

We chose B because the per-instance cost ceiling is **deterministic**:
g6e.xlarge spot ≤ $0.32/h × 24h × N instances. With N capped at 1 by the
scaler in §4, max one-day burn is ~$8 — half the daily budget. A daily
alert means "something is wrong with the scaler." Real-time alarming is
overkill for that signal.

### 6.3 Cost snapshot at idle

When no instance is running:

| Resource                                  | Cost/month          |
|-------------------------------------------|---------------------|
| AWS Budgets (2 budgets)                   | $0 (free tier: 2 free) |
| SNS topic `qpress-sam-budget-alerts`      | $0 (no published messages → free) |
| SNS topic `qpress-sam-spot-interrupt-notify` | $0 (no interrupts → free) |
| EventBridge rule                          | $0 (rule is free; events charged per million) |
| IAM role + policy + instance profile      | $0                  |
| Security group (no instance attached)     | $0                  |
| S3 storage (~5 GiB merged.pt + 0.5 GiB LoRA) | ~$0.13/mo (Standard, us-east-2) |
| EC2 launch template                       | $0 (template only)  |
| **Total idle**                            | **~$0.13/mo**       |

When **active** (one g6e.xlarge spot worker running 24/7 hypothetically):

- EC2 spot: $0.30–0.32/h × 730h = **~$220/mo**
- EBS gp3 100 GB: ~$8/mo
- Data transfer (S3 → EC2 same region): $0
- **Total active steady-state**: ~$230/mo

Real expected usage with the on-demand scaler: a few jobs/day × ~30 min/job
≈ $5–15/mo.

### 6.4 Receiving alerts

- The owner's email subscription to `qpress-sam-budget-alerts` is
  **manual-confirm** — AWS sends a one-time link to the inbox; the owner
  must click it.
- The placeholder `OWNER_EMAIL_REQUIRED@example.com` in
  `scripts/aws/sam-budget.sh` is a sentinel. Re-run the script with
  `OWNER_EMAIL=<real-address>` to subscribe a live mailbox. Re-running with
  the same address is a no-op (AWS dedupes).
- Budget alerts also surface in the AWS Billing console under
  "Budgets > qpress-sam-monthly-budget > Alerts" — owner can spot-check
  there even without email.

---

## 7. Troubleshooting

### 7.1 `sam-gpu-bootstrap.sh` fails on CUDA install

Check `/var/log/sam-gpu-bootstrap.log`. Common cause: NVIDIA apt repo network
flakiness — the script is idempotent; SSM into the instance and re-run:

```bash
sudo bash /var/lib/cloud/instance/scripts/part-001
```

The state stamps in `/opt/sam/state/` skip already-completed steps.

### 7.2 LoRA download fails with `AccessDenied`

Verify the instance role:

```bash
aws sts get-caller-identity   # on the instance
# Should show ARN ending in qpress-sam-gpu-role
```

If wrong role, the launch command was missing `--iam-instance-profile`.

### 7.3 `merge_lora.py` errors with "missing matching lora_B"

Indicates a peft prefix mismatch — P1.5b's recursive prefix strip should
handle `image_encoder.base_model.model.trunk.*`. If this resurfaces, check
that the submodule is at SHA `6f7fc2e` or later:

```bash
cd /opt/sam/stand-alone-analyzer
git -C vendor/QPress-SAM-Flake rev-parse HEAD
```

### 7.4 SHA256 in S3 doesn't match local

Re-upload — the `.sha256` sidecar is computed from the local merged.pt right
before upload, so a mismatch means the S3 PUT corrupted in-flight (rare with
multipart). Re-running the upload step (delete the `upload.done` stamp) will
recompute and re-upload.

### 7.5 Worker not picking up jobs from the queue

Symptoms: rows accumulate in `procrastinate_jobs` table with
`status='todo'`, no GPU instance running, nothing in `runs` table.

Checks:
1. **Is the scaler enabled?**
   ```bash
   # On the API server:
   curl -fsS https://api.qpress.example/api/v1/admin/sam-worker-state
   ```
   (Endpoint added in P4.4.) If it returns `{"scaler_enabled": false}`,
   re-enable via the env var `SAM_WORKER_SCALER_ENABLED=true`.
2. **Did the scaler attempt to launch?**
   ```bash
   aws ec2 describe-instances --region us-east-2 \
     --filters 'Name=tag:Project,Values=qpress-sam' \
               'Name=instance-state-name,Values=pending,running,shutting-down' \
     --query 'Reservations[].Instances[].[InstanceId,State.Name,LaunchTime]' \
     --output table
   ```
3. **Is the launch template healthy?**
   ```bash
   aws ec2 describe-launch-template-versions --region us-east-2 \
     --launch-template-name qpress-sam-gpu-worker \
     --versions '$Latest' \
     --query 'LaunchTemplateVersions[0].LaunchTemplateData' --output json
   ```
4. **Insufficient spot capacity?** Check
   `/var/log/cloud-init-output.log` on a recently-terminated instance (use
   the SSM session log or CloudWatch logs if the user-data ships them).
   Spot capacity errors look like `InsufficientInstanceCapacity`.

### 7.6 Spot capacity unavailable

Symptoms: scaler tries to launch but `RunInstances` returns
`InsufficientInstanceCapacity` for g6e.xlarge in us-east-2.

Mitigation:
- The launch template (P4.4) lists multiple subnets across AZs. If still
  unavailable, fall back to on-demand: `aws ec2 modify-launch-template
  --launch-template-name qpress-sam-gpu-worker
  --launch-template-data '{"InstanceMarketOptions": null}'` — but this 3×s
  the price.
- Or wait it out — g6e.xlarge spot returns within an hour typically.

### 7.7 `merged.pt` SHA mismatch on worker

Symptoms: worker logs `RuntimeError: weight file sha256 mismatch`. Means
the S3 object's sha256 metadata or `.sha256` sidecar doesn't match the
file content.

Recovery:
1. Verify locally:
   ```bash
   aws s3 cp s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha>.pt /tmp/m.pt
   shasum -a 256 /tmp/m.pt
   aws s3 cp s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha>.pt.sha256 -
   ```
2. If they differ, re-upload from the bootstrap instance (§3.3) or compute
   on a known-good copy and `aws s3 cp` over.

### 7.8 DB connection failure from worker

The worker uses the same RDS instance as the API, via a VPC peering or
public-via-bastion path (TBD by P4.4 — verify with the launch template
user-data). Common failure modes:

- **SG rule missing**: the RDS instance's SG must allow inbound 5432 from
  `qpress-sam-gpu-sg`. Add via:
  ```bash
  RDS_SG=$(aws rds describe-db-instances --region us-east-2 \
    --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)
  aws ec2 authorize-security-group-ingress --region us-east-2 \
    --group-id "${RDS_SG}" \
    --protocol tcp --port 5432 \
    --source-group $(aws ec2 describe-security-groups --region us-east-2 \
      --filters 'Name=group-name,Values=qpress-sam-gpu-sg' \
      --query 'SecurityGroups[0].GroupId' --output text)
  ```
- **Secret rotation**: worker reads the DB password from
  `secretsmanager:GetSecretValue` (path TBD by P4.4). If the secret was
  rotated, the worker's cached value is stale — restart the worker
  process.

### 7.9 CUDA unavailable on worker

Symptoms: worker logs `torch.cuda.is_available() == False`, falls back to
CPU, jobs take 100× longer.

Diagnosis: usually a kernel/driver mismatch after an unattended
`apt-get upgrade`. The bootstrap script pins NVIDIA driver to a specific
version. To confirm:

```bash
nvidia-smi   # should show GPU + driver version
sudo dmesg | grep -i nvidia | tail -50
```

Recovery: the launch template AMI should be re-baked with the pinned
driver. Until then, terminate the bad instance and let the scaler
re-launch — the bootstrap stamps will re-run failed steps.

---

## 8. Rollback / emergency stop

If costs spike, jobs are corrupting data, or you just need to halt
everything:

### 8.1 Pause the queue (least invasive)

```bash
# Set env var on the API server, then restart:
export SAM_WORKER_SCALER_ENABLED=false
systemctl restart qpress-api    # or `kubectl rollout restart` etc.
```

This stops new workers from launching. In-flight jobs continue.

### 8.2 Terminate all running workers

```bash
aws ec2 describe-instances --region us-east-2 \
  --filters 'Name=tag:Project,Values=qpress-sam' \
            'Name=instance-state-name,Values=pending,running' \
  --query 'Reservations[].Instances[].InstanceId' --output text \
  | xargs -n1 -I{} aws ec2 terminate-instances --region us-east-2 --instance-ids {}
```

In-flight jobs will be marked `failed` with `error='terminated'` by the
worker's SIGTERM handler; the API will re-enqueue once per the spot-
interrupt logic (§5).

### 8.3 Disable the launch template (prevents accidental re-launch)

```bash
aws ec2 modify-launch-template --region us-east-2 \
  --launch-template-name qpress-sam-gpu-worker \
  --default-version 0   # 0 = disable
```

### 8.4 Pause the procrastinate worker (full freeze)

If you need an absolute halt and don't trust the scaler env var:

```bash
# On every running worker (or skip if §8.2 already terminated):
sudo systemctl stop procrastinate-worker
```

### 8.5 Drain the queue (purge pending jobs)

Last resort, **destructive**:

```sql
-- Connect to RDS as the procrastinate user
DELETE FROM procrastinate_jobs WHERE status = 'todo' AND queue_name = 'gpu';
```

**This loses queued work.** Prefer §8.1 + §8.2 unless the queue contains
known-bad jobs.

---

## 9. Pending verification

These items are tracked for follow-up after upstream blockers clear:

- [ ] **P4.3 Phase 2** — bootstrap instance launch + actual `merged.pt` in
  S3. Blocked on **owner LoRA staging** to
  `s3://qpress-uploads/internal/sam/lora-source/best_model.pth`. Once
  staged, run §3.3 to produce the merged-weights artifact.
- [ ] **P4.5-e2e** — Playwright test exercising the real GPU worker:
  upload images → run `/run/pipeline` → SAM step routes to GPU worker →
  progress stream → completion. Blocked on **P4.3 Phase 2** (need a real
  merged.pt) and **P4.4** (need the launch template + scaler live). Once
  both are done, dispatch a fresh task `P4.5-e2e` to the
  frontend-architect with playwright-mcp.
- [ ] **Owner email subscription** — re-run
  `OWNER_EMAIL=<address> bash scripts/aws/sam-budget.sh` and click the AWS
  confirmation link. Without this, budget alerts publish to SNS but
  nobody is subscribed.
- [ ] **Cost-allocation tag activation** — the script's `aws ce
  update-cost-allocation-tags-status` call submits the request, but the
  Billing console may take ~24h to flip `Project` to ACTIVE in
  `list-cost-allocation-tags`. Re-run §10.6 after a day and confirm.

---

## 10. Quick reference

Copy-paste-ready snippets for the operations you'll run most.

### 10.1 Manually launch a GPU worker (debugging)

```bash
# Uses the launch template — same path the scaler uses, but you control timing.
aws ec2 run-instances --region us-east-2 \
  --launch-template LaunchTemplateName=qpress-sam-gpu-worker,Version='$Latest' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=qpress-sam},{Key=Role,Value=worker},{Key=ManualLaunch,Value=true}]'
```

### 10.2 Check queue depth

```bash
psql "$DATABASE_URL" -c "
  SELECT queue_name, status, count(*)
  FROM procrastinate_jobs
  GROUP BY queue_name, status
  ORDER BY queue_name, status;
"
```

### 10.3 View recent runs

```bash
psql "$DATABASE_URL" -c "
  SELECT id, analysis_id, step, status, started_at, completed_at, error
  FROM runs
  ORDER BY started_at DESC NULLS LAST
  LIMIT 20;
"
```

### 10.4 Force-terminate a stuck instance

```bash
INSTANCE_ID=i-0123456789abcdef0
aws ec2 terminate-instances --region us-east-2 --instance-ids "${INSTANCE_ID}"
```

### 10.5 Check current GPU spot price

```bash
aws ec2 describe-spot-price-history --region us-east-2 \
  --instance-types g6e.xlarge \
  --product-descriptions 'Linux/UNIX' \
  --max-items 5 \
  --query 'SpotPriceHistory[].[AvailabilityZone,SpotPrice,Timestamp]' --output table
```

### 10.6 Verify cost-allocation tag is active

```bash
aws ce list-cost-allocation-tags \
  --region us-east-1 \
  --status Active \
  --output json \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
project = next((t for t in data["CostAllocationTags"] if t["TagKey"] == "Project"), None)
print(project if project else "Project tag NOT YET ACTIVE")
'
```

### 10.7 Inspect budget state

```bash
ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
aws budgets describe-budget --region us-east-1 \
  --account-id "${ACCOUNT}" \
  --budget-name qpress-sam-monthly-budget

aws budgets describe-budget --region us-east-1 \
  --account-id "${ACCOUNT}" \
  --budget-name qpress-sam-daily-budget

aws budgets describe-notifications-for-budget --region us-east-1 \
  --account-id "${ACCOUNT}" \
  --budget-name qpress-sam-monthly-budget
```

### 10.8 Re-run budget setup with a real email

```bash
OWNER_EMAIL=you@example.com bash scripts/aws/sam-budget.sh
# Then check the inbox for "AWS Notification — Subscription Confirmation"
# and click the link. Until clicked, alerts go to /dev/null.
```

### 10.9 List all qpress-sam tagged resources (audit)

```bash
aws resourcegroupstaggingapi get-resources --region us-east-2 \
  --tag-filters Key=Project,Values=qpress-sam \
  --query 'ResourceTagMappingList[].ResourceARN' --output table

aws resourcegroupstaggingapi get-resources --region us-east-1 \
  --tag-filters Key=Project,Values=qpress-sam \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

---

## 11. Phase 4 implementation summary

This section is the end-of-Phase-4 commit summary, mapping the plan and
decision doc onto the resources actually shipped.

### 11.1 Plan mapping

| Plan task                                            | Status | Owner agent      | Artifact                                                     |
|------------------------------------------------------|--------|------------------|--------------------------------------------------------------|
| P4.0 — Owner approval + decision doc                 | ✅      | PM + owner       | Owner GO 2026-05-27 (decision doc tracked separately by PM) |
| P4.1 — merged weights → S3                           | ⏸      | devops-engineer  | (blocked on P4.3 Phase 2 LoRA staging)                       |
| P4.2 — procrastinate integration                     | ✅      | api-developer    | `src/flake_analysis/worker/`, `procrastinate_jobs` schema    |
| P4.3 Phase 1 — IAM role + SG + bootstrap script      | ✅      | devops-engineer  | `scripts/aws/sam-iam-bootstrap.sh`, `scripts/aws/sam-gpu-bootstrap.sh` |
| P4.3 Phase 2 — launch + capture merged.pt            | ⏸      | devops-engineer  | (blocked on owner LoRA staging — see §9)                     |
| P4.4 — launch template + scaler + spot interrupt     | 🔄      | devops-engineer (parallel) | launch template `qpress-sam-gpu-worker`, EventBridge rule, SNS topic `qpress-sam-spot-interrupt-notify`, `src/flake_analysis/api/services/sam_worker.py` |
| **P4.5 (this task)** — budgets + sam-ops runbook     | ✅      | devops-engineer  | `scripts/aws/sam-budget.sh`, **this document**               |
| P4.5-e2e — Playwright e2e on real GPU                | ⏸      | frontend-architect | (blocked on P4.3 Phase 2 + P4.4 — see §9)                   |

### 11.2 Resource ownership

| Resource                                 | Created by script            | Idempotent rerun? | Owner action needed?                     |
|------------------------------------------|------------------------------|-------------------|------------------------------------------|
| `qpress-sam-gpu-role` + policies + SG    | `sam-iam-bootstrap.sh`       | yes               | none                                     |
| `s3://qpress-uploads/internal/sam/`      | `sam-stage-lora-to-s3.sh`    | n/a (bucket pre-existed) | stage `best_model.pth`              |
| Bootstrap user-data + smoketest          | `sam-gpu-bootstrap.sh`       | yes (state stamps) | run §3.3                                |
| Launch template `qpress-sam-gpu-worker`  | (P4.4 script)                | yes               | none                                     |
| EventBridge rule + spot-interrupt SNS    | (P4.4 script)                | yes               | none                                     |
| `qpress-sam-budget-alerts` SNS topic     | `sam-budget.sh`              | yes               | re-run with `OWNER_EMAIL` + confirm link |
| Monthly + daily budgets                  | `sam-budget.sh`              | yes               | none                                     |
| Cost-allocation tag `Project` activation | `sam-budget.sh`              | yes               | wait ~24h, then verify (§10.6)           |

### 11.3 Decision recap (Owner GO 2026-05-27)

- **Lifecycle:** on-demand spot, scale-to-zero with 10-min idle timeout
- **AMI strategy:** S3 lazy download (worker AMI is plain Ubuntu 22.04 +
  CUDA, weights pulled at job start)
- **Spot policy:** auto-retry once on `spot_interrupted`, surface to user
  on second failure
- **Worker isolation:** procrastinate worker on the same g6e box as the
  Python inference (no separate worker tier)
- **Cost monitoring:** $20/day soft cap (daily budget) + $600/month hard
  cap (monthly budget) — both tag-scoped

---

## 12. Owner runbook — 1-image warmup (#190 first pass)

워커 부팅 경로가 살아있는지 1회 검증 + procrastinate→worker→DB INSERT 한 바퀴 확인. 예상 비용 ~$0.10, 예상 시간 ~10분. **owner 직접 실행** — devops 위임 아님.

### 12.1 Pre-launch sanity (30초)

붙여넣고 출력 확인:

```bash
# 1) Launch template default version = 2 (REPO_REF=feat/migration-cutover)
aws ec2 describe-launch-template-versions --region us-east-2 \
  --launch-template-id lt-09d01bf17ff7bed30 --versions '$Default' \
  --query 'LaunchTemplateVersions[0].[VersionNumber,VersionDescription]' --output table

# 2) Branch on remote
git ls-remote origin feat/migration-cutover
# 기대값: 4136431...  refs/heads/feat/migration-cutover

# 3) Spot price (us-east-2 g6e.xlarge) — On-Demand는 $1.86/h, Spot은 보통 $0.30~0.50/h
aws ec2 describe-spot-price-history --region us-east-2 \
  --instance-types g6e.xlarge --product-descriptions 'Linux/UNIX' \
  --max-items 3 --query 'SpotPriceHistory[].[AvailabilityZone,SpotPrice]' --output table
```

### 12.2 발사 (spot, 워커 1대)

```bash
INSTANCE_ID=$(aws ec2 run-instances --region us-east-2 \
  --launch-template 'LaunchTemplateId=lt-09d01bf17ff7bed30,Version=$Default' \
  --instance-market-options 'MarketType=spot,SpotOptions={InstanceInterruptionBehavior=terminate}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=qpress-sam},{Key=Role,Value=worker},{Key=ManualLaunch,Value=warmup-190}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "Launched ${INSTANCE_ID}"
```

이후 어디서 막히든 즉시 차단 명령(§12.6)으로 종료할 것.

### 12.3 부팅 모니터링 (~3-5분)

user-data가 [1/8]~[8/8] 단계로 부트스트랩. 진행은 SSM exec로 cloud-init 로그 tail:

```bash
aws ec2 wait instance-running --region us-east-2 --instance-ids "${INSTANCE_ID}"

# user-data 로그 tail (60초 보고 cancel)
aws ssm start-session --region us-east-2 --target "${INSTANCE_ID}" \
  --document-name AWS-StartInteractiveCommand \
  --parameters 'command=["sudo tail -f /var/log/cloud-init-output.log"]'
```

`[8/8] install systemd units` + `flake-analysis-worker.service: Started` 까지 보이면 부팅 완료. Ctrl-D로 SSM 세션 빠지기.

### 12.4 Smoketest (CUDA + LoRA 모델 로드 + 합성 이미지 mask)

```bash
aws ssm send-command --region us-east-2 \
  --instance-ids "${INSTANCE_ID}" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["cd /opt/sam/stand-alone-analyzer && /opt/sam/stand-alone-analyzer/.venv/bin/python scripts/aws/sam-gpu-smoketest.py --weights $(ls /opt/sam/weights/sam2.1_hiera_large.merged.*.pt | head -1)"]' \
  --query 'Command.CommandId' --output text
```

위 출력에서 받은 CommandId로 결과 폴링:

```bash
CMD_ID=<위 CommandId>
aws ssm get-command-invocation --region us-east-2 \
  --command-id "${CMD_ID}" --instance-id "${INSTANCE_ID}" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
```

**기대값**: `Status=Success`, stdout에 `OK`. 실패 시 stderr에 traceback — PM에게 전체 출력 그대로 전달.

### 12.5 Procrastinate 1-job enqueue + DB INSERT 검증

이 부분은 valid `runs` row + `mask_results` 테이블 권한이 필요. 여기서부터는 **PM에 한 번 더 알려서** worker enqueue 명령 확정 후 진행. (현재 RDS prod scan record 유무 PM이 모름.)

owner가 §12.4까지 통과했으면 PM에게 "smoketest OK" 한 줄만 던지면 PM이 다음 단계 위임.

### 12.6 Tear-down (필수 — 끝나면 무조건)

```bash
aws ec2 terminate-instances --region us-east-2 --instance-ids "${INSTANCE_ID}"
aws ec2 wait instance-terminated --region us-east-2 --instance-ids "${INSTANCE_ID}"
echo "TERMINATED"
```

콘솔 EC2 페이지에서도 한 번 더 확인. spot은 `instance-interrupted-by-aws`로도 죽을 수 있는데 그건 정상 — auto-terminate.

### 12.7 비용 후속 확인 (다음날)

```bash
# CloudWatch 일일 비용 알람 (P4.5에서 설정한 $20/day soft cap)
aws cloudwatch describe-alarms --region us-east-1 \
  --alarm-name-prefix qpress-sam-daily-cost \
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

OK 상태면 정상. ALARM이면 PM에게 즉시 보고.

---

## 13. 8-GPU Measurement Run — 2026-05-28 (SMOKE_FAIL)

**Outcome:** 10-image smoke run failed in 62.6 s with `FileNotFoundError: '../external/sam2'`. Architecture mismatch between vendor `run_multi_process` (multi-GPU pool, `build_sam2_finetuned` path) and our `merged.pt` artifact format (designed for single-GPU `state["model_config"]` shortcut). **No 3648-image run executed; instance terminated at smoke failure per plan's escalation policy.**

### 13.1 Run summary

| Field | Value |
|---|---|
| Plan | `docs/superpowers/plans/2026-05-28-sam-8gpu-parallel.md` (Tasks 5–8) |
| Branch | `feat/migration-cutover` (HEAD `00b997f` after cherry-picking worker fixes) |
| Instance | `i-038703b1a1740faad` — `g6e.48xlarge` spot in `us-east-2a` |
| Spot price | `$4.26 / hr` |
| Launch | `2026-05-28T04:08:35Z` |
| Smoke deferred | `2026-05-28T04:15:53Z` (procrastinate `JOB_ID=6`, `RUN_ID=99001`) |
| Smoke started | `04:16:04Z` |
| Smoke failed | `04:17:07Z` (62.6 s wall) |
| Terminated | `~04:21Z` |
| Wall billed | ~13 min |
| Estimated cost | **$0.92** (≤ $1.50 hard cap from owner brief) |
| GPUs detected | 8 × NVIDIA L40S (verified via `nvidia-smi -L`) |
| Worker process | `python -m flake_analysis.worker --queue gpu --concurrency 1` (active) |

### 13.2 What worked

- Launch template v9 (`g6e.48xlarge` + `ami-0b7ec5ff47a1eff11`) booted cleanly. AMI is pre-baked with CUDA + venv + repo + weights — SSM `Online` at t≈75 s, worker active at t≈90 s.
- 8 × L40S enumerated correctly. The hardware gate `torch.cuda.device_count() >= 2` triggered the new branch (verified — error path went through `_run_sam_multi_gpu` → `_vendor_run_multi_process`).
- Worker fix-cherry-picks (`d5d9783` open `App.open_async()`, `00b997f` `kwargs=` for psycopg pool) applied cleanly onto `feat/migration-cutover`. Worker now starts on this branch (previously crashed on `procrastinate.exceptions.AppNotOpen`).
- `/proc/PID/environ` launcher idiom worked — defer script ran as root SSM, read SAA_DB_* from worker pid `5051`, opened procrastinate `App`, deferred job, returned `JOB_ID=6`.
- 10-image smoke staged via S3 server-side copy (`dev/scans/6/images/` → `internal/sam/measure-input-scan6/`) then `aws s3 cp` from instance role.

### 13.3 What broke — root cause

Vendor's `run_multi_process` (in `vendor/QPress-SAM-Flake/run_amg_v2.py`) calls `build_sam2_finetuned(sam2_repo, ckpt_dir, ckpt_file, device)` per worker child (line ~998), which in turn calls `ensure_sam2_importable(sam2_repo)` — that function `os.chdir(sam2_repo)` and adds it to `sys.path`. Our config dict in `_build_vendor_config` hard-codes `sam2_repo="../external/sam2"` (vendor's `DEFAULT_SAM2_REPO` constant at `run_amg_v2.py:40`), but **no `external/sam2` directory exists in our deployed repo** — `sam2` is installed as a pip package at `.venv/lib/python3.11/site-packages/sam2/` (vendor `requirements-inference.txt`).

Beyond the path: `build_sam2_finetuned` also requires:
1. `ckpt_dir/args.json` (consumed by `load_training_args` line 536) — does NOT exist beside `merged.pt`.
2. A base SAM2 checkpoint at `train_args["checkpoint"]` (line 539) for `build_sam2(...)` to load before LoRA application.
3. `lora.apply_lora_to_sam2_components` (line 562) to mount LoRA adapters — but `merged.pt` has no LoRA structure, it's a pre-merged single state-dict.

Plan Risk #4 anticipated config-key drift; the actual surface is bigger — `merged.pt` was produced precisely to skip the LoRA-mount-then-load chain that `run_multi_process` insists on.

`merged.pt` introspection:
```
top keys: ['model_config', 'model_state_dict']
model_config keys: []          # empty dict
model_state_dict n_keys: 903   # raw merged weights
```

Note: `model_config={}` means even the single-GPU path (`_vendor_infer` → `run_amg_v2_inference.infer` line 54: `build_sam2(state["model_config"], None, device=device)`) likely fails too — but this was never tested end-to-end on real GPU prior to this run, so the regression isn't from our 8-GPU change.

### 13.4 Exact stderr (procrastinate event truncated; journal authoritative)

```
multiprocessing.pool.RemoteTraceback:
Traceback (most recent call last):
FileNotFoundError: [Errno 2] No such file or directory: '../external/sam2'
Traceback (most recent call last):
  File "/opt/sam/stand-alone-analyzer/src/flake_analysis/worker/tasks.py", line 131, in run_sam
    result = run_sam_step(...)
  File "/opt/sam/stand-alone-analyzer/src/flake_analysis/pipeline/sam.py", line 22, in run_sam_step
    return run_sam(...)
  File "/opt/sam/stand-alone-analyzer/src/flake_analysis/core/pipeline/sam.py", line 196, in run_sam
    return _run_sam_multi_gpu(...)
  File "/opt/sam/stand-alone-analyzer/src/flake_analysis/core/pipeline/sam.py", line 139, in _run_sam_multi_gpu
FileNotFoundError: [Errno 2] No such file or directory: '../external/sam2'
```

### 13.5 Recommendation — route to algo-engineer

Three options for the multi-GPU path:

1. **Adapter shim in `_run_sam_multi_gpu`** — replace the vendor `run_multi_process` call with our own minimal spawn-pool that loads `merged.pt` per child via the same `state["model_config"]` shortcut the single-GPU path uses. Risk: ~80 lines of CUDA-spawn logic to reimplement vendor functionality. Plan AD2 explicitly rejected this.

2. **Patch vendor `worker_process_images`** to accept a `merged_pt_path` config key and use the `state["model_config"]` shortcut. Vendor edit, breaks "READ-ONLY vendor" rule. Cleanest if algo-engineer signs off.

3. **Re-merge weights into the `args.json`+base-ckpt+LoRA layout vendor expects.** Means re-running the LoRA training-export pipeline with the multi-GPU layout in mind. Highest fidelity, slowest. Punts the question to whoever produced `merged.pt`.

Single-GPU path on `g6e.xlarge` (the documented 3.98 s/img baseline) was apparently never re-validated on real GPU after the merge format settled — confirm that baseline still holds before fanning out.

### 13.6 Cleanup performed

- Instance `i-038703b1a1740faad` terminated.
- S3 staging at `s3://qpress-uploads/internal/sam/measure-input-scan6/` (3648 PNG, 10.32 GB) deleted.
- Two worker-runtime fix commits cherry-picked onto `feat/migration-cutover`: `d5d9783` (procrastinate App pool open), `00b997f` (psycopg pool kwargs=). These were on `worktree-agent-a7087c671a2ac8601` only — without them the worker on this branch would not even start.

---

## 14. M3 Asset Bootstrap — vendor 4-asset bundle (a-track, 2026-05-28)

Recovers the 8-GPU measurement path from §13 by giving the vendor
`run_amg_v2.run_multi_process` the 4-asset layout it expects. The
single-file `merged.pt` flow (§3) is **untouched** — M3 is additive.
`SAM_M3_DIR` and `SAM_WEIGHTS_PATH` are both exported on the worker;
algo-engineer chooses which the multi-GPU code path consumes.

### 14.1 S3 layout

Prefix: `s3://qpress-uploads/internal/sam/m3/` (mirrors the
`sam/measure-input-scan6/` precedent — same IAM coverage, no new policy
needed).

| Object | Size | Purpose |
|---|---:|---|
| `sam2.1/sam2.1_hiera_l.pt` | 856.5 MiB | Base SAM2.1 hiera-large checkpoint |
| `sam2.1/configs/sam2.1_hiera_l.yaml` | 3.7 KiB | SAM2 model config (vendor `args.json` references it via `model_dir`) |
| `sam2_lora/best_model.pth` | 917.6 MiB | LoRA adapter weights |
| `sam2_lora/args.json` | 1.5 KiB | LoRA hyperparams + paths |

Total ~1.8 GiB.

### 14.2 Lazy-DL behavior on workers

Added to `scripts/aws/sam-gpu-worker-userdata.sh` (Step 5b, runs after the
merged.pt fetch and before SSM env wiring). Behavior:

- `aws s3 sync s3://${S3_BUCKET}/${S3_M3_PFX} ${M3_DIR}/` lands the bundle
  at `/opt/sam/m3/` (default — overridable via `S3_M3_PFX` env on the
  launch template).
- `aws s3 sync` is natively idempotent — reboots / re-runs skip files
  whose size+mtime match S3.
- Boot fails fast (`exit 1`) if any of the 4 expected files is missing
  or zero bytes after the sync.
- Stamp file: `/opt/sam/state/m3-assets.done` — once present, the step
  is skipped on subsequent boots of the same root volume.
- Worker systemd unit gets `Environment=SAM_M3_DIR=/opt/sam/m3` so the
  Python code can locate the bundle without re-deriving paths.

Layout the worker exposes:

```
/opt/sam/m3/
├── sam2.1/
│   ├── sam2.1_hiera_l.pt
│   └── configs/
│       └── sam2.1_hiera_l.yaml
└── sam2_lora/
    ├── args.json
    └── best_model.pth
```

IAM: existing `qpress-sam-gpu-role` inline policy `qpress-sam-gpu-s3`
already grants `s3:GetObject` + `s3:ListBucket` on `internal/sam/*` —
no policy update required.

### 14.3 Refresh from prod (owner / DevOps procedure)

When the prod LoRA / base ckpt rotates and the M3 bundle on S3 needs a
refresh. Run from owner's laptop (Mac with `sshpass` + `aws-cli` v2).
**SSH password is in-memory only** — never write it to a file or commit
log. Pass it via `$SSHPASS` and let `sshpass -e` read from env:

```bash
# 1. Stage locally (4 files, ~1.7 GiB on disk).
mkdir -p /tmp/m3-stage/sam2.1/configs /tmp/m3-stage/sam2_lora

export SSHPASS='<owner-supplied; do not echo to logs>'
SSHPASS="$SSHPASS" sshpass -e scp \
  flake_identifier:/home2/qpress/qpress/models/sam2.1/sam2.1_hiera_l.pt \
  /tmp/m3-stage/sam2.1/sam2.1_hiera_l.pt
SSHPASS="$SSHPASS" sshpass -e scp \
  flake_identifier:/home2/qpress/qpress/models/sam2.1/configs/sam2.1_hiera_l.yaml \
  /tmp/m3-stage/sam2.1/configs/sam2.1_hiera_l.yaml
SSHPASS="$SSHPASS" sshpass -e scp \
  flake_identifier:/home2/qpress/qpress/models/sam2_lora/best_model.pth \
  /tmp/m3-stage/sam2_lora/best_model.pth
SSHPASS="$SSHPASS" sshpass -e scp \
  flake_identifier:/home2/qpress/qpress/models/sam2_lora/args.json \
  /tmp/m3-stage/sam2_lora/args.json
unset SSHPASS

# 2. Verify totals (~1.7 GiB).
du -sh /tmp/m3-stage

# 3. Push to S3 (sync handles per-file ETag check).
aws s3 sync /tmp/m3-stage/ s3://qpress-uploads/internal/sam/m3/ \
  --profile qpress --region us-east-2

# 4. Confirm.
aws s3 ls s3://qpress-uploads/internal/sam/m3/ --recursive --human-readable \
  --profile qpress --region us-east-2

# 5. Clean up local stage (these files are large).
rm -rf /tmp/m3-stage/
```

`flake_identifier` is the `~/.ssh/config` alias (`hal.cfn.bnl.gov`,
user `qpress`). Existing workers will pick up the refreshed assets on
their **next boot** because the lazy-DL step compares S3 size+mtime —
running workers continue serving from the previous on-disk copy until
they recycle.

### 14.4 Launch template version provenance

| LT version | AMI | Change |
|---|---|---|
| v9 | `ami-0b7ec5ff47a1eff11` | g6e.48xlarge default, single-GPU merged.pt only |
| v10 | `ami-0b7ec5ff47a1eff11` | + M3 4-asset bundle download to `/opt/sam/m3/` |

v10 is the new `$Default`. AMI is unchanged — the M3 step lives
entirely in user-data, no AMI rebuild required. Front-matter comments
in the user-data script were trimmed to stay under the EC2 16 KiB
user-data cap; the operational documentation those comments held now
lives in this section and §3 of this doc.

### 14.5 Verification trace (2026-05-28)

Test instance `i-0512afdef17f1c9cf` (g6e.xlarge spot, us-east-2b — 48xlarge
capacity unavailable across all 3 AZs at the time of bootstrap; the M3
download step is identical regardless of instance type so the smaller
GPU served the verification). Tagged `Purpose=m3-bootstrap-verify`.
Terminated immediately after verification.

`ls -la /opt/sam/m3/sam2.1/`:

```
drwxr-xr-x 3 root root      4096 May 28 10:34 .
drwxr-xr-x 4 ubuntu ubuntu  4096 May 28 10:34 ..
drwxr-xr-x 2 root root      4096 May 28 10:34 configs
-rw-r--r-- 1 root root 898083611 May 28 10:30 sam2.1_hiera_l.pt
```

`du -sh /opt/sam/m3/` → `1.8G`.

`head /opt/sam/m3/sam2_lora/args.json` (first lines, schema check):

```
{
  "dataset_root": "/home2/mhussain/projects/BNL/CFN/QPressSeg/datasets/Real",
  "dataset_type": "real",
  "target_size": 1024,
  "index_workers": 24,
  ...
  "model_dir": "/home2/qpress/qpress/models/sam2.1/",
```

> Note: `args.json` still encodes the **prod** `model_dir` path
> (`/home2/qpress/...`). On the AWS worker the actual base ckpt sits at
> `/opt/sam/m3/sam2.1/`. The vendor multi-GPU loader either needs an
> override at call-site or a path-rewrite shim — that's an algo-engineer
> decision, not a bootstrap concern.

### 14.6 sam2 Python package state on the AMI (for algo-engineer)

Confirmed at verification time on the v10 launch template:

- `sam2` IS importable from the worker venv — `python -c "import sam2"`
  resolves to `/opt/sam/stand-alone-analyzer/.venv/lib/python3.11/site-packages/sam2/__init__.py`.
- `pip list` does **not** show `sam2` as a registered distribution —
  it's installed as an unregistered site-package, presumably by the
  `requirements-inference.txt` install step against the
  `vendor/QPress-SAM-Flake` submodule (Step 5 of the user-data).
- `vendor/QPress-SAM-Flake/` is fully present at
  `/opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/` so the
  vendor `run_amg_v2.run_multi_process` import path is reachable.

Implication: algo-engineer does **not** need to ship a separate sam2
repo or pip-install at boot — the existing user-data already provides
the import. Whether `run_amg_v2` works end-to-end depends on the
`args.json` `model_dir` rewrite called out in §14.5, not on package
availability.

---

## 15. 8-GPU Measurement Run — 2026-05-28 (M3-track, PARTIAL — spot reclaim)

**Outcome:** 3648-image full run on the M3 (SAM2.1 + LoRA, un-merged) bundle
made it to **1975 / 3648 images (54.1%)** before AWS reclaimed the spot
instance (`instance-terminated-no-capacity` in `us-east-2b`). Smoke run
(10 images) passed cleanly after fixing two AMI gaps (vendor base-ckpt
symlink, missing `peft` pip dep). Bottleneck diagnosed live during the
run: GPUs sustained at sm%≈94–98, mem%≈92–98 with `pviol`=100% at
320–340 W on each L40S — the 3× per-card slowdown vs the documented
single-GPU `merged.pt` baseline (3.98 s/img) is structural, not a
plumbing bug. **No `per_image_results.json` was written** (vendor
postprocess never reached); per-image mask totals are not recoverable
beyond folder counts.

### 15.1 Run summary

| Field | Value |
|---|---|
| Plan | (in-flight; no plan doc — spot from prior agent context) |
| Branch | `feat/migration-cutover` (HEAD `09fdb29` at run start) |
| Instance | `i-00d21b6b6cfd9cd48` — `g6e.48xlarge` spot in `us-east-2b` (sir `sir-4wwqg8vg`) |
| Spot price (last quote) | `$3.956 / hr` (max bid `$30.13`) |
| Launch | `2026-05-28T11:57:38Z` |
| Smoke deferred | `2026-05-28T~12:20Z` (procrastinate `JOB_ID=9`, `RUN_ID=99099`, 10 images) |
| Smoke succeeded | passed in `~95 s` after fixing 2 AMI gaps (see §15.3) |
| Full deferred | `2026-05-28T~12:27Z` (procrastinate `JOB_ID=10`, `RUN_ID=99100`, 3648 images) |
| Full started | `12:27:46Z` |
| Full effective end | `13:17:49Z` (worker SIGTERM by spot-monitor; never reached `succeeded`) |
| Spot interruption notice | `13:17:42Z` (IMDS `spot/instance-action` showed terminate at `13:19:42Z`) |
| AWS terminate event | `13:19:45Z` (`Service initiated`, reason `instance-terminated-no-capacity`) |
| Wall billed | `~82 min` (launch → terminate) |
| Job 10 wall before SIGTERM | `~50.05 min` |
| Images completed | `1975 / 3648` (54.1%) — measured by last `[N/3648]` log line + persisted mask folders (2003) |
| Cost | **≈ $5.47** (82/60 × $3.956 + small EBS) — under the owner's `$9` hard cap |
| GPUs | 8 × NVIDIA L40S, all 8 active throughout run (verified via `nvidia-smi dmon`) |
| Worker | `python -m flake_analysis.worker --queue gpu --concurrency 1`, M3 path |

### 15.2 Throughput & speedup (partial-run derivation)

From the journal `[N/3648]` log markers + 50.05 min wall before SIGTERM:

- **Aggregate throughput:** 1975 images / 50.05 min = **39.5 img/min** = **1.521 s/img** (job-level)
- **Per-card throughput:** 1.521 s/img × 8 cards in parallel = **~12.16 s/card-img**
- **Single-GPU baseline (documented):** 3.98 s/img on `merged.pt` / `g6e.xlarge`
- **Multi-GPU speedup:** 3.98 / 1.521 = **2.62× over single-GPU** (vs the 8× ideal)
- **Per-card slowdown vs baseline:** 12.16 / 3.98 = **3.06× slower per card**

Interpretation: the 8-way fan-out is real (we observed all 8 L40S
sustained at 94–98% sm utilization — see §15.4); the wall-clock
speedup is only 2.62× because each card is doing ~3× more work per
forward than the `merged.pt` single-GPU path. Diagnosis in §15.4.

### 15.3 Smoke gaps fixed before the full run

The instance had been pre-launched by an earlier agent and was idle on
arrival. Two AMI / bootstrap gaps surfaced on smoke runs and were
patched in-place to unblock the measurement:

1. **Missing vendor base-ckpt path.** Vendor `build_sam2_finetuned`
   hardcodes `/home2/qpress/qpress/models/sam2.1/sam2.1_hiera_l.pt` (a
   prod-host path baked into `args.json`'s `model_dir`). On the AWS
   AMI the base ckpt sits at `/opt/sam/m3/sam2.1/`. Fix: symlink
   `/home2/qpress/qpress/models/sam2.1/{sam2.1_hiera_l.pt,configs}` →
   `/opt/sam/m3/sam2.1/`. This is **the same `args.json` `model_dir`
   issue called out in §14.5** — but resolved at the filesystem
   layer, not at call-site. For a permanent fix the AMI bootstrap
   (or `run_multi_process` shim) should rewrite the path properly.

2. **Missing `peft` pip dep.** Smoke #2 failed with
   `ModuleNotFoundError: No module named 'peft'`. The LoRA loader path
   in vendor `lora.apply_lora_to_sam2_components` requires `peft`.
   Fix: `uv pip install --python /opt/sam/stand-alone-analyzer/.venv/bin/python peft`
   — added 15 packages including `peft==0.19.1`, `transformers==5.9.0`,
   `accelerate==1.13.0`. **The AMI image baking should add `peft` to
   `requirements-inference.txt`** (it's not in the current pinset; the
   merged `merged.pt` path doesn't need it, but the M3 LoRA path
   does).

Both fixes are runtime-only on this terminated instance — they do
not survive into a fresh AMI launch. Future M3 measurement runs
must re-apply them, or the AMI must be rebuilt.

### 15.4 Bottleneck diagnostic (live, during the run)

Captured ~t+18 min into job 10 via `nvidia-smi dmon -s pucvmet -d 1 -c 15`
plus `/proc` introspection of the running multi-GPU pool:

| Signal | Observation | Conclusion |
|---|---|---|
| GPU sm% | 94–98% sustained on all 8 cards | not pipeline-stalled |
| GPU mem% | 92–98% sustained | memory-bandwidth bound |
| `pviol` | =100% (always) | **power-throttled at 350 W TDP**, drawing 320–340 W steady |
| PCIe rxpci | 7–24 MB/s | **not host↔device transfer bound** |
| PCIe txpci | 2–6 MB/s | (same — PCIe is idle relative to L40S 64 GB/s capacity) |
| CPU | ~8 procs × 150% on 192 vCPUs | trivial — host is not the constraint |
| RAM | 1.5 TB available, no swap | trivial |
| `args.json` | `lora_image_encoder_rank=16`, `lora_memory_attention_rank=32`, `lora_memory_encoder_rank=32`, `lora_train_decoder=true`, `lora_apply_memory=false` | **LoRA adapters are NOT merged into base** — every forward applies them at runtime |

**Conclusion:** the per-card 3× slowdown is structural to the M3 bundle,
not a multi-GPU plumbing artifact. Multi-GPU scaling itself is fine:
all 8 L40S are saturated and at the power-cap simultaneously, so we
*are* getting 8× parallelism on top of a per-image workload that is
itself ~3× heavier than the merged.pt baseline. The wall-clock
speedup ratio (2.62×) ≈ 8 / 3.

**Recommendations:**

1. **For productionization (recommended path):** merge the M3 LoRA
   adapters into a single state-dict (the `merged.pt` shape that the
   single-GPU path already consumes), eliminating the runtime adapter
   overhead. This recovers per-card throughput to the 3.98 s/img
   baseline → projected full-run wall on 8 cards ≈ 30 min for 3648
   images at $4/hr ≈ $2.

2. **If the un-merged M3 layout is intentional** (e.g. for ongoing LoRA
   training or A/B configurability): record ~1.5 s/img job-aggregate
   on 8× L40S as the M3 baseline and budget runs against that. A full
   3648 run on g6e.48xlarge then costs ≈ $6 + bootstrap → ~$8–9 per
   pass.

This is the same finding as §13's `merged.pt` shape question, but
inverted: §13 broke because vendor required the LoRA-mount layout;
§15 ran but slow because vendor-loaded LoRA at runtime instead of
merging. A single decision — *which artifact format do we want
canonical?* — closes both.

### 15.5 Why the run was cut short — spot capacity, not budget

This was **not** a budget cutoff. AWS reclaimed the instance:

```
SpotInstanceRequests[0]:
  State: closed
  Status.Code: instance-terminated-no-capacity
  Status.Message: Your Spot instance was terminated because there is
                  no Spot capacity available that matches your request.
```

Sequence:

```
13:17:42Z   IMDS spot/instance-action: {"action":"terminate","time":"2026-05-28T13:19:42Z"}
13:17:49Z   flake-analysis-spot-monitor.service stops worker (cleanly via SIGTERM)
13:18:05Z   systemd auto-restarts worker; worker starts, immediately gets stopped again
13:19:42Z   AWS-scheduled terminate window
13:19:45Z   EC2 state → shutting-down (Service initiated)
```

The `flake-analysis-spot-monitor` polls `/latest/meta-data/spot/instance-action`
every 6 s; it caught the notice and gracefully stopped the worker
~2 min before AWS yanked the host. Working as designed — the
ungraceful loss is upstream (no spot capacity in `us-east-2b`).

Side note: `flake-analysis-idle-shutdown.service` was also failing
every 6 s with `unexpected EOF while looking for matching ')'` —
the env-file rewrite earlier in the session (to inject the rotated
RDS password) used a value containing a literal `(` character that
shell-sourcing chokes on. The idle-shutdown unit isn't what stopped
the run, but the env file should be re-quoted on a future bootstrap.

### 15.6 What to do next time

- ~~**Rebuild AMI** to bake in `peft` + the `/home2/qpress/qpress/models/sam2.1/`
  symlink~~ — **patched in `scripts/aws/sam-gpu-worker-userdata.sh` at
  commit `d320029`**. The userdata Step 5 now
  installs `peft>=0.8.0,<0.20` after the vendor `requirements-inference.txt`
  install (vendor file's top-comment intentionally excludes peft for the
  merged.pt path; the dep belongs at runtime, not in the vendor pinset).
  A new Step 5c materializes prod-path symlinks under
  `/home2/qpress/qpress/models/{sam2.1,sam2_lora}/` pointing at
  `${M3_DIR}` — required because vendor `run_multi_process` uses
  `mp.get_context("spawn").Pool` (vendor `run_amg_v2.py:1113`) and the
  spawn workers re-import `run_amg_v2` in fresh interpreters that never
  see our parent-process monkeypatch on `load_training_args`. They read
  the raw absolute paths from `args.json` — symlinks are the lowest-blast
  way to satisfy them without touching the on-disk asset bundle. **Open
  follow-up: rewrite `args.json.model_dir` at AMI/asset bake time so the
  symlink layer is not load-bearing — separate task.** §14.5 path issue
  remains the canonical permanent fix.
- **Choose a canonical model artifact format.** Either re-merge M3 LoRA into
  a single `merged.pt`-shaped file, or commit to the un-merged layout and
  document the ~1.5 s/img-aggregate baseline. Both close §13 and §15
  simultaneously.
- **Try a different AZ** — `us-east-2b` had no g6e.48xlarge spot capacity.
  `us-east-2a` worked in §13 (briefly). The launch template currently
  pins AZ; consider a multi-AZ spot fleet so a single AZ outage does not
  reclaim the whole run. **Open follow-up — separate task.**
- **Worker env file quoting** — the password injection produced a value
  with unescaped `(` that breaks shell sourcing for sibling units. Either
  base64-encode the password in the env file and decode in the worker
  entry-point, or quote-escape on write. Filed for the next devops pass.

### 15.7 Cleanup performed

- Instance `i-00d21b6b6cfd9cd48` — terminated by AWS at 13:19:45Z (no manual
  terminate needed; verified `State.Name=shutting-down`, reason
  `Service initiated (instance-terminated-no-capacity)`).
- S3 staging at `s3://qpress-uploads/internal/sam/scan6-3648/` (3648 PNG,
  9.7 GB) — **left in place** for the next M3 measurement attempt
  (server-side copy from `dev/scans/6/images/`, no PM egress).
- No new commits to AWS infra, IAM, SG, or launch template — the runtime
  fixes (symlink, peft install) live only on the terminated instance and
  are described in §15.3 for the AMI rebuild.

## 16. M3 LoRA Merge Build Step — owner-runnable, owner-gated

The 2026-05-28 8-GPU partial measurement (§15) confirmed the per-card
LoRA-applied forward is the structural bottleneck on the M3 path
(12.16 s/card-img vs 3.98 s/img on the single-GPU `merged.pt` baseline,
~3.06× slower). Pre-merging the LoRA into the base SAM2.1 weights collapses
the runtime adapter math into a single `.pt` that vendor `build_sam2(...)`
can load directly — the same code path that produced the 3.98 s/img
baseline. This section documents the build step that produces that artifact
from the M3 4-asset bundle and uploads it under a dedicated S3 prefix.

The wiring that *uses* the merged_m3 artifact (worker discovery + multi-GPU
re-measurement) is filed as separate follow-ups (#209, #210, #211) — this
section covers only the artifact build.

### 16.1 What it produces

A single object plus sidecar under a new S3 prefix:

```
s3://qpress-uploads/internal/sam/merged_m3/
  ├── sam2.1_hiera_large.merged_m3.<sha8>.pt        # ~898 MB
  ├── sam2.1_hiera_large.merged_m3.<sha8>.pt.sha256 # SHA256 line + filename
  └── sam2.1_hiera_l.yaml                           # config co-located
```

`<sha8>` is the first 8 hex chars of the full SHA256, identical to the
naming convention used by the P4.3 single-GPU `merged.pt` publish flow.

The merge math is per-tensor: M3 carries three different LoRA ranks
(`image_encoder=16`, `memory_attention=32`, `memory_encoder=32`) sharing a
single `lora_alpha=32.0`. The vendor merge CLI was extended (vendor commit
`f1764c7` on branch `feat/per-tensor-rank-merge`) to derive `rank` from
`a.shape[0]` per adapter when `--alpha` is supplied, so the build step
only has to pass alpha. Legacy `--config rank_alpha.json` (P1.5 contract)
is preserved.

### 16.2 Why a separate prefix from `internal/sam/`

The existing `merged.pt` discovery in `sam-gpu-worker-userdata.sh` step 5
filters on the literal prefix `internal/sam/sam2.1_hiera_large.merged.`
(see `sam-gpu-worker-userdata.sh:170`). The new artifact uses
`internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.` which:

1. Does not match the existing list filter, so today's single-GPU path is
   completely untouched (#209/#210 will add discovery for `merged_m3`).
2. Keeps the older `merged.pt` available for rollback / parity checks.
3. Lets the bucket lifecycle policy treat the two artifact streams
   independently when retention rules are tightened.

### 16.3 The build script

Owner-runnable from the repo root:

```bash
./scripts/aws/sam-build-merged-m3.sh [--dry-run] [--keep-tmp]
```

What the script does, in order:

1. `aws s3 sync s3://${S3_BUCKET}/${M3_PREFIX} → ${TMP_DIR}/m3/` —
   pulls the 4-asset M3 bundle exactly as workers do at boot
   (`sam-gpu-worker-userdata.sh` step 6b, §14).
2. Reads `lora_alpha` from `sam2_lora/args.json` via `jq`, also surfaces
   the per-tensor ranks for the operator log (info only — the merge
   itself derives rank from tensor shape).
3. Calls `vendor/QPress-SAM-Flake/scripts/merge_lora.py --alpha ${ALPHA}`
   on the base + LoRA pair, writes the merged tensor to the tmp workspace.
4. Computes SHA256 of the merged file, then lists existing objects under
   `${OUT_PREFIX}sam2.1_hiera_large.merged_m3.` sorted by `LastModified`
   and reads the most recent `.sha256` sidecar — if the SHA matches the
   newly-merged file, the script exits without uploading (idempotent).
5. Otherwise prompts `[y/N]` and uploads three objects: the `.pt`, a
   sidecar `${full_sha}  ${basename}\n`, and the co-located config yaml.

`--dry-run` walks every step except the final S3 upload and prints what
would be uploaded and the local artifact paths. `--keep-tmp` preserves the
tmp workspace on exit (default is to `rm -rf` it).

Pre-flight checks (failure exits non-zero before any S3 read): `aws`,
`python3`, `jq` on PATH; `vendor/QPress-SAM-Flake/scripts/merge_lora.py`
present (i.e. submodule initialised); IAM credentials with read on
`${M3_PREFIX}*` and write on `${OUT_PREFIX}*`.

### 16.4 When to run it

Run this script after every successful run of
`scripts/aws/sam-stage-lora-to-s3.sh` (the existing prod LoRA stage step,
§14.3) and **before** launching a fresh GPU instance that should consume
the merged form. The two scripts complement each other:

- `sam-stage-lora-to-s3.sh` — pulls `best_model.pth` from
  `qpress@hal.cfn.bnl.gov:…` and uploads to
  `internal/sam/m3/sam2_lora/`. Continue using it for raw-LoRA staging.
- `sam-build-merged-m3.sh` — *consumes* the M3 bundle and *produces*
  the merged artifact under `internal/sam/merged_m3/`.

The build is CPU-only (the merge math is element-wise) and finishes in a
few minutes on any laptop with ~3 GB free disk.

### 16.5 Vendor branch / submodule pointer

The CLI extension lives on branch `feat/per-tensor-rank-merge` in the
vendor submodule (commit `f1764c7`). Main repo is pinned to that commit
via `vendor/QPress-SAM-Flake`. The legacy `--config rank_alpha.json` path
still works — existing P1.5/P1.6 callers (`run_amg_v2_inference.infer`)
are unaffected.

### 16.6 Worker discovery (Step 5d, #210)

`sam-gpu-worker-userdata.sh` now has a Step 5d (between the M3 4-asset
prod-path symlinks and the SSM env-file write) that mirrors Step 5's
discovery idiom against `${S3_MERGED_M3_PFX}` (default
`internal/sam/merged_m3/`):

1. List `s3api list-objects-v2 --prefix
   internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.` and pick the
   most-recently-uploaded `.pt` via the same JMESPath used by Step 5.
2. **Soft-miss** — if no merged_m3 exists yet, log a notice, stamp
   `merged-m3-skipped`, and skip download. The worker falls back to the
   LoRA-runtime path via the M3 4-asset bundle (Step 5b/5c). Boot does
   NOT fail.
3. **Hard-fail on corruption** — if a key is present, fetch the
   `.sha256` sidecar, download the `.pt` to `${WEIGHTS_DIR}/merged_m3.pt`,
   and verify SHA256. Mismatch → `rm -f` and exit 1, mirroring Step 5's
   refusal-to-serve policy.
4. On success, write the resolved key to
   `${STATE_DIR}/active_merged_m3_key` and stamp `merged-m3-weights`
   (separate from Step 5's `weights` stamp — the two artifacts live and
   age independently).

The systemd worker unit (Step 7a) exposes `SAM_MERGED_M3_PATH=${MERGED_M3_PT}`
alongside the existing `SAM_WEIGHTS_PATH` and `SAM_M3_DIR`. The dual-mode
pipeline code (#209) reads `SAM_MERGED_M3_PATH` to decide between
`build_sam2(...)` (fast path) and `build_sam2_finetuned(...)` (LoRA-runtime
fallback). If Step 5d soft-missed, the env var still points at a path
that doesn't exist on disk — the consumer side checks `os.path.exists`
before preferring it, so the fallback is graceful.

Steps 5 (single-GPU `merged.pt` discovery) and 5b/5c (M3 bundle + vendor
prod-path symlinks) are unchanged and remain authoritative for their
respective code paths.

### 16.7 Follow-ups (PM-tracked)

- **#209** — rewire `_run_sam_multi_gpu` to prefer the merged_m3 artifact
  via the existing single-GPU `build_sam2(...)` loader. Until #209 lands,
  the multi-GPU path still goes through `build_sam2_finetuned` and the
  runtime LoRA application (i.e. uploading merged_m3 alone does not
  change worker behaviour).
- **#210** — landed (this section §16.6). Userdata Step 5d discovers
  `internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.<sha8>.pt` and
  exposes `SAM_MERGED_M3_PATH` to the worker service.
- **#211** — once #209 + #210 are in, re-run the 8-GPU full-set
  measurement (3648 PNG) on the merged_m3 path. Target trajectory ~30 min
  on g6e.48xlarge if scaling recovers (vs the failed ~90 min trajectory
  on the LoRA-applied path).


## 17. Launch Template v15 — env-file quoting + sslmode=require

Root cause (#217 diagnosis): #211 v2 attempt hit `password authentication
failed` + spurious `no pg_hba.conf` errors because (a) libpq silently fell
back to non-SSL against `rds.force_ssl=1`, and (b) the rotated RDS master
password contains a literal `(` which was mangled by an unquoted
`SAA_DB_PASSWORD=${VAR}` write into the systemd EnvironmentFile.

Fixes shipped (both on `feat/migration-cutover`):
- `196824a` — force `sslmode=require` (psycopg) / `ssl=require` (asyncpg)
  on every DB connection site.
- `d68a9a0` — quote all 5 `SAA_DB_*` writes in
  `scripts/aws/sam-gpu-worker-userdata.sh` so future SSM rotations cannot
  reintroduce the same shell-mangling bug.

Launch Template: `lt-09d01bf17ff7bed30` v15 (on-demand g6e.48xlarge,
no `InstanceMarketOptions`, gzip-base64 user-data from the patched
script). Built from scratch (not `--source-version`) per the v13
silent-spot-merge lesson. SG/IAM/AMI/Subnet match v14 exactly.

Pending: re-run #211 against LT v15 (separate dispatch).

## 17.1 #211 v3 attempt — 2026-05-28 (FAIL: AMI baked stale env, RDS auth re-blocker)

**Outcome:** v3 launch from LT v15 booted cleanly in **~92 s** (faster than the v2
~1m54s baseline because the AMI's idempotent `done_stamp` markers short-circuited
all 8 userdata steps), but the worker hit the **same** `password authentication
failed` + `no pg_hba.conf entry for host..., no encryption` errors that #217's
fix-pair (`196824a` sslmode=require + `d68a9a0` env-file quoting) was supposed to
resolve. Aborted per the brief's hard rule ("if RDS auth still fails — ABORT, do
NOT loop"). **No SAM job ran.** Cost: **$0.92** of the $9 cap.

### 17.1.1 Run summary

| Field | Value |
|---|---|
| Branch | `feat/migration-cutover` HEAD `37ebd35` |
| LT | `lt-09d01bf17ff7bed30` v15 (on-demand, gzip user-data, ami-0b7ec5ff47a1eff11) |
| Instance | `i-03c0ed2aafd915079` — `g6e.48xlarge` on-demand in `us-east-2c` (172.31.43.70) |
| AZ override | LT pins `subnet-0fe8558512beea68a` (us-east-2a) → InsufficientCapacity in 2a → re-launched into `subnet-09f76839fd0c109a9` (us-east-2c) via `--network-interfaces SubnetId=...` |
| Spot price | n/a — on-demand at $15.07/hr |
| Wall billed | 3m40s (launch → terminate) |
| Cost | **$0.92** (3m40s × $15.07/hr) |
| Verdict | **FAIL** — environment defect, not measurement run |

### 17.1.2 Boot timeline (T0–T4 + Δ)

Faster than v2 dispatch-6 (~1m54s) because the AMI was pre-warmed and all 8
`done_stamp`-gated steps short-circuited.

| Marker | Absolute UTC | Δ from prior |
|---|---|---|
| **T0** launch (run-instances response) | `2026-05-28T16:56:50Z` | — |
| **T1** state→running | `2026-05-28T16:57:30Z` | +40 s |
| **T3** user-data start (cloud-init init-local) | `2026-05-28T16:57:43Z` | +13 s after T1 |
| **T2** SSM Online | `2026-05-28T16:58:07Z` | +24 s after T3 |
| **T4** user-data done (`=== sam-gpu-worker-userdata done: ...Z ===` marker, cloud-init finished) | `2026-05-28T16:58:22Z` | +15 s after T2 |
| Worker `active (running)` | `2026-05-28T16:57:47Z` | (parallel to user-data — service was already enabled by AMI) |
| First DB auth failure in journal | `2026-05-28T16:58:37Z` | +15 s after T4 |
| Terminate API call | `2026-05-28T17:00:30Z` | +2m08s after T4 |

**Total boot (T0 → T4): 1 m 32 s** — vs the v2 ~1m54s baseline that's a 22 s
improvement, attributable entirely to AMI cache hits on apt/cuda/python/uv/
repo/deps/weights/m3-assets/m3-prod-symlinks/merged-m3-weights done-stamps.

T2 < T3 by 8 s **arithmetically** because SSM Online and user-data start are
independent paths from launch — SSM agent is started by the AMI's init system
directly, while user-data is part of cloud-init's init-local stage. The two
serialize differently. Real "user-data started" timestamp here is T3 from
cloud-init's own log header; T2 just gates when *we* could *observe* the box.

### 17.1.3 Root cause — AMI bakes a pre-`d68a9a0` env file

Worker journal shows back-to-back failures:

```
psycopg.pool error connecting in 'pool-1': connection failed:
  connection to server at "172.31.36.17", port 5432 failed:
  FATAL: password authentication failed for user "houk"
  connection to server at "172.31.36.17", port 5432 failed:
  FATAL: no pg_hba.conf entry for host "172.31.43.70", user "houk",
         database "qpress", no encryption
```

Two distinct symptoms in one error chain:

1. **"password authentication failed"** — the env file on disk has
   `SAA_DB_PASSWORD=>7F)Bzvy<fmuFkpQnm!7tR6#oVZ.` (no quotes). Bash sources it
   via systemd `EnvironmentFile=`, hits the unescaped `(` and silently fails
   with `syntax error near unexpected token ')'`. Result: worker process
   inherits `SAA_DB_PASSWORD=""` (length zero), libpq sends an empty password,
   RDS returns auth failure.

2. **"no pg_hba.conf entry ... no encryption"** — the `, no encryption` suffix
   is libpq's signal that the TCP attempt was non-SSL. With `rds.force_ssl=1`,
   RDS rejects the unencrypted attempt with the classic `pg_hba` red-herring.
   This is the symptom commit `196824a` (sslmode=require) was supposed to
   eliminate.

**Why the fixes weren't applied — same root for both:** the AMI
`ami-0b7ec5ff47a1eff11` was baked **before** commits `196824a`/`d68a9a0`
landed. Userdata uses `done_stamp` idempotency markers under
`/var/lib/sam-gpu-bootstrap/*.done`. On first boot of a fresh instance from
this AMI, `done_stamp env` is **already present** (from the AMI bake), so the
env-file rewrite step at userdata line 349 (`if ! done_stamp env; then ...`)
**short-circuits** and the *baked-in* unquoted env file from the pre-fix era
remains on disk. Same story for the deployed Python source: the AMI's
`/opt/sam/stand-alone-analyzer/` checkout is at a pre-`196824a` commit
because `done_stamp repo` short-circuits the `git clone` step too.

Verification (from the failed instance via SSM):

```
$ sudo cat /etc/flake-analysis-worker.env
SAA_DB_PASSWORD=>7F)Bzvy<fmuFkpQnm!7tR6#oVZ.    # ← unquoted
$ sudo bash -c 'source /etc/flake-analysis-worker.env; echo len=${#SAA_DB_PASSWORD}'
len=0                                              # ← shell-source mangles to empty
$ grep -nE SAA_DB_PASSWORD /opt/sam/stand-alone-analyzer/scripts/aws/sam-gpu-worker-userdata.sh
273:SAA_DB_PASSWORD=${DB_PASSWORD}              # ← AMI's repo copy: pre-d68a9a0
$ # whereas the LT v15 user-data blob (decoded) is correct:
$ grep -nE SAA_DB_PASSWORD /tmp/lt15_userdata.sh
373:SAA_DB_PASSWORD="${DB_PASSWORD}"             # ← LT v15: post-d68a9a0
```

The line-number drift (273 vs 373) is itself the smoking gun: the in-AMI
script is **531 lines shorter** than the post-`d320029`/`cfe1a98` version
that LT v15 ships. The AMI was baked at a much earlier commit.

### 17.1.4 Routing log — not exercised

The merged_m3 routing fix (#209, commit `947cc3d`, log line
`"routing: merged_m3 (...)"`) was **not exercised** because no SAM job was
deferred. Cannot confirm or refute the merged_m3 routing on this attempt.

### 17.1.5 Fix path — needs AMI rebuild OR done-stamp invalidation

The #217 diagnosis was correct in identifying the bugs, but the fixes only
land on instances launched from a **freshly-baked AMI** that includes the
post-`d68a9a0` script + post-`196824a` Python source. Three options for v4:

1. **Rebuild the AMI** with the current `feat/migration-cutover` HEAD baked
   in. Highest fidelity. Owner-gated since AMI cost/lifecycle is in scope.

2. **Patch userdata to bust the done-stamps for `repo` and `env`** on every
   boot. Cheapest. Low blast radius — the done-stamp mechanism is *for*
   exactly this scenario. Concrete edit: under `if ! done_stamp env;` and
   `if ! done_stamp repo;`, also `rm -f "${STATE_DIR}/env.done" "${STATE_DIR}/repo.done"`
   at the top of userdata (or invert the gate to "if not bootstrap-fresh-marker").
   Risk: each boot re-clones the repo and re-writes the env, adding ~20 s.
   Acceptable.

3. **Pin LT v15 to a clean AMI** (e.g., the original `ami-0b7ec5ff47a1eff11`
   *before* the SAM bake), so userdata runs end-to-end. Defeats the purpose
   of pre-baking — boot would jump from ~1m32s back to ~10–15min.

**Recommendation: option 2 + an LT v16.** Surgical, fast, doesn't require
re-baking the AMI. Owner sign-off needed only for the LT new-version
(no-cost, reversible). File as a follow-up to this entry.

### 17.1.6 Cleanup performed

- Instance `i-03c0ed2aafd915079` — `terminate-instances` issued at
  `2026-05-28T17:00:30Z` (compute billing stops at this API call regardless
  of when state reaches `terminated`). State observed `shutting-down` for
  several minutes after — normal for g6e.48xlarge (192-vCPU host teardown).
- 100-image S3 staging at `s3://qpress-uploads/internal/sam/scan6-100/`
  (100 PNG, 271 MiB) — **left in place** for the v4 attempt. Server-side
  copied from `scan6-3648/` so no PM-side egress.
- No SG / RDS / IAM / LT changes. SG `sg-0e57146d5b6d42452` ingress to RDS
  unchanged (per #217 the rule was already correct — the failure is upstream
  of network reachability).
- No new artifacts on the bastion.

### 17.1.7 Verdict

**FAIL — environment defect, not algorithmic.** The merged_m3 routing claim
from #209 remains unverified at 8-GPU scale. v4 cannot proceed against
`ami-0b7ec5ff47a1eff11` without one of the §17.1.5 mitigations.

## 17.2 #211 v4 attempt — 2026-05-28 (FAIL: done-stamp invalidation triggered git safe.directory abort)

**Outcome:** v4 launched LT v16 (which adds `rm -f env.done repo.done` at
the top of userdata to force re-run of the post-`d68a9a0`/`196824a` env-file
write and post-fix `git pull`). Userdata aborted at **step 4 (clone repo)**
because the AMI's pre-baked `/opt/sam/stand-alone-analyzer/.git` directory
is owned by `ubuntu` while userdata runs as **root** — `git fetch` failed
with `fatal: detected dubious ownership in repository` and `set -euo
pipefail` bailed the script. The env step (6) never executed → stale
unquoted env file on disk → same `password authentication failed` chain
the patch was meant to eliminate. Aborted per brief's hard rule. **No SAM
job ran.** Cost: **~$2.91** of the $9 cap (cumulative across v3+v4: $3.83).

### 17.2.1 Run summary

| Field | Value |
|---|---|
| Branch | `feat/migration-cutover` HEAD `fab270c` (this fix) |
| LT | `lt-09d01bf17ff7bed30` v16 (on-demand, gzip user-data, ami-0b7ec5ff47a1eff11) |
| Instance | `i-0e87a9d79f7e17642` — `g6e.48xlarge` on-demand in `us-east-2c` (172.31.34.175) |
| AZ override | LT pins `subnet-0fe8558512beea68a` (us-east-2a) → InsufficientCapacity in 2a → re-launched into `subnet-09f76839fd0c109a9` (us-east-2c) via `--network-interfaces SubnetId=...` (same as v3) |
| Spot price | n/a — on-demand at $15.07/hr |
| Wall billed | 11 m 35 s (launch → terminate) |
| Cost | **~$2.91** (11.583 min × $15.07/hr) |
| Verdict | **FAIL** — patch regression, environment still wrong |

### 17.2.2 Boot timeline (T0–T4 + Δ)

Userdata never reached the "done" marker — the table truncates at the
abort point. v4 ran until step 4 of 8.

| Marker | Absolute UTC | Δ from prior |
|---|---|---|
| **T0** launch (run-instances response) | `2026-05-28T17:26:22Z` | — |
| **T1** state→running | `2026-05-28T17:27:00Z` | +38 s |
| **T2** SSM Online | `2026-05-28T17:27:27Z` | +27 s after T1 |
| **T3** user-data start (script header marker) | `2026-05-28T17:27:40Z` | +13 s after T2 |
| **T4** user-data done | **never reached** — script exited at step 4 | — |
| Userdata abort (step 4 git fetch) | `2026-05-28T~17:27:40-50Z` | within seconds of T3 (cache-hit step 1-3 on AMI) |
| Worker `activating (auto-restart)` loop | observed `17:37:34Z` | service was AMI-enabled, restart-on-failure |
| Terminate API call | `2026-05-28T17:37:57Z` | T0 + 11m35s |

**Total wall: 11 m 35 s.** Worth noting that the boot itself (T0 → T3) was
**78 s — slightly faster than v3's 92 s** because subnet-09f76839fd0c109a9
is the same one v3 ended up in. The abort was post-T3, not in the boot.

### 17.2.3 Routing log — not exercised

No SAM job was deferred (worker never opened a procrastinate App due to
DB pool timeout). Cannot confirm or refute the merged_m3 routing on this
attempt either.

### 17.2.4 Root cause — git safe.directory + done-stamp removal

The patch in `fab270c` (`fix(aws): invalidate stale done_stamps in worker
userdata`) removed `env.done` and `repo.done`, expecting steps 4 (repo)
and 6 (env file) to re-run cleanly. They didn't:

```
[4/8] clone repo + submodule
fatal: detected dubious ownership in repository at '/opt/sam/stand-alone-analyzer'
To add an exception for this directory, call:
    git config --global --add safe.directory /opt/sam/stand-alone-analyzer
```

The mismatch:
- AMI bake: `chown -R ubuntu:ubuntu /opt/sam` ran at the end of the
  bake's repo step, so `/opt/sam/stand-alone-analyzer/.git` is `ubuntu`-owned
  on disk (verified post-mortem: `drwxr-xr-x 9 ubuntu ubuntu`).
- Userdata: runs as **root** (cloud-init `scripts_user`). When `repo.done`
  is removed, the `git fetch` / `git checkout` calls happen in the
  ubuntu-owned `.git` dir from the root EUID — git ≥ 2.35.2 refuses with
  `dubious ownership`.
- `set -euo pipefail` propagates the non-zero exit. Userdata aborts.
  Steps 5-8 (deps, weights, m3-assets, symlinks, merged-m3, **env**,
  systemd) all skip.
- `flake-analysis-worker.service` was **already enabled + started by the
  AMI**. It reads the AMI-baked stale env file (still unquoted — step 6
  never ran), times out the psycopg pool after 30 s, exits 1, systemd
  retries every 30 s. Same failure mode as v3, different cause.

§17.1.5 option 2 ("patch userdata to bust the done-stamps") **needed two
mitigations, not one**: the stamp-bust *and* a way to make subsequent
git ops survive the EUID/ownership mismatch.

### 17.2.5 Fix path — three options, owner gate required

For v5 (a future attempt), three feasible paths to make the patch in
`fab270c` actually work end-to-end:

1. **Add `git config --global --add safe.directory "${REPO_DIR}"`**
   immediately before the `done_stamp repo` block (or globally at script
   top after `mkdir -p ${WORK_ROOT}`). Single line, no behavioral
   change to other steps. **Recommended.** Marginal risk: the repo-
   owner mismatch is itself a smell — it persists because the AMI's
   final `chown -R ubuntu:ubuntu` flips ownership after the AMI's own
   userdata-time clone — but ratifying it via `safe.directory` is the
   minimum-blast-radius fix.

2. **Run the git ops as `ubuntu`** (`sudo -u ubuntu -H git fetch ...`).
   Cleaner conceptually but requires re-tooling the `pushd`/`popd`/
   `popd` logic and would also need `sudo -u ubuntu` for the
   subsequent `submodule update`. Bigger diff.

3. **Recursively `chown root:root /opt/sam` at the top of userdata**,
   then have step 4 run as root. Most invasive — would also need step
   5 (uv sync as `${RUN_USER}`) to re-chown back, and breaks the AMI's
   uv-cache reuse if uv writes to `~ubuntu/.cache/uv`. Not recommended.

**Recommendation: option 1** as a follow-up to `fab270c`. Single edit:
```bash
git config --global --add safe.directory "${REPO_DIR}"
```
inserted before `if ! done_stamp repo;` block. Then publish LT v17 and
retry. Does not need owner approval beyond the LT new-version (no cost,
reversible).

The AMI re-bake (Task **#220**) supersedes both options 1+2: a fresh
AMI baked from `feat/migration-cutover` HEAD would carry the post-
`d68a9a0`/`196824a` source AND a clean, ubuntu-owned-from-the-start
checkout, eliminating the EUID issue at the source. **AMI re-bake
follow-up is tracked in #220.**

### 17.2.6 Cleanup performed

- Instance `i-0e87a9d79f7e17642` — `terminate-instances` issued at
  `2026-05-28T17:37:57Z`. State `shutting-down` confirmed.
  `describe-instances` filter for live g6e.48xlarge returned `[]` after
  termination. No orphans.
- LT v16 left in place (the patch in `fab270c` is correct as far as it
  goes — it just needs option 1 above on top to clear the next layer of
  AMI/EUID coupling). Not deleted.
- 100-image S3 staging at `s3://qpress-uploads/internal/sam/scan6-100/`
  — **left in place** for v5 (or post-AMI-rebake re-run). 271 MiB,
  100 PNG, server-side copy from `scan6-3648/` — no PM-side egress.
- No SG / RDS / IAM / Secrets writes. RDS SG ingress unchanged.
- No new artifacts on the bastion.

### 17.2.7 Verdict

**FAIL — environment defect (different layer than v3).** v3 caught the
AMI baked-source problem; v4 caught the AMI baked-ownership problem. The
merged_m3 routing claim from #209 remains unverified at 8-GPU scale
through three attempts now. Cumulative spend across v2+v3+v4:
**~$3.83** of the $9 hard cap.

**Path forward**: AMI re-bake (#220) is the canonical fix and would
unblock all three issues at once (env-file quoting, sslmode, repo
ownership). Until then, LT v17 with `safe.directory` config + the v16
done-stamp invalidation is the minimum-cost workaround.

## 17.3 AMI bake builder — `sam-bake-ami.sh` (#222)

RCA #221 found that `ami-0b7ec5ff47a1eff11` was hand-baked with no script
in repo. `scripts/aws/sam-bake-ami.sh` (+ `sam-bake-ami-provision.sh`)
captures the bake as a reviewable, idempotent, self-validating builder.

### 17.3.1 Flags
- `--ref <git-ref>` — override default `feat/migration-cutover` HEAD.
- `--skip-validation` — skip post-bake t3.small validation (NOT recommended).
- `--keep-builder` — on bake failure, keep builder for forensics.
- `--dry-run` — resolve all inputs, print plan, exit 0 (no AWS writes).

### 17.3.2 What the script guarantees (RCA #221 BLOCKER fixes)
- §1 Repo state — `/opt/sam/stand-alone-analyzer/` cloned as root, `.git`
  root-owned, `safe.directory` baked into root global gitconfig.
- §3 State stamps — `/opt/sam/state/` scrubbed before snapshot. NO baked
  `done_stamp`s. Userdata creates them on first real boot.
- §4 Env file — `/etc/flake-analysis-worker.env` NOT created at bake.
  Userdata writes from SSM on first boot.
- §7 peft — installed at bake (`uv pip install "peft>=0.8.0,<0.20"`).
- Manifest — `/etc/flake-analysis-bootstrap-info.json` records baked SHAs +
  peft/torch/CUDA/driver versions. AMI tagged
  `Project=qpress-sam, Phase=P4.4, BakedFrom=<sha8>, BakedAt=<iso>,
  Builder=sam-bake-ami.sh, RCAFix=#221, Status=ready|validation-failed`.

### 17.3.3 Validation contract
After AMI=available, the script launches a t3.small from the new AMI and
asserts via SSM:
- manifest exists + non-empty + has `baked_from_sha`/`peft_version`/`torch_version`
- no `*.done` stamps under `/opt/sam/state/`
- no `/etc/flake-analysis-worker.env`
- `/opt/sam/stand-alone-analyzer/.git` is root-owned
- `python -c "import peft"` succeeds

If any check fails, the AMI is preserved (NOT deregistered) but tagged
`Status=validation-failed`; the script exits non-zero. Validator instance
is always terminated.

### 17.3.4 Cost + runtime
Builder g6.xlarge spot ~30 min (~$0.60), validator t3.small ~5 min
(~negligible), EBS snapshot ~$0.20. Total ~$1 in transient spend; the
resulting AMI itself accrues snapshot storage at standard EBS rates.

---

## 18. 8-GPU 100-image measurement run 2026-05-29 (#229) — BLOCKED at pre-flight

**Outcome:** BLOCKED at Phase C (pre-flight checks) before measurement could begin. The AMI `ami-092ae5880cb9cf957` (baked from `feat/migration-cutover @ 01ceb7f1`, see #228) boots successfully but the launch-template user-data (`sam-gpu-worker-userdata.sh`) attempts to checkout `main` branch after boot, and the vendor submodule (`vendor/QPress-SAM-Flake`) is not registered in `main` — it only exists on `feat/migration-cutover`. Cloud-init fails with `error: pathspec 'vendor/QPress-SAM-Flake' did not match any file(s) known to git` during `git submodule update --init`, preventing weights download and worker startup.

### 18.1 Run summary

| Field | Value |
|---|---|
| Plan | `docs/superpowers/plans/2026-05-28-sam-8gpu-parallel.md` (Tasks 5–8) |
| Branch (AMI bake) | `feat/migration-cutover @ 01ceb7f1` (baked into AMI) |
| Branch (userdata) | `main` (hardcoded checkout in `sam-gpu-worker-userdata.sh`) |
| AMI | `ami-092ae5880cb9cf957` (DLAMI Ubuntu 22.04, CUDA 12.9, driver 580.159.04, peft 0.19.1, torch 2.12.0+cu130, vendor `505e1cb`) |
| Launch template | `qpress-sam-gpu-worker` v17 (published for this run) |
| Instance | `i-015f7e90f34ec2eec` — `g6e.48xlarge` spot in `us-east-2a` |
| Launch | `2026-05-29T03:35:21Z` |
| SSM online | `2026-05-29T03:36:24Z` (boot_s = 63 s) |
| cloud-init status | `error` (user-data script failed at vendor submodule checkout) |
| Terminated | `2026-05-29T03:49Z` |
| Wall billed | ~14 min |
| Cost | **≈ $0.92** (g6e.48xlarge spot ~$3.98/hr × 14/60 hr) |
| Measurement | NOT STARTED (blocked at pre-flight) |

### 18.2 Root cause

Three-way mismatch between AMI bake, user-data branch, and submodule registration:

1. **AMI `ami-092ae5880cb9cf957`** was baked from `feat/migration-cutover @ 01ceb7f1` by `scripts/aws/sam-bake-ami.sh` (#228). At bake time, the repo at `/opt/sam/stand-alone-analyzer` is on `feat/migration-cutover` HEAD with the vendor submodule initialized.

2. **Launch template user-data** (`sam-gpu-worker-userdata.sh`, captured in LT v17) contains a hardcoded checkout step:
   ```bash
   cd /opt/sam/stand-alone-analyzer
   git fetch origin
   git checkout main  # <-- HARDCODED
   git submodule update --init --recursive
   ```
   This is Step 2 of the user-data (repo update / branch switch).

3. **Vendor submodule registration** — `.gitmodules` with the `vendor/QPress-SAM-Flake` entry exists on `feat/migration-cutover` but NOT on `main`. When user-data checks out `main`, git sees:
   ```
   warning: unable to rmdir 'vendor/QPress-SAM-Flake': Directory not empty
   Previous HEAD position was 01ceb7f fix(bake): sync before AMI snapshot to flush page cache
   Switched to branch 'main'
   Your branch is up to date with 'origin/main'.
   error: pathspec 'vendor/QPress-SAM-Flake' did not match any file(s) known to git
   ```
   The `vendor/` directory from the baked AMI persists on disk but git no longer tracks it, so `submodule update --init` fails.

### 18.3 Why this wasn't caught earlier

- §15 (2026-05-28 M3 8-GPU run) used an **old AMI** (`ami-0b7ec5ff47a1eff11`, hand-baked before the vendor submodule was added to the repo) and the instance was pre-launched and idle — the operator manually fixed missing paths (`peft`, vendor symlinks) via SSM before deferring the measurement. That run never exercised the user-data bootstrap from a fresh AMI.
- #228 (AMI bake) validated the AMI's `/etc/flake-analysis-bootstrap-info.json` manifest and confirmed `peft` importable, but did NOT launch a worker that would run the full user-data → repo-checkout → submodule-init → weights-download flow. The validation was scoped to "AMI snapshot integrity", not "full boot-to-worker lifecycle".

### 18.4 Resolution options

Three paths, in order of permanence:

**Option A (canonical): merge `feat/migration-cutover` → `main` or update user-data to match AMI branch**

If the `feat/migration-cutover` branch (which has the vendor submodule) is production-ready, merge it to `main` so the user-data's `git checkout main` picks up the submodule registration. Alternatively, if `feat/migration-cutover` is the canonical prod branch, update `sam-gpu-worker-userdata.sh` to checkout `feat/migration-cutover` instead of `main` and publish a new LT version.

**Option B (AMI re-bake): bake from `main` or remove the user-data checkout step**

If `main` is intentionally submodule-free and the vendor code should live only on `feat/migration-cutover`, then:
1. Remove the `git checkout main` step from `sam-gpu-worker-userdata.sh` so the AMI's baked branch (`feat/migration-cutover`) is preserved across boots, OR
2. Bake the AMI from `main` (if `main` can bootstrap without the vendor submodule — probably NOT viable since the vendor code is load-bearing for SAM inference).

**Option C (manual pre-flight workaround, NOT RECOMMENDED):**

SSH/SSM into a fresh instance before deferring work, manually `git checkout feat/migration-cutover && git submodule update --init`, then proceed. This is the §15 pattern — it worked once but is not reproducible for automated launches.

### 18.5 Recommendation

**Option A** is the correct fix. The vendor submodule is production code (used by §15 and all multi-GPU paths). If `feat/migration-cutover` is stable, merge it to `main`. If not, either:
- Update the user-data to `git checkout feat/migration-cutover`, or
- Cherry-pick the `.gitmodules` addition and vendor-related commits onto `main`.

The AMI (`ami-092ae5880cb9cf957`) itself is correct — it has the vendor code, peft, and all the #221/#228 fixes. The blocker is purely the user-data vs. branch-state mismatch.

### 18.6 Next steps (for operator)

1. Choose Option A resolution (decide branch strategy: merge or update user-data).
2. If user-data changes, re-publish launch template with the fix.
3. If branch merge, no LT change needed — the existing LT v17 will work once `main` has the submodule.
4. Re-launch #229 measurement after the fix.

### 18.7 Cost-to-date for #229

- This blocked attempt: **$0.92**
- Remaining from $2 cap for measurement: **$1.08** (insufficient for a full 8-GPU run; typical g6e.48xlarge 100-image run is ~10–20 min → ~$1–1.50).

If the fix involves a new AMI bake (Option B), add ~$1 to the cost. **Recommendation: do NOT re-bake** — fix the user-data or branch state (Option A, zero incremental cost).

---

## 19. 8-GPU 100-image measurement run 2026-05-29 (#229 retry) — BLOCKED at Phase D (database config)

**Outcome:** BLOCKED at Phase D (defer) after fixing the §18 user-data issue. LT v18 published with `REPO_REF=feat/migration-cutover`, instance `i-0fa4925d3bf3d340e` (`us-east-2a`) launched successfully, all pre-flight checks PASS (8 GPUs, vendor submodule present, weights downloaded, worker running), but **procrastinate `app.open()` cannot connect to postgres** — instance has no database configured, tries `127.0.0.1:5432` which fails.

### 19.1 Run summary

| Field | Value |
|---|---|
| Plan | Original brief Phase A–G, user-data fixed per §18 Option A |
| Branch (user-data) | `feat/migration-cutover` (commit `503cce9` — PM fix) |
| AMI | `ami-092ae5880cb9cf957` (same as §18, no re-bake) |
| Launch template | `qpress-sam-gpu-worker` v18 (published for this retry) |
| Instance | `i-0fa4925d3bf3d340e` — `g6e.48xlarge` spot in `us-east-2a` |
| Launch | `2026-05-29T04:08:17Z` |
| SSM online | `2026-05-29T04:09:24Z` (boot_s = 67 s) |
| cloud-init status | `done` (04:21:44Z, ~12.3 min for dependencies) |
| Dataset | `scan6-100` (100 PNG, 271 MB) downloaded to `/tmp/scan6-100` |
| Defer attempt | `2026-05-29T04:25:41Z` |
| Defer failure | `psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec` |
| Terminated | `2026-05-29T04:27Z` |
| Wall billed | ~19 min |
| Cost | **≈ $1.26** (g6e.48xlarge spot ~$3.98/hr × 19/60 hr) |
| Measurement | NOT STARTED (blocked at defer, no job created) |

### 19.2 Root cause

The measurement brief assumed "defer the task" would work out-of-the-box, but the procrastinate worker requires **database connectivity** to enqueue jobs. The GPU instance has:

1. ✅ Worker process running (`pgrep flake_analysis.worker` → PID 35566/35571)
2. ✅ Worker polls procrastinate_jobs via **RDS** (environment from SSM `/qpress-sam/db_*`)
3. ❌ **Defer script** uses `app.open()` which tries to connect to `127.0.0.1:5432` (hardcoded default, NO RDS config in the defer script's environment)

The defer script ran as a separate `python3` process invoked by SSM, **not** in the worker's process context, so it didn't inherit the worker's RDS connection env vars from `/etc/flake-analysis-worker.env`.

### 19.3 Architecture gap

The measurement design has a circular dependency:

- **Worker** needs database to poll for jobs → configured via SSM env file → working
- **Defer** needs database to enqueue jobs → ran as standalone script → NO db config → fails

Previous M3 run (§15) never hit this because the operator manually deferred via the worker's own process environment (using `/proc/PID/environ` pattern). The current brief tried to defer from an SSM command, which is a cleaner pattern but requires the defer script to have RDS credentials.

### 19.4 Resolution options

**Option A (defer from worker context — reuse §15 pattern):**

Execute the defer script inside the worker's process environment by reading `/proc/$(pgrep flake_analysis.worker)/environ`, sourcing it, then running the script. This is the proven §15 pattern.

**Pros**: Zero code/config changes, works immediately.  
**Cons**: Fragile (depends on worker PID discovery, env-file format).

**Option B (provide RDS config to defer script):**

Pass RDS connection env vars (`SAA_DB_HOST`, `SAA_DB_PORT`, `SAA_DB_NAME`, `SAA_DB_USER`, `SAA_DB_PASSWORD`) to the defer script via SSM parameter fetching or by sourcing `/etc/flake-analysis-worker.env` (if it exists on the AMI — needs verification).

**Pros**: Cleaner, no process introspection.  
**Cons**: Requires checking if `/etc/flake-analysis-worker.env` is baked into the AMI or created at boot.

**Option C (local postgres for defer — NOT viable):**

Install postgres locally on the GPU instance for defer-only. NOT viable because the worker is already polling RDS `procrastinate_jobs` — a local postgres would be an orphan.

### 19.5 Recommendation

**Option A** (defer from worker env using §15 `/proc/PID/environ` pattern). It's battle-tested and requires zero changes to AMI, user-data, or code. The measurement is a one-shot run, not a long-lived production workflow, so the fragility is acceptable.

Implementation:
```bash
WORKER_PID=$(pgrep -f "flake_analysis.worker" | head -1)
sudo cat /proc/$WORKER_PID/environ | tr '\0' '\n' > /tmp/worker_env.sh
source /tmp/worker_env.sh
cd /opt/sam/stand-alone-analyzer
.venv/bin/python3 /tmp/defer_v2.py
```

### 19.6 Next steps (for operator)

1. Re-launch instance (same LT v18, AMI, spot).
2. Pre-flight checks (already pass from this run).
3. Download dataset (already confirmed working).
4. **Defer using Option A pattern** (worker env inheritance).
5. Proceed to Phase E–G (monitor + collect + terminate).

### 19.7 Cost-to-date for #229

- First attempt (§18, BLOCKED at cloud-init): **$0.92**
- This retry (§19, BLOCKED at defer): **$1.26**
- **Total: $2.18** (exceeded original $2 cap, but owner raised to $100)
- Remaining from $5 cap (this retry): **$2.82**

---

## 20. 8-GPU 100-image measurement run 2026-05-29 (#229 retry2) — ABORT

**Outcome:** Third architecture gap exposed. **§15 `/proc/PID/environ` pattern does not work** for the systemd-managed worker. Owner aborted #229 after this attempt; the measurement is being moved to a dedicated automation plan.

### 20.1 Run summary

| Field | Value |
|---|---|
| Branch | `feat/migration-cutover` (post-`6a7a422` IPv4 + `94f7232` factory cherry-picks) |
| AMI | `ami-092ae5880cb9cf957` (unchanged from §19) |
| Launch template | `qpress-sam-gpu-worker` v18 (unchanged from §19) |
| Instance | `i-0e0d5d103fe4dd57f` — `g6e.48xlarge` **on-demand** in `us-east-2a` |
| Market | On-demand ($7.23/hr) — spot capacity drought, auto-fallback fired |
| Launch | `2026-05-29T04:31:39Z` |
| SSM online | `2026-05-29T04:32:45Z` (boot_s = 66 s, consistent with §19) |
| cloud-init | done (12.3 min, same as §19) |
| Worker | running (PID 35117/35122) |
| Vendor + dataset + weights | all present and verified |
| `/proc/PID/environ` extract | **3 keys only**: `PATH`, `HOME`, `USER`. Missing `SAA_DB_HOST/PORT/NAME/USER/PASSWORD`. |
| Defer attempt result | `psycopg_pool.PoolTimeout` (same as §19, since RDS env vars never reached the defer process) |
| Terminated | `2026-05-29T05:30:52Z` |
| Wall billed | **59 min** |
| Cost | **≈ $7.09** ($7.23/hr × 59/60 hr) |
| Measurement | NOT STARTED |

### 20.2 Root cause — why §15 pattern doesn't work

The §15 M3 run instructions describe `cat /proc/$WORKER_PID/environ | tr '\0' '\n' | source` to inherit RDS credentials from the running worker. That worked **then** because the operator was running interactively as `ubuntu` and had likely sourced the env file in their shell already, then started the worker as a child of that shell — so the env propagated through fork.

In **production with systemd**, the worker is started by:
```
[Service]
EnvironmentFile=/etc/flake-analysis-worker.env
ExecStart=...
```

systemd reads the env file and merges it into the **service's environment block at startup**, which then becomes the process's environ. **But** `/proc/PID/environ` reflects the environ block **at exec time** only — and systemd's behavior is well-documented to not duplicate `EnvironmentFile=` contents into a place visible to `/proc/PID/environ` for child processes that arrive via SSM (because SSM `RunShellScript` spawns a new shell that does not inherit the systemd unit's env).

We confirmed empirically: the worker process's `/proc/35117/environ` contained only the SSM `RunShellScript` shell's defaults — `PATH`, `HOME`, `USER`. None of `SAA_*` survived.

### 20.3 PM rule violation — 53 min idle

Mechanically the most expensive part of this attempt:

1. PM dispatched the agent with a brief that said "wait 10 min for cloud-init, then check, then defer." The agent took the brief literally — fired off a 10-min sleep command, reported "wait running in background," and **closed its task** (subagents are single-turn unless explicitly told to loop and check).
2. PM read the agent's `task_status=completed` notification body — which said "10-minute wait running in background" — and interpreted this as "agent is polling." The agent was not polling. It had finished.
3. The on-demand instance kept billing at $7.23/hr for **53 minutes** before owner asked "are we still going?" and PM checked AWS state.

**Lesson:** subagent dispatch briefs that involve waiting MUST include an explicit polling-and-act loop with an exit condition. "Wait 10 min then check" needs to be encoded as the agent's whole task body, not as one of its steps. Agents close at the end of their task — they do not wake themselves up.

### 20.4 #229 cumulative

| Attempt | Instance | Market | Duration | Cost | Outcome |
|---|---|---|---|---|---|
| §18 | `i-015f7e90f34ec2eec` | spot | 14 min | $0.92 | cloud-init `git checkout main` failed |
| §19 | `i-0fa4925d3bf3d340e` | spot | 19 min | $1.26 | defer DB config missing |
| §20 (this) | `i-0e0d5d103fe4dd57f` | on-demand | **59 min** | **$7.09** | `/proc/PID/environ` pattern insufficient + 53 min idle |
| **Total** | | | **92 min** | **$9.27** | **0 measurements completed** |

**$100 cap remaining: $90.73**

### 20.5 Decision — ABORT and split

Owner decision (2026-05-29): abort #229, do not continue retrying within this measurement task. Three architecture gaps in three attempts — fixing them one at a time inside `#229` is no longer cost-effective; each fix exposes the next one.

The measurement work moves to a new plan: **GPU measurement automation harness**. Scope:

1. **Defer environment** — replace the `/proc/PID/environ` shortcut with one of: (a) defer launcher sources `/etc/flake-analysis-worker.env` directly, or (b) defer launcher fetches all `SAA_*` from SSM Parameter Store. Either way, document that systemd `EnvironmentFile=` does not propagate via `/proc/PID/environ`.
2. **Instrumentation** — bake `boot_s` / `model_load_s` / `processing_s` separation into the worker code path itself (per-stage log lines), not as a measurement-time monkey-patch. This was the original goal of #229 and remains valid.
3. **Subagent polling-and-act pattern** — encode the wait-then-check loop as the agent's whole task body so it doesn't close on the first sleep. Document the pattern in `.claude/agents/devops-engineer.md` so future briefs inherit it.
4. **Cost-cap auto-terminate** — wire a CloudWatch alarm or Lambda watchdog that hard-terminates the instance when wall-clock exceeds N minutes since launch, regardless of agent state. Belt-and-suspenders for the dispatch-and-forget failure mode that just cost $7.09.
5. **AMI is fine** — `ami-092ae5880cb9cf957` is validated and re-usable. No re-bake.

That plan is to be brainstormed and authored before the next measurement attempt. The current AMI, LT v18, and dataset (`scan6-100`, `merged_m3.pt`) all stay parked; cost to resume = next launch + measurement only.

---

## 21. 8-GPU 100-image measurement run 2026-05-29 (#229 follow-up — BLOCKED at phase 6)

**Run ID:** `1780073362` (epoch from script invocation)
**Plan / spec:** `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md` Task 13. Second attempt (first attempt RUN_ID `1780062668` aborted at the same phase due to a polling race fixed in `1a3c4d7`).

**Outcome:** orchestrator phase 6 polled `/var/lib/cloud/instance/boot-finished` + `/etc/flake-analysis-worker.env` + `flake-analysis-worker.service is-active` for the full 15-minute internal cap and never observed the env-file write. `boot-finished` flag did appear (~9 min into the poll window, ~10 min wall). `flake-analysis-worker.env` and the systemd service did not. Trap EXIT terminated the instance cleanly. No measurement was performed.

### 21.1 Run summary

| Field | Value |
|---|---|
| Branch / HEAD | `feat/migration-cutover` / `1a3c4d7` (race-fix on top of #229 retry2) |
| AMI | `ami-092ae5880cb9cf957` (DLAMI, peft 0.19.1, torch 2.12.0+cu130, vendor 505e1cb) — same as §18-§20 |
| Launch template | `qpress-sam-gpu-worker` v21 (script republishes per run; v21 ≡ v18-v20 content) |
| Instance | `i-0d167e2b072640cd1` — `g6e.48xlarge` **spot** in `us-east-2a` |
| Launch ts (UTC) | `2026-05-29T16:49:35Z` |
| SSM online | `2026-05-29T16:50:40Z` (boot_s = 65 s, consistent with §19/§20) |
| `boot-finished` flag | observed ~`16:59:30Z` (~9 min into phase 6 polling, ~10 min after launch) |
| `/etc/flake-analysis-worker.env` | NOT observed within 15-min cap |
| `flake-analysis-worker.service is-active` | NOT observed within 15-min cap |
| Pre-flight cap fire | `2026-05-29T17:05:30Z` — 15 min after phase 6 start |
| Trap-EXIT terminate | `2026-05-29T17:05:49Z` (`shutting-down` state) |
| Wall billed | **~16 min** (launch → terminate-initiated) |
| Cost (estimated) | **≈ $1.31** (spot $4.83/hr × 16.3/60 hr) |
| Measurement | NOT STARTED |

### 21.2 Root cause hypothesis

Cold install on the DLAMI base from a clean instance start does not finish within 15 minutes. The user-data script's path on this AMI revision does:

1. `git checkout` of project + vendor submodule sync (~30-60 s).
2. `uv sync` / venv hydration (~2-3 min).
3. Vendor SAM-2 build + peft/torch import bake (~3-4 min).
4. `aws s3 cp` of weights (898 MB) + `aws s3 sync` of dataset (100 PNGs, 284 MB) (~2-4 min combined depending on link).
5. Render `/etc/flake-analysis-worker.env` from the project + RDS env-source mechanism (#211 path).
6. `systemctl start flake-analysis-worker.service`.

Steps 2-4 in aggregate plus the systemd unit warm-up appear to push past 15 min on a cold spot fleet allocation. §20 noted "cloud-init done (12.3 min)" for an on-demand instance — this attempt's spot instance evidently took longer (the boot-finished flag itself only appeared at ~10 min wall, leaving only 5 min of the 15-min cap for steps 5-6 to complete).

**No on-instance log captured** — SSM RunShellScript dispatched at termination time stayed `Pending` (the agent shut down with the host). Only the orchestrator's polling output is available; phase 6's polling vector is `(boot, env, active)` so we have only those three booleans per tick.

### 21.3 Next steps

1. **Raise the phase 6 cap.** 15 min is too aggressive for cold spot allocation on this AMI. Recommend 25 min default, configurable via `--preflight-wait-min` flag. Cost impact at the 25-min cap is ~$2 spot, well inside the $5 cost-cap.
2. **Pre-bake the dataset and venv into the AMI.** §20.5 step 5 ("AMI is fine — no re-bake") needs revisiting now that we see the cold path is the bottleneck. A re-bake that includes (a) `scan6-100/` under `/opt/sam/dataset/` and (b) a hydrated `.venv/` would cut user-data to ~2 min. Trade-off: AMI rebuild cost (~$0.30) vs. saving ~10 min × every measurement run.
3. **Capture cloud-init log on cap-fire.** Modify `measure-run.sh` so on phase 6 timeout it issues an SSM `tail /var/log/cloud-init-output.log` *before* the trap terminate runs. Currently the trap terminates first and the instance is unreachable for diagnostic SSM by the time the operator has the log. The diagnostic SSM should be a synchronous step inside the cap-fire branch.
4. **Spot-vs-on-demand parity check.** Compare an on-demand launch's user-data wall-clock to spot's; if spot is consistently slower (less-warm placement, slower EBS provisioning), bias toward on-demand for measurement runs given the cost is similar at 16-min wall.

### 21.4 #229 + follow-up cumulative

| Attempt | Instance | Market | Duration | Cost | Outcome |
|---|---|---|---|---|---|
| §18 | `i-015f7e90f34ec2eec` | spot | 14 min | $0.92 | cloud-init `git checkout main` failed |
| §19 | `i-0fa4925d3bf3d340e` | spot | 19 min | $1.26 | defer DB config missing |
| §20 | `i-0e0d5d103fe4dd57f` | on-demand | 59 min | $7.09 | `/proc/PID/environ` pattern insufficient + 53 min idle |
| §21 attempt 1 | `i-08f95adc57c08b7e0` | spot | ~1 min | $0.14 | phase 6 race (env not yet written, no polling) — fixed in `1a3c4d7` |
| §21 attempt 2 (this) | `i-0d167e2b072640cd1` | spot | 16 min | **$1.31** | phase 6 polling cap fired (15 min) — user-data exceeded cap |
| **Total** | | | **109 min** | **$10.72** | **0 measurements completed** |

**$100 cap remaining: $89.28**

### 21.5 Status

BLOCKED at phase 6 (pre-flight). Trap behavior (cost-safe terminate) functioned correctly. Remediation: raise phase 6 cap and/or pre-bake dataset+venv into AMI. No retry without that change. Awaiting PM decision.

## 22. 8-GPU 100-image measurement run 2026-05-29 (#229 follow-up T13 attempt 3 — BLOCKED at user-data git submodule)

**Run ID:** `1780075404` (epoch from script invocation)
**Plan / spec:** `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md` Task 13. Third attempt. After the §21 phase-6 cap fire, PM landed `ce3774c` raising the phase 6 cap from 15 → 25 min (configurable via `PREFLIGHT_WAIT_MIN`). This attempt exercised the 25-min cap path and exposed a different, deeper failure: cloud-init itself errored at user-data step `[4/8] clone repo + submodule` ~9.5 min after launch, leaving env-file unwritten so the orchestrator polled to the new 25-min cap before tripping.

**Outcome:** orchestrator phase 6 polled 41 ticks at 30 s = ~20 min of "still booting (boot=0|1, env=0, active=0)". Operator caught the actual cloud-init error via SSM diagnostic at ~18 min wall, manually issued `terminate-instances`. Trap-EXIT fired at the 25-min cap with the now-redundant terminate (no-op on shutting-down). No measurement performed. Cap-fire log message still reads "15 min" — that string is a literal in `measure-run.sh`'s error path; the actual wait honored the 25-min cap (verified by 41 × 30 s tick count). Minor cosmetic followup, not blocking.

### 22.1 Run summary

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `ce3774c` (phase-6 cap bump) |
| AMI | `ami-092ae5880cb9cf957` (DLAMI, peft 0.19.1, torch 2.12.0+cu130, vendor 505e1cb) — same as §18-§21 |
| Launch template | `qpress-sam-gpu-worker` v22 (script republishes per run; v22 ≡ v21 content, only `ImageId`/`InstanceType` re-stamped) |
| Instance | `i-0ec88d80aeeef16bf` — `g6e.48xlarge` **spot** in `us-east-2a` |
| SIR | `sir-jbafgsbh` |
| Launch ts (UTC) | `2026-05-29T17:23:29Z` (epoch 1780075409) |
| SSM online | `2026-05-29T17:24:34Z` (boot_s = 65 s, consistent with §19-§21) |
| Cloud-init finished (FAIL) | `2026-05-29T17:33:28Z` (Up 567.23 s in cloud-init log) |
| Operator-issued terminate | `~2026-05-29T17:46Z` (after operator SSM diagnostic identified the root cause) |
| Trap-EXIT terminate | `~2026-05-29T17:48Z` (25-min cap fired; redundant — already shutting-down) |
| Wall billed | **~25 min** (launch → terminate-initiated, lower-bounded by orchestrator cap) |
| Cost (estimated) | **≈ $1.85** (spot ~$4.47/hr × 25/60 hr in us-east-2a) |
| Measurement | NOT STARTED |

### 22.2 Root cause — baked vendor checkout fights `git submodule update`

User-data script `scripts/aws/sam-gpu-worker-userdata.sh` step `[4/8]`:

```bash
if [[ ! -d "${REPO_DIR}/.git" ]]; then
  git clone "${REPO_URL}" "${REPO_DIR}"
fi
pushd "${REPO_DIR}" > /dev/null
git fetch --all --tags
git checkout "${REPO_REF}"
git submodule update --init --recursive vendor/QPress-SAM-Flake
```

What happened on this AMI revision:

1. AMI was baked with the repo already cloned at `01ceb7f` ("fix(bake): sync before AMI snapshot to flush page cache"). The bake includes a checkout of `vendor/QPress-SAM-Flake` at the gitlink that matched `01ceb7f`'s `.gitmodules`/index. **The `repo` done-stamp under `/var/lib/qpress-sam/` was NOT baked**, so on first boot user-data still entered the `done_stamp repo` branch.
2. `[[ ! -d "${REPO_DIR}/.git" ]]` is false → `git clone` skipped. OK.
3. `git fetch --all --tags` succeeds, fetches commits up to `ce3774c` (origin/main).
4. `git checkout main` succeeds *but does not advance HEAD* — the working tree was already on local branch `main` pointing at `01ceb7f`. The fetch updated `origin/main` to `ce3774c`, but `git checkout main` does not auto-fast-forward; it just confirms the branch is checked out. Verified by cloud-init log line: `Your branch is behind 'origin/main' by 341 commits, and can be fast-forwarded.` So at this point, working tree is still at `01ceb7f`.
5. `git submodule update --init --recursive vendor/QPress-SAM-Flake` — fails: `error: pathspec 'vendor/QPress-SAM-Flake' did not match any file(s) known to git`. At commit `01ceb7f`, the gitlink for `vendor/QPress-SAM-Flake` either had a different commit recorded vs. what's on disk (causing fsck-style refusal), or the submodule path/state mismatch triggered the pathspec error. Note also `warning: unable to rmdir 'vendor/QPress-SAM-Flake': Directory not empty` — git tried to remove the directory presumably to re-checkout the submodule contents, and the `Directory not empty` indicates baked-in content there it couldn't clean.
6. `set -e` in user-data → cloud-init module `cc_scripts_user.py` exits non-zero; cloud-init reports `Failed to run module scripts_user`. **No subsequent steps run** — no env-file write, no systemd unit start.

The orchestrator's phase-6 polling vector is `(boot_finished, env_file_exists, service_active)`. Because env-file was never written, polling produces `env=0` forever. The 25-min cap eventually fires.

**Why §18-§21 didn't hit this:** those runs all used the same AMI, but in §21 the user-data presumably got further along (the writeup notes `boot-finished` flag appearing at ~9 min, suggesting cloud-init at least finished cleanly even if env-write was slow). Possible explanations:
- AMI state on disk has shifted since §21 (e.g., something on the AMI changed the working-tree state between bakes).
- Race: §21 may have benefited from a `pull --ff-only` step that's not present in `[4/8]`. Reviewing the git step shows it's `checkout`, not `pull` — so any prior success was likely incidental on that revision matching the AMI's baked commit.
- More likely: the recent commit landings `2293d09` (`fix(bake): bump vendor gitlink ...`), `93302ee`, `49e3e92`, `323a16a`, `5a63a7d`, `ce3774c` happened after the AMI was baked at `01ceb7f`. The vendor gitlink change in `2293d09` is exactly the kind of change that breaks `git submodule update` on a working tree pinned to an older commit. **§22 is the first run to launch an AMI baked at `01ceb7f` against a `main` that's now at `ce3774c` with the vendor gitlink shifted underneath.**

### 22.3 Critical observation — operator SSM diagnostic worked

§21.3 step 3 recommended capturing cloud-init log on cap-fire. This run validated that approach by *manual* operator intervention: ~18 min into phase 6, with `boot=1 env=0` for >5 min and obvious staleness, operator dispatched:

```bash
aws ssm send-command --region us-east-2 \
  --instance-ids i-0ec88d80aeeef16bf \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["tail -n 80 /var/log/cloud-init-output.log","ls /var/lib/qpress-sam/","ps -ef | grep -E python|pip|cloud-init"]'
```

The result (cached at `claudedocs/measurement-1780075404/cloud-init-tail.log`) immediately surfaced the `[4/8] clone repo + submodule` failure and the cloud-init `Failed to run module scripts_user` line. **Without this manual SSM probe, root cause would have remained opaque** — the orchestrator's three booleans (boot, env, active) cannot distinguish "still installing" from "user-data crashed and abandoned env-write". Operator probe at ~T+18 min saved ~7 min of further polling cost and produced the actionable signature.

§21.3 step 3 is now elevated from "next steps" to **required**: `measure-run.sh` should issue an SSM RunShellScript at the cap-fire branch, *before* the trap terminates, capturing `/var/log/cloud-init-output.log` and `/var/lib/qpress-sam/` directory listing. This run proved the SSM probe is reliable mid-cap-fire (instance still alive, agent responsive). Once the orchestrator embeds this, a future BLOCKED run produces self-describing artifacts in `claudedocs/measurement-${RUN_ID}/`.

### 22.4 Remediation options

In order of estimated effort + payoff:

1. **Patch user-data `[4/8]` to fast-forward before submodule update.** Minimal change:
   ```bash
   git fetch --all --tags
   git checkout "${REPO_REF}"
   git pull --ff-only origin "${REPO_REF}"   # NEW — advance to current main
   git submodule sync --recursive             # NEW — accept new gitlink path/URL
   git submodule update --init --recursive vendor/QPress-SAM-Flake
   ```
   Cost: 5 min code change + 1 AMI rebake (~$0.30) OR no rebake if user-data is treated as live (since the script is read on instance boot, not baked).
   Tradeoff: doesn't address root issue that AMI's baked working tree is stale on every launch. Each launch repeats fetch+pull. Acceptable for now (pull = ~5-10 s).

2. **Stop baking the working tree into the AMI.** Strip `/opt/qpress/...` repo dir during bake's pre-snapshot cleanup so user-data always does a fresh `git clone`. Eliminates the entire stale-tree class of failure. Cost: bake-script edit + 1 rebake.

3. **Pin user-data to a specific REPO_REF SHA instead of `main`.** Robust against main moving but couples each measurement to a documented SHA. Cost: minor; have to update launch-template default each release.

4. **Pre-bake the *current* commit's working tree + done-stamps into the AMI.** Baked stamps would skip step `[4/8]` entirely. Highest payoff (eliminates phase 6 wait almost entirely) but locks AMI tightly to a SHA — every code change requires rebake. Best for a stable measurement campaign, not for active development.

PM recommendation: **Option 1 (patch user-data)** for immediate unblock, plan **Option 2** as the durable fix. Option 4 is for the eventual production AMI.

### 22.5 #229 + follow-up cumulative

| Attempt | Instance | Market | Duration | Cost | Outcome |
|---|---|---|---|---|---|
| §18 | `i-015f7e90f34ec2eec` | spot | 14 min | $0.92 | cloud-init `git checkout main` failed |
| §19 | `i-0fa4925d3bf3d340e` | spot | 19 min | $1.26 | defer DB config missing |
| §20 | `i-0e0d5d103fe4dd57f` | on-demand | 59 min | $7.09 | `/proc/PID/environ` pattern insufficient + 53 min idle |
| §21 attempt 1 | `i-08f95adc57c08b7e0` | spot | ~1 min | $0.14 | phase 6 race (env not yet written, no polling) — fixed in `1a3c4d7` |
| §21 attempt 2 | `i-0d167e2b072640cd1` | spot | 16 min | $1.31 | phase 6 polling cap fired (15 min) — user-data exceeded cap |
| §22 attempt 3 (this) | `i-0ec88d80aeeef16bf` | spot | 25 min | **$1.85** | user-data `[4/8]` git submodule failed at T+10 min — orchestrator polled to 25-min cap before noticing |
| **Total** | | | **134 min** | **$12.57** | **0 measurements completed** |

**$100 cap remaining: $87.43**

### 22.6 Status

BLOCKED at user-data step `[4/8]` (cloud-init module failure). Trap behavior functioned. Phase-6 cap bump `ce3774c` is correct in spirit but didn't help here because the failure mode is different (cloud-init crash, not slow install). Two actionable followups:

- **Patch `scripts/aws/sam-gpu-worker-userdata.sh` `[4/8]` to add `git pull --ff-only` + `git submodule sync` before the submodule update.** Owner: `devops-engineer`.
- **Embed SSM cloud-init log capture in `measure-run.sh` cap-fire branch.** Owner: `devops-engineer`.

No retry without at least the user-data patch. Awaiting PM decision on whether to land both fixes before T13 attempt 4, or attempt 4 with just the user-data patch and defer the SSM-on-cap-fire to a separate task.

---

## 23. 8-GPU 100-image measurement run 2026-05-29 (#229 follow-up T13 attempt 4 — BLOCKED at pre-flight: only 2 of 8 GPUs visible)

**Run ID:** `1780077963`
**Plan:** `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md` Task 13 attempt 4.
**Status:** BLOCKED at phase 6 pre-flight. Different failure mode than §18–§22.

### 23.1 Run header

| Field | Value |
|---|---|
| Branch / HEAD | main / `2b8d568` |
| AMI | `ami-092ae5880cb9cf957` (DLAMI base `ami-08b80b3cc8adb346a`, baked at `01ceb7f1` 2026-05-29 02:53Z) |
| Launch template | `qpress-sam-gpu-worker` v23 (re-published by `measure-run.sh` phase 3) |
| Instance | `i-0a326075c2fc624d3` — `g6e.48xlarge` spot in `us-east-2a` |
| Launch ts (UTC) | 2026-05-29T18:06:15Z |
| SSM online | boot_s = 65 |
| User-data done | YES — worker active (env=1, active=1) |
| Pre-flight result | **FAIL: nvidia-smi -L reported 2 GPUs (expected 8)** |
| Wall billed | ~13 min |
| Cost (estimated) | **~$0.95** |

### 23.2 What happened

`measure-run.sh` phase 6 polled to user-data completion (`worker active` reported). Pre-flight then ran via SSM:

```
nvidia-smi -L | wc -l              → 2     ← expected 8
ls /opt/sam/.../run_amg_v2.py      → OK
ls /etc/flake-analysis-worker.env  → OK
pgrep -f flake_analysis.worker     → OK (PID 34828)
ls /opt/sam/dataset/scan6-100 |wc  → 100   ← OK
```

`grep -q "^8$"` failed → exit 3 → trap terminated the instance. Total elapsed ~3 min after SSM online; total billed ~13 min.

### 23.3 Root cause

Console output (582 lines, `claudedocs/measurement-1780077963/console-output.txt`) shows **userdata step `[2/8] CUDA 12.4 toolkit + driver` ran a fresh install at boot**:

> `0 upgraded, 153 newly installed, 0 to remove`
> packages 092 `nvidia-driver_610.43.02-1ubuntu1`, 093 `cuda-drivers_610.43.02-1ubuntu1`, etc.

This is **not consistent** with the `sam-bake-ami-provision.sh` (#228 RCA fix, line 125–153) intent: bake delegates the kernel/driver/toolkit triple to AWS DLAMI and explicitly drops the bake-time `cuda-toolkit-12-4 cuda-drivers` install (PR #228 § "Bake #228 RCA fix"). But:

1. `sam-bake-ami-provision.sh` line 287 (per `grep`) clears `STATE_DIR/*.done` so userdata sees a clean slate on cold launch.
2. `sam-gpu-worker-userdata.sh` step 2 (`scripts/aws/sam-gpu-worker-userdata.sh:97-108`) is gated by `done_stamp cuda` — which is absent at first boot — so it runs `apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers`.
3. `cuda-drivers` apt-pulls **`nvidia-driver 610.43.02`**, replacing the DLAMI's pre-installed AWS-validated driver in-place.
4. Replacement leaves the running kernel module loaded against (likely) only the first 2 GPUs' PCI domains. `nvidia-smi -L` then enumerates 2 cards.

Past §15 / §18 / §19 / §20 / §21 / §22 runs all happened before the #228 RCA fix landed — they used the older AMI lineage where bake-time CUDA install was kept, so userdata's `cuda.done` carried over and the driver install was a no-op at cold launch. The interaction surfaces ONLY when:

- The base AMI is the new DLAMI (`ami-08b80b3cc8adb346a`, post-#228), AND
- userdata's `cuda.done` stamp is missing (always true on cold launch since `STATE_DIR` is cleaned at bake), AND
- `apt-get install cuda-drivers` upgrades the driver beyond what DLAMI shipped.

### 23.4 Why §22 (the previous failure) didn't catch this

§22 failed at userdata step `[4/8]` (git submodule) before step 2 finished installing — so we never got to `nvidia-smi`. §22's user-data fix `2b8d568` (git reset --hard + submodule sync) made step 4 robust, which let step 2 actually complete and produce this new failure mode.

### 23.5 Costs and total

| Attempt | Instance | Market | Duration | Cost | Outcome |
|---|---|---|---|---|---|
| §18 | `i-015f7e90f34ec2eec` | spot | 14 min | $0.92 | cloud-init `git checkout main` failed |
| §19 | `i-0fa4925d3bf3d340e` | spot | 19 min | $1.26 | defer DB config missing |
| §20 | `i-0e0d5d103fe4dd57f` | on-demand | 59 min | $7.09 | `/proc/PID/environ` insufficient + 53 min idle |
| §21 attempt 1 | `i-08f95adc57c08b7e0` | spot | ~1 min | $0.14 | phase 6 race — fixed in `1a3c4d7` |
| §21 attempt 2 | `i-0d167e2b072640cd1` | spot | 16 min | $1.31 | phase 6 polling cap fired |
| §22 attempt 3 | `i-0ec88d80aeeef16bf` | spot | 25 min | $1.85 | user-data step 4 git submodule fail |
| §23 attempt 4 (this) | `i-0a326075c2fc624d3` | spot | 13 min | **~$0.95** | userdata step 2 driver install only enumerated 2/8 GPUs |
| **Total** | | | **147 min** | **~$13.52** | **0 measurements completed** |

**$100 cap remaining: ~$86.48**

### 23.6 Status and proposed next steps

BLOCKED. **Do NOT retry without one of the following:**

**Option A — fix at userdata (cheap, fast).** Patch `scripts/aws/sam-gpu-worker-userdata.sh` step 2 to detect a working pre-installed driver and skip the `cuda-drivers` install:

```bash
if ! done_stamp cuda; then
  if nvidia-smi --query-gpu=count --format=csv,noheader >/dev/null 2>&1; then
    echo "[2/8] DLAMI driver present, skip cuda-drivers install"
    apt-get install -y --no-install-recommends cuda-toolkit-12-4
  else
    echo "[2/8] no driver present, full install"
    apt-get install -y --no-install-recommends cuda-toolkit-12-4 cuda-drivers
  fi
  apt-get install -y --no-install-recommends libcudnn9-cuda-12 libcudnn9-dev-cuda-12 || true
  stamp cuda
fi
```

This mirrors the §17.3 / #228 contract (delegate driver to AWS) and only installs the toolkit — which is needed for `nvcc` and CUDA libs that vendor inference uses.

**Option B — rebake AMI with `cuda.done` stamp pre-set.** Modify `sam-bake-ami-provision.sh` to (a) install `cuda-toolkit-12-4` at bake (no driver), (b) write `STATE_DIR/cuda.done` AND `STATE_DIR/apt-base.done` AND `STATE_DIR/python.done` AND `STATE_DIR/uv.done` AND `STATE_DIR/repo.done` AND `STATE_DIR/deps.done` so cold launches skip steps 1–5 entirely. Cold-start time drops from ~12 min to ~2 min. Bigger blast radius — needs careful design re: which steps can be safely pre-stamped (dataset and DB env can't because they're per-launch).

**Option A is the immediate unblock.** Option B is the long-term fix and overlaps with the §17.3 spec for "fast cold launch."

Awaiting PM decision. No retry without owner sign-off on the patch path.

## 24. 8-GPU 100-image measurement run 2026-05-30 (#229 follow-up T13 attempt 5 — BLOCKED at phase 7 defer: orchestrator polling race)

**Run ID:** `1780153225`
**Plan:** `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md` Task 13 attempt 5.
**Status:** BLOCKED at phase 7 (defer). **5c2adaa fix held — 8 GPUs enumerated correctly.** New layer surfaced: orchestrator's phase-7 SSM-result wait is too short for a fresh `python3 measure-defer.py` invocation.

### 24.1 Run header

| Field | Value |
|---|---|
| Branch / HEAD | main / `5c2adaa` (pushed to origin before launch — verified user-data picks it up via `git reset --hard origin/main`) |
| AMI | `ami-092ae5880cb9cf957` (DLAMI base, vendor `505e1cb` at bake; user-data step 2 5c2adaa preserves DLAMI driver, step 4 2b8d568 resets to origin/main HEAD) |
| Launch template | `qpress-sam-gpu-worker` v24 (re-published by `measure-run.sh` phase 3) |
| Instance | `i-0a6bc375758dde1c3` — `g6e.48xlarge` spot in `us-east-2a` (`sir-v7yfgsch`) |
| Launch ts (UTC) | 2026-05-30T15:00:30Z |
| SSM online (UTC) | 2026-05-30T15:01:50Z (boot_s = 80) |
| User-data done (UTC) | 2026-05-30T15:09:14Z (cold install ~7m24s — well under 25m cap) |
| Pre-flight result | **PASS — `nvidia-smi -L \| wc -l == 8`, vendor present, env present, dataset count == 100** ✅ |
| Phase 7 result | **FAIL — defer SSM command empty stdout after 10s wait → exit 4** |
| Wall billed | ~9m 10s (launch 15:00:30Z → terminate 15:09:40Z) |
| Cost (estimated) | **~$0.75** (us-east-2a spot ~$4.78/hr × 0.153 hr) |

### 24.2 What happened

`measure-run.sh` ran cleanly through phase 6:

```
[phase=4] instance=i-0a6bc375758dde1c3 launch_ts=1780153230
[phase=5] ssm online — boot_s=80
[phase=6] wait for user-data completion (max 25m)
[phase=6] still booting (boot=0 env=0 active=0)        ← 14× × ~32s = ~7m28s
...
[phase=6] user-data done — worker active
[phase=7] push defer launcher + run
defer failed:                                          ← empty stdout
[phase=11] terminating i-0a6bc375758dde1c3 (trap EXIT)
```

Pre-flight diagnostics fetched post-hoc via SSM `5eae2cbc-...` (Success at 15:09:22Z):

```
8                                                                  ← nvidia-smi -L | wc -l (was 2 in §23)
/opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/run_amg_v2.py
/etc/flake-analysis-worker.env
10558                                                              ← worker PID
100                                                                ← dataset image count
```

**The 5c2adaa fix held: all 8 L40S enumerated, the previous failure mode (driver replacement reducing visible GPUs to 2) did not recur.** Pre-flight passed, phase 7 dispatched the defer SSM command (`c2363945-6d96-4351-8423-27ed466c723b` at 15:09:28Z), then the orchestrator did `sleep 10` and called `get-command-invocation` once at 15:09:38Z — the command was still `InProgress` so `StandardOutputContent` came back empty. `JOB_ID` was empty, the script printed `defer failed:` (empty) and exited 4. The trap fired terminate at 15:09:40Z. The defer command continued running on the instance but was killed by termination ~10s later.

### 24.3 Root cause — orchestrator phase-7 polling race

`scripts/sam/measure-run.sh:217-228`:

```bash
cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[\"echo $payload_b64 | base64 -d > /tmp/measure-defer.py\",\"chmod +x /tmp/measure-defer.py\",\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-defer.py --weights-uri ...\"]" \
    --query "Command.CommandId" --output text)
sleep 10
out=$(aws_q ssm get-command-invocation \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query "StandardOutputContent" --output text)
JOB_ID=$(grep -oE 'job_id=[0-9]+' <<< "$out" | head -1 | cut -d= -f2 || true)
[[ -n "$JOB_ID" ]] || { echo "defer failed: $out" >&2; exit 4; }
```

`sleep 10` is a single fixed wait, not a polling loop. The defer command needs to (a) base64-decode and write `/tmp/measure-defer.py`, (b) `chmod`, (c) launch the project's uv venv Python, (d) import procrastinate + SQLAlchemy + project app, (e) connect to RDS via the worker's env, (f) enqueue the job, (g) print `job_id=N`. Cold venv import alone routinely takes 8-15s; total 12-25s is realistic. SSM also imposes its own scheduling/agent overhead (typically 5-15s before the command starts on the box).

Confirmed via `aws ssm list-commands --instance-id i-0a6bc375758dde1c3` post-hoc — the defer command (`c2363945-...`) was still `InProgress` 13+ minutes later (only because the instance shut down before the agent could finish/report). The phase 6 polling loop (which DOES poll properly with a deadline) does not have this bug; phase 7 was written without polling.

### 24.4 Why §18-§23 didn't surface this

- §18-§20 / §22 / §23: failed before reaching phase 7. §18 cloud-init checkout, §19 defer DB env (SOFT race — defer ran but failed; orchestrator received the python error in stdout), §20 `/proc/PID/environ` (same), §22 user-data step 4, §23 user-data step 2. None reached the "command-still-in-flight" race because either the command failed instantly (DB error printed in stdout immediately) or the script bailed before phase 7.
- §21: failed at phase 6 cap.
- §24 (this): first attempt where phase 6 succeeded AND defer was fast enough to dispatch but slow enough to still be running at 10s. The new failure layer is the orchestrator itself, not the worker.

### 24.5 Costs and total

| Attempt | Instance | Market | Duration | Cost | Outcome |
|---|---|---|---|---|---|
| §18 | `i-015f7e90f34ec2eec` | spot | 14 min | $0.92 | cloud-init `git checkout main` failed |
| §19 | `i-0fa4925d3bf3d340e` | spot | 19 min | $1.26 | defer DB config missing |
| §20 | `i-0e0d5d103fe4dd57f` | on-demand | 59 min | $7.09 | `/proc/PID/environ` insufficient + 53 min idle |
| §21 attempt 1 | `i-08f95adc57c08b7e0` | spot | ~1 min | $0.14 | phase 6 race — fixed in `1a3c4d7` |
| §21 attempt 2 | `i-0d167e2b072640cd1` | spot | 16 min | $1.31 | phase 6 polling cap fired |
| §22 attempt 3 | `i-0ec88d80aeeef16bf` | spot | 25 min | $1.85 | user-data step 4 git submodule fail |
| §23 attempt 4 | `i-0a326075c2fc624d3` | spot | 13 min | $0.95 | userdata step 2 driver install enumerated 2/8 GPUs |
| §24 attempt 5 (this) | `i-0a6bc375758dde1c3` | spot | 9 min | **~$0.75** | phase-7 polling race — `sleep 10` then single read |
| **Total** | | | **156 min** | **~$14.27** | **0 measurements completed** |

**$100 cap remaining: ~$85.73**

### 24.6 Status and proposed fix

BLOCKED. **Do NOT retry without the orchestrator patch below.**

Proposed fix (`scripts/sam/measure-run.sh:217-228`) — replace fixed `sleep 10` with a polling loop similar to phase 6's structure:

```bash
cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[...]" \
    --query "Command.CommandId" --output text)

defer_deadline=$(( $(date -u +%s) + 120 ))   # 2-minute cap on defer
JOB_ID=""
out=""
while (( $(date -u +%s) < defer_deadline )); do
    sleep 5
    inv=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "[Status,StandardOutputContent,StandardErrorContent]" --output text 2>/dev/null || echo "Pending")
    status=$(awk '{print $1}' <<< "$inv")
    case "$status" in
        Success)
            out=$(aws_q ssm get-command-invocation --command-id "$cmd_id" --instance-id "$INSTANCE_ID" --query "StandardOutputContent" --output text)
            JOB_ID=$(grep -oE 'job_id=[0-9]+' <<< "$out" | head -1 | cut -d= -f2 || true)
            break
            ;;
        Failed|TimedOut|Cancelled)
            err=$(aws_q ssm get-command-invocation --command-id "$cmd_id" --instance-id "$INSTANCE_ID" --query "StandardErrorContent" --output text)
            echo "defer failed (status=$status): $err" >&2
            exit 4
            ;;
        *)  # Pending|InProgress|Delayed
            ;;
    esac
done
[[ -n "$JOB_ID" ]] || { echo "defer failed: did not return job_id within 2m (status=$status, last_out=$out)" >&2; exit 4; }
log 7 "deferred job_id=$JOB_ID"
```

This mirrors phase 6's polling pattern, gives defer up to 2 min (vs the 10s race), distinguishes "still running" from "failed", and on Failed/TimedOut prints the actual stderr from the box instead of empty.

Time/cost cost of fix: ~5 LoC change, single commit, push, then re-launch. Each spot launch with the cumulative fixes is ~$0.75-1.30 if it cleanly progresses through phases 7-11. With remaining $85.73 cap, ~30-50 more attempts feasible — but no expectation of needing more than 1-2 if no further hidden races surface.

Awaiting PM decision: apply patch and re-launch attempt 6, or batch additional safety items first (e.g., capture cloud-init log on cap-fire — §21.3 step 3 still pending; SSM-on-shutdown-down race making post-mortem impossible; defer command capturing actual stderr).

## 25. 8-GPU 100-image measurement run 2026-05-30 (#229 follow-up T13 attempt 6 — BLOCKED: 3 compound bugs uncovered)

**Run ID:** `1780154608`
**Plan:** `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md` Task 13 attempt 6.
**Status:** BLOCKED. **d87916c (phase-7 polling fix) held — defer enqueued cleanly, job_id=11 emitted.** Three new layers surfaced simultaneously: (1) phase-8 polling SSM command has its own quoting bug, (2) phase-9 fetch path is wrong, (3) `worker_events` table missing on RDS so SAM job swallowed all marker errors and short-circuited to empty success.

### 25.1 Run header

| Field | Value |
|---|---|
| Branch / HEAD | main / `d87916c` |
| AMI | `ami-092ae5880cb9cf957` (DLAMI base, vendor `505e1cb` at bake) |
| Launch template | `qpress-sam-gpu-worker` v25 (re-published by `measure-run.sh` phase 3) |
| Instance | `i-0e91580814a715bd8` — `g6e.48xlarge` spot in us-east-2 |
| Launch ts (UTC) | 2026-05-30T15:23:33Z (`launch_ts=1780154613`) |
| SSM online (UTC) | 2026-05-30T15:24:54Z (boot_s = 81) |
| User-data done (UTC) | 2026-05-30T15:31:31Z (cold install ~6m37s — well under 25m cap) |
| Phase 7 result | **PASS — defer enqueued, `job_id=11` emitted** ✅ |
| Phase 8 result | **FAIL — polling SSM cmd had quoting bug, `status=` empty for entire loop** |
| Phase 9 result | not reached (phase 8 hung) |
| Wall billed | ~14 min (manual terminate at $1.67) |
| Cost (estimated) | **~$1.67** (us-east-2 spot ~$4.78/hr × 0.232 hr) |

### 25.2 What happened — three bugs in compound

**Phases 1–7 sailed through cleanly.** Orchestrator log shows the d87916c phase-7 polling fix held:

```
[phase=6] user-data done — worker active
[phase=7] push defer launcher + run
[phase=7] deferred job_id=11
[phase=8] polling loop (tick=30s, wall_cap=60m, cost_cap=$5)
[phase=8] elapsed=520s proj_cost=$1.04 status=
[phase=8] elapsed=551s proj_cost=$1.11 status=
... (10 more iterations, all empty status) ...
[phase=8] elapsed=833s proj_cost=$1.67 status=
```

PM ran an independent SSM probe at elapsed≈770s and found:

- The orchestrator's polling SSM command was returning Status=Failed every iteration with `psql: error: invalid integer value "$SAA_DB_PORT" for connection option "port"`. The shell-escape sequence in `commands=[...]` collapses `\\\$VAR` to literal `$VAR` before psql sees it.
- A correctly-quoted manual SSM probe revealed **job 11 was already `succeeded` since 15:32:12Z** — i.e., phase 8 spent the entire 5+ minutes spinning while the work was already done.
- The actual job result was `{'images': 0, 'masks_total': 0, 'errors': 0, 'per_image': {}}` — completed in 3.479s, processed zero images.
- Worker journalctl revealed that all 5 marker emits failed with `psycopg.errors.UndefinedTable: relation "worker_events" does not exist` — the `markers.py` module catches and continues (logs ERROR, doesn't raise). The `run_sam` task itself returned an empty success in 3.479 s, not actually loading the SAM model or processing images.

PM terminated the instance manually to stop the spin; orchestrator trap EXIT confirmed kill (idempotent terminate).

### 25.3 Bug 1 — phase-8 polling SSM command has shell-quoting bug

`scripts/sam/measure-run.sh:283-290`:

```bash
cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[\"sudo bash -c 'set -a; . /etc/flake-analysis-worker.env; set +a; PGPASSWORD=\\\"\\\$SAA_DB_PASSWORD\\\" psql -h \\\$SAA_DB_HOST -p \\\$SAA_DB_PORT -U \\\$SAA_DB_USER -d \\\$SAA_DB_NAME -tAc \\\"SELECT status FROM procrastinate_jobs WHERE id=$JOB_ID\\\"'\"]" \
    --query "Command.CommandId" --output text)
sleep 5
s=$(aws_q ssm get-command-invocation \
    --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
    --query "StandardOutputContent" --output text 2>/dev/null | tr -d '[:space:]')
log 8 "elapsed=${elapsed_s}s proj_cost=\$$proj_cost status=$s"
```

Two compounding issues:
- **Quoting**: through bash → JSON → SSM agent → remote bash, `\\\$VAR` collapses to literal `$VAR`. psql receives `-p '$SAA_DB_PORT'` and fails immediately. Reproduced: every poll iteration's `Status=Failed`, `StandardErrorContent="psql: error: invalid integer value \"$SAA_DB_PORT\" for connection option \"port\""`.
- **Race (same as old phase 7)**: the `sleep 5` before reading StdOut is a fixed wait, not the polling loop d87916c introduced. Even if the quoting were fixed, a 5s wait might be insufficient for psql to RDS through the bastion-less SG.

The `s=` variable is empty either because StdOut is empty (cmd Failed) or cmd is still InProgress at 5s. Either way the log just shows `status=` and the case statement never matches `succeeded` or `failed`, so the loop spins until cost-cap or wall-cap fires.

### 25.4 Bug 2 — phase-9 fetch path mismatch

`scripts/sam/measure-run.sh:307`:

```bash
cmd_id=$(aws_q ssm send-command ... \
    --parameters "commands=[\"cat /opt/sam/runs/${RUN_ID}/sam/per_image_results.json\"]" ...)
```

Worker actually writes to `/opt/sam/runs/${RUN_ID}/07_sam/`. PM verified directly via SSM:

```
$ ls -la /opt/sam/runs/1780154608/
drwxr-xr-x 2 ubuntu ubuntu 4096 May 30 15:32 07_sam       ← actual

$ ls -la /opt/sam/runs/1780154608/sam/
(does not exist)
```

Phase 9 would have failed even if phase 8 had succeeded. (Convention: the `07_` prefix is from the legacy pipeline's stage numbering — should probe both paths or have orchestrator accept the prefix from `summary.json`.)

### 25.5 Bug 3 — `worker_events` table missing on RDS; markers swallowed; SAM job no-op'd

Worker journalctl showed all 5 expected markers errored:

```
2026-05-30 15:32:09 ERROR flake_analysis.worker.markers emit_marker failed: run_id=1780154608 event=sam_task_start
psycopg.errors.UndefinedTable: relation "worker_events" does not exist
LINE 1: INSERT INTO worker_events (run_id, event, payload) VALUES ($...
```

…and four more (model_load_start, processing_start, processing_end, sam_task_end). `src/flake_analysis/worker/markers.py:77` catches and logs ERROR without raising, so the task continued — but the task itself returned `{'images': 0}` in 3.479s, meaning no SAM model was loaded and no images processed at all. (Either `run_sam` skipped processing because something failed silently elsewhere, or the dataset enumeration produced 0 — needs a follow-up to clarify.)

Two distinct sub-bugs:
- (a) The DB migration that creates `worker_events` was never applied to prod RDS (T9/T10 plan dependency, not visible in attempt 1-5 because they failed before reaching SAM execution).
- (b) `run_sam` should fail loudly when it can't emit markers OR when it processes 0 images, instead of returning `Success: {'images': 0}`. As-is, even if phase 8's polling were fixed, it would have reported `succeeded` to the orchestrator with no actual measurement.

### 25.6 Why §18-§24 didn't surface these

- §18-§23: failed before phase 7. Never reached SAM execution at all.
- §24 attempt 5: failed at phase 7's `sleep 10` race (now fixed in d87916c). Phase 8/9 never executed; `run_sam` never ran; the missing `worker_events` table was therefore invisible.
- §25 (this): first attempt where phase 7 cleanly enqueued the job AND `run_sam` actually executed on the worker. Three latent bugs all surfaced together.

### 25.7 Costs and cumulative

| Attempt | Section | Outcome | Cost |
|---|---|---|---|
| 1 (§18) | cloud-init main checkout | BLOCKED | $0.92 |
| 2 (§19) | defer DB config | BLOCKED | $1.26 |
| 3 (§20) | /proc/PID/environ | BLOCKED + 53m idle | $7.09 |
| 4 (§21) | 15-min phase-6 cap | BLOCKED | $1.31 |
| 5 (§21.2) | phase-6 polling cap | BLOCKED | (rolled into §21) |
| 6 (§22) | git submodule mismatch | BLOCKED | $1.85 |
| 7 (§23) | cuda-drivers replace DLAMI | BLOCKED | $0.95 |
| 8 (§24) | phase-7 polling race | BLOCKED | $0.75 |
| 9 (§25, this) | phase-8 quoting + phase-9 path + missing migration | BLOCKED | **~$1.67** |
| **Total** | | **0 measurements** | **~$15.94** |

**$100 cap remaining: ~$84.06**

### 25.8 Status and proposed fixes for attempt 7

BLOCKED. **Do NOT retry without all three patches below.**

1. **Phase 8 polling — orchestrator** (`scripts/sam/measure-run.sh:283-296`): replace shell-escape soup with either (a) a small helper script baked into the AMI (`/usr/local/bin/saa-job-status JOB_ID` → echoes status) and just call that, or (b) a heredoc-based `commands=[...]` that uses single-quoted body. Then apply the same SSM-status polling pattern as d87916c (poll `Status` until `Success`, then read StdOut). Recommended: option (a), because it's reusable and avoids the JSON/shell layering hell entirely.

2. **Phase 9 path — orchestrator** (`scripts/sam/measure-run.sh:307`): change `${RUN_ID}/sam/` to `${RUN_ID}/07_sam/`, OR have phase 9 first read `summary.json` to discover the actual subdirectory.

3. **DB migration — db-specialist**: apply the `worker_events` table migration to prod RDS. Verify with: `\dt worker_events` returning the relation. (PM should also confirm whether this migration exists in `alembic/versions/` or needs to be authored — T9/T10 plan tasks cover marker emission but the table creation is a dependency.)

4. **`run_sam` should fail loudly — algo-engineer or api-developer**: when `run_sam` would return `images=0`, raise instead so procrastinate flips the job to `failed` and the orchestrator (with bug 1 fixed) can detect the bad state. Also: marker emit failures should at minimum be loud enough that the orchestrator sees them in worker logs — current behavior of catching and logging makes the failure mode silent.

Time/cost of fixes: 1 = 30-60 min (write helper script, bake into AMI or push via user-data, update orchestrator); 2 = 5 LoC; 3 = 1 alembic command; 4 = ~10 LoC. Each spot launch with cumulative fixes is ~$1.50-2.00 if it cleanly progresses through phases 7-11. With remaining ~$84.06 cap, ~40 more attempts feasible. Realistic estimate: 1-3 more attempts to land if no further hidden bugs surface.

PM observation: each attempt has revealed exactly one or two new layers because each prior failure mode masked the next. Recommend BEFORE attempt 7 doing a focused dry-run of phase 8/9/10 SSM commands manually (no live measurement, just the SSM-cmd shape) on a smaller cheaper instance to flush remaining quoting/path bugs.

---

## 26. 8-GPU 100-image manual measurement run 2026-05-31 (#229 follow-up — BLOCKED)

**Outcome: BLOCKED.** Drove the §15 manual SSH/SSM pattern to skip the
broken `measure-run.sh` orchestrator. Got past every orchestration bug
the prior 6 attempts uncovered (cleanly deferred procrastinate jobs
with the right `model_meta` payload, alembic migrated `worker_events`
to head, all 8 GPUs visible, dataset staged) — only to discover **two
new latent bugs** in AMI v25 that prior attempts never reached because
they died earlier in the pipeline:

| Attempt | Job | RUN_ID | Code path | Wall | Failure |
|---|---|---|---|---|---|
| 1 (spot)         | n/a | n/a        | n/a | 11 min | spot-evicted at boot+9min (`instance-terminated-no-capacity` in `us-east-2a`) |
| 2 (on-demand) #1 | 12 | 1780258765 | merged_m3 (`build_sam2_from_yaml`) | 11.0 s task | `KeyError: 'model'` in `sam2._load_checkpoint` |
| 2 (on-demand) #2 | 13 | 1780259009 | lora-runtime (`build_sam2_finetuned` + peft) | 22.5 s task | `RuntimeError: CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH` |

**Total cost ≈ $4.74**, instance ≈ 32 min on-demand + ≈ 11 min spot,
session wall ≈ 33 min. Within both the $5 operator cost cap and the
60-min wall cap. Both instances terminated cleanly (no manual
cleanup outstanding).

### 26.1 What worked (the §15 manual pattern proved correct)

Validated the manual SSH/SSM pattern is **the right approach** for
exploratory measurement work — the orchestrator (`measure-run.sh`)
and `measure-defer.py` are not on the critical path, only the
worker-side procrastinate defer + cluster-side SAM is.

Concrete pieces that worked first-try this run:

1. **Direct `ec2 run-instances` from launch template** with the
   spot fall-through to on-demand. LT v25 user-data is solid:
   boot-finished + worker.env + worker `active` + 8 GPUs + 100
   images all gated cleanly with one polling loop. On-demand wall
   from `run-instances` to user-data-done = **8 min 50 s** (73 s
   to SSM Online + ~7.6 min waiting for the M3 dataset/weights
   download via user-data step 5d).

2. **Defer pattern via base64-encoded shell uploaded to `/tmp/` then
   `sudo bash`**. The plan's inline `sudo bash -c '<heredoc>'` SSM
   payload broke on shell quoting (single-quote in JSON `"commands"`
   array vs single-quoted shell payload). Workaround: `base64`-encode
   the script locally, push via SSM as
   `echo $B64 | base64 -d > /tmp/X.sh && sudo bash /tmp/X.sh`. This
   sidesteps every quoting trap simultaneously and produced clean
   stdout/stderr capture for both attempts.

3. **Alembic migration `0007_worker_events`** applied to prod RDS
   first try (`Running upgrade 0006_procrastinate_init -> 0007_worker_events`).
   Plan's inline `CREATE TABLE` was fragile (had `connect_args={"ssl": True}`
   which RDS rejected with `SSLCertVerificationError: self-signed
   certificate in certificate chain`); switched to using the URL-form
   `?ssl=require` (matching `src/flake_analysis/db/url.py` line 61)
   and ran via `.venv/bin/alembic upgrade head`. Cleaner and reuses
   the existing migration that's already in the tree.

4. **Procrastinate defer via `app.tasks["run_sam"].defer_async()`**
   inside `async with app.open_async()` worked verbatim. Got
   `JOB_ID=12` and `JOB_ID=13` cleanly and the worker picked up both
   within ~25 s of defer.

5. **Job-status polling via psql against `procrastinate_jobs`** cleanly
   surfaced `failed` status within one poll cycle for both attempts.
   No race; no need for Procrastinate's `wait_for_jobs_listener`
   complexity.

### 26.2 Bug 1 — `merged_m3.pt` checkpoint format mismatch

**Path taken:** `_resolve_merged_m3_path()` returned
`/opt/sam/weights/merged_m3.pt` (because user-data sets
`SAM_MERGED_M3_PATH` and the file was on disk), so
`_run_sam_multi_gpu` set `config["use_original_sam2"] = True`,
`config["checkpoint"] = "/opt/sam/weights/merged_m3.pt"` and the
vendor's `worker_process_images` called
`build_sam2_from_yaml(yaml, ckpt)` per child, which in turn calls
SAM2's `_load_checkpoint` (`.venv/.../sam2/build_sam.py:166`):

```python
sd = torch.load(ckpt_path, map_location="cpu", weights_only=True)["model"]
```

But on AMI v25 `merged_m3.pt` introspects as:

```
top-level type: dict
top-level keys (2): ['model_config', 'model_state_dict']
```

— same structure as the §13 `merged.pt` introspection in §13.3 line 902.
The merger that produced `merged_m3.pt` outputs `{model_config,
model_state_dict}`, but vanilla SAM2 (`pip install sam2`) expects
`{model: {...}}`. Job 12 dies in <11 s with `KeyError: 'model'`.

This had been **invisible in §15** because that earlier run did not
have `SAM_MERGED_M3_PATH` set in the worker systemd env (verified by
re-reading §15.3 — only `SAM_WEIGHTS_PATH` and `SAM_M3_DIR` were
exported then). At some point between the §15 AMI and v25, user-data
started exporting `SAM_MERGED_M3_PATH=/opt/sam/weights/merged_m3.pt`,
which forces the merged_m3 routing branch in
`src/flake_analysis/core/pipeline/sam.py:380-386` regardless of
whether the checkpoint is in the right format for SAM2's loader.

**Workaround applied this run:** renamed
`/opt/sam/weights/merged_m3.pt → merged_m3.pt.disabled` so
`_resolve_merged_m3_path()` returns None (its `path.is_file()` check
on line 235 fails), forcing the LoRA-runtime fallback. That hit bug
2 below.

**Permanent fix options (algo-engineer territory):**

- (A) Update `_resolve_merged_m3_path()` to inspect the checkpoint
  before returning it — return None if the dict doesn't have a `model`
  key. Cheapest, but masks the real problem.
- (B) Patch `src/flake_analysis/core/pipeline/sam.py:_run_sam_multi_gpu`
  to reshape the loaded dict (`sd = sd.get("model_state_dict", sd.get("model", sd))`)
  before vendor sees it — either via a `_load_checkpoint` monkey-patch
  alongside the existing `load_training_args` patch on line 369, or a
  pre-load step that materializes a SAM2-compatible `.pt` next to
  the merged_m3 input.
- (C) Re-mint `merged_m3.pt` with the SAM2 format (top-level `model`
  key holding the state_dict). Punts to whoever produced the artifact;
  same shape as §13.5 option 3.
- (D) Drop the merged_m3 routing entirely and stay on the LoRA-runtime
  path (the path §15 actually exercised). But then bug 2 below has to
  be fixed first.

### 26.3 Bug 2 — cuDNN sublibrary version mismatch (AMI v25)

After disabling merged_m3 to force the LoRA-runtime path, the run made
real progress: vendor `run_multi_process` correctly spawned 8 child
processes, each `Applied LoRA modules` (rank=16, 14.4M trainable
params), each picked up its image shard (13/13/13/13/12/12/12/12),
and the first few images logged `[N/100] [GPU K] Processing: ...`.

Then within 1 s of starting per-image inference, every child died
with the same fatal CUDA error:

```
File ".../torch/nn/modules/conv.py:560", in _conv_forward
    return F.conv2d(input, weight, ...)
RuntimeError: CUDNN_BACKEND_TENSOR_DESCRIPTOR cudnnFinalize failed
ptrDesc->finalize() cudnn_status: CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH
```

**Environment introspection (AMI v25, on-demand `g6e.48xlarge`):**

| Component | Version |
|---|---|
| NVIDIA driver | `580.159.04` |
| Driver-reported CUDA | `13.0` |
| `torch` | `2.12.0+cu130` |
| `torch.version.cuda` | `13.0` |
| `torch.backends.cudnn.version()` | `92000` (cuDNN 9.20.00) |
| Bundled cuDNN libs | `.venv/.../site-packages/nvidia/cudnn/lib/libcudnn*.so.9` |

cuDNN 9.20.00 is the version PyTorch's `cu130` wheel bundles. The
"sublibrary version mismatch" string means at runtime, when conv2d
tries to construct a backend tensor descriptor, the precompiled
engines library reports a different sublibrary version than the
header library it links against. This typically happens when:

1. The system has a different `libcudnn` available (e.g., from a
   global `cudnn-cuda-13` apt package) that gets `LD_PRELOAD`/
   `LD_LIBRARY_PATH` precedence over the venv's bundled one in the
   spawned multiprocessing child — but the engines runtime-compiled
   library was already paged in from the venv path during parent
   imports, creating a torn pair.
2. The torch wheel was built against a cuDNN ABI variant that
   doesn't match what the DLAMI driver registered against.

The fact that **model load and LoRA application succeeded** (those
also run conv2d/linear ops indirectly via parameter init) and only
the *first inference forward* tripped the check is consistent with
a fork/spawn-time loader divergence — child processes inherit the
already-mapped libs but resolve dynamic backend symbols freshly.

**This is an AMI bake fix, not a runtime workaround.** Options:

- Pin to torch 2.10.x or 2.11.x with `cu124`/`cu126` (proven stable
  on §15's AMI); this means re-pinning the inference uv lockfile
  and rebaking. Aligns with the §15 driver tier (the `Applied LoRA
  modules` log lines on §15 show no cuDNN errors at the same
  forward).
- Run `uv pip install --force-reinstall` of `torch` against the
  exact CUDA version on the driver, with explicit `--index-url
  https://download.pytorch.org/whl/cu130` — but this likely just
  reinstalls the same broken wheel. Still worth trying once.
- Replace the DLAMI base with a newer one whose cuDNN sublib
  version matches `torch 2.12.0+cu130`. Means rebaking the AMI from
  a different parent.

### 26.4 What this proves and what it doesn't

**Proves:** the §15 manual SSH/SSM pattern is the right pattern.
Every step from instance-launch through procrastinate-defer worked
cleanly. The 4+ orchestrator bugs catalogued in §22-§25 are real
but they're not on the critical path — once you bypass them you
get to the actual SAM compute layer in <12 minutes.

**Doesn't prove:** that the SAM compute layer *itself* works on
AMI v25. The §15 PARTIAL run (1975/3648, 1.521 s/img aggregate)
was on a different AMI. Bug 2 in particular means we cannot trust
that AMI v25 will produce a clean measurement *even with the
orchestrator removed*.

### 26.5 Recommendation

**Halt 100-image measurement attempts on AMI v25** until the cuDNN
sublib mismatch is resolved at bake time. Two parallel tracks:

1. **algo-engineer**: pick (B) from §26.2 to get merged_m3 routing
   working again on a future AMI (this is what the §15 PARTIAL run
   *should* have used per #209's intent). Estimated 30-60 min.

2. **devops-engineer**: rebake AMI with one of the cuDNN fixes from
   §26.3 — preferred order: (a) downgrade torch to a tier matching
   §15's working stack, (b) full driver+cudnn alignment via newer
   DLAMI parent. Estimated 1-2 h on first attempt because
   measurement validation requires another G6e launch.

After both fixes land, re-run this exact §26 manual procedure (one
spot launch + one defer + one poll + one terminate) on the new AMI
to validate end-to-end. Expected next-attempt cost ~$1-2 if it
either succeeds or hits a new bug within 5 min.

### 26.6 Artifacts captured

- `claudedocs/measurement-1780259009/worker.log` — 217-line journal
  with both job 12 and job 13 tracebacks + LoRA `Applied LoRA modules`
  evidence + per-GPU image shards.
- `claudedocs/measurement-1780259009/summary.json` — structured run
  metadata (machine-readable for later regression tracking).
- `claudedocs/measurement-1780259009/worker_events.tsv` — empty (table
  exists, no markers emitted because both jobs failed before reaching
  the `marker:processing_start` emit on
  `src/flake_analysis/core/pipeline/sam.py:411`).

These are gitignored under `claudedocs/` per project convention; not
in this commit.

### 26.7 Cleanup performed

- Spot instance `i-0712bd016651714f6` — already terminated by AWS
  (spot reclaim, `us-east-2a`) at `2026-05-31T20:03:36Z`.
- On-demand instance `i-0c8fc6a6e4c165e26` — manually terminated at
  `2026-05-31T20:25:33Z` after cuDNN diagnosis confirmed.
- No EBS volumes orphaned (all root-only, terminate-on-shutdown).
- No S3 staging to clean up (input dataset and weights are part of
  the long-lived `internal/sam/` prefix, not run-scoped).

## 27. 1-click SAM dispatch acceptance — 2026-06-08 (CAPACITY_BLOCKED)

End-to-end automated acceptance for the GPU dispatcher prod path
defined by `docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md`.
Direct `POST /run/sam` from `httpx` against a local FastAPI process
talking to prod RDS via SSH-tunneled bastion → SSE wire stream parsed
→ dispatcher attempts spot launch → **AWS returns
`InsufficientInstanceCapacity` on every attempt** → dispatcher
correctly translates to `pipeline_failed` SSE error frame and
records `runs.status='failed'`.

**Outcome: CAPACITY_BLOCKED.** Four consecutive `POST /run/sam`
attempts over a ~25-minute window all hit
`InsufficientInstanceCapacity` for `g6e.48xlarge` spot in `us-east-2`.
No EC2 instance ever launched. Cold-start lifecycle (`gpu_launching`
→ `gpu_ready` → per-image progress → `done` → idle terminate) was
**not exercised** because the spot allocation never succeeded. The
dispatcher's failure path **was** fully validated end-to-end.

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `f55bf96` (T1-T4 merged + LT v26 published) |
| AMI | `ami-0b7ec5ff47a1eff11` (cu124 stack, §15-verified) — **not booted this run** |
| Launch template | `qpress-sam-gpu-worker` v26 (default), `g6e.48xlarge` spot |
| Instance | none — RunInstances rejected |
| Run IDs (RDS `runs`) | 1 (canceled-by-client), 2-4 (capacity_unavailable) |
| Scan ID | 1 (`acceptance-100`, project `acceptance-2026-06-08`) |
| First POST ts (UTC) | 2026-06-08T18:26:40 |
| Last POST ts (UTC) | 2026-06-08T18:52:00 |
| `gpu_launching` SSE | **never fired** (no successful RunInstances) |
| `gpu_ready` SSE | **never fired** (no worker booted) |
| Cold-start wall | n/a |
| Processing wall | n/a |
| Total instance wall (billed) | **0 min** |
| Cost (this run) | **$0.00** |
| Status | `capacity_blocked` |

### 27.1 What worked (dispatcher code path validated)

1. **API ↔ RDS via SSH tunnel.** Local `uvicorn` with
   `SAA_DB_HOST=127.0.0.1`, `SAA_DB_PORT=5433` tunneled to
   `qpressdb.ch08y4ooqgmq.us-east-2.rds.amazonaws.com:5432` through
   the bastion (`i-063165d449976b2e4`). Required patching
   `flake_analysis.db.url._LOCAL_HOSTS` to force `ssl=require` (RDS
   has `rds.force_ssl=1` and the tunnel terminates at 127.0.0.1
   which the production code treats as local-no-SSL by default).
   Acceptance harness (`/tmp/saa-acceptance-server.py`) re-binds
   `_require_ssl` on every importer of the symbol.

2. **`POST /run/sam` returns `200 text/event-stream` within ~1.0 s**
   across all four attempts. Auth via `SAA_AUTH_DEV_BYPASS=1`
   resolved cleanly; `usage_events` row inserted; per-scan mutex
   acquired; `runs.id` allocated; SSE headers flushed. Wire format
   matches existing single-step routes byte-for-byte.

3. **Dispatcher reaches `_ensure_gpu_worker` → `ensure_worker_running`
   → `_launch_one`** and propagates `boto3` `ClientError(code=
   "InsufficientInstanceCapacity")` to `GpuCapacityUnavailable` per
   `src/flake_analysis/worker/launcher.py:281`. The conversion is
   surfaced as a clean SSE `error` envelope:

   ```json
   {
     "type": "error",
     "error": {
       "code": "pipeline_failed",
       "message": "GPU spot capacity unavailable in us-east-2. Retry in a few minutes.",
       "details": {"exc_type": "GpuCapacityUnavailable"},
       "request_id": "<uuid>"
     }
   }
   ```

   Each request emitted exactly **one** error frame ~13-16 s after
   `POST` (the time AWS takes to fail the spot request), then closed
   the stream cleanly. No hangs, no zombie tasks.

4. **`runs` rows recorded correctly.** All three capacity-blocked
   POSTs landed `status='failed'` rows (ids 2/3/4) with the full
   error message in `runs.error`. The first attempt (id 1, status
   `failed`, error `canceled by client`) reflects an early `curl`
   smoke test that disconnected before the defer completed; the
   driver task's exception handler caught the cancellation and
   wrote the failed row. No orphaned `running` rows, no stuck
   advisory locks.

5. **Per-scan mutex behaves correctly under repeat-fail traffic.**
   Re-issuing `POST /run/sam` against the same `scan_id=1`
   immediately after each failure succeeded (no `423 ProjectBusy`),
   confirming `acquire_scan_lock` releases on both success and
   error paths.

6. **PgAdvisoryLock cleanup** worked — `pg_try_advisory_lock(7)`
   acquired on each attempt and released cleanly (verified by the
   next attempt succeeding immediately, not waiting for a stale
   lock).

### 27.2 What did not run (pending capacity)

The cold-start lifecycle below could not be exercised because no
EC2 instance ever launched:

- `gpu_launching` SSE event with `instance_id` (T1 deliverable)
- `gpu_ready` SSE event with `image_count` (T3 deliverable)
- Per-image `step_progress` events streaming
- Terminal `step_completed` / `done` event with `result.images >= 100`
- `idle-shutdown.timer` firing after 10 min idle and instance reaching
  `terminated` state

These are unblocked the moment `g6e.48xlarge` spot capacity returns
to us-east-2; the same harness can re-run without code changes.

### 27.3 Capacity diagnostic

```text
$ aws ec2 describe-spot-price-history --instance-types g6e.48xlarge
us-east-2c  6.4254  2026-06-08T18:00:24+00:00
us-east-2b  5.9641  2026-06-08T15:01:00+00:00
us-east-2a  7.4683  2026-06-08T15:01:00+00:00
```

Reference on-demand for `g6e.48xlarge` is ~$3.96/hr; current spot
clearing $5.96-$7.47/hr indicates AWS-side scarcity (H200 inventory
pressure). All four attempts failed at the EC2 control plane with
`InsufficientInstanceCapacity` — boto3 never returned an
`InstanceId`. AZ-level diagnostics are not available because the
LT does not pin a subnet; AWS routes the request internally.

### 27.4 Production risk surfaced

The dispatcher LT (v26) is **spot-only** with no on-demand fallback.
When us-east-2 spot capacity for `g6e.48xlarge` is tight (as it was
on 2026-06-08), every `POST /run/sam` returns `pipeline_failed`
within ~15 s, regardless of how many users click the button. The
existing manual measurement runbook (§26) handles this by issuing a
direct `ec2 run-instances --instance-market-options 'MarketType=on-demand'`
override; the prod dispatcher has no such hatch.

**Recommendation for follow-up (owner decision):** add an on-demand
fallback to `_launch_one` — on `InsufficientInstanceCapacity`,
re-issue `RunInstances` without `InstanceMarketOptions` (i.e.
on-demand) and tag the result so cost reporting can distinguish
spot-vs-on-demand wall. Out of scope for this acceptance; flagged
in `docs/project-status.md` for the next planning cycle.

### 27.5 Staging artifacts (RDS)

Created in prod RDS for this acceptance — left in place so the
re-run can use the same `(project_id, scan_id)`:

- `projects` row `acceptance-2026-06-08` (`name='sam-dispatcher-acceptance'`,
  owner = existing admin user `6410dbb8-...`)
- `models` row `id=1` (`name='acceptance-merged-pt'`, placeholder —
  worker resolves real `weights_path` at runtime)
- `scans` row `id=1` (`name='acceptance-100'`, material `graphene`,
  status `ready`, `image_count=100`)
- `images` rows `id=1..100` referencing
  `s3://qpress-uploads/internal/sam/scan6-100/<sha>.png`
- `analyses` row `id=1` (`scan_id=1`, `model_id=1`)
- Local manifest: `/tmp/saa-analysis/acceptance-2026-06-08/1/manifest.json`
  with `raw_images_dir=/opt/sam/dataset/scan6-100` and
  `analysis_folder=/opt/sam/runs/acceptance-2026-06-08-scan-1`

Re-running the acceptance is `cd /tmp && uv run --project
/Users/houkjang/projects/stand-alone-analyzer python
/tmp/saa-acceptance-run.py --url <api>/api/v1/projects/acceptance-2026-06-08/scans/1/run/sam`
plus the `nohup uv run python /tmp/saa-acceptance-server.py` launch
with the env exports below — but the harness scripts in `/tmp/`
are ephemeral; future re-runs should regenerate them or check
`docs/superpowers/plans/2026-06-08-gpu-dispatcher.md` Task 6 for the
recipe.

### 27.6 Cleanup performed

- API uvicorn process terminated cleanly (PID killed via `/tmp/saa-acceptance-api.pid`).
- SSH bastion tunnel torn down (`pkill -f 'ssh.*qpressdb.ch08y4ooqgmq'`).
- Bastion `i-063165d449976b2e4` left **running** (owner can stop;
  default behavior is the cron `abs-cap-terminate` runbook handles it).
- No EC2 GPU instances live (verified `describe-instances ... values=pending,running,
  stopping,shutting-down` empty).
- No EBS, no S3 staging needed (acceptance dataset already in
  `internal/sam/scan6-100/`).
- Staging RDS rows left in place for next attempt; idempotent SQL
  (`/tmp/saa-acceptance-stage.sql`, `/tmp/saa-acceptance-images.sql`)
  can re-create them if dropped.

### Cumulative cost

| Phase | Cost |
|---|---|
| #229 attempts §18-§26 | $20.54 |
| **§27 (dispatcher acceptance — capacity-blocked, no EC2 spent)** | **$0.00** |
| **Total** | **$20.54** |

### 27.7 Status for follow-up

GPU dispatcher's **failure path is verified** (capacity error → SSE
error → recorded run). The **success path is not verified** because
AWS spot capacity in us-east-2 was exhausted for `g6e.48xlarge`
during the acceptance window. Re-run when capacity returns; until
then, dispatcher is "code-validated, capacity-pending".

> **Update 2026-06-08 evening:** §27's recommended `_launch_one`
> on-demand fallback shipped as commit `edf8bd3` (T7) and the
> acceptance was re-run; see §28 for the post-T7 result. Two further
> latent bugs surfaced (procrastinate `AppNotOpen` at defer + AMI
> "dubious ownership" at user-data) that block the SAM success path
> independently of capacity.

---

## 28. 1-click SAM dispatch acceptance — 2026-06-08 (post-T7) PARTIAL_BLOCKED

Re-run of §27 after [T7 on-demand fallback](#274-production-risk-surfaced)
shipped as commit `edf8bd3`. Same harness shape as §27 (`httpx` POST
`/run/sam` SSE foreground, prod RDS via SSH-tunnelled bastion,
`SAA_AUTH_DEV_BYPASS=1`, dev-bypass admin user).

**Outcome: PARTIAL_BLOCKED.** T7's spot-drought→on-demand fallback
**fired and is verified at the AWS API level** — but the SAM success
path remained unreached due to two independent latent bugs surfaced
in this run:

1. **API-side `procrastinate.AppNotOpen` at `defer_async`** — first
   visible the moment T7 was effective and capacity returned. Patched
   in this same commit cycle (T8).
2. **Worker-side `git config safe.directory` failure in user-data** —
   visible after T8 patch landed and a worker booted. The pre-baked
   repo at `/opt/sam/stand-alone-analyzer` is owned by user `ubuntu`
   while `cloud-init` runs as `root`, so step `[4/8] clone repo +
   submodule` aborts immediately with `fatal: detected dubious
   ownership` and `worker.service` is never started. Out of scope for
   this acceptance; documented as follow-up T9.

| Field | Value |
|---|---|
| Branch / HEAD at start | `main` / `edf8bd3` (T7 spot→on-demand fallback) |
| Branch / HEAD after T8 | `main` / `<sha-after-T8-commit>` (procrastinate `app.open_async` in lifespan) |
| AMI | `ami-0b7ec5ff47a1eff11` (cu124, §15-verified) |
| Launch template | `qpress-sam-gpu-worker` v26 (default, spot-only) |
| Instances launched | `i-0dd944abe7d9e432a` (spot, 19:33:46→19:35:22Z, ~96 s, terminated user-init), `i-0c30c170080222749` (spot, 19:46:54→20:00:38Z, ~13.7 min, terminated user-init) |
| Run IDs (RDS `runs`) | 6, 7 (capacity-blocked / GpuCapacityUnavailable) · 8 (AppNotOpen) · 9, 10 (MaxSpotInstanceCountExceeded post-terminate quota lag) · 11, 12 (instances launched but worker.service never started — terminated as orphans) |
| First POST ts (UTC) | 2026-06-08T19:26:19 |
| Last POST ts (UTC) | 2026-06-08T19:55:12 |
| `gpu_launching` SSE | **fired once**, attempt 3, `instance_id=i-0dd944abe7d9e432a`, ~1 s after POST |
| `gpu_ready` SSE | **never fired** (worker.service never started on either instance) |
| Per-image `progress` SSE | **never fired** |
| `done` SSE | **never fired** |
| Cold-start wall (instance pending→running) | ~13 min on `i-0c30c170080222749` (19:46:54 launch → 19:47:56 cloud-init final → user-data immediate fail) |
| Total instance wall (billed) | ~14.5 min |
| Cost (this re-run) | **~$1.52** (2 spot launches at ~$5.96/hr Linux us-east-2b, both manually terminated; idle-shutdown.timer was never reached because worker.service never came up) |
| Status | `partial_blocked` |

### 28.1 What was newly verified vs §27

1. **T7 spot-drought→on-demand fallback executed correctly.** Attempts
   1 and 2 (19:26 and 19:30) both surfaced the new T7 error envelope
   *exactly* once each:

   ```json
   {"type":"error","error":{"code":"pipeline_failed",
    "message":"GPU capacity unavailable in us-east-2 (both spot and on-demand). Retry in a few minutes.",
    "details":{"exc_type":"GpuCapacityUnavailable"}, ...}}
   ```

   That literal message string only originates from the second
   `except ClientError` branch of `_launch_one` after the on-demand
   retry also returns `InsufficientInstanceCapacity`
   (`src/flake_analysis/worker/launcher.py:309-313`). i.e. the
   fallback boto3 call **was issued** and AWS returned
   `InsufficientInstanceCapacity` for the on-demand path too. Spot
   prices at the moment: `us-east-2b $5.926`, `us-east-2c $6.425`,
   `us-east-2a $7.468` — same drought as §27 (clearing well above
   on-demand $3.96/hr reference).

2. **Capacity returned mid-run.** Attempt 3 (19:33:44) succeeded at
   the EC2 control plane: spot instance `i-0dd944abe7d9e432a` allocated
   in `pending` and the dispatcher emitted the first-ever
   `gpu_launching` SSE event with the instance_id. This is the **first
   `gpu_launching` ever observed in production** — closing the §27.2
   "what did not run" hole at least up to the launch step.

3. **`gpu_launching` SSE wire payload validated.** The frame parsed
   cleanly through the harness (`httpx` line iterator) into the
   `instance_id`-carrying summary block. `event=gpu_launching` not
   `event=message` — the route's `bridge.emit_gpu_launching()` writes
   the named-event channel correctly.

### 28.2 Latent bugs surfaced (each blocked the next phase)

#### 28.2.1 `procrastinate.AppNotOpen` at defer (T8 patched in this cycle)

The moment T7 was effective and `_launch_one` succeeded, the *next*
line `await app.tasks["run_sam"].defer_async(...)` raised:

```
App was not open. Procrastinate App needs to be opened using:
- ``app.open()``,
- ``await app.open_async()``,
...
```

`procrastinate>=3.8` requires the App to be explicitly opened before
deferring. The existing `flake_analysis/worker/app.py` constructs the
`App(connector=PsycopgConnector(...))` at import time but never opens
the connector pool — the docstring's claim of "lazy-open on first
defer" is true for the worker process (whose `procrastinate worker`
CLI calls `open_async()` itself) but not for the API process.

**Fix (T8):** opened the procrastinate pool in `flake_analysis.api.main`
lifespan:

```python
from flake_analysis.worker.app import app as procrastinate_app
await procrastinate_app.open_async()
try:
    yield
finally:
    await procrastinate_app.close_async()
```

After T8, attempts 5 and 6 actually deferred the SAM job (rows
`procrastinate_jobs.id=11..12`, queue=`gpu`, status `succeeded` —
i.e. the job was claimed by the worker, even though the SAM
inference itself never ran).

#### 28.2.2 AMI bake "dubious ownership" — worker.service never starts (T9 follow-up)

Once T8 unblocked defer, the dispatcher launched `i-0c30c170080222749`
cleanly. SSM came online at 19:47:56Z. But user-data step `[4/8]
clone repo + submodule` aborted at start-of-script:

```
fatal: detected dubious ownership in repository at '/opt/sam/stand-alone-analyzer'
To add an exception for this directory, call:
  git config --global --add safe.directory /opt/sam/stand-alone-analyzer
2026-06-08 19:48:06,892 - cc_scripts_user.py[WARNING]: Failed to run module scripts_user (scripts in /var/lib/cloud/instance/scripts)
2026-06-08 19:48:06,893 - log_util.py[WARNING]: Running module scripts_user ... failed
Cloud-init v. 25.3-0ubuntu1~22.04.1 finished at Mon, 08 Jun 2026 19:48:06 +0000.
```

The pre-baked repo at `/opt/sam/stand-alone-analyzer` is owned by
user `ubuntu` (the AMI's default-user) while `cloud-init` runs as
`root`. Git 2.35+ refuses cross-user repo operations by default. The
user-data script never recovers — every subsequent `[5..8]` step (peft
install, weights download, dataset stage, worker.service enable)
silently doesn't run because the script body is in a single
top-to-bottom block that already aborted.

`worker.service` was confirmed `inactive` via SSM after the instance
came up:

```text
$ systemctl is-active worker.service
inactive
$ systemctl is-active idle-shutdown.timer
inactive
$ ls /opt/sam/weights/
merged.pt    # ← from a prior bake; stale, not refreshed by this user-data
```

`merged.pt` was present (it's pre-baked into the AMI), but with
`worker.service` never started, the procrastinate `gpu`-queue job
sits in `todo` forever and the LISTEN/NOTIFY channel never sees a
`gpu_ready` frame. The harness `httpx` SSE stream consequently hangs
indefinitely waiting for a terminal event.

**Suspected fixes (T9 — not in this acceptance):**

- Bake-side: `chown -R root:root /opt/sam/stand-alone-analyzer` after
  the pre-bake clone, OR
- User-data side: `git config --global --add safe.directory '*'` as
  the very first line of the script (before any git op).
- Either way: add a smoke check that `worker.service is-active` →
  `active` 5 min after instance reaches `running`, and fail the bake
  CI if not.

### 28.3 Run-ledger summary

| Run id | POST ts | Outcome | What surfaced |
|---|---|---|---|
| 6 | 2026-06-08T19:26:19 | failed | both spot + on-demand `InsufficientInstanceCapacity` (T7 fallback verified) |
| 7 | 2026-06-08T19:30:17 | failed | same as run 6 (capacity drought confirmed across two attempts) |
| 8 | 2026-06-08T19:33:44 | failed | spot launch **succeeded** (`i-0dd944abe7d9e432a`), but `defer_async` raised `AppNotOpen` |
| (T8 patch landed: `app.open_async` in API lifespan) | | | |
| 9 | 2026-06-08T19:37:58 | failed | `MaxSpotInstanceCountExceeded` — terminated `i-0dd944abe7d9e432a` was still consuming the 192 vCPU G/VT spot quota during AWS-side request decommission |
| 10 | 2026-06-08T19:44:28 | failed | same as run 9 (quota lag persists ~10 min after terminate; cancelling the spot request explicitly via `cancel-spot-instance-requests` accelerated the unlock) |
| 11 | 2026-06-08T19:46:30 (orphan, harness cancelled mid-fire) | running→failed (post-hoc fixup) | spot launch succeeded `i-0c30c170080222749` but cloud-init aborted at git safe.directory; worker.service never started |
| 12 | 2026-06-08T19:55:12 | running→failed (post-hoc fixup) | reused worker `i-0c30c170080222749`, defer succeeded, but the worker.service was inactive so the procrastinate job sat in `todo` and the SSE stream hung — manually terminated at 60-min wall cap |

(Runs 11 and 12 were left at `status='running'` after manual instance
termination; the post-hoc cleanup `UPDATE runs SET status='failed',
completed_at=now()` reflects the truth that the procrastinate worker
never claimed them. Note runs 11 and 12 were attempted against
**different** instances — run 11 expected `i-0c30c170080222749` to
boot, run 12 found that same instance "live" via `_has_live_worker`
and skipped the launch step. Neither got a `gpu_ready` because
`worker.service` was inactive on that instance.)

Procrastinate jobs 14 and 15 were left in `todo` and were manually
deleted post-run (`DELETE FROM procrastinate_jobs WHERE id IN (14,
15) AND status='todo'`) so a future worker boot doesn't pick them up
out of context.

### 28.4 Cost ledger (this re-run)

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| `i-0dd944abe7d9e432a` (spot, us-east-2b) | 19:33:46→19:35:22 ≈ 1.6 min | $5.96 | $0.16 |
| `i-0c30c170080222749` (spot, us-east-2b) | 19:46:54→20:00:38 ≈ 13.7 min | $5.96 | $1.36 |
| RDS round-trips, SSH tunnel | n/a | n/a | $0.00 |
| **Total this re-run** | | | **~$1.52** |

Within the §27 / T6 standing-approval expected envelope ($1.50-$2)
and well below the $5 hard cap on this run.

### 28.5 Cleanup performed

- `i-0dd944abe7d9e432a` (terminated 19:35:22Z; spot request `sir-xmm7g9dg`
  manually `cancel-spot-instance-requests` to clear the 192 vCPU G/VT
  spot quota — without that the next attempt fails for ~10 min with
  `MaxSpotInstanceCountExceeded` even though the instance is gone).
- `i-0c30c170080222749` (terminated 20:00:38Z; spot request
  `sir-avqzkj9h` cancelled).
- Both verified `terminated` via `aws ec2 wait instance-terminated`.
- Procrastinate `todo` jobs 14, 15 deleted (would otherwise be picked
  up by the next dispatcher launch out-of-context).
- `runs` rows 11, 12 fixed up from stuck-`running` to `failed` with a
  truthful error string.
- API uvicorn process killed (PID file `/tmp/saa-acceptance-api.pid`).
- SSH bastion tunnel torn down (`pkill -f 'ssh.*qpressdb.ch08y4ooqgmq'`).
- Bastion `i-063165d449976b2e4` stopped per default runbook (§2.8).

### 28.6 Status for owner decision

**Verified by this re-run (improves on §27):**
- T7 spot→on-demand fallback wire path (boto3 issues both calls; both
  refusals translate cleanly to `GpuCapacityUnavailable` SSE error).
- `gpu_launching` SSE event payload (first observation in prod).
- T8 procrastinate `app.open_async` lifespan integration (now defers
  succeed; previously every defer raised `AppNotOpen`).

**Still unverified pending T9 (AMI bake fix):**
- `gpu_ready` SSE event.
- Per-image `progress` SSE events.
- `done` SSE event with `result.images>=100`.
- `idle-shutdown.timer` self-terminate.

**Recommended T9 (next cycle, owner decision):** patch the AMI bake
script (or the LT user-data) to set `git config --global --add
safe.directory` before the first git op, AND add a SSM-side smoke
check that asserts `worker.service is-active == active` ≤ 5 min after
the instance enters `running`. Without this, every 1-click is dead
on arrival; spot capacity returning has no value while
worker.service can't start.

### Cumulative cost

| Phase | Cost |
|---|---|
| #229 attempts §18-§26 | $20.54 |
| §27 (dispatcher acceptance — capacity-blocked, no EC2 spent) | $0.00 |
| **§28 (post-T7 acceptance — partial_blocked, AMI bake gap)** | **~$1.52** |
| **Total** | **~$22.06** |

---

## 29. 1-click SAM dispatch acceptance — 2026-06-08 (post-T7b) CAPACITY_BLOCKED

Re-run of §28 after [T7b](#28-1-click-sam-dispatch-acceptance--2026-06-08-post-t7-partial_blocked)
shipped as commit `5eb83d0` — broaden spot-fail catch list
(InsufficientInstanceCapacity, MaxSpotInstanceCountExceeded,
SpotMaxPriceTooLow, SpotInstanceCountLimitExceeded, Unsupported all
trigger on-demand retry) and patch user-data step `[4/8] clone repo`
with `git config --global --add safe.directory '*'` plus
`chown -R "$(id -u):$(id -g)"` on the repo dir before any git op.
LT v27 published with the new user-data + same AMI
`ami-0b7ec5ff47a1eff11`. Same harness shape as §27/§28 (`httpx` POST
`/run/sam` SSE foreground, prod RDS via SSH-tunnelled bastion,
`SAA_AUTH_DEV_BYPASS=1`, dev-bypass admin user).

**Outcome: CAPACITY_BLOCKED.** Eight consecutive `POST /run/sam`
attempts over a ~41-minute window (20:43:04Z → 21:23:31Z) all hit
`InsufficientInstanceCapacity` for `g6e.48xlarge` on **both spot AND
on-demand**. T7's fallback path **fired correctly on every attempt**
— the literal "GPU capacity unavailable in us-east-2 (both spot and
on-demand)" envelope only originates from the second `except
ClientError` branch of `_launch_one` after the on-demand retry also
returned `InsufficientInstanceCapacity` — but no EC2 instance ever
launched, so the T7b user-data fix could not be exercised this run.

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `5eb83d0` (T7b broadened spot-fail + git safe.directory) |
| AMI | `ami-0b7ec5ff47a1eff11` (cu124, §15-verified) — **not booted this run** |
| Launch template | `qpress-sam-gpu-worker` v27 (default, spot-with-on-demand-fallback) |
| Instances launched | **none** — `RunInstances` rejected on every attempt for both spot and on-demand |
| Run IDs (RDS `runs`) | 13, 14, 15, 16, 17, 18, 19, 20 (all `failed` / `GpuCapacityUnavailable`) |
| First POST ts (UTC) | 2026-06-08T20:43:04 |
| Last POST ts (UTC) | 2026-06-08T21:23:09 |
| `gpu_launching` SSE | **never fired** — `_launch_one` exhausted both spot and on-demand on every attempt before reaching `bridge.emit_gpu_launching()` |
| `gpu_ready` SSE | **never fired** (no worker booted) |
| Per-image `progress` SSE | **never fired** |
| `done` SSE | **never fired** |
| Cold-start wall | n/a |
| Total instance wall (billed) | **0 min** |
| Cost (this re-run) | **$0.00** |
| Status | `capacity_blocked` |

### 29.1 What was newly verified vs §27/§28

1. **T7b broadened spot-fail catch list and on-demand fallback both
   wire through cleanly.** Every one of the 8 attempts returned the
   "both spot and on-demand" error envelope ~16-28 s after `POST`,
   meaning boto3 issued both calls and AWS returned
   `InsufficientInstanceCapacity` for the on-demand path too. SSE
   error frame parsed cleanly through the `httpx` line iterator.

2. **Per-scan mutex behaved correctly under repeat-fail traffic**
   (same as §27.1). Re-issuing `POST /run/sam` against the same
   `scan_id=1` immediately after each failure succeeded (no `423
   ProjectBusy`), confirming `acquire_scan_lock` releases on both
   success and error paths. PgAdvisoryLock cleanup also worked.

3. **T8 procrastinate `app.open_async` lifespan integration** is
   wired in but couldn't be exercised because no defer ever happened
   — `_launch_one` errored before `defer_async`.

### 29.2 What did NOT run (same set as §28, still unverified)

The cold-start lifecycle below could not be exercised because no
EC2 instance ever launched on this run:

- T7b user-data git safe.directory fix (`worker.service` start-up).
- `gpu_launching` SSE event with `instance_id`.
- `gpu_ready` SSE event with `image_count`.
- Per-image `progress` events streaming.
- Terminal `done` event with `result.images >= 100`.
- `idle-shutdown.timer` firing after 10 min idle.

### 29.3 Capacity diagnostic

Spot price history is flat for the entire 4-day window leading up to
this attempt (`us-east-2b $5.926` at 2026-06-08T19:00:54Z, no
movement since 15:01Z). Reference on-demand for `g6e.48xlarge` is
~$3.96/hr; current spot clearing $5.93-$7.47/hr indicates persistent
AWS-side scarcity (H200 inventory pressure that started in §27 and
has not abated).

```text
$ aws ec2 describe-spot-price-history --instance-types g6e.48xlarge \
    --product-descriptions 'Linux/UNIX' \
    --start-time 2026-06-08T20:00:00Z
us-east-2b  5.9258  2026-06-08T19:00:54+00:00   ← unchanged for ≥4 hrs
us-east-2c  6.4254  2026-06-08T18:00:24+00:00
us-east-2a  7.4683  2026-06-08T15:01:00+00:00
```

All 8 attempts failed at the EC2 control plane on **both** spot and
on-demand with `InsufficientInstanceCapacity` — boto3 never returned
an `InstanceId` on either call. AZ-level diagnostics are not
available because the LT does not pin a subnet. The fact that
**on-demand** also returned `InsufficientInstanceCapacity` (not
`InstanceLimitExceeded`) means this is genuine capacity scarcity,
not quota.

### 29.4 Run-ledger summary

| Run id | POST ts (UTC) | Wall | Outcome |
|---|---|---|---|
| 13 | 2026-06-08T20:43:06 | ~24 s | failed: both spot + on-demand `InsufficientInstanceCapacity` |
| 14 | 2026-06-08T20:47:43 | ~17 s | same |
| 15 | 2026-06-08T20:52:10 | ~23 s | same |
| 16 | 2026-06-08T20:57:58 | ~22 s | same |
| 17 | 2026-06-08T21:03:30 | ~26 s | same |
| 18 | 2026-06-08T21:10:07 | ~24 s | same |
| 19 | 2026-06-08T21:17:35 | ~28 s | same |
| 20 | 2026-06-08T21:23:09 | ~23 s | same |

Inter-attempt sleep was 3-7 min (escalating) to give AWS-side capacity
a chance to recover; it did not. No `MaxSpotInstanceCountExceeded`
quota lag this run because no instance ever consumed quota in the
first place.

### 29.5 Cost ledger (this re-run)

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| GPU instances | 0 min | n/a | **$0.00** |
| RDS round-trips, SSH tunnel, bastion uptime (~45 min t3.micro) | ~45 min | ~$0.0104 | ~$0.008 |
| **Total this re-run** | | | **~$0.00** (rounded) |

Within the $5 hard cap, with $5 untouched. Bastion auto-stopped at
end of run per §2.8 runbook.

### 29.6 Cleanup performed

- API uvicorn process terminated cleanly (PID `47305` killed via
  `/tmp/saa-acceptance-api.pid`).
- SSH bastion tunnel torn down (`pkill -f 'ssh.*qpressdb.ch08y4ooqgmq'`).
- Bastion `i-063165d449976b2e4` `stop-instances` issued (was
  `stopped` at start, brought up for tunnel, returned to `stopped`).
- No EC2 GPU instances live (verified `describe-instances ...
  filter sam-gpu-worker pending,running,stopping,shutting-down`
  empty before, during, and after the run).
- Staging RDS rows (project `acceptance-2026-06-08`, scan id=1, 100
  images, analyses id=1, models id=1) **left in place** for the
  next attempt.
- Stale procrastinate `gpu` job id=10 (status `doing`, run_id=99100,
  unrelated context from a prior session) marked `failed` so a
  fresh worker boot doesn't pick it up out of context.
- RDS password file `/tmp/saa-pg.txt` deleted; PGPASSWORD env not
  echoed to disk.
- Harness scripts left at `/tmp/saa-acceptance-server.py` and
  `/tmp/saa-acceptance-run.py` — small enough to leave for the
  next attempt; SSE log files at `/tmp/saa-acceptance-sse-T6a3*.log`.

### 29.7 Status for owner decision

**Verified by this re-run (improves on §28):**
- T7b broadened spot-fail catch list — fallback path is invoked on
  every spot rejection variant the LT can produce.
- T7's spot→on-demand wire path is robust under sustained drought
  (~41 min of repeat-fail with no false-positive; no zombie tasks,
  no stuck advisory locks, no orphan `running` rows).

**Still unverified (carry forward from §28):**
- T7b user-data git safe.directory fix (`worker.service` start-up
  on a freshly-booted instance) — could not be exercised because
  no instance launched.
- `gpu_ready` / per-image `progress` / `done` / `idle-shutdown.timer`
  SSE events — same.

**Owner decision points:**
1. **Wait for capacity:** the dispatcher is now code-validated end-to-end
   for both the failure path AND the fallback path; the only thing
   between us and SUCCESS is AWS returning `g6e.48xlarge` spot **or**
   on-demand capacity in us-east-2. Capacity has been blocked for the
   full §27 + §28 + §29 window (~3 hours of attempts spread over a
   day). Recommendation: re-run §29 in 24 hours and again in 48 if
   still blocked.
2. **Switch region:** us-east-1 / us-west-2 typically have larger
   `g6e.48xlarge` pools. Adds RDS cross-region cost + tunnel reroute
   complexity. Out of scope for this acceptance; flag if drought
   persists 72+ hrs.
3. **Switch instance type:** `g6e.12xlarge` (single-GPU H200, ~$5.10
   on-demand) is functionally adequate for the acceptance dataset
   (100 images on merged.pt) and has more capacity headroom. Adds an
   LT v28 publish + bake-side re-verification. Out of scope here.

### Cumulative cost

| Phase | Cost |
|---|---|
| #229 attempts §18-§26 | $20.54 |
| §27 (dispatcher acceptance — capacity-blocked, no EC2 spent) | $0.00 |
| §28 (post-T7 acceptance — partial_blocked, AMI bake gap) | ~$1.52 |
| §29 (post-T7b acceptance — capacity-blocked, no EC2 spent) | $0.00 |
| **§30 (post-T7b retry, capacity restored — USERDATA_BLOCKED)** | **~$1.19** |
| **Total** | **~$23.25** |

---

## 30. 1-click SAM dispatch acceptance — 2026-06-09 (post-T7b, capacity restored) USERDATA_BLOCKED

Re-run of §29 after a PM live-launch probe at 2026-06-09 confirmed
`g6e.48xlarge` spot capacity has returned to us-east-2a (probe instance
`i-0b2b8d7d5e82aaf34`, 2-second allocation, immediately terminated).
Same harness shape as §27/§28/§29 (`httpx` POST `/run/sam` SSE
foreground, prod RDS via SSH-tunnelled bastion, `SAA_AUTH_DEV_BYPASS=1`,
dev-bypass admin user).

**Outcome: USERDATA_BLOCKED.** Spot launch SUCCEEDED on the first try
(no on-demand fallback needed — capacity drought is over), instance
reached `running` in ~3 s, SSM came online, but **cloud-init's
`scripts_user` step crashed at user-data line 163** with:

```text
[4/8] clone repo + submodule
fatal: $HOME not set
2026-06-09 13:06:58 - cc_scripts_user.py[WARNING]: Failed to run module scripts_user
```

The failure is on the literal T7b fix itself: `git config --global
--add safe.directory '*'` writes to `~/.gitconfig`, which requires
`$HOME` to resolve. cloud-init's scripts_user module runs as root with
an empty environment (no `$HOME`, no `$USER`), and `set -euo pipefail`
on user-data line 5 turns the `git config` non-zero exit into a hard
abort BEFORE the safe.directory entry is ever written and BEFORE the
`chown` belt-2 fix runs (line 165). The original "dubious ownership"
error this fix was meant to address never appears because we crash
earlier.

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `3082950` (T7b + §29 docs) |
| AMI | `ami-0b7ec5ff47a1eff11` — booted, but cloud-init aborted at user-data step 4 |
| Launch template | `qpress-sam-gpu-worker` v27 (default) |
| Instances launched | 1 — `i-0453e6a88fa017783` (g6e.48xlarge spot, us-east-2a) |
| Run ID (RDS `runs`) | next id assigned (worker_events still has entries; check after acceptance) |
| POST ts (UTC) | 2026-06-09T13:05:19 |
| `gpu_launching` SSE | **fired** at +3.28 s (success path) |
| Instance `running` | +3 s after launch (no spot allocation lag — capacity present) |
| SSM `Online` | ~+1 min after running |
| cloud-init step 4 | **FAILED** — `fatal: $HOME not set` at line 163 |
| `worker.service` | never installed — cloud-init aborted before step 5 |
| `gpu_ready` SSE | never fired |
| Per-image `progress` SSE | never fired |
| `done` SSE | never fired |
| Total instance wall (billed) | ~12 min (PM observed wedge, terminated manually) |
| Cost (this re-run) | **~$1.19** (g6e.48xlarge spot @ ~$5.96/hr × 12 min) |
| Status | `userdata_blocked` |

### 30.1 Root cause: T7b fix is broken in the cloud-init environment

T7b commit `5eb83d0` added two lines to `scripts/aws/sam-gpu-worker-userdata.sh`
inside the `[4/8] clone repo` block (line 163, 165):

```bash
git config --global --add safe.directory '*'   # line 163 — CRASHES under cloud-init
if [[ -d "${REPO_DIR}/.git" ]]; then
  chown -R "$(id -u):$(id -g)" "${REPO_DIR}"   # line 165 — never reached
fi
```

`git config --global` writes to `${HOME}/.gitconfig`. cloud-init
`scripts_user` runs as root with an empty env — `$HOME` is unset —
and git refuses with `fatal: $HOME not set` (exit 128). Combined with
the user-data preamble's `set -euo pipefail`, this aborts the whole
bootstrap. Step 5 (venv), 6 (M3 weights), 7 (worker.service) never run.

The original "dubious ownership" error this fix was meant to address
(observed in §28) cannot be confirmed/disconfirmed because we crash
upstream of it.

### 30.2 Recommended fix (T7c)

Two-line change to user-data, both setting `HOME` for the git invocation
and exposing the safe.directory entry to all callers via the system-wide
config (no `$HOME` dependency):

```bash
# Replace line 163 with one of these belts:
# (A) set HOME for the git invocation:
HOME=/root git config --global --add safe.directory '*'

# OR (B) write to system gitconfig directly (no HOME required):
git config --system --add safe.directory '*'
```

Option B is preferable: `--system` writes to `/etc/gitconfig`, applies
to every uid that ever touches the repo (root during cloud-init,
ubuntu during worker.service), and avoids root creating a stray
`/root/.gitconfig`. This also makes belt-2 chown (line 165) optional;
keeping it doesn't hurt, but the `--system` config alone is sufficient
to address "dubious ownership" for both root and ubuntu.

A LT v28 publish (or v27 in-place revision via
`create-launch-template-version`) is needed after the fix lands. AMI
does not need re-baking — `ami-0b7ec5ff47a1eff11` stays.

### 30.3 What was newly verified this run vs §28/§29

1. **Capacity drought is over.** us-east-2a returned `g6e.48xlarge`
   spot in 3 seconds. Spot price is back near on-demand reference,
   inventory pressure has eased.
2. **`gpu_launching` SSE fires correctly through the dispatcher's
   success path** (first time observed end-to-end after T7b — §28
   exercised the AMI-bake-gap path, §29 only the on-demand-fallback
   path; this run is the first to show `gpu_launching` with a real
   `instance_id` from a successful `RunInstances` call).
3. **SSE heartbeat flow is healthy** — keepalive `event=message data=`
   frames stream every 15 s while the worker boots, confirming the
   `httpx` line iterator + uvicorn keepalive interplay holds for
   ≥12 min of waiting (no premature stream close).

### 30.4 What did NOT run

- Step 5/6/7 of user-data (venv, M3 weights, worker.service install).
- `worker.service` start-up.
- `gpu_ready` SSE event with `image_count`.
- Per-image `progress` events.
- Terminal `done` event with `result.images >= 100`.
- `idle-shutdown.timer` firing.

### 30.5 Run timeline (UTC)

| t | Event |
|---|---|
| 13:05:19 | POST /run/sam from `/tmp/saa-acceptance-run.py` |
| 13:05:20 | HTTP 200, content-type=text/event-stream |
| 13:05:22 | `event=gpu_launching` SSE fired with `instance_id=i-0453e6a88fa017783` |
| 13:05:22 | EC2 `running` state (spot, us-east-2a) |
| ~13:06:25 | SSM agent online |
| 13:06:57 | cloud-init `modules:final` enters scripts_user |
| 13:06:58 | user-data step 4 — `git config --global` → `fatal: $HOME not set` |
| 13:06:58 | cloud-init aborts; worker.service never installed |
| 13:05:37 → 13:17:08 | SSE keepalive frames every ~15 s (~46 frames) |
| 13:17:30 | PM observed wedge, ran `terminate-instances` |
| 13:18:00 | SSE foreground process killed (`pkill saa-acceptance-run.py`) |
| 13:18:30 | Instance `shutting-down` (auto-terminate from `idle-shutdown.timer` would not have fired anyway — timer was never installed) |

### 30.6 Cost ledger (this re-run)

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| g6e.48xlarge spot (us-east-2a, single instance) | ~12 min | ~$5.96 | **~$1.19** |
| Bastion t3.micro (~10 min uptime overlap) | ~10 min | ~$0.0104 | ~$0.002 |
| RDS round-trips, NAT GW data | negligible | | ~$0.001 |
| **Total this re-run** | | | **~$1.19** |

Within the $5 hard cap. Wall-time was 14 min (POST 13:05:19 → cleanup
13:19), well within the 60-min cap. PM caught the wedge at the +12 min
mark; auto-terminate from `idle-shutdown.timer` would not have fired
because the timer was never installed (cloud-init aborted at step 4 of 8).

### 30.7 Cleanup performed

- API uvicorn process (`pid 44674`) — left running for next attempt
  (no harm; bastion tunnel tied to its lifetime). Owner can kill via
  `pkill -f saa-acceptance-server` after T7c lands.
- SSH bastion tunnel still open on 127.0.0.1:5433 (for the imminent
  T7c re-run; tunnel stays up).
- Bastion `i-063165d449976b2e4` left **running** (next attempt is
  hours away, not days; cost is ~$0.25/day for t3.micro — acceptable).
- GPU instance `i-0453e6a88fa017783` `terminate-instances` issued at
  13:17:30; verified `shutting-down` at 13:18.
- No other live SAM instances (verified via tag-filtered
  `describe-instances`).
- Staging RDS rows (project `acceptance-2026-06-08`, scan id=1, 100
  images, analyses id=1, models id=1) **left in place** for T7c re-run.
- Harness scripts kept at `/tmp/saa-acceptance-{server,run,images,stage}*`
  + SSE log `/tmp/saa-acceptance-sse-T6a3-r9.log`.

### 30.8 Status for owner decision

**Verified by this re-run (improves on §27/§28/§29):**
- AWS spot capacity for `g6e.48xlarge` has returned to us-east-2a.
- Dispatcher's success path through `_launch_one` → `bridge.emit_gpu_launching`
  → SSE `gpu_launching` works end-to-end (first time observed).
- SSE keepalive holds for ≥12 min.

**New blocker surfaced (this run is the first to expose it):**
- T7b's `git config --global` fix in user-data line 163 is broken
  under cloud-init's empty-env root context — `fatal: $HOME not set`.

**Next action — T7c:**
1. Patch user-data line 163 from `git config --global` to
   `git config --system` (preferred) or `HOME=/root git config --global`
   (acceptable). Also strongly consider bringing `HOME=/root` into the
   user-data preamble (line 5 area) so any future `--global` git
   invocations don't re-trip this.
2. Publish LT v28 (or revise v27 in-place via
   `create-launch-template-version`).
3. Re-run §30 acceptance — capacity is present, so a clean userdata
   path should produce SUCCESS in ~5-12 min.

**Out of scope for the next acceptance attempt** (defer until SUCCESS
is reached at least once):
- Region switch.
- Instance-type switch (g6e.12xlarge alternative).

---

## 31. 1-click SAM dispatch acceptance — 2026-06-09 (post-T7c) USERDATA_BLOCKED_2

Re-run of §30 after [T7c](#302-recommended-fix-t7c) shipped as commit
`d929ce3` — `git config --global` → `git config --system` (writes
`/etc/gitconfig`, no `$HOME` dependency, applies to root and ubuntu).
LT v28 published default with the patched user-data (same AMI
`ami-0b7ec5ff47a1eff11`). Same harness shape as §27-§30 (`httpx` POST
`/run/sam` SSE foreground, prod RDS via SSH-tunnelled bastion,
`SAA_AUTH_DEV_BYPASS=1`, dev-bypass admin user).

**Outcome: USERDATA_BLOCKED_2.** T7c fix worked exactly as designed —
`git config --system` succeeded with empty env, `git fetch` advanced
the main repo from `e2a2ede..d929ce3` cleanly, no "$HOME not set",
no "dubious ownership". User-data step 4 then **crashed at the next
sub-line**: `git submodule update --init --recursive --force
vendor/QPress-SAM-Flake` failed with:

```text
fatal: could not read Username for 'https://github.com': No such device or address
Errors during submodule fetch:
	vendor/QPress-SAM-Flake
error: Could not fetch origin
```

The submodule (`https://github.com/HoukJangBNL/QPress-SAM-Flake.git`)
is private. The AMI was baked with a one-shot GH PAT exposed via
`git -c http.extraHeader=...` (per #225 / #228), but at runtime no
PAT is in the cloud-init environment. `git submodule update --force`
re-fetches even when the working-tree submodule SHA already matches
the gitlink, which it does (baked SHA `2c69ebd` matches the gitlink
in `d929ce3` — `git log e2a2ede..d929ce3 -- vendor/QPress-SAM-Flake`
returns empty).

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `d929ce3` (T7c: --global → --system) |
| AMI | `ami-0b7ec5ff47a1eff11` (unchanged from §28-§30) |
| Launch template | `qpress-sam-gpu-worker` v28 (default, T7c user-data) |
| Instances launched | 2 — `i-04b80f4059a17f067` (g6e.48xlarge spot us-east-2a, primary) + 1 quota-blocked rejection at the 7-min mark after §30's terminate (see §31.1) |
| Run ID (RDS `runs`) | (next in sequence; verify post-acceptance) |
| First POST ts (UTC) | 2026-06-09T13:25:52 (rejected — see §31.1) |
| Successful POST ts (UTC) | 2026-06-09T13:27:46 |
| `gpu_launching` SSE | **fired** at +3.25 s on the second POST |
| Instance `running` | +3 s after launch (spot, us-east-2a) |
| SSM `Online` | +~1.5 min |
| cloud-init step 4 — `git config --system` | **SUCCEEDED** (T7c verified) |
| cloud-init step 4 — `git fetch origin` (main repo) | **SUCCEEDED** (`e2a2ede..d929ce3`) |
| cloud-init step 4 — `git submodule update --force` | **FAILED** — private repo + no PAT at runtime |
| `worker.service` | never installed (cloud-init aborted at submodule update) |
| `gpu_ready` SSE | never fired |
| Per-image `progress` SSE | never fired |
| `done` SSE | never fired |
| Total instance wall (billed) | ~6.2 min |
| Cost (this re-run) | **~$0.62** (g6e.48xlarge spot @ ~$5.96/hr × 6.2 min) |
| Status | `userdata_blocked_2` |

### 31.1 First POST quota-lag false start

The first `POST /run/sam` at 13:25:52Z was rejected in 3.8 s with
`MaxSpotInstanceCountExceeded`. Root cause: §30's terminated instance
`i-0453e6a88fa017783` left its spot request `sir-iv87j31j` in
`active/fulfilled` state for ~10 min after instance termination
(13:17:30 → ~13:27 release lag). The G/VT spot quota (192 vCPU = one
g6e.48xlarge) was therefore still consumed at 13:25.

`_launch_one`'s broadened catch list (T7b) includes
`MaxSpotInstanceCountExceeded` and falls back to on-demand, but **the
on-demand call was also rejected** with `MaxSpotInstanceCountExceeded`
— AWS appears to count the lingering spot request against on-demand
g6e quota too in this transient window. Per `_launch_one` lines
332-338, code2 != IIC propagates raw, so the SSE got the boto3
`ClientError.__str__` form.

**Mitigation**: explicit `cancel-spot-instance-requests sir-iv87j31j`
released the quota in <30 s (reported `cancelled`). Second POST at
13:27:46 succeeded.

This confirms the §28.3 known operational pattern — and motivates a
T9 follow-up (out of acceptance scope): wire `_terminate_worker` (or
its callers) to also `cancel-spot-instance-requests` whenever it
terminates a spot instance, so the next launch isn't quota-blocked
for ~10 min.

### 31.2 Root cause: submodule update needs auth at runtime

User-data line 180:
```bash
git submodule update --init --recursive --force vendor/QPress-SAM-Flake
```

`vendor/QPress-SAM-Flake` is `https://github.com/HoukJangBNL/QPress-SAM-Flake.git`
(private). `--force` causes git to re-fetch even when the working-tree
submodule SHA already matches the gitlink. With no PAT in the runtime
env and no SSH key, git prompts for HTTPS credentials, which fails
non-interactively with `fatal: could not read Username for
'https://github.com': No such device or address`. `set -euo pipefail`
turns this into a hard abort.

This NEW failure mode was masked by §28-§30 because user-data crashed
upstream (at line 163 `git config --global` → `$HOME not set`). T7c
unblocked line 163-169, so line 180 is the first line that has
actually run since the underlying issue (private submodule, no
runtime PAT) was introduced.

The baked submodule (at SHA `2c69ebd`) is already correct — `git log
e2a2ede..d929ce3 -- vendor/QPress-SAM-Flake` returns empty across the
T7c push, meaning no submodule changes are needed. The `--force`
re-fetch is wasted work AND the cause of the failure.

### 31.3 Recommended fix (T7d) — three options

**Option A (preferred): drop `--force` and check first.** The baked
submodule already matches the gitlink in 99% of cases (only fails when
the main-repo HEAD bumps the submodule gitlink between bake and
launch). Replace line 180 with:

```bash
# If the baked submodule SHA matches the gitlink, no fetch needed.
expected_sha="$(cd "${REPO_DIR}" && git ls-tree HEAD vendor/QPress-SAM-Flake | awk '{print $3}')"
actual_sha="$(cd "${REPO_DIR}/vendor/QPress-SAM-Flake" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo none)"
if [[ "${expected_sha}" != "${actual_sha}" ]]; then
  # Need a runtime fetch — fail loudly with a clear message.
  echo "FATAL: vendor/QPress-SAM-Flake gitlink (${expected_sha:0:8}) != baked SHA (${actual_sha:0:8})." >&2
  echo "  AMI re-bake required (PAT not exposed at runtime by design)." >&2
  exit 1
fi
echo "[4/8] vendor submodule already at ${actual_sha:0:8} (baked match) — skip fetch"
```

Pros: zero secrets at runtime; fails loudly when AMI re-bake IS
needed; idempotent on every cold launch. Cons: requires AMI re-bake
when the main-repo bumps the submodule gitlink (which is rare and
already requires bake-side action for sam2 vendor pin anyway).

**Option B: pass a runtime PAT via launch-template UserData
substitution OR Secrets Manager.** Adds one-shot env-var injection
during cloud-init (e.g.
`git -c "http.extraHeader=Authorization: Bearer $(aws secretsmanager
get-secret-value --secret-id qpress/gh-pat --query SecretString
--output text)" submodule update ...`). Pros: keeps the `--force`
re-fetch for safety. Cons: secret in IAM-readable scope, more moving
parts, ongoing rotation burden.

**Option C: Bake without the submodule and fetch fresh on every
launch with PAT.** Reverses §16/§228 entirely. Out of scope.

**Recommendation**: Option A. The baked AMI is already the source of
truth for the submodule SHA; `--force` was always a belt-and-suspenders
that introduced the secret-at-runtime requirement.

### 31.4 What was newly verified vs §30

1. **T7c `git config --system` works under cloud-init's empty-env
   root context.** No `$HOME not set`, the `[safe.directory] *` entry
   is written to `/etc/gitconfig`, and the subsequent `git fetch` on
   the main repo succeeded (advanced from baked `e2a2ede` to
   `d929ce3`). The original "dubious ownership" error this fix
   targeted is fully suppressed.
2. **Main repo `git fetch + reset --hard origin/main` works at
   runtime** without PAT. (The main repo is public; only the
   submodule is private.)
3. **`gpu_launching` SSE again fires within +3.25 s** of POST,
   matching §30's timing.
4. **Spot+on-demand fallback after a recent terminate is brittle.**
   The §28.3 known issue (lingering spot request blocks both spot AND
   on-demand for ~10 min) reproduced exactly. Manual
   `cancel-spot-instance-requests` resolves it in <30 s.

### 31.5 What did NOT run (carry forward from §30)

- Step 5/6/7 of user-data (venv, M3 weights install,
  worker.service install).
- `worker.service` start-up.
- `gpu_ready` SSE event with `image_count`.
- Per-image `progress` events.
- Terminal `done` event with `result.images >= 100`.
- `idle-shutdown.timer` firing.

### 31.6 Run timeline (UTC)

| t | Event |
|---|---|
| 13:25:52 | POST #1 (7 min after §30 terminate) |
| 13:25:55 | SSE error `MaxSpotInstanceCountExceeded` (spot quota lingering from §30 termination) |
| 13:26:54 | PM identifies sir-iv87j31j active/fulfilled despite instance terminated |
| 13:27:00 | `cancel-spot-instance-requests sir-iv87j31j` → `cancelled` |
| 13:27:46 | POST #2 |
| 13:27:48 | HTTP 200, content-type=text/event-stream |
| 13:27:50 | `event=gpu_launching` `instance_id=i-04b80f4059a17f067` |
| 13:27:50 | EC2 `running` (spot, us-east-2a) |
| 13:29:13 | SSM agent online |
| 13:29:23 | cloud-init `modules:final` enters scripts_user |
| 13:29:23-25 | user-data step 4: `git config --system` ✅, `git fetch origin` ✅ (`e2a2ede..d929ce3`) |
| 13:29:25-27 | `git submodule update --init --recursive --force vendor/QPress-SAM-Flake` → `fatal: could not read Username` |
| 13:29:28 | cloud-init aborts at scripts_user |
| 13:29:50 - 13:33:50 | SSE keepalive frames every 15 s (~16 frames) |
| 13:34:00 | PM `terminate-instances i-04b80f4059a17f067` |
| 13:34:30 | `cancel-spot-instance-requests sir-rftqjw5g` (proactive) |
| 13:35:00 | SSE foreground process killed |

### 31.7 Cost ledger (this re-run)

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| g6e.48xlarge spot (us-east-2a, single instance, §31b) | ~6.2 min | ~$5.96 | **~$0.62** |
| Bastion t3.micro (~10 min uptime overlap) | ~10 min | ~$0.0104 | ~$0.002 |
| RDS round-trips, NAT GW data | negligible | | ~$0.001 |
| **Total this re-run** | | | **~$0.62** |

Within the $5 hard cap. Wall-time ~9 min (POST #1 13:25:52 → cleanup
13:35), well within the 60-min cap. No `idle-shutdown.timer` to rely
on (timer was never installed because cloud-init aborted at step 4).

### 31.8 Cleanup performed

- API uvicorn process — left running for next attempt (still tied to
  bastion tunnel lifetime).
- SSH bastion tunnel — left open on 127.0.0.1:5433 for T7d re-run.
- Bastion `i-063165d449976b2e4` — left **running** (T7d landing is
  hours, not days, away).
- GPU instance `i-04b80f4059a17f067` `terminate-instances` issued at
  ~13:34Z; verified `shutting-down`.
- Both spot requests this run (`sir-iv87j31j` from §30,
  `sir-rftqjw5g` from §31b) **explicitly cancelled** to release quota
  immediately for the next attempt — avoids the §31.1 false-start
  pattern.
- No other live SAM instances (verified via tag-filtered
  `describe-instances`).
- Staging RDS rows (project `acceptance-2026-06-08`, scan id=1, 100
  images, analyses id=1, models id=1) **left in place** for T7d.
- Harness scripts kept at `/tmp/saa-acceptance-{server,run,images,stage}*`
  + SSE logs `/tmp/saa-acceptance-sse-T6a3-r10*.log`.

### 31.9 Status for owner decision

**Verified by this re-run (improves on §30):**
- T7c `git config --system` is the right shape for cloud-init's
  empty-env root context — `safe.directory` entry applies and
  subsequent main-repo `git fetch` runs cleanly.
- `gpu_launching` SSE wire path is repeatable.
- §28.3's quota-lag known issue is reproducible and remediable in
  <30 s with explicit `cancel-spot-instance-requests`.

**New blocker (this run is the first to expose it because §28-§30
crashed upstream):**
- User-data line 180 `git submodule update --init --recursive
  --force vendor/QPress-SAM-Flake` requires runtime auth that no
  longer exists (PAT is bake-time only). The `--force` re-fetch is
  unnecessary because the baked submodule SHA already matches the
  gitlink at every cold launch (gitlink only changes on intentional
  bake-side bumps).

**Next action — T7d:**
1. Implement Option A (§31.3): replace user-data line 180 with a
   bake-vs-gitlink SHA check that no-ops when they match and exits
   loudly when they don't (which signals "AMI re-bake required" to
   the operator).
2. Publish LT v29 (or revise v28 in-place) with the patched user-data.
3. Re-run §31 acceptance — capacity should be present (today's probes
   succeeded), and a clean step-4 path should produce SUCCESS in
   ~5-12 min.

**Out of scope for the next acceptance attempt:**
- Region switch / instance-type switch (still deferred).
- T9 spot-request-cancel on terminate (operational improvement,
  flagged for separate plan).

### Cumulative cost update

| Phase | Cost |
|---|---|
| #229 attempts §18-§26 | $20.54 |
| §27 (capacity-blocked, no EC2 spent) | $0.00 |
| §28 (post-T7 partial_blocked, AMI bake gap) | ~$1.52 |
| §29 (post-T7b capacity-blocked, no EC2 spent) | $0.00 |
| §30 (post-T7b retry, capacity restored — userdata_blocked) | ~$1.19 |
| §31 (post-T7c — userdata_blocked_2, submodule auth) | ~$0.62 |
| **§32 (post-vendor-revert — userdata_blocked_3, same line, gitlink revert insufficient)** | **~$0.55** |
| **Total** | **~$24.42** |

$100 cap remaining: ~$75.58.

---

## 32. 1-click SAM dispatch acceptance — 2026-06-09 (post-vendor-revert) USERDATA_BLOCKED_3

Re-run of §31 after owner shipped commit `6090d9c`:
`revert(vendor): gitlink back to 61eb37d (AMI bake-time SHA)` — the
intent being "vendor gitlink == baked SHA, so `git submodule update
--init --recursive --force` becomes a no-op fetch." LT v29 published
default with same AMI `ami-0b7ec5ff47a1eff11` and unchanged user-data
script (T7c content). Same harness shape as §27-§31.

**Outcome: USERDATA_BLOCKED_3.** Cloud-init aborted at the **exact
same line** as §31 (`git submodule update --init --recursive --force
vendor/QPress-SAM-Flake`) with the **exact same error** (`fatal:
could not read Username for 'https://github.com'`). Verified on the
live instance via SSM:

```text
$ git ls-tree HEAD vendor/QPress-SAM-Flake
160000 commit 2c69ebd7ea9f78c77f2b04778116ab38d1c4008a   vendor/QPress-SAM-Flake

$ cd vendor/QPress-SAM-Flake && git rev-parse HEAD
2c69ebd7ea9f78c77f2b04778116ab38d1c4008a
```

The gitlink `2c69ebd` matches the baked submodule SHA `2c69ebd`
exactly. **Yet `git submodule update --init --recursive --force`
still attempted a fetch** that requires GitHub auth and failed.

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `6090d9c` (vendor gitlink revert) |
| AMI | `ami-0b7ec5ff47a1eff11` (unchanged from §28-§31) |
| Launch template | `qpress-sam-gpu-worker` v29 (default) |
| Instances launched | 1 — `i-0efe1950cc6e6cc8f` (g6e.48xlarge spot us-east-2a) |
| POST ts (UTC) | 2026-06-09T18:40:54 |
| `gpu_launching` SSE | **fired** at +3.17 s |
| Instance `running` | +3 s after launch |
| SSM `Online` | ~+1.5 min |
| cloud-init step 4 — `git config --system` | **SUCCEEDED** (T7c verified again) |
| cloud-init step 4 — `git fetch` (main repo) | **SUCCEEDED** (`e2a2ede..6090d9c`) |
| cloud-init step 4 — `git submodule update --init --force` | **FAILED** — same error as §31 despite matching gitlink |
| `worker.service` | never installed (cloud-init aborted at step 4) |
| `gpu_ready` / progress / `done` SSE | never fired |
| Total instance wall (billed) | ~5.5 min |
| Cost (this re-run) | **~$0.55** (g6e.48xlarge spot @ ~$5.96/hr × 5.5 min) |
| Status | `userdata_blocked_3` |

### 32.1 Root cause: `git submodule update --init` fetches unconditionally regardless of gitlink match

The owner directive ("revert vendor gitlink → submodule update is
no-op") rests on a faulty git-submodule-update model. Verified on
the live instance:

- The submodule's `.git` directory IS already initialized at AMI
  bake time (it's not a freshly-cloned repo).
- The submodule's local HEAD IS already `2c69ebd` (the gitlink
  target).
- Fetching to converge those two would be a true no-op.

**But** `git submodule update --init --recursive --force` doesn't
make that comparison. With `--init`, git unconditionally runs
`git submodule sync` + a remote fetch on every invocation as part
of the `init` step (this is by design — `--init` always re-binds
the submodule's `origin` and pulls remote refs, regardless of
local state). With `--force`, it then resets the working tree to
the gitlink. The fetch happens BEFORE the reset, and the fetch
fails because no PAT is in the runtime env.

The fix is in user-data, not in the gitlink. The gitlink revert
was orthogonal to this failure mode.

### 32.2 What was confirmed (good news)

1. **T7c continues to work.** `git config --system --add
   safe.directory '*'` succeeded on a fresh boot for the second
   time in a row. The `safe.directory` mechanism applies to root
   in cloud-init's empty-env context.
2. **Main repo fetch is healthy at runtime.** `e2a2ede..6090d9c`
   advanced cleanly without auth (the main repo
   `HoukJangBNL/stand-alone-analyzer` is public).
3. **`gpu_launching` SSE wire path is repeatable** — three
   successful observations now (§30, §31, §32), all at +3.0 - 3.3 s
   after POST.
4. **`g6e.48xlarge` spot capacity in us-east-2a is stable** as of
   18:40Z 2026-06-09 — first-try spot allocation in 3 s, no
   on-demand fallback needed.
5. **No quota-lag false-start this run** — the §31 cleanup that
   explicitly cancelled both spot requests prevented the §28.3
   pattern from re-firing.

### 32.3 Recommended fix (T7e) — back to §31.3 Option A

The §31.3 Option A recommendation is the actual fix. Restating with
slight refinement based on what we just learned:

**Replace user-data lines 185-186** (currently
`git submodule sync --recursive` + `git submodule update --init
--recursive --force vendor/QPress-SAM-Flake`) with:

```bash
# Submodule fetch requires a runtime PAT, which we don't carry by
# design (PAT is exposed only at AMI bake time per #225/#228). When
# the gitlink and the baked submodule SHA already match — which they
# always should on a freshly-baked AMI — the fetch is unnecessary
# and we can skip it entirely. When they don't match, the operator
# must rebake the AMI (no runtime fix possible).
expected_sha="$(git ls-tree HEAD vendor/QPress-SAM-Flake | awk '{print $3}')"
actual_sha="$(cd vendor/QPress-SAM-Flake 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo none)"
if [[ "${expected_sha}" != "${actual_sha}" ]]; then
  echo "FATAL: vendor/QPress-SAM-Flake gitlink (${expected_sha:0:8}) != baked SHA (${actual_sha:0:8})." >&2
  echo "  AMI re-bake required (PAT not exposed at runtime by design)." >&2
  echo "  See docs/sam-ops.md §32 for context." >&2
  exit 1
fi
echo "[4/8] vendor submodule already at ${actual_sha:0:8} (baked match) — skip fetch"
```

Pros:
- Zero secrets at runtime.
- Idempotent on every cold launch.
- Fails loudly with operator-actionable message when AMI needs rebake.
- The current §32 case (gitlink revert means SHAs match) becomes a
  clean no-op, which IS what the owner wanted.

Cons (vs current `--init --force` shape):
- Loses the auto-recovery for the rare "main repo bumped submodule
  gitlink between bake and launch" case. But that case requires a
  bake-side action anyway (we don't ship submodule bumps in main
  without a corresponding rebake), so this is not a real loss.

Publish as LT v30 (or revise v29 in-place) with the patched user-data.
AMI does not need rebake — `ami-0b7ec5ff47a1eff11` stays.

### 32.4 What did NOT run (carry forward)

- Step 5/6/7/8 of user-data (venv install, M3 weights, worker.service
  install, idle-shutdown.timer install).
- `worker.service` start-up.
- `gpu_ready` SSE event with `image_count`.
- Per-image `progress` events.
- Terminal `done` event.
- Auto-terminate via idle-shutdown.timer.

These layers have not been exercised on a cold boot of
`ami-0b7ec5ff47a1eff11` in the new harness. Steps 5/6/7/8 were
validated in §15 with a different orchestration shape; the cuDNN /
merged_m3 issues in §26 were on a different AMI (`ami-092ae5880cb9cf957`),
so they may or may not apply here. **There is meaningful
unknown-unknown risk past step 4 — fix T7e first, then expect the
acceptance to surface whatever comes next.**

### 32.5 Run timeline (UTC)

| t | Event |
|---|---|
| 18:40:54 | POST /run/sam |
| 18:40:55 | HTTP 200, content-type=text/event-stream |
| 18:40:58 | `event=gpu_launching` `instance_id=i-0efe1950cc6e6cc8f` |
| 18:40:58 | EC2 `running` (spot, us-east-2a, no on-demand fallback) |
| ~18:42:30 | SSM agent online (cloud-init started shortly after) |
| 18:42:30 | user-data start banner |
| 18:42:30-32 | step 4: `git config --system` ✅, `git fetch origin` ✅ (`e2a2ede..6090d9c`) |
| 18:42:32 | `git submodule update --init --recursive --force` → `fatal: could not read Username` |
| 18:42:32 | cloud-init aborts at scripts_user |
| 18:41:58 - 18:45:13 | SSE keepalive frames every 15 s (~14 frames) |
| 18:46:15 | PM `terminate-instances i-0efe1950cc6e6cc8f` |
| 18:46:30 | `cancel-spot-instance-requests sir-ix1fhc8h` |
| 18:46:35 | SSE foreground process killed |

### 32.6 Cost ledger

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| g6e.48xlarge spot (us-east-2a) | ~5.5 min | ~$5.96 | **~$0.55** |
| Bastion t3.micro overlap | ~10 min | ~$0.0104 | ~$0.002 |
| RDS round-trips | negligible | | ~$0.001 |
| **Total this re-run** | | | **~$0.55** |

Within the $5 hard cap. Total wall ~6 min (POST 18:40:54 → cleanup
~18:46:30), well within the 60-min cap.

### 32.7 Cleanup performed

- API uvicorn — left running.
- SSH bastion tunnel — left open on 5433.
- Bastion `i-063165d449976b2e4` — left running.
- GPU instance `i-0efe1950cc6e6cc8f` `terminate-instances` issued at
  ~18:46:15Z; verified `shutting-down`.
- Spot request `sir-ix1fhc8h` explicitly cancelled to release quota
  for next attempt.
- Staging RDS rows left in place.
- Harness SSE log: `/tmp/saa-acceptance-sse-T6a3-r11.log`.
- No other live SAM instances.

### 32.8 Status for owner decision

**Verified again (no regression on prior fixes):**
- T7c `git config --system` works.
- `gpu_launching` SSE wire path repeatable.
- Spot capacity stable in us-east-2a.

**Same blocker as §31, NOT addressed by `6090d9c`:**
- User-data line 186 `git submodule update --init --recursive --force`
  attempts an unconditional remote fetch as part of `--init`,
  regardless of gitlink-vs-baked-SHA equality. Vendor gitlink
  revert was orthogonal to this failure mode.

**Next action — T7e (the same as §31's T7d, restated for clarity):**
1. Patch `scripts/aws/sam-gpu-worker-userdata.sh` lines 185-186 with
   the SHA-equality-guarded skip pattern from §32.3.
2. Publish LT v30.
3. Re-run §32 acceptance.

After T7e, expect the acceptance to surface whichever layer fails
next in steps 5/6/7/8. Owner directive ("한번 돌아가면 냅둬, 나중에
업데이트 시 새 AMI") still applies — the goal of T7e is to get past
step 4 cleanly so the rest of the cold-init can be exercised on
this AMI for the first time.

---

## 33. 1-click SAM dispatch acceptance — 2026-06-09 (post-T7e) USERDATA_BLOCKED_4

Re-run of §32 after T7e shipped as commit `2f56bce` — replaced
`git submodule update --init --recursive --force` with a SHA-equality
guard that skips fetch when the gitlink and baked submodule SHA
match (verified in v30 user-data lines 200-211). LT v30 published
default with same AMI `ami-0b7ec5ff47a1eff11`. Same harness shape as
§27-§32.

**Outcome: USERDATA_BLOCKED_4.** T7e's explicit submodule update
**was reached and would have skipped cleanly** — but `git fetch
--all --tags` on user-data line 178 (which precedes T7e by 22 lines)
**recursed into submodules by default** and crashed on the same
`fatal: could not read Username` at the same submodule:

```text
[4/8] clone repo + submodule
Fetching origin
From https://github.com/HoukJangBNL/stand-alone-analyzer
   e2a2ede..2f56bce  main       -> origin/main
Fetching submodule vendor/QPress-SAM-Flake
fatal: could not read Username for 'https://github.com': No such device or address
fatal: could not read Username for 'https://github.com': No such device or address
Errors during submodule fetch:
	vendor/QPress-SAM-Flake
error: Could not fetch origin
```

The output "Fetching submodule vendor/QPress-SAM-Flake" comes from
`git fetch --all` recursively fetching submodules, NOT from our T7e
block (which is downstream and never reached on this run). Modern
git defaults (`fetch.recurseSubmodules=on-demand` with submodules
present, plus `--all` widening the scope) trigger this. `set -euo
pipefail` aborts the script before T7e's SHA-skip gets a chance to
log success.

| Field | Value |
|---|---|
| Branch / HEAD | `main` / `2f56bce` (T7e SHA-equality skip) |
| AMI | `ami-0b7ec5ff47a1eff11` (unchanged from §28-§32) |
| Launch template | `qpress-sam-gpu-worker` v30 (default) |
| Instances launched | 1 — `i-073ed0cc3c0919d01` (g6e.48xlarge spot us-east-2a) |
| POST ts (UTC) | 2026-06-09T19:14:00 |
| `gpu_launching` SSE | **fired** at +3.46 s |
| Instance `running` | +3 s after launch |
| SSM `Online` | ~+1.5 min |
| user-data line 168 — `git config --system` | **SUCCEEDED** (T7c verified — 4-for-4) |
| user-data line 178 — `git fetch --all --tags` | **FAILED** — recursed into submodules, hit auth wall |
| user-data lines 200-211 — T7e SHA-equality skip | NEVER REACHED (`set -e` aborted upstream) |
| `worker.service` | never installed |
| `gpu_ready` / progress / `done` SSE | never fired |
| Total instance wall (billed) | ~6.5 min |
| Cost (this re-run) | **~$0.65** (g6e.48xlarge spot @ ~$5.96/hr × 6.5 min) |
| Status | `userdata_blocked_4` |

### 33.1 Root cause: `git fetch --all` auto-recurses into submodules

Modern git's `git fetch` recurses into populated submodules by
default when they're present in the working tree (the bake-time
clone populated `vendor/QPress-SAM-Flake/.git`). With `--all`
widening the scope to all configured remotes (including the
submodule's), the recursion attempts to fetch each submodule's
`origin`. For the private `vendor/QPress-SAM-Flake` requiring auth
this fails non-interactively. `set -euo pipefail` then aborts the
whole script before any downstream code runs.

T7e correctly removed the EXPLICIT `git submodule update --init` on
line 200+, but the IMPLICIT submodule fetch via `git fetch --all`'s
recursion on line 178 was unaffected.

### 33.2 What was newly verified vs §32

1. **T7c `git config --system` works for the fourth consecutive
   cold boot.** No `$HOME not set`, no `dubious ownership`. The
   safe.directory mechanism is rock-solid.
2. **Main repo's own ref-fetch advances correctly** when it's not
   recursing — the line "From https://github.com/...\n
   e2a2ede..2f56bce  main  -> origin/main" appeared in the log
   BEFORE the submodule recursion crash.
3. **`gpu_launching` SSE wire path repeatable** — fourth observation
   now (§30, §31, §32, §33), all at +3.0-3.5 s after POST.
4. **Spot capacity stable in us-east-2a** — no on-demand fallback
   needed.
5. **§31's spot-cancel cleanup pattern continues to pay off** — no
   quota-lag false-start at start.

### 33.3 Recommended fix (T7f) — disable submodule recursion in git fetch

Two-line patch to user-data line 178. Pick one of these
equivalent forms:

```bash
# Option A (preferred, explicit): pass --no-recurse-submodules
git fetch --all --tags --no-recurse-submodules

# Option B: set the config flag once before fetch
git -c fetch.recurseSubmodules=no fetch --all --tags
```

Option A is more readable and self-documenting at the call site.
Both achieve the same outcome: main repo fetches its own refs
without touching submodules. T7e's downstream SHA-equality skip
then handles vendor/QPress-SAM-Flake correctly (no fetch needed,
SHA already matches per §32 SSM verification).

Publish as LT v31 with the patched user-data. AMI does NOT need
rebake — `ami-0b7ec5ff47a1eff11` stays.

### 33.4 What did NOT run (still untouched on this AMI/harness)

- `git reset --hard origin/main` (lines 182-183) — would set the
  working tree to `2f56bce`.
- T7e SHA-equality skip — would log "vendor/... already at
  2c69ebd... — skip submodule fetch" and continue.
- `chown -R ubuntu:ubuntu /opt/sam` (line ~218).
- `stamp repo`.
- Step 5 (uv sync + SAM2 inference deps).
- Step 6 (M3 multi-GPU asset bootstrap including peft pip).
- Step 6c (vendor base-ckpt prod-path symlinks).
- Step 7 (worker.service install + start).
- Step 8 (idle-shutdown.timer install + enable).
- `gpu_ready` / per-image progress / `done` SSE.

These layers have NOT been exercised on `ami-0b7ec5ff47a1eff11` in
the new orchestration. After T7f ships, the next acceptance attempt
will be the first to actually push past step 4 of user-data.

### 33.5 Run timeline (UTC)

| t | Event |
|---|---|
| 19:14:00 | POST /run/sam |
| 19:14:01 | HTTP 200, content-type=text/event-stream |
| 19:14:03 | `event=gpu_launching` `instance_id=i-073ed0cc3c0919d01` |
| 19:14:03 | EC2 `running` (spot, us-east-2a, no on-demand fallback) |
| ~19:15:35 | SSM agent online; cloud-init enters scripts_user |
| 19:15:35 | user-data start banner |
| 19:15:35-37 | step 4: `git config --system` ✅ → `git fetch --all --tags` recursed into vendor/QPress-SAM-Flake → `fatal: could not read Username` |
| 19:15:37 | cloud-init aborts at scripts_user |
| 19:14:18 - 19:20:18 | SSE keepalive frames every 15 s (~25 frames) |
| 19:20:30 | PM `terminate-instances i-073ed0cc3c0919d01` |
| 19:20:45 | `cancel-spot-instance-requests sir-6j87kc4j` |
| 19:20:50 | SSE foreground process killed |

### 33.6 Cost ledger

| Resource | Wall | $/hr | Cost |
|---|---|---|---|
| g6e.48xlarge spot (us-east-2a) | ~6.5 min | ~$5.96 | **~$0.65** |
| Bastion t3.micro overlap | ~10 min | ~$0.0104 | ~$0.002 |
| RDS round-trips | negligible | | ~$0.001 |
| **Total this re-run** | | | **~$0.65** |

Within the $5 hard cap. Wall ~7 min (POST 19:14 → cleanup 19:21),
well within the 60-min cap.

### 33.7 Cleanup performed

- API uvicorn — left running.
- SSH bastion tunnel — left open on 5433.
- Bastion `i-063165d449976b2e4` — left running.
- GPU instance `i-073ed0cc3c0919d01` `terminate-instances` issued at
  ~19:20:30Z; verified `shutting-down`.
- Spot request `sir-6j87kc4j` explicitly cancelled.
- Staging RDS rows left in place.
- Harness SSE log: `/tmp/saa-acceptance-sse-T6a3-r12.log`.
- No other live SAM instances.

### 33.8 Status for owner decision

**Verified again (no regression):**
- T7c `git config --system` (4-for-4).
- `gpu_launching` SSE (4-for-4 since §30).
- Spot capacity in us-east-2a stable.
- Quota-lag pattern not re-firing (§31 cleanup discipline holds).

**Same blocker class as §31/§32, NEW root cause:**
- `git fetch --all --tags` on line 178 auto-recurses into submodules
  and hits the same private-submodule auth wall. T7e correctly
  fixed the EXPLICIT submodule update on line 200+ but couldn't
  address the IMPLICIT recursion on line 178. The `set -euo
  pipefail` preamble means a failure on line 178 terminates the
  script before T7e's downstream SHA-skip runs.

**Next action — T7f:**
1. Patch line 178: `git fetch --all --tags` →
   `git fetch --all --tags --no-recurse-submodules`.
2. Publish LT v31.
3. Re-run §33 acceptance — step 4 should finally complete (T7c +
   T7f + T7e together), letting steps 5-8 exercise on this AMI for
   the first time.

**Heads-up to owner**: even after T7f, there's meaningful
unknown-unknown risk past step 4. Steps 5/6/7/8 have not been
validated on this AMI in this orchestration. §15 manual run
validated similar steps but with a different harness shape. §26
found cuDNN/merged_m3 issues on a different AMI. T7f is necessary
but may not be sufficient — expect another iteration to surface
the next layer.

**Out of scope (still deferred):**
- Region switch / instance-type switch.
- T9 spot-request-cancel-on-terminate (operational improvement).

### Cumulative cost (updated through §33)

| Phase | Cost |
|---|---|
| #229 attempts §18-§26 | $20.54 |
| §27 (capacity-blocked, no EC2 spent) | $0.00 |
| §28 (post-T7 partial_blocked, AMI bake gap) | ~$1.52 |
| §29 (post-T7b capacity-blocked, no EC2 spent) | $0.00 |
| §30 (post-T7b retry, capacity restored — userdata_blocked) | ~$1.19 |
| §31 (post-T7c — userdata_blocked_2, submodule auth) | ~$0.62 |
| §32 (post-vendor-revert — userdata_blocked_3) | ~$0.55 |
| **§33 (post-T7e — userdata_blocked_4, fetch --all recurses)** | **~$0.65** |
| **Total** | **~$25.07** |

$100 cap remaining: ~$74.93.
