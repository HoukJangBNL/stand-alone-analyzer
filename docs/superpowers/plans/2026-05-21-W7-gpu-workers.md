# W7 — GPU Worker Trigger (background → SAM → domain_stats → domain_proximity) Implementation Plan

> **Status: SKETCH + DECISIONS-PENDING.** Captures the architecture options for moving heavy compute (especially SAM inference) off the FastAPI process and onto GPU workers, with `runs` table as the audit log. PM must resolve §"Decisions Pending" with the user before this plan becomes executable.

> **For agentic workers (after sign-off):** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Replace the in-process `run_in_executor` pattern in `src/flake_analysis/api/routes/run.py` with a dispatch-and-poll model that pushes the four pipeline steps (`background`, `sam`, `domain_stats`, `domain_proximity`) onto GPU/CPU workers, tracks attempts in the `runs` table, and emits SSE progress to the SPA. Today: every step runs synchronously in the API container's thread pool; SAM has no implementation at all (no `pipeline/sam.py`, no route).

**Architecture (intent, not pinned):**
- **Job queue.** API enqueues a job, worker pulls. Three viable backends in D1: (a) SQS + EventBridge spot launch, (b) Postgres-backed job queue (e.g. `pgmq` extension or hand-rolled `runs` table polling), (c) ZMQ DEALER/ROUTER (lab-style, no AWS service). Recommend **(b) Postgres polling** for v1 — `runs` already exists, no extra service, easy to debug — with a path to (a) when scale demands.
- **Worker provisioning.** GPU steps (`sam`) need g6e.xlarge spot. CPU steps (`background`, `domain_stats`, `domain_proximity`) can run on the API container or a small CPU worker. Two GPU lifecycle models in D2: (a) always-on g6e (cheap when idle, expensive when 24/7), (b) on-demand spot launch (cold start ~3-5 min, ~70% cheaper).
- **`runs` is the audit log.** `runs(analysis_id, step, status, instance_type, instance_id, is_spot, started_at, completed_at, error, metrics)` is already on RDS. Worker INSERTs `running` on claim, UPDATEs `completed`/`failed` on finish. API polls the latest `runs` row for SSE progress.
- **Step orchestration.** Steps must run in order: `background → sam → domain_stats → domain_proximity`. Background re-run cascades (clears `steps_done.sam/domain_stats/domain_proximity`) per `db-schema-v6.md` §10 Convention #4. The orchestrator is server-side, not the user's responsibility — D3.
- **SSE progress.** Worker writes progress rows (`runs.metrics.progress`) every N seconds. API LISTENs (Postgres NOTIFY) or polls and forwards to the SSE channel — D4.
- **Per-image SAM failures.** Convention exists (`runs.metrics.per_image_failures`) but isn't enforced anywhere. Worker MUST populate this map (`{image_filename: error_string}`) per schema doc §10 Convention #6.

**Tech Stack (intent):**
- API: FastAPI + asyncpg LISTEN/NOTIFY for progress fan-out, or short-poll. Existing `ProgressBridge`/`sse_stream` reused.
- Worker: separate Python entry point (`flake_analysis.worker.gpu_runner`), shares `flake_analysis.core.pipeline.*` engines.
- AWS: EC2 spot (g6e.xlarge), IAM role for S3 + RDS access, EventBridge for spot interruption.
- Tests: `moto` + `testcontainers-postgres` for the queue layer; `pytest.mark.pg` for `runs` writes.

