"""Offline tests for Phase 4 (agent loop, AST, guards, project index, MCP server): no network, no
API keys. Tests that need Verilator's --json-only skip when it isn't available."""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from bartmoss.files import Workspace, WorkspaceError
from bartmoss.providers.base import ChatResult
from bartmoss.ui import UI
from tests.test_core import HAS_VERILATOR, REPO, make_ctx


def has_json_ast() -> bool:
    if not HAS_VERILATOR:
        return False
    from bartmoss.sim import hdl_ast

    d = Path(tempfile.mkdtemp()) / "m.sv"
    d.write_text("module m(input logic a, output logic y); assign y = a; endmodule\n")
    return hdl_ast.parse([d])[0] is not None


HAS_AST = has_json_ast()
DESIGN = """package p;
  typedef logic [7:0] byte_t;
endpackage

module sub #(parameter int W = 4) (input logic clk, input logic [W-1:0] a, output logic [W-1:0] y);
  always_ff @(posedge clk) y <= a;
endmodule

module top import p::*; #(parameter int N = 8) (
  input logic clk,
  input byte_t din,
  output logic [N-1:0] dout
);
  logic [3:0] mid;
  sub #(.W(4)) u_sub (.clk(clk), .a(din[3:0]), .y(mid));
  assign dout = {4'b0, mid};
endmodule
"""
COUNTER = """// counts 0..9 and wraps
module cnt (input logic clk, input logic rst, output logic [3:0] q);
  always_ff @(posedge clk) q <= rst ? 4'd0 : (q == 4'd9 ? 4'd0 : q + 4'd1);
endmodule
"""


def fake_replies(*texts):
    replies = iter(texts)
    return lambda mode, messages, **kw: ChatResult(text=next(replies), model="fake")


