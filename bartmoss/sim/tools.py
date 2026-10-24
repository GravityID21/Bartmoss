"""Wrappers around free HDL tools. Only known executables are run, with argument lists
(never a shell string), in a scratch directory, with a timeout."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

INSTALL_HINTS = {
    "verilator": "brew install verilator  (or apt install verilator)",
    "iverilog": "brew install icarus-verilog  (or apt install iverilog)",
    "nvc": "brew install nvc  (or apt install nvc)",
    "ghdl": "apt install ghdl  (the Homebrew cask is disabled on macOS; use nvc there)",
    "yosys": "brew install yosys  (or apt install yosys)",
    "yosys-smtbmc": "installed with Yosys; /formal also needs an SMT solver",
    "z3": "brew install z3  (or apt install z3); yices, boolector and bitwuzla also work for /formal",
    "fst2vcd": "comes with GTKWave: apt install gtkwave  (or brew install gtkwave); only needed for .fst in /wave",
}
# SMT solvers yosys-smtbmc can use, fastest first: (executable, name for `-s`).
SOLVERS = (("yices-smt2", "yices"), ("bitwuzla", "bitwuzla"), ("boolector", "boolector"), ("z3", "z3"))


@dataclass
class RunResult:
    ok: bool
    stage: str  # lint | compile | run
    tool: str
    output: str
    timed_out: bool = False


def available() -> dict[str, Optional[str]]:
    return {name: shutil.which(name) for name in INSTALL_HINTS}


VHDL_SUFFIXES = (".vhd", ".vhdl")
VHDL_SIMULATORS = ("nvc", "ghdl")
VERILOG_SIMULATORS = ("verilator", "iverilog")


def is_vhdl(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VHDL_SUFFIXES


def pick_simulator(preference: str = "auto", vhdl: bool = False) -> Optional[str]:
    """Verilator first for (System)Verilog: best free SV support. NVC first for VHDL: it installs
    cleanly everywhere, while GHDL's macOS package is currently disabled."""
    candidates = VHDL_SIMULATORS if vhdl else VERILOG_SIMULATORS
    if preference != "auto" and preference in candidates:
        return preference if shutil.which(preference) else None
    for name in candidates:
        if shutil.which(name):
            return name
    return None


def tool_version(name: str) -> Optional[tuple]:
    """Installed version as a tuple, e.g. (5, 50) for Verilator 5.050; None if missing or unparsable."""
    if not shutil.which(name):
        return None
    flag = "-V" if name == "iverilog" else "--version"
    try:
        out = subprocess.run([name, flag], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else None


def unmet_requirement(requires: dict) -> Optional[str]:
    """Check {"verilator": "5.050"}-style minimum versions; returns why it's unmet, or None."""
    for name, minimum in requires.items():
        wanted = tuple(int(x) for x in minimum.split(".")[:2])
        found = tool_version(name)
        if found is None:
            return f"needs {name} >= {minimum}, not installed"
        if found < wanted:
            return f"needs {name} >= {minimum}, found {found[0]}.{found[1]:03d}"
    return None


def _run(cmd: list[str], cwd: Path, timeout: int, stage: str) -> RunResult:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # TimeoutExpired carries bytes even when text=True.
        output = "".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part for part in (e.stdout or "", e.stderr or "")
        )
        return RunResult(False, stage, cmd[0], output + f"\n[timed out after {timeout}s]", timed_out=True)
    return RunResult(proc.returncode == 0, stage, Path(cmd[0]).name, proc.stdout + proc.stderr)


def lint(path: Path, context: tuple = (), timeout: int = 60) -> Optional[RunResult]:
    """Lint `path`, with `context` files (packages, interfaces, submodules) compiled first.
    Verilator for (System)Verilog; VHDL-2008 analysis with NVC or GHDL for VHDL.
    Warnings are reported but only errors make ok=False. Returns None if no tool is installed."""
    sources = [*map(str, context), str(path)]
    if is_vhdl(path):
        tool = pick_simulator(vhdl=True)
        if tool is None:
            return None
        scratch = Path(tempfile.mkdtemp(prefix="bartmoss_lint_"))  # analysers write a work library
        if tool == "nvc":
            return _run(["nvc", "--std=2008", "-a", *sources], scratch, timeout, "lint")
        return _run(["ghdl", "-a", "--std=08", *sources], scratch, timeout, "lint")
    if not shutil.which("verilator"):
        return None
    cmd = ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing", *sources]
    return _run(cmd, path.parent, timeout, "lint")


