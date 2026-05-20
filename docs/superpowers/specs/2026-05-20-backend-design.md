# Backend Design — React + FastAPI Migration

**Date:** 2026-05-20
**Stage:** 2 (design)
**Source app:** `stand-alone-analyzer` v0.2.18
**Companion docs:**
- Requirements: `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md`
- Reuse map: `docs/superpowers/research/2026-05-20-codebase-reuse-map.md`

This document specifies the FastAPI backend only. Frontend, tile-serving
algorithms, and infra topology are owned by other architects (see §8).

---

## 0. Design constraints recap

From requirements §4.99 + §2:

- Single-process Python. Pipeline wrappers (`src/flake_analysis/pipeline/*.py`)
  imported in-process. No compute service split.
- v1 single-user. URL prefix is `/api/v1/projects/{project_id}/...`. v1
  resolves `project_id` to a single active analysis_folder; the URL
  schema does **not** assume single-user.
- `analysis_folder/` and `raw_images/` on **SMB**. Backend tolerates
  slow IO. Process-local caches mandatory; cross-process cache is
  post-v1.
- Synchronous compute is acceptable. Each compute call streams progress
  via **SSE**. The wrappers' `progress_callback(pct, msg)` contract
  (`src/flake_analysis/core/_compat.py:32`) maps directly to SSE events.
- Atomic cutover with Streamlit. No parallel-run.
- Manifest schema **frozen at v1**. Backwards-compat with v0.2.15
  thumbnail layout (reuse map §C.4).
- Frontend may be on a different host. **CORS** required.
- **Auth hook** present but pass-through in v1. `Depends(get_current_user)`
  returns a stub user.

---

## 1. API surface

### 1.0 Conventions

- Base path: `/api/v1/projects/{project_id}/...` for project-scoped
  resources. v1 always uses `project_id="default"` (frontend resolves
  via `GET /api/v1/projects/active`).
- All endpoints depend on `get_current_user` (§4). v1 returns a stub.
- All endpoints take/return JSON unless noted (SSE / static).
- Path validation, manifest stamping, and `params_hash` computation
  happen in the wrappers — the API layer is a thin adapter, not a
  re-implementation.
- All paths in request bodies are **server-side absolute paths**. The
  client never executes a path it typed; the user types it, the
  backend validates it, the response echoes the resolved canonical
  form. (Requirements N8.)

### 1.1 Project lifecycle

| Method | Path | Body / Query | Response | Streamlit equivalent |
|---|---|---|---|---|
| POST | `/api/v1/projects` | `CreateProjectRequest` | `ProjectHandle` | `ui/sidebar.py:44-99` |
| GET | `/api/v1/projects/active` | — | `ProjectHandle` (404 if none) | `ui/sidebar.py:129-134` |
| GET | `/api/v1/projects/{pid}` | — | `ProjectDetail` (manifest + step statuses) | `ui/sidebar.py:107-128` (manifest panel) |
| POST | `/api/v1/projects/{pid}/reload` | — | `ProjectDetail` | `ui/sidebar.py:129-134` (reload manifest) |
| POST | `/api/v1/projects/validate-paths` | `ValidatePathsRequest` | `ValidatePathsResponse` | path validation in `ui/sidebar.py` |

#### Schemas

```python
class CreateProjectRequest(BaseModel):
    analysis_folder: str    # absolute path on backend host
    raw_images_dir: str | None = None    # auto-filled from manifest if None
    annotations_path: str | None = None  # idem

class ProjectHandle(BaseModel):
    project_id: str       # v1: always "default" — opaque token v2+
    analysis_folder: str
    raw_images_dir: str | None
    annotations_path: str | None

class ProjectDetail(ProjectHandle):
    manifest: ManifestModel       # mirrors src/flake_analysis/state/manifest.py:Manifest
    step_statuses: dict[str, str] # step_name -> "not_started"|"done"|"stale"

class ValidatePathsRequest(BaseModel):
    analysis_folder: str | None = None
    raw_images_dir: str | None = None
    annotations_path: str | None = None

class ValidatePathsResponse(BaseModel):
    analysis_folder: PathStatus | None
    raw_images_dir: PathStatus | None
    annotations_path: PathStatus | None

class PathStatus(BaseModel):
    exists: bool
    is_dir: bool
    is_file: bool
    readable: bool
    writable: bool          # only meaningful for analysis_folder
    canonical: str          # resolved absolute path (symlinks followed)
```

#### Errors

- `400 invalid_path` — `details: {field, reason}` (e.g. analysis_folder is a file).
- `404 project_not_found` — for `{pid}` paths when project list is empty post-v1.
- `409 manifest_corrupt` — JSON parse failure (R4 says treat as not-started; this returns the structured warning rather than masking).

