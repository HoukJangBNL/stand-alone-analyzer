# Upload Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the upload pipeline to a state where uploading 3648 real PNGs (9GB) and recovering from per-file failures both work end-to-end with clear status feedback.

**Architecture:** Five phases in priority order. Phase A surfaces what's happening (so the rest can be debugged). Phase B closes the highest-impact server bugs. Phase C unblocks the post-upload Compute tab. Phase D handles draft / partial-state semantics. Phase E is a real-data verification loop with mixed success/failure scenarios.

**Tech Stack:** FastAPI + asyncpg + boto3 + AWS S3 (us-east-2, bucket `qpress-uploads`); React 18 + TanStack Query + Zustand; pytest + vitest.

---

## Inputs from research (2026-05-26)

Three concurrent researcher reports:

- **Backend** (`a54d8674796e5b6ea`): presign retry of same sha256 returns 409 (no resume); orphan `UploadSession`/`UploadItem` never cleaned; finalize doesn't flip scan-ready state; raw `HTTPException(detail=...)` bypasses ErrorEnvelope; failed S3 PUT leaves UploadItem PENDING forever.
- **Frontend** (`a2a3f2f673da358f9`): 200-row cap hides failures past row 200; no aggregate (X done / Y running / Z failed of N) counter; modal close discards `scan_id`; `(N imgs)` label is the planted expected count not actual; **Compute Tab 404 confirmed** — `StepCard.tsx:11` drops the `scanId` prop, hook calls `useStepProgress(pid, step)` without scan id, URL has no `/scans/{sid}` segment; cancelAll's `cancelled` flag is never reset; concurrency 4 hardcoded with no backoff; `request_id` captured but never displayed.
- **Infra** (`a2914e8a3924d6027`): real AWS S3 (`qpress-uploads`, us-east-2); CORS already covers PUT + `x-amz-checksum-sha256`; presign TTL hard-coded **300s**; 2GB per-file cap; `complete` calls **synchronous** boto3 `head_object` on the event loop; DB pool 5+5; **no module loggers in upload paths**; orchestrator is lazy-presign per slot (so TTL is OK at concurrency 4).

Real test data: `/Volumes/QPressDataShare/data/test_data/.../EE5A8OD5/rawImages` — 3648 PNGs, 1920×1200, ~2.5 MB/ea, ix###_iy### naming. There's also `captureImages/{G,Thumbnails,Zoomed}` subdir which must NOT be uploaded as raw scan input.

---

## Phase A — Visibility (must come first, others depend on this for debugging)

### Task A1: Aggregate counter in UploadModal

**Files:**
- Modify: `web/src/components/upload/UploadModal.tsx`
- Test: `web/src/components/upload/__tests__/UploadModal.test.tsx`

- [ ] **Step 1: Write failing test** — render UploadModal, populate store with 5 files (2 done, 1 uploading, 1 failed, 1 queued), assert `data-testid="upload-modal-counts"` shows `2 done · 1 uploading · 1 failed · 1 queued of 5`.

- [ ] **Step 2: Implement** — derive `{done, uploading, failed, queued, total}` from `useUploadStore` order/files; render a single line above the action buttons.

- [ ] **Step 3: Commit** — `feat(web): add aggregate upload counter`

### Task A2: Failed-only filter in ProgressList

**Files:**
- Modify: `web/src/components/upload/ProgressList.tsx`
- Test: `web/src/components/upload/__tests__/ProgressList.test.tsx` (create if absent)

- [ ] **Step 1: Failing test** — populate store with 250 files (rows 0..199 queued, rows 200..249 failed). Render. Assert default view shows truncation. Click `data-testid="progress-list-failed-only"` toggle; assert all 50 failed rows are now visible (regardless of 200 cap), `<= 200` queued rendered.

- [ ] **Step 2: Implement** — local `failedOnly` state. When on, filter `order` to `files[uid].status === 'failed'` first, then apply 200 cap to that filtered list. Hide truncation message when filtered list size ≤ 200.

- [ ] **Step 3: Commit** — `feat(web): failed-only toggle in ProgressList`

### Task A3: Show ApiError request_id in FileRow

