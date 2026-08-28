# Quorum Operations and Event Guide

This guide explains how a pull-request review moves through Quorum, which
events appear in the Streamlit UI, where Claude models are used, which
boundaries are deterministic, and how human decisions become improvement
signals.

For implementation-level component notes, see
[ARCHITECTURE.md](../ARCHITECTURE.md). For installation and basic usage, see
[README.md](../README.md).

## System at a glance

```mermaid
flowchart LR
    Human([Human reviewer])
    UI[Streamlit UI]

    subgraph Trusted[Deterministic control plane]
        Loader[PR loader<br/>freeze target + head SHA]
        Manifest[Eligible-file manifest]
        VFS[(Ephemeral VFS<br/>immutable source + patches)]
        Health[Health contracts]
        Post[Post boundary<br/>re-anchor + diff validation]
    end

    subgraph Agentic[Claude review plane]
        Orchestrator[Claude Sonnet 5<br/>orchestration + adaptive reasoning]
        Python[Claude Haiku 4.5<br/>Python reviewer]
        Generic[Claude Haiku 4.5<br/>generic reviewer]
        Findings[(JSON findings artifacts)]
    end

    GitHub[(GitHub API)]
    Improve[(SQLite improvement store)]

    Human -->|select PR + run| UI
    UI --> Loader
    Loader -->|read metadata, files, source| GitHub
    Loader --> Manifest
    Manifest --> VFS
    VFS --> Orchestrator
    Orchestrator -->|delegate Python| Python
    Orchestrator -->|delegate other files| Generic
    Python --> Findings
    Generic --> Findings
    Findings --> Orchestrator
    Orchestrator -->|candidate comments| Health
    Manifest --> Health
    VFS --> Health
    Health --> UI
    Health --> Improve
    UI -->|approved comments only| Post
    Post -->|verify current head| GitHub
    Post -->|one GitHub review| GitHub
    UI -->|approval / rejection| Improve
    Post -->|posted / rejected by boundary| Improve

    classDef human fill:#19324d,stroke:#6cb6ff,color:#fff;
    classDef trusted fill:#17392f,stroke:#5fbf97,color:#fff;
    classDef agent fill:#3b2f18,stroke:#e3bc3f,color:#fff;
    classDef data fill:#2f263d,stroke:#bd93f9,color:#fff;
    class Human,UI human;
    class Loader,Manifest,VFS,Health,Post trusted;
    class Orchestrator,Python,Generic agent;
    class GitHub,Findings,Improve data;
```

The separation is intentional. Claude decides how to inspect and delegate a
review, but it cannot change the selected repository, mutate reviewed source,
write persistent memory, or post to GitHub.

## Current model routing

The local deployment is configured with `MODEL_PROVIDER=anthropic` and
`REVIEW_COST_PROFILE=economy`.

```mermaid
flowchart TD
    Start([Review request]) --> S5[Claude Sonnet 5<br/>low effort]
    S5 --> Decide{File type and complexity}
    Decide -->|Python| Hpy[Claude Haiku 4.5<br/>Python skills + Bandit]
    Decide -->|YAML, shell, config, other| Hgen[Claude Haiku 4.5<br/>generic security skills]
    Decide -->|Trivial file| Inline[Sonnet inline review]
    Hpy --> Artifact["/findings/&lt;path&gt;.json"]
    Hgen --> Artifact
    Inline --> Artifact
    Artifact --> S5
    S5 --> Final[Consolidated candidates]

    classDef sonnet fill:#3b2f18,stroke:#e3bc3f,color:#fff;
    classDef haiku fill:#17392f,stroke:#5fbf97,color:#fff;
    classDef artifact fill:#2f263d,stroke:#bd93f9,color:#fff;
    class S5,Inline sonnet;
    class Hpy,Hgen haiku;
    class Artifact,Final artifact;
```

Sonnet uses adaptive thinking because orchestration is a multi-step tool-use
problem. Haiku reviewers do not receive adaptive-thinking parameters: Haiku
4.5 does not support adaptive or interleaved thinking, and a fixed manual
thinking budget would increase review cost on every call. The deterministic
budget middleware still limits the complete run to the configured dollar and
call ceilings.

## End-to-end review sequence

