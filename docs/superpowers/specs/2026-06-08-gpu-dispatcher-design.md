# GPU Dispatcher (1-Click Fine-Tuned SAM) — Design Spec

> **Status:** approved (brainstorming sweep 2026-06-08).
> **Owner directive:** "원스텝으로 런치해서 fine-tuned SAM 돌리는게 중요해" — Web UI에서 1-click이면 끝.
> **Acceptance:** Real Compute Tab click → cold start (~2-3 min) → fine-tuned SAM 100-image run → DB-persisted results → 10-min idle self-terminate.

---

## 1. Goal

Web UI Compute Tab에서 "Run SAM" 클릭 1번에 fine-tuned SAM이 GPU에서 끝까지 돈다. 사용자는 SSE로 실시간 진행 상황 본다. 작업 끝나면 GPU 인스턴스 자동 종료.

이 spec은 **prod feature**다 — measurement용 1회성 도구가 아니다. measurement도 같은 메커니즘 통해서 무료(웹에서 클릭).

## 2. Non-goals

- 새 measurement orchestrator(`measure-run.sh` / `measure-defer.py` 같은 것). 이미 존재하나 prod 경로에는 불필요. **이 spec은 그것들을 deprecate한다 — backlog.**
- AMI 새로 baking. 검증된 `ami-0b7ec5ff47a1eff11` 그대로 사용 (§15).
- Multi-AZ spot fleet, CloudWatch + Lambda watchdog, 별도 GPU dispatcher 마이크로서비스. 이 프로젝트 규모에 over-engineering — backlog.
- Frontend SAM `model` dropdown. 별도 plan(`2026-05-27-pipeline-params-refactor.md`)에서 처리.
- Always-on GPU. 비용 부담 — Q4에서 reject.

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Web (Compute Tab)                                                  │
│  SamRunPanel.tsx — "Run SAM" button                                 │
│       ↓ POST /run/sam {scan_id, params}                             │
└──┬──────────────────────────────────────────────────────────────────┘
   │
┌──▼──────────────────────────────────────────────────────────────────┐
│  FastAPI api/routes/run.py::run_sam (existing, EDIT)                │
│   1. resolve scan + analysis_folder (existing)                      │
│   2. acquire_gpu_spawn_lock(scan_id)  ← race protection             │
│   3. check_gpu_capacity(): queue length + live worker count         │
│      ├─ live worker found → just defer (existing path)              │
│      └─ no worker:                                                  │
│          • boto3 run-instances (LT v26: ami-0b7ec5ff47a1eff11)      │
│          • emit SSE "gpu_launching" with instance-id                │
│          • return 202 immediately (caller polls SSE)                │
│   4. defer run_sam to procrastinate gpu queue (existing line 334)   │
└──┬──────────────────────────────────────────────────────────────────┘
   │
   │ procrastinate stores job in PG, queue='gpu' status='todo'
   │
┌──▼──────────────────────────────────────────────────────────────────┐
│  GPU instance (g6e.48xlarge spot or on-demand fallback)             │
│                                                                     │
│  AMI: ami-0b7ec5ff47a1eff11                                         │
│    pre-baked: CUDA 12.4 + venv + repo + merged.pt + vendor          │
│                                                                     │
│  user-data on cold launch (NEW — 2 line fix):                       │
│    • peft pip install (~30s)                                        │
│    • mkdir + symlink /home2/qpress/qpress/models/sam2.1/{...} → \   │
│       /opt/sam/m3/sam2.1/{...}                                      │
│    • systemd start flake-analysis-worker.service (existing)         │
│                                                                     │
│  procrastinate worker polls 'gpu' queue, picks up run_sam           │
│  ↓                                                                  │
│  run_sam → run_sam_step → core.pipeline.sam.run_sam → vendor        │
│      _run_sam_multi_gpu OR single-GPU _vendor_infer                 │
│  ↓ progress_callback emits SSE "step_progress" frames               │
│                                                                     │
│  flake-analysis-idle-shutdown.timer (existing, 10min idle)          │
│    → terminate-instances                                            │
└──┬──────────────────────────────────────────────────────────────────┘
   │
   │ SSE per-image progress + "step_completed"
   │
