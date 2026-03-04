"""Shared test fixtures."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.config import Settings
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient


@pytest.fixture
def settings():
    return Settings(
        API_KEY="test-key",
        NIM_API_KEY="test-nim-key",
        RARITY_CONFIDENCE_THRESHOLD=0.6,
        HMAC_SECRET="test-hmac-secret",
    )


@pytest.fixture
def mock_nim_client():
    client = AsyncMock(spec=NIMClient)
    client.complete.return_value = "fibre, cotton"
    client.complete_with_choices.return_value = "fibre, cotton"
    client.complete_bulk.return_value = ["fibre, cotton"]
    client.health_check.return_value = True
    return client


@pytest.fixture
def cache():
    return NormalizationCache(max_size=100, ttl=3600)


@pytest.fixture
def sample_product():
    return ProductInput(
        product_id="test-001",
        name="Classic Trench Coat",
        category_path=["Clothing", "Coats", "Trench Coats"],
        materials=[
            MaterialInput(
                name="goat leather",
                percentage=80.0,
                country_of_origin="Italy",
            ),
            MaterialInput(
                name="polyester lining",
                percentage=20.0,
                country_of_origin="China",
            ),
        ],
        total_weight_kg=1.5,
        weight_unit="kg",
        preprocessing_steps=["cutting", "sewing", "dyeing", "finishing"],
        origin_region="Europe",
    )


@pytest.fixture
def sample_product_minimal():
    return ProductInput(
        product_id="test-002",
        name="Simple T-Shirt",
        category_path=["Clothing"],
        materials=[
            MaterialInput(name="cotton", percentage=100.0),
        ],
        total_weight_kg=0.2,
    )


@pytest.fixture
def mock_model_result():
    return {
        "predictions": {
            "cf_raw_materials_kg_co2e": 15.2,
            "cf_transport_kg_co2e": 4.0,
            "cf_processing_kg_co2e": 3.1,
            "cf_packaging_kg_co2e": 1.15,
            "cf_modelled_kg_co2e": 23.45,
            "cf_total_kg_co2e": 23.92,
        },
        "predicted_ape": {
            "cf_raw_materials": 0.12,
            "cf_transport": 0.15,
            "cf_processing": 0.10,
            "cf_packaging": 0.20,
        },
        "confidence": {
            "cf_raw_materials": 0.88,
            "cf_transport": 0.85,
            "cf_processing": 0.90,
            "cf_packaging": 0.80,
            "overall": 0.86,
        },
        "frequencies": {
            "material_frequencies": {"cotton": 0.35},
            "step_frequencies": {"sewing": 0.5},
            "category_origin_count": 100,
            "origin_region_frequency": 0.25,
        },
    }
