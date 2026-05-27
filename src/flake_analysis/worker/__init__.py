"""Procrastinate worker package — GPU-bound steps deferred from the API.

Phase 4 P4.2 segmentation web integration: SAM inference is too heavy to
run in the API process, so the route enqueues a procrastinate job and a
GPU-resident worker process picks it up. CPU steps stay in-process per
the Phase 4 D5 decision.

Public surface:
    - :data:`flake_analysis.worker.app.app` — the procrastinate App.
    - :mod:`flake_analysis.worker.tasks` — task definitions registered on
      the app at import time.
"""
