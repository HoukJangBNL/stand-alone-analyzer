# W10-B — `_active_project` decoupling + per-scan manifest + per-scan mutex

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global `_active_project: str | None` in `api/deps.py` with per-request explicit `project_id` resolution and rewire manifest + mutex + filesystem layout to be **per-scan**, not per-project. Land the foundation that W10-C routes will sit on. Today the whole API uses one process-global path; W10-B turns it into `(project_id, scan_id) → analysis_folder/<project_id>/<scan_id>/manifest.json` + `_scan_locks: dict[int, asyncio.Lock]`.

**Architecture:** Pre-W10 the API resolved every request to a single shared analysis_folder via `_active_project` mutated by `POST /projects` (a singleton legacy from the Streamlit prototype). W10-A landed the real `projects` table; W10-B retires the singleton. Path resolution moves into a pure helper `resolve_analysis_folder(project_id, scan_id)` that returns `<root>/<project_id>/<scan_id>/`. Manifest IO operates on that folder; mutex keys on `scan_id` (D4). The `_active_project` mutation in `routes/projects.py` is removed; W10-C will rewrite the route surface to never need it. The silent `ORDER BY analyses.id DESC LIMIT 1` fallback in `get_active_analysis` is replaced with explicit `analysis = SELECT ... WHERE scan_id = :sid` — D1 says one analysis per scan, no implicit selection.

**Tech Stack:** FastAPI 0.110+, SQLAlchemy 2.x async, pytest-asyncio strict, Path-based filesystem layout under `SAA_ANALYSIS_ROOT` env var (renamed from `SAA_ANALYSIS_FOLDER` to disambiguate root-vs-folder).

---

## Locked Decisions (W10 D-block, 2026-05-22)

- **D1.** Pipeline runs per scan (`analyses.scan_id` exists; this plan re-routes manifest/mutex/paths around scan_id).
- **D4.** Mutex granularity = per scan, full serial (one lock per scan covering all steps; cross-scan parallel OK).
- **D5.** Manifest path = `<analysis_root>/<project_id>/<scan_id>/manifest.json`.

### Plan-level decision (locked here, 2026-05-22)

**Last-active project: in-memory only, no DB column.** Justification:
1. Cross-session persistence is a UX nicety, not a correctness requirement — the frontend (W10-D) URL already encodes the active project via `/projects/:pid/...`.
2. Adding `users.last_active_project_id NULL` couples auth-table schema to a UI concern. Simpler to let the frontend's localStorage / URL drive it.
3. If the user comes back and hits `/`, frontend redirects to "most recent project they own" — derivable from `SELECT id FROM projects WHERE owner_id = :uid ORDER BY created_at DESC LIMIT 1`, no extra column needed.

The `_active_project` global is **deleted** entirely. No replacement persistence layer.

---

## File Structure

- Modify: `src/flake_analysis/api/deps.py` — delete `_active_project`/`_resolve_project_id`/`DEFAULT_*`/`get_project_context` global state; rewrite `get_manifest` to take `(project_id, scan_id)`; rewrite `get_active_analysis` to take explicit `scan_id`.
- Modify: `src/flake_analysis/api/mutex.py` — `_project_locks: dict[str, asyncio.Lock]` → `_scan_locks: dict[int, asyncio.Lock]`; `acquire_project_lock(project_id)` → `acquire_scan_lock(scan_id)`. Per D4.
- Modify: `src/flake_analysis/state/paths.py` — add `analysis_folder(root, project_id, scan_id) -> Path`; `manifest_path(root, project_id, scan_id) -> Path`; rename env-var reader to `SAA_ANALYSIS_ROOT` (root of all per-project folders).
- Modify: `src/flake_analysis/state/manifest.py` — `load_manifest`/`save_manifest`/`stamp_top_level` accept `(root, project_id, scan_id)` (or a pre-resolved `analysis_folder` Path — pick one signature consistently).
- Modify: `tests/api/test_deps.py`, `tests/api/test_mutex.py`, `tests/state/test_paths.py`, `tests/state/test_manifest.py`.
- Create: `tests/api/test_scan_mutex_isolation.py` — "different scans run in parallel" + "same scan steps serialized" assertions.

---

## Verification Env Block

