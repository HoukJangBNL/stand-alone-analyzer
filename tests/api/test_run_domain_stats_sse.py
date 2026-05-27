# tests/api/test_run_domain_stats_sse.py
import pytest
import asyncio
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import run as run_route
from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest
from flake_analysis.state.paths import analysis_folder

# Test scan id used by tests that don't carry a real DB row.
SID = 42


def _make_app() -> FastAPI:
    """Mini-app exposing only the run router (W10-C.4b)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_route.router, prefix="/api/v1")
    return app


def _setup_project(tmp_path, monkeypatch, pid: str = "p_ds", sid: int = SID):
    """Create a per-scan manifest on disk with annotations_path + completed background step.

    Domain stats requires the upstream Background step to have run, so the
    manifest must already record a 'background' StepEntry. The pipeline
    function is mocked at the route boundary so we don't actually need the
    background.npy file on disk for these tests.
    """
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text("{}", encoding="utf-8")

    m = Manifest(
        analysis_folder=str(folder),
        raw_images_dir=str(raw_images_dir),
        annotations_path=str(annotations_path),
        steps={
            "background": StepEntry(
                completed_at=datetime.now(timezone.utc).isoformat(),
                params={"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"},
                params_hash="bg_hash_stub",
                outputs={"background_npy": "01_background/background.npy"},
            ),
        },
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
async def test_run_domain_stats_sse(tmp_path, monkeypatch, _client_app):
    """POST /run/domain_stats streams progress and completes."""
    folder = _setup_project(tmp_path, monkeypatch, pid="p_ds", sid=SID)

    def mock_run_domain_stats(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "output_path": str(folder / "02_domain_stats" / "stats.npz"),
            "num_flakes": 42,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/p_ds/scans/{SID}/run/domain_stats",
                json={"repr_mode": "median", "raw_ext": ".png"},
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

                assert len(progress_events) == 3
                assert len(done_events) == 1
                assert done_events[0]["result"]["num_flakes"] == 42


@pytest.mark.asyncio
async def test_run_domain_stats_sse_wire_format(tmp_path, monkeypatch, _client_app):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path, monkeypatch, pid="p_ds_wire", sid=SID)

    def mock_run_domain_stats(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(1.0, "done")
        return {
            "output_path": "/tmp/stats.npz",
            "num_flakes": 7,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/projects/p_ds_wire/scans/{SID}/run/domain_stats",
                json={"repr_mode": "median"},
            )
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_domain_stats_mutex_contention_returns_locked(tmp_path, monkeypatch, _client_app):
    """Concurrent requests to same scan: second gets HTTP 423, NOT a stream error."""
    _setup_project(tmp_path, monkeypatch, pid="p_ds_busy", sid=SID)

    release = threading.Event()
    started = threading.Event()

    def mock_run_domain_stats(**kwargs):
        # Block the worker thread until the test releases it. This keeps the
        # scan lock held and lets a second request collide.
        started.set()
        release.wait(timeout=5.0)
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "output_path": "/tmp/stats.npz",
            "num_flakes": 1,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async def first_request():
                async with client.stream(
                    "POST", f"/api/v1/projects/p_ds_busy/scans/{SID}/run/domain_stats", json={"repr_mode": "median"}
                ) as resp:
                    assert resp.status_code == 200
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                    await asyncio.sleep(0.05)
                    release.set()
                    async for _ in resp.aiter_lines():
                        pass
                    return resp.status_code

            async def second_request():
                while not started.is_set():
                    await asyncio.sleep(0.01)
                resp = await client.post(
                    f"/api/v1/projects/p_ds_busy/scans/{SID}/run/domain_stats", json={"repr_mode": "median"}
                )
                return resp

            first_task = asyncio.create_task(first_request())
            second_resp = await second_request()
            await first_task

    # ProjectBusy.status_code is HTTP_423_LOCKED. Critical: this is a regular
    # HTTP error, NOT a streaming response that opens with 200 and errors.
    assert second_resp.status_code == 423
    assert "text/event-stream" not in second_resp.headers.get("content-type", "")
    body = second_resp.json()
    assert body["error"]["code"] == "project_busy"
    assert "request_id" in body["error"]


@pytest.mark.asyncio
async def test_run_domain_stats_pipeline_error_emits_sse_error_event(tmp_path, monkeypatch, _client_app):
    """A pipeline exception is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path, monkeypatch, pid="p_ds_err", sid=SID)

    def mock_run_domain_stats(**kwargs):
        raise RuntimeError("background not ready")

    _stub_session_dep(_client_app)
    with _runs_audit_patches(), patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST", f"/api/v1/projects/p_ds_err/scans/{SID}/run/domain_stats", json={"repr_mode": "median"}
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
    assert "background not ready" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]


@pytest.mark.pg
@pytest.mark.asyncio
async def test_run_domain_stats_writes_runs_row(tmp_path, monkeypatch, pg_session, active_scan, _client_app):
    """End-to-end: SSE flow writes a runs audit-log row with status=completed."""
    from contextlib import asynccontextmanager

    from sqlalchemy import select

    from flake_analysis.api.deps import get_db_session
    from flake_analysis.db.models import Analysis, Model
    from flake_analysis.db.models.analysis import Run

    pid = "p_ds_pg"
    folder = _setup_project(tmp_path, monkeypatch, pid=pid, sid=active_scan.id)

    model = Model(name="t-ds-pg-model", base_model="sam2", s3_uri="s3://t/ds-pg")
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

    async def _yield_pg():
        yield pg_session

    _client_app.dependency_overrides[get_db_session] = _yield_pg

    @asynccontextmanager
    async def _bg_pg():
        yield pg_session

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_session_for_background", _bg_pg
    )

    def mock_run_domain_stats(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "output_path": str(folder / "02_domain_stats" / "stats.npz"),
            "num_flakes": 1,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    with patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/{pid}/scans/{active_scan.id}/run/domain_stats",
                json={"repr_mode": "median", "raw_ext": ".png"},
            ) as resp:
                assert resp.status_code == 200
                async for _ in resp.aiter_lines():
                    pass

    rows = (
        await pg_session.execute(
            select(Run).where(Run.analysis_id == analysis.id).order_by(Run.id)
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected 1 Run row, got {len(rows)}"
    row = rows[0]
    assert row.step == "domain_stats"
    await pg_session.refresh(row)
    assert row.status.value == "completed"
    assert row.completed_at is not None
    assert row.error is None
    assert row.metrics == {"repr_mode": "median", "raw_ext": ".png"}