**Files:**
- Modify: `web/src/state/uploadSlice.ts` (add `request_id: string | null` to UploadFile)
- Modify: `web/src/lib/uploadOrchestrator.ts` (extract `request_id` in catch)
- Modify: `web/src/components/upload/FileRow.tsx` (render in error state)
- Modify/Add tests for orchestrator + FileRow

- [ ] **Step 1: Failing test (orchestrator)** — mock `presignImage` to throw `new ApiError(500, 'x', 'boom', null, 'req-123')`; run orchestrator; assert `files[uid].request_id === 'req-123'` and `files[uid].error.includes('boom')`.

- [ ] **Step 2: Implement** — `(e as { request_id?: string })?.request_id` into store patch.

- [ ] **Step 3: FileRow render test** — when `file.error && file.request_id`, FileRow renders `req-123` next to error text in a `<small data-testid={'file-row-reqid'}>` element.

- [ ] **Step 4: Commit** — `feat(web): expose request_id on failed upload rows`

### Task A4: Backend module logger + log presign/complete failures

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (top-level `logger = logging.getLogger(__name__)`; log each 4xx/5xx with `extra={'request_id': ..., 'scan_id': ..., 'sha256': ...}`)
- Modify: `src/flake_analysis/api/services/upload_service.py` (logger; log presign-side failures)

- [ ] **Step 1: Failing test** — using `caplog`, assert that a 409 sha256 collision in presign emits an `INFO` log with `extra={'event': 'presign_collision_sha256', 'scan_id': ...}`.

- [ ] **Step 2: Implement** — add logger; emit structured logs at the existing 409/404/500 points. Don't change response shape.

- [ ] **Step 3: Commit** — `feat(api): structured logs on upload failures`

---

## Phase B — Server reliability

### Task B1: Presign idempotency (same sha256 returns existing upload_item_id)

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (the in-flight upload_items collision branch)
- Test: `tests/api/routes/test_scans_presign.py` (or wherever presign tests live — search first)

- [ ] **Step 1: Failing test** — call presign twice with the same `(scan_id, filename, sha256, ix, iy, size)`; assert second call **returns 200 with the SAME `upload_item_id`**, not 409.

- [ ] **Step 2: Implement** — when the in-flight upload_items lookup hits a row whose `(filename, ix, iy, size_bytes)` also match, regenerate the presigned URL for the existing object key and return the existing `upload_item_id`. Only return 409 when fields actually disagree.

- [ ] **Step 3: Test the disagreement case** — same sha256 but different ix/iy → still 409.

- [ ] **Step 4: Commit** — `fix(api): idempotent presign for same (sha256, filename, grid)`

### Task B2: Startup check for SAA_S3_BUCKET

**Files:**
- Modify: `src/flake_analysis/api/main.py` or `app.py` (find startup hook)
- Test: `tests/api/test_startup.py`

- [ ] **Step 1: Failing test** — instantiate the app with `SAA_S3_BUCKET` unset (monkeypatch); assert app construction raises `RuntimeError` with a clear message before any request.

- [ ] **Step 2: Implement** — read settings at app startup; raise immediately if S3 bucket is missing.

- [ ] **Step 3: Commit** — `fix(api): fail fast when S3 bucket is unconfigured`

### Task B3: Move complete()'s head_object off the event loop

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (in `complete_image`)

- [ ] **Step 1: Test** — existing complete tests should keep passing. Add: under `asyncio.timeout(0.1)`, four parallel `complete` calls should not block each other beyond the head_object latency budget. (Skip if too slow to write — at minimum confirm `await loop.run_in_executor(None, head_object, ...)` is used.)

- [ ] **Step 2: Implement** — wrap the `s3_client.head_object(...)` call in `await asyncio.get_event_loop().run_in_executor(None, lambda: s3_client.head_object(...))`.

- [ ] **Step 3: Commit** — `perf(api): non-blocking head_object in complete`

### Task B4: cancelAll resets `cancelled` flag

**Files:**
- Modify: `web/src/lib/uploadOrchestrator.ts` (drop the bare `cancelled` flag; instead have `runAll()` create a fresh worker generation, or reset flag at the start of `runAll()` / `retry()`)
- Test: `web/src/lib/__tests__/uploadOrchestrator.test.ts`

