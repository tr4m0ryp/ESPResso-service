"""Integration tests for the unified /predict endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.config import Settings
from app.main import create_app
from app.models.loader import ModelLoader
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient


@pytest.fixture
def mock_app(mock_model_result):
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
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_SERVICE_KEY="test-service-key",
    )
    app.state.start_time = 0.0

    return app


@pytest.fixture
def client(mock_app):
    return TestClient(mock_app)


def _mock_products():
    """Return a list of ProductInput objects that fetch_products would return."""
    return [
        ProductInput(
            product_id="p1",
            name="Test Shirt",
            category_path=["Clothing", "Shirts"],
            materials=[MaterialInput(name="cotton", percentage=100.0)],
            total_weight_kg=0.3,
        ),
    ]


class TestPredictEndpoint:
    @patch("app.supabase.client.SupabaseClient")
    @patch("app.supabase.result_writer.write_predictions", new_callable=AsyncMock)
    @patch("app.supabase.product_fetcher.fetch_products_for_prediction", new_callable=AsyncMock)
    def test_predict_success(
        self, mock_fetch, mock_write, mock_sb_cls, client,
    ):
        mock_fetch.return_value = _mock_products()
        mock_write.return_value = {"written": 1, "skipped": 0}
        mock_sb_instance = AsyncMock()
        mock_sb_cls.return_value = mock_sb_instance

        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data
        assert "summary" in data
        assert "db_write" in data
        assert data["summary"]["total_products"] == 1
        assert data["summary"]["successful"] == 1
        assert data["db_write"]["written"] == 1

    def test_predict_unauthorized(self, client):
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_predict_no_auth(self, client):
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
        )
        assert response.status_code in (401, 403)

    def test_predict_empty_product_ids(self, client):
        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": []},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 422

    @patch("app.supabase.client.SupabaseClient")
    @patch("app.supabase.product_fetcher.fetch_products_for_prediction", new_callable=AsyncMock)
    def test_predict_no_products_found(
        self, mock_fetch, mock_sb_cls, client,
    ):
        mock_fetch.return_value = []
        mock_sb_cls.return_value = AsyncMock()

        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["nonexistent"]},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 400
        assert "No valid products" in response.json()["detail"]

    def test_predict_supabase_not_configured(self, mock_app):
        mock_app.state.settings = Settings(API_KEY="test-key")
        no_sb_client = TestClient(mock_app)

        response = no_sb_client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 500
        assert "Supabase not configured" in response.json()["detail"]

    def test_batch_endpoint_removed(self, client):
        response = client.post(
            "/api/v1/predict/batch",
            json={"products": [{"product_id": "p1"}]},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code in (404, 405)

    @patch("app.supabase.client.SupabaseClient")
    @patch("app.supabase.result_writer.write_predictions", new_callable=AsyncMock)
    @patch("app.supabase.product_fetcher.fetch_products_for_prediction", new_callable=AsyncMock)
    @patch("app.pipeline.orchestrator._run_pipeline")
    def test_predict_with_null_failed_products(
        self, mock_pipeline, mock_fetch, mock_write, mock_sb_cls,
        client, mock_model_result,
    ):
        """Permanently failed products have null carbon values in response."""
        from app.api.v1.schemas.response import (
            BatchPredictResponse,
            PredictionResult,
            PredictionSummary,
        )

        products = _mock_products()
        mock_fetch.return_value = products
        mock_write.return_value = {"written": 1, "skipped": 0}
        mock_sb_cls.return_value = AsyncMock()

        # Simulate pipeline always failing for this product
        async def always_fail(batch, *args, **kwargs):
            return BatchPredictResponse(
                predictions=[
                    PredictionResult(
                        product_id=p.product_id,
                        carbon_kg_co2e=0.0,
                        components=None,
                        confidence=0.0,
                        model_used="none",
                        error="Model A prediction failed",
                    )
                    for p in batch
                ],
                summary=PredictionSummary(
                    total_products=len(batch),
                    successful=0,
                    failed=len(batch),
                    rare_count=0,
                    avg_confidence=0.0,
                    processing_time_seconds=0.01,
                ),
            )

        mock_pipeline.side_effect = always_fail

        response = client.post(
            "/api/v1/predict",
            json={"brand_id": "brand-1", "product_ids": ["p1"]},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 200
        data = response.json()
        pred = data["predictions"][0]
        assert pred["carbon_kg_co2e"] is None
        assert pred["components"] is None
        assert pred["confidence"] is None
        assert pred["model_used"] == "none"
        assert "failed after 3 attempts" in pred["error"]
        assert data["summary"]["failed"] == 1
        assert len(data["failed_products"]) == 1


class TestHealthEndpoint:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "models" in data
        assert data["status"] == "healthy"
