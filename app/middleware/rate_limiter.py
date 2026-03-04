"""In-memory sliding window rate limiter middleware."""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding window rate limiter.

    Skips the /health and /api/v1/health endpoints.
    Returns 429 with Retry-After and X-RateLimit-* headers when exceeded.
    """

    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, key: str, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self.window_seconds
        timestamps = self._requests[key]
        # Find the first index within the window
        idx = 0
        while idx < len(timestamps) and timestamps[idx] < cutoff:
            idx += 1
        if idx:
            self._requests[key] = timestamps[idx:]

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        # Skip health endpoints
        path = request.url.path.rstrip("/")
        if path in ("/health", "/api/v1/health"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        self._cleanup(client_ip, now)

        timestamps = self._requests[client_ip]
        remaining = max(0, self.max_requests - len(timestamps))

        if len(timestamps) >= self.max_requests:
            # Oldest request in window determines when the next slot opens
            retry_after = int(self.window_seconds - (now - timestamps[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(timestamps[0] + self.window_seconds)),
                },
            )

        timestamps.append(now)
        response = await call_next(request)

        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining - 1 if remaining > 0 else 0)
        return response
