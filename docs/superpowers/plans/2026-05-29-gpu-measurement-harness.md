# GPU Measurement Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run an 8-GPU 100-image SAM measurement on `g6e.48xlarge` with `boot_s` / `model_load_s` / `processing_s` / `total_s` separated, while leaving prod-grade unit methods in place so future model swaps drop into the existing compute pipeline without ad-hoc plumbing.

**Architecture:** Permanent timing instrumentation in `core/pipeline/sam.py` + `worker/tasks.py` writes structured rows to a new tiny `worker_events` table (refines spec D2 — see "Spec deviation" below). A new `worker/measurement.py` ships three pure-ish unit methods (`load_worker_env`, `resolve_model_meta`, `build_defer_payload`) that both this measurement AND any future prod GPU dispatcher call. Operator-facing one-shot `scripts/sam/measure-run.sh` orchestrates LT publish + spot launch (with on-demand auto-fallback) + SSM defer + 30 s polling loop with cost/wall caps + always-terminate trap. Instance-side `flake-analysis-abs-cap.timer` is a belt-and-suspenders self-terminate at T+60 min regardless of operator state.

**Tech Stack:** Python 3.11 (worker, scripts/sam/measure-defer.py) · Bash (scripts/sam/measure-run.sh, abs-cap-terminate.sh) · SQLAlchemy 2.x async ORM · Alembic · procrastinate · psycopg (sync inside worker) · boto3 (model resolver) · AWS CLI (SSM RunShellScript / EC2 RunInstances) · systemd (timer + service unit).

**Spec deviation (vs spec D2):** the spec said "`usage_events` is the timing sink — no new `measurement_runs` table." On implementation review, the existing `usage_events` row schema requires `user_id NOT NULL FK→users.id` and async-session writes via `usage.emit`. The worker is sync `psycopg` with no user context. Rather than mint a system user attribution that pollutes per-user analytics, we write timing markers to a **new minimal `worker_events` table** (id, ts, run_id, event, payload JSONB) with no user FK. This honours the spec's intent ("structured DB sink for SAM timing"), avoids overloading user-attributed analytics, and keeps the worker-side write path one short sync function (mirrors the existing `_emit_progress`). One alembic revision; ~8 lines of model.

**Spec D7 acceptance:** Task 13 manual smoke is the production measurement — 8-GPU `g6e.48xlarge` × `scan6-100` (100 PNG) finishing inside `--cost-cap-usd=5` with timing breakdown printed.

---

## File Structure

| Path | Responsibility | New/Modify |
|---|---|---|
| `alembic/versions/0006_worker_events.py` | `worker_events` table migration | NEW |
| `src/flake_analysis/db/models/worker_events.py` | `WorkerEvent` ORM model | NEW |
| `src/flake_analysis/db/models/__init__.py` | re-export `WorkerEvent` | MODIFY |
| `src/flake_analysis/worker/markers.py` | sync `emit_marker(run_id, event, payload)` helper (mirrors `_emit_progress`) | NEW |
| `src/flake_analysis/worker/measurement.py` | `load_worker_env`, `resolve_model_meta`, `build_defer_payload` — prod-grade unit methods | NEW |
| `src/flake_analysis/core/pipeline/sam.py` | 4 marker calls via `progress_callback("marker:...")` on both single-GPU and multi-GPU paths | MODIFY |
| `src/flake_analysis/worker/tasks.py` | `model_meta` arg, marker-aware progress callback wrapper, task_start/task_end emits | MODIFY |
| `scripts/sam/measure-defer.py` | one-shot SSM defer launcher (imports prod measurement module) | NEW |
| `scripts/sam/measure-run.sh` | operator-facing orchestrator | NEW |
| `scripts/aws/abs-cap-terminate.sh` | userdata-installed self-terminate script | NEW |
| `scripts/aws/sam-gpu-worker-userdata.sh` | install `flake-analysis-abs-cap.timer` + service | MODIFY |
| `tests/db/test_worker_events.py` | round-trip ORM test (PG-marked) | NEW |
| `tests/worker/test_markers.py` | `emit_marker` writes a row (PG-marked) | NEW |
| `tests/worker/test_measurement.py` | `load_worker_env`, `resolve_model_meta`, `build_defer_payload` units | NEW |
| `tests/worker/test_tasks.py` | extend with marker fan-out + model_meta tests | MODIFY |
| `tests/core/pipeline/test_sam_markers.py` | 4 marker calls in correct order on both branches | NEW |
| `tests/scripts/test_measure_run_dryrun.py` | bash dryrun spot check | NEW |

---

## Task 1: Worker events table — migration + ORM

**Files:**
- Create: `alembic/versions/0006_worker_events.py`
- Create: `src/flake_analysis/db/models/worker_events.py`
- Modify: `src/flake_analysis/db/models/__init__.py`
- Test: `tests/db/test_worker_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_worker_events.py
"""Round-trip test for the WorkerEvent ORM model + 0006 migration."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import WorkerEvent

pytestmark = pytest.mark.pg


async def test_worker_event_round_trip(pg_session: AsyncSession) -> None:
    row = WorkerEvent(
        run_id=42,
        event="marker:processing_start",
        payload={"weights": "merged_m3", "n_gpus": 8},
    )
    pg_session.add(row)
    await pg_session.flush()
    await pg_session.refresh(row)

    assert row.id is not None
    assert row.ts is not None  # server_default NOW()
    assert row.event == "marker:processing_start"
    assert row.payload == {"weights": "merged_m3", "n_gpus": 8}

    fetched = (await pg_session.execute(
        select(WorkerEvent).where(WorkerEvent.run_id == 42)
    )).scalar_one()
    assert fetched.id == row.id


async def test_worker_event_payload_optional(pg_session: AsyncSession) -> None:
    row = WorkerEvent(run_id=1, event="marker:model_load_start", payload=None)
    pg_session.add(row)
    await pg_session.flush()
    assert row.payload is None
```

- [ ] **Step 2: Run test to verify it fails (model not defined)**

Run: `pytest tests/db/test_worker_events.py -v -m pg`
Expected: FAIL — `ImportError: cannot import name 'WorkerEvent' from 'flake_analysis.db.models'`

- [ ] **Step 3: Write the ORM model**

```python
# src/flake_analysis/db/models/worker_events.py
"""WorkerEvent ORM model — sink for run_sam timing markers and lifecycle events.

Distinct from usage_events (which is per-user telemetry):
* No user_id FK — workers run without an authenticated user context.
* Indexed by (run_id, ts) for measurement-time analytics.
* Append-only; no updates, no deletes.

Writers: src/flake_analysis/worker/markers.py::emit_marker (sync psycopg)
         src/flake_analysis/worker/tasks.py (via emit_marker)

Schema lives in alembic 0006_worker_events.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flake_analysis.db.models.base import Base


class WorkerEvent(Base):
    """Append-only row for a single timing marker or worker lifecycle event."""

    __tablename__ = "worker_events"
    __table_args__ = (
        Index("worker_events_run_id_ts_idx", "run_id", text("ts DESC")),
        Index("worker_events_event_ts_idx", "event", text("ts DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
```

- [ ] **Step 4: Re-export from db.models package**

Add to the bottom of `src/flake_analysis/db/models/__init__.py`:

```python
from flake_analysis.db.models.worker_events import WorkerEvent  # noqa: E402

__all__ = [*__all__, "WorkerEvent"]
```

(If `__all__` is not defined in the file, append `WorkerEvent` to whatever export list / `from … import *` pattern the file uses. Read the file first to confirm shape.)

- [ ] **Step 5: Author the alembic migration**

```python
# alembic/versions/0006_worker_events.py
"""worker_events table for SAM run timing markers

Revision ID: 0006_worker_events
Revises: 0005_scan_status
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_worker_events"
down_revision = "0005_scan_status"  # confirm via `alembic heads` before applying
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "worker_events_run_id_ts_idx",
        "worker_events",
        ["run_id", sa.text("ts DESC")],
    )
    op.create_index(
        "worker_events_event_ts_idx",
        "worker_events",
        ["event", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("worker_events_event_ts_idx", table_name="worker_events")
    op.drop_index("worker_events_run_id_ts_idx", table_name="worker_events")
    op.drop_table("worker_events")
```

