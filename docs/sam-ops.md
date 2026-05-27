# SAM2 GPU Operations Runbook

> **Status (2026-05-27):** P4.3 in flight — this doc starts here and is finished
> in P4.5. Sections marked `(P4.5)` are placeholders.

This runbook covers running the SAM2 + LoRA inference path on AWS GPU spot
instances (`g6e.xlarge`, `us-east-2`).

## 1. AWS resources owned by this stack

All resources live in `us-east-2` and are tagged `Project=qpress-sam` for cost
allocation (matches the $20/day CloudWatch alarm at P4.5).

| Resource | Name | Notes |
|---|---|---|
| IAM role | `qpress-sam-gpu-role` | Trust: `ec2.amazonaws.com`. Inline policy `qpress-sam-gpu-s3` + AWS managed `AmazonSSMManagedInstanceCore`. |
| IAM instance profile | `qpress-sam-gpu-role` | Same name as the role. Attached to GPU EC2 launches. |
| Inline policy | `qpress-sam-gpu-s3` | `s3:GetObject` + `s3:PutObject` + `s3:PutObjectAcl` on `arn:aws:s3:::qpress-uploads/internal/sam/*`; `s3:ListBucket` on bucket scoped to `internal/sam/` prefix. |
| Security group | `qpress-sam-gpu-sg` | VPC = same as bastion (`vpc-053a4df895c279c84`). No ingress. Egress 443/HTTPS to anywhere (S3, package mirrors, GitHub). SSM-only access. |
| S3 prefix | `s3://qpress-uploads/internal/sam/` | Stores `lora-source/best_model.pth` (input) + `sam2.1_hiera_large.merged.<sha8>.pt` (output) + `.sha256` sidecar files. |

> **PutObject note:** the role currently grants PutObject on `internal/sam/*`
> so the bootstrap instance can upload merged weights. Production worker
> instances (P4.4) only need GetObject. We can tighten this in P4.5 by either
> (a) splitting into two roles, or (b) keeping a single role and accepting
> the small blast radius (worker boxes are short-lived spot, no human SSH).
> Decision deferred to P4.5.

## 2. One-time setup (P4.3)

### 2.1 Stage prod LoRA to S3 (owner-driven, run once)

The bootstrap instance pulls the LoRA adapter from S3. The adapter currently
lives on `qpress@hal.cfn.bnl.gov:~/sam2_lora/best_model.pth`.

```bash
# On owner's laptop, with best_model.pth pulled from hal:
./scripts/aws/sam-stage-lora-to-s3.sh /path/to/best_model.pth
```

Expected: ~few hundred MiB upload to
`s3://qpress-uploads/internal/sam/lora-source/best_model.pth`.

### 2.2 Create IAM role + SG (one-time, idempotent)

See `scripts/aws/sam-iam-bootstrap.sh` (creates role, policy, instance profile,
security group). Re-running is safe; it skips already-existing resources.

### 2.3 Launch bootstrap instance + capture merged weights

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

## 3. Operating production GPU workers (P4.4)

_(P4.5 — to be filled in by the EC2 launch flow + spot-interrupt handler work
in P4.4 and the e2e/runbook polish in P4.5.)_

## 4. Cost & alarms (P4.5)

_(P4.5 — CloudWatch alarm on Project=qpress-sam tag at $20/day threshold, SNS
to owner email.)_

## 5. Troubleshooting

### 5.1 `sam-gpu-bootstrap.sh` fails on CUDA install

Check `/var/log/sam-gpu-bootstrap.log`. Common cause: NVIDIA apt repo network
flakiness — the script is idempotent; SSM into the instance and re-run:

```bash
sudo bash /var/lib/cloud/instance/scripts/part-001
```

The state stamps in `/opt/sam/state/` skip already-completed steps.

### 5.2 LoRA download fails with `AccessDenied`

Verify the instance role:

```bash
aws sts get-caller-identity   # on the instance
# Should show ARN ending in qpress-sam-gpu-role
```

If wrong role, the launch command was missing `--iam-instance-profile`.

### 5.3 merge_lora.py errors with "missing matching lora_B"

Indicates a peft prefix mismatch — P1.5b's recursive prefix strip should
handle `image_encoder.base_model.model.trunk.*`. If this resurfaces, check
that the submodule is at SHA `6f7fc2e` or later:

```bash
cd /opt/sam/stand-alone-analyzer
git -C vendor/QPress-SAM-Flake rev-parse HEAD
```

### 5.4 SHA256 in S3 doesn't match local

Re-upload — the `.sha256` sidecar is computed from the local merged.pt right
before upload, so a mismatch means the S3 PUT corrupted in-flight (rare with
multipart). Re-running the upload step (delete the `upload.done` stamp) will
recompute and re-upload.
