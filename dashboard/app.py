"""AegisData AI — AWS Athena-powered business analytics dashboard.

Run from the project root:
    python -m streamlit run dashboard/app.py

Requires dashboard/athena_client.py with run_query(sql: str) -> pandas.DataFrame.
The Athena client must paginate GetQueryResults for complete detail/chart data.
"""

from __future__ import annotations
from revenue_analytics import render_revenue_analytics
from customer_intelligence import render_customer_intelligence
from product_performance import render as render_product_performance
from inventory_health import render_inventory_health

from payment_analytics import render_payment_analytics


from pathlib import Path
import sys

import pandas as pd
import streamlit as st

# Support execution from the repository root or directly from dashboard/.
DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from athena_client import run_query  # noqa: E402

DATABASE = "aegisdata_gold_db"
# Validation benchmark for the current demo dataset.
EXPECTED_DAILY_SALES_ROWS = 644

st.set_page_config(page_title="AegisData AI", page_icon="📊", layout="wide")


@st.cache_data(ttl=900, show_spinner="Loading data from AWS Athena...")
def query(sql: str) -> pd.DataFrame:
    """Run a query using the existing Athena client, with short-lived caching."""
    result = run_query(sql)
    if not isinstance(result, pd.DataFrame):
        raise TypeError(
            "athena_client.run_query() must return a pandas DataFrame")
    return result.copy()


def scalar(frame: pd.DataFrame, column: str, default: float = 0.0) -> float:
    """Extract a numeric scalar safely from a single-row aggregate response."""
    if frame.empty or column not in frame.columns:
        raise ValueError(f"Missing Athena aggregate result: {column}")
    value = pd.to_numeric(frame.iloc[0][column], errors="coerce")
    return default if pd.isna(value) else float(value)


def load_sales_summary() -> dict[str, float]:
    """Calculate complete-table KPIs inside Athena, independently of detail pagination."""
    result = query(f"""
        SELECT
            COUNT(*) AS sales_days,
            COALESCE(SUM(paid_orders), 0) AS total_orders,
            COALESCE(SUM(units_sold), 0) AS total_units,
            COALESCE(SUM(paid_sales_amount), CAST(0 AS DECIMAL(18,2))) AS total_revenue
        FROM {DATABASE}.fact_daily_sales
    """)
    total_orders = scalar(result, "total_orders")
    total_revenue = scalar(result, "total_revenue")
    return {
        "sales_days": scalar(result, "sales_days"),
        "total_orders": total_orders,
        "total_units": scalar(result, "total_units"),
        "total_revenue": total_revenue,
        "aov": total_revenue / total_orders if total_orders else 0.0,
    }


def load_customer_count() -> int:
    result = query(f"""
        SELECT COUNT(*) AS total_customers
        FROM {DATABASE}.dim_customer_segments
    """)
    return int(scalar(result, "total_customers"))


def load_sales_detail() -> pd.DataFrame:
    result = query(f"""
        SELECT
            sales_date,
            paid_orders,
            units_sold,
            paid_sales_amount,
            average_order_value
        FROM {DATABASE}.fact_daily_sales
        ORDER BY sales_date
    """)
    required = {
        "sales_date", "paid_orders", "units_sold", "paid_sales_amount",
        "average_order_value",
    }
    missing = required.difference(result.columns)
    if missing:
        raise ValueError(
            f"Daily sales query is missing columns: {sorted(missing)}")

    result = result.copy()
    result["sales_date"] = pd.to_datetime(
        result["sales_date"], errors="coerce")
    for column in required - {"sales_date"}:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["sales_date"]).sort_values("sales_date")


with st.sidebar:
    st.header("Navigation")
    page = st.radio(
        "Choose a dashboard",
        [
            "Executive Overview",
            "Revenue Analytics",
            "Customer Intelligence",
            "Product Performance",
            "Inventory Health",
            "Payment Analytics",
        ],
    )
    if st.button("Refresh Athena data", use_container_width=True):
        query.clear()
        st.rerun()
    st.caption(f"Source: AWS Athena / {DATABASE}")

st.title("AegisData AI")
st.caption("Intelligent Data Engineering & Business Analytics Platform")

