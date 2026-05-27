"""P4.2.c — worker tasks module unit tests.

The worker module owns the procrastinate App definition and the
``run_sam`` task. The task must:

1. Defer cleanly: ``app.tasks["run_sam"].defer_async(...)`` produces a
   queued JobRow on the connector.
2. Execute via ``app.run_worker_async(wait=False)`` against an
   :class:`procrastinate.testing.InMemoryConnector`.
3. Call ``flake_analysis.pipeline.sam.run_sam_step`` with the args we
   passed in.
4. Forward progress samples through an injectable sink (pg_notify in
   prod; a list collector in tests). Each sample carries
   ``{run_id, step="sam", progress, message}``.
5. Send a terminal "completed" sample after the inner runner returns.
6. On runner exception, send a terminal "error" sample and re-raise so
   procrastinate marks the job failed.

We exercise the same task object the production app exposes (no
re-decoration), but swap the connector via :py:meth:`App.replace_connector`
so the InMemoryConnector handles defer/fetch/finish.

Notes:
- Tests don't need pg_notify wiring — we patch the module-level
  ``_emit_progress`` sink with a collector list.
- ``run_sam_step`` (the in-process pipeline wrapper) is patched at its
  import site inside ``flake_analysis.worker.tasks`` so the real SAM
  engine never runs.
"""
from __future__ import annotations

import pytest
from procrastinate.testing import InMemoryConnector


@pytest.fixture
def collector(monkeypatch):
    """Collect progress samples instead of pg_notifying."""
    from flake_analysis.worker import tasks as worker_tasks

    samples: list[dict] = []

    def fake_emit(*, run_id: int, payload: dict) -> None:
        samples.append({"run_id": run_id, **payload})

    monkeypatch.setattr(worker_tasks, "_emit_progress", fake_emit)
    return samples


@pytest.fixture
def fake_runner(monkeypatch):
    """Replace the SAM pipeline wrapper with a controllable mock."""
    from flake_analysis.worker import tasks as worker_tasks

    calls: list[dict] = []

    def fake(*, raw_images_dir, analysis_folder, weights_path, device, progress_callback):
        calls.append(
            {
                "raw_images_dir": str(raw_images_dir),
                "analysis_folder": str(analysis_folder),
                "weights_path": str(weights_path),
                "device": device,
            }
        )
        if progress_callback:
            progress_callback(0.5, "halfway")
            progress_callback(1.0, "done")
        return {"images": 2, "masks_total": 7, "errors": 0, "per_image": {}}

    monkeypatch.setattr(worker_tasks, "run_sam_step", fake)
    return calls


@pytest.fixture
def in_memory_app():
    """Yield the production app with its connector swapped for InMemoryConnector.

    Importing the worker tasks module registers ``@app.task`` handlers on
    the production app; without it the app's ``tasks`` dict would be
    empty until procrastinate's lazy ``perform_import_paths()`` ran (it
    only fires inside ``_worker()`` / ``configure_task()``, not on a bare
    ``app.tasks["run_sam"]`` lookup).
    """
    from flake_analysis.worker import tasks as _tasks  # noqa: F401 — register tasks
    from flake_analysis.worker.app import app

    connector = InMemoryConnector()
    with app.replace_connector(connector) as test_app:
        yield test_app, connector


@pytest.mark.asyncio
async def test_defer_run_sam_enqueues_job(in_memory_app):
    """defer_async writes a job to the in-memory queue."""
    app, connector = in_memory_app

    job_id = await app.tasks["run_sam"].defer_async(
        run_id=1,
        raw_images_dir="/tmp/raw",
        analysis_folder="/tmp/folder",
        weights_path="/tmp/weights.pt",
        device=None,
    )
    assert job_id > 0
    job = connector.jobs[job_id]
    assert job["task_name"] == "run_sam"
    assert job["queue_name"] == "gpu"
    assert job["args"]["run_id"] == 1
    assert job["args"]["weights_path"] == "/tmp/weights.pt"


@pytest.mark.asyncio
async def test_worker_executes_run_sam_and_forwards_progress(
    in_memory_app, fake_runner, collector
):
    """run_worker_async drains the queue, calls run_sam_step, emits progress + completed."""
    app, connector = in_memory_app

    await app.tasks["run_sam"].defer_async(
        run_id=42,
        raw_images_dir="/tmp/raw",
        analysis_folder="/tmp/folder",
        weights_path="/tmp/weights.pt",
        device="cuda:0",
    )
    await app.run_worker_async(wait=False, install_signal_handlers=False)

    # Inner SAM wrapper called once with the deferred args.
    assert len(fake_runner) == 1
    call = fake_runner[0]
    assert call["raw_images_dir"] == "/tmp/raw"
    assert call["weights_path"] == "/tmp/weights.pt"
    assert call["device"] == "cuda:0"

    # Progress samples: 2 progress + 1 completed.
    progress = [s for s in collector if s["type"] == "progress"]
    completed = [s for s in collector if s["type"] == "completed"]
    assert len(progress) == 2
    assert progress[0]["progress"] == 0.5
    assert progress[0]["message"] == "halfway"
    assert progress[1]["progress"] == 1.0
    assert len(completed) == 1
    assert completed[0]["result"]["images"] == 2
    assert completed[0]["result"]["masks_total"] == 7
    # Every sample carries run_id.
    assert all(s["run_id"] == 42 for s in collector)

    # The procrastinate job ended in 'succeeded'.
    finished = connector.finished_jobs
    assert len(finished) == 1
    assert finished[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_worker_emits_error_on_runner_exception(
    in_memory_app, monkeypatch, collector
):
    """Runner exception → 'error' sample + procrastinate marks job 'failed'."""
    from flake_analysis.worker import tasks as worker_tasks

    def boom(**kwargs):
        raise RuntimeError("weights not found")

    monkeypatch.setattr(worker_tasks, "run_sam_step", boom)

    app, connector = in_memory_app
    await app.tasks["run_sam"].defer_async(
        run_id=7,
        raw_images_dir="/tmp/raw",
        analysis_folder="/tmp/folder",
        weights_path="/tmp/missing.pt",
        device=None,
    )
    await app.run_worker_async(wait=False, install_signal_handlers=False)

    errors = [s for s in collector if s["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["run_id"] == 7
    assert errors[0]["code"] == "RuntimeError"
    assert "weights not found" in errors[0]["message"]

    # No 'completed' sample on failure.
    assert not [s for s in collector if s["type"] == "completed"]

    # Procrastinate marks the job failed.
    finished = connector.finished_jobs
    assert len(finished) == 1
    assert finished[0]["status"] == "failed"
