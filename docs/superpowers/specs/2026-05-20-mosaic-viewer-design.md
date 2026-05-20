# Explorer Mosaic Viewer — Design (OpenSeadragon, v1)

**Date:** 2026-05-20
**Stage:** 2 (design — implementation deferred)
**Author:** Machine Vision Specialist
**Scope:** Frontend mosaic viewer + the backend tile contract that feeds it.
Out-of-scope: app shell, store, auth, SSE, non-tile endpoints (Frontend
Architect / Backend Architect / DevOps own those).

This doc replaces the Plotly `go.Image` mosaic at
`src/flake_analysis/ui/tab_explorer.py:307-852` with an OpenSeadragon
(OSD) viewer driven by per-image LOD WebPs that the existing thumbnail
pyramid (`src/flake_analysis/core/pipeline/thumbnails.py:57-61`)
already produces. **No new pyramid is generated.**

---

## 1. Why OpenSeadragon vs alternatives

OSD is the de-facto microscopy viewer (used by HistomicsTK, OMERO,
Bio-Formats web). It pairs a single canvas with a battle-tested
viewport-coordinate model, has a stable plugin surface for SVG
overlays (selection rect, pass/fail dim), and accepts heterogeneous
"image" sources via `LegacyTileSource` / `SimpleImage` so we can mount
3,600 independent per-image WebPs without baking a Deep Zoom pyramid.

Briefly dismissed:

- **Leaflet.** Geo coordinate system (lat/lng wrap, datum, projections)
  is overhead we never use. Forces an artificial CRS.Simple. OSD's
  viewport math is image-native.
- **deck.gl.** WebGL bitmap-layer rendering is overkill for 3,600
  static tiles; we'd write our own pan/zoom/inertia/wheel handling and
  every overlay primitive. ROI: negative.
- **Pure HTML grid (CSS transforms).** No smooth zoom — wheel events
  would discrete-step. Browsers cap layer count and memory on
  3,600 `<img>` elements; current Streamlit numpy mosaic exists
  precisely to dodge this failure mode.

OSD wins on: viewport-driven tile lifecycle, image-native coordinates,
built-in inertia/wheel/pinch, SVG/canvas overlay plugins, and a
permissive license. Not magical — its weakness is exactly the one we
sidestep below in §2 (it expects DZI, not per-image LODs).

---

## 2. Tile model decision

> **Verdict for v1: Path A — `LegacyTileSource` per image, no DZI.**

### Path A (chosen) — image-per-tile via `LegacyTileSource`

Each grid cell `(col, row)` is one OSD "tiled image" whose entire
"pyramid" has exactly one source per LOD level. We register up to 4
levels per image (lod0 / lod1 / lod2 / raw) with their on-disk
`(width, height)` from `LOD_SIZES` at
`src/flake_analysis/core/pipeline/thumbnails.py:57-61`. OSD drives LOD
selection per-image based on the on-screen size of that tile in the
current viewport (`getMaxZoom` / `tileLevels`).

Reasoning:

1. **Zero new artifacts.** The pyramid we have IS the pyramid OSD
   needs — it just needs to be told the dimensions per level. Saves
   the second thumbnails-step pass that DZI would require (Path B
   would roughly double thumbnail step time + storage).
2. **Heterogeneous LOD set is fine.** OSD doesn't require contiguous
   power-of-two levels; `LegacyTileSource` accepts an explicit list.
   Our LODs are 64×40, 192×120, 480×300, 1920×1200 — non-power-of-two
   but consistent across all images.
3. **Per-image LOD is exactly the access pattern.** When the user
   zooms to a single tile, OSD requests `raw` for that one image, not
   `raw` for every image in the viewport (which is what a global DZI
   would force-fetch as one mega-image).
