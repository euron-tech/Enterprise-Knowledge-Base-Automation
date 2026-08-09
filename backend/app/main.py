"""App factory. Middleware order matters — see docs/ARCHITECTURE.md §3.2."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.agent.tools import register_all
from app.api.middleware import (
    BodySizeLimitMiddleware,
    CorrelationMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.routes import router
from app.clients.euri import EuriClient
from app.clients.vectorstore import VectorStore
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import (
    configure_logging,
    correlation_id_var,
    get_logger,
    log_event,
)
from app.core.ratelimit import RateLimiter
from app.db.session import init_db
from app.ingestion.pipeline import IngestionPipeline
from app.rag.cache import AnswerCache
from app.rag.service import RagContext, RagService

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    register_all()  # idempotent; a real registration violation still raises

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db()
        await app.state.vectors.ensure_collection()
        log_event(logger, logging.INFO, "app.started", environment=settings.environment)
        yield
        await app.state.euri.aclose()

    app = FastAPI(
        title="EKBA API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )

    # Wiring — one instance each, created at startup, shared.
    euri = EuriClient(settings)
    vectors = VectorStore(settings)
    cache = AnswerCache(settings)
    app.state.settings = settings
    app.state.euri = euri
    app.state.vectors = vectors
    app.state.cache = cache
    app.state.rate_limiter = RateLimiter()
    app.state.rag = RagService(settings, euri, vectors, cache)
    app.state.ingestion = IngestionPipeline(settings, euri, vectors)
    app.state.rag_context_factory = lambda: RagContext(settings, euri, vectors)

    # Outermost first.
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,  # explicit allow-list; never "*" with credentials
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        cid = correlation_id_var.get()
        log_event(
            logger,
            logging.WARNING if exc.http_status < 500 else logging.ERROR,
            "app.error",
            code=exc.code,
            status=exc.http_status,
            detail=exc.internal_detail,  # detail stays in logs, never in the response
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": exc.code,
                "message": exc.public_message,
                "correlation_id": cid,
            },
            headers={"X-Correlation-ID": cid},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        cid = correlation_id_var.get()
        log_event(logger, logging.INFO, "app.validation_error", path=request.url.path)
        content: dict = {
            "error": "invalid_request",
            "message": "The request was invalid.",
            "correlation_id": cid,
        }
        if not settings.is_prod:
            content["detail"] = exc.errors()[:5]
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        cid = correlation_id_var.get()
        logger.exception(
            "unhandled error", extra={"extra_fields": {"path": request.url.path}}
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred.",
                "correlation_id": cid,
            },
        )

    app.include_router(router)
    return app


app = create_app()
