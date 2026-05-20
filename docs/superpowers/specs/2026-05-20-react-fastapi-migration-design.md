# Stand-Alone Analyzer — React + FastAPI Migration (Integrated Design)

**Date:** 2026-05-20
**Stage:** 2 (design — integrated)
**Source app:** `stand-alone-analyzer` v0.2.18
**Migration target:** v1 (single-user web service, ready to scale to multi-user)

This document integrates four architect specs into one source of truth.
Component specs remain authoritative for their respective layers.

| Layer | Spec |
|---|---|
| Requirements | `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md` |
| Codebase reuse | `docs/superpowers/research/2026-05-20-codebase-reuse-map.md` |
| Backend (FastAPI) | `docs/superpowers/specs/2026-05-20-backend-design.md` |
| Frontend (React) | `docs/superpowers/specs/2026-05-20-frontend-design.md` |
| Mosaic viewer (OSD) | `docs/superpowers/specs/2026-05-20-mosaic-viewer-design.md` |
| Deployment (nginx + systemd) | `docs/superpowers/specs/2026-05-20-deployment-design.md` |

---

## 0. End-state in one sentence

A single Linux host serves a React SPA from nginx, reverse-proxies a
single-process FastAPI/uvicorn backend that imports the existing
`pipeline/`/`core/`/`state/` modules unchanged, streams compute progress
over SSE, and serves the existing LOD-pyramid WebPs (and raw fallbacks)
through nginx via `X-Accel-Redirect` to an OpenSeadragon viewer.

## 1. System diagram

```
                                              ┌────────────────────────┐
                                              │ SMB rw /mnt/analysis   │
                                              │  manifest.json         │
                                              │  00..06_*/             │
                                              └──────▲─────────────────┘
                                                     │ open/read/write
                                                     │ (slow, latency)
┌────────┐ HTTPS ┌──────────────────┐ /api/* ┌───────┴───────────┐
│Browser │──────▶│ nginx 80/443     │───────▶│ uvicorn :8000     │
│ (SPA)  │       │  • static SPA    │        │ FastAPI app       │
│  React │◀──────│  • reverse proxy │◀───────│  in-process       │
│  +OSD  │JSON   │  • X-Accel for   │ JSON   │  pipeline + core  │
└────────┘ SSE   │    /tiles/*      │  SSE   └───────┬───────────┘
                 └──┬───────────────┘                │ tile resolve
                    │ X-Accel-Redirect               │ (cache_dir from
                    │ /_tiles_internal/...           │  index.json)
                    ▼                                ▼
                 ┌────────────────────────────────────────┐
                 │ Local SSD                              │
                 │  /var/cache/stand-alone-analyzer/      │
                 │   thumbnails/<sha>/lod{0,1,2}/*.webp   │
                 └────────────────────────────────────────┘
                                                     ▲
                 ┌─────────────────────────┐         │ raw fallback
                 │ SMB ro /mnt/raw_images/ │─────────┘ (lod3 only)
                 │  ix###_iy###.png        │
                 └─────────────────────────┘
```

## 2. Layer summary (1 paragraph each)

**Backend.** FastAPI on uvicorn `--workers 1`. Routes are thin adapters
over `pipeline/` wrappers — algorithms unchanged. Per-project
`asyncio.Lock` serializes mutating steps. Compute streams progress via
SSE on the same POST connection; cancel = client disconnects →
`request.is_disconnected()` → cooperative cancel through the existing
`progress_callback`. Read endpoints expose every former direct-parquet
read site. TTL'd `cachetools` per artifact, manifest-driven invalidation.
Auth = `Depends(get_current_user)` stub returning `{id:"local"}`.

**Frontend.** Vite + React 18 + TS strict. TanStack Query for server
state, Zustand slices for UI state (per-tab, mirroring `session_state`
inventory). Plotly Scattergl for Selector/Clustering, OpenSeadragon for
Explorer mosaic, native `<img>` + pan/zoom hook for Selector preview.
RHF replaces canonical+widget rehydrate. SSE via `fetch()` parser
(POST-based, not native `EventSource`). OpenAPI codegen from FastAPI
keeps types in lockstep.

**Mosaic viewer.** OSD `LegacyTileSource` per image (Path A), no DZI in
v1. Each TiledImage advertises 4 LODs (lod0/1/2/raw); OSD picks
per-image LOD natively. CSS-filter dim for pass/fail; SVG rect for
selection highlight. Y-flip and dual-layout (v0.2.15 vs v0.2.16)
resolution happen server-side. Peak browser RAM ~50–100 MB (vs 1.5–6 GB
today).

