# Selector Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sprint 2 of the React + FastAPI migration: deliver a fully working Selector tab — 5-metric filter, axis-pickable Plotly Scattergl scatter with lasso brushing, native `<img>` raw-image preview, virtualized flake table, focus precedence, and atomic commit (filter ∩ lasso) — talking to four new backend endpoints (`POST /run/selector`, `GET /data/domain_stats`, `GET /data/selector/selection`, `GET /data/annotations/{domain_id}/preview`, `GET /selector/export`).

**Architecture:** Backend adapters wrap `pipeline/selector.run_selector_step` (algorithm unchanged, lock+drain pattern from Plan 1 verbatim). Frontend uses TanStack Query for server arrays (`staleTime: Infinity`), Zustand `selectorSlice` for filter/axis/brush UI state, RHF for sliders+number inputs, lazy-loaded `react-plotly.js` Scattergl for scatter, native `<img>` + `usePanZoom` hook for the preview (Q-U3 — NOT OpenSeadragon), `react-window` for the virtualized flake table.

**Tech Stack:**
- Backend: FastAPI 0.110+, pydantic v2.6+, pyarrow 15+ (Arrow IPC streaming), pandas 2.x (already in use), httpx 0.28.1 (test transport), pytest-asyncio 0.23+
- Frontend: react-plotly.js 2.6+ (Plotly.js 2.30+), react-window 1.8+, react-hook-form 7.51+, lucide-react 0.358+, sonner 1.4+, vitest 1.4+, msw 2.2+

---

## File Structure

### Backend (new — under `src/flake_analysis/api/`)

