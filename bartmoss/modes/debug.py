"""/debug <file>: lint, ask the model for an analysis and a fixed file, re-lint, retry on new errors.
The original file is never modified; the fix is written to the output dir as <name>.fixed<ext>.

With agent=True (`debug --agent`, SPEC P4.1) the model gets tools first: it can read the project,
query the Verilator AST, lint, and write and run its own small testbenches before answering. The
answer then goes through exactly the same checks and retries as the fixed workflow."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import hdl_block, tag, top_module
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim import hdl_ast
from bartmoss.sim.tools import RunResult, lint, lint_errors, synth_check, synth_findings, tail


@dataclass
class DebugResult:
    status: str  # clean | new_lint_errors | new_synth_problems | interface_changed | no_fix | refused | unchecked
    analysis: Optional[str] = None
    fixed: Optional[str] = None
    lint_before: Optional[RunResult] = None
    lint_after: Optional[RunResult] = None
    written: Optional[Path] = None
    attempts: int = 0
    truncated: bool = False
    synth_before: Optional[set] = None  # Yosys findings; None if Yosys was unavailable or unsupported
    synth_after: Optional[set] = None
    interface_changes: tuple = ()  # how the fix changed the module's ports/parameters (P4.4 guard)
    agent_steps: int = 0  # model calls made by the agent (0 without --agent)
    tool_calls: int = 0
    agent_stopped: str = ""  # why the agent was asked to finish early: budget | steps | truncated


def build_message(name: str, source: str, lint_result: Optional[RunResult], context: list = (),
                  synth: Optional[set] = None) -> str:
    message = f'<file name="{name}">\n{source}</file>\n' + context_blocks(context)
    if lint_result is None:
        message += "\nNo linter is available, so review the file by reading it.\n"
    elif lint_result.output.strip():
        message += f"\n{lint_result.tool} lint output:\n<lint>\n{tail(lint_result.output)}\n</lint>\n"
    else:
        message += f"\n{lint_result.tool} lint reported nothing.\n"
    if synth:
        listing = "\n".join(sorted(synth))
        message += f"\nYosys synthesis check findings:\n<synth>\n{tail(listing)}\n</synth>\n"
    elif synth is not None:
        message += "\nYosys synthesis check found no latches, multiple drivers or logic loops.\n"
    return message


def run_debug(ctx: Context, path: str | Path, write: bool = True, context_files=(), agent: bool = False,
              visible=None) -> DebugResult:
    """`visible` limits which workspace files the agent may see (evals hide reference testbenches)."""
    ui = ctx.ui
    src_path, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files, for_file=src_path)
    context_paths = tuple(p for p, _ in context)
    ui.info(f"debug{' --agent' if agent else ''} {ctx.workspace.relative(src_path)} · model {ctx.model_spec('debug')}")

    before = lint(src_path, context_paths)
    if before is None:
        ui.warn("No linter found; skipping lint checks (brew install verilator, or nvc for VHDL).")
    else:
        n_err = len(lint_errors(before.output))
        n_warn = before.output.count("%Warning") + before.output.count("** Warning")
        ui.dim(f"  lint before: {n_err} errors, {n_warn} warnings")
    known_errors = lint_errors(before.output) if before else set()

    top = top_module(source)
    # Interface guard (P4.4): the fix must keep the module's ports and parameters. Skipped when the
    # original doesn't parse (a syntax error may be the bug) or Verilator can't produce an AST.
    iface_before = None
    if top and before is not None and before.ok:
        modules, _ = hdl_ast.parse([*context_paths, src_path], top)
        iface_before = hdl_ast.interface(modules, top) if modules else None
    synth_run = synth_check(src_path, top, context_paths) if top else None
    synth_before = synth_findings(synth_run.output) if synth_run else None
    if synth_before is not None:
        ui.dim(f"  synth before: {len(synth_before)} findings (latches, drivers, loops)")
    elif synth_run is not None:
        ui.dim("  synth check skipped: Yosys can't elaborate this design")

    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_debug_"))
    interface_warned = [None]

    def check(text: str) -> CheckOutcome:
        fixed = tag(text, "fixed_file") or hdl_block(text)
        if not fixed:
            return CheckOutcome(False, True, "Your reply had no <fixed_file> section. "
                                "Reply again in the required format, including the complete file.")
        if before is None:
            return CheckOutcome(True, data={"fixed": fixed})
        candidate = scratch / src_path.name
        candidate.write_text(fixed)
        after = lint(candidate, context_paths)
        new_errors = lint_errors(after.output) - known_errors
        if new_errors:
            listing = "\n".join(sorted(new_errors))
            return CheckOutcome(False, True,
                                f"Your fixed file introduced new {after.tool} errors:\n<lint>\n{tail(listing)}\n</lint>\n"
                                "Fix them and reply again in the same format with the complete file.",
                                data={"fixed": fixed, "lint": after})
        data = {"fixed": fixed, "lint": after}
        if iface_before is not None:
            modules, _ = hdl_ast.parse([*context_paths, candidate], top)
            changes = hdl_ast.interface_changes(iface_before, hdl_ast.interface(modules, top)) if modules else []
            data["interface"] = changes
            # Push back once; a resubmitted change is accepted (the bug may really be in a declaration)
            # and reported as interface_changed.
            if changes and changes != interface_warned[0]:
                interface_warned[0] = changes
                return CheckOutcome(False, True,
                                    "Your fixed file changes the module interface, which would break every place it's "
                                    "instantiated:\n- " + "\n- ".join(changes) + "\nKeep the module name, ports and "
                                    "parameters exactly as in the original unless the bug is in a declaration; if it is, "
                                    "keep the change and say why in the analysis. Reply again in the same format with the "
                                    "complete file.",
                                    data=data)
        if synth_before is not None:
            synth_after = synth_findings(synth_check(candidate, top, context_paths).output)
            data["synth"] = synth_after
            new_synth = (synth_after or set()) - synth_before
            if new_synth:
                listing = "\n".join(sorted(new_synth))
                return CheckOutcome(False, True,
                                    f"Your fixed file introduced new synthesis problems (Yosys):\n<synth>\n{tail(listing)}\n"
                                    "</synth>\nFix them and reply again in the same format with the complete file.",
                                    data=data)
        return CheckOutcome(True, data=data)

    def on_attempt(attempt: int, previous: Optional[CheckOutcome]) -> None:
        if attempt == 1:
            ui.dim("  asking model...")
        else:
            ui.warn(f"attempt {attempt}: sending check failures back to the model")

    message = build_message(src_path.name, source, before, context, synth_before)
    session = None
    if agent:
        from bartmoss.agent import AgentSession, Workbench

        bench = Workbench(ctx, visible)
        session = AgentSession(ctx, "debug", "debug_agent", bench)
        names = [ctx.workspace.relative(p) for p in (src_path, *context_paths)]
        message = (f"<target>{ctx.workspace.relative(src_path)}</target>\n"
                   f"Files you can pass to tools: {', '.join(names)}, plus anything you find with list_dir or "
                   f"search, and scratch/<name> files you write.\n\n" + message)
    loop = generate_with_checks(
        session.ask if session else (lambda messages: ctx.ask_model("debug", messages)),
        message,
        check,
        ctx.config["modes"]["debug"].get("max_retries", 2),
        on_attempt,
    )
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return DebugResult("refused", attempts=loop.attempts, lint_before=before)

    reply_text = loop.reply.text if loop.reply else ""
    analysis = tag(reply_text, "analysis") or reply_text.split("<fixed_file>")[0].strip() or None
    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    fixed = data.get("fixed")
    lint_after = data.get("lint")
    if fixed is None:
        status = "no_fix"
    elif before is None:
        status = "unchecked"
    elif lint_after is not None and lint_errors(lint_after.output) - known_errors:
        status = "new_lint_errors"
    elif data.get("interface"):
        status = "interface_changed"
    elif synth_before is not None and (data.get("synth") or set()) - synth_before:
        status = "new_synth_problems"
    else:
        status = "clean"
    result = DebugResult(status, analysis, fixed, before, lint_after, attempts=loop.attempts,
                         truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)),
                         synth_before=synth_before, synth_after=data.get("synth"),
                         interface_changes=tuple(data.get("interface") or ()),
                         agent_steps=session.steps if session else 0, tool_calls=session.tool_calls if session else 0,
                         agent_stopped=session.stopped if session else "")
    if session:
        ui.dim(f"  agent: {session.steps} model calls, {session.tool_calls} tool calls"
               + (f", stopped early ({session.stopped})" if session.stopped else ""))
    if result.truncated:
        ui.warn("The reply hit max_tokens and may be incomplete; raise modes.debug.max_tokens in bartmoss.json.")

    if analysis:
        ui.print()
        ui.print(analysis.strip())
        ui.print()
    if fixed and write:
        name = f"{src_path.stem}.fixed{src_path.suffix}"
        result.written = ctx.workspace.write_text(name, fixed, ctx.confirm_overwrite)
        if result.written:
            ui.ok(f"fixed file written to {ctx.workspace.relative(result.written)}")
    status_text = {
        "clean": ui.style("lint clean (no new errors)" + (", synth clean" if synth_before is not None else ""), "green"),
        "new_lint_errors": ui.style("fix still has new lint errors; review it carefully", "yellow"),
        "new_synth_problems": ui.style("fix adds synthesis problems (latch/driver/loop); review it", "yellow"),
        "interface_changed": ui.style("fix changes the module's ports or parameters: "
                                      + "; ".join(result.interface_changes), "yellow"),
        "unchecked": ui.style("fix not lint-checked", "yellow"),
        "no_fix": ui.style("model did not return a fixed file", "red"),
    }[status]
    ui.info(f"{status_text} · attempts {loop.attempts}")
    return result
