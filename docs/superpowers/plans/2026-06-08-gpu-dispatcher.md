# GPU Dispatcher (1-Click Fine-Tuned SAM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web UI Compute Tab "Run SAM" 클릭 1번으로 fine-tuned SAM이 GPU EC2에서 끝까지 돈다. cold-start ~2-3분 동안 SSE로 단계별 상황(`gpu_launching` → `gpu_ready` → 각 이미지 진행) 보여주고, 끝나면 10-min idle self-terminate.

**Architecture:** **이 plan은 새 모듈을 만들지 않는다.** 코드 audit으로 prod에 이미 다음이 구현돼 있음을 확인:
* `worker/launcher.py` (351 LoC) — `ensure_worker_running` + `PgAdvisoryLock` + `LaunchResult` (action='launched'|'noop' + instance_id) + spot-with-on-demand fallback
* `api/routes/run.py::_ensure_gpu_worker` + `_defer_sam_job` (위 launcher 호출하는 seam)
* `api/routes/run.py::run_sam` — `_stream_sam_events(run_id)` → `bridge.emit_progress / emit_done / emit_error` 분기
* `api/sse.py::ProgressBridge` — `emit_progress / emit_done / emit_error / close` 메소드, queue로 SSE 큐잉
* `worker/tasks.py::_emit_progress` — sync psycopg `pg_notify(sam_progress:{run_id}, json)` 호출
* `worker/tasks.py::run_sam` — task body 진입 후 `_emit_progress({"type": "progress", ...})` 등 호출
* `scripts/aws/sam-gpu-worker-userdata.sh` — peft pip install (line 198) + /home2/qpress symlink (step 5c) **이미 commit됨**

진짜 필요한 변경은 그 사이의 작은 gap 5개:
1. `_ensure_gpu_worker()` 시그니처 → `LaunchResult` 리턴 (현재 `None`)
2. `ProgressBridge`에 `emit_gpu_launching` + `emit_gpu_ready` 메소드 추가 (whitelist는 없음 — `event["type"]`이 wire frame name이 됨)
3. `_defer_sam_job`에서 `LaunchResult.action == "launched"`이면 `bridge.emit_gpu_launching(instance_id)` 호출
4. `run_sam` route driver loop에 `gpu_ready` payload type 분기 추가 → `bridge.emit_gpu_ready(image_count)`
5. `worker/tasks.py::run_sam` 진입에서 `_emit_progress({"type": "gpu_ready", "image_count": N})` 호출
6. SamRunPanel.tsx — 두 새 SSE event 렌더링
7. LT v26 publish — `ami-0b7ec5ff47a1eff11`로 default 교체 (현재 v25는 cuDNN ABI 깨진 새 AMI)
8. 라이브 acceptance smoke

**Spec deviation note (vs `docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md`):** spec §4.1은 새 모듈 `api/services/gpu_lifecycle.py`를 제안했지만 launcher.py가 이미 동등 기능 구현. spec §4.3은 `_PIPELINE_EVENT_TYPES` whitelist 추가를 제안했지만 PipelineProgressBridge는 whitelist를 쓰지 않고 직접 method 호출 패턴 — 이 plan은 ProgressBridge(per-step용)에 두 method를 추가한다. Spec의 의도는 동일하게 충족.

**Tech Stack:** Python 3.11 (FastAPI route + worker task + asyncpg LISTEN), TypeScript (React + Vite + vitest), AWS EC2 (`ami-0b7ec5ff47a1eff11` via launch template `qpress-sam-gpu-worker`), procrastinate (PG-backed queue), psycopg sync (NOTIFY emit), pytest (PG-marked + moto).

**Pre-read (engineer skim before starting):**
- `docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md` (361 lines) — spec + lock-in decisions
- `src/flake_analysis/worker/launcher.py:1-100` — contract for `ensure_worker_running` + `LaunchResult`
- `src/flake_analysis/api/routes/run.py:300-470` — existing `_ensure_gpu_worker` + `_defer_sam_job` + driver loop with payload type branching
- `src/flake_analysis/api/sse.py:1-90` — `ProgressBridge` methods + queue mechanics
- `src/flake_analysis/worker/tasks.py:60-160` — `_emit_progress` sync NOTIFY + run_sam task body
- `web/src/components/run/SamRunPanel.tsx` (121 LoC) — current SSE rendering surface
- `scripts/aws/sam-gpu-worker-userdata.sh:185-310` — peft + symlink (already in main, just verify)

---

## File Structure

| Path | Responsibility | New/Modify | LoC delta |
|---|---|---|---|
| `src/flake_analysis/api/sse.py` | Add `ProgressBridge.emit_gpu_launching(instance_id)` + `emit_gpu_ready(image_count)` | MODIFY | +20 |
| `src/flake_analysis/api/routes/run.py` | (a) `_ensure_gpu_worker` → returns `LaunchResult` (b) `_defer_sam_job` calls `bridge.emit_gpu_launching` (c) driver loop branch for `gpu_ready` payload | MODIFY | +25 |
| `src/flake_analysis/worker/tasks.py::run_sam` | At task entry, count images and emit `_emit_progress({"type": "gpu_ready", "image_count": N})` | MODIFY | +15 |
| `web/src/components/run/SamRunPanel.tsx` | Render `gpu_launching` + `gpu_ready` SSE events | MODIFY | +30 |
| `tests/api/test_sse_progress_bridge.py` | New methods round-trip + queue ordering | NEW | ~40 |
| `tests/api/test_run_sam_sse.py` | cold-path / warm-path / driver loop branch tests (extend) | MODIFY | ~80 |
| `tests/worker/test_tasks.py` | `gpu_ready` emit at task entry (extend) | MODIFY | ~50 |
| `web/src/components/run/__tests__/SamRunPanel.test.tsx` | Render cold-start badges (extend or new) | NEW or MODIFY | ~50 |
| LT v26 (AWS state) | Republish default version with verified AMI | one-shot | n/a |
| `docs/sam-ops.md` §27 | Live acceptance writeup | NEW | ~80 |
| `docs/project-status.md` | One-line history + Last-updated header | MODIFY | ~5 |

