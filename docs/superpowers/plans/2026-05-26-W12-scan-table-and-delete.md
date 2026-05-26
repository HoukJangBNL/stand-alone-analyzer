# W12: Scan Table + Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `<select>` ScanPicker with a sortable table that surfaces scan metadata and supports delete (DB row + S3 prefix).

**Architecture:** Backend adds a `DELETE /scans/{scan_id}` endpoint that uses the existing `require_editor_for_scan` guard, deletes all S3 objects under `dev/scans/{scan_id}/`, and lets ON DELETE CASCADE wipe child rows (images / upload_sessions / upload_items / analyses). Frontend swaps the dropdown for a table with column-header sort and an inline delete button per row (confirm dialog). Same placement (top of each tab) — no routing changes.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, boto3 (S3 batch delete), React 18, TanStack Query (invalidation), vitest + jsdom.

---

## Pre-flight: Cascade verification

Before Task 1, confirm `images`/`upload_sessions`/`upload_items`/`analyses` cascade-delete from `scans`. From `src/flake_analysis/db/models/catalog.py:101-116`:

```python
images: Mapped[list[Image]] = relationship(
    back_populates="scan", cascade="all, delete-orphan", passive_deletes=True,
)
analyses: Mapped[list[Analysis]] = relationship(
    back_populates="scan", cascade="all, delete-orphan", passive_deletes=True,
)
upload_sessions: Mapped[list[UploadSession]] = relationship(
    back_populates="scan", cascade="all, delete-orphan", passive_deletes=True,
)
```

`UploadItem` has `session_id` FK to `upload_sessions` — verify `ondelete="CASCADE"` in `src/flake_analysis/db/models/upload.py:160-165`. If absent, add it before Task 1 starts (out-of-scope alembic migration — flag to PM).

`usage_events` carries `scan_id` only inside the `value_json` JSONB (no FK), so it survives scan delete by design — historical record. Don't touch.

---

## File Structure

**Backend (Python):**
- Create: `src/flake_analysis/api/services/s3_cleanup.py` — boto3 helper to delete all objects under a prefix.
- Modify: `src/flake_analysis/api/routes/scans.py` — append `DELETE /scans/{scan_id}` handler.
- Modify: `src/flake_analysis/api/services/scans_service.py` — already has `require_editor_for_scan`; nothing new there. Just call it from the new route.
- Test: `tests/api/test_scan_delete.py` (new) — outsider 404, viewer 403, editor success (DB+S3), idempotency.
- Test: `tests/api/services/test_s3_cleanup.py` (new) — moto-backed unit tests for the prefix-delete helper.

**Frontend (TypeScript):**
- Modify: `web/src/api/upload.ts` — add `deleteScan(scanId)` client.
- Create: `web/src/components/scans/ScanTable.tsx` — new component (replaces ScanPicker dropdown UI, keeps the same export point so all tab pages just keep importing `ScanPicker`).
- Modify: `web/src/components/scans/ScanPicker.tsx` — re-export the new ScanTable so consumers (Compute/Selector/Clustering/Explorer tab pages) need no changes. New component lives in its own file for testability.
- Test: `web/src/components/scans/__tests__/ScanTable.test.tsx` (new) — render rows, sort, delete confirm flow.
- Modify: `web/src/components/scans/__tests__/ScanPicker.test.tsx` — keep one minimal smoke test ("renders ScanTable") since the dropdown semantics are gone.

**Docs:**
- Modify: `docs/project-status.md` — log W12 completion.

---

## Task 1: ScanCleanupService — S3 prefix delete helper

**Files:**
- Create: `src/flake_analysis/api/services/s3_cleanup.py`
- Test: `tests/api/services/test_s3_cleanup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/services/test_s3_cleanup.py`:

```python
"""Tests for s3_cleanup.delete_prefix using moto mock_aws."""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from flake_analysis.api.services.s3_cleanup import delete_prefix


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        yield "test-bucket"


def _put(bucket: str, key: str, body: bytes = b"x") -> None:
    boto3.client("s3", region_name="us-east-2").put_object(
        Bucket=bucket, Key=key, Body=body
    )


def _list(bucket: str, prefix: str) -> list[str]:
    resp = boto3.client("s3", region_name="us-east-2").list_objects_v2(
        Bucket=bucket, Prefix=prefix
    )
    return [obj["Key"] for obj in resp.get("Contents", [])]


def test_delete_prefix_removes_all_objects_under_prefix(s3_bucket):
    _put(s3_bucket, "dev/scans/42/images/a.png")
    _put(s3_bucket, "dev/scans/42/images/b.png")
    _put(s3_bucket, "dev/scans/42/manifest.json")

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 3
    assert _list(s3_bucket, "dev/scans/42/") == []


def test_delete_prefix_does_not_touch_sibling_prefixes(s3_bucket):
    _put(s3_bucket, "dev/scans/42/images/a.png")
    _put(s3_bucket, "dev/scans/43/images/a.png")
    _put(s3_bucket, "dev/scans/420/images/a.png")  # numeric prefix collision guard

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 1
    assert _list(s3_bucket, "dev/scans/43/") == ["dev/scans/43/images/a.png"]
    assert _list(s3_bucket, "dev/scans/420/") == ["dev/scans/420/images/a.png"]


def test_delete_prefix_returns_zero_when_nothing_to_delete(s3_bucket):
    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/999/")
    assert deleted == 0


def test_delete_prefix_handles_more_than_1000_objects(s3_bucket):
    """delete_objects has a 1000-key limit per call — helper must page."""
    for i in range(1050):
        _put(s3_bucket, f"dev/scans/42/images/{i:04d}.png")

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 1050
    assert _list(s3_bucket, "dev/scans/42/") == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
export SAA_TEST_DATABASE_URL="postgresql+asyncpg://saa_test:saa_test@localhost:5432/saa_test"
uv run pytest tests/api/services/test_s3_cleanup.py -v
```

