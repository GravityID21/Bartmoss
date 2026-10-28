"""Design structure from Verilator's JSON AST (`--json-only`), for the agent's `hdl_structure` tool
and the hallucination guards (SPEC P4.1, P4.4): modules, parameters, ports, internal signals,
instances, always blocks and continuous assigns, plus which lines drive and read each signal.
No new dependency; returns None when Verilator is missing, too old for --json-only, or the
design doesn't parse (the caller reports the lint errors instead)."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bartmoss.sim.tools import RunResult, is_vhdl, verilator_json


@dataclass
class Signal:
    name: str
    kind: str  # input | output | inout | param | var | wire
    type: str  # e.g. logic[7:0], int, byte_t
    line: int
    value: str = ""  # parameter default, as Verilator prints it


@dataclass
class Instance:
    name: str
    module: str
    line: int
    pins: list = field(default_factory=list)


@dataclass
class Block:
    kind: str  # always_ff | always_comb | always | assign | initial
    line: int
    writes: list = field(default_factory=list)
    reads: list = field(default_factory=list)


@dataclass
class Module:
    name: str
    kind: str  # module | package | interface
    file: str
    line: int
    signals: list = field(default_factory=list)
    instances: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    refs: list = field(default_factory=list)  # (signal, "RD"|"WR", line)

    @property
    def ports(self) -> list:
        return [s for s in self.signals if s.kind in ("input", "output", "inout")]

    @property
    def params(self) -> list:
        return [s for s in self.signals if s.kind == "param"]


def _line(loc: str) -> int:
    """'e,12:3,12:14' -> 12."""
    try:
        return int(loc.split(",")[1].split(":")[0])
    except (IndexError, ValueError):
        return 0


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _type_text(var: dict, types: dict) -> str:
    dtype = types.get(var.get("dtypep"), {})
    name = var.get("dtypeName") or dtype.get("name") or "?"
    rng = dtype.get("range")
    return f"{name}[{rng}]" if rng and dtype.get("type") == "BASICDTYPE" and not var.get("isParam") else name


def _signal(var: dict, types: dict) -> Signal:
    direction = var.get("direction", "NONE")
    if var.get("isParam"):
        kind = "param"
    elif direction in ("INPUT", "OUTPUT", "INOUT"):
        kind = direction.lower()
    else:
        kind = "wire" if var.get("varType") == "WIRE" else "var"
    value = ""
    if kind == "param":
        const = next((n for n in _walk(var.get("valuep") or []) if n.get("type") == "CONST"), None)
        value = const["name"] if const else ""
        number = re.fullmatch(r"\d+'s?h([0-9a-fA-F]+)", value)
        value = str(int(number.group(1), 16)) if number else value
    return Signal(var["name"], kind, _type_text(var, types), _line(var.get("loc", "")), value)


def from_json(tree: dict, meta: dict) -> dict:
    """{module name: Module} from Verilator's tree.json and meta.json."""
    files = {k: v.get("filename", "") for k, v in meta.get("files", {}).items()}
    types = {}
    for misc in tree.get("miscsp", []):
        for t in misc.get("typesp", []) or []:
            types[t["addr"]] = t
    names = {m["addr"]: m["name"] for m in tree.get("modulesp", [])}
    modules = {}
    for m in tree.get("modulesp", []):
        loc = m.get("loc", "")
        kind = {"PACKAGE": "package", "IFACE": "interface"}.get(m["type"], "module")
        mod = Module(m.get("origName", m["name"]), kind, Path(files.get(loc.split(",")[0], "")).name, _line(loc))
        for stmt in m.get("stmtsp", []) or []:
            t = stmt.get("type")
            if t == "VAR":
                mod.signals.append(_signal(stmt, types))
            elif t == "CELL":
                pins = [p["name"] for p in stmt.get("pinsp", []) or []]
                mod.instances.append(Instance(stmt.get("origName", stmt["name"]), names.get(stmt.get("modp"), "?"),
                                              _line(stmt.get("loc", "")), pins))
            elif t in ("ALWAYS", "ASSIGNW", "INITIAL", "INITIALSTATIC"):
                kind_text = {"cont_assign": "assign"}.get(stmt.get("keyword"), stmt.get("keyword")) or \
                    {"ASSIGNW": "assign", "INITIAL": "initial"}.get(t, "always")
                refs = [n for n in _walk(stmt) if n.get("type") == "VARREF"]
                block = Block(kind_text, _line(stmt.get("loc", "")),
                              sorted({r["name"] for r in refs if r.get("access") in ("WR", "RW")}),
                              sorted({r["name"] for r in refs if r.get("access") in ("RD", "RW")}))
                mod.blocks.append(block)
            for ref in (n for n in _walk(stmt) if n.get("type") == "VARREF"):
                mod.refs.append((ref["name"], "WR" if ref.get("access") in ("WR", "RW") else "RD", _line(ref.get("loc", ""))))
        modules[mod.name] = mod
    return modules


