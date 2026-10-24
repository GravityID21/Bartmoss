"""/tb <file>: generate a self-checking directed testbench + notes, compile and run it, retry on
compile errors. A TEST FAILED result is reported as a possible DUT bug, not "fixed" by the model.

With random=True (`/tb --random`, SPEC P3.5) the testbench is constrained-random with counter-based
functional coverage. It's run with seeds 1..N; any failing seed fails the test, and coverage is the
union over seeds. Coverage below modes.tb.coverage_target goes back to the model with the uncovered
bins, like a compile error."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import hdl_block, tag, top_module
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim.tools import RunResult, coverage, is_vhdl, pick_simulator, rerun, simulate, tail, tb_verdict


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
    seeds: int = 0  # random testbenches: seeds run
    failing_seed: Optional[int] = None
    coverage: Optional[tuple] = None  # (bins hit over all seeds, total bins, uncovered bin names)


def run_seeds(workdir: Path, top: str, simulator: str, seeds: int, first: RunResult, timeout: int) -> dict:
    """Run an already-built random testbench with +seed=2..N after `first` (seed 1). Returns the
    verdict (fail if any seed fails), the failing seed and its log, and coverage over all seeds."""
    runs = [(1, first)] + [(seed, rerun(workdir, top, simulator, (f"+seed={seed}",), timeout))
                           for seed in range(2, seeds + 1)]
    verdict, failing, sim = "pass", None, first
    for seed, run in runs:
        v = "fail" if run.timed_out else tb_verdict(run.output)
        if v != "pass" and verdict == "pass":
            verdict, failing, sim = ("fail" if v == "fail" else "unknown"), seed, run
    covs = [c for c in (coverage(run.output) for _, run in runs) if c]
    cov = None
    if covs:
        total = covs[0][1]
        if all(len(names) == t - h for h, t, names in covs):
            # Every seed named the bins it missed, so the union is total minus the bins all of them missed.
            missed = set.intersection(*(names for _, _, names in covs))
            cov = (total - len(missed), total, tuple(sorted(missed)))
        else:
            best = max(covs, key=lambda c: c[0])
            cov = (best[0], total, tuple(sorted(best[2])))
    return {"verdict": verdict, "failing_seed": failing, "sim": sim, "coverage": cov}


def run_tb(ctx: Context, path: str | Path, write: bool = True, context_files=(), random: bool = False,
           seeds: Optional[int] = None) -> TbResult:
    ui = ctx.ui
    src_path, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files)
    context_paths = [p for p, _ in context]
    vhdl = is_vhdl(src_path)
    if random and vhdl:
        raise ValueError("random testbenches are SystemVerilog only for now; use /tb without --random for VHDL")
    cfg = ctx.config["modes"]["tb"]
    seeds = max(1, seeds or cfg.get("random_seeds", 3)) if random else 0
    target = cfg.get("coverage_target", 0.9)
    top = top_module(source)
    if not top:
        ui.error(f"No {'entity' if vhdl else 'module'} declaration found in {src_path.name}.")
        return TbResult("broken")

    simulator = pick_simulator(ctx.config.get("simulator", "auto"), vhdl=vhdl)
    ui.info(f"tb{' --random' if random else ''} for {top} ({ctx.workspace.relative(src_path)}) · "
            f"model {ctx.model_spec('tb')} · simulator {simulator or 'none'}" + (f" · {seeds} seed{'s' if seeds > 1 else ''}" if random else ""))
    if simulator is None:
        ui.warn(f"No simulator found; the testbench won't be checked ({'brew install nvc' if vhdl else 'brew install verilator'}).")

    scratch = Path(tempfile.mkdtemp(prefix="bartmoss_tb_"))
    tb_name = f"tb_{top}"
    tb_file = f"{tb_name}{'.vhd' if vhdl else '.sv'}"
    unit = "entity" if vhdl else "module"
    attempt_no = [0]

    def check(text: str) -> CheckOutcome:
        attempt_no[0] += 1
        tb = tag(text, "tb_file") or hdl_block(text)
        notes = tag(text, "notes") or ""
        if not tb:
            return CheckOutcome(False, True, "Your reply had no <tb_file> section. Reply again in the required format.")
        if f"{unit} {tb_name}".lower() not in tb.lower():
            return CheckOutcome(False, True, f"The testbench {unit} must be named `{tb_name}`. Reply again in the same format.",
                                data={"tb": tb, "notes": notes})
        data = {"tb": tb, "notes": notes}
        if simulator is None:
            return CheckOutcome(True, data={**data, "verdict": "unchecked"})
        workdir = scratch / f"attempt{attempt_no[0]}"
        workdir.mkdir()
        tb_path = workdir / tb_file
        tb_path.write_text(tb)
        timeout = ctx.config.get("sim_timeout_s", 300)
        run = simulate([*context_paths, src_path, tb_path], tb_name, workdir, simulator, timeout,
                       ("+seed=1",) if random else ())
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
        if random:
            data.update(run_seeds(workdir, tb_name, simulator, seeds, run, timeout))
            verdict = data.pop("verdict")
        else:
            verdict = tb_verdict(run.output)
        if verdict == "unknown":
            return CheckOutcome(False, True,
                                f"The simulation ran but printed neither TEST PASSED nor TEST FAILED:\n<log>\n"
                                f"{tail(data['sim'].output)}\n</log>\nReply again in the same format.",
                                data={**data, "verdict": "broken"})
        if random and data["coverage"] is None:
            return CheckOutcome(False, True,
                                "The testbench didn't print a `COVERAGE: <hit>/<total> bins` line. Add the coverage "
                                "counters and report, and reply again in the same format.",
                                data={**data, "verdict": verdict})
        if random and verdict == "pass" and data["coverage"][0] < target * data["coverage"][1]:
            hit, total, missed = data["coverage"]
            return CheckOutcome(False, True,
                                f"The testbench passed with {seeds} seeds, but coverage is {hit}/{total} bins (target "
                                f"{target:.0%}). Bins no seed hit: {', '.join(missed) or 'not listed'}.\n"
                                "Improve the stimulus (bias, sequences, longer runs) so these bins are hit. Keep every "
                                "bin and every check. Reply again in the same format with the complete testbench.",
                                data={**data, "verdict": verdict})
        return CheckOutcome(True, data={**data, "verdict": verdict})

    def on_attempt(attempt: int, previous: Optional[CheckOutcome]) -> None:
        if attempt == 1:
            ui.dim("  asking model...")
        else:
            ui.warn(f"attempt {attempt}: sending simulator results back to the model")

    message = (f"Top module: {top}\nTestbench module name: {tb_name}\n\n"
               f'<file name="{src_path.name}">\n{source}</file>\n' + context_blocks(context))
    prompt = "tb_vhdl" if vhdl else "tb_random" if random else "tb"
    loop = generate_with_checks(lambda messages: ctx.ask_model("tb", messages, prompt=prompt), message, check,
                                ctx.config["modes"]["tb"].get("max_retries", 2), on_attempt)
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return TbResult("refused", top=top, attempts=loop.attempts)

    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    result = TbResult(data.get("verdict", "broken"), top, data.get("tb"), data.get("notes"), data.get("sim"),
                      simulator, attempts=loop.attempts,
                      truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)),
                      seeds=seeds, failing_seed=data.get("failing_seed"), coverage=data.get("coverage"))
    if result.truncated:
        ui.warn("The reply hit max_tokens and may be incomplete; raise modes.tb.max_tokens in bartmoss.json.")

    if result.tb and write:
        written = [ctx.workspace.write_text(tb_file, result.tb, ctx.confirm_overwrite)]
        if result.notes:
            written.append(ctx.workspace.write_text(f"{tb_name}.md", result.notes, ctx.confirm_overwrite))
        result.written = tuple(p for p in written if p)
        for p in result.written:
            ui.ok(f"wrote {ctx.workspace.relative(p)}")

    if result.coverage:
        hit, total, missed = result.coverage
        text = f"coverage {hit}/{total} bins over {seeds} seed{'s' if seeds > 1 else ''}"
        ui.ok(text) if hit >= target * total else ui.warn(text + (f"; missed: {', '.join(missed[:8])}" if missed else ""))
    if result.verdict == "pass":
        ui.ok(ui.style("TEST PASSED", "green") + f" with {simulator}" + (f" ({seeds} seed{'s' if seeds > 1 else ''})" if random else "") +
              f" · attempts {loop.attempts}")
    elif result.verdict == "fail":
        seed_text = f" with seed {result.failing_seed} (rerun with +seed={result.failing_seed})" if result.failing_seed else ""
        ui.warn(ui.style("TEST FAILED", "red") + seed_text +
                " — could be a DUT bug or a wrong expectation in the testbench.")
        for line in [l for l in result.sim.output.splitlines() if "ERROR" in l][:8]:
            ui.dim("  " + line)
        ui.dim(f"  Review the failures, then try /debug {ctx.workspace.relative(src_path)}")
    elif result.verdict == "unchecked":
        ui.warn("testbench not simulated (no simulator found)")
    else:
        ui.error(f"no working testbench after {loop.attempts} attempts")
        if result.sim:
            ui.dim(tail(result.sim.output, 15))
    return result
