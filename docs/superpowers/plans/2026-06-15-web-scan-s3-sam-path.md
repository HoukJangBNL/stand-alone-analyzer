# Web-Scan → Worker S3-Sync SAM Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use subagent-driven-development to execute task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a web-uploaded scan (images in S3 under sha-named keys) run fine-tuned SAM on the remote GPU worker WITHOUT downloading images to the API host — the worker pulls from S3 directly, renames sha→original filename for grid parsing, runs SAM, and syncs masks back to S3.

**Architecture:** API generates a per-scan manifest (sha→filename+grid) and writes it to `s3://qpress-uploads/scans/{id}/manifest.json`, then defers `run_sam` with an `s3_prefix` arg (replacing the API-local `raw_images_dir`). The worker task syncs the prefix to a local per-run dir, renaming each object to its original ix/iy filename via the manifest, runs `run_sam_step`, then uploads `07_sam/` results back to `s3://.../scans/{id}/07_sam/`. The API exposes a results-read route. Local `hydrate.py` stays for the 4 in-process pipeline steps (thumbnails/background/domain_stats/domain_proximity) which run on the API host; SAM no longer uses it.

**Tech Stack:** FastAPI, procrastinate (gpu queue), boto3, SQLAlchemy 2.x async, EC2 g6e GPU worker (cloud-init), pytest.

**Key facts (verified):**
- S3 keys: `scans/{scan_id}/images/{sha256}.png` (3648 objects for scan 51 confirmed).
- DB `images` has `sha256`, `s3_uri`, `filename` (e.g. `ix025_iy025.png`), `grid_ix`, `grid_iy`.
- SAM grid parsing reads ix/iy from filename (`core/pipeline/sam.py:133-137` `_list_images`). SAM is self-contained — `flatfield_path=None`, no prior-step dependency (confirmed).
- Worker has boto3 (`pyproject.toml`), AWS creds via instance profile (`sam-iam-bootstrap.sh`), RDS access via procrastinate.
- Worker IAM currently scopes S3 to `internal/sam/*` only — `scans/*` NOT covered (BLOCKER, needs IAM update + owner approval, already granted).
- SAM masks were ephemeral in measure-run; web path needs them returned (S3 upload).

---

## Task 1: Worker IAM — add `scans/*` S3 access (AWS change, owner-approved)

**Files:**
- Modify: `scripts/aws/sam-iam-bootstrap.sh` (inline policy JSON, ~lines 66-91)

- [ ] **Step 1: Add a second policy statement for the scans prefix**

Add to the inline policy `qpress-sam-gpu-s3` (alongside the existing `internal/sam/*` statement) two statements — object RW on `scans/*` and bucket List scoped to `scans/*`:

```json
{
  "Sid": "ReadWriteScansPrefix",
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": "arn:aws:s3:::qpress-uploads/scans/*"
},
{
  "Sid": "ListScansPrefix",
  "Effect": "Allow",
  "Action": "s3:ListBucket",
  "Resource": "arn:aws:s3:::qpress-uploads",
  "Condition": {"StringLike": {"s3:prefix": ["scans/*"]}}
}
```

Keep the existing `internal/sam/*` statements unchanged (additive). Verify the script's S3_BUCKET default is `qpress-uploads`.

- [ ] **Step 2: Re-run the bootstrap to update the role policy (devops-engineer, owner-approved)**

Run `scripts/aws/sam-iam-bootstrap.sh` against the `qpress` profile to update the `qpress-sam-gpu-role` inline policy. This is an idempotent re-run that only changes the S3 policy document. Verify via `aws iam get-role-policy --role-name qpress-sam-gpu-role --policy-name <name>` that both `internal/sam/*` and `scans/*` resources are present.

Expected: policy includes `arn:aws:s3:::qpress-uploads/scans/*`. Note: existing running workers keep the old policy until relaunched; a fresh worker (booted by the next Run SAM) picks up the new one. No existing worker is running (verify).

