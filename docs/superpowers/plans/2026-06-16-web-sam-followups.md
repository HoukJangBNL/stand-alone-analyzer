# Web→SAM Path — Post-Integration Follow-ups

> **Status:** Parking lot of improvements deferred during the 2026-06-16 web→remote-GPU SAM bring-up. The core path WORKS end-to-end (web upload → backend defer on RDS → worker claims → S3 sync → 8-GPU fine-tuned SAM → masks). These are quality/UX/robustness items, NOT blockers. Do NOT touch a running job to apply these — schedule when idle.

> **For agentic workers:** each item below is independently scoped. Use brainstorming first for #1 (approach is genuinely open), then writing-plans. The rest are smaller and can go straight to subagent-driven-development.

---

## Background — how the path works now (context for whoever picks this up)

- Web "Run pipeline"/"Run SAM" → `POST /run/{pipeline,sam}` → `sam_dispatch.defer_sam_job` writes a per-scan manifest to `s3://qpress-uploads/{scan_prefix}manifest.json` and defers `run_sam` to the procrastinate `gpu` queue with `s3_prefix`.
- The GPU worker (EC2 g6e, AMI-baked code from GitHub `main`) claims the job, `_sync_scan_from_s3` downloads images by manifest key → local dir renamed to ix/iy filenames, then vendor `run_multi_process` fans out across 8 GPUs (spawn Pool), then `_upload_results_to_s3` pushes `07_sam/` back to S3.
- **Local-dev requires:** backend pointed at RDS (5433 bastion tunnel), scan data present in RDS (not just local PG), S3 scan prefix covered by worker IAM. See `docs/superpowers/plans/2026-06-15-web-scan-s3-sam-path.md` + this session's commits (`920fd93`..`decad22`).

---

## Item 1 — SAM step progress % (THE main ask) [needs brainstorming]

**Problem:** During the 8-GPU SAM step the web UI shows `0%` the whole time and looks frozen, even though the worker is actively processing (verified: masks accumulate in `07_sam/masks/<sha>/`, GPUs at 90-97%). Root cause: `core/pipeline/sam.py::_run_sam_multi_gpu` only calls `progress_callback` with `progress=0.0` at phase boundaries (`marker:model_load_start`, `starting N-GPU fan-out`, `routing:...`, `marker:processing_start`) and `1.0` at the end. Vendor `run_multi_process` uses a spawn `Pool` (vendor ~line 1113) that returns results only when ALL 8 children finish — no per-image progress flows back to the parent mid-run. So the SSE `progress` events carry 0.0 until the single terminal `completed`.

**Why it's not trivial:** the per-image work happens in spawned child processes that don't share state with the parent. Options (pick in brainstorming):
- **(a) Poll the output dir** — a thread/async task in the worker task counts completed images under `analysis_folder/07_sam/masks/*/` (one dir per image) every ~5-10s and emits `progress = done/total` via the existing `_emit_progress` NOTIFY path. Cleanest; no vendor change; the masks-on-disk are the ground truth (we already used this to track progress manually this session). Total = manifest image count.
- **(b) Vendor IPC** — have the spawn children report completions through a shared `mp.Queue`/`Manager`; parent drains it and emits progress. Requires patching vendor `run_multi_process` or wrapping it — heavier, touches the vendor submodule.
- **(c) Tail vendor stdout** — vendor logs `[N/3648]`-style lines per worker; parse them. Fragile (log-format dependent).

**Recommendation to validate in brainstorming:** (a) output-dir polling — minimal, robust, vendor-agnostic. The worker task already knows `analysis_folder` + total image count; spawn a polling coroutine alongside the `run_in_executor(run_multi_process)` call that emits `progress` NOTIFY frames until the executor returns.

**Acceptance:** web SAM step shows a climbing % (and ideally "N/3648 images") during the 8-GPU run, matching the masks-on-disk count, with no change to the vendor inference path or measurable slowdown.

**Files likely involved:** `src/flake_analysis/worker/tasks.py` (run_sam — add progress poller around the multi-gpu call), maybe `src/flake_analysis/core/pipeline/sam.py` (expose total/done hook). SSE wire format already supports `progress` (frontend `step_progress` reducer reads pct/msg). Frontend likely needs NO change.

---

## Item 2 — Frozen-UI recovery on dead/stale SSE [small]

