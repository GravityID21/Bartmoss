"""Command-line entry point: one-shot subcommands and the interactive REPL."""

from __future__ import annotations

import argparse
import os
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
  /ask <question>     theory Q&A, remembers the conversation until /clear;
                      @path attaches a workspace file (e.g. "why is @rtl/fifo.sv full early?")
  /debug <file> [ctx...]  review + fix one RTL file (writes <name>.fixed.<ext>);
                          extra files (packages, interfaces, submodules) are read-only context
  /tb <file> [ctx...]     generate + run a self-checking testbench
  /vplan <spec> [ctx...]  verification plan: features, checks, coverage, edge cases (Markdown)
  /uvmplan <spec> [ctx...] UVM environment architecture: agents, sequences, scoreboard, tests
  /filter [on|off]    pre-check /ask questions with a cheap model (off by default)
  /web [on|off]       allow Claude to search the web in /ask (off by default; billed per search)
  /model [p:model]    show or set the model for this session (e.g. anthropic:claude-haiku-5-5)
  /models             list configured providers and per-mode models
  /doctor             check simulators and provider SDKs
  /usage              tokens and estimated cost so far
  /clear              forget the /ask conversation
  /save [name]        save the /ask conversation to the output dir as JSON
  /load <file>        restore a saved /ask conversation (and its model)
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
    elif command in ("/vplan", "/uvmplan"):
        from bartmoss.modes.plan import run_plan

        args = shlex.split(rest)
        if not args:
            ui.warn(f"usage: {command} <spec, README or RTL file> [context files...]")
        else:
            run_plan(ctx, command[1:], args[0], context_files=args[1:])
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
    elif command == "/filter":
        if rest in ("on", "off"):
            ctx.use_filter = rest == "on"
        elif rest:
            ui.warn("usage: /filter [on|off]")
        enabled = ctx.config["filter"]["enabled"] if ctx.use_filter is None else ctx.use_filter
        ui.print(f"  /ask pre-filter is {'on' if enabled else 'off'} · model {ctx.config['filter']['model']}")
    elif command == "/web":
        if rest in ("on", "off"):
            ctx.web_search = rest == "on"
        elif rest:
            ui.warn("usage: /web [on|off]")
        ui.print(f"  web search for /ask is {'on' if ctx.web_search else 'off'}")
    elif command == "/models":
        cmd_models(ctx)
    elif command == "/doctor":
        cmd_doctor(ctx)
    elif command == "/usage":
        ui.print("  " + ctx.usage.summary())
    elif command == "/save":
        from bartmoss.session import save_session

        if not ctx.ask_history:
            ui.warn("nothing to save yet; ask something first")
        else:
            path = save_session(ctx, rest or None)
            if path:
                ui.ok(f"saved {len(ctx.ask_history)} messages to {ctx.workspace.relative(path)}")
    elif command == "/load":
        from bartmoss.session import load_session

        if not rest:
            ui.warn("usage: /load <file>")
        else:
            count = load_session(ctx, rest)
            ui.ok(f"loaded {count} messages" + (f" · model {ctx.model_override}" if ctx.model_override else ""))
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
    p_ask.add_argument("--web", action="store_true", help="allow Claude to search the web (billed per search)")
    for name, help_text in (("debug", "review and fix one RTL file"), ("tb", "generate and run a testbench for one RTL file"),
                            ("vplan", "write a verification plan from a spec, README or RTL file"),
                            ("uvmplan", "design a UVM environment from a spec, README or RTL file")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file")
        p.add_argument("--with", dest="context", action="append", default=[], metavar="FILE",
                       help="supporting file (package, interface, submodule); repeatable")
    p_eval = sub.add_parser("eval", help="score a model on the eval cases")
    p_eval.add_argument("--mode", choices=["debug", "tb", "vplan", "uvmplan"], required=True)
    p_eval.add_argument("--cases", default="evals/cases", help="cases directory (default: evals/cases)")
    p_eval.add_argument("--only", help="run only cases whose name contains this text")
    p_eval.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="run each case N times and report mean, min/max and full passes (default 1)")
    p_eval.add_argument("--prompt", metavar="NAME", help="prompt variant to test, e.g. debug.v2 (prompts/debug.v2.md)")
    p_eval.add_argument("--judge", metavar="PROVIDER:MODEL", help="judge model for vplan/uvmplan rubrics")
    p_lb = sub.add_parser("leaderboard", help="summarise saved eval results by model")
    p_lb.add_argument("--results", default="evals/results", help="results directory (default: evals/results)")
    p_lb.add_argument("--write", metavar="FILE", help="also write the Markdown table to FILE")
    p_lb.add_argument("--trend", action="store_true", help="list every saved full run in date order instead")
    p_cmp = sub.add_parser("compare", help="compare two eval result files case by case")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
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

            ctx.web_search = args.web
            return 0 if run_ask(ctx, " ".join(args.question)) is not None else 1
        if args.command == "debug":
            from bartmoss.modes.debug import run_debug

            return 0 if run_debug(ctx, args.file, context_files=args.context).status in ("clean", "unchecked") else 1
        if args.command == "tb":
            from bartmoss.modes.tb import run_tb

            return 0 if run_tb(ctx, args.file, context_files=args.context).verdict in ("pass", "unchecked") else 1
        if args.command in ("vplan", "uvmplan"):
            from bartmoss.modes.plan import run_plan

            return 0 if run_plan(ctx, args.command, args.file, context_files=args.context).status == "ok" else 1
        if args.command == "eval":
            from bartmoss.evals import run_eval

            if args.prompt:
                from bartmoss.context import system_prompt

                system_prompt(args.prompt)  # fail fast on a typo
                ctx.prompt_overrides[args.mode] = args.prompt
            if args.judge:
                parse_model_spec(args.judge)
                ctx.config["eval"]["judge_model"] = args.judge
            try:
                run_eval(ctx, args.mode, Path(args.cases), args.only, args.repeat)
            except ProviderError as e:
                ctx.ui.error(f"eval aborted, no results saved: {e}")
                return 2
            return 0
        if args.command == "leaderboard":
            from bartmoss.leaderboard import best_runs, load_runs, recommend, render, render_categories, render_trend

            runs = load_runs(Path(args.results))
            if args.trend:
                ctx.ui.print(render_trend(runs))
                return 0
            best = best_runs(runs)
            categories = render_categories(best, Path(args.results).parent / "cases")
            table = render(best) + ("\n### Debug score by bug category\n\n" + categories if categories else "")
            ctx.ui.print(table)
            for mode, (model, score, top) in recommend(best).items():
                ctx.ui.info(f"{mode:8} cheapest within 0.05 of the best ({top:.2f}): {model} ({score:.2f})")
            if args.write:
                Path(args.write).write_text(table)
                ctx.ui.ok(f"wrote {args.write}")
            return 0
        if args.command == "compare":
            from bartmoss.compare import compare

            compare(ctx.ui, Path(args.a), Path(args.b))
            return 0
        if args.command == "doctor":
            cmd_doctor(ctx)
        elif args.command == "models":
            cmd_models(ctx)
        return 0
    except (ProviderError, WorkspaceError, ValueError) as e:
        ctx.ui.error(str(e))
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if args.command in ("ask", "debug", "tb", "vplan", "uvmplan", "eval") and ctx.usage.calls:
            ctx.ui.dim(ctx.usage.summary())


def entry() -> int:
    """Console-script entry point: exit quietly when output is piped into e.g. `head`."""
    try:
        return main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


if __name__ == "__main__":
    sys.exit(entry())
