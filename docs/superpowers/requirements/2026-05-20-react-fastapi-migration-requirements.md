# React + FastAPI Migration — Requirements

**Date:** 2026-05-20
**Status:** Stage 1 (requirements only — NO design)
**Source app:** `stand-alone-analyzer` v0.2.18 (Streamlit single-page)
**Target stack (decided):** React frontend + FastAPI backend + OpenSeadragon for Explorer

This document captures WHAT must survive (and what is explicitly cut)
in the migration. Architecture, components, libraries beyond the three
already chosen, and implementation details are out of scope here.

---

## 0. Context (one screen)

The app today is a 4-tab Streamlit single-page tool:

1. **Compute** — runs Thumbnails → Background → Domain Stats → Domain
   Proximity batch jobs (`src/flake_analysis/ui/tab_compute.py:38-415`,
   per-step pipeline wrappers in `src/flake_analysis/pipeline/*`).
2. **Selector** — 5-metric range filter + interactive scatter +
   image preview + Commit (`src/flake_analysis/ui/tab_selector.py`).
3. **Clustering** — manual seed-group GMM with live posterior +
   Mahalanobis-distance gates (`src/flake_analysis/ui/tab_clustering.py`).
4. **Explorer** — substrate-grid raw-image mosaic (LOD 0–2 + raw
   fallback), include/exclude cluster picker, neighbor filter, flake
   list, DetailPanel (`src/flake_analysis/ui/tab_explorer.py`).

State is persisted under a user-selected `analysis_folder/` with
`manifest.json` plus 7 numbered subdirs (see
`src/flake_analysis/state/paths.py:23-46`). Inputs come from a sidebar
(`raw_images/`, `annotations.json`, `analysis_folder/`).

Migration trigger: Streamlit's full-rerun model + Plotly's missing
zoom-relayout signal make the Explorer mosaic load full-resolution
data even when the user is zoomed in to a small viewport. Tab-Explorer
has a 4-entry numpy-mosaic cache (`tab_explorer.py:453-525`) that
accumulates GB of pixels; Ctrl+C does not free it cleanly.

**End goal:** public web service. **v1:** ship single-user only.
Multi-user features are out, but the design must not preclude them.

---

## 1. User stories (functional)

Each story has Priority (M = Must / S = Should / C = Could / W = Won't
for v1), affected modules, and acceptance criteria. "Acceptance"
bullets are the contract the new stack has to satisfy and should be
the basis for tests.

### 1.1 Cross-cutting / sidebar / persistence

#### US-X1 — Configure project paths (M)
As a scientist, I open the app, point it at three paths
(`raw_images/`, `annotations.json`, `analysis_folder/`), and continue
where I left off.
- AC: After setting `analysis_folder/`, if `manifest.json` exists,
  `raw_images_dir` and `annotations_path` are auto-filled from it
  (current: `ui/sidebar.py:44-99`).
- AC: User can override either auto-filled path before any compute
  step runs.
- AC: First compute step that runs writes its paths into
  `manifest.json` top-level (`stamp_top_level`, `state/manifest.py:62-95`).
- AC: Path validation: backend must verify each path exists and is the
  right kind (dir/file) before any compute call; otherwise return a
  structured error the UI can show inline.
- AC: A "Reload manifest" action re-reads `manifest.json` and refreshes
  the UI (current: `ui/sidebar.py:129-134`).

#### US-X2 — See pipeline status at a glance (M)
As a scientist, I see which of the 7 pipeline steps are
`not_started` / `done` / `stale` for the current `analysis_folder`.
- AC: All 7 steps from `state/paths.py:8-16` are listed:
  thumbnails, background, domain_stats, selector, clustering,
  domain_proximity, explorer.
- AC: Status mirrors `step_status()` (`state/manifest.py:98-112`),
  including the `stale` warn-only state once stale detection lands
  upstream of the migration.
- AC: Status updates without a full page reload after a compute step
  finishes.

#### US-X3 — Resume work across sessions (M)
As a scientist, I close the browser, come back tomorrow, point at the
same `analysis_folder/`, and the UI shows me the same Selector filter
ranges, Clustering seed groups, Clustering thresholds, and Explorer
include/exclude picks I left.
- AC: All persisted state lives in the analysis folder (no browser
  localStorage, no server-side per-user DB) — see §2.5.
