"""GET /api/v1/health endpoint."""

import time

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Return service health status."""
    model_loader = request.app.state.model_loader
    cache = request.app.state.cache
    start_time = getattr(request.app.state, "start_time", 0.0)

    uptime = time.time() - start_time if start_time else 0.0

    nim_reachable = False
    try:
        nim_client = request.app.state.nim_client
        nim_reachable = await nim_client.health_check()
    except Exception:
        pass

    return {
        "status": "healthy" if model_loader.is_loaded else "degraded",
        "models": model_loader.status(),
        "nim_reachable": nim_reachable,
        "cache": cache.stats(),
        "uptime_seconds": round(uptime, 1),
    }
