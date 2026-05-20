"""FastAPI app factory per integrated design §2, backend design §1."""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flake_analysis.api.settings import Settings
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import health, version

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

    return app

app = create_app()
