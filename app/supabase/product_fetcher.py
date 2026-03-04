"""Fetch product data from Supabase and assemble into ProductInput objects.

Ported from scripts/batch_predict.py load_product_data(), adapted to use
async PostgREST queries instead of raw psycopg2 SQL.
"""

import logging
from typing import Any

from app.api.v1.schemas.request import MaterialInput, ProductInput
from app.supabase.client import SupabaseClient

logger = logging.getLogger(__name__)


def _build_in_filter(ids: list[str]) -> str:
    """Build a PostgREST IN filter value: in.(uuid1,uuid2,...)."""
    joined = ",".join(ids)
    return f"in.({joined})"


def _build_category_paths(
    category_rows: list[dict[str, Any]],
    target_ids: set[str],
) -> dict[str, list[str]]:
    """Walk the taxonomy_categories tree to build root-to-leaf paths."""
    cat_map: dict[str, dict[str, Any]] = {}
    for row in category_rows:
        cid = str(row["id"])
        cat_map[cid] = {
            "name": row["name"],
            "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
        }

    paths: dict[str, list[str]] = {}
    for cat_id in target_ids:
        path: list[str] = []
        current = str(cat_id)
        visited: set[str] = set()
        while current and current in cat_map and current not in visited:
            visited.add(current)
            path.insert(0, cat_map[current]["name"])
            current = cat_map[current]["parent_id"]
        paths[str(cat_id)] = path
    return paths


async def fetch_products_for_prediction(
    client: SupabaseClient,
    brand_id: str,
    product_ids: list[str],
) -> list[ProductInput]:
    """Fetch all product data needed for ESPResso predictions.

    Queries (all via PostgREST):
    1. products -- base info filtered by brand_id + product_ids
    2. product_materials + brand_materials -- material details
    3. product_weight -- weight and unit
    4. product_journey_steps -- manufacturing steps + transport distance
    5. taxonomy_categories -- category path from root to leaf
    6. product_packaging -- packaging data (gracefully skipped if missing)

    Returns:
        List of ProductInput objects ready for the prediction pipeline.
    """
    id_filter = _build_in_filter(product_ids)

    # 1. Base product info
    product_rows = await client.select(
        "products",
        columns="id,name,category_id",
        params={
            "brand_id": f"eq.{brand_id}",
            "id": id_filter,
            "order": "created_at.asc",
        },
    )
    if not product_rows:
        return []

    products_map: dict[str, dict[str, Any]] = {
        str(r["id"]): r for r in product_rows
    }
    found_ids = list(products_map.keys())
    found_filter = _build_in_filter(found_ids)

    # 2-5: Fetch related data in parallel-ish calls
    materials_rows = await client.select(
        "product_materials",
        columns="product_id,percentage,brand_material_id,brand_materials(name,country_of_origin)",
        params={
            "product_id": found_filter,
            "order": "created_at.asc",
        },
    )

    weight_rows = await client.select(
        "product_weight",
        columns="product_id,weight,weight_unit",
        params={"product_id": found_filter},
    )

    step_rows = await client.select(
        "product_journey_steps",
        columns="product_id,step_type,transport_distance_km",
        params={
            "product_id": found_filter,
            "order": "sort_index.asc",
        },
    )

    # 5. Category paths
    category_ids: set[str] = set()
    for p in products_map.values():
        if p.get("category_id"):
            category_ids.add(str(p["category_id"]))

    category_paths: dict[str, list[str]] = {}
    if category_ids:
        cat_rows = await client.select(
            "taxonomy_categories",
            columns="id,name,parent_id",
        )
        category_paths = _build_category_paths(cat_rows, category_ids)

    # 6. Packaging (gracefully skip if table doesn't exist)
    packaging_rows: list[dict[str, Any]] = []
    has_packaging = await client.table_exists("product_packaging")
    if has_packaging:
        packaging_rows = await client.select(
            "product_packaging",
            columns="product_id,category,mass_kg",
            params={
                "product_id": found_filter,
                "order": "created_at.asc",
            },
        )

    # Index related data by product_id
    materials_by_pid = _group_by_pid(materials_rows)
    weights_by_pid = _index_by_pid(weight_rows)
    steps_by_pid = _group_by_pid(step_rows)
    packaging_by_pid = _group_by_pid(packaging_rows)

    # Assemble ProductInput objects
    results: list[ProductInput] = []
    for pid, prow in products_map.items():
        mats = materials_by_pid.get(pid, [])
        weight_row = weights_by_pid.get(pid)
        steps = steps_by_pid.get(pid, [])
        cat_id = str(prow["category_id"]) if prow.get("category_id") else None
        cat_path = category_paths.get(cat_id, []) if cat_id else []
        packaging = packaging_by_pid.get(pid, [])

        total_weight = (
            float(weight_row["weight"])
            if weight_row and weight_row.get("weight") is not None
            else None
        )
        weight_unit = (
            weight_row["weight_unit"]
            if weight_row and weight_row.get("weight_unit")
            else "g"
        )

        # Sum transport distances from journey steps
        transport_km = None
        step_distances = [
            float(s["transport_distance_km"])
            for s in steps
            if s.get("transport_distance_km") is not None
        ]
        if step_distances:
            transport_km = sum(step_distances)

        # Extract material info from the embedded brand_materials join
        material_inputs = _extract_materials(mats)

        origin_region = (
            material_inputs[0].country_of_origin if material_inputs else None
        )

        results.append(ProductInput(
            product_id=pid,
            name=prow.get("name") or "",
            category_path=cat_path,
            materials=material_inputs,
            total_weight_kg=total_weight,
            weight_unit=weight_unit,
            preprocessing_steps=[s["step_type"] for s in steps],
            origin_region=origin_region,
            packaging_categories=[p["category"] for p in packaging],
            packaging_masses_kg=[float(p["mass_kg"]) for p in packaging],
            total_transport_distance_km=transport_km,
        ))

    return results


def _extract_materials(
    mat_rows: list[dict[str, Any]],
) -> list[MaterialInput]:
    """Extract MaterialInput from PostgREST rows with embedded brand_materials."""
    result: list[MaterialInput] = []
    for row in mat_rows:
        bm = row.get("brand_materials")
        if not bm:
            continue
        result.append(MaterialInput(
            name=bm.get("name", ""),
            percentage=float(row["percentage"]) if row.get("percentage") else 0.0,
            country_of_origin=bm.get("country_of_origin"),
        ))
    return result


def _group_by_pid(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group rows by product_id."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        pid = str(row["product_id"])
        grouped.setdefault(pid, []).append(row)
    return grouped


def _index_by_pid(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index rows by product_id (one row per product)."""
    return {str(r["product_id"]): r for r in rows}
