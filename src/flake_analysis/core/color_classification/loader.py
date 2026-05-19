"""Flake loading + per-domain color stats computation.

Extracted from Qpress ``modules/analyzer/color_based_classification/loader.py``.
The only edits are import shims (msg + image_processing) — algorithmic core
preserved verbatim so caches written by Qpress and standalone are
byte-identical (NPZ schema: repr_rgbs, std_pcts, areas, flake_ids).
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from PIL import Image

from flake_analysis.core._compat import msg
from flake_analysis.core.image_processing.background import get_median_background, save_background


def _compute_match_for_flake_group(args: tuple) -> list:
    """Compute color match% for all flakes sharing the same raw image.

    This avoids redundant PNG loading by loading raw image once per group.
    Reduces ~8000 PNG loads to ~500 (one per raw image) for typical datasets.

    Args:
        args: (raw_path, flakes_with_indices, rgb_filter_range,
               median_background, background_mode)

        flakes_with_indices: list of (flake_idx, flake) tuples
        rgb_filter_range: {'r': (min, max), 'g': (min, max), 'b': (min, max)}

    Returns:
        list of (flake_idx, match_pct) tuples
    """
    raw_path, flakes_with_indices, rgb_filter_range, median_background, background_mode = args

    try:
        raw_image = np.array(Image.open(raw_path))
        if background_mode == "median" and median_background is not None:
            raw_image = raw_image.astype(np.float64)
            bg_safe = np.where(median_background > 0, median_background, 1)
    except Exception as e:
        msg.debug(f"[MATCH] Error loading raw image {raw_path}: {e}")
        return [(idx, 0.0) for idx, _ in flakes_with_indices]

    r_min, r_max = rgb_filter_range['r']
    g_min, g_max = rgb_filter_range['g']
    b_min, b_max = rgb_filter_range['b']

    results = []
    for flake_idx, flake in flakes_with_indices:
        try:
            y_min, y_max, x_min, x_max = flake.bbox
            if y_max <= y_min or x_max <= x_min:
                results.append((flake_idx, 0.0))
                continue

            mask_2d = flake.mask_binary

            # Crop to bbox (avoids full-array boolean indexing)
            raw_crop = raw_image[y_min:y_max+1, x_min:x_max+1]
            mask_crop = mask_2d[y_min:y_max+1, x_min:x_max+1]

            if background_mode == "median" and median_background is not None:
                bg_crop = bg_safe[y_min:y_max+1, x_min:x_max+1]
                raw_pixels = raw_crop[mask_crop].astype(np.float64)
                bg_pixels = bg_crop[mask_crop]

                with np.errstate(divide='ignore', invalid='ignore'):
                    pixels = np.where(bg_pixels > 0, raw_pixels / bg_pixels, 0.0)
                    pixels = np.nan_to_num(pixels, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                pixels = raw_crop[mask_crop].astype(np.float64) / 255.0

            if len(pixels) == 0:
                results.append((flake_idx, 0.0))
                continue

            r_match = (pixels[:, 0] >= r_min) & (pixels[:, 0] <= r_max)
            g_match = (pixels[:, 1] >= g_min) & (pixels[:, 1] <= g_max)
            b_match = (pixels[:, 2] >= b_min) & (pixels[:, 2] <= b_max)

            all_match = r_match & g_match & b_match
            pct = np.sum(all_match) / len(pixels) * 100

            flake.clear_cache()
            results.append((flake_idx, pct))

        except Exception:
            results.append((flake_idx, 0.0))

    return results


def _compute_stats_for_flake_group(args: tuple) -> list:
    """Compute stats for all flakes sharing the same raw image.

    This avoids redundant PNG loading by loading raw image once per group.

    Args:
        args: (raw_path, flakes_group, representative_mode, median_background)

    Returns list of tuples: (flake_id, repr_rgb, std_pct, area)
    """
    raw_path, flakes_group, representative_mode, median_background = args

    try:
        raw_image = np.array(Image.open(raw_path))
        if median_background is not None:
            raw_image = raw_image.astype(np.float64)
            bg_safe = np.where(median_background > 0, median_background, 1)
    except Exception as e:
        msg.debug(f"[STATS] Error loading raw image {raw_path}: {e}")
        return []

    results = []
    for flake in flakes_group:
        try:
            # Use flake.mask property (RLEFlake decodes RLE efficiently)
            mask = flake.mask

            # Get binary mask
            if mask.ndim == 3:
                mask_binary = np.any(mask > 0, axis=2)
            else:
                mask_binary = mask > 0

            # Calculate area
            area = int(np.sum(mask_binary))

            if median_background is not None:
                # Vignetting correction mode
                raw_pixels = raw_image[mask_binary]
                bg_pixels = bg_safe[mask_binary]

                if len(raw_pixels) == 0:
                    continue

                corrected_pixels = raw_pixels / bg_pixels

                if representative_mode == "median":
                    repr_rgb = np.median(corrected_pixels, axis=0)
                else:
                    repr_rgb = corrected_pixels.mean(axis=0)

                std_rgb = corrected_pixels.std(axis=0)
            else:
                # Raw mode (no correction)
                pixels = raw_image[mask_binary]

                if len(pixels) == 0:
                    continue

                if representative_mode == "median":
                    repr_rgb = np.median(pixels, axis=0)
                else:
                    repr_rgb = pixels.mean(axis=0)

                std_rgb = pixels.std(axis=0)

            # Calculate std as percentage
            repr_safe = np.where(repr_rgb > 0, repr_rgb, 1)
            std_pct = (std_rgb / repr_safe) * 100

            # Clear cache to free memory
            flake.clear_cache()

            results.append((flake.flake_id, repr_rgb, std_pct, area))

        except Exception as e:
            msg.debug(f"[STATS] Error computing stats for flake: {e}")
            continue

    return results


def compute_and_cache_stats_from_flakes(
    flakes: list,
    cache_dir: Path,
    raw_image_folder: Path = None,
    background_mode: Literal["raw", "median"] = "median",
    representative_mode: Literal["median", "mean"] = "median",
    force_recompute: bool = False,
    raw_ext: str = ".png",
    progress_callback=None,
    background_image: Optional[np.ndarray] = None,
) -> dict:
    """Compute flake statistics using the Flake interface (supports RLEFlake).

    This function uses the flake.mask property which allows RLEFlake objects
    to decode masks from RLE (5x faster than PNG loading).

    Parameters
    ----------
    flakes : list
        List of RLEFlake objects
    cache_dir : Path
        Directory to save cache file (segmentation/data/)
    raw_image_folder : Path, optional
        Directory containing raw images. Required for median background_mode.
    background_mode : str
        'raw' - use raw pixel values (no vignetting correction)
        'median' - apply vignetting correction using median background
    representative_mode : str
        'median' - use median pixel value per channel (default, robust)
        'mean' - use average pixel value per channel
    force_recompute : bool
        If True, ignore cache and recompute
    raw_ext : str
        Extension for raw images (for background computation)
    progress_callback : callable, optional
        Function(current, total, message) for progress updates
    background_image : np.ndarray, optional
        Pre-loaded background image for normalization. If provided, overrides
        background_mode and uses this image directly for vignetting correction.

    Returns
    -------
    dict
        Dictionary with 'repr_rgbs', 'std_pcts', 'areas' arrays
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / f"flake_stats_{background_mode}_{representative_mode}.npz"
    bg_file = cache_dir / "median_background.png"

    # Check cache
    if cache_file.exists() and not force_recompute:
        msg.info(f"[STATS] Loading cached stats from {cache_file}")
        data = np.load(cache_file, allow_pickle=True)

        # flake_ids 키가 있는 새 캐시만 사용 (레거시 mask_paths 캐시는 miss)
        # Also require sam2 — older caches predate the sam2 column and we need
        # to invalidate them so the rewrite below populates sam2.
        if "flake_ids" in data and "sam2" in data:
            cached_ids = set(data["flake_ids"])
            current_ids = {f.flake_id for f in flakes}

            if cached_ids == current_ids:
                msg.info(f"[STATS] Cache hit - loaded {len(flakes)} flake stats")

                # 병렬 처리 결과를 flakes 리스트 순서에 맞게 재정렬
                cached_flake_ids = data["flake_ids"]
                id_to_idx = {int(fid): i for i, fid in enumerate(cached_flake_ids)}

                order = [id_to_idx[f.flake_id] for f in flakes]
                repr_rgbs = data["repr_rgbs"][order]
                std_pcts = data["std_pcts"][order]
                areas = data["areas"][order] if "areas" in data else None

                msg.info(f"[STATS] Reordered stats to match flakes list")
                return {
                    "repr_rgbs": repr_rgbs,
                    "std_pcts": std_pcts,
                    "areas": areas,
                }

        msg.info("[STATS] Cache mismatch (or pre-sam2 cache), recomputing...")

    # Load or compute median background
    median_background = None
    if background_image is not None:
        # Use explicitly provided background (e.g., flatfield from segmentation)
        median_background = background_image.astype(np.float64) if background_image.dtype != np.float64 else background_image
        msg.info(f"[STATS] Using provided background image for normalization")
    elif background_mode == "median":
        if raw_image_folder is None:
            raise ValueError("raw_image_folder is required for median background_mode")

        raw_image_folder = Path(raw_image_folder)

        # Check backgrounds folder first (primary storage)
        backgrounds_dir = raw_image_folder.parent / "backgrounds"
        bg_primary = backgrounds_dir / "median_background.png"

        if bg_primary.exists():
            msg.info(f"[STATS] Loading background from {bg_primary}")
            median_background = np.array(Image.open(bg_primary)).astype(np.float64)
        elif bg_file.exists() and not force_recompute:
            msg.info(f"[STATS] Loading cached background from {bg_file}")
            median_background = np.array(Image.open(bg_file)).astype(np.float64)
        else:
            msg.info("[STATS] Computing median background...")
            file_pattern = f"[!._]*{raw_ext}"
            median_background = get_median_background(
                raw_images_dir=raw_image_folder,
                max_images=300,
                random_sample=True,
                file_pattern=file_pattern,
            )
            save_background(median_background, bg_file)
            msg.info(f"[STATS] Saved background to {bg_file}")

    msg.info(f"[STATS] Computing stats for {len(flakes)} flakes using Flake interface")

    # Group flakes by raw_path to avoid redundant PNG loading
    raw_to_flakes = defaultdict(list)
    for flake in flakes:
        raw_to_flakes[str(flake.raw_path)].append(flake)

    num_groups = len(raw_to_flakes)
    msg.info(f"[STATS] Grouped into {num_groups} raw images (avg {len(flakes)/num_groups:.1f} flakes/image)")

    repr_rgbs = []
    std_pcts = []
    areas = []
    flake_ids = []

    total = len(flakes)
    use_tqdm = progress_callback is None

    args_list = [
        (raw_path, flake_group, representative_mode, median_background)
        for raw_path, flake_group in raw_to_flakes.items()
    ]

    completed_flakes = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_compute_stats_for_flake_group, args): args[0] for args in args_list}

        if use_tqdm:
            try:
                from tqdm import tqdm
                pbar = tqdm(total=total, desc=f"Processing (bg={background_mode})")
            except ImportError:
                pbar = None
                use_tqdm = False
        else:
            pbar = None

        for future in as_completed(futures):
            results = future.result()
            for fid, repr_rgb, std_pct, area in results:
                repr_rgbs.append(repr_rgb)
                std_pcts.append(std_pct)
                areas.append(area)
                flake_ids.append(fid)

            batch_size = len(results)
            completed_flakes += batch_size
            if use_tqdm and pbar is not None:
                pbar.update(batch_size)
            elif progress_callback and completed_flakes % 500 < batch_size:
                progress_callback(completed_flakes, total, f"Computing stats... {completed_flakes}/{total}")

        if use_tqdm and pbar is not None:
            pbar.close()

    if progress_callback:
        progress_callback(total, total, f"Computing stats... {total}/{total}")

    repr_rgbs = np.array(repr_rgbs)
    std_pcts = np.array(std_pcts)
    areas = np.array(areas, dtype=np.int32)
    flake_ids = np.array(flake_ids, dtype=np.int64)

    # 병렬 처리 결과를 flakes 리스트 순서에 맞게 재정렬
    # (as_completed()는 완료 순서로 반환하므로 원래 순서와 다름)
    id_to_idx = {int(fid): i for i, fid in enumerate(flake_ids)}
    order = [id_to_idx[f.flake_id] for f in flakes]
    repr_rgbs = repr_rgbs[order]
    std_pcts = std_pcts[order]
    areas = areas[order]
    flake_ids = flake_ids[order]

    # SAM2 confidence score from each flake's metadata. Stored alongside the
    # other per-flake arrays so the standalone Selector tab can filter on it
    # (otherwise the sam2 column was always zeros). Default 1.0 if missing
    # (matches annotation_loader's fallback at load time).
    def _flake_score(f) -> float:
        # RLEFlake exposes its FlakeMetadata as _metadata; older or fixture
        # objects may have a public .metadata. Fall back to score=1.0 if
        # neither carries one (matches annotation_loader's default).
        meta = getattr(f, "_metadata", None) or getattr(f, "metadata", None)
        if meta is None:
            return 1.0
        try:
            return float(getattr(meta, "score", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0

    sam2_scores = np.array([_flake_score(f) for f in flakes], dtype=np.float32)

    np.savez(
        cache_file,
        repr_rgbs=repr_rgbs,
        std_pcts=std_pcts,
        areas=areas,
        flake_ids=flake_ids,
        sam2=sam2_scores,
    )
    msg.info(f"[STATS] Saved stats to {cache_file}")

    return {
        "repr_rgbs": repr_rgbs,
        "std_pcts": std_pcts,
        "areas": areas,
    }
