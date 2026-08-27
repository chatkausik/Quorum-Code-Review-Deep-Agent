"""Typed review artifacts shared by the agent, the UI, and the post step."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
Category = Literal["correctness", "security", "tests"]

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


class ReviewComment(BaseModel):
    """A single candidate review comment produced by the agent."""

    path: str
    line: int = Field(ge=1, description="1-based line number in the file at head")
    severity: Severity
    category: Category
    confidence: int = Field(ge=0, le=100)
    anchor_text: str = Field(min_length=1, description="exact verbatim line of code")
    body: str
    suggestion: Optional[str] = None
    title: Optional[str] = Field(
        default=None, description="short noun phrase naming the issue"
    )

    def summary(self, limit: int = 78) -> str:
        """A one-line label for the findings list.

        Prefers an explicit title; otherwise takes the first sentence of the
        body, so findings produced before `title` existed still read well.
        """
        if self.title and self.title.strip():
            text = self.title.strip()
        else:
            first = re.split(r"(?<=[.!?])\s", self.body.strip(), maxsplit=1)[0]
            text = first.strip() or self.body.strip()
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    def sort_key(self) -> tuple[int, int]:
        """Most severe first, then most confident."""
        return (SEVERITY_ORDER.get(self.severity, 9), -self.confidence)

    def formatted_body(self) -> str:
        """Body with the attribution prefix required on every posted comment."""
        header = f"**[AI Review · {self.severity.upper()} / {self.category}]**"
        parts = [f"{header}\n\n{self.body.strip()}"]
        if self.suggestion:
            parts.append(f"\n\n**Suggestion:**\n```suggestion\n{self.suggestion}\n```")
        return "".join(parts)


@dataclass(frozen=True)
class ReviewContext:
    """Runtime context passed into the graph, read by PRMetadataMiddleware.

    Fetched deterministically before the agent starts, so PR metadata and the
    head SHA never depend on the LLM reporting them correctly.
    """

    owner: str
    repo: str
    pr_number: int
    title: str
    body: str
    head_sha: str
    base_sha: str
    author: str

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class ReviewResult:
    """What run_review hands back to the UI."""

    comments: list[ReviewComment]
    context: ReviewContext
    total_cost_usd: float
    llm_calls: int
    budget_exceeded: bool = False
    error: str | None = None
    trace: list[str] = None  # type: ignore[assignment]
    # Why candidate findings did not make the final list, keyed by reason.
    dropped: dict[str, int] = None  # type: ignore[assignment]
    subagent_reported: int = 0
    # LangSmith links, populated only when tracing is enabled.
    trace_url: str | None = None
    project_url: str | None = None

    def __post_init__(self) -> None:
        if self.trace is None:
            self.trace = []
        if self.dropped is None:
            self.dropped = {}

    def drop_summary(self) -> str:
        """Human-readable account of filtered findings, empty when none."""
        if not self.dropped:
            return ""
        parts = [f"{count} {reason}" for reason, count in sorted(self.dropped.items())]
        return ", ".join(parts)


@dataclass
class PostResult:
    """Outcome of the deterministic post step."""

    posted: int
    dropped_off_diff: list[str]
    re_anchored: list[str]
    review_url: str | None = None