**Deployment.** Single Linux VM. nginx serves `/usr/share/.../web/`
SPA, reverse-proxies `/api/*`, and serves `/_tiles_internal/*` (internal
location) when uvicorn returns `X-Accel-Redirect`. SMB mounts via
`cifs vers=3.0 cache=loose actimeo=30 rsize=wsize=1MiB`. systemd unit
runs `uvicorn` as user `saa`, `HOME=/var/lib/...` so the existing
`~/.cache/...` redirect resolves to `/var/lib/saa/.cache/...` (symlinked
to `/var/cache/saa/`). `KillMode=mixed` + 30 s graceful shutdown solves
the v0.2.18 Ctrl+C "ghost cache" pain (R5).

## 3. Cross-domain decisions (locked here)

These resolve open questions where two architects' assumptions
conflicted or required a system-level call.

### 3.1 Tile-serve path: **nginx via `X-Accel-Redirect`**

- BE preferred FastAPI native `FileResponse` (simpler).
- DevOps preferred nginx `X-Accel-Redirect` (perf, fewer Python hops).
- **Decision: DevOps's path wins.** Tile traffic dominates the byte
  budget (60×60 mosaic = ~32 MB lod1, ~200 MB lod2 on zoom). Keeping
  it off the uvicorn loop directly serves §2.1 NFRs (≤150 ms pan,
  ≤8 in-flight, ≤500 MB server RAM). Implementation = ~10 LoC in BE
  tile endpoint: resolve `cache_dir`, set `X-Accel-Redirect: /_tiles_internal/<sha>/lod{N}/<stem>.webp`,
  return empty body.
- BE keeps the resolver pseudocode from MV §10 — runs once per request,
  preserves dual-layout fallback. Auth stays in Python (the redirect is
  only set after the dependency chain runs).
- **Action for BE spec**: §3.4 is overridden — implement Option B
  (X-Accel-Redirect) at v1, not "consider for post-v1".

### 3.2 SSE pattern: **single-connection POST→SSE**

- BE proposed `POST /run/{step}` opens SSE on the same response;
  cancel = client closes → `is_disconnected()` set → cooperative cancel.
- FE initially proposed two-phase (POST returns `run_id`, GET stream,
  DELETE cancel).
- **Decision: BE's single-connection model wins.** Simpler, no
  cross-connection state to track. FE uses `fetch()` + manual
  event-stream parser (native `EventSource` is GET-only, doesn't apply
  here). Cancel = `AbortController.abort()` on the fetch.
- **Action for FE spec**: `useStepProgress` hook simplifies — no
  `run_id`, no DELETE; just POST + abort.

### 3.3 Tile manifest: server omits `n_pass` per tile

- FE wanted `n_pass` per tile (include/exclude UI affordance).
- MV's `/explorer/grid` returns geometry + LODs only.
- **Decision: client computes `n_pass` from the filtered flake list it
  already loads.** Server returns the join of `index.json` + clustering
  filters; client groups by `image_id`. Saves a server-side aggregation
  + payload bytes. Aligns with MV §7 Option A (first-flake lookup is
  also client-side).

### 3.4 v0.2.15 / v0.2.16 thumbnail layout

- All resolution lives in the BE tile endpoint via the resolver in
  MV §10. FE always requests `/api/v1/projects/{pid}/tiles/lod{N}/{stem}.webp`.
  Backwards compat is invisible to FE and to MV.

### 3.5 LOD3 (raw) size discovery

- BE peeks the first raw image's PIL header on first `/explorer/grid`
  request, caches by `params_hash`. No hardcoded `1920×1200`.

### 3.6 CORS posture v1

- Default deploy = same-origin nginx → no CORS.
- Split-host posture documented in DevOps §6.2; FastAPI
  `CORSMiddleware` reads `SAA_ALLOWED_ORIGINS` (CSV, never `*`).

### 3.7 Auth v1

- Stub dependency returns `User(id="local", roles=("owner",))`.
- Replaced post-v1 by `oauth2-proxy` in front of nginx; route
  signatures unchanged.

### 3.8 Cancellation latency

- Bounded by interval between two `progress_callback` calls — per
  US-C1 AC, ≤ 1 s. Pipeline core unchanged. Cancellation tokens are
  post-v1.

### 3.9 First-paint budget for 3,600-tile mosaic

