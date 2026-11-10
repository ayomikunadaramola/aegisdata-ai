# AegisData AI — Phase 8G: Offline Reliability Evaluation

## What this adds

- Rolling seven-day reliability assessment for **already-recorded** Phase 8F events.
- Explicit `INSUFFICIENT_DATA`, `HEALTHY`, and `ALERT` outcomes.
- Provisional SLOs: 5 live attempts minimum, 95% success, 10,000 ms p95 latency, max 20% rate-limited.
- Failure categories and a downloadable aggregate JSON summary.
- Deterministic regression tests and a separate GitHub Actions workflow.
- An expanded **Agent Observability** page; **no changes to `dashboard/app.py` required**.

The evaluator **does not** call Bedrock or Athena, retain prompts/model outputs, perform auto-remediation, or judge the semantic accuracy of explanations. The thresholds are demonstration defaults. The existing JSONL file stays local and ignored by Git.

## Install

Extract the ZIP. Copy the *contents* of `aegisdata_phase8g/` into your local project root:

`C:\AWS-AI-Projects\aegisdata-ai\`

Preserve folder hierarchy. Replace the existing `dashboard/agent_observability.py` only after making a backup, e.g.:

```cmd
copy dashboard\agent_observability.py dashboard\agent_observability.py.phase8g.bak
```

The ZIP contains:

- `agents/data_quality/phase8g_evaluation.py` (new)
- `dashboard/agent_observability.py` (replacement; includes Phase 8F and 8G)
- `tests/test_phase8g_evaluation.py` (new)
- `.github/workflows/phase8g.yml` (new)
- `README_PHASE8G.md` (guide)

Make sure `.gitignore` includes `*.phase8g.bak` and `reports/observability/`.

## Verify from your activated virtual environment

```cmd
cd /d C:\AWS-AI-Projects\aegisdata-ai
python -m py_compile agents\data_quality\phase8g_evaluation.py dashboard\agent_observability.py
python -m pytest -o addopts="" tests\test_phase8g_evaluation.py -v
python -m pytest -o addopts="" tests\test_phase8f_observability.py tests\test_phase8g_evaluation.py -v
python -m agents.data_quality.phase8g_evaluation
python -m streamlit run dashboard\app.py
```

Select **Agent Observability** in Streamlit. It now shows the Phase 8G panel above the existing Phase 8F metrics. With only one live event, expect `INSUFFICIENT_DATA`, not `HEALTHY`.

To simulate the reliability scenarios, use the automated tests. **Do not manufacture real Bedrock requests just to reach five attempts.**

## Optional CLI CI-friendly mode

```cmd
python -m agents.data_quality.phase8g_evaluation --strict
```

Exit code: 0 = healthy; 1 = alert; 2 = insufficient data; 3 = invalid input/evaluation error. Do **not** run strict mode as a mandatory GitHub build gate without a representative log; the repo deliberately does not upload private runtime audit logs.

## Commit after tests pass

```cmd
git add agents/data_quality/phase8g_evaluation.py dashboard/agent_observability.py tests/test_phase8g_evaluation.py .github/workflows/phase8g.yml README_PHASE8G.md .gitignore
git diff --cached --name-only
git commit -m "feat: implement Phase 8G offline agent reliability evaluation"
git push origin main
```

Check the **Phase 8G - Offline reliability evaluation regression** GitHub Actions workflow.
