"""P6.5 user-defined modes: loading, validation, checks and CLI wiring. Offline (mock provider)."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bartmoss.context import system_prompt
from bartmoss.plugins import discover, register, run_plugin
from bartmoss.ui import UI
from tests.test_core import HAS_VERILATOR, REPO, make_ctx

EXAMPLES = REPO / "examples" / "plugins"


def write_plugin(root: Path, name: str, prompt: str = "Do the thing.", options=None, check: str = "") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "prompt.md").write_text(prompt)
    if options is not None:
        (path / "mode.json").write_text(json.dumps(options))
    if check:
        (path / "check.py").write_text(check)
    return path


def plugin_ctx(plugins: dict, files: dict):
    root = Path(tempfile.mkdtemp())
    for name, text in files.items():
        (root / name).write_text(text)
    ctx = make_ctx(root)
    ctx.plugins = plugins
    register(ctx.config, plugins)
    return ctx


class TestPlugins(unittest.TestCase):
    def test_examples_load(self):
        plugins = discover(EXAMPLES)
        self.assertEqual(sorted(plugins), ["lint-clean", "review"])
        self.assertTrue(plugins["lint-clean"].lint)
        self.assertEqual(plugins["review"].sections, ["Summary", "Issues", "Suggestions"])
        self.assertIsNotNone(plugins["review"].checker)

    def test_invalid_plugins_are_skipped_with_a_reason(self):
        root = Path(tempfile.mkdtemp())
        write_plugin(root, "debug")  # built-in name
        (root / "noprompt").mkdir()
        write_plugin(root, "badkey", options={"modle": "x"})
        write_plugin(root, "escape", options={"output_suffix": "/../../etc.md"})
        write_plugin(root, "brokencheck", check="raise RuntimeError('boom')\n")
        write_plugin(root, "Upper")
        write_plugin(root, "good")
        warnings = []
        plugins = discover(root, warnings.append)
        self.assertEqual(list(plugins), ["good"])
        text = "\n".join(warnings)
        for reason in ("built-in", "no prompt.md", "unknown mode.json keys modle", "output_suffix", "failed to load",
                       "lowercase"):
            self.assertIn(reason, text)

    def test_prompt_gets_guard_mode_line_and_tag_instruction(self):
        root = Path(tempfile.mkdtemp())
        write_plugin(root, "summarise", "Summarise the file.", {"output_tag": "summary"})
        plugins = discover(root)
        register(make_ctx(root).config, plugins)
        prompt = system_prompt("summarise")
        self.assertIn("MODE: summarise", prompt)
        self.assertIn("Summarise the file.", prompt)
        self.assertTrue(prompt.rstrip().endswith("Reply with the result inside <summary> tags."))
        self.assertIn((REPO / "bartmoss" / "prompts" / "guard.md").read_text().strip()[:60], prompt)

    def test_user_config_overrides_plugin_settings(self):
        root = Path(tempfile.mkdtemp())
        write_plugin(root, "tidy", options={"model": "mock:plugin", "max_retries": 3})
        ctx = make_ctx(root)
        ctx.config["modes"]["tidy"] = {"model": "mock:mine"}
        register(ctx.config, discover(root))
        self.assertEqual(ctx.config["modes"]["tidy"], {"model": "mock:mine", "max_retries": 3})

    def test_missing_sections_are_sent_back_and_reported(self):
        plugins = discover(EXAMPLES)
        ctx = plugin_ctx(plugins, {"a.sv": "module a; endmodule\n"})
        result = run_plugin(ctx, plugins["review"], "a.sv")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], 2)  # max_retries 1 in mode.json
        self.assertTrue((ctx.workspace.root / "out" / "a.review.md").is_file())

    def test_checker_problems_and_errors(self):
        root = Path(tempfile.mkdtemp())
        write_plugin(root, "nosemi", options={"output_tag": "out"},
                     check="def check(output, source):\n    return ['has a semicolon'] if ';' in output else []\n")
        write_plugin(root, "crash", options={"output_tag": "out"},
                     check="def check(output, source):\n    raise KeyError('x')\n")
        plugins = discover(root)
        ctx = plugin_ctx(plugins, {"a.sv": "module a; endmodule\n", "b.txt": "no semicolons here\n"})
        self.assertEqual(run_plugin(ctx, plugins["nosemi"], "a.sv")["status"], "failed")
        self.assertEqual(run_plugin(ctx, plugins["nosemi"], "b.txt")["status"], "ok")
        crashed = run_plugin(ctx, plugins["crash"], "b.txt")
        self.assertEqual((crashed["status"], crashed["attempts"]), ("error", 1))  # not retried

    def test_text_input_plugin_prints_output(self):
        root = Path(tempfile.mkdtemp())
        write_plugin(root, "explain", options={"input": "text", "output_tag": "answer"})
        plugins = discover(root)
        ctx = plugin_ctx(plugins, {})
        result = run_plugin(ctx, plugins["explain"], "what is a latch?")
        self.assertEqual(result["status"], "ok")
        self.assertIn("what is a latch?", ctx.ui.out.getvalue())
        self.assertIsNone(result["written"])

    def test_lint_check_refuses_unsafe_hdl_before_any_tool_runs(self):
        plugins = discover(EXAMPLES)
        source = 'module a; initial $system("rm -rf /"); endmodule\n'
        ctx = plugin_ctx(plugins, {"a.sv": source})
        with mock.patch("bartmoss.sim.tools.lint") as lint:
            result = run_plugin(ctx, plugins["lint-clean"], "a.sv")
        lint.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("not allowed", ctx.ui.out.getvalue())

    @unittest.skipUnless(HAS_VERILATOR, "verilator not installed")
    def test_lint_check_passes_clean_rtl_and_sends_errors_back(self):
        plugins = discover(EXAMPLES)
        clean = "module a(input logic x, output logic y);\n  assign y = x;\nendmodule\n"
        ctx = plugin_ctx(plugins, {"a.sv": clean, "b.sv": "module b(input x); assign y = ; endmodule\n"})
        ok = run_plugin(ctx, plugins["lint-clean"], "a.sv")
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["written"].name, "a.clean.sv")
        bad = run_plugin(ctx, plugins["lint-clean"], "b.sv")
        self.assertEqual((bad["status"], bad["attempts"]), ("failed", 3))

    def test_cli_lists_and_runs_plugins(self):
        from bartmoss.cli import dispatch, main

        root = Path(tempfile.mkdtemp())
        (root / "a.sv").write_text("module a; endmodule\n")
        with mock.patch.dict(os.environ, {"BARTMOSS_MODES_DIR": str(EXAMPLES)}), \
                mock.patch("bartmoss.cli.UI", lambda color=None: UI(color=False, out=io.StringIO())):
            self.assertEqual(main(["--model", "mock:echo", "--root", str(root), "--yes", "plugins"]), 0)
            # Exit 0 either way: with no linter installed the lint check is skipped.
            self.assertEqual(main(["--model", "mock:echo", "--root", str(root), "--yes", "lint-clean", "a.sv"]), 0)
        self.assertTrue((root / "bartmoss_out" / "a.clean.sv").is_file())
        ctx = plugin_ctx(discover(EXAMPLES), {"a.sv": "module a; endmodule\n"})
        dispatch(ctx, "/review a.sv")
        dispatch(ctx, "/help")
        out = ctx.ui.out.getvalue()
        self.assertIn("review for a.sv", out)
        self.assertIn("/review", out)


if __name__ == "__main__":
    unittest.main()
