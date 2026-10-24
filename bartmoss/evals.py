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

from bartmoss.config import parse_model_spec
from bartmoss.context import PROMPT_DIR, Context
from bartmoss.extract import tag
from bartmoss.files import Workspace
from bartmoss.providers import ProviderError
from bartmoss.sim.tools import is_vhdl, pick_simulator, simulate, tb_verdict, unmet_requirement
from bartmoss.ui import UI


def _score_debug(case_ctx: Context, case: Path, meta: dict, simulator: str, scratch: Path) -> dict:
    from bartmoss.modes.debug import run_debug

    context = meta.get("context", [])
    result = run_debug(case_ctx, meta["file"], write=False, context_files=context)
    detail = {"status": result.status, "attempts": result.attempts, "truncated": result.truncated, "score": 0.0,
              "_artifacts": {"analysis.txt": result.analysis, Path(meta["file"]).name: result.fixed}}
    if not result.fixed:
        return detail
    simulator = pick_simulator(simulator, vhdl=is_vhdl(meta["file"]))
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
              "_artifacts": {f"tb_{result.top}{'.vhd' if is_vhdl(meta['file']) else '.sv'}": result.tb,
                             f"tb_{result.top}.md": result.notes,
                             "sim.log": result.sim.output if result.sim else None}}
    if result.verdict not in ("pass", "fail"):
        return detail
    detail["score"] = 0.1
    if result.verdict != "pass":
        return detail
    detail["score"] += 0.2
    simulator = result.simulator
    tb_path = scratch / f"tb_{result.top}{'.vhd' if is_vhdl(meta['file']) else '.sv'}"
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


def _run_case(ctx: Context, mode: str, case: Path, meta: dict, simulator: str, artifact_dir: Path) -> dict:
    """One scored attempt at one case, with its model outputs saved under artifact_dir."""
    case_ctx = replace(ctx, workspace=Workspace(case, ctx.config["output_dir"]), ui=UI(quiet=True),
                       assume_yes=True, ask_history=[], _providers=ctx._providers)
    calls_before = len(ctx.usage.calls)
    started = time.time()
    scratch = Path(tempfile.mkdtemp(prefix=f"bartmoss_eval_{case.name}_"))
    try:
        scorer = {"debug": _score_debug, "tb": _score_tb}.get(mode, _score_plan)
        detail = scorer(case_ctx, case, meta, simulator, scratch)
    except ProviderError:
        # API, billing, auth or network failures say nothing about the model: abort the whole run
        # rather than recording zeros that would skew the leaderboard.
        raise
    except Exception as e:  # one broken case shouldn't stop the run
        detail = {"score": 0.0, "error": f"{type(e).__name__}: {e}"}
    # Save what the model produced so failures can be inspected later.
    for name, text in detail.pop("_artifacts", {}).items():
        if text:
            target = artifact_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    calls = ctx.usage.calls[calls_before:]
    detail.update(seconds=round(time.time() - started, 1),
                  input_tokens=sum(r.input_tokens + r.cache_write_tokens + r.cache_read_tokens for _, r in calls),
                  output_tokens=sum(r.output_tokens for _, r in calls))
    return detail


def judge(ctx: Context, document: str, rubric: list[str]) -> list[dict]:
    """Grade a document against rubric items with the judge model (eval.judge_model).
    Returns [{"id", "met", "evidence"}]; an unparsable reply counts every item as unmet."""
    spec = ctx.config["eval"]["judge_model"]
    provider_name, model = parse_model_spec(spec)
    system = "MODE: judge\n\n" + (PROMPT_DIR / "judge.md").read_text().strip() + "\n"
    items = "\n".join(f"{i}. {item}" for i, item in enumerate(rubric, 1))
    result = ctx.provider(provider_name).chat(
        system, [{"role": "user", "content": f"<document>\n{document}\n</document>\n<rubric>\n{items}\n</rubric>"}],
        model=model, max_tokens=ctx.config["eval"].get("judge_max_tokens", 8000), effort="low")
    ctx.usage.add(spec, result)
    raw = tag(result.text, "result") or result.text
    try:
        graded = {int(x["id"]): x for x in json.loads(raw[raw.index("{"):raw.rindex("}") + 1])["items"]}
    except (ValueError, KeyError, TypeError):
        graded = {}
    return [{"id": i, "item": item, "met": bool(graded.get(i, {}).get("met")),
             "evidence": graded.get(i, {}).get("evidence", "")} for i, item in enumerate(rubric, 1)]


def _score_plan(case_ctx: Context, case: Path, meta: dict, simulator: str, scratch: Path) -> dict:
    """0.2 for a structurally complete plan + 0.8 × fraction of rubric items the judge finds."""
    from bartmoss.modes.plan import run_plan

    result = run_plan(case_ctx, meta["mode"], meta["file"], write=False, context_files=meta.get("context", []))
    detail = {"status": result.status, "attempts": result.attempts, "truncated": result.truncated, "score": 0.0,
              "_artifacts": {f"plan.{meta['mode']}.md": result.plan}}
    if not result.plan:
        return detail
    graded = judge(case_ctx, result.plan, meta["rubric"])
    met = sum(g["met"] for g in graded)
    detail.update(rubric_met=f"{met}/{len(graded)}", rubric=graded,
                  score=round((0.2 if result.status == "ok" else 0.0) + 0.8 * met / len(graded), 3))
    return detail


