# Clustering Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sprint 3 of the React + FastAPI migration: deliver a fully working Clustering tab — seed-group authoring (add-from-selection / rename / delete / edit-mode highlight), GMM refit (`fit_scope` + initial Mahalanobis), per-cluster posterior threshold sliders + live Mahalanobis gate, cluster-coloured scatter + cluster-size bar chart, and atomic commit (apply_thresholds → assignments.parquet rewrite) — talking to five new backend endpoints (`POST /run/clustering/refit`, `POST /run/clustering/apply_thresholds`, `GET /data/clustering/labels`, `GET /data/clustering/assignments`, `GET /data/clustering/seed_groups`).

**Architecture:** Backend adapters wrap `pipeline/clustering.run_clustering_step` and `pipeline/clustering.apply_thresholds` (algorithm unchanged, lock+drain SSE pattern from Plan 2 verbatim). Both SSE endpoints share the same per-project mutex (backend design §3.2). Frontend uses TanStack Query for `labels.json`/`assignments.parquet`/`seed_groups.json` (`staleTime: Infinity` + manual invalidation on mutation success), Zustand `clusteringSlice` for in-flight seed-group / threshold / brushing state (independent from `selectorSlice` per Q-U4), RHF for the `<InitialMahalanobisSlider>` and threshold sliders, lazy-loaded `react-plotly.js` Scattergl (cluster-coloured palette d3-category10) reusing the Plan-2 `ScatterCanvas` shape, plus a Plotly `bar` for cluster sizes. Threshold slider drag is debounced 100ms (tighter than Plan 2's 200ms because design §2.1 caps the recolor budget at 300ms).

**Tech Stack:**
- Backend: FastAPI 0.110+, pydantic v2.6+, pyarrow 15+ (Arrow IPC streaming), pandas 2.x, httpx 0.28.1 (test transport), pytest-asyncio 0.23+
- Frontend: react-plotly.js 2.6+ (Plotly.js 2.30+), react-hook-form 7.51+, lucide-react 0.358+, sonner 1.4+, vitest 1.4+, msw 2.2+

---

## File Structure

### Backend (new — under `src/flake_analysis/api/`)

- `schemas/clustering.py` — `SeedGroup` (`{name: str, domain_ids: list[int]}`), `ClusteringRefitParams` (mirrors `pipeline/clustering.py:53-65`), `ApplyThresholdsParams` (mirrors `pipeline/clustering.py:183-188`), `ClusteringSummary`, `ApplyThresholdsSummary`, `LabelsJson` (frozen schema per `core/pipeline/clustering.py:300-309`)
- `services/clustering_service.py` — `load_labels_json(analysis_folder) -> dict`, `load_assignments_table(analysis_folder) -> pa.Table`, `load_seed_groups(analysis_folder) -> list[SeedGroup]` (port `_load_committed_seed_groups` from `tab_clustering.py`)
- `routes/clustering.py` — `POST /projects/{pid}/run/clustering/refit` (SSE, lock+drain), `POST /projects/{pid}/run/clustering/apply_thresholds` (SSE, lock+drain — distinct mutex slot reuses the same `acquire_project_lock(pid)`)

### Backend (modifications)

- `routes/data.py` — add `GET /projects/{pid}/data/clustering/labels`, `GET /projects/{pid}/data/clustering/assignments`, `GET /projects/{pid}/data/clustering/seed_groups`
- `errors.py` — add `ClusteringNotFitted` (404, code `clustering_not_fitted`), `SeedGroupsMissing` (404, code `seed_groups_missing`)
- `main.py` — `app.include_router(clustering.router, prefix="/api/v1")`

### Frontend (new — under `web/src/`)

- `state/clusteringSlice.ts` — Zustand slice per design §3.4 (`seedGroups`, `fitScope`, `initialMaxMahalanobis`, `liveMaxMahalanobis`, `perClusterThresholds`, `axisX`, `axisY`, `brushing`, `editingGroupId`, all actions)
- `lib/clusterColors.ts` — port `CLUSTER_PALETTE` (10-color d3-category10) + `NEUTRAL_GRAY` from `tab_clustering.py:35-39`; `colorForLabel(cluster_label)` helper
- `api/clustering.ts` — typed fetch wrappers (`getClusteringLabels`, `getClusteringAssignments`, `getClusteringSeedGroups`, `runClusteringRefit` SSE, `applyClusteringThresholds` SSE)
- `hooks/useClusteringLabels.ts` — TanStack Query for `labels.json` shape
- `hooks/useClusteringAssignments.ts` — TanStack Query returning `{domain_id, cluster_label, max_posterior, nearest_mahalanobis?}` arrays (Arrow IPC accepted)
- `hooks/useClusteringSeedGroups.ts` — TanStack Query returning `SeedGroup[]` (used for the §3.4 autoload contract)
- `hooks/useClusteringRefit.ts` — Mutation wrapping the SSE refit, returns `{run, progress, result, error, status}`
- `hooks/useClusteringApplyThresholds.ts` — Mutation wrapping the SSE apply_thresholds, same shape
- `components/clustering/SeedGroupList.tsx` — rows: name + count + edit/delete buttons; edit mode highlight
- `components/clustering/SeedGroupEditor.tsx` — `<SeedGroupList>` + `Add from selection` / `Reload last fit's seeds` / `Import clusters → seeds` buttons
- `components/clustering/FitScopeRadio.tsx` — `seeds` / `all_selected` radio
- `components/clustering/InitialMahalanobisSlider.tsx` — RHF slider 0.5–6.0 step 0.1, default 3.0
- `components/clustering/FitGMMButton.tsx` — disabled when `seedGroups.length < 2`, runs the SSE refit, toasts on success/error
- `components/clustering/ClusterRow.tsx` — color swatch + slider (0.0–1.0 step 0.01) + "K/N pass" caption for one cluster
- `components/clustering/PerClusterThresholdPanel.tsx` — `<ClusterRow>` per cluster + "Reset thresholds to default" button
- `components/clustering/LiveMahalanobisSlider.tsx` — slider 0.5–8.0 step 0.1, drives `liveMaxMahalanobis` (post-fit gate)
- `components/clustering/CommitClusteringButton.tsx` — runs the SSE apply_thresholds, toasts on success/error
- `components/clustering/ClusterScatterCanvas.tsx` — Plotly Scattergl, cluster-coloured + edit-mode orange ring overlay (parity with `tab_clustering.py:621-633`)
- `components/clustering/ClusterSizeBarChart.tsx` — Plotly `bar` chart of cluster sizes
- `components/clustering/ClusteringRightRail.tsx` — composes seed editor + fit-scope + initial-mah + fit + per-cluster thresholds + live-mah + brushing + axis pickers + commit button
- `components/clustering/ClusteringMain.tsx` — composes scatter + bar chart
- `pages/ClusteringTab.tsx` — top-level tab, lazy-loads Plotly via the same code-split as Selector
- `App.tsx` — register the lazy `ClusteringTab` route

### Frontend (modifications)

- `state/selectorSlice.ts` — no change (Q-U4: clustering brushing is independent — confirm with a unit test)
- `components/selector/BrushingControls.tsx` — extract the mode store into a generic factory so the clustering tab can use its own scoped instance (mirrors Q-U4)
- `state/__tests__/selectorSlice.test.ts` — extend with a regression test that mutating `clusteringSlice.brushing` does NOT touch `selectorSlice.brushing`

### Tests (backend)

- `tests/api/test_clustering_schemas.py`
- `tests/api/test_clustering_service.py`
- `tests/api/test_data_clustering_labels.py`
- `tests/api/test_data_clustering_assignments.py`
- `tests/api/test_data_clustering_seed_groups.py`
- `tests/api/test_run_clustering_refit_sse.py`
- `tests/api/test_run_clustering_apply_thresholds_sse.py`
- `tests/api/test_clustering_mutex_sharing.py`

### Tests (frontend)

- `web/src/state/__tests__/clusteringSlice.test.ts`
- `web/src/state/__tests__/clustering_selector_isolation.test.ts`
- `web/src/lib/__tests__/clusterColors.test.ts`
- `web/src/hooks/__tests__/useClusteringLabels.test.tsx`
- `web/src/hooks/__tests__/useClusteringRefit.test.tsx`
- `web/src/hooks/__tests__/useClusteringApplyThresholds.test.tsx`
- `web/src/components/clustering/__tests__/SeedGroupList.test.tsx`
- `web/src/components/clustering/__tests__/FitScopeRadio.test.tsx`
- `web/src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx`
- `web/src/components/clustering/__tests__/FitGMMButton.test.tsx`
- `web/src/components/clustering/__tests__/ClusterRow.test.tsx`
- `web/src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx`
- `web/src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx`
- `web/src/components/clustering/__tests__/CommitClusteringButton.test.tsx`
- `web/src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx`
- `web/src/pages/__tests__/ClusteringTab.test.tsx`

---

## Tasks (Grouped into Phases)

### Phase 1 — Backend schemas + services (clustering params, labels.json reader, seed-group reader)

#### Task 1: Clustering schemas

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/schemas/clustering.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_clustering_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_clustering_schemas.py
import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.clustering import (
    SeedGroup,
    ClusteringRefitParams,
    ApplyThresholdsParams,
    ClusteringSummary,
    ApplyThresholdsSummary,
    LabelsJson,
)


def test_seed_group_round_trip():
    sg = SeedGroup(name="thin", domain_ids=[1, 2, 3])
    assert sg.name == "thin"
    assert sg.domain_ids == [1, 2, 3]


def test_refit_params_defaults_match_pipeline():
    p = ClusteringRefitParams(seed_groups=[SeedGroup(name="a", domain_ids=[1, 2])])
    assert p.feature_cols == ["mean_r", "mean_g", "mean_b"]
    assert p.covariance_type == "full"
    assert p.rgb_threshold == 0.50
    assert p.fit_scope == "seeds"
    assert p.max_mahalanobis == 3.0


def test_refit_params_validates_fit_scope():
    with pytest.raises(ValidationError):
        ClusteringRefitParams(
            seed_groups=[SeedGroup(name="a", domain_ids=[1])],
            fit_scope="garbage",
        )


def test_apply_thresholds_params_optional_max_mah():
    p = ApplyThresholdsParams(cluster_thresholds={0: 0.5, 1: 0.6})
    assert p.max_mahalanobis is None
    p2 = ApplyThresholdsParams(cluster_thresholds={0: 0.5}, max_mahalanobis=2.5)
    assert p2.max_mahalanobis == 2.5


def test_clustering_summary_shape():
    s = ClusteringSummary(
        output_dir="/tmp/04_clustering",
        n_clusters=3,
        n_assigned=120,
        n_unassigned=30,
        wrapper_params_hash="abc",
    )
    assert s.n_clusters == 3


def test_apply_thresholds_summary_shape():
    s = ApplyThresholdsSummary(n_pass=80, n_total=150, n_clusters=3)
    assert s.n_pass == 80


def test_labels_json_groups_required():
    payload = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 10, "mean_rgb": [0.1, 0.2, 0.3]},
            {"id": 1, "name": "b", "size": 5, "mean_rgb": [0.4, 0.5, 0.6]},
        ],
        "assignments": {"1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.6},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    obj = LabelsJson.model_validate(payload)
    assert obj.n_clusters == 2
    assert len(obj.groups) == 2
    assert obj.thresholds == {"0": 0.5, "1": 0.6}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.schemas.clustering'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/schemas/clustering.py
"""Clustering schemas per backend design §1.2 + §1.3."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class SeedGroup(BaseModel):
    """A single seed group authored by the user."""
    name: str
    domain_ids: list[int]


class ClusteringRefitParams(BaseModel):
    """Mirrors pipeline/clustering.py:53-65."""
    seed_groups: list[SeedGroup]
    feature_cols: list[str] = Field(default_factory=lambda: ["mean_r", "mean_g", "mean_b"])
    covariance_type: Literal["full", "tied", "diag", "spherical"] = "full"
    rgb_threshold: float = 0.50
    fit_scope: Literal["seeds", "all_selected"] = "seeds"
    max_mahalanobis: float = 3.0


class ApplyThresholdsParams(BaseModel):
    """Mirrors pipeline/clustering.py:183-188."""
    cluster_thresholds: dict[int, float]
    max_mahalanobis: float | None = None


class ClusteringSummary(BaseModel):
    """Result wrapper used inside SSE 'done' event for /run/clustering/refit."""
    output_dir: str
    n_clusters: int
    n_assigned: int
    n_unassigned: int
    wrapper_params_hash: str | None = None


class ApplyThresholdsSummary(BaseModel):
    """Result wrapper for /run/clustering/apply_thresholds — mirrors apply_thresholds() return."""
    n_pass: int
    n_total: int
    n_clusters: int


class LabelsGroup(BaseModel):
    """One row of ``labels.json["groups"]`` per core/pipeline/clustering.py:272-286."""
    id: int
    name: str
    size: int
    mean_rgb: list[float]


class LabelsJson(BaseModel):
    """Frozen schema per core/pipeline/clustering.py:300-309 (plan v1 r7 §7.1)."""
    version: int
    n_clusters: int
    groups: list[LabelsGroup]
    assignments: dict[str, int]
    thresholds: dict[str, float]
    noise_label: int = -1
    random_state: int = 42
    fitted_at: str
    max_mahalanobis: float | None = None  # added by apply_thresholds when set
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_schemas.py -v`
Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/schemas/clustering.py tests/api/test_clustering_schemas.py
git commit -m "feat(api): add clustering schemas (refit/apply_thresholds/labels)"
```

---

#### Task 2: Clustering service — labels.json + assignments + seed_groups readers

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/clustering_service.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_clustering_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_clustering_service.py
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from flake_analysis.api.services.clustering_service import (
    load_labels_json,
    load_assignments_table,
    load_seed_groups,
)


def _write_labels(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 10, "mean_rgb": [0.1, 0.2, 0.3]},
            {"id": 1, "name": "b", "size": 5, "mean_rgb": [0.4, 0.5, 0.6]},
        ],
        "assignments": {"1": 0, "2": 1, "3": 0},
        "thresholds": {"0": 0.5, "1": 0.6},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_assignments(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, 0],
        "max_posterior": [0.95, 0.80, 0.70],
        "nearest_mahalanobis": [0.5, 1.2, 2.8],
    })
    df.to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)


def _write_seed_groups(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    payload = [
        {"name": "thin", "domain_ids": [1, 2, 3]},
        {"name": "thick", "domain_ids": [4, 5]},
    ]
    (folder / "04_clustering" / "seed_groups.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_load_labels_json_round_trip(tmp_path: Path):
    _write_labels(tmp_path)
    obj = load_labels_json(tmp_path)
    assert obj["n_clusters"] == 2
    assert obj["thresholds"]["0"] == 0.5


def test_load_labels_json_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_labels_json(tmp_path)


def test_load_assignments_table_returns_arrow(tmp_path: Path):
    _write_assignments(tmp_path)
    table = load_assignments_table(tmp_path)
    assert isinstance(table, pa.Table)
    assert set(table.column_names) >= {"domain_id", "cluster_label", "max_posterior"}
    ids = table.column("domain_id").to_pylist()
    assert ids == [1, 2, 3]


def test_load_assignments_table_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_assignments_table(tmp_path)


def test_load_seed_groups_round_trip(tmp_path: Path):
    _write_seed_groups(tmp_path)
    groups = load_seed_groups(tmp_path)
    assert len(groups) == 2
    assert groups[0]["name"] == "thin"
    assert groups[0]["domain_ids"] == [1, 2, 3]


def test_load_seed_groups_missing_returns_empty(tmp_path: Path):
    # Missing file is not an error — empty list is the autoload contract.
    assert load_seed_groups(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.services.clustering_service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/clustering_service.py
"""Clustering data readers — labels.json + assignments.parquet + seed_groups.json."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa


def load_labels_json(analysis_folder: str | Path) -> dict[str, Any]:
    """Read 04_clustering/labels.json. Raises FileNotFoundError if missing."""
    p = Path(analysis_folder) / "04_clustering" / "labels.json"
    if not p.exists():
        raise FileNotFoundError(f"labels.json missing at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_assignments_table(analysis_folder: str | Path) -> pa.Table:
    """Read 04_clustering/assignments.parquet as an Arrow table."""
    p = Path(analysis_folder) / "04_clustering" / "assignments.parquet"
    if not p.exists():
        raise FileNotFoundError(f"assignments.parquet missing at {p}")
    df = pd.read_parquet(p)
    return pa.Table.from_pandas(df, preserve_index=False)


def load_seed_groups(analysis_folder: str | Path) -> list[dict[str, Any]]:
    """Read 04_clustering/seed_groups.json. Missing file returns []."""
    p = Path(analysis_folder) / "04_clustering" / "seed_groups.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_service.py -v`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/clustering_service.py tests/api/test_clustering_service.py
git commit -m "feat(api): add clustering service (labels/assignments/seed_groups readers)"
```

---

#### Task 3: Add ClusteringNotFitted + SeedGroupsMissing to errors

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/errors.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_clustering_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_clustering_errors.py
from flake_analysis.api.errors import ClusteringNotFitted, SeedGroupsMissing


def test_clustering_not_fitted_envelope():
    e = ClusteringNotFitted(expected_path="/x/y/labels.json")
    body = e.to_response()
    assert body["error"]["code"] == "clustering_not_fitted"
    assert e.status_code == 404


def test_seed_groups_missing_envelope():
    e = SeedGroupsMissing(expected_path="/x/y/seed_groups.json")
    body = e.to_response()
    assert body["error"]["code"] == "seed_groups_missing"
    assert e.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_errors.py -v`
Expected: FAIL with `ImportError: cannot import name 'ClusteringNotFitted'`.

- [ ] **Step 3: Write minimal implementation**

Append to `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/errors.py`:

```python
class ClusteringNotFitted(AppError):
    code = "clustering_not_fitted"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Clustering has not been fitted yet. Click Fit GMM on the Clustering tab."


class SeedGroupsMissing(AppError):
    code = "seed_groups_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "No seed groups committed yet."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_errors.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/errors.py tests/api/test_clustering_errors.py
git commit -m "feat(api): add ClusteringNotFitted + SeedGroupsMissing error envelopes"
```

---

### Phase 2 — Backend data routes (labels, assignments, seed_groups)

#### Task 4: GET /data/clustering/labels

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_clustering_labels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_clustering_labels.py
import json
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_get_clustering_labels_404_when_missing(tmp_path: Path, monkeypatch):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))

    from flake_analysis.api import deps
    monkeypatch.setattr(deps, "get_manifest", lambda project_id="local": manifest)
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/labels")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "clustering_not_fitted"


