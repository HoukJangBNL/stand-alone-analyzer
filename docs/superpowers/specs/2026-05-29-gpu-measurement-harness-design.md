# GPU Measurement Harness — Design Spec

> **Status:** approved (brainstorming sweep 2026-05-29)
> **Owner directive:** "측정은 1회지만 단위 메서드는 prod-grade — 다른 모델 swap 시 compute pipeline에 즉시 투입 가능해야"
> **Acceptance:** 8-GPU 100-image (scan6-100) measurement run completes in this session with `boot_s` / `model_load_s` / `processing_s` separated.

---

## 1. Goal

Enable a single, repeatable 8-GPU measurement run on `g6e.48xlarge` for the 100-image `scan6-100` dataset using `merged_m3` weights, and **simultaneously** harden the worker code path so future model swaps drop into the existing compute pipeline without ad-hoc plumbing.

Three architecture gaps that aborted #229 are root-fixed:

1. §18 — cloud-init checked out `main`, vendor submodule lived only on `feat/migration-cutover`. **Fixed already** by the feat → main merge (commit `5528b1b` reverts `REPO_REF` default to `main`); this spec assumes `main` is the canonical bake source.
2. §19 / §20 — defer launcher had no RDS env. systemd `EnvironmentFile=` does NOT propagate via `/proc/PID/environ`. **Fix here:** the defer launcher loads `/etc/flake-analysis-worker.env` directly via a single shared utility.
3. §20 — operator session lost track of an idle on-demand instance for 53 min. **Fix here:** instance-side `flake-analysis-abs-cap.timer` self-terminates at wall-clock T+60 min regardless of operator state, plus operator-side polling loop with a cost cap.

## 2. Non-goals

- Production-grade GPU dispatcher (API → procrastinate → spot launch with auto-tear-down). That is a separate plan to be brainstormed when Compute Pipeline Phase 4 lands.
- Frontend SAM `model` dropdown. Already covered by `docs/superpowers/plans/2026-05-27-pipeline-params-refactor.md` (P5.4-derived items).
- AMI re-bake. `ami-092ae5880cb9cf957` is validated and re-used as-is.
- Multi-AZ spot fleet. The single-AZ spot-then-on-demand fallback (already proven in `sam-bake-ami.sh`) is sufficient for a measurement run.
- New procrastinate queue. Same `gpu` queue.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Operator (PM session or human, runs locally with AWS profile=qpress)│
│                                                                      │
│  ./scripts/sam/measure-run.sh                                        │
│    --weights s3://qpress-uploads/internal/sam/merged_m3/...pt        │
│    --dataset s3://qpress-uploads/internal/sam/scan6-100/             │
│    --instance-type g6e.48xlarge                                      │
│    --cost-cap-usd 5                                                  │
│    --wall-cap-min 60                                                 │
└──┬───────────────────────────────────────────────────────────────────┘
   │
   │ 1. publish LT v19 (REPO_REF=main, IMAGE_ID=ami-092ae5880cb9cf957)
   │ 2. spot launch + on-demand auto-fallback (mirror sam-bake-ami.sh)
   │ 3. wait SSM online (boot_s = run_instances → SSM Online)
   │ 4. SSM put scripts/sam/measure-defer.py to /tmp on instance
   │ 5. SSM exec defer launcher with weights+dataset args
   │ 6. polling-and-act loop (30s tick) on procrastinate_jobs.status
   │    + cost-cap projection check on every tick
   │ 7. on success: pull per_image_results.json + query usage_events
   │    SQL for timing markers
   │ 8. ALWAYS: aws ec2 terminate-instances --instance-ids ...
   │
   ▼ via SSM RunShellScript
