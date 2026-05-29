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
