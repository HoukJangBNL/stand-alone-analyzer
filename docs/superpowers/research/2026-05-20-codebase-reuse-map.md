# Codebase Reuse Map — Streamlit → React+FastAPI Migration

Researcher report (Stage 1 of migration brainstorming, 2026-05-20).
Source: agent run on `main` @ 3b12f99.

---

## A. Reuse Classification Table

Legend: **REUSE** = no Streamlit, drop into FastAPI as-is · **WRAP** = pure logic, no
Streamlit, but called from UI; trivial to expose · **REWRITE** = Streamlit-bound, must
be replaced · **EXTRACT** = mixed; has reusable pure helpers + Streamlit-bound shell

| File | Lines | Class | Streamlit Imports | Notes |
|---|---|---|---|---|
| `src/flake_analysis/__init__.py` | 3 | REUSE | none | Just `__version__` |
| `src/flake_analysis/state/paths.py` | 57 | REUSE | none | `PIPELINE_STEPS`, `SUBDIRS`, `ARTIFACTS`, `step_dir()`, `manifest_path()` |
| `src/flake_analysis/state/manifest.py` | 112 | REUSE | none | `Manifest`, `StepEntry` dataclasses, MANIFEST_VERSION=1 |
| `src/flake_analysis/state/hashing.py` | 29 | REUSE | none | SHA256 params hashing, mtime helpers |
| `src/flake_analysis/state/__init__.py` | 0 | REUSE | none | empty |
| `src/flake_analysis/cache/__init__.py` | 0 | REUSE | none | empty |
| `src/flake_analysis/pipeline/__init__.py` | 0 | REUSE | none | empty |
| `src/flake_analysis/pipeline/background.py` | ~80 | REUSE | none | `run_background_step` — manifest-aware wrapper |
| `src/flake_analysis/pipeline/thumbnails.py` | 89 | REUSE | none | `run_thumbnails_step` |
| `src/flake_analysis/pipeline/domain_stats.py` | ~80 | REUSE | none | `run_domain_stats_step` |
| `src/flake_analysis/pipeline/domain_proximity.py` | ~80 | REUSE | none | `run_domain_proximity_step` |
| `src/flake_analysis/pipeline/selector.py` | ~80 | REUSE | none | `run_selector_step` |
| `src/flake_analysis/pipeline/clustering.py` | ~120 | REUSE-with-caveat | none | `run_clustering_step` + `apply_thresholds` (latter rewrites parquet directly — see §E) |
| `src/flake_analysis/pipeline/explorer.py` | ~60 | REUSE | none | `save_explorer_state` / `load_explorer_state` |
| `src/flake_analysis/core/__init__.py` | small | REUSE | none | |
| `src/flake_analysis/core/_compat.py` | 100 | REUSE | none | Qpress shims: `msg`, `OperationContext`, `AnalysisTree`, `ProgressCallback` |
| `src/flake_analysis/core/pipeline/__init__.py` | small | REUSE | none | Exports `run_background`, `run_clustering`, `run_domain_proximity`, `run_domain_stats`, `run_selector` (note: not `run_thumbnails`) |
| `src/flake_analysis/core/pipeline/background.py` | 116 | REUSE | none | |
| `src/flake_analysis/core/pipeline/domain_stats.py` | 256 | REUSE | none | |
| `src/flake_analysis/core/pipeline/domain_proximity.py` | 244 | REUSE | none | |
| `src/flake_analysis/core/pipeline/selector.py` | 192 | REUSE | none | |
| `src/flake_analysis/core/pipeline/clustering.py` | 350 | REUSE | none | |
| `src/flake_analysis/core/pipeline/thumbnails.py` | 382 | REUSE | none | LOD pyramid + local-cache redirect (`_should_redirect_to_local_cache:104-116`) |
| `src/flake_analysis/core/clustering/engine.py` | 294 | REUSE | none | `InteractiveClusteringEngine`, `random_state=42` constant |
| `src/flake_analysis/core/image_processing/background.py` | 177 | REUSE | none | |
| `src/flake_analysis/core/image_processing/pair_distance.py` | 363 | REUSE | none | |
| `src/flake_analysis/core/annotations/annotation_loader.py` | 404 | REUSE | none | |
| `src/flake_analysis/core/annotations/rle_flake.py` | 212 | REUSE | none | |
| `src/flake_analysis/core/color_classification/loader.py` | 401 | REUSE | none | `compute_and_cache_stats_from_flakes` |
| `app/streamlit_app.py` | 80 | REWRITE | yes | Entry-point shell |
| `src/flake_analysis/ui/__init__.py` | 0 | n/a | none | empty |
| `src/flake_analysis/ui/sidebar.py` | 158 | REWRITE | yes | Path pickers + manifest panel |
| `src/flake_analysis/ui/tab_compute.py` | 414 | REWRITE | yes | Shell only — pipeline calls already isolated |
| `src/flake_analysis/ui/tab_selector.py` | 1011 | EXTRACT | yes | Defines `AVAILABLE_AXES`, `_focus_domain_id`, `_values_for_axis` (consumed cross-tab — §E) |
| `src/flake_analysis/ui/tab_clustering.py` | 1287 | REWRITE | yes | Imports from `tab_selector` |
| `src/flake_analysis/ui/tab_explorer.py` | 1107 | EXTRACT | yes | Has `@st.cache_data(max_entries=4)` on `_build_mosaic_array:453`; mosaic-array builder + LOD picker are pure |
| `src/flake_analysis/ui/_brushing.py` | 841 | EXTRACT | yes | `BrushingState` dataclass + history/redo logic is pure; render funcs are Streamlit |
| `src/flake_analysis/ui/_image_preview.py` | 486 | EXTRACT | yes | `crop_for_domain`, `decode_segmentation_mask`, `contours_for_mask`, `build_image_preview_figure`, `load_annotations_index` are pure; `@st.cache_data` on `_cached_load_annotations:482` |

