# SAM 8-GPU Multi-Process Parallelization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Scope:** Measurement-only. We adapt the existing single-GPU `run_sam` procrastinate task to fan out to 8 worker processes (one per GPU) when `torch.cuda.device_count() >= 2`, run it once on a 3648-image scan on a `g6e.48xlarge` spot instance, and record wall-time. **No new pytest is added** — full TDD red/green ritual is skipped because this is a one-shot wall-time measurement, not a long-lived prod feature. Tests come later if we promote it.
>
> **Goal:** Drive per-image wall-time from the measured 3.98 s/img (g6e.xlarge, 1× L40S) toward ideal ~0.50 s/img on g6e.48xlarge (8× L40S), bringing a 3648-image scan from ~4 hr to ~30 min.
>
> **Architecture:** Two-layer change inside the GPU worker only — (1) a hardware-gated branch in our SAM core adapter that, when ≥2 GPUs are visible, calls vendor's existing `run_multi_process(images, output_dir, config, num_gpus)` from `vendor/QPress-SAM-Flake/run_amg_v2.py` instead of the single-GPU `run_amg_v2_inference.infer`; (2) a `g6e.48xlarge`-capable launch path in `scripts/aws/sam-launch-template.sh` driven by `INSTANCE_TYPE` env override, reusing the existing AMI (`ami-0b7ec5ff47a1eff11`), SG, IAM, subnet, and user-data. Procrastinate worker stays `concurrency=1`; the 8-way fan-out happens inside one task invocation via `mp.get_context("spawn")`.
>
> **Tech Stack:** Python `multiprocessing` (spawn ctx, already used by vendor), PyTorch CUDA, procrastinate (no changes), AWS EC2 launch templates.

---

## Background — what's already in place

- **Vendor `run_amg_v2.py` already implements the multi-GPU pool.** `run_multi_process` (lines ~1069–1149) splits the image list across `num_gpus` workers, uses `mp.get_context("spawn")` for CUDA-safe forking, pins each child to a GPU via `worker_id == gpu_id`, shares progress through `manager.Value("i", 0) + manager.Lock()`, and returns a single combined `List[Dict]` sorted by `image_id`. Per-worker function `worker_process_images` (lines ~1010–1066) loads the SAM2 model **once per child process**, then iterates its slice of images. Result merging is done by `run_multi_process` itself — `pool.apply_async(...).get()` collects each worker's `List[Dict]` and `all_results.extend(...)` flattens them. **We do not write a spawn pool.**
- **Vendor original config-loading uses `config["sam2_repo"] / config["config_yaml"] / config["checkpoint"]`** — *not* the dict-config form that our `run_amg_v2_inference.py` line 54 patches around. So the hot-patch we apply for inference does **not** carry over; verify once during implementation and skip if confirmed.
- **Our adapter** `src/flake_analysis/core/pipeline/sam.py` currently calls only `run_amg_v2_inference.infer(...)` (single-GPU). The pipeline wrapper `src/flake_analysis/pipeline/sam.py` and the procrastinate task `src/flake_analysis/worker/tasks.py::run_sam` both stay byte-identical from the API caller's POV — the multi-GPU branch is fully internal to the core adapter.
- **Procrastinate worker** (`src/flake_analysis/worker/__main__.py`) runs `concurrency=1` on queue `gpu`. One task at a time, no change.
- **AMI `ami-0b7ec5ff47a1eff11`** ships the vendor submodule including `run_amg_v2.py`, the merged `.pt`, and CUDA-12 PyTorch — already 8-GPU-capable from the OS side.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/flake_analysis/core/pipeline/sam.py` | **EDIT.** Adds the `device_count() >= 2` branch that delegates to a new helper `_run_sam_multi_gpu` calling vendor `run_amg_v2.run_multi_process`. The existing `_vendor_infer` single-GPU path is preserved unchanged. |
| `vendor/QPress-SAM-Flake/run_amg_v2.py` | **READ-ONLY.** Vendor — call `run_multi_process(...)` from it. Do not edit. |
| `vendor/QPress-SAM-Flake/run_amg_v2_inference.py` | **READ-ONLY.** Single-GPU path stays as-is. |
| `src/flake_analysis/pipeline/sam.py` | **NO CHANGE.** Wrapper signature stable. |
| `src/flake_analysis/worker/tasks.py` | **NO CHANGE.** Procrastinate task signature stable. |
| `scripts/aws/sam-launch-template.sh` | **EDIT.** Pass `INSTANCE_TYPE` env override through to the launch-template version (already plumbed at line 36 — verify the env path produces a usable `g6e.48xlarge` template version on AWS). |
| `docs/sam-ops.md` | **EDIT (small).** Add a "Measurement: 8-GPU run" section documenting the launch + defer + collect commands. |

---

## Architecture Decisions

**AD1 — Hardware gate, not config flag.** Branch on `torch.cuda.device_count() >= 2` inside the core adapter. No new schema field, no new env var, no UI exposure. The same procrastinate task on a 1-GPU host runs single-GPU; on an 8-GPU host runs multi-GPU. Removes any chance of a misconfigured worker silently underutilizing 7 GPUs.

**AD2 — Reuse vendor `run_multi_process` verbatim.** It already does spawn-pool, GPU pinning, shared progress counter, and per-image-id ordering. Re-implementing would duplicate ~80 lines of subtle CUDA-spawn logic for zero gain.

**AD3 — Each child loads `merged.pt` independently.** Vendor's `worker_process_images` calls `build_sam2(...)` per child after spawn (CUDA forking with a pre-loaded model is unsafe). Memory cost: 8 × ~898 MB ≈ 7.2 GB host RAM and 8 × <2 GB GPU VRAM. `g6e.48xlarge` has 1.5 TB host RAM and 8 × 46 GB L40S — comfortable headroom; do not pre-share the model.

**AD4 — Progress callback degraded for multi-GPU.** Vendor's `run_multi_process` does not surface a per-image callback hook (progress goes to stdout in the child via the shared counter). For the measurement run we accept stdout-only progress; the procrastinate `_emit_progress` path will only emit start + completed + error frames. The SSE stream looks sparse but the wire format stays valid.

**AD5 — No backwards compat.** We're at HEAD; this measurement may or may not promote. Skip alias / dual-path code.

---

## Code Path & Branching Logic

The change in `src/flake_analysis/core/pipeline/sam.py::run_sam` is a single `if` at the top:

```python
import torch
n_gpus = torch.cuda.device_count() if torch.cuda.is_available() else 0
if n_gpus >= 2:
    return _run_sam_multi_gpu(images_dir, weights_path, out_dir, n_gpus, progress_callback)
