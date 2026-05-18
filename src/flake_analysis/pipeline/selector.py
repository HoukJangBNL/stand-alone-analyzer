"""App-level pipeline wrapper for Selector commit.

Wraps ``flake_core.pipeline.selector.run_selector`` and updates
``manifest.json`` with the selector StepEntry (params, params_hash,
upstream input_hashes, output paths).

Per plan v1 r9 §M2 PR 2.3.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flake_core.pipeline.selector import run_selector as core_run_selector

from flake_analysis.state.hashing import params_hash
from flake_analysis.state.manifest import StepEntry, load_manifest, save_manifest
from flake_analysis.state.paths import step_dir


def run_selector_step(
    *,
    analysis_folder: str | Path,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    std_r_min: Optional[float] = None,
    std_r_max: Optional[float] = None,
    std_g_min: Optional[float] = None,
    std_g_max: Optional[float] = None,
    std_b_min: Optional[float] = None,
    std_b_max: Optional[float] = None,
    sam2_min: Optional[float] = None,
    sam2_max: Optional[float] = None,
) -> Dict[str, Any]:
    """Apply the 5-metric bidirectional filter and write selection.parquet.

    Requires the Domain Stats step to be completed first
    (``02_domain_stats/stats.npz`` must exist on disk and be recorded
    in the manifest).

    Returns a summary dict with ``output_path``, ``selected_count``,
    ``total_count``, ``params``, and ``params_hash``.
    """
    manifest = load_manifest(analysis_folder)
    stats_entry = manifest.steps.get("domain_stats")
    if stats_entry is None or stats_entry.completed_at is None:
        raise RuntimeError(
            "Domain Stats step not completed. Run Compute → Domain Stats first."
        )

    stats_npz = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    if not stats_npz.exists():
        raise RuntimeError(f"stats.npz missing at {stats_npz}")

    output_dir = step_dir(analysis_folder, "selector")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "selection.parquet"

    params: Dict[str, Any] = {
        "area_min": area_min,
        "area_max": area_max,
        "std_r_min": std_r_min,
        "std_r_max": std_r_max,
        "std_g_min": std_g_min,
        "std_g_max": std_g_max,
        "std_b_min": std_b_min,
        "std_b_max": std_b_max,
        "sam2_min": sam2_min,
        "sam2_max": sam2_max,
    }

    result = core_run_selector(
        stats_npz_path=stats_npz,
        output_path=output_path,
        **params,
    )

    manifest.steps["selector"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "domain_stats_params_hash": stats_entry.params_hash,
        },
        outputs={"selection_parquet": "03_selector/selection.parquet"},
    )
    save_manifest(manifest, analysis_folder)

    return {
        "output_path": str(result.get("output_path")),
        "selected_count": int(result.get("selected_count", 0)),
        "total_count": int(result.get("total_count", 0)),
        "params": params,
        "params_hash": result.get("params_hash"),
    }
