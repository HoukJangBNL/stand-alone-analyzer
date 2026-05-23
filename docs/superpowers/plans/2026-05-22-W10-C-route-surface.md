# W10-C — Route surface: per-scan analyses + projects CRUD

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the FastAPI route surface so analysis endpoints are scoped to a real `(project_id, scan_id)` pair instead of project_id alone. Land a real projects CRUD (`GET /projects`, `POST /projects`, `GET /projects/{pid}`, `PATCH /projects/{pid}`, `DELETE /projects/{pid}` with RESTRICT). Switch the 6 analysis routers (`data`, `run`, `selector`, `clustering`, `explorer`, `static`) to the new URL grammar `/projects/{pid}/scans/{sid}/...` and to the per-scan deps from W10-B (`get_manifest(pid, sid)`, `get_active_analysis(scan_id)`, `acquire_scan_lock(scan_id)`).

**Architecture:** Two layers of change. **Layer 1 (Project CRUD):** `routes/projects.py` becomes a real CRUD router backed by the new `projects` table. The legacy POST that stashed `_active_project` is dropped; `validate-paths` is dropped (frontend can synthesize per-scan paths client-side from `manifest_path()` semantics — W10-D handles). Project DELETE returns `409` with body `{"error": {"code": "project_has_scans", "details": {"scan_count": N}}}` when scans exist (D2). **Layer 2 (analysis routes):** the 6 routers gain a `:scan_id` path param. Both `pid` and `sid` stay in the path so RBAC `require_project_role(project_id_param="project_id", min_project_role=...)` still validates by pid; sid is the analysis target. New router `scans.py` already exists from W5 — we extend it with `GET /projects/{pid}/scans` (list scans for a project) and `GET /projects/{pid}/scans/{sid}` (scan detail; W5-B2 already has `/scans/{sid}` — relocate or alias).

**Tech Stack:** FastAPI 0.110+, pydantic v2, SQLAlchemy 2.x async, pytest-asyncio strict + `pytest.mark.pg`, httpx ASGITransport.

---

## Locked Decisions (W10 D-block, 2026-05-22)

- **D1.** Pipeline runs per scan — analyses keyed on `scan_id`.
- **D2.** Project DELETE = RESTRICT if scans exist → HTTP 409 envelope.
- **D4.** `acquire_scan_lock(scan_id)` for all run endpoints.
- **D5.** Manifest path = `<root>/<pid>/<sid>/manifest.json` (W10-B already wired).

### Plan-level decision (locked here, 2026-05-22)

**Path grammar carries both `pid` and `sid` even though `sid` would be sufficient.** Justification:
1. RBAC `require_project_role(project_id_param="project_id", ...)` is the existing W6.4 dependency — refactoring it to look up `project_id FROM scans WHERE id = :sid` adds a DB query per request and complicates the dep cache. Keeping `pid` in the path is one extra string per URL but zero extra runtime cost.
2. Audit log clarity. When `usage_events` rows record `value_json: {"project_id": pid, "scan_id": sid}`, both come from the URL — no risk of "wrong project but right scan" (which would be a hint of an ACL bug).
3. Frontend (W10-D) URL convention `/projects/:pid/scans/:sid/{compute|selector|...}` is naturally hierarchical — the API mirrors it 1:1.

A future refactor could derive pid from sid in a route-level dependency that emits a 404 if `sid` doesn't belong to `pid`; for now we keep it explicit.

---

## File Structure

