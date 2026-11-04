"""User-defined modes (P6.5): a prompt plus optional checks, dropped into ~/.config/bartmoss/modes/.

Each plugin is a directory named after its command:

    ~/.config/bartmoss/modes/lintfix/
        prompt.md    required: the mode's system prompt (the shared guard rules are prepended)
        mode.json    optional: description, input, output tag and suffix, checks, model settings
        check.py     optional: def check(output: str, source: str) -> list[str]  (problems; [] = ok)

Plugins are loaded only from the user's own config directory (or $BARTMOSS_MODES_DIR), never from
the workspace, so cloning a repository can't add code that runs. The built-in checks ("lint" and
"sections") go through the same sandboxed tools and unsafe-construct check as the built-in modes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bartmoss.config import USER_CONFIG

DEFAULT_DIR = USER_CONFIG.parent / "modes"
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# Commands and subcommands a plugin can't take over.
RESERVED = {"ask", "debug", "tb", "wave", "formal", "flow", "vplan", "uvmplan", "project", "search", "index", "rag",
            "filter", "web", "model", "models", "doctor", "usage", "clear", "save", "load", "quit", "exit", "q",
            "help", "eval", "leaderboard", "compare", "mcp", "plugins", "judge", "guard", "tb_random", "tb_vhdl",
            "debug_agent"}
MODEL_KEYS = ("model", "max_tokens", "effort", "max_retries")
OPTION_KEYS = {"description", "input", "output_tag", "output_suffix", "lint", "sections", *MODEL_KEYS}


class PluginError(ValueError):
    pass


@dataclass
class Plugin:
    name: str
    path: Path
    description: str = ""
    input: str = "file"  # file: a workspace file (plus context files); text: free text, like /ask
    output_tag: Optional[str] = None  # extract this tag from the reply; the whole reply otherwise
    output_suffix: Optional[str] = None  # write <stem><suffix> to the output dir; {ext} is the input's extension
    lint: bool = False  # the output is HDL: refuse unsafe constructs, lint it, send errors back
    sections: list = field(default_factory=list)  # required '# Heading' sections in the output
    settings: dict = field(default_factory=dict)  # model, max_tokens, effort, max_retries defaults
    checker: Optional[Callable[[str, str], list]] = None

    def prompt(self) -> str:
        text = (self.path / "prompt.md").read_text().strip()
        if self.output_tag:
            text += f"\n\nReply with the result inside <{self.output_tag}> tags."
        return text


def plugin_dir() -> Path:
    return Path(os.environ.get("BARTMOSS_MODES_DIR") or DEFAULT_DIR)


def load_plugin(path: Path) -> Plugin:
    name = path.name
    if not NAME.match(name):
        raise PluginError(f"{name}: plugin names are lowercase letters, digits, - and _ (max 32)")
    if name in RESERVED:
        raise PluginError(f"{name}: clashes with a built-in command")
    if not (path / "prompt.md").is_file():
        raise PluginError(f"{name}: no prompt.md")
    options = {}
    if (path / "mode.json").is_file():
        try:
            options = json.loads((path / "mode.json").read_text())
        except json.JSONDecodeError as e:
            raise PluginError(f"{name}: invalid mode.json: {e}")
        unknown = sorted(set(options) - OPTION_KEYS)
        if unknown:
            raise PluginError(f"{name}: unknown mode.json keys {', '.join(unknown)}")
    if options.get("input", "file") not in ("file", "text"):
        raise PluginError(f"{name}: input must be \"file\" or \"text\"")
    tag_name = options.get("output_tag")
    if tag_name is not None and not re.match(r"^[A-Za-z_][\w-]*$", str(tag_name)):
        raise PluginError(f"{name}: output_tag must be a plain tag name")
    suffix = options.get("output_suffix")
    if suffix is not None and (not str(suffix).startswith(".") or "/" in suffix or "\\" in suffix or ".." in suffix):
        raise PluginError(f"{name}: output_suffix must look like .review.md (no directories)")
    checker = None
    if (path / "check.py").is_file():
        spec = importlib.util.spec_from_file_location(f"bartmoss_plugin_{name.replace('-', '_')}", path / "check.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:  # a broken plugin shouldn't stop bartmoss from starting
            raise PluginError(f"{name}: check.py failed to load: {e}")
        checker = getattr(module, "check", None)
        if not callable(checker):
            raise PluginError(f"{name}: check.py has no check(output, source) function")
    return Plugin(name, path, str(options.get("description", "")), options.get("input", "file"), tag_name, suffix,
                  bool(options.get("lint")), list(options.get("sections", [])),
                  {k: options[k] for k in MODEL_KEYS if k in options}, checker)


def discover(root: Optional[Path] = None, warn: Optional[Callable[[str], None]] = None) -> dict[str, Plugin]:
    """Every valid plugin under `root` (default: plugin_dir()); invalid ones are reported and skipped."""
    root = root or plugin_dir()
    plugins = {}
    if not root.is_dir():
        return plugins
    for path in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            plugins[path.name] = load_plugin(path)
        except PluginError as e:
            if warn:
                warn(f"skipping plugin {e}")
    return plugins


def register(config: dict, plugins: dict[str, Plugin]) -> None:
    """Give each plugin a modes.<name> config entry: mode.json's settings, overridden by the user's
    bartmoss.json, so /models and per-mode model choice work as for the built-in modes."""
    from bartmoss.context import PLUGIN_PROMPTS

    for name, plugin in plugins.items():
        config["modes"][name] = {**plugin.settings, **config["modes"].get(name, {})}
        PLUGIN_PROMPTS[name] = plugin


def section_problems(text: str, sections: list) -> list[str]:
    headings = {m.strip() for m in re.findall(r"^#\s+(.+?)\s*$", text, re.M)}
    return [f"missing section '# {s}'" for s in sections if s not in headings]


def lint_problems(output: str, suffix: str, context: list) -> list[str]:
    from bartmoss.sim.tools import lint, tail, unsafe_constructs

    unsafe = unsafe_constructs(output)
    if unsafe:
        return [f"not allowed: {why}" for why in unsafe]
    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_plugin_"))
    target = scratch / f"output{suffix}"
    target.write_text(output)
    files = []
    for path, text in context:
        (scratch / path.name).write_text(text)
        files.append(scratch / path.name)
    run = lint(target, tuple(files))
    if run is None or run.ok:
        return []
    return ["lint errors:\n" + tail(run.output, 40)]


def run_plugin(ctx, plugin: Plugin, arg: str, context_files=()) -> dict:
    """Run one plugin mode. Returns {"status": ok|failed|refused|error, "output", "written", "attempts"}."""
    from bartmoss import training
    from bartmoss.context import context_blocks
    from bartmoss.extract import tag
    from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit

    ui = ctx.ui
    if plugin.input == "file":
        src_path, source = ctx.workspace.read_text(arg)
        context = ctx.read_context_files(context_files, for_file=src_path)
        message = f'<file name="{src_path.name}">\n{source}</file>\n' + context_blocks(context)
        ui.info(f"{plugin.name} for {ctx.workspace.relative(src_path)} · model {ctx.model_spec(plugin.name)}")
    else:
        src_path, source, context = None, arg, []
        message = arg
    suffix = src_path.suffix if src_path else ".sv"

    def check(text: str) -> CheckOutcome:
        output = tag(text, plugin.output_tag) if plugin.output_tag else text.strip() + "\n"
        if output is None:
            return CheckOutcome(False, True, f"Your reply had no <{plugin.output_tag}> section. "
                                             "Reply again in the required format.")
        problems = section_problems(output, plugin.sections)
        lint_checked = False
        if plugin.lint and not problems:
            problems = lint_problems(output, suffix, context)
            lint_checked = True
        if plugin.checker and not problems:
            try:
                problems = [str(p) for p in (plugin.checker(output, source) or [])]
            except Exception as e:
                return CheckOutcome(False, False, data={"output": output, "error": f"check.py raised {e!r}"})
        if problems:
            return CheckOutcome(False, True, "Your result has problems:\n- " + "\n- ".join(problems) +
                                "\nFix every one and reply again in the same format.",
                                data={"output": output, "problems": problems})
        return CheckOutcome(True, data={"output": output, "lint_checked": lint_checked})

    def on_attempt(attempt: int, previous) -> None:
        ui.dim("  asking model..." if attempt == 1 else f"  attempt {attempt}: sending the problems back")

    retries = ctx.config["modes"].get(plugin.name, {}).get("max_retries", 1)
    loop = generate_with_checks(lambda messages: ctx.ask_model(plugin.name, messages, prompt=plugin.name),
                                message, check, retries, on_attempt)
    training.record(ctx, plugin.name, message, loop, plugin.name)
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return {"status": "refused", "output": None, "written": None, "attempts": loop.attempts}
    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    output = data.get("output")
    status = "ok" if loop.outcome and loop.outcome.ok else "error" if data.get("error") else "failed"
    if loop.reply and hit_token_limit(loop.reply.stop_reason):
        ui.warn(f"The reply hit max_tokens and may be incomplete; raise modes.{plugin.name}.max_tokens.")
    written = None
    if output and src_path and plugin.output_suffix:
        name = src_path.stem + plugin.output_suffix.replace("{ext}", src_path.suffix)
        written = ctx.workspace.write_text(name, output, ctx.confirm_overwrite)
    elif output:
        ui.print()
        ui.print(output.rstrip())
        ui.print()
    if written:
        ui.ok(f"{plugin.name} output written to {ctx.workspace.relative(written)}")
    if status == "error":
        ui.error(f"plugin {plugin.name}: {data['error']}")
    elif status == "failed":
        ui.warn("checks still fail after the retries: " + "; ".join(data.get("problems", ["no output"]))[:600])
    return {"status": status, "output": output, "written": written, "attempts": loop.attempts}
