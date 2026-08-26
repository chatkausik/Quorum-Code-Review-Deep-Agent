"""GitHub access: PR metadata, changed files, file content, and the post step.

`post_approved_review` is pure deterministic Python with no LLM involvement —
posting authority never belongs to the agent.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import PurePosixPath

from github import Auth, Github
from github.GithubException import GithubException
from langchain.tools import tool
from unidiff import PatchSet

from quorum.config import REVIEW_DOCS, github_token
from quorum.models import PostResult, ReviewComment, ReviewContext

# Paths whose diffs are noise for a reviewer: generated, vendored, or minified.
SKIP_EXACT_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "go.sum",
}
SKIP_DIR_MARKERS = ("/dist/", "/build/", "/vendor/", "/node_modules/", "/.venv/")
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".svg", ".png", ".jpg", ".gif", ".pdf")
# Prose is not code. Reviewing it costs real money and yields soft findings
# (documented limitations read as security issues), so it is skipped unless
# explicitly requested via REVIEW_DOCS.
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
DOC_DIR_MARKERS = ("/docs/", "/doc/")

MAX_FILE_CHARS = 100_000


@lru_cache(maxsize=1)
def _client() -> Github:
    return Github(auth=Auth.Token(github_token()))


def is_doc(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(DOC_SUFFIXES) or any(
        marker in f"/{lowered}" for marker in DOC_DIR_MARKERS
    )


def should_skip(path: str, review_docs: bool | None = None) -> bool:
    """True when a changed file is not worth spending review budget on."""
    name = PurePosixPath(path).name
    lowered = path.lower()
    if name in SKIP_EXACT_NAMES:
        return True
    if any(marker in f"/{lowered}" for marker in SKIP_DIR_MARKERS):
        return True
    if lowered.endswith(SKIP_SUFFIXES):
        return True
    allow_docs = REVIEW_DOCS if review_docs is None else review_docs
    return is_doc(path) and not allow_docs


def load_pr_context(owner: str, repo: str, pr_number: int) -> ReviewContext:
    """Fetch PR metadata deterministically, before the agent starts.

    This is what PRMetadataMiddleware injects and what the post step trusts for
    the head SHA — neither depends on the LLM reporting them correctly.
    """
    pull = _client().get_repo(f"{owner}/{repo}").get_pull(pr_number)
    return ReviewContext(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        title=pull.title or "",
        body=pull.body or "",
        head_sha=pull.head.sha,
        base_sha=pull.base.sha,
        author=pull.user.login if pull.user else "unknown",
    )


@tool
def fetch_pr(owner: str, repo: str, pr_number: int) -> str:
    """Fetch pull request metadata: title, body, head SHA, base SHA, and author.

    Args:
        owner: Repository owner or organization.
        repo: Repository name.
        pr_number: Pull request number.
    """
    try:
        ctx = load_pr_context(owner, repo, pr_number)
    except GithubException as exc:
        return f"ERROR fetching PR: {exc.data.get('message', exc)}"
    return json.dumps(
        {
            "title": ctx.title,
            "body": ctx.body[:4000],
            "head_sha": ctx.head_sha,
            "base_sha": ctx.base_sha,
            "author": ctx.author,
        },
        indent=2,
    )


@tool
def list_files(owner: str, repo: str, pr_number: int) -> str:
    """List the changed files in a pull request, skipping generated and vendored paths.

    Returns one JSON object per file with its path, status, additions, deletions,
    and unified diff patch.

    Args:
        owner: Repository owner or organization.
        repo: Repository name.
        pr_number: Pull request number.
    """
    try:
        pull = _client().get_repo(f"{owner}/{repo}").get_pull(pr_number)
        entries = []
        skipped = []
        for item in pull.get_files():
            if should_skip(item.filename):
                skipped.append(item.filename)
                continue
            entries.append(
                {
                    "path": item.filename,
                    "status": item.status,
                    "additions": item.additions,
                    "deletions": item.deletions,
                    "patch": item.patch or "",
                }
            )
    except GithubException as exc:
        return f"ERROR listing files: {exc.data.get('message', exc)}"

    return json.dumps({"files": entries, "skipped": skipped}, indent=2)


@tool
def get_file_content(full_repo: str, path: str, ref: str) -> str:
    """Get the full source of a file at a git ref.

    Args:
        full_repo: Repository in "owner/repo" form.
        path: File path within the repository.
        ref: Git ref — use the PR's head SHA so line numbers match the diff.
    """
    try:
        blob = _client().get_repo(full_repo).get_contents(path, ref=ref)
    except GithubException as exc:
        return f"ERROR reading {path}: {exc.data.get('message', exc)}"
    if isinstance(blob, list):
        return f"ERROR: {path} is a directory, not a file."
    try:
        content = blob.decoded_content.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return f"ERROR: {path} is not UTF-8 text (likely binary)."
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... file truncated"
    return content


@lru_cache(maxsize=64)
def get_file_lines(full_repo: str, path: str, ref: str) -> tuple[str, ...]:
    """File content at a ref as a tuple of lines, cached for UI rendering."""
    try:
        blob = _client().get_repo(full_repo).get_contents(path, ref=ref)
        if isinstance(blob, list):
            return ()
        return tuple(blob.decoded_content.decode("utf-8").splitlines())
    except (GithubException, UnicodeDecodeError, AttributeError):
        return ()


# --------------------------------------------------------------------------
# Post step — deterministic, no LLM
# --------------------------------------------------------------------------


def added_lines_by_path(pull) -> dict[str, set[int]]:
    """Map each changed file to the set of line numbers on the '+' side of its diff."""
    added: dict[str, set[int]] = {}
    for item in pull.get_files():
        if not item.patch:
            continue
        # unidiff needs a file header to parse a bare patch body.
        header = (
            f"--- a/{item.previous_filename or item.filename}\n"
            f"+++ b/{item.filename}\n"
        )
        try:
            patch = PatchSet(header + item.patch)
        except Exception:  # noqa: BLE001 - a malformed patch must not abort posting
            continue
        lines: set[int] = set()
        for patched_file in patch:
            for hunk in patched_file:
                for line in hunk:
                    if line.is_added and line.target_line_no is not None:
                        lines.add(line.target_line_no)
        added[item.filename] = lines
    return added


def re_anchor(
    comment: ReviewComment, file_lines: list[str]
) -> tuple[ReviewComment, bool]:
    """Snap a comment to the line where its anchor_text actually appears.

    LLMs hallucinate line numbers; the verbatim anchor text is far more
    reliable. Returns the (possibly corrected) comment and whether it moved.
    """
    target = comment.anchor_text.strip()
    if not target:
        return comment, False

    matches = [i for i, text in enumerate(file_lines, start=1) if text.strip() == target]
    if not matches or comment.line in matches:
        return comment, False

    # Several identical lines can match; prefer the one nearest the claim.
    best = min(matches, key=lambda n: abs(n - comment.line))
    return comment.model_copy(update={"line": best}), True


def post_approved_review(
    context: ReviewContext,
    comments: list[ReviewComment],
) -> PostResult:
    """Validate approved comments and post them as a single GitHub review.

    Pass 1 re-anchors each comment by its verbatim anchor_text. Pass 2 drops
    anything that does not land on the '+' side of the diff. What survives is
    posted in one `create_review` call.
    """
    if not comments:
        return PostResult(posted=0, dropped_off_diff=[], re_anchored=[])

    repository = _client().get_repo(context.full_repo)
    pull = repository.get_pull(context.pr_number)

    # Pass 1 — re-anchor by anchor_text against the file at head.
    file_cache: dict[str, list[str]] = {}
    corrected: list[ReviewComment] = []
    re_anchored: list[str] = []
    for comment in comments:
        if comment.path not in file_cache:
            try:
                blob = repository.get_contents(comment.path, ref=context.head_sha)
                text = blob.decoded_content.decode("utf-8")  # type: ignore[union-attr]
                file_cache[comment.path] = text.splitlines()
            except (GithubException, UnicodeDecodeError, AttributeError):
                file_cache[comment.path] = []
        moved_comment, moved = re_anchor(comment, file_cache[comment.path])
        if moved:
            re_anchored.append(
                f"{comment.path}: line {comment.line} -> {moved_comment.line}"
            )
        corrected.append(moved_comment)

    # Pass 2 — drop anything not on the '+' side of the diff.
    added = added_lines_by_path(pull)
    payload = []
    dropped: list[str] = []
    for comment in corrected:
        valid = added.get(comment.path, set())
        if comment.line not in valid:
            dropped.append(f"{comment.path}:{comment.line} — not on the added side")
            continue
        payload.append(
            {
                "path": comment.path,
                "line": comment.line,
                "side": "RIGHT",
                "body": comment.formatted_body(),
            }
        )

    if not payload:
        return PostResult(posted=0, dropped_off_diff=dropped, re_anchored=re_anchored)

    try:
        review = pull.create_review(
            commit=repository.get_commit(context.head_sha),
            event="COMMENT",
            comments=payload,
        )
    except GithubException as exc:
        if exc.status == 403:
            raise PermissionError(
                "The GitHub token cannot post reviews on "
                f"{context.full_repo} (403).\n\n"
                "Reading a PR and writing a review need different permissions, "
                "so a token that loaded this PR fine can still fail here. Fix "
                "with either:\n"
                "  • Fine-grained token: grant 'Pull requests: Read and write' "
                "on this repository.\n"
                "  • Classic PAT: grant the 'repo' scope.\n"
                "  • Or run `gh auth login` and remove GITHUB_TOKEN from .env "
                "to use the GitHub CLI's token instead.\n\n"
                f"No comments were posted. The {len(payload)} validated "
                "comment(s) are unchanged — fix the token and click Post again."
            ) from exc
        raise
    return PostResult(
        posted=len(payload),
        dropped_off_diff=dropped,
        re_anchored=re_anchored,
        review_url=getattr(review, "html_url", None),
    )
