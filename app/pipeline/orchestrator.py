"""6-phase batch pipeline coordinator.

Phase 1 uses bulk normalization: collect unique unknowns across all
products, resolve them in at most 3 parallel NIM calls (one per
column type), then patch each product using lookup tables. Product
IDs never enter the normalization -- they stay on ProductInput objects.
"""

import asyncio
import logging
import time
from typing import Any

from app.api.v1.schemas.request import ProductInput
from app.api.v1.schemas.response import (
    BatchPredictResponse,
    CarbonComponents,
    PredictionResult,
    PredictionSummary,
)
from app.config import Settings
from app.models.loader import ModelLoader
from app.models.predictor import batch_predict
from app.normalization.cache import NormalizationCache
from app.normalization.category_normalizer import CategoryNormalizer
from app.normalization.material_normalizer import MaterialNormalizer
from app.normalization.nim_client import NIMClient
from app.normalization.step_normalizer import StepNormalizer
from app.pipeline.bulk_normalizer import bulk_normalize
from app.pipeline.data_mapper import map_to_record

logger = logging.getLogger(__name__)


def _format_result(
    product: ProductInput,
    pred: dict[str, Any],
    model_name: str,
    is_rare: bool,
    warnings: list[str],
) -> PredictionResult:
    """Format a raw model prediction into a PredictionResult."""
    preds = pred["predictions"]
    conf = pred["confidence"]

    return PredictionResult(
        product_id=product.product_id,
        carbon_kg_co2e=preds["cf_total_kg_co2e"],
        components=CarbonComponents(
            raw_materials=preds["cf_raw_materials_kg_co2e"],
            transport=preds["cf_transport_kg_co2e"],
            processing=preds["cf_processing_kg_co2e"],
            packaging=preds["cf_packaging_kg_co2e"],
        ),
        confidence=conf["overall"],
        model_used=model_name,
        is_rare=is_rare,
        warnings=warnings,
    )


def _select_best(
    result_a: dict | None,
    result_b: dict | None,
    result_c: dict | None,
) -> tuple[dict, str]:
    """Select the model with highest overall confidence."""
    candidates = []
    if result_a is not None:
        candidates.append((result_a, "A"))
    if result_b is not None:
        candidates.append((result_b, "B"))
    if result_c is not None:
        candidates.append((result_c, "C"))

    if not candidates:
        raise ValueError("All models failed for this record")

    best = max(candidates, key=lambda x: x[0]["confidence"]["overall"])
    return best


def _make_failed_result(
    product: ProductInput,
    is_rare: bool,
    warnings: list[str],
    error: str,
) -> PredictionResult:
    return PredictionResult(
        product_id=product.product_id,
        carbon_kg_co2e=0.0,
        components=CarbonComponents(
            raw_materials=0, transport=0,
            processing=0, packaging=0,
        ),
        confidence=0.0,
        model_used="none",
        is_rare=is_rare,
        warnings=warnings,
        error=error,
    )


def _make_null_failed_result(
    product: ProductInput,
    error: str = "Prediction failed after 3 attempts",
) -> PredictionResult:
    """Create a result with null carbon values for permanently failed products."""
    return PredictionResult(
        product_id=product.product_id,
        carbon_kg_co2e=None,
        components=None,
        confidence=None,
        model_used="none",
        is_rare=False,
        error=error,
    )


async def run_batch_prediction(
    products: list[ProductInput],
    settings: Settings,
    model_loader: ModelLoader,
    nim_client: NIMClient,
    cache: NormalizationCache,
    confidence_threshold: float | None = None,
    max_retries: int = 3,
) -> BatchPredictResponse:
    """Run batch prediction with retry for failed products.

    Failed products are collected after each attempt and re-run through the
    full pipeline (normalization + all models). After max_retries exhausted,
    permanently failed products get null carbon values.
    """
    start = time.time()
    pending: list[tuple[int, ProductInput]] = list(enumerate(products))
    final: dict[int, PredictionResult] = {}
    total_retried = 0
    total_rare = 0

    for attempt in range(1, max_retries + 1):
        batch_products = [p for _, p in pending]
        response = await _run_pipeline(
            batch_products, settings, model_loader, nim_client, cache,
            confidence_threshold,
        )
        total_rare += response.summary.rare_count

        next_pending: list[tuple[int, ProductInput]] = []
        for (orig_idx, product), result in zip(pending, response.predictions):
            if not result.error:
                final[orig_idx] = result
            elif attempt < max_retries:
                next_pending.append((orig_idx, product))
            else:
                final[orig_idx] = _make_null_failed_result(product)

        if next_pending and attempt < max_retries:
            total_retried += len(next_pending)
            logger.warning(
                "Retrying %d failed products (attempt %d/%d)",
                len(next_pending), attempt + 1, max_retries,
            )
        pending = next_pending
        if not pending:
            break

    elapsed = time.time() - start
    ordered = [final[i] for i in range(len(products))]

    successful = sum(1 for r in ordered if not r.error)
    failed = len(ordered) - successful
    total_confidence = sum(
        r.confidence for r in ordered if r.confidence is not None and not r.error
    )
    avg_conf = total_confidence / successful if successful > 0 else 0.0

    return BatchPredictResponse(
        predictions=ordered,
        summary=PredictionSummary(
            total_products=len(products),
            successful=successful,
            failed=failed,
            rare_count=total_rare,
            avg_confidence=round(avg_conf, 4),
            processing_time_seconds=round(elapsed, 3),
            retried=total_retried,
        ),
    )


