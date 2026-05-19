"""App-level pipeline wrapper for Domain Stats."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flake_analysis.core.pipeline.domain_stats import run_domain_stats as core_run_domain_stats

from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
    stamp_top_level,
)
from flake_analysis.state.hashing import file_mtime, params_hash


ProgressCallback = Callable[[float, str], None]


def run_domain_stats_step(
    *,
    raw_images_dir: str | Path,
    annotations_path: str | Path,
    analysis_folder: str | Path,
    repr_mode: str = "median",
    raw_ext: str = ".png",
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run domain stats step.

    Requires the Background step to be completed first (background.npy must
    exist on disk and be recorded in the manifest).
    """
    manifest = load_manifest(analysis_folder)
    bg_entry = manifest.steps.get("background")
    if bg_entry is None or bg_entry.completed_at is None:
        raise RuntimeError(
            "Background step not completed. Run Background first."
        )

    background_path = Path(analysis_folder) / "01_background" / "background.npy"
    if not background_path.exists():
        raise RuntimeError(f"background.npy missing at {background_path}")

    params: Dict[str, Any] = {
        "repr_mode": repr_mode,
        "raw_ext": raw_ext,
    }

    result = core_run_domain_stats(
        annotations_path=annotations_path,
        raw_images_dir=raw_images_dir,
        background_path=background_path,
        analysis_folder=analysis_folder,
        repr_mode=repr_mode,
        raw_ext=raw_ext,
        progress_callback=progress_callback,
    )

    stamp_top_level(
        manifest,
        analysis_folder=analysis_folder,
        raw_images_dir=raw_images_dir,
        annotations_path=annotations_path,
    )
    manifest.steps["domain_stats"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "annotations_mtime": file_mtime(annotations_path),
            "background_params_hash": bg_entry.params_hash,
        },
        outputs={"stats_npz": "02_domain_stats/stats.npz"},
    )
    save_manifest(manifest, analysis_folder)

    return {
        "output_path": str(result.get("output_path")),
        "num_flakes": int(result.get("num_flakes", 0)),
        "params": params,
    }
