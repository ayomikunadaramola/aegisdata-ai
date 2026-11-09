"""AegisData AI — Payment Analytics dashboard.

Called by dashboard/app.py using render_payment_analytics(query).
The query callable must accept read-only SQL and return a pandas DataFrame.
Source: aegisdata_gold_db.fact_payment_performance.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

DATABASE = "aegisdata_gold_db"
TABLE = f"{DATABASE}.fact_payment_performance"
EXPECTED_PAYMENT_ROWS = 2351  # Demo benchmark, not a hard-coded data requirement.

COUNT_COLUMNS = [
    "total_transactions",
    "successful_transactions",
    "failed_transactions",
    "pending_transactions",
]
AMOUNT_COLUMNS = [
    "total_attempted_amount",
    "successful_payment_amount",
    "failed_payment_amount",
    "pending_payment_amount",
]
COLUMNS = [
    "payment_date_utc",
    "payment_method",
    *COUNT_COLUMNS,
    *AMOUNT_COLUMNS,
    "payment_success_rate_pct",
]


def load_payments(query: Callable[[str], pd.DataFrame]) -> pd.DataFrame:
    """Load every row through app.py's paginated Athena query callable."""
    sql = f"""
        SELECT
            payment_date_utc,
            payment_method,
            total_transactions,
            successful_transactions,
            failed_transactions,
            pending_transactions,
            total_attempted_amount,
            successful_payment_amount,
            failed_payment_amount,
            pending_payment_amount,
            payment_success_rate_pct
        FROM {TABLE}
        ORDER BY payment_date_utc, payment_method
    """
    result = query(sql)
    if not isinstance(result, pd.DataFrame):
        raise TypeError("Athena query must return a pandas DataFrame.")

    missing = set(COLUMNS) - set(result.columns)
    if missing:
        raise ValueError(f"Missing payment columns: {sorted(missing)}")

    payments = result[COLUMNS].copy()
    payments["payment_date_utc"] = pd.to_datetime(
        payments["payment_date_utc"], errors="coerce", utc=True
    )
    payments["payment_method"] = (
        payments["payment_method"].fillna("Unknown").astype(str).str.strip()
        .replace("", "Unknown")
    )
    for col in COUNT_COLUMNS + AMOUNT_COLUMNS + ["payment_success_rate_pct"]:
        payments[col] = pd.to_numeric(payments[col], errors="coerce")

    # Important: return AFTER processing every numeric column.
    return payments


def aggregate_metrics(df: pd.DataFrame) -> dict[str, float | int]:
    """Calculate transaction-weighted metrics, never mean of row percentages."""
    totals = df[COUNT_COLUMNS + AMOUNT_COLUMNS].sum()
    attempts = int(totals["total_transactions"])
    successful = int(totals["successful_transactions"])
    failed = int(totals["failed_transactions"])
    pending = int(totals["pending_transactions"])
    attempted_amount = float(totals["total_attempted_amount"])
    successful_amount = float(totals["successful_payment_amount"])
    failed_amount = float(totals["failed_payment_amount"])
    pending_amount = float(totals["pending_payment_amount"])

    return {
        "attempts": attempts,
        "successful": successful,
        "failed": failed,
        "pending": pending,
        "attempted_amount": attempted_amount,
        "successful_amount": successful_amount,
        "failed_amount": failed_amount,
        "pending_amount": pending_amount,
        "success_rate": (100 * successful / attempts) if attempts else 0.0,
        "failure_rate": (100 * failed / attempts) if attempts else 0.0,
        "pending_rate": (100 * pending / attempts) if attempts else 0.0,
        "avg_successful_payment": (
            successful_amount / successful if successful else 0.0
        ),
    }


def money(value: float) -> str:
    """Format NGN amounts compactly for Streamlit KPI cards."""
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"₦{value / 1_000_000_000:,.2f}B"
    if abs(value) >= 1_000_000:
        return f"₦{value / 1_000_000:,.2f}M"
    return f"₦{value:,.2f}"


def _add_success_rate(frame: pd.DataFrame) -> pd.DataFrame:
    """Add transaction-weighted success rate to a grouped DataFrame."""
    frame = frame.copy()
    denominator = frame["total_transactions"].replace(0, float("nan"))
    frame["success_rate_pct"] = (
        100 * frame["successful_transactions"] / denominator
    ).fillna(0.0)
    return frame


