"""/flow: lint → sim → synth → formal → report for one design, with no model calls (SPEC P5.2).

The plan is made by running each step with every tool call recorded instead of executed
(`tools.intercept`), so the dry-run shows exactly the commands that will run. Then each command
asks for confirmation before it runs (y / n / a = yes to the rest), unless --yes is given. Every
command also passes the sandbox allowlist (P5.4). A Markdown report goes to <stem>.flow.md.

Steps: lint (Verilator, or NVC/GHDL for VHDL); sim, if a testbench is given or found in the
project (a tb file that instantiates the top); synth (Yosys: latches, multiple drivers, logic
loops, then a generic synthesis for a cell count); formal, if the design has an `ifdef FORMAL block
(for example a .formal.sv written by /formal). A lint error stops the later steps.
"""

from __future__ import annotations

import re
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bartmoss.context import Context
from bartmoss.extract import top_module
from bartmoss.sim import tools
from bartmoss.sim.tools import RunResult, is_vhdl, lint_errors, tail

STEPS = ("lint", "sim", "synth", "formal")


@dataclass
class StepResult:
    name: str
    status: str  # pass | fail | error | skipped | declined | unchecked
    summary: str
    details: str = ""
    commands: list = field(default_factory=list)


@dataclass
class FlowResult:
    status: str  # pass | fail | declined | planned
    steps: list = field(default_factory=list)
    plan: list = field(default_factory=list)  # (step, command, stage)
    report: Optional[Path] = None


@dataclass
class Design:
    path: Path
    source: str
    context: list  # [(Path, text)]
    top: str
    tb: Optional[Path] = None
    tb_top: Optional[str] = None

    @property
    def files(self) -> list[Path]:
        return [p for p, _ in self.context] + [self.path]


def find_testbench(ctx: Context, design: Path, top: str) -> Optional[Path]:
    """A project testbench that instantiates `top`, preferring tb_<top> / <top>_tb names."""
    project = ctx.project()
    candidates = [f.path for f in project.files.values() if f.role == "tb" and top in f.uses and f.path != design]
    named = [p for p in candidates if p.stem.lower() in (f"tb_{top}".lower(), f"{top}_tb".lower(), f"{top}_test".lower())]
    return (named or sorted(candidates) or [None])[0]


def _lint(design: Design) -> StepResult:
    run = tools.lint(design.path, tuple(p for p, _ in design.context))
    if run is None:
        return StepResult("lint", "unchecked", "no linter installed (verilator, or nvc/ghdl for VHDL)")
    if run.declined:
        return StepResult("lint", "declined", "not run")
    messages = [l for l in run.output.splitlines() if re.match(r"^(%(Error|Warning)|\*\* (Error|Warning)|.*:\d+:\d*:? ?(error|warning))", l)]
    errors = lint_errors(run.output)
    warnings = sum(1 for l in messages if "Warning" in l or "warning" in l)
    if not run.ok or errors:
        return StepResult("lint", "fail", f"{run.tool}: {len(errors) or 'some'} error(s), {warnings} warning(s)",
                          tail(run.output, 40))
    return StepResult("lint", "pass", f"{run.tool}: no errors, {warnings} warning(s)",
                      "\n".join(messages[:30]))


def _sim(ctx: Context, design: Design) -> StepResult:
    simulator = tools.pick_simulator(ctx.config.get("simulator", "auto"), vhdl=is_vhdl(design.path))
    if simulator is None:
        return StepResult("sim", "unchecked", "no simulator installed")
    workdir = Path(tempfile.mkdtemp(prefix="bartmoss_flow_sim_"))
    run = tools.simulate([*design.files, design.tb], design.tb_top, workdir, simulator,
                         ctx.config.get("sim_timeout_s", 300))
    if run.declined:
        return StepResult("sim", "declined", "not run")
    if run.stage == "compile":
        return StepResult("sim", "fail", f"{simulator}: testbench didn't compile", tail(run.output, 40))
    verdict = tools.tb_verdict(run.output)
    detail = tail(run.output, 40)
    if run.timed_out:
        return StepResult("sim", "fail", f"{simulator}: timed out", detail)
    if verdict == "pass":
        return StepResult("sim", "pass", f"{simulator}: {design.tb.name} printed TEST PASSED", detail)
    if verdict == "fail":
        return StepResult("sim", "fail", f"{simulator}: {design.tb.name} failed", detail)
    return StepResult("sim", "pass" if run.ok else "fail",
                      f"{simulator}: exited {'cleanly' if run.ok else 'with an error'} without printing TEST PASSED/FAILED",
                      detail)


