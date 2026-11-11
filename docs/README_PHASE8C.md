# AegisData AI — Phase 8C: optional Bedrock reasoning

Keep existing `agents/data_quality/agent.py`, `rules.py`, `report_reader.py` and dashboard files unchanged.

Copy:
- `agents/data_quality/bedrock_reasoner.py` into the matching project folder.
- `tests/test_bedrock_reasoner.py` into project `tests/`.
- This README into project `docs/`.

From `C:\AWS-AI-Projects\aegisdata-ai` with `.venv` active:

```powershell
python -m py_compile agents\data_quality\bedrock_reasoner.py
python -m pytest tests\test_bedrock_reasoner.py -v
python -m agents.data_quality.bedrock_reasoner
```

The last command is **offline**, reads Phase 8B's deterministic report, and does not call AWS.

To discover suitable configured Bedrock inference profiles in your account:

```powershell
aws sts get-caller-identity --profile aegisdata
aws bedrock list-inference-profiles --profile aegisdata --region us-east-1 --query "inferenceProfileSummaries[].{ID:inferenceProfileId,Name:inferenceProfileName,Status:status}" --output table
```

Confirm your chosen profile's access, available region, pricing and destination regions. A model ID is intentionally **not** hardcoded. Invoke Bedrock only when ready (may incur charges):

```powershell
$env:AEGISDATA_BEDROCK_MODEL_ID = "REPLACE_WITH_VERIFIED_MODEL_OR_PROFILE_ID"
python -m agents.data_quality.bedrock_reasoner --live --profile aegisdata --region us-east-1
```

AWS IAM permission must allow `bedrock:InvokeModel` on the target model/profile and applicable destination foundation-model resources. Inference profile policies may require cross-Region permissions; do not grant wildcard production access for convenience. This phase does not need S3 write, Athena write, SQL mutation, or Bedrock tool calling.

## Safety and interpretation

- The validated Phase 8B report remains authoritative; generated text is non-authoritative commentary.
- Input is a compact subset of results, not raw datasets. Review findings before sending confidential data.
- All offline tests use a mocked Bedrock client.
- Inference is opt-in only (`--live`).
- API problems return `mode=fallback` with deterministic results preserved.
- Agent runs the existing Phase 8B CLI as a child process; if your CLI prints non-JSON diagnostics to stdout, adapt `load_phase8b_report` accordingly.
- No Streamlit changes in this phase. Dashboard exposure belongs in a later step after live validation.
