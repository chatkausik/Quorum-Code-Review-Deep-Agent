"""Central configuration: models, pricing, thresholds, credentials."""

from __future__ import annotations

import os
import sysconfig
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resource_dir(name: str) -> Path:
    """Resolve a resource in a source checkout or an installed wheel."""
    override = os.getenv(f"QUORUM_{name.upper()}_DIR")
    candidates = [
        Path(override).expanduser() if override else None,
        PROJECT_ROOT / name,
        Path(sysconfig.get_path("data")) / "share" / "quorum" / name,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    # Keep a deterministic path in the error message produced by build_agent.
    return PROJECT_ROOT / name


SKILLS_DIR = _resource_dir("skills")


class ConfigurationError(ValueError):
    """Raised when an environment setting would make a run ambiguous or unsafe."""


def _choice(name: str, value: str, allowed: set[str]) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigurationError(f"{name} must be one of: {choices}; got {value!r}.")
    return normalized


def _env_int(
    name: str,
    fallback: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name)
    try:
        value = fallback if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer; got {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}; got {value}.")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{name} must be at most {maximum}; got {value}.")
    return value


def _env_float(name: str, fallback: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        value = fallback if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number; got {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}; got {value}.")
    return value


def _env_bool(name: str, fallback: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"{name} must be true or false; got {raw!r}.")
    return normalized == "true"


def _env_text(name: str, fallback: str) -> str:
    value = os.getenv(name, fallback).strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty.")
    return value

# --- provider -------------------------------------------------------------
MODEL_PROVIDER = _choice(
    "MODEL_PROVIDER", os.getenv("MODEL_PROVIDER", "openai"), {"openai", "anthropic"}
)

# --- cost profiles --------------------------------------------------------
# Measured on a 4-file review: output tokens were 53% of spend and cache
# writes 38%. Reasoning tokens bill as output, so effort is the strongest
# single lever, followed by the model tier used for subagents (which make
# most of the calls).
COST_PROFILES: dict[str, dict[str, dict[str, object]]] = {
    "openai": {
        "economy": {
            "orchestrator_model": "gpt-5.4-mini",
            "subagent_model": "gpt-5.4-nano",
            "orchestrator_effort": "low",
            "subagent_effort": "low",
            "max_tokens": 8000,
            "review_docs": False,
        },
        "balanced": {
            "orchestrator_model": "gpt-5.4",
            "subagent_model": "gpt-5.4-mini",
            "orchestrator_effort": "medium",
            "subagent_effort": "low",
            "max_tokens": 12000,
            "review_docs": False,
        },
        "thorough": {
            "orchestrator_model": "gpt-5.5",
            "subagent_model": "gpt-5.4",
            "orchestrator_effort": "high",
            "subagent_effort": "medium",
            "max_tokens": 16000,
            "review_docs": True,
        },
    },
    "anthropic": {
        "economy": {
            "orchestrator_model": "claude-sonnet-5",
            "subagent_model": "claude-haiku-4-5",
            "orchestrator_effort": "low",
            "subagent_effort": "low",
            "max_tokens": 8000,
            "review_docs": False,
        },
        "balanced": {
            "orchestrator_model": "claude-sonnet-5",
            "subagent_model": "claude-sonnet-5",
            "orchestrator_effort": "medium",
            "subagent_effort": "low",
            "max_tokens": 12000,
            "review_docs": False,
        },
        "thorough": {
            "orchestrator_model": "claude-opus-5",
            "subagent_model": "claude-sonnet-5",
            "orchestrator_effort": "high",
            "subagent_effort": "medium",
            "max_tokens": 16000,
            "review_docs": True,
        },
    },
}

# Costs measured on a real 4-file PR with ~20 findings (chatkausik/Evidensia.AI#7).
PROFILE_LABELS = {
    "economy": "Economy — ~$0.07/PR. Same findings as Balanced on our benchmark.",
    "balanced": "Balanced — ~$0.23/PR. Recommended default.",
    "thorough": "Thorough — ~$1+/PR. Deepest analysis, highest cost.",
}

COST_PROFILE = _choice(
    "REVIEW_COST_PROFILE",
    os.getenv("REVIEW_COST_PROFILE", "balanced"),
    set(PROFILE_LABELS),
)
_profile = COST_PROFILES[MODEL_PROVIDER][COST_PROFILE]

# --- models ---------------------------------------------------------------
# Orchestrator plans, dispatches and consolidates; subagents do per-file
# review. Explicit env vars still win over the profile.
ORCHESTRATOR_MODEL = _env_text(
    "ORCHESTRATOR_MODEL", str(_profile["orchestrator_model"])
)
SUBAGENT_MODEL = _env_text("SUBAGENT_MODEL", str(_profile["subagent_model"]))
ORCHESTRATOR_EFFORT = _choice(
    "ORCHESTRATOR_EFFORT",
    os.getenv("ORCHESTRATOR_EFFORT", str(_profile["orchestrator_effort"])),
    {"low", "medium", "high"},
)
SUBAGENT_EFFORT = _choice(
    "SUBAGENT_EFFORT",
    os.getenv("SUBAGENT_EFFORT", str(_profile["subagent_effort"])),
    {"low", "medium", "high"},
)
MAX_OUTPUT_TOKENS = _env_int(
    "REVIEW_MAX_OUTPUT_TOKENS", int(_profile["max_tokens"]), minimum=1
)
# Reviewing prose costs real money and yields soft findings; off by default.
REVIEW_DOCS = _env_bool("REVIEW_DOCS", bool(_profile["review_docs"]))

# USD per 1M tokens, keyed by model id: (input, output).
PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    # Anthropic
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# Used when a model id is missing from PRICING, so an unknown model can never
# silently cost nothing and slip past the kill switch.
FALLBACK_PRICING = (5.00, 30.00)

# Cached input is billed at 10% of the input rate by both providers.
CACHE_READ_MULTIPLIER = 0.1
# Anthropic charges a premium to WRITE the cache; OpenAI does not bill writes.
CACHE_WRITE_MULTIPLIER = 1.25 if MODEL_PROVIDER == "anthropic" else 0.0

# --- run limits -----------------------------------------------------------
MAX_COST_USD = _env_float("REVIEW_MAX_COST_USD", 1.00, minimum=0.01)
MAX_LLM_CALLS = _env_int("REVIEW_MAX_LLM_CALLS", 25, minimum=1)
MAX_REVIEW_FILES = _env_int("REVIEW_MAX_FILES", 50, minimum=1)
MAX_TOTAL_SOURCE_CHARS = _env_int(
    "REVIEW_MAX_TOTAL_CHARS", 1_000_000, minimum=1_000
)

# --- review policy --------------------------------------------------------
CONFIDENCE_THRESHOLD = _env_int(
    "REVIEW_CONFIDENCE_THRESHOLD", 75, minimum=0, maximum=100
)
FINAL_MARKER = "FINAL_FINDINGS_JSON:"

# --- memory ---------------------------------------------------------------
MEMORY_DIR = Path(
    os.getenv("REVIEW_MEMORY_DIR", str(Path.home() / ".quorum_memory"))
)
# Pre-rename location; stats are migrated from here on first run so a rename
# does not silently reset a repository's history.
LEGACY_MEMORY_DIR = Path.home() / ".review_agent_memory"
IMPROVEMENT_DB = Path(
    os.getenv("REVIEW_IMPROVEMENT_DB", str(MEMORY_DIR / "improvement.db"))
).expanduser()

# --- observability --------------------------------------------------------
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "code-review-agent")
LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
LANGSMITH_HOST = os.getenv("LANGSMITH_HOST", "https://smith.langchain.com")