def run_eval(ctx: Context, mode: str, cases_root: Path, only: str | None = None, repeat: int = 1) -> dict:
    """Score every case `repeat` times. Reports per-case mean, min/max and full passes, plus the
    spread of the overall mean across repeats. Filtered (`only`) and repeated runs are saved under
    their own names so they never overwrite a plain full run."""
    ui = ctx.ui
    simulator = ctx.config.get("simulator", "auto")
    if mode in ("debug", "tb") and pick_simulator(simulator) is None:
        raise SystemExit("Evals need a simulator: brew install verilator (and nvc for VHDL cases)")
    cases_dir = (cases_root / mode).resolve()
    cases = sorted(p for p in cases_dir.iterdir() if (p / "meta.json").is_file()) if cases_dir.is_dir() else []
    if only:
        cases = [c for c in cases if only in c.name]
    if not cases:
        raise SystemExit(f"No cases found in {cases_dir}")
    repeat = max(1, repeat)

    spec = ctx.model_spec(mode)
    run_name = f"{date.today().isoformat()}_{mode}_{re.sub(r'[^A-Za-z0-9.-]+', '-', spec)}"
    if only:
        run_name += f"_only-{re.sub(r'[^A-Za-z0-9.-]+', '-', only)}"
    prompt = ctx.prompt_overrides.get(mode)
    if prompt:
        run_name += f"_p-{re.sub(r'[^A-Za-z0-9.-]+', '-', prompt)}"
    if repeat > 1:
        run_name += f"_x{repeat}"
    results_dir = cases_root.resolve().parent / "results"
    ui.info(f"eval {mode} · {len(cases)} cases × {repeat} · model {spec} · simulator {simulator}")
    calls_at_start = len(ctx.usage.calls)
    started = time.time()
    rows = []
    skipped = []
    for case in cases:
        meta = json.loads((case / "meta.json").read_text())
        reason = unmet_requirement(meta.get("requires", {}))
        if reason:
            # Skipped cases are reported but left out of the mean, so an old local
            # simulator isn't counted as a model failure.
            ui.print(f"  {ui.style('skip', 'yellow')}  {case.name:28} {reason}")
            skipped.append({"case": case.name, "skipped": reason})
            continue
        runs = []
        for i in range(repeat):
            artifact_dir = results_dir / run_name / case.name / (f"run{i + 1}" if repeat > 1 else "")
            runs.append(_run_case(ctx, mode, case, meta, simulator, artifact_dir))
        scores = [r["score"] for r in runs]
        row = {"case": case.name, "score": round(sum(scores) / repeat, 3), "min": min(scores), "max": max(scores),
               "full_passes": sum(s >= 0.99 for s in scores), "repeat": repeat, "runs": runs}
        rows.append(row)
        colour = "green" if row["score"] >= 0.99 else "yellow" if row["score"] > 0 else "red"
        score_text = ui.style(f"{row['score']:.2f}", colour)
        if repeat > 1:
            extra = f"min {row['min']:.2f} max {row['max']:.2f} · full score {row['full_passes']}/{repeat}"
        else:
            only_run = runs[0]
            extra = (only_run.get("error") or only_run.get("mutants_caught") or only_run.get("ref_tb")
                     or only_run.get("rubric_met") or "")
        ui.print(f"  {score_text}  {case.name:28} {extra}")

    if not rows:
        raise SystemExit("Every case was skipped; see the reasons above.")
    mean = sum(r["score"] for r in rows) / len(rows)
    # Mean across cases for each repeat: the spread shows how much one full run can vary.
    run_means = [round(sum(r["runs"][i]["score"] for r in rows) / len(rows), 3) for i in range(repeat)]
    # Cost of this run only (model and judge calls), not of the whole session.
    run_calls = ctx.usage.calls[calls_at_start:]
    costs = [ctx.usage.cost(s, r) for s, r in run_calls]
    report = {"format": 2, "date": date.today().isoformat(), "mode": mode, "model": spec, "simulator": simulator,
              "repeat": repeat, "prompt": prompt or mode, "mean_score": round(mean, 3), "run_means": run_means,
              "cost_usd": None if None in costs else round(sum(costs), 4),
              "seconds": round(time.time() - started, 1), "usage": ctx.usage.summary(),
              "cases": rows, "skipped": skipped}
    results_dir.mkdir(exist_ok=True)
    out = results_dir / f"{run_name}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    spread = f" (per-run means {min(run_means):.2f}–{max(run_means):.2f})" if repeat > 1 else ""
    ui.ok(f"mean score {mean:.2f}{spread} · results saved to {out}")
    return report
