"""Optional real-AWS smoke tests. Run only with: pytest -m live

This actually invokes Athena and may incur AWS charges. Requires valid aegisdata
AWS profile, configured workgroup and query results bucket.
"""
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
PAGES = ["Executive Overview", "Revenue Analytics", "Customer Intelligence",
         "Product Performance", "Inventory Health", "Payment Analytics"]
pytestmark = pytest.mark.live


def check(at, page):
    errors = [str(x.message) for x in at.exception]
    errors += [str(x.message) for x in at.error]
    assert not errors, f"{page}: " + " | ".join(errors)
    assert any(page == str(h.value) for h in at.header), f"Page heading absent: {page}"


@pytest.mark.parametrize("page", PAGES)
def test_live_page_loads(page):
    at = AppTest.from_file(str(APP), default_timeout=240).run()
    check(at, "Executive Overview")
    if page != "Executive Overview":
        at.sidebar.radio[0].set_value(page).run(timeout=240)
    check(at, page)


def test_live_product_filter_interaction():
    at = AppTest.from_file(str(APP), default_timeout=240).run()
    at.sidebar.radio[0].set_value("Product Performance").run(timeout=240)
    check(at, "Product Performance")
    if not at.multiselect:
        pytest.fail("Product category multiselect not found")
    control = at.multiselect[0]
    options = list(control.options)
    assert len(options) > 1
    control.set_value([options[0]]).run(timeout=240)
    check(at, "Product Performance")
    assert list(at.multiselect[0].value) == [options[0]]


def test_live_payment_date_and_method_filters():
    at = AppTest.from_file(str(APP), default_timeout=240).run()
    at.sidebar.radio[0].set_value("Payment Analytics").run(timeout=240)
    check(at, "Payment Analytics")
    assert at.date_input, "Payment reporting date filter missing"
    assert at.multiselect, "Payment method filter missing"
    widget = at.multiselect[0]
    options = list(widget.options)
    assert len(options) >= 1
    widget.set_value([options[0]]).run(timeout=240)
    check(at, "Payment Analytics")
    assert list(at.multiselect[0].value) == [options[0]]
