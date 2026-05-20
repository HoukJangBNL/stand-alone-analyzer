# Deployment Design — React + FastAPI Stand-Alone Analyzer (v1)

**Date:** 2026-05-20
**Stage:** Spec (deployment / infra only)
**Scope:** Single Linux host, single user, SMB-mounted data. Atomic
cutover from Streamlit. Backend = FastAPI/uvicorn (Python, in-process
pipeline imports). Frontend = React static build.

This doc sits below the requirements (§4.99 decisions Q-S1..Q-S4) and
above the BE/FE/MV implementation specs. It describes how bits get
served, where files live, and how the process stays alive — not what
the bits compute.

---

## 1. Topology (target end-state for v1)

Single Linux host. nginx terminates HTTP, serves the React SPA from
disk, and reverse-proxies the FastAPI app on a localhost port. uvicorn
runs FastAPI single-process (1 user, sync compute, no queue). The host
has SMB mounts for `analysis_folder/` (rw) and `raw_images/` (ro), and
a fast local SSD for the thumbnail cache.

```
                                                   ┌──────────────────────┐
                                                   │ SMB share (rw)       │
                                                   │  /mnt/analysis/      │
                                                   │   manifest.json,     │
                                                   │   00..06_*/          │
                                                   └──────▲───────────────┘
                                                          │  open/read/write
                                                          │  (slow, latency)
 ┌────────┐  HTTPS/HTTP  ┌──────────────────┐  /api/*    ┌┴───────────────┐
 │Browser │─────────────▶│ nginx :80/:443   │───────────▶│ uvicorn :8000  │
 │ (SPA)  │              │  (reverse proxy  │            │ FastAPI app    │
 │        │◀─────────────│   + static)      │◀───────────│ (in-process    │
 └────────┘  /, /assets/*│ /usr/share/      │ JSON/SSE   │  pipeline.*)   │
            /api/v1/...  │  stand-alone-    │            └┬───────────────┘
                         │  analyzer/web/   │             │
                         │                  │             │ tile resolve
                         │  /tiles/* ──────────────────┐  │ (cache_dir
                         └──────────────────┘ X-Accel  │  │  from index.json)
                                                       ▼  ▼
                                              ┌──────────────────────┐
                                              │ Local SSD (cache)    │
                                              │  /var/cache/saa/     │
                                              │   thumbnails/<sha>/  │
                                              │    lod0/.../*.webp   │
                                              └──────────────────────┘
                                                       ▲
                                                       │  read-only
                                              ┌────────┴─────────────┐
                                              │ SMB share (ro)       │
                                              │  /mnt/raw_images/    │
                                              │   ix###_iy###.png    │
                                              └──────────────────────┘
```

Data-flow per request type:
- **Static asset** (`GET /`, `/assets/index-XXXX.js`): browser → nginx → disk → browser. uvicorn never sees it.
- **API JSON** (`GET /api/v1/projects/<id>/manifest`): browser → nginx → uvicorn → SMB (`manifest.json`) → uvicorn → nginx → browser.
- **SSE progress** (`POST /api/v1/run/<step>`): browser → nginx (proxy_buffering off, long timeout) → uvicorn → pipeline runs in-process → events flushed.
- **Tile** (`GET /api/v1/projects/<id>/tiles/lod1/<stem>.webp`): browser → nginx → uvicorn → resolve cache_dir from index.json → `X-Accel-Redirect: /_tiles_internal/<sha>/lod1/<stem>.webp` → nginx serves from local SSD → browser. (See §2 for justification.)

### 1.1 Why nginx (and not the alternatives)

- **FastAPI-native StaticFiles**: works but blocks the uvicorn event loop on every static asset request (one less worker available for SSE/compute). nginx serves static from disk with sendfile/aio at orders-of-magnitude lower per-request cost. nginx wins for the tile serve path even more (§2).
- **Caddy**: fine product, automatic TLS, but no operational presence at BNL/CFN; pulling a new binary into a regulated environment is friction with no upside for v1. nginx is on every base image.
- **Traefik**: optimised for dynamic container service discovery. We have one static backend. Overkill.
- **Apache httpd**: heavier per-connection footprint; SSE keep-alive handling is more awkward. No reason to choose it over nginx.

Decision: **nginx** for v1.

---

## 2. Static asset layout

```
/usr/share/stand-alone-analyzer/web/        # React build output (dist/)
├── index.html
├── assets/
│   ├── index-<hash>.js
│   ├── index-<hash>.css
│   └── ...
└── favicon.ico
```

