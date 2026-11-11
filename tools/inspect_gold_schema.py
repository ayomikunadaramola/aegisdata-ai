
"""Inspect every model in the latest AegisData AI Gold run."""

from pathlib import Path
from pyspark.sql import SparkSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = PROJECT_ROOT / "data" / "processed" / "gold"

MODELS = [
    "fact_daily_sales",
    "dim_customer_segments",
    "fact_product_performance",
    "fact_payment_performance",
    "fact_inventory_health",
]


def main():
    runs = sorted(
        (p for p in GOLD_DIR.glob("run_id=*") if p.is_dir()),
        reverse=True,
    )

    if not runs:
        raise FileNotFoundError("No Gold runs found.")

    # Select the newest run containing all expected models.
    run = next(
        (
            p for p in runs
            if all(
                (p / model).is_dir()
                and list((p / model).glob("*.parquet"))
                for model in MODELS
            )
        ),
        None,
    )

    if run is None:
        raise FileNotFoundError(
            "No complete Gold run containing all five models."
        )

    print(f"\nSelected Gold run: {run.name}", flush=True)

    spark = (
        SparkSession.builder
        .master("local[2]")
        .appName("AegisData-Gold-Schema-Inspection")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    try:
        for model in MODELS:
            print(f"\n{'=' * 55}", flush=True)
            print(f"MODEL: {model}", flush=True)
            print(f"{'=' * 55}", flush=True)

            path = str(run / model)
            df = spark.read.parquet(path)

            df.printSchema()
            print(f"Record count: {df.count():,}", flush=True)
            print(f"[SUCCESS] Inspected {model}", flush=True)

            print("\n[SUCCESS] All five Gold models inspected.")

    finally:
        spark.stop()


if __name__ == "__main__":
    main()
