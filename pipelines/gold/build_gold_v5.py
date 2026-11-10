"""AegisData AI: build versioned sales, customer, product, payment and inventory Gold marts from Silver Parquet.

Run from the repository root:
    python pipelines\\gold\\build_gold.py

Only completed orders with a successful, matching payment are included.
The reported amount is paid sales, not accounting-standard revenue recognition.
"""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DecimalType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SILVER_ROOT = PROJECT_ROOT / "data" / "processed" / "silver"
GOLD_ROOT = PROJECT_ROOT / "data" / "processed" / "gold"
REQUIRED_SILVER = ("orders", "payments", "customers", "products", "inventory")
MONEY = DecimalType(18, 2)


def find_latest_silver_run():
    """Only select a published Silver run containing the required inputs."""
    if not SILVER_ROOT.is_dir():
        raise FileNotFoundError(f"Silver directory not found: {SILVER_ROOT}")

    runs = sorted(
        (p for p in SILVER_ROOT.glob("run_id=*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for run in runs:
        manifest_path = run / "manifest.json"
        if not manifest_path.is_file() or not all((run / x).is_dir() for x in REQUIRED_SILVER):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "validated":
            return run
    raise FileNotFoundError("No validated Silver run containing orders and payments was found")


def build_daily_sales(orders, payments):
    """Aggregate paid completed orders by UTC calendar date."""
    if orders.filter(F.col("order_id").isNull()).limit(1).count():
        raise ValueError("orders contains null order_id")
    if orders.groupBy("order_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("orders contains duplicate order_id")
    if payments.groupBy("order_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("payments contains multiple records for the same order")

    valid_payments = payments.filter(F.col("payment_status") == "Successful").select(
        "order_id", "payment_amount"
    )
    paid_orders = (
        orders.filter(F.col("order_status") == "Completed")
        .join(valid_payments, "order_id", "inner")
    )

    # Reject inconsistent paid amounts instead of quietly skewing business KPIs.
    if paid_orders.filter(
        F.col("total_amount").isNull()
        | F.col("payment_amount").isNull()
        | (F.col("quantity") <= 0)
        | (F.abs(F.col("total_amount") - F.col("payment_amount")) > F.lit(0.01))
    ).limit(1).count():
        raise ValueError("Invalid paid order: missing amount, invalid quantity, or payment mismatch")

    daily = (
        paid_orders.withColumn("sales_date", F.to_date("order_date"))
        .groupBy("sales_date")
        .agg(
            F.count("order_id").alias("paid_orders"),
            F.sum("quantity").cast("long").alias("units_sold"),
            F.sum("payment_amount").cast(MONEY).alias("paid_sales_amount"),
        )
        .withColumn(
            "average_order_value",
            (F.col("paid_sales_amount") / F.col("paid_orders")).cast(MONEY),
        )
        .orderBy("sales_date")
    )
    if daily.filter(F.col("sales_date").isNull()).limit(1).count():
        raise ValueError("Found orders with invalid sales_date")
    return daily, paid_orders


def build_customer_segments(customers, paid_orders, as_of_date):
    """Create one row per customer, including customers with no paid orders.

    Spending is historical paid sales, NOT predicted lifetime value.
    Recency is relative to the latest order date within the source snapshot.
    """
    from pyspark.sql.types import IntegerType

    if customers.filter(F.col("customer_id").isNull()).limit(1).count():
        raise ValueError("customers has null customer_id")
    if customers.groupBy("customer_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("customers has duplicate customer_id")

    activity = paid_orders.groupBy("customer_id").agg(
        F.count("order_id").alias("paid_order_count"),
        F.sum("quantity").cast("long").alias("units_purchased"),
        F.sum("payment_amount").cast(MONEY).alias("historical_paid_spend"),
        F.min("order_date").alias("first_paid_order_at"),
        F.max("order_date").alias("last_paid_order_at"),
    )
    result = (
        customers.select("customer_id", "customer_name", "city", "customer_segment", "signup_date")
        .join(activity, "customer_id", "left")
        .fillna({"paid_order_count": 0, "units_purchased": 0})
        .withColumn("historical_paid_spend", F.coalesce(F.col("historical_paid_spend"), F.lit(0).cast(MONEY)))
        .withColumn("recency_days", F.when(
            F.col("last_paid_order_at").isNotNull(),
            F.datediff(F.lit(as_of_date), F.to_date("last_paid_order_at"))
        ).cast(IntegerType()))
        .withColumn("customer_value_segment", F.when(F.col("paid_order_count") == 0, "No Purchase")
                    .when(F.col("historical_paid_spend") >= F.lit(1_000_000), "High Value")
                    .when(F.col("paid_order_count") >= 3, "Repeat Buyer")
                    .otherwise("Occasional Buyer"))
    )
    return result


def build_product_performance(products, paid_orders):
    """One row per catalog product, including products with zero paid sales."""
    if products.filter(F.col("product_id").isNull()).limit(1).count():
        raise ValueError("products contains null product_id")
    if products.groupBy("product_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("products contains duplicate product_id")

    # Reject sales that cannot be assigned to a valid product.
    unknown = paid_orders.select("product_id").join(
        products.select("product_id"), "product_id", "left_anti"
    )
    if unknown.limit(1).count():
        raise ValueError("Paid orders contain unknown product IDs")

    sales = paid_orders.groupBy("product_id").agg(
        F.count("order_id").alias("paid_order_count"),
        F.sum("quantity").cast("long").alias("units_sold"),
        F.sum("payment_amount").cast(MONEY).alias("paid_sales_amount"),
    )

    result = (
        products.select("product_id", "product_name", "category", "unit_price", "currency", "is_active")
        .join(sales, "product_id", "left")
        .fillna({"paid_order_count": 0, "units_sold": 0})
        .withColumn("paid_sales_amount", F.coalesce(F.col("paid_sales_amount"), F.lit(0).cast(MONEY)))
        .withColumn(
            "average_paid_unit_price",
            F.when(F.col("units_sold") > 0,
                   (F.col("paid_sales_amount") / F.col("units_sold")).cast(MONEY))
             .otherwise(F.lit(None).cast(MONEY)),
        )
    )
    return result


def build_payment_performance(payments):
    """Aggregate every payment by UTC date, method, and status.

    Payment amount is an attempted transaction amount for failed/pending rows;
    it must not be interpreted as recognized revenue or settled cash.
    """
    invalid = payments.filter(
        F.col("payment_id").isNull()
        | F.col("order_id").isNull()
        | F.col("payment_date").isNull()
        | F.col("payment_method").isNull()
        | F.col("payment_status").isNull()
        | F.col("payment_amount").isNull()
        | (F.col("payment_amount") < 0)
        | (~F.col("payment_status").isin("Successful", "Failed", "Pending"))
    )
    if invalid.limit(1).count():
        raise ValueError("Payments contain invalid IDs, dates, amounts, methods or statuses")
    if payments.groupBy("payment_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("Duplicate payment_id in Silver payments")

    payment_details = payments.withColumn("payment_date_utc", F.to_date("payment_date"))
    result = (
        payment_details.groupBy("payment_date_utc", "payment_method")
        .agg(
            F.count("payment_id").alias("total_transactions"),
            F.sum(F.when(F.col("payment_status") == "Successful", 1).otherwise(0)).cast("long").alias("successful_transactions"),
            F.sum(F.when(F.col("payment_status") == "Failed", 1).otherwise(0)).cast("long").alias("failed_transactions"),
            F.sum(F.when(F.col("payment_status") == "Pending", 1).otherwise(0)).cast("long").alias("pending_transactions"),
            F.sum("payment_amount").cast(MONEY).alias("total_attempted_amount"),
            F.sum(F.when(F.col("payment_status") == "Successful", F.col("payment_amount"))
                  .otherwise(F.lit(0).cast(MONEY))).cast(MONEY).alias("successful_payment_amount"),
            F.sum(F.when(F.col("payment_status") == "Failed", F.col("payment_amount"))
                  .otherwise(F.lit(0).cast(MONEY))).cast(MONEY).alias("failed_payment_amount"),
            F.sum(F.when(F.col("payment_status") == "Pending", F.col("payment_amount"))
                  .otherwise(F.lit(0).cast(MONEY))).cast(MONEY).alias("pending_payment_amount"),
        )
        .withColumn(
            "payment_success_rate_pct",
            F.round(F.col("successful_transactions") * F.lit(100.0) / F.col("total_transactions"), 2),
        )
        .orderBy("payment_date_utc", "payment_method")
    )
    return result



def build_inventory_health(inventory, products, paid_orders):
    """Build inventory health from a *snapshot* and trailing 90-day paid sales.

    Coverage is a descriptive estimate using 90 calendar days, not a forecast.
    Products with no sales in this interval have NULL coverage (undefined demand).
    """
    if inventory.filter(
        F.col("product_id").isNull()
        | F.col("last_updated").isNull()
        | F.col("stock_quantity").isNull()
        | F.col("reorder_level").isNull()
        | (F.col("stock_quantity") < 0)
        | (F.col("reorder_level") < 0)
    ).limit(1).count():
        raise ValueError("Inventory contains invalid product, snapshot date, stock or reorder level")
    if inventory.groupBy("product_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("Inventory contains duplicate product IDs; expected one snapshot per product")
    unknown = inventory.select("product_id").join(
        products.select("product_id"), "product_id", "left_anti"
    )
    if unknown.limit(1).count():
        raise ValueError("Inventory contains products missing from the catalog")
    # This first implementation expects one coherent, point-in-time snapshot.
    snapshot_values = inventory.select(F.to_date("last_updated").alias("snapshot_date")).distinct().collect()
    if len(snapshot_values) != 1 or snapshot_values[0]["snapshot_date"] is None:
        raise ValueError("Inventory records must share exactly one non-null UTC snapshot date")
    snapshot_date = snapshot_values[0]["snapshot_date"]
    expected_status = F.when(
        F.col("stock_quantity") <= F.col("reorder_level"), "Low Stock"
    ).otherwise("In Stock")
    if inventory.filter(
        F.col("inventory_status").isNull()
        | (F.col("inventory_status") != expected_status)
    ).limit(1).count():
        raise ValueError("Inventory status is inconsistent with stock and reorder levels")

    recent_paid = paid_orders.filter(
        F.to_date("order_date").between(
            F.date_sub(F.lit(snapshot_date), 89), F.lit(snapshot_date)
        )
    )
    recent = recent_paid.groupBy("product_id").agg(
        F.count("order_id").cast("long").alias("paid_orders_90d"),
        F.sum("quantity").cast("long").alias("units_sold_90d"),
    )
    enriched = (
        products.select("product_id", "product_name", "category", "currency", "is_active")
        .join(
            inventory.select("product_id", "stock_quantity", "reorder_level",
                             "warehouse_location", "inventory_status", "last_updated"),
            "product_id", "left"
        )
        .join(recent, "product_id", "left")
        .fillna({"paid_orders_90d": 0, "units_sold_90d": 0})
    )
    # Reconciliation: one stock snapshot is required for every catalog product.
    if enriched.filter(F.col("stock_quantity").isNull()).limit(1).count():
        raise ValueError("A catalog product is missing its inventory snapshot")
    result = (
        enriched
        .withColumn("snapshot_date", F.lit(snapshot_date).cast("date"))
        .withColumn("is_low_stock", F.col("stock_quantity") <= F.col("reorder_level"))
        .withColumn("is_out_of_stock", F.col("stock_quantity") == 0)
        .withColumn("needs_reorder", F.col("stock_quantity") <= F.col("reorder_level"))
        .withColumn("avg_daily_units_90d", F.round(F.col("units_sold_90d") / F.lit(90.0), 4))
        .withColumn(
            "estimated_stock_coverage_days",
            F.when(F.col("units_sold_90d") > 0,
                   F.round(F.col("stock_quantity") * F.lit(90.0) /
                           F.col("units_sold_90d"), 2))
             .otherwise(F.lit(None).cast("double")),
        )
        .withColumn(
            "suggested_reorder_units",
            F.when(F.col("needs_reorder"),
                   F.greatest(F.col("reorder_level") * F.lit(2) - F.col("stock_quantity"), F.lit(0)))
             .otherwise(F.lit(0)).cast("integer"),
        )
    )
    return result


def main():
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("AegisData-Gold-Analytics")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    staging = None
    try:
        silver_run = find_latest_silver_run()
        print(f"[SOURCE] {silver_run.name}")
        orders = spark.read.parquet(str(silver_run / "orders"))
        payments = spark.read.parquet(str(silver_run / "payments"))
        customers = spark.read.parquet(str(silver_run / "customers"))
        products = spark.read.parquet(str(silver_run / "products"))
        inventory = spark.read.parquet(str(silver_run / "inventory"))

        daily, paid_orders = build_daily_sales(orders, payments)
        daily = daily.cache()
        paid_orders = paid_orders.cache()
        try:
            paid_order_count = paid_orders.count()
            daily_order_count = daily.agg(F.sum("paid_orders")).first()[0] or 0
            if paid_order_count != daily_order_count:
                raise ValueError("Daily aggregation order-count reconciliation failed")

            row = daily.agg(
                F.count("sales_date").alias("days"),
                F.sum("paid_sales_amount").alias("total_sales"),
            ).first()
            days = row["days"]
            if days == 0:
                raise ValueError("No successfully paid completed orders found")

            as_of = orders.agg(F.max(F.to_date("order_date"))).first()[0]
            if as_of is None:
                raise ValueError("Source orders have no valid date")
            customer_segments = build_customer_segments(customers, paid_orders, as_of).cache()
            try:
                customer_count = customers.count()
                if customer_segments.count() != customer_count:
                    raise ValueError("Customer segmentation count does not match customers")
                segment_totals = customer_segments.agg(
                    F.sum("paid_order_count").alias("orders"),
                    F.sum("historical_paid_spend").alias("spend"),
                ).first()
                if segment_totals["orders"] != paid_order_count:
                    raise ValueError("Customer segmentation order-count reconciliation failed")
                if abs(segment_totals["spend"] - row["total_sales"]) > 0.01:
                    raise ValueError("Customer segmentation spend reconciliation failed")

                product_performance = build_product_performance(products, paid_orders).cache()
                try:
                    product_count = products.count()
                    if product_performance.count() != product_count:
                        raise ValueError("Product performance row count does not match product catalog")
                    product_totals = product_performance.agg(
                        F.sum("paid_order_count").alias("orders"),
                        F.sum("units_sold").alias("units"),
                        F.sum("paid_sales_amount").alias("sales"),
                    ).first()
                    daily_totals = daily.agg(
                        F.sum("paid_orders").alias("orders"),
                        F.sum("units_sold").alias("units"),
                        F.sum("paid_sales_amount").alias("sales"),
                    ).first()
                    if product_totals["orders"] != daily_totals["orders"]:
                        raise ValueError("Product order count does not reconcile to daily sales")
                    if product_totals["units"] != daily_totals["units"]:
                        raise ValueError("Product units sold do not reconcile to daily sales")
                    if abs(product_totals["sales"] - daily_totals["sales"]) > 0.01:
                        raise ValueError("Product paid sales do not reconcile to daily sales")

                    payment_performance = build_payment_performance(payments).cache()
                    payment_row = payment_performance.agg(
                        F.count("*").alias("groups"),
                        F.sum("total_transactions").alias("transactions"),
                        F.sum("successful_transactions").alias("successful"),
                        F.sum("failed_transactions").alias("failed"),
                        F.sum("pending_transactions").alias("pending"),
                        F.sum("total_attempted_amount").alias("amount"),
                        F.sum("successful_payment_amount").alias("successful_amount"),
                    ).first()
                    source_payment_row = payments.agg(
                        F.count("*").alias("transactions"),
                        F.sum(F.when(F.col("payment_status") == "Successful", 1).otherwise(0)).alias("successful"),
                        F.sum(F.when(F.col("payment_status") == "Failed", 1).otherwise(0)).alias("failed"),
                        F.sum(F.when(F.col("payment_status") == "Pending", 1).otherwise(0)).alias("pending"),
                        F.sum("payment_amount").alias("amount"),
                    ).first()
                    try:
                        for metric in ("transactions", "successful", "failed", "pending"):
                            if payment_row[metric] != source_payment_row[metric]:
                                raise ValueError(f"Payment performance {metric} reconciliation failed")
                        if payment_row["transactions"] != (payment_row["successful"] + payment_row["failed"] + payment_row["pending"]):
                            raise ValueError("Payment status counts do not sum to transactions")
                        if abs(payment_row["amount"] - source_payment_row["amount"]) > 0.01:
                            raise ValueError("Payment amount reconciliation failed")
                        if abs(payment_row["successful_amount"] - daily_totals["sales"]) > 0.01:
                            raise ValueError("Successful payments do not reconcile to daily paid sales")
                    finally:
                        payment_performance.unpersist()

                    inventory_health = build_inventory_health(inventory, products, paid_orders).cache()
                    try:
                        inventory_count = inventory.count()
                        inventory_summary = inventory_health.agg(
                            F.count("*").alias("rows"),
                            F.sum(F.col("is_low_stock").cast("long")).alias("low_stock"),
                            F.sum(F.col("is_out_of_stock").cast("long")).alias("out_of_stock"),
                            F.sum("stock_quantity").alias("stock_units"),
                            F.sum("units_sold_90d").alias("units_sold_90d"),
                        ).first()
                        if inventory_summary["rows"] != product_count or inventory_summary["rows"] != inventory_count:
                            raise ValueError("Inventory mart must contain one record per product and inventory row")
                        source_stock = inventory.agg(F.sum("stock_quantity")).first()[0]
                        if inventory_summary["stock_units"] != source_stock:
                            raise ValueError("Inventory stock quantities do not reconcile to Silver")
                        snapshot_date = inventory_health.select("snapshot_date").first()[0]
                        expected_recent_units = paid_orders.filter(
                            F.to_date("order_date").between(F.date_sub(F.lit(snapshot_date), 89), F.lit(snapshot_date))
                        ).agg(F.sum("quantity")).first()[0] or 0
                        if inventory_summary["units_sold_90d"] != expected_recent_units:
                            raise ValueError("Trailing 90-day units do not reconcile to paid orders")
                    finally:
                        inventory_health.unpersist()

                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
                    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
                    staging = GOLD_ROOT / f".staging_{run_id}"
                    final = GOLD_ROOT / f"run_id={run_id}"
                    staging.mkdir()

                    daily.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_daily_sales"))
                    customer_segments.coalesce(1).write.mode("errorifexists").parquet(str(staging / "dim_customer_segments"))
                    product_performance.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_product_performance"))
                    payment_performance.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_payment_performance"))
                    inventory_health.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_inventory_health"))
                    manifest = {
                        "run_id": run_id,
                        "source_silver_run": silver_run.name,
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "status": "validated",
                        "format": "parquet",
                        "tables": {
                            "fact_daily_sales": {"rows": days, "paid_orders": paid_order_count},
                            "dim_customer_segments": {"rows": customer_count, "paid_orders": paid_order_count},
                            "fact_product_performance": {"rows": product_count, "paid_orders": paid_order_count},
                            "fact_payment_performance": {"rows": payment_row["groups"], "transactions": payment_row["transactions"], "successful": payment_row["successful"], "failed": payment_row["failed"], "pending": payment_row["pending"]},
                            "fact_inventory_health": {"rows": inventory_summary["rows"], "low_stock": inventory_summary["low_stock"], "out_of_stock": inventory_summary["out_of_stock"], "stock_units": inventory_summary["stock_units"], "snapshot_date": str(snapshot_date), "trailing_sales_days": 90},
                        },
                        "segmentation_as_of_date": str(as_of),
                        "segmentation_note": "High Value >= NGN 1m paid spend; else Repeat Buyer >= 3 paid orders; else Occasional Buyer; No Purchase for zero paid orders",
                        "currency": "NGN",
                        "business_rule": "Completed orders with Successful matching payments only",
                        "metric_note": "paid_sales_amount is a paid-sales KPI, not formal revenue recognition",
                        "payment_metric_note": "Failed and pending payment amounts represent attempted transactions, not settled revenue; success rate uses all payment statuses",
                        "inventory_metric_note": "Point-in-time snapshot; 90-day sales pace assumes 90 calendar days. Coverage is indicative, not a demand forecast. Null coverage means no paid units in window.",
                    }
                    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                    staging.rename(final)
                    staging = None
                    print(f"[VALIDATED] Paid orders: {paid_order_count:,}")
                    print(f"[WRITTEN] fact_daily_sales: {days:,} dates")
                    print(f"[WRITTEN] dim_customer_segments: {customer_count:,} customers")
                    print(f"[WRITTEN] fact_product_performance: {product_count:,} products")
                    print(f"[WRITTEN] fact_payment_performance: {payment_row['groups']:,} date-method groups")
                    print(f"[WRITTEN] fact_inventory_health: {inventory_summary['rows']:,} products")
                    print(f"[INVENTORY] {inventory_summary['low_stock']:,} low-stock | {inventory_summary['out_of_stock']:,} out-of-stock | {inventory_summary['stock_units']:,} units on hand")
                    print(f"[PAYMENTS] {payment_row['transactions']:,} transactions | {payment_row['successful']:,} successful | {payment_row['failed']:,} failed | {payment_row['pending']:,} pending")
                    print(f"[TOTAL] Paid sales: NGN {row['total_sales']:,.2f}")
                    print(f"[SUCCESS] Gold published: {final}")
                finally:
                    product_performance.unpersist()
            finally:
                customer_segments.unpersist()
        finally:
            daily.unpersist()
            paid_orders.unpersist()
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        spark.stop()


if __name__ == "__main__":
    main()
