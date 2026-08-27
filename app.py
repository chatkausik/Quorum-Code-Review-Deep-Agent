"""Quorum — Streamlit UI for the deep-agents code reviewer.

Human-in-the-loop lives here, not in the agent. The agent's job ends at
"produce candidate findings"; the authority to post belongs to the person
reading them.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st

from quorum.agent import bucket_by_confidence, run_review
from quorum.config import (
    CONFIDENCE_THRESHOLD,
    COST_PROFILE,
    COST_PROFILES,
    MAX_COST_USD,
    MAX_LLM_CALLS,
    MODEL_PROVIDER,
    PROFILE_LABELS,
    langsmith_enabled,
)
from quorum.memory import FileBackedStore
from quorum.models import SEVERITY_ORDER, ReviewComment
from quorum.observability import project_url
from quorum.reporting import build_report
from quorum.tools.github_tools import get_file_lines, post_approved_review
from quorum.ui_theme import (
    CATEGORY_META,
    feed,
    meters,
    model_table,
    phase_strip,
    CLEAN_IMAGE,
    CSS,
    EMPTY_IMAGE,
    HERO_IMAGE,
    code_window,
    confidence_color,
    diff_block,
    list_row,
    severity_style,
)

st.set_page_config(page_title="Quorum — Code Review", page_icon="⚖️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def finding_key(comment: ReviewComment) -> str:
    return f"{comment.path}:{comment.line}:{comment.category}"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def hero() -> None:
    st.markdown(
        f"""<div class="cra-hero">
  <div class="cra-hero-text">
    <h1>Quorum</h1>
    <p>Specialist reviewers examine each changed file in isolation, their
       findings are consolidated and scored for confidence, and nothing reaches
       the pull request without your explicit approval.</p>
    <div class="cra-chips">
      <span class="cra-chip">🧠 {MODEL_PROVIDER}</span>
      <span class="cra-chip">🤖 2 specialist subagents</span>
      <span class="cra-chip">📚 5 skills</span>
      <span class="cra-chip">🛡️ sandboxed scanners</span>
      <span class="cra-chip">✅ human-gated posting</span>
    </div>
  </div>
  <img src="{HERO_IMAGE}" alt=""/>