The frontend team's `npm run build` produces `web/dist/`; deploy copies
that to `/usr/share/stand-alone-analyzer/web/`.

### 2.1 nginx routes

```nginx
# /etc/nginx/sites-available/stand-alone-analyzer
server {
    listen 80 default_server;
    server_name _;

    # ---- React SPA ----
    root /usr/share/stand-alone-analyzer/web;
    index index.html;

    # Hashed assets: immutable, cache forever
    location /assets/ {
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # SPA shell: never cache the HTML itself
    location = /index.html {
        add_header Cache-Control "no-store, must-revalidate";
        try_files $uri =404;
    }

    # SPA history fallback for client-side routes (/projects/<id>/explorer etc.)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # ---- API proxy ----
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-Id $request_id;

        # SSE: disable buffering, long read timeout (compute may stream
        # for minutes — see Q-P2)
        proxy_buffering off;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
        chunked_transfer_encoding on;
    }

    # ---- Tile serve (X-Accel-Redirect path) ----
    # uvicorn returns X-Accel-Redirect: /_tiles_internal/<sha>/lodN/<stem>.webp
    # nginx serves the file from the local cache disk without re-entering Python.
    location /_tiles_internal/ {
        internal;                                # only reachable via X-Accel-Redirect
        alias /var/cache/stand-alone-analyzer/thumbnails/;
        access_log off;
        add_header Cache-Control "public, max-age=86400";
        # webp content-type usually inferred; pin it for safety
        types { image/webp webp; }
        default_type image/webp;
    }

    # Health probe (cheap, no proxy)
    location = /healthz {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    client_max_body_size 16m;
}
```

### 2.2 Tile-serving path: nginx (via X-Accel-Redirect) vs uvicorn StreamingResponse — **decision: nginx**

The tile URL `/api/v1/projects/<id>/tiles/lod{0,1,2}/<stem>.webp` cannot
be served by nginx alone because the cache directory is keyed by
`sha256(absolute_analysis_folder)[:16]` (§C.5 of the reuse map) and the
authoritative source is `00_thumbnails/index.json["cache_dir"]` — Python
must read the manifest + index.json to resolve which `<sha>/` dir the
tile lives in, and to fall back to the in-folder layout when
`cache_dir` is absent (B6/D2 in requirements §2.4–2.5).

Two options:

**Option A — uvicorn returns `FileResponse`**: simple, all logic in
Python. Cost: every tile (~3,600 in a 60×60 mosaic) hits the uvicorn
worker, holds the asyncio loop while reading from the local SSD, and
sends bytes through Python. With OpenSeadragon's typical 8 concurrent
fetches (NFR §2.1), every pan/zoom step queues 8 disk-read tasks on
the single uvicorn process. Workable but contends with SSE / compute.

**Option B — uvicorn resolves path, returns `X-Accel-Redirect` header,
nginx sends the file**: Python does ~1ms of path resolution per tile;
nginx sendfile()s the WebP off the SSD with zero data copy through the
Python process. Auth/permission checks still happen in Python (the
project_id → cache_dir resolution acts as the access check). This is
the standard nginx + Python pattern for static file serving with
dynamic resolution.

**Decision: Option B (X-Accel-Redirect).** Reasoning:

1. Tile traffic dominates the byte budget (60×60 lod1 ≈ 32 MB in a
   single Explorer cold-open, lod2 ≈ 200 MB on zoom-in). Keeping it
   off the Python loop directly serves the Explorer NFRs (≤150ms pan
   latency, ≤8 concurrent fetches without SMB read storms).
2. The future auth hook (N4) still gates: nginx only accepts
   `X-Accel-Redirect` from the proxied uvicorn response, so unauth'd
   clients can't directly fetch `/_tiles_internal/...` (it's marked
   `internal`).
3. Implementation cost: ~10 lines in the FastAPI tile endpoint. Same
   Python code path resolves cache_dir; only the response object
   differs.

**Trade-off accepted:** tile responses now have an nginx-level
Cache-Control header that the backend no longer fully controls.
Acceptable because tiles are content-addressed by `<sha>/<lod>/<stem>`
and never mutate without a recompute (which changes the sha key).

### 2.3 Cache-Control summary

| Asset class | URL pattern | Header | Rationale |
|---|---|---|---|
| Hashed JS/CSS | `/assets/*` | `public, max-age=31536000, immutable` | Vite hashes filenames; safe to cache forever |
| HTML shell | `/index.html`, `/` | `no-store, must-revalidate` | New deploy must be visible immediately |
| API JSON | `/api/v1/*` | `no-store` (set by FastAPI) | Manifest changes during compute |
| SSE stream | `/api/v1/run/*` | `no-cache, no-transform` | Buffering would defeat SSE |
| Tiles | `/_tiles_internal/*` | `public, max-age=86400` | Content-addressed; safe to cache |

