# Avelero -- ESPResso Integration

## What Avelero sends

`POST /api/v1/predict` with bearer auth.

```json
{
  "brand_id": "uuid",
  "product_ids": ["uuid-1", "uuid-2"],
  "confidence_threshold": 0.7
}
```

- `brand_id` (required) -- brand that owns the products
- `product_ids` (required) -- 1 to 100 UUIDs per request
- `confidence_threshold` (optional, default 0.7) -- products scoring below
  this on Model A get re-predicted by Models B and C; the best score wins

Auth header: `Authorization: Bearer <ESPRESSO_API_KEY>`

## What Avelero receives

A summary only. Per-product results are written directly to Supabase by the
ESPResso service -- they never appear in the HTTP response.

```json
{
  "summary": {
    "total_products": 2,
    "successful": 2,
    "failed": 0,
    "rare_count": 0,
    "avg_confidence": 0.82,
    "processing_time_seconds": 1.2
  },
  "failed_products": [],
  "db_write": { "written": 2, "skipped": 0 }
}
```

- `summary` -- counts and timing for the batch
- `failed_products` -- list of `{ product_id, reason }` for any failures
- `db_write` -- how many predictions were persisted to Supabase

Errors: 401 (bad key), 422 (bad input), 400 (no products found), 500 (pipeline failure).

## Env vars

```env
ESPRESSO_SERVICE_URL=https://espresso.your-domain.com
ESPRESSO_API_KEY=your-shared-secret
```

## Implementation pattern

Follow the existing bulk export/import pattern in Avelero:

| Layer | Reference file | What it does |
|-------|---------------|-------------|
| Bulk menu | `apps/app/.../shared/bulk-actions-menu.tsx` | Selection -> action -> confirm |
| TRPC | `apps/api/.../bulk/export.ts` | `start` mutation, creates Trigger.dev task |
| Trigger.dev | `packages/jobs/.../bulk/export-products.ts` | Chunks IDs, calls service, reports progress |
| Progress | `apps/app/.../use-job-progress.ts` | Realtime updates via publicAccessToken |

Flow: user selects products -> TRPC creates job -> Trigger.dev chunks and
calls `/api/v1/predict` per chunk -> results land in Supabase -> UI refreshes.

## Quick test

```bash
curl -s -X POST http://localhost:8000/api/v1/predict \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"brand_id":"your-brand-uuid","product_ids":["product-uuid-1"]}' \
  | python3 -m json.tool
```
