"""Prediction endpoint.

POST /api/v1/predict -- accepts product IDs, fetches from Supabase, predicts,
writes results back, and returns full predictions with summary.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_cache, get_model_loader, get_nim_client, get_settings
from app.api.v1.schemas import (
    DbWriteResult,
    FailedProduct,
    PredictRequest,
    PredictResponse,
)
from app.config import Settings
from app.middleware.api_key_auth import verify_api_key
from app.models.loader import ModelLoader
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient
from app.pipeline.orchestrator import run_batch_prediction

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/predict",
    response_model=PredictResponse,
    dependencies=[Depends(verify_api_key)],
)
async def predict(
    request: PredictRequest,
    settings: Settings = Depends(get_settings),
    model_loader: ModelLoader = Depends(get_model_loader),
    nim_client: NIMClient = Depends(get_nim_client),
    cache: NormalizationCache = Depends(get_cache),
) -> PredictResponse:
    """Fetch product data from Supabase, predict, and write results back.

    Full cycle: fetch -> predict -> write -> respond.
    Returns full predictions, summary, failed list, and DB write result.
    """
    start = time.time()

    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY)",
        )

    from app.supabase.client import SupabaseClient

    client = SupabaseClient(settings)
    try:
        return await _run_predict_cycle(
            client, request, settings, model_loader, nim_client, cache, start,
        )
    finally:
        await client.close()


async def _run_predict_cycle(
    client: SupabaseClient,
    request: PredictRequest,
    settings: Settings,
    model_loader: ModelLoader,
    nim_client: NIMClient,
    cache: NormalizationCache,
    start: float,
) -> PredictResponse:
    """Execute the full fetch -> predict -> write cycle."""
    from app.supabase.product_fetcher import fetch_products_for_prediction
    from app.supabase.result_writer import write_predictions

    # Step 1: Fetch product data from Supabase
    try:
        products = await fetch_products_for_prediction(
            client, request.brand_id, request.product_ids,
        )
    except Exception as exc:
        logger.exception("Failed to fetch products from Supabase")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase fetch failed: {exc}",
        ) from exc

    if not products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No valid products found for the given brand_id and product_ids. "
                "Check that the IDs exist and belong to the specified brand."
            ),
        )

    # Step 2: Run through prediction pipeline
    try:
        batch_response = await run_batch_prediction(
            products=products,
            settings=settings,
            model_loader=model_loader,
            nim_client=nim_client,
            cache=cache,
            confidence_threshold=request.confidence_threshold,
        )
    except Exception as exc:
        logger.exception("Prediction pipeline failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction pipeline error: {exc}",
        ) from exc

    # Step 3: Write results back to Supabase
    db_write = None
    try:
        write_result = await write_predictions(client, batch_response.predictions)
        db_write = DbWriteResult(
            written=write_result["written"],
            skipped=write_result["skipped"],
        )
        logger.info(
            "Wrote predictions to Supabase",
            extra={"written": db_write.written, "skipped": db_write.skipped},
        )
    except Exception:
        logger.exception("Failed to write predictions to Supabase")

    # Step 4: Build response
    failed_products = [
        FailedProduct(product_id=p.product_id, reason=p.error or "Unknown error")
        for p in batch_response.predictions
        if p.error
    ]

    elapsed = time.time() - start
    return PredictResponse(
        predictions=batch_response.predictions,
        summary=batch_response.summary,
        failed_products=failed_products,
        db_write=db_write,
    )
