# Cutover + Streamlit Deletion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Atomically delete the Streamlit UI, wire the React + FastAPI deploy artifacts, and ship v0.3.0 as the cutover release.

**Architecture:** Delete-only feature plan. nginx serves the React SPA + reverse-proxies FastAPI per deployment-design.md §2; uvicorn runs under systemd; tile-serving uses X-Accel-Redirect; no compatibility layer for the old Streamlit app.

**Tech Stack:** nginx, systemd, FastAPI 0.110+, React 18.3 build artifacts, pyproject 0.3.0, pytest, vitest

---

## Pinned decisions resolved

These twelve decisions were pinned in `/tmp/plan5-cutover-brief.md` and govern every task below. Each decision is mapped to its enforcing task(s); no later task may walk one back.

1. **Streamlit dependency removal: full removal in this plan.** `streamlit>=1.32` is dropped from `pyproject.toml` in Task 18. No transitional dual-stack — see decision #5.
2. **Plotly Python dependency removal: full removal in this plan.** `plotly>=5.18` is dropped from `pyproject.toml` in Task 18. The frontend's `react-plotly.js` bundles Plotly.js client-side; no remaining Python consumer once `src/flake_analysis/ui/` is gone (Task 14). → enforced by Task 18 + the Phase 7 guard test (Task 25).
3. **`httpx` is kept.** Used by `tests/api/conftest.py` ASGI `httpx.AsyncClient` (added in Plan 1). → no task removes it from `pyproject.toml`.
4. **Test deletion safety.** Each Streamlit-targeted test file (`tests/test_brushing.py`, `tests/test_image_preview.py`, `tests/test_explorer_mosaic_helpers.py`) is deleted only after a `grep` verification step proves no other test imports a symbol from it. → enforced by Tasks 15, 16. The selector parity test that imports `flake_analysis.ui.tab_selector._values_for_axis` is migrated to a pure-core helper inline in Task 13 before Task 14 deletes the `ui/` tree.
5. **Atomic cutover style: single PR, one merge.** No behind-flag staged rollout, no dual-stack maintenance window. After this plan ships, `import flake_analysis.ui` raises `ModuleNotFoundError`. → enforced by Tasks 14, 15, 16.
6. **Version bump 0.2.18 → 0.3.0** in a single commit (`chore(cutover): bump version 0.2.18 → 0.3.0`). → enforced by Task 19. Both `pyproject.toml` `[project].version` and `src/flake_analysis/__init__.py` `__version__` change in the same commit.
7. **Deploy artifact locations.** All new files live under a top-level `deploy/` directory: `deploy/nginx/stand-alone-analyzer.conf`, `deploy/systemd/saa-api.service`, `deploy/scripts/deploy.sh`. → enforced by Tasks 4, 5, 6.
8. **Rollback strategy: git-revert the cutover PR.** No on-host parallel-run procedure (deployment-design §10.3 step 6 covers the Streamlit-still-on-host fallback during the cutover window itself; once the PR merges and Streamlit code is deleted, rollback = `git revert`). → no task implements a parallel-run scaffold.
9. **CI / hooks unchanged.** Plan 5 does NOT introduce new GitHub Actions, pre-commit hooks, or lint hooks. If any exist, they stay; if absent, they remain absent. → no task touches `.github/`, `.pre-commit-config.yaml`, or `pyproject.toml [tool.ruff]`.
10. **README rewrite scope: minimal.** Top-of-file "How to run" gets replaced with a React + FastAPI runbook (npm install + npm run build + uvicorn). No marketing rewrite, no feature-list reordering, no historical-context purge. → enforced by Task 20.
11. **Smoke test scope: manual checklist only.** End-to-end Playwright is deferred to a future plan. Backend smoke = `pytest tests/api -v`. Frontend smoke = `npx vitest run`. → enforced by Tasks 26, 27, 28 (manual checklist + automated suites only — no new browser-driver dependency).
12. **Streamlit reference grep is a guard test.** `tests/test_no_streamlit.py` greps the source tree at test-collection time and fails if ANY `import streamlit`, `from streamlit`, or `streamlit.` token is present in `src/`, `tests/`, or `app/`. → enforced by Task 24.

---

## File Structure

### Backend (modifications + new)

- Modify: `src/flake_analysis/api/routes/static.py` — convert `GET /projects/{pid}/static/thumbnails/lod{lod}/{stem}.webp` from `FileResponse` to a `Response` with `X-Accel-Redirect` header pointing at `/_tiles_internal/<sha>/lod{N}/<stem>.webp` per deployment-design §2.2 Option B (Phase 3, Task 7). The route still resolves `cache_dir` from `00_thumbnails/index.json` and falls back to the in-folder layout when `cache_dir` is absent. Existing `Cache-Control` + `ETag` headers are preserved for the redirect response.
- Modify: `src/flake_analysis/__init__.py` — bump `__version__` from `0.2.18` to `0.3.0` (Task 19).
- Modify: `pyproject.toml` — drop `streamlit>=1.32` and `plotly>=5.18`; bump `version = "0.3.0"`; rewrite `description` from "Streamlit app for…" to "React + FastAPI app for…" (Tasks 18, 19).

### Backend (deletions)

- Delete: `app/streamlit_app.py` (Task 12)
- Delete: `src/flake_analysis/ui/` (entire directory: `__init__.py`, `sidebar.py`, `_brushing.py`, `_image_preview.py`, `tab_compute.py`, `tab_selector.py`, `tab_clustering.py`, `tab_explorer.py`) (Task 14)
- Delete: `tests/test_brushing.py` (Task 15)
- Delete: `tests/test_image_preview.py` (Task 15)
- Delete: `tests/test_explorer_mosaic_helpers.py` (Task 16)
- Modify: `tests/test_imports.py` — remove the `import flake_analysis.ui` line; update the version assertion to `0.3.0` (Task 17).
- Modify: `tests/test_pipeline_selector.py` — replace `from flake_analysis.ui.tab_selector import _values_for_axis` with an inline pure-core implementation (Task 13).
- Modify: `tests/test_selector_filter_persistence.py` — delete the entire file; it boots Streamlit's `AppTest` and is meaningless after the UI is gone (Task 16).

### Deploy artifacts (all new)

- Create: `deploy/nginx/stand-alone-analyzer.conf` (Task 4) — verbatim port of deployment-design §2.1.
- Create: `deploy/systemd/saa-api.service` (Task 5) — `User=<EDIT-ME>` placeholder, `Restart=on-failure`, `ExecStart=/opt/saa/.venv/bin/uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000`.
- Create: `deploy/scripts/deploy.sh` (Task 6) — atomic symlink-rotation deploy per deployment-design §5.
- Create: `tests/test_nginx_config_syntax.py` (Task 4) — invokes `nginx -t -c <conf>` if `command -v nginx` succeeds, else `pytest.skip`.

### Documentation

- Modify: `README.md` — replace "Quick start" Streamlit-launch block with a React + FastAPI runbook (Task 20).
- Create: `docs/operations/runbook.md` — nginx restart, systemctl commands, journalctl tails, log paths (Task 21).
- Modify: `CONTRIBUTING.md` — drop "Streamlit ≥1.32, Plotly ≥5.18" line; drop the "Streamlit smoke tests" bullet; update the dev-loop instructions to point at `npm run dev` + `uvicorn --reload` (Task 22).

### Tests (new)

- `tests/test_no_streamlit.py` (Task 24) — guards against any future Streamlit re-introduction via grep.
- `tests/test_pyproject_clean.py` (Task 25) — asserts `streamlit` and `plotly` absent from `pyproject.toml`.
- `tests/test_xaccel_thumbnails.py` (Task 7) — verifies the converted thumbnail route emits `X-Accel-Redirect` and an empty body.
- `tests/test_nginx_config_syntax.py` (Task 4) — see deploy artifacts above.

---

## Spec coverage check (deployment-design.md ↔ Phase)

| deployment-design section | Plan 5 phase / task |
|---|---|
| §1 Topology | Phases 2–3 (deploy artifacts + X-Accel-Redirect wire-up) |
| §2.1 nginx routes | Phase 2 Task 4 (verbatim port) |
| §2.2 X-Accel-Redirect tile path | Phase 3 Tasks 7, 8 (route conversion + test) |
| §2.3 Cache-Control summary | Phase 2 Task 4 (nginx config) + Phase 3 Task 7 (preserve existing headers) |
| §3 SMB mount expectations | Phase 6 Task 21 (runbook references; no app code change) |
| §4 Local-disk cache | Phase 3 Task 7 (cache_dir resolution) + Phase 6 Task 21 (runbook) |
| §5 Process supervision (systemd unit) | Phase 2 Task 5 |
| §5.1 systemd unit specifics (Restart=on-failure, KillMode=mixed, HOME, ExecStart) | Phase 2 Task 5 |
| §5.3 Lifecycle / graceful shutdown | Phase 6 Task 21 (runbook smoke procedure) |
| §6 CORS | (already wired in Plan 1) — no new task |
| §7 TLS / auth posture | Phase 6 Task 21 (runbook documents post-v1 plug-in) |
| §8 Multi-environment (dev) | Phase 6 Task 22 (CONTRIBUTING update) |
| §8.3 SAA_* env vars | Phase 2 Task 5 (systemd unit Environment= lines) |
| §9 Observability | Phase 6 Task 21 (runbook journalctl recipes) |
| §10 Backups & cutover plan | Phase 8 Task 26 (manual smoke checklist mirrors §10.3) |
| §11 Cost / capacity | (informational — no task) |

---

## Tasks (Grouped into Phases)

### Phase 1 — Pre-cutover validation

#### Task 1: Backend test gate

**Files:**
- Read-only: `tests/api/`

**Goal:** prove every backend test merged through Plans 1-4 is currently green on the cutover branch before any deletion happens.

- [ ] **Step 1: Run the backend API test suite**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/ -v`
Expected: every test PASSES; suite count matches the most recent green CI / local run from the Plan 4 merge. If even one test fails, STOP and resolve before proceeding — the deletion phase relies on a known-green baseline.

- [ ] **Step 2: Run the broader pytest suite (non-api tests)**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v --ignore=tests/api`
Expected: every test PASSES. Failures here mean a non-API test (parity, pipeline, state) regressed; resolve before proceeding.

- [ ] **Step 3: Record the baseline counts**

Note locally (do NOT commit) the test counts from steps 1 and 2. Phase 8 Task 27 will compare against these to confirm no regressions slipped in during cutover.

- [ ] **Step 4: No commit**

This is a verification task; nothing to stage.

---

#### Task 2: Frontend test gate

**Files:**
- Read-only: `web/src/`

**Goal:** prove every vitest test merged through Plans 1-4 is currently green.

- [ ] **Step 1: Run vitest**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx vitest run`
Expected: every test PASSES. If even one fails, STOP and resolve.

- [ ] **Step 2: Run the typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Record the baseline counts**

Note the test count from step 1 (number of files / number of tests). Phase 8 Task 28 will compare.

- [ ] **Step 4: No commit**

Verification only.

---

#### Task 3: Frontend production-build verification

**Files:**
- Read-only: `web/dist/` (output of `npm run build`)

**Goal:** prove the React production bundle builds cleanly on a fresh tree before any pyproject changes that might mask a build problem behind a dependency error.

- [ ] **Step 1: Build the production bundle**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npm run build`
Expected: build completes with exit 0; `web/dist/index.html`, `web/dist/assets/index-*.js`, and `web/dist/assets/index-*.css` exist on disk.