**Roll-up**: 28 of 35 non-empty modules (80%) have **zero Streamlit imports**. All
`core/` and `pipeline/` and `state/` subtrees are clean. Streamlit is fully isolated
to `ui/` (7 files) plus `app/streamlit_app.py`.

---

## B. Pipeline Boundary Spec (per step)

All steps share `progress_callback: Callable[[float, str], None]` (pct in [0,1] +
status string) per `core/_compat.py`. All wrappers update `manifest.steps[<step>]`
with `StepEntry(completed_at, params, params_hash, input_hashes, outputs)`.

### B.1 Thumbnails (`pipeline/thumbnails.py:31-88`)
- **Inputs**: `analysis_folder`, `raw_images_dir`, `raw_ext='.png'`, `quality=80`, `force_recompute=False`
- **Reads**: raw image files in `raw_images_dir`
- **Writes**:
  - `<analysis>/00_thumbnails/index.json` — always
  - WebP files: either `<analysis>/00_thumbnails/lod{0,1,2}/<stem>.webp` OR `~/.cache/stand-alone-analyzer/thumbnails/<sha>/lod{N}/<stem>.webp` when redirected
- **Manifest entry**: `steps['thumbnails']`, `outputs={'index_json': '00_thumbnails/index.json'}`, `input_hashes={'raw_images_dir_mtime_max': ...}` (`pipeline/thumbnails.py:66-74`)
- **Returns**: `{output_dir, n_images, n_skipped, n_failed, params, params_hash, cache_dir}` (`cache_dir` may be None)
- **Dependencies**: none

### B.2 Background (`pipeline/background.py`)
- **Inputs**: `raw_images_dir`, `analysis_folder`, `seed=0`, `max_images=100`, `gaussian_sigma=10.0`, `method='median'`
- **Writes**: `<analysis>/01_background/background.npy`
- **Manifest entry**: `steps['background']`
- **Dependencies**: none

### B.3 Domain Stats (`pipeline/domain_stats.py`)
- **Inputs**: `raw_images_dir`, `annotations_path`, `analysis_folder`, `repr_mode='median'`, `raw_ext='.png'`
- **Reads**: `01_background/background.npy`
- **Writes**: `<analysis>/02_domain_stats/<artifacts>` (parquet/npz)
- **Dependencies**: requires Background (`tab_compute.py:290-296` — UI gates on `bg_complete`)

