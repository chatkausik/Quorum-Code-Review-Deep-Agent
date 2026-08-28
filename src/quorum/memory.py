"""Per-repo statistics that survive process restarts.

This legacy-compatible store intentionally contains aggregate counters only.
Finding-level decisions, run health, and evaluation cases live in the SQLite
improvement store so model-facing graph state never owns durable feedback.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.store.memory import InMemoryStore

from quorum.config import LEGACY_MEMORY_DIR, MEMORY_DIR

NAMESPACE = ("review_memory",)


def repo_key(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def _filename(namespace: tuple[str, ...], key: str) -> str:
    """Flatten a namespace + key into one safe filename."""
    joined = "__".join((*namespace, key))
    return joined.replace("/", "__").replace("\\", "__") + ".json"


def empty_stats() -> dict[str, Any]:
    return {"total_runs": 0, "total_comments_posted": 0, "last_review_at": None}


class FileBackedStore(InMemoryStore):
    """An InMemoryStore that loads from and writes through to disk."""

    def __init__(self, directory: Path | str = MEMORY_DIR) -> None:
        super().__init__()
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        # Only the default location adopts pre-rename data; an explicitly
        # chosen directory must stay exactly what the caller asked for.
        if self.directory == Path(MEMORY_DIR).expanduser():
            self._migrate_legacy()
        self._load()

    def _migrate_legacy(self) -> None:
        """Adopt stats written under the pre-rename directory.

        Only runs when this store is empty, so it can never clobber newer data.
        """
        if any(self.directory.glob("*.json")):
            return
        legacy = LEGACY_MEMORY_DIR.expanduser()
        if not legacy.is_dir() or legacy == self.directory:
            return
        for path in legacy.glob("*.json"):
            try:
                shutil.copy2(path, self.directory / path.name)
            except OSError:
                continue

    def _load(self) -> None:
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                namespace = tuple(record["namespace"])
                super().put(namespace, record["key"], record["value"])
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                # A corrupt memory file must never prevent a review from running.
                continue

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, Any], **kwargs: Any) -> None:  # type: ignore[override]
        super().put(namespace, key, value, **kwargs)
        payload = {"namespace": list(namespace), "key": key, "value": value}
        path = self.directory / _filename(namespace, key)
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            # Losing persistence is preferable to failing the review.
            pass

    # -- convenience used by run_review, outside any graph context ----------

    def get_stats(self, owner: str, repo: str) -> dict[str, Any]:
        item = self.get(NAMESPACE, repo_key(owner, repo))
        return dict(item.value) if item else empty_stats()

    def record_run(self, owner: str, repo: str, comments_posted: int = 0) -> dict[str, Any]:
        stats = self.get_stats(owner, repo)
        stats["total_runs"] = int(stats.get("total_runs", 0)) + 1
        stats["total_comments_posted"] = (
            int(stats.get("total_comments_posted", 0)) + comments_posted
        )
        stats["last_review_at"] = datetime.now(timezone.utc).isoformat()
        self.put(NAMESPACE, repo_key(owner, repo), stats)
        return stats

    def record_posted(self, owner: str, repo: str, count: int) -> dict[str, Any]:
        stats = self.get_stats(owner, repo)
        stats["total_comments_posted"] = (
            int(stats.get("total_comments_posted", 0)) + count
        )
        stats["last_review_at"] = datetime.now(timezone.utc).isoformat()
        self.put(NAMESPACE, repo_key(owner, repo), stats)
        return stats