**Problem:** Several times this session the UI showed a stuck SAM ⏳ from a prior attempt whose SSE stream died without a terminal event, while the backend had no active job. The pipeline state stayed `running`, disabling the Run button and looking frozen. (A separate cause was a stale HMR cache crashing the component — that's dev-only.)

**Fix:** On ComputeTab mount / scan change, reconcile UI state with reality — if there's no actual running job (query job status / the SSE opens and gets no live events), reset `pipelineState.phase` to idle so the Run button re-enables. Or add a visible "reset"/"reconnect" affordance. Keep the legit disable for a genuinely-running job.

**Files:** `web/src/hooks/usePipelineProgress.ts`, `web/src/pages/ComputeTab.tsx`.

---

## Item 3 — Surface swallowed background-task errors to SSE [small-medium]

**Problem:** When `_run_sam`'s S3 manifest upload hit an `AccessDenied` (dev/ prefix governance policy), the exception was swallowed in the `run_in_executor` closure — no SSE error, UI just froze at ⏳. We diagnosed it only via backend logs + S3 inspection.

**Fix:** Wrap the manifest-upload (and other background-task S3/DB ops) so failures emit a `pipeline_error`/`error` SSE terminal with a clear message instead of hanging silently. The user should SEE "manifest upload failed: AccessDenied" rather than a frozen spinner.

**Files:** `src/flake_analysis/api/routes/run_pipeline.py` (_run_sam), `src/flake_analysis/api/routes/run.py` (run_sam). NOTE: an agent drafted an "inline manifest fallback" for this that was REJECTED (it risked large procrastinate payloads § la §42, and didn't fix the mask-upload deny). The right fix is error-surfacing, not silent fallback.

---

## Item 4 — Local-dev environment hardening [small, docs + ops]

This session lost time to environment drift. Capture the lessons:
- **Backend must point at RDS for web→remote-GPU** (worker only sees RDS). Local PG causes a defer/claim split-brain (jobs land in local PG, worker on RDS never claims). Document the required env (`SAA_DB_*` → 5433 bastion tunnel) for the web-SAM dev flow, and that scan data must exist in RDS (not just local PG).
- **Single backend instance** — multiple stale `uvicorn` processes coexisted (port 8000 nondeterministic). A start script should kill-then-start, or check for an existing listener.
- **HMR caveat** — after frontend edits that remove symbols, a hard refresh (⌘⇧R) is needed; document in the dev runbook.
- Consider a `scripts/dev/start-web-sam-stack.sh` that wires backend→RDS + vite + verifies the bastion tunnel, superseding the local-PG `start-local-stack.sh` for the GPU-SAM flow.

**Files:** a dev runbook doc (e.g. `docs/dev-web-sam.md`), optionally a start script.

---

## Item 5 — `dev/scans/*` data + S3 governance decision [needs owner]

**Context:** scan 6 (`dev/scans/6/`) couldn't run because the S3 bucket policy denies PutObject to untagged principals under `dev/*` and `prod/*` (intentional dev/prod isolation), so the backend couldn't write manifest/masks there. We worked around it by testing with scan 51 (`scans/51/`, unrestricted prefix) and migrating its rows to RDS.

**Open question for owner:** how should dev-prefixed legacy scans (scan 6) be handled long-term? Options: tag the dev backend principal with `Env=dev` (policy allows dev→dev writes), or migrate/re-key legacy `dev/scans/*` data to the unrestricted `scans/*` prefix, or leave legacy dev scans read-only. Not urgent; scan 51 path works.

---

## Item 6 — Worker cold-boot RDS password race (T7s, pre-existing) [medium]

Observed again this session: a freshly-booted worker's first `procrastinate app.open_async()` sometimes fails psycopg auth (`password authentication failed`) → PoolTimeout → systemd restarts it 2-3× before it connects (~1 min). Known issue (handoff doc T7s — `worker.service After=cloud-final.service` cold-boot env-write race). Self-recovers but adds boot latency + noise. Fix is in the parking lot from the §43 acceptance close.

---

## Notes
- The end-to-end web→SAM run that's CURRENTLY executing (scan 51, job 31) validated the whole path live. Let it finish; capture its result (per_image_results.json → S3 `scans/51/07_sam/`) as the first real web-driven SAM output.
- None of these items should be applied to a running job. Schedule when the queue is idle.