---

## Task 1 — `ProgressBridge`에 두 메소드 추가

**Files:**
- Modify: `src/flake_analysis/api/sse.py`
- Test: `tests/api/test_sse_progress_bridge.py` (NEW)

**Goal:** `ProgressBridge`가 `emit_gpu_launching(instance_id)` + `emit_gpu_ready(image_count)` 호출 시 큐에 `{type: "gpu_launching", instance_id: ...}` / `{type: "gpu_ready", image_count: N}` 이벤트를 넣는다. `sse_stream`은 `event["type"]`을 SSE event name으로 그대로 쓰니 wire frame은 `event: gpu_launching\ndata: {...}` 형태로 자동 발행.

- [ ] **Step 1: Read existing ProgressBridge (sse.py:1-90)**

Read `src/flake_analysis/api/sse.py` 1-90줄을 읽어 `ProgressBridge` 클래스의 `emit_progress` 패턴 확인. Drop-on-full vs guaranteed-terminal 구분과 `_loop.call_soon_threadsafe` 패턴을 그대로 따라야 함 (워커 스레드에서 호출될 수도 있음).

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_sse_progress_bridge.py
"""ProgressBridge round-trip for gpu_launching + gpu_ready events."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_emit_gpu_launching_round_trips_through_stream():
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    bridge.emit_gpu_launching("i-abc123")
    bridge.close()

    events = [e async for e in bridge.stream()]
    assert len(events) == 1
    assert events[0] == {"type": "gpu_launching", "instance_id": "i-abc123"}


@pytest.mark.asyncio
async def test_emit_gpu_ready_round_trips_through_stream():
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    bridge.emit_gpu_ready(100)
    bridge.close()

    events = [e async for e in bridge.stream()]
    assert len(events) == 1
    assert events[0] == {"type": "gpu_ready", "image_count": 100}


@pytest.mark.asyncio
async def test_gpu_events_drop_under_pressure_like_progress():
    """gpu_launching + gpu_ready use the drop-when-full path (same as
    emit_progress) — they are NOT terminal events."""
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    # Fill the queue past 128 (the bounded maxsize).
    for i in range(150):
        bridge.emit_gpu_launching(f"i-{i:03d}")
    bridge.close()

    events = [e async for e in bridge.stream()]
    # Some events dropped, but close() (terminal) ensures the stream ends.
    # No assertion on exact count — just that the stream terminates and
    # the bridge does not deadlock.
    assert len(events) <= 150
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/api/test_sse_progress_bridge.py -v
```

Expected: 3 fail with `AttributeError: 'ProgressBridge' object has no attribute 'emit_gpu_launching'`.

- [ ] **Step 4: Add the two methods to ProgressBridge**

In `src/flake_analysis/api/sse.py`, after `emit_progress` and before `emit_done` (keep alongside the other non-terminal `emit_*` so the file stays grouped):

```python
    def emit_gpu_launching(self, instance_id: str) -> None:
        """Non-terminal cold-start event. Worker not yet running.

        Drops silently if the SSE consumer has fallen behind — same
        semantics as emit_progress. Frontend renders this as the
        'Launching GPU instance...' badge during the ~60-90s spot
        allocation + boot window.
        """
        event = {"type": "gpu_launching", "instance_id": str(instance_id)}
        self._loop.call_soon_threadsafe(self._put_progress, event)

    def emit_gpu_ready(self, image_count: int) -> None:
        """Non-terminal cold-start event. Worker has picked up the
        procrastinate job and is about to load the SAM model.

        Drops silently if the SSE consumer has fallen behind — same
        semantics as emit_progress. Frontend flips from 'launching'
        to 'GPU ready, processing N images' on this event.
        """
        event = {"type": "gpu_ready", "image_count": int(image_count)}
        self._loop.call_soon_threadsafe(self._put_progress, event)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/api/test_sse_progress_bridge.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Run wider SSE/route test sweep**

```bash
uv run pytest tests/api/ -v -k "sse or progress_bridge"
```

Expected: all green; new methods don't perturb existing ProgressBridge tests.

- [ ] **Step 7: Commit**

```bash
git add src/flake_analysis/api/sse.py tests/api/test_sse_progress_bridge.py
git commit -m "feat(sse): ProgressBridge emit_gpu_launching + emit_gpu_ready

Two non-terminal cold-start events for the 1-click fine-tuned SAM
dispatcher. emit_gpu_launching fires when the API route boots a
fresh GPU instance; emit_gpu_ready fires when the worker picks up
the procrastinate job. Frontend renders both as cold-start badges
in SamRunPanel before the per-image progress takes over.

Both use the drop-on-full path (same as emit_progress) — these are
non-terminal cosmetic UX events; missing one is fine, the per-image
progress that follows is what tells the user the run completed.

Plan: docs/superpowers/plans/2026-06-08-gpu-dispatcher.md (Task 1)
Spec: docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md §4.3
"
```

---

## Task 2 — `_ensure_gpu_worker` 시그니처 + `_defer_sam_job`이 `gpu_launching` emit

**Files:**
- Modify: `src/flake_analysis/api/routes/run.py`
- Test: `tests/api/test_run_sam_sse.py` (extend)

**Goal:** `_ensure_gpu_worker()`가 `LaunchResult`를 리턴한다 (이미 launcher.py가 만들어주니 그냥 await 결과 그대로 리턴). `_defer_sam_job`이 그 결과를 받아서 `bridge` 인자가 있으면 `action == "launched"`일 때 `bridge.emit_gpu_launching(instance_id)`를 호출.

- [ ] **Step 1: Read existing _ensure_gpu_worker + _defer_sam_job (run.py:300-380)**

Read `src/flake_analysis/api/routes/run.py:300-380`. 현재 `_ensure_gpu_worker()`는 `None` 리턴, `_defer_sam_job(*, run_id, raw_images_dir, analysis_folder, weights_path, device)` 인자를 받음. driver loop는 `_stream_sam_events(run_id)` async iterator를 돌면서 `payload["type"]` 분기 (`progress` / `completed` / `error`).

- [ ] **Step 2: Write the failing tests**

Append to `tests/api/test_run_sam_sse.py`:

```python
@pytest.mark.asyncio
async def test_defer_sam_job_emits_gpu_launching_when_action_is_launched(monkeypatch):
    """When ensure_worker_running returns LaunchResult(action='launched'),
    _defer_sam_job calls bridge.emit_gpu_launching with the instance_id."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.api.sse import ProgressBridge
    from flake_analysis.worker.launcher import LaunchResult

    captured: list[tuple[str, str]] = []

    class _CaptureBridge(ProgressBridge):
        def emit_gpu_launching(self, instance_id):
            captured.append(("gpu_launching", instance_id))

    bridge = _CaptureBridge()

    # Force ensure path to "launched"
    async def _fake_ensure():
        return LaunchResult(action="launched", instance_id="i-test123")

    monkeypatch.setattr(run_module, "_ensure_gpu_worker", _fake_ensure)

    # No-op the actual procrastinate defer
    async def _fake_inner_defer(**kw):
        pass

    # Replace the internal app.tasks reference with a no-op
    class _FakeTask:
        async def defer_async(self, **kw):
            pass

    class _FakeApp:
        tasks = {"run_sam": _FakeTask()}

    monkeypatch.setattr(
        "flake_analysis.worker.app.app",
        _FakeApp(),
    )

    # Call _defer_sam_job with the bridge so it can emit
    await run_module._defer_sam_job(
        run_id=1,
        raw_images_dir="/x",
        analysis_folder="/y",
        weights_path="/z.pt",
        device=None,
        bridge=bridge,  # NEW kwarg
    )

    assert ("gpu_launching", "i-test123") in captured