### 1.2 Compute (pipeline run) — SSE

Pattern: `POST` opens an SSE stream. The request body carries step
parameters; the stream emits `{type: progress|done|error}` events; on
`done` or `error` the stream closes. See §2 for event shape +
cancellation semantics.

| Method | Path | Body | SSE result | Streamlit equivalent |
|---|---|---|---|---|
| POST | `/api/v1/projects/{pid}/run/thumbnails` | `ThumbnailsParams` | `RunResult[ThumbnailsSummary]` | `ui/tab_compute.py:170-265` → `pipeline/thumbnails.py:31-88` |
| POST | `/api/v1/projects/{pid}/run/background` | `BackgroundParams` | `RunResult[BackgroundSummary]` | `ui/tab_compute.py:268-335` → `pipeline/background.py:26-86` |
| POST | `/api/v1/projects/{pid}/run/domain_stats` | `DomainStatsParams` | `RunResult[DomainStatsSummary]` | `ui/tab_compute.py:290-340` → `pipeline/domain_stats.py:21-83` |
| POST | `/api/v1/projects/{pid}/run/selector` | `SelectorParams` | `RunResult[SelectorSummary]` | `ui/tab_selector.py:711-789` → `pipeline/selector.py:29-106` |
| POST | `/api/v1/projects/{pid}/run/clustering/refit` | `ClusteringRefitParams` | `RunResult[ClusteringSummary]` | `ui/tab_clustering.py:949-1000` → `pipeline/clustering.py:53-180` |
| POST | `/api/v1/projects/{pid}/run/clustering/apply_thresholds` | `ApplyThresholdsParams` | `RunResult[ApplyThresholdsSummary]` | `ui/tab_clustering.py:869-906` → `pipeline/clustering.py:183-302` |
| POST | `/api/v1/projects/{pid}/run/domain_proximity` | `DomainProximityParams` | `RunResult[DomainProximitySummary]` | `ui/tab_compute.py:342-415` → `pipeline/domain_proximity.py:24-94` |
| POST | `/api/v1/projects/{pid}/run/explorer/save_state` | `SaveExplorerStateParams` | (synchronous JSON, no SSE) | `pipeline/explorer.py:38-125` |
| GET | `/api/v1/projects/{pid}/run/explorer/state` | — | `ExplorerState \| null` | `pipeline/explorer.py:128-133` |

**Why two clustering endpoints** (reuse map §E.3): `apply_thresholds`
rewrites `assignments.parquet` directly, bypassing `core/`. It is a
fast path that must not be conflated with refit. Each must own its own
concurrency guard.

**Why explorer state is not SSE**: it is a JSON write (~100 ms even on
SMB); progress streaming is overkill. Same payload shape as the rest
for consistency, but a synchronous `POST` returning the result.

#### Schema sketches (illustrative, not exhaustive)

```python
class ThumbnailsParams(BaseModel):
    raw_ext: str = ".png"
    quality: int = 80
    force_recompute: bool = False

class BackgroundParams(BaseModel):
    seed: int = 0
    max_images: int = 100
    gaussian_sigma: float = 10.0
    method: Literal["median", "mean"] = "median"

class DomainStatsParams(BaseModel):
    repr_mode: Literal["median"] = "median"  # v1 frozen — reuse map §E.7
    raw_ext: str = ".png"

class SelectorParams(BaseModel):
    area_min: float | None = None
    area_max: float | None = None
    std_r_min: float | None = None
    std_r_max: float | None = None
    # ... mirrors pipeline/selector.py:29-43

class ClusteringRefitParams(BaseModel):
    seed_groups: list[SeedGroup]      # [{name, domain_ids}]
    feature_cols: list[str] = ["mean_r", "mean_g", "mean_b"]
    covariance_type: Literal["full", "tied", "diag", "spherical"] = "full"
    rgb_threshold: float = 0.50
    fit_scope: Literal["seeds", "all_selected"] = "seeds"
    max_mahalanobis: float = 3.0

class ApplyThresholdsParams(BaseModel):
    cluster_thresholds: dict[int, float]
    max_mahalanobis: float | None = None

class DomainProximityParams(BaseModel):
    r_max_px: float = 200.0
    min_area_px: int = 10
    max_area_px: int | None = None
    d_touch_px: float = 2.0
    pixel_size_um: float = 0.5
    link_distance_um: float = 5.0
    workers: int = 4

class SaveExplorerStateParams(BaseModel):
    include_labels: list[str]
    exclude_labels: list[str]
    neighbor_filter: dict        # passthrough to pipeline/explorer.py
    selected_flake_ids: list[int] | None = None
```

#### Errors (compute family)