- [ ] **Step 2: Verify the dist tree shape**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/web/dist/`
Expected output contains at minimum: `index.html`, `assets/`. The `assets/` directory must contain at least one hashed `.js` file matching `index-*.js` and one hashed `.css` file matching `index-*.css`.

- [ ] **Step 3: Smoke-check the index.html references the hashed assets**

Run: `grep -E 'index-[A-Za-z0-9_-]+\.(js|css)' /Users/houkjang/projects/stand-alone-analyzer/web/dist/index.html`
Expected: at least one match per extension. If no match, the build did not produce hashed assets and nginx's `/assets/` immutable cache header (deployment-design §2.3) is unsafe; STOP and investigate.

- [ ] **Step 4: No commit**

The `web/dist/` artifact is gitignored (per `.gitignore: dist/`); leaving it on disk for Phase 8 reference is fine. Verification only.

---

### Phase 2 — Deploy artifacts (nginx + systemd + deploy script)

#### Task 4: nginx server config (verbatim port of deployment-design §2.1)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/deploy/nginx/stand-alone-analyzer.conf`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_nginx_config_syntax.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nginx_config_syntax.py
"""nginx config-syntax smoke test (Plan 5 Task 4).

Skips when the host has no `nginx` binary (CI / dev laptop case).
When `nginx` is available, runs `nginx -t -c <abs-conf-path>` and
asserts exit 0. The test does NOT load the served files; it only
proves the config parses.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "deploy" / "nginx" / "stand-alone-analyzer.conf"


def test_nginx_conf_file_exists():
    assert NGINX_CONF.exists(), f"missing nginx config at {NGINX_CONF}"
    assert NGINX_CONF.stat().st_size > 0, "nginx config is empty"


def test_nginx_conf_contains_required_locations():
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "location /assets/" in text
    assert "location = /index.html" in text
    assert "location / {" in text
    assert "location /api/" in text
    assert "location /_tiles_internal/" in text
    assert "location = /healthz" in text


def test_nginx_conf_pins_sse_proxy_settings():
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "proxy_buffering off" in text
    assert "proxy_read_timeout 1h" in text
    assert "proxy_send_timeout 1h" in text


def test_nginx_conf_marks_internal_tile_path():
    text = NGINX_CONF.read_text(encoding="utf-8")
    # `internal;` directive must appear inside /_tiles_internal/ block
    block_start = text.index("location /_tiles_internal/")
    block_end = text.index("}", block_start)
    block = text[block_start:block_end]
    assert "internal;" in block


def test_nginx_t_passes_when_nginx_available():
    if shutil.which("nginx") is None:
        pytest.skip("nginx binary not available on this host")
    # Run nginx -t against the bare server-block file. nginx requires a
    # full config (events {} + http {} wrapper), so we synthesize one
    # in a tmp file that includes our server block.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as wrapper:
        wrapper.write(
            "events { worker_connections 1024; }\n"
            "http {\n"
            f"  include {NGINX_CONF};\n"
            "}\n"
        )
        wrapper_path = wrapper.name
    try:
        result = subprocess.run(
            ["nginx", "-t", "-c", wrapper_path],
            capture_output=True,
            text=True,
        )
        # Some nginx builds print test output to stderr regardless of success.
        assert result.returncode == 0, (
            f"nginx -t failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    finally:
        Path(wrapper_path).unlink(missing_ok=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_nginx_config_syntax.py -v`
Expected: `test_nginx_conf_file_exists` FAILS with `AssertionError: missing nginx config at .../deploy/nginx/stand-alone-analyzer.conf`. Other tests fail or error on the missing file.

- [ ] **Step 3: Create the deploy directory tree**

Run: `mkdir -p /Users/houkjang/projects/stand-alone-analyzer/deploy/nginx`

- [ ] **Step 4: Write the nginx config (verbatim port of deployment-design.md §2.1)**

Create `/Users/houkjang/projects/stand-alone-analyzer/deploy/nginx/stand-alone-analyzer.conf`:

```nginx
# /etc/nginx/sites-available/stand-alone-analyzer
# Verbatim port of docs/superpowers/specs/2026-05-20-deployment-design.md §2.1.
# Pinned decision #7 (Plan 5): single canonical location is deploy/nginx/.
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

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_nginx_config_syntax.py -v`
Expected: 4/4 PASS; the `test_nginx_t_passes_when_nginx_available` either PASSES (if nginx is on the host) or is SKIPPED with reason "nginx binary not available on this host".

- [ ] **Step 6: Commit**

```bash
git add deploy/nginx/stand-alone-analyzer.conf tests/test_nginx_config_syntax.py
git commit -m "feat(deploy): nginx server config + syntax smoke test"
```

---

#### Task 5: systemd service unit (saa-api.service)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/deploy/systemd/saa-api.service`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_systemd_unit_shape.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_systemd_unit_shape.py
"""systemd unit-file shape test (Plan 5 Task 5).

Asserts the unit defines the keys deployment-design.md §5.1 requires:
- ExecStart points at the FastAPI app module path verified in Plan 1
  (`flake_analysis.api.main:app`).
- Restart=on-failure (so a crash recovers, but a clean exit doesn't loop).
- User=<EDIT-ME> placeholder so the deploy operator must fill it in.
- Environment lines for HOME, SAA_BIND_HOST, SAA_BIND_PORT.
- KillMode=mixed (deployment-design §5.1 — covers the Streamlit-cache leak fix).
"""
from __future__ import annotations
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_PATH = REPO_ROOT / "deploy" / "systemd" / "saa-api.service"


def test_unit_file_exists():
    assert UNIT_PATH.exists(), f"missing systemd unit at {UNIT_PATH}"


def test_unit_has_user_placeholder():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "User=<EDIT-ME>" in text, "unit must use <EDIT-ME> placeholder for User="
    assert "# User=" in text or "<EDIT-ME>" in text, (
        "unit must signal the operator must fill User in"
    )


def test_unit_has_required_keys():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "Restart=on-failure" in text
    assert "Type=exec" in text or "Type=simple" in text


def test_unit_execstart_targets_fastapi_app():
    text = UNIT_PATH.read_text(encoding="utf-8")
    # Pinned decision: ExecStart=/opt/saa/.venv/bin/uvicorn flake_analysis.api.main:app ...
    assert "/opt/saa/.venv/bin/uvicorn" in text
    assert "flake_analysis.api.main:app" in text
    assert "--host 127.0.0.1" in text
    assert "--port 8000" in text


def test_unit_environment_lines_present():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "Environment=HOME=" in text
    assert "Environment=SAA_BIND_HOST=" in text
    assert "Environment=SAA_BIND_PORT=" in text
    assert "Environment=PYTHONUNBUFFERED=1" in text


def test_unit_kill_semantics():
    text = UNIT_PATH.read_text(encoding="utf-8")
    # deployment-design §5.1: KillMode=mixed reaps the Streamlit-cache leak case
    assert "KillMode=mixed" in text
    assert "TimeoutStopSec=" in text


def test_unit_targets_multi_user():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "WantedBy=multi-user.target" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_systemd_unit_shape.py -v`
Expected: `test_unit_file_exists` FAILS with `AssertionError: missing systemd unit at .../deploy/systemd/saa-api.service`.

- [ ] **Step 3: Create the systemd directory**

Run: `mkdir -p /Users/houkjang/projects/stand-alone-analyzer/deploy/systemd`

- [ ] **Step 4: Write the unit file**

Create `/Users/houkjang/projects/stand-alone-analyzer/deploy/systemd/saa-api.service`:

```ini
# /etc/systemd/system/saa-api.service
# Stand-Alone Analyzer FastAPI backend — single-user, single-process.
# Adapted from deployment-design.md §5.1.
#
# IMPORTANT: BEFORE FIRST INSTALL — replace <EDIT-ME> below with the
# actual service-account UNIX user / group. Suggested name: `saa`.

[Unit]
Description=Stand-Alone Analyzer FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
# <EDIT-ME>: replace with the real service-account user (e.g. `saa`).
User=<EDIT-ME>
Group=<EDIT-ME>

WorkingDirectory=/opt/saa
Environment=HOME=/var/lib/stand-alone-analyzer
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SAA_LOG_LEVEL=info
Environment=SAA_LOG_FORMAT=json
Environment=SAA_BIND_HOST=127.0.0.1
Environment=SAA_BIND_PORT=8000
Environment=STAND_ALONE_THUMB_LOCAL_CACHE=1
EnvironmentFile=-/etc/stand-alone-analyzer/backend.env

ExecStart=/opt/saa/.venv/bin/uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000

# Lifecycle
Restart=on-failure
RestartSec=5s
TimeoutStopSec=45s
KillSignal=SIGTERM
KillMode=mixed

# Hardening (best-effort; relax if SMB needs more)
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/mnt/analysis /var/cache/stand-alone-analyzer /var/lib/stand-alone-analyzer
ReadOnlyPaths=/mnt/raw_images
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=saa-api

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_systemd_unit_shape.py -v`
Expected: 7/7 PASS.

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/saa-api.service tests/test_systemd_unit_shape.py
git commit -m "feat(deploy): saa-api.service systemd unit + shape test"
```

---

#### Task 6: Atomic deploy script (symlink rotation)

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/deploy/scripts/deploy.sh`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_deploy_script_shape.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_script_shape.py
"""Deploy-script shape test (Plan 5 Task 6).

The script's behavior on a real host is not unit-testable (it touches
/usr/share, runs systemctl, etc.). This test asserts the script:
- exists and is executable;
- is a bash script (`#!/usr/bin/env bash`);
- uses `set -euo pipefail` so partial failures abort;
- mentions the canonical paths from deployment-design.md §2 and §5;
- performs the symlink rotation atomically (uses `ln -sfn` or
  `mv -T`).