</div>""",
        unsafe_allow_html=True,
    )


def stat_tiles(tiles: list[tuple[str, str, str]]) -> None:
    cells = "".join(
        f'<div class="cra-stat"><div class="k">{k}</div>'
        f'<div class="v">{v}</div><div class="s">{s}</div></div>'
        for k, v, s in tiles
    )
    st.markdown(f'<div class="cra-stats">{cells}</div>', unsafe_allow_html=True)


def severity_distribution(comments: list[ReviewComment]) -> None:
    if not comments:
        return
    counts = Counter(c.severity for c in comments)
    order = sorted(counts, key=lambda s: SEVERITY_ORDER.get(s, 9))
    total = sum(counts.values())
    bars = "".join(
        f'<span style="width:{counts[s] / total * 100:.2f}%;'
        f'background:{severity_style(s)["color"]}"></span>'
        for s in order
    )
    legend = "".join(
        f'<span><i style="background:{severity_style(s)["color"]}"></i>'
        f'{severity_style(s)["label"].title()} · {counts[s]}</span>'
        for s in order
    )
    st.markdown(
        f'<div class="cra-dist">{bars}</div><div class="cra-legend">{legend}</div>',
        unsafe_allow_html=True,
    )


def empty_state(image: str, title: str, detail: str) -> None:
    st.markdown(
        f'<div class="cra-empty"><img src="{image}" alt=""/>'
        f'<div class="t">{title}</div><div class="d">{detail}</div></div>',
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False, ttl=900)
def file_lines(full_repo: str, path: str, ref: str) -> tuple[str, ...]:
    """Source at the reviewed head, for showing code context around a finding."""
    return get_file_lines(full_repo, path, ref)


def _finding_body(comment: ReviewComment, context) -> None:
    """Shared detail rendering: code in context, explanation, colour-coded fix."""
    style = severity_style(comment.severity)
    lines = file_lines(context.full_repo, comment.path, context.head_sha)

    st.markdown(
        f'<div class="cra-modal-sub">{comment.path}:{comment.line}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        code_window(lines, comment.line, comment.anchor_text, color=style["color"]),
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="cra-secheading">Why this matters</div>', unsafe_allow_html=True
    )
    st.markdown(comment.body)

    if comment.suggestion:
        st.markdown(
            '<div class="cra-secheading fix">Suggested fix</div>'
            + diff_block(comment.anchor_text.strip(), comment.suggestion.strip()),
            unsafe_allow_html=True,
        )
    else:
        st.caption("No concrete fix was proposed for this finding.")


@st.dialog(" ", width="large")
def finding_dialog(comment: ReviewComment, threshold: int, context, gated: bool) -> None:
    """Modal detail view opened by clicking a finding."""
    style = severity_style(comment.severity)
    conf_color = confidence_color(comment.confidence, threshold)
    meta = CATEGORY_META.get(comment.category, {"icon": "•", "label": comment.category})

    st.markdown(
        f'<div class="cra-detail-head" style="--sev:{style["color"]};'
        f'--sev-soft:{style["soft"]};border-radius:11px;border-bottom:'
        f'1px solid rgba(140,150,170,.2)">'
        f'<span class="cra-badge" style="background:{style["color"]}">'
        f'{style["label"]}</span>'
        f'<span class="cra-path">{comment.summary(90)}</span>'
        f'<span class="cra-cat">{meta["icon"]} {meta["label"]}</span>'
        f'<span class="cra-conf">confidence'
        f'<span class="cra-bar"><span style="width:{comment.confidence}%;'
        f'background:{conf_color}"></span></span>'
        f'<b style="color:{conf_color}">{comment.confidence}</b></span></div>',
        unsafe_allow_html=True,
    )
    _finding_body(comment, context)

    st.divider()
    key = f"approve::{finding_key(comment)}"
    st.checkbox(
        "Approve this comment" if gated else "Include when posting",
        key=f"dlg::{key}",
        value=st.session_state.get(key, not gated),
    )
    if st.button("Save and close", type="primary", use_container_width=True):
        st.session_state[key] = st.session_state[f"dlg::{key}"]
        st.rerun()


def render_bucket(
    comments: list[ReviewComment], threshold: int, context, *, gated: bool, slot: str
) -> None:
    """Master/detail: scannable list on the left, live detail on the right.

    Each row also opens a modal, for reading one finding without the list
    competing for attention.
    """
    sel_key = f"sel::{slot}"
    if st.session_state.get(sel_key, 0) >= len(comments):
        st.session_state[sel_key] = 0
    selected = st.session_state.get(sel_key, 0)

    left, right = st.columns([2, 3], gap="medium")

    with left:
        st.caption(f"{len(comments)} finding(s) — click to inspect")
        for index, comment in enumerate(comments):
            style = severity_style(comment.severity)
            meta = CATEGORY_META.get(comment.category, {"icon": "•"})
            st.markdown(
                list_row(
                    style["color"],
                    style["label"],
                    comment.summary(),
                    comment.path,
                    comment.line,
                    comment.confidence,
                    meta["icon"],
                    confidence_color(comment.confidence, threshold),
                    index == selected,
                ),
                unsafe_allow_html=True,
            )
            approved = st.session_state.get(
                f"approve::{finding_key(comment)}", not gated
            )
            cols = st.columns([3, 2])
            with cols[0]:
                if st.button(
                    "Open details",
                    key=f"pick::{slot}::{index}",
                    use_container_width=True,
                    type="primary" if index == selected else "secondary",
                ):
                    st.session_state[sel_key] = index
                    finding_dialog(comment, threshold, context, gated)
            with cols[1]:
                st.checkbox(
                    "post",
                    key=f"approve::{finding_key(comment)}",
                    value=approved,
                    help="Include this comment when posting to GitHub.",
                )

    with right:
        if comments:
            comment = comments[selected]
            style = severity_style(comment.severity)
            conf_color = confidence_color(comment.confidence, threshold)
            meta = CATEGORY_META.get(
                comment.category, {"icon": "•", "label": comment.category}
            )
            st.markdown(
                f'<div class="cra-detail-head" style="--sev:{style["color"]};'
                f'--sev-soft:{style["soft"]}">'
                f'<span class="cra-badge" style="background:{style["color"]}">'
                f'{style["label"]}</span>'
                f'<span class="cra-path">{comment.summary(70)}</span>'
                f'<span class="cra-cat">{meta["icon"]} {meta["label"]}</span>'
                f'<span class="cra-conf">confidence'
                f'<span class="cra-bar"><span style="width:{comment.confidence}%;'
                f'background:{conf_color}"></span></span>'
                f'<b style="color:{conf_color}">{comment.confidence}</b>'
                f"</span></div>",
                unsafe_allow_html=True,
            )
            _finding_body(comment, context)


def approved_comments(comments: list[ReviewComment]) -> list[ReviewComment]:
    return [
        c for c in comments if st.session_state.get(f"approve::{finding_key(c)}", False)
    ]


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Review a pull request")
    owner = st.text_input("Owner", placeholder="octocat")
    repo = st.text_input("Repository", placeholder="hello-world")
    pr_number = st.number_input("PR number", min_value=1, step=1, value=1)
    threshold = st.number_input(
        "Confidence threshold",
        min_value=0,
        max_value=100,
        value=CONFIDENCE_THRESHOLD,
        help="At or above this, findings are auto-approved. Below it, you decide.",
    )

    profile_names = list(PROFILE_LABELS)
    profile = st.selectbox(
        "Cost profile",
        profile_names,
        index=profile_names.index(COST_PROFILE),
        format_func=lambda name: name.title(),
        help="Reasoning tokens bill as output and were over half of a measured "
        "run's cost, so effort and model tier are the strongest levers.",
    )
    st.caption(PROFILE_LABELS[profile])

    spec = COST_PROFILES[MODEL_PROVIDER][profile]
    st.caption(
        f"`{spec['orchestrator_model']}` ({spec['orchestrator_effort']}) "
        f"orchestrator · `{spec['subagent_model']}` ({spec['subagent_effort']}) "
        f"subagents"
    )

    run_clicked = st.button("▶  Run Review", type="primary", use_container_width=True)
    st.caption(f"Ceiling ${MAX_COST_USD:.2f} · {MAX_LLM_CALLS} calls · docs skipped")

    if "store" not in st.session_state:
        st.session_state.store = FileBackedStore()

    st.divider()
    if langsmith_enabled():
        link = project_url()
        st.markdown(
            f"🔬 **LangSmith** · tracing on"
            + (f"  \n[Open dashboard →]({link})" if link else "")
        )
    else:
        st.caption("🔬 LangSmith tracing off — set `LANGSMITH_API_KEY` in `.env`.")

    if owner and repo:
        stats = st.session_state.store.get_stats(owner, repo)
        st.divider()
        st.caption(
            f"**{owner}/{repo}** · {stats['total_runs']} run(s) · "
            f"{stats['total_comments_posted']} posted"
        )


hero()

# --------------------------------------------------------------------------
# Run — with live streaming progress
# --------------------------------------------------------------------------

if run_clicked:
    if not owner or not repo:
        st.error("Enter an owner and a repository.")
    else:
        for key in [k for k in st.session_state if k.startswith("approve::")]:
            del st.session_state[key]
        st.session_state.pop("post_result", None)
        st.session_state.pop("result", None)

        events: list[dict] = []
        seen_phases: set[str] = set()
        latest_stats: dict = {}
        target = f"{owner}/{repo}#{int(pr_number)}"

        with st.status(f"Reviewing {target}", expanded=True) as status:
            phase_slot = st.empty()
            meter_slot = st.empty()
            feed_slot = st.empty()
            model_slot = st.empty()

            def paint(current: str | None) -> None:
                phase_slot.markdown(
                    phase_strip(seen_phases, current), unsafe_allow_html=True
                )
                if latest_stats:
                    meter_slot.markdown(
                        meters(latest_stats, MAX_COST_USD, MAX_LLM_CALLS),
                        unsafe_allow_html=True,
                    )
                    model_slot.markdown(
                        model_table(latest_stats.get("by_model", {})),
                        unsafe_allow_html=True,
                    )
                feed_slot.markdown(feed(events), unsafe_allow_html=True)

            def on_event(event: dict) -> None:
                if event.get("type") == "stats":
                    latest_stats.clear()
                    latest_stats.update(event)
                    paint(events[-1].get("phase") if events else None)
                    return
                events.append(event)
                phase = event.get("phase")
                if phase:
                    seen_phases.add(phase)
                paint(phase)

            try:
                result = run_review(
                    owner,
                    repo,
                    int(pr_number),
                    store=st.session_state.store,
                    profile=profile,
                    on_event=on_event,
                )
                st.session_state.result = result
                status.update(
                    label=f"Reviewed {target} — {len(result.comments)} finding(s) · "
                    f"{result.llm_calls} calls · ${result.total_cost_usd:.4f}",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
                st.session_state.result = None
                status.update(label=f"Review failed — {target}", state="error")
                st.error(f"{type(exc).__name__}: {exc}")

result = st.session_state.get("result")

if not result:
    empty_state(
        EMPTY_IMAGE,
        "No review yet",
        "Enter an owner, repository, and PR number in the sidebar, then click "
        "Run Review.",
    )
else:
    if result.error and not result.comments:
        st.error(result.error)
    elif result.budget_exceeded:
        st.warning(f"{result.error} Findings below may be incomplete.")

    stat_tiles(
        [
            ("Findings", str(len(result.comments)), result.context.full_repo),
            ("LLM calls", str(result.llm_calls), f"ceiling {MAX_LLM_CALLS}"),
            ("Cost", f"${result.total_cost_usd:.4f}", f"ceiling ${MAX_COST_USD:.2f}"),
            ("Head", result.context.head_sha[:7], f"PR #{result.context.pr_number}"),
        ]
    )
    severity_distribution(result.comments)

    if result.trace_url or result.project_url:
        links = []
        if result.trace_url:
            links.append(f'<a href="{result.trace_url}" target="_blank">This run\'s trace ↗</a>')
        if result.project_url:
            links.append(f'<a href="{result.project_url}" target="_blank">Project dashboard ↗</a>')
        st.markdown(
            '<div class="cra-obs">🔬 <b>LangSmith</b>' + " · ".join(links) + "</div>",
            unsafe_allow_html=True,
        )

    if not result.comments:
        summary = result.drop_summary()
        if result.subagent_reported:
            st.info(
                f"Subagents reported {result.subagent_reported} candidate "
                "finding(s), but none reached the final list"
                + (f" — filtered: {summary}." if summary else ".")
                + "  \nLow-severity findings are never reported, and duplicates "
                "on the same line are merged."
            )
        elif result.files_reviewed:
            st.success(
                f"Reviewed {result.files_reviewed} file(s) and found nothing "
                "above the reporting bar. Low-severity issues are never "
                "reported."
            )
        # A run with no files and no findings already surfaced an error above.
    elif result.dropped:
        st.caption(f"Filtered during consolidation: {result.drop_summary()}.")

    auto, manual = bucket_by_confidence(result.comments, int(threshold))

    tab_auto, tab_manual = st.tabs(
        [f"✅  Auto-approved · {len(auto)}", f"⚠️  Needs review · {len(manual)}"]
    )
    with tab_auto:
        st.caption(f"Confidence ≥ {int(threshold)} — pre-selected for posting.")
        if not auto:
            empty_state(
                CLEAN_IMAGE, "Nothing cleared the threshold",
                "Lower the confidence threshold to widen the auto-approved set.",
            )
        else:
            render_bucket(
                auto, int(threshold), result.context, gated=False, slot="auto"
            )

    with tab_manual:
        st.caption(f"Confidence < {int(threshold)} — approve individually.")
        if not manual:
            empty_state(
                CLEAN_IMAGE, "Nothing needs a manual decision",
                "Every finding cleared your confidence threshold.",
            )
        else:
            render_bucket(
                manual, int(threshold), result.context, gated=True, slot="manual"
            )

    st.divider()
    selected = approved_comments(result.comments)
    left, right = st.columns([2, 1])

    with left:
        st.markdown(f"**{len(selected)}** comment(s) selected for posting.")
        if st.button(
            "🚀  Post Approved Comments",
            type="primary",
            disabled=not selected,
            use_container_width=True,
        ):
            with st.spinner("Validating line numbers and posting …"):
                try:
                    st.session_state.post_result = post_approved_review(
                        result.context, selected
                    )
                    st.session_state.store.record_posted(
                        result.context.owner,
                        result.context.repo,
                        st.session_state.post_result.posted,
                    )
                except PermissionError as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Posting failed — {type(exc).__name__}: {exc}")

    with right:
        st.download_button(
            "⬇  Download report",
            data=build_report(result, int(threshold)),
            file_name=(
                f"review-{result.context.owner}-{result.context.repo}"
                f"-pr{result.context.pr_number}.md"
            ),
            mime="text/markdown",
            use_container_width=True,
        )

    posted = st.session_state.get("post_result")
    if posted:
        if posted.posted:
            st.success(f"Posted {posted.posted} comment(s) in a single review.")
            if posted.review_url:
                st.markdown(f"[View the review on GitHub ↗]({posted.review_url})")
        else:
            st.warning("Nothing was posted — every comment failed diff validation.")
        if posted.re_anchored:
            with st.expander(f"Re-anchored {len(posted.re_anchored)} comment(s)"):
                for entry in posted.re_anchored:
                    st.text(entry)
        if posted.dropped_off_diff:
            with st.expander(
                f"Dropped {len(posted.dropped_off_diff)} comment(s) off the diff"
            ):
                for entry in posted.dropped_off_diff:
                    st.text(entry)

    if result.trace:
        with st.expander("Run trace"):
            for entry in result.trace:
                st.text(entry)
