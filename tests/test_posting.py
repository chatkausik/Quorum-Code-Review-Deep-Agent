"""The post step is the last line of defence against hallucinated line numbers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from quorum.models import ReviewComment
from quorum.tools.github_tools import (
    added_lines_by_path,
    re_anchor,
    should_skip,
)

FILE_LINES = [
    "import os",
    "",
    "def connect():",
    '    password = "hunter2"',
    "    return password",
]


def make_comment(**overrides):
    base = dict(
        path="app.py",
        line=4,
        severity="high",
        category="security",
        confidence=95,
        anchor_text='    password = "hunter2"',
        body="Hardcoded credential.",
    )
    base.update(overrides)
    return ReviewComment(**base)


class TestReAnchor:
    def test_correct_line_is_left_alone(self):
        comment = make_comment(line=4)
        result, moved = re_anchor(comment, FILE_LINES)
        assert not moved
        assert result.line == 4

    def test_hallucinated_line_snaps_to_the_anchor(self):
        comment = make_comment(line=17)
        result, moved = re_anchor(comment, FILE_LINES)
        assert moved
        assert result.line == 4

    def test_anchor_matches_ignoring_indentation(self):
        comment = make_comment(line=99, anchor_text='password = "hunter2"')
        result, moved = re_anchor(comment, FILE_LINES)
        assert moved
        assert result.line == 4

    def test_duplicate_anchors_pick_the_nearest(self):
        lines = ["x = 1", "y = 2", "x = 1", "z = 3", "x = 1"]
        comment = make_comment(line=4, anchor_text="x = 1")
        result, _ = re_anchor(comment, lines)
        assert result.line == 3  # nearer to 4 than either 1 or 5

    def test_missing_anchor_leaves_the_line_for_pass_two(self):
        comment = make_comment(line=4, anchor_text="this line does not exist")
        result, moved = re_anchor(comment, FILE_LINES)
        assert not moved
        assert result.line == 4

    def test_empty_file_is_survivable(self):
        comment = make_comment(line=4)
        result, moved = re_anchor(comment, [])
        assert not moved
        assert result.line == 4


PATCH = """@@ -1,4 +1,6 @@
 import os
+import sys
 
 def connect():
