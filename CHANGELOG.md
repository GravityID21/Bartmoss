# Changelog

## 0.1.0 (unreleased)

First public release. The core is pure Python 3.9+ with no dependencies; provider SDKs are optional extras.

**Modes**
- `ask`: hardware design Q&A, multi-turn in the REPL, with `@file` attachments, optional web search (Claude) and optional retrieval over the project (`--rag`).
- `debug`: lint (Verilator, NVC or GHDL) and Yosys synthesis checks, a model fix, re-checks and retries; `--agent` lets the model use tools first.
- `tb`: directed self-checking testbenches (SystemVerilog or VHDL), compiled and run; `--random` for constrained-random testbenches with functional coverage across seeds.
- `wave`: waveform review from a local VCD/FST summary (the raw dump is never sent).
- `formal`: model-written assertions checked with Yosys + yosys-smtbmc, without editing the design; unsupported SVA is caught before Yosys runs.
- `flow`: lint → simulate → synthesise → formal → report, with no model calls.
- `vplan` and `uvmplan`: verification plans and UVM environment designs.
- `search`, `project`, `index`: structural retrieval (BM25, optional embeddings) and a project index for automatic context.
- `mcp`: an MCP server exposing the free checkers to Claude Code and other clients.
- Plugin modes: a prompt plus optional checks in `~/.config/bartmoss/modes/` (see `docs/plugins.md`).
- `export-training`: opt-in log of verified runs, exported as chat-format JSONL for fine-tuning (see `docs/fine-tuning.md`).

**Models:** Anthropic (streaming, prompt caching, effort, refusal handling), any OpenAI-compatible API (OpenAI, OpenRouter, Ollama, LM Studio, vLLM) and an offline mock.

**Evals:** simulator- and solver-graded cases for debug (54), tb (25), formal (20), wave (11), plus rubric-graded ask, vplan and uvmplan, and a retrieval set; `eval --repeat`, `leaderboard` and `compare`. Results are in `evals/LEADERBOARD.md`.

**Safety:** every file access is confined to the workspace, every tool run goes through an executable and option allowlist with credentials stripped, and model-written HDL is screened for `$system`, file I/O and DPI before it is simulated.