Verify the `down_revision` value first:

```bash
uv run alembic heads
```

If the head is something other than `0005_scan_status`, replace `down_revision` with the actual head value before continuing.

- [ ] **Step 6: Apply migration to local saa_test**

```bash
SAA_DATABASE_URL="postgresql+psycopg://saa_test:saa_test@127.0.0.1:5432/saa_test" \
  uv run alembic upgrade head
```

Expected output ends with `Running upgrade <prev> -> 0006_worker_events, worker_events table for SAM run timing markers`.

- [ ] **Step 7: Run test to verify pass**

Run: `uv run pytest tests/db/test_worker_events.py -v -m pg`
Expected: 2 passed.

- [ ] **Step 8: Run drift check**

```bash
uv run python scripts/check_alembic_drift.py
```

Expected: `OK — no drift`. If it reports drift, fix the model OR migration to match before committing.

- [ ] **Step 9: Commit**

```bash
git add alembic/versions/0006_worker_events.py \
        src/flake_analysis/db/models/worker_events.py \
        src/flake_analysis/db/models/__init__.py \
        tests/db/test_worker_events.py
git commit -m "feat(db): worker_events table for SAM run timing markers (#229 follow-up)

WorkerEvent is the timing sink for run_sam — distinct from usage_events
(which is per-user telemetry and requires a user_id FK we don't have
in the worker process). Append-only, indexed by (run_id, ts) and
(event, ts).

Spec: docs/superpowers/specs/2026-05-29-gpu-measurement-harness-design.md
Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 1)
"
```

---

## Task 2: `emit_marker` sync helper

**Files:**
- Create: `src/flake_analysis/worker/markers.py`
- Test: `tests/worker/test_markers.py`

The worker-side write path is sync psycopg, mirroring `_emit_progress` in `worker/tasks.py`. Centralised so the worker code path is unchanged regardless of whether the operator scrapes by SQL or by NOTIFY tail.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_markers.py
"""emit_marker writes a single worker_events row via sync psycopg."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import WorkerEvent
from flake_analysis.worker.markers import emit_marker

pytestmark = pytest.mark.pg


async def test_emit_marker_round_trip(pg_session: AsyncSession) -> None:
    # emit_marker is sync — call it bare. It opens its own short-lived
    # psycopg connection using SAA_DB_* env, same pattern as
    # _emit_progress in worker/tasks.py.
    emit_marker(run_id=99, event="marker:processing_start",
                payload={"n_gpus": 8})

    rows = (await pg_session.execute(
        select(WorkerEvent).where(WorkerEvent.run_id == 99)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].event == "marker:processing_start"
    assert rows[0].payload == {"n_gpus": 8}


async def test_emit_marker_optional_payload(pg_session: AsyncSession) -> None:
    emit_marker(run_id=100, event="marker:model_load_start", payload=None)
    rows = (await pg_session.execute(
        select(WorkerEvent).where(WorkerEvent.run_id == 100)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_markers.py -v -m pg`
Expected: FAIL — `ImportError: cannot import name 'emit_marker' from 'flake_analysis.worker.markers'`

- [ ] **Step 3: Write the helper**

```python
# src/flake_analysis/worker/markers.py
"""Sync sink for run_sam timing markers and lifecycle events.

Mirrors :func:`flake_analysis.worker.tasks._emit_progress` — opens a
short-lived psycopg connection using SAA_DB_* env, inserts one
worker_events row, autocommits.

Permanent in production: prod SAM runs also emit these markers, which
means SAM throughput regression analysis works in prod without any
measurement-only code path.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg

from flake_analysis.db.url import DbSettings, _require_ssl

logger = logging.getLogger(__name__)


def emit_marker(*, run_id: int, event: str, payload: dict[str, Any] | None = None) -> None:
    """Insert one row into worker_events.

    Never raises — marker emit failures are logged and swallowed so they
    cannot fail an in-flight SAM job. Same defensive posture as
    ``_emit_progress``.

    Args:
        run_id: The same run_id the deferred run_sam task got.
        event: Short string like "marker:processing_start" or
            "sam_task_end". Goes into the event column verbatim.
        payload: Optional JSON-serialisable dict; goes into payload JSONB.
    """
    s = DbSettings()
    conn_kwargs: dict[str, Any] = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if _require_ssl(s.db_host):
        conn_kwargs["sslmode"] = "require"
    if s.db_user:
        conn_kwargs["user"] = s.db_user
    if s.db_password:
        conn_kwargs["password"] = s.db_password

    payload_json = json.dumps(payload, default=str) if payload is not None else None
    try:
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO worker_events (run_id, event, payload) "
                    "VALUES (%s, %s, %s::jsonb)",
                    (run_id, event, payload_json),
                )
    except Exception:  # noqa: BLE001 — never let marker emit failures
        logger.exception("emit_marker failed: run_id=%s event=%s", run_id, event)
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/worker/test_markers.py -v -m pg`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/worker/markers.py tests/worker/test_markers.py
git commit -m "feat(worker): emit_marker sync helper for worker_events sink (#229 follow-up)

Same short-lived-psycopg pattern as _emit_progress, but writes to
worker_events instead of issuing PG NOTIFY. Used by run_sam to log
timing markers (model_load_start / processing_start / processing_end)
and task lifecycle (sam_task_start / sam_task_end). Defensive: never
raises, all failures logged.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 2)
"
```

---

## Task 3: `core/pipeline/sam.py` — add 4 marker calls

**Files:**
- Modify: `src/flake_analysis/core/pipeline/sam.py`
- Test: `tests/core/pipeline/test_sam_markers.py`

Add markers via the existing `progress_callback` parameter so the channel is unchanged. Caller decides what to do with `marker:*` messages (Task 4 wires them to `emit_marker`).

- [ ] **Step 1: Read the existing function shape**

Read: `src/flake_analysis/core/pipeline/sam.py` lines 250–460. Confirm the function signatures of `_run_sam_multi_gpu`, `_vendor_infer`, and `run_sam` so the inserts in step 3 match the actual control flow.

- [ ] **Step 2: Write the failing test**

```python
# tests/core/pipeline/test_sam_markers.py
"""Verify marker:* progress_callback calls fire in correct order."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _collect_messages(progress_calls):
    return [msg for (_pct, msg) in progress_calls]


def test_run_sam_single_gpu_emits_three_markers(monkeypatch, tmp_path: Path) -> None:
    """The single-GPU branch emits model_load_start, processing_start,
    processing_end through progress_callback, in that order, before
    returning."""
    from flake_analysis.core.pipeline import sam as sam_mod

    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    monkeypatch.setattr("torch.cuda.device_count", lambda: 0)

    fake_summary = {"images": 0, "masks_total": 0, "errors": 0, "per_image": {}}

    def fake_vendor_infer(*args, progress_callback=None, **kwargs):
        # vendor inference fires no progress on a 0-image dir; the markers
        # must come from the wrapper code, not vendor.
        return fake_summary

    monkeypatch.setattr(sam_mod, "_vendor_infer", fake_vendor_infer)

    progress_calls: list[tuple[float, str]] = []
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    weights = tmp_path / "w.pt"
    weights.touch()

    sam_mod.run_sam(
        images_dir=images_dir,
        weights_path=weights,
        out_dir=out_dir,
        progress_callback=lambda p, m: progress_calls.append((p, m)),
    )

    msgs = _collect_messages(progress_calls)
    markers = [m for m in msgs if m.startswith("marker:")]
    assert markers == [
        "marker:model_load_start",
        "marker:processing_start",
        "marker:processing_end",
    ]


def test_run_sam_multi_gpu_emits_three_markers(monkeypatch, tmp_path: Path) -> None:
    """The multi-GPU branch emits the same three markers via the same
    progress_callback channel."""
    from flake_analysis.core.pipeline import sam as sam_mod

    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("torch.cuda.device_count", lambda: 8)

    fake_results: list[dict] = []  # vendor returns empty list for 0 images

    def fake_run_multi(images, output_dir, config, num_gpus):
        return fake_results

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi)

    progress_calls: list[tuple[float, str]] = []
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    weights = tmp_path / "w.pt"
    weights.touch()

    sam_mod.run_sam(
        images_dir=images_dir,
        weights_path=weights,
        out_dir=out_dir,
        progress_callback=lambda p, m: progress_calls.append((p, m)),
    )

    msgs = _collect_messages(progress_calls)
    markers = [m for m in msgs if m.startswith("marker:")]
    assert markers == [
        "marker:model_load_start",
        "marker:processing_start",
        "marker:processing_end",
    ]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/core/pipeline/test_sam_markers.py -v`
