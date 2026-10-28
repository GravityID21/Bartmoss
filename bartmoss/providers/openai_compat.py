"""Any OpenAI-compatible chat endpoint through the `openai` SDK (install with `pip install -e ".[openai]"`).

Covers OpenAI itself plus DeepSeek, OpenRouter, Gemini's compatibility endpoint, and local
servers such as Ollama, LM Studio and vLLM. Endpoints are configured in config.DEFAULTS["providers"].
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

from bartmoss.providers.base import ChatResult, Provider, ProviderError


class OpenAICompatProvider(Provider):
    def __init__(self, name: str, config: dict):
        try:
            import openai
        except ImportError:
            raise ProviderError(f'The {name} provider needs the SDK: pip install -e ".[openai]"')
        self._sdk = openai
        self.name = name
        key_env = config.get("api_key_env")
        api_key = os.environ.get(key_env) if key_env else "not-needed"
        if not api_key:
            raise ProviderError(f"Set {key_env} to use the {name} provider.")
        self.client = openai.OpenAI(api_key=api_key, base_url=config.get("base_url"), max_retries=2)
        self.max_tokens_param = config.get("max_tokens_param", "max_tokens")
        self.stream_usage = config.get("stream_usage", True)

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
            "messages": [{"role": "system", "content": system}] + to_openai_messages(messages),
            "stream": True,
            self.max_tokens_param: max_tokens,
        }
        if self.stream_usage:
            params["stream_options"] = {"include_usage": True}
        if tools:
            params["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                 "parameters": t["input_schema"]}} for t in tools]
            if tool_choice:
                params["tool_choice"] = tool_choice

        parts: list[str] = []
        calls: dict = {}  # stream index -> {"id", "name", "arguments"}
        finish_reason = None
        input_tokens = output_tokens = 0
        try:
            for chunk in self.client.chat.completions.create(**params):
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens or 0
                    output_tokens = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta and choice.delta.content:
                    parts.append(choice.delta.content)
                    if on_text:
                        on_text(choice.delta.content)
                for delta in (choice.delta.tool_calls or []) if choice.delta else []:
                    call = calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = delta.id or call["id"]
                    if delta.function:
                        call["name"] += delta.function.name or ""
                        call["arguments"] += delta.function.arguments or ""
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except sdk.AuthenticationError:
            raise ProviderError(f"{self.name} rejected the API key.")
        except sdk.NotFoundError:
            raise ProviderError(f"{self.name} doesn't know model {model!r}.")
        except sdk.RateLimitError:
            raise ProviderError(f"{self.name} rate limit hit; wait and try again.")
        except sdk.APIStatusError as e:
            raise ProviderError(f"{self.name} API error {e.status_code}: {e.message}")
        except sdk.APIConnectionError:
            raise ProviderError(f"Could not reach {self.name}; is the server running and the base_url right?")

        tool_calls = []
        for index in sorted(calls):
            call = calls[index]
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except ValueError:
                arguments = None  # invalid JSON: the agent reports it back as a tool error
            tool_calls.append({"id": call["id"] or f"call_{index}", "name": call["name"],
                               "input": arguments if isinstance(arguments, dict) else {"_invalid_json": call["arguments"]}})
        return ChatResult(
            text="".join(parts),
            model=model,
            stop_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            refused=finish_reason == "content_filter",
            tool_calls=tool_calls,
        )


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Neutral agent messages -> Chat Completions messages."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"] or "(no output)"} for r in m["results"]]
            if m.get("note"):
                out.append({"role": "user", "content": m["note"]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": c["id"], "type": "function",
                                        "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
                                       for c in m["tool_calls"]]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out
