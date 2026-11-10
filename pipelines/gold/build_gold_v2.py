"""AegisData AI: build a versioned daily-sales Gold mart from Silver Parquet.

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
REQUIRED_SILVER = ("orders", "payments", "customers")
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


def main():
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("AegisData-Gold-Daily-Sales")
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

                run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
                GOLD_ROOT.mkdir(parents=True, exist_ok=True)
                staging = GOLD_ROOT / f".staging_{run_id}"
                final = GOLD_ROOT / f"run_id={run_id}"
                staging.mkdir()

                daily.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_daily_sales"))
                customer_segments.coalesce(1).write.mode("errorifexists").parquet(str(staging / "dim_customer_segments"))
                manifest = {
                    "run_id": run_id,
                    "source_silver_run": silver_run.name,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "validated",
                    "format": "parquet",
                    "tables": {
                        "fact_daily_sales": {"rows": days, "paid_orders": paid_order_count},
                        "dim_customer_segments": {"rows": customer_count, "paid_orders": paid_order_count},
                    },
                    "segmentation_as_of_date": str(as_of),
                    "segmentation_note": "High Value >= NGN 1m paid spend; else Repeat Buyer >= 3 paid orders; else Occasional Buyer; No Purchase for zero paid orders",
                    "currency": "NGN",
                    "business_rule": "Completed orders with Successful matching payments only",
                    "metric_note": "paid_sales_amount is a paid-sales KPI, not formal revenue recognition",
                }
                (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                staging.rename(final)
                staging = None
                print(f"[VALIDATED] Paid orders: {paid_order_count:,}")
                print(f"[WRITTEN] fact_daily_sales: {days:,} dates")
                print(f"[WRITTEN] dim_customer_segments: {customer_count:,} customers")
                print(f"[TOTAL] Paid sales: NGN {row['total_sales']:,.2f}")
                print(f"[SUCCESS] Gold published: {final}")
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