# .env templates ship with placeholder values. A placeholder is a non-empty
# string, so a naive truth check turns tracing on and then fails every call.
_PLACEHOLDER_MARKERS = ("<", ">", "your-", "your_", "changeme", "xxx", "placeholder")


def is_placeholder(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def langsmith_enabled() -> bool:
    """Tracing is opt-in: it needs a real key, not a template placeholder."""
    return not is_placeholder(LANGSMITH_API_KEY)


def enable_langsmith() -> bool:
    """Turn on LangChain tracing for this process. Returns whether it is on."""
    if not langsmith_enabled():
        os.environ.pop("LANGSMITH_TRACING", None)
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT
    os.environ["LANGSMITH_ENDPOINT"] = LANGSMITH_ENDPOINT
    return True


# --- sandbox --------------------------------------------------------------
# Scanner support is deliberately narrower than a generic executable allowlist.
# Bandit is installed with the project and has a small argument surface that we
# validate explicitly. Semgrep may be reintroduced when rules are vendored and
# its network-capable configuration flags can be constrained safely.
ALLOWED_COMMANDS = frozenset({"bandit"})
COMMAND_TIMEOUT_SECONDS = 60

# --- virtual filesystem layout -------------------------------------------
PR_DIR = "/pr"
FINDINGS_DIR = "/findings"
PATCHES_DIR = "/patches"
SKILLS_MOUNT = "/skills"


def resolve_profile(name: str | None = None) -> dict[str, object]:
    """Return one validated cost profile."""
    key = _choice("REVIEW_COST_PROFILE", name or COST_PROFILE, set(PROFILE_LABELS))
    return dict(COST_PROFILES[MODEL_PROVIDER][key])


@dataclass(frozen=True)
class ReviewSettings:
    """Fully resolved settings for one review run.

    Keeping this as one value prevents the UI, file filter, and model builder
    from each interpreting a cost profile differently.
    """

    profile_name: str
    orchestrator_model: str
    subagent_model: str
    orchestrator_effort: str
    subagent_effort: str
    max_tokens: int
    review_docs: bool
    max_cost_usd: float
    max_llm_calls: int
    max_review_files: int
    max_total_source_chars: int


def resolve_review_settings(name: str | None = None) -> ReviewSettings:
    """Resolve a profile and then apply explicit environment overrides."""
    profile_name = _choice(
        "REVIEW_COST_PROFILE", name or COST_PROFILE, set(PROFILE_LABELS)
    )
    spec = resolve_profile(profile_name)
    return ReviewSettings(
        profile_name=profile_name,
        orchestrator_model=_env_text(
            "ORCHESTRATOR_MODEL", str(spec["orchestrator_model"])
        ),
        subagent_model=_env_text("SUBAGENT_MODEL", str(spec["subagent_model"])),
        orchestrator_effort=_choice(
            "ORCHESTRATOR_EFFORT",
            os.getenv("ORCHESTRATOR_EFFORT", str(spec["orchestrator_effort"])),
            {"low", "medium", "high"},
        ),
        subagent_effort=_choice(
            "SUBAGENT_EFFORT",
            os.getenv("SUBAGENT_EFFORT", str(spec["subagent_effort"])),
            {"low", "medium", "high"},
        ),
        max_tokens=_env_int(
            "REVIEW_MAX_OUTPUT_TOKENS", int(spec["max_tokens"]), minimum=1
        ),
        review_docs=_env_bool("REVIEW_DOCS", bool(spec["review_docs"])),
        max_cost_usd=_env_float("REVIEW_MAX_COST_USD", MAX_COST_USD, minimum=0.01),
        max_llm_calls=_env_int("REVIEW_MAX_LLM_CALLS", MAX_LLM_CALLS, minimum=1),
        max_review_files=_env_int(
            "REVIEW_MAX_FILES", MAX_REVIEW_FILES, minimum=1
        ),
        max_total_source_chars=_env_int(
            "REVIEW_MAX_TOTAL_CHARS", MAX_TOTAL_SOURCE_CHARS, minimum=1_000
        ),
    )


def anthropic_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env before running a review."
        )
    return key


def openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env before running a review."
        )
    return key


def provider_api_key() -> str:
    """The key for whichever provider is configured."""
    return openai_api_key() if MODEL_PROVIDER == "openai" else anthropic_api_key()


def _gh_cli_token() -> str | None:
    """Fall back to the GitHub CLI's token when no env token is configured."""
    import shutil
    import subprocess

    if not shutil.which("gh"):
        return None
    # `gh` honours GITHUB_TOKEN/GH_TOKEN from the environment and will echo it
    # straight back. load_dotenv() has already put .env's token there, so
    # without stripping these the "fallback" just returns the token it is
    # meant to replace.
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token or None


def github_token() -> str:
    token = os.getenv("GITHUB_TOKEN") or _gh_cli_token()
    if not token:
        raise RuntimeError(
            "No GitHub token available. Either set GITHUB_TOKEN in .env — a "
            "classic PAT with the 'repo' scope, or a fine-grained token with "
            "'Pull requests: read and write' on the target repository — or run "
            "`gh auth login` and leave GITHUB_TOKEN unset to use the CLI's token."
        )
    return token