- [ ] **Step 1: Failing test** — start runAll on 3 files, cancelAll mid-way (so 1 done + 2 stuck queued), then call `runAll()` again on the same orchestrator. Assert remaining files complete (don't immediately exit).

- [ ] **Step 2: Implement** — reset `this.cancelled = false` at the top of `runAll()` (idempotent) and `retry()`.

- [ ] **Step 3: Commit** — `fix(web): cancelAll no longer permanently halts the orchestrator`

### Task B5: Centralize presign TTL constant

**SSoT motivation:** the 300s TTL currently lives as a magic number in two locations (`upload_service.py` presign call + frontend retry-on-403 calculation). Single constant, single source.

**Files:**
- Modify: `src/flake_analysis/api/services/upload_service.py` (extract module-level `PRESIGN_TTL_SECONDS = 300`)
- Modify: any other reference (search `300` in upload paths)
- Test: `tests/api/services/test_upload_service.py` — assert presign call uses the named constant.

- [ ] **Step 1: Failing test** — assert that `upload_service.PRESIGN_TTL_SECONDS == 300` and that `generate_presigned_url` is called with `ExpiresIn=PRESIGN_TTL_SECONDS`.

- [ ] **Step 2: Implement** — extract constant, replace literals.

- [ ] **Step 3: Commit** — `refactor(api): single PRESIGN_TTL_SECONDS constant`

### Task B6: Unify error envelope (raw HTTPException → ErrorEnvelope)

**SSoT/SRP motivation:** routes currently mix `raise HTTPException(detail=...)` with `ErrorEnvelope`-shaped responses. Frontend ApiError parser can extract `request_id` only from the structured shape — bare HTTPException paths drop it. **This unblocks A3** (request_id display) for ~half the failure modes.

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (replace every `raise HTTPException(...)` with the project's `raise_envelope_error(...)` helper or equivalent — read existing helpers first)
- Modify: `src/flake_analysis/api/routes/projects.py`, `routes/run.py` (same audit)
- Test: `tests/api/routes/test_error_envelope.py` (or extend existing) — for each previously-raw path, assert response body shape matches ErrorEnvelope (`{error: {code, message, request_id, ...}}`).

- [ ] **Step 1: Audit** — `grep -rn "raise HTTPException" src/flake_analysis/api/routes/` and list every site. Catalog which ones already emit envelope vs which are raw.

- [ ] **Step 2: Failing test** — pick one raw path (e.g., presign 404 scan_not_found); assert response is envelope-shaped with `request_id` populated.

- [ ] **Step 3: Implement** — convert raw paths to envelope helper. Keep status codes identical.

- [ ] **Step 4: Repeat for remaining sites**, batched in 1–2 commits.

- [ ] **Step 5: Commit** — `refactor(api): all upload-path errors use ErrorEnvelope`

---

## Phase C — Unblock Compute Tab (404 fix)

### Task C1: StepCard pipes scanId to useStepProgress; hook uses scan-scoped URL

**Files:**
- Modify: `web/src/components/StepCard.tsx` (destructure `scanId` and pass through)
- Modify: `web/src/hooks/useStepProgress.ts` (accept `scanId` and forward)
- Modify: `web/src/api/sseRun.ts` (`/api/v1/projects/{pid}/scans/{sid}/run/{step}` if backend route demands it — verify against `routes/run.py` first)
- Tests: `web/src/components/__tests__/StepCard.test.tsx`, `web/src/hooks/__tests__/useStepProgress.test.ts`

- [ ] **Step 1: Verify backend route shape** — read `src/flake_analysis/api/routes/run.py` to confirm whether the route is `/projects/{pid}/scans/{sid}/run/{step}` or scan-less. If scan-less but step needs scan, the backend also needs an update (note here, then split into a separate task).

- [ ] **Step 2: Failing test** — render StepCard with `scanId={42}`; assert the fetched URL contains `/scans/42/`.

- [ ] **Step 3: Implement** — wire `scanId` end-to-end through StepCard → useStepProgress → sseRun.

- [ ] **Step 4: Commit** — `fix(web): pass scanId through StepCard so Compute Run hits the right URL`

---

## Phase D — Draft / partial-state semantics

### Task D1: Backend list_scans returns uploaded_count + status

**SSoT decision (locked):** `Scan.image_count` = the *intended* cell count (planned grid size, set at scan creation, immutable). `uploaded_count` = the *actual* number of completed Image rows (derived/joined, never stored as a column). `Scan.status` ∈ {`'draft'`, `'ready'`} is the readiness flag. Finalize transitions `draft → ready` only when `uploaded_count == image_count`. **No third "expected count" column exists anywhere.**

**Files:**
- Modify: `src/flake_analysis/api/routes/scans.py` (list endpoint at `:76-101`)
- Modify: `src/flake_analysis/api/schemas/upload.py` (`ScanSummary` adds `uploaded_count`, `status` of `'draft' | 'ready'`)
- Migration: alembic `add_scan_status` (default `'draft'` for existing rows; backfill `'ready'` where `uploaded_count == image_count` at migration time — query at migration time)
- Test: `tests/api/routes/test_scans_list.py`

- [ ] **Step 1: Failing test** — create a scan with image_count=10, upload 3 images, hit list endpoint; assert response has `uploaded_count: 3, status: 'draft'`. Then finalize after uploading remaining 7; re-list; assert `status: 'ready'`.

- [ ] **Step 2: Implement** — at finalize, set `Scan.status = 'ready'` (add column via alembic migration). At list time, JOIN-count Image rows for `uploaded_count`.

- [ ] **Step 3: Commit** — `feat(api): list_scans exposes uploaded_count + status`

### Task D2: ScanPicker shows "X of Y · draft/ready"

**Files:**
- Modify: `web/src/components/scans/ScanPicker.tsx:81`
- Modify: `web/src/api/upload.ts` (ScanSummary fields)
- Test: `web/src/components/scans/__tests__/ScanPicker.test.tsx`

- [ ] **Step 1: Failing test** — feed list mock with `{uploaded_count: 3, image_count: 10, status: 'draft'}`; assert option label reads `s-name (3/10 · draft)`.

- [ ] **Step 2: Implement** — change the label template.

- [ ] **Step 3: Commit** — `feat(web): truthful scan picker label`

### Task D3: Modal close while running prompts confirm + preserves scan_id for resume

**Files:**
- Modify: `web/src/components/upload/UploadModal.tsx`
- Test: `web/src/components/upload/__tests__/UploadModal.test.tsx`

- [ ] **Step 1: Failing test** — start upload, click Close mid-run; assert a confirmation appears; on confirm, assert `scanId` is NOT cleared (so user can re-open later — though the actual resume UX is a separate task).

- [ ] **Step 2: Implement** — on close while `running`, show a `window.confirm`-equivalent inline prompt; if user confirms, abort but keep `scanId` and the failed/queued files in store cleared (those are throwaway). Successfully-uploaded items remain server-side.

- [ ] **Step 3: Commit** — `feat(web): modal close confirms during upload, preserves scan_id`

### Task D4: Retry all failed button

**Files:**
- Modify: `web/src/components/upload/UploadModal.tsx`
- Modify: `web/src/lib/uploadOrchestrator.ts` (add `retryAllFailed()` helper)
- Test: orchestrator test + UploadModal test

- [ ] **Step 1: Failing test** — populate 3 files all `failed`; click `data-testid="upload-modal-retry-all"`; assert all three are picked up and run again.

- [ ] **Step 2: Implement** — orchestrator method patches all failed rows to `queued` + clears errors, then runs `runAll()`.

- [ ] **Step 3: Commit** — `feat(web): retry all failed`

### Task D5: Materials cache invalidation after material create

**SSoT motivation:** ScanForm reads materials from a cached query; creating a new material doesn't invalidate, so the dropdown stays stale until reload.

**Files:**
- Modify: wherever `createMaterial` mutation lives (likely `web/src/api/materials.ts` consumer in ScanForm)
- Test: existing ScanForm test or new — after creating material, the new option appears in dropdown without page reload.

- [ ] **Step 1: Failing test** — render ScanForm; trigger create-material flow; assert new material is selectable in the dropdown immediately.

- [ ] **Step 2: Implement** — `qc.invalidateQueries({queryKey: ['materials', projectId]})` on material-create success.

- [ ] **Step 3: Commit** — `fix(web): refresh materials list after create`

---

## Phase E — Real-data verification loop

Run **after** Phases A–D land. Each scenario produces a checked outcome. Run from a fresh `saa_test`-like DB (or dedicated dev project). Backend must be running; bucket `qpress-uploads` is real AWS — DO NOT spam it.

### Scenario E1: Smoke (1 file)
- [ ] Create new project + new scan
- [ ] Drag in 1 PNG from data folder
- [ ] Start upload → expect `1 done`, finalize green, ScanPicker shows `1/1 · ready`

### Scenario E2: Happy path (10 files)
- [ ] 10 PNGs from `ix000_iy000..ix000_iy009`
- [ ] Expect `10 done`, finalize green, then **Compute → Thumbnails Run** → expect non-404

### Scenario E3: Mixed failure (10 files + 1 corrupt)
- [ ] Take a real PNG, truncate to 100 bytes (simulated corruption); add to drop set
- [ ] Expect 10 done + 1 failed; aggregate counter shows `10 done · 1 failed of 11`
- [ ] Click retry on the bad row → still fails (corrupt); error visible with request_id
- [ ] Finalize blocked (server returns 409 short-by-1) — verify error envelope matches expectation
- [ ] Remove failed row, finalize → green

### Scenario E4: Cancel + resume (50 files)
- [ ] Drop 50 files; start; halfway through (~25 done), click Cancel
- [ ] Some queued; click Start again; after B4 fix, remaining should finish
- [ ] Aggregate counter accurately tracks across resume

### Scenario E5: Modal-close + reopen (20 files)
- [ ] Drop 20 files; start; close mid-run (D3 confirm); reopen modal — currently regenerates new scan_id (expected for now); successfully-uploaded files remain server-side
- [ ] In ScanPicker, draft scan visible with `M/20 · draft`

### Scenario E6: Full stress (3648 files / 9 GB)
- [ ] **Owner approval required before this scenario** (real S3 spend, ~9 GB). Cost estimate: PUT × 3648 × $0.005/1000 ≈ $0.02 + 9 GB × $0.023/GB-month storage on the day = a few cents — but confirm with owner first.
- [ ] Drop entire `rawImages/*.png` set (must NOT include `captureImages/` subfolder)
- [ ] Expected wall time at concurrency 4: ~15–25 min depending on home network
- [ ] Watch counter; at end `3648 done · 0 failed`
- [ ] Finalize → ready
- [ ] Compute Tab → Thumbnails Run on this scan should not 404

### Scenario E7: Idempotent retry stress (3648, simulated mid-flight failure)
- [ ] Drop 3648 PNGs; start; physically yank wifi for 30s mid-run; reconnect
- [ ] Some rows fail with network errors; click "retry all failed" (D4)
- [ ] Expect convergence to 3648 done with NO duplicate Image rows server-side (B1 idempotency proves this)

---

## Out-of-scope (note for follow-up)

- Backend orphan cleanup job (UploadSession state transitions on close, scheduled cleanup of stale PENDING items) — listed in research but defer until usage shows it matters.
- Multipart / parallel-part uploads — files are 2.5 MB, not needed.
- Server-side SSE for upload progress — purely client-driven is fine for now.
- Dynamic concurrency tuning — concurrency 4 covers the 9 GB case in ~15 min; revisit if users push 100 GB.
- **SRP refactor of `routes/scans.py`** (split presign/complete/list/finalize/orchestration into separate modules + service layer) — pure restructure with zero behavior change. Tracked separately in `docs/superpowers/plans/post-1차목표-routes-srp-refactor.md` (to be written after 1차목표 lands). Keeping it out of this plan to avoid regression risk on the path to a working upload.
- Other LOW/MED SSoT findings (F3 list cache consistency, F4 FK ordering, F5/F7/F10 naming) — collect into the same post-1차목표 refactor plan.

---

## Self-review notes

- **Spec coverage**: every HIGH severity finding in the three research reports is mapped to a Phase A–D task. MEDs that don't directly impact 1차목표 (request_id display, head_object blocking, retry-all) are also covered. LOWs (per-file size cap, IntegrityError leak text) deferred.
- **Type consistency**: `ScanSummary` adds 2 fields used in both ScanPicker and `.../scans` list response — wired in same task (D1+D2 must land together).
- **Build sequence**: A is a no-op semantically (just visibility) → safe first. B1 is a server change but is backward-compatible (existing 409 path narrows to a strict subset). C1 may surface a second backend bug if the run route is scan-less; handle as it appears. D1 needs an alembic migration (small).