- AC: Selector: filter values reload from manifest `params` block
  if available; UI defaults if not.
- AC: Clustering: seed groups reload from
  `04_clustering/seed_groups.json` (current: `tab_clustering.py:60-82`,
  `_maybe_autoload_seed_groups`); per-cluster thresholds reload from
  `labels.json["thresholds"]`.
- AC: Explorer: include/exclude + neighbor filter reload from
  `06_explorer/explorer_state.json` (current: `pipeline/explorer.py:128-133`).

#### US-X4 — Bilingual UI strings (S)
As a Korean scientist who occasionally pairs with English-only
collaborators, I want UI labels available in both Korean and English.
- AC: All user-visible strings (labels, button text, tooltip help,
  toast messages, error messages from the backend) come from a single
  string table per locale.
- AC: Language picker persists per-browser (cookie or localStorage).
- AC: Currently the codebase is English-only with Korean only in
  source-code comments — Korean UI is net-new but the help text in the
  current Selector tab (e.g. `tab_selector.py:140-149`) shows the
  existing Korean-speaker assumption.

### 1.2 Compute tab

#### US-C1 — Run a single compute step with custom params (M)
- AC: Per-step params preserved (`tab_compute.py:124-411`):
  - Thumbnails: `quality`, `force_recompute`, `raw_ext`.
  - Background: `seed`, `max_images`, `gaussian_sigma`, `method`.
  - Domain Stats: `repr_mode`, `raw_ext`.
  - Domain Proximity: `r_max_px`, `d_touch_px`, `link_distance_um`,
    `min_area_px`, `pixel_size_um`, `workers`.
- AC: Progress contract `(pct ∈ [0,1], status string)` streams to UI
  at ≥ 1 update / sec (`pipeline/thumbnails.py:27`).
- AC: Success → summary message (e.g. "n_images / n_skipped / n_failed").
  Error → inline message, page does NOT crash, manifest unchanged.
- AC: Step prerequisites enforced server-side: Domain Stats requires
  Background; Selector requires Domain Stats; Clustering requires
  Selector; Explorer requires Clustering AND Domain Proximity.

#### US-C2 — Run All in sequence (M)
- AC: Four progress bars visible simultaneously.
- AC: First failure halts subsequent steps; prior successes keep
  their manifest entries.

#### US-C3 — Background preview (S)
- AC: Downsampled (~4×) preview of `01_background/background.npy`
  when present (`tab_compute.py:266-287`); rendering non-blocking.

#### US-C4 — Local thumbnail cache for SMB analysis folders (S)
- AC: Auto-redirect WebP writes to
  `~/.cache/stand-alone-analyzer/thumbnails/<sha>/` for network mounts
  + opt-in via env var `STAND_ALONE_THUMB_LOCAL_CACHE=1`
  (`core/pipeline/thumbnails.py:21-38`).
- AC: UI shows the resolved cache path + "Clear cache" button when a
  cache exists (`tab_compute.py:198-217`).
- AC: `index.json["cache_dir"]` records the absolute cache path so
  Explorer reads tiles without re-deriving.

### 1.3 Selector tab

#### US-S1 — Filter domains via 5-metric ranges (M)
- AC: Five metrics with `(min, max)` bounds: area, std_r, std_g,
  std_b, sam2. Each gets a range slider + two number inputs that
  stay in sync (`tab_selector.py:200-234`). Sync should be natural
  in the new stack — today's three-surface workaround
  (`tab_selector.py:101-130`) is a Streamlit-only artifact.
- AC: "Select All" / "Reset filter" presets clear all bounds.
- AC: Missing `sam2` column → bounds silently ignored
  (`tab_selector.py:79-84`).
- AC: Live counters: Accepted N/total (%), Rejected, Selected
  (lasso), "Will commit" prediction.

#### US-S2 — Inspect filtered domains in a 2D scatter (M)
- AC: X/Y axis picker over: R, G, B, area, std_r, std_g, std_b, sam2.
- AC: Accepted = green (`#43a047`), selected-but-rejected = amber
  (`#fbc02d`) (`tab_selector.py:489-490`).
- AC: ≥5,000 visible points must remain interactive at 30 fps. Today
  caps at 5,000 (`tab_selector.py:295`) — keep cap or lift with a
  smarter representation, but selected ids must always be visible
  even when total exceeds the cap (`tab_selector.py:299-330`).

