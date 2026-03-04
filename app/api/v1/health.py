"""GET /api/v1/health endpoint with tiered responses."""

import hmac
import time

from fastapi import APIRouter, Request

router = APIRouter()


def _is_authenticated(request: Request) -> bool:
    """Check if the request carries a valid Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[len("Bearer "):]
    expected = request.app.state.settings.API_KEY
    return hmac.compare_digest(token, expected)


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Return service health status.

    When HEALTH_REQUIRE_AUTH is true and the caller is not authenticated,
    returns only a minimal {"status": "..."} response. Otherwise returns
    full operational details including model status, NIM, cache, and uptime.
    """
    model_loader = request.app.state.model_loader
    settings = request.app.state.settings
    status_str = "healthy" if model_loader.is_loaded else "degraded"

    # Minimal response for unauthenticated callers when auth is required
    if settings.HEALTH_REQUIRE_AUTH and not _is_authenticated(request):
        return {"status": status_str}

    # Full response
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
        "status": status_str,
        "models": model_loader.status(),
        "nim_reachable": nim_reachable,
        "cache": cache.stats(),
        "uptime_seconds": round(uptime, 1),
    }
