"""HMAC-based brand authorization middleware.

Verifies that the avelero backend explicitly authorized the brand_id
in each request via a signed header pair:
  X-Brand-Id: <uuid>
  X-Brand-Signature: <unix_timestamp>:<hmac_sha256_hex>
"""

import hashlib
import hmac
import time

import structlog
from fastapi import Depends, HTTPException, Request, status

from app.middleware.api_key_auth import verify_api_key

logger = structlog.get_logger()

MAX_SIGNATURE_AGE_SECONDS = 300  # 5 minutes


def _compute_hmac(secret: str, brand_id: str, timestamp: str) -> str:
    """Compute HMAC-SHA256 hex digest for brand authorization."""
    message = f"{brand_id}:{timestamp}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def verify_brand_authorization(
    request: Request,
    _token: str = Depends(verify_api_key),
) -> str:
    """Verify HMAC brand signature from request headers.

    Chains through verify_api_key so the Bearer token is checked first.
    In development (no HMAC_SECRET configured), skips verification with a
    warning and returns the X-Brand-Id header value directly.

    Returns the verified brand_id string.
    """
    hmac_secret = request.app.state.settings.HMAC_SECRET

    brand_id = request.headers.get("X-Brand-Id", "")
    if not brand_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Brand-Id header",
        )

    # Development mode: skip HMAC verification when no secret is set
    if not hmac_secret:
        logger.warning(
            "HMAC verification skipped -- no HMAC_SECRET configured",
            brand_id=brand_id,
        )
        return brand_id

    signature_header = request.headers.get("X-Brand-Signature", "")
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Brand-Signature header",
        )

    # Parse "timestamp:hex_digest"
    parts = signature_header.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed X-Brand-Signature (expected timestamp:hmac)",
        )

    sig_timestamp, sig_digest = parts

    # Validate timestamp is within allowed window (replay protection)
    try:
        ts = int(sig_timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid timestamp in X-Brand-Signature",
        )

    age = abs(time.time() - ts)
    if age > MAX_SIGNATURE_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Brand-Signature has expired",
        )

    # Verify HMAC digest (constant-time comparison)
    expected = _compute_hmac(hmac_secret, brand_id, sig_timestamp)
    if not hmac.compare_digest(sig_digest, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid brand signature",
        )

    return brand_id