┌──────────────────────────────────────────────────────────────────────┐
│  GPU instance (g6e.48xlarge spot/on-demand, ami-092ae5880cb9cf957)   │
│                                                                      │
│  user-data on boot:                                                  │
│    • cloud-init clones REPO_REF=main + vendor submodule              │
│    • S3 download merged.pt + merged_m3.pt → /opt/sam/weights/        │
│    • write /etc/flake-analysis-worker.env from SSM SecureString      │
│    • systemd flake-analysis-worker.service                           │
│    • systemd flake-analysis-idle-shutdown.timer (existing, 10m idle) │
│    • systemd flake-analysis-abs-cap.timer (NEW, 60m absolute)        │
│                                                                      │
│  measure-defer.py launcher (SSM-pushed to /tmp):                     │
│    1. from worker.measurement import load_worker_env                 │
│       os.environ.update(load_worker_env())   ← the §19/§20 fix       │
│    2. import worker.app + measurement.build_defer_payload            │
│    3. async with app.open_async(): defer_async(name="run_sam", ...)  │
│    4. print(job_id); exit                                            │
│                                                                      │
│  procrastinate worker (already running) picks up job from gpu queue: │
│    run_sam (worker/tasks.py — instrumentation EDIT):                 │
│      usage.emit("sam_task_start", payload={run_id, model_meta, ...}) │
│      → core.pipeline.sam.run_sam(...)                                │
│           progress_callback("marker:model_load_start") ──┐           │
│           build_sam2(...) / merged_m3 dispatch           │           │
│           progress_callback("marker:processing_start")   │ each      │
│           run_multi_process(...) or _vendor_infer(...)   │ marker    │
│           progress_callback("marker:processing_end") ────┘ → 1 row   │
│      usage.emit("sam_task_end", payload={status, masks_total, ...})  │
└──────────────────────────────────────────────────────────────────────┘
                            │
                            ▼ progress + usage events
                     ┌───────────────────┐
                     │  RDS qpressdb     │
                     │  procrastinate_*  │ ← job lifecycle (status flag)
                     │  usage_events     │ ← timing + model meta rows
                     │  runs table       │ ← record_run_start/end summary
                     └───────────────────┘
```

### Boundary discipline (the prod/measurement split)

| Layer | Files | Permanence | Scope |
|---|---|---|---|
| **Prod permanent** | `core/pipeline/sam.py`, `worker/tasks.py` | always shipped | timing + model-meta logged on every SAM run, prod or measurement |
| **Prod permanent — single-purpose unit methods** | `worker/measurement.py` | always shipped | env loader, S3 model resolver, defer payload builder — re-used by future prod GPU dispatcher |
| **Measurement-only wrappers** | `scripts/sam/measure-run.sh`, `scripts/sam/measure-defer.py` | retire-able when prod dispatcher lands | one-shot operator harness; safe to remove later |
| **Infra hardening** | `scripts/aws/sam-gpu-worker-userdata.sh` | always shipped | `abs-cap.timer` self-terminate; benefits prod too |

## 4. Components

### 4.1 `src/flake_analysis/core/pipeline/sam.py` — EDIT

**Existing:** `run_sam(images_dir, weights_path, out_dir, device, progress_callback)` with `torch.cuda.device_count() >= 2` branch at line 447–451 calling `_run_sam_multi_gpu`.

**Change:** insert four marker calls via the existing `progress_callback` parameter.

| Marker | Location |
|---|---|
| `marker:model_load_start` | first line of `_run_sam_multi_gpu` AND first line of single-GPU `_vendor_infer` path |
| `marker:processing_start` | immediately before `run_multi_process(...)` (multi-GPU) and immediately before `infer(...)` (single-GPU) |
| `marker:processing_end` | immediately after the same calls return |

Marker calls use `progress_callback(0.0, "marker:<name>")` — no new channel, no new arg. Caller (worker/tasks.py) routes `marker:*` separately.

**LoC delta:** ≤ 12 lines across two functions.

### 4.2 `src/flake_analysis/worker/tasks.py::run_sam` — EDIT

**Existing signature:**
```python
@app.task(queue="gpu", name="run_sam")
def run_sam(*, run_id, raw_images_dir, analysis_folder, weights_path, device=None) -> dict
```

**New signature:**
```python
@app.task(queue="gpu", name="run_sam")
def run_sam(*, run_id, raw_images_dir, analysis_folder, weights_path, device=None,
            model_meta: dict[str, str] | None = None) -> dict