---

## Task 2: SAM manifest generator service

**Files:**
- Create: `src/flake_analysis/api/services/sam_manifest.py`
- Test: `tests/api/services/test_sam_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# Given Image rows for a scan, generate_sam_manifest_for_scan returns a dict
# with version, scan_id, and images[] each carrying sha256/filename/grid_ix/grid_iy.
async def test_generate_manifest_maps_sha_to_filename(db_session):
    # seed a scan + 2 images (sha keys, ix/iy filenames)
    ...
    m = await generate_sam_manifest_for_scan(db_session, scan_id)
    assert m["scan_id"] == scan_id
    assert {i["filename"] for i in m["images"]} == {"ix001_iy002.png", "ix003_iy004.png"}
    assert all("sha256" in i and "grid_ix" in i for i in m["images"])
```

- [ ] **Step 2: Run it, confirm it fails** (`uv run pytest tests/api/services/test_sam_manifest.py -v`) — ImportError / function missing.

- [ ] **Step 3: Implement `generate_sam_manifest_for_scan`**

Query `images` for the scan ordered by id; return `{"version": 1, "scan_id": scan_id, "images": [{"sha256","filename","grid_ix","grid_iy"}, ...]}`. Pure read; no S3, no IO. Match the project's async-session + ORM patterns. Verify the exact `Image` field names against `src/flake_analysis/db/models`.

- [ ] **Step 4: Run test, confirm pass.**

- [ ] **Step 5: Commit** (`feat(api): SAM manifest generator (sha→filename+grid)`).

---

## Task 3: API run_sam — write manifest to S3 + defer with s3_prefix

**Files:**
- Modify: `src/flake_analysis/api/routes/run.py` (run_sam endpoint ~412-455, `_defer_sam_job` ~319-382)
- Test: `tests/api/` (the run_sam SSE / defer-payload test — find the existing one)

- [ ] **Step 1: Write/extend failing test for defer payload shape**

Assert that calling run_sam (with the defer + S3 client mocked) (a) writes a manifest to `scans/{id}/manifest.json`, and (b) defers `run_sam` with `s3_prefix=f"scans/{id}/"` and WITHOUT `raw_images_dir`. Mock boto3 S3 put_object + `app.tasks["run_sam"].defer_async`.

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Implement**

In run_sam: after auth/lock/usage/record_run_start, call `generate_sam_manifest_for_scan`, serialize to JSON, `boto3.client("s3").put_object(Bucket=SAA_S3_BUCKET, Key=f"scans/{scan_id}/manifest.json", Body=...)`. Change `_defer_sam_job` to accept + forward `s3_prefix` instead of `raw_images_dir`; update the `defer_async(...)` call to pass `s3_prefix=f"scans/{scan_id}/"` and drop `raw_images_dir`. Keep `analysis_folder`, `weights_path`, `device`. Keep the `ensure_scan_hydrated` call removed from the SAM path (SAM no longer needs API-local images) — but confirm whether run_sam still needs the manifest/analysis_folder stamped; if analysis_folder is still required for output pathing, keep that part. Do the S3 put off the event loop (run_in_executor) since it's sync boto3.

- [ ] **Step 4: Run test, confirm pass + existing run_sam tests still green.**

- [ ] **Step 5: Commit** (`feat(api): run_sam writes S3 manifest + defers s3_prefix`).

---

## Task 4: Worker task — sync from S3 (rename via manifest) + run + upload results

**Files:**
- Modify: `src/flake_analysis/worker/tasks.py` (run_sam signature ~106-114, body ~130-246)
- Test: `tests/worker/test_tasks.py`

- [ ] **Step 1: Write failing tests** (mock boto3 S3 + run_sam_step)

