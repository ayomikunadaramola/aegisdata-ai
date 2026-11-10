# AegisData AI — Phase 8H: Security and Governance Baseline

Phase 8H adds **offline, read-only security posture checks** without changing the existing Phase 8E/8F/8G execution flow or contacting AWS.

## Installation

Copy the three files to their matching paths under `C:\AWS-AI-Projects\aegisdata-ai`:

- `agents/data_quality/phase8h_security.py`
- `tests/test_phase8h_security.py`
- `.github/workflows/phase8h.yml`

From a terminal at repository root with `.venv` activated:

```cmd
python -m py_compile agents\data_quality\phase8h_security.py
python -m pytest -o addopts="" tests\test_phase8h_security.py -v
python -m agents.data_quality.phase8h_security
```

## Governance boundaries

The offline scanner verifies selected `.gitignore` rules, examines tracked implementation files for a **limited set of high-confidence secret signatures**, confirms the presence of existing safety entry points, and checks the shape of local audit records.

**Not guaranteed:** IAM least privilege, authentication, authorization, exhaustive secret detection, historical secret exposure, audit log tamper resistance, malicious code detection, or production readiness.

The dashboard currently runs locally and is not an authenticated multi-user service. Do not expose Streamlit directly to the public internet without a reviewed authentication and authorization layer.

## GitHub

```cmd
git status
git add agents/data_quality/phase8h_security.py tests/test_phase8h_security.py .github/workflows/phase8h.yml README_PHASE8H.md
git commit -m "feat: add Phase 8H offline security governance checks"
git push origin main
```

Confirm the **Phase 8H - Offline security governance** workflow is green in GitHub Actions.

## Security notes

- If a credential is discovered in a commit, **revoke/rotate it**. `.gitignore` does not remove it from Git history.
- `reports/observability/` stays ignored, and logs should not be shared publicly.
- `PASS` reports only a narrow control baseline, not comprehensive security assurance.
- Phase 8I/8J must separately assess IAM policies, deployment authentication, network boundaries, and centralized audit retention.