All compute errors arrive as a terminal SSE `error` event with the
structured body in §6. HTTP status remains `200` once the SSE
connection is established (this is the SSE convention; the `error`
event carries the failure code).

Pre-stream HTTP errors:
- `400 params_invalid` — pydantic validation failure (returned before SSE opens).
- `409 prerequisite_missing` — wrapper raises `RuntimeError("X step not completed")` (e.g. `pipeline/domain_stats.py:38`).
- `423 project_busy` — concurrency mutex held (§3).

### 1.3 Data reads (replace direct UI parquet/json reads)

Every direct `Path(analysis_folder) / "..."` read in the UI (reuse map
§E.1) becomes an endpoint. These are read-only, cached (§5), and do
not touch the manifest.

| Method | Path | Query | Response | Streamlit equivalent |
|---|---|---|---|---|
| GET | `/api/v1/projects/{pid}/data/manifest` | — | `ManifestModel` | `state/manifest.py:38-49` (called from sidebar) |
| GET | `/api/v1/projects/{pid}/data/thumbnails/index` | — | thumbnails `index.json` payload | `ui/tab_compute.py:27-35` (`_read_cache_dir_from_index`) |
| GET | `/api/v1/projects/{pid}/data/background/preview` | `downsample: int = 4` | PNG `image/png` | `ui/tab_compute.py:266-287` |
| GET | `/api/v1/projects/{pid}/data/domain_stats` | `cols?: list[str]`, `limit?: int`, `offset?: int` | Apache Arrow IPC stream OR JSON rows | `tab_selector.py:78-92` |
| GET | `/api/v1/projects/{pid}/data/selector/selection` | `cols?`, `limit?`, `offset?` | Arrow IPC / JSON | `tab_selector.py:170-200` |
| GET | `/api/v1/projects/{pid}/data/clustering/labels` | — | `LabelsJson` | `tab_explorer.py:80-110` |
| GET | `/api/v1/projects/{pid}/data/clustering/assignments` | `cols?`, `limit?`, `offset?` | Arrow IPC / JSON | `tab_explorer.py:80-110` |
| GET | `/api/v1/projects/{pid}/data/clustering/seed_groups` | — | `list[SeedGroup]` | `tab_clustering.py:60-82` |
| GET | `/api/v1/projects/{pid}/data/domain_proximity/flakes` | `cols?`, `limit?`, `offset?` | Arrow IPC / JSON | `tab_explorer.py:93-110` |
| GET | `/api/v1/projects/{pid}/data/domain_proximity/distances` | `cols?`, `limit?`, `offset?` | Arrow IPC / JSON | (post-v1; provided for completeness) |
| GET | `/api/v1/projects/{pid}/data/explorer/selected_flakes` | — | `list[int]` (404 if absent) | `pipeline/explorer.py:96-101` |
| GET | `/api/v1/projects/{pid}/data/annotations/index` | — | flat domain → bbox/mask index | `_image_preview.py:482` (`_cached_load_annotations`) |
| GET | `/api/v1/projects/{pid}/data/annotations/{domain_id}/preview` | `with_contour: bool = false` | PNG | `_image_preview.py:200-360` (raw image + contour) |

**Why Arrow IPC**: parquet payloads are 10⁴–10⁵ rows of typed columns.
Arrow IPC over HTTP halves bytes vs JSON and lets the frontend deserialize
into typed columnar buffers. Negotiate via `Accept: application/vnd.apache.arrow.stream`
vs `application/json`. Both shapes returned identically by the same
endpoint.

#### Errors (data reads)

- `404 artifact_missing` — file not on disk (step not yet run). `details: {step, expected_path}`.
- `409 manifest_mismatch` — file present but `manifest.steps[step].completed_at is None`. Means a previous run wrote files without finishing the manifest stamp. Surface as warning in UI.
- `416 range_invalid` — `offset/limit` out of bounds.

### 1.4 Static — tile and raw-image serving (delegated to MV)

Only the **URL contract** is defined here. Backend storage and DZI vs
raw decisions are owned by the Machine-Vision Specialist.

| Pattern | Description |
|---|---|
| `GET /api/v1/projects/{pid}/static/thumbnails/lod{N}/{stem}.webp` | One thumbnail. Backend resolves via `00_thumbnails/index.json` (honors `cache_dir` redirect — reuse map §C.5). |
| `GET /api/v1/projects/{pid}/static/raw/{filename}` | One raw image. Backend resolves to `manifest.raw_images_dir`. |

Backend contract:
1. URLs **never** leak a filesystem path. The frontend constructs URLs
   from `(project_id, lod, stem)` triples it gets from data endpoints.
2. Backend rejects path traversal (`..`, absolute paths) at the route
   level.
