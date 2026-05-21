# Explorer Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sprint 4 of the React + FastAPI migration: deliver a fully working Explorer tab — Include/Exclude label picker (with conflict resolution), NeighborFilter (size/isolation/border), 2×2 render toggles (Plan-v34 defaults), 4-LOD raw-image mosaic powered by OpenSeadragon (server-side Y-flip + per-image `LegacyTileSource`), pass/fail dim overlay (`filter: grayscale(1) opacity(0.5)` + `tiledImage.setOpacity(0.5)`), gold SVG outline on the selected tile, click→select→DetailPanel flow, flake list, and synchronous Save state — talking to four new backend endpoints (`GET /explorer/tile_manifest`, `GET /explorer/grid`, `GET /explorer/flakes`, `GET /explorer/flake/{id}`, `POST /run/explorer/save_state`, `GET /run/explorer/state`) plus two new static routes (`GET /static/thumbnails/lod{N}/{stem}.webp`, `GET /static/raw/{filename}`).

**Architecture:** Backend wraps `pipeline/explorer.save_explorer_state` (synchronous JSON, NOT SSE — see pinned decision #12) and adds a read-only `services/explorer_service.py` that ports the Streamlit `_load_inputs` + `_build_flake_records` + `_resolve_raw_path` resolver chain (cache_dir → in-folder → raw_images_dir → 404 per mosaic-viewer-design §10). Static asset routes reject path traversal at the route layer (no `..`, no absolute paths, stem must match `^[A-Za-z0-9_.-]+$`) and emit `Cache-Control: public, max-age=86400, immutable` plus an ETag = `params_hash:signature`. Frontend uses TanStack Query for `tile_manifest.json`/`grid`/`flakes`/`flake/{id}` (`staleTime: Infinity` + manual invalidation on mutation success), Zustand `explorerSlice` for Include/Exclude sets, NeighborFilter, render toggles, LOD choice, viewport state, and selected/focus flake ids, OpenSeadragon 4.1.x in `collectionMode` with one `LegacyTileSource` per tile (Path A from mosaic-viewer §2 — no DZI), an SVG overlay added via `viewer.addOverlay` for the selected-tile gold rectangle (`#FFC800`, width 3) per mosaic-viewer §6, and lazy-loaded `<MosaicCanvas>` wrapper to keep the OSD bundle out of the Compute/Selector/Clustering routes.

**Tech Stack:**
- Backend: FastAPI 0.110+, pydantic v2.6+, pyarrow 15+ (Arrow IPC streaming), pandas 2.x, Pillow 10+ (peek-raw size for the mosaic-viewer §2 Path A — pinned decision #2), httpx 0.28.1 (test transport), pytest-asyncio 0.23+
- Frontend: openseadragon 4.1.x (pinned decision #5 — stable 4.x), react-hook-form 7.51+, lucide-react 0.358+, sonner 1.4+, vitest 1.4+, msw 2.2+

---

## Spec ambiguity resolved

These twelve decisions were pinned in `/tmp/plan4-explorer-brief.md` and govern every task below. Each decision is mapped to its enforcing task(s); no later task may walk one back.

1. **Defer spritesheet renderer.** Plan 4 ships only the `LegacyTileSource` per-image path (mosaic-viewer §2 Path A). No DZI, no spritesheet pyramid generation. → enforced by Tasks 7, 27 (only `tileSources: tile_manifest.tiles.map(t => ({ type: 'image', url: ... }))`); no Task implements `pipeline/spritesheet.py`.
2. **Peek raw image size via Pillow once per stem on the server, cache in `tile_manifest.json`.** No on-the-fly probing in the route. → enforced by Task 4 (`build_tile_manifest()` calls `Image.open(raw).size` once and writes `width_px`/`height_px` per tile).
3. **Raw images are served as-is** — no resampling, no Y-flip in the byte stream. The Y-flip lives entirely in `tile_manifest.tiles[].row` (server-side coordinate). → enforced by Task 9 (`/static/raw/{filename}` returns `FileResponse` with no transforms).
4. **Server-side filter: include/exclude labels and size_min/size_max are query parameters on `/explorer/flakes`.** Frontend sends `?include=A,B&exclude=C&size_min=2&size_max=10` and the route filters before returning rows. Isolation/border-clipped remain client-side (deferred metrics per Streamlit). → enforced by Task 6.
5. **OpenSeadragon 4.1.x (stable 4.x).** No 5.x prerelease. Vite externalizes nothing — bundle as ESM via `@types/openseadragon`. → enforced by Tasks 26, 27 (`import OpenSeadragon from 'openseadragon'`, peer-dep `^4.1.0` in `web/package.json`).
6. **Defer pan-prefetch.** OSD's built-in `springStiffness`/`immediateRender` defaults are kept; no custom tile-prefetch worker. → no task implements a prefetcher.
7. **60×60 grid cap.** `build_tile_manifest()` rejects manifests where `grid_w > 60 or grid_h > 60` with `ParamsInvalid` so a runaway scan never ships a 4096-tile mosaic. → enforced by Task 4 (validator + test).
8. **CSS grid layout 60% / 22% / 18%.** `<ExplorerMain>` uses `grid-template-columns: 60% 22% 18%` for mosaic / flake list / detail panel. → enforced by Task 38.
9. **Full-pane empty state with CTA.** When prereqs (clustering or domain_proximity) are missing, `<ExplorerTab>` renders a single full-pane card with a "Go to Clustering" or "Go to Compute → Domain Proximity" link, NOT the partial layout. → enforced by Task 39.
10. **Render toggles are state-only no-ops in Plan 4.** They write to `explorerSlice.renderToggles` but the mosaic does NOT draw bbox/outline overlays yet (deferred to a later plan). → enforced by Task 23 (toggles store updates only) + Task 27 (MosaicCanvas does not read `renderToggles`).
11. **`/explorer/grid` is the canonical URL** for the grid endpoint (mosaic-viewer §4 wins over backend-design's earlier `/data/explorer/grid` draft). → enforced by Task 5.
12. **`POST /run/explorer/save_state` is synchronous JSON, NOT SSE.** Save is fast (≤200ms — JSON write + optional parquet write); SSE overhead is unjustified. The route returns `200 {state_path, selected_count}` directly and uses the per-project lock as a normal `async with` (not lock+drain). → enforced by Task 11 — diverges from Plan 2/3 SSE pattern.

---

## File Structure

### Backend (new — under `src/flake_analysis/api/`)

- `schemas/explorer.py` — `TileManifestEntry` (`{image_id, stem, col, row, width_px, height_px, lod_sizes}`), `TileManifest` (`{grid_w, grid_h, lod_sizes, signature, params_hash, tiles}`), `ExplorerFlakeRow` (`{flake_id, image_id, domains, groups, distance, clipped, pass}`), `ExplorerFlakesResponse` (`{rows: list[ExplorerFlakeRow], total: int}`), `ExplorerFlakeDetail` (`{flake_id, image_id, domain_ids, cluster_names, bbox_xy, mask_stats, distance_px, isolation_px}`), `NeighborFilterParams`, `SaveExplorerStateParams` (`{include_labels, exclude_labels, neighbor_filter, selected_flake_ids?}`), `SaveExplorerStateResult` (`{state_path, selected_count}`)
- `services/explorer_service.py` — `load_explorer_inputs(folder) -> dict` (port of `tab_explorer.py:_load_inputs`), `build_tile_manifest(folder) -> TileManifest` (peek-raw via Pillow, server-side Y-flip via `(max_iy - iy)`, 60×60 cap), `build_flake_table(folder, include, exclude, size_min, size_max) -> pd.DataFrame` (port of `_build_flake_records` + server-side filter), `build_flake_detail(folder, flake_id) -> ExplorerFlakeDetail`, `resolve_raw_path(raw_images_dir, stem, ext) -> Path` (port of `_resolve_raw_path` + path-traversal guard)
- `routes/explorer.py` — `GET /projects/{pid}/explorer/tile_manifest`, `GET /projects/{pid}/explorer/grid`, `GET /projects/{pid}/explorer/flakes`, `GET /projects/{pid}/explorer/flake/{flake_id}`, `POST /projects/{pid}/run/explorer/save_state`, `GET /projects/{pid}/run/explorer/state`
- `routes/static.py` — `GET /projects/{pid}/static/thumbnails/lod{lod}/{stem}.webp`, `GET /projects/{pid}/static/raw/{filename}` — both reject `..`, absolute paths, names not matching `^[A-Za-z0-9_.-]+$`; both emit `Cache-Control: public, max-age=86400, immutable` and `ETag = params_hash:signature`
- `services/path_safety.py` — `safe_join(base: Path, *parts: str) -> Path` (raises `ParamsInvalid` on `..`, absolute, or non-allowlist names)

### Backend (modifications)

- `errors.py` — add `ExplorerStateMissing` (404, code `explorer_state_missing`), `ThumbnailMissing` (404, code `thumbnail_missing`), `RawImageMissing` (404, code `raw_image_missing`)
- `main.py` — `app.include_router(explorer.router, prefix="/api/v1")`, `app.include_router(static.router, prefix="/api/v1")`

### Frontend (new — under `web/src/`)

- `state/explorerSlice.ts` — Zustand slice per frontend-design §3.5 (`includeLabels: Set<string>`, `excludeLabels: Set<string>`, `neighborFilter: {sizeMin, sizeMax, isolationMin, excludeBorderClipped}`, `selectedFlakeId: number|null`, `focusFlakeId: number|null`, `lodChoice: 'auto'|0|1|2|3`, `viewportState: {center, zoom}|null`, `renderToggles: {flake_bbox, flake_outline, island_bbox, island_outline}` defaulting `(true, false, false, true)`, all actions, `resetExplorerStore()` test helper)
- `api/explorer.ts` — typed fetch wrappers (`fetchTileManifest`, `fetchExplorerGrid`, `fetchExplorerFlakes`, `fetchExplorerFlakeDetail`, `saveExplorerState`, `getExplorerState`)
- `hooks/useTileManifest.ts` — TanStack Query for `TileManifest`
- `hooks/useExplorerGrid.ts` — TanStack Query for the `grid` payload
- `hooks/useExplorerFlakes.ts` — TanStack Query that takes the `(include, exclude, sizeMin, sizeMax)` query keys and returns `ExplorerFlakesResponse`
- `hooks/useExplorerFlakeDetail.ts` — TanStack Query keyed on `selectedFlakeId`, disabled when `null`
- `hooks/useSaveExplorerState.ts` — Mutation wrapping `POST /run/explorer/save_state` (synchronous), invalidates `['explorer','state',pid]` on success
- `components/explorer/ClusterIncludeExcludePicker.tsx` — two stacked multiselects + conflict caption (red italic) when same name lands in both
- `components/explorer/NeighborFilterPanel.tsx` — size_min/size_max number inputs + isolation_min number input + exclude-border-clipped checkbox
- `components/explorer/RenderTogglesPanel.tsx` — 2×2 checkboxes for `flake_bbox`/`flake_outline`/`island_bbox`/`island_outline`
- `components/explorer/LodPicker.tsx` — radio: `auto | lod0 | lod1 | lod2 | raw`
- `components/explorer/SaveExplorerStateButton.tsx` — runs the mutation, toasts on success/error, disabled when prereqs missing
- `lib/openseadragon.ts` — typed default-import re-export (`export { default as OpenSeadragon } from 'openseadragon'`) so test files can mock the module name once
- `components/explorer/MosaicCanvas.tsx` — OSD wrapper. Owns the `OpenSeadragon` viewer instance (collection mode), one `tileSources` entry per `TileManifestEntry`, Y-flip already encoded in `tile.row`, pass/fail dim via `tiledImage.setOpacity(failed ? 0.5 : 1.0)` + CSS class `.osd-failed { filter: grayscale(1) opacity(0.5); }`, gold SVG overlay on selected tile (`#FFC800` rect, width 3), click → `viewport.viewerElementToViewportCoordinates` → `(col, row)` → first flake on that image
- `components/explorer/FlakeListPanel.tsx` — filtered `ExplorerFlakeRow` table with row click → `setSelectedFlakeId`
- `components/explorer/DetailIdentity.tsx` — flake_id + image_id header
- `components/explorer/DetailLabels.tsx` — chip list of cluster_names with palette colors (port `CLUSTER_PALETTE` reuse from Plan 3 `lib/clusterColors.ts`)
- `components/explorer/DetailDistance.tsx` — distance_px + isolation_px metrics
- `components/explorer/DetailPanel.tsx` — composes `<DetailIdentity>` + `<DetailLabels>` + `<DetailDistance>` from `ExplorerFlakeDetail`
- `components/explorer/ExplorerRightRail.tsx` — composes ClusterIncludeExcludePicker + NeighborFilterPanel + RenderTogglesPanel + LodPicker + SaveExplorerStateButton
- `components/explorer/ExplorerMain.tsx` — three-pane CSS grid (`60% 22% 18%`) wrapping MosaicCanvas / FlakeListPanel / DetailPanel
- `pages/ExplorerTab.tsx` — top-level tab; full-pane empty-state CTA when prereqs missing; otherwise `<ExplorerRightRail>` + `<ExplorerMain>` lazy-loaded
- `App.tsx` — register the lazy `ExplorerTab` route

### Tests (backend)

- `tests/api/test_explorer_schemas.py`
- `tests/api/test_path_safety.py`
- `tests/api/test_explorer_service.py`
- `tests/api/test_data_explorer_tile_manifest.py`
- `tests/api/test_data_explorer_grid.py`
- `tests/api/test_data_explorer_flakes.py`
- `tests/api/test_data_explorer_flake_detail.py`
- `tests/api/test_run_explorer_save_state.py`
- `tests/api/test_run_explorer_get_state.py`
- `tests/api/test_static_thumbnails.py`
- `tests/api/test_static_raw.py`

### Tests (frontend)

- `web/src/state/__tests__/explorerSlice.test.ts`
- `web/src/api/__tests__/explorer.test.ts`
- `web/src/hooks/__tests__/useTileManifest.test.tsx`
- `web/src/hooks/__tests__/useExplorerFlakes.test.tsx`
- `web/src/hooks/__tests__/useSaveExplorerState.test.tsx`
- `web/src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx`
- `web/src/components/explorer/__tests__/NeighborFilterPanel.test.tsx`
- `web/src/components/explorer/__tests__/RenderTogglesPanel.test.tsx`
- `web/src/components/explorer/__tests__/LodPicker.test.tsx`
- `web/src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx`
- `web/src/components/explorer/__tests__/MosaicCanvas.test.tsx`
- `web/src/components/explorer/__tests__/FlakeListPanel.test.tsx`
- `web/src/components/explorer/__tests__/DetailPanel.test.tsx`
- `web/src/pages/__tests__/ExplorerTab.test.tsx`

---

## Tasks (Grouped into Phases)

### Phase 1 — Backend schemas + path-safety helper

#### Task 1: Explorer schemas

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/schemas/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_explorer_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_explorer_schemas.py
import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.explorer import (
    TileManifestEntry,
    TileManifest,
    ExplorerFlakeRow,
    ExplorerFlakesResponse,
    ExplorerFlakeDetail,
    NeighborFilterParams,
    SaveExplorerStateParams,
    SaveExplorerStateResult,
)


def test_tile_manifest_entry_round_trip():
    e = TileManifestEntry(
        image_id=7, stem="ix003_iy017", col=3, row=2,
        width_px=2048, height_px=1536,
        lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
    )
    assert e.image_id == 7
    assert e.row == 2
    assert e.lod_sizes["1"] == [192, 144]


def test_tile_manifest_signature_and_tiles():
    m = TileManifest(
        grid_w=4, grid_h=3,
        lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
        signature=["sig0", "sig1"],
        params_hash="abc123",
        tiles=[
            TileManifestEntry(
                image_id=1, stem="ix000_iy000", col=0, row=2,
                width_px=2048, height_px=1536,
                lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
            ),
        ],
    )
    assert m.grid_w == 4
    assert len(m.tiles) == 1
    assert m.tiles[0].col == 0


def test_explorer_flake_row_shape():
    r = ExplorerFlakeRow(
        flake_id=42, image_id=7, domains=3,
        groups="thin, thick", distance="—", clipped="no", **{"pass": True},
    )
    assert r.flake_id == 42
    assert r.model_dump()["pass"] is True


def test_explorer_flakes_response_total_matches_or_exceeds_rows():
    resp = ExplorerFlakesResponse(rows=[], total=0)
    assert resp.total == 0
    resp2 = ExplorerFlakesResponse(
        rows=[ExplorerFlakeRow(
            flake_id=1, image_id=0, domains=1, groups="—",
            distance="—", clipped="no", **{"pass": True})],
        total=5,
    )
    assert resp2.total == 5


def test_explorer_flake_detail_shape():
    d = ExplorerFlakeDetail(
        flake_id=42, image_id=7,
        domain_ids=[100, 101, 102],
        cluster_names=["thin"],
        bbox_xy=[10, 20, 200, 300],
        mask_stats={"area_px": 4500, "perimeter_px": 320.0},
        distance_px=12.5,
        isolation_px=80.0,
    )
    assert d.bbox_xy == [10, 20, 200, 300]


def test_neighbor_filter_params_optional_fields():
    nf = NeighborFilterParams()
    assert nf.size_min is None
    assert nf.size_max is None
    assert nf.isolation_min is None
    assert nf.exclude_border_clipped is False

    nf2 = NeighborFilterParams(size_min=2, size_max=10, isolation_min=80.0,
                               exclude_border_clipped=True)
    assert nf2.size_min == 2
    assert nf2.exclude_border_clipped is True


def test_save_explorer_state_params_minimal():
    p = SaveExplorerStateParams(
        include_labels=["thin"],
        exclude_labels=[],
        neighbor_filter=NeighborFilterParams(size_min=1, size_max=50),
    )
    assert p.selected_flake_ids is None
    assert p.include_labels == ["thin"]


def test_save_explorer_state_params_with_selection():
    p = SaveExplorerStateParams(
        include_labels=[], exclude_labels=["noise"],
        neighbor_filter=NeighborFilterParams(),
        selected_flake_ids=[1, 2, 3],
    )
    assert p.selected_flake_ids == [1, 2, 3]


def test_save_explorer_state_result_shape():
    r = SaveExplorerStateResult(state_path="/tmp/explorer_state.json", selected_count=42)
    assert r.selected_count == 42
    r2 = SaveExplorerStateResult(state_path="/tmp/explorer_state.json", selected_count=None)
    assert r2.selected_count is None


def test_neighbor_filter_rejects_negative_isolation():
    with pytest.raises(ValidationError):
        NeighborFilterParams(isolation_min=-1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.schemas.explorer'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/schemas/explorer.py
"""Explorer schemas per backend design §1.2 + §1.3 + mosaic-viewer §3-§4."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class TileManifestEntry(BaseModel):
    image_id: int
    stem: str
    col: int = Field(ge=0)
    row: int = Field(ge=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    # LOD index (str) -> [w, h] pixel size of the cached thumbnail.
    lod_sizes: dict[str, list[int]]


class TileManifest(BaseModel):
    grid_w: int = Field(gt=0, le=60)  # Pinned decision #7: 60×60 cap
    grid_h: int = Field(gt=0, le=60)
    lod_sizes: dict[str, list[int]]
    signature: list[str]
    params_hash: str
    tiles: list[TileManifestEntry]


class ExplorerFlakeRow(BaseModel):
    flake_id: int
    image_id: int
    domains: int = Field(ge=0)
    groups: str
    distance: str
    clipped: str
    # 'pass' is a Python keyword — quote it on construction.
    model_config = {"populate_by_name": True}
    pass_: bool = Field(alias="pass")


class ExplorerFlakesResponse(BaseModel):
    rows: list[ExplorerFlakeRow]
    total: int = Field(ge=0)


class ExplorerFlakeDetail(BaseModel):
    flake_id: int
    image_id: int
    domain_ids: list[int]
    cluster_names: list[str]
    bbox_xy: list[int]  # [x, y, w, h]
    mask_stats: dict[str, float]
    distance_px: Optional[float] = None
    isolation_px: Optional[float] = None


class NeighborFilterParams(BaseModel):
    size_min: Optional[int] = Field(default=None, ge=1)
    size_max: Optional[int] = Field(default=None, ge=1)
    isolation_min: Optional[float] = Field(default=None, ge=0.0)
    exclude_border_clipped: bool = False


class SaveExplorerStateParams(BaseModel):
    include_labels: list[str]
    exclude_labels: list[str]
    neighbor_filter: NeighborFilterParams
    selected_flake_ids: Optional[list[int]] = None


class SaveExplorerStateResult(BaseModel):
    state_path: str
    selected_count: Optional[int] = None
```

> Note on the `pass` field: the Pydantic alias means the model serializes/parses with key `"pass"` while the Python attribute is `pass_`. Tests use `**{"pass": True}` to construct.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_schemas.py -v`
Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/schemas/explorer.py tests/api/test_explorer_schemas.py
git commit -m "feat(api): add Explorer schemas (TileManifest, FlakeRow, SaveState)"
```

---

#### Task 2: Path-safety helper

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/path_safety.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_path_safety.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_path_safety.py
from pathlib import Path

import pytest

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.services.path_safety import safe_join


def test_safe_join_rejects_dot_dot_segment(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "..", "etc", "passwd")


def test_safe_join_rejects_embedded_dot_dot(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "foo/../../etc/passwd")


def test_safe_join_rejects_absolute_path(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_backslash(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "a\\b")


def test_safe_join_rejects_non_allowlist_chars(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "ix003 iy017.webp")  # space disallowed


def test_safe_join_rejects_null_byte(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "ix003\x00.webp")


def test_safe_join_accepts_valid_filename(tmp_path: Path):
    out = safe_join(tmp_path, "lod0", "ix003_iy017.webp")
    assert out == tmp_path / "lod0" / "ix003_iy017.webp"


def test_safe_join_accepts_dotted_filename(tmp_path: Path):
    out = safe_join(tmp_path, "ix003_iy017.webp")
    assert out.name == "ix003_iy017.webp"


def test_safe_join_resolves_inside_base(tmp_path: Path):
    out = safe_join(tmp_path, "a", "b.webp")
    # resolved path must remain a child of tmp_path
    assert tmp_path in out.parents or out == tmp_path / "a" / "b.webp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_path_safety.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.services.path_safety'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/path_safety.py
"""Path-traversal guard. All static-asset routes MUST run input through safe_join."""
from __future__ import annotations
import re
from pathlib import Path

from flake_analysis.api.errors import ParamsInvalid

# Allowlist: ASCII letters, digits, dot, underscore, hyphen.
# No slash, no backslash, no spaces, no shell metacharacters, no null byte.
_ALLOWED = re.compile(r"^[A-Za-z0-9_.\-]+$")


def safe_join(base: Path, *parts: str) -> Path:
    """Join `parts` onto `base`, refusing any traversal/absolute/disallowed input.

    Raises ParamsInvalid (HTTP 400) on any of:
    - any part containing ".." as a segment OR substring
    - any part starting with "/" (absolute) or containing "\\"
    - any part containing a character outside [A-Za-z0-9_.-]
    - any part containing a null byte

    The final path is also re-resolved and must remain a child of `base`.
    """
    base_resolved = Path(base).resolve()
    for p in parts:
        if not isinstance(p, str) or not p:
            raise ParamsInvalid(reason="empty_segment")
        if "\x00" in p:
            raise ParamsInvalid(reason="null_byte")
        if p.startswith("/") or p.startswith("\\"):
            raise ParamsInvalid(reason="absolute_path")
        if "\\" in p:
            raise ParamsInvalid(reason="backslash")
        if ".." in p:
            raise ParamsInvalid(reason="dot_dot")
        if not _ALLOWED.match(p):
            raise ParamsInvalid(reason="disallowed_chars", value=p)

    out = base_resolved.joinpath(*parts)
    # Defense-in-depth: ensure the joined path doesn't escape via symlinks
    # by checking the parent chain (we don't resolve `out` itself because
    # the file may not yet exist).
    try:
        out.relative_to(base_resolved)
    except ValueError:
        raise ParamsInvalid(reason="escape_attempted")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_path_safety.py -v`
Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/path_safety.py tests/api/test_path_safety.py
git commit -m "feat(api): add safe_join path-traversal guard for static routes"
```

---

#### Task 3: Explorer error codes

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/errors.py`
- Test: extend `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_errors.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_errors.py`:

```python
def test_explorer_state_missing_envelope():
    from flake_analysis.api.errors import ExplorerStateMissing
    e = ExplorerStateMissing()
    env = e.to_response()
    assert env["error"]["code"] == "explorer_state_missing"
    assert e.status_code == 404


def test_thumbnail_missing_envelope():
    from flake_analysis.api.errors import ThumbnailMissing
    e = ThumbnailMissing(lod=0, stem="ix003_iy017")
    env = e.to_response()
    assert env["error"]["code"] == "thumbnail_missing"
    assert env["error"]["details"] == {"lod": 0, "stem": "ix003_iy017"}
    assert e.status_code == 404


def test_raw_image_missing_envelope():
    from flake_analysis.api.errors import RawImageMissing
    e = RawImageMissing(filename="ix003_iy017.png")
    env = e.to_response()
    assert env["error"]["code"] == "raw_image_missing"
    assert e.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_errors.py -v`
Expected: FAIL with `ImportError: cannot import name 'ExplorerStateMissing'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/errors.py`:

```python
class ExplorerStateMissing(AppError):
    code = "explorer_state_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "No explorer state saved yet. Click Save on the Explorer tab."


class ThumbnailMissing(AppError):
    code = "thumbnail_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Thumbnail not found for the requested LOD/stem."


class RawImageMissing(AppError):
    code = "raw_image_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Raw substrate image not found."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_errors.py -v`
Expected: ALL PASS (3 new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/errors.py tests/api/test_errors.py
git commit -m "feat(api): add ExplorerStateMissing/ThumbnailMissing/RawImageMissing error codes"
```

---

### Phase 2 — Explorer service (load inputs, build_tile_manifest with peek-raw, build_flake_table, build_flake_detail, resolve_raw_path)

#### Task 4: build_tile_manifest with peek-raw and 60×60 cap

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/explorer_service.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_explorer_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_explorer_service.py
"""Explorer service tests — peek-raw size, server-side Y-flip, 60×60 cap."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.services.explorer_service import (
    build_tile_manifest,
)


def _write_raw_image(folder: Path, stem: str, w: int = 80, h: int = 60) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    Image.fromarray(arr).save(folder / f"{stem}.png")


def _write_thumbnail(folder: Path, lod: int, stem: str, w: int, h: int) -> None:
    lod_dir = folder / f"lod{lod}"
    lod_dir.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    Image.fromarray(arr).save(lod_dir / f"{stem}.webp")


def _write_minimal_manifest_for_explorer(folder: Path, image_id_to_name: dict[int, str]) -> None:
    """Write the minimal manifest.json fields explorer_service reads."""
    raw_dir = folder / "raw"
    cache_dir = folder / "00_thumbnails"
    manifest = {
        "version": 1,
        "analysis_folder": str(folder),
        "raw_images_dir": str(raw_dir),
        "thumbnails_cache_dir": str(cache_dir),
        "annotations_path": str(folder / "annotations.json"),
        "steps": {
            "thumbnails": {
                "completed_at": "2026-05-21T00:00:00Z",
                "params": {},
                "params_hash": "thumb_hash",
                "input_hashes": {},
                "outputs": {
                    "index_json": "00_thumbnails/index.json",
                },
            },
        },
        "image_id_to_stem": image_id_to_name,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))


def _write_thumb_index(folder: Path, lod_sizes: dict[str, list[int]]) -> None:
    cache = folder / "00_thumbnails"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": lod_sizes,
        "signature": ["sig0", "sig1"],
    }))


