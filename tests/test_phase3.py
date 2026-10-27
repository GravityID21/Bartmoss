"""Offline tests for the Phase 3 modes (/wave, /formal, /tb --random): no network, no API keys.
Simulator and solver tests skip when the tools aren't installed."""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bartmoss.files import WorkspaceError
from bartmoss.sim import vcd
from bartmoss.ui import UI
from tests.test_core import HAS_VERILATOR, REPO, make_ctx

HAS_FORMAL = bool(shutil.which("yosys") and shutil.which("yosys-smtbmc")
                  and any(shutil.which(s) for s in ("z3", "yices-smt2", "boolector", "bitwuzla")))

# 1 ps timescale, clk period 10 ns. tb.clk and tb.dut.clk are aliases (same code). data is a
# left-truncated vector ("b1" means 0001). valid drops while ready is low at 35 ns, and data
# changes while stalled; pulse is a 3 ns glitch.
VCD = """$date today $end
$timescale 1ps $end
$scope module tb $end
$var reg 1 ! clk $end
$var reg 1 " rst $end
$scope module dut $end
$var wire 1 ! clk $end
$var wire 1 # valid $end
$var wire 1 $ ready $end
$var wire 4 % data [3:0] $end
$var wire 1 & pulse $end
$var reg 2 ' state [1:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
0!
1"
0#
1$
bx %
0&
bx '
$end
#5000
1!
#10000
0!
0"
b1 %
b0 '
#15000
1!
#20000
0!
1#
0$
#25000
1!
#30000
0!
b10 %
1&
#33000
0&
#35000
1!
#40000
0!
0#
1$
b1 '
#45000
1!
#50000
0!
#55000
1!
#60000
0!
#65000
1!
"""


def write_vcd(root: Path, name: str = "w.vcd") -> Path:
    path = root / name
    path.write_text(VCD)
    return path


class TestVcd(unittest.TestCase):
    def setUp(self):
        self.path = write_vcd(Path(tempfile.mkdtemp()))

    def test_header_scopes_and_timescale(self):
        with open(self.path) as f:
            timescale, variables = vcd.read_header(f)
        self.assertEqual(timescale, 1e-3)
        self.assertEqual([v.name for v in variables][:3], ["tb.clk", "tb.rst", "tb.dut.clk"])
        self.assertEqual(variables[5].width, 4)

    def test_select_patterns_and_aliases(self):
        with open(self.path) as f:
            _, variables = vcd.read_header(f)
        chosen, unmatched = vcd.select(variables, ["dut.*", "nope"])
        self.assertEqual([v.leaf for v in chosen], ["clk", "valid", "ready", "data", "pulse", "state"])
        self.assertEqual(unmatched, ["nope"])
        everything, _ = vcd.select(variables, [])
        self.assertEqual(len(everything), 7)  # tb.dut.clk is an alias of tb.clk
        by_leaf, _ = vcd.select(variables, ["data"])
        self.assertEqual(by_leaf[0].name, "tb.dut.data")

    def test_values_window_and_clock(self):
        dump, signals, clock, _, _ = vcd.load(self.path, ["dut.*"], window="12:")
        data = next(v for v in signals if v.leaf == "data")
        self.assertEqual(dump.changes[data.code][0], (12.0, "0001"))  # value held at the window start
        self.assertEqual(dump.value_at(data.code, 30), "0010")
        self.assertEqual(dump.value_before(data.code, 30), "0001")
        self.assertEqual(clock[0].name, "tb.dut.clk")
        self.assertEqual(clock[1], 10.0)

    def test_parse_window_and_time(self):
        self.assertEqual(vcd.parse_window("1us:2us"), (1000.0, 2000.0))
        self.assertEqual(vcd.parse_window(":500"), (0.0, 500.0))
        self.assertEqual(vcd.parse_time("250 ps"), 0.25)
        with self.assertRaises(ValueError):
            vcd.parse_window("500:100")
        with self.assertRaises(ValueError):
            vcd.parse_window("soon")

    def test_summary_reports_handshake_pulse_and_x(self):
        dump, signals, clock, _, _ = vcd.load(self.path, ["dut.*"])
        text = vcd.summarise(dump, signals, clock, source="w.vcd")
        self.assertIn("Clock: tb.dut.clk, period 10 ns", text)
        self.assertIn("valid dropped before ready", text)
        self.assertIn("data changed from 1 to 2 while valid=1 and ready=0", text)
        self.assertIn("pulse high pulse only 3 ns wide at 30", text)
        self.assertIn("tb.dut.data is x from 0 to 10", text)
        self.assertIn("| t | valid | ready | data | pulse | state |", text)
        self.assertIn("more identical cycles", text)

    def test_clock_needs_regular_edges_and_duty_cycle(self):
        strobe = [(0, "0"), (5, "1"), (6, "0"), (45, "1"), (46, "0"), (85, "1"), (86, "0"), (125, "1")]
        self.assertIsNone(vcd.clock_period(strobe))
        clk = [(t * 5.0, "1" if t % 2 else "0") for t in range(12)]
        self.assertEqual(vcd.clock_period(clk), 10.0)

    def test_not_a_vcd(self):
        bad = self.path.parent / "bad.vcd"
        bad.write_text("hello\n")
        with self.assertRaises(vcd.VcdError):
            with open(bad) as f:
                vcd.read_header(f)


