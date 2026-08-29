"""Test isolation from optional external observability services."""

from __future__ import annotations

import os

# A developer may have real tracing credentials in .env. Unit tests must remain
# offline and must never export fixture or repository content as traces.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
# A developer may also have a real hosted-memory credential in .env. Tests use
# injected fake clients and must never read or write the developer's Mem0 data.
os.environ["MEM0_ENABLED"] = "false"
