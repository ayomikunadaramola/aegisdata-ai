"""Offline regression tests: no AWS resources or credentials required."""
import pytest

from agents.data_quality.agent import analyze_report


def test_all_passed_report():
    result = analyze_report({"checks": [{"name": "unique_customer_ids", "status": "PASS"}],
                             "summary": {"PASS": 1, "FAIL": 0}})
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["summary_mismatch"] is None


def test_failure_has_action_not_confirmed_root_cause():
    result = analyze_report({"checks": [
        {"name": "revenue_reconciliation", "status": "FAIL", "actual": 90,
         "expected": 100}], "summary": {"counts": {"PASS": 0, "FAIL": 1}}})
    assert result["status"] == "FAIL"
    assert result["findings"][0]["severity"] == "high"
    assert "Reconcile" in result["findings"][0]["recommended_action"]
    assert result["findings"][0]["root_cause_confirmed"] is False


def test_warning_and_error_severity():
    result = analyze_report({"checks": {"freshness": {"result": "WARNING"},
                                               "duplicate_ids": {"status": "ERROR"}}})
    assert result["status"] == "FAIL"
    assert [f["severity"] for f in result["findings"]] == ["medium", "critical"]


def test_unknown_check_fails_closed():
    result = analyze_report({"checks": [{"name": "unrecognized"}]})
    assert result["status"] == "UNKNOWN"
    assert result["findings"][0]["status"] == "UNKNOWN"


def test_summary_disagreement_is_not_silent():
    result = analyze_report({"checks": [{"name": "x", "status": "PASS"}],
                             "summary": {"counts": {"PASS": 19, "FAIL": 0}}})
    assert result["status"] == "UNKNOWN"
    assert result["summary_mismatch"]["PASS"] == {"summary": 19, "checks": 1}


def test_no_checks_cannot_pass_from_summary_alone():
    result = analyze_report({"checks": [], "summary": {"counts": {"PASS": 19}}})
    assert result["status"] == "UNKNOWN"


def test_unexpected_check_structure_is_rejected():
    with pytest.raises(ValueError, match="must be a list or mapping"):
        analyze_report({"checks": "broken"})


def test_boolean_passed_flags():
    result = analyze_report({"checks": [{"check_name": "row_count", "passed": False}]})
    assert result["status"] == "FAIL"
    assert result["findings"][0]["check_name"] == "row_count"
