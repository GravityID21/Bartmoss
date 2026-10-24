"""Save and restore the /ask conversation as JSON (REPL commands /save and /load)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from bartmoss.config import parse_model_spec
from bartmoss.context import Context
from bartmoss.files import WorkspaceError

FORMAT_VERSION = 1


def save_session(ctx: Context, name: Optional[str] = None) -> Optional[Path]:
    """Write the conversation into the output dir. Returns the path, or None if the user declined
    to overwrite an existing file."""
    name = name or f"session-{datetime.now():%Y%m%d-%H%M%S}.json"
    if not name.endswith(".json"):
        name += ".json"
    data = {
        "bartmoss_session": FORMAT_VERSION,
        "saved": datetime.now().isoformat(timespec="seconds"),
        "model": ctx.model_override,
        "ask_history": ctx.ask_history,
    }
    return ctx.workspace.write_text(name, json.dumps(data, indent=2) + "\n", ctx.confirm_overwrite)


def _validate_history(history) -> list:
    if not isinstance(history, list):
        raise ValueError("ask_history must be a list")
    for i, message in enumerate(history):
        expected = "user" if i % 2 == 0 else "assistant"
        if not isinstance(message, dict) or message.get("role") != expected or not isinstance(message.get("content"), str):
            raise ValueError(f"message {i} must be a {expected} message with text content")
    if history and history[-1]["role"] != "assistant":
        raise ValueError("the conversation must end with an assistant reply")
    return history


def load_session(ctx: Context, path: str) -> int:
    """Replace the current conversation with a saved one. Looks in the workspace first, then in
    the output dir. Returns the number of messages loaded."""
    try:
        _, text = ctx.workspace.read_text(path)
    except WorkspaceError:
        _, text = ctx.workspace.read_text(Path(ctx.workspace.output_dir) / path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict) or data.get("bartmoss_session") != FORMAT_VERSION:
        raise ValueError(f"{path} is not a Bartmoss session file (version {FORMAT_VERSION})")
    history = _validate_history(data.get("ask_history"))
    model = data.get("model")
    if model:
        parse_model_spec(model)
    ctx.ask_history = list(history)
    ctx.model_override = model or ctx.model_override
    return len(history)
