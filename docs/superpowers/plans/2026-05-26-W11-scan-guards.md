# W11 — Scan Access Guards & New-Scan-Only Upload Policy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the E5 gap surfaced during Phase E manual verification — scan-level routes (`presign_image_put`, `complete_image`, `finalize_scan`, `get_scan`) currently only check `ScanNotFound` and never verify that the caller has access to the scan's parent project. After this plan, calling any of those routes for a scan in a project the caller cannot reach returns a uniform 404 (no information leak about scan existence). Also lock in the policy that `UploadModal` is new-scan-only — there is no resume-into-draft-scan UX in scope.

**Architecture:**
- New service helper `flake_analysis.api.services.scans_service.get_scan_for_user(session, *, scan_id, user) -> Scan` that loads the scan, resolves the caller's effective `ProjectRole` against the scan's parent project via the existing `acl.resolve_effective_project_role`, and raises `app_errors.ScanNotFound(scan_id)` (404) when the caller has no access. Read-only call sites use the same helper; write call sites add an editor-required check on top.
- Wire the helper into all four scan-level routes. `create_scan` already short-circuits on `ProjectNotFound`, but it must also enforce editor access on the parent project (using a parallel `get_project_for_user` helper). Tests cover both 403 (insufficient role) and 404 (no access) cases.
- Codify the new-scan-only policy as a docstring on `UploadModal` and a one-line note in `docs/project-status.md`. No code change to the modal — it is already structurally new-scan-only; the goal is to prevent accidental re-introduction of resume UX.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async ORM, pytest-asyncio, existing `flake_analysis.api.services.acl.resolve_effective_project_role`, existing `app_errors` envelope.

---

## Background — Confirmed E5 Gap

