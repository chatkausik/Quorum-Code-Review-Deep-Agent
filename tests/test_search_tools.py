from __future__ import annotations

from quorum.tools.search_tools import regex_search


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
