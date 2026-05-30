#!/usr/bin/env bash
# measure-run.sh — operator-facing one-shot for SAM 8-GPU measurement runs.
#
# Phases:
#   1. Precheck (AWS profile, region)
#   2. Args parse + validation
#   3. LT publish (sam-launch-template.sh with IMAGE_ID
#      ami-092ae5880cb9cf957)
#   4. Spot launch with on-demand auto-fallback (mirrors sam-bake-ami.sh)
#   5. SSM wait online (records boot_s = SSM_online - launch_ts)
#   6. Pre-flight on instance (8 GPUs, vendor path, worker env file,
#      worker PID alive)
#   7. Push measure-defer.py via SSM, capture JOB_ID from stdout
#   8. Polling-and-act loop (30 s tick, max --wall-cap-min)
#      * On every tick: project cost vs --cost-cap-usd, abort if exceeded
#      * On every tick: query procrastinate_jobs.status
#   9. On success: SSM pull per_image_results.json + worker_events SQL
#  10. Compute & print boot_s / model_load_s / processing_s / total_s
#  11. Always: terminate-instances (trap EXIT)
#
# Belt-and-suspenders: instance-side abs-cap.timer self-terminates at
# T+ABS_CAP_MIN unconditionally, regardless of this script's state.
#
# Usage:
#   ./scripts/sam/measure-run.sh \
#     --weights s3://qpress-uploads/internal/sam/merged_m3/...pt \
#     --dataset s3://qpress-uploads/internal/sam/scan6-100/ \
#     [--instance-type g6e.48xlarge] \
#     [--cost-cap-usd 5] \
#     [--wall-cap-min 60] \
#     [--ami-id ami-092ae5880cb9cf957] \
#     [--dryrun]

set -euo pipefail

# ------- defaults -------
INSTANCE_TYPE="g6e.48xlarge"
COST_CAP_USD="5"
WALL_CAP_MIN="60"
AMI_ID="ami-092ae5880cb9cf957"
AWS_PROFILE="${AWS_PROFILE:-qpress}"
AWS_REGION="${AWS_REGION:-us-east-2}"
RUN_ID_DEFAULT="$(date -u +%s)"
RUN_ID="${RUN_ID:-${RUN_ID_DEFAULT}}"
SCAN_ID="${SCAN_ID:-0}"
DRYRUN=0

WEIGHTS=""
DATASET=""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ------- args -------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --weights) WEIGHTS="$2"; shift 2;;
        --dataset) DATASET="$2"; shift 2;;
        --instance-type) INSTANCE_TYPE="$2"; shift 2;;
        --cost-cap-usd) COST_CAP_USD="$2"; shift 2;;
        --wall-cap-min) WALL_CAP_MIN="$2"; shift 2;;
        --ami-id) AMI_ID="$2"; shift 2;;
        --dryrun) DRYRUN=1; shift 1;;
        -h|--help)
            sed -n '/^# Usage:/,/^$/p' "$0" >&2
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 2;;
    esac
done

[[ -n "$WEIGHTS" ]] || { echo "missing --weights" >&2; exit 2; }
[[ -n "$DATASET" ]] || { echo "missing --dataset" >&2; exit 2; }

# ------- helpers -------
log() { echo "[phase=$1] $2"; }
aws_q() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

INSTANCE_ID=""
LAUNCH_TS_EPOCH=""

# shellcheck disable=SC2329  # invoked indirectly via trap EXIT
terminate_now() {
    local reason="${1:-EXIT trap}"
    if [[ -z "$INSTANCE_ID" ]]; then
        log 11 "no instance to terminate ($reason)"
        return 0
    fi
    if (( DRYRUN )); then
        log 11 "Would: aws ec2 terminate-instances --instance-ids $INSTANCE_ID  ($reason)"
        return 0
    fi
    log 11 "terminating $INSTANCE_ID ($reason)"
    aws_q ec2 terminate-instances --instance-ids "$INSTANCE_ID" || true
}
trap 'terminate_now "trap EXIT"' EXIT

# ------- phase 1 -------
log 1 "precheck — profile=$AWS_PROFILE region=$AWS_REGION"
if (( ! DRYRUN )); then
    aws_q sts get-caller-identity > /dev/null
fi

# ------- phase 2 -------
log 2 "args — weights=$WEIGHTS dataset=$DATASET instance=$INSTANCE_TYPE cap=\$$COST_CAP_USD wall=${WALL_CAP_MIN}m ami=$AMI_ID dryrun=$DRYRUN"

