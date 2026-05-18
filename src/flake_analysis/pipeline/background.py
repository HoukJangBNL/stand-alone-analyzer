"""App-level pipeline wrapper for Background generation.

Calls flake_core.pipeline.background.run_background, then updates manifest.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flake_core.pipeline.background import run_background as core_run_background

from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
)
from flake_analysis.state.paths import step_dir
from flake_analysis.state.hashing import dir_mtime_max, params_hash


# Public progress signature: pct in [0, 1] + short status string.
ProgressCallback = Callable[[float, str], None]


def run_background_step(
    *,
    raw_images_dir: str | Path,
    analysis_folder: str | Path,
    seed: int = 0,
    max_images: int = 100,
    gaussian_sigma: float = 10.0,
    method: str = "median",
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run background generation step.

    Writes ``<analysis_folder>/01_background/background.npy`` and updates
    ``manifest.json`` with the ``background`` step entry.
    """
    output_dir = step_dir(analysis_folder, "background")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "background.npy"

    params: Dict[str, Any] = {
        "seed": seed,
        "max_images": max_images,
        "gaussian_sigma": gaussian_sigma,
        "method": method,
    }

    result = core_run_background(
        raw_images_dir=raw_images_dir,
        output_path=output_path,
        seed=seed,
        max_images=max_images,
        gaussian_sigma=gaussian_sigma,
        method=method,
        progress_callback=progress_callback,
    )

    # Update manifest
    manifest = load_manifest(analysis_folder)
    manifest.steps["background"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "raw_images_dir_mtime_max": dir_mtime_max(raw_images_dir),
        },
        outputs={"background_npy": "01_background/background.npy"},
    )
    save_manifest(manifest, analysis_folder)

    array = result.get("array")
    return {
        "output_path": str(output_path),
        "shape": tuple(array.shape) if array is not None else None,
        "params": params,
    }
