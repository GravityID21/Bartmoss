"""Tool-use loop for agentic modes (SPEC P4.1) with a context budget (P4.3).

The model can explore the workspace and run the free tools itself: list_dir, read_file, search,
hdl_structure (Verilator AST), run_lint, write_scratch (a temp dir, never the workspace) and run_sim
(compiles and runs, and can summarise a VCD the testbench dumps). Read-only tools and the scratch
writer are auto-approved; run_sim runs model-written code, so it needs the user's OK unless --yes or
agent.auto_approve says otherwise, and code using $system, file I/O or DPI is refused outright.

History is append-only, so prompt caching and thinking blocks stay valid. The budget is kept by
capping each tool result when it's produced, and by asking for a final answer (tool_choice "none")
once the context passes agent.context_budget tokens or the step limit is near, never by editing
earlier turns.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bartmoss.files import WorkspaceError
from bartmoss.project import SKIP_DIRS
from bartmoss.providers.base import ChatResult
from bartmoss.sim import hdl_ast, vcd
from bartmoss.sim.loop import hit_token_limit
from bartmoss.sim.tools import lint, pick_simulator, simulate, tail, tb_verdict, unsafe_constructs

SCRATCH = "scratch/"  # tool paths starting with this live in the agent's temp dir


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], str]
    auto: bool  # runs without asking: no side effects on the workspace, no model-written code executed

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _schema(properties: dict, required: list) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TYPES = {"string": str, "integer": int, "boolean": bool, "array": list}


def validate(schema: dict, args: dict) -> Optional[str]:
    """Why `args` doesn't fit a tool's input schema, or None. Tool inputs stream without server-side
    validation (eager_input_streaming), so they're checked here before anything runs."""
    if not isinstance(args, dict) or "_invalid_json" in args:
        return "the tool input wasn't valid JSON"
    props = schema["properties"]
    for name in schema.get("required", []):
        if name not in args:
            return f"missing required argument {name!r}"
    for name, value in args.items():
        if name not in props:
            return f"unknown argument {name!r}"
        expected = TYPES.get(props[name].get("type"))
        if expected and not (isinstance(value, expected) and not (expected is int and isinstance(value, bool))):
            kind = props[name]["type"]
            return f"argument {name!r} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}"
    return None


