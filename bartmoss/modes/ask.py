"""/ask: hardware design theory Q&A, multi-turn within a session. `@path` mentions attach
workspace files to the question."""

from __future__ import annotations

import re
from typing import Optional

from bartmoss.context import Context

# @path at the start or after whitespace, so e-mail addresses aren't treated as mentions.
_MENTION = re.compile(r"(?:^|(?<=\s))@([\w./-]+)")
MAX_ATTACHMENTS = 5


def attach_mentions(ctx: Context, question: str) -> str:
    """Append each @-mentioned file as a <file> block. Raises WorkspaceError if one can't be read."""
    # Only path-like mentions (with a dot or slash) count, so "@clk" or "@reset" stays plain text.
    names = list(dict.fromkeys(m.rstrip(".") for m in _MENTION.findall(question)))
    names = [n for n in names if "." in n or "/" in n]
    if len(names) > MAX_ATTACHMENTS:
        raise ValueError(f"at most {MAX_ATTACHMENTS} @file mentions per question")
    blocks = []
    for name in names:
        path, text = ctx.workspace.read_text(name)
        blocks.append(f'<file name="{path.name}">\n{text}</file>')
        ctx.ui.dim(f"  attached {ctx.workspace.relative(path)}")
    return question + ("\n\n" + "\n".join(blocks) if blocks else "")


def run_ask(ctx: Context, question: str) -> Optional[str]:
    ctx.ask_history.append({"role": "user", "content": attach_mentions(ctx, question)})
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