# ------- phase 3 -------
log 3 "publish LT (IMAGE_ID=$AMI_ID, INSTANCE_TYPE=$INSTANCE_TYPE)"
if (( ! DRYRUN )); then
    INSTANCE_TYPE="$INSTANCE_TYPE" IMAGE_ID_OVERRIDE="$AMI_ID" \
        bash "$REPO_ROOT/scripts/aws/sam-launch-template.sh"
fi

# ------- phase 4 -------
log 4 "spot launch (with on-demand fallback)"
if (( DRYRUN )); then
    log 4 "Would: aws ec2 run-instances --launch-template Name=qpress-sam-gpu-worker --instance-type $INSTANCE_TYPE"
    INSTANCE_ID="i-DRYRUNXXXXXXXXXXX"
    LAUNCH_TS_EPOCH="$(date -u +%s)"
else
    LAUNCH_TS_EPOCH="$(date -u +%s)"
    if ! INSTANCE_ID=$(aws_q ec2 run-instances \
            --launch-template "LaunchTemplateName=qpress-sam-gpu-worker,Version=\$Default" \
            --instance-type "$INSTANCE_TYPE" \
            --instance-market-options "MarketType=spot" \
            --tag-specifications "ResourceType=instance,Tags=[{Key=Purpose,Value=measure-run-${RUN_ID}}]" \
            --query "Instances[0].InstanceId" --output text 2>/dev/null); then
        log 4 "spot capacity drought → on-demand fallback"
        INSTANCE_ID=$(aws_q ec2 run-instances \
            --launch-template "LaunchTemplateName=qpress-sam-gpu-worker,Version=\$Default" \
            --instance-type "$INSTANCE_TYPE" \
            --tag-specifications "ResourceType=instance,Tags=[{Key=Purpose,Value=measure-run-${RUN_ID}-ondemand}]" \
            --query "Instances[0].InstanceId" --output text)
    fi
    log 4 "instance=$INSTANCE_ID launch_ts=$LAUNCH_TS_EPOCH"
fi

# ------- phase 5 -------
log 5 "wait SSM online"
if (( DRYRUN )); then
    SSM_ONLINE_TS_EPOCH="$(date -u +%s)"
    BOOT_S=70
    log 5 "Would: poll describe-instance-information until PingStatus=Online"
else
    while :; do
        ping_status=$(aws_q ssm describe-instance-information \
            --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
            --query "InstanceInformationList[0].PingStatus" \
            --output text 2>/dev/null || echo "None")
        [[ "$ping_status" == "Online" ]] && break
        sleep 15
    done
    SSM_ONLINE_TS_EPOCH="$(date -u +%s)"
    BOOT_S=$(( SSM_ONLINE_TS_EPOCH - LAUNCH_TS_EPOCH ))
    log 5 "ssm online — boot_s=${BOOT_S}"
fi

# ------- phase 6 -------
log 6 "pre-flight"
if (( DRYRUN )); then
    log 6 "Would: wait for user-data done, then SSM run nvidia-smi -L | wc -l == 8 etc"
else
    # Wait for cloud-init / user-data to finish before checking artifacts.
    # SSM-online (phase 5) only means ssm-agent registered — userdata may
    # still be installing CUDA, downloading weights (898 MB merged_m3),
    # building vendor, staging dataset (100 PNG / 284 MB), writing the
    # worker env file. Cold spot allocation observed ~16 min on
    # ami-092ae5880cb9cf957 (T13 attempt 2). Cap at 25 min to leave
    # headroom; future AMI re-bake with pre-staged .venv + dataset
    # would shrink this to ~2 min.
    PREFLIGHT_WAIT_MIN="${PREFLIGHT_WAIT_MIN:-25}"
    log 6 "wait for user-data completion (max ${PREFLIGHT_WAIT_MIN}m)"
    pf_deadline=$(( $(date -u +%s) + PREFLIGHT_WAIT_MIN * 60 ))
    while :; do
        if (( $(date -u +%s) >= pf_deadline )); then
            echo "pre-flight fail: user-data did not finish within 15 min" >&2
            exit 3
        fi
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --document-name AWS-RunShellScript \
            --parameters 'commands=["test -f /var/lib/cloud/instance/boot-finished && echo BOOT_FINISHED","test -f /etc/flake-analysis-worker.env && echo ENV_PRESENT","systemctl is-active flake-analysis-worker.service || true"]' \
            --query "Command.CommandId" --output text)
        sleep 10
        out=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "StandardOutputContent" --output text 2>/dev/null || echo "")
        if grep -q "^BOOT_FINISHED$" <<< "$out" \
                && grep -q "^ENV_PRESENT$" <<< "$out" \
                && grep -q "^active$" <<< "$out"; then
            log 6 "user-data done — worker active"
            break
        fi
        log 6 "still booting (boot=$(grep -c BOOT_FINISHED <<<"$out") env=$(grep -c ENV_PRESENT <<<"$out") active=$(grep -c "^active$" <<<"$out"))"
        sleep 20
    done

    # Now run the actual artifact checks.
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters 'commands=["nvidia-smi -L | wc -l","ls /opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/run_amg_v2.py","ls /etc/flake-analysis-worker.env","pgrep -f flake_analysis.worker | head -1","ls /opt/sam/dataset/scan6-100 | wc -l"]' \
        --query "Command.CommandId" --output text)
    sleep 5
    out=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text)
    grep -q "^8$" <<< "$out" || { echo "pre-flight fail: not 8 GPUs visible" >&2; echo "$out" >&2; exit 3; }
    grep -q "run_amg_v2.py" <<< "$out" || { echo "pre-flight fail: vendor not present" >&2; echo "$out" >&2; exit 3; }
    grep -q "flake-analysis-worker.env" <<< "$out" || { echo "pre-flight fail: worker env missing" >&2; echo "$out" >&2; exit 3; }
    grep -q "^100$" <<< "$out" || { echo "pre-flight fail: dataset count != 100" >&2; echo "$out" >&2; exit 3; }
