
"""Integration tests for AegisData AI Silver Parquet datasets."""

import json
from pathlib import Path

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BooleanType,
    DecimalType,
    IntegerType,
    TimestampType,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = PROJECT_ROOT / "data" / "processed" / "silver"

EXPECTED_COUNTS = {
    "customers": 5000,
    "products": 500,
    "orders": 20000,
    "payments": 20000,
    "inventory": 500,
}


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder
        .master("local[2]")
        .appName("AegisData-Silver-Tests")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )

    session.sparkContext.setLogLevel("ERROR")

    yield session
    session.stop()


@pytest.fixture(scope="module")
def silver_run():
    assert SILVER_DIR.is_dir(), (
        f"Silver output directory missing: {SILVER_DIR}"
    )

    for run in sorted(
        SILVER_DIR.glob("run_id=*"),
        reverse=True
    ):
        if (
            (run / "manifest.json").is_file()
            and all(
                (run / name).is_dir()
                for name in EXPECTED_COUNTS
            )
        ):
            return run

        pytest.fail("No complete Silver run was found.")


@pytest.mark.parametrize(
    "dataset,expected",
    EXPECTED_COUNTS.items()
)
def test_record_counts(spark, silver_run, dataset, expected):
    df = spark.read.parquet(str(silver_run / dataset))
    assert df.count() == expected


@pytest.mark.parametrize(
    "dataset,column,expected_type",
    [
        ("customers", "signup_date", TimestampType),
        ("products", "unit_price", DecimalType),
        ("products", "is_active", BooleanType),
        ("orders", "order_date", TimestampType),
        ("orders", "quantity", IntegerType),
        ("orders", "total_amount", DecimalType),
        ("payments", "payment_date", TimestampType),
        ("payments", "payment_amount", DecimalType),
        ("inventory", "stock_quantity", IntegerType),
        ("inventory", "last_updated", TimestampType),
    ],
)
def test_column_types(
    spark, silver_run, dataset, column, expected_type
):
    df = spark.read.parquet(str(silver_run / dataset))
    actual = df.schema[column].dataType

    assert isinstance(actual, expected_type), (
        f"{dataset}.{column}: unexpected type {actual}"
    )


def test_manifest_exists_and_is_valid(silver_run):
    path = silver_run / "manifest.json"

    with path.open(encoding="utf-8") as file:
        manifest = json.load(file)

        assert isinstance(manifest, dict)
        assert manifest


def test_unique_order_ids(spark, silver_run):
    df = spark.read.parquet(str(silver_run / "orders"))
    assert df.select("order_id").distinct().count() == df.count()


def test_nonnegative_inventory(spark, silver_run):
    df = spark.read.parquet(str(silver_run / "inventory"))
    assert df.filter("stock_quantity < 0").count() == 0
