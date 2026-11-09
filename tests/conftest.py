"""Test bootstrap for the existing AegisData dashboard layout."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
if not (DASHBOARD / "app.py").exists():
    raise RuntimeError(f"Expected dashboard/app.py under {ROOT}. Copy tests into the project root.")
sys.path.insert(0, str(DASHBOARD))
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def disable_aws_metadata(monkeypatch):
    # Offline tests must never attempt AWS instance-role metadata discovery.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
