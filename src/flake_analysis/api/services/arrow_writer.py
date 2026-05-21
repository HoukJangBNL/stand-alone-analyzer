"""Arrow IPC + JSON content negotiation per backend design §1.3 'Why Arrow IPC'."""
from __future__ import annotations
import io
import json

import pyarrow as pa
import pyarrow.ipc as ipc
from fastapi import Response

ARROW_MIME = "application/vnd.apache.arrow.stream"


def write_arrow_ipc(table: pa.Table) -> bytes:
    """Serialize a pyarrow Table to Arrow IPC stream bytes."""
    sink = io.BytesIO()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def _table_to_json_columns(table: pa.Table) -> bytes:
    """Column-oriented JSON: {col_name: [values]} — matches frontend typed-array shape."""
    payload = {name: table.column(name).to_pylist() for name in table.column_names}
    return json.dumps(payload).encode("utf-8")


def arrow_or_json_response(
    table: pa.Table,
    *,
    accept_header: str | None,
) -> Response:
    """Return Arrow IPC if the client asked for it, else JSON column-oriented."""
    wants_arrow = bool(accept_header) and ARROW_MIME in (accept_header or "")
    if wants_arrow:
        return Response(content=write_arrow_ipc(table), media_type=ARROW_MIME)
    return Response(content=_table_to_json_columns(table), media_type="application/json")