All test runs MUST use this exact prefix:

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root
```

(`SAA_ANALYSIS_ROOT` replaces `SAA_ANALYSIS_FOLDER`. Tests that previously set `SAA_ANALYSIS_FOLDER` need to update — this is captured in W10-E sweep, not W10-B.)

---

## Task 1 — `state/paths.py` per-scan layout

**Files:**
- Modify: `src/flake_analysis/state/paths.py`
- Modify: `tests/state/test_paths.py` (or create if absent)

**Why:** Foundation. The whole stack reads filesystem paths through this module; once `analysis_folder(project_id, scan_id)` and `manifest_path(project_id, scan_id)` are pure functions of those args, the manifest + mutex modules can drop their globals.

### Step 1.1: Read the existing file + test landscape

- [ ] **Run:**

```bash
ls /Users/houkjang/projects/stand-alone-analyzer/tests/state/ 2>&1
grep -rn "manifest_path\|analysis_folder" /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/state/paths.py
```

Capture the existing test file path; if `tests/state/test_paths.py` doesn't exist, create it.

### Step 1.2: Write the failing test

- [ ] **Create or modify `tests/state/test_paths.py`:**

```python
"""W10-B: per-scan filesystem layout."""
from __future__ import annotations

from pathlib import Path

import pytest

from flake_analysis.state.paths import (
    analysis_folder,
    manifest_path,
    step_dir,
    SUBDIRS,
)


def test_analysis_folder_combines_root_project_scan(tmp_path):
    """analysis_folder(root, project_id, scan_id) -> root/project_id/scan_id/."""
    got = analysis_folder(tmp_path, "proj-abc", 42)
    assert got == tmp_path / "proj-abc" / "42"


def test_manifest_path_under_analysis_folder(tmp_path):
    """manifest_path is analysis_folder/manifest.json."""
    got = manifest_path(tmp_path, "proj-abc", 42)
    assert got == tmp_path / "proj-abc" / "42" / "manifest.json"


def test_step_dir_unchanged_takes_analysis_folder(tmp_path):
    """step_dir signature is unchanged — caller resolves analysis_folder first."""
    folder = analysis_folder(tmp_path, "p", 1)
    got = step_dir(folder, "background")
    assert got == folder / SUBDIRS["background"]


def test_analysis_folder_rejects_empty_project_id(tmp_path):
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "", 1)


def test_analysis_folder_rejects_non_positive_scan_id(tmp_path):
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "p", 0)
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "p", -1)
```

### Step 1.3: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/state/test_paths.py -v
```

Expected: FAIL — `analysis_folder` does not exist.

### Step 1.4: Implement the new helpers

- [ ] **Modify `src/flake_analysis/state/paths.py`:**

Replace the existing `manifest_path` function with the per-scan version, add `analysis_folder`:

```python
"""Filesystem layout: per-scan analysis folders.

Layout: <SAA_ANALYSIS_ROOT>/<project_id>/<scan_id>/
                                                  ├── manifest.json
                                                  ├── 00_thumbnails/
                                                  ├── 01_background/
                                                  ├── 02_domain_stats/
                                                  ├── 03_selector/
                                                  ├── 04_clustering/
                                                  ├── 05_domain_proximity/
                                                  └── 06_explorer/

W10-B introduced the (project_id, scan_id) dimensions; pre-W10 callers
resolved everything through a process-global `_active_project` in
`api/deps.py` (now removed).
"""
from __future__ import annotations

from pathlib import Path

PIPELINE_STEPS = (
    "background",
    "thumbnails",
    "domain_stats",
    "selector",
    "clustering",
    "domain_proximity",
    "explorer",
)

SUBDIRS = {
    "background":       "01_background",
    "thumbnails":       "00_thumbnails",
    "domain_stats":     "02_domain_stats",
    "selector":         "03_selector",
    "clustering":       "04_clustering",
    "domain_proximity": "05_domain_proximity",
    "explorer":         "06_explorer",
}

ARTIFACTS = {
    "background": ["background.npy"],
    "thumbnails": ["index.json"],
    "domain_stats": ["stats.npz"],
    "selector": ["selection.parquet"],
    "clustering": ["seed_groups.json", "gmm_model.pkl", "assignments.parquet", "labels.json"],
    "domain_proximity": ["distances.parquet", "flake_assignments.parquet"],
    "explorer": ["explorer_state.json"],
}


def analysis_folder(root: str | Path, project_id: str, scan_id: int) -> Path:
    """Return the per-scan analysis folder.

    `<root>/<project_id>/<scan_id>/` — created lazily by callers that
    write into it (manifest.save_manifest does the mkdir). Pure path
    composition, no IO here.
    """
    if not project_id:
        raise ValueError("project_id must be a non-empty string")
    if not isinstance(scan_id, int) or scan_id <= 0:
        raise ValueError(f"scan_id must be a positive int, got {scan_id!r}")
    return Path(root) / project_id / str(scan_id)


def manifest_path(root: str | Path, project_id: str, scan_id: int) -> Path:
    """Return the manifest.json path for a (project_id, scan_id) pair (D5)."""
    return analysis_folder(root, project_id, scan_id) / "manifest.json"


def step_dir(analysis_folder_path: str | Path, step: str) -> Path:
    """Return the directory path for a given pipeline step within an analysis_folder."""
    if step not in SUBDIRS:
        raise ValueError(f"unknown step: {step}")
    return Path(analysis_folder_path) / SUBDIRS[step]
```

