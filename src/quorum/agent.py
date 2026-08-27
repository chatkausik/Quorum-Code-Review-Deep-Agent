"""Builds the deep-agents orchestrator and runs a review.

The orchestrator is a loop, not a pipeline: it decides which tools to call,
which files to delegate, and when to stop. What is *not* left to the model is
the budget ceiling, the PR metadata, and the posting decision.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from quorum.config import (
    CONFIDENCE_THRESHOLD,
    MAX_OUTPUT_TOKENS,
    MODEL_PROVIDER,
    ORCHESTRATOR_EFFORT,
    SUBAGENT_EFFORT,
    enable_langsmith,
    provider_api_key,
    resolve_profile,
    FINAL_MARKER,
    FINDINGS_DIR,
    MAX_COST_USD,
    MAX_LLM_CALLS,
    ORCHESTRATOR_MODEL,
    SKILLS_DIR,
    SKILLS_MOUNT,
    SUBAGENT_MODEL,
)
from quorum.memory import FileBackedStore
from quorum.middleware import (
    BudgetExceeded,
    CostTrackingMiddleware,
    PRMetadataMiddleware,
)
from quorum.models import ReviewComment, ReviewContext, ReviewResult
from quorum.observability import capture_run, project_url
from quorum.prompts import (
    GENERIC_REVIEWER_PROMPT,
    ORCHESTRATOR_PROMPT,
    PYTHON_REVIEWER_PROMPT,
)
from quorum.tools.github_tools import (
    fetch_pr,
    get_file_content,
    list_files,
    load_pr_context,
)
from quorum.tools.memory_tools import read_review_memory, write_review_memory
from quorum.tools.sandbox import make_run_command
from quorum.tools.search_tools import regex_search

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 150


def _model(name: str, effort: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> BaseChatModel:
    """Build a chat model at a given effort level for the configured provider.

    Reasoning tokens bill as output, and output was over half of a measured
    run's cost, so effort is the strongest single cost lever available. The
    two providers spell it differently: OpenAI takes `reasoning_effort`,
    Anthropic takes `output_config={"effort": ...}` alongside adaptive
    thinking.
    """
    if MODEL_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        # The Responses API is required here: on /v1/chat/completions OpenAI
        # rejects reasoning_effort whenever function tools are bound, and this
        # agent binds tools on every call.
        return ChatOpenAI(
            model=name,
            api_key=provider_api_key(),
            max_tokens=max_tokens,
            reasoning_effort=effort,
            use_responses_api=True,
        )

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=name,
        api_key=provider_api_key(),
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
    )


def build_backend() -> CompositeBackend:
    """VFS for run artifacts, real disk for skills.

    /pr, /findings and /patches live in agent state, so the host filesystem is
    never touched by a review. /skills is mounted read-only from the repo so a
    security team can edit a pattern without a redeploy.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={f"{SKILLS_MOUNT}/": FilesystemBackend(root_dir=str(SKILLS_DIR))},
    )


PYTHON_SKILLS = (
    "python-secret-patterns",
    "python-sql-injection",
    "python-async-pitfalls",
)
GENERIC_SKILLS = ("generic-secret-patterns", "generic-injection")


def skill_path(name: str) -> str:
    return f"{SKILLS_MOUNT}/{name}"


def all_skill_paths() -> list[str]:
    """Every skill, for the orchestrator's own inline review of trivial files."""
    return [skill_path(n) for n in (*PYTHON_SKILLS, *GENERIC_SKILLS)]


