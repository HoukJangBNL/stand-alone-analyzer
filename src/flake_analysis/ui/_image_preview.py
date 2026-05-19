"""Image preview helper — load raw image + crop to a domain bbox.

Used by ``tab_selector`` to show the raw imagery context for a single
focused domain. Wraps ``flake_analysis.core.annotations.AnnotationsCache`` when
available, with a json.loads fallback that does not require flake_analysis.core.

v0.1.4: switched from ``st.image`` to a Plotly ``px.imshow`` figure so the
user gets mouse-wheel zoom, click-drag pan, and a native modebar reset.
A boundary overlay decoded from the COCO RLE ``segmentation`` field can
be toggled (``B`` shortcut) to highlight the segmented contour.

Public API:
    render_image_preview(...)           — Streamlit panel
    crop_for_domain(...)                — pure crop helper (testable)
    crop_for_domain_with_mask(...)      — crop + cropped binary mask
    decode_segmentation_mask(...)       — pycocotools wrapper (RLE → ndarray)
    contours_for_mask(mask)             — cv2.findContours wrapper (testable)
    build_image_preview_figure(...)     — pure Plotly figure builder (testable)
    load_annotations_index(...)         — return (id_lookup, image_lookup)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
import streamlit as st


# ─── Annotation lookup (no Streamlit dependency, easy to unit-test) ─────

def load_annotations_index(
    annotations_path: str | Path,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Return (annotation_by_id, image_by_id) mappings.

    Falls back to ``json.loads`` of the file directly. We keep this
    independent of ``flake_analysis.core`` so the preview helper still works in
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


# ─── Mask + contour helpers ────────────────────────────────────────────

def decode_segmentation_mask(
    segmentation: Any,
    *,
    height: Optional[int] = None,
    width: Optional[int] = None,
) -> Optional[np.ndarray]:
    """Decode a COCO ``segmentation`` field into a HxW uint8 binary mask.

    Supports the two common shapes:

    * ``{"size": [H, W], "counts": str}`` — pycocotools-encoded RLE.
    * ``{"size": [H, W], "counts": list[int]}`` — uncompressed RLE.

    Returns ``None`` if the field is missing/empty/unrecognised so callers
    can fall back to a no-overlay preview cleanly.

    ``height``/``width`` are accepted for symmetry with future polygon
    support but are ignored when the RLE itself carries ``size``.
    """
    if not segmentation:
        return None
    try:
        from pycocotools import mask as mask_util
    except ImportError:  # pragma: no cover - dep is in install reqs
        return None

    # Polygon segmentations are a list of lists of floats — convert via
    # frPyObjects when we know the image dims.
    if isinstance(segmentation, list):
        if not segmentation:
            return None
        if height is None or width is None:
            return None
        try:
            rles = mask_util.frPyObjects(segmentation, int(height), int(width))
            rle = mask_util.merge(rles)
        except Exception:
            return None
    elif isinstance(segmentation, dict):
        rle = dict(segmentation)
        # pycocotools expects bytes for compressed counts; tolerate str.
        counts = rle.get("counts")
        if isinstance(counts, str):
            rle["counts"] = counts.encode("ascii")
        elif isinstance(counts, list):
            try:
                rle = mask_util.frPyObjects(
                    [rle], int(rle["size"][0]), int(rle["size"][1])
                )[0]
            except Exception:
                return None
    else:
        return None

    try:
        decoded = mask_util.decode(rle)
    except Exception:
        return None
    if decoded is None:
        return None
    arr = np.asarray(decoded)
    if arr.ndim == 3:
        arr = arr[..., 0]
    return arr.astype(np.uint8)


def contours_for_mask(mask: np.ndarray) -> List[np.ndarray]:
    """Return external contours of a binary mask as a list of (N, 2) ndarrays.

    Each contour is closed (first point appended at the end) so it can
    be drawn as a filled-line Plotly trace without a visible seam.
    Returns an empty list if cv2 isn't available, the mask is empty, or
    no contours were found.
    """
    if mask is None or mask.size == 0:
        return []
    try:
        import cv2
    except ImportError:  # pragma: no cover - dep is in install reqs
        return []
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.sum() == 0:
        return []
    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    closed: List[np.ndarray] = []
    for c in contours:
        if c is None or len(c) == 0:
            continue
        pts = c.reshape(-1, 2)
        if len(pts) < 2:
            continue
        closed.append(np.vstack([pts, pts[:1]]))
    return closed


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
    result = crop_for_domain_with_mask(
        raw_images_dir, annotations, images, domain_id, bbox_padding=bbox_padding
    )
    if result is None:
        return None
    crop, _mask, info = result
    return crop, info


def crop_for_domain_with_mask(
    raw_images_dir: str | Path,
    annotations: Dict[int, Dict[str, Any]],
    images: Dict[int, Dict[str, Any]],
    domain_id: int,
    *,
    bbox_padding: float = 0.2,
) -> Optional[Tuple[Image.Image, Optional[np.ndarray], Dict[str, Any]]]:
    """Like :func:`crop_for_domain` but also returns the mask cropped to the
    same window (or ``None`` if the annotation has no usable segmentation).

    The returned mask is a HxW uint8 array in the *crop* coordinate
    system, so passing it to :func:`contours_for_mask` yields contour
    coordinates that overlay directly on the crop without further
    translation.
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

    crop_mask: Optional[np.ndarray] = None
    full_mask = decode_segmentation_mask(
        ann.get("segmentation"),
        height=img_info.get("height", img.height),
        width=img_info.get("width", img.width),
    )
    if full_mask is not None:
        # Defend against shape mismatch (older datasets sometimes have an
        # image-info width/height that doesn't equal the actual asset).
        full_h, full_w = full_mask.shape[:2]
        m_left = max(0, min(left, full_w))
        m_right = max(0, min(right, full_w))
        m_top = max(0, min(top, full_h))
        m_bottom = max(0, min(bottom, full_h))
        if m_right > m_left and m_bottom > m_top:
            crop_mask = full_mask[m_top:m_bottom, m_left:m_right]
        else:
            crop_mask = None

    info = {
        "domain_id": int(domain_id),
        "image_id": image_id,
        "image_name": img_info.get("file_name", ""),
        "bbox": (x, y, w, h),
        "crop_box": (left, top, right, bottom),
        "image_path": str(image_path),
        "has_mask": crop_mask is not None,
    }
    return crop, crop_mask, info