- `schemas/selector.py` — `SelectorParams` (mirrors `pipeline/selector.py:29-43`), `SelectorSummary`, `SelectorCommitRequest` (params + `lasso_ids: list[int] | None`), `SelectorCommitSummary`, `MetricRange`, `MetricDefs` const
- `schemas/data_arrays.py` — `DomainStatsArrays` (typed array bundle: `flake_ids`, `repr_rgbs`, `std_pcts`, `areas`, `sam2?`), `SelectionRows`, `AnnotationPreview`
- `services/selector_service.py` — `apply_brush_intersection(parquet_path, lasso_ids) -> int` (ports the `_commit_selection` post-pipeline tightening from `tab_selector.py:773-779`)
- `services/arrow_writer.py` — `write_arrow_ipc(table: pa.Table) -> bytes` + `arrow_or_json_response(table, accept_header)` content-negotiation helper
- `services/annotation_preview.py` — `load_preview(annotations_path, raw_images_dir, domain_id, with_contour) -> bytes` (ports `_image_preview.py:200-360`'s server-side pieces — crop + optional outline overlay; returns PNG bytes)
- `routes/selector.py` — `POST /projects/{pid}/run/selector` (SSE, lock+drain), `POST /projects/{pid}/selector/commit` (synchronous JSON; runs selector step + brush intersection), `GET /projects/{pid}/selector/export?mode={filtered|selected}` (CSV streaming)

### Backend (modifications)

- `routes/data.py` — add `GET /projects/{pid}/data/domain_stats`, `GET /projects/{pid}/data/selector/selection`, `GET /projects/{pid}/data/annotations/{domain_id}/preview`
- `main.py` — `app.include_router(selector.router, prefix="/api/v1")`

### Frontend (new — under `web/src/`)

- `state/selectorSlice.ts` — Zustand slice per design §3.3
- `lib/brushing.ts` — `BrushingState`, `applyLasso`, `undo`, `redo`, `clearBrush` (ports `_brushing.py` semantics)
- `lib/usePanZoom.ts` — pinch/wheel/drag pan-zoom on a `<div>`+`<img>` (ports `_image_preview.py` UX bits — Q-U3)
- `lib/metricDefs.ts` — port of `_METRIC_DEFS` (`tab_selector.py:92-98`)
- `lib/focus.ts` — `pickFocusDomainId(state) -> number | null` (ports `_focus_domain_id` `tab_selector.py:695-708`)
- `api/selector.ts` — typed fetch wrappers for each new endpoint
- `hooks/useDomainStats.ts` — TanStack Query hook returning typed arrays
- `hooks/useSelectionRows.ts` — TanStack Query hook for `selection.parquet` rows
- `hooks/useAnnotationPreview.ts` — TanStack Query hook for the PNG preview
- `hooks/useSelectorCommit.ts` — TanStack Mutation hook around `POST /selector/commit`
- `components/selector/MetricRangeRow.tsx` — slider + 2 RHF number inputs for one metric, debounced commit to slice
- `components/selector/FilterControls.tsx` — 5 `<MetricRangeRow>` + Reset/Select-All
- `components/selector/AxisPicker.tsx` — radio group for X/Y axis
- `components/selector/BrushingControls.tsx` — Single/Lasso(R/A/D)/Zoom + Undo/Redo/Clear
- `components/selector/Live3DToggle.tsx` — checkbox toggle wired to slice
- `components/selector/LiveCounters.tsx` — accepted/rejected/selected/will-commit derived counters
- `components/selector/CommitButton.tsx` — wraps `useSelectorCommit`, disables while running, shows toast
- `components/selector/SelectorRightRail.tsx` — composes the above
- `components/selector/ScatterCanvas.tsx` — `react-plotly.js` Scattergl with lasso/click events, downsample cap 5000 pts
- `components/selector/ScatterPanel.tsx` — wraps `<ScatterCanvas>` + axis labels
- `components/selector/RawImagePreview.tsx` — native `<img>` + `usePanZoom` + boundary toggle
- `components/selector/ImagePreviewPanel.tsx` — wraps `<RawImagePreview>` + focus indicator
- `components/selector/RGBScatter3DPanel.tsx` — display-only Plotly Scatter3D (no events)
- `components/selector/FlakeTable.tsx` — `react-window` virtualized table, row click → focus
- `components/selector/FlakeListAccordion.tsx` — collapsible wrapper around `<FlakeTable>`
- `components/selector/SelectorMain.tsx` — composes scatter + image + 3d
- `pages/SelectorTab.tsx` — top-level tab, lazy-loads Plotly to keep main bundle small
- `App.tsx` — register the lazy `SelectorTab` route

### Frontend (modifications)

- `hooks/useStepProgress.ts` — extend return type with `result: unknown | null` populated from the `done` event payload (Selector commit needs the summary in the UI; reused by Plan 3 + 4)

### Tests (backend)

- `tests/api/test_selector_schemas.py`
- `tests/api/test_selector_service.py`
- `tests/api/test_arrow_writer.py`
- `tests/api/test_annotation_preview_service.py`
- `tests/api/test_data_domain_stats.py`
- `tests/api/test_data_selection.py`
- `tests/api/test_data_annotation_preview.py`
- `tests/api/test_run_selector_sse.py`
- `tests/api/test_selector_commit.py`
- `tests/api/test_selector_export.py`

### Tests (frontend)

- `web/src/state/__tests__/selectorSlice.test.ts`
- `web/src/lib/__tests__/brushing.test.ts`
- `web/src/lib/__tests__/focus.test.ts`
- `web/src/lib/__tests__/metricDefs.test.ts`
- `web/src/hooks/__tests__/useStepProgress.test.ts` (extend existing)
- `web/src/hooks/__tests__/useDomainStats.test.tsx`
- `web/src/components/selector/__tests__/MetricRangeRow.test.tsx`
- `web/src/components/selector/__tests__/AxisPicker.test.tsx`
- `web/src/components/selector/__tests__/BrushingControls.test.tsx`
- `web/src/components/selector/__tests__/LiveCounters.test.tsx`
- `web/src/components/selector/__tests__/CommitButton.test.tsx`
- `web/src/components/selector/__tests__/RawImagePreview.test.tsx`
- `web/src/components/selector/__tests__/FlakeTable.test.tsx`
- `web/src/pages/__tests__/SelectorTab.test.tsx`

---

## Tasks (Grouped into Phases)

### Phase 1 — Backend schemas + services (selector params, brush intersection, Arrow IPC, annotation preview)

#### Task 1: Selector schemas

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/schemas/selector.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_selector_schemas.py
from flake_analysis.api.schemas.selector import (
    SelectorParams,
    SelectorSummary,
    SelectorCommitRequest,
    SelectorCommitSummary,
    METRIC_DEFS,
)


def test_selector_params_all_optional():
    """All bounds are optional — None means unbounded (matches pipeline/selector.py:29-43)."""
    p = SelectorParams()
    assert p.area_min is None
    assert p.area_max is None
    assert p.std_r_min is None
    assert p.sam2_max is None


def test_selector_params_partial():
    """Partial bounds still parse."""
    p = SelectorParams(area_min=10.0, std_r_max=20.0)
    assert p.area_min == 10.0
    assert p.std_r_max == 20.0
    assert p.area_max is None


def test_selector_summary_shape():
    s = SelectorSummary(
        output_path="/p/03_selector/selection.parquet",
        selected_count=42,
        total_count=100,
        params={"area_min": 10.0},
        params_hash="sha256:abc",
    )
    assert s.selected_count == 42


def test_commit_request_lasso_optional():
    req = SelectorCommitRequest(params=SelectorParams(area_min=5.0), lasso_ids=None)
    assert req.lasso_ids is None
    req2 = SelectorCommitRequest(params=SelectorParams(), lasso_ids=[1, 2, 3])
    assert req2.lasso_ids == [1, 2, 3]


def test_commit_summary_shape():
    s = SelectorCommitSummary(
        output_path="/p/03_selector/selection.parquet",
        n_committed=5,
        n_filter_accepted=10,
        n_lasso=7,
        total_count=100,
        params_hash="sha256:abc",
    )
    assert s.n_committed == 5


def test_metric_defs_has_five_entries():
    """Ports tab_selector.py:92-98."""
    keys = [d["key"] for d in METRIC_DEFS]
    assert keys == ["area", "std_r", "std_g", "std_b", "sam2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.schemas.selector'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/schemas/selector.py
"""Selector schemas per backend design §1.2 + §1.3."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class SelectorParams(BaseModel):
    """Mirrors pipeline/selector.py:29-43 — all bounds optional, None = unbounded."""
    area_min: float | None = None
    area_max: float | None = None
    std_r_min: float | None = None
    std_r_max: float | None = None
    std_g_min: float | None = None
    std_g_max: float | None = None
    std_b_min: float | None = None
    std_b_max: float | None = None
    sam2_min: float | None = None
    sam2_max: float | None = None


class SelectorSummary(BaseModel):
    """Result wrapper used inside SSE 'done' event for POST /run/selector."""
    output_path: str
    selected_count: int
    total_count: int
    params: dict[str, Any]
    params_hash: str | None


class SelectorCommitRequest(BaseModel):
    """Body for POST /selector/commit (synchronous JSON)."""
    params: SelectorParams
    lasso_ids: list[int] | None = None


class SelectorCommitSummary(BaseModel):
    """Result of POST /selector/commit — includes intersection stats."""
    output_path: str
    n_committed: int          # final selected=True count after brush ∩ filter
    n_filter_accepted: int    # filter pass count (== selected_count from pipeline)
    n_lasso: int              # |lasso_ids| or 0 if None
    total_count: int
    params_hash: str | None


# Ports tab_selector.py:92-98 to a structured const so the frontend can fetch defaults.
METRIC_DEFS: list[dict[str, Any]] = [
    {"key": "area",  "label": "Area (px)",   "lo": 0.0, "hi": 1_000_000.0, "step": 10.0,  "fmt": "%.0f"},
    {"key": "std_r", "label": "Std R %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "std_g", "label": "Std G %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "std_b", "label": "Std B %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "sam2",  "label": "SAM2 score",  "lo": 0.0, "hi": 1.0,         "step": 0.05,  "fmt": "%.2f"},
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_schemas.py -v`
Expected: PASS — 6 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/schemas/selector.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_schemas.py
git commit -m "feat(api): add selector schemas + METRIC_DEFS port"
```

#### Task 2: Brush ∩ filter intersection service

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/__init__.py` (empty)
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/selector_service.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_selector_service.py
from pathlib import Path
import pandas as pd
import pytest

from flake_analysis.api.services.selector_service import apply_brush_intersection


def _write_selection(p: Path, rows: list[tuple[int, bool]]) -> None:
    df = pd.DataFrame(rows, columns=["domain_id", "selected"])
    df.to_parquet(p, index=False)


def test_no_lasso_keeps_pipeline_output(tmp_path):
    """lasso_ids=None → return original count, file unchanged."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True), (3, False)])

    n = apply_brush_intersection(p, lasso_ids=None)
    assert n == 2

    df = pd.read_parquet(p)
    assert df.loc[df["domain_id"] == 1, "selected"].iat[0] == True
    assert df.loc[df["domain_id"] == 2, "selected"].iat[0] == True


def test_empty_lasso_keeps_pipeline_output(tmp_path):
    """lasso_ids=[] is treated as "no brush" (matches tab_selector.py:773)."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True)])

    n = apply_brush_intersection(p, lasso_ids=[])
    assert n == 2


def test_lasso_intersection_tightens(tmp_path):
    """Brush ∩ filter — domains lassoed but rejected by filter stay rejected."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True), (3, False), (4, True)])

    # Lasso [2, 3, 5] — only 2 is in filter; 3 was rejected; 5 doesn't exist.
    n = apply_brush_intersection(p, lasso_ids=[2, 3, 5])
    assert n == 1

    df = pd.read_parquet(p)
    assert df.loc[df["domain_id"] == 1, "selected"].iat[0] == False
    assert df.loc[df["domain_id"] == 2, "selected"].iat[0] == True
    assert df.loc[df["domain_id"] == 3, "selected"].iat[0] == False
    assert df.loc[df["domain_id"] == 4, "selected"].iat[0] == False


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        apply_brush_intersection(tmp_path / "nope.parquet", lasso_ids=[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/__init__.py
```

```python
# src/flake_analysis/api/services/selector_service.py
"""Brush ∩ filter intersection — ports tab_selector.py:773-779."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def apply_brush_intersection(
    selection_parquet_path: str | Path,
    *,
    lasso_ids: list[int] | None,
) -> int:
    """Tighten ``selected`` to (filter ∩ lasso) when a non-empty lasso set is given.

    Mirrors tab_selector.py:773-779:
      - lasso_ids None or [] → file untouched, return current True count
      - lasso_ids non-empty  → selected := selected & isin(lasso_ids), rewrite parquet

    Returns the final count of selected=True rows.
    """
    p = Path(selection_parquet_path)
    if not p.exists():
        raise FileNotFoundError(f"selection.parquet missing at {p}")

    df = pd.read_parquet(p)
    if lasso_ids is None or len(lasso_ids) == 0:
        return int(df["selected"].astype(bool).sum())

    brush_arr = np.fromiter((int(x) for x in lasso_ids), dtype=np.int64)
    in_brush = df["domain_id"].astype(np.int64).isin(brush_arr)
    df["selected"] = df["selected"].astype(bool) & in_brush
    df.to_parquet(p, index=False)
    return int(df["selected"].sum())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_service.py -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/__init__.py /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/selector_service.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_service.py
git commit -m "feat(api): add brush-intersection selector service"
```

#### Task 3: Arrow IPC writer + content negotiation

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/arrow_writer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_arrow_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_arrow_writer.py
import io
import pyarrow as pa
import pyarrow.ipc as ipc

from flake_analysis.api.services.arrow_writer import (
    write_arrow_ipc,
    arrow_or_json_response,
)


def _table():
    return pa.table({"a": pa.array([1, 2, 3], type=pa.int32()),
                     "b": pa.array([1.0, 2.0, 3.0], type=pa.float64())})


def test_write_arrow_ipc_roundtrips():
    buf = write_arrow_ipc(_table())
    assert isinstance(buf, bytes)
    assert len(buf) > 0
    reader = ipc.open_stream(io.BytesIO(buf))
    out = reader.read_all()
    assert out.column("a").to_pylist() == [1, 2, 3]


def test_arrow_or_json_response_arrow_accept():
    resp = arrow_or_json_response(
        _table(),
        accept_header="application/vnd.apache.arrow.stream",
    )
    assert resp.media_type == "application/vnd.apache.arrow.stream"
    assert isinstance(resp.body, bytes)
    reader = ipc.open_stream(io.BytesIO(resp.body))
    out = reader.read_all()
    assert out.num_rows == 3


def test_arrow_or_json_response_json_default():
    """Default Accept (or */*) returns JSON column-oriented payload."""
    resp = arrow_or_json_response(
        _table(),
        accept_header=None,
    )
    assert resp.media_type == "application/json"
    import json
    payload = json.loads(resp.body)
    assert payload == {"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]}


def test_arrow_or_json_response_explicit_json():
    resp = arrow_or_json_response(
        _table(),
        accept_header="application/json",
    )
    assert resp.media_type == "application/json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_arrow_writer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/arrow_writer.py
"""Arrow IPC + JSON content negotiation per backend design §1.3 'Why Arrow IPC'."""
from __future__ import annotations
import io
import json

import pyarrow as pa
import pyarrow.ipc as ipc
from fastapi import Response

ARROW_MIME = "application/vnd.apache.arrow.stream"


def write_arrow_ipc(table: pa.Table) -> bytes:
    """Serialize a pyarrow Table to Arrow IPC stream bytes."""
    sink = io.BytesIO()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def _table_to_json_columns(table: pa.Table) -> bytes:
    """Column-oriented JSON: {col_name: [values]} — matches frontend typed-array shape."""
    payload = {name: table.column(name).to_pylist() for name in table.column_names}
    return json.dumps(payload).encode("utf-8")


def arrow_or_json_response(
    table: pa.Table,
    *,
    accept_header: str | None,
) -> Response:
    """Return Arrow IPC if the client asked for it, else JSON column-oriented."""
    wants_arrow = bool(accept_header) and ARROW_MIME in (accept_header or "")
    if wants_arrow:
        return Response(content=write_arrow_ipc(table), media_type=ARROW_MIME)
    return Response(content=_table_to_json_columns(table), media_type="application/json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_arrow_writer.py -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/arrow_writer.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_arrow_writer.py
git commit -m "feat(api): add Arrow IPC writer + JSON negotiation helper"
```

#### Task 4: Annotation preview service

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/annotation_preview.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_annotation_preview_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_annotation_preview_service.py
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from flake_analysis.api.services.annotation_preview import load_preview


@pytest.fixture
def fake_project(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    img = Image.new("RGB", (200, 200), color=(40, 80, 120))
    img.save(raw / "tile_0.png")

    annotations = {
        "tile_0.png": {
            "domains": [
                {"domain_id": 7, "bbox": [50, 50, 100, 100], "polygon": [[50, 50], [100, 50], [100, 100], [50, 100]]},
            ],
        }
    }
    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps(annotations))

    return raw, ann_path


def test_load_preview_returns_png_bytes(fake_project):
    raw, ann_path = fake_project
    png = load_preview(
        annotations_path=ann_path,
        raw_images_dir=raw,
        domain_id=7,
        with_contour=False,
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (50, 50)


def test_load_preview_with_contour_returns_png(fake_project):
    raw, ann_path = fake_project
    png = load_preview(
        annotations_path=ann_path,
        raw_images_dir=raw,
        domain_id=7,
        with_contour=True,
    )
    img = Image.open(io.BytesIO(png))
    arr = np.array(img)
    # Contour pixels (red) should be present somewhere along the bbox edge.
    red = (arr[..., 0] > 200) & (arr[..., 1] < 80) & (arr[..., 2] < 80)
    assert red.any()


def test_load_preview_unknown_domain_raises(fake_project):
    raw, ann_path = fake_project
    with pytest.raises(KeyError):
        load_preview(
            annotations_path=ann_path,
            raw_images_dir=raw,
            domain_id=999,
            with_contour=False,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_annotation_preview_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/annotation_preview.py
"""Server-side raw-image crop + optional contour overlay.

Ports the data loading + drawing pieces of ui/_image_preview.py:200-360
(the Streamlit version did its own pan/zoom in the browser; here we
serve a fixed crop + optional outline as PNG, and the frontend handles
pan/zoom client-side per Q-U3).
"""
from __future__ import annotations
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw


def _load_index(annotations_path: Path) -> dict:
    with open(annotations_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_domain(index: dict, domain_id: int) -> tuple[str, dict]:
    for tile_name, payload in index.items():
        for d in payload.get("domains", []):
            if int(d.get("domain_id", -1)) == int(domain_id):
                return tile_name, d
    raise KeyError(f"domain_id {domain_id} not found in annotations")


def load_preview(
    *,
    annotations_path: str | Path,
    raw_images_dir: str | Path,
    domain_id: int,
    with_contour: bool,
) -> bytes:
    """Return PNG bytes for the crop around ``domain_id``.

    ``with_contour=True`` overlays the polygon in red (RGB 255,0,0) at 1px width.
    """
    index = _load_index(Path(annotations_path))
    tile_name, dom = _find_domain(index, domain_id)
    bbox = dom["bbox"]  # [x0, y0, x1, y1]
    polygon = dom.get("polygon") or []

    img_path = Path(raw_images_dir) / tile_name
    if not img_path.exists():
        raise FileNotFoundError(f"raw image missing: {img_path}")

    with Image.open(img_path) as src:
        crop = src.convert("RGB").crop(tuple(bbox))

    if with_contour and polygon:
        draw = ImageDraw.Draw(crop)
        x0, y0, _, _ = bbox
        local = [(int(px - x0), int(py - y0)) for px, py in polygon]
        if len(local) >= 2:
            draw.line(local + [local[0]], fill=(255, 0, 0), width=1)

    out = io.BytesIO()
    crop.save(out, format="PNG")
    return out.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_annotation_preview_service.py -v`
Expected: PASS — 3 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/annotation_preview.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_annotation_preview_service.py
git commit -m "feat(api): add annotation preview service (crop + optional contour)"
```

### Phase 2 — Backend data-read routes (domain_stats, selection, annotation preview)

#### Task 5: GET /data/domain_stats — Arrow IPC / JSON

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_domain_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_domain_stats.py
import io
import json
import os
from pathlib import Path

import numpy as np
import pyarrow.ipc as ipc
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup_project(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    stats_dir = analysis / "02_domain_stats"
    stats_dir.mkdir()
    np.savez(
        stats_dir / "stats.npz",
        flake_ids=np.array([1, 2, 3], dtype=np.int64),
        repr_rgbs=np.array([[10, 20, 30], [40, 50, 60], [70, 80, 90]], dtype=np.float64),
        std_pcts=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], dtype=np.float64),
        areas=np.array([100.0, 200.0, 300.0], dtype=np.float64),
        sam2=np.array([0.1, 0.5, 0.9], dtype=np.float64),
    )
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_data_domain_stats_json(tmp_path):
    _setup_project(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/domain_stats")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/json")
            payload = r.json()
            assert payload["flake_ids"] == [1, 2, 3]
            assert payload["areas"] == [100.0, 200.0, 300.0]
            assert payload["sam2"] == [0.1, 0.5, 0.9]
            # std_pcts split into 3 columns for typed-array consumption
            assert payload["std_r"] == [1.0, 4.0, 7.0]
            assert payload["std_g"] == [2.0, 5.0, 8.0]
            assert payload["std_b"] == [3.0, 6.0, 9.0]
            # repr_rgbs likewise
            assert payload["mean_r"] == [10.0, 40.0, 70.0]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_data_domain_stats_arrow(tmp_path):
    _setup_project(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/data/domain_stats",
                headers={"Accept": "application/vnd.apache.arrow.stream"},
            )
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/vnd.apache.arrow.stream"
            reader = ipc.open_stream(io.BytesIO(r.content))
            table = reader.read_all()
            assert table.num_rows == 3
            assert table.column("flake_ids").to_pylist() == [1, 2, 3]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_data_domain_stats_missing_npz(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/domain_stats")
            assert r.status_code == 404
            body = r.json()
            assert body["error"]["code"] == "domain_stats_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_domain_stats.py -v`
Expected: FAIL with `404 Not Found` (route does not exist yet)

- [ ] **Step 3: Write minimal implementation**

Modify `src/flake_analysis/api/routes/data.py` — add the new endpoint:

```python
"""Data read endpoints per backend design §1.3."""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pyarrow as pa
from fastapi import APIRouter, Depends, Header

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import AppError
from flake_analysis.api.schemas.data import ManifestModel
from flake_analysis.api.services.arrow_writer import arrow_or_json_response
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}/data", tags=["data"])


@router.get("/manifest")
async def get_manifest_endpoint(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> ManifestModel:
    """Return manifest as JSON."""
    return ManifestModel.model_validate(manifest)


def _load_stats_table(analysis_folder: str | Path) -> pa.Table:
    npz_path = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    if not npz_path.exists():
        raise AppError(
            code="domain_stats_not_found",
            message="Domain Stats not computed yet. Run Compute → Domain Stats first.",
            status_code=404,
            details={"path": str(npz_path)},
        )
    z = np.load(npz_path, allow_pickle=False)
    flake_ids = z["flake_ids"].astype(np.int64)
    repr_rgbs = z["repr_rgbs"].astype(np.float64)
    std_pcts = z["std_pcts"].astype(np.float64)
    areas = z["areas"].astype(np.float64)

    cols: dict[str, pa.Array] = {
        "flake_ids": pa.array(flake_ids, type=pa.int64()),
        "mean_r": pa.array(repr_rgbs[:, 0], type=pa.float64()),
        "mean_g": pa.array(repr_rgbs[:, 1], type=pa.float64()),
        "mean_b": pa.array(repr_rgbs[:, 2], type=pa.float64()),
        "std_r": pa.array(std_pcts[:, 0], type=pa.float64()),
        "std_g": pa.array(std_pcts[:, 1], type=pa.float64()),
        "std_b": pa.array(std_pcts[:, 2], type=pa.float64()),
        "areas": pa.array(areas, type=pa.float64()),
    }
    if "sam2" in z.files:
        cols["sam2"] = pa.array(z["sam2"].astype(np.float64), type=pa.float64())
    return pa.table(cols)


@router.get("/domain_stats")
async def get_domain_stats(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return domain stats arrays (Arrow IPC if Accept: application/vnd.apache.arrow.stream, else JSON)."""
    table = _load_stats_table(manifest.analysis_folder)
    return arrow_or_json_response(table, accept_header=accept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_domain_stats.py -v`
Expected: PASS — 3 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_domain_stats.py
git commit -m "feat(api): add GET /data/domain_stats with Arrow IPC + JSON"
```

#### Task 6: GET /data/selector/selection

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_selection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_selection.py
import os
from pathlib import Path

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    (analysis / "03_selector").mkdir()
    df = pd.DataFrame({
        "domain_id": [1, 2, 3, 4],
        "selected": [True, False, True, True],
    })
    df.to_parquet(analysis / "03_selector" / "selection.parquet", index=False)

    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_selection_json(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/selector/selection")
            assert r.status_code == 200
            payload = r.json()
            assert payload["domain_id"] == [1, 2, 3, 4]
            assert payload["selected"] == [True, False, True, True]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_selection_404_when_missing(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/selector/selection")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "selection_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_selection.py -v`
Expected: FAIL with `404` and route not present

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/data.py` (keep existing content):

```python
import pandas as pd

@router.get("/selector/selection")
async def get_selection(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return 03_selector/selection.parquet rows (Arrow IPC or JSON)."""
    p = Path(manifest.analysis_folder) / "03_selector" / "selection.parquet"
    if not p.exists():
        raise AppError(
            code="selection_not_found",
            message="No selection committed yet. Click Commit on the Selector tab.",
            status_code=404,
            details={"path": str(p)},
        )
    df = pd.read_parquet(p)
    table = pa.Table.from_pandas(df, preserve_index=False)
    return arrow_or_json_response(table, accept_header=accept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_selection.py -v`
Expected: PASS — 2 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_selection.py
git commit -m "feat(api): add GET /data/selector/selection"
```

#### Task 7: GET /data/annotations/{domain_id}/preview

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_annotation_preview.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_annotation_preview.py
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    Image.new("RGB", (200, 200), (10, 20, 30)).save(raw / "tile_0.png")

    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps({
        "tile_0.png": {
            "domains": [
                {"domain_id": 7, "bbox": [50, 50, 100, 100], "polygon": [[50, 50], [100, 50], [100, 100], [50, 100]]},
            ],
        }
    }))

    save_manifest(
        Manifest(
            analysis_folder=str(analysis),
            raw_images_dir=str(raw),
            annotations_path=str(ann_path),
        ),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_annotation_preview_returns_png(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/annotations/7/preview")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            img = Image.open(io.BytesIO(r.content))
            assert img.size == (50, 50)
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_annotation_preview_with_contour_query(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/data/annotations/7/preview",
                params={"with_contour": "true"},
            )
            assert r.status_code == 200
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_annotation_preview_404(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/annotations/9999/preview")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "domain_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_annotation_preview.py -v`
Expected: FAIL — endpoint missing

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/data.py`:

```python
from fastapi import Response
from flake_analysis.api.services.annotation_preview import load_preview


@router.get("/annotations/{domain_id}/preview")
async def get_annotation_preview(
    domain_id: int,
    with_contour: bool = False,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return PNG crop around ``domain_id`` (optionally with red contour overlay)."""
    if not manifest.annotations_path:
        raise AppError(
            code="annotations_path_unset",
            message="annotations_path is not configured for this project.",
            status_code=400,
            details={},
        )
    try:
        png = load_preview(
            annotations_path=manifest.annotations_path,
            raw_images_dir=manifest.raw_images_dir,
            domain_id=domain_id,
            with_contour=with_contour,
        )
    except KeyError as e:
        raise AppError(
            code="domain_not_found",
            message=str(e),
            status_code=404,
            details={"domain_id": domain_id},
        )
    return Response(content=png, media_type="image/png")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_annotation_preview.py -v`
Expected: PASS — 3 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/data.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_annotation_preview.py
git commit -m "feat(api): add GET /data/annotations/{domain_id}/preview"
```

### Phase 3 — Selector run/commit/export routes

#### Task 8: POST /run/selector with SSE (lock+drain pattern from Plan 1)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_selector_sse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_selector_sse.py
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


@pytest.mark.asyncio
async def test_run_selector_sse_streams_progress_and_done(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)

    def mock_run_selector(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "loading")
            cb(0.5, "filtering")
            cb(1.0, "done")
        return {
            "output_path": str(analysis / "03_selector" / "selection.parquet"),
            "selected_count": 7,
            "total_count": 12,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:zzz",
        }

    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=mock_run_selector,
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/api/v1/projects/local/run/selector",
                    json={"area_min": 5.0},
                ) as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]
                    events = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
                    progress = [e for e in events if e["type"] == "progress"]
                    done = [e for e in events if e["type"] == "done"]
                    assert len(progress) == 3
                    assert len(done) == 1
                    assert done[0]["result"]["selected_count"] == 7
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_run_selector_propagates_pipeline_error(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)

    def boom(**_kwargs):
        raise RuntimeError("Domain Stats step not completed.")

    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=boom,
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/api/v1/projects/local/run/selector",
                    json={},
                ) as resp:
                    events = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
                    err = [e for e in events if e["type"] == "error"]
                    assert len(err) == 1
                    assert err[0]["error"]["code"] == "pipeline_failed"
                    assert "Domain Stats" in err[0]["error"]["message"]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_selector_sse.py -v`
Expected: FAIL with `404 Not Found` (route absent)

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/routes/selector.py
"""Selector routes per backend design §1.2 + frontend design §4.2.

POST /run/selector — SSE.
POST /selector/commit — synchronous JSON; runs selector + brush ∩ filter.
GET  /selector/export — CSV stream of filtered or selected rows.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.schemas.selector import (
    SelectorParams,
)
from flake_analysis.api.sse import ProgressBridge, emit_sse_event
from flake_analysis.state.manifest import Manifest
from flake_analysis.pipeline.selector import run_selector_step

router = APIRouter(prefix="/projects/{project_id}", tags=["selector"])


@router.post("/run/selector")
async def run_selector(
    project_id: str,
    params: SelectorParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Run selector pipeline step with SSE progress (writes selection.parquet)."""
    # Lock+drain pattern (verbatim from routes/run.py): acquire synchronously so
    # contention surfaces as a 423 ProjectBusy HTTP error, then drain in the
    # generator's finally to release exactly once.
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_selector_step(
            analysis_folder=manifest.analysis_folder,
            area_min=params.area_min,
            area_max=params.area_max,
            std_r_min=params.std_r_min,
            std_r_max=params.std_r_max,
            std_g_min=params.std_g_min,
            std_g_max=params.std_g_max,
            std_b_min=params.std_b_min,
            std_b_max=params.std_b_max,
            sam2_min=params.sam2_min,
            sam2_max=params.sam2_max,
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

Modify `src/flake_analysis/api/main.py` — add the router. Add to imports and to `create_app()`:

```python
# in imports
from flake_analysis.api.routes import health, version, projects, data, run, selector

# inside create_app(), after existing app.include_router(...) calls
app.include_router(selector.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_selector_sse.py -v`
Expected: PASS — 2 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_selector_sse.py
git commit -m "feat(api): add POST /run/selector with SSE (lock+drain)"
```

#### Task 9: POST /selector/commit (synchronous JSON; brush ∩ filter)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_commit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_selector_commit.py
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _make_pipeline_mock(analysis: Path):
    """Returns a stub run_selector_step that writes a 4-row selection.parquet."""
    def stub(**kwargs):
        out = analysis / "03_selector"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "selection.parquet"
        pd.DataFrame({
            "domain_id": [1, 2, 3, 4],
            "selected": [True, True, False, True],
        }).to_parquet(p, index=False)
        return {
            "output_path": str(p),
            "selected_count": 3,
            "total_count": 4,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:abc",
        }
    return stub


@pytest.mark.asyncio
async def test_commit_no_lasso_returns_filter_count(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=_make_pipeline_mock(analysis),
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/projects/local/selector/commit",
                    json={"params": {"area_min": 5.0}, "lasso_ids": None},
                )
                assert r.status_code == 200
                body = r.json()
                assert body["n_committed"] == 3
                assert body["n_filter_accepted"] == 3
                assert body["n_lasso"] == 0
                assert body["total_count"] == 4
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_commit_with_lasso_intersects(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=_make_pipeline_mock(analysis),
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/projects/local/selector/commit",
                    json={"params": {"area_min": 5.0}, "lasso_ids": [2, 3]},
                )
                assert r.status_code == 200
                body = r.json()
                # filter: {1,2,4} accepted; lasso: {2,3}; intersection: {2}
                assert body["n_committed"] == 1
                assert body["n_filter_accepted"] == 3
                assert body["n_lasso"] == 2
                # Verify file actually rewritten
                df = pd.read_parquet(analysis / "03_selector" / "selection.parquet")
                rows = dict(zip(df["domain_id"].tolist(), df["selected"].tolist()))
                assert rows == {1: False, 2: True, 3: False, 4: False}
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_commit.py -v`
Expected: FAIL with `404` (route absent)

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/selector.py`:

```python
from flake_analysis.api.schemas.selector import (
    SelectorCommitRequest,
    SelectorCommitSummary,
)
from flake_analysis.api.services.selector_service import apply_brush_intersection


@router.post("/selector/commit")
async def commit_selection(
    project_id: str,
    body: SelectorCommitRequest,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> SelectorCommitSummary:
    """Run selector pipeline + apply brush intersection. Synchronous JSON."""
    async with acquire_project_lock(project_id):
        loop = asyncio.get_running_loop()

        def _call():
            return run_selector_step(
                analysis_folder=manifest.analysis_folder,
                area_min=body.params.area_min,
                area_max=body.params.area_max,
                std_r_min=body.params.std_r_min,
                std_r_max=body.params.std_r_max,
                std_g_min=body.params.std_g_min,
                std_g_max=body.params.std_g_max,
                std_b_min=body.params.std_b_min,
                std_b_max=body.params.std_b_max,
                sam2_min=body.params.sam2_min,
                sam2_max=body.params.sam2_max,
                progress_callback=None,
            )

        result = await loop.run_in_executor(None, _call)
        out_path = Path(str(result["output_path"]))
        n_filter_accepted = int(result["selected_count"])
        total_count = int(result["total_count"])

        n_committed = await loop.run_in_executor(
            None,
            lambda: apply_brush_intersection(out_path, lasso_ids=body.lasso_ids),
        )

        return SelectorCommitSummary(
            output_path=str(out_path),
            n_committed=n_committed,
            n_filter_accepted=n_filter_accepted,
            n_lasso=len(body.lasso_ids) if body.lasso_ids else 0,
            total_count=total_count,
            params_hash=result.get("params_hash"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_commit.py -v`
Expected: PASS — 2 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_commit.py
git commit -m "feat(api): add POST /selector/commit (filter ∩ lasso)"
```

#### Task 10: GET /selector/export?mode={filtered|selected} CSV stream

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_export.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_selector_export.py
import os
from pathlib import Path

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    (analysis / "03_selector").mkdir()
    pd.DataFrame({
        "domain_id": [1, 2, 3, 4],
        "selected": [True, False, True, True],
    }).to_parquet(analysis / "03_selector" / "selection.parquet", index=False)
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_export_selected_only(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "selected"},
            )
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/csv")
            lines = r.text.strip().splitlines()
            assert lines[0] == "domain_id,selected"
            assert {l for l in lines[1:]} == {"1,True", "3,True", "4,True"}
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_export_filtered_returns_all_rows(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "filtered"},
            )
            assert r.status_code == 200
            lines = r.text.strip().splitlines()
            assert len(lines) == 5  # header + 4 rows
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_export_invalid_mode(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "garbage"},
            )
            assert r.status_code == 422
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_export.py -v`
Expected: FAIL — endpoint missing

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/selector.py`:

```python
import io
from typing import Literal
from fastapi.responses import StreamingResponse
import pandas as pd

from flake_analysis.api.errors import AppError


@router.get("/selector/export")
async def export_selection(
    project_id: str,
    mode: Literal["filtered", "selected"] = "selected",
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Stream selection.parquet rows as CSV.

    mode=filtered → all rows (every domain with its selected boolean).
    mode=selected → only rows where ``selected`` is True.
    """
    p = Path(manifest.analysis_folder) / "03_selector" / "selection.parquet"
    if not p.exists():
        raise AppError(
            code="selection_not_found",
            message="No selection committed yet.",
            status_code=404,
            details={"path": str(p)},
        )
    df = pd.read_parquet(p)
    if mode == "selected":
        df = df[df["selected"].astype(bool)]

    def _iter():
        # Single-shot CSV (a few hundred KB even at 10⁵ rows; no need to chunk).
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        yield buf.getvalue()

    headers = {
        "Content-Disposition": f'attachment; filename="selection_{mode}.csv"',
    }
    return StreamingResponse(_iter(), media_type="text/csv; charset=utf-8", headers=headers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_export.py -v`
Expected: PASS — 3 tests

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/selector.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_selector_export.py
git commit -m "feat(api): add GET /selector/export CSV endpoint"
```

### Phase 4 — Frontend foundation (deps, slice, brushing, focus, metric defs, hook extension)

#### Task 11: Install frontend deps for Selector

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/package.json`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/package-lock.json` (auto)

- [ ] **Step 1: Install runtime deps**

Run (in `/Users/houkjang/projects/stand-alone-analyzer/web/`):

```bash
npm install --save \
  react-plotly.js@^2.6.0 \
  plotly.js-dist-min@^2.30.0 \
  react-window@^1.8.10 \
  react-hook-form@^7.51.0 \
  zustand@^4.5.0
```

Expected: `package.json` `dependencies` block now contains all five.
Bundle-size note: `plotly.js-dist-min` adds ~1.3MB gzipped. The Selector route MUST lazy-import the wrapper (Task 25) so this only loads when the user clicks the Selector tab.

- [ ] **Step 2: Install dev deps for tests**

Run (in `/Users/houkjang/projects/stand-alone-analyzer/web/`):

```bash
npm install --save-dev \
  @types/react-plotly.js@^2.6.0 \
  @types/react-window@^1.8.8
```

- [ ] **Step 3: Add a no-op type shim to keep TS strict happy**

Create `/Users/houkjang/projects/stand-alone-analyzer/web/src/plotly.d.ts`:

```ts
// react-plotly.js publishes minimal types; re-export the factory shape we use.
// Note: we import React inside the module declaration so the global namespace
// resolution works under TS strict + react-jsx without a top-level React import
// in source files that consume Plot.
declare module 'react-plotly.js' {
  import type { ComponentType, CSSProperties } from 'react'
  export interface PlotParams {
    data: any[]
    layout?: any
    config?: any
    frames?: any[]
    style?: CSSProperties
    className?: string
    onClick?: (event: any) => void
    onSelected?: (event: any) => void
    onRelayout?: (event: any) => void
    useResizeHandler?: boolean
    revision?: number
  }
  const Plot: ComponentType<PlotParams>
  export default Plot
}
```

- [ ] **Step 4: Verify install**

Run (in `/Users/houkjang/projects/stand-alone-analyzer/web/`):

```bash
npm run typecheck
```

Expected: PASS (no new errors).

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/package.json /Users/houkjang/projects/stand-alone-analyzer/web/package-lock.json /Users/houkjang/projects/stand-alone-analyzer/web/src/plotly.d.ts
git commit -m "chore(web): add plotly + react-window + RHF for Selector"
```

#### Task 12: Port `_METRIC_DEFS` to TS

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/metricDefs.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/metricDefs.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/lib/__tests__/metricDefs.test.ts
import { describe, expect, it } from 'vitest'
import { METRIC_DEFS, type MetricKey, defaultRange } from '@/lib/metricDefs'

describe('METRIC_DEFS', () => {
  it('has the 5 entries from tab_selector.py:92-98', () => {
    const keys = METRIC_DEFS.map((d) => d.key)
    expect(keys).toEqual(['area', 'std_r', 'std_g', 'std_b', 'sam2'])
  })

  it('area bounds match Streamlit defaults', () => {
    const area = METRIC_DEFS.find((d) => d.key === 'area')!
    expect(area.lo).toBe(0)
    expect(area.hi).toBe(1_000_000)
    expect(area.step).toBe(10)
  })

  it('sam2 bounds are [0, 1]', () => {
    const sam2 = METRIC_DEFS.find((d) => d.key === 'sam2')!
    expect(sam2.lo).toBe(0)
    expect(sam2.hi).toBe(1)
  })

  it('defaultRange returns [lo, hi]', () => {
    expect(defaultRange('std_r')).toEqual([0, 100])
  })

  it('MetricKey type covers all keys', () => {
    const k: MetricKey = 'area'
    expect(typeof k).toBe('string')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/lib/__tests__/metricDefs.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/lib/metricDefs.ts
export type MetricKey = 'area' | 'std_r' | 'std_g' | 'std_b' | 'sam2'

export interface MetricDef {
  key: MetricKey
  label: string
  lo: number
  hi: number
  step: number
  /** printf-style format mirrored from Streamlit defaults; consumed by formatters in MetricRangeRow. */
  fmt: string
}

export const METRIC_DEFS: readonly MetricDef[] = [
  { key: 'area', label: 'Area (px)', lo: 0, hi: 1_000_000, step: 10, fmt: '%.0f' },
  { key: 'std_r', label: 'Std R %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'std_g', label: 'Std G %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'std_b', label: 'Std B %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'sam2', label: 'SAM2 score', lo: 0, hi: 1, step: 0.05, fmt: '%.2f' },
]

export function defaultRange(key: MetricKey): [number, number] {
  const def = METRIC_DEFS.find((d) => d.key === key)!
  return [def.lo, def.hi]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/lib/__tests__/metricDefs.test.ts`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/metricDefs.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/metricDefs.test.ts
git commit -m "feat(web): port _METRIC_DEFS to metricDefs.ts"
```

#### Task 13: Brushing state + ops (port `_brushing.py`)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/brushing.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/brushing.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/lib/__tests__/brushing.test.ts
import { describe, expect, it } from 'vitest'
import {
  emptyBrushing,
  applyLasso,
  type BrushingState,
  type LassoMode,
  undo,
  redo,
  clearBrush,
  setFocusId,
} from '@/lib/brushing'

const ids = (s: Set<number>) => Array.from(s).sort((a, b) => a - b)

describe('applyLasso', () => {
  it('replace mode replaces selection', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2, 3], 'replace')
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
    s = applyLasso(s, [4, 5], 'replace')
    expect(ids(s.selectedIds)).toEqual([4, 5])
  })

  it('add mode unions ids', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [2, 3], 'add')
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
  })

  it('remove mode subtracts ids', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2, 3, 4], 'replace')
    s = applyLasso(s, [2, 3], 'remove')
    expect(ids(s.selectedIds)).toEqual([1, 4])
  })

  it('pushes onto history for undo', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    expect(s.history.length).toBe(2)
  })
})

