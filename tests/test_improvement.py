from __future__ import annotations

import json
import sqlite3

from quorum.evaluation import evaluate_run_health
from quorum.improvement import ImprovementStore
from quorum.models import HealthCheck, PostResult, ReviewComment, ReviewContext, ReviewResult


def make_comment(**overrides) -> ReviewComment:
    values = {
        "path": "src/app.py",
        "line": 1,
        "severity": "high",
        "category": "security",
        "confidence": 91,
        "anchor_text": 'password = "secret"',
        "title": "Hardcoded password",
        "body": "A credential is embedded in source.",
    }
    values.update(overrides)
    return ReviewComment(**values)


def make_result(*, checks=None) -> ReviewResult:
    context = ReviewContext(
        owner="acme", repo="widgets", pr_number=4, title="T", body="",
        head_sha="abc123", base_sha="def456", author="dev",
    )
    return ReviewResult(
        comments=[make_comment()], context=context, total_cost_usd=0.1, llm_calls=3,
        run_id="review-1", profile="balanced", expected_files=1, files_reviewed=1,
        health_checks=checks or [],
    )


def test_health_contract_detects_missing_mount_and_anchor():
    checks = evaluate_run_health(
        expected_paths={"src/app.py"},
        comments=[make_comment()],
        state={"files": {}},
        error=None,
        budget_exceeded=False,
        total_cost_usd=0.1,
        llm_calls=3,
        max_cost_usd=1.0,
        max_llm_calls=25,
    )
    by_name = {check.name: check for check in checks}

    assert not by_name["eligible_file_coverage"].passed
    assert not by_name["frozen_source_mount"].passed
    assert not by_name["finding_anchor_exists"].passed
    assert by_name["finding_path_scope"].passed
    assert by_name["run_budget"].passed


def test_health_contract_detects_source_mutation_but_counts_review_artifact():
    checks = evaluate_run_health(
        expected_paths={"src/app.py"},
        expected_content={"src/app.py": "trusted\n"},
        comments=[],
        state={
            "files": {
                "/pr/src/app.py": {"content": "poisoned\n"},
                "/findings/src/app.py.json": {"content": '{"comments": []}'},
            }
        },
        error=None,
        budget_exceeded=False,
        total_cost_usd=0.1,
        llm_calls=3,
        max_cost_usd=1.0,
        max_llm_calls=25,
    )
    by_name = {check.name: check for check in checks}

    assert not by_name["source_content_integrity"].passed
    assert by_name["eligible_file_coverage"].passed


def test_health_contract_rejects_invalid_or_out_of_scope_artifacts():
    checks = evaluate_run_health(
        expected_paths={"src/app.py"},
        expected_content={"src/app.py": "trusted\n"},
        comments=[],
        state={
            "files": {
                "/pr/src/app.py": {"content": "trusted\n"},
                "/findings/src/app.py.json": {"content": "not json"},
                "/findings/other.py.json": {"content": '{"comments": []}'},
            }
        },
        error=None,
        budget_exceeded=False,
        total_cost_usd=0.1,
        llm_calls=3,
        max_cost_usd=1.0,
        max_llm_calls=25,
    )
    by_name = {check.name: check for check in checks}

    assert not by_name["finding_artifact_validity"].passed
    assert not by_name["finding_artifact_scope"].passed


def test_health_contract_surfaces_missing_diff_and_preapproval_rejections():
    checks = evaluate_run_health(
        expected_paths={"src/app.py"},
        expected_content={"src/app.py": "x = 1\n"},
        expected_patches={"src/app.py": ""},
        postability_failures={"off_diff": ["src/app.py:1 — not on added side"]},
        comments=[],
        state={
            "files": {
                "/pr/src/app.py": {"content": "x = 1\n"},
                "/findings/src/app.py.json": {"content": '{"comments": []}'},
            }
        },
        error=None,
        budget_exceeded=False,
        total_cost_usd=0.1,
        llm_calls=3,
        max_cost_usd=1.0,
        max_llm_calls=25,
    )
    by_name = {check.name: check for check in checks}

    assert not by_name["diff_availability"].passed
    assert not by_name["finding_postability"].passed


