"""Regex search over file content, exposed to the orchestrator and subagents."""

from __future__ import annotations

import re

from langchain.tools import tool

MAX_MATCHES = 100
MAX_PATTERN_LENGTH = 500


@tool
def regex_search(pattern: str, content: str) -> str:
    """Search content for a regex pattern, returning matching line numbers and text.

    Args:
        pattern: A Python regular expression.
        content: The text to search, typically a file read from /pr/<name>.

    Returns:
        One "line <n>: <text>" entry per match, or a no-match / error message.
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        return f"ERROR: pattern too long (max {MAX_PATTERN_LENGTH} characters)."
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex {pattern!r}: {exc}"

    hits: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        # Guard against pathological backtracking on very long lines.
        if len(line) > 5000:
            line = line[:5000]
        if compiled.search(line):
            hits.append(f"line {lineno}: {line.strip()}")
            if len(hits) >= MAX_MATCHES:
                hits.append(f"... truncated at {MAX_MATCHES} matches")
                break

    if not hits:
        return f"No matches for {pattern!r}."
    return "\n".join(hits)
