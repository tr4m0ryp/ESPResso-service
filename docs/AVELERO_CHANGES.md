# Avelero Integration with ESPResso Carbon Footprint Service

This document describes the changes needed in the Avelero codebase to integrate
with the ESPResso carbon footprint prediction service.

## 1. New Environment Variables

Add to the deployment configuration:

```
ESPRESSO_SERVICE_URL=https://espresso.your-domain.com
ESPRESSO_API_KEY=shared-secret-here
```

## 2. Data Assembly

No data assembly is needed on the Avelero side. The ESPResso service fetches
product data directly from Supabase using the provided brand ID and product IDs.
Callers only need to send the IDs.

## 3. HTTP Call to ESPResso Service

The unified `/predict` endpoint handles the full cycle: it fetches product data
from Supabase, runs predictions, and writes results back. Callers only need to
send brand ID and product IDs.

```typescript
async function predictCarbonFootprint(
  brandId: string,
  productIds: string[],
): Promise<EspressoResponse> {
  const response = await fetch(
    `${process.env.ESPRESSO_SERVICE_URL}/api/v1/predict`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${process.env.ESPRESSO_API_KEY}`,
      },
      body: JSON.stringify({
        brand_id: brandId,
        product_ids: productIds,
      }),
    },
  );

  if (!response.ok) {
    throw new Error(`ESPResso service error: ${response.status}`);
  }

  return response.json();
}
```

## 4. Write-Back

The ESPResso service writes prediction results back to Supabase automatically.
The response includes a `db_write` field confirming how many were written.
No write-back code is needed in Avelero.

## 5. UI Trigger

Two options for triggering predictions:

### Option A: Manual button (recommended for initial rollout)
Add a "Calculate Carbon Footprint" button to the product detail page or bulk
actions menu. When clicked, assemble data for the selected product(s), call
the ESPResso service, and write results back.

### Option B: Background job
Run a periodic job (e.g., nightly) that:
1. Queries products missing carbon footprint data or updated since last prediction
2. Batches them (up to 500 per request)
3. Calls the ESPResso service
4. Writes results back

## 6. Missing Fields (Future Improvements)

The following fields are not currently tracked in Avelero but would improve
prediction accuracy if added. The ESPResso models handle their absence
gracefully (trained with 15% feature group masking), so predictions work
without them -- they will just be less precise for those components.

### total_transport_distance_km
- Add a numeric field to the product or product journey model
- Represents total estimated transport distance from raw material to point of sale
- Impact: transport component prediction degrades without this

### packaging_categories
- Add a relation linking products to packaging types
- Values should be from: "paper/cardboard", "plastic", "glass", "other"
- Impact: packaging component prediction degrades without this

### packaging_masses_kg
- Add weight fields corresponding to each packaging category
- Numeric, in kilograms
- Impact: packaging component prediction degrades without this

### Suggested schema additions

```sql
-- Option: add to product_journey or a new product_transport table
ALTER TABLE product_journey_steps
  ADD COLUMN transport_distance_km NUMERIC(10, 2);

-- Option: new product_packaging table
CREATE TABLE product_packaging (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  category TEXT NOT NULL,  -- paper/cardboard, plastic, glass, other
  mass_kg NUMERIC(8, 4) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

## 7. Response Shape Reference

### Request: POST /api/v1/predict

```json
{
  "brand_id": "uuid",
  "product_ids": ["uuid1", "uuid2"]
}
```

### Response

```json
{
  "predictions": [
    {
      "product_id": "uuid",
      "carbon_kg_co2e": 23.45,
      "components": {
        "raw_materials": 15.20,
        "transport": 4.00,
        "processing": 3.10,
        "packaging": 1.15
      },
      "confidence": 0.82,
      "model_used": "A",
      "is_rare": false,
      "warnings": ["..."]
    }
  ],
  "summary": {
    "total_products": 1,
    "successful": 1,
    "failed": 0,
    "rare_count": 0,
    "avg_confidence": 0.82,
    "processing_time_seconds": 1.2
  },
  "failed_products": [],
  "db_write": {
    "written": 1,
    "skipped": 0
  }
}
```

The `components` breakdown and `confidence` score can be stored in additional
columns or a JSON field for display in the UI if desired.
