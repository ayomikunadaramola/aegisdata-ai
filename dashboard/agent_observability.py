"""Phase 8F: read-only Streamlit operational metrics from local sanitized JSONL."""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.data_quality.phase8f_observability import DEFAULT_AUDIT_LOG, read_events  # noqa: E402


def render_agent_observability() -> None:
    st.header("Agent Observability")
    st.caption("Phase 8F · Local operational audit log · Read-only · No AWS calls")
    st.caption(f"Log: {DEFAULT_AUDIT_LOG.relative_to(ROOT)}")
    try:
        entries = read_events(limit=500)
    except (OSError, ValueError, UnicodeError):
        st.error("Unable to read the local audit log. No AWS request was made.")
        return
    if not entries:
        st.info("No recorded agent runs yet. Generate an optional Bedrock explanation in AI Data Quality to record one.")
        return
    frame = pd.DataFrame(entries)
    attempts = frame[frame["opted_in"] == True]  # noqa: E712
    successes = int((attempts["mode"] == "bedrock").sum())
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