class TestHdlAst(unittest.TestCase):
    @unittest.skipUnless(HAS_AST, "Verilator --json-only not available")
    def test_structure_and_interface(self):
        from bartmoss.sim import hdl_ast

        f = Path(tempfile.mkdtemp()) / "d.sv"
        f.write_text(DESIGN)
        modules, _ = hdl_ast.parse([f], "top")
        top = modules["top"]
        self.assertEqual([(p.name, p.kind) for p in top.ports], [("clk", "input"), ("din", "input"), ("dout", "output")])
        self.assertEqual(top.params[0].value, "8")
        self.assertEqual((top.instances[0].name, top.instances[0].module), ("u_sub", "sub"))
        text = hdl_ast.describe(modules, "top", signal="mid")
        self.assertIn("instance u_sub: sub", text)
        self.assertIn("assign line 16: writes dout; reads mid", text)
        self.assertIn("mid: driven at lines [15]; read at lines [16]", text)
        self.assertEqual(modules["p"].kind, "package")

    @unittest.skipUnless(HAS_AST, "Verilator --json-only not available")
    def test_parse_failure_returns_none(self):
        from bartmoss.sim import hdl_ast

        f = Path(tempfile.mkdtemp()) / "bad.sv"
        f.write_text("module bad(input a);\n assign y = ;\nendmodule\n")
        modules, run = hdl_ast.parse([f])
        self.assertIsNone(modules)
        self.assertIn("syntax error", run.output)

    def test_interface_changes(self):
        from bartmoss.sim.hdl_ast import interface_changes

        before = {"ports": {"a": ("input", "logic"), "y": ("output", "logic[3:0]")}, "params": {"W"}}
        after = {"ports": {"a": ("input", "logic"), "y": ("output", "logic[4:0]"), "z": ("output", "logic")}, "params": set()}
        self.assertEqual(interface_changes(before, after),
                         ["port y changed from output logic[3:0] to output logic[4:0]", "port z was added",
                          "parameter W was removed"])
        self.assertEqual(interface_changes(before, None), ["the module is missing or was renamed"])

    def test_unknown_connections(self):
        from bartmoss.sim.hdl_ast import named_connections, unknown_connections

        tb = "module tb; cnt #(.W(4), .DEPTH(2)) dut (.clk(clk), .rst(rst), .qq(q)); // .fake(x)\nendmodule"
        iface = {"ports": {"clk": ("input", "logic"), "rst": ("input", "logic"), "q": ("output", "logic")}, "params": {"W"}}
        self.assertEqual(unknown_connections(tb, "cnt", iface), ["port qq", "parameter DEPTH"])
        self.assertIsNone(named_connections("module tb; cnt dut (.*); endmodule", "cnt"))


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.root)
        (self.root / "cnt.sv").write_text(COUNTER)

    @unittest.skipUnless(HAS_AST, "Verilator --json-only not available")
    def test_debug_pushes_back_once_on_interface_change(self):
        from bartmoss.modes.debug import run_debug

        renamed = COUNTER.replace("output logic [3:0] q", "output logic [3:0] count").replace(" q ", " count ") \
            .replace("(q ==", "(count ==").replace(": q +", ": count +")
        reply = f"<analysis>x</analysis><fixed_file>{renamed}</fixed_file>"
        self.ctx.ask_model = fake_replies(reply, reply)
        result = run_debug(self.ctx, "cnt.sv", write=False)
        self.assertEqual(result.attempts, 2)  # one push-back, then the repeated change is accepted
        self.assertEqual(result.status, "interface_changed")
        self.assertIn("port q was removed", result.interface_changes)
        self.ctx.ask_model = fake_replies(reply, f"<analysis>x</analysis><fixed_file>{COUNTER}</fixed_file>")
        self.assertEqual(run_debug(self.ctx, "cnt.sv", write=False).status, "clean")

    @unittest.skipUnless(HAS_AST, "Verilator --json-only not available")
    def test_tb_rejects_unknown_port_and_unsafe_code(self):
        from bartmoss.modes.tb import run_tb

        good = ("<tb_file>`timescale 1ns/1ps\nmodule tb_cnt; logic clk = 0, rst = 1; logic [3:0] q;\n"
                "cnt dut (.clk(clk), .rst(rst), .q(q));\ninitial begin $display(\"TEST PASSED\"); $finish; end\n"
                "endmodule</tb_file><notes>n</notes>")
        wrong_pin = good.replace(".q(q)", ".count(q)")
        unsafe = good.replace('$display("TEST PASSED");', '$system("ls"); $display("TEST PASSED");')
        self.ctx.ask_model = fake_replies(wrong_pin, unsafe, good)
        result = run_tb(self.ctx, "cnt.sv", write=False)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.verdict, "pass")

    def test_wave_diagnosis_must_name_a_known_signal(self):
        from bartmoss.modes.wave import run_wave
        from tests.test_phase3 import write_vcd

        write_vcd(self.root)
        bad = "<analysis>a</analysis><diagnosis>\nsignal: tb.dut.made_up\ntime: 30\n</diagnosis>"
        good = "<analysis>a</analysis><diagnosis>\nsignal: dut.data\ntime: 30\n</diagnosis>"
        self.ctx.ask_model = fake_replies(bad, good)
        result = run_wave(self.ctx, "w.vcd", ["dut.*"], write=False)
        self.assertEqual((result.attempts, result.signal), (2, "dut.data"))

    def test_unsafe_constructs(self):
        from bartmoss.sim.tools import unsafe_constructs

        self.assertEqual(unsafe_constructs('initial $dumpfile("wave.vcd");'), [])
        self.assertTrue(unsafe_constructs('initial $dumpfile("/tmp/x.vcd");'))
        self.assertTrue(unsafe_constructs('import "DPI-C" function int f();'))
        self.assertTrue(unsafe_constructs("initial $readmemh(\"/etc/passwd\", mem);"))
        self.assertTrue(unsafe_constructs("file f : text open read_mode is \"x\";"))