- Modify: `src/flake_analysis/api/routes/projects.py` — full rewrite as CRUD.
- Modify: `src/flake_analysis/api/schemas/projects.py` — drop `validate-paths` schemas; add `ProjectListResponse`, `CreateProjectRequest{name, description}`, `PatchProjectRequest{name, description}`, `ProjectDetail{id, name, owner_id, created_at, scan_count}`.
- Modify: `src/flake_analysis/api/routes/scans.py` — add `GET /projects/{pid}/scans` listing.
- Modify: `src/flake_analysis/api/routes/data.py` — prefix `/projects/{project_id}/scans/{scan_id}/data`; switch deps to per-scan.
- Modify: `src/flake_analysis/api/routes/run.py` — same prefix swap; `acquire_scan_lock(scan_id)` instead of `acquire_project_lock(project_id)`; usage events emit both.
- Modify: `src/flake_analysis/api/routes/selector.py` — same.
- Modify: `src/flake_analysis/api/routes/clustering.py` — same.
- Modify: `src/flake_analysis/api/routes/explorer.py` — same.
- Modify: `src/flake_analysis/api/routes/static.py` — same.
- Create: `src/flake_analysis/api/services/projects_service.py` — `create_project`, `list_projects_for_user`, `get_project`, `delete_project_or_409`.
- Modify: `tests/api/test_projects.py` — full rewrite for CRUD.
- Modify: `tests/api/test_data_*.py`, `tests/api/test_run_*.py`, `tests/api/test_selector_*.py`, `tests/api/test_clustering_*.py`, `tests/api/test_explorer_*.py`, `tests/api/test_static*.py` — URL fixture updates (~27 backend tests touched).

---

## Verification Env Block

All test runs MUST use this exact prefix:

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root
```

Migration assumed at head `0004_w10_projects` (W10-A complete). W10-B merged.

---

## Task 1 — Projects CRUD service + schemas

**Files:**
- Modify: `src/flake_analysis/api/schemas/projects.py`
- Create: `src/flake_analysis/api/services/projects_service.py`
- Create: `tests/api/test_projects_service.py`

**Why:** Pure DB layer first. Service helpers can be unit-tested without HTTP. Routes import service + schemas in Task 2.

### Step 1.1: Write the failing test

- [ ] **Create `tests/api/test_projects_service.py`:**

```python
"""W10-C: projects_service unit tests (PG-backed)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.api.services import projects_service
from flake_analysis.db.models import Project, Scan

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_create_project_assigns_uuid_id(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(
        pg_session, owner_id=owner.id, name="P1", description="hello",
    )
    await pg_session.flush()
    assert p.id and len(p.id) >= 8
    assert p.name == "P1"
    assert p.description == "hello"
    assert p.owner_id == owner.id


@pytest.mark.asyncio
async def test_create_project_rejects_dup_name_per_owner(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    await projects_service.create_project(pg_session, owner_id=owner.id, name="dup")
    await pg_session.flush()
    with pytest.raises(projects_service.DuplicateProjectName):
        await projects_service.create_project(pg_session, owner_id=owner.id, name="dup")
        await pg_session.flush()


@pytest.mark.asyncio
async def test_list_projects_for_user_returns_owned_only(pg_session, sample_user_factory):
    a = await sample_user_factory()
    b = await sample_user_factory()
    await projects_service.create_project(pg_session, owner_id=a.id, name="A1")
    await projects_service.create_project(pg_session, owner_id=a.id, name="A2")
    await projects_service.create_project(pg_session, owner_id=b.id, name="B1")
    await pg_session.flush()

    rows = await projects_service.list_projects_for_user(pg_session, user_id=a.id)
    names = {r.name for r in rows}
    assert names == {"A1", "A2"}


@pytest.mark.asyncio
async def test_delete_project_restricts_when_scans_exist(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(pg_session, owner_id=owner.id, name="restrict-me")
    await pg_session.flush()
    pg_session.add(Scan(name="s1", material="graphene", project_id=p.id, created_by_id=owner.id))
    await pg_session.flush()

    with pytest.raises(projects_service.ProjectHasScans) as exc_info:
        await projects_service.delete_project_or_409(pg_session, project_id=p.id)
    assert exc_info.value.scan_count == 1


@pytest.mark.asyncio
async def test_delete_project_succeeds_when_empty(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(pg_session, owner_id=owner.id, name="empty")
    await pg_session.flush()

    await projects_service.delete_project_or_409(pg_session, project_id=p.id)
    await pg_session.flush()
    gone = (await pg_session.execute(select(Project).where(Project.id == p.id))).scalar_one_or_none()
    assert gone is None
```

### Step 1.2: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_projects_service.py -v
```

Expected: collection error — `flake_analysis.api.services.projects_service` does not exist.

### Step 1.3: Implement schemas

- [ ] **Replace `src/flake_analysis/api/schemas/projects.py` with:**

```python
"""Project lifecycle schemas (W10-C).

