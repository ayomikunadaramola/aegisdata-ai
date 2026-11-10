"""Phase 8F offline regression: no AWS credentials, boto3 or network required."""
import json
from pathlib import Path

from agents.data_quality.phase8f_observability import (
    append_event, make_event, observed_explanation, read_events,
)

TRUSTED = {"status": "FAIL", "observed_counts": {"PASS": 2, "FAIL": 1}}


def test_no_opt_in_no_reasoner_invocation(tmp_path):
    called = []
    result, logged = observed_explanation(TRUSTED, lambda: called.append(True),
                                          allow_live=False, log_path=tmp_path / "audit.jsonl")
    assert result["mode"] == "offline" and logged
    assert not called
    assert read_events(tmp_path / "audit.jsonl")[0]["opted_in"] is False


def test_success_records_tokens_without_content(tmp_path):
    log = tmp_path / "audit.jsonl"
    result, saved = observed_explanation(
        TRUSTED,
        lambda: {"deterministic": TRUSTED, "ai_explanation": "SENSITIVE PROMPT SECRET",
                 "usage": {"inputTokens": 7, "outputTokens": 8, "totalTokens": 15,
                           "password": "DO_NOT_LOG"}},
        allow_live=True, log_path=log,
    )
    assert saved and result["mode"] == "bedrock"
    raw = log.read_text(encoding="utf-8")
    assert "SECRET" not in raw and "SENSITIVE" not in raw and "DO_NOT_LOG" not in raw
    record = read_events(log)[0]
    assert (record["input_tokens"], record["output_tokens"], record["total_tokens"]) == (7, 8, 15)
    assert record["validation_status"] == "FAIL"


def test_mismatch_fails_closed_and_is_audited(tmp_path):
    log = tmp_path / "audit.jsonl"
    out, saved = observed_explanation(
        TRUSTED,
        lambda: {"deterministic": {"status": "PASS"}, "ai_explanation": "everything okay"},
        allow_live=True, log_path=log,
    )
    assert saved and out["mode"] == "fallback" and out["ai_error"] == "deterministic_mismatch"
    assert out["deterministic"] == TRUSTED
    assert read_events(log)[0]["error_category"] == "deterministic_mismatch"


def test_logging_failure_does_not_change_model_result(tmp_path):
    bad_path = tmp_path / "directory"
    bad_path.mkdir()
    result, saved = observed_explanation(TRUSTED, lambda: {"ai_explanation": "safe"},
                                         allow_live=True, log_path=bad_path)
    assert not saved and result["mode"] == "bedrock"


def test_sensitive_extra_fields_rejected(tmp_path):
    event = make_event(mode="bedrock", status="PASS", opted_in=True, latency_ms=12)
    event["aws_secret_access_key"] = "SECRET"
    assert not append_event(event, tmp_path / "audit.jsonl")


def test_invalid_events_are_ignored(tmp_path):
    log = tmp_path / "audit.jsonl"
    good = make_event(mode="offline", status="PASS", opted_in=False, latency_ms=1)
    log.write_text("not json\n" + json.dumps({"prompt": "secret"}) + "\n" + json.dumps(good) + "\n")
    assert read_events(log) == [good]


def test_error_is_allowlisted(tmp_path):
    log = tmp_path / "audit.jsonl"
    def fails():
        raise TimeoutError("sensitive internal hostname")
    out, saved = observed_explanation(TRUSTED, fails, allow_live=True, log_path=log)
    assert saved and out["ai_error"] == "network_error"
    assert "sensitive internal hostname" not in log.read_text()
