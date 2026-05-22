# W5-B1 — Materials Vocabulary + Scan Creation API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first three FastAPI endpoints of the W5 image upload flow on top of the W5-A schema head (`0003_w5a_materials_uploads`): list/auto-add materials vocabulary and create a per-scan upload row. This plan is the first half of the original W5-B; presign/complete/finalize are deferred to W5-B2 (which depends on this plan being merged). All routes require `get_current_user` (dev-bypass + Cognito both supported).

**Architecture:** W5-A migration is already applied on `saa_test` (verified 2026-05-22 — `materials` populated with 5 seed rows, `scans.material` NOT NULL+FK, `images.grid_ix/grid_iy` NOT NULL+UNIQUE). This plan introduces two new routers — `materials` and `scans` — and the leaf-level helper modules they share. The `scans` router is mounted with a stub here; W5-B2 will append the presign/complete/finalize/get handlers to it. The path `/projects/{project_id}/scans` is path-only-for-routing in v1 — `scans` has no `project_id` FK in v7 (locked decision; see Naming Decisions below).

**Tech Stack:** FastAPI 0.110+, pydantic v2, SQLAlchemy 2.x async ORM, PostgreSQL 16, pytest-asyncio strict + `pytest.mark.pg`, httpx ASGITransport.

---

## Naming Decisions (locked 2026-05-22)

- Auth: `from flake_analysis.api.auth import User, get_current_user` — same import that all v7 routes already use.
- Routes scoped: `/api/v1/materials`, `/api/v1/projects/{project_id}/scans` (create). The `/api/v1/scans/{scan_id}/...` namespace is reserved for W5-B2 endpoints.
- **`project_id` is path-only — not stored on the scan row in v1.** Future v2 work will introduce a `projects` table and add `scans.project_id` FK in a single migration that also rewrites manifest-based pipelines. The frontend already uses `"local"` as the project alias and is unaffected by this decision. (Locked 2026-05-22 — user confirmed "1 project = 1 scan v1, defer multi-scan-per-project to v2".)
- The endpoint MAY emit a `usage_events` row tagged with `project_id` in `value_json` (matches the existing `run.py` convention — see `/projects/{project_id}/run/thumbnails` etc). W5-B1 does not emit one for scan creation; that lands at finalize time in W5-B2.
- Material name normalization: `strip().lower()`. Schema layer accepts raw input, route layer normalizes before insert.
- Material insert is idempotent via `INSERT ... ON CONFLICT (name) DO NOTHING` followed by a SELECT to discover whether the row pre-existed (RETURNING only fires on actual insert).

---

## File Structure

- Create: `src/flake_analysis/api/schemas/upload.py` — pydantic models for materials + scan-create bodies/responses (W5-B2 will append presign/complete/finalize schemas).
- Create: `src/flake_analysis/api/services/upload_service.py` — DB write helpers (idempotent material insert, scan creation; W5-B2 will append upload-session/upload-item lifecycle helpers).
- Create: `src/flake_analysis/api/routes/materials.py` — `GET /materials`, `POST /materials`.
- Create: `src/flake_analysis/api/routes/scans.py` — `POST /projects/{project_id}/scans` plus a stub for the namespace W5-B2 expands.
- Modify: `src/flake_analysis/api/main.py` — mount the two new routers.
- Create: `tests/api/test_upload_schemas.py` — schema unit tests.
- Create: `tests/api/test_materials_routes.py` — PG + httpx.
- Create: `tests/api/test_scans_create.py` — PG + httpx.

All tests use the `get_db_session` dependency override pattern from `tests/api/test_admin_usage_route.py` — read that file FIRST and mirror the override pattern in every test.

---

## Verification Env Block