def build_agent(
    context: ReviewContext,
    store: FileBackedStore,
    profile: str | None = None,
):
    """Assemble the orchestrator, its subagents, and the shared cost ceiling."""
    spec = resolve_profile(profile)
    orchestrator_model = str(spec["orchestrator_model"])
    subagent_model = str(spec["subagent_model"])
    orchestrator_effort = str(spec["orchestrator_effort"])
    subagent_effort = str(spec["subagent_effort"])
    max_tokens = int(spec["max_tokens"])

    backend = build_backend()
    run_command = make_run_command(backend)

    # One shared instance: subagents are separately compiled graphs, so a
    # ceiling attached only to the orchestrator would not bound the run.
    cost = CostTrackingMiddleware(max_cost_usd=MAX_COST_USD, max_calls=MAX_LLM_CALLS)
    pr_metadata = PRMetadataMiddleware(context)

    python_reviewer = {
        "name": "python_reviewer",
        "description": (
            "Reviews Python (.py) files for correctness, security, and test "
            "coverage. Has bandit in a sandbox plus Python-specific skills for "
            "secrets, SQL injection, and async pitfalls. Use for every .py file."
        ),
        "system_prompt": PYTHON_REVIEWER_PROMPT,
        "tools": [regex_search, run_command],
        "model": _model(subagent_model, subagent_effort, max_tokens),
        "middleware": [cost],
        "skills": [skill_path(name) for name in PYTHON_SKILLS],
    }

    generic_reviewer = {
        "name": "generic_reviewer",
        "description": (
            "Reviews non-Python files — YAML, Dockerfile, shell, .env, CI and "
            "config — for secrets and injection vectors. No bandit, which is "
            "Python-only. Use for every file that is not .py."
        ),
        "system_prompt": GENERIC_REVIEWER_PROMPT,
        "tools": [regex_search],
        "model": _model(subagent_model, subagent_effort, max_tokens),
        "middleware": [cost],
        "skills": [skill_path(name) for name in GENERIC_SKILLS],
    }

    agent = create_deep_agent(
        model=_model(orchestrator_model, orchestrator_effort, max_tokens),
        tools=[
            fetch_pr,
            list_files,
            get_file_content,
            regex_search,
            run_command,
            read_review_memory,
            write_review_memory,
        ],
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=[python_reviewer, generic_reviewer],
        middleware=[pr_metadata, cost],
        skills=all_skill_paths(),
        backend=backend,
        store=store,
        # Checkpointing is what makes partial results recoverable: when the
        # budget ceiling fires mid-run, invoke() raises and returns no state,
        # so the only way to reach findings already written to the VFS is to
        # read them back out of the last checkpoint.
        checkpointer=InMemorySaver(),
    )
    return agent, cost


# --------------------------------------------------------------------------
# Output parsing
# --------------------------------------------------------------------------


# Models reliably produce the right *findings* but drift on field names. A
# real critical finding must not be thrown away because the key said "comment"
# instead of "body".
_BODY_ALIASES = ("body", "comment", "message", "description", "issue", "detail")
_SUGGESTION_ALIASES = ("suggestion", "fix", "suggested_fix", "recommendation")
_ANCHOR_ALIASES = ("anchor_text", "anchor", "code", "line_text")
_TITLE_ALIASES = ("title", "summary", "headline", "name", "issue_title")

_SECURITY_HINTS = (
    "secret", "credential", "password", "token", "api key", "injection",
    "sql", "xss", "auth", "privilege", "sanitiz", "vulnerab", "exploit",
    "command execution", "traversal", "csrf", "ssrf", "encryption",
)
_TEST_HINTS = ("test", "coverage", "assert", "fixture", "mock")


def _first(raw: dict, names: tuple[str, ...]) -> Any:
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _infer_category(text: str) -> str:
    lowered = text.lower()
    if any(hint in lowered for hint in _SECURITY_HINTS):
        return "security"
    if any(hint in lowered for hint in _TEST_HINTS):
        return "tests"
    return "correctness"


def normalize_finding(raw: dict) -> dict:
    """Map common field-name drift onto the ReviewComment schema."""
    normalized = dict(raw)

    body = _first(raw, _BODY_ALIASES)
    if body is not None:
        normalized["body"] = str(body)

    anchor = _first(raw, _ANCHOR_ALIASES)
    if anchor is not None:
        normalized["anchor_text"] = str(anchor)

    suggestion = _first(raw, _SUGGESTION_ALIASES)
    normalized["suggestion"] = str(suggestion) if suggestion is not None else None

    title = _first(raw, _TITLE_ALIASES)
    normalized["title"] = str(title) if title is not None else None

    category = str(normalized.get("category", "")).lower()
    if category not in ("correctness", "security", "tests"):
        normalized["category"] = _infer_category(
            f"{normalized.get('body', '')} {normalized.get('anchor_text', '')}"
        )

    severity = str(normalized.get("severity", "")).lower()
    if severity not in ("low", "medium", "high", "critical"):
        normalized["severity"] = "medium"
    else:
        normalized["severity"] = severity

    confidence = normalized.get("confidence")
    if not isinstance(confidence, (int, float)):
        # An unscored finding is exactly the case a human should adjudicate,
        # so park it just below the default threshold rather than dropping it.
        normalized["confidence"] = 60
    else:
        normalized["confidence"] = max(0, min(100, int(confidence)))

    return normalized