class TestWaveMode(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.root)
        write_vcd(self.root)

    def test_review_with_mock(self):
        from bartmoss.modes.wave import run_wave

        result = run_wave(self.ctx, "w.vcd", ["dut.*"], question="valid must hold until ready")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.signal, "none")
        self.assertTrue((self.root / "out" / "w.wave.md").is_file())
        self.assertNotIn("$enddefinitions", (self.root / "out" / "w.wave.md").read_text())

    def test_summary_and_list_make_no_model_call(self):
        from bartmoss.modes.wave import run_wave

        self.assertEqual(run_wave(self.ctx, "w.vcd", summary_only=True).status, "summary")
        self.assertEqual(run_wave(self.ctx, "w.vcd", list_only=True).status, "listed")
        self.assertEqual(self.ctx.usage.calls, [])

    def test_unknown_signal_is_an_error(self):
        from bartmoss.modes.wave import run_wave

        with self.assertRaises(ValueError):
            run_wave(self.ctx, "w.vcd", ["missing_signal"])

    def test_parse_diagnosis(self):
        from bartmoss.modes.wave import parse_diagnosis

        self.assertEqual(parse_diagnosis("<diagnosis>\nsignal: tb.dut.count\ntime: 1.5us\n</diagnosis>"),
                         ("tb.dut.count", 1500.0))
        self.assertEqual(parse_diagnosis("<diagnosis>\nsignal: `count`\ntime: t=105 ns\n</diagnosis>"),
                         ("count", 105.0))
        self.assertEqual(parse_diagnosis("<diagnosis>\nsignal: None\ntime: none\n</diagnosis>"), ("none", None))

    def test_fst_without_fst2vcd_is_a_clear_error(self):
        from bartmoss.modes.wave import run_wave

        (self.root / "w.fst").write_bytes(b"\x00")
        with mock.patch("bartmoss.sim.tools.shutil.which", return_value=None):
            with self.assertRaises(WorkspaceError):
                run_wave(self.ctx, "w.fst")

    @unittest.skipUnless(shutil.which("fst2vcd") and shutil.which("vcd2fst"), "GTKWave tools not installed")
    def test_fst_is_converted(self):
        import subprocess

        from bartmoss.modes.wave import run_wave

        subprocess.run(["vcd2fst", str(self.root / "w.vcd"), str(self.root / "w.fst")], check=True, capture_output=True)
        result = run_wave(self.ctx, "w.fst", ["dut.*"], summary_only=True)
        self.assertIn("valid dropped before ready", result.summary)


