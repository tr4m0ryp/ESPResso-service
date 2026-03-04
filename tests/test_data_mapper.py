"""Tests for the data mapper."""

import pytest

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.pipeline.data_mapper import NormalizedData, map_to_record


class TestMapToRecord:
    def test_basic_mapping(self, sample_product):
        norm = NormalizedData(
            materials=["goat leather", "fibre, polyester"],
            category_name="Clothing",
            subcategory_name="Trench Coats",
            preprocessing_steps=["cutting", "sewing", "dyeing", "finishing"],
        )
        record, warnings = map_to_record(sample_product, norm)

        assert record["category_name"] == "Clothing"
        assert record["subcategory_name"] == "Trench Coats"
        assert record["materials"] == ["goat leather", "fibre, polyester"]
        assert record["total_weight_kg"] == 1.5
        assert len(record["material_weights_kg"]) == 2
        # 80% of 1.5 = 1.2
        assert record["material_weights_kg"][0] == pytest.approx(1.2)
        # 20% of 1.5 = 0.3
        assert record["material_weights_kg"][1] == pytest.approx(0.3)
        assert record["origin_region"] == "Europe"

    def test_missing_fields_generate_warnings(self, sample_product_minimal):
        norm = NormalizedData(
            materials=["fibre, cotton"],
            category_name="Clothing",
            subcategory_name="Clothing",
            preprocessing_steps=[],
        )
        record, warnings = map_to_record(sample_product_minimal, norm)

        # Should have warnings for missing packaging and transport
        warning_texts = " ".join(warnings)
        assert "packaging_categories" in warning_texts
        assert "packaging_masses_kg" in warning_texts
        assert "total_transport_distance_km" in warning_texts

    def test_weight_unit_conversion(self):
        product = ProductInput(
            product_id="test-g",
            name="Light item",
            materials=[MaterialInput(name="cotton", percentage=100.0)],
            total_weight_kg=500.0,
            weight_unit="g",
        )
        norm = NormalizedData(materials=["fibre, cotton"])
        record, _ = map_to_record(product, norm)
        assert record["total_weight_kg"] == pytest.approx(0.5)

    def test_origin_fallback_to_material(self):
        product = ProductInput(
            product_id="test-fb",
            name="Test",
            materials=[
                MaterialInput(
                    name="cotton",
                    percentage=100.0,
                    country_of_origin="India",
                ),
            ],
            total_weight_kg=1.0,
        )
        norm = NormalizedData(materials=["fibre, cotton"])
        record, _ = map_to_record(product, norm)
        assert record["origin_region"] == "India"
