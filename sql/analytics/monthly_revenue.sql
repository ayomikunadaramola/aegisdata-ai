-- Monthly revenue trends
-- This shows the evolution of sales over time and will later support a monthly revenue chart.

SELECT
    DATE_TRUNC('month', sales_date) AS sales_month,
    SUM(paid_orders) AS paid_orders,
    SUM(units_sold) AS units_sold,
    SUM(paid_sales_amount) AS revenue_ngn
FROM aegisdata_gold_db.fact_daily_sales
GROUP BY 1
ORDER BY sales_month;
