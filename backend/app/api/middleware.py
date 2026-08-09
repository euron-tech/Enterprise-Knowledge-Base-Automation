"""Middleware: correlation id, security headers, body-size limit, access log."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import (
    correlation_id_var,
    get_logger,
    log_event,
    tenant_id_var,
    user_id_var,
)

logger = get_logger("api.access")
CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        cid = "".join(ch for ch in cid if ch.isalnum() or ch in "-_")[:64] or str(
            uuid.uuid4()
        )
        correlation_id_var.set(cid)
        tenant_id_var.set("-")
        user_id_var.set("-")
        request.state.correlation_id = cid

        started = time.perf_counter()
        response = await call_next(request)
        elapsed = int((time.perf_counter() - started) * 1000)

        response.headers[CORRELATION_HEADER] = cid
        log_event(
            logger,
            logging.INFO,
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=elapsed,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        h["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        h["Cache-Control"] = "no-store"
        if self.settings.is_prod:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        is_upload = request.url.path.endswith("/upload")
        limit = (
            self.settings.max_upload_bytes
            if is_upload
            else self.settings.max_body_bytes
        )
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "payload_too_large",
                    "message": "The request body is too large.",
                    "correlation_id": correlation_id_var.get(),
                },
            )
        return await call_next(request)
