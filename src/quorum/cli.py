"""Installed command-line entry point for the Streamlit application."""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path


def app_path() -> Path:
    """Locate app.py in a source checkout or wheel data installation."""
    source = Path(__file__).resolve().parents[2] / "app.py"
    installed = Path(sysconfig.get_path("data")) / "share" / "quorum" / "app.py"
    for candidate in (source, installed):
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Quorum's Streamlit app was not installed. Reinstall quorum-review or run "
        "`streamlit run app.py` from a source checkout."
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Launch Quorum through Streamlit, forwarding additional CLI arguments."""
    from streamlit.web import cli as streamlit_cli

    forwarded = list(sys.argv[1:] if argv is None else argv)
    sys.argv = ["streamlit", "run", str(app_path()), *forwarded]
    raise SystemExit(streamlit_cli.main())
