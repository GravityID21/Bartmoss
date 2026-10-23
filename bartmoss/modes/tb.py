"""/tb <file>: generate a self-checking directed testbench + notes, compile and run it, retry on
compile errors. A TEST FAILED result is reported as a possible DUT bug, not "fixed" by the model."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import hdl_block, tag, top_module
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim.tools import RunResult, pick_simulator, simulate, tail, tb_verdict


@dataclass
class TbResult:
    verdict: str  # pass | fail | unchecked | broken | refused
    top: Optional[str] = None
    tb: Optional[str] = None
    notes: Optional[str] = None
    sim: Optional[RunResult] = None
    simulator: Optional[str] = None
    written: tuple = ()
    attempts: int = 0
    truncated: bool = False


def run_tb(ctx: Context, path: str | Path, write: bool = True, context_files=()) -> TbResult:
    ui = ctx.ui
    src_path, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files)
    context_paths = [p for p, _ in context]
    if src_path.suffix.lower() in (".vhd", ".vhdl"):
        ui.warn("VHDL testbenches aren't supported yet (planned: SPEC P1.14).")
        return TbResult("broken")
    top = top_module(source)
    if not top:
        ui.error(f"No module declaration found in {src_path.name}.")
        return TbResult("broken")

    simulator = pick_simulator(ctx.config.get("simulator", "auto"))
    ui.info(f"tb for {top} ({ctx.workspace.relative(src_path)}) · model {ctx.model_spec('tb')} · "
            f"simulator {simulator or 'none'}")
    if simulator is None:
        ui.warn("No simulator found; the testbench won't be checked (brew install verilator).")

    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_tb_"))
    tb_name = f"tb_{top}"
    attempt_no = [0]

    def check(text: str) -> CheckOutcome:
        attempt_no[0] += 1
        tb = tag(text, "tb_file") or hdl_block(text)
        notes = tag(text, "notes") or ""
        if not tb:
            return CheckOutcome(False, True, "Your reply had no <tb_file> section. Reply again in the required format.")
        if f"module {tb_name}" not in tb:
            return CheckOutcome(False, True, f"The testbench module must be named `{tb_name}`. Reply again in the same format.",
                                data={"tb": tb, "notes": notes})
        data = {"tb": tb, "notes": notes}
        if simulator is None:
            return CheckOutcome(True, data={**data, "verdict": "unchecked"})
        workdir = scratch / f"attempt{attempt_no[0]}"
        workdir.mkdir()
        tb_path = workdir / f"{tb_name}.sv"
        tb_path.write_text(tb)
        run = simulate([*context_paths, src_path, tb_path], tb_name, workdir, simulator, ctx.config.get("sim_timeout_s", 300))
        data["sim"] = run
        if run.stage == "compile" and not run.ok:
            return CheckOutcome(False, True,
                                f"The testbench failed to compile with {simulator}:\n<log>\n{tail(run.output)}\n</log>\n"
                                "Reply again in the same format with a corrected complete testbench. Don't change the DUT.",
                                data={**data, "verdict": "broken"})
        if run.timed_out:
            return CheckOutcome(False, True,
                                "The simulation didn't finish before the timeout. Make sure every path reaches $finish "
                                "and the watchdog is shorter. Reply again in the same format.",
                                data={**data, "verdict": "broken"})
        verdict = tb_verdict(run.output)
        if verdict == "unknown":
            return CheckOutcome(False, True,
                                f"The simulation ran but printed neither TEST PASSED nor TEST FAILED:\n<log>\n{tail(run.output)}\n"
                                "</log>\nReply again in the same format.",
                                data={**data, "verdict": "broken"})
        return CheckOutcome(True, data={**data, "verdict": verdict})

    def on_attempt(attempt: int, previous: Optional[CheckOutcome]) -> None:
        if attempt == 1:
            ui.dim("  asking model...")
        else:
            ui.warn(f"attempt {attempt}: sending simulator errors back to the model")

    message = (f"Top module: {top}\nTestbench module name: {tb_name}\n\n"
               f'<file name="{src_path.name}">\n{source}</file>\n' + context_blocks(context))
    loop = generate_with_checks(lambda messages: ctx.ask_model("tb", messages), message, check,
                                ctx.config["modes"]["tb"].get("max_retries", 2), on_attempt)
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return TbResult("refused", top=top, attempts=loop.attempts)

    data = loop.outcome.data if loop.outcome else {}
    result = TbResult(data.get("verdict", "broken"), top, data.get("tb"), data.get("notes"), data.get("sim"),
                      simulator, attempts=loop.attempts,
                      truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)))
    if result.truncated:
        ui.warn("The reply hit max_tokens and may be incomplete; raise modes.tb.max_tokens in bartmoss.json.")

    if result.tb and write:
        written = [ctx.workspace.write_text(f"{tb_name}.sv", result.tb, ctx.confirm_overwrite)]
        if result.notes:
            written.append(ctx.workspace.write_text(f"{tb_name}.md", result.notes, ctx.confirm_overwrite))
        result.written = tuple(p for p in written if p)
        for p in result.written:
            ui.ok(f"wrote {ctx.workspace.relative(p)}")

    if result.verdict == "pass":
        ui.ok(ui.style("TEST PASSED", "green") + f" with {simulator} · attempts {loop.attempts}")
    elif result.verdict == "fail":
        ui.warn(ui.style("TEST FAILED", "red") + " — could be a DUT bug or a wrong expectation in the testbench.")
        for line in [l for l in result.sim.output.splitlines() if l.startswith("ERROR")][:8]:
            ui.dim("  " + line)
        ui.dim(f"  Review the failures, then try /debug {ctx.workspace.relative(src_path)}")
    elif result.verdict == "unchecked":
        ui.warn("testbench not simulated (no simulator found)")
    else:
        ui.error(f"no working testbench after {loop.attempts} attempts")
        if result.sim:
            ui.dim(tail(result.sim.output, 15))
    return result
