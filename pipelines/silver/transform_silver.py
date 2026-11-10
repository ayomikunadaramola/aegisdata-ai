"""AegisData AI: validate Bronze CSVs and publish versioned Silver Parquet.

Run from the project root:
    python pipelines\\silver\\transform_silver.py

All input columns are read as strings first; casts and business rules are
verified before any output is published. A run fails closed on invalid data.
"""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DecimalType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRONZE_ROOT = PROJECT_ROOT / "data" / "raw" / "bronze"
SILVER_ROOT = PROJECT_ROOT / "data" / "processed" / "silver"
DATASETS = ("customers", "products", "orders", "payments", "inventory")
DECIMAL = DecimalType(18, 2)


def latest_complete_batch():
    if not BRONZE_ROOT.is_dir():
        raise FileNotFoundError(f"Missing Bronze directory: {BRONZE_ROOT}")

    batches = sorted(
        (p for p in BRONZE_ROOT.glob("batch_id=*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for batch in batches:
        if all((batch / f"{name}.csv").is_file() for name in DATASETS):
            return batch
    raise FileNotFoundError("No complete Bronze batch with five CSV files")


def convert(dataset, df):
    """Apply explicit, stable data types to known numeric and date fields."""
    types = {
        "customers": {"signup_date": "timestamp"},
        "products": {"unit_price": DECIMAL, "is_active": "boolean"},
        "orders": {
            "order_date": "timestamp", "quantity": "int",
            "unit_price": DECIMAL, "total_amount": DECIMAL,
        },
        "payments": {"payment_date": "timestamp", "payment_amount": DECIMAL},
        "inventory": {
            "stock_quantity": "int", "reorder_level": "int",
            "last_updated": "timestamp",
        },
    }
    for name, dtype in types[dataset].items():
        df = df.withColumn(name, F.col(name).cast(dtype))
    return df


def assert_no_rows(df, condition, error):
    if df.filter(condition).limit(1).count():
        raise ValueError(error)


def validate(frames):
    keys = {
        "customers": "customer_id", "products": "product_id",
        "orders": "order_id", "payments": "payment_id",
        "inventory": "product_id",
    }
    counts = {}
    expected = {"customers": 5000, "products": 500,
                "orders": 20000, "payments": 20000, "inventory": 500}
    for name, df in frames.items():
        count = df.count()
        counts[name] = count
        if count != expected[name]:
            raise ValueError(
                f"{name}: expected {expected[name]:,} rows, got {count:,}")
        for field in df.columns:
            assert_no_rows(df, F.col(field).isNull(),
                           f"{name}: null/invalid {field}")
        if df.select(keys[name]).distinct().count() != count:
            raise ValueError(f"{name}: duplicate {keys[name]}")
        print(f"[VALIDATED] {name}: {count:,} rows")

    customer_ids = frames["customers"].select("customer_id")
    product_ids = frames["products"].select("product_id")
    order_ids = frames["orders"].select("order_id")

    for field, reference in (("customer_id", customer_ids), ("product_id", product_ids)):
        unmatched = frames["orders"].select(
            field).join(reference, field, "left_anti")
        if unmatched.limit(1).count():
            raise ValueError(f"orders: unknown {field}")
    if frames["payments"].select("order_id").join(order_ids, "order_id", "left_anti").limit(1).count():
        raise ValueError("payments: unknown order_id")
    if frames["inventory"].select("product_id").join(product_ids, "product_id", "left_anti").limit(1).count():
        raise ValueError("inventory: unknown product_id")
    if frames["payments"].select("order_id").distinct().count() != counts["payments"]:
        raise ValueError(
            "payments: multiple payments for one order in initial data contract")

    orders = frames["orders"]
    assert_no_rows(
        orders,
        (F.col("quantity") <= 0) | (F.col("unit_price") < 0)
        | (F.abs(F.col("total_amount") - F.col("quantity") * F.col("unit_price")) > F.lit(0.01)),
        "orders: invalid quantity, price, or total",
    )
    assert_no_rows(frames["products"], F.col("unit_price")
                   < 0, "products: negative unit_price")
    assert_no_rows(
        frames["inventory"],
        (F.col("stock_quantity") < 0) | (F.col("reorder_level") < 0),
        "inventory: negative stock or reorder level",
    )
    assert_no_rows(
        frames["orders"].join(frames["customers"].select(
            "customer_id", "signup_date"), "customer_id"),
        F.col("order_date") < F.col("signup_date"),
        "orders: order predates customer signup",
    )

    payment_check = frames["payments"].join(
        orders.select("order_id", "total_amount", "order_status"), "order_id"
    )
    assert_no_rows(
        payment_check,
        (F.abs(F.col("payment_amount") - F.col("total_amount")) > F.lit(0.01))
        | ((F.col("order_status") == "Completed") & (F.col("payment_status") != "Successful"))
        | ((F.col("order_status") == "Cancelled") & (F.col("payment_status") != "Failed"))
        | ((F.col("order_status") == "Pending") & (F.col("payment_status") != "Pending")),
        "payments: amount/status mismatch",
    )
    return counts


def main():
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("AegisData-Silver-Transform")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.ansi.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    staging = None
    try:
        batch = latest_complete_batch()
        print(f"[SOURCE] {batch.name}")
        frames = {}
        for name in DATASETS:
            raw = spark.read.option("header", "true").option("inferSchema", "false").csv(
                str(batch / f"{name}.csv")
            )
            frames[name] = convert(name, raw)
        counts = validate(frames)

        run_id = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
        SILVER_ROOT.mkdir(parents=True, exist_ok=True)
        staging = SILVER_ROOT / f".staging_{run_id}"
        final = SILVER_ROOT / f"run_id={run_id}"
        staging.mkdir()
        for name, df in frames.items():
            df.write.mode("errorifexists").parquet(str(staging / name))
            print(f"[WRITTEN] {name}: {counts[name]:,} rows")

        metadata = {
            "run_id": run_id,
            "source_batch": batch.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "format": "parquet",
            "status": "validated",
            "row_counts": counts,
        }
        (staging / "manifest.json").write_text(json.dumps(metadata,
                                                          indent=2), encoding="utf-8")
        # Local same-filesystem publication after all files succeed.
        staging.rename(final)
        staging = None
        print(f"[SUCCESS] Silver published: {final}")
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        spark.stop()


if __name__ == "__main__":
    main()