(a) `run_sam` with `s3_prefix` downloads manifest + each image, saving to local dir under the ORIGINAL filename (not sha); (b) after `run_sam_step`, results under `07_sam/` are uploaded to `scans/{id}/07_sam/`; (c) backward-compat: when `s3_prefix` is None but `raw_images_dir` is given (measure-run path), it still works (no S3 sync, no upload). Assert the local file written is `ix.../iy...png`, not the sha.

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Implement**

Change signature: add `s3_prefix: str | None = None`; keep `raw_images_dir: str | None = None` for measure-run backward-compat. Add `_sync_from_s3(s3_prefix, local_dir)` helper: `get_object(manifest.json)` → for each entry `download_file(f"{s3_prefix}images/{sha}.png", local_dir/filename)`. Bounded concurrency (ThreadPoolExecutor ~12). In the task body: if `s3_prefix`, sync to `/opt/sam/runs/{run_id}/raw_images/` and use that as the effective raw_images_dir; else use the passed `raw_images_dir`. After `run_sam_step` succeeds and `s3_prefix` is set, upload `Path(analysis_folder)/"07_sam"` recursively to `scans/{id}/07_sam/` (derive scan id / output prefix from s3_prefix). Keep the existing gpu_ready/markers/slim-completed-notify behavior. Bucket from `SAA_S3_BUCKET` env (default qpress-uploads).

- [ ] **Step 4: Run tests, confirm pass.**

- [ ] **Step 5: Commit** (`feat(worker): run_sam syncs S3 prefix (sha→filename) + uploads masks`).

---

## Task 5: API — results-read route for masks/summary

**Files:**
- Modify/Create: a route under `src/flake_analysis/api/routes/` (e.g. extend run.py or a results router)
- Test: `tests/api/`

- [ ] **Step 1: Write failing test**

`GET /scans/{scan_id}/sam/results` returns the per_image_results.json summary (read from `s3://.../scans/{id}/07_sam/per_image_results.json`), and a mask-fetch path returns a presigned URL (or proxies) for a given mask object. Mock S3.

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Implement** the read route(s): list/get from `scans/{id}/07_sam/` in S3; presign mask objects for the browser. Match existing presign patterns in scans.py. Keep auth dependency.

- [ ] **Step 4: Run test, confirm pass.**

- [ ] **Step 5: Commit** (`feat(api): SAM results-read route (S3-backed)`).

---

## Task 6: Integration verification (no full GPU run unless owner triggers)

- [ ] **Step 1:** Restart the local backend to load all changes (PM coordinates with owner — brief downtime). Verify `/api/v1/auth/me`, `/gpu/status`, materials all 200.
- [ ] **Step 2:** Verify manifest write path without launching GPU: call run_sam with the defer mocked OR confirm via unit tests that the manifest lands at `scans/{id}/manifest.json` and the defer payload carries `s3_prefix`. (Optionally do a real S3 put for scan 51's manifest and inspect it: `aws s3 cp s3://qpress-uploads/scans/51/manifest.json -`.)
- [ ] **Step 3:** Confirm worker IAM (Task 1) is live: a dry `aws s3 ls s3://qpress-uploads/scans/51/images/ --profile qpress` succeeds (PM already has access; the worker role gets it via Task 1).
- [ ] **Step 4:** Hand off to owner for the real Run SAM click (launches g6e, ~$9, ~95min for 3648 or use a small new scan). PM monitors the run (worker S3 sync log, gpu_ready, masks uploaded to S3, results route returns them).

---

## Notes / out of scope
- The 4 in-process pipeline steps keep using `hydrate.py` (API-local download) — unchanged here.
- Manifest is regenerated per run (idempotent); caching is a future optimization.
- Worker `/opt/sam/runs/{run_id}` cleanup left to instance idle-terminate (ephemeral).
- Full 5-step "Run pipeline" still needs the non-SAM steps' local hydrate to work end-to-end; this plan makes the SAM step (and single-step "Run SAM") work via S3. Full-pipeline E2E is a follow-up if the non-SAM steps reveal their own gaps.
