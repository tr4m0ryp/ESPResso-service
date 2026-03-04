"""Bearer token authentication middleware."""

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer()


async def verify_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """Validate Bearer token against API_KEY from app state.

    Uses constant-time comparison to prevent timing attacks.
    Returns the token on success; raises 401 on failure.
    """
    expected = request.app.state.settings.API_KEY
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return credentials.credentials
