"""Background image calculation utilities."""

import random
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from flake_analysis.core._compat import ProgressCallback, msg


def get_median_background(
    raw_images_dir: Union[str, Path] = None,
    image_files: List[Path] = None,
    max_images: int = 100,
    file_pattern: str = "[!._]*.png",
    random_sample: bool = True,
    gaussian_sigma: float = 10.0,
    method: str = "median",
    seed: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> np.ndarray:
    """
    Calculate median background from raw images for vignetting correction.

    Computes pixel-wise median across all images, which is more robust to
    outliers (e.g., flakes) than mean. Applies Gaussian smoothing to reduce noise.

    Parameters
    ----------
    raw_images_dir : str or Path, optional
        Directory containing raw images. Either this or image_files must be provided.
    image_files : list of Path, optional
        List of image file paths. Either this or raw_images_dir must be provided.
    max_images : int, optional
        Maximum number of images to use. Default: 100
    file_pattern : str, optional
        Glob pattern for image files. Default: "[!._]*.png"
    random_sample : bool, optional
        If True, randomly sample images. If False, use first N images. Default: True
    gaussian_sigma : float, optional
        Sigma for Gaussian smoothing. Higher = more smoothing. Default: 10.0
    method : str, optional
        Aggregation method: "median" (default) or "mean".
    seed : int, optional
        Reproducibility seed for random sampling. When ``random_sample=True`` and
        ``len(image_files) > max_images``:
          - If ``seed is None`` (default): bare ``random.sample`` is used —
            non-deterministic, matches the legacy Qpress behavior.
          - If ``seed`` is an integer: a dedicated ``random.Random(seed)`` instance
            is used so the sample is fully reproducible across runs.
        This parameter was added during the standalone extraction (M1 PR 1.1) to
        fix the upstream reproducibility gap noted in the cycle 1 vision-specialist
        finding (``background.py:78``).

    Returns
    -------
    np.ndarray
        Median background image (float64 for correction, preserves precision)

    Notes
    -----
    Memory usage: ~12MB per image (2000x2000x3 uint8), ~3.6GB for 300 images.
    Images are loaded as uint8 to minimize memory, then median is computed.

    Example
    -------
    >>> from flake_analysis.core.image_processing import get_median_background
    >>> background = get_median_background("/path/to/rawImages", seed=42)
    """
    # Get image files
    if image_files is None:
        if raw_images_dir is None:
            raise ValueError("Either raw_images_dir or image_files must be provided")

        raw_images_dir = Path(raw_images_dir)
        if not raw_images_dir.exists():
            raise FileNotFoundError(f"Directory not found: {raw_images_dir}")

        image_files = list(raw_images_dir.glob(file_pattern))

        if not image_files:
            raise ValueError(
                f"No images found matching pattern '{file_pattern}' in {raw_images_dir}"
            )

    # Sort for deterministic ordering before sampling — ensures the input order
    # to ``random.sample`` is stable across filesystem implementations. Without
    # this, two runs with the same seed could still diverge if ``Path.glob``
    # returned files in different order.
    image_files = sorted(image_files)

    # Sample images
    if len(image_files) > max_images:
        if random_sample:
            if seed is not None:
                rng = random.Random(seed)
                image_files = rng.sample(image_files, max_images)
            else:
                image_files = random.sample(image_files, max_images)
        else:
            image_files = image_files[:max_images]

    # Load all images into memory for median calculation
    total = len(image_files)
    msg.info(f"Loading {total} images for median background...")
    # Per-image progress emission. Bound the cadence so we don't spam the
    # callback on tiny inputs (1 emit per ~10% of work, min every image).
    emit_every = max(1, total // 10)
    images = []
    for i, file in enumerate(image_files):
        img_arr = np.array(Image.open(file))  # uint8 to save memory
        images.append(img_arr)
        if (i + 1) % 20 == 0:
            msg.debug(f"  Loaded {i + 1}/{total} images")
        if progress_callback is not None and ((i + 1) % emit_every == 0 or (i + 1) == total):
            # Loading occupies ~70% of the wall-clock for typical inputs.
            load_pct = 0.7 * float(i + 1) / float(total)
            progress_callback(load_pct, f"Loaded {i + 1}/{total} images")
    msg.debug(f"  Loaded {total}/{total} images")

    # Stack images and compute pixel-wise aggregation
    # Shape: (N, H, W, C) -> aggregate along axis 0 -> (H, W, C)
    stacked = np.stack(images, axis=0)
    del images  # Free memory
    if method == "mean":
        msg.info("Computing pixel-wise mean...")
        if progress_callback is not None:
            progress_callback(0.75, "Computing pixel-wise mean...")
        result_image = np.mean(stacked, axis=0).astype(np.float64)
    else:
        msg.info("Computing pixel-wise median...")
        if progress_callback is not None:
            progress_callback(0.75, "Computing pixel-wise median...")
        result_image = np.median(stacked, axis=0).astype(np.float64)
    del stacked  # Free memory

    # Apply Gaussian smoothing to reduce noise
    if gaussian_sigma and gaussian_sigma > 0:
        msg.debug(f"Applying Gaussian smoothing (sigma={gaussian_sigma})...")
        if progress_callback is not None:
            progress_callback(0.9, f"Applying Gaussian smoothing (sigma={gaussian_sigma})...")
        if result_image.ndim == 3:
            for c in range(result_image.shape[2]):
                result_image[:, :, c] = gaussian_filter(result_image[:, :, c], sigma=gaussian_sigma)
        else:
            result_image = gaussian_filter(result_image, sigma=gaussian_sigma)

    msg.info("Background calculation complete.")

    return result_image


def save_background(background: np.ndarray, save_path: Union[str, Path]) -> None:
    """
    Save background image to file.

    Parameters
    ----------
    background : np.ndarray
        Background image (float64 or uint8)
    save_path : str or Path
        Path to save the image
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to uint8 for saving
    if background.dtype != np.uint8:
        bg_uint8 = np.clip(background, 0, 255).astype(np.uint8)
    else:
        bg_uint8 = background

    Image.fromarray(bg_uint8).save(save_path)
    msg.info(f"Saved background to {save_path}")
