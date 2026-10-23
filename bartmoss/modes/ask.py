"""/ask: hardware design theory Q&A, multi-turn within a session."""

from __future__ import annotations

from typing import Optional

from bartmoss.context import Context


def run_ask(ctx: Context, question: str) -> Optional[str]:
    ctx.ask_history.append({"role": "user", "content": question})
    ctx.ui.print()
    try:
        result = ctx.ask_model("ask", ctx.ask_history, on_text=ctx.ui.stream)
    except Exception:
        ctx.ask_history.pop()  # keep history valid (alternating roles) after a failed call
        raise
    ctx.ui.print("\n")
    if result.refused:
        ctx.ask_history.pop()
        ctx.ui.warn("The model declined this request. Try rephrasing, or switch model with /model.")
        return None
    ctx.ask_history.append({"role": "assistant", "content": result.text})
    if result.stop_reason in ("max_tokens", "length"):
        ctx.ui.warn("Answer was cut off at the max_tokens limit for /ask.")
    return result.text