class TestProject(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for rel, text in {
            "rtl/pkg.sv": "package bus_pkg; typedef logic [7:0] word_t; endpackage\n",
            "rtl/bus_if.sv": "interface bus_if; logic valid; endinterface\n",
            "rtl/leaf.sv": "module leaf(input logic a, output logic y); assign y = a; endmodule\n",
            "rtl/mid.sv": "module mid import bus_pkg::*; (bus_if b, input word_t d);\n  leaf u (.a(d[0]), .y());\nendmodule\n",
            "rtl/top.sv": "// uses leaf in a comment only\nmodule top; bus_if b(); mid u_mid (.b(b), .d('0)); endmodule\n",
            "tb/tb_top.sv": "module tb_top; top dut(); endmodule\n",
            "rtl/inc/defs.svh": "`define W 8\n",
            "alt/leaf.sv": "module leaf(input logic a, output logic y); assign y = ~a; endmodule\n",
        }.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        self.ws = Workspace(self.root, "out")

    def test_scan_roles_and_dependencies(self):
        from bartmoss.project import Project

        project = Project(self.ws)
        roles = {self.ws.relative(p): f.role for p, f in project.files.items()}
        self.assertEqual(roles["tb/tb_top.sv"], "tb")
        self.assertEqual(roles["rtl/inc/defs.svh"], "header")
        deps = [self.ws.relative(p) for p in project.dependencies("rtl/top.sv")]
        self.assertEqual(deps, ["rtl/pkg.sv", "rtl/bus_if.sv", "rtl/leaf.sv", "rtl/mid.sv"])
        self.assertIn("top", project.tops())
        self.assertIn("leaf is defined in 2 files", project.summary())

    def test_filelist(self):
        from bartmoss.project import Project, parse_filelist

        os.environ["BARTMOSS_TEST_RTL"] = "rtl"
        (self.root / "sub.f").write_text("$BARTMOSS_TEST_RTL/leaf.sv  // the leaf\n")
        (self.root / "files.f").write_text("# design\n+incdir+rtl/inc\n+define+SIM+W=8\n-f sub.f\n${BARTMOSS_TEST_RTL}/top.sv\n")
        files, incdirs, defines = parse_filelist(self.ws, "files.f")
        self.assertEqual([self.ws.relative(f) for f in files], ["rtl/leaf.sv", "rtl/top.sv"])
        self.assertEqual([self.ws.relative(d) for d in incdirs], ["rtl/inc"])
        self.assertEqual(defines, ["SIM", "W=8"])
        self.assertEqual(len(Project(self.ws, "files.f").files), 2)
        (self.root / "evil.f").write_text("../../etc/passwd\n")
        with self.assertRaises(WorkspaceError):
            parse_filelist(self.ws, "evil.f")

    def test_auto_context_and_opt_out(self):
        ctx = make_ctx(self.root)
        files = ctx.read_context_files([], for_file=self.root / "rtl" / "mid.sv")
        self.assertEqual([p.name for p, _ in files], ["pkg.sv", "bus_if.sv", "leaf.sv"])
        ctx.config["project"]["auto_context"] = False
        self.assertEqual(ctx.read_context_files([], for_file=self.root / "rtl" / "mid.sv"), [])
        ctx.config["project"]["auto_context"] = True
        explicit = ctx.read_context_files(["rtl/leaf.sv"], for_file=self.root / "rtl" / "mid.sv")
        self.assertEqual([p.name for p, _ in explicit], ["leaf.sv"])


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.root)
        (self.root / "cnt.sv").write_text(COUNTER)
        (self.root / "tb_ref.sv").write_text("module tb_ref; endmodule\n")

    def test_validate(self):
        from bartmoss.agent import _schema, validate

        schema = _schema({"path": {"type": "string"}, "start_line": {"type": "integer"}}, ["path"])
        self.assertIsNone(validate(schema, {"path": "a"}))
        self.assertIn("missing", validate(schema, {}))
        self.assertIn("must be an integer", validate(schema, {"path": "a", "start_line": True}))
        self.assertIn("unknown argument", validate(schema, {"path": "a", "x": 1}))
        self.assertIn("valid JSON", validate(schema, {"_invalid_json": "{"}))

    def test_workbench_hides_files_and_guards_scratch(self):
        from bartmoss.agent import Workbench

        bench = Workbench(self.ctx, visible=["cnt.sv"])
        self.assertIn("cnt.sv", bench.list_dir({}))
        self.assertNotIn("tb_ref", bench.list_dir({}))
        with self.assertRaises(WorkspaceError):
            bench.read_file({"path": "tb_ref.sv"})
        self.assertNotIn("tb_ref", bench.search({"pattern": "module"}))
        with self.assertRaises(WorkspaceError):
            bench.resolve("scratch/../escape.sv")
        bench.write_scratch({"name": "tb_x.sv", "content": 'module tb_x; initial $system("rm -rf /"); endmodule\n'})
        with self.assertRaises(ValueError):
            bench.run_sim({"files": ["scratch/tb_x.sv"], "top": "tb_x"})
        self.ctx.config["agent"]["max_read_lines"] = 2
        self.assertIn("ask for start_line=3", bench.read_file({"path": "cnt.sv"}))

    def test_loop_runs_tools_then_answers(self):
        from bartmoss.agent import AgentSession, Workbench

        calls = []

        def model(mode, messages, **kw):
            calls.append(kw)
            if sum(m["role"] == "tool" for m in messages) == 0:
                return ChatResult(text="", model="m", stop_reason="tool_use",
                                  tool_calls=[{"id": "1", "name": "read_file", "input": {"path": "cnt.sv"}},
                                              {"id": "2", "name": "no_such_tool", "input": {}}])
            return ChatResult(text="done", model="m", stop_reason="end_turn")

        self.ctx.ask_model = model
        session = AgentSession(self.ctx, "debug", "debug_agent", Workbench(self.ctx))
        reply = session.ask([{"role": "user", "content": "go"}])
        self.assertEqual(reply.text, "done")
        results = session.transcript[2]["results"]
        self.assertIn("counts 0..9", results[0]["content"])
        self.assertTrue(results[1]["is_error"])
        self.assertEqual([m["role"] for m in session.transcript], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(calls[0]["tools"][0]["name"], "list_dir")

    def test_budget_asks_for_final_answer_without_editing_history(self):
        from bartmoss.agent import AgentSession, Workbench

        self.ctx.config["agent"]["context_budget"] = 10
        seen = []

        def model(mode, messages, **kw):
            seen.append(kw.get("tool_choice"))
            return ChatResult(text="t", model="m", input_tokens=50, stop_reason="tool_use",
                              tool_calls=[{"id": str(len(seen)), "name": "list_dir", "input": {}}])

        self.ctx.ask_model = model
        session = AgentSession(self.ctx, "debug", "debug_agent", Workbench(self.ctx))
        session.ask([{"role": "user", "content": "go"}])
        self.assertEqual(seen, [None, "none"])
        self.assertEqual(session.stopped, "budget")
        self.assertIn("Budget reached", session.transcript[2]["note"])

    def test_truncated_and_declined_calls_are_not_run(self):
        from bartmoss.agent import AgentSession, Workbench

        self.ctx.assume_yes = False
        self.ctx.ui.confirm = lambda question: False
        replies = iter([
            ChatResult(text="", model="m", stop_reason="max_tokens",
                       tool_calls=[{"id": "1", "name": "write_scratch", "input": {"name": "a.sv", "content": "mod"}}]),
            ChatResult(text="", model="m", stop_reason="tool_use",
                       tool_calls=[{"id": "2", "name": "run_sim", "input": {"files": ["cnt.sv"], "top": "cnt"}}]),
            ChatResult(text="final", model="m", stop_reason="end_turn"),
        ])
        self.ctx.ask_model = lambda mode, messages, **kw: next(replies)
        bench = Workbench(self.ctx)
        session = AgentSession(self.ctx, "debug", "debug_agent", bench)
        session.ask([{"role": "user", "content": "go"}])
        self.assertIn("max_tokens", session.transcript[2]["results"][0]["content"])
        self.assertFalse((bench.scratch / "a.sv").exists())
        self.assertIn("declined", session.transcript[4]["results"][0]["content"])
        self.assertEqual(bench.sim_runs, 0)

    def test_debug_agent_with_mock(self):
        from bartmoss.modes.debug import run_debug

        result = run_debug(self.ctx, "cnt.sv", agent=True)
        self.assertEqual(result.agent_steps, 3)
        self.assertEqual(result.tool_calls, 2)
        self.assertIn(result.status, ("clean", "unchecked"))
        self.assertEqual((self.root / "out" / "cnt.fixed.sv").read_text().strip(), COUNTER.strip())

    def test_debug_agent_eval_hides_reference_tb(self):
        from bartmoss.evals import run_eval

        root = Path(tempfile.mkdtemp()) / "cases"
        shutil.copytree(REPO / "evals" / "cases" / "debug" / "counter_wrap", root / "debug" / "counter_wrap")
        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.ui = UI(color=False, out=io.StringIO())
        if not HAS_VERILATOR:
            self.skipTest("verilator not installed")
        report = run_eval(ctx, "debug", root, agent=True)
        self.assertEqual(report["prompt"], "debug_agent")
        run = report["cases"][0]["runs"][0]
        self.assertEqual(run["agent_steps"], 3)
        self.assertEqual(run["score"], 0.25)  # the mock returns the buggy file unchanged
        listing = (root.parent / "results").glob("*_p-debug-agent.json")
        self.assertEqual(len(list(listing)), 1)


class TestProviderMessages(unittest.TestCase):
    TRANSCRIPT = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "read_file", "input": {"path": "a"}}],
         "raw": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}]},
        {"role": "tool", "results": [{"id": "t1", "content": "", "is_error": True}], "note": "wrap up"},
    ]

    def test_claude_shapes(self):
        from bartmoss.providers.anthropic_provider import to_claude_messages

        out = to_claude_messages(self.TRANSCRIPT)
        self.assertEqual(out[1], {"role": "assistant", "content": self.TRANSCRIPT[1]["raw"]})
        self.assertEqual(out[2]["content"][0], {"type": "tool_result", "tool_use_id": "t1", "content": "(no output)",
                                                "is_error": True})
        self.assertEqual(out[2]["content"][1], {"type": "text", "text": "wrap up"})

    def test_openai_shapes(self):
        from bartmoss.providers.openai_compat import to_openai_messages

        out = to_openai_messages(self.TRANSCRIPT)
        self.assertEqual(out[1]["tool_calls"][0]["function"], {"name": "read_file", "arguments": '{"path": "a"}'})
        self.assertEqual(out[2], {"role": "tool", "tool_call_id": "t1", "content": "(no output)"})
        self.assertEqual(out[3], {"role": "user", "content": "wrap up"})


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "cnt.sv").write_text(COUNTER)
        (self.root / "tb_evil.sv").write_text('module tb_evil; initial $system("ls"); endmodule\n')
        self.ctx = make_ctx(self.root)

    def rpc(self, *messages):
        from bartmoss.mcp_server import serve

        out = io.StringIO()
        serve(self.ctx, io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), out, log=lambda m: None)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_protocol(self):
        replies = self.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "project_deps", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "simulate", "arguments": {"files": ["tb_evil.sv"], "top": "tb_evil"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        )
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])  # no reply to the notification
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "bartmoss")
        self.assertIn("formal_check", [t["name"] for t in replies[1]["result"]["tools"]])
        self.assertIn("module cnt", replies[2]["result"]["content"][0]["text"])
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertIn("$system", replies[3]["result"]["content"][0]["text"])
        self.assertEqual(replies[4]["error"]["code"], -32601)

    def test_outside_workspace_is_an_error(self):
        reply = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "lint", "arguments": {"files": ["/etc/hosts"]}}})[0]
        self.assertTrue(reply["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
