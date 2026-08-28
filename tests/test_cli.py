from __future__ import annotations

from pathlib import Path

from quorum.cli import app_path


def test_source_checkout_app_is_discoverable():
    assert app_path() == Path(__file__).resolve().parents[1] / "app.py"