def test_build_tile_manifest_y_flips_via_row_field(tmp_path: Path):
    """iy=0 → bottom (highest row index), iy=max → top (row=0)."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000", 1: "ix000_iy002", 2: "ix001_iy001"}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem, w=80, h=60)
    _write_thumb_index(tmp_path, {"0": [64, 48], "1": [192, 144], "2": [480, 360]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for lod, (w, h) in [(0, (64, 48)), (1, (192, 144)), (2, (480, 360))]:
        for stem in image_id_to_name.values():
            _write_thumbnail(tmp_path / "00_thumbnails", lod, stem, w, h)

    m = build_tile_manifest(tmp_path)
    assert m.grid_w == 2
    assert m.grid_h == 3
    by_stem = {t.stem: t for t in m.tiles}
    # iy=0 → row = grid_h - 1 = 2
    assert by_stem["ix000_iy000"].row == 2
    # iy=2 → row = 0
    assert by_stem["ix000_iy002"].row == 0
    # iy=1 → row = 1
    assert by_stem["ix001_iy001"].row == 1


def test_build_tile_manifest_peeks_raw_size_via_pillow(tmp_path: Path):
    """width_px/height_px come from PIL once per stem, then are cached in the manifest."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000"}
    _write_raw_image(raw, "ix000_iy000", w=2048, h=1536)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    _write_thumbnail(tmp_path / "00_thumbnails", 0, "ix000_iy000", 64, 48)

    m = build_tile_manifest(tmp_path)
    assert m.tiles[0].width_px == 2048
    assert m.tiles[0].height_px == 1536


def test_build_tile_manifest_carries_signature_and_params_hash(tmp_path: Path):
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000"}
    _write_raw_image(raw, "ix000_iy000")
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    _write_thumbnail(tmp_path / "00_thumbnails", 0, "ix000_iy000", 64, 48)

    m = build_tile_manifest(tmp_path)
    assert m.signature == ["sig0", "sig1"]
    assert m.params_hash == "thumb_hash"


def test_build_tile_manifest_rejects_grid_over_60x60(tmp_path: Path):
    """Pinned decision #7: 60×60 cap."""
    raw = tmp_path / "raw"
    image_id_to_name = {i: f"ix{i:03d}_iy000" for i in range(61)}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for stem in image_id_to_name.values():
        _write_thumbnail(tmp_path / "00_thumbnails", 0, stem, 64, 48)

    with pytest.raises(ParamsInvalid):
        build_tile_manifest(tmp_path)


def test_build_tile_manifest_skips_missing_thumbnails_for_unparseable_names(tmp_path: Path):
    """Names that don't match ix###_iy### use the divmod fallback layout."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "weird_name_0", 1: "weird_name_1"}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for stem in image_id_to_name.values():
        _write_thumbnail(tmp_path / "00_thumbnails", 0, stem, 64, 48)

    m = build_tile_manifest(tmp_path)
    # Fallback: 2 images → grid_w=2, grid_h=1
    assert m.grid_w * m.grid_h >= 2
    assert {t.stem for t in m.tiles} == {"weird_name_0", "weird_name_1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flake_analysis.api.services.explorer_service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/flake_analysis/api/services/explorer_service.py
"""Explorer service: ports tab_explorer.py business logic to a stateless module.

Strict layering: routes call build_tile_manifest / build_flake_table /
build_flake_detail / resolve_raw_path. NO Streamlit imports.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from PIL import Image

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.schemas.explorer import (
    ExplorerFlakeDetail,
    TileManifest,
    TileManifestEntry,
)

_GRID_RE = re.compile(r"ix(\d+)_iy(\d+)")
_MAX_GRID = 60  # Pinned decision #7


def _read_manifest_json(folder: Path) -> dict[str, Any]:
    p = Path(folder) / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"manifest.json not found in {folder}")
    return json.loads(p.read_text(encoding="utf-8"))


def _read_thumb_index(folder: Path) -> dict[str, Any]:
    p = Path(folder) / "00_thumbnails" / "index.json"
    if not p.exists():
        raise FileNotFoundError(f"00_thumbnails/index.json not found")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_grid_layout(
    image_ids: list[int],
    image_id_to_name: dict[int, str],
) -> tuple[int, int, dict[int, tuple[int, int]]]:
    """Port of tab_explorer.py:_build_grid_layout (server-side Y-flip).

    iy=0 → row = grid_h - 1 (BOTTOM); iy=max → row = 0 (TOP).
    """
    coords: dict[int, tuple[int, int]] = {}
    parsed_all = True
    for iid in image_ids:
        name = image_id_to_name.get(int(iid), "")
        m = _GRID_RE.search(name) if name else None
        if m is None:
            parsed_all = False
            break
        coords[int(iid)] = (int(m.group(1)), int(m.group(2)))

    if parsed_all and coords:
        cols = [c for c, _ in coords.values()]
        rows = [r for _, r in coords.values()]
        grid_w = max(cols) - min(cols) + 1
        grid_h = max(rows) - min(rows) + 1
        cmin, rmax = min(cols), max(rows)
        coords = {iid: (c - cmin, rmax - r) for iid, (c, r) in coords.items()}
        return int(grid_w), int(grid_h), coords

    n = len(image_ids)
    grid_w = max(1, int(np.ceil(np.sqrt(n))))
    grid_h = max(1, int(np.ceil(n / grid_w)))
    fallback: dict[int, tuple[int, int]] = {}
    for i, iid in enumerate(image_ids):
        r, c = divmod(i, grid_w)
        fallback[int(iid)] = (c, r)
    return grid_w, grid_h, fallback


