from __future__ import annotations

from quorum import config


def test_review_settings_apply_explicit_environment_overrides(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MODEL", "custom-orchestrator")
    monkeypatch.setenv("SUBAGENT_MODEL", "custom-subagent")
    monkeypatch.setenv("REVIEW_MAX_OUTPUT_TOKENS", "1234")
    monkeypatch.setenv("REVIEW_DOCS", "true")

    settings = config.resolve_review_settings("economy")

    assert settings.orchestrator_model == "custom-orchestrator"
    assert settings.subagent_model == "custom-subagent"
    assert settings.max_tokens == 1234
    assert settings.review_docs is True


def test_selected_profile_controls_docs_when_env_is_absent(monkeypatch):
    monkeypatch.delenv("REVIEW_DOCS", raising=False)

    assert config.resolve_review_settings("balanced").review_docs is False
    assert config.resolve_review_settings("thorough").review_docs is True