┌──▼──────────────────────────────────────────────────────────────────┐
│  Web                                                                │
│  PipelineTimeline updates: "GPU launching" → "GPU ready" →          │
│       "[5/100] processing..." → "Done"                              │
└─────────────────────────────────────────────────────────────────────┘
```

### Boundary discipline

| Layer | Files | Permanence | Scope |
|---|---|---|---|
| **Prod permanent — GPU lifecycle** | `api/services/gpu_lifecycle.py` (NEW) | always shipped | check_gpu_capacity + spawn_gpu_worker, single-purpose unit methods |
| **Prod permanent — API integration** | `api/routes/run.py::run_sam` (EDIT, ~20 LoC) | always shipped | calls gpu_lifecycle before defer |
| **Prod permanent — SSE event types** | `api/sse.py` (EDIT, ~10 LoC) | always shipped | adds `gpu_launching` / `gpu_ready` to whitelist |
| **Infra hardening** | `scripts/aws/sam-gpu-worker-userdata.sh` (EDIT, +10 LoC) | always shipped | the §15.3 two manual fixes baked in |
| **Frontend SSE rendering** | `web/src/components/run/SamRunPanel.tsx` (EDIT, ~15 LoC) | always shipped | renders `gpu_launching` / `gpu_ready` badges |

## 4. Components

### 4.1 `src/flake_analysis/api/services/gpu_lifecycle.py` — NEW

Single-file module, three pure-ish functions, no class, no global state. ~80 LoC.

```python
"""GPU instance lifecycle — check current capacity + spawn-on-demand.

Used by api/routes/run.py::run_sam to launch a GPU spot instance when
the procrastinate gpu queue has work but no live worker. Leverages the
existing flake-analysis-idle-shutdown.timer for self-terminate, so this
module only handles the spawn side; cleanup is automatic.
"""

async def check_gpu_capacity(session: AsyncSession, *, ec2: boto3.client = None) -> dict:
    """Returns {'live_workers': int, 'queue_depth': int, 'should_spawn': bool}.

    live_workers: count of EC2 instances tagged Project=qpress-sam in
        states pending|running|stopping|shutting-down. Excludes terminated.
    queue_depth: count of procrastinate_jobs WHERE queue_name='gpu' AND
        status IN ('todo','doing'). Includes the job we're about to defer.
    should_spawn: True iff queue_depth > 0 AND live_workers == 0.
    """

async def spawn_gpu_worker(*, run_id: int, instance_type: str = "g6e.48xlarge",
                           ec2: boto3.client = None) -> str:
    """Launch a GPU spot (with on-demand fallback) using the configured LT.

    Returns instance_id. Tags Purpose=run-{run_id}, Project=qpress-sam.
    Mirrors sam-bake-ami.sh 6-step fallback: spot us-east-2a → 2c →
    on-demand 2a → 2c → fail. LT name from SAA_GPU_LT_NAME env, default
    qpress-sam-gpu-worker.
    """

async def acquire_gpu_spawn_lock(session: AsyncSession, *, scan_id: int) -> bool:
    """PG advisory lock to serialise spawn decisions across concurrent
    /run/sam calls within the same scan. Returns True if acquired.

    Lock key: hash('gpu_spawn:{scan_id}'). Released on session close.
    Caller pattern: try_acquire → re-check check_gpu_capacity inside →
    spawn or skip → release.
    """
```

**Why a service module not inline in run.py?** Future Compute Pipeline (`/run/pipeline`) will call the same logic. Putting it in a service keeps both routes consistent.

### 4.2 `src/flake_analysis/api/routes/run.py::run_sam` — EDIT, ~20 LoC

Existing logic preserved. New block inserted before `app.tasks["run_sam"].defer_async(...)` (currently line 334):

```python
from flake_analysis.api.services.gpu_lifecycle import (
    acquire_gpu_spawn_lock, check_gpu_capacity, spawn_gpu_worker,
)
from flake_analysis.api.sse import emit_progress