3. Cache headers: `Cache-Control: private, max-age=3600`,
   `ETag: "<sha256(file_path + mtime)[:16]>"`. LOD WebPs are immutable
   for a given `params_hash`; raws change rarely.
4. Tile serving must release file handles after the response — no
   long-lived `mmap`. (Memory budget §2.1: ≤ 500 MB server per session.)

`[NEEDS-MV]` Whether v1 needs DZI for the >60×60 case (post-v1
according to Q-P1) or whether `raw=1` query parameter triggers
on-the-fly downsample. See §8.

### 1.5 Health / version

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/api/v1/health` | `{status: "ok", version, git_commit}` | Mirrors startup banner — reqs O3. No auth. |
| GET | `/api/v1/version` | `{flake_core_version, api_version: "v1"}` | No auth. |

---

## 2. Streaming progress (SSE)

### 2.1 Transport choice — why SSE over WebSocket

- One-way (server → client) progress fits SSE exactly.
- HTTP/1.1, plain `text/event-stream`. No protocol upgrade. CORS works
  with the same `Access-Control-Allow-Origin` header set globally.
- Native `EventSource` browser API — frontend doesn't need a WS
  library.
- Auto-reconnect on transient drops. (We don't *need* it — compute
  runs are idempotent in v1 — but it's free.)

### 2.2 URL pattern

```
POST /api/v1/projects/{pid}/run/{step}
  Content-Type: application/json
  Accept: text/event-stream
  Body: <StepParams>
  Response: 200 text/event-stream
```

The `POST` body carries params; the response is the SSE stream. Some
SSE clients prefer GET; we tolerate that mismatch because params are
not URL-safe (e.g. seed_groups can be 10⁵ ids). Native `EventSource`
doesn't support POST, so the frontend uses `fetch()` + a manual
event-stream parser. `[NEEDS-FE]` confirm acceptable.

### 2.3 Event shape

Each SSE message is a JSON object on a single `data:` line.

```
event: progress
data: {"type":"progress","pct":0.42,"msg":"thumbnails lod1: 380/1024"}

event: progress
data: {"type":"progress","pct":0.43,"msg":"..."}

event: done
data: {"type":"done","result":{...step summary...}}

event: error
data: {"type":"error","detail":{"code":"prerequisite_missing","message":"...","details":{...}}}
```

- `progress.pct ∈ [0, 1]` exactly mirrors `_compat.ProgressCallback`.
- `done.result` is the dict returned from the wrapper (e.g.
  `pipeline/thumbnails.py:77-88`). Defined per-step in §1.2.
- `error.detail` matches the §6 error shape.
- Stream **closes** after `done` or `error`. No keepalive past
  termination.
- Server-side keepalive: send a `: heartbeat` comment line every 15 s
  while compute is running, to keep proxies from idle-timing-out.

### 2.4 Cancellation

Pipeline core functions accept `progress_callback` but **not** a
cancellation token (`core/_compat.py:77-79` is a no-op stub).
Retrofitting cancellation into the core is out of scope for this
migration.

**v1 strategy: cooperative cancellation via the progress callback.**

1. Backend stores a `threading.Event` keyed by `(project_id, step)`
   when the compute starts.
2. The wrapper-level `progress_callback` is replaced by an
   instrumented one:
   ```
   def cb(pct, msg):
       if cancel_event.is_set():
           raise CancelledError()
       sse_queue.put(...)
   ```
3. When the SSE client disconnects (FastAPI exposes this via
   `Request.is_disconnected()` polled in the SSE generator), we set
   `cancel_event`.
4. Next time a core function calls back into `progress_callback`, it
   raises. The pipeline wrapper does **not** catch — manifest stays
   unstamped (R1). The exception propagates out of the executor task;
   the SSE generator emits `{type: "error", detail: {code: "cancelled"}}`
   then closes.
5. Granularity: cancellation latency = time between two
   `progress_callback` calls. Per requirements §1.2 US-C1 AC, that's
   ≤ 1 s.

Limitations:
- A step that doesn't call `progress_callback` (e.g. a stuck IO
  syscall on SMB) is **not** cancellable in v1. Documented; revisit
  post-v1 with proper cancellation tokens.
- This is ugly, but it's honest.

### 2.5 Backpressure

`SSE_queue` is a `asyncio.Queue` of bounded size (e.g. 64). The
synchronous `progress_callback` runs on a worker thread (§3.1) and
writes to the queue via `loop.call_soon_threadsafe`. If the queue is
full, drop oldest progress events (keep monotonic latest) — a stuck
client must never stall the compute.

---

## 3. Concurrency & safety

### 3.1 Thread model

FastAPI runs on `uvicorn` (single process). Compute steps are
CPU/IO-bound synchronous Python. Strategy:

- API routes are `async def`.
- Compute steps run on the default `asyncio` thread-pool via
  `loop.run_in_executor(None, partial(run_step_step, ...))`. One
  compute step per worker thread.
- `progress_callback` is invoked on the worker thread. It posts events
  to an `asyncio.Queue` via `loop.call_soon_threadsafe(queue.put_nowait, evt)`.
- The SSE generator (`async def`) drains the queue.

### 3.2 Per-project compute mutex

Only one compute step per project at a time. (US-C2 "Run All" is a
client-driven sequence, not parallel.)

```
class ProjectMutexes:
    _locks: dict[str, asyncio.Lock]    # keyed by project_id
