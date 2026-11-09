
"""Integration tests for AegisData AI Bronze storage."""

import csv
import hashlib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "data" / "synthetic"
BRONZE_DIR = PROJECT_ROOT / "data" / "raw" / "bronze"
AUDIT_DB = PROJECT_ROOT / "data" / "raw" / "ingestion_audit.db"

DATASETS = [
    "customers.csv",
    "products.csv",
    "orders.csv",
    "payments.csv",
    "inventory.csv",
]


def sha256_file(path):
    """Calculate the SHA-256 hash of a file."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)

            return digest.hexdigest()


def get_complete_batch():
    """
    Find the latest Bronze batch containing all five datasets.

    Duplicate-only batches may contain a manifest without
    any CSV files. Those batches are intentionally ignored.
    """
    assert BRONZE_DIR.exists(), (
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

    if not batches:
        pytest.fail(
            "No Bronze batch directories were found."
        )

    for batch in batches:
        existing_files = [
            name
            for name in DATASETS
            if (batch / name).is_file()
        ]

        if len(existing_files) == len(DATASETS):
            return batch

    details = []

    for batch in batches:
        present = [
            name
            for name in DATASETS
            if (batch / name).exists()
        ]

        details.append(
            f"{batch.name}: {len(present)}/{len(DATASETS)} files"
        )

        pytest.fail(
            "No complete Bronze batch was found.\n"
            + "\n".join(details)
        )


def test_bronze_files_exist():
    """Confirm all datasets exist in a complete Bronze batch."""
    batch = get_complete_batch()

    for filename in DATASETS:
        assert (batch / filename).is_file()


def test_bronze_checksums():
    """Confirm Bronze files match their original source files."""
    batch = get_complete_batch()

    for filename in DATASETS:
        source = SOURCE_DIR / filename
        bronze = batch / filename

        assert sha256_file(source) == sha256_file(bronze)


def test_bronze_row_counts():
    """Confirm no records were lost during ingestion."""
    batch = get_complete_batch()

    for filename in DATASETS:
        source = SOURCE_DIR / filename
        bronze = batch / filename

        with source.open(encoding="utf-8", newline="") as f:
            source_count = sum(1 for _ in csv.DictReader(f))

            with bronze.open(encoding="utf-8", newline="") as f:
                bronze_count = sum(1 for _ in csv.DictReader(f))

                assert source_count == bronze_count


def test_manifest_exists():
    """Verify batch metadata was generated."""
    batch = get_complete_batch()
    assert (batch / "manifest.json").is_file()


def test_audit_database_exists():
    """Verify that an ingestion audit database was created."""
    assert AUDIT_DB.is_file()
    assert AUDIT_DB.stat().st_size > 0
