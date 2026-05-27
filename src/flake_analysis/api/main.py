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
    materials, scans,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan hook: startup banner + shutdown cleanup."""
    try:
        from flake_analysis import __version__
    except ImportError:
        __version__ = "unknown"

    print(f"Stand-Alone Analyzer API v{__version__} starting...")
    yield
    print("Stand-Alone Analyzer API shutting down...")

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

    return app

app = create_app()
