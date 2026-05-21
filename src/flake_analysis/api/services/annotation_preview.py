"""Server-side raw-image crop + optional contour overlay.

Ports the data loading + drawing pieces of ui/_image_preview.py:200-360
(the Streamlit version did its own pan/zoom in the browser; here we
serve a fixed crop + optional outline as PNG, and the frontend handles
pan/zoom client-side per Q-U3).
"""
from __future__ import annotations
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw


def _load_index(annotations_path: Path) -> dict:
    with open(annotations_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_domain(index: dict, domain_id: int) -> tuple[str, dict]:
    for tile_name, payload in index.items():
        for d in payload.get("domains", []):
            if int(d.get("domain_id", -1)) == int(domain_id):
                return tile_name, d
    raise KeyError(f"domain_id {domain_id} not found in annotations")


def load_preview(
    *,
    annotations_path: str | Path,
    raw_images_dir: str | Path,
    domain_id: int,
    with_contour: bool,
) -> bytes:
    """Return PNG bytes for the crop around ``domain_id``.

    ``with_contour=True`` overlays the polygon in red (RGB 255,0,0) at 1px width.
    """
    index = _load_index(Path(annotations_path))
    tile_name, dom = _find_domain(index, domain_id)
    bbox = dom["bbox"]  # [x0, y0, x1, y1]
    polygon = dom.get("polygon") or []

    img_path = Path(raw_images_dir) / tile_name
    if not img_path.exists():
        raise FileNotFoundError(f"raw image missing: {img_path}")

    with Image.open(img_path) as src:
        crop = src.convert("RGB").crop(tuple(bbox))

    if with_contour and polygon:
        draw = ImageDraw.Draw(crop)
        x0, y0, _, _ = bbox
        local = [(int(px - x0), int(py - y0)) for px, py in polygon]
        if len(local) >= 2:
            draw.line(local + [local[0]], fill=(255, 0, 0), width=1)

    out = io.BytesIO()
    crop.save(out, format="PNG")
    return out.getvalue()