# else: existing single-GPU path unchanged
result = _vendor_infer(...)
```

`_run_sam_multi_gpu` builds the vendor `config` dict from project state (or constants — see Task 2), calls `vendor.run_amg_v2.run_multi_process(images, out_dir, config, num_gpus=n_gpus)`, then collapses the returned `List[Dict[str, Any]]` into the same `{"images": int, "masks_total": int, "errors": int, "per_image": {...}}` summary the single-GPU path emits — so the procrastinate task return value, the SSE `completed` payload, and the persisted `per_image_results.json` are all byte-compatible with what downstream stages expect.

---

## Result Aggregation — vendor trace

`run_multi_process` (lines 1069–1149) ends with:

```python
all_results = []
for ar in async_results:
    worker_results = ar.get()       # List[Dict] from one worker
    all_results.extend(worker_results)
all_results.sort(key=lambda x: x["image_info"]["id"])
return all_results
```

So **vendor already returns one merged, ordered list**. Our adapter only has to translate the vendor record shape (`{image_info, annotations, num_masks, mask_paths, image_path}`) to our summary dict — drop `annotations` and `mask_paths` (they're disk-resident already from `process_image`'s side effects), keep `num_masks` per filename.

---

## Risks (Top 4)

1. **Child OOM if `model_config` not freed between images.** Each child holds the full SAM2 model in VRAM for its slice (~456 imgs/GPU). L40S 46 GB headroom is large, but a memory leak in the AMG inner loop would bite. Mitigation: monitor `nvidia-smi` during run; if any GPU passes 30 GB sustained, kill and retry with smaller per-worker chunks. **Pre-mitigation:** none — we're measuring vendor as-is.
2. **Bookkeeping race in shared counter.** `manager.Value + Lock` is correct in vendor code, but `image_id_offset` arithmetic (line ~1104) means the 1-based `image_id` field is per-worker-slice unique, not cluster-unique — verify before parsing the merged list. Mitigation: sort/dedupe by `image_path` if `image_info["id"]` collides.
3. **Spot interrupt mid-fanout.** SIGTERM hits the procrastinate worker → `graceful_timeout=30s` → children get killed. We get partial output (some children flushed their per-image NPZ files, others didn't). Acceptable for a measurement run; document as best-effort.
4. **`config` dict shape drift between vendor `run_amg_v2.py` and our existing project-state plumbing.** Vendor expects ~25 keys (`sam2_repo`, `config_yaml`, `checkpoint`, `points_per_side`, etc.) while our single-GPU inference path doesn't build that dict. Mitigation: hard-code the dict using the same defaults baked into `run_amg_v2.py` `parse_args` (lines ~880–950); if a key is missing, vendor `KeyError` will surface on first task invocation and the operator fixes it before measurement run.

---

## Tasks

### Task 1 — Audit vendor `run_amg_v2.py` config dict shape and verify dict-config bug absence (~5 min)
- [x] Open `vendor/QPress-SAM-Flake/run_amg_v2.py` and list every `config[...]` access in `worker_process_images` (lines 1010–1066) and `process_image` (search for callees). Produce a flat list of required keys.
- [x] Verify lines around 988–1003 (build_sam2 path resolution) accept a string-form `config["config_yaml"]`, not a dict — i.e. confirm the inference-path patch at `run_amg_v2_inference.py:54` does NOT need to be re-applied here.
- [x] Document the key list in a comment at the top of the new helper (Task 2). **Output:** comment block listing all `config[...]` keys.

### Task 2 — Add `_run_sam_multi_gpu` helper to `src/flake_analysis/core/pipeline/sam.py` (~12 min)
- [x] Add a lazy-import shim `_vendor_run_multi_process(images, output_dir, config, num_gpus)` mirroring the existing `_vendor_infer` pattern (sys.path insert of `vendor/QPress-SAM-Flake`, then `from run_amg_v2 import run_multi_process`).
- [x] Add `_build_vendor_config(weights_path: Path) -> dict` returning the hard-coded dict matching Task 1's key list. Hard-code the SAM2 defaults (`points_per_side=32`, `pred_iou_thresh=0.88`, etc. — match `run_amg_v2.parse_args` defaults). `weights_path` populates `checkpoint` / `ckpt_dir` + `ckpt_file`.
- [x] Add `_run_sam_multi_gpu(images_dir, weights_path, out_dir, n_gpus, progress_callback)` that: builds the image list (reuse vendor's `_list_images` helper or glob `*.png|*.jpg|*.tif*`), builds config, calls `_vendor_run_multi_process(...)`, translates the returned `List[Dict]` to our summary shape `{"images", "masks_total", "errors", "per_image": {filename: {n_masks, error}}}`, writes `out_dir / "per_image_results.json"`. Emit a single `progress_callback(0.0, "starting 8-GPU fan-out")` at start and `progress_callback(1.0, "completed N images")` at end (vendor stdout handles intermediate progress).
- [x] Edit `run_sam(...)` in the same file to add the `torch.cuda.device_count() >= 2` branch at the top, before the single-GPU `_vendor_infer` call.

### Task 3 — Smoke-import the new path locally without GPUs (~3 min)
- [x] On any laptop / CI box with no CUDA, run `python -c "from flake_analysis.core.pipeline.sam import run_sam; print('ok')"` to confirm imports don't break (the lazy `_vendor_run_multi_process` shim must not be triggered at import time).
- [x] Run `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"` — confirms `device_count == 0` keeps us on the single-GPU path. **Output:** import passes, `device_count == 0` confirmed.

