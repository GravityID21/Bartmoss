"""Shared state for one CLI session: config, workspace, UI, usage and provider clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bartmoss.config import model_for_mode, parse_model_spec
from bartmoss.files import Workspace
from bartmoss.providers import ChatResult, Provider, make_provider
from bartmoss.ui import UI
from bartmoss.usage import Usage

PROMPT_DIR = Path(__file__).parent / "prompts"


def context_blocks(files: list[tuple[Path, str]]) -> str:
    return "".join(f'\n<file name="{p.name}" role="context">\n{text}</file>\n' for p, text in files)


def system_prompt(mode: str) -> str:
    """Shared guard rules + the mode's prompt. Kept byte-stable so it can be prompt-cached."""
    guard = (PROMPT_DIR / "guard.md").read_text()
    body = (PROMPT_DIR / f"{mode}.md").read_text()
    return f"{guard.strip()}\n\nMODE: {mode}\n\n{body.strip()}\n"


@dataclass
class Context:
    config: dict
    workspace: Workspace
    ui: UI
    usage: Usage
    model_override: Optional[str] = None
    assume_yes: bool = False
    ask_history: list = field(default_factory=list)
    _providers: dict = field(default_factory=dict)

    def model_spec(self, mode: str) -> str:
        return model_for_mode(self.config, mode, self.model_override)

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = make_provider(name, self.config)
        return self._providers[name]

    def ask_model(self, mode: str, messages: list[dict], on_text: Optional[Callable[[str], None]] = None,
                  prompt: Optional[str] = None) -> ChatResult:
        """`prompt` picks a prompt variant (e.g. tb_vhdl); model and limits still come from `mode`."""
        spec = self.model_spec(mode)
        provider_name, model = parse_model_spec(spec)
        mode_cfg = self.config["modes"].get(mode, {})
        result = self.provider(provider_name).chat(
            system_prompt(prompt or mode),
            messages,
            model=model,
            max_tokens=mode_cfg.get("max_tokens", 8000),
            effort=mode_cfg.get("effort"),
            on_text=on_text,
        )
        self.usage.add(spec, result)
        return result

    def read_context_files(self, paths) -> list[tuple[Path, str]]:
        """Supporting files (packages, interfaces, submodules): shown to the model read-only
        and included in every lint and simulation run."""
        return [self.workspace.read_text(p) for p in paths]

    def confirm_overwrite(self, path: str, diff: str) -> bool:
        if self.assume_yes:
            return True
        self.ui.dim("\n".join(diff.splitlines()[:40]))
        return self.ui.confirm(f"{path} exists and differs. Overwrite?")
