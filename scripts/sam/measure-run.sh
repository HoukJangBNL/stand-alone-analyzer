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
#     [--cancel-stale-jobs] \
#     [--dryrun]

set -euo pipefail

# ------- defaults -------
INSTANCE_TYPE=""  # empty = walk full ladder (via prod launcher.py); set via --instance-type to pin
# Instance type ladder (mirrors src/flake_analysis/worker/launcher.py T7q):
# g6e.48xlarge: 8 GPU ($5.96 spot / $7.23 OD)
# g6e.24xlarge: 4 GPU ($2.97 spot / $3.61 OD)
# g6e.12xlarge: 4 GPU ($1.86 spot / $2.52 OD)
# g6e.4xlarge:  1 GPU ($0.62 spot / $0.77 OD)
INSTANCE_TYPE_LADDER=("g6e.48xlarge" "g6e.24xlarge" "g6e.12xlarge" "g6e.4xlarge")
COST_CAP_USD="100"  # owner-approved budget envelope (2026-06-15)
WALL_CAP_MIN="200"  # covers 4-GPU case (184 min) + headroom; 8-GPU fits easily
AMI_ID="ami-0b7ec5ff47a1eff11"  # §43-verified working AMI (cu124, peft baked, vendor 2c69ebd)
AWS_PROFILE="${AWS_PROFILE:-qpress}"
AWS_REGION="${AWS_REGION:-us-east-2}"
RUN_ID_DEFAULT="$(date -u +%s)"
RUN_ID="${RUN_ID:-${RUN_ID_DEFAULT}}"
SCAN_ID="${SCAN_ID:-0}"
DRYRUN=0
EXPECTED_IMAGE_COUNT=""  # optional: if set, phase-6 asserts dataset count == this value
CANCEL_STALE_JOBS=0  # flag: if 1, delete all todo jobs from gpu queue before defer

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
        --expected-image-count) EXPECTED_IMAGE_COUNT="$2"; shift 2;;
        --cancel-stale-jobs) CANCEL_STALE_JOBS=1; shift 1;;
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

# Export AWS_PROFILE and AWS_REGION globally for all child processes.
export AWS_PROFILE
export AWS_REGION

# ------- phase 1 -------
log 1 "precheck — profile=$AWS_PROFILE region=$AWS_REGION"
if (( ! DRYRUN )); then
    aws_q sts get-caller-identity > /dev/null
fi

# ------- phase 2 -------
# If INSTANCE_TYPE empty, use ladder mode; otherwise pin to the specified type.
if [[ -z "$INSTANCE_TYPE" ]]; then
    INSTANCE_MODE="ladder (8/4/4/1 GPU)"
    # LT publish needs *some* instance type — use the first tier for the template.
    # Actual launch will override per-tier in phase 4.
    LT_INSTANCE_TYPE="${INSTANCE_TYPE_LADDER[0]}"
else
    INSTANCE_MODE="pinned ($INSTANCE_TYPE)"
    LT_INSTANCE_TYPE="$INSTANCE_TYPE"
fi
log 2 "args — weights=$WEIGHTS dataset=$DATASET instance=$INSTANCE_MODE cap=\$${COST_CAP_USD:-auto} wall=${WALL_CAP_MIN:-auto}m ami=$AMI_ID dryrun=$DRYRUN"

# ------- phase 3 -------
log 3 "publish LT (IMAGE_ID=$AMI_ID, INSTANCE_TYPE=$LT_INSTANCE_TYPE)"
if (( ! DRYRUN )); then
    # Extract dataset prefix from --dataset URI for DATASET_PFX_OVERRIDE.
    # Input: s3://bucket/prefix/ → Output: prefix/
    DATASET_PFX=$(echo "$DATASET" | sed -E 's|^s3://[^/]+/||')
    # Arm instance-side abs-cap timer with +10 min headroom over wall-cap
    # for phase-9 collect before self-terminate.
    ABS_CAP_FOR_INSTANCE=$(( WALL_CAP_MIN + 10 ))
    log 3 "ABS_CAP_MIN=${ABS_CAP_FOR_INSTANCE} (wall_cap=${WALL_CAP_MIN} + 10 min headroom)"
    INSTANCE_TYPE="$LT_INSTANCE_TYPE" IMAGE_ID_OVERRIDE="$AMI_ID" \
        DATASET_PFX_OVERRIDE="$DATASET_PFX" \
        ABS_CAP_MIN="$ABS_CAP_FOR_INSTANCE" \
        bash "$REPO_ROOT/scripts/aws/sam-launch-template.sh"
fi

