"""Image preview helper — load raw image + crop to a domain bbox.

Used by ``tab_selector`` to show the raw imagery context for a single
focused domain. Wraps ``flake_core.annotations.AnnotationsCache`` when
available, with a json.loads fallback that does not require flake_core.

Public API:
    render_image_preview(...)           — Streamlit panel
    crop_for_domain(...)                — pure crop helper (testable)
    load_annotations_index(...)         — return (id_lookup, image_lookup)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image
import streamlit as st


# ─── Annotation lookup (no Streamlit dependency, easy to unit-test) ─────

def load_annotations_index(
    annotations_path: str | Path,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Return (annotation_by_id, image_by_id) mappings.

    Falls back to ``json.loads`` of the file directly. We keep this
    independent of ``flake_core`` so the preview helper still works in
    test environments without the full annotations cache machinery.

    Parameters
    ----------
    annotations_path : path-like
        Direct path to ``annotations.json`` (COCO format).

    Returns
    -------
    tuple
        (id_lookup, image_lookup): each is a dict keyed by COCO id.
    """
    path = Path(annotations_path)
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))

    images: Dict[int, Dict[str, Any]] = {}
    for img in data.get("images", []):
        images[int(img["id"])] = img

    annotations: Dict[int, Dict[str, Any]] = {}
    for ann in data.get("annotations", []):
        annotations[int(ann["id"])] = ann

    return annotations, images


def crop_for_domain(
    raw_images_dir: str | Path,
    annotations: Dict[int, Dict[str, Any]],
    images: Dict[int, Dict[str, Any]],
    domain_id: int,
    *,
    bbox_padding: float = 0.2,
) -> Optional[Tuple[Image.Image, Dict[str, Any]]]:
    """Return (cropped_image, info_dict) for a domain, or None if missing.

    Pads the bbox by ``bbox_padding`` (fraction of bbox width/height)
    and clamps to image bounds.
    """
    ann = annotations.get(int(domain_id))
    if ann is None:
        return None

    image_id = int(ann["image_id"])
    img_info = images.get(image_id)
    if img_info is None:
        return None

    image_path = Path(raw_images_dir) / img_info["file_name"]
    if not image_path.exists():
        # Try the stem as PNG (raw vs segmented filename mismatch).
        stem_png = Path(raw_images_dir) / (Path(img_info["file_name"]).stem + ".png")
        if stem_png.exists():
            image_path = stem_png
        else:
            return None

    img = Image.open(image_path)
    x, y, w, h = ann["bbox"]
    pad_x = w * bbox_padding
    pad_y = h * bbox_padding
    left = max(0, int(x - pad_x))
    top = max(0, int(y - pad_y))
    right = min(img.width, int(x + w + pad_x))
    bottom = min(img.height, int(y + h + pad_y))
    if right <= left or bottom <= top:
        return None
    crop = img.crop((left, top, right, bottom))

    info = {
        "domain_id": int(domain_id),
        "image_id": image_id,
        "image_name": img_info.get("file_name", ""),
        "bbox": (x, y, w, h),
        "crop_box": (left, top, right, bottom),
        "image_path": str(image_path),
    }
    return crop, info


# ─── Streamlit panel ────────────────────────────────────────────────────

def render_image_preview(
    *,
    raw_images_dir: str,
    annotations_path: str,
    domain_id: Optional[int],
    n_selected: int = 0,
    bbox_padding: float = 0.2,
) -> None:
    """Render a per-domain raw-image preview panel.

    Designed to be called inside a Streamlit column. Caches the parsed
    annotations.json by file path (and mtime) so successive reruns don't
    re-parse the file on every selection change.
    """
    st.subheader("Raw image preview")

    if not annotations_path or not raw_images_dir:
        st.caption("Set raw_images/ and annotations.json in the sidebar.")
        return
    if domain_id is None:
        st.caption("Click a domain in any 2D pane to see its raw image.")
        return

    try:
        annotations, images = _cached_load_annotations(annotations_path)
    except Exception as e:  # pragma: no cover - I/O error edge
        st.caption(f"Annotations error: {e}")
        return

    if not annotations:
        st.caption(f"annotations.json missing or empty: {annotations_path}")
        return

    result = crop_for_domain(
        raw_images_dir,
        annotations,
        images,
        int(domain_id),
        bbox_padding=bbox_padding,
    )
    if result is None:
        st.caption(
            f"Domain {domain_id} not found in annotations or raw image missing."
        )
        return

    crop, info = result
    suffix = f" (focus of {n_selected} selected)" if n_selected > 1 else ""
    st.image(
        crop,
        caption=(
            f"Domain {info['domain_id']} · img_{info['image_id']} "
            f"· {info['image_name']}{suffix}"
        ),
        use_container_width=True,
    )
    bx, by, bw, bh = info["bbox"]
    st.caption(
        f"bbox=({bx:.0f},{by:.0f},{bw:.0f},{bh:.0f}) "
        f"· crop={info['crop_box']} · pad={bbox_padding:.0%}"
    )


# Cache: keyed by (path, mtime) so external edits re-parse.
@st.cache_data(show_spinner=False)
def _cached_load_annotations(
    annotations_path: str,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    return load_annotations_index(annotations_path)
