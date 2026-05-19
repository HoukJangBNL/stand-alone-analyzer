"""Verify ``get_median_background`` is reproducible when given a seed."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from flake_analysis.core.image_processing import get_median_background


def _create_fixture_images(tmpdir: Path, n: int) -> None:
    """Create N random RGB test images sized 64x64."""
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
        Image.fromarray(arr).save(tmpdir / f"img_{i:03d}.png")


def test_background_with_seed_is_reproducible() -> None:
    """Same seed + same inputs => identical sample => identical background."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _create_fixture_images(tmp_path, n=20)

        bg1 = get_median_background(
            str(tmp_path), max_images=10, seed=42, gaussian_sigma=0.0
        )
        bg2 = get_median_background(
            str(tmp_path), max_images=10, seed=42, gaussian_sigma=0.0
        )

        np.testing.assert_array_equal(bg1, bg2)


def test_background_different_seeds_differ() -> None:
    """Different seeds typically produce different backgrounds.

    With 30 source images and 10 sampled, the chance of an identical sample
    is negligible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _create_fixture_images(tmp_path, n=30)

        bg1 = get_median_background(
            str(tmp_path), max_images=10, seed=42, gaussian_sigma=0.0
        )
        bg2 = get_median_background(
            str(tmp_path), max_images=10, seed=123, gaussian_sigma=0.0
        )

        assert not np.array_equal(bg1, bg2)


def test_background_seed_none_preserves_legacy_behavior() -> None:
    """seed=None must not raise — keeps backward compatibility."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _create_fixture_images(tmp_path, n=5)

        bg = get_median_background(
            str(tmp_path), max_images=100, seed=None, gaussian_sigma=0.0
        )
        assert bg.shape == (64, 64, 3)