# ------- phase 4 -------
log 4 "instance-type ladder launch (prod launcher.py T7q via measure-launch.py)"
if (( DRYRUN )); then
    log 4 "Would: AWS_PROFILE=$AWS_PROFILE python3 $REPO_ROOT/scripts/sam/measure-launch.py"
    INSTANCE_ID="i-DRYRUNXXXXXXXXXXX"
    WON_INSTANCE_TYPE="${INSTANCE_TYPE_LADDER[0]}"
    WON_GPU_COUNT=8
    LAUNCH_TS_EPOCH="$(date -u +%s)"
else
    LAUNCH_TS_EPOCH="$(date -u +%s)"
    log 4 "calling prod launcher (full ladder: 8/4/4/1 GPU × 3 AZ × spot+OD = 24 attempts)"
    # NO timeout — let _launch_one run to natural completion (either win or 24-attempt exhaust).
    # PYTHONUNBUFFERED=1 ensures launcher INFO logs reach stderr immediately.
    launcher_output=$(AWS_PROFILE="$AWS_PROFILE" PYTHONUNBUFFERED=1 "$REPO_ROOT/.venv/bin/python3" "$REPO_ROOT/scripts/sam/measure-launch.py" 2>&1)
    launcher_exit=$?
    if (( launcher_exit != 0 )); then
        if [[ "$launcher_output" == *"CAPACITY_DROUGHT"* ]]; then
            echo "FATAL: GPU capacity unavailable across full prod ladder" >&2
            echo "$launcher_output" >&2
            exit 5
        else
            echo "FATAL: launcher failed (exit $launcher_exit)" >&2
            echo "$launcher_output" >&2
            exit 2
        fi
    fi
    # On success, stdout is the instance id (single line).
    INSTANCE_ID=$(echo "$launcher_output" | tail -1 | tr -d '[:space:]')
    if [[ ! "$INSTANCE_ID" =~ ^i-[0-9a-f]{8,17}$ ]]; then
        echo "FATAL: launcher returned invalid instance id: $INSTANCE_ID" >&2
        echo "Full output: $launcher_output" >&2
        exit 2
    fi
    log 4 "launched → $INSTANCE_ID"

    # Query AWS for won instance details.
    instance_info=$(aws_q ec2 describe-instances --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].[InstanceType,Placement.AvailabilityZone,InstanceLifecycle]' \
        --output text)
    WON_INSTANCE_TYPE=$(echo "$instance_info" | awk '{print $1}')
    WON_AZ=$(echo "$instance_info" | awk '{print $2}')
    lifecycle=$(echo "$instance_info" | awk '{print $3}')
    if [[ "$lifecycle" == "spot" ]]; then
        WON_MARKET="spot"
    else
        WON_MARKET="on-demand"
    fi

    # Map won instance type to GPU count.
    case "$WON_INSTANCE_TYPE" in
        g6e.48xlarge) WON_GPU_COUNT=8;;
        g6e.24xlarge) WON_GPU_COUNT=4;;
        g6e.12xlarge) WON_GPU_COUNT=4;;
        g6e.4xlarge)  WON_GPU_COUNT=1;;
        *) echo "FATAL: unknown instance type $WON_INSTANCE_TYPE" >&2; exit 2;;
    esac

    log 4 "won: ${WON_INSTANCE_TYPE} (${WON_GPU_COUNT} GPU) ${WON_MARKET} in ${WON_AZ}, launch_ts=$LAUNCH_TS_EPOCH"

    # Caps: use passed values or leave empty to trigger auto-derivation below.
    # Per owner directive: do NOT block on 1-GPU — let it run under the cap.
    if [[ -z "$WALL_CAP_MIN" ]] || [[ -z "$COST_CAP_USD" ]]; then
        log 4 "caps not explicitly set; will use passed values or fail if still empty"
    fi
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
            echo "pre-flight fail: user-data did not finish within ${PREFLIGHT_WAIT_MIN} min" >&2
            exit 3
        fi
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --timeout-seconds 60 \
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
    # Derive dataset dir basename from --dataset arg for dataset-agnostic checking.
    DATASET_BASENAME=$(basename "$DATASET" | tr -d '/')
    # Pre-construct full paths for use in SSM commands (so they expand locally).
    DATASET_DIR="/opt/sam/dataset/${DATASET_BASENAME}"
    ANALYSIS_DIR="/opt/sam/runs/${RUN_ID}"
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 60 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"nvidia-smi -L | wc -l\",\"ls /opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/run_amg_v2.py\",\"ls /etc/flake-analysis-worker.env\",\"pgrep -f flake_analysis.worker | head -1\",\"find $DATASET_DIR -maxdepth 1 -type f -name '*.png' | wc -l\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    out=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text)
    # GPU count check: tier-agnostic — verify actual count matches won tier.
    actual_gpu_count=$(head -1 <<< "$out" | tr -d '[:space:]')
    if [[ ! "$actual_gpu_count" =~ ^[0-9]+$ ]]; then
        echo "pre-flight fail: GPU count not a number (got: $actual_gpu_count)" >&2
        echo "$out" >&2
        exit 3
    fi
    if (( actual_gpu_count != WON_GPU_COUNT )); then
        echo "pre-flight fail: GPU count mismatch (nvidia-smi reports $actual_gpu_count, expected $WON_GPU_COUNT for $WON_INSTANCE_TYPE)" >&2
        echo "$out" >&2
        exit 3
    fi
    log 6 "GPU count verified: $actual_gpu_count GPU ($WON_INSTANCE_TYPE)"

    grep -q "run_amg_v2.py" <<< "$out" || { echo "pre-flight fail: vendor not present" >&2; echo "$out" >&2; exit 3; }
    grep -q "flake-analysis-worker.env" <<< "$out" || { echo "pre-flight fail: worker env missing" >&2; echo "$out" >&2; exit 3; }
    # Dataset count check: verify non-zero, and if --expected-image-count
    # provided, assert exact match (guards against partial S3 sync).
    dataset_count=$(tail -1 <<< "$out" | tr -d '[:space:]')
    if [[ ! "$dataset_count" =~ ^[0-9]+$ ]] || (( dataset_count <= 0 )); then
        echo "pre-flight fail: dataset count not positive integer (got: $dataset_count)" >&2
        echo "$out" >&2
        exit 3
    fi
    if [[ -n "$EXPECTED_IMAGE_COUNT" ]]; then
        if (( dataset_count != EXPECTED_IMAGE_COUNT )); then
            echo "pre-flight fail: dataset count mismatch (got $dataset_count, expected $EXPECTED_IMAGE_COUNT)" >&2
            echo "$out" >&2
            exit 3
        fi
        log 6 "dataset staged: $dataset_count images in $DATASET_DIR (matches expected count)"
    else
        log 6 "dataset staged: $dataset_count images in $DATASET_DIR"
    fi
