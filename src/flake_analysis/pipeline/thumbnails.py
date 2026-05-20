"""App-level pipeline wrapper for Thumbnails LOD pre-render.

Calls flake_analysis.core.pipeline.thumbnails.run_thumbnails, then
updates the manifest. Thumbnails depend only on the raw images
directory (no upstream pipeline step), so this can run before
Background — it is the first step in the page.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flake_analysis.core.pipeline.thumbnails import (
    run_thumbnails as core_run_thumbnails,
)

from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
    stamp_top_level,
)
from flake_analysis.state.paths import step_dir
from flake_analysis.state.hashing import dir_mtime_max


# Public progress signature: pct in [0, 1] + short status string.
ProgressCallback = Callable[[float, str], None]


def run_thumbnails_step(
    *,
    analysis_folder: str | Path,
    raw_images_dir: str | Path,
    raw_ext: str = ".png",
    quality: int = 80,
    force_recompute: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run the thumbnails LOD pre-render step.

    Writes per-LOD WebP thumbnails under
    ``<analysis_folder>/00_thumbnails/lod{0,1,2}/`` plus ``index.json``,
    and updates ``manifest.json`` with the ``thumbnails`` step entry.
    """
    output_dir = step_dir(analysis_folder, "thumbnails")
    output_dir.mkdir(parents=True, exist_ok=True)

    result = core_run_thumbnails(
        raw_images_dir=raw_images_dir,
        output_dir=output_dir,
        raw_ext=raw_ext,
        quality=quality,
        force_recompute=force_recompute,
        progress_callback=progress_callback,
    )

    # Update manifest. Thumbnails have no upstream pipeline dependency
    # — only the raw_images_dir mtime is recorded for stale detection.
    manifest = load_manifest(analysis_folder)
    stamp_top_level(
        manifest,
        analysis_folder=analysis_folder,
        raw_images_dir=raw_images_dir,
    )
    manifest.steps["thumbnails"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params=result.get("params", {}),
        params_hash=result.get("params_hash"),
        input_hashes={
            "raw_images_dir_mtime_max": dir_mtime_max(raw_images_dir),
        },
        outputs={"index_json": "00_thumbnails/index.json"},
    )
    save_manifest(manifest, analysis_folder)

    return {
        "output_dir": str(output_dir),
        "n_images": int(result.get("n_images", 0)),
        "n_skipped": int(result.get("n_skipped", 0)),
        "n_failed": int(result.get("n_failed", 0)),
        "params": result.get("params", {}),
        "params_hash": result.get("params_hash"),
    }
