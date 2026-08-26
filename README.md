# Quorum

**A deep-agents code reviewer that never posts without your approval.**

Quorum reviews a GitHub pull request for correctness, security, and
test-coverage issues. It decides on the fly which tools to call, which files to
delegate to a specialist subagent, and which skills to load — rather than
following a hardcoded fetch → parse → review sequence.

The name is the design: findings need enough confidence to pass the gate, and a
human casts the deciding vote before anything is posted.

Findings surface in a Streamlit UI bucketed by confidence. You approve what
should go out, and a single GitHub review is posted with every comment prefixed
`[AI Review]`.

## How it works

```
Streamlit UI  ──run_review()──▶  Deep Agent Orchestrator (claude-opus-5)
                                          │
                                 task(name=...) delegates per file
                                          │
                          ┌───────────────┴───────────────┐
                    python_reviewer                 generic_reviewer
                    (claude-sonnet-5)               (claude-sonnet-5)
                    regex_search, bandit            regex_search
                    3 Python skills                 2 generic skills
                          └───────────────┬───────────────┘
                              /findings/<file>.json  (virtual filesystem)
                                          │
                                 FINAL_FINDINGS_JSON
                                          │
                          Confidence-gated HITL in the UI
                                          │
                     post_approved_review()  ── deterministic, no LLM
                       re-anchor → unidiff validate → one review
```

The orchestrator is a loop, not a pipeline. What is *not* left to the model:
the budget ceiling, the PR metadata, line-number validation, and the decision
to post.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env      # then fill in both keys
```

`.env` needs:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Orchestrator and subagents (default provider) |
| `ANTHROPIC_API_KEY` | Only if `MODEL_PROVIDER=anthropic` |
| `GITHUB_TOKEN` | Read the PR, post the review. Classic PAT with `repo`, or fine-grained with *Pull requests: read and write* |
| `LANGSMITH_API_KEY` | Optional. Enables tracing and the in-app trace links |

### Cost profiles

Pick in the sidebar, or set `REVIEW_COST_PROFILE`. Measured on a real 4-file PR
with ~20 findings:

| Profile | Orchestrator | Subagents | Cost | Findings |
| --- | --- | --- | --- | --- |
| `economy` | `gpt-5.4-mini` (low) | `gpt-5.4-nano` (low) | **$0.07** | 20 |
| `balanced` | `gpt-5.4` (medium) | `gpt-5.4-mini` (low) | **$0.23** | 20 |
| `thorough` | `gpt-5.5` (high) | `gpt-5.4` (medium) | ~$1+ | — |

Economy matched Balanced finding-for-finding on that benchmark at a third of
the price, so start there. Set `MODEL_PROVIDER=anthropic` to switch providers;
the same three profiles map onto Claude models.

## Run

```bash
.venv/bin/streamlit run app.py
```

Enter owner, repository, and PR number, then click **Run Review**. Findings
appear in two buckets:

- **confidence ≥ threshold** — auto-approved, pre-checked for posting
- **confidence < threshold** — held back for an explicit decision

Adjust the threshold in the sidebar (default 75). Click **Post Approved
Comments** to publish, or **Download findings report** for a prioritized
markdown report.

## Tests

```bash
.venv/bin/python -m pytest
```

Covers line-number re-anchoring, unidiff validation, the sandbox allowlist, the
cost kill switch, finding normalization, and memory persistence. No network or
credentials required.

## Design notes

**Skills are markdown, not prompt strings.** The five files under `skills/`
hold the pattern knowledge — regexes, severity guidance, worked good/bad
examples. A security team can update a pattern by editing markdown, with no
redeploy, and anyone can read what the agent "knows" without reading code.

**Subagents are specialists.** `python_reviewer` gets bandit and the three
Python skills; `generic_reviewer` gets neither, because bandit is Python-only.
Smaller tool sets produce sharper findings.

**The cost ceiling is enforced, not requested.** `CostTrackingMiddleware`
raises past `$1.00` or 25 LLM calls. One shared instance is attached to the
orchestrator *and* both subagents — subagents are separately compiled graphs,
so a ceiling on the orchestrator alone would not bound the run. A killed run
still returns whatever findings reached `/findings/`, recovered from the last
checkpoint.

Accounting is cache-aware: both providers cache prompts automatically, and
`input_tokens` already includes the cached portion, so charging it all at the
base rate overstates a run ~10x and trips the ceiling early. Cached input bills
at 10% of the input rate; Anthropic also charges a 1.25x premium to *write* the
cache, while OpenAI does not bill writes at all.

**Where the money goes.** On a measured run: output tokens 53%, cache writes
38%, cache reads 8%. Reasoning tokens bill as output, so `effort` is the
strongest single lever — hence the cost profiles. Docs and markdown are skipped
by default (`REVIEW_DOCS=true` to include them): prose is not code, and
reviewing it produces soft findings at real cost.

**LLMs hallucinate line numbers, so posting does not trust them.** Every
comment carries a verbatim `anchor_text`. Before posting, each comment is
re-anchored to the line where its anchor text actually appears, then dropped
entirely if it does not land on the `+` side of the diff.

**HITL lives in the UI, not the framework.** The agent's job ends at "produce
candidate findings". Keeping the gate in the UI makes it provider-agnostic and
version-independent, and turns "add Slack approval" into a UI change rather
than an agent change.

**The host filesystem is never touched.** `/pr/`, `/findings/`, and
`/patches/` live in agent state. Only `/skills/` is backed by real disk, and
read-only.

See `ARCHITECTURE.md` for the component reference and the deviations from the
original specification.