Expected: ImportError (`flake_analysis.api.services.s3_cleanup` does not exist).

- [ ] **Step 3: Write minimal implementation**

Create `src/flake_analysis/api/services/s3_cleanup.py`:

```python
"""S3 batch-delete helper for scan cleanup.

Deletes all objects under a key prefix in pages of 1000 (the AWS API limit
per `delete_objects` call). Trailing slash on the prefix is required so we
don't match e.g. `dev/scans/420/` when asked to clean `dev/scans/42/`.
"""
from __future__ import annotations

import logging
import os

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

_PAGE = 1000


def _client():
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def delete_prefix(*, bucket: str, prefix: str) -> int:
    """Delete every object whose key starts with `prefix`. Return count deleted.

    `prefix` must end with `/` to avoid sibling-prefix collisions
    (e.g. `dev/scans/42/` not `dev/scans/42`).
    """
    if not prefix.endswith("/"):
        raise ValueError(f"prefix must end with '/': {prefix!r}")
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        for i in range(0, len(contents), _PAGE):
            batch = contents[i : i + _PAGE]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
            )
            total += len(batch)
    logger.info("s3_cleanup.delete_prefix done", extra={"bucket": bucket, "prefix": prefix, "deleted": total})
    return total
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/api/services/test_s3_cleanup.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/api/services/test_s3_cleanup.py src/flake_analysis/api/services/s3_cleanup.py
git commit -m "feat(api): add s3_cleanup.delete_prefix helper"
```

---

## Task 2: DELETE /scans/{scan_id} — outsider returns 404

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (append handler at end of file)
- Test: `tests/api/test_scan_delete.py` (new)

This task does ONLY the route skeleton + auth guard. S3 + DB delete come in Task 3.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_scan_delete.py`. Use the same pattern as `test_scan_access_guards.py`:

```python
"""Tests for DELETE /scans/{scan_id} (W12)."""
from __future__ import annotations

from uuid import UUID, uuid4

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws

from flake_analysis.api.auth import User as DomainUser, UserRole, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import ProjectUser
from flake_analysis.db.models.user import User as ORMUser, ProjectRole


def _to_domain(orm_user: ORMUser) -> DomainUser:
    return DomainUser(
        id=orm_user.id,
        email=orm_user.email,
        role=orm_user.role,
        email_verified=True,
        cognito_sub=orm_user.cognito_sub or "test-sub",
    )


def _override_session(pg_session):
    async def _override():
        yield pg_session
    return _override


