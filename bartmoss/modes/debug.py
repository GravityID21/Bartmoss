"""/debug <file>: lint, ask the model for an analysis and a fixed file, re-lint, retry on new errors.
The original file is never modified; the fix is written to the output dir as <name>.fixed<ext>."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import hdl_block, tag, top_module
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim.tools import RunResult, lint, lint_errors, synth_check, synth_findings, tail


@dataclass
class DebugResult:
    status: str  # clean | new_lint_errors | new_synth_problems | no_fix | refused | unchecked
    analysis: Optional[str] = None
    fixed: Optional[str] = None
    lint_before: Optional[RunResult] = None
    lint_after: Optional[RunResult] = None
    written: Optional[Path] = None
    attempts: int = 0
    truncated: bool = False
    synth_before: Optional[set] = None  # Yosys findings; None if Yosys was unavailable or unsupported
    synth_after: Optional[set] = None


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


def run_debug(ctx: Context, path: str | Path, write: bool = True, context_files=()) -> DebugResult:
    ui = ctx.ui
    src_path, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files)
    context_paths = tuple(p for p, _ in context)
    ui.info(f"debug {ctx.workspace.relative(src_path)} · model {ctx.model_spec('debug')}")

    before = lint(src_path, context_paths)
    if before is None:
        ui.warn("No linter found; skipping lint checks (brew install verilator, or nvc for VHDL).")
    else:
        n_err = len(lint_errors(before.output))
        n_warn = before.output.count("%Warning") + before.output.count("** Warning")
        ui.dim(f"  lint before: {n_err} errors, {n_warn} warnings")
    known_errors = lint_errors(before.output) if before else set()

    top = top_module(source)
    synth_run = synth_check(src_path, top, context_paths) if top else None
    synth_before = synth_findings(synth_run.output) if synth_run else None
    if synth_before is not None:
        ui.dim(f"  synth before: {len(synth_before)} findings (latches, drivers, loops)")
    elif synth_run is not None:
        ui.dim("  synth check skipped: Yosys can't elaborate this design")

    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_debug_"))

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

    loop = generate_with_checks(
        lambda messages: ctx.ask_model("debug", messages),
        build_message(src_path.name, source, before, context, synth_before),
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
    elif synth_before is not None and (data.get("synth") or set()) - synth_before:
        status = "new_synth_problems"
    else:
        status = "clean"
    result = DebugResult(status, analysis, fixed, before, lint_after, attempts=loop.attempts,
                         truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)),
                         synth_before=synth_before, synth_after=data.get("synth"))
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
        "unchecked": ui.style("fix not lint-checked", "yellow"),
        "no_fix": ui.style("model did not return a fixed file", "red"),
    }[status]
    ui.info(f"{status_text} · attempts {loop.attempts}")
    return result
