"""SAM step wrapper — resolves subdir from PIPELINE_STEPS layout, delegates to core engine."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from flake_analysis.core.pipeline.sam import run_sam
from flake_analysis.state.paths import SUBDIRS

ProgressCallback = Callable[[float, str], None]


def run_sam_step(
    *,
    raw_images_dir: Path,
    analysis_folder: Path,
    weights_path: Path,
    device: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    out_dir = Path(analysis_folder) / SUBDIRS["sam"]
    return run_sam(
        images_dir=Path(raw_images_dir),
        weights_path=Path(weights_path),
        out_dir=out_dir,
        device=device,
        progress_callback=progress_callback,
    )
