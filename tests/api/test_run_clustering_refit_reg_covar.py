"""W3.5 — /run/clustering/refit reg_covar forwarding + auto_tune branch.

Manual path: schema reg_covar must reach the wrapper and be echoed back as
result.reg_covar_chosen. Auto-tune path: schema-supplied reg_covar must be
ignored; server picks from the candidate set and echoes the chosen value.
"""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import clustering as clustering_route
from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest
from flake_analysis.state.paths import analysis_folder

SID = 42


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(clustering_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _seed_analysis(tmp_path: Path, pid: str = "local", sid: int = SID) -> Path:
    """Build a minimal per-scan analysis folder with stats.npz + selection.parquet."""
    af = analysis_folder(tmp_path, pid, sid)
    (af / "02_domain_stats").mkdir(parents=True)
    (af / "03_selector").mkdir(parents=True)
    rng = np.random.default_rng(0)
    n = 30
    rgb = np.concatenate(
        [rng.normal(loc=0.2, scale=0.02, size=(n // 2, 3)),
         rng.normal(loc=0.8, scale=0.02, size=(n // 2, 3))]
    )
    flake_ids = np.arange(n, dtype=np.int64)
    np.savez(af / "02_domain_stats" / "stats.npz", repr_rgbs=rgb, flake_ids=flake_ids)
    pd.DataFrame({"domain_id": flake_ids, "selected": [True] * n}).to_parquet(
        af / "03_selector" / "selection.parquet", engine="pyarrow", index=False
    )
    m = Manifest(analysis_folder=str(af))
    m.steps["domain_stats"] = StepEntry(
        completed_at="x", params={}, params_hash="ds",
        input_hashes={}, outputs={}, reproducibility={},
    )
    m.steps["selector"] = StepEntry(
        completed_at="x", params={}, params_hash="sel",
        input_hashes={}, outputs={}, reproducibility={},
    )
    # Persist to disk so wrapper's load_manifest() picks up the prereq StepEntries.
    save_manifest(m, af)
    return af


def _find_done_payload(text: str) -> dict | None:
    """Parse the SSE buffer and return the parsed `done` event JSON, if any."""
    for chunk in text.split("\n\n"):
        if "event: done" in chunk:
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    return json.loads(line[len("data: "):])
    return None


@pytest.mark.asyncio
async def test_refit_forwards_reg_covar_and_echoes_in_done(tmp_path, monkeypatch):
    af = _seed_analysis(tmp_path)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "seed_groups": [
                {"name": "a", "domain_ids": [0, 1, 2]},
                {"name": "b", "domain_ids": [15, 16, 17]},
            ],
            "reg_covar": 3.0,
        }
        text = ""
        async with c.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/refit",
            json=body,
        ) as resp:
            async for chunk in resp.aiter_text():
                text += chunk
        done_event = _find_done_payload(text)
        assert done_event is not None, f"no done event in stream:\n{text}"
        assert done_event["result"]["reg_covar_chosen"] == 3.0

        # Manifest must record the value forwarded.
        entry = json.loads((af / "manifest.json").read_text())
        assert entry["steps"]["clustering"]["params"]["reg_covar"] == 3.0


@pytest.mark.asyncio
async def test_refit_auto_tune_returns_chosen_from_candidates(tmp_path, monkeypatch):
    """Auto-tune path must (a) call auto_tune_reg_covar, (b) ignore the schema's
    reg_covar, (c) forward the optimiser's pick to the wrapper, and (d) echo it
    in result.reg_covar_chosen + manifest.steps.clustering.params.reg_covar.

    We monkeypatch auto_tune_reg_covar to a sentinel return so the assertion
    distinguishes the auto-tune branch from the manual path on synthetic data.
    """
    sentinel = 3.0
    captured: dict[str, Any] = {}

    def fake_auto_tune(points, seeds, *args, **kwargs):
        captured["points_shape"] = tuple(points.shape)
        captured["seeds"] = [list(s) for s in seeds]
        return sentinel

    monkeypatch.setattr(
        "flake_analysis.api.routes.clustering.auto_tune_reg_covar",
        fake_auto_tune,
    )

    af = _seed_analysis(tmp_path)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "seed_groups": [
                {"name": "a", "domain_ids": [0, 1, 2]},
                {"name": "b", "domain_ids": [15, 16, 17]},
            ],
            "auto_tune": True,
            "reg_covar": 0.1,  # MUST be ignored when auto_tune=True
        }
        text = ""
        async with c.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/refit",
            json=body,
        ) as resp:
            async for chunk in resp.aiter_text():
                text += chunk
        done_event = _find_done_payload(text)
        assert done_event is not None, f"no done event in stream:\n{text}"
        chosen = done_event["result"]["reg_covar_chosen"]
        assert chosen == sentinel, "route did not use auto_tune_reg_covar's return"

        entry = json.loads((af / "manifest.json").read_text())
        # The wrapper received the optimiser's pick, NOT the schema's 0.1.
        assert entry["steps"]["clustering"]["params"]["reg_covar"] == sentinel
        assert entry["steps"]["clustering"]["params"]["reg_covar"] != 0.1

        # Sanity: route built the auto-tune inputs (RGB shape (n_selected, 3) and
        # two seed groups with three positions each).
        assert captured["points_shape"] == (30, 3)
        assert captured["seeds"] == [[0, 1, 2], [15, 16, 17]]
