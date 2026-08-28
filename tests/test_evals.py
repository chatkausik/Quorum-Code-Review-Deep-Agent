from __future__ import annotations

import json

import pytest

from quorum.evals import evaluate_files, main, score_findings
from quorum.models import ReviewComment


def comment(path: str, line: int, category: str, anchor: str = "x = 1") -> ReviewComment:
    return ReviewComment(
        path=path,
        line=line,
        severity="high",
        category=category,
        confidence=90,
        anchor_text=anchor,
        body="Issue.",
    )


def test_scores_precision_recall_and_anchor_accuracy():
    expected = [
        {"path": "a.py", "line": 1, "category": "security", "anchor_text": "x = 1"},
        {"path": "b.py", "line": 2, "category": "correctness", "anchor_text": "y = 2"},
    ]
    actual = [
        comment("a.py", 1, "security", "x = 1"),
        comment("extra.py", 9, "tests"),
    ]

    score = score_findings(expected, actual)
    assert score.true_positive == 1
    assert score.false_positive == 1
    assert score.false_negative == 1
    assert score.precision == pytest.approx(0.5)
    assert score.recall == pytest.approx(0.5)
    assert score.anchor_accuracy == 1.0


def test_fixture_files_round_trip(tmp_path):
    expected = tmp_path / "expected.json"
    actual = tmp_path / "actual.json"
    expected.write_text(
        json.dumps(
            {"expected": [{"path": "a.py", "line": 1, "category": "security"}]}
        ),
        encoding="utf-8",
    )
    actual.write_text(
        json.dumps({"comments": [comment("a.py", 1, "security").model_dump()]}),
        encoding="utf-8",
    )

    score = evaluate_files(expected, actual)
    assert score.precision == score.recall == score.f1 == 1.0


def test_cli_can_enforce_f1_threshold(tmp_path):
    expected = tmp_path / "expected.json"
    actual = tmp_path / "actual.json"
    expected.write_text(
        json.dumps(
            {
                "expected": [
                    {"path": "a.py", "line": 1, "category": "security"},
                    {"path": "b.py", "line": 2, "category": "correctness"},
                ]
            }
        ),
        encoding="utf-8",
    )
    actual.write_text(
        json.dumps({"comments": [comment("a.py", 1, "security").model_dump()]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        main([str(expected), str(actual), "--min-f1", "0.8"])

    assert exc_info.value.code == 1
