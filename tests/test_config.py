from __future__ import annotations

import pytest

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


def test_unknown_provider_or_profile_fails_closed():
    with pytest.raises(config.ConfigurationError, match="MODEL_PROVIDER"):
        config._choice("MODEL_PROVIDER", "typo-provider", {"openai", "anthropic"})
    with pytest.raises(config.ConfigurationError, match="REVIEW_COST_PROFILE"):
        config.resolve_review_settings("turbo")


def test_numeric_and_boolean_overrides_are_validated(monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_LLM_CALLS", "0")
    with pytest.raises(config.ConfigurationError, match="at least 1"):
        config.resolve_review_settings("economy")

    monkeypatch.setenv("REVIEW_MAX_LLM_CALLS", "5")
    monkeypatch.setenv("REVIEW_DOCS", "sometimes")
    with pytest.raises(config.ConfigurationError, match="true or false"):
        config.resolve_review_settings("economy")
