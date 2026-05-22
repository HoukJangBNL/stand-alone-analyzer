# W6 — Authentication / Session (v2, executable)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> This plan REPLACES the SKETCH at `2026-05-21-W6-auth-session.md`. Owner has resolved the entire D-block. Sub-plans W6.0 / W6.2 / W6.3 / W6.4 / W6.5 are READY for dispatch; W6.1 (AWS Cognito infra) is READY-WITH-AWS-APPROVAL-GATE.

**Goal:** Replace the FastAPI `User(id='local', roles=('owner',))` stub (`src/flake_analysis/api/auth.py:11-13`) with a real Cognito-backed identity layer + 4-tier RBAC + per-project ACL + usage tracking + a dedicated `/login` UI. Every existing route already takes `Depends(get_current_user)`; the new dependency must be a drop-in replacement so no route body changes.

**Architecture:**
- **Identity provider — AWS Cognito User Pool** (D1 frozen). MFA, email-verify, password reset all run in Cognito Hosted UI / `cognito-idp` SDK. We do NOT redirect to Hosted UI; the SPA owns the `/login` page and posts credentials to its own `/auth/callback` after the OAuth `code` exchange.
- **Token shape — JWT bearer** (D2 frozen). The Cognito ID token is verified server-side using the user pool's JWKS (cached in-process with TTL). `Authorization: Bearer <id_token>` on every protected request. Refresh tokens are stored in an `httpOnly` cookie set by `/auth/callback`; the SPA never touches them directly.
- **Roles — 4-tier global ENUM `member < reader < operator < admin`** (D3 frozen). Per-project ACL `project_users(project_id, user_id, project_role ENUM viewer|editor)` overrides upward only — global `operator+` always overrides ACL.
- **Schema is FROZEN at v6.** This plan introduces v7 via a single hand-written migration `0002_v7_auth.py` (no autogenerate). The W8 alembic-drift CI gates parity.
- **Dev bypass.** When `SAA_AUTH_DEV_BYPASS=1` and `SAA_ENV != 'prod'`, the dependency mints a synthetic `local` user (admin role, cognito_sub=`dev:local`). Hard-fail at startup if both `SAA_AUTH_DEV_BYPASS=1` and `SAA_ENV=prod`.
- **`system` user keeps role=admin.** Workers continue to attribute writes to `system`; user-facing requests run as the authenticated principal.

**Tech Stack:**
- Backend: FastAPI 0.110+, pydantic v2.6+, `python-jose[cryptography]` 3.3+ for JWT verify, `httpx` for JWKS fetch, SQLAlchemy 2.x async + asyncpg, alembic.
- AWS: Cognito User Pool + App Client + Hosted UI domain in `us-east-2`. SES (default Cognito sender for v1; domain-verified sender deferred).
- Frontend: React 18.3, Zustand 4.5, react-router-dom 6.x.
- Tests: pytest 8.x + pytest-asyncio + httpx 0.28 `AsyncClient`+`ASGITransport`; `pytest.mark.pg` opt-in PG fixture (already present per W4.2). Vitest 1.4 + @testing-library/react.

**Pre-read (do not modify):** `src/flake_analysis/api/auth.py`, `src/flake_analysis/api/routes/*.py` (every file imports `get_current_user`), `src/flake_analysis/db/models/user.py`, `alembic/versions/0001_initial_v6.py`, `docs/db-schema-v6.md` §3 + §10, `docs/db-ops.md` §3, `tests/api/test_auth_stub.py`, `tests/api/conftest.py`, `tests/db/conftest.py`.

---

## Decisions Resolved (FROZEN — no later sub-plan may walk one back)

1. **D1. IdP = AWS Cognito User Pool.** Managed MFA + email-verify + password reset. `us-east-2`. App Client uses Authorization Code Grant + PKCE; client secret stored in SSM Parameter Store under `/saa/cognito/app_client_secret`.
2. **D2. Token = JWT bearer (Cognito ID token).** Verified via Cognito JWKS (`https://cognito-idp.us-east-2.amazonaws.com/<USER_POOL_ID>/.well-known/jwks.json`). JWKS cache TTL = 1 h, refresh-on-kid-miss. Audience = App Client ID. Issuer = user-pool URL. `token_use=id`.
3. **D3. Roles = 4-tier global ENUM `user_role`** with PG enum values `member`, `reader`, `operator`, `admin`. Default = `member`. Permissions:
   - **member**: full CRUD on projects they created (`projects.created_by_id = self.id`). No access to others'.
   - **reader**: + read on all projects.
   - **operator**: + edit/run/delete on all projects.
   - **admin**: + manage users (role change, invite, deactivate) + override per-project ACL. Always wins.
4. **D3b. Per-project ACL = `project_users(project_id, user_id, project_role ENUM viewer|editor)`.** Composite PK `(project_id, user_id)`. Both FKs `ON DELETE CASCADE`. Effective role resolution:
   ```
   effective_project_role(user, project_id) =
       'admin'                                      if user.role == 'admin'
       'editor'                                     if user.role == 'operator'
       max(global_equiv(user.role), acl_role)       otherwise
   where global_equiv: member→(editor if owner else None), reader→viewer
         max(viewer, editor) = editor
   ```
   - `member` who is NOT owner of the project AND has no ACL row → `None` (403).
   - ACL is OVERRIDE upward only; it cannot demote `operator`/`admin`.
5. **D4. Multiple projects.** A `projects` table is assumed to land in W2.x (currently `project_id="local"` is hardcoded in `ProjectContext`). For this plan we wire ACL against `project_id: str` and accept that until W2.x ships, `project_users` rows are sparse and the ACL gate is effectively a no-op for `local`. Schema lands now; gate becomes meaningful when W2.x adds the real `projects` table.
6. **D5. UI = dedicated `/login` page.** No auto-redirect to Cognito Hosted UI. The SPA submits email/password to `/auth/callback` (which proxies to Cognito `InitiateAuth`), receives tokens, stores ID token in memory + refresh cookie. Logout = sidebar user pill → menu item.
7. **D6. Order: W6 BEFORE W5.** `created_by_id` is correct from day 1 once W6 lands. W5 (uploads) attributes everything to the authenticated user.
8. **Usage tracking** = single `usage_events(id BIGSERIAL PK, user_id UUID NOT NULL, kind TEXT NOT NULL, value_json JSONB, ts TIMESTAMPTZ NOT NULL DEFAULT now())` with btree indexes on `(user_id, ts DESC)` and `(kind, ts DESC)`. Emitted from server hooks: `login`, `logout`, `image_upload` (W5), `scan_run` (in `routes/run.py`). `page_visit` is deferred to a frontend-emitted API call (NOT auto-instrumented in W6).
9. **Org** = nullable `users.organization TEXT`. No FK to a separate org table; just a label.
10. **Email verification** is mirrored from Cognito to `users.email_verified_at TIMESTAMPTZ`. We trust the `email_verified` claim on the ID token; we also persist the timestamp for audit.
11. **Dev bypass** = `SAA_AUTH_DEV_BYPASS=1`. Mints a stub `User(id=<local-uuid>, email='local@dev', role='admin', email_verified=True, cognito_sub='dev:local')`. App startup hard-fails when `SAA_AUTH_DEV_BYPASS=1` AND `SAA_ENV=prod`. Documented as `R3` in the risk register.
12. **`users.id` becomes `UUID` in v7.** v6 had `BIGSERIAL`. The system row is migrated in-place via data step. All `created_by_id BIGINT` columns are widened to `UUID` in the same migration. (See File Structure §W6.0 for the cascade.)
13. **alembic `--autogenerate` is FORBIDDEN.** Migration `0002_v7_auth.py` is hand-written. The W8 drift CI gates parity.
14. **No comments in code blocks unless documenting non-obvious WHY.** UI strings: Korean OK / English OK (match existing mixed tone). Code identifiers + docstrings: English.