-    password = "old"
+    password = "hunter2"
+    return password
"""


def fake_pull(patch=PATCH, filename="app.py"):
    item = SimpleNamespace(
        filename=filename, patch=patch, previous_filename=None
    )
    return SimpleNamespace(get_files=lambda: [item])


class TestAddedLines:
    def test_only_added_lines_are_collected(self):
        added = added_lines_by_path(fake_pull())
        # +import sys (2), +password = "hunter2" (5), +return password (6)
        assert added["app.py"] == {2, 5, 6}

    def test_context_and_removed_lines_are_excluded(self):
        added = added_lines_by_path(fake_pull())["app.py"]
        assert 1 not in added  # context line "import os"
        assert 4 not in added  # context line "def connect():"

    def test_malformed_patch_does_not_abort(self):
        added = added_lines_by_path(fake_pull(patch="not a real patch"))
        assert added.get("app.py", set()) == set()

    def test_empty_patch_is_skipped(self):
        added = added_lines_by_path(fake_pull(patch=None))
        assert "app.py" not in added


class TestSkipFilter:
    @pytest.mark.parametrize(
        "path",
        [
            "package-lock.json",
            "poetry.lock",
            "web/dist/bundle.js",
            "static/app.min.js",
            "node_modules/left-pad/index.js",
            "docs/diagram.png",
        ],
    )
    def test_generated_and_vendored_paths_are_skipped(self, path):
        assert should_skip(path)

    @pytest.mark.parametrize(
        "path", ["src/app.py", "Dockerfile", ".github/workflows/ci.yml", "main.go"]
    )
    def test_real_source_is_reviewed(self, path):
        assert not should_skip(path)


class TestFormatting:
    def test_body_carries_the_attribution_prefix(self):
        body = make_comment().formatted_body()
        assert body.startswith("**[AI Review · HIGH / security]**")

    def test_suggestion_is_rendered_as_a_suggestion_block(self):
        body = make_comment(suggestion='password = os.environ["PW"]').formatted_body()
        assert "```suggestion" in body

    def test_sort_key_orders_by_severity_then_confidence(self):
        comments = [
            make_comment(severity="medium", confidence=99),
            make_comment(severity="critical", confidence=50),
            make_comment(severity="high", confidence=60),
            make_comment(severity="high", confidence=90),
        ]
        ordered = sorted(comments, key=lambda c: c.sort_key())
        assert [(c.severity, c.confidence) for c in ordered] == [
            ("critical", 50),
            ("high", 90),
            ("high", 60),
            ("medium", 99),
        ]


class TestNormalization:
    """A real finding must not be lost to field-name drift."""

    def test_comment_alias_maps_to_body(self):
        from quorum.agent import normalize_finding

        result = normalize_finding(
            {
                "path": "src/db.py",
                "line": 10,
                "severity": "critical",
                "confidence": 95,
                "anchor_text": "x = 1",
                "comment": "SQL injection via f-string.",
            }
        )
        assert result["body"] == "SQL injection via f-string."

    def test_missing_category_is_inferred_from_content(self):
        from quorum.agent import normalize_finding

        security = normalize_finding(
            {"body": "Hardcoded API key committed to source.", "anchor_text": "k=1"}
        )
        tests = normalize_finding(
            {"body": "No test coverage for this branch.", "anchor_text": "k=1"}
        )
        other = normalize_finding(
            {"body": "Off-by-one in the loop bound.", "anchor_text": "k=1"}
        )
        assert security["category"] == "security"
        assert tests["category"] == "tests"
        assert other["category"] == "correctness"

    def test_invalid_severity_defaults_to_medium(self):
        from quorum.agent import normalize_finding

        assert normalize_finding({"severity": "catastrophic"})["severity"] == "medium"

    def test_missing_confidence_lands_below_the_default_threshold(self):
        from quorum.agent import normalize_finding
        from quorum.config import CONFIDENCE_THRESHOLD

        assert normalize_finding({})["confidence"] < CONFIDENCE_THRESHOLD

    def test_confidence_is_clamped(self):
        from quorum.agent import normalize_finding

        assert normalize_finding({"confidence": 150})["confidence"] == 100
        assert normalize_finding({"confidence": -5})["confidence"] == 0

    def test_drifted_finding_survives_full_coercion(self):
        from quorum.agent import _coerce

        comment = _coerce(
            {
                "path": "src/db.py",
                "line": 10,
                "severity": "critical",
                "confidence": 95,
                "anchor_text": "    os.system(cmd)",
                "comment": "Command injection through os.system.",
            },
            set(),
        )
        assert comment is not None
        assert comment.category == "security"
        assert comment.body == "Command injection through os.system."

    def test_vfs_path_is_rewritten_to_the_repo_path(self):
        from quorum.agent import _coerce

        comment = _coerce(
            {
                "path": "/pr/db.py",
                "line": 3,
                "severity": "high",
                "category": "security",
                "confidence": 90,
                "anchor_text": "x = 1",
                "body": "Issue.",
            },
            set(),
        )
        assert comment.path == "db.py"

    def test_low_severity_is_dropped(self):
        from quorum.agent import _coerce

        assert _coerce(
            {
                "path": "a.py", "line": 1, "severity": "low", "category": "correctness",
                "confidence": 90, "anchor_text": "x", "body": "Nit.",
            },
            set(),
        ) is None

    def test_duplicates_by_path_and_line_are_dropped(self):
        from quorum.agent import _coerce

        seen: set = set()
        raw = {
            "path": "a.py", "line": 1, "severity": "high", "category": "security",
            "confidence": 90, "anchor_text": "x", "body": "Issue.",
        }
        assert _coerce(dict(raw), seen) is not None
        assert _coerce(dict(raw), seen) is None


class TestDropAccounting:
    """A zero-finding run must explain itself."""

    def _raw(self, **over):
        base = {
            "path": "a.py", "line": 1, "severity": "high", "category": "security",
            "confidence": 90, "anchor_text": "x = 1", "body": "Issue.",
        }
        base.update(over)
        return base

    def test_low_severity_drops_are_counted(self):
        from collections import Counter

        from quorum.agent import _coerce

        dropped = Counter()
        _coerce(self._raw(severity="low"), set(), dropped)
        assert dropped["low severity"] == 1

    def test_duplicate_drops_are_counted(self):
        from collections import Counter

        from quorum.agent import _coerce

        dropped, seen = Counter(), set()
        _coerce(self._raw(), seen, dropped)
        _coerce(self._raw(), seen, dropped)
        assert dropped["duplicate"] == 1

    def test_malformed_drops_are_counted(self):
        from collections import Counter

        from quorum.agent import _coerce

        dropped = Counter()
        _coerce({"path": "a.py"}, set(), dropped)  # missing required fields
        _coerce("not a dict", set(), dropped)
        assert dropped["malformed"] == 2

    def test_parse_findings_files_reports_reasons(self):
        import json
        from collections import Counter

        from quorum.agent import parse_findings_files

        files = {
            "/findings/a.json": {
                "content": json.dumps(
                    {"comments": [self._raw(severity="low"), self._raw(severity="low")]}
                )
            }
        }
        dropped = Counter()
        assert parse_findings_files(files, dropped) == []
        assert dropped["low severity"] == 2

    def test_result_renders_a_drop_summary(self):
        from quorum.models import ReviewContext, ReviewResult

        ctx = ReviewContext(
            owner="o", repo="r", pr_number=1, title="t", body="",
            head_sha="s", base_sha="b", author="a",
        )
        result = ReviewResult(
            comments=[], context=ctx, total_cost_usd=0.1, llm_calls=3,
            dropped={"low severity": 2}, subagent_reported=2,
        )
        assert result.drop_summary() == "2 low severity"

    def test_empty_drop_summary_when_nothing_filtered(self):
        from quorum.models import ReviewContext, ReviewResult

        ctx = ReviewContext(
            owner="o", repo="r", pr_number=1, title="t", body="",
            head_sha="s", base_sha="b", author="a",
        )
        assert ReviewResult(comments=[], context=ctx, total_cost_usd=0, llm_calls=0).drop_summary() == ""


class TestTokenResolution:
    def test_env_token_wins(self, monkeypatch):
        from quorum.config import github_token

        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        assert github_token() == "env-token"

    def test_falls_back_to_gh_cli(self, monkeypatch):
        import quorum.config as cfg

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(cfg, "_gh_cli_token", lambda: "gh-token")
        assert cfg.github_token() == "gh-token"

    def test_raises_with_actionable_guidance(self, monkeypatch):
        import quorum.config as cfg

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(cfg, "_gh_cli_token", lambda: None)
        with pytest.raises(RuntimeError, match="gh auth login"):
            cfg.github_token()

    def test_gh_fallback_is_not_shadowed_by_the_env_token(self, monkeypatch):
        """gh echoes GITHUB_TOKEN back if it is present in the environment."""
        import quorum.config as cfg

        captured = {}

        class FakeCompleted:
            stdout = "gho_from_keyring"

        def fake_run(argv, **kwargs):
            captured["env"] = kwargs.get("env", {})
            return FakeCompleted()

        monkeypatch.setenv("GITHUB_TOKEN", "github_pat_shadow")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/gh")
        monkeypatch.setattr("subprocess.run", fake_run)

        assert cfg._gh_cli_token() == "gho_from_keyring"
        assert "GITHUB_TOKEN" not in captured["env"]
        assert "GH_TOKEN" not in captured["env"]


class TestMergeRecovery:
    """A consolidation slip must not silently lose a subagent's finding."""

    def _raw(self, line, **over):
        base = {
            "path": "a.py", "line": line, "severity": "high", "category": "security",
            "confidence": 90, "anchor_text": f"line{line}", "body": "Issue.",
        }
        base.update(over)
        return base

    def test_findings_files_supplement_the_marker(self):
        import json
        from collections import Counter

        from quorum.agent import parse_findings_files, parse_marker_output
        from quorum.config import FINAL_MARKER

        marker_text = f"{FINAL_MARKER}\n{json.dumps([self._raw(1)])}"
        files = {
            "/findings/a.json": {
                "content": json.dumps(
                    {"comments": [self._raw(1), self._raw(2), self._raw(3)]}
                )
            }
        }
        seen: set = set()
        dropped: Counter = Counter()
        from_marker = parse_marker_output(marker_text, dropped, seen)
        recovered = parse_findings_files(files, dropped, seen)

        assert len(from_marker) == 1
        # Line 1 is already seen, so only 2 and 3 are recovered — no duplicates.
        assert [c.line for c in recovered] == [2, 3]
        assert len(from_marker + recovered) == 3

    def test_recovery_still_drops_low_severity(self):
        import json
        from collections import Counter

        from quorum.agent import parse_findings_files

        files = {
            "/findings/a.json": {
                "content": json.dumps({"comments": [self._raw(9, severity="low")]})
            }
        }
        dropped: Counter = Counter()
        assert parse_findings_files(files, dropped, set()) == []
        assert dropped["low severity"] == 1

    def test_shared_seen_prevents_double_counting(self):
        import json
        from collections import Counter

        from quorum.agent import parse_findings_files

        files = {
            "/findings/a.json": {"content": json.dumps({"comments": [self._raw(5)]})},
            "/findings/b.json": {"content": json.dumps({"comments": [self._raw(5)]})},
        }
        assert len(parse_findings_files(files, Counter(), set())) == 1
