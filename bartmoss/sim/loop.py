"""Generate -> check -> feed errors back -> retry, up to a fixed number of attempts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from bartmoss.providers.base import ChatResult


@dataclass
class CheckOutcome:
    ok: bool
    retryable: bool = False
    feedback: str = ""  # sent back to the model when retrying
    data: dict = field(default_factory=dict)


def hit_token_limit(stop_reason) -> bool:
    return stop_reason in ("max_tokens", "length")


@dataclass
class LoopResult:
    reply: Optional[ChatResult]
    outcome: Optional[CheckOutcome]
    attempts: int
    refused: bool = False


def generate_with_checks(
    ask: Callable[[list[dict]], ChatResult],
    first_message: str,
    check: Callable[[str], CheckOutcome],
    max_retries: int,
    on_attempt: Optional[Callable[[int, Optional[CheckOutcome]], Any]] = None,
) -> LoopResult:
    """`ask(messages)` performs one model call. History is append-only so prompt caching keeps working."""
    messages = [{"role": "user", "content": first_message}]
    reply: Optional[ChatResult] = None
    outcome: Optional[CheckOutcome] = None
    for attempt in range(1, max_retries + 2):
        if on_attempt:
            on_attempt(attempt, outcome)
        reply = ask(messages)
        if reply.refused:
            return LoopResult(reply, None, attempt, refused=True)
        messages.append({"role": "assistant", "content": reply.text})
        outcome = check(reply.text)
        if outcome.ok or not outcome.retryable:
            return LoopResult(reply, outcome, attempt)
        messages.append({"role": "user", "content": outcome.feedback})
    return LoopResult(reply, outcome, max_retries + 1)
