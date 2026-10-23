"""Pick a provider and model per role from environment variables.

    get_llm("extractor")   # EXTRACTOR_PROVIDER / EXTRACTOR_MODEL
    get_llm("agent")       # AGENT_PROVIDER / AGENT_MODEL
    get_llm("judge")       # JUDGE_PROVIDER / JUDGE_MODEL

A role with no provider set uses LLM_PROVIDER (default: anthropic). LLM_MODEL
applies only when the role ends up on that same default provider; otherwise the
provider's default model is used. Explicit provider/model arguments (CLI flags)
win over the environment.
"""
from __future__ import annotations

import importlib
import os
import warnings
from pathlib import Path

from dotenv import load_dotenv

from .base import LLMClient, LLMError

ROLES = ("extractor", "agent", "judge", "planner")

PROVIDERS = {
    "anthropic": ("anthropic_provider", "AnthropicProvider"),
    "openai": ("openai_provider", "OpenAIProvider"),
    "gemini": ("gemini_provider", "GeminiProvider"),
    "local": ("local_provider", "LocalProvider"),
    "mock": ("mock_provider", "MockProvider"),
}

# Defaults only; set <ROLE>_MODEL or LLM_MODEL to choose. Anthropic default follows blueprint section 6.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-3.8-flash",
    "local": "qwen2.5:7b-instruct",
    "mock": "mock",
}

# Load .env once, at import, without overriding variables already set.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def resolve(role: str, provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    """Return (provider, model) for a role without building a client."""
    if role not in ROLES:
        raise LLMError(f"unknown LLM role {role!r}; expected one of {ROLES}")
    key = role.upper()
    default_provider = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    provider = (provider or os.getenv(f"{key}_PROVIDER") or default_provider).strip().lower()
    if provider not in PROVIDERS:
        raise LLMError(f"unknown LLM provider {provider!r}; expected one of {sorted(PROVIDERS)}")
    model = model or os.getenv(f"{key}_MODEL")
    if not model and provider == default_provider:
        model = os.getenv("LLM_MODEL")
    return provider, (model or DEFAULT_MODELS[provider]).strip()


def resolve_fallbacks(role: str) -> list[tuple[str, str]]:
    """Ordered (provider, model) pairs to try when the role's provider is out of capacity."""
    key = role.upper()
    chain = os.getenv(f"{key}_FALLBACKS") or os.getenv("LLM_FALLBACKS")
    pairs: list[tuple[str, str]] = []
    if chain:
        for item in chain.split(","):
            provider, _, model = item.strip().partition(":")     # model names may contain ':' too
            provider = provider.strip().lower()
            if provider:
                pairs.append((provider, model.strip() or DEFAULT_MODELS.get(provider, "")))
    else:
        provider = os.getenv(f"{key}_FALLBACK_PROVIDER") or os.getenv("LLM_FALLBACK_PROVIDER")
        if provider:
            provider = provider.strip().lower()
            model = (os.getenv(f"{key}_FALLBACK_MODEL") or os.getenv("LLM_FALLBACK_MODEL")
                     or DEFAULT_MODELS.get(provider, ""))
            pairs.append((provider, model.strip()))
    for provider, _ in pairs:
        if provider not in PROVIDERS:
            raise LLMError(f"unknown fallback provider {provider!r}")
    return pairs


def get_llm(role: str, *, provider: str | None = None, model: str | None = None,
            fallback: bool = True, **kwargs) -> LLMClient:
    """Build the client for a role. Extra kwargs go to the provider (e.g. responses= for mock).
    If a fallback provider is configured (and no explicit provider was asked for), the client
    switches to it on rate-limit / quota errors."""
    explicit = provider is not None
    provider, model = resolve(role, provider, model)
    if role == "judge" and (provider, model) == resolve("extractor"):
        warnings.warn(f"judge and extractor both use {provider}:{model}; "
                      "the judge would be grading its own work. Set JUDGE_PROVIDER/JUDGE_MODEL.",
                      UserWarning, stacklevel=2)
    primary = _build(provider, model, role, **kwargs)
    chain = [fb for fb in (resolve_fallbacks(role) if fallback and not explicit else []) if fb != (provider, model)]
    if chain:
        from .fallback import FallbackClient
        return FallbackClient(primary, *[_build(p, m, role) for p, m in chain])
    return primary


def _build(provider: str, model: str, role: str, **kwargs) -> LLMClient:
    module_name, class_name = PROVIDERS[provider]
    try:
        module = importlib.import_module(f".providers.{module_name}", __package__)
    except ImportError as e:
        raise LLMError(f"provider {provider!r} needs its SDK installed: {e}") from e
    try:
        return getattr(module, class_name)(model, role, **kwargs)
    except LLMError:
        raise
    except Exception as e:  # e.g. missing API key raised by the vendor SDK
        raise LLMError(f"could not set up {provider}:{model}: {e}") from e
