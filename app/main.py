"""FastAPI application factory with lifespan for model loading."""

import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.rate_limiter import RateLimitMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.models.loader import ModelLoader
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient

logger = structlog.get_logger()

_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models and initialize services on startup."""
    global _start_time
    _start_time = time.time()

    logger.info("Loading ESPResso models...")
    loader = ModelLoader(settings)
    loader.load_all()
    app.state.model_loader = loader
    logger.info("All models loaded successfully")

    app.state.nim_client = NIMClient(settings)
    app.state.cache = NormalizationCache(
        max_size=settings.CACHE_MAX_SIZE,
        ttl=settings.CACHE_TTL_SECONDS,
    )
    app.state.settings = settings
    app.state.start_time = _start_time

    yield

    logger.info("Shutting down ESPResso service")
    await app.state.nim_client.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Hide docs in production
    docs_kwargs = {}
    if settings.is_production:
        docs_kwargs = {
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }

    app = FastAPI(
        title="ESPResso Carbon Footprint Prediction Service",
        version="0.1.0",
        lifespan=lifespan,
        **docs_kwargs,
    )

    # CORS -- restrictive defaults, configurable via ALLOWED_ORIGINS
    origins = settings.allowed_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else [],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "X-Brand-Id", "X-Brand-Signature"],
    )

    # Rate limiting
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.RATE_LIMIT_REQUESTS,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    )

    # Request logging (outermost -- wraps everything)
    app.add_middleware(RequestLoggingMiddleware)

    from app.api.router import api_router
    app.include_router(api_router)

    return app


app = create_app()