def test_review_failures_become_deduplicated_improvement_issues(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    failure = HealthCheck(
        name="eligible_file_coverage", severity="high", passed=False,
        detail="One file was missed.", evidence={"missing": ["/pr/src/app.py"]},
    )
    result = make_result(checks=[failure])

    store.record_review(result)
    store.record_review(result)

    issues = store.list_issues("acme/widgets")
    assert len(issues) == 1
    assert issues[0]["occurrences"] == 1
    assert issues[0]["invariant"] == "eligible_file_coverage"


def test_provider_error_text_is_not_persisted(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    result = make_result()
    result.error = "ProviderError: echoed-secret-value"

    store.record_review(result)

    with sqlite3.connect(store.path) as conn:
        stored = conn.execute("SELECT error FROM review_runs").fetchone()[0]
    assert stored == "ProviderError"
    assert "echoed-secret-value" not in store.path.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_human_decisions_and_postability_create_sanitized_eval_cases(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    result = make_result()
    comment = result.comments[0]
    store.record_review(result)

    store.record_decisions(result, [comment])
    store.record_post_result(
        result,
        [comment],
        PostResult(
            posted=1,
            dropped_off_diff=[],
            re_anchored=[],
            posted_locations=["src/app.py:1"],
        ),
    )

    summary = store.summary("acme/widgets")
    assert summary["posted"] == 1
    assert summary["evaluation_cases"] == 2  # approved and posted labels

    with sqlite3.connect(store.path) as conn:
        title = conn.execute("SELECT title FROM finding_decisions").fetchone()[0]
        payloads = [
            json.loads(row[0])
            for row in conn.execute("SELECT payload_json FROM evaluation_cases")
        ]
    assert title is None
    assert all("title" not in payload for payload in payloads)
    serialized = json.dumps(payloads)
    assert comment.anchor_text not in serialized
    assert comment.body not in serialized


def test_changed_human_decision_replaces_the_old_eval_label(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    result = make_result()
    comment = result.comments[0]
    store.record_review(result)

    store.record_decisions(result, [])
    store.record_decisions(result, [comment])

    with sqlite3.connect(store.path) as conn:
        labels = [
            row[0]
            for row in conn.execute(
                "SELECT label FROM evaluation_cases ORDER BY label"
            )
        ]
        current = conn.execute("SELECT decision FROM finding_decisions").fetchone()[0]
    assert current == "approved"
    assert labels == ["approved"]


def _failing(name: str, detail: str = "Contract failed.") -> HealthCheck:
    return HealthCheck(
        name=name, severity="high", passed=False, detail=detail, evidence={},
    )


def _statuses(store, repository="acme/widgets", status="open"):
    return sorted(
        (issue["invariant"], issue["occurrences"])
        for issue in store.list_issues(repository, status=status)
    )


def test_re_recording_a_run_captures_a_check_that_failed_later(tmp_path):
    """Idempotency must not swallow a failure the store has never seen."""
    store = ImprovementStore(tmp_path / "improvement.db")
    first = make_result(checks=[_failing("eligible_file_coverage")])
    store.record_review(first)

    second = make_result(
        checks=[_failing("eligible_file_coverage"), _failing("run_budget")]
    )
    store.record_review(second)  # same run_id

    assert _statuses(store) == [("eligible_file_coverage", 1), ("run_budget", 1)]


def test_recurrence_in_a_later_run_increments_and_reopens(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    first = make_result(checks=[_failing("run_budget")])
    store.record_review(first)

    fingerprint = store.list_issues("acme/widgets")[0]["fingerprint"]
    store.set_issue_status(fingerprint, "fixed")
    assert _statuses(store) == []

    later = make_result(checks=[_failing("run_budget")])
    later.run_id = "review-2"
    store.record_review(later)

    assert _statuses(store) == [("run_budget", 2)]


def test_a_fixed_issue_is_not_reopened_by_re_persisting_the_same_run(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    result = make_result(checks=[_failing("run_budget")])
    store.record_review(result)
    fingerprint = store.list_issues("acme/widgets")[0]["fingerprint"]
    store.set_issue_status(fingerprint, "fixed")

    store.record_review(result)

    assert _statuses(store) == []
    assert _statuses(store, status="fixed") == [("run_budget", 1)]


def test_muted_issues_stay_muted_and_remain_listable(tmp_path):
    store = ImprovementStore(tmp_path / "improvement.db")
    first = make_result(checks=[_failing("run_budget")])
    store.record_review(first)
    fingerprint = store.list_issues("acme/widgets")[0]["fingerprint"]
    store.set_issue_status(fingerprint, "muted")

    later = make_result(checks=[_failing("run_budget")])
    later.run_id = "review-2"
    store.record_review(later)

    assert _statuses(store) == []
    assert _statuses(store, status="muted") == [("run_budget", 2)]

    store.set_issue_status(fingerprint, "open")
    assert _statuses(store) == [("run_budget", 2)]