# ... existing scan/analysis resolution ...

acquired = await acquire_gpu_spawn_lock(session, scan_id=scan_id)
if acquired:
    cap = await check_gpu_capacity(session)
    if cap["should_spawn"]:
        instance_id = await spawn_gpu_worker(run_id=run.id)
        await emit_progress(
            run_id=run.id,
            payload={"type": "gpu_launching", "instance_id": instance_id},
        )

# existing defer (unchanged)
await app.tasks["run_sam"].defer_async(
    run_id=run.id,
    raw_images_dir=str(raw_images_dir),
    analysis_folder=str(analysis_folder),
    weights_path=str(weights_path),
    ...
)
```

### 4.3 `src/flake_analysis/api/sse.py` — EDIT, ~10 LoC

Existing `PipelineProgressBridge` whitelist of event types. Add:

```python
_PIPELINE_EVENT_TYPES = frozenset({
    "step_started", "step_progress", "step_completed",
    "pipeline_done", "pipeline_error",
    "gpu_launching", "gpu_ready",  # NEW
})
```

Worker emits `gpu_ready` from `run_sam` task entry (replacing the no-op SSE first frame); the API route emits `gpu_launching` directly when spawn fires.

### 4.4 `src/flake_analysis/worker/tasks.py::run_sam` — EDIT, ~5 LoC

Add a single `_emit_progress(payload={"type": "gpu_ready", ...})` at task entry:

```python
@app.task(queue="gpu", name="run_sam")
def run_sam(*, run_id, raw_images_dir, ..., model_meta=None):
    # NEW: announce we picked up the job (cold-start UX)
    try:
        n_imgs = len(_list_images(Path(raw_images_dir)))
    except Exception:
        n_imgs = 0
    _emit_progress(run_id=run_id, payload={"type": "gpu_ready", "image_count": n_imgs})

    # existing emit_marker(sam_task_start, ...) and rest of task body
```

### 4.5 `scripts/aws/sam-gpu-worker-userdata.sh` — EDIT, +10 LoC

Append to step 5 (deps), after `uv sync`:

```bash
# §15.3 fix #2 — peft for vendor M3 LoRA path (not in requirements-inference.txt)
sudo -u "${RUN_USER}" -H \
    /usr/local/bin/uv pip install \
    --python "${REPO_DIR}/.venv/bin/python" \
    "peft>=0.8.0,<0.20"

