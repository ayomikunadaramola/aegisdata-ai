"""Phase 8A: safely load an existing Gold validation report, without AWS calls.

This module does not presume a specific validator's internal JSON layout.
Only the top-level result counts are normalized, when present.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_RELATIVE_PATH = Path("reports/validation/gold_validation_latest.json")
ALLOWED_STATUSES = ("PASS", "FAIL", "WARN", "ERROR")
MAX_REPORT_BYTES = 5 * 1024 * 1024


def load_validation_report(project_root: str | Path, relative_path: str | Path = DEFAULT_RELATIVE_PATH) -> dict[str, Any]:
    """Load a JSON object from the project validation directory only.

    Raises an explicit exception for missing, oversized or invalid reports.
    The original report is not modified.
    """
    root = Path(project_root).resolve()
    allowed_dir = (root / "reports" / "validation").resolve()
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("Absolute report paths are not allowed")
    resolved = (root / requested).resolve()
    if not resolved.is_relative_to(allowed_dir):
        raise ValueError("Report must be inside reports/validation")
    if resolved.suffix.lower() != ".json":
        raise ValueError("Only JSON validation reports are supported")
    if resolved.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("Validation report exceeds size limit")
    with resolved.open("r", encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("Validation report must contain a JSON object")
    return result


def summarize_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary without inventing undocumented checks.

    Recognizes counts under `results` or `summary` (or at the root),
    with exact uppercase or lowercase PASS/FAIL/WARN/ERROR names.
    """
    candidates = [report.get("results"), report.get("summary"), report]
    counts = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        extracted = {}
        for status in ALLOWED_STATUSES:
            value = item.get(status, item.get(status.lower()))
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                extracted[status] = value
        if extracted:
            counts = extracted
            break
    if counts is None:
        return {
            "status": "UNKNOWN",
            "counts": {},
            "message": "Report loaded, but its count schema has not yet been mapped.",
        }
    if "FAIL" not in counts or "ERROR" not in counts:
        status = "UNKNOWN"
    elif counts["FAIL"] or counts["ERROR"]:
        status = "ATTENTION_REQUIRED"
    elif counts.get("WARN", 0):
        status = "WARNING"
    else:
        status = "PASS"
    return {"status": status, "counts": counts, "message": "Counts extracted from the report; no individual findings inferred."}
