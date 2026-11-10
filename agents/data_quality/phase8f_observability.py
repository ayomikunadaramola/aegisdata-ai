"""Phase 8F: privacy-preserving local observability for the AI quality agent.

Only allowlisted operational metadata is stored as JSON Lines. No AWS access,
secrets, prompts, validation findings, commentary, or raw errors are recorded.
A logging failure never changes the authoritative deterministic validation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4
import json
import os

from agents.data_quality.phase8e_guard import guarded_explanation

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_LOG = ROOT / "reports" / "observability" / "agent_events.jsonl"
ALLOWED_MODES = {"bedrock", "fallback", "offline"}
ALLOWED_ERRORS = {
    "rate_limited", "credentials_expired", "access_denied", "model_unavailable",
    "network_error", "model_error", "invalid_model_response", "empty_model_response",
    "response_too_long", "deterministic_mismatch", "observability_error",
}
ALLOWED_STATUSES = {"PASS", "FAIL", "WARN", "UNKNOWN"}
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_READ_EVENTS = 2000


def _token(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10_000_000 else None


def make_event(*, mode: str, status: str, opted_in: bool,
               latency_ms: float, error: str | None = None,
               usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Construct an allowlisted event; never serialize caller-supplied payloads."""
    usage = usage if isinstance(usage, Mapping) else {}
    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_type": "data_quality_explanation",
        "mode": mode if mode in ALLOWED_MODES else "fallback",
        "validation_status": status if status in ALLOWED_STATUSES else "UNKNOWN",
        "opted_in": opted_in is True,
        "latency_ms": round(max(0.0, min(float(latency_ms), 3_600_000.0)), 2),
        "error_category": error if error in ALLOWED_ERRORS else ("model_error" if error else None),
        "input_tokens": _token(usage.get("inputTokens")),
        "output_tokens": _token(usage.get("outputTokens")),
        "total_tokens": _token(usage.get("totalTokens")),
    }


def append_event(event: Mapping[str, Any], path: Path = DEFAULT_AUDIT_LOG) -> bool:
    """Append one bounded event locally; reject symlinks and non-allowlisted fields.

    A False return signals an audit-write failure; consumers must not misreport
    such failures as successful observability. This is not a tamper-proof log.
    """
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and path.stat().st_size >= MAX_LOG_BYTES):
            return False
        allowed = set(make_event(mode="offline", status="UNKNOWN", opted_in=False, latency_ms=0))
        if set(event) != allowed:
            return False
        line = (json.dumps(dict(event), separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(line) > 2048:
            return False
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        return False


def observed_explanation(
    deterministic: Mapping[str, Any],
    reasoner: Callable[[], Any] | None,
    *, allow_live: bool,
    log_path: Path = DEFAULT_AUDIT_LOG,
) -> tuple[dict[str, Any], bool]:
    """Call the existing safety guard and record only safe metadata."""
    started = perf_counter()
    output = guarded_explanation(deterministic, reasoner, allow_live=allow_live)
    elapsed = (perf_counter() - started) * 1000
    event = make_event(
        mode=output["mode"],
        status=str(deterministic.get("status", "UNKNOWN")),
        opted_in=allow_live,
        latency_ms=elapsed,
        error=output.get("ai_error"),
        usage=output.get("usage"),
    )
    saved = append_event(event, log_path)
    return output, saved


def read_events(path: Path = DEFAULT_AUDIT_LOG, *, limit: int = 500) -> list[dict[str, Any]]:
    """Read recent verified event records, ignoring malformed/untrusted lines."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return []
    limit = max(1, min(int(limit), MAX_READ_EVENTS))
    if path.stat().st_size > MAX_LOG_BYTES:
        return []
    allowed = set(make_event(mode="offline", status="UNKNOWN", opted_in=False, latency_ms=0))
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 2048:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or set(item) != allowed:
                continue
            if item.get("schema_version") != 1 or item.get("event_type") != "data_quality_explanation":
                continue
            if item.get("mode") not in ALLOWED_MODES or item.get("validation_status") not in ALLOWED_STATUSES:
                continue
            records.append(item)
    return records[-limit:]
