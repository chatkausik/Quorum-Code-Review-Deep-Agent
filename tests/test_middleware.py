"""An agent in a tight loop cannot be trusted to stop itself."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from quorum.middleware import (
    BudgetExceeded,
    CostTrackingMiddleware,
    PRMetadataMiddleware,
)
from quorum.models import ReviewContext

CONTEXT = ReviewContext(
    owner="acme",
    repo="widgets",
    pr_number=42,
    title="Add checkout flow",
    body="Implements the new checkout.",
    head_sha="abc123",
    base_sha="def456",
    author="dev",
)


class TestPricing:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus-5", (5.00, 25.00)),
            ("claude-opus-5-20260101", (5.00, 25.00)),
            ("claude-sonnet-5", (3.00, 15.00)),
            ("claude-haiku-4-5", (1.00, 5.00)),
        ],
    )
    def test_known_models(self, model, expected):
        assert CostTrackingMiddleware.price_for(model) == expected

    def test_unknown_model_falls_back_to_the_most_expensive_tier(self):
        # An unpriced model must never appear free and slip past the ceiling.
        from quorum.config import FALLBACK_PRICING

        assert CostTrackingMiddleware.price_for("some-new-model") == FALLBACK_PRICING
        assert CostTrackingMiddleware.price_for(None) == FALLBACK_PRICING

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-5.4-mini", (0.75, 4.50)),
            ("gpt-5.4-nano", (0.20, 1.25)),
            ("gpt-5.4", (2.50, 15.00)),
            ("gpt-5.4-2026-03-05", (2.50, 15.00)),
            ("gpt-5.5", (5.00, 30.00)),
            ("gpt-5-mini", (0.25, 2.00)),
        ],
    )
    def test_nested_openai_ids_resolve_to_the_most_specific(self, model, expected):
        # "gpt-5.4" is a substring of "gpt-5.4-mini"; a first-match scan would
        # bill the mini model at the flagship rate.
        assert CostTrackingMiddleware.price_for(model) == expected


class TestCostCeiling:
    def test_cost_is_accumulated_accurately(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        cost.record("claude-sonnet-5", 1_000_000, 1_000_000)
        assert cost.total_cost_usd == pytest.approx(18.00, rel=1e-6)

    def test_call_ceiling_halts_the_run(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0, max_calls=25)
        for _ in range(25):
            cost.record("claude-sonnet-5", 10, 10)
        assert cost.calls == 25
        with pytest.raises(BudgetExceeded, match="Call ceiling"):
            cost.record("claude-sonnet-5", 10, 10)

    def test_cost_ceiling_halts_the_run(self):
        cost = CostTrackingMiddleware(max_cost_usd=1.00, max_calls=10_000)
        with pytest.raises(BudgetExceeded, match="Cost ceiling"):
            for _ in range(50):
                cost.record("claude-opus-5", 100_000, 5_000)
        assert cost.total_cost_usd > 1.00

    def test_reset_clears_counters(self):
        cost = CostTrackingMiddleware()
        cost.record("claude-sonnet-5", 100, 100)
        cost.reset()
        assert (cost.calls, cost.total_cost_usd, cost.log) == (0, 0.0, [])

    def test_after_model_reads_usage_metadata(self):
        cost = CostTrackingMiddleware()
        message = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 1000,
                "output_tokens": 200,
                "total_tokens": 1200,
            },
            response_metadata={"model_name": "claude-sonnet-5"},
        )
        cost.after_model({"messages": [message]}, None)
        assert cost.calls == 1
        assert cost.input_tokens == 1000
        assert cost.output_tokens == 200

    def test_after_model_ignores_non_ai_messages(self):
        cost = CostTrackingMiddleware()
        cost.after_model({"messages": [HumanMessage(content="hi")]}, None)
        assert cost.calls == 0

    def test_shared_instance_accumulates_across_agents(self):
        # Subagents are separately compiled graphs; one shared counter is what
        # makes the ceiling bound the whole run rather than just the orchestrator.
        cost = CostTrackingMiddleware(max_cost_usd=1000.0, max_calls=10)
        for _ in range(4):
            cost.record("claude-opus-5", 100, 100)  # orchestrator
        for _ in range(6):
            cost.record("claude-sonnet-5", 100, 100)  # subagents
        assert cost.calls == 10
        with pytest.raises(BudgetExceeded):
            cost.record("claude-sonnet-5", 100, 100)


class TestPRMetadata:
    def _request(self, system: str = "Base prompt."):
        from langchain_core.messages import SystemMessage

        captured = {}

        class FakeRequest:
            system_message = SystemMessage(content=system) if system else None

            def override(self, **kwargs):
                captured.update(kwargs)
                return self

        return FakeRequest(), captured

    def test_pr_context_is_folded_into_the_system_prompt(self):
        request, captured = self._request()
        PRMetadataMiddleware(CONTEXT).wrap_model_call(request, lambda r: r)
        content = captured["system_message"].content
        assert "acme/widgets" in content
        assert "Add checkout flow" in content
        assert "abc123" in content

    def test_the_existing_system_prompt_is_preserved(self):
        request, captured = self._request("You are an orchestrator.")
        PRMetadataMiddleware(CONTEXT).wrap_model_call(request, lambda r: r)
        assert captured["system_message"].content.startswith("You are an orchestrator.")

    def test_applies_on_every_call_not_just_the_first(self):
        middleware = PRMetadataMiddleware(CONTEXT)
        for _ in range(3):
            request, captured = self._request()
            middleware.wrap_model_call(request, lambda r: r)
            assert "acme/widgets" in captured["system_message"].content

    def test_missing_description_is_handled(self):
        ctx = ReviewContext(
            owner="a", repo="b", pr_number=1, title="T", body="",
            head_sha="s", base_sha="t", author="u",
        )
        request, captured = self._request()
        PRMetadataMiddleware(ctx).wrap_model_call(request, lambda r: r)
        assert "(no description provided)" in captured["system_message"].content

    def test_no_system_prompt_still_injects(self):
        request, captured = self._request(system="")
        PRMetadataMiddleware(CONTEXT).wrap_model_call(request, lambda r: r)
        assert "acme/widgets" in captured["system_message"].content


class TestCachePricing:
    """deepagents caches prompts automatically; cached input bills at a fraction."""

    def test_cache_reads_are_priced_at_a_tenth(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        cost.record("claude-sonnet-5", 1_000_000, 0, cache_read=1_000_000)
        # $3.00/MTok base × 0.1 = $0.30, not $3.00.
        assert cost.total_cost_usd == pytest.approx(0.30, rel=1e-6)

    def test_cache_writes_use_the_provider_multiplier(self):
        from quorum.config import CACHE_WRITE_MULTIPLIER

        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        cost.record("claude-sonnet-5", 1_000_000, 0, cache_write=1_000_000)
        assert cost.total_cost_usd == pytest.approx(
            3.00 * CACHE_WRITE_MULTIPLIER, abs=1e-9
        )

    def test_tiers_are_not_double_counted(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        # 100k total = 70k cache read + 20k cache write + 10k uncached.
        cost.record("claude-sonnet-5", 100_000, 0, cache_read=70_000, cache_write=20_000)
        from quorum.config import (
            CACHE_READ_MULTIPLIER,
            CACHE_WRITE_MULTIPLIER,
        )

        expected = (
            (10_000 / 1e6) * 3.00
            + (70_000 / 1e6) * 3.00 * CACHE_READ_MULTIPLIER
            + (20_000 / 1e6) * 3.00 * CACHE_WRITE_MULTIPLIER
        )
        assert cost.total_cost_usd == pytest.approx(expected, rel=1e-6)

    def test_fully_cached_call_is_far_cheaper_than_naive_pricing(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        cost.record("claude-opus-5", 200_000, 100, cache_read=199_000)
        naive = (200_000 / 1e6) * 5.00 + (100 / 1e6) * 25.00
        assert cost.total_cost_usd < naive / 5

    def test_after_model_reads_cache_details(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        message = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 7224,
                "output_tokens": 5,
                "total_tokens": 7229,
                "input_token_details": {"cache_read": 7214, "cache_creation": 0},
            },
            response_metadata={"model_name": "claude-sonnet-5"},
        )
        cost.after_model({"messages": [message]}, None)
        assert cost.cache_read_tokens == 7214
        # Only ~10 tokens billed at the full rate.
        assert cost.total_cost_usd < 0.003

    def test_cache_write_reported_under_its_ttl_bucket(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        message = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 7223,
                "output_tokens": 5,
                "total_tokens": 7228,
                "input_token_details": {
                    "cache_read": 0,
                    "cache_creation": 0,
                    "ephemeral_5m_input_tokens": 7214,
                },
            },
            response_metadata={"model_name": "claude-sonnet-5"},
        )
        cost.after_model({"messages": [message]}, None)
        assert cost.cache_write_tokens == 7214

    def test_negative_or_missing_details_are_safe(self):
        cost = CostTrackingMiddleware(max_cost_usd=1000.0)
        cost.record("claude-sonnet-5", 1000, 10, cache_read=-5, cache_write=0)
        assert cost.total_cost_usd > 0


class TestLangSmithGating:
    """A template placeholder must not switch tracing on."""

    @pytest.mark.parametrize(
        "value",
        ["<your-api-key>", "", "   ", None, "your-key-here", "changeme", "xxx"],
    )
    def test_placeholders_are_rejected(self, value):
        from quorum.config import is_placeholder

        assert is_placeholder(value)

    @pytest.mark.parametrize("value", ["lsv2_pt_abc123", "ls__realkey", "sk-abc"])
    def test_real_keys_are_accepted(self, value):
        from quorum.config import is_placeholder

        assert not is_placeholder(value)

    def test_tracing_off_without_a_real_key(self, monkeypatch):
        import quorum.config as cfg

        monkeypatch.setattr(cfg, "LANGSMITH_API_KEY", "<your-api-key>")
        assert not cfg.langsmith_enabled()
        assert not cfg.enable_langsmith()

    def test_enable_clears_stale_tracing_flags(self, monkeypatch):
        import os

        import quorum.config as cfg

        monkeypatch.setattr(cfg, "LANGSMITH_API_KEY", None)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        assert not cfg.enable_langsmith()
        # A stale flag would make every call try to export traces with no key.
        assert "LANGSMITH_TRACING" not in os.environ
        assert "LANGCHAIN_TRACING_V2" not in os.environ
