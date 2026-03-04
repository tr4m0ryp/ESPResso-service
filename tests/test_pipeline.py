"""Tests for the pipeline orchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.api.v1.schemas.response import BatchPredictResponse
from app.config import Settings
from app.models.predictor import batch_predict
from app.normalization.cache import NormalizationCache


class TestBatchPredict:
    def test_successful_predictions(self, mock_model_result):
        mock_model = MagicMock()
        mock_model.predict.return_value = mock_model_result

        records = [{"category_name": "Clothing"}] * 3
        results = batch_predict(mock_model, records)

        assert len(results) == 3
        assert all(r is not None for r in results)

    def test_error_isolation(self, mock_model_result):
        mock_model = MagicMock()
        mock_model.predict.side_effect = [
            mock_model_result,
            RuntimeError("boom"),
            mock_model_result,
        ]

        records = [{"a": 1}, {"a": 2}, {"a": 3}]
        results = batch_predict(mock_model, records)

        assert len(results) == 3
        assert results[0] is not None
        assert results[1] is None  # failed
        assert results[2] is not None

    def test_empty_batch(self):
        mock_model = MagicMock()
        results = batch_predict(mock_model, [])
        assert results == []


def _make_products(n: int) -> list[ProductInput]:
    """Create n minimal ProductInput objects for testing."""
    return [
        ProductInput(
            product_id=f"p{i}",
            name=f"Product {i}",
            category_path=["Clothing"],
            materials=[MaterialInput(name="cotton", percentage=100.0)],
            total_weight_kg=0.3,
        )
        for i in range(n)
    ]


def _make_pipeline_response(products, mock_result, fail_indices=None):
    """Build a BatchPredictResponse simulating _run_pipeline output.

    Args:
        products: list of ProductInput objects fed to this pipeline call.
        mock_result: the mock model result dict for successful predictions.
        fail_indices: set of indices (within this batch) that should fail.
    """
    from app.api.v1.schemas.response import (
        CarbonComponents,
        PredictionResult,
        PredictionSummary,
    )

    fail_indices = fail_indices or set()
    predictions = []
    successful = 0
    failed = 0

    for i, p in enumerate(products):
        if i in fail_indices:
            predictions.append(PredictionResult(
                product_id=p.product_id,
                carbon_kg_co2e=0.0,
                components=CarbonComponents(
                    raw_materials=0, transport=0, processing=0, packaging=0,
                ),
                confidence=0.0,
                model_used="none",
                is_rare=False,
                error="Model A prediction failed",
            ))
            failed += 1
        else:
            preds = mock_result["predictions"]
            conf = mock_result["confidence"]
            predictions.append(PredictionResult(
                product_id=p.product_id,
                carbon_kg_co2e=preds["cf_total_kg_co2e"],
                components=CarbonComponents(
                    raw_materials=preds["cf_raw_materials_kg_co2e"],
                    transport=preds["cf_transport_kg_co2e"],
                    processing=preds["cf_processing_kg_co2e"],
                    packaging=preds["cf_packaging_kg_co2e"],
                ),
                confidence=conf["overall"],
                model_used="A",
                is_rare=False,
            ))
            successful += 1

    return BatchPredictResponse(
        predictions=predictions,
        summary=PredictionSummary(
            total_products=len(products),
            successful=successful,
            failed=failed,
            rare_count=0,
            avg_confidence=0.86 if successful else 0.0,
            processing_time_seconds=0.1,
        ),
    )


class TestRetryMechanism:
    """Tests for the run_batch_prediction retry wrapper."""

    @pytest.mark.asyncio
    @patch("app.pipeline.orchestrator._run_pipeline")
    async def test_all_succeed_no_retry(self, mock_pipeline, mock_model_result):
        """When all products succeed on first attempt, no retries happen."""
        from app.pipeline.orchestrator import run_batch_prediction

        products = _make_products(3)
        mock_pipeline.return_value = _make_pipeline_response(
            products, mock_model_result,
        )

        settings = Settings(API_KEY="k", RARITY_CONFIDENCE_THRESHOLD=0.6)
        response = await run_batch_prediction(
            products, settings, MagicMock(), AsyncMock(), NormalizationCache(100, 3600),
        )

        assert mock_pipeline.call_count == 1
        assert response.summary.successful == 3
        assert response.summary.failed == 0
        assert response.summary.retried == 0
        assert all(r.carbon_kg_co2e is not None for r in response.predictions)

    @pytest.mark.asyncio
    @patch("app.pipeline.orchestrator._run_pipeline")
    async def test_all_fail_permanently(self, mock_pipeline, mock_model_result):
        """Products that fail all 3 attempts get null carbon values."""
        from app.pipeline.orchestrator import run_batch_prediction

        products = _make_products(2)

        # All products fail on every attempt
        mock_pipeline.return_value = _make_pipeline_response(
            products, mock_model_result, fail_indices={0, 1},
        )

        def side_effect(batch, *args, **kwargs):
            return _make_pipeline_response(
                batch, mock_model_result,
                fail_indices=set(range(len(batch))),
            )

        mock_pipeline.side_effect = side_effect

        settings = Settings(API_KEY="k", RARITY_CONFIDENCE_THRESHOLD=0.6)
        response = await run_batch_prediction(
            products, settings, MagicMock(), AsyncMock(), NormalizationCache(100, 3600),
        )

        assert mock_pipeline.call_count == 3
        assert response.summary.successful == 0
        assert response.summary.failed == 2
        assert response.summary.retried == 4  # 2 products retried twice
        for r in response.predictions:
            assert r.carbon_kg_co2e is None
            assert r.components is None
            assert r.confidence is None
            assert r.model_used == "none"
            assert "failed after 3 attempts" in r.error

    @pytest.mark.asyncio
    @patch("app.pipeline.orchestrator._run_pipeline")
    async def test_partial_fail_then_succeed(self, mock_pipeline, mock_model_result):
        """Product fails on attempt 1, succeeds on attempt 2."""
        from app.pipeline.orchestrator import run_batch_prediction

        products = _make_products(3)
        call_count = 0

        async def side_effect(batch, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: p1 fails, p0 and p2 succeed
                return _make_pipeline_response(
                    batch, mock_model_result, fail_indices={1},
                )
            else:
                # Second call: only p1 in batch, now succeeds
                return _make_pipeline_response(
                    batch, mock_model_result,
                )

        mock_pipeline.side_effect = side_effect

        settings = Settings(API_KEY="k", RARITY_CONFIDENCE_THRESHOLD=0.6)
        response = await run_batch_prediction(
            products, settings, MagicMock(), AsyncMock(), NormalizationCache(100, 3600),
        )

        assert mock_pipeline.call_count == 2
        assert response.summary.successful == 3
        assert response.summary.failed == 0
        assert response.summary.retried == 1
        # All predictions should have real values
        for r in response.predictions:
            assert r.carbon_kg_co2e is not None
            assert r.error is None

    @pytest.mark.asyncio
    @patch("app.pipeline.orchestrator._run_pipeline")
    async def test_mixed_success_and_permanent_failure(
        self, mock_pipeline, mock_model_result,
    ):
        """Mix: some succeed immediately, one fails permanently."""
        from app.pipeline.orchestrator import run_batch_prediction

        products = _make_products(3)
        call_count = 0

        async def side_effect(batch, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # p2 fails on first attempt
                return _make_pipeline_response(
                    batch, mock_model_result, fail_indices={2},
                )
            else:
                # p2 keeps failing in retries
                return _make_pipeline_response(
                    batch, mock_model_result,
                    fail_indices=set(range(len(batch))),
                )

        mock_pipeline.side_effect = side_effect

        settings = Settings(API_KEY="k", RARITY_CONFIDENCE_THRESHOLD=0.6)
        response = await run_batch_prediction(
            products, settings, MagicMock(), AsyncMock(), NormalizationCache(100, 3600),
        )

        assert mock_pipeline.call_count == 3
        assert response.summary.successful == 2
        assert response.summary.failed == 1
        # p0 and p1 succeeded
        assert response.predictions[0].carbon_kg_co2e is not None
        assert response.predictions[1].carbon_kg_co2e is not None
        # p2 permanently failed with null values
        assert response.predictions[2].carbon_kg_co2e is None
        assert response.predictions[2].components is None
        assert response.predictions[2].confidence is None

    @pytest.mark.asyncio
    @patch("app.pipeline.orchestrator._run_pipeline")
    async def test_original_order_preserved(self, mock_pipeline, mock_model_result):
        """Results are returned in the original product order."""
        from app.pipeline.orchestrator import run_batch_prediction

        products = _make_products(4)
        call_count = 0

        async def side_effect(batch, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # p1 and p3 fail
                return _make_pipeline_response(
                    batch, mock_model_result, fail_indices={1, 3},
                )
            else:
                # All succeed on retry
                return _make_pipeline_response(
                    batch, mock_model_result,
                )

        mock_pipeline.side_effect = side_effect

        settings = Settings(API_KEY="k", RARITY_CONFIDENCE_THRESHOLD=0.6)
        response = await run_batch_prediction(
            products, settings, MagicMock(), AsyncMock(), NormalizationCache(100, 3600),
        )

        assert len(response.predictions) == 4
        for i, r in enumerate(response.predictions):
            assert r.product_id == f"p{i}"