def _override_user(domain_user: DomainUser):
    async def _override():
        return domain_user
    return _override


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_outsider_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    outsider = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(outsider))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "scan_not_found"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
export SAA_TEST_DATABASE_URL="postgresql+asyncpg://saa_test:saa_test@localhost:5432/saa_test"
export SAA_S3_BUCKET="qpress-uploads"
uv run pytest tests/api/test_scan_delete.py::test_delete_outsider_404 -v
```

Expected: FAIL — 405 Method Not Allowed (route doesn't exist yet).

- [ ] **Step 3: Add the handler skeleton**

In `src/flake_analysis/api/routes/scans.py`, after the `get_scan` handler at the end of the file, append:

```python
@router.delete(
    "/scans/{scan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_scan(
    scan_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    """Delete a scan and all its data (DB cascade + S3 prefix wipe).

    Outsiders see 404 `scan_not_found` (no leak). In-project viewers see
    403 `forbidden{action: scan_delete}`. Editors / owner / admin succeed.
    """
    scan = await scans_service.require_editor_for_scan(
        session, scan_id=scan_id, user=user,
    )
    # S3 + DB cascade implemented in Task 3
    raise NotImplementedError("delete body not yet implemented")  # noqa: PIE790
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/api/test_scan_delete.py::test_delete_outsider_404 -v
```

Expected: PASS (the guard fires before the NotImplementedError).

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_delete.py
git commit -m "feat(api): add DELETE /scans/{scan_id} skeleton with editor guard"
```

---

## Task 3: DELETE — viewer returns 403 + body wires S3 + DB cascade

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (the `delete_scan` body)
- Modify: `tests/api/test_scan_delete.py` (add 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_scan_delete.py`:

```python
@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_viewer_403(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    viewer = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)
    pg_session.add(ProjectUser(
        project_id=project.id, user_id=viewer.id, project_role=ProjectRole.VIEWER,
    ))
    await pg_session.commit()

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(viewer))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "forbidden"
        assert body["error"]["details"]["action"] == "scan_edit"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_owner_succeeds_and_wipes_s3(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(owner))
    try:
        with mock_aws():
            _create_bucket()
            s3 = boto3.client("s3", region_name="us-east-2")
            s3.put_object(
                Bucket="qpress-uploads",
                Key=f"dev/scans/{scan.id}/images/a.png",
                Body=b"x",
            )
            s3.put_object(
                Bucket="qpress-uploads",
                Key=f"dev/scans/{scan.id}/images/b.png",
                Body=b"y",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")

            assert resp.status_code == 204

            remaining = s3.list_objects_v2(
                Bucket="qpress-uploads", Prefix=f"dev/scans/{scan.id}/"
            ).get("Contents")
            assert remaining is None or len(remaining) == 0

        # DB row gone
        from sqlalchemy import select
        from flake_analysis.db.models import Scan
        gone = (await pg_session.execute(
            select(Scan).where(Scan.id == scan.id)
        )).scalar_one_or_none()
        assert gone is None
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_idempotent_second_call_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(owner))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.delete(f"/api/v1/scans/{scan.id}")
                assert first.status_code == 204
                second = await client.delete(f"/api/v1/scans/{scan.id}")
                assert second.status_code == 404
                assert second.json()["error"]["code"] == "scan_not_found"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/test_scan_delete.py -v
```

Expected: 1 passed (outsider 404 from Task 2), 3 failed (the 3 new tests — `test_delete_viewer_403` should already pass actually since the guard is in place; verify carefully. `test_delete_owner_succeeds_and_wipes_s3` and `test_delete_idempotent_second_call_404` will fail with NotImplementedError or 500.)

If `test_delete_viewer_403` passes already (it should — guard is wired in Task 2), that's expected. The remaining two need the body.

- [ ] **Step 3: Replace the NotImplementedError body**

In `src/flake_analysis/api/routes/scans.py`, replace the `raise NotImplementedError(...)` line in `delete_scan` with the real body. Add `from flake_analysis.api.services import s3_cleanup` to the imports near the top of the file (next to the existing `s3_presign` import in the `flake_analysis.api.services` block):

```python
from flake_analysis.api.services import (
    projects_service as projects_svc,
    s3_cleanup,
    s3_presign,
    scans_service,
    upload_service,
)
```

Replace the body:

```python
    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        logger.error(
            "delete aborted: SAA_S3_BUCKET not configured",
            extra=_log_extra(scan_id=scan_id, user_id=str(user.id)),
        )
        raise app_errors.ConfigError()

    project_id = scan.project_id
    prefix = f"dev/scans/{scan_id}/"

    # Run boto3 in executor to avoid blocking the event loop on S3 latency.
    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(
        None, lambda: s3_cleanup.delete_prefix(bucket=bucket, prefix=prefix)
    )

    await session.delete(scan)
    await session.commit()

    logger.info(
        "scan deleted",
        extra=_log_extra(
            scan_id=scan_id, project_id=project_id, user_id=str(user.id),
            s3_deleted=deleted,
        ),
    )
```

Note: `app_errors.ConfigError()` already exists at `src/flake_analysis/api/errors.py:165` — verify with grep before commit. If absent, use `app_errors.S3BucketNotConfigured()` (the actual class name — confirm in `errors.py`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/test_scan_delete.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_delete.py
git commit -m "feat(api): wire DELETE /scans/{scan_id} body — S3 prefix wipe + DB cascade"
```

---

## Task 4: Backend regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Run full api test suite**

```bash
export SAA_TEST_DATABASE_URL="postgresql+asyncpg://saa_test:saa_test@localhost:5432/saa_test"
export SAA_S3_BUCKET="qpress-uploads"
uv run pytest tests/api/ -v
```

Expected: existing pre-existing failures only (the same 15-17 baseline failures W11 documented — flaky `test_admin_usage_*`, `test_scans_*` integration order-dependence, etc.). NO new failures from W12.

- [ ] **Step 2: Lint**

```bash
uv run ruff check src/flake_analysis/api/routes/scans.py \
                  src/flake_analysis/api/services/s3_cleanup.py \
                  tests/api/test_scan_delete.py \
                  tests/api/services/test_s3_cleanup.py
```

Expected: clean (0 issues).

- [ ] **Step 3: No commit** (this is a verification gate)

If anything fails outside the documented W11 baseline, STOP and report to PM.

---

## Task 5: Frontend deleteScan API client

**Files:**
- Modify: `web/src/api/upload.ts` (append after `listScansForProject`)
- Test: `web/src/api/__tests__/upload.test.ts` (check if exists; if not, create just the test for `deleteScan`)

- [ ] **Step 1: Write the failing test**

Check first:

```bash
ls web/src/api/__tests__/ 2>/dev/null
```

If `upload.test.ts` exists, append. Otherwise create:

```typescript
// web/src/api/__tests__/upload.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { deleteScan } from '@/api/upload'

describe('deleteScan', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('issues DELETE /api/v1/scans/{scan_id} with auth headers', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 204 })
    )

    await deleteScan(42)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/scans/42',
      expect.objectContaining({
        method: 'DELETE',
        credentials: 'include',
      })
    )
  })

  it('throws on non-204 response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: 'forbidden', details: { action: 'scan_edit' } } }),
        { status: 403, headers: { 'content-type': 'application/json' } }
      )
    )

    await expect(deleteScan(42)).rejects.toThrow(/forbidden|scan_edit|403/)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- src/api/__tests__/upload.test.ts
```

Expected: FAIL — `deleteScan` is not exported.

- [ ] **Step 3: Implement deleteScan**

In `web/src/api/upload.ts`, after `listScansForProject` (~line 160), append:

```typescript
export async function deleteScan(scanId: number): Promise<void> {
  const resp = await fetch(`/api/v1/scans/${scanId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeaders() },
    credentials: 'include',
  })
  if (resp.status === 204) return
  const body = await resp.json().catch(() => null)
  const code = body?.error?.code ?? `http_${resp.status}`
  const action = body?.error?.details?.action
  throw new Error(`deleteScan failed: ${code}${action ? ` (${action})` : ''}`)
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- src/api/__tests__/upload.test.ts
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/upload.ts web/src/api/__tests__/upload.test.ts
git commit -m "feat(web): add deleteScan API client"
```

---

## Task 6: ScanTable component skeleton — renders rows

**Files:**
- Create: `web/src/components/scans/ScanTable.tsx`
- Test: `web/src/components/scans/__tests__/ScanTable.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/src/components/scans/__tests__/ScanTable.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ScanTable } from '@/components/scans/ScanTable'
import * as uploadApi from '@/api/upload'