W10-C dropped the legacy `validate-paths` schemas and the
`analysis_folder/raw_images_dir/annotations_path` echo fields from
`ProjectHandle` — those served the pre-W10 path-only routing.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    """POST /projects body."""
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class PatchProjectRequest(BaseModel):
    """PATCH /projects/{pid} body — all fields optional, omitted = no change."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class ProjectHandle(BaseModel):
    """List item / GET response. Lightweight — does NOT carry scan_count."""
    project_id: str
    name: str
    owner_id: UUID
    description: str | None
    created_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectHandle]


class ProjectDetail(ProjectHandle):
    """GET /projects/{pid} — adds scan_count."""
    scan_count: int
```

### Step 1.4: Implement the service module

- [ ] **Create `src/flake_analysis/api/services/projects_service.py`:**

```python
"""DB-side helpers for projects CRUD (W10-C)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Project, Scan


class DuplicateProjectName(Exception):
    """Raised when (owner_id, name) UNIQUE violation fires."""


class ProjectNotFound(Exception):
    """Raised when a project_id has no row."""


class ProjectHasScans(Exception):
    """Raised when DELETE is attempted on a project with at least 1 scan (D2)."""

    def __init__(self, project_id: str, scan_count: int):
        self.project_id = project_id
        self.scan_count = scan_count
        super().__init__(f"project {project_id!r} has {scan_count} scan(s)")


async def create_project(
    session: AsyncSession,
    *,
    owner_id: UUID,
    name: str,
    description: str | None = None,
) -> Project:
    p = Project(name=name, owner_id=owner_id, description=description)
    session.add(p)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProjectName(name) from exc
    return p