#### US-S3 — Brush via lasso / box / single-click / zoom (M)
- AC: Five interaction modes: Replace (default), Add, Subtract,
  Single-pick, Zoom (`_brushing.py`).
- AC: Undo / Redo / Clear-selection on brushing history.
- AC: Selection survives axis swaps and slider edits — v0.2.5
  regression fix at `tab_selector.py:380-411`.

#### US-S4 — Raw image preview for focused domain (M)
- AC: Focus precedence: row click in flake list > scatter
  single-click > first id of lasso (`_focus_domain_id`,
  `tab_selector.py:695-708`).
- AC: Side-by-side scatter / preview layout, equal height (~520 px).
- AC: Pan + wheel-zoom on the preview; segmentation contour
  toggleable.
- AC: Missing/unparseable `annotations.json` → clear "missing" state,
  never a crash.

#### US-S5 — Optional 3D RGB scatter (S)
- AC: Off by default; display-only (no lasso events) when on
  (`tab_selector.py:875-888`).

#### US-S6 — Sort / export the filtered list (S)
- AC: Table columns: domain_id, area_px, mean_r/g/b, std_r/g/b%,
  sam2, status (`tab_selector.py:559-595`). Row click → focus.
- AC: "Export filtered (CSV)" always enabled; "Export selected (CSV)"
  enabled only when lasso buffer is non-empty.

#### US-S7 — Commit selection (M)
- AC: Empty lasso → commit all Accepted as `selected=True`.
  Non-empty lasso → commit Accepted ∩ Selected
  (`tab_selector.py:711-789`).
- AC: Commit updates manifest `selector` step (params, params_hash,
  input_hashes — `pipeline/selector.py`).
- AC: Commit accessible from both sidebar drawer and a body-level
  button.

### 1.4 Clustering tab

#### US-CL1 — Build seed groups by lassoing in scatter (M)
- AC: ≥2 seed groups required to enable Fit GMM
  (`tab_clustering.py:944-945`).
- AC: Add / Rename / Remove / Clear-all actions on the seed group list.
- AC: Edit-group dropdown highlights its members with an orange ring
  in the scatter (`tab_clustering.py:621-633`).
- AC: First-visit auto-load from `04_clustering/seed_groups.json`
  ONLY when the in-memory list is empty — never clobber unsaved
  edits (`_maybe_autoload_seed_groups`, `tab_clustering.py:60-82`).
- AC: "Reload last fit's seeds" and "Import clusters → seeds" actions
  available when a fit is on disk.

#### US-CL2 — Fit GMM with chosen scope and distance gate (M)
- AC: Fit-scope picker: "Seeds only (recommended)" (default) or "All
  selected (legacy)" (`tab_clustering.py:949-960`).
- AC: Initial Mahalanobis distance gate slider 0.5–6.0 σ, default 3.0
  (`tab_clustering.py:973-979`).
- AC: GMM uses `random_state=42` and `means_init` from seed groups —
  byte-for-byte reproducible per `tests/parity/test_reproducibility.py`.
- AC: Result summary: n_clusters, n_assigned, n_unassigned, plus
  mapping diagnostics when seed/selected ids were dropped.

#### US-CL3 — Live-preview per-cluster threshold + distance gate (M)
- AC: Per-cluster posterior threshold slider [0..1, step 0.01], live
  recolor of scatter, "K / N pass" caption per cluster
  (`tab_clustering.py:805-866`).
- AC: Live Mahalanobis distance gate slider 0.5–8.0 σ filters by
  `assignments.parquet[nearest_mahalanobis]`
  (`tab_clustering.py:749-787`).
- AC: Reset-to-0.50 button restores the default per-cluster threshold.

#### US-CL4 — Commit clustering to disk (M)
- AC: "Commit clustering" rewrites `assignments.parquet[cluster_label]`
  with the live threshold + distance gate baked in (`apply_thresholds`,
  `tab_clustering.py:869-906`).
- AC: Manifest `clustering` step entry updated with new params.

#### US-CL5 — Cluster size bar chart (S)
- AC: One bar per cluster, sized by member count
  (`tab_clustering.py:909-922`).

### 1.5 Explorer tab — performance-critical

