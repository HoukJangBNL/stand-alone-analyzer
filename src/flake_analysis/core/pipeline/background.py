"""Thin wrapper for background generation.

Function-shape API, no Operation class, no DB. Calls
``flake_analysis.core.image_processing.background.get_median_background`` and writes
the resulting ndarray to disk.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np

from flake_analysis.core._compat import ProgressCallback, msg
from flake_analysis.core.image_processing.background import (
    get_median_background,
    save_background,
)


def _hash_params(params: Dict[str, Any]) -> str:
    """SHA-256 of canonical JSON for the params dict."""
    payload = json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def run_background(
    raw_images_dir: Union[str, Path],
    *,
    output_path: Union[str, Path],
    seed: Optional[int] = 0,
    max_images: int = 100,
    gaussian_sigma: float = 10.0,
    method: str = "median",
    random_sample: bool = True,
    file_pattern: str = "[!._]*.png",
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run background generation as a function (no Operation class).

    Parameters
    ----------
    raw_images_dir : str | Path
        Directory containing raw PNG images.
    output_path : str | Path
        Where to write the background. ``.npy`` extension dispatches to
        ``np.save`` (preserves float64 precision); any other extension is
        written via ``save_background`` (uint8 image).
    seed : int | None, optional
        Reproducibility seed forwarded to ``get_median_background``. Default ``0``
        (reproducible). Pass ``None`` for legacy unseeded behavior.
    max_images : int, optional
        Sample size cap. Default 100.
    gaussian_sigma : float, optional
        Smoothing sigma. Default 10.0.
    method : str, optional
        ``"median"`` (default) or ``"mean"``.
    random_sample : bool, optional
        If True, randomly sample ``max_images`` files. Default True.
    file_pattern : str, optional
        Glob pattern for image discovery. Default ``"[!._]*.png"``.

    Returns
    -------
    dict
        ``{"array": ndarray, "output_path": Path, "params": {...}, "params_hash": str}``.
    """
    output_path = Path(output_path)
    msg.info(
        f"[pipeline.background] start raw={raw_images_dir} out={output_path} "
        f"seed={seed} max_images={max_images} method={method}"
    )

    if progress_callback is not None:
        progress_callback(0.0, "Listing images...")

    bg = get_median_background(
        raw_images_dir=raw_images_dir,
        max_images=max_images,
        file_pattern=file_pattern,
        random_sample=random_sample,
        gaussian_sigma=gaussian_sigma,
        method=method,
        seed=seed,
        progress_callback=progress_callback,
    )

    if progress_callback is not None:
        progress_callback(0.95, "Writing background...")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".npy":
        np.save(output_path, bg)
        msg.info(f"[pipeline.background] saved ndarray to {output_path}")
    else:
        save_background(bg, output_path)

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    params: Dict[str, Any] = {
        "max_images": max_images,
        "gaussian_sigma": gaussian_sigma,
        "method": method,
        "random_sample": random_sample,
        "file_pattern": file_pattern,
        "seed": seed,
    }
    return {
        "array": bg,
        "output_path": output_path,
        "params": params,
        "params_hash": _hash_params(params),
    }
