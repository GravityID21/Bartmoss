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


def system_prompt(name: str) -> str:
    """Shared guard rules + a mode prompt. `name` is a mode ("debug") or a variant of one
    ("debug.v2" -> prompts/debug.v2.md); variants keep the base mode's MODE line.
    Kept byte-stable so it can be prompt-cached."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise ValueError(f"unknown prompt {name!r}: no {path.name} in {PROMPT_DIR}")
    guard = (PROMPT_DIR / "guard.md").read_text()
    return f"{guard.strip()}\n\nMODE: {name.split('.')[0]}\n\n{path.read_text().strip()}\n"


@dataclass
class Context:
    config: dict
    workspace: Workspace
    ui: UI
    usage: Usage
    model_override: Optional[str] = None
    assume_yes: bool = False
    ask_history: list = field(default_factory=list)  # replaced wholesale by /load
    web_search: bool = False  # /web on|off; allows Claude web searches in /ask
    use_filter: Optional[bool] = None  # /filter on|off; None means use config["filter"]["enabled"]
    prompt_overrides: dict = field(default_factory=dict)  # mode -> prompt variant, e.g. {"debug": "debug.v2"}
    _providers: dict = field(default_factory=dict)
    _project: Optional[object] = None

    def model_spec(self, mode: str) -> str:
        return model_for_mode(self.config, mode, self.model_override)

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = make_provider(name, self.config)
        return self._providers[name]

    def ask_model(self, mode: str, messages: list[dict], on_text: Optional[Callable[[str], None]] = None,
                  prompt: Optional[str] = None, web_search: int = 0, max_tokens: Optional[int] = None,
                  tools: Optional[list] = None, tool_choice: Optional[str] = None) -> ChatResult:
        """`prompt` picks a prompt explicitly (e.g. tb_vhdl); otherwise a variant set with
        `eval --prompt` or `modes.<mode>.prompt` in config, else the mode's own prompt.
        Model and limits come from `mode`, unless `max_tokens` overrides the cap."""
        spec = self.model_spec(mode)
        provider_name, model = parse_model_spec(spec)
        mode_cfg = self.config["modes"].get(mode, {})
        name = prompt or self.prompt_overrides.get(mode) or mode_cfg.get("prompt") or mode
        result = self.provider(provider_name).chat(
            system_prompt(name),
            messages,
            model=model,
            max_tokens=max_tokens or mode_cfg.get("max_tokens", 8000),
            effort=mode_cfg.get("effort"),
            on_text=on_text,
            web_search=web_search,
            **({"tools": tools, "tool_choice": tool_choice} if tools else {}),
        )
        self.usage.add(spec, result)
        return result

    def project(self):
        """The workspace's project index (P4.2), built once per session."""
        if self._project is None:
            from bartmoss.project import Project

            cfg = self.config.get("project", {})
            self._project = Project(self.workspace, cfg.get("filelist"), cfg.get("max_files", 500))
        return self._project

    def read_context_files(self, paths, for_file: Optional[Path] = None) -> list[tuple[Path, str]]:
        """Supporting files (packages, interfaces, submodules): shown to the model read-only
        and included in every lint and simulation run. With no explicit files, `for_file`'s
        dependencies from the project index are used (config project.auto_context)."""
        paths = list(paths)
        cfg = self.config.get("project", {})
        from bartmoss.project import HDL_SUFFIXES

        if not paths and for_file is not None and cfg.get("auto_context", True) and \
                for_file.suffix.lower() in HDL_SUFFIXES:
            deps = self.project().dependencies(for_file)
            limit = cfg.get("max_context_files", 20)
            if len(deps) > limit:
                self.ui.warn(f"{for_file.name} depends on {len(deps)} files; using the first {limit} "
                             "(raise project.max_context_files or pass --with)")
                deps = deps[:limit]
            if deps:
                self.ui.dim("  auto context: " + ", ".join(self.workspace.relative(p) for p in deps) +
                            " (project index; --with overrides it, --no-auto-context turns it off)")
            paths = deps
        return [self.workspace.read_text(p) for p in paths]

    def confirm_overwrite(self, path: str, diff: str) -> bool:
        if self.assume_yes:
            return True
        self.ui.dim("\n".join(diff.splitlines()[:40]))
        return self.ui.confirm(f"{path} exists and differs. Overwrite?")
