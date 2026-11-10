"""Phase 8G offline, deterministic reliability and safety-regression tests."""
from datetime import datetime, timedelta, timezone

import pytest

from agents.data_quality.phase8e_guard import guarded_explanation
from agents.data_quality.phase8f_observability import make_event, append_event
from agents.data_quality.phase8g_evaluation import evaluate_events, evaluate_default_log

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def event(mode="bedrock", *, error=None, latency=50, opted_in=True, hours_ago=1):
    value = make_event(mode=mode, status="PASS", opted_in=opted_in,
                       latency_ms=latency, error=error)
    value["timestamp_utc"] = (NOW - timedelta(hours=hours_ago)).isoformat()
    return value


def test_empty_is_insufficient_not_success():
    result = evaluate_events([], now=NOW)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["success_rate"] is None


def test_one_success_does_not_claim_reliability():
    result = evaluate_events([event()], now=NOW)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["success_rate"] == 1.0


def test_healthy_five_successes():
    result = evaluate_events([event(latency=x) for x in (100, 200, 300, 400, 500)], now=NOW)
    assert result["status"] == "HEALTHY" and result["p95_latency_ms"] == 480


def test_low_success_rate_alert():
    data = [event() for _ in range(4)] + [event("fallback", error="network_error")]
    result = evaluate_events(data, now=NOW)
    assert result["status"] == "ALERT"
    assert "success_rate_below_target" in result["alerts"]
    assert result["failed_live_requests"] == 1


def test_rate_limit_alert():
    data = [event() for _ in range(3)] + [event("fallback", error="rate_limited") for _ in range(2)]
    result = evaluate_events(data, now=NOW)
    assert "rate_limit_fraction_above_target" in result["alerts"]
    assert result["error_categories"] == {"rate_limited": 2}


def test_latency_alert():
    result = evaluate_events([event(latency=12000) for _ in range(5)], now=NOW)
    assert result["status"] == "ALERT"
    assert "p95_latency_above_target" in result["alerts"]


def test_no_opt_in_excluded_from_denominator():
    data = [event(opted_in=False, mode="offline") for _ in range(20)] + [event()]
    assert evaluate_events(data, now=NOW)["live_attempts"] == 1


def test_out_of_window_and_future_ignored():
    assert evaluate_events([event(hours_ago=200), event(hours_ago=-1)], now=NOW)["live_attempts"] == 0


def test_bad_timestamps_ignored():
    a = event()
    a["timestamp_utc"] = "not-a-date"
    assert evaluate_events([a], now=NOW)["recorded_events_in_window"] == 0


def test_missing_latency_not_reported_as_healthy():
    records = [event() for _ in range(5)]
    records[0]["latency_ms"] = None
    assert "latency_telemetry_incomplete" in evaluate_events(records, now=NOW)["alerts"]


def test_no_text_or_secrets_in_evaluation():
    e = event()
    e["prompt"] = "PRIVATE PROMPT"
    e["ai_explanation"] = "PRIVATE OUTPUT"
    result = evaluate_events([e], now=NOW)
    assert "PRIVATE" not in str(result)


def test_invalid_thresholds_fail_closed():
    with pytest.raises(ValueError):
        evaluate_events([], min_attempts=0, now=NOW)
    with pytest.raises(ValueError):
        evaluate_events([], min_success_rate=2, now=NOW)


def test_offline_audit_log_roundtrip(tmp_path):
    path = tmp_path / "audit.jsonl"
    assert append_event(event(), path=path)
    result = evaluate_default_log(path=path, now=NOW)
    assert result["live_attempts"] == 1


def test_safety_guard_preserves_deterministic_verdict():
    trusted = {"status": "FAIL", "observed_counts": {"FAIL": 1}}
    response = guarded_explanation(trusted, lambda: {
        "deterministic": {"status": "PASS"}, "ai_explanation": "All is fine"
    }, allow_live=True)
    assert response["mode"] == "fallback"
    assert response["deterministic"] == trusted
    assert response["ai_error"] == "deterministic_mismatch"


def test_no_live_opt_in_never_calls_reasoner():
    invoked = []
    out = guarded_explanation({"status": "PASS"}, lambda: invoked.append(True), allow_live=False)
    assert out["mode"] == "offline" and not invoked
