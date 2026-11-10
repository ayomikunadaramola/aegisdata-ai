"""Entirely offline: exercises failure safety, no boto3 or AWS network use."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agents.data_quality.phase8e_guard import guarded_explanation


@pytest.fixture
def trusted():
    return {"status": "FAIL", "checks_inspected": 2,
            "observed_counts": {"PASS": 1, "FAIL": 1, "WARN": 0, "ERROR": 0, "UNKNOWN": 0},
            "findings": [{"check_name": "revenue mismatch", "status": "FAIL"}]}


def test_opt_in_required(trusted):
    calls = []
    output = guarded_explanation(trusted, lambda: calls.append(1), allow_live=False)
    assert not calls
    assert output["mode"] == "offline"
    assert output["deterministic"] == trusted


def test_normal_response_preserves_failure(trusted):
    before = deepcopy(trusted)
    output = guarded_explanation(trusted, lambda: {
        "ai_explanation": "Investigate reconciliation.",
        "deterministic": before,
        "usage": {"totalTokens": 42, "secret": "redacted"}}, allow_live=True)
    assert output["mode"] == "bedrock"
    assert output["deterministic"]["status"] == "FAIL"
    assert output["usage"] == {"totalTokens": 42}
    assert trusted == before


def test_mutated_response_cannot_override_truth(trusted):
    response = {"deterministic": {**trusted, "status": "PASS"},
                "ai_explanation": "Everything passed"}
    out = guarded_explanation(trusted, lambda: response, allow_live=True)
    assert out["mode"] == "fallback"
    assert out["ai_error"] == "deterministic_mismatch"
    assert out["deterministic"]["status"] == "FAIL"


@pytest.mark.parametrize("bad", [None, [], "PASS", {}, {"ai_explanation": "  "},
                                    {"ai_explanation": "x" * 12001}])
def test_invalid_model_response_fails_closed(trusted, bad):
    out = guarded_explanation(trusted, lambda: bad, allow_live=True)
    assert out["mode"] == "fallback"
    assert out["ai_explanation"] is None
    assert out["deterministic"] == trusted


@pytest.mark.parametrize("code,expected", [
    ("ThrottlingException", "rate_limited"),
    ("ExpiredTokenException", "credentials_expired"),
    ("AccessDeniedException", "access_denied"),
    ("ResourceNotFoundException", "model_unavailable"),
    ("OtherServiceError", "model_error"),
])
def test_bedrock_error_categories_are_safe(trusted, code, expected):
    class FakeAwsError(Exception):
        response = {"Error": {"Code": code, "Message": "SECRET-DO-NOT-LEAK"}}
    def fail():
        raise FakeAwsError("SECRET-DO-NOT-LEAK")
    out = guarded_explanation(trusted, fail, allow_live=True)
    assert out["ai_error"] == expected
    assert "SECRET" not in str(out)
    assert out["deterministic"]["status"] == "FAIL"


def test_network_failure(trusted):
    def fail():
        raise TimeoutError("internal address")
    out = guarded_explanation(trusted, fail, allow_live=True)
    assert out["ai_error"] == "network_error"
    assert "internal address" not in str(out)


def test_input_mutation_is_not_propagated(trusted):
    before = deepcopy(trusted)
    def mutate():
        trusted["status"] = "PASS"
        return {"ai_explanation": "Success"}
    out = guarded_explanation(before, mutate, allow_live=True)
    assert out["deterministic"] == before


def test_invalid_deterministic_input_fails_closed():
    with pytest.raises(TypeError):
        guarded_explanation(["PASS"])