def _synth(design: Design) -> StepResult:
    if is_vhdl(design.path):
        return StepResult("synth", "skipped", "open-source Yosys reads Verilog/SystemVerilog only")
    run = tools.synth_stats(design.path, design.top, tuple(p for p, _ in design.context))
    if run is None:
        return StepResult("synth", "unchecked", "Yosys isn't installed")
    if run.declined:
        return StepResult("synth", "declined", "not run")
    findings = tools.synth_findings(run.output)
    if findings is None or not run.ok:
        return StepResult("synth", "error", "Yosys couldn't elaborate the design (often SystemVerilog it doesn't "
                          "support, such as package imports in a module header)", tail(run.output, 30))
    stats = tools.parse_stat(run.output)
    size = ""
    details = []
    if stats:
        size = f"; {stats['cells']} generic cells, {stats['flops']} flip-flops"
        top_types = sorted(stats["types"].items(), key=lambda kv: -kv[1])[:12]
        details = ["| Cell type | Count |", "|---|---|"] + [f"| `{t}` | {n} |" for t, n in top_types]
    if findings:
        return StepResult("synth", "fail", f"{len(findings)} problem(s): " + "; ".join(sorted(findings))[:200] + size,
                          "\n".join(sorted(findings) + [""] + details))
    return StepResult("synth", "pass", "no latches, multiple drivers or logic loops" + size, "\n".join(details))


def _formal(ctx: Context, design: Design) -> StepResult:
    from bartmoss.modes.formal import failed_lines, stage_files

    if is_vhdl(design.path):
        return StepResult("formal", "skipped", "open-source Yosys reads Verilog/SystemVerilog only")
    if "`ifdef FORMAL" not in design.source:
        return StepResult("formal", "skipped", "no `ifdef FORMAL block in the design (write one with /formal)")
    cfg = ctx.config["modes"]["formal"]
    workdir = Path(tempfile.mkdtemp(prefix="bartmoss_flow_formal_"))
    files = stage_files(workdir, design.context, design.path.name, design.source)
    result = tools.formal_check(files, design.top, workdir, cfg.get("depth", 20), False, cfg.get("timeout_s", 300))
    depth = cfg.get("depth", 20)
    if result.status == "pass":
        return StepResult("formal", "pass", f"every assertion holds for {depth} cycles (bounded check)")
    if result.status == "fail":
        lines = failed_lines(result, design.source)
        return StepResult("formal", "fail", f"{len(lines)} assertion(s) fail" +
                          (f" at step {result.step}" if result.step is not None else ""), "\n".join(lines))
    if result.status == "unchecked":
        return StepResult("formal", "unchecked", result.output)
    if result.status == "vacuous":
        return StepResult("formal", "fail", "the assumptions contradict each other, so nothing was checked")
    if "declined" in result.output:
        return StepResult("formal", "declined", "not run")
    return StepResult("formal", "error", "the properties didn't elaborate", tail(result.output, 30))


def _display(cmd: list, root: Path) -> str:
    """A command as the user should read it: workspace paths relative, scratch dirs as $TMP."""
    text = shlex.join(cmd)
    for tmp in dict.fromkeys((tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve()))):
        text = re.sub(re.escape(tmp.rstrip("/")) + r"/bartmoss_\w+", "$TMP", text)
    return text.replace(str(root) + "/", "")


def _resolve_design(ctx: Context, path, context_files, tb, top) -> Design:
    src, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files, for_file=src)
    top = top or top_module(source)
    if not top:
        raise ValueError(f"no module or entity found in {src.name}; pass --top")
    design = Design(src, source, context, top)
    if tb:
        design.tb = ctx.workspace.resolve(tb)
    else:
        design.tb = find_testbench(ctx, src, top)
    if design.tb:
        _, tb_text = ctx.workspace.read_text(design.tb)
        design.tb_top = top_module(tb_text)
    return design


