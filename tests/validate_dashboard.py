"""AegisData AI — Phase 7: Gold-layer reconciliation checks.

Copy into tests/validate_dashboard.py and run from repository root:
    python tests/validate_dashboard.py

Requires an authenticated AWS CLI profile and dashboard/athena_client.py.
No AWS calls happen at import time.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

from athena_client import run_query  # noqa: E402

DB = "aegisdata_gold_db"
OUT_DIR = ROOT / "reports" / "validation"

# Benchmarks for the current synthetic dataset, not general production limits.
EXPECTED = {
    "customers": 5000,
    "products": 500,
    "inventory": 500,
    "daily_sales_rows": 644,
    "payment_rows": 2351,
    "paid_orders": 16996,
    "payment_transactions": 20000,
}


@dataclass
class Check:
    name: str
    status: str
    actual: Any
    expected: Any
    details: str = ""


checks: list[Check] = []


def check(name: str, actual: Any, expected: Any, *, details: str = "") -> None:
    ok = actual == expected
    checks.append(Check(name, "PASS" if ok else "FAIL", str(actual), str(expected), details))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: actual={actual}, expected={expected}", flush=True)


def warn(name: str, actual: Any, expected: Any, *, details: str = "") -> None:
    checks.append(Check(name, "WARN", str(actual), str(expected), details))
    print(f"[WARN] {name}: actual={actual}, expected={expected}", flush=True)


def number(value: Any) -> Decimal:
    if value is None or str(value).strip().lower() in {"", "none", "nan"}:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Non-numeric Athena aggregate: {value!r}") from exc


def row(sql: str) -> dict[str, Any]:
    data = run_query(sql)
    if data is None or data.empty or len(data) != 1:
        raise ValueError("Expected exactly one aggregate row from Athena")
    return data.iloc[0].to_dict()


def count(name: str, sql: str) -> int:
    return int(number(row(sql)[name]))


def money_check(name: str, left: Any, right: Any, tolerance: str = "0.05") -> None:
    a, b = number(left), number(right)
    diff = abs(a - b)
    limit = Decimal(tolerance)
    ok = diff <= limit
    checks.append(Check(name, "PASS" if ok else "FAIL", str(a), str(b), f"Difference={diff}; tolerance={limit}"))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: difference={diff} NGN", flush=True)


def run_validation() -> None:
    print("\nAEGISDATA AI - GOLD DATA RECONCILIATION\n", flush=True)

    customers = row(f"""SELECT COUNT(*) AS n, COUNT(DISTINCT customer_id) AS unique_ids,
        COALESCE(SUM(paid_order_count), 0) AS paid_orders,
        COALESCE(SUM(historical_paid_spend), 0) AS paid_spend
        FROM {DB}.dim_customer_segments""")
    check("Customer row count", int(number(customers["n"])), EXPECTED["customers"])
    check("Customer IDs unique", int(number(customers["unique_ids"])), int(number(customers["n"])))

    sales = row(f"""SELECT COUNT(*) AS n, COUNT(DISTINCT sales_date) AS unique_dates,
        COALESCE(SUM(paid_orders), 0) AS paid_orders,
        COALESCE(SUM(units_sold), 0) AS units,
        COALESCE(SUM(paid_sales_amount), 0) AS revenue
        FROM {DB}.fact_daily_sales""")
    check("Daily sales row count", int(number(sales["n"])), EXPECTED["daily_sales_rows"])
    check("Unique daily sales dates", int(number(sales["unique_dates"])), int(number(sales["n"])))
    check("Paid orders benchmark", int(number(sales["paid_orders"])), EXPECTED["paid_orders"])

    products = row(f"""SELECT COUNT(*) AS n, COUNT(DISTINCT product_id) AS unique_ids,
        COALESCE(SUM(paid_order_count), 0) AS paid_order_lines,
        COALESCE(SUM(units_sold), 0) AS units,
        COALESCE(SUM(paid_sales_amount), 0) AS revenue
        FROM {DB}.fact_product_performance""")
    check("Product row count", int(number(products["n"])), EXPECTED["products"])
    check("Product IDs unique", int(number(products["unique_ids"])), int(number(products["n"])))
    check("Product vs sales units", int(number(products["units"])), int(number(sales["units"])))
    money_check("Product vs sales revenue", products["revenue"], sales["revenue"])
    # Product paid_order_count is a sum of product-level counts; it may count
    # one order multiple times, so it is deliberately not asserted equal.
    print(f"[INFO] Sum of product-level paid-order counts: {products['paid_order_lines']}")

    inventory = row(f"""SELECT COUNT(*) AS n, COUNT(DISTINCT product_id) AS unique_ids,
        COALESCE(SUM(stock_quantity), 0) AS stock_units,
        COALESCE(SUM(suggested_reorder_units), 0) AS reorder_units
        FROM {DB}.fact_inventory_health""")
    check("Inventory row count", int(number(inventory["n"])), EXPECTED["inventory"])
    check("Inventory product IDs unique", int(number(inventory["unique_ids"])), int(number(inventory["n"])))
    if number(inventory["stock_units"]) < 0:
        check("Stock nonnegative", False, True)
    else:
        check("Stock nonnegative", True, True)
    if number(inventory["reorder_units"]) < 0:
        check("Reorder units nonnegative", False, True)
    else:
        check("Reorder units nonnegative", True, True)

    payment = row(f"""SELECT COUNT(*) AS n,
        COALESCE(SUM(total_transactions), 0) AS transactions,
        COALESCE(SUM(successful_transactions), 0) AS successful,
        COALESCE(SUM(failed_transactions), 0) AS failed,
        COALESCE(SUM(pending_transactions), 0) AS pending,
        COALESCE(SUM(successful_payment_amount), 0) AS successful_amount
        FROM {DB}.fact_payment_performance""")
    check("Payment aggregate rows", int(number(payment["n"])), EXPECTED["payment_rows"])
    check("Transaction benchmark", int(number(payment["transactions"])), EXPECTED["payment_transactions"])
    statuses = sum(int(number(payment[k])) for k in ("successful", "failed", "pending"))
    check("Payment statuses reconcile", statuses, int(number(payment["transactions"])))
    check("Successful payments vs paid orders", int(number(payment["successful"])), int(number(sales["paid_orders"])))
    money_check("Successful payments vs daily sales revenue", payment["successful_amount"], sales["revenue"])
    money_check("Customer historical spend vs sales revenue", customers["paid_spend"], sales["revenue"])

    print("\n[INFO] Gold-table row counts validated through server-side Athena aggregates.")
    print("[INFO] Run interactive filter tests manually in the Streamlit app next.")


def main() -> int:
    error = None
    try:
        run_validation()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        checks.append(Check("Validation execution", "ERROR", error, "All queries should finish"))
        print(f"[ERROR] {error}", file=sys.stderr)

    summary = {state: sum(c.status == state for c in checks) for state in ("PASS", "FAIL", "WARN", "ERROR")}
    output = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": DB,
        "summary": summary,
        "checks": [asdict(c) for c in checks],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "gold_validation_latest.json"
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nValidation report: {path}")
    print("Results:", summary)
    return 1 if summary["FAIL"] or summary["ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
