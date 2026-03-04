-- Migration 001: Add carbon component breakdown storage
--
-- The existing product_environment table stores a single carbon_kg_co2e value.
-- This migration adds a companion table for the component-level breakdown
-- (raw_materials, transport, processing, packaging) and prediction metadata
-- returned by the ESPResso service.
--
-- This is a NEW table that does not modify any existing Avelero tables,
-- so it is safe to apply without affecting current functionality.

CREATE TABLE IF NOT EXISTS product_carbon_predictions (
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE ON UPDATE CASCADE,
    carbon_kg_co2e NUMERIC(12, 4),
    cf_raw_materials_kg_co2e NUMERIC(12, 4),
    cf_transport_kg_co2e NUMERIC(12, 4),
    cf_processing_kg_co2e NUMERIC(12, 4),
    cf_packaging_kg_co2e NUMERIC(12, 4),
    confidence NUMERIC(5, 4),
    model_used TEXT,
    is_rare BOOLEAN DEFAULT FALSE,
    warnings TEXT[],
    predicted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id)
);

COMMENT ON TABLE product_carbon_predictions IS
    'ESPResso carbon footprint prediction results with component breakdown. '
    'One row per product. Re-predicted products overwrite previous results.';

CREATE INDEX IF NOT EXISTS idx_carbon_predictions_predicted_at
    ON product_carbon_predictions(predicted_at DESC);

CREATE INDEX IF NOT EXISTS idx_carbon_predictions_confidence
    ON product_carbon_predictions(confidence);