# ─── Plotly figure builder ─────────────────────────────────────────────

# Distinct gold tone for the boundary overlay — keeps it visible against
# the green/red segmentation palette and the typical purple substrate.
BOUNDARY_COLOR = "#FFC800"
BOUNDARY_WIDTH = 2


def build_image_preview_figure(
    crop: Image.Image,
    crop_mask: Optional[np.ndarray],
    show_boundary: bool,
    *,
    full_image: Optional[Image.Image] = None,
    crop_box: Optional[Tuple[int, int, int, int]] = None,
):
    """Build a Plotly Figure for a raw image preview with bbox-zoomed viewport.

    When ``full_image`` and ``crop_box`` are supplied (preferred), the
    figure shows the *entire* raw image but starts zoomed in to the
    crop_box region. Zooming out reveals the rest of the FOV instead of
    just empty padding around the cropped tile (the previous behavior).

    Falling back to the legacy ``crop``-only signature still works for
    callers that haven't migrated yet.

    Pure function (no Streamlit calls) so it can be smoke-tested.
    """
    import plotly.express as px
    import plotly.graph_objects as go

    if full_image is not None and crop_box is not None:
        arr = np.asarray(full_image)
        x0, y0, x1, y1 = crop_box
        # Contour offset converts crop-coord points back into image coords.
        contour_offset = (x0, y0)
        # Initial viewport — Plotly y-axis is reversed for px.imshow, so
        # the higher y value (y1, bottom of image) is the lower bound.
        initial_xrange = [x0, x1]
        initial_yrange = [y1, y0]
    else:
        arr = np.asarray(crop)
        contour_offset = (0, 0)
        initial_xrange = None
        initial_yrange = None

    fig = px.imshow(arr)
    fig.update_xaxes(
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        range=initial_xrange,
    )
    fig.update_yaxes(
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        range=initial_yrange,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        dragmode="pan",
        showlegend=False,
    )

    if show_boundary and crop_mask is not None:
        ox, oy = contour_offset
        for contour in contours_for_mask(crop_mask):
            xs = (contour[:, 0] + ox).tolist()
            ys = (contour[:, 1] + oy).tolist()
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    line=dict(color=BOUNDARY_COLOR, width=BOUNDARY_WIDTH),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
    return fig