- v1 strategy: render-as-they-arrive. Initial paint of viewport-visible
  tiles (~50 lod0 WebPs at fit-grid zoom) hits the ≤2 s target;
  off-screen tiles populate after.
- Spritesheet (lod0 atlas) is post-v1 if profiling shows perceived
  latency >2 s on SMB-served deployments.
- HTTP/2 between browser and nginx is enabled when TLS is turned on
  (`listen 443 ssl http2`). Until then, `imageLoaderLimit=8` is over
  HTTP/1.1's 6-per-origin cap; OSD will queue. Acceptable for v1.

## 4. URL contract (canonical)

```
# SPA (served by nginx from /usr/share/stand-alone-analyzer/web/)
GET  /                                             → index.html
GET  /assets/index-<hash>.{js,css}                 → hashed, cached forever
GET  /projects/{projectId}/{compute|selector|clustering|explorer}
                                                   → SPA history fallback

# API (proxied by nginx to uvicorn :8000)
# All API routes carry Depends(get_current_user)
GET  /api/v1/health                                → liveness + smb_reachable
GET  /api/v1/version
POST /api/v1/projects                              → CreateProjectRequest
GET  /api/v1/projects/active
GET  /api/v1/projects/{pid}                        → ProjectDetail (manifest + step_statuses)
POST /api/v1/projects/{pid}/reload
POST /api/v1/projects/validate-paths

# Compute (SSE on same POST connection)
POST /api/v1/projects/{pid}/run/thumbnails
POST /api/v1/projects/{pid}/run/background
POST /api/v1/projects/{pid}/run/domain_stats
POST /api/v1/projects/{pid}/run/selector
POST /api/v1/projects/{pid}/run/clustering/refit
POST /api/v1/projects/{pid}/run/clustering/apply_thresholds
POST /api/v1/projects/{pid}/run/domain_proximity
POST /api/v1/projects/{pid}/run/explorer/save_state    (synchronous JSON, no SSE)
GET  /api/v1/projects/{pid}/run/explorer/state

# Data reads (replace direct UI parquet/json reads)
GET  /api/v1/projects/{pid}/data/manifest
GET  /api/v1/projects/{pid}/data/thumbnails/index
GET  /api/v1/projects/{pid}/data/background/preview?downsample=4
GET  /api/v1/projects/{pid}/data/domain_stats[?cols=&limit=&offset=]
GET  /api/v1/projects/{pid}/data/selector/selection[?…]
GET  /api/v1/projects/{pid}/data/clustering/labels
GET  /api/v1/projects/{pid}/data/clustering/assignments[?…]
GET  /api/v1/projects/{pid}/data/clustering/seed_groups
GET  /api/v1/projects/{pid}/data/domain_proximity/flakes[?…]
GET  /api/v1/projects/{pid}/data/explorer/selected_flakes
GET  /api/v1/projects/{pid}/data/annotations/index
GET  /api/v1/projects/{pid}/data/annotations/{domain_id}/preview?with_contour=…

# Explorer / tile contract (drives OSD)
GET  /api/v1/projects/{pid}/explorer/grid              → grid_w, grid_h, lod_sizes, tiles[]
GET  /api/v1/projects/{pid}/tiles/lod{N}/{stem}.webp   → X-Accel-Redirect
GET  /api/v1/projects/{pid}/static/raw/{filename}      → X-Accel-Redirect (lod3 path)
```

JSON is default for all `data/*` endpoints in v1; Arrow IPC stays on
the post-v1 list (FE confirmed JSON is sufficient for current sizes).

## 5. State boundaries (one rule)

**Server data NEVER duplicates into client store.** Zustand slices
hold UI state only (filters, lasso selection, viewport, render
toggles). Server-state (manifest, stats, labels, assignments, flakes,
tile manifest) lives in TanStack Query caches. The line is bright; if
something is reachable by re-fetch, it's a query.

| Concern | Owner | Notes |
|---|---|---|
| Path inputs | Zustand `pathsSlice` | Sent on first compute call; manifest persists canonical |
| Manifest, step statuses | TanStack Query | Re-fetched on tab focus, after every compute |
| Filter ranges, brush selection | Zustand `selectorSlice`, `clusteringSlice` | Brushing per-tab independent (Q-U4) |
| Cluster thresholds, seed groups | Zustand `clusteringSlice` | Persisted via `apply_thresholds` / debounced PUT |
| Explorer LOD, viewport, render toggles | Zustand `explorerSlice` | Saved to `explorer_state.json` on demand (US-E5) |
| Tile manifest, flake table, detail | TanStack Query | Server filters; client never holds full assignments parquet |
| Tile pixels | OSD internal cache | `maxImageCacheCount=1024`; ceiling enforced inside `<MosaicCanvas>` |

