"""SAM2 inference adapter — bridges vendor run_amg_v2_inference into
our ProgressCallback (pct, msg) protocol used by other pipeline steps."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional


def _vendor_infer(*args, **kwargs):
    """Lazy import of vendor module so unit tests can patch this seam
    without requiring sam2/torch to be importable in CI."""
    import sys
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2_inference import infer
    return infer(*args, **kwargs)


ProgressCallback = Callable[[float, str], None]


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