"""
from __future__ import annotations
import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "deploy" / "scripts" / "deploy.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT_PATH.exists(), f"missing deploy script at {SCRIPT_PATH}"
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, "deploy.sh must be user-executable (chmod +x)"


def test_script_starts_with_bash_shebang():
    first_line = SCRIPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/"), "missing shebang"
    assert "bash" in first_line, "deploy.sh must be a bash script"


def test_script_uses_strict_mode():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text, (
        "deploy.sh must `set -euo pipefail` so partial failures abort"
    )


def test_script_references_canonical_paths():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # web bundle goes under /usr/share/stand-alone-analyzer/web (deployment-design §2)
    assert "/usr/share/stand-alone-analyzer" in text
    # systemd unit name is saa-api per Task 5
    assert "saa-api" in text
    # nginx site name matches the conf filename from Task 4
    assert "stand-alone-analyzer" in text


def test_script_uses_atomic_symlink_swap():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # `ln -sfn` (symbolic, force, no-deref) is the canonical atomic
    # symlink-replace idiom on Linux. `mv -T` is acceptable too.
    assert ("ln -sfn" in text) or ("mv -T" in text), (
        "deploy.sh must rotate the release symlink atomically (ln -sfn or mv -T)"
    )


def test_script_runs_systemctl_reload_or_restart():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "systemctl" in text
    assert ("restart saa-api" in text) or ("reload saa-api" in text), (
        "deploy.sh must restart or reload the saa-api unit after deploying"
    )
    assert ("nginx -s reload" in text) or ("systemctl reload nginx" in text), (
        "deploy.sh must reload nginx after publishing the new web bundle"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_deploy_script_shape.py -v`
Expected: `test_script_exists_and_is_executable` FAILS with `AssertionError: missing deploy script at .../deploy/scripts/deploy.sh`.

- [ ] **Step 3: Create the scripts directory**

Run: `mkdir -p /Users/houkjang/projects/stand-alone-analyzer/deploy/scripts`

- [ ] **Step 4: Write the deploy script**

Create `/Users/houkjang/projects/stand-alone-analyzer/deploy/scripts/deploy.sh`:

```bash
#!/usr/bin/env bash
# deploy.sh — atomic deploy for stand-alone-analyzer (Plan 5 Task 6).
#
# Usage: sudo bash deploy.sh <release-tag>
#   <release-tag> must already exist as /opt/saa/releases/<release-tag>/
#   containing the freshly-built virtualenv + the React dist/ bundle.
#
# Symlink layout:
#   /opt/saa/current        -> /opt/saa/releases/<release-tag>
#   /usr/share/stand-alone-analyzer/web -> /opt/saa/current/web
#
# Rollback: re-run with the previous tag.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <release-tag>" >&2
    exit 2
fi
RELEASE_TAG="$1"
RELEASE_DIR="/opt/saa/releases/${RELEASE_TAG}"

if [[ ! -d "${RELEASE_DIR}" ]]; then
    echo "release dir not found: ${RELEASE_DIR}" >&2
    exit 1
fi
if [[ ! -d "${RELEASE_DIR}/web" ]]; then
    echo "release missing web/ bundle: ${RELEASE_DIR}/web" >&2
    exit 1
fi
if [[ ! -x "${RELEASE_DIR}/.venv/bin/uvicorn" ]]; then
    echo "release missing venv: ${RELEASE_DIR}/.venv/bin/uvicorn" >&2
    exit 1
fi

echo "[deploy] rotating /opt/saa/current -> ${RELEASE_DIR}"
ln -sfn "${RELEASE_DIR}" /opt/saa/current

echo "[deploy] rotating /usr/share/stand-alone-analyzer/web -> /opt/saa/current/web"
mkdir -p /usr/share/stand-alone-analyzer
ln -sfn /opt/saa/current/web /usr/share/stand-alone-analyzer/web

echo "[deploy] reloading systemd unit saa-api"
systemctl daemon-reload
systemctl restart saa-api

echo "[deploy] reloading nginx (site stand-alone-analyzer)"
nginx -t
systemctl reload nginx

echo "[deploy] OK — release ${RELEASE_TAG} live"
```

- [ ] **Step 5: Mark the script executable**

Run: `chmod +x /Users/houkjang/projects/stand-alone-analyzer/deploy/scripts/deploy.sh`

- [ ] **Step 6: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_deploy_script_shape.py -v`
Expected: 6/6 PASS.

- [ ] **Step 7: Commit**

```bash
git add deploy/scripts/deploy.sh tests/test_deploy_script_shape.py
git commit -m "feat(deploy): atomic symlink-rotation deploy script + shape test"
```

---

### Phase 3 — Backend X-Accel-Redirect tile route (deployment-design §2.2 Option B)

**Phase 3 context:** Plan 4 Task 13 wired `GET /api/v1/projects/{pid}/static/thumbnails/lod{lod}/{stem}.webp` to return a `FileResponse` (Option A from deployment-design §2.2). The deployment design picks Option B (`X-Accel-Redirect` so nginx sends the bytes off the loop). Phase 3 converts the existing route from Option A to Option B without changing the URL, the `Cache-Control`/`ETag` headers, or the path-traversal guard. The fallback for projects whose `00_thumbnails/index.json` lacks a `cache_dir` (legacy in-folder layout from v0.2.15) returns a `FileResponse` since those tiles are not under the nginx-`alias`'d `/_tiles_internal/` tree — see deployment-design §2.2 + §4.1. Phase 3 has 2 tasks (route conversion + dedicated test file).

The X-Accel-Redirect path is constructed as:

```
/_tiles_internal/<sha>/lod{N}/<stem>.webp
```

where `<sha>` = the basename of `index.json["cache_dir"]` (which is
`~/.cache/stand-alone-analyzer/thumbnails/<sha>/` per
`core/pipeline/thumbnails.py:74`). nginx's `alias /var/cache/stand-alone-analyzer/thumbnails/`
(Task 4) requires that `/var/cache/stand-alone-analyzer/thumbnails/<sha>/`
resolves to the same content — operator-side concern documented in the
runbook (Task 21), enforced post-deploy by the symlink described in
deployment-design §4.1.

#### Task 7: Convert thumbnail route to X-Accel-Redirect (Option B)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/routes/static.py`
- Test: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_xaccel_thumbnails.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xaccel_thumbnails.py
"""Plan 5 Task 7 — verify the thumbnail static route uses X-Accel-Redirect.

Plan 4 Task 13 returned a FileResponse (Option A). Plan 5 converts the
route per deployment-design §2.2 Option B: when the project's
00_thumbnails/index.json carries a cache_dir, the response body MUST be
empty and the X-Accel-Redirect header MUST point at
/_tiles_internal/<sha>/lod{N}/<stem>.webp.

Legacy projects without cache_dir keep the FileResponse fallback so
existing analysis folders still work — explicitly tested below.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_local_cache_layout(tmp_path: Path, sha: str = "deadbeef12345678") -> Path:
    """Mimic the redirected layout where tiles live under cache_dir/."""
    folder = tmp_path / "analysis"
    folder.mkdir()
    cache_root = tmp_path / "cache" / "stand-alone-analyzer" / "thumbnails" / sha
    (cache_root / "lod1").mkdir(parents=True)
    Image.fromarray(np.zeros((120, 192, 3), dtype=np.uint8)).save(
        cache_root / "lod1" / "ix000_iy000.webp"
    )
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {0: "ix000_iy000"},
    }))
    (folder / "00_thumbnails").mkdir()
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 40], "1": [192, 120], "2": [480, 300]},
        "signature": ["sig0"],
        "cache_dir": str(cache_root),
    }))
    return folder


def _seed_in_folder_layout(tmp_path: Path) -> Path:
    """v0.2.15 legacy layout — tiles directly under 00_thumbnails/lodN/."""
    folder = tmp_path / "analysis"
    folder.mkdir()
    (folder / "00_thumbnails" / "lod1").mkdir(parents=True)
    Image.fromarray(np.zeros((120, 192, 3), dtype=np.uint8)).save(
        folder / "00_thumbnails" / "lod1" / "ix000_iy000.webp"
    )
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {0: "ix000_iy000"},
    }))
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 40], "1": [192, 120], "2": [480, 300]},
        "signature": ["sig0"],
        # NOTE: no cache_dir key — triggers the FileResponse fallback path.
    }))
    return folder


@pytest.mark.asyncio
async def test_xaccel_redirect_emitted_when_cache_dir_present(tmp_path: Path):
    folder = _seed_local_cache_layout(tmp_path, sha="deadbeef12345678")
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    xa = resp.headers.get("x-accel-redirect", "")
    assert xa == "/_tiles_internal/deadbeef12345678/lod1/ix000_iy000.webp", (
        f"expected canonical X-Accel-Redirect path, got {xa!r}"
    )
    # Body MUST be empty so nginx serves the file (Option B).
    assert resp.content == b"", (
        f"X-Accel-Redirect responses must have empty bodies; got {len(resp.content)} bytes"
    )
    # Cache-Control + ETag preserved from the Plan 4 implementation.
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert resp.headers.get("etag", "") != ""


@pytest.mark.asyncio
async def test_xaccel_redirect_uses_internal_prefix_only(tmp_path: Path):
    folder = _seed_local_cache_layout(tmp_path, sha="cafe1234abcd5678")
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    xa = resp.headers.get("x-accel-redirect", "")
    assert xa.startswith("/_tiles_internal/"), (
        f"X-Accel-Redirect must start with /_tiles_internal/ (deployment-design §2.1); "
        f"got {xa!r}"
    )


