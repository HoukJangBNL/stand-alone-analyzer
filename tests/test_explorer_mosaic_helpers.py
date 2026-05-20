"""Unit tests for the v0.2.15 Explorer substrate-mosaic helpers.

Exercises the pure helpers (parsing + LOD selection + layout fallback)
without spinning up Streamlit. The mosaic-array build itself is
exercised by the Compute → Explorer end-to-end in tests/parity.
"""
from __future__ import annotations

from flake_analysis.ui.tab_explorer import (
    _build_grid_layout,
    _choose_lod,
    _parse_grid_coord,
)


def test_parse_grid_coord_standard_name():
    assert _parse_grid_coord("ix003_iy017.png") == (3, 17)
    assert _parse_grid_coord("ix000_iy000.png") == (0, 0)


def test_parse_grid_coord_returns_none_on_unrecognised():
    assert _parse_grid_coord("foo.png") is None
    assert _parse_grid_coord("") is None


def test_choose_lod_thresholds():
    # 1.5× cached LOD widths: 96, 288, 720.
    assert _choose_lod(50) == 0
    assert _choose_lod(96) == 0
    assert _choose_lod(97) == 1
    assert _choose_lod(288) == 1
    assert _choose_lod(289) == 2
    assert _choose_lod(720) == 2
    assert _choose_lod(721) == 3  # implicit raw LOD
    assert _choose_lod(2000) == 3


def test_build_grid_layout_uses_filenames_when_all_parse():
    image_ids = [10, 11, 12, 13]
    name_map = {
        10: "ix000_iy000.png",
        11: "ix001_iy000.png",
        12: "ix000_iy001.png",
        13: "ix001_iy001.png",
    }
    grid_w, grid_h, coords = _build_grid_layout(image_ids, name_map)
    assert (grid_w, grid_h) == (2, 2)
    assert coords[10] == (0, 0)
    assert coords[11] == (1, 0)
    assert coords[12] == (0, 1)
    assert coords[13] == (1, 1)


def test_build_grid_layout_normalises_origin():
    image_ids = [1, 2]
    name_map = {1: "ix005_iy010.png", 2: "ix006_iy010.png"}
    grid_w, grid_h, coords = _build_grid_layout(image_ids, name_map)
    # Normalised so (5,10) and (6,10) become (0,0) and (1,0).
    assert (grid_w, grid_h) == (2, 1)
    assert coords[1] == (0, 0)
    assert coords[2] == (1, 0)


def test_build_grid_layout_falls_back_to_square_on_unparseable_names():
    image_ids = [0, 1, 2, 3]
    name_map = {0: "a.png", 1: "b.png", 2: "c.png", 3: "d.png"}
    grid_w, grid_h, coords = _build_grid_layout(image_ids, name_map)
    # Square layout; first row covers the first grid_w ids.
    assert grid_w * grid_h >= len(image_ids)
    assert coords[0] == (0, 0)
