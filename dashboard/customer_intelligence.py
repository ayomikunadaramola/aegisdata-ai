
"""Customer Intelligence dashboard for AegisData AI.

Data source:
    aegisdata_gold_db.dim_customer_segments

    Uses the existing cached query function from dashboard/app.py.
    """

import pandas as pd
import plotly.express as px
import streamlit as st


CUSTOMER_SQL = """
SELECT
customer_id,
customer_name,
city,
customer_segment,
signup_date,
paid_order_count,
units_purchased,
historical_paid_spend,
first_paid_order_at,
last_paid_order_at,
recency_days,
customer_value_segment
FROM aegisdata_gold_db.dim_customer_segments
"""


def load_customers(query):
    """Retrieve and prepare customer data from Athena."""

    df = query(CUSTOMER_SQL).copy()

    if df.empty:
        return df

    numeric_columns = [
        "paid_order_count",
        "units_purchased",
        "historical_paid_spend",
        "recency_days",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

        for column in [
            "signup_date",
            "first_paid_order_at",
            "last_paid_order_at",
        ]:
            df[column] = pd.to_datetime(
                df[column],
                errors="coerce",
                utc=True,
            )

            category_columns = [
                "city",
                "customer_segment",
                "customer_value_segment",
            ]

            for column in category_columns:
                df[column] = (
                    df[column]
                    .fillna("Unknown")
                    .astype(str)
                    .str.strip()
                    .replace("", "Unknown")
                )

                df["customer_id"] = df["customer_id"].astype(str)

                return df


def money(value):
    """Format Nigerian Naira amounts."""
    return f"₦{value:,.2f}"


def compact_money(value):
    """Format large currency amounts for KPI cards."""

    if abs(value) >= 1_000_000_000:
        return f"₦{value / 1_000_000_000:,.2f}B"

    if abs(value) >= 1_000_000:
        return f"₦{value / 1_000_000:,.2f}M"

    if abs(value) >= 1_000:
        return f"₦{value / 1_000:,.2f}K"

    return money(value)


def render_customer_intelligence(query):
    """Render the Customer Intelligence dashboard."""

    st.header("Customer Intelligence")

    st.caption(
        "Source: AWS Athena • "
        "aegisdata_gold_db.dim_customer_segments"
    )

    customers = load_customers(query)
    
    # Ensure Athena numeric values are correctly converted
    numeric_columns = [
        "paid_order_count",
        "units_purchased",
        "historical_paid_spend",
        "recency_days",
    ]

    for col in numeric_columns:
        customers[col] = pd.to_numeric(
            customers[col],
            errors="coerce"
        )

    if customers.empty:
        st.warning(
            "No customer records were returned from Athena."
        )
        return

    # -----------------------------------------
    # INTERACTIVE FILTERS
    # -----------------------------------------

    st.subheader("Customer Filters")

    col1, col2 = st.columns(2)

    segments = sorted(
        customers["customer_segment"].unique().tolist()
    )

    cities = sorted(
        customers["city"].unique().tolist()
    )

    with col1:
        selected_segments = st.multiselect(
            "Customer Segment",
            options=segments,
            default=segments,
        )

        with col2:
            selected_cities = st.multiselect(
                "Customer City",
                options=cities,
                default=cities,
            )

            filtered = customers[
                customers["customer_segment"].isin(selected_segments)
                & customers["city"].isin(selected_cities)
            ].copy()

            if filtered.empty:
                st.warning(
                    "No customers match the selected filters."
                )
                return

            # -----------------------------------------
            # CUSTOMER KPIs
            # -----------------------------------------

            customer_count = filtered["customer_id"].nunique()

            paying_customers = int(
                filtered.loc[
                    filtered["paid_order_count"] > 0,
                    "customer_id",
                ].nunique()
            )

            repeat_customers = int(
                filtered.loc[
                    filtered["paid_order_count"] > 1,
                    "customer_id",
                ].nunique()
            )

            total_spend = float(
                pd.to_numeric(
                    filtered["historical_paid_spend"],
                    errors="coerce"
                ).fillna(0).sum()
            )

            avg_spend = (
                total_spend / customer_count
                if customer_count else 0
            )

            repeat_rate = (
                100 * repeat_customers / paying_customers
                if paying_customers else 0
            )

            st.subheader("Customer KPIs")

            a, b, c, d = st.columns(4)

            a.metric(
                "Total Customers",
                f"{customer_count:,}",
            )

            b.metric(
                "Paying Customers",
                f"{paying_customers:,}",
            )

            c.metric(
                "Repeat Customers",
                f"{repeat_customers:,}",
            )

            d.metric(
                "Historical Paid Spend",
                compact_money(total_spend),
                help=money(total_spend),
            )

            e, f = st.columns(2)

            e.metric(
                "Average Spend per Customer",
                compact_money(avg_spend),
            )

            f.metric(
                "Repeat Buyer Rate",
                f"{repeat_rate:.1f}%",
                help="Customers with more than one paid order "
                "divided by paying customers.",
            )

            st.caption(
                "Metrics reflect the selected customer filters. "
                "Spend is historical paid spend from the Gold "
                "customer dimension, not a date-filtered measure."
            )

            st.divider()

            # -----------------------------------------
            # CUSTOMER SEGMENTATION
            # -----------------------------------------

            st.subheader("Customer Segmentation")

            value_counts = (
                filtered.groupby(
                    "customer_value_segment",
                    dropna=False,
                )
                .agg(
                    customers=("customer_id", "nunique"),
                    paid_spend=("historical_paid_spend", "sum"),
                )
                .reset_index()
            )

            left, right = st.columns(2)

            with left:
                fig = px.pie(
                    value_counts,
                    names="customer_value_segment",
                    values="customers",
                    hole=0.45,
                    title="Customer Value Segments",
                )

                st.plotly_chart(
                    fig,
                    width="stretch",
                )

                with right:
                    fig = px.bar(
                        value_counts.sort_values(
                            "paid_spend",
                            ascending=False,
                        ),
                        x="customer_value_segment",
                        y="paid_spend",
                        title="Historical Spend by Value Segment",
                        labels={
                            "customer_value_segment": "Value Segment",
                            "paid_spend": "Paid Spend (₦)",
                        },
                    )

                    st.plotly_chart(
                        fig,
                        width="stretch",
                    )

                    # -----------------------------------------
                    # GEOGRAPHIC INTELLIGENCE
                    # -----------------------------------------

                    st.subheader("Geographic Customer Distribution")

                    city_summary = (
                        filtered.groupby("city")
                        .agg(
                            customers=("customer_id", "nunique"),
                            paid_spend=("historical_paid_spend", "sum"),
                        )
                        .reset_index()
                        .sort_values("customers", ascending=False)
                    )

                    fig = px.bar(
                        city_summary,
                        x="city",
                        y="customers",
                        title="Customers by City",
                        labels={
                            "city": "City",
                            "customers": "Customer Count",
                        },
                    )

                    st.plotly_chart(fig, width="stretch")

                    with st.expander("Explore city-level spending"):
                        st.dataframe(
                            city_summary,
                            width="stretch",
                            hide_index=True,
                        )

                        # -----------------------------------------
                        # PURCHASE BEHAVIOUR
                        # -----------------------------------------

                        st.subheader("Customer Purchase Behaviour")

                        left, right = st.columns(2)

                        with left:
                            fig = px.histogram(
                                filtered,
                                x="paid_order_count",
                                nbins=30,
                                title="Paid Orders per Customer",
                                labels={
                                    "paid_order_count": "Paid Orders",
                                    "count": "Customers",
                                },
                            )

                            st.plotly_chart(fig, width="stretch")

                            with right:
                                fig = px.scatter(
                                    filtered,
                                    x="paid_order_count",
                                    y="historical_paid_spend",
                                    color="customer_value_segment",
                                    title="Purchase Frequency vs Historical Spend",
                                    labels={
                                        "paid_order_count": "Paid Orders",
                                        "historical_paid_spend": "Spend (₦)",
                                        "customer_value_segment": "Value Segment",
                                    },
                                    hover_data=["customer_id", "city"],
                                )

                                st.plotly_chart(fig, width="stretch")

                                # -----------------------------------------
                                # CUSTOMER RECENCY
                                # -----------------------------------------

                                st.subheader("Customer Recency Analysis")

                                recency = filtered.dropna(
                                    subset=["recency_days"]
                                ).copy()

                                if not recency.empty:
                                    fig = px.histogram(
                                        recency,
                                        x="recency_days",
                                        nbins=30,
                                        title="Days Since Last Paid Order",
                                        labels={
                                            "recency_days": "Recency (Days)",
                                            "count": "Customers",
                                        },
                                    )

                                    st.plotly_chart(fig, width="stretch")
                                else:
                                    st.info(
                                        "No customer recency values are available."
                                    )

                                    # -----------------------------------------
                                    # CUSTOMER SIGNUP TREND
                                    # -----------------------------------------

                                    st.subheader("Customer Acquisition Trend")

                                    signups = filtered.dropna(
                                        subset=["signup_date"]
                                    ).copy()

                                    if not signups.empty:
                                        signups["signup_month"] = (
                                            signups["signup_date"]
                                            .dt.tz_localize(None)
                                            .dt.to_period("M")
                                            .dt.to_timestamp()
                                        )

                                        monthly_signups = (
                                            signups.groupby("signup_month")
                                            .agg(
                                                customers=("customer_id", "nunique")
                                            )
                                            .reset_index()
                                            .sort_values("signup_month")
                                        )

                                        fig = px.line(
                                            monthly_signups,
                                            x="signup_month",
                                            y="customers",
                                            markers=True,
                                            title="Monthly Customer Signups",
                                            labels={
                                                "signup_month": "Month",
                                                "customers": "New Customers",
                                            },
                                        )

                                        st.plotly_chart(fig, width="stretch")

                                        # -----------------------------------------
                                        # HIGH-VALUE CUSTOMERS
                                        # -----------------------------------------

                                        st.subheader("Top Customers by Historical Spend")

                                        top_customers = (
                                            filtered.sort_values(
                                                "historical_paid_spend",
                                                ascending=False,
                                            )
                                            .head(20)
                                        )

                                        display_columns = [
                                            "customer_id",
                                            "customer_name",
                                            "city",
                                            "customer_segment",
                                            "customer_value_segment",
                                            "paid_order_count",
                                            "historical_paid_spend",
                                            "recency_days",
                                        ]

                                        st.dataframe(
                                            top_customers[display_columns],
                                            width="stretch",
                                            hide_index=True,
                                        )

                                        # -----------------------------------------
                                        # RAW DATA EXPLORER
                                        # -----------------------------------------

                                        with st.expander("Explore Customer Dataset"):
                                            st.dataframe(
                                                filtered,
                                                width="stretch",
                                                hide_index=True,
                                            )

                                            csv = filtered.to_csv(index=False)

                                            st.download_button(
                                                label="Download Filtered Customer Data",
                                                data=csv,
                                                file_name="aegisdata_customer_intelligence.csv",
                                                mime="text/csv",
                                            )

                                            st.caption(
                                                "AegisData AI • Customer Intelligence • "
                                                "Powered by AWS Athena and Streamlit"
                                            )
