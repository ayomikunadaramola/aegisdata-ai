-- Customer value segmentation
-- This helps identify which customer groups contribute most to revenue

SELECT
    customer_value_segment,
    COUNT(*) AS total_customers,
    SUM(paid_order_count) AS paid_orders,
    SUM(historical_paid_spend) AS revenue_ngn
FROM aegisdata_gold_db.dim_customer_segments
GROUP BY customer_value_segment
ORDER BY revenue_ngn DESC;