All test runs MUST use this exact prefix:

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
```

Migration assumed at head `0003_w5a_materials_uploads` (W5-A complete).

---

## Task 1 — Schemas + idempotent material insert helper

**Files:**
- Create: `src/flake_analysis/api/schemas/upload.py`
- Create: `src/flake_analysis/api/services/upload_service.py`

**Why:** Land the leaf-level building blocks before any route exists. Schemas can be unit-tested without hitting the DB or HTTP layer; the service helpers are imported by Tasks 2 and 3.

### Step 1.1: Write the failing schemas test

- [ ] **Create `tests/api/test_upload_schemas.py`:**

```python
"""Pydantic schema sanity tests for W5-B upload models (B1 subset)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.upload import (
    CreateScanRequest,
    MaterialItem,
    MaterialCreateRequest,
    MaterialCreateResponse,
)


def test_create_scan_request_accepts_minimal():
    req = CreateScanRequest(name="s1", material="graphene", image_count=10)
    assert req.extra_metadata == {}


def test_create_scan_request_rejects_zero_image_count():
    with pytest.raises(ValidationError):
        CreateScanRequest(name="s1", material="graphene", image_count=0)


def test_material_item_shape():
    from datetime import datetime, timezone
    m = MaterialItem(name="graphene", created_at=datetime.now(timezone.utc))
    assert m.name == "graphene"


def test_material_create_normalizes_name():
    """Name normalization is the route's job; schema only enforces non-empty."""
    req = MaterialCreateRequest(name="  Graphene  ")
    assert req.name == "  Graphene  "  # raw value preserved at schema layer


def test_material_create_response():
    r = MaterialCreateResponse(name="graphene", created=True)
    assert r.created is True
```

### Step 1.2: Run — expect ImportError

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_upload_schemas.py -v
```

Expected: collection error — `ModuleNotFoundError: flake_analysis.api.schemas.upload`.

### Step 1.3: Implement schemas (B1 subset)

- [ ] **Create `src/flake_analysis/api/schemas/upload.py`:**

```python
"""Pydantic schemas for the W5-B upload flow.

W5-B1 subset: materials + scan-create models. W5-B2 will append presign,
complete, finalize, and scan-detail schemas to this file.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---- materials ----

class MaterialItem(BaseModel):
    name: str
    created_at: datetime


class MaterialListResponse(BaseModel):
    materials: list[MaterialItem]


class MaterialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MaterialCreateResponse(BaseModel):
    name: str
    created: bool


# ---- scans (create) ----

class CreateScanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    material: str = Field(min_length=1, max_length=128)
    image_count: int = Field(gt=0, le=100_000)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class ScanResponse(BaseModel):
    scan_id: int
    name: str
    material: str
    image_count: int
    extra_metadata: dict[str, Any]
    created_at: datetime
```

### Step 1.4: Run schemas test — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_upload_schemas.py -v
```

Expected: 5 passed.

### Step 1.5: Implement upload_service helpers (B1 subset)

- [ ] **Create `src/flake_analysis/api/services/upload_service.py`:**

```python
"""DB write helpers for the W5-B upload flow.

W5-B1 subset: idempotent material insert (on-conflict-do-nothing) + scan
creation. W5-B2 will append upload-session and upload-item lifecycle helpers
to this same file.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Material, Scan


def normalize_material_name(raw: str) -> str:
    """Trim + lowercase. Empty after trim is the caller's problem."""
    return raw.strip().lower()


async def upsert_material(
    session: AsyncSession,
    *,
    name: str,
    created_by_id: UUID | None,
) -> tuple[str, bool]:
    """Insert material idempotently; return (canonical_name, created_flag).

    Uses INSERT ... ON CONFLICT DO NOTHING then SELECT to discover whether
    the row pre-existed. The follow-up SELECT is required because RETURNING
    only fires on an actual insert.
    """
    canonical = normalize_material_name(name)
    if not canonical:
        raise ValueError("material name is empty after normalization")

    stmt = (
        pg_insert(Material)
        .values(name=canonical, created_by_id=created_by_id)
        .on_conflict_do_nothing(index_elements=["name"])
        .returning(Material.name)
    )
    result = await session.execute(stmt)
    inserted = result.scalar_one_or_none()
    await session.flush()
    return canonical, inserted is not None


async def list_materials(session: AsyncSession) -> list[Material]:
    """Return all materials alphabetical by name."""
    result = await session.execute(select(Material).order_by(Material.name))
    return list(result.scalars().all())


async def create_scan(
    session: AsyncSession,
    *,
    name: str,
    material: str,
    image_count: int,
    extra_metadata: dict,
    created_by_id: UUID,
) -> Scan:
    """Create a scan row. Material is auto-added via upsert_material first."""
    canonical, _ = await upsert_material(
        session, name=material, created_by_id=created_by_id,
    )
    scan = Scan(
        name=name,
        material=canonical,
        image_count=image_count,
        extra_metadata=extra_metadata,
        created_by_id=created_by_id,
    )
    session.add(scan)
    await session.flush()
    await session.refresh(scan)
    return scan