def parse(files: list[Path], top: Optional[str] = None, timeout: int = 60) -> tuple[Optional[dict], Optional[RunResult]]:
    """(modules, run). modules is None if the design can't be parsed or Verilator can't do it."""
    files = [Path(f).resolve() for f in files]
    if not shutil.which("verilator") or any(is_vhdl(f) for f in files):
        return None, None
    workdir = Path(tempfile.mkdtemp(prefix="bartmoss_ast_"))
    run = verilator_json(files, workdir, top, timeout)
    tree, meta = workdir / "tree.json", workdir / "meta.json"
    if not run.ok or not tree.is_file():
        return None, run
    try:
        return from_json(json.loads(tree.read_text()), json.loads(meta.read_text()) if meta.is_file() else {}), run
    except (ValueError, KeyError, TypeError):
        return None, run


def describe(modules: dict, only: Optional[str] = None, signal: Optional[str] = None) -> str:
    """Compact structure listing for a model: much smaller than the source."""
    lines = []
    for mod in modules.values():
        if only and mod.name != only:
            continue
        lines.append(f"{mod.kind} {mod.name} ({mod.file}:{mod.line})")
        if mod.params:
            lines.append("  params: " + ", ".join(f"{p.name}={p.value or '?'}" for p in mod.params))
        for p in mod.ports:
            lines.append(f"  {p.kind:6} {p.type:16} {p.name}  (line {p.line})")
        internal = [s for s in mod.signals if s.kind in ("var", "wire")]
        if internal:
            lines.append("  signals: " + ", ".join(f"{s.name}:{s.type}" for s in internal))
        for inst in mod.instances:
            lines.append(f"  instance {inst.name}: {inst.module} (line {inst.line}) pins {', '.join(inst.pins)}")
        for b in mod.blocks:
            lines.append(f"  {b.kind} line {b.line}: writes {', '.join(b.writes) or '-'}; reads {', '.join(b.reads) or '-'}")
        if signal and any(name == signal for name, _, _ in mod.refs):
            drivers = sorted({line for name, acc, line in mod.refs if name == signal and acc == "WR"})
            loads = sorted({line for name, acc, line in mod.refs if name == signal and acc == "RD"})
            lines.append(f"  {signal}: driven at lines {drivers or 'none'}; read at lines {loads or 'none'}")
    return "\n".join(lines) + "\n" if lines else f"no module named {only!r}\n"


def interface(modules: dict, name: str) -> Optional[dict]:
    """{"ports": {name: (direction, type)}, "params": set} of one module."""
    mod = modules.get(name)
    if mod is None:
        return None
    return {"ports": {p.name: (p.kind, p.type) for p in mod.ports}, "params": {p.name for p in mod.params}}


def interface_changes(before: dict, after: Optional[dict]) -> list[str]:
    """How `after` differs from `before`: removed/added/changed ports and parameters."""
    if after is None:
        return ["the module is missing or was renamed"]
    changes = []
    for name, (direction, typ) in before["ports"].items():
        if name not in after["ports"]:
            changes.append(f"port {name} was removed")
        elif after["ports"][name] != (direction, typ):
            changes.append(f"port {name} changed from {direction} {typ} to {' '.join(after['ports'][name])}")
    changes += [f"port {name} was added" for name in after["ports"] if name not in before["ports"]]
    changes += [f"parameter {name} was removed" for name in before["params"] - after["params"]]
    changes += [f"parameter {name} was added" for name in after["params"] - before["params"]]
    return changes


def named_connections(source: str, module: str) -> Optional[tuple[set, set]]:
    """(pin names, parameter names) used by the first `module` instance in `source`, from named
    `.name(...)` connections. None if there's no instance or it uses `.*` / positional ports."""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.S)
    match = re.search(rf"\b{re.escape(module)}\s*(#\s*\((?P<params>(?:[^;])*?)\)\s*)?\w+\s*\((?P<pins>.*?)\)\s*;",
                      text, re.S)
    if not match or ".*" in match.group("pins"):
        return None
    pins = set(re.findall(r"\.(\w+)\s*\(", match.group("pins")))
    if not pins:
        return None
    return pins, set(re.findall(r"\.(\w+)\s*\(", match.group("params") or ""))


def unknown_connections(source: str, module: str, iface: dict) -> list[str]:
    """Pins and parameters a testbench connects that the DUT doesn't have (P4.4)."""
    found = named_connections(source, module)
    if not found:
        return []
    pins, params = found
    return ([f"port {p}" for p in sorted(pins - set(iface["ports"]))] +
            [f"parameter {p}" for p in sorted(params - iface["params"])])
