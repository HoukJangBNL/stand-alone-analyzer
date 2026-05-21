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

Local-disk cache redirect (v0.2.16)
-----------------------------------
When ``output_dir`` resolves to a network mount (``/Volumes/...``
on macOS SMB) — or when the user opts in via env var
``STAND_ALONE_THUMB_LOCAL_CACHE=1`` — the actual WebP files are
written to a local cache directory under
``~/.cache/stand-alone-analyzer/thumbnails/<sha>/`` instead of the
analysis folder. Only ``index.json`` is left in
``output_dir/00_thumbnails/`` (so manifest discoverability works);
``index.json["cache_dir"]`` stores the absolute local path. This
saves ~1.5 GB of SMB writes per analysis and dramatically speeds up
the Explorer mosaic reads, which can do 500+ round-trips per
zoom step.

Backward compat: when ``index.json`` lacks ``cache_dir`` (v0.2.15
caches), readers fall back to the legacy in-folder
``00_thumbnails/lod{N}/<stem>.webp`` layout, so existing analysis
folders keep working without re-generation.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

from flake_analysis.core._compat import ProgressCallback, msg


# (lod_index, (width_px, height_px))
LOD_SIZES: Dict[int, Tuple[int, int]] = {
    0: (64, 40),     # substrate-grid cell default zoom-out
    1: (192, 120),   # mid zoom
    2: (480, 300),   # zoom-in just before raw
}

# Raw is the implicit LOD 3; never cached.
MAX_LOD: int = 3

# Env var that forces the local-disk cache redirect even when
# output_dir is on a local filesystem (mostly for tests).
_LOCAL_CACHE_ENV: str = "STAND_ALONE_THUMB_LOCAL_CACHE"

# Root of the per-analysis-folder local cache. The hashed subdir
# keeps each analysis folder's thumbnails isolated so the user can
# nuke a single project's cache without touching others.
_LOCAL_CACHE_ROOT: Path = (
    Path.home() / ".cache" / "stand-alone-analyzer" / "thumbnails"
)


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


def _should_redirect_to_local_cache(out_root: Path) -> bool:
    """Decide whether to route WebP writes to a local-disk cache.

    Triggers when ``out_root.resolve()`` lives under ``/Volumes/``
    (macOS SMB mount convention) or when the env-var opt-in is set.
    """
    if os.environ.get(_LOCAL_CACHE_ENV, "").strip() in ("1", "true", "yes"):
        return True
    try:
        resolved = str(out_root.resolve())
    except OSError:
        return False
    return resolved.startswith("/Volumes/")


def _local_cache_dir_for(out_root: Path) -> Path:
    """Return the per-analysis-folder local cache directory.

    Hash key = first 16 hex chars of ``sha256(absolute analysis
    folder path)``. The analysis folder is ``out_root.parent`` (the
    caller passes ``<analysis>/00_thumbnails/`` as ``output_dir``).
    """
    try:
        analysis_folder = out_root.resolve().parent
    except OSError:
        analysis_folder = out_root.parent
    digest = hashlib.sha256(
        str(analysis_folder).encode("utf-8")
    ).hexdigest()[:16]
    return _LOCAL_CACHE_ROOT / digest


