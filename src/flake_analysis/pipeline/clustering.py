"""App-level pipeline wrapper for Clustering commit.

Wraps ``flake_analysis.core.pipeline.clustering.run_clustering`` and updates
``manifest.json`` with the clustering StepEntry (params, params_hash,
upstream input_hashes, output paths, reproducibility).

Per plan v1 r9 §M2 PR 2.4 + §7.1 frozen labels.json schema.

Notes on the core API contract (read from
``src/flake_analysis/core/pipeline/clustering.py``):

* The core ``run_clustering`` signature does NOT take ``feature_cols``
  or ``covariance_type`` — RGB-only fitting is the v1 contract.
  ``random_state=42`` is hard-baked in the engine (plan r6 D6.1).
  These params are recorded in the manifest as wrapper-level metadata
  for forward compatibility / display, even though they are not passed
  through to the engine in v1.

* The core writes ``labels.json``, ``assignments.parquet``, and
  ``gmm_model.pkl``. This wrapper additionally writes
  ``seed_groups.json`` (the user's authored seed groups, kept
  verbatim) — required by plan §6 ``ARTIFACTS["clustering"]``.

* ``assignments.parquet`` columns from core are
  ``domain_id, cluster_label, max_posterior``.
  ``apply_thresholds`` adds a ``threshold_pass`` column (re-evaluated
  per call) without refitting the GMM.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from flake_analysis.core.pipeline.clustering import run_clustering as core_run_clustering

from flake_analysis.state.hashing import params_hash
from flake_analysis.state.manifest import (
    StepEntry,
    load_manifest,
    save_manifest,
    stamp_top_level,
)
from flake_analysis.state.paths import step_dir


ProgressCallback = Callable[[float, str], None]


def run_clustering_step(
    *,
    analysis_folder: str | Path,
    seed_groups: List[Dict[str, Any]],
    feature_cols: Optional[List[str]] = None,
    covariance_type: str = "full",
    random_state: int = 42,
    rgb_threshold: float = 0.50,
    cluster_thresholds: Optional[Dict[int, float]] = None,
    fit_scope: str = "seeds",
    max_mahalanobis: float = 3.0,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Fit manual seed-group GMM and persist outputs.

    Requires both Domain Stats and Selector to be committed.

    Parameters
    ----------
    analysis_folder : str | Path
        Project analysis folder.
    seed_groups : list of dict
        Each entry: ``{"name": str, "domain_ids": list[int]}``.
    feature_cols : list[str], optional
        Wrapper-level metadata (RGB-only fitting in v1; recorded in
        manifest but not forwarded to the core engine).
    covariance_type : str
        Wrapper-level metadata; default ``"full"``.
    random_state : int
        Wrapper-level metadata; the engine hard-codes 42 (r6 D6.1).
    rgb_threshold : float
        Initial single posterior cutoff (broadcast to all clusters).
    cluster_thresholds : dict[int, float], optional
        Per-cluster thresholds to record in the manifest. Used for
        downstream ``apply_thresholds`` reruns.

    Returns
    -------
    dict
        Pass-through of core summary plus ``output_dir`` and
        ``params_hash`` for the wrapper-level params.
    """
    if feature_cols is None:
        feature_cols = ["mean_r", "mean_g", "mean_b"]

    manifest = load_manifest(analysis_folder)
    stats_entry = manifest.steps.get("domain_stats")
    selector_entry = manifest.steps.get("selector")
    if stats_entry is None or stats_entry.completed_at is None:
        raise RuntimeError(
            "Domain Stats step not completed. Run Compute → Domain Stats first."
        )
    if selector_entry is None or selector_entry.completed_at is None:
        raise RuntimeError(
            "Selector step not committed. Commit selection first."
        )

    stats_npz = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    selection_pq = Path(analysis_folder) / "03_selector" / "selection.parquet"
    if not stats_npz.exists():
        raise RuntimeError(f"stats.npz missing at {stats_npz}")
    if not selection_pq.exists():
        raise RuntimeError(f"selection.parquet missing at {selection_pq}")

    output_dir = step_dir(analysis_folder, "clustering")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Wrapper-level params (recorded in manifest)
    params: Dict[str, Any] = {
        "seed_groups": seed_groups,
        "feature_cols": feature_cols,
        "covariance_type": covariance_type,
        "random_state": random_state,
        "rgb_threshold": rgb_threshold,
        "fit_scope": fit_scope,
        "max_mahalanobis": max_mahalanobis,
    }
    if cluster_thresholds is not None:
        params["cluster_thresholds"] = {
            str(int(k)): float(v) for k, v in cluster_thresholds.items()
        }

    # Forward only the params accepted by the core API.
    result = core_run_clustering(
        stats_npz_path=stats_npz,
        selection_parquet_path=selection_pq,
        seed_groups=seed_groups,
        output_dir=output_dir,
        rgb_threshold=rgb_threshold,
        fit_scope=fit_scope,
        max_mahalanobis=max_mahalanobis,
        progress_callback=progress_callback,
    )

    # Wrapper-only output: seed_groups.json (verbatim user authoring).
    seed_groups_path = output_dir / "seed_groups.json"
    seed_groups_path.write_text(
        json.dumps(seed_groups, indent=2),
        encoding="utf-8",
    )

    fitted_at = datetime.now(timezone.utc).isoformat()
    stamp_top_level(manifest, analysis_folder=analysis_folder)
    manifest.steps["clustering"] = StepEntry(
        completed_at=fitted_at,
        params=params,
        params_hash=params_hash(params),
        input_hashes={
            "domain_stats_params_hash": stats_entry.params_hash,
            "selector_params_hash": selector_entry.params_hash,
        },
        outputs={
            "labels_json":         "04_clustering/labels.json",
            "assignments_parquet": "04_clustering/assignments.parquet",
            "gmm_model_pkl":       "04_clustering/gmm_model.pkl",
            "seed_groups_json":    "04_clustering/seed_groups.json",
        },
        reproducibility={
            "fitted_at": fitted_at,
            "random_state": random_state,
        },
    )
    save_manifest(manifest, analysis_folder)

    summary: Dict[str, Any] = dict(result)
    summary["output_dir"] = str(output_dir)
    summary["wrapper_params_hash"] = params_hash(params)
    return summary