```

### Step 1.6: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/schemas/upload.py src/flake_analysis/api/services/upload_service.py tests/api/test_upload_schemas.py
git commit -m "feat(api): W5-B1.1 schemas + upload_service helpers (materials + scan create)"
```

---

## Task 2 — Materials routes (`GET /materials`, `POST /materials`)

**Files:**
- Create: `src/flake_analysis/api/routes/materials.py`
- Create: `src/flake_analysis/api/routes/scans.py` (stub — Task 3 fills it)
- Modify: `src/flake_analysis/api/main.py`
- Create: `tests/api/test_materials_routes.py`

**Why:** Materials is the smallest endpoint pair; landing it first proves the whole router→main wiring + dep-override test pattern before tackling the more involved scan flow.

### Step 2.1: Write the failing materials route test

- [ ] **Create `tests/api/test_materials_routes.py`:**

```python
"""W5-B1.2 — GET/POST /materials route tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


@pytest.mark.asyncio
async def test_list_materials_includes_seed(pg_session):
    """GET /materials returns at least the W5-A seed rows alphabetically."""
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/materials")
            assert r.status_code == 200
            names = [m["name"] for m in r.json()["materials"]]
            for expected in ["MoS2", "WS2", "WSe2", "graphene", "hBN"]:
                assert expected in names
            assert names == sorted(names)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_post_materials_creates_new(pg_session):
    """POST /materials with a fresh name returns created=True."""
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/materials", json={"name": "  Si  "})
            assert r.status_code == 200
            body = r.json()
            assert body == {"name": "si", "created": True}
            # Second call is idempotent
            r2 = await c.post("/api/v1/materials", json={"name": "SI"})
            assert r2.json() == {"name": "si", "created": False}
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_post_materials_rejects_blank(pg_session):
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/materials", json={"name": "   "})
            assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db_session, None)
```

### Step 2.2: Run — expect 404

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_materials_routes.py -v
```

Expected: 3 failed with 404 (router not mounted yet).

### Step 2.3: Implement materials router

- [ ] **Create `src/flake_analysis/api/routes/materials.py`:**

```python
"""W5-B1.2 — GET /materials, POST /materials."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.upload import (
    MaterialCreateRequest,
    MaterialCreateResponse,
    MaterialItem,
    MaterialListResponse,
)
from flake_analysis.api.services import upload_service

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("")
async def list_materials(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MaterialListResponse:
    rows = await upload_service.list_materials(session)
    return MaterialListResponse(
        materials=[MaterialItem(name=r.name, created_at=r.created_at) for r in rows],
    )


@router.post("")
async def create_material(
    req: MaterialCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MaterialCreateResponse:
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="material name is blank")
    canonical, created = await upload_service.upsert_material(
        session, name=req.name, created_by_id=user.id,
    )
    await session.commit()
    return MaterialCreateResponse(name=canonical, created=created)
```

### Step 2.4: Create scans.py stub so main.py imports

- [ ] **Create `src/flake_analysis/api/routes/scans.py` with an empty router so main.py imports cleanly:**

```python
"""W5-B scans router (placeholder — Task 3 adds scan-create; W5-B2 adds presign/complete/finalize/get)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["scans"])
```

### Step 2.5: Mount routers in main.py

- [ ] **In `src/flake_analysis/api/main.py`, add to the import line that pulls in route modules:**

```python
from flake_analysis.api.routes import (
    health, version, projects, data, run, selector, clustering, explorer, static, auth, admin, admin_usage,
    materials, scans,
)
```

- [ ] **Add the router mounts immediately after `admin_usage.router`:**

```python
    app.include_router(materials.router, prefix="/api/v1")
    app.include_router(scans.router, prefix="/api/v1")
```

### Step 2.6: Run materials tests — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_materials_routes.py -v
```

Expected: 3 passed.

### Step 2.7: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/materials.py src/flake_analysis/api/routes/scans.py src/flake_analysis/api/main.py tests/api/test_materials_routes.py
git commit -m "feat(api): materials routes (GET + POST, idempotent upsert)"
```

---

## Task 3 — `POST /projects/{project_id}/scans` (create scan)

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py`
- Create: `tests/api/test_scans_create.py`