def _generate_one(
    raw_path: Path,
    write_root: Path,
    quality: int,
) -> Dict[str, Any]:
    """Generate every LOD thumbnail for a single raw image.

    Writes to ``write_root/lod{N}/<stem>.webp``. ``write_root`` is
    either the analysis-folder ``00_thumbnails/`` or the local
    cache directory — the per-entry ``outputs`` paths are stored
    relative to ``write_root`` so the index can be re-rooted
    transparently at read time.
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
        lod_dir = write_root / f"lod{lod}"
        lod_dir.mkdir(parents=True, exist_ok=True)
        out_path = lod_dir / f"{stem}.webp"
        thumb.save(out_path, format="WEBP", quality=quality, method=4)
        # Store the path *relative to write_root* — readers join with
        # ``cache_dir`` (when present) or the legacy
        # ``00_thumbnails/`` root, so the same shape works either way.
        out_paths[f"lod{lod}"] = f"lod{lod}/{stem}.webp"

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
    max_workers: int = 16,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Pre-render LOD thumbnails for every raw image.

    Parameters
    ----------
    raw_images_dir : str | Path
        Directory containing the raw microscopy tiles.
    output_dir : str | Path
        Destination ``00_thumbnails/`` directory. ``index.json`` is
        always written here. WebP tiles are written here too **unless**
        the directory lives on a network mount (``/Volumes/...``) or
        ``STAND_ALONE_THUMB_LOCAL_CACHE=1`` is set, in which case they
        are routed to ``~/.cache/stand-alone-analyzer/thumbnails/<sha>/``
        and the local path is recorded as ``index.json["cache_dir"]``.
    raw_ext : str, optional
        Raw image extension. Default ``".png"``.
    quality : int, optional
        WebP quality 0-100. Default 80 (visually lossless at thumbnail
        sizes; ~3-5 KB / lod0, ~30-60 KB / lod2).
    force_recompute : bool, optional
        When True, regenerates every thumbnail even if ``index.json``
        already lists matching signatures.
    max_workers : int, optional
        ThreadPool size for the per-image work. Default 16 (Pillow
        releases the GIL during JPEG/WebP encode + LANCZOS resize, so
        on high-latency mounts more workers ~linearly improve
        throughput up to this point).
    progress_callback : callable, optional
        ``(pct, message)`` updates 0.0 -> 1.0.

    Returns
    -------
    dict
        ``{output_dir, n_images, n_skipped, n_failed, params,
        params_hash, cache_dir}``. ``cache_dir`` is ``None`` when the
        WebPs were written in-folder (legacy layout); otherwise it is
        the absolute path of the local-disk cache directory.
    """
    raw_dir = Path(raw_images_dir)
    out_root = Path(output_dir)
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw_images_dir does not exist: {raw_dir}")
    out_root.mkdir(parents=True, exist_ok=True)

    raws = _list_raw_images(raw_dir, raw_ext)
    if not raws:
        raise RuntimeError(f"no '*{raw_ext}' files in {raw_dir}")

    # Decide where the actual WebP bytes go. ``write_root`` may differ
    # from ``out_root`` when the redirect fires; ``index.json`` is
    # always written to ``out_root`` so ``manifest.outputs[index_json]``
    # remains accurate.
    redirect = _should_redirect_to_local_cache(out_root)
    if redirect:
        write_root = _local_cache_dir_for(out_root)
        write_root.mkdir(parents=True, exist_ok=True)
        msg.info(
            f"[pipeline.thumbnails] network mount detected — "
            f"redirecting WebP writes to {write_root}"
        )
    else:
        write_root = out_root

    # Load previous index for cache hit detection.
    index_path = out_root / "index.json"
    prev_entries: Dict[str, Dict[str, Any]] = {}
    prev_cache_dir: Optional[str] = None
    if index_path.exists() and not force_recompute:
        try:
            prev = json.loads(index_path.read_text(encoding="utf-8"))
            for e in prev.get("entries", []):
                prev_entries[str(e.get("raw_name"))] = e
            prev_cache_dir = prev.get("cache_dir")
        except Exception:
            prev_entries = {}
            prev_cache_dir = None

    # Where to look when verifying that a previously-cached entry's
    # files still exist on disk. Honour the *previous* run's
    # ``cache_dir`` when present so we don't invalidate a still-valid
    # cache because the redirect policy flipped.
    prev_read_root = (
        Path(prev_cache_dir) if prev_cache_dir else out_root
    )

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
            outs = prev.get("outputs") or {}
            # v0.2.15 entries stored ``00_thumbnails/lod{N}/...`` with
            # the analysis-folder prefix; v0.2.16 stores ``lod{N}/...``
            # relative to write_root. Resolve both shapes.
            def _resolve(rel: str) -> Path:
                p = Path(rel)
                if p.is_absolute():
                    return p
                if rel.startswith("00_thumbnails/"):
                    # Legacy v0.2.15: relative to analysis folder.
                    return out_root.parent / rel
                return prev_read_root / rel
            all_present = all(_resolve(p).exists() for p in outs.values())
            if all_present:
                skipped.append(prev)
                continue
        pending.append(r)

    msg.info(
        f"[pipeline.thumbnails] {len(raws)} raws, "
        f"{len(skipped)} cached, {len(pending)} to generate "
        f"(workers={max_workers}, redirect={redirect})"
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
                ex.submit(_generate_one, r, write_root, quality): r
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
    cache_dir_str: Optional[str] = (
        str(write_root.resolve()) if redirect else None
    )
    index_payload: Dict[str, Any] = {
        "version": 1,
        "params": params,
        "params_hash": _hash_params(params),
        "n_images": len(all_entries),
        "n_skipped": len(skipped),
        "n_failed": len(failures),
        "entries": all_entries,
    }
    # Only emit the field when the redirect is active, to keep the
    # legacy-layout index.json byte-identical for non-redirected runs.
    if cache_dir_str is not None:
        index_payload["cache_dir"] = cache_dir_str
    index_path.write_text(json.dumps(index_payload, indent=2), encoding="utf-8")
    msg.info(
        f"[pipeline.thumbnails] wrote index.json ({len(all_entries)} entries"
        f"{', cache_dir=' + cache_dir_str if cache_dir_str else ''})"
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
        "cache_dir": cache_dir_str,
    }
