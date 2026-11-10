"""Phases 8F/8G: read-only Streamlit agent audit and reliability metrics."""
from __future__ import annotations

from pathlib import Path
import json
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.data_quality.phase8f_observability import DEFAULT_AUDIT_LOG, read_events  # noqa: E402
from agents.data_quality.phase8g_evaluation import evaluate_events  # noqa: E402


def render_agent_observability() -> None:
    st.header("Agent Observability")
    st.caption("Phases 8F/8G · Local operational audit and reliability evaluation · Read-only · No AWS calls")
    st.caption(f"Log: {DEFAULT_AUDIT_LOG.relative_to(ROOT)}")
    try:
        entries = read_events(limit=2000)
    except (OSError, ValueError, UnicodeError):
        st.error("Unable to read the local audit log. No AWS request was made.")
        return

    # Phase 8G cannot substitute for an external production monitor. No AWS calls.
    st.subheader("Agent reliability evaluation — Phase 8G")
    st.caption("Rolling 7-day sample. Provisional SLOs: at least 5 attempts, ≥95% success, "
               "p95 latency ≤10 seconds, rate-limited attempts ≤20%.")
    report = evaluate_events(entries)
    status = report["status"]
    if status == "HEALTHY":
        st.success("Observed reliability meets the configured provisional thresholds.")
    elif status == "ALERT":
        st.error("One or more observed reliability thresholds were exceeded.")
    else:
        st.info("Insufficient recent attempts to assess reliability; no health claim is made.")
    cols = st.columns(4)
    cols[0].metric("Reliability status", status)
    cols[1].metric("7-day live attempts", report["live_attempts"])
    cols[2].metric("7-day success rate", f"{100 * report['success_rate']:.1f}%" if report["success_rate"] is not None else "N/A")
    cols[3].metric("7-day p95 latency", f"{report['p95_latency_ms']:,.0f} ms" if report["p95_latency_ms"] is not None else "N/A")
    if report["alerts"]:
        st.warning("Reliability alerts: " + ", ".join(report["alerts"]))
    with st.expander("Evaluation thresholds, errors, and limitations"):
        st.json({
            "thresholds": report["thresholds"],
            "error_categories": report["error_categories"],
            "limitations": report["limitations"],
        })
    st.download_button("Download reliability evaluation (JSON)",
                       data=json.dumps(report, indent=2),
                       file_name="aegisdata_phase8g_reliability.json",
                       mime="application/json", key="phase8g_reliability_download")

    st.divider()
    st.subheader("Phase 8F — Recorded agent activity")
    if not entries:
        st.info("No recorded agent runs yet. Generate an optional Bedrock explanation in AI Data Quality to record one.")
        return
    frame = pd.DataFrame(entries)
    attempts = frame[frame["opted_in"] == True]  # noqa: E712
    successes = int(((attempts["mode"] == "bedrock") & attempts["error_category"].isna()).sum())
    cols = st.columns(4)
    cols[0].metric("Recorded events", len(frame))
    cols[1].metric("Live attempts", len(attempts))
    cols[2].metric("Successful live requests", successes)
    cols[3].metric("Live success rate", f"{100 * successes / len(attempts):.1f}%" if len(attempts) else "N/A")

    st.subheader("Bedrock usage")
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    token_cols = st.columns(3)
    for col, (key, label) in zip(token_cols, [
        ("input_tokens", "Input tokens"),
        ("output_tokens", "Output tokens"),
        ("total_tokens", "Total tokens"),
    ]):
        col.metric(label, f"{int(frame[key].sum()):,}")
    st.caption("Token totals include only returned usage values; missing usage is not estimated. "
               "The local audit log is best-effort and not a tamper-proof billing ledger.")

    st.subheader("Execution outcomes")
    outcomes = frame.groupby("mode", as_index=False).size().rename(columns={"size": "events"})
    st.bar_chart(outcomes.set_index("mode")["events"])

    st.subheader("Recent audit events")
    display = frame.sort_values("timestamp_utc", ascending=False)
    st.dataframe(display, hide_index=True, width="stretch")
    st.download_button("Download sanitized events (CSV)",
                       data=display.to_csv(index=False).encode("utf-8"),
                       file_name="aegisdata_phase8f_audit.csv", mime="text/csv",
                       key="agent_observability_csv")
