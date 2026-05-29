"""Measurement & model-swap utilities — prod-grade unit methods.

These functions are the boundary between systemd-managed worker state
and ad-hoc Python (measurement scripts, future prod GPU dispatcher).

Currently shipped:
* :func:`load_worker_env`     — bridge systemd EnvironmentFile= → os.environ
* :func:`resolve_model_meta`  — local path or s3:// URI → deterministic
                                 local artifact + name/sha256/source_uri
                                 metadata
* :func:`build_defer_payload` — kwargs for app.configure_task('run_sam').defer

Designed to be called from:
* ``scripts/sam/measure-defer.py`` (this plan)
* future prod GPU dispatcher (out of scope here)
"""
from __future__ import annotations

import re
from pathlib import Path


def load_worker_env(env_file: Path = Path("/etc/flake-analysis-worker.env")) -> dict[str, str]:
    """Parse a systemd-style EnvironmentFile into a dict of env vars.

    Supports::

        KEY=value
        KEY="quoted value with spaces"
        KEY='single quoted'
        # comment lines (any leading whitespace)
        <blank lines>

    Raises:
        FileNotFoundError: env_file does not exist.
        ValueError: any non-blank, non-comment line is missing '='.
    """
    env_file = Path(env_file)
    if not env_file.exists():
        raise FileNotFoundError(f"worker env file not found: {env_file}")

    out: dict[str, str] = {}
    for lineno, raw in enumerate(env_file.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"malformed line {lineno} in {env_file}: missing '='"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching surrounding quotes — single or double.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


# Default download dir on the GPU worker. Overridable in tests via monkeypatch.
_WEIGHTS_LOCAL_DIR = Path("/opt/sam/weights")
_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def _read_sidecar_sha256(sidecar_text: str) -> str:
    """Parse the first 64-hex token of a .sha256 sidecar file."""
    match = re.search(r"\b([0-9a-f]{64})\b", sidecar_text)
    if not match:
        raise ValueError(f"no sha256 in sidecar: {sidecar_text!r}")
    return match.group(1)


def resolve_model_meta(weights_uri: str) -> dict[str, str]:
    """Resolve a weights reference into a deterministic local artifact + metadata.

    Args:
        weights_uri: Either an absolute local path to a .pt file, or an
            ``s3://bucket/prefix/name.pt`` URI. A sidecar
            ``<name>.pt.sha256`` is required at the same prefix; the
            sidecar must contain a 64-hex sha256 token.

    Returns:
        Dict with keys ``name``, ``sha256``, ``source_uri``, ``local_path``.

    Raises:
        ValueError: invalid scheme, missing sidecar, malformed sidecar.
    """
    if weights_uri.startswith("s3://"):
        return _resolve_s3(weights_uri)
    if weights_uri.startswith("/") or weights_uri.startswith("file://"):
        local = (
            weights_uri[len("file://"):]
            if weights_uri.startswith("file://")
            else weights_uri
        )
        return _resolve_local(Path(local))
    raise ValueError(f"unsupported weights_uri scheme: {weights_uri!r}")


def _resolve_local(pt_path: Path) -> dict[str, str]:
    if not pt_path.exists():
        raise ValueError(f"weights_uri points to missing file: {pt_path}")
    sidecar = pt_path.with_name(pt_path.name + ".sha256")
    if not sidecar.exists():
        raise ValueError(f"sidecar sha256 file missing: {sidecar}")
    sha = _read_sidecar_sha256(sidecar.read_text())
    return {
        "name": pt_path.stem,
        "sha256": sha,
        "source_uri": f"file://{pt_path}",
        "local_path": str(pt_path),
    }


def _resolve_s3(s3_uri: str) -> dict[str, str]:
    import boto3

    match = _S3_URI_RE.match(s3_uri)
    if not match:
        raise ValueError(f"malformed s3 URI: {s3_uri!r}")
    bucket, key = match.group(1), match.group(2)
    if not key.endswith(".pt"):
        raise ValueError(f"weights URI must end in .pt: {s3_uri!r}")

    s3 = boto3.client("s3")
    sidecar_obj = s3.get_object(Bucket=bucket, Key=key + ".sha256")
    sha = _read_sidecar_sha256(sidecar_obj["Body"].read().decode("utf-8"))

    _WEIGHTS_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    local_path = _WEIGHTS_LOCAL_DIR / Path(key).name
    # Idempotent: skip download if local sha already matches sidecar.
    if local_path.exists():
        import hashlib
        h = hashlib.sha256()
        with local_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() == sha.lower():
            return {
                "name": Path(key).stem,
                "sha256": sha,
                "source_uri": s3_uri,
                "local_path": str(local_path),
            }

    s3.download_file(bucket, key, str(local_path))
    return {
        "name": Path(key).stem,
        "sha256": sha,
        "source_uri": s3_uri,
        "local_path": str(local_path),
    }


def build_defer_payload(
    *,
    run_id: int,
    scan_id: int,  # noqa: ARG001 — reserved for future use; kept for caller stability
    model_meta: dict,
    dataset_dir: Path,
    analysis_folder: Path,
) -> dict:
    """Construct kwargs for ``app.configure_task('run_sam', queue='gpu').defer_async``.

    Pure function: no DB, no IO. ``model_meta`` must include
    ``local_path`` (set by :func:`resolve_model_meta`); only the
    user-facing keys (``name``/``sha256``/``source_uri``) propagate
    into the deferred payload — ``local_path`` is consumed here and
    stripped (the path is what becomes ``weights_path``).
    """
    if "local_path" not in model_meta:
        raise ValueError(
            "model_meta missing 'local_path' — call resolve_model_meta first"
        )
    return {
        "run_id": run_id,
        "raw_images_dir": str(dataset_dir),
        "analysis_folder": str(analysis_folder),
        "weights_path": model_meta["local_path"],
        "model_meta": {
            "name": model_meta["name"],
            "sha256": model_meta["sha256"],
            "source_uri": model_meta["source_uri"],
        },
    }
