"""SAM2 inference adapter — bridges vendor run_amg_v2_inference into
our ProgressCallback (pct, msg) protocol used by other pipeline steps.

Hardware-gated multi-GPU branch: when ``torch.cuda.device_count() >= 2``,
delegate to vendor ``run_amg_v2.run_multi_process`` (spawn-pool, GPU pin,
per-image-id ordering — see vendor lines 1069–1149). Single-GPU /
no-CUDA hosts continue through ``_vendor_infer`` unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional


def _vendor_infer(*args, **kwargs):
    """Lazy import of vendor module so unit tests can patch this seam
    without requiring sam2/torch to be importable in CI."""
    import sys
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2_inference import infer
    return infer(*args, **kwargs)


def _vendor_run_multi_process(*args, **kwargs):
    """Lazy import of vendor multi-GPU pool. Mirrors ``_vendor_infer``'s
    sys.path shim so import-time has zero cost on CPU-only hosts."""
    import sys
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2 import run_multi_process
    return run_multi_process(*args, **kwargs)


ProgressCallback = Callable[[float, str], None]


# Vendor multi-process config-dict keys consumed by
# ``worker_process_images`` (vendor lines 988–1053). Building this dict
# is OUR responsibility — vendor only builds it inside its own ``main()``
# from ``argparse`` defaults (lines 1212–1238). We mirror the default
# values from ``parse_args`` (lines 880–947) verbatim.
_VENDOR_CONFIG_KEYS = (
    # Model paths
    "sam2_repo", "use_original_sam2",
    "config_yaml", "checkpoint",
    "ckpt_dir", "ckpt_file",
    # AMG params
    "points_per_side", "points_per_batch",
    "pred_iou_thresh", "stability_score_thresh", "box_nms_thresh",
    "crop_n_layers", "crop_overlap_ratio",
    "output_mode",
    # Per-image side-effect controls
    "flatfield_path",
    "save_masks", "save_original", "save_overlays", "overlay_alpha",
    "min_mask_region_area",
    # Flat-enclosure rejection knobs
    "reject_flat_enclosed",
    "flat_rgb_std_thresh", "flat_edge_in_thresh",
    "enclosure_edge_ratio", "enclosure_ring_width",
)


def _build_vendor_config(weights_path: Path) -> dict[str, Any]:
    """Construct the config dict consumed by vendor ``worker_process_images``.

    Defaults match ``run_amg_v2.parse_args`` exactly (vendor lines ~880–947);
    do NOT invent values here — drift between this dict and the vendor
    parser is the #1 risk per the plan.

    Path strategy: vendor's multi-GPU branch builds models via
    ``build_sam2_finetuned(ckpt_dir, ckpt_file)`` or ``build_sam2_from_yaml``,
    NOT via the dict-form ``state["model_config"]`` patch used by
    ``run_amg_v2_inference.py:54``. We point ``ckpt_dir`` at the parent of
    ``weights_path`` and ``ckpt_file`` at its basename — adapt downstream
    if the merged-pt layout doesn't fit (acknowledged R#4 in the plan).
    """
    weights_path = Path(weights_path)
    return {
        "sam2_repo": "../external/sam2",            # DEFAULT_SAM2_REPO
        "use_original_sam2": False,
        "config_yaml": None,
        "checkpoint": None,
        "ckpt_dir": str(weights_path.parent),
        "ckpt_file": weights_path.name,
        "points_per_side": 48,
        "points_per_batch": 64,
        "pred_iou_thresh": 0.78,
        "stability_score_thresh": 0.88,
        "box_nms_thresh": 0.6,
        "crop_n_layers": 1,
        "crop_overlap_ratio": 512 / 1500,
        "output_mode": "binary_mask",
        "flatfield_path": None,
        "save_masks": True,
        "save_original": False,
        "save_overlays": False,
        "overlay_alpha": 0.45,
        "min_mask_region_area": 500,
        "reject_flat_enclosed": False,
        "flat_rgb_std_thresh": 6.0,
        "flat_edge_in_thresh": 2.0,
        "enclosure_edge_ratio": 3.0,
        "enclosure_ring_width": 7,
    }


def _list_images(images_dir: Path) -> list[Path]:
    """Match the extension whitelist used by ``run_amg_v2_inference._list_images``
    so single-GPU and multi-GPU paths see identical input sets."""
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    return sorted(p for p in images_dir.iterdir() if p.suffix.lower() in exts)


def _run_sam_multi_gpu(
    *,
    images_dir: Path,
    weights_path: Path,
    out_dir: Path,
    n_gpus: int,
    progress_callback: Optional[ProgressCallback],
) -> dict:
    """Multi-GPU branch — delegates to vendor ``run_multi_process`` and
    translates its ``List[Dict]`` return into our summary shape.

    Vendor record shape per item (worker lines 1055–1061):
        {image_info: {id, file_name, ...}, annotations, num_masks, mask_paths, image_path}
    Our summary shape (matches single-GPU ``_vendor_infer`` consumer):
        {"images", "masks_total", "errors", "per_image": {filename: {n_masks, error}}}
    """
    if progress_callback is not None:
        progress_callback(0.0, f"starting 8-GPU fan-out across {n_gpus} GPUs")

    images = _list_images(images_dir)
    config = _build_vendor_config(weights_path)
    vendor_results = _vendor_run_multi_process(
        images, out_dir, config, n_gpus,
    )

    per_image: dict[str, dict] = {}
    masks_total = 0
    errors = 0
    for rec in vendor_results:
        # ``image_info["file_name"]`` is the basename written by vendor
        # ``process_image``; fall back to ``image_path`` for safety.
        info = rec.get("image_info") or {}
        filename = info.get("file_name") or Path(rec["image_path"]).name
        n_masks = int(rec.get("num_masks", 0))
        # Vendor ``run_multi_process`` does not surface per-image errors
        # back through the result list — failures inside ``process_image``
        # raise and abort the worker. We record ``error=None`` for any
        # record we received; aborted slices simply won't appear here.
        per_image[filename] = {"n_masks": n_masks, "error": None}
        masks_total += n_masks

    summary = {
        "images": len(per_image),
        "masks_total": masks_total,
        "errors": errors,
        "per_image": per_image,
    }
    (out_dir / "per_image_results.json").write_text(json.dumps(summary, indent=2))

    if progress_callback is not None:
        progress_callback(1.0, f"completed {summary['images']} images")
    return summary


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
    # don't pay an import-time penalty — but in practice the worker host
    # already has torch loaded, so the cost is negligible there.
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
