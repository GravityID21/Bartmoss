"""Offline tests: no network, no API keys. Simulator tests skip when Verilator isn't installed."""

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from bartmoss.config import DEFAULTS, deep_merge, parse_model_spec
from bartmoss.context import Context
from bartmoss.extract import hdl_block, tag, top_module
from bartmoss.files import Workspace, WorkspaceError
from bartmoss.sim.tools import lint_errors, tb_verdict
from bartmoss.ui import UI
from bartmoss.usage import Usage

REPO = Path(__file__).resolve().parent.parent
HAS_VERILATOR = shutil.which("verilator") is not None
HAS_VHDL_SIM = bool(shutil.which("nvc") or shutil.which("ghdl"))
VHDL_INV = """library ieee;
use ieee.std_logic_1164.all;

entity inv is
    port (a : in std_logic; y : out std_logic);
end entity;

architecture rtl of inv is
begin
    y <= not a;
end architecture;
"""


def make_ctx(root: Path) -> Context:
    import copy

    config = copy.deepcopy(DEFAULTS)
    return Context(config=config, workspace=Workspace(root, "out"), ui=UI(color=False, out=io.StringIO()),
                   usage=Usage(), model_override="mock:echo", assume_yes=True)


class TestConfig(unittest.TestCase):
    def test_parse_model_spec_keeps_ollama_tag(self):
        self.assertEqual(parse_model_spec("ollama:qwen2.5-coder:14b"), ("ollama", "qwen2.5-coder:14b"))

    def test_parse_model_spec_rejects_bare_name(self):
        with self.assertRaises(ValueError):
            parse_model_spec("claude-opus-5-5")

    def test_deep_merge(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
        self.assertEqual(merged, {"a": {"b": 1, "c": 3}})


class TestExtract(unittest.TestCase):
    def test_tag_strips_inner_fence(self):
        text = "<fixed_file>\n```systemverilog\nmodule a; endmodule\n```\n</fixed_file>"
        self.assertEqual(tag(text, "fixed_file"), "module a; endmodule\n")

    def test_hdl_block_prefers_hdl(self):
        text = "```text\nlots of words here\n```\n```verilog\nmodule x; endmodule\n```"
        self.assertEqual(hdl_block(text), "module x; endmodule\n")

    def test_top_module_ignores_comments(self):
        src = "// module fake;\n/* module also_fake; */\nmodule real_one #(parameter N=1) (input a);\nendmodule"
        self.assertEqual(top_module(src), "real_one")


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_rejects_escape(self):
        ws = Workspace(self.root)
        with self.assertRaises(WorkspaceError):
            ws.resolve("../outside.sv")

    def test_rejects_symlink_escape(self):
        outside = Path(tempfile.mkdtemp()) / "secret.sv"
        outside.write_text("x")
        os.symlink(outside, self.root / "link.sv")
        with self.assertRaises(WorkspaceError):
            Workspace(self.root).read_text("link.sv")

    def test_overwrite_needs_confirmation(self):
        ws = Workspace(self.root, "out")
        ws.write_text("a.sv", "one")
        self.assertIsNone(ws.write_text("a.sv", "two", confirm=lambda p, d: False))
        self.assertEqual((self.root / "out" / "a.sv").read_text(), "one")
        self.assertIsNotNone(ws.write_text("a.sv", "two", confirm=lambda p, d: True))
        self.assertEqual((self.root / "out" / "a.sv").read_text(), "two")


class TestSimHelpers(unittest.TestCase):
    def test_verdict(self):
        self.assertEqual(tb_verdict("x\nTEST PASSED\n"), "pass")
        self.assertEqual(tb_verdict("ERROR: a\nTEST FAILED: 1 errors"), "fail")
        self.assertEqual(tb_verdict("nothing"), "unknown")

    def test_lint_errors_vhdl_formats(self):
        nvc = lint_errors("** Error: no visible declaration for FOO\n    > /tmp/a.vhd:3\n** Warning: unused")
        ghdl = lint_errors("/tmp/a.vhd:3:5: no declaration for \"foo\"\n/tmp/a.vhd:4:1:warning: unused")
        self.assertEqual(nvc, {"no visible declaration for FOO"})
        self.assertEqual(ghdl, {'no declaration for "foo"'})

    def test_vhdl_verdict(self):
        self.assertEqual(tb_verdict("** Note: 10ns+0: TEST PASSED"), "pass")
        self.assertEqual(tb_verdict("** Failure: 5ns+0: Assertion violation."), "fail")

    def test_lint_errors_ignore_location(self):
        a = lint_errors("%Error: /tmp/a/x.sv:3:5: syntax error, unexpected endmodule\n%Error: Exiting due to 1 error(s)")
        b = lint_errors("%Error: /other/x.sv:9:1: syntax error, unexpected endmodule")
        self.assertEqual(a, b)


class TestLoop(unittest.TestCase):
    def test_keeps_last_output_when_final_reply_is_empty(self):
        from bartmoss.providers.base import ChatResult
        from bartmoss.sim.loop import CheckOutcome, generate_with_checks

        replies = iter(["<tb_file>broken</tb_file>", "no tags at all"])
        ask = lambda messages: ChatResult(text=next(replies), model="m")

        def check(text):
            if "<tb_file>" in text:
                return CheckOutcome(False, True, "fix it", data={"tb": "broken"})
            return CheckOutcome(False, True, "missing tag")

        loop = generate_with_checks(ask, "go", check, max_retries=1)
        self.assertEqual(loop.attempts, 2)
        self.assertEqual(loop.outcome.data, {})
        self.assertEqual(loop.last_data, {"tb": "broken"})


class TestModesWithMock(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.root)

    def test_ask(self):
        from bartmoss.modes.ask import run_ask

        self.assertIn("setup time", run_ask(self.ctx, "what is setup time?"))
        self.assertEqual(len(self.ctx.ask_history), 2)

    def test_debug_writes_fixed_copy(self):
        from bartmoss.modes.debug import run_debug

        (self.root / "inv.sv").write_text("module inv(input logic a, output logic y);\n  assign y = ~a;\nendmodule\n")
        result = run_debug(self.ctx, "inv.sv")
        self.assertIsNotNone(result.written)
        self.assertEqual(result.written.name, "inv.fixed.sv")
        self.assertIn(result.status, ("clean", "unchecked"))

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_tb_runs_in_simulator(self):
        from bartmoss.modes.tb import run_tb

        (self.root / "inv.sv").write_text("module inv(input logic a, output logic y);\n  assign y = ~a;\nendmodule\n")
        result = run_tb(self.ctx, "inv.sv")
        self.assertEqual(result.verdict, "pass")
        self.assertTrue((self.root / "out" / "tb_inv.sv").is_file())
        self.assertTrue((self.root / "out" / "tb_inv.md").is_file())

    @unittest.skipUnless(HAS_VHDL_SIM, "nvc/ghdl not installed")
    def test_vhdl_tb_runs_in_simulator(self):
        from bartmoss.modes.tb import run_tb

        (self.root / "inv.vhd").write_text(VHDL_INV)
        result = run_tb(self.ctx, "inv.vhd")
        self.assertEqual(result.verdict, "pass")
        self.assertTrue((self.root / "out" / "tb_inv.vhd").is_file())

    @unittest.skipUnless(HAS_VHDL_SIM, "nvc/ghdl not installed")
    def test_vhdl_debug_lints(self):
        from bartmoss.modes.debug import run_debug

        (self.root / "inv.vhd").write_text(VHDL_INV)
        result = run_debug(self.ctx, "inv.vhd")
        self.assertEqual(result.status, "clean")
        self.assertTrue(result.lint_before.ok)

    def test_debug_refuses_file_outside_workspace(self):
        from bartmoss.modes.debug import run_debug

        with self.assertRaises(WorkspaceError):
            run_debug(self.ctx, "/etc/hosts")


if __name__ == "__main__":
    unittest.main()
