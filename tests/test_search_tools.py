from __future__ import annotations

from types import SimpleNamespace

from quorum.tools.search_tools import make_regex_search, regex_search


def test_regex_search_reports_line_numbers():
    result = regex_search.invoke(
        {"pattern": r"password\s*=", "content": "safe = 1\npassword = 'x'\n"}
    )

    assert "line 2: password = 'x'" in result


def test_regex_search_rejects_invalid_or_oversized_patterns():
    assert regex_search.invoke({"pattern": "(", "content": "x"}).startswith(
        "ERROR: invalid regex"
    )
    assert regex_search.invoke({"pattern": "x" * 501, "content": "x"}).startswith(
        "ERROR: pattern too long"
    )


def test_regex_search_times_out_pathological_backtracking():
    result = regex_search.invoke(
        {"pattern": r"(a+)+$", "content": ("a" * 4000) + "!"}
    )

    assert result.startswith("ERROR: regex search timed out")


def test_bound_regex_search_reads_the_frozen_vfs_path():
    class Backend:
        def read(self, path):
            assert path == "/pr/src/app.py"
            return SimpleNamespace(
                error=None,
                file_data={"content": "safe = 1\npassword = 'x'\n", "encoding": "utf-8"},
            )

    bound = make_regex_search(Backend())
    schema = bound.args_schema.model_json_schema()["properties"]
    assert set(schema) == {"pattern", "path"}
    result = bound.invoke({"pattern": r"password\s*=", "path": "/pr/src/app.py"})
    assert "line 2: password = 'x'" in result


def test_bound_regex_search_rejects_host_or_traversal_paths():
    class Backend:
        def read(self, _path):
            raise AssertionError("unsafe paths must be rejected before a read")

    bound = make_regex_search(Backend())
    assert bound.invoke({"pattern": "x", "path": "/etc/passwd"}).startswith("REJECTED")
    assert bound.invoke({"pattern": "x", "path": "/pr/../secret"}).startswith("REJECTED")