if page == "Executive Overview":
    st.header("Executive Overview")
    try:
        summary = load_sales_summary()
        total_customers = load_customer_count()

        # Aggregated KPIs remain accurate even if the Athena client returns
        # only the first page of detailed rows.
        a, b, c, d = st.columns(4)
        a.metric("Total Paid Revenue", f"₦{summary['total_revenue']:,.2f}")
        b.metric("Paid Orders", f"{int(summary['total_orders']):,}")
        c.metric("Total Customers", f"{total_customers:,}")
        d.metric("Units Sold", f"{int(summary['total_units']):,}")
        st.metric("Weighted Average Order Value", f"₦{summary['aov']:,.2f}")
        st.caption(
            "KPIs cover the full Gold sales table. "
            "Average order value = total paid revenue ÷ total paid orders."
        )

        sales = load_sales_detail()
        expected_rows = int(summary["sales_days"])
        returned_rows = len(sales)

        if sales.empty:
            st.warning("No valid daily sales rows were returned for charting.")
        else:
            # Checking against the aggregate's exact COUNT(*) exposes broken
            # pagination without silently presenting an incomplete time series.
            if returned_rows != expected_rows:
                st.warning(
                    f"Incomplete daily sales data: Athena reports {expected_rows:,} "
                    f"rows, but athena_client.run_query() returned "
                    f"{returned_rows:,}. KPIs above are complete, but "
                    "charts may be incomplete. Check GetQueryResults pagination "
                    "and any row slicing inside dashboard/athena_client.py."
                )

            st.subheader("Daily Sales Trends")
            if returned_rows == expected_rows:
                min_day = sales["sales_date"].min().date()
                max_day = sales["sales_date"].max().date()
                date_range = st.date_input(
                    "Filter chart dates",
                    value=(min_day, max_day),
                    min_value=min_day,
                    max_value=max_day,
                )
                if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
                    start_day, end_day = date_range
                    displayed_sales = sales.loc[
                        sales["sales_date"].dt.date.between(start_day, end_day)
                    ].copy()
                else:
                    displayed_sales = sales.copy()
            else:
                # Don't offer a potentially misleading date filter on an incomplete page.
                displayed_sales = sales.copy()

            if displayed_sales.empty:
                st.info("No daily sales records match the selected dates.")
            else:
                st.subheader("Daily Revenue Trend")
                st.line_chart(
                    displayed_sales.set_index("sales_date")[
                        ["paid_sales_amount"]],
                    width="stretch",
                )
                st.subheader("Daily Paid Orders")
                st.bar_chart(
                    displayed_sales.set_index("sales_date")[["paid_orders"]],
                    width="stretch",
                )
                with st.expander("Explore daily sales data"):
                    st.dataframe(displayed_sales,
                                 width="stretch", hide_index=True)

        with st.expander("Data validation and diagnostics"):
            st.write(f"Athena Gold daily-sales rows: **{expected_rows:,}**")
            st.write(f"Rows received by Streamlit: **{returned_rows:,}**")
            st.write(
                f"Original demo benchmark: **{EXPECTED_DAILY_SALES_ROWS:,}** rows")
            st.write(f"Customer records: **{total_customers:,}**")
            if expected_rows != EXPECTED_DAILY_SALES_ROWS:
                st.info(
                    "The Gold dataset's record count differs from the original "
                    "demo benchmark; this is not necessarily an error."
                )

    except Exception as exc:
        st.error("Unable to load the Executive Overview from AWS Athena.")
        st.exception(exc)
        st.info(
            "Verify your aegisdata login, region, Athena query-results location, "
            "and the five tables in aegisdata_gold_db."
        )

elif page == "Revenue Analytics":
    render_revenue_analytics(query)

elif page == "Customer Intelligence":
    try:
        render_customer_intelligence(query)
    except Exception as exc:
        st.error(
            "Unable to load Customer Intelligence "
            "from AWS Athena."
        )
        st.exception(exc)

elif page == "Product Performance":
    render_product_performance()

elif page == "Inventory Health":
    render_inventory_health()

elif page == "Payment Analytics":
    try:
        render_payment_analytics(query)
    except Exception as exc:
        st.error("Unable to load Payment Analytics from AWS Athena.")
        st.exception(exc)
else:
    st.header(page)
    st.info(
        "This dashboard will be connected "
        "to its Gold table in a later phase."
    )
