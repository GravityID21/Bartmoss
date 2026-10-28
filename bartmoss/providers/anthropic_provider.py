"""Claude models through the official `anthropic` SDK (install with `pip install -e ".[claude]"`)."""

from __future__ import annotations

from typing import Callable, Optional

AGENT_TOOL_FIELDS = ("name", "description", "input_schema")

from bartmoss.providers.base import ChatResult, Provider, ProviderError

# Models that accept server-side refusal fallback in its `"default"` form.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_CREDENTIALS = ("No Claude credentials found. Set ANTHROPIC_API_KEY in your environment, in .env in the "
                  "workspace, or in ~/.config/bartmoss/.env (or run `ant auth login`).")
# Models that take the dynamic-filtering web search tool; every other model uses the basic one.
DYNAMIC_SEARCH_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                         "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6"}
MAX_CONTINUATIONS = 3  # pause_turn resumes allowed per question
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
        web_search: int = 0,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> ChatResult:
        sdk = self._sdk
        params: dict = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is identical for every request in a mode, so cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": to_claude_messages(messages),
        }
        extra_body: dict = {}
        if effort and model.startswith(EFFORT_PREFIXES):
            params["output_config"] = {"effort": effort}
        if self.refusal_fallback and model in FALLBACK_MODELS:
            # On a safety decline the API re-runs the request on a recommended fallback model.
            params["extra_headers"] = {"anthropic-beta": FALLBACK_BETA}
            extra_body["fallbacks"] = "default"
        if web_search:
            tool_type = "web_search_20260209" if model in DYNAMIC_SEARCH_MODELS else "web_search_20250305"
            params["tools"] = [{"type": tool_type, "name": "web_search", "max_uses": web_search}]
        if tools:
            # Inputs stream as they're generated (no server-side buffering); the agent validates them.
            params["tools"] = params.get("tools", []) + [
                {**{k: t[k] for k in AGENT_TOOL_FIELDS}, "eager_input_streaming": True} for t in tools]
            if tool_choice:
                params["tool_choice"] = {"type": tool_choice}
            # Automatic caching moves a breakpoint to the end of the growing conversation each turn.
            extra_body["cache_control"] = {"type": "ephemeral"}
        if extra_body:
            params["extra_body"] = extra_body

        try:
            messages_sent = list(messages)
            parts: list = []
            for _ in range(MAX_CONTINUATIONS + 1):
                with self.client.messages.stream(**{**params, "messages": messages_sent}) as stream:
                    for text in stream.text_stream:
                        if on_text:
                            on_text(text)
                    message = stream.get_final_message()
                parts.append(message)
                if message.stop_reason != "pause_turn":
                    break
                # The server paused its search loop; re-send its turn unchanged and it resumes.
                messages_sent = messages_sent + [{"role": "assistant", "content": message.content}]
        except sdk.AuthenticationError:
            raise ProviderError("Claude rejected the credentials: check ANTHROPIC_API_KEY (or run `ant auth login`).")
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
        except TypeError as e:
            # The SDK raises a bare TypeError when it finds no credentials at all.
            if "authentication" in str(e).lower():
                raise ProviderError(NO_CREDENTIALS)
            raise

        blocks = [block for m in parts for block in m.content]
        text = "".join(block.text for block in blocks if block.type == "text")
        citations = []
        for block in blocks:
            for cite in (getattr(block, "citations", None) or []) if block.type == "text" else []:
                url = getattr(cite, "url", None)
                if url and (getattr(cite, "title", None) or url, url) not in citations:
                    citations.append((getattr(cite, "title", None) or url, url))

        def total(attr: str) -> int:
            return sum(getattr(m.usage, attr, 0) or 0 for m in parts)

        calls = [{"id": b.id, "name": b.name, "input": b.input if isinstance(b.input, dict) else {}}
                 for b in message.content if b.type == "tool_use"]
        searches = sum(getattr(getattr(m.usage, "server_tool_use", None), "web_search_requests", 0) or 0 for m in parts)
        return ChatResult(
            text=text,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=total("input_tokens"),
            output_tokens=total("output_tokens"),
            cache_read_tokens=total("cache_read_input_tokens"),
            cache_write_tokens=total("cache_creation_input_tokens"),
            refused=message.stop_reason == "refusal",
            web_searches=searches,
            citations=citations,
            tool_calls=calls,
            raw=message.content if tools else None,
        )


def to_claude_messages(messages: list[dict]) -> list[dict]:
    """Neutral agent messages -> Messages API messages. Assistant turns from an agent loop are replayed
    from `raw` exactly as received; tool results become tool_result blocks in a user turn, with any
    budget note as a text block after them (history is never edited)."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            blocks = [{"type": "tool_result", "tool_use_id": r["id"], "content": r["content"] or "(no output)",
                       **({"is_error": True} if r.get("is_error") else {})} for r in m["results"]]
            if m.get("note"):
                blocks.append({"type": "text", "text": m["note"]})
            out.append({"role": "user", "content": blocks})
        elif m["role"] == "assistant" and m.get("raw") is not None:
            out.append({"role": "assistant", "content": m["raw"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out