**Pre-read:**
- `src/flake_analysis/api/routes/run.py` (current in-process pattern)
- `src/flake_analysis/pipeline/{background,domain_proximity,domain_stats}.py` (no `sam.py` exists yet)
- `src/flake_analysis/core/pipeline/` (compute engines)
- `docs/db-schema-v6.md` §8 (runs table) + §10 (Conventions #4, #6)
- `src/flake_analysis/api/sse.py` (`ProgressBridge`)

---

## Decisions Pending

### D1. Job queue backend

| Option | Pros | Cons |
|---|---|---|
| **A. SQS + EventBridge** | AWS-managed, dead-letter queue, scales to 1000s of jobs | New AWS service; IAM sprawl; must duplicate state into `runs` |
| **B. Postgres polling on `runs`** | No new service; `runs` already exists; LISTEN/NOTIFY for low-latency dispatch | Worker count limited by PG connection pool; not great >100 jobs/min |
| **C. ZMQ DEALER/ROUTER** | Zero AWS dependency, lab-friendly | Hand-roll retry/visibility/DLQ; no audit trail outside `runs` |

**Recommendation**: B (Postgres polling) for v1 — fits current scale (~100K images/month per `db-schema-v6.md` header), reuses `runs`, no new AWS service. Migrate to A only if dispatch latency becomes a bottleneck.

**Open**: A vs B vs C. **Owner**: user (architectural lock-in).

### D2. GPU worker lifecycle

| Option | Pros | Cons |
|---|---|---|
| **A. Always-on g6e.xlarge** | ~3s job pickup; simple ops | $0.97/h on-demand × 730h = $700/mo idle; spot $0.30/h still ~$220/mo idle |
| **B. On-demand spot launch per job** | ~$0 idle; pay only for compute | 3-5 min cold start; spot interruption mid-job; needs EventBridge wiring |
| **C. Auto-scaling group, scale-to-zero** | Best of both — cheap idle, fast warm | Most complex; ASG + lifecycle hooks + spot fleet config |

**Recommendation**: B for v1 (cost dominates at low utilization; cold start acceptable for batch SAM). Migrate to C when interactive use grows.

**Open**: A vs B vs C. **Owner**: user (cost vs latency).

### D3. Orchestration: explicit step calls vs auto-chain

- **A. Explicit per-step calls** (current `run.py` pattern): UI calls `/run/background` → waits → calls `/run/sam` → ... User is the orchestrator.
- **B. Single `/run/pipeline` endpoint**: API enqueues all 4 steps with dependencies, worker chains them. UI sees one SSE stream covering all 4.
- **C. Hybrid**: keep per-step endpoints for re-runs, add `/run/pipeline` for first-time runs.

**Recommendation**: C — first-time uploads need the chain (UX win), but a re-run after a parameter tweak only wants the affected step.

**Open**: A vs B vs C. **Owner**: user + frontend-architect (UX shape).

### D4. Progress fan-out: poll vs LISTEN/NOTIFY

- **A. Short-poll `runs.metrics.progress`** every 1-2s from API → simple, slightly stale.
- **B. Postgres LISTEN/NOTIFY** on `run_progress` channel → real-time, but requires a dedicated asyncpg connection per active stream.

**Recommendation**: B if D1=B (since we're already on Postgres). A if D1=A.

**Open**: coupled to D1.

### D5. SAM model & weights distribution

- SAM checkpoint is ~2.5GB. Where does it live?
  - Worker AMI bake (immutable, fast cold start, AMI rebuild on weight update).
  - S3 + lazy download on cold start (mutable, +30-60s cold start).
  - EFS mount (shared, fast, +$0.30/GB-mo, +complexity).
- Per-LoRA fine-tuned weights (`models` table per `db-schema-v6.md` §3) — one S3 key per model, downloaded on demand?

**Open**: weight distribution strategy. **Owner**: devops-engineer drafts → user approves.

### D6. Spot interruption handling

- 2-min spot interruption notice → worker MUST: (a) flush current progress to `runs.metrics`, (b) set `runs.status = 'failed'` with `error = 'spot_interrupted'`, (c) re-enqueue itself (or let API retry on user click).
- D6: auto-retry vs surface to user?

**Recommendation**: surface to user — auto-retry hides cost from the user when they're triggering 50 jobs.

**Open**: auto-retry policy. **Owner**: user.

### D7. Per-image SAM failure policy

`db-schema-v6.md` §10 Convention #6 says per-image failures land in `runs.metrics.per_image_failures` JSONB, not as run-level failures. But the API/UI don't surface this anywhere. Two questions:
- **D7a**: failure-rate threshold above which the run as a whole is marked `failed`? (e.g. >10% of images failed?)
- **D7b**: does the UI need a "Failed images" panel, or is `runs.metrics` for ops-only?

**Open**: D7a threshold, D7b UI surface. **Owner**: user + algo-engineer.

---

## Sketch of File Structure (subject to D1–D7)

**New (worker):**
- `src/flake_analysis/worker/__init__.py`
- `src/flake_analysis/worker/gpu_runner.py` — long-running entry point, polls `runs` for `pending` rows, dispatches to step engine.
- `src/flake_analysis/worker/sam_step.py` — wraps `flake_analysis.core.pipeline.sam.run_sam` (currently MISSING — must be ported from Streamlit reference at `app/streamlit_app.py`).
- `src/flake_analysis/worker/lifecycle.py` — spot-interruption signal handler, graceful shutdown.

**New (backend):**
- `src/flake_analysis/api/services/job_dispatch.py` — INSERT into `runs(status='pending')`, NOTIFY (D1=B) or `boto3.sqs.send_message` (D1=A).
- `src/flake_analysis/api/routes/run.py` — REWRITE: replace `run_in_executor` with `enqueue_job` + SSE poll on `runs.metrics`.
- `src/flake_analysis/api/routes/run_pipeline.py` (D3=B/C) — `POST /projects/{pid}/run/pipeline` enqueues all 4 steps with dependency edges.

**New (frontend, only if D3=B/C):**
- `web/src/api/pipeline.ts` — single endpoint client.
- `web/src/components/run/PipelineProgress.tsx` — multi-step progress UI.

**New (devops):**
- `infra/worker-ami/packer.json` (D5=AMI bake) or `infra/worker-bootstrap.sh` (D5=S3 lazy).
- `infra/eventbridge-spot-launch.yaml` (D2=B).
- IAM policy: worker → RDS write `runs` + S3 read/write scan blobs.

**Tests:**
- `tests/worker/test_gpu_runner.py` — Postgres polling smoke test (`pytest.mark.pg`).
- `tests/worker/test_sam_step.py` — engine-level (mock SAM model).
- `tests/api/test_run_dispatch.py` — `runs` row inserted, SSE forwards `metrics.progress`.
- E2E (Playwright MCP, post-W6): trigger pipeline → see progress stream → result lands.

---

## Risk register

- **R1. SAM port from Streamlit.** The legacy `app/streamlit_app.py` references SAM but the new `flake_analysis/core/pipeline/` has no `sam.py` module. This is real implementation work, not just plumbing — port + parity test before integration.
- **R2. Spot interruption mid-SAM.** SAM on 1000 images can take 20-30 min. A spot interruption at min 25 wastes everything if the worker doesn't checkpoint. Mitigation: per-image checkpointing into `runs.metrics.per_image_completed` so re-launch resumes.
- **R3. Postgres LISTEN connection exhaustion (D4=B).** Each active SSE stream = 1 dedicated PG connection. 50 concurrent users = 50 connections. RDS `db.t4g.small` defaults to ~150 max — sufficient for v1, monitor.
- **R4. Worker IAM credential scope.** Worker writes `runs`, reads/writes S3 scan blobs. Must NOT have permission to `DROP TABLE` or write user/auth tables. Mitigation: dedicated worker IAM role, RLS on RDS.
- **R5. Idempotency of step re-runs.** If a worker dies after writing artifacts to S3 but before updating `runs`, the API may dispatch a duplicate. Mitigation: worker checks `runs.status` on claim and short-circuits if a `completed` row exists for the same `(analysis_id, step, params_hash)`.
- **R6. Local-dev story.** Without an EC2 worker, dev box must run the worker locally. Add `flake-analysis worker` CLI that polls the same RDS `runs` table — but ONLY if a `--dev-mode` flag is set, to prevent accidental "dev box claims prod jobs" disasters.
- **R7. SAM weights size.** ~2.5GB checkpoint × per-LoRA variants → AMI bloat or S3 download cost. D5 lock decides.

---

## Next step (PM action)

1. PM bundles D1–D7 into a single AskUserQuestion sweep (focus D1 + D2 + D3 — others can defer).
2. devops-engineer drafts AMI/IAM/spot-fleet config for user approval.
3. PM rewrites this file with task-level red→green steps, ordered:
   1. db-specialist: extend `runs` if needed (likely no schema change — the v6 columns suffice).
   2. algo-engineer: port SAM engine into `flake_analysis/core/pipeline/sam.py` + parity fixture.
   3. api-developer: `runs` write path + SSE rewrite.
   4. devops-engineer: worker AMI + spot launch + IAM.
   5. frontend-architect: pipeline progress UI (if D3=B/C).
4. **Order vs W5/W6**: W7 doesn't strictly block W5 (uploads attribute to `system` until W6) but it DOES block any "see results" UX milestone — uploads without compute are inert.

---

## Execution Handoff

**Status: NOT READY.** Decisions D1–D7 must land before this plan becomes executable. SAM port (R1) is the largest single piece of work — likely a sub-plan of its own once D-block is resolved.
