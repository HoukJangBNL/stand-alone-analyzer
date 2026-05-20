"""request_id ContextVar + middleware per integrated design §6, deployment §9.3."""
from __future__ import annotations
import uuid
from contextvars import ContextVar
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Retrieve the current request_id from context."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> str:
    """Set the request_id in context and return it."""
    _request_id_var.set(request_id)
    return request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Injects a UUID4 request_id on every request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