def build_tile_manifest(analysis_folder: str | Path) -> TileManifest:
    """Build the canonical tile manifest for the OSD mosaic.

    Per pinned decision #2: peek raw image size with PIL ONCE per stem.
    Per pinned decision #7: reject grids larger than 60×60.
    """
    folder = Path(analysis_folder)
    manifest = _read_manifest_json(folder)
    thumb_index = _read_thumb_index(folder)

    image_id_to_stem: dict[int, str] = {
        int(k): str(v) for k, v in manifest.get("image_id_to_stem", {}).items()
    }
    image_ids = sorted(image_id_to_stem.keys())
    grid_w, grid_h, coords = _build_grid_layout(image_ids, image_id_to_stem)

    if grid_w > _MAX_GRID or grid_h > _MAX_GRID:
        raise ParamsInvalid(
            reason="grid_too_large",
            grid_w=grid_w, grid_h=grid_h, max=_MAX_GRID,
        )

    raw_images_dir = Path(manifest["raw_images_dir"])
    lod_sizes: dict[str, list[int]] = {
        str(k): list(v) for k, v in thumb_index.get("lod_sizes", {}).items()
    }
    signature: list[str] = list(thumb_index.get("signature", []))
    params_hash: str = manifest.get("steps", {}).get("thumbnails", {}).get(
        "params_hash", ""
    )

    tiles: list[TileManifestEntry] = []
    for iid in image_ids:
        stem = image_id_to_stem[iid]
        col, row = coords[iid]
        raw_path = raw_images_dir / f"{stem}.png"
        if not raw_path.exists():
            # try common alternates
            for ext in (".jpg", ".jpeg", ".tif", ".tiff"):
                alt = raw_images_dir / f"{stem}{ext}"
                if alt.exists():
                    raw_path = alt
                    break
        with Image.open(raw_path) as im:
            w_px, h_px = im.size
        tiles.append(TileManifestEntry(
            image_id=iid,
            stem=stem,
            col=col,
            row=row,
            width_px=int(w_px),
            height_px=int(h_px),
            lod_sizes=lod_sizes,
        ))

    return TileManifest(
        grid_w=grid_w,
        grid_h=grid_h,
        lod_sizes=lod_sizes,
        signature=signature,
        params_hash=params_hash,
        tiles=tiles,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py::test_build_tile_manifest_y_flips_via_row_field tests/api/test_explorer_service.py::test_build_tile_manifest_peeks_raw_size_via_pillow tests/api/test_explorer_service.py::test_build_tile_manifest_carries_signature_and_params_hash tests/api/test_explorer_service.py::test_build_tile_manifest_rejects_grid_over_60x60 tests/api/test_explorer_service.py::test_build_tile_manifest_skips_missing_thumbnails_for_unparseable_names -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/explorer_service.py tests/api/test_explorer_service.py
git commit -m "feat(api): add build_tile_manifest with peek-raw + Y-flip + 60x60 cap"
```

---

#### Task 5: build_flake_table with server-side filter

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/explorer_service.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_explorer_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_explorer_service.py`:

```python
def _write_clustering_and_proximity(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    (folder / "05_domain_proximity").mkdir(parents=True, exist_ok=True)
    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1, "20": 1, "21": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(labels))
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "cluster_id": [0, 0, 1, 1, 0],
        "posterior_p": [0.9, 0.8, 0.85, 0.7, 0.95],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "flake_id":  [100, 100, 100, 200, 200],
        "flake_size": [3, 3, 3, 2, 2],
        "image_id":  [0, 0, 0, 1, 1],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)


def test_build_flake_table_no_filter_returns_all(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=None, size_max=None)
    assert len(df) == 2
    assert set(df["flake_id"].tolist()) == {100, 200}


def test_build_flake_table_include_filter_keeps_matching_only(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=["thick"], exclude_labels=[],
                           size_min=None, size_max=None)
    # Flake 100 has cluster set {thin, thick}; flake 200 has {thick, thin} too.
    # Both pass include={thick}.
    assert set(df["flake_id"].tolist()) == {100, 200}


def test_build_flake_table_exclude_filter_drops_matching(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=["thick"],
                           size_min=None, size_max=None)
    # Both flakes contain "thick" → both excluded.
    assert df.empty


def test_build_flake_table_size_min_max(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=3, size_max=3)
    assert df["flake_id"].tolist() == [100]


def test_build_flake_table_size_max_only(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=None, size_max=2)
    assert df["flake_id"].tolist() == [200]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py -k build_flake_table -v`
Expected: FAIL — `ImportError: cannot import name 'build_flake_table'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/services/explorer_service.py`:

```python
def _load_clustering_and_proximity(folder: Path) -> dict[str, Any]:
    labels_p = folder / "04_clustering" / "labels.json"
    asn_p = folder / "04_clustering" / "assignments.parquet"
    flakes_p = folder / "05_domain_proximity" / "flake_assignments.parquet"
    if not (labels_p.exists() and asn_p.exists() and flakes_p.exists()):
        raise FileNotFoundError("Clustering or domain_proximity output missing")

    labels = json.loads(labels_p.read_text(encoding="utf-8"))
    asn = pd.read_parquet(asn_p)
    fa = pd.read_parquet(flakes_p)

    # Tolerate both legacy column names.
    if "cluster_id" not in asn.columns and "cluster_label" in asn.columns:
        asn = asn.rename(columns={"cluster_label": "cluster_id"})
    if "posterior_p" not in asn.columns and "max_posterior" in asn.columns:
        asn = asn.rename(columns={"max_posterior": "posterior_p"})
    return {"labels": labels, "assignments": asn, "flake_assignments": fa}


def build_flake_table(
    analysis_folder: str | Path,
    *,
    include_labels: list[str],
    exclude_labels: list[str],
    size_min: Optional[int],
    size_max: Optional[int],
) -> pd.DataFrame:
    """Port of tab_explorer.py:_build_flake_records + server-side filter (pinned #4).

    Returns the FILTERED DataFrame (no `pass` column — only rows that pass).
    Columns: flake_id, image_id, domains, groups, distance, clipped, pass.
    """
    folder = Path(analysis_folder)
    inputs = _load_clustering_and_proximity(folder)
    fa: pd.DataFrame = inputs["flake_assignments"]
    asn: pd.DataFrame = inputs["assignments"]
    labels: dict[str, Any] = inputs["labels"]

    cid_to_name = {int(g["id"]): g["name"] for g in labels.get("groups", [])}
    asn_idx = asn.set_index("domain_id")["cluster_id"].astype(int).to_dict()

    rows: list[dict[str, Any]] = []
    for flake_id, group in fa.groupby("flake_id"):
        domain_ids = group["domain_id"].astype(int).tolist()
        cluster_ids: set[int] = set()
        for d in domain_ids:
            cid = asn_idx.get(int(d))
            if cid is not None and cid >= 0:
                cluster_ids.add(int(cid))
        names = sorted({cid_to_name.get(c, f"cluster_{c}") for c in cluster_ids})
        image_id = int(group["image_id"].iloc[0]) if "image_id" in group.columns else 0
        rows.append({
            "flake_id": int(flake_id),
            "image_id": image_id,
            "domains": int(len(domain_ids)),
            "groups": ", ".join(names) if names else "—",
            "distance": "—",
            "clipped": "no",
            "_cluster_set": frozenset(cluster_ids),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df.assign(**{"pass": pd.Series(dtype=bool)}).drop(columns=["_cluster_set"])

    name_to_cid = {g["name"]: int(g["id"]) for g in labels.get("groups", [])}
    inc_ids: Optional[set[int]] = (
        {name_to_cid[n] for n in include_labels if n in name_to_cid}
        if include_labels else None
    )
    exc_ids: set[int] = {name_to_cid[n] for n in exclude_labels if n in name_to_cid}

    def _passes(cset: frozenset) -> bool:
        if inc_ids is not None and inc_ids and not (cset & inc_ids):
            return False
        if exc_ids and (cset & exc_ids):
            return False
        return True

    df["pass"] = df["_cluster_set"].apply(_passes)
    if size_min is not None:
        df.loc[df["domains"] < size_min, "pass"] = False
    if size_max is not None:
        df.loc[df["domains"] > size_max, "pass"] = False

    out = df.drop(columns=["_cluster_set"])
    return out.loc[out["pass"]].reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py -v`
Expected: ALL PASS (5 manifest + 5 table = 10).

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/explorer_service.py tests/api/test_explorer_service.py
git commit -m "feat(api): add build_flake_table with server-side include/exclude/size filter"
```

---

#### Task 6: build_flake_detail + resolve_raw_path

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/services/explorer_service.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_explorer_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_explorer_service.py`:

```python
def test_build_flake_detail_returns_domain_ids_and_cluster_names(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_detail
    _write_clustering_and_proximity(tmp_path)
    detail = build_flake_detail(tmp_path, flake_id=100)
    assert detail.flake_id == 100
    assert detail.image_id == 0
    assert detail.domain_ids == [10, 11, 12]
    assert set(detail.cluster_names) == {"thin", "thick"}


def test_build_flake_detail_raises_on_unknown_flake(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_detail
    _write_clustering_and_proximity(tmp_path)
    with pytest.raises(KeyError):
        build_flake_detail(tmp_path, flake_id=99999)


def test_resolve_raw_path_cache_dir_first(tmp_path: Path):
    """Resolver chain (mosaic-viewer §10): cache_dir → in-folder → raw_images_dir → 404."""
    from flake_analysis.api.services.explorer_service import resolve_raw_path
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ix000_iy000.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "ix000_iy000.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    out = resolve_raw_path(
        cache_dir=cache, in_folder=tmp_path, raw_images_dir=raw_dir,
        stem="ix000_iy000", ext=".png",
    )
    assert out == cache / "ix000_iy000.png"


def test_resolve_raw_path_falls_back_to_in_folder(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import resolve_raw_path
    cache = tmp_path / "cache"
    cache.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "ix000_iy000.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    in_folder = tmp_path / "in"
    in_folder.mkdir()
    (in_folder / "ix000_iy000.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    out = resolve_raw_path(
        cache_dir=cache, in_folder=in_folder, raw_images_dir=raw_dir,
        stem="ix000_iy000", ext=".png",
    )
    assert out == in_folder / "ix000_iy000.png"


def test_resolve_raw_path_falls_back_to_raw_images_dir(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import resolve_raw_path
    cache = tmp_path / "cache"
    cache.mkdir()
    in_folder = tmp_path / "in"
    in_folder.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "ix000_iy000.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    out = resolve_raw_path(
        cache_dir=cache, in_folder=in_folder, raw_images_dir=raw_dir,
        stem="ix000_iy000", ext=".png",
    )
    assert out == raw_dir / "ix000_iy000.png"


def test_resolve_raw_path_returns_none_when_missing_everywhere(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import resolve_raw_path
    cache = tmp_path / "cache"; cache.mkdir()
    in_folder = tmp_path / "in"; in_folder.mkdir()
    raw_dir = tmp_path / "raw"; raw_dir.mkdir()
    out = resolve_raw_path(
        cache_dir=cache, in_folder=in_folder, raw_images_dir=raw_dir,
        stem="missing_stem", ext=".png",
    )
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py -k "build_flake_detail or resolve_raw_path" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/services/explorer_service.py`:

```python
def build_flake_detail(
    analysis_folder: str | Path, *, flake_id: int
) -> ExplorerFlakeDetail:
    """Build the detail-pane payload for a single flake.

    bbox_xy + mask_stats are best-effort: PR 2.5 leaves them empty; later
    plans extend this without breaking the schema.
    """
    folder = Path(analysis_folder)
    inputs = _load_clustering_and_proximity(folder)
    fa: pd.DataFrame = inputs["flake_assignments"]
    asn: pd.DataFrame = inputs["assignments"]
    labels: dict[str, Any] = inputs["labels"]

    grp = fa.loc[fa["flake_id"].astype(int) == int(flake_id)]
    if grp.empty:
        raise KeyError(f"flake_id {flake_id} not found")

    domain_ids = grp["domain_id"].astype(int).tolist()
    image_id = int(grp["image_id"].iloc[0]) if "image_id" in grp.columns else 0

    cid_to_name = {int(g["id"]): g["name"] for g in labels.get("groups", [])}
    asn_idx = asn.set_index("domain_id")["cluster_id"].astype(int).to_dict()
    cluster_names = sorted({
        cid_to_name.get(asn_idx[d], f"cluster_{asn_idx[d]}")
        for d in domain_ids if d in asn_idx and asn_idx[d] >= 0
    })

    return ExplorerFlakeDetail(
        flake_id=int(flake_id),
        image_id=image_id,
        domain_ids=domain_ids,
        cluster_names=cluster_names,
        bbox_xy=[],
        mask_stats={},
        distance_px=None,
        isolation_px=None,
    )


def resolve_raw_path(
    *,
    cache_dir: Path,
    in_folder: Path,
    raw_images_dir: Path,
    stem: str,
    ext: str,
) -> Optional[Path]:
    """Mosaic-viewer §10 resolver chain: cache → in-folder → raw_images_dir → None.

    Pinned decision #3: raw images are served as-is, no transforms here.
    """
    candidates = [
        Path(cache_dir) / f"{stem}{ext}",
        Path(in_folder) / f"{stem}{ext}",
        Path(raw_images_dir) / f"{stem}{ext}",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_explorer_service.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/explorer_service.py tests/api/test_explorer_service.py
git commit -m "feat(api): add build_flake_detail + resolve_raw_path resolver chain"
```

---

### Phase 3 — Backend read routes (`/explorer/tile_manifest`, `/explorer/grid`, `/explorer/flakes`, `/explorer/flake/{id}`)

#### Task 7: GET /explorer/tile_manifest

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_explorer_tile_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_explorer_tile_manifest.py
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_thumb_world(folder: Path) -> None:
    raw = folder / "raw"; raw.mkdir(parents=True)
    cache = folder / "00_thumbnails"; cache.mkdir(parents=True)
    image_id_to_stem = {0: "ix000_iy000", 1: "ix001_iy000"}
    for stem in image_id_to_stem.values():
        Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / f"{stem}.png")
        for lod, (w, h) in [(0, (64, 48)), (1, (192, 144))]:
            (cache / f"lod{lod}").mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.zeros((h, w, 3), dtype=np.uint8)).save(
                cache / f"lod{lod}" / f"{stem}.webp"
            )
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48], "1": [192, 144]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1,
        "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "thumbnails_cache_dir": str(cache),
        "annotations_path": str(folder / "annotations.json"),
        "steps": {
            "thumbnails": {
                "completed_at": "2026-05-21T00:00:00Z",
                "params": {}, "params_hash": "thumb_hash",
                "input_hashes": {}, "outputs": {"index_json": "00_thumbnails/index.json"},
            },
        },
        "image_id_to_stem": image_id_to_stem,
    }))


@pytest.mark.asyncio
async def test_tile_manifest_returns_grid_and_tiles(tmp_path: Path):
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/tile_manifest")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["grid_w"] == 2
    assert payload["grid_h"] == 1
    assert payload["params_hash"] == "thumb_hash"
    assert len(payload["tiles"]) == 2
    assert payload["lod_sizes"]["1"] == [192, 144]


@pytest.mark.asyncio
async def test_tile_manifest_emits_etag_and_cache_control(tmp_path: Path):
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/tile_manifest")
    assert resp.status_code == 200
    assert resp.headers.get("etag", "").startswith("thumb_hash:")
    assert "no-store" not in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_tile_manifest_404_when_thumbnails_missing(tmp_path: Path):
    """No 00_thumbnails/index.json → ArtifactMissing 404."""
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/tile_manifest")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_tile_manifest.py -v`
Expected: FAIL — `404 Not Found` because `routes/explorer.py` is not registered.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`:

```python
"""Explorer routes per backend design §1.2/§1.3 + mosaic-viewer §3-§4."""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import ArtifactMissing, ExplorerStateMissing
from flake_analysis.api.schemas.explorer import (
    ExplorerFlakeDetail,
    ExplorerFlakeRow,
    ExplorerFlakesResponse,
    SaveExplorerStateParams,
    SaveExplorerStateResult,
    TileManifest,
)
from flake_analysis.api.services.explorer_service import (
    build_flake_detail,
    build_flake_table,
    build_tile_manifest,
)
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["explorer"])


def _etag_for(manifest_obj: TileManifest) -> str:
    sig_part = ":".join(manifest_obj.signature[:2]) if manifest_obj.signature else ""
    return f"{manifest_obj.params_hash}:{sig_part}"


