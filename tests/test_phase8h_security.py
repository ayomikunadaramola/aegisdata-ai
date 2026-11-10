"""Phase 8H: completely offline regression tests."""
from pathlib import Path

from agents.data_quality.phase8h_security import audit_security

GITIGNORE = '''.env\n.env.*\n.venv/\n*.tfstate\n*.tfvars\nreports/observability/\n'''


def project(tmp_path: Path) -> Path:
    (tmp_path / '.gitignore').write_text(GITIGNORE, encoding='utf-8')
    (tmp_path / 'agents/data_quality').mkdir(parents=True)
    (tmp_path / 'dashboard').mkdir()
    (tmp_path / 'agents/data_quality/phase8e_guard.py').write_text(
        'def guarded_explanation(): pass\n', encoding='utf-8')
    (tmp_path / 'agents/data_quality/phase8f_observability.py').write_text(
        'def observed_explanation(): pass\n', encoding='utf-8')
    (tmp_path / 'dashboard/ai_data_quality.py').write_text(
        'observed_explanation()\n', encoding='utf-8')
    import subprocess
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    return tmp_path


def outcome(root: Path, control: str) -> str:
    return next(c['status'] for c in audit_security(root)['checks'] if c['control'] == control)


def test_clean_project_passes(tmp_path):
    assert audit_security(project(tmp_path))['status'] == 'PASS'


def test_missing_ignore_rule_detected(tmp_path):
    root = project(tmp_path)
    (root / '.gitignore').write_text('.env\n', encoding='utf-8')
    assert outcome(root, 'sensitive_artifacts_ignored') == 'FAIL'


def test_exposed_aws_access_key_detected_without_disclosing_value(tmp_path):
    root = project(tmp_path)
    fake_key = 'AKIA' + 'A' * 16
    (root / 'dashboard/unsafe.py').write_text('KEY = "' + fake_key + '"\n', encoding='utf-8')
    import subprocess
    subprocess.run(['git', '-C', str(root), 'add', '-f', 'dashboard/unsafe.py'], check=True)
    result = audit_security(root)
    assert result['status'] == 'FAIL'
    assert 'dashboard/unsafe.py' in str(result)
    assert fake_key not in str(result)


def test_tracked_audit_log_is_rejected(tmp_path):
    root = project(tmp_path)
    log = root / 'reports/observability/agent_events.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text('', encoding='utf-8')
    import subprocess
    subprocess.run(['git', '-C', str(root), 'add', '-f', 'reports/observability/agent_events.jsonl'], check=True)
    assert outcome(root, 'no_tracked_sensitive_artifacts') == 'FAIL'


def test_symlink_log_rejected_when_supported(tmp_path):
    root = project(tmp_path)
    log = root / 'reports/observability/agent_events.jsonl'
    log.parent.mkdir(parents=True)
    target = root / 'target.txt'
    target.write_text('placeholder', encoding='utf-8')
    try:
        log.symlink_to(target)
    except (OSError, NotImplementedError):
        return  # Windows accounts may not permit symlinks.
    assert outcome(root, 'audit_log_shape') == 'FAIL'


def test_extra_sensitive_audit_field_rejected(tmp_path):
    root = project(tmp_path)
    log = root / 'reports/observability/agent_events.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text('{"secret":"should-not-be-here"}\n', encoding='utf-8')
    assert outcome(root, 'audit_log_shape') == 'FAIL'


def test_missing_guard_integration_detected(tmp_path):
    root = project(tmp_path)
    (root / 'dashboard/ai_data_quality.py').write_text('pass\n', encoding='utf-8')
    assert outcome(root, 'agent_guard_wiring_present') == 'FAIL'


def test_no_cloud_dependencies_required(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')
    assert audit_security(root)['failed'] == 0