def lint_errors(output: str) -> set[str]:
    """Error messages with file/line/column removed, so runs on different files can be compared.
    Understands Verilator (%Error), NVC (** Error) and GHDL (file:line:col: message)."""
    errors = set()
    for line in output.splitlines():
        if line.startswith("%Error"):
            message = re.sub(r"^%Error(-\w+)?:\s*(\S+:\d+:(\d+:)?\s*)?", "", line).strip()
            if message.startswith("Exiting due to"):
                continue
        elif line.startswith("** Error") or line.startswith("** Fatal"):
            message = re.sub(r"^\*\* (Error|Fatal):\s*", "", line).strip()
        elif re.match(r"^\S+\.vhdl?:\d+:\d+:", line) and "warning" not in line.lower():
            message = re.sub(r"^\S+:\d+:\d+:\s*(error:\s*)?", "", line).strip()
        else:
            continue
        if message:
            errors.add(message)
    return errors


def synth_check(path: Path, top: str, context: tuple = (), timeout: int = 120) -> Optional[RunResult]:
    """Elaborate with Yosys and run its structural checks (latches, multiple drivers, logic loops).
    Verilog/SystemVerilog only; returns None when Yosys isn't installed or the file is VHDL."""
    if is_vhdl(path) or not shutil.which("yosys") or not re.fullmatch(r"\w+", top):
        return None
    script = f"hierarchy -check -top {top}; proc; opt_clean; check"
    # Files go on the command line (not inside the script) so paths with spaces are safe.
    return _run(["yosys", "-p", script, *map(str, context), str(path)], path.parent, timeout, "synth")


def synth_findings(output: str) -> Optional[set[str]]:
    """Synthesis problems with locations removed, or None if Yosys couldn't elaborate the design
    (often unsupported SystemVerilog, such as package imports in a module header)."""
    findings = set()
    for line in output.splitlines():
        latch = re.search(r"Latch inferred for signal `([^']+)'", line)
        if latch:
            findings.add(f"latch inferred for {latch.group(1)}")
        elif "ERROR:" in line:
            return None
        elif line.startswith("Warning:") and ("driver" in line or "logic loop" in line):
            findings.add(line[len("Warning:"):].strip())
    return findings


def simulate(files: list[Path], top: str, workdir: Path, simulator: str, timeout: int = 300,
             plusargs: tuple = ()) -> RunResult:
    """Compile `files` with `top` as the root module, then run. Returns the first failing stage.
    `plusargs` (e.g. ("+seed=3",)) are passed to the run; see also `rerun`."""
    workdir.mkdir(parents=True, exist_ok=True)
    sources = [str(f) for f in files]
    if simulator == "verilator":
        build = _run(
            ["verilator", "--binary", "--timing", "-j", "0", "-Wno-fatal", "-Wno-lint", "-Wno-style",
             "--top-module", top, "-Mdir", "obj_dir", "-o", "sim", *sources],
            workdir, timeout, "compile",
        )
        if not build.ok:
            return build
        return rerun(workdir, top, simulator, plusargs, timeout)
    if simulator == "iverilog":
        build = _run(["iverilog", "-g2012", "-s", top, "-o", "sim.vvp", *sources], workdir, timeout, "compile")
        if not build.ok:
            return build
        return rerun(workdir, top, simulator, plusargs, timeout)
    if simulator == "nvc":
        build = _run(["nvc", "--std=2008", "-a", *sources, "-e", top], workdir, timeout, "compile")
        if not build.ok:
            return build
        return _run(["nvc", "--std=2008", "-r", top], workdir, timeout, "run")
    if simulator == "ghdl":
        build = _run(["ghdl", "-a", "--std=08", *sources], workdir, timeout, "compile")
        if build.ok:
            build = _run(["ghdl", "-e", "--std=08", top], workdir, timeout, "compile")
        if not build.ok:
            return build
        return _run(["ghdl", "-r", "--std=08", top], workdir, timeout, "run")
    raise ValueError(f"Unsupported simulator {simulator!r}")


def rerun(workdir: Path, top: str, simulator: str, plusargs: tuple = (), timeout: int = 300) -> RunResult:
    """Run a design `simulate` already built in `workdir` again, e.g. with another +seed=N.
    (System)Verilog simulators only."""
    if simulator == "verilator":
        return _run([str(workdir / "obj_dir" / "sim"), *plusargs], workdir, timeout, "run")
    if simulator == "iverilog":
        return _run(["vvp", "-n", "sim.vvp", *plusargs], workdir, timeout, "run")
    raise ValueError(f"rerun doesn't support {simulator!r}")