vi.mock('@/api/upload', async () => {
  const actual = await vi.importActual<typeof uploadApi>('@/api/upload')
  return { ...actual, listScansForProject: vi.fn(), deleteScan: vi.fn() }
})

function wrap(node: React.ReactNode, { pid = 'pid-1', sid = '' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/projects/${pid}/scans/${sid}/compute`]}>
        <Routes>
          <Route path="/projects/:projectId/scans/:scanId/:tab" element={node} />
          <Route path="/projects/:projectId" element={node} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('ScanTable', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 1, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
      {
        scan_id: 2, name: 'beta', material: 'MoS2',
        image_count: 50, uploaded_count: 30, status: 'draft',
        created_at: '2026-05-02T10:00:00Z',
      },
    ])
  })

  it('renders one row per scan with all 6 columns', async () => {
    wrap(<ScanTable />)
    expect(await screen.findByTestId('scan-table')).toBeInTheDocument()
    expect(screen.getByTestId('scan-table-row-1')).toBeInTheDocument()
    expect(screen.getByTestId('scan-table-row-2')).toBeInTheDocument()

    // Column headers
    expect(screen.getByTestId('scan-table-col-name')).toHaveTextContent(/name/i)
    expect(screen.getByTestId('scan-table-col-material')).toHaveTextContent(/material/i)
    expect(screen.getByTestId('scan-table-col-images')).toHaveTextContent(/images/i)
    expect(screen.getByTestId('scan-table-col-status')).toHaveTextContent(/status/i)
    expect(screen.getByTestId('scan-table-col-created')).toHaveTextContent(/created/i)
    expect(screen.getByTestId('scan-table-col-actions')).toHaveTextContent(/actions/i)

    // Row 1 cell content
    expect(screen.getByTestId('scan-table-cell-1-name')).toHaveTextContent('alpha')
    expect(screen.getByTestId('scan-table-cell-1-material')).toHaveTextContent('graphene')
    expect(screen.getByTestId('scan-table-cell-1-images')).toHaveTextContent('100/100')
    expect(screen.getByTestId('scan-table-cell-1-status')).toHaveTextContent('ready')
  })

  it('shows "No scans" empty state when list is empty', async () => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([])
    wrap(<ScanTable />)
    expect(await screen.findByTestId('scan-table-empty')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: FAIL — `ScanTable` not exported.

- [ ] **Step 3: Implement ScanTable (read-only, no sort/delete yet)**

Create `web/src/components/scans/ScanTable.tsx`:

```tsx
// web/src/components/scans/ScanTable.tsx
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listScansForProject, type ScanSummary } from '@/api/upload'
import { useProjectStore } from '@/state/projectSlice'
import { UploadModal } from '@/components/upload/UploadModal'

const TH: React.CSSProperties = {
  textAlign: 'left',
  fontSize: 12,
  color: '#374151',
  padding: '4px 8px',
  borderBottom: '1px solid #e5e7eb',
  cursor: 'pointer',
  userSelect: 'none',
}

const TD: React.CSSProperties = {
  fontSize: 12,
  padding: '4px 8px',
  borderBottom: '1px solid #f3f4f6',
}

export function ScanTable() {
  const navigate = useNavigate()
  const { projectId: urlPid, scanId: urlSid, tab } = useParams<{
    projectId: string
    scanId?: string
    tab?: string
  }>()
  const sliceProject = useProjectStore((s) => s.activeProjectId)
  const setActiveScan = useProjectStore((s) => s.setActiveScanId)
  const projectId = urlPid ?? sliceProject ?? null
  const [showUpload, setShowUpload] = useState(false)

  const scans = useQuery<ScanSummary[]>({
    queryKey: ['scans', 'list', projectId],
    queryFn: () => listScansForProject(projectId!),
    enabled: !!projectId,
    staleTime: 5_000,
  })

  if (!projectId) return null

  const tabSlug = tab ?? 'compute'
  const activeSid = urlSid ? Number(urlSid) : null

  if (scans.isLoading) {
    return <div data-testid="scan-table-loading" style={{ fontSize: 12, color: '#6b7280' }}>Loading scans…</div>
  }

  const rows = scans.data ?? []

  if (rows.length === 0) {
    return (
      <div data-testid="scan-table-empty" style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0' }}>
        <span style={{ fontSize: 12, color: '#6b7280' }}>No scans in this project yet.</span>
        <button
          data-testid="scan-table-empty-cta"
          type="button"
          onClick={() => setShowUpload(true)}
        >
          + New scan
        </button>
        <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
      </div>
    )
  }

  const onSelect = (sid: number) => {
    setActiveScan(sid)
    navigate(`/projects/${projectId}/scans/${sid}/${tabSlug}`)
  }

  return (
    <div data-testid="scan-table" style={{ padding: '6px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
        <button
          data-testid="scan-table-new"
          type="button"
          onClick={() => setShowUpload(true)}
        >
          + New scan
        </button>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th data-testid="scan-table-col-name" style={TH}>Name</th>
            <th data-testid="scan-table-col-material" style={TH}>Material</th>
            <th data-testid="scan-table-col-images" style={TH}>Images</th>
            <th data-testid="scan-table-col-status" style={TH}>Status</th>
            <th data-testid="scan-table-col-created" style={TH}>Created</th>
            <th data-testid="scan-table-col-actions" style={TH}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const isActive = s.scan_id === activeSid
            return (
              <tr
                key={s.scan_id}
                data-testid={`scan-table-row-${s.scan_id}`}
                style={{ background: isActive ? '#eef2ff' : undefined, cursor: 'pointer' }}
                onClick={() => onSelect(s.scan_id)}
              >
                <td data-testid={`scan-table-cell-${s.scan_id}-name`} style={TD}>{s.name}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-material`} style={TD}>{s.material}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-images`} style={TD}>
                  {s.uploaded_count}/{s.image_count}
                </td>
                <td data-testid={`scan-table-cell-${s.scan_id}-status`} style={TD}>{s.status}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-created`} style={TD}>
                  {new Date(s.created_at).toLocaleString()}
                </td>
                <td data-testid={`scan-table-cell-${s.scan_id}-actions`} style={TD}>
                  {/* delete button added in Task 8 */}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/scans/ScanTable.tsx web/src/components/scans/__tests__/ScanTable.test.tsx
git commit -m "feat(web): add ScanTable component with 6 columns"
```

---

## Task 7: ScanTable column sort

**Files:**
- Modify: `web/src/components/scans/ScanTable.tsx`
- Modify: `web/src/components/scans/__tests__/ScanTable.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `ScanTable.test.tsx`:

```tsx
import { fireEvent } from '@testing-library/react'

describe('ScanTable sort', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 10, name: 'charlie', material: 'WSe2',
        image_count: 200, uploaded_count: 200, status: 'ready',
        created_at: '2026-05-03T10:00:00Z',
      },
      {
        scan_id: 11, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
      {
        scan_id: 12, name: 'beta', material: 'MoS2',
        image_count: 50, uploaded_count: 30, status: 'draft',
        created_at: '2026-05-02T10:00:00Z',
      },
    ])
  })

  it('sorts by name ascending then descending on header click', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')

    // Default order = newest first (by created_at desc, server-provided)
    let rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows[0]).toHaveAttribute('data-testid', 'scan-table-row-10')

    fireEvent.click(screen.getByTestId('scan-table-col-name'))
    rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-11', // alpha
      'scan-table-row-12', // beta
      'scan-table-row-10', // charlie
    ])

    fireEvent.click(screen.getByTestId('scan-table-col-name'))
    rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-10', // charlie
      'scan-table-row-12', // beta
      'scan-table-row-11', // alpha
    ])
  })

  it('sorts by images uploaded count', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-col-images'))
    const rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-12', // 30
      'scan-table-row-11', // 100
      'scan-table-row-10', // 200
    ])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: 2 of the new 2 fail (rows are in server order — no sort yet).

