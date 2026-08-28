"""Test isolation from optional external observability services."""

from __future__ import annotations

import os

# A developer may have real tracing credentials in .env. Unit tests must remain
# offline and must never export fixture or repository content as traces.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