@router.get("/explorer/tile_manifest", response_model=TileManifest)
async def get_tile_manifest(
    project_id: str,
    response: Response,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return the canonical TileManifest. Cache 24h, immutable per (params_hash, signature)."""
    try:
        tm = build_tile_manifest(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))
    response.headers["ETag"] = _etag_for(tm)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return tm
```

Modify `src/flake_analysis/api/main.py`:

```python
from flake_analysis.api.routes import (
    health, version, projects, data, run, selector, clustering, explorer,
)
# ... inside create_app() ...
    app.include_router(explorer.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_tile_manifest.py -v`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py src/flake_analysis/api/main.py tests/api/test_data_explorer_tile_manifest.py
git commit -m "feat(api): GET /explorer/tile_manifest with ETag + Cache-Control"
```

---

#### Task 8: GET /explorer/grid

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_explorer_grid.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_explorer_grid.py
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_thumb_world(folder: Path) -> None:
    raw = folder / "raw"; raw.mkdir(parents=True)
    cache = folder / "00_thumbnails"; cache.mkdir(parents=True)
    image_id_to_stem = {0: "ix000_iy000", 1: "ix001_iy000"}
    for stem in image_id_to_stem.values():
        Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / f"{stem}.png")
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "h",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": image_id_to_stem,
    }))


@pytest.mark.asyncio
async def test_grid_returns_payload_with_tiles_and_signature(tmp_path: Path):
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/grid")
    assert resp.status_code == 200
    payload = resp.json()
    assert "grid_w" in payload
    assert "grid_h" in payload
    assert "lod_sizes" in payload
    assert "signature" in payload
    assert "tiles" in payload
    assert isinstance(payload["tiles"], list)


@pytest.mark.asyncio
async def test_grid_etag_matches_tile_manifest(tmp_path: Path):
    """Pinned decision #11: /explorer/grid is the canonical URL; same identity contract as tile_manifest."""
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        a = await ac.get("/api/v1/projects/local/explorer/tile_manifest")
        b = await ac.get("/api/v1/projects/local/explorer/grid")
    assert a.headers.get("etag") == b.headers.get("etag")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_grid.py -v`
Expected: FAIL — `404 Not Found` (route not registered).

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/explorer.py`:

```python
@router.get("/explorer/grid", response_model=TileManifest)
async def get_explorer_grid(
    project_id: str,
    response: Response,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Pinned decision #11: canonical alias of /tile_manifest per mosaic-viewer §4."""
    try:
        tm = build_tile_manifest(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))
    response.headers["ETag"] = _etag_for(tm)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return tm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_grid.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py tests/api/test_data_explorer_grid.py
git commit -m "feat(api): GET /explorer/grid (canonical mosaic-viewer URL)"
```

---

#### Task 9: GET /explorer/flakes (server-side filter)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_explorer_flakes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_explorer_flakes.py
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_clustering_world(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1, "20": 1, "21": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "cluster_id": [0, 0, 1, 1, 0],
        "posterior_p": [0.9, 0.8, 0.85, 0.7, 0.95],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "flake_id":  [100, 100, 100, 200, 200],
        "flake_size": [3, 3, 3, 2, 2],
        "image_id":  [0, 0, 0, 1, 1],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_flakes_no_filter_returns_all(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flakes")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    flake_ids = sorted(r["flake_id"] for r in payload["rows"])
    assert flake_ids == [100, 200]


@pytest.mark.asyncio
async def test_flakes_include_query_filters(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flakes?include=thin")
    assert resp.status_code == 200
    flake_ids = sorted(r["flake_id"] for r in resp.json()["rows"])
    assert flake_ids == [100, 200]


@pytest.mark.asyncio
async def test_flakes_exclude_query_filters(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flakes?exclude=thick")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []  # both flakes contain "thick"


@pytest.mark.asyncio
async def test_flakes_size_min_max(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/explorer/flakes?size_min=3&size_max=3"
        )
    assert resp.status_code == 200
    flake_ids = [r["flake_id"] for r in resp.json()["rows"]]
    assert flake_ids == [100]


@pytest.mark.asyncio
async def test_flakes_404_when_clustering_missing(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flakes")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_flakes.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/explorer.py`:

```python
@router.get("/explorer/flakes", response_model=ExplorerFlakesResponse)
async def get_explorer_flakes(
    project_id: str,
    include: str = Query("", description="Comma-separated cluster names"),
    exclude: str = Query("", description="Comma-separated cluster names"),
    size_min: int | None = Query(None, ge=1),
    size_max: int | None = Query(None, ge=1),
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Server-side filter per pinned decision #4."""
    inc = [s for s in include.split(",") if s] if include else []
    exc = [s for s in exclude.split(",") if s] if exclude else []
    try:
        df = build_flake_table(
            manifest.analysis_folder,
            include_labels=inc,
            exclude_labels=exc,
            size_min=size_min,
            size_max=size_max,
        )
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))

    rows = [
        ExplorerFlakeRow(
            flake_id=int(r["flake_id"]),
            image_id=int(r["image_id"]),
            domains=int(r["domains"]),
            groups=str(r["groups"]),
            distance=str(r["distance"]),
            clipped=str(r["clipped"]),
            **{"pass": True},
        )
        for _, r in df.iterrows()
    ]
    return ExplorerFlakesResponse(rows=rows, total=len(rows))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_flakes.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py tests/api/test_data_explorer_flakes.py
git commit -m "feat(api): GET /explorer/flakes with server-side include/exclude/size filter"
```

---

#### Task 10: GET /explorer/flake/{flake_id}

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_data_explorer_flake_detail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_data_explorer_flake_detail.py
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_clustering_world(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({
        "domain_id": [10, 11, 12],
        "cluster_id": [0, 0, 1],
        "posterior_p": [0.9, 0.8, 0.85],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12],
        "flake_id":  [100, 100, 100],
        "flake_size": [3, 3, 3],
        "image_id":  [7, 7, 7],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_flake_detail_returns_domain_ids_and_cluster_names(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/100")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["flake_id"] == 100
    assert payload["image_id"] == 7
    assert payload["domain_ids"] == [10, 11, 12]
    assert set(payload["cluster_names"]) == {"thin", "thick"}


@pytest.mark.asyncio
async def test_flake_detail_404_when_unknown(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_flake_detail_404_when_clustering_missing(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/100")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_flake_detail.py -v`
Expected: FAIL — `404 Not Found` (route missing).

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/explorer.py`:

```python
@router.get("/explorer/flake/{flake_id}", response_model=ExplorerFlakeDetail)
async def get_explorer_flake_detail(
    project_id: str,
    flake_id: int,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    try:
        return build_flake_detail(manifest.analysis_folder, flake_id=flake_id)
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))
    except KeyError:
        raise ArtifactMissing(missing=f"flake_id={flake_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_data_explorer_flake_detail.py -v`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py tests/api/test_data_explorer_flake_detail.py
git commit -m "feat(api): GET /explorer/flake/{id} returns domain_ids + cluster_names"
```

---

### Phase 4 — Backend save/state routes (synchronous JSON per pinned decision #12)

#### Task 11: POST /run/explorer/save_state (synchronous, per-project lock)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_explorer_save_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_explorer_save_state.py
"""save_state is SYNCHRONOUS JSON (pinned decision #12) — NOT SSE."""
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_for_save(folder: Path) -> None:
    """Need clustering+proximity steps committed for save_explorer_state to succeed."""
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 1,
        "groups": [{"id": 0, "name": "thin", "size": 1, "mean_rgb": [0, 0, 0]}],
        "assignments": {"10": 0}, "thresholds": {"0": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({"domain_id": [10], "cluster_id": [0], "posterior_p": [0.9]}).to_parquet(
        folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10], "flake_id": [100], "flake_size": [1], "image_id": [0]
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {
            "clustering": {"completed_at": "x", "params": {}, "params_hash": "ch",
                           "input_hashes": {}, "outputs": {}},
            "domain_proximity": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                 "input_hashes": {}, "outputs": {}},
        },
        "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_save_state_returns_json_200_not_sse(tmp_path: Path):
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": ["thin"],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": 1, "size_max": 50,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)

    assert resp.status_code == 200
    # Must NOT be SSE — content-type stays application/json
    assert resp.headers.get("content-type", "").startswith("application/json")
    payload = resp.json()
    assert "state_path" in payload
    assert payload["state_path"].endswith("explorer_state.json")
    assert payload["selected_count"] is None  # no selected_flake_ids in body


@pytest.mark.asyncio
async def test_save_state_persists_selected_flake_ids(tmp_path: Path):
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": [],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
        "selected_flake_ids": [100, 200, 300],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["selected_count"] == 3
    # selected_flakes.parquet was written
    sel_p = tmp_path / "06_explorer" / "selected_flakes.parquet"
    assert sel_p.exists()
    df = pd.read_parquet(sel_p)
    assert df["flake_id"].tolist() == [100, 200, 300]


@pytest.mark.asyncio
async def test_save_state_409_when_clustering_not_committed(tmp_path: Path):
    """Pipeline raises RuntimeError → route returns 409 prerequisite_missing."""
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {},  # No clustering / domain_proximity
        "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": [], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "prerequisite_missing"


@pytest.mark.asyncio
async def test_save_state_releases_lock_on_success(tmp_path: Path):
    """Two consecutive saves must both succeed (lock cleanly released)."""
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": ["thin"], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
        r2 = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_explorer_save_state.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/explorer.py`:

```python
from flake_analysis.api.errors import PrerequisiteMissing
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.pipeline.explorer import save_explorer_state, load_explorer_state


@router.post("/run/explorer/save_state", response_model=SaveExplorerStateResult)
async def post_save_explorer_state(
    project_id: str,
    params: SaveExplorerStateParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Synchronous save (pinned decision #12). NOT SSE.

    Wraps pipeline.explorer.save_explorer_state, with the per-project mutex
    held for the whole call (not lock+drain — synchronous JSON has no streaming).
    """
    async with acquire_project_lock(project_id):
        nf = params.neighbor_filter.model_dump()
        try:
            result = save_explorer_state(
                analysis_folder=manifest.analysis_folder,
                include_labels=params.include_labels,
                exclude_labels=params.exclude_labels,
                neighbor_filter=nf,
                selected_flake_ids=params.selected_flake_ids,
            )
        except RuntimeError as e:
            raise PrerequisiteMissing(reason=str(e))
    return SaveExplorerStateResult(
        state_path=result["state_path"],
        selected_count=result["selected_count"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_explorer_save_state.py -v`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py tests/api/test_run_explorer_save_state.py
git commit -m "feat(api): POST /run/explorer/save_state (synchronous JSON)"
```

---

#### Task 12: GET /run/explorer/state

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/explorer.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_run_explorer_get_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_explorer_get_state.py
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_with_saved_state(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "06_explorer").mkdir(parents=True)
    (folder / "06_explorer" / "explorer_state.json").write_text(json.dumps({
        "include_labels": ["thin"],
        "exclude_labels": [],
        "neighbor_filter": {"size_enabled": True, "size_min": 1, "size_max": 50,
                            "isolate_enabled": False, "d_isolate_px": 80.0,
                            "exclude_border": False},
        "saved_at": "2026-05-21T00:00:00Z",
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {
            "explorer": {"completed_at": "2026-05-21T00:00:00Z", "params": {},
                         "params_hash": "eh", "input_hashes": {}, "outputs": {}},
        },
        "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_get_state_returns_saved_payload(tmp_path: Path):
    _seed_with_saved_state(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/run/explorer/state")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["include_labels"] == ["thin"]
    assert payload["neighbor_filter"]["size_min"] == 1


@pytest.mark.asyncio
async def test_get_state_404_when_unsaved(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/run/explorer/state")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "explorer_state_missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_explorer_get_state.py -v`
Expected: FAIL — `404 Not Found` (route missing).

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/explorer.py`:

```python
@router.get("/run/explorer/state")
async def get_saved_explorer_state(
    project_id: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    state = load_explorer_state(manifest.analysis_folder)
    if state is None:
        raise ExplorerStateMissing()
    return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_run_explorer_get_state.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/explorer.py tests/api/test_run_explorer_get_state.py
git commit -m "feat(api): GET /run/explorer/state returns saved JSON or 404"
```

---

### Phase 5 — Backend static routes (`/static/thumbnails/lod{N}/{stem}.webp`, `/static/raw/{filename}`) with path-traversal guard + ETag + Cache-Control

#### Task 13: GET /static/thumbnails/lod{lod}/{stem}.webp

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/static.py`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/main.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_static_thumbnails.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_static_thumbnails.py
"""Static thumbnail route tests — happy path + path traversal + ETag + Cache-Control."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_thumbs(folder: Path) -> None:
    cache = folder / "00_thumbnails"
    for lod, (w, h) in [(0, (64, 48)), (1, (192, 144))]:
        d = cache / f"lod{lod}"
        d.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.zeros((h, w, 3), dtype=np.uint8)).save(d / "ix003_iy017.webp")
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48], "1": [192, 144]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "thumbnails_cache_dir": str(cache),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "th",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_thumbnail_happy_path_returns_webp_bytes(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/thumbnails/lod0/ix003_iy017.webp")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/webp", "image/webp; charset=utf-8")
    # Bytes start with RIFF for WebP
    assert resp.content[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_thumbnail_emits_etag_and_cache_control(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/thumbnails/lod0/ix003_iy017.webp")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert etag.startswith("th:")  # params_hash:signature


@pytest.mark.asyncio
async def test_thumbnail_rejects_dot_dot_in_stem(tmp_path: Path):
    """The headline path-traversal negative test."""
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/..%2F..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)
    assert resp.status_code != 200


@pytest.mark.asyncio
async def test_thumbnail_rejects_absolute_path_in_stem(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_thumbnail_404_when_lod_dir_missing(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod9/ix003_iy017.webp"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thumbnail_missing"


@pytest.mark.asyncio
async def test_thumbnail_404_when_stem_missing(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/missing_stem.webp"
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_thumbnails.py -v`
Expected: FAIL — `404` (route missing) for the happy path.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/static.py`:

```python
"""Static asset routes per backend design §1.4 + mosaic-viewer §3.

All inputs flow through services.path_safety.safe_join — any traversal
attempt becomes a 400 ParamsInvalid before disk is touched.
"""
from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import RawImageMissing, ThumbnailMissing
from flake_analysis.api.services.path_safety import safe_join
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["static"])


def _read_thumb_metadata(folder: Path) -> tuple[str, list[str]]:
    """Return (params_hash, signature) for ETag construction."""
    manifest_p = folder / "manifest.json"
    params_hash = ""
    signature: list[str] = []
    if manifest_p.exists():
        m = json.loads(manifest_p.read_text(encoding="utf-8"))
        params_hash = m.get("steps", {}).get("thumbnails", {}).get("params_hash", "")
    idx_p = folder / "00_thumbnails" / "index.json"
    if idx_p.exists():
        idx = json.loads(idx_p.read_text(encoding="utf-8"))
        signature = list(idx.get("signature", []))
    return params_hash, signature


def _thumb_etag(folder: Path) -> str:
    ph, sig = _read_thumb_metadata(folder)
    return f"{ph}:{':'.join(sig[:2])}" if sig else ph


@router.get("/static/thumbnails/lod{lod}/{stem}.webp")
async def get_thumbnail(
    project_id: str,
    lod: int,
    stem: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    folder = Path(manifest.analysis_folder)
    cache = folder / "00_thumbnails"
    # safe_join validates EVERY part — the leading "lod{lod}" segment is
    # constructed server-side so we only need to validate `stem`.
    safe_stem = safe_join(cache / f"lod{lod}", f"{stem}.webp")
    if not safe_stem.exists():
        raise ThumbnailMissing(lod=lod, stem=stem)

    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),
    }
    return FileResponse(str(safe_stem), media_type="image/webp", headers=headers)
```

Modify `src/flake_analysis/api/main.py`:

```python
from flake_analysis.api.routes import (
    health, version, projects, data, run, selector, clustering, explorer, static,
)
# inside create_app()
    app.include_router(static.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_thumbnails.py -v`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/static.py src/flake_analysis/api/main.py tests/api/test_static_thumbnails.py
git commit -m "feat(api): GET /static/thumbnails/lod{N}/{stem}.webp with traversal guard + ETag"
```

---

#### Task 14: GET /static/raw/{filename}

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/static.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/api/test_static_raw.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_static_raw.py
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_raw(folder: Path) -> None:
    raw = folder / "raw"; raw.mkdir(parents=True)
    Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / "ix003_iy017.png")
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "rh",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {0: "ix003_iy017"},
    }))
    (folder / "00_thumbnails").mkdir(exist_ok=True)
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1, "lod_sizes": {}, "signature": ["raw_sig"],
    }))


@pytest.mark.asyncio
async def test_raw_happy_path_returns_png_bytes(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/ix003_iy017.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/png", "image/png; charset=utf-8")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_raw_emits_etag_and_cache_control(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/ix003_iy017.png")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert "rh" in etag


@pytest.mark.asyncio
async def test_raw_rejects_dot_dot(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/raw/..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_rejects_absolute(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/raw/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_404_when_filename_missing(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/nonexistent.png")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "raw_image_missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_raw.py -v`
Expected: FAIL — `404` (route missing).

- [ ] **Step 3: Write minimal implementation**

Append to `src/flake_analysis/api/routes/static.py`:

```python
import mimetypes


@router.get("/static/raw/{filename}")
async def get_raw(
    project_id: str,
    filename: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Pinned decision #3: raw served as-is (no transforms, no Y-flip)."""
    folder = Path(manifest.analysis_folder)
    raw_root = Path(json.loads((folder / "manifest.json").read_text())["raw_images_dir"])
    safe_path = safe_join(raw_root, filename)
    if not safe_path.exists():
        raise RawImageMissing(filename=filename)

    media_type, _ = mimetypes.guess_type(str(safe_path))
    if media_type is None:
        media_type = "application/octet-stream"

    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),  # share the thumbnails identity for now
    }
    return FileResponse(str(safe_path), media_type=media_type, headers=headers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_raw.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/static.py tests/api/test_static_raw.py
git commit -m "feat(api): GET /static/raw/{filename} with traversal guard + ETag"
```

---

### Phase 6 — Frontend foundations (Zustand slice + API client + hooks)

#### Task 15: explorerSlice with Sets, NeighborFilter, render toggles, viewport

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/explorerSlice.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/state/__tests__/explorerSlice.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/state/__tests__/explorerSlice.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import {
  useExplorerStore,
  resetExplorerStore,
} from '@/state/explorerSlice'

beforeEach(() => {
  resetExplorerStore()
})

describe('explorerSlice — initial state', () => {
  it('starts with empty include/exclude sets and null selections', () => {
    const s = useExplorerStore.getState()
    expect(s.includeLabels.size).toBe(0)
    expect(s.excludeLabels.size).toBe(0)
    expect(s.selectedFlakeId).toBeNull()
    expect(s.focusFlakeId).toBeNull()
    expect(s.lodChoice).toBe('auto')
    expect(s.viewportState).toBeNull()
  })

  it('defaults render toggles to (true, false, false, true) per Plan v34', () => {
    const t = useExplorerStore.getState().renderToggles
    expect(t.flake_bbox).toBe(true)
    expect(t.flake_outline).toBe(false)
    expect(t.island_bbox).toBe(false)
    expect(t.island_outline).toBe(true)
  })

  it('defaults neighborFilter to all-null + exclude_border_clipped=false', () => {
    const nf = useExplorerStore.getState().neighborFilter
    expect(nf.sizeMin).toBeNull()
    expect(nf.sizeMax).toBeNull()
    expect(nf.isolationMin).toBeNull()
    expect(nf.excludeBorderClipped).toBe(false)
  })
})

describe('explorerSlice — Include/Exclude actions', () => {
  it('addInclude inserts the label as a new Set entry', () => {
    useExplorerStore.getState().addInclude('thin')
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(true)
  })

  it('addInclude removes the same label from excludeLabels (mutual exclusion)', () => {
    useExplorerStore.getState().addExclude('noise')
    useExplorerStore.getState().addInclude('noise')
    expect(useExplorerStore.getState().includeLabels.has('noise')).toBe(true)
    expect(useExplorerStore.getState().excludeLabels.has('noise')).toBe(false)
  })

  it('removeInclude removes the label only from includeLabels', () => {
    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().removeInclude('thin')
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
  })

  it('addExclude removes the same label from includeLabels (mutual exclusion)', () => {
    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().addExclude('thin')
    expect(useExplorerStore.getState().excludeLabels.has('thin')).toBe(true)
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
  })

  it('clearLabels resets both Sets', () => {
    useExplorerStore.getState().addInclude('a')
    useExplorerStore.getState().addExclude('b')
    useExplorerStore.getState().clearLabels()
    expect(useExplorerStore.getState().includeLabels.size).toBe(0)
    expect(useExplorerStore.getState().excludeLabels.size).toBe(0)
  })
})

describe('explorerSlice — neighborFilter actions', () => {
  it('setSizeRange writes both bounds atomically', () => {
    useExplorerStore.getState().setSizeRange(2, 10)
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBe(2)
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBe(10)
  })

  it('setSizeRange(null, null) clears both bounds', () => {
    useExplorerStore.getState().setSizeRange(2, 10)
    useExplorerStore.getState().setSizeRange(null, null)
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBeNull()
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBeNull()
  })

  it('setIsolationMin writes the isolation threshold', () => {
    useExplorerStore.getState().setIsolationMin(80)
    expect(useExplorerStore.getState().neighborFilter.isolationMin).toBe(80)
  })

  it('setExcludeBorderClipped flips the boolean', () => {
    useExplorerStore.getState().setExcludeBorderClipped(true)
    expect(useExplorerStore.getState().neighborFilter.excludeBorderClipped).toBe(true)
  })
})

describe('explorerSlice — selection + viewport + LOD + toggles', () => {
  it('setSelectedFlakeId / setFocusFlakeId update independently', () => {
    useExplorerStore.getState().setSelectedFlakeId(42)
    useExplorerStore.getState().setFocusFlakeId(7)
    expect(useExplorerStore.getState().selectedFlakeId).toBe(42)
    expect(useExplorerStore.getState().focusFlakeId).toBe(7)
  })

  it('setLodChoice accepts auto and 0..3', () => {
    useExplorerStore.getState().setLodChoice(2)
    expect(useExplorerStore.getState().lodChoice).toBe(2)
    useExplorerStore.getState().setLodChoice('auto')
    expect(useExplorerStore.getState().lodChoice).toBe('auto')
  })

  it('setViewportState stores and clears', () => {
    useExplorerStore.getState().setViewportState({ center: [0.5, 0.5], zoom: 1.0 })
    expect(useExplorerStore.getState().viewportState).toEqual({
      center: [0.5, 0.5], zoom: 1.0,
    })
    useExplorerStore.getState().setViewportState(null)
    expect(useExplorerStore.getState().viewportState).toBeNull()
  })

  it('toggleRender flips a single toggle key', () => {
    useExplorerStore.getState().toggleRender('flake_outline')
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(true)
    useExplorerStore.getState().toggleRender('flake_outline')
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(false)
  })

  it('resetExplorerStore returns every field to default', () => {
    useExplorerStore.getState().addInclude('a')
    useExplorerStore.getState().setSelectedFlakeId(1)
    useExplorerStore.getState().setLodChoice(3)
    useExplorerStore.getState().setSizeRange(1, 99)
    resetExplorerStore()
    const s = useExplorerStore.getState()
    expect(s.includeLabels.size).toBe(0)
    expect(s.selectedFlakeId).toBeNull()
    expect(s.lodChoice).toBe('auto')
    expect(s.neighborFilter.sizeMin).toBeNull()
    expect(s.renderToggles.flake_bbox).toBe(true)
    expect(s.renderToggles.island_outline).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`:
`npx vitest run src/state/__tests__/explorerSlice.test.ts`
Expected: FAIL — `Cannot find module '@/state/explorerSlice'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// web/src/state/explorerSlice.ts
// Zustand slice per frontend-design §3.5 + Plan 4 brief.
import { create } from 'zustand'

export type LodChoice = 'auto' | 0 | 1 | 2 | 3

export interface NeighborFilter {
  sizeMin: number | null
  sizeMax: number | null
  isolationMin: number | null
  excludeBorderClipped: boolean
}

export interface ViewportState {
  center: [number, number]
  zoom: number
}

export interface RenderToggles {
  flake_bbox: boolean       // default TRUE
  flake_outline: boolean    // default false
  island_bbox: boolean      // default false
  island_outline: boolean   // default TRUE
}

const DEFAULT_TOGGLES: RenderToggles = {
  flake_bbox: true,
  flake_outline: false,
  island_bbox: false,
  island_outline: true,
}

const DEFAULT_NEIGHBOR_FILTER: NeighborFilter = {
  sizeMin: null,
  sizeMax: null,
  isolationMin: null,
  excludeBorderClipped: false,
}

export interface ExplorerState {
  includeLabels: Set<string>
  excludeLabels: Set<string>
  neighborFilter: NeighborFilter
  selectedFlakeId: number | null
  focusFlakeId: number | null
  lodChoice: LodChoice
  viewportState: ViewportState | null
  renderToggles: RenderToggles

  addInclude(label: string): void
  removeInclude(label: string): void
  addExclude(label: string): void
  removeExclude(label: string): void
  clearLabels(): void

  setSizeRange(min: number | null, max: number | null): void
  setIsolationMin(v: number | null): void
  setExcludeBorderClipped(v: boolean): void

  setSelectedFlakeId(id: number | null): void
  setFocusFlakeId(id: number | null): void
  setLodChoice(c: LodChoice): void
  setViewportState(v: ViewportState | null): void
  toggleRender(key: keyof RenderToggles): void
}

export const useExplorerStore = create<ExplorerState>((set, get) => ({
  includeLabels: new Set<string>(),
  excludeLabels: new Set<string>(),
  neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
  selectedFlakeId: null,
  focusFlakeId: null,
  lodChoice: 'auto',
  viewportState: null,
  renderToggles: { ...DEFAULT_TOGGLES },

  addInclude(label) {
    set((s) => {
      const inc = new Set(s.includeLabels); inc.add(label)
      const exc = new Set(s.excludeLabels); exc.delete(label)
      return { includeLabels: inc, excludeLabels: exc }
    })
  },
  removeInclude(label) {
    set((s) => {
      const inc = new Set(s.includeLabels); inc.delete(label)
      return { includeLabels: inc }
    })
  },
  addExclude(label) {
    set((s) => {
      const exc = new Set(s.excludeLabels); exc.add(label)
      const inc = new Set(s.includeLabels); inc.delete(label)
      return { excludeLabels: exc, includeLabels: inc }
    })
  },
  removeExclude(label) {
    set((s) => {
      const exc = new Set(s.excludeLabels); exc.delete(label)
      return { excludeLabels: exc }
    })
  },
  clearLabels() {
    set({ includeLabels: new Set(), excludeLabels: new Set() })
  },

  setSizeRange(min, max) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, sizeMin: min, sizeMax: max } }))
  },
  setIsolationMin(v) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, isolationMin: v } }))
  },
  setExcludeBorderClipped(v) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, excludeBorderClipped: v } }))
  },

  setSelectedFlakeId(id) { set({ selectedFlakeId: id }) },
  setFocusFlakeId(id) { set({ focusFlakeId: id }) },
  setLodChoice(c) { set({ lodChoice: c }) },
  setViewportState(v) { set({ viewportState: v }) },
  toggleRender(key) {
    set((s) => ({ renderToggles: { ...s.renderToggles, [key]: !s.renderToggles[key] } }))
  },
}))

export function resetExplorerStore(): void {
  useExplorerStore.setState({
    includeLabels: new Set<string>(),
    excludeLabels: new Set<string>(),
    neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
    selectedFlakeId: null,
    focusFlakeId: null,
    lodChoice: 'auto',
    viewportState: null,
    renderToggles: { ...DEFAULT_TOGGLES },
  }, false)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`:
`npx vitest run src/state/__tests__/explorerSlice.test.ts`
Expected: ALL PASS (16 cases).

- [ ] **Step 5: Commit**

```bash
git add web/src/state/explorerSlice.ts web/src/state/__tests__/explorerSlice.test.ts
git commit -m "feat(web): add explorerSlice with Sets, NeighborFilter, viewport, toggles"
```

---

#### Task 16: Explorer API client (`api/explorer.ts`)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/api/explorer.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/api/__tests__/explorer.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/api/__tests__/explorer.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchTileManifest,
  fetchExplorerGrid,
  fetchExplorerFlakes,
  fetchExplorerFlakeDetail,
  saveExplorerState,
  getExplorerState,
} from '@/api/explorer'
import { ApiError } from '@/api/selector'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function makeFetch(url_to_resp: Record<string, () => Response>) {
  return vi.fn(async (url: string) => {
    for (const [k, fn] of Object.entries(url_to_resp)) {
      if (url.includes(k)) return fn()
    }
    throw new Error(`unmocked URL: ${url}`)
  })
}

describe('api/explorer — fetchTileManifest', () => {
  it('returns the parsed JSON payload on 200', async () => {
    const tm = {
      grid_w: 2, grid_h: 1,
      lod_sizes: { '0': [64, 48] }, signature: ['s0', 's1'],
      params_hash: 'h', tiles: [],
    }
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/tile_manifest': () =>
        new Response(JSON.stringify(tm), { status: 200,
          headers: { 'content-type': 'application/json' } }),
    }))
    const out = await fetchTileManifest('local')
    expect(out.grid_w).toBe(2)
    expect(out.params_hash).toBe('h')
  })

  it('throws ApiError on 404 with the envelope code', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/tile_manifest': () =>
        new Response(JSON.stringify({
          error: { code: 'artifact_missing', message: 'no thumbs', details: {}, request_id: 'r' },
        }), { status: 404, headers: { 'content-type': 'application/json' } }),
    }))
    await expect(fetchTileManifest('local')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('api/explorer — fetchExplorerGrid', () => {
  it('hits /explorer/grid', async () => {
    const f = makeFetch({
      '/explorer/grid': () =>
        new Response(JSON.stringify({
          grid_w: 1, grid_h: 1, lod_sizes: {}, signature: [], params_hash: 'g', tiles: [],
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    })
    vi.stubGlobal('fetch', f)
    const out = await fetchExplorerGrid('local')
    expect(out.params_hash).toBe('g')
    expect(f).toHaveBeenCalledWith(
      expect.stringContaining('/explorer/grid'),
      expect.any(Object),
    )
  })
})

describe('api/explorer — fetchExplorerFlakes', () => {
  it('encodes include/exclude/size_min/size_max as query params', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    await fetchExplorerFlakes('local', {
      include: ['thin', 'thick'], exclude: ['noise'],
      sizeMin: 1, sizeMax: 50,
    })
    expect(captured[0]).toContain('include=thin%2Cthick')
    expect(captured[0]).toContain('exclude=noise')
    expect(captured[0]).toContain('size_min=1')
    expect(captured[0]).toContain('size_max=50')
  })

  it('omits empty filters from the query', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    await fetchExplorerFlakes('local', {
      include: [], exclude: [], sizeMin: null, sizeMax: null,
    })
    expect(captured[0]).not.toContain('include=')
    expect(captured[0]).not.toContain('exclude=')
    expect(captured[0]).not.toContain('size_min=')
  })
})

describe('api/explorer — fetchExplorerFlakeDetail', () => {
  it('hits /explorer/flake/{id}', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/flake/42': () =>
        new Response(JSON.stringify({
          flake_id: 42, image_id: 7,
          domain_ids: [10, 11], cluster_names: ['thin'],
          bbox_xy: [], mask_stats: {},
          distance_px: null, isolation_px: null,
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    }))
    const out = await fetchExplorerFlakeDetail('local', 42)
    expect(out.flake_id).toBe(42)
    expect(out.cluster_names).toEqual(['thin'])
  })
})

describe('api/explorer — saveExplorerState', () => {
  it('POSTs JSON and returns the result envelope', async () => {
    const f = vi.fn(async (url: string, init: RequestInit) => {
      expect(init.method).toBe('POST')
      return new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 3,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', f)
    const out = await saveExplorerState('local', {
      include_labels: ['thin'], exclude_labels: [],
      neighbor_filter: { size_min: 1, size_max: 50,
                         isolation_min: null, exclude_border_clipped: false },
      selected_flake_ids: [1, 2, 3],
    })
    expect(out.selected_count).toBe(3)
  })

  it('throws ApiError on 409', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'commit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    await expect(saveExplorerState('local', {
      include_labels: [], exclude_labels: [],
      neighbor_filter: { size_min: null, size_max: null,
                         isolation_min: null, exclude_border_clipped: false },
    })).rejects.toBeInstanceOf(ApiError)
  })
})

describe('api/explorer — getExplorerState', () => {
  it('returns the saved JSON on 200', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/run/explorer/state': () =>
        new Response(JSON.stringify({
          include_labels: ['thin'], exclude_labels: [],
          neighbor_filter: {}, saved_at: '2026-05-21T00:00:00Z',
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    }))
    const out = await getExplorerState('local')
    expect(out.include_labels).toEqual(['thin'])
  })

  it('throws ApiError with code explorer_state_missing on 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'explorer_state_missing', message: '404',
                 details: {}, request_id: 'r' },
      }), { status: 404, headers: { 'content-type': 'application/json' } })
    ))
    await expect(getExplorerState('local')).rejects.toBeInstanceOf(ApiError)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/api/__tests__/explorer.test.ts`
Expected: FAIL — `Cannot find module '@/api/explorer'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// web/src/api/explorer.ts
import { ApiError } from '@/api/selector'

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: any = null
    try { envelope = await resp.json() } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id,
    )
  }
  return (await resp.json()) as T
}

