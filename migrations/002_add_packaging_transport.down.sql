-- Rollback migration 002: Remove packaging tables and transport distance columns

DROP TABLE IF EXISTS variant_packaging;
DROP TABLE IF EXISTS product_packaging;

ALTER TABLE variant_journey_steps
    DROP COLUMN IF EXISTS transport_distance_km;

ALTER TABLE product_journey_steps
    DROP COLUMN IF EXISTS transport_distance_km;
