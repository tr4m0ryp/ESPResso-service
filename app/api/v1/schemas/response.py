"""Response schemas for the prediction API."""

from typing import Optional

from pydantic import BaseModel, Field


class CarbonComponents(BaseModel):
    """Breakdown of carbon footprint by component."""

    raw_materials: float = Field(..., description="Raw materials kg CO2e")
    transport: float = Field(..., description="Transport kg CO2e")
    processing: float = Field(..., description="Processing kg CO2e")
    packaging: float = Field(..., description="Packaging kg CO2e")


class PredictionResult(BaseModel):
    """Prediction result for a single product."""

    product_id: str
    carbon_kg_co2e: Optional[float] = Field(
        None, description="Total carbon footprint in kg CO2e (null if prediction failed)"
    )
    components: Optional[CarbonComponents] = Field(
        None, description="Carbon component breakdown (null if prediction failed)"
    )
    confidence: Optional[float] = Field(
        None, description="Overall confidence score (null if prediction failed)"
    )
    model_used: str = Field(..., description="Model that produced this result")
    is_rare: bool = Field(
        False, description="Whether the item was flagged as rare"
    )
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = Field(
        None, description="Error message if prediction failed"
    )


class PredictionSummary(BaseModel):
    """Summary statistics for a batch prediction."""

    total_products: int
    successful: int
    failed: int
    rare_count: int
    avg_confidence: float
    processing_time_seconds: float
    retried: int = Field(
        0, description="Number of products that needed at least one retry"
    )


class BatchPredictResponse(BaseModel):
    """Response for a batch prediction request."""

    predictions: list[PredictionResult]
    summary: PredictionSummary


class FailedProduct(BaseModel):
    """A product that failed prediction."""

    product_id: str
    reason: str


class DbWriteResult(BaseModel):
    """Summary of database write operation."""

    written: int = Field(..., description="Products successfully written to DB")
    skipped: int = Field(..., description="Products skipped (errors)")


class PredictResponse(BaseModel):
    """Response for the unified predict endpoint (fetch + predict + write)."""

    predictions: list[PredictionResult] = Field(
        ..., description="Full prediction results for each product"
    )
    summary: PredictionSummary = Field(
        ..., description="Aggregate statistics"
    )
    failed_products: list[FailedProduct] = Field(
        default_factory=list, description="Which products failed and why"
    )
    db_write: Optional[DbWriteResult] = Field(
        None, description="Database write outcome (null if write failed entirely)"
    )
