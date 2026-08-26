"""Long-term memory tools, reaching the store through the graph runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain.tools import tool
from langgraph.config import get_store

from quorum.memory import NAMESPACE, empty_stats, repo_key


@tool
def read_review_memory(owner: str, repo: str) -> str:
    """Read persisted statistics for a repository from previous review runs.

    Args:
        owner: Repository owner or organization.
        repo: Repository name.

    Returns:
        JSON with total_runs, total_comments_posted, and last_review_at.
    """
    store = get_store()
    item = store.get(NAMESPACE, repo_key(owner, repo))
    return json.dumps(item.value if item else empty_stats(), indent=2)


@tool
def write_review_memory(owner: str, repo: str, total_runs: int) -> str:
    """Persist the run count for a repository.

    Args:
        owner: Repository owner or organization.
        repo: Repository name.
        total_runs: Cumulative number of review runs, including this one.
    """
    store = get_store()
    key = repo_key(owner, repo)
    item = store.get(NAMESPACE, key)
    stats = dict(item.value) if item else empty_stats()

    stats["total_runs"] = max(0, int(total_runs))
    # total_comments_posted is owned by the deterministic post step. The agent
    # never posts, so it has no way to know this number — letting it write one
    # produced a memory claiming comments were posted when none ever were.
    stats.setdefault("total_comments_posted", 0)
    stats["last_review_at"] = datetime.now(timezone.utc).isoformat()

    store.put(NAMESPACE, key, stats)
    return f"Memory updated for {owner}/{repo}: {json.dumps(stats)}"
