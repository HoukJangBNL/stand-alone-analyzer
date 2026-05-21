# W8 — CI alembic drift check + SSM Session Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement Part 1 (CI drift check) task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Part 2 (SSM Session Manager) is **SKETCH + DECISIONS-PENDING** — will be expanded after the user resolves §"Decisions Pending (Part 2)".

**Goal (Part 1, executable):** Add a CI guard that fails any PR where `alembic` migrations have drifted from the SQLAlchemy ORM models (e.g., a model column added without a matching migration). The guard must work offline against an ephemeral Postgres in GitHub Actions — no RDS access from CI.

**Goal (Part 2, sketch):** Replace the bastion-EC2 + SSH-tunnel access pattern with AWS SSM Session Manager so the owner (and any future teammate) can reach RDS without distributing SSH keys or maintaining a public-IP bastion.

**Architecture (Part 1):**
- New CI job `alembic-drift` runs alongside `pytest`. It (a) spins up a clean Postgres service, (b) runs `alembic upgrade head` against it, (c) calls a Python helper that asks SQLAlchemy to compare `Base.metadata` to the live DB, (d) fails the build if any diffs are reported. The check is **read-only** — it never writes a migration; it just refuses to merge when drift exists.
- Test fixture pattern reused from `pytest.mark.pg` (W4.2). Same Postgres image, same env vars.

**Architecture (Part 2 intent, not pinned):**
- Stop the bastion EC2 (or keep it as cold-standby).
- Owner installs `session-manager-plugin` locally + uses `aws ssm start-session --target <ec2-or-rds-proxy>` to open a port-forward tunnel without inbound SSH.
- Either (a) keep bastion EC2 with SSM agent + remove SSH SG ingress entirely, or (b) replace bastion with RDS Proxy + SSM tunnel.

**Tech Stack:**
- CI: GitHub Actions, `services.postgres` matrix, `alembic` 1.13.x (already in `[dev]`), `sqlalchemy[asyncio]` 2.x.
- Drift detection: `alembic.autogenerate.compare_metadata` (NOT `--autogenerate` write — we use the comparison API directly to surface diffs without producing files).
- Tests: nothing new — the CI job IS the test.

**Pre-read:**
- `.github/workflows/test.yml` (current CI)
- `alembic/env.py` (async-aware, reads `flake_analysis.db.get_db_url`)
- `alembic.ini`
- `src/flake_analysis/db/__init__.py` (`Base`, `get_db_url`)
- `docs/db-ops.md` §3 (alembic ops)
- `docs/db-ops.md` §"future option" mention of SSM at the bottom

---

## Decisions Resolved (Part 1)

- **D1.1 Postgres image.** Use `postgres:17` (matches RDS engine version 17.4 per `db-ops.md`). Pin the minor on the GH Actions service.
- **D1.2 Drift surfacing.** Print the full diff (`alembic.autogenerate.compare_metadata` returns a list of operation tuples) to the job log AND `echo "::error::"` so it appears as a checks-tab annotation.
- **D1.3 What counts as drift.** ANY non-empty diff from `compare_metadata` fails the job — including index/comment/server-default differences, even though `--autogenerate` notoriously misses some of them. Conservative: false positives are a one-line migration; false negatives leak schema bugs.
- **D1.4 Branch policy.** Run on `pull_request` to `main` AND `push` to `main` (catches direct-push slip-ups).
- **D1.5 Allow-list.** No allow-list mechanism in v1. If a known-acceptable drift exists, the resolution is "write the migration", not "ignore the diff".

## Decisions Pending (Part 2)

### D2.1. Bastion replacement strategy

| Option | Pros | Cons |
|---|---|---|
| **A. Keep bastion EC2 + add SSM agent, remove SSH inbound** | Minimal change; same RDS connection path | Still pays for EC2 (`t4g.nano` ~$3/mo); two access methods to maintain in transition |
| **B. Replace bastion with RDS Proxy + SSM tunnel through a private SSM-only EC2** | Pooled connections; cleaner IAM | New AWS service ($/mo) + still needs an SSM target |
| **C. Direct VPC endpoint + SSM port-forward to RDS** | No EC2 at all | RDS doesn't natively accept SSM port-forward — needs an intermediate ENI; not really simpler than A |

**Recommendation**: A (incremental). Bastion stays, SSH inbound goes away, SSM gives access. Cheapest, least disruptive.

**Open**: A vs B vs C. **Owner**: user.

### D2.2. SSH access deprecation timeline

- Hard cutoff (delete `qpress-bastion.pem` + remove SG rule on day-X)?
- Soft cutoff (both methods work for 2 weeks, then SSH off)?

**Recommendation**: soft (2 weeks). Owner has muscle memory on `ssh -L`; abrupt switch invites self-lockout.

**Open**: timeline. **Owner**: user.

### D2.3. Teammate onboarding flow