Expected: FAIL — markers list is empty (no progress_callback("marker:...") calls yet).

- [ ] **Step 4: Insert the marker calls**

In `src/flake_analysis/core/pipeline/sam.py`:

In `_run_sam_multi_gpu(images_dir, weights_path, out_dir, n_gpus, progress_callback)` — at the very top of the function body, before any other work:

```python
    if progress_callback is not None:
        progress_callback(0.0, "marker:model_load_start")
```

Immediately before the call to `_vendor_run_multi_process(...)`:

```python
    if progress_callback is not None:
        progress_callback(0.0, "marker:processing_start")
```

Immediately after `_vendor_run_multi_process(...)` returns (before the result-translation code):

```python
    if progress_callback is not None:
        progress_callback(1.0, "marker:processing_end")
```

For the single-GPU branch — locate the block that calls `_vendor_infer(...)` inside `run_sam`. Wrap it like:

```python
    if progress_callback is not None:
        progress_callback(0.0, "marker:model_load_start")
        progress_callback(0.0, "marker:processing_start")
    result = _vendor_infer(...)
    if progress_callback is not None:
        progress_callback(1.0, "marker:processing_end")
```

(The two pre-call markers fire back-to-back in the single-GPU path because vendor's `_vendor_infer` does the model load and processing in one opaque call — we only have one boundary to mark, so model_load_start and processing_start fire together. Multi-GPU has separate boundaries.)

- [ ] **Step 5: Run test to verify pass**

Run: `uv run pytest tests/core/pipeline/test_sam_markers.py -v`
Expected: 2 passed.

- [ ] **Step 6: Run the existing pipeline tests to verify no regression**

Run: `uv run pytest tests/core/pipeline/ -v`
Expected: all green; the new markers should not perturb existing tests.

- [ ] **Step 7: Commit**

```bash
git add src/flake_analysis/core/pipeline/sam.py tests/core/pipeline/test_sam_markers.py
git commit -m "feat(sam): emit timing markers via progress_callback (#229 follow-up)

Three boundary markers fired through the existing progress_callback:
* marker:model_load_start  — entry to multi-GPU branch / vendor infer
* marker:processing_start  — immediately before run_multi_process /
                             _vendor_infer call
* marker:processing_end    — immediately after the same returns

Caller (worker/tasks.py) routes marker:* prefixed messages to a
separate sink (worker_events) — non-marker progress flows through to
SSE as before. Single-GPU path collapses model_load and processing
boundaries since vendor does both inside one opaque call.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 3)
"
```

---

## Task 4: `worker/tasks.py::run_sam` — model_meta + marker fan-out

**Files:**
- Modify: `src/flake_analysis/worker/tasks.py`
- Modify: `tests/worker/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/worker/test_tasks.py`:

```python
def test_run_sam_emits_task_lifecycle_events(monkeypatch, pg_session_sync):
    """run_sam emits sam_task_start at entry, sam_task_end at exit, both
    persisted via emit_marker (worker_events rows)."""
    from flake_analysis.worker import tasks
    from flake_analysis.db.models import WorkerEvent
    from sqlalchemy import select

    captured: list[dict] = []

    def fake_emit_marker(*, run_id, event, payload=None):
        captured.append({"run_id": run_id, "event": event, "payload": payload})

    monkeypatch.setattr(tasks, "emit_marker", fake_emit_marker)
    monkeypatch.setattr(tasks, "_emit_progress", lambda **kw: None)

    fake_result = {"images": 5, "masks_total": 12, "errors": 0, "per_image": {}}
    monkeypatch.setattr(
        tasks,
        "run_sam_step",
        lambda *, raw_images_dir, analysis_folder, weights_path, device,
                 progress_callback: fake_result,
    )

    tasks.run_sam(
        run_id=7,
        raw_images_dir="/tmp/raw",
        analysis_folder="/tmp/an",
        weights_path="/opt/sam/weights/m.pt",
        model_meta={"name": "merged_m3", "sha256": "abc", "source_uri": "s3://b/k"},
    )

    events = [c["event"] for c in captured]
    assert events[0] == "sam_task_start"
    assert events[-1] == "sam_task_end"
    assert captured[0]["payload"]["model_meta"] == {
        "name": "merged_m3", "sha256": "abc", "source_uri": "s3://b/k",
    }
    assert captured[-1]["payload"]["status"] == "success"
    assert captured[-1]["payload"]["masks_total"] == 12


def test_run_sam_routes_marker_progress_to_emit_marker(monkeypatch):
    """When the runner emits a progress message starting with 'marker:',
    the wrapper routes it to emit_marker (NOT _emit_progress)."""
    from flake_analysis.worker import tasks

    marker_events: list[str] = []
    progress_events: list[str] = []

    monkeypatch.setattr(
        tasks, "emit_marker",
        lambda *, run_id, event, payload=None: marker_events.append(event),
    )
    monkeypatch.setattr(
        tasks, "_emit_progress",
        lambda *, run_id, payload: progress_events.append(payload.get("message", "")),
    )

    def fake_runner(*, raw_images_dir, analysis_folder, weights_path,
                   device, progress_callback):
        progress_callback(0.0, "starting")
        progress_callback(0.1, "marker:model_load_start")
        progress_callback(0.5, "halfway")
        progress_callback(1.0, "marker:processing_end")
        return {"images": 1, "masks_total": 0, "errors": 0, "per_image": {}}

    monkeypatch.setattr(tasks, "run_sam_step", fake_runner)

    tasks.run_sam(run_id=8, raw_images_dir="/x", analysis_folder="/y",
                  weights_path="/z.pt")

    # Markers fan out to emit_marker, including task_start/task_end bookends.
    assert "marker:model_load_start" in marker_events
    assert "marker:processing_end" in marker_events
    # Non-marker progress goes to _emit_progress as plain progress messages.
    assert "starting" in progress_events
    assert "halfway" in progress_events
    # Markers must NOT leak into _emit_progress.
    assert "marker:model_load_start" not in progress_events
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_tasks.py -v -k "task_lifecycle or marker_progress"`
Expected: both FAIL — `model_meta` is not a kwarg yet, and the dispatch logic doesn't route `marker:*` separately.

- [ ] **Step 3: Modify `run_sam`**

In `src/flake_analysis/worker/tasks.py`:

Add the import near the top (with the other imports):

```python
from flake_analysis.worker.markers import emit_marker
```

Replace the `run_sam` task definition with:

```python
@app.task(queue="gpu", name="run_sam")
def run_sam(
    *,
    run_id: int,
    raw_images_dir: str,
    analysis_folder: str,
    weights_path: str,
    device: str | None = None,
    model_meta: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run SAM2 inference, fan-out progress + markers, return runner result.

    Marker fan-out: progress messages whose text starts with ``"marker:"``
    are routed to :func:`emit_marker` (worker_events sink) instead of
    SSE NOTIFY. All other progress messages flow through the existing
    SSE path unchanged.

    Lifecycle: emits ``sam_task_start`` at entry (with model_meta and
    inputs in the payload) and ``sam_task_end`` at exit (with status,
    masks_total, errors). These are sufficient for offline analysis
    without joining against procrastinate_jobs.
    """
    emit_marker(
        run_id=run_id,
        event="sam_task_start",
        payload={
            "raw_images_dir": raw_images_dir,
            "analysis_folder": analysis_folder,
            "weights_path": weights_path,
            "model_meta": model_meta,
        },
    )

    def _on_progress(progress: float, message: str) -> None:
        msg = str(message)
        if msg.startswith("marker:"):
            try:
                emit_marker(run_id=run_id, event=msg, payload=None)
            except Exception:  # noqa: BLE001
                logger.exception("marker emit failed for run_id=%s", run_id)
            return
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "progress",
                    "progress": float(progress),
                    "message": msg,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("progress emit failed for run_id=%s", run_id)

    status = "success"
    masks_total = 0
    errors = 0
    try:
        result = run_sam_step(
            raw_images_dir=raw_images_dir,
            analysis_folder=analysis_folder,
            weights_path=weights_path,
            device=device,
            progress_callback=_on_progress,
        )
        masks_total = int(result.get("masks_total", 0) or 0)
        errors = int(result.get("errors", 0) or 0)
    except BaseException as exc:  # noqa: BLE001
        status = "failed"
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "error",
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("error emit failed for run_id=%s", run_id)
        emit_marker(
            run_id=run_id,
            event="sam_task_end",
            payload={"status": status, "masks_total": masks_total,
                     "errors": errors, "exc": type(exc).__name__},
        )
        raise

    try:
        _emit_progress(
            run_id=run_id,
            payload={"type": "completed", "result": result},
        )
    except Exception:  # noqa: BLE001
        logger.exception("completed emit failed for run_id=%s", run_id)

    emit_marker(
        run_id=run_id,
        event="sam_task_end",
        payload={"status": status, "masks_total": masks_total,
                 "errors": errors},
    )
    return result
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/worker/test_tasks.py -v`
Expected: existing tests still pass + 2 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/worker/tasks.py tests/worker/test_tasks.py
git commit -m "feat(worker): run_sam emits task lifecycle + routes marker progress (#229 follow-up)

* New optional model_meta kwarg ({name, sha256, source_uri}) — recorded
  in sam_task_start payload for per-model timing analytics.
* Progress messages prefixed 'marker:' route to emit_marker
  (worker_events sink) and do NOT leak into the SSE channel.
* sam_task_start at entry, sam_task_end at exit (success or failure)
  with status/masks_total/errors for offline analysis without
  joining procrastinate_jobs.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 4)
