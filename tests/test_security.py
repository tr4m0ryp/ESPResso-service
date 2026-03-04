"""Integration tests for security middleware (brand auth, health tiers, rate limiting)."""

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.config import Settings
from app.main import create_app
from app.models.loader import ModelLoader
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient

HMAC_SECRET = "test-hmac-secret"


def _sign_brand(brand_id: str, secret: str = HMAC_SECRET) -> dict:
    """Generate X-Brand-Id and X-Brand-Signature headers for testing."""
    ts = str(int(time.time()))
    message = f"{brand_id}:{ts}"
    digest = hmac.new(
        secret.encode(), message.encode(), hashlib.sha256,
    ).hexdigest()
    return {
        "X-Brand-Id": brand_id,
        "X-Brand-Signature": f"{ts}:{digest}",
    }


def _mock_products():
    return [
        ProductInput(
            product_id="p1",
            name="Test Shirt",
            category_path=["Clothing", "Shirts"],
            materials=[MaterialInput(name="cotton", percentage=100.0)],
            total_weight_kg=0.3,
        ),
    ]


def _make_app(mock_model_result, hmac_secret=HMAC_SECRET):
    """Create a test app with mocked dependencies."""
    app = create_app()

    loader = MagicMock(spec=ModelLoader)
    loader.is_loaded = True
    loader.status.return_value = {"A": True, "B": True, "C": True}
    mock_model = MagicMock()
    mock_model.predict.return_value = mock_model_result
    loader.get.return_value = mock_model

    nim = AsyncMock(spec=NIMClient)
    nim.complete.return_value = "fibre, cotton"
    nim.health_check.return_value = True

    app.state.model_loader = loader
    app.state.nim_client = nim
    app.state.cache = NormalizationCache(max_size=100, ttl=3600)
    app.state.settings = Settings(
        API_KEY="test-key",
        HMAC_SECRET=hmac_secret,
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_SERVICE_KEY="test-service-key",
    )
    app.state.start_time = 0.0
    return app


@pytest.fixture
def mock_app(mock_model_result):
    return _make_app(mock_model_result)


@pytest.fixture
def mock_app_no_hmac(mock_model_result):
    return _make_app(mock_model_result, hmac_secret="")


@pytest.fixture
def client(mock_app):
    return TestClient(mock_app)


class TestBrandAuthorization:
    """Tests for HMAC brand authorization."""

    @patch("app.supabase.client.SupabaseClient")
    @patch("app.supabase.result_writer.write_predictions", new_callable=AsyncMock)
    @patch("app.supabase.product_fetcher.fetch_products_for_prediction", new_callable=AsyncMock)
    def test_valid_signature(self, mock_fetch, mock_write, mock_sb_cls, client):
        mock_fetch.return_value = _mock_products()
        mock_write.return_value = {"written": 1, "skipped": 0}
        mock_sb_cls.return_value = AsyncMock()

        headers = {"Authorization": "Bearer test-key", **_sign_brand("brand-1")}
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers=headers,
        )
        assert response.status_code == 200

    def test_missing_signature(self, client):
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={
                "Authorization": "Bearer test-key",
                "X-Brand-Id": "brand-1",
            },
        )
        assert response.status_code == 401

    def test_expired_signature(self, client):
        ts = str(int(time.time()) - 600)
        message = f"brand-1:{ts}"
        digest = hmac.new(
            HMAC_SECRET.encode(), message.encode(), hashlib.sha256,
        ).hexdigest()
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={
                "Authorization": "Bearer test-key",
                "X-Brand-Id": "brand-1",
                "X-Brand-Signature": f"{ts}:{digest}",
            },
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"]

    def test_brand_id_mismatch(self, client):
        headers = {
            "Authorization": "Bearer test-key",
            **_sign_brand("brand-1"),
        }
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-OTHER", "product_ids": ["p1"]},
            headers=headers,
        )
        assert response.status_code == 403
        assert "does not match" in response.json()["detail"]

    @patch("app.supabase.client.SupabaseClient")
    @patch("app.supabase.result_writer.write_predictions", new_callable=AsyncMock)
    @patch("app.supabase.product_fetcher.fetch_products_for_prediction", new_callable=AsyncMock)
    def test_dev_mode_skips_hmac(
        self, mock_fetch, mock_write, mock_sb_cls, mock_app_no_hmac,
    ):
        """With no HMAC_SECRET set, HMAC verification is skipped."""
        mock_fetch.return_value = _mock_products()
        mock_write.return_value = {"written": 1, "skipped": 0}
        mock_sb_cls.return_value = AsyncMock()

        dev_client = TestClient(mock_app_no_hmac)
        response = dev_client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={
                "Authorization": "Bearer test-key",
                "X-Brand-Id": "brand-1",
            },
        )
        assert response.status_code == 200


class TestHealthTiered:
    """Tests for tiered health endpoint responses."""

    def test_health_full_details_default(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert "nim_reachable" in data
        assert "cache" in data

    def test_health_minimal_when_auth_required(self, mock_app):
        mock_app.state.settings.HEALTH_REQUIRE_AUTH = True
        auth_client = TestClient(mock_app)
        response = auth_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert list(data.keys()) == ["status"]

    def test_health_full_when_authenticated(self, mock_app):
        mock_app.state.settings.HEALTH_REQUIRE_AUTH = True
        auth_client = TestClient(mock_app)
        response = auth_client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert "uptime_seconds" in data


class TestRateLimiting:
    """Tests for rate limiting middleware."""

    def test_rate_limit_headers_present(self, client):
        headers = {"Authorization": "Bearer test-key", **_sign_brand("brand-1")}
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers=headers,
        )
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers

    def test_rate_limit_exceeded(self):
        """Exceeding rate limit returns 429 (tested directly on middleware)."""
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from app.middleware.rate_limiter import RateLimitMiddleware

        async def echo(request):
            return JSONResponse({"ok": True})

        inner = Starlette(routes=[Route("/test", echo)])
        app = RateLimitMiddleware(inner, max_requests=2, window_seconds=60)
        limited_client = TestClient(app)

        assert limited_client.get("/test").status_code == 200
        assert limited_client.get("/test").status_code == 200
        resp = limited_client.get("/test")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert resp.headers["X-RateLimit-Remaining"] == "0"