- When a teammate joins, they need: AWS account access, IAM user with `ssm:StartSession` scoped to bastion target, `session-manager-plugin` on their machine.
- Document in `db-ops.md` §"team onboarding" (new section) — d2.3 is whether the user wants this NOW (greenlights the doc work) or DEFERRED until a teammate actually appears.

**Open**: scope of onboarding doc. **Owner**: user.

---

## File Structure (Part 1)

**New:**
- `scripts/check_alembic_drift.py` — Python entry point that runs `compare_metadata` and exits non-zero on diff.
- `.github/workflows/alembic-drift.yml` — GH Actions workflow (separate file to keep CI matrix clean).

**Modified:**
- None in v1. (We do NOT touch `.github/workflows/test.yml`. Drift is a separate workflow so a long pytest matrix doesn't mask the drift signal.)

**Tests (developer-facing):**
- `tests/scripts/test_check_alembic_drift.py` — exercises the diff helper against a known-clean and a known-dirty schema (`pytest.mark.pg`).

---

## Part 1 — Tasks (red→green)

### Task 1: Add `compare_metadata` helper script

**Files:**
- Create: `scripts/check_alembic_drift.py`
- Test: `tests/scripts/test_check_alembic_drift.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_check_alembic_drift.py
import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from scripts.check_alembic_drift import compute_drift


@pytest.mark.pg
async def test_compute_drift_clean(pg_engine):
    """A schema that exactly matches Base.metadata returns []."""
    from flake_analysis.db import Base
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    diffs = await compute_drift(pg_engine, Base.metadata)
    assert diffs == []


@pytest.mark.pg
async def test_compute_drift_extra_db_table(pg_engine):
    """An extra table in the DB but not in metadata is flagged."""
    from flake_analysis.db import Base
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("CREATE TABLE rogue (id INTEGER PRIMARY KEY)")

    diffs = await compute_drift(pg_engine, Base.metadata)
    assert any("rogue" in str(op) for op in diffs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scripts/test_check_alembic_drift.py -v -m pg`
Expected: FAIL with "module scripts.check_alembic_drift not found"

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/check_alembic_drift.py
"""Compare alembic-applied schema to SQLAlchemy ORM metadata.

Exit 0 on clean (no drift), 1 on drift. Used by CI.
"""
from __future__ import annotations
import asyncio
import sys
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from flake_analysis.db import Base, get_db_url


async def compute_drift(engine: AsyncEngine, metadata) -> list[Any]:
    """Return non-empty list when DB schema diverges from metadata."""
    def _compare(sync_conn) -> list[Any]:
        ctx = MigrationContext.configure(sync_conn)
        return list(compare_metadata(ctx, metadata))

    async with engine.connect() as conn:
        return await conn.run_sync(_compare)


async def main() -> int:
    engine = create_async_engine(get_db_url(async_driver=True))
    try:
        diffs = await compute_drift(engine, Base.metadata)
    finally:
        await engine.dispose()

    if not diffs:
        print("alembic drift check: CLEAN")
        return 0

    print("alembic drift check: DRIFT DETECTED")
    for op in diffs:
        print(f"  {op!r}")
        # GH Actions annotation
        print(f"::error::alembic drift: {op!r}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/scripts/test_check_alembic_drift.py -v -m pg`
Expected: PASS (both `test_compute_drift_clean` and `test_compute_drift_extra_db_table`).

- [ ] **Step 5: Commit**

```bash
git add scripts/check_alembic_drift.py tests/scripts/test_check_alembic_drift.py
git commit -m "feat(ci): add alembic drift detection helper"
```

---

### Task 2: Wire the helper into a CI workflow

**Files:**
- Create: `.github/workflows/alembic-drift.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/alembic-drift.yml
name: alembic-drift

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  drift:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: qpress
          POSTGRES_PASSWORD: qpress
          POSTGRES_DB: qpress
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U qpress"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      QPRESS_DB_HOST: localhost
      QPRESS_DB_PORT: "5432"
      QPRESS_DB_USER: qpress
      QPRESS_DB_PASSWORD: qpress
      QPRESS_DB_NAME: qpress
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install
        run: pip install -e ".[dev]"
      - name: Apply migrations
        run: alembic upgrade head
      - name: Check drift
        run: python scripts/check_alembic_drift.py
```

- [ ] **Step 2: Verify the workflow shape**

Run: `actionlint .github/workflows/alembic-drift.yml`
(If `actionlint` is not installed, skip — the gate is the actual CI run on the next push.)
Expected: no errors.

- [ ] **Step 3: Sanity-check env-var names match `flake_analysis.db.get_db_url`**

Run: `grep -n "QPRESS_DB_" src/flake_analysis/db/__init__.py`
Expected: shows the same `QPRESS_DB_HOST`/`QPRESS_DB_PORT`/`QPRESS_DB_USER`/`QPRESS_DB_PASSWORD`/`QPRESS_DB_NAME` keys this workflow sets. If the names differ, fix the workflow to match the helper, NOT the other way around.

> **Note for the implementer:** If `get_db_url` reads a different env-var convention (e.g., a single `DATABASE_URL`), simplify the workflow accordingly. Don't invent a parallel set of env vars.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/alembic-drift.yml
git commit -m "ci: add alembic drift workflow"
```

---

### Task 3: Update docs to reference the drift check

**Files:**
- Modify: `docs/db-ops.md` §3 (alembic ops) — add a one-liner that drift is now a CI gate.
- Modify: `docs/project-status.md` §3.2 — strike `CI: alembic offline render + 모델↔스키마 drift check`.

- [ ] **Step 1: Edit `docs/db-ops.md`**

Find the alembic section (search for `alembic upgrade head`) and append a paragraph:

```markdown
> **CI guard.** PRs against `main` run [`alembic-drift.yml`](../.github/workflows/alembic-drift.yml)
> which spins up an ephemeral Postgres, applies migrations, then calls
> `scripts/check_alembic_drift.py`. If a model change ships without a
> migration, CI fails before merge.
```

- [ ] **Step 2: Edit `docs/project-status.md`**

In §3.2 backlog, change:

```
- [ ] CI: alembic offline render + 모델↔스키마 drift check
```

to:

```
- [x] **W8 (Part 1)** CI: alembic 모델↔스키마 drift check — `.github/workflows/alembic-drift.yml`
- [ ] **W8 (Part 2)** SSM Session Manager 검토 (SSH key 배포 없이 DB 접근) — sketch in `docs/superpowers/plans/2026-05-21-W8-ci-drift-and-ssm.md`
```

- [ ] **Step 3: Commit**

```bash
git add docs/db-ops.md docs/project-status.md
git commit -m "docs: document alembic drift CI gate"
```

---

## Part 2 — Tasks (sketched, not executable)

After D2.1–D2.3 are resolved, this section will be rewritten with concrete tasks. The expected shape:

1. **devops-engineer**: install SSM agent on bastion (Amazon Linux 2023 ARM AMI ships with it — verify), attach `AmazonSSMManagedInstanceCore` IAM role, test `aws ssm start-session --target i-063165d449976b2e4`.
2. **devops-engineer**: test SSM port-forward — `aws ssm start-session --target i-063165d449976b2e4 --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{"host":["qpressdb.ch08y4ooqgmq.us-east-2.rds.amazonaws.com"],"portNumber":["5432"],"localPortNumber":["5432"]}'`.
3. **devops-engineer**: update `docs/db-ops.md` §2 — replace SSH tunnel section with SSM tunnel section. Keep the SSH section in an "Appendix: legacy bastion SSH" until D2.2 hard-cutoff.
4. **devops-engineer**: revoke SG ingress `sgr-014d8b085d17d950a` (port 22) on D2.2 cutoff date. **NEEDS USER APPROVAL** — irreversible without re-authorize.
5. **PM**: update `CLAUDE.md` §6 (Infrastructure quick reference) and `docs/db-ops.md` §1 inventory table with SSM-relevant ARNs.

**Risks (Part 2):**
- **R2.1 Self-lockout.** If SSM access fails (IAM misconfig, agent down) AND SSH ingress is gone, owner can't reach DB. Mitigation: keep SSH ingress until SSM is verified working at least 3 times.
- **R2.2 IAM drift.** SSM permissions ride on the EC2 instance role; if someone re-creates the bastion without the role, SSM silently won't work. Mitigation: detection runbook in `db-ops.md` ("if SSM fails, check role attachment first").
- **R2.3 Cross-region quirks.** SSM endpoints in `us-east-2` are reliable, but verify `session-manager-plugin` works on Mac ARM (owner's box).

---

## Risk register (Part 1)

- **R1.1 `compare_metadata` false positives.** Known: it can flag server-default formatting differences (`'now()'` vs `'NOW()'`). Mitigation: D1.3 says we accept the false-positive cost; if it bites in practice, the resolution is to align the server_default in the model, not to bypass the check.
- **R1.2 GH Actions Postgres image lag.** `postgres:17` is current, but if the major version on RDS bumps, this workflow gets stale silently. Mitigation: open a follow-up task to sync `postgres:` tag whenever `db-ops.md` engine version changes.
- **R1.3 Async engine in CI.** The script uses async; if `pytest-asyncio` config differs in CI vs local, the test could pass locally and fail in CI. Mitigation: the script's `__main__` calls `asyncio.run(main())` directly — no pytest-asyncio dependency for the production run path.

---

## Execution Handoff

**Status (Part 1): READY** for `superpowers:subagent-driven-development`.

**Status (Part 2): NOT READY.** Decisions D2.1–D2.3 must land before tasks are written.

**Dispatch order (Part 1):**
1. devops-engineer or api-developer: Tasks 1–2 (helper + workflow). Either can drive — devops-engineer if CI/IAM-adjacent; api-developer if the async/DB plumbing dominates.
2. PM: Task 3 (docs).