```

`model_meta` is `{name, sha256, source_uri}`. Optional with backward-compatible None default — when present, it's persisted in the `sam_task_start` usage event so future analytics can attribute timings to a specific model artifact.

**Change inside the task body:**

1. At entry: `usage.emit(event="sam_task_start", payload={run_id, model_meta, raw_images_dir, weights_path})`.
2. Wrap the `_on_progress` callback so `message.startswith("marker:")` routes to `usage.emit(event=marker_name, payload={run_id})` and bypasses the SSE `progress` event. Non-marker progress flows through as before.
3. At exit (try/except): `usage.emit(event="sam_task_end", payload={run_id, status: "success"|"failed", masks_total, errors})`.

**LoC delta:** ~25 lines (helper + 3 emit calls + callback wrapper).

### 4.3 `src/flake_analysis/worker/measurement.py` — NEW

Single-file module, three pure-ish functions, no class, no global state.

```python
"""Measurement & model-swap utilities. Prod-grade — re-used by future GPU dispatcher.

Three concerns:
* Worker env inheritance (load_worker_env) — bridges systemd EnvironmentFile= into ad-hoc Python.
* Model artifact discovery (resolve_model_meta) — local path or S3 URI → name + sha + local_path.
* Defer payload construction (build_defer_payload) — pure data shape for app.configure_task(...).defer.
"""

def load_worker_env(env_file: Path = Path("/etc/flake-analysis-worker.env")) -> dict[str, str]:
    """Parse a systemd-style EnvironmentFile into a dict.

    Handles K=V, quoted values, comment lines, blank lines. Raises FileNotFoundError
    if the file is missing (prod systems must have it; this isn't a fallback path).
    Used by measure-defer.py and by future prod dispatcher cron jobs.
    """

def resolve_model_meta(weights_uri: str) -> dict[str, str]:
    """Resolve a weights reference into a deterministic local artifact + metadata.

    Args:
        weights_uri: either an absolute local path to a .pt file, or an
            "s3://bucket/prefix/name.pt" URI. Sidecar "<name>.pt.sha256" is
            required at the same prefix; missing sidecar raises ValueError.

    Returns:
        {"name": "<basename without .pt>",
         "sha256": "<lowercase hex>",
         "source_uri": "<s3://...>" or "file:///opt/sam/weights/...",
         "local_path": "/opt/sam/weights/<name>.pt"}

    For S3 inputs: download is idempotent (skips if local sha256 matches).
    For local inputs: read sidecar and trust it.

    This is THE entry point for adding a new model — point measure-run.sh at the
    new S3 URI, and the rest of the pipeline picks up the new artifact without
    code change.
    """

def build_defer_payload(*, run_id: int, scan_id: int, model_meta: dict,
                        dataset_dir: Path, analysis_folder: Path) -> dict:
    """Construct kwargs for app.configure_task(name='run_sam', queue='gpu').defer_async.

    Pure function: no DB, no IO. Returns a dict suitable to splat into defer_async.
    Centralised so that future prod dispatcher and current measurement script call
    the same constructor — no defer-shape drift.
    """
```

**Why these three?** §19/§20 root cause was env vs ad-hoc Python boundary; resolve_model_meta is owner's "swap any model into compute pipeline" directive; build_defer_payload prevents the next defer-shape regression.

### 4.4 `scripts/sam/measure-defer.py` — NEW

SSM-pushed to `/tmp/measure-defer.py` on the GPU instance. Imports the prod measurement module — does NOT re-implement env or payload logic.

```python
#!/usr/bin/env python3
"""One-shot defer launcher executed via SSM on the GPU worker instance.

Reads CLI args, sources worker env, defers run_sam to procrastinate, prints job_id.
"""
import argparse, asyncio, os, sys
from pathlib import Path
sys.path.insert(0, "/opt/sam/stand-alone-analyzer/src")  # AMI repo path

