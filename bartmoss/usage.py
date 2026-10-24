"""Token and cost tracking for a session."""

from __future__ import annotations

from dataclasses import dataclass, field

from bartmoss.providers.base import ChatResult

# Providers that run locally (or not at all) and never bill per token.
FREE_PROVIDERS = {"mock", "ollama"}


@dataclass
class Usage:
    prices: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def add(self, spec: str, result: ChatResult) -> None:
        self.calls.append((spec, result))

    def cost(self, spec: str, result: ChatResult) -> float | None:
        if spec.split(":", 1)[0] in FREE_PROVIDERS:
            return 0.0
        price = self.prices.get(spec)
        if not price:
            return None
        price_in, price_out = price
        # Cache writes bill at ~1.25x input, cache reads at ~0.1x input (Anthropic pricing).
        return (
            result.input_tokens * price_in
            + result.cache_write_tokens * price_in * 1.25
            + result.cache_read_tokens * price_in * 0.1
            + result.output_tokens * price_out
        ) / 1_000_000

    def totals(self) -> dict:
        totals = {"calls": len(self.calls), "input": 0, "output": 0, "cache_read": 0, "cost": 0.0, "cost_known": True}
        for spec, result in self.calls:
            totals["input"] += result.input_tokens + result.cache_write_tokens
            totals["output"] += result.output_tokens
            totals["cache_read"] += result.cache_read_tokens
            cost = self.cost(spec, result)
            if cost is None:
                totals["cost_known"] = False
            else:
                totals["cost"] += cost
        return totals

    def summary(self) -> str:
        t = self.totals()
        cost = f"${t['cost']:.4f}" + ("" if t["cost_known"] else " (some models unpriced)")
        return (
            f"{t['calls']} calls · {t['input']:,} in · {t['output']:,} out · "
            f"{t['cache_read']:,} cache-read · est. {cost}"
        )
