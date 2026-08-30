from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setuptools_security_floor_is_enforced_in_build_dev_and_ci():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "setuptools>=83" in project["build-system"]["requires"]
    assert "setuptools>=83" in project["project"]["optional-dependencies"]["dev"]

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'pip install --upgrade pip "setuptools>=83" wheel' in workflow
