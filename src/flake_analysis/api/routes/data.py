"""Data read endpoints per backend design §1.3."""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
from fastapi import APIRouter, Depends, Header, Response

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import (
    AnnotationsPathUnset,
    ClusteringNotFitted,
    DomainNotFound,
    DomainStatsNotFound,
    SelectionNotFound,
)
from flake_analysis.api.schemas.data import ManifestModel
from flake_analysis.api.services.annotation_preview import load_preview
from flake_analysis.api.services.arrow_writer import arrow_or_json_response
from flake_analysis.api.services.clustering_service import (
    load_assignments_table,
    load_labels_json,
    load_seed_groups,
)
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}/data", tags=["data"])


@router.get("/manifest")
async def get_manifest_endpoint(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> ManifestModel:
    """Return manifest as JSON."""
    return ManifestModel.model_validate(manifest)


def _load_stats_table(analysis_folder: str | Path) -> pa.Table:
    npz_path = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    if not npz_path.exists():
        raise DomainStatsNotFound(path=str(npz_path))
    z = np.load(npz_path, allow_pickle=False)
    flake_ids = z["flake_ids"].astype(np.int64)
    repr_rgbs = z["repr_rgbs"].astype(np.float64)
    std_pcts = z["std_pcts"].astype(np.float64)
    areas = z["areas"].astype(np.float64)

    cols: dict[str, pa.Array] = {
        "flake_ids": pa.array(flake_ids, type=pa.int64()),
        "mean_r": pa.array(repr_rgbs[:, 0], type=pa.float64()),
        "mean_g": pa.array(repr_rgbs[:, 1], type=pa.float64()),
        "mean_b": pa.array(repr_rgbs[:, 2], type=pa.float64()),
        "std_r": pa.array(std_pcts[:, 0], type=pa.float64()),
        "std_g": pa.array(std_pcts[:, 1], type=pa.float64()),
        "std_b": pa.array(std_pcts[:, 2], type=pa.float64()),
        "areas": pa.array(areas, type=pa.float64()),
    }
    if "sam2" in z.files:
        cols["sam2"] = pa.array(z["sam2"].astype(np.float64), type=pa.float64())
    return pa.table(cols)


@router.get("/domain_stats")
async def get_domain_stats(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return domain stats arrays (Arrow IPC if Accept: application/vnd.apache.arrow.stream, else JSON)."""
    table = _load_stats_table(manifest.analysis_folder)
    return arrow_or_json_response(table, accept_header=accept)


@router.get("/selector/selection")
async def get_selection(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return 03_selector/selection.parquet rows (Arrow IPC or JSON column-oriented)."""
    p = Path(manifest.analysis_folder) / "03_selector" / "selection.parquet"
    if not p.exists():
        raise SelectionNotFound(path=str(p))
    df = pd.read_parquet(p)
    table = pa.Table.from_pandas(df, preserve_index=False)
    return arrow_or_json_response(table, accept_header=accept)


@router.get("/annotations/{domain_id}/preview")
async def get_annotation_preview(
    domain_id: int,
    with_contour: bool = False,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return PNG crop around ``domain_id`` (optionally with red contour overlay)."""
    if not manifest.annotations_path:
        raise AnnotationsPathUnset()
    try:
        png = load_preview(
            annotations_path=manifest.annotations_path,
            raw_images_dir=manifest.raw_images_dir,
            domain_id=domain_id,
            with_contour=with_contour,
        )
    except KeyError as e:
        raise DomainNotFound(domain_id=domain_id, reason=str(e))
    return Response(content=png, media_type="image/png")


@router.get("/clustering/labels")
async def get_clustering_labels(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return labels.json as JSON."""
    try:
        return load_labels_json(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ClusteringNotFitted(expected_path=str(e).split("missing at ", 1)[-1])


@router.get("/clustering/assignments")
async def get_clustering_assignments(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
    accept: str | None = Header(default=None),
):
    """Return 04_clustering/assignments.parquet (Arrow IPC if Accept: application/vnd.apache.arrow.stream, else JSON)."""
    try:
        table = load_assignments_table(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ClusteringNotFitted(expected_path=str(e).split("missing at ", 1)[-1])
    return arrow_or_json_response(table, accept_header=accept)
