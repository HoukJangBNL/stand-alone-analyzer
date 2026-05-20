"""LOD thumbnail pre-render — feeds the Explorer substrate mosaic.

Each raw image (typically 1920 x 1200 px microscopy tile) gets
downsampled into 3 cached levels, written as WebP for ~3x smaller
files than PNG with negligible visual loss at thumbnail sizes.

LOD pyramid (per :data:`LOD_SIZES`):

  * **LOD 0**  64 x 40 px   — substrate-grid default zoom-out cell
  * **LOD 1** 192 x 120 px  — mid zoom
  * **LOD 2** 480 x 300 px  — zoom-in just before raw

The original raw image is the *implicit* highest LOD (level 3) — the
Explorer falls back to it on full zoom-in. We never duplicate the raw.

Cache logic: re-running this step skips images whose
``(raw_path, mtime, size)`` triple matches the previous run's
``index.json`` entry. Use ``force_recompute=True`` from the wrapper
to bypass.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from PIL import Image

from flake_analysis.core._compat import msg

ProgressCallback = Callable[[float, str], None]


# (lod_index, (width_px, height_px))
LOD_SIZES: Dict[int, Tuple[int, int]] = {
    0: (64, 40),     # substrate-grid cell default zoom-out
    1: (192, 120),   # mid zoom
    2: (480, 300),   # zoom-in just before raw
}

# Raw is the implicit LOD 3; never cached.
MAX_LOD: int = 3


def _hash_params(params: Dict[str, Any]) -> str:
    payload = json.dumps(
        params, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _list_raw_images(raw_dir: Path, ext: str) -> List[Path]:
    """List raw image files in stable order. Skips macOS dotfiles
    (``._*``) which corrupt PIL when fed back."""
    return sorted(
        p for p in raw_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() == ext.lower()
        and not p.name.startswith("._")
        and not p.name.startswith(".")
    )


def _file_signature(p: Path) -> Tuple[str, int, float]:
    """A cheap (path, size, mtime) fingerprint used for cache hit
    detection. Avoids hashing pixel content."""
    s = p.stat()
    return (p.name, int(s.st_size), float(s.st_mtime))


def _generate_one(
    raw_path: Path,
    out_root: Path,
    quality: int,
) -> Dict[str, Any]:
    """Generate every LOD thumbnail for a single raw image.

    Returns a manifest entry recording per-LOD output paths so the
    caller can write a single ``index.json``.
    """
    img = Image.open(raw_path)
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    stem = raw_path.stem
    out_paths: Dict[str, str] = {}
    for lod, (w, h) in LOD_SIZES.items():
        # PIL's .thumbnail mutates in place + preserves aspect ratio
        # but we want exact (w, h) — use .resize for that, with the
        # high-quality LANCZOS filter.
        thumb = img.resize((w, h), Image.LANCZOS)
        lod_dir = out_root / f"lod{lod}"
        lod_dir.mkdir(parents=True, exist_ok=True)
        out_path = lod_dir / f"{stem}.webp"
        thumb.save(out_path, format="WEBP", quality=quality, method=4)
        out_paths[f"lod{lod}"] = str(out_path.relative_to(out_root.parent.parent))

    return {
        "raw_name": raw_path.name,
        "stem": stem,
        "outputs": out_paths,
        "signature": _file_signature(raw_path),
    }


def run_thumbnails(
    raw_images_dir: Union[str, Path],
    *,
    output_dir: Union[str, Path],
    raw_ext: str = ".png",
    quality: int = 80,
    force_recompute: bool = False,
    max_workers: int = 8,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Pre-render LOD thumbnails for every raw image.

    Parameters
    ----------
    raw_images_dir : str | Path
        Directory containing the raw microscopy tiles.
    output_dir : str | Path
        Destination ``00_thumbnails/`` directory. Subfolders
        ``lod0/`` ... ``lod2/`` and ``index.json`` are written here.
    raw_ext : str, optional
        Raw image extension. Default ``".png"``.
    quality : int, optional
        WebP quality 0-100. Default 80 (visually lossless at thumbnail
        sizes; ~3-5 KB / lod0, ~30-60 KB / lod2).
    force_recompute : bool, optional
        When True, regenerates every thumbnail even if ``index.json``
        already lists matching signatures.
    max_workers : int, optional
        ThreadPool size for the per-image work. Default 8 (Pillow
        releases the GIL during JPEG/WebP encode + LANCZOS resize).
    progress_callback : callable, optional
        ``(pct, message)`` updates 0.0 -> 1.0.

    Returns
    -------
    dict
        ``{output_dir, n_images, n_skipped, n_failed, params, params_hash}``.
    """
    raw_dir = Path(raw_images_dir)
    out_root = Path(output_dir)
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw_images_dir does not exist: {raw_dir}")
    out_root.mkdir(parents=True, exist_ok=True)

    raws = _list_raw_images(raw_dir, raw_ext)
    if not raws:
        raise RuntimeError(f"no '*{raw_ext}' files in {raw_dir}")

    # Load previous index for cache hit detection.
    index_path = out_root / "index.json"
    prev_entries: Dict[str, Dict[str, Any]] = {}
    if index_path.exists() and not force_recompute:
        try:
            prev = json.loads(index_path.read_text(encoding="utf-8"))
            for e in prev.get("entries", []):
                prev_entries[str(e.get("raw_name"))] = e
        except Exception:
            prev_entries = {}

    # Decide what needs work.
    pending: List[Path] = []
    skipped: List[Dict[str, Any]] = []
    for r in raws:
        sig = _file_signature(r)
        prev = prev_entries.get(r.name)
        if (
            prev is not None
            and tuple(prev.get("signature") or ()) == sig
            and not force_recompute
        ):
            # Verify the cached files still exist.
            outs = prev.get("outputs") or {}
            all_present = all(
                (out_root.parent.parent / Path(p)).exists()
                if not Path(p).is_absolute()
                else Path(p).exists()
                for p in outs.values()
            )
            if all_present:
                skipped.append(prev)
                continue
        pending.append(r)

    msg.info(
        f"[pipeline.thumbnails] {len(raws)} raws, "
        f"{len(skipped)} cached, {len(pending)} to generate"
    )
    if progress_callback is not None:
        if not pending:
            progress_callback(1.0, f"All {len(skipped)} cached")
        else:
            progress_callback(0.0, f"Generating {len(pending)} thumbnails...")

    # Generate in parallel.
    new_entries: List[Dict[str, Any]] = []
    failures: List[Tuple[str, str]] = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
            futures = {
                ex.submit(_generate_one, r, out_root, quality): r
                for r in pending
            }
            done = 0
            for fut in as_completed(futures):
                r = futures[fut]
                try:
                    entry = fut.result()
                    new_entries.append(entry)
                except Exception as e:  # pragma: no cover
                    failures.append((r.name, str(e)))
                    msg.warning(
                        f"[pipeline.thumbnails] failed for {r.name}: {e}"
                    )
                done += 1
                if progress_callback is not None:
                    pct = done / max(1, len(pending))
                    progress_callback(
                        pct, f"thumbnail {done}/{len(pending)}"
                    )

    # Write the merged index.
    all_entries = skipped + new_entries
    all_entries.sort(key=lambda e: str(e.get("raw_name", "")))

    params: Dict[str, Any] = {
        "raw_images_dir": str(raw_dir),
        "raw_ext": raw_ext,
        "quality": int(quality),
        "lod_sizes": {str(k): list(v) for k, v in LOD_SIZES.items()},
    }
    index_payload = {
        "version": 1,
        "params": params,
        "params_hash": _hash_params(params),
        "n_images": len(all_entries),
        "n_skipped": len(skipped),
        "n_failed": len(failures),
        "entries": all_entries,
    }
    index_path.write_text(json.dumps(index_payload, indent=2), encoding="utf-8")
    msg.info(
        f"[pipeline.thumbnails] wrote index.json ({len(all_entries)} entries)"
    )

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    return {
        "output_dir": str(out_root),
        "n_images": len(all_entries),
        "n_skipped": len(skipped),
        "n_failed": len(failures),
        "params": params,
        "params_hash": index_payload["params_hash"],
    }
