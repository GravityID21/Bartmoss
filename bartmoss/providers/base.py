"""Provider interface shared by every model backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


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
    ) -> ChatResult:
        """Send one request. `messages` are {"role": "user"|"assistant", "content": str}.
        `on_text` receives streamed text chunks when given. `effort` is ignored by providers
        that don't support it."""
        raise NotImplementedError
