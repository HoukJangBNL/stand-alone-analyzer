"""Thin wrapper for domain proximity (pair distance + flake construction).

Replaces Qpress's ``pair_distance_operation.py`` logic without DB / Operation /
Context. The two sub-stages (pair distance + union-find flake construction)
are executed sequentially in a single call per plan v1 D5.

Outputs (in ``output_dir``):
  - ``distances.parquet``           — columns: domain_id_a, domain_id_b, distance_px, distance_um
  - ``flake_assignments.parquet``   — columns: domain_id, flake_id, flake_size

The ``flake_id`` here is the standalone "flake" (= group of touching domains,
i.e. the legacy Qpress "island"). Singleton domains receive their own
``flake_id`` (the ``domain_id`` of the only member).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from flake_analysis.core._compat import ProgressCallback, msg
from flake_analysis.core.image_processing.pair_distance import (
    process_image,
    union_find_islands,
)


def _hash_params(params: Dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load_annotations(annotations_path: Path) -> Dict[str, Any]:
    """Load COCO-style annotations.json."""
    text = annotations_path.read_text(encoding="utf-8")
    return json.loads(text)


def _group_annotations_by_image(
    coco: Dict[str, Any],
) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, Tuple[int, int]]]:
    """Return (image_id -> [annotation dicts], image_id -> (width, height))."""
    images_meta: Dict[int, Tuple[int, int]] = {}
    for img in coco.get("images", []):
        images_meta[img["id"]] = (int(img["width"]), int(img["height"]))

    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for ann in coco.get("annotations", []):
        grouped[int(ann["image_id"])].append(ann)
    return grouped, images_meta


def run_domain_proximity(
    annotations_path: Union[str, Path],
    *,
    output_dir: Union[str, Path],
    r_max_px: float = 200.0,
    min_area_px: int = 10,
    max_area_px: Optional[int] = None,
    d_touch_px: float = 2.0,
    link_distance_um: Optional[float] = None,
    pixel_size_um: float = 0.5,
    workers: int = 4,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Run pair distance computation followed by union-find flake construction.

    Parameters
    ----------
    annotations_path : str | Path
        COCO-style ``annotations.json`` file.
    output_dir : str | Path
        Directory to receive the two parquet outputs (created if missing).
    r_max_px : float, optional
        Maximum pair distance (px) retained in stage 1. Default 200.0.
    min_area_px : int, optional
        Domains below this area are skipped (per image). Default 10.
    max_area_px : int | None, optional
        Domains above this area are skipped (filters SAM2 mega-mask
        hallucinations). Default ``None`` (no upper bound).
    d_touch_px : float, optional
        Per-image island merge threshold passed to ``process_image`` for
        sub-stage 1's bookkeeping. Default 2.0. **Not** used for the global
        flake construction in stage 2 — that uses ``link_distance_um``.
    link_distance_um : float | None, optional
        Stage 2 connected-component threshold in microns. ``None`` →
        falls back to ``d_touch_px * pixel_size_um``.
    pixel_size_um : float, optional
        Pixel size in microns. Used to convert pair distances to microns
        and to convert ``link_distance_um`` back to pixels for union-find.
    workers : int, optional
        Thread pool size for per-image processing. Default 4.

    Returns
    -------
    dict
        Summary including output paths, params, params_hash, and counts.
    """
    annotations_path = Path(annotations_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    msg.info(
        f"[pipeline.domain_proximity] start annotations={annotations_path} "
        f"out={output_dir} r_max_px={r_max_px} pixel_size_um={pixel_size_um}"
    )

    if progress_callback is not None:
        progress_callback(0.0, "Loading annotations...")

    coco = _load_annotations(annotations_path)
    grouped, images_meta = _group_annotations_by_image(coco)

    # Capture full set of domain_ids (including those that may be filtered out
    # by min_area_px / max_area_px — they need to remain singleton flakes per
    # review_algorithm.md T2). We use *all* annotation ids here.
    all_domain_ids: List[int] = sorted(int(ann["id"]) for ann in coco.get("annotations", []))

    pairs_global: List[Tuple[int, int, float]] = []

    def _do_image(image_id: int, anns: List[Dict[str, Any]]):
        meta = images_meta.get(image_id)
        if meta is None:
            msg.warning(
                f"[pipeline.domain_proximity] image_id={image_id} missing in images[]; skipping"
            )
            return []
        img_w, img_h = meta
        pairs, _border, _islands = process_image(
            anns,
            img_w=img_w,
            img_h=img_h,
            r_max=r_max_px,
            min_area_px=min_area_px,
            d_touch_px=d_touch_px,
            max_area_px=max_area_px,
        )
        return pairs

    # Run per-image pair computation in parallel. The pair-distance phase
    # spans 0% .. 0.9 (90% of the wrapper time budget); the union-find at the
    # end occupies the remaining 0.9 .. 1.0.
    n_total_images = max(1, len(grouped))
    n_done = 0
    if workers and workers > 1 and len(grouped) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_do_image, img_id, anns): img_id
                for img_id, anns in grouped.items()
            }
            for fut in as_completed(futures):
                img_pairs = fut.result()
                pairs_global.extend(img_pairs)
                n_done += 1
                if progress_callback is not None:
                    pct = 0.9 * float(n_done) / float(n_total_images)
                    progress_callback(
                        pct,
                        f"Processed image {n_done}/{n_total_images} "
                        f"(+{len(img_pairs)} pairs)",
                    )
    else:
        for img_id, anns in grouped.items():
            img_pairs = _do_image(img_id, anns)
            pairs_global.extend(img_pairs)
            n_done += 1
            if progress_callback is not None:
                pct = 0.9 * float(n_done) / float(n_total_images)
                progress_callback(
                    pct,
                    f"Processed image {n_done}/{n_total_images} "
                    f"(+{len(img_pairs)} pairs)",
                )

    pairs_global.sort(key=lambda p: (p[0], p[1]))

    if progress_callback is not None:
        progress_callback(0.95, "Building flake assignments via union-find...")

    # Persist distances.parquet (with both px and um).
    distances_path = output_dir / "distances.parquet"
    distances_df = pd.DataFrame(
        {
            "domain_id_a": [int(a) for a, _b, _d in pairs_global],
            "domain_id_b": [int(b) for _a, b, _d in pairs_global],
            "distance_px": [float(d) for _a, _b, d in pairs_global],
        }
    )
    distances_df["distance_um"] = distances_df["distance_px"] * float(pixel_size_um)
    distances_df.to_parquet(distances_path, engine="pyarrow", index=False)
    msg.info(
        f"[pipeline.domain_proximity] wrote {len(distances_df)} pairs to {distances_path}"
    )

    # Stage 2: union-find with link_distance_um.
    effective_link_um = (
        link_distance_um
        if link_distance_um is not None
        else d_touch_px * pixel_size_um
    )
    link_distance_px = effective_link_um / pixel_size_um if pixel_size_um > 0 else 0.0

    flakes = union_find_islands(all_domain_ids, pairs_global, link_distance_px)

    rows: List[Tuple[int, int, int]] = []
    for members in flakes:
        # flake_id == smallest constituent domain_id (deterministic).
        flake_id = members[0]
        size = len(members)
        for did in members:
            rows.append((int(did), int(flake_id), int(size)))
    flake_df = pd.DataFrame(rows, columns=["domain_id", "flake_id", "flake_size"])
    flake_assignments_path = output_dir / "flake_assignments.parquet"
    flake_df.to_parquet(flake_assignments_path, engine="pyarrow", index=False)
    msg.info(
        f"[pipeline.domain_proximity] wrote {len(flake_df)} domain rows / "
        f"{len(flakes)} flakes to {flake_assignments_path}"
    )

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    params: Dict[str, Any] = {
        "r_max_px": r_max_px,
        "min_area_px": min_area_px,
        "max_area_px": max_area_px,
        "d_touch_px": d_touch_px,
        "link_distance_um": effective_link_um,
        "pixel_size_um": pixel_size_um,
    }
    return {
        "distances_path": distances_path,
        "flake_assignments_path": flake_assignments_path,
        "n_pairs": int(len(distances_df)),
        "n_domains": int(len(all_domain_ids)),
        "n_flakes": int(len(flakes)),
        "params": params,
        "params_hash": _hash_params(params),
    }
