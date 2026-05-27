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
| Launch template           | `qpress-sam-gpu-worker`               | us-east-2     | Spot GPU worker template (g6e.xlarge, user-data)         | P4.4 (parallel agent)                     |
| EventBridge rule          | `qpress-sam-spot-interrupt-rule`      | us-east-2     | Catches `EC2 Spot Instance Interruption Warning`         | P4.4 (parallel agent)                     |
| SNS topic                 | `qpress-sam-spot-interrupt-notify`    | us-east-2     | Spot-interrupt audit fan-out                             | P4.4 (parallel agent)                     |
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
   `src/flake_analysis/api/services/sam_worker.py`) checks if any EC2 instance
   tagged `Project=qpress-sam,Role=worker` is in `running` or `pending` state
   in us-east-2. If not, it calls `RunInstances` against the
   `qpress-sam-gpu-worker` launch template.
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
   event to the default event bus. Our rule
   `qpress-sam-spot-interrupt-rule` (P4.4) matches it, scoped to instances
   with tag `Project=qpress-sam`, and fans out to SNS topic
   `qpress-sam-spot-interrupt-notify`. **This SNS topic is for audit only**
   — no email subscription, just a CloudWatch dashboard / log store target.
3. **In-instance handler** — a small daemon on the worker
   (`/opt/sam/spot-handler.py`, P4.4) polls IMDS every 5 s. On notice it:
   - sends `SIGTERM` to the procrastinate worker
   - the worker's signal handler runs the current job's `record_run_end`
     with `status='failed'`, `error='spot_interrupted'`
   - flushes any open DB sessions
   - exits cleanly so EBS doesn't go corrupt
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
