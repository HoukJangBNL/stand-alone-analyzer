"""Per-image flake mask edge-to-edge pair distances.

Pure-compute primitives used by PairDistanceOperation.

Per image the pipeline is:
  1. bbox prefilter — retain pairs whose axis-aligned rectangle edge
     distance is ≤ r_max_px.
  2. Decode the masks used by surviving candidates (COCO RLE → dense).
  3. Build one EDT per flake (local window = bbox padded by r_max_px,
     clipped to image bounds).
  4. For each candidate pair (a, b), sample EDT[a] at b's mask pixels
     falling inside a's window and vice versa; the mask edge distance
     is the min of both samples.
  5. Return per-image pair list and the set of flakes whose masks touch
     the image border.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pycocotools.mask as mask_util


# ---------------------------------------------------------------------------
# bbox helpers
# ---------------------------------------------------------------------------

def bbox_edge_distance(b1, b2) -> float:
    """Axis-aligned rectangle edge-to-edge distance.

    bbox format: (x, y, w, h). Overlapping rectangles return 0.0.
    """
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    dx = max(0.0, max(x1, x2) - min(x1 + w1, x2 + w2))
    dy = max(0.0, max(y1, y2) - min(y1 + h1, y2 + h2))
    return float((dx * dx + dy * dy) ** 0.5)


# ---------------------------------------------------------------------------
# RLE decoding
# ---------------------------------------------------------------------------

def decode_rle(seg: Dict) -> np.ndarray:
    """Decode one COCO RLE dict to a dense uint8 HxW array.

    Accepts both `bytes` and `str` forms of `counts` (JSON round-trip
    produces `str`).
    """
    if isinstance(seg["counts"], str):
        rle = {"size": list(seg["size"]), "counts": seg["counts"].encode("ascii")}
    else:
        rle = seg
    return mask_util.decode(rle)


# ---------------------------------------------------------------------------
# Per-flake EDT window
# ---------------------------------------------------------------------------

def build_flake_edt(
    mask: np.ndarray,
    bbox,
    img_w: int,
    img_h: int,
    r_max: float,
) -> Optional[Dict]:
    """Build a local Euclidean Distance Transform around one flake.

    The EDT window is the flake's bbox padded by `r_max + 1`, clipped to
    image bounds. EDT values are the distance from each pixel in the
    window to the nearest mask-true pixel.

    Returns None if the mask is empty in the window (should not happen
    when bbox is correct, but we defend against corrupt annotations).

    Returns dict with:
      y0, x0, y1, x1: window offsets in full image coords
      edt            : HxW float array (cv2.distanceTransform output)
      abs_ys, abs_xs : absolute pixel coords of mask-true pixels
    """
    import cv2

    pad = int(r_max) + 1
    x, y, w, h = bbox
    x0 = int(max(0, x - pad))
    y0 = int(max(0, y - pad))
    x1 = int(min(img_w, x + w + pad))
    y1 = int(min(img_h, y + h + pad))
    if y1 <= y0 or x1 <= x0:
        return None

    loc = mask[y0:y1, x0:x1]
    if loc.sum() == 0:
        return None

    # cv2.distanceTransform expects the *background* to be non-zero,
    # i.e. we want the EDT of "distance to nearest mask pixel", so we
    # pass the complement as uint8.
    inv = (loc == 0).astype(np.uint8)
    edt = cv2.distanceTransform(inv, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)

    ys, xs = np.where(loc > 0)
    return {
        "y0": y0,
        "x0": x0,
        "y1": y1,
        "x1": x1,
        "edt": edt,
        "abs_ys": ys + y0,
        "abs_xs": xs + x0,
    }


def mask_edge_distance(edt_a: Optional[Dict], flake_b: Optional[Dict], r_max: float) -> float:
    """Sample EDT of A at B's mask pixels; return min or inf.

    Returns inf when either flake has no EDT, or when none of B's mask
    pixels fall within A's EDT window.
    """
    if edt_a is None or flake_b is None:
        return float("inf")
    ys = flake_b["abs_ys"]
    xs = flake_b["abs_xs"]
    y0, y1 = edt_a["y0"], edt_a["y1"]
    x0, x1 = edt_a["x0"], edt_a["x1"]
    in_win = (ys >= y0) & (ys < y1) & (xs >= x0) & (xs < x1)
    if not in_win.any():
        return float("inf")
    local_ys = ys[in_win] - y0
    local_xs = xs[in_win] - x0
    vals = edt_a["edt"][local_ys, local_xs]
    return float(vals.min())


# ---------------------------------------------------------------------------
# Border detection
# ---------------------------------------------------------------------------

def touches_border(mask: np.ndarray) -> bool:
    """True if any mask pixel sits on the image edge."""
    if mask.size == 0:
        return False
    if mask[0, :].any() or mask[-1, :].any():
        return True
    if mask[:, 0].any() or mask[:, -1].any():
        return True
    return False


# ---------------------------------------------------------------------------
# Per-image pipeline
# ---------------------------------------------------------------------------

def process_image(
    anns: List[Dict],
    img_w: int,
    img_h: int,
    r_max: float,
    min_area_px: int = 0,
    d_touch_px: float = 2.0,
    max_area_px: Optional[int] = None,
) -> Tuple[
    List[Tuple[int, int, float]],
    List[int],
    List[Dict[str, Any]],
]:
    """Compute per-image pair distances, border flakes, and islands.

    Args:
        anns: COCO-style annotation dicts for one image.
        img_w, img_h: image dimensions in pixels.
        r_max: max bbox/mask distance to retain (stored in pairs list).
        min_area_px: exclude flakes with area below this.
        d_touch_px: merge threshold for islands (≤ r_max recommended; caller
            should validate).
        max_area_px: Optional upper bound on flake area; flakes larger than
            this are excluded. Use to filter SAM2 mega-flake substrate
            hallucinations. None = no upper bound.

    Returns:
        (pairs, border_flakes, islands):
          - pairs: `(flake_a, flake_b, distance_px)` triples, sorted.
          - border_flakes: flake IDs touching image boundary, sorted.
          - islands: list of dicts `{flake_ids, bbox, nearest_external_distance_px}`,
            sorted by smallest flake_id in each island.
    """
    # Area filter first (lower bound + optional upper bound)
    upper = max_area_px if max_area_px is not None else float("inf")
    kept = [
        a for a in anns
        if min_area_px <= a.get("area", 0) <= upper
    ]
    if not kept:
        return [], [], []

    ids = [a["id"] for a in kept]
    bboxes = [a["bbox"] for a in kept]
    n = len(kept)

    # bbox prefilter
    candidates: List[Tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if bbox_edge_distance(bboxes[i], bboxes[j]) <= r_max:
                candidates.append((i, j))

    # Decode only the masks we actually need (candidate members + all
    # for border detection).
    needed_for_pairs = set()
    for i, j in candidates:
        needed_for_pairs.add(i)
        needed_for_pairs.add(j)

    masks: Dict[int, np.ndarray] = {}
    border_flakes: List[int] = []
    for idx in range(n):
        mask = decode_rle(kept[idx]["segmentation"])
        masks[idx] = mask
        if touches_border(mask):
            border_flakes.append(ids[idx])

    # Build per-flake EDT windows, only for flakes that appear in a
    # candidate pair.
    flake_edt: Dict[int, Optional[Dict]] = {}
    for idx in needed_for_pairs:
        flake_edt[idx] = build_flake_edt(
            masks[idx], bboxes[idx], img_w, img_h, r_max
        )

    # Compute pair distances, keeping `d ≤ r_max`.
    pairs: List[Tuple[int, int, float]] = []
    for i, j in candidates:
        d_ij = mask_edge_distance(flake_edt[i], flake_edt[j], r_max)
        d_ji = mask_edge_distance(flake_edt[j], flake_edt[i], r_max)
        d = min(d_ij, d_ji)
        if d <= r_max:
            a, b = sorted((ids[i], ids[j]))
            pairs.append((a, b, d))

    pairs.sort(key=lambda p: (p[0], p[1]))
    border_flakes.sort()

    # Island construction
    island_members = union_find_islands(ids, pairs, d_touch_px)
    nearest_externals = compute_nearest_external_distances(island_members, pairs)

    id_to_bbox = {ids[i]: bboxes[i] for i in range(n)}
    islands: List[Dict[str, Any]] = []
    for members, d_ext in zip(island_members, nearest_externals):
        # Union bbox
        bboxes_in = [id_to_bbox[fid] for fid in members]
        x0 = min(b[0] for b in bboxes_in)
        y0 = min(b[1] for b in bboxes_in)
        x1 = max(b[0] + b[2] for b in bboxes_in)
        y1 = max(b[1] + b[3] for b in bboxes_in)
        islands.append({
            "flake_ids": members,
            "bbox": [x0, y0, x1 - x0, y1 - y0],
            "nearest_external_distance_px": d_ext,
        })

    return pairs, border_flakes, islands


# ---------------------------------------------------------------------------
# Union-find island construction
# ---------------------------------------------------------------------------

def union_find_islands(
    flake_ids: List[int],
    pairs: List[Tuple[int, int, float]],
    d_touch_px: float,
) -> List[List[int]]:
    """Group flakes into islands via union-find where pair distance ≤ d_touch_px.

    Args:
        flake_ids: All flake IDs in the scope (one image). Must be deduplicated.
        pairs: `(flake_a, flake_b, distance_px)` triples. `a` and `b` are flake IDs;
            distance is pixel edge-to-edge (already computed).
        d_touch_px: Merge threshold; pairs with distance > this are ignored.

    Returns:
        Islands as list of sorted `flake_ids` lists. Outer list is sorted ascending
        by each island's minimum flake_id. Singletons (flakes with no in-threshold
        pair) appear as single-element lists.
    """
    if not flake_ids:
        return []

    # Union-find over the provided flake_ids (not dense ints — so use dict).
    parent: Dict[int, int] = {fid: fid for fid in flake_ids}

    def find(x: int) -> int:
        # Path compression
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a, b, d in pairs:
        if d <= d_touch_px and a in parent and b in parent:
            union(a, b)

    # Group
    groups: Dict[int, List[int]] = defaultdict(list)
    for fid in flake_ids:
        groups[find(fid)].append(fid)

    islands = [sorted(members) for members in groups.values()]
    islands.sort(key=lambda m: m[0])
    return islands


def compute_nearest_external_distances(
    islands: List[List[int]],
    pairs: List[Tuple[int, int, float]],
) -> List[Optional[float]]:
    """For each island, find the minimum pair distance to any flake outside it.

    Args:
        islands: Output of `union_find_islands` — ordered list of sorted flake_id lists.
        pairs: Same pair list passed to `union_find_islands`. Pair ordering and
            distance values are preserved.

    Returns:
        List parallel to `islands`. Element is the min distance to any external
        flake, or `None` if no pair in `pairs` crosses this island's boundary
        (i.e., every neighbor is within the island).
    """
    if not islands:
        return []

    # Map flake_id -> island_index for O(1) membership checks.
    flake_to_island: Dict[int, int] = {}
    for idx, island in enumerate(islands):
        for fid in island:
            flake_to_island[fid] = idx

    nearest: List[Optional[float]] = [None] * len(islands)
    for a, b, d in pairs:
        ia = flake_to_island.get(a)
        ib = flake_to_island.get(b)
        if ia is None or ib is None:
            continue
        if ia == ib:
            continue  # internal pair
        for island_idx in (ia, ib):
            current = nearest[island_idx]
            if current is None or d < current:
                nearest[island_idx] = d

    return nearest
