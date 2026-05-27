"""Worker entry-point: ``python -m flake_analysis.worker`` (P4.2.g).

Boots a procrastinate worker that pulls jobs from one or more queues and
executes them in this process. Designed for the GPU-host machine that
owns the SAM weights — typically launched as::

    uv run python -m flake_analysis.worker --queue gpu --name saa-gpu-1

CPU steps are executed in-process inside the API and are not handled
here (Phase 4 D5).

Signal handling
---------------
Procrastinate installs SIGTERM / SIGINT handlers when ``install_signal_handlers=True``
(its default). On SIGTERM the worker stops fetching new jobs, lets the
in-flight job finish (bounded by ``shutdown_graceful_timeout``), then
exits cleanly. We rely on that — no custom signal wiring needed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Sequence

from flake_analysis.worker import tasks as _tasks  # noqa: F401 — register tasks
from flake_analysis.worker.app import app

logger = logging.getLogger("flake_analysis.worker")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m flake_analysis.worker",
        description="Run a procrastinate worker for the SAA pipeline.",
    )
    p.add_argument(
        "--queue",
        action="append",
        dest="queues",
        default=None,
        metavar="QUEUE",
        help=(
            "Queue to consume from. Repeat to listen to multiple queues. "
            "If omitted, the worker listens to every queue."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of jobs the worker may run in parallel (default: 1).",
    )
    p.add_argument(
        "--name",
        type=str,
        default=None,
        help="Worker name surfaced in logs and JobContext (default: 'worker').",
    )
    return p


async def _amain(argv: Sequence[str]) -> None:
    """Async entry-point — argv parsing + run_worker_async.

    Split from :func:`main` so tests can drive the parsing/forwarding
    logic without spawning a real event loop via ``asyncio.run``.
    """
    args = _build_parser().parse_args(list(argv))
    logger.info(
        "starting procrastinate worker: queues=%s concurrency=%d name=%s",
        args.queues,
        args.concurrency,
        args.name,
    )
    await app.run_worker_async(
        queues=args.queues,
        concurrency=args.concurrency,
        name=args.name,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Synchronous CLI entry. ``argv`` defaults to ``sys.argv[1:]``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_amain(sys.argv[1:] if argv is None else list(argv)))


if __name__ == "__main__":
    main()