```mermaid
sequenceDiagram
    autonumber
    actor Human as Human reviewer
    participant UI as Streamlit UI
    participant Run as run_review()
    participant GH as GitHub API
    participant VFS as Ephemeral VFS
    participant S as Sonnet orchestrator
    participant H as Haiku reviewers
    participant Eval as Health evaluator
    participant DB as SQLite improvement store

    Human->>UI: Enter owner, repo, PR, threshold, profile
    Human->>UI: Click Run Review
    UI->>Run: owner, repo, PR, profile, progress callback
    Run->>GH: Load PR identity and head SHA
    Run->>GH: Load changed-file manifest
    Run->>GH: Load eligible UTF-8 source at frozen head
    Run->>VFS: Preload immutable /pr and /patches evidence
    Run->>S: Start graph with frozen evidence

    S->>S: Plan work and read frozen manifest
    loop Every eligible file
        alt Python file
            S->>H: Delegate to python_reviewer
            H->>VFS: Read /pr/path
            H->>H: Run Bandit and pattern checks
            H->>VFS: Write /findings/path.json
        else Non-Python file
            S->>H: Delegate to generic_reviewer
            H->>VFS: Read /pr/path
            H->>H: Run pattern and manual checks
            H->>VFS: Write /findings/path.json
        else Trivial file
            S->>VFS: Read /pr/path inline
            S->>VFS: Write /findings/path.json
        end
    end

    S->>VFS: Read all findings artifacts
    S-->>Run: FINAL_FINDINGS_JSON candidates
    Run->>Run: Normalize, scope-filter, merge strongest duplicate
    Run->>Eval: Compare output with manifest and frozen evidence
    Eval-->>Run: Deterministic health checks
    Run->>DB: Persist run and recurring failures
    Run-->>UI: ReviewResult
    UI-->>Human: Findings, health, costs, and trace

    Human->>UI: Approve / reject findings
    UI->>DB: Save decision labels
    opt Post approved comments
        UI->>GH: Recheck PR head SHA
        UI->>GH: Read anchors and current diff
        UI->>GH: Create one validated review
        UI->>DB: Save posted / postability labels
    end
```

### Short-circuit branches

- If no eligible files remain after filtering, Quorum returns without an LLM
  call and records a zero-cost run.
- If the PR head changes while the manifest is being frozen, the run stops and
  asks for a new review.
- If a budget or call ceiling interrupts the graph, Quorum recovers the latest
  checkpoint and marks the output incomplete.
- If the reviewed head changes before posting, all approvals from the stale
  diff are rejected.
- If an anchor is absent or does not land on an added diff line, that comment
  is not posted.

## Live event flow

The UI receives progress through the `on_event` callback. Log events use this
shape:

```json
{
  "type": "log",
  "phase": "review",
  "icon": "🤖",
  "text": "Delegating to python_reviewer",
  "detail": "Review /pr/src/app.py"
}
```

Cost updates use `type: "stats"` and contain the current call count, cost,
token totals, cache share, and per-model rollup.

```mermaid
flowchart LR
    Plan[Plan] --> Memory[Memory]
    Memory --> Fetch[Fetch]
    Fetch --> Mount[Mount]
    Mount --> Review[Review]
    Review --> Consolidate[Consolidate]
    Consolidate --> Validate[Validate]

    Stats[(Stats side channel)] -. after model calls .-> Review
    Stats -. final totals .-> Validate

    classDef done fill:#17392f,stroke:#5fbf97,color:#fff;
    classDef active fill:#3b2f18,stroke:#e3bc3f,color:#fff;
    classDef side fill:#2f263d,stroke:#bd93f9,color:#fff;
    class Plan,Memory,Fetch,Mount,Consolidate,Validate done;
    class Review active;
    class Stats side;
```

### Event catalog

| Phase | Event | Source | Meaning |
| --- | --- | --- | --- |
| Plan | `Starting review of owner/repo#PR` | `run_review` | A unique run ID and selected profile are active. |
| Plan | `Planning the run` | Sonnet `write_todos` call | The orchestrator decomposed the review. |
| Memory | `Loaded trusted historical counters` | `FileBackedStore` | Only integer run/post counters are supplied; the model cannot mutate them. |
| Fetch | `Froze pull-request target and manifest` | Trusted loader | Repository, PR number, head SHA, eligible paths, and skipped paths are fixed. |
| Fetch | `Reading frozen PR metadata` | Bound `fetch_pr` tool | Sonnet is reading untrusted title/body data for context. |
| Fetch | `Reading frozen manifest` | Bound `list_files` tool | The model sees only files selected by trusted Python. |
| Fetch | `Reading frozen file` | Bound `get_file_content` tool | Content comes from the frozen in-memory copy, not a new arbitrary GitHub read. |
| Mount | `Preloaded immutable review evidence` | `run_review` | `/pr/` source and `/patches/` diffs are present before the graph starts. |
| Review | `Delegating to …` | Sonnet `task` call | A specialist subagent receives one exact VFS/repository path. |
| Review | `Running scanner` | Haiku `run_command` call | Validated Bandit arguments run in a temporary directory without a shell. |
| Review | `Pattern scan` | Reviewer `regex_search` call | A bounded, timeout-protected regex scan is running. |
| Review | `Reviewer wrote its result` | Reviewer `write_file` call | A JSON findings artifact, including an empty result, was written. |
| Consolidate | `Reading findings back` | Sonnet filesystem call | Per-file artifacts are being assembled. |
| Consolidate | `Consolidated candidate findings` | Deterministic parser | Low severity, malformed, duplicate, and out-of-scope items are accounted for. |
| Validate | `Evaluated deterministic health contract` | Health evaluator | Coverage, integrity, anchors, artifact validity, completion, and budgets were checked. |
| Any model phase | `stats` update | Cost middleware | Calls, tokens, cache usage, and cost meters refresh. |

