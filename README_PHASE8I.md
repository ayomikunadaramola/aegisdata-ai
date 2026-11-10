# Phase 8I — Integration and Release Readiness

## Install paths

Copy files into `C:\AWS-AI-Projects\aegisdata-ai` as follows:

- `tools/phase8i_audit.py`
- `.github/workflows/phase8i.yml`
- `README_PHASE8I.md`

Do not replace existing CI files. The new workflow is additive.

## Run local audit and offline tests

From activated `.venv` at project root in Windows CMD:

```cmd
python -m py_compile tools\phase8i_audit.py
python tools\phase8i_audit.py
python -m pytest -o addopts="" tests\test_athena_client_contract.py tests\test_dashboard_offline.py tests\test_agent_report_reader.py tests\test_bedrock_reasoner.py tests\test_data_quality_agent.py tests\test_phase8e_deterministic.py tests\test_phase8e_guard.py tests\test_phase8f_observability.py tests\test_phase8g_evaluation.py tests\test_phase8h_security.py -m "not live" -v -ra
```

The audit is read-only. `BLOCKED` means repository release work remains; it does **not** mean existing tests have failed. The CI audit runs non-strictly so the report is visible without hiding unrelated test results.

## Expected publication gaps to investigate

- `README.md` is referenced by `pyproject.toml` but currently absent from published repository root.
- `ingestion/`, `pipelines/`, `infrastructure/`, and `sql/` were visible locally but not published on the branch inspected.
- `.env.example` was visible locally but not published. Verify placeholders contain no secrets before tracking it.
- Do not commit generated datasets, report logs, `.env`, Terraform state, or credentials.

## Review gate

To finish Phase 8I, require passing CI, accurate documented installation, tracked reproducible source (not generated data), controlled end-to-end integration evidence, security review, and documented known limitations. Do not run live Athena/Bedrock tests automatically.
