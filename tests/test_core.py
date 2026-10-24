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
        self.assertEqual(tb_verdict("[235000] %Error: f.sv:109: Assertion failed in tb.dut.sva_x: SVA"), "fail")

    def test_lint_errors_vhdl_formats(self):
        nvc = lint_errors("** Error: no visible declaration for FOO\n    > /tmp/a.vhd:3\n** Warning: unused")
        ghdl = lint_errors("/tmp/a.vhd:3:5: no declaration for \"foo\"\n/tmp/a.vhd:4:1:warning: unused")
        self.assertEqual(nvc, {"no visible declaration for FOO"})
        self.assertEqual(ghdl, {'no declaration for "foo"'})

    def test_unmet_requirement(self):
        from bartmoss.sim.tools import unmet_requirement

        self.assertIsNone(unmet_requirement({}))
        self.assertIn("not installed", unmet_requirement({"no-such-simulator-xyz": "1.0"}))

    def test_vhdl_verdict(self):
        self.assertEqual(tb_verdict("** Note: 10ns+0: TEST PASSED"), "pass")
        self.assertEqual(tb_verdict("** Failure: 5ns+0: Assertion violation."), "fail")

    def test_synth_findings(self):
        from bartmoss.sim.tools import synth_findings

        out = ("ERROR: Latch inferred for signal `\\m.\\q' from always_comb process `\\m.$proc$/x.sv:2$1'.\n"
               "Warning: Wire m.\\y is used but has no driver.\nWarning: Replacing memory with registers.")
        self.assertEqual(synth_findings(out), {"latch inferred for \\m.\\q", "Wire m.\\y is used but has no driver."})
        self.assertIsNone(synth_findings("x.sv:5: ERROR: syntax error, unexpected TOK_IMPORT"))
        self.assertEqual(synth_findings("Found and reported 0 problems."), set())

    @unittest.skipUnless(shutil.which("yosys"), "yosys not installed")
    def test_synth_check_catches_latch(self):
        from bartmoss.sim.tools import synth_check, synth_findings

        f = Path(tempfile.mkdtemp()) / "l.sv"
        f.write_text("module l(input logic en, input logic d, output logic q);\n  always_comb if (en) q = d;\nendmodule\n")
        findings = synth_findings(synth_check(f, "l").output)
        self.assertTrue(any("latch" in x for x in findings))

    def test_lint_errors_ignore_location(self):
        a = lint_errors("%Error: /tmp/a/x.sv:3:5: syntax error, unexpected endmodule\n%Error: Exiting due to 1 error(s)")
        b = lint_errors("%Error: /other/x.sv:9:1: syntax error, unexpected endmodule")
        self.assertEqual(a, b)


