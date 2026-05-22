# W5 — Upload Flow (S3 Presigned URL + upload_sessions/items) Implementation Plan

> **Status: SUPERSEDED (2026-05-22).** D-block decisions resolved on 2026-05-22 and the executable plan was split into three task-level files:
> - `2026-05-22-W5-A-schema.md` — DB schema (materials + scans/images deltas)
> - `2026-05-22-W5-B-api.md` — FastAPI presigned PUT + scan lifecycle
> - `2026-05-22-W5-C-frontend.md` — React upload modal
> - `2026-05-22-W5-infra.md` — AWS S3 bucket + CORS + IAM
>
> Read this file only for original architectural intent / risk register. Do NOT execute.

> **For agentic workers (after sign-off):** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Stand up the end-to-end upload pipeline so a user can drag images into the React UI and have them land in S3 with their `(scan, upload_session, upload_item, image)` rows in Postgres. Today: zero upload UI, zero S3 wiring, `upload_sessions` / `upload_items` / `images` tables exist on RDS but are unused.

**Architecture (intent, not pinned):**
- **Browser → presigned PUT.** Client requests a presigned URL per file from the API; PUTs directly to S3; reports completion back. No bytes flow through FastAPI.
- **Server orchestrates rows.** `POST /upload/sessions` creates a `scans` row + `upload_sessions` row + N `upload_items` (status='pending'). `POST /upload/sessions/{sid}/items/{iid}/presign` issues a presigned URL and flips the item to 'uploading'. `POST /upload/sessions/{sid}/items/{iid}/complete` validates the SHA256, inserts the matching `images` row, flips the item to 'succeeded' and links `image_id`. Failures flip the item to 'failed' with `error`.
- **SHA256 computed client-side before upload.** That makes `images.UNIQUE(scan_id, sha256)` deduplication a precondition check on `presign`, not a post-hoc reconciliation.
- **Concurrency is per-session, not per-account.** Each session can run N parallel PUTs (default 4 — bounded by browser fetch concurrency). No global queue.
- **The UI is one drop-zone + per-file progress bars.** No multi-step wizard. The drop-zone lives at `/projects/{pid}/upload` (a new route).

**Tech Stack (intent):**
- API: FastAPI route group `/upload/*`, `boto3` S3 client (sync), pydantic schemas.
- DB: existing v6 tables (`scans`, `upload_sessions`, `upload_items`, `images`).
- Web: React route + dropzone (no new lib if `<input type="file">` + drag handlers suffice; `react-dropzone` if not), TanStack Query mutations, SubtleCrypto for SHA256.
- Tests: pytest + httpx + moto (S3 mock) for backend; Vitest + jsdom for UI; Playwright MCP for end-to-end smoke.

---

## Decisions Pending (block task-level write-up)

PM must resolve these with the user before this plan becomes executable.

### D1. S3 bucket layout + lifecycle

- Bucket: `qpress-uploads` (us-east-2)? Or per-env (`qpress-uploads-dev`, `qpress-uploads-prod`)?
- Key prefix scheme: `s3://<bucket>/scans/{scan_id}/images/{sha256}.{ext}` vs `scans/{scan_id}/{upload_session_id}/{filename}`?
- Lifecycle: do orphaned (presign-issued, never-completed) blobs get auto-purged after N days? IA tiering?
- KMS encryption: SSE-S3 (default) or SSE-KMS (`alias/aws/s3` vs custom CMK)?

**Open**: bucket name(s), key scheme, lifecycle rules, encryption mode. **Owner**: devops-engineer drafts → user approves AWS state changes.

### D2. SHA256 computation strategy

- **A. Client-side** (browser SubtleCrypto): blocks the UI on a 100MB file ~3–5s; deduplication is checkable BEFORE the PUT. Also makes `presign` idempotent.
- **B. Server-side via S3 event** (Lambda + `head_object` + `update upload_items`): no UI block, but dedup happens *after* the PUT is already paid for; complicates the lifecycle (orphan vs duplicate).
- **C. Hybrid** — client-side for files <50MB, server-side for larger.

**Open**: A vs B vs C. **Owner**: PM/user — UX and AWS-cost trade-off. Recommendation: A (uniform, simpler, no Lambda).