class TestRandomTb(unittest.TestCase):
    def test_coverage_lines(self):
        from bartmoss.sim.tools import coverage

        self.assertEqual(coverage("UNCOVERED: full\nUNCOVERED: wrap \nCOVERAGE: 6/8 bins\n"), (6, 8, {"full", "wrap"}))
        self.assertIsNone(coverage("TEST PASSED"))

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_seeds_union_coverage(self):
        from bartmoss.modes.tb import run_tb

        root = Path(tempfile.mkdtemp())
        ctx = make_ctx(root)
        (root / "inv.sv").write_text("module inv(input logic a, output logic y);\n  assign y = ~a;\nendmodule\n")
        result = run_tb(ctx, "inv.sv", random=True)
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.coverage, (2, 2, ()))  # seed 1 misses "odd", seeds 2-3 miss "even"
        self.assertEqual(result.attempts, 1)
        one = run_tb(ctx, "inv.sv", random=True, seeds=1)
        self.assertEqual(one.coverage, (1, 2, ("odd",)))
        self.assertEqual(one.attempts, 3)  # below coverage_target, sent back twice
        self.assertEqual(one.verdict, "pass")

    def test_random_uses_its_own_token_cap(self):
        from bartmoss.modes.tb import run_tb
        from bartmoss.providers.base import ChatResult

        root = Path(tempfile.mkdtemp())
        ctx = make_ctx(root)
        (root / "inv.sv").write_text("module inv(input logic a, output logic y);\n  assign y = ~a;\nendmodule\n")
        caps = []
        ctx.ask_model = lambda mode, messages, **kw: caps.append(kw.get("max_tokens")) or ChatResult(text="", model="m")
        run_tb(ctx, "inv.sv", random=True, write=False)
        run_tb(ctx, "inv.sv", write=False)
        self.assertEqual(set(caps), {64000, None})  # random TBs use modes.tb.random_max_tokens; directed keep max_tokens

    def test_random_vhdl_is_rejected(self):
        from bartmoss.modes.tb import run_tb

        root = Path(tempfile.mkdtemp())
        (root / "inv.vhd").write_text("entity inv is end entity;\n")
        with self.assertRaises(ValueError):
            run_tb(make_ctx(root), "inv.vhd", random=True)


class TestFormal(unittest.TestCase):
    COUNTER = """module other(input logic a);
endmodule

module cnt(input logic clk, input logic rst, output logic [3:0] q);
    always_ff @(posedge clk) q <= rst ? 4'd0 : (q == 4'd9 ? 4'd0 : q + 4'd1);
endmodule
"""

    def test_insert_properties_targets_top_module(self):
        from bartmoss.modes.formal import insert_properties

        out = insert_properties(self.COUNTER, "cnt", "`ifdef FORMAL\nalways @* assert (q <= 9);\n`endif\n")
        self.assertEqual(out.count("`ifdef FORMAL"), 1)
        self.assertLess(out.index("module cnt"), out.index("`ifdef FORMAL"))
        self.assertTrue(out.rstrip().endswith("`endif\nendmodule"))
        self.assertIn("module other(input logic a);\nendmodule", out)

    @unittest.skipUnless(HAS_FORMAL, "yosys/yosys-smtbmc/solver not installed")
    def test_formal_check_pass_fail_vacuous_error(self):
        from bartmoss.modes.formal import insert_properties, stage_files
        from bartmoss.sim.tools import formal_check

        props = ("reg f_past_valid = 1'b0;\nalways @(posedge clk) f_past_valid <= 1'b1;\ninitial assume (rst);\n"
                 "always @* if (f_past_valid) assert (q <= 4'd9);\n")
        cases = {"pass": self.COUNTER, "fail": self.COUNTER.replace("q == 4'd9", "q == 4'd10")}
        for expected, design in cases.items():
            workdir = Path(tempfile.mkdtemp())
            files = stage_files(workdir, [], "cnt.sv", insert_properties(design, "cnt", props))
            result = formal_check(files, "cnt", workdir, depth=15)
            self.assertEqual(result.status, expected)
        self.assertEqual(result.failed, ["cnt.sv:10"])
        self.assertTrue(result.trace and result.trace.is_file())
        workdir = Path(tempfile.mkdtemp())
        bad = props + "always @* assume (rst);\nalways @* assume (!rst);\n"
        self.assertEqual(formal_check(stage_files(workdir, [], "cnt.sv", insert_properties(self.COUNTER, "cnt", bad)),
                                      "cnt", workdir).status, "vacuous")
        workdir = Path(tempfile.mkdtemp())
        broken = insert_properties(self.COUNTER, "cnt", "assert property (@(posedge clk) q |-> ##1 q);")
        self.assertEqual(formal_check(stage_files(workdir, [], "cnt.sv", broken), "cnt", workdir).status, "error")

    @unittest.skipUnless(HAS_FORMAL, "yosys/yosys-smtbmc/solver not installed")
    def test_formal_mode_with_mock(self):
        from bartmoss.modes.formal import run_formal

        root = Path(tempfile.mkdtemp())
        (root / "cnt.sv").write_text(self.COUNTER)
        result = run_formal(make_ctx(root), "cnt.sv")
        self.assertEqual(result.status, "pass")
        self.assertIn("`ifdef FORMAL", (root / "out" / "cnt.formal.sv").read_text())
        self.assertEqual((root / "cnt.sv").read_text(), self.COUNTER)  # the design is never edited

    def test_formal_rejects_vhdl(self):
        from bartmoss.modes.formal import run_formal

        root = Path(tempfile.mkdtemp())
        (root / "x.vhd").write_text("entity x is end entity;\n")
        with self.assertRaises(ValueError):
            run_formal(make_ctx(root), "x.vhd")


