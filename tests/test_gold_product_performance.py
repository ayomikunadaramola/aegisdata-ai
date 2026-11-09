"""Integration tests for AegisData AI's product-performance Gold mart."""

import json
from pathlib import Path

import pytest
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DecimalType

ROOT = Path(__file__).resolve().parents[1]
GOLD_ROOT = ROOT / "data" / "processed" / "gold"
SILVER_ROOT = ROOT / "data" / "processed" / "silver"


def latest_run(root, required):
    for run in sorted(root.glob("run_id=*"), key=lambda p: p.name, reverse=True):
        manifest_path = run / "manifest.json"
        if not manifest_path.is_file() or not all((run / x).is_dir() for x in required):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "validated":
            return run
    pytest.fail(f"No validated run with {required} found under {root}")


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[2]")
        .appName("AegisData-Gold-Product-Tests")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def tables(spark):
    gold_run = latest_run(GOLD_ROOT, ["fact_daily_sales", "fact_product_performance"])
    silver_run = latest_run(SILVER_ROOT, ["products"])
    product = spark.read.parquet(str(gold_run / "fact_product_performance")).cache()
    daily = spark.read.parquet(str(gold_run / "fact_daily_sales")).cache()
    source = spark.read.parquet(str(silver_run / "products")).cache()
    yield product, daily, source, gold_run
    product.unpersist()
    daily.unpersist()
    source.unpersist()


def test_product_counts_and_uniqueness(tables):
    products, _, source, _ = tables
    assert products.count() == source.count() == 500
    assert products.select("product_id").distinct().count() == products.count()


def test_product_schema(tables):
    products, _, _, _ = tables
    needed = {
        "product_id", "product_name", "category", "unit_price", "currency",
        "is_active", "paid_order_count", "units_sold", "paid_sales_amount",
        "average_paid_unit_price",
    }
    assert needed.issubset(set(products.columns))
    assert isinstance(products.schema["paid_sales_amount"].dataType, DecimalType)


def test_metrics_nonnegative(tables):
    products, _, _, _ = tables
    bad = products.filter(
        (F.col("paid_order_count") < 0)
        | (F.col("units_sold") < 0)
        | (F.col("paid_sales_amount") < 0)
        | ((F.col("paid_order_count") == 0) & (F.col("paid_sales_amount") != 0))
    )
    assert bad.count() == 0


def test_product_totals_reconcile_with_daily_sales(tables):
    products, daily, _, _ = tables
    p = products.agg(
        F.sum("paid_order_count").alias("orders"),
        F.sum("units_sold").alias("units"),
        F.sum("paid_sales_amount").alias("sales"),
    ).first()
    d = daily.agg(
        F.sum("paid_orders").alias("orders"),
        F.sum("units_sold").alias("units"),
        F.sum("paid_sales_amount").alias("sales"),
    ).first()
    assert p["orders"] == d["orders"]
    assert p["units"] == d["units"]
    assert abs(p["sales"] - d["sales"]) <= 0.01


def test_average_unit_price(tables):
    products, _, _, _ = tables
    bad = products.filter(
        ((F.col("units_sold") > 0) & (
            F.col("average_paid_unit_price").isNull()
            | (F.abs(
                F.col("average_paid_unit_price")
                - F.col("paid_sales_amount") / F.col("units_sold")
            ) > 0.01)
        ))
        | ((F.col("units_sold") == 0) & F.col("average_paid_unit_price").isNotNull())
    )
    assert bad.count() == 0


def test_manifest_contains_product_model(tables):
    _, _, _, run = tables
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["fact_product_performance"]["rows"] == 500