Audit of `src/flake_analysis/api/routes/scans.py` (this plan's reference snapshot):

| Route | Path | Project membership check today |
|---|---|---|
| `create_scan` | `POST /projects/{project_id}/scans` | `projects_svc.get_project(project_id)` → `ProjectNotFound` only. Does NOT verify caller has editor access to that project. |
| `presign_image_put` | `POST /scans/{scan_id}/images/presign` | None. Checks `ScanNotFound` only. |
| `complete_image` | `POST /scans/{scan_id}/images/{upload_item_id}/complete` | None at scan level. Checks `item.session.scan_id == scan_id` (cross-scan upload_item misuse) but not caller↔project. |
| `finalize_scan` | `POST /scans/{scan_id}/finalize` | None. Checks `ScanNotFound` only. |
| `get_scan` | `GET /scans/{scan_id}` | None. Checks `ScanNotFound` only. |
| `list_scans` | `GET /projects/{project_id}/scans` | None at project level. Lists all scans under the project regardless of caller. |

The `acl.resolve_effective_project_role(global_role, is_owner=…, acl_role=…)` pure function exists and is exercised elsewhere; this plan threads it through the scan routes. `projects_service.list_projects_for_user(user_id)` already filters by `Project.owner_id == user_id` (v1 ACL) — the same logic must be reused here so v1 and v2 (ACL row union) graduate together.

**404 over 403 by default.** When the caller has no relationship to the project, return `ScanNotFound` (404) so we don't leak scan existence to outsiders. Return `Forbidden` (403) only when the caller IS in the project but lacks the role for the operation (e.g., viewer trying to presign).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/flake_analysis/api/services/scans_service.py` | Create | New helpers: `get_scan_for_user(...)` and `require_editor_for_scan(...)`. Both return the `Scan` after access check; the editor variant raises `Forbidden` for viewer-only callers. |
| `src/flake_analysis/api/services/projects_service.py` | Modify | Add `get_project_for_user(session, *, project_id, user, require_editor=False) -> Project`. Reuses `resolve_effective_project_role`. Returns `ProjectNotFound` (404) when no access; `Forbidden` (403) when access is viewer but editor was required. |
| `src/flake_analysis/api/errors.py` | Modify (only if missing) | Ensure `Forbidden(action: str, …)` exists with a stable `error.code = "forbidden"` and 403 status. If already present, no change. |
| `src/flake_analysis/api/routes/scans.py` | Modify | Replace direct `select(Scan).where(Scan.id == scan_id)` lookups in the 4 affected routes with `scans_service.get_scan_for_user(...)` (or `require_editor_for_scan(...)` for write ops). `create_scan` swaps `projects_svc.get_project(...)` for `projects_svc.get_project_for_user(..., require_editor=True)`. `list_scans` switches to `get_project_for_user(..., require_editor=False)`. |
| `tests/api/test_scan_access_guards.py` | Create | Six dedicated tests (one happy path + one denial test per route × 4 routes, plus a list-scans isolation test, plus a viewer-vs-editor 403 test). Reuses `tests/api/conftest.py` fixtures. |
| `web/src/components/upload/UploadModal.tsx` | Modify | Add a top-of-file docstring stating `UploadModal is new-scan-only by design — see docs/superpowers/plans/2026-05-26-W11-scan-guards.md §"Policy: New-Scan-Only Upload"`. No behavioral change. |
| `docs/project-status.md` | Modify | Append: (a) Phase E DONE marker, (b) W11 plan registered, (c) new-scan-only policy line. |

---

## Policy: New-Scan-Only Upload

Locking this in so it doesn't get rediscovered as a "missing feature":

- `UploadModal` creates a fresh scan on every open. There is no UX to resume into a draft scan from a previous session. If a user closes the modal mid-upload via "Stop & Close", the server-side `draft` scan is preserved (visible in the picker as `name (uploaded/expected · draft)`), but reopening the modal starts a NEW scan — it does not adopt the draft.
- Rationale: resume UX requires reconciling client-side dropped-file state (which is throwaway and not persisted) with server-side `upload_items` state. The cost of getting that right is high, and the value is low — operators can simply create a new scan with the missing files. This was confirmed during Phase E2 manual verification.
- Out of scope for this plan and for the current milestone. If we ever want resume UX, it gets its own brainstorm + plan.

---

## Task 1 — `Forbidden` error envelope (only if missing)

**Files:**
- Modify: `src/flake_analysis/api/errors.py`
- Test: `tests/api/test_errors.py`

- [ ] **Step 1: Audit `errors.py` for existing `Forbidden`**

Run: `grep -n 'Forbidden\|forbidden\|status_code=403' src/flake_analysis/api/errors.py`
If a `Forbidden` class is already defined with `status_code=403` and `error.code = "forbidden"`, **skip the rest of Task 1**. Otherwise proceed.

- [ ] **Step 2: Write the failing test**

Add to `tests/api/test_errors.py`:

```python
def test_forbidden_error_envelope_shape():
    from flake_analysis.api.errors import Forbidden

    err = Forbidden(action="finalize", scan_id=42)
    payload = err.to_envelope(request_id="req-test")
    assert payload["error"]["code"] == "forbidden"
    assert err.status_code == 403
    assert payload["error"]["details"] == {"action": "finalize", "scan_id": 42}
```

- [ ] **Step 3: Run test, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_errors.py::test_forbidden_error_envelope_shape -v`
Expected: FAIL (`Forbidden` import fails or attributes missing).

- [ ] **Step 4: Implement `Forbidden`**

In `src/flake_analysis/api/errors.py`, mirroring the existing `ScanNotFound` / `ProjectNotFound` pattern:

```python
class Forbidden(AppError):
    """Caller authenticated but lacks the role required for this action."""
    status_code = 403
    code = "forbidden"

    def __init__(self, *, action: str, **details):
        super().__init__(message=f"Forbidden: {action}", details={"action": action, **details})
```

- [ ] **Step 5: Run test, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_errors.py::test_forbidden_error_envelope_shape -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/errors.py tests/api/test_errors.py
git commit -m "feat(api): add Forbidden error envelope (403)"
```

---

## Task 2 — `projects_service.get_project_for_user`

**Files:**
- Modify: `src/flake_analysis/api/services/projects_service.py`
- Test: `tests/api/test_projects_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_projects_service.py`:

```python
import pytest
from flake_analysis.api import errors as app_errors
from flake_analysis.api.services import projects_service as svc
from flake_analysis.db.models import UserRole


@pytest.mark.asyncio
async def test_get_project_for_user_owner_ok(db_session, project_factory, user_factory):
    owner = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    got = await svc.get_project_for_user(db_session, project_id=project.id, user=owner)
    assert got.id == project.id


@pytest.mark.asyncio
async def test_get_project_for_user_outsider_404(db_session, project_factory, user_factory):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    with pytest.raises(app_errors.ProjectNotFound):
        await svc.get_project_for_user(db_session, project_id=project.id, user=outsider)


@pytest.mark.asyncio
async def test_get_project_for_user_viewer_403_when_editor_required(
    db_session, project_factory, user_factory, project_acl_factory,
):
    owner = await user_factory(role=UserRole.MEMBER)
    reader = await user_factory(role=UserRole.READER)
    project = await project_factory(owner=owner)
    # READER global role → viewer baseline, no ACL upgrade
    with pytest.raises(app_errors.Forbidden):
        await svc.get_project_for_user(
            db_session, project_id=project.id, user=reader, require_editor=True,
        )


@pytest.mark.asyncio
async def test_get_project_for_user_admin_always_editor(
    db_session, project_factory, user_factory,
):
    owner = await user_factory(role=UserRole.MEMBER)
    admin = await user_factory(role=UserRole.ADMIN)
    project = await project_factory(owner=owner)
    got = await svc.get_project_for_user(
        db_session, project_id=project.id, user=admin, require_editor=True,
    )
    assert got.id == project.id
```

If `project_acl_factory` doesn't exist yet in `tests/api/conftest.py`, define it inline in this test file as a fallback fixture that inserts a `project_users` row.

- [ ] **Step 2: Run tests, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_projects_service.py -v -k get_project_for_user`
Expected: FAIL on import (`get_project_for_user` not defined).

- [ ] **Step 3: Implement `get_project_for_user`**

Add to `src/flake_analysis/api/services/projects_service.py` (after the existing `get_project`):

```python
from sqlalchemy import select
from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User
from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import Project, ProjectRole, ProjectUser


async def get_project_for_user(
    session: AsyncSession,
    *,
    project_id: str,
    user: User,
    require_editor: bool = False,
) -> Project:
    """Return project iff caller has access; raise ProjectNotFound (404) otherwise.

    Returns 403 Forbidden when the caller has access but lacks editor role and
    require_editor=True. This avoids leaking the existence of projects the
    caller can't see.
    """
    project = (await session.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise app_errors.ProjectNotFound(project_id=project_id)

    is_owner = project.owner_id == user.id
    acl_role = (await session.execute(
        select(ProjectUser.project_role)
        .where(ProjectUser.project_id == project_id)
        .where(ProjectUser.user_id == user.id)
    )).scalar_one_or_none()

    effective = resolve_effective_project_role(
        user.global_role, is_owner=is_owner, acl_role=acl_role,
    )
    if effective is None:
        # No relationship to project — 404, not 403, to avoid existence leak.
        raise app_errors.ProjectNotFound(project_id=project_id)
    if require_editor and effective != ProjectRole.EDITOR:
        raise app_errors.Forbidden(
            action="project_edit", project_id=project_id,
        )
    return project
```

Adjust attribute names (`user.global_role`, `ProjectUser.project_role`, `Project.owner_id`) to match the actual model — verify with `grep -n` before writing.

- [ ] **Step 4: Run tests, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_projects_service.py -v -k get_project_for_user`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/projects_service.py tests/api/test_projects_service.py
git commit -m "feat(api): add get_project_for_user with editor gating"
```

---

## Task 3 — `scans_service.get_scan_for_user` + `require_editor_for_scan`

**Files:**
- Create: `src/flake_analysis/api/services/scans_service.py`
- Test: `tests/api/test_scans_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_scans_service.py`:

```python
import pytest
from flake_analysis.api import errors as app_errors
from flake_analysis.api.services import scans_service as svc
from flake_analysis.db.models import UserRole


@pytest.mark.asyncio
async def test_get_scan_for_user_owner_ok(db_session, project_factory, scan_factory, user_factory):
    owner = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    got = await svc.get_scan_for_user(db_session, scan_id=scan.id, user=owner)
    assert got.id == scan.id


@pytest.mark.asyncio
async def test_get_scan_for_user_outsider_404(db_session, project_factory, scan_factory, user_factory):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    with pytest.raises(app_errors.ScanNotFound):
        await svc.get_scan_for_user(db_session, scan_id=scan.id, user=outsider)


@pytest.mark.asyncio
async def test_get_scan_for_user_unknown_scan_404(db_session, user_factory):
    user = await user_factory(role=UserRole.MEMBER)
    with pytest.raises(app_errors.ScanNotFound):
        await svc.get_scan_for_user(db_session, scan_id=999_999_999, user=user)


@pytest.mark.asyncio
async def test_require_editor_for_scan_viewer_403(
    db_session, project_factory, scan_factory, user_factory,
):
    owner = await user_factory(role=UserRole.MEMBER)
    reader = await user_factory(role=UserRole.READER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    # READER global → viewer baseline; no ACL upgrade
    with pytest.raises(app_errors.Forbidden):
        await svc.require_editor_for_scan(db_session, scan_id=scan.id, user=reader)
```

- [ ] **Step 2: Run tests, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_scans_service.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the service**

Create `src/flake_analysis/api/services/scans_service.py`:

```python
"""Scan-level access guards.

These helpers translate the existing project-level ACL into scan-level
checks: load the scan, look up its parent project, resolve the caller's
effective ProjectRole, and raise ScanNotFound (404) for outsiders or
Forbidden (403) for viewers attempting writes.

Why ScanNotFound and not Forbidden for outsiders: returning 403 would leak
the existence of scans in projects the caller has no business knowing
about. 403 is reserved for "you ARE in the project, but your role is too
low for this action".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User
from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import ProjectRole, ProjectUser, Scan


async def _resolve_effective(
    session: AsyncSession, *, scan_id: int, user: User,
) -> tuple[Scan, ProjectRole | None]:
    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)

    is_owner = scan.project.owner_id == user.id  # ensure relationship loaded
    # Re-fetch project explicitly if .project lazy-loads under async
    from flake_analysis.db.models import Project
    project = (await session.execute(
        select(Project).where(Project.id == scan.project_id)
    )).scalar_one()
    is_owner = project.owner_id == user.id

    acl_role = (await session.execute(
        select(ProjectUser.project_role)
        .where(ProjectUser.project_id == scan.project_id)
        .where(ProjectUser.user_id == user.id)
    )).scalar_one_or_none()

    effective = resolve_effective_project_role(
        user.global_role, is_owner=is_owner, acl_role=acl_role,
    )
    return scan, effective


async def get_scan_for_user(
    session: AsyncSession, *, scan_id: int, user: User,
) -> Scan:
    """Return scan iff caller has any access to the parent project; else 404."""
    scan, effective = await _resolve_effective(session, scan_id=scan_id, user=user)
    if effective is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)
    return scan


async def require_editor_for_scan(
    session: AsyncSession, *, scan_id: int, user: User,
) -> Scan:
    """Like get_scan_for_user but additionally requires editor role; else 403."""
    scan, effective = await _resolve_effective(session, scan_id=scan_id, user=user)
    if effective is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)
    if effective != ProjectRole.EDITOR:
        raise app_errors.Forbidden(action="scan_edit", scan_id=scan_id)
    return scan
```

- [ ] **Step 4: Run tests, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_scans_service.py -v`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/scans_service.py tests/api/test_scans_service.py
git commit -m "feat(api): add scan-level access guards"
```

---

## Task 4 — Wire guards into `presign_image_put`

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (the `presign_image_put` handler)
- Test: `tests/api/test_scan_access_guards.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_scan_access_guards.py`:

```python
"""W11 — verify scan-level routes refuse callers outside the parent project."""
import pytest
from flake_analysis.db.models import UserRole


@pytest.mark.asyncio
async def test_presign_outsider_404(api_client, project_factory, scan_factory, user_factory, auth_header):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    body = {
        "filename": "ix000_iy000.png",
        "size": 1024,
        "sha256": "a" * 64,
        "content_type": "image/png",
        "ix": 0,
        "iy": 0,
    }
    r = await api_client.post(
        f"/api/v1/scans/{scan.id}/images/presign",
        json=body,
        headers=auth_header(outsider),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "scan_not_found"
```

The exact fixture names (`api_client`, `auth_header`) must match `tests/api/conftest.py`. If they differ, adapt — do not invent.

- [ ] **Step 2: Run test, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_presign_outsider_404 -v`
Expected: FAIL with status 200 or 201 (presign currently succeeds for outsiders).

- [ ] **Step 3: Wire the guard**

In `src/flake_analysis/api/routes/scans.py`, in the `presign_image_put` handler (around line 149), replace the direct `Scan` lookup with:

```python
from flake_analysis.api.services import scans_service

# ...inside the handler, replacing the existing select(Scan) lookup:
try:
    scan = await scans_service.require_editor_for_scan(
        session, scan_id=scan_id, user=user,
    )
except app_errors.ScanNotFound:
    logger.info(
        "presign aborted: scan not found or no access",
        extra=_log_extra(event="presign_scan_not_found", scan_id=scan_id),
    )
    raise
```

The existing post-lookup logic (status checks, size validation, etc.) is unchanged.

- [ ] **Step 4: Run test, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_presign_outsider_404 -v`
Expected: PASS.

- [ ] **Step 5: Run full presign test suite for regression**

Run via api-developer agent: `uv run pytest tests/api/test_scans_presign.py -v`
Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_access_guards.py
git commit -m "feat(api): guard presign against outsiders"
```

---

## Task 5 — Wire guards into `complete_image`

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (`complete_image`)
- Test: `tests/api/test_scan_access_guards.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_scan_access_guards.py`:

```python
@pytest.mark.asyncio
async def test_complete_outsider_404(
    api_client, project_factory, scan_factory, upload_item_factory,
    user_factory, auth_header,
):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    item = await upload_item_factory(scan=scan)
    r = await api_client.post(
        f"/api/v1/scans/{scan.id}/images/{item.id}/complete",
        json={"etag": "fake-etag"},
        headers=auth_header(outsider),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "scan_not_found"
```

If `upload_item_factory` doesn't exist, build it inline minimally — insert one `upload_session` + one `upload_item` with status `PENDING`.

- [ ] **Step 2: Run test, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_complete_outsider_404 -v`
Expected: FAIL (currently returns 4xx for a different reason or 200).

- [ ] **Step 3: Wire the guard**

In `complete_image` (around line 389), add the access check **before** the existing `select(UploadItem)` lookup:

```python
await scans_service.require_editor_for_scan(
    session, scan_id=scan_id, user=user,
)
# existing logic continues unchanged
```

- [ ] **Step 4: Run test, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_complete_outsider_404 -v`
Expected: PASS.

- [ ] **Step 5: Regression check**

Run via api-developer agent: `uv run pytest tests/api/ -v -k complete`
Expected: all existing complete-image tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_access_guards.py
git commit -m "feat(api): guard complete_image against outsiders"
```

---

## Task 6 — Wire guards into `finalize_scan`

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (`finalize_scan`)
- Test: `tests/api/test_scan_access_guards.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_scan_access_guards.py`:

```python
@pytest.mark.asyncio
async def test_finalize_outsider_404(
    api_client, project_factory, scan_factory, user_factory, auth_header,
):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    r = await api_client.post(
        f"/api/v1/scans/{scan.id}/finalize",
        headers=auth_header(outsider),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "scan_not_found"
```

- [ ] **Step 2: Run test, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_finalize_outsider_404 -v`
Expected: FAIL.

- [ ] **Step 3: Wire the guard**

In `finalize_scan` (around line 551), replace the direct `select(Scan)` with:

```python
scan = await scans_service.require_editor_for_scan(
    session, scan_id=scan_id, user=user,
)
```

Drop the now-redundant `if scan is None` block — `require_editor_for_scan` raises `ScanNotFound` itself.

- [ ] **Step 4: Run test, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py::test_finalize_outsider_404 -v`
Expected: PASS.

- [ ] **Step 5: Regression check**

Run via api-developer agent: `uv run pytest tests/api/test_scans_finalize.py -v`
Expected: all existing finalize tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_access_guards.py
git commit -m "feat(api): guard finalize_scan against outsiders"
```

---

## Task 7 — Wire guards into `get_scan` and `list_scans`

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (`get_scan`, `list_scans`)
- Test: `tests/api/test_scan_access_guards.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_scan_access_guards.py`:

```python
@pytest.mark.asyncio
async def test_get_scan_outsider_404(
    api_client, project_factory, scan_factory, user_factory, auth_header,
):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    scan = await scan_factory(project=project)
    r = await api_client.get(
        f"/api/v1/scans/{scan.id}",
        headers=auth_header(outsider),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "scan_not_found"


@pytest.mark.asyncio
async def test_list_scans_outsider_404(
    api_client, project_factory, scan_factory, user_factory, auth_header,
):
    owner = await user_factory(role=UserRole.MEMBER)
    outsider = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    await scan_factory(project=project)
    r = await api_client.get(
        f"/api/v1/projects/{project.id}/scans",
        headers=auth_header(outsider),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"


@pytest.mark.asyncio
async def test_viewer_can_get_scan_but_not_finalize(
    api_client, project_factory, scan_factory, user_factory, project_acl_factory, auth_header,
):
    owner = await user_factory(role=UserRole.MEMBER)
    viewer = await user_factory(role=UserRole.MEMBER)
    project = await project_factory(owner=owner)
    await project_acl_factory(project=project, user=viewer, role="viewer")
    scan = await scan_factory(project=project)
    # GET succeeds
    ok = await api_client.get(
        f"/api/v1/scans/{scan.id}",
        headers=auth_header(viewer),
    )
    assert ok.status_code == 200
    # finalize is forbidden
    forbid = await api_client.post(
        f"/api/v1/scans/{scan.id}/finalize",
        headers=auth_header(viewer),
    )
    assert forbid.status_code == 403
    assert forbid.json()["error"]["code"] == "forbidden"
```

- [ ] **Step 2: Run tests, expect failure**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py -v -k 'get_scan_outsider or list_scans_outsider or viewer_can_get'`
Expected: FAIL on all three.

- [ ] **Step 3: Wire guards**

In `get_scan` (around line 599), replace the `select(Scan)` lookup with `scans_service.get_scan_for_user(...)` (read-only, no editor required).

In `list_scans` (around line 100), insert at the top of the body:

```python
await projects_svc.get_project_for_user(
    session, project_id=project_id, user=user, require_editor=False,
)
```

`create_scan` (around line 67): swap `projects_svc.get_project(...)` for `projects_svc.get_project_for_user(..., require_editor=True)`. The existing `ProjectNotFound` log line still fires — `get_project_for_user` raises the same exception type.

- [ ] **Step 4: Run tests, expect pass**

Run via api-developer agent: `uv run pytest tests/api/test_scan_access_guards.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Full regression**

Run via api-developer agent: `uv run pytest tests/api/ -v`
Expected: all PASS. If any pre-existing test fails because it lacks proper project membership for the test user, fix the fixture (don't weaken the guard).

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scan_access_guards.py
git commit -m "feat(api): guard get_scan/list_scans/create_scan against outsiders"
```

---

## Task 8 — UploadModal new-scan-only docstring

**Files:**
- Modify: `web/src/components/upload/UploadModal.tsx`

- [ ] **Step 1: Add the policy docstring**

Replace the top-of-file comment in `UploadModal.tsx` so the first lines read:

```tsx
// web/src/components/upload/UploadModal.tsx
//
// UploadModal is new-scan-only by design. Each open creates a fresh scan
// (lazily on Start). If the user closes mid-upload, the server-side draft
// scan survives and is visible in the picker, but reopening this modal
// starts a NEW scan — there is no resume-into-draft UX.
// See docs/superpowers/plans/2026-05-26-W11-scan-guards.md
```

No behavioral change. The goal is to make the policy obvious to anyone tempted to add a "Resume previous upload" button.

- [ ] **Step 2: Verify vitest still green**

Run via frontend-architect agent: `npm run -w web test -- --run`
Expected: 343/343 PASS (or whatever the current baseline is — no regression).

- [ ] **Step 3: Commit**

```bash
git add web/src/components/upload/UploadModal.tsx
git commit -m "docs(web): codify new-scan-only policy on UploadModal"
```

---

## Task 9 — Update project-status

**Files:**
- Modify: `docs/project-status.md`

- [ ] **Step 1: Append Phase E completion + W11 entry**

Add (or update existing sections of) `docs/project-status.md` with:

- Phase E (single + multi + cancel + retry + reopen): **DONE** as of 2026-05-26 via real S3 + localhost workaround. CORS apply (`infra/s3/cors.json` → AWS) deferred to owner.
- W11 plan registered: `docs/superpowers/plans/2026-05-26-W11-scan-guards.md`. Closes E5 (wrong-project guard) at the API layer; locks UploadModal as new-scan-only.

Use the file's existing tone and structure — do not invent a new format.

- [ ] **Step 2: Commit**

```bash
git add docs/project-status.md
git commit -m "docs(status): mark Phase E done, register W11 plan"
```

---

## Out of Scope (do NOT do in this plan)

- AWS CORS apply (`aws s3api put-bucket-cors`) — owner-gated, separate task.
- Resume-into-draft-scan UX — see "Policy: New-Scan-Only Upload" above.
- v2 ACL semantics changes — this plan reuses `resolve_effective_project_role` as-is. If v1 logic is wrong, that's a separate plan.
- E6 large-batch (3648 files / 9GB) verification — owner approval pending.
- Auth-wide review of every other route (runs, clustering, etc.) — this plan covers the four scan-level routes the E5 audit identified. A broader sweep can follow if needed.