fi

# ------- phase 7 -------
log 7 "push defer launcher + run"
if (( DRYRUN )); then
    JOB_ID="DRYRUN-job"
    log 7 "Would: scp measure-defer.py via SSM + run with --weights-uri $WEIGHTS"
else
    payload_b64=$(base64 < "$REPO_ROOT/scripts/sam/measure-defer.py")
    # shellcheck disable=SC2016  # $DATASET expands locally inside $(basename ...)
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"echo $payload_b64 | base64 -d > /tmp/measure-defer.py\",\"chmod +x /tmp/measure-defer.py\",\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-defer.py --weights-uri '$WEIGHTS' --dataset-dir /opt/sam/dataset/$(basename '$DATASET' | tr -d '/') --analysis-folder /opt/sam/runs/$RUN_ID --run-id $RUN_ID --scan-id $SCAN_ID\"]" \
        --query "Command.CommandId" --output text)
    # Poll the SSM command Status until it leaves InProgress. Cold-start
    # measure-defer.py (uv venv import + RDS connect + procrastinate
    # enqueue) takes 12–25s; a fixed sleep raced and read empty stdout
    # while the command was still InProgress (T13 attempt 5).
    DEFER_WAIT_S=180
    defer_deadline=$(( $(date -u +%s) + DEFER_WAIT_S ))
    while :; do
        if (( $(date -u +%s) >= defer_deadline )); then
            echo "defer poll timed out after ${DEFER_WAIT_S}s" >&2
            exit 4
        fi
        sleep 5
        invo=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "[Status,StandardOutputContent,StandardErrorContent]" \
            --output text 2>/dev/null || echo "Pending				")
        defer_status=$(awk -F'\t' 'NR==1 {print $1}' <<< "$invo")
        case "$defer_status" in
            InProgress|Pending|Delayed) continue;;
            Success) break;;
            Failed|Cancelled|TimedOut)
                echo "defer command status=$defer_status" >&2
                echo "$invo" >&2
                exit 4
                ;;
        esac
    done
    out=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text)
    JOB_ID=$(grep -oE 'job_id=[0-9]+' <<< "$out" | head -1 | cut -d= -f2 || true)
    [[ -n "$JOB_ID" ]] || { echo "defer success but no job_id in stdout: $out" >&2; exit 4; }
    log 7 "deferred job_id=$JOB_ID"
fi

# ------- phase 8 -------
log 8 "polling loop (tick=30s, wall_cap=${WALL_CAP_MIN}m, cost_cap=\$$COST_CAP_USD)"
deadline=$(( LAUNCH_TS_EPOCH + WALL_CAP_MIN * 60 ))
hourly_rate_on_demand=7.23
status="unknown"
if (( DRYRUN )); then
    log 8 "Would: poll procrastinate_jobs.status WHERE id=$JOB_ID"
    status="succeeded"