#### US-E1 — View raw-image mosaic with viewport-only loading (M)
As a scientist, I see a "Google Maps style" mosaic of all raw image
tiles laid out by their `ix###_iy###` filename coordinates (or
sqrt-fallback). I pan / wheel-zoom freely; only the visible tiles at
the right LOD are fetched.
- AC: Tile coordinate parser preserved (`tab_explorer.py:323-342`).
- AC: Y-axis flip preserved: `iy=0` renders at the bottom row (matches
  cataloger scan convention; v0.2.17 fix — `tab_explorer.py:404-440`).
- AC: At any zoom level, peak browser RAM ≤ 1 GB on a 60×60 = 3600-tile
  dataset. (Current Streamlit baseline: GB-scale, accumulates across
  reruns.)
- AC: Pan / zoom interaction must update the visible tile set within
  150 ms of a wheel/drag event (no full-mosaic rebuild on pan).
- AC: LOD pyramid uses the existing 4 tiers:
  lod0 64×40, lod1 192×120, lod2 480×300, raw ≈ 1920×1200
  (`core/pipeline/thumbnails.py:57-61` + raw fallback).
- AC: Auto-LOD and manual LOD picker (current: `tab_explorer.py:613-639`).
- AC: Closing the browser tab releases all tile memory within 30 s
  (current bug: cache survives Ctrl+C).

#### US-E2 — Filter mosaic by clusters + neighbor filter (M)
- AC: Cluster picker = two stacked multiselects (Include / Exclude)
  with conflict warning when same name appears in both
  (`tab_explorer.py:208-246`).
- AC: NeighborFilter: size range (M, active today), isolation ≥ N px
  (S, placeholder), exclude border-clipped (C, placeholder) —
  matching today's status (`tab_explorer.py:249-285`).
- AC: Tiles whose `image_id` has zero passing flakes are visibly
  receded (today: gray + 50% white blend, `tab_explorer.py:512-519`).
  New stack may render this differently as long as pass/fail is
  unambiguous.

#### US-E3 — Click a tile to drill in (M)
- AC: Click sets selected flake = first flake on that tile
  (`tab_explorer.py:826-842`).
- AC: Selected tile gets a 3-px gold border (`tab_explorer.py:764-780`).
- AC: Tile hover shows `image {iid}: {n_pass}/{n_total} pass`.

#### US-E4 — Flake list + DetailPanel (M)
- AC: Sortable flake list with single-row click → select flake.
  Columns: flake_id, image_id, domains, groups, distance, clipped
  (`tab_explorer.py:856-906`).
- AC: DetailPanel shows Identity / Labels / Distance; Geometry and
  MaskStats are deferred (today's "M3 polish" stays deferred —
  `tab_explorer.py:959-969`).
- AC: When no flake selected, show legend explaining filter /
  selection visual conventions (`tab_explorer.py:917-928`).

#### US-E5 — Save explorer state (M)
- AC: Writes `06_explorer/explorer_state.json` and
  `selected_flakes.parquet`; manifest `explorer` step entry updated
  with params + upstream input_hashes (`pipeline/explorer.py:38-125`).

#### US-E6 — Backwards-compatible thumbnail cache (M)
- AC: If `index.json` lacks `cache_dir`, fall back to in-folder
  layout `00_thumbnails/lod{N}/<stem>.webp`
  (`tab_explorer.py:381-386`, `core/pipeline/thumbnails.py:35-38`).

### 1.6 Explicitly descoped for v1

See §3 — auth, sharing, email, accounts, cloud storage backends, job
queues, public API are all out for v1.

---

## 2. Non-functional requirements

### 2.1 Performance budgets

#### Explorer mosaic (the migration motivation)

| Metric | Target | Notes |
|---|---|---|
| Pan latency (visible tile update) | ≤ 150 ms | from wheel/drag to next paint at the same LOD |
| Zoom-step latency (LOD switch) | ≤ 500 ms | including fetch of higher-LOD tiles for the new viewport |
| Initial mosaic render (60×60 grid, lod0) | ≤ 2 s | cold open of the Explorer tab |
| Peak browser RAM (60×60 grid) | ≤ 1 GB | hard ceiling — current bug accumulates GBs |
| Peak server RAM per active session | ≤ 500 MB | tile serving must stream, not hold full mosaic |
| Tile fetch concurrency | ≤ 8 in flight | avoid SMB read storms |
| Memory release after tab close | ≤ 30 s | both client and server |