@pytest.mark.asyncio
async def test_defer_sam_job_skips_gpu_launching_when_action_is_noop(monkeypatch):
    """When ensure_worker_running returns LaunchResult(action='noop'),
    _defer_sam_job does NOT call emit_gpu_launching."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.api.sse import ProgressBridge
    from flake_analysis.worker.launcher import LaunchResult

    captured: list[tuple[str, str]] = []

    class _CaptureBridge(ProgressBridge):
        def emit_gpu_launching(self, instance_id):
            captured.append(("gpu_launching", instance_id))

    bridge = _CaptureBridge()

    async def _fake_ensure():
        return LaunchResult(action="noop", reason="worker_already_running")

    monkeypatch.setattr(run_module, "_ensure_gpu_worker", _fake_ensure)

    class _FakeTask:
        async def defer_async(self, **kw):
            pass

    class _FakeApp:
        tasks = {"run_sam": _FakeTask()}

    monkeypatch.setattr("flake_analysis.worker.app.app", _FakeApp())

    await run_module._defer_sam_job(
        run_id=2,
        raw_images_dir="/x",
        analysis_folder="/y",
        weights_path="/z.pt",
        device=None,
        bridge=bridge,
    )

    assert captured == []


@pytest.mark.asyncio
async def test_run_sam_driver_loop_routes_gpu_ready_payload(monkeypatch):
    """When listen_for_run yields {type: 'gpu_ready', image_count: N},
    the driver loop calls bridge.emit_gpu_ready(N) and continues
    listening (it's a non-terminal event)."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.api.sse import ProgressBridge

    captured: list[tuple[str, int]] = []

    class _CaptureBridge(ProgressBridge):
        def emit_gpu_ready(self, image_count):
            captured.append(("gpu_ready", image_count))

    # Build a fake stream: gpu_ready, then completed (terminal)
    async def _fake_stream(run_id):
        yield {"type": "gpu_ready", "image_count": 100}
        yield {"type": "completed", "result": {"images": 100, "masks_total": 0, "errors": 0}}

    monkeypatch.setattr(run_module, "_stream_sam_events", _fake_stream)

    # ... use existing test fixtures to invoke run_sam endpoint with the
    # capture bridge in place. Look at how test_run_sam_emits_progress
    # (existing) wires this up and follow the same pattern.
```

The third test will need the existing `tests/api/test_run_sam_sse.py` patterns — read that file first to see how the route is invoked under test (most likely an `httpx.AsyncClient` against a mini-FastAPI app with overrides applied).

- [ ] **Step 3: Run tests to verify failure**

```bash
uv run pytest tests/api/test_run_sam_sse.py -v -k "gpu_launching or gpu_ready"
```

Expected: 3 fail.

- [ ] **Step 4: Modify `_ensure_gpu_worker` signature**

In `src/flake_analysis/api/routes/run.py`, change return type and body:

```python
async def _ensure_gpu_worker():
    """Boot a GPU worker EC2 if none is live (P4.4).

    Returns the LaunchResult so the caller (_defer_sam_job) can emit
    a gpu_launching SSE frame when action == 'launched'.

    Module-level seam so tests can monkeypatch with a no-op or a
    canned LaunchResult.
    """
    from flake_analysis.worker.launcher import (
        PgAdvisoryLock,
        ensure_worker_running,
    )

    return await ensure_worker_running(advisory_lock=PgAdvisoryLock())
```

- [ ] **Step 5: Modify `_defer_sam_job` to accept bridge + emit**

```python
async def _defer_sam_job(
    *,
    run_id: int,
    raw_images_dir,
    analysis_folder,
    weights_path,
    device: str | None,
    bridge: "ProgressBridge | None" = None,
) -> None:
    """Push a SAM job onto the procrastinate ``gpu`` queue.

    Before deferring, ensures a GPU worker exists (P4.4). If the fleet
    is empty, kicks off a spot launch and emits a gpu_launching SSE
    frame on the supplied bridge so the frontend can render the
    cold-start wait.
    """
    launch_result = await _ensure_gpu_worker()

    if (
        bridge is not None
        and launch_result.action == "launched"
        and launch_result.instance_id is not None
    ):
        try:
            bridge.emit_gpu_launching(launch_result.instance_id)
        except Exception:  # noqa: BLE001 — never let SSE emit failures
            logger.exception(
                "gpu_launching emit failed for run_id=%s", run_id,
            )

    # existing defer path — unchanged
    from flake_analysis.worker import tasks as _tasks  # noqa: F401
    from flake_analysis.worker.app import app

    await app.tasks["run_sam"].defer_async(
        run_id=run_id,
        raw_images_dir=str(raw_images_dir),
        analysis_folder=str(analysis_folder),
        weights_path=str(weights_path),
        device=device,
    )
```

- [ ] **Step 6: Wire bridge into `run_sam` route's `_defer_sam_job` call**

In the `run_sam` route's `driver()` function (which constructs `bridge = ProgressBridge()` and calls `_defer_sam_job`):

```python
    bridge = ProgressBridge()

    async def driver():
        try:
            await _defer_sam_job(
                run_id=run_id,
                raw_images_dir=manifest.raw_images_dir,
                analysis_folder=manifest.analysis_folder,
                weights_path=params.weights_path,
                device=params.device,
                bridge=bridge,  # NEW — pass the bridge so it can emit
            )

            # existing driver loop (no change to existing branches)
            terminal_seen = False
            async for payload in _stream_sam_events(run_id):
                ptype = payload.get("type")
                if ptype == "progress":
                    bridge.emit_progress(...)
                # NEW branch — non-terminal, worker just picked up the job
                elif ptype == "gpu_ready":
                    image_count = int(payload.get("image_count", 0) or 0)
                    bridge.emit_gpu_ready(image_count)
                elif ptype == "completed":
                    # ... existing ...
                elif ptype == "error":
                    # ... existing ...
            # ... rest of driver unchanged ...
```

- [ ] **Step 7: Run tests to verify pass**

```bash
uv run pytest tests/api/test_run_sam_sse.py -v
```

Expected: existing tests still pass + 3 new tests pass.

- [ ] **Step 8: Run wider sweep**

```bash
uv run pytest tests/api/ -v -k "run_sam or run_pipeline or progress_bridge"
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/flake_analysis/api/routes/run.py tests/api/test_run_sam_sse.py
git commit -m "feat(api): /run/sam emits gpu_launching + routes gpu_ready

_ensure_gpu_worker now returns LaunchResult from worker.launcher.
_defer_sam_job accepts an optional bridge and calls emit_gpu_launching
when launch_result.action == 'launched'. The driver loop in run_sam
adds a gpu_ready branch that calls bridge.emit_gpu_ready with the
worker-supplied image_count.

This closes the cold-start UX loop on the API side: frontend gets
gpu_launching ~1s after click, then gpu_ready ~120-180s later when
the worker picks up the procrastinate job.

Plan: docs/superpowers/plans/2026-06-08-gpu-dispatcher.md (Task 2)
Spec: docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md §4.2
"
```

---

## Task 3 — `worker/tasks.py::run_sam` emits `gpu_ready` at task entry

**Files:**
- Modify: `src/flake_analysis/worker/tasks.py`
- Test: extend `tests/worker/test_tasks.py`

**Goal:** procrastinate가 SAM job을 픽업한 직후, worker는 `_emit_progress({"type": "gpu_ready", "image_count": N})`를 호출. 이게 cold-start UX의 마지막 신호.

- [ ] **Step 1: Read existing run_sam (tasks.py:100-200)**

Read `src/flake_analysis/worker/tasks.py:100-200`. 현재 task body는 `emit_marker(sam_task_start, ...)`로 시작 (T4 of prior plan). `gpu_ready` emit를 그 직전에 추가한다 — `_emit_progress`는 동일 모듈에 있는 sync NOTIFY 함수.

- [ ] **Step 2: Write the failing test**

Append to `tests/worker/test_tasks.py`:

```python
def test_run_sam_emits_gpu_ready_at_task_entry(monkeypatch, tmp_path):
    """run_sam emits gpu_ready as the FIRST progress event, BEFORE
    sam_task_start emit_marker, so the frontend cold-start UX flips
    from 'launching' to 'ready' before any other work."""
    from flake_analysis.worker import tasks as worker_tasks

    progress_payloads: list[dict] = []
    monkeypatch.setattr(
        worker_tasks,
        "_emit_progress",
        lambda *, run_id, payload: progress_payloads.append(payload),
    )
    monkeypatch.setattr(worker_tasks, "emit_marker", lambda **kw: None)

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    for n in ("a.png", "b.png", "c.png"):
        (images_dir / n).touch()

    # Stub the runner so we don't actually call vendor SAM
    monkeypatch.setattr(
        worker_tasks,
        "run_sam_step",
        lambda **kw: {"images": 3, "masks_total": 0, "errors": 0, "per_image": {}},
    )

    worker_tasks.run_sam(
        run_id=42,
        raw_images_dir=str(images_dir),
        analysis_folder=str(tmp_path / "out"),
        weights_path="/opt/sam/weights/m.pt",
    )

    # gpu_ready must be the first emit in the progress channel
    types = [p.get("type") for p in progress_payloads]
    assert types[0] == "gpu_ready"
    assert progress_payloads[0]["image_count"] == 3


def test_run_sam_gpu_ready_image_count_zero_when_dir_unreadable(monkeypatch, tmp_path):
    """If the images dir is missing, gpu_ready still fires with
    image_count=0 — the UX should still flip from 'launching' to 'ready'."""
    from flake_analysis.worker import tasks as worker_tasks

    progress_payloads: list[dict] = []
    monkeypatch.setattr(
        worker_tasks,
        "_emit_progress",
        lambda *, run_id, payload: progress_payloads.append(payload),
    )
    monkeypatch.setattr(worker_tasks, "emit_marker", lambda **kw: None)
    monkeypatch.setattr(
        worker_tasks,
        "run_sam_step",
        lambda **kw: {"images": 0, "masks_total": 0, "errors": 0, "per_image": {}},
    )

    worker_tasks.run_sam(
        run_id=43,
        raw_images_dir=str(tmp_path / "does_not_exist"),
        analysis_folder=str(tmp_path / "out"),
        weights_path="/opt/sam/weights/m.pt",
    )

    types = [p.get("type") for p in progress_payloads]
    assert types[0] == "gpu_ready"
    assert progress_payloads[0]["image_count"] == 0
```

- [ ] **Step 3: Run to verify failure**

```bash
uv run pytest tests/worker/test_tasks.py -v -k "gpu_ready"
```

Expected: 2 fail — no `gpu_ready` payload.

- [ ] **Step 4: Add gpu_ready emit at run_sam entry**

In `src/flake_analysis/worker/tasks.py::run_sam`, add a block at the very top of the function body, BEFORE `emit_marker(sam_task_start, ...)`:

```python
@app.task(queue="gpu", name="run_sam")
def run_sam(*, run_id, raw_images_dir, analysis_folder, weights_path,
            device=None, model_meta=None):
    # NEW: announce the cold-start handoff (frontend flips from
    # 'launching' to 'ready' on this event). Image count is best-effort:
    # the SAM step will count again precisely once it lists the dir.
    try:
        from pathlib import Path
        n_imgs = sum(
            1
            for p in Path(raw_images_dir).iterdir()
            if p.is_file()
            and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        )
    except Exception:  # noqa: BLE001 — count failure must not block the run
        n_imgs = 0
    try:
        _emit_progress(
            run_id=run_id,
            payload={"type": "gpu_ready", "image_count": int(n_imgs)},
        )
    except Exception:  # noqa: BLE001 — never let SSE emit failures cancel the job
        logger.exception("gpu_ready emit failed for run_id=%s", run_id)

    # existing emit_marker(sam_task_start, ...) — unchanged
    emit_marker(
        run_id=run_id,
        event="sam_task_start",
        payload={...},
    )
    # ... rest of task body unchanged ...
```

The image extension set matches `core/pipeline/sam.py::_list_images` (jpg/jpeg/png/tif/tiff) — keep them in lockstep.

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/worker/test_tasks.py -v
```

Expected: existing tests + 2 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/worker/tasks.py tests/worker/test_tasks.py
git commit -m "feat(worker): run_sam emits gpu_ready at task entry

Closes the cold-start UX loop end-to-end: api/routes/run.py emits
gpu_launching when the API spawns a fresh GPU instance; the worker
emits gpu_ready as soon as procrastinate hands it the run_sam job.
SamRunPanel renders both as cold-start badges before per-image
progress takes over.

Defensive: image count failure is logged but doesn't cancel the run
(the SAM step itself counts again precisely once it lists the dir).

Plan: docs/superpowers/plans/2026-06-08-gpu-dispatcher.md (Task 3)
Spec: docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md §4.4
"
```

---

## Task 4 — Frontend renders `gpu_launching` + `gpu_ready` badges

**Files:**
- Modify: `web/src/components/run/SamRunPanel.tsx`
- Test: `web/src/components/run/__tests__/SamRunPanel.test.tsx` (NEW or extend)

**Goal:** SamRunPanel는 cold-start 두 단계를 시각적으로 구분한다. SSE event `gpu_launching` 받으면 "Launching GPU instance (i-xxx)…" 보임. `gpu_ready` 받으면 "GPU ready, processing N images" 로 전환. 그 후 기존 per-image progress가 그대로.

- [ ] **Step 1: Read SamRunPanel.tsx + the SSE consumer hook**

Read `web/src/components/run/SamRunPanel.tsx` (121 LoC) 전체. 현재 SSE 메시지를 어떤 hook으로 받는지 (`useStepProgress`, `useEventSource`, 또는 직접 `EventSource`) 확인하고, 어디에서 message type 분기가 일어나는지 찾는다. 새 두 type을 그 surface에 끼워넣는다.

- [ ] **Step 2: Write the failing test**

Create or extend `web/src/components/run/__tests__/SamRunPanel.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { SamRunPanel } from "../SamRunPanel";

// Mock the SSE consumer hook used by SamRunPanel. Replace
// "@/hooks/useStepProgress" with whichever module the component
// actually imports — read SamRunPanel.tsx first.
vi.mock("@/hooks/useStepProgress", () => ({
  useStepProgress: vi.fn(),
}));

import { useStepProgress } from "@/hooks/useStepProgress";

const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);