## 6. Component contracts

### `<MosaicCanvas>` (MV → FE)

```ts
interface MosaicCanvasProps {
  tileManifest: GridResponse;          // /explorer/grid response
  lod: 'auto' | 0 | 1 | 2 | 3;
  includeMask: Set<number>;            // image_ids that pass current filters
  onTileClick: (imageId: number, modifiers: ModifierKeys) => void;
  onViewportChange: (v: { center: [number, number]; zoom: number; lodActual: number }) => void;
}
```

- Memory ceiling enforced internally via `imageLoaderLimit=8` and
  `maxImageCacheCount=1024`. No `maxCacheBytes` prop in v1.
- Pass/fail dim = `tiledImage.setOpacity(0.5)` + SVG white blend
  overlay.
- Selection highlight = SVG `<rect>` overlay at `(col,row,1,1)`
  viewport coords, `stroke=#FFC800 stroke-width=3`.

### `useStepProgress(projectId, step)` (FE hook)

```ts
function useStepProgress<P>(
  projectId: string,
  step: PipelineStep,
): {
  status: 'idle' | 'running' | 'done' | 'error';
  pct: number;
  message: string;
  start(params: P): void;     // POST /run/{step} + parse SSE on same response
  cancel(): void;             // AbortController.abort() → server's is_disconnected()
};
```

### Error envelope (everywhere)

```json
{
  "error": {
    "code": "snake_case",
    "message": "human readable",
    "details": {},
    "request_id": "uuid4"
  }
}
```

`code` is the i18n key the FE uses to look up a localized string.

## 7. Performance budgets (single source of truth)

| Surface | Target | Strategy |
|---|---|---|
| Selector slider → scatter recolor | ≤500 ms | `useDeferredValue` + Zustand selector memoization; no server roundtrip |
| Clustering threshold drag → recolor | ≤300 ms | Debounce 100 ms; client-side `posterior >= threshold` filter |
| Explorer pan latency | ≤150 ms | OSD requestAnimationFrame; tiles in cache redraw instantly |
| Explorer zoom-step LOD switch | ≤500 ms | nginx + local SSD cache; ≤8 parallel fetches |
| Initial 60×60 mosaic first paint | ≤2 s | Render-as-they-arrive; visible-tile budget only |
| Peak browser RAM (Explorer) | ≤1 GB | OSD `maxImageCacheCount=1024`; ~50–100 MB typical |
| Peak server RAM per session | ≤500 MB | TTL caches sized by entry; no numpy mosaics |
| Compute cancellation latency | ≤1 s | progress_callback polling interval |
| SIGTERM → process gone | ≤30 s | systemd `--timeout-graceful-shutdown 30` + `KillMode=mixed` |

## 8. Risks (top 3)

1. **`<MosaicCanvas>` integration is the schedule risk.** Explorer is
   the last tab ported but the highest risk. **Mitigation:** MV ships a
   stub `<MosaicCanvas>` returning a static placeholder by week 4 so FE
   integrates against the contract early, before OSD wiring is done.

2. **Cooperative cancellation has gaps.** A pipeline step stuck in an
   SMB syscall can't be cancelled until it returns to user space.
   **Mitigation:** documented; revisit post-v1 with proper cancellation
   tokens. The SIGTERM path (R5) still works because uvicorn closes
   the connection and the wrapper sees `OSError`.

3. **TTL-cache memory bound is loose.** Worst-case fill across all
   caches is ~600 MB; budget is 500 MB. **Mitigation:** annotations
   cache is the dominant term (`maxsize=4`). Drop to `maxsize=2` if
   monitoring shows pressure.

## 9. Parity guarantees

These are the spec-frozen invariants from the reuse map §E.7 and
requirements:

| Invariant | Source | Touched? |
|---|---|---|
| `random_state=42` (GMM) | `core/clustering/engine.py` | No |
| `repr_mode='median'` only | `pipeline/domain_stats.py` | No |
| `noise_label=-1` | `04_clustering/labels.json` schema | No |
| Manifest schema v1 | `state/manifest.py` | No |
| `tests/parity/` golden fixtures | tests/ | Must keep passing |
| d3 category10 cluster palette | `tab_explorer.py:39-42` → `web/src/styles/palette.ts` | Mirrored, not changed |
| First-flake-of-tile click | `tab_explorer.py:837-842` | Preserved (Q-U2) |
| Pass/fail dim semantics | `tab_explorer.py:512-519` | Preserved (CSS filter, same visual) |

