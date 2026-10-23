"""Pull structured pieces out of model replies.

Modes ask for XML-style tags (<fixed_file>...</fixed_file>) because they survive nested
Markdown better than code fences. Fenced blocks are the fallback for models that ignore the tags.
"""

from __future__ import annotations

import re
from typing import Optional

_FENCE = re.compile(r"```([\w+-]*)[^\n]*\n(.*?)```", re.S)
HDL_LANGS = ("systemverilog", "verilog", "sv", "v", "vhdl", "vhd")


def code_blocks(text: str) -> list[tuple[str, str]]:
    return [(lang.lower(), body) for lang, body in _FENCE.findall(text)]


def strip_fences(text: str) -> str:
    """Remove a single wrapping code fence if the model added one inside a tag."""
    stripped = text.strip("\n")
    match = re.fullmatch(r"\s*```[^\n]*\n(.*?)\n?```\s*", stripped, re.S)
    return match.group(1) if match else stripped


def tag(text: str, name: str) -> Optional[str]:
    match = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    if match:
        return strip_fences(match.group(1)).strip("\n") + "\n"
    return None


def hdl_block(text: str) -> Optional[str]:
    """Largest fenced HDL block, or the largest block of any language if none is tagged as HDL."""
    blocks = code_blocks(text)
    if not blocks:
        return None
    hdl = [body for lang, body in blocks if lang in HDL_LANGS]
    return max(hdl or [body for _, body in blocks], key=len)


def top_module(source: str) -> Optional[str]:
    """First module/entity name in an HDL file (comments ignored)."""
    no_comments = re.sub(r"//.*?$|/\*.*?\*/", "", source, flags=re.S | re.M)
    match = re.search(r"^\s*module\s+(?:automatic\s+|static\s+)?(\w+)", no_comments, re.M)
    if match:
        return match.group(1)
    match = re.search(r"^\s*entity\s+(\w+)\s+is", no_comments, re.M | re.I)
    return match.group(1) if match else None