"
```

---

## Task 5: `worker/measurement.py::load_worker_env`

**Files:**
- Create: `src/flake_analysis/worker/measurement.py` (start of file)
- Test: `tests/worker/test_measurement.py` (start of file)

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_measurement.py
"""Unit tests for the prod-grade measurement utility module."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_load_worker_env_basic(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "SAA_DB_HOST=qpressdb.example.com\n"
        "SAA_DB_PORT=5432\n"
        "SAA_DB_NAME=qpress\n"
    )
    out = load_worker_env(env_file)
    assert out == {
        "SAA_DB_HOST": "qpressdb.example.com",
        "SAA_DB_PORT": "5432",
        "SAA_DB_NAME": "qpress",
    }


def test_load_worker_env_quoted_values(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        'SAA_DB_PASSWORD="hunter2 with spaces"\n'
        "SAA_DB_USER='uname'\n"
    )
    out = load_worker_env(env_file)
    assert out["SAA_DB_PASSWORD"] == "hunter2 with spaces"
    assert out["SAA_DB_USER"] == "uname"


def test_load_worker_env_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "# top comment\n"
        "\n"
        "SAA_DB_HOST=h\n"
        "  # indented comment\n"
        "SAA_DB_PORT=5432\n"
    )
    out = load_worker_env(env_file)
    assert out == {"SAA_DB_HOST": "h", "SAA_DB_PORT": "5432"}


def test_load_worker_env_missing_file_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    with pytest.raises(FileNotFoundError):
        load_worker_env(tmp_path / "nonexistent.env")


def test_load_worker_env_malformed_line_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text("LINE_WITHOUT_EQUALS\nSAA_DB_HOST=h\n")
    with pytest.raises(ValueError, match="malformed"):
        load_worker_env(env_file)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_measurement.py -v`
Expected: 5 FAIL — `ImportError: cannot import name 'load_worker_env' from 'flake_analysis.worker.measurement'`

- [ ] **Step 3: Implement `load_worker_env`**

```python
# src/flake_analysis/worker/measurement.py
"""Measurement & model-swap utilities — prod-grade unit methods.

These three functions are the boundary between systemd-managed worker
state and ad-hoc Python (measurement scripts, future prod dispatcher).

* :func:`load_worker_env`     — bridge systemd EnvironmentFile= → os.environ
* :func:`resolve_model_meta`  — local path or s3:// URI → deterministic local
                                artifact + name/sha256/source_uri metadata
* :func:`build_defer_payload` — kwargs for app.configure_task('run_sam').defer

Designed to be called from:
* scripts/sam/measure-defer.py (this plan)
* future prod GPU dispatcher (out of scope here)
"""
from __future__ import annotations

from pathlib import Path


def load_worker_env(env_file: Path = Path("/etc/flake-analysis-worker.env")) -> dict[str, str]:
    """Parse a systemd-style EnvironmentFile into a dict of env vars.

    Supports::

        KEY=value
        KEY="quoted value with spaces"
        KEY='single quoted'
        # comment lines (any leading whitespace)
        <blank lines>

    Raises:
        FileNotFoundError: env_file does not exist.
        ValueError: any non-blank, non-comment line is missing '='.
    """
    env_file = Path(env_file)
    if not env_file.exists():
        raise FileNotFoundError(f"worker env file not found: {env_file}")

    out: dict[str, str] = {}
    for lineno, raw in enumerate(env_file.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"malformed line {lineno} in {env_file}: missing '='"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching surrounding quotes — single or double.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/worker/test_measurement.py -v -k load_worker_env`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/worker/measurement.py tests/worker/test_measurement.py
git commit -m "feat(worker): measurement.load_worker_env (the §19/§20 fix)

