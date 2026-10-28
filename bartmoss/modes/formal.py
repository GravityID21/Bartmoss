"""/formal <file>: the model writes assertions for one module; Bartmoss inserts them in an
`ifdef FORMAL block (the design itself is never edited) and checks them with Yosys + yosys-smtbmc.
Properties that don't elaborate, or whose assumptions contradict each other, go back to the model.
A failing assertion is reported with a counterexample summary as a possible design bug or a wrong
property, the same way /tb reports TEST FAILED; it isn't sent back to be "fixed"."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import tag, top_module
from bartmoss.sim import vcd
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim.tools import FormalResult, formal_check, is_vhdl, pick_solver, tail


@dataclass
class FormalModeResult:
    status: str  # pass | fail | unproven | vacuous | broken | unchecked | refused
    top: Optional[str] = None
    properties: Optional[str] = None
    notes: Optional[str] = None
    check: Optional[FormalResult] = None
    counterexample: Optional[str] = None  # waveform summary of the failing trace
    written: tuple = ()
    attempts: int = 0
    truncated: bool = False


def insert_properties(source: str, top: str, properties: str) -> str:
    """The design with `properties` in an `ifdef FORMAL block before the top module's endmodule."""
    body = re.sub(r"^\s*`ifdef\s+FORMAL\s*\n|\n\s*`endif\b[^\n]*\s*$", "", properties.strip("\n") + "\n")
    start = re.search(rf"^\s*module\s+(?:automatic\s+|static\s+)?{re.escape(top)}\b", source, re.M)
    end = re.compile(r"^\s*endmodule\b", re.M).search(source, start.end() if start else 0)
    if not end:
        raise ValueError(f"no endmodule found for {top}")
    block = f"`ifdef FORMAL\n{body.rstrip()}\n`endif\n"
    return source[:end.start()] + ("" if source[:end.start()].endswith("\n") else "\n") + block + source[end.start():]


def failed_lines(result: FormalResult, formal_source: str) -> list[str]:
    """Each failed assertion as 'line N: <source text>'."""
    lines = formal_source.splitlines()
    out = []
    for loc in result.failed:
        number = int(loc.rsplit(":", 1)[1])
        text = lines[number - 1].strip() if 0 < number <= len(lines) else ""
        out.append(f"line {number}: {text}")
    return out


def stage_files(workdir: Path, context: list, name: str, text: str) -> list[Path]:
    """Copy sources into the work dir with plain names (Yosys scripts can't quote paths)."""
    files = []
    for i, (path, body) in enumerate(context):
        target = workdir / (path.name if re.fullmatch(r"[\w.-]+", path.name) else f"context{i}{path.suffix}")
        target.write_text(body)
        files.append(target)
    target = workdir / (name if re.fullmatch(r"[\w.-]+", name) else "design.sv")
    target.write_text(text)
    return files + [target]


def run_formal(ctx: Context, path: str | Path, write: bool = True, context_files=(), depth: Optional[int] = None,
               prove: bool = False) -> FormalModeResult:
    ui = ctx.ui
    cfg = ctx.config["modes"]["formal"]
    src_path, source = ctx.workspace.read_text(path)
    if is_vhdl(src_path):
        raise ValueError("/formal supports (System)Verilog only; open-source Yosys can't read VHDL")
    context = ctx.read_context_files(context_files, for_file=src_path)
    top = top_module(source)
    if not top:
        ui.error(f"No module declaration found in {src_path.name}.")
        return FormalModeResult("broken")
    depth = depth or cfg.get("depth", 20)
    solver = pick_solver()
    ui.info(f"formal for {top} ({ctx.workspace.relative(src_path)}) · model {ctx.model_spec('formal')} · "
            f"{'prove' if prove else 'bmc'} depth {depth} · solver {solver or 'none'}")
    if solver is None or not shutil.which("yosys-smtbmc"):
        ui.warn("No yosys-smtbmc or SMT solver found; the properties won't be checked (brew install yosys z3).")

    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_formal_"))
    formal_name = f"{src_path.stem}.formal{src_path.suffix}"
    attempt_no = [0]

    def check(text: str) -> CheckOutcome:
        attempt_no[0] += 1
        props = tag(text, "properties")
        notes = tag(text, "notes") or ""
        if not props:
            return CheckOutcome(False, True, "Your reply had no <properties> section. Reply again in the required format.")
        formal_source = insert_properties(source, top, props)
        data = {"properties": props, "notes": notes, "formal": formal_source}
        workdir = scratch / f"attempt{attempt_no[0]}"
        workdir.mkdir()
        files = stage_files(workdir, context, formal_name, formal_source)
        result = formal_check(files, top, workdir, depth, prove, cfg.get("timeout_s", 300))
        data["check"] = result
        if result.status == "unchecked":
            return CheckOutcome(True, data=data)
        if result.status == "error" and not result.timed_out:
            return CheckOutcome(False, True,
                                f"Yosys couldn't elaborate the design with your properties:\n<log>\n{tail(result.output, 30)}\n"
                                "</log>\nRemember the supported subset (immediate assertions, $past, no |-> or ##). "
                                "Reply again in the same format.", data=data)
        if result.status == "vacuous":
            return CheckOutcome(False, True,
                                "Your assumptions contradict each other, so every assertion would pass vacuously. "
                                "Remove or relax assumptions and reply again in the same format.", data=data)
        return CheckOutcome(True, data=data)

    def on_attempt(attempt: int, previous: Optional[CheckOutcome]) -> None:
        ui.dim("  asking model..." if attempt == 1 else f"  attempt {attempt}: sending Yosys errors back to the model")

    message = f"Top module: {top}\n\n" + f'<file name="{src_path.name}">\n{source}</file>\n' + context_blocks(context)
    loop = generate_with_checks(lambda messages: ctx.ask_model("formal", messages), message, check,
                                cfg.get("max_retries", 2), on_attempt)
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return FormalModeResult("refused", top, attempts=loop.attempts)

    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    check_result: Optional[FormalResult] = data.get("check")
    status = "broken" if not data.get("properties") else check_result.status if check_result else "broken"
    status = "broken" if status == "error" else status
    result = FormalModeResult(status, top, data.get("properties"), data.get("notes"), check_result,
                              attempts=loop.attempts, truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)))
    if result.truncated:
        ui.warn("The reply hit max_tokens and may be incomplete; raise modes.formal.max_tokens in bartmoss.json.")
    if check_result and check_result.trace and check_result.status in ("fail", "unproven"):
        dump, signals, clock, _, _ = vcd.load(check_result.trace, max_signals=cfg.get("max_signals", 24))
        result.counterexample = vcd.summarise(dump, signals, clock, 60, "counterexample")

    if data.get("formal") and write:
        written = [ctx.workspace.write_text(formal_name, data["formal"], ctx.confirm_overwrite)]
        if result.notes:
            written.append(ctx.workspace.write_text(f"{src_path.stem}.formal.md", result.notes, ctx.confirm_overwrite))
        if result.counterexample and check_result.trace:
            written.append(ctx.workspace.write_text(f"{src_path.stem}.cex.vcd", check_result.trace.read_text(),
                                                    ctx.confirm_overwrite))
        result.written = tuple(p for p in written if p)
        for p in result.written:
            ui.ok(f"wrote {ctx.workspace.relative(p)}")

    if status == "pass":
        what = f"proved (k-induction, depth {depth})" if prove else f"hold for {depth} cycles (bounded check)"
        ui.ok(ui.style("all assertions " + what, "green") + f" · attempts {loop.attempts}")
    elif status in ("fail", "unproven"):
        label = "ASSERTION FAILED" if status == "fail" else "NOT PROVEN (induction failed; BMC passed)"
        ui.warn(ui.style(label, "red" if status == "fail" else "yellow") +
                (f" at step {check_result.step}" if check_result.step is not None else "") +
                " — could be a design bug or a wrong property.")
        for line in failed_lines(check_result, data["formal"])[:8]:
            ui.dim("  " + line)
        if result.counterexample and status == "fail":
            ui.dim("  counterexample:")
            ui.dim("\n".join("  " + l for l in result.counterexample.splitlines()[-25:]))
    elif status == "unchecked":
        ui.warn("properties not checked (no yosys-smtbmc or SMT solver)")
    elif status == "vacuous":
        ui.error("the assumptions contradict each other; every check would pass vacuously")
    else:
        ui.error(f"no properties that elaborate after {loop.attempts} attempts")
        if check_result:
            ui.dim(tail(check_result.output, 15))
    return result