describe("SamRunPanel cold-start SSE", () => {
  it("renders Launching badge on gpu_launching event", () => {
    (useStepProgress as ReturnType<typeof vi.fn>).mockReturnValue({
      events: [{ type: "gpu_launching", instance_id: "i-abc123" }],
      progress: 0,
      status: "running",
    });
    render(wrap(<SamRunPanel scanId={1} runId={1} />));
    expect(screen.getByTestId("sam-progress-gpu-launching")).toBeInTheDocument();
    expect(screen.getByText(/Launching GPU.*i-abc123/i)).toBeInTheDocument();
  });

  it("renders Ready badge on gpu_ready event", () => {
    (useStepProgress as ReturnType<typeof vi.fn>).mockReturnValue({
      events: [
        { type: "gpu_launching", instance_id: "i-abc123" },
        { type: "gpu_ready", image_count: 100 },
      ],
      progress: 0,
      status: "running",
    });
    render(wrap(<SamRunPanel scanId={1} runId={1} />));
    expect(screen.getByTestId("sam-progress-gpu-ready")).toBeInTheDocument();
    expect(screen.getByText(/GPU ready.*processing 100 images/i)).toBeInTheDocument();
  });
});
```

If the existing SamRunPanel uses a different prop API (e.g. takes `manifest` + `scanId` only, or a different SSE hook), adapt the mocks to match. The contract is the same: `gpu_launching` → testId + readable text; `gpu_ready` → different testId + count.

- [ ] **Step 3: Run to verify failure**

```bash
cd web && npx vitest run src/components/run/__tests__/SamRunPanel.test.tsx
```

Expected: 2 fail — testIds not present.

- [ ] **Step 4: Add the cold-start badges to SamRunPanel.tsx**

Find the SSE event renderer in `SamRunPanel.tsx` and add two cases:

```tsx
{events.map((event, idx) => {
  if (event.type === "gpu_launching") {
    return (
      <div
        key={idx}
        className="flex items-center gap-2 text-sm text-blue-700"
        data-testid="sam-progress-gpu-launching"
      >
        <Spinner />
        Launching GPU instance ({event.instance_id})…
      </div>
    );
  }
  if (event.type === "gpu_ready") {
    return (
      <div
        key={idx}
        className="flex items-center gap-2 text-sm text-green-700"
        data-testid="sam-progress-gpu-ready"
      >
        ✓ GPU ready, processing {event.image_count} images
      </div>
    );
  }
  // ... existing renderer for step_progress / step_completed / etc ...
})}
```

Match the existing styling system (whether it's tailwind classes, CSS modules, or a `<Badge>` component). The structural pieces required by the test:
- `data-testid="sam-progress-gpu-launching"` on the launching element
- `data-testid="sam-progress-gpu-ready"` on the ready element
- Text content that matches the regex `/Launching GPU.*<instance_id>/i` and `/GPU ready.*processing <N> images/i`

You may need to extend the TypeScript discriminated union for SSE messages. Find `SamProgressMessage` (or equivalent) and add:

```ts
type SamProgressMessage =
  | { type: "step_progress"; pct: number; msg: string }
  | { type: "step_completed"; result: SamResult }
  | { type: "step_error"; error: ErrorEnvelope }
  | { type: "gpu_launching"; instance_id: string }     // NEW
  | { type: "gpu_ready"; image_count: number };        // NEW
