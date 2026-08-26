"""Sandboxed subprocess execution, restricted to an allowlist of scanners.

The allowlist is a design requirement, not a polish item: `rm -rf` or
`curl evil.com` must be impossible by construction, not merely discouraged
by the prompt.

Files live in the agent's virtual filesystem (`/pr/<name>`), which no real
subprocess can open. `run_command` therefore resolves VFS paths through the
backend and materializes them to short-lived temp files. File content never
passes through the LLM's context to get here.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import PurePosixPath

from langchain.tools import tool
from langchain_core.tools import BaseTool

from quorum.config import (
    ALLOWED_COMMANDS,
    COMMAND_TIMEOUT_SECONDS,
    FINDINGS_DIR,
    PATCHES_DIR,
    PR_DIR,
)

# Characters that would let a caller chain or redirect commands if the string
# were ever handed to a shell. We never use shell=True, but we reject them
# outright so a malformed prompt cannot smuggle intent past the allowlist.
SHELL_METACHARACTERS = set(";|&$`><\n\r\\")

VFS_PREFIXES = (PR_DIR, FINDINGS_DIR, PATCHES_DIR)

MAX_OUTPUT_CHARS = 8000

# Scanners are installed as project dependencies, so they live in the running
# interpreter's bin directory — which is not on PATH when the app is launched
# via an absolute interpreter path.
SEARCH_PATH = os.pathsep.join(
    [os.path.dirname(sys.executable), os.environ.get("PATH", "")]
)


def resolve_executable(program: str) -> str | None:
    """Locate an allowlisted scanner, preferring the active environment."""
    return shutil.which(program, path=SEARCH_PATH)


class CommandRejected(ValueError):
    """Raised when a command fails the allowlist or metacharacter checks."""


def validate_command(cmd: str) -> list[str]:
    """Parse and validate a command string, returning its argv.

    Raises:
        CommandRejected: if the command is unparseable, uses shell
            metacharacters, or is not on the allowlist.
    """
    if not cmd or not cmd.strip():
        raise CommandRejected("Empty command.")

    offending = SHELL_METACHARACTERS & set(cmd)
    if offending:
        raise CommandRejected(
            f"Command contains disallowed shell metacharacters: "
            f"{''.join(sorted(offending))!r}. Commands are run without a shell."
        )

    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise CommandRejected(f"Could not parse command: {exc}") from exc

    if not argv:
        raise CommandRejected("Empty command.")

    program = PurePosixPath(argv[0]).name
    if program not in ALLOWED_COMMANDS:
        raise CommandRejected(
            f"Command {program!r} is not allowed. Permitted commands: "
            f"{', '.join(sorted(ALLOWED_COMMANDS))}."
        )
    # Reject a path-qualified invocation (./bandit, /tmp/bandit) that merely
    # ends in an allowlisted name.
    if program != argv[0]:
        raise CommandRejected(
            f"Use the bare command name, not a path: {argv[0]!r}."
        )
    return argv


def _is_vfs_path(token: str) -> bool:
    return any(
        token == prefix or token.startswith(prefix + "/") for prefix in VFS_PREFIXES
    )


def make_run_command(backend) -> BaseTool:
    """Build the `run_command` tool bound to a backend for VFS resolution."""

    @tool
    def run_command(cmd: str) -> str:
        """Run an allowlisted static-analysis command (bandit or semgrep).

        Virtual filesystem paths such as /pr/app.py are materialized to real
        temporary files automatically, so pass them exactly as they appear in
        the VFS. Example: `bandit -ll -f json /pr/app.py`.

        Args:
            cmd: The command to run. Only `bandit` and `semgrep` are permitted;
                no shell metacharacters, pipes, or redirection.

        Returns:
            Combined stdout/stderr from the command, or an explanatory error.
        """
        try:
            argv = validate_command(cmd)
        except CommandRejected as exc:
            return f"REJECTED: {exc}"

        from deepagents.backends.utils import file_data_to_string

        with tempfile.TemporaryDirectory(prefix="review-sandbox-") as workdir:
            resolved: list[str] = []
            for token in argv:
                if not _is_vfs_path(token):
                    resolved.append(token)
                    continue
                result = backend.read(token)
                if result.error or not result.file_data:
                    return (
                        f"ERROR: {token} is not present in the virtual filesystem. "
                        "Mount it with write_file before running a scanner."
                    )
                content = file_data_to_string(result.file_data)
                # Preserve the basename so scanner output references a
                # recognizable filename and extension-based detection works.
                local = os.path.join(workdir, PurePosixPath(token).name)
                with open(local, "w", encoding="utf-8") as handle:
                    handle.write(content)
                resolved.append(local)

            executable = resolve_executable(argv[0])
            if executable is None:
                return (
                    f"ERROR: {argv[0]} is not installed in this environment. "
                    "Log this and continue the review without it."
                )
            resolved[0] = executable

            try:
                completed = subprocess.run(  # noqa: S603 - argv validated above
                    resolved,
                    capture_output=True,
                    text=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                    cwd=workdir,
                    shell=False,
                    env={"PATH": SEARCH_PATH, "HOME": workdir},
                )
            except FileNotFoundError:
                return (
                    f"ERROR: {argv[0]} is not installed in this environment. "
                    "Log this and continue the review without it."
                )
            except subprocess.TimeoutExpired:
                return f"ERROR: {argv[0]} timed out after {COMMAND_TIMEOUT_SECONDS}s."

        output = (completed.stdout or "") + (completed.stderr or "")
        output = output.strip() or "(no output)"
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n... output truncated"
        return f"exit_code={completed.returncode}\n{output}"

    return run_command
