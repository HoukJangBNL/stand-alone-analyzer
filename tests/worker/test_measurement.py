"""Unit tests for the prod-grade measurement utility module."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_load_worker_env_basic(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "SAA_DB_HOST=qpressdb.example.com\n"
        "SAA_DB_PORT=5432\n"
        "SAA_DB_NAME=qpress\n"
    )
    out = load_worker_env(env_file)
    assert out == {
        "SAA_DB_HOST": "qpressdb.example.com",
        "SAA_DB_PORT": "5432",
        "SAA_DB_NAME": "qpress",
    }


def test_load_worker_env_quoted_values(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        'SAA_DB_PASSWORD="hunter2 with spaces"\n'
        "SAA_DB_USER='uname'\n"
    )
    out = load_worker_env(env_file)
    assert out["SAA_DB_PASSWORD"] == "hunter2 with spaces"
    assert out["SAA_DB_USER"] == "uname"


def test_load_worker_env_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "# top comment\n"
        "\n"
        "SAA_DB_HOST=h\n"
        "  # indented comment\n"
        "SAA_DB_PORT=5432\n"
    )
    out = load_worker_env(env_file)
    assert out == {"SAA_DB_HOST": "h", "SAA_DB_PORT": "5432"}


def test_load_worker_env_missing_file_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    with pytest.raises(FileNotFoundError):
        load_worker_env(tmp_path / "nonexistent.env")


def test_load_worker_env_malformed_line_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text("LINE_WITHOUT_EQUALS\nSAA_DB_HOST=h\n")
    with pytest.raises(ValueError, match="malformed"):
        load_worker_env(env_file)


def test_resolve_model_meta_local_with_sidecar(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    pt = tmp_path / "merged_m3.pt"
    pt.write_bytes(b"fake-weights")
    sidecar = tmp_path / "merged_m3.pt.sha256"
    sidecar.write_text(
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef  merged_m3.pt\n"
    )

    meta = resolve_model_meta(str(pt))
    assert meta["name"] == "merged_m3"
    assert meta["sha256"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert meta["source_uri"] == f"file://{pt}"
    assert meta["local_path"] == str(pt)


def test_resolve_model_meta_local_missing_sidecar_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    pt = tmp_path / "no_sidecar.pt"
    pt.write_bytes(b"x")
    with pytest.raises(ValueError, match="sidecar"):
        resolve_model_meta(str(pt))


def test_resolve_model_meta_s3_uri_downloads(monkeypatch, tmp_path: Path) -> None:
    """S3 URI: downloads .pt + reads .sha256 sidecar, returns metadata
    with the s3:// URI as source_uri. moto provides the in-memory S3."""
    import boto3
    from moto import mock_aws

    from flake_analysis.worker import measurement as meas_mod

    # Redirect download dir into tmp_path so the test never touches /opt/sam.
    monkeypatch.setattr(meas_mod, "_WEIGHTS_LOCAL_DIR", tmp_path / "weights")

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-2")
        s3.create_bucket(
            Bucket="qpress-uploads",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        s3.put_object(
            Bucket="qpress-uploads",
            Key="internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt",
            Body=b"fake-weights",
        )
        s3.put_object(
            Bucket="qpress-uploads",
            Key="internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt.sha256",
            Body=b"3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc  sam2.1_hiera_large.merged_m3.3ec586fc.pt\n",
        )

        meta = meas_mod.resolve_model_meta(
            "s3://qpress-uploads/internal/sam/merged_m3/"
            "sam2.1_hiera_large.merged_m3.3ec586fc.pt"
        )

    assert meta["name"] == "sam2.1_hiera_large.merged_m3.3ec586fc"
    assert meta["sha256"] == "3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc3ec586fc"
    assert meta["source_uri"].startswith("s3://qpress-uploads/")
    assert Path(meta["local_path"]).exists()
    assert Path(meta["local_path"]).read_bytes() == b"fake-weights"


def test_resolve_model_meta_invalid_uri_raises() -> None:
    from flake_analysis.worker.measurement import resolve_model_meta

    with pytest.raises(ValueError, match="weights_uri|scheme"):
        resolve_model_meta("ftp://example.com/x.pt")


def test_build_defer_payload_shape(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import build_defer_payload

    payload = build_defer_payload(
        run_id=42,
        scan_id=287,
        model_meta={
            "name": "merged_m3",
            "sha256": "abc",
            "source_uri": "s3://qpress-uploads/internal/sam/merged_m3/x.pt",
            "local_path": "/opt/sam/weights/x.pt",
        },
        dataset_dir=tmp_path / "dataset",
        analysis_folder=tmp_path / "an",
    )

    assert payload == {
        "run_id": 42,
        "raw_images_dir": str(tmp_path / "dataset"),
        "analysis_folder": str(tmp_path / "an"),
        "weights_path": "/opt/sam/weights/x.pt",
        "model_meta": {
            "name": "merged_m3",
            "sha256": "abc",
            "source_uri": "s3://qpress-uploads/internal/sam/merged_m3/x.pt",
        },
    }


def test_build_defer_payload_missing_local_path_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import build_defer_payload

    with pytest.raises(ValueError, match="local_path"):
        build_defer_payload(
            run_id=1,
            scan_id=1,
            model_meta={"name": "x", "sha256": "y", "source_uri": "z"},
            dataset_dir=tmp_path,
            analysis_folder=tmp_path,
        )