```

- Acquire with `async with` before kicking off `run_in_executor`.
- Releasing happens on completion **or** cancellation (use `try/finally`).
- If acquisition would block, return HTTP `423 project_busy` immediately
  rather than queueing. (v1 single-user: this should not happen unless
  the user double-clicks.)
- `apply_thresholds` shares the same mutex as `clustering/refit` —
  they both touch `assignments.parquet`.
- Read-only data endpoints (§1.3) do **not** acquire the mutex. They
  may observe a half-written parquet during compute.

### 3.3 File locking on SMB

POSIX advisory locks (`fcntl.flock`) are unreliable on SMB / CIFS
mounts — server-side enforcement varies, and the Linux kernel
silently drops in some configurations. **We do not rely on OS file
locks.**

Strategy for SMB-safe writes:
1. **Single-writer assumption.** The per-project asyncio mutex (§3.2)
   ensures one writer per project per backend process.
2. **Cross-process writers** (e.g. someone runs the CLI while the API
   is up): post-v1 problem (R3 says last-writer-wins is acceptable).
   Document the risk; don't engineer around it in v1.
3. **Atomic writes via `.tmp` + `os.replace`**: already done in
   `state/manifest.py:55-59`. Apply same pattern to:
   - `apply_thresholds` parquet rewrite (`pipeline/clustering.py:266`)
     — write to `assignments.parquet.tmp` then replace.
   - Explorer state JSON (`pipeline/explorer.py:94`) — same pattern.
4. **mtime-based conflict detection (post-v1)**: read manifest's
   `completed_at` before write, re-check after acquiring lock; abort
   if changed. Skip in v1 because R3 says we don't need merge.

`[NEEDS-DEVOPS]` Confirm SMB mount is mounted with `noserverino` and
`actimeo=0` or similar settings that make atomic-replace semantics
work. CIFS clients differ.

### 3.4 Static file serving — FastAPI vs nginx

| Aspect | FastAPI native (`StaticFiles` / `FileResponse`) | nginx in front |
|---|---|---|
| Latency | Adds ~1 ms Python overhead per file | Direct disk → socket |
| Memory | Small; FastAPI streams via aiofiles | Smaller; nginx zero-copy |
| URL resolution | Trivial — Python looks up `index.json["cache_dir"]` per request | Nginx must replicate the cache_dir lookup OR FastAPI rewrites a 302 |
| Range requests | Supported | Supported |
| Auth | `Depends(get_current_user)` runs first | nginx `auth_request` to FastAPI — extra hop |
| Configuration | Zero infra dependency | Devops nginx config + healthcheck |

**Recommendation: FastAPI native for v1.** Reasons:
1. Tile URL resolution is non-trivial — the WebP path depends on
   `index.json["cache_dir"]` (could be `~/.cache/...`) and the
   v0.2.15-vs-v0.2.16 layout (reuse map §C.4). Pushing this into
   nginx means duplicating logic.
2. Memory budget is 500 MB / session (reqs §2.1). Even at peak
   tile fetch rate (≤ 8 in flight) FastAPI's `aiofiles`-backed
   `FileResponse` is well under that.
3. Auth applies cleanly (post-v1).
4. v1 traffic is one user. Nginx optimization is premature.

If post-v1 perf testing shows tile serving as the bottleneck, swap to
nginx with `X-Accel-Redirect` (FastAPI returns the canonical path,
nginx serves the bytes). The route signatures don't change.

`[NEEDS-DEVOPS]` Confirm devops topology will pass the same
`Cache-Control` and CORS headers FastAPI emits.

---

## 4. Auth hook v1 stub

### 4.1 Dependency

```python
# api/auth.py
@dataclass(frozen=True)
class User:
    id: str
    roles: tuple[str, ...]

async def get_current_user() -> User:
    # v1 stub. Post-v1: parse Authorization header, validate JWT/SSO,
    # raise 401 on failure.
    return User(id="local", roles=("owner",))
