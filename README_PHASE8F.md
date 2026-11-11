# AegisData AI — Phase 8F: Local Agent Observability

This package adds local, allowlisted, read-only audit logs around the **existing Phase 8E guard**. No AWS resources are provisioned. No prompts, findings, credentials, error messages, or AI commentary are saved. The local JSONL is **not** a tamper-proof or comprehensive audit solution.

## 1. Copy the supplied files

Copy, preserving folders, into `C:\AWS-AI-Projects\aegisdata-ai\`:

- `agents/data_quality/phase8f_observability.py`
- `dashboard/agent_observability.py`
- `tests/test_phase8f_observability.py`

## 2. Recommended: one-command safe integration

Copy `install_phase8f.py` into the project root, then run:

```cmd
python install_phase8f.py
```

The installer checks expected Phase 8E code anchors, parses both edited files, creates `.phase8f.bak` backups and avoids duplicate installation. If it reports a mismatch, **do not force edits**; use the manual steps below after reviewing the local source.

### Manual integration (alternative to running installer)

#### Update `dashboard/ai_data_quality.py`

After the existing import of `guarded_explanation`, add:

```python
from agents.data_quality.phase8f_observability import observed_explanation  # noqa: E402
```

Inside the button-click block only, replace:

```python
response = guarded_explanation(
    deterministic=trusted,
    reasoner=verified_reasoner,
    allow_live=enabled,
)
```

with:

```python
response, audit_saved = observed_explanation(
    deterministic=trusted,
    reasoner=verified_reasoner,
    allow_live=enabled,
)
```

Immediately after the spinner block (same indentation level as the `with st.spinner` statement), add:

```python
if not audit_saved:
    st.warning("The AI response was processed, but its local audit event could not be saved.")
```

Do not change the `verified_reasoner` function, validation metrics, or JSON download.

#### Update `dashboard/app.py`

Add at the top alongside dashboard imports:

```python
from agent_observability import render_agent_observability
```

Add `"Agent Observability"` to the `st.radio` navigation list. Then before the final `else:` add:

```python
elif page == "Agent Observability":
    render_agent_observability()
```

## 4. Run locally (Windows CMD, project root, active venv)

```cmd
python -m py_compile agents\data_quality\phase8f_observability.py dashboard\agent_observability.py dashboard\ai_data_quality.py dashboard\app.py
python -m pytest -o addopts="" tests\test_phase8f_observability.py tests\test_phase8e_guard.py tests\test_phase8e_deterministic.py -v
python -m streamlit run dashboard/app.py
```

Test **AI Data Quality** -> opt in -> **Generate AI explanation**, then select **Agent Observability**. A Bedrock fallback is still recorded with its safe error category. Clicking the global Athena refresh does **not** create a Bedrock invocation.

## 5. Safe source control

Add the following to the root `.gitignore` (do not commit operational logs):

```gitignore
reports/observability/
```

Then:

```cmd
git add agents/data_quality/phase8f_observability.py dashboard/agent_observability.py dashboard/ai_data_quality.py dashboard/app.py tests/test_phase8f_observability.py .gitignore
git commit -m "feat: add Phase 8F local agent observability"
git push origin main
```

The existing CI is unaffected unless its workflows are explicitly extended. To run this new test in GitHub Actions, add `tests/test_phase8f_observability.py` to the pytest file list in the Phase 8E safety regression workflow or CI test step.

**Limitations:** events are appended locally, do not automatically aggregate across machines, can be lost during disk failures, and cannot replace CloudWatch or CloudTrail. Success-rate metrics refer to logged live attempts, not all real usage. This phase intentionally avoids AWS charges.
