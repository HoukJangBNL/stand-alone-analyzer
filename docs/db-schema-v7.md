# Qpress SAM Pipeline — DB Schema v7 (DELTA over v6)

> **Status**: Active. Source of truth for `0002_v7_auth.py`.
>
> **Stack**: PostgreSQL on RDS (`db.t4g.small`) + SQLAlchemy 2.x async + asyncpg + alembic + pgcrypto (`gen_random_uuid()`).
>
> **Scope**: This document describes ONLY the v6 → v7 delta. Tables not listed here are unchanged from v6 — see [`docs/db-schema-v6.md`](db-schema-v6.md) for canonical definitions.
>
> **Migration**: `alembic/versions/0002_v7_auth.py` (`down_revision = "0001_initial_v6"`).

---

## 1. Changes from v6

### 1.1 `users.id` widened from `BIGSERIAL` → `UUID`

- Primary key now `UUID NOT NULL DEFAULT gen_random_uuid()`.
- All six `created_by_id BIGINT REFERENCES users(id)` foreign keys widened to `UUID` in the same transaction (shadow-table migration via `users_v7` + `legacy_id BIGINT UNIQUE` mapping column to preserve referential integrity during cutover).
- Affected FK-holder tables: `scans`, `upload_sessions`, `analyses`, `domain_analyses`, `flake_analyses`, `flake_curations`.
- The pre-existing `('system')` row is **preserved** — it is re-inserted into `users_v7` with the same `created_at`, then granted `role='admin'` via the new role column. The new UUID surfaces through `legacy_id=1` mapping during FK rewire so historical rows continue to resolve.

### 1.2 New auth columns on `users`

| Column | Type | Notes |
|---|---|---|
| `cognito_sub` | `TEXT UNIQUE` | Cognito User Pool `sub` claim. Nullable — `'system'` row has none. |
| `email` | `TEXT` | Plus partial unique index `users_email_uniq` (`WHERE email IS NOT NULL`). |
| `email_verified_at` | `TIMESTAMPTZ` | Nullable; set when Cognito reports the email is verified. |
| `organization` | `TEXT` | Free-text affiliation displayed in admin views. |
| `role` | `user_role NOT NULL DEFAULT 'member'` | New ENUM (see §1.4). |
| `deactivated_at` | `TIMESTAMPTZ` | Soft-delete marker; `IS NULL` = active. |

`username` is no longer `UNIQUE NOT NULL` at the column level. v7 widens it to nullable `TEXT` with a partial unique index `users_username_uniq` (`WHERE username IS NOT NULL`) so multiple Cognito-only users (no human-readable username) can coexist.

### 1.3 New tables

| Table | PK | Purpose |
|---|---|---|
| `project_users` | `(project_id, user_id)` composite | ACL for v7 multi-project work. Maps a user to a project with a `project_role` (viewer/editor). |
| `usage_events` | `id BIGSERIAL` | Append-only telemetry: per-user, per-action events with JSONB payload. Indexed for "recent activity by user" and "recent activity by kind" queries. |

`project_users` is a forward-looking ACL skeleton — v7 ships the table and FK wiring, but the route layer (W6.1+) is what enforces it. No `projects` table exists yet (single-project deployment); `project_id` is `TEXT` so the eventual projects table can be a string-keyed slug or a UUID without a follow-up migration.

`usage_events` is intentionally minimal: `(user_id UUID FK, kind TEXT, value_json JSONB, ts TIMESTAMPTZ DEFAULT NOW())`. The two composite indexes `(user_id, ts DESC)` and `(kind, ts DESC)` cover the only two access patterns we need today.

### 1.4 New ENUMs

```sql
CREATE TYPE user_role    AS ENUM ('member', 'reader', 'operator', 'admin');
CREATE TYPE project_role AS ENUM ('viewer', 'editor');
```

`user_role` is the global tenancy role (assigned on user creation; admin is the only role that bypasses ACL checks). `project_role` is the per-project capability and lives only on `project_users`.

### 1.5 Extension requirement

`CREATE EXTENSION IF NOT EXISTS pgcrypto;` is added at the start of `upgrade()` to provide `gen_random_uuid()`. RDS already permits this extension.

---

## 2. New DDL only

The migration emits these statements (paraphrased; exact SQL lives in `alembic/versions/0002_v7_auth.py`):

