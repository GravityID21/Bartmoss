"""Fine-tuning data: the opt-in training log and the JSONL exporter. Offline (mock provider)."""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bartmoss.training import CANARY_GUID, export, log_path, read_log
from bartmoss.ui import UI
from tests.test_core import HAS_VERILATOR, REPO, make_ctx

DESIGN = "module inv(input logic a, output logic y);\n  assign y = ~a;\nendmodule\n"


def logging_ctx(files=None):
    root = Path(tempfile.mkdtemp())
    for name, text in (files or {"inv.sv": DESIGN}).items():
        (root / name).write_text(text)
    ctx = make_ctx(root)
    ctx.config["training"] = {"log": True}
    return ctx


def entry(user, check="sim", mode="tb", assistant="reply", time="2026-11-04T10:00:00"):
    return {"time": time, "mode": mode, "prompt": mode, "model": "mock:echo", "check": check, "attempts": 1,
            "system": f"MODE: {mode}", "user": user, "assistant": assistant}


class TestTrainingLog(unittest.TestCase):
    def test_nothing_is_logged_unless_turned_on(self):
        from bartmoss.modes.plan import run_plan

        ctx = logging_ctx()
        ctx.config["training"] = {"log": False}
        run_plan(ctx, "vplan", "inv.sv")
        self.assertFalse(log_path(ctx).exists())

    def test_plan_is_logged_as_structure_only(self):
        from bartmoss.modes.plan import run_plan

        ctx = logging_ctx()
        run_plan(ctx, "vplan", "inv.sv")
        [logged] = read_log(log_path(ctx))
        self.assertEqual((logged["mode"], logged["check"], logged["prompt"]), ("vplan", "structure", "vplan"))
        self.assertIn("MODE: vplan", logged["system"])
        self.assertIn(DESIGN, logged["user"])
        self.assertIn("<plan>", logged["assistant"])

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_passing_testbench_is_logged_as_sim_checked(self):
        from bartmoss.modes.tb import run_tb

        ctx = logging_ctx()
        self.assertEqual(run_tb(ctx, "inv.sv").verdict, "pass")
        [logged] = read_log(log_path(ctx))
        self.assertEqual((logged["mode"], logged["check"]), ("tb", "sim"))

    def test_eval_inputs_carrying_the_canary_are_never_logged(self):
        from bartmoss.modes.plan import run_plan

        ctx = logging_ctx({"inv.sv": f"// {CANARY_GUID}\n" + DESIGN})
        run_plan(ctx, "vplan", "inv.sv")
        self.assertFalse(log_path(ctx).exists())

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_eval_runs_are_never_logged(self):
        from bartmoss.evals import run_eval

        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(REPO / "evals" / "cases" / "tb" / "alu8", tmp / "cases" / "tb" / "alu8")
        ctx = make_ctx(tmp)
        ctx.config["training"] = {"log": True}
        run_eval(ctx, "tb", tmp / "cases")
        self.assertEqual(list(tmp.rglob("log.jsonl")), [])


class TestExport(unittest.TestCase):
    def write_log(self, entries):
        path = Path(tempfile.mkdtemp()) / "log.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def test_levels_duplicates_and_canary(self):
        log = self.write_log([
            entry("a"), entry("a", assistant="newer", time="2026-11-04T11:00:00"),  # duplicate: newest wins
            entry("b", check="lint", mode="debug"), entry("c", check="structure", mode="vplan"),
            entry(f"d {CANARY_GUID}"),
        ])
        out = Path(tempfile.mkdtemp())
        counts = export([log], out, "sim", valid_fraction=0.0)
        self.assertEqual((counts["train"], counts["valid"], counts["duplicates"]), (1, 0, 1))
        self.assertEqual(counts["skipped"]["weaker check"], 2)
        self.assertEqual(counts["skipped"]["eval canary"], 1)
        [example] = [json.loads(l) for l in (out / "train.jsonl").read_text().splitlines()]
        self.assertEqual([m["role"] for m in example["messages"]], ["system", "user", "assistant"])
        self.assertEqual(example["messages"][2]["content"], "newer")
        self.assertEqual(export([log], out, "structure", 0.0)["train"], 3)
        self.assertEqual(export([log], out, "structure", 0.0, modes={"debug"})["by_mode"], {"debug": 1})

    def test_split_is_stable_and_roughly_the_requested_size(self):
        log = self.write_log([entry(f"input {i}") for i in range(400)])
        first, second = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        counts = export([log], first, valid_fraction=0.2)
        export([log], second, valid_fraction=0.2)
        self.assertEqual((first / "valid.jsonl").read_text(), (second / "valid.jsonl").read_text())
        self.assertTrue(50 <= counts["valid"] <= 110, counts)
        self.assertEqual(counts["train"] + counts["valid"], 400)

    def test_cli_export(self):
        from bartmoss.cli import main

        ctx = logging_ctx()
        root = str(ctx.workspace.root)
        with mock.patch("bartmoss.cli.UI", lambda color=None: UI(color=False, out=io.StringIO())):
            self.assertEqual(main(["--root", root, "--out", "out", "export-training"]), 1)  # no log yet
            log_path(ctx).parent.mkdir(parents=True)
            log_path(ctx).write_text(json.dumps(entry("x")) + "\n")
            self.assertEqual(main(["--root", root, "--out", "out", "export-training", "--valid", "0"]), 0)
        self.assertEqual(len((ctx.workspace.root / "out" / "training" / "train.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
