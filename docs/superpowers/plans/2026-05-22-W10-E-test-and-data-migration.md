# W10-E — Test sweep + data migration + acceptance gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the connective tissue around W10-A/B/C/D so that the merged result is provably green:
1. **Pre-flight DB wipe** for the local `saa_test` database (W10-A introduces a real `projects` FK in `scans` and `project_users`; existing rows must clear before the migration applies cleanly). The SQL itself ships in W10-A; this plan owns the operational wrapper, the documentation in `docs/db-ops.md`, and the dispatch sequencing.
2. **Backend test sweep** — 27 backend test files reference `projectId`/`active_project`/`SAA_ANALYSIS_FOLDER`/per-project URL grammar. Most need mechanical rewrites to the new per-scan grammar; some need a project + scan ORM fixture instead of `_active_project` mutation.
3. **New integration tests** that exercise W10's locked decisions: D2 (Project DELETE → 409 RESTRICT), D4 (per-scan `asyncio.Lock` isolation), D6 (force-create-project UX backend assertions).
4. **Acceptance gate** — a single composite check (`scripts/dev/w10-acceptance.sh`) that runs alembic up-to-head, the full backend test suite (PG + non-PG), and full vitest. Run it once after every W10-* PR lands; it is the merge gate for the W10 branch into `main`.

**Architecture:** Separation of concerns:
- The **pre-flight wipe SQL** lives in `scripts/db/wipe-saa-test-pre-w10.sql` (shipped by W10-A). W10-E adds a thin shell wrapper `scripts/db/wipe-saa-test.sh` so devops engineers (and the acceptance gate) call one command, plus the runbook entry in `docs/db-ops.md`.
- The **backend sweep** is split into 4 parallel batches by domain: (a) `tests/api/test_projects*.py` + `test_deps.py` + `test_project_context.py`, (b) `tests/api/test_run_*_sse.py` + `test_sse_heartbeat.py`, (c) `tests/api/test_data_*.py` + `test_static_*.py`, (d) `tests/api/test_selector_*.py` + `test_scans_*.py` + `test_clustering_mutex_sharing.py` + `test_mutex.py`. Each batch is independent and gets its own subagent.
- **New integration tests** go in `tests/api/test_w10_acceptance.py` (3 specs — one per locked decision under test). Existing `tests/db/test_w10_projects.py` from W10-A handles the DB-layer assertions; this file is the API-layer counterpart.
- **Acceptance gate** is a Bash script that delegates every domain command to `uv run` / `npm test` so it is cheap to re-run and obvious to read. It does NOT install deps — assumes the environment is bootstrapped (`uv sync` + `cd web && npm ci`).

**Tech Stack:** pytest-asyncio strict, pytest.mark.pg for PostgreSQL-bound tests, Vitest 1.4 + jsdom, alembic 1.13, psql 16. No new test runner introduced.

---

## Codebase Findings (verified 2026-05-22 against feat/migration-cutover head)

- **27 backend test files reference per-project URL grammar / `_active_project` / `SAA_ANALYSIS_FOLDER`** (verified via `grep -rln "projectId\|project_id\|/projects/\|active_project" tests --include="*.py"`):
  ```
  tests/api/conftest.py
  tests/api/test_admin_routes.py
  tests/api/test_auth_dep_dropin.py
  tests/api/test_clustering_mutex_sharing.py
  tests/api/test_data_annotation_preview.py
  tests/api/test_data_clustering_assignments.py
  tests/api/test_data_clustering_labels.py
  tests/api/test_data_clustering_seed_groups.py
  tests/api/test_data_domain_stats.py
  tests/api/test_data_explorer_flake_detail.py
  tests/api/test_data_explorer_flakes.py
  tests/api/test_data_explorer_grid.py
  tests/api/test_data_explorer_tile_manifest.py
  tests/api/test_data_manifest.py
  tests/api/test_data_selection.py
  tests/api/test_deps.py
  tests/api/test_get_active_analysis.py
  tests/api/test_guards.py
  tests/api/test_manifest_endpoint_db.py
  tests/api/test_mutex.py
  tests/api/test_path_validation.py
  tests/api/test_project_context.py
  tests/api/test_projects.py
  tests/api/test_run_*_sse.py    (8 files)
  tests/api/test_run_emits_usage.py
  tests/api/test_run_explorer_get_state.py
  tests/api/test_run_explorer_save_state.py
  tests/api/test_scans_complete.py
  tests/api/test_scans_create.py
  tests/api/test_scans_finalize.py
  tests/api/test_scans_presign.py
  tests/api/test_selector_commit.py
  tests/api/test_selector_export.py
  tests/api/test_sse_heartbeat.py
  tests/api/test_static_raw.py
  tests/api/test_static_thumbnails.py
  tests/db/test_project_users.py
  tests/test_xaccel_thumbnails.py
  ```
  → 4 batches sized for parallel dispatch.

- **`tests/api/conftest.py`** owns `_active_project` setup. After W10-B drops the global, this conftest needs a fresh `active_scan` fixture that yields `(project_id, scan_id)` tuples and seeds the matching DB rows. Verified: `_active_project` appears once at conftest module scope.