class TestPromptsAndEvalTools(unittest.TestCase):
    def test_unknown_prompt_variant_is_rejected(self):
        from bartmoss.context import system_prompt

        self.assertIn("MODE: debug", system_prompt("debug"))
        with self.assertRaises(ValueError):
            system_prompt("debug.does-not-exist")

    def test_compare_reports_deltas(self):
        import json
        from bartmoss.compare import compare

        tmp = Path(tempfile.mkdtemp())
        base = {"mode": "debug", "model": "m", "repeat": 1}
        (tmp / "a.json").write_text(json.dumps({**base, "cases": [{"case": "x", "score": 0.25}, {"case": "y", "score": 1.0}]}))
        (tmp / "b.json").write_text(json.dumps({**base, "prompt": "debug.v2",
                                                "cases": [{"case": "x", "score": 1.0}, {"case": "y", "score": 1.0}]}))
        result = compare(UI(color=False, out=io.StringIO()), tmp / "a.json", tmp / "b.json")
        self.assertAlmostEqual(result["mean_b"] - result["mean_a"], 0.375)

    def test_leaderboard_prefers_repeated_runs_and_recommends_cheapest(self):
        from bartmoss.leaderboard import best_runs, recommend, render

        def run(model, score, repeat, cost):
            return {"mode": "debug", "model": model, "prompt": "debug", "date": "2026-10-24", "repeat": repeat,
                    "mean_score": score, "run_means": [score] * repeat, "cost_usd": cost, "seconds": 60.0,
                    "cases": [{"case": "x", "score": score, "repeat": repeat, "full_passes": repeat}]}

        best = best_runs([run("a:big", 0.80, 1, 3.0), run("a:big", 0.95, 3, 9.0), run("a:small", 0.93, 3, 0.3)])
        self.assertEqual(best[("debug", "a:big", "debug")]["repeat"], 3)
        self.assertEqual(recommend(best)["debug"][0], "a:small")
        self.assertIn("| debug | a:big | 1 | **0.95** |", render(best))

    def test_eval_aborts_on_provider_error(self):
        from bartmoss.evals import run_eval
        from bartmoss.providers import ProviderError

        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.ui = UI(color=False, out=io.StringIO())

        class Broken:
            def chat(self, *args, **kwargs):
                raise ProviderError("Your credit balance is too low")

        ctx._providers["mock"] = Broken()  # shared with the per-case contexts
        results = REPO / "evals" / "results"
        before = set(results.glob("*.json"))
        with self.assertRaises(ProviderError):
            run_eval(ctx, "debug", REPO / "evals" / "cases", only="counter_wrap")
        self.assertEqual(set(results.glob("*.json")), before)

    def test_ask_eval_scores_rubric(self):
        from bartmoss.evals import run_eval

        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.config["eval"]["judge_model"] = "mock:echo"
        results = REPO / "evals" / "results"
        report = run_eval(ctx, "ask", REPO / "evals" / "cases", only="fifo_depth")
        self.assertEqual(report["cases"][0]["runs"][0]["rubric_met"], "2/4")  # mock judge alternates
        for f in results.glob("*mock-echo_only-fifo-depth*"):
            shutil.rmtree(f) if f.is_dir() else f.unlink()

    def test_judge_parses_items(self):
        from bartmoss.evals import judge

        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.config["eval"]["judge_model"] = "mock:echo"
        graded = judge(ctx, "# Plan", ["first item", "second item", "third item"])
        self.assertEqual([g["met"] for g in graded], [True, False, True])


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

    def test_ask_attaches_mentions_but_not_emails(self):
        from bartmoss.modes.ask import attach_mentions

        (self.root / "rtl").mkdir()
        (self.root / "rtl" / "f.sv").write_text("module f; endmodule\n")
        text = attach_mentions(self.ctx, "explain @rtl/f.sv on @clk, mail me at a@b.com")
        self.assertIn('<file name="f.sv">', text)
        self.assertEqual(text.count("<file"), 1)

    def test_ask_mention_outside_workspace_is_refused(self):
        from bartmoss.modes.ask import attach_mentions

        with self.assertRaises(WorkspaceError):
            attach_mentions(self.ctx, "look at @../../etc/hosts")

    def test_filter_blocks_off_topic_and_keeps_history_clean(self):
        from bartmoss.modes.ask import run_ask

        self.ctx.config["filter"]["model"] = "mock:echo"
        self.ctx.use_filter = True
        self.assertIsNone(run_ask(self.ctx, "give me a pancake recipe"))
        self.assertEqual(self.ctx.ask_history, [])
        self.assertIsNotNone(run_ask(self.ctx, "what is clock skew?"))
        self.assertEqual(len(self.ctx.ask_history), 2)

    def test_web_search_falls_back_without_claude(self):
        from bartmoss.modes.ask import run_ask

        self.ctx.web_search = True
        self.assertIsNotNone(run_ask(self.ctx, "latest verilator?"))
        self.assertIn("only available with Claude", self.ctx.ui.out.getvalue())

    def test_web_search_cost(self):
        from bartmoss.providers.base import ChatResult

        usage = Usage(prices={"anthropic:m": [1.0, 1.0]})
        self.assertAlmostEqual(usage.cost("anthropic:m", ChatResult("", "m", web_searches=2)), 0.02)

    def test_vplan_and_uvmplan_write_complete_plans(self):
        from bartmoss.modes.plan import run_plan

        (self.root / "spec.md").write_text("# FIFO\n8-deep synchronous FIFO with full/empty flags.\n")
        for mode in ("vplan", "uvmplan"):
            result = run_plan(self.ctx, mode, "spec.md")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.written.name, f"spec.{mode}.md")

    def test_plan_structure_check(self):
        from bartmoss.modes.plan import structure_problems

        problems = structure_problems("vplan", "# Overview\nx\n# Features\nno table here\n")
        self.assertIn("missing section '# Checks'", problems)
        self.assertIn("section '# Features' needs a table with at least one row", problems)

    def test_session_round_trip(self):
        from bartmoss.modes.ask import run_ask
        from bartmoss.session import load_session, save_session

        run_ask(self.ctx, "what is a latch?")
        path = save_session(self.ctx, "chat")
        self.assertEqual(path.name, "chat.json")
        saved = list(self.ctx.ask_history)
        self.ctx.ask_history.clear()
        self.assertEqual(load_session(self.ctx, "chat.json"), 2)
        self.assertEqual(self.ctx.ask_history, saved)

    def test_session_rejects_bad_history(self):
        from bartmoss.session import load_session

        (self.root / "bad.json").write_text('{"bartmoss_session": 1, "ask_history": [{"role": "assistant", "content": "x"}]}')
        with self.assertRaises(ValueError):
            load_session(self.ctx, "bad.json")

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

    @unittest.skipUnless(shutil.which("yosys") and HAS_VERILATOR, "yosys/verilator not installed")
    def test_debug_retries_when_fix_adds_latch(self):
        from bartmoss.modes.debug import run_debug
        from bartmoss.providers.base import ChatResult

        good = "module m(input logic en, input logic d, output logic q);\n  always_comb q = en & d;\nendmodule\n"
        latchy = "module m(input logic en, input logic d, output logic q);\n  always_comb if (en) q = d;\nendmodule\n"
        (self.root / "m.sv").write_text(good)
        replies = iter([latchy, good])
        self.ctx.ask_model = lambda mode, messages, **kw: ChatResult(
            text=f"<analysis>x</analysis><fixed_file>{next(replies)}</fixed_file>", model="fake")
        result = run_debug(self.ctx, "m.sv", write=False)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.status, "clean")

    def test_debug_refuses_file_outside_workspace(self):
        from bartmoss.modes.debug import run_debug

        with self.assertRaises(WorkspaceError):
            run_debug(self.ctx, "/etc/hosts")


if __name__ == "__main__":
    unittest.main()
