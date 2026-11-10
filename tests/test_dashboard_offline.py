"""Headless Streamlit regression tests, with Athena replaced by an offline fixture.

Only the Executive Overview is tested end-to-end offline because its full SQL
contract is known. Remaining pages receive live smoke checks in a separate file.
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import athena_client

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"
PAGES = PAGES = [
    "Executive Overview",
    "Revenue Analytics",
    "Customer Intelligence",
    "Product Performance",
    "Inventory Health",
    "Payment Analytics",
    "AI Data Quality",
    "Agent Observability",
]


def offline_query(sql, *args, **kwargs):
    text = " ".join(sql.lower().split())
    if "dim_customer_segments" in text and "count(" in text:
        return pd.DataFrame({"total_customers": [5000]})
    if "fact_daily_sales" in text:
        if "count(" in text and "sum(" in text:
            return pd.DataFrame({"sales_days": [3], "total_orders": [6],
                                 "total_units": [12], "total_revenue": [6000.0]})
        return pd.DataFrame({
            "sales_date": ["2026-10-29", "2026-10-30", "2026-10-31"],
            "paid_orders": [1, 2, 3], "units_sold": [2, 4, 6],
            "paid_sales_amount": [1000.0, 2000.0, 3000.0],
            "average_order_value": [1000.0, 1000.0, 1000.0],
        })
    raise AssertionError(f"Offline fixture missing SQL mapping: {sql[:160]}")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(athena_client, "run_query", offline_query)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    return at


def assert_clean(at):
    errors = [str(e.message) for e in at.exception]
    assert not errors, "Streamlit exceptions: " + " | ".join(errors)
    # UI-level errors sometimes use st.error instead of raising.
    assert not at.error, "Streamlit errors: " + \
        " | ".join(str(e.message) for e in at.error)


def test_navigation_contains_eight_pages(app):
    assert_clean(app)
    radio = app.sidebar.radio[0]
    assert list(radio.options) == PAGES


def test_executive_metrics_and_charts_render(app):
    assert_clean(app)
    assert app.sidebar.radio[0].value == "Executive Overview"
    metrics = {m.label: str(m.value) for m in app.metric}
    assert "Paid Orders" in metrics
    assert "6" in metrics["Paid Orders"]
    assert "Total Customers" in metrics
    assert "5,000" in metrics["Total Customers"]
    assert any("Daily" in str(h.value) for h in app.subheader)


def test_executive_date_range_interaction(app):
    assert_clean(app)
    assert len(app.date_input) >= 1
    app.date_input[0].set_value((date(2026, 10, 30), date(2026, 10, 31))).run()
    assert_clean(app)
    assert app.date_input[0].value == (date(2026, 10, 30), date(2026, 10, 31))


def test_sidebar_refresh_does_not_crash(app):
    assert_clean(app)
    refresh = [b for b in app.sidebar.button if "Refresh Athena data" in b.label]
    assert len(refresh) == 1
    refresh[0].click().run()
    assert_clean(app)


def test_expected_dashboard_modules_present():
    for filename in [
        "athena_client.py",
        "app.py",
        "revenue_analytics.py",
        "customer_intelligence.py",
        "product_performance.py",
        "inventory_health.py",
        "payment_analytics.py",
        "ai_data_quality.py",
        "agent_observability.py",
    ]:
        assert (ROOT / "dashboard" / filename).is_file(), (
            f"Missing {filename}"
        )
