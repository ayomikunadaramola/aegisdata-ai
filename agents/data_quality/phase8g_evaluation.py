"""Phase 8G: deterministic, offline reliability evaluation of Phase 8F events.

Never performs AWS calls, modifies validation results, or reads prompt/commentary.
An empty/small sample is INSUFFICIENT_DATA rather than a success claim.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.data_quality.phase8f_observability import DEFAULT_AUDIT_LOG, read_events

ALLOWED_MODES = frozenset({"bedrock", "fallback", "offline"})
ERROR_CATEGORIES = frozenset({
    "rate_limited", "credentials_expired", "access_denied", "model_unavailable",
    "network_error", "model_error", "invalid_model_response", "empty_model_response",
    "response_too_long", "deterministic_mismatch", "observability_error",
})


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def evaluate_events(
    events: Sequence[Mapping[str, Any]], *,
    min_attempts: int = 5,
    min_success_rate: float = 0.95,
    max_p95_latency_ms: float = 10000,
    max_rate_limited_fraction: float = 0.2,
    window_hours: int = 24 * 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate recent opt-in attempts; ignore malformed/future-dated events.

    Thresholds are demonstration SLOs, not measured production baselines.
    """
    if min_attempts < 1 or not 0 <= min_success_rate <= 1 or not 0 <= max_rate_limited_fraction <= 1:
        raise ValueError("Invalid reliability thresholds")
    if window_hours < 1 or max_p95_latency_ms <= 0:
        raise ValueError("Invalid evaluation window/latency threshold")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    valid: list[Mapping[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        try:
            stamp = datetime.fromisoformat(event["timestamp_utc"].replace("Z", "+00:00"))
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
        if stamp.tzinfo is None or not cutoff <= stamp.astimezone(timezone.utc) <= now:
            continue
        if event.get("mode") not in ALLOWED_MODES or type(event.get("opted_in")) is not bool:
            continue
        valid.append(event)

    attempts = [event for event in valid if event["opted_in"]]
    successful = [event for event in attempts if event["mode"] == "bedrock" and event.get("error_category") is None]
    failures = len(attempts) - len(successful)
    latencies = [float(v) for e in attempts if type(v := e.get("latency_ms")) in (int, float)
                 and math.isfinite(v) and 0 <= v <= 3600000]
    error_counts = dict(sorted(Counter(e.get("error_category") for e in attempts
                                       if e.get("error_category") in ERROR_CATEGORIES).items()))
    rate_limited = error_counts.get("rate_limited", 0)
    success_rate = round(len(successful) / len(attempts), 4) if attempts else None
    p95 = _percentile(latencies, 0.95)
    alerts = []
    if len(attempts) >= min_attempts:
        if success_rate is not None and success_rate < min_success_rate:
            alerts.append("success_rate_below_target")
        if p95 is None:
            alerts.append("latency_telemetry_incomplete")
        elif p95 > max_p95_latency_ms:
            alerts.append("p95_latency_above_target")
        if rate_limited / len(attempts) > max_rate_limited_fraction:
            alerts.append("rate_limit_fraction_above_target")
        if len(latencies) != len(attempts):
            alerts.append("latency_telemetry_incomplete")
        status = "ALERT" if alerts else "HEALTHY"
    else:
        status = "INSUFFICIENT_DATA"

    return {
        "schema_version": 1,
        "phase": "8G",
        "status": status,
        "window_hours": window_hours,
        "window_end_utc": now.isoformat(),
        "recorded_events_in_window": len(valid),
        "live_attempts": len(attempts),
        "successful_live_requests": len(successful),
        "failed_live_requests": failures,
        "success_rate": success_rate,
        "p95_latency_ms": p95,
        "latency_samples": len(latencies),
        "error_categories": error_counts,
        "alerts": sorted(set(alerts)),
        "thresholds": {
            "min_attempts": min_attempts,
            "min_success_rate": min_success_rate,
            "max_p95_latency_ms": max_p95_latency_ms,
            "max_rate_limited_fraction": max_rate_limited_fraction,
        },
        "limitations": [
            "Only locally logged, recent events are evaluated; missing events cannot be inferred.",
            "No semantic answer-quality scoring: Phase 8F intentionally does not store model output.",
            "SLO thresholds are provisional; tune using representative production history.",
            "No automated remediation, notifications, or AWS calls are performed.",
        ],
    }


def evaluate_default_log(*, path: Path = DEFAULT_AUDIT_LOG, limit: int = 2000,
                         now: datetime | None = None) -> dict[str, Any]:
    return evaluate_events(read_events(path=path, limit=limit), now=now)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline AegisData AI agent reliability evaluation")
    parser.add_argument("--log", type=Path, default=DEFAULT_AUDIT_LOG,
                        help="Local sanitized Phase 8F JSONL log")
    parser.add_argument("--min-attempts", type=int, default=5)
    parser.add_argument("--window-hours", type=int, default=168)
    parser.add_argument("--strict", action="store_true", help="Exit 1 on ALERT; 2 on insufficient data")
    args = parser.parse_args()
    try:
        result = evaluate_events(read_events(path=args.log, limit=2000),
                                 min_attempts=args.min_attempts, window_hours=args.window_hours)
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(3, f"Evaluation error: {type(exc).__name__}\n")
    print(json.dumps(result, indent=2))
    if args.strict and result["status"] != "HEALTHY":
        raise SystemExit(1 if result["status"] == "ALERT" else 2)


if __name__ == "__main__":
    main()