---

## File Structure

### W6.0 — Schema v7 (db-specialist)

**Create:**
- `alembic/versions/0002_v7_auth.py` — hand-written migration; `down_revision = "0001_initial_v6"`.
- `docs/db-schema-v7.md` — DELTA over v6 only (not a full re-statement).
- `src/flake_analysis/db/models/auth.py` — new ORM file with `ProjectUser`, `UsageEvent`, `UserRole`, `ProjectRole` enums.

**Modify:**
- `src/flake_analysis/db/models/user.py` — `id: UUID`, add `cognito_sub`, `email`, `email_verified_at`, `organization`, `role`, `deactivated_at`. Drop `username` UNIQUE → keep column nullable (legacy seed) but no UNIQUE.
- `src/flake_analysis/db/models/__init__.py` — export the new symbols.
- All other ORM models with `created_by_id` (Scan, UploadSession, UploadItem, Analysis, DomainAnalysis, FlakeAnalysis, FlakeCuration) — column type widens `BigInteger → UUID(as_uuid=True)`.

**Tests:**
- `tests/db/test_auth_models.py` — `pytest.mark.pg`, asserts every new column / FK / index / enum exists post-`alembic upgrade head`.
- `tests/db/test_users_uuid_migration.py` — `pytest.mark.pg`, asserts the `system` row survives with `role='admin'` and a stable UUID, and that `users.id` is now `uuid` type.

### W6.1 — Cognito infra (devops-engineer, AWS-APPROVAL-GATED)

**Create:**
- `docs/cognito-setup.md` — runbook with every `aws cognito-idp` command, parameter store key map, rotation policy.
- `scripts/devops/cognito_bootstrap.sh` — idempotent shell script that creates User Pool + App Client + Hosted UI Domain + writes SSM params. Includes `--dry-run` flag.
- SSM params (created by the script):
  - `/saa/cognito/user_pool_id`
  - `/saa/cognito/app_client_id`
  - `/saa/cognito/app_client_secret` (SecureString)
  - `/saa/cognito/hosted_ui_domain`
  - `/saa/cognito/region` = `us-east-2`

**Modify:** none.

### W6.2 — Backend auth dependency (api-developer)

**Create (replacing single-file `auth.py` with a package):**
- `src/flake_analysis/api/auth/__init__.py` — re-exports `User`, `UserRole`, `get_current_user`.
- `src/flake_analysis/api/auth/tokens.py` — Cognito JWKS fetch + verify, in-process cache with TTL, `verify_id_token(token: str) -> dict`.
- `src/flake_analysis/api/auth/users.py` — async upsert by `cognito_sub`; returns the ORM `User` and a domain `User` dataclass.
- `src/flake_analysis/api/auth/dev_bypass.py` — `mint_dev_user()`, gated on env vars; raises at import time if `SAA_AUTH_DEV_BYPASS=1` AND `SAA_ENV=prod`.
- `src/flake_analysis/api/guards.py` — `require_role(min_role: UserRole)` and `require_project_role(min_project_role: ProjectRole)` factories returning FastAPI dependencies.
- `src/flake_analysis/api/routes/auth.py` — `GET /auth/me`, `POST /auth/callback` (OAuth code exchange), `POST /auth/logout`.

**Modify:**
- `src/flake_analysis/api/auth.py` → DELETE (replaced by package). Imports across `routes/*.py` keep working because `auth/__init__.py` re-exports the same names.
- `src/flake_analysis/api/app.py` — register `routes.auth.router` and `auth.dev_bypass`'s startup-validate hook.
- `src/flake_analysis/api/settings.py` (or equivalent) — add Cognito config block; load from SSM at startup with env-var fallback.

**Tests:**
- `tests/api/test_auth_tokens.py` — sign tokens with a test JWKS (`cryptography` RSA keypair fixture); cases: valid / expired / wrong-aud / wrong-iss / wrong-token-use / missing-kid / kid-rotation.
- `tests/api/test_auth_users_upsert.py` — `pytest.mark.pg`; first-login creates row with `role='member'`; second login with same `cognito_sub` is idempotent; email change updates `users.email`.
- `tests/api/test_auth_routes.py` — `/auth/me` returns the upserted user; `/auth/callback` exchanges code (Cognito mocked); `/auth/logout` clears refresh cookie.
- `tests/api/test_auth_dev_bypass.py` — flag mints `local` admin user; flag + `SAA_ENV=prod` raises at import.
- `tests/api/test_auth_stub.py` — DELETE (superseded). The two pre-existing assertions (`User` shape; `get_current_user` returns `local`/`owner`) are absorbed into `test_auth_dev_bypass.py`.

### W6.3 — Per-project ACL (api-developer)

**Create:**
- `src/flake_analysis/api/services/acl.py` — `resolve_effective_project_role(user, project_id) -> ProjectRole | None`.
- `src/flake_analysis/api/routes/admin.py` — admin-only routes:
  - `POST /admin/users/{user_id}/role` — change global role.
  - `POST /admin/projects/{project_id}/grants` — grant `viewer|editor`.
  - `DELETE /admin/projects/{project_id}/grants/{user_id}` — revoke.
  - `POST /admin/users/{user_id}/deactivate` and `/reactivate`.

**Modify:**
- `src/flake_analysis/api/guards.py` — wire `require_project_role` to `acl.resolve_effective_project_role`.

**Tests:**
- `tests/api/test_acl_resolve.py` — pure-Python matrix over (global_role × is_owner × acl_row × min_required) → expected outcome.
- `tests/api/test_admin_routes.py` — `pytest.mark.pg`; admin can grant/revoke/role-change; non-admin gets 403; deactivated user gets 401 on next request.

### W6.4 — Usage events (api-developer)

**Create:**
- `src/flake_analysis/api/services/usage.py` — `async def emit(session, user, kind, value_json=None) -> None`.
- `src/flake_analysis/api/routes/admin_usage.py` — `GET /admin/usage` with query params `user_id?, kind?, since?, until?, limit=100`. Returns rows + aggregate counts grouped by `kind`.

**Modify:**
- `src/flake_analysis/api/routes/auth.py` — call `usage.emit(kind='login')` in callback; `kind='logout'` in logout.
- `src/flake_analysis/api/routes/run.py` — call `usage.emit(kind='scan_run', value_json={'analysis_id': ...})` after a successful run start.
- (W5 will later add `image_upload`. Hook point reserved by this plan; not implemented here because W5 has not landed.)

