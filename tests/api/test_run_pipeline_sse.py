# tests/api/test_run_pipeline_sse.py
"""P5.2 W13 pipeline orchestrator SSE tests.

Single endpoint POST /api/v1/projects/{pid}/scans/{sid}/run/pipeline drives all
5 pipeline steps and emits a multi-step event vocabulary distinct from the
per-step routes (`step_started` / `step_progress` / `step_completed` /
`pipeline_done` / `pipeline_error`).

All tests are PG-marked: they exercise the real Analysis/Run/Domain/Flake
ORM via the per-test SAVEPOINT-rolled-back ``pg_session``. Step wrappers
themselves (``run_thumbnails_step`` etc) are mocked to keep these tests
focused on orchestration semantics, not pipeline numerics.

P4.2.d swap (SAM step → procrastinate worker queue):
    The CPU-step wrappers (thumbnails / background / domain_stats /
    domain_proximity) still patch the runner symbols at
    ``flake_analysis.api.routes.run_pipeline``. The SAM step now defers
    to a worker via two seams that we patch instead:
      - ``defer_sam_job`` from sam_dispatch service (no-op AsyncMock to skip the queue)
      - ``_stream_sam_events`` (fake async iterator yielding the
        ``progress``/``completed``/``error`` payloads the old in-process
        mock would have produced via its progress_callback + return).
    Wire format observable by the client is unchanged.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

pytestmark = pytest.mark.pg


def _fake_sam_stream(payloads):
    """Build a fake :func:`_stream_sam_events` substitute.

    Returns a callable that, when invoked with ``run_id``, yields the
    canned payloads. Mirrors the production seam's contract — the route
    iterates the result with ``async for`` regardless of the underlying
    transport (real LISTEN/NOTIFY in prod, this list in tests).
    """

    async def _gen(run_id):
        for p in payloads:
            yield p

    return _gen


def _make_app() -> FastAPI:
    """Mini-app exposing only the run_pipeline router."""
    from flake_analysis.api.routes import run_pipeline as run_pipeline_route

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_pipeline_route.router, prefix="/api/v1")
    return app


def _setup_manifest(tmp_path, monkeypatch, pid: str, sid: int):
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir(exist_ok=True)
    annotations_path = folder / "annotations.json"
    annotations_path.write_text("[]")

    m = Manifest(
        analysis_folder=str(folder),
        raw_images_dir=str(raw_images_dir),
        annotations_path=str(annotations_path),
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


@pytest.fixture(autouse=True)
def _mock_sam_manifest_and_s3(monkeypatch):
    """Auto-mock generate_sam_manifest_for_scan, boto3, and _ensure_gpu_worker for all pipeline tests.

    The pipeline SAM step requires these for the S3-sync path. Tests that need
    specific manifest data or S3 put_object capture can override by patching again.
    Mirrors the fixture in test_run_sam_sse.py.
    """
    from unittest.mock import AsyncMock, MagicMock

    # Mock manifest generator
    fake_manifest = {
        "version": 1,
        "scan_id": 42,
        "scan_prefix": "scans/42/",
        "images": [],
    }
    monkeypatch.setattr(
        "flake_analysis.api.services.sam_manifest.generate_sam_manifest_for_scan",
        AsyncMock(return_value=fake_manifest),
    )

    # Mock boto3 S3 client to no-op
    def fake_boto3_client(service_name, **kwargs):
        if service_name == "s3":
            mock_client = MagicMock()
            mock_client.put_object = MagicMock(return_value={})
            return mock_client
        raise ValueError(f"Unexpected service: {service_name}")

    monkeypatch.setattr("boto3.client", fake_boto3_client)
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")

    # Mock _ensure_gpu_worker to return None (noop) by default
    async def _fake_ensure_noop():
        return None

    monkeypatch.setattr(
        "flake_analysis.api.services.sam_dispatch._ensure_gpu_worker",
        _fake_ensure_noop,
    )


def _wire_pg_sessions(app, pg_session, monkeypatch):
    """Override get_db_session + monkeypatch get_session_for_background to share pg_session.

    The pg_session fixture is single-connection; if two parallel orchestrator
    branches (domain_stats || domain_proximity) hit it concurrently asyncpg
    raises ``InterfaceError: cannot perform operation: another operation is
    in progress``. Wrap the bg context manager in an asyncio.Lock so two
    parallel callers serialise on the shared session — production uses fresh
    sessions per call from ``async_session_maker``, so no lock is needed there.
    """
    from flake_analysis.api.deps import get_db_session

    async def _yield_pg():
        yield pg_session

    app.dependency_overrides[get_db_session] = _yield_pg

    bg_lock = asyncio.Lock()

    @asynccontextmanager
    async def _bg_pg():
        async with bg_lock:
            yield pg_session

    monkeypatch.setattr(
        "flake_analysis.api.routes.run_pipeline.get_session_for_background", _bg_pg
    )


def _seed_model(pg_session, name: str = "t-pipeline-model"):
    """Insert a Model row so get_or_create_default_analysis has a fallback."""
    from flake_analysis.db.models import Model

    m = Model(name=name, base_model="sam2", s3_uri=f"s3://t/{name}")
    pg_session.add(m)
    return m


def _make_step_mock(summary: dict, *, progress_calls: list[tuple[float, str]] | None = None):
    """Build a mock step wrapper that fires its progress_callback before returning."""
    progress_calls = progress_calls or [(1.0, "done")]

    def _impl(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            for pct, msg in progress_calls:
                cb(pct, msg)
        return summary

    return _impl


async def _post_pipeline_collect_events(client, pid: str, sid: int, body: dict) -> tuple[int, list[dict]]:
    events: list[dict] = []
    async with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/scans/{sid}/run/pipeline",
        json=body,
    ) as resp:
        status = resp.status_code
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return status, events


# ----- Test 1: happy path streams 5 step_started events + pipeline_done + 4 Run rows -----


@pytest.mark.asyncio
async def test_pipeline_streams_all_5_steps_and_writes_4_runs_rows(
    tmp_path, monkeypatch, pg_session, active_scan
):
    pid = active_scan.project_id
    sid = active_scan.id
    _setup_manifest(tmp_path, monkeypatch, pid, sid)
    _seed_model(pg_session)
    await pg_session.flush()

    app = _make_app()
    _wire_pg_sessions(app, pg_session, monkeypatch)

    body = {
        "thumbnails": {"raw_ext": ".png", "quality": 80, "force_recompute": False},
        "background": {"seed": 0, "max_images": 1, "method": "median"},
        "sam": {"weights_path": "/tmp/fake_weights.pt"},
        "domain_stats": {"repr_mode": "median", "raw_ext": ".png"},
        "domain_proximity": {
            "r_max_px": 200.0,
            "min_area_px": 10,
            "d_touch_px": 2.0,
            "pixel_size_um": 0.5,
            "link_distance_um": 5.0,
            "workers": 1,
        },
    }

    thumbs = _make_step_mock({"output_dir": "/tmp/thumbs", "n_images": 1, "n_skipped": 0, "n_failed": 0, "params": {}, "params_hash": None, "cache_dir": None})
    bg = _make_step_mock({"output_path": "/tmp/bg.npy", "shape": (256, 256, 3), "params": {}})
    stats = _make_step_mock({"output_path": "/tmp/stats.json", "num_flakes": 3, "params": {}})
    prox = _make_step_mock(
        {
            "distances_path": "/tmp/d.parquet",
            "flake_assignments_path": "/tmp/fa.parquet",
            "n_pairs": 0,
            "n_domains": 0,
            "n_flakes": 0,
            "params": {},
        }
    )

    sam_payloads = [
        {"type": "progress", "progress": 1.0, "message": "done"},
        {
            "type": "completed",
            "result": {"images": 1, "masks_total": 3, "errors": 0, "per_image": {}},
        },
    ]

    with patch("flake_analysis.api.routes.run_pipeline.run_thumbnails_step", side_effect=thumbs), \
         patch("flake_analysis.api.routes.run_pipeline.run_background_step", side_effect=bg), \
         patch("flake_analysis.api.services.sam_dispatch.defer_sam_job", new=AsyncMock(return_value=None)), \
         patch("flake_analysis.api.routes.run_pipeline._stream_sam_events", side_effect=_fake_sam_stream(sam_payloads)), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_stats_step", side_effect=stats), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_proximity_step", side_effect=prox):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            status, events = await _post_pipeline_collect_events(client, pid, sid, body)
            assert status == 200

    started = [e for e in events if e["type"] == "step_started"]
    completed = [e for e in events if e["type"] == "step_completed"]
    done = [e for e in events if e["type"] == "pipeline_done"]
    errors = [e for e in events if e["type"] == "pipeline_error"]

    # Strict order for the first 3 steps; last 2 (stats, proximity) run in parallel.
    started_steps_in_order = [e["step"] for e in started]
    assert started_steps_in_order[:3] == ["thumbnails", "background", "sam"]
    assert set(started_steps_in_order[3:]) == {"domain_stats", "domain_proximity"}
    assert all(e["total"] == 5 for e in started)

    # All 5 steps completed.
    assert {e["step"] for e in completed} == {
        "thumbnails",
        "background",
        "sam",
        "domain_stats",
        "domain_proximity",
    }

    # Single pipeline_done with cascade summary. First run with non-empty
    # background body fires cascade by design (persisted was None → "different").
    assert len(done) == 1
    assert "cascade" in done[0]
    assert done[0]["cascade"]["fired"] is True
    assert errors == []

    # 4 Run rows (no thumbnails), all completed.
    from flake_analysis.db.models import Analysis
    from flake_analysis.db.models.analysis import Run

    analysis = (
        await pg_session.execute(select(Analysis).where(Analysis.scan_id == sid))
    ).scalar_one()
    rows = (
        await pg_session.execute(
            select(Run).where(Run.analysis_id == analysis.id).order_by(Run.id)
        )
    ).scalars().all()
    assert len(rows) == 4, f"expected 4 Run rows, got {len(rows)}"
    steps_set = {r.step for r in rows}
    assert steps_set == {"background", "sam", "domain_stats", "domain_proximity"}
    assert "thumbnails" not in steps_set
    for r in rows:
        await pg_session.refresh(r)
        assert r.status.value == "completed"
        assert r.error is None


# ----- Test 2: SAM failure stops cascade, emits pipeline_error, marks SAM run failed -----


@pytest.mark.asyncio
async def test_pipeline_emits_pipeline_error_on_step_failure(
    tmp_path, monkeypatch, pg_session, active_scan
):
    pid = active_scan.project_id
    sid = active_scan.id
    _setup_manifest(tmp_path, monkeypatch, pid, sid)
    _seed_model(pg_session, name="t-pipeline-model-err")
    await pg_session.flush()

    app = _make_app()
    _wire_pg_sessions(app, pg_session, monkeypatch)

    body = {
        "sam": {"weights_path": "/tmp/missing.pt"},
    }

    thumbs = _make_step_mock({"output_dir": "/tmp/t", "n_images": 0, "n_skipped": 0, "n_failed": 0, "params": {}, "params_hash": None, "cache_dir": None})
    bg = _make_step_mock({"output_path": "/tmp/bg.npy", "shape": (1, 1, 1), "params": {}})

    sam_error_payloads = [
        {"type": "error", "code": "RuntimeError", "message": "weights not found"},
    ]

    stats_called = {"n": 0}
    prox_called = {"n": 0}

    def stats_impl(**kwargs):
        stats_called["n"] += 1
        return {"output_path": "/tmp/s.json", "num_flakes": 0, "params": {}}

    def prox_impl(**kwargs):
        prox_called["n"] += 1
        return {
            "distances_path": "/tmp/d.parquet",
            "flake_assignments_path": "/tmp/fa.parquet",
            "n_pairs": 0,
            "n_domains": 0,
            "n_flakes": 0,
            "params": {},
        }

    with patch("flake_analysis.api.routes.run_pipeline.run_thumbnails_step", side_effect=thumbs), \
         patch("flake_analysis.api.routes.run_pipeline.run_background_step", side_effect=bg), \
         patch("flake_analysis.api.services.sam_dispatch.defer_sam_job", new=AsyncMock(return_value=None)), \
         patch("flake_analysis.api.routes.run_pipeline._stream_sam_events", side_effect=_fake_sam_stream(sam_error_payloads)), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_stats_step", side_effect=stats_impl), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_proximity_step", side_effect=prox_impl):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            status, events = await _post_pipeline_collect_events(client, pid, sid, body)
            assert status == 200

    pipeline_errors = [e for e in events if e["type"] == "pipeline_error"]
    pipeline_dones = [e for e in events if e["type"] == "pipeline_done"]

    assert len(pipeline_errors) == 1
    err = pipeline_errors[0]
    assert err["step"] == "sam"
    assert err["error"]["code"]  # RuntimeError or pipeline_failed; just ensure non-empty
    assert "weights not found" in err["error"]["message"]
    assert "request_id" in err["error"]
    assert pipeline_dones == []

    # Subsequent steps (domain_stats / domain_proximity) MUST NOT have run.
    assert stats_called["n"] == 0
    assert prox_called["n"] == 0

    # SAM Run row must be 'failed'; thumbnails has none; background must be 'completed';
    # domain_stats / domain_proximity must have no Run rows.
    from flake_analysis.db.models import Analysis
    from flake_analysis.db.models.analysis import Run

    analysis = (
        await pg_session.execute(select(Analysis).where(Analysis.scan_id == sid))
    ).scalar_one()
    rows = (
        await pg_session.execute(
            select(Run).where(Run.analysis_id == analysis.id).order_by(Run.id)
        )
    ).scalars().all()
    by_step = {}
    for r in rows:
        await pg_session.refresh(r)
        by_step[r.step] = r
    assert "sam" in by_step
    assert by_step["sam"].status.value == "failed"
    assert by_step["sam"].error and "weights not found" in by_step["sam"].error
    # Background ran first and succeeded.
    assert by_step.get("background") is not None
    assert by_step["background"].status.value == "completed"
    # Stats / proximity never started → no Run row.
    assert "domain_stats" not in by_step
    assert "domain_proximity" not in by_step


# ----- Test 3: cascade clears steps_done when background params change -----


@pytest.mark.asyncio
async def test_pipeline_cascade_clears_steps_done(
    tmp_path, monkeypatch, pg_session, active_scan
):
    pid = active_scan.project_id
    sid = active_scan.id
    _setup_manifest(tmp_path, monkeypatch, pid, sid)
    model = _seed_model(pg_session, name="t-pipeline-model-cascade")
    await pg_session.flush()

    # Pre-create an Analysis with stale background_params and a populated steps_done.
    from flake_analysis.db.models import Analysis

    pre_analysis = Analysis(
        scan_id=sid,
        model_id=model.id,
        amg_params={},
        background_params={"seed": 99, "max_images": 5, "method": "median", "gaussian_sigma": 10.0},
        link_distance_px=200.0,
        steps_done={"background": True, "sam": True, "domain_stats": True},
    )
    pg_session.add(pre_analysis)
    await pg_session.flush()
    pre_analysis_id = pre_analysis.id
    await pg_session.refresh(pre_analysis)

    app = _make_app()
    _wire_pg_sessions(app, pg_session, monkeypatch)

    # New body provides DIFFERENT background.seed → cascade must fire.
    body = {
        "background": {"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"},
        "sam": {"weights_path": "/tmp/fake_weights.pt"},
    }

    thumbs = _make_step_mock({"output_dir": "/tmp/t", "n_images": 0, "n_skipped": 0, "n_failed": 0, "params": {}, "params_hash": None, "cache_dir": None})
    bg = _make_step_mock({"output_path": "/tmp/bg.npy", "shape": (1, 1, 1), "params": {}})
    stats = _make_step_mock({"output_path": "/tmp/s.json", "num_flakes": 0, "params": {}})
    prox = _make_step_mock(
        {"distances_path": "/tmp/d", "flake_assignments_path": "/tmp/fa", "n_pairs": 0, "n_domains": 0, "n_flakes": 0, "params": {}}
    )

    sam_payloads = [
        {"type": "progress", "progress": 1.0, "message": "done"},
        {
            "type": "completed",
            "result": {"images": 1, "masks_total": 0, "errors": 0, "per_image": {}},
        },
    ]
    defer_mock = AsyncMock(return_value=None)

    with patch("flake_analysis.api.routes.run_pipeline.run_thumbnails_step", side_effect=thumbs), \
         patch("flake_analysis.api.routes.run_pipeline.run_background_step", side_effect=bg), \
         patch("flake_analysis.api.services.sam_dispatch.defer_sam_job", new=defer_mock), \
         patch("flake_analysis.api.routes.run_pipeline._stream_sam_events", side_effect=_fake_sam_stream(sam_payloads)), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_stats_step", side_effect=stats), \
         patch("flake_analysis.api.routes.run_pipeline.run_domain_proximity_step", side_effect=prox):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            status, events = await _post_pipeline_collect_events(client, pid, sid, body)
            assert status == 200

    done = [e for e in events if e["type"] == "pipeline_done"]
    assert len(done) == 1, f"expected 1 pipeline_done, got events={[e['type'] for e in events]}"
    assert done[0]["cascade"]["fired"] is True
    assert "sam" in done[0]["cascade"]["cleared_steps"]
    assert "domain_stats" in done[0]["cascade"]["cleared_steps"]
    # SAM was re-run after cascade — defer was called exactly once.
    assert defer_mock.call_count == 1

    # Assert post-run steps_done has all 4 marked True (background + sam + domain_stats + domain_proximity).
    refreshed = await pg_session.get(Analysis, pre_analysis_id)
    await pg_session.refresh(refreshed)
    sd = refreshed.steps_done
    assert sd.get("background") is True
    assert sd.get("sam") is True
    assert sd.get("domain_stats") is True
    assert sd.get("domain_proximity") is True
    # Background params persisted from the new body.
    assert refreshed.background_params == {"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"}
