# Phase 8B — AegisData AI deterministic Data Quality Agent

## Install
Copy the two modules to `agents/data_quality/` in your existing repository, and
copy `test_data_quality_agent.py` to `tests/`. **Do not replace** your existing
`report_reader.py`, `inspect_report.py`, Streamlit dashboard, or Athena client.

## Execute from project root (Windows VS Code terminal)

```bat
python -m py_compile agents\data_quality\rules.py agents\data_quality\agent.py
python -m pytest tests\test_data_quality_agent.py -v
python -m agents.data_quality.agent
```

The last command reads `reports/validation/gold_validation_latest.json`.

## Important schema limitation

Your report was previously observed to have top-level keys:
`checked_at_utc`, `checks`, `database`, `summary`.
The exact nested shape of `checks` was not supplied. This starter accepts a
list of validation-check dictionaries or a mapping of name to check dictionary.
Each check should contain `status`, `result`, `outcome`, or boolean `passed`.
If your checks use a different schema, the agent returns UNKNOWN or raises an
explicit schema error. In that case, share a **redacted** sample of one check
and the summary; adapt the parser rather than silently manufacturing pass/fail
findings.

`summary_mismatch` highlights differences between extracted report-level
counts and individually inspected checks. It is not safe to infer a PASS from
summary counts alone without inspecting the underlying check records.

## Scope
This phase is intentionally offline and read-only. Remediation actions are
recommendations, not automated changes. No Bedrock, S3, or Athena calls occur.
