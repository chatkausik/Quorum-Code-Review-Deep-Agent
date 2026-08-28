"""Regex search over file content, exposed to the orchestrator and subagents."""

from __future__ import annotations

import time
from pathlib import PurePosixPath

import regex as regex_engine
from langchain.tools import tool
from langchain_core.tools import BaseTool

MAX_MATCHES = 100
MAX_PATTERN_LENGTH = 500
MAX_SEARCH_SECONDS = 1.0
MAX_LINE_SECONDS = 0.05


def _search_text(pattern: str, content: str) -> str:
    """Bounded regex implementation shared by public and VFS-bound tools."""

    if len(pattern) > MAX_PATTERN_LENGTH:
        return f"ERROR: pattern too long (max {MAX_PATTERN_LENGTH} characters)."
    try:
        compiled = regex_engine.compile(pattern)
    except regex_engine.error as exc:
        return f"ERROR: invalid regex {pattern!r}: {exc}"

    hits: list[str] = []
    deadline = time.monotonic() + MAX_SEARCH_SECONDS
    for lineno, line in enumerate(content.splitlines(), start=1):
        # Guard against pathological backtracking on very long lines.
        if len(line) > 5000:
            line = line[:5000]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return f"ERROR: regex search timed out after {MAX_SEARCH_SECONDS:.1f}s."
        try:
            match = compiled.search(line, timeout=min(MAX_LINE_SECONDS, remaining))
        except TimeoutError:
            return f"ERROR: regex search timed out after {MAX_SEARCH_SECONDS:.1f}s."
        if match:
            hits.append(f"line {lineno}: {line.strip()}")
            if len(hits) >= MAX_MATCHES:
                hits.append(f"... truncated at {MAX_MATCHES} matches")
                break

    if not hits:
        return f"No matches for {pattern!r}."
    return "\n".join(hits)


@tool
def regex_search(pattern: str, content: str) -> str:
    """Search supplied text with a bounded regular expression.

    This compatibility tool is useful outside the review graph. Agents receive
    the VFS-bound variant below so source does not have to be reproduced in a
    model-generated tool argument.
    """
    return _search_text(pattern, content)


def _is_pr_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(
        path.startswith("/pr/")
        and "\x00" not in path
        and all(part not in ("", ".", "..") for part in candidate.parts[1:])
    )


def make_regex_search(backend) -> BaseTool:
    """Create a regex tool bound to immutable source in one review backend."""

    @tool("regex_search")
    def bound_regex_search(pattern: str, path: str) -> str:
        """Search one frozen review file by exact VFS path.

        Args:
            pattern: A Python regular expression.
            path: Exact immutable path under /pr/ returned by the review manifest.
        """
        if not _is_pr_path(path):
            return f"REJECTED: regex_search paths must be safe files below /pr/; got {path!r}."
        result = backend.read(path)
        if result.error or not result.file_data:
            return f"ERROR: {path} is not present in the frozen review filesystem."

        from deepagents.backends.utils import file_data_to_string

        return _search_text(pattern, file_data_to_string(result.file_data))

    return bound_regex_search
