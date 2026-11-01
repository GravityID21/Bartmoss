"""Offline tests for Phase 5: the tool sandbox (P5.4) and /flow (P5.2). No network, no API keys.
Tests that run real tools skip when they aren't installed."""

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bartmoss.sim import sandbox, tools
from bartmoss.sim.sandbox import SandboxError
from tests.test_core import HAS_VERILATOR, make_ctx

HAS_YOSYS = shutil.which("yosys") is not None

ALU = """module alu (input logic [3:0] a, b, input logic op, output logic [3:0] y);
  assign y = op ? a - b : a + b;
endmodule
"""
TB = """module tb_alu;
  logic [3:0] a, b, y;
  logic op;
  alu dut (.a, .b, .op, .y);
  initial begin
    a = 4'd3; b = 4'd1; op = 1'b0; #1;
    if (y !== 4'd4) begin $display("TEST FAILED"); $finish; end
    op = 1'b1; #1;
    if (y !== 4'd2) begin $display("TEST FAILED"); $finish; end
    $display("TEST PASSED");
    $finish;
  end
endmodule
"""


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bartmoss_test_"))
        self.src = self.tmp / "alu.sv"
        self.src.write_text(ALU)

    def refused(self, cmd, cwd=None):
        with self.assertRaises(SandboxError):
            sandbox.check(cmd, cwd or self.tmp)

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_allows_what_bartmoss_runs(self):
        sandbox.check(["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing", f"-I{self.tmp}", str(self.src)],
                      self.tmp)
        sandbox.check(["verilator", "--binary", "-j", "0", "--top-module", "alu", "-Mdir", "obj_dir", "-o", "sim",
                       str(self.src)], self.tmp)
        sandbox.check([str(self.tmp / "obj_dir" / "sim"), "+seed=3"], self.tmp)
        sandbox.check(["verilator", "--version"], self.tmp)

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_refuses_everything_else(self):
        self.refused("verilator --lint-only alu.sv")  # a shell string
        self.refused(["bash", "-c", "ls"])
        self.refused(["/bin/sh"])
        self.refused(["verilator", "-CFLAGS", "-O0", str(self.src)])  # unknown option
        self.refused(["verilator", "--exe", str(self.src)])
        self.refused(["verilator", "--top-module", "x;rm", str(self.src)])
        self.refused(["verilator", "-I", str(self.src)])  # -I must name a directory, attached
        self.refused(["verilator", "-Irelative/dir", str(self.src)])
        self.refused(["verilator", "--lint-only", "/etc/passwd"])  # not an HDL file
        self.refused(["verilator", "--lint-only", str(self.tmp / "missing.sv")])
        self.refused(["verilator", "--lint-only", str(self.src) + "\x00"])
        self.refused(["/bin/ls", "+seed=1"])  # only the binary Verilator built here may run
        self.refused([str(self.tmp / "obj_dir" / "sim"), "+seed=$(id)"])
        # Tools never run in the workspace, only in the temp dir.
        self.refused(["verilator", "--lint-only", str(self.src)], cwd=Path.home())

    @unittest.skipUnless(HAS_YOSYS, "needs yosys")
    def test_yosys_scripts(self):
        sandbox.check(["yosys", "-p", "hierarchy -check -top alu; proc; check; synth -top alu; stat", str(self.src)],
                      self.tmp)
        for script in ("shell ls", "!ls", "tee -o /tmp/x stat", "write_verilog /etc/x", "script evil.ys",
                       "read_verilog ../../etc/passwd"):
            self.refused(["yosys", "-p", script])

    def test_environment_has_no_secrets(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x", "GITHUB_TOKEN": "t", "MY_PASSWORD": "p",
                                          "PATH": "/usr/bin"}):
            env = sandbox.clean_env()
        self.assertEqual(env.get("PATH"), "/usr/bin")
        for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "MY_PASSWORD"):
            self.assertNotIn(name, env)

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_run_uses_absolute_executable_and_clean_env(self):
        with mock.patch("bartmoss.sim.tools.subprocess.run") as run, \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}):
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            tools.lint(self.src)
        cmd, kwargs = run.call_args[0][0], run.call_args[1]
        self.assertTrue(os.path.isabs(cmd[0]))
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertTrue(str(Path(kwargs["cwd"]).resolve()).startswith(str(sandbox.jail_root())))

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_includes_resolve_from_the_source_directory(self):
        (self.tmp / "defs.svh").write_text("localparam int W = 4;\n")
        top = self.tmp / "inc.sv"
        top.write_text('`include "defs.svh"\nmodule inc(input logic [W-1:0] a, output logic [W-1:0] y);\n'
                       "  assign y = a;\nendmodule\n")
        self.assertTrue(tools.lint(top).ok)

    def test_parse_stat_both_layouts(self):
        new = "=== top ===\n\n        +----------Local Count\n      268 cells\n        6   $_ANDNOT_\n" \
              "       32   $_DFFE_PN0P_\n        9   $_DFF_PN0_\n"
        old = "=== top ===\n\n   Number of wires:  10\n   Number of cells:  268\n     $_ANDNOT_   6\n" \
              "     $_DFFE_PN0P_   32\n     $_DFF_PN0_   9\n"
        for text in (new, old):
            stats = tools.parse_stat(text)
            self.assertEqual(stats["cells"], 268)
            self.assertEqual(stats["flops"], 41)
            self.assertEqual(stats["types"]["$_ANDNOT_"], 6)
        self.assertIsNone(tools.parse_stat("no statistics here"))


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "rtl").mkdir()
        (self.root / "tb").mkdir()
        (self.root / "rtl" / "alu.sv").write_text(ALU)
        (self.root / "tb" / "tb_alu.sv").write_text(TB)
        (self.root / "tb" / "tb_other.sv").write_text("module tb_other; alu u (.a(), .b(), .op(), .y()); endmodule\n")
        self.ctx = make_ctx(self.root)

    def test_finds_the_testbench_named_after_the_top(self):
        from bartmoss.modes.flow import find_testbench

        self.assertEqual(find_testbench(self.ctx, self.root / "rtl" / "alu.sv", "alu").name, "tb_alu.sv")

    @unittest.skipUnless(HAS_VERILATOR and HAS_YOSYS, "needs verilator and yosys")
    def test_dry_run_runs_nothing(self):
        from bartmoss.modes.flow import run_flow

        with mock.patch("bartmoss.sim.tools.subprocess.run", side_effect=AssertionError("ran a tool")):
            result = run_flow(self.ctx, "rtl/alu.sv", dry_run=True)
        self.assertEqual(result.status, "planned")
        steps = [s for s, _, _ in result.plan]
        self.assertEqual(sorted(set(steps)), ["lint", "sim", "synth"])
        out = self.ctx.ui.out.getvalue()
        self.assertIn("verilator --lint-only", out)
        self.assertIn("rtl/alu.sv", out)  # workspace paths shown relative
        self.assertIn("$TMP/obj_dir/sim", out)
        self.assertIn("formal: skipped", out)

    @unittest.skipUnless(HAS_VERILATOR and HAS_YOSYS, "needs verilator and yosys")
    def test_full_run_and_report(self):
        from bartmoss.modes.flow import run_flow

        result = run_flow(self.ctx, "rtl/alu.sv")  # make_ctx sets assume_yes
        self.assertEqual(result.status, "pass", self.ctx.ui.out.getvalue())
        by_name = {r.name: r for r in result.steps}
        self.assertEqual(by_name["sim"].status, "pass")
        self.assertIn("generic cells", by_name["synth"].summary)
        self.assertEqual(by_name["formal"].status, "skipped")
        report = result.report.read_text()
        self.assertIn("| sim | pass |", report)
        self.assertIn("verilator --binary", report)

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_lint_failure_stops_the_flow(self):
        from bartmoss.modes.flow import run_flow

        (self.root / "rtl" / "alu.sv").write_text(ALU.replace("assign y", "assign y y"))
        result = run_flow(self.ctx, "rtl/alu.sv", steps=("lint", "sim", "synth"), write=False)
        self.assertEqual(result.status, "fail")
        self.assertEqual([r.status for r in result.steps], ["fail", "skipped", "skipped"])

    @unittest.skipUnless(HAS_VERILATOR, "needs verilator")
    def test_asks_before_each_tool_and_stops_when_declined(self):
        from bartmoss.modes.flow import run_flow

        self.ctx.assume_yes = False
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ValueError):
                run_flow(self.ctx, "rtl/alu.sv")
        with mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", side_effect=["y", "n"]) as asked:
            result = run_flow(self.ctx, "rtl/alu.sv", steps=("lint", "sim", "synth"), write=False)
        self.assertEqual(asked.call_count, 2)  # lint approved, the sim compile declined, nothing asked after
        self.assertEqual(result.status, "declined")
        self.assertEqual([r.status for r in result.steps], ["pass", "declined", "declined"])

    def test_repl_rejects_unknown_steps(self):
        from bartmoss.cli import dispatch

        with self.assertRaises(ValueError):
            dispatch(self.ctx, "/flow rtl/alu.sv --steps lint,place")


if __name__ == "__main__":
    unittest.main()
