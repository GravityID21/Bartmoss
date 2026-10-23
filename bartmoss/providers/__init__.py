"""Provider factory: turns a provider name from config into a ready client."""

from __future__ import annotations

from bartmoss.providers.base import ChatResult, Provider, ProviderError

__all__ = ["ChatResult", "Provider", "ProviderError", "make_provider"]


def make_provider(name: str, config: dict) -> Provider:
    provider_cfg = config["providers"].get(name)
    if provider_cfg is None:
        known = ", ".join(sorted(config["providers"]))
        raise ProviderError(f"Unknown provider {name!r}. Configured providers: {known}")
    kind = provider_cfg.get("type", "openai_compat")
    if kind == "anthropic":
        from bartmoss.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(provider_cfg)
    if kind == "mock":
        from bartmoss.providers.mock import MockProvider

        return MockProvider()
    if kind == "openai_compat":
        from bartmoss.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(name, provider_cfg)
    raise ProviderError(f"Provider {name!r} has unknown type {kind!r}")
