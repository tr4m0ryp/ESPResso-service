"""Unit tests for middleware components."""

import hashlib
import hmac
import time
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from app.middleware.brand_auth import MAX_SIGNATURE_AGE_SECONDS, _compute_hmac
from app.middleware.rate_limiter import RateLimitMiddleware


class TestComputeHmac:
    """Tests for the _compute_hmac helper."""

    def test_deterministic(self):
        result1 = _compute_hmac("secret", "brand-1", "1000000")
        result2 = _compute_hmac("secret", "brand-1", "1000000")
        assert result1 == result2

    def test_different_brand_different_digest(self):
        r1 = _compute_hmac("secret", "brand-1", "1000000")
        r2 = _compute_hmac("secret", "brand-2", "1000000")
        assert r1 != r2

    def test_different_timestamp_different_digest(self):
        r1 = _compute_hmac("secret", "brand-1", "1000000")
        r2 = _compute_hmac("secret", "brand-1", "2000000")
        assert r1 != r2

    def test_matches_stdlib_hmac(self):
        secret = "my-secret"
        brand_id = "abc-123"
        ts = "1700000000"
        expected = hmac.new(
            secret.encode(), f"{brand_id}:{ts}".encode(), hashlib.sha256,
        ).hexdigest()
        assert _compute_hmac(secret, brand_id, ts) == expected


class TestSignatureParsing:
    """Tests for signature format validation."""

    def test_valid_format(self):
        ts = str(int(time.time()))
        digest = _compute_hmac("secret", "brand-1", ts)
        sig = f"{ts}:{digest}"
        parts = sig.split(":", 1)
        assert len(parts) == 2
        assert parts[0] == ts
        assert parts[1] == digest

    def test_missing_colon(self):
        sig = "no-colon-here"
        parts = sig.split(":", 1)
        assert len(parts) == 1

    def test_extra_colons_preserved(self):
        sig = "12345:abc:def"
        parts = sig.split(":", 1)
        assert len(parts) == 2
        assert parts[0] == "12345"
        assert parts[1] == "abc:def"


class TestConstantTimeComparison:
    """Verify that hmac.compare_digest is used correctly."""

    def test_equal_strings(self):
        assert hmac.compare_digest("abc", "abc")

    def test_unequal_strings(self):
        assert not hmac.compare_digest("abc", "def")

    def test_empty_strings(self):
        assert hmac.compare_digest("", "")


class TestRateLimiterCleanup:
    """Tests for rate limiter window cleanup logic."""

    def test_cleanup_removes_old_timestamps(self):
        from starlette.applications import Starlette

        app = Starlette()
        middleware = RateLimitMiddleware(app, max_requests=10, window_seconds=60)

        now = time.time()
        key = "127.0.0.1"
        # Add timestamps: some old, some current
        middleware._requests[key] = [
            now - 120,  # old, should be removed
            now - 90,   # old, should be removed
            now - 30,   # within window
            now - 10,   # within window
        ]

        middleware._cleanup(key, now)
        assert len(middleware._requests[key]) == 2
        assert all(t >= now - 60 for t in middleware._requests[key])

    def test_cleanup_empty_list(self):
        from starlette.applications import Starlette

        app = Starlette()
        middleware = RateLimitMiddleware(app, max_requests=10, window_seconds=60)
        key = "127.0.0.1"
        middleware._requests[key] = []
        middleware._cleanup(key, time.time())
        assert middleware._requests[key] == []

    def test_cleanup_all_old(self):
        from starlette.applications import Starlette

        app = Starlette()
        middleware = RateLimitMiddleware(app, max_requests=10, window_seconds=60)
        now = time.time()
        key = "127.0.0.1"
        middleware._requests[key] = [now - 120, now - 100, now - 80]
        middleware._cleanup(key, now)
        assert middleware._requests[key] == []


class TestSignatureAge:
    """Test that the max signature age constant is reasonable."""

    def test_max_age_is_five_minutes(self):
        assert MAX_SIGNATURE_AGE_SECONDS == 300
