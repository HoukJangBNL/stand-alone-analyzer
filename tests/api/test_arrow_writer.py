# tests/api/test_arrow_writer.py
import io
import pyarrow as pa
import pyarrow.ipc as ipc

from flake_analysis.api.services.arrow_writer import (
    write_arrow_ipc,
    arrow_or_json_response,
)


def _table():
    return pa.table({"a": pa.array([1, 2, 3], type=pa.int32()),
                     "b": pa.array([1.0, 2.0, 3.0], type=pa.float64())})


def test_write_arrow_ipc_roundtrips():
    buf = write_arrow_ipc(_table())
    assert isinstance(buf, bytes)
    assert len(buf) > 0
    reader = ipc.open_stream(io.BytesIO(buf))
    out = reader.read_all()
    assert out.column("a").to_pylist() == [1, 2, 3]


def test_arrow_or_json_response_arrow_accept():
    resp = arrow_or_json_response(
        _table(),
        accept_header="application/vnd.apache.arrow.stream",
    )
    assert resp.media_type == "application/vnd.apache.arrow.stream"
    assert isinstance(resp.body, bytes)
    reader = ipc.open_stream(io.BytesIO(resp.body))
    out = reader.read_all()
    assert out.num_rows == 3


def test_arrow_or_json_response_json_default():
    """Default Accept (or */*) returns JSON column-oriented payload."""
    resp = arrow_or_json_response(
        _table(),
        accept_header=None,
    )
    assert resp.media_type == "application/json"
    import json
    payload = json.loads(resp.body)
    assert payload == {"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]}


def test_arrow_or_json_response_explicit_json():
    resp = arrow_or_json_response(
        _table(),
        accept_header="application/json",
    )
    assert resp.media_type == "application/json"
