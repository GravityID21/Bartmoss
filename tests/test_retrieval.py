"""Offline tests for retrieval (SPEC P4.6): chunking, BM25, hybrid ranking with a fake embedder,
and its use from /ask, /vplan, the agent, the MCP server and the retrieval eval. No network."""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bartmoss.files import Workspace
from bartmoss.retrieval import (BM25, Index, RetrievalError, build_index, chunk_doc, chunk_hdl, embedder,
                                excerpt_blocks, tokens)
from tests.test_core import REPO, make_ctx

LONG_MODULE = """// header comment
module uart_tx (
  input  logic clk,
  input  logic rst_n,
  output logic txd
);
  logic [15:0] baud_cnt;
  logic        parity_bit;

  // Baud generator
  always_ff @(posedge clk or negedge rst_n) begin : baud_gen
    if (!rst_n) baud_cnt <= '0;
    else baud_cnt <= baud_cnt + 16'd1;
  end

  // Parity of the shift register
  assign parity_bit = ^baud_cnt;

  always_ff @(posedge clk) begin
    case (baud_cnt)
      16'd0: txd <= 1'b0;
      default: begin
        txd <= parity_bit;
      end
    endcase
  end
endmodule

package small_pkg;
  localparam int WATCHDOG_KEY = 8'h5A;
endpackage
"""

VHDL = """library ieee;
use ieee.std_logic_1164.all;

entity blink is
  port (clk : in std_logic; led : out std_logic);
end entity;

architecture rtl of blink is
  signal q : std_logic := '0';
begin
  -- toggles every clock
  toggle: process (clk)
  begin
    if rising_edge(clk) then
      q <= not q;
    end if;
  end process;

  led <= q;
end architecture;
"""

SPEC = """<!-- generated -->
# Device spec

## 1. Registers

### 1.1 CTRL

Bit 0 enables the transmitter.

```
# not a heading inside a code fence
```

### 1.2 Baud divider

The baud rate is f_clk / (DIV + 1).

Watchdog
--------

Write the unlock key 0x5A, then the kick key.
"""


def covers(chunk, line):
    return chunk.start <= line <= chunk.end


class TestTokens(unittest.TestCase):
    def test_identifier_parts_literals_and_plurals(self):
        t = tokens("tx_valid rdPtr registers")
        for word in ("tx_valid", "tx", "valid", "rdptr", "rd", "ptr", "register"):
            self.assertIn(word, t)
        # 8'h5A, 0x5A and 90 are the same number
        self.assertEqual(tokens("8'h5A"), tokens("0x5A"))
        self.assertEqual(tokens("8'h5A"), tokens("90"))
        self.assertNotIn("the", tokens("the clock"))

    def test_bm25_prefers_rare_exact_terms(self):
        bm = BM25([tokens("clock enable logic"), tokens("parity bit logic"), tokens("logic logic logic")])
        scores = bm.scores(tokens("parity"))
        self.assertEqual(max(range(3), key=lambda i: scores[i]), 1)