class Workbench:
    """The tools, bound to one workspace and one scratch dir. `visible` limits what the model can see
    (evals hide reference testbenches and mutants that sit next to the case file)."""

    def __init__(self, ctx, visible: Optional[set] = None):
        self.ctx = ctx
        self.cfg = ctx.config.get("agent", {})
        self.scratch = Path(tempfile.mkdtemp(prefix="bartmoss_agent_"))
        self.visible = {ctx.workspace.resolve(p) for p in visible} if visible is not None else None
        self.sim_runs = 0
        self.tools = {t.name: t for t in self._tools()}

    def resolve(self, path: str) -> Path:
        if path.startswith(SCRATCH):
            name = path[len(SCRATCH):]
            if not re.fullmatch(r"[\w.-]+", name):
                raise WorkspaceError(f"scratch file names must be plain names like tb_x.sv, got {name!r}")
            return self.scratch / name
        resolved = self.ctx.workspace.resolve(path)
        if self.visible is not None and resolved not in self.visible and not resolved.is_dir():
            raise WorkspaceError(f"{path} is not available")
        return resolved

    def _visible(self, path: Path) -> bool:
        return self.visible is None or path in self.visible

    def _files(self, paths: list) -> list[Path]:
        files = [self.resolve(p) for p in paths]
        missing = [str(p) for p, f in zip(paths, files) if not f.is_file()]
        if missing:
            raise WorkspaceError("not found: " + ", ".join(missing))
        return files

    # ---- tools -------------------------------------------------------------------------------

    def list_dir(self, args: dict) -> str:
        if args.get("path", ".").startswith(SCRATCH.rstrip("/")):
            entries = sorted(self.scratch.iterdir())
            return "\n".join(f"{SCRATCH}{p.name}  {p.stat().st_size} B" for p in entries) or "(scratch is empty)"
        base = self.ctx.workspace.resolve(args.get("path", "."))
        if not base.is_dir():
            raise WorkspaceError(f"{args.get('path')} is not a directory")
        lines = []
        for p in sorted(base.iterdir()):
            if p.name.startswith(".") or p.name in SKIP_DIRS:
                continue
            if p.is_dir():
                if self.visible is None or any(base_p == p or p in base_p.parents for base_p in self.visible):
                    lines.append(f"{self.ctx.workspace.relative(p)}/")
            elif self._visible(p):
                lines.append(f"{self.ctx.workspace.relative(p)}  {p.stat().st_size} B")
        return "\n".join(lines[:200]) or "(empty)"

    def read_file(self, args: dict) -> str:
        path = self.resolve(args["path"])
        if not path.is_file():
            raise WorkspaceError(f"{args['path']} not found")
        lines = path.read_text(errors="replace").splitlines()
        start = max(1, args.get("start_line", 1))
        limit = self.cfg.get("max_read_lines", 400)
        end = min(len(lines), args.get("end_line", start + limit - 1), start + limit - 1)
        body = "\n".join(f"{n:5d} | {lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n(lines {start}-{end} of {len(lines)}; ask for start_line={end + 1} for more)" if end < len(lines) else ""
        return body + more

    def search(self, args: dict) -> str:
        try:
            pattern = re.compile(args["pattern"])
        except re.error as e:
            raise ValueError(f"bad regex: {e}")
        hits = []
        for f in sorted(self.ctx.project().files) + sorted(self.scratch.iterdir()):
            if f.parent != self.scratch and not self._visible(f):
                continue
            for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    name = SCRATCH + f.name if f.parent == self.scratch else self.ctx.workspace.relative(f)
                    hits.append(f"{name}:{n}: {line.strip()}")
                    if len(hits) >= 60:
                        return "\n".join(hits) + "\n(first 60 matches)"
        return "\n".join(hits) or "no matches"

    def hdl_structure(self, args: dict) -> str:
        modules, run = hdl_ast.parse(self._files(args["files"]), args.get("top"))
        if modules is None:
            reason = tail(run.output, 30) if run else "Verilator (5.0xx with --json-only) isn't installed"
            return f"couldn't parse the design:\n{reason}"
        return hdl_ast.describe(modules, args.get("module"), args.get("signal"))

    def run_lint(self, args: dict) -> str:
        files = self._files(args["files"])
        result = lint(files[-1], tuple(files[:-1]))
        if result is None:
            return "no linter installed"
        return f"{result.tool} lint, {'no errors' if result.ok else 'errors'}:\n{tail(result.output, 60) or '(no messages)'}"

    def write_scratch(self, args: dict) -> str:
        if not re.fullmatch(r"[\w.-]+", args["name"]):
            raise ValueError("use a plain file name such as tb_fifo.sv")
        target = self.scratch / args["name"]
        target.write_text(args["content"])
        return f"wrote {SCRATCH}{args['name']} ({len(args['content'].splitlines())} lines)"

    def run_sim(self, args: dict) -> str:
        files = self._files(args["files"])
        for f in files:
            if f.parent == self.scratch:
                unsafe = unsafe_constructs(f.read_text(errors="replace"))
                if unsafe:
                    raise ValueError(f"{SCRATCH}{f.name} uses constructs Bartmoss won't run: {'; '.join(unsafe)}")
        simulator = pick_simulator(self.ctx.config.get("simulator", "auto"), vhdl=files[-1].suffix in (".vhd", ".vhdl"))
        if simulator is None:
            return "no simulator installed"
        self.sim_runs += 1
        workdir = self.scratch / f"sim{self.sim_runs}"
        run = simulate(files, args["top"], workdir, simulator, self.cfg.get("sim_timeout_s", 120))
        out = [f"{simulator} {run.stage}: {'ok' if run.ok else 'failed'}" + (" (timed out)" if run.timed_out else ""),
               f"verdict: {tb_verdict(run.output)}" if run.stage == "run" else "", tail(run.output, 50)]
        dump = workdir / "wave.vcd"
        if dump.is_file():
            loaded = vcd.load(dump, args.get("signals", []), args.get("window"), max_signals=16)
            out.append(vcd.summarise(loaded[0], loaded[1], loaded[2], 80, "wave.vcd", loaded[4]))
        return "\n".join(x for x in out if x)

    def _tools(self) -> list[Tool]:
        files = {"type": "array", "items": {"type": "string"},
                 "description": "workspace paths, or scratch/<name> for files you wrote; dependencies first"}
        return [
            Tool("list_dir", "List files in a workspace directory (or 'scratch').",
                 _schema({"path": {"type": "string", "description": "directory, default '.'"}}, []), self.list_dir, True),
            Tool("read_file", "Read a file with line numbers. Long files come in chunks; use start_line/end_line.",
                 _schema({"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
                         ["path"]), self.read_file, True),
            Tool("search", "Regex search over the project's HDL files and scratch files; returns file:line matches.",
                 _schema({"pattern": {"type": "string"}}, ["pattern"]), self.search, True),
            Tool("hdl_structure", "Parse files with Verilator and list modules, parameters, ports, internal signals, "
                 "instances and always/assign blocks with the signals each writes and reads. With `signal`, also the "
                 "lines that drive and read it. Much cheaper than reading whole files.",
                 _schema({"files": files, "top": {"type": "string"}, "module": {"type": "string"},
                          "signal": {"type": "string"}}, ["files"]), self.hdl_structure, True),
            Tool("run_lint", "Lint files with Verilator -Wall (or NVC/GHDL for VHDL). The last file is the one checked.",
                 _schema({"files": files}, ["files"]), self.run_lint, True),
            Tool("write_scratch", "Write a file (for example a small testbench or a candidate fix) to the scratch "
                 "directory, as scratch/<name>. It never touches the user's files.",
                 _schema({"name": {"type": "string"}, "content": {"type": "string"}}, ["name", "content"]),
                 self.write_scratch, True),
            Tool("run_sim", "Compile and run a simulation with `top` as the root module. A self-checking testbench "
                 "should print TEST PASSED or TEST FAILED and call $finish. If it calls $dumpfile(\"wave.vcd\") and "
                 "$dumpvars, a waveform summary of `signals` (globs) within `window` (e.g. 0:500) is returned too.",
                 _schema({"files": files, "top": {"type": "string"},
                          "signals": {"type": "array", "items": {"type": "string"}}, "window": {"type": "string"}},
                         ["files", "top"]), self.run_sim, False),
        ]


@dataclass
class AgentSession:
    """One agent conversation. `ask(messages)` fits generate_with_checks: the first call starts the
    transcript and later calls append only the newest user message (check feedback), so tool turns
    survive retries and the history stays append-only."""

    ctx: object
    mode: str
    prompt: str
    bench: Workbench
    transcript: list = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    stopped: str = ""  # "" | budget | steps | truncated

    def ask(self, messages: list[dict]) -> ChatResult:
        if not self.transcript:
            self.transcript.append(messages[0])
        else:
            self.transcript.append(messages[-1])
        return self.loop()

    def _approved(self, tool: Tool, args: dict) -> bool:
        cfg = self.ctx.config.get("agent", {})
        if tool.auto or self.ctx.assume_yes or tool.name in cfg.get("auto_approve", []):
            return True
        shown = ", ".join(f"{k}={v!r}"[:80] for k, v in args.items())
        return self.ctx.ui.confirm(f"Allow the model to {tool.name}({shown})?")

    def loop(self) -> ChatResult:
        cfg = self.ctx.config.get("agent", {})
        max_steps, budget = cfg.get("max_steps", 20), cfg.get("context_budget", 100_000)
        max_chars = cfg.get("max_result_chars", 12_000)
        ui = self.ctx.ui
        specs = [t.spec() for t in self.bench.tools.values()]
        wrap_up = False
        while True:
            reply = self.ctx.ask_model(self.mode, self.transcript, prompt=self.prompt, tools=specs,
                                       tool_choice="none" if wrap_up else None)
            self.steps += 1
            self.transcript.append({"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls,
                                    "raw": reply.raw})
            if reply.refused or not reply.tool_calls:
                return reply
            if wrap_up:
                self.stopped = self.stopped or "budget"
                return reply
            results = []
            for call in reply.tool_calls:
                tool = self.bench.tools.get(call["name"])
                problem = "unknown tool" if tool is None else validate(tool.input_schema, call["input"])
                if hit_token_limit(reply.stop_reason):
                    # A tool input cut off at max_tokens can parse as a valid but truncated object.
                    problem = "the reply hit max_tokens before this tool input was complete; send it again, shorter"
                    self.stopped = "truncated"
                if problem is None and not self._approved(tool, call["input"]):
                    problem = "the user declined this tool call"
                if problem:
                    results.append({"id": call["id"], "content": f"error: {problem}", "is_error": True})
                    continue
                ui.dim(f"  tool {call['name']}({_brief(call['input'])})")
                self.tool_calls += 1
                try:
                    output = tool.run(call["input"])
                    error = False
                except (WorkspaceError, ValueError, OSError) as e:
                    output, error = f"error: {e}", True
                if len(output) > max_chars:
                    half = max_chars // 2
                    output = (output[:half] + f"\n... ({len(output) - max_chars} characters cut; narrow the request) ...\n"
                              + output[-half:])
                results.append({"id": call["id"], "content": output, "is_error": error})
            context = reply.input_tokens + reply.cache_read_tokens + reply.cache_write_tokens
            note = None
            if context > budget or self.steps >= max_steps - 1:
                wrap_up = True
                self.stopped = "budget" if context > budget else "steps"
                note = ("Budget reached: don't call any more tools. Give your final answer now in the required "
                        "format, using what you've found so far.")
                ui.dim(f"  agent {'context budget' if context > budget else 'step limit'} reached; asking for the answer")
            self.transcript.append({"role": "tool", "results": results, **({"note": note} if note else {})})


def _brief(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if k == "content":
            v = f"<{len(str(v).splitlines())} lines>"
        parts.append(f"{k}={v}" if not isinstance(v, list) else f"{k}=[{', '.join(map(str, v))}]")
    return ", ".join(parts)[:100]
