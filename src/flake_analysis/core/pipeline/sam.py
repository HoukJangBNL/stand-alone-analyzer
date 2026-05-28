"""SAM2 inference adapter — bridges vendor ``run_amg_v2`` /
``run_amg_v2_inference`` into our ``ProgressCallback(pct, msg)`` protocol.

Hardware-gated multi-GPU branch: when ``torch.cuda.device_count() >= 2``,
delegate to vendor ``run_amg_v2.run_multi_process`` (spawn pool, GPU pin,
per-image ordering — see vendor lines 1069–1149).

The multi-GPU branch is **dual-mode** (#209, see docs/sam-ops.md §15/§16):

* **merged_m3 (preferred)** — when ``SAM_MERGED_M3_PATH`` env var is set
  and points at a non-empty ``.pt`` file, we route the spawn workers
  through vendor ``build_sam2_from_yaml`` (``use_original_sam2=True``)
  with the merged_m3 ``.pt`` as ``checkpoint`` and the M3 bundle's
  ``sam2.1_hiera_l.yaml`` as ``config_yaml``. The merged_m3 artifact has
  LoRA folded into the base weights, so each forward pass skips the
  per-call adapter math. This recovers the ~3.06× per-card slowdown
  documented in §15 (12.16 s/card-img on un-merged M3 vs 3.98 s/img
  baseline on the single-GPU ``merged.pt`` path).
* **lora-runtime (fallback)** — when ``SAM_MERGED_M3_PATH`` is unset
  OR points at a missing/empty file, we route through vendor
  ``build_sam2_finetuned`` (``use_original_sam2=False``) which loads
  the base SAM2.1 ckpt + applies LoRA at runtime from the M3 4-asset
  bundle under ``M3_ROOT`` (default ``/opt/sam/m3``):

      /opt/sam/m3/sam2.1/sam2.1_hiera_l.pt           # base SAM2.1 ckpt
      /opt/sam/m3/sam2.1/configs/sam2.1_hiera_l.yaml # config
      /opt/sam/m3/sam2_lora/best_model.pth           # LoRA fine-tune
      /opt/sam/m3/sam2_lora/args.json                # LoRA hyperparams

  This path is the existing behaviour and remains the source of
  correctness truth until the merged_m3 build (§16) has parity-checked
  outputs in production.

**Spawn-worker survival.** Vendor ``run_multi_process`` calls
``mp.get_context("spawn").Pool`` (vendor line 1113) — workers re-import
``run_amg_v2`` in fresh interpreters and do NOT see parent-process
monkeypatches. The routing decision therefore lives in the **config
dict** that vendor pickles to each worker (vendor passes ``config`` into
``worker_process_images`` which reads ``config["use_original_sam2"]``
at vendor line 990). Because the dict round-trips through pickle, the
merged_m3 vs lora-runtime choice survives spawn re-import deterministically
without env-var tricks inside the worker. The env var
``SAM_MERGED_M3_PATH`` is read once in the parent before fanning out.

Single-GPU / no-CUDA hosts continue through ``_vendor_infer`` (the
``run_amg_v2_inference.infer`` ``state["model_config"]`` shortcut at
vendor lines 51–56) — that path is **unchanged** by this branch.

Two vendor side effects are neutralised in our adapter so we can call
``run_multi_process`` without polluting process state:

1. ``run_amg_v2.ensure_sam2_importable`` does ``os.chdir(sam2_repo)``
   (P1.2 precedent). We wrap it in ``_safe_ensure_sam2_importable``
   which save+restores ``os.getcwd()``.
2. ``run_amg_v2.load_training_args`` returns the args.json *as-is*, and
   the prod args.json carries absolute paths to the trainer host
   (``/home2/qpress/...``). We monkeypatch the vendor binding at the
   start of every multi-GPU run with ``_patched_load_training_args``,
   which rewrites ``model_dir`` / ``checkpoint`` / ``config`` to the
   M3-local equivalents *in memory* (the on-disk file is never
   touched). Note: this only affects the parent — spawn workers
   re-import vendor and use ``args.json`` raw, but the prod-path
   symlinks installed by ``sam-gpu-worker-userdata.sh`` step 5c
   resolve those raw paths to the M3 layout. Under the merged_m3
   path this monkeypatch is inert (vendor never calls
   ``load_training_args`` when ``use_original_sam2=True``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional


# On-instance M3 asset root. Override via ``SAM_M3_ROOT`` env var (used
# by tests so they don't depend on /opt/sam being writable). The
# function-time read of ``M3_ROOT`` lets ``monkeypatch.setattr`` swap it.
M3_ROOT: Path = Path(os.environ.get("SAM_M3_ROOT", "/opt/sam/m3"))


# Module-level guard — vendor's chdir is only worth running once per
# process; subsequent calls are no-ops in our wrapper.
_SAM2_IMPORT_APPLIED: bool = False


def _vendor_infer(*args, **kwargs):
    """Lazy import of vendor inference module so unit tests can patch
    this seam without requiring sam2/torch in CI."""
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2_inference import infer
    return infer(*args, **kwargs)


def _vendor_run_multi_process(*args, **kwargs):
    """Lazy import of vendor multi-GPU pool. Mirrors ``_vendor_infer``'s
    sys.path shim so import-time has zero cost on CPU-only hosts."""
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2 import run_multi_process
    return run_multi_process(*args, **kwargs)


def _vendor_amg_module():
    """Lazy access to the vendor ``run_amg_v2`` module so we can swap
    its ``load_training_args`` binding at runtime without forcing the
    import on CPU-only hosts."""
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    import run_amg_v2
    return run_amg_v2


def _vendor_ensure_sam2_importable(sam2_repo: Path) -> None:
    """Lazy proxy for vendor's chdir-side-effecting helper. Tests stub
    this seam; ``_safe_ensure_sam2_importable`` is what production code
    calls."""
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2 import ensure_sam2_importable
    ensure_sam2_importable(sam2_repo)


ProgressCallback = Callable[[float, str], None]


def _list_images(images_dir: Path) -> list[Path]:
    """Match the extension whitelist used by ``run_amg_v2_inference._list_images``
    so single-GPU and multi-GPU paths see identical input sets."""
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    return sorted(p for p in images_dir.iterdir() if p.suffix.lower() in exts)


# ---------------------------------------------------------------------------
# M3 path wiring
# ---------------------------------------------------------------------------


def _resolve_sam2_repo() -> Path:
    """Resolve the SAM2 source-tree directory the vendor needs on
    ``sys.path``. Strategy: prefer an installed ``sam2`` package (devops
    may pip-install it on the worker), fall back to env ``SAM_REPO_DIR``.

    Vendor's ``ensure_sam2_importable`` will be called against this path,
    and ``build_sam2_finetuned`` resolves Hydra configs by walking the
    ``configs/`` tree under it.
    """
    try:
        import sam2  # noqa: F401
        # Installed package — walk up from its module file to the package root.
        import importlib
        pkg = importlib.import_module("sam2")
        pkg_path = Path(pkg.__file__).resolve().parent
        # Hydra config resolution wants the directory ABOVE configs/, which
        # is the sam2 package root.
        return pkg_path
    except ImportError:
        pass
    env = os.environ.get("SAM_REPO_DIR")
    if env:
        return Path(env)
    raise RuntimeError(
        "SAM2 source tree not locatable: install the `sam2` package or "
        "set SAM_REPO_DIR to a directory containing the sam2/ tree."
    )


def _safe_ensure_sam2_importable(sam2_repo: Path) -> None:
    """Wrap vendor's ``ensure_sam2_importable`` so its ``os.chdir`` side
    effect doesn't leak into our process. Idempotent across one process
    (the guard flag suppresses repeat calls)."""
    global _SAM2_IMPORT_APPLIED
    if _SAM2_IMPORT_APPLIED:
        return
    cwd_before = os.getcwd()
    try:
        _vendor_ensure_sam2_importable(sam2_repo)
    finally:
        os.chdir(cwd_before)
    _SAM2_IMPORT_APPLIED = True


def _load_and_patch_args(ckpt_dir: Path) -> dict:
    """Read ``args.json`` from ``ckpt_dir`` and return a dict whose
    ``model_dir`` / ``checkpoint`` / ``config`` fields point at the
    M3-local layout — without writing to disk.

    Prod args.json carries trainer-host absolute paths
    (``/home2/qpress/qpress/models/...``); rewriting these is what makes
    vendor ``build_sam2_finetuned`` find the base ckpt and yaml on the
    worker.
    """
    args_path = ckpt_dir / "args.json"
    with args_path.open("r") as f:
        args = json.load(f)
    # Patch in-memory — never mutate the on-disk file.
    args["model_dir"] = str(M3_ROOT)
    args["checkpoint"] = str(M3_ROOT / "sam2.1" / "sam2.1_hiera_l.pt")
    args["config"] = str(M3_ROOT / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml")
    return args


def _patched_load_training_args(ckpt_dir: Path) -> dict:
    """Replacement for ``run_amg_v2.load_training_args`` — installed via
    monkeypatch at the start of every multi-GPU run so vendor's
    ``build_sam2_finetuned`` sees the rewritten paths."""
    return _load_and_patch_args(Path(ckpt_dir))


def _resolve_merged_m3_path() -> Optional[Path]:
    """Discover the pre-merged M3 artifact (#209, docs/sam-ops.md §16).

    Returns the absolute path to ``merged_m3.pt`` iff:
        1. ``SAM_MERGED_M3_PATH`` env var is set (populated by
           ``sam-gpu-worker-userdata.sh`` step 5d), AND
        2. The path resolves to an existing file, AND
        3. The file is non-empty (defensive against partial downloads
           the userdata SHA256 check did not catch).

    Returns ``None`` on any miss → caller falls back to the LoRA-runtime
    path. Soft-miss is **expected** when a worker boots before the
    merged_m3 has been published to S3; the userdata script is
    deliberately tolerant of an empty ``${S3_MERGED_M3_PFX}`` listing.
    """
    raw = os.environ.get("SAM_MERGED_M3_PATH")
    if not raw:
        return None
    path = Path(raw)
    try:
        if not path.is_file():
            return None
        if path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return path


def _build_vendor_config() -> dict[str, Any]:
    """Construct the config dict consumed by vendor
    ``worker_process_images`` (vendor lines 988–1053) for the M3 asset
    bundle. AMG defaults match ``run_amg_v2.parse_args`` verbatim
    (lines ~880–947); do NOT invent values.

    ``sam2_repo`` is left empty here — the caller (``_run_sam_multi_gpu``)
    populates it after resolving the on-instance SAM2 source tree, so
    config-build remains side-effect-free.
    """
    return {
        # Model paths — M3 layout. ``sam2_repo`` filled in by caller.
        "sam2_repo": "",
        "use_original_sam2": False,
        "config_yaml": None,        # only read when use_original_sam2=True
        "checkpoint": None,         # only read when use_original_sam2=True
        "ckpt_dir": str(M3_ROOT / "sam2_lora"),
        "ckpt_file": "best_model.pth",
        # AMG params (parser defaults).
        "points_per_side": 48,
        "points_per_batch": 64,
        "pred_iou_thresh": 0.78,
        "stability_score_thresh": 0.88,
        "box_nms_thresh": 0.6,
        "crop_n_layers": 1,
        "crop_overlap_ratio": 512 / 1500,
        "output_mode": "binary_mask",
        # Per-image side-effect controls.
        "flatfield_path": None,
        "save_masks": True,
        "save_original": False,
        "save_overlays": False,
        "overlay_alpha": 0.45,
        "min_mask_region_area": 500,
        # Flat-enclosure rejection knobs.
        "reject_flat_enclosed": False,
        "flat_rgb_std_thresh": 6.0,
        "flat_edge_in_thresh": 2.0,
        "enclosure_edge_ratio": 3.0,
        "enclosure_ring_width": 7,
    }


# ---------------------------------------------------------------------------
# Multi-GPU orchestration
# ---------------------------------------------------------------------------


def _run_sam_multi_gpu(
    *,
    images_dir: Path,
    weights_path: Path,
    out_dir: Path,
    n_gpus: int,
    progress_callback: Optional[ProgressCallback],
) -> dict:
    """Multi-GPU branch — delegate to vendor ``run_multi_process``.

    **Dual-mode routing** (#209, docs/sam-ops.md §15/§16):

    * If ``SAM_MERGED_M3_PATH`` resolves (via :func:`_resolve_merged_m3_path`)
      we flip ``config["use_original_sam2"]=True`` and point
      ``checkpoint`` at the pre-merged ``.pt`` + ``config_yaml`` at the
      M3 bundle's ``sam2.1_hiera_l.yaml``. Vendor
      ``worker_process_images`` (line 990) then dispatches to
      ``build_sam2_from_yaml`` — the LoRA-folded single-``.pt`` path
      that produced the 3.98 s/img baseline.
    * Otherwise we keep ``use_original_sam2=False`` and route through
      ``build_sam2_finetuned`` (LoRA-applied-at-runtime, the existing
      M3 4-asset path).

    The routing decision lives in the config dict and is therefore
    pickle-safe across vendor's ``mp.spawn`` workers. The routing
    choice is logged to ``progress_callback`` so #211 re-measurement
    can confirm the new path is being taken without scraping
    nvidia-smi.

    Returns the same summary shape as the single-GPU path:
        {"images", "masks_total", "errors", "per_image": {filename: {n_masks, error}}}

    ``weights_path`` is accepted for signature symmetry with the
    single-GPU path but is ignored here — multi-GPU resolves the
    LoRA + base ckpt via ``M3_ROOT`` (or the merged_m3 ``.pt`` via
    env var).
    """
    images = _list_images(images_dir)
    total = len(images)

    if progress_callback is not None:
        progress_callback(0.0, f"starting {n_gpus}-GPU fan-out across {n_gpus} GPUs")

    if total == 0:
        summary = {"images": 0, "masks_total": 0, "errors": 0, "per_image": {}}
        (out_dir / "per_image_results.json").write_text(json.dumps(summary, indent=2))
        if progress_callback is not None:
            progress_callback(1.0, "completed 0 images, 0 masks")
        return summary

    # Resolve sam2 repo + neutralise vendor's chdir.
    sam2_repo = _resolve_sam2_repo()
    _safe_ensure_sam2_importable(sam2_repo)

    # Monkeypatch vendor binding so build_sam2_finetuned sees rewritten
    # paths instead of the raw prod absolutes baked into args.json. We
    # patch the attribute on the vendor module (NOT the on-disk file).
    # Note: under the merged_m3 routing this patch is inert (vendor only
    # calls ``load_training_args`` from inside ``build_sam2_finetuned``);
    # we still install it because the patch is cheap and we want the
    # parent-side fallback to work even if ``_resolve_merged_m3_path``
    # is wrong about the artifact's validity at process-start time.
    _vendor_amg = _vendor_amg_module()
    original_loader = _vendor_amg.load_training_args
    _vendor_amg.load_training_args = _patched_load_training_args
    try:
        config = _build_vendor_config()
        # Ensure sam2_repo is present in the dict regardless of probe outcome.
        config["sam2_repo"] = str(sam2_repo)

        # Dual-mode override: prefer merged_m3 single-.pt path when the
        # artifact is on disk. The decision is encoded in the config
        # dict so it pickles cleanly to vendor's spawn workers.
        merged_m3 = _resolve_merged_m3_path()
        if merged_m3 is not None:
            yaml_path = M3_ROOT / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml"
            config["use_original_sam2"] = True
            config["config_yaml"] = str(yaml_path)
            config["checkpoint"] = str(merged_m3)
            if progress_callback is not None:
                size_mb = merged_m3.stat().st_size / (1024 * 1024)
                progress_callback(
                    0.0,
                    f"routing: merged_m3 ({merged_m3.name}, {size_mb:.0f} MiB) "
                    f"→ build_sam2_from_yaml (LoRA pre-merged)",
                )
        else:
            # LoRA-runtime fallback log mirrors the merged_m3 log so a
            # re-measurement can grep either string and know which path
            # was taken.
            if progress_callback is not None:
                ckpt_basename = config["ckpt_file"]
                progress_callback(
                    0.0,
                    f"routing: lora-runtime ({ckpt_basename} from "
                    f"{Path(config['ckpt_dir']).name}) "
                    f"→ build_sam2_finetuned (LoRA applied per forward)",
                )

        vendor_results = _vendor_run_multi_process(
            images, out_dir, config, n_gpus
        )
    finally:
        _vendor_amg.load_training_args = original_loader

    # Vendor returns List[Dict] with ``image_info``, ``num_masks``, etc.
    # Translate to our ``per_image`` map keyed by filename.
    per_image: dict[str, dict] = {}
    for r in vendor_results:
        name = r["image_info"]["file_name"]
        per_image[name] = {"n_masks": r["num_masks"], "error": None}

    masks_total = sum(r["n_masks"] for r in per_image.values())
    errors = sum(1 for r in per_image.values() if r["error"])
    summary = {
        "images": len(per_image),
        "masks_total": masks_total,
        "errors": errors,
        "per_image": per_image,
    }
    (out_dir / "per_image_results.json").write_text(json.dumps(summary, indent=2))

    if progress_callback is not None:
        progress_callback(
            1.0,
            f"completed {summary['images']} images, {masks_total} masks",
        )
    return summary


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_sam(
    *,
    images_dir: Path,
    weights_path: Path,
    out_dir: Path,
    device: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Run SAM2 AMG over images_dir, write per-image masks + summary manifest.

    Returns: {"images": int, "masks_total": int, "errors": int, "per_image": {...}}
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Hardware gate (AD1): branch on visible GPU count, not on a config flag.
    # Lazy-import torch so CPU-only test/CI hosts without torch installed
    # don't pay an import-time penalty.
    try:
        import torch
        n_gpus = torch.cuda.device_count() if torch.cuda.is_available() else 0
    except Exception:  # noqa: BLE001 — torch missing on CPU CI is fine
        n_gpus = 0
    if n_gpus >= 2:
        return _run_sam_multi_gpu(
            images_dir=images_dir,
            weights_path=weights_path,
            out_dir=out_dir,
            n_gpus=n_gpus,
            progress_callback=progress_callback,
        )

    def _shim(payload: dict) -> None:
        if progress_callback is None:
            return
        total = payload["total"] or 1
        pct = payload["idx"] / total
        err = payload.get("error")
        msg = (
            f"[{payload['idx']}/{payload['total']}] {payload['image_name']}: "
            f"{payload['n_masks']} masks"
            + (f" — ERROR: {err}" if err else "")
        )
        progress_callback(pct, msg)

    result = _vendor_infer(
        images_dir=images_dir,
        weights_path=weights_path,
        out_dir=out_dir,
        device=device,
        progress_callback=_shim,
    )

    summary = {
        "images": len(result),
        "masks_total": sum(r["n_masks"] for r in result.values()),
        "errors": sum(1 for r in result.values() if r["error"]),
        "per_image": result,
    }
    (out_dir / "per_image_results.json").write_text(json.dumps(summary, indent=2))
    return summary