async def _run_pipeline(
    products: list[ProductInput],
    settings: Settings,
    model_loader: ModelLoader,
    nim_client: NIMClient,
    cache: NormalizationCache,
    confidence_threshold: float | None = None,
) -> BatchPredictResponse:
    """Run the 6-phase batch prediction pipeline.

    Phase 1: Bulk-normalize all inputs (max 3 NIM calls)
    Phase 2: Map to ESPResso records
    Phase 3: Model A batch predict (all products)
    Phase 4: Filter rare items by confidence threshold
    Phase 5: Run rare items through B + C in parallel
    Phase 6: Select best model for rare items
    """
    start = time.time()
    threshold = confidence_threshold or settings.RARITY_CONFIDENCE_THRESHOLD

    mat_norm = MaterialNormalizer(nim_client, cache)
    cat_norm = CategoryNormalizer(nim_client, cache)
    step_norm = StepNormalizer(nim_client, cache)

    # Phase 1: Bulk normalize
    normalized = await bulk_normalize(
        products, mat_norm, cat_norm, step_norm, cache,
    )

    # Phase 2: Map to ESPResso records
    records = []
    all_warnings: list[list[str]] = []
    for product, norm_data in zip(products, normalized):
        record, warnings = map_to_record(product, norm_data)
        records.append(record)
        all_warnings.append(warnings)

    # Phase 3: Model A batch predict (all products)
    model_a = model_loader.get("A")
    results_a = await asyncio.to_thread(batch_predict, model_a, records)

    # Phase 4: Filter rare items
    rare_indices: list[int] = []
    for i, result in enumerate(results_a):
        if result is None:
            rare_indices.append(i)
        elif result["confidence"]["overall"] < threshold:
            rare_indices.append(i)
    rare_set = set(rare_indices)

    # Phase 5: Run rare items through B + C in parallel
    results_b: list[dict | None] = []
    results_c: list[dict | None] = []
    if rare_indices:
        rare_records = [records[i] for i in rare_indices]
        status = model_loader.status()
        has_b = status.get("B", False)
        has_c = status.get("C", False)

        tasks = []
        if has_b:
            model_b = model_loader.get("B")
            tasks.append(
                asyncio.to_thread(batch_predict, model_b, rare_records)
            )
        if has_c:
            model_c = model_loader.get("C")
            tasks.append(
                asyncio.to_thread(batch_predict, model_c, rare_records)
            )

        if tasks:
            gathered = await asyncio.gather(*tasks)
            idx = 0
            if has_b:
                results_b = gathered[idx]
                idx += 1
            if has_c:
                results_c = gathered[idx]

        if not results_b:
            results_b = [None] * len(rare_records)
        if not results_c:
            results_c = [None] * len(rare_records)

    # Phase 6: Assemble final results
    final_results: list[PredictionResult] = []
    rare_ptr = 0
    successful = 0
    failed = 0
    total_confidence = 0.0

    for i, product in enumerate(products):
        if i in rare_set:
            ra = results_a[i]
            rb = results_b[rare_ptr] if results_b else None
            rc = results_c[rare_ptr] if results_c else None
            rare_ptr += 1

            try:
                best_result, best_model = _select_best(ra, rb, rc)
                result = _format_result(
                    product, best_result, best_model,
                    is_rare=True, warnings=all_warnings[i],
                )
                final_results.append(result)
                successful += 1
                total_confidence += result.confidence
            except ValueError:
                final_results.append(_make_failed_result(
                    product, is_rare=True, warnings=all_warnings[i],
                    error="All models failed for this product",
                ))
                failed += 1
        else:
            ra = results_a[i]
            if ra is not None:
                result = _format_result(
                    product, ra, "A",
                    is_rare=False, warnings=all_warnings[i],
                )
                final_results.append(result)
                successful += 1
                total_confidence += result.confidence
            else:
                final_results.append(_make_failed_result(
                    product, is_rare=False, warnings=all_warnings[i],
                    error="Model A prediction failed",
                ))
                failed += 1

    elapsed = time.time() - start
    avg_conf = total_confidence / successful if successful > 0 else 0.0

    return BatchPredictResponse(
        predictions=final_results,
        summary=PredictionSummary(
            total_products=len(products),
            successful=successful,
            failed=failed,
            rare_count=len(rare_indices),
            avg_confidence=round(avg_conf, 4),
            processing_time_seconds=round(elapsed, 3),
        ),
    )