describe('undo / redo', () => {
  it('undo reverts to previous state', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    expect(ids(s.selectedIds)).toEqual([1, 2])
  })

  it('redo replays undone state', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    s = redo(s)
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
  })

  it('redo stack cleared when a new lasso is applied after undo', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    s = applyLasso(s, [9], 'add')
    s = redo(s)
    // redo is a no-op now
    expect(ids(s.selectedIds)).toEqual([1, 2, 9])
  })
})

describe('clearBrush', () => {
  it('clears selection but preserves focus', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = setFocusId(s, 7)
    s = clearBrush(s)
    expect(s.selectedIds.size).toBe(0)
    expect(s.focusId).toBe(7)
  })
})

describe('setFocusId', () => {
  it('sets focus without touching selection', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = setFocusId(s, 5)
    expect(s.focusId).toBe(5)
    expect(ids(s.selectedIds)).toEqual([1, 2])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/lib/__tests__/brushing.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/lib/brushing.ts
/**
 * Brushing state machine — port of src/flake_analysis/ui/_brushing.py.
 *
 * Selected ids are tracked as a Set<number>; history is a stack of prior
 * Sets so undo is O(1). Redo stack is cleared whenever a new applyLasso /
 * clearBrush mutates state (standard editor semantics).
 */

export type LassoMode = 'replace' | 'add' | 'remove'

export interface BrushingState {
  selectedIds: Set<number>
  focusId: number | null
  history: Array<Set<number>>  // prior selectedIds (oldest -> newest)
  redoStack: Array<Set<number>>
}

export function emptyBrushing(): BrushingState {
  return {
    selectedIds: new Set(),
    focusId: null,
    history: [],
    redoStack: [],
  }
}

function pushHistory(s: BrushingState, prior: Set<number>): BrushingState {
  return {
    ...s,
    history: [...s.history, prior],
    redoStack: [],
  }
}

export function applyLasso(
  s: BrushingState,
  ids: number[],
  mode: LassoMode
): BrushingState {
  const prior = new Set(s.selectedIds)
  let next: Set<number>
  if (mode === 'replace') {
    next = new Set(ids)
  } else if (mode === 'add') {
    next = new Set(prior)
    for (const id of ids) next.add(id)
  } else {
    next = new Set(prior)
    for (const id of ids) next.delete(id)
  }
  const withHist = pushHistory(s, prior)
  return { ...withHist, selectedIds: next }
}

export function undo(s: BrushingState): BrushingState {
  if (s.history.length === 0) return s
  const prior = s.history[s.history.length - 1]
  const newHistory = s.history.slice(0, -1)
  const current = new Set(s.selectedIds)
  return {
    ...s,
    selectedIds: prior,
    history: newHistory,
    redoStack: [...s.redoStack, current],
  }
}

export function redo(s: BrushingState): BrushingState {
  if (s.redoStack.length === 0) return s
  const next = s.redoStack[s.redoStack.length - 1]
  const newRedo = s.redoStack.slice(0, -1)
  const current = new Set(s.selectedIds)
  return {
    ...s,
    selectedIds: next,
    history: [...s.history, current],
    redoStack: newRedo,
  }
}

export function clearBrush(s: BrushingState): BrushingState {
  const prior = new Set(s.selectedIds)
  const withHist = pushHistory(s, prior)
  return { ...withHist, selectedIds: new Set() }
}

export function setFocusId(s: BrushingState, focusId: number | null): BrushingState {
  return { ...s, focusId }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/lib/__tests__/brushing.test.ts`
Expected: PASS — 9 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/brushing.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/brushing.test.ts
git commit -m "feat(web): port _brushing.py to brushing.ts"
```

#### Task 14: Focus precedence helper

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/focus.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/focus.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/lib/__tests__/focus.test.ts
import { describe, expect, it } from 'vitest'
import { pickFocusDomainId } from '@/lib/focus'
import { emptyBrushing, applyLasso, setFocusId } from '@/lib/brushing'

describe('pickFocusDomainId — ports tab_selector.py:695-708', () => {
  it('returns explicit focus_id when set (priority 1)', () => {
    let s = emptyBrushing()
    s = applyLasso(s, [3, 4, 5], 'replace')
    s = setFocusId(s, 99)
    expect(pickFocusDomainId(s)).toBe(99)
  })

  it('returns min(selectedIds) when no explicit focus (priority 2)', () => {
    let s = emptyBrushing()
    s = applyLasso(s, [3, 4, 5], 'replace')
    expect(pickFocusDomainId(s)).toBe(3)
  })

  it('returns null when neither focus nor selection (priority 3)', () => {
    expect(pickFocusDomainId(emptyBrushing())).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/lib/__tests__/focus.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/lib/focus.ts
/**
 * Focus precedence — port of tab_selector.py:695-708.
 *   1. explicit focusId (row click)
 *   2. min(selectedIds) (lasso fallback)
 *   3. null
 */
import type { BrushingState } from '@/lib/brushing'

export function pickFocusDomainId(s: BrushingState): number | null {
  if (s.focusId !== null && s.focusId !== undefined) {
    return s.focusId
  }
  if (s.selectedIds.size === 0) {
    return null
  }
  let min = Infinity
  for (const id of s.selectedIds) if (id < min) min = id
  return min === Infinity ? null : min
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/lib/__tests__/focus.test.ts`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/focus.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/focus.test.ts
git commit -m "feat(web): add focus precedence helper"
```

#### Task 15: `selectorSlice` Zustand store

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/selectorSlice.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/__tests__/selectorSlice.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/state/__tests__/selectorSlice.test.ts
import { beforeEach, describe, expect, it } from 'vitest'
import { useSelectorStore } from '@/state/selectorSlice'

describe('selectorSlice', () => {
  beforeEach(() => {
    useSelectorStore.getState().resetFilter()
    useSelectorStore.setState({
      axisX: 'std_r',
      axisY: 'std_g',
      show3D: false,
      brushing: {
        selectedIds: new Set(),
        focusId: null,
        history: [],
        redoStack: [],
      },
      focusDomainId: null,
    })
  })

  it('default filter ranges match metricDefs', () => {
    const s = useSelectorStore.getState()
    expect(s.filter.area).toEqual([0, 1_000_000])
    expect(s.filter.std_r).toEqual([0, 100])
    expect(s.filter.sam2).toEqual([0, 1])
  })

  it('setFilter updates one metric', () => {
    useSelectorStore.getState().setFilter('area', [10, 50_000])
    expect(useSelectorStore.getState().filter.area).toEqual([10, 50_000])
  })

  it('resetFilter restores defaults', () => {
    useSelectorStore.getState().setFilter('area', [10, 50_000])
    useSelectorStore.getState().resetFilter()
    expect(useSelectorStore.getState().filter.area).toEqual([0, 1_000_000])
  })

  it('setAxis updates X or Y independently', () => {
    useSelectorStore.getState().setAxis('X', 'area')
    useSelectorStore.getState().setAxis('Y', 'sam2')
    const s = useSelectorStore.getState()
    expect(s.axisX).toBe('area')
    expect(s.axisY).toBe('sam2')
  })

  it('toUrlParams produces SelectorParams payload (None=null when range == default)', () => {
    useSelectorStore.getState().setFilter('area', [10, 500])
    const p = useSelectorStore.getState().toApiParams()
    expect(p.area_min).toBe(10)
    expect(p.area_max).toBe(500)
    // Untouched metrics must serialize to null/null (= unbounded on backend)
    expect(p.std_r_min).toBeNull()
    expect(p.std_r_max).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/state/__tests__/selectorSlice.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/state/selectorSlice.ts
/**
 * selectorSlice — frontend design §3.3.
 *
 * Single Zustand store (RHF holds live slider values; values commit to this
 * store on blur or 200ms debounce). Persistence happens only on Commit
 * (US-S7), so this slice never touches localStorage / server.
 */
import { create } from 'zustand'
import { defaultRange, METRIC_DEFS, type MetricKey } from '@/lib/metricDefs'
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

export type AvailableAxis = MetricKey | 'R' | 'G' | 'B'

export type FilterRanges = Record<MetricKey, [number, number]>

export interface SelectorApiParams {
  area_min: number | null; area_max: number | null
  std_r_min: number | null; std_r_max: number | null
  std_g_min: number | null; std_g_max: number | null
  std_b_min: number | null; std_b_max: number | null
  sam2_min: number | null; sam2_max: number | null
}

export interface SelectorState {
  filter: FilterRanges
  axisX: AvailableAxis
  axisY: AvailableAxis
  show3D: boolean
  brushing: BrushingState
  focusDomainId: number | null

  setFilter(key: MetricKey, range: [number, number]): void
  resetFilter(): void
  setAxis(pane: 'X' | 'Y', value: AvailableAxis): void
  setShow3D(v: boolean): void

  applyLasso(ids: number[], mode: LassoMode): void
  undoBrush(): void
  redoBrush(): void
  clearBrush(): void
  setFocusId(id: number | null): void

  toApiParams(): SelectorApiParams
}

function buildDefaultFilter(): FilterRanges {
  return {
    area: defaultRange('area'),
    std_r: defaultRange('std_r'),
    std_g: defaultRange('std_g'),
    std_b: defaultRange('std_b'),
    sam2: defaultRange('sam2'),
  }
}

function rangeIsDefault(key: MetricKey, range: [number, number]): boolean {
  const [lo, hi] = defaultRange(key)
  return range[0] === lo && range[1] === hi
}

export const useSelectorStore = create<SelectorState>((set, get) => ({
  filter: buildDefaultFilter(),
  axisX: 'std_r',
  axisY: 'std_g',
  show3D: false,
  brushing: emptyBrushing(),
  focusDomainId: null,

  setFilter(key, range) {
    set((s) => ({ filter: { ...s.filter, [key]: range } }))
  },
  resetFilter() {
    set({ filter: buildDefaultFilter() })
  },
  setAxis(pane, value) {
    set(pane === 'X' ? { axisX: value } : { axisY: value })
  },
  setShow3D(v) {
    set({ show3D: v })
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
    set((s) => ({ brushing: setFocusIdFn(s.brushing, id), focusDomainId: id }))
  },

  toApiParams() {
    const f = get().filter
    const out: SelectorApiParams = {
      area_min: null, area_max: null,
      std_r_min: null, std_r_max: null,
      std_g_min: null, std_g_max: null,
      std_b_min: null, std_b_max: null,
      sam2_min: null, sam2_max: null,
    }
    for (const def of METRIC_DEFS) {
      const range = f[def.key]
      if (rangeIsDefault(def.key, range)) continue
      ;(out as any)[`${def.key}_min`] = range[0]
      ;(out as any)[`${def.key}_max`] = range[1]
    }
    return out
  },
}))
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/state/__tests__/selectorSlice.test.ts`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/state/selectorSlice.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/state/__tests__/selectorSlice.test.ts
git commit -m "feat(web): add selectorSlice (Zustand) per design §3.3"
```

#### Task 16: Extend `useStepProgress` to expose `result`

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useStepProgress.ts`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useStepProgress.test.ts`

- [ ] **Step 1: Write the failing test (extend existing file)**

Add to the bottom of `web/src/hooks/__tests__/useStepProgress.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useStepProgress } from '@/hooks/useStepProgress'

describe('useStepProgress.result', () => {
  it('exposes the done event payload', async () => {
    const sseBody = [
      'event: progress\ndata: {"type":"progress","pct":0.5,"msg":"halfway"}\n\n',
      'event: done\ndata: {"type":"done","result":{"selected_count":7,"total_count":12}}\n\n',
    ].join('')

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(sseBody))
        controller.close()
      },
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
        })
      )
    )

    const { result } = renderHook(() => useStepProgress('local', 'selector'))
    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result).toEqual({ selected_count: 7, total_count: 12 })
  })

  it('result is null until done event arrives', () => {
    const { result } = renderHook(() => useStepProgress('local', 'selector'))
    expect(result.current.result).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/hooks/__tests__/useStepProgress.test.ts`
Expected: FAIL — `result.current.result` is `undefined`.

- [ ] **Step 3: Modify the hook**

Replace `web/src/hooks/useStepProgress.ts` body:

```ts
// web/src/hooks/useStepProgress.ts
/**
 * useStepProgress hook per integrated design §6 (extended for Plan 2 to
 * surface the 'done' event's result payload).
 */
import { useState, useCallback, useRef } from 'react'
import { parseEventStream } from '@/lib/sse'

type StepStatus = 'idle' | 'running' | 'done' | 'error'

export function useStepProgress<P = unknown, R = unknown>(
  projectId: string,
  step: string
) {
  const [status, setStatus] = useState<StepStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<R | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (params: P) => {
      abortControllerRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)

      try {
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/${step}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
            signal: abortControllerRef.current.signal,
          }
        )

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        for await (const event of parseEventStream(
          response,
          abortControllerRef.current.signal
        )) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as R | null)
            setStatus('done')
            setPct(1)
            break
          } else if (event.type === 'error') {
            setStatus('error')
            setMessage(event.data.error?.message || 'Pipeline failed')
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
    [projectId, step]
  )

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { status, pct, message, result, start, cancel }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (in `web/`): `npx vitest run src/hooks/__tests__/useStepProgress.test.ts`
Expected: PASS — old + new tests.

Also re-run `web/src/components/__tests__/StepCard.test.tsx` to make sure StepCard still works (it ignores `result`):

Run: `npx vitest run src/components/__tests__/StepCard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useStepProgress.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useStepProgress.test.ts
git commit -m "feat(web): expose 'done' result on useStepProgress"
```

### Phase 5 — Frontend API client + TanStack Query hooks

#### Task 17: API wrapper for Selector endpoints

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/api/selector.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/api/__tests__/selector.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// web/src/api/__tests__/selector.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import {
  fetchDomainStats,
  fetchSelection,
  postCommit,
  buildPreviewUrl,
  buildExportUrl,
} from '@/api/selector'

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchDomainStats', () => {
  it('GETs /api/v1/projects/{pid}/data/domain_stats and parses JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          flake_ids: [1, 2],
          mean_r: [10, 20], mean_g: [30, 40], mean_b: [50, 60],
          std_r: [1, 2], std_g: [3, 4], std_b: [5, 6],
          areas: [100, 200],
          sam2: [0.1, 0.5],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const out = await fetchDomainStats('local')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/local/data/domain_stats',
      expect.any(Object)
    )
    expect(out.flake_ids).toEqual([1, 2])
    expect(out.sam2).toEqual([0.1, 0.5])
  })

  it('throws ApiError on non-2xx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'domain_stats_not_found', message: 'no npz' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    await expect(fetchDomainStats('local')).rejects.toThrow(/domain_stats_not_found/)
  })
})

describe('fetchSelection', () => {
  it('parses {domain_id, selected} columns', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ domain_id: [1, 2, 3], selected: [true, false, true] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    const out = await fetchSelection('local')
    expect(out.domain_id).toEqual([1, 2, 3])
    expect(out.selected).toEqual([true, false, true])
  })
})

describe('postCommit', () => {
  it('POSTs JSON body with params + lasso_ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          output_path: '/p/03_selector/selection.parquet',
          n_committed: 1,
          n_filter_accepted: 3,
          n_lasso: 2,
          total_count: 4,
          params_hash: 'sha256:zzz',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const out = await postCommit('local', { params: { area_min: 5 } as any, lasso_ids: [2, 3] })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/local/selector/commit',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      })
    )
    expect(out.n_committed).toBe(1)
  })
})

describe('buildPreviewUrl + buildExportUrl', () => {
  it('builds preview url with optional contour', () => {
    expect(buildPreviewUrl('local', 7, false))
      .toBe('/api/v1/projects/local/data/annotations/7/preview?with_contour=false')
    expect(buildPreviewUrl('local', 7, true))
      .toBe('/api/v1/projects/local/data/annotations/7/preview?with_contour=true')
  })
  it('builds export url', () => {
    expect(buildExportUrl('local', 'selected'))
      .toBe('/api/v1/projects/local/selector/export?mode=selected')
    expect(buildExportUrl('local', 'filtered'))
      .toBe('/api/v1/projects/local/selector/export?mode=filtered')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/api/__tests__/selector.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/api/selector.ts
/**
 * Typed fetch wrappers for the Selector endpoints.
 *
 * Pulls every error envelope into a thrown ApiError to keep TanStack Query's
 * onError / isError contract clean.
 */

import type { SelectorApiParams } from '@/state/selectorSlice'

export class ApiError extends Error {
  code: string
  details: unknown
  status: number
  constructor(status: number, code: string, message: string, details: unknown) {
    super(`[${code}] ${message}`)
    this.code = code
    this.details = details
    this.status = status
  }
}

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
      err.details ?? null
    )
  }
  return (await resp.json()) as T
}

export interface DomainStats {
  flake_ids: number[]
  mean_r: number[]
  mean_g: number[]
  mean_b: number[]
  std_r: number[]
  std_g: number[]
  std_b: number[]
  areas: number[]
  sam2?: number[]
}

export async function fetchDomainStats(projectId: string): Promise<DomainStats> {
  const resp = await fetch(`/api/v1/projects/${projectId}/data/domain_stats`, {
    headers: { Accept: 'application/json' },
  })
  return unwrap<DomainStats>(resp)
}

export interface SelectionRows {
  domain_id: number[]
  selected: boolean[]
}

export async function fetchSelection(projectId: string): Promise<SelectionRows> {
  const resp = await fetch(`/api/v1/projects/${projectId}/data/selector/selection`, {
    headers: { Accept: 'application/json' },
  })
  return unwrap<SelectionRows>(resp)
}

export interface CommitRequest {
  params: SelectorApiParams
  lasso_ids: number[] | null
}

export interface CommitSummary {
  output_path: string
  n_committed: number
  n_filter_accepted: number
  n_lasso: number
  total_count: number
  params_hash: string | null
}

export async function postCommit(
  projectId: string,
  body: CommitRequest
): Promise<CommitSummary> {
  const resp = await fetch(`/api/v1/projects/${projectId}/selector/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return unwrap<CommitSummary>(resp)
}

export function buildPreviewUrl(
  projectId: string,
  domainId: number,
  withContour: boolean
): string {
  return `/api/v1/projects/${projectId}/data/annotations/${domainId}/preview?with_contour=${withContour}`
}

export function buildExportUrl(
  projectId: string,
  mode: 'filtered' | 'selected'
): string {
  return `/api/v1/projects/${projectId}/selector/export?mode=${mode}`
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/api/__tests__/selector.test.ts`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/api/selector.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/api/__tests__/selector.test.ts
git commit -m "feat(web): add selector API wrappers"
```

#### Task 18: TanStack Query hooks (`useDomainStats`, `useSelectionRows`, `useAnnotationPreview`, `useSelectorCommit`)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useDomainStats.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useSelectionRows.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useAnnotationPreview.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useSelectorCommit.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useDomainStats.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useDomainStats.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { useDomainStats } from '@/hooks/useDomainStats'

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: import('react').ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => vi.unstubAllGlobals())

describe('useDomainStats', () => {
  it('fetches domain stats and exposes typed arrays', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2],
            mean_r: [10, 20], mean_g: [30, 40], mean_b: [50, 60],
            std_r: [1, 2], std_g: [3, 4], std_b: [5, 6],
            areas: [100, 200],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    const { result } = renderHook(() => useDomainStats('local'), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.flake_ids).toEqual([1, 2])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/hooks/__tests__/useDomainStats.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementations**

```ts
// web/src/hooks/useDomainStats.ts
import { useQuery } from '@tanstack/react-query'
import { fetchDomainStats, type DomainStats } from '@/api/selector'

export function useDomainStats(projectId: string) {
  return useQuery<DomainStats>({
    queryKey: ['domainStats', projectId],
    queryFn: () => fetchDomainStats(projectId),
    staleTime: Infinity,
  })
}
```

```ts
// web/src/hooks/useSelectionRows.ts
import { useQuery } from '@tanstack/react-query'
import { fetchSelection, type SelectionRows } from '@/api/selector'

export function useSelectionRows(projectId: string) {
  return useQuery<SelectionRows>({
    queryKey: ['selectionRows', projectId],
    queryFn: () => fetchSelection(projectId),
    staleTime: Infinity,
    retry: false,  // 404 is a normal "not committed yet" state
  })
}
```

```ts
// web/src/hooks/useAnnotationPreview.ts
import { buildPreviewUrl } from '@/api/selector'

/**
 * Returns the preview URL string. The browser handles caching via the standard
 * HTTP cache (set Cache-Control on the backend later if needed). No TanStack
 * needed — <img src=...> does the right thing here.
 */
export function useAnnotationPreview(
  projectId: string,
  domainId: number | null,
  withContour: boolean
): string | null {
  if (domainId === null) return null
  return buildPreviewUrl(projectId, domainId, withContour)
}
```

```ts
// web/src/hooks/useSelectorCommit.ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { postCommit, type CommitRequest, type CommitSummary } from '@/api/selector'

export function useSelectorCommit(projectId: string) {
  const qc = useQueryClient()
  return useMutation<CommitSummary, Error, CommitRequest>({
    mutationFn: (body) => postCommit(projectId, body),
    onSuccess: () => {
      // selection.parquet just changed; invalidate readers that depend on it.
      qc.invalidateQueries({ queryKey: ['selectionRows', projectId] })
      qc.invalidateQueries({ queryKey: ['manifest', projectId] })
    },
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/hooks/__tests__/useDomainStats.test.tsx`
Expected: PASS — 1 test.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useDomainStats.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useSelectionRows.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useAnnotationPreview.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useSelectorCommit.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useDomainStats.test.tsx
git commit -m "feat(web): add Selector data hooks (TanStack Query)"
```

### Phase 6 — Right-rail components

#### Task 19: `MetricRangeRow` (slider + 2 RHF number inputs, 200ms debounce)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/MetricRangeRow.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/MetricRangeRow.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/selector/__tests__/MetricRangeRow.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, act } from '@testing-library/react'
import { MetricRangeRow } from '@/components/selector/MetricRangeRow'

beforeEach(() => vi.useFakeTimers())

describe('MetricRangeRow', () => {
  it('renders label + min/max inputs with default values', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="area"
        value={[0, 1_000_000]}
        onChange={onChange}
      />
    )
    expect(screen.getByText(/Area \(px\)/i)).not.toBeNull()
    const minInput = screen.getByLabelText('area min') as HTMLInputElement
    expect(minInput.value).toBe('0')
  })

  it('debounces commit by 200ms', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="std_r"
        value={[0, 100]}
        onChange={onChange}
      />
    )
    const max = screen.getByLabelText('std_r max') as HTMLInputElement
    fireEvent.change(max, { target: { value: '50' } })
    // before debounce fires
    expect(onChange).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(200) })
    expect(onChange).toHaveBeenCalledWith([0, 50])
  })

  it('swaps min/max if user enters min > max', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="area"
        value={[0, 100]}
        onChange={onChange}
      />
    )
    const min = screen.getByLabelText('area min') as HTMLInputElement
    fireEvent.change(min, { target: { value: '500' } })
    act(() => { vi.advanceTimersByTime(200) })
    // ports tab_selector.py:181-184 — swap so committed range is [100, 500]
    expect(onChange).toHaveBeenCalledWith([100, 500])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/MetricRangeRow.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/selector/MetricRangeRow.tsx
/**
 * One row in the filter drawer: label + slider + min/max number inputs.
 * RHF holds the working values; we commit (with 200ms debounce) to the
 * caller's onChange so Zustand updates only at rest.
 *
 * Ports the swap-on-cross behaviour from tab_selector.py:181-184.
 */
import { useEffect, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { METRIC_DEFS, type MetricKey } from '@/lib/metricDefs'

interface MetricRangeRowProps {
  metricKey: MetricKey
  value: [number, number]
  onChange(next: [number, number]): void
}

interface FormShape {
  min: number
  max: number
}

const DEBOUNCE_MS = 200

export function MetricRangeRow({ metricKey, value, onChange }: MetricRangeRowProps) {
  const def = METRIC_DEFS.find((d) => d.key === metricKey)!
  const { register, watch, setValue } = useForm<FormShape>({
    defaultValues: { min: value[0], max: value[1] },
  })

  // Sync external prop -> form when it changes from outside (e.g. resetFilter).
  useEffect(() => {
    setValue('min', value[0])
    setValue('max', value[1])
  }, [value, setValue])

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const min = watch('min')
  const max = watch('max')

  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      let lo = Number(min)
      let hi = Number(max)
      if (Number.isNaN(lo) || Number.isNaN(hi)) return
      if (lo > hi) {
        const swap = lo
        lo = hi
        hi = swap
      }
      // Clamp to def range
      lo = Math.max(def.lo, Math.min(def.hi, lo))
      hi = Math.max(def.lo, Math.min(def.hi, hi))
      onChange([lo, hi])
    }, DEBOUNCE_MS)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [min, max, def.lo, def.hi, onChange])

  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
        {def.label}
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          aria-label={`${metricKey} min`}
          type="number"
          step={def.step}
          min={def.lo}
          max={def.hi}
          {...register('min', { valueAsNumber: true })}
          style={{ width: '50%' }}
        />
        <input
          aria-label={`${metricKey} max`}
          type="number"
          step={def.step}
          min={def.lo}
          max={def.hi}
          {...register('max', { valueAsNumber: true })}
          style={{ width: '50%' }}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/MetricRangeRow.test.tsx`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/MetricRangeRow.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/MetricRangeRow.test.tsx
git commit -m "feat(web): add MetricRangeRow with debounced commit + swap"
```

#### Task 20: `FilterControls` + `AxisPicker` + `Live3DToggle`

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FilterControls.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/AxisPicker.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/Live3DToggle.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/AxisPicker.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/selector/__tests__/AxisPicker.test.tsx
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { AxisPicker } from '@/components/selector/AxisPicker'
import { useSelectorStore } from '@/state/selectorSlice'

describe('AxisPicker', () => {
  it('renders 8 radios (R, G, B, area, std_r, std_g, std_b, sam2)', () => {
    render(<AxisPicker pane="X" />)
    const radios = screen.getAllByRole('radio')
    expect(radios.length).toBe(8)
  })

  it('updates store when an axis is picked', () => {
    render(<AxisPicker pane="X" />)
    const areaRadio = screen.getByLabelText('X: area') as HTMLInputElement
    fireEvent.click(areaRadio)
    expect(useSelectorStore.getState().axisX).toBe('area')
  })

  it('Y pane writes to axisY', () => {
    render(<AxisPicker pane="Y" />)
    const sam2 = screen.getByLabelText('Y: sam2') as HTMLInputElement
    fireEvent.click(sam2)
    expect(useSelectorStore.getState().axisY).toBe('sam2')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/AxisPicker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementations**

```tsx
// web/src/components/selector/AxisPicker.tsx
import { useSelectorStore, type AvailableAxis } from '@/state/selectorSlice'

const AXES: AvailableAxis[] = ['R', 'G', 'B', 'area', 'std_r', 'std_g', 'std_b', 'sam2']

interface AxisPickerProps {
  pane: 'X' | 'Y'
}

export function AxisPicker({ pane }: AxisPickerProps) {
  const setAxis = useSelectorStore((s) => s.setAxis)
  const current = useSelectorStore((s) => (pane === 'X' ? s.axisX : s.axisY))
  const groupName = `axis-${pane}`
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

```tsx
// web/src/components/selector/FilterControls.tsx
import { METRIC_DEFS } from '@/lib/metricDefs'
import { useSelectorStore } from '@/state/selectorSlice'
import { MetricRangeRow } from './MetricRangeRow'

export function FilterControls() {
  const filter = useSelectorStore((s) => s.filter)
  const setFilter = useSelectorStore((s) => s.setFilter)
  const resetFilter = useSelectorStore((s) => s.resetFilter)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <strong>Filter</strong>
        <button onClick={resetFilter}>Reset</button>
      </div>
      {METRIC_DEFS.map((def) => (
        <MetricRangeRow
          key={def.key}
          metricKey={def.key}
          value={filter[def.key]}
          onChange={(next) => setFilter(def.key, next)}
        />
      ))}
    </div>
  )
}
```

```tsx
// web/src/components/selector/Live3DToggle.tsx
import { useSelectorStore } from '@/state/selectorSlice'

export function Live3DToggle() {
  const show3D = useSelectorStore((s) => s.show3D)
  const setShow3D = useSelectorStore((s) => s.setShow3D)
  return (
    <label style={{ display: 'flex', gap: 6, alignItems: 'center', margin: '8px 0' }}>
      <input
        type="checkbox"
        checked={show3D}
        onChange={(e) => setShow3D(e.target.checked)}
      />
      <span>Live 3D RGB scatter</span>
    </label>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/AxisPicker.test.tsx`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FilterControls.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/AxisPicker.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/Live3DToggle.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/AxisPicker.test.tsx
git commit -m "feat(web): add FilterControls, AxisPicker, Live3DToggle"
```

#### Task 21: `BrushingControls` (mode + undo/redo/clear)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/BrushingControls.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/BrushingControls.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/selector/__tests__/BrushingControls.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen, renderHook, act } from '@testing-library/react'
import { BrushingControls } from '@/components/selector/BrushingControls'
import { useSelectorStore } from '@/state/selectorSlice'
import { useBrushModeStore } from '@/components/selector/BrushingControls'

beforeEach(() => {
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
  useBrushModeStore.setState({ mode: 'replace' })
})

describe('BrushingControls', () => {
  it('switches the lasso mode when a button is clicked', () => {
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Add/ }))
    expect(useBrushModeStore.getState().mode).toBe('add')
  })

  it('undo button calls store.undoBrush', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('clear button empties selection', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/BrushingControls.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/selector/BrushingControls.tsx
/**
 * Lasso mode + undo/redo/clear buttons.
 *
 * Mode is held in a tiny local Zustand store rather than the SelectorSlice
 * because it's transient interaction state — the mode never affects
 * what we commit. ScatterCanvas reads the mode when it sends a lasso event.
 */
import { create } from 'zustand'
import type { LassoMode } from '@/lib/brushing'
import { useSelectorStore } from '@/state/selectorSlice'

interface BrushModeState {
  mode: LassoMode
  setMode(m: LassoMode): void
}

export const useBrushModeStore = create<BrushModeState>((set) => ({
  mode: 'replace',
  setMode(mode) {
    set({ mode })
  },
}))

const BUTTONS: Array<{ mode: LassoMode; label: string; title: string }> = [
  { mode: 'replace', label: 'Replace (R)', title: 'Replace selection with lassoed ids' },
  { mode: 'add', label: 'Add (A)', title: 'Union lassoed ids into selection' },
  { mode: 'remove', label: 'Remove (D)', title: 'Subtract lassoed ids from selection' },
]

export function BrushingControls() {
  const mode = useBrushModeStore((s) => s.mode)
  const setMode = useBrushModeStore((s) => s.setMode)
  const undo = useSelectorStore((s) => s.undoBrush)
  const redo = useSelectorStore((s) => s.redoBrush)
  const clear = useSelectorStore((s) => s.clearBrush)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, margin: '8px 0' }}>
      <div style={{ display: 'flex', gap: 4 }}>
        {BUTTONS.map((b) => (
          <button
            key={b.mode}
            title={b.title}
            onClick={() => setMode(b.mode)}
            aria-pressed={mode === b.mode}
            style={{
              fontWeight: mode === b.mode ? 700 : 400,
              border: mode === b.mode ? '2px solid #2563eb' : '1px solid #ccc',
            }}
          >
            {b.label}
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 4 }}>
        <button onClick={undo}>Undo</button>
        <button onClick={redo}>Redo</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/BrushingControls.test.tsx`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/BrushingControls.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/BrushingControls.test.tsx
git commit -m "feat(web): add BrushingControls (mode + undo/redo/clear)"
```

#### Task 22: `LiveCounters` + `CommitButton`

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/LiveCounters.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/CommitButton.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/applyFilter.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/LiveCounters.test.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/CommitButton.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// web/src/components/selector/__tests__/LiveCounters.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LiveCounters } from '@/components/selector/LiveCounters'
import { useSelectorStore } from '@/state/selectorSlice'

beforeEach(() => {
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
})

describe('LiveCounters', () => {
  it('renders 4 counters from given stats + selection', () => {
    const stats = {
      flake_ids: [1, 2, 3, 4, 5],
      mean_r: [10, 20, 30, 40, 50],
      mean_g: [10, 20, 30, 40, 50],
      mean_b: [10, 20, 30, 40, 50],
      std_r: [5, 5, 5, 5, 5],
      std_g: [5, 5, 5, 5, 5],
      std_b: [5, 5, 5, 5, 5],
      areas: [10, 20, 30, 40, 50],
    }
    useSelectorStore.getState().setFilter('area', [15, 45])
    useSelectorStore.getState().applyLasso([2, 3], 'replace')

    render(<LiveCounters stats={stats} />)
    // accepted = ids in [15..45] for areas = {2, 3, 4} = 3
    expect(screen.getByTestId('counter-accepted').textContent).toContain('3')
    // rejected = 5 - 3 = 2
    expect(screen.getByTestId('counter-rejected').textContent).toContain('2')
    // selected (lassoed) = 2
    expect(screen.getByTestId('counter-selected').textContent).toContain('2')
    // will commit = accepted ∩ lasso = {2, 3} = 2
    expect(screen.getByTestId('counter-will-commit').textContent).toContain('2')
  })

  it('will-commit equals accepted when lasso is empty (filter-only commit)', () => {
    const stats = {
      flake_ids: [1, 2, 3],
      mean_r: [0, 0, 0], mean_g: [0, 0, 0], mean_b: [0, 0, 0],
      std_r: [0, 0, 0], std_g: [0, 0, 0], std_b: [0, 0, 0],
      areas: [10, 20, 30],
    }
    useSelectorStore.getState().setFilter('area', [15, 25])
    render(<LiveCounters stats={stats} />)
    expect(screen.getByTestId('counter-accepted').textContent).toContain('1')
    expect(screen.getByTestId('counter-will-commit').textContent).toContain('1')
  })
})
```

```tsx
// web/src/components/selector/__tests__/CommitButton.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CommitButton } from '@/components/selector/CommitButton'

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('CommitButton', () => {
  it('POSTs to /selector/commit and shows summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            output_path: '/p/03_selector/selection.parquet',
            n_committed: 5,
            n_filter_accepted: 7,
            n_lasso: 0,
            total_count: 10,
            params_hash: 'sha256:abc',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<CommitButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Commit/ }))
    await waitFor(() => expect(screen.getByTestId('commit-summary')).not.toBeNull())
    expect(screen.getByTestId('commit-summary').textContent).toContain('5')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `web/`):
```
npx vitest run src/components/selector/__tests__/LiveCounters.test.tsx
npx vitest run src/components/selector/__tests__/CommitButton.test.tsx
```
Expected: FAIL — modules not found.

- [ ] **Step 3: Write minimal implementations**

```ts
// web/src/lib/applyFilter.ts
import type { DomainStats } from '@/api/selector'
import type { FilterRanges } from '@/state/selectorSlice'

/**
 * Returns the set of flake_ids that pass all 5 metric ranges.
 * sam2 is treated as "no constraint" when the column is absent (matches
 * pipeline/selector.py:135-149 allow_missing semantics).
 */
export function computeAccepted(
  stats: DomainStats,
  filter: FilterRanges
): Set<number> {
  const out = new Set<number>()
  const n = stats.flake_ids.length
  const [aLo, aHi] = filter.area
  const [srLo, srHi] = filter.std_r
  const [sgLo, sgHi] = filter.std_g
  const [sbLo, sbHi] = filter.std_b
  const [s2Lo, s2Hi] = filter.sam2
  const sam2 = stats.sam2

  for (let i = 0; i < n; i++) {
    const a = stats.areas[i]
    if (a < aLo || a > aHi) continue
    if (stats.std_r[i] < srLo || stats.std_r[i] > srHi) continue
    if (stats.std_g[i] < sgLo || stats.std_g[i] > sgHi) continue
    if (stats.std_b[i] < sbLo || stats.std_b[i] > sbHi) continue
    if (sam2 !== undefined) {
      if (sam2[i] < s2Lo || sam2[i] > s2Hi) continue
    }
    out.add(stats.flake_ids[i])
  }
  return out
}
```

```tsx
// web/src/components/selector/LiveCounters.tsx
import { useMemo } from 'react'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'
import type { DomainStats } from '@/api/selector'

interface LiveCountersProps {
  stats: DomainStats
}

export function LiveCounters({ stats }: LiveCountersProps) {
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const counts = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const total = stats.flake_ids.length
    const selectedCount = selectedIds.size
    let willCommit = 0
    if (selectedCount === 0) {
      willCommit = accepted.size
    } else {
      for (const id of selectedIds) if (accepted.has(id)) willCommit++
    }
    return {
      accepted: accepted.size,
      rejected: total - accepted.size,
      selected: selectedCount,
      willCommit,
    }
  }, [stats, filter, selectedIds])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4, fontSize: 12 }}>
      <div data-testid="counter-accepted">Accepted: <strong>{counts.accepted}</strong></div>
      <div data-testid="counter-rejected">Rejected: <strong>{counts.rejected}</strong></div>
      <div data-testid="counter-selected">Selected: <strong>{counts.selected}</strong></div>
      <div data-testid="counter-will-commit">Will commit: <strong>{counts.willCommit}</strong></div>
    </div>
  )
}
```

```tsx
// web/src/components/selector/CommitButton.tsx
import { useSelectorStore } from '@/state/selectorSlice'
import { useSelectorCommit } from '@/hooks/useSelectorCommit'

interface CommitButtonProps {
  projectId: string
}

export function CommitButton({ projectId }: CommitButtonProps) {
  const toApiParams = useSelectorStore((s) => s.toApiParams)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)
  const mutation = useSelectorCommit(projectId)

  const onClick = () => {
    const lasso = selectedIds.size > 0 ? Array.from(selectedIds) : null
    mutation.mutate({ params: toApiParams(), lasso_ids: lasso })
  }

  return (
    <div>
      <button
        onClick={onClick}
        disabled={mutation.isPending}
        style={{ background: '#16a34a', color: 'white', padding: '6px 12px' }}
      >
        {mutation.isPending ? 'Committing...' : 'Commit selection'}
      </button>
      {mutation.data && (
        <div data-testid="commit-summary" style={{ marginTop: 4, fontSize: 12 }}>
          Committed {mutation.data.n_committed} / {mutation.data.total_count} domains
        </div>
      )}
      {mutation.isError && (
        <div role="alert" style={{ color: 'red', marginTop: 4 }}>
          {mutation.error?.message}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (in `web/`):
```
npx vitest run src/components/selector/__tests__/LiveCounters.test.tsx
npx vitest run src/components/selector/__tests__/CommitButton.test.tsx
```
Expected: PASS — 3 + 1 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/LiveCounters.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/CommitButton.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/applyFilter.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/LiveCounters.test.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/CommitButton.test.tsx
git commit -m "feat(web): add LiveCounters + CommitButton + applyFilter helper"
```

### Phase 7 — Main panel (Scatter + Image preview + Flake table)

#### Task 23: `usePanZoom` hook + `RawImagePreview` (native `<img>`, Q-U3)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/usePanZoom.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/RawImagePreview.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/RawImagePreview.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/selector/__tests__/RawImagePreview.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { RawImagePreview } from '@/components/selector/RawImagePreview'

describe('RawImagePreview (Q-U3 — native <img>, NOT OpenSeadragon)', () => {
  it('renders nothing when domainId is null', () => {
    const { container } = render(<RawImagePreview projectId="local" domainId={null} />)
    expect(container.querySelector('img')).toBeNull()
  })

  it('renders an <img> with the preview URL when domainId is set', () => {
    render(<RawImagePreview projectId="local" domainId={7} />)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('/api/v1/projects/local/data/annotations/7/preview')
    expect(img.src).toContain('with_contour=false')
  })

  it('toggles contour overlay via the boundary toggle', () => {
    render(<RawImagePreview projectId="local" domainId={7} />)
    const toggle = screen.getByRole('checkbox', { name: /Show boundary/ })
    fireEvent.click(toggle)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('with_contour=true')
  })

  it('wheel event scales the image', () => {
    const { container } = render(<RawImagePreview projectId="local" domainId={7} />)
    const wrapper = container.querySelector('[data-testid="panzoom-wrapper"]') as HTMLElement
    fireEvent.wheel(wrapper, { deltaY: -100, ctrlKey: false })
    const img = container.querySelector('img') as HTMLImageElement
    // After wheel zoom in, transform style should include scale > 1
    expect(img.style.transform).toMatch(/scale\([12]\.\d/)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/RawImagePreview.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementations**

```ts
// web/src/lib/usePanZoom.ts
/**
 * Lightweight pan/zoom hook for a fixed-size <img> inside a relatively-positioned
 * wrapper. NOT OpenSeadragon (per Q-U3).
 *
 * Returns:
 *   wrapperProps — onWheel + onMouseDown + onMouseMove + onMouseUp.
 *   imgStyle     — { transform, cursor }.
 *   reset        — function that returns to scale=1, translate=0.
 */
import { useCallback, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent as ReactMouseEvent, WheelEvent as ReactWheelEvent } from 'react'

interface State {
  scale: number
  tx: number
  ty: number
}

export function usePanZoom() {
  const [state, setState] = useState<State>({ scale: 1, tx: 0, ty: 0 })
  const dragging = useRef(false)
  const last = useRef({ x: 0, y: 0 })

  const onWheel = useCallback((e: ReactWheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY < 0 ? 1.1 : 1 / 1.1
    setState((s) => ({ ...s, scale: Math.max(0.25, Math.min(8, s.scale * delta)) }))
  }, [])

  const onMouseDown = useCallback((e: ReactMouseEvent) => {
    dragging.current = true
    last.current = { x: e.clientX, y: e.clientY }
  }, [])

  const onMouseMove = useCallback((e: ReactMouseEvent) => {
    if (!dragging.current) return
    const dx = e.clientX - last.current.x
    const dy = e.clientY - last.current.y
    last.current = { x: e.clientX, y: e.clientY }
    setState((s) => ({ ...s, tx: s.tx + dx, ty: s.ty + dy }))
  }, [])

  const onMouseUp = useCallback(() => {
    dragging.current = false
  }, [])

  const reset = useCallback(() => {
    setState({ scale: 1, tx: 0, ty: 0 })
  }, [])

  return {
    wrapperProps: { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave: onMouseUp },
    imgStyle: {
      transform: `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`,
      cursor: dragging.current ? 'grabbing' : 'grab',
      transformOrigin: '0 0',
    } as CSSProperties,
    reset,
    state,
  }
}
```

```tsx
// web/src/components/selector/RawImagePreview.tsx
import { useState } from 'react'
import { usePanZoom } from '@/lib/usePanZoom'
import { useAnnotationPreview } from '@/hooks/useAnnotationPreview'

interface RawImagePreviewProps {
  projectId: string
  domainId: number | null
}

export function RawImagePreview({ projectId, domainId }: RawImagePreviewProps) {
  const [withContour, setWithContour] = useState(false)
  const url = useAnnotationPreview(projectId, domainId, withContour)
  const { wrapperProps, imgStyle, reset } = usePanZoom()

  if (!url) {
    return (
      <div style={{ padding: 16, color: '#888', fontStyle: 'italic' }}>
        Click a point or row to preview a domain.
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <label style={{ fontSize: 12 }}>
          <input
            type="checkbox"
            checked={withContour}
            onChange={(e) => setWithContour(e.target.checked)}
          />{' '}
          Show boundary
        </label>
        <button onClick={reset} aria-label="Reset zoom">Reset</button>
      </div>
      <div
        data-testid="panzoom-wrapper"
        {...wrapperProps}
        style={{
          width: '100%',
          height: 320,
          overflow: 'hidden',
          background: '#111',
          position: 'relative',
        }}
      >
        <img
          src={url}
          alt={`Domain ${domainId}`}
          style={imgStyle}
          draggable={false}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/RawImagePreview.test.tsx`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/usePanZoom.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/RawImagePreview.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/RawImagePreview.test.tsx
git commit -m "feat(web): add usePanZoom + RawImagePreview (native <img>, Q-U3)"
```

#### Task 24: `ScatterCanvas` (Plotly Scattergl + lasso/click)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ScatterCanvas.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/downsample.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/downsample.test.ts`

- [ ] **Step 1: Write the failing test (downsample helper only — Plotly is mocked at the page level later)**

```ts
// web/src/lib/__tests__/downsample.test.ts
import { describe, expect, it } from 'vitest'
import { downsampleIndices } from '@/lib/downsample'

describe('downsampleIndices', () => {
  it('returns all indices when n <= cap', () => {
    const idx = downsampleIndices(10, [], 5000)
    expect(idx.length).toBe(10)
    expect(idx[0]).toBe(0)
    expect(idx[9]).toBe(9)
  })

  it('caps at "cap" but unions must-include indices', () => {
    const flakeIds = Array.from({ length: 10000 }, (_, i) => i + 1)
    const mustInclude = new Set([5, 9999])
    const idx = downsampleIndices(10000, flakeIds, 5000, mustInclude)
    // result length is at most 5000 (we trim) but must contain index of id=5 (i=4) and id=9999 (i=9998)
    expect(idx.length).toBeLessThanOrEqual(5000)
    expect(idx.includes(4)).toBe(true)
    expect(idx.includes(9998)).toBe(true)
  })

  it('is deterministic given the same seed', () => {
    const a = downsampleIndices(10000, [], 100)
    const b = downsampleIndices(10000, [], 100)
    expect(a).toEqual(b)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/lib/__tests__/downsample.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementations**

```ts
// web/src/lib/downsample.ts
/**
 * Port of tab_selector.py:299-322 — keep at most ``cap`` indices but always
 * include any indices whose flake_id appears in ``mustIncludeIds``.
 *
 * Uses a fixed-seed mulberry32 PRNG so ScatterCanvas re-renders return the
 * same pinned subset across renders (no flicker on filter changes).
 */
export function downsampleIndices(
  n: number,
  flakeIds: number[],
  cap: number,
  mustIncludeIds?: Set<number>
): number[] {
  if (n <= cap) {
    const all = new Array<number>(n)
    for (let i = 0; i < n; i++) all[i] = i
    return all
  }
  const rng = mulberry32(0)
  const picked = new Set<number>()
  while (picked.size < cap) {
    picked.add(Math.floor(rng() * n))
  }
  if (mustIncludeIds && mustIncludeIds.size > 0 && flakeIds.length === n) {
    for (let i = 0; i < n; i++) {
      if (mustIncludeIds.has(flakeIds[i])) picked.add(i)
    }
  }
  return Array.from(picked).sort((a, b) => a - b).slice(0, Math.max(cap, mustIncludeIds?.size ?? 0))
}

function mulberry32(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6D2B79F5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
```

```tsx
// web/src/components/selector/ScatterCanvas.tsx
/**
 * Plotly Scattergl with lasso + click events.
 * Imports react-plotly.js statically because the wrapping <SelectorTab/> is
 * itself lazy-loaded (Task 25). That gives one chunk for the whole tab.
 */
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore, type AvailableAxis } from '@/state/selectorSlice'
import { useBrushModeStore } from './BrushingControls'
import { downsampleIndices } from '@/lib/downsample'
import { computeAccepted } from '@/lib/applyFilter'

interface ScatterCanvasProps {
  stats: DomainStats
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

export function ScatterCanvas({ stats }: ScatterCanvasProps) {
  const axisX = useSelectorStore((s) => s.axisX)
  const axisY = useSelectorStore((s) => s.axisY)
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)
  const applyLasso = useSelectorStore((s) => s.applyLasso)
  const setFocusId = useSelectorStore((s) => s.setFocusId)
  const mode = useBrushModeStore((s) => s.mode)

  const { data, layout } = useMemo(() => {
    const n = stats.flake_ids.length
    const idxs = downsampleIndices(n, stats.flake_ids, MAX_POINTS, selectedIds)
    const accepted = computeAccepted(stats, filter)
    const xCol = pickColumn(stats, axisX)
    const yCol = pickColumn(stats, axisY)

    const x = idxs.map((i) => xCol[i])
    const y = idxs.map((i) => yCol[i])
    const ids = idxs.map((i) => stats.flake_ids[i])
    const colors = ids.map((id) => {
      if (selectedIds.has(id)) return '#dc2626'  // selected = red
      if (accepted.has(id)) return '#2563eb'     // accepted = blue
      return '#9ca3af'                            // rejected = grey
    })

    return {
      data: [
        {
          type: 'scattergl' as const,
          mode: 'markers' as const,
          x,
          y,
          customdata: ids,
          marker: { size: 5, color: colors },
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
  }, [stats, axisX, axisY, filter, selectedIds])

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
        if (pt?.customdata !== undefined) {
          setFocusId(pt.customdata as number)
        }
      }}
    />
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/lib/__tests__/downsample.test.ts`
Expected: PASS — 3 tests.

(ScatterCanvas itself is exercised in the SelectorTab integration test via a Plotly mock — Task 25.)

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ScatterCanvas.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/downsample.ts /Users/houkjang/projects/stand-alone-analyzer/web/src/lib/__tests__/downsample.test.ts
git commit -m "feat(web): add ScatterCanvas (Plotly Scattergl) + downsample"
```

#### Task 25: `RGBScatter3DPanel` (display-only)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/RGBScatter3DPanel.tsx`

- [ ] **Step 1: No new test file** (rendered behind a `show3D` flag; covered by SelectorTab integration test in Task 28)

- [ ] **Step 2: Write the implementation**

```tsx
// web/src/components/selector/RGBScatter3DPanel.tsx
/**
 * Display-only 3D RGB scatter — no lasso events (US-S5 AC).
 * Reuses the same selectedIds colouring rules as ScatterCanvas.
 */
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'

interface Props {
  stats: DomainStats
}

export function RGBScatter3DPanel({ stats }: Props) {
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const { data, layout } = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const colors = stats.flake_ids.map((id) => {
      if (selectedIds.has(id)) return '#dc2626'
      if (accepted.has(id)) return '#2563eb'
      return '#9ca3af'
    })
    return {
      data: [
        {
          type: 'scatter3d' as const,
          mode: 'markers' as const,
          x: stats.mean_r,
          y: stats.mean_g,
          z: stats.mean_b,
          marker: { size: 2, color: colors },
        },
      ],
      layout: {
        scene: {
          xaxis: { title: { text: 'R' } },
          yaxis: { title: { text: 'G' } },
          zaxis: { title: { text: 'B' } },
        },
        margin: { t: 10, r: 10, b: 10, l: 10 },
      },
    }
  }, [stats, filter, selectedIds])

  return (
    <Plot
      data={data}
      layout={layout}
      style={{ width: '100%', height: 360 }}
      useResizeHandler
      // explicitly NO onSelected handler (US-S5 AC)
    />
  )
}
```

- [ ] **Step 3: Verify it typechecks**

Run (in `web/`): `npm run typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/RGBScatter3DPanel.tsx
git commit -m "feat(web): add display-only RGBScatter3DPanel"
```

#### Task 26: `FlakeTable` (react-window virtualization)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FlakeTable.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FlakeListAccordion.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/FlakeTable.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/selector/__tests__/FlakeTable.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { FlakeTable } from '@/components/selector/FlakeTable'
import { useSelectorStore } from '@/state/selectorSlice'

const stats = {
  flake_ids: [1, 2, 3],
  mean_r: [10, 20, 30],
  mean_g: [10, 20, 30],
  mean_b: [10, 20, 30],
  std_r: [1, 2, 3],
  std_g: [1, 2, 3],
  std_b: [1, 2, 3],
  areas: [100, 200, 300],
}

beforeEach(() => {
  useSelectorStore.getState().clearBrush()
})

describe('FlakeTable', () => {
  it('renders one row per accepted flake (default filter accepts all)', () => {
    render(<FlakeTable stats={stats} />)
    // header + 3 rows
    expect(screen.getAllByRole('row').length).toBe(4)
  })

  it('row click sets focusId on the store', () => {
    render(<FlakeTable stats={stats} />)
    fireEvent.click(screen.getByTestId('flake-row-2'))
    expect(useSelectorStore.getState().brushing.focusId).toBe(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/FlakeTable.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementations**

```tsx
// web/src/components/selector/FlakeTable.tsx
import { useMemo } from 'react'
import { FixedSizeList as List } from 'react-window'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'

interface FlakeTableProps {
  stats: DomainStats
}

const ROW_HEIGHT = 24

export function FlakeTable({ stats }: FlakeTableProps) {
  const filter = useSelectorStore((s) => s.filter)
  const setFocusId = useSelectorStore((s) => s.setFocusId)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const rows = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const out: Array<{ id: number; area: number; std_r: number; selected: boolean }> = []
    for (let i = 0; i < stats.flake_ids.length; i++) {
      const id = stats.flake_ids[i]
      if (!accepted.has(id)) continue
      out.push({
        id,
        area: stats.areas[i],
        std_r: stats.std_r[i],
        selected: selectedIds.has(id),
      })
    }
    return out
  }, [stats, filter, selectedIds])

  return (
    <div role="table">
      <div role="row" style={{ display: 'grid', gridTemplateColumns: '60px 80px 80px 60px', fontWeight: 600, padding: '4px 8px', borderBottom: '1px solid #ddd' }}>
        <span>id</span><span>area</span><span>std_r</span><span>sel</span>
      </div>
      <List
        height={Math.min(360, ROW_HEIGHT * rows.length || ROW_HEIGHT)}
        itemCount={rows.length}
        itemSize={ROW_HEIGHT}
        width="100%"
      >
        {({ index, style }) => {
          const r = rows[index]
          return (
            <div
              role="row"
              data-testid={`flake-row-${r.id}`}
              key={r.id}
              style={{ ...style, display: 'grid', gridTemplateColumns: '60px 80px 80px 60px', padding: '4px 8px', cursor: 'pointer', background: r.selected ? '#fee2e2' : 'transparent' }}
              onClick={() => setFocusId(r.id)}
            >
              <span>{r.id}</span>
              <span>{r.area}</span>
              <span>{r.std_r.toFixed(2)}</span>
              <span>{r.selected ? 'Y' : ''}</span>
            </div>
          )
        }}
      </List>
    </div>
  )
}
```

```tsx
// web/src/components/selector/FlakeListAccordion.tsx
import { useState } from 'react'
import type { DomainStats } from '@/api/selector'
import { FlakeTable } from './FlakeTable'

interface Props {
  stats: DomainStats
}

export function FlakeListAccordion({ stats }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} style={{ marginTop: 12 }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Flake list ({stats.flake_ids.length})</summary>
      <div style={{ marginTop: 8 }}>
        {open && <FlakeTable stats={stats} />}
      </div>
    </details>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/components/selector/__tests__/FlakeTable.test.tsx`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FlakeTable.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/FlakeListAccordion.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/__tests__/FlakeTable.test.tsx
git commit -m "feat(web): add virtualized FlakeTable + accordion"
```

### Phase 8 — Tab assembly + lazy route + integration test

#### Task 27: `SelectorRightRail`, `ScatterPanel`, `ImagePreviewPanel`, `SelectorMain`

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/SelectorRightRail.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ScatterPanel.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ImagePreviewPanel.tsx`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/SelectorMain.tsx`

- [ ] **Step 1: Write the implementations**

```tsx
// web/src/components/selector/SelectorRightRail.tsx
import type { DomainStats } from '@/api/selector'
import { FilterControls } from './FilterControls'
import { AxisPicker } from './AxisPicker'
import { BrushingControls } from './BrushingControls'
import { Live3DToggle } from './Live3DToggle'
import { LiveCounters } from './LiveCounters'
import { CommitButton } from './CommitButton'

interface Props {
  projectId: string
  stats: DomainStats
}

export function SelectorRightRail({ projectId, stats }: Props) {
  return (
    <aside style={{ width: 280, borderLeft: '1px solid #eee', padding: 12, overflow: 'auto' }}>
      <FilterControls />
      <AxisPicker pane="X" />
      <AxisPicker pane="Y" />
      <BrushingControls />
      <Live3DToggle />
      <LiveCounters stats={stats} />
      <div style={{ marginTop: 12 }}>
        <CommitButton projectId={projectId} />
      </div>
    </aside>
  )
}
```

```tsx
// web/src/components/selector/ScatterPanel.tsx
import type { DomainStats } from '@/api/selector'
import { ScatterCanvas } from './ScatterCanvas'

interface Props {
  stats: DomainStats
}

export function ScatterPanel({ stats }: Props) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <ScatterCanvas stats={stats} />
    </div>
  )
}
```

```tsx
// web/src/components/selector/ImagePreviewPanel.tsx
import { useSelectorStore } from '@/state/selectorSlice'
import { pickFocusDomainId } from '@/lib/focus'
import { RawImagePreview } from './RawImagePreview'

interface Props {
  projectId: string
}

export function ImagePreviewPanel({ projectId }: Props) {
  const focus = useSelectorStore((s) => pickFocusDomainId(s.brushing))
  return (
    <div style={{ width: 380 }}>
      <h4 style={{ margin: '0 0 6px 0' }}>Preview {focus !== null ? `(domain ${focus})` : ''}</h4>
      <RawImagePreview projectId={projectId} domainId={focus} />
    </div>
  )
}
```

```tsx
// web/src/components/selector/SelectorMain.tsx
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { ScatterPanel } from './ScatterPanel'
import { ImagePreviewPanel } from './ImagePreviewPanel'
import { RGBScatter3DPanel } from './RGBScatter3DPanel'

interface Props {
  projectId: string
  stats: DomainStats
}

export function SelectorMain({ projectId, stats }: Props) {
  const show3D = useSelectorStore((s) => s.show3D)
  return (
    <div style={{ flex: 1, padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 12, minHeight: 0 }}>
        <ScatterPanel stats={stats} />
        <ImagePreviewPanel projectId={projectId} />
      </div>
      {show3D && <RGBScatter3DPanel stats={stats} />}
    </div>
  )
}
```

- [ ] **Step 2: Verify it typechecks**

Run (in `web/`): `npm run typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/SelectorRightRail.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ScatterPanel.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/ImagePreviewPanel.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/components/selector/SelectorMain.tsx
git commit -m "feat(web): add SelectorMain + RightRail composition"
```

#### Task 28: `SelectorTab` page + integration test (Plotly mocked)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/pages/SelectorTab.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/pages/__tests__/SelectorTab.test.tsx`

- [ ] **Step 1: Write the failing integration test**

```tsx
// web/src/pages/__tests__/SelectorTab.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SelectorTab } from '@/pages/SelectorTab'
import { useSelectorStore } from '@/state/selectorSlice'

vi.mock('react-plotly.js', () => ({
  default: (_props: any) => <div data-testid="plotly-mock" />,
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
})

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('SelectorTab integration', () => {
  it('loads domain stats then renders rail + main', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2, 3],
            mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
            std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
            areas: [100, 200, 300],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Filter/)).not.toBeNull())
    expect(screen.getByTestId('plotly-mock')).not.toBeNull()
    expect(screen.getByText(/Commit selection/)).not.toBeNull()
  })

  it('shows error envelope when domain_stats is missing (404)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'domain_stats_not_found', message: 'Run Compute → Domain Stats first.' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByRole('alert')).not.toBeNull())
    expect(screen.getByRole('alert').textContent).toMatch(/Run Compute/)
  })

  it('row click in flake list updates focus + the preview <img>', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2, 3],
            mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
            std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
            areas: [100, 200, 300],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Flake list/)).not.toBeNull())
    // Open the accordion
    fireEvent.click(screen.getByText(/Flake list/))
    fireEvent.click(await screen.findByTestId('flake-row-2'))
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('/data/annotations/2/preview')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `web/`): `npx vitest run src/pages/__tests__/SelectorTab.test.tsx`
Expected: FAIL — `SelectorTab` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// web/src/pages/SelectorTab.tsx
import { useDomainStats } from '@/hooks/useDomainStats'
import { SelectorMain } from '@/components/selector/SelectorMain'
import { SelectorRightRail } from '@/components/selector/SelectorRightRail'
import { FlakeListAccordion } from '@/components/selector/FlakeListAccordion'
import { CommitButton } from '@/components/selector/CommitButton'

interface SelectorTabProps {
  projectId: string
}

export function SelectorTab({ projectId }: SelectorTabProps) {
  const { data, isLoading, error } = useDomainStats(projectId)

  if (isLoading) {
    return <div style={{ padding: 16 }}>Loading domain stats...</div>
  }
  if (error) {
    return (
      <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>
        {(error as Error).message}
      </div>
    )
  }
  if (!data) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <SelectorMain projectId={projectId} stats={data} />
        <SelectorRightRail projectId={projectId} stats={data} />
      </div>
      <FlakeListAccordion stats={data} />
      <div style={{ padding: 12, borderTop: '1px solid #eee' }}>
        {/* Body-level mirror of the right-rail commit per design §4.2 */}
        <CommitButton projectId={projectId} />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (in `web/`): `npx vitest run src/pages/__tests__/SelectorTab.test.tsx`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/pages/SelectorTab.tsx /Users/houkjang/projects/stand-alone-analyzer/web/src/pages/__tests__/SelectorTab.test.tsx
git commit -m "feat(web): add SelectorTab page + integration tests"
```

#### Task 29: Lazy-load `SelectorTab` from the App router

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/web/src/App.tsx`

- [ ] **Step 1: Read the current App.tsx**

Run (in `web/`): `cat src/App.tsx`
Note the existing route layout — the new `/selector` route slots beside the existing `/compute` route.

- [ ] **Step 2: Modify App.tsx to add the lazy route**

Replace the import block + routes section with:

```tsx
// web/src/App.tsx (relevant changes)
import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { ComputeTab } from '@/pages/ComputeTab'
import { AppShell } from '@/components/AppShell'

const SelectorTab = lazy(() =>
  import('@/pages/SelectorTab').then((m) => ({ default: m.SelectorTab }))
)

export function App() {
  const projectId = 'local'
  return (
    <BrowserRouter>
      <AppShell>
        <nav style={{ display: 'flex', gap: 12, padding: 12 }}>
          <NavLink to="/compute">Compute</NavLink>
          <NavLink to="/selector">Selector</NavLink>
        </nav>
        <Routes>
          <Route path="/compute" element={<ComputeTab projectId={projectId} />} />
          <Route
            path="/selector"
            element={
              <Suspense fallback={<div style={{ padding: 16 }}>Loading Selector tab...</div>}>
                <SelectorTab projectId={projectId} />
              </Suspense>
            }
          />
          <Route path="*" element={<ComputeTab projectId={projectId} />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
```

If the existing `App.tsx` has a different shape, preserve everything else and only:
1. Add the `lazy` + `Suspense` imports.
2. Add the `SelectorTab = lazy(...)` line.
3. Add the `<Route path="/selector" element={<Suspense fallback=...><SelectorTab projectId={projectId}/></Suspense>}/>` entry.
4. Add a NavLink to `/selector` next to existing nav links.

- [ ] **Step 3: Verify build splits the chunk**

Run (in `web/`): `npm run build`
Expected: build succeeds. Vite output should show a separate chunk for `SelectorTab` (look for `SelectorTab-*.js` in `dist/assets/`). Bundle-size note: that chunk will be large (~1.3MB gzipped) because it pulls in Plotly; this is expected and is exactly why we lazy-load.

- [ ] **Step 4: Run frontend test suite**

Run (in `web/`): `npx vitest run`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add /Users/houkjang/projects/stand-alone-analyzer/web/src/App.tsx
git commit -m "feat(web): wire lazy SelectorTab route"
```

#### Task 30: Backend regression run + final smoke

**Files:** none — verification only.

- [ ] **Step 1: Full backend test pass**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/ -v`
Expected: PASS — all of Plan 1 + the 10 new Plan 2 test files.

- [ ] **Step 2: Full frontend test pass**

Run (in `web/`): `npx vitest run && npm run typecheck`
Expected: PASS.

- [ ] **Step 3: Manual SSE smoke test**

Run the backend (in repo root):
```
/Users/houkjang/anaconda3/bin/python -m uvicorn flake_analysis.api.main:create_app --factory --port 8000
```

In another terminal:
```
curl -N -X POST http://localhost:8000/api/v1/projects/local/run/selector \
  -H 'Content-Type: application/json' -d '{}'
```
Expected: stream of `event: progress` then `event: done` (or `event: error: pipeline_failed` if no domain stats yet — that is the correct propagation of the upstream RuntimeError from `pipeline/selector.py:55-58`).

- [ ] **Step 4: Manual lock contention check**

Open two streams concurrently. The second one should immediately return HTTP 423 with body `{"error":{"code":"project_busy",...}}`.

- [ ] **Step 5: No commit — verification only**

```bash
echo "Plan 2 verification complete."
```

---

## Self-Review Notes

### Spec coverage check

| Spec section | Coverage |
|---|---|
| Frontend §3.3 `selectorSlice` | Task 15 |
| Frontend §4.2 component tree | Tasks 19–28 (every box in the tree maps to a task) |
| Frontend §4.2 server queries (`/data/domain_stats`, `/annotations/preview`) | Tasks 5, 7, 18 |
| Frontend §4.2 mutations (`/selector/commit`) | Task 9 |
| Frontend §4.2 export | Task 10 |
| Frontend §4.2 key interactions (slider debounce 200ms, lasso, click, row click, commit toast) | Tasks 19, 24, 26, 22 |
| Frontend §4.2 don't-overengineer (no filter undo, only Reset; 3D no events) | Tasks 19 (no history), 25 (no `onSelected`) |
| Frontend §5.1/§5.2 API client + SSE recap | Tasks 16, 17 |
| Frontend §7.2 porting order — Selector second | This entire plan |
| Backend §1.2 `POST /run/selector` | Task 8 |
| Backend §1.2 `SelectorParams` | Task 1 |
| Backend §1.3 `GET /data/domain_stats` | Task 5 |
| Backend §1.3 `GET /data/selector/selection` | Task 6 |
| Backend §1.3 `GET /data/annotations/{domain_id}/preview` | Task 7 |
| Backend §1.3 Arrow IPC vs JSON negotiation | Task 3 (writer) + Tasks 5, 6 (use it) |
| Backend §3 ProjectBusy → 423 | Task 8 (lock+drain) |
| Q-U3 native `<img>` (NOT OpenSeadragon) | Task 23 |
| `_brushing.py` port | Task 13 |
| `_image_preview.py` port (server side: crop + outline) | Task 4 (service) + Task 7 (route) |
| `_focus_domain_id` port (`tab_selector.py:695-708`) | Task 14 |
| `_METRIC_DEFS` port (`tab_selector.py:92-98`) | Task 1 (backend) + Task 12 (frontend) |
| `_commit_selection` brush ∩ filter (`tab_selector.py:773-779`) | Task 2 (service) + Task 9 (route) |
| Plotly Scattergl + lazy-load + bundle note | Tasks 11, 24, 25, 29 |

### Placeholder scan
- Searched for "TODO", "TBD", "implement here", "similar to Task" — none.
- Every step that produces code includes the actual code block.
- Every test step shows actual assertions and uses real fixture data.

### Type/name consistency
- `SelectorParams`, `SelectorCommitRequest`, `SelectorCommitSummary` are defined in Task 1 and used unchanged in Tasks 8, 9, 17.
- `BrushingState`, `applyLasso`, `undo`, `redo`, `clearBrush`, `setFocusId` defined in Task 13 and consumed in Tasks 14, 15, 21.
- `useSelectorStore` API is set in Task 15 (`setFilter`, `resetFilter`, `setAxis`, `applyLasso`, `undoBrush`, `redoBrush`, `clearBrush`, `setFocusId`, `toApiParams`) and every later component uses exactly those names.
- `DomainStats`, `SelectionRows`, `CommitSummary` types defined in Task 17 and consumed unchanged downstream.
- `useStepProgress` extension (`result`) added in Task 16; not consumed elsewhere in this plan (the Selector commit uses `useSelectorCommit` mutation, not SSE), but explicitly shipped because it was requested as a delta to Plan 1's hook.

### Spec ambiguity resolved
- **Frontend §3.3 vs §4.2 commit endpoint shape**: §3.3 says "Selector commit endpoint takes the full filter + selection". §4.2 says `POST /api/v1/projects/{pid}/selector/commit — body: full {filter_params, lasso_ids}`. We resolved this as `SelectorCommitRequest{params: SelectorParams, lasso_ids: list[int] | None}` (Task 1) — matches §4.2 exactly. The pipeline call inside the route writes `selection.parquet` (the filter pass) and the brush intersection then tightens it (Task 9), preserving the `_commit_selection` semantics from `tab_selector.py:773-779` verbatim.
- **`POST /run/selector` vs `POST /selector/commit`**: The backend §1.2 table lists the SSE one and the frontend §4.2 lists the synchronous mutation. They are NOT the same endpoint — both ship. SSE one is for power users / future scripting; the UI uses the synchronous JSON one (Tasks 8 + 9).

