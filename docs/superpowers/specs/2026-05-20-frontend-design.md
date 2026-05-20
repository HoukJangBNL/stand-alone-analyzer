# Frontend Design — React + FastAPI Migration

**Date:** 2026-05-20
**Stage:** Stage 2 (design — informed by requirements + reuse map)
**Scope:** React frontend only. Backend endpoints, OpenSeadragon internals,
and deployment are designed by sibling agents.
**Inputs:** `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md`
(esp. §1, §4.99) and `docs/superpowers/research/2026-05-20-codebase-reuse-map.md`
(esp. §D session_state inventory).

---

## 1. Tech stack

| Concern | Choice | Rationale (≤4 lines) |
|---|---|---|
| Build tool | **Vite 5** | Fast HMR, native ESM, simple dev-proxy to FastAPI; zero-config TS. Webpack/Next.js bring SSR baggage we don't need (this is an internal tool, not SEO surface). |
| UI runtime | **React 18.3** (stable) | Concurrent features (`useTransition`, `useDeferredValue`) help slider→scatter latency budgets (§2.1). React 19 is fine but adds no new must-haves; pin 18.x for v1 stability. |
| Language | **TypeScript 5.4 strict** | Pipeline boundaries (`StepEntry`, `BrushingState`, manifest schema) deserve compile-time guarantees; cuts whole classes of "did you mean cluster_id or cluster_label" bugs (§E.1 of reuse map). |
| Server-state | **TanStack Query v5** | Manifest, stats, assignments, labels, explorer state all map cleanly to query keys. Built-in dedup, stale-time, refetch-on-focus, mutation cache invalidation — exactly the `st.rerun()` replacement called for in §E.5. |
| Client-state (UI) | **Zustand 4** | Lightweight (~1KB), no Provider boilerplate, slice pattern matches §D's per-tab session_state buckets. Redux Toolkit overkill for ~6 slices; Context+useReducer would force component re-renders we don't need on the scatter. |
| Routing | **React Router 6.22+** (data-router mode) | Loaders pre-fetch manifest before tab mount; URL-driven project + tab matches N2 contract. |
| Styling | **Tailwind CSS 3.4** + **CSS variables for tokens** | Utility-first scales for the dense control panels of Selector/Clustering. CSS Modules adds a build step per file with no payoff at 4 tabs. styled-components inflates bundle. d3 palette goes into CSS vars (cluster-0 … cluster-9). |
| Component primitives | **Radix UI primitives** + **shadcn/ui** copy-in components | Accessible (A2/A3/A4 in §2.6), unstyled-by-default, no theme-coupling. We OWN the components in `web/src/components/ui/` — no version-pin lock-in like MUI. |
| Charting (scatter + lasso) | **Plotly.js 2.30+** (via `react-plotly.js`) | The lasso/box brushing + customdata pattern in `_brushing.py:288-321` is Plotly-native; rewriting in Visx/Recharts blows the v1 budget. Visx-deck.gl was considered for >5k points but US-S2 caps at 5,000 (`tab_selector.py:295`) — Plotly Scattergl handles that comfortably. We accept the bundle cost (~3.2MB raw, ~900KB gzip) because it's used on 2 of 4 tabs. |
| Mosaic viewer | **OpenSeadragon 4.x** (wrapped by MV agent) | Decided in §4.99 Q-P1. We CONSUME `<MosaicCanvas>` from MV and only wire it to filtered tile manifests. |
| Image preview (Selector) | **Native `<img>` + custom pan/zoom hook** | Per §4.99 Q-U3: lightweight HTML img, NOT OpenSeadragon. Plain wheel-zoom via CSS transform; ~150 LOC. |
| Forms / inputs | **react-hook-form 7.x** | Replaces the canonical-store + widget-key rehydrate hack (§E.6). RHF's `useController` gives us controlled+uncontrolled hybrid for free; `mode: 'onBlur'` keeps the slider→repaint smooth. |
| SSE (compute progress) | **Native `EventSource`** + custom `useStepProgress` hook | The progress contract is tiny (`{pct, msg}`); a library is overkill. Fallback to long-poll if `EventSource` is unavailable (it's universally supported in our browser matrix §2.6 A1). |
| i18n | **react-i18next 14** | Standard, lazy-loadable namespaces, ICU MessageFormat. `en.json` ships day 1; `ko.json` slots in by adding the file. |
| Icons | **lucide-react** | Tree-shakeable, MIT, ~600 SVG icons; replaces Streamlit's emoji-glyphs (`▶ Run All`). |
| Notifications | **Sonner** (toast lib) | 12KB, already shadcn/ui-friendly; for compute success/failure surfaces. |
| Lint/format | **ESLint** (typescript-eslint, react-hooks) + **Prettier** | Standard. |
| Unit test | **Vitest** + **@testing-library/react** | Vite-native; replaces Jest config-pain. |
| E2E (post-v1) | **Playwright** | Already MV's choice for mosaic perf tests; same harness can plug in later. |
| API typing | **OpenAPI codegen** via `openapi-typescript` | Backend Architect produces an `openapi.json` from FastAPI; we generate `web/src/api/types.ts` from it. Hand-written types drift the moment the backend changes a field name. |

**Bundle target**: <1.5MB gzip total. Plotly is the dominant cost; lazy-import it inside Selector/Clustering tabs so Compute/Explorer don't pay for it.

---

## 2. App shell + routing

### 2.1 URL scheme (single source of truth for "current state")

```
/                                    → redirect to /projects/local
/projects/:projectId                  → redirect to /projects/:projectId/compute
/projects/:projectId/compute          → Compute tab
/projects/:projectId/selector         → Selector tab
/projects/:projectId/clustering       → Clustering tab
/projects/:projectId/explorer         → Explorer tab
```

For v1: `projectId` is hardcoded to the literal string `"local"` (single project). All API calls use `/api/v1/projects/local/...`. Multi-project lights up later by:
1. Adding `<ProjectPicker>` in the header that swaps `projectId` in the URL.
2. Backend already accepts `<project_id>` as a path param — no client refactor needed.

### 2.2 Top-level layout

```
<AppShell>
├─ <TopBar>           ← project switcher (v1: read-only "local"), version badge, language picker, manifest reload button
├─ <SidebarLeft>      ← path inputs (raw_images_dir, annotations_path, analysis_folder), pipeline status (7 chips: not_started/done/stale)
├─ <Outlet />         ← tab content (one of 4)
└─ <ToastViewport>    ← Sonner notifications
```

Per-tab "right rail" panels (replacing Streamlit sidebar drawers like `tab_selector.py:200-234`'s controls drawer) are owned by each tab via a `<RightRail>` slot the tab renders into. Sidebar drawer pattern is a Streamlit affordance for narrow viewports — in the React app we use a real two-column layout because the real estate is there.

### 2.3 Loaders (React Router `loader` functions)

| Route | Loader fetches |
|---|---|
| `/projects/:projectId` | `GET /api/v1/projects/{projectId}/manifest` |
| `/projects/:projectId/compute` | manifest + thumbnails `index.json` summary (cache_dir presence) |
| `/projects/:projectId/selector` | manifest + `02_domain_stats/stats.npz` summary (n_domains, axis ranges) |
| `/projects/:projectId/clustering` | manifest + `04_clustering/seed_groups.json` (if present) + `labels.json` |
| `/projects/:projectId/explorer` | manifest + `00_thumbnails/index.json` + `06_explorer/explorer_state.json` |

Loaders return TanStack Query-prefetched data so tab body mounts with hydrated cache.

---

## 3. State model

Six Zustand slices + TanStack Query caches. Server data is NEVER duplicated into Zustand — Zustand only holds **UI state** (filters, selections, toggles).

### 3.1 `pathsSlice` (US-X1)

```ts
interface PathsSlice {
  rawImagesDir: string;        // user-edited, sent on first compute call
  annotationsPath: string;
  analysisFolder: string;
  setPath(field: keyof PathsSlice, value: string): void;
  reloadManifest(): void;      // invalidates manifest query
}
```
- **Loads**: from manifest query on mount (`raw_images_dir`, `annotations_path` auto-fill from manifest, `analysis_folder` from URL or local-stored "last used" hint).
- **Persists**: server-side via manifest stamping (no localStorage for the canonical values; per N5 in §2.3).

### 3.2 `manifestSlice` — REPLACED by TanStack Query, not Zustand

Manifest is server-state; lives entirely in `useQuery(['manifest', projectId])` with `staleTime: 0` so the "Reload manifest" button just calls `queryClient.invalidateQueries`. UI components read it via `useManifest()` hook.

### 3.3 `selectorSlice` (US-S1, S2, S3)

```ts
interface SelectorSlice {
  filter: {
    area: [number, number];
    std_r: [number, number];
    std_g: [number, number];
    std_b: [number, number];
    sam2: [number, number];
  };
  axisX: AvailableAxis;        // "R" | "G" | "B" | "area" | "std_r" | "std_g" | "std_b" | "sam2"
  axisY: AvailableAxis;
  show3D: boolean;
  brushing: BrushingState;     // see §3.7
  focusDomainId: number | null;
  setFilter(metric, range): void;
  resetFilter(): void;
  setAxis(axis: 'X' | 'Y', value: AvailableAxis): void;
}
```

- **Replaces** the canonical-store + widget-key rehydrate pattern (§E.6) entirely. RHF's controlled inputs hold the live value; commit to Zustand on `onBlur` or debounced 200ms during drag.
- **Loads**: defaults from `_METRIC_DEFS` (port `tab_selector.py:92-98` to a TS const). Manifest's `selector.params` block hydrates the slice on mount if present (US-X3 AC).
- **Persists**: only on Commit (US-S7) — the Selector commit endpoint takes the full filter + selection.

### 3.4 `clusteringSlice` (US-CL1..CL5)

```ts
interface SeedGroup { id: string; name: string; member_ids: number[]; }

interface ClusteringSlice {
  seedGroups: SeedGroup[];
  fitScope: 'seeds' | 'all_selected';
  initialMaxMahalanobis: number;          // 0.5–6.0, default 3.0
  liveMaxMahalanobis: number;             // 0.5–8.0, post-fit gate
  perClusterThresholds: Record<number, number>;  // {0: 0.5, 1: 0.5, ...}
  axisX: AvailableAxis;
  axisY: AvailableAxis;
  brushing: BrushingState;                // independent from Selector's per Q-U4
  editingGroupId: string | null;
  // mutations
  addSeedGroup(name: string, memberIds: number[]): void;
  renameSeedGroup(id: string, name: string): void;
  removeSeedGroup(id: string): void;
  clearSeedGroups(): void;
  setThreshold(clusterId: number, value: number): void;
  resetThresholdsToDefault(): void;
}
```

- **Loads**: First-mount autoload from `04_clustering/seed_groups.json` ONLY if `seedGroups.length === 0` (preserve `_maybe_autoload_seed_groups` semantics from `tab_clustering.py:60-82`); thresholds from `labels.json["thresholds"]`.
- **Persists**: "Fit GMM" → POST clustering run; "Commit clustering" → POST apply_thresholds (which corresponds to `pipeline/clustering.py::apply_thresholds`, §E.3).

### 3.5 `explorerSlice` (US-E1..E5)

```ts
interface ExplorerSlice {
  includeLabels: Set<string>;       // cluster names
  excludeLabels: Set<string>;
  neighborFilter: {
    sizeMin: number | null;
    sizeMax: number | null;
    isolationMin: number | null;    // placeholder, S-priority
    excludeBorderClipped: boolean;  // placeholder, C-priority
  };
  selectedFlakeId: number | null;
  focusFlakeId: number | null;      // table-row click (vs tile-click)
  lodChoice: 'auto' | 0 | 1 | 2 | 3;   // 3 = raw
  viewportState: { center: [number, number]; zoom: number } | null;
  renderToggles: {
    flake_bbox: boolean;            // default true
    flake_outline: boolean;         // default false
    island_bbox: boolean;
    island_outline: boolean;
  };
}
```

- **Loads**: `06_explorer/explorer_state.json` (US-X3 AC).
- **Persists**: "Save explorer state" → POST endpoint that wraps `save_explorer_state(...)` from `pipeline/explorer.py:38`.
- **viewportState** is ephemeral (NOT persisted to disk) — it's a session affordance owned by `<MosaicCanvas>` from MV.

### 3.6 `uiSlice` (cross-cutting)

```ts
interface UiSlice {
  locale: 'en' | 'ko';
  theme: 'light';                     // dark mode out of scope (O7)
  setLocale(locale): void;
}
```

Locale persists in localStorage (per US-X4: per-browser).

### 3.7 `BrushingState` (per-tab; ports `_brushing.py:78-106`)

```ts
type BrushMode = 'replace' | 'add' | 'subtract';
type InteractionMode = 'single' | 'lasso' | 'zoom';

interface BrushingState {
  selectedIds: Set<number>;
  history: Set<number>[];            // bounded to 20 (HISTORY_MAX)
  redoStack: Set<number>[];
  mode: BrushMode;                   // active when interactionMode==='lasso'
  interactionMode: InteractionMode;
  focusId: number | null;
}
```

- One instance lives inside `selectorSlice.brushing`, another inside `clusteringSlice.brushing`. Per Q-U4 they NEVER share state.
- `pushHistory`, `undo`, `redo`, `applyLasso`, `clearSelection` are pure functions in `web/src/lib/brushing.ts` — direct port of `_brushing.py:129-184`.
- Sets serialize to arrays for Zustand devtools/persistence (transparent via custom storage adapter).

---

## 4. Per-tab component design

### 4.1 Compute tab

**Component tree**:
```
<ComputeTab>
├─ <RunAllPanel>
│   ├─ <Button>Run All</Button>
│   └─ <StepProgressList>           ← four <StepProgressBar> rows
├─ <StepCard step="thumbnails">
│   ├─ <ParamForm>                  ← quality, force, raw_ext (RHF)
│   ├─ <Button>Run</Button>
│   ├─ <StepProgressBar />
│   ├─ <ThumbnailCacheInfo />       ← cache_dir + Clear cache button
│   └─ <StepResultSummary />        ← n_images / n_skipped / n_failed
├─ <StepCard step="background">
│   ├─ <ParamForm /> + <Button> + <StepProgressBar />
│   └─ <BackgroundPreview />        ← downsampled <img> from server endpoint
├─ <StepCard step="domain_stats">
│   ├─ <PrereqGate prereq="background" />  ← greyed out until prereq=done
│   └─ <ParamForm /> + <Button> + <StepProgressBar />
└─ <StepCard step="domain_proximity">
    └─ <ParamForm /> + <Button> + <StepProgressBar />
```

**Server queries**:
- `GET /api/v1/projects/{pid}/manifest` — pipeline status chips
- `GET /api/v1/projects/{pid}/thumbnails/index` — cache_dir + counts
- `GET /api/v1/projects/{pid}/background/preview` — returns downsampled PNG (US-C3)

**Mutations** (with optimistic invalidation of `manifest` query on success):
- `POST /api/v1/projects/{pid}/compute/thumbnails` — body: params; returns SSE stream
- `POST /api/v1/projects/{pid}/compute/background`
- `POST /api/v1/projects/{pid}/compute/domain_stats`
- `POST /api/v1/projects/{pid}/compute/domain_proximity`
- `POST /api/v1/projects/{pid}/compute/run_all` — orchestrated by backend, single SSE
- `DELETE /api/v1/projects/{pid}/thumbnails/cache` — Clear cache

**Local vs server state**:
- Form params: local (RHF). Submitted on click, params NOT persisted between sessions (matches today; manifest carries last-run params via response).
- Step status, progress: server (SSE during run, query refetch after).

**Key interactions**:
- Click "Run thumbnails" → POST → opens `EventSource` → updates progress bar → on `event: done` → `queryClient.invalidateQueries(['manifest'])` → status chip flips to "done".
- Cancel button (R2 in §2.2): sends `DELETE` to step's run endpoint with the run handle (Backend Architect defines).

**Don't over-engineer**:
- No global compute queue UI (one user, one job — see §4.99 Q-P2).
- No retry buttons; user re-clicks Run.
- No param presets / saved profiles in v1.

### 4.2 Selector tab

**Component tree**:
```
<SelectorTab>
├─ <SelectorRightRail>
│   ├─ <FilterControls>             ← 5 <MetricRangeRow> (slider + 2 number inputs, RHF)
│   ├─ <FilterPresets>              ← Select All, Reset
│   ├─ <AxisPicker pane="X" /> + <AxisPicker pane="Y" />
│   ├─ <BrushingControls />         ← Single / Lasso R/A/D / Zoom + Undo/Redo/Clear
│   ├─ <Live3DToggle />
│   ├─ <LiveCounters />             ← Accepted / Rejected / Selected / Will commit
│   └─ <CommitButton />             ← mirrored in body
├─ <SelectorMain>
│   ├─ <ScatterPanel>
│   │   └─ <ScatterCanvas>          ← Plotly Scattergl + lasso/click events
│   ├─ <ImagePreviewPanel>
│   │   └─ <RawImagePreview>        ← <img> + pan/zoom + boundary toggle
│   └─ <RGBScatter3DPanel cond={show3D} />
├─ <FlakeListAccordion>
│   └─ <FlakeTable />               ← virtualized, row-click → focus
└─ <CommitButton />                 ← duplicate, body-level
```

**Server queries**:
- `GET /api/v1/projects/{pid}/domain_stats` — flake_ids, repr_rgbs, std_pcts, areas, sam2 (typed array shapes; JSON for v1, optionally arrow-ipc later).
- `GET /api/v1/projects/{pid}/annotations/preview/{domain_id}` — returns crop + mask outline metadata for `<RawImagePreview>` (replaces `_image_preview.py` server-side).

**Mutations**:
- `POST /api/v1/projects/{pid}/selector/commit` — body: full `{filter_params, lasso_ids}`; backend runs `pipeline/selector.run_selector_step`.
- `GET /api/v1/projects/{pid}/selector/export?mode={filtered|selected}` — CSV streaming.

**Local vs server**:
- Filter values, selection (`brushing.selectedIds`), axis picks, focus — all Zustand. Server only sees them on commit.
- Domain stats arrays — server (TanStack Query, `staleTime: Infinity` since they don't change without a re-Compute).

**Key interactions**:
- Slider drag → debounced 200ms → updates `selectorSlice.filter` → `<ScatterCanvas>` recolors via `useMemo` (no server roundtrip; matches §2.1 ≤500ms budget).
- Lasso event from Plotly → `applyLasso(brushing, ids)` → `<LiveCounters>` recompute.
- Single-click on a point → `setFocusDomainId(id)` → `<RawImagePreview>` queries crop endpoint.
- Row click in `<FlakeTable>` → highest-precedence focus (`_focus_domain_id` from `tab_selector.py:695-708`).
- Commit click → mutation → on success → toast + invalidate manifest.

**Don't over-engineer**:
- No undo for filter slider edits (only brushing has history per §1.3 US-S3).
- No saved filter presets beyond Select All / Reset.
- 3D scatter is display-only — no lasso events on it (US-S5 AC).

### 4.3 Clustering tab

**Component tree**:
```
<ClusteringTab>
├─ <ClusteringRightRail>
│   ├─ <SeedGroupEditor>
│   │   ├─ <SeedGroupList>          ← rows: name + count + edit/delete; edit-mode highlights members
│   │   ├─ <Button>Add from selection</Button>
│   │   ├─ <Button>Reload last fit's seeds</Button>
│   │   └─ <Button>Import clusters → seeds</Button>
│   ├─ <FitScopeRadio />            ← seeds-only / all-selected
│   ├─ <InitialMahalanobisSlider />
│   ├─ <FitGMMButton />             ← disabled when seedGroups.length < 2
│   ├─ <PerClusterThresholdPanel>   ← live posterior sliders
│   │   └─ <ClusterRow * n>         ← color swatch + slider + "K/N pass"
│   ├─ <LiveMahalanobisSlider />    ← post-fit gate
│   ├─ <BrushingControls />
│   ├─ <AxisPicker pane="X|Y" />    ← shared component with Selector
│   └─ <CommitClusteringButton />
├─ <ClusteringMain>
│   ├─ <ScatterCanvas />            ← shared with Selector; coloring per cluster
│   └─ <ClusterSizeBarChart cond={fitDone} />
└─ <FlakeListAccordion />           ← optional, for inspection
```

**Server queries**:
- `GET /api/v1/projects/{pid}/domain_stats` — same as Selector (shared cache).
- `GET /api/v1/projects/{pid}/selector/selection` — domain_ids passing the committed selector (subset to cluster on).
- `GET /api/v1/projects/{pid}/clustering/labels` — labels.json + assignments.parquet derived shape.
- `GET /api/v1/projects/{pid}/clustering/seed_groups` — for autoload.

**Mutations**:
- `POST /api/v1/projects/{pid}/clustering/fit` — body: seed_groups, fit_scope, initial_max_mahalanobis. Returns labels + assignments inline (or a job handle if N6 lights up).
- `POST /api/v1/projects/{pid}/clustering/apply_thresholds` — body: per_cluster_thresholds, max_mahalanobis. **MUST be a distinct endpoint** because it's a separate mutation surface (§E.3).
- `PUT /api/v1/projects/{pid}/clustering/seed_groups` — incremental save (debounced or on-blur) of in-flight seed group edits.

**Local vs server**:
- Seed groups: Zustand (live editing), with debounced PUT to disk so the autoload contract still works on browser-tab close.
- Thresholds + max_mahalanobis: Zustand for live preview; written via `apply_thresholds` mutation.
- GMM model artifacts: server (`gmm_model.pkl` written by backend, never sent to client).

**Key interactions**:
- Lasso → `applyLasso` → "Add as seed group" button writes to `clusteringSlice.seedGroups`.
- Edit-group dropdown change → `setEditingGroupId` → `<ScatterCanvas>` adds orange ring overlay on those member ids (parity with `tab_clustering.py:621-633`).
- Threshold slider drag → debounced 100ms (tighter than Selector because the recolor budget is 300ms, §2.1) → recoloring memo recomputes `cluster_label[i] = clusters[i].posterior >= thresholds[clusters[i]] ? clusters[i] : -1`.
- Commit → POST apply_thresholds → invalidate clustering queries → toast.

**Don't over-engineer**:
- No GMM hyperparameter tuning UI beyond fit_scope + initial mahalanobis (frozen by E.7 reproducibility contract).
- No cluster auto-naming / merging.
- DetailPanel from Explorer NOT shared into this tab — clustering is for authoring, not inspection.

### 4.4 Explorer tab — performance-critical

**Component tree**:
```
<ExplorerTab>
├─ <ExplorerRightRail>
│   ├─ <ClusterIncludeExcludePicker>  ← two stacked multiselects + conflict warning
│   ├─ <NeighborFilterPanel>
│   ├─ <RenderTogglesPanel>           ← 2x2 grid (4 toggles)
│   ├─ <LodPicker />                  ← Auto / 0 / 1 / 2 / Raw
│   └─ <SaveExplorerStateButton />
├─ <ExplorerMain>
│   ├─ <MosaicCanvas>                 ← OWNED BY MV — wraps OpenSeadragon
│   │   └─ contract:
│   │       props: tileManifest, viewport, lod, includeMask, onTileClick, onViewportChange
│   │       events: tileClick(image_id), viewportChange({center, zoom, lodActual})
│   ├─ <FlakeListPanel>
│   │   └─ <FlakeTable />              ← sortable, virtualized, 6 cols (US-E4)
│   └─ <DetailPanel>
│       ├─ <DetailIdentity />          ← flake_id, image_id, domains
│       ├─ <DetailLabels />
│       ├─ <DetailDistance />
│       └─ (Geometry + MaskStats deferred — O10)
```

**Server queries**:
- `GET /api/v1/projects/{pid}/explorer/tile_manifest` — `[{image_id, ix, iy, n_pass, n_total, has_thumbnail_lod{0,1,2}}]`. Backend joins `index.json` + clustering filters server-side; client never sees the LOD filenames directly.
- `GET /api/v1/projects/{pid}/explorer/flakes?include=...&exclude=...&size_min=...` — filtered flake table. Server-side filtering keeps the §2.1 1GB ceiling.
- `GET /api/v1/projects/{pid}/explorer/flake/{flake_id}` — DetailPanel data.
- Tile bytes: `GET /api/v1/projects/{pid}/thumbnails/{lod}/{image_id}.webp` — served by backend with appropriate caching headers; OpenSeadragon's tile-source talks to this.

**Mutations**:
- `POST /api/v1/projects/{pid}/explorer/state` — saves `explorer_state.json` + `selected_flakes.parquet` (US-E5).

**Local vs server**:
- Filter state, selection, viewport, render toggles, LOD choice — Zustand (NOT a query — would kill perf if it round-tripped).
- Tile manifest, flake list, detail data — server.
- Tile **pixels** — OpenSeadragon's internal cache (with hard memory ceiling enforced by MV's wrapper).

**Key interactions**:
- Filter toggle change → `<MosaicCanvas>` `includeMask` prop changes → MV recolors / fades non-passing tiles (parity with `tab_explorer.py:512-519`'s gray + 50% white blend, but rendered on the canvas, not numpy).
- Tile click → `onTileClick(image_id)` → first flake in tile becomes selected (Q-U2 preserved) → `<DetailPanel>` queries `flake/{id}`.
- Viewport change (debounced 100ms) → `<MosaicCanvas>` requests visible tile set at the right LOD; auto-LOD ladder lives in MV.
- "Save state" button → POST → toast.

**Don't over-engineer**:
- No bbox/outline overlay rendering — explicitly deferred (O9).
- No Geometry / MaskStats sections (O10).
- No tile-level animations or transitions (cost vs benefit).
- No client-side thumbnail recomputation — backend serves pre-built WebP only.

---

## 5. Cross-cutting concerns

### 5.1 API client

```ts
// web/src/api/client.ts
export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
```

Thin `fetch` wrapper that:
1. Prepends `${apiBaseUrl}/projects/${projectId}` to relative paths.
2. Parses error envelope `{error: {code, message, details?}}` and throws a typed `ApiError`.
3. Adds a request id header (UUIDv4) for §2.8 O2 traceability.

Dev-mode (`VITE_API_BASE_URL` unset) → Vite proxy forwards `/api/v1` to `http://localhost:8000/api/v1`. Production → environment variable points at backend host (split-host case from §4.99 Q-S1).

CORS: handled by backend (`Access-Control-Allow-Origin`); frontend never sends credentials in v1 (auth no-op per N4).

### 5.2 SSE consumption — `useStepProgress` hook

```ts
function useStepProgress(
  projectId: string,
  step: PipelineStep,
): {
  status: 'idle' | 'running' | 'done' | 'error';
  pct: number;
  message: string;
  start(params: StepParams): void;
  cancel(): void;
};
```

Hand-rolled (no `react-use-event-source` dep). Internally:
- `start()` does `POST /compute/{step}` and gets back a `{run_id}`.
- Opens `new EventSource(${apiBase}/compute/{step}/stream/${runId})`.
- Parses `{pct, msg}` JSON from each `event: progress` line.
- Closes on `event: done` or `event: error`.
- `cancel()` does `DELETE /compute/{step}/run/${runId}` (R2).
- On unmount: `eventSource.close()` (R5 — no zombie connections).

### 5.3 Error boundaries + toast

- Top-level `<ErrorBoundary>` around `<Outlet />` in `<AppShell>` catches render errors → shows fallback with "Reload" + bug-report link (manifest snapshot dumped to clipboard).
- Per-tab `<ErrorBoundary>` so a Selector crash doesn't blow away Compute.
- Sonner toasts:
  - `success`: compute done, commit done.
  - `error`: API errors caught by mutation `onError`.
  - `warning`: "Folder mid-write — try Reload manifest" (R3 conflict detection).

### 5.4 i18n

- `web/src/i18n/index.ts` initializes react-i18next with `en` namespace.
- `web/src/i18n/locales/en.json` keys grouped by tab: `compute.run_all`, `selector.filter.area_label`, `clustering.fit_button`, etc.
- Helper hook: `const t = useTranslation();` then `<button>{t('compute.run_all')}</button>`.
- Backend error messages come back with a `code` field; UI maps codes to localized strings (server doesn't know the user's locale).
- Adding `ko` later = drop `ko.json` next to `en.json` + add `ko` to the picker. Zero code changes.

### 5.5 Theme + d3 category10 palette

```css
/* web/src/styles/theme.css */
:root {
  --cluster-0: #1f77b4;
  --cluster-1: #ff7f0e;
  --cluster-2: #2ca02c;
  --cluster-3: #d62728;
  --cluster-4: #9467bd;
  --cluster-5: #8c564b;
  --cluster-6: #e377c2;
  --cluster-7: #7f7f7f;
  --cluster-8: #bcbd22;
  --cluster-9: #17becf;
  --neutral-gray: #9e9e9e;

  --accept-green: #43a047;
  --reject-amber: #fbc02d;
  --selected-gold: #ffc800;
  --selected-orange: #ff5722;
}
```

Mirror as TS const for Plotly (which can't read CSS vars in WebGL traces):

```ts
// web/src/styles/palette.ts
export const CLUSTER_PALETTE = ['#1f77b4', '#ff7f0e', /* ... 8 more */] as const;
```

Single source of truth for screenshot continuity (Q-M2). Source comments explicitly cite `tab_explorer.py:39-42` so a future palette change can be traced.

### 5.6 Feature flags

V1: none. The architecture supports it (a `featureFlags` field on the manifest response, read by `<FeatureFlagProvider>`), but no flag is wired in v1.

---

## 6. Build & dev workflow

### 6.1 Dev

```
backend:    uvicorn app:app --port 8000 --reload
frontend:   npm run dev    (Vite at :5173 with proxy to :8000/api)
```

`vite.config.ts` proxy:
```ts
server: {
  proxy: { '/api': 'http://localhost:8000' }
}
```

### 6.2 API typing

OpenAPI codegen, automated:

1. Backend exposes `GET /openapi.json` (FastAPI default).
2. `npm run gen:api` runs `openapi-typescript http://localhost:8000/openapi.json -o web/src/api/types.ts`.
3. Pre-commit hook runs `npm run gen:api` and fails commit if `types.ts` changed without being staged.

Rationale: hand-written types lie within a week. Codegen guarantees the wire shape stays in sync with FastAPI's Pydantic models.

### 6.3 Lint/format

- `eslint --max-warnings=0` blocks PR merge.
- Prettier defaults; pre-commit hook formats staged files.

### 6.4 Build

`npm run build` → `dist/` static bundle. FastAPI serves it via `StaticFiles` mount at `/` in production (DevOps decides; not our concern).

### 6.5 E2E (post-v1)

Playwright runs against a fixture analysis folder. One test per tab asserting "no console errors + key control renders". Slot for the §2.1 perf regression test (T4).

---

## 7. Migration tactics

### 7.1 File layout (`web/` is a brand-new top-level folder)

```
web/
├─ package.json
├─ vite.config.ts
├─ tsconfig.json
├─ index.html
├─ public/
└─ src/
   ├─ main.tsx
   ├─ App.tsx
   ├─ routes.tsx
   ├─ api/
   │   ├─ client.ts
   │   ├─ types.ts          ← codegen target
   │   ├─ queries.ts        ← TanStack Query hooks
   │   └─ mutations.ts
   ├─ store/
   │   ├─ paths.ts
   │   ├─ selector.ts
   │   ├─ clustering.ts
   │   ├─ explorer.ts
   │   └─ ui.ts
   ├─ lib/
   │   ├─ brushing.ts       ← port of _brushing.py (pure)
   │   └─ palette.ts
   ├─ hooks/
   │   ├─ useStepProgress.ts
   │   ├─ useManifest.ts
   │   └─ useDebounced.ts
   ├─ i18n/
   │   ├─ index.ts
   │   └─ locales/en.json
   ├─ components/
   │   ├─ ui/               ← shadcn primitives (Button, Slider, Dialog, Toast)
   │   ├─ shell/            ← AppShell, TopBar, SidebarLeft, RightRail
   │   ├─ shared/           ← ScatterCanvas, AxisPicker, BrushingControls,
   │   │                      MetricRangeRow, FlakeTable, LiveCounters
   │   ├─ compute/
   │   ├─ selector/
   │   ├─ clustering/
   │   └─ explorer/         ← MosaicCanvas wrapper lives here (MV ships internals)
   ├─ pages/
   │   ├─ ComputeTab.tsx
   │   ├─ SelectorTab.tsx
   │   ├─ ClusteringTab.tsx
   │   └─ ExplorerTab.tsx
   └─ styles/
       ├─ theme.css
       └─ globals.css
tests/
└─ web/
    ├─ unit/                 ← Vitest + RTL
    └─ e2e/                  ← Playwright (post-v1)
```

### 7.2 Porting order (recommendation)

1. **Compute** (1 wk). Smallest surface; validates SSE pipe, manifest query, error envelope. No Plotly. Lowest risk of correctness regression because outputs are unchanged on disk.
2. **Selector** (2 wk). Forces the brushing port (`_brushing.py`), establishes `<ScatterCanvas>` pattern reused by Clustering. RHF replaces the canonical-store pattern (§E.6).
3. **Clustering** (2 wk). Reuses Selector's `<ScatterCanvas>`, `<AxisPicker>`, `<BrushingControls>`, `<MetricRangeRow>`. Adds the GMM-fit + threshold-preview machinery.
4. **Explorer** (3 wk). Highest risk because of `<MosaicCanvas>` integration — MV's component must be ready by this stage. We treat it as an integration sprint.

### 7.3 What we DELETE on cutover (atomic, per Q-C1)

```
app/streamlit_app.py
src/flake_analysis/ui/__init__.py
src/flake_analysis/ui/sidebar.py
src/flake_analysis/ui/tab_compute.py
src/flake_analysis/ui/tab_selector.py
src/flake_analysis/ui/tab_clustering.py
src/flake_analysis/ui/tab_explorer.py
src/flake_analysis/ui/_brushing.py     ← logic ported to web/src/lib/brushing.ts
src/flake_analysis/ui/_image_preview.py ← logic ported to backend endpoint
```

Streamlit's `pyproject.toml` dep is removed in the same PR. Total deleted: ~5,400 lines of Streamlit-bound code.

What we KEEP unchanged (per reuse map §A): all of `core/`, `pipeline/`, `state/`, plus `cache/` (empty). 28 of 35 modules.

---

## 8. Open questions for orchestrator

### Backend Architect

- **[NEEDS-BE-1]** What is the SSE event protocol? Proposing `event: progress` + `data: {"pct": 0.42, "msg": "step 3/7"}` and `event: done` / `event: error`. Confirm or specify alternative (e.g., raw JSON over chunked transfer).
- **[NEEDS-BE-2]** Job-handle pattern for cancellation (R2): is `POST /compute/{step}` synchronous-with-SSE-on-same-connection, or two-phase (`POST` returns `{run_id}`, then `GET /compute/{step}/stream/{run_id}` for SSE)? Two-phase is friendlier for cancellation (`DELETE /compute/{step}/run/{run_id}`).
- **[NEEDS-BE-3]** Domain stats payload format: JSON-serialized arrays (simple, ~10MB for 50k domains), or Apache Arrow IPC (5x smaller, +complexity)? V1 can ship JSON; would like a non-blocking note that Arrow is on the post-v1 list if domain counts grow.
- **[NEEDS-BE-4]** Tile manifest endpoint shape: who computes `n_pass` per tile — server (preferred, smaller payload) or client (forces shipping the full assignments table)? Strongly prefer server-side aggregation.
- **[NEEDS-BE-5]** `apply_thresholds` (§E.3) concurrency guard — a per-project lock on the backend? What does the client see if a second commit fires while the first is in flight?
- **[NEEDS-BE-6]** OpenAPI emission: confirm FastAPI's `openapi.json` is exposed at `/openapi.json` in dev so codegen works (default behavior; just want to confirm it's not disabled in production).

### Machine-Vision Specialist

- **[NEEDS-MV-1]** `<MosaicCanvas>` props contract — final shape of `tileManifest` (JSON), `lod` enum values, `includeMask` representation (Set<image_id> vs bitmap)?
- **[NEEDS-MV-2]** `<MosaicCanvas>` event contract — `onTileClick(imageId, modifiers)` and `onViewportChange({center, zoom, lodActual})` — confirm signatures and debounce expectations.
- **[NEEDS-MV-3]** Memory ceiling enforcement — does `<MosaicCanvas>` accept a `maxCacheBytes` prop, or is the 1GB §2.1 ceiling enforced internally and we trust it?
- **[NEEDS-MV-4]** Tile fade/recede style for non-passing tiles (parity with `tab_explorer.py:512-519` gray + 50% white blend) — is this a prop on `<MosaicCanvas>` (e.g., `nonPassingStyle: 'fade' | 'gray'`) or do we render an overlay on top?

### DevOps

- **[NEEDS-DEVOPS-1]** Production frontend hosting: served by FastAPI's `StaticFiles` (single-process), or separate static host (CDN/nginx)? Affects `apiBaseUrl` config and CORS posture.
- **[NEEDS-DEVOPS-2]** Build artifact distribution — does the React `dist/` get checked into the repo, built in CI, or built at install time? Affects `pyproject.toml` packaging.
- **[NEEDS-DEVOPS-3]** Environment variable convention for `VITE_API_BASE_URL` in production deployments — `.env.production` baked into build, or runtime injection (less flexible with Vite without an entrypoint shim)?

---

## Top 3 risks (for the orchestrator's heat-map)

1. **Plotly bundle size** (~900KB gzip) on Selector/Clustering — mitigated by route-level code splitting; revisit if it hurts cold-load on the lab's slow network.
2. **`<MosaicCanvas>` integration timing** — Explorer is the last tab ported but the highest risk; a MV slip propagates to the v1 cutover date. Recommend MV ships a stub component returning a placeholder canvas by week 4 so we can integrate against the contract early.
3. **Brushing port correctness** — `_brushing.py:288-321`'s event extraction and Plotly customdata plumbing has subtle behaviors (selectedpoints=[], stale event purge in `_purge_pane_event_state`). Recommend porting `tests/` for `_brushing` to Vitest as part of step 7.2.2.
