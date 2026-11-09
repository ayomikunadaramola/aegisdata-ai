
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px

# Project configuration
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = PROJECT_ROOT / "data" / "processed" / "gold"

st.set_page_config(
    page_title="AegisData AI | Analytics Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("AegisData AI")
st.caption("Intelligent Data Engineering & Business Analytics Platform")

st.sidebar.title("AegisData AI")
st.sidebar.markdown("### Navigation")

page = st.sidebar.radio(
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

st.divider()

if page == "Executive Overview":
    st.header("Executive Overview")
    st.info("Revenue, customers, orders and business KPIs will appear here.")

elif page == "Revenue Analytics":
    st.header("Revenue Analytics")
    st.info("Daily and monthly revenue trends will appear here.")

elif page == "Customer Intelligence":
    st.header("Customer Intelligence")
    st.info("Customer segmentation and purchasing behavior.")

elif page == "Product Performance":
    st.header("Product Performance")
    st.info("Top products, sales and category performance.")

elif page == "Inventory Health":
    st.header("Inventory Health")
    st.info("Stock coverage, reorder priorities and inventory alerts.")

elif page == "Payment Analytics":
    st.header("Payment Analytics")
    st.info("Payment success rates and transaction performance.")

    st.sidebar.divider()
    st.sidebar.caption("AegisData AI | Portfolio Project")