### D3. Upload session boundaries

- One session = one `scans` row, or can a session add files to an existing scan?
- Cap per session: total files / total bytes? (e.g. 10k files, 50GB)?
- Resumability: if the page refreshes mid-upload, can the user resume the same session?

**Open**: session-to-scan cardinality, hard caps, resume semantics. **Owner**: user.

### D4. Image metadata — manifest.json or per-file?

- v6 `upload_items` has `grid_ix`, `grid_iy`, `stage_x_um`, `stage_y_um`, `pixel_size_um` columns. Does the user upload a `manifest.json` alongside images (one POST, server parses), or does the client read EXIF/TIFF tags and send per-file?
- Where does `pixel_size_um` come from when neither manifest nor TIFF carries it?

**Open**: metadata source, fallback policy, validation rules. **Owner**: user + algo-engineer (pipeline preconditions).

### D5. Authn for uploads — `system` user only, or per-user?

- `created_by_id` columns on `upload_sessions` / `scans` exist. v1 uses the seeded `system` user only. Real users (W6) and uploads can ship in either order.
- If W5 ships first: every upload is attributed to `system`, the UI hides any "uploaded by" affordance.
- If W6 ships first: uploads carry `created_by_id` from the session.

**Open**: ordering W5 vs W6 (linked decision — see W6 plan).

### D6. Frontend route placement

- New route `/projects/:pid/upload` in `web/src/main.tsx`?
- Or modal that overlays any tab via `<UploadButton>` in the sidebar?

**Open**: route vs modal. **Owner**: user (UX preference).

---

## Sketch of File Structure (subject to D1–D6)

**New (backend):**
- `src/flake_analysis/api/routes/upload.py` — `POST /upload/sessions`, `POST /upload/sessions/{sid}/items/{iid}/presign`, `POST /upload/sessions/{sid}/items/{iid}/complete`, `GET /upload/sessions/{sid}` (status).
- `src/flake_analysis/api/services/s3.py` — boto3 wrapper for `generate_presigned_url('put_object', ...)`.
- `src/flake_analysis/api/schemas/upload.py` — pydantic for the four endpoints.
- `src/flake_analysis/api/services/upload_service.py` — DB write paths.

**New (frontend):**
- `web/src/api/upload.ts` — typed client.
- `web/src/state/uploadSlice.ts` — Zustand: per-file progress, errors, retries.
- `web/src/components/upload/Dropzone.tsx`
- `web/src/pages/UploadTab.tsx` (or `UploadModal.tsx` per D6).

**Tests:**
- `tests/api/test_upload_routes.py` (with `moto` for S3).
- `tests/api/test_upload_service.py` (PG-backed, `pytest.mark.pg`).
- `web/src/components/upload/__tests__/Dropzone.test.tsx`.

---

## Risk register (capture pre-execution)

- **R1. Orphan blobs.** Presigned PUT succeeds, `complete` never fires. Mitigation: D1 lifecycle rule + a daily reconciler job (out-of-scope follow-up).
- **R2. Duplicate `(scan_id, sha256)`.** UNIQUE constraint protects the table; presign must reject upfront when an `images` row already exists.
- **R3. Browser SHA256 cost.** Lock with D2.
- **R4. Presigned URL leak.** Short TTL (5 min). Single-use is enforced by S3 once consumed. Bucket policy refuses non-presigned writes.
- **R5. Backpressure.** N parallel PUTs per session — if the user drops 5000 files, the UI must batch (default 4 concurrent, queue rest).
- **R6. CORS on the bucket.** Required `PUT` + `Content-MD5` headers, origin = the prod app domain. Devops-engineer territory.

---

## Next step (PM action)

1. PM raises D1–D6 with the user (single message, AskUserQuestion bundles).
2. After answers: PM rewrites this file with the W3.5-grade task-level Plan (red→green TDD steps, exact file paths, exact code blocks).
3. Subagent dispatch: api-developer (routes + service), db-specialist (FK + transaction shape), devops-engineer (S3 bucket + CORS + IAM), frontend-architect (UI).

---

## Execution Handoff

**Status: NOT READY.** Do not invoke superpowers:subagent-driven-development on this file in its current state. The "Decisions Pending" block must be filled in first.