### B.4 Selector (`pipeline/selector.py`)
- **Inputs**: `analysis_folder`, `area_min/max`, `std_{r,g,b}_{min,max}`, `sam2_{min,max}`
- **Reads**: `02_domain_stats/`
- **Writes**: `<analysis>/03_selector/<artifacts>`
- **Dependencies**: requires Domain Stats

### B.5 Clustering (`pipeline/clustering.py`)
- **Inputs**: `analysis_folder`, `seed_groups`, `feature_cols`, `covariance_type`, `random_state=42`, `rgb_threshold=0.50`, `cluster_thresholds`, `fit_scope='seeds'`, `max_mahalanobis=3.0`
- **Writes**: `<analysis>/04_clustering/{labels.json, assignments.parquet, gmm_model.pkl, seed_groups.json}`
- **`labels.json` schema** (frozen per Plan v1 r7): `{version=1, n_clusters, groups, assignments, thresholds, noise_label=-1, random_state, fitted_at}`
- **Second entry-point**: `apply_thresholds(analysis_folder, cluster_thresholds, max_mahalanobis)` — **rewrites `assignments.parquet` directly** without going through core (see §E)
- **Dependencies**: requires Selector

### B.6 Domain Proximity (`pipeline/domain_proximity.py`)
- **Inputs**: `annotations_path`, `analysis_folder`, `r_max_px=200`, `min_area_px=10`, `max_area_px=None`, `d_touch_px=2.0`, `pixel_size_um=0.5`, `link_distance_um=5.0`, `workers=4`
- **Writes**: `<analysis>/05_domain_proximity/{pairs.parquet, flakes.parquet}`
- **Returns**: `{n_pairs, n_flakes, n_domains}` (`tab_compute.py:404-408`)
- **Dependencies**: **none** (only needs `annotations_path`, per `tab_compute.py:342`)

### B.7 Explorer (`pipeline/explorer.py`)
- **Not a compute step** — `save_explorer_state(analysis_folder, include_labels, exclude_labels, neighbor_filter, selected_flake_ids)` + `load_explorer_state(...)`
- **Writes**: `<analysis>/06_explorer/explorer_state.json`
- **Dependencies**: reads from clustering + proximity

---

## C. Manifest + On-Disk State Schema

### C.1 `manifest.json` (root of analysis folder)

```python
# state/manifest.py
MANIFEST_VERSION = 1

@dataclass
class StepEntry:
    completed_at: Optional[str]      # ISO8601 UTC
    params: Dict[str, Any]
    params_hash: Optional[str]       # "sha256:..."
    input_hashes: Dict[str, Any]
    outputs: Dict[str, str]          # logical_name -> relative path
    reproducibility: Dict[str, Any]

@dataclass
class Manifest:
    version: int = 1
    created_at: Optional[str]
    raw_images_dir: Optional[str]
    annotations_path: Optional[str]
    analysis_folder: Optional[str]
    flake_core_version: Optional[str]
    steps: Dict[str, StepEntry]      # keys: 'thumbnails','background','domain_stats','selector','clustering','domain_proximity'
```

Backwards-compat fields: `version` field gates schema migration. Currently only v1
exists.

### C.2 Numbered subdirs (`state/paths.py`)

| Step | Subdir | Key artifacts |
|---|---|---|
| thumbnails | `00_thumbnails/` | `index.json` (+ optionally `lod0/`, `lod1/`, `lod2/`) |
| background | `01_background/` | `background.npy` |
| domain_stats | `02_domain_stats/` | parquet/npz |
| selector | `03_selector/` | parquet |
| clustering | `04_clustering/` | `labels.json`, `assignments.parquet`, `gmm_model.pkl`, `seed_groups.json` |
| domain_proximity | `05_domain_proximity/` | `pairs.parquet`, `flakes.parquet` |
| explorer | `06_explorer/` | `explorer_state.json` |

### C.3 LOD pyramid layout (`core/pipeline/thumbnails.py:57-64`)

```python
LOD_SIZES = {0: (64, 40), 1: (192, 120), 2: (480, 300)}  # raw = implicit lod 3
```

### C.4 `index.json` shape (thumbnails) — **two layouts must be supported** (`core/pipeline/thumbnails.py:283-298`)

