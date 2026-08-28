"""Drive the Improve tab through Streamlit's AppTest.

The panel is the only route to a muted invariant, so its status transitions
are covered here rather than trusted to review.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quorum.improvement import ImprovementStore
from quorum.memory import FileBackedStore
from quorum.models import HealthCheck, ReviewComment, ReviewContext, ReviewResult

APP = Path(__file__).resolve().parents[1] / "app.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _result(checks: list[HealthCheck]) -> ReviewResult:
    context = ReviewContext(
        owner="acme", repo="widgets", pr_number=4, title="T", body="",
        head_sha="abc123", base_sha="def456", author="dev",
    )
    comment = ReviewComment(
        path="src/app.py", line=1, severity="high", category="security",
        confidence=91, anchor_text='password = "secret"', body="Credential in source.",
    )
    return ReviewResult(
        comments=[comment], context=context, total_cost_usd=0.1, llm_calls=3,
        run_id="review-1", profile="balanced", expected_files=1, files_reviewed=1,
        health_checks=checks,
    )


def _app(tmp_path):
    failure = HealthCheck(
        name="run_budget", severity="high", passed=False,
        detail="The review exceeded its budget.", evidence={"calls": 40},
    )
    result = _result([failure])
    store = ImprovementStore(tmp_path / "improvement.db")
    store.record_review(result)

    app = AppTest.from_file(str(APP), default_timeout=60)
    app.session_state["store"] = FileBackedStore(tmp_path / "stats")
    app.session_state["improvement_store"] = store
    app.session_state["result"] = result
    return app.run(), store


def _labels(app) -> list[str]:
    return [button.label for button in app.button]


def test_muting_an_issue_keeps_it_reachable_and_reversible(tmp_path):
    app, store = _app(tmp_path)
    assert not app.exception

    assert any(label == "Mute" for label in _labels(app))
    mute = [b for b in app.button if b.label == "Mute"][0]
    app = mute.click().run()
    assert not app.exception
    assert store.list_issues("acme/widgets", status="open") == []
    assert len(store.list_issues("acme/widgets", status="muted")) == 1

    # The muted issue must still be reachable from the panel, not stranded.
    muted_tab = [r for r in app.radio if r.key == "improve-status"][0]
    app = muted_tab.set_value("muted").run()
    assert not app.exception
    assert any(b.label == "Unmute" for b in app.button)

    unmute = [b for b in app.button if b.label == "Unmute"][0]
    app = unmute.click().run()
    assert not app.exception
    assert len(store.list_issues("acme/widgets", status="open")) == 1
