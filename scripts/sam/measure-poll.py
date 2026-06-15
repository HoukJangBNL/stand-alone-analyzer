#!/usr/bin/env python3
"""Single-shot procrastinate job status poller executed via SSM.

Connects to the same Postgres DB that measure-defer.py uses, queries
the status of a single procrastinate job, and prints exactly one line:

    JOB_STATUS=<status>

where <status> is one of:
    - The procrastinate status string (todo/doing/succeeded/failed/aborted/
      cancelled/aborting) if the row exists
    - NOT_FOUND if no row with the given job_id
    - DB_ERROR:<short msg> if any DB connection or query error occurs

Always exits 0 (success) so bash caller can parse stdout reliably
without fighting SSM exit code translation. The polling loop stays in
measure-run.sh (this script runs once per tick via SSM).

Usage (executed via SSM RunShellScript on the GPU worker):

    sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 \\
        /tmp/measure-poll.py --job-id <N>

    OR (to cancel orphaned todo jobs before defer):

    sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 \\
        /tmp/measure-poll.py --cancel-stale-jobs

Re-uses the same DB connection path that measure-defer.py proved works
on all 4 attempts:
    load_worker_env → DbSettings() → psycopg.connect → query
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure the repo `src/` is on sys.path. AMI lays it out at this path.
_REPO_SRC = Path("/opt/sam/stand-alone-analyzer/src")
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Procrastinate job status poller (single-shot)"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--job-id",
        type=int,
        help="Query status of a single procrastinate job by ID",
    )
    mode.add_argument(
        "--cancel-stale-jobs",
        action="store_true",
        help="Delete all todo jobs from the gpu queue (orphan cleanup)",
    )
    p.add_argument(
        "--worker-env-file",
        default="/etc/flake-analysis-worker.env",
        help="systemd EnvironmentFile to inherit RDS creds from",
    )
    return p.parse_args()


def _conn_kwargs() -> dict:
    """Build psycopg connection params from DbSettings.

    Mirrors launcher.py::PgAdvisoryLock._conn_kwargs() exactly: reads
    DbSettings (which picks up SAA_DB_* from os.environ after
    load_worker_env), and forces sslmode=require for non-localhost.
    """
    from flake_analysis.db.url import DbSettings, _require_ssl

    s = DbSettings()
    kwargs = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if _require_ssl(s.db_host):
        kwargs["sslmode"] = "require"
    if s.db_user:
        kwargs["user"] = s.db_user
    if s.db_password:
        kwargs["password"] = s.db_password
    return kwargs


def query_job_status(job_id: int) -> None:
    """Query procrastinate_jobs.status for a given job_id.

    Prints exactly one line to stdout: JOB_STATUS=<value>
    Always exits 0 (caller parses stdout, not exit code).
    """
    try:
        import psycopg

        # Import AFTER load_worker_env() updates os.environ so DbSettings
        # reads SAA_DB_* correctly.
        conn = psycopg.connect(**_conn_kwargs(), autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM procrastinate_jobs WHERE id=%s",
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    print("JOB_STATUS=NOT_FOUND")
                else:
                    print(f"JOB_STATUS={row[0]}")
        finally:
            conn.close()
    except Exception as e:
        # Encode error in stdout so bash can parse it as a token.
        # Keep message short (no multi-line stack traces).
        msg = str(e).replace("\n", " ").strip()[:100]
        print(f"JOB_STATUS=DB_ERROR:{msg}")


def cancel_stale_jobs() -> None:
    """Delete all todo jobs from procrastinate_jobs on the gpu queue.

    This is the orphan cleanup mode: before deferring a fresh job,
    ensure no stale todo jobs exist that could be claimed by the worker
    instead of our new job. Prints the count of deleted rows.

    Always exits 0 (bash caller checks stdout for verification).
    """
    try:
        import psycopg

        conn = psycopg.connect(**_conn_kwargs(), autocommit=True)
        try:
            with conn.cursor() as cur:
                # Procrastinate's queue_name is VARCHAR; the worker
                # listens to "gpu". Delete any todo jobs on that queue.
                cur.execute(
                    "DELETE FROM procrastinate_jobs "
                    "WHERE queue_name='gpu' AND status='todo' "
                    "RETURNING id"
                )
                deleted_ids = [row[0] for row in cur.fetchall()]
                if deleted_ids:
                    print(f"CANCELLED_JOBS={','.join(map(str, deleted_ids))}")
                else:
                    print("CANCELLED_JOBS=NONE")
        finally:
            conn.close()
    except Exception as e:
        msg = str(e).replace("\n", " ").strip()[:100]
        print(f"CANCELLED_JOBS=DB_ERROR:{msg}")


def main() -> int:
    args = _parse_args()

    # Load worker env BEFORE any imports that read DbSettings.
    from flake_analysis.worker.measurement import load_worker_env

    os.environ.update(load_worker_env(Path(args.worker_env_file)))

    if args.job_id is not None:
        query_job_status(args.job_id)
    else:
        cancel_stale_jobs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
