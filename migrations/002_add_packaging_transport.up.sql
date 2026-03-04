-- Migration 002: Add packaging and transport distance fields
--
-- These fields are not currently tracked in Avelero but improve
-- ESPResso prediction accuracy significantly. The models handle
-- their absence gracefully (trained with 15% feature group masking),
-- so this migration is optional for initial rollout.
--
-- Adding transport_distance_km to product_journey_steps makes sense
-- because each journey step represents a supply chain leg that has
-- an associated distance. The total is summed at query time.
--
-- Product packaging is a new concept that warrants its own table.

-- Add transport distance to journey steps
ALTER TABLE product_journey_steps
    ADD COLUMN IF NOT EXISTS transport_distance_km NUMERIC(10, 2);

COMMENT ON COLUMN product_journey_steps.transport_distance_km IS
    'Estimated transport distance for this journey step in kilometers. '
    'Sum across all steps gives total_transport_distance_km for ESPResso.';

-- Also add to variant journey steps for completeness
ALTER TABLE variant_journey_steps
    ADD COLUMN IF NOT EXISTS transport_distance_km NUMERIC(10, 2);

-- New packaging table
CREATE TABLE IF NOT EXISTS product_packaging (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE ON UPDATE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('paper/cardboard', 'plastic', 'glass', 'other')),
    mass_kg NUMERIC(8, 4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_packaging_product_id
    ON product_packaging(product_id);

COMMENT ON TABLE product_packaging IS
    'Product packaging materials. Categories match ESPResso vocabulary: '
    'paper/cardboard, plastic, glass, other.';

-- Variant-level packaging override table
CREATE TABLE IF NOT EXISTS variant_packaging (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id UUID NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE ON UPDATE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('paper/cardboard', 'plastic', 'glass', 'other')),
    mass_kg NUMERIC(8, 4) NOT NULL,
    source_integration TEXT,
    source_external_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_variant_packaging_variant_id
    ON variant_packaging(variant_id);
