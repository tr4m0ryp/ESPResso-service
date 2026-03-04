"""Map ProductInput + normalized data to ESPResso record_dict."""

from dataclasses import dataclass, field
from typing import Any

from app.api.v1.schemas.request import ProductInput

# Weight unit conversion factors to kg
_WEIGHT_CONVERSIONS: dict[str, float] = {
    "kg": 1.0,
    "g": 0.001,
    "lb": 0.453592,
    "lbs": 0.453592,
    "oz": 0.0283495,
}


@dataclass
class NormalizedData:
    """Container for normalized product data."""

    materials: list[str] = field(default_factory=list)
    category_name: str = "Unknown"
    subcategory_name: str = "Unknown"
    preprocessing_steps: list[str] = field(default_factory=list)


def _convert_weight(weight: float | None, unit: str) -> float | None:
    """Convert weight to kg."""
    if weight is None:
        return None
    factor = _WEIGHT_CONVERSIONS.get(unit.lower().strip(), 1.0)
    return weight * factor


def map_to_record(
    product: ProductInput,
    normalized: NormalizedData,
) -> tuple[dict[str, Any], list[str]]:
    """Transform a ProductInput + normalized data into an ESPResso record dict.

    Returns:
        Tuple of (record_dict, warnings). The record_dict matches
        ESPResso's INPUT_FIELDS. Warnings list fields that were absent
        or defaulted.
    """
    warnings: list[str] = []

    # Weight conversion
    total_weight_kg = _convert_weight(
        product.total_weight_kg, product.weight_unit,
    )
    if total_weight_kg is None:
        total_weight_kg = 1.0
        warnings.append("total_weight_kg: not provided, defaulting to 1.0 kg")

    # Material weights: total_weight * percentage / 100
    material_names = normalized.materials
    material_weights_kg: list[float] = []
    for mat_input in product.materials:
        weight = total_weight_kg * mat_input.percentage / 100.0
        material_weights_kg.append(weight)

    if not material_names:
        warnings.append("materials: none provided")

    # Origin region
    origin_region = product.origin_region
    if not origin_region and product.materials:
        # Fall back to first material's country_of_origin
        first_origin = product.materials[0].country_of_origin
        if first_origin:
            origin_region = first_origin

    # Transport distance
    transport_km = product.total_transport_distance_km
    if transport_km is None:
        warnings.append(
            "total_transport_distance_km: not provided "
            "(model will use degraded prediction)"
        )

    # Packaging
    packaging_cats = product.packaging_categories or []
    packaging_masses = product.packaging_masses_kg or []
    if not packaging_cats:
        warnings.append(
            "packaging_categories: not provided "
            "(model will use degraded prediction)"
        )
    if not packaging_masses:
        warnings.append(
            "packaging_masses_kg: not provided "
            "(model will use degraded prediction)"
        )

    record = {
        "category_name": normalized.category_name,
        "subcategory_name": normalized.subcategory_name,
        "materials": material_names,
        "material_weights_kg": material_weights_kg,
        "total_weight_kg": total_weight_kg,
        "preprocessing_steps": normalized.preprocessing_steps,
        "total_transport_distance_km": transport_km,
        "origin_region": origin_region,
        "packaging_categories": packaging_cats if packaging_cats else None,
        "packaging_masses_kg": packaging_masses if packaging_masses else None,
    }

    return record, warnings
