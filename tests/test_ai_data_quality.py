"""Offline Phase 8D tests. These tests never invoke AWS."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import ai_data_quality as dq


def test_findings_empty_for_pass():
    frame = dq.finding_rows({"findings": []})
    assert frame.empty
    assert "recommended_action" in frame.columns


def test_findings_preserve_evidence():
    finding = {"check_name": "row_count", "status": "FAIL",
               "severity": "high", "recommended_action": "Investigate",
               "root_cause_confirmed": False}
    frame = dq.finding_rows({"findings": [finding]})
    assert frame.iloc[0]["status"] == "FAIL"
    assert not bool(frame.iloc[0]["root_cause_confirmed"])


def test_deterministic_entrypoint_is_used(monkeypatch):
    monkeypatch.setattr(dq, "analyze_default_report", lambda: {"status": "PASS"})
    assert dq.summarize_report()["status"] == "PASS"


def test_live_not_invoked_by_deterministic_summary(monkeypatch):
    monkeypatch.setattr(dq, "invoke_bedrock", lambda **kw: pytest.fail("AWS called"))
    monkeypatch.setattr(dq, "analyze_default_report", lambda: {"status": "PASS"})
    dq.summarize_report()


def test_bedrock_cli_is_fixed_and_parses_json(monkeypatch):
    seen = {}
    def fake_run(args, **kwargs):
        seen["args"] = args
        class Process:
            returncode = 0
            stdout = '{"mode":"bedrock","ai_explanation":"ok"}\n'
            stderr = ""
        return Process()
    monkeypatch.setattr(dq.subprocess, "run", fake_run)
    assert dq.invoke_bedrock()["ai_explanation"] == "ok"
    assert seen["args"][1:3] == ["-m", "agents.data_quality.bedrock_reasoner"]
    assert "--live" in seen["args"]
