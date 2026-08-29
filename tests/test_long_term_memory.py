from __future__ import annotations

import json

from quorum.long_term_memory import MEMORY_QUERY, Mem0LongTermMemory
from quorum.models import HealthCheck, PostResult, ReviewComment, ReviewContext, ReviewResult


class FakeMem0Client:
    def __init__(self, search_response=None, *, fail_search: bool = False) -> None:
        self.search_response = search_response or {"results": []}
        self.fail_search = fail_search
        self.search_calls: list[dict] = []
        self.add_calls: list[dict] = []

    def search(self, query, *, filters, top_k):
        self.search_calls.append(
            {"query": query, "filters": filters, "top_k": top_k}
        )
        if self.fail_search:
            raise RuntimeError("remote response containing sensitive text")
        return self.search_response

    def add(self, messages, *, user_id, app_id, metadata):
        self.add_calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "app_id": app_id,
                "metadata": metadata,
            }
        )
        return {"results": [{"id": "memory-1"}]}


def make_comment(**overrides) -> ReviewComment:
    values = {
        "path": "private/customer-name.py",
        "line": 7,
        "severity": "high",
        "category": "security",
        "confidence": 94,
        "anchor_text": 'password = "private-secret"',
        "title": "Private customer credential",
        "body": "Private prose must never leave the process.",
    }
    values.update(overrides)
    return ReviewComment(**values)


def make_result() -> ReviewResult:
    return ReviewResult(
        comments=[make_comment()],
        context=ReviewContext(
            owner="private-owner",
            repo="secret-repository",
            pr_number=17,
            title="Private pull request title",
            body="Private pull request body",
            head_sha="private-head-sha",
            base_sha="private-base-sha",
            author="private-author",
        ),
        total_cost_usd=0.2,
        llm_calls=4,
        run_id="review-safe-id",
        profile="balanced",
        expected_files=2,
        files_reviewed=2,
        health_checks=[
            HealthCheck(
                name="finding_postability",
                severity="high",
                passed=False,
                detail="Private detail must not leave.",
                evidence={"path": "private/customer-name.py"},
            )
        ],
    )


def serialized_calls(client: FakeMem0Client) -> str:
    return json.dumps(client.add_calls, sort_keys=True)


def test_disabled_memory_never_calls_the_client():
    client = FakeMem0Client()
    memory = Mem0LongTermMemory(
        enabled=False,
        api_key="configured",
        client=client,
    )

    assert memory.retrieve("acme/widgets").count == 0
    assert not memory.record_review(make_result())
    assert client.search_calls == []
    assert client.add_calls == []


def test_retrieval_is_opaque_scoped_deduplicated_and_bounded():
    client = FakeMem0Client(
        {
            "results": [
                {"memory": "Security/high findings were often approved."},
                {"memory": "Security/high findings were often approved."},
                {"memory": "Postability failures recurred."},
                {"memory": "x" * 500},
            ]
        }
    )
    memory = Mem0LongTermMemory(
        enabled=True,
        client=client,
        app_id="quorum-test",
        top_k=3,
        max_context_chars=100,
    )

    context = memory.retrieve("Private-Owner/Secret-Repository")

    assert context.count == 2
    assert context.text.count("Security/high") == 1
    assert "Postability failures" in context.text
    call = client.search_calls[0]
    assert call["query"] == MEMORY_QUERY
    assert call["top_k"] == 3
    assert call["filters"]["app_id"] == "quorum-test"
    assert call["filters"]["user_id"].startswith("quorum-repo-")
    assert "private-owner" not in json.dumps(call).lower()
    assert "secret-repository" not in json.dumps(call).lower()


def test_retrieval_failure_is_best_effort(caplog):
    client = FakeMem0Client(fail_search=True)
    memory = Mem0LongTermMemory(enabled=True, client=client)

    assert memory.retrieve("acme/widgets").count == 0
    assert "RuntimeError" in caplog.text
    assert "sensitive text" not in caplog.text


def test_review_memory_excludes_repository_and_review_content():
    client = FakeMem0Client()
    memory = Mem0LongTermMemory(enabled=True, client=client)
    result = make_result()

    assert memory.record_review(result)

    payload = serialized_calls(client)
    for private_value in (
        result.context.full_repo,
        result.context.title,
        result.context.body,
        result.context.head_sha,
        result.comments[0].path,
        result.comments[0].anchor_text,
        result.comments[0].title,
        result.comments[0].body,
        result.health_checks[0].detail,
    ):
        assert private_value not in payload
    assert "finding_postability" in payload
    assert "security/high=1" in payload
    assert client.add_calls[0]["metadata"]["kind"] == "review_outcome"


def test_feedback_is_sanitized_and_identical_event_is_not_resent():
    client = FakeMem0Client()
    memory = Mem0LongTermMemory(enabled=True, client=client)
    result = make_result()

    assert memory.record_decisions(
        result, result.comments, rejection_reason="arbitrary private reason"
    )
    assert memory.record_decisions(
        result, result.comments, rejection_reason="arbitrary private reason"
    )
    assert len(client.add_calls) == 1

    payload = serialized_calls(client)
    assert "Approved=1" in payload
    assert "not specified" in payload
    assert "arbitrary private reason" not in payload
    assert result.comments[0].path not in payload
    assert result.comments[0].anchor_text not in payload
    assert result.comments[0].body not in payload

    assert memory.record_decisions(result, [], rejection_reason="false positive")
    assert len(client.add_calls) == 2
    assert "false positive" in serialized_calls(client)


def test_post_memory_contains_counts_not_locations_or_url():
    client = FakeMem0Client()
    memory = Mem0LongTermMemory(enabled=True, client=client)
    result = make_result()
    post = PostResult(
        posted=0,
        dropped_off_diff=["private/customer-name.py:7"],
        re_anchored=["private/customer-name.py:7 -> 9"],
        review_url="https://github.example/private/review",
        dropped_invalid_anchor=["private/customer-name.py:7"],
    )

    assert memory.record_post_result(result, result.comments, post)

    payload = serialized_calls(client)
    assert "posted=0" in payload
    assert "invalid anchors=1" in payload
    assert "off-diff=1" in payload
    assert "private/customer-name.py" not in payload
    assert "github.example" not in payload
