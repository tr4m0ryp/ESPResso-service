-- Enable RLS on product_carbon_predictions and add policies
-- matching the existing product_environment pattern.

ALTER TABLE product_carbon_predictions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "carbon_pred_select" ON product_carbon_predictions FOR SELECT
  TO authenticated, service_role
  USING (EXISTS (
    SELECT 1 FROM products
    WHERE products.id = product_carbon_predictions.product_id
    AND is_brand_member(products.brand_id)
  ));

CREATE POLICY "carbon_pred_insert" ON product_carbon_predictions FOR INSERT
  TO authenticated, service_role
  WITH CHECK (EXISTS (
    SELECT 1 FROM products
    WHERE products.id = product_carbon_predictions.product_id
    AND is_brand_member(products.brand_id)
  ));

CREATE POLICY "carbon_pred_update" ON product_carbon_predictions FOR UPDATE
  TO authenticated, service_role
  USING (EXISTS (
    SELECT 1 FROM products
    WHERE products.id = product_carbon_predictions.product_id
    AND is_brand_member(products.brand_id)
  ));

CREATE POLICY "carbon_pred_delete" ON product_carbon_predictions FOR DELETE
  TO authenticated, service_role
  USING (EXISTS (
    SELECT 1 FROM products
    WHERE products.id = product_carbon_predictions.product_id
    AND is_brand_member(products.brand_id)
  ));
