"""FastAPI app factory per integrated design §2, backend design §1."""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flake_analysis.api.settings import Settings
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import (
    health, version, projects, data, run, run_pipeline, selector, clustering, explorer, static, auth, admin, admin_usage,
    materials, scans, gpu,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan hook: startup banner + procrastinate pool open/close.

    Procrastinate 3.x requires the App's connector pool to be explicitly
    opened before ``defer_async`` will succeed; otherwise it raises
    ``AppNotOpen`` ("App was not open. Procrastinate App needs to be
    opened using ``app.open_async()``..."). The API process defers SAM
    jobs from :mod:`flake_analysis.api.routes.run`, so we open the pool
    once at startup and close it on shutdown. The connector itself is
    constructed at import time in :mod:`flake_analysis.worker.app` and
    only the pool round-trip happens here.
    """
    try:
        from flake_analysis import __version__
    except ImportError:
        __version__ = "unknown"

    print(f"Stand-Alone Analyzer API v{__version__} starting...")
    # Open procrastinate pool. Importing tasks via the App's
    # ``import_paths`` is automatic on first defer; here we just need
    # the connection pool live.
    from flake_analysis.worker.app import app as procrastinate_app
    await procrastinate_app.open_async()
    try:
        yield
    finally:
        print("Stand-Alone Analyzer API shutting down...")
        try:
            await procrastinate_app.close_async()
        except Exception:  # noqa: BLE001 — never block shutdown on cleanup
            pass

def create_app() -> FastAPI:
    """FastAPI app factory."""
    settings = Settings()

    # B2 — fail fast if S3 upload target is unconfigured. The presign and
    # complete routes need a bucket; without one every upload would 500
    # mid-flow. Surface the misconfig at boot so deploys crash before
    # accepting traffic.
    if not settings.s3_bucket:
        raise RuntimeError(
            "SAA_S3_BUCKET is not configured. Set the SAA_S3_BUCKET environment "
            "variable (or .env entry) to the S3 upload bucket name before starting "
            "the API."
        )

    app = FastAPI(
        title="Stand-Alone Analyzer API",
        version="v1",
        lifespan=lifespan,
    )

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
            expose_headers=["X-Request-Id"],
            max_age=600,
        )

    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(version.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(admin_usage.router, prefix="/api/v1")
    app.include_router(materials.router, prefix="/api/v1")
    app.include_router(scans.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(data.router, prefix="/api/v1")
    app.include_router(run.router, prefix="/api/v1")
    app.include_router(run_pipeline.router, prefix="/api/v1")
    app.include_router(selector.router, prefix="/api/v1")
    app.include_router(clustering.router, prefix="/api/v1")
    app.include_router(explorer.router, prefix="/api/v1")
    app.include_router(static.router, prefix="/api/v1")
    app.include_router(gpu.router, prefix="/api/v1")

    return app

app = create_app()
