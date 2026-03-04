"""Request schemas for the prediction API."""

from typing import Optional

from pydantic import BaseModel, Field


class MaterialInput(BaseModel):
    """Single material in a product."""

    name: str = Field(..., description="Material name (e.g., 'goat leather')")
    percentage: float = Field(
        ..., ge=0, le=100, description="Percentage of total weight"
    )
    country_of_origin: Optional[str] = Field(
        None, description="Country where material originates"
    )


class ProductInput(BaseModel):
    """Single product to predict carbon footprint for."""

    product_id: str = Field(..., description="Unique product identifier")
    name: str = Field("", description="Product name")
    category_path: list[str] = Field(
        default_factory=list,
        description="Category hierarchy from root to leaf",
    )
    materials: list[MaterialInput] = Field(
        default_factory=list, description="Materials in the product"
    )
    total_weight_kg: Optional[float] = Field(
        None, ge=0, description="Total product weight in kg"
    )
    weight_unit: str = Field("kg", description="Weight unit (kg, g, lb, oz)")
    preprocessing_steps: list[str] = Field(
        default_factory=list, description="Manufacturing steps"
    )
    origin_region: Optional[str] = Field(
        None, description="Geographic origin region"
    )
    packaging_categories: list[str] = Field(
        default_factory=list, description="Packaging types"
    )
    packaging_masses_kg: list[float] = Field(
        default_factory=list, description="Packaging weights in kg"
    )
    total_transport_distance_km: Optional[float] = Field(
        None, ge=0, description="Total transport distance in km"
    )


class PredictRequest(BaseModel):
    """Predict request -- sends product IDs, service fetches data from Supabase."""

    brand_id: str = Field(
        ..., description="UUID of the brand owning the products"
    )
    product_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Product UUIDs to predict (1-500)",
    )
    confidence_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Override default rarity confidence threshold",
    )
