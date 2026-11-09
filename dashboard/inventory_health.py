"""AegisData AI — Inventory Health dashboard backed by AWS Athena.

Install as dashboard/inventory_health.py and invoke render_inventory_health()
from dashboard/app.py. Uses the existing dashboard/athena_client.py.
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
TABLE = "fact_inventory_health"
SQL = f"""
SELECT
    product_id, product_name, category, currency, is_active,
    stock_quantity, reorder_level, warehouse_location,
    inventory_status, last_updated, paid_orders_90d, units_sold_90d,
    snapshot_date, is_low_stock, is_out_of_stock, needs_reorder,
    avg_daily_units_90d, estimated_stock_coverage_days,
    suggested_reorder_units
FROM {DATABASE}.{TABLE}
"""
NUMERIC_COLS = [
    "stock_quantity", "reorder_level", "paid_orders_90d", "units_sold_90d",
    "avg_daily_units_90d", "estimated_stock_coverage_days",
    "suggested_reorder_units",
]
BOOLEAN_COLS = ["is_active", "is_low_stock", "is_out_of_stock", "needs_reorder"]


def _boolean(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.lower().isin(
        ["true", "1", "yes", "y"]
    )


@st.cache_data(ttl=900, show_spinner="Loading inventory from AWS Athena...")
def load_inventory() -> pd.DataFrame:
    frame = run_query(SQL)
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Athena client must return a pandas DataFrame")
    required = set(
        ["product_id", "product_name", "category", "currency", "warehouse_location",
         "inventory_status", "last_updated", "snapshot_date"] + NUMERIC_COLS + BOOLEAN_COLS
    )
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing inventory Gold columns: {sorted(missing)}")
    frame = frame.copy()
    for col in ["product_id", "product_name", "category", "currency", "warehouse_location", "inventory_status"]:
        frame[col] = frame[col].astype("string").str.strip().fillna("Unknown")
    for col in NUMERIC_COLS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in BOOLEAN_COLS:
        frame[col] = _boolean(frame[col])
    for col in ["snapshot_date", "last_updated"]:
        frame[col] = pd.to_datetime(frame[col], errors="coerce")
    return frame


def _count(value: int) -> str:
    return f"{int(value):,}"


def render_inventory_health() -> None:
    st.header("Inventory Health")
    st.caption(f"Source: AWS Athena · {DATABASE}.{TABLE} · latest available Gold snapshot")

    try:
        df = load_inventory()
        if df.empty:
            st.warning("The inventory Gold table returned no records.")
            return

        # Snapshot consistency is essential: mixing snapshots would overcount stock.
        valid_snapshots = df["snapshot_date"].dropna()
        if not valid_snapshots.empty:
            latest_snapshot = valid_snapshots.max()
            if df["snapshot_date"].nunique(dropna=True) > 1:
                st.info("Multiple inventory snapshots detected; showing only the latest snapshot.")
            df = df.loc[df["snapshot_date"] == latest_snapshot].copy()
            st.caption(f"Snapshot date: {latest_snapshot.date().isoformat()}")
        else:
            st.warning("Snapshot dates are missing; confirm that the Gold table is a single snapshot.")

        st.subheader("Inventory Filters")
        currencies = sorted(df["currency"].dropna().unique().tolist())
        categories = sorted(df["category"].dropna().unique().tolist())
        warehouses = sorted(df["warehouse_location"].dropna().unique().tolist())

        a, b, c = st.columns(3)
        with a:
            selected_currency = st.selectbox("Currency", currencies, key="inventory_currency")
        with b:
            selected_categories = st.multiselect(
                "Categories", categories, default=categories, key="inventory_categories"
            )
        with c:
            selected_warehouses = st.multiselect(
                "Warehouse locations", warehouses, default=warehouses,
                key="inventory_warehouses"
            )
        d, e = st.columns(2)
        with d:
            risk_filter = st.selectbox(
                "Stock priority",
                ["All Products", "Out of Stock", "Needs Reorder", "Low Stock", "Healthy"],
                key="inventory_priority",
            )
        with e:
            active_filter = st.selectbox(
                "Product status", ["All", "Active", "Inactive"], key="inventory_active"
            )

        filtered = df.loc[
            df["currency"].eq(selected_currency)
            & df["category"].isin(selected_categories)
            & df["warehouse_location"].isin(selected_warehouses)
        ].copy()
        if active_filter == "Active":
            filtered = filtered.loc[filtered["is_active"]]
        elif active_filter == "Inactive":
            filtered = filtered.loc[~filtered["is_active"]]

        if risk_filter == "Out of Stock":
            filtered = filtered.loc[filtered["is_out_of_stock"]]
        elif risk_filter == "Needs Reorder":
            filtered = filtered.loc[filtered["needs_reorder"]]
        elif risk_filter == "Low Stock":
            filtered = filtered.loc[filtered["is_low_stock"]]
        elif risk_filter == "Healthy":
            filtered = filtered.loc[
                ~(filtered["is_out_of_stock"] | filtered["is_low_stock"] | filtered["needs_reorder"])
            ]

        st.caption(f"Showing {_count(len(filtered))} of {_count(len(df))} inventory records.")
        if filtered.empty:
            st.warning("No inventory items match the selected filters.")
            return

        st.subheader("Stock KPIs")
        items = filtered["product_id"].nunique()
        out_of_stock = int(filtered["is_out_of_stock"].sum())
        low_stock = int(filtered["is_low_stock"].sum())
        reorder = int(filtered["needs_reorder"].sum())
        stock_units = filtered["stock_quantity"].sum(min_count=1)
        reorder_units = filtered.loc[filtered["needs_reorder"], "suggested_reorder_units"].sum(min_count=1)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Products", _count(items))
        k2.metric("Out of Stock", _count(out_of_stock))
        k3.metric("Low Stock", _count(low_stock))
        k4.metric("Need Reorder", _count(reorder))
        k5, k6 = st.columns(2)
        k5.metric("Stock Units Available", _count(stock_units) if pd.notna(stock_units) else "N/A")
        k6.metric("Suggested Reorder Units", _count(reorder_units) if pd.notna(reorder_units) else "N/A")
        st.caption("Stock-risk flags may overlap; do not add their counts together.")

        st.subheader("Replenishment Priorities")
        urgent = filtered.loc[filtered["needs_reorder"] | filtered["is_out_of_stock"]].copy()
        if urgent.empty:
            st.success("No products in the current filter require replenishment.")
        else:
            urgent["out_first"] = urgent["is_out_of_stock"].astype(int)
            urgent["coverage_sort"] = urgent["estimated_stock_coverage_days"].fillna(float("inf"))
            urgent = urgent.sort_values(
                ["out_first", "coverage_sort", "suggested_reorder_units"],
                ascending=[False, True, False],
            )
            st.dataframe(
                urgent[[
                    "product_id", "product_name", "category", "warehouse_location",
                    "stock_quantity", "reorder_level", "units_sold_90d",
                    "estimated_stock_coverage_days", "suggested_reorder_units",
                    "is_out_of_stock", "needs_reorder",
                ]].head(25),
                hide_index=True, width="stretch",
            )
            st.caption("Priority: out-of-stock first, then lowest estimated coverage. Suggestions are model estimates, not purchase orders.")

        st.subheader("Inventory Distribution")
        c1, c2 = st.columns(2)
        with c1:
            status_counts = (
                filtered.groupby("inventory_status", dropna=False)
                .size().reset_index(name="products")
            )
            fig = px.bar(status_counts, x="inventory_status", y="products",
                         labels={"inventory_status": "Inventory status", "products": "Products"})
            st.plotly_chart(fig, width="stretch")
        with c2:
            category_counts = (
                filtered.groupby("category", dropna=False)
                .agg(stock_units=("stock_quantity", "sum"))
                .reset_index().sort_values("stock_units", ascending=False)
            )
            fig = px.bar(category_counts, x="category", y="stock_units",
                         labels={"category": "Category", "stock_units": "Stock units"})
            st.plotly_chart(fig, width="stretch")

        st.subheader("Stock Coverage")
        coverage = filtered.loc[
            filtered["estimated_stock_coverage_days"].notna()
            & (filtered["estimated_stock_coverage_days"] >= 0)
        ].copy()
        if coverage.empty:
            st.info("No estimated stock-coverage values are available.")
        else:
            fig = px.histogram(
                coverage, x="estimated_stock_coverage_days", nbins=30,
                labels={"estimated_stock_coverage_days": "Estimated coverage (days)"},
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("Coverage is estimated using the Gold model's 90-day sales-rate calculation.")

        st.subheader("Inventory Data Explorer")
        view_cols = [
            "product_id", "product_name", "category", "warehouse_location",
            "inventory_status", "is_active", "stock_quantity", "reorder_level",
            "paid_orders_90d", "units_sold_90d", "estimated_stock_coverage_days",
            "suggested_reorder_units", "is_low_stock", "is_out_of_stock",
            "needs_reorder", "snapshot_date",
        ]
        display = filtered[view_cols].sort_values("stock_quantity", ascending=True)
        st.dataframe(display, hide_index=True, width="stretch")
        st.download_button(
            "Download filtered inventory CSV", display.to_csv(index=False).encode("utf-8-sig"),
            "aegisdata_inventory_health.csv", "text/csv", key="inventory_download",
        )

        with st.expander("Data Quality and Diagnostics"):
            st.write("Source rows (latest snapshot):", len(df))
            st.write("Selected rows:", len(filtered))
            st.write("Unique product IDs:", df["product_id"].nunique())
            st.write("Duplicate product IDs:", int(df["product_id"].duplicated().sum()))
            st.write("Missing stock quantities:", int(df["stock_quantity"].isna().sum()))
            st.write("Negative stock quantities:", int((df["stock_quantity"] < 0).sum()))
            st.write("Missing estimated coverage:", int(df["estimated_stock_coverage_days"].isna().sum()))
            st.caption("Verify stock-risk flags and suggested reorder quantities against the Gold business rules before operational use.")
    except Exception as exc:
        st.error("Unable to load Inventory Health from AWS Athena.")
        st.exception(exc)
        st.info("Check AWS profile/permissions and aegisdata_gold_db.fact_inventory_health.")


if __name__ == "__main__":
    st.set_page_config(page_title="AegisData AI - Inventory Health", layout="wide")
    render_inventory_health()
