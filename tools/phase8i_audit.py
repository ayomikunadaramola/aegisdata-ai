"""Phase 8I-A: read-only repository release-readiness inventory.

Does not contact AWS, execute project code, or change repository contents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TRACKED = [
    "pyproject.toml", "requirements-ci.txt", "pytest.ini",
    "dashboard/app.py", "dashboard/ai_data_quality.py",
    "dashboard/agent_observability.py",
    "agents/data_quality/phase8e_guard.py",
    "agents/data_quality/phase8g_evaluation.py",
    "agents/data_quality/phase8h_security.py",
    "tests/test_dashboard_offline.py",
    "tests/test_athena_client_contract.py",
    "tests/test_phase8e_guard.py",
    "tests/test_phase8f_observability.py",
    "tests/test_phase8g_evaluation.py",
    "tests/test_phase8h_security.py",
    ".github/workflows/ci.yml",
    ".github/workflows/phase8e.yml",
    ".github/workflows/phase8g.yml",
    ".github/workflows/phase8h.yml",
]

RELEASE_FILES = ["README.md", "LICENSE", ".env.example"]
PIPELINE_DIRS = ["ingestion", "pipelines", "infrastructure", "sql"]


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    return {p.decode("utf-8", errors="replace").replace("\\", "/")
            for p in result.stdout.split(b"\0") if p}


def audit(root: Path) -> dict:
    tracked = tracked_files(root)
    required_missing = [name for name in REQUIRED_TRACKED if name not in tracked]
    release_missing = [name for name in RELEASE_FILES if name not in tracked]
    pipelines = {
        name: {"local_exists": (root / name).is_dir(),
               "tracked": any(path.startswith(name + "/") for path in tracked)}
        for name in PIPELINE_DIRS
    }
    # Generated datasets and local audit files are intentionally never packaged.
    return {
        "phase": "8I-A",
        "status": "BLOCKED" if required_missing or release_missing or any(
            not p["tracked"] for p in pipelines.values()
        ) else "READY_FOR_TESTING",
        "tracked_source_missing": required_missing,
        "release_files_missing": release_missing,
        "pipeline_inventory": pipelines,
        "notes": [
            "Inventory checks tracked Git files; it does not run data pipelines or AWS calls.",
            "Check pipeline folders individually before staging; do not publish generated data or credentials.",
            "READY_FOR_TESTING is not production approval.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Return exit code 1 if release inventory is blocked")
    args = parser.parse_args()
    try:
        report = audit(ROOT)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"phase": "8I-A", "status": "ERROR", "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 1 if args.strict and report["status"] != "READY_FOR_TESTING" else 0


if __name__ == "__main__":
    raise SystemExit(main())
