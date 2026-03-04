"""Local end-to-end test: DB -> normalize -> predict -> display results.

Bypasses the API endpoint entirely. Connects to the Avelero database,
lists brands, lets you pick one, pulls products, normalizes materials
via the static synonym map (no NIM needed), runs predictions through
the loaded models, and displays results.

Used by start.sh for interactive local testing.
"""

import json
import os
import sys
from pathlib import Path

# Load .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 not installed.")
    print("  pip install psycopg2-binary")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def connect_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    # Mask password for display
    display = url
    if "@" in url and ":" in url:
        pre_at = url.split("@")[0]
        if ":" in pre_at:
            parts = pre_at.rsplit(":", 1)
            display = parts[0] + ":****@" + url.split("@", 1)[1]

    print(f"Connecting to: {display}")
    try:
        conn = psycopg2.connect(url)
        print("Connected successfully.\n")
        return conn
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)


def list_brands(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                b.id,
                b.name,
                COUNT(p.id) AS product_count
            FROM brands b
            LEFT JOIN products p ON p.brand_id = b.id
            GROUP BY b.id, b.name
            HAVING COUNT(p.id) > 0
            ORDER BY COUNT(p.id) DESC
        """)
        return cur.fetchall()


def get_brand_stats(conn, brand_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                COUNT(p.id) AS total_products,
                COUNT(pw.product_id) AS with_weight,
                COUNT(DISTINCT pm.product_id) AS with_materials,
                COUNT(DISTINCT pjs.product_id) AS with_journey,
                COUNT(DISTINCT pe.product_id) AS with_carbon
            FROM products p
            LEFT JOIN product_weight pw ON pw.product_id = p.id
            LEFT JOIN product_materials pm ON pm.product_id = p.id
            LEFT JOIN product_journey_steps pjs ON pjs.product_id = p.id
            LEFT JOIN product_environment pe
                ON pe.product_id = p.id AND pe.metric = 'carbon_kg_co2e'
            WHERE p.brand_id = %s
        """, (brand_id,))
        return cur.fetchone()