These targets assume the existing LOD pyramid sizes
(`core/pipeline/thumbnails.py:57-61`) — lod0 64×40, lod1 192×120,
lod2 480×300, raw ~1920×1200 — and a 60×60 dataset where lod0 ≈
3.6 MB / lod1 ≈ 32 MB / lod2 ≈ 200 MB / raw ≈ 12 GB uncompressed.

#### Compute / Selector / Clustering — batch-style

These tabs are NOT performance-critical. Acceptable budgets:

| Action | Budget |
|---|---|
| Selector filter slider drag → scatter repaint | ≤ 500 ms for ≤ 5,000 visible points |
| Selector axis swap → scatter repaint | ≤ 500 ms |
| Clustering Fit GMM (1,000 domains) | ≤ 5 s — bound by sklearn, not UI |
| Clustering threshold slider drag → scatter recolor | ≤ 300 ms |
| Compute step run | streaming progress callbacks ≥ 1 update / sec |

### 2.2 Reliability & recovery

- **R1 (M):** Compute step failure must not corrupt `manifest.json`.
  Today the wrappers stamp manifest only after the core step returns
  (`pipeline/selector.py:87-98` etc.) — preserve that invariant.
- **R2 (M):** Long-running compute steps must be cancellable from the
  UI. Currently they are not — Streamlit reruns can leave Python
  workers running in the background.
- **R3 (S):** If two clients open the same `analysis_folder/`
  simultaneously and both write, the one that wrote last wins; the
  other client should detect the conflict on next read and prompt the
  user to reload. (The system does NOT need to merge concurrent edits.)
- **R4 (M):** Backend startup must not hang when an analysis folder
  is mid-write. Manifest loader (`state/manifest.py:38-49`) tolerates
  missing files; same tolerance must extend to corrupted JSON
  (treat as "not started").
- **R5 (M):** No zombie cache after browser-tab close. Today's
  `@st.cache_data(max_entries=4)` mosaic cache (`tab_explorer.py:453`)
  is the source of the perceived memory leak.

### 2.3 Multi-user-readiness without multi-user features

The system is single-user for v1, but design must NOT bake assumptions
that block the public-service migration later. Concrete things to
avoid:

- **N1 (M):** No global mutable state on the backend that depends on
  "the current project". Every backend request must carry the
  analysis-folder identifier (or equivalent project handle)
  explicitly. Today's Streamlit state (`st.session_state`) is
  process-global and tab-scoped — that pattern doesn't survive.
- **N2 (M):** URL routes must be parameterised by project / view.
  No `/explorer` that implicitly references "the current project";
  routes must look like `/projects/{pid}/explorer` (or equivalent
  scheme) so deep links work even when the project switches.
- **N3 (M):** No hardcoded local file paths in client code. All
  filesystem access goes through backend endpoints. The frontend
  must be able to run on a different host than the data.