**Tests:**
- `tests/api/test_usage_emit.py` — `pytest.mark.pg`; emit writes a row with the expected shape.
- `tests/api/test_admin_usage_route.py` — `pytest.mark.pg`; query filters work; non-admin gets 403.

### W6.5 — Frontend slice (frontend-architect)

**Create:**
- `web/src/api/auth.ts` — `loginWithPassword(email, password)`, `logout()`, `fetchCurrentUser()`, `exchangeCode(code)`.
- `web/src/state/authSlice.ts` — Zustand slice: `currentUser: User | null`, `status: 'idle' | 'loading' | 'authenticated' | 'error'`, `error: string | null`, actions `login`, `logout`, `hydrate`.
- `web/src/hooks/useCurrentUser.ts` — reads slice + triggers `hydrate` on mount.
- `web/src/pages/LoginPage.tsx` — `email`/`password` form + submit. data-testids: `auth-email-input`, `auth-password-input`, `auth-submit`.
- `web/src/pages/SignupPage.tsx` — minimal stub (Cognito sign-up via API; email verify token entry). data-testids: `auth-signup-email`, `auth-signup-password`, `auth-signup-submit`, `auth-signup-confirm-code`.
- `web/src/pages/AdminPage.tsx` — usage table + global-role manager. data-testids: `admin-usage-row`, `admin-role-select-{userId}`.
- `web/src/components/auth/LogoutMenu.tsx` — sidebar user pill + dropdown with logout. data-testid: `auth-logout-button`.
- `web/src/components/auth/RequireAuth.tsx` — route guard component.
- `web/src/components/auth/RequireRole.tsx` — role-gated component (hides nav for non-admin etc.).

**Modify:**
- `web/src/App.tsx` — add `/login`, `/signup`, `/admin` routes; wrap protected routes in `<RequireAuth>`; wrap admin route in `<RequireRole role="admin">`.
- `web/src/components/Sidebar.tsx` — mount `<LogoutMenu>` at the bottom; hide "delete others' projects" for non-operator+ via `<RequireRole>`.
- `web/src/api/sseRun.ts` and other API helpers — attach `Authorization: Bearer <id_token>` from the slice.

**Tests:**
- `web/src/state/__tests__/authSlice.test.ts` — login success/failure transitions; logout clears state.
- `web/src/hooks/__tests__/useCurrentUser.test.tsx` — hydrate fires once on mount.
- `web/src/pages/__tests__/LoginPage.test.tsx` — submit calls `loginWithPassword` with form values.
- `web/src/pages/__tests__/AdminPage.test.tsx` — renders usage rows; role-select calls API.
- `web/src/components/auth/__tests__/RequireRole.test.tsx` — hides children when role insufficient.

---

## Sub-plan W6.0 — Schema v7

**Owner:** `db-specialist` (subagent type: `qpress-db-specialist`).

**Dispatch order:** FIRST. All other sub-plans depend on this.

**Pinned constraints:**
- Single migration file: `0002_v7_auth.py`. No autogenerate.
- `users.id` widens `BIGSERIAL → UUID(as_uuid=True)`. Every `created_by_id` FK widens with it. The `system` row is migrated in place: assign a stable UUID (`gen_random_uuid()` saved in a CTE, then propagated to all `*.created_by_id` rows that referenced the old `BIGINT` id 1). Use a single transaction.
- New ENUMs `user_role` and `project_role` are created with `CREATE TYPE`.
- `project_users` and `usage_events` are CREATEd in this same migration.

### Task W6.0.1: write the migration skeleton + down-rev

**Files:**
- Create: `alembic/versions/0002_v7_auth.py`.

- [ ] **Step 1: Write the failing migration-shape test**

```python
# tests/db/test_v7_migration_shape.py
import importlib

def test_v7_migration_module_exists():
    mod = importlib.import_module("alembic.versions.0002_v7_auth")
    assert mod.revision == "0002_v7_auth"
    assert mod.down_revision == "0001_initial_v6"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
```

- [ ] **Step 2: Run — expect RED**

