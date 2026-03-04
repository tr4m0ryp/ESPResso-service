# Avelero -- ESPResso Integration

## What Avelero sends

`POST /api/v1/predict` with Bearer auth and HMAC brand authorization.

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <ESPRESSO_API_KEY>` |
| `X-Brand-Id` | Yes | UUID of the brand being queried |
| `X-Brand-Signature` | Production only | `<unix_timestamp>:<hmac_sha256_hex>` (see signing below) |
| `Content-Type` | Yes | `application/json` |

### Body

```json
{
  "brand_id": "uuid",
  "product_ids": ["uuid-1", "uuid-2"],
  "confidence_threshold": 0.7
}
```

- `brand_id` (required) -- must match the `X-Brand-Id` header exactly
- `product_ids` (required) -- 1 to 500 UUIDs per request
- `confidence_threshold` (optional, default 0.7) -- products scoring below
  this on Model A get re-predicted by Models B and C; the best score wins

### HMAC brand signing

ESPResso validates that the avelero backend explicitly authorized the
`brand_id` in each request. The signature prevents one compromised caller
from querying another brand's data.

**Protocol:**

1. Get the current Unix timestamp as a string (e.g. `"1709564400"`)
2. Compute `HMAC-SHA256(ESPRESSO_HMAC_SECRET, "<brand_id>:<timestamp>")`
3. Send as `X-Brand-Signature: <timestamp>:<hex_digest>`

Signatures expire after 5 minutes (replay protection).

**TypeScript example (avelero side):**

```typescript
import { createHmac } from "crypto";

function signBrand(brandId: string, secret: string): {
  "X-Brand-Id": string;
  "X-Brand-Signature": string;
} {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const digest = createHmac("sha256", secret)
    .update(`${brandId}:${timestamp}`)
    .digest("hex");
  return {
    "X-Brand-Id": brandId,
    "X-Brand-Signature": `${timestamp}:${digest}`,
  };
}
```

**Development mode:** When `HMAC_SECRET` is empty on the ESPResso side,
signature verification is skipped. Only `X-Brand-Id` is required.

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

### Response headers

| Header | Description |
|--------|-------------|
| `X-Request-ID` | 8-char unique ID for tracing (log correlation) |
| `X-RateLimit-Limit` | Max requests per window |
| `X-RateLimit-Remaining` | Requests remaining in current window |

### Error codes

| Status | Meaning |
|--------|---------|
| 400 | No valid products found for the given brand_id and product_ids |
| 401 | Invalid or missing Bearer token, or expired/invalid HMAC signature |
| 403 | Valid signature but brand_id in body does not match header |
| 422 | Malformed request body (validation error) |
| 429 | Rate limit exceeded (check `Retry-After` header) |
| 500 | Pipeline failure or Supabase not configured |
| 502 | Supabase fetch failed |

## Env vars

### Avelero side

```env
ESPRESSO_SERVICE_URL=https://espresso.your-domain.com
ESPRESSO_API_KEY=your-shared-bearer-token
ESPRESSO_HMAC_SECRET=your-shared-hmac-secret
```

Both services must share the same `API_KEY` (Bearer token) and `HMAC_SECRET`.

### ESPResso side (relevant to integration)

```env
API_KEY=your-shared-bearer-token
HMAC_SECRET=your-shared-hmac-secret
ENVIRONMENT=production
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
```

When `ENVIRONMENT=production`:
- `/docs`, `/redoc`, `/openapi.json` are disabled
- `HMAC_SECRET` is required (startup fails without it)

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

The Trigger.dev job should call `signBrand()` before each request to generate
fresh headers (signatures expire after 5 minutes).

## Quick test

### Development (no HMAC)

```bash
curl -s -X POST http://localhost:8000/api/v1/predict \
  -H "Authorization: Bearer your-api-key" \
  -H "X-Brand-Id: your-brand-uuid" \
  -H "Content-Type: application/json" \
  -d '{"brand_id":"your-brand-uuid","product_ids":["product-uuid-1"]}' \
  | python3 -m json.tool
```

### Production (with HMAC)

```bash
BRAND_ID="your-brand-uuid"
TIMESTAMP=$(date +%s)
SIGNATURE=$(echo -n "${BRAND_ID}:${TIMESTAMP}" \
  | openssl dgst -sha256 -hmac "your-hmac-secret" -hex \
  | awk '{print $NF}')

curl -s -X POST https://espresso.your-domain.com/api/v1/predict \
  -H "Authorization: Bearer your-api-key" \
  -H "X-Brand-Id: ${BRAND_ID}" \
  -H "X-Brand-Signature: ${TIMESTAMP}:${SIGNATURE}" \
  -H "Content-Type: application/json" \
  -d "{\"brand_id\":\"${BRAND_ID}\",\"product_ids\":[\"product-uuid-1\"]}" \
  | python3 -m json.tool
```

### Health check

```bash
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

When `HEALTH_REQUIRE_AUTH=true`, unauthenticated requests return only
`{"status": "healthy"}`. Pass the Bearer token for full details.
