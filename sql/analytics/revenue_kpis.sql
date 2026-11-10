-- Revenue and average order value
-- This calculates the total paid revenue, number of paid orders, units sold and average order value across the dataset.
-- From the previously validated Gold build, total paid revenue should reconcile to ₦6,473,621,049.24.
SELECT
    SUM(paid_sales_amount) AS total_revenue_ngn,
    SUM(paid_orders) AS total_paid_orders,
    SUM(units_sold) AS total_units_sold,
    ROUND(
        SUM(paid_sales_amount) /
        NULLIF(SUM(paid_orders), 0),
        2
    ) AS average_order_value_ngn
FROM aegisdata_gold_db.fact_daily_sales;