fi

# ------- phase 7 -------
log 7 "push defer launcher + poll helper + run"
if (( DRYRUN )); then
    JOB_ID="DRYRUN-job"
    log 7 "Would: push measure-defer.py + measure-poll.py via SSM"
    if (( CANCEL_STALE_JOBS )); then
        log 7 "Would: cancel stale todo jobs on gpu queue"
    fi
else
    # Push measure-poll.py (used in phase 8 and optionally for stale-job cleanup here).
    poll_b64=$(base64 < "$REPO_ROOT/scripts/sam/measure-poll.py")
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 60 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"echo $poll_b64 | base64 -d > /tmp/measure-poll.py\",\"chmod +x /tmp/measure-poll.py\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    poll_push_status=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "Status" --output text 2>/dev/null || echo "Failed")
    if [[ "$poll_push_status" != "Success" ]]; then
        echo "measure-poll.py push failed (status=$poll_push_status)" >&2
        exit 4
    fi
    log 7 "measure-poll.py pushed"

    # If --cancel-stale-jobs is set, delete any todo jobs on the gpu queue
    # before deferring the new job (prevents worker from claiming orphans).
    if (( CANCEL_STALE_JOBS )); then
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --timeout-seconds 60 \
            --document-name AWS-RunShellScript \
            --parameters "commands=[\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-poll.py --cancel-stale-jobs\"]" \
            --query "Command.CommandId" --output text)
        sleep 5
        cancel_out=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "StandardOutputContent" --output text 2>/dev/null || echo "")
        if [[ "$cancel_out" == *"DB_ERROR"* ]]; then
            echo "cancel-stale-jobs failed: $cancel_out" >&2
            exit 4
        fi
        if [[ "$cancel_out" == "CANCELLED_JOBS=NONE" ]]; then
            log 7 "no stale todo jobs found (clean queue)"
        else
            log 7 "cancelled stale jobs: $cancel_out"
        fi
    fi

    # Now push and run measure-defer.py.
    payload_b64=$(base64 < "$REPO_ROOT/scripts/sam/measure-defer.py")
    # Use pre-constructed DATASET_DIR and ANALYSIS_DIR (computed in phase 6).
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 120 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"echo $payload_b64 | base64 -d > /tmp/measure-defer.py\",\"chmod +x /tmp/measure-defer.py\",\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-defer.py --weights-uri '$WEIGHTS' --dataset-dir '$DATASET_DIR' --analysis-folder '$ANALYSIS_DIR' --run-id $RUN_ID --scan-id $SCAN_ID\"]" \
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
    # Capture both stdout and stderr for better diagnostics on defer failure.
    invo=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "[StandardOutputContent,StandardErrorContent]" \
        --output text)
    out=$(awk -F'\t' 'NR==1 {print $1}' <<< "$invo")
    err=$(awk -F'\t' 'NR==1 {print $2}' <<< "$invo")
    JOB_ID=$(grep -oE 'job_id=[0-9]+' <<< "$out" | head -1 | cut -d= -f2 || true)
    if [[ -z "$JOB_ID" ]]; then
        echo "defer success but no job_id in stdout" >&2
        echo "stdout: $out" >&2
        echo "stderr: $err" >&2
        exit 4
    fi
    log 7 "deferred job_id=$JOB_ID"