def load_products(conn, brand_id, limit=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        limit_clause = f"LIMIT {limit}" if limit else ""
        cur.execute(f"""
            SELECT p.id, p.name, p.category_id
            FROM products p
            WHERE p.brand_id = %s
            ORDER BY p.created_at
            {limit_clause}
        """, (brand_id,))
        product_rows = cur.fetchall()
        product_ids = [r["id"] for r in product_rows]

        if not product_ids:
            return []

        placeholders = ",".join(["%s"] * len(product_ids))

        # Materials
        cur.execute(f"""
            SELECT pm.product_id, bm.name AS material_name,
                   pm.percentage, bm.country_of_origin
            FROM product_materials pm
            JOIN brand_materials bm ON bm.id = pm.brand_material_id
            WHERE pm.product_id IN ({placeholders})
            ORDER BY pm.created_at
        """, product_ids)
        materials_by_pid = {}
        for row in cur.fetchall():
            pid = str(row["product_id"])
            materials_by_pid.setdefault(pid, []).append(row)

        # Weights
        cur.execute(f"""
            SELECT product_id, weight, weight_unit
            FROM product_weight
            WHERE product_id IN ({placeholders})
        """, product_ids)
        weights = {str(r["product_id"]): r for r in cur.fetchall()}

        # Journey steps
        cur.execute(f"""
            SELECT product_id, step_type
            FROM product_journey_steps
            WHERE product_id IN ({placeholders})
            ORDER BY sort_index
        """, product_ids)
        steps_by_pid = {}
        for row in cur.fetchall():
            pid = str(row["product_id"])
            steps_by_pid.setdefault(pid, []).append(row["step_type"])

        # Category paths
        category_ids = [
            r["category_id"] for r in product_rows if r["category_id"]
        ]
        category_paths = {}
        if category_ids:
            cur.execute("SELECT id, name, parent_id FROM taxonomy_categories")
            cat_map = {
                str(r["id"]): {
                    "name": r["name"],
                    "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                }
                for r in cur.fetchall()
            }
            for cat_id in category_ids:
                path = []
                current = str(cat_id)
                visited = set()
                while current and current in cat_map and current not in visited:
                    visited.add(current)
                    path.insert(0, cat_map[current]["name"])
                    current = cat_map[current]["parent_id"]
                category_paths[str(cat_id)] = path

    # Assemble
    products = []
    for prow in product_rows:
        pid = str(prow["id"])
        mats = materials_by_pid.get(pid, [])
        w = weights.get(pid)
        steps = steps_by_pid.get(pid, [])
        cat_id = str(prow["category_id"]) if prow["category_id"] else None
        cat_path = category_paths.get(cat_id, []) if cat_id else []

        products.append({
            "product_id": pid,
            "name": prow["name"] or "(unnamed)",
            "category_path": cat_path,
            "materials": [
                {
                    "name": m["material_name"],
                    "percentage": float(m["percentage"]) if m["percentage"] else 0.0,
                    "country_of_origin": m.get("country_of_origin"),
                }
                for m in mats
            ],
            "total_weight_kg": float(w["weight"]) if w and w["weight"] else None,
            "weight_unit": w["weight_unit"] if w and w["weight_unit"] else "g",
            "preprocessing_steps": steps,
        })

    return products


# ---------------------------------------------------------------------------
# Normalization (static only, no NIM)
# ---------------------------------------------------------------------------

def normalize_materials(products):
    """Normalize material names using the static synonym map."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.normalization.synonym_map import resolve_static

    stats = {"total": 0, "resolved": 0, "unresolved_names": set()}

    for product in products:
        for mat in product["materials"]:
            stats["total"] += 1
            resolved = resolve_static(mat["name"])
            if resolved is None:
                resolved = resolve_static(mat["name"].strip().lower())
            if resolved is not None:
                mat["_normalized"] = resolved
                stats["resolved"] += 1
            else:
                mat["_normalized"] = mat["name"]
                stats["unresolved_names"].add(mat["name"])

    return stats


# ---------------------------------------------------------------------------
# Model prediction (direct, no API)
# ---------------------------------------------------------------------------

def try_load_models():
    """Try to load model artifacts. Returns dict of loaded models or empty."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    models = {}

    paths = {
        "A": os.environ.get("MODEL_A_PATH", "artifacts/model_a.pkl"),
        "B": os.environ.get("MODEL_B_PATH", "artifacts/model_b.pkl"),
        "C": os.environ.get("MODEL_C_PATH", "artifacts/model_c.pkl"),
    }

    for name, path in paths.items():
        full = Path(__file__).resolve().parent.parent / path
        if full.exists():
            try:
                print(f"  Loading Model {name} from {path}...")
                if name == "A":
                    from espresso_models.model_a.model import CarbonFootprintModel
                    models[name] = CarbonFootprintModel.load(str(full))
                elif name == "B":
                    from espresso_models.model_b.model import CarbonFootprintModelB
                    models[name] = CarbonFootprintModelB.load(str(full))
                elif name == "C":
                    from espresso_models.model_c.model import CarbonFootprintModelC
                    models[name] = CarbonFootprintModelC.load(str(full))
                print(f"  Model {name} loaded.")
            except Exception as e:
                print(f"  Model {name} failed to load: {e}")
        else:
            print(f"  Model {name} not found at {path} -- skipping")

    return models


def build_espresso_record(product):
    """Convert a product dict to an ESPResso input record."""
    from app.pipeline.data_mapper import _convert_weight

    total_weight = _convert_weight(
        product["total_weight_kg"],
        product.get("weight_unit", "g"),
    )
    if total_weight is None:
        total_weight = 1.0

    material_names = [m.get("_normalized", m["name"]) for m in product["materials"]]
    material_weights = [
        total_weight * (m["percentage"] / 100.0) for m in product["materials"]
    ]

    origin = None
    for m in product["materials"]:
        if m.get("country_of_origin"):
            origin = m["country_of_origin"]
            break

    return {
        "category_name": product["category_path"][0] if product["category_path"] else "Unknown",
        "subcategory_name": product["category_path"][-1] if product["category_path"] else "Unknown",
        "materials": material_names,
        "material_weights_kg": material_weights,
        "total_weight_kg": total_weight,
        "preprocessing_steps": product.get("preprocessing_steps", []),
        "total_transport_distance_km": None,
        "origin_region": origin,
        "packaging_categories": None,
        "packaging_masses_kg": None,
    }


def predict_products(products, models):
    """Run predictions on assembled products."""
    if "A" not in models:
        print("\n  No models loaded -- cannot predict.")
        return []

    model_a = models["A"]
    threshold = float(os.environ.get("RARITY_CONFIDENCE_THRESHOLD", "0.6"))
    results = []

    for i, product in enumerate(products):
        record = build_espresso_record(product)
        try:
            result = model_a.predict(record)
            confidence = result["confidence"]["overall"]
            model_used = "A"

            # If rare and we have B or C, try them
            if confidence < threshold:
                best_conf = confidence
                best_result = result
                for mname in ("B", "C"):
                    if mname in models:
                        try:
                            alt = models[mname].predict(record)
                            alt_conf = alt["confidence"]["overall"]
                            if alt_conf > best_conf:
                                best_conf = alt_conf
                                best_result = alt
                                model_used = mname
                        except Exception:
                            pass
                result = best_result
                confidence = best_conf

            preds = result["predictions"]
            results.append({
                "product_id": product["product_id"],
                "name": product["name"],
                "carbon_kg_co2e": preds["cf_total_kg_co2e"],
                "raw_materials": preds["cf_raw_materials_kg_co2e"],
                "transport": preds["cf_transport_kg_co2e"],
                "processing": preds["cf_processing_kg_co2e"],
                "packaging": preds["cf_packaging_kg_co2e"],
                "confidence": confidence,
                "model_used": model_used,
                "is_rare": confidence < threshold,
            })
        except Exception as e:
            results.append({
                "product_id": product["product_id"],
                "name": product["name"],
                "error": str(e),
            })

        # Progress
        if (i + 1) % 25 == 0 or i == len(products) - 1:
            print(f"  Predicted {i + 1}/{len(products)}")

    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_table(headers, rows, widths=None):
    """Print a simple text table."""
    if widths is None:
        widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                  for i, h in enumerate(headers)]

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep = "  ".join("-" * w for w in widths)
    print(f"  {header_line}")
    print(f"  {sep}")
    for row in rows:
        line = "  ".join(str(row[i]).ljust(w) for i, w in enumerate(widths))
        print(f"  {line}")


# ---------------------------------------------------------------------------
# Main interactive flow
# ---------------------------------------------------------------------------

def main():
    print("=" * 64)
    print("  ESPResso Local Test Runner")
    print("=" * 64)
    print()

    # Step 1: Connect to database
    print("[1/5] Connecting to Avelero database...")
    conn = connect_db()

    # Step 2: List brands
    print("[2/5] Fetching brands with products...\n")
    brands = list_brands(conn)

    if not brands:
        print("  No brands with products found in the database.")
        conn.close()
        return

    headers = ["#", "Brand Name", "Products"]
    rows = [
        (str(i + 1), b["name"] or "(unnamed)", str(b["product_count"]))
        for i, b in enumerate(brands)
    ]
    print_table(headers, rows, widths=[4, 40, 10])

    # Step 3: Select brand
    print()
    while True:
        try:
            choice = input("  Select a brand number (or 'q' to quit): ").strip()
            if choice.lower() == "q":
                conn.close()
                return
            idx = int(choice) - 1
            if 0 <= idx < len(brands):
                break
            print(f"  Enter a number between 1 and {len(brands)}")
        except ValueError:
            print("  Enter a valid number")

    brand = brands[idx]
    brand_id = str(brand["id"])
    print(f"\n  Selected: {brand['name']} ({brand['product_count']} products)\n")

    # Step 4: Show brand data stats
    print("[3/5] Checking data quality...\n")
    stats = get_brand_stats(conn, brand_id)
    total = stats["total_products"]
    print(f"  Total products:      {total}")
    print(f"  With weight:         {stats['with_weight']}/{total}")
    print(f"  With materials:      {stats['with_materials']}/{total}")
    print(f"  With journey steps:  {stats['with_journey']}/{total}")
    print(f"  Already have carbon: {stats['with_carbon']}/{total}")

    # Ask how many to process
    print()
    limit_input = input(
        f"  How many products to test? (enter for all, or a number): "
    ).strip()
    limit = int(limit_input) if limit_input.isdigit() else None

    # Step 5: Load products
    print(f"\n[4/5] Loading product data...")
    products = load_products(conn, brand_id, limit=limit)
    print(f"  Loaded {len(products)} products\n")

    if not products:
        print("  No products found.")
        conn.close()
        return

    # Show sample product
    sample = products[0]
    print("  Sample product:")
    print(f"    Name:       {sample['name']}")
    print(f"    Category:   {' > '.join(sample['category_path']) if sample['category_path'] else '(none)'}")
    print(f"    Materials:  {len(sample['materials'])}")
    for m in sample["materials"][:5]:
        pct = f"{m['percentage']:.0f}%" if m["percentage"] else "?%"
        origin = f" ({m['country_of_origin']})" if m.get("country_of_origin") else ""
        print(f"      - {m['name']} [{pct}]{origin}")
    if len(sample["materials"]) > 5:
        print(f"      ... and {len(sample['materials']) - 5} more")
    weight = sample["total_weight_kg"]
    unit = sample.get("weight_unit", "g")
    print(f"    Weight:     {weight} {unit}" if weight else "    Weight:     (not set)")
    print(f"    Steps:      {', '.join(sample.get('preprocessing_steps', [])) or '(none)'}")

    # Normalize materials
    print("\n  Normalizing materials (static synonym map)...")
    norm_stats = normalize_materials(products)
    print(f"    {norm_stats['resolved']}/{norm_stats['total']} materials resolved via synonym map")
    if norm_stats["unresolved_names"]:
        unresolved = sorted(norm_stats["unresolved_names"])
        print(f"    {len(unresolved)} unresolved (would use NIM in production):")
        for name in unresolved[:10]:
            print(f"      - {name}")
        if len(unresolved) > 10:
            print(f"      ... and {len(unresolved) - 10} more")

    # Try loading models
    print(f"\n[5/5] Loading ESPResso models...\n")
    models = try_load_models()

    if not models:
        print("\n  No model artifacts found. Place .pkl files in artifacts/")
        print("  to run predictions. Showing assembled data instead.\n")

        print("  ESPResso records (first 3):\n")
        for p in products[:3]:
            record = build_espresso_record(p)
            print(f"    Product: {p['name']}")
            print(f"    Record:  {json.dumps(record, indent=4, default=str)}")
            print()

        conn.close()
        return

    # Run predictions
    print(f"\n  Running predictions on {len(products)} products...\n")
    results = predict_products(products, models)

    # Display results
    successful = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]

    if successful:
        print(f"\n  Results ({len(successful)} successful, {len(failed)} failed):\n")

        # Sort by carbon footprint descending
        successful.sort(key=lambda r: r["carbon_kg_co2e"], reverse=True)

        headers = ["Product", "kg CO2e", "Conf", "Model", "Raw Mat", "Transport", "Process", "Packaging"]
        rows = []
        for r in successful[:30]:
            name = r["name"][:30]
            rows.append((
                name,
                f"{r['carbon_kg_co2e']:.2f}",
                f"{r['confidence']:.2f}",
                r["model_used"],
                f"{r['raw_materials']:.2f}",
                f"{r['transport']:.2f}",
                f"{r['processing']:.2f}",
                f"{r['packaging']:.2f}",
            ))
        print_table(headers, rows, widths=[32, 10, 6, 6, 10, 10, 10, 10])

        if len(successful) > 30:
            print(f"\n  ... and {len(successful) - 30} more (showing top 30 by carbon)")

        # Summary
        avg_carbon = sum(r["carbon_kg_co2e"] for r in successful) / len(successful)
        avg_conf = sum(r["confidence"] for r in successful) / len(successful)
        rare = sum(1 for r in successful if r["is_rare"])
        print(f"\n  Summary:")
        print(f"    Average carbon footprint: {avg_carbon:.2f} kg CO2e")
        print(f"    Average confidence:       {avg_conf:.2f}")
        print(f"    Rare items (re-predicted): {rare}")
        print(f"    Model breakdown: " + ", ".join(
            f"{m}={sum(1 for r in successful if r['model_used'] == m)}"
            for m in ("A", "B", "C")
            if any(r["model_used"] == m for r in successful)
        ))

    if failed:
        print(f"\n  Failed predictions ({len(failed)}):")
        for r in failed[:5]:
            print(f"    {r['name']}: {r['error']}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
