"""Explorer schemas per backend design §1.2 + §1.3 + mosaic-viewer §3-§4."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class TileManifestEntry(BaseModel):
    image_id: int
    stem: str
    col: int = Field(ge=0)
    row: int = Field(ge=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    # LOD index (str) -> [w, h] pixel size of the cached thumbnail.
    lod_sizes: dict[str, list[int]]


class TileManifest(BaseModel):
    grid_w: int = Field(gt=0, le=60)  # Pinned decision #7: 60×60 cap
    grid_h: int = Field(gt=0, le=60)
    lod_sizes: dict[str, list[int]]
    signature: list[str]
    params_hash: str
    tiles: list[TileManifestEntry]


class ExplorerFlakeRow(BaseModel):
    flake_id: int
    image_id: int
    domains: int = Field(ge=0)
    groups: str
    distance: str
    clipped: str
    # 'pass' is a Python keyword — quote it on construction.
    # serialize_by_alias=True makes model_dump() emit "pass" (not "pass_").
    model_config = {"populate_by_name": True, "serialize_by_alias": True}
    pass_: bool = Field(alias="pass")


class ExplorerFlakesResponse(BaseModel):
    rows: list[ExplorerFlakeRow]
    total: int = Field(ge=0)


class ExplorerFlakeDetail(BaseModel):
    flake_id: int
    image_id: int
    domain_ids: list[int]
    cluster_names: list[str]
    bbox_xy: list[int]  # [x, y, w, h]
    mask_stats: dict[str, float]
    distance_px: Optional[float] = None
    isolation_px: Optional[float] = None


class NeighborFilterParams(BaseModel):
    size_min: Optional[int] = Field(default=None, ge=1)
    size_max: Optional[int] = Field(default=None, ge=1)
    isolation_min: Optional[float] = Field(default=None, ge=0.0)
    exclude_border_clipped: bool = False


class SaveExplorerStateParams(BaseModel):
    include_labels: list[str]
    exclude_labels: list[str]
    neighbor_filter: NeighborFilterParams
    selected_flake_ids: Optional[list[int]] = None


class SaveExplorerStateResult(BaseModel):
    state_path: str
    selected_count: Optional[int] = None
