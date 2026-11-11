"""Generate validated, reproducible synthetic e-commerce data for AegisData AI."""

import csv
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42
CUSTOMER_COUNT = 5_000
PRODUCT_COUNT = 500
ORDER_COUNT = 20_000
OUTPUT_DIR = Path(__file__).resolve().parent
START_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)

CATEGORIES = ["Electronics", "Fashion", "Home & Kitchen", "Beauty", "Sports", "Books", "Groceries"]
LOCATIONS = ["Lagos", "Abuja", "Ibadan", "Port Harcourt", "Kano", "Enugu", "Benin City"]
PAYMENT_METHODS = ["Card", "Bank Transfer", "Wallet", "USSD"]
CUSTOMER_SEGMENTS = ["Standard", "Premium", "VIP"]
ORDER_STATUSES = ["Completed", "Cancelled", "Pending"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def random_datetime(start: datetime, end: datetime) -> datetime:
    """Return a random UTC-aware datetime between start and end."""
    if start > end:
        raise ValueError("Start date cannot be later than end date")
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, seconds))


def save_csv(filename: str, rows: list[dict]) -> Path:
    """Write one fully generated dataset to a UTF-8 CSV file."""
    if not rows:
        raise ValueError(f"No rows supplied for {filename}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    logger.info("[SUCCESS] %s: %s records", filename, f"{len(rows):,}")
    return path


def generate_customers() -> list[dict]:
    logger.info("Generating customer records...")
    customers = []
    for i in range(1, CUSTOMER_COUNT + 1):
        customers.append({
            "customer_id": f"CUST{i:06d}",
            "customer_name": f"Customer {i:06d}",
            "email": f"customer{i:06d}@example.com",
            "city": random.choice(LOCATIONS),
            "signup_date": random_datetime(START_DATE, END_DATE - timedelta(days=1)).isoformat(),
            "customer_segment": random.choices(CUSTOMER_SEGMENTS, weights=[70, 25, 5], k=1)[0],
        })
    return customers


def generate_products() -> list[dict]:
    logger.info("Generating product records...")
    products = []
    for i in range(1, PRODUCT_COUNT + 1):
        category = random.choice(CATEGORIES)
        products.append({
            "product_id": f"PROD{i:05d}",
            "product_name": f"{category} Item {i:05d}",
            "category": category,
            "unit_price": round(random.uniform(1_500, 250_000), 2),
            "currency": "NGN",
            "is_active": True,
        })
    return products


def generate_orders(customers: list[dict], products: list[dict]) -> list[dict]:
    logger.info("Generating order records...")
    orders = []
    for i in range(1, ORDER_COUNT + 1):
        customer = random.choice(customers)
        product = random.choice(products)
        signup_date = datetime.fromisoformat(customer["signup_date"])
        quantity = random.randint(1, 5)
        price = product["unit_price"]
        orders.append({
            "order_id": f"ORD{i:08d}",
            "customer_id": customer["customer_id"],
            "product_id": product["product_id"],
            "order_date": random_datetime(signup_date, END_DATE).isoformat(),
            "quantity": quantity,
            "unit_price": price,
            "total_amount": round(quantity * price, 2),
            "currency": "NGN",
            "order_status": random.choices(ORDER_STATUSES, weights=[85, 10, 5], k=1)[0],
        })
    return orders


def generate_payments(orders: list[dict]) -> list[dict]:
    logger.info("Generating payment records...")
    status_map = {"Completed": "Successful", "Cancelled": "Failed", "Pending": "Pending"}
    payments = []
    for i, order in enumerate(orders, start=1):
        payments.append({
            "payment_id": f"PAY{i:08d}",
            "order_id": order["order_id"],
            "payment_date": order["order_date"],
            "payment_method": random.choice(PAYMENT_METHODS),
            "payment_amount": order["total_amount"],
            "currency": "NGN",
            "payment_status": status_map[order["order_status"]],
        })
    return payments


def generate_inventory(products: list[dict]) -> list[dict]:
    logger.info("Generating inventory records...")
    inventory = []
    for product in products:
        stock = random.randint(0, 500)
        threshold = random.randint(20, 80)
        inventory.append({
            "product_id": product["product_id"],
            "stock_quantity": stock,
            "reorder_level": threshold,
            "warehouse_location": random.choice(LOCATIONS),
            "inventory_status": "Low Stock" if stock <= threshold else "In Stock",
            "last_updated": END_DATE.isoformat(),
        })
    return inventory


def validate_datasets(datasets: dict[str, list[dict]]) -> None:
    """Validate counts, uniqueness, foreign keys, and financial consistency."""
    logger.info("Validating generated datasets...")
    expected_counts = {
        "customers.csv": CUSTOMER_COUNT,
        "products.csv": PRODUCT_COUNT,
        "orders.csv": ORDER_COUNT,
        "payments.csv": ORDER_COUNT,
        "inventory.csv": PRODUCT_COUNT,
    }
    primary_keys = {
        "customers.csv": "customer_id", "products.csv": "product_id",
        "orders.csv": "order_id", "payments.csv": "payment_id",
        "inventory.csv": "product_id",
    }
    for filename, expected in expected_counts.items():
        rows = datasets[filename]
        if len(rows) != expected:
            raise ValueError(f"{filename}: expected {expected:,} records, got {len(rows):,}")
        key = primary_keys[filename]
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate {key} values in {filename}")
        logger.info("[VALIDATED] %s: %s records", filename, f"{len(rows):,}")

    customer_ids = {r["customer_id"] for r in datasets["customers.csv"]}
    products_by_id = {r["product_id"]: r for r in datasets["products.csv"]}
    orders_by_id = {r["order_id"]: r for r in datasets["orders.csv"]}
    payment_order_ids = set()

    for order in datasets["orders.csv"]:
        if order["customer_id"] not in customer_ids:
            raise ValueError(f"Unknown customer for {order['order_id']}")
        product = products_by_id.get(order["product_id"])
        if product is None:
            raise ValueError(f"Unknown product for {order['order_id']}")
        if order["unit_price"] != product["unit_price"]:
            raise ValueError(f"Incorrect product price for {order['order_id']}")
        if abs(order["total_amount"] - round(order["quantity"] * order["unit_price"], 2)) > 0.01:
            raise ValueError(f"Incorrect total amount for {order['order_id']}")
        if datetime.fromisoformat(order["order_date"]) > END_DATE:
            raise ValueError(f"Future order date for {order['order_id']}")

    status_map = {"Completed": "Successful", "Cancelled": "Failed", "Pending": "Pending"}
    for payment in datasets["payments.csv"]:
        order_id = payment["order_id"]
        order = orders_by_id.get(order_id)
        if order is None or order_id in payment_order_ids:
            raise ValueError(f"Unknown or duplicate payment order reference: {order_id}")
        payment_order_ids.add(order_id)
        if payment["payment_amount"] != order["total_amount"]:
            raise ValueError(f"Incorrect payment amount for {order_id}")
        if payment["payment_status"] != status_map[order["order_status"]]:
            raise ValueError(f"Incorrect payment status for {order_id}")
    if payment_order_ids != set(orders_by_id):
        raise ValueError("Not every order has a payment record")

    for record in datasets["inventory.csv"]:
        if record["product_id"] not in products_by_id:
            raise ValueError(f"Unknown inventory product {record['product_id']}")
        expected_status = "Low Stock" if record["stock_quantity"] <= record["reorder_level"] else "In Stock"
        if record["inventory_status"] != expected_status:
            raise ValueError(f"Incorrect inventory status for {record['product_id']}")

    logger.info("All dataset validation checks passed.")


def main() -> None:
    random.seed(SEED)
    print("=" * 60, flush=True)
    print("AEGISDATA AI - SYNTHETIC DATA GENERATION", flush=True)
    print("=" * 60, flush=True)
    datasets = {}
    customers = generate_customers()
    products = generate_products()
    orders = generate_orders(customers, products)
    datasets["customers.csv"] = customers
    datasets["products.csv"] = products
    datasets["orders.csv"] = orders
    datasets["payments.csv"] = generate_payments(orders)
    datasets["inventory.csv"] = generate_inventory(products)
    validate_datasets(datasets)

    for filename, rows in datasets.items():
        save_csv(filename, rows)

    print("-" * 60)
    print(f"Total datasets: {len(datasets)}")
    print(f"Total records: {sum(map(len, datasets.values())):,}")
    print(f"Output directory: {OUTPUT_DIR}")
    print("Generation completed successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
