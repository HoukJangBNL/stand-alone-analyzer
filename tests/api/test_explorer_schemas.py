import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.explorer import (
    TileManifestEntry,
    TileManifest,
    ExplorerFlakeRow,
    ExplorerFlakesResponse,
    ExplorerFlakeDetail,
    NeighborFilterParams,
    SaveExplorerStateParams,
    SaveExplorerStateResult,
)


def test_tile_manifest_entry_round_trip():
    e = TileManifestEntry(
        image_id=7, stem="ix003_iy017", col=3, row=2,
        width_px=2048, height_px=1536,
        lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
    )
    assert e.image_id == 7
    assert e.row == 2
    assert e.lod_sizes["1"] == [192, 144]


def test_tile_manifest_signature_and_tiles():
    m = TileManifest(
        grid_w=4, grid_h=3,
        lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
        signature=["sig0", "sig1"],
        params_hash="abc123",
        tiles=[
            TileManifestEntry(
                image_id=1, stem="ix000_iy000", col=0, row=2,
                width_px=2048, height_px=1536,
                lod_sizes={"0": [64, 48], "1": [192, 144], "2": [480, 360]},
            ),
        ],
    )
    assert m.grid_w == 4
    assert len(m.tiles) == 1
    assert m.tiles[0].col == 0


def test_explorer_flake_row_shape():
    r = ExplorerFlakeRow(
        flake_id=42, image_id=7, domains=3,
        groups="thin, thick", distance="—", clipped="no", **{"pass": True},
    )
    assert r.flake_id == 42
    assert r.model_dump()["pass"] is True


def test_explorer_flakes_response_total_matches_or_exceeds_rows():
    resp = ExplorerFlakesResponse(rows=[], total=0)
    assert resp.total == 0
    resp2 = ExplorerFlakesResponse(
        rows=[ExplorerFlakeRow(
            flake_id=1, image_id=0, domains=1, groups="—",
            distance="—", clipped="no", **{"pass": True})],
        total=5,
    )
    assert resp2.total == 5


def test_explorer_flake_detail_shape():
    d = ExplorerFlakeDetail(
        flake_id=42, image_id=7,
        domain_ids=[100, 101, 102],
        cluster_names=["thin"],
        bbox_xy=[10, 20, 200, 300],
        mask_stats={"area_px": 4500, "perimeter_px": 320.0},
        distance_px=12.5,
        isolation_px=80.0,
    )
    assert d.bbox_xy == [10, 20, 200, 300]


def test_neighbor_filter_params_optional_fields():
    nf = NeighborFilterParams()
    assert nf.size_min is None
    assert nf.size_max is None
    assert nf.isolation_min is None
    assert nf.exclude_border_clipped is False

    nf2 = NeighborFilterParams(size_min=2, size_max=10, isolation_min=80.0,
                               exclude_border_clipped=True)
    assert nf2.size_min == 2
    assert nf2.exclude_border_clipped is True


def test_save_explorer_state_params_minimal():
    p = SaveExplorerStateParams(
        include_labels=["thin"],
        exclude_labels=[],
        neighbor_filter=NeighborFilterParams(size_min=1, size_max=50),
    )
    assert p.selected_flake_ids is None
    assert p.include_labels == ["thin"]


def test_save_explorer_state_params_with_selection():
    p = SaveExplorerStateParams(
        include_labels=[], exclude_labels=["noise"],
        neighbor_filter=NeighborFilterParams(),
        selected_flake_ids=[1, 2, 3],
    )
    assert p.selected_flake_ids == [1, 2, 3]


def test_save_explorer_state_result_shape():
    r = SaveExplorerStateResult(state_path="/tmp/explorer_state.json", selected_count=42)
    assert r.selected_count == 42
    r2 = SaveExplorerStateResult(state_path="/tmp/explorer_state.json", selected_count=None)
    assert r2.selected_count is None


def test_neighbor_filter_rejects_negative_isolation():
    with pytest.raises(ValidationError):
        NeighborFilterParams(isolation_min=-1.0)
