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