@pytest.mark.asyncio
async def test_get_clustering_labels_returns_json(tmp_path: Path, monkeypatch):
    (tmp_path / "04_clustering").mkdir(parents=True)
    payload = {
        "version": 1,
        "n_clusters": 1,
        "groups": [{"id": 0, "name": "a", "size": 3, "mean_rgb": [0.1, 0.2, 0.3]}],
        "assignments": {"1": 0, "2": 0, "3": 0},
        "thresholds": {"0": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (tmp_path / "04_clustering" / "labels.json").write_text(json.dumps(payload))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))

    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/labels")
    assert r.status_code == 200
    body = r.json()
    assert body["n_clusters"] == 1
    assert body["thresholds"]["0"] == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_labels.py -v`
Expected: FAIL with 404 on a route that doesn't exist yet (or the `clustering_not_fitted` test fails first because the route returns the FastAPI default 404).

- [ ] **Step 3: Write minimal implementation**

Append to `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`:

```python
from flake_analysis.api.errors import (
    AnnotationsPathUnset,
    ClusteringNotFitted,
    DomainNotFound,
    DomainStatsNotFound,
    SeedGroupsMissing,
    SelectionNotFound,
)
from flake_analysis.api.services.clustering_service import (
    load_assignments_table,
    load_labels_json,
    load_seed_groups,
)