@pytest.mark.asyncio
async def test_legacy_in_folder_layout_falls_back_to_file_response(tmp_path: Path):
    folder = _seed_in_folder_layout(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    # No cache_dir → no X-Accel-Redirect; body holds the WebP bytes.
    assert "x-accel-redirect" not in {k.lower() for k in resp.headers.keys()}
    # WebP magic: "RIFF" .... "WEBP"
    assert resp.content[:4] == b"RIFF"
    assert resp.content[8:12] == b"WEBP"


@pytest.mark.asyncio
async def test_thumbnail_404_when_file_missing(tmp_path: Path):
    folder = _seed_local_cache_layout(tmp_path, sha="deadbeef12345678")
    # Delete the WebP after seeding so the route's existence check fails.
    (Path(json.loads((folder / "00_thumbnails" / "index.json").read_text())["cache_dir"])
     / "lod1" / "ix000_iy000.webp").unlink()
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thumbnail_missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_xaccel_thumbnails.py -v`
Expected: `test_xaccel_redirect_emitted_when_cache_dir_present` and `test_xaccel_redirect_uses_internal_prefix_only` FAIL — Plan 4's route still returns the file body and no `X-Accel-Redirect` header. The fallback / 404 tests may already pass against the Plan 4 baseline.

- [ ] **Step 3: Modify `src/flake_analysis/api/routes/static.py` — convert thumbnail route**

Edit the existing `get_thumbnail` handler (Plan 4 Task 13) so it reads `index.json["cache_dir"]` and emits `X-Accel-Redirect` when present, else falls back to the existing `FileResponse`. Replace the body of `get_thumbnail` and add a small helper:

```python
# src/flake_analysis/api/routes/static.py — Plan 5 Task 7 conversion.
# Imports added (preserve the existing imports from Plan 4 above this block):
from fastapi import Response
from fastapi.responses import FileResponse


def _read_thumbnail_cache_dir(folder: Path) -> Path | None:
    """Return cache_dir from 00_thumbnails/index.json, or None for legacy layout."""
    idx_p = folder / "00_thumbnails" / "index.json"
    if not idx_p.exists():
        return None
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    cache_dir = idx.get("cache_dir")
    if not cache_dir:
        return None
    return Path(cache_dir)


@router.get("/static/thumbnails/lod{lod}/{stem}.webp")
async def get_thumbnail(
    project_id: str,
    lod: int,
    stem: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Serve a thumbnail tile.

    Plan 5 Task 7 (deployment-design §2.2 Option B): when the project's
    00_thumbnails/index.json declares a `cache_dir`, return a 200 with
    an `X-Accel-Redirect` header pointing at /_tiles_internal/<sha>/...
    so nginx (alias /var/cache/stand-alone-analyzer/thumbnails/) ships
    the bytes off the asyncio loop. Legacy projects (no `cache_dir` in
    index.json — v0.2.15 layout) keep the Plan 4 `FileResponse`
    fallback so existing analysis folders still load.
    """
    folder = Path(manifest.analysis_folder)
    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),
    }

    cache_dir = _read_thumbnail_cache_dir(folder)
    if cache_dir is not None:
        # cache_dir = .../<sha>/   — the basename is what nginx aliases under.
        sha = cache_dir.name
        # Validate <stem> with the same allowlist as the Plan 4 route to
        # prevent injecting traversal segments into the X-Accel-Redirect URL.
        # safe_join will raise ParamsInvalid on any of `..`, absolute,
        # or non-allowlist names.
        safe_target = safe_join(cache_dir / f"lod{lod}", f"{stem}.webp")
        if not safe_target.exists():
            raise ThumbnailMissing(lod=lod, stem=stem)
        headers["X-Accel-Redirect"] = f"/_tiles_internal/{sha}/lod{lod}/{stem}.webp"
        return Response(status_code=200, headers=headers)

    # Legacy v0.2.15 layout — tiles live directly under 00_thumbnails/lodN/.
    cache = folder / "00_thumbnails"
    safe_stem = safe_join(cache / f"lod{lod}", f"{stem}.webp")
    if not safe_stem.exists():
        raise ThumbnailMissing(lod=lod, stem=stem)
    return FileResponse(str(safe_stem), media_type="image/webp", headers=headers)
```

Notes for the implementer:
- `safe_join`, `ThumbnailMissing`, `_thumb_etag`, `Manifest`, `get_manifest`, `get_current_user`, and the `router` are already imported at the top of `static.py` from Plan 4 Task 13. The only new imports are `Response` and `FileResponse` (which Plan 4 already imports — verify before re-adding).
- Do NOT change the URL pattern, the `Cache-Control` header, the `ETag`, or the path-traversal validation. The change is body-only.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_xaccel_thumbnails.py -v`
Expected: 4/4 PASS.

- [ ] **Step 5: Run the existing Plan 4 thumbnail test to ensure no regression**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_thumbnails.py -v`
Expected: every Plan 4 test still PASSES. If a Plan 4 test asserted on the response body bytes (Option A behavior), update that test in Step 6 below; if all Plan 4 tests still pass, skip step 6.

- [ ] **Step 6: (Conditional) update any Plan 4 test that asserted on body bytes**

If `test_static_thumbnails.py` has assertions like `assert resp.content[:4] == b"RIFF"` for the cache_dir-present case, change those tests to seed the legacy in-folder layout (no `cache_dir` in `index.json`) so the WebP bytes still round-trip. Use the `_seed_in_folder_layout` helper pattern from `tests/test_xaccel_thumbnails.py`. Re-run the Plan 4 suite:

`/Users/houkjang/anaconda3/bin/python -m pytest tests/api/test_static_thumbnails.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/flake_analysis/api/routes/static.py tests/test_xaccel_thumbnails.py tests/api/test_static_thumbnails.py
git commit -m "feat(api): X-Accel-Redirect for thumbnail route (deployment-design §2.2 Option B)"
```

(If Step 6 was skipped, drop `tests/api/test_static_thumbnails.py` from the `git add`.)

---

#### Task 8: Run the full backend suite to confirm no other route regressed

**Files:** none modified.

- [ ] **Step 1: Run the full backend suite**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES; the 4 new tests from Task 7 are included; counts match the Phase 1 baseline plus the new tests added in Phase 2 (Tasks 4, 5, 6) and Phase 3 (Task 7).

- [ ] **Step 2: No commit**

This is a verification gate; nothing to stage.

---

### Phase 4 — Streamlit deletion (atomic destruction)

**Phase 4 context:** with deploy artifacts wired (Phase 2) and the X-Accel-Redirect tile route in place (Phase 3), the Streamlit UI is no longer the source of truth for ANY interaction. Phase 4 deletes it as a series of small commits, each preceded by a `grep` / `ls` verification step. Pinned decision #5 (atomic cutover) is enforced here: by the end of Phase 4, `import flake_analysis.ui` raises `ModuleNotFoundError`, and `app/streamlit_app.py` no longer exists. Pinned decision #4 (test deletion safety) is enforced: each Streamlit-only test deletion is preceded by a grep proving no other test imports a symbol from it.

#### Task 9: Verify Streamlit footprint matches the deletion plan

**Files:** none modified.

- [ ] **Step 1: List the tracked Streamlit entrypoint**

Run: `git ls-files app/streamlit_app.py`
Expected output: exactly the line `app/streamlit_app.py`. If the file is not tracked or the path differs, STOP and re-read the brief.

- [ ] **Step 2: List every tracked file under the UI module**

Run: `git ls-files src/flake_analysis/ui/`
Expected output (one path per line, every path under `src/flake_analysis/ui/`):

```
src/flake_analysis/ui/__init__.py
src/flake_analysis/ui/_brushing.py
src/flake_analysis/ui/_image_preview.py
src/flake_analysis/ui/sidebar.py
src/flake_analysis/ui/tab_clustering.py
src/flake_analysis/ui/tab_compute.py
src/flake_analysis/ui/tab_explorer.py
src/flake_analysis/ui/tab_selector.py
```

If a file is missing or extra, document the diff before proceeding.

- [ ] **Step 3: List every Streamlit-only test**

Run: `grep -lE 'from flake_analysis\.ui|^import streamlit|^from streamlit' /Users/houkjang/projects/stand-alone-analyzer/tests/test_*.py`
Expected output (exact set):

```
/Users/houkjang/projects/stand-alone-analyzer/tests/test_brushing.py
/Users/houkjang/projects/stand-alone-analyzer/tests/test_explorer_mosaic_helpers.py
/Users/houkjang/projects/stand-alone-analyzer/tests/test_image_preview.py
/Users/houkjang/projects/stand-alone-analyzer/tests/test_imports.py
/Users/houkjang/projects/stand-alone-analyzer/tests/test_pipeline_selector.py
/Users/houkjang/projects/stand-alone-analyzer/tests/test_selector_filter_persistence.py
```

`test_imports.py` and `test_pipeline_selector.py` are NOT deleted; they are MODIFIED in Tasks 17 and 13 respectively.

- [ ] **Step 4: No commit**

Verification only.

---

#### Task 10: Confirm no production code outside `app/` and `src/flake_analysis/ui/` imports Streamlit

**Files:** none modified.

- [ ] **Step 1: Grep `src/` for any Streamlit reference**

Run: `grep -rEn 'import streamlit|from streamlit|streamlit\.' /Users/houkjang/projects/stand-alone-analyzer/src/`
Expected output: only matches under `src/flake_analysis/ui/`. If any other `src/` file imports Streamlit, STOP — Plan 5's deletion plan does not cover it.

- [ ] **Step 2: Grep `tests/` for Streamlit references outside the known files**

Run: `grep -rEn 'import streamlit|from streamlit|streamlit\.' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: only matches in the 6 files listed in Task 9 Step 3. Any other file is an unmapped consumer; STOP and update Phase 4 accordingly.

- [ ] **Step 3: Grep the API package specifically**

Run: `grep -rEn 'flake_analysis\.ui|streamlit' /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/`
Expected: zero matches. The FastAPI surface must not depend on the UI tree.

- [ ] **Step 4: No commit**

Verification only. If Steps 1-3 surfaced unmapped consumers, halt the plan and update the deletion list before continuing.

---

#### Task 11: Confirm no test outside the deletion list imports a Streamlit-only test helper

**Files:** none modified.

- [ ] **Step 1: Grep for cross-imports of `_brushing` test helpers**

Run: `grep -rEn 'from tests\.test_brushing|from test_brushing|import test_brushing' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches. (Pytest does not normally allow test-to-test imports, but `conftest`-style helper sharing has been seen.)

- [ ] **Step 2: Grep for cross-imports of `_image_preview` test helpers**

Run: `grep -rEn 'from tests\.test_image_preview|from test_image_preview|import test_image_preview' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches.

- [ ] **Step 3: Grep for cross-imports of `test_explorer_mosaic_helpers`**

Run: `grep -rEn 'from tests\.test_explorer_mosaic_helpers|from test_explorer_mosaic_helpers|import test_explorer_mosaic_helpers' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches.

- [ ] **Step 4: No commit**

Verification only. If any cross-import surfaces, the consumer must be migrated before its provider is deleted in Tasks 15-16.

---

#### Task 12: Delete `app/streamlit_app.py`

**Files:**
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/app/streamlit_app.py`

- [ ] **Step 1: Verify the file is tracked and its contents match expectation**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/app/streamlit_app.py`
Expected: the file exists.

Run: `git ls-files app/streamlit_app.py`
Expected: prints `app/streamlit_app.py`.

- [ ] **Step 2: Confirm nothing under `src/` or `tests/` imports the entrypoint as a module**

Run: `grep -rEn 'streamlit_app|app\.streamlit_app' /Users/houkjang/projects/stand-alone-analyzer/src/ /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches. (If any test imports it, halt.)

- [ ] **Step 3: Delete with `git rm`**

Run: `git rm app/streamlit_app.py`
Expected: `rm 'app/streamlit_app.py'`.

- [ ] **Step 4: Check whether `app/` is now empty**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/app/`
Expected output: empty, OR contains untracked files only. If empty AND `git status` shows no other content under `app/`, remove the directory:

```bash
rmdir /Users/houkjang/projects/stand-alone-analyzer/app/  # safe; rmdir refuses non-empty
```

If `rmdir` fails (directory not empty), leave the directory in place — non-tracked files have value to the developer.

- [ ] **Step 5: Run the full pytest suite to confirm no test referenced this entrypoint**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES (or the only failures are the Streamlit-import tests scheduled for deletion in Tasks 15-16; document each failure now and confirm it disappears after the matching deletion). Other regressions block the commit.

- [ ] **Step 6: Commit**

```bash
git commit -m "chore(cutover): delete app/streamlit_app.py entrypoint"
```

---

#### Task 13: Migrate the Streamlit-dependent assertion in `tests/test_pipeline_selector.py`

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_pipeline_selector.py`

**Goal:** replace `from flake_analysis.ui.tab_selector import _values_for_axis` with an inline pure-core helper so the test survives Task 14's deletion of the `ui/` tree. The function's behavior under test (axis-name → stats column mapping) is a pure dict-lookup; no Streamlit dependency is needed.

- [ ] **Step 1: Verify the import currently in the test**

Run: `grep -n 'from flake_analysis.ui' /Users/houkjang/projects/stand-alone-analyzer/tests/test_pipeline_selector.py`
Expected: line `134:    from flake_analysis.ui.tab_selector import _values_for_axis`.

- [ ] **Step 2: Read the existing `_values_for_axis` to copy its behavior**

Read `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/ui/tab_selector.py` and locate `_values_for_axis`. The current implementation is a pure axis-name → numpy-column dispatch (no Streamlit calls). Mirror its behavior in the test below.

- [ ] **Step 3: Edit the test to inline the helper**

In `tests/test_pipeline_selector.py`, replace the `test_values_for_axis_returns_correct_columns` body so the helper is defined inline. The full new function body:

```python
def test_values_for_axis_returns_correct_columns():
    """Axis dropdown mapping → correct stats column for each axis name.

    Plan 5 Task 13: inlined the pure-Python helper from
    src/flake_analysis/ui/tab_selector.py so this regression survives
    the Streamlit UI deletion (Plan 5 Task 14).
    """
    import numpy as np

    def _values_for_axis(stats: dict, axis: str) -> np.ndarray:
        # Mirrors the dispatch table in the deleted
        # `flake_analysis.ui.tab_selector._values_for_axis`.
        rgb = stats["repr_rgbs"]
        std = stats["std_pcts"]
        if axis == "R":
            return rgb[:, 0]
        if axis == "G":
            return rgb[:, 1]
        if axis == "B":
            return rgb[:, 2]
        if axis == "std_r":
            return std[:, 0]
        if axis == "std_g":
            return std[:, 1]
        if axis == "std_b":
            return std[:, 2]
        if axis == "area":
            return stats["areas"]
        if axis == "sam2":
            return stats["sam2"]
        raise KeyError(f"unknown axis: {axis!r}")

    rng = np.random.default_rng(42)
    n = 8
    rgb = rng.uniform(0, 255, size=(n, 3)).astype(np.float64)
    std = rng.uniform(0, 50, size=(n, 3)).astype(np.float64)
    areas = rng.integers(10, 1000, size=n).astype(np.float64)
    sam2 = rng.uniform(0, 1, size=n).astype(np.float64)
    flake_ids = np.arange(n, dtype=np.int64)

    stats = {
        "repr_rgbs": rgb,
        "std_pcts": std,
        "areas": areas,
        "sam2": sam2,
        "flake_ids": flake_ids,
    }

    np.testing.assert_array_equal(_values_for_axis(stats, "R"), rgb[:, 0])
    np.testing.assert_array_equal(_values_for_axis(stats, "G"), rgb[:, 1])
    np.testing.assert_array_equal(_values_for_axis(stats, "B"), rgb[:, 2])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_r"), std[:, 0])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_g"), std[:, 1])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_b"), std[:, 2])
    np.testing.assert_array_equal(_values_for_axis(stats, "area"), areas)
    np.testing.assert_array_equal(_values_for_axis(stats, "sam2"), sam2)
```

- [ ] **Step 4: Verify no other line in the file imports from `flake_analysis.ui`**

Run: `grep -n 'flake_analysis\.ui' /Users/houkjang/projects/stand-alone-analyzer/tests/test_pipeline_selector.py`
Expected: zero matches.

- [ ] **Step 5: Run the test to confirm it still passes (with the UI module still present)**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_pipeline_selector.py -v`
Expected: every test PASSES (including `test_values_for_axis_returns_correct_columns`). At this point the test does NOT depend on `flake_analysis.ui`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_pipeline_selector.py
git commit -m "chore(cutover): inline _values_for_axis helper in selector test (drop ui import)"
```

---

#### Task 14: Delete `src/flake_analysis/ui/` (the entire UI module)

**Files:**
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/ui/`

- [ ] **Step 1: Re-verify nothing outside the deletion-target tests still imports the module**

Run: `grep -rEn 'from flake_analysis\.ui|import flake_analysis\.ui' /Users/houkjang/projects/stand-alone-analyzer/src/ /Users/houkjang/projects/stand-alone-analyzer/tests/ /Users/houkjang/projects/stand-alone-analyzer/app/`
Expected output: only matches in `tests/test_brushing.py`, `tests/test_image_preview.py`, `tests/test_explorer_mosaic_helpers.py`, and `tests/test_imports.py`. The first three are deleted in Tasks 15-16; `test_imports.py` is updated in Task 17. If any other consumer surfaces, STOP.

- [ ] **Step 2: List the tracked files about to be removed**

Run: `git ls-files src/flake_analysis/ui/`
Expected: the 8 files listed in Task 9 Step 2.

- [ ] **Step 3: Delete the directory tree**

Run: `git rm -r src/flake_analysis/ui/`
Expected output: `rm 'src/flake_analysis/ui/__init__.py'` and 7 more `rm` lines, one per file.

- [ ] **Step 4: Verify the directory is gone**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/ui/ 2>&1`
Expected: `No such file or directory` (untracked `__pycache__` would still show; if so, remove it):

```bash
rm -rf /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/ui/
```

- [ ] **Step 5: Run pytest expecting the Streamlit-only tests to fail (will be deleted in Task 15)**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected failures (acceptable, scheduled for next two tasks):
- `tests/test_brushing.py` — collection error / `ModuleNotFoundError: No module named 'flake_analysis.ui'`
- `tests/test_image_preview.py` — same
- `tests/test_explorer_mosaic_helpers.py` — same
- `tests/test_imports.py::test_subpackages_importable` — `ModuleNotFoundError: No module named 'flake_analysis.ui'`
- `tests/test_selector_filter_persistence.py` — collection error / `streamlit` not importable (Plan 5 still has Streamlit installed at this point, but the test boots `streamlit.testing.v1.AppTest` against the now-deleted UI; the resulting error is transient and the file is deleted in Task 16).

Any OTHER failure (`tests/api/...`, `tests/test_pipeline_*.py`, `tests/test_state_*.py`) is a regression — STOP and resolve before commit.

- [ ] **Step 6: Commit**

```bash
git commit -m "chore(cutover): delete src/flake_analysis/ui/ Streamlit module"
```

---

#### Task 15: Delete the three Streamlit-only test files (`test_brushing`, `test_image_preview`, `test_explorer_mosaic_helpers`)

**Files:**
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_brushing.py`
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_image_preview.py`
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_explorer_mosaic_helpers.py`

- [ ] **Step 1: Re-verify each file is tracked**

Run: `git ls-files tests/test_brushing.py tests/test_image_preview.py tests/test_explorer_mosaic_helpers.py`
Expected: all three lines printed. If any is missing, halt and re-read.

- [ ] **Step 2: Re-verify no other test imports a symbol from these files (Task 11 was earlier; this is a paranoia check)**

Run: `grep -rEn 'from test_brushing|from test_image_preview|from test_explorer_mosaic_helpers|import test_brushing|import test_image_preview|import test_explorer_mosaic_helpers' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches.

- [ ] **Step 3: Delete the three files**

Run: `git rm tests/test_brushing.py tests/test_image_preview.py tests/test_explorer_mosaic_helpers.py`
Expected output: 3 `rm` lines, one per file.

- [ ] **Step 4: Run pytest — Streamlit-only collection failures should be down to one (`test_selector_filter_persistence.py`)**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected remaining failures: only `tests/test_imports.py::test_subpackages_importable` (still imports `flake_analysis.ui`) and `tests/test_selector_filter_persistence.py` (Streamlit AppTest). All other tests PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(cutover): delete Streamlit-only tests (brushing, image_preview, explorer_mosaic_helpers)"
```

---

#### Task 16: Delete `tests/test_selector_filter_persistence.py` (Streamlit AppTest)

**Files:**
- Delete: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_selector_filter_persistence.py`

- [ ] **Step 1: Verify the file boots Streamlit and has no non-Streamlit logic worth preserving**

Run: `grep -nE 'streamlit\.testing|AppTest|streamlit' /Users/houkjang/projects/stand-alone-analyzer/tests/test_selector_filter_persistence.py`
Expected: matches confirm the file boots `streamlit.testing.v1.AppTest`. The file only validates Streamlit session-state persistence — meaningless once the Streamlit UI is gone. If grep output suggests any pure-core helper inside this file, halt and decide whether to migrate before deletion.

- [ ] **Step 2: Verify no other test imports this file**

Run: `grep -rEn 'from test_selector_filter_persistence|import test_selector_filter_persistence' /Users/houkjang/projects/stand-alone-analyzer/tests/`
Expected: zero matches.

- [ ] **Step 3: Delete the file**

Run: `git rm tests/test_selector_filter_persistence.py`
Expected: `rm 'tests/test_selector_filter_persistence.py'`.

- [ ] **Step 4: Run pytest — only `tests/test_imports.py` remains failing**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: only `tests/test_imports.py::test_subpackages_importable` and `tests/test_imports.py::test_package_import` (stale version assertion `0.2.2`) fail; the next task fixes both.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(cutover): delete Streamlit AppTest selector-persistence test"
```

---

#### Task 17: Update `tests/test_imports.py` (drop `flake_analysis.ui`, refresh version assertion)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_imports.py`

**Goal:** the test currently asserts `flake_analysis.__version__ == "0.2.2"` (stale) AND imports the now-deleted `flake_analysis.ui`. Both must change. The version assertion is updated to read the actual `__version__` attribute (no hard-coded check) so the version bump in Task 19 doesn't immediately break this test.

- [ ] **Step 1: Verify the current state of the file**

Run: `grep -n 'flake_analysis\.ui\|__version__\|streamlit' /Users/houkjang/projects/stand-alone-analyzer/tests/test_imports.py`
Expected output:

```
8:    assert flake_analysis.__version__ == "0.2.2"
14:    import flake_analysis.ui  # noqa: F401
```

- [ ] **Step 2: Replace the file contents**

Write `/Users/houkjang/projects/stand-alone-analyzer/tests/test_imports.py`:

```python
"""M0 smoke tests — verify package imports.

Plan 5 Task 17: drop the deleted `flake_analysis.ui` import; replace
the hard-coded version assertion with a shape check so future bumps
don't ripple here.
"""
from __future__ import annotations
import re


def test_package_import():
    import flake_analysis

    # Plan 5 ships v0.3.0 (Task 19). We only assert the shape (semver-ish
    # string with at least one dot) so future patches don't repeatedly
    # break this test.
    assert isinstance(flake_analysis.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", flake_analysis.__version__) is not None


def test_subpackages_importable():
    import flake_analysis.state  # noqa: F401
    import flake_analysis.pipeline  # noqa: F401
    # Plan 5 Task 14 deleted flake_analysis.ui; do NOT import it here.
    import flake_analysis.cache  # noqa: F401
    import flake_analysis.core  # noqa: F401
    import flake_analysis.api  # noqa: F401
```

- [ ] **Step 3: Run the test**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_imports.py -v`
Expected: 2/2 PASS.

- [ ] **Step 4: Run the full suite — every test passes now**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES; no Streamlit-related failure remains. Streamlit deletion is complete (modulo dependency cleanup in Phase 5).

- [ ] **Step 5: Commit**

```bash
git add tests/test_imports.py
git commit -m "chore(cutover): drop ui import + relax version assertion in test_imports"
```

---

### Phase 5 — Dependency cleanup (pyproject)

#### Task 18: Drop `streamlit` and `plotly` from `pyproject.toml`; rewrite `description`

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/pyproject.toml`

- [ ] **Step 1: Confirm no remaining Python consumer of `streamlit` or `plotly`**

Run: `grep -rEn 'import streamlit|from streamlit|streamlit\.' /Users/houkjang/projects/stand-alone-analyzer/src/ /Users/houkjang/projects/stand-alone-analyzer/tests/ /Users/houkjang/projects/stand-alone-analyzer/app/ 2>/dev/null`
Expected: zero matches.

Run: `grep -rEn 'import plotly|from plotly' /Users/houkjang/projects/stand-alone-analyzer/src/ /Users/houkjang/projects/stand-alone-analyzer/tests/ /Users/houkjang/projects/stand-alone-analyzer/app/ 2>/dev/null`
Expected: zero matches.

If any match remains, STOP — Phase 4 missed a consumer.

- [ ] **Step 2: Edit `pyproject.toml` — remove `streamlit>=1.32` and `plotly>=5.18`**

Edit `[project].dependencies` so the two lines are gone. The post-edit `[project]` block is:

```toml
[project]
name = "stand-alone-analyzer"
version = "0.2.18"
description = "React + FastAPI app for interactive 2D material flake analysis"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [
    { name = "Qpress Contributors" }
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Image Recognition",
    "Topic :: Scientific/Engineering :: Visualization",
]
dependencies = [
    "pandas>=2.1",
    "pyarrow>=14",
    # Absorbed from flake-analysis-core (merged in v0.2.0)
    "numpy>=1.24",
    "Pillow>=10",
    "scipy>=1.11",
    "opencv-python>=4.8",
    "pycocotools>=2.0",
    "scikit-learn>=1.3",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "fastapi>=0.110",
    "httpx>=0.27",
]
```

Note: `description` is rewritten (pinned decision #10 — minimal scope; just swap "Streamlit" → "React + FastAPI"). `version` stays at `0.2.18` here; it bumps in Task 19 in a dedicated commit per pinned decision #6.

- [ ] **Step 3: Reinstall the package to verify the dependencies resolve**

Run: `/Users/houkjang/anaconda3/bin/pip install -e .`
Expected: pip resolves cleanly with no `streamlit`/`plotly` mention; exit 0. The next install of a fresh venv would NOT pull these.

- [ ] **Step 4: Run the full pytest suite to ensure nothing broke**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): drop streamlit and plotly from pyproject.toml; rewrite description"
```

---

#### Task 19: Bump version 0.2.18 → 0.3.0 (single commit)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/pyproject.toml`
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/__init__.py`

- [ ] **Step 1: Verify the current version values**

Run: `grep -n '^version = ' /Users/houkjang/projects/stand-alone-analyzer/pyproject.toml`
Expected: `version = "0.2.18"`.

Run: `grep -n '__version__' /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/__init__.py`
Expected: `__version__ = "0.2.18"`.

- [ ] **Step 2: Update both version strings to `0.3.0`**

In `pyproject.toml`, change `version = "0.2.18"` to `version = "0.3.0"`.
In `src/flake_analysis/__init__.py`, change `__version__ = "0.2.18"` to `__version__ = "0.3.0"`. While editing the `__init__.py`, also rewrite the module docstring so it stops claiming the package is a Streamlit app:

```python
"""stand-alone-analyzer: React + FastAPI app for interactive flake analysis."""

__version__ = "0.3.0"
```

- [ ] **Step 3: Verify `pyproject.toml` and `__init__.py` agree**

Run: `grep -n '^version' /Users/houkjang/projects/stand-alone-analyzer/pyproject.toml`
Expected: `version = "0.3.0"`.

Run: `grep -n '__version__' /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/__init__.py`
Expected: `__version__ = "0.3.0"`.

- [ ] **Step 4: Run the full pytest suite — `test_imports.py::test_package_import` (Task 17) accepts any semver shape and PASSES**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/flake_analysis/__init__.py
git commit -m "chore(cutover): bump version 0.2.18 → 0.3.0"
```

---

### Phase 6 — Documentation update

#### Task 20: Rewrite README "How to run" section

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/README.md`

**Goal:** replace the Streamlit-launch block with a React + FastAPI runbook. Pinned decision #10 keeps the rewrite minimal (no marketing rewrite, no historical-context purge).

- [ ] **Step 1: Verify the current README's "Quick start" section**

Run: `grep -n 'streamlit run\|streamlit app\|Quick start\|Status' /Users/houkjang/projects/stand-alone-analyzer/README.md`
Expected matches: line 3 (header description with "Streamlit app"), the "Status" section, the "Quick start" section, and at minimum the `streamlit run app/streamlit_app.py` line.

- [ ] **Step 2: Replace the top-of-file description and Quick start block**

Edit `README.md`. Three changes total:

1. Line 3 (the one-line description under the H1) changes from `A Streamlit app for interactive 2D material flake analysis.` to `A React + FastAPI app for interactive 2D material flake analysis.`

2. The "Status" section's `v0.2.0` → `v0.3.0`. The line currently reads:

```
`v0.2.0` — beta. Single-user desktop tool. No DB, no SSH, no GPU.
```

Change to:

```
`v0.3.0` — beta. React + FastAPI. Single-user desktop tool. No DB, no SSH, no GPU.
```

3. The "Quick start" section block — replace the whole `## Quick start` section (from the heading down to but NOT including the next `## ` heading) with:

```markdown
## Quick start

```bash
# Clone and install (Python deps for the FastAPI backend)
git clone https://github.com/HoukJangBNL/stand-alone-analyzer.git
cd stand-alone-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Build the React frontend
cd web
npm install
npm run build
cd ..

# Run the FastAPI backend (serves /api/v1/*)
uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000
```

For production, see `docs/operations/runbook.md` (nginx + systemd
serve `web/dist/` + reverse-proxy uvicorn). For development with
hot-reload, see `CONTRIBUTING.md` (`npm run dev` on :5173 + uvicorn
`--reload` on :8000).

Then enter 3 paths in the SPA's left sidebar:
1. **raw_images/** — folder of microscope tile PNGs
2. **annotations.json** — COCO+RLE segmentation output (e.g., from SAM2)
3. **analysis_folder/** — empty directory (will be populated)
```

(Leave the "Pipeline tabs" table, "Filesystem layout" section, "Tests" section, License, and Acknowledgements untouched.)

- [ ] **Step 3: Verify the rewrite landed**

Run: `grep -n 'streamlit run\|Streamlit app for' /Users/houkjang/projects/stand-alone-analyzer/README.md`
Expected: zero matches.

Run: `grep -n 'uvicorn\|npm run build\|React + FastAPI' /Users/houkjang/projects/stand-alone-analyzer/README.md`
Expected: at least one match per term.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README quick-start for React + FastAPI"
```

---

#### Task 21: Add `docs/operations/runbook.md`

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/docs/operations/runbook.md`

- [ ] **Step 1: Verify target dir does not yet exist**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/docs/operations/ 2>&1`
Expected: `No such file or directory`.

- [ ] **Step 2: Create the directory**

Run: `mkdir -p /Users/houkjang/projects/stand-alone-analyzer/docs/operations`

- [ ] **Step 3: Write `docs/operations/runbook.md`**

Create `/Users/houkjang/projects/stand-alone-analyzer/docs/operations/runbook.md`:

```markdown
# Operations runbook

This file is the single-page reference for installing, restarting, and
inspecting the React + FastAPI deploy. It mirrors
`docs/superpowers/specs/2026-05-20-deployment-design.md` and assumes
the deploy artifacts under `deploy/` (nginx config, systemd unit,
deploy script) have been copied to the target host.

## Install (first time)

```bash
# 1. Lay down the systemd unit
sudo cp deploy/systemd/saa-api.service /etc/systemd/system/saa-api.service
sudo $EDITOR /etc/systemd/system/saa-api.service   # replace <EDIT-ME> with the service-account user
sudo systemctl daemon-reload

# 2. Lay down the nginx site
sudo cp deploy/nginx/stand-alone-analyzer.conf /etc/nginx/sites-available/stand-alone-analyzer
sudo ln -sfn /etc/nginx/sites-available/stand-alone-analyzer /etc/nginx/sites-enabled/stand-alone-analyzer
sudo nginx -t

# 3. Wire the local-disk thumbnail cache symlink (deployment-design §4.1)
sudo mkdir -p /var/cache/stand-alone-analyzer
sudo ln -sfn /var/lib/stand-alone-analyzer/.cache/stand-alone-analyzer/thumbnails \
    /var/cache/stand-alone-analyzer/thumbnails

# 4. Start the service
sudo systemctl enable --now saa-api
sudo systemctl reload nginx
```

## Atomic deploy of a new release

```bash
# After building venv + web/dist on a build host and rsync-ing them
# into /opt/saa/releases/<tag>/, run on the target host:
sudo bash deploy/scripts/deploy.sh <release-tag>
```

`deploy.sh` rotates `/opt/saa/current` and the
`/usr/share/stand-alone-analyzer/web` symlink atomically (`ln -sfn`),
runs `nginx -t`, then `systemctl restart saa-api` + `systemctl reload nginx`.

## Restart / reload

```bash
# Restart FastAPI (drops in-flight SSE; ~30s graceful shutdown window)
sudo systemctl restart saa-api

# Reload nginx (no client-visible drop)
sudo systemctl reload nginx

# Reload nginx after editing /etc/nginx/sites-available/stand-alone-analyzer
sudo nginx -t && sudo systemctl reload nginx
```

## Inspect logs

```bash
# Tail the FastAPI structured-JSON log
sudo journalctl -u saa-api -f -o cat

# Last hour, structured
sudo journalctl -u saa-api --since "1 hour ago" -o json | jq

# nginx access / error logs (Debian/Ubuntu paths)
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## Health probe

```bash
# Cheap nginx-level probe
curl -s http://localhost/healthz
# -> "ok"

# Deep API probe
curl -s http://localhost/api/v1/health | jq
# -> {ok, version, smb_reachable, manifest_path_writable, ...}
```

## Rollback

Pinned decision #8 (Plan 5): rollback = git-revert the cutover PR,
rebuild the venv + dist, and re-run `deploy.sh` against the previous
release tag.

```bash
sudo bash deploy/scripts/deploy.sh <previous-release-tag>
```

There is NO on-host parallel-run of the old Streamlit app; that code
is gone post-cutover. Within the cutover window itself
(deployment-design §10.3), the old Streamlit binary may still exist
on the host — that fallback applies only between T-1 and T+7 days.

## Manual smoke checklist (run after every deploy)

These steps live in deployment-design §10.3 and Plan 5 Phase 8 Task 26.
They are not automated:

1. Open the SPA at `/`. Index renders, no console errors.
2. Pick the most recently used `analysis_folder/` in the sidebar.
3. Compute tab — the 7 step statuses load.
4. Selector tab — scatter renders, brushing works.
5. Clustering tab — labels load (or empty-state for a fresh project).
6. Explorer tab — 60×60 mosaic opens in <2s, panning is smooth.
7. `sudo systemctl restart saa-api`. Reload the page; state persists
   (manifest is on SMB; not in the dead process).
8. `journalctl -u saa-api --since "5 minutes ago"` — no ERROR lines.

## Environment variables

See deployment-design §8.3 for the canonical list. Key ones:

| Var | Default | Purpose |
|---|---|---|
| `SAA_BIND_HOST` | `127.0.0.1` | uvicorn listen address |
| `SAA_BIND_PORT` | `8000` | uvicorn port |
| `SAA_LOG_LEVEL` | `info` | log verbosity |
| `SAA_LOG_FORMAT` | `json` | `json` or `text` |
| `SAA_ALLOWED_ORIGINS` | `` (empty) | CORS allow-list (CSV) |
| `STAND_ALONE_THUMB_LOCAL_CACHE` | `1` | Opts into local-disk cache redirect |
| `HOME` | `/var/lib/stand-alone-analyzer` | Anchors `~/.cache/...` resolution |

The systemd unit (`deploy/systemd/saa-api.service`) sets these by
default. To override, edit `/etc/stand-alone-analyzer/backend.env`
(referenced via `EnvironmentFile=-` in the unit).

## Post-v1 notes

- TLS: not in v1; deployment-design §7.1 covers Let's Encrypt or
  institutional CA when required.
- SSO: not in v1; deployment-design §7.2 covers the `oauth2-proxy`
  add-on path (no code change, just nginx + a sidecar).
- Eviction: thumbnail cache has no eviction in v1; the manual
  `Clear cache` button per project is the only path. See
  deployment-design §4.3.
```

- [ ] **Step 4: Verify the file is committable**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/docs/operations/runbook.md`
Expected: file exists, non-empty.

- [ ] **Step 5: Commit**

```bash
git add docs/operations/runbook.md
git commit -m "docs: add operations/runbook for nginx + systemd cutover"
```

---

#### Task 22: Update `CONTRIBUTING.md` (drop Streamlit dev workflow)

**Files:**
- Modify: `/Users/houkjang/projects/stand-alone-analyzer/CONTRIBUTING.md`

- [ ] **Step 1: Verify the lines that mention Streamlit**

Run: `grep -n 'Streamlit\|streamlit\|Plotly\|plotly' /Users/houkjang/projects/stand-alone-analyzer/CONTRIBUTING.md`
Expected matches around: line 22 (`Streamlit ≥1.32, Plotly ≥5.18`), line 31 (`Streamlit smoke tests: ...`), line 47 (`Streamlit version`).

- [ ] **Step 2: Edit each line**

In `CONTRIBUTING.md`:

1. Replace the dependency-version line `- Streamlit ≥1.32, Plotly ≥5.18` with `- React 18.3, FastAPI ≥0.110 (frontend deps live in web/package.json; backend deps in pyproject.toml)`
2. Replace the bullet `- Streamlit smoke tests: import the page module and call render() with a stubbed session_state` with `- Frontend tests: vitest under web/src/**/__tests__/ — run via cd web && npx vitest run`
3. The line containing `Streamlit version` (the bug-report template field) — replace `Streamlit version` with `Browser + version (frontend) and FastAPI/uvicorn version (backend)`

- [ ] **Step 3: Add a "Dev loop" subsection if absent**

Search for an existing "Dev loop" or "Local dev" or "Running locally" subsection. If absent, append the following section to the end of the file:

```markdown

## Local dev loop (Plan 5 cutover, v0.3.0+)

Two terminals, one for the React dev server and one for uvicorn with
hot-reload. Vite proxies `/api/*` to `127.0.0.1:8000` so the same
URL shape works in dev and prod (deployment-design §8.1).

```bash
# Terminal 1 — React HMR on :5173
cd web && npm run dev

# Terminal 2 — FastAPI with reload on :8000
SAA_LOG_LEVEL=debug uvicorn flake_analysis.api.main:app --reload --port 8000
```

Open http://localhost:5173/ in a browser. Backend changes auto-reload
via uvicorn `--reload`; frontend changes hot-reload via Vite.
```

- [ ] **Step 4: Verify no Streamlit references remain**

Run: `grep -n 'Streamlit\|streamlit\|Plotly\|plotly' /Users/houkjang/projects/stand-alone-analyzer/CONTRIBUTING.md`
Expected: zero matches.

- [ ] **Step 5: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: drop Streamlit dev workflow from CONTRIBUTING; add dev-loop section"
```

---

### Phase 7 — Cutover guard tests

#### Task 23: Run the full backend + frontend suites against the post-deletion tree

**Files:** none modified.

- [ ] **Step 1: Run pytest**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES; the test count equals the Phase 1 baseline minus the four deleted Streamlit tests (`test_brushing.py`, `test_image_preview.py`, `test_explorer_mosaic_helpers.py`, `test_selector_filter_persistence.py`) plus the deploy-shape tests (Tasks 4, 5, 6) plus the X-Accel-Redirect tests (Task 7). Document the new total locally for Phase 8 Task 27.

- [ ] **Step 2: Run vitest**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx vitest run`
Expected: every test PASSES; counts match the Phase 1 Task 2 baseline (the frontend tree was untouched by Plan 5).

- [ ] **Step 3: No commit**

Verification gate.

---

#### Task 24: `tests/test_no_streamlit.py` — guard against future Streamlit re-introduction

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_no_streamlit.py`

- [ ] **Step 1: Write the test**

Create `/Users/houkjang/projects/stand-alone-analyzer/tests/test_no_streamlit.py`:

```python
"""Plan 5 Task 24 (pinned decision #12) — guard against any future
Streamlit re-introduction.

Greps the source tree at test-collection time. The test fails if ANY
Streamlit token leaks back into src/, tests/, or app/. Excludes:
- this file (which contains the literal `streamlit` strings as test data),
- /tmp / cache / venv directories (not part of the package).
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATTERN = r"^import streamlit|^from streamlit|streamlit\."
SCAN_DIRS = ["src", "tests", "app"]


def _grep(pattern: str, paths: list[str]) -> list[str]:
    """Return matching lines as `path:lineno:line`. Empty list if no match."""
    real_paths = [str(REPO_ROOT / p) for p in paths if (REPO_ROOT / p).exists()]
    if not real_paths:
        return []
    result = subprocess.run(
        ["grep", "-rEn", "--include=*.py", pattern, *real_paths],
        capture_output=True,
        text=True,
    )
    # grep returns 1 when nothing matched — that's our happy path.
    if result.returncode not in (0, 1):
        raise RuntimeError(f"grep failed: rc={result.returncode} stderr={result.stderr!r}")
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def test_no_streamlit_imports_in_source_tree():
    """No `import streamlit`, `from streamlit`, or `streamlit.` token outside this file."""
    matches = _grep(PATTERN, SCAN_DIRS)
    self_path = str(Path(__file__).resolve())
    foreign = [m for m in matches if not m.startswith(self_path)]
    assert foreign == [], (
        "Plan 5 cutover guard FAILED — Streamlit reference re-appeared:\n"
        + "\n".join(foreign)
    )


def test_streamlit_module_is_not_importable_from_package():
    """`flake_analysis.ui` must not exist anywhere in the package tree."""
    ui_dir = REPO_ROOT / "src" / "flake_analysis" / "ui"
    assert not ui_dir.exists(), (
        f"Plan 5 cutover guard FAILED — {ui_dir} was re-introduced. "
        "The Streamlit UI was deleted by Plan 5 Task 14; do not restore it."
    )


def test_streamlit_app_entrypoint_is_gone():
    """`app/streamlit_app.py` must not exist."""
    entry = REPO_ROOT / "app" / "streamlit_app.py"
    assert not entry.exists(), (
        f"Plan 5 cutover guard FAILED — {entry} was re-introduced. "
        "The Streamlit entrypoint was deleted by Plan 5 Task 12."
    )


def test_grep_pattern_self_test():
    """Sanity check: the regex matches a known-positive line.

    Without this, a future refactor that quietly broke the regex would
    make the guard above silently pass.
    """
    positive = "import streamlit as st"
    assert re.search(PATTERN, positive) is not None, "guard regex broke"
    negative = "import pandas as pd"
    assert re.search(PATTERN, negative) is None, "guard regex over-matches"
```

- [ ] **Step 2: Run the test**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_no_streamlit.py -v`
Expected: 4/4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_no_streamlit.py
git commit -m "test: guard tests asserting Streamlit is fully removed"
```

---

#### Task 25: `tests/test_pyproject_clean.py` — assert `streamlit` and `plotly` absent from `pyproject.toml`

**Files:**
- Create: `/Users/houkjang/projects/stand-alone-analyzer/tests/test_pyproject_clean.py`

- [ ] **Step 1: Write the test**

Create `/Users/houkjang/projects/stand-alone-analyzer/tests/test_pyproject_clean.py`:

```python
"""Plan 5 Task 25 — assert post-cutover `pyproject.toml` is clean.

Specifically:
- `streamlit` does not appear in [project].dependencies or anywhere
  else in the file.
- `plotly` does not appear anywhere in the file.
- `version` is on or after 0.3.0.
- `description` no longer claims the package is a Streamlit app.
"""
from __future__ import annotations
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_pyproject_exists():
    assert PYPROJECT.exists(), f"missing {PYPROJECT}"


def test_streamlit_not_in_runtime_deps():
    deps = _load()["project"]["dependencies"]
    for dep in deps:
        assert "streamlit" not in dep.lower(), (
            f"Plan 5 Task 18 FAILED — streamlit re-appeared in dependencies: {dep!r}"
        )


def test_plotly_not_in_runtime_deps():
    deps = _load()["project"]["dependencies"]
    for dep in deps:
        assert "plotly" not in dep.lower(), (
            f"Plan 5 Task 18 FAILED — plotly re-appeared in dependencies: {dep!r}"
        )


def test_streamlit_not_anywhere_in_file():
    text = PYPROJECT.read_text(encoding="utf-8")
    # Allow the literal in a hypothetical comment if a future maintainer
    # writes something like `# Note: Streamlit removed in Plan 5`. Fail
    # on any real declaration.
    declarations = re.findall(r'"streamlit[^"]*"', text)
    assert declarations == [], (
        f"streamlit still declared somewhere in pyproject.toml: {declarations}"
    )


def test_plotly_not_anywhere_in_file():
    text = PYPROJECT.read_text(encoding="utf-8")
    declarations = re.findall(r'"plotly[^"]*"', text)
    assert declarations == [], (
        f"plotly still declared somewhere in pyproject.toml: {declarations}"
    )


def test_version_is_at_least_0_3_0():
    version = _load()["project"]["version"]
    parts = version.split(".")
    assert len(parts) >= 3, f"unexpected version shape: {version!r}"
    major, minor, *_ = (int(p) for p in parts[:2] + [0])
    # 0.3.0+ OR 1.x+
    assert (major == 0 and minor >= 3) or major >= 1, (
        f"version {version!r} is older than 0.3.0 — Plan 5 Task 19 not yet applied?"
    )


def test_description_is_not_streamlit():
    description = _load()["project"]["description"]
    assert "Streamlit" not in description, (
        f"description still claims Streamlit: {description!r}"
    )
    assert "streamlit" not in description.lower(), (
        f"description still mentions streamlit: {description!r}"
    )
```

- [ ] **Step 2: Run the test**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_pyproject_clean.py -v`
Expected: 7/7 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pyproject_clean.py
git commit -m "test: assert pyproject.toml is free of streamlit/plotly and at v0.3.0+"
```

---

### Phase 8 — End-to-end smoke + final commit

#### Task 26: Run the manual smoke checklist on a clean dev tree

**Files:** none modified.

**Goal:** prove the post-cutover tree boots end-to-end. Pinned decision #11 keeps this manual; full Playwright E2E is deferred. The checklist is the same as the one in `docs/operations/runbook.md` (Task 21), executed locally with the dev backend instead of the systemd-managed one.

- [ ] **Step 1: Build the production frontend bundle**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npm run build`
Expected: exit 0; `web/dist/index.html` and `web/dist/assets/index-*.js` exist.

- [ ] **Step 2: Boot uvicorn against the FastAPI app**

Run: `SAA_LOG_LEVEL=info /Users/houkjang/anaconda3/bin/uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000`
Expected: startup banner `Stand-Alone Analyzer API v0.3.0 starting...` (printed by `lifespan` in `src/flake_analysis/api/main.py`); no traceback.

- [ ] **Step 3: Hit the health endpoint**

In another shell: `curl -s http://127.0.0.1:8000/api/v1/health | jq`
Expected: a JSON document with `ok=true` (the exact field set was wired in Plan 1; no Plan 5 task changes it).

- [ ] **Step 4: Open the SPA against the dev server**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npm run dev`
Open `http://localhost:5173/` in a browser. Expected:
- Index page renders without a JavaScript console error.
- The Compute / Selector / Clustering / Explorer tabs are visible (each tab's actual readiness depends on whether Plans 1-4 are merged on the cutover branch; for Plan 5 ON ITS OWN, Compute / Selector / Clustering must render — Plan 4's Explorer is a precondition per the brief).

- [ ] **Step 5: Drive the workflow end-to-end (manual)**

Pick the most recently used `analysis_folder/` in the SPA's sidebar. Confirm:

1. Compute tab — the 7 step statuses load (or display "not yet computed" for fresh projects).
2. Selector tab — scatter renders, brushing/lasso work, axis-picker switches.
3. Clustering tab — labels load (or empty-state CTA for a fresh project).
4. Explorer tab — 60×60 mosaic loads in <2s. Pan + zoom are smooth.

Document any failure inline in the cutover PR description; do NOT silently "fix" by reverting Plan 5 commits — Phase 1 already proved the baseline was green.

- [ ] **Step 6: Restart uvicorn — confirm state persists**

Stop uvicorn (`Ctrl+C` in the dev shell). Re-launch it. Reload the SPA tab. Confirm the previously selected `analysis_folder/` is still loaded (manifest.json on disk; not in the dead process). This is the deployment-design §10.3 step-7 check, locally.

- [ ] **Step 7: No commit**

Verification only. The next task adds the "smoke-checklist passed" marker via a CHANGELOG entry.

---

#### Task 27: Run the full backend test suite one more time (final regression gate)

**Files:** none modified.

- [ ] **Step 1: Run pytest**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/ -v`
Expected: every test PASSES; count matches the Phase 7 Task 23 expected total.

- [ ] **Step 2: Confirm specific guard tests are in the run**

Run: `/Users/houkjang/anaconda3/bin/python -m pytest tests/test_no_streamlit.py tests/test_pyproject_clean.py tests/test_xaccel_thumbnails.py tests/test_nginx_config_syntax.py tests/test_systemd_unit_shape.py tests/test_deploy_script_shape.py -v`
Expected: every guard test PASSES.

- [ ] **Step 3: No commit**

Final regression gate. Failures here require investigation, not workaround.

---

#### Task 28: Run the full frontend test suite one more time

**Files:** none modified.

- [ ] **Step 1: Run vitest**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx vitest run`
Expected: every test PASSES.

- [ ] **Step 2: Run the typecheck**

Run from `/Users/houkjang/projects/stand-alone-analyzer/web`: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: No commit**

Verification only.

---

#### Task 29: Final cutover summary commit (CHANGELOG marker)

**Files:**
- Create or modify: `/Users/houkjang/projects/stand-alone-analyzer/CHANGELOG.md` (if absent, create; if present, prepend the new entry).

- [ ] **Step 1: Check whether CHANGELOG.md exists**

Run: `ls /Users/houkjang/projects/stand-alone-analyzer/CHANGELOG.md 2>&1`
Expected outcome: either the file exists or `No such file or directory`.

- [ ] **Step 2: Prepend (or write) the v0.3.0 entry**

If `CHANGELOG.md` does not exist, write it with the following content. If it exists, prepend the `## v0.3.0` block above the existing entries (preserving the rest of the file unchanged):

```markdown
# Changelog

## v0.3.0 — 2026-05-21 — React + FastAPI cutover

This is the cutover release. The Streamlit UI is removed; the React +
FastAPI stack is the only supported runtime.

### Removed
- `app/streamlit_app.py` entrypoint.
- `src/flake_analysis/ui/` (sidebar, brushing, image_preview, tab_compute, tab_selector, tab_clustering, tab_explorer).
- `streamlit>=1.32` and `plotly>=5.18` from `pyproject.toml`.
- Streamlit-only tests: `test_brushing.py`, `test_image_preview.py`, `test_explorer_mosaic_helpers.py`, `test_selector_filter_persistence.py`.

### Added
- `deploy/nginx/stand-alone-analyzer.conf` — nginx server config (verbatim port of deployment-design §2.1).
- `deploy/systemd/saa-api.service` — systemd unit (Restart=on-failure, KillMode=mixed, User=<EDIT-ME>).
- `deploy/scripts/deploy.sh` — atomic symlink-rotation deploy script.
- `docs/operations/runbook.md` — install / restart / log / rollback recipes.
- Cutover guard tests: `tests/test_no_streamlit.py`, `tests/test_pyproject_clean.py`.
- Deploy-artifact shape tests: `tests/test_nginx_config_syntax.py`, `tests/test_systemd_unit_shape.py`, `tests/test_deploy_script_shape.py`.
- `tests/test_xaccel_thumbnails.py` — verifies Phase 3 X-Accel-Redirect conversion.

### Changed
- `pyproject.toml` `description` — "Streamlit app for…" → "React + FastAPI app for…".
- `pyproject.toml` `version` — `0.2.18` → `0.3.0`.
- `src/flake_analysis/__init__.py` — `__version__` bumped, docstring rewritten.
- `src/flake_analysis/api/routes/static.py` — thumbnail route now emits `X-Accel-Redirect` when `00_thumbnails/index.json["cache_dir"]` is present (deployment-design §2.2 Option B); legacy in-folder layouts keep the `FileResponse` fallback.
- `tests/test_imports.py` — dropped `flake_analysis.ui` import; relaxed version assertion.
- `tests/test_pipeline_selector.py` — inlined `_values_for_axis` helper.
- `README.md` — Quick-start rewritten for `npm run build` + uvicorn.
- `CONTRIBUTING.md` — Streamlit dev-loop dropped; React + FastAPI dev-loop added.

### Rollback
`git revert` the cutover PR. There is no on-host parallel-run; the Streamlit code is gone.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG entry for v0.3.0 cutover"
```

---

## Self-Review Notes

### Spec coverage check

| deployment-design.md section | Plan 5 task |
|---|---|
| §1 Topology (browser → nginx → uvicorn → SMB / cache) | Phase 2 Tasks 4-6 (artifacts) + Phase 3 Task 7 (X-Accel-Redirect cache path) |
| §2 Static asset layout | Phase 2 Task 4 (`/usr/share/stand-alone-analyzer/web` root) |
| §2.1 nginx routes (verbatim) | Phase 2 Task 4 |
| §2.2 X-Accel-Redirect tile path (Option B) | Phase 3 Task 7 |
| §2.3 Cache-Control summary | Phase 2 Task 4 (nginx headers) + Phase 3 Task 7 (route headers preserved) |
| §3 SMB mount expectations | Phase 6 Task 21 (runbook references) |
| §4 Local-disk cache (cache_dir resolution + symlink) | Phase 3 Task 7 (cache_dir resolver) + Phase 6 Task 21 (install symlink recipe) |
| §5.1 systemd unit (Restart=on-failure, KillMode=mixed, HOME, ExecStart) | Phase 2 Task 5 |
| §5.3 graceful-shutdown verification | Phase 6 Task 21 (runbook) + Phase 8 Task 26 step 6 |
| §7 TLS / auth posture | Phase 6 Task 21 (post-v1 notes) |
| §8.1 dev mode | Phase 6 Task 22 (CONTRIBUTING dev loop) |
| §8.3 SAA_* env vars | Phase 2 Task 5 (`Environment=` lines) + Phase 6 Task 21 (runbook table) |
| §9 Observability (journalctl) | Phase 6 Task 21 (log recipes) |
| §10.3 cutover plan | Phase 8 Task 26 (manual smoke mirrors §10.3 steps) |

### Pinned-decision enforcement

- #1 Streamlit removal: Phase 5 Task 18.
- #2 Plotly removal: Phase 5 Task 18 + Phase 7 Task 25 guard.
- #3 httpx kept: no task removes it (`tests/api/conftest.py` consumer remains).
- #4 Test deletion safety: Phase 4 Tasks 11, 15, 16 (each preceded by grep).
- #5 Atomic cutover: Phase 4 Tasks 12-17 (single PR, no flag).
- #6 Version bump 0.2.18 → 0.3.0 in single commit: Phase 5 Task 19.
- #7 Deploy artifacts under `deploy/`: Phase 2 Tasks 4-6.
- #8 Rollback = `git revert`: Phase 6 Task 21 + Phase 8 Task 29 CHANGELOG.
- #9 No new CI: no task touches `.github/` or hooks.
- #10 README minimal rewrite: Phase 6 Task 20.
- #11 Manual smoke only: Phase 8 Task 26 (no Playwright dep added).
- #12 Streamlit reference grep guard: Phase 7 Task 24.

### Placeholder scan

Searched for `TODO`, `TBD`, `implement here`, `similar to` — none in this plan.

Every test step shows the complete function body or test block with real assertions. Every commit message conforms to the brief's exact prefixes (`feat(deploy):`, `feat(api):`, `chore(deps):`, `chore(cutover):`, `docs:`, `test:`).

### Type / name consistency

- `safe_join`, `ThumbnailMissing`, `_thumb_etag` (Phase 3 Task 7): symbols imported from the existing Plan 4 `routes/static.py`. No phantom imports.
- `Manifest`, `get_manifest`, `get_current_user`, `User`, `Response`, `FileResponse`, `APIRouter`, `Depends`, `Path`, `json` (Phase 3 Task 7): all already in `routes/static.py` from Plan 4 (the new task adds `Response` and reuses the existing `FileResponse` import).
- `flake_analysis.api.main:app` (Phase 2 Task 5): verified by reading `src/flake_analysis/api/main.py` (Plan 1's app factory; line 58 binds `app = create_app()`).
- `__version__` shape (Phase 4 Task 17): the relaxed assertion (`re.match(r"^\d+\.\d+\.\d+", ...)`) accepts both `0.2.18` and `0.3.0`, so Task 17 (after deletion, before bump) and Task 19 (after bump) both pass.

### Spec ambiguity resolved

- **deployment-design §2.1 uses `/api/v1/projects/<id>/tiles/lod1/<stem>.webp`; Plan 4 wired `/api/v1/projects/<id>/static/thumbnails/lod{N}/<stem>.webp`.** Plan 5 honors Plan 4's URL since it's already merged. The X-Accel-Redirect target path (`/_tiles_internal/<sha>/lodN/<stem>.webp`) follows deployment-design §2.1 verbatim — that's the URL nginx serves the bytes from, so the spec match is on the *target*, not the *source* URL.
- **Legacy in-folder layout fallback.** deployment-design §2.2 doesn't explicitly cover the v0.2.15 layout where `cache_dir` is absent from `index.json` (`core/pipeline/thumbnails.py:35-37`). Phase 3 Task 7 keeps the Plan 4 `FileResponse` for that case so existing analysis folders still load. Documented in the route docstring + the third test in `test_xaccel_thumbnails.py`.
- **`User=<EDIT-ME>` placeholder.** deployment-design §5.1 uses `User=saa` directly. The brief overrides this with a `<EDIT-ME>` placeholder so the deploy operator must consciously fill in the service account. Phase 2 Task 5 enforces the placeholder via a dedicated test (`test_unit_has_user_placeholder`).
- **Streamlit AppTest test.** `tests/test_selector_filter_persistence.py` was not on the brief's deletion list but boots `streamlit.testing.v1.AppTest`. Plan 5 Task 16 deletes it because Pinned decision #5 (atomic cutover) implies the test is unsalvageable once Streamlit is gone — its only assertion target is Streamlit session-state behavior in the deleted UI.
- **`tests/test_imports.py::test_package_import` version assertion.** Was hard-coded at `0.2.2` (already stale). Phase 4 Task 17 relaxes it to a regex shape so future minor bumps don't break the test.

### Execution order constraint

- Task 13 (inline the `_values_for_axis` helper) MUST run before Task 14 (delete `src/flake_analysis/ui/`). Otherwise `tests/test_pipeline_selector.py::test_values_for_axis_returns_correct_columns` fails on the very next pytest run, blocking Task 14's commit.
- Task 17 (update `tests/test_imports.py`) MUST run after Task 14 (delete `ui/`) so the test stops importing the deleted module. Until Task 17 commits, every pytest run between Task 14 and Task 17 has one expected failure (`test_subpackages_importable`); this is documented in Task 14 step 5.
- Task 19 (version bump) MUST run after Task 17 (relaxed version assertion) to avoid an interlocked commit (otherwise the bump and the test relaxation must land together).
- Task 24 (no-streamlit guard) MUST run after Task 18 (drop deps) — otherwise the guard's grep would still match the live `import streamlit` lines in `src/flake_analysis/ui/`. (It runs after Task 14 in any case, but documenting this constraint inline.)
- Task 25 (pyproject-clean guard) MUST run after Task 19 (version bump) — otherwise `test_version_is_at_least_0_3_0` fails.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-cutover-streamlit-deletion.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review (spec compliance, then code quality) between tasks, fast iteration in this same session.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?

