"""Phase 8H: offline, read-only security posture checks for AegisData AI.

No AWS requests, authentication attempts, file mutations, or secrets output.
A passing audit is NOT a penetration test or proof of production readiness.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT_RELATIVE = Path('reports/observability/agent_events.jsonl')
REQUIRED_IGNORE = ('.env', '.env.*', '.venv/', '*.tfstate', '*.tfvars',
                   'reports/observability/')
SOURCE_PREFIXES = ('agents/', 'dashboard/', 'api/', 'pipelines/', 'ingestion/',
                   'infrastructure/', '.github/')
SOURCE_SUFFIXES = ('.py', '.yml', '.yaml', '.tf', '.toml', '.sh', '.ps1')
MAX_SOURCE_BYTES = 512_000
MAX_AUDIT_BYTES = 8 * 1024 * 1024
EXPECTED_EVENT_KEYS = frozenset({
    'schema_version', 'event_id', 'timestamp_utc', 'event_type', 'mode',
    'validation_status', 'opted_in', 'latency_ms', 'error_category',
    'input_tokens', 'output_tokens', 'total_tokens',
})
ALLOWED_MODES = frozenset({'offline', 'fallback', 'bedrock'})
ALLOWED_STATUSES = frozenset({'PASS', 'FAIL', 'WARN', 'UNKNOWN'})
ALLOWED_ERRORS = frozenset({
    'rate_limited', 'credentials_expired', 'access_denied', 'model_unavailable',
    'network_error', 'model_error', 'invalid_model_response',
    'empty_model_response', 'response_too_long', 'deterministic_mismatch',
    'observability_error',
})
# Only strong signatures to minimize false positives; this is not a full secret scanner.
AWS_ACCESS_KEY = re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
SECRET_LITERAL = re.compile(
    r'''(?ix)\b(?:aws_secret_access_key|aws_session_token)\b\s*[:=]\s*['\"][^'\"\r\n]{12,}['\"]'''
)


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {'control': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail}


def _tracked_files(root: Path) -> tuple[list[str] | None, str | None]:
    try:
        proc = subprocess.run(
            ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, 'Git tracked-file inventory unavailable'
    if proc.returncode != 0:
        return None, 'Not a Git repository or Git inventory unavailable'
    return [p.decode('utf-8', errors='replace').replace('\\', '/')
            for p in proc.stdout.split(b'\0') if p], None


def audit_security(root: Path = ROOT) -> dict[str, Any]:
    """Inspect existing files without modifying them or calling cloud services."""
    root = Path(root).resolve()
    checks: list[dict[str, str]] = []
    ignore_path = root / '.gitignore'
    try:
        ignore = ignore_path.read_text(encoding='utf-8')
        rules = {line.strip() for line in ignore.splitlines()
                 if line.strip() and not line.lstrip().startswith('#')}
        missing = sorted(set(REQUIRED_IGNORE) - rules)
        checks.append(_check('sensitive_artifacts_ignored', not missing,
                             'Required ignore rules present' if not missing
                             else 'Missing rules: ' + ', '.join(missing)))
    except OSError:
        checks.append(_check('sensitive_artifacts_ignored', False, '.gitignore is unavailable'))

    tracked, git_error = _tracked_files(root)
    if tracked is None:
        checks.append(_check('tracked_file_inventory', False, git_error or 'Unknown Git error'))
    else:
        checks.append(_check('tracked_file_inventory', True, 'Git index inspected'))
        sensitive = [p for p in tracked if p == str(AUDIT_RELATIVE).replace('\\', '/')
                     or p == '.env' or (p.startswith('.env.') and p != '.env.example')
                     or p.endswith(('.pem', '.p12', '.tfstate', '.tfvars'))]
        checks.append(_check('no_tracked_sensitive_artifacts', not sensitive,
                             'No tracked local credentials, state, or audit log'
                             if not sensitive else 'Sensitive tracked paths: ' + ', '.join(sensitive[:8])))
        flagged: list[str] = []
        oversized: list[str] = []
        for rel in tracked:
            if not rel.startswith(SOURCE_PREFIXES) or not rel.endswith(SOURCE_SUFFIXES):
                continue
            file = (root / rel).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                continue
            if file.stat().st_size > MAX_SOURCE_BYTES:
                oversized.append(rel)
                continue
            try:
                content = file.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                oversized.append(rel)
                continue
            if any(pattern.search(content) for pattern in (AWS_ACCESS_KEY, PRIVATE_KEY, SECRET_LITERAL)):
                flagged.append(rel)
        checks.append(_check('tracked_source_secret_signatures', not flagged and not oversized,
                             'No high-confidence credential signatures in inspected source'
                             if not flagged and not oversized else
                             'Files requiring review: ' + ', '.join((flagged + oversized)[:10])))

    safety = root / 'agents/data_quality/phase8e_guard.py'
    observability = root / 'agents/data_quality/phase8f_observability.py'
    dashboard = root / 'dashboard/ai_data_quality.py'
    # Presence is a regression tripwire, not a proof of correct authorization.
    expected = [(safety, 'def guarded_explanation'),
                (observability, 'def observed_explanation'),
                (dashboard, 'observed_explanation(')]
    missing = []
    for path, marker in expected:
        try:
            if marker not in path.read_text(encoding='utf-8'):
                missing.append(str(path.relative_to(root)))
        except OSError:
            missing.append(str(path.relative_to(root)))
    checks.append(_check('agent_guard_wiring_present', not missing,
                         'Phase 8E/8F safety entry points found' if not missing
                         else 'Inspect missing wiring: ' + ', '.join(missing)))

    audit_path = root / AUDIT_RELATIVE
    if audit_path.is_symlink():
        checks.append(_check('audit_log_shape', False, 'Audit log is a symlink'))
    elif not audit_path.exists():
        checks.append(_check('audit_log_shape', True, 'No local audit log yet'))
    elif not audit_path.is_file() or audit_path.stat().st_size > MAX_AUDIT_BYTES:
        checks.append(_check('audit_log_shape', False, 'Audit log is not a bounded regular file'))
    else:
        bad = 0
        try:
            with audit_path.open('r', encoding='utf-8') as stream:
                for line in stream:
                    if len(line) > 2048:
                        bad += 1
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    if (not isinstance(event, dict) or set(event) != EXPECTED_EVENT_KEYS
                        or type(event.get('schema_version')) is not int
                        or event['schema_version'] != 1
                        or event.get('event_type') != 'data_quality_explanation'
                        or event.get('mode') not in ALLOWED_MODES
                        or event.get('validation_status') not in ALLOWED_STATUSES
                        or type(event.get('opted_in')) is not bool
                        or (event.get('error_category') is not None
                            and event.get('error_category') not in ALLOWED_ERRORS)
                        or any(type(event.get(k)) not in (int, type(None)) or
                               (type(event.get(k)) is int and not 0 <= event[k] <= 10_000_000)
                               for k in ('input_tokens', 'output_tokens', 'total_tokens'))):
                        bad += 1
        except (OSError, UnicodeError):
            bad += 1
        checks.append(_check('audit_log_shape', bad == 0,
                             'Audit records conform to the approved metadata schema'
                             if bad == 0 else f'{bad} malformed or unexpected audit record(s)'))

    failed = sum(item['status'] == 'FAIL' for item in checks)
    return {
        'phase': '8H', 'status': 'PASS' if failed == 0 else 'FAIL',
        'checks': checks, 'passed': len(checks) - failed, 'failed': failed,
        'limitations': [
            'Local static checks are not a penetration test or IAM authorization audit.',
            'Git checks inspect tracked files and selected code paths, not Git history.',
            'Local JSONL audit records are not cryptographically tamper-proof.',
            'Authentication, IAM least privilege, and deployment isolation require separate review.',
            'No AWS or remediation operations were performed.',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='AegisData AI Phase 8H offline security audit')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    result = audit_security(args.root)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
