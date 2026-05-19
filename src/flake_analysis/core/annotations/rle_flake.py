"""RLEFlake class - Flake with RLE mask decoding from annotations.json.

This class provides the same interface as Flake but loads masks from
RLE-encoded segmentation data instead of PNG files, providing ~5x faster
mask loading performance.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from flake_analysis.core.annotations.annotation_loader import AnnotationsCache, FlakeMetadata


class RLEFlake:
    """Flake with RLE mask decoding - same interface as Flake.

    Uses RLE-encoded segmentation from annotations.json for ~5x faster
    mask loading compared to PNG files. Stores only references to metadata
    and cache to minimize memory footprint.

    Parameters
    ----------
    raw_path : Path
        Path to the raw image file
    metadata : FlakeMetadata
        Metadata from annotations.json containing RLE segmentation
    annotations_cache : AnnotationsCache
        Cache object that provides RLE decoding functionality
    """

    def __init__(
        self,
        raw_path: Path,
        metadata: "FlakeMetadata",
        annotations_cache: "AnnotationsCache",
    ):
        self.raw_path = Path(raw_path)
        self._metadata = metadata
        self._cache = annotations_cache

        # Lazy-loaded cached values
        self._mask: Optional[np.ndarray] = None
        self._mask_binary: Optional[np.ndarray] = None
        self._raw_image: Optional[np.ndarray] = None
        self._mean_rgb: Optional[np.ndarray] = None
        self._std_rgb: Optional[np.ndarray] = None

    @property
    def raw_name(self) -> str:
        """Name of raw image file (without extension)."""
        return self.raw_path.stem

    @property
    def mask(self) -> np.ndarray:
        """Binary mask array, lazy-loaded from RLE.

        Returns mask as uint8 array with values 0 or 255.
        RLE data is required - PNG fallback removed for performance.
        """
        if self._mask is None:
            # RLE decode only - PNG fallback removed for performance
            rle_mask = self._cache.decode_rle_mask(self._metadata)
            if rle_mask is not None:
                self._mask = rle_mask
            else:
                # No PNG fallback - return empty mask and log warning
                from flake_analysis.core._compat import msg
                msg.warning(f"[RLEFlake] No RLE data for {self.raw_name}, returning empty mask")
                raw_shape = self.raw_image.shape[:2]  # (H, W)
                self._mask = np.zeros(raw_shape, dtype=np.uint8)
        return self._mask

    @property
    def mask_binary(self) -> np.ndarray:
        """Binary mask as boolean array (cached for performance)."""
        if self._mask_binary is None:
            mask = self.mask
            self._mask_binary = np.any(mask > 0, axis=2) if mask.ndim == 3 else mask > 0
        return self._mask_binary

    @property
    def raw_image(self) -> np.ndarray:
        """Raw image array, lazy-loaded."""
        if self._raw_image is None:
            self._raw_image = np.array(Image.open(self.raw_path))
        return self._raw_image

    @property
    def pixels(self) -> np.ndarray:
        """Pixel values where mask is non-zero. Shape: (N, 3) for RGB."""
        return self.raw_image[self.mask_binary]

    @property
    def mean_rgb(self) -> np.ndarray:
        """Mean RGB values [R, G, B]."""
        if self._mean_rgb is None:
            pixels = self.pixels
            if len(pixels) > 0:
                self._mean_rgb = pixels.mean(axis=0)
            else:
                self._mean_rgb = np.array([0.0, 0.0, 0.0])
        return self._mean_rgb

    @property
    def std_rgb(self) -> np.ndarray:
        """Standard deviation of RGB values [R, G, B]."""
        if self._std_rgb is None:
            pixels = self.pixels
            if len(pixels) > 0:
                self._std_rgb = pixels.std(axis=0)
            else:
                self._std_rgb = np.array([0.0, 0.0, 0.0])
        return self._std_rgb

    @property
    def std_mean(self) -> float:
        """Mean of RGB standard deviations (single value for filtering)."""
        return float(self.std_rgb.mean())

    @property
    def bbox(self) -> tuple:
        """Bounding box (y_min, y_max, x_min, x_max).

        Uses pre-computed bbox from annotations.json (required).
        Mask-based fallback removed for performance.
        """
        # bbox_coco is required from annotations.json
        if self._metadata.bbox_coco:
            x, y, w, h = self._metadata.bbox_coco
            x_min, y_min = int(x), int(y)
            x_max, y_max = int(x + w - 1), int(y + h - 1)
            return (y_min, y_max, x_min, x_max)

        # No fallback - bbox_coco must exist in annotations.json
        raise ValueError(f"bbox_coco missing for flake {self.flake_id}")

    @property
    def raw_region(self) -> np.ndarray:
        """Cropped raw image region based on bbox."""
        y_min, y_max, x_min, x_max = self.bbox
        return self.raw_image[y_min : y_max + 1, x_min : x_max + 1]

    @property
    def image(self) -> Image.Image:
        """Display bbox cropped image as PIL Image."""
        return Image.fromarray(self.raw_region)

    @property
    def area(self) -> int:
        """Pixel count of the flake (from metadata)."""
        return self._metadata.area

    @property
    def score(self) -> float:
        """SAM2 confidence score (from metadata)."""
        return self._metadata.score

    @property
    def flake_id(self) -> int:
        """COCO annotation ID."""
        return self._metadata.flake_id

    def color_ratio(
        self,
        r_range: tuple = (0, 255),
        g_range: tuple = (0, 255),
        b_range: tuple = (0, 255),
    ) -> float:
        """Calculate ratio of pixels within RGB range.

        Parameters
        ----------
        r_range : tuple
            (min, max) for red channel
        g_range : tuple
            (min, max) for green channel
        b_range : tuple
            (min, max) for blue channel

        Returns
        -------
        float
            Ratio of pixels within range (0.0 to 1.0)
        """
        pixels = self.pixels
        if len(pixels) == 0:
            return 0.0

        in_range = (
            (pixels[:, 0] >= r_range[0])
            & (pixels[:, 0] <= r_range[1])
            & (pixels[:, 1] >= g_range[0])
            & (pixels[:, 1] <= g_range[1])
            & (pixels[:, 2] >= b_range[0])
            & (pixels[:, 2] <= b_range[1])
        )
        return float(np.sum(in_range) / len(pixels))

    def clear_cache(self):
        """Clear cached images to free memory."""
        self._mask = None
        self._mask_binary = None
        self._raw_image = None
        self._mean_rgb = None
        self._std_rgb = None

    def __repr__(self):
        return f"RLEFlake({self.raw_name}, id={self.flake_id})"
