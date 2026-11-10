"""Offline regression for the existing Phase 8B deterministic agent."""
from copy import deepcopy
from agents.data_quality.agent import analyze_report


def sample(checks, counts=None):
    report = {"checks": checks}
    if counts is not None:
        report["summary"] = {"counts": counts}
    return report


def test_pass_only_with_evidence():
    result = analyze_report(sample([{"name": "customers unique", "passed": True}]))
    assert result["status"] == "PASS"
    assert result["checks_inspected"] == 1
    assert result["findings"] == []


def test_failure_cannot_be_downgraded():
    report = sample([{"name": "revenue reconciliation", "passed": False}])
    unchanged = deepcopy(report)
    result = analyze_report(report)
    assert result["status"] == "FAIL"
    assert result["findings"][0]["root_cause_confirmed"] is False
    assert report == unchanged


def test_missing_status_is_unknown():
    result = analyze_report(sample([{"name": "unclassified check"}]))
    assert result["status"] == "UNKNOWN"


def test_empty_report_cannot_pass():
    result = analyze_report(sample([]))
    assert result["status"] == "UNKNOWN"


def test_summary_mismatch_cannot_pass():
    result = analyze_report(sample([{"name": "only check", "passed": True}],
                                  {"PASS": 2, "FAIL": 0, "WARN": 0, "ERROR": 0}))
    assert result["status"] != "PASS"
    assert result["summary_mismatch"] is not None


def test_report_level_failure_cannot_pass():
    result = analyze_report(sample([{"name": "only check", "passed": True}],
                                  {"PASS": 1, "FAIL": 1, "WARN": 0, "ERROR": 0}))
    assert result["status"] == "FAIL"
