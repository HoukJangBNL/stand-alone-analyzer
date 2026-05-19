"""App-level wrapper for Explorer state save.

Persists the Include/Exclude label picker state, the NeighborFilter
parameters, and (optionally) the post-filter list of selected flake_ids
into ``06_explorer/`` and updates ``manifest.json`` with the explorer
StepEntry.

Per plan v1 r9 §M2 PR 2.5 + §8.5 + §10 R9.

Inputs (read upstream from manifest, not directly):

* ``04_clustering/labels.json`` (frozen schema v1)
* ``05_domain_proximity/flake_assignments.parquet``

Outputs:

* ``06_explorer/explorer_state.json`` — filter+neighbor state
* ``06_explorer/selected_flakes.parquet`` — optional post-filter flake_id list
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from flake_analysis.state.hashing import params_hash
from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
    stamp_top_level,
)
from flake_analysis.state.paths import step_dir


def save_explorer_state(
    *,
    analysis_folder: str | Path,
    include_labels: List[str],
    exclude_labels: List[str],
    neighbor_filter: Dict[str, Any],
    selected_flake_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Save explorer filter state + (optional) selected flake list.

    Both Clustering and Domain Proximity must be committed; otherwise the
    explorer state has no upstream to anchor to.

    Parameters
    ----------
    analysis_folder : str | Path
    include_labels : list of str
        Cluster names placed in the Include column.
    exclude_labels : list of str
        Cluster names placed in the Exclude column.
    neighbor_filter : dict
        Compact NeighborFilter state — ``size_enabled``, ``size_min``,
        ``size_max``, ``isolate_enabled``, ``d_isolate_px``,
        ``exclude_border``.
    selected_flake_ids : list[int], optional
        Post-filter list of flake_ids. If provided, written to
        ``selected_flakes.parquet``.

    Returns
    -------
    dict
        ``{"state_path": str, "selected_count": int | None}``.
    """
    manifest = load_manifest(analysis_folder)
    cluster_entry = manifest.steps.get("clustering")
    proximity_entry = manifest.steps.get("domain_proximity")
    if cluster_entry is None or cluster_entry.completed_at is None:
        raise RuntimeError(
            "Clustering step not completed. Commit clustering first."
        )
    if proximity_entry is None or proximity_entry.completed_at is None:
        raise RuntimeError(
            "Domain Proximity step not completed. Run Compute → Domain Proximity first."
        )

    output_dir = step_dir(analysis_folder, "explorer")
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "explorer_state.json"

    params: Dict[str, Any] = {
        "include_labels": list(include_labels),
        "exclude_labels": list(exclude_labels),
        "neighbor_filter": dict(neighbor_filter),
    }
    saved_at = datetime.now(timezone.utc).isoformat()
    state = {**params, "saved_at": saved_at}
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    sel_path: Optional[Path] = None
    if selected_flake_ids is not None:
        sel_path = output_dir / "selected_flakes.parquet"
        pd.DataFrame(
            {"flake_id": [int(x) for x in selected_flake_ids]}
        ).to_parquet(sel_path, engine="pyarrow", index=False)

    outputs: Dict[str, str] = {
        "explorer_state_json": "06_explorer/explorer_state.json",
    }
    if sel_path is not None:
        outputs["selected_flakes_parquet"] = "06_explorer/selected_flakes.parquet"

    stamp_top_level(manifest, analysis_folder=analysis_folder)
    manifest.steps["explorer"] = StepEntry(
        completed_at=saved_at,
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "clustering_params_hash": cluster_entry.params_hash,
            "domain_proximity_params_hash": proximity_entry.params_hash,
        },
        outputs=outputs,
    )
    save_manifest(manifest, analysis_folder)

    return {
        "state_path": str(state_path),
        "selected_count": len(selected_flake_ids) if selected_flake_ids is not None else None,
    }


def load_explorer_state(analysis_folder: str | Path) -> Optional[Dict[str, Any]]:
    """Load saved explorer state, or None if not yet committed."""
    p = step_dir(analysis_folder, "explorer") / "explorer_state.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
