"""Deterministic, read-only severity and remediation rules.

Rules never infer a root cause from a failed validation alone.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

PASS = {"PASS", "PASSED", "SUCCESS", "SUCCEEDED", "OK"}
WARN = {"WARN", "WARNING"}
FAIL = {"FAIL", "FAILED", "FAILURE"}
ERROR = {"ERROR", "EXCEPTION"}

# Order of tests matters: narrower patterns precede general ones.
RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"duplicate|uniqu(e|eness)", re.I),
     "Check the affected business key and deduplication logic at its source.",
     "Duplicate business keys can inflate aggregates or produce ambiguous joins."),
    (re.compile(r"null|missing|complet(e|eness)", re.I),
     "Inspect null counts, required-field constraints, and upstream ingestion mappings.",
     "Missing required values can break joins and reduce reporting coverage."),
    (re.compile(r"reconcil|mismatch|revenue|amount|total|balance|sum", re.I),
     "Reconcile source-to-Gold aggregates and check filter, currency, and status definitions.",
     "Inconsistent financial metrics can undermine downstream reporting."),
    (re.compile(r"row.count|record.count|count|volume", re.I),
     "Compare expected and actual row counts across pipeline layers and partitions.",
     "Unexpected volume may indicate missing or duplicated records."),
    (re.compile(r"fresh|stale|latency|delay|date", re.I),
     "Check source arrival times, pipeline schedules, and partition freshness.",
     "Late data can make dashboards out of date."),
    (re.compile(r"stock|inventory|reorder|negative", re.I),
     "Inspect inventory calculations and verify nonnegative stock and reorder constraints.",
     "Incorrect stock metrics can distort replenishment recommendations."),
)


def normalize_status(value: Any) -> str:
    """Normalize documented outcome synonyms, leaving unknowns explicit."""
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    raw = str(value or "").strip().upper()
    if raw in PASS:
        return "PASS"
    if raw in WARN:
        return "WARN"
    if raw in FAIL:
        return "FAIL"
    if raw in ERROR:
        return "ERROR"
    return "UNKNOWN"


def severity_for(status: str) -> str:
    return {"ERROR": "critical", "FAIL": "high", "WARN": "medium",
            "UNKNOWN": "needs_review", "PASS": "none"}[status]


def recommendation_for(name: str, status: str) -> tuple[str, str]:
    if status == "UNKNOWN":
        return (
            "Review the validation status and check schema; outcome is not recognized.",
            "Business impact cannot be determined from this validation record.",
        )
    for pattern, action, impact in RULES:
        if pattern.search(name):
            return action, impact
    return (
        "Review the failing validation's actual and expected values, then trace its upstream inputs.",
        "A failed data quality check may affect analytics that depend on this dataset.",
    )


def get_check_status(check: Mapping[str, Any]) -> str:
    """Do not infer PASS merely from the absence of failure fields."""
    for key in ("status", "result", "outcome"):
        if key in check:
            raw = check[key]
            if isinstance(raw, Mapping):
                for nested_key in ("status", "outcome"):
                    if nested_key in raw:
                        return normalize_status(raw[nested_key])
                return "UNKNOWN"
            return normalize_status(raw)
    if "passed" in check and isinstance(check["passed"], bool):
        return normalize_status(check["passed"])
    return "UNKNOWN"