---

## 3. SMB mount expectations

Two mounts. `analysis_folder/` is rw (the app writes manifest.json,
parquets, json artifacts). `raw_images/` is ro (we never write raw
microscope images from this app).

### 3.1 /etc/fstab

```fstab
# /etc/fstab — SMB mounts for stand-alone-analyzer
//fileserver.example/analysis  /mnt/analysis  cifs  \
    credentials=/etc/cifs-credentials-saa,uid=saa,gid=saa,\
    file_mode=0664,dir_mode=0775,vers=3.0,\
    cache=loose,actimeo=30,rsize=1048576,wsize=1048576,\
    noserverino,nofail,_netdev,x-systemd.automount,x-systemd.idle-timeout=600    0 0

//fileserver.example/raw       /mnt/raw_images cifs  \
    credentials=/etc/cifs-credentials-saa,uid=saa,gid=saa,ro,\
    file_mode=0444,dir_mode=0555,vers=3.0,\
    cache=strict,actimeo=120,rsize=1048576,\
    noserverino,nofail,_netdev,x-systemd.automount    0 0
```

Key parameters:
- `vers=3.0` — SMB3, encryption + better reconnect than 2.x. Pin the
  version explicitly; `vers=default` has bitten people on Windows
  Server upgrades.
- `cache=loose` for analysis (we own the writes; one-writer is fine
  for v1 single-user). `cache=strict` for raw_images (read-only, safe
  to aggressively cache).
- `actimeo=30` for analysis — short attribute timeout so Compute step
  completion is visible quickly. `actimeo=120` for raw_images (rarely
  changes).
- `rsize=1048576, wsize=1048576` — 1 MiB I/O units. Raw PNGs are
  ~1–2 MB each; this halves syscall round-trips.
- `noserverino` — avoid inode collision issues seen on some Windows
  shares; cheap insurance.
- `nofail` + `x-systemd.automount` — host boots even when the file
  server is down; mount happens lazily on first access.
- `_netdev` — wait for network before attempting mount.
- `credentials=...` — file mode 0600, owned by root. Never inline
  password in fstab.

### 3.2 SMB outage behavior

- **Mount unreachable on boot**: `nofail` + `x-systemd.automount` →
  systemd skips, app starts. First filesystem access blocks until
  mount succeeds or kernel timeout (~60s default for cifs). The
  health endpoint (§9) probes `os.path.ismount('/mnt/analysis')` and
  reports `smb_reachable: false` so the FE can show a banner instead
  of timing out on every API call.
- **Mid-session outage**: cifs blocks I/O up to its retry budget,
  then returns ESTALE / EIO. FastAPI endpoints catch `OSError` and
  return HTTP 503 with `{error: "smb_unreachable"}`. Frontend retries
  on user action (we do not auto-retry — the user's compute might be
  half-written).
- **Outage during a compute step**: the in-process pipeline raises
  `OSError`; the SSE stream emits an `error` event and the manifest
  is left untouched (R1 invariant). User restarts the step after SMB
  comes back.

### 3.3 Performance tuning

| Knob | Value | Effect |
|---|---|---|
| `rsize`, `wsize` | 1 MiB | Larger reads → fewer round-trips; matches typical raw PNG size |
| `cache=loose` (rw) | Loose | Aggressive client cache; safe under single-writer |
| `cache=strict` (ro) | Strict | Read-only mount; cache as much as possible |
| `actimeo` | 30s rw / 120s ro | Trade staleness for fewer GETATTR roundtrips |
| `oplocks` (default on) | Leave on | SMB3 leases reduce metadata chatter |
| Kernel `dirty_writeback_centisecs` | default | Don't tune; cifs has its own writeback |

The local-disk thumbnail cache (§4) is the real performance solution;
SMB tuning above is to keep the manifest read/write path tolerable
(<200ms per call), not to make tiles fast off SMB.

---

## 4. Local-disk cache

The existing redirect (`core/pipeline/thumbnails.py:104-116`) writes
WebP tiles to `~/.cache/stand-alone-analyzer/thumbnails/<sha>/lod{N}/`
when the analysis folder is on SMB or when
`STAND_ALONE_THUMB_LOCAL_CACHE=1`. Q-S4 keeps this layout for v1.

### 4.1 HOME under a service account