The progress feed is observational. It does not grant new capabilities; trust
boundaries are enforced in tool schemas, backends, and deterministic Python.

## Health contracts

The **Improve** tab shows the checks from the current run and recurring
failures for the repository.

| Contract | Detects |
| --- | --- |
| `frozen_source_mount` | An eligible frozen source file was absent from the VFS. |
| `vfs_path_identity` | Source appeared outside the frozen manifest. |
| `source_content_integrity` | Mounted source differs from the trusted frozen copy. |
| `eligible_file_coverage` | An eligible file has no per-file review artifact. |
| `finding_artifact_scope` | A findings artifact belongs to an unknown file. |
| `finding_artifact_validity` | An artifact is malformed or lacks a JSON comments list. |
| `source_truncation` | A file exceeded the configured review size limit. |
| `finding_path_scope` | A candidate comment refers to an ineligible path. |
| `finding_anchor_exists` | The claimed anchor does not exist at the reviewed head. |
| `finding_line_in_file` | A claimed line is outside the file. |
| `finding_anchor_at_claimed_line` | The anchor exists elsewhere and requires re-anchoring. |
| `run_budget` | Cost/call ceilings were exceeded or halted the run. |
| `run_completed` | The graph ended with an error. |

## Supervised improvement loop

```mermaid
flowchart TD
    Run[Review run] --> Contract[Evaluate health contracts]
    Run --> RunStore[(review_runs)]
    Run --> Candidate[Candidate finding]
    Contract -->|pass| Healthy[Health result retained on run]
    Contract -->|fail| Issue[(improvement_issues)]
    Candidate --> Human{Human decision}
    Human -->|approve| Approved[approved label]
    Human -->|reject + reason| Rejected[rejected label]
    Approved --> Decisions[(finding_decisions)]
    Rejected --> Decisions
    Approved --> Eval[(evaluation_cases)]
    Rejected --> Eval
    Approved --> Boundary{Post boundary}
    Boundary -->|valid| Posted[posted label]
    Boundary -->|invalid anchor or diff| Failed[postability_failure label]
    Posted --> Decisions
    Failed --> Decisions
    Posted --> Eval
    Failed --> Eval
    Issue --> Triage[Human triage in Improve tab]
    Triage -->|mute| Muted[Muted issue]
    Triage -->|mark fixed| Fixed[Fixed issue]
    Muted -->|unmute| Issue
    Fixed -->|reopen| Issue
    Fixed -->|contract fails again| Issue
    Muted -->|contract fails again| Muted

    classDef healthy fill:#17392f,stroke:#5fbf97,color:#fff;
    classDef warn fill:#3b2f18,stroke:#e3bc3f,color:#fff;
    classDef data fill:#2f263d,stroke:#bd93f9,color:#fff;
    class Healthy,Approved,Posted healthy;
    class Issue,Rejected,Failed,Muted,Fixed warn;
    class RunStore,Decisions,Eval,Triage data;
```

The loop is supervised. It gathers failure evidence and human labels, but does
not edit prompts, modify code, create branches, or open pull requests.

### Issue status lifecycle

An issue is fingerprinted by repository and invariant, so the same contract
failing on a later run updates one row rather than creating a second.

| Situation | Effect |
| --- | --- |
| Contract fails on a new run | Occurrence count increments; a `fixed` issue reopens. |
| Contract fails again while muted | Occurrence count increments; the issue stays muted and out of the Open list. |
| The same run is persisted twice | Nothing inflates and no status changes -- but a contract failing for the first time is still recorded. |
| Human mutes, marks fixed, unmutes, or reopens | Status changes only; evidence and counts are preserved. |

Every transition is reversible and the **Improve** tab filters by status, so
muting an invariant hides it from the Open list without putting it out of
reach. Mark an issue fixed only after the contract passes on a fresh run --
the count is the evidence that it was real.

### Improvement database contents

Stored:

- run ID, repository, PR number, head SHA, selected profile;
- expected/reviewed file counts, call count, cost, and health checks;
- finding path, line, severity, category, confidence, and anchor hash;
- approved, rejected, posted, and postability-failure labels;
- deduplicated issue fingerprints, status, count, and path-level evidence.

Not stored:

- source code or patches;
- PR title or description;
- finding body, suggested fix, or raw anchor text;
- model-provider error messages that could echo reviewed input;
- API keys or GitHub tokens.

## Posting flow