- [ ] **Step 3: Add sort state and logic**

In `web/src/components/scans/ScanTable.tsx`, replace the function body. Add at the top of the component:

```tsx
type SortKey = 'name' | 'material' | 'images' | 'status' | 'created'
type SortDir = 'asc' | 'desc'

const [sortKey, setSortKey] = useState<SortKey | null>(null)
const [sortDir, setSortDir] = useState<SortDir>('asc')

const onSort = (key: SortKey) => {
  if (sortKey === key) {
    setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
  } else {
    setSortKey(key)
    setSortDir('asc')
  }
}

const sorted = (() => {
  if (!sortKey) return rows
  const cmp = (a: ScanSummary, b: ScanSummary): number => {
    switch (sortKey) {
      case 'name': return a.name.localeCompare(b.name)
      case 'material': return a.material.localeCompare(b.material)
      case 'images': return a.uploaded_count - b.uploaded_count
      case 'status': return a.status.localeCompare(b.status)
      case 'created': return Date.parse(a.created_at) - Date.parse(b.created_at)
    }
  }
  const out = [...rows].sort(cmp)
  return sortDir === 'asc' ? out : out.reverse()
})()
```

Replace `rows.map(...)` in the `<tbody>` with `sorted.map(...)`. Wire each header to `onSort`:

```tsx
<th data-testid="scan-table-col-name" style={TH} onClick={() => onSort('name')}>
  Name{sortKey === 'name' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
</th>
<th data-testid="scan-table-col-material" style={TH} onClick={() => onSort('material')}>
  Material{sortKey === 'material' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
</th>
<th data-testid="scan-table-col-images" style={TH} onClick={() => onSort('images')}>
  Images{sortKey === 'images' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
</th>
<th data-testid="scan-table-col-status" style={TH} onClick={() => onSort('status')}>
  Status{sortKey === 'status' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
</th>
<th data-testid="scan-table-col-created" style={TH} onClick={() => onSort('created')}>
  Created{sortKey === 'created' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
</th>
<th data-testid="scan-table-col-actions" style={TH}>Actions</th>
```

Note: The `cursor: 'pointer'` already on TR can fight with sort header clicks if a child element bubbles; `<th onClick>` is a separate element so this is fine.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: 4 passed (2 from Task 6 + 2 new from Task 7).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/scans/ScanTable.tsx web/src/components/scans/__tests__/ScanTable.test.tsx
git commit -m "feat(web): ScanTable column sort with asc/desc toggle"
```

---

## Task 8: ScanTable delete button + confirm dialog

**Files:**
- Modify: `web/src/components/scans/ScanTable.tsx`
- Modify: `web/src/components/scans/__tests__/ScanTable.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `ScanTable.test.tsx`:

```tsx
import { waitFor } from '@testing-library/react'

describe('ScanTable delete', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 1, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
    ])
    vi.mocked(uploadApi.deleteScan).mockResolvedValue(undefined)
  })

  it('shows delete button per row, opens confirm, calls deleteScan, refetches', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')

    const delBtn = screen.getByTestId('scan-table-delete-1')
    expect(delBtn).toBeInTheDocument()

    fireEvent.click(delBtn)

    // Confirm dialog appears
    const confirm = await screen.findByTestId('scan-table-confirm-1')
    expect(confirm).toHaveTextContent(/alpha/)

    fireEvent.click(screen.getByTestId('scan-table-confirm-yes-1'))

    await waitFor(() => {
      expect(uploadApi.deleteScan).toHaveBeenCalledWith(1)
    })
  })

  it('cancel keeps scan and closes dialog', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-delete-1'))
    await screen.findByTestId('scan-table-confirm-1')
    fireEvent.click(screen.getByTestId('scan-table-confirm-no-1'))

    await waitFor(() => {
      expect(screen.queryByTestId('scan-table-confirm-1')).not.toBeInTheDocument()
    })
    expect(uploadApi.deleteScan).not.toHaveBeenCalled()
  })

  it('shows error toast and keeps row on delete failure', async () => {
    vi.mocked(uploadApi.deleteScan).mockRejectedValue(new Error('deleteScan failed: forbidden (scan_edit)'))
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-delete-1'))
    await screen.findByTestId('scan-table-confirm-1')
    fireEvent.click(screen.getByTestId('scan-table-confirm-yes-1'))

    await waitFor(() => {
      expect(uploadApi.deleteScan).toHaveBeenCalledWith(1)
    })
    // Row still rendered after failure
    expect(screen.getByTestId('scan-table-row-1')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: 3 of the new 3 fail (no delete button yet).

- [ ] **Step 3: Wire delete + confirm**

In `web/src/components/scans/ScanTable.tsx`:

1. Add imports near the top:
```tsx
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listScansForProject, deleteScan, type ScanSummary } from '@/api/upload'
```

2. Inside the component, add:
```tsx
const qc = useQueryClient()
const [confirmId, setConfirmId] = useState<number | null>(null)