Per-user `~/.cache` is fine for an interactive scientist on a laptop
but problematic for a uvicorn process running as a service account.
Linux services often have `HOME=/` (root) or unset; `os.path.expanduser`
then resolves to `/.cache` which is unwritable.

**Convention**: define `HOME` explicitly in the systemd unit so the
existing code paths resolve as expected:

```ini
# saa-backend.service (excerpt)
[Service]
User=saa
Group=saa
Environment=HOME=/var/lib/stand-alone-analyzer
```

`/var/lib/stand-alone-analyzer/.cache/stand-alone-analyzer/thumbnails/<sha>/`
is the resulting path. nginx's `alias` in §2.1 must match the same
directory — pick one canonical path:

```
CACHE_ROOT = /var/cache/stand-alone-analyzer/thumbnails/   # symlink or bind-mount
            ↔ /var/lib/stand-alone-analyzer/.cache/stand-alone-analyzer/thumbnails/
```

A symlink at `/var/cache/stand-alone-analyzer` →
`/var/lib/stand-alone-analyzer/.cache/stand-alone-analyzer` keeps both
pointers consistent. Alternatively, override with the env var
`XDG_CACHE_HOME=/var/cache/stand-alone-analyzer` (Python's
`platformdirs` respects it, but the current code uses
`Path.home() / ".cache"` directly — `[NEEDS-BE]` confirm whether to
add XDG_CACHE_HOME support to the cache redirect helper).

### 4.2 Capacity sizing

Per the LOD pyramid + Q-P1 (60×60 = 3,600 tiles):

| LOD | Pixel size | Per-tile WebP (q=80) | 3,600 tiles |
|---|---|---|---|
| lod0 | 64×40 | ~1 KB | ~3.5 MB |
| lod1 | 192×120 | ~7 KB | ~25 MB |
| lod2 | 480×300 | ~30 KB | ~110 MB |
| **Total per project** | | | **~140 MB** |

(Raw ~1920×1200 is **not** cached locally — served via fallback to
SMB on rare zoom-to-raw events. Confirmed by reading
`core/pipeline/thumbnails.py:57-61` LOD_SIZES.)

For 50 projects on disk: ~7 GB. For 200: ~28 GB. A 100 GB cache
partition (or root partition with that headroom) covers any realistic
v1 caseload comfortably.

### 4.3 Eviction policy v1: **none**

We do not implement LRU, TTL, or quota eviction in v1. Rationale:

- One scientist, ≤200 projects historically, ~28 GB ceiling — well
  inside any reasonable disk budget.
- The `Clear cache` button (US-C4 AC) is the manual eviction path
  per project.
- Implementing eviction wrong corrupts state more often than it helps.

Document the limitation: when the cache disk fills, thumbnails step
fails with ENOSPC and the user must `Clear cache` on stale projects
or manually `rm -rf /var/cache/stand-alone-analyzer/thumbnails/<sha>/`.

Post-v1: per-project LRU with size cap (env var
`SAA_CACHE_MAX_BYTES`). Out of scope for now.

---

## 5. Process supervision

### 5.1 systemd unit (recommended for v1)

```ini
# /etc/systemd/system/saa-backend.service
[Unit]
Description=Stand-Alone Analyzer FastAPI backend
After=network-online.target mnt-analysis.automount mnt-raw_images.automount
Wants=network-online.target

[Service]
Type=exec
User=saa
Group=saa
WorkingDirectory=/opt/stand-alone-analyzer
Environment=HOME=/var/lib/stand-alone-analyzer
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SAA_LOG_LEVEL=info
Environment=SAA_LOG_FORMAT=json
Environment=SAA_BIND_HOST=127.0.0.1
Environment=SAA_BIND_PORT=8000
Environment=STAND_ALONE_THUMB_LOCAL_CACHE=1
EnvironmentFile=-/etc/stand-alone-analyzer/backend.env

ExecStart=/opt/stand-alone-analyzer/venv/bin/uvicorn \
    flake_analysis.api.main:app \
    --host ${SAA_BIND_HOST} \
    --port ${SAA_BIND_PORT} \
    --workers 1 \
    --timeout-graceful-shutdown 30 \
    --log-config /etc/stand-alone-analyzer/log-config.json

# Lifecycle
Restart=on-failure
RestartSec=5s
TimeoutStopSec=45s              # > graceful shutdown so SSE drains
KillSignal=SIGTERM
KillMode=mixed                  # SIGTERM main, SIGKILL stragglers

# Hardening (best-effort; some can be relaxed if SMB needs more)
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/mnt/analysis /var/cache/stand-alone-analyzer /var/lib/stand-alone-analyzer /var/log/stand-alone-analyzer
ReadOnlyPaths=/mnt/raw_images
PrivateTmp=true
ProtectHome=true                # we set HOME explicitly above; still hide /home
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=saa-backend

[Install]
WantedBy=multi-user.target
```

