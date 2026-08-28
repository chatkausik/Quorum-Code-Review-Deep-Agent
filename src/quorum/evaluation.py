"""Deterministic health-contract checks for Quorum review runs."""

from __future__ import annotations

import json
from typing import Any

from quorum.models import HealthCheck, ReviewComment


def _file_content(files: dict[str, Any], path: str) -> str | None:
    data = files.get(path)
    content = data.get("content") if isinstance(data, dict) else data
    return content if isinstance(content, str) else None


def _error_type(error: str | None) -> str | None:
    if not error:
        return None
    prefix = error.split(":", 1)[0].strip()
    return prefix[:80] if prefix.replace("_", "").isalnum() else "review_error"


def evaluate_run_health(
    *,
    expected_paths: set[str],
    expected_content: dict[str, str] | None = None,
    expected_patches: dict[str, str] | None = None,
    postability_failures: dict[str, list[str]] | None = None,
    comments: list[ReviewComment],
    state: dict[str, Any],
    error: str | None,
    budget_exceeded: bool,
    total_cost_usd: float,
    llm_calls: int,
    max_cost_usd: float,
    max_llm_calls: int,
) -> list[HealthCheck]:
    """Evaluate invariants from trusted run state, never from model claims."""
    files = state.get("files") or {}
    expected_content = expected_content or {}
    patch_evidence_supplied = expected_patches is not None
    expected_patches = expected_patches or {}
    postability_failures = postability_failures or {}
    expected_vfs = {f"/pr/{path}" for path in expected_paths}
    mounted_vfs = {path for path in files if path.startswith("/pr/")}
    missing = sorted(expected_vfs - mounted_vfs)
    unexpected = sorted(mounted_vfs - expected_vfs)

    expected_artifacts = {f"/findings/{path}.json" for path in expected_paths}
    actual_artifacts = {
        path for path in files if path.startswith("/findings/")
    }
    missing_artifacts = sorted(expected_artifacts - actual_artifacts)
    unexpected_artifacts = sorted(actual_artifacts - expected_artifacts)
    invalid_artifacts: list[str] = []
    for path in sorted(expected_artifacts & actual_artifacts):
        content = _file_content(files, path)
        try:
            payload = json.loads(content) if content is not None else None
        except json.JSONDecodeError:
            payload = None
        items = payload.get("comments") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            invalid_artifacts.append(path)
    modified_sources = sorted(
        path
        for path, trusted_content in expected_content.items()
        if _file_content(files, f"/pr/{path}") != trusted_content
    )
    truncated_sources = sorted(
        path
        for path, content in expected_content.items()
        if content.endswith("\n... file truncated")
    )
    missing_patches = (
        sorted(path for path in expected_paths if not expected_patches.get(path))
        if patch_evidence_supplied
        else []
    )

    checks = [
        HealthCheck(
            name="frozen_source_mount",
            severity="high",
            passed=not missing,
            detail=(
                "Every eligible changed file was mounted from the frozen head."
                if not missing
                else f"{len(missing)} frozen source file(s) were not mounted."
            ),
            evidence={
                "expected": len(expected_vfs),
                "mounted": len(mounted_vfs),
                "missing": missing,
            },
        ),
        HealthCheck(
            name="vfs_path_identity",
            severity="high",
            passed=not unexpected,
            detail=(
                "Mounted paths preserve the frozen repository manifest."
                if not unexpected
                else f"{len(unexpected)} mounted path(s) were outside the manifest."
            ),
            evidence={"unexpected": unexpected},
        ),
        HealthCheck(
            name="source_content_integrity",
            severity="critical",
            passed=not modified_sources,
            detail=(
                "Mounted source matches the content frozen before the run."
                if not modified_sources
                else f"{len(modified_sources)} mounted source file(s) changed during the run."
            ),
            evidence={"modified": modified_sources},
        ),
        HealthCheck(
            name="eligible_file_coverage",
            severity="high",
            passed=not missing_artifacts,
            detail=(
                "Every eligible file produced a review artifact."
                if not missing_artifacts
                else f"{len(missing_artifacts)} eligible file(s) have no review artifact."
            ),
            evidence={"missing": missing_artifacts},
        ),
        HealthCheck(
            name="finding_artifact_scope",
            severity="high",
            passed=not unexpected_artifacts,
            detail=(
                "Every findings artifact belongs to the frozen manifest."
                if not unexpected_artifacts
                else f"{len(unexpected_artifacts)} findings artifact(s) were out of scope."
            ),
            evidence={"unexpected": unexpected_artifacts},
        ),
        HealthCheck(
            name="finding_artifact_validity",
            severity="high",
            passed=not invalid_artifacts,
            detail=(
                "Every findings artifact contains a JSON comments list."
                if not invalid_artifacts
                else f"{len(invalid_artifacts)} findings artifact(s) were invalid."
            ),
            evidence={"invalid": invalid_artifacts},
        ),
        HealthCheck(
            name="source_truncation",
            severity="medium",
            passed=not truncated_sources,
            detail=(
                "Every eligible source file was reviewed in full."
                if not truncated_sources
                else "At least one eligible source file exceeded the review size limit."
            ),
            evidence={"truncated": truncated_sources},
        ),
        HealthCheck(
            name="diff_availability",
            severity="high",
            passed=not missing_patches,
            detail=(
                "Every eligible file has frozen patch evidence."
                if not missing_patches
                else f"{len(missing_patches)} eligible file(s) had no patch evidence."
            ),
            evidence={"missing": missing_patches},
        ),
    ]

    unknown_findings = sorted({comment.path for comment in comments} - expected_paths)
    checks.append(
        HealthCheck(
            name="finding_path_scope",
            severity="critical",
            passed=not unknown_findings,
            detail=(
                "Every finding belongs to an eligible changed file."
                if not unknown_findings
                else f"Findings referenced {len(unknown_findings)} out-of-scope path(s)."
            ),
            evidence={"unknown_paths": unknown_findings},
        )
    )

    missing_anchors: list[str] = []
    invalid_lines: list[str] = []
    misplaced_anchors: list[str] = []
    for comment in comments:
        content = _file_content(files, f"/pr/{comment.path}")
        if content is None:
            missing_anchors.append(f"{comment.path}:{comment.line}")
            continue
        lines = content.splitlines()
        target = comment.anchor_text.strip()
        if not target or not any(line.strip() == target for line in lines):
            missing_anchors.append(f"{comment.path}:{comment.line}")
        if comment.line > len(lines):
            invalid_lines.append(f"{comment.path}:{comment.line}")
        elif target and lines[comment.line - 1].strip() != target:
            misplaced_anchors.append(f"{comment.path}:{comment.line}")

    checks.extend(
        [
            HealthCheck(
                name="finding_anchor_exists",
                severity="high",
                passed=not missing_anchors,
                detail=(
                    "Every finding anchor exists at the reviewed head."
                    if not missing_anchors
                    else f"{len(missing_anchors)} finding anchor(s) were not found."
                ),
                evidence={"invalid": missing_anchors},
            ),
            HealthCheck(
                name="finding_line_in_file",
                severity="high",
                passed=not invalid_lines,
                detail=(
                    "Every claimed line is inside its reviewed file."
                    if not invalid_lines
                    else f"{len(invalid_lines)} finding line(s) exceeded file length."
                ),
                evidence={"invalid": invalid_lines},
            ),
            HealthCheck(
                name="finding_anchor_at_claimed_line",
                severity="medium",
                passed=not misplaced_anchors,
                detail=(
                    "Every finding anchor matches its claimed line."
                    if not misplaced_anchors
                    else f"{len(misplaced_anchors)} finding(s) required re-anchoring."
                ),
                evidence={"misplaced": misplaced_anchors},
            ),
        ]
    )

    failed_postability = sorted(
        entry
        for entries in postability_failures.values()
        for entry in entries
    )
    checks.append(
        HealthCheck(
            name="finding_postability",
            severity="high",
            passed=not failed_postability,
            detail=(
                "Every retained candidate was postable on the frozen added-side diff."
                if not failed_postability
                else f"{len(failed_postability)} candidate finding(s) were rejected before approval."
            ),
            evidence={
                "invalid_anchors": postability_failures.get("invalid_anchor", []),
                "off_diff": postability_failures.get("off_diff", []),
            },
        )
    )

    within_budget = (
        not budget_exceeded
        and total_cost_usd <= max_cost_usd
        and llm_calls <= max_llm_calls
    )
    checks.append(
        HealthCheck(
            name="run_budget",
            severity="high",
            passed=within_budget,
            detail=(
                "The review completed within its configured budget."
                if within_budget
                else "The review exceeded or was halted by a configured budget."
            ),
            evidence={
                "cost": round(total_cost_usd, 6),
                "max_cost": max_cost_usd,
                "calls": llm_calls,
                "max_calls": max_llm_calls,
            },
        )
    )
    checks.append(
        HealthCheck(
            name="run_completed",
            severity="high",
            passed=error is None,
            detail=(
                "The review completed without an error."
                if error is None
                else "The review did not complete successfully."
            ),
            evidence={"error_type": _error_type(error)},
        )
    )
    return checks