```sql
-- 2.1 Extension + ENUMs
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TYPE user_role    AS ENUM ('member', 'reader', 'operator', 'admin');
CREATE TYPE project_role AS ENUM ('viewer', 'editor');

-- 2.2 Shadow users_v7 (renamed to users at end of upgrade)
CREATE TABLE users_v7 (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username           TEXT,
    cognito_sub        TEXT UNIQUE,
    email              TEXT,
    email_verified_at  TIMESTAMPTZ,
    organization       TEXT,
    role               user_role    NOT NULL DEFAULT 'member',
    deactivated_at     TIMESTAMPTZ,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    legacy_id          BIGINT       UNIQUE         -- dropped at end of upgrade
);

INSERT INTO users_v7 (username, role, legacy_id, created_at)
SELECT username, 'admin'::user_role, id, created_at FROM users;

-- 2.3 Per-FK-table rewire (×6: scans, upload_sessions, analyses,
--     domain_analyses, flake_analyses, flake_curations)
ALTER TABLE <tbl> ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);
UPDATE <tbl> t SET created_by_uuid = u.id
    FROM users_v7 u WHERE t.created_by_id = u.legacy_id;
ALTER TABLE <tbl> DROP COLUMN created_by_id;
ALTER TABLE <tbl> RENAME COLUMN created_by_uuid TO created_by_id;

-- 2.4 Cutover
DROP TABLE users CASCADE;
ALTER TABLE users_v7 RENAME TO users;
ALTER TABLE users DROP COLUMN legacy_id;

CREATE UNIQUE INDEX users_username_uniq ON users(username) WHERE username IS NOT NULL;
CREATE UNIQUE INDEX users_email_uniq    ON users(email)    WHERE email    IS NOT NULL;

-- 2.5 ACL table
CREATE TABLE project_users (
    project_id   TEXT          NOT NULL,
    user_id      UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         project_role  NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX project_users_user_idx ON project_users(user_id);

-- 2.6 Telemetry
CREATE TABLE usage_events (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT         NOT NULL,
    value_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX usage_events_user_id_ts_idx ON usage_events(user_id, ts DESC);
CREATE INDEX usage_events_kind_ts_idx    ON usage_events(kind,    ts DESC);
```

---

## 3. Migration steps (operational order)

1. `pip install -e ".[dev]"` — picks up new ORM modules (`auth.py`) and the `flake_analysis.db.models` import in `scripts/check_alembic_drift.py`.
2. **Stage gate:** run `alembic upgrade head` against a snapshot of production. Verify no rows are lost (`SELECT COUNT(*) FROM users` before/after stays at `1` — only the `'system'` row exists today).
3. **Drift gate:** run `python scripts/check_alembic_drift.py` after upgrade — must print `alembic drift check: CLEAN`. Required by the `alembic-drift.yml` GitHub Action.
4. **App restart:** every async handler holding a stale `users.id BIGINT` cached value must be recycled. Restart the API workers after upgrade lands.
5. **System user lookup:** any code that referenced `users.id = 1` for the `system` user must now look up by `username = 'system' AND role = 'admin'`. v7 explicitly forbids hard-coding the UUID — fetch it once at startup and cache in module state.

---

## 4. Rollback notes

`downgrade()` reverses the cutover via the symmetric pattern: rebuild `users_v6` with `BIGSERIAL` PK + a temporary `uuid_id UUID UNIQUE` mapping column, repopulate from current `users` (skipping rows with `username IS NULL`), rewire each FK-holder table back to `BIGINT`, drop the v7 `users` (CASCADE), rename `users_v6 → users`, drop the mapping column, then drop `usage_events`, `project_users`, and the two new ENUMs.

**Caveats:**

- Any user row with `username IS NULL` (Cognito-only) is **dropped** on downgrade — v6 schema requires `username NOT NULL UNIQUE`. In a deployment that has already onboarded Cognito users, downgrade is a destructive operation. The intended rollback path is "fix forward" via a corrective v7.x migration; this `downgrade()` exists only for lower-environment safety nets.
- `project_users` and `usage_events` rows are **fully dropped** — no v6 equivalent exists. Re-applying v7 starts these tables empty.
- The `'system'` user is reconstructed with `id=1` only if it survived v7 with `legacy_id=1` originally; otherwise the BIGSERIAL assigns a fresh id and any code that hardcoded `id=1` will break. Same lesson as §3.5: never hardcode the system-user id.

The migration roundtrip (`downgrade → base → upgrade head`) is verified clean against PostgreSQL 17 in CI before each release.