```json
{
  "version": 1,
  "params": { "raw_images_dir": "...", "raw_ext": ".png", "quality": 80, "lod_sizes": {...} },
  "params_hash": "sha256:...",
  "n_images": N,
  "n_skipped": K,
  "n_failed": F,
  "entries": [
    {
      "raw_name": "tile_0001.png",
      "stem": "tile_0001",
      "outputs": { "lod0": "lod0/tile_0001.webp", "lod1": "...", "lod2": "..." },
      "signature": ["tile_0001.png", 4096, 1700000000.0]
    }
  ],
  "cache_dir": "/abs/path/...."
}
```

**Backwards compat (v0.2.15 vs v0.2.16)**:
- v0.2.15 stored `outputs[lodN]` as `"00_thumbnails/lod{N}/<stem>.webp"` (analysis-folder-relative)
- v0.2.16 stores `"lod{N}/<stem>.webp"` (write_root-relative) + `cache_dir` field
- Resolver at `core/pipeline/thumbnails.py:287-294` handles both shapes — any reader
  **must** preserve this fallback

### C.5 Local-cache redirect rules (`core/pipeline/thumbnails.py:104-116`)

Triggers when:
- `output_dir.resolve()` startswith `/Volumes/` (macOS SMB), OR
- env var `STAND_ALONE_THUMB_LOCAL_CACHE` ∈ `{"1","true","yes"}`

Cache key: `sha256(absolute analysis folder path)[:16]`, rooted at
`~/.cache/stand-alone-analyzer/thumbnails/<digest>/`.

---

## D. Streamlit `session_state` Inventory

Grouped by tab.

### D.1 Sidebar (`ui/sidebar.py`)
- `raw_images_dir`, `annotations_path`, `analysis_folder` — three path strings
- `last_loaded_manifest` — manifest snapshot for change-detection

### D.2 Compute (`ui/tab_compute.py`)
- Param widget keys: `th_quality`, `th_force`, `th_raw_ext`, `bg_seed`, `bg_max_images`, `bg_sigma`, `bg_method`, `ds_repr`, `ds_raw_ext`, `dp_r_max`, `dp_d_touch`, `dp_link`, `dp_min_area`, `dp_pixel_size`, `dp_workers`
- Buttons: `th_compute`, `bg_compute`, `ds_compute`, `dp_compute`, `th_clear_cache`

### D.3 Selector (`ui/tab_selector.py`)
- Pervasive **canonical-store + widget-key rehydrate** pattern: every filter slider has both `selector_<axis>_min/max` (canonical) and `selector_<axis>_min/max__widget` (transient)
- `selector_brushing` — `BrushingState`
- `selector_focus_domain_id` — single-select highlight
- `selector_axis_x`, `selector_axis_y` — current axes

### D.4 Clustering (`ui/tab_clustering.py`)
- Imports `AVAILABLE_AXES`, `_focus_domain_id`, `_values_for_axis` from `tab_selector` (lines 25-29)
- `clustering_brushing` — `BrushingState`
- `clustering_seed_groups` — list of seed-group dicts
- `clustering_feature_cols`, `clustering_covariance_type`, `clustering_rgb_threshold`, `clustering_max_mahalanobis`, `clustering_fit_scope`
- `clustering_thresholds` — per-cluster threshold dict

### D.5 Explorer (`ui/tab_explorer.py`)
- `explorer_include_labels`, `explorer_exclude_labels` — label-filter sets
- `explorer_neighbor_filter` — proximity-filter dict
- `explorer_selected_flake_ids` — current selection
- `explorer.lod` (canonical) + `explorer_lod_choice_widget` (widget)
- `explorer_focus_flake_id` — last-clicked flake
- `explorer_table_selection` — table dataframe selection
- `explorer_render_toggles` — show-overlays/hover/etc. flags

### D.6 Brushing (shared, `ui/_brushing.py`)
- `BrushingState` dataclass: `selected_ids: set[int]`, `history: deque(maxlen=20)`, `redo_stack: list`, `mode: str`, `interaction_mode: str`, `focus_id: Optional[int]`
- One instance per tab keyed by tab name

---

## E. Hidden Coupling / Smells

### E.1 UI tabs read pipeline outputs directly (no API layer)