Key choices:
- `--workers 1`: v1 is single-user with sync compute; multiple workers
  would split the in-process clustering/explorer state across
  processes and serialize manifest writes. Scale-out is post-v1
  (§11).
- `TimeoutStopSec=45 > timeout-graceful-shutdown=30`: gives uvicorn
  the full 30s to drain SSE streams before systemd escalates to
  SIGKILL.
- `KillMode=mixed`: SIGTERM to the main pid, SIGKILL to leftover
  children if they overrun. Addresses the user's pain point that
  Ctrl+C left Streamlit caches alive — uvicorn + cgroup kill ensures
  the whole process tree dies with the unit.

### 5.2 Docker Compose alternative

```yaml
# docker-compose.yml (alternative; not chosen for v1)
services:
  backend:
    image: stand-alone-analyzer/backend:0.3.0
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    user: "1000:1000"
    environment:
      HOME: /home/saa
      STAND_ALONE_THUMB_LOCAL_CACHE: "1"
      SAA_LOG_FORMAT: json
    volumes:
      - /mnt/analysis:/mnt/analysis:rw         # SMB host-mount, bind into container
      - /mnt/raw_images:/mnt/raw_images:ro
      - saa-cache:/home/saa/.cache/stand-alone-analyzer
    stop_grace_period: 45s
    stop_signal: SIGTERM
    logging:
      driver: json-file
      options: { max-size: "50m", max-file: "5" }

  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports: ["80:80"]
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./web/dist:/usr/share/nginx/html:ro
      - saa-cache:/var/cache/stand-alone-analyzer/thumbnails:ro
    depends_on: [backend]

volumes:
  saa-cache:
```

**Recommendation: systemd for v1.** Reasons: (1) BNL/CFN hosts run
systemd natively; no Docker daemon to install or maintain. (2) SMB
mounts are a host concern — bind-mounting them into a container is
extra layering with no benefit at one user. (3) journald gives free
log rotation + structured query (`journalctl -u saa-backend -o json
--since=...`). (4) easier rollback via `systemctl revert` + the unit
file in version control.

Docker compose stays as a documented option for deployment to
container-only hosts (post-v1).

### 5.3 Lifecycle: graceful shutdown verification

The user's headline pain point: `Ctrl+C` on Streamlit left numpy
mosaic caches alive (R5 in requirements). For uvicorn:

1. systemd sends `SIGTERM` to the main pid.
2. uvicorn sets `should_exit = True` and stops accepting new
   connections. Existing connections drain.
3. SSE streams in flight: the FastAPI handler must check
   `request.is_disconnected()` in its event loop and exit cleanly
   when the SIGTERM-induced disconnect propagates. `[NEEDS-BE]` to
   confirm the SSE handler does this.
4. uvicorn exits at the latest after 30s
   (`--timeout-graceful-shutdown 30`).
5. cgroup-level `KillMode=mixed` reaps any straggler child threads.

**Verification procedure** (post-deploy smoke test):
```bash
# 1. Note RSS before
ps -o rss,cmd -p $(systemctl show -p MainPID --value saa-backend)

# 2. Start a 60×60 mosaic load via the Explorer; let it cache tiles

# 3. Stop the unit
sudo systemctl stop saa-backend

# 4. Confirm no leftover children
ps -ef | grep -E 'uvicorn|flake_analysis' | grep -v grep
# (should be empty)

# 5. Confirm cache files are still on disk (regenerable, not a leak)
ls /var/cache/stand-alone-analyzer/thumbnails/
```

### 5.4 Logs

- systemd → journald → JSON-line stdout from uvicorn.
  `journalctl -u saa-backend -f` for tail; `journalctl -u saa-backend
  --since="1 hour ago" -o json | jq` for structured query.
- Rotation: journald handles it (`SystemMaxUse=2G` in
  `/etc/systemd/journald.conf` is typical).
- For Docker, `json-file` driver with `max-size=50m, max-file=5` →
  250 MB cap, similar effect.
- All log lines are JSON with at least: `ts, level, logger, msg,
  request_id, project_id, span` (per N7 + O2 in NFRs).

---

## 6. CORS

Decision per §4.99 Q-S1: backend may be on a different host from the
frontend; CORS support is required.

### 6.1 Same-origin (single-host nginx)

