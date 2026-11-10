"""Pure offline Phase 8A tests; no Streamlit, boto3, or AWS calls."""
import json

import pytest

from agents.data_quality.report_reader import load_validation_report, summarize_validation_report


def test_load_and_summarize(tmp_path):
    directory = tmp_path / "reports" / "validation"
    directory.mkdir(parents=True)
    (directory / "gold_validation_latest.json").write_text(
        json.dumps({"results": {"PASS": 19, "FAIL": 0, "WARN": 0, "ERROR": 0}}),
        encoding="utf-8",
    )
    report = load_validation_report(tmp_path)
    assert summarize_validation_report(report)["status"] == "PASS"


def test_failures_are_not_masked():
    summary = summarize_validation_report({"results": {"PASS": 18, "FAIL": 1, "WARN": 0, "ERROR": 0}})
    assert summary["status"] == "ATTENTION_REQUIRED"


def test_unknown_schema_is_explicit():
    summary = summarize_validation_report({"checks": ["not mapped"]})
    assert summary["status"] == "UNKNOWN"


def test_missing_report_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_validation_report(tmp_path)


def test_path_escape_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_validation_report(tmp_path, "../outside.json")