# §15.3 fix #1 — vendor args.json hardcoded /home2/qpress/... → symlink to /opt/sam/m3/
mkdir -p /home2/qpress/qpress/models
ln -sfn /opt/sam/m3/sam2.1 /home2/qpress/qpress/models/sam2.1
```

### 4.6 New launch template version v26

- AMI: `ami-0b7ec5ff47a1eff11` (verified §15 stack)
- Instance type: `g6e.48xlarge` (8-GPU prod target — works with existing `_run_sam_multi_gpu` hardware gate)
- User-data: post-edit version (with §15.3 fixes baked in)
- Published via `INSTANCE_TYPE=g6e.48xlarge IMAGE_ID_OVERRIDE=ami-0b7ec5ff47a1eff11 ./scripts/aws/sam-launch-template.sh`

### 4.7 `web/src/components/run/SamRunPanel.tsx` — EDIT, ~15 LoC

In the SSE event renderer (existing `useStepProgress` hook), add cases:

```tsx
const renderProgress = (msg: SamProgressMessage) => {
  if (msg.type === 'gpu_launching') {
    return (
      <ProgressBadge variant="info" data-testid="sam-progress-gpu-launching">
        Launching GPU instance ({msg.instance_id})…
      </ProgressBadge>
    )
  }
  if (msg.type === 'gpu_ready') {
    return (
      <ProgressBadge variant="success" data-testid="sam-progress-gpu-ready">
        GPU ready, processing {msg.image_count} images
      </ProgressBadge>
    )
  }
  // existing step_progress / step_completed rendering
  return existingRenderer(msg)
}
```

### 4.8 Tests — NEW + EDIT

| Layer | File | Type |
|---|---|---|
| `gpu_lifecycle.check_gpu_capacity` | `tests/api/services/test_gpu_lifecycle.py` | Unit (PG-marked + moto) |
| `gpu_lifecycle.spawn_gpu_worker` | same | Unit (moto) — assert spot-then-on-demand fallback |
| `gpu_lifecycle.acquire_gpu_spawn_lock` | same | Unit (PG-marked) — concurrent acquire returns False for second |
| `routes/run.run_sam` cold path | `tests/api/test_run_sam_route.py` | Integration |
| `routes/run.run_sam` warm path | same | Integration |
| `routes/run.run_sam` race | same | Integration — two concurrent calls → spawn called once |
| `SamRunPanel.tsx` SSE rendering | `tests/web/SamRunPanel.test.tsx` | Vitest |
| **Manual smoke (acceptance)** | docs/sam-ops.md §27 writeup | Real Compute Tab click |

## 5. Data flow

### Cold path (no live worker)

| t | Actor | Event |
|---|---|---|
| 0 | user | clicks Run SAM in Compute Tab |
| +0.1s | API | POST /run/sam → check_gpu_capacity returns should_spawn=True |
| +1s | API | boto3 run-instances; SSE `gpu_launching` |
| +2s | API | defer run_sam to procrastinate; HTTP 202 returned |
| +30-90s | EC2 | spot allocated, instance running |
| +60-120s | EC2 | SSM Online, user-data step 5 finishes (peft + symlinks) |
| +90-150s | EC2 | flake-analysis-worker.service active |
| +92-152s | worker | procrastinate picks up run_sam; emits SSE `gpu_ready` |
| +95-155s | worker | build_sam2_finetuned model load (~30s) |
| +125-185s | worker | first [1/100]; per-image SSE `step_progress` |
| +220-280s | worker | last [100/100]; SSE `step_completed` |
| +225s | worker | per_image_results.json on disk; run_sam returns |
| +225s+ | client | "Done" displayed |
| +825s | EC2 | idle 10min → /usr/local/sbin/flake-analysis-idle-shutdown.sh |
| +830s | EC2 | aws ec2 terminate-instances (instance role) |
| +860s | EC2 | terminated; cost ~$1.50 (g6e.48xlarge spot 14min) |

### Warm path (live worker idle, within 10min)

| t | Actor | Event |
|---|---|---|
| 0 | user | clicks Run SAM |
| +0.1s | API | check_gpu_capacity: should_spawn=False (1 live worker) |
| +0.2s | API | defer run_sam; HTTP 202 |
| +1s | worker | picks up immediately; SSE `gpu_ready` |
| +30s | worker | model load + first [1/100] |
| +125s | worker | last [100/100] |
| +725s | EC2 | idle terminate (timer reset by activity) |

### Race-protected concurrent click path

| t | Caller | Event |
|---|---|---|
| 0 | A | POST /run/sam |
| 0 | B | POST /run/sam (same scan, ~5ms apart) |
| +0.1s | A | acquire_gpu_spawn_lock(scan_id) → True |
| +0.1s | B | acquire_gpu_spawn_lock(scan_id) → False (waits) |
| +0.2s | A | check_gpu_capacity → should_spawn=True; spawn fires |
| +0.5s | A | defer; lock released |
| +0.5s | B | acquire returns True (lock freed) |
| +0.5s | B | check_gpu_capacity → live_workers=1 (just-launched A) → should_spawn=False |
| +0.6s | B | defer (no spawn); lock released |

## 6. Error handling

| Failure | Detection | Recovery |
|---|---|---|
| Spot capacity drought | `RunInstances InsufficientInstanceCapacity` | spawn_gpu_worker auto-fallback to on-demand (mirror `sam-bake-ami.sh`) |
| `run-instances` IAM fail | boto3 `ClientError` | API returns 503 `gpu_lifecycle_failed`; SSE `pipeline_error`; user retries later |
| User-data fails on cold start | SSM never reaches Online OR worker.service inactive after 10 min | procrastinate job sits in `todo` indefinitely; current `idle-shutdown` doesn't fire because queue still has work; **mitigation:** add 20-min API-side timeout that emits `pipeline_error` and DOES NOT auto-retry (user clicks again) |
| GPU worker crashes mid-task | `procrastinate_jobs.status='failed'` | existing exception path emits `pipeline_error` SSE with traceback (T4 wiring); user clicks Run SAM again |
| Spot reclaim mid-task | `flake-analysis-spot-monitor.sh` SIGTERMs worker; worker `record_run_end status='failed' error='spot_interrupted'` | existing API-side once-per-job re-enqueue (§13 sam-ops.md) |
| Idle-shutdown fires while job still pending | `idle-shutdown.sh` checks queue first (existing) | already correct |
| Concurrent Run SAM clicks | both see queue_depth=0 + live_workers=0 | acquire_gpu_spawn_lock serialises; second caller re-checks and skips spawn |
| Cost runaway | instance abs-cap.timer 60min self-terminate (T9 still in user-data) | already implemented |

## 7. Testing strategy

Plan-level test gates (writing-plans expands per task):

| Layer | Test type | Acceptance |
|---|---|---|
| `check_gpu_capacity` | Unit (PG + moto) | seed procrastinate_jobs rows + mock describe-instances; returns correct should_spawn |
| `spawn_gpu_worker` | Unit (moto) | spot succeeds → returns instance_id; spot drought → on-demand → returns instance_id; both fail → raises |
| `acquire_gpu_spawn_lock` | Unit (PG) | first acquire True; concurrent second False until first releases |
| `routes/run.run_sam` cold | Integration (PG + moto) | mock spawn; assert SSE `gpu_launching` event emitted, defer_async called once |
| `routes/run.run_sam` warm | Integration | seed live worker tag; assert no spawn, no `gpu_launching`, defer_async called |
| `routes/run.run_sam` race | Integration | two concurrent requests; spawn called once total |
| `SamRunPanel.tsx` SSE | Vitest | mock SSE `gpu_launching` → "Launching GPU…" badge; `gpu_ready` → "GPU ready" badge |
| **Manual acceptance smoke** | Real run | Compute Tab click → 2-3 min cold start → 100-image SAM run → DB results → 10-min idle terminate |

Acceptance writeup destination: `docs/sam-ops.md §27`.

## 8. Lock-in decisions

These are decided. Future drift requires its own brainstorm.

- **D1**: AMI is `ami-0b7ec5ff47a1eff11` (cu124 stack, §15 verified). NOT the new DLAMI `ami-092ae5880cb9cf957`.
- **D2**: GPU spawn trigger lives in API route (`run.py::run_sam`). Not Lambda, not cron.
- **D3**: GPU lifecycle is on-demand. No always-on, no warm pool. 10-min idle self-terminate (existing).
- **D4**: §15.3 two manual fixes (peft + symlink) baked into user-data. No AMI rebake.
- **D5**: SSE event types `gpu_launching` and `gpu_ready` enter the whitelist. They're a thin metadata layer on the existing SSE channel.
- **D6**: Race protection via PG advisory lock keyed on `scan_id`. No global lock.
- **D7**: User-data NEW launch template version v26 (publish-once after this plan lands).
- **D8**: NO new measurement-only orchestrator. The 7-attempt `measure-run.sh` / `measure-defer.py` machinery from `2026-05-29-gpu-measurement-harness.md` is **deprecated** by this spec — backlog cleanup task.

## 9. Out-of-scope follow-ups (parking lot)

- Lambda + EventBridge GPU lifecycle service (over-engineering at this scale).
- Multi-AZ spot fleet (capacity drought mitigation; revisit when concurrent runs become common).
- AMI re-bake with `peft` + symlinks pre-installed (saves ~30s cold start, but user-data is the simpler home for now).
- Frontend `model` dropdown (covered by `2026-05-27-pipeline-params-refactor.md`).
- Decommission `measure-run.sh` + `measure-defer.py` + `worker/measurement.py` + `worker/markers.py` + `worker_events` table (separate cleanup plan after this lands).

---

*Authored 2026-06-08. Implementation plan to follow via writing-plans skill.*
