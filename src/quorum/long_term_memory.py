"""Privacy-safe semantic long-term memory backed by the Mem0 Platform.

Local SQLite remains the source of truth for counters, health, and feedback.
Mem0 receives only fixed-schema aggregate outcome summaries and supplies
bounded, repository-scoped historical context to future reviews.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from quorum.config import (
    MEM0_API_KEY,
    MEM0_APP_ID,
    MEM0_ENABLED,
    MEM0_MAX_CONTEXT_CHARS,
    MEM0_TIMEOUT_SECONDS,
    MEM0_TOP_K,
)
from quorum.models import PostResult, ReviewComment, ReviewResult

logger = logging.getLogger(__name__)

MEMORY_QUERY = (
    "Prior sanitized code-review outcomes, recurring health-contract failures, "
    "human approval preferences, and posting reliability for this repository"
)
_SAFE_REJECTION_REASONS = {
    "false positive",
    "duplicate",
    "not actionable",
    "wrong severity",
    "wrong location",
}
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class Mem0Client(Protocol):
    """The small hosted-client surface Quorum relies on."""

    def search(
        self, query: str, *, filters: dict[str, str], top_k: int
    ) -> Any: ...

    def add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str,
        app_id: str,
        metadata: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True)
class MemoryContext:
    """Bounded semantic memory prepared for the orchestrator prompt."""

    text: str = ""
    count: int = 0


def _repo_user_id(repository: str) -> str:
    """Opaque, stable Mem0 entity id; raw repository names never leave Quorum."""
    digest = hashlib.sha256(repository.strip().lower().encode("utf-8")).hexdigest()
    return f"quorum-repo-{digest[:32]}"


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())


def _safe_contract_names(result: ReviewResult) -> list[str]:
    return sorted(
        check.name
        for check in result.health_checks
        if not check.passed and _SAFE_NAME.fullmatch(check.name)
    )


def _finding_mix(comments: list[ReviewComment]) -> str:
    counts = Counter((comment.category, comment.severity) for comment in comments)
    if not counts:
        return "none"
    return ", ".join(
        f"{category}/{severity}={count}"
        for (category, severity), count in sorted(counts.items())
    )


def _confidence_band(confidence: int) -> str:
    floor = min(90, max(0, confidence // 10 * 10))
    return "90-100" if floor == 90 else f"{floor}-{floor + 9}"


def _feedback_mix(comments: list[ReviewComment]) -> str:
    counts = Counter(
        (comment.category, comment.severity, _confidence_band(comment.confidence))
        for comment in comments
    )
    if not counts:
        return "none"
    return ", ".join(
        f"{category}/{severity}/confidence-{band}={count}"
        for (category, severity, band), count in sorted(counts.items())
    )


def _comment_key(comment: ReviewComment) -> tuple[str, int, str, str]:
    return (
        comment.path,
        comment.line,
        comment.category,
        comment.anchor_text.strip(),
    )


class Mem0LongTermMemory:
    """Best-effort Mem0 adapter with deterministic privacy boundaries.

    Mem0 is additive context, never the source of truth. Network, SDK, or API
    failures are logged by exception class only and never fail a review, save,
    or post operation.
    """

    def __init__(
        self,
        *,
        api_key: str | None = MEM0_API_KEY,
        enabled: bool = MEM0_ENABLED,
        app_id: str = MEM0_APP_ID,
        top_k: int = MEM0_TOP_K,
        max_context_chars: int = MEM0_MAX_CONTEXT_CHARS,
        timeout_seconds: int = MEM0_TIMEOUT_SECONDS,
        client: Mem0Client | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.enabled = bool(enabled and (self.api_key or client is not None))
        self.app_id = app_id
        self.top_k = top_k
        self.max_context_chars = max_context_chars
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._event_hashes: dict[str, str] = {}
        self._event_lock = Lock()

    @property
    def available(self) -> bool:
        return self.enabled

    def _get_client(self) -> Mem0Client | None:
        if not self.enabled:
            return None
        if self._client is None:
            import httpx
            from mem0 import MemoryClient

            http_client = httpx.Client(timeout=self.timeout_seconds)
            self._client = MemoryClient(api_key=self.api_key, client=http_client)
        return self._client

    def retrieve(self, repository: str) -> MemoryContext:
        """Search repository-scoped memories using a fixed, source-free query."""
        try:
            client = self._get_client()
            if client is None:
                return MemoryContext()
            response = client.search(
                MEMORY_QUERY,
                filters={
                    "user_id": _repo_user_id(repository),
                    "app_id": self.app_id,
                },
                top_k=self.top_k,
            )
            raw_items = response.get("results", []) if isinstance(response, dict) else response
            if not isinstance(raw_items, list):
                return MemoryContext()

            memories: list[str] = []
            seen: set[str] = set()
            used = 0
            for item in raw_items:
                content = item.get("memory") if isinstance(item, dict) else None
                line = _one_line(content).strip()
                if not line or line in seen:
                    continue
                rendered = f"- {line}"
                extra = len(rendered) + (1 if memories else 0)
                if used + extra > self.max_context_chars:
                    break
                memories.append(rendered)
                seen.add(line)
                used += extra
            return MemoryContext(text="\n".join(memories), count=len(memories))
        except Exception as exc:  # noqa: BLE001 - optional external memory
            logger.warning("Mem0 retrieval unavailable (%s)", type(exc).__name__)
            return MemoryContext()

    def _add(
        self,
        repository: str,
        content: str,
        *,
        kind: str,
        event_key: str,
        metadata: dict[str, Any],
    ) -> bool:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        try:
            with self._event_lock:
                if self._event_hashes.get(event_key) == content_hash:
                    return True
                client = self._get_client()
                if client is None:
                    return False
                client.add(
                    messages=[{"role": "assistant", "content": content}],
                    user_id=_repo_user_id(repository),
                    app_id=self.app_id,
                    metadata={
                        "source": "quorum",
                        "schema_version": 1,
                        "kind": kind,
                        "event_key": event_key,
                        **metadata,
                    },
                )
                self._event_hashes[event_key] = content_hash
                return True
        except Exception as exc:  # noqa: BLE001 - optional external memory
            logger.warning("Mem0 write unavailable (%s)", type(exc).__name__)
            return False

    def record_review(self, result: ReviewResult) -> bool:
        """Store an aggregate run outcome without repository or source details."""
        failures = _safe_contract_names(result)
        passed = sum(check.passed for check in result.health_checks)
        completed = result.error is None
        content = (
            "Sanitized Quorum review outcome. "
            f"Files expected={result.expected_files}; reviewed={result.files_reviewed}. "
            f"Retained findings={len(result.comments)}; mix={_finding_mix(result.comments)}. "
            f"Health passed={passed}/{len(result.health_checks)}; "
            f"failed contracts={','.join(failures) if failures else 'none'}. "
            f"Budget halted={'yes' if result.budget_exceeded else 'no'}; "
            f"completed={'yes' if completed else 'no'}; profile={result.profile}."
        )
        return self._add(
            result.context.full_repo,
            content,
            kind="review_outcome",
            event_key=f"{result.run_id}:review",
            metadata={
                "expected_files": result.expected_files,
                "reviewed_files": result.files_reviewed,
                "finding_count": len(result.comments),
                "health_failure_count": len(failures),
                "completed": completed,
            },
        )

    def record_decisions(
        self,
        result: ReviewResult,
        selected: list[ReviewComment],
        *,
        rejection_reason: str | None = None,
    ) -> bool:
        """Store aggregate reviewer preferences without finding content or paths."""
        selected_keys = {_comment_key(comment) for comment in selected}
        approved = [
            comment for comment in result.comments if _comment_key(comment) in selected_keys
        ]
        rejected = [
            comment for comment in result.comments if _comment_key(comment) not in selected_keys
        ]
        safe_reason = (
            rejection_reason
            if rejection_reason in _SAFE_REJECTION_REASONS
            else "not specified"
        )
        content = (
            "Sanitized Quorum human feedback. "
            f"Approved={len(approved)} ({_feedback_mix(approved)}). "
            f"Rejected={len(rejected)} ({_feedback_mix(rejected)}). "
            f"Rejection reason={safe_reason}."
        )
        return self._add(
            result.context.full_repo,
            content,
            kind="human_feedback",
            event_key=f"{result.run_id}:human-feedback",
            metadata={
                "approved_count": len(approved),
                "rejected_count": len(rejected),
            },
        )

    def record_post_result(
        self,
        result: ReviewResult,
        selected: list[ReviewComment],
        post: PostResult,
    ) -> bool:
        """Store aggregate posting reliability, never GitHub locations or URLs."""
        failed = max(0, len(selected) - post.posted)
        content = (
            "Sanitized Quorum posting outcome. "
            f"Selected={len(selected)}; posted={post.posted}; failed validation={failed}; "
            f"re-anchored={len(post.re_anchored)}; "
            f"invalid anchors={len(post.dropped_invalid_anchor)}; "
            f"off-diff={len(post.dropped_off_diff)}."
        )
        return self._add(
            result.context.full_repo,
            content,
            kind="posting_outcome",
            event_key=f"{result.run_id}:posting",
            metadata={
                "selected_count": len(selected),
                "posted_count": post.posted,
                "validation_failure_count": failed,
            },
        )
