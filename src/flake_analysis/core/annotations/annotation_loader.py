"""
Annotation Loader Module - COCO format annotations.json parsing and caching.

Provides FlakeMetadata and AnnotationsCache for efficient flake metadata lookup
and RLE mask decoding (5x faster than PNG loading).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import time

import numpy as np

from flake_analysis.core._compat import msg

# Import RLEFlake for type hints (avoid circular import with TYPE_CHECKING)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from flake_analysis.core.annotations.rle_flake import RLEFlake


@dataclass
class FlakeMetadata:
    """Metadata for a single flake from annotations.json."""

    flake_id: int              # annotation["id"] - COCO annotation id (1-based)
    image_id: int              # COCO image id
    image_name: str            # "ix000_iy001.png"
    mask_id: int               # mask number (1, 2, 3, ...)
    bbox_coco: Tuple[float, float, float, float]  # [x, y, w, h] COCO format
    area: int                  # pixel count
    score: float               # SAM2 confidence (0.0-1.0)
    rle_segmentation: Optional[Dict] = None  # {size, counts} for RLE decode


class AnnotationsCache:
    """Cache for annotations.json with efficient lookups.

    Provides O(1) lookup by flake_id, plus RLE mask decoding.
    """

    def __init__(self):
        self._annotations: Dict[int, FlakeMetadata] = {}  # flake_id -> metadata
        self._images: Dict[int, dict] = {}  # image_id -> image info
        self._loaded = False
        self._scan_folder: Optional[Path] = None

    @property
    def is_loaded(self) -> bool:
        """Check if annotations are loaded."""
        return self._loaded

    def _is_network_path(self, path: Path) -> bool:
        """Check if path is on a network mount (SMB/NFS/etc).

        Network mounts can have stale file descriptor issues with Python's open().
        Using pathlib read_text() with fresh Path resolution mitigates this.
        """
        path_str = str(path)
        # macOS SMB/NFS mounts
        if path_str.startswith('/Volumes/'):
            return True
        # Linux NFS/CIFS mounts (common patterns)
        if path_str.startswith('/mnt/') or path_str.startswith('/media/'):
            return True
        # UNC paths (Windows-style)
        if path_str.startswith('//'):
            return True
        return False

    def _load_json_via_pathlib(self, path: Path) -> dict:
        """Load JSON file via pathlib to bypass Python FD cache.

        Uses Path.read_text() which opens/reads/closes in one call,
        avoiding stale file descriptor issues on network mounts (Errno 9).
        Cross-platform replacement for the previous subprocess cat approach.
        """
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                # Fresh Path resolution each attempt to avoid stale FD
                resolved = Path(str(path)).resolve()
                text = resolved.read_text(encoding='utf-8')
                return json.loads(text)
            except OSError as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = 2 ** attempt
                    msg.warning(
                        f"[ANNOTATIONS] Network file read failed (attempt {attempt + 1}/{max_retries}): {e} "
                        f"(retrying in {delay}s)"
                    )
                    time.sleep(delay)

        raise last_error

    def _load_json_via_python(self, path: Path) -> dict:
        """Load JSON file via standard Python open().

        Includes retry mechanism for transient errors on local filesystems.
        """
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                resolved_path = Path(str(path)).resolve()
                with open(resolved_path, 'r') as f:
                    return json.load(f)
            except OSError as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 1s, 2s
                    msg.warning(
                        f"[ANNOTATIONS] File open failed (attempt {attempt + 1}/{max_retries}): {e} "
                        f"(retrying in {delay}s)"
                    )
                    time.sleep(delay)

        raise last_error

    def load(self, scan_folder: Path, analysis_dir: Path = None, analysis_type: str = "segmentation") -> bool:
        """Load annotations.json and build lookup indices.

        Parameters
        ----------
        scan_folder : Path
            The scan folder containing segmentation/annotations.json
        analysis_dir : Path, optional
            Analysis root directory (e.g. scan_root/analyses/19/).
        analysis_type : str
            Type subdirectory under analysis_dir (default: "segmentation").

        Returns
        -------
        bool
            True if successfully loaded, False otherwise
        """
        self._annotations.clear()
        self._images.clear()
        self._loaded = False
        self._scan_folder = scan_folder

        annotations_path = (
            analysis_dir / analysis_type / "annotations.json"
            if analysis_dir
            else scan_folder / "segmentation" / "annotations.json"
        )

        if not annotations_path.exists():
            msg.error(f"[ANNOTATIONS] annotations.json not found: {annotations_path}")
            return False

        try:
            msg.info(f"[ANNOTATIONS] Loading {annotations_path}")

            # Network paths: use pathlib read_text() to bypass Python FD cache
            # This avoids Errno 9 (Bad file descriptor) issues that require 15s+ retries
            if self._is_network_path(annotations_path):
                msg.debug("[ANNOTATIONS] Network path detected, using pathlib for reliability")
                data = self._load_json_via_pathlib(annotations_path)
            else:
                # Local paths: use standard Python open() with minimal retry
                data = self._load_json_via_python(annotations_path)

            # Build image lookup (id -> file_name)
            for img in data.get("images", []):
                self._images[img["id"]] = img

            # Process annotations
            for ann in data.get("annotations", []):
                image_id = ann["image_id"]
                image_info = self._images.get(image_id)

                if image_info is None:
                    msg.warning(f"[ANNOTATIONS] Image id {image_id} not found for annotation {ann['id']}")
                    continue

                image_name = image_info["file_name"]
                mask_id = ann.get("mask_id", ann["id"])

                metadata = FlakeMetadata(
                    flake_id=ann["id"],
                    image_id=image_id,
                    image_name=image_name,
                    mask_id=mask_id,
                    bbox_coco=tuple(ann["bbox"]),
                    area=ann["area"],
                    score=ann.get("score", 1.0),  # Default to 1.0 if not present
                    rle_segmentation=ann.get("segmentation"),
                )

                self._annotations[ann["id"]] = metadata

            self._loaded = True
            msg.info(f"[ANNOTATIONS] Loaded {len(self._annotations)} annotations from {len(self._images)} images")
            return True

        except Exception as e:
            msg.error(f"[ANNOTATIONS] Failed to load annotations.json: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_by_flake_id(self, flake_id: int) -> Optional[FlakeMetadata]:
        """Get metadata by flake_id (annotation id)."""
        return self._annotations.get(flake_id)

    def build_flake_mapping(self, flakes: list) -> Tuple[np.ndarray, Dict[int, FlakeMetadata]]:
        """Build score array and metadata mapping aligned with flakes list.

        Parameters
        ----------
        flakes : list[RLEFlake]
            List of RLEFlake objects

        Returns
        -------
        tuple
            (scores_array, metadata_dict)
            - scores_array: np.ndarray of shape (N,) with scores for each flake
            - metadata_dict: Dict[int, FlakeMetadata] mapping array index to metadata
        """
        n = len(flakes)
        scores = np.zeros(n, dtype=np.float32)
        metadata_dict: Dict[int, FlakeMetadata] = {}

        matched = 0
        unmatched = 0

        for idx, flake in enumerate(flakes):
            metadata = self.get_by_flake_id(flake.flake_id)
            if metadata:
                scores[idx] = metadata.score
                metadata_dict[idx] = metadata
                matched += 1
            else:
                scores[idx] = 1.0  # Default score for unmatched
                unmatched += 1

        if unmatched > 0:
            msg.warning(f"[ANNOTATIONS] {unmatched}/{n} flakes not matched in annotations.json")

        msg.info(f"[ANNOTATIONS] Built flake mapping: {matched} matched, {unmatched} unmatched")
        return scores, metadata_dict

    def decode_rle_mask(self, metadata: FlakeMetadata) -> Optional[np.ndarray]:
        """Decode RLE segmentation to binary mask.

        This is approximately 5x faster than loading PNG (0.95ms vs 4.7ms).

        Parameters
        ----------
        metadata : FlakeMetadata
            Metadata containing RLE segmentation data

        Returns
        -------
        np.ndarray or None
            Binary mask array (H, W) with dtype uint8, or None if no RLE data
        """
        if metadata.rle_segmentation is None:
            return None

        try:
            from pycocotools import mask as mask_util

            rle = metadata.rle_segmentation

            # pycocotools expects counts as bytes if it's a string
            if isinstance(rle.get("counts"), str):
                rle_copy = {
                    "size": rle["size"],
                    "counts": rle["counts"].encode("utf-8")
                }
            else:
                rle_copy = rle

            decoded = mask_util.decode(rle_copy)
            return decoded.astype(np.uint8) * 255

        except ImportError:
            raise RuntimeError(
                "pycocotools is required for RLE mask decoding but not installed. "
                "Install with: pip install pycocotools"
            )
        except Exception as e:
            msg.error(f"[ANNOTATIONS] RLE decode error: {e}")
            return None

    def get_all_metadata(self) -> List[FlakeMetadata]:
        """Get all loaded metadata entries."""
        return list(self._annotations.values())

    def get_score_range(self) -> Tuple[float, float]:
        """Get min and max score values."""
        if not self._annotations:
            return (0.0, 1.0)

        scores = [m.score for m in self._annotations.values()]
        return (min(scores), max(scores))

    def __len__(self) -> int:
        return len(self._annotations)


def load_flakes_from_annotations(
    annotations_cache: AnnotationsCache,
    raw_image_folder: Path,
    raw_ext: str = ".png",
) -> List["RLEFlake"]:
    """Load flakes from annotations.json without scanning masks/ directory.

    This function creates RLEFlake objects directly from the annotations cache,
    providing ~5x faster loading compared to scanning mask directories and
    loading PNG files.

    Parameters
    ----------
    annotations_cache : AnnotationsCache
        Pre-loaded annotations cache (must call cache.load() first)
    raw_image_folder : Path
        Directory containing raw images
    raw_ext : str
        Extension for raw image files (default: ".png")

    Returns
    -------
    List[RLEFlake]
        List of RLEFlake objects with lazy-loaded properties

    Raises
    ------
    ValueError
        If annotations_cache is not loaded
    """
    from flake_analysis.core.annotations.rle_flake import RLEFlake

    if not annotations_cache.is_loaded:
        raise ValueError("AnnotationsCache must be loaded before calling load_flakes_from_annotations")

    raw_image_folder = Path(raw_image_folder)
    flakes: List[RLEFlake] = []
    missing_raw = 0

    all_metadata = annotations_cache.get_all_metadata()
    msg.info(f"[ANNOTATIONS] Creating flakes from {len(all_metadata)} annotations")

    for i, metadata in enumerate(all_metadata):
        # Bug 11 diagnostic: per-1000 progress counter to disambiguate
        # SMB stat-loop slowness vs true hang. Read-only log, no behavior change.
        if i and i % 1000 == 0:
            msg.info(f"[ANNOTATIONS] {i}/{len(all_metadata)} processed")
        # Get raw image path from image_name
        image_stem = Path(metadata.image_name).stem
        raw_path = raw_image_folder / f"{image_stem}{raw_ext}"

        if not raw_path.exists():
            missing_raw += 1
            continue

        flake = RLEFlake(
            raw_path=raw_path,
            metadata=metadata,
            annotations_cache=annotations_cache,
        )
        flakes.append(flake)

    if missing_raw > 0:
        msg.warning(f"[ANNOTATIONS] {missing_raw} annotations skipped (raw images not found)")

    msg.info(f"[ANNOTATIONS] Created {len(flakes)} RLEFlake objects")
    return flakes
