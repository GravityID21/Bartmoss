"""Provider interface shared by every model backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ChatResult:
    text: str
    model: str
    stop_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    refused: bool = False
    web_searches: int = 0  # server-side searches performed (billed per search)
    citations: list = field(default_factory=list)  # [(title, url)] from web search results
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "input"}] requested by the model
    raw: Any = None  # the provider's own assistant content, replayed unchanged in agent loops


class ProviderError(Exception):
    """A provider failure with a message that is safe and useful to show the user."""


class Provider:
    name = "base"

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
        """Send one request. `messages` are {"role": "user"|"assistant", "content": str}.
        `on_text` receives streamed text chunks when given. `effort` is ignored by providers
        that don't support it. `web_search` > 0 allows up to that many server-side web searches
        (Claude only; other providers ignore it).

        Agent loops (P4.1) pass `tools` ([{"name", "description", "input_schema"}]) and two more
        message shapes: an assistant turn {"role": "assistant", "content": text, "tool_calls": [...],
        "raw": ChatResult.raw}, replayed unchanged so thinking blocks and caching stay valid, and
        {"role": "tool", "results": [{"id", "content", "is_error"}], "note": optional text after
        the results}. `tool_choice` "none" asks for a final answer without tool calls."""
        raise NotImplementedError
