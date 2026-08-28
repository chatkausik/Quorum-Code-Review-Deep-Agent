"""Offline scoring for recorded or fixture code-review findings."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from quorum.models import ReviewComment

FindingKey = tuple[str, int, str]


@dataclass(frozen=True)
class EvalScore:
    expected: int
    actual: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    anchor_accuracy: float

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def _key(item: dict[str, Any] | ReviewComment) -> FindingKey:
    if isinstance(item, ReviewComment):
        return item.path, item.line, item.category
    return str(item["path"]), int(item["line"]), str(item["category"])


def score_findings(
    expected: list[dict[str, Any]], actual: list[ReviewComment]
) -> EvalScore:
    """Score exact path/line/category matches and anchor fidelity."""
    expected_by_key = {_key(item): item for item in expected}
    actual_by_key = {_key(item): item for item in actual}
    expected_keys = set(expected_by_key)
    actual_keys = set(actual_by_key)
    true_keys = expected_keys & actual_keys

    tp = len(true_keys)
    fp = len(actual_keys - expected_keys)
    fn = len(expected_keys - actual_keys)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    anchored = 0
    comparable = 0
    for key in true_keys:
        expected_anchor = expected_by_key[key].get("anchor_text")
        if expected_anchor is None:
            continue
        comparable += 1
        if actual_by_key[key].anchor_text.strip() == str(expected_anchor).strip():
            anchored += 1
    anchor_accuracy = anchored / comparable if comparable else 1.0

    return EvalScore(
        expected=len(expected_keys),
        actual=len(actual_keys),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        anchor_accuracy=anchor_accuracy,
    )


def _load_list(path: Path, key: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{path} must contain a JSON list or a {key!r} list.")
    return items


def evaluate_files(expected_path: Path, actual_path: Path) -> EvalScore:
    expected = _load_list(expected_path, "expected")
    raw_actual = _load_list(actual_path, "comments")
    try:
        actual = [ReviewComment.model_validate(item) for item in raw_actual]
    except ValidationError as exc:
        raise ValueError(f"Invalid actual finding in {actual_path}: {exc}") from exc
    return score_findings(expected, actual)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("--min-precision", type=float, default=0.0)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--min-f1", type=float, default=0.0)
    parser.add_argument("--min-anchor-accuracy", type=float, default=0.0)
    args = parser.parse_args(argv)

    score = evaluate_files(args.expected, args.actual)
    print(json.dumps(score.as_dict(), indent=2, sort_keys=True))
    failures = []
    if score.precision < args.min_precision:
        failures.append(f"precision {score.precision:.3f} < {args.min_precision:.3f}")
    if score.recall < args.min_recall:
        failures.append(f"recall {score.recall:.3f} < {args.min_recall:.3f}")
    if score.f1 < args.min_f1:
        failures.append(f"F1 {score.f1:.3f} < {args.min_f1:.3f}")
    if score.anchor_accuracy < args.min_anchor_accuracy:
        failures.append(
            f"anchor accuracy {score.anchor_accuracy:.3f} < {args.min_anchor_accuracy:.3f}"
        )
    if failures:
        parser.exit(1, "Evaluation threshold failed: " + "; ".join(failures) + "\n")


if __name__ == "__main__":
    main()