4. **3,600 tiles is below OSD's "many images" practical ceiling.**
   `imageLoaderLimit` capped at 8 (matches the §2.1 budget of "≤ 8 in
   flight") + `maxImageCacheCount` ≈ 600–1000 lod0/lod1 images
   keeps DOM canvas surface bounded.

### Path B (deferred) — pre-baked DZI mosaic

Stitch all images into one virtual canvas, generate a Deep Zoom
pyramid (256-px tiles, log₂ levels). Pros: smoother zoom across
boundaries, OSD's hot path. Cons: ~2× thumbnail step time, ~1.5×
storage, requires re-generation when the dataset changes, complicates
single-tile click-to-select (need pixel→cell math). Revisit if v1
profiling shows OSD struggling on the 3,600-image case.

### OSD configuration sketch (Path A)

Settings the FE will instantiate the `OpenSeadragon.Viewer` with:

| Option | Value | Why |
|---|---|---|
| `tileSources` | array of `LegacyTileSource` per image | one per cell |
| `collectionMode` | `true` | grid layout |
| `collectionRows` / `collectionColumns` | `grid_h` / `grid_w` | from backend |
| `collectionTileSize` | `1` | use levels' own widths |
| `collectionTileMargin` | `0` | no gutters between tiles |
| `imageLoaderLimit` | `8` | matches §2.1 concurrency budget |
| `maxImageCacheCount` | `1024` | ≈ 0.7× full grid at lod0 |
| `immediateRender` | `false` | let lower LODs draw first |
| `preserveImageSizeOnResize` | `true` | stable coords on window resize |
| `gestureSettingsMouse.scrollToZoom` | `true` | matches v0.2.17 scrollZoom |
| `defaultZoomLevel` | `0` (fit-grid) | initial mosaic render |
| `minZoomLevel` | `0.8` | prevent zoom-out pan into empty void |
| `maxZoomLevel` | `~grid_w * 60` | allow zoom into raw at any tile |
| `crossOriginPolicy` | `"Anonymous"` | required for canvas overlay readback |

**Y-axis:** OSD's viewport y grows downward, but we want `iy=0` at the
bottom of the mosaic per `tab_explorer.py:404-440`. The flip happens
**server-side** (see §4 below); the frontend treats whatever
`(col, row)` it receives as already-flipped.

---

## 3. Backend tile URL contract (for Backend Architect)

### URL pattern

```
GET /api/v1/projects/<project_id>/tiles/lod{N}/<stem>.webp
```

- `project_id` — opaque project handle (per §N1/§N2 of requirements).
- `N` ∈ `{0, 1, 2}` for cached LODs, `N = 3` for raw fallback.
- `<stem>` — raw image stem from `index.json["entries"][i]["stem"]`,
  same as `core/pipeline/thumbnails.py:154`.

When `N == 3`, backend serves the raw PNG/JPEG/WebP from
`raw_images_dir` directly. URL stays under `/tiles/lod3/...` even
though the file isn't a thumbnail — keeps a single endpoint shape.

### Caching headers

| Header | Value | Source |
|---|---|---|
| `Cache-Control` | `public, max-age=86400, immutable` | content is content-addressed |
| `ETag` | `"<index.params_hash>:<entry.signature[1]>:<entry.signature[2]>"` | `params_hash` from `index.json` + per-entry size+mtime, see `core/pipeline/thumbnails.py:97-101` |
| `Content-Type` | `image/webp` (lod0–2) or actual raw type (lod3) | sniffed |
| `Last-Modified` | from `entry.signature[2]` mtime | for HTTP/1.1 conditional GET |

The ETag is **stable across re-runs** as long as the source raw image
hasn't changed (`signature` is `(name, size, mtime)`). When thumbnails
are regenerated with `force_recompute=True`, the source raws are
unchanged, so the ETag stays the same — which is correct, the WebP
bytes are deterministic at fixed `quality=80`.

### 404 behavior

Missing tile → return 404 (not 500). Frontend draws an empty (white)
cell in OSD via the `tile-load-failed` event handler — **no toast, no
error banner.** Mirrors the current `tab_explorer.py:509-511`
silent-fallback behavior. Log at `INFO` server-side with project_id +
stem so we can spot systematic gaps.

### Auth dependency

Yes — even tile endpoints require `Depends(get_current_user)` for
parity with the rest of the API and to satisfy §N4 (auth hook in
place v1, real auth post-v1). For v1 the dependency returns a static
"local" identity; the dependency line stays in place so post-v1 auth
doesn't force tile-endpoint surgery.

### File resolution (backend hides on-disk layout from FE)

Backend resolves `lod{N}/<stem>.webp` against either:

1. `index.json["cache_dir"]` if present (v0.2.16 layout, redirected
   per `core/pipeline/thumbnails.py:104-116`), OR
2. `<analysis_folder>/00_thumbnails/lod{N}/<stem>.webp` (v0.2.15
   in-folder layout, fallback per
   `core/pipeline/thumbnails.py:284-294`), OR
3. For `N==3` (raw): `<raw_images_dir>/<stem><raw_ext>` where
   `raw_ext` comes from `index.json["params"]["raw_ext"]`.

The frontend never sees this — it always requests
`/tiles/lod{N}/<stem>.webp` and gets back bytes-or-404.

Pseudocode handed to Backend Architect (§10 below).

---

## 4. Image_id ↔ tile mapping

The frontend needs a single endpoint that returns enough metadata to
populate OSD's `tileSources` array AND map clicks back to image_ids.

### Endpoint

```
GET /api/v1/projects/<project_id>/explorer/grid
```

### Response shape

```json
{
  "grid_w": 60,
  "grid_h": 60,
  "lod_sizes": {
    "0": [64, 40],
    "1": [192, 120],
    "2": [480, 300],
    "3": [1920, 1200]
  },
  "signature": "sha256:abc123...",
  "tiles": [
    {
      "image_id": 0,
      "col": 0,
      "row": 59,
      "stem": "ix000_iy000",
      "raw_name": "ix000_iy000.png",
      "available_lods": [0, 1, 2, 3]
    },
    ...
  ]
}
```

Field semantics:

- **`grid_w` / `grid_h`** — final mosaic dimensions in cells. Computed
  from filename parsing per `tab_explorer.py:425-440`. Falls back to
  `ceil(sqrt(n))` when filenames don't parse
  (`tab_explorer.py:443-450`).
- **`lod_sizes`** — single source of truth shared with FE. Read from
  `index.json["params"]["lod_sizes"]`; backend appends the implicit
  `"3"` raw size by reading the first raw image's PIL header (or
  hardcoded 1920×1200 with a `[NEEDS-BE]` for whether to peek).