When nginx serves both `/` (SPA) and `/api/*` from the same origin,
**no CORS headers are needed**. The browser treats the API call as
same-origin. This is the v1 default deploy.

### 6.2 Split-host (FE on a different origin)

When the React build is hosted on, say, `https://saa.cfn.bnl.gov/`
but the API is `https://saa-api.cfn.bnl.gov/`, FastAPI's
`CORSMiddleware` is configured with:

```python
# illustrative — actual config lives in BE spec
CORSMiddleware(
    app,
    allow_origins=os.environ["SAA_ALLOWED_ORIGINS"].split(","),
    allow_credentials=True,            # for future SSO cookie
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
    max_age=600,
)
```

Operational rules:
- `SAA_ALLOWED_ORIGINS` is an explicit list; **never `*`**, especially
  not with `allow_credentials=True` (browsers reject the combo). v1
  example: `SAA_ALLOWED_ORIGINS=https://saa.cfn.bnl.gov`.
- Pre-flight `OPTIONS /api/v1/...` is handled by `CORSMiddleware`
  automatically; nginx must not strip the `Access-Control-*` request
  headers (default config does not, mentioned for completeness).
- SSE endpoints inherit CORS — `EventSource` does not send Origin in
  some older browsers; verify against §2.6 A1 supported browsers.

---

## 7. TLS / auth posture v1

### 7.1 TLS

v1 lives on internal networks (BNL/CFN). Plain HTTP is acceptable for
launch. If TLS is desired:

- **Public-DNS hosts**: Let's Encrypt via `certbot --nginx`.
  Auto-renewal cron is standard.
- **Internal-only hosts** without public DNS: institutional CA
  (BNL ITD provides certs), or self-signed with internal trust
  roots distributed to client laptops.
- nginx terminates TLS; uvicorn always speaks HTTP on `127.0.0.1:8000`.
  No TLS cert ever loaded into Python. Simpler to rotate.

### 7.2 Auth

v1 auth hook is a backend stub (N4 in requirements). All requests are
treated as identity `local`. Infra implication: **none for v1** beyond
"don't expose the backend port to the internet."