UI code constructs `Path(analysis_folder) / "<numbered_dir>" / "<file>"` and reads
parquet/json/npy directly — bypassing `pipeline/` and `state/`. Examples:
- `tab_compute.py:266` — reads `01_background/background.npy` directly via `np.load`
- `tab_compute.py:27-35` — `_read_cache_dir_from_index` parses `00_thumbnails/index.json` directly
- `tab_explorer.py` — loads `04_clustering/labels.json`, `04_clustering/assignments.parquet`, `05_domain_proximity/flakes.parquet` directly

**Migration risk**: every direct read site is a future REST endpoint or a leak across
the React/FastAPI boundary.

### E.2 Cross-tab Python import (`tab_clustering` ← `tab_selector`)

`tab_clustering.py:25-29` imports `AVAILABLE_AXES`, `_focus_domain_id`,
`_values_for_axis` from `tab_selector.py`. **EXTRACT** target: move these to a
tab-agnostic module before rewriting either tab.

### E.3 `apply_thresholds` rewrites parquet directly

`pipeline/clustering.py::apply_thresholds(analysis_folder, cluster_thresholds, max_mahalanobis)`
rewrites `04_clustering/assignments.parquet` **without going through
`core/pipeline/clustering.py`**. Fast path that skips refit but creates a second
mutation surface for the same artifact. API must expose this as a distinct endpoint
with its own concurrency guard.

### E.4 `@st.cache_data` decorators (only 2)

- `ui/tab_explorer.py:453` — `@st.cache_data(max_entries=4)` on `_build_mosaic_array(...)`
- `ui/_image_preview.py:482` — `@st.cache_data` on `_cached_load_annotations(...)`

Both wrap pure functions whose pure cores are reusable.

### E.5 `st.rerun()` ubiquity

Every Compute button ends with `st.rerun()` (e.g., `tab_compute.py:105, 189, 215, 260, 330, 409`).
React/FastAPI will need explicit cache-invalidation on step completion.

### E.6 Canonical-store + widget-key rehydrate

Every slider/numeric input has two session_state slots (`<key>` canonical,
`<key>__widget` transient) to survive Streamlit's widget GC. Pure Streamlit
workaround — React form state replaces it entirely.

### E.7 Hardcoded constants frozen by Plan v1

- `random_state=42` (Plan D6.1) — `core/clustering/engine.py`
- `repr_mode='median'` only (Plan v1 r7) — `pipeline/domain_stats.py`
- `noise_label=-1` (labels.json schema)

These are spec-frozen and **must** survive the migration unchanged for parity with
golden fixtures (`tests/parity/`).

### E.8 `core/pipeline/__init__.py` doesn't export `run_thumbnails`

Other `run_*` are exported; `run_thumbnails` is imported directly from the submodule.
Minor inconsistency — fix on first touch.

---

## Summary

**Top 3 reuse wins**:
1. **`src/flake_analysis/state/`** (3 files, 198 lines) — `Manifest`/`StepEntry` dataclasses, params hashing, path conventions. Zero Streamlit, zero coupling.
2. **`src/flake_analysis/core/`** (10 files, ~3000 lines) — all algorithmic code: GMM clustering engine, background extraction, pair-distance, annotation/RLE loaders, color stats. Zero Streamlit imports anywhere.
3. **`src/flake_analysis/pipeline/`** wrappers (7 files) — manifest-aware step runners with `(progress_callback)` contract already shaped like a job-queue/SSE handler.

**Top 3 reuse blockers**:
1. **`apply_thresholds` rewrites `assignments.parquet` directly** (§E.3) — needs API-level concurrency guard.
2. **Cross-tab imports** (`tab_clustering` ← `tab_selector`, §E.2) — must lift to shared module.
3. **Direct `Path(analysis_folder)/...` reads in UI** (§E.1) — every direct read site is a future REST endpoint.

**Quantitative**:
- **28 of 35** non-empty modules (80%) have **zero** Streamlit imports.
- Streamlit confined to **7 files** in `ui/` + `app/streamlit_app.py`.
- **2** `@st.cache_data` decorators, **6+** `st.rerun()` call sites.
- Manifest schema **v1**, but thumbnails `index.json` already supports **two**
  layouts (v0.2.15 + v0.2.16).
- **3** spec-frozen constants anchor parity tests (`tests/parity/`).
