"""Category path normalizer: Avelero taxonomy -> ESPResso vocabulary.

Supports both single-path normalization (backward compat) and bulk
normalization that deduplicates unknown root categories across an
entire batch before making a single NIM call.
"""

import logging

from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient

logger = logging.getLogger(__name__)

# Known Avelero root categories -> ESPResso category_name
_CATEGORY_MAP: dict[str, str] = {
    "clothing": "Clothing",
    "footwear": "Footwear",
    "accessories": "Accessories",
    "bags": "Bags",
    "textiles": "Textiles",
    "home textiles": "Home Textiles",
    "sportswear": "Sportswear",
    "outdoor": "Outdoor",
    "underwear": "Underwear",
    "swimwear": "Swimwear",
}

_VALID_CATEGORIES = sorted(set(_CATEGORY_MAP.values()))

_SYSTEM_PROMPT = (
    "You are a product taxonomy expert for fashion and textile "
    "Digital Product Passports (DPP). Your task is to classify "
    "product categories into the canonical taxonomy used by carbon "
    "footprint prediction models.\n\n"
    "VALID TOP-LEVEL CATEGORIES:\n"
    + "\n".join(f"  - {c}" for c in _VALID_CATEGORIES)
    + "\n\n"
    "RULES:\n"
    "- Output EXACTLY one category name from the list above\n"
    "- Never invent new categories outside the vocabulary\n"
    "- Map the root of the category path to the closest match\n\n"
    "EXAMPLES:\n"
    "- Apparel -> Clothing\n"
    "- Shoes -> Footwear\n"
    "- Luggage -> Bags\n"
    "- Garments -> Clothing\n"
    "- Activewear -> Sportswear\n"
    "- Home Decor -> Home Textiles\n"
)


class CategoryNormalizer:
    """Normalize Avelero category paths to ESPResso vocabulary."""

    def __init__(
        self, nim_client: NIMClient, cache: NormalizationCache,
    ):
        self._nim = nim_client
        self._cache = cache

    async def normalize(
        self, category_path: list[str],
    ) -> tuple[str, str]:
        """Map category_path to (category_name, subcategory_name).

        Uses the root of the path as category_name and the leaf as
        subcategory_name. Falls back to NIM for unknown categories.
        """
        if not category_path:
            return ("Unknown", "Unknown")

        root = category_path[0].strip()
        leaf = category_path[-1].strip()

        root_lower = root.lower()
        category_name = _CATEGORY_MAP.get(root_lower, root)

        subcategory_name = leaf if len(category_path) > 1 else root

        if root_lower not in _CATEGORY_MAP:
            cache_key = f"category:{root_lower}"
            cached = await self._cache.get(cache_key)
            if cached is not None:
                category_name = cached
            else:
                try:
                    result = await self._nim.complete_with_choices(
                        _SYSTEM_PROMPT,
                        f"Category: {root}",
                        _VALID_CATEGORIES,
                    )
                    category_name = result.strip()
                    await self._cache.set(cache_key, category_name)
                except Exception:
                    logger.warning(
                        "NIM category normalization failed for %s",
                        category_path,
                    )

        return (category_name, subcategory_name)

    async def normalize_bulk(
        self, raw_roots: list[str],
    ) -> dict[str, str]:
        """Normalize a list of unknown root category names in bulk.

        1. Deduplicate input root names
        2. Filter out roots resolved by static map or cache
        3. Send remaining unknowns in one NIM call with guided_choice
        4. Cache all NIM results
        5. Return mapping: {raw_root: canonical_category}
        """
        result_map: dict[str, str] = {}
        unknowns: list[str] = []

        unique_roots = list(dict.fromkeys(raw_roots))

        for raw in unique_roots:
            root_lower = raw.strip().lower()

            if root_lower in _CATEGORY_MAP:
                result_map[raw] = _CATEGORY_MAP[root_lower]
                continue

            cache_key = f"category:{root_lower}"
            cached = await self._cache.get(cache_key)
            if cached is not None:
                result_map[raw] = cached
                continue

            unknowns.append(raw)

        if unknowns:
            try:
                results = await self._nim.complete_bulk(
                    _SYSTEM_PROMPT,
                    unknowns,
                    choices=_VALID_CATEGORIES,
                )
                for raw, canonical in zip(unknowns, results):
                    canonical = canonical.strip()
                    if not canonical:
                        canonical = raw.strip()
                    cache_key = f"category:{raw.strip().lower()}"
                    await self._cache.set(cache_key, canonical)
                    result_map[raw] = canonical
                    logger.info(
                        "NIM bulk normalized category: %r -> %r",
                        raw, canonical,
                    )
            except Exception:
                logger.warning(
                    "NIM bulk category normalization failed for %d "
                    "roots, falling back to per-item",
                    len(unknowns),
                )
                for raw in unknowns:
                    try:
                        result = await self._nim.complete_with_choices(
                            _SYSTEM_PROMPT,
                            f"Category: {raw}",
                            _VALID_CATEGORIES,
                        )
                        result_map[raw] = result.strip()
                    except Exception:
                        result_map[raw] = raw.strip()

        return result_map
