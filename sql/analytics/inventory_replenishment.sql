-- Inventory replenishment priorities 


SELECT
    product_id,
    product_name,
    stock_quantity,
    reorder_level,
    units_sold_90d,
    estimated_stock_coverage_days,
    suggested_reorder_units
FROM aegisdata_gold_db.fact_inventory_health
WHERE needs_reorder = TRUE
ORDER BY estimated_stock_coverage_days ASC NULLS LAST
LIMIT 20;
