"""FastAPI application factory with lifespan for model loading."""

import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import settings
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
    app = FastAPI(
        title="ESPResso Carbon Footprint Prediction Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    from app.api.router import api_router
    app.include_router(api_router)

    return app


app = create_app()
