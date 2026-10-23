"""Claude models through the official `anthropic` SDK (install with `pip install -e ".[claude]"`)."""

from __future__ import annotations

from typing import Callable, Optional

from bartmoss.providers.base import ChatResult, Provider, ProviderError

# Models that accept server-side refusal fallback in its `"default"` form.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Model families that accept output_config.effort (it errors on Haiku 4.5 / Sonnet 4.5).
EFFORT_PREFIXES = ("claude-opus-", "claude-fable-", "claude-mythos-", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, config: dict):
        try:
            import anthropic
        except ImportError:
            raise ProviderError('The Claude provider needs the SDK: pip install -e ".[claude]"')
        self._sdk = anthropic
        self.client = anthropic.Anthropic(max_retries=2)
        self.refusal_fallback = config.get("refusal_fallback", True)

    def chat(
        self,
        system: str,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        effort: Optional[str] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ) -> ChatResult:
        sdk = self._sdk
        params: dict = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is identical for every request in a mode, so cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if effort and model.startswith(EFFORT_PREFIXES):
            params["output_config"] = {"effort": effort}
        if self.refusal_fallback and model in FALLBACK_MODELS:
            # On a safety decline the API re-runs the request on a recommended fallback model.
            params["extra_headers"] = {"anthropic-beta": FALLBACK_BETA}
            params["extra_body"] = {"fallbacks": "default"}

        try:
            with self.client.messages.stream(**params) as stream:
                for text in stream.text_stream:
                    if on_text:
                        on_text(text)
                message = stream.get_final_message()
        except sdk.AuthenticationError:
            raise ProviderError("Claude rejected the credentials: set ANTHROPIC_API_KEY (or run `ant auth login`).")
        except sdk.NotFoundError:
            raise ProviderError(f"Unknown Claude model: {model}")
        except sdk.RateLimitError:
            raise ProviderError("Claude rate limit hit; wait a minute and try again.")
        except sdk.BadRequestError as e:
            raise ProviderError(f"Claude rejected the request: {e.message}")
        except sdk.APIStatusError as e:
            raise ProviderError(f"Claude API error {e.status_code}: {e.message}")
        except sdk.APIConnectionError:
            raise ProviderError("Could not reach the Claude API; check your connection.")
        except sdk.AnthropicError as e:
            raise ProviderError(f"Claude client error: {e}")

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return ChatResult(
            text=text,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            refused=message.stop_reason == "refusal",
        )