```

- [ ] **Step 5: Run vitest to verify pass**

```bash
cd web && npx vitest run src/components/run/__tests__/SamRunPanel.test.tsx
```

Expected: 2 passed.

- [ ] **Step 6: Run full vitest + tsc**

```bash
cd web && npx tsc --noEmit && npx vitest run
```

Expected: tsc clean, all vitest green.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/run/SamRunPanel.tsx \
        web/src/components/run/__tests__/SamRunPanel.test.tsx
git commit -m "feat(web): SamRunPanel renders gpu_launching + gpu_ready badges

Cold-start UX shows live state during the ~2-3 min between click
and first per-image progress event. 'Launching GPU instance (i-…)…'
appears within ~1s of click; flips to 'GPU ready, processing N
images' when the worker picks up the procrastinate job.

Plan: docs/superpowers/plans/2026-06-08-gpu-dispatcher.md (Task 4)
Spec: docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md §4.7
"
```

---

## Task 5 — LT v26 publish (verified AMI)

**Files:** none — AWS state change only (LT version publish; no instance launch; free).

**Goal:** Default LT version is v26 with `ImageId=ami-0b7ec5ff47a1eff11` (the §15 cu124 stack) and `InstanceType=g6e.48xlarge`. User-data is the post-Tasks-1-4 commits version (peft + symlink already in main).

