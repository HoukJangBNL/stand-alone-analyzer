# W6 — Authentication / Session (system → real users) Implementation Plan

> **Status: SKETCH + DECISIONS-PENDING.** Captures the architecture options for replacing the v1 `system` user stub with real user identity. PM must resolve §"Decisions Pending" with the user before this plan becomes executable.

> **For agentic workers (after sign-off):** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Replace the FastAPI `get_current_user()` stub (`src/flake_analysis/api/auth.py:11–13` returns `User(id='local', roles=('owner',))` regardless of request headers) with a real identity layer so multi-user scenarios are possible. `users` table already exists on RDS (`docs/db-schema-v6.md` §3 line 414, `'system'` row seeded), and every `*_by` column is wired to `users(id)`.

**Architecture (intent, not pinned):**
- **Identity provider — pick one in D1.** Three viable v1 paths: (a) AWS Cognito User Pool (managed, MFA, OAuth2); (b) GitHub OAuth (low-friction for an internal lab tool, no password storage); (c) hand-rolled email+password with bcrypt (full control, but the smallest team feature has the most security cost).
- **Token shape — pick one in D2.** JWT (stateless, fits Cognito) vs server-side session cookie (stateful, fits hand-rolled). `httpOnly` + `Secure` + `SameSite=Lax` regardless.
- **Session resolution.** `get_current_user()` reads the bearer token (or session cookie), validates, looks up or upserts the user row, returns `User(id=..., roles=...)`. Routes that already depend on `get_current_user` need no change — the dependency surface is preserved.
- **`system` row stays.** Background jobs / SAM workers run as `system`; user-facing requests run as the authenticated principal.
- **No new DB schema.** v6 `users` row supports username + (optionally) email; auth provider IDs live in a JSONB column or a new column added in v7 if D1 forces it.

**Tech Stack (intent):**
- API: FastAPI dependency, `python-jose` (JWT) or `itsdangerous` (signed cookies), `cryptography`.
- AWS: Cognito (if D1 = a) — devops-engineer territory; SSM Parameter Store for client secrets.
- Tests: httpx + signed-token fixtures.

**Pre-read:** `src/flake_analysis/api/auth.py`, `docs/db-schema-v6.md` §3 (users table).

---

## Decisions Pending

### D1. Identity provider

| Option | Pros | Cons |
|---|---|---|
| **A. Cognito** | Managed, MFA, password reset, OAuth2 federation, AWS-native | Adds AWS service + cost (~$0.0055/MAU, free tier 50k); user pool config to manage |
| **B. GitHub OAuth** | Zero account setup for internal users with GitHub, free | External dependency; non-GitHub users blocked; identity tied to GitHub handle |
| **C. Hand-rolled email+password** | No external IdP, full control, easy to test | Password reset flow + email infrastructure + bcrypt + rate limiting all on us |

**Recommendation**: A (Cognito) if even modest external user growth is expected, otherwise B for an internal-only deployment. C only if there's a hard requirement to avoid both AWS Cognito and GitHub.

**Open**: A vs B vs C. **Owner**: user (cost vs convenience vs lock-in).

### D2. Token shape

| Option | Pros | Cons |
|---|---|---|
| **JWT bearer** | Stateless, fits Cognito | Revocation requires a deny-list; must rotate signing key |
| **Server session cookie** | Easy revocation (delete row), standard browser behaviour | Stateful — every request hits a session table |
| **Both** | API clients use JWT, browser clients use cookies | Two code paths |

**Recommendation**: server session cookie if D1=C, JWT if D1=A.

**Open**: token type — coupled to D1.

### D3. User upsert policy

- First-login: silently create the `users` row, role = `'viewer'`?
- Or: explicit invite list (only emails on the list can log in)?
- Roles in v1: `owner`, `viewer` only? Or also `admin`?

**Open**: D3a (auto-upsert vs invite-only), D3b (role set), D3c (default role for first-time login).

### D4. Multi-project / authorization model

- v1 has a single project (`local`). When projects multiply (W2.1 backlog), do all users see all projects, or do projects have ACLs?
- If ACLs: `project_users(project_id, user_id, role)` bridge table — adds a v7 schema rev.

**Open**: ACL model. **Owner**: user (multi-tenancy intent).

### D5. UI flow

- Login page route (`/login`) vs redirect-to-IdP (Cognito/GitHub OAuth)?
- Where does logout live? (Sidebar footer? Top-right menu?)

**Open**: D5a (login UX), D5b (logout placement).

### D6. Order vs W5 (uploads)

- Two valid orderings:
  - W5 first → all uploads attributed to `system` until W6 lands. Migration risk: backfilling `created_by_id` is awkward when names are pseudonymous.
  - W6 first → no uploads exist yet, so `created_by_id` is correct from day one. UI work doubles before any user value.

**Open**: order. **Owner**: user (which gates which feature).

---

## Sketch of File Structure (subject to D1–D6)

**New (backend):**
- `src/flake_analysis/api/auth/` — replaces single-file `auth.py`.
  - `__init__.py` — re-export `User`, `get_current_user`.
  - `tokens.py` — sign/verify (JWT or cookie helpers).
  - `provider.py` — IdP-specific glue (Cognito client, GitHub OAuth callback, or password verifier).
  - `users.py` — async DB upsert + role lookup.
- `src/flake_analysis/api/routes/auth.py` — `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me`.
- Migrations: possibly `0002_users_idp_columns.py` adding `users.idp_subject TEXT UNIQUE`, `users.email TEXT UNIQUE`, `users.role TEXT NOT NULL DEFAULT 'viewer'`. v7 doc rev required.

**New (frontend):**
- `web/src/api/auth.ts`
- `web/src/state/authSlice.ts`
- `web/src/pages/LoginPage.tsx`
- Sidebar: user pill + logout.

**Tests:**
- `tests/api/test_auth_dep.py` — token fixtures, valid/invalid/expired.
- `tests/api/test_auth_routes.py`
- `web/src/state/__tests__/authSlice.test.ts`
- E2E (Playwright MCP): login → access protected page → logout.

---

## Risk register

- **R1. Token leak via SPA.** JWT in `localStorage` is XSS-readable. Use `httpOnly` cookies regardless of D2 choice for the browser.
- **R2. CSRF.** Cookie auth needs CSRF tokens or `SameSite=Strict`. Decide alongside D2.
- **R3. Backfilling `created_by_id` post-launch.** Existing rows attribute to `system`; switching them to a real user is non-trivial — handle as a migration when D6 is W5-first.
- **R4. Provider lock-in (D1=A).** Cognito user pool migration is painful. Mitigation: store IdP user_id in `users.idp_subject`, use Cognito user ID as the canonical key.
- **R5. Local-dev login.** Whatever the prod IdP, dev must work without the cloud. Add a `--dev-bypass` flag that mints a `local` user — but ensure it cannot ship to prod.

---

## Next step (PM action)

1. PM bundles D1–D6 into a single AskUserQuestion sweep.
2. PM rewrites this file with task-level red→green steps after sign-off, including the migration revision (if D1 forces one).
3. Dispatch order: db-specialist (migration) → api-developer (routes + dependency) → frontend-architect (UI) → devops-engineer (Cognito user pool / OAuth client config).

---

## Execution Handoff

**Status: NOT READY.** Decisions D1–D6 must land before this plan becomes executable.