- **N4 (M):** Authentication hook present but no-op for v1. Backend
  request handlers should accept an "identity" parameter (even if
  it's always `local`). Adding real auth later must not require
  rewriting every endpoint.
- **N5 (S):** Per-project state must NOT live in browser localStorage
  alone — it must round-trip through the backend so multi-device use
  works in v2.
- **N6 (M):** Long-running compute steps must support a job-handle
  pattern (start → poll → cancel) — synchronous request/response works
  for v1 only because there is one user; v2 needs the handle.
- **N7 (S):** Logging must include a request id / project id so
  per-user log filtering works in v2 without retrofitting.
- **N8 (M):** No client-side execution of arbitrary filesystem paths
  the user types. Path validation happens server-side; client only
  ever sees opaque project handles or already-validated paths.

### 2.4 Data location assumptions and unknowns

Today's three input paths and where they live:

- `raw_images/` — local filesystem directory of PNGs (typ. ~1920×1200
  microscope tiles named `ix###_iy###.png`). Often on an SMB share.
- `annotations.json` — local file, COCO+RLE format (e.g., from SAM2),
  same parent as `raw_images/` typically.
- `analysis_folder/` — local writable directory. Sometimes on SMB,
  hence the local cache redirect in `core/pipeline/thumbnails.py`.

For v1, these stay local-filesystem. Open questions are flagged in §4.
The migration MUST NOT lock paths to "next to the backend process":

- **D1 (M):** Backend may serve the React frontend remotely; data
  paths the user types must still be valid on the backend host.
- **D2 (M):** Tile serving must accept either the existing in-folder
  WebP layout OR the redirected local cache layout; both should be
  resolved server-side by reading `index.json["cache_dir"]`
  (current: `tab_explorer.py:381-386`).
- **D3 (S):** Where the LOD cache lives (per-project under
  `analysis_folder/`, per-user under `~/.cache/`, per-server under
  `/var/cache/`) must be configurable per-deployment, not hardcoded.

### 2.5 Backwards compatibility

The new stack must read existing analysis folders without
re-computation. This is a hard requirement — users have multi-day
compute runs they will not re-do.

- **B1 (M):** `manifest.json` v1 schema (current
  `state/manifest.py:13-49`) reads unchanged. Any new fields are
  additive and optional.
- **B2 (M):** All step output artifacts read unchanged:
  - `00_thumbnails/index.json` + per-LOD WebP folders
  - `01_background/background.npy`
  - `02_domain_stats/stats.npz`
  - `03_selector/selection.parquet`
  - `04_clustering/{labels.json, assignments.parquet, gmm_model.pkl, seed_groups.json}`
  - `05_domain_proximity/{distances.parquet, flake_assignments.parquet}`
  - `06_explorer/{explorer_state.json, selected_flakes.parquet}`
  See `state/paths.py:34-46`.
- **B3 (M):** Tolerate column-name drift already handled by the
  current code: `cluster_label` ↔ `cluster_id`, `max_posterior` ↔
  `posterior_p` (current: `tab_explorer.py:84-89`).
- **B4 (M):** Tolerate `image_id` missing from
  `flake_assignments.parquet` and reconstruct from `annotations.json`
  (current: `tab_explorer.py:93-110`).
- **B5 (S):** The reproducibility contract (`random_state=42`, fixed
  seed pipeline) verified by `tests/parity/test_reproducibility.py`
  must continue to pass against the new wrappers.

### 2.6 Browser & accessibility

- **A1 (M):** Latest two stable releases of Chrome, Firefox, Safari,
  Edge.
- **A2 (S):** Keyboard navigation through tabs, sidebar controls,
  flake list. (Today the keyboard shortcuts injected by
  `_brushing.render_keyboard_shortcuts()` are best-effort and Streamlit
  iframe-blocked — the new stack should make this reliable.)
- **A3 (S):** Color contrast ≥ WCAG AA for non-decorative text.
  Color-blind-safe palette: today's d3 category10 palette
  (`tab_explorer.py:39-42`) is NOT colorblind-safe; either keep it
  for parity with screenshots or switch (open question §4).
- **A4 (C):** Screen-reader labels on all icons / image-only buttons.

### 2.7 Localization

- **L1 (S):** UI string table supports English and Korean (see
  US-X4). All user-facing strings (including backend error messages
  surfaced in the UI) must be localizable.
- **L2 (C):** Number / date formatting is locale-aware (e.g., 1,000
  vs 1 000).
- **L3 (M):** Source code comments in Korean (which exist today —
  e.g., `tab_selector.py:144-149`) are not user-facing and don't need
  translation.

### 2.8 Logging & observability

- **O1 (M):** All compute steps log via `logging` (stdlib), never
  `print()` (CONTRIBUTING.md:23). Backend must surface these logs to
  the developer (file or stdout); the frontend must surface
  user-facing errors only.
- **O2 (S):** Per-request log lines include a request id + project id
  (see N7).
- **O3 (M):** Startup banner with version + git commit (current:
  `app/streamlit_app.py:7-37`) survives — backend logs once on boot.

### 2.9 Testing

- **T1 (M):** Backend pipeline wrappers have the same test coverage
  as today (44 tests, full suite < 60 s — `CONTRIBUTING.md:34`).
- **T2 (M):** Parity harness in `tests/parity/` continues to pass
  byte-for-byte against the same fixtures (reproducibility contract).
- **T3 (S):** Frontend has a smoke-test layer that loads each tab
  with a fixture analysis folder and asserts no console errors.
- **T4 (C):** Explorer mosaic has a perf regression test pinning the
  budgets in §2.1.

---

## 3. Out of scope for v1

Concise list with one-line justifications. (Same content as §1.6 plus
infra cuts.)

| # | Item | Why cut |
|---|---|---|
| O1 | Multi-user auth / accounts / passwords | v1 is solo; auth hook (N4) is enough |
| O2 | Sharing links / collaboration | scope explosion |
| O3 | Email / notifications | no infra to send |
| O4 | S3 / GCS / cloud storage | local FS works; abstraction added later (D3) |
| O5 | Pipeline queue / overnight jobs | one user, run synchronously |
| O6 | Public REST/GraphQL API | internal API for our React FE only |
| O7 | Per-user theming | dark mode etc. — nice but not v1 |
| O8 | Mobile / tablet layouts | scientists use desktop |
| O9 | Bbox / outline overlay rendering on mosaic | already deferred today (`tab_explorer.py:21-22`) |
| O10 | Geometry + MaskStats sections of DetailPanel | already deferred today |
| O11 | Stale-detection auto-invalidation | warn-only is sufficient (current TODO at `state/manifest.py:109-111`) |
| O12 | Linked brushing across multiple panes (legacy 4-pane) | replaced by single configurable pane in v0.2.2; we keep the new behavior |
| O13 | Plotly modebar parity | OpenSeadragon has its own controls; do not replicate |
| O14 | CSV export of arbitrary tables | only filtered + selected from Selector (US-S6) |
| O15 | Real-time multi-tab sync inside one browser | rare; if user opens two tabs they see eventual consistency |

---

## 4. Open questions (for the user)

> **2026-05-20 — All answered. See §4.99 for resolutions; original list kept
> below for traceability.**

Each is a yes/no or A/B/C with a short why.

### Storage & deployment

1. **Q-S1 (single-host vs split).** v1 backend and frontend on the
   same host (scientist's laptop), or already split (e.g. backend on
   a small Linux box, frontend served from anywhere)? Affects URL
   schemes (N2) and CORS posture.
2. **Q-S2 (analysis_folder location).** v1: always local FS on the
   backend host (yes / no)? If "no", which of {SMB mount, S3, NFS}
   must work day-1?
3. **Q-S3 (raw_images access).** Are `raw_images/` always on the same
   filesystem as `analysis_folder/`, or can they be split (e.g. raw
   images on a read-only share, analysis folder on local disk)?
4. **Q-S4 (thumbnail cache location).** Keep today's per-user
   `~/.cache/stand-alone-analyzer/thumbnails/<sha>/` redirect, or move
   to a per-project `analysis_folder/.cache/` so it travels with the
   project?
5. **Q-S5 (project switching).** v1 supports one analysis folder at a
   time (yes / no), or do we want a project picker that lists recent
   projects?

### Performance

6. **Q-P1 (mosaic dataset size).** What is the realistic upper bound
   for v1 — 60×60 (3600 tiles), 100×100 (10k), or larger? Affects the
   §2.1 budgets.
7. **Q-P2 (concurrent compute jobs).** Should v1 allow a Compute step
   to run while the user browses the Explorer (yes — needs job handle
   N6), or is "modal: cannot navigate while computing" acceptable?

### Migration scope

8. **Q-M1 (3 non-Explorer tabs).** Migrate Compute, Selector,
   Clustering 1:1 to React in v1, OR ship Explorer-only first and
   keep the other three on Streamlit during a transition?
9. **Q-M2 (visual parity).** Match today's d3 category10 palette
   exactly (for screenshot continuity), OR adopt a colorblind-safe
   palette in the migration (one-time visual change)?
10. **Q-M3 (backend language).** Must the backend stay Python (so
    pipeline wrappers in `src/flake_analysis/pipeline/*` are reused
    in-process), or are we open to a thin Python compute service +
    different-language API gateway?

### UX

11. **Q-U1 (locale).** Is bilingual Korean/English required at v1
    launch, or is English-only acceptable for v1 with i18n
    infrastructure in place?
12. **Q-U2 (Explorer tile interaction).** Click-tile-to-select-flake
    today picks the *first* flake in the tile. Keep this behavior, or
    surface a popup to pick from the tile's flakes?
13. **Q-U3 (raw image preview parity).** Selector tab's image preview
    today is a Plotly figure with wheel-zoom + contour toggle. Keep
    Plotly there for v1 (only Explorer is OpenSeadragon), or
    standardise on OpenSeadragon for both?
14. **Q-U4 (linked brushing on/off).** Today's brushing state is
    per-tab (Selector vs Clustering have independent states). Keep
    that, or unify them so a lasso in Selector carries into
    Clustering?

### Compatibility & rollback

15. **Q-C1 (parallel run).** Does the Streamlit app keep running
    against the same `analysis_folder/` during migration (must read
    new artifacts without breaking), or is the cutover atomic?
16. **Q-C2 (manifest schema).** Are we allowed to bump
    `MANIFEST_VERSION` and add new top-level fields in v1, or must we
    stay on v1 schema for read-back parity with v0.2.18?
17. **Q-C3 (deprecate `gmm_model.pkl`).** The pickle file is not used
    by the UI; is it still a hard requirement for downstream tooling
    (we keep writing it), or can we make it optional in v1?

### 4.99 Resolutions (2026-05-20)

| Q | Decision | Implication |
|---|---|---|
| Q-S1 | Backend on host with SMB access; frontend may be split. CORS required. | API URLs use absolute origins; `Access-Control-Allow-Origin` configured per environment. |
| Q-S2 | `analysis_folder/` lives on **SMB-mounted** filesystem accessible to backend. | All file IO must tolerate SMB latency. Local-disk caches are mandatory, not optional. |
| Q-S3 | `raw_images/` also SMB. May or may not be same mount as `analysis_folder/`. | Same — assume slow reads; thumbnail pyramid + cache critical. |
| Q-S4 | Keep `~/.cache/stand-alone-analyzer/thumbnails/<sha>/` local cache. | Already implemented in `core/pipeline/thumbnails.py:104-116`; preserve in port. |
| Q-S5 | One project at a time in v1. Recent-projects picker is post-v1. | URL routing can be stateless `/projects/<id>/...` but UI exposes one. |
| Q-P1 | Mosaic upper bound: **60×60 (3,600 tiles)**. | OpenSeadragon "image = tile" model is sufficient. No DZI/TileLayer pyramid needed for v1. |
| Q-P2 | Synchronous compute is acceptable (1-user). | No job queue v1; `/api/run/<step>` blocks for the duration with SSE progress. |
| Q-M1 | **All 4 tabs** ported to React in v1. Streamlit fully removed at cutover. | Bigger v1 but no operational duality. |
| Q-M2 | Keep d3 category10 palette for screenshot continuity. | Port `tab_explorer.py:39-42` palette as-is; colorblind-safe is post-v1 (#13). |
| Q-M3 | Backend stays Python; pipeline wrappers imported in-process. | FastAPI app directly imports `flake_analysis.pipeline.*`. No process-boundary serialization. |
| Q-U1 | English only at v1 launch. i18n table infrastructure must exist (en table only). | All user-visible strings go through translation function from day 1. |
| Q-U2 | Click-tile-to-select keeps current behavior (first flake in tile). | `tab_explorer.py:837-842` semantics preserved. |
| Q-U3 | Selector preview: lightweight HTML `<img>` + simple zoom. **NOT** OpenSeadragon. | OpenSeadragon scope: Explorer mosaic only. Selector preview is single raw image (`tab_selector` 's `_image_preview.py`). |
| Q-U4 | Brushing stays per-tab independent. | `BrushingState` instances remain keyed by tab name — no cross-tab event bus. |
| Q-C1 | Atomic cutover at v1 release; no parallel run requirement. | Streamlit code can be deleted in the same release. |
| Q-C2 | Manifest schema stays v1. | No schema migration; new metadata added under `params` blocks if needed. |
| Q-C3 | `gmm_model.pkl` keeps being written for now (defer deprecation decision). | Confirm with downstream tooling before removing post-v1. |

---

## 5. Appendix — current code references

All inline `path:line` citations point at v0.2.18 source. Top of each
tree: `app/streamlit_app.py`, `src/flake_analysis/ui/`,
`src/flake_analysis/pipeline/`, `src/flake_analysis/state/`,
`src/flake_analysis/core/pipeline/`, `tests/` (44 tests, `tests/parity/`
for reproducibility).