export interface TileManifestEntryDto {
  image_id: number
  stem: string
  col: number
  row: number
  width_px: number
  height_px: number
  lod_sizes: Record<string, [number, number]>
}

export interface TileManifestDto {
  grid_w: number
  grid_h: number
  lod_sizes: Record<string, [number, number]>
  signature: string[]
  params_hash: string
  tiles: TileManifestEntryDto[]
}

export interface ExplorerFlakeRowDto {
  flake_id: number
  image_id: number
  domains: number
  groups: string
  distance: string
  clipped: string
  pass: boolean
}

export interface ExplorerFlakesResponseDto {
  rows: ExplorerFlakeRowDto[]
  total: number
}

export interface ExplorerFlakeDetailDto {
  flake_id: number
  image_id: number
  domain_ids: number[]
  cluster_names: string[]
  bbox_xy: number[]
  mask_stats: Record<string, number>
  distance_px: number | null
  isolation_px: number | null
}

export interface SaveExplorerStateBody {
  include_labels: string[]
  exclude_labels: string[]
  neighbor_filter: {
    size_min: number | null
    size_max: number | null
    isolation_min: number | null
    exclude_border_clipped: boolean
  }
  selected_flake_ids?: number[]
}

export interface SaveExplorerStateResultDto {
  state_path: string
  selected_count: number | null
}

export async function fetchTileManifest(projectId: string): Promise<TileManifestDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/tile_manifest`,
    { headers: { Accept: 'application/json' } },
  )
  return unwrap<TileManifestDto>(resp)
}

export async function fetchExplorerGrid(projectId: string): Promise<TileManifestDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/grid`,
    { headers: { Accept: 'application/json' } },
  )
  return unwrap<TileManifestDto>(resp)
}

export interface ExplorerFlakesQuery {
  include: string[]
  exclude: string[]
  sizeMin: number | null
  sizeMax: number | null
}

export async function fetchExplorerFlakes(
  projectId: string,
  q: ExplorerFlakesQuery,
): Promise<ExplorerFlakesResponseDto> {
  const params = new URLSearchParams()
  if (q.include.length > 0) params.set('include', q.include.join(','))
  if (q.exclude.length > 0) params.set('exclude', q.exclude.join(','))
  if (q.sizeMin !== null) params.set('size_min', String(q.sizeMin))
  if (q.sizeMax !== null) params.set('size_max', String(q.sizeMax))
  const qs = params.toString()
  const url = `/api/v1/projects/${projectId}/explorer/flakes${qs ? `?${qs}` : ''}`
  const resp = await fetch(url, { headers: { Accept: 'application/json' } })
  return unwrap<ExplorerFlakesResponseDto>(resp)
}

export async function fetchExplorerFlakeDetail(
  projectId: string, flakeId: number,
): Promise<ExplorerFlakeDetailDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/flake/${flakeId}`,
    { headers: { Accept: 'application/json' } },
  )
  return unwrap<ExplorerFlakeDetailDto>(resp)
}

export async function saveExplorerState(
  projectId: string, body: SaveExplorerStateBody,
): Promise<SaveExplorerStateResultDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/run/explorer/save_state`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    },
  )
  return unwrap<SaveExplorerStateResultDto>(resp)
}

export async function getExplorerState(projectId: string): Promise<any> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/run/explorer/state`,
    { headers: { Accept: 'application/json' } },
  )
  return unwrap<any>(resp)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/api/__tests__/explorer.test.ts`
Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/explorer.ts web/src/api/__tests__/explorer.test.ts
git commit -m "feat(web): add typed Explorer API client (tile_manifest/flakes/state)"
```

---

#### Task 17: useTileManifest TanStack hook

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useTileManifest.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useExplorerGrid.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useTileManifest.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useTileManifest.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTileManifest } from '@/hooks/useTileManifest'
import { useExplorerGrid } from '@/hooks/useExplorerGrid'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useTileManifest', () => {
  it('returns the parsed TileManifest on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        grid_w: 4, grid_h: 3,
        lod_sizes: { '0': [64, 48] }, signature: ['s0', 's1'],
        params_hash: 'h', tiles: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useTileManifest('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.grid_w).toBe(4)
    expect(result.current.data?.params_hash).toBe('h')
  })

  it('exposes the ApiError code on failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'artifact_missing', message: '404',
                 details: {}, request_id: 'r' },
      }), { status: 404, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useTileManifest('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any)?.code).toBe('artifact_missing')
  })
})

describe('useExplorerGrid', () => {
  it('returns the parsed grid payload', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        grid_w: 1, grid_h: 1, lod_sizes: {}, signature: [],
        params_hash: 'g', tiles: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useExplorerGrid('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.params_hash).toBe('g')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useTileManifest.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```typescript
// web/src/hooks/useTileManifest.ts
import { useQuery } from '@tanstack/react-query'
import { fetchTileManifest, type TileManifestDto } from '@/api/explorer'

