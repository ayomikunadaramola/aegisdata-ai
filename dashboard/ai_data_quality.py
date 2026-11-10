"""AegisData AI | Phase 8D/8E: read-only AI Data Quality dashboard.

The local Gold validation report is authoritative. A live Amazon Bedrock
explanation requires explicit opt-in and cannot change validation findings.
Bedrock failures fall back safely without modifying data or triggering repairs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd
import streamlit as st

# Make project imports available when Streamlit launches dashboard/app.py.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.data_quality.agent import DEFAULT_REPORT, analyze_default_report  # noqa: E402
from agents.data_quality.phase8e_guard import guarded_explanation  # noqa: E402


BEDROCK_PROFILE = "aegisdata"
BEDROCK_REGION = "us-east-1"


def summarize_report() -> dict[str, Any]:
    """Evaluate the local Gold report deterministically; no AWS calls."""
    return analyze_default_report()


def invoke_bedrock(
    *,
    profile: str = BEDROCK_PROFILE,
    region: str = BEDROCK_REGION,
    timeout: int = 90,
) -> dict[str, Any]:
    """Execute the existing read-only Phase 8C CLI only on explicit user action.

    Arguments are application-controlled. A subprocess timeout prevents a
    stalled request from blocking the dashboard indefinitely.
    """
    args = [
        sys.executable,
        "-m",
        "agents.data_quality.bedrock_reasoner",
        "--live",
        "--profile",
        profile,
        "--region",
        region,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        # Do not surface raw stderr, which can include sensitive diagnostics.
        raise RuntimeError("Bedrock CLI failed; check your AWS session and terminal logs")

    # The CLI normally emits JSON, but may prepend informational log lines.
    decoder = json.JSONDecoder()
    output = proc.stdout.strip()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            candidate, consumed = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and not output[index + consumed:].strip():
            return candidate
    raise ValueError("Bedrock reasoner did not return a valid JSON object")


def finding_rows(result: dict[str, Any]) -> pd.DataFrame:
    """Build a table using only findings from the deterministic report."""
    columns = [
        "check_name",
        "status",
        "severity",
        "possible_impact",
        "recommended_action",
        "root_cause_confirmed",
    ]
    return pd.DataFrame(result.get("findings") or [], columns=columns)


def render_ai_data_quality() -> None:
    """Render report metrics, deterministic findings, and optional AI prose."""
    st.header("AI Data Quality")
    st.caption("Phase 8E · Local Gold validation report · Read-only diagnostic agent")
    st.caption(f"Report: {DEFAULT_REPORT.relative_to(ROOT)}")

    if not DEFAULT_REPORT.is_file():
        st.error("The Gold validation report does not exist locally. Run Gold validation first.")
        st.code("python tests/validate_dashboard.py", language="bash")
        return

    try:
        result = summarize_report()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        st.error("Unable to interpret the Gold validation report. No AI request was sent.")
        st.exception(exc)
        return

    status = str(result.get("status", "UNKNOWN"))
    counts = result.get("observed_counts") or {}

    st.subheader("Validation overview")
    cols = st.columns(5)
    metrics = [
        ("Overall status", status),
        ("Checks inspected", int(result.get("checks_inspected", 0))),
        ("Passed", int(counts.get("PASS", 0))),
        ("Failed", int(counts.get("FAIL", 0)) + int(counts.get("ERROR", 0))),
        (
            "Warnings / unknown",
            int(counts.get("WARN", 0)) + int(counts.get("UNKNOWN", 0)),
        ),
    ]
    for column, (label, value) in zip(cols, metrics):
        column.metric(label, value)

    if status == "PASS":
        st.success(
            "All inspected checks passed. This does not prove every possible "
            "data issue is absent."
        )
    elif status == "WARN":
        st.warning("The report contains warnings that warrant review.")
    elif status == "FAIL":
        st.error("The report contains failed checks or errors; review the findings.")
    else:
        st.warning("The report is incomplete or inconsistent; status is UNKNOWN.")

    if result.get("summary_mismatch"):
        st.error("Summary counts disagree with individual checks. Review the validation report.")
        st.json(result["summary_mismatch"])

    st.subheader("Deterministic findings")
    findings = finding_rows(result)
    if findings.empty:
        st.info("No non-passing findings in the inspected report.")
    else:
        st.dataframe(findings, width="stretch", hide_index=True)

    with st.expander("Validation metadata and limitations"):
        st.json(
            {
                "report_summary_counts": result.get("report_summary_counts"),
                "observed_counts": counts,
                "limitations": result.get("limitations", []),
            }
        )

    st.divider()
    st.subheader("Optional Amazon Bedrock explanation")
    st.caption(
        "Nova Lite commentary is non-authoritative. It cannot change validation "
        "results or initiate data changes. Running a live request can incur AWS charges."
    )
    enabled = st.checkbox(
        "I want to make a live Bedrock request",
        key="dq_bedrock_opt_in",
    )

    if st.button(
        "Generate AI explanation",
        disabled=not enabled,
        key="dq_run_bedrock",
    ):
        # Copy the trusted values before calling any model-related code.
        trusted = {
            "status": status,
            "observed_counts": counts.copy(),
        }

        def verified_reasoner() -> dict[str, Any]:
            """Adapt the Phase 8C CLI payload to the Phase 8E safety boundary."""
            raw = invoke_bedrock()
            if not isinstance(raw, dict):
                raise ValueError("Invalid Bedrock response")
            if raw.get("mode") != "bedrock":
                raise RuntimeError("Bedrock explanation unavailable")

            # Always pass the returned evidence to the guard. The guard rejects
            # differences under the explicit 'deterministic_mismatch' category.
            remote = raw.get("deterministic")
            if not isinstance(remote, dict):
                remote = {}
            evidence = {
                "status": remote.get("status"),
                "observed_counts": remote.get("observed_counts"),
            }
            return {
                "deterministic": evidence,
                "ai_explanation": raw.get("ai_explanation"),
                "usage": raw.get("usage"),
            }

        with st.spinner("Requesting an explanation from Bedrock..."):
            response = guarded_explanation(
                deterministic=trusted,
                reasoner=verified_reasoner,
                allow_live=enabled,
            )

        if response.get("mode") == "bedrock":
            st.markdown(response["ai_explanation"])
            usage = response.get("usage") or {}
            st.caption(f"Bedrock tokens: {usage.get('totalTokens', 'not reported')}")
        else:
            st.warning(
                "Bedrock explanation unavailable. "
                "Deterministic validation results remain unchanged."
            )
            if response.get("ai_error"):
                st.caption(f"Error category: {response['ai_error']}")

    # The trusted report remains downloadable whether or not AI is used.
    st.download_button(
        "Download deterministic findings (JSON)",
        data=json.dumps(result, indent=2, ensure_ascii=False),
        file_name="aegisdata_data_quality_findings.json",
        mime="application/json",
        key="dq_download",
    )
