#!/usr/bin/env bash
# Hard self-terminate the current EC2 instance.
#
# Triggered by flake-analysis-abs-cap.timer (OnBootSec=ABS_CAP_MIN minutes
# after boot). Ensures runaway operator sessions can't bleed an idle
# on-demand instance — see docs/sam-ops.md §20 (#229 retry2: 53 min idle
# at $7.23/hr = $7.09 lost).
#
# Defends against multiple failure modes:
#   * Operator session dies / network drops
#   * scripts/sam/measure-run.sh crashes between launch and terminate
#   * SSM polling loop wedges
#
# Idempotent — calling terminate-instances on an already-terminating
# instance is a no-op.

set -euo pipefail

TOKEN=$(curl -fsS -X PUT \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" \
    http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl -fsS \
    -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -fsS \
    -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/placement/region)

logger -t abs-cap "ABS_CAP fired — terminating $INSTANCE_ID in $REGION"
aws ec2 terminate-instances \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION"
