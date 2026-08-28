from __future__ import annotations

from langchain_core.messages import AIMessage

from quorum.agent import _describe_update


def describe(*tool_calls):
    message = AIMessage(content="", tool_calls=list(tool_calls))
    return _describe_update("model", {"messages": [message]})


def test_bound_github_tools_are_described_as_frozen_reads():
    events = describe(
        {"name": "fetch_pr", "args": {}, "id": "1", "type": "tool_call"},
        {"name": "list_files", "args": {}, "id": "2", "type": "tool_call"},
        {
            "name": "get_file_content",
            "args": {"path": "src/app.py"},
            "id": "3",
            "type": "tool_call",
        },
    )

    assert [event["text"] for event in events] == [
        "Reading frozen PR metadata",
        "Reading frozen manifest",
        "Reading frozen file",
    ]
    assert {event["phase"] for event in events} == {"fetch"}


def test_finding_artifact_is_a_review_event_not_a_mount_event():
    events = describe(
        {
            "name": "write_file",
            "args": {"file_path": "/findings/src/app.py.json"},
            "id": "1",
            "type": "tool_call",
        }
    )

    assert events == [
        {
            "phase": "review",
            "icon": "📋",
            "text": "Reviewer wrote its result",
            "detail": "/findings/src/app.py.json",
        }
    ]


def test_attempted_source_write_does_not_claim_that_source_was_mounted():
    events = describe(
        {
            "name": "write_file",
            "args": {"file_path": "/pr/src/app.py"},
            "id": "1",
            "type": "tool_call",
        }
    )

    assert events == []