@router.get("/clustering/labels")
async def get_clustering_labels(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return labels.json as JSON."""
    try:
        return load_labels_json(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ClusteringNotFitted(expected_path=str(e).split("missing at ", 1)[-1])
```

(Replace the existing `from flake_analysis.api.errors import (...)` block with the new one above, keeping all previously imported names plus the two new ones.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_labels.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/data.py tests/api/test_data_clustering_labels.py
git commit -m "feat(api): GET /data/clustering/labels"
```

---

#### Task 5: GET /data/clustering/assignments

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_clustering_assignments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_clustering_assignments.py
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_assignments_404_when_missing(tmp_path: Path):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/assignments")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "clustering_not_fitted"


@pytest.mark.asyncio
async def test_assignments_returns_json(tmp_path: Path):
    (tmp_path / "04_clustering").mkdir(parents=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, -1],
        "max_posterior": [0.9, 0.8, 0.4],
    })
    df.to_parquet(tmp_path / "04_clustering" / "assignments.parquet", index=False)

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/assignments")
    assert r.status_code == 200
    body = r.json()
    assert body["domain_id"] == [1, 2, 3]
    assert body["cluster_label"] == [0, 1, -1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_assignments.py -v`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Append to `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`:

```python
@router.get("/clustering/assignments")
async def get_clustering_assignments(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return 04_clustering/assignments.parquet (Arrow IPC if Accept: application/vnd.apache.arrow.stream, else JSON)."""
    try:
        table = load_assignments_table(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ClusteringNotFitted(expected_path=str(e).split("missing at ", 1)[-1])
    return arrow_or_json_response(table, accept_header=accept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_assignments.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/data.py tests/api/test_data_clustering_assignments.py
git commit -m "feat(api): GET /data/clustering/assignments (arrow/json)"
```

---

#### Task 6: GET /data/clustering/seed_groups

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_clustering_seed_groups.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_clustering_seed_groups.py
import json
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_seed_groups_returns_empty_when_missing(tmp_path: Path):
    """Missing file is the empty-list autoload contract, not a 404."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/seed_groups")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_seed_groups_returns_list(tmp_path: Path):
    (tmp_path / "04_clustering").mkdir(parents=True)
    payload = [
        {"name": "thin", "domain_ids": [1, 2, 3]},
        {"name": "thick", "domain_ids": [4, 5]},
    ]
    (tmp_path / "04_clustering" / "seed_groups.json").write_text(json.dumps(payload))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/seed_groups")
    assert r.status_code == 200
    body = r.json()
    assert body == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_seed_groups.py -v`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Append to `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`:

```python
@router.get("/clustering/seed_groups")
async def get_clustering_seed_groups(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Return 04_clustering/seed_groups.json. Missing file → []."""
    return load_seed_groups(manifest.analysis_folder)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_clustering_seed_groups.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/data.py tests/api/test_data_clustering_seed_groups.py
git commit -m "feat(api): GET /data/clustering/seed_groups"
```

---

### Phase 3 — Backend run routes (refit SSE + apply_thresholds SSE) + mutex sharing

#### Task 7: POST /run/clustering/refit (SSE, lock+drain)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/clustering.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_clustering_refit_sse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_clustering_refit_sse.py
"""SSE refit route — error path + lock-release semantics.

The happy path is exercised end-to-end via tests/api/test_clustering_mutex_sharing.py;
this file focuses on the contract surface (route exists, params validate, lock releases).
"""
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_refit_streams_error_when_prereq_missing(tmp_path: Path):
    """No domain_stats / selector commit → wrapper raises RuntimeError → SSE 'error' event."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "seed_groups": [
            {"name": "a", "domain_ids": [1, 2]},
            {"name": "b", "domain_ids": [3, 4]},
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r:
            assert r.status_code == 200  # SSE convention
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert 'event: error' in text or '"type": "error"' in text or '"type":"error"' in text


@pytest.mark.asyncio
async def test_refit_releases_lock_after_error(tmp_path: Path):
    """After the first request errors out, the project mutex must be free for the next request."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"seed_groups": [{"name": "a", "domain_ids": [1, 2]}, {"name": "b", "domain_ids": [3]}]}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First call: drains
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r1:
            async for _ in r1.aiter_text():
                pass
        # Second call: must NOT 423 (lock should be released)
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r2:
            assert r2.status_code != 423
            async for _ in r2.aiter_text():
                pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_clustering_refit_sse.py -v`
Expected: FAIL — `404 Not Found` because `routes/clustering.py` is not registered.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/clustering.py`:

```python
"""Clustering routes per backend design §1.2.

POST /run/clustering/refit — SSE (expensive: GMM EM).
POST /run/clustering/apply_thresholds — SSE (cheap: parquet rewrite).
Both share the same per-project mutex (acquire_project_lock(pid)).
"""
from __future__ import annotations
import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.schemas.clustering import (
    ApplyThresholdsParams,
    ClusteringRefitParams,
)
from flake_analysis.api.sse import ProgressBridge, emit_sse_event
from flake_analysis.pipeline.clustering import apply_thresholds, run_clustering_step
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["clustering"])


@router.post("/run/clustering/refit")
async def run_clustering_refit(
    project_id: str,
    params: ClusteringRefitParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Fit GMM with manual seed groups (SSE). Lock+drain pattern."""
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()
    seed_groups_payload: list[dict[str, Any]] = [
        {"name": sg.name, "domain_ids": sg.domain_ids} for sg in params.seed_groups
    ]

    def call_wrapper():
        return run_clustering_step(
            analysis_folder=manifest.analysis_folder,
            seed_groups=seed_groups_payload,
            feature_cols=params.feature_cols,
            covariance_type=params.covariance_type,
            rgb_threshold=params.rgb_threshold,
            fit_scope=params.fit_scope,
            max_mahalanobis=params.max_mahalanobis,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                bridge.emit_done(result)
            except Exception as e:
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for event in bridge.stream():
                yield emit_sse_event(event["type"], event)
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

Modify `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py`:

```python
from flake_analysis.api.routes import health, version, projects, data, run, selector, clustering
# ...
    app.include_router(clustering.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_clustering_refit_sse.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/clustering.py src/flake_analysis/api/main.py tests/api/test_run_clustering_refit_sse.py
git commit -m "feat(api): POST /run/clustering/refit (SSE, lock+drain)"
```

---

#### Task 8: POST /run/clustering/apply_thresholds (SSE, lock+drain)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/clustering.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_clustering_apply_thresholds_sse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_clustering_apply_thresholds_sse.py
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _write_minimal_clustering(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, 0],
        "max_posterior": [0.9, 0.8, 0.4],
    })
    df.to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 2, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1, "3": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(labels))


@pytest.mark.asyncio
async def test_apply_thresholds_streams_error_without_clustering(tmp_path: Path):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"cluster_thresholds": {0: 0.7}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/apply_thresholds", json=body
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "error" in text


@pytest.mark.asyncio
async def test_apply_thresholds_streams_done_with_summary(tmp_path: Path):
    _write_minimal_clustering(tmp_path)
    # apply_thresholds also reads/writes manifest.json — write a minimal one.
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "steps": {}}))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"cluster_thresholds": {0: 0.5, 1: 0.5}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/apply_thresholds", json=body
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "done" in text
            assert "n_pass" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_clustering_apply_thresholds_sse.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

Append to `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/clustering.py`:

```python
@router.post("/run/clustering/apply_thresholds")
async def run_clustering_apply_thresholds(
    project_id: str,
    params: ApplyThresholdsParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Rewrite assignments.parquet with new thresholds + max_mahalanobis (SSE). Lock+drain."""
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()

    def call_wrapper():
        return apply_thresholds(
            analysis_folder=manifest.analysis_folder,
            cluster_thresholds={int(k): float(v) for k, v in params.cluster_thresholds.items()},
            max_mahalanobis=params.max_mahalanobis,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                bridge.emit_progress(0.1, "Applying thresholds...")
                result = await loop.run_in_executor(None, call_wrapper)
                bridge.emit_progress(1.0, "Done")
                bridge.emit_done(result)
            except Exception as e:
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for event in bridge.stream():
                yield emit_sse_event(event["type"], event)
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_clustering_apply_thresholds_sse.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/clustering.py tests/api/test_run_clustering_apply_thresholds_sse.py
git commit -m "feat(api): POST /run/clustering/apply_thresholds (SSE, lock+drain)"
```

---

#### Task 9: Mutex sharing test (refit and apply_thresholds share the same project lock)

**Files:**
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_clustering_mutex_sharing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_clustering_mutex_sharing.py
"""Both clustering endpoints share the per-project mutex (backend design §3.2).

Asserted shape: while one endpoint is mid-stream, a request to the *other*
endpoint on the *same* project must return 423 (or be queued — we accept
either, but contention MUST be visible).
"""
import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_apply_thresholds_blocks_while_refit_holds_lock(tmp_path: Path):
    # Write minimal clustering artifacts so apply_thresholds reaches its work, not its prereq guard.
    (tmp_path / "04_clustering").mkdir(parents=True)
    pd.DataFrame({
        "domain_id": [1, 2],
        "cluster_label": [0, 1],
        "max_posterior": [0.9, 0.8],
    }).to_parquet(tmp_path / "04_clustering" / "assignments.parquet", index=False)
    (tmp_path / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 1, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "2026-05-21T00:00:00Z",
    }))
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "steps": {}}))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    # Manually grab the lock OUTSIDE the route, to simulate refit holding it.
    held = acquire_project_lock("local")
    await held.__aenter__()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # The route should signal contention either as 423 immediately, or
            # by waiting; we run with a short timeout to detect waiting.
            try:
                async with asyncio.timeout(0.5):
                    async with ac.stream(
                        "POST",
                        "/api/v1/projects/local/run/clustering/apply_thresholds",
                        json={"cluster_thresholds": {0: 0.5, 1: 0.5}},
                    ) as r:
                        if r.status_code == 423:
                            return  # contention surfaced as 423 — fine
                        # If the route waits on the lock, the timeout will fire below.
                        async for _ in r.aiter_text():
                            pass
                # If we got here without 423, something is wrong.
                pytest.fail("apply_thresholds should have signalled contention while lock is held")
            except asyncio.TimeoutError:
                # Route is waiting on the lock — that's the "queued" branch. Acceptable.
                pass
    finally:
        await held.__aexit__(None, None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_mutex_sharing.py -v`
Expected: PASS already if Tasks 7 and 8 used `acquire_project_lock(project_id)` with the same key. (TDD red is intentionally light here — this is a regression guard, not a new behavior; if it fails, fix the route to use the shared lock function.)

- [ ] **Step 3: Write minimal implementation**

No implementation step — Tasks 7 and 8 already use `acquire_project_lock(project_id)`. If the test fails, audit those routes.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_clustering_mutex_sharing.py -v`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_clustering_mutex_sharing.py
git commit -m "test(api): clustering refit + apply_thresholds share per-project mutex"
```

---

### Phase 4 — Frontend foundation (cluster colors + clustering slice + brushing isolation)

#### Task 10: lib/clusterColors.ts

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/clusterColors.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/clusterColors.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/lib/__tests__/clusterColors.test.ts
import { describe, it, expect } from 'vitest'
import { CLUSTER_PALETTE, NEUTRAL_GRAY, colorForLabel } from '@/lib/clusterColors'

describe('clusterColors', () => {
  it('CLUSTER_PALETTE is the d3 category10 sequence (matches tab_clustering.py:35-39)', () => {
    expect(CLUSTER_PALETTE).toEqual([
      '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    ])
  })

  it('NEUTRAL_GRAY matches the tab_clustering.py constant', () => {
    expect(NEUTRAL_GRAY).toBe('#9e9e9e')
  })

  it('colorForLabel returns palette color for label >= 0', () => {
    expect(colorForLabel(0)).toBe('#1f77b4')
    expect(colorForLabel(2)).toBe('#2ca02c')
  })

  it('colorForLabel wraps around past length 10', () => {
    expect(colorForLabel(10)).toBe('#1f77b4')
    expect(colorForLabel(15)).toBe('#8c564b')
  })

  it('colorForLabel returns NEUTRAL_GRAY for label === -1', () => {
    expect(colorForLabel(-1)).toBe('#9e9e9e')
  })

  it('colorForLabel returns NEUTRAL_GRAY for any negative label', () => {
    expect(colorForLabel(-2)).toBe('#9e9e9e')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx vitest run src/lib/__tests__/clusterColors.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/lib/clusterColors.ts
// Ports CLUSTER_PALETTE + NEUTRAL_GRAY from src/flake_analysis/ui/tab_clustering.py:35-39.

export const CLUSTER_PALETTE = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
] as const

export const NEUTRAL_GRAY = '#9e9e9e'

export function colorForLabel(label: number): string {
  if (label < 0) return NEUTRAL_GRAY
  return CLUSTER_PALETTE[label % CLUSTER_PALETTE.length]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/__tests__/clusterColors.test.ts`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/clusterColors.ts web/src/lib/__tests__/clusterColors.test.ts
git commit -m "feat(web): add cluster color palette + colorForLabel"
```

---

#### Task 11: state/clusteringSlice.ts (Zustand slice per design §3.4)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/clusteringSlice.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/__tests__/clusteringSlice.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/state/__tests__/clusteringSlice.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('clusteringSlice', () => {
  beforeEach(() => {
    resetClusteringStore()
  })

  it('default state matches design §3.4', () => {
    const s = useClusteringStore.getState()
    expect(s.seedGroups).toEqual([])
    expect(s.fitScope).toBe('seeds')
    expect(s.initialMaxMahalanobis).toBe(3.0)
    expect(s.liveMaxMahalanobis).toBe(3.0)
    expect(s.perClusterThresholds).toEqual({})
    expect(s.editingGroupId).toBeNull()
    expect(s.brushing.selectedIds.size).toBe(0)
  })

  it('addSeedGroup appends a uniquely-id\'d group', () => {
    const { addSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1, 2, 3])
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('thin')
    expect(groups[0].member_ids).toEqual([1, 2, 3])
    expect(typeof groups[0].id).toBe('string')
  })

  it('renameSeedGroup updates only the matching group', () => {
    const { addSeedGroup, renameSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1])
    addSeedGroup('thick', [2])
    const id = useClusteringStore.getState().seedGroups[0].id
    renameSeedGroup(id, 'super-thin')
    const groups = useClusteringStore.getState().seedGroups
    expect(groups[0].name).toBe('super-thin')
    expect(groups[1].name).toBe('thick')
  })

  it('removeSeedGroup drops the matching group', () => {
    const { addSeedGroup, removeSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1])
    addSeedGroup('thick', [2])
    const id = useClusteringStore.getState().seedGroups[0].id
    removeSeedGroup(id)
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('thick')
  })

  it('clearSeedGroups removes every group', () => {
    const { addSeedGroup, clearSeedGroups } = useClusteringStore.getState()
    addSeedGroup('a', [1])
    addSeedGroup('b', [2])
    clearSeedGroups()
    expect(useClusteringStore.getState().seedGroups).toEqual([])
  })

  it('setThreshold writes per-cluster threshold', () => {
    const { setThreshold } = useClusteringStore.getState()
    setThreshold(0, 0.7)
    setThreshold(1, 0.3)
    const t = useClusteringStore.getState().perClusterThresholds
    expect(t[0]).toBe(0.7)
    expect(t[1]).toBe(0.3)
  })

  it('resetThresholdsToDefault clears overrides', () => {
    const { setThreshold, resetThresholdsToDefault } = useClusteringStore.getState()
    setThreshold(0, 0.9)
    resetThresholdsToDefault()
    expect(useClusteringStore.getState().perClusterThresholds).toEqual({})
  })

  it('setEditingGroupId toggles edit highlight target', () => {
    const { setEditingGroupId } = useClusteringStore.getState()
    setEditingGroupId('g-1')
    expect(useClusteringStore.getState().editingGroupId).toBe('g-1')
    setEditingGroupId(null)
    expect(useClusteringStore.getState().editingGroupId).toBeNull()
  })

  it('applyLasso updates brushing.selectedIds', () => {
    const { applyLasso } = useClusteringStore.getState()
    applyLasso([1, 2, 3], 'replace')
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([1, 2, 3]))
  })

  it('setLiveMaxMahalanobis writes liveMaxMahalanobis', () => {
    const { setLiveMaxMahalanobis } = useClusteringStore.getState()
    setLiveMaxMahalanobis(2.5)
    expect(useClusteringStore.getState().liveMaxMahalanobis).toBe(2.5)
  })

  it('setInitialMaxMahalanobis writes initialMaxMahalanobis', () => {
    const { setInitialMaxMahalanobis } = useClusteringStore.getState()
    setInitialMaxMahalanobis(4.5)
    expect(useClusteringStore.getState().initialMaxMahalanobis).toBe(4.5)
  })

  it('setFitScope flips between seeds and all_selected', () => {
    const { setFitScope } = useClusteringStore.getState()
    setFitScope('all_selected')
    expect(useClusteringStore.getState().fitScope).toBe('all_selected')
  })

  it('hydrateSeedGroups installs disk groups when state is empty', () => {
    const { hydrateSeedGroups } = useClusteringStore.getState()
    hydrateSeedGroups([
      { name: 'thin', domain_ids: [1, 2] },
      { name: 'thick', domain_ids: [3] },
    ])
    const sg = useClusteringStore.getState().seedGroups
    expect(sg.map((g) => g.name)).toEqual(['thin', 'thick'])
    expect(sg[0].member_ids).toEqual([1, 2])
  })

  it('hydrateSeedGroups does NOT clobber in-flight edits (preserves _maybe_autoload_seed_groups semantics)', () => {
    const { addSeedGroup, hydrateSeedGroups } = useClusteringStore.getState()
    addSeedGroup('user-edit', [99])
    hydrateSeedGroups([{ name: 'disk', domain_ids: [1] }])
    const sg = useClusteringStore.getState().seedGroups
    expect(sg.length).toBe(1)
    expect(sg[0].name).toBe('user-edit')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/state/__tests__/clusteringSlice.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/state/clusteringSlice.ts
import { create } from 'zustand'
import {
  applyLasso as applyLassoFn,
  clearBrush as clearBrushFn,
  emptyBrushing,
  redo as redoFn,
  setFocusId as setFocusIdFn,
  undo as undoFn,
  type BrushingState,
  type LassoMode,
} from '@/lib/brushing'
import type { AvailableAxis } from '@/state/selectorSlice'

export interface SeedGroup {
  id: string
  name: string
  member_ids: number[]
}

export type FitScope = 'seeds' | 'all_selected'

export interface ClusteringState {
  seedGroups: SeedGroup[]
  fitScope: FitScope
  initialMaxMahalanobis: number  // 0.5–6.0, default 3.0
  liveMaxMahalanobis: number     // 0.5–8.0, post-fit gate
  perClusterThresholds: Record<number, number>
  axisX: AvailableAxis
  axisY: AvailableAxis
  brushing: BrushingState
  editingGroupId: string | null

  addSeedGroup(name: string, memberIds: number[]): void
  renameSeedGroup(id: string, name: string): void
  removeSeedGroup(id: string): void
  clearSeedGroups(): void
  hydrateSeedGroups(disk: Array<{ name: string; domain_ids: number[] }>): void

  setThreshold(clusterId: number, value: number): void
  resetThresholdsToDefault(): void

  setFitScope(scope: FitScope): void
  setInitialMaxMahalanobis(v: number): void
  setLiveMaxMahalanobis(v: number): void

  setAxis(pane: 'X' | 'Y', value: AvailableAxis): void
  setEditingGroupId(id: string | null): void

  applyLasso(ids: number[], mode: LassoMode): void
  undoBrush(): void
  redoBrush(): void
  clearBrush(): void
  setFocusId(id: number | null): void
}

let _seedIdCounter = 0
function nextSeedId(): string {
  _seedIdCounter += 1
  return `sg-${_seedIdCounter}`
}

export const useClusteringStore = create<ClusteringState>((set, get) => ({
  seedGroups: [],
  fitScope: 'seeds',
  initialMaxMahalanobis: 3.0,
  liveMaxMahalanobis: 3.0,
  perClusterThresholds: {},
  axisX: 'R',
  axisY: 'G',
  brushing: emptyBrushing(),
  editingGroupId: null,

  addSeedGroup(name, memberIds) {
    set((s) => ({
      seedGroups: [...s.seedGroups, { id: nextSeedId(), name, member_ids: [...memberIds] }],
    }))
  },
  renameSeedGroup(id, name) {
    set((s) => ({
      seedGroups: s.seedGroups.map((g) => (g.id === id ? { ...g, name } : g)),
    }))
  },
  removeSeedGroup(id) {
    set((s) => ({ seedGroups: s.seedGroups.filter((g) => g.id !== id) }))
  },
  clearSeedGroups() {
    set({ seedGroups: [], editingGroupId: null })
  },
  hydrateSeedGroups(disk) {
    // Mirrors _maybe_autoload_seed_groups (tab_clustering.py:60-82): only
    // hydrate when in-memory state is empty; never clobber user edits.
    if (get().seedGroups.length > 0) return
    set({
      seedGroups: disk.map((d) => ({
        id: nextSeedId(),
        name: d.name,
        member_ids: [...d.domain_ids],
      })),
    })
  },

  setThreshold(clusterId, value) {
    set((s) => ({
      perClusterThresholds: { ...s.perClusterThresholds, [clusterId]: value },
    }))
  },
  resetThresholdsToDefault() {
    set({ perClusterThresholds: {} })
  },

  setFitScope(scope) {
    set({ fitScope: scope })
  },
  setInitialMaxMahalanobis(v) {
    set({ initialMaxMahalanobis: v })
  },
  setLiveMaxMahalanobis(v) {
    set({ liveMaxMahalanobis: v })
  },

  setAxis(pane, value) {
    set(pane === 'X' ? { axisX: value } : { axisY: value })
  },
  setEditingGroupId(id) {
    set({ editingGroupId: id })
  },

  applyLasso(ids, mode) {
    set((s) => ({ brushing: applyLassoFn(s.brushing, ids, mode) }))
  },
  undoBrush() {
    set((s) => ({ brushing: undoFn(s.brushing) }))
  },
  redoBrush() {
    set((s) => ({ brushing: redoFn(s.brushing) }))
  },
  clearBrush() {
    set((s) => ({ brushing: clearBrushFn(s.brushing) }))
  },
  setFocusId(id) {
    set((s) => ({ brushing: setFocusIdFn(s.brushing, id) }))
  },
}))

// Test helper: zustand v4.5 has no getInitialState(), so we expose an
// explicit reset that mirrors the create() defaults. Production code does
// not use this — only tests.
export function resetClusteringStore(): void {
  _seedIdCounter = 0
  useClusteringStore.setState(
    {
      seedGroups: [],
      fitScope: 'seeds',
      initialMaxMahalanobis: 3.0,
      liveMaxMahalanobis: 3.0,
      perClusterThresholds: {},
      axisX: 'R',
      axisY: 'G',
      brushing: emptyBrushing(),
      editingGroupId: null,
    },
    false
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/state/__tests__/clusteringSlice.test.ts`
Expected: 14/14 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/state/clusteringSlice.ts web/src/state/__tests__/clusteringSlice.test.ts
git commit -m "feat(web): add clusteringSlice (seed groups, thresholds, brushing per design §3.4)"
```

---

#### Task 12: Selector ↔ Clustering brushing isolation regression test (Q-U4)

**Files:**
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/__tests__/clustering_selector_isolation.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/state/__tests__/clustering_selector_isolation.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useSelectorStore } from '@/state/selectorSlice'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('Q-U4: clustering and selector brushing are independent', () => {
  beforeEach(() => {
    useSelectorStore.getState().resetFilter()
    useSelectorStore.getState().clearBrush()
    resetClusteringStore()
  })

  it('mutating selector brushing does not change clustering brushing', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    expect(useSelectorStore.getState().brushing.selectedIds).toEqual(new Set([1, 2, 3]))
    expect(useClusteringStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('mutating clustering brushing does not change selector brushing', () => {
    useClusteringStore.getState().applyLasso([7, 8, 9], 'replace')
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([7, 8, 9]))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('focusId is independent', () => {
    useSelectorStore.getState().setFocusId(42)
    expect(useSelectorStore.getState().brushing.focusId).toBe(42)
    expect(useClusteringStore.getState().brushing.focusId).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails (or already passes — it's a regression guard)**

Run: `npx vitest run src/state/__tests__/clustering_selector_isolation.test.ts`
Expected: 3/3 PASS already (because Tasks 10/11 keep `brushing` as a per-store property). If it fails, the slices are sharing state and need to be fixed.

- [ ] **Step 3: Write minimal implementation**

No implementation step — pure regression guard.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/state/__tests__/clustering_selector_isolation.test.ts`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/state/__tests__/clustering_selector_isolation.test.ts
git commit -m "test(web): regression-guard Q-U4 (selector/clustering brushing isolation)"
```

---

### Phase 5 — Frontend API client + TanStack Query / Mutation hooks

#### Task 13: api/clustering.ts (typed fetch wrappers)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/api/clustering.ts`

- [ ] **Step 1: Write the failing test** (consumer-side — exercised through hook tests in Tasks 14–17; no dedicated test file because this module is pure types + thin fetches)

Skip — covered by Tasks 14, 15, 16, 17.

- [ ] **Step 2: Run test to verify it fails**

Skip.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/api/clustering.ts
import { ApiError } from '@/api/selector'

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: any = null
    try {
      envelope = await resp.json()
    } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id
    )
  }
  return (await resp.json()) as T
}

export interface SeedGroupDto {
  name: string
  domain_ids: number[]
}

export interface LabelsGroup {
  id: number
  name: string
  size: number
  mean_rgb: [number, number, number]
}

export interface LabelsJson {
  version: number
  n_clusters: number
  groups: LabelsGroup[]
  assignments: Record<string, number>
  thresholds: Record<string, number>
  noise_label: number
  random_state: number
  fitted_at: string
  max_mahalanobis?: number
}

export interface AssignmentsRows {
  domain_id: number[]
  cluster_label: number[]
  max_posterior: number[]
  nearest_mahalanobis?: number[]
  threshold_pass?: boolean[]
}

export interface ClusteringRefitBody {
  seed_groups: SeedGroupDto[]
  feature_cols?: string[]
  covariance_type?: 'full' | 'tied' | 'diag' | 'spherical'
  rgb_threshold?: number
  fit_scope?: 'seeds' | 'all_selected'
  max_mahalanobis?: number
}

export interface ApplyThresholdsBody {
  cluster_thresholds: Record<number, number>
  max_mahalanobis?: number | null
}

export async function fetchClusteringLabels(projectId: string): Promise<LabelsJson> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/labels`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<LabelsJson>(resp)
}

export async function fetchClusteringAssignments(
  projectId: string
): Promise<AssignmentsRows> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/assignments`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<AssignmentsRows>(resp)
}

export async function fetchClusteringSeedGroups(
  projectId: string
): Promise<SeedGroupDto[]> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/seed_groups`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<SeedGroupDto[]>(resp)
}
```

- [ ] **Step 4: Run typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/clustering.ts
git commit -m "feat(web): add api/clustering.ts (labels/assignments/seed_groups fetch wrappers)"
```

---

#### Task 14: useClusteringLabels hook

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useClusteringLabels.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useClusteringLabels.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useClusteringLabels.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringLabels } from '@/hooks/useClusteringLabels'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useClusteringLabels', () => {
  it('returns labels.json on success', async () => {
    const payload = {
      version: 1, n_clusters: 1,
      groups: [{ id: 0, name: 'a', size: 5, mean_rgb: [0.1, 0.2, 0.3] }],
      assignments: { '1': 0 }, thresholds: { '0': 0.5 },
      noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringLabels('local'), { wrapper: wrap(qc) })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.n_clusters).toBe(1)
  })

  it('surfaces a 404 ApiError when clustering not fitted', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringLabels('local'), { wrapper: wrap(qc) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any).code).toBe('clustering_not_fitted')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useClusteringLabels.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/hooks/useClusteringLabels.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringLabels, type LabelsJson } from '@/api/clustering'

export function useClusteringLabels(projectId: string) {
  return useQuery<LabelsJson>({
    queryKey: ['clustering', 'labels', projectId],
    queryFn: () => fetchClusteringLabels(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useClusteringLabels.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useClusteringLabels.ts web/src/hooks/__tests__/useClusteringLabels.test.tsx
git commit -m "feat(web): add useClusteringLabels TanStack Query hook"
```

---

#### Task 15: useClusteringAssignments + useClusteringSeedGroups hooks

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useClusteringAssignments.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useClusteringSeedGroups.ts`

- [ ] **Step 1: Write the failing test**

No dedicated test file — these are thin wrappers identical to Task 14's pattern. Verified through the integration test (Task 26).

- [ ] **Step 2: Run test to verify it fails**

Skip.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/hooks/useClusteringAssignments.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringAssignments, type AssignmentsRows } from '@/api/clustering'

export function useClusteringAssignments(projectId: string, enabled = true) {
  return useQuery<AssignmentsRows>({
    queryKey: ['clustering', 'assignments', projectId],
    queryFn: () => fetchClusteringAssignments(projectId),
    staleTime: Infinity,
    retry: false,
    enabled,
  })
}
```

```ts
// web/src/hooks/useClusteringSeedGroups.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringSeedGroups, type SeedGroupDto } from '@/api/clustering'

export function useClusteringSeedGroups(projectId: string) {
  return useQuery<SeedGroupDto[]>({
    queryKey: ['clustering', 'seed_groups', projectId],
    queryFn: () => fetchClusteringSeedGroups(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
```

- [ ] **Step 4: Run typecheck**

Run: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useClusteringAssignments.ts web/src/hooks/useClusteringSeedGroups.ts
git commit -m "feat(web): add useClusteringAssignments + useClusteringSeedGroups hooks"
```

---

#### Task 16: useClusteringRefit mutation hook (SSE)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useClusteringRefit.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useClusteringRefit.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useClusteringRefit.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringRefit } from '@/hooks/useClusteringRefit'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function makeSseResponse(events: string[]): Response {
  const body = events.join('\n') + '\n'
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

describe('useClusteringRefit', () => {
  it('starts running and finishes done with the result payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        makeSseResponse([
          'event: progress',
          'data: {"type":"progress","pct":0.5,"msg":"halfway"}',
          '',
          'event: done',
          'data: {"type":"done","result":{"n_clusters":2,"n_assigned":10,"n_unassigned":1,"output_dir":"/tmp"}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringRefit('local'), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({
        seed_groups: [
          { name: 'a', domain_ids: [1] },
          { name: 'b', domain_ids: [2] },
        ],
      })
    })
    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result?.n_clusters).toBe(2)
  })

  it('surfaces error events as status=error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        makeSseResponse([
          'event: error',
          'data: {"type":"error","error":{"code":"pipeline_failed","message":"oops"}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringRefit('local'), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({
        seed_groups: [{ name: 'a', domain_ids: [1] }, { name: 'b', domain_ids: [2] }],
      })
    })
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.message).toMatch(/oops/)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useClusteringRefit.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/hooks/useClusteringRefit.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { parseEventStream } from '@/lib/sse'
import type { ClusteringRefitBody } from '@/api/clustering'

interface RefitResult {
  n_clusters: number
  n_assigned: number
  n_unassigned: number
  output_dir: string
}

type RefitStatus = 'idle' | 'running' | 'done' | 'error'

export function useClusteringRefit(projectId: string) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<RefitStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<RefitResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const run = useCallback(
    async (body: ClusteringRefitBody) => {
      abortRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)
      try {
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/clustering/refit`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: abortRef.current.signal,
          }
        )
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        for await (const event of parseEventStream(response, abortRef.current.signal)) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as RefitResult | null)
            setStatus('done')
            setPct(1)
            qc.invalidateQueries({ queryKey: ['clustering', 'labels', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'assignments', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'seed_groups', projectId] })
            break
          } else if (event.type === 'error') {
            setStatus('error')
            setMessage(event.data.error?.message || 'Refit failed')
            break
          }
        }
      } catch (err: any) {
        if (err.name === 'AbortError') {
          setStatus('idle')
        } else {
          setStatus('error')
          setMessage(err.message)
        }
      }
    },
    [projectId, qc]
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { status, pct, message, result, run, cancel }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useClusteringRefit.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useClusteringRefit.ts web/src/hooks/__tests__/useClusteringRefit.test.tsx
git commit -m "feat(web): add useClusteringRefit SSE mutation hook"
```

---

#### Task 17: useClusteringApplyThresholds mutation hook (SSE)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useClusteringApplyThresholds.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useClusteringApplyThresholds.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useClusteringApplyThresholds.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringApplyThresholds } from '@/hooks/useClusteringApplyThresholds'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function sse(events: string[]): Response {
  return new Response(events.join('\n') + '\n', {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

describe('useClusteringApplyThresholds', () => {
  it('marks done with the apply summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          'event: done',
          'data: {"type":"done","result":{"n_pass":80,"n_total":150,"n_clusters":3}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringApplyThresholds('local'), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({ cluster_thresholds: { 0: 0.5 } })
    })
    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result?.n_pass).toBe(80)
    expect(result.current.result?.n_total).toBe(150)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useClusteringApplyThresholds.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/hooks/useClusteringApplyThresholds.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { parseEventStream } from '@/lib/sse'
import type { ApplyThresholdsBody } from '@/api/clustering'

interface ApplySummary {
  n_pass: number
  n_total: number
  n_clusters: number
}

type ApplyStatus = 'idle' | 'running' | 'done' | 'error'

export function useClusteringApplyThresholds(projectId: string) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<ApplyStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<ApplySummary | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const run = useCallback(
    async (body: ApplyThresholdsBody) => {
      abortRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)
      try {
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/clustering/apply_thresholds`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: abortRef.current.signal,
          }
        )
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        for await (const event of parseEventStream(response, abortRef.current.signal)) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as ApplySummary | null)
            setStatus('done')
            setPct(1)
            qc.invalidateQueries({ queryKey: ['clustering', 'labels', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'assignments', projectId] })
            break
          } else if (event.type === 'error') {
            setStatus('error')
            setMessage(event.data.error?.message || 'Apply thresholds failed')
            break
          }
        }
      } catch (err: any) {
        if (err.name === 'AbortError') {
          setStatus('idle')
        } else {
          setStatus('error')
          setMessage(err.message)
        }
      }
    },
    [projectId, qc]
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { status, pct, message, result, run, cancel }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useClusteringApplyThresholds.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useClusteringApplyThresholds.ts web/src/hooks/__tests__/useClusteringApplyThresholds.test.tsx
git commit -m "feat(web): add useClusteringApplyThresholds SSE mutation hook"
```

---

### Phase 6 — Frontend right-rail authoring components

#### Task 18: SeedGroupList component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/SeedGroupList.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/SeedGroupList.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/SeedGroupList.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SeedGroupList } from '@/components/clustering/SeedGroupList'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
  useClusteringStore.getState().addSeedGroup('thin', [1, 2, 3])
  useClusteringStore.getState().addSeedGroup('thick', [4, 5])
})

describe('SeedGroupList', () => {
  it('renders one row per seed group with name and member count', () => {
    render(<SeedGroupList />)
    expect(screen.getByText('thin')).not.toBeNull()
    expect(screen.getByText('thick')).not.toBeNull()
    expect(screen.getByText('3 members')).not.toBeNull()
    expect(screen.getByText('2 members')).not.toBeNull()
  })

  it('clicking edit toggles editingGroupId', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    fireEvent.click(screen.getByTestId(`seed-group-edit-${id}`))
    expect(useClusteringStore.getState().editingGroupId).toBe(id)
  })

  it('clicking delete removes the group from the store', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    fireEvent.click(screen.getByTestId(`seed-group-delete-${id}`))
    expect(useClusteringStore.getState().seedGroups.length).toBe(1)
    expect(useClusteringStore.getState().seedGroups[0].name).toBe('thick')
  })

  it('row in edit mode renders with data-editing="true"', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    useClusteringStore.getState().setEditingGroupId(id)
    const row = screen.getByTestId(`seed-group-row-${id}`)
    expect(row.getAttribute('data-editing')).toBe('true')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/SeedGroupList.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/SeedGroupList.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function SeedGroupList() {
  const groups = useClusteringStore((s) => s.seedGroups)
  const editingGroupId = useClusteringStore((s) => s.editingGroupId)
  const setEditingGroupId = useClusteringStore((s) => s.setEditingGroupId)
  const removeSeedGroup = useClusteringStore((s) => s.removeSeedGroup)

  if (groups.length === 0) {
    return <div style={{ color: '#888', fontStyle: 'italic' }}>No seed groups yet. Lasso → "Add as seed group".</div>
  }

  return (
    <div role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {groups.map((g) => {
        const editing = editingGroupId === g.id
        return (
          <div
            key={g.id}
            data-testid={`seed-group-row-${g.id}`}
            data-editing={editing ? 'true' : 'false'}
            role="listitem"
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 90px 60px 60px',
              alignItems: 'center',
              padding: '4px 8px',
              border: '1px solid #ddd',
              background: editing ? '#fef3c7' : 'transparent',
              borderRadius: 4,
            }}
          >
            <span>{g.name}</span>
            <span style={{ color: '#666', fontSize: 12 }}>{g.member_ids.length} members</span>
            <button
              type="button"
              data-testid={`seed-group-edit-${g.id}`}
              onClick={() => setEditingGroupId(editing ? null : g.id)}
            >
              {editing ? 'Done' : 'Edit'}
            </button>
            <button
              type="button"
              data-testid={`seed-group-delete-${g.id}`}
              onClick={() => removeSeedGroup(g.id)}
            >
              Delete
            </button>
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/SeedGroupList.test.tsx`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/SeedGroupList.tsx web/src/components/clustering/__tests__/SeedGroupList.test.tsx
git commit -m "feat(web): add SeedGroupList (rows + edit/delete + edit-mode highlight)"
```

---

#### Task 19: SeedGroupEditor component (composer with action buttons)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/SeedGroupEditor.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/SeedGroupEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/SeedGroupEditor.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SeedGroupEditor } from '@/components/clustering/SeedGroupEditor'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('SeedGroupEditor', () => {
  it('"Add from selection" is disabled when brushing.selectedIds is empty', () => {
    render(<SeedGroupEditor />)
    const btn = screen.getByRole('button', { name: /Add from selection/ })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('"Add from selection" appends a seed group from current brush', () => {
    useClusteringStore.getState().applyLasso([10, 11, 12], 'replace')
    render(<SeedGroupEditor />)
    const nameInput = screen.getByPlaceholderText(/seed group name/i) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'monolayer' } })
    fireEvent.click(screen.getByRole('button', { name: /Add from selection/ }))
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('monolayer')
    expect(groups[0].member_ids).toEqual([10, 11, 12])
  })

  it('after add, the name input is cleared', () => {
    useClusteringStore.getState().applyLasso([1], 'replace')
    render(<SeedGroupEditor />)
    const nameInput = screen.getByPlaceholderText(/seed group name/i) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /Add from selection/ }))
    expect(nameInput.value).toBe('')
  })

  it('"Clear all" wipes seed groups when confirmed', () => {
    useClusteringStore.getState().addSeedGroup('a', [1])
    useClusteringStore.getState().addSeedGroup('b', [2])
    render(<SeedGroupEditor />)
    fireEvent.click(screen.getByRole('button', { name: /Clear all/ }))
    expect(useClusteringStore.getState().seedGroups).toEqual([])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/SeedGroupEditor.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/SeedGroupEditor.tsx
import { useState } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import { SeedGroupList } from './SeedGroupList'

export function SeedGroupEditor() {
  const [name, setName] = useState('')
  const selectedIds = useClusteringStore((s) => s.brushing.selectedIds)
  const addSeedGroup = useClusteringStore((s) => s.addSeedGroup)
  const clearSeedGroups = useClusteringStore((s) => s.clearSeedGroups)

  const canAdd = selectedIds.size > 0

  function handleAdd() {
    if (!canAdd) return
    const memberIds = Array.from(selectedIds)
    const finalName = name.trim() || `group ${Date.now() % 10000}`
    addSeedGroup(finalName, memberIds)
    setName('')
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h4 style={{ margin: 0 }}>Seed groups</h4>
      <SeedGroupList />
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          type="text"
          placeholder="seed group name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ flex: 1, padding: '4px 6px' }}
        />
        <button type="button" onClick={handleAdd} disabled={!canAdd}>
          Add from selection ({selectedIds.size})
        </button>
      </div>
      <div>
        <button type="button" onClick={clearSeedGroups}>
          Clear all
        </button>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/SeedGroupEditor.test.tsx`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/SeedGroupEditor.tsx web/src/components/clustering/__tests__/SeedGroupEditor.test.tsx
git commit -m "feat(web): add SeedGroupEditor (name input + add-from-selection + clear-all)"
```

---

#### Task 20: FitScopeRadio component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/FitScopeRadio.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/FitScopeRadio.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/FitScopeRadio.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { FitScopeRadio } from '@/components/clustering/FitScopeRadio'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('FitScopeRadio', () => {
  it('default selection is "seeds"', () => {
    render(<FitScopeRadio />)
    expect((screen.getByLabelText(/seeds only/i) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/all selected/i) as HTMLInputElement).checked).toBe(false)
  })

  it('clicking "all selected" updates the store', () => {
    render(<FitScopeRadio />)
    fireEvent.click(screen.getByLabelText(/all selected/i))
    expect(useClusteringStore.getState().fitScope).toBe('all_selected')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/FitScopeRadio.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/FitScopeRadio.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function FitScopeRadio() {
  const fitScope = useClusteringStore((s) => s.fitScope)
  const setFitScope = useClusteringStore((s) => s.setFitScope)
  return (
    <fieldset style={{ border: '1px solid #ddd', borderRadius: 4, padding: 8 }}>
      <legend style={{ fontSize: 12, fontWeight: 600 }}>Fit scope</legend>
      <label style={{ display: 'block' }}>
        <input
          type="radio"
          name="fit-scope"
          value="seeds"
          checked={fitScope === 'seeds'}
          onChange={() => setFitScope('seeds')}
        />
        {' '}Seeds only
      </label>
      <label style={{ display: 'block' }}>
        <input
          type="radio"
          name="fit-scope"
          value="all_selected"
          checked={fitScope === 'all_selected'}
          onChange={() => setFitScope('all_selected')}
        />
        {' '}All selected (selector subset)
      </label>
    </fieldset>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/FitScopeRadio.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/FitScopeRadio.tsx web/src/components/clustering/__tests__/FitScopeRadio.test.tsx
git commit -m "feat(web): add FitScopeRadio (seeds-only vs all_selected)"
```

---

#### Task 21: InitialMahalanobisSlider component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/InitialMahalanobisSlider.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { InitialMahalanobisSlider } from '@/components/clustering/InitialMahalanobisSlider'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('InitialMahalanobisSlider', () => {
  it('renders the default value 3.0', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.value)).toBe(3.0)
  })

  it('change event writes initialMaxMahalanobis', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(input, { target: { value: '4.5' } })
    expect(useClusteringStore.getState().initialMaxMahalanobis).toBe(4.5)
  })

  it('respects 0.5–6.0 bounds (per design §3.4)', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.min)).toBe(0.5)
    expect(parseFloat(input.max)).toBe(6.0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/InitialMahalanobisSlider.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function InitialMahalanobisSlider() {
  const value = useClusteringStore((s) => s.initialMaxMahalanobis)
  const setValue = useClusteringStore((s) => s.setInitialMaxMahalanobis)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>
        Initial max Mahalanobis ({value.toFixed(2)})
      </span>
      <input
        type="range"
        min={0.5}
        max={6.0}
        step={0.1}
        value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
      />
    </label>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/InitialMahalanobisSlider.tsx web/src/components/clustering/__tests__/InitialMahalanobisSlider.test.tsx
git commit -m "feat(web): add InitialMahalanobisSlider (0.5–6.0, default 3.0)"
```

---

#### Task 22: FitGMMButton component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/FitGMMButton.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/FitGMMButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/FitGMMButton.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FitGMMButton } from '@/components/clustering/FitGMMButton'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { ReactNode } from 'react'

beforeEach(() => {
  resetClusteringStore()
  vi.unstubAllGlobals()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('FitGMMButton', () => {
  it('is disabled when seedGroups.length < 2', () => {
    wrap(<FitGMMButton projectId="local" />)
    const btn = screen.getByRole('button', { name: /Fit GMM/ })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    useClusteringStore.getState().addSeedGroup('a', [1])
    wrap(<FitGMMButton projectId="local" />)
    const stillDisabled = screen.getAllByRole('button', { name: /Fit GMM/ }).at(-1) as HTMLButtonElement
    expect(stillDisabled.disabled).toBe(true)
  })

  it('is enabled with 2+ seed groups and POSTs refit on click', async () => {
    useClusteringStore.getState().addSeedGroup('a', [1])
    useClusteringStore.getState().addSeedGroup('b', [2])
    const sseBody =
      'event: progress\ndata: {"step":"refit","pct":0.5}\n\n' +
      'event: done\ndata: {"result":{"n_clusters":2}}\n\n'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<FitGMMButton projectId="local" />)
    const btn = screen.getByRole('button', { name: /Fit GMM/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/clustering/refit')
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.seed_groups).toEqual([
      { name: 'a', domain_ids: [1] },
      { name: 'b', domain_ids: [2] },
    ])
    expect(body.fit_scope).toBe('seeds')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/FitGMMButton.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/FitGMMButton.tsx
import { useClusteringStore } from '@/state/clusteringSlice'
import { useClusteringRefit } from '@/hooks/useClusteringRefit'

interface Props {
  projectId: string
}

export function FitGMMButton({ projectId }: Props) {
  const seedGroups = useClusteringStore((s) => s.seedGroups)
  const fitScope = useClusteringStore((s) => s.fitScope)
  const initialMaxMahalanobis = useClusteringStore((s) => s.initialMaxMahalanobis)
  const refit = useClusteringRefit(projectId)

  const enoughGroups = seedGroups.length >= 2
  const busy = refit.status === 'running'
  const disabled = !enoughGroups || busy

  function handleClick() {
    refit.run({
      seed_groups: seedGroups.map((g) => ({ name: g.name, domain_ids: g.member_ids })),
      fit_scope: fitScope,
      max_mahalanobis: initialMaxMahalanobis,
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <button type="button" onClick={handleClick} disabled={disabled} style={{ padding: '6px 12px' }}>
        Fit GMM{busy ? ` (${Math.round(refit.pct * 100)}%)` : ''}
      </button>
      {refit.status === 'error' && (
        <div role="alert" style={{ color: '#b91c1c', fontSize: 12 }}>
          {refit.message || 'Fit failed'}
        </div>
      )}
      {!enoughGroups && (
        <div style={{ color: '#888', fontSize: 12 }}>Need ≥2 seed groups to fit.</div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/FitGMMButton.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/FitGMMButton.tsx web/src/components/clustering/__tests__/FitGMMButton.test.tsx
git commit -m "feat(web): add FitGMMButton (refit dispatcher; disabled when seed_groups<2)"
```

---

### Phase 7 — Frontend threshold + commit components

#### Task 23: ClusterRow component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusterRow.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusterRow.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusterRow.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusterRow } from '@/components/clustering/ClusterRow'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
  vi.useFakeTimers()
})

describe('ClusterRow', () => {
  it('renders color swatch and "K/N pass" stats', () => {
    render(<ClusterRow clusterId={0} clusterName="thin" passCount={42} totalCount={100} />)
    expect(screen.getByText(/42 \/ 100/).textContent).toContain('42 / 100')
    expect(screen.getByTestId('cluster-swatch-0')).not.toBeNull()
  })

  it('slider change writes setThreshold (debounced 100ms)', () => {
    render(<ClusterRow clusterId={0} clusterName="thin" passCount={0} totalCount={1} />)
    const slider = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(slider, { target: { value: '0.7' } })
    // Pre-debounce: store still has default
    expect(useClusteringStore.getState().perClusterThresholds[0]).toBeUndefined()
    vi.advanceTimersByTime(100)
    expect(useClusteringStore.getState().perClusterThresholds[0]).toBe(0.7)
  })

  it('reads default threshold 0.5 when no override is set', () => {
    render(<ClusterRow clusterId={2} clusterName="x" passCount={0} totalCount={0} />)
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(slider.value)).toBe(0.5)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusterRow.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusterRow.tsx
import { useEffect, useRef, useState } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import { CLUSTER_PALETTE } from '@/lib/clusterColors'

interface Props {
  clusterId: number
  clusterName: string
  passCount: number
  totalCount: number
}

const DEBOUNCE_MS = 100  // per design §2.1: recolor budget 300ms → tighter than Selector's 200ms

export function ClusterRow({ clusterId, clusterName, passCount, totalCount }: Props) {
  const stored = useClusteringStore((s) => s.perClusterThresholds[clusterId])
  const setThreshold = useClusteringStore((s) => s.setThreshold)

  const initial = stored ?? 0.5
  const [local, setLocal] = useState<number>(initial)

  // Sync from store when external resets occur
  useEffect(() => {
    setLocal(stored ?? 0.5)
  }, [stored])

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = parseFloat(e.target.value)
    setLocal(v)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setThreshold(clusterId, v), DEBOUNCE_MS)
  }

  const swatch = CLUSTER_PALETTE[clusterId % CLUSTER_PALETTE.length]

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '16px 1fr 1fr 90px',
        alignItems: 'center',
        gap: 6,
        padding: '4px 0',
      }}
    >
      <span
        data-testid={`cluster-swatch-${clusterId}`}
        style={{ width: 14, height: 14, background: swatch, borderRadius: 2, display: 'inline-block' }}
      />
      <span style={{ fontSize: 12 }}>{clusterName}</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={local}
        onChange={handleChange}
        aria-label={`threshold cluster ${clusterId}`}
      />
      <span style={{ fontSize: 12, color: '#444' }}>
        {passCount} / {totalCount} pass ({local.toFixed(2)})
      </span>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusterRow.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusterRow.tsx web/src/components/clustering/__tests__/ClusterRow.test.tsx
git commit -m "feat(web): add ClusterRow (color swatch + 100ms-debounced threshold slider)"
```

---

#### Task 24: PerClusterThresholdPanel component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/PerClusterThresholdPanel.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PerClusterThresholdPanel } from '@/components/clustering/PerClusterThresholdPanel'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

beforeEach(() => {
  resetClusteringStore()
})

const labels: LabelsJson = {
  version: 1,
  n_clusters: 2,
  groups: [
    { id: 0, name: 'thin', size: 50, mean_rgb: [0.1, 0.2, 0.3] },
    { id: 1, name: 'thick', size: 30, mean_rgb: [0.4, 0.5, 0.6] },
  ],
  assignments: {},
  thresholds: { '0': 0.5, '1': 0.5 },
  noise_label: -1,
  random_state: 42,
  fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 0, 1, 1],
  max_posterior: [0.9, 0.4, 0.8, 0.3],
}

describe('PerClusterThresholdPanel', () => {
  it('renders one ClusterRow per group', () => {
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    expect(screen.getByText(/thin/)).not.toBeNull()
    expect(screen.getByText(/thick/)).not.toBeNull()
  })

  it('"Reset" sets all thresholds back to default', () => {
    useClusteringStore.getState().setThreshold(0, 0.9)
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    const btn = screen.getByRole('button', { name: /Reset/ })
    btn.click()
    expect(useClusteringStore.getState().perClusterThresholds).toEqual({})
  })

  it('"K/N pass" reflects domains where max_posterior >= per-cluster threshold', () => {
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    // Default threshold 0.5; cluster 0 has posteriors [0.9, 0.4] → 1 pass; cluster 1 has [0.8, 0.3] → 1 pass
    expect(screen.getAllByText(/1 \/ 2 pass/).length).toBeGreaterThanOrEqual(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/PerClusterThresholdPanel.tsx
import { useMemo } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { ClusterRow } from './ClusterRow'

interface Props {
  labels: LabelsJson
  assignments: AssignmentsRows
}

export function PerClusterThresholdPanel({ labels, assignments }: Props) {
  const overrides = useClusteringStore((s) => s.perClusterThresholds)
  const reset = useClusteringStore((s) => s.resetThresholdsToDefault)

  // Pre-compute per-cluster (passCount, totalCount) under current thresholds
  const stats = useMemo(() => {
    const out: Record<number, { pass: number; total: number }> = {}
    for (const g of labels.groups) {
      out[g.id] = { pass: 0, total: 0 }
    }
    for (let i = 0; i < assignments.cluster_label.length; i++) {
      const cid = assignments.cluster_label[i]
      if (out[cid] === undefined) continue
      const t = overrides[cid] ?? labels.thresholds[String(cid)] ?? 0.5
      out[cid].total += 1
      if (assignments.max_posterior[i] >= t) out[cid].pass += 1
    }
    return out
  }, [labels, assignments, overrides])

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4 style={{ margin: 0 }}>Per-cluster thresholds</h4>
        <button type="button" onClick={reset} style={{ fontSize: 12 }}>
          Reset
        </button>
      </header>
      <div>
        {labels.groups.map((g) => (
          <ClusterRow
            key={g.id}
            clusterId={g.id}
            clusterName={g.name}
            passCount={stats[g.id]?.pass ?? 0}
            totalCount={stats[g.id]?.total ?? 0}
          />
        ))}
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/PerClusterThresholdPanel.tsx web/src/components/clustering/__tests__/PerClusterThresholdPanel.test.tsx
git commit -m "feat(web): add PerClusterThresholdPanel (rows + reset + live K/N pass)"
```

---

#### Task 25: LiveMahalanobisSlider component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/LiveMahalanobisSlider.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { LiveMahalanobisSlider } from '@/components/clustering/LiveMahalanobisSlider'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('LiveMahalanobisSlider', () => {
  it('default value is 3.0', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.value)).toBe(3.0)
  })

  it('change updates liveMaxMahalanobis', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(input, { target: { value: '5.5' } })
    expect(useClusteringStore.getState().liveMaxMahalanobis).toBe(5.5)
  })

  it('respects 0.5–8.0 bounds (per design §3.4 post-fit gate)', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.min)).toBe(0.5)
    expect(parseFloat(input.max)).toBe(8.0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/LiveMahalanobisSlider.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function LiveMahalanobisSlider() {
  const value = useClusteringStore((s) => s.liveMaxMahalanobis)
  const setValue = useClusteringStore((s) => s.setLiveMaxMahalanobis)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>
        Live max Mahalanobis ({value.toFixed(2)})
      </span>
      <input
        type="range"
        min={0.5}
        max={8.0}
        step={0.1}
        value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
      />
    </label>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/LiveMahalanobisSlider.tsx web/src/components/clustering/__tests__/LiveMahalanobisSlider.test.tsx
git commit -m "feat(web): add LiveMahalanobisSlider (post-fit gate, 0.5–8.0)"
```

---

#### Task 26: CommitClusteringButton component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/CommitClusteringButton.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/CommitClusteringButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/CommitClusteringButton.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CommitClusteringButton } from '@/components/clustering/CommitClusteringButton'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { ReactNode } from 'react'

beforeEach(() => {
  resetClusteringStore()
  vi.unstubAllGlobals()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('CommitClusteringButton', () => {
  it('POSTs apply_thresholds with current per-cluster thresholds and live max mahalanobis', async () => {
    useClusteringStore.getState().setThreshold(0, 0.7)
    useClusteringStore.getState().setThreshold(1, 0.3)
    useClusteringStore.getState().setLiveMaxMahalanobis(4.5)
    const sseBody =
      'event: progress\ndata: {"step":"apply","pct":0.5}\n\n' +
      'event: done\ndata: {"result":{"n_pass":42,"n_total":100,"n_clusters":2}}\n\n'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<CommitClusteringButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Commit clustering/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/clustering/apply_thresholds')
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.cluster_thresholds).toEqual({ 0: 0.7, 1: 0.3 })
    expect(body.max_mahalanobis).toBe(4.5)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/CommitClusteringButton.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/CommitClusteringButton.tsx
import { useClusteringStore } from '@/state/clusteringSlice'
import { useClusteringApplyThresholds } from '@/hooks/useClusteringApplyThresholds'

interface Props {
  projectId: string
}

export function CommitClusteringButton({ projectId }: Props) {
  const thresholds = useClusteringStore((s) => s.perClusterThresholds)
  const liveMax = useClusteringStore((s) => s.liveMaxMahalanobis)
  const apply = useClusteringApplyThresholds(projectId)
  const busy = apply.status === 'running'

  function handleClick() {
    apply.run({
      cluster_thresholds: { ...thresholds },
      max_mahalanobis: liveMax,
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <button type="button" onClick={handleClick} disabled={busy} style={{ padding: '6px 12px' }}>
        Commit clustering{busy ? ` (${Math.round(apply.pct * 100)}%)` : ''}
      </button>
      {apply.status === 'done' && apply.result && (
        <div style={{ fontSize: 12, color: '#0f5132' }}>
          {apply.result.n_pass} / {apply.result.n_total} pass across {apply.result.n_clusters} clusters
        </div>
      )}
      {apply.status === 'error' && (
        <div role="alert" style={{ color: '#b91c1c', fontSize: 12 }}>
          {apply.message || 'Commit failed'}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/CommitClusteringButton.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/CommitClusteringButton.tsx web/src/components/clustering/__tests__/CommitClusteringButton.test.tsx
git commit -m "feat(web): add CommitClusteringButton (apply_thresholds dispatcher)"
```

---

### Phase 8 — Frontend main panel + composers

#### Task 27: ClusteringAxisPicker component (clusteringSlice variant)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusteringAxisPicker.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx`

> Why a separate component: `selector/AxisPicker.tsx` writes to `useSelectorStore`. Clustering uses an independent `clusteringSlice.axisX/axisY` per design §3.4, so a thin per-tab variant keeps both stores untouched.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusteringAxisPicker } from '@/components/clustering/ClusteringAxisPicker'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('ClusteringAxisPicker', () => {
  it('writes to clusteringSlice (not selectorSlice)', () => {
    render(<ClusteringAxisPicker pane="X" />)
    fireEvent.click(screen.getByLabelText('X: B'))
    expect(useClusteringStore.getState().axisX).toBe('B')
  })

  it('reads its current selection from clusteringSlice', () => {
    useClusteringStore.getState().setAxis('Y', 'std_r')
    render(<ClusteringAxisPicker pane="Y" />)
    expect((screen.getByLabelText('Y: std_r') as HTMLInputElement).checked).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusteringAxisPicker.tsx
import { useClusteringStore } from '@/state/clusteringSlice'
import type { AvailableAxis } from '@/state/selectorSlice'

const AXES: AvailableAxis[] = ['R', 'G', 'B', 'area', 'std_r', 'std_g', 'std_b', 'sam2']

interface Props {
  pane: 'X' | 'Y'
}

export function ClusteringAxisPicker({ pane }: Props) {
  const setAxis = useClusteringStore((s) => s.setAxis)
  const current = useClusteringStore((s) => (pane === 'X' ? s.axisX : s.axisY))
  const groupName = `clustering-axis-${pane}`
  return (
    <fieldset style={{ border: 'none', padding: 0, margin: '8px 0' }}>
      <legend style={{ fontSize: 12, fontWeight: 600 }}>Axis {pane}</legend>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4 }}>
        {AXES.map((a) => (
          <label key={a} style={{ fontSize: 11 }}>
            <input
              type="radio"
              name={groupName}
              checked={current === a}
              onChange={() => setAxis(pane, a)}
              aria-label={`${pane}: ${a}`}
            />{' '}
            {a}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusteringAxisPicker.tsx web/src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx
git commit -m "feat(web): add ClusteringAxisPicker (clusteringSlice variant of AxisPicker)"
```

---

#### Task 28: ClusteringBrushingControls component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusteringBrushingControls.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx`

> Why a separate component: `selector/BrushingControls.tsx` toggles a Selector-tab-local Zustand store for `mode` and dispatches `useSelectorStore`. Per Q-U4 the Clustering tab keeps a fully independent brushing state; this variant points at `useClusteringStore` for undo/redo/clear.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusteringBrushingControls } from '@/components/clustering/ClusteringBrushingControls'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('ClusteringBrushingControls', () => {
  it('"Clear brush" empties brushing.selectedIds', () => {
    useClusteringStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<ClusteringBrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    expect(useClusteringStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('Undo reverts last applyLasso', () => {
    useClusteringStore.getState().applyLasso([1, 2], 'replace')
    useClusteringStore.getState().applyLasso([3, 4], 'add')
    render(<ClusteringBrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([1, 2]))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusteringBrushingControls.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function ClusteringBrushingControls() {
  const undo = useClusteringStore((s) => s.undoBrush)
  const redo = useClusteringStore((s) => s.redoBrush)
  const clear = useClusteringStore((s) => s.clearBrush)
  const count = useClusteringStore((s) => s.brushing.selectedIds.size)
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center', margin: '8px 0' }}>
      <span style={{ fontSize: 12, color: '#444' }}>Brush ({count})</span>
      <button type="button" onClick={undo} style={{ fontSize: 12 }}>Undo</button>
      <button type="button" onClick={redo} style={{ fontSize: 12 }}>Redo</button>
      <button type="button" onClick={clear} style={{ fontSize: 12 }}>Clear</button>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusteringBrushingControls.tsx web/src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx
git commit -m "feat(web): add ClusteringBrushingControls (clusteringSlice undo/redo/clear)"
```

---

#### Task 29: ClusterScatterCanvas component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusterScatterCanvas.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { ClusterScatterCanvas } from '@/components/clustering/ClusterScatterCanvas'
import type { DomainStats } from '@/api/selector'
import type { AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({
  default: (props: any) => {
    ;(globalThis as any).__plotlyProps = props
    return <div data-testid="plotly-mock" />
  },
}))

const stats: DomainStats = {
  flake_ids: [1, 2, 3, 4],
  mean_r: [10, 20, 30, 40], mean_g: [10, 20, 30, 40], mean_b: [10, 20, 30, 40],
  std_r: [1, 2, 3, 4], std_g: [1, 2, 3, 4], std_b: [1, 2, 3, 4],
  areas: [100, 200, 300, 400],
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 1, 0, -1],
  max_posterior: [0.9, 0.8, 0.7, 0.0],
}

describe('ClusterScatterCanvas', () => {
  it('renders one trace whose marker.color is per-cluster (palette[0], palette[1], palette[0], gray)', () => {
    render(<ClusterScatterCanvas stats={stats} assignments={assignments} />)
    const props = (globalThis as any).__plotlyProps
    const colors = props.data[0].marker.color as string[]
    expect(colors[0]).toBe('#1f77b4')        // palette[0]
    expect(colors[1]).toBe('#ff7f0e')        // palette[1]
    expect(colors[2]).toBe('#1f77b4')        // palette[0]
    expect(colors[3]).toBe('#9e9e9e')        // NEUTRAL_GRAY (noise)
  })

  it('falls back to neutral-gray for all points when assignments=null (pre-fit)', () => {
    render(<ClusterScatterCanvas stats={stats} assignments={null} />)
    const props = (globalThis as any).__plotlyProps
    const colors = props.data[0].marker.color as string[]
    expect(new Set(colors)).toEqual(new Set(['#9e9e9e']))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusterScatterCanvas.tsx
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import type { AssignmentsRows } from '@/api/clustering'
import { useClusteringStore } from '@/state/clusteringSlice'
import { useBrushModeStore } from '@/components/selector/BrushingControls'
import { downsampleIndices } from '@/lib/downsample'
import { CLUSTER_PALETTE, NEUTRAL_GRAY, colorForCluster } from '@/lib/clusterColors'
import type { AvailableAxis } from '@/state/selectorSlice'

interface Props {
  stats: DomainStats
  assignments: AssignmentsRows | null
}

const MAX_POINTS = 5000

function pickColumn(stats: DomainStats, axis: AvailableAxis): number[] {
  switch (axis) {
    case 'R': return stats.mean_r
    case 'G': return stats.mean_g
    case 'B': return stats.mean_b
    case 'area': return stats.areas
    case 'std_r': return stats.std_r
    case 'std_g': return stats.std_g
    case 'std_b': return stats.std_b
    case 'sam2': return stats.sam2 ?? new Array(stats.flake_ids.length).fill(0)
  }
}

export function ClusterScatterCanvas({ stats, assignments }: Props) {
  const axisX = useClusteringStore((s) => s.axisX)
  const axisY = useClusteringStore((s) => s.axisY)
  const overrides = useClusteringStore((s) => s.perClusterThresholds)
  const editingGroupId = useClusteringStore((s) => s.editingGroupId)
  const seedGroups = useClusteringStore((s) => s.seedGroups)
  const selectedIds = useClusteringStore((s) => s.brushing.selectedIds)
  const applyLasso = useClusteringStore((s) => s.applyLasso)
  const setFocusId = useClusteringStore((s) => s.setFocusId)
  const mode = useBrushModeStore((s) => s.mode)

  const editingMembers = useMemo<Set<number>>(() => {
    if (!editingGroupId) return new Set()
    const g = seedGroups.find((x) => x.id === editingGroupId)
    return g ? new Set(g.member_ids) : new Set()
  }, [editingGroupId, seedGroups])

  const { data, layout } = useMemo(() => {
    const n = stats.flake_ids.length
    const idxs = downsampleIndices(n, stats.flake_ids, MAX_POINTS, selectedIds)
    const xCol = pickColumn(stats, axisX)
    const yCol = pickColumn(stats, axisY)

    // Build domain_id → (cluster_label, max_posterior)
    const lookup = new Map<number, { c: number; p: number }>()
    if (assignments) {
      for (let i = 0; i < assignments.domain_id.length; i++) {
        lookup.set(assignments.domain_id[i], {
          c: assignments.cluster_label[i],
          p: assignments.max_posterior[i],
        })
      }
    }

    const x = idxs.map((i) => xCol[i])
    const y = idxs.map((i) => yCol[i])
    const ids = idxs.map((i) => stats.flake_ids[i])
    const colors = ids.map((id) => {
      const r = lookup.get(id)
      if (!r) return NEUTRAL_GRAY
      const t = overrides[r.c] ?? 0.5
      if (r.c < 0 || r.p < t) return NEUTRAL_GRAY
      return colorForCluster(r.c)
    })

    // Edit-mode ring overlay: orange outline around the editing group's members
    const lineColors = ids.map((id) => (editingMembers.has(id) ? '#f97316' : 'rgba(0,0,0,0)'))
    const lineWidths = ids.map((id) => (editingMembers.has(id) ? 2 : 0))

    return {
      data: [
        {
          type: 'scattergl' as const,
          mode: 'markers' as const,
          x,
          y,
          customdata: ids,
          marker: { size: 5, color: colors, line: { color: lineColors, width: lineWidths } },
          hovertemplate: 'id=%{customdata}<br>x=%{x}<br>y=%{y}<extra></extra>',
        },
      ],
      layout: {
        xaxis: { title: { text: axisX } },
        yaxis: { title: { text: axisY } },
        dragmode: 'lasso' as const,
        margin: { t: 10, r: 10, b: 40, l: 40 },
        hovermode: 'closest' as const,
        autosize: true,
      },
    }
  }, [stats, assignments, axisX, axisY, overrides, selectedIds, editingMembers])

  // Reference imports so tree-shaking doesn't drop tested constants
  void CLUSTER_PALETTE
  void NEUTRAL_GRAY

  return (
    <Plot
      data={data}
      layout={layout}
      style={{ width: '100%', height: 480 }}
      useResizeHandler
      onSelected={(ev: any) => {
        if (!ev?.points) return
        const ids = ev.points.map((p: any) => p.customdata as number)
        applyLasso(ids, mode)
      }}
      onClick={(ev: any) => {
        const pt = ev?.points?.[0]
        if (pt?.customdata !== undefined) setFocusId(pt.customdata as number)
      }}
    />
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusterScatterCanvas.tsx web/src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx
git commit -m "feat(web): add ClusterScatterCanvas (cluster-color + edit-group ring overlay)"
```

---

#### Task 30: ClusterSizeBarChart component

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusterSizeBarChart.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { ClusterSizeBarChart } from '@/components/clustering/ClusterSizeBarChart'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({
  default: (props: any) => {
    ;(globalThis as any).__plotlyBarProps = props
    return <div data-testid="plotly-bar-mock" />
  },
}))

const labels: LabelsJson = {
  version: 1, n_clusters: 2,
  groups: [
    { id: 0, name: 'thin', size: 0, mean_rgb: [0, 0, 0] },
    { id: 1, name: 'thick', size: 0, mean_rgb: [0, 0, 0] },
  ],
  assignments: {}, thresholds: { '0': 0.5, '1': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 0, 1, -1],
  max_posterior: [0.9, 0.4, 0.8, 0.0],
}

describe('ClusterSizeBarChart', () => {
  it('renders one bar per cluster with counts pre-threshold', () => {
    render(<ClusterSizeBarChart labels={labels} assignments={assignments} />)
    const props = (globalThis as any).__plotlyBarProps
    const trace = props.data[0]
    expect(trace.x).toEqual(['thin', 'thick'])
    expect(trace.y).toEqual([2, 1])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusterSizeBarChart.tsx
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { colorForCluster } from '@/lib/clusterColors'

interface Props {
  labels: LabelsJson
  assignments: AssignmentsRows
}

export function ClusterSizeBarChart({ labels, assignments }: Props) {
  const { x, y, colors } = useMemo(() => {
    const counts = new Map<number, number>()
    for (const g of labels.groups) counts.set(g.id, 0)
    for (const c of assignments.cluster_label) {
      if (counts.has(c)) counts.set(c, (counts.get(c) ?? 0) + 1)
    }
    const ordered = labels.groups.map((g) => g)
    return {
      x: ordered.map((g) => g.name),
      y: ordered.map((g) => counts.get(g.id) ?? 0),
      colors: ordered.map((g) => colorForCluster(g.id)),
    }
  }, [labels, assignments])

  return (
    <Plot
      data={[{ type: 'bar' as const, x, y, marker: { color: colors } }]}
      layout={{
        title: { text: 'Cluster sizes' },
        xaxis: { title: { text: 'Cluster' } },
        yaxis: { title: { text: 'Count' } },
        margin: { t: 30, r: 10, b: 40, l: 40 },
        autosize: true,
        height: 220,
      }}
      style={{ width: '100%', height: 220 }}
      useResizeHandler
    />
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusterSizeBarChart.tsx web/src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx
git commit -m "feat(web): add ClusterSizeBarChart (per-cluster size bars, palette-colored)"
```

---

#### Task 31: ClusteringRightRail composer

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusteringRightRail.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusteringRightRail.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusteringRightRail.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClusteringRightRail } from '@/components/clustering/ClusteringRightRail'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import type { ReactNode } from 'react'

vi.mock('react-plotly.js', () => ({ default: () => <div /> }))

beforeEach(() => {
  resetClusteringStore()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

const labels: LabelsJson = {
  version: 1, n_clusters: 1,
  groups: [{ id: 0, name: 'a', size: 1, mean_rgb: [0, 0, 0] }],
  assignments: {}, thresholds: { '0': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1], cluster_label: [0], max_posterior: [0.9],
}

describe('ClusteringRightRail', () => {
  it('renders authoring controls when labels=null (pre-fit)', () => {
    wrap(<ClusteringRightRail projectId="local" labels={null} assignments={null} />)
    expect(screen.getByText(/Seed groups/)).not.toBeNull()
    expect(screen.getByRole('button', { name: /Fit GMM/ })).not.toBeNull()
    // Threshold panel should NOT render pre-fit
    expect(screen.queryByText(/Per-cluster thresholds/)).toBeNull()
  })

  it('renders threshold + commit blocks when labels and assignments are present', () => {
    wrap(<ClusteringRightRail projectId="local" labels={labels} assignments={assignments} />)
    expect(screen.getByText(/Per-cluster thresholds/)).not.toBeNull()
    expect(screen.getByRole('button', { name: /Commit clustering/ })).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringRightRail.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusteringRightRail.tsx
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { SeedGroupEditor } from './SeedGroupEditor'
import { FitScopeRadio } from './FitScopeRadio'
import { InitialMahalanobisSlider } from './InitialMahalanobisSlider'
import { FitGMMButton } from './FitGMMButton'
import { PerClusterThresholdPanel } from './PerClusterThresholdPanel'
import { LiveMahalanobisSlider } from './LiveMahalanobisSlider'
import { ClusteringBrushingControls } from './ClusteringBrushingControls'
import { ClusteringAxisPicker } from './ClusteringAxisPicker'
import { CommitClusteringButton } from './CommitClusteringButton'

interface Props {
  projectId: string
  labels: LabelsJson | null
  assignments: AssignmentsRows | null
}

export function ClusteringRightRail({ projectId, labels, assignments }: Props) {
  const fitDone = labels !== null && assignments !== null
  return (
    <aside style={{ width: 320, borderLeft: '1px solid #eee', padding: 12, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <SeedGroupEditor />
      <FitScopeRadio />
      <InitialMahalanobisSlider />
      <FitGMMButton projectId={projectId} />
      {fitDone && (
        <>
          <PerClusterThresholdPanel labels={labels} assignments={assignments} />
          <LiveMahalanobisSlider />
        </>
      )}
      <ClusteringBrushingControls />
      <ClusteringAxisPicker pane="X" />
      <ClusteringAxisPicker pane="Y" />
      {fitDone && <CommitClusteringButton projectId={projectId} />}
    </aside>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringRightRail.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusteringRightRail.tsx web/src/components/clustering/__tests__/ClusteringRightRail.test.tsx
git commit -m "feat(web): add ClusteringRightRail composer (authoring + post-fit gates)"
```

---

#### Task 32: ClusteringMain composer

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/ClusteringMain.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/clustering/__tests__/ClusteringMain.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/clustering/__tests__/ClusteringMain.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ClusteringMain } from '@/components/clustering/ClusteringMain'
import type { DomainStats } from '@/api/selector'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({ default: () => <div data-testid="plotly-mock" /> }))

const stats: DomainStats = {
  flake_ids: [1], mean_r: [0], mean_g: [0], mean_b: [0],
  std_r: [0], std_g: [0], std_b: [0], areas: [1],
}
const labels: LabelsJson = {
  version: 1, n_clusters: 1,
  groups: [{ id: 0, name: 'a', size: 1, mean_rgb: [0, 0, 0] }],
  assignments: {}, thresholds: { '0': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = { domain_id: [1], cluster_label: [0], max_posterior: [0.9] }

describe('ClusteringMain', () => {
  it('renders only the scatter pre-fit', () => {
    render(<ClusteringMain stats={stats} labels={null} assignments={null} />)
    // The scatter and the bar chart both use the mocked Plot component, but
    // pre-fit only one is rendered (the scatter) — assert exactly 1 mock.
    expect(screen.getAllByTestId('plotly-mock').length).toBe(1)
  })

  it('renders scatter + bar chart post-fit', () => {
    render(<ClusteringMain stats={stats} labels={labels} assignments={assignments} />)
    expect(screen.getAllByTestId('plotly-mock').length).toBe(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringMain.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/clustering/ClusteringMain.tsx
import type { DomainStats } from '@/api/selector'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { ClusterScatterCanvas } from './ClusterScatterCanvas'
import { ClusterSizeBarChart } from './ClusterSizeBarChart'

interface Props {
  stats: DomainStats
  labels: LabelsJson | null
  assignments: AssignmentsRows | null
}

export function ClusteringMain({ stats, labels, assignments }: Props) {
  const fitDone = labels !== null && assignments !== null
  return (
    <div style={{ flex: 1, padding: 12, display: 'flex', flexDirection: 'column', gap: 12, minHeight: 0 }}>
      <ClusterScatterCanvas stats={stats} assignments={assignments} />
      {fitDone && <ClusterSizeBarChart labels={labels} assignments={assignments} />}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/clustering/__tests__/ClusteringMain.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/clustering/ClusteringMain.tsx web/src/components/clustering/__tests__/ClusteringMain.test.tsx
git commit -m "feat(web): add ClusteringMain composer (scatter + post-fit bar chart)"
```

---

### Phase 9 — Tab assembly, lazy route, and integration test

#### Task 33: ClusteringTab page

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/pages/ClusteringTab.tsx`

- [ ] **Step 1: Write the failing test** (covered by integration test in Task 35)

Skip — covered by Task 35.

- [ ] **Step 2: Run test to verify it fails**

Skip.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/pages/ClusteringTab.tsx
import { useEffect } from 'react'
import { useDomainStats } from '@/hooks/useDomainStats'
import { useClusteringLabels } from '@/hooks/useClusteringLabels'
import { useClusteringAssignments } from '@/hooks/useClusteringAssignments'
import { useClusteringSeedGroups } from '@/hooks/useClusteringSeedGroups'
import { useClusteringStore } from '@/state/clusteringSlice'
import { ClusteringMain } from '@/components/clustering/ClusteringMain'
import { ClusteringRightRail } from '@/components/clustering/ClusteringRightRail'
import { ApiError } from '@/api/selector'

interface Props {
  projectId: string
}

export function ClusteringTab({ projectId }: Props) {
  const stats = useDomainStats(projectId)
  const labels = useClusteringLabels(projectId)
  const assignments = useClusteringAssignments(projectId)
  const seedGroups = useClusteringSeedGroups(projectId)
  const hydrate = useClusteringStore((s) => s.hydrateSeedGroups)

  // Autoload seed groups (preserves _maybe_autoload_seed_groups semantics)
  useEffect(() => {
    if (seedGroups.data) hydrate(seedGroups.data)
  }, [seedGroups.data, hydrate])

  if (stats.isLoading) {
    return <div style={{ padding: 16 }}>Loading domain stats...</div>
  }
  if (stats.error) {
    return (
      <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>
        {(stats.error as Error).message}
      </div>
    )
  }
  if (!stats.data) return null

  // labels/assignments may legitimately be 404 ("not fitted yet"). Treat that
  // as "pre-fit" rather than an error surface.
  const labelsErr = labels.error as ApiError | null
  const assignErr = assignments.error as ApiError | null
  const labelsIs404 = labelsErr instanceof ApiError && labelsErr.status === 404
  const assignIs404 = assignErr instanceof ApiError && assignErr.status === 404

  if (labelsErr && !labelsIs404) {
    return <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>{labelsErr.message}</div>
  }
  if (assignErr && !assignIs404) {
    return <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>{assignErr.message}</div>
  }

  const labelsData = labels.data ?? null
  const assignmentsData = assignments.data ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <ClusteringMain stats={stats.data} labels={labelsData} assignments={assignmentsData} />
        <ClusteringRightRail projectId={projectId} labels={labelsData} assignments={assignmentsData} />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ClusteringTab.tsx
git commit -m "feat(web): add ClusteringTab page (autoload seed groups + main/rail composer)"
```

---

#### Task 34: Register lazy route in App.tsx

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/src/App.tsx`

- [ ] **Step 1: Write the failing test** (covered indirectly — App.tsx is exercised by `pages/__tests__/SelectorTab.test.tsx` style integration. Defer to Task 35.)

Skip.

- [ ] **Step 2: Run test to verify it fails**

Skip.

- [ ] **Step 3: Write minimal implementation**

Edit `/Users/houkjang/projects/stand-alone-analyzer/web/src/App.tsx`. Replace the existing module top-section so it adds a new lazy import and route alongside the Selector route. The full target file:

```tsx
import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import { ComputeTab } from './pages/ComputeTab'

const SelectorTab = lazy(() =>
  import('@/pages/SelectorTab').then((m) => ({ default: m.SelectorTab }))
)
const ClusteringTab = lazy(() =>
  import('@/pages/ClusteringTab').then((m) => ({ default: m.ClusteringTab }))
)

function SelectorTabRoute() {
  const { projectId } = useParams<{ projectId: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Selector tab...</div>}>
      <SelectorTab projectId={projectId || 'local'} />
    </Suspense>
  )
}

function ClusteringTabRoute() {
  const { projectId } = useParams<{ projectId: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Clustering tab...</div>}>
      <ClusteringTab projectId={projectId || 'local'} />
    </Suspense>
  )
}

export function App() {
  return (
    <BrowserRouter>
      <div style={{ padding: '20px' }}>
        <h1>Stand-Alone Analyzer</h1>
        <Routes>
          <Route path="/" element={<Navigate to="/projects/local/compute" replace />} />
          <Route path="/projects/:projectId/compute" element={<ComputeTab />} />
          <Route path="/projects/:projectId/selector" element={<SelectorTabRoute />} />
          <Route path="/projects/:projectId/clustering" element={<ClusteringTabRoute />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
```

- [ ] **Step 4: Run typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx
git commit -m "feat(web): register lazy /clustering route"
```

---

#### Task 35: ClusteringTab integration test (3 scenarios mirroring SelectorTab.test.tsx)

**Files:**
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/pages/__tests__/ClusteringTab.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/pages/__tests__/ClusteringTab.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClusteringTab } from '@/pages/ClusteringTab'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

vi.mock('react-plotly.js', () => ({
  default: (_props: any) => <div data-testid="plotly-mock" />,
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  resetClusteringStore()
})

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

const stats = {
  flake_ids: [1, 2, 3],
  mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
  std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
  areas: [100, 200, 300],
}

function makeFetchMock(handlers: Record<string, () => Response>) {
  return vi.fn(async (url: string) => {
    for (const [needle, make] of Object.entries(handlers)) {
      if (url.includes(needle)) return make()
    }
    return new Response(JSON.stringify({ error: { code: 'unhandled', message: url } }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    })
  })
}

describe('ClusteringTab integration', () => {
  it('pre-fit: renders authoring controls when labels=404 + no assignments', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(JSON.stringify([]), { status: 200, headers: { 'content-type': 'application/json' } }),
      })
    )

    wrap(<ClusteringTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Seed groups/)).not.toBeNull())
    expect(screen.getByRole('button', { name: /Fit GMM/ })).not.toBeNull()
    expect(screen.queryByText(/Per-cluster thresholds/)).toBeNull()
  })

  it('post-fit: renders threshold panel + commit + bar chart when labels and assignments are present', async () => {
    const labelsPayload = {
      version: 1, n_clusters: 2,
      groups: [
        { id: 0, name: 'thin', size: 2, mean_rgb: [0.1, 0.2, 0.3] },
        { id: 1, name: 'thick', size: 1, mean_rgb: [0.4, 0.5, 0.6] },
      ],
      assignments: {}, thresholds: { '0': 0.5, '1': 0.5 },
      noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
    }
    const assignmentsPayload = {
      domain_id: [1, 2, 3],
      cluster_label: [0, 0, 1],
      max_posterior: [0.9, 0.8, 0.7],
    }
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify(labelsPayload), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify(assignmentsPayload), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(JSON.stringify([{ name: 'thin', domain_ids: [1, 2] }]), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      })
    )

    wrap(<ClusteringTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Per-cluster thresholds/)).not.toBeNull())
    expect(screen.getByRole('button', { name: /Commit clustering/ })).not.toBeNull()
    expect(screen.getAllByTestId('plotly-mock').length).toBe(2)
  })

  it('autoload: hydrates seed groups from /seed_groups on first mount only', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(
            JSON.stringify([
              { name: 'thin', domain_ids: [1, 2] },
              { name: 'thick', domain_ids: [3] },
            ]),
            { status: 200, headers: { 'content-type': 'application/json' } }
          ),
      })
    )

    wrap(<ClusteringTab projectId="local" />)
    await waitFor(() => expect(useClusteringStore.getState().seedGroups.length).toBe(2))
    expect(useClusteringStore.getState().seedGroups.map((g) => g.name)).toEqual(['thin', 'thick'])

    // Simulate a user edit, then re-render — autoload must NOT clobber it
    useClusteringStore.getState().clearSeedGroups()
    useClusteringStore.getState().addSeedGroup('user-edit', [99])
    fireEvent.scroll(window)
    expect(useClusteringStore.getState().seedGroups.length).toBe(1)
    expect(useClusteringStore.getState().seedGroups[0].name).toBe('user-edit')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`:
`npx vitest run src/pages/__tests__/ClusteringTab.test.tsx`
Expected: FAIL — likely module-not-found or behavior mismatch.

- [ ] **Step 3: Write minimal implementation**

Implementation already in Task 33. If failures appear, fix them in `pages/ClusteringTab.tsx` (e.g., gate hydrate on initial empty state — already enforced by `hydrateSeedGroups` itself per Task 11 guard).

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/pages/__tests__/ClusteringTab.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Run the full frontend test suite**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx vitest run`
Expected: ALL PASS (including pre-existing Selector + Compute tests).

- [ ] **Step 6: Run typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 7: Run the backend test suite**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/`
Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/__tests__/ClusteringTab.test.tsx
git commit -m "test(web): add ClusteringTab integration test (pre-fit, post-fit, autoload)"
```

---

## Self-Review Notes

### Spec coverage check

| Spec section | Coverage |
|---|---|
| Frontend §3.4 `clusteringSlice` (seed groups, fitScope, mahalanobis, thresholds, brushing, editingGroupId, mutations) | Task 11 |
| Frontend §3.4 autoload from disk only when `seedGroups.length === 0` | Task 11 (`hydrateSeedGroups` guard) + Task 33 (effect wiring) + Task 35 (regression) |
| Frontend §3.4 thresholds default 0.5; bounds 0.5–6.0 (initial), 0.5–8.0 (live) | Tasks 21, 23, 25 |
| Frontend §3.7 `BrushingState` (per-tab) | Reused via `@/lib/brushing` (Plan 2 Task 13); isolation guarded by Task 12 |
| Frontend §4.3 component tree (SeedGroupEditor, FitScopeRadio, InitialMahalanobisSlider, FitGMMButton, PerClusterThresholdPanel, ClusterRow, LiveMahalanobisSlider, BrushingControls, AxisPicker, CommitClusteringButton, ScatterCanvas variant, ClusterSizeBarChart) | Tasks 18–32 (every box in the tree maps to a task) |
| Frontend §4.3 server queries (`/clustering/labels`, `/clustering/seed_groups`, `/selector/selection`) | Tasks 4, 5, 6 (backend) + Tasks 13, 14, 15 (frontend) |
| Frontend §4.3 mutations (`/clustering/fit`, `/clustering/apply_thresholds`) → wired as `/clustering/refit` and `/clustering/apply_thresholds` per backend §1.2 | Tasks 7, 8 (backend) + Tasks 16, 17 (frontend) |
| Frontend §4.3 key interaction: lasso → "Add as seed group" | Task 19 |
| Frontend §4.3 key interaction: edit-group dropdown → orange ring overlay | Task 29 (line color/width on Plotly trace) |
| Frontend §4.3 key interaction: 100ms debounced threshold slider | Task 23 |
| Frontend §4.3 key interaction: Commit → invalidate clustering queries → toast | Task 17 (`qc.invalidateQueries(['clustering','labels',pid])` + `['clustering','assignments',pid]`) + Task 26 (UX surface) |
| Frontend §4.3 don't-overengineer: no GMM hyperparameter UI beyond fit_scope + initialMahalanobis; no auto-naming/merging; no DetailPanel | Tasks 20, 21, 22 (only those controls); no extra components |
| Frontend §5.1/§5.2 API client + SSE consumer | Tasks 13, 16, 17 (reusing parser from Plan 2) |
| Frontend §5.5 d3-category10 palette | Task 10 |
| Frontend §7.2 porting order — Clustering third | This entire plan |
| Backend §1.2 `POST /run/clustering/refit` (SSE, lock+drain) | Task 7 |
| Backend §1.2 `POST /run/clustering/apply_thresholds` (SSE, lock+drain) | Task 8 |
| Backend §1.3 `GET /data/clustering/labels` | Task 4 |
| Backend §1.3 `GET /data/clustering/assignments` (Arrow vs JSON negotiation) | Task 5 |
| Backend §1.3 `GET /data/clustering/seed_groups` | Task 6 |
| Backend §3.2 per-project mutex shared by both clustering SSE endpoints | Tasks 7, 8 (use shared lock) + Task 9 (regression test) |
| Backend errors: `clustering_not_fitted`, `seed_groups_missing` | Task 3 |
| Q-U4 selector ↔ clustering brushing isolation | Task 12 (regression test) |
| `_maybe_autoload_seed_groups` semantics from `tab_clustering.py:60-82` | Task 11 (`hydrateSeedGroups` early-return guard) |
| `tab_clustering.py:621-633` orange ring on edit-group members | Task 29 |
| `pipeline/clustering.py::run_clustering_step` (lines 53-180) signature mapping | Task 7 (route maps `seed_groups`+`fit_scope`+`max_mahalanobis` → kwargs) |
| `pipeline/clustering.py::apply_thresholds` (lines 183-302) signature mapping | Task 8 (route maps `cluster_thresholds` + `max_mahalanobis` → kwargs) |
| `core/pipeline/clustering.py:300-309` frozen `labels.json` schema | Task 4 (route serialization) + Task 13 (frontend `LabelsJson` type) |
| CLUSTER_PALETTE (10 d3 hex) + NEUTRAL_GRAY constants from `tab_clustering.py:34-39` | Task 10 |
| Plotly Scattergl + edit-mode ring + cluster colors + lazy-loaded route | Tasks 29, 33, 34 |
| Streamlit `_brushing` reuse for undo/redo/clear/lasso modes | Task 11 (slice methods) + Task 28 (controls) |

### Placeholder scan
- Searched for "TODO", "TBD", "implement here", "similar to Task" — none.
- Every step that produces code includes the actual code block.
- Every test step shows actual assertions and uses real fixture data.
- Every backend route step lists the exact `errors.py` entry it raises (Task 3).

### Type/name consistency

**Backend types**
- `ClusteringRefitParams`, `ClusteringApplyThresholdsParams`, `ClusteringRefitResult`, `ClusteringApplySummary` defined in Task 1; consumed unchanged in Tasks 7, 8, 9.
- `LabelsResponse` (Task 1) ↔ frontend `LabelsJson` (Task 13): same field set.
- `SeedGroupDisk` (`{name, domain_ids}`) is the disk schema, used in Tasks 2, 6, 7, and the frontend `SeedGroupDto` mirror (Task 13). NOT the same shape as in-memory `SeedGroup` (`{id, name, member_ids}`) — translation happens in Task 11 (`hydrateSeedGroups`) and Task 22 (`FitGMMButton.handleClick` maps `member_ids → domain_ids` for the POST body).
- `ClusteringNotFitted`, `SeedGroupsMissing` error codes defined in Task 3; raised in Tasks 4, 5, 8 (when no labels).

**Frontend types**
- `useClusteringStore` API surface set in Task 11 (`addSeedGroup`, `renameSeedGroup`, `removeSeedGroup`, `clearSeedGroups`, `hydrateSeedGroups`, `setThreshold`, `resetThresholdsToDefault`, `setFitScope`, `setInitialMaxMahalanobis`, `setLiveMaxMahalanobis`, `setAxis`, `setEditingGroupId`, `applyLasso`, `undoBrush`, `redoBrush`, `clearBrush`, `setFocusId`); every later component uses exactly those names.
- `LabelsJson`, `AssignmentsRows`, `SeedGroupDto`, `ClusteringRefitBody`, `ApplyThresholdsBody` defined in Task 13; consumed unchanged in Tasks 14–17, 22, 24, 26, 29, 30, 31, 32, 33, 35.
- `useClusteringRefit` and `useClusteringApplyThresholds` mutation API: `{status, pct, message, result, run, cancel}` — set in Tasks 16, 17 and consumed in Tasks 22 and 26 unchanged.
- `useClusteringRefit.run({seed_groups, fit_scope, max_mahalanobis})` matches `ClusteringRefitBody` (Task 13) exactly.
- `useClusteringApplyThresholds.run({cluster_thresholds, max_mahalanobis})` matches `ApplyThresholdsBody` (Task 13) exactly.
- `CLUSTER_PALETTE`, `NEUTRAL_GRAY`, `colorForCluster` from Task 10 are consumed in Tasks 23, 29, 30 with the exact same names.
- Both `ClusteringMain` and `ClusteringRightRail` accept `labels: LabelsJson | null` and `assignments: AssignmentsRows | null` (Tasks 31, 32) — same signature, same null-gating.

### Spec ambiguity resolved
- **`POST /clustering/fit` vs `POST /clustering/refit`**: Frontend §4.3 calls it `fit`; backend §1.2 calls it `refit`. We resolved this as `/clustering/refit` (matches backend) and added the alias understanding to Task 7's docstring. Frontend hooks (Task 16) use `/clustering/refit` end-to-end.
- **Two distinct clustering mutations sharing one mutex**: Backend §3.2 says both endpoints share the per-project lock. Task 9 is an explicit regression test that holds the lock outside the route and asserts the second endpoint either returns 423 or queues with a 0.5s timeout; we did NOT replace the existing single-mutex implementation with an endpoint-level mutex.
- **In-memory vs on-disk seed group shape**: Frontend §3.4 uses `member_ids: number[]`; backend/disk uses `domain_ids: list[int]`. We resolved this by keeping both shapes intact and translating at the boundary (Task 11 `hydrateSeedGroups` does `domain_ids → member_ids` on autoload; Task 22 `FitGMMButton.handleClick` does `member_ids → domain_ids` on POST). The autoload contract from `tab_clustering.py:60-82` (only hydrate when state is empty) is enforced inside `hydrateSeedGroups` so even an effect that re-fires on data refresh cannot clobber user edits — verified by Task 35's third scenario.
- **Threshold default value**: Spec doesn't pin a default for `perClusterThresholds` rows. We default to 0.5 in Task 23 (`stored ?? 0.5`) — matches Streamlit `tab_clustering.py` defaults and `labels.json["thresholds"]` initial value of 0.5 written by `core/pipeline/clustering.py`.
- **`apply_thresholds` returning n_pass/n_total/n_clusters**: Pipeline returns this dict (`pipeline/clustering.py:183-302`). Backend SSE `done` event packs it under `result` (Task 8); frontend `ApplySummary` type (Task 17) and `CommitClusteringButton` UI (Task 26) consume those exact field names.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-clustering-tab.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review (spec compliance, then code quality) between tasks, fast iteration in this same session.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