## 10. Migration tactics

### 10.1 New code lives under

- `src/flake_analysis/api/` — FastAPI app, routes, schemas, caching,
  mutex, SSE helper, error catalog. New code only.
- `web/` — Vite + React project (top-level new folder). Source +
  tests. `npm run build` → `web/dist/`.

### 10.2 What stays untouched (per reuse map)

- All of `src/flake_analysis/core/` (algorithms).
- All of `src/flake_analysis/pipeline/` (manifest-aware wrappers).
- All of `src/flake_analysis/state/` (manifest, paths, hashing).
- `tests/parity/` — preserved by definition; wrappers don't change.

### 10.3 What gets deleted at cutover (Q-C1, atomic)

```
app/streamlit_app.py
src/flake_analysis/ui/__init__.py
src/flake_analysis/ui/sidebar.py
src/flake_analysis/ui/tab_compute.py
src/flake_analysis/ui/tab_selector.py
src/flake_analysis/ui/tab_clustering.py
src/flake_analysis/ui/tab_explorer.py
src/flake_analysis/ui/_brushing.py        (logic ported to web/src/lib/brushing.ts)
src/flake_analysis/ui/_image_preview.py   (logic ported to BE annotation/preview endpoint)
```

`streamlit` is removed from `pyproject.toml` in the same PR. ~5,400
LoC deleted.

### 10.4 Porting order (4 sprints)

| Sprint | Tab | Notes |
|---|---|---|
| 1 (1 wk) | Compute | Smallest surface; validates SSE pipe + manifest queries; no Plotly |
| 2 (2 wk) | Selector | Brushing port (`_brushing.py` → `lib/brushing.ts`); RHF replaces canonical+widget pattern; `<ScatterCanvas>` reusable |
| 3 (2 wk) | Clustering | Reuses `<ScatterCanvas>`, `<AxisPicker>`, `<BrushingControls>` from Selector; adds GMM-fit + threshold preview |
| 4 (3 wk) | Explorer | `<MosaicCanvas>` integration sprint; MV stub from week 4 → wiring → OSD pixel work |

Cutover is the atomic delete in §10.3 once Sprint 4 passes acceptance.

### 10.5 Boot order on the host

1. `mnt-analysis.automount` + `mnt-raw_images.automount` (systemd lazy
   mount).
2. `saa-backend.service` (uvicorn, depends on the mounts).
3. nginx already running (system service, independent).

## 11. Spec-self-review (placeholders / contradictions)

- ✅ All architects' specs cross-reference cleanly.
- ✅ Conflicts in §3 are explicitly resolved with the action item per
  affected spec.
- ✅ No "TBD" or "TODO" sections.
- ✅ Spec-frozen constants (§9) match all 4 architect specs.
- ⚠️ One known gap: bbox/outline overlays in Explorer (US-X-deferred /
  O9). Plug-in point documented in MV §6.3; render deferred. Tracked
  as v1.5.

## 12. Open questions left for implementation time

These don't block design approval; they're items the implementer will
encounter and the architects' specs already give the answer:

- **OSD version pin.** OSD 4.x at v1 (FE-2 in MV doc).
- **`raw_ext` per-project.** Single extension across the project (BE-4
  in MV doc, matches existing `index.json["params"]["raw_ext"]`
  contract).
- **Build artifact distribution.** CI builds `web/dist/`, deploy
  copies to `/usr/share/stand-alone-analyzer/web/`. No version-controlled
  build output.
- **`SAA_CACHE_DIR` env var.** Optional override of `HOME`-based cache
  path; either env var or systemd-set `HOME=/var/lib/saa` works (DevOps
  §4.1). Pick one at implementation time.

## 13. Done when

- Compute, Selector, Clustering, Explorer tabs all renderable in React.
- All 7 manifest steps runnable end-to-end via the new API; SSE
  progress observed; manifest stamped on success, untouched on
  failure (R1).
- 60×60 mosaic loads in <2 s, pans at <150 ms, peaks <100 MB browser
  RAM.
- `Ctrl+C` (or `systemctl stop`) ends the process within 30 s with no
  ghost children. (R5 fixed.)
- `tests/parity/` still green.
- Streamlit code deleted, `streamlit` dep removed.