- **`signature`** — `index.json["params_hash"]` + manifest
  `steps.thumbnails.completed_at` concatenated, hashed. Used by FE as
  a cache buster on the `/explorer/grid` response and as the ETag
  prefix for tile URLs.
- **`tiles[].col` / `row`** — already y-flipped server-side (i.e.
  `iy=0 → row=grid_h-1`). FE plugs straight into OSD's
  `collectionRows`/`collectionColumns`. Single source of truth for
  the flip math at `tab_explorer.py:404-440`.
- **`tiles[].available_lods`** — which LOD WebPs actually exist.
  Always `[0,1,2,3]` for normal datasets but tolerates partial
  generation (`n_failed > 0` in `index.json`).

### Why backend, not frontend, computes the y-flip

It's deterministic from filenames, runs once per project load, and
keeps the FE coordinate model trivially "row 0 = top, row grid_h-1 =
bottom" with no client-side awareness of `iy`. Keeps coordinate
transform logic in one place — required by Critical Rule #5 (every
transform must be explicit and tested).

### Where the mapping is built

Backend reads `00_thumbnails/index.json` (for stems) and
`annotations.json` `images[]` array (for image_id → file_name) per
`tab_explorer.py:565-585`. If `annotations.json` is missing, return
HTTP 409 with `{error: "annotations_required", detail: ...}` — same
contract as the current `tab_explorer.py:581-585` warning.

---

## 5. LOD selection logic

### Today (Streamlit)

- `_choose_lod(cell_px)` at `tab_explorer.py:360-368` maps the
  per-cell pixel budget to a LOD: ≤96 → lod0, ≤288 → lod1, ≤720 →
  lod2, else raw.
- Manual override picker (Auto / lod0 / lod1 / lod2 / raw) at
  `tab_explorer.py:613-639`.
- Choice is **global** — every cell uses the same LOD because the
  Streamlit code builds one giant numpy mosaic via
  `_build_mosaic_array` at `tab_explorer.py:453-525`.

### New (OSD per-image LOD)

OSD's `LegacyTileSource` advertises levels `0..len(LODs)-1` per image.
The viewer requests the level whose pixel size best matches the
on-screen size of *that* image in the current viewport — independently
per visible image. Concretely:

