"""AegisData AI Product Performance dashboard.

Rendered by dashboard/app.py via:
    from product_performance import render as render_product_performance
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from .athena_client import run_query
except ImportError:
    from athena_client import run_query

DATABASE = "aegisdata_gold_db"
TABLE = "fact_product_performance"

SQL = f"""
SELECT
    product_id, product_name, category, unit_price, currency,
    is_active, paid_order_count, units_sold, paid_sales_amount,
    average_paid_unit_price
FROM {DATABASE}.{TABLE}
"""

NUMERIC_COLUMNS = [
    "unit_price", "paid_order_count", "units_sold",
    "paid_sales_amount", "average_paid_unit_price",
]
REQUIRED_COLUMNS = [
    "product_id", "product_name", "category", "currency", "is_active",
    *NUMERIC_COLUMNS,
]
SYMBOLS = {"NGN": "₦", "USD": "$", "GBP": "£", "EUR": "€"}


@st.cache_data(ttl=900, show_spinner="Loading product data from AWS Athena...")
def load_product_data() -> pd.DataFrame:
    """Load all product rows, validate their schema, and normalize values."""
    df = run_query(SQL)
    if not isinstance(df, pd.DataFrame):
        raise TypeError("athena_client.run_query() must return a pandas DataFrame.")

    missing = sorted(set(REQUIRED_COLUMNS) - set(df.columns))
    if missing:
        raise ValueError(f"Missing columns in Athena Gold product table: {missing}")

    df = df.copy()
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in ["product_id", "product_name", "category", "currency"]:
        df[column] = df[column].astype("string").str.strip()

    df["category"] = df["category"].fillna("Unknown").replace("", "Unknown")
    df["currency"] = (
        df["currency"].fillna("Unknown").replace("", "Unknown").str.upper()
    )
    df["is_active"] = (
        df["is_active"].astype("string").str.strip().str.lower()
        .isin(["true", "1", "yes", "t"])
    )
    return df


def money(value: float, currency: str) -> str:
    symbol = SYMBOLS.get(currency, f"{currency} ")
    return f"{symbol}{value:,.2f}"


def compact_money(value: float, currency: str) -> str:
    symbol = SYMBOLS.get(currency, f"{currency} ")
    amount = abs(value)
    for cutoff, suffix in [(1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if amount >= cutoff:
            return f"{symbol}{value / cutoff:,.2f}{suffix}"
    return money(value, currency)


def render() -> None:
    st.header("Product Performance")
    st.caption(f"Source: AWS Athena · {DATABASE}.{TABLE}")

    try:
        products = load_product_data()
        if products.empty:
            st.warning("No product records were returned from AWS Athena.")
            return

        st.subheader("Product Filters")
        currencies = sorted(products["currency"].dropna().unique().tolist())
        selected_currency = st.selectbox(
            "Reporting currency", currencies, key="product_currency",
            help="Monetary totals are calculated for one currency at a time.",
        )
        currency_products = products.loc[
            products["currency"] == selected_currency
        ].copy()
        all_categories = sorted(
            currency_products["category"].dropna().unique().tolist()
        )

        left, right = st.columns(2)
        with left:
            categories = st.multiselect(
                "Product Categories", options=all_categories,
                default=all_categories, key=f"product_categories_{selected_currency}",
            )
        with right:
            status = st.selectbox(
                "Product Status", ["All Products", "Active", "Inactive"],
                key="product_status",
            )

        filtered = currency_products.loc[
            currency_products["category"].isin(categories)
        ].copy()
        if status == "Active":
            filtered = filtered.loc[filtered["is_active"]].copy()
        elif status == "Inactive":
            filtered = filtered.loc[~filtered["is_active"]].copy()

        st.caption(
            f"Showing {len(filtered):,} of {len(currency_products):,} "
            f"{selected_currency} product records."
        )
        if filtered.empty:
            st.warning("No products match these filters. Select another category or status.")
            return

        # All KPIs and charts below use the filtered dataset.
        total_products = filtered["product_id"].nunique()
        active_products = filtered.loc[filtered["is_active"], "product_id"].nunique()
        revenue = float(filtered["paid_sales_amount"].fillna(0).sum())
        units = float(filtered["units_sold"].fillna(0).sum())
        product_order_counts = float(filtered["paid_order_count"].fillna(0).sum())
        weighted_unit_price = revenue / units if units > 0 else 0.0

        st.subheader("Product KPIs")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Products", f"{total_products:,}")
        k2.metric("Active Products", f"{active_products:,}")
        k3.metric("Paid Product Revenue", compact_money(revenue, selected_currency))
        k4.metric("Units Sold", f"{int(units):,}")
        k5, k6 = st.columns(2)
        k5.metric("Paid Order Counts (Summed)", f"{int(product_order_counts):,}")
        k6.metric("Weighted Average Unit Price", money(weighted_unit_price, selected_currency))
        st.caption(
            "Weighted average unit price = paid product revenue ÷ units sold. "
            "Summed product order counts can count the same order across multiple products."
        )

        st.subheader("Top 10 Products by Revenue")
        top_products = (
            filtered.groupby(["product_id", "product_name"], as_index=False, dropna=False)
            .agg(revenue=("paid_sales_amount", "sum"), units_sold=("units_sold", "sum"))
            .sort_values("revenue", ascending=False)
            .head(10)
            .copy()
        )
        top_products["label"] = (
            top_products["product_name"].fillna("Unknown").astype(str)
            + " (" + top_products["product_id"].fillna("Unknown").astype(str) + ")"
        )
        fig_top = px.bar(
            top_products.sort_values("revenue"), x="revenue", y="label",
            orientation="h", hover_data=["units_sold"],
            labels={"revenue": f"Paid Revenue ({selected_currency})", "label": "Product"},
        )
        fig_top.update_layout(height=480, showlegend=False, margin=dict(l=10, r=20, t=20, b=20))
        st.plotly_chart(fig_top, width="stretch")

        st.subheader("Category Performance")
        category_performance = (
            filtered.groupby("category", as_index=False)
            .agg(revenue=("paid_sales_amount", "sum"),
                 units_sold=("units_sold", "sum"),
                 products=("product_id", "nunique"))
            .sort_values("revenue", ascending=False)
        )
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Revenue by Category**")
            fig_revenue = px.bar(
                category_performance, x="category", y="revenue",
                labels={"category": "Category", "revenue": f"Paid Revenue ({selected_currency})"},
            )
            st.plotly_chart(fig_revenue, width="stretch")
        with col2:
            st.markdown("**Units Sold by Category**")
            fig_units = px.bar(
                category_performance, x="category", y="units_sold",
                labels={"category": "Category", "units_sold": "Units Sold"},
            )
            st.plotly_chart(fig_units, width="stretch")

        st.subheader("Revenue Concentration")
        ranked = (
            filtered.groupby("product_id", as_index=False)
            .agg(revenue=("paid_sales_amount", "sum"))
            .sort_values("revenue", ascending=False)
        )
        share = (
            float(ranked.head(10)["revenue"].sum()) / revenue * 100
            if revenue > 0 else 0.0
        )
        st.metric("Revenue Share of Top 10 Products", f"{share:.1f}%")
        st.progress(max(0.0, min(share / 100, 1.0)))
        st.caption("Share of selected product revenue generated by the ten highest-revenue products.")

        st.subheader("Product Data Explorer")
        display_columns = [
            "product_id", "product_name", "category", "is_active", "unit_price",
            "paid_order_count", "units_sold", "paid_sales_amount",
            "average_paid_unit_price",
        ]
        display = filtered[display_columns].sort_values(
            "paid_sales_amount", ascending=False
        )
        st.dataframe(display, width="stretch", hide_index=True)
        st.download_button(
            "Download Product Performance CSV",
            data=display.to_csv(index=False).encode("utf-8-sig"),
            file_name="aegisdata_product_performance.csv",
            mime="text/csv",
        )

        with st.expander("Data Quality and Diagnostics"):
            st.write("Total Athena product records:", len(products))
            st.write("Records for selected currency:", len(currency_products))
            st.write("Records after filters:", len(filtered))
            st.write("Duplicate product IDs (full source):", int(products["product_id"].duplicated().sum()))
            st.write("Missing paid revenue values:", int(products["paid_sales_amount"].isna().sum()))
            st.write("Negative paid revenue values:", int((products["paid_sales_amount"] < 0).sum()))
            st.caption("The Gold product table represents a current snapshot, not a historical time series.")

    except Exception as exc:
        st.error("Unable to load Product Performance from AWS Athena.")
        st.exception(exc)
        st.info("Check AWS credentials, the Gold table and dashboard/athena_client.py.")


if __name__ == "__main__":
    st.set_page_config(page_title="AegisData AI - Product Performance", layout="wide")
    render()
