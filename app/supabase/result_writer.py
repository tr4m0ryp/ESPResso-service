"""Write prediction results back to Supabase.

Ported from scripts/batch_predict.py write_results(), adapted to use
async PostgREST upserts instead of raw psycopg2 SQL.

Dual-write to:
1. product_environment -- total carbon_kg_co2e (insert-only, never overwrite)
2. product_carbon_predictions -- full breakdown (upsert on conflict)
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.api.v1.schemas.response import PredictionResult
from app.supabase.client import SupabaseClient

logger = logging.getLogger(__name__)


async def write_predictions(
    client: SupabaseClient,
    predictions: list[PredictionResult],
) -> dict[str, int]:
    """Write prediction results to Supabase.

    Successful predictions are written to both product_environment and
    product_carbon_predictions. Permanently failed predictions (null carbon
    values) are written to product_carbon_predictions only, marking them as
    "tried and failed" in the database.

    Args:
        client: Supabase PostgREST client.
        predictions: List of PredictionResult objects from the pipeline.

    Returns:
        Dict with "written" and "skipped" counts.
    """
    successful = [p for p in predictions if not p.error]
    permanently_failed = [
        p for p in predictions if p.error and p.carbon_kg_co2e is None
    ]
    skipped = len(predictions) - len(successful) - len(permanently_failed)

    if not successful and not permanently_failed:
        return {"written": 0, "skipped": len(predictions)}

    now = datetime.now(timezone.utc).isoformat()

    # 1. Insert successful into product_environment (insert-only, skip duplicates)
    if successful:
        env_rows = _build_environment_rows(successful)
        try:
            await client.insert_if_not_exists(
                "product_environment",
                env_rows,
                on_conflict="product_id,metric",
            )
        except Exception:
            logger.exception("Failed to write to product_environment")

    # 2. Upsert into product_carbon_predictions (successful + permanently failed)
    pred_rows = _build_prediction_rows(successful, now)
    pred_rows.extend(_build_failed_prediction_rows(permanently_failed, now))

    if pred_rows:
        has_table = await client.table_exists("product_carbon_predictions")
        if has_table:
            try:
                await client.upsert(
                    "product_carbon_predictions",
                    pred_rows,
                    on_conflict="product_id",
                )
            except Exception:
                logger.exception("Failed to write to product_carbon_predictions")
        else:
            logger.warning(
                "product_carbon_predictions table not found. "
                "Run migration 001 first. Writing to product_environment only."
            )

    written = len(successful) + len(permanently_failed)
    return {"written": written, "skipped": skipped}


def _build_environment_rows(
    predictions: list[PredictionResult],
) -> list[dict[str, Any]]:
    """Build rows for the product_environment table."""
    return [
        {
            "product_id": p.product_id,
            "value": f"{p.carbon_kg_co2e:.4f}",
            "unit": "kgCO2e",
            "metric": "carbon_kg_co2e",
        }
        for p in predictions
    ]


def _build_prediction_rows(
    predictions: list[PredictionResult],
    now: str,
) -> list[dict[str, Any]]:
    """Build rows for the product_carbon_predictions table (successful)."""
    return [
        {
            "product_id": p.product_id,
            "carbon_kg_co2e": p.carbon_kg_co2e,
            "cf_raw_materials_kg_co2e": p.components.raw_materials if p.components else None,
            "cf_transport_kg_co2e": p.components.transport if p.components else None,
            "cf_processing_kg_co2e": p.components.processing if p.components else None,
            "cf_packaging_kg_co2e": p.components.packaging if p.components else None,
            "confidence": p.confidence,
            "model_used": p.model_used,
            "is_rare": p.is_rare,
            "warnings": p.warnings,
            "predicted_at": now,
            "updated_at": now,
        }
        for p in predictions
    ]


def _build_failed_prediction_rows(
    predictions: list[PredictionResult],
    now: str,
) -> list[dict[str, Any]]:
    """Build rows for permanently failed products (NULL carbon values)."""
    return [
        {
            "product_id": p.product_id,
            "carbon_kg_co2e": None,
            "cf_raw_materials_kg_co2e": None,
            "cf_transport_kg_co2e": None,
            "cf_processing_kg_co2e": None,
            "cf_packaging_kg_co2e": None,
            "confidence": None,
            "model_used": p.model_used,
            "is_rare": p.is_rare,
            "warnings": p.warnings,
            "predicted_at": now,
            "updated_at": now,
        }
        for p in predictions
    ]
