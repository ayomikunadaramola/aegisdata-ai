"""Integration checks for AegisData AI Gold customer segmentation."""

from pathlib import Path

import pytest
from pyspark.sql import SparkSession, functions as F

ROOT = Path(__file__).resolve().parents[1]
GOLD_ROOT = ROOT / "data" / "processed" / "gold"


@pytest.fixture(scope="module")
def spark():
    session = (SparkSession.builder.master("local[2]")
               .appName("AegisData-Customer-Gold-Tests")
               .config("spark.sql.shuffle.partitions", "4")
               .getOrCreate())
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def marts(spark):
    runs = sorted(GOLD_ROOT.glob("run_id=*"), reverse=True)
    for run in runs:
        if (run / "manifest.json").exists() and (run / "dim_customer_segments").is_dir() and (run / "fact_daily_sales").is_dir():
            return (spark.read.parquet(str(run / "dim_customer_segments")),
                    spark.read.parquet(str(run / "fact_daily_sales")))
    pytest.fail("No Gold run containing customer segments and daily sales")


def test_all_customers_present(marts):
    customers, _ = marts
    assert customers.count() == 5000
    assert customers.select("customer_id").distinct().count() == 5000


def test_paid_order_reconciliation(marts):
    customers, daily = marts
    orders = customers.agg(F.sum("paid_order_count")).first()[0]
    paid_orders = daily.agg(F.sum("paid_orders")).first()[0]
    assert orders == paid_orders


def test_paid_spend_reconciliation(marts):
    customers, daily = marts
    spend = customers.agg(F.sum("historical_paid_spend")).first()[0]
    sales = daily.agg(F.sum("paid_sales_amount")).first()[0]
    assert abs(spend - sales) <= 0.01


def test_no_purchase_customers_have_zero_spend(marts):
    customers, _ = marts
    invalid = customers.filter(
        (F.col("paid_order_count") == 0) &
        ((F.col("historical_paid_spend") != 0) | F.col("last_paid_order_at").isNotNull())
    )
    assert invalid.count() == 0