class TestChunking(unittest.TestCase):
    def test_small_unit_is_one_chunk(self):
        chunks = chunk_hdl("a.sv", LONG_MODULE, max_lines=60)
        # The leading comment is outside any unit but has no code, so it isn't a chunk of its own.
        self.assertEqual([c.title for c in chunks], ["module uart_tx", "package small_pkg"])

    def test_long_unit_splits_at_items_with_their_comments(self):
        chunks = [c for c in chunk_hdl("a.sv", LONG_MODULE, max_lines=8) if "uart_tx" in c.title]
        self.assertGreater(len(chunks), 2)
        lines = LONG_MODULE.splitlines()
        baud = next(c for c in chunks if "baud_gen" in c.title)
        # The comment above the block travels with it.
        self.assertEqual(lines[baud.start - 1].strip(), "// Baud generator")
        parity = next(c for c in chunks if "assign parity_bit" in c.title)
        self.assertIn("// Parity of the shift register", parity.text)
        # No overlaps, nothing lost between the module and endmodule lines.
        spans = sorted((c.start, c.end) for c in chunks)
        for (s1, e1), (s2, _) in zip(spans, spans[1:]):
            self.assertEqual(s2, e1 + 1)
        self.assertTrue(spans[-1][1] == lines.index("endmodule") + 1)
        # The case/endcase and nested begin/end inside the always block don't start new items.
        last = next(c for c in chunks if "endcase" in c.text)
        self.assertIn("always_ff @(posedge clk)", last.text)

    def test_unterminated_module_and_line_mode(self):
        chunks = chunk_hdl("b.sv", "module broken(input a);\n  assign x = a;\n")
        self.assertEqual(chunks[0].title, "module broken")
        windows = chunk_hdl("a.sv", LONG_MODULE, max_lines=10, mode="lines")
        self.assertEqual(windows[0].start, 1)
        self.assertEqual(windows[1].start, 11)

    def test_vhdl_units_and_processes(self):
        chunks = chunk_hdl("blink.vhd", VHDL, max_lines=8)
        titles = [c.title for c in chunks]
        self.assertTrue(any(t.startswith("entity blink") for t in titles))
        self.assertTrue(any("architecture rtl of blink" in t and "toggle: process" in t for t in titles))
        first = next(c for c in chunks if c.title.startswith("entity"))
        self.assertEqual(first.start, 1)  # library/use clauses belong to the first unit

    def test_markdown_headings(self):
        chunks = chunk_doc("docs/spec.md", SPEC)
        titles = [c.title for c in chunks]
        self.assertIn("spec.md § Device spec > 1. Registers > 1.1 CTRL", titles)
        self.assertIn("spec.md § Device spec > 1. Registers > 1.2 Baud divider", titles)
        self.assertTrue(any(t.endswith("Watchdog") for t in titles))  # setext heading
        self.assertFalse(any("not a heading" in t for t in titles))
        self.assertFalse(any(c.text.strip() == "<!-- generated -->" for c in chunks))

    def test_other_document_formats(self):
        rst = chunk_doc("a.rst", "Intro\n=====\n\ntext\n\nDetails\n-------\n\nmore\n")
        self.assertEqual([c.title for c in rst], ["a.rst § Intro", "a.rst § Intro > Details"])
        txt = chunk_doc("a.txt", "Preface line.\n\n1 Overview\nabout\n\n1.1 Timing\nfast\n")
        self.assertEqual([c.title for c in txt], ["a.txt", "a.txt § 1 Overview", "a.txt § 1 Overview > 1.1 Timing"])
        adoc = chunk_doc("a.adoc", "= Title\n\n== Part\nbody\n")
        self.assertEqual(adoc[-1].title, "a.adoc § Title > Part")

    def test_long_section_splits_at_paragraphs(self):
        body = "\n\n".join(f"para {i} " + "word " * 20 for i in range(12))
        chunks = chunk_doc("a.md", "# Big\n\n" + body + "\n", max_lines=80, max_chars=600)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(c.title.startswith("a.md § Big (part ") for c in chunks))
        self.assertTrue(all(len(c.text) <= 700 for c in chunks))
        self.assertTrue(all(c.text.lstrip().startswith(("# Big", "para")) for c in chunks))