Bridges systemd EnvironmentFile= → os.environ for ad-hoc Python
launchers run via SSM that don't inherit the worker service env.
This is the missing piece that aborted #229 retry2 — the
/proc/PID/environ pattern doesn't propagate EnvironmentFile= contents.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 5)
"
```

---

## Task 6: `worker/measurement.py::resolve_model_meta`

**Files:**
- Modify: `src/flake_analysis/worker/measurement.py`
- Modify: `tests/worker/test_measurement.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/worker/test_measurement.py`:

```python
def test_resolve_model_meta_local_with_sidecar(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    pt = tmp_path / "merged_m3.pt"
    pt.write_bytes(b"fake-weights")
    sidecar = tmp_path / "merged_m3.pt.sha256"
    sidecar.write_text(
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef  merged_m3.pt\n"
    )

    meta = resolve_model_meta(str(pt))
    assert meta["name"] == "merged_m3"
    assert meta["sha256"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert meta["source_uri"] == f"file://{pt}"
    assert meta["local_path"] == str(pt)


def test_resolve_model_meta_local_missing_sidecar_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    pt = tmp_path / "no_sidecar.pt"
    pt.write_bytes(b"x")
    with pytest.raises(ValueError, match="sidecar"):
        resolve_model_meta(str(pt))


def test_resolve_model_meta_s3_uri_downloads(monkeypatch, tmp_path: Path) -> None:
    """S3 URI: downloads .pt + reads .sha256 sidecar, returns metadata
    with the s3:// URI as source_uri."""
    import boto3
    from moto import mock_aws

    from flake_analysis.worker.measurement import resolve_model_meta

    monkeypatch.setattr(
        "flake_analysis.worker.measurement._WEIGHTS_LOCAL_DIR",
        tmp_path / "weights",
    )

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-2")
        s3.create_bucket(
            Bucket="qpress-uploads",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        s3.put_object(
            Bucket="qpress-uploads",
            Key="internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt",
            Body=b"fake-weights",
        )
        s3.put_object(
            Bucket="qpress-uploads",
            Key="internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt.sha256",
            Body=b"3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc  sam2.1_hiera_large.merged_m3.3ec586fc.pt\n",
        )

        meta = resolve_model_meta(
            "s3://qpress-uploads/internal/sam/merged_m3/"
            "sam2.1_hiera_large.merged_m3.3ec586fc.pt"
        )

    assert meta["name"] == "sam2.1_hiera_large.merged_m3.3ec586fc"
    assert meta["sha256"] == "3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc"
    assert meta["source_uri"].startswith("s3://qpress-uploads/")
    assert Path(meta["local_path"]).exists()
    assert Path(meta["local_path"]).read_bytes() == b"fake-weights"


def test_resolve_model_meta_invalid_uri_raises() -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    with pytest.raises(ValueError, match="weights_uri"):
        resolve_model_meta("ftp://example.com/x.pt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_measurement.py -v -k resolve_model_meta`
Expected: 4 FAIL — `resolve_model_meta` not defined.

- [ ] **Step 3: Implement `resolve_model_meta`**

Append to `src/flake_analysis/worker/measurement.py`:

```python
import re

# Default download dir on the GPU worker. Overridable in tests via monkeypatch.
_WEIGHTS_LOCAL_DIR = Path("/opt/sam/weights")
_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def _read_sidecar_sha256(sidecar_text: str) -> str:
    """Parse the first 64-hex token of a .sha256 sidecar file."""
    match = re.search(r"\b([0-9a-f]{64})\b", sidecar_text)
    if not match:
        raise ValueError(f"no sha256 in sidecar: {sidecar_text!r}")
    return match.group(1)


def resolve_model_meta(weights_uri: str) -> dict[str, str]:
    """Resolve a weights reference into a deterministic local artifact + metadata.

    Args:
        weights_uri: Either an absolute local path to a .pt file, or an
            ``s3://bucket/prefix/name.pt`` URI. A sidecar
            ``<name>.pt.sha256`` is required at the same prefix; the
            sidecar must contain a 64-hex sha256 token.

    Returns:
        Dict with keys ``name``, ``sha256``, ``source_uri``, ``local_path``.

    Raises:
        ValueError: invalid scheme, missing sidecar, malformed sidecar.
    """
    if weights_uri.startswith("s3://"):
        return _resolve_s3(weights_uri)
    if weights_uri.startswith("/") or weights_uri.startswith("file://"):
        local = weights_uri[len("file://"):] if weights_uri.startswith("file://") else weights_uri
        return _resolve_local(Path(local))
    raise ValueError(f"unsupported weights_uri scheme: {weights_uri!r}")


def _resolve_local(pt_path: Path) -> dict[str, str]:
    if not pt_path.exists():
        raise ValueError(f"weights_uri points to missing file: {pt_path}")
    sidecar = pt_path.with_name(pt_path.name + ".sha256")
    if not sidecar.exists():
        raise ValueError(f"sidecar sha256 file missing: {sidecar}")
    sha = _read_sidecar_sha256(sidecar.read_text())
    return {
        "name": pt_path.stem,
        "sha256": sha,
        "source_uri": f"file://{pt_path}",
        "local_path": str(pt_path),
    }


def _resolve_s3(s3_uri: str) -> dict[str, str]:
    import boto3

    match = _S3_URI_RE.match(s3_uri)
    if not match:
        raise ValueError(f"malformed s3 URI: {s3_uri!r}")
    bucket, key = match.group(1), match.group(2)
    if not key.endswith(".pt"):
        raise ValueError(f"weights URI must end in .pt: {s3_uri!r}")

    s3 = boto3.client("s3")
    sidecar_obj = s3.get_object(Bucket=bucket, Key=key + ".sha256")
    sha = _read_sidecar_sha256(sidecar_obj["Body"].read().decode("utf-8"))

    _WEIGHTS_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    local_path = _WEIGHTS_LOCAL_DIR / Path(key).name
    # Idempotent: skip download if local sha already matches sidecar.
    if local_path.exists():
        import hashlib
        h = hashlib.sha256()
        with local_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() == sha.lower():
            return {
                "name": Path(key).stem,
                "sha256": sha,
                "source_uri": s3_uri,
                "local_path": str(local_path),
            }

    s3.download_file(bucket, key, str(local_path))
    return {
        "name": Path(key).stem,
        "sha256": sha,
        "source_uri": s3_uri,
        "local_path": str(local_path),
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/worker/test_measurement.py -v -k resolve_model_meta`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/worker/measurement.py tests/worker/test_measurement.py
git commit -m "feat(worker): measurement.resolve_model_meta — local + s3 weights resolver

Single entry point for adding a new model — point measure-run.sh at
the new S3 URI, sidecar .sha256 enforces integrity, idempotent
download skips on local sha match. Future prod dispatcher will call
this same function.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 6)
"
```

---

## Task 7: `worker/measurement.py::build_defer_payload`

**Files:**
- Modify: `src/flake_analysis/worker/measurement.py`
- Modify: `tests/worker/test_measurement.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_build_defer_payload_shape(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import build_defer_payload

    payload = build_defer_payload(
        run_id=42,
        scan_id=287,
        model_meta={
            "name": "merged_m3",
            "sha256": "abc",
            "source_uri": "s3://qpress-uploads/internal/sam/merged_m3/x.pt",
            "local_path": "/opt/sam/weights/x.pt",
        },
        dataset_dir=tmp_path / "dataset",
        analysis_folder=tmp_path / "an",
    )

    assert payload == {
        "run_id": 42,
        "raw_images_dir": str(tmp_path / "dataset"),
        "analysis_folder": str(tmp_path / "an"),
        "weights_path": "/opt/sam/weights/x.pt",
        "model_meta": {
            "name": "merged_m3",
            "sha256": "abc",
            "source_uri": "s3://qpress-uploads/internal/sam/merged_m3/x.pt",
        },
    }


def test_build_defer_payload_missing_local_path_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import build_defer_payload

    with pytest.raises(ValueError, match="local_path"):
        build_defer_payload(
            run_id=1, scan_id=1,
            model_meta={"name": "x", "sha256": "y", "source_uri": "z"},
            dataset_dir=tmp_path, analysis_folder=tmp_path,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_measurement.py -v -k build_defer_payload`
Expected: 2 FAIL — function not defined.

- [ ] **Step 3: Implement `build_defer_payload`**

Append to `src/flake_analysis/worker/measurement.py`:

```python
def build_defer_payload(
    *,
    run_id: int,
    scan_id: int,  # noqa: ARG001 — reserved for future use; kept for caller stability
    model_meta: dict,
    dataset_dir: Path,
    analysis_folder: Path,
) -> dict:
    """Construct kwargs for ``app.configure_task('run_sam', queue='gpu').defer_async``.

    Pure function: no DB, no IO. ``model_meta`` must include ``local_path``
    (set by :func:`resolve_model_meta`); only the user-facing keys
    (name/sha256/source_uri) propagate into the deferred payload — local_path
    is consumed here and stripped (the path is what becomes ``weights_path``).
    """
    if "local_path" not in model_meta:
        raise ValueError("model_meta missing 'local_path' — call resolve_model_meta first")
    return {
        "run_id": run_id,
        "raw_images_dir": str(dataset_dir),
        "analysis_folder": str(analysis_folder),
        "weights_path": model_meta["local_path"],
        "model_meta": {
            "name": model_meta["name"],
            "sha256": model_meta["sha256"],
            "source_uri": model_meta["source_uri"],
        },
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/worker/test_measurement.py -v`
Expected: all measurement.py tests pass (load_worker_env + resolve_model_meta + build_defer_payload).

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/worker/measurement.py tests/worker/test_measurement.py
git commit -m "feat(worker): measurement.build_defer_payload — central kwargs constructor

Pure function — both measurement script and future prod dispatcher
call this. Prevents defer-shape drift between callers.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 7)
"
```

---

## Task 8: `scripts/sam/measure-defer.py` SSM-pushed launcher

**Files:**
- Create: `scripts/sam/measure-defer.py`

This file is **not** unit-tested — it's a thin glue script whose pieces (load_worker_env, resolve_model_meta, build_defer_payload) are unit-tested in Tasks 5–7. End-to-end coverage comes from the manual smoke run in Task 13.

- [ ] **Step 1: Create the launcher**

```python
#!/usr/bin/env python3
"""One-shot defer launcher executed via SSM on the GPU worker instance.

Loads worker env from /etc/flake-analysis-worker.env, resolves the
model URI to a local artifact + metadata, defers run_sam to the
procrastinate gpu queue, prints job_id, exits.

Re-uses prod-grade unit methods from flake_analysis.worker.measurement.
DO NOT add measurement-specific logic here — this script is intentionally
thin so future prod GPU dispatcher can call the same module.

Usage (executed via SSM RunShellScript on the GPU worker)::

    sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 \\
        /tmp/measure-defer.py \\
        --weights-uri s3://qpress-uploads/internal/sam/merged_m3/...pt \\
        --dataset-dir /opt/sam/dataset/scan6-100 \\
        --analysis-folder /opt/sam/runs/<RUN_ID> \\
        --run-id <RUN_ID> \\
        --scan-id <SCAN_ID>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure the repo `src/` is on sys.path. AMI lays it out at this path.
_REPO_SRC = Path("/opt/sam/stand-alone-analyzer/src")
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from flake_analysis.worker.measurement import (  # noqa: E402
    build_defer_payload, load_worker_env, resolve_model_meta,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SAM measurement defer launcher")
    p.add_argument("--weights-uri", required=True,
                   help="s3:// URI or local path to a .pt weights file")
    p.add_argument("--dataset-dir", required=True,
                   help="local directory of .png input images")
    p.add_argument("--analysis-folder", required=True,
                   help="local output folder for SAM results")
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--scan-id", type=int, required=True)
    p.add_argument("--worker-env-file",
                   default="/etc/flake-analysis-worker.env",
                   help="systemd EnvironmentFile to inherit RDS creds from")
    return p.parse_args()


async def _defer(payload: dict) -> int:
    # Imports happen AFTER load_worker_env() updates os.environ so that
    # DbSettings() picks up SAA_DB_*.
    from flake_analysis.worker.app import app

    async with app.open_async():
        job_id = await app.configure_task(
            name="run_sam", queue="gpu",
        ).defer_async(**payload)
    return int(job_id)


def main() -> int:
    args = _parse_args()
    os.environ.update(load_worker_env(Path(args.worker_env_file)))

    model_meta = resolve_model_meta(args.weights_uri)
    payload = build_defer_payload(
        run_id=args.run_id,
        scan_id=args.scan_id,
        model_meta=model_meta,
        dataset_dir=Path(args.dataset_dir),
        analysis_folder=Path(args.analysis_folder),
    )

    job_id = asyncio.run(_defer(payload))
    print(f"job_id={job_id}")
    print(f"model_name={model_meta['name']}")
    print(f"model_sha256={model_meta['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Mark executable**

```bash
chmod +x scripts/sam/measure-defer.py
```

- [ ] **Step 3: Local syntax check**

```bash
uv run python -c "import ast; ast.parse(open('scripts/sam/measure-defer.py').read())"
```

Expected: silent (no errors).

- [ ] **Step 4: Commit**

```bash
git add scripts/sam/measure-defer.py
git commit -m "feat(scripts): measure-defer.py — SSM-pushed defer launcher

Thin glue script — imports prod-grade flake_analysis.worker.measurement
and defers a run_sam task. No measurement-specific logic; future prod
GPU dispatcher will call the same module functions.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 8)
"
```

---

## Task 9: Instance-side `abs-cap` self-terminate (script + systemd units in user-data)

**Files:**
- Create: `scripts/aws/abs-cap-terminate.sh`
- Modify: `scripts/aws/sam-gpu-worker-userdata.sh`

The bash script ships in the repo so it's auditable; user-data installs it on each cold launch (and the next AMI bake will pick it up automatically).

- [ ] **Step 1: Create the terminator script**

```bash
# scripts/aws/abs-cap-terminate.sh
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
```

- [ ] **Step 2: Mark executable**

```bash
chmod +x scripts/aws/abs-cap-terminate.sh
```

- [ ] **Step 3: Modify `sam-gpu-worker-userdata.sh` to install the timer**

Read `scripts/aws/sam-gpu-worker-userdata.sh` and find the existing block that writes `flake-analysis-idle-shutdown.service` + `.timer`. Just below that block (before the section that enables/starts the worker), append the abs-cap install.

Add near the top of the file (with other tunables):

```bash
ABS_CAP_MIN="${ABS_CAP_MIN:-60}"
```

In the section that writes systemd units, add (mirror the idle-shutdown block — exact location: after `systemctl enable --now flake-analysis-idle-shutdown.timer` and before the worker-service install):

```bash
echo "[8b/8] flake-analysis-abs-cap timer (T+${ABS_CAP_MIN}min)"

install -m 0755 \
    "${REPO_DIR}/scripts/aws/abs-cap-terminate.sh" \
    /usr/local/bin/abs-cap-terminate.sh

cat > /etc/systemd/system/flake-analysis-abs-cap.service <<'UNIT'
[Unit]
Description=Absolute wall-clock cap — terminate this instance
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/abs-cap-terminate.sh
UNIT

cat > /etc/systemd/system/flake-analysis-abs-cap.timer <<UNIT
[Unit]
Description=Fire abs-cap.service ${ABS_CAP_MIN} min after boot

[Timer]
OnBootSec=${ABS_CAP_MIN}min
Unit=flake-analysis-abs-cap.service
AccuracySec=10s

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now flake-analysis-abs-cap.timer
```

- [ ] **Step 4: Local lint**

```bash
shellcheck scripts/aws/abs-cap-terminate.sh
shellcheck scripts/aws/sam-gpu-worker-userdata.sh
```

Expected: no warnings (or pre-existing warnings only — fix only those introduced by your edit).

- [ ] **Step 5: Commit**

```bash
git add scripts/aws/abs-cap-terminate.sh scripts/aws/sam-gpu-worker-userdata.sh
git commit -m "feat(devops): instance-side abs-cap self-terminate timer (#229 §20 fix)

flake-analysis-abs-cap.timer fires \${ABS_CAP_MIN:-60} min after boot
and unconditionally terminates the instance. Belt-and-suspenders
against operator-session death — measure-run.sh polling loop already
enforces wall/cost caps from the operator side, but a dead operator
session was exactly the #229 retry2 failure mode (53 min idle on
on-demand g6e.48xlarge, \$7.09 lost).

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 9)
"
```

---

## Task 10: `scripts/sam/measure-run.sh` operator orchestrator

**Files:**
- Create: `scripts/sam/measure-run.sh`
- Test: `tests/scripts/test_measure_run_dryrun.py`

- [ ] **Step 1: Write the failing dryrun test**

```python
# tests/scripts/test_measure_run_dryrun.py
"""measure-run.sh --dryrun prints intended commands and exits 0 without
contacting AWS. Smoke-level: confirms argparse + phase ordering."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sam" / "measure-run.sh"


@pytest.mark.skipif(not SCRIPT.exists(), reason="script not yet present")
def test_dryrun_prints_phases_and_exits_zero(tmp_path: Path) -> None:
    env = {**os.environ, "AWS_PROFILE": "qpress", "AWS_REGION": "us-east-2"}
    result = subprocess.run(
        [
            str(SCRIPT),
            "--weights",
            "s3://qpress-uploads/internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt",
            "--dataset",
            "s3://qpress-uploads/internal/sam/scan6-100/",
            "--instance-type", "g6e.48xlarge",
            "--cost-cap-usd", "5",
            "--wall-cap-min", "60",
            "--dryrun",
        ],
        env=env, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    # Phases must run in order, regardless of formatting.
    for phase in ["[phase=1]", "[phase=2]", "[phase=3]", "[phase=4]",
                  "[phase=11]"]:
        assert phase in out, f"missing {phase} in dryrun output:\n{out}"
    # Dryrun must NOT call run-instances.
    assert "Would: aws ec2 run-instances" in out
    assert "Would: aws ec2 terminate-instances" in out


def test_dryrun_missing_required_arg_exits_nonzero() -> None:
    if not SCRIPT.exists():
        pytest.skip("script not yet present")
    result = subprocess.run(
        [str(SCRIPT), "--dryrun"],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "--weights" in result.stderr or "--weights" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scripts/test_measure_run_dryrun.py -v`
Expected: 2 FAIL (or 2 SKIP if the file truly doesn't exist yet — both are acceptable as "red" before step 3).

- [ ] **Step 3: Write the orchestrator**

```bash
# scripts/sam/measure-run.sh
#!/usr/bin/env bash
# measure-run.sh — operator-facing one-shot for SAM 8-GPU measurement runs.
#
# Phases:
#   1. Precheck (AWS profile, region)
#   2. Args parse + validation
#   3. LT publish (sam-launch-template.sh with REPO_REF=main, IMAGE_ID
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
log 3 "publish LT (REPO_REF=main, IMAGE_ID=$AMI_ID, INSTANCE_TYPE=$INSTANCE_TYPE)"
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
    # Spot first, fallback to on-demand on InsufficientInstanceCapacity.
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
    log 6 "Would: SSM run nvidia-smi -L | wc -l == 8 etc"
else
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters 'commands=["nvidia-smi -L | wc -l","ls /opt/sam/stand-alone-analyzer/vendor/QPress-SAM-Flake/run_amg_v2.py","ls /etc/flake-analysis-worker.env","pgrep -f flake_analysis.worker | head -1"]' \
        --query "Command.CommandId" --output text)
    sleep 5
    out=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text)
    grep -q "^8$" <<< "$out" || { echo "pre-flight fail: not 8 GPUs visible" >&2; exit 3; }
    grep -q "run_amg_v2.py" <<< "$out" || { echo "pre-flight fail: vendor not present" >&2; exit 3; }
    grep -q "flake-analysis-worker.env" <<< "$out" || { echo "pre-flight fail: worker env missing" >&2; exit 3; }
fi

# ------- phase 7 -------
log 7 "push defer launcher + run"
if (( DRYRUN )); then
    JOB_ID="DRYRUN-job"
    log 7 "Would: scp measure-defer.py via SSM + run with --weights-uri $WEIGHTS"
else
    # Inline-copy the launcher via base64 to avoid an S3 hop.
    payload_b64=$(base64 < "$REPO_ROOT/scripts/sam/measure-defer.py")
    cmd_id=$(aws_q ssm send-command --instance-ids "$INSTANCE_ID" \
        --document-name AWS-RunShellScript \
        --parameters "commands=[\"echo $payload_b64 | base64 -d > /tmp/measure-defer.py\",\"chmod +x /tmp/measure-defer.py\",\"sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 /tmp/measure-defer.py --weights-uri '$WEIGHTS' --dataset-dir /opt/sam/dataset/$(basename '$DATASET' | tr -d '/') --analysis-folder /opt/sam/runs/$RUN_ID --run-id $RUN_ID --scan-id $SCAN_ID\"]" \
        --query "Command.CommandId" --output text)
    sleep 10
    out=$(aws_q ssm get-command-invocation \
        --command-id "$cmd_id" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text)
    JOB_ID=$(grep -oE 'job_id=[0-9]+' <<< "$out" | head -1 | cut -d= -f2 || true)
    [[ -n "$JOB_ID" ]] || { echo "defer failed:\n$out" >&2; exit 4; }
    log 7 "deferred job_id=$JOB_ID"
fi

# ------- phase 8 -------
log 8 "polling loop (tick=30s, wall_cap=${WALL_CAP_MIN}m, cost_cap=\$$COST_CAP_USD)"
deadline=$(( LAUNCH_TS_EPOCH + WALL_CAP_MIN * 60 ))
hourly_rate_spot=3.98
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
        # Conservative — assume on-demand rate even if launched as spot.
        proj_cost=$(awk -v s="$elapsed_s" -v r="$hourly_rate_on_demand" \
                        'BEGIN { printf "%.2f", s/3600.0*r }')
        if awk -v p="$proj_cost" -v c="$COST_CAP_USD" \
               'BEGIN { exit !(p > c) }'; then
            log 8 "cost-cap exceeded (\$$proj_cost > \$$COST_CAP_USD)"
            status="cost_cap_exceeded"
            break
        fi
        # Query procrastinate_jobs.status by SSM-ssh'ing to the instance.
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
mkdir -p "claudedocs/measurement-${RUN_ID}"
if [[ "$status" == "succeeded" && $DRYRUN -eq 0 ]]; then
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
        # Parse worker_events.tsv: column 1 ts_epoch, column 2 event.
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

log 11 "(terminate happens in trap EXIT)"
exit 0
```

- [ ] **Step 4: Mark executable**

```bash
chmod +x scripts/sam/measure-run.sh
```

- [ ] **Step 5: Lint and run dryrun test**

```bash
shellcheck scripts/sam/measure-run.sh
uv run pytest tests/scripts/test_measure_run_dryrun.py -v
```

Expected: shellcheck warnings only acceptable if pre-existing in the repo's other shell scripts; new errors must be fixed. pytest: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/sam/measure-run.sh tests/scripts/test_measure_run_dryrun.py
git commit -m "feat(scripts): measure-run.sh — operator orchestrator for SAM 8-GPU runs

End-to-end one-shot: LT publish → spot launch (on-demand fallback)
→ SSM defer (via Task 8 launcher) → 30s polling loop with cost+wall
caps → SSM pull per_image_results.json + worker_events SQL → compute
boot/load/proc/total → trap EXIT terminate.

Belt-and-suspenders with instance-side abs-cap.timer (Task 9).

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 10)
"
```

---

## Task 11: Republish launch-template (LT v19 with main + abs-cap user-data)

**Files:** none — AWS state change only.

This task is run BEFORE the smoke test (Task 13). LT v18 still has the old user-data with `REPO_REF=feat/migration-cutover` — the smoke run needs LT v19 with the main-default + abs-cap installation.

- [ ] **Step 1: Confirm preconditions**

```bash
git log --oneline -3
```

Expected: HEAD includes Task 10's commit (`feat(scripts): measure-run.sh ...`) and Task 9's commit (`feat(devops): instance-side abs-cap ...`).

- [ ] **Step 2: Publish LT v19**

```bash
INSTANCE_TYPE=g6e.48xlarge \
    IMAGE_ID_OVERRIDE=ami-092ae5880cb9cf957 \
    bash scripts/aws/sam-launch-template.sh
```

Expected stdout ends with `Created version: v19` and `Set default → v19`.

- [ ] **Step 3: Verify v19 user-data contains REPO_REF=main + abs-cap**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-launch-template-versions \
    --launch-template-name qpress-sam-gpu-worker \
    --versions '$Latest' \
    --query 'LaunchTemplateVersions[0].LaunchTemplateData.UserData' \
    --output text \
  | base64 -d | gunzip | grep -E 'REPO_REF.*main|abs-cap'
```

Expected: at least one match per pattern.

- [ ] **Step 4: Note in claudedocs**

Append a short note to `claudedocs/sam-211-bake-#228.md` (or create `claudedocs/lt-v19-publish-2026-05-29.md`) recording v19 publish timestamp + verification grep output. No commit needed for this task — runbook record only.

---

## Task 12: Stage `scan6-100` dataset on AMI startup path

**Files:**
- Modify: `scripts/aws/sam-gpu-worker-userdata.sh`

The dataset (100 PNG, ~284 MB) is already in S3 at `s3://qpress-uploads/internal/sam/scan6-100/`. The user-data already downloads weights; extend it to also stage this dataset to `/opt/sam/dataset/scan6-100/` so the smoke run finds them at the path measure-defer.py expects.

- [ ] **Step 1: Add the dataset stage step**

In `scripts/aws/sam-gpu-worker-userdata.sh`, near the existing weights download block (look for `S3_MERGED_PFX` aws s3 cp), add a parallel block:

```bash
# --- Stage measurement dataset (idempotent; skips if dir already populated) -
DATASET_PFX="${DATASET_PFX:-internal/sam/scan6-100/}"
DATASET_DIR="/opt/sam/dataset/$(basename "${DATASET_PFX%/}")"
if ! done_stamp dataset; then
    echo "[6c] stage measurement dataset s3://${S3_BUCKET}/${DATASET_PFX} → ${DATASET_DIR}"
    mkdir -p "${DATASET_DIR}"
    aws s3 sync "s3://${S3_BUCKET}/${DATASET_PFX}" "${DATASET_DIR}" \
        --no-progress --only-show-errors
    chown -R "${RUN_USER}:${RUN_USER}" "${DATASET_DIR}"
    stamp dataset
fi
```

(Place the block immediately after the existing weights-download block. The exact step number prefix in the echo can be whatever fits the existing numbering.)

- [ ] **Step 2: Lint**

```bash
shellcheck scripts/aws/sam-gpu-worker-userdata.sh
```

Expected: no new errors.

- [ ] **Step 3: Republish LT v20**

```bash
INSTANCE_TYPE=g6e.48xlarge \
    IMAGE_ID_OVERRIDE=ami-092ae5880cb9cf957 \
    bash scripts/aws/sam-launch-template.sh
```

Expected: `Created version: v20`.

- [ ] **Step 4: Verify v20 user-data contains the dataset stage**

```bash
aws --profile qpress --region us-east-2 \
    ec2 describe-launch-template-versions \
    --launch-template-name qpress-sam-gpu-worker --versions '$Latest' \
    --query 'LaunchTemplateVersions[0].LaunchTemplateData.UserData' \
    --output text | base64 -d | gunzip \
  | grep -E 'aws s3 sync.*scan6-100|stage measurement dataset'
```

Expected: at least one match.

- [ ] **Step 5: Commit**

```bash
git add scripts/aws/sam-gpu-worker-userdata.sh
git commit -m "feat(devops): stage measurement dataset (scan6-100) in worker userdata

Cold launch syncs s3://qpress-uploads/internal/sam/scan6-100/ to
/opt/sam/dataset/scan6-100/. measure-defer.py points at this path.
Idempotent (done_stamp gated). Future smoke runs that point at a
different dataset prefix can override via DATASET_PFX env at LT
version create time.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 12)
"
```

---

## Task 13: Manual smoke — real 8-GPU 100-image measurement run

**Files:** none — AWS measurement run + writeup.

This is the **acceptance gate** for the entire plan (spec D7). Acceptance: timing breakdown printed, instance verified terminated, claudedocs/measurement-* artifacts on disk.

- [ ] **Step 1: Confirm everything from prior tasks is in place**

```bash
# All commits landed
git log --oneline -8
# Latest LT version uses main + abs-cap + dataset stage
aws --profile qpress --region us-east-2 \
    ec2 describe-launch-template-versions \
    --launch-template-name qpress-sam-gpu-worker --versions '$Latest' \
    --query 'LaunchTemplateVersions[0].LaunchTemplateData.UserData' \
    --output text | base64 -d | gunzip \
  | grep -E 'REPO_REF.*main|abs-cap|scan6-100' | head -5
```

Expected: at least 3 grep matches.

- [ ] **Step 2: Dryrun the orchestrator first (sanity)**

```bash
./scripts/sam/measure-run.sh \
    --weights s3://qpress-uploads/internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt \
    --dataset s3://qpress-uploads/internal/sam/scan6-100/ \
    --instance-type g6e.48xlarge \
    --cost-cap-usd 5 \
    --wall-cap-min 60 \
    --dryrun
```

Expected: phases 1–11 print with `[phase=N]` prefixes, no AWS state changes, exit 0.

- [ ] **Step 3: Live run**

```bash
./scripts/sam/measure-run.sh \
    --weights s3://qpress-uploads/internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt \
    --dataset s3://qpress-uploads/internal/sam/scan6-100/ \
    --instance-type g6e.48xlarge \
    --cost-cap-usd 5 \
    --wall-cap-min 60 \
  2>&1 | tee "claudedocs/measurement-$(date -u +%s)/run.log"
```

Expected timeline (approximate, see spec §3 / §5):

| Phase | Wall clock |
|---|---|
| 1–4 (precheck → run-instances) | ≤ 30 s |
| 5 (SSM online) | +60–90 s after launch |
| 6 (pre-flight) | ≤ 10 s |
| 7 (defer) | ≤ 15 s |
| 8 (polling — measurement) | ~45–90 s for the SAM run itself |
| 9–11 (collect + terminate) | ≤ 30 s |
| **Total** | ~3–5 min wall clock; **cost ≤ $1** |

If anything wedges:
* operator-side wall-cap or cost-cap fires within 60 min
* instance-side abs-cap.timer fires at T+60 min absolute
* trap EXIT terminates on script kill / Ctrl-C

Acceptance pass criteria:
* `phase=10 boot_s=... model_load_s=... processing_s=... total_s=...` line printed
* `claudedocs/measurement-<RUN_ID>/summary.json` exists and parses
* `claudedocs/measurement-<RUN_ID>/per_image_results.json` exists and contains 100 entries
* `aws ec2 describe-instances --instance-ids $INSTANCE_ID` shows `terminated`

- [ ] **Step 4: Append measurement section to docs/sam-ops.md**

Append `## 21. 8-GPU 100-image measurement run 2026-05-29 (#229 follow-up — SUCCESS)` to `docs/sam-ops.md` documenting:
* the actual timing breakdown values
* total cost
* instance-id, AMI, LT version
* per_image_results.json summary (images / masks_total / errors)
* recommendation (e.g. "M3 still LoRA-runtime — for prod use, merge LoRA into base. See §15.")

- [ ] **Step 5: Update docs/project-status.md**

Append a one-line history entry referencing the new sam-ops §21.

- [ ] **Step 6: Commit**

```bash
git add docs/sam-ops.md docs/project-status.md \
        "claudedocs/measurement-${RUN_ID}/"
git commit -m "docs(sam): §21 — 8-GPU 100-image measurement run SUCCESS (#229 follow-up)

First successful 8-GPU measurement run on g6e.48xlarge for the 100-image
scan6-100 dataset. boot_s / model_load_s / processing_s / total_s
captured via worker_events. measure-run.sh + abs-cap.timer + the
worker.measurement module (env-source / model resolver / defer payload)
all functioning end-to-end.

Plan: docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md (Task 13)
"
```

- [ ] **Step 7: Push**

```bash
git push origin main
```

---

## Self-review

* **Spec coverage:**
  * §1 Goal — Tasks 13 acceptance gate. ✓
  * §3 Architecture — Tasks 1–12 each implement one named box. ✓
  * §4.1 core/sam.py markers — Task 3. ✓
  * §4.2 worker/tasks.py instrumentation + model_meta — Task 4. ✓
  * §4.3 worker/measurement.py — Tasks 5/6/7. ✓
  * §4.4 measure-defer.py — Task 8. ✓
  * §4.5 measure-run.sh — Task 10. ✓
  * §4.6 abs-cap.timer — Task 9. ✓
  * §5 Data flow — exercised by Task 13 smoke run. ✓
  * §6 Error handling — Task 10 implements wall-cap + cost-cap + trap EXIT; Task 9 implements unconditional self-terminate. ✓
  * §7 Testing strategy — Tasks 1–7, 10 each include a Test layer. ✓
  * Spec D2 deviation explicitly called out at top of plan. ✓

* **Placeholder scan:** No "TBD"/"TODO"/"implement later"/"add appropriate" — every step has either real code or a real command with an expected output. ✓

* **Type consistency:** `model_meta` payload shape `{name, sha256, source_uri[, local_path]}` is referenced consistently in tasks 4, 6, 7, 8. `emit_marker` signature `(*, run_id, event, payload)` consistent in tasks 2, 4. `worker_events` columns `(id, run_id, event, payload, ts)` consistent in tasks 1, 2, 4, 10. ✓

* **Spec deviation noted:** D2 (usage_events vs worker_events) is called out in the plan header with explicit rationale. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-29-gpu-measurement-harness.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, PM reviews between tasks, fast iteration; matches the way other plans in this repo are executed (W11/W12). Tasks 1–10 dispatch in this session, Task 11–12 are AWS-state plus a quick repo edit, Task 13 is the live measurement.

2. **Inline Execution** — execute tasks in this session with checkpoints; slower turnover but PM context kept warm.

Which approach?
