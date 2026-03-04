-- Rollback: remove RLS policies from product_carbon_predictions

DROP POLICY IF EXISTS "carbon_pred_delete" ON product_carbon_predictions;
DROP POLICY IF EXISTS "carbon_pred_update" ON product_carbon_predictions;
DROP POLICY IF EXISTS "carbon_pred_insert" ON product_carbon_predictions;
DROP POLICY IF EXISTS "carbon_pred_select" ON product_carbon_predictions;

ALTER TABLE product_carbon_predictions DISABLE ROW LEVEL SECURITY;