class TestIndex(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "rtl").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "rtl" / "uart_tx.sv").write_text(LONG_MODULE)
        (self.root / "rtl" / "blink.vhd").write_text(VHDL)
        (self.root / "docs" / "spec.md").write_text(SPEC)
        (self.root / "LICENSE.txt").write_text("1 Permission\nis granted\n")
        self.ctx = make_ctx(self.root)
        self.ctx.config["retrieval"]["chunk_lines"] = 8

    def test_search_finds_blocks_and_sections(self):
        index = self.ctx.retrieval()
        self.assertFalse(any(c.rel == "LICENSE.txt" for c in index.chunks))
        hits = index.search("where is the parity bit computed", 3)
        self.assertIn("assign parity_bit", hits[0].chunk.title)
        hits = index.search("baud rate formula", 3, kind="doc")
        self.assertTrue(all(h.chunk.kind == "doc" for h in hits))
        self.assertIn("Baud divider", hits[0].chunk.title)
        self.assertEqual(index.search("watchdog key 0x5A", 1, kind="hdl")[0].chunk.title, "package small_pkg")
        self.assertEqual(index.search("baud", 5, allow=lambda rel: rel.startswith("docs/"))[0].chunk.rel, "docs/spec.md")
        self.assertEqual(index.search("   ", 5), [])

    def test_excerpts_respect_the_budget(self):
        hits = self.ctx.retrieval().search("baud parity watchdog", 8)
        text, used = excerpt_blocks(hits, 1)
        self.assertEqual(len(used), 1)  # the best one is always returned, even over budget
        text, used = excerpt_blocks(hits, 10 ** 6)
        self.assertEqual(len(used), len(hits))
        self.assertIn(f'source="{hits[0].chunk.source}"', text)

    def test_hybrid_ranking_and_cache(self):
        calls = []

        def fake_embed(texts):
            # "pending interrupts" and the CTRL section share no words but get the same vector.
            calls.append(len(texts))
            return [[1.0, 0.0] if ("enables the transmitter" in t or "turn on sending" in t) else [0.0, 1.0]
                    for t in texts]

        self.ctx.config["retrieval"]["embeddings"] = "ollama:fake-embed"
        with mock.patch("bartmoss.retrieval.embedder", return_value=fake_embed):
            index = self.ctx.retrieval()
            self.assertEqual(index.embeddings, "ollama:fake-embed")
            hits = index.search("how do I turn on sending", 3)
            self.assertIn("1.1 CTRL", hits[0].chunk.title)
            self.assertEqual(hits[0].via, "vector")
            cache = Path(self.root / "out" / "index" / "embeddings_ollama-fake-embed.json")
            self.assertTrue(cache.is_file())
            embedded = sum(calls[:-1])
            calls.clear()
            self.ctx._index = None
            self.ctx.retrieval()
            self.assertEqual(calls, [])  # every chunk came from the cache
            self.assertEqual(len(json.loads(cache.read_text())), embedded)

    def test_embeddings_failures_fall_back_to_bm25(self):
        with self.assertRaises(RetrievalError):
            embedder("anthropic:claude-haiku-5-5", self.ctx.config)
        with self.assertRaises(RetrievalError):
            embedder("openai:text-embedding-3-small", {"providers": {"openai": {"type": "openai_compat",
                     "base_url": "https://example.invalid/v1", "api_key_env": "BARTMOSS_TEST_NO_SUCH_KEY"}}})
        self.ctx.config["retrieval"]["embeddings"] = "anthropic:claude-haiku-5-5"
        index = self.ctx.retrieval()
        self.assertIsNone(index.embeddings)
        self.assertIn("keyword search only", self.ctx.ui.out.getvalue())

    def test_ask_rag_attaches_excerpts(self):
        from bartmoss.modes.ask import run_ask

        self.ctx.rag = True
        run_ask(self.ctx, "where is the parity bit computed?")
        question = self.ctx.ask_history[0]["content"]
        self.assertIn('<excerpt source="rtl/uart_tx.sv:', question)
        self.assertIn("retrieved: rtl/uart_tx.sv", self.ctx.ui.out.getvalue())
        self.ctx.rag = False
        run_ask(self.ctx, "where is the parity bit computed?")
        self.assertNotIn("<excerpt", self.ctx.ask_history[2]["content"])

    def test_plan_docs_adds_spec_sections(self):
        from bartmoss.modes.plan import docs_query, run_plan
        from bartmoss.providers.mock import MockProvider

        query = docs_query(Path("uart_tx.sv"), "module uart_tx (input logic baud_cnt); endmodule", "extra")
        self.assertEqual(query.split()[:3], ["extra", "uart_tx", "input"])
        self.assertIn("baud_cnt", query)
        seen = []
        original = MockProvider.chat

        def spy(provider, system, messages, **kw):
            seen.append(messages[0]["content"])
            return original(provider, system, messages, **kw)

        with mock.patch.object(MockProvider, "chat", spy):
            run_plan(self.ctx, "vplan", "rtl/uart_tx.sv", write=False, docs=True, docs_query_text="baud divider")
            run_plan(self.ctx, "vplan", "docs/spec.md", write=False, docs=True, docs_query_text="baud divider")
            run_plan(self.ctx, "vplan", "rtl/uart_tx.sv", write=False)
        self.assertIn('<excerpt source="docs/spec.md', seen[0])
        self.assertNotIn('source="rtl/', seen[0])  # documents only
        self.assertNotIn("<excerpt", seen[1])  # a spec isn't given its own sections back
        self.assertNotIn("<excerpt", seen[2])

    def test_agent_and_mcp_tools(self):
        from bartmoss.agent import Workbench
        from bartmoss.mcp_server import CheckerTools, handle

        bench = Workbench(self.ctx, visible=["rtl/blink.vhd"])
        self.assertNotIn("uart_tx", bench.retrieve({"query": "parity baud"}))
        self.assertIn("toggle", bench.retrieve({"query": "toggle process"}))
        tools = CheckerTools(self.ctx)
        reply = handle(tools, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "project_search", "arguments": {"query": "parity bit", "k": 2}}})
        self.assertIn("rtl/uart_tx.sv:", reply["result"]["content"][0]["text"])
        reply = handle(tools, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "project_search", "arguments": {"query": "x", "kind": "pdf"}}})
        self.assertTrue(reply["result"]["isError"])

    def test_repl_search_and_rag_toggle(self):
        from bartmoss.cli import dispatch

        dispatch(self.ctx, "/search parity bit -k 2 --show")
        dispatch(self.ctx, "/index")
        dispatch(self.ctx, "/rag on")
        out = self.ctx.ui.out.getvalue()
        self.assertIn("rtl/uart_tx.sv:", out)
        self.assertIn(" | ", out)  # --show prints the excerpt with line numbers
        self.assertIn("ranking: BM25", out)
        self.assertTrue(self.ctx.rag)


class TestRetrievalEval(unittest.TestCase):
    def test_periph_soc_case_scores_and_saves(self):
        from bartmoss.evals import run_retrieval_eval

        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(REPO / "evals" / "cases" / "retrieval", tmp / "cases" / "retrieval")
        ctx = make_ctx(tmp)
        report = run_retrieval_eval(ctx, tmp / "cases")
        self.assertEqual(report["mode"], "retrieval")
        self.assertEqual(report["model"], "bm25")
        self.assertGreaterEqual(report["mean_score"], 0.9)  # 1.00 when the case was added
        self.assertEqual(len(report["cases"]), 28)
        self.assertTrue((tmp / "results").is_dir())


if __name__ == "__main__":
    unittest.main()
