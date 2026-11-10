"""Phase 8J-D: read-only local Gold ↔ Athena ↔ dashboard KPI reconciliation.

Run from the AegisData AI repository root (Windows CMD):
    python tools/phase8jd_reconcile.py

Uses the configured AWS_PROFILE (default aegisdata) and AWS_REGION (default
us-east-1) through dashboard/athena_client.py. Requires a complete local Gold
run under data/processed/gold/run_id=*/ with all five marts and manifest.json.
Does not write to AWS, alter Gold data, or require a live Bedrock request.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
from athena_client import run_query  # noqa: E402

DATABASE = "aegisdata_gold_db"
GOLD = ROOT / "data" / "processed" / "gold"
OUT = ROOT / "reports" / "reconciliation" / "phase8jd_latest.json"
MARTS = [
    "fact_daily_sales", "dim_customer_segments", "fact_product_performance",
    "fact_payment_performance", "fact_inventory_health",
]
# These values were observed on the Executive Overview, not automatically
# validated expectations. A mismatch may indicate that the data has changed.
DISPLAYED_SNAPSHOT = {
    "paid_revenue": "6473621049.24", "paid_orders": 16996,
    "customers": 5000, "units_sold": 51052, "average_order_value": "380890.86",
}
QUERIES = {
    "daily": f"""SELECT COUNT(*) AS sales_days,
        COALESCE(SUM(paid_orders),0) AS paid_orders,
        COALESCE(SUM(units_sold),0) AS units_sold,
        COALESCE(SUM(paid_sales_amount),CAST(0 AS DECIMAL(38,2))) AS paid_revenue
        FROM {DATABASE}.fact_daily_sales""",
    "customers": f"""SELECT COUNT(*) AS customers,
        COALESCE(SUM(paid_order_count),0) AS paid_orders,
        COALESCE(SUM(units_purchased),0) AS units_sold,
        COALESCE(SUM(historical_paid_spend),CAST(0 AS DECIMAL(38,2))) AS paid_revenue
        FROM {DATABASE}.dim_customer_segments""",
    "products": f"""SELECT COUNT(*) AS products,
        COALESCE(SUM(paid_order_count),0) AS paid_orders,
        COALESCE(SUM(units_sold),0) AS units_sold,
        COALESCE(SUM(paid_sales_amount),CAST(0 AS DECIMAL(38,2))) AS paid_revenue
        FROM {DATABASE}.fact_product_performance""",
    "payments": f"""SELECT COUNT(*) AS payment_groups,
        COALESCE(SUM(total_transactions),0) AS transactions,
        COALESCE(SUM(successful_transactions),0) AS successful,
        COALESCE(SUM(failed_transactions),0) AS failed,
        COALESCE(SUM(pending_transactions),0) AS pending,
        COALESCE(SUM(successful_payment_amount),CAST(0 AS DECIMAL(38,2))) AS paid_revenue
        FROM {DATABASE}.fact_payment_performance""",
    "inventory": f"""SELECT COUNT(*) AS inventory_products FROM {DATABASE}.fact_inventory_health""",
}
MONEY_KEYS = {"paid_revenue", "average_order_value"}


def number(value: Any) -> Decimal:
    if value is None or str(value).strip() == "":
        raise ValueError("Aggregate contains an unexpected NULL/blank value")
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid aggregate: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"Non-finite aggregate: {value!r}")
    return d


def sql_metrics() -> dict[str, dict[str, Decimal]]:
    result = {}
    for name, sql in QUERIES.items():
        frame = run_query(sql)
        if len(frame) != 1:
            raise ValueError(f"{name}: expected exactly one aggregate row, got {len(frame)}")
        result[name] = {key: number(value) for key, value in frame.iloc[0].items()}
    return result


def local_metrics() -> tuple[Path, dict[str, dict[str, Decimal]]]:
    try:
        from pyspark.sql import SparkSession, functions as F
    except ImportError as exc:
        raise RuntimeError("pyspark is required for local Gold reconciliation") from exc
    candidates = sorted((p for p in GOLD.glob("run_id=*")
                         if (p / "manifest.json").is_file()
                         and all((p / mart).is_dir() for mart in MARTS)), reverse=True)
    if not candidates:
        raise FileNotFoundError("No complete local Gold run found in data/processed/gold/")
    latest = candidates[0]
    spark = (SparkSession.builder.master("local[2]")
             .appName("AegisData-Phase8JD-Reconciliation")
             .config("spark.sql.session.timeZone", "UTC")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    specs = {
        "daily": ("fact_daily_sales", {"sales_days": "*", "paid_orders": "paid_orders", "units_sold": "units_sold", "paid_revenue": "paid_sales_amount"}),
        "customers": ("dim_customer_segments", {"customers": "*", "paid_orders": "paid_order_count", "units_sold": "units_purchased", "paid_revenue": "historical_paid_spend"}),
        "products": ("fact_product_performance", {"products": "*", "paid_orders": "paid_order_count", "units_sold": "units_sold", "paid_revenue": "paid_sales_amount"}),
        "payments": ("fact_payment_performance", {"payment_groups": "*", "transactions": "total_transactions", "successful": "successful_transactions", "failed": "failed_transactions", "pending": "pending_transactions", "paid_revenue": "successful_payment_amount"}),
        "inventory": ("fact_inventory_health", {"inventory_products": "*"}),
    }
    results = {}
    try:
        for name, (mart, columns) in specs.items():
            frame = spark.read.parquet(str(latest / mart))
            aggs = [F.count(F.lit(1)).alias(key) if src == "*" else
                    F.sum(F.col(src)).alias(key) for key, src in columns.items()]
            row = frame.agg(*aggs).first().asDict()
            results[name] = {key: number(value if value is not None else 0)
                             for key, value in row.items()}
    finally:
        spark.stop()
    return latest, results


def main() -> int:
    report: dict[str, Any] = {
        "phase": "8J-D", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": DATABASE, "status": "ERROR", "checks": [],
        "notes": ["Read-only. All monetary comparisons use Decimal with ₦0.01 tolerance.",
                  "Snapshot values are historical screen observations, not fixed requirements.",
                  "Local versus Athena comparison assumes both sources represent the same Gold run."],
    }

    def check(label: str, actual: Decimal, expected: Decimal, *, money: bool = False) -> None:
        tolerance = Decimal("0.01") if money else Decimal(0)
        passed = abs(actual - expected) <= tolerance
        report["checks"].append({"check": label, "status": "PASS" if passed else "FAIL",
                                 "actual": str(actual), "expected": str(expected),
                                 "difference": str(actual - expected)})

    try:
        remote = sql_metrics()
        local_path, local = local_metrics()
        report["local_gold_run"] = local_path.name
        report["athena"] = {table: {k: str(v) for k, v in values.items()}
                            for table, values in remote.items()}
        for table, metrics in remote.items():
            for key, value in metrics.items():
                check(f"local Gold vs Athena / {table}.{key}", value,
                      local[table][key], money=(key in MONEY_KEYS))
        daily = remote["daily"]
        for name in ("customers", "products"):
            for key in ("paid_orders", "units_sold", "paid_revenue"):
                check(f"Athena daily vs {name} / {key}", remote[name][key],
                      daily[key], money=(key in MONEY_KEYS))
        check("Athena daily vs successful payments / paid_revenue",
              remote["payments"]["paid_revenue"], daily["paid_revenue"], money=True)
        check("Athena products vs inventory product count", remote["products"]["products"],
              remote["inventory"]["inventory_products"])
        pay = remote["payments"]
        check("payment statuses sum to total transactions",
              pay["successful"] + pay["failed"] + pay["pending"], pay["transactions"])
        check("positive paid orders", Decimal(1) if daily["paid_orders"] > 0 else Decimal(0), Decimal(1))
        aov = (daily["paid_revenue"] / daily["paid_orders"]).quantize(Decimal("0.01")) if daily["paid_orders"] else Decimal(0)
        report["current_athena_dashboard_kpis"] = {"paid_revenue": str(daily["paid_revenue"]),
            "paid_orders": str(daily["paid_orders"]), "customers": str(remote["customers"]["customers"]),
            "units_sold": str(daily["units_sold"]), "average_order_value": str(aov)}
        # Comparisons to the historical screenshot are informational only.
        report["displayed_snapshot_comparison"] = {
            key: {"screen": str(old), "current_athena": report["current_athena_dashboard_kpis"][key],
                  "matches": abs(number(old) - number(report["current_athena_dashboard_kpis"][key])) <=
                  (Decimal("0.01") if key in MONEY_KEYS else Decimal(0))}
            for key, old in DISPLAYED_SNAPSHOT.items()}
        report["status"] = "PASS" if all(c["status"] == "PASS" for c in report["checks"]) else "FAIL"
    except Exception as exc:
        report["status"] = "ERROR"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": report["phase"], "status": report["status"],
                      "checks": len(report["checks"]),
                      "failed": sum(c["status"] == "FAIL" for c in report["checks"]),
                      "local_gold_run": report.get("local_gold_run"),
                      "error": report.get("error"), "report": str(OUT)}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