**Why:** Creating a scan is the gateway to the rest of the upload flow — every presign and complete call (W5-B2) needs a scan id. We also exercise the auto-add-material path here (`POST /materials` is one entrypoint, scan-create is the other).

### Step 3.1: Write the failing scan-create test

- [ ] **Create `tests/api/test_scans_create.py`:**

```python
"""W5-B1.3 — POST /projects/{pid}/scans tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import Material, Scan

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


@pytest.mark.asyncio
async def test_create_scan_with_known_material(pg_session):
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/projects/local/scans",
                json={
                    "name": "scan_2026_05_22_a",
                    "material": "graphene",
                    "image_count": 100,
                    "extra_metadata": {"microscope": "Olympus BX53M"},
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["material"] == "graphene"
            assert body["image_count"] == 100
            assert body["extra_metadata"] == {"microscope": "Olympus BX53M"}
            assert isinstance(body["scan_id"], int)
            # Verify in DB
            row = (await pg_session.execute(
                select(Scan).where(Scan.id == body["scan_id"])
            )).scalar_one()
            assert row.created_by_id is not None
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_scan_auto_adds_material(pg_session):
    """Unknown material is normalized + inserted, then scan binds to it."""
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/projects/local/scans",
                json={
                    "name": "scan_b",
                    "material": "  NewMat  ",
                    "image_count": 5,
                },
            )
            assert r.status_code == 201
            assert r.json()["material"] == "newmat"
            mat = (await pg_session.execute(
                select(Material).where(Material.name == "newmat")
            )).scalar_one()
            assert mat is not None
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_scan_rejects_zero_image_count(pg_session):
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/projects/local/scans",
                json={"name": "x", "material": "graphene", "image_count": 0},
            )
            assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db_session, None)
```

