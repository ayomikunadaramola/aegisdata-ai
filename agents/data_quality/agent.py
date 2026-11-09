"""Phase 8B: offline AegisData AI Data Quality Agent.

Reads a report without modifying it. Produces deterministic, explainable
findings (no Bedrock calls, AWS credentials, or auto-remediation).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .rules import get_check_status, recommendation_for, severity_for

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "validation" / "gold_validation_latest.json"
STATUSES = ("PASS", "FAIL", "WARN", "ERROR", "UNKNOWN")


def _checks(raw: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """Supports a list or a name-to-check dictionary; other schemas fail closed."""
    if isinstance(raw, list):
        records: list[tuple[str, Mapping[str, Any]]] = []
        for pos, item in enumerate(raw, 1):
            if not isinstance(item, Mapping):
                raise ValueError(f"checks[{pos - 1}] is not an object")
            name = next((str(item[key]) for key in ("name", "check_name", "check", "id")
                         if item.get(key) not in (None, "")), f"check_{pos}")
            records.append((name, item))
        return records
    if isinstance(raw, Mapping):
        records = []
        for name, item in raw.items():
            if not isinstance(item, Mapping):
                raise ValueError(f"checks[{name!r}] is not an object")
            records.append((str(name), item))
        return records
    raise ValueError("Report 'checks' must be a list or mapping")


def _report_counts(report: Mapping[str, Any]) -> dict[str, int] | None:
    """Extract known report summary count shapes without manufacturing zeros."""
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        return None
    source: Mapping[str, Any] = summary
    for key in ("counts", "results"):
        if isinstance(summary.get(key), Mapping):
            source = summary[key]
            break
    counts: dict[str, int] = {}
    for status in ("PASS", "FAIL", "WARN", "ERROR"):
        raw = next((value for key, value in source.items()
                    if str(key).upper() == status), None)
        if raw is None:
            continue
        if isinstance(raw, bool):
            raise ValueError(f"Summary count {status} must be an integer")
        try:
            converted = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Summary count {status} must be an integer") from exc
        if converted < 0 or str(raw).strip() not in (str(converted), f"{converted}.0"):
            raise ValueError(f"Summary count {status} must be nonnegative integer")
        counts[status] = converted
    return counts or None


def analyze_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe findings and report-check reconciliation diagnostics."""
    if not isinstance(report, Mapping):
        raise TypeError("Validation report must be a JSON object")
    entries = _checks(report.get("checks"))
    counts = Counter()
    findings: list[dict[str, Any]] = []
    for index, (name, check) in enumerate(entries, 1):
        status = get_check_status(check)
        counts[status] += 1
        if status == "PASS":
            continue
        action, impact = recommendation_for(name, status)
        finding: dict[str, Any] = {
            "check_index": index,
            "check_name": name,
            "status": status,
            "severity": severity_for(status),
            "possible_impact": impact,
            "recommended_action": action,
            "root_cause_confirmed": False,
        }
        for key in ("message", "actual", "expected"):
            value = check.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                if key in check:
                    finding[key] = value
        findings.append(finding)

    report_counts = _report_counts(report)
    observed = {s: counts[s] for s in STATUSES}
    mismatch = None
    if report_counts is not None:
        mismatch = {s: {"summary": num, "checks": observed[s]}
                    for s, num in report_counts.items() if num != observed[s]} or None

    if observed["ERROR"] or observed["FAIL"]:
        decision = "FAIL"
    elif observed["UNKNOWN"]:
        decision = "UNKNOWN"
    elif observed["WARN"]:
        decision = "WARN"
    else:
        decision = "PASS"
    # Never claim PASS when the report-level counts show problems or disagree.
    if report_counts:
        if report_counts.get("FAIL", 0) or report_counts.get("ERROR", 0):
            decision = "FAIL"
        elif mismatch:
            decision = "UNKNOWN"
        elif report_counts.get("WARN", 0) and decision == "PASS":
            decision = "WARN"
    if not entries:
        decision = "UNKNOWN"

    return {
        "agent": "aegisdata_data_quality",
        "version": "8B",
        "status": decision,
        "checks_inspected": len(entries),
        "observed_counts": observed,
        "report_summary_counts": report_counts,
        "summary_mismatch": mismatch,
        "findings": findings,
        "limitations": [
            "Recommendations are deterministic triage hints, not verified root causes.",
            "No AWS actions or data changes have been performed.",
            "UNKNOWN means further schema or validation review is required.",
        ],
    }


def analyze_default_report() -> dict[str, Any]:
    """Read the fixed, local Gold validation report (no arbitrary file access)."""
    if not DEFAULT_REPORT.is_file():
        raise FileNotFoundError(f"Missing Gold validation report: {DEFAULT_REPORT}")
    with DEFAULT_REPORT.open("r", encoding="utf-8") as stream:
        content = json.load(stream)
    return analyze_report(content)


if __name__ == "__main__":
    print(json.dumps(analyze_default_report(), indent=2, ensure_ascii=False))