class TestPhase3Evals(unittest.TestCase):
    def cases_copy(self, mode: str, name: str) -> Path:
        root = Path(tempfile.mkdtemp()) / "cases"
        shutil.copytree(REPO / "evals" / "cases" / mode / name, root / mode / name)
        return root

    def test_signal_matches_leaf_and_aliases(self):
        from bartmoss.evals import signal_matches

        expect = {"signal": "tb.dut.count", "accept": ["carry"]}
        self.assertTrue(signal_matches("dut.count[3:0]", expect))
        self.assertTrue(signal_matches("CARRY", expect))
        self.assertFalse(signal_matches("tb.dut.en", expect))
        self.assertFalse(signal_matches(None, expect))

    def test_wave_eval_control_case(self):
        from bartmoss.evals import run_eval

        cases = self.cases_copy("wave", "wave_control_gray")
        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.config["eval"]["judge_model"] = "mock:echo"
        report = run_eval(ctx, "wave", cases)
        detail = report["cases"][0]["runs"][0]
        self.assertTrue(detail["signal_ok"] and detail["time_ok"])  # mock finds nothing, which is right here
        self.assertEqual(detail["score"], 1.0)  # mock judge marks item 1 met

    @unittest.skipUnless(HAS_FORMAL, "yosys/yosys-smtbmc/solver not installed")
    def test_formal_eval_trivial_properties_score_0_3(self):
        from bartmoss.evals import run_eval

        cases = self.cases_copy("formal", "formal_decade_counter")
        report = run_eval(make_ctx(Path(tempfile.mkdtemp())), "formal", cases)
        detail = report["cases"][0]["runs"][0]
        self.assertEqual(detail["mutants_caught"], "0/3")
        self.assertEqual(detail["score"], 0.3)

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_random_tb_eval_is_named_and_skips_vhdl(self):
        from bartmoss.evals import run_eval

        root = Path(tempfile.mkdtemp()) / "cases"
        for name in ("alu8", "vhdl_pwm"):
            shutil.copytree(REPO / "evals" / "cases" / "tb" / name, root / "tb" / name)
        ctx = make_ctx(Path(tempfile.mkdtemp()))
        ctx.ui = UI(color=False, out=io.StringIO())
        report = run_eval(ctx, "tb", root, random_tb=True)
        self.assertEqual(report["prompt"], "tb_random")
        self.assertEqual([s["case"] for s in report["skipped"]], ["vhdl_pwm"])
        saved = list((root.parent / "results").glob("*_p-tb-random.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text())["cases"][0]["runs"][0]["coverage"], "2/2")


if __name__ == "__main__":
    unittest.main()
