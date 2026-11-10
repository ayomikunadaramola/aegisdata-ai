"""Phase 8J-E: offline, read-only release acceptance audit for AegisData AI.

Inspects tracked Git paths, expected deliverables, and optional local pytest.
Does not invoke AWS, edit Git files, inspect secrets, or certify production use.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    'README.md', 'LICENSE', '.env.example', '.gitignore',
    'requirements-ci.txt', 'pyproject.toml', 'pytest.ini',
    'dashboard/app.py', 'dashboard/ai_data_quality.py',
    'dashboard/agent_observability.py',
    'agents/data_quality/agent.py',
    'agents/data_quality/bedrock_reasoner.py',
    'agents/data_quality/phase8e_guard.py',
    'agents/data_quality/phase8f_observability.py',
    'agents/data_quality/phase8g_evaluation.py',
    'agents/data_quality/phase8h_security.py',
    'tools/phase8i_audit.py', 'tools/phase8jd_reconcile.py',
    '.github/workflows/ci.yml',
    '.github/workflows/phase8i.yml',
)
REQUIRED_DIRS = ('ingestion/', 'pipelines/', 'infrastructure/', 'sql/', 'tests/')
TEST_FILES = (
    'tests/test_athena_client_contract.py',
    'tests/test_dashboard_offline.py',
    'tests/test_phase8e_guard.py',
    'tests/test_phase8f_observability.py',
    'tests/test_phase8g_evaluation.py',
    'tests/test_phase8h_security.py',
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', '-C', str(ROOT), *args],
                          text=True, capture_output=True, check=True, timeout=20)


def sensitive(path: str) -> bool:
    p = path.replace('\\', '/').lower()
    name = p.rsplit('/', 1)[-1]
    parts = p.split('/')
    if name == '.env' or (name.startswith('.env.') and name != '.env.example'):
        return True
    if name.endswith(('.pem', '.p12', '.key', '.tfstate', '.tfplan')):
        return True
    if '.tfstate.' in name or name in ('terraform.tfvars', 'tfplan'):
        return True
    if name.endswith('.tfvars') or (name.endswith('.tfvars.json')):
        return name != 'terraform.tfvars.example'
    if 'reports' in parts:
        return name != '.gitkeep'
    if 'data' in parts and any(s in parts for s in ('raw', 'synthetic', 'processed')):
        return name != '.gitkeep'
    return False


def run_audit(run_tests: bool = False) -> dict:
    tracked = set(git('ls-files', '-z').stdout.split('\0')) - {''}
    tracked = {p.replace('\\', '/') for p in tracked}
    missing = sorted(p for p in REQUIRED if p not in tracked)
    absent_sections = sorted(d for d in REQUIRED_DIRS
                             if not any(p.startswith(d) for p in tracked))
    flagged = sorted(p for p in tracked if sensitive(p))
    missing_tests = sorted(p for p in TEST_FILES if p not in tracked)
    changes = git('status', '--porcelain', '--untracked-files=no').stdout.strip()
    # Untracked files are noted but not read or published.
    untracked = git('ls-files', '--others', '--exclude-standard').stdout.splitlines()
    report = {
        'phase': '8J-E', 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'git_head': git('rev-parse', '--short', 'HEAD').stdout.strip(),
        'branch': git('branch', '--show-current').stdout.strip(),
        'checks': {
            'required_release_files': {'pass': not missing, 'missing': missing},
            'source_sections_tracked': {'pass': not absent_sections, 'missing': absent_sections},
            'required_regression_tests': {'pass': not missing_tests, 'missing': missing_tests},
            'no_tracked_sensitive_artifacts': {'pass': not flagged, 'paths': flagged},
            'tracked_worktree_clean': {'pass': not bool(changes), 'dirty': bool(changes)},
        },
        'untracked_count': len(untracked),
        'untracked_paths': untracked[:30],
        'offline_test_result': 'NOT_RUN',
        'live_aws_reconciliation': 'NOT_VERIFIED_BY_THIS_AUDIT',
        'github_actions': 'VERIFY_ON_GITHUB_FOR_THIS_COMMIT',
        'production_security_review': 'NOT_PERFORMED',
    }
    if run_tests:
        command = [sys.executable, '-m', 'pytest', '-o', 'addopts=',
                   'tests', '-m', 'not live', '-q']
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                       text=True, timeout=600, check=False)
            report['offline_test_result'] = 'PASS' if completed.returncode == 0 else 'FAIL'
            report['offline_test_exit_code'] = completed.returncode
            report['offline_test_tail'] = '\n'.join((completed.stdout + '\n' + completed.stderr).splitlines()[-12:])
        except subprocess.TimeoutExpired:
            report['offline_test_result'] = 'TIMEOUT'
    checks_ok = all(item['pass'] for item in report['checks'].values())
    if not checks_ok or report['offline_test_result'] in ('FAIL', 'TIMEOUT'):
        report['status'] = 'BLOCKED'
    elif report['offline_test_result'] == 'PASS':
        report['status'] = 'READY_FOR_MANUAL_REVIEW'
    else:
        report['status'] = 'PENDING_TEST_EVIDENCE'
    report['limitations'] = [
        'Git inventory is not a historical secret scan or cloud IAM assessment.',
        'A clean Git index does not prove AWS services are configured correctly.',
        'Offline tests do not validate live Athena, S3, Bedrock, or deployments.',
        'Manual approval and release evidence are required; not production certification.',
    ]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-offline-tests', action='store_true',
                        help='Run pytest tests -m "not live"; no AWS intended')
    parser.add_argument('--strict', action='store_true',
                        help='Fail unless all static checks and offline tests pass')
    parser.add_argument('--output', default='reports/acceptance/phase8je_latest.json',
                        help='Path for local JSON output (not for Git)')
    args = parser.parse_args()
    try:
        result = run_audit(args.run_offline_tests)
        output = (ROOT / args.output).resolve()
        if not output.is_relative_to(ROOT / 'reports' / 'acceptance'):
            raise ValueError('Output must be inside reports/acceptance')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f'Acceptance audit error: {type(exc).__name__}', file=sys.stderr)
        return 2
    print(json.dumps({'status': result['status'], 'report': str(output),
                      'checks': result['checks'],
                      'offline_test_result': result['offline_test_result']}, indent=2))
    if args.strict and result['status'] != 'READY_FOR_MANUAL_REVIEW':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