def render_payment_analytics(query: Callable[[str], pd.DataFrame]) -> None:
    """Render Payment Analytics exactly once per Streamlit page execution."""
    st.header("Payment Analytics")
    st.caption(f"Source: AWS Athena · {TABLE} · Reporting dates in UTC")

    try:
        payments = load_payments(query)
        if payments.empty:
            st.warning("No payment records were returned from AWS Athena.")
            return

        raw_count = len(payments)
        invalid_dates = int(payments["payment_date_utc"].isna().sum())
        numeric_columns = COUNT_COLUMNS + AMOUNT_COLUMNS
        missing_numeric_rows = int(
            payments[numeric_columns].isna().any(axis=1).sum()
        )

        payments = payments.dropna(subset=["payment_date_utc"]).copy()
        if payments.empty:
            st.error("No valid payment dates are available.")
            return

        for col in numeric_columns:
            payments[col] = payments[col].fillna(0)

        # Filters are OUTSIDE the numeric-conversion loop.
        # Each widget is constructed only once per page execution.
        st.subheader("Payment Filters")
        left, right = st.columns(2)
        min_date = payments["payment_date_utc"].min().date()
        max_date = payments["payment_date_utc"].max().date()

        with left:
            selected_dates = st.date_input(
                "Payment reporting period",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key="payment_reporting_period",
            )
        with right:
            methods = sorted(payments["payment_method"].unique().tolist())
            selected_methods = st.multiselect(
                "Payment Methods",
                options=methods,
                default=methods,
                key="payment_methods",
            )

        if isinstance(selected_dates, (tuple, list)):
            if len(selected_dates) != 2:
                st.info("Select both dates to complete the reporting period.")
                return
            start_date, end_date = selected_dates
        else:
            start_date = end_date = selected_dates

        if start_date > end_date:
            st.warning("Start date cannot exceed end date.")
            return

        filtered = payments.loc[
            payments["payment_date_utc"].dt.date.between(start_date, end_date)
            & payments["payment_method"].isin(selected_methods)
        ].copy()
        st.caption(
            f"Showing {len(filtered):,} of {raw_count:,} payment performance records."
        )
        if filtered.empty:
            st.info("No payments match the selected filters.")
            return

        metrics = aggregate_metrics(filtered)

        st.subheader("Payment KPIs")
        a, b, c, d = st.columns(4)
        a.metric("Total Transactions", f"{metrics['attempts']:,}")
        b.metric("Successful Transactions", f"{metrics['successful']:,}")
        c.metric("Failed Transactions", f"{metrics['failed']:,}")
        d.metric("Pending Transactions", f"{metrics['pending']:,}")

        a, b, c, d = st.columns(4)
        a.metric("Payment Success Rate", f"{metrics['success_rate']:.2f}%")
        b.metric("Payment Failure Rate", f"{metrics['failure_rate']:.2f}%")
        c.metric("Successful Payment Value", money(metrics["successful_amount"]))
        d.metric(
            "Average Successful Payment", money(metrics["avg_successful_payment"])
        )
        st.caption(
            "Success and failure rates are transaction-weighted. "
            "NGN display assumes the Gold payment amounts are in naira."
        )

        st.subheader("Payment Amount Breakdown")
        a, b, c, d = st.columns(4)
        a.metric("Attempted Payment Value", money(metrics["attempted_amount"]))
        b.metric("Successful Payment Value", money(metrics["successful_amount"]))
        c.metric("Failed Payment Value", money(metrics["failed_amount"]))
        d.metric("Pending Payment Value", money(metrics["pending_amount"]))

        st.subheader("Daily Payment Trends")
        daily = (
            filtered.assign(payment_date=filtered["payment_date_utc"].dt.date)
            .groupby("payment_date", as_index=False)[numeric_columns]
            .sum()
        )
        daily["payment_date"] = pd.to_datetime(daily["payment_date"])
        daily = _add_success_rate(daily.sort_values("payment_date"))
        daily_indexed = daily.set_index("payment_date")

        st.markdown("#### Successful Payment Value")
        st.line_chart(daily_indexed[["successful_payment_amount"]], width="stretch")
        a, b = st.columns(2)
        with a:
            st.markdown("#### Payment Transaction Volumes")
            st.bar_chart(
                daily_indexed[
                    ["successful_transactions", "failed_transactions", "pending_transactions"]
                ],
                width="stretch",
            )
        with b:
            st.markdown("#### Daily Payment Success Rate (%)")
            st.line_chart(daily_indexed[["success_rate_pct"]], width="stretch")

        st.subheader("Payment Method Performance")
        by_method = (
            filtered.groupby("payment_method", as_index=False)[numeric_columns]
            .sum()
            .sort_values("successful_payment_amount", ascending=False)
        )
        by_method = _add_success_rate(by_method)
        by_method_indexed = by_method.set_index("payment_method")
        a, b = st.columns(2)
        with a:
            st.markdown("#### Successful Value by Method")
            st.bar_chart(
                by_method_indexed[["successful_payment_amount"]], width="stretch"
            )
        with b:
            st.markdown("#### Success Rate by Method (%)")
            st.bar_chart(by_method_indexed[["success_rate_pct"]], width="stretch")
        st.dataframe(by_method, width="stretch", hide_index=True)

        st.subheader("Failed and Pending Payments")
        a, b = st.columns(2)
        with a:
            st.markdown("#### Failed Transactions by Method")
            st.bar_chart(by_method_indexed[["failed_transactions"]], width="stretch")
        with b:
            st.markdown("#### Pending Transactions by Method")
            st.bar_chart(by_method_indexed[["pending_transactions"]], width="stretch")

        st.subheader("Payment Data Explorer")
        displayed = filtered[COLUMNS].sort_values(
            "payment_date_utc", ascending=False
        )
        st.dataframe(displayed, width="stretch", hide_index=True)
        st.download_button(
            "Download Filtered Payment Data CSV",
            data=displayed.to_csv(index=False).encode("utf-8-sig"),
            file_name="aegisdata_payment_analytics.csv",
            mime="text/csv",
            key="download_payment_csv",
        )

        with st.expander("Data Quality and Diagnostics"):
            st.write(f"Gold payment rows retrieved: **{raw_count:,}**")
            st.write(f"Original dataset benchmark: **{EXPECTED_PAYMENT_ROWS:,}**")
            st.write(f"Rows after filters: **{len(filtered):,}**")
            st.write(f"Invalid payment dates: **{invalid_dates:,}**")
            st.write(
                "Rows containing missing numeric values: "
                f"**{missing_numeric_rows:,}**"
            )
            if raw_count != EXPECTED_PAYMENT_ROWS:
                st.warning(
                    "Payment record count differs from the original demo benchmark. "
                    "Check whether a newer Gold run is being queried."
                )
            if invalid_dates or missing_numeric_rows:
                st.warning(
                    "Invalid-date rows were excluded and missing numeric values "
                    "were treated as zero for reporting. Review the source data."
                )

            transaction_difference = (
                metrics["attempts"] - metrics["successful"]
                - metrics["failed"] - metrics["pending"]
            )
            st.write(
                "Transaction count reconciliation difference: "
                f"**{transaction_difference:,}**"
            )
            if transaction_difference:
                st.warning(
                    "Total transactions do not equal successful + failed + pending."
                )
            else:
                st.success("Transaction status counts reconcile.")

            amount_difference = (
                metrics["attempted_amount"] - metrics["successful_amount"]
                - metrics["failed_amount"] - metrics["pending_amount"]
            )
            st.write(
                "Payment amount reconciliation difference: "
                f"**₦{amount_difference:,.2f}**"
            )
            if abs(amount_difference) > 0.01:
                st.warning(
                    "Payment amounts do not fully reconcile. "
                    "Review the Gold aggregation rules."
                )
            else:
                st.success("Payment status amounts reconcile.")

            negative_rows = int((filtered[numeric_columns] < 0).any(axis=1).sum())
            st.write(f"Rows with negative counts or amounts: **{negative_rows:,}**")
            if negative_rows:
                st.warning(
                    "Negative amounts or counts detected. "
                    "Confirm whether adjustments are expected."
                )

    except Exception as exc:
        st.error("Unable to load Payment Analytics from AWS Athena.")
        st.exception(exc)
