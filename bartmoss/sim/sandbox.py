"""Sandbox for every tool Bartmoss runs (SPEC P5.4). `sim/tools.py` calls `check` before each
subprocess, so a command runs only if:

- it's an argument list, never a shell string, with no NUL or newline in any argument;
- the executable is on the allowlist (run by absolute path from PATH), or is the simulation
  binary Verilator built inside the working directory;
- every option is on that tool's allowlist, with a value of the expected shape (a top-module name,
  a plain file name, a number...), and every other argument is an existing HDL/SMT file or, for
  GHDL, a design-unit name;
- Yosys scripts use only the passes Bartmoss needs (no `shell`, `!`, `tee`, `script`, no paths);
- the working directory is inside the system temp dir: tools never run in, or write to, the
  workspace.

Tools also get an environment with API keys, tokens and passwords removed.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional


class SandboxError(ValueError):
    pass


NAME = r"[A-Za-z_]\w*"
PLAIN_FILE = r"[\w.-]+"  # a file name in the working directory, no path
SOURCE_SUFFIXES = (".sv", ".v", ".svh", ".vh", ".vhd", ".vhdl", ".fst")
BUILD_OUTPUTS = (".vvp", ".smt2")
PLUSARG = re.compile(r"\+\w+(=[\w.+-]*)?")
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.I)

# Options per tool: exact flags map to None (no value) or a value kind; a key ending in "*" matches
# by prefix and its value kind applies to the rest of the argument. Value kinds are a regex, "dir"
# (an existing directory), "file" (an existing source file) or "out" (a path inside the working dir).
OPTIONS: dict[str, dict[str, Optional[str]]] = {
    "verilator": {"--lint-only": None, "-Wall": None, "-Wno-*": r"[A-Za-z-]+", "--timing": None, "--binary": None,
                  "-j": r"\d+", "--top-module": NAME, "-Mdir": PLAIN_FILE, "-o": PLAIN_FILE, "--trace": None,
                  "--json-only": None, "--json-only-output": PLAIN_FILE, "--json-only-meta-output": PLAIN_FILE,
                  "-I*": "dir", "--version": None},
    "iverilog": {"-g2012": None, "-s": NAME, "-o": PLAIN_FILE, "-I*": "dir", "-V": None},
    "vvp": {"-n": None},
    "nvc": {"--std=2008": None, "-a": None, "-e": NAME, "-r": NAME, "--version": None},
    "ghdl": {"-a": None, "-e": None, "-r": None, "--std=08": None, "--version": None},
    "yosys": {"-q": None, "-p": "yosys", "-V": None, "--version": None},
    "yosys-smtbmc": {"-s": r"yices|bitwuzla|boolector|z3", "-t": r"\d+", "--presat": None, "-i": None,
                     "--dump-vcd": PLAIN_FILE},
    "fst2vcd": {"-f": "file", "-o": "out"},
}
# Programs that take +plusargs (a testbench's +seed=N): Icarus's runtime and Verilator's binary.
TAKES_PLUSARGS = ("vvp", "sim-binary")
# Yosys passes Bartmoss's own scripts use. Anything else (shell, tee, script, write_* to a path,
# plugins) is refused.
YOSYS_PASSES = {"read_verilog", "hierarchy", "proc", "opt_clean", "check", "synth", "stat", "prep", "async2sync",
                "chformal", "dffunmap", "write_smt2"}


def jail_root() -> Path:
    return Path(tempfile.gettempdir()).resolve()


def clean_env() -> dict:
    """The current environment without anything that looks like a credential."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}


def _value_ok(kind: str, value: str, cwd: Path) -> bool:
    if kind == "dir":
        return Path(value).is_absolute() and Path(value).is_dir()
    if kind == "file":
        return Path(value).is_absolute() and Path(value).is_file() and Path(value).suffix.lower() in SOURCE_SUFFIXES
    if kind == "out":
        target = (cwd / value).resolve()
        return target.parent == cwd or cwd in target.parents
    if kind == "yosys":
        return yosys_script_problem(value) is None
    return re.fullmatch(kind, value) is not None


def yosys_script_problem(script: str) -> Optional[str]:
    if "!" in script or "/" in script or "\\" in script:
        return "Yosys scripts may not contain shell escapes or paths"
    for command in filter(None, (c.strip() for c in script.split(";"))):
        name = command.split()[0]
        if name not in YOSYS_PASSES:
            return f"Yosys pass {name!r} isn't allowed"
    return None


def check(cmd, cwd: Path) -> tuple[str, str]:
    """Raise SandboxError unless `cmd` may run in `cwd`. Returns (tool name, executable path)."""
    if not isinstance(cmd, (list, tuple)) or not cmd or not all(isinstance(a, str) for a in cmd):
        raise SandboxError("commands must be a non-empty list of strings (no shell strings)")
    if any("\x00" in a or "\n" in a for a in cmd):
        raise SandboxError("command arguments may not contain NUL or newline characters")
    cwd = Path(cwd).resolve()
    root = jail_root()
    if cwd != root and root not in cwd.parents:
        raise SandboxError(f"tools run only in a temporary directory, not {cwd}")
    exe = cmd[0]
    if os.sep in exe or "/" in exe:
        if Path(exe).resolve() != (cwd / "obj_dir" / "sim").resolve():
            raise SandboxError(f"{exe} isn't an allowed executable")
        tool, path = "sim-binary", str(Path(exe).resolve())
    else:
        if exe not in OPTIONS:
            raise SandboxError(f"{exe} isn't an allowed executable")
        found = shutil.which(exe)
        if not found:
            raise SandboxError(f"{exe} isn't installed")
        tool, path = exe, found
    options = OPTIONS.get(tool, {})
    args = list(cmd[1:])
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("+") and tool in TAKES_PLUSARGS:
            if not PLUSARG.fullmatch(arg):
                raise SandboxError(f"plusarg {arg!r} isn't allowed")
        elif arg.startswith("-") or arg.startswith("+"):
            if arg in options:
                kind = options[arg]
                if kind is not None:
                    if i + 1 >= len(args) or not _value_ok(kind, args[i + 1], cwd):
                        raise SandboxError(f"{tool} {arg} needs a value of the allowed form")
                    i += 1
            else:
                prefix = next((k for k in options if k.endswith("*") and arg.startswith(k[:-1])), None)
                if prefix is None or not _value_ok(options[prefix], arg[len(prefix) - 1:], cwd):
                    raise SandboxError(f"option {arg!r} isn't allowed for {tool}")
        elif tool == "ghdl" and re.fullmatch(NAME, arg):
            pass  # design unit name for -e / -r
        elif re.fullmatch(PLAIN_FILE, arg) and Path(arg).suffix in BUILD_OUTPUTS:
            pass  # a file an earlier step wrote in the working directory (sim.vvp, design.smt2)
        elif not _value_ok("file", arg, cwd):
            raise SandboxError(f"{tool} argument {arg!r} isn't an existing HDL file")
        i += 1
    return tool, path
