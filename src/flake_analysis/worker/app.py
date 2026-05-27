"""Procrastinate App definition (P4.2.c).

The :data:`app` object is the single source of truth for queue/task
definitions used by both the API process (defer side) and the worker
process (consume side).

Connection wiring follows the same SAA_DB_* env vars the rest of the
project uses (see :mod:`flake_analysis.db.url`). The connector itself
is constructed at import time, but the pool is only opened lazily by
procrastinate when the app first defers a job or starts a worker —
importing this module never touches the network.

For tests, swap the connector via :py:meth:`procrastinate.App.replace_connector`
with a :class:`procrastinate.testing.InMemoryConnector` (see
``tests/worker/test_tasks.py``).
"""
from __future__ import annotations

import procrastinate

from flake_analysis.db.url import DbSettings


def _connector_kwargs() -> dict:
    """Build psycopg connection kwargs from SAA_DB_* env.

    psycopg accepts ``host/port/user/password/dbname`` as keyword args, so
    we pass them straight through rather than building a DSN string. This
    sidesteps any percent-escape edge cases in passwords.
    """
    s = DbSettings()
    kwargs: dict = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if s.db_user:
        kwargs["user"] = s.db_user
    if s.db_password:
        kwargs["password"] = s.db_password
    return kwargs


# Module-level App. Importing this module does NOT open the pool — that
# happens on first defer_async / run_worker_async call. Tests can swap
# the connector wholesale via app.replace_connector(InMemoryConnector()).
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(**_connector_kwargs()),
    # Force-import the tasks module so @app.task decorators register before
    # the worker starts looking for handlers.
    import_paths=["flake_analysis.worker.tasks"],
)