else
    while :; do
        now=$(date -u +%s)
        if (( now >= deadline )); then
            log 8 "wall-cap exceeded (${WALL_CAP_MIN}m)"
            status="wall_cap_exceeded"
            break
        fi
        elapsed_s=$(( now - LAUNCH_TS_EPOCH ))
        proj_cost=$(awk -v s="$elapsed_s" -v r="$hourly_rate_on_demand" \
                        'BEGIN { printf "%.2f", s/3600.0*r }')
        if awk -v p="$proj_cost" -v c="$COST_CAP_USD" \
               'BEGIN { exit !(p > c) }'; then
            log 8 "cost-cap exceeded (\$$proj_cost > \$$COST_CAP_USD)"
            status="cost_cap_exceeded"
            break
        fi
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --document-name AWS-RunShellScript \
            --parameters "commands=[\"sudo bash -c 'set -a; . /etc/flake-analysis-worker.env; set +a; PGPASSWORD=\\\"\\\$SAA_DB_PASSWORD\\\" psql -h \\\$SAA_DB_HOST -p \\\$SAA_DB_PORT -U \\\$SAA_DB_USER -d \\\$SAA_DB_NAME -tAc \\\"SELECT status FROM procrastinate_jobs WHERE id=$JOB_ID\\\"'\"]" \
            --query "Command.CommandId" --output text)
        sleep 5
        s=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "StandardOutputContent" --output text 2>/dev/null | tr -d '[:space:]')
        log 8 "elapsed=${elapsed_s}s proj_cost=\$$proj_cost status=$s"
        case "$s" in
            succeeded) status="succeeded"; break;;
            failed)    status="failed"; break;;
        esac
        sleep 25
    done
fi
log 8 "loop exit status=$status"

# ------- phase 9 + 10 -------
log 9 "collect"
if [[ "$status" == "succeeded" && $DRYRUN -eq 0 ]]; then
    mkdir -p "claudedocs/measurement-${RUN_ID}"
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"cat /opt/sam/runs/${RUN_ID}/sam/per_image_results.json\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text \
        > "claudedocs/measurement-${RUN_ID}/per_image_results.json"

    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"sudo bash -c 'set -a; . /etc/flake-analysis-worker.env; set +a; PGPASSWORD=\\\"\\\$SAA_DB_PASSWORD\\\" psql -h \\\$SAA_DB_HOST -p \\\$SAA_DB_PORT -U \\\$SAA_DB_USER -d \\\$SAA_DB_NAME -tAc \\\"SELECT extract(epoch from ts) as ts_epoch, event, payload FROM worker_events WHERE run_id=$RUN_ID ORDER BY ts\\\"'\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text \
        > "claudedocs/measurement-${RUN_ID}/worker_events.tsv"
fi

log 10 "compute timing breakdown"
if [[ "$status" == "succeeded" ]]; then
    if (( DRYRUN )); then
        boot_s=70; model_load_s=30; proc_s=30; total_s=130
    else
        boot_s="$BOOT_S"
        events_tsv="claudedocs/measurement-${RUN_ID}/worker_events.tsv"
        ts_load=$(awk -F'|' '$2 ~ /marker:model_load_start/ {print $1; exit}' "$events_tsv")
        ts_proc_start=$(awk -F'|' '$2 ~ /marker:processing_start/ {print $1; exit}' "$events_tsv")
        ts_proc_end=$(awk -F'|' '$2 ~ /marker:processing_end/ {print $1; exit}' "$events_tsv")
        ts_task_start=$(awk -F'|' '$2 ~ /sam_task_start/ {print $1; exit}' "$events_tsv")
        ts_task_end=$(awk -F'|' '$2 ~ /sam_task_end/ {print $1; exit}' "$events_tsv")
        model_load_s=$(awk -v a="$ts_proc_start" -v b="$ts_load" 'BEGIN { printf "%.1f", a-b }')
        proc_s=$(awk -v a="$ts_proc_end" -v b="$ts_proc_start" 'BEGIN { printf "%.1f", a-b }')
        total_s=$(awk -v a="$ts_task_end" -v b="$ts_task_start" 'BEGIN { printf "%.1f", a-b }')
    fi
    log 10 "boot_s=$boot_s model_load_s=$model_load_s processing_s=$proc_s total_s=$total_s"
    if (( ! DRYRUN )); then
        cat > "claudedocs/measurement-${RUN_ID}/summary.json" <<EOF
{
  "run_id": ${RUN_ID},
  "instance_id": "${INSTANCE_ID}",
  "instance_type": "${INSTANCE_TYPE}",
  "ami_id": "${AMI_ID}",
  "weights_uri": "${WEIGHTS}",
  "dataset_uri": "${DATASET}",
  "boot_s": ${boot_s},
  "model_load_s": ${model_load_s},
  "processing_s": ${proc_s},
  "total_s": ${total_s},
  "status": "${status}"
}
EOF
    fi
fi

log 11 "(terminate happens in trap EXIT)"
exit 0
