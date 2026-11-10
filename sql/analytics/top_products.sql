-- Top 10 performing products
-- This identifies the products contributing the most revenue.

SELECT
    product_name,
    category,
    paid_order_count,
    units_sold,
    paid_sales_amount AS revenue_ngn
FROM aegisdata_gold_db.fact_product_performance
ORDER BY paid_sales_amount DESC
LIMIT 10;