**Where SSO plugs in post-v1**: the standard play is
[`oauth2-proxy`](https://oauth2-proxy.github.io/oauth2-proxy/) as a
sidecar in front of nginx (or as an nginx `auth_request` upstream).
Topology:

```
Browser → oauth2-proxy :4180 (validates session cookie / OIDC)
           │  (on success: sets X-Forwarded-User header)
           ▼
         nginx :80 (now sees authenticated user)
           │
           ▼
         uvicorn :8000 (reads X-Forwarded-User → identity)
```

- nginx config gains `auth_request /oauth2/auth;` on `/api/`.
- FastAPI reads `X-Forwarded-User` (or `Authorization: Bearer ...`
  for service tokens) and replaces the `local` stub with the real
  identity. Handler signatures unchanged because of N4.

This is documented for post-v1; **no code changes required for v1
launch**. The point is that the v1 infra (`nginx → uvicorn`) does
not block adding `oauth2-proxy → nginx → uvicorn` later.

---

## 8. Multi-environment

### 8.1 Dev (developer laptop)

Split: Vite dev server on `:5173` (React HMR), uvicorn `--reload`
on `:8000`. Vite proxies `/api/*` to `127.0.0.1:8000` so the FE code
uses the same `/api/v1/...` URLs as prod. CORS is therefore not
needed in dev either.

```bash
# terminal 1
cd web && npm run dev          # Vite :5173
# terminal 2
SAA_LOG_LEVEL=debug uvicorn flake_analysis.api.main:app --reload --port 8000
```

### 8.2 Staging / prod

Single nginx host as designed. No staging environment is required for
v1 (single user, atomic cutover). When a staging host is added
post-v1, it's the same systemd unit pointed at a non-prod SMB share.

### 8.3 Backend env-var convention

All config goes through environment variables (12-factor; survives
container migration; no on-disk config to drift). Prefix `SAA_`:

| Var | Default | Purpose |
|---|---|---|
| `SAA_BIND_HOST` | `127.0.0.1` | uvicorn listen address |
| `SAA_BIND_PORT` | `8000` | uvicorn port |
| `SAA_LOG_LEVEL` | `info` | `debug`/`info`/`warning`/`error` |
| `SAA_LOG_FORMAT` | `json` | `json` or `text` |
| `SAA_ALLOWED_ORIGINS` | `` (empty) | CSV list for CORS; empty = same-origin only |
| `SAA_ANALYSIS_ROOTS` | `/mnt/analysis` | CSV allow-list of dir prefixes the user may pick (path traversal guard, N8) |
| `SAA_RAW_ROOTS` | `/mnt/raw_images` | CSV allow-list for raw image paths |
| `SAA_CACHE_DIR` | derived from `HOME` | Override the cache root explicitly `[NEEDS-BE]` |
| `SAA_VERSION` | derived from package | shown in startup banner + health endpoint |
| `STAND_ALONE_THUMB_LOCAL_CACHE` | `1` (in unit) | Existing var; opts into local-disk redirect |
| `HOME` | `/var/lib/stand-alone-analyzer` | Anchors `~/.cache/...` resolution |
| `PYTHONUNBUFFERED` | `1` | Real-time stdout for journald |

`SAA_ANALYSIS_ROOTS` and `SAA_RAW_ROOTS` are critical: they prevent
the user (or future attacker) from asking the backend to read
`/etc/passwd` by typing it as `analysis_folder`. The validator at
the backend rejects any path not under one of these prefixes (this is
infra config; the validation logic is BE scope).

---

## 9. Observability

### 9.1 Logs

- Format: JSON line, one record per log call. Fields: `ts, level,
  logger, msg, request_id, project_id` (project_id present when in a
  request scope), `step` (when in a pipeline run), `error.type` /
  `error.stack` on exceptions.
- Destination: stdout (uvicorn) → systemd → journald. No file
  handlers; journald handles rotation (`SystemMaxUse`).
- For Docker deployments: same stdout, captured by Docker's `json-file`
  driver (250 MB cap configured in §5.2).
- The BE log config file (`/etc/stand-alone-analyzer/log-config.json`)
  is referenced from the systemd unit but its content is BE scope.

### 9.2 Metrics

**v1: skip Prometheus.** One user, sync compute, no SLA — operational
monitoring via journald + the health endpoint is enough. Document
where Prometheus would plug in:

- FastAPI middleware (e.g.
  `prometheus-fastapi-instrumentator`) exposes `/metrics` on the same
  uvicorn port.
- nginx adds `location = /metrics { allow 10.0.0.0/8; deny all;
  proxy_pass http://127.0.0.1:8000/metrics; }`.
- Scrape target added to the institutional Prometheus.

Estimated effort: half a day. Defer until there's a reason.

### 9.3 Health endpoint

`GET /api/v1/health` → 200 always (so liveness probes never flap on
SMB outage), with body:

```json
{
  "ok": true,
  "version": "0.3.0",
  "git_commit": "abc1234",
  "manifest_path_writable": true,
  "smb_reachable": true,
  "cache_dir": "/var/cache/stand-alone-analyzer/thumbnails",
  "cache_dir_writable": true,
  "uptime_seconds": 12345
}
```

`ok` reflects "process alive" only. The SMB / writable flags let the
FE distinguish "backend up but storage broken" from "all good"
without the user having to interpret a 503 on the next call. nginx's
`/healthz` (§2.1) is the cheap probe for load balancer / monitoring
liveness; `/api/v1/health` is the deeper one.

---

## 10. Backups & data safety

### 10.1 What we own

Writable artifacts under `analysis_folder/`:
- `manifest.json`
- `00_thumbnails/index.json`
- `01_background/background.npy`
- `02_domain_stats/*.parquet`, `*.npz`
- `03_selector/*.parquet`
- `04_clustering/labels.json`, `assignments.parquet`, `gmm_model.pkl`,
  `seed_groups.json`
- `05_domain_proximity/*.parquet`
- `06_explorer/explorer_state.json`, `selected_flakes.parquet`

These all live on the **SMB share**. Backups are the SMB owner's
responsibility (institutional NAS snapshots). The application takes
no part in them.

### 10.2 What is regenerable

- `/var/cache/stand-alone-analyzer/thumbnails/` — fully regenerable
  by re-running the thumbnails step. No backup.
- `/var/log/...` (if any) — operational only.
- The React build artifacts under `/usr/share/...` — produced by CI
  from version-controlled source. No backup.

### 10.3 Migration cutover plan (Streamlit → v1)

Atomic per Q-C1; no parallel run.

1. **T-1 day:** deploy v1 backend + frontend to the host but disable
   the systemd unit (`systemctl disable --now saa-backend`). Verify
   `nginx -t` and that the SPA loads at `/` (it'll show "backend
   unreachable" — expected).
2. **T-0:** stop the running Streamlit process
   (`pkill -f streamlit run app/streamlit_app.py` or whatever the
   current launch mechanism is). Confirm port released.
3. `systemctl enable --now saa-backend`. Tail
   `journalctl -u saa-backend -f` until startup banner with version
   appears.
4. `curl -s http://localhost/api/v1/health | jq` — confirm
   `ok=true, smb_reachable=true, manifest_path_writable=true`.
5. Open the browser. Pick the most recently used `analysis_folder/`.
   Confirm: manifest loads, all 7 step statuses display, Explorer
   tab opens a 60×60 mosaic in <2s, panning is smooth (§2.1 NFRs).
6. **Rollback (if needed):** `systemctl stop saa-backend`,
   restart the Streamlit process. The Streamlit code is still on the
   host (only the unit is disabled). v0.2.18 reads the same
   `analysis_folder/` because B1–B5 require it.
7. **T+7 days, post-success:** delete `app/streamlit_app.py` and
   `src/flake_analysis/ui/` from the next release per Q-C1.

The cutover never modifies any artifact under `analysis_folder/`, so
rollback is just a process swap.

---

## 11. Cost / capacity

### 11.1 v1 sizing

Single VM, modest:

| Resource | Spec | Notes |
|---|---|---|
| vCPU | 4 | clustering is sklearn-bound, thumbnails uses parallelism |
| RAM | 8 GB | Server-side budget §2.1 = 500 MB per session; OS + kernel SMB cache eats a few GB; headroom for one Compute step |
| Local SSD | 100 GB | Cache (~28 GB ceiling §4.2) + OS + logs + room |
| Network | 1 Gbps to SMB | Raw image read storms cap at SMB throughput |

A single small VM (or a developer-class box) is enough for one user.
There is no autoscaling and no need for any.

### 11.2 Scale-out triggers

Move off the single-VM model when:

- **>1 concurrent user**: synchronous compute + single uvicorn
  worker means user B blocks while user A runs Compute. Trigger
  → introduce job queue (N6 in NFRs); celery + redis is the
  conventional add. Bump uvicorn workers and gate by
  `--workers N` once endpoints are stateless.
- **Mosaic >100×100 (>10k tiles)**: per Q-P1 the budgets break.
  Move to a real DZI / TileLayer pyramid (OpenSeadragon supports
  it) and reconsider whether tiles should be served by a CDN.
- **Cache disk >70% full**: implement eviction (§4.3) or grow the
  partition.
- **SSE timeouts >5 min routinely**: long compute steps need a true
  job-handle + polling (N6).

None of these apply to v1.

---

## 12. Open questions

| Tag | Question | Owner |
|---|---|---|
| `[NEEDS-BE]` | Does the SSE handler check `request.is_disconnected()` so SIGTERM during a Compute step terminates cleanly? (Required for §5.3 verification.) | Backend Architect |
| `[NEEDS-BE]` | Should we add `XDG_CACHE_HOME` support to `core/pipeline/thumbnails.py:104-116` so `SAA_CACHE_DIR` can override the cache location without HOME tricks? Cleaner than the symlink in §4.1. | Backend Architect |
| `[NEEDS-BE]` | Confirm tile endpoint can return `X-Accel-Redirect` and that auth/permission checks happen before the redirect header is set (§2.2 Option B). | Backend Architect |
| `[NEEDS-BE]` | Does the manifest read at startup tolerate a brand-new analysis folder with no `manifest.json` yet, so the health endpoint's `manifest_path_writable` probe does not 500? | Backend Architect |
| `[NEEDS-FE]` | When `smb_reachable=false` on `/api/v1/health`, what banner / state does the SPA show? Affects whether health polling should be aggressive (5s) or lazy (60s). | Frontend Architect |
| `[NEEDS-FE]` | Does the FE production build need any runtime config injection (`/api/v1/config` call before app boot), or are all settings baked in at build time? Affects whether nginx needs a config endpoint. | Frontend Architect |
| `[NEEDS-FE]` | What's the SPA's behavior on SSE disconnect (e.g., during a uvicorn restart)? Auto-reconnect with `EventSource`'s built-in retry, or explicit user retry? Determines `proxy_read_timeout` value. | Frontend Architect |
| `[NEEDS-MV]` | Does OpenSeadragon's "image = tile" mode (no DZI pyramid, per Q-P1) tolerate the per-LOD URL structure `/tiles/lod{0,1,2}/<stem>.webp`, or does it need a DZI XML descriptor at any LOD? | Mosaic / Visualization owner |
| `[NEEDS-MV]` | Tile concurrency: NFR §2.1 caps at 8 in flight; does OSD's default `imageLoaderLimit` honor that, or must we pass it explicitly? Affects nginx `worker_connections` sizing if higher. | Mosaic / Visualization owner |

---

*End of deployment design.*