fi

# ------- phase 8 -------
log 8 "polling loop (tick=30s, wall_cap=${WALL_CAP_MIN}m, cost_cap=\$$COST_CAP_USD)"
deadline=$(( LAUNCH_TS_EPOCH + WALL_CAP_MIN * 60 ))
# Map won instance type + market to actual hourly rate for cost projection.
case "$WON_INSTANCE_TYPE" in
    g6e.48xlarge)
        if [[ "$WON_MARKET" == "spot" ]]; then hourly_rate=5.96; else hourly_rate=7.23; fi
        ;;
    g6e.24xlarge)
        if [[ "$WON_MARKET" == "spot" ]]; then hourly_rate=2.97; else hourly_rate=3.61; fi
        ;;
    g6e.12xlarge)
        if [[ "$WON_MARKET" == "spot" ]]; then hourly_rate=1.86; else hourly_rate=2.52; fi
        ;;
    g6e.4xlarge)
        if [[ "$WON_MARKET" == "spot" ]]; then hourly_rate=0.62; else hourly_rate=0.77; fi
        ;;
    *)
        hourly_rate=7.23  # fallback to highest tier OD rate if unknown
        log 8 "WARNING: unknown instance type $WON_INSTANCE_TYPE, using fallback rate \$$hourly_rate/hr"
        ;;
esac
log 8 "cost projection: $WON_INSTANCE_TYPE $WON_MARKET = \$$hourly_rate/hr"
status="unknown"
if (( DRYRUN )); then
    log 8 "Would: poll procrastinate_jobs.status WHERE id=$JOB_ID via measure-poll.py"
    status="succeeded"
else
    db_error_streak=0  # count consecutive DB_ERROR ticks to abort on persistent failures
    while :; do
        now=$(date -u +%s)
        if (( now >= deadline )); then
            log 8 "wall-cap exceeded (${WALL_CAP_MIN}m)"
            status="wall_cap_exceeded"
            break
        fi
        elapsed_s=$(( now - LAUNCH_TS_EPOCH ))
        proj_cost=$(awk -v s="$elapsed_s" -v r="$hourly_rate" \
                        'BEGIN { printf "%.2f", s/3600.0*r }')
        if awk -v p="$proj_cost" -v c="$COST_CAP_USD" \
               'BEGIN { exit !(p > c) }'; then
            log 8 "cost-cap exceeded (\$$proj_cost > \$$COST_CAP_USD)"
            status="cost_cap_exceeded"
            break
        fi

        # Query job status via measure-poll.py (Python, reuses proven DB path).
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --timeout-seconds 60 \
            --document-name AWS-RunShellScript \
            --parameters "commands=[\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-poll.py --job-id $JOB_ID\"]" \
            --query "Command.CommandId" --output text)
        sleep 5

        # Capture SSM invocation status + stdout.
        invo=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "[Status,StandardOutputContent]" \
            --output text 2>/dev/null || echo "Failed	")
        ssm_status=$(awk -F'\t' 'NR==1 {print $1}' <<< "$invo")
        stdout=$(awk -F'\t' 'NR==1 {print $2}' <<< "$invo")

        # Check SSM command execution status first.
        if [[ "$ssm_status" != "Success" ]]; then
            log 8 "SSM command status=$ssm_status (transient failure, will retry)"
            sleep 25
            continue
        fi

        # Parse JOB_STATUS=<value> from stdout.
        job_status=$(grep -oE 'JOB_STATUS=.+' <<< "$stdout" | head -1 | cut -d= -f2 || echo "")
        if [[ -z "$job_status" ]]; then
            log 8 "WARNING: no JOB_STATUS line in stdout, elapsed=${elapsed_s}s proj_cost=\$$proj_cost"
            sleep 25
            continue
        fi

        # Handle the parsed status.
        case "$job_status" in
            succeeded)
                status="succeeded"
                break
                ;;
            failed|aborted|cancelled)
                status="failed"
                break
                ;;
            todo|doing|aborting)
                # Job in progress — reset error streak and continue polling.
                db_error_streak=0
                log 8 "elapsed=${elapsed_s}s proj_cost=\$$proj_cost status=$job_status"
                sleep 25
                continue
                ;;
            NOT_FOUND)
                # Job row missing (unusual but not fatal — maybe transient replication lag).
                db_error_streak=0
                log 8 "WARNING: job row not found (id=$JOB_ID), elapsed=${elapsed_s}s proj_cost=\$$proj_cost"
                sleep 25
                continue
                ;;
            DB_ERROR:*)
                # DB connection/query error. Tolerate a few transient blips but abort on persistent failures.
                db_error_streak=$((db_error_streak + 1))
                log 8 "DB error (streak=$db_error_streak): $job_status, elapsed=${elapsed_s}s proj_cost=\$$proj_cost"
                if (( db_error_streak >= 5 )); then
                    echo "polling ABORT: 5 consecutive DB errors, last=$job_status" >&2
                    status="db_error"
                    break
                fi
                sleep 25
                continue
                ;;
            *)
                # Unknown status value — log but continue (maybe a new procrastinate status we don't know).
                db_error_streak=0
                log 8 "WARNING: unknown job status '$job_status', elapsed=${elapsed_s}s proj_cost=\$$proj_cost"
                sleep 25
                continue
                ;;
        esac
    done