### Step 3.2: Run — expect 404 (route doesn't exist)

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_scans_create.py -v
```

Expected: 3 failed with 404.

### Step 3.3: Implement POST scan endpoint

- [ ] **Replace `src/flake_analysis/api/routes/scans.py` contents:**

```python
"""W5-B scans router — create scan (W5-B1).

W5-B2 appends presign, complete, finalize, and GET handlers to this module.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.upload import (
    CreateScanRequest,
    ScanResponse,
)
from flake_analysis.api.services import upload_service

router = APIRouter(tags=["scans"])


@router.post(
    "/projects/{project_id}/scans",
    status_code=status.HTTP_201_CREATED,
    response_model=ScanResponse,
)
async def create_scan(
    project_id: str,
    req: CreateScanRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanResponse:
    """Create a scan under the given project.

    NOTE: project_id is currently routing-only (no scans.project_id FK in v7).
    See "Open follow-up" in the W5-B1 plan for the deferred binding (locked:
    v1 leaves it path-only; v2 introduces a projects table).
    """
    scan = await upload_service.create_scan(
        session,
        name=req.name,
        material=req.material,
        image_count=req.image_count,
        extra_metadata=req.extra_metadata,
        created_by_id=user.id,
    )
    await session.commit()
    return ScanResponse(
        scan_id=scan.id,
        name=scan.name,
        material=scan.material,
        image_count=scan.image_count,
        extra_metadata=scan.extra_metadata,
        created_at=scan.created_at,
    )
```

### Step 3.4: Run scan-create tests — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_scans_create.py -v
```

Expected: 3 passed.

### Step 3.5: Acceptance gate — full W5-B1 suite + no regression

- [ ] **Run the W5-B1 suite:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api/test_upload_schemas.py tests/api/test_materials_routes.py tests/api/test_scans_create.py -v
```

Expected: 11 passed (5 schema + 3 materials + 3 scan-create).

- [ ] **Run the broader `tests/api -m pg` suite to verify no regression:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run pytest tests/api -m pg --ignore=tests/scripts -q
```

Expected: pre-W5-B1 baseline + 11 W5-B1 additions. No prior tests fail.

### Step 3.6: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scans_create.py
git commit -m "feat(api): scan create POST /projects/{pid}/scans (auto-adds material)"
```

### Step 3.7: Update project-status

- [ ] **In `docs/project-status.md` §3.1, append:**

> 2026-05-22 — W5-B1 백엔드 API (materials + scan create) 완료. 3개 엔드포인트, `tests/api -m pg` +11 통과. 다음: W5-B2 (presign/complete/finalize) 본 플랜 머지 후 착수.

```bash
git add docs/project-status.md
git commit -m "docs(status): mark W5-B1 complete — materials + scan create green on saa_test"
```

---

## Self-Review

**Spec coverage (W5-B1 scope):**
- D4 user-input metadata: `material` controlled vocab with auto-add, `image_count` declared, `extra_metadata` JSONB → all in Task 1 schemas + Task 3. ✓
- D5 auth: every route depends on `get_current_user`, `created_by_id` populated from `user.id`. ✓
- D6 routing: `/api/v1/projects/{pid}/scans` for create (path-only `project_id`); `/api/v1/scans/{sid}/...` namespace reserved for W5-B2. ✓
- 3 endpoints planned: `GET /materials`, `POST /materials`, `POST /projects/{pid}/scans`. ✓

**Placeholder scan:** none. The Task 2.4 `scans.py` placeholder router contains a real (empty) APIRouter object — Task 3 replaces it with the full module on first edit. No `# TODO`, no `// TODO`, no `[fill in]`, no `pass`-stubs.

**Type consistency:**
- `extra_metadata: Mapped[dict]` (W5-A) round-trips through `dict[str, Any]` pydantic. ✓
- `Material.name` is unique-indexed (W5-A) — `ON CONFLICT (name) DO NOTHING` is correct. ✓
- `Scan.material` is FK to `materials.name` (W5-A) — `upsert_material` runs first, so the FK is satisfied by the time `create_scan` inserts. ✓

**Edge cases:**
- Concurrent POST /materials with same name: ON CONFLICT DO NOTHING means at most one wins; the loser's SELECT sees the row and reports `created=False`. ✓
- Material name with mixed case + whitespace: normalized via `strip().lower()`; schema layer accepts raw. ✓
- `project_id` path with arbitrary string: not validated against any DB table (no `projects` table exists in v7). Documented in Task 3.3 docstring. ✓

**Boundary risks:**
- `pg_session` rollback semantics: routes use `await session.commit()` after success; the conftest `pg_session` is savepoint-per-test so the commit is contained. No HTTPException(409) flows in W5-B1 (those land in W5-B2 with the IntegrityError branches).
- `scans.project_id` does not exist on the schema in v7 — the path `project_id` is purely routing. Documented in Task 3.3 docstring AND in the open follow-up below. The frontend treats `project_id` as opaque.

---

## Open follow-up (out of W5-B1 scope)

### Resolved

- **`projects` table missing in saa_test / whether to add `scans.project_id` FK now or defer.** Locked 2026-05-22: v1 leaves `project_id` as path-only routing. v2 will introduce a `projects` table and add `scans.project_id` FK in a single migration that also rewrites manifest-based pipelines. The frontend already uses `"local"` as the project alias and is unaffected.

### Carried forward (deferred to W5-B2 or later)

1. **Resumability deferred per D3.** Per-scan upload session is per page-load. A future improvement: `GET /scans/{id}/upload-session` returning the active session id + per-tile status, so a page refresh can pick up where the user left off. Not in v1 scope.

2. **`upload_sessions.completed_files` / `failed_files` counters** are not maintained — the route layer treats them as informational only, since `finalize` (W5-B2) re-counts `images` from scratch. If we ever want a long-running upload progress meter, a follow-up will trigger DB-side updates from `complete`.

3. **`upload_sessions` being marked COMPLETED at finalize.** W5-B2's finalize only checks counts and emits a usage event; it does NOT flip `upload_session.status` to `COMPLETED`. Decide in a follow-up whether to do so (cosmetic; nothing reads it yet outside admin queries).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W5-B1-materials-and-scan-create.md`.

**Recommended execution mode:** Subagent-Driven. 3 tasks total — 1 leaf-level (schemas + service helpers), 1 simple route pair (materials), 1 scan-create endpoint. Each task ~10–15 min implementer + spec review + code review.

**Dispatch order:** 1 → 2 → 3 (strict; each task depends on prior — Task 2 imports Task 1's schemas + service, Task 3 builds on Task 2's router wiring).

**Pre-flight check before Task 1:** confirm alembic head on saa_test is `0003_w5a_materials_uploads` (W5-A complete). If not, halt and dispatch db-specialist to apply.

**Downstream:** W5-B2 (presign/complete/finalize/get) starts only after this plan is merged on main. W5-B2's Step 0.1 verifies the merge before proceeding.
