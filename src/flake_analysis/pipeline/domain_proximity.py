"""App-level pipeline wrapper for Domain Proximity (pair distance + flake construction)."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flake_analysis.core.pipeline.domain_proximity import (
    run_domain_proximity as core_run_domain_proximity,
)

from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
    stamp_top_level,
)
from flake_analysis.state.paths import step_dir
from flake_analysis.state.hashing import file_mtime, params_hash


ProgressCallback = Callable[[float, str], None]


def run_domain_proximity_step(
    *,
    annotations_path: str | Path,
    analysis_folder: str | Path,
    r_max_px: float = 200.0,
    min_area_px: int = 10,
    max_area_px: Optional[int] = None,
    d_touch_px: float = 2.0,
    pixel_size_um: float = 0.5,
    link_distance_um: float = 5.0,
    workers: int = 4,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run pair distance + flake construction.

    Independent of Background / Domain Stats — only requires annotations.json.
    """
    output_dir = step_dir(analysis_folder, "domain_proximity")
    output_dir.mkdir(parents=True, exist_ok=True)

    params: Dict[str, Any] = {
        "r_max_px": r_max_px,
        "min_area_px": min_area_px,
        "max_area_px": max_area_px,
        "d_touch_px": d_touch_px,
        "pixel_size_um": pixel_size_um,
        "link_distance_um": link_distance_um,
        "workers": workers,
    }

    result = core_run_domain_proximity(
        annotations_path=annotations_path,
        output_dir=output_dir,
        r_max_px=r_max_px,
        min_area_px=min_area_px,
        max_area_px=max_area_px,
        d_touch_px=d_touch_px,
        link_distance_um=link_distance_um,
        pixel_size_um=pixel_size_um,
        workers=workers,
        progress_callback=progress_callback,
    )

    manifest = load_manifest(analysis_folder)
    stamp_top_level(
        manifest,
        analysis_folder=analysis_folder,
        annotations_path=annotations_path,
    )
    manifest.steps["domain_proximity"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "annotations_mtime": file_mtime(annotations_path),
        },
        outputs={
            "distances_parquet": "05_domain_proximity/distances.parquet",
            "flake_assignments_parquet": "05_domain_proximity/flake_assignments.parquet",
        },
    )
    save_manifest(manifest, analysis_folder)

    return {
        "distances_path": str(result.get("distances_path")),
        "flake_assignments_path": str(result.get("flake_assignments_path")),
        "n_pairs": int(result.get("n_pairs", 0)),
        "n_domains": int(result.get("n_domains", 0)),
        "n_flakes": int(result.get("n_flakes", 0)),
        "params": params,
    }