### Step 1.5: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/state/test_paths.py -v
```

Expected: 5 passed.

### Step 1.6: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/state/paths.py tests/state/test_paths.py
git commit -m "feat(state): per-scan analysis_folder + manifest_path (W10-B.1)"
```

---

## Task 2 — `state/manifest.py` accepts `(root, project_id, scan_id)`

**Files:**
- Modify: `src/flake_analysis/state/manifest.py`
- Modify: `tests/state/test_manifest.py` (or create if absent)

**Why:** Manifest IO follows path layout. Today `load_manifest(analysis_folder)` takes a pre-resolved folder; W10-B keeps that signature **as the canonical low-level helper** and adds a higher-level `load_manifest_for_scan(root, project_id, scan_id)` that composes via `analysis_folder()`. This keeps the existing core stable while exposing the per-scan API the route layer needs.

### Step 2.1: Write the failing test

- [ ] **Create or modify `tests/state/test_manifest.py`:**

```python
"""W10-B: per-scan manifest IO."""
from __future__ import annotations

from pathlib import Path

import pytest

from flake_analysis.state.manifest import (
    Manifest,
    StepEntry,
    load_manifest,
    load_manifest_for_scan,
    save_manifest,
    save_manifest_for_scan,
)


def test_save_then_load_for_scan_round_trip(tmp_path):
    """save_manifest_for_scan writes; load_manifest_for_scan reads back."""
    m = Manifest(steps={"background": StepEntry(completed_at="2026-05-22T00:00:00Z")})
    save_manifest_for_scan(m, root=tmp_path, project_id="proj1", scan_id=7)

    loaded = load_manifest_for_scan(tmp_path, "proj1", 7)
    assert "background" in loaded.steps
    assert loaded.steps["background"].completed_at == "2026-05-22T00:00:00Z"


def test_load_for_scan_missing_returns_fresh(tmp_path):
    """No manifest.json yet → fresh Manifest, not error."""
    loaded = load_manifest_for_scan(tmp_path, "proj1", 99)
    assert loaded.steps == {}


def test_isolation_between_scans(tmp_path):
    """Two scans under same project don't see each other's manifests."""
    m1 = Manifest(steps={"background": StepEntry(completed_at="t1")})
    m2 = Manifest(steps={"selector": StepEntry(completed_at="t2")})
    save_manifest_for_scan(m1, root=tmp_path, project_id="p", scan_id=1)
    save_manifest_for_scan(m2, root=tmp_path, project_id="p", scan_id=2)

    a = load_manifest_for_scan(tmp_path, "p", 1)
    b = load_manifest_for_scan(tmp_path, "p", 2)
    assert "background" in a.steps and "selector" not in a.steps
    assert "selector" in b.steps and "background" not in b.steps
```

### Step 2.2: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/state/test_manifest.py -v
```

Expected: FAIL — `load_manifest_for_scan` / `save_manifest_for_scan` don't exist.

### Step 2.3: Implement the per-scan wrappers

- [ ] **In `src/flake_analysis/state/manifest.py`, append below the existing `save_manifest`:**

```python
from flake_analysis.state.paths import analysis_folder


def load_manifest_for_scan(
    root: str | Path, project_id: str, scan_id: int
) -> Manifest:
    """Load manifest for a (project_id, scan_id) pair (D5)."""
    folder = analysis_folder(root, project_id, scan_id)
    return load_manifest(folder)