const del = useMutation({
  mutationFn: (sid: number) => deleteScan(sid),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ['scans', 'list', projectId] })
    toast.success('Scan deleted')
    setConfirmId(null)
  },
  onError: (e: Error) => {
    toast.error(e.message ?? 'Delete failed')
    setConfirmId(null)
  },
})
```

3. In each row's actions cell, replace the empty `<td>` placeholder with:

```tsx
<td data-testid={`scan-table-cell-${s.scan_id}-actions`} style={TD}>
  <button
    data-testid={`scan-table-delete-${s.scan_id}`}
    type="button"
    onClick={(ev) => {
      ev.stopPropagation()
      setConfirmId(s.scan_id)
    }}
    style={{ fontSize: 11, color: '#b91c1c' }}
  >
    Delete
  </button>
  {confirmId === s.scan_id && (
    <div
      data-testid={`scan-table-confirm-${s.scan_id}`}
      onClick={(ev) => ev.stopPropagation()}
      style={{
        position: 'absolute',
        marginTop: 4,
        padding: 8,
        background: '#fff',
        border: '1px solid #b91c1c',
        borderRadius: 4,
        zIndex: 10,
        fontSize: 12,
      }}
    >
      Delete scan "{s.name}"? This wipes its DB row and all S3 objects.
      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
        <button
          data-testid={`scan-table-confirm-yes-${s.scan_id}`}
          type="button"
          disabled={del.isPending}
          onClick={() => del.mutate(s.scan_id)}
          style={{ color: '#b91c1c' }}
        >
          {del.isPending ? 'Deleting…' : 'Yes, delete'}
        </button>
        <button
          data-testid={`scan-table-confirm-no-${s.scan_id}`}
          type="button"
          onClick={() => setConfirmId(null)}
        >
          Cancel
        </button>
      </div>
    </div>
  )}
</td>
```

The `ev.stopPropagation()` on the delete button + confirm panel is critical — without it, clicking either bubbles to the row's `onClick={() => onSelect(s.scan_id)}` and navigates.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npm test -- src/components/scans/__tests__/ScanTable.test.tsx
```

Expected: 7 passed (2+2+3).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/scans/ScanTable.tsx web/src/components/scans/__tests__/ScanTable.test.tsx
git commit -m "feat(web): ScanTable inline delete with confirm dialog"
```

---

## Task 9: Swap ScanPicker → ScanTable at the export point

**Files:**
- Modify: `web/src/components/scans/ScanPicker.tsx`
- Modify: `web/src/components/scans/__tests__/ScanPicker.test.tsx`

Goal: keep all four tab pages (Compute / Selector / Clustering / Explorer) untouched. They import `ScanPicker` — we re-export `ScanTable` under that name to swap behavior atomically.

- [ ] **Step 1: Replace ScanPicker.tsx body**

Replace the entire file with:

```tsx
// web/src/components/scans/ScanPicker.tsx
//
// Backwards-compat shim. The dropdown was replaced by a sortable table in
// W12 (docs/superpowers/plans/2026-05-26-W12-scan-table-and-delete.md).
// Keeping the export name avoids touching every tab page that imports it.
export { ScanTable as ScanPicker } from './ScanTable'
```

- [ ] **Step 2: Update the existing ScanPicker test to a smoke test**

Replace `web/src/components/scans/__tests__/ScanPicker.test.tsx` body with:

```tsx
import { describe, it, expect } from 'vitest'
import { ScanPicker } from '@/components/scans/ScanPicker'
import { ScanTable } from '@/components/scans/ScanTable'

describe('ScanPicker (compat shim)', () => {
  it('re-exports ScanTable under the legacy name', () => {
    expect(ScanPicker).toBe(ScanTable)
  })
})
```

- [ ] **Step 3: Run all related tests**

```bash
cd web && npm test -- src/components/scans/
```

Expected: ScanTable tests (7) pass + ScanPicker shim test (1) passes = 8 total.

- [ ] **Step 4: Run the full vitest suite**

```bash
cd web && npm test
```

Expected: existing baseline (345 from W11/Hotfix #10) + new tests. No regressions in tab pages (Compute/Selector/Clustering/Explorer) — they get the table instead of the dropdown but everything still mounts.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/scans/ScanPicker.tsx web/src/components/scans/__tests__/ScanPicker.test.tsx
git commit -m "refactor(web): ScanPicker re-exports ScanTable for transparent swap"
```

