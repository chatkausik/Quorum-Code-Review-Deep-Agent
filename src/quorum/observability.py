"""LangSmith tracing helpers.

Tracing is opt-in and must never break a review: every failure here is
swallowed, because losing an observability link is not worth losing a run.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import quote

from quorum.config import (
    LANGSMITH_HOST,
    LANGSMITH_PROJECT,
    enable_langsmith,
    langsmith_enabled,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def project_url() -> str | None:
    """Link to the LangSmith project dashboard.

    The real URL embeds the org and project UUIDs, which cannot be derived
    from the project name — so it is read from the API and cached.
    """
    if not langsmith_enabled():
        return None
    try:
        from langsmith import Client

        project = Client().read_project(project_name=LANGSMITH_PROJECT)
        url = getattr(project, "url", None)
        if url:
            return str(url)
    except Exception:  # noqa: BLE001 - a missing link must not fail the review
        logger.debug("Could not resolve LangSmith project URL", exc_info=True)
    return f"{LANGSMITH_HOST.rstrip('/')}/projects/p/{quote(LANGSMITH_PROJECT)}"


def run_url(run_id: str | None) -> str | None:
    """Resolve a permalink for one traced run.

    The working shape is `{project_url}/r/{run_id}` — the project URL already
    carries both the org and project UUIDs. Building `/o/{org}/r/{id}` without
    the project segment returns a 404.
    """
    if not run_id or not langsmith_enabled():
        return None
    base = project_url()
    if base:
        return f"{base.rstrip('/')}/r/{run_id}"
    return f"{LANGSMITH_HOST.rstrip('/')}/o/-/r/{run_id}"


@contextmanager
def capture_run():
    """Collect the traced root run, yielding a dict that gains a 'url' key.

    Yields an empty mapping when tracing is disabled, so callers need no
    conditional logic.
    """
    result: dict[str, str] = {}
    if not enable_langsmith():
        yield result
        return

    try:
        from langchain_core.tracers.context import collect_runs
    except ImportError:
        yield result
        return

    try:
        with collect_runs() as runs:
            yield result
    finally:
        try:
            collected = list(getattr(runs, "traced_runs", []) or [])
            if collected:
                root = collected[0]
                result["id"] = str(root.id)
                url = run_url(str(root.id))
                if url:
                    result["url"] = url
        except Exception:  # noqa: BLE001
            logger.debug("Could not capture LangSmith run", exc_info=True)