- [ ] **Step 1: Confirm preconditions**

```bash
git log --oneline -6
```

Expected: HEAD includes Tasks 1-4 commits (sse method + run.py + worker tasks + SamRunPanel).

- [ ] **Step 2: Verify the verified AMI is still available**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-images --image-ids ami-0b7ec5ff47a1eff11 \
    --query 'Images[0].[ImageId,State,Name]' --output text
```

Expected: `ami-0b7ec5ff47a1eff11   available   qpress-saa-sam-warmup-2026-05-28`. If `State≠available`, STOP and report — the verified AMI is gone and the plan needs revisiting.

- [ ] **Step 3: Verify user-data has the §15.3 fixes (peft + symlink)**

```bash
grep -cE 'peft>=0.8.0|/home2/qpress/qpress/models' scripts/aws/sam-gpu-worker-userdata.sh
```

Expected: at least 2 matches (peft pip line ~198, symlink step 5c).

If 0, the §15.3 fixes were lost — restore them from git history (`git log -p scripts/aws/sam-gpu-worker-userdata.sh`) before publishing the LT.

- [ ] **Step 4: Publish v26**

```bash
INSTANCE_TYPE=g6e.48xlarge \
    IMAGE_ID_OVERRIDE=ami-0b7ec5ff47a1eff11 \
    bash scripts/aws/sam-launch-template.sh
```

Expected stdout: `Created version: v26` (or whatever the next free is — will be 26 since current is 25) + `Set default → v26`.

- [ ] **Step 5: Verify v26 user-data has both fixes baked in**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-launch-template-versions \
    --launch-template-name qpress-sam-gpu-worker --versions '$Latest' \
    --query 'LaunchTemplateVersions[0].LaunchTemplateData.UserData' \
    --output text \
  | base64 -d | gunzip 2>/dev/null \
  | grep -cE 'peft>=0.8.0|/home2/qpress/qpress/models'
```

Expected: at least 2.

- [ ] **Step 6: Verify ImageId + InstanceType + DefaultVersion**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-launch-template-versions \
    --launch-template-name qpress-sam-gpu-worker --versions '$Latest' \
    --query 'LaunchTemplateVersions[0].[VersionNumber,LaunchTemplateData.ImageId,LaunchTemplateData.InstanceType,DefaultVersion]' \
    --output text
```

Expected: `26   ami-0b7ec5ff47a1eff11   g6e.48xlarge   True`.

- [ ] **Step 7: Record in claudedocs (local-only)**

```bash
cat > "claudedocs/lt-publish-$(date -u +%Y-%m-%d)-v26.md" <<EOF
# LT publish v26 — $(date -u +%Y-%m-%dT%H:%M:%SZ)

Launch template: \`qpress-sam-gpu-worker\` v26 (set as default).

ImageId: ami-0b7ec5ff47a1eff11 (cu124 stack, §15-verified).
InstanceType: g6e.48xlarge.

User-data captures: post-Tasks-1-4 commits + §15.3 fixes (peft pip
install + /home2/qpress symlink) baked in.

Verified by:
  base64 → gunzip → grep -cE 'peft>=0.8.0|/home2/qpress/qpress/models' = ≥2

Ready for Task 6 acceptance smoke.
EOF
```