```mermaid
flowchart TD
    Select[Human selects comments] --> Save[Save approval feedback]
    Select --> Post[Post approved comments]
    Post --> Head{Current head equals reviewed head?}
    Head -->|No| Stale[Abort entire post as stale]
    Head -->|Yes| Anchor{Anchor exists in source?}
    Anchor -->|No| DropAnchor[Drop invalid anchor]
    Anchor -->|Yes| Reanchor[Choose nearest added-line match]
    Reanchor --> Diff{Line is on added side?}
    Diff -->|No| DropDiff[Drop off-diff comment]
    Diff -->|Yes| Payload[Add to one review payload]
    Payload --> Submit[GitHub create_review]
    Submit --> Record[Record posted locations]
    DropAnchor --> RecordFailure[Record postability failure]
    DropDiff --> RecordFailure

    classDef pass fill:#17392f,stroke:#5fbf97,color:#fff;
    classDef fail fill:#4a2027,stroke:#f4606c,color:#fff;
    classDef action fill:#19324d,stroke:#6cb6ff,color:#fff;
    class Select,Save,Post,Reanchor,Payload action;
    class Submit,Record pass;
    class Stale,DropAnchor,DropDiff,RecordFailure fail;
```

## UI screenshots

Captured from a live run against the public sandbox pull request
`chatkausik/Evidensia.AI#7` at a 1600x1000 viewport, Economy profile, Anthropic
provider. The Streamlit developer toolbar is hidden in these captures; nothing
else is edited. `ui-empty.png` in the repository root is a historical capture
that predates the **Improve** tab -- treat it as a layout relic, not as
evidence of the current runtime.

### Empty state

The provider chip, cost profile, model routing, ceilings, and tracing status are
all readable before a run starts. Confirm these match your intent first.

![Quorum empty state](images/quorum-home-anthropic.png)

### Active review

The phase strip advances Plan -> Memory -> Fetch -> Mount -> Review ->
Consolidate -> Validate. Meters update per model call, so a run that is going to
breach the ceiling is visible long before it does.

![Quorum review in progress](images/quorum-review-progress.png)

### Completed review

Stat tiles carry the reviewed head SHA, the severity bar shows the mix, and each
finding is one row: severity dot, description, `file:line`, and confidence. Rows
at or above the threshold arrive pre-selected.

![Quorum findings list](images/quorum-findings.png)

### Finding detail

Clicking a row opens the evidence rather than expanding prose in place: the
offending line highlighted in its surrounding context, why it matters, and a
colour-coded suggested fix.

![Quorum finding detail dialog](images/quorum-finding-detail.png)

### Improve tab -- health contract

Thirteen deterministic checks run against trusted state, never against model
claims. Read this before treating a low finding count as a clean result.

![Quorum improve tab health contract](images/quorum-improve.png)

### Improve tab -- recurring issues

Failed contracts become fingerprinted issues with an occurrence count, last-seen
run, and evidence. In the capture below, `finding_anchor_exists` and
`finding_anchor_at_claimed_line` failed on a run whose findings were otherwise
sound -- the health contract caught anchors the posting step would have had to
drop.

![Quorum improvement issues](images/quorum-improve-issues.png)

### Improve tab -- closed issues stay reachable

Issues are filtered by status, and every transition is reversible. The two
issues below were marked fixed after an earlier rate-limited run; they remain
inspectable and can be reopened.

![Quorum fixed improvement issues](images/quorum-improve-fixed.png)

### Not captured

`docs/images/quorum-post-result.png` (posted count, review URL, re-anchored
comments, validation drops) is missing on purpose: producing it means submitting
a real review to a real pull request. Capture it during a genuine posting run
rather than manufacturing one for documentation.

### Capture rules

Do not capture `.env`, browser developer tools, request headers, API keys, or
private repository source. Use a public test PR or a synthetic repository when
creating documentation screenshots.

## Operator checklist

Before a review:

1. Confirm the UI shows `anthropic` and the intended cost profile.
2. Verify the PR head is stable and the GitHub token can read it.
3. Confirm the cost and call ceilings in the sidebar.
4. Decide whether documentation files should be included.

After a review:

1. Check the **Improve** tab before treating zero findings as a clean result.
2. Inspect any failed coverage, artifact, anchor, budget, or completion check.
3. Review low-confidence findings individually.
4. Save approval feedback even when all findings are rejected.
5. Post only after confirming the reviewed head SHA in the summary.
6. Follow the GitHub review link and verify the rendered comment placement.

For recurring failures:

1. Open the issue evidence in the **Improve** tab.
2. Reproduce with the same PR shape when possible.
3. Add or update a deterministic test before changing prompts or agent logic.
4. Mark the issue fixed only after the health contract passes on a new run.
5. Mute only expected exceptions; a recurrence of a fixed issue reopens it.
