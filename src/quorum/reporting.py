"""Prioritized findings report, exported from the UI as markdown."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from quorum.models import ReviewComment, ReviewResult

SEVERITY_LABEL = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}


def build_report(result: ReviewResult, threshold: int) -> str:
    """Render findings as a prioritized markdown report."""
    ctx = result.context
    comments = sorted(result.comments, key=lambda c: c.sort_key())
    by_severity = Counter(c.severity for c in comments)
    by_category = Counter(c.category for c in comments)

    lines: list[str] = [
        f"# Code Review Report — {ctx.full_repo} PR #{ctx.pr_number}",
        "",
        f"**{ctx.title}**",
        "",
        f"- Author: `{ctx.author}`",
        f"- Head SHA: `{ctx.head_sha}`",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- LLM calls: {result.llm_calls} · Cost: ${result.total_cost_usd:.4f}",
        f"- Confidence threshold: {threshold}",
        "",
        "## Summary",
        "",
        f"{len(comments)} finding(s).",
        "",
        "| Severity | Count |",
        "| --- | --- |",
    ]
    for severity in ("critical", "high", "medium", "low"):
        if by_severity.get(severity):
            lines.append(f"| {SEVERITY_LABEL[severity]} | {by_severity[severity]} |")

    lines += ["", "| Category | Count |", "| --- | --- |"]
    for category, count in sorted(by_category.items()):
        lines.append(f"| {category} | {count} |")

    if result.budget_exceeded:
        lines += [
            "",
            "> **Note:** the run stopped at its budget ceiling. These findings "
            "may be incomplete.",
        ]

    failed_checks = [check for check in result.health_checks if not check.passed]
    lines += ["", "## Review Health", ""]
    if not failed_checks:
        lines.append(f"All {len(result.health_checks)} deterministic checks passed.")
    else:
        lines.append(
            f"{len(failed_checks)} of {len(result.health_checks)} deterministic checks failed:"
        )
        lines.append("")
        for check in failed_checks:
            lines.append(f"- **{check.name}** ({check.severity}): {check.detail}")

    lines += ["", "## Findings", ""]
    if not comments:
        lines.append("No findings above the reporting bar.")
    for index, comment in enumerate(comments, start=1):
        lines += _render_finding(index, comment, threshold)

    return "\n".join(lines) + "\n"


def _render_finding(index: int, comment: ReviewComment, threshold: int) -> list[str]:
    gate = "auto-approved" if comment.confidence >= threshold else "needs review"
    block = [
        f"### {index}. [{SEVERITY_LABEL[comment.severity]}] {comment.path}:{comment.line}",
        "",
        f"- Category: {comment.category}",
        f"- Confidence: {comment.confidence} ({gate})",
        "",
        "```",
        comment.anchor_text,
        "```",
        "",
        comment.body.strip(),
        "",
    ]
    if comment.suggestion:
        block += ["**Suggested fix:**", "", "```", comment.suggestion, "```", ""]
    return block