fi
log 8 "loop exit status=$status"

# ------- phase 9 + 10 -------
log 9 "collect"
if [[ "$status" == "succeeded" && $DRYRUN -eq 0 ]]; then
    # Check if output dir exists; if so, rename to avoid clobbering.
    outdir="claudedocs/measurement-${RUN_ID}"
    if [[ -d "$outdir" ]]; then
        retry_suffix="retry-$(date -u +%s)"
        log 9 "output dir exists, renaming to ${outdir}-${retry_suffix}"
        mv "$outdir" "${outdir}-${retry_suffix}"
    fi
    mkdir -p "$outdir"

    # SAM writes to SUBDIRS["sam"] = "07_sam" (not "sam").
    RESULTS_JSON="${ANALYSIS_DIR}/07_sam/per_image_results.json"

    # Pre-check: does the expected results file exist?
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 60 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"test -f $RESULTS_JSON && echo EXISTS || echo MISSING\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    existence_check=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text | tr -d '[:space:]')
    if [[ "$existence_check" != "EXISTS" ]]; then
        echo "collect FAIL: results file not found at $RESULTS_JSON" >&2
        log 9 "running find to diagnose..."
        cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
            --timeout-seconds 60 \
            --document-name AWS-RunShellScript \
            --parameters "commands=[\"find $ANALYSIS_DIR -name per_image_results.json\"]" \
            --query "Command.CommandId" --output text)
        sleep 5
        found=$(aws_q ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
            --query "StandardOutputContent" --output text)
        echo "find results: $found" >&2
        exit 6
    fi
    log 9 "results file exists, collecting..."

    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 60 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"cat $RESULTS_JSON\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text \
        > "${outdir}/per_image_results.json"

    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --timeout-seconds 60 \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"sudo bash -c 'set -a; . /etc/flake-analysis-worker.env; set +a; PGPASSWORD=\\\"\\\$SAA_DB_PASSWORD\\\" psql -h \\\$SAA_DB_HOST -p \\\$SAA_DB_PORT -U \\\$SAA_DB_USER -d \\\$SAA_DB_NAME -tAc \\\"SELECT extract(epoch from ts) as ts_epoch, event, payload FROM worker_events WHERE run_id=$RUN_ID ORDER BY ts\\\"'\"]" \
        --query "Command.CommandId" --output text)
    sleep 5
    aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text \
        > "${outdir}/worker_events.tsv"
fi

log 10 "compute timing breakdown"
if [[ "$status" == "succeeded" ]]; then
    if (( DRYRUN )); then
        boot_s=70; model_load_s=30; proc_s=30; total_s=130
    else
        boot_s="$BOOT_S"
        events_tsv="${outdir}/worker_events.tsv"
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
        cat > "${outdir}/summary.json" <<EOF
{
  "run_id": ${RUN_ID},
  "instance_id": "${INSTANCE_ID}",
  "instance_type": "${WON_INSTANCE_TYPE}",
  "gpu_count": ${WON_GPU_COUNT},
  "market": "${WON_MARKET}",
  "availability_zone": "${WON_AZ}",
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