def apply_thresholds(
    *,
    analysis_folder: str | Path,
    cluster_thresholds: Dict[int, float],
    max_mahalanobis: Optional[float] = None,
) -> Dict[str, Any]:
    """Commit live filter state (thresholds + distance gate) to disk.

    Rewrites ``assignments.parquet`` so that the ``cluster_label`` column
    reflects the current filter state — domains that fail either the
    per-cluster posterior threshold or the optional Mahalanobis distance
    gate are demoted to ``-1``. Downstream Explorer / Domain Proximity
    consume ``cluster_label`` directly, so the live preview the user
    just dialled in becomes the committed clustering.

    Parameters
    ----------
    analysis_folder : str | Path
    cluster_thresholds : dict[int, float]
        Per-cluster posterior cutoffs. Clusters absent from this dict
        fall back to ``0.50``.
    max_mahalanobis : float, optional
        Distance-gate cap (seed-σ units). When provided and the
        parquet has a ``nearest_mahalanobis`` column, points farther
        than this from every cluster centre are also demoted to ``-1``.
        ``None`` leaves the distance gate untouched (recovers the old
        threshold-only behaviour).

    Returns
    -------
    dict
        ``{"n_pass": int, "n_total": int, "n_clusters": int}`` —
        n_pass counts domains with cluster_label >= 0 after the rewrite.
    """
    output_dir = step_dir(analysis_folder, "clustering")
    asn_path = output_dir / "assignments.parquet"
    labels_path = output_dir / "labels.json"
    if not asn_path.exists() or not labels_path.exists():
        raise RuntimeError(
            "Clustering not yet committed; cannot apply thresholds"
        )

    df = pd.read_parquet(asn_path)
    # Core writes column names ``cluster_label`` and ``max_posterior``.
    # Tolerate the alt names from plan §7.1 in case a future core revision aligns.
    cluster_col = "cluster_label" if "cluster_label" in df.columns else "cluster_id"
    posterior_col = (
        "max_posterior" if "max_posterior" in df.columns else "posterior_p"
    )
    has_mah = (
        "nearest_mahalanobis" in df.columns and max_mahalanobis is not None
    )

    norm_thresh = {int(k): float(v) for k, v in cluster_thresholds.items()}

    # Reload the original engine assignment from labels.json so repeated
    # commits don't permanently demote points whose threshold was
    # tightened then loosened. ``labels.json["assignments"]`` was written
    # with the engine's first labelling (cluster_label >= 0); we use that
    # as the base, then re-apply the live filters.
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    base_assign: Dict[int, int] = {
        int(k): int(v) for k, v in (labels.get("assignments") or {}).items()
    }
    # Domain ids in labels.json are only the originally-assigned ones;
    # everything else stays -1.
    df[cluster_col] = df["domain_id"].astype(int).map(base_assign).fillna(-1).astype(int)

    def _new_label(row) -> int:
        cid = int(row[cluster_col])
        if cid < 0:
            return -1
        if has_mah and float(row["nearest_mahalanobis"]) > float(max_mahalanobis):
            return -1
        cutoff = norm_thresh.get(cid, 0.50)
        if float(row[posterior_col]) < cutoff:
            return -1
        return cid

    df[cluster_col] = df.apply(_new_label, axis=1).astype(np.int64)
    # Keep the legacy ``threshold_pass`` boolean for any caller that
    # still reads it; it now means "survived all live filters".
    df["threshold_pass"] = (df[cluster_col] >= 0).astype(bool)
    df.to_parquet(asn_path, engine="pyarrow", index=False)

    # Update labels.json: thresholds + max_mahalanobis + group sizes
    # + the ``assignments`` map (so a future apply_thresholds can use
    # *this* commit as a baseline if the user prefers cumulative
    # behaviour over re-resetting every call). We deliberately do NOT
    # overwrite the original assignments — we keep them as the engine's
    # ground truth so repeated commits stay reversible.
    labels["thresholds"] = {str(k): float(v) for k, v in norm_thresh.items()}
    if max_mahalanobis is not None:
        labels["max_mahalanobis"] = float(max_mahalanobis)
    # Refresh per-group sizes to match the new cluster_label distribution.
    for group in labels.get("groups", []):
        cid = int(group.get("id", -1))
        if cid >= 0:
            group["size"] = int((df[cluster_col] == cid).sum())
    labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")

    # Update manifest params.cluster_thresholds + max_mahalanobis.
    manifest = load_manifest(analysis_folder)
    if "clustering" in manifest.steps:
        entry = manifest.steps["clustering"]
        entry.params["cluster_thresholds"] = {
            str(k): float(v) for k, v in norm_thresh.items()
        }
        if max_mahalanobis is not None:
            entry.params["max_mahalanobis"] = float(max_mahalanobis)
        # Refresh wrapper-level params_hash to reflect the threshold update.
        entry.params_hash = params_hash(entry.params)
        save_manifest(manifest, analysis_folder)

    n_pass = int((df[cluster_col] >= 0).sum())
    return {
        "n_pass": n_pass,
        "n_total": int(len(df)),
        "n_clusters": int(labels.get("n_clusters", 0)),
    }
