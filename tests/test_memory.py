"""Per-repo statistics must survive a process restart."""

from __future__ import annotations

import json
import sqlite3

import pytest

from quorum.memory import NAMESPACE, FileBackedStore, empty_stats, repo_key


@pytest.fixture
def store_dir(tmp_path):
    return tmp_path / "memory"


class TestPersistence:
    def test_unknown_repo_returns_empty_stats(self, store_dir):
        assert FileBackedStore(store_dir).get_stats("acme", "widgets") == empty_stats()

    def test_round_trips_across_instances(self, store_dir):
        FileBackedStore(store_dir).record_run("acme", "widgets", comments_posted=3)
        reloaded = FileBackedStore(store_dir).get_stats("acme", "widgets")
        assert reloaded["total_runs"] == 1
        assert reloaded["total_comments_posted"] == 3
        assert reloaded["last_review_at"] is not None

    def test_runs_accumulate(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        store.record_run("acme", "widgets")
        assert FileBackedStore(store_dir).get_stats("acme", "widgets")["total_runs"] == 2

    def test_posted_counts_accumulate_separately(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        store.record_posted("acme", "widgets", 5)
        stats = FileBackedStore(store_dir).get_stats("acme", "widgets")
        assert stats == {
            "total_runs": 1,
            "total_comments_posted": 5,
            "last_review_at": stats["last_review_at"],
        }

    def test_repos_are_isolated(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        store.record_run("other", "project")
        reloaded = FileBackedStore(store_dir)
        assert reloaded.get_stats("acme", "widgets")["total_runs"] == 1
        assert reloaded.get_stats("other", "project")["total_runs"] == 1

    def test_slash_in_key_does_not_escape_the_directory(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        assert store.path.parent == store_dir
        with sqlite3.connect(store.path) as conn:
            key = conn.execute("SELECT key FROM store_entries").fetchone()[0]
        assert key == "acme/widgets"

    def test_corrupt_file_does_not_break_loading(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        (store_dir / "corrupt.json").write_text("{not json", encoding="utf-8")
        reloaded = FileBackedStore(store_dir)
        assert reloaded.get_stats("acme", "widgets")["total_runs"] == 1

    def test_written_record_is_readable_from_sqlite(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        with sqlite3.connect(store.path) as conn:
            namespace, key, value_json = conn.execute(
                "SELECT namespace,key,value_json FROM store_entries"
            ).fetchone()
        assert json.loads(namespace) == list(NAMESPACE)
        assert key == repo_key("acme", "widgets")
        assert json.loads(value_json)["total_runs"] == 1

    def test_separate_sessions_increment_atomically(self, store_dir):
        first = FileBackedStore(store_dir)
        second = FileBackedStore(store_dir)

        first.record_run("acme", "widgets")
        second.record_run("acme", "widgets")

        assert FileBackedStore(store_dir).get_stats("acme", "widgets")["total_runs"] == 2

    def test_legacy_json_is_imported_once(self, store_dir):
        store_dir.mkdir(parents=True)
        legacy = {
            "namespace": list(NAMESPACE),
            "key": repo_key("acme", "widgets"),
            "value": {"total_runs": 4, "total_comments_posted": 2, "last_review_at": None},
        }
        (store_dir / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")

        store = FileBackedStore(store_dir)
        assert store.get_stats("acme", "widgets")["total_runs"] == 4
        # Reopening must not import and add the same legacy value again.
        assert FileBackedStore(store_dir).get_stats("acme", "widgets")["total_runs"] == 4


class TestPostedCounterIntegrity:
    """total_comments_posted is owned by the post step, never by the agent."""

    def test_agent_cannot_write_the_posted_counter(self):
        from quorum.tools.memory_tools import write_review_memory

        params = write_review_memory.args_schema.model_json_schema()["properties"]
        assert "total_comments_posted" not in params
        assert set(params) == {"owner", "repo", "total_runs"}

    def test_existing_posted_count_survives_an_agent_write(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_posted("acme", "widgets", 7)

        # Simulate the tool body against the same store.
        key = repo_key("acme", "widgets")
        stats = dict(store.get(NAMESPACE, key).value)
        stats["total_runs"] = 3
        stats.setdefault("total_comments_posted", 0)
        store.put(NAMESPACE, key, stats)

        reloaded = FileBackedStore(store_dir).get_stats("acme", "widgets")
        assert reloaded["total_runs"] == 3
        assert reloaded["total_comments_posted"] == 7

    def test_post_step_is_the_only_writer(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        assert store.get_stats("acme", "widgets")["total_comments_posted"] == 0
        store.record_posted("acme", "widgets", 4)
        assert store.get_stats("acme", "widgets")["total_comments_posted"] == 4