### Task 4 — Extend `scripts/aws/sam-launch-template.sh` for `INSTANCE_TYPE=g6e.48xlarge` and `--image-id` override (~6 min)
- [x] Verify `INSTANCE_TYPE` env override at line 36 already works end-to-end. Add a `--image-id ami-0b7ec5ff47a1eff11` env-controlled path (`IMAGE_ID_OVERRIDE`) so we don't pull a fresh Ubuntu AMI lookup but reuse our prepared one.
- [x] If override env is set, skip the `aws ec2 describe-images` call (lines ~57–66) and use the override.
- [x] Test with `INSTANCE_TYPE=g6e.48xlarge IMAGE_ID_OVERRIDE=ami-0b7ec5ff47a1eff11 ./scripts/aws/sam-launch-template.sh` — should create or update `qpress-sam-gpu-worker` to a new version targeting the 8-GPU instance type. Do **not** launch an instance yet; just verify the template version JSON is correct via `aws ec2 describe-launch-template-versions`. **Result: v9 created (default), InstanceType=g6e.48xlarge, ImageId=ami-0b7ec5ff47a1eff11.**

### Task 5 — Launch g6e.48xlarge spot, wait for procrastinate worker (~3 min compute, ~7 min wait)
- [ ] `aws ec2 run-instances --launch-template LaunchTemplateName=qpress-sam-gpu-worker,Version=\$Default --instance-type g6e.48xlarge --region us-east-2 --tag-specifications 'ResourceType=instance,Tags=[{Key=Purpose,Value=8gpu-measurement}]'`. Capture instance-id.
- [ ] Wait for SSM agent: poll `aws ssm describe-instance-information --instance-information-filter-list 'key=InstanceIds,valueSet=<id>'` until `PingStatus=Online` (~3–5 min).
- [ ] Once online, verify worker started: `aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript --parameters 'commands=["pgrep -af flake_analysis.worker | head -3", "nvidia-smi -L | wc -l"]'` — expect 1 worker pid and `8` from `nvidia-smi -L`.

