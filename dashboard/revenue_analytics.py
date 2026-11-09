"""Revenue Analytics page for AegisData AI.

Integrate from dashboard/app.py using:
    from revenue_analytics import render_revenue_analytics
    ...
    elif page == "Revenue Analytics":
        render_revenue_analytics(query)

`query(sql)` must return a pandas DataFrame from AWS Athena.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd
import streamlit as st


DAILY_SALES_TABLE = "aegisdata_gold_db.fact_daily_sales"


def _load_daily_sales(query: Callable[[str], pd.DataFrame]) -> pd.DataFrame:
    """Read the full 644-row-or-more daily sales history from Athena."""
    sql = f"""
        SELECT sales_date, paid_orders, units_sold, paid_sales_amount
        FROM {DAILY_SALES_TABLE}
        ORDER BY sales_date
    """
    df = query(sql)
    if not isinstance(df, pd.DataFrame):
        raise TypeError("query(sql) must return a pandas DataFrame")
    required = {"sales_date", "paid_orders", "units_sold", "paid_sales_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required Athena columns: {', '.join(sorted(missing))}")

    df = df.copy()
    df["sales_date"] = pd.to_datetime(df["sales_date"], errors="coerce")
    df = df.dropna(subset=["sales_date"])
    for col in ("paid_orders", "units_sold", "paid_sales_amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df.sort_values("sales_date").reset_index(drop=True)


def _compact_naira(amount: float) -> str:
    absolute = abs(amount)
    if absolute >= 1_000_000_000:
        return f"₦{amount / 1_000_000_000:,.2f}B"
    if absolute >= 1_000_000:
        return f"₦{amount / 1_000_000:,.2f}M"
    return f"₦{amount:,.2f}"


def render_revenue_analytics(query: Callable[[str], pd.DataFrame]) -> None:
    """Render interactive revenue metrics from fact_daily_sales."""
    st.header("Revenue Analytics")
    st.caption("Source: AWS Athena • aegisdata_gold_db.fact_daily_sales • paid orders only")

    try:
        daily = _load_daily_sales(query)
    except Exception as exc:
        st.error("Could not retrieve Revenue Analytics data from AWS Athena.")
        st.exception(exc)
        return

    if daily.empty:
        st.warning("No valid sales dates were returned by Athena.")
        return

    earliest = daily["sales_date"].min().date()
    latest = daily["sales_date"].max().date()

    selected = st.date_input(
        "Revenue reporting period",
        value=(earliest, latest),
        min_value=earliest,
        max_value=latest,
        key="revenue_reporting_period",
    )
    if not isinstance(selected, (tuple, list)) or len(selected) != 2:
        st.info("Select both a start date and an end date to see revenue analytics.")
        return
    start_date, end_date = selected
    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    filtered = daily.loc[
        daily["sales_date"].dt.date.between(start_date, end_date)
    ].copy()
    if filtered.empty:
        st.warning("No paid sales fall within the chosen period.")
        return

    revenue = float(filtered["paid_sales_amount"].sum())
    orders = int(filtered["paid_orders"].sum())
    units = int(filtered["units_sold"].sum())
    aov = revenue / orders if orders else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Paid Revenue", _compact_naira(revenue), help=f"Exact value: ₦{revenue:,.2f}")
    c2.metric("Paid Orders", f"{orders:,}")
    c3.metric("Units Sold", f"{units:,}")
    c4.metric("Weighted AOV", _compact_naira(aov), help=f"Exact value: ₦{aov:,.2f}")
    st.caption(f"Selected-period revenue (exact): ₦{revenue:,.2f}. AOV = paid revenue ÷ paid orders.")

    filtered["month"] = filtered["sales_date"].dt.to_period("M").dt.to_timestamp()
    monthly = (
        filtered.groupby("month", as_index=False)
        .agg(
            paid_revenue=("paid_sales_amount", "sum"),
            paid_orders=("paid_orders", "sum"),
            units_sold=("units_sold", "sum"),
        )
        .sort_values("month")
    )
    monthly["weighted_aov"] = (
        monthly["paid_revenue"] / monthly["paid_orders"].replace(0, float("nan"))
    ).fillna(0.0)
    monthly["mom_growth_pct"] = monthly["paid_revenue"].pct_change() * 100
    # The first month has no comparison. If prior-month revenue is zero,
    # month-over-month growth is undefined rather than infinity.
    monthly["mom_growth_pct"] = monthly["mom_growth_pct"].replace(
        [float("inf"), -float("inf")], float("nan")
    )

    st.subheader("Monthly Revenue Trend")
    st.line_chart(monthly.set_index("month")["paid_revenue"], y_label="Paid revenue (₦)")

    left, right = st.columns(2)
    with left:
        st.subheader("Monthly Paid Orders")
        st.bar_chart(monthly.set_index("month")["paid_orders"], y_label="Orders")
    with right:
        st.subheader("Monthly Average Order Value")
        st.line_chart(monthly.set_index("month")["weighted_aov"], y_label="AOV (₦)")

    st.subheader("Month-over-Month Revenue Growth")
    st.caption("Growth compares adjacent displayed months. A partial first or last month is not directly comparable with a full month.")
    growth = monthly.dropna(subset=["mom_growth_pct"])
    if growth.empty:
        st.info("Select at least two months with nonzero prior-month revenue to calculate growth.")
    else:
        st.bar_chart(growth.set_index("month")["mom_growth_pct"], y_label="Growth (%)")

    with st.expander("Explore monthly revenue figures"):
        display = monthly.copy()
        display["month"] = display["month"].dt.strftime("%Y-%m")
        display["mom_growth_pct"] = display["mom_growth_pct"].round(2)
        st.dataframe(display, width="stretch", hide_index=True)
        st.download_button(
            "Download monthly revenue CSV",
            data=display.to_csv(index=False).encode("utf-8"),
            file_name="aegisdata_monthly_revenue.csv",
            mime="text/csv",
        )

    with st.expander("Explore underlying daily sales"):
        st.dataframe(filtered.drop(columns=["month"]), width="stretch", hide_index=True)

    st.caption(
        "Note: Revenue Analytics reflects paid sales recorded in fact_daily_sales; "
        "it does not include pending/failed payment amounts or refund adjustments "
        "unless those are already accounted for in the Gold model."
    )
