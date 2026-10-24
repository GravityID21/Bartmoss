"""Wrappers around free HDL tools. Only known executables are run, with argument lists
(never a shell string), in a scratch directory, with a timeout."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

INSTALL_HINTS = {
    "verilator": "brew install verilator  (or apt install verilator)",
    "iverilog": "brew install icarus-verilog  (or apt install iverilog)",
    "nvc": "brew install nvc  (or apt install nvc)",
    "ghdl": "apt install ghdl  (the Homebrew cask is disabled on macOS; use nvc there)",
    "yosys": "brew install yosys  (or apt install yosys)",
}


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


def simulate(files: list[Path], top: str, workdir: Path, simulator: str, timeout: int = 300) -> RunResult:
    """Compile `files` with `top` as the root module, then run. Returns the first failing stage."""
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
        return _run([str(workdir / "obj_dir" / "sim")], workdir, timeout, "run")
    if simulator == "iverilog":
        build = _run(["iverilog", "-g2012", "-s", top, "-o", "sim.vvp", *sources], workdir, timeout, "compile")
        if not build.ok:
            return build
        return _run(["vvp", "-n", "sim.vvp"], workdir, timeout, "run")
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


def tb_verdict(output: str) -> str:
    """'pass', 'fail' or 'unknown', from the PASS/FAIL line the testbench prompt requires."""
    if "TEST FAILED" in output or re.search(r"^\s*(%Fatal|FATAL|\*\* Failure)|\(assertion failure\)", output, re.M):
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