from flake_analysis.worker.measurement import (
    load_worker_env, resolve_model_meta, build_defer_payload,
)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--weights-uri", required=True)
    p.add_argument("--dataset-dir", required=True)
    p.add_argument("--analysis-folder", required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--scan-id", type=int, required=True)
    args = p.parse_args()

    os.environ.update(load_worker_env())

    # Imports that need RDS env happen AFTER load_worker_env().
    from flake_analysis.worker.app import app
    model_meta = resolve_model_meta(args.weights_uri)
    payload = build_defer_payload(
        run_id=args.run_id, scan_id=args.scan_id, model_meta=model_meta,
        dataset_dir=Path(args.dataset_dir),
        analysis_folder=Path(args.analysis_folder),
    )

    async def _defer():
        async with app.open_async():
            job_id = await app.configure_task(
                name="run_sam", queue="gpu"
            ).defer_async(**payload)
            print(f"job_id={job_id}")

    asyncio.run(_defer())

if __name__ == "__main__":
    main()
```

### 4.5 `scripts/sam/measure-run.sh` — NEW

Operator-facing one-shot. Bash, idempotent, dryrun-friendly.

Phases (each one logs `[phase=<n>] <description>` to stdout):

1. **Precheck** — `aws sts get-caller-identity`, profile=qpress, region=us-east-2. Fail loudly if missing.
2. **Args parse** — `--weights`, `--dataset`, `--instance-type=g6e.48xlarge`, `--cost-cap-usd=5`, `--wall-cap-min=60`, `--dryrun`.
3. **LT publish** — call `scripts/aws/sam-launch-template.sh` with `INSTANCE_TYPE=$1 IMAGE_ID_OVERRIDE=ami-092ae5880cb9cf957`. Verify v19 is `$Default` and user-data decodes to contain `REPO_REF.*main`.
4. **Spot launch with fallback** — mirror the `sam-bake-ami.sh` 6-step pattern: spot in 2a → 2c → on-demand 2a → 2c → fail. Capture `INSTANCE_ID`, `LAUNCH_TS_EPOCH`, `MARKET_TYPE`. Tag `Purpose=measure-run-<git-short-sha>-<UTC-yymmdd-hhmm>`.
5. **SSM wait** — poll `aws ssm describe-instance-information` until `PingStatus=Online`. Record `SSM_ONLINE_TS_EPOCH`. Compute `boot_s = SSM_ONLINE_TS_EPOCH - LAUNCH_TS_EPOCH`.
6. **Pre-flight** — SSM run: nvidia-smi -L | wc -l == 8, /opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/run_amg_v2.py exists, /etc/flake-analysis-worker.env exists, `pgrep -f flake_analysis.worker` returns ≥1 PID.
7. **Push defer** — `aws ssm send-command` with `Document=AWS-RunShellScript` payload that downloads `scripts/sam/measure-defer.py` from S3 (or inlines it via base64) and runs it with `--weights-uri "$WEIGHTS" --dataset-dir /opt/sam/dataset/scan6-100 --analysis-folder /opt/sam/runs/<RUN_ID> --run-id <RUN_ID> --scan-id <SCAN_ID>`. Capture `JOB_ID` from stdout. **Fail fast** if non-zero exit.
8. **Polling-and-act loop** (30s tick, max `wall-cap-min` minutes):
   - Query `procrastinate_jobs WHERE id=$JOB_ID` via SSM (psql executed as ubuntu user with the worker env sourced — same env-file pattern).
   - On every tick: also query `usage_events WHERE event='sam_task_start' OR event LIKE 'sam_%'` to see how far the job is.
   - On every tick: project cost = `(now - LAUNCH_TS_EPOCH) / 3600 * hourly_rate`; if `> cost-cap-usd`: terminate + report.
   - On `status='succeeded'`: break loop, go to phase 9.
   - On `status='failed'`: capture worker.log + procrastinate_jobs.attempts/last_heartbeat, terminate, report.
9. **Collect** — SSM pull `per_image_results.json` from `/opt/sam/runs/<RUN_ID>/sam/`, copy to `claudedocs/measurement-<UTC-yymmdd-hhmm>/`. Run SQL to extract timing breakdown:
   ```sql
   SELECT event, occurred_at, payload
     FROM usage_events
    WHERE payload->>'run_id' = '<RUN_ID>'
      AND event LIKE 'sam_%' OR event LIKE 'marker:%'
    ORDER BY occurred_at;
   ```
10. **Compute & print** — derive `boot_s` (phase 5), `model_load_s` (`marker:processing_start - marker:model_load_start`), `processing_s` (`marker:processing_end - marker:processing_start`), `total_s` (sam_task_end - sam_task_start). Single-line summary + JSON dump.
11. **Always-terminate** — `aws ec2 terminate-instances --instance-ids $INSTANCE_ID` runs in a `trap EXIT` so it fires even on script kill / phase-2 fail / Ctrl-C.

### 4.6 `scripts/aws/sam-gpu-worker-userdata.sh` — EDIT

Add a systemd timer + service near the existing `flake-analysis-idle-shutdown` block:

```ini
# /etc/systemd/system/flake-analysis-abs-cap.service
[Unit]
Description=Absolute wall-clock cap — terminate this instance after T+ABS_CAP_MIN minutes
[Service]
Type=oneshot
ExecStart=/usr/local/bin/abs-cap-terminate.sh
```

```ini
# /etc/systemd/system/flake-analysis-abs-cap.timer
[Unit]
Description=Fire abs-cap.service ABS_CAP_MIN minutes after boot
[Timer]
OnBootSec=${ABS_CAP_MIN}min
Unit=flake-analysis-abs-cap.service
[Install]
WantedBy=timers.target
```

```bash
# /usr/local/bin/abs-cap-terminate.sh
#!/usr/bin/env bash
set -euo pipefail
TOKEN=$(curl -sS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
logger -t abs-cap "ABS_CAP fired — terminating $INSTANCE_ID"
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "$REGION"
```

`ABS_CAP_MIN` defaults to 60, env-overridable at user-data render time. The instance role already has `ec2:TerminateInstances` (P4.3 IAM bootstrap).

## 5. Data flow

| t | Actor | Event | Note |
|---|---|---|---|
| 0 | operator | `./measure-run.sh ...` | LT publish + run-instances issued |
| ~70s | instance | SSM Online | `boot_s = ssm_online_epoch - launch_epoch` |
| ~120s | operator | SSM exec measure-defer.py | inherits worker env via load_worker_env() |
| ~120s | instance | run_sam picked up | `usage_events.sam_task_start` row inserted |
| ~125s | worker | `marker:model_load_start` | row inserted |
| ~155s | worker | `marker:processing_start` | row inserted; `model_load_s = ts(processing_start) - ts(model_load_start)` |
| ~185s | worker | `marker:processing_end` | row inserted; `processing_s = ts(processing_end) - ts(processing_start)` |
| ~190s | worker | `usage_events.sam_task_end` + per_image_results.json on disk | `total_s = ts(task_end) - ts(task_start)` |
| ~200s | operator | polling sees status=succeeded | break loop |
| ~210s | operator | SSM pull per_image_results.json + SQL timing | results saved to claudedocs/ |
| ~215s | operator | terminate-instances | trap EXIT path |

**Backstop:** if anything between t=0 and t=3600s wedges, the instance-side `abs-cap.timer` self-terminates at t=3600s. Operator-side polling enforces `cost-cap-usd` as a tighter bound (default $5 ≈ 41 min on g6e.48xlarge spot, ≈ 25 min on on-demand).

## 6. Error handling

| Failure | Detection | Recovery |
|---|---|---|
| Spot capacity drought | `RunInstances` `InsufficientInstanceCapacity` | auto-fallback to next AZ, then on-demand (mirror `sam-bake-ami.sh`) |
| Cloud-init fails | Phase 6 SSM checks (vendor path / worker env / worker PID) | abort, dump `/var/log/cloud-init.log` + worker.log, terminate |
| `measure-defer.py` non-zero exit | Phase 7 SSM command `Status != Success` | abort, capture stderr to `claudedocs/`, terminate |
| RDS connect fails inside defer | `load_worker_env` returns 0 keys / sslmode error | abort with clear message ("worker env file empty or unreadable"), terminate |
| Worker crashes mid-task | `procrastinate_jobs.status='failed'` in poll | pull worker.log + last 20 lines of `/var/log/sam-gpu-worker-userdata.log`, terminate |
| `wall-cap-min` exceeded | operator clock | force terminate, mark run as `wall_cap_exceeded` |
| `cost-cap-usd` projected exceeded | poll-loop projection | force terminate, mark run as `cost_cap_exceeded` |
| Operator session dies | OS / network | `abs-cap.timer` fires at T+60 min unconditionally |
| Operator session OOMs the laptop | OS | same — instance self-terminates regardless |
| Spot reclaim mid-run | EC2 sends SIGTERM | partial outputs preserved on instance EBS until terminate; results may be incomplete (acceptable for this measurement) |

**Two independent timers** = belt and suspenders. One can fail without the other.

## 7. Testing strategy

Detailed test plan is the writing-plans phase's job. Layer-by-layer guardrails this spec commits to:

| Layer | Test type | Acceptance |
|---|---|---|
| `core.pipeline.sam` markers | Unit (no GPU) | Mock `progress_callback`, assert 4 marker calls in correct order on both single-GPU and multi-GPU paths. |
| `worker.tasks.run_sam` instrumentation | Unit (PG-marked) | Defer mock task with `model_meta`, assert ≥5 `usage_events` rows: task_start + 3 markers + task_end. |
| `worker.measurement.load_worker_env` | Unit | Parse fixture env files: K=V, quoted, escaped, blank lines, comments. Missing file → FileNotFoundError. |
| `worker.measurement.resolve_model_meta` | Unit (`moto`) | Mock S3, sidecar present → returns dict with sha matching sidecar. Missing sidecar → ValueError. Local path with sidecar → no S3 call. |
| `worker.measurement.build_defer_payload` | Unit | Pure function, golden-snapshot dict — pin against drift. |
| `scripts/sam/measure-defer.py` | Integration smoke (local PG, no GPU) | Run against test PG with `flake_analysis.worker` registered, assert job_id printed and PG row visible. |
| `scripts/sam/measure-run.sh` | `shellcheck` + `--dryrun` mode | Dryrun skips run-instances and SSM, prints intended commands. |
| `flake-analysis-abs-cap.timer` | Manual one-time | Bake/launch a t3.micro test instance with `ABS_CAP_MIN=2`, observe self-terminate within 2–3 min. (Outside the AMI; tested on Ubuntu base.) |

**Final acceptance** = a real `g6e.48xlarge` measurement run with `scan6-100` finishing inside `cost-cap-usd=5`, `boot_s` / `model_load_s` / `processing_s` / `total_s` printed, instance verified terminated.

## 8. Out-of-scope follow-ups (parking lot)

These are NOT in this plan; preserved for the next brainstorm:

- **Prod-grade GPU dispatcher** — API endpoint that defers + spawns GPU spot + tears down. Compute Pipeline Phase 4 territory.
- **Multi-AZ spot fleet** — cross-AZ resilience post-#229. Capacity drought / 2b outage scenarios both motivate it.
- **CloudWatch + Lambda watchdog** — AWS-native cost cap. Belt-only when there are many concurrent GPU runs.
- **Frontend `model` dropdown** — covered by the pipeline-params-refactor plan.
- **Polling-and-act subagent template** — encoding the "wait then act in one task body" pattern in `.claude/agents/devops-engineer.md` so future briefs inherit it. Agreed pattern, but a docs-only chore — write it as a small follow-up commit, no plan needed.
- **`094b30f` userdata harden** (AWS CLI v2 step in `sam-gpu-bootstrap.sh`, GH PAT injection in worker user-data) — accessible in origin's reflog by SHA. Cherry-pick after this plan lands if a cold launch path needs it.

## 9. Lock-in decisions

These are decided. Future drift requires its own brainstorm.

- **D1**: Three timing markers (`model_load_start`, `processing_start`, `processing_end`) — not four, not seven. Keep the analytic surface narrow.
- **D2**: `usage_events` is the timing sink — no new `measurement_runs` table.
- **D3**: `model_meta` payload is `{name, sha256, source_uri}` — three keys, no version, no provenance JSON.
- **D4**: Defer launcher is a Python file pushed via SSM — not a Lambda, not a service inside the AMI.
- **D5**: Cost cap is operator-side projection — no AWS Budgets API integration.
- **D6**: Wall cap is two-layer — operator polling + instance `abs-cap.timer`. Both default 60 min, env-overridable.
- **D7**: Spec includes the manual smoke acceptance in this session (100-image scan6-100 measurement run).

---

*Authored 2026-05-29. Implementation plan to follow via writing-plans skill.*
