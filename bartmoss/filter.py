"""Optional pre-filter for /ask: a cheap model labels each question IN_SCOPE, OFF_TOPIC or HARMFUL
before the main model sees it. Off by default (config "filter.enabled" or /filter on)."""

from __future__ import annotations

import re

from bartmoss.config import parse_model_spec
from bartmoss.context import Context, PROMPT_DIR
from bartmoss.providers import ProviderError

LABELS = ("IN_SCOPE", "OFF_TOPIC", "HARMFUL")
REPLIES = {
    "OFF_TOPIC": "I only handle hardware design and verification topics. Try asking about RTL, timing, "
                 "verification or EDA tools.",
    "HARMFUL": "I can't help with that request.",
}


def classify(ctx: Context, question: str) -> str:
    """Label a question. Fails open: if the filter model errors or replies with something
    unexpected, the question is treated as IN_SCOPE and a warning is shown."""
    spec = ctx.config["filter"]["model"]
    provider_name, model = parse_model_spec(spec)
    system = "MODE: filter\n\n" + (PROMPT_DIR / "filter.md").read_text().strip() + "\n"
    try:
        result = ctx.provider(provider_name).chat(
            system,
            [{"role": "user", "content": f"<question>\n{question}\n</question>"}],
            model=model,
            # Small budget, but room for models that think before answering.
            max_tokens=ctx.config["filter"].get("max_tokens", 1024),
            effort="low",
        )
    except ProviderError as e:
        ctx.ui.warn(f"filter unavailable ({e}); answering without it")
        return "IN_SCOPE"
    ctx.usage.add(spec, result)
    match = re.search(r"\b(IN_SCOPE|OFF_TOPIC|HARMFUL)\b", result.text.upper())
    if not match:
        ctx.ui.warn("filter gave no label; answering without it")
        return "IN_SCOPE"
    return match.group(1)