---

## Task 10: Frontend typecheck + build

**Files:** none (verification gate)

- [ ] **Step 1: Typecheck**

```bash
cd web && npx tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 2: Build**

```bash
cd web && npm run build
```

Expected: build succeeds.

- [ ] **Step 3: No commit**

If either fails, fix in a follow-up commit on the same task.

---

## Task 11: Manual smoke test (PM-driven)

**Files:** none

- [ ] **Step 1: Start backend + frontend**

```bash
./scripts/dev/start-backend.sh    # in one terminal
./scripts/dev/start-frontend.sh   # in another
```

- [ ] **Step 2: Open http://localhost:5173/projects/<test-pid>/scans/<sid>/compute**

Verify:
1. Table renders with 6 columns: Name / Material / Images / Status / Created / Actions.
2. Clicking a column header sorts asc, clicking again sorts desc, indicator (▲/▼) appears.
3. Clicking a row navigates to that scan's compute tab.
4. Clicking "Delete" on a row opens the confirm dialog with the scan name.
5. "Cancel" closes the dialog without deleting.
6. "Yes, delete" calls the API; on success the row disappears (refetch) and a toast shows.
7. Trying to delete as a non-editor (manually craft via dev-bypass project ACL) shows an error toast.

- [ ] **Step 2: No commit** (this is owner verification)

If a step fails, file a follow-up.

---

## Task 12: Update project-status.md

**Files:**
- Modify: `docs/project-status.md`

PM-direct task per CLAUDE.md §8.1. Not delegated.

- [ ] **Step 1: Update §3.1 "다음 한 발" to remove W12 from todo list and reflect completion**

- [ ] **Step 2: Append to §7 변경 로그:**

```markdown
- 2026-05-26 — **W12 ScanTable + Delete** 완료. 12-task subagent-driven TDD plan ([`plan`](superpowers/plans/2026-05-26-W12-scan-table-and-delete.md)). Backend: `s3_cleanup.delete_prefix` 헬퍼 (paginated batch delete, 1000-key chunks) + `DELETE /scans/{scan_id}` 라우트 (`require_editor_for_scan` 가드 → S3 prefix `dev/scans/{scan_id}/` 와이프 (executor) → ORM cascade로 images/upload_sessions/upload_items/analyses 자동 삭제). usage_events는 JSONB scan_id만 가지고 FK 없음 — 이력 보존, 손대지 않음. Frontend: `ScanPicker` 드롭다운 → `ScanTable` (6 columns name/material/images/status/created/actions, 컬럼 헤더 클릭으로 asc↔desc 정렬, 인라인 delete 버튼 + confirm 다이얼로그 + TanStack Query invalidation). `ScanPicker.tsx`는 호환 shim으로 `ScanTable`을 re-export — 4개 탭 페이지(Compute/Selector/Clustering/Explorer) 무변경.
```

- [ ] **Step 3: Commit**

```bash
git add docs/project-status.md
git commit -m "docs(status): W12 ScanTable + Delete complete"
```

---

## Self-Review

**1. Spec coverage:**
- Owner request: "Scan 고르는게 정보가 너무 없는상태에서 고르는것 같다 → 테이블로 만들어서 메타데이터 보여주고 컬럼별로 정렬할수있게" → Tasks 6+7 ✅
- Owner request: "delete 할수있게" → Tasks 1+2+3 (backend) + Task 8 (UI) ✅
- Placement = 각 탭 상단 (현재 Picker 위치) → Task 9 (re-export shim) ✅
- Columns = name/material/images/status/created/actions → Task 6 ✅
- S3 cleanup = DB + S3 모두 → Task 1 (helper) + Task 3 (route wires it) ✅
- Permission = EDITOR (W11과 일관) → Task 2 (skeleton uses `require_editor_for_scan`) ✅

**2. Placeholder scan:** No "TBD"/"TODO"/"appropriate"/"similar to Task N" patterns. Each task has full code.

**3. Type consistency:**
- `ScanSummary` (api/upload.ts:139): `scan_id`, `name`, `material`, `image_count`, `uploaded_count`, `status`, `created_at` — all match.
- `deleteScan(scanId: number): Promise<void>` defined in Task 5, called from Task 8.
- `s3_cleanup.delete_prefix(*, bucket, prefix)` defined in Task 1, called in Task 3.
- `require_editor_for_scan` (existing W11 helper) — same signature in Task 2 + Task 3.

**4. One known unknown:** Task 3 references `app_errors.ConfigError()`. The actual class name in `errors.py:165` may differ. The plan tells the implementer to grep and adjust. This is the only verification step left to the implementer; everything else is fully specified.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-W12-scan-table-and-delete.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec then quality), fast iteration.
2. **Inline Execution** — execute in this session with batch checkpoints.