### Task 6 — Defer the 3648-image `run_sam` task using the `/proc/PID/environ` pattern (~5 min)
- [ ] On the instance via SSM: write `/tmp/measure_8gpu_defer.py` that imports `flake_analysis.worker.app.app`, computes `raw_images_dir`, `analysis_folder`, `weights_path` from project-state for the target scan (3648 imgs), and calls `app.configure_task(name="run_sam", queue="gpu").defer(run_id=<NEW_ID>, raw_images_dir=..., analysis_folder=..., weights_path="/opt/sam/weights/merged.pt")`.
- [ ] Reuse the launcher idiom from `/tmp/phase_c_run.sh` (read `SAA_*`, `HF_*`, `SAM_*` from `/proc/$(pgrep flake_analysis.worker)/environ`, exec the venv python on `/tmp/measure_8gpu_defer.py`).
- [ ] Send via `aws ssm send-command` and capture the run_id. **Output:** procrastinate job id + run_id, both logged.

### Task 7 — Monitor + collect (~30 min, mostly waiting)
- [ ] Tail the worker via SSM every 60s: `tail -n 50 /var/log/flake_analysis/worker.log` and `nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader`. Record GPU utilization snapshots at t=2min, 5min, 15min.
- [ ] When task completes (procrastinate emits `completed` SSE frame OR PG `procrastinate_jobs.status='succeeded'`), capture: total wall-time (job `attempted_at` → `succeeded_at`), `per_image_results.json` summary (images, masks_total, errors), worker stdout for any `[Worker N]` warnings.
- [ ] Compute observed s/img and speedup vs 3.98 s baseline. **Output:** numbers in a comment on the run record + `docs/sam-ops.md` measurement section.

### Task 8 — Terminate + write up (~5 min)
- [ ] `aws ec2 terminate-instances --instance-ids <id>` immediately after collection. Verify spot instance stops (no idle billing).
- [ ] Append a "8-GPU Measurement Run YYYY-MM-DD" section to `docs/sam-ops.md` with: instance-type, run_id, image count, wall-time, s/img observed, s/img ideal, speedup, errors, per-GPU utilization snapshot.
- [ ] Update `docs/project-status.md` with the measurement outcome and a one-line recommendation (promote / shelve / iterate). **Done.**

---

## Measurement Protocol — Reference Commands

**Launch** (operator shell, post-Task 4):
```bash
INSTANCE_TYPE=g6e.48xlarge IMAGE_ID_OVERRIDE=ami-0b7ec5ff47a1eff11 \
  ./scripts/aws/sam-launch-template.sh
aws ec2 run-instances --region us-east-2 \
  --launch-template LaunchTemplateName=qpress-sam-gpu-worker,Version='$Default' \
  --instance-type g6e.48xlarge \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Purpose,Value=8gpu-measurement}]'
```

**Defer** (via SSM, see Task 6 — script lives at `/tmp/measure_8gpu_defer.py`, launcher at `/tmp/measure_8gpu_run.sh`, modeled on existing `/tmp/phase_c_run.sh`).

**Collect**:
```sql
SELECT id, status, attempted_at, succeeded_at,
       EXTRACT(EPOCH FROM (succeeded_at - attempted_at)) AS wall_s
  FROM procrastinate_jobs
 WHERE id = <JOB_ID>;
```
plus `cat <analysis_folder>/sam/per_image_results.json | jq '{images,masks_total,errors}'`.

**Terminate**: `aws ec2 terminate-instances --instance-ids <id>` (do not wait for collection — terminate is irreversible only after the per_image_results.json is pulled / verified on the bastion).

---

## What This Plan Explicitly Does NOT Do

- **No new pytest.** Verification is the production measurement run itself.
- **No alembic migration.** Pure code path.
- **No new procrastinate queue.** Same `gpu` queue.
- **No AMI rebuild.** Reuse `ami-0b7ec5ff47a1eff11`.
- **No backwards-compat flags.** Single hardware gate, no config knob.
- **No SSE progress refinement for multi-GPU.** Stdout-only intermediate progress is acceptable for a measurement run; if we promote, a per-worker NOTIFY hook is a follow-up.