- **`tests/db/test_project_users.py`** (W6.3) currently inserts `ProjectUser` rows with arbitrary `project_id` strings. After W10-A makes `project_users.project_id` an FK to `projects(id)`, every test row needs a parent `Project` insert. The fix is a 3-line fixture addition, not a rewrite — captured in batch (a).

- **`tests/scripts/conftest.py`** has the known `drop_all` footgun (project-status item #65). W10-E's pre-flight wipe is OUT-OF-SCOPE for that conftest fix — they're independent backlog items. Document in W10-E that the wipe SQL is RUN BY HAND (or the wrapper script), NOT through the `tests/scripts/conftest.py` path.

- **No existing `scripts/dev/w10-acceptance.sh`.** This plan creates it. `scripts/dev/` already contains `start-backend.sh` and `start-frontend.sh` — same directory + same `#!/usr/bin/env bash` style.

- **`docs/db-ops.md`** is the runbook for RDS/bastion/alembic ops. The pre-flight wipe needs a section there. Verified the doc exists and is 200+ lines — appending a §3.x is consistent with prior W5 entries.

- **The W10-A pre-flight SQL is** `TRUNCATE` on tables in dependency order: `usage_events`, `flakes`, `flake_assignments`, `flake_clusterings`, `clustering_runs`, `images`, `analyses`, `scans`, `project_users`, `materials` (then re-seed materials). It runs on `saa_test` ONLY — RDS is W6.1 territory and is currently empty (no W7 data yet).

---

## Verification Env Block

Pre-flight wipe (manual, owner approval — saa_test only, RDS untouched):

```
psql -h 127.0.0.1 -U houkjang -d saa_test -f scripts/db/wipe-saa-test-pre-w10.sql
# OR via wrapper (Task 1):
bash scripts/db/wipe-saa-test.sh saa_test
```

Backend tests (one batch — substitute the file list):

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q tests/api/test_projects.py tests/api/test_deps.py
```

Backend full suite (acceptance gate):

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q tests/
```

Frontend full vitest:

```
cd web && npm test -- --run
```

Acceptance gate (Task 5):

```
bash scripts/dev/w10-acceptance.sh
```

> **Branch policy:** `feat/migration-cutover`. No source code edits in W10-E itself outside `tests/`, `scripts/`, and `docs/db-ops.md`.

---

## Naming Decisions (locked)

- **Pre-flight wrapper script**: `scripts/db/wipe-saa-test.sh` (Bash, takes one arg `db_name`, refuses to run unless arg startswith `saa_test`).
- **Acceptance script**: `scripts/dev/w10-acceptance.sh` (single-purpose; W11 will add its own).
- **New integration test file**: `tests/api/test_w10_acceptance.py` (NOT `test_w10_*.py` plural — single file with 3 marked specs is easier to dispatch).
- **conftest fixture name**: `active_scan` returning `tuple[Project, Scan]`. Replaces the implicit dependency on `_active_project`.
- **Test sweep batches**: 4a / 4b / 4c / 4d — same numeric prefix as the task. Mirrors W10-D's 7a/7b/7c style.

---

## File Structure (target)

```
docs/
└── db-ops.md                              ← APPENDED (§3.4 W10 saa_test wipe runbook)

scripts/
├── db/
│   ├── wipe-saa-test-pre-w10.sql          (shipped by W10-A — DO NOT touch)
│   └── wipe-saa-test.sh                   ← NEW (Bash wrapper)
└── dev/
    └── w10-acceptance.sh                  ← NEW

tests/
├── api/
│   ├── conftest.py                        ← MODIFIED (active_scan fixture; drop _active_project usage)
│   ├── test_w10_acceptance.py             ← NEW (3 integration specs)
│   ├── test_projects.py                   ← REWRITTEN (CRUD, not legacy)
│   ├── test_project_context.py            ← REWRITTEN or DELETED (W10-B drops the global)
│   ├── test_deps.py                       ← MODIFIED (active_scan instead of _active_project)
│   ├── test_get_active_analysis.py        ← MODIFIED (per-scan, no silent fallback)
│   ├── test_data_*.py                     ← MODIFIED (URL grammar)
│   ├── test_run_*_sse.py                  ← MODIFIED (URL grammar)
│   ├── test_static_*.py                   ← MODIFIED (URL grammar)
│   ├── test_selector_*.py                 ← MODIFIED (URL grammar)
│   ├── test_scans_*.py                    ← MODIFIED (path-only routing → URL grammar; project FK now real)
│   ├── test_mutex.py                      ← REWRITTEN (acquire_scan_lock, not project_lock)
│   ├── test_clustering_mutex_sharing.py   ← REWRITTEN (per-scan lock semantics)
│   └── test_path_validation.py            ← DELETED (W10-D drops `validatePaths`; backend stub removed in W10-C)
├── db/
│   └── test_project_users.py              ← MODIFIED (parent Project row before each ProjectUser insert)
└── test_xaccel_thumbnails.py              ← MODIFIED (URL grammar; this is the file flagged as 4 pre-existing failures in project-status — W10-E either fixes or marks xfail with W10-D acknowledgement)
```

---

## Task 1 — Pre-flight wrapper script + db-ops runbook entry

**Files:**
- Create: `scripts/db/wipe-saa-test.sh`
- Modify: `docs/db-ops.md` (append §3.4)

**Why:** A one-liner the owner / acceptance gate can call without remembering the psql incantation. Critical safety: refuse to run unless the target DB name starts with `saa_test` so it's impossible to point at `qpress` (RDS) by accident.

### Step 1.1: Implement the wrapper

- [ ] **Create `scripts/db/wipe-saa-test.sh`:**

```bash
#!/usr/bin/env bash
# scripts/db/wipe-saa-test.sh
#
# Pre-flight wipe before applying alembic 0004 (W10) on a LOCAL test database.
# Refuses to run unless the target DB name starts with "saa_test" — never
# pointable at RDS / production accidentally.
#
# Usage:
#   bash scripts/db/wipe-saa-test.sh saa_test [host] [user]
#
# Defaults: host=127.0.0.1 user=houkjang
set -euo pipefail

DB_NAME="${1:?Usage: $0 <db_name> [host] [user]}"
DB_HOST="${2:-127.0.0.1}"
DB_USER="${3:-houkjang}"

if [[ ! "$DB_NAME" =~ ^saa_test ]]; then
  echo "REFUSING: db name '$DB_NAME' must start with 'saa_test' (got: $DB_NAME)" >&2
  echo "   This script is for local test DBs only. RDS / qpress wipes are not allowed." >&2
  exit 2
fi

SQL_FILE="$(dirname "$0")/wipe-saa-test-pre-w10.sql"
if [[ ! -f "$SQL_FILE" ]]; then
  echo "REFUSING: SQL file not found at $SQL_FILE" >&2
  exit 3
fi

echo "Pre-flight wipe target: $DB_USER@$DB_HOST/$DB_NAME"
echo "   Using $SQL_FILE"
echo
read -r -p "Continue? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "Aborted."
  exit 1
fi

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$SQL_FILE"
echo "Wipe complete. Now run: uv run alembic upgrade head"
```

- [ ] **Make it executable:**

```
chmod +x scripts/db/wipe-saa-test.sh
```

### Step 1.2: Smoke test the guard rail

- [ ] **Run (expect REFUSAL):**

```
bash scripts/db/wipe-saa-test.sh qpress
```

Expected: exit code 2, message "REFUSING: db name 'qpress' must start with 'saa_test'".

> **PM check:** this guard is the ONLY thing protecting prod from a fat-fingered wipe — verify the regex `^saa_test` matches `saa_test`, `saa_test_dev`, `saa_test_w10`, but NOT `qpress`, `saatest` (no underscore), or empty string.

### Step 1.3: Append `docs/db-ops.md` §3.4

- [ ] **Append to `docs/db-ops.md`** (after the existing §3 section — "alembic 운영"):

```markdown
### 3.4 W10 pre-flight wipe (saa_test only)

W10 introduces a real `projects` table + FK rewires on `scans.project_id` and
`project_users.project_id`. Existing rows on `saa_test` (carried over from W5/W6
test runs) lack `projects` parents, so alembic `0004_w10_projects` will fail to
apply unless those rows are wiped first. RDS is empty for W10-relevant tables
(W7 has not landed) so this procedure is local-only.

**Run order:**

1. Confirm target is `saa_test` (NEVER `qpress`).
2. `bash scripts/db/wipe-saa-test.sh saa_test`
3. The script prompts for confirmation, then `TRUNCATE`s in dependency order
   and re-seeds the 5 W5-A material rows.
4. `uv run alembic upgrade head` — applies `0004_w10_projects` cleanly.
5. `uv run pytest -m pg tests/db/test_w10_projects.py` — confirms ORM<->DDL parity.

**Safety guard:** the wrapper refuses to run unless `$1` starts with `saa_test`.
The SQL file (`scripts/db/wipe-saa-test-pre-w10.sql`) is unconditional — DO NOT
invoke it directly with `psql -d qpress`.

**Rollback:** the wipe is destructive. To restore a saa_test instance:
- `dropdb saa_test && createdb saa_test`
- `uv run alembic upgrade head`
- Re-seed via test fixtures.

Owner ran this once on 2026-05-XX before applying alembic 0004 (commit hash to
fill in upon merge).
```

### Step 1.4: Commit

- [ ] **Run:**

```bash
git add scripts/db/wipe-saa-test.sh docs/db-ops.md
git commit -m "ops: add wipe-saa-test.sh wrapper + db-ops §3.4 W10 pre-flight runbook (W10-E.1)"
```

---

## Task 2 — `tests/api/conftest.py` rewire (replace `_active_project` with `active_scan`)

**Files:**
- Modify: `tests/api/conftest.py`

**Why:** Single chokepoint that every batch in Task 3 depends on. After W10-B drops the `_active_project` global, the conftest's old `set_active_project` fixture is useless — every test now needs an explicit `(project_id, scan_id)` pair. Provide one canonical fixture so the batches don't each invent their own.

### Step 2.1: Inspect the current conftest

- [ ] **Read `tests/api/conftest.py`** carefully. Identify:
  - The fixture that currently calls `_active_project` setter (likely named `set_active_project` or `with_active_project`).
  - Any fixture that creates a `Scan` row but no `Project` row.
  - Any fixture that sets `SAA_ANALYSIS_FOLDER` (must rename to `SAA_ANALYSIS_ROOT` per W10-B; the legacy fallback in `paths.py` still reads the old name as a safety net for one release, but new tests should use the new name).

### Step 2.2: Add the `active_scan` fixture

- [ ] **In `tests/api/conftest.py`, add** (placement: alongside other DB fixtures, after `pg_session`):

```python
import pytest
from flake_analysis.db.models.projects import Project
from flake_analysis.db.models.catalog import Scan, Material


@pytest.fixture
async def active_project(pg_session, current_user_dev):
    """Insert a fresh `projects` row owned by the dev user. Returns the Project."""
    p = Project(
        id="p_test_w10",
        owner_id=current_user_dev.id,
        name="w10-test",
        description="W10 test fixture",
    )
    pg_session.add(p)
    await pg_session.flush()
    return p


@pytest.fixture
async def active_material(pg_session):
    """Materials are seeded by alembic but tests may run on an empty DB."""
    existing = await pg_session.get(Material, "graphene")
    if existing is not None:
        return existing
    m = Material(name="graphene")
    pg_session.add(m)
    await pg_session.flush()
    return m


@pytest.fixture
async def active_scan(pg_session, active_project, active_material, current_user_dev):
    """Insert a Scan row under `active_project`. Returns the Scan."""
    s = Scan(
        project_id=active_project.id,
        name="w10-test-scan",
        material=active_material.name,
        image_count=4,
        extra_metadata={},
        created_by_id=current_user_dev.id,
    )
    pg_session.add(s)
    await pg_session.flush()
    return s
```

> **NOTE:** the exact import path for `current_user_dev` depends on the existing W6.3 conftest. If it's named differently (e.g. `dev_user`), substitute. Do NOT introduce a new auth fixture.

### Step 2.3: Drop the legacy `_active_project` fixture

- [ ] **Remove** every reference to `_active_project` and `set_active_project` from `tests/api/conftest.py`. Replace any test that depended on it with `active_scan` (covered per-file in Task 3).

### Step 2.4: Rename `SAA_ANALYSIS_FOLDER` → `SAA_ANALYSIS_ROOT` in conftest

- [ ] **In `tests/api/conftest.py`, find any `monkeypatch.setenv("SAA_ANALYSIS_FOLDER", ...)`** and rename to `SAA_ANALYSIS_ROOT`. The legacy fallback in `state/paths.py` (W10-B) still reads the old name, but tests should be on the new name so the fallback can be removed in W11.

### Step 2.5: Run a smoke pytest

- [ ] **Run** (expect a smaller number of failures than the full sweep — most failures here are about routes still using the old URL grammar, which is fine; we just want conftest itself to import):

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q --collect-only tests/api/
```

Expected: collection succeeds (no import errors). Test run is gated to Task 3.

### Step 2.6: Commit

- [ ] **Run:**

```bash
git add tests/api/conftest.py
git commit -m "test: conftest active_scan fixture replaces _active_project (W10-E.2)"
```

---

## Task 3 — Backend test sweep (4 parallel batches)

> **Sequencing:** Batches 3a–3d are independent — same edit pattern, disjoint files. PM dispatches four subagents in parallel. Each batch ends with its own pytest run + commit.

### Batch 3a — projects + deps + project_context

**Files:**
- `tests/api/test_projects.py` ← **rewrite** against W10-C CRUD (5 endpoints).
- `tests/api/test_deps.py` ← swap `_active_project` mocks for `active_scan` fixture; assert `get_active_analysis(scan_id)` raises (no silent fallback).
- `tests/api/test_project_context.py` ← if the file only tested the legacy `get_project_context` global, **delete the whole file** (W10-B drops the function). If a few tests pivot to `(project_id, scan_id)` resolution, keep those and rename the file to `test_project_scan_resolution.py`.
- `tests/api/test_get_active_analysis.py` ← rewrite per-scan (W10-B raises `NotFound` instead of silent `LIMIT 1`).
- `tests/api/test_path_validation.py` ← **delete** (W10-D drops the `validatePaths` UI hook; W10-C drops the backend route).
- `tests/db/test_project_users.py` ← prepend `Project` row to every `ProjectUser` insert (W10-A makes `project_users.project_id` an FK).

**Edit pattern:**
- Replace any `client.post("/api/v1/projects", json={"analysis_folder": ...})` with the new `{"name": "...", "description": "..."}`.
- Replace `_active_project` mutation with `active_scan` fixture injection.
- Add asserts for the W10-C new endpoints: `GET /projects`, `GET /projects/{pid}`, `PATCH /projects/{pid}`, `DELETE /projects/{pid}` (409 on RESTRICT — covered fully in `test_w10_acceptance.py`, but a positive 204 path belongs here).

**Run + commit:**

- [ ] **Run:**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q tests/api/test_projects.py tests/api/test_deps.py \
  tests/api/test_get_active_analysis.py tests/db/test_project_users.py
# (if test_project_context.py was renamed:)
# uv run pytest -q tests/api/test_project_scan_resolution.py
```

Expected: all green.

```bash
git add tests/api/test_projects.py tests/api/test_deps.py \
        tests/api/test_get_active_analysis.py tests/db/test_project_users.py
# Conditional removes:
[ -f tests/api/test_project_context.py ] && git rm tests/api/test_project_context.py
[ -f tests/api/test_path_validation.py ] && git rm tests/api/test_path_validation.py
git commit -m "test(api): batch 3a — projects/deps/project_context migrated to W10 (W10-E.3a)"
```

### Batch 3b — run/SSE routes

**Files:**
- `tests/api/test_run_background_sse.py`
- `tests/api/test_run_thumbnails_sse.py`
- `tests/api/test_run_domain_stats_sse.py`
- `tests/api/test_run_domain_proximity_sse.py`
- `tests/api/test_run_selector_sse.py`
- `tests/api/test_run_clustering_apply_thresholds_sse.py`
- `tests/api/test_run_clustering_refit_sse.py`
- `tests/api/test_run_clustering_refit_reg_covar.py`
- `tests/api/test_run_emits_usage.py`
- `tests/api/test_run_explorer_get_state.py`
- `tests/api/test_run_explorer_save_state.py`
- `tests/api/test_run_fake_sse.py`
- `tests/api/test_sse_heartbeat.py`

**Edit pattern:**
- Replace `client.post("/api/v1/run/<step>")` with `client.post(f"/api/v1/projects/{pid}/scans/{sid}/run/<step>")` per W10-C task 4b/4c.
- Replace `_active_project` setup with `active_scan` fixture.
- For tests that asserted `acquire_project_lock`, swap to `acquire_scan_lock` per W10-B.
- For tests that asserted `usage_events.context["project_id"]`, also assert `context["scan_id"]` is set.

**Run + commit:**

- [ ] **Run:**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q \
  tests/api/test_run_background_sse.py \
  tests/api/test_run_thumbnails_sse.py \
  tests/api/test_run_domain_stats_sse.py \
  tests/api/test_run_domain_proximity_sse.py \
  tests/api/test_run_selector_sse.py \
  tests/api/test_run_clustering_apply_thresholds_sse.py \
  tests/api/test_run_clustering_refit_sse.py \
  tests/api/test_run_clustering_refit_reg_covar.py \
  tests/api/test_run_emits_usage.py \
  tests/api/test_run_explorer_get_state.py \
  tests/api/test_run_explorer_save_state.py \
  tests/api/test_run_fake_sse.py \
  tests/api/test_sse_heartbeat.py
```

Expected: all green except `test_run_*_sse.py` known-hang issue (project-status #66, asyncio teardown). If the hang surfaces, mark those specific specs `@pytest.mark.skip(reason="project-status #66")` and document; do NOT block this plan on it.

```bash
git add tests/api/test_run_*.py tests/api/test_sse_heartbeat.py
git commit -m "test(api): batch 3b — run/SSE routes migrated to /scans/:sid grammar (W10-E.3b)"
```

### Batch 3c — data + static routes

**Files:**
- `tests/api/test_data_annotation_preview.py`
- `tests/api/test_data_clustering_assignments.py`
- `tests/api/test_data_clustering_labels.py`
- `tests/api/test_data_clustering_seed_groups.py`
- `tests/api/test_data_domain_stats.py`
- `tests/api/test_data_explorer_flake_detail.py`
- `tests/api/test_data_explorer_flakes.py`
- `tests/api/test_data_explorer_grid.py`
- `tests/api/test_data_explorer_tile_manifest.py`
- `tests/api/test_data_manifest.py`
- `tests/api/test_data_selection.py`
- `tests/api/test_static_raw.py`
- `tests/api/test_static_thumbnails.py`
- `tests/api/test_manifest_endpoint_db.py`
- `tests/test_xaccel_thumbnails.py`

**Edit pattern:**
- Replace `client.get("/api/v1/data/<endpoint>")` with `client.get(f"/api/v1/projects/{pid}/scans/{sid}/data/<endpoint>")` per W10-C task 4a.
- Static routes (`/api/v1/static/raw/...`, `/api/v1/static/thumbnails/...`) stay un-prefixed (they read from disk paths, not DB) — but the underlying disk path now lives under `<root>/<pid>/<sid>/...` per W10-B. Update fixtures accordingly.
- `test_xaccel_thumbnails.py`: project-status notes 4 pre-existing failures here (W6 auth-gate); fix in this batch IF the failures' root cause was URL grammar. If they were genuinely auth-related, leave them xfail and document.

**Run + commit:**

- [ ] **Run:**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q tests/api/test_data_*.py tests/api/test_static_*.py \
  tests/api/test_manifest_endpoint_db.py tests/test_xaccel_thumbnails.py
```

Expected: all green (modulo any documented xfail).

```bash
git add tests/api/test_data_*.py tests/api/test_static_*.py \
        tests/api/test_manifest_endpoint_db.py tests/test_xaccel_thumbnails.py
git commit -m "test(api): batch 3c — data/static routes migrated to /scans/:sid grammar (W10-E.3c)"
```

### Batch 3d — selector / scans / mutex / guards / admin / auth

**Files:**
- `tests/api/test_selector_commit.py`
- `tests/api/test_selector_export.py`
- `tests/api/test_scans_create.py`
- `tests/api/test_scans_presign.py`
- `tests/api/test_scans_complete.py`
- `tests/api/test_scans_finalize.py`
- `tests/api/test_mutex.py`               ← rewrite for `acquire_scan_lock`
- `tests/api/test_clustering_mutex_sharing.py` ← assert per-scan isolation
- `tests/api/test_guards.py`
- `tests/api/test_admin_routes.py`
- `tests/api/test_auth_dep_dropin.py`

**Edit pattern:**
- `test_mutex.py`: rewrite every `acquire_project_lock("p1")` to `acquire_scan_lock(11)`. Add a test that two different scans get two different locks (no contention). Per project-status #64, `test_scans_*.py` had `KeyError s3_uri` — the W10 ORM tightening in W10-A (or the W5-B presigned response shape) should resolve those. If not, leave a TODO + xfail with the project-status reference.
- `test_clustering_mutex_sharing.py`: now that mutex is per-scan, the OLD assertion was "two clustering ops on the SAME PROJECT contend" — flip it to "two clustering ops on the SAME SCAN contend, but on DIFFERENT SCANS in the same project they don't".
- `test_guards.py`: any `require_project_role(p1, ...)` test needs a real `Project` row so the resolver doesn't 404 on missing project.
- `test_admin_routes.py`: `admin_usage` already isolated (project-status 21e9640 fix); should not regress. If it does, document.
- `test_auth_dep_dropin.py`: pure auth — no W10 surface change unless it exercised `/projects/active` (W10-C deletes that route). Drop those specs.

**Run + commit:**

- [ ] **Run:**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q \
  tests/api/test_selector_commit.py \
  tests/api/test_selector_export.py \
  tests/api/test_scans_create.py \
  tests/api/test_scans_presign.py \
  tests/api/test_scans_complete.py \
  tests/api/test_scans_finalize.py \
  tests/api/test_mutex.py \
  tests/api/test_clustering_mutex_sharing.py \
  tests/api/test_guards.py \
  tests/api/test_admin_routes.py \
  tests/api/test_auth_dep_dropin.py
```

Expected: all green.

```bash
git add tests/api/test_selector_*.py tests/api/test_scans_*.py \
        tests/api/test_mutex.py tests/api/test_clustering_mutex_sharing.py \
        tests/api/test_guards.py tests/api/test_admin_routes.py tests/api/test_auth_dep_dropin.py
git commit -m "test(api): batch 3d — selector/scans/mutex/guards migrated (W10-E.3d)"
```

---

## Task 4 — New W10 acceptance integration tests

**Files:**
- Create: `tests/api/test_w10_acceptance.py`

**Why:** The 3 locked decisions need direct, named coverage so a regression is loud:
1. **D2** — `DELETE /api/v1/projects/{pid}` returns 409 with `code: project_has_scans` when `scans` rows exist; returns 204 once they're gone.
2. **D4** — Two concurrent `POST /api/v1/projects/{pid}/scans/{sid}/run/<step>` on the SAME scan_id contend (one returns 409 `mutex_busy` or its successor); on DIFFERENT scan_ids they run independently (both 200).
3. **D6** — A user with no projects gets `[]` from `GET /api/v1/projects` and the frontend force-create flow is unblocked. Backend assertion: a fresh user can call `POST /api/v1/projects` without any prior state and immediately get a 201.

### Step 4.1: Failing test

- [ ] **Create `tests/api/test_w10_acceptance.py`:**

```python
"""W10 acceptance gate — direct coverage of D2/D4/D6.

These specs are intentionally chunky integration tests; if they break, the W10
contract has regressed and someone needs to look hard. Each spec runs against a
real PG via `pg_session` + `client` fixtures from conftest.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.pg


# ----- D2: DELETE-RESTRICT --------------------------------------------------
async def test_delete_project_with_scans_returns_409(client: AsyncClient, active_scan):
    """D2: a project with at least one scan must NOT be deletable."""
    project_id = active_scan.project_id

    resp = await client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "project_has_scans"
    assert body["error"]["details"]["scan_count"] >= 1


async def test_delete_project_after_clearing_scans_returns_204(
    client: AsyncClient, active_scan, pg_session
):
    """D2 inverse: once scans are gone, delete works."""
    project_id = active_scan.project_id
    # Drop the only scan via ORM; mimics manual cleanup.
    await pg_session.delete(active_scan)
    await pg_session.commit()

    resp = await client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 204


# ----- D4: per-scan mutex isolation ----------------------------------------
async def test_two_scans_run_concurrently_no_contention(
    client: AsyncClient, active_project, active_material, current_user_dev, pg_session,
):
    """D4: different scans in the same project share NO lock."""
    from flake_analysis.db.models.catalog import Scan
    s_a = Scan(
        project_id=active_project.id, name="scanA", material=active_material.name,
        image_count=1, extra_metadata={}, created_by_id=current_user_dev.id,
    )
    s_b = Scan(
        project_id=active_project.id, name="scanB", material=active_material.name,
        image_count=1, extra_metadata={}, created_by_id=current_user_dev.id,
    )
    pg_session.add_all([s_a, s_b])
    await pg_session.commit()

    pid = active_project.id
    # Use a FAKE step that the run router accepts but does cheap work — see
    # tests/api/test_run_fake_sse.py for the existing pattern. Adjust path to
    # whatever W10-C wired (likely /api/v1/projects/{pid}/scans/{sid}/run/fake).
    async def run_one(sid: int) -> int:
        r = await client.post(f"/api/v1/projects/{pid}/scans/{sid}/run/fake")
        return r.status_code

    codes = await asyncio.gather(run_one(s_a.id), run_one(s_b.id))
    assert codes == [200, 200], f"expected both to succeed, got {codes}"


async def test_same_scan_concurrent_runs_one_409(
    client: AsyncClient, active_scan,
):
    """D4: two concurrent runs on the SAME scan — one wins, the other 409s."""
    pid = active_scan.project_id
    sid = active_scan.id

    async def run_one() -> int:
        r = await client.post(f"/api/v1/projects/{pid}/scans/{sid}/run/fake")
        return r.status_code

    codes = await asyncio.gather(run_one(), run_one())
    assert sorted(codes) == [200, 409], f"expected one success + one busy, got {codes}"


# ----- D6: force-create-project UX, backend side ---------------------------
async def test_fresh_user_lists_zero_projects(
    client: AsyncClient, current_user_dev,
):
    """D6: a user with no projects gets an empty list (not a 404)."""
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"projects": []}


async def test_fresh_user_can_create_first_project(
    client: AsyncClient, current_user_dev,
):
    """D6: no prior state required — POST works on day zero."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "first-project", "description": "hello"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "first-project"
    assert body["scan_count"] == 0
```

### Step 4.2: Run — expect partial PASS / partial FAIL depending on W10-C surface

- [ ] **Run:**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest -q tests/api/test_w10_acceptance.py
```

Expected: 6 passed if W10-A/B/C/D are merged. If any of A/B/C is missing, the failures pinpoint exactly which. Surface failures back to the PM — these are the canary.

### Step 4.3: Commit

- [ ] **Run:**

```bash
git add tests/api/test_w10_acceptance.py
git commit -m "test(api): W10 acceptance specs for D2/D4/D6 (W10-E.4)"
```

---

## Task 5 — Acceptance gate script

**Files:**
- Create: `scripts/dev/w10-acceptance.sh`

**Why:** One command the PM (or a CI job) runs to verify the entire W10 stack is green. Reads like a runbook — every command is one line, prints the boundary clearly so the operator can see which step failed.

### Step 5.1: Implement

- [ ] **Create `scripts/dev/w10-acceptance.sh`:**

```bash
#!/usr/bin/env bash
# scripts/dev/w10-acceptance.sh
#
# W10 merge gate. Run after every W10-* PR lands; the gate must be green
# before merging the W10 branch into main.
#
# Assumes:
#   - `uv sync` completed
#   - `cd web && npm ci` completed
#   - saa_test database is wiped + alembic at head (Task 1 wrapper ran)
#   - SAA_AUTH_DEV_BYPASS=1 is acceptable for this environment
#
# Does NOT install deps. Does NOT mutate the DB beyond what pytest does.
set -euo pipefail

cd "$(dirname "$0")/../.."

REPO="$(pwd)"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="${REPO}/.w10-acceptance.${TS}.log"

echo "W10 acceptance gate — $(date -Iseconds)"
echo "   repo: $REPO"
echo "   log : $LOG"
echo

step() {
  printf "==> %s\n" "$*"
  printf "==> %s\n" "$*" >> "$LOG"
}

run() {
  step "$1"
  shift
  ( "$@" ) 2>&1 | tee -a "$LOG"
}

# 1) Alembic at head — proves migrations apply cleanly.
run "alembic upgrade head" \
  env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
      uv run alembic upgrade head

# 2) Backend tests — full suite, both PG and non-PG marks.
run "pytest tests/ (full suite)" \
  env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
      SAA_AUTH_DEV_BYPASS=1 SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
      uv run pytest -q tests/

# 3) Frontend vitest — full sweep.
run "vitest (full sweep)" \
  bash -c "cd web && npm test -- --run"

# 4) Frontend type-check + production build.
run "tsc + vite build" \
  bash -c "cd web && npm run build"

echo
echo "W10 acceptance gate PASSED"
echo "   Full log: $LOG"
```

- [ ] **Make executable:**

```
chmod +x scripts/dev/w10-acceptance.sh
```

### Step 5.2: Smoke

- [ ] **Run** (delegate to a subagent — PM Bash rule §2.5 forbids pytest directly):

```
bash scripts/dev/w10-acceptance.sh
```

Expected: every step PASS. The log file `.w10-acceptance.<timestamp>.log` is written for forensics.

### Step 5.3: Commit

- [ ] **Run:**

```bash
git add scripts/dev/w10-acceptance.sh
git commit -m "ops: W10 acceptance gate script (alembic + pytest + vitest + build) (W10-E.5)"
```

---

## Self-Review

**D-block coverage (locked 2026-05-22):**
- D1 (real `projects` table): pre-flight wipe + alembic upgrade in Task 1 + Task 5 step 1. ✓
- D2 (DELETE RESTRICT): direct test in `test_w10_acceptance.py` (2 specs — 409 with scans, 204 after wipe). ✓
- D3 (1:N project→scan): every `active_scan` fixture + every URL grammar rewrite asserts the parent-child path. ✓
- D4 (per-scan `asyncio.Lock`): 2 specs in `test_w10_acceptance.py` (concurrent same-scan vs concurrent diff-scan). ✓
- D5 (per-scan analysis folder): covered transitively — fixtures use `SAA_ANALYSIS_ROOT` and `analysis_folder(root, pid, sid)` via W10-B. No dedicated W10-E spec needed; W10-B's own test file covers it. ✓
- D6 (force-create-project UX): 2 specs in `test_w10_acceptance.py` (zero-list, day-zero create). ✓

**Test coverage delta:**
- 27 backend test files migrated across 4 batches.
- 1 file deleted (`test_path_validation.py`).
- 1 file conditionally renamed/deleted (`test_project_context.py` depending on whether anything survives W10-B's deletion of `get_project_context`).
- 1 new file added (`test_w10_acceptance.py`, 6 specs).

**Operational safety:**
- Pre-flight wipe wrapper REFUSES on any DB name not starting with `saa_test`. Independently verified in Step 1.2.
- `docs/db-ops.md` §3.4 documents the procedure. Owner approval required for the wipe (the wrapper's interactive `[y/N]` prompt enforces this).
- Acceptance gate is read-only beyond what pytest does — no schema changes outside alembic.

**Sequencing assumptions:**
- W10-A merged → wipe SQL exists → Task 1 wrapper works.
- W10-B merged → `acquire_scan_lock` exists → batches 3b/3d pass.
- W10-C merged → URL grammar exists → batches 3a/3b/3c/3d all pass.
- W10-D merged → frontend tests green → Task 5 step 3 passes.
- If any earlier W10-* is in flight, the relevant batch fails loudly. Don't paper over with skips.

**Risks:**
- `test_run_*_sse.py` hang (project-status #66) is pre-existing. If it persists after W10-B's fixture cleanup, mark `xfail` rather than `skip` so we don't lose visibility.
- `test_xaccel_thumbnails.py` 4 known failures — root cause should be re-checked. Document outcome in batch 3c.
- `tests/db/test_project_users.py` parent-Project insertion may surface other tests doing the same shortcut (raw `ProjectUser` insert). Grep `grep -rn "ProjectUser(" tests/` after batch 3a — surface findings to PM if any.

**Out of scope:**
- RDS migration of `0004_w10_projects` — W6.1 territory; defer until Cognito is approved.
- W11 backlog (project rename/delete UI).
- Removing the `SAA_ANALYSIS_FOLDER` legacy fallback in `state/paths.py` — W11.

---

## Open follow-up (out of W10-E scope)

- **CI integration of acceptance gate**: wire `w10-acceptance.sh` into `.github/workflows/` once W10 merges. Today it's a manual gate.
- **`tests/scripts/conftest.py` `drop_all` footgun** (project-status #65): independent fix, not a W10 prerequisite.
- **Real Playwright e2e for the full project-create → scan-create → run pipeline**: extends `tests/e2e/upload.spec.ts` from W5-C. Out of scope here; W10-D Task 9 is the placeholder.
- **A "create initial demo project" seed script** for new dev-environment setup. Currently the wipe leaves the DB empty; we rely on UI-driven creation. Optional W11 polish.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W10-E-test-and-data-migration.md`.

**Recommended execution mode:** Subagent-Driven. 5 tasks. Estimated subagent budget:

```
1 (wipe wrapper + db-ops)        ─ 15 min  (devops-engineer)
2 (conftest active_scan)         ─ 12 min  (api-developer)
3a (projects + deps)             ─ 15 min  ┐
3b (run/SSE)                     ─ 25 min  ├─ Tasks 3a-3d are independent — dispatch
3c (data + static)               ─ 25 min  │   four api-developer subagents in parallel
3d (selector/scans/mutex/...)    ─ 25 min  ┘
4 (acceptance integration tests) ─ 20 min  (api-developer)
5 (acceptance script)            ─ 10 min  (devops-engineer)
```

**Dispatch order (strict):**

```
1 (wipe wrapper + runbook) — must be ready before any pytest can run on saa_test
  → 2 (conftest)            — chokepoint; every batch in 3 depends on it
    → 3a, 3b, 3c, 3d        — parallel
      → 4 (acceptance)      — depends on all of 3
        → 5 (gate script)   — depends on all prior
```

**PM check-ins:**
- After Task 1: hand-run `bash scripts/db/wipe-saa-test.sh saa_test` once on the local saa_test (owner approval required) before dispatching any pytest batch.
- After Task 2: review the `active_scan` fixture import paths — `current_user_dev` is the most likely typo.
- After Task 3 (all batches): run the FULL `tests/` once via a subagent before starting Task 4. If anything red leaked between batches, fix before adding new specs.
- After Task 4: review the D4 mutex specs carefully — `asyncio.gather` race between two test clients can be flaky if the lock is contended in the wrong direction. If the spec hangs, that's a real bug in W10-B.
- After Task 5: the gate is the merge-gate. Green ⇒ W10 ships. Red ⇒ block the merge until fixed. PM owns this signal.
