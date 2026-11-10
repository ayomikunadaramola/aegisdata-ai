
"""
AegisData AI
Silver Layer - Bronze Data Inspection

Purpose:
    Load the latest complete Bronze batch,
    inspect schemas, count records, and display
    sample data using Apache Spark.
    """

from pathlib import Path
from pyspark.sql import SparkSession


PROJECT_ROOT = Path(__file__).resolve().parents[2]

BRONZE_DIR = (
    PROJECT_ROOT / "data" / "raw" / "bronze"
)

DATASETS = [
    "customers",
    "products",
    "orders",
    "payments",
    "inventory",
]


def get_latest_complete_batch():
    """
    Find the latest complete Bronze batch.

    Duplicate-only batches may contain only a manifest.
    These must be skipped during discovery.
    """

    print("\n[DEBUG] Project root:", PROJECT_ROOT)
    print("[DEBUG] Bronze directory:", BRONZE_DIR)

    if not BRONZE_DIR.exists():
        raise FileNotFoundError(
            f"Bronze directory does not exist: {BRONZE_DIR}"
        )

    batches = sorted(
        [
            path
            for path in BRONZE_DIR.iterdir()
            if path.is_dir()
            and path.name.startswith("batch_id=")
        ],
        key=lambda path: path.name,
        reverse=True,
    )

    print(f"[DEBUG] Found {len(batches)} batch directories")

    for batch in batches:
        print(f"\n[DEBUG] Checking batch: {batch.name}")

        existing_files = [
            name
            for name in DATASETS
            if (batch / f"{name}.csv").is_file()
        ]

        print("[DEBUG] Files found:", existing_files)

        if len(existing_files) == len(DATASETS):
            print("[SUCCESS] Complete Bronze batch found.")
            return batch

    # IMPORTANT:
    # This statement is OUTSIDE the for loop.
    # All batches must be checked before raising an error.
    raise FileNotFoundError(
        "No complete Bronze batch found. "
        "Check the batch directory contents."
    )


def main():
    spark = (
        SparkSession.builder
        .appName("AegisData-Silver-Inspection")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("ERROR")

    try:
        batch = get_latest_complete_batch()

        print("=" * 60)
        print("AEGISDATA AI - BRONZE DATA INSPECTION")
        print("=" * 60)
        print(f"Selected batch: {batch.name}")

        for dataset in DATASETS:
            file_path = batch / f"{dataset}.csv"

            df = (
                spark.read
                .option("header", True)
                .option("inferSchema", False)
                .csv(str(file_path))
            )

            print(f"\nDATASET: {dataset.upper()}")
            print("-" * 50)

            print(f"Record count: {df.count():,}")

            print("\nSchema:")
            df.printSchema()

            print("\nSample records:")
            df.show(5, truncate=False)

            print("\nInspection completed successfully.")

    finally:
        spark.stop()


if __name__ == "__main__":
    main()