| Viewport zoom | On-screen tile size | LOD requested |
|---|---|---|
| Fit-grid (60×60 visible) | ~16 px wide | lod0 (64×40, downsampled) |
| Mid-zoom (~10×10 visible) | ~96 px wide | lod0→lod1 swap |
| Close zoom (~3×3 visible) | ~320 px wide | lod1→lod2 |
| Single-tile zoom (1 visible) | ~960+ px wide | lod2→raw |

This is what OSD does natively for DZI; with `LegacyTileSource` we
just hand it the level list and it picks. No custom "swap" logic in
FE code — that's the win over the current explicit picker.

### Manual LOD override (parity with current picker)

Keep the picker for power-users (some users want "force raw on all
visible cells now"). Implemented via OSD's
`tiledImage.setMaxLevel(N)` per visible image — caps the pyramid at
the chosen level. For "Auto" (default) we leave `setMaxLevel` at its
natural max.

| Picker option | OSD action |
|---|---|
| Auto | natural per-image LOD selection (default) |
| lod0 | `setMaxLevel(0)` on every TiledImage |
| lod1 | `setMaxLevel(1)` |
| lod2 | `setMaxLevel(2)` |
| raw | `setMaxLevel(3)` AND force `setMinLevel(3)` (no fallback to lower) |

### Memory comparison

**Current v0.2.18 Streamlit (`tab_explorer.py:483-525`):**
- Builds one numpy `canvas` of shape `(grid_h*cell_h, grid_w*cell_w, 3)`.
- At LOD2 with 60×60 grid + native 480×300 cells:
  `60*300 × 60*480 × 3 bytes = 18000 × 28800 × 3 ≈ 1.5 GB` per call.
- Cached `max_entries=4` → **up to 6 GB** held in Streamlit's
  process. This is the bug.

**New OSD (Path A, lod0 always loaded):**
- 3,600 lod0 WebPs × 3-5 KB each compressed = **~15 MB on the wire**.
- Decompressed in browser canvas: 3,600 × 64×40×4 bytes (RGBA) ≈
  **35 MB peak** if all are decoded. OSD discards offscreen canvases
  via `maxImageCacheCount`.
- On zoom-in: 9 visible images at lod2 = 9 × 480×300×4 ≈ **5 MB**;
  on raw zoom: 1 × 1920×1200×4 ≈ **9 MB**.
- Peak working set: **~50–100 MB**, well under the 1 GB §2.1 ceiling.

The win: ~15× memory reduction at the worst case, ~150× at the
typical case.

---

## 6. Overlays

OSD overlays attach to viewport coordinates and follow pan/zoom for
free. Two overlay types in v1:

### 6.1 Pass/fail dimming (US-E2)

**Today:** numpy desaturate + 50% white blend at
`tab_explorer.py:513-519`. Per-pixel cost scales with mosaic size.

**New:** CSS `filter: grayscale(1) opacity(0.5)` applied to the
`<canvas>` element OSD draws each failing TiledImage into. OSD exposes
`tiledImage.setOpacity()` and `tiledImage.setCompositeOperation()`;
combine those with a per-image overlay rectangle holding the white
blend.

Concretely:
- For each `image_id` not in `pass_set`:
  `tiledImage.setOpacity(0.5)` + add a same-size SVG `<rect>` with
  `fill: white; fill-opacity: 0.5`.
- Toggling the cluster filter only updates `setOpacity` calls — no
  re-render of pixels, no fetch.

Picked CSS-filter+SVG over canvas readback because (a) it preserves
the original pixels for overlays added later, (b) it's GPU-composited
in modern browsers (zero JS cost), (c) re-applies for free across
pan/zoom.

### 6.2 Selected-tile highlight (US-E3)

**Today:** Plotly `add_shape` gold rect at `tab_explorer.py:764-780`.

**New:** SVG `<rect>` overlay attached at viewport coords
`(col, row, 1, 1)` (unit cell) with stroke `#FFC800`, width `3` (per
the current value). OSD's `addOverlay` API places it; pan/zoom
follows for free.

Selected-flake update flow: when `selected_flake_id` changes,
look up its `image_id`, find `(col, row)` from the grid mapping, call
`viewer.addOverlay({location: rect, element: highlightSvg})`. Remove
the previous overlay first.

### 6.3 Bbox / outline overlays (DEFERRED — O9)

Per requirements §3 O9, bbox/outline overlays on flakes are deferred.
Design plug-in point: each flake's bbox is in image-pixel coordinates
of its raw image. To project onto the OSD viewport, multiply by the
TiledImage's `imageToViewportCoordinates`. Add as SVG `<rect>` /
`<path>` overlays. The plumbing is identical to the selected-tile
highlight, just at sub-image granularity.

---

## 7. Click-to-select interaction

### Event flow

```
user clicks canvas
  → OSD canvas-click event (viewport coords)
  → viewer.viewport.viewerElementToViewportCoordinates(event.position)
  → translate viewport (x,y) → (col, row)  via collection layout
  → look up tiles[col, row] → image_id from /explorer/grid response
  → call onTileClick(image_id)
  → frontend updates selected_flake_id locally to first flake of image_id
```

### Coordinate translation

In `collectionMode`, OSD lays each TiledImage at `(col, row)` in
viewport coordinates `[0..grid_w] × [0..grid_h]`. Click at viewport
`(x, y)` maps to `(col=floor(x), row=floor(y))`.

### Where the "first flake" lookup happens

Two valid options:

**Option A (recommended):** Frontend already has the full filtered
flake list cached (it renders the right-side flake list). Look up
`first_flake = filtered.find(f => f.image_id === clicked_image_id)`
in JS. No round-trip.

**Option B:** Backend endpoint
`GET /api/v1/projects/<id>/explorer/first-flake?image_id=...`
returning `{flake_id}`. Adds a roundtrip per click — unjustified for
v1 with ≤ a few thousand flakes already in memory.

Pick Option A. Matches current single-process semantics at
`tab_explorer.py:837-842`.

### Drag vs click discrimination

OSD already distinguishes `canvas-click` from `canvas-drag` via its
gesture-settings click threshold. No custom code. Verify this on
trackpad two-finger pan to confirm it doesn't generate spurious
clicks.

---

## 8. Performance budgets

### §2.1 reqs — Explorer mosaic

| Metric | Target | OSD path-A approach |
|---|---|---|
| Pan latency | ≤ 150 ms | OSD requestAnimationFrame-driven; tiles already in cache redraw instantly |
| Zoom-step latency (LOD switch) | ≤ 500 ms | 8 parallel tile fetches × ~50 ms WebP fetch over LAN = ~50 ms; SMB-backed needs §9 mitigations |
| Initial mosaic render (60×60, lod0) | ≤ 2 s | 3,600 lod0 WebPs × ~3 KB / 8-concurrent / ~30 ms each = ~13.5 s naive — **needs spritesheet-or-progressive-render strategy, see [NEEDS-FE]** |
| Peak browser RAM | ≤ 1 GB | ~50–100 MB typical (§5) |
| Peak server RAM per session | ≤ 500 MB | static-file serving, near-zero RAM cost |
| Tile fetch concurrency | ≤ 8 | OSD `imageLoaderLimit: 8` |
| Memory release on tab close | ≤ 30 s | OSD destructor + browser tab GC; no server-side per-session cache |

### Initial render bottleneck — addressed

3,600 sequential lod0 fetches at 8-concurrent over a non-LAN connection
will exceed 2 s. Three mitigations available:

1. **Render-as-they-arrive.** OSD draws each tile as soon as it
   loads. The user sees the mosaic populate. The "≤ 2 s" budget is
   for *first paint of viewport-visible tiles* (≤ ~50 at lod0 fitting
   the screen), not all 3,600.
2. **Server-side spritesheet for lod0.** Optional v1.5: backend
   stitches all 3,600 lod0 WebPs into one big atlas image
   (60×60 × 64×40 = 3840×2400 px ≈ ~3 MB). Frontend uses one
   `SimpleImage` source for the lod0 layer, falls back to per-image
   for higher LODs. **[NEEDS-BE]** to confirm spritesheet generation
   isn't disproportionate work for v1.
3. **HTTP/2 multiplexing.** If served behind nginx (DevOps), the
   per-tile RTT amortizes near-perfectly. **[NEEDS-DEVOPS]**.

For v1, recommend mitigation #1 alone; revisit #2 if profiling shows
>2 s on the perceived-latency metric (not all-tiles-loaded).

### Comparison to current v0.2.18

| Metric | v0.2.18 | OSD v1 | Improvement |
|---|---|---|---|
| Peak RAM (LOD2 60×60) | ~1.5 GB / call, ~6 GB cached | ~100 MB | **15-60× lower** |
| Pan response | full Streamlit rerun → numpy rebuild → re-encode → re-send (~3-5 s) | OSD canvas redraw (~16 ms) | **~200× faster** |
| LOD switch | full rebuild + recache (~3-5 s) | per-image fetch (~100-500 ms) | **~10× faster** |
| Memory leak on tab close | persists in Streamlit cache (US-E1 AC violation) | gone on tab GC | **fixed** |

---

## 9. SMB latency mitigation

Backend serves tile bytes from SMB-mounted filesystems (per Q-S2
resolution). SMB latency is the binding constraint, not network or
CPU. Client-side strategies:

### 9.1 HTTP/2 multiplexing (assumed via nginx)

If the FastAPI app is served direct (uvicorn → browser), HTTP/1.1
caps to 6 connections per origin → only 6-concurrent tile fetches at
once, plus head-of-line blocking. **HTTP/2** lets one TCP connection
multiplex our 8 parallel fetches with no HOL. **[NEEDS-DEVOPS]:**
confirm nginx (or equivalent) terminates HTTPS/2 in front of FastAPI.
If not, drop `imageLoaderLimit` to 6 to match HTTP/1.1 connection
ceiling.

### 9.2 Aggressive caching headers (covered §3)

`max-age=86400, immutable` + ETag means browser tab refresh hits the
HTTP cache, not the server. Big win for the "I'm exploring the same
project all day" workflow.

### 9.3 Local-disk cache (already in place)

The local-cache redirect at
`core/pipeline/thumbnails.py:104-116` already mitigates SMB **read**
latency for the backend (writes go to `~/.cache/...`, subsequent
reads go to local disk). This is reused unchanged.

### 9.4 Predictive pan-prefetch (DEFERRED)

Post-v1: detect pan velocity vector, prefetch tiles in the predicted
direction at one zoom level higher than current. OSD has hooks for
this (`viewer.addHandler('viewport-change', ...)`). Flag as
`[NEEDS-FE]` for v1.5.

---

## 10. Compatibility & v0.2.15 fallback

### Frontend impact: zero

The FE always requests
`/api/v1/projects/<id>/tiles/lod{N}/<stem>.webp`. Backend hides
whether the bytes come from:

1. `~/.cache/stand-alone-analyzer/thumbnails/<sha>/lod{N}/<stem>.webp`
   (v0.2.16, when `index.json["cache_dir"]` is set), OR
2. `<analysis_folder>/00_thumbnails/lod{N}/<stem>.webp` (v0.2.15
   in-folder layout), OR
3. `<raw_images_dir>/<stem><raw_ext>` (LOD3 raw fallback).

### Backend resolver pseudocode (for Backend Architect)

```
def resolve_tile_path(project, lod, stem):
    index = read_index_json(project.analysis_folder)
    raw_ext = index["params"]["raw_ext"]

    if lod == 3:                    # raw fallback
        return project.raw_images_dir / f"{stem}{raw_ext}"

    cache_dir = index.get("cache_dir")
    if cache_dir is not None:        # v0.2.16 redirect layout
        candidate = Path(cache_dir) / f"lod{lod}" / f"{stem}.webp"
        if candidate.exists():
            return candidate

    # v0.2.15 in-folder fallback (also catches new entries in
    # legacy projects pre-redirect).
    candidate = (
        project.analysis_folder
        / "00_thumbnails"
        / f"lod{lod}"
        / f"{stem}.webp"
    )
    if candidate.exists():
        return candidate

    # entry's `outputs[lod{N}]` may carry an absolute or
    # `00_thumbnails/...`-prefixed path (more legacy edge cases —
    # mirror core/pipeline/thumbnails.py:287-294).
    entry = next(
        (e for e in index["entries"] if e["stem"] == stem), None
    )
    if entry is not None:
        rel = entry["outputs"].get(f"lod{lod}")
        if rel:
            p = Path(rel)
            if p.is_absolute() and p.exists():
                return p
            if rel.startswith("00_thumbnails/"):
                return project.analysis_folder / rel
            if cache_dir:
                return Path(cache_dir) / rel
            return project.analysis_folder / "00_thumbnails" / rel

    raise HTTPException(404)
```

This mirrors the `_resolve` closure at
`core/pipeline/thumbnails.py:287-294` exactly. **B6 (M)** of
requirements is satisfied here.

---

## 11. Tile flow — labeled diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                         BROWSER (React + OSD)                      │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  ExplorerPage                                                │  │
│  │   │                                                          │  │
│  │   ├──▶ GET /api/v1/projects/<id>/explorer/grid               │  │
│  │   │   { grid_w, grid_h, tiles[], lod_sizes, signature }      │  │
│  │   │                                                          │  │
│  │   └──▶ build OSD tileSources[]                               │  │
│  │       │                                                      │  │
│  │       ▼                                                      │  │
│  │   ┌────────────────────────────────────────────────────────┐ │  │
│  │   │  OpenSeadragon.Viewer (collectionMode)                 │ │  │
│  │   │                                                        │ │  │
│  │   │  for each tile in tiles[]:                             │ │  │
│  │   │    LegacyTileSource(                                   │ │  │
│  │   │      levels: [                                         │ │  │
│  │   │        {url:.../tiles/lod0/<stem>.webp, w:64,  h:40},  │ │  │
│  │   │        {url:.../tiles/lod1/<stem>.webp, w:192, h:120}, │ │  │
│  │   │        {url:.../tiles/lod2/<stem>.webp, w:480, h:300}, │ │  │
│  │   │        {url:.../tiles/lod3/<stem>.webp, w:1920,h:1200},│ │  │
│  │   │      ],                                                │ │  │
│  │   │      x: tile.col, y: tile.row                          │ │  │
│  │   │    )                                                   │ │  │
│  │   │                                                        │ │  │
│  │   │  Overlays:                                             │ │  │
│  │   │    • per-image opacity (pass/fail dim) §6.1            │ │  │
│  │   │    • SVG gold rect on selected tile §6.2               │ │  │
│  │   │                                                        │ │  │
│  │   │  Events:                                               │ │  │
│  │   │    canvas-click → (col,row) → image_id → first_flake   │ │  │
│  │   │    viewport-change → OSD picks LOD per visible image   │ │  │
│  │   └────────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                  │                                 │
└──────────────────────────────────┼─────────────────────────────────┘
                                   │ HTTP/2 (nginx)
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│                              FastAPI                               │
│                                                                    │
│  GET /tiles/lod{N}/<stem>.webp                                     │
│    │                                                               │
│    ├─▶ resolve_tile_path() (§10)                                   │
│    │     ├─ cache_dir set?  → ~/.cache/.../lod{N}/<stem>.webp      │
│    │     ├─ legacy?         → analysis/00_thumbnails/lod{N}/...    │
│    │     └─ N==3?           → raw_images_dir/<stem><raw_ext>       │
│    │                                                               │
│    └─▶ FileResponse + Cache-Control + ETag                         │
│                                  │                                 │
└──────────────────────────────────┼─────────────────────────────────┘
                                   │ filesystem read
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│                    Local disk + SMB-mounted analysis_folder       │
│                                                                    │
│  ~/.cache/stand-alone-analyzer/thumbnails/<sha>/                   │
│    └─ lod0/, lod1/, lod2/   (v0.2.16 layout, fast)                 │
│                                                                    │
│  /Volumes/<mount>/<analysis_folder>/                               │
│    ├─ 00_thumbnails/index.json     ← always here                   │
│    └─ 00_thumbnails/lod{0,1,2}/    ← v0.2.15 only                  │
│                                                                    │
│  /Volumes/<mount>/<raw_images_dir>/                                │
│    └─ <stem>.png                   ← LOD 3 source                  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 12. Open questions

### `[NEEDS-BE]`

1. **BE-1 — LOD3 raw size.** Is `1920×1200` safe to hardcode for the
   `lod_sizes["3"]` field in `/explorer/grid`, or should backend peek
   the first raw image's PIL header? Real datasets may use different
   raw resolutions. Recommend: peek once at endpoint call time, cache
   in memory keyed by `params_hash`.
2. **BE-2 — Spritesheet for initial render.** Worth implementing the
   stitched-lod0 atlas (§8 mitigation #2) for v1, or defer until
   profiling proves the per-tile fetch path doesn't meet the 2 s
   first-paint target?
3. **BE-3 — Raw image content-type.** When the raw is PNG/TIFF, do we
   transcode to WebP on the fly (CPU cost per request) or serve PNG
   directly (larger bytes)? Recommend: serve as-is; PNG is decoded
   natively by browsers and the LOD3 fetch is rare (one image at a
   time on max zoom).
4. **BE-4 — `raw_ext` per-project consistency.** Do we assume one
   extension across all raws in a project (current code assumes yes
   via `index.json["params"]["raw_ext"]`), or handle mixed `.png` +
   `.jpg` in the same `raw_images_dir`?

### `[NEEDS-FE]`

1. **FE-1 — Pan-prefetch in v1?** The §9.4 predictive prefetch is
   useful but adds complexity. v1 ships without it unless profiling
   under SMB shows pan-into-cold-tiles latency >150 ms.
2. **FE-2 — OSD version.** OSD 4.x is recent; 5.x ships in 2025 with
   improved collection-mode performance. Pin a version with `npm`
   that's stable across browsers per §A1.
3. **FE-3 — Drag-to-pan vs lasso conflict.** Current Streamlit has
   `lasso2d` removed via `modeBarButtonsToRemove`
   (`tab_explorer.py:803`). Confirm the React Explorer doesn't need
   any selection mode beyond click — if it does, OSD's drag conflicts
   need a modifier key (Shift+drag = lasso?).
4. **FE-4 — Resize handling.** When the right-side flake list expands
   or the sidebar collapses, the OSD viewport resizes. Confirm
   `preserveImageSizeOnResize: true` keeps zoom level stable rather
   than refit-to-grid.

### `[NEEDS-DEVOPS]`

1. **DEVOPS-1 — HTTP/2 in front of FastAPI.** Section §9.1 assumes
   nginx (or equivalent reverse proxy) terminates HTTP/2. If FastAPI
   is exposed directly via uvicorn, downgrade `imageLoaderLimit` to 6
   per HTTP/1.1 host limit AND flag the §8 first-paint budget as
   at-risk.
2. **DEVOPS-2 — CORS for tile endpoints.** Per Q-S1 resolution,
   frontend may live on a different origin than the backend.
   `Access-Control-Allow-Origin` must whitelist the FE origin for
   tile URLs (and for `/explorer/grid`). `crossOriginPolicy:
   "Anonymous"` on the OSD viewer triggers preflight on tile fetches
   if any custom header is set — confirm the tile endpoint stays
   header-clean.
3. **DEVOPS-3 — SMB read concurrency.** Backend tile endpoint is
   stateless and trivially parallelizable, but SMB read storms can
   tank the mount. Confirm whether uvicorn worker count + filesystem
   read concurrency stays under the SMB mount's connection limit.

---

## 13. Summary

- **Tile model:** Path A (per-image `LegacyTileSource`, no DZI) — reuses
  the existing pyramid as-is, zero new artifacts, matches §2.1
  budgets.
- **Backend contract:** `GET /api/v1/projects/<id>/tiles/lod{N}/<stem>.webp`
  + `GET /api/v1/projects/<id>/explorer/grid`. Y-flip and dual-layout
  resolution server-side.
- **Memory budget:** ~50–100 MB peak in browser; 15–60× below current
  Streamlit baseline.
- **Overlays:** CSS-filter dim for pass/fail, SVG rect for selection.
  Bbox/outline plug-in points documented; render deferred per O9.
- **Compatibility:** v0.2.15 in-folder + v0.2.16 cache-redirect layouts
  both resolved server-side; FE never sees the difference.

The crux call — Path A over Path B — is what makes this a no-new-pyramid
v1. If profiling later shows OSD struggling on collection-mode at 3,600
images, Path B (DZI mosaic) is the documented escape hatch.