# ─── Streamlit panel ────────────────────────────────────────────────────

# session_state slot for the boundary toggle — flat key so the keyboard
# shortcut can flip it without scoping (single preview panel per app).
_BOUNDARY_KEY = "preview.show_boundary"


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

    v0.1.4 — uses a Plotly figure (zoom/pan via modebar) and shows a
    boundary overlay decoded from the segmentation RLE when the user
    enables it (``B`` shortcut or the "Boundary (B)" button).
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

    result = crop_for_domain_with_mask(
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

    crop, crop_mask, info = result
    show_boundary = bool(st.session_state.get(_BOUNDARY_KEY, True))

    # The boundary toggle button — ASCII label so the JS shortcut handler
    # can match by innerText (mirrors the mode-button convention).
    btn_cols = st.columns([1, 1, 3])
    with btn_cols[0]:
        toggle_label = (
            "Boundary on (B)" if show_boundary else "Boundary off (B)"
        )
        if st.button(
            toggle_label,
            key="preview_boundary_btn",
            disabled=not info.get("has_mask", False),
            help="Toggle the segmentation boundary overlay (B)",
        ):
            st.session_state[_BOUNDARY_KEY] = not show_boundary
            st.rerun()

    # Show the full raw image so the user can zoom out into the rest of
    # the FOV, rather than into empty padding around just the cropped tile.
    full_image: Optional[Image.Image] = None
    crop_box = info.get("crop_box")
    try:
        full_image = Image.open(info["image_path"])
    except Exception:
        full_image = None
    fig = build_image_preview_figure(
        crop,
        crop_mask,
        show_boundary,
        full_image=full_image,
        crop_box=tuple(crop_box) if crop_box is not None else None,
    )

    suffix = f" (focus of {n_selected} selected)" if n_selected > 1 else ""
    st.caption(
        f"Domain {info['domain_id']} · img_{info['image_id']} "
        f"· {info['image_name']}{suffix}"
    )

    # Plotly modebar already provides +/-/Reset View; remove lasso/select
    # buttons since they're meaningless for a static raster preview.
    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    }
    st.plotly_chart(
        fig,
        config=config,
        use_container_width=True,
        # The key includes the boundary flag so toggling forces a fresh
        # render (avoids the same caching pitfall fixed in Task 1).
        key=f"preview_{info['domain_id']}_{int(show_boundary)}",
    )

    bx, by, bw, bh = info["bbox"]
    st.caption(
        f"bbox=({bx:.0f},{by:.0f},{bw:.0f},{bh:.0f}) "
        f"· crop={info['crop_box']} · pad={bbox_padding:.0%}"
        + ("" if info.get("has_mask") else " · (no mask)")
    )


# Cache: keyed by (path, mtime) so external edits re-parse.
@st.cache_data(show_spinner=False)
def _cached_load_annotations(
    annotations_path: str,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    return load_annotations_index(annotations_path)
