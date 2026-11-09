"""AegisData AI Gold v5 integration tests for inventory health."""

import json
from pathlib import Path

import pytest
from pyspark.sql import SparkSession, functions as F

ROOT = Path(__file__).resolve().parents[1] / "data" / "processed"


def latest_run(layer, tables):
    for folder in sorted((ROOT / layer).glob("run_id=*"), reverse=True):
        if (folder / "manifest.json").is_file() and all(
            (folder / name).is_dir() for name in tables
        ):
            return folder
    pytest.fail(f"No complete {layer} run containing {tables}")


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[2]")
        .appName("AegisData-Gold-Inventory-Tests")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def tables(spark):
    silver = latest_run("silver", ["inventory", "products", "orders", "payments"])
    gold = latest_run("gold", ["fact_inventory_health", "fact_daily_sales"])
    inventory = spark.read.parquet(str(silver / "inventory")).cache()
    products = spark.read.parquet(str(silver / "products")).cache()
    orders = spark.read.parquet(str(silver / "orders")).cache()
    payments = spark.read.parquet(str(silver / "payments")).cache()
    result = spark.read.parquet(str(gold / "fact_inventory_health")).cache()
    yield inventory, products, orders, payments, result, gold
    for df in (inventory, products, orders, payments, result):
        df.unpersist()


def test_expected_products_and_unique_ids(tables):
    inventory, products, _, _, result, _ = tables
    assert result.count() == 500
    assert result.count() == inventory.count() == products.count()
    assert result.select("product_id").distinct().count() == result.count()
    assert result.select("product_id").join(products.select("product_id"), "product_id", "left_anti").count() == 0


def test_schema_and_snapshot_date(tables):
    _, _, _, _, result, _ = tables
    columns = {
        "product_id", "product_name", "category", "currency",
        "stock_quantity", "reorder_level", "warehouse_location", "inventory_status",
        "snapshot_date", "paid_orders_90d", "units_sold_90d", "avg_daily_units_90d",
        "estimated_stock_coverage_days", "is_low_stock", "is_out_of_stock",
        "needs_reorder", "suggested_reorder_units",
    }
    assert columns.issubset(result.columns)
    assert result.filter("snapshot_date IS NULL").count() == 0
    assert result.select("snapshot_date").distinct().count() == 1


def test_stock_reconciles_to_silver(tables):
    inventory, _, _, _, result, _ = tables
    source = inventory.agg(F.sum("stock_quantity")).first()[0]
    output = result.agg(F.sum("stock_quantity")).first()[0]
    assert source == output
    mismatches = result.select("product_id", "stock_quantity", "reorder_level").join(
        inventory.select("product_id", F.col("stock_quantity").alias("src_stock"),
                         F.col("reorder_level").alias("src_reorder")),
        "product_id", "inner",
    ).filter("stock_quantity != src_stock OR reorder_level != src_reorder")
    assert mismatches.count() == 0


def test_low_and_out_of_stock_flags(tables):
    _, _, _, _, result, _ = tables
    invalid = result.filter(
        (F.col("stock_quantity") < 0)
        | (F.col("reorder_level") < 0)
        | (F.col("is_low_stock") != (F.col("stock_quantity") <= F.col("reorder_level")))
        | (F.col("is_out_of_stock") != (F.col("stock_quantity") == 0))
        | (F.col("needs_reorder") != F.col("is_low_stock"))
    )
    assert invalid.count() == 0


def test_reorder_quantities(tables):
    _, _, _, _, result, _ = tables
    expected = F.when(
        F.col("is_low_stock"),
        F.greatest(F.col("reorder_level") * 2 - F.col("stock_quantity"), F.lit(0)),
    ).otherwise(F.lit(0))
    assert result.filter(F.col("suggested_reorder_units") != expected).count() == 0


def test_stock_coverage_and_no_sales(tables):
    _, _, _, _, result, _ = tables
    assert result.filter("units_sold_90d < 0 OR paid_orders_90d < 0").count() == 0
    assert result.filter("units_sold_90d = 0 AND estimated_stock_coverage_days IS NOT NULL").count() == 0
    expected = F.round(F.col("stock_quantity") * 90.0 / F.col("units_sold_90d"), 2)
    assert result.filter(
        (F.col("units_sold_90d") > 0)
        & (F.abs(F.col("estimated_stock_coverage_days") - expected) > 0.011)
    ).count() == 0


def test_90_day_units_reconcile(tables):
    _, _, orders, payments, result, _ = tables
    snapshot_date = result.select("snapshot_date").first()[0]
    successful = payments.filter(F.col("payment_status") == "Successful").select("order_id")
    paid = orders.filter(F.col("order_status") == "Completed").join(successful, "order_id", "inner")
    expected = paid.filter(F.to_date("order_date").between(
        F.date_sub(F.lit(snapshot_date), 89), F.lit(snapshot_date)
    )).agg(F.sum("quantity")).first()[0] or 0
    actual = result.agg(F.sum("units_sold_90d")).first()[0] or 0
    assert actual == expected


def test_inventory_manifest(tables):
    _, _, _, _, result, gold = tables
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("status") == "validated"
    info = manifest["tables"]["fact_inventory_health"]
    assert info["rows"] == result.count()
    assert info["trailing_sales_days"] == 90
    assert info["stock_units"] == result.agg(F.sum("stock_quantity")).first()[0]
