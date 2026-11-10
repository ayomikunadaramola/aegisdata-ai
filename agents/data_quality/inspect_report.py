"""Run from project root: python -m agents.data_quality.inspect_report"""
from __future__ import annotations

import json
from pathlib import Path

from .report_reader import load_validation_report, summarize_validation_report


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    report = load_validation_report(project_root)
    print(json.dumps(summarize_validation_report(report), indent=2))
    print("Top-level report keys:", sorted(report.keys()))


if __name__ == "__main__":
    main()