```

Every route uses `user: User = Depends(get_current_user)`. The route
body may inspect `user.roles` for authorization checks (none in v1).

### 4.2 Future swap path

When SSO is added:
1. Replace `get_current_user` body with token validation. Route
   signatures unchanged.
2. Add `Depends(require_role("owner"))` to mutating endpoints. v1 stub
   already returns the `owner` role, so existing routes keep working.
3. Auth middleware (e.g. starlette `AuthenticationMiddleware`) is
   optional — the dependency injection path covers both per-route and
   per-request auth.

### 4.3 Logging

User id propagates to log records via a context-variable-backed
logging filter (`api/logging_ctx.py`). Together with the per-request
request_id (§6), this satisfies reqs N7 / O2.

---

## 5. Caching layer

### 5.1 Targets

Replace these `@st.cache_data` sites (reuse map §E.4):
1. `tab_explorer.py:453` — `_build_mosaic_array(...)`. **Drops out**
   in the new architecture: OpenSeadragon never builds a numpy mosaic.
   No backend cache needed.
2. `_image_preview.py:482` — `_cached_load_annotations(annotations_path)`.
   Backend caches the parsed `annotations.json` index per file path.

Additional caching opportunities for SMB-hosted data:
3. `manifest.json` parse (`state/manifest.py:38-49`).
4. `00_thumbnails/index.json` parse.
5. `04_clustering/labels.json` parse.
6. Tabular reads (`assignments.parquet`, `flake_assignments.parquet`,
   `selection.parquet`, `stats.npz`) — return cached `pyarrow.Table`
   or `numpy.ndarray`.

### 5.2 Implementation

`cachetools.TTLCache` per cache target. Keyed by canonical absolute
path. Sized by **entry count, not bytes**, with a per-cache documented
memory bound.

| Cache | Type | Capacity | Expected memory |
|---|---|---|---|
| `manifest_cache` | `TTLCache(maxsize=8, ttl=60)` | 8 manifests | < 1 MB |
| `thumbnails_index_cache` | `TTLCache(maxsize=8, ttl=60)` | 8 indices | < 8 MB (10⁴ entries × ~1 KB) |
| `labels_cache` | `TTLCache(maxsize=8, ttl=300)` | 8 labels.json | < 1 MB |
| `parquet_cache` | `TTLCache(maxsize=16, ttl=300)` | 16 tables | ≤ 200 MB (cap individual table at 12 MB; documented as a per-table assertion) |
| `annotations_cache` | `TTLCache(maxsize=4, ttl=600)` | 4 indices | ≤ 400 MB (annotations.json can be hundreds of MB; this is the main cache size driver) |
| `background_preview_cache` | `TTLCache(maxsize=4, ttl=300)` | 4 PNGs | < 8 MB |

**Total expected**: ~ 600 MB upper bound under worst-case fill. Within
the 500 MB / session budget (reqs §2.1) when only 1–2 caches are
warm. Document this and revisit if monitoring shows otherwise.

Caches live as module-level singletons in `api/caching.py`, guarded
by `threading.Lock` for write-safety.

### 5.3 Invalidation — manifest-driven

When a compute step finishes, the manifest's `steps[step].completed_at`
changes. The wrapper writes the manifest at the end (e.g.
`pipeline/thumbnails.py:75`). The API layer hooks the post-write
moment and invalidates dependent caches.

**Dependency map (cache → invalidating step):**

| Cache key | Invalidated by step |
|---|---|
| `manifest_cache[pid]` | every step |
| `thumbnails_index_cache[pid]` | thumbnails |
| `labels_cache[pid]` | clustering (refit + apply_thresholds) |
| `parquet_cache[<file>]` | the step that writes the file (e.g. `assignments.parquet` ← clustering, `flake_assignments.parquet` ← domain_proximity) |
| `annotations_cache[<path>]` | external mtime change only — file is user-supplied; check `file_mtime` via `state/hashing.py:15-20` and bypass cache if newer |
| `background_preview_cache[pid]` | background |

Invalidation is a one-line `cache.pop(key, None)` after a successful
wrapper return, before the SSE `done` event is emitted. A miss is fine
(idempotent).

### 5.4 What's deliberately NOT cached

- Tile WebP bytes — already small, OS page cache handles it.
- Raw images — too large; OS page cache handles it.
- `gmm_model.pkl` — only used by `apply_thresholds`, which reads its
  baseline from `labels.json` instead (`pipeline/clustering.py:243-249`).

---

## 6. Error contract

### 6.1 Wire shape

Every error (HTTP 4xx/5xx body, terminal SSE `error` event) is:

```json
{
  "error": {
    "code": "snake_case_machine_readable",
    "message": "Human-readable English (i18n key for frontend lookup)",
    "details": { "field": "...", "expected": "...", "got": "..." },
    "request_id": "uuid4"
  }
}
```

- `code` is the contract surface for frontend error handling. Stable.
- `message` is for log/dev surface. Frontend should use `code` to
  pick a localized string from the i18n table (reqs L1).
- `details` is freeform but per-code shape is documented in code as
  `TypedDict` per error.
- `request_id` propagates from the request context (reqs N7).

### 6.2 Code catalog

Common codes mapped to the situations that produce them:

| Code | Wrapper exception / situation | HTTP | Notes |
|---|---|---|---|
| `params_invalid` | pydantic validation failure | 400 | `details.errors` = pydantic error list |
| `path_invalid` | `ValidatePathsRequest` failure | 400 | `details.field` = which path |
| `prerequisite_missing` | `RuntimeError("X step not completed")` (e.g. `pipeline/domain_stats.py:38`) | 409 | `details.step` |
| `artifact_missing` | `FileNotFoundError`, `RuntimeError("X missing at ...")` (e.g. `pipeline/clustering.py:113-115`) | 404 | `details.expected_path` |
| `manifest_corrupt` | `json.JSONDecodeError` reading `manifest.json` | 409 | R4 says treat as not-started, but for explicit reload calls we surface this |
| `manifest_mismatch` | step's file exists but `completed_at is None` | 409 | suggests previous crash |
| `project_busy` | per-project mutex held | 423 | `details.holder` = step name |
| `cancelled` | client disconnect → `CancelledError` | (SSE only) | terminal SSE `error` event |
| `pipeline_failed` | uncaught exception inside `core_run_*` | 500 (or SSE error) | `details.exc_type, exc_msg`; full traceback to logs only, never to client |
| `internal_error` | unexpected | 500 | catch-all; alerts on |

### 6.3 Logging

Per requirements O1–O3, §2.8:

- stdlib `logging`, configured at startup with `logging.config.dictConfig`.
- JSON-line formatter on stdout (one record = one line). Fields:
  `ts, level, logger, msg, request_id, project_id, user_id, exc_info?`.
- Compute steps already log via stdlib (`core/_compat.py:20`).
- Startup banner (version + git commit + active SMB paths) emitted
  once at `lifespan` startup — mirrors `app/streamlit_app.py:7-37`.
- A FastAPI middleware injects `request_id` (uuid4) into the
  context-var read by the formatter.

---

## 7. Migration path

### 7.1 New code layout

All new code under `src/flake_analysis/api/`. Streamlit code at
`app/streamlit_app.py` and `src/flake_analysis/ui/` is **deleted at
cutover** (Q-C1).

```
src/flake_analysis/api/
    __init__.py
    main.py                # FastAPI app factory, lifespan, CORS
    settings.py            # pydantic Settings — reads env vars
    auth.py                # get_current_user stub (§4)
    logging_ctx.py         # request_id + user_id contextvars + JSON formatter
    errors.py              # ApiError exception + handler; code catalog (§6)
    deps.py                # shared dependencies (project resolution, mutex)
    project_resolver.py    # project_id → analysis_folder lookup; v1 always "default"
    mutex.py               # per-project asyncio.Lock registry (§3.2)
    sse.py                 # SSE response helper, queue-based streaming
    caching.py             # cachetools instances + invalidation hooks (§5)
    schemas/
        projects.py
        compute.py         # *Params + *Summary models
        data.py            # row models, label models
    routes/
        projects.py        # §1.1
        compute.py         # §1.2
        data.py            # §1.3
        static.py          # §1.4
        health.py          # §1.5