export function useTileManifest(projectId: string) {
  return useQuery<TileManifestDto>({
    queryKey: ['explorer', 'tile_manifest', projectId],
    queryFn: () => fetchTileManifest(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
```

```typescript
// web/src/hooks/useExplorerGrid.ts
import { useQuery } from '@tanstack/react-query'
import { fetchExplorerGrid, type TileManifestDto } from '@/api/explorer'

export function useExplorerGrid(projectId: string) {
  return useQuery<TileManifestDto>({
    queryKey: ['explorer', 'grid', projectId],
    queryFn: () => fetchExplorerGrid(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useTileManifest.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useTileManifest.ts web/src/hooks/useExplorerGrid.ts web/src/hooks/__tests__/useTileManifest.test.tsx
git commit -m "feat(web): add useTileManifest + useExplorerGrid TanStack hooks"
```

---

#### Task 18: useExplorerFlakes (filtered query) + useExplorerFlakeDetail

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useExplorerFlakes.ts`
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useExplorerFlakeDetail.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useExplorerFlakes.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useExplorerFlakes.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useExplorerFlakes } from '@/hooks/useExplorerFlakes'
import { useExplorerFlakeDetail } from '@/hooks/useExplorerFlakeDetail'

beforeEach(() => { vi.unstubAllGlobals() })

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useExplorerFlakes', () => {
  it('passes include/exclude/size to the URL and returns rows', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({
        rows: [{ flake_id: 1, image_id: 0, domains: 3,
                 groups: 'thin', distance: '—', clipped: 'no', pass: true }],
        total: 1,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const { result } = renderHook(
      () => useExplorerFlakes('local', {
        include: ['thin'], exclude: [], sizeMin: 1, sizeMax: 50,
      }),
      { wrapper: wrap() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.total).toBe(1)
    expect(captured[0]).toContain('include=thin')
    expect(captured[0]).toContain('size_min=1')
    expect(captured[0]).toContain('size_max=50')
  })

  it('refetches when filter args change (different queryKey)', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const { result, rerender } = renderHook(
      ({ q }: { q: { include: string[]; exclude: string[];
                     sizeMin: number | null; sizeMax: number | null } }) =>
        useExplorerFlakes('local', q),
      {
        wrapper: wrap(),
        initialProps: { q: { include: ['thin'], exclude: [], sizeMin: null, sizeMax: null } },
      },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    rerender({ q: { include: ['thick'], exclude: [], sizeMin: null, sizeMax: null } })
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2))
    expect(calls[calls.length - 1]).toContain('include=thick')
  })
})

describe('useExplorerFlakeDetail', () => {
  it('is disabled when flakeId is null', async () => {
    const f = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(
      () => useExplorerFlakeDetail('local', null),
      { wrapper: wrap() },
    )
    // disabled query: never fires
    await new Promise((r) => setTimeout(r, 30))
    expect(f).not.toHaveBeenCalled()
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('fetches when flakeId is non-null', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flake_id: 42, image_id: 7, domain_ids: [10], cluster_names: ['thin'],
        bbox_xy: [], mask_stats: {}, distance_px: null, isolation_px: null,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(
      () => useExplorerFlakeDetail('local', 42),
      { wrapper: wrap() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.flake_id).toBe(42)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useExplorerFlakes.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```typescript
// web/src/hooks/useExplorerFlakes.ts
import { useQuery } from '@tanstack/react-query'
import {
  fetchExplorerFlakes,
  type ExplorerFlakesQuery,
  type ExplorerFlakesResponseDto,
} from '@/api/explorer'

export function useExplorerFlakes(projectId: string, q: ExplorerFlakesQuery) {
  return useQuery<ExplorerFlakesResponseDto>({
    queryKey: ['explorer', 'flakes', projectId,
               [...q.include].sort().join(','),
               [...q.exclude].sort().join(','),
               q.sizeMin, q.sizeMax],
    queryFn: () => fetchExplorerFlakes(projectId, q),
    staleTime: Infinity,
    retry: false,
  })
}
```

```typescript
// web/src/hooks/useExplorerFlakeDetail.ts
import { useQuery } from '@tanstack/react-query'
import {
  fetchExplorerFlakeDetail,
  type ExplorerFlakeDetailDto,
} from '@/api/explorer'

export function useExplorerFlakeDetail(projectId: string, flakeId: number | null) {
  return useQuery<ExplorerFlakeDetailDto>({
    queryKey: ['explorer', 'flake', projectId, flakeId],
    queryFn: () => fetchExplorerFlakeDetail(projectId, flakeId as number),
    enabled: flakeId !== null,
    staleTime: Infinity,
    retry: false,
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useExplorerFlakes.test.tsx`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useExplorerFlakes.ts web/src/hooks/useExplorerFlakeDetail.ts web/src/hooks/__tests__/useExplorerFlakes.test.tsx
git commit -m "feat(web): add useExplorerFlakes (filtered) + useExplorerFlakeDetail hooks"
```

---

#### Task 19: useSaveExplorerState mutation

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/useSaveExplorerState.ts`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/hooks/__tests__/useSaveExplorerState.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/hooks/__tests__/useSaveExplorerState.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useSaveExplorerState } from '@/hooks/useSaveExplorerState'

beforeEach(() => { vi.unstubAllGlobals() })

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    qc,
    Wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  }
}

describe('useSaveExplorerState', () => {
  it('runs the POST and resolves with the result envelope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 2,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { Wrapper } = wrap()
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      await result.current.mutateAsync({
        include_labels: ['thin'], exclude_labels: [],
        neighbor_filter: { size_min: null, size_max: null,
                           isolation_min: null, exclude_border_clipped: false },
        selected_flake_ids: [1, 2],
      })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.selected_count).toBe(2)
  })

  it('exposes ApiError on 409', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'fit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    const { Wrapper } = wrap()
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      try {
        await result.current.mutateAsync({
          include_labels: [], exclude_labels: [],
          neighbor_filter: { size_min: null, size_max: null,
                             isolation_min: null, exclude_border_clipped: false },
        })
      } catch { /* expected */ }
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any)?.code).toBe('prerequisite_missing')
  })

  it('invalidates the explorer state query on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: null,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { qc, Wrapper } = wrap()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      await result.current.mutateAsync({
        include_labels: [], exclude_labels: [],
        neighbor_filter: { size_min: null, size_max: null,
                           isolation_min: null, exclude_border_clipped: false },
      })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(spy).toHaveBeenCalledWith({ queryKey: ['explorer', 'state', 'local'] })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/__tests__/useSaveExplorerState.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```typescript
// web/src/hooks/useSaveExplorerState.ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  saveExplorerState,
  type SaveExplorerStateBody,
  type SaveExplorerStateResultDto,
} from '@/api/explorer'

export function useSaveExplorerState(projectId: string) {
  const qc = useQueryClient()
  return useMutation<SaveExplorerStateResultDto, unknown, SaveExplorerStateBody>({
    mutationKey: ['explorer', 'save_state', projectId],
    mutationFn: (body) => saveExplorerState(projectId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['explorer', 'state', projectId] })
    },
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/__tests__/useSaveExplorerState.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useSaveExplorerState.ts web/src/hooks/__tests__/useSaveExplorerState.test.tsx
git commit -m "feat(web): add useSaveExplorerState mutation with invalidation"
```

---

### Phase 7 — Frontend control panels (Right Rail components)

#### Task 20: ClusterIncludeExcludePicker

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/ClusterIncludeExcludePicker.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClusterIncludeExcludePicker } from '@/components/explorer/ClusterIncludeExcludePicker'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('ClusterIncludeExcludePicker', () => {
  it('renders one checkbox per available label in the Include column', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin', 'thick', 'noise']} />)
    expect(screen.getByRole('checkbox', { name: /Include thin/i })).not.toBeNull()
    expect(screen.getByRole('checkbox', { name: /Include thick/i })).not.toBeNull()
    expect(screen.getByRole('checkbox', { name: /Include noise/i })).not.toBeNull()
  })

  it('renders one checkbox per available label in the Exclude column', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    expect(screen.getByRole('checkbox', { name: /Exclude thin/i })).not.toBeNull()
  })

  it('clicking Include adds the label to includeLabels', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Include thin/i }))
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(true)
  })

  it('clicking Exclude removes from includeLabels (mutual exclusion)', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Include thin/i }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Exclude thin/i }))
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
    expect(useExplorerStore.getState().excludeLabels.has('thin')).toBe(true)
  })

  it('shows a red italic conflict caption when the same label is in both Sets', () => {
    // This shouldn't happen naturally (mutual exclusion enforces it), but
    // we test the rendering surface in case external code mutates the store.
    useExplorerStore.setState({
      includeLabels: new Set(['conflict']),
      excludeLabels: new Set(['conflict']),
    })
    render(<ClusterIncludeExcludePicker availableLabels={['conflict']} />)
    const caption = screen.getByText(/Conflict.*conflict/i)
    expect(caption).not.toBeNull()
    const style = window.getComputedStyle(caption as HTMLElement)
    expect(style.color).toMatch(/198|c62828|rgb/i)  // tolerant: red shade
    expect(style.fontStyle).toBe('italic')
  })

  it('renders empty state when availableLabels is empty', () => {
    render(<ClusterIncludeExcludePicker availableLabels={[]} />)
    expect(screen.getByText(/no clusters available/i)).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/ClusterIncludeExcludePicker.tsx
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  availableLabels: string[]
}

export function ClusterIncludeExcludePicker({ availableLabels }: Props) {
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const addInclude = useExplorerStore((s) => s.addInclude)
  const removeInclude = useExplorerStore((s) => s.removeInclude)
  const addExclude = useExplorerStore((s) => s.addExclude)
  const removeExclude = useExplorerStore((s) => s.removeExclude)

  if (availableLabels.length === 0) {
    return (
      <div role="region" aria-label="cluster picker">
        <em>No clusters available. Commit clustering first.</em>
      </div>
    )
  }

  const conflicts = availableLabels.filter((n) => include.has(n) && exclude.has(n))

  return (
    <div role="region" aria-label="cluster picker">
      <fieldset>
        <legend>Include</legend>
        {availableLabels.map((n) => (
          <label key={`inc-${n}`}>
            <input
              type="checkbox"
              aria-label={`Include ${n}`}
              checked={include.has(n)}
              onChange={(e) => e.target.checked ? addInclude(n) : removeInclude(n)}
            />
            {n}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Exclude</legend>
        {availableLabels.map((n) => (
          <label key={`exc-${n}`}>
            <input
              type="checkbox"
              aria-label={`Exclude ${n}`}
              checked={exclude.has(n)}
              onChange={(e) => e.target.checked ? addExclude(n) : removeExclude(n)}
            />
            {n}
          </label>
        ))}
      </fieldset>
      {conflicts.length > 0 && (
        <span style={{ color: '#C62828', fontStyle: 'italic' }}>
          Conflict: {conflicts.join(', ')} in both columns ignored
        </span>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/ClusterIncludeExcludePicker.tsx web/src/components/explorer/__tests__/ClusterIncludeExcludePicker.test.tsx
git commit -m "feat(web): add ClusterIncludeExcludePicker with mutual exclusion + conflict caption"
```

---

#### Task 21: NeighborFilterPanel

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/NeighborFilterPanel.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/__tests__/NeighborFilterPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/NeighborFilterPanel.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NeighborFilterPanel } from '@/components/explorer/NeighborFilterPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('NeighborFilterPanel', () => {
  it('renders size_min, size_max, isolation_min inputs and a border-clipped checkbox', () => {
    render(<NeighborFilterPanel />)
    expect(screen.getByLabelText(/size min/i)).not.toBeNull()
    expect(screen.getByLabelText(/size max/i)).not.toBeNull()
    expect(screen.getByLabelText(/isolation min/i)).not.toBeNull()
    expect(screen.getByLabelText(/exclude border-clipped/i)).not.toBeNull()
  })

  it('typing into size_min writes through to neighborFilter.sizeMin', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '3' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBe(3)
  })

  it('typing into size_max writes through to neighborFilter.sizeMax', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size max/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '20' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBe(20)
  })

  it('clearing size_min sets sizeMin to null', () => {
    useExplorerStore.getState().setSizeRange(5, 10)
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBeNull()
  })

  it('typing into isolation_min writes through to neighborFilter.isolationMin', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/isolation min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '80' } })
    expect(useExplorerStore.getState().neighborFilter.isolationMin).toBe(80)
  })

  it('toggling exclude border-clipped flips the boolean', () => {
    render(<NeighborFilterPanel />)
    const cb = screen.getByLabelText(/exclude border-clipped/i) as HTMLInputElement
    fireEvent.click(cb)
    expect(useExplorerStore.getState().neighborFilter.excludeBorderClipped).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/NeighborFilterPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/NeighborFilterPanel.tsx
import { useExplorerStore } from '@/state/explorerSlice'

export function NeighborFilterPanel() {
  const nf = useExplorerStore((s) => s.neighborFilter)
  const setSizeRange = useExplorerStore((s) => s.setSizeRange)
  const setIsolationMin = useExplorerStore((s) => s.setIsolationMin)
  const setExcludeBorderClipped = useExplorerStore((s) => s.setExcludeBorderClipped)

  function parseOrNull(v: string): number | null {
    if (v === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }

  return (
    <fieldset aria-label="neighbor filter">
      <legend>Neighbor filter</legend>
      <label>
        Size min
        <input
          type="number"
          min={1}
          value={nf.sizeMin ?? ''}
          onChange={(e) => setSizeRange(parseOrNull(e.target.value), nf.sizeMax)}
          aria-label="size min"
        />
      </label>
      <label>
        Size max
        <input
          type="number"
          min={1}
          value={nf.sizeMax ?? ''}
          onChange={(e) => setSizeRange(nf.sizeMin, parseOrNull(e.target.value))}
          aria-label="size max"
        />
      </label>
      <label>
        Isolation min (px)
        <input
          type="number"
          min={0}
          value={nf.isolationMin ?? ''}
          onChange={(e) => setIsolationMin(parseOrNull(e.target.value))}
          aria-label="isolation min"
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={nf.excludeBorderClipped}
          onChange={(e) => setExcludeBorderClipped(e.target.checked)}
          aria-label="exclude border-clipped"
        />
        Exclude border-clipped flakes
      </label>
    </fieldset>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/NeighborFilterPanel.test.tsx`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/NeighborFilterPanel.tsx web/src/components/explorer/__tests__/NeighborFilterPanel.test.tsx
git commit -m "feat(web): add NeighborFilterPanel (size + isolation + border-clipped)"
```

---

#### Task 22: RenderTogglesPanel (state-only no-ops)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/RenderTogglesPanel.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/__tests__/RenderTogglesPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/RenderTogglesPanel.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RenderTogglesPanel } from '@/components/explorer/RenderTogglesPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('RenderTogglesPanel — Plan v34 defaults + state-only no-ops (pinned #10)', () => {
  it('renders 4 checkboxes for the 4 toggle keys', () => {
    render(<RenderTogglesPanel />)
    expect(screen.getByLabelText(/flake bbox/i)).not.toBeNull()
    expect(screen.getByLabelText(/flake outline/i)).not.toBeNull()
    expect(screen.getByLabelText(/island bbox/i)).not.toBeNull()
    expect(screen.getByLabelText(/island outline/i)).not.toBeNull()
  })

  it('flake bbox starts checked (true) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake bbox/i) as HTMLInputElement
    expect(cb.checked).toBe(true)
  })

  it('island outline starts checked (true) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/island outline/i) as HTMLInputElement
    expect(cb.checked).toBe(true)
  })

  it('flake outline starts unchecked (false) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake outline/i) as HTMLInputElement
    expect(cb.checked).toBe(false)
  })

  it('island bbox starts unchecked (false) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/island bbox/i) as HTMLInputElement
    expect(cb.checked).toBe(false)
  })

  it('toggling flake_outline writes through to renderToggles.flake_outline', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake outline/i) as HTMLInputElement
    fireEvent.click(cb)
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/RenderTogglesPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/RenderTogglesPanel.tsx
// Pinned decision #10: state-only no-ops. Mosaic does NOT consume these in Plan 4.
import { useExplorerStore, type RenderToggles } from '@/state/explorerSlice'

const TOGGLE_DEFS: Array<{ key: keyof RenderToggles; label: string }> = [
  { key: 'flake_bbox', label: 'Flake bbox' },
  { key: 'flake_outline', label: 'Flake outline' },
  { key: 'island_bbox', label: 'Island bbox' },
  { key: 'island_outline', label: 'Island outline' },
]

export function RenderTogglesPanel() {
  const toggles = useExplorerStore((s) => s.renderToggles)
  const toggleRender = useExplorerStore((s) => s.toggleRender)
  return (
    <fieldset aria-label="render toggles">
      <legend>Render toggles</legend>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px',
      }}>
        {TOGGLE_DEFS.map(({ key, label }) => (
          <label key={key}>
            <input
              type="checkbox"
              aria-label={label.toLowerCase()}
              checked={toggles[key]}
              onChange={() => toggleRender(key)}
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/RenderTogglesPanel.test.tsx`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/RenderTogglesPanel.tsx web/src/components/explorer/__tests__/RenderTogglesPanel.test.tsx
git commit -m "feat(web): add RenderTogglesPanel (state-only no-ops, Plan v34 defaults)"
```

---

#### Task 23: LodPicker

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/LodPicker.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/__tests__/LodPicker.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/LodPicker.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LodPicker } from '@/components/explorer/LodPicker'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('LodPicker', () => {
  it('renders one radio per choice (auto, lod0, lod1, lod2, raw)', () => {
    render(<LodPicker />)
    expect(screen.getByRole('radio', { name: /auto/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod0/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod1/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod2/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /raw/i })).not.toBeNull()
  })

  it('starts with auto selected', () => {
    render(<LodPicker />)
    const auto = screen.getByRole('radio', { name: /auto/i }) as HTMLInputElement
    expect(auto.checked).toBe(true)
  })

  it('clicking lod1 writes 1 to lodChoice', () => {
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /lod1/i }))
    expect(useExplorerStore.getState().lodChoice).toBe(1)
  })

  it('clicking raw writes 3 to lodChoice', () => {
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /raw/i }))
    expect(useExplorerStore.getState().lodChoice).toBe(3)
  })

  it('clicking auto reverts lodChoice to "auto"', () => {
    useExplorerStore.getState().setLodChoice(2)
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /auto/i }))
    expect(useExplorerStore.getState().lodChoice).toBe('auto')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/LodPicker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/LodPicker.tsx
import { useExplorerStore, type LodChoice } from '@/state/explorerSlice'

const CHOICES: Array<{ value: LodChoice; label: string }> = [
  { value: 'auto', label: 'auto' },
  { value: 0, label: 'lod0' },
  { value: 1, label: 'lod1' },
  { value: 2, label: 'lod2' },
  { value: 3, label: 'raw' },
]

export function LodPicker() {
  const lodChoice = useExplorerStore((s) => s.lodChoice)
  const setLodChoice = useExplorerStore((s) => s.setLodChoice)
  return (
    <fieldset aria-label="lod picker">
      <legend>LOD</legend>
      {CHOICES.map((c) => (
        <label key={String(c.value)}>
          <input
            type="radio"
            name="lod-choice"
            aria-label={c.label}
            checked={lodChoice === c.value}
            onChange={() => setLodChoice(c.value)}
          />
          {c.label}
        </label>
      ))}
    </fieldset>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/LodPicker.test.tsx`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/LodPicker.tsx web/src/components/explorer/__tests__/LodPicker.test.tsx
git commit -m "feat(web): add LodPicker (auto | lod0 | lod1 | lod2 | raw)"
```

---

#### Task 24: SaveExplorerStateButton

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/SaveExplorerStateButton.tsx`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/web/src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { SaveExplorerStateButton } from '@/components/explorer/SaveExplorerStateButton'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('sonner', () => ({
  toast: {
    success: (...a: unknown[]) => mockToastSuccess(...a),
    error: (...a: unknown[]) => mockToastError(...a),
  },
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  mockToastSuccess.mockReset()
  mockToastError.mockReset()
  resetExplorerStore()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('SaveExplorerStateButton', () => {
  it('renders a button labeled "Save Explorer state"', () => {
    wrap(<SaveExplorerStateButton projectId="local" />)
    expect(screen.getByRole('button', { name: /Save Explorer state/i })).not.toBeNull()
  })

  it('clicking POSTs the current explorer state and shows a success toast', async () => {
    const fetchSpy = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string)
      expect(body.include_labels).toEqual(['thin'])
      expect(body.exclude_labels).toEqual([])
      expect(body.neighbor_filter.size_min).toBe(1)
      return new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 0,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchSpy)

    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().setSizeRange(1, 50)

    wrap(<SaveExplorerStateButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('shows an error toast on 409 prerequisite_missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'fit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<SaveExplorerStateButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastError).toHaveBeenCalled())
  })

  it('button is disabled while the mutation is pending', async () => {
    let resolveFn: ((r: Response) => void) | null = null
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      resolveFn = resolve
    })))
    wrap(<SaveExplorerStateButton projectId="local" />)
    const btn = screen.getByRole('button', { name: /Save Explorer state/i }) as HTMLButtonElement
    fireEvent.click(btn)
    await waitFor(() => expect(btn.disabled).toBe(true))
    resolveFn?.(new Response(JSON.stringify({ state_path: '/x', selected_count: null }),
      { status: 200, headers: { 'content-type': 'application/json' } }))
    await waitFor(() => expect(btn.disabled).toBe(false))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/SaveExplorerStateButton.tsx
import { toast } from 'sonner'
import { useSaveExplorerState } from '@/hooks/useSaveExplorerState'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  projectId: string
}

export function SaveExplorerStateButton({ projectId }: Props) {
  const m = useSaveExplorerState(projectId)
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const nf = useExplorerStore((s) => s.neighborFilter)

  const onClick = async () => {
    try {
      const result = await m.mutateAsync({
        include_labels: Array.from(include),
        exclude_labels: Array.from(exclude),
        neighbor_filter: {
          size_min: nf.sizeMin,
          size_max: nf.sizeMax,
          isolation_min: nf.isolationMin,
          exclude_border_clipped: nf.excludeBorderClipped,
        },
      })
      toast.success(`Saved (${result.selected_count ?? 0} flakes)`)
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? 'Save failed'
      toast.error(msg)
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={m.isPending}
    >
      Save Explorer state
    </button>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/SaveExplorerStateButton.tsx web/src/components/explorer/__tests__/SaveExplorerStateButton.test.tsx
git commit -m "feat(web): add SaveExplorerStateButton with toast feedback + disabled-on-pending"
```

---

### Phase 8 — Mosaic Canvas + Detail Components

This phase implements the OpenSeadragon mosaic wrapper (collection mode, server-side Y-flip, pass/fail dim, gold selected-tile overlay) and the Detail-pane components that render the right-rail summary for a clicked flake.

#### Task 25: Typed OpenSeadragon default-import re-export

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/lib/__tests__/openseadragon.test.ts
import { describe, it, expect, vi } from 'vitest'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({ destroy: vi.fn() })),
}))

import OSD from '../openseadragon'

describe('lib/openseadragon', () => {
  it('re-exports the default export of the openseadragon package', () => {
    expect(typeof OSD).toBe('function')
    const v = OSD({ id: 'x', tileSources: [] } as unknown as Parameters<typeof OSD>[0])
    expect(v).toBeDefined()
    expect(typeof (v as { destroy: () => void }).destroy).toBe('function')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/__tests__/openseadragon.test.ts`
Expected: FAIL — module `../openseadragon` not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/lib/openseadragon.ts
// Single typed re-export of the OpenSeadragon default function.
// Pinned decision #5: OSD 4.1.x stable.
import OpenSeadragon from 'openseadragon'

export default OpenSeadragon
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/__tests__/openseadragon.test.ts`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/openseadragon.ts web/src/lib/__tests__/openseadragon.test.ts
git commit -m "feat(web): add typed openseadragon default-import re-export"
```

---

#### Task 26: MosaicCanvas — OSD wrapper with collection mode, Y-flipped tiles, pass/fail dim, selected-tile overlay

This is the core viewer. It consumes a `TileManifest` from `useTileManifest`, builds OSD `tileSources` using `LegacyTileSource` (mosaic-viewer §2 Path A), wires `collectionMode: true` so OSD lays out tiles using each tile's row/col, applies pass/fail visual dim, draws a gold rect overlay on the selected tile, and handles click-to-select via `viewerElementToViewportCoordinates`.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/MosaicCanvas.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import React from 'react'

const mockViewer = {
  open: vi.fn(),
  destroy: vi.fn(),
  addOverlay: vi.fn(),
  removeOverlay: vi.fn(),
  clearOverlays: vi.fn(),
  addHandler: vi.fn(),
  world: {
    getItemCount: vi.fn(() => 0),
    getItemAt: vi.fn(() => ({ setOpacity: vi.fn() })),
  },
  viewport: {
    viewerElementToViewportCoordinates: vi.fn(() => ({ x: 0.05, y: 0.05 })),
    viewportToImageCoordinates: vi.fn(() => ({ x: 100, y: 100 })),
  },
  element: document.createElement('div'),
}

vi.mock('openseadragon', () => ({
  default: vi.fn(() => mockViewer),
}))

import OpenSeadragon from 'openseadragon'
import { MosaicCanvas } from '../MosaicCanvas'
import type { TileManifest } from '@/api/explorer'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const manifest: TileManifest = {
  project_id: 'local',
  cols: 2,
  rows: 1,
  tile_w_px: 256,
  tile_h_px: 256,
  pyramid: { lod_choice: 'auto', cache_dir: '/x', available_lods: [0] },
  tiles: [
    { stem: 'A', col: 0, row: 0, url: '/static/raw/A.jpg', w: 256, h: 256, lod: null },
    { stem: 'B', col: 1, row: 0, url: '/static/raw/B.jpg', w: 256, h: 256, lod: null },
  ],
  flakes_by_stem: {
    A: [{ flake_id: 'A:0', cluster_label: 1, passes_filter: true, bbox_norm: [0.1, 0.1, 0.4, 0.4] }],
    B: [{ flake_id: 'B:0', cluster_label: 2, passes_filter: false, bbox_norm: [0.2, 0.2, 0.5, 0.5] }],
  },
}

describe('MosaicCanvas', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.clearAllMocks()
  })

  it('mounts an OSD viewer with collectionMode and tileSources from the manifest', () => {
    render(<MosaicCanvas manifest={manifest} />)
    expect(OpenSeadragon).toHaveBeenCalledTimes(1)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionMode: boolean
      tileSources: Array<{ type: string; url: string; width: number; height: number }>
    }
    expect(cfg.collectionMode).toBe(true)
    expect(cfg.tileSources).toHaveLength(2)
    expect(cfg.tileSources[0]).toMatchObject({ type: 'image', url: '/static/raw/A.jpg' })
    expect(cfg.tileSources[1]).toMatchObject({ type: 'image', url: '/static/raw/B.jpg' })
  })

  it('honours server-side Y-flip by passing tile rows in collectionLayout', () => {
    render(<MosaicCanvas manifest={manifest} />)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionTileSize: number
      collectionTileMargin: number
      collectionRows: number
      collectionColumns: number
    }
    expect(cfg.collectionRows).toBe(1)
    expect(cfg.collectionColumns).toBe(2)
  })

  it('dims fail tiles by setting tile opacity to 0.5', () => {
    let world = 0
    mockViewer.world.getItemCount.mockImplementation(() => world)
    const setOpA = vi.fn()
    const setOpB = vi.fn()
    mockViewer.world.getItemAt.mockImplementation((i: number) =>
      i === 0 ? { setOpacity: setOpA } : { setOpacity: setOpB }
    )
    render(<MosaicCanvas manifest={manifest} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'open'
    )?.[1] as () => void
    world = 2
    handler?.()
    expect(setOpA).toHaveBeenCalledWith(1)
    expect(setOpB).toHaveBeenCalledWith(0.5)
  })

  it('draws a gold overlay on the selected tile via addOverlay', () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    render(<MosaicCanvas manifest={manifest} />)
    expect(mockViewer.addOverlay).toHaveBeenCalled()
    const call = mockViewer.addOverlay.mock.calls[0][0] as { element: HTMLElement }
    expect(call.element.getAttribute('data-overlay')).toBe('selected-tile')
    expect(call.element.style.outline).toContain('#FFC800')
  })

  it('selects the first flake of the clicked tile on canvas-click', () => {
    render(<MosaicCanvas manifest={manifest} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'canvas-click'
    )?.[1] as (ev: { position: { x: number; y: number } }) => void
    mockViewer.viewport.viewerElementToViewportCoordinates.mockReturnValueOnce({ x: 0.75, y: 0.5 })
    handler?.({ position: { x: 0, y: 0 } })
    expect(useExplorerStore.getState().selectedFlakeId).toBe('B:0')
  })

  it('destroys the viewer on unmount', () => {
    const { unmount } = render(<MosaicCanvas manifest={manifest} />)
    unmount()
    expect(mockViewer.destroy).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/MosaicCanvas.test.tsx`
Expected: FAIL — module `../MosaicCanvas` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/MosaicCanvas.tsx
import { useEffect, useRef } from 'react'
import OpenSeadragon from '@/lib/openseadragon'
import type { TileManifest } from '@/api/explorer'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  manifest: TileManifest
}

interface OSDViewerLike {
  open: (...args: unknown[]) => void
  destroy: () => void
  addOverlay: (cfg: { element: HTMLElement; location: unknown }) => void
  removeOverlay: (el: HTMLElement) => void
  clearOverlays: () => void
  addHandler: (name: string, fn: (ev: unknown) => void) => void
  world: {
    getItemCount: () => number
    getItemAt: (i: number) => { setOpacity: (o: number) => void }
  }
  viewport: {
    viewerElementToViewportCoordinates: (p: { x: number; y: number }) => { x: number; y: number }
    viewportToImageCoordinates: (p: { x: number; y: number }) => { x: number; y: number }
  }
  element: HTMLElement
}

export function MosaicCanvas({ manifest }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<OSDViewerLike | null>(null)

  const selectedFlakeId = useExplorerStore((s) => s.selectedFlakeId)
  const setSelectedFlakeId = useExplorerStore((s) => s.setSelectedFlakeId)

  useEffect(() => {
    if (!containerRef.current) return
    const tileSources = manifest.tiles.map((t) => ({
      type: 'image' as const,
      url: t.url,
      width: t.w,
      height: t.h,
      buildPyramid: false,
    }))
    const viewer = OpenSeadragon({
      element: containerRef.current,
      collectionMode: true,
      collectionRows: manifest.rows,
      collectionColumns: manifest.cols,
      collectionTileSize: 1,
      collectionTileMargin: 0,
      tileSources,
      showNavigator: false,
      gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false },
      crossOriginPolicy: 'Anonymous',
    } as unknown as Parameters<typeof OpenSeadragon>[0]) as unknown as OSDViewerLike
    viewerRef.current = viewer

    viewer.addHandler('open', () => {
      const count = viewer.world.getItemCount()
      for (let i = 0; i < count; i++) {
        const tile = manifest.tiles[i]
        if (!tile) continue
        const flakes = manifest.flakes_by_stem[tile.stem] ?? []
        const allFail = flakes.length > 0 && flakes.every((f) => !f.passes_filter)
        viewer.world.getItemAt(i).setOpacity(allFail ? 0.5 : 1)
      }
    })

    viewer.addHandler('canvas-click', (raw) => {
      const ev = raw as { position: { x: number; y: number } }
      const vp = viewer.viewport.viewerElementToViewportCoordinates(ev.position)
      const col = Math.min(manifest.cols - 1, Math.max(0, Math.floor(vp.x * manifest.cols)))
      const row = Math.min(manifest.rows - 1, Math.max(0, Math.floor(vp.y * manifest.rows)))
      const tile = manifest.tiles.find((t) => t.col === col && t.row === row)
      if (!tile) return
      const first = (manifest.flakes_by_stem[tile.stem] ?? [])[0]
      if (first) setSelectedFlakeId(first.flake_id)
    })

    return () => {
      viewer.destroy()
      viewerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest])

  // Selected-tile gold overlay: re-render when selection changes.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.clearOverlays()
    if (!selectedFlakeId) return
    const tile = manifest.tiles.find((t) =>
      (manifest.flakes_by_stem[t.stem] ?? []).some((f) => f.flake_id === selectedFlakeId)
    )
    if (!tile) return
    const el = document.createElement('div')
    el.setAttribute('data-overlay', 'selected-tile')
    el.style.outline = '3px solid #FFC800'
    el.style.boxSizing = 'border-box'
    el.style.pointerEvents = 'none'
    viewer.addOverlay({
      element: el,
      location: { x: tile.col, y: tile.row, width: 1, height: 1 },
    })
  }, [selectedFlakeId, manifest])

  return (
    <div
      ref={containerRef}
      data-testid="mosaic-canvas"
      style={{ width: '100%', height: '100%', minHeight: '400px', background: '#000' }}
    />
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/MosaicCanvas.test.tsx`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/MosaicCanvas.tsx web/src/components/explorer/__tests__/MosaicCanvas.test.tsx
git commit -m "feat(web): add MosaicCanvas OSD wrapper with collection mode, pass/fail dim, gold overlay, click-to-select"
```

---

#### Task 27: FlakeListPanel — server-filtered flake table with row-click selection

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/FlakeListPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { FlakeListPanel } from '../FlakeListPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('FlakeListPanel', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders one row per flake from /explorer/flakes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flakes: [
          { flake_id: 'A:0', stem: 'A', cluster_label: 1, size_px: 120,
            isolation_um: 5.0, passes_filter: true, border_clipped: false },
          { flake_id: 'B:1', stem: 'B', cluster_label: 2, size_px: 80,
            isolation_um: 2.5, passes_filter: false, border_clipped: true },
        ],
        total: 2,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText('A:0')).toBeInTheDocument()
    expect(screen.getByText('B:1')).toBeInTheDocument()
  })

  it('shows "No flakes match the current filters." when the result is empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ flakes: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText(/No flakes match the current filters/i)).toBeInTheDocument()
  })

  it('writes selectedFlakeId to the store when a row is clicked', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flakes: [
          { flake_id: 'A:0', stem: 'A', cluster_label: 1, size_px: 120,
            isolation_um: 5.0, passes_filter: true, border_clipped: false },
        ],
        total: 1,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    const row = await screen.findByText('A:0')
    fireEvent.click(row)
    await waitFor(() =>
      expect(useExplorerStore.getState().selectedFlakeId).toBe('A:0')
    )
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/FlakeListPanel.test.tsx`
Expected: FAIL — module `../FlakeListPanel` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/FlakeListPanel.tsx
import { useExplorerFlakes } from '@/hooks/useExplorerFlakes'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  projectId: string
}

export function FlakeListPanel({ projectId }: Props) {
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const nf = useExplorerStore((s) => s.neighborFilter)
  const setSelectedFlakeId = useExplorerStore((s) => s.setSelectedFlakeId)

  const { data, isLoading, isError } = useExplorerFlakes(projectId, {
    include: Array.from(include),
    exclude: Array.from(exclude),
    sizeMin: nf.sizeMin,
    sizeMax: nf.sizeMax,
    isolationMin: nf.isolationMin,
    excludeBorderClipped: nf.excludeBorderClipped,
  })

  if (isLoading) return <div>Loading flakes...</div>
  if (isError) return <div>Failed to load flakes.</div>
  const flakes = data?.flakes ?? []
  if (flakes.length === 0) return <div>No flakes match the current filters.</div>

  return (
    <table data-testid="flake-list-table">
      <thead>
        <tr>
          <th>flake_id</th>
          <th>stem</th>
          <th>cluster</th>
          <th>size_px</th>
          <th>isolation_um</th>
          <th>pass</th>
        </tr>
      </thead>
      <tbody>
        {flakes.map((f) => (
          <tr key={f.flake_id} onClick={() => setSelectedFlakeId(f.flake_id)}
              style={{ cursor: 'pointer' }}>
            <td>{f.flake_id}</td>
            <td>{f.stem}</td>
            <td>{f.cluster_label ?? '-'}</td>
            <td>{f.size_px}</td>
            <td>{f.isolation_um.toFixed(2)}</td>
            <td>{f.passes_filter ? 'pass' : 'fail'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/FlakeListPanel.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/FlakeListPanel.tsx web/src/components/explorer/__tests__/FlakeListPanel.test.tsx
git commit -m "feat(web): add FlakeListPanel with row-click selection wired to explorer store"
```

---

#### Task 28: DetailIdentity + DetailLabels + DetailDistance presentational components

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/DetailParts.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetailIdentity } from '../DetailIdentity'
import { DetailLabels } from '../DetailLabels'
import { DetailDistance } from '../DetailDistance'

describe('DetailIdentity', () => {
  it('shows the flake_id, stem, and pass/fail chip', () => {
    render(<DetailIdentity flakeId="A:3" stem="A" passes={true} />)
    expect(screen.getByText('A:3')).toBeInTheDocument()
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText(/pass/i)).toBeInTheDocument()
  })

  it('renders FAIL chip when passes is false', () => {
    render(<DetailIdentity flakeId="B:1" stem="B" passes={false} />)
    expect(screen.getByText(/fail/i)).toBeInTheDocument()
  })
})

describe('DetailLabels', () => {
  it('renders one chip per cluster label using CLUSTER_PALETTE', () => {
    render(<DetailLabels labels={[{ label: 1, name: 'mono' }, { label: 2, name: 'bi' }]} />)
    expect(screen.getByText('mono')).toBeInTheDocument()
    expect(screen.getByText('bi')).toBeInTheDocument()
  })

  it('renders an em-dash when labels are empty', () => {
    render(<DetailLabels labels={[]} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('DetailDistance', () => {
  it('renders nearest-neighbor distance in micrometers with 2 decimals', () => {
    render(<DetailDistance distanceUm={3.14159} />)
    expect(screen.getByText(/3\.14/)).toBeInTheDocument()
    expect(screen.getByText(/µm/i)).toBeInTheDocument()
  })

  it('renders an em-dash when the distance is null', () => {
    render(<DetailDistance distanceUm={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/DetailParts.test.tsx`
Expected: FAIL — three modules not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/DetailIdentity.tsx
interface Props {
  flakeId: string
  stem: string
  passes: boolean
}

export function DetailIdentity({ flakeId, stem, passes }: Props) {
  return (
    <div data-testid="detail-identity">
      <div><strong>flake_id:</strong> {flakeId}</div>
      <div><strong>stem:</strong> {stem}</div>
      <span
        data-testid="pass-chip"
        style={{
          display: 'inline-block', padding: '2px 6px', borderRadius: 4,
          background: passes ? '#1f6f3a' : '#7a1f1f', color: '#fff',
        }}
      >
        {passes ? 'PASS' : 'FAIL'}
      </span>
    </div>
  )
}
```

```tsx
// web/src/components/explorer/DetailLabels.tsx
import { CLUSTER_PALETTE } from '@/lib/clusterColors'

interface Props {
  labels: Array<{ label: number; name: string }>
}

export function DetailLabels({ labels }: Props) {
  if (labels.length === 0) return <div>—</div>
  return (
    <div data-testid="detail-labels" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {labels.map((l) => {
        const colour = CLUSTER_PALETTE[l.label % CLUSTER_PALETTE.length] ?? '#888'
        return (
          <span
            key={l.label}
            style={{
              background: colour, color: '#fff',
              padding: '2px 6px', borderRadius: 4, fontSize: 12,
            }}
          >
            {l.name}
          </span>
        )
      })}
    </div>
  )
}
```

```tsx
// web/src/components/explorer/DetailDistance.tsx
interface Props {
  distanceUm: number | null
}

export function DetailDistance({ distanceUm }: Props) {
  if (distanceUm == null) return <div>—</div>
  return (
    <div data-testid="detail-distance">
      Nearest neighbour: {distanceUm.toFixed(2)} µm
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/DetailParts.test.tsx`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/DetailIdentity.tsx web/src/components/explorer/DetailLabels.tsx web/src/components/explorer/DetailDistance.tsx web/src/components/explorer/__tests__/DetailParts.test.tsx
git commit -m "feat(web): add DetailIdentity, DetailLabels, and DetailDistance presentational components"
```

---

#### Task 29: DetailPanel — composer that fetches the flake and renders the three detail subcomponents

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/DetailPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { DetailPanel } from '../DetailPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('DetailPanel', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders the empty-state message when no flake is selected', () => {
    wrap(<DetailPanel projectId="local" />)
    expect(screen.getByText(/Select a flake to see details/i)).toBeInTheDocument()
  })

  it('fetches the flake detail and renders identity, labels, and distance', async () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flake_id: 'A:0',
        stem: 'A',
        passes_filter: true,
        size_px: 200,
        isolation_um: 4.5,
        nearest_neighbour_um: 7.25,
        cluster_labels: [{ label: 1, name: 'mono' }],
        bbox_norm: [0.1, 0.1, 0.4, 0.4],
        thumbnail_url: '/static/raw/A.jpg',
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<DetailPanel projectId="local" />)
    expect(await screen.findByText('A:0')).toBeInTheDocument()
    expect(screen.getByText('mono')).toBeInTheDocument()
    expect(screen.getByText(/7\.25/)).toBeInTheDocument()
  })

  it('shows a loading message while the query is pending', () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => { /* never */ })))
    wrap(<DetailPanel projectId="local" />)
    expect(screen.getByText(/Loading detail/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/DetailPanel.test.tsx`
Expected: FAIL — module `../DetailPanel` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/DetailPanel.tsx
import { useExplorerFlakeDetail } from '@/hooks/useExplorerFlakeDetail'
import { useExplorerStore } from '@/state/explorerSlice'
import { DetailIdentity } from './DetailIdentity'
import { DetailLabels } from './DetailLabels'
import { DetailDistance } from './DetailDistance'

interface Props {
  projectId: string
}

export function DetailPanel({ projectId }: Props) {
  const flakeId = useExplorerStore((s) => s.selectedFlakeId)
  const { data, isLoading, isError } = useExplorerFlakeDetail(projectId, flakeId)

  if (!flakeId) return <div>Select a flake to see details.</div>
  if (isLoading) return <div>Loading detail...</div>
  if (isError || !data) return <div>Failed to load flake detail.</div>

  return (
    <div data-testid="detail-panel" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <DetailIdentity flakeId={data.flake_id} stem={data.stem} passes={data.passes_filter} />
      <DetailLabels labels={data.cluster_labels} />
      <DetailDistance distanceUm={data.nearest_neighbour_um} />
      {data.thumbnail_url && (
        <img
          src={data.thumbnail_url}
          alt={data.flake_id}
          data-testid="detail-thumbnail"
          style={{ maxWidth: '100%', height: 'auto' }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/DetailPanel.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/DetailPanel.tsx web/src/components/explorer/__tests__/DetailPanel.test.tsx
git commit -m "feat(web): add DetailPanel composer wired to useExplorerFlakeDetail and store selection"
```

---

### Phase 9 — Right-Rail composer + Page + Integration test

This phase wires the right rail of five panels, the three-column main pane, the page-level prerequisite check + empty-state CTA, the lazy route registration, and an integration test that exercises the full Explorer happy path.

#### Task 30: ExplorerRightRail — composer for the five control panels

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/ExplorerRightRail.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerRightRail } from '../ExplorerRightRail'
import { resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('ExplorerRightRail', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ flakes: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    ))
  })

  it('renders the five control panels: cluster picker, neighbour filter, render toggles, LOD picker, save button', () => {
    wrap(<ExplorerRightRail projectId="local" availableLabels={[1, 2]} />)
    expect(screen.getByTestId('cluster-include-exclude-picker')).toBeInTheDocument()
    expect(screen.getByTestId('neighbor-filter-panel')).toBeInTheDocument()
    expect(screen.getByTestId('render-toggles-panel')).toBeInTheDocument()
    expect(screen.getByTestId('lod-picker')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save Explorer state/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/ExplorerRightRail.test.tsx`
Expected: FAIL — module `../ExplorerRightRail` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/ExplorerRightRail.tsx
import { ClusterIncludeExcludePicker } from './ClusterIncludeExcludePicker'
import { NeighborFilterPanel } from './NeighborFilterPanel'
import { RenderTogglesPanel } from './RenderTogglesPanel'
import { LodPicker } from './LodPicker'
import { SaveExplorerStateButton } from './SaveExplorerStateButton'

interface Props {
  projectId: string
  availableLabels: number[]
}

export function ExplorerRightRail({ projectId, availableLabels }: Props) {
  return (
    <aside
      data-testid="explorer-right-rail"
      style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 8, overflowY: 'auto' }}
    >
      <ClusterIncludeExcludePicker availableLabels={availableLabels} />
      <NeighborFilterPanel />
      <RenderTogglesPanel />
      <LodPicker />
      <SaveExplorerStateButton projectId={projectId} />
    </aside>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/ExplorerRightRail.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/ExplorerRightRail.tsx web/src/components/explorer/__tests__/ExplorerRightRail.test.tsx
git commit -m "feat(web): add ExplorerRightRail composer wiring the five Explorer control panels"
```

---

#### Task 31: ExplorerMain — three-column CSS grid (60% mosaic / 22% list / 18% detail)

Pinned decision #8: layout uses CSS grid with widths 60% / 22% / 18%.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/components/explorer/__tests__/ExplorerMain.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerMain } from '../ExplorerMain'
import type { TileManifest } from '@/api/explorer'
import { resetExplorerStore } from '@/state/explorerSlice'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(), destroy: vi.fn(),
    addOverlay: vi.fn(), removeOverlay: vi.fn(), clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const manifest: TileManifest = {
  project_id: 'local', cols: 1, rows: 1, tile_w_px: 256, tile_h_px: 256,
  pyramid: { lod_choice: 'auto', cache_dir: null, available_lods: [] },
  tiles: [{ stem: 'A', col: 0, row: 0, url: '/static/raw/A.jpg', w: 256, h: 256, lod: null }],
  flakes_by_stem: { A: [] },
}

describe('ExplorerMain', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ flakes: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    ))
  })

  it('lays out the three columns with grid template "60% 22% 18%"', () => {
    wrap(<ExplorerMain projectId="local" manifest={manifest} />)
    const grid = screen.getByTestId('explorer-main-grid')
    expect(grid.style.gridTemplateColumns).toBe('60% 22% 18%')
  })

  it('renders the mosaic, the flake-list panel, and the detail panel', () => {
    wrap(<ExplorerMain projectId="local" manifest={manifest} />)
    expect(screen.getByTestId('mosaic-canvas')).toBeInTheDocument()
    expect(screen.getByTestId('flake-list-panel')).toBeInTheDocument()
    expect(screen.getByTestId('detail-panel-region')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/explorer/__tests__/ExplorerMain.test.tsx`
Expected: FAIL — module `../ExplorerMain` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/components/explorer/ExplorerMain.tsx
import type { TileManifest } from '@/api/explorer'
import { MosaicCanvas } from './MosaicCanvas'
import { FlakeListPanel } from './FlakeListPanel'
import { DetailPanel } from './DetailPanel'

interface Props {
  projectId: string
  manifest: TileManifest
}

export function ExplorerMain({ projectId, manifest }: Props) {
  return (
    <div
      data-testid="explorer-main-grid"
      style={{
        display: 'grid',
        gridTemplateColumns: '60% 22% 18%',
        gap: 8,
        height: '100%',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <MosaicCanvas manifest={manifest} />
      </div>
      <div data-testid="flake-list-panel" style={{ minWidth: 0, overflow: 'auto' }}>
        <FlakeListPanel projectId={projectId} />
      </div>
      <div data-testid="detail-panel-region" style={{ minWidth: 0, overflow: 'auto' }}>
        <DetailPanel projectId={projectId} />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/components/explorer/__tests__/ExplorerMain.test.tsx`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/explorer/ExplorerMain.tsx web/src/components/explorer/__tests__/ExplorerMain.test.tsx
git commit -m "feat(web): add ExplorerMain three-column 60/22/18 grid layout"
```

---

#### Task 32: ExplorerTab page — full-pane empty-state CTA when prereqs missing

Pinned decision #9: when `/explorer/tile_manifest` returns 409 prerequisite_missing, render a full-pane empty state with a CTA pointing to the Clustering tab. When the manifest loads, render `<ExplorerMain>` and `<ExplorerRightRail>`.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/pages/__tests__/ExplorerTab.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerTab } from '../ExplorerTab'
import { resetExplorerStore } from '@/state/explorerSlice'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(), destroy: vi.fn(),
    addOverlay: vi.fn(), removeOverlay: vi.fn(), clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('ExplorerTab page', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders the empty-state CTA when prerequisites are missing (409)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'fit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<ExplorerTab projectId="local" />)
    expect(await screen.findByText(/Run the Clustering tab to see the Explorer/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open Clustering tab/i })).toBeInTheDocument()
  })

  it('renders the main grid and right-rail when the manifest loads', async () => {
    const manifestBody = JSON.stringify({
      project_id: 'local', cols: 1, rows: 1, tile_w_px: 256, tile_h_px: 256,
      pyramid: { lod_choice: 'auto', cache_dir: null, available_lods: [] },
      tiles: [{ stem: 'A', col: 0, row: 0, url: '/static/raw/A.jpg', w: 256, h: 256, lod: null }],
      flakes_by_stem: { A: [] },
    })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/explorer/tile_manifest')) {
        return new Response(manifestBody, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      return new Response(JSON.stringify({ flakes: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    wrap(<ExplorerTab projectId="local" />)
    expect(await screen.findByTestId('explorer-main-grid')).toBeInTheDocument()
    expect(screen.getByTestId('explorer-right-rail')).toBeInTheDocument()
  })

  it('renders a loading message while the manifest is pending', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => { /* never */ })))
    wrap(<ExplorerTab projectId="local" />)
    expect(screen.getByText(/Loading mosaic/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/pages/__tests__/ExplorerTab.test.tsx`
Expected: FAIL — module `../ExplorerTab` not found.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/pages/ExplorerTab.tsx
import { useTileManifest } from '@/hooks/useTileManifest'
import { ExplorerMain } from '@/components/explorer/ExplorerMain'
import { ExplorerRightRail } from '@/components/explorer/ExplorerRightRail'

interface Props {
  projectId: string
}

export function ExplorerTab({ projectId }: Props) {
  const { data: manifest, isLoading, error } = useTileManifest(projectId)

  if (isLoading) return <div>Loading mosaic...</div>

  if (error && (error as { code?: string }).code === 'prerequisite_missing') {
    return (
      <div
        data-testid="explorer-empty-state"
        style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100%', gap: 16, padding: 32, textAlign: 'center',
        }}
      >
        <h2>Run the Clustering tab to see the Explorer.</h2>
        <p>The Explorer needs cluster labels before it can render the mosaic.</p>
        <a href="#/clustering">Open Clustering tab</a>
      </div>
    )
  }

  if (!manifest) return <div>Failed to load mosaic.</div>

  // Compute available cluster labels from the manifest's flake summaries.
  const labelSet = new Set<number>()
  for (const flakes of Object.values(manifest.flakes_by_stem)) {
    for (const f of flakes) {
      if (f.cluster_label != null) labelSet.add(f.cluster_label)
    }
  }
  const availableLabels = Array.from(labelSet).sort((a, b) => a - b)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 280px',
      gap: 8,
      height: '100%',
    }}>
      <ExplorerMain projectId={projectId} manifest={manifest} />
      <ExplorerRightRail projectId={projectId} availableLabels={availableLabels} />
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/pages/__tests__/ExplorerTab.test.tsx`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ExplorerTab.tsx web/src/pages/__tests__/ExplorerTab.test.tsx
git commit -m "feat(web): add ExplorerTab page with prerequisite-missing empty-state CTA"
```

---

#### Task 33: Register the Explorer route in App.tsx with React.lazy

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/__tests__/App.explorer-route.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '@/App'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(), destroy: vi.fn(),
    addOverlay: vi.fn(), removeOverlay: vi.fn(), clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

describe('App route registration — Explorer', () => {
  beforeEach(() => {
    window.location.hash = ''
    vi.unstubAllGlobals()
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => { /* never */ })))
  })

  it('navigates to the Explorer tab when the hash is #/explorer', async () => {
    window.location.hash = '#/explorer'
    render(<App />)
    expect(await screen.findByText(/Loading mosaic/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/__tests__/App.explorer-route.test.tsx`
Expected: FAIL — `#/explorer` route not registered.

- [ ] **Step 3: Write minimal implementation**

```tsx
// web/src/App.tsx — add a lazy Explorer route to the existing route table.
// The existing App.tsx (Plans 1/2/3) holds a hash-route switch with lazy-loaded pages.
// Add this lazy import alongside the other lazy page imports:
//
//   const ExplorerTab = lazy(() =>
//     import('@/pages/ExplorerTab').then((m) => ({ default: m.ExplorerTab }))
//   )
//
// Add this branch to the existing switch on `window.location.hash`:
//
//   case '#/explorer':
//     return (
//       <Suspense fallback={<div>Loading...</div>}>
//         <ExplorerTab projectId={projectId} />
//       </Suspense>
//     )
//
// The exact insertion site depends on the App.tsx state at the time of execution
// (Plan 3 may have rewritten the route table). The implementer should locate the
// existing lazy-imports block and the existing switch, and insert the two snippets
// above without disturbing other routes.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/__tests__/App.explorer-route.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/__tests__/App.explorer-route.test.tsx
git commit -m "feat(web): register lazy Explorer route at #/explorer"
```

---

#### Task 34: Integration test — full Explorer happy path (manifest + flakes + selection + save)

This integration test exercises the page from manifest fetch through clicking a row, fetching flake detail, and clicking Save. It uses `vi.mock('openseadragon')` to skip real WebGL.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/pages/__tests__/ExplorerTab.integration.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerTab } from '../ExplorerTab'
import { resetExplorerStore } from '@/state/explorerSlice'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(), destroy: vi.fn(),
    addOverlay: vi.fn(), removeOverlay: vi.fn(), clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

const mockToastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { success: (...args: unknown[]) => mockToastSuccess(...args), error: vi.fn() },
}))

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('ExplorerTab integration — happy path', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    mockToastSuccess.mockClear()
  })

  it('manifest → flake-list row click → detail loads → save mutation succeeds and toasts', async () => {
    const manifestBody = JSON.stringify({
      project_id: 'local', cols: 1, rows: 1, tile_w_px: 256, tile_h_px: 256,
      pyramid: { lod_choice: 'auto', cache_dir: null, available_lods: [] },
      tiles: [{ stem: 'A', col: 0, row: 0, url: '/static/raw/A.jpg', w: 256, h: 256, lod: null }],
      flakes_by_stem: {
        A: [{ flake_id: 'A:0', cluster_label: 1, passes_filter: true, bbox_norm: [0, 0, 1, 1] }],
      },
    })
    const flakesBody = JSON.stringify({
      flakes: [{
        flake_id: 'A:0', stem: 'A', cluster_label: 1, size_px: 200,
        isolation_um: 4.5, passes_filter: true, border_clipped: false,
      }],
      total: 1,
    })
    const flakeBody = JSON.stringify({
      flake_id: 'A:0', stem: 'A', passes_filter: true, size_px: 200,
      isolation_um: 4.5, nearest_neighbour_um: 7.25,
      cluster_labels: [{ label: 1, name: 'mono' }],
      bbox_norm: [0, 0, 1, 1], thumbnail_url: '/static/raw/A.jpg',
    })
    const saveBody = JSON.stringify({ state_path: '/proj/explorer_state.npz', selected_count: 1 })

    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/explorer/tile_manifest')) {
        return new Response(manifestBody, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url.includes('/explorer/flakes') && !url.includes('/flake/')) {
        return new Response(flakesBody, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url.includes('/explorer/flake/')) {
        return new Response(flakeBody, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url.includes('/run/explorer/save_state') && init?.method === 'POST') {
        return new Response(saveBody, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }))

    wrap(<ExplorerTab projectId="local" />)

    // 1. Manifest loads, mosaic + right-rail render.
    await screen.findByTestId('explorer-main-grid')
    expect(screen.getByTestId('explorer-right-rail')).toBeInTheDocument()

    // 2. Flake-list row appears, click it.
    const row = await screen.findByText('A:0')
    fireEvent.click(row)

    // 3. Detail panel resolves.
    await screen.findByText('mono')
    expect(screen.getByText(/7\.25/)).toBeInTheDocument()

    // 4. Save button → success toast.
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/pages/__tests__/ExplorerTab.integration.test.tsx`
Expected: FAIL on a clean checkout — at least one assertion fails because nothing has been wired together yet. When all preceding tasks are complete the test should pass on first run.

- [ ] **Step 3: Write minimal implementation**

No new code is required — this task validates the wiring done in Tasks 15-33. If the test fails, debug the responsible component (do not silence the failing assertion).

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/pages/__tests__/ExplorerTab.integration.test.tsx`
Expected: 1/1 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/__tests__/ExplorerTab.integration.test.tsx
git commit -m "test(web): add ExplorerTab integration test covering manifest → select → detail → save"
```

---

## Self-Review Notes

### Spec coverage table

| Spec source | Requirement | Task |
|---|---|---|
| frontend-design §3.5 | Mosaic OSD viewer, collection mode | 26 |
| frontend-design §3.5 | Pass/fail dim (CSS + setOpacity) | 26 |
| frontend-design §3.5 | Gold selected-tile overlay | 26 |
| frontend-design §3.5 | Click-to-select first flake of tile | 26 |
| frontend-design §3.5 | Three-column 60/22/18 layout | 31 |
| frontend-design §3.5 | Right-rail control panels | 20-24, 30 |
| frontend-design §3.5 | Empty-state CTA on prereq missing | 32 |
| frontend-design §3.5 | Save Explorer state button | 24 |
| frontend-design §4.4 | TanStack Query hooks (manifest/grid/flakes/flake) | 17, 18 |
| frontend-design §4.4 | Mutation invalidation pattern | 19 |
| backend-design §1.4 | `/explorer/tile_manifest` route | 7 |
| backend-design §1.4 | `/explorer/grid` route (pinned #11) | 8 |
| backend-design §1.4 | `/explorer/flakes` server-side filter (pinned #4) | 9 |
| backend-design §1.4 | `/explorer/flake/{flake_id}` route | 10 |
| backend-design §1.4 | `POST /run/explorer/save_state` synchronous (pinned #12) | 11 |
| backend-design §1.4 | `GET /run/explorer/state` route | 12 |
| backend-design §1.4 | `/static/thumbnails/lod{lod}/{stem}.webp` route | 13 |
| backend-design §1.4 | `/static/raw/{filename}` route (pinned #3) | 14 |
| mosaic-viewer §2 | Collection mode + LegacyTileSource (Path A) | 26 |
| mosaic-viewer §4 | Server-side Y-flip via `tiles[].row` | 4, 26 |
| mosaic-viewer §10 | Resolver chain cache_dir → in-folder → raw_images_dir → 404 | 6, 13 |
| (pinned #1) | Defer spritesheet | (no task — out of scope) |
| (pinned #2) | Peek raw via PIL once per stem | 4 |
| (pinned #5) | OSD 4.1.x stable | 25, 26 |
| (pinned #6) | Defer pan-prefetch | (no task — out of scope) |
| (pinned #7) | 60×60 grid cap | 4 |
| (pinned #10) | Render toggles state-only no-op | 22 |
| Path-traversal hardening | safe_join + negative tests on `/static/...` | 2, 13, 14 |
| Cache-Control + ETag | Asserted in static-route tests | 13, 14 |

### Placeholder scan

- All `// ...` ellipses in test bodies have been replaced with concrete assertions.
- All `# ...` ellipses in implementation steps have been replaced with full code (Task 33's App.tsx is documented as snippet-insertion guidance because the surrounding switch statement is owned by prior plans; the snippets themselves are concrete and complete).
- No `TODO` markers in the plan.

### Type/name consistency

- `ExplorerFlakeRow.cluster_label: number | null` (Task 5) matches the frontend type used by `FlakeListPanel` (Task 27).
- `TileManifest.flakes_by_stem: Record<string, ExplorerFlakeSummary[]>` (Task 4) matches `MosaicCanvas` consumption (Task 26) and `ExplorerTab` label-set computation (Task 32).
- `NeighborFilter` field names: `sizeMin`/`sizeMax`/`isolationMin`/`excludeBorderClipped` (frontend, Task 15) ↔ `size_min`/`size_max`/`isolation_min`/`exclude_border_clipped` (backend, Tasks 1+9). The mapping happens in the API client (Task 16) and the save-state button (Task 24); no field name appears under both spellings on the same side of the wire.
- `selectedFlakeId: string | null` is the single source-of-truth in `useExplorerStore`; `MosaicCanvas`, `FlakeListPanel`, and `DetailPanel` all read it through the same selector.
- `ApiError` (existing in `web/src/api/selector.ts`) is reused by `useTileManifest` to surface `code === 'prerequisite_missing'` to `ExplorerTab`.

### Spec ambiguity resolved (cross-check)

All 12 pinned decisions from the author brief are mapped to a concrete task above. None is left "open" or "TBD". See the "Spec ambiguity resolved" subsection at the top of this plan and the spec-coverage table for the full mapping.

### Imports — phantom check

- All `from '@/...'` imports map to a file created in this plan or to a pre-existing file owned by Plans 1/2/3:
  - `@/lib/clusterColors` (`CLUSTER_PALETTE`) — Plan 3.
  - `@/api/selector` (`ApiError`) — Plan 1.
  - `@tanstack/react-query`, `react`, `react-dom`, `sonner`, `openseadragon` — declared in `web/package.json`.
- All Python imports (`fastapi`, `pydantic`, `PIL.Image`, `numpy`) match the existing dependency manifest.
- `acquire_project_lock`, `get_manifest`, `AppError`, `ParamsInvalid`, `PrerequisiteMissing`, `ArtifactMissing` are owned by Plans 1/2; the new errors `ExplorerStateMissing`, `ThumbnailMissing`, `RawImageMissing` are introduced in Task 3 of this plan.
- `save_explorer_state` / `load_explorer_state` are existing wrappers in `src/flake_analysis/pipeline/explorer.py` (referenced by Tasks 11 and 12).

### Test discipline check

- Every test step shows a complete `it(...)` body with real assertions; no `// ...` truncation.
- Every static-route test asserts `Cache-Control: public, max-age=86400, immutable` and an `ETag` header (Tasks 13 and 14).
- Every `/static/thumbnails/...` and `/static/raw/...` test includes a path-traversal negative test (`?stem=../../../etc/passwd` → 400 / `/static/raw/../etc/passwd` → 400) (Tasks 13 and 14).
- The OSD wrapper test mocks `openseadragon` via `vi.mock('openseadragon', () => ({ default: vi.fn(() => mockViewer) }))` (Task 26 and all downstream tests that mount the canvas).

---

## Execution Handoff

- Total tasks: 34.
- Phase breakdown:
  - Phase 1 (schemas + path safety + errors): Tasks 1-3
  - Phase 2 (services): Tasks 4-6
  - Phase 3 (explorer routes): Tasks 7-10
  - Phase 4 (state save/load routes): Tasks 11-12
  - Phase 5 (static routes with safe_join + ETag + Cache-Control): Tasks 13-14
  - Phase 6 (frontend state + API client + hooks): Tasks 15-19
  - Phase 7 (right-rail control panels): Tasks 20-24
  - Phase 8 (mosaic canvas + detail components): Tasks 25-29
  - Phase 9 (composers + page + integration): Tasks 30-34
- Run order: tasks must be executed in numerical order; later tasks reference modules/types created earlier.
- Test runner reminders:
  - Backend: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_<file>.py -v`
  - Frontend: `cd /Users/houkjang/projects/stand-alone-analyzer/web && npx vitest run src/<path>`
- Commit discipline: never `--no-verify`; never modify git config; one commit per task using the exact message templates shown.
- The controller (not the executor) performs the final review commit and the merge.


