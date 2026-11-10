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
REQUIRED_SILVER = ("orders", "payments")
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

            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
            GOLD_ROOT.mkdir(parents=True, exist_ok=True)
            staging = GOLD_ROOT / f".staging_{run_id}"
            final = GOLD_ROOT / f"run_id={run_id}"
            staging.mkdir()

            daily.coalesce(1).write.mode("errorifexists").parquet(str(staging / "fact_daily_sales"))
            manifest = {
                "run_id": run_id,
                "source_silver_run": silver_run.name,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "validated",
                "format": "parquet",
                "tables": {"fact_daily_sales": {"rows": days, "paid_orders": paid_order_count}},
                "currency": "NGN",
                "business_rule": "Completed orders with Successful matching payments only",
                "metric_note": "paid_sales_amount is a paid-sales KPI, not formal revenue recognition",
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            staging.rename(final)
            staging = None
            print(f"[VALIDATED] Paid orders: {paid_order_count:,}")
            print(f"[WRITTEN] fact_daily_sales: {days:,} dates")
            print(f"[TOTAL] Paid sales: NGN {row['total_sales']:,.2f}")
            print(f"[SUCCESS] Gold published: {final}")
        finally:
            daily.unpersist()
            paid_orders.unpersist()
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        spark.stop()


if __name__ == "__main__":
    main()
