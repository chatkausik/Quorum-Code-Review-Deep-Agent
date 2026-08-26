"""Per-repo statistics must survive a process restart."""

from __future__ import annotations

import json

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
        files = list(store_dir.glob("*.json"))
        assert len(files) == 1
        assert "/" not in files[0].name

    def test_corrupt_file_does_not_break_loading(self, store_dir):
        store = FileBackedStore(store_dir)
        store.record_run("acme", "widgets")
        (store_dir / "corrupt.json").write_text("{not json", encoding="utf-8")
        reloaded = FileBackedStore(store_dir)
        assert reloaded.get_stats("acme", "widgets")["total_runs"] == 1

    def test_written_file_is_readable_json(self, store_dir):
        FileBackedStore(store_dir).record_run("acme", "widgets")
        payload = json.loads(next(store_dir.glob("*.json")).read_text())
        assert payload["namespace"] == list(NAMESPACE)
        assert payload["key"] == repo_key("acme", "widgets")


class TestPostedCounterIntegrity:
    """total_comments_posted is owned by the post step, never by the agent."""

    def test_agent_cannot_write_the_posted_counter(self):
        from quorum.tools.memory_tools import write_review_memory

        params = write_review_memory.args_schema.model_json_schema()["properties"]
        assert "total_comments_posted" not in params
        assert set(params) == {"owner", "repo", "total_runs"}

    def test_existing_posted_count_survives_an_agent_write(self, store_dir):
        from langgraph.config import get_store

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
