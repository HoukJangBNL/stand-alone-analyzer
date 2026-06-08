# tests/api/test_run_sam_sse.py
"""SSE wire-format tests for POST /run/sam.

P4.2.d swap: the route no longer calls ``run_sam_step`` in-process —
it defers a procrastinate job and LISTENs on a per-run NOTIFY channel
for progress/terminal payloads from the worker.

Tests now patch two route-level seams:

- ``flake_analysis.api.routes.run._defer_sam_job``: stub to a no-op so
  no real queue is touched. Tests don't assert on the deferred args
  here (the worker's own tests cover that).
- ``flake_analysis.api.routes.run._stream_sam_events``: replace with a
  fake async iterator that yields the canned payloads (``progress`` /
  ``completed`` / ``error``) the wire-format test wants to assert on.

The SSE wire format produced by the route is identical to the
pre-P4.2.d in-process flow, so these tests still verify the same
contract — just with a worker queue inserted in the middle.
"""
import pytest
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import run as run_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

# Test scan id used by tests that don't carry a real DB row.
SID = 42


def _fake_stream_factory(payloads):
    """Return a callable that, when called with ``run_id``, yields ``payloads``.

    Mirrors the :func:`flake_analysis.api.routes.run._stream_sam_events`
    contract: takes a ``run_id`` (ignored by the fake) and returns an
    async iterator of NOTIFY-shaped dicts.
    """

    async def _gen(run_id):
        for p in payloads:
            yield p

    return _gen


def _make_app() -> FastAPI:
    """Mini-app exposing only the run router (W10-C.4b)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_route.router, prefix="/api/v1")
    return app


def _setup_project(tmp_path, monkeypatch, pid: str = "p_sam", sid: int = SID):
    """Create a per-scan manifest on disk and point SAA_ANALYSIS_ROOT at tmp_path."""
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()

    m = Manifest(
        analysis_folder=str(folder),
        raw_images_dir=str(raw_images_dir),
    )
    save_manifest(m, folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    """Per-scan locks are module-global; clear between tests to avoid leaks."""
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


@pytest.fixture
def _client_app():
    return _make_app()


def _stub_session_dep(app):
    """Override get_db_session with a no-op session for usage event emission."""
    from unittest.mock import AsyncMock, MagicMock

    from flake_analysis.api.deps import get_db_session

    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()

    async def _yield():
        yield mock_session

    app.dependency_overrides[get_db_session] = _yield


@contextmanager
def _runs_audit_patches():
    """Compose mocks for the runs audit-log helpers + get_active_analysis.

    P2.6 wired ``record_run_start`` / ``record_run_end`` into the route. With a
    stubbed session those functions can't return a real ``run_id`` (they call
    ``session.flush()`` and read ``row.id``). Patching them to AsyncMock keeps
    the existing SSE tests focused on streaming semantics.

    Also patches ``get_active_analysis`` to return a stub Analysis row so the
    route's ``404 if analysis is None`` guard passes without a real DB.
    """
    with (
        patch(
            "flake_analysis.api.routes.run.get_active_analysis",
            new=AsyncMock(return_value=SimpleNamespace(id=99)),
        ),
        patch(
            "flake_analysis.api.routes.run.record_run_start",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "flake_analysis.api.routes.run.record_run_end",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_run_sam_sse(tmp_path, monkeypatch, _client_app):
    """POST /run/sam streams progress and completes."""
    _setup_project(tmp_path, monkeypatch, pid="p_sam", sid=SID)

    fake_payloads = [
        {"type": "progress", "progress": 0.5, "message": "[1/2] image_a.png: 3 masks"},
        {"type": "progress", "progress": 1.0, "message": "[2/2] image_b.png: 4 masks"},
        {
            "type": "completed",
            "result": {"images": 2, "masks_total": 7, "errors": 0, "per_image": {}},
        },
    ]

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch(
        "flake_analysis.api.routes.run._defer_sam_job", new=AsyncMock(return_value=None)
    ), patch(
        "flake_analysis.api.routes.run._stream_sam_events",
        side_effect=_fake_stream_factory(fake_payloads),
    ):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/p_sam/scans/{SID}/run/sam",
                json={"weights_path": "/tmp/fake_weights.pt"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append(data)

                progress_events = [e for e in events if e["type"] == "progress"]
                done_events = [e for e in events if e["type"] == "done"]

                assert len(progress_events) >= 2
                assert len(done_events) == 1
                assert done_events[0]["result"]["images"] == 2
                assert done_events[0]["result"]["masks_total"] == 7


@pytest.mark.asyncio
async def test_run_sam_sse_wire_format(tmp_path, monkeypatch, _client_app):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path, monkeypatch, pid="p_sam_wire", sid=SID)

    fake_payloads = [
        {"type": "progress", "progress": 0.5, "message": "halfway"},
        {"type": "progress", "progress": 1.0, "message": "done"},
        {
            "type": "completed",
            "result": {"images": 2, "masks_total": 7, "errors": 0, "per_image": {}},
        },
    ]

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch(
        "flake_analysis.api.routes.run._defer_sam_job", new=AsyncMock(return_value=None)
    ), patch(
        "flake_analysis.api.routes.run._stream_sam_events",
        side_effect=_fake_stream_factory(fake_payloads),
    ):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/projects/p_sam_wire/scans/{SID}/run/sam",
                json={"weights_path": "/tmp/fake_weights.pt"},
            )
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_sam_pipeline_error_emits_sse_error_event(tmp_path, monkeypatch, _client_app):
    """A worker error notification is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path, monkeypatch, pid="p_sam_err", sid=SID)

    # Simulate the worker emitting an 'error' notification (which the route
    # translates into the same wire-format pipeline_failed envelope the
    # in-process flow used to produce on uncaught exceptions).
    fake_payloads = [
        {"type": "error", "code": "RuntimeError", "message": "weights not found"},
    ]

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch(
        "flake_analysis.api.routes.run._defer_sam_job", new=AsyncMock(return_value=None)
    ), patch(
        "flake_analysis.api.routes.run._stream_sam_events",
        side_effect=_fake_stream_factory(fake_payloads),
    ):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/p_sam_err/scans/{SID}/run/sam",
                json={"weights_path": "/tmp/missing.pt"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    err = error_events[0]
    assert err["error"]["code"] == "pipeline_failed"
    assert "weights not found" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]


