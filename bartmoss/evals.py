"""Simulator-graded evals: run a mode over evals/cases/<mode>/* and score each case objectively.

debug: 1.0 if the fixed file passes the hidden reference testbench, 0.25 if it compiles but fails, else 0.
tb:    0.1 compiles · +0.2 passes on the correct DUT · +0.7 × fraction of mutants it catches
       (mutants are only scored when the testbench passes on the correct DUT, so a testbench
       that always prints TEST PASSED scores 0.3).
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from bartmoss.context import Context
from bartmoss.files import Workspace
from bartmoss.sim.tools import pick_simulator, simulate, tb_verdict
from bartmoss.ui import UI


def _score_debug(case_ctx: Context, case: Path, meta: dict, simulator: str, scratch: Path) -> dict:
    from bartmoss.modes.debug import run_debug

    context = meta.get("context", [])
    result = run_debug(case_ctx, meta["file"], write=False, context_files=context)
    detail = {"status": result.status, "attempts": result.attempts, "truncated": result.truncated, "score": 0.0,
              "_artifacts": {"analysis.txt": result.analysis, Path(meta["file"]).name: result.fixed}}
    if not result.fixed:
        return detail
    fixed_path = scratch / Path(meta["file"]).name
    fixed_path.write_text(result.fixed)
    run = simulate([*(case / c for c in context), fixed_path, case / meta["tb"]], meta["tb_top"], scratch / "sim", simulator)
    detail["ref_tb"] = tb_verdict(run.output) if run.stage == "run" else "compile_error"
    if detail["ref_tb"] == "pass":
        detail["score"] = 1.0
    elif run.stage == "run":
        detail["score"] = 0.25
    return detail


def _score_tb(case_ctx: Context, case: Path, meta: dict, simulator: str, scratch: Path) -> dict:
    from bartmoss.modes.tb import run_tb

    context = [case / c for c in meta.get("context", [])]
    result = run_tb(case_ctx, meta["file"], write=False, context_files=context)
    detail = {"verdict": result.verdict, "attempts": result.attempts, "truncated": result.truncated, "score": 0.0,
              "_artifacts": {f"tb_{result.top}.sv": result.tb, f"tb_{result.top}.md": result.notes,
                             "sim.log": result.sim.output if result.sim else None}}
    if result.verdict not in ("pass", "fail"):
        return detail
    detail["score"] = 0.1
    if result.verdict != "pass":
        return detail
    detail["score"] += 0.2
    tb_path = scratch / f"tb_{result.top}.sv"
    tb_path.write_text(result.tb)
    mutants = meta.get("mutants", [])
    caught = 0
    for i, mutant in enumerate(mutants):
        run = simulate([*context, case / mutant, tb_path], f"tb_{result.top}", scratch / f"mutant{i}", simulator)
        # A mutant that breaks compilation or hangs also counts as caught.
        if run.stage == "compile" or run.timed_out or tb_verdict(run.output) != "pass":
            caught += 1
    if mutants:
        detail["mutants_caught"] = f"{caught}/{len(mutants)}"
        detail["score"] += 0.7 * caught / len(mutants)
    else:
        detail["score"] += 0.7
    detail["score"] = round(detail["score"], 3)
    return detail


def run_eval(ctx: Context, mode: str, cases_root: Path, only: str | None = None) -> dict:
    ui = ctx.ui
    simulator = pick_simulator(ctx.config.get("simulator", "auto"))
    if simulator is None:
        raise SystemExit("Evals need a simulator: brew install verilator")
    cases_dir = (cases_root / mode).resolve()
    cases = sorted(p for p in cases_dir.iterdir() if (p / "meta.json").is_file()) if cases_dir.is_dir() else []
    if only:
        cases = [c for c in cases if only in c.name]
    if not cases:
        raise SystemExit(f"No cases found in {cases_dir}")

    spec = ctx.model_spec(mode)
    run_name = f"{date.today().isoformat()}_{mode}_{re.sub(r'[^A-Za-z0-9.-]+', '-', spec)}"
    results_dir = cases_root.resolve().parent / "results"
    ui.info(f"eval {mode} · {len(cases)} cases · model {spec} · simulator {simulator}")
    rows = []
    for case in cases:
        meta = json.loads((case / "meta.json").read_text())
        case_ctx = replace(ctx, workspace=Workspace(case, ctx.config["output_dir"]), ui=UI(quiet=True),
                           assume_yes=True, ask_history=[], _providers=ctx._providers)
        calls_before = len(ctx.usage.calls)
        started = time.time()
        scratch = Path(tempfile.mkdtemp(prefix=f"bartmoss_eval_{case.name}_"))
        try:
            scorer = _score_debug if mode == "debug" else _score_tb
            detail = scorer(case_ctx, case, meta, simulator, scratch)
        except Exception as e:  # one broken case shouldn't stop the run
            detail = {"score": 0.0, "error": f"{type(e).__name__}: {e}"}
        # Save what the model produced so failures can be inspected later.
        for name, text in detail.pop("_artifacts", {}).items():
            if text:
                target = results_dir / run_name / case.name / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
        calls = ctx.usage.calls[calls_before:]
        detail.update(case=case.name, seconds=round(time.time() - started, 1),
                      input_tokens=sum(r.input_tokens + r.cache_write_tokens + r.cache_read_tokens for _, r in calls),
                      output_tokens=sum(r.output_tokens for _, r in calls))
        rows.append(detail)
        colour = "green" if detail["score"] >= 0.99 else "yellow" if detail["score"] > 0 else "red"
        extra = detail.get("error") or detail.get("mutants_caught") or detail.get("ref_tb") or ""
        score_text = ui.style(f"{detail['score']:.2f}", colour)
        ui.print(f"  {score_text}  {case.name:28} {extra}")

    mean = sum(r["score"] for r in rows) / len(rows)
    report = {"date": date.today().isoformat(), "mode": mode, "model": spec, "simulator": simulator,
              "mean_score": round(mean, 3), "usage": ctx.usage.summary(), "cases": rows}
    results_dir.mkdir(exist_ok=True)
    out = results_dir / f"{run_name}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    ui.ok(f"mean score {mean:.2f} · results saved to {out}")
    return report
