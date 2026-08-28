"""Concurrency-safe per-repository statistics and graph store persistence.

The store keeps LangGraph's in-memory interface, but SQLite owns persistence
and aggregate increments. The previous JSON implementation performed a
read-modify-write in each Streamlit session, so concurrent sessions could lose
updates.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.store.memory import InMemoryStore

from quorum.config import LEGACY_MEMORY_DIR, MEMORY_DIR

NAMESPACE = ("review_memory",)
DB_NAME = "review_memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_entries (
  namespace TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (namespace, key)
);
"""


def repo_key(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def empty_stats() -> dict[str, Any]:
    return {"total_runs": 0, "total_comments_posted": 0, "last_review_at": None}


def _namespace_key(namespace: tuple[str, ...]) -> str:
    return json.dumps(list(namespace), separators=(",", ":"))


class FileBackedStore(InMemoryStore):
    """An ``InMemoryStore`` with a transactional SQLite backing store."""

    def __init__(self, directory: Path | str = MEMORY_DIR) -> None:
        super().__init__()
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / DB_NAME
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._import_legacy_json_if_empty()
        self._load()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _import_legacy_json_if_empty(self) -> None:
        """Import the old JSON format once without overwriting SQLite data."""
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM store_entries LIMIT 1").fetchone():
                return

        candidates = sorted(self.directory.glob("*.json"))
        if not candidates and self.directory == Path(MEMORY_DIR).expanduser():
            legacy = LEGACY_MEMORY_DIR.expanduser()
            if legacy.is_dir() and legacy != self.directory:
                candidates = sorted(legacy.glob("*.json"))

        for path in candidates:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                namespace = tuple(record["namespace"])
                key = str(record["key"])
                value = dict(record["value"])
                self._write_entry(namespace, key, value)
            except (json.JSONDecodeError, KeyError, TypeError, OSError, sqlite3.Error):
                # A corrupt legacy record must never prevent a review from running.
                continue

    def _load(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT namespace, key, value_json FROM store_entries"
            ).fetchall()
        for namespace_json, key, value_json in rows:
            try:
                namespace = tuple(json.loads(namespace_json))
                value = json.loads(value_json)
                if isinstance(value, dict):
                    super().put(namespace, key, value)
            except (json.JSONDecodeError, TypeError):
                continue

    def _write_entry(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(value, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO store_entries(namespace,key,value_json,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET "
                "value_json=excluded.value_json, updated_at=excluded.updated_at",
                (_namespace_key(namespace), key, payload, now),
            )

    def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        **kwargs: Any,
    ) -> None:  # type: ignore[override]
        self._write_entry(namespace, key, value)
        super().put(namespace, key, value, **kwargs)

    def _read_entry(
        self, namespace: tuple[str, ...], key: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM store_entries WHERE namespace=? AND key=?",
                (_namespace_key(namespace), key),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
        return dict(value) if isinstance(value, dict) else None

    def get_stats(self, owner: str, repo: str) -> dict[str, Any]:
        """Read current counters from SQLite, not this instance's cache."""
        return self._read_entry(NAMESPACE, repo_key(owner, repo)) or empty_stats()

    def _increment_stats(
        self,
        owner: str,
        repo: str,
        *,
        runs: int = 0,
        comments: int = 0,
    ) -> dict[str, Any]:
        key = repo_key(owner, repo)
        namespace = _namespace_key(NAMESPACE)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value_json FROM store_entries WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            try:
                stats = json.loads(row[0]) if row else empty_stats()
            except (json.JSONDecodeError, TypeError):
                stats = empty_stats()
            if not isinstance(stats, dict):
                stats = empty_stats()
            stats["total_runs"] = int(stats.get("total_runs", 0)) + runs
            stats["total_comments_posted"] = (
                int(stats.get("total_comments_posted", 0)) + comments
            )
            stats["last_review_at"] = now
            conn.execute(
                "INSERT INTO store_entries(namespace,key,value_json,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET "
                "value_json=excluded.value_json, updated_at=excluded.updated_at",
                (namespace, key, json.dumps(stats, separators=(",", ":")), now),
            )
        super().put(NAMESPACE, key, stats)
        return dict(stats)

    def record_run(
        self, owner: str, repo: str, comments_posted: int = 0
    ) -> dict[str, Any]:
        return self._increment_stats(
            owner, repo, runs=1, comments=max(0, int(comments_posted))
        )

    def record_posted(self, owner: str, repo: str, count: int) -> dict[str, Any]:
        return self._increment_stats(owner, repo, comments=max(0, int(count)))