@pytest.mark.pg
@pytest.mark.asyncio
async def test_run_sam_writes_runs_row(tmp_path, monkeypatch, pg_session, active_scan, _client_app):
    """End-to-end: SSE flow writes a runs audit-log row with status=completed."""
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from flake_analysis.api.deps import get_db_session
    from flake_analysis.db.models import Analysis, Model
    from flake_analysis.db.models.analysis import Run

    pid = "p_sam_pg"
    _setup_project(tmp_path, monkeypatch, pid=pid, sid=active_scan.id)

    # Build Model + Analysis tied to active_scan so get_active_analysis()
    # returns a real row keyed by scan_id.
    model = Model(name="t-sam-pg-model", base_model="sam2", s3_uri="s3://t/sam-pg")
    pg_session.add(model)
    await pg_session.flush()
    analysis = Analysis(
        scan_id=active_scan.id,
        model_id=model.id,
        amg_params={},
        link_distance_px=10.0,
        steps_done={},
    )
    pg_session.add(analysis)
    await pg_session.flush()
    await pg_session.refresh(analysis)

    # Wire pg_session into the request scope.
    async def _yield_pg():
        yield pg_session

    _client_app.dependency_overrides[get_db_session] = _yield_pg

    # Wire pg_session into the background scope.
    @asynccontextmanager
    async def _bg_pg():
        yield pg_session

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_session_for_background", _bg_pg
    )

    fake_payloads = [
        {"type": "progress", "progress": 1.0, "message": "done"},
        {
            "type": "completed",
            "result": {"images": 2, "masks_total": 7, "errors": 0, "per_image": {}},
        },
    ]

    with patch(
        "flake_analysis.api.routes.run._defer_sam_job", new=AsyncMock(return_value=None)
    ), patch(
        "flake_analysis.api.routes.run._stream_sam_events",
        side_effect=_fake_stream_factory(fake_payloads),
    ):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/{pid}/scans/{active_scan.id}/run/sam",
                json={"weights_path": "/tmp/fake_weights.pt"},
            ) as resp:
                assert resp.status_code == 200
                async for _ in resp.aiter_lines():
                    pass

    # Verify a Run row landed with status=completed.
    rows = (
        await pg_session.execute(
            select(Run).where(Run.analysis_id == analysis.id).order_by(Run.id)
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected 1 Run row, got {len(rows)}"
    row = rows[0]
    assert row.step == "sam"
    await pg_session.refresh(row)
    assert row.status.value == "completed"
    assert row.completed_at is not None
    assert row.error is None
    assert row.metrics["images"] == 2
    assert row.metrics["masks_total"] == 7
    assert row.metrics["errors"] == 0


# ---------------------------------------------------------------------------
# GPU Dispatcher Task 2 — _ensure_gpu_worker / _defer_sam_job / driver loop
#
# These tests cover the cold-start UX wiring on the API side:
#   - _ensure_gpu_worker now returns LaunchResult (was None)
#   - _defer_sam_job optionally takes a ProgressBridge and emits
#     gpu_launching when a fresh boot fired
#   - run_sam driver loop has a non-terminal gpu_ready branch that
#     forwards image_count from the worker NOTIFY payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_sam_job_emits_gpu_launching_when_action_is_launched(monkeypatch):
    """When _ensure_gpu_worker returns LaunchResult(action='launched'),
    _defer_sam_job calls bridge.emit_gpu_launching with the instance_id."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.api.sse import ProgressBridge
    from flake_analysis.worker.launcher import LaunchResult

    captured: list[tuple[str, str]] = []

    bridge = ProgressBridge()
    # Wrap the bridge's method to capture instead of relying on queue drain.
    bridge.emit_gpu_launching = lambda iid: captured.append(("gpu_launching", iid))

    async def _fake_ensure():
        return LaunchResult(action="launched", instance_id="i-test123")

    monkeypatch.setattr(run_module, "_ensure_gpu_worker", _fake_ensure)

    # Pre-import tasks so its @app.task decorators run against the real
    # app, BEFORE we monkeypatch the app symbol. _defer_sam_job's later
    # `from flake_analysis.worker import tasks` will then be a no-op
    # (module already in sys.modules).
    import flake_analysis.worker.tasks  # noqa: F401

    # Replace app.tasks["run_sam"] with a no-op defer_async
    class _FakeTask:
        async def defer_async(self, **kw):
            pass

    class _FakeApp:
        tasks = {"run_sam": _FakeTask()}

    monkeypatch.setattr("flake_analysis.worker.app.app", _FakeApp())

    await run_module._defer_sam_job(
        run_id=1,
        raw_images_dir="/x",
        analysis_folder="/y",
        weights_path="/z.pt",
        device=None,
        bridge=bridge,
    )

    assert ("gpu_launching", "i-test123") in captured


@pytest.mark.asyncio
async def test_defer_sam_job_skips_gpu_launching_when_action_is_noop(monkeypatch):
    """When _ensure_gpu_worker returns LaunchResult(action='noop'),
    _defer_sam_job does NOT call emit_gpu_launching."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.api.sse import ProgressBridge
    from flake_analysis.worker.launcher import LaunchResult

    captured: list[tuple[str, str]] = []

    bridge = ProgressBridge()
    bridge.emit_gpu_launching = lambda iid: captured.append(("gpu_launching", iid))

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
async def test_defer_sam_job_works_without_bridge_kwarg(monkeypatch):
    """Backwards-compat: _defer_sam_job(...) without bridge= still works.
    Existing call sites that haven't been updated must keep functioning."""
    from flake_analysis.api.routes import run as run_module
    from flake_analysis.worker.launcher import LaunchResult

    async def _fake_ensure():
        return LaunchResult(action="launched", instance_id="i-test")

    monkeypatch.setattr(run_module, "_ensure_gpu_worker", _fake_ensure)

    class _FakeTask:
        async def defer_async(self, **kw):
            pass

    class _FakeApp:
        tasks = {"run_sam": _FakeTask()}

    monkeypatch.setattr("flake_analysis.worker.app.app", _FakeApp())

    # No bridge= kwarg — must not raise.
    await run_module._defer_sam_job(
        run_id=3,
        raw_images_dir="/x",
        analysis_folder="/y",
        weights_path="/z.pt",
        device=None,
    )


@pytest.mark.asyncio
async def test_run_sam_driver_loop_routes_gpu_ready_payload(
    tmp_path, monkeypatch, _client_app
):
    """When _stream_sam_events yields a gpu_ready payload, the driver
    loop emits an SSE 'gpu_ready' frame with image_count and continues
    (non-terminal — the subsequent completed payload is the actual
    terminator).

    Asserts at the SSE wire-frame level (matching the existing patterns
    in this file, which also assert via wire frames) — bridge.emit_gpu_ready
    routes through _put_progress, so it surfaces as `event: gpu_ready`.
    """
    _setup_project(tmp_path, monkeypatch, pid="p_sam_gpu_ready", sid=SID)

    fake_payloads = [
        {"type": "gpu_ready", "image_count": 100},
        {
            "type": "completed",
            "result": {"images": 2, "masks_total": 7, "errors": 0, "per_image": {}},
        },
    ]

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch(
        "flake_analysis.api.routes.run._defer_sam_job", new=AsyncMock(return_value=None)
    ), patch(
        "flake_analysis.api.routes.run._stream_sam_events",
        side_effect=_fake_stream_factory(fake_payloads),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_client_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/projects/p_sam_gpu_ready/scans/{SID}/run/sam",
                json={"weights_path": "/tmp/fake_weights.pt"},
            )
            assert resp.status_code == 200
            body = resp.text

    # Wire-frame assertions: gpu_ready frame appears, followed by done.
    assert "event: gpu_ready\ndata: " in body
    assert "event: done\ndata: " in body
    # Order: gpu_ready frame must come before the done frame (driver
    # continues after gpu_ready instead of treating it as terminal).
    assert body.index("event: gpu_ready") < body.index("event: done")
    # Payload must include image_count.
    gpu_ready_line_idx = body.index("event: gpu_ready")
    # The data: line is the next line after the event: line.
    data_after = body[gpu_ready_line_idx:].split("\n", 2)[1]
    assert data_after.startswith("data: ")
    payload = json.loads(data_after[len("data: "):])
    assert payload["type"] == "gpu_ready"
    assert payload["image_count"] == 100