app/
    api_main.py            # uvicorn entry point: `uvicorn flake_analysis.api.main:app`
                           # replaces app/streamlit_app.py at cutover
```

### 7.2 Boot sequence (lifespan)

`@asynccontextmanager async def lifespan(app)`:

1. **Validate static dirs**: confirm `~/.cache/stand-alone-analyzer/`
   is writable (or skip if env says local cache disabled).
2. **Configure logging** (JSON formatter, stdout).
3. **Emit startup banner**: version, git commit, configured CORS
   origins, configured `analysis_folder` default. (Reqs O3.)
4. **Warm caches?** No — caches are TTL'd and lazy. Cold start is
   acceptable (single user; warm-up is wasted work for an unread
   project).
5. **Resolve active project**: read `STAND_ALONE_DEFAULT_ANALYSIS_FOLDER`
   from env (optional) and verify it exists. If absent, no active
   project — frontend prompts user via `POST /api/v1/projects`.
6. **Initialize per-project mutex registry** (empty).
7. Yield.
8. On shutdown: drain SSE queues, cancel in-flight compute (set all
   cancel events), wait up to 10 s for orderly cancellation, then exit.

### 7.3 Settings (`api/settings.py`)

Driven entirely by env vars. Required for SMB tolerance + CORS
configurability.

| Var | Default | Purpose |
|---|---|---|
| `STAND_ALONE_API_HOST` | `127.0.0.1` | uvicorn bind |
| `STAND_ALONE_API_PORT` | `8000` | uvicorn bind |
| `STAND_ALONE_CORS_ORIGINS` | empty | comma-separated allowed origins |
| `STAND_ALONE_DEFAULT_ANALYSIS_FOLDER` | none | preselected project |
| `STAND_ALONE_THUMB_LOCAL_CACHE` | unset | already honored by `core/pipeline/thumbnails.py:104-116` — surface in API logs only |
| `STAND_ALONE_LOG_LEVEL` | `INFO` | |

`[NEEDS-DEVOPS]` Confirm `STAND_ALONE_CORS_ORIGINS` is set per
deployment; defaults are empty (most-restrictive).

### 7.4 What the API layer does NOT touch

The migration is **additive only** in these subtrees:

- `src/flake_analysis/core/**` — algorithms unchanged. Reuse map class
  REUSE.
- `src/flake_analysis/pipeline/**` — wrappers unchanged. The API
  layer imports them as-is. The progress_callback contract is
  exactly what we need.
- `src/flake_analysis/state/**` — manifest, paths, hashing all unchanged.
  Backwards-compat with v0.2.18 manifests.
- `tests/parity/**` — must keep passing. Wrappers are unchanged so
  parity is preserved; the API layer wraps them without changing
  semantics.

The **only** changes outside `api/` are:
- Delete `src/flake_analysis/ui/` and `app/streamlit_app.py` at
  cutover.
- Add the missing `run_thumbnails` export to
  `src/flake_analysis/core/pipeline/__init__.py` (reuse map §E.8).
  Trivial fix on first touch.

---

## 8. Open questions for the orchestrator

### `[NEEDS-FE]`
1. **SSE-over-POST.** Native `EventSource` is GET-only. Plan is for the
   frontend to implement a `fetch()`-based SSE parser. Confirm acceptable
   vs. switching to `GET` with a server-cached request id (POST→cache,
   then `GET /run/{step}?ticket=…` opens stream).
2. **Arrow IPC vs JSON for tabular reads.** Endpoints in §1.3 default
   to content negotiation. Confirm frontend has an Arrow loader path,
   or simplify to JSON-only for v1.
3. **Tile URL construction.** Frontend needs to map `(image_id, lod)`
   to `(stem, lod)` to build a static URL. The mapping lives in
   `00_thumbnails/index.json`. Confirm frontend will fetch this index
   at Explorer-tab load and key tile URLs by `stem`.

### `[NEEDS-MV]`
4. **DZI vs raw-image-per-tile.** Q-P1 capped v1 at 60×60 = 3,600
   tiles, OpenSeadragon "image = tile" model. Confirm whether DZI is
   needed at all in v1, OR whether OpenSeadragon's
   `tileSources: [{type:'image', url:...}]` array suffices. Drives
   §1.4 static URL design.
5. **Raw image downsampling.** When user zooms into a single tile past
   lod3 (~1920×1200), do we need to serve a downsampled crop, or does
   OpenSeadragon's client-side scaling cover it? If server-side, that's
   a new endpoint.
6. **Selector preview parity.** Q-U3 keeps Selector preview as
   lightweight HTML `<img>` + simple zoom — NOT OpenSeadragon. Confirm
   `/api/v1/projects/{pid}/data/annotations/{domain_id}/preview` PNG
   contract is the right shape (server-rendered crop + optional
   contour overlay), vs. shipping the raw image + mask separately and
   compositing client-side.

### `[NEEDS-DEVOPS]`
7. **SMB mount options.** `noserverino`, `actimeo=0`, `cache=none` —
   which combination on the deployment host gives reliable
   atomic-replace semantics? Drives §3.3.
8. **CORS origins.** What hosts will serve the React frontend in
   production / staging / dev? Default in §7.3 is empty (locked
   down).
9. **Reverse proxy / TLS termination.** If nginx/Caddy fronts the API,
   confirm SSE event-stream is **not** buffered (`X-Accel-Buffering: no`
   or `proxy_buffering off`). Otherwise progress arrives in chunks
   not events.
10. **Process supervisor.** uvicorn workers = 1 (state in process,
    per-project mutex is in-memory). Multi-worker requires a
    cross-worker mutex (Redis/file-lock) — out of scope for v1, but
    devops should pin `--workers 1` until then.
