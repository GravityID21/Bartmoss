"""`bartmoss mcp`: an MCP server over stdio exposing Bartmoss's checkers as tools (SPEC P4.5), so
Claude Code or any other MCP client can lint, simulate, query structure, summarise waveforms and
run formal checks with the same safety rules Bartmoss applies to itself. No model is called and
no API key is needed: these are the free tools.

Stdlib only: newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio transport). Paths are
confined to the workspace root. Anything human-readable goes to stderr, never stdout.

Register with Claude Code:  claude mcp add bartmoss -- bartmoss --root /path/to/project mcp
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, TextIO

from bartmoss import __version__
from bartmoss.agent import Workbench, _schema, validate
from bartmoss.files import WorkspaceError
from bartmoss.sim import vcd
from bartmoss.sim.tools import formal_check, synth_check, synth_findings, tail, unsafe_constructs

PROTOCOL_VERSION = "2025-06-18"
FILES = {"type": "array", "items": {"type": "string"}, "description": "workspace-relative paths, dependencies first"}


class CheckerTools:
    """The MCP tool set: Workbench's tools that make sense for an outside client, plus synthesis,
    waveform and formal checks. Every simulated file gets the unsafe-construct check, since the
    client is usually an AI agent writing those files."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.bench = Workbench(ctx)
        b = self.bench.tools
        self.tools = {
            "lint": (b["run_lint"].description, b["run_lint"].input_schema, self.bench.run_lint),
            "hdl_structure": (b["hdl_structure"].description, b["hdl_structure"].input_schema, self.bench.hdl_structure),
            "simulate": (b["run_sim"].description + " Files using $system, file I/O or DPI are refused.",
                         b["run_sim"].input_schema, self.simulate),
            "synth_check": ("Elaborate with Yosys and report latches, multiple drivers and logic loops.",
                            _schema({"files": FILES, "top": {"type": "string"}}, ["files", "top"]), self.synth),
            "wave_summary": ("Summarise a VCD/FST dump (clock-sampled cycle table, X/Z spans, short pulses, "
                             "valid/ready checks) instead of reading it raw.",
                             _schema({"file": {"type": "string"}, "signals": {"type": "array", "items": {"type": "string"}},
                                      "window": {"type": "string", "description": "e.g. 100:500 or 1us:2us"},
                                      "clock": {"type": "string"}}, ["file"]), self.wave),
            "formal_check": ("Bounded model check (k-induction with prove=true) of properties with Yosys + "
                             "yosys-smtbmc. `properties` is the body of an `ifdef FORMAL block, inserted before the "
                             "top module's endmodule; use immediate assertions with $past (no |-> or ##).",
                             _schema({"file": {"type": "string"}, "top": {"type": "string"},
                                      "properties": {"type": "string"}, "context": FILES,
                                      "depth": {"type": "integer"}, "prove": {"type": "boolean"}},
                                     ["file", "top", "properties"]), self.formal),
            "project_search": ("Ranked keyword search (BM25, plus embeddings if configured) over the project's HDL, "
                               "split into modules and always/assign blocks, and its documents, split by heading. "
                               "Returns path:lines, titles and excerpts.",
                               b["retrieve"].input_schema, self.bench.retrieve),
            "project_deps": ("The project files a file depends on (packages, interfaces, submodules), in compile "
                             "order; with no file, a summary of the project index.",
                             _schema({"file": {"type": "string"}}, []), self.deps),
        }

    def simulate(self, args: dict) -> str:
        for name in args["files"]:
            path = self.bench.resolve(name)
            unsafe = unsafe_constructs(path.read_text(errors="replace")) if path.is_file() else []
            if unsafe:
                raise ValueError(f"{name} uses constructs Bartmoss won't run: {'; '.join(unsafe)}")
        return self.bench.run_sim(args)

    def synth(self, args: dict) -> str:
        files = self.bench._files(args["files"])
        run = synth_check(files[-1], args["top"], tuple(files[:-1]))
        if run is None:
            return "Yosys isn't installed, or the file is VHDL"
        findings = synth_findings(run.output)
        if findings is None:
            return f"Yosys couldn't elaborate the design:\n{tail(run.output, 30)}"
        return "\n".join(sorted(findings)) or "no latches, multiple drivers or logic loops"

    def wave(self, args: dict) -> str:
        from bartmoss.modes.wave import vcd_path

        path = vcd_path(self.ctx, args["file"])
        dump, signals, clock, unmatched, note = vcd.load(path, args.get("signals", []), args.get("window"),
                                                         args.get("clock"))
        text = vcd.summarise(dump, signals, clock, 150, Path(args["file"]).name, note)
        return text + (f"\nNo signal matched: {', '.join(unmatched)}\n" if unmatched else "")

    def formal(self, args: dict) -> str:
        from bartmoss.modes.formal import failed_lines, insert_properties, stage_files

        files = self.bench._files([*args.get("context", []), args["file"]])
        context = [(f, f.read_text(errors="replace")) for f in files[:-1]]
        source = insert_properties(files[-1].read_text(errors="replace"), args["top"], args["properties"])
        workdir = Path(tempfile.mkdtemp(prefix="bartmoss_mcp_formal_"))
        result = formal_check(stage_files(workdir, context, files[-1].name, source), args["top"], workdir,
                              args.get("depth", 20), args.get("prove", False))
        lines = [f"status: {result.status} ({result.stage})"]
        if result.status in ("fail", "unproven"):
            lines += ["failed: " + l for l in failed_lines(result, source)]
            if result.trace:
                dump, signals, clock, _, _ = vcd.load(result.trace, max_signals=24)
                lines.append(vcd.summarise(dump, signals, clock, 60, "counterexample"))
        elif result.status != "pass":
            lines.append(tail(result.output, 30))
        return "\n".join(lines)

    def deps(self, args: dict) -> str:
        project = self.ctx.project()
        if not args.get("file"):
            return project.summary()
        return "\n".join(self.ctx.workspace.relative(p) for p in project.dependencies(args["file"])) or "no dependencies"


def handle(tools: CheckerTools, request: dict) -> Optional[dict]:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, rid = request.get("method"), request.get("id")
    if rid is None:
        return None  # notifications such as notifications/initialized need no reply

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        version = (request.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return ok({"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "bartmoss", "version": __version__},
                   "instructions": "Free HDL checkers (Verilator, Icarus, NVC/GHDL, Yosys, yosys-smtbmc). Paths are "
                                   "relative to the Bartmoss workspace root. Prefer hdl_structure and wave_summary "
                                   "over reading large files or raw VCD."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in tools.tools.items()]})
    if method == "tools/call":
        params = request.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in tools.tools:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        _, schema, run = tools.tools[name]
        problem = validate(schema, args)
        if problem:
            return ok({"content": [{"type": "text", "text": f"error: {problem}"}], "isError": True})
        try:
            text, error = run(args), False
        except (WorkspaceError, ValueError, OSError) as e:
            text, error = f"error: {e}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": error})
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(ctx, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout,
          log: Callable[[str], None] = lambda m: print(m, file=sys.stderr)) -> int:
    tools = CheckerTools(ctx)
    log(f"bartmoss MCP server {__version__} on {ctx.workspace.root} ({len(tools.tools)} tools)")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle(tools, request) if isinstance(request, dict) else \
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batches aren't supported"}}
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
