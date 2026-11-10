"""Offline Phase 8C unit tests: never invoke real AWS services."""
import json
import subprocess

import pytest

from agents.data_quality import bedrock_reasoner as reasoner


@pytest.fixture
def clean_report():
    return {"status": "PASS", "checks_inspected": 19,
            "observed_counts": {"PASS": 19, "FAIL": 0, "WARN": 0, "ERROR": 0},
            "findings": [], "summary_mismatch": None}


class FakeRuntime:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or {"output": {"message": {"content": [{"text": "No current findings."}]}},
                                     "usage": {"inputTokens": 40, "outputTokens": 5}}
        self.error = error

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_offline_never_constructs_client(monkeypatch, clean_report):
    monkeypatch.setattr(reasoner, "_make_client", lambda *a: pytest.fail("AWS call attempted"))
    result = reasoner.analyze(clean_report)
    assert result["mode"] == "offline"
    assert result["deterministic"] == clean_report
    assert result["ai_explanation"] is None


def test_live_requires_explicit_model(monkeypatch, clean_report):
    monkeypatch.delenv("AEGISDATA_BEDROCK_MODEL_ID", raising=False)
    result = reasoner.analyze(clean_report, live=True)
    assert result["mode"] == "offline"
    assert "MODEL_ID" in result["ai_error"]


def test_success_does_not_override_evidence(clean_report):
    fake = FakeRuntime()
    output = reasoner.analyze(clean_report, live=True, model_id="test-model", client=fake)
    assert output["mode"] == "bedrock"
    assert output["deterministic"]["status"] == "PASS"
    assert output["deterministic"]["findings"] == []
    assert output["ai_explanation"] == "No current findings."
    assert fake.calls[0]["modelId"] == "test-model"
    assert fake.calls[0]["inferenceConfig"]["temperature"] == 0


def test_service_failure_falls_back(clean_report):
    output = reasoner.analyze(clean_report, live=True, model_id="x",
                              client=FakeRuntime(error=RuntimeError("secret details")))
    assert output["mode"] == "fallback"
    assert "secret details" not in output["ai_error"]
    assert output["deterministic"] == clean_report


def test_empty_text_response_falls_back(clean_report):
    fake = FakeRuntime(response={"output": {"message": {"content": []}}})
    output = reasoner.analyze(clean_report, live=True, model_id="x", client=fake)
    assert output["mode"] == "fallback"


def test_prompt_excludes_raw_fields(clean_report):
    dirty = {**clean_report, "aws_secret_access_key": "DO_NOT_SEND", "raw_rows": ["private"]}
    fake = FakeRuntime()
    reasoner.analyze(dirty, live=True, model_id="x", client=fake)
    payload = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert "DO_NOT_SEND" not in payload
    assert "private" not in payload


def test_failure_evidence_preserved(clean_report):
    broken = {**clean_report, "status": "FAIL", "findings": [{"check": "unique_customer_id", "severity": "HIGH"}]}
    output = reasoner.analyze(broken)
    assert output["deterministic"]["findings"][0]["check"] == "unique_customer_id"


def test_report_loader_json(monkeypatch, tmp_path, clean_report):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": json.dumps(clean_report)})())
    assert reasoner.load_phase8b_report(tmp_path) == clean_report


def test_report_loader_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": "not-json"})())
    with pytest.raises(ValueError, match="valid JSON"):
        reasoner.load_phase8b_report(tmp_path)
