import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import get_settings
from app.db import engine
from app.errors import install_error_handlers
from app.middleware import QueryCountMiddleware, SecurityHeadersMiddleware
from app.rate_limit import GlobalRateLimitMiddleware
from app.routers import (
    coach,
    command,
    dashboard,
    flashcards,
    health,
    internal,
    me,
    notes,
    quizzes,
    sessions,
    snap,
    topics,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    prod = settings.is_production
    app = FastAPI(
        title="Study Hub API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        redoc_url=None,
        openapi_url=None if prod else "/openapi.json",
    )

    install_error_handlers(app)

    for r in (
        health,
        me,
        topics,
        sessions,
        dashboard,
        notes,
        flashcards,
        quizzes,
        snap,
        command,
        coach,
        internal,
    ):
        app.include_router(r.router)

    # Middleware: the last one added is the outermost. CORS is outermost so even
    # 429s and errors carry CORS headers and the browser can read them.
    if not prod:
        app.add_middleware(QueryCountMiddleware, threshold=settings.query_warn_threshold)
    app.add_middleware(GlobalRateLimitMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityHeadersMiddleware, hsts=prod)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Timezone"],
        expose_headers=["Retry-After"],
        max_age=600,
    )
    return app


app = create_app()
