"""Command-line entry point: one-shot subcommands and the interactive REPL."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from bartmoss import __version__
from bartmoss.config import load_config, parse_model_spec
from bartmoss.context import Context
from bartmoss.files import Workspace, WorkspaceError
from bartmoss.providers import ProviderError
from bartmoss.sim.tools import INSTALL_HINTS, available
from bartmoss.ui import UI
from bartmoss.usage import Usage

HELP = """\
  <question>          ask a hardware design question (same as /ask)
  /ask <question>     theory Q&A, remembers the conversation until /clear
  /debug <file> [ctx...]  review + fix one RTL file (writes <name>.fixed.<ext>);
                          extra files (packages, interfaces, submodules) are read-only context
  /tb <file> [ctx...]     generate + run a self-checking testbench
  /model [p:model]    show or set the model for this session (e.g. anthropic:claude-haiku-5-5)
  /models             list configured providers and per-mode models
  /doctor             check simulators and provider SDKs
  /usage              tokens and estimated cost so far
  /clear              forget the /ask conversation
  /quit               exit"""


def build_context(args: argparse.Namespace) -> Context:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    config = load_config(root)
    if args.out:
        config["output_dir"] = args.out
    if args.model:
        parse_model_spec(args.model)  # validate early
    ui = UI(color=False if args.no_color else None)
    return Context(
        config=config,
        workspace=Workspace(root, config["output_dir"]),
        ui=ui,
        usage=Usage(prices=config.get("prices", {})),
        model_override=args.model,
        assume_yes=args.yes,
    )


def cmd_doctor(ctx: Context) -> None:
    ui = ctx.ui
    ui.print(ui.style("Simulators / tools", "bold"))
    for name, path in available().items():
        if path:
            ui.ok(f"{name:10} {path}")
        else:
            ui.dim(f"  {name:10} not found · {INSTALL_HINTS[name]}")
    ui.print(ui.style("Provider SDKs", "bold"))
    for module, extra in (("anthropic", "claude"), ("openai", "openai")):
        try:
            __import__(module)
            ui.ok(f"{module:10} installed")
        except ImportError:
            ui.dim(f'  {module:10} not installed · pip install -e ".[{extra}]"')


def cmd_models(ctx: Context) -> None:
    ui = ctx.ui
    ui.print(ui.style("Per-mode models", "bold"))
    for mode in ctx.config["modes"]:
        ui.print(f"  {mode:6} {ctx.model_spec(mode)}")
    ui.print(ui.style("Configured providers", "bold"))
    for name, cfg in ctx.config["providers"].items():
        where = cfg.get("base_url") or ""
        ui.print(f"  {name:11} {cfg.get('type', 'openai_compat'):14} {where}")


def dispatch(ctx: Context, line: str) -> bool:
    """Handle one REPL line. Returns False when the user wants to quit."""
    line = line.strip()
    if not line:
        return True
    if not line.startswith("/"):
        line = "/ask " + line
    command, _, rest = line.partition(" ")
    rest = rest.strip()
    ui = ctx.ui

    if command in ("/quit", "/exit", "/q"):
        return False
    if command == "/help":
        ui.print(HELP)
    elif command == "/ask":
        from bartmoss.modes.ask import run_ask

        run_ask(ctx, rest) if rest else ui.warn("usage: /ask <question>")
    elif command in ("/debug", "/tb"):
        args = shlex.split(rest)
        if not args:
            ui.warn(f"usage: {command} <file> [context files...]")
        elif command == "/debug":
            from bartmoss.modes.debug import run_debug

            run_debug(ctx, args[0], context_files=args[1:])
        else:
            from bartmoss.modes.tb import run_tb

            run_tb(ctx, args[0], context_files=args[1:])
    elif command == "/model":
        if rest:
            parse_model_spec(rest)
            ctx.model_override = rest
            ctx.ask_history.clear()
            ui.ok(f"model set to {rest} for all modes (conversation cleared)")
        else:
            ui.print(f"  {ctx.model_override or 'per-mode defaults (see /models)'}")
    elif command == "/models":
        cmd_models(ctx)
    elif command == "/doctor":
        cmd_doctor(ctx)
    elif command == "/usage":
        ui.print("  " + ctx.usage.summary())
    elif command == "/clear":
        ctx.ask_history.clear()
        ui.ok("conversation cleared")
    else:
        ui.warn(f"unknown command {command}; try /help")
    return True


def repl(ctx: Context) -> int:
    ctx.ui.banner()
    try:
        import readline  # noqa: F401  (line editing + history where available)
    except ImportError:
        pass
    prompt = ctx.ui.style("bartmoss", "magenta") + ctx.ui.style(" ❯ ", "cyan")
    while True:
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            ctx.ui.print()
            break
        try:
            if not dispatch(ctx, line):
                break
        except (ProviderError, WorkspaceError, ValueError) as e:
            ctx.ui.error(str(e))
        except KeyboardInterrupt:
            ctx.ui.warn("interrupted")
    if ctx.usage.calls:
        ctx.ui.dim("session: " + ctx.usage.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bartmoss", description="AI assistant for RTL design and verification.")
    parser.add_argument("--version", action="version", version=f"bartmoss {__version__}")
    parser.add_argument("--model", help="provider:model for every mode, e.g. anthropic:claude-haiku-5-5 or mock:echo")
    parser.add_argument("--root", help="workspace root (default: current directory); files outside it are refused")
    parser.add_argument("--out", help="output directory inside the workspace (default: bartmoss_out)")
    parser.add_argument("--yes", "-y", action="store_true", help="overwrite existing output files without asking")
    parser.add_argument("--no-color", action="store_true", help="disable colours (NO_COLOR also works)")
    sub = parser.add_subparsers(dest="command")
    p_ask = sub.add_parser("ask", help="ask a hardware design question")
    p_ask.add_argument("question", nargs="+")
    for name, help_text in (("debug", "review and fix one RTL file"), ("tb", "generate and run a testbench for one RTL file")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file")
        p.add_argument("--with", dest="context", action="append", default=[], metavar="FILE",
                       help="supporting file (package, interface, submodule); repeatable")
    p_eval = sub.add_parser("eval", help="score a model on the eval cases")
    p_eval.add_argument("--mode", choices=["debug", "tb"], required=True)
    p_eval.add_argument("--cases", default="evals/cases", help="cases directory (default: evals/cases)")
    p_eval.add_argument("--only", help="run only cases whose name contains this text")
    sub.add_parser("doctor", help="check simulators and provider SDKs")
    sub.add_parser("models", help="show configured providers and models")
    args = parser.parse_args(argv)

    try:
        ctx = build_context(args)
    except ValueError as e:
        parser.error(str(e))

    try:
        if args.command is None:
            return repl(ctx)
        if args.command == "ask":
            from bartmoss.modes.ask import run_ask

            return 0 if run_ask(ctx, " ".join(args.question)) is not None else 1
        if args.command == "debug":
            from bartmoss.modes.debug import run_debug

            return 0 if run_debug(ctx, args.file, context_files=args.context).status in ("clean", "unchecked") else 1
        if args.command == "tb":
            from bartmoss.modes.tb import run_tb

            return 0 if run_tb(ctx, args.file, context_files=args.context).verdict in ("pass", "unchecked") else 1
        if args.command == "eval":
            from bartmoss.evals import run_eval

            run_eval(ctx, args.mode, Path(args.cases), args.only)
            return 0
        if args.command == "doctor":
            cmd_doctor(ctx)
        elif args.command == "models":
            cmd_models(ctx)
        return 0
    except (ProviderError, WorkspaceError) as e:
        ctx.ui.error(str(e))
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if args.command in ("ask", "debug", "tb", "eval") and ctx.usage.calls:
            ctx.ui.dim(ctx.usage.summary())


if __name__ == "__main__":
    sys.exit(main())