def coverage(output: str) -> Optional[tuple[int, int, set]]:
    """(bins hit, total bins, uncovered bin names) from the `COVERAGE: <hit>/<total>` and
    `UNCOVERED: <bin>` lines random testbenches print; None if there's no COVERAGE line."""
    match = re.search(r"^\s*COVERAGE:\s*(\d+)\s*/\s*(\d+)", output, re.M)
    if not match:
        return None
    uncovered = {m.strip() for m in re.findall(r"^\s*UNCOVERED:\s*(.+?)\s*$", output, re.M)}
    return int(match.group(1)), int(match.group(2)), uncovered


def fst_to_vcd(path: Path, out_dir: Path, timeout: int = 300) -> Optional[RunResult]:
    """Convert an FST dump to VCD with GTKWave's fst2vcd; None if it isn't installed.
    The VCD is written to out_dir/<stem>.vcd."""
    if not shutil.which("fst2vcd"):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    return _run(["fst2vcd", "-f", str(path), "-o", str(out_dir / f"{path.stem}.vcd")], out_dir, timeout, "convert")


def pick_solver() -> Optional[str]:
    """Solver name for yosys-smtbmc `-s`, or None if no supported solver is installed."""
    for exe, name in SOLVERS:
        if shutil.which(exe):
            return name
    return None


@dataclass
class FormalResult:
    status: str  # pass | fail | unproven (induction failed) | vacuous | error (doesn't elaborate) | unchecked
    stage: str  # elaborate | bmc | prove
    output: str
    failed: list = field(default_factory=list)  # "file:line" of each failed assertion
    step: Optional[int] = None  # BMC step of the first failure
    trace: Optional[Path] = None  # counterexample VCD
    timed_out: bool = False


def formal_check(files: list[Path], top: str, workdir: Path, depth: int = 20, prove: bool = False,
                 timeout: int = 300) -> FormalResult:
    """Bounded model check (and with `prove`, k-induction) of the assertions inside `ifdef FORMAL`
    blocks, using Yosys + yosys-smtbmc directly (the engine SymbiYosys drives). `files` must already
    be in `workdir` with plain names, since Yosys scripts can't quote paths."""
    solver = pick_solver()
    if not shutil.which("yosys") or not shutil.which("yosys-smtbmc") or solver is None or not re.fullmatch(r"\w+", top):
        return FormalResult("unchecked", "elaborate", "needs yosys, yosys-smtbmc and an SMT solver (z3 or yices)")
    reads = "; ".join(f"read_verilog -formal -sv {f.name}" for f in files)
    script = (f"{reads}; prep -top {top}; async2sync; chformal -assume -early; opt_clean; dffunmap; "
              "write_smt2 -wires design.smt2")
    build = _run(["yosys", "-q", "-p", script], workdir, timeout, "elaborate")
    if not build.ok:
        return FormalResult("error", "elaborate", build.output, timed_out=build.timed_out)
    stages = [("bmc", ["--presat"])] + ([("prove", ["-i"])] if prove else [])
    output = ""
    for stage, flags in stages:
        trace = workdir / f"{stage}_trace.vcd"
        run = _run(["yosys-smtbmc", "-s", solver, "-t", str(depth), *flags, "--dump-vcd", trace.name, "design.smt2"],
                   workdir, timeout, stage)
        output += run.output
        if run.timed_out:
            return FormalResult("error", stage, output, timed_out=True)
        if "Assumptions are unsatisfiable" in run.output:
            return FormalResult("vacuous", stage, output)
        failed = re.findall(r"Assert(?:ion)? failed in \S+: (\S+?:\d+)", run.output)
        if failed or "Status: FAILED" in run.output:
            steps = re.findall(r"in step (\d+)", run.output)
            # An induction failure may start from an unreachable state, so it isn't a real
            # counterexample: the properties hold to `depth` but aren't proven.
            return FormalResult("fail" if stage == "bmc" else "unproven", stage, output, sorted(set(failed)),
                                int(steps[-1]) if steps else None, trace if trace.is_file() else None)
        if "Status: PASSED" not in run.output:
            return FormalResult("error", stage, output)
    return FormalResult("pass", stages[-1][0], output)


def tb_verdict(output: str) -> str:
    """'pass', 'fail' or 'unknown', from the PASS/FAIL line the testbench prompt requires."""
    # Assertion failures inside the design (or a $stop they trigger) also count as failures.
    if "TEST FAILED" in output or re.search(
        r"^\s*(%Fatal|FATAL|\*\* Failure)|\(assertion failure\)|^(\[\d+\] )?%Error: .*(Assertion failed|Verilog \$stop)",
        output, re.M,
    ):
        return "fail"
    if "TEST PASSED" in output:
        return "pass"
    return "unknown"


def tail(text: str, max_lines: int = 60) -> str:
    """Keep error feedback small: the first lines usually hold the real cause."""
    lines = text.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"])