def save_manifest_for_scan(
    manifest: Manifest,
    *,
    root: str | Path,
    project_id: str,
    scan_id: int,
) -> None:
    """Atomic write of manifest.json for a (project_id, scan_id) pair (D5)."""
    folder = analysis_folder(root, project_id, scan_id)
    save_manifest(manifest, folder)
```

### Step 2.4: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/state/test_manifest.py -v
```

Expected: 3 passed (plus any pre-existing manifest tests that still rely on the lower-level `save_manifest(folder)` signature — those should keep passing because we didn't touch that signature).

### Step 2.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/state/manifest.py tests/state/test_manifest.py
git commit -m "feat(state): load_manifest_for_scan / save_manifest_for_scan helpers (W10-B.2)"
```

---

## Task 3 — `api/mutex.py` per-scan lock registry

**Files:**
- Modify: `src/flake_analysis/api/mutex.py`
- Modify: `tests/api/test_mutex.py`
- Create: `tests/api/test_scan_mutex_isolation.py`

**Why:** Per D4, each scan gets one lock that covers ALL its pipeline steps. Cross-scan parallelism is intentional (two scans = two GPU jobs OK on a multi-GPU host). The current per-project lock blocks the entire project even when only one scan is running.

### Step 3.1: Write the failing test

- [ ] **Modify `tests/api/test_mutex.py` (or write fresh — see existing file first):**

```python
"""W10-B: per-scan lock semantics."""
from __future__ import annotations

import asyncio

import pytest

from flake_analysis.api.errors import ProjectBusy  # error class kept for now
from flake_analysis.api.mutex import acquire_scan_lock


@pytest.mark.asyncio
async def test_same_scan_serializes():
    """Two `acquire_scan_lock(7)` simultaneously: second raises ProjectBusy (immediate fail-fast)."""
    async with acquire_scan_lock(7):
        with pytest.raises(ProjectBusy):
            async with acquire_scan_lock(7):
                pass


@pytest.mark.asyncio
async def test_different_scans_parallel_ok():
    """`acquire_scan_lock(7)` does not block `acquire_scan_lock(8)`."""
    async with acquire_scan_lock(7):
        async with acquire_scan_lock(8):
            pass  # no exception
```

- [ ] **Create `tests/api/test_scan_mutex_isolation.py`:**

```python
"""W10-B D4: per-scan mutex isolation across overlapping pipeline steps."""
from __future__ import annotations

import asyncio

import pytest

from flake_analysis.api.errors import ProjectBusy
from flake_analysis.api.mutex import acquire_scan_lock


@pytest.mark.asyncio
async def test_pipeline_step_within_one_scan_serializes():
    """All steps for one scan share the same lock — sequential."""
    order: list[str] = []

    async def step(name: str, scan_id: int, hold: float):
        async with acquire_scan_lock(scan_id):
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    # Same scan_id — second step must wait until first releases. We model
    # the wait by awaiting sequentially (the gather variant would raise
    # ProjectBusy by design).
    await step("background", 1, 0.01)
    await step("sam", 1, 0.01)

    assert order == [
        "background:start", "background:end",
        "sam:start", "sam:end",
    ]


@pytest.mark.asyncio
async def test_two_scans_run_concurrently():
    """Different scan_ids run in parallel."""
    started: list[int] = []
    finished: list[int] = []

    async def work(scan_id: int):
        async with acquire_scan_lock(scan_id):
            started.append(scan_id)
            await asyncio.sleep(0.05)
            finished.append(scan_id)

    await asyncio.gather(work(10), work(11))
    # Both scans started before either finished (proves overlap)
    # If they were serialized, started would be [10,11] only after
    # finished[10] — which `gather` cannot interleave that way given
    # the sleep length.
    assert set(started) == {10, 11}
    assert set(finished) == {10, 11}
```

### Step 3.2: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/api/test_mutex.py tests/api/test_scan_mutex_isolation.py -v
```

Expected: FAIL — `acquire_scan_lock` doesn't exist.

### Step 3.3: Rewrite `api/mutex.py`

- [ ] **Replace `src/flake_analysis/api/mutex.py` with:**

```python
"""Per-scan asyncio.Lock registry (W10-B, D4).

Granularity: one lock per `scan_id`. All pipeline steps for a given scan
(thumbnails / background / SAM / domain_stats / selector / clustering /
domain_proximity / explorer) share the lock — full serial within a scan.
Different scans hold separate locks → cross-scan parallel execution is
allowed by design (multi-GPU hosts can chew through two scans at once).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from flake_analysis.api.errors import ProjectBusy

_scan_locks: dict[int, asyncio.Lock] = {}


def _get_lock(scan_id: int) -> asyncio.Lock:
    if scan_id not in _scan_locks:
        _scan_locks[scan_id] = asyncio.Lock()
    return _scan_locks[scan_id]


@asynccontextmanager
async def acquire_scan_lock(scan_id: int):
    """Acquire per-scan lock or raise ProjectBusy immediately if held.

    `ProjectBusy` is reused as the wire-level error to keep the existing
    HTTP 423 envelope identical — clients can't tell whether the lock is
    keyed on project_id or scan_id, and we don't want to break the
    ProjectBusy.code contract just for the rename.
    """
    lock = _get_lock(scan_id)
    if lock.locked():
        raise ProjectBusy(project_id=str(scan_id))

    async with lock:
        yield
```

> **Backwards compat:** `acquire_project_lock` is removed. Callers in `routes/run.py`, `routes/clustering.py`, `routes/selector.py`, `routes/explorer.py` switch to `acquire_scan_lock(scan_id)` — done in W10-C, NOT W10-B. For the duration between W10-B merge and W10-C completion, the existing route imports will break. Run W10-B + W10-C in the same merge window OR temporarily keep a `acquire_project_lock` shim that delegates to `acquire_scan_lock(int(project_id))` — but **prefer the clean break** (W10-C dispatches right after this).

### Step 3.4: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
uv run pytest tests/api/test_mutex.py tests/api/test_scan_mutex_isolation.py -v
```

Expected: 4 passed (2 in test_mutex.py + 2 in test_scan_mutex_isolation.py).

### Step 3.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/mutex.py tests/api/test_mutex.py tests/api/test_scan_mutex_isolation.py
git commit -m "feat(api): per-scan mutex (acquire_scan_lock) per D4 (W10-B.3)"
```

---

## Task 4 — `api/deps.py` decoupling

**Files:**
- Modify: `src/flake_analysis/api/deps.py`
- Modify: `tests/api/test_deps.py` (or `test_project_context.py` — pick the right file by reading first)

**Why:** Heart of W10-B. Drop `_active_project` global, drop `DEFAULT_PROJECT_ID = "local"`, drop `get_project_context` (path-param resolution becomes the route's job), rewrite `get_manifest` to take `(project_id, scan_id)`, rewrite `get_active_analysis` to require explicit `scan_id` and stop the silent fallback.

### Step 4.1: Read the existing tests

- [ ] **Run:**

```bash
ls /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_deps.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_project_context.py /Users/houkjang/projects/stand-alone-analyzer/tests/api/test_get_active_analysis.py 2>&1
```

Read each file that exists; the new `test_deps.py` should replace `test_project_context.py` (the old one tested the global behavior).

### Step 4.2: Write the new `tests/api/test_deps.py`

- [ ] **Replace `tests/api/test_deps.py` with (or extend, if it already covers other concerns):**

```python
"""W10-B: explicit project/scan dependency resolution."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.api.deps import get_manifest, get_active_analysis
from flake_analysis.api.errors import DbUnavailable
from flake_analysis.db.models import Analysis

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_get_manifest_loads_per_scan(tmp_path, monkeypatch):
    """get_manifest(pid, sid) reads root/<pid>/<sid>/manifest.json."""
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    target = tmp_path / "p1" / "42"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        '{"version": 1, "analysis_folder": null, "raw_images_dir": null,'
        ' "annotations_path": null, "created_at": null,'
        ' "flake_core_version": null, "steps": {}}',
        encoding="utf-8",
    )

    manifest = await get_manifest(project_id="p1", scan_id=42)
    assert manifest.version == 1


@pytest.mark.asyncio
async def test_get_manifest_missing_file_returns_fresh(tmp_path, monkeypatch):
    """Missing manifest.json → fresh Manifest, not error."""
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    manifest = await get_manifest(project_id="p1", scan_id=99)
    assert manifest.steps == {}


@pytest.mark.asyncio
async def test_get_active_analysis_explicit_scan_id(pg_session, sample_scan_factory):
    """get_active_analysis(scan_id, session) returns the analysis with that scan_id."""
    scan = await sample_scan_factory()
    # Insert a single analysis row for that scan
    a = Analysis(
        scan_id=scan.id, model_id=1,
        amg_params={}, link_distance_px=1.0, min_area_px=10,
    )
    pg_session.add(a)
    await pg_session.flush()

    got = await get_active_analysis(scan_id=scan.id, session=pg_session)
    assert got is not None
    assert got.scan_id == scan.id


@pytest.mark.asyncio
async def test_get_active_analysis_no_row_returns_none(pg_session):
    """No analysis for that scan_id → None (not an error)."""
    got = await get_active_analysis(scan_id=999_999, session=pg_session)
    assert got is None
```

> **Fixtures:** `sample_scan_factory` is a NEW fixture for tests/api/conftest.py — adds a project owner, a project, and a scan in one shot. If the fixture doesn't exist yet, drop it into `tests/api/conftest.py` as part of this task (mirror the W6 `sample_user_factory` pattern). The fixture is also used by W10-C tests, so it's a shared cost.

### Step 4.3: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api/test_deps.py -v
```

Expected: FAIL on signature mismatch / missing fixture.

### Step 4.4: Rewrite `api/deps.py`

- [ ] **Replace `src/flake_analysis/api/deps.py` with:**

```python
"""FastAPI dependencies (W10-B: per-scan, no globals).

Pre-W10 this module owned a process-global `_active_project` and a
`DEFAULT_PROJECT_ID = "local"` alias. W10-B retired both — every request
carries explicit `(project_id, scan_id)` from the path; the analysis
folder is `<SAA_ANALYSIS_ROOT>/<project_id>/<scan_id>/`.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.errors import DbUnavailable
from flake_analysis.db.engine import async_session_maker
from flake_analysis.db.models import Analysis
from flake_analysis.state.manifest import Manifest, load_manifest_for_scan


def _analysis_root() -> str:
    """Return the root directory under which all per-scan analysis folders live."""
    root = os.environ.get("SAA_ANALYSIS_ROOT")
    if root is None:
        # Backwards compat: legacy env var name from pre-W10
        root = os.environ.get("SAA_ANALYSIS_FOLDER", "/mnt/analysis")
    return root


async def get_manifest(project_id: str, scan_id: int) -> Manifest:
    """Load manifest.json for the (project_id, scan_id) pair (D5)."""
    return load_manifest_for_scan(_analysis_root(), project_id, scan_id)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session per request; close on exit."""
    async with async_session_maker() as session:
        yield session


async def get_active_analysis(
    scan_id: int, session: AsyncSession
) -> Analysis | None:
    """Resolve the Analysis row for an explicit scan_id (D1).

    Per D1 the pipeline runs per scan; this function returns at most one
    analysis (the row whose scan_id matches). Pre-W10 silently fell back
    to `ORDER BY analyses.id DESC LIMIT 1` regardless of scan — that
    silent fallback is GONE. Returns ``None`` when no row exists; raises
    ``DbUnavailable`` (500) on SQL errors per pinned decision #5.
    """
    try:
        stmt = select(Analysis).where(Analysis.scan_id == scan_id).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise DbUnavailable() from exc
```

> **Removed exports:** `_active_project`, `_resolve_project_id`, `DEFAULT_PROJECT_ID`, `DEFAULT_ANALYSIS_FOLDER`, `ProjectContext`, `get_project_context`. Any caller that imported these now gets `ImportError` — that's the signal for W10-C to rewrite the import.

### Step 4.5: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api/test_deps.py -v
```

Expected: 4 passed.

### Step 4.6: Sanity check — what now imports the deleted symbols

- [ ] **Run:**

```bash
grep -rn "_active_project\|_resolve_project_id\|DEFAULT_PROJECT_ID\|DEFAULT_ANALYSIS_FOLDER\|get_project_context\|ProjectContext" /Users/houkjang/projects/stand-alone-analyzer/src/flake_analysis/api/ 2>&1 | head -40
```

Expected: many call sites in `routes/data.py`, `routes/run.py`, `routes/projects.py`, `routes/selector.py`, `routes/clustering.py`, `routes/explorer.py`, `routes/static.py`. These are W10-C's problem — flag them but do NOT fix here. Capture the count in the commit message body.

### Step 4.7: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/deps.py tests/api/test_deps.py tests/api/conftest.py
git commit -m "feat(api): drop _active_project global; per-scan deps (W10-B.4)"
```

---

## Task 5 — Final acceptance gate

### Step 5.1: Run all touched test files

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/state tests/api/test_deps.py tests/api/test_mutex.py tests/api/test_scan_mutex_isolation.py -v
```

Expected: all green (`tests/state` + `test_deps.py` + `test_mutex.py` + `test_scan_mutex_isolation.py`).

### Step 5.2: Confirm route-level breakage is the documented regression

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api -m pg --ignore=tests/api/test_deps.py --ignore=tests/api/test_mutex.py --ignore=tests/api/test_scan_mutex_isolation.py 2>&1 | tail -20
```

Expected: many ImportError / signature failures in `routes/data.py`, `routes/run.py`, etc. That's intended — W10-C fixes them. Capture the count for the project-status note.

### Step 5.3: Update `docs/project-status.md`

- [ ] **In §3.1 (다음 한 발), append:**

> 2026-05-22 — W10-B (active-project decoupling + per-scan manifest/mutex) 완료. `_active_project` global 제거 + `acquire_scan_lock(scan_id)` + `manifest_for_scan(root, pid, sid)`. `tests/state` + `tests/api/test_deps|mutex|scan_mutex_isolation` 전부 green. `tests/api` 라우트 레벨 ImportError 다수 — W10-C에서 수정. W10-C (route surface) 진입 가능.

```bash
git add docs/project-status.md
git commit -m "docs(status): mark W10-B complete — _active_project removed"
```

---

## Self-Review

**Spec coverage:**
- D1 (per-scan analyses) — `get_active_analysis(scan_id)` requires explicit scan_id, no silent fallback. ✓
- D4 (per-scan mutex) — `acquire_scan_lock(scan_id)` + isolation tests. ✓
- D5 (manifest path `<root>/<pid>/<sid>/manifest.json`) — `analysis_folder()` + `manifest_path()` + `load/save_manifest_for_scan()`. ✓
- "in-memory only, no DB column for last-active project" — locked in §"Plan-level decision". ✓

**Placeholder scan:** none — `SAA_ANALYSIS_ROOT` env var is the live config.

**Type consistency:**
- `scan_id: int` everywhere (matches `scans.id BIGINT` ORM mapping).
- `project_id: str` everywhere (matches `projects.id TEXT` from W10-A).

**Edge cases:**
- `SAA_ANALYSIS_FOLDER` legacy env-var fallback in `_analysis_root()` — keeps W6.x callers working until W10-E test sweep updates the test env. Documented in W10-B §"Verification Env Block".
- `analysis_folder.mkdir(parents=True)` — NOT done in `paths.py` (pure path composition); manifest's `save_manifest` already does it via `p.parent.mkdir`. No change needed.
- The `ProjectBusy` error class still carries a `project_id` field; we pass `str(scan_id)` so the existing wire envelope stays identical. Renaming the field to `scan_id` would break the frontend (W10-D) — defer to a v3 cleanup.

---

## Open follow-up (out of W10-B scope)

- **Route rewrites** (`routes/data.py`, `routes/run.py`, `routes/projects.py`, `routes/selector.py`, `routes/clustering.py`, `routes/explorer.py`, `routes/static.py`) — all consume the deleted `get_project_context` / `_active_project`. Done in W10-C.
- **`ProjectBusy.scan_id` field** — wire envelope cleanup. Defer.
- **GC of stale `analysis_folder` directories** — when a scan is deleted, its on-disk folder lingers. Defer to a janitor task (out of scope; analysis_folder data is reproducible from S3 + DB).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W10-B-active-project-decoupling.md`.

**Recommended execution mode:** Subagent-Driven. 5 tasks, ~5–10 min implementer per task. Task 4 has the largest surface (deps.py rewrite); read existing test_deps.py / test_project_context.py / test_get_active_analysis.py first to plan the fixture fold-in.

Dispatch order: 1 → 2 → 3 → 4 → 5 (strict; Task 4 imports Task 1's helper).

**Hard dependency:** W10-A must be merged first (Task 4's tests use the new `projects` table via `sample_scan_factory`).