`pytest tests/db/test_v7_migration_shape.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create the skeleton**

```python
"""v7 auth + ACL + usage_events

Revision ID: 0002_v7_auth
Revises: 0001_initial_v6
Create Date: 2026-05-21
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002_v7_auth"
down_revision: Union[str, None] = "0001_initial_v6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    raise NotImplementedError


def downgrade() -> None:
    raise NotImplementedError
```

- [ ] **Step 4: Run — expect GREEN** on the shape test.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_v7_auth.py tests/db/test_v7_migration_shape.py
git commit -m "feat(db): scaffold v7 auth migration"
```

### Task W6.0.2: ENUMs + `users` table extension

**Files:**
- Modify: `alembic/versions/0002_v7_auth.py:upgrade` + `:downgrade`.

- [ ] **Step 1: Write the failing PG test**

```python
# tests/db/test_users_v7_columns.py
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_users_has_v7_columns(pg_session):
    rows = (await pg_session.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'users' ORDER BY column_name"
    ))).all()
    cols = {r[0]: r[1] for r in rows}
    assert cols["id"] == "uuid"
    assert "cognito_sub" in cols
    assert "email" in cols
    assert "email_verified_at" in cols
    assert "organization" in cols
    assert "role" in cols
    assert "deactivated_at" in cols

@pytest.mark.asyncio
async def test_user_role_enum_values(pg_session):
    rows = (await pg_session.execute(text(
        "SELECT unnest(enum_range(NULL::user_role))::text"
    ))).all()
    assert {r[0] for r in rows} == {"member", "reader", "operator", "admin"}

@pytest.mark.asyncio
async def test_system_user_promoted_to_admin(pg_session):
    row = (await pg_session.execute(text(
        "SELECT role::text FROM users WHERE username = 'system'"
    ))).scalar_one()
    assert row == "admin"
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement upgrade()**

```python
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE TYPE user_role AS ENUM ('member', 'reader', 'operator', 'admin');")
    op.execute("CREATE TYPE project_role AS ENUM ('viewer', 'editor');")

    op.execute("""
        CREATE TABLE users_v7 (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username           TEXT,
            cognito_sub        TEXT UNIQUE,
            email              TEXT,
            email_verified_at  TIMESTAMPTZ,
            organization       TEXT,
            role               user_role NOT NULL DEFAULT 'member',
            deactivated_at     TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            legacy_id          BIGINT UNIQUE
        );
    """)
    op.execute("""
        INSERT INTO users_v7 (username, role, legacy_id, created_at)
        SELECT username, 'admin'::user_role, id, created_at FROM users;
    """)

    op.execute("""
        ALTER TABLE scans               ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
        ALTER TABLE upload_sessions     ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
        ALTER TABLE analyses            ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
        ALTER TABLE domain_analyses     ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
        ALTER TABLE flake_analyses      ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
        ALTER TABLE flake_curations     ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
    """)
    for tbl in ("scans", "upload_sessions", "analyses", "domain_analyses",
                "flake_analyses", "flake_curations"):
        op.execute(f"""
            UPDATE {tbl} t
               SET created_by_uuid = u.id
              FROM users_v7 u
             WHERE t.created_by_id = u.legacy_id;
        """)
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN created_by_id;")
        op.execute(f"ALTER TABLE {tbl} RENAME COLUMN created_by_uuid TO created_by_id;")

    op.execute("DROP TABLE users CASCADE;")
    op.execute("ALTER TABLE users_v7 RENAME TO users;")
    op.execute("ALTER TABLE users DROP COLUMN legacy_id;")
    op.execute("CREATE UNIQUE INDEX users_username_uniq ON users(username) WHERE username IS NOT NULL;")
    op.execute("CREATE UNIQUE INDEX users_email_uniq    ON users(email) WHERE email IS NOT NULL;")
```

(`downgrade()` mirrors in reverse: re-create `users` with BIGSERIAL, re-thread FKs, drop ENUMs.)

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_v7_auth.py tests/db/test_users_v7_columns.py
git commit -m "feat(db): widen users.id to UUID + add auth columns"
```

### Task W6.0.3: `project_users` table

**Files:**
- Modify: `alembic/versions/0002_v7_auth.py`.

- [ ] **Step 1: Failing test**

```python
# tests/db/test_project_users.py
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_project_users_table_shape(pg_session):
    cols = {r[0]: r[1] for r in (await pg_session.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='project_users'"
    ))).all()}
    assert cols["project_id"] == "text"
    assert cols["user_id"] == "uuid"
    assert "project_role" in cols
    assert "created_at" in cols

@pytest.mark.asyncio
async def test_project_users_pk_is_composite(pg_session):
    pk = (await pg_session.execute(text(
        "SELECT array_agg(a.attname ORDER BY a.attnum) "
        "FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
        "WHERE i.indrelid='project_users'::regclass AND i.indisprimary"
    ))).scalar_one()
    assert set(pk) == {"project_id", "user_id"}
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Append to upgrade()**

```python
    op.execute("""
        CREATE TABLE project_users (
            project_id    TEXT NOT NULL,
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_role  project_role NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (project_id, user_id)
        );
    """)
    op.execute("CREATE INDEX project_users_user_idx ON project_users(user_id);")
```

(downgrade: `DROP TABLE project_users;`)

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_v7_auth.py tests/db/test_project_users.py
git commit -m "feat(db): add project_users ACL table"
```

### Task W6.0.4: `usage_events` table + indexes

- [ ] **Step 1: Failing test**

```python
# tests/db/test_usage_events.py
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_usage_events_columns(pg_session):
    cols = {r[0] for r in (await pg_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='usage_events'"
    ))).all()}
    assert cols >= {"id", "user_id", "kind", "value_json", "ts"}

@pytest.mark.asyncio
async def test_usage_events_indexes(pg_session):
    rows = (await pg_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename='usage_events'"
    ))).all()
    names = {r[0] for r in rows}
    assert any("user_id" in n and "ts" in n for n in names)
    assert any("kind"    in n and "ts" in n for n in names)
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Append to upgrade()**

```python
    op.execute("""
        CREATE TABLE usage_events (
            id          BIGSERIAL PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            value_json  JSONB,
            ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("CREATE INDEX usage_events_user_ts_idx ON usage_events(user_id, ts DESC);")
    op.execute("CREATE INDEX usage_events_kind_ts_idx ON usage_events(kind, ts DESC);")
```

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_v7_auth.py tests/db/test_usage_events.py
git commit -m "feat(db): add usage_events table + composite indexes"
```

### Task W6.0.5: ORM updates

**Files:**
- Modify: `src/flake_analysis/db/models/user.py`.
- Create: `src/flake_analysis/db/models/auth.py`.
- Modify: `src/flake_analysis/db/models/__init__.py`, `analysis.py`, `catalog.py`, `domain_branch.py`, `flake_branch.py`, `upload.py` — widen `created_by_id: Mapped[int]` to `Mapped[UUID]`.

- [ ] **Step 1: Failing ORM test**

```python
# tests/db/test_orm_v7_shape.py
from flake_analysis.db.models import User, ProjectUser, UsageEvent, UserRole, ProjectRole

def test_user_has_v7_attributes():
    cols = {c.name for c in User.__table__.columns}
    assert {"id", "cognito_sub", "email", "email_verified_at",
            "organization", "role", "deactivated_at"} <= cols

def test_enums_exist():
    assert {"member", "reader", "operator", "admin"} == {r.value for r in UserRole}
    assert {"viewer", "editor"} == {r.value for r in ProjectRole}
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement** — update ORM files. `User.id` becomes `Mapped[UUID]` with `mapped_column(PG_UUID(as_uuid=True), primary_key=True)`. Add new ORM module `auth.py` with `ProjectUser` and `UsageEvent` mapped classes.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Run `python scripts/check_alembic_drift.py` locally** — expect EMPTY drift.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/db/models/ tests/db/test_orm_v7_shape.py
git commit -m "feat(db): ORM models for v7 (UUID users, ProjectUser, UsageEvent)"
```

### Task W6.0.6: write `docs/db-schema-v7.md` (DELTA)

**Files:**
- Create: `docs/db-schema-v7.md`. Sections: header (Status/Stack/Scope), §1 Changes from v6 (UUID widening, new ENUMs, new tables), §2 New DDL only, §3 Migration steps, §4 Rollback notes. Cross-link v6 doc for unchanged tables.

- [ ] **Step 1: Verify file exists with required sections**

`grep -c "^## " docs/db-schema-v7.md` → expect ≥ 4.

- [ ] **Step 2: Commit**

```bash
git add docs/db-schema-v7.md
git commit -m "docs(db): add v7 schema delta (auth + ACL + usage_events)"
```

### Task W6.0.7: alembic-drift CI gate

- [ ] **Step 1: Push branch and verify `alembic-drift.yml` workflow passes** on the PR. If RED, fix model/DDL drift before merging.
- [ ] **Step 2: No commit needed unless drift is found.**

---

## Sub-plan W6.1 — Cognito infrastructure (AWS-APPROVAL-GATED)

**Owner:** `devops-engineer` (subagent type: `devops-architect`).

> **🚨 STOP — USER APPROVAL GATE 🚨**
>
> Before any `aws cognito-idp create-user-pool` or `aws ssm put-parameter` runs, the agent MUST present this approval prompt to the user (via PM):
>
> ```
> About to create AWS resources in account 931886963315 (us-east-2):
>   - Cognito User Pool: saa-users (MFA optional, email verify required, password policy: 12 chars min, mixed case, digit, symbol)
>   - App Client: saa-spa (Authorization Code Grant + PKCE, refresh token TTL = 30 days)
>   - Hosted UI Domain: saa-{accountId}.auth.us-east-2.amazoncognito.com
>   - SSM SecureString:  /saa/cognito/app_client_secret
>   - SSM String x4:     /saa/cognito/{user_pool_id, app_client_id, hosted_ui_domain, region}
>
> Cost: Cognito free tier covers 50,000 MAU, then $0.0055/MAU. SSM Parameter Store standard tier is free up to 10,000 params.
>
> Approve? (yes / no / dry-run-first)
> ```
>
> If user replies `yes`: proceed with `cognito_bootstrap.sh`.
> If `dry-run-first`: run `cognito_bootstrap.sh --dry-run` and present the planned commands; ask again.
> If `no`: abort and report back.

### Task W6.1.1: Write `cognito_bootstrap.sh` (offline)

- [ ] **Step 1: Author the script** at `scripts/devops/cognito_bootstrap.sh`. Idempotent — uses `aws cognito-idp list-user-pools` to detect existing pool; skips if found. Supports `--dry-run` (prints commands without running). Required args: `--region us-east-2 --account-id 931886963315`.
- [ ] **Step 2: Shellcheck**

```bash
shellcheck scripts/devops/cognito_bootstrap.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/devops/cognito_bootstrap.sh
git commit -m "feat(devops): cognito bootstrap script (idempotent, dry-run capable)"
```

### Task W6.1.2: Write `docs/cognito-setup.md`

- [ ] **Step 1: Author runbook**. Sections: §1 Inventory (pool ID, client ID, region — placeholders until §3 fills them), §2 Bootstrap (how to run the script), §3 Post-create checklist (sign-up test, email-verify test, MFA enroll test), §4 Rotation policy (client secret rotation every 90 days), §5 Tear-down.
- [ ] **Step 2: Commit**

```bash
git add docs/cognito-setup.md
git commit -m "docs(devops): cognito-setup runbook"
```

### Task W6.1.3: APPROVAL GATE → run bootstrap

- [ ] **Step 1: Present approval prompt to PM/user (template above).**
- [ ] **Step 2: On `yes`, run `bash scripts/devops/cognito_bootstrap.sh --region us-east-2 --account-id 931886963315`.**
- [ ] **Step 3: Verify SSM params**

```bash
aws --profile qpress --region us-east-2 ssm get-parameters-by-path --path /saa/cognito --with-decryption --query 'Parameters[].Name'
```

Expected: 5 params present.

- [ ] **Step 4: Update `docs/cognito-setup.md` §1 with the actual IDs.**

- [ ] **Step 5: Commit**

```bash
git add docs/cognito-setup.md
git commit -m "docs(devops): record cognito user pool + app client IDs"
```

### Task W6.1.4: smoke test — sign up a test user

- [ ] **Step 1:** Use `aws cognito-idp sign-up` with a `+test` Gmail alias.
- [ ] **Step 2:** Confirm via emailed code → `aws cognito-idp confirm-sign-up`.
- [ ] **Step 3:** `aws cognito-idp initiate-auth` with `USER_PASSWORD_AUTH` flow → expect a valid ID token.
- [ ] **Step 4:** Decode the ID token with `python -c "import jwt; ..."` and confirm `aud`, `iss`, `token_use=id`, `email_verified=True`.
- [ ] **Step 5:** Document result in `docs/cognito-setup.md` §3.
- [ ] **Step 6:** Commit doc update.

---

## Sub-plan W6.2 — Backend auth dependency

**Owner:** `api-developer` (subagent type: `backend-architect`).

**Dispatch order:** parallel with W6.1 once W6.0 lands. W6.2 tests use a mock JWKS — no real Cognito needed.

### Task W6.2.1: token verifier with JWKS cache

**Files:**
- Create: `src/flake_analysis/api/auth/__init__.py`, `tokens.py`, `users.py`, `dev_bypass.py`.
- DELETE: `src/flake_analysis/api/auth.py` (the single-file stub).

- [ ] **Step 1: Failing test for happy path**

```python
# tests/api/test_auth_tokens.py
import time
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flake_analysis.api.auth.tokens import verify_id_token, _JwksCache

@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def _sign(claims, key, kid="testkid"):
    from jose import jwt
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

def _jwk_from_key(key, kid="testkid"):
    from jose.utils import long_to_base64
    pub = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
            "n": long_to_base64(pub.n).decode(), "e": long_to_base64(pub.e).decode()}

@pytest.mark.asyncio
async def test_verify_id_token_happy(rsa_key, monkeypatch):
    cache = _JwksCache()
    cache.set([_jwk_from_key(rsa_key)])
    monkeypatch.setattr("flake_analysis.api.auth.tokens._jwks_cache", cache)
    monkeypatch.setenv("SAA_COGNITO_AUDIENCE", "client-abc")
    monkeypatch.setenv("SAA_COGNITO_ISSUER", "https://issuer.example/pool-1")
    token = _sign({
        "sub": "user-1", "aud": "client-abc",
        "iss": "https://issuer.example/pool-1",
        "token_use": "id", "email": "u@e", "email_verified": True,
        "exp": int(time.time()) + 600, "iat": int(time.time()),
    }, rsa_key)
    claims = await verify_id_token(token)
    assert claims["sub"] == "user-1"
    assert claims["email_verified"] is True
```

- [ ] **Step 2: Run — expect RED** (module missing).

- [ ] **Step 3: Implement `tokens.py`** — `_JwksCache` (TTL=3600s, refresh on `kid` miss), `async def verify_id_token(token: str) -> dict` (rejects expired / wrong-aud / wrong-iss / `token_use != 'id'`).

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Add the 6 negative tests** (expired, wrong-aud, wrong-iss, wrong-token-use, missing-kid, malformed). Run — expect 6 GREEN after implementation.

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/auth/tokens.py src/flake_analysis/api/auth/__init__.py tests/api/test_auth_tokens.py
git commit -m "feat(api): cognito ID token verifier with JWKS cache + TTL"
```

### Task W6.2.2: async user upsert by `cognito_sub`

- [ ] **Step 1: Failing PG test**

```python
# tests/api/test_auth_users_upsert.py
import pytest
from flake_analysis.api.auth.users import upsert_from_claims

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_first_login_creates_member(pg_session):
    u = await upsert_from_claims(pg_session, {
        "sub": "cog-1", "email": "a@b", "email_verified": True,
    })
    assert u.role.value == "member"
    assert u.cognito_sub == "cog-1"

@pytest.mark.asyncio
async def test_second_login_is_idempotent(pg_session):
    a = await upsert_from_claims(pg_session, {"sub": "cog-1", "email": "a@b", "email_verified": True})
    b = await upsert_from_claims(pg_session, {"sub": "cog-1", "email": "a@b", "email_verified": True})
    assert a.id == b.id

@pytest.mark.asyncio
async def test_email_change_updates_row(pg_session):
    a = await upsert_from_claims(pg_session, {"sub": "cog-1", "email": "a@b", "email_verified": True})
    b = await upsert_from_claims(pg_session, {"sub": "cog-1", "email": "c@d", "email_verified": True})
    assert a.id == b.id and b.email == "c@d"
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `users.py`** — INSERT ... ON CONFLICT (cognito_sub) DO UPDATE.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/auth/users.py tests/api/test_auth_users_upsert.py
git commit -m "feat(api): async user upsert by cognito_sub"
```

### Task W6.2.3: `get_current_user` dependency (drop-in)

- [ ] **Step 1: Failing route test** — POST a valid token to an existing protected route and assert it succeeds with the upserted user, not the `local` stub.

```python
# tests/api/test_auth_dep_dropin.py
import pytest
from httpx import ASGITransport, AsyncClient
from flake_analysis.api.app import app

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_protected_route_accepts_valid_token(signed_token, pg_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {signed_token}"})
        assert r.status_code == 200
        assert r.json()["email"]

@pytest.mark.asyncio
async def test_protected_route_rejects_missing_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me")
        assert r.status_code == 401
```

(`signed_token` fixture lives in `tests/api/conftest.py`, derived from the JWKS fixture used in `test_auth_tokens.py`.)

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement** the new `get_current_user` in `auth/__init__.py`:
  - Read `Authorization: Bearer <token>`.
  - Call `verify_id_token`.
  - Call `upsert_from_claims`.
  - Return domain `User(id: UUID, email: str, role: UserRole, email_verified: bool, cognito_sub: str)`.
  - On any error → raise `HTTPException(401, ...)`.
  - When `SAA_AUTH_DEV_BYPASS=1` → short-circuit to `mint_dev_user()`.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Verify all 35+ existing route tests still pass** (the dependency is drop-in):

```bash
pytest tests/api/ -x
```

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/auth/__init__.py tests/api/test_auth_dep_dropin.py tests/api/conftest.py
git commit -m "feat(api): drop-in cognito-backed get_current_user"
```

### Task W6.2.4: dev-bypass + prod-leak guard

- [ ] **Step 1: Failing test for the prod guard**

```python
# tests/api/test_auth_dev_bypass.py
import pytest

def test_bypass_blocked_in_prod(monkeypatch):
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "prod")
    with pytest.raises(RuntimeError, match="dev-bypass.*prod"):
        import importlib, flake_analysis.api.auth.dev_bypass as m
        importlib.reload(m)

def test_bypass_mints_local_admin(monkeypatch):
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "dev")
    import importlib, flake_analysis.api.auth.dev_bypass as m
    importlib.reload(m)
    u = m.mint_dev_user()
    assert u.role.value == "admin"
    assert u.cognito_sub == "dev:local"
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `dev_bypass.py`** — module-level `if os.getenv("SAA_AUTH_DEV_BYPASS")=="1" and os.getenv("SAA_ENV")=="prod": raise RuntimeError(...)`.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Delete `tests/api/test_auth_stub.py`** (superseded — the two original assertions are absorbed above).

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/auth/dev_bypass.py tests/api/test_auth_dev_bypass.py
git rm tests/api/test_auth_stub.py
git commit -m "feat(api): dev-bypass with prod-leak hard guard"
```

### Task W6.2.5: `/auth/me`, `/auth/callback`, `/auth/logout` routes

- [ ] **Step 1: Failing route tests**

```python
# tests/api/test_auth_routes.py — sketches
@pytest.mark.asyncio
async def test_auth_me_returns_user(signed_token):
    ...

@pytest.mark.asyncio
async def test_auth_callback_exchanges_code(monkeypatch, mock_cognito):
    ...

@pytest.mark.asyncio
async def test_auth_logout_clears_refresh_cookie():
    ...
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `routes/auth.py`.**
  - `GET /auth/me` → returns the current user.
  - `POST /auth/callback {code, redirect_uri}` → calls Cognito `oauth2/token`; sets `Set-Cookie: refresh=...; HttpOnly; Secure; SameSite=Lax`; returns `{id_token, expires_in, user}`.
  - `POST /auth/logout` → clears refresh cookie; calls Cognito global sign-out; emits `usage.emit(kind='logout')`.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/auth.py src/flake_analysis/api/app.py tests/api/test_auth_routes.py
git commit -m "feat(api): /auth/me + /auth/callback + /auth/logout"
```

---

## Sub-plan W6.3 — Per-project ACL

**Owner:** `api-developer`.

**Dispatch order:** AFTER W6.2.

### Task W6.3.1: pure resolver + matrix test

**Files:**
- Create: `src/flake_analysis/api/services/acl.py`, `tests/api/test_acl_resolve.py`.

- [ ] **Step 1: Failing matrix test**

```python
# tests/api/test_acl_resolve.py
import pytest
from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import UserRole, ProjectRole

@pytest.mark.parametrize("global_role,is_owner,acl,expected", [
    (UserRole.MEMBER,   True,  None,                 ProjectRole.EDITOR),
    (UserRole.MEMBER,   False, None,                 None),
    (UserRole.MEMBER,   False, ProjectRole.VIEWER,   ProjectRole.VIEWER),
    (UserRole.MEMBER,   False, ProjectRole.EDITOR,   ProjectRole.EDITOR),
    (UserRole.READER,   False, None,                 ProjectRole.VIEWER),
    (UserRole.READER,   False, ProjectRole.EDITOR,   ProjectRole.EDITOR),
    (UserRole.OPERATOR, False, None,                 ProjectRole.EDITOR),
    (UserRole.OPERATOR, False, ProjectRole.VIEWER,   ProjectRole.EDITOR),
    (UserRole.ADMIN,    False, None,                 ProjectRole.EDITOR),
])
def test_resolve_matrix(global_role, is_owner, acl, expected):
    out = resolve_effective_project_role(global_role, is_owner=is_owner, acl_role=acl)
    assert out == expected
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `acl.py`** per the matrix in §"Decisions Resolved" #4.

- [ ] **Step 4: Run — expect GREEN (9 cases).**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/acl.py tests/api/test_acl_resolve.py
git commit -m "feat(api): pure ACL resolver (global role + ownership + project_users)"
```

### Task W6.3.2: `require_role` and `require_project_role` guards

- [ ] **Step 1: Failing test** — protected route with `require_role(UserRole.OPERATOR)`; member gets 403, operator gets 200.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `guards.py`** as factory functions returning FastAPI dependencies.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/guards.py tests/api/test_guards.py
git commit -m "feat(api): require_role + require_project_role guards"
```

### Task W6.3.3: admin routes

- [ ] **Step 1: Failing tests** for each route (grant/revoke/role-change/deactivate/reactivate). Cover both happy path and 403-for-non-admin.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `routes/admin.py`** — gated on `require_role(UserRole.ADMIN)`.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/admin.py tests/api/test_admin_routes.py
git commit -m "feat(api): admin routes for role + ACL + deactivation"
```

---

## Sub-plan W6.4 — Usage events

**Owner:** `api-developer`.

**Dispatch order:** AFTER W6.3 (or in parallel — only depends on W6.0 + W6.2).

### Task W6.4.1: `emit` helper

- [ ] **Step 1: Failing test**

```python
# tests/api/test_usage_emit.py
import pytest
from flake_analysis.api.services.usage import emit
from flake_analysis.db.models import UsageEvent

pytestmark = pytest.mark.pg

@pytest.mark.asyncio
async def test_emit_writes_row(pg_session, sample_user_factory):
    u = await sample_user_factory()
    await emit(pg_session, u, "scan_run", {"analysis_id": 42})
    rows = (await pg_session.execute(
        "SELECT kind, value_json->>'analysis_id' FROM usage_events"
    )).all()
    assert rows == [("scan_run", "42")]
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `services/usage.py`.**

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/usage.py tests/api/test_usage_emit.py
git commit -m "feat(api): usage.emit helper with composite-indexed write path"
```

### Task W6.4.2: hook into `routes/auth.py` (login/logout)

- [ ] **Step 1: Extend `test_auth_routes.py`** — assert that a successful `/auth/callback` writes one `usage_events` row with `kind='login'`. `/auth/logout` writes `kind='logout'`.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Insert `await emit(...)` calls.**
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/auth.py tests/api/test_auth_routes.py
git commit -m "feat(api): emit usage events on login + logout"
```

### Task W6.4.3: hook into `routes/run.py` (scan_run)

- [ ] **Step 1: Failing test** — POST any `/run/...` SSE start → assert one `usage_events` row with `kind='scan_run'`.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Insert `await emit(...)` after the run kicks off (NOT inside the executor — inside the request handler).**
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/run.py tests/api/test_run_emits_usage.py
git commit -m "feat(api): emit scan_run usage event from /run endpoints"
```

### Task W6.4.4: `GET /admin/usage`

- [ ] **Step 1: Failing tests** — query by `user_id`, `kind`, `since`, `until`; aggregate counts by kind; non-admin gets 403.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `routes/admin_usage.py`.** Use the composite indexes.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/routes/admin_usage.py tests/api/test_admin_usage_route.py
git commit -m "feat(api): GET /admin/usage with kind+time+user filters"
```

> **Reserved hook (NOT implemented in W6.4):**
>
> - `image_upload` event will be emitted from W5's upload-complete handler. W6.4 only sets up the helper; the call site lands when W5 ships.
> - `page_visit` is FRONTEND-EMITTED. The SPA will POST `/admin/usage/track {kind: 'page_visit', value_json: {path}}` (a future endpoint, NOT in W6.4 scope).

---

## Sub-plan W6.5 — Frontend slice

**Owner:** `frontend-architect` (subagent type: `frontend-architect`).

**Dispatch order:** LAST. Depends on W6.2 (auth dep), W6.3 (admin routes), W6.4 (usage routes).

### Task W6.5.1: API client

- [ ] **Step 1: Failing typing test**

```typescript
// web/src/api/__tests__/auth.types.test.ts
import { expectTypeOf, describe, it } from 'vitest'
import type { LoginResult, CurrentUser } from '@/api/auth'

describe('auth types', () => {
  it('LoginResult exposes id_token + user', () => {
    expectTypeOf<LoginResult>().toMatchTypeOf<{ id_token: string; user: CurrentUser }>()
  })
})
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `web/src/api/auth.ts`** — `loginWithPassword`, `logout`, `fetchCurrentUser`, `exchangeCode`. Use `fetch` with `credentials: 'include'` for the refresh cookie.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add web/src/api/auth.ts web/src/api/__tests__/auth.types.test.ts
git commit -m "feat(web): auth API client (login/logout/me/exchangeCode)"
```

### Task W6.5.2: Zustand `authSlice`

- [ ] **Step 1: Failing slice test**

```typescript
// web/src/state/__tests__/authSlice.test.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

vi.mock('@/api/auth', () => ({
  loginWithPassword: vi.fn(async () => ({ id_token: 'tok', user: { id: 'u', email: 'a@b', role: 'member', email_verified: true } })),
  logout: vi.fn(async () => undefined),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u', email: 'a@b', role: 'member', email_verified: true })),
}))

describe('authSlice', () => {
  beforeEach(() => resetAuthStore())

  it('login transitions idle→loading→authenticated', async () => {
    const p = useAuthStore.getState().login('a@b', 'p')
    expect(useAuthStore.getState().status).toBe('loading')
    await p
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().currentUser?.email).toBe('a@b')
  })

  it('logout clears state', async () => {
    await useAuthStore.getState().login('a@b', 'p')
    await useAuthStore.getState().logout()
    expect(useAuthStore.getState().currentUser).toBeNull()
    expect(useAuthStore.getState().status).toBe('idle')
  })
})
```

- [ ] **Step 2: Run — expect RED.**

- [ ] **Step 3: Implement `web/src/state/authSlice.ts`.** Token kept in memory only; refresh handled via cookie.

- [ ] **Step 4: Run — expect GREEN.**

- [ ] **Step 5: Commit**

```bash
git add web/src/state/authSlice.ts web/src/state/__tests__/authSlice.test.ts
git commit -m "feat(web): authSlice (login/logout/hydrate transitions)"
```

### Task W6.5.3: `useCurrentUser` hook

- [ ] **Step 1: Failing test** — render a component using the hook → assert `fetchCurrentUser` is called once on mount.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement** the hook with a `useEffect(..., [])`.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useCurrentUser.ts web/src/hooks/__tests__/useCurrentUser.test.tsx
git commit -m "feat(web): useCurrentUser hook with mount-time hydrate"
```

### Task W6.5.4: `LoginPage`

- [ ] **Step 1: Failing test**

```tsx
// web/src/pages/__tests__/LoginPage.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoginPage } from '@/pages/LoginPage'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

describe('<LoginPage>', () => {
  beforeEach(() => resetAuthStore())
  it('submits email + password to slice', () => {
    const spy = vi.spyOn(useAuthStore.getState(), 'login')
    render(<LoginPage />)
    fireEvent.change(screen.getByTestId('auth-email-input'), { target: { value: 'a@b' } })
    fireEvent.change(screen.getByTestId('auth-password-input'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByTestId('auth-submit'))
    expect(spy).toHaveBeenCalledWith('a@b', 'pw')
  })
})
```

- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `LoginPage.tsx`** with the three test-ids. Plain HTML form.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/pages/LoginPage.tsx web/src/pages/__tests__/LoginPage.test.tsx
git commit -m "feat(web): LoginPage (email + password form)"
```

### Task W6.5.5: `SignupPage` (minimal)

- [ ] **Step 1: Failing test** — fields render + submit calls a `signUp` API stub.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `SignupPage.tsx`** with email/password/confirm-code fields and a 2-step (sign-up → confirm) state machine.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/pages/SignupPage.tsx web/src/pages/__tests__/SignupPage.test.tsx web/src/api/auth.ts
git commit -m "feat(web): SignupPage with confirm-code step"
```

### Task W6.5.6: `RequireAuth` and `RequireRole`

- [ ] **Step 1: Failing tests** — `RequireAuth` redirects to `/login` when `currentUser` null; `RequireRole role="admin"` hides children for member.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement both components.**
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/components/auth/RequireAuth.tsx web/src/components/auth/RequireRole.tsx web/src/components/auth/__tests__/
git commit -m "feat(web): RequireAuth + RequireRole route guards"
```

### Task W6.5.7: Sidebar user pill + logout

- [ ] **Step 1: Failing test** — click `auth-logout-button` calls `useAuthStore.logout()`.
- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `LogoutMenu.tsx`** and mount in `Sidebar.tsx`. Hide the "delete others' projects" affordance behind `<RequireRole role="operator">`.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/components/auth/LogoutMenu.tsx web/src/components/Sidebar.tsx web/src/components/auth/__tests__/LogoutMenu.test.tsx
git commit -m "feat(web): sidebar user pill + logout menu + role-gated nav"
```

### Task W6.5.8: `AdminPage`

- [ ] **Step 1: Failing test** — renders usage rows from API; role-select calls API.

```tsx
// web/src/pages/__tests__/AdminPage.test.tsx
it('renders usage rows', async () => {
  vi.mocked(fetchUsage).mockResolvedValue([{ user_id: 'u', kind: 'login', ts: 'x' }])
  render(<AdminPage />)
  expect(await screen.findAllByTestId(/^admin-usage-row/)).toHaveLength(1)
})

it('role-select submits', async () => {
  render(<AdminPage />)
  const sel = await screen.findByTestId('admin-role-select-u1')
  fireEvent.change(sel, { target: { value: 'operator' } })
  expect(updateUserRole).toHaveBeenCalledWith('u1', 'operator')
})
```

- [ ] **Step 2: Run — expect RED.**
- [ ] **Step 3: Implement `AdminPage.tsx`** — usage table + role manager grid.
- [ ] **Step 4: Run — expect GREEN.**
- [ ] **Step 5: Commit**

```bash
git add web/src/pages/AdminPage.tsx web/src/pages/__tests__/AdminPage.test.tsx
git commit -m "feat(web): AdminPage (usage table + role manager)"
```

### Task W6.5.9: wire routes in `App.tsx` + attach `Authorization` header

- [ ] **Step 1: Read current `App.tsx` to find Routes mount site.**

```bash
grep -n "<Routes>" web/src/App.tsx
```

- [ ] **Step 2: Add `/login`, `/signup`, `/admin` routes; wrap protected routes in `<RequireAuth>`; wrap `/admin` in `<RequireRole role="admin">`.**
- [ ] **Step 3: Modify `web/src/api/sseRun.ts` and `clustering.ts`/`projects.ts`/`selector.ts`/`explorer.ts`** — read `useAuthStore.getState().idToken` and attach `Authorization: Bearer ...` to every fetch.
- [ ] **Step 4: Run full suite** — `cd web && npm test` — expect all green.
- [ ] **Step 5: Browser smoke (Playwright MCP)** — visit `/login`, sign in with the test user from W6.1.4, navigate Compute → Selector → Clustering → Explorer → Admin; verify role-gated nav.
- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/api/
git commit -m "feat(web): wire auth routes + attach Bearer token to all API calls"
```

---

## Risk Register

| ID | Risk | Mitigation |
|---|---|---|
| **R1** | **Token leak via SPA / XSS.** ID token in memory is XSS-readable; refresh cookie is `httpOnly`. | ID token TTL = 1h (Cognito default); refresh cookie `httpOnly + Secure + SameSite=Lax`; CSP header restricts inline scripts. |
| **R2** | **JWKS rotation.** Cognito rotates signing keys; clients with stale `kid` cache fail. | `_JwksCache` refreshes on `kid` miss (force re-fetch) before rejecting. TTL fallback = 1 h. Test in W6.2.1 covers this case. |
| **R3** | **Dev-bypass leaks to prod.** Operator forgets `SAA_AUTH_DEV_BYPASS=1` in env. | Hard fail at module import when `SAA_AUTH_DEV_BYPASS=1 && SAA_ENV=prod` — covered by `test_auth_dev_bypass.py`. CI smoke test sets `SAA_ENV=prod` and asserts startup raises. |
| **R4** | **Migration of existing `system` row.** v6 has `BIGSERIAL id=1`; v7 needs UUID. Cascade through every `created_by_id` FK. | Single-transaction migration with `users_v7` shadow table + `legacy_id` mapping column. Test `test_users_uuid_migration.py` asserts `system` survives + role=admin. |
| **R5** | **`project_users` is empty until W2.x.** Until a real `projects` table lands, ACL gate effectively no-ops for `local`. | Schema lands now; documented in §"D5". `resolve_effective_project_role` falls back to global-role-only. No write path in W6 depends on `project_users` rows. |
| **R6** | **`usage_events` table growth.** Every login + scan_run + image_upload writes a row → potentially millions per year. | Composite indexes `(user_id, ts DESC)` and `(kind, ts DESC)` keep queries fast. Defer partitioning until table > 10M rows; documented in `docs/db-schema-v7.md` §"Future". |
| **R7** | **Cognito vendor lock-in.** Cognito user pool migration is painful. | Persist `cognito_sub` as the canonical external key — swappable for any OIDC `sub`. Token verifier abstracts JWKS source via `SAA_COGNITO_ISSUER` env. Switch IdP = swap issuer + re-upsert users by `cognito_sub`. |
| **R8** | **Token revocation latency.** JWT is stateless; revoking a compromised token requires waiting up to 1 h for expiry. | `users.deactivated_at` is checked on every `get_current_user` call after JWKS verify. A deactivated user's token is rejected immediately even if otherwise valid. Documented in W6.3.3. |
| **R9** | **Admin lockout.** Demoting the only admin leaves no one able to manage roles. | `routes/admin.py` `POST /admin/users/{id}/role` rejects demotion when caller is the only remaining `admin`. Covered by a test in W6.3.3. |
| **R10** | **OAuth code-exchange CSRF.** Stale or replayed `code` accepted. | `code` is single-use (Cognito enforces). PKCE `code_verifier` carried in client state. Documented in `routes/auth.py` callback. |

---

## Execution Handoff

**Sub-plan readiness:**

| Sub-plan | Status | Owner | Dispatch |
|---|---|---|---|
| **W6.0** Schema v7 | READY | db-specialist | Dispatch FIRST |
| **W6.1** Cognito infra | READY-WITH-AWS-APPROVAL-GATE | devops-engineer | Parallel with W6.2 after W6.0 |
| **W6.2** Backend auth dep | READY | api-developer | Parallel with W6.1 after W6.0 |
| **W6.3** Per-project ACL | READY | api-developer | After W6.2 |
| **W6.4** Usage events | READY | api-developer | After W6.2 (parallel with W6.3 OK) |
| **W6.5** Frontend slice | READY | frontend-architect | LAST — after W6.2/W6.3/W6.4 |

**Dispatch order:**

```
W6.0 → ( W6.1  ‖  W6.2 ) → ( W6.3  ‖  W6.4 ) → W6.5
```

**Inter-sub-plan handoffs:**
- W6.0 → W6.2: ORM models (`User`, `ProjectUser`, `UsageEvent`, `UserRole`, `ProjectRole`) must be importable.
- W6.1 → W6.2: SSM params populated for the runtime config; until then W6.2 uses env-var fallback.
- W6.2 → W6.3: `get_current_user` returns the new domain `User`; guards depend on it.
- W6.3 → W6.5: admin routes return shapes the AdminPage consumes.
- W6.4 → W6.5: `/admin/usage` shape is consumed by the AdminPage usage table.

**Plan saved to** `docs/superpowers/plans/2026-05-21-W6-auth-session-v2.md`.

**Recommended:** Subagent-Driven Development. Each task is self-contained TDD-ready (red → green → commit). Cross-domain (db / api / devops / frontend) — ideal for fresh-subagent dispatch per sub-plan.
