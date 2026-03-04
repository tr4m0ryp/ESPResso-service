"""Batch prediction runner: send product IDs to ESPResso service for prediction.

The ESPResso service handles the full cycle internally: fetching product data
from Supabase, running predictions, and writing results back.

Usage:
    # Predict all products for a brand
    python scripts/batch_predict.py --brand-id <uuid>

    # Predict specific products for a brand
    python scripts/batch_predict.py --brand-id <uuid> --product-ids <uuid1> <uuid2>

    # Predict products missing carbon data
    python scripts/batch_predict.py --brand-id <uuid> --missing-only

Requires:
    DATABASE_URL      - Avelero PostgreSQL connection string (for product discovery)
    ESPRESSO_URL      - ESPResso service URL (default: http://localhost:8000)
    ESPRESSO_API_KEY  - ESPResso service API key
"""

import argparse
import os
import sys
from pathlib import Path

# Load .env file from project root if it exists
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

try:
    import psycopg2
except ImportError:
    print("Install psycopg2: pip install psycopg2-binary")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is required")
        sys.exit(1)
    return psycopg2.connect(url)


def get_espresso_client():
    url = os.environ.get("ESPRESSO_URL", "http://localhost:8000")
    key = os.environ.get("ESPRESSO_API_KEY")
    if not key:
        print("ERROR: ESPRESSO_API_KEY is required")
        sys.exit(1)
    return url, key


def fetch_products_for_brand(conn, brand_id, missing_only=False):
    """Fetch product IDs for a brand."""
    with conn.cursor() as cur:
        if missing_only:
            cur.execute("""
                SELECT p.id FROM products p
                WHERE p.brand_id = %s
                AND NOT EXISTS (
                    SELECT 1 FROM product_environment pe
                    WHERE pe.product_id = p.id
                    AND pe.metric = 'carbon_kg_co2e'
                )
                ORDER BY p.created_at
            """, (brand_id,))
        else:
            cur.execute("""
                SELECT id FROM products
                WHERE brand_id = %s
                ORDER BY created_at
            """, (brand_id,))
        return [str(row[0]) for row in cur.fetchall()]


def call_espresso(brand_id, product_ids, batch_size=500):
    """Send product IDs to ESPResso service in batches."""
    url, key = get_espresso_client()
    all_predictions = []

    for i in range(0, len(product_ids), batch_size):
        batch = product_ids[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(product_ids) + batch_size - 1) // batch_size
        print(f"  Sending batch {batch_num}/{total_batches} ({len(batch)} products)...")

        resp = httpx.post(
            f"{url}/api/v1/predict",
            json={"brand_id": brand_id, "product_ids": batch},
            headers={"Authorization": f"Bearer {key}"},
            timeout=300.0,
        )

        if resp.status_code != 200:
            print(f"  ERROR: ESPResso returned {resp.status_code}")
            print(f"  {resp.text[:500]}")
            continue

        data = resp.json()
        summary = data["summary"]
        db_write = data.get("db_write") or {}
        print(
            f"  Results: {summary['successful']} ok, "
            f"{summary['failed']} failed, "
            f"{summary['rare_count']} rare, "
            f"avg confidence {summary['avg_confidence']:.2f}, "
            f"{summary['processing_time_seconds']:.1f}s"
        )
        if db_write:
            print(
                f"  DB write: {db_write.get('written', 0)} written, "
                f"{db_write.get('skipped', 0)} skipped"
            )
        all_predictions.extend(data["predictions"])

        if data.get("failed_products"):
            for fp in data["failed_products"]:
                print(f"  FAILED: {fp['product_id']} -- {fp['reason']}")

    return all_predictions


def main():
    parser = argparse.ArgumentParser(
        description="Run ESPResso predictions for Avelero products"
    )
    parser.add_argument(
        "--brand-id", required=True,
        help="Brand UUID (required -- the service needs it to fetch products)",
    )
    parser.add_argument(
        "--product-ids", nargs="+",
        help="Optional subset of product UUIDs to predict",
    )
    parser.add_argument(
        "--missing-only", action="store_true",
        help="Only predict products without existing carbon data",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Products per ESPResso request (default: 500)",
    )
    args = parser.parse_args()

    # Determine product IDs
    if args.product_ids:
        product_ids = args.product_ids
        print(f"Processing {len(product_ids)} specified products for brand {args.brand_id}")
    else:
        conn = get_db()
        print(f"Fetching products for brand {args.brand_id}...")
        product_ids = fetch_products_for_brand(
            conn, args.brand_id, missing_only=args.missing_only,
        )
        conn.close()
        filter_note = " (missing carbon data only)" if args.missing_only else ""
        print(f"Found {len(product_ids)} products{filter_note}")

    if not product_ids:
        print("No products to process.")
        return

    # Send to ESPResso (service handles fetch + predict + write internally)
    print("\nSending to ESPResso service...")
    predictions = call_espresso(args.brand_id, product_ids, batch_size=args.batch_size)

    # Summary
    if predictions:
        successful = [p for p in predictions if not p.get("error")]
        if successful:
            avg_carbon = sum(p["carbon_kg_co2e"] for p in successful) / len(successful)
            avg_conf = sum(p["confidence"] for p in successful) / len(successful)
            rare = sum(1 for p in successful if p["is_rare"])
            print(f"\nSummary:")
            print(f"  Products processed: {len(predictions)}")
            print(f"  Successful:         {len(successful)}")
            print(f"  Average carbon:     {avg_carbon:.2f} kg CO2e")
            print(f"  Average confidence: {avg_conf:.2f}")
            print(f"  Rare items:         {rare}")
    else:
        print("No predictions received.")


if __name__ == "__main__":
    main()