def _coerce(
    raw: Any,
    seen: set[tuple[str, int]],
    dropped: Counter | None = None,
) -> ReviewComment | None:
    """Validate one raw finding, dropping low severity and duplicates.

    Every rejection is counted. A run where subagents produced findings but the
    UI shows zero is otherwise indistinguishable from a run that found nothing,
    which makes the tool look broken when it is working exactly as specified.
    """
    counter = dropped if dropped is not None else Counter()

    if not isinstance(raw, dict):
        counter["malformed"] += 1
        return None
    try:
        comment = ReviewComment.model_validate(normalize_finding(raw))
    except ValidationError as exc:
        logger.warning("Discarding malformed finding %r: %s", raw, exc)
        counter["malformed"] += 1
        return None
    if comment.severity == "low":
        counter["low severity"] += 1
        return None
    # Strip a VFS path the model may have echoed instead of the repo path.
    if comment.path.startswith("/pr/"):
        comment = comment.model_copy(
            update={"path": comment.path[len("/pr/") :]}
        )
    key = (comment.path, comment.line)
    if key in seen:
        counter["duplicate"] += 1
        return None
    seen.add(key)
    return comment


def parse_marker_output(
    text: str,
    dropped: Counter | None = None,
    seen: set[tuple[str, int]] | None = None,
) -> list[ReviewComment]:
    """Extract findings from the FINAL_FINDINGS_JSON marker in the final message."""
    if FINAL_MARKER not in text:
        return []
    tail = text.rsplit(FINAL_MARKER, 1)[1].strip()
    # Tolerate a fenced block around the array.
    fence = re.search(r"```(?:json)?\s*(.*?)```", tail, re.DOTALL)
    if fence:
        tail = fence.group(1).strip()
    start, end = tail.find("["), tail.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        raw_items = json.loads(tail[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("FINAL_FINDINGS_JSON was not valid JSON: %s", exc)
        return []

    seen = set() if seen is None else seen
    return [c for item in raw_items if (c := _coerce(item, seen, dropped))]


def parse_findings_files(
    files: dict[str, Any],
    dropped: Counter | None = None,
    seen: set[tuple[str, int]] | None = None,
) -> list[ReviewComment]:
    """Fallback: consolidate /findings/*.json straight out of the final state.

    Used when the marker is missing or malformed, and when a budget kill leaves
    subagent output on the floor. Deterministic, so a failed final message
    never costs a whole run's work.
    """
    seen = set() if seen is None else seen
    collected: list[ReviewComment] = []
    for path, data in (files or {}).items():
        if not path.startswith(FINDINGS_DIR) or not path.endswith(".json"):
            continue
        content = data.get("content") if isinstance(data, dict) else data
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        items = payload.get("comments", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        for item in items:
            comment = _coerce(item, seen, dropped)
            if comment:
                collected.append(comment)
    return collected


def _final_text(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p)
            if joined.strip():
                return joined
    return ""


def _trace(result: dict[str, Any]) -> list[str]:
    """Audit trail: what the agent mounted and what each subagent produced.

    Subagents run context-quarantined, so their `[SUBAGENT/...]` announcements
    never reach the parent's message list. The virtual filesystem is the
    reliable record of what actually happened.
    """
    files = result.get("files") or {}
    mounted = sorted(p for p in files if p.startswith("/pr/"))
    findings = sorted(p for p in files if p.startswith(FINDINGS_DIR))

    lines = [f"mounted {len(mounted)} file(s) into the virtual filesystem"]
    lines += [f"  /pr: {path}" for path in mounted]
    lines.append(f"{len(findings)} subagent findings file(s) written")
    for path in findings:
        data = files.get(path)
        content = data.get("content") if isinstance(data, dict) else data
        count = "?"
        if isinstance(content, str):
            try:
                payload = json.loads(content)
                items = payload.get("comments", []) if isinstance(payload, dict) else payload
                count = str(len(items)) if isinstance(items, list) else "?"
            except json.JSONDecodeError:
                count = "unparseable"
        lines.append(f"  {path}: {count} finding(s)")

    for message in result.get("messages") or []:
        content = getattr(message, "content", "")
        if isinstance(content, str) and "[SUBAGENT/" in content:
            lines.extend(
                line.strip() for line in content.splitlines() if "[SUBAGENT/" in line
            )
    return lines


# --------------------------------------------------------------------------
# Entry point used by the UI
# --------------------------------------------------------------------------


def _describe_update(node: str, update: Any) -> list[dict[str, str]]:
    """Turn one graph update into structured progress events.

    Each event carries a phase so the UI can show where the run has reached,
    not just a scrolling log.
    """
    events: list[dict[str, str]] = []
    if not isinstance(update, dict):
        return events

    def add(phase: str, icon: str, text: str, detail: str = "") -> None:
        events.append({"phase": phase, "icon": icon, "text": text, "detail": detail})

    for message in update.get("messages") or []:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            if name == "task":
                target = args.get("subagent_type") or args.get("name") or "subagent"
                detail = str(args.get("description") or args.get("task") or "")
                add("review", "🤖", f"Delegating to {target}", detail[:110])
            elif name == "write_file":
                path = str(args.get("file_path", ""))
                if path.startswith(FINDINGS_DIR):
                    add("consolidate", "📋", "Subagent wrote findings", path)
                elif path.startswith("/pr/"):
                    add("mount", "📂", "Mounted file", path)
                elif path.startswith("/patches/"):
                    add("mount", "🧩", "Stored patch", path)
            elif name == "get_file_content":
                add("fetch", "⬇️", "Fetching file", str(args.get("path", "")))
            elif name == "list_files":
                add("fetch", "📄", "Listing changed files", "")
            elif name == "fetch_pr":
                add("fetch", "🔗", "Fetching PR metadata", "")
            elif name == "run_command":
                add("review", "🛡️", "Running scanner", str(args.get("cmd", ""))[:80])
            elif name == "regex_search":
                add("review", "🔎", "Pattern scan", str(args.get("pattern", ""))[:60])
            elif name == "read_review_memory":
                add("memory", "🧠", "Reading long-term memory", "")
            elif name == "write_review_memory":
                add("memory", "💾", "Updating long-term memory", "")
            elif name == "write_todos":
                add("plan", "🗂️", "Planning the run", "")
            elif name in ("ls", "read_file"):
                add("consolidate", "📥", "Reading findings back", str(args.get("file_path", "")))
    return events


def _count_subagent_findings(state: dict[str, Any]) -> int:
    """How many raw findings the subagents wrote, before consolidation."""
    total = 0
    for path, data in (state.get("files") or {}).items():
        if not path.startswith(FINDINGS_DIR) or not path.endswith(".json"):
            continue
        content = data.get("content") if isinstance(data, dict) else data
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        items = payload.get("comments", []) if isinstance(payload, dict) else payload
        if isinstance(items, list):
            total += len(items)
    return total


def _recover_state(agent, run_config: dict[str, Any]) -> dict[str, Any]:
    """Read the last checkpoint after a failed run.

    A run killed by the budget ceiling has usually already written real
    findings to /findings/. Without this, that work is thrown away.
    """
    try:
        snapshot = agent.get_state(run_config)
    except Exception:  # noqa: BLE001 - recovery must never mask the real error
        logger.warning("Could not recover state from the checkpoint")
        return {}
    return dict(snapshot.values or {})


def run_review(
    owner: str,
    repo: str,
    pr_number: int,
    store: FileBackedStore | None = None,
    profile: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> ReviewResult:
    """Review a pull request and return candidate findings.

    Never posts. The agent's job ends at "produce candidate findings"; posting
    authority belongs to the human at the UI.
    """
    store = store or FileBackedStore()
    context = load_pr_context(owner, repo, pr_number)
    agent, cost = build_agent(context, store, profile)

    task = (
        f"Review pull request #{pr_number} in {owner}/{repo}. "
        f"The head SHA is {context.head_sha} — mount every changed file at that "
        f"ref. Follow your run plan exactly and finish with the "
        f"{FINAL_MARKER} marker."
    )

    budget_exceeded = False
    error: str | None = None
    state: dict[str, Any] = {}
    run_config = {
        "recursion_limit": RECURSION_LIMIT,
        "configurable": {"thread_id": f"{owner}/{repo}#{pr_number}"},
    }

    def emit(event: dict[str, Any]) -> None:
        if on_event:
            try:
                on_event(event)
            except Exception:  # noqa: BLE001 - UI callbacks must not fail a run
                logger.debug("progress callback raised", exc_info=True)

    trace: dict[str, str] = {}
    try:
        with capture_run() as trace:
            emit({"type": "log", "phase": "plan", "icon": "🚀",
                  "text": f"Starting review of {context.full_repo}#{pr_number}",
                  "detail": context.title[:110]})
            # "values" carries the accumulated state on every step, so the
            # final findings are captured as they stream. Relying only on a
            # post-hoc checkpoint read made a failed read look identical to a
            # run that genuinely found nothing.
            for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": task}]},
                config=run_config,
                stream_mode=["updates", "values"],
            ):
                if mode == "values":
                    if isinstance(chunk, dict) and chunk:
                        state = chunk
                    continue
                for node, update in (chunk or {}).items():
                    for event in _describe_update(node, update):
                        emit({"type": "log", **event})
                if cost.calls:
                    emit({"type": "stats", **cost.snapshot()})

            if not state.get("files"):
                # Streaming gave us nothing usable; fall back to the checkpoint.
                recovered = _recover_state(agent, run_config)
                if recovered:
                    state = recovered
    except BudgetExceeded as exc:
        budget_exceeded = True
        error = str(exc)
        logger.warning("Run halted by budget ceiling: %s", exc)
        state = _recover_state(agent, run_config)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Review failed")
        state = _recover_state(agent, run_config)

    # The orchestrator's consolidated list is authoritative for wording, but it
    # sometimes drops findings its own subagents wrote. Merging the raw
    # /findings files back in — deduped by (path, line) — means a consolidation
    # slip can no longer silently lose a real issue. Low severity and
    # duplicates are still filtered by _coerce.
    dropped: Counter = Counter()
    seen: set[tuple[str, int]] = set()
    comments = parse_marker_output(_final_text(state), dropped, seen) if state else []
    recovered = (
        parse_findings_files(state.get("files", {}), dropped, seen) if state else []
    )
    if recovered:
        logger.info(
            "Recovered %d finding(s) the consolidation step omitted", len(recovered)
        )
    comments = comments + recovered

    comments.sort(key=lambda c: c.sort_key())

    if not state and error is None:
        error = (
            "The run finished but its final state could not be read, so any "
            "findings it produced were lost. This is a bug, not a clean bill "
            "of health — re-run before trusting a zero-finding result."
        )
        logger.error("Empty final state after a successful stream")

    store.record_run(owner, repo)

    return ReviewResult(
        comments=comments,
        context=context,
        total_cost_usd=cost.total_cost_usd,
        llm_calls=cost.calls,
        budget_exceeded=budget_exceeded,
        error=error,
        trace=_trace(state) + cost.log if state else cost.log,
        dropped=dict(dropped),
        subagent_reported=_count_subagent_findings(state) if state else 0,
        trace_url=trace.get("url"),
        project_url=project_url(),
        recovered_from_files=len(recovered),
        files_reviewed=sum(
            1 for path in (state.get("files") or {}) if path.startswith("/pr/")
        ),
    )


def bucket_by_confidence(
    comments: list[ReviewComment], threshold: int = CONFIDENCE_THRESHOLD
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    """Split findings into (auto-approved, needs-review) at the threshold."""
    auto = [c for c in comments if c.confidence >= threshold]
    manual = [c for c in comments if c.confidence < threshold]
    return auto, manual
