
"""Automated tests for AegisData AI synthetic datasets."""

import csv
from pathlib import Path

import pytest


DATA_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "synthetic"
)

EXPECTED_COUNTS = {
    "customers.csv": 5000,
    "products.csv": 500,
    "orders.csv": 20000,
    "payments.csv": 20000,
    "inventory.csv": 500,
}


def load_csv(filename):
    path = DATA_DIR / filename

    assert path.exists(), f"Missing file: {path}"

    with path.open(
        "r", encoding="utf-8", newline=""
    ) as file:
        return list(csv.DictReader(file))


@pytest.mark.parametrize(
    "filename,expected",
    EXPECTED_COUNTS.items()
)
def test_record_counts(filename, expected):
    assert len(load_csv(filename)) == expected


def test_unique_primary_keys():
    keys = {
        "customers.csv": "customer_id",
        "products.csv": "product_id",
        "orders.csv": "order_id",
        "payments.csv": "payment_id",
        "inventory.csv": "product_id",
    }

    for filename, key in keys.items():
        rows = load_csv(filename)
        ids = [row[key] for row in rows]

        assert len(ids) == len(set(ids))


def test_order_references():
    customers = {
        row["customer_id"]
        for row in load_csv("customers.csv")
    }
    products = {
        row["product_id"]
        for row in load_csv("products.csv")
    }

    for order in load_csv("orders.csv"):
        assert order["customer_id"] in customers
        assert order["product_id"] in products


def test_order_totals():
    for order in load_csv("orders.csv"):
        expected = (
            int(order["quantity"])
            * float(order["unit_price"])
        )
        assert abs(
            float(order["total_amount"]) - expected
        ) < 0.01


def test_payment_integrity():
    orders = {
        row["order_id"]: row
        for row in load_csv("orders.csv")
    }

    payments = load_csv("payments.csv")

    assert len({
        p["order_id"] for p in payments
    }) == len(payments)

    for payment in payments:
        assert payment["order_id"] in orders
        assert (
            payment["payment_amount"]
            == orders[payment["order_id"]]["total_amount"]
        )


def test_inventory_references():
    products = {
        row["product_id"]
        for row in load_csv("products.csv")
    }

    for inventory in load_csv("inventory.csv"):
        assert inventory["product_id"] in products
