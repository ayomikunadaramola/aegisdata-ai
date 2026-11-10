"""Phase 8E safety boundary for optional, read-only Bedrock commentary.

Pass an existing reasoner callable to guarded_explanation. The deterministic
result is the only source of truth, including when the model fails, responds
with malformed data, or attempts to alter validation counts.

No AWS calls are made by this module; the caller must explicitly opt in.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

MAX_COMMENTARY_CHARS = 12000


def _error_category(exc: Exception) -> str:
    """Return safe, coarse error categories without leaking tokens or messages."""
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        info = response.get("Error", {})
        if isinstance(info, Mapping):
            code = str(info.get("Code", ""))
    label = code or type(exc).__name__
    if label in {"ThrottlingException", "TooManyRequestsException", "LimitExceededException"}:
        return "rate_limited"
    if label in {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId"}:
        return "credentials_expired"
    if label in {"AccessDeniedException", "AccessDenied", "UnauthorizedException"}:
        return "access_denied"
    if label in {"ModelNotReadyException", "ResourceNotFoundException", "ValidationException"}:
        return "model_unavailable"
    if label in {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionError", "TimeoutError"}:
        return "network_error"
    return "model_error"


def _fallback(deterministic: dict[str, Any], error: str | None) -> dict[str, Any]:
    return {
        "mode": "fallback" if error else "offline",
        "deterministic": deepcopy(deterministic),
        "ai_explanation": None,
        "ai_error": error,
        "usage": None,
        "notice": "AI commentary is non-authoritative; no remediation executed.",
    }


def guarded_explanation(
    deterministic: Mapping[str, Any],
    reasoner: Callable[[], Any] | None = None,
    *,
    allow_live: bool = False,
) -> dict[str, Any]:
    """Optionally call the existing reasoner; preserve trusted validation data.

    ``reasoner`` is a zero-argument callable supplied by the UI or application.
    The module refuses live execution without explicit allow_live=True.
    An expected reasoner result is a dict with ai_explanation (string) and
    optionally usage. A nested `deterministic` field must match exactly if present.
    """
    if not isinstance(deterministic, Mapping):
        raise TypeError("deterministic must be a mapping")
    trusted = deepcopy(dict(deterministic))
    if not allow_live or reasoner is None:
        return _fallback(trusted, None)

    try:
        response = reasoner()
    except Exception as exc:
        return _fallback(trusted, _error_category(exc))

    if not isinstance(response, Mapping):
        return _fallback(trusted, "invalid_model_response")
    provided = response.get("deterministic")
    if provided is not None and provided != trusted:
        return _fallback(trusted, "deterministic_mismatch")
    commentary = response.get("ai_explanation")
    if not isinstance(commentary, str) or not commentary.strip():
        return _fallback(trusted, "empty_model_response")
    if len(commentary) > MAX_COMMENTARY_CHARS:
        return _fallback(trusted, "response_too_long")
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        usage = None
    else:
        usage = {key: val for key, val in usage.items()
                 if key in ("inputTokens", "outputTokens", "totalTokens")
                 and type(val) is int and 0 <= val <= 10000000}
    return {
        "mode": "bedrock",
        "deterministic": trusted,
        "ai_explanation": commentary.strip(),
        "ai_error": None,
        "usage": usage,
        "notice": "AI commentary is non-authoritative; no remediation executed.",
    }
