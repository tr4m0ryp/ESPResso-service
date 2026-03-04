"""Bulk normalization: collect unknowns, resolve in parallel, patch products.

The LLM never sees products, product IDs, or full records. It only
sees deduplicated lists of unknown raw values (material names, step
names, category roots). Product IDs stay on ProductInput objects
and are matched back by list index.
"""

import asyncio
import logging

from app.api.v1.schemas.request import ProductInput
from app.normalization.cache import NormalizationCache
from app.normalization.category_normalizer import (
    CategoryNormalizer,
    _CATEGORY_MAP,
)
from app.normalization.material_normalizer import MaterialNormalizer
from app.normalization.step_normalizer import StepNormalizer, _STEP_MAP
from app.normalization.synonym_map import resolve_static
from app.pipeline.data_mapper import NormalizedData

logger = logging.getLogger(__name__)


def _resolve_material(raw: str, bulk_lookup: dict[str, str]) -> str:
    """Resolve a material name from static map, then bulk lookup."""
    static = resolve_static(raw.strip())
    if static is not None:
        return static
    static = resolve_static(raw.strip().lower())
    if static is not None:
        return static
    return bulk_lookup.get(raw, raw.strip().lower())


async def _collect_unknowns(
    products: list[ProductInput],
    cache: NormalizationCache,
) -> tuple[list[str], list[str], list[str]]:
    """Stage 1: Collect unique unknowns per column across all products."""
    unknown_materials: dict[str, None] = {}
    unknown_steps: dict[str, None] = {}
    unknown_categories: dict[str, None] = {}

    for product in products:
        for m in product.materials:
            raw = m.name
            static = resolve_static(raw.strip())
            if static is None:
                static = resolve_static(raw.strip().lower())
            if static is None:
                cache_key = f"material:{raw.strip().lower()}"
                cached = await cache.get(cache_key)
                if cached is None:
                    unknown_materials[raw] = None

        for s in product.preprocessing_steps:
            cleaned = s.strip().lower()
            if cleaned not in _STEP_MAP:
                cache_key = f"step:{cleaned}"
                cached = await cache.get(cache_key)
                if cached is None:
                    unknown_steps[s] = None

        if product.category_path:
            root = product.category_path[0].strip()
            root_lower = root.lower()
            if root_lower not in _CATEGORY_MAP:
                cache_key = f"category:{root_lower}"
                cached = await cache.get(cache_key)
                if cached is None:
                    unknown_categories[root] = None

    return (
        list(unknown_materials.keys()),
        list(unknown_steps.keys()),
        list(unknown_categories.keys()),
    )


def _patch_product(
    product: ProductInput,
    mat_lookup: dict[str, str],
    step_lookup: dict[str, str],
    cat_lookup: dict[str, str],
) -> NormalizedData:
    """Stage 3: Patch a single product using lookup tables."""
    # Materials
    materials = [
        _resolve_material(m.name, mat_lookup) for m in product.materials
    ]

    # Steps
    steps = []
    for s in product.preprocessing_steps:
        cleaned = s.strip().lower()
        if cleaned in _STEP_MAP:
            resolved = _STEP_MAP[cleaned]
        elif s in step_lookup:
            resolved = step_lookup[s]
        else:
            resolved = cleaned
        if resolved and resolved != "unknown":
            steps.append(resolved)

    # Category
    if product.category_path:
        root = product.category_path[0].strip()
        leaf = product.category_path[-1].strip()
        root_lower = root.lower()
        if root_lower in _CATEGORY_MAP:
            cat_name = _CATEGORY_MAP[root_lower]
        elif root in cat_lookup:
            cat_name = cat_lookup[root]
        else:
            cat_name = root
        sub_name = leaf if len(product.category_path) > 1 else root
    else:
        cat_name = "Unknown"
        sub_name = "Unknown"

    return NormalizedData(
        materials=materials,
        category_name=cat_name,
        subcategory_name=sub_name,
        preprocessing_steps=steps,
    )


async def bulk_normalize(
    products: list[ProductInput],
    mat_norm: MaterialNormalizer,
    cat_norm: CategoryNormalizer,
    step_norm: StepNormalizer,
    cache: NormalizationCache,
) -> list[NormalizedData]:
    """Bulk-normalize all products in three stages.

    Stage 1: Collect unique unknowns per column across ALL products.
    Stage 2: One NIM call per column type (max 3, in parallel).
    Stage 3: Patch each product using the lookup tables.
    """
    unknown_mats, unknown_steps, unknown_cats = await _collect_unknowns(
        products, cache,
    )

    logger.info(
        "Bulk normalization: %d unknown materials, %d unknown steps, "
        "%d unknown categories",
        len(unknown_mats), len(unknown_steps), len(unknown_cats),
    )

    # Stage 2: Parallel NIM calls (one per column type)
    mat_lookup, step_lookup, cat_lookup = await asyncio.gather(
        mat_norm.normalize_bulk(unknown_mats),
        step_norm.normalize_bulk(unknown_steps),
        cat_norm.normalize_bulk(unknown_cats),
    )

    # Stage 3: Patch each product
    return [
        _patch_product(p, mat_lookup, step_lookup, cat_lookup)
        for p in products
    ]