def run_flow(ctx: Context, path, context_files=(), tb: Optional[str] = None, top: Optional[str] = None,
             steps=STEPS, dry_run: bool = False, write: bool = True) -> FlowResult:
    ui = ctx.ui
    design = _resolve_design(ctx, path, context_files, tb, top)
    rel = ctx.workspace.relative
    ui.info(f"flow for {rel(design.path)} · top {design.top}" +
            (f" · testbench {rel(design.tb)}" if design.tb else " · no testbench"))
    if design.context:
        ui.dim("  with " + ", ".join(rel(p) for p, _ in design.context))

    runners: dict[str, Callable[[], StepResult]] = {
        "lint": lambda: _lint(design),
        "sim": lambda: _sim(ctx, design),
        "synth": lambda: _synth(design),
        "formal": lambda: _formal(ctx, design),
    }
    skipped = {}
    if "sim" in steps and not design.tb:
        skipped["sim"] = "no testbench found that instantiates " + design.top + " (pass --tb, or generate one with /tb)"
    elif "sim" in steps and not design.tb_top:
        skipped["sim"] = f"no module or entity found in {design.tb.name}"
    if is_vhdl(design.path):
        skipped["synth"] = skipped["formal"] = "open-source Yosys reads Verilog/SystemVerilog only"
    elif "`ifdef FORMAL" not in design.source:
        skipped["formal"] = "no `ifdef FORMAL block in the design (write one with /formal)"
    selected = [s for s in STEPS if s in steps]

    # Plan: run every step with tool calls recorded instead of executed.
    plan = []

    def record(cmd, cwd, stage):
        plan.append((current[0], cmd, stage))
        return RunResult(True, stage, Path(cmd[0]).name, "Status: PASSED\n(dry run)")

    current = [""]
    with tools.intercept(record):
        for name in selected:
            if name not in skipped:
                current[0] = name
                runners[name]()
    ui.print(ui.style("Plan", "bold") + f" ({len(plan)} tool run{'s' if len(plan) != 1 else ''}, each in its own temp dir):")
    for n, name in enumerate(selected, 1):
        cmds = [c for s, c, _ in plan if s == name]
        if name in skipped:
            ui.print(f"  {n}. {name}: skipped, {skipped[name]}")
            continue
        ui.print(f"  {n}. {name}" + ("" if cmds else ": nothing to run"))
        for cmd in cmds:
            ui.dim("       $ " + _display(cmd, ctx.workspace.root))
    if dry_run:
        return FlowResult("planned", plan=plan)
    if not ctx.assume_yes and not sys.stdin.isatty():
        raise ValueError("/flow asks before each tool run; pass --yes to run it non-interactively, or --dry-run")

    state = {"all": ctx.assume_yes, "declined": False}

    def approve(cmd, cwd, stage):
        if state["declined"]:
            return RunResult(False, stage, Path(cmd[0]).name, "declined", declined=True)
        if state["all"]:
            return None
        answer = ui.ask(f"run `{_display(cmd, ctx.workspace.root)}`?", "[y/N/a]")
        if answer in ("a", "all"):
            state["all"] = True
            return None
        if answer in ("y", "yes"):
            return None
        state["declined"] = True
        return RunResult(False, stage, Path(cmd[0]).name, "declined", declined=True)

    results = []
    with tools.intercept(approve):
        for name in selected:
            if name in skipped:
                results.append(StepResult(name, "skipped", skipped[name]))
            elif state["declined"]:
                results.append(StepResult(name, "declined", "not run"))
            elif results and results[0].name == "lint" and results[0].status == "fail":
                results.append(StepResult(name, "skipped", "lint failed"))
            else:
                result = runners[name]()
                results.append(result)
            r = results[-1]
            colour = {"pass": "green", "fail": "red", "error": "red"}.get(r.status, "yellow")
            ui.print(f"  {ui.style(f'{r.status:9}', colour)} {r.name:7} {r.summary}")

    ran = [r for r in results if r.status not in ("skipped", "unchecked")]
    status = ("declined" if any(r.status == "declined" for r in results) else
              "fail" if any(r.status in ("fail", "error") for r in results) else
              "pass" if ran else "fail")
    flow = FlowResult(status, results, plan)
    if write:
        flow.report = ctx.workspace.write_text(f"{design.path.stem}.flow.md", render_report(ctx, design, flow),
                                               ctx.confirm_overwrite)
        if flow.report:
            ui.ok(f"report written to {rel(flow.report)}")
    if status == "pass":
        ui.ok("flow passed")
    elif status == "fail":
        ui.warn("flow found problems; see the report")
    return flow


def render_report(ctx: Context, design: Design, flow: FlowResult) -> str:
    rel = ctx.workspace.relative
    lines = [f"# Flow report: {rel(design.path)}", "",
             f"- **Top:** `{design.top}`",
             f"- **Context files:** " + (", ".join(f"`{rel(p)}`" for p, _ in design.context) or "none"),
             f"- **Testbench:** " + (f"`{rel(design.tb)}` (top `{design.tb_top}`)" if design.tb else "none"),
             f"- **Result:** {flow.status}", "",
             "| Step | Status | Summary |", "|---|---|---|"]
    lines += [f"| {r.name} | {r.status} | {r.summary.replace('|', '/')} |" for r in flow.steps]
    for r in flow.steps:
        lines += ["", f"## {r.name}", "", r.summary]
        cmds = [c for s, c, _ in flow.plan if s == r.name]
        if cmds and r.status not in ("skipped",):
            lines += ["", "Commands:", "", "```"] + [_display(c, ctx.workspace.root) for c in cmds] + ["```"]
        if r.details.strip():
            if r.details.lstrip().startswith("|"):
                lines += ["", r.details.strip()]
            else:
                lines += ["", "```", r.details.strip(), "```"]
    return "\n".join(lines) + "\n"
