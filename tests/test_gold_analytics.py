
"""Integration tests for AegisData AI daily-sales Gold mart."""

from pathlib import Path

import pytest
from pyspark.sql import SparkSession, functions as F


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

GOLD_COLUMNS = {
    "sales_date",
    "paid_orders",
    "units_sold",
    "paid_sales_amount",
    "average_order_value",
}


def latest_run(layer, datasets):
    directory = PROCESSED / layer

    for path in sorted(
        directory.glob("run_id=*"),
        reverse=True,
    ):
        if (
            (path / "manifest.json").is_file()
            and all((path / name).is_dir() for name in datasets)
        ):
            return path

        pytest.fail(f"No complete {layer} run found.")


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder
        .master("local[2]")
        .appName("AegisData-Gold-Tests")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )

    session.sparkContext.setLogLevel("ERROR")

    yield session
    session.stop()


@pytest.fixture(scope="module")
def datasets(spark):
    silver = latest_run("silver", ["orders", "payments"])
    gold = latest_run("gold", ["fact_daily_sales"])

    orders = spark.read.parquet(str(silver / "orders"))
    payments = spark.read.parquet(str(silver / "payments"))
    daily = spark.read.parquet(str(gold / "fact_daily_sales"))

    return orders, payments, daily


def paid_orders(orders, payments):
    successful_payments = payments.filter(
        F.col("payment_status") == "Successful"
    ).select("order_id")

    return orders.filter(
        F.col("order_status") == "Completed"
    ).join(successful_payments, "order_id", "inner")


def test_gold_schema(datasets):
    _, _, daily = datasets

    assert GOLD_COLUMNS.issubset(set(daily.columns))


def test_unique_sales_dates(datasets):
    _, _, daily = datasets

    assert daily.select("sales_date").distinct().count() == daily.count()


def test_positive_daily_metrics(datasets):
    _, _, daily = datasets

    invalid = daily.filter(
        (F.col("paid_orders") <= 0)
        | (F.col("units_sold") <= 0)
        | (F.col("paid_sales_amount") <= 0)
        | (F.col("average_order_value") <= 0)
    )

    assert invalid.count() == 0


def test_paid_order_reconciliation(datasets):
    orders, payments, daily = datasets

    expected = paid_orders(orders, payments).count()

    actual = daily.agg(
        F.sum("paid_orders").alias("total")
    ).first()["total"]

    assert actual == expected


def test_paid_sales_reconciliation(datasets):
    orders, payments, daily = datasets

    expected = paid_orders(orders, payments).agg(
        F.sum("total_amount").alias("total")
    ).first()["total"]

    actual = daily.agg(
        F.sum("paid_sales_amount").alias("total")
    ).first()["total"]

    assert abs(float(actual) - float(expected)) < 0.01


def test_daily_average_order_value(datasets):
    _, _, daily = datasets

    invalid = daily.filter(
        F.abs(
            F.col("average_order_value")
            - F.col("paid_sales_amount") / F.col("paid_orders")
        ) > 0.01
    )

    assert invalid.count() == 0
