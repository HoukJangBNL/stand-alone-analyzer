"""Thin wrapper for per-domain color stats compute.

Replaces Qpress's ``flake_stats_operation.py`` logic without DB / Operation /
Context. Loads annotations + raw images + a mandatory pre-computed background
and writes a single ``stats.npz`` artifact whose schema matches Qpress's
``flake_stats_median_<repr_mode>.npz`` exactly:

  * ``repr_rgbs``  shape (N, 3) float64
  * ``std_pcts``   shape (N, 3) float64
  * ``areas``      shape (N,)   int32
  * ``flake_ids``  shape (N,)   int64

Output is reordered to match the input flakes list (deterministic for a
given annotations + raw image set).

Plan v1 r7:
  * ``bg_mode`` parameter removed — median-mode only.
  * ``output_path`` parameter removed — NPZ is always written to
    ``<analysis_folder>/02_domain_stats/stats.npz``.
  * ``background_path`` is mandatory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np
from PIL import Image

from flake_analysis.core._compat import ProgressCallback, msg
from flake_analysis.core.annotations import AnnotationsCache, load_flakes_from_annotations
from flake_analysis.core.color_classification.loader import compute_and_cache_stats_from_flakes


def _hash_params(params: Dict[str, Any]) -> str:
    """SHA-256 of canonical JSON for the params dict."""
    payload = json.dumps(
        params, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def run_domain_stats(
    annotations_path: Union[str, Path],
    raw_images_dir: Union[str, Path],
    background_path: Union[str, Path],
    *,
    analysis_folder: Union[str, Path],
    repr_mode: str = "median",
    raw_ext: str = ".png",
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Compute per-domain color stats and write the NPZ artifact.

    Parameters
    ----------
    annotations_path : str | Path
        Path to ``annotations.json`` (COCO-style). Either a file path or the
        directory containing the file is accepted (pursues the file inside
        ``segmentation/`` when a parent dir is given).
    raw_images_dir : str | Path
        Directory containing raw PNGs whose stems match
        ``annotations.images[].file_name`` stems.
    background_path : str | Path
        Pre-computed background image (PNG or NPY) used for median-mode
        vignetting correction. **Mandatory** as of plan v1 r7.
    analysis_folder : str | Path
        Root folder for the analysis artifacts. Output NPZ is written to
        ``<analysis_folder>/02_domain_stats/stats.npz`` (parent created if
        missing).
    repr_mode : str, optional
        ``"median"`` (default, robust) or ``"mean"``.
    raw_ext : str, optional
        File extension of raw images. Default ``".png"``.

    Returns
    -------
    dict
        Summary including ``output_path``, ``num_flakes``, ``params``,
        ``params_hash``.

    Raises
    ------
    ValueError
        When ``background_path`` is ``None`` (plan v1 r7 makes background
        mandatory in median-only mode).
    FileNotFoundError
        When the resolved ``background_path`` does not exist on disk.
    """
    if background_path is None:
        raise ValueError(
            "background_path is required (median-only mode in r7)"
        )
    if repr_mode not in ("median", "mean"):
        raise ValueError(f"repr_mode must be 'median' or 'mean', got {repr_mode!r}")

    annotations_path = Path(annotations_path)
    raw_images_dir = Path(raw_images_dir)
    background_path = Path(background_path)
    analysis_folder = Path(analysis_folder)

    if not background_path.exists():
        raise FileNotFoundError(
            f"background_path does not exist: {background_path} "
            f"(median-only mode in r7 requires a pre-computed background)"
        )

    # Hardcoded output path: <analysis_folder>/02_domain_stats/stats.npz
    output_path = analysis_folder / "02_domain_stats" / "stats.npz"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    msg.info(
        f"[pipeline.domain_stats] start ann={annotations_path} "
        f"raw={raw_images_dir} bg={background_path} "
        f"out={output_path} repr_mode={repr_mode}"
    )

    if progress_callback is not None:
        progress_callback(0.0, "Loading annotations + raw images...")

    # --- Resolve annotations layout --------------------------------------
    # AnnotationsCache.load() expects:
    #   annotations.json at: {analysis_dir}/{analysis_type}/annotations.json
    # We accept either a direct file path or any of its ancestors and
    # reconstruct the (analysis_dir, analysis_type) split.
    if annotations_path.is_file():
        ann_file = annotations_path
    elif annotations_path.is_dir() and (annotations_path / "annotations.json").exists():
        ann_file = annotations_path / "annotations.json"
    else:
        raise FileNotFoundError(f"annotations.json not found at {annotations_path}")

    analysis_type_dir = ann_file.parent
    analysis_type = analysis_type_dir.name or "segmentation"
    analysis_dir = analysis_type_dir.parent
    scan_folder = analysis_dir.parent if analysis_dir != analysis_type_dir else analysis_dir

    cache = AnnotationsCache()
    loaded = cache.load(
        scan_folder=scan_folder,
        analysis_dir=analysis_dir,
        analysis_type=analysis_type,
    )
    if not loaded:
        raise RuntimeError(
            f"Failed to load annotations from {ann_file} "
            f"(analysis_dir={analysis_dir}, analysis_type={analysis_type})"
        )

    # Adapter: load_flakes_from_annotations emits its own 0.0–1.0 stream;
    # we squash it into our 0.0–0.30 outer band so it doesn't drown out
    # the rest of the stats pipeline.
    if progress_callback is not None:
        def _flake_load_cb(pct: float, msg_: str) -> None:
            progress_callback(0.30 * float(pct), msg_)
    else:
        _flake_load_cb = None

    flakes = load_flakes_from_annotations(
        cache,
        raw_images_dir,
        raw_ext=raw_ext,
        progress_callback=_flake_load_cb,
    )
    msg.info(f"[pipeline.domain_stats] loaded {len(flakes)} flakes")

    if progress_callback is not None:
        progress_callback(0.3, f"Loaded {len(flakes)} flakes; reading background...")

    # --- Load explicit background image ----------------------------------
    if background_path.suffix.lower() == ".npy":
        background_image = np.load(background_path).astype(np.float64)
    else:
        background_image = np.array(Image.open(background_path)).astype(np.float64)
    msg.info(f"[pipeline.domain_stats] using background from {background_path}")

    if progress_callback is not None:
        progress_callback(0.5, "Computing stats per flake group...")

    # --- Compute (writes Qpress-compatible NPZ inside cache_dir) ---------
    cache_dir = output_path.parent

    # Adapter: ``compute_and_cache_stats_from_flakes`` reports progress as
    # ``(current, total, message)`` while our public callback is ``(pct, message)``.
    # Map the inner progress into the 0.5 .. 0.9 band of the wrapper's wall-clock.
    inner_cb = None
    if progress_callback is not None:
        def inner_cb(current: int, total: int, message: str) -> None:
            denom = float(total) if total else 1.0
            inner_pct = max(0.0, min(1.0, float(current) / denom))
            outer_pct = 0.5 + 0.4 * inner_pct
            progress_callback(outer_pct, message)

    result = compute_and_cache_stats_from_flakes(
        flakes=flakes,
        cache_dir=cache_dir,
        raw_image_folder=raw_images_dir,
        background_mode="median",  # Plan v1 r7: median-only
        representative_mode=repr_mode,
        force_recompute=True,  # wrapper always writes fresh artifact
        raw_ext=raw_ext,
        background_image=background_image,
        progress_callback=inner_cb,
    )

    if progress_callback is not None:
        progress_callback(0.9, "Writing NPZ...")

    # ``compute_and_cache_stats_from_flakes`` writes
    #   cache_dir/flake_stats_median_<repr>.npz
    # which does not match the user's hardcoded output_path. Resolve by
    # writing the canonical artifact at output_path explicitly.
    flake_ids = np.array([f.flake_id for f in flakes], dtype=np.int64)
    # Persist sam2 score from each flake's metadata so the Selector tab can
    # filter on it. RLEFlake exposes the FlakeMetadata as ``_metadata``;
    # fixture/test objects sometimes use a public ``metadata`` attribute.
    def _flake_score(f) -> float:
        meta = getattr(f, "_metadata", None) or getattr(f, "metadata", None)
        if meta is None:
            return 1.0
        try:
            return float(getattr(meta, "score", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0
    sam2 = np.array([_flake_score(f) for f in flakes], dtype=np.float32)
    np.savez(
        output_path,
        repr_rgbs=result["repr_rgbs"],
        std_pcts=result["std_pcts"],
        areas=result["areas"],
        flake_ids=flake_ids,
        sam2=sam2,
    )
    msg.info(
        f"[pipeline.domain_stats] wrote {len(flake_ids)} domain rows to {output_path}"
    )

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    params: Dict[str, Any] = {
        "annotations_path": str(annotations_path),
        "raw_images_dir": str(raw_images_dir),
        "background_path": str(background_path),
        "analysis_folder": str(analysis_folder),
        "repr_mode": repr_mode,
        "raw_ext": raw_ext,
    }
    return {
        "output_path": output_path,
        "num_flakes": int(len(flake_ids)),
        "params": params,
        "params_hash": _hash_params(params),
    }