async def list_projects_for_user(
    session: AsyncSession, *, user_id: UUID
) -> list[Project]:
    """v1: returns projects the user owns. v2 will union with project_users grants."""
    result = await session.execute(
        select(Project)
        .where(Project.owner_id == user_id)
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


async def get_project(session: AsyncSession, *, project_id: str) -> Project:
    p = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if p is None:
        raise ProjectNotFound(project_id)
    return p


async def get_project_with_scan_count(
    session: AsyncSession, *, project_id: str
) -> tuple[Project, int]:
    p = await get_project(session, project_id=project_id)
    n = (
        await session.execute(
            select(func.count(Scan.id)).where(Scan.project_id == project_id)
        )
    ).scalar_one()
    return p, int(n)


async def patch_project(
    session: AsyncSession,
    *,
    project_id: str,
    name: str | None = None,
    description: str | None = None,
) -> Project:
    p = await get_project(session, project_id=project_id)
    if name is not None:
        p.name = name
    if description is not None:
        p.description = description
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProjectName(name or "") from exc
    return p


async def delete_project_or_409(
    session: AsyncSession, *, project_id: str
) -> None:
    """Delete iff no scans exist; otherwise raise ProjectHasScans (D2)."""
    p = await get_project(session, project_id=project_id)
    n = (
        await session.execute(
            select(func.count(Scan.id)).where(Scan.project_id == project_id)
        )
    ).scalar_one()
    if n > 0:
        raise ProjectHasScans(project_id, int(n))
    await session.delete(p)
    await session.flush()
```

### Step 1.5: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_projects_service.py -v
```

Expected: 5 passed.

### Step 1.6: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/schemas/projects.py src/flake_analysis/api/services/projects_service.py tests/api/test_projects_service.py
git commit -m "feat(api): projects CRUD service + schemas (W10-C.1)"
```

---

## Task 2 — `routes/projects.py` full rewrite (CRUD)

**Files:**
- Modify: `src/flake_analysis/api/routes/projects.py`
- Modify: `tests/api/test_projects.py`

**Why:** Wire the service layer to HTTP. RBAC: list = any authed user (filtered to their own); detail/patch/delete = `require_project_role(min=editor)` — but list/create can't depend on `require_project_role` because the project doesn't exist yet on create / aren't filtered on list.

### Step 2.1: Write the failing test

- [ ] **Replace `tests/api/test_projects.py` with (or extend — read first):**

```python
"""W10-C: projects CRUD HTTP integration tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.main import app
from flake_analysis.db.models import Scan

pytestmark = pytest.mark.pg


async def _client(pg_session, current_user):
    """Wire the FastAPI app's get_db_session + get_current_user to test fixtures."""
    from flake_analysis.api.deps import get_db_session
    from flake_analysis.api.auth import get_current_user

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_create_then_list_then_get(pg_session, sample_user):
    async with await _client(pg_session, sample_user) as client:
        r = await client.post("/api/v1/projects", json={"name": "Alpha"})
        assert r.status_code == 201
        body = r.json()
        pid = body["project_id"]
        assert body["name"] == "Alpha"

        r = await client.get("/api/v1/projects")
        assert r.status_code == 200
        names = [p["name"] for p in r.json()["projects"]]
        assert "Alpha" in names

        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["scan_count"] == 0


@pytest.mark.asyncio
async def test_create_dup_name_returns_409(pg_session, sample_user):
    async with await _client(pg_session, sample_user) as client:
        await client.post("/api/v1/projects", json={"name": "dup"})
        r = await client.post("/api/v1/projects", json={"name": "dup"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "duplicate_project_name"


@pytest.mark.asyncio
async def test_patch_renames_project(pg_session, sample_user):
    async with await _client(pg_session, sample_user) as client:
        r = await client.post("/api/v1/projects", json={"name": "Original"})
        pid = r.json()["project_id"]

        r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "Renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_empty_project(pg_session, sample_user):
    async with await _client(pg_session, sample_user) as client:
        r = await client.post("/api/v1/projects", json={"name": "tmp"})
        pid = r.json()["project_id"]

        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 204

        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_with_scans_returns_409(pg_session, sample_user):
    async with await _client(pg_session, sample_user) as client:
        r = await client.post("/api/v1/projects", json={"name": "with-scan"})
        pid = r.json()["project_id"]

        # Insert a scan directly via session
        pg_session.add(
            Scan(name="s1", material="graphene", project_id=pid, created_by_id=sample_user.id)
        )
        await pg_session.commit()

        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "project_has_scans"
        assert body["error"]["details"]["scan_count"] == 1
```

### Step 2.2: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_projects.py -v
```

Expected: many FAILs (route still uses legacy validate-paths/active impl).

### Step 2.3: Rewrite the router

- [ ] **Replace `src/flake_analysis/api/routes/projects.py` with:**

```python
"""Projects CRUD (W10-C). Replaces the pre-W10 path-only stub."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    PatchProjectRequest,
    ProjectDetail,
    ProjectHandle,
    ProjectListResponse,
)
from flake_analysis.api.services import projects_service
from flake_analysis.api.services.projects_service import (
    DuplicateProjectName,
    ProjectHasScans,
    ProjectNotFound,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_handle(p) -> ProjectHandle:
    return ProjectHandle(
        project_id=p.id,
        name=p.name,
        owner_id=p.owner_id,
        description=p.description,
        created_at=p.created_at,
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectListResponse:
    rows = await projects_service.list_projects_for_user(session, user_id=user.id)
    return ProjectListResponse(projects=[_to_handle(r) for r in rows])


@router.post(
    "",
    response_model=ProjectHandle,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    req: CreateProjectRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectHandle:
    try:
        p = await projects_service.create_project(
            session, owner_id=user.id, name=req.name, description=req.description,
        )
    except DuplicateProjectName as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_project_name", "message": str(exc)},
        )
    await session.commit()
    return _to_handle(p)


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectDetail:
    try:
        p, n = await projects_service.get_project_with_scan_count(
            session, project_id=project_id,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    handle = _to_handle(p)
    return ProjectDetail(scan_count=n, **handle.model_dump())


@router.patch("/{project_id}", response_model=ProjectHandle)
async def patch_project(
    project_id: str,
    req: PatchProjectRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectHandle:
    try:
        p = await projects_service.patch_project(
            session,
            project_id=project_id,
            name=req.name,
            description=req.description,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    except DuplicateProjectName as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_project_name", "message": str(exc)},
        )
    await session.commit()
    return _to_handle(p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    try:
        await projects_service.delete_project_or_409(session, project_id=project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    except ProjectHasScans as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_has_scans",
                "details": {"scan_count": exc.scan_count},
            },
        )
    await session.commit()
    return Response(status_code=204)
```

> **Note on error envelope:** the `app_error_handler` in `api/errors.py` wraps `AppError` instances; raw `HTTPException(detail={...})` flows through FastAPI's default handler. To match the existing `{"error": {...}}` envelope tests assert on, we pass `detail` as a dict shaped like `{"code": ..., "details": ...}`. The default handler returns `{"detail": {...}}`. Adjust by either (a) extending `AppError` for the four error cases above, or (b) wrapping in a custom `ProjectsAppError` subclass. **Pick (a)** for parity with existing route conventions — read `api/errors.py` and add `DuplicateProjectName(AppError)`, `ProjectNotFound(AppError)`, `ProjectHasScans(AppError)` as 3 thin subclasses, then raise those instead of HTTPException.

### Step 2.4: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_projects.py -v
```

Expected: 5 passed.

### Step 2.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/projects.py src/flake_analysis/api/errors.py tests/api/test_projects.py
git commit -m "feat(api): projects CRUD route surface (W10-C.2)"
```

---

## Task 3 — `routes/scans.py` add per-project listing

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py`
- Modify: `tests/api/test_scans_create.py` or create a `test_scans_list.py`

**Why:** Frontend (W10-D) needs `GET /api/v1/projects/{pid}/scans` for the scan-picker dropdown. Existing `routes/scans.py` from W5 has POST create; we add GET list.

### Step 3.1: Write the failing test

- [ ] **Create `tests/api/test_scans_list.py`:**

```python
"""W10-C: GET /projects/{pid}/scans listing."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app

pytestmark = pytest.mark.pg


async def _client(pg_session, current_user):
    from flake_analysis.api.deps import get_db_session
    from flake_analysis.api.auth import get_current_user

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_scans_for_project_empty(pg_session, sample_user, sample_project_factory):
    proj = await sample_project_factory(owner=sample_user)
    async with await _client(pg_session, sample_user) as client:
        r = await client.get(f"/api/v1/projects/{proj.id}/scans")
        assert r.status_code == 200
        assert r.json() == {"scans": []}


@pytest.mark.asyncio
async def test_list_scans_returns_only_for_that_project(
    pg_session, sample_user, sample_project_factory, sample_scan_factory,
):
    p1 = await sample_project_factory(owner=sample_user)
    p2 = await sample_project_factory(owner=sample_user)
    s1 = await sample_scan_factory(project=p1, name="s1")
    s2 = await sample_scan_factory(project=p1, name="s2")
    s3 = await sample_scan_factory(project=p2, name="s3")
    await pg_session.commit()

    async with await _client(pg_session, sample_user) as client:
        r = await client.get(f"/api/v1/projects/{p1.id}/scans")
        names = [s["name"] for s in r.json()["scans"]]
        assert sorted(names) == ["s1", "s2"]
```

### Step 3.2: Run — expect FAIL

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_scans_list.py -v
```

Expected: 404 — endpoint missing.

### Step 3.3: Add the GET listing to `routes/scans.py`

- [ ] **Append to `src/flake_analysis/api/routes/scans.py`:**

```python
@router.get("/projects/{project_id}/scans")
async def list_scans_for_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """List scans belonging to a project, newest first."""
    rows = (
        await session.execute(
            select(Scan)
            .where(Scan.project_id == project_id)
            .order_by(Scan.created_at.desc())
        )
    ).scalars().all()
    return {
        "scans": [
            {
                "scan_id": r.id,
                "name": r.name,
                "material": r.material,
                "image_count": r.image_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
```

### Step 3.4: Run — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_scans_list.py -v
```

Expected: 2 passed.

### Step 3.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scans_list.py
git commit -m "feat(api): GET /projects/{pid}/scans (W10-C.3)"
```

---

## Task 4 — Migrate the 6 analysis routers (parallel sub-batches)

**Files:**
- Modify: `src/flake_analysis/api/routes/data.py`
- Modify: `src/flake_analysis/api/routes/run.py`
- Modify: `src/flake_analysis/api/routes/selector.py`
- Modify: `src/flake_analysis/api/routes/clustering.py`
- Modify: `src/flake_analysis/api/routes/explorer.py`
- Modify: `src/flake_analysis/api/routes/static.py`
- Modify: corresponding tests in `tests/api/test_data_*.py`, `test_run_*.py`, `test_selector_*.py`, `test_clustering_*.py`, `test_explorer_*.py`.

**Why:** Heart of W10-C. The 6 routers all share the same shape transform — change prefix from `/projects/{project_id}/X` to `/projects/{project_id}/scans/{scan_id}/X`, swap deps from `get_manifest()`/`get_active_analysis()`/`acquire_project_lock()` to the W10-B per-scan equivalents, update usage events to carry both `project_id` and `scan_id`. PM should dispatch this as **3 parallel sub-batches** of 2 routers each (e.g. data+static, run+selector, clustering+explorer) — each sub-batch is independent and can land as a separate commit.

### Step 4.1: Build the canonical transform recipe (apply to each router)

For each `routes/X.py`:

1. **Router prefix change:**
   ```python
   # Before
   router = APIRouter(prefix="/projects/{project_id}/X", tags=["X"])
   # After
   router = APIRouter(prefix="/projects/{project_id}/scans/{scan_id}/X", tags=["X"])
   ```

2. **Dep imports change:**
   ```python
   # Before
   from flake_analysis.api.deps import get_active_analysis, get_manifest
   from flake_analysis.api.mutex import acquire_project_lock
   # After
   from flake_analysis.api.deps import get_active_analysis, get_manifest
   from flake_analysis.api.mutex import acquire_scan_lock
   ```
   (`get_manifest` and `get_active_analysis` moved to per-scan signatures in W10-B; their import path is unchanged.)

3. **Endpoint signatures gain `scan_id: int`:**
   ```python
   # Before
   @router.get("/manifest")
   async def get_manifest_endpoint(
       manifest: Manifest = Depends(get_manifest),
       analysis = Depends(get_active_analysis),
       user: User = Depends(get_current_user),
   ) -> ManifestModel: ...
   # After
   @router.get("/manifest")
   async def get_manifest_endpoint(
       project_id: str,
       scan_id: int,
       session: Annotated[AsyncSession, Depends(get_db_session)],
       user: User = Depends(get_current_user),
   ) -> ManifestModel:
       manifest = await get_manifest(project_id=project_id, scan_id=scan_id)
       analysis = await get_active_analysis(scan_id=scan_id, session=session)
       ...
   ```
   (W10-B's `get_manifest` is now a plain async helper, not a FastAPI dep — call it directly. Same for `get_active_analysis`.)

4. **Mutex change:**
   ```python
   # Before
   lock_cm = acquire_project_lock(project_id)
   # After
   lock_cm = acquire_scan_lock(scan_id)
   ```

5. **Usage event payloads carry both:**
   ```python
   # Before
   await emit(session, user, "scan_run", {"step": "thumbnails", "project_id": project_id})
   # After
   await emit(session, user, "scan_run", {
       "step": "thumbnails",
       "project_id": project_id,
       "scan_id": scan_id,
   })
   ```

### Step 4.2: Sub-batch 1 — `data.py` + `static.py`

- [ ] **Read both files first:**

```bash
cat src/flake_analysis/api/routes/data.py
cat src/flake_analysis/api/routes/static.py
```

- [ ] **Apply the transform in §4.1 to both. Update the corresponding test files:** `tests/api/test_data_manifest.py`, `tests/api/test_data_domain_stats.py`, `tests/api/test_data_selection.py`, `tests/api/test_data_clustering_*.py`, `tests/api/test_data_annotation_preview.py`, `tests/api/test_data_explorer_*.py`, `tests/api/test_path_safety.py`. Each test client call changes from `/api/v1/projects/{pid}/data/X` → `/api/v1/projects/{pid}/scans/{sid}/data/X`.

- [ ] **Test fixtures:** add a `scan_id` fixture that returns a real `Scan.id` (sample_scan_factory from W10-B Task 4 fixtures).

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='...' SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api/test_data_*.py tests/api/test_path_safety.py -v
```

Expected: all green.

- [ ] **Commit:**

```bash
git add src/flake_analysis/api/routes/data.py src/flake_analysis/api/routes/static.py tests/api/test_data_*.py tests/api/test_path_safety.py
git commit -m "feat(api): /projects/{pid}/scans/{sid}/data + /static (W10-C.4a)"
```

### Step 4.3: Sub-batch 2 — `run.py` + `selector.py`

- [ ] **Apply the transform.** `run.py` is the largest — 4 SSE endpoints (thumbnails / background / domain_stats / domain_proximity). Each one acquires the lock, emits a usage event, runs the pipeline through `ProgressBridge`, and exits via `lock_cm.__aexit__`. Replace `acquire_project_lock(project_id)` with `acquire_scan_lock(scan_id)` and emit both IDs.

- [ ] **Update tests:** `tests/api/test_run_thumbnails_sse.py`, `test_run_background_sse.py`, `test_run_domain_stats_sse.py`, `test_run_domain_proximity_sse.py`, `test_run_selector_sse.py`, `test_run_emits_usage.py`, `test_run_explorer_*`, `test_run_clustering_*`, `test_clustering_mutex_sharing.py`. The mutex-sharing test was specifically designed around per-project locks; rewrite assertions to per-scan lock semantics.

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='...' SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api/test_run_*.py tests/api/test_selector_*.py -v
```

Expected: all green.

- [ ] **Commit:**

```bash
git add src/flake_analysis/api/routes/run.py src/flake_analysis/api/routes/selector.py tests/api/test_run_*.py tests/api/test_selector_*.py tests/api/test_clustering_mutex_sharing.py
git commit -m "feat(api): /projects/{pid}/scans/{sid}/run + /selector + per-scan mutex (W10-C.4b)"
```

### Step 4.4: Sub-batch 3 — `clustering.py` + `explorer.py`

- [ ] **Apply the transform.** Both share the same shape as `run.py` but are smaller (1–2 endpoints each).

- [ ] **Update tests:** `tests/api/test_clustering_*.py` (excluding `test_clustering_mutex_sharing.py` already covered), `tests/api/test_explorer_*.py`, `test_run_explorer_*`.

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='...' SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api/test_clustering_*.py tests/api/test_explorer_*.py -v
```

Expected: all green.

- [ ] **Commit:**

```bash
git add src/flake_analysis/api/routes/clustering.py src/flake_analysis/api/routes/explorer.py tests/api/test_clustering_*.py tests/api/test_explorer_*.py
git commit -m "feat(api): /projects/{pid}/scans/{sid}/clustering + /explorer (W10-C.4c)"
```

---

## Task 5 — Final acceptance gate

### Step 5.1: Run the full PG-marked suite

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api -m pg --ignore=tests/scripts -q
```

Expected: all PASS (target ~38+ counting new W10-A/B/C additions). Capture the count and compare to baseline; any unexpected failure goes back to the relevant Task.

### Step 5.2: Run the non-PG suite

- [ ] **Run:**

```
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_ANALYSIS_ROOT=/tmp/saa-test-root \
uv run pytest tests/api -m "not pg" -q
```

Expected: all PASS.

### Step 5.3: Confirm OpenAPI surface

- [ ] **Quick smoke (read-only):** ask the implementer to print the route table:

```python
# scripts/print_routes.py (one-shot, do NOT commit)
from flake_analysis.api.main import app
for r in app.routes:
    if hasattr(r, "methods"):
        print(f"{','.join(r.methods):10} {r.path}")
```

Expected output includes:
- `GET /api/v1/projects` (list)
- `POST /api/v1/projects` (create)
- `GET /api/v1/projects/{project_id}` (detail)
- `PATCH /api/v1/projects/{project_id}` (rename)
- `DELETE /api/v1/projects/{project_id}` (delete)
- `GET /api/v1/projects/{project_id}/scans` (list scans)
- `POST /api/v1/projects/{project_id}/scans` (create scan, from W5)
- `GET /api/v1/projects/{project_id}/scans/{scan_id}/data/manifest`
- `POST /api/v1/projects/{project_id}/scans/{scan_id}/run/{thumbnails|background|domain_stats|domain_proximity}`
- `POST /api/v1/projects/{project_id}/scans/{scan_id}/selector/...`
- `POST /api/v1/projects/{project_id}/scans/{scan_id}/clustering/...`
- `GET /api/v1/projects/{project_id}/scans/{scan_id}/explorer/...`
- legacy `/api/v1/scans/{scan_id}/...` (W5-B2 finalize/get) — keep, frontend already targets these by sid alone.

Delete the smoke script after running.

### Step 5.4: Update `docs/project-status.md`

- [ ] **In §3.1, append:**

> 2026-05-22 — W10-C (route surface) 완료. projects CRUD + per-scan analysis routes (`/projects/{pid}/scans/{sid}/...`) + per-scan mutex 적용. `tests/api -m pg` 전부 green. W10-D (frontend) 진입 가능.

```bash
git add docs/project-status.md
git commit -m "docs(status): mark W10-C complete — route surface migrated"
```

---

## Self-Review

**Spec coverage:**
- D1 — `get_active_analysis(scan_id)` (Task 4 transforms route signatures). ✓
- D2 — `delete_project_or_409` 409 envelope + test (Task 1, 2). ✓
- D4 — `acquire_scan_lock(scan_id)` swap (Task 4). ✓
- D5 — `get_manifest(project_id, scan_id)` (W10-B; this plan consumes). ✓
- "pid + sid both in path" — locked in §"Plan-level decision". ✓

**Placeholder scan:** none.

**Type consistency:**
- Path params: `project_id: str`, `scan_id: int` everywhere.
- Usage event payloads: `{"project_id": str, "scan_id": int, ...}`.

**Edge cases:**
- The `app_error_handler` envelope-wrapping (Task 2 §2.3 note): pick (a) — extend `AppError` for the 3 project-error cases. Test envelope shape matches existing `tests/api/test_errors.py` patterns.
- The two W5-B2 endpoints `/scans/{scan_id}/finalize` and `/scans/{scan_id}` (no project_id in path) stay where they are — they're scan-only operations and the frontend already calls them by sid. RBAC is on the create-scan endpoint that established the pid linkage.

---

## Open follow-up (out of W10-C scope)

- **PATCH /projects/{pid}/owner** — transfer ownership. Defer.
- **POST /projects/{pid}/users** — explicit ACL grant management UI. Today the W6.4 `routes/admin.py` covers it for admins; project owners getting a self-service UI is a v2 idea.
- **Project-level usage event** (`project_created`, `project_deleted`) — defer; usage events focus on compute today.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W10-C-route-surface.md`.

**Recommended execution mode:** Subagent-Driven. Tasks 1, 2, 3 are sequential. Task 4 dispatches **3 parallel sub-batches** (4a/4b/4c) — independent file pairs, each merges separately. Total ~30–60 min implementer time.

Dispatch order: 1 → 2 → 3 → (4a, 4b, 4c parallel) → 5.

**Hard dependencies:** W10-A merged (projects table). W10-B merged (per-scan deps + mutex).
