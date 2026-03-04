"""Batch prediction wrapper with per-record error isolation."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def batch_predict(
    model: Any, records: list[dict],
) -> list[dict | None]:
    """Run model.predict() on each record with error isolation.

    One failure does not kill the entire batch; failed records
    return None.

    Args:
        model: A loaded ESPResso model (A, B, or C).
        records: List of ESPResso record dicts.

    Returns:
        List of prediction result dicts (or None for failures).
    """
    results: list[dict | None] = []
    for i, record in enumerate(records):
        try:
            result = model.predict(record)
            results.append(result)
        except Exception:
            logger.exception(
                "Prediction failed for record %d", i,
            )
            results.append(None)
    return results