(claudedocs/ is gitignored. No git commit.)

---

## Task 6 — Manual acceptance smoke (the gate)

**Files:** none until the writeup commit.

**Goal:** Real Compute Tab "Run SAM" click → cold-start ~2-3 min → 100-image SAM run → DB results → 10-min idle self-terminate. Acceptance writeup → `docs/sam-ops.md §27`.

**Owner pre-approval:** Standing — owner directive "원스텝으로 fine-tuned SAM 돌리기"가 standing approval. Expected cost ~$1.50-2.

- [ ] **Step 1: Pre-flight checks**

```bash
git log --oneline -6
aws --profile qpress --region us-east-2 ec2 describe-instances \
    --filters "Name=tag:Project,Values=qpress-sam" \
              "Name=instance-state-name,Values=pending,running,stopping,shutting-down" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```

Expected: Tasks 1-4 commits present in HEAD; no live SAM instances.

- [ ] **Step 2: Confirm test data available**

A scan with the 100 PNG dataset must exist in the dev project (or owner's standing test project). `s3://qpress-uploads/internal/sam/scan6-100/` has 100 PNGs available — they may already be staged on the API side, or owner uploads via UploadModal first. If not present in any scan accessible from the Compute Tab, STOP and ask owner to stage. Don't try to create the scan via API in this task — that's outside scope.

- [ ] **Step 3: Start the dev environment**

```bash
# In one terminal:
uv run uvicorn flake_analysis.api:app --reload --port 8000

# In another terminal:
cd web && npm run dev
```

Both come up. `curl localhost:8000/healthz` returns 200. Browser at `http://localhost:5173` loads.

- [ ] **Step 4: Click Run SAM in the browser**

In the Compute Tab for the test scan, click Run SAM with default params (or whatever standing config the owner uses). Watch the SamRunPanel timeline. Expected sequence:

1. Within ~1s: "Launching GPU instance (i-xxx…)…" badge.
2. ~60-90s later: instance enters running state.
3. ~120-180s: cold-install finishes; "GPU ready, processing 100 images" badge.
4. ~125-185s: First per-image progress.
5. ~220-280s: "step_completed" — run done.

Capture screenshots of each phase (browser DevTools → element screenshot) → save to `claudedocs/acceptance-2026-06-08/<phase>.png`.

- [ ] **Step 5: Verify the worker terminates**

After the run completes, wait 10 minutes (the idle-shutdown timer interval), then:

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-instances --instance-ids <i-from-step-4> \
    --query 'Reservations[0].Instances[0].[State.Name,StateTransitionReason]' \
    --output text
```

Expected: `terminated User initiated...` (idle-shutdown self-call). If still `running` or `shutting-down` 12+ min after completion, manually terminate and document the lag.

- [ ] **Step 6: Pull artifacts for the writeup**

Capture from the running session + DB:
- run_id (procrastinate_jobs.id where queue_name='gpu' AND name='run_sam')
- scan_id, project_id
- instance_id (from the Launching badge text)
- launch ts (run-instances API response time)
- gpu_launching SSE ts (from browser DevTools network tab → EventStream)
- gpu_ready SSE ts (same)
- first step_progress SSE ts (same)
- last step_completed SSE ts (same)
- terminate ts (describe-instances StateTransitionReason)
- masks_total + errors (from per_image_results.json or worker_events sam_task_end payload)
- Cost = wall_min/60 × $3.96 (spot) or × $7.23 (on-demand) — match whichever the run actually was

- [ ] **Step 7: Append §27 to docs/sam-ops.md**

Truthful section:

```markdown
## 27. 1-click SAM dispatch acceptance — 2026-06-08 (SUCCESS)

First end-to-end demonstration of the GPU dispatcher prod path defined
by `docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md`. Real
Compute Tab click on a 100-PNG scan → cold-start GPU spawn → SAM
inference → idle-shutdown terminate. **No measurement orchestrator
was used.**

| Field | Value |
|---|---|
| Branch / HEAD | main / `<sha-after-T6-commit>` |
| AMI | `ami-0b7ec5ff47a1eff11` (cu124 stack, §15-verified) |
| Launch template | `qpress-sam-gpu-worker` v26 |
| Instance | `<i-...>` — `g6e.48xlarge` `<spot|on-demand>` in `<az>` |
| Run ID | `<N>` (procrastinate_jobs) |
| Scan ID | `<N>` |
| Click ts (UTC) | `<ISO>` |
| `gpu_launching` SSE ts | `<ISO>` (~+1s) |
| `gpu_ready` SSE ts | `<ISO>` (~+120-180s) |
| First `step_progress` ts | `<ISO>` |
| `step_completed` ts | `<ISO>` |
| Idle-terminate ts | `<ISO>` |
| Cold-start wall | `<N>` s (click → gpu_ready) |
| Processing wall | `<N>` s (gpu_ready → step_completed) |
| Total instance wall (billed) | `<N>` min |
| Cost (estimated) | **$<N>** |
| Result | per_image_results.json: `<N>` images, `<N>` masks_total, `<N>` errors |
| Status | `succeeded` |

### What worked
* `_ensure_gpu_worker` returning `LaunchResult` + the `_defer_sam_job`
  bridge wiring fired `gpu_launching` exactly once; PgAdvisoryLock
  serialised the spawn even with React strict-mode double-render.
* `run_sam` worker-side `gpu_ready` emit closed the cold-start UX gap;
  frontend flipped from launching to ready badge as expected.
* AMI `ami-0b7ec5ff47a1eff11` (cu124 + cuDNN ABI-aligned) ran vendor
  `build_sam2_finetuned` cleanly — no shape mismatch, no cuDNN
  failure (the two §26 root causes did not surface on this stack).
* idle-shutdown.timer fired at T+10min; instance auto-terminated.

### Recommendation
GPU dispatcher is prod-ready for routine 1-click SAM dispatch. Any
operator (or measurement campaign) can trigger fine-tuned SAM by
clicking Run SAM in the Compute Tab — no separate orchestrator
needed. The deprecated measurement harness (`measure-run.sh`,
`measure-defer.py`, `worker/measurement.py`, `worker/markers.py`,
`worker_events` table) can be decommissioned in a follow-up cleanup
plan (§9 of the spec parking lot).

### Cumulative #229 + follow-up cost
| Phase | Cost |
|---|---|
| #229 attempts §18-§20 | $9.27 |
| §21-§24 follow-up onion-peel | $4.66 |
| §25 attempt (manual orchestrator-less) | $1.31 |
| §26 attempt (manual §15 pattern, broken AMI v25) | $4.74 |
| **§27 (this prod path success)** | **$<N>** |
| **Total** | **$<TOTAL>** |
```

- [ ] **Step 8: One-line history entry + Last-updated header in docs/project-status.md**

Add to "## 7. 변경 로그":

```
- 2026-06-08 — **GPU Dispatcher SUCCESS — 1-click fine-tuned SAM running prod-side.** AMI `ami-0b7ec5ff47a1eff11` (§15 검증 stack), LT v26. Compute Tab Run SAM 클릭 → cold-start ~<N>s → 100-image SAM 끝 → 10-min idle self-terminate. Cost ~$<N>. measurement orchestrator (`measure-run.sh` / `measure-defer.py` / `worker/measurement.py`) deprecated — Compute Tab 클릭이 그 역할 흡수. Spec: [`2026-06-08-gpu-dispatcher-design.md`](superpowers/specs/2026-06-08-gpu-dispatcher-design.md) / Plan: [`2026-06-08-gpu-dispatcher.md`](superpowers/plans/2026-06-08-gpu-dispatcher.md). Detail: [`sam-ops.md §27`](sam-ops.md#27-1-click-sam-dispatch-acceptance--2026-06-08-success).
```

Refresh the "**Last updated**:" header at the top of `project-status.md` with one sentence reflecting this milestone (Korean tone matching existing pattern).

- [ ] **Step 9: Commit + push**

```bash
git add docs/sam-ops.md docs/project-status.md
git commit -m "docs(sam): §27 — 1-click SAM dispatch SUCCESS (prod path validated)

First successful end-to-end Compute Tab click on a 100-PNG scan.
gpu_launching SSE → ~<N>s cold start → gpu_ready → SAM inference →
step_completed → 10-min idle self-terminate. AMI
ami-0b7ec5ff47a1eff11 (§15 cu124 stack), LT v26.

Eight-deep onion-peel of attempts §18-§26 ended here: the prod path
that already shipped (api/routes/run.py + worker/launcher.py +
worker/tasks.py + SamRunPanel.tsx + idle-shutdown.timer) needed only
five tiny gap fixes (Tasks 1-4 of this plan) and one LT republish
(Task 5) to reach 1-click acceptance. The separate measurement
orchestrator from the prior plan is deprecated by this — follow-up
cleanup parking.

Plan: docs/superpowers/plans/2026-06-08-gpu-dispatcher.md (Task 6)
Spec: docs/superpowers/specs/2026-06-08-gpu-dispatcher-design.md
"

git push origin main
```

- [ ] **Step 10: Verify post-push terminate state**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-instances --instance-ids <i-from-step-4> \
    --query 'Reservations[0].Instances[0].State.Name' --output text
```

Expected: `terminated`. If still `shutting-down` 5+ min after step 9, manually `aws ec2 terminate-instances --instance-ids <id>` and document the lag in §27.

---

## Self-review

- **Spec coverage:**
  - §3 architecture — Tasks 1-5 cover the 5 boxes; Task 6 is e2e validation.
  - §4.1 gpu_lifecycle.py NEW — **DEVIATION**: launcher.py already covers; documented at plan top.
  - §4.2 routes/run.py edit — Task 2.
  - §4.3 sse.py whitelist — **DEVIATION**: PipelineProgressBridge has no whitelist; ProgressBridge gets two new methods (Task 1). Documented at plan top.
  - §4.4 worker/tasks.py gpu_ready — Task 3.
  - §4.5 user-data peft + symlink — already in main; verified Task 5 step 3.
  - §4.6 LT v26 publish — Task 5.
  - §4.7 SamRunPanel.tsx — Task 4.
  - §4.8 tests — embedded in Tasks 1-4 (TDD).
  - §5 data flow + §6 error handling — exercised by Task 6 acceptance.
  - §7 testing strategy — embedded.
  - §8 D1 (AMI), D5 (event types), D7 (LT v26) — Task 5.

- **Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in" / "add appropriate". Each step has either real code, a real command with expected output, or an observe-and-capture step in Task 6.

- **Type consistency:**
  - `LaunchResult` (action + reason + instance_id) — used consistently in Task 2 (existing class from launcher.py).
  - `gpu_launching` payload `{type, instance_id}` consistent in Tasks 1, 2, 4.
  - `gpu_ready` payload `{type, image_count}` consistent in Tasks 1, 2, 3, 4.
  - `bridge.emit_gpu_launching(instance_id: str)` and `bridge.emit_gpu_ready(image_count: int)` — same signatures across Tasks 1, 2.
  - `_emit_progress(*, run_id, payload)` — existing prod symbol, used in Task 3 unchanged.

- **Spec deviations are documented** at the plan top in two places (gpu_lifecycle.py NEW → use launcher.py instead; whitelist → method add). Both accomplish the spec intent without inventing new modules.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-08-gpu-dispatcher.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, PM reviews between tasks, fast iteration. Tasks 1-4 are repo edits with TDD; Task 5 is one AWS state change (LT v26 publish, free); Task 6 is the live acceptance smoke (~$1.50-2 spend).

**2. Inline Execution** — execute tasks in this session with checkpoints; slower turnover but PM context kept warm.

Which approach?
