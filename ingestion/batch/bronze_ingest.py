"""Local immutable Bronze ingestion for AegisData AI."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATASETS = ("customers", "products", "orders", "payments", "inventory")
ROOT = Path(__file__).resolve().parents[2]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_records(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        if not next(reader, None):
            raise ValueError(f"Empty CSV: {path}")
        return sum(1 for row in reader if row)


def connect_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_audit (
            dataset TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            bronze_file TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            ingested_at TEXT NOT NULL,
            PRIMARY KEY (dataset, source_sha256)
        )
    """)
    conn.commit()
    return conn


def ingest(source_dir, bronze_dir, db_path):
    source_dir = Path(source_dir)
    bronze_dir = Path(bronze_dir)
    files = [source_dir / f"{dataset}.csv" for dataset in DATASETS]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing source CSVs: " + ", ".join(missing))

    batch_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:12]}"
    batch_dir = bronze_dir / f"batch_id={batch_id}"
    manifest = {"batch_id": batch_id, "started_at": utc_now(), "files": []}
    conn = connect_db(Path(db_path))
    try:
        for file in files:
            digest = sha256(file)
            row = conn.execute(
                "SELECT bronze_file FROM ingestion_audit WHERE dataset=? AND source_sha256=?",
                (file.stem, digest),
            ).fetchone()
            if row and Path(row[0]).is_file():
                print(f"[SKIP] {file.name}: identical content already ingested")
                manifest["files"].append({"dataset": file.stem, "status": "skipped", "sha256": digest})
                continue
            if row:
                raise RuntimeError(f"Audit record exists but Bronze file is missing: {row[0]}")

            count = count_records(file)
            destination = batch_dir / file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_path = destination.with_suffix(".csv.tmp")
            try:
                shutil.copyfile(file, temp_path)
                if sha256(temp_path) != digest:
                    raise RuntimeError(f"Source changed while copying: {file.name}")
                os.replace(temp_path, destination)
            finally:
                temp_path.unlink(missing_ok=True)

            ingested_at = utc_now()
            conn.execute(
                "INSERT INTO ingestion_audit VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file.stem, digest, batch_id, str(file.resolve()), str(destination.resolve()), count, ingested_at),
            )
            conn.commit()
            manifest["files"].append({
                "dataset": file.stem, "status": "ingested", "row_count": count,
                "sha256": digest, "bronze_path": str(destination.resolve()),
                "ingested_at": ingested_at,
            })
            print(f"[INGESTED] {file.name}: {count:,} rows")

        manifest["completed_at"] = utc_now()
        batch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = batch_dir / "manifest.json"
        temp_manifest = batch_dir / "manifest.json.tmp"
        temp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temp_manifest, manifest_path)
        print(f"[SUCCESS] Batch {batch_id} | Manifest: {manifest_path}")
        return manifest
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "synthetic")
    parser.add_argument("--bronze", type=Path, default=ROOT / "data" / "raw" / "bronze")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "raw" / "ingestion_audit.db")
    args = parser.parse_args()
    ingest(args.source, args.bronze, args.db)


if __name__ == "__main__":
    main()
