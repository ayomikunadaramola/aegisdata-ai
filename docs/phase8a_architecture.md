# Phase 8A — AegisData AI agent architecture

## Objective
Create a read-only, testable foundation for a Data Quality Agent without changing the running Streamlit dashboard or existing Athena queries.

## Current confirmed sources
- Local validation report: `reports/validation/gold_validation_latest.json`
- Existing AWS Athena Gold database: `aegisdata_gold_db`
- Existing dashboard: `dashboard/app.py`
- Existing CI regression suite: `tests/`

## Planned boundaries
1. UI/CLI calls a service or orchestrator, not boto3 directly.
2. The orchestrator invokes explicitly registered tools.
3. A report-reader tool reads and summarizes local validation reports.
4. Future Athena tools execute allowlisted read-only queries, with limits and separate IAM permissions.
5. Future Bedrock reasoning receives minimized tool outputs and produces evidence-linked explanations; model output is advisory, not automatically executed.
6. Any pipeline repair or write operation requires a separately designed authorization/approval flow.

## Phase 8A implementation
`agents/data_quality/report_reader.py` validates paths, loads JSON, and summarizes known counters.
`agents/data_quality/inspect_report.py` provides a CLI smoke test.
`tests/test_agent_report_reader.py` contains offline unit tests.

**Schema limitation:** Existing screenshots show an aggregate results map with PASS/FAIL/WARN/ERROR. The individual findings schema is not yet verified, so Phase 8A intentionally does not guess names, severity, timestamps or recommended fixes.

## Next: Phase 8B
Inspect the real report schema. Build a normalized finding model and deterministic severity/rule evaluation. Add tests for missing reports, warnings, failures and malformed data before connecting Bedrock.
