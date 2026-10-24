# Bartmoss — Project Specification

> Open-source, model-agnostic AI assistant for RTL design and verification, driven from the terminal.

| | |
|---|---|
| **Status** | Phase 1 complete · Phase 2 mostly complete (P2.13–P2.15 open) |
| **Owner** | Elliot Stevenson |
| **Language** | Python ≥ 3.9 (3.12 recommended) |
| **Licence** | MIT (see `LICENSE`) |
| **Last updated** | 2026-10-24 |

**How to use this document:** every feature has an ID (`P1.4`, `P3.2`, …) and a checkbox. Tick the box when the feature is merged and tested. Long-term or hard items live in their phase with a **"Return when"** note that says what has to be true before they're worth picking up. Decisions are recorded in §12 so they aren't re-argued later.

---

## 1. Vision

A terminal tool where a hardware engineer can ask theory questions, debug a single RTL file, or generate a testbench, and pick which LLM does the work (paid frontier models or open/local ones). The tool's main advantage over a plain chatbot is that **generated or fixed RTL is checked by a real, free simulator/linter before the user sees it**. The same check is used to score models against each other.

### 1.1 Goals
1. Useful from day one for single-file RTL tasks (theory Q&A, debug, directed testbenches).
2. Runs with any model through a small provider adapter layer.
3. Objective, repeatable model comparison using simulator-graded eval cases.
4. As few dependencies and as little running cost as possible.
5. A clean, well-documented open-source GitHub project.

### 1.2 Non-goals (for now)
- Hosting a public web service (legal/compliance, hosting cost; see §9).
- Multi-file project refactoring or full SoC integration.
- Fine-tuning models. Your own RTL projects are used as **eval cases and few-shot examples**, not training data.
- Driving licensed vendor tools (Quartus, Vivado, ModelSim/Questa, Libero, Radiant) before Phase 5.

---

## 2. Design principles

| Principle | What it means in practice |
|---|---|
| **Modes, not models** | Each feature is a *mode* = system prompt + input handling + output format + optional checker. Any model can run any mode; which model is best per mode is decided by the eval pipeline, not hard-coded. |
| **Simulator as judge** | Wherever output is RTL/testbench code, it's linted/compiled/run with a free tool (Verilator, Icarus, GHDL, Yosys) and errors are fed back to the model for a bounded number of retries. |
| **Bring your own key** | Users supply their own API keys or run local models. The project never pays for user inference and never proxies requests. |
| **Zero required runtime deps** | The core uses only the Python standard library. Each provider SDK is an optional extra, installed only if you use that provider. |
| **Safe file handling** | Reads and writes are confined to the workspace root. Never overwrite a user's file silently: write new files, show what will be written, ask before overwriting. |
| **Short, structured output** | Every mode has a fixed output template and a `max_tokens` cap. This fights verbosity, scope drift and cost at the same time. |
| **Append-only conversations** | Chat history is never edited mid-conversation. That keeps prompt caching effective and is required by newer Claude models. |

---

## 3. Architecture

```
            ┌───────────────────────────── bartmoss CLI / REPL ─────────────────────────────┐
user ──▶    │  input ──▶ mode router ──▶ mode (prompt + context builder) ──▶ provider adapter │ ──▶ LLM API / local model
            │               ▲  (/ask /debug /tb, explicit)          │                          │
            │               │                                       ▼                          │
            │           config (mode → provider:model)      extract code blocks              │
            │                                                       │                          │
            │                                    ┌──────────────────▼──────────────────┐       │
            │                                    │ checker: verilator / iverilog / ghdl │──┐   │
            │                                    └──────────────────┬──────────────────┘  │   │
            │                                          pass │   fail│ (≤ N retries,       │   │
            │                                               │       └─ errors fed back)───┘   │
            │                          safe file writer ◀───┘                                 │
            │                          usage/cost tracker                                      │
            └──────────────────────────────────────────────────────────────────────────────────┘
evals/  ──▶ same modes + same checker, run against many models ──▶ scored report
```

### 3.1 Package layout

```
bartmoss/
  cli.py            argparse entry point + interactive REPL
  context.py        per-session state; builds system prompts; one place that calls models
  config.py         defaults + user JSON config + env overrides
  ui.py             ANSI colours, banner/mascot, status lines (no deps)
  files.py          workspace-confined read/write, diff preview, confirm
  extract.py        pull fenced code blocks out of model replies
  usage.py          token + estimated cost tracking per session
  providers/
    base.py         Provider interface, ChatResult, ProviderError
    anthropic_provider.py   Claude via official `anthropic` SDK (optional extra)
    openai_compat.py        any OpenAI-compatible endpoint via `openai` SDK (optional extra)
    mock.py         offline, deterministic provider for tests and demos
  sim/
    tools.py        detect + run verilator / iverilog (SV) and nvc / ghdl (VHDL)
    loop.py         generate → check → feed back errors → retry
  modes/
    ask.py  debug.py  tb.py   one run_<mode>() each, returning a result object (used by evals too)
  prompts/          one Markdown system prompt per mode (+ shared guard.md rules, tb_vhdl.md variant)
  evals.py          eval runner (`bartmoss eval`)
evals/cases/        eval cases (debug/, tb/), each a folder with RTL + meta.json
evals/results/      saved eval runs (JSON)
tests/              stdlib unittest suite (no network, no API keys)
```

### 3.2 Provider adapter contract

```python
class Provider:
    name: str
    def chat(self, system: str, messages: list[dict], *, model: str,
             max_tokens: int, effort=None, on_text=None) -> ChatResult: ...

ChatResult(text, model, stop_reason, input_tokens, output_tokens,
           cache_read_tokens, cache_write_tokens, refused: bool)
```

| Provider key | Library | Covers |
|---|---|---|
| `anthropic` | `anthropic` SDK | Claude Haiku / Sonnet / Opus / Fable |
| `openai` | `openai` SDK | OpenAI models |
| `openai_compat` (with `base_url`) | `openai` SDK | DeepSeek, Qwen, Kimi, Mistral, Gemini (OpenAI-compatible endpoint), OpenRouter, **Ollama (local, free)**, LM Studio, vLLM |
| `mock` | none | offline tests, demos, CI |

Model IDs and prices change often. They live in config, not code, and should be checked against each provider's docs when you set them.

### 3.3 Configuration

Config is resolved in this order (later wins): built-in defaults → `~/.config/bartmoss/config.json` → `./bartmoss.json` → environment variables → CLI flags.

```json
{
  "default_model": "anthropic:claude-haiku-5-5",
  "modes": {
    "ask":   {"model": null, "max_tokens": 4000},
    "debug": {"model": null, "max_tokens": 16000, "max_retries": 2},
    "tb":    {"model": null, "max_tokens": 16000, "max_retries": 2}
  },
  "providers": {
    "ollama":   {"type": "openai_compat", "base_url": "http://localhost:11434/v1", "api_key_env": null},
    "deepseek": {"type": "openai_compat", "base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"}
  },
  "simulator": "auto",
  "output_dir": "bartmoss_out"
}
```

API keys are **only** read from environment variables or a git-ignored `.env`. They are never written to config files.

---

## 4. Feature modes (summary)

| # | Mode | Command | Output | Checker | Phase |
|---|---|---|---|---|---|
| 1 | Theory chat | `/ask` | text | — | 1 (web search: 2) |
| 2 | RTL debugger | `/debug <file>` | analysis + `<name>.fixed.sv` | Verilator lint (before and after) | 1 |
| 3 | Testbench generator | `/tb <file>` | `tb_<module>.sv` + `tb_<module>.md` (files only) | compile + run, PASS/FAIL | 1 |
| 4 | Verification planner | `/vplan <spec>` | `<stem>.vplan.md`: features, checks, coverage, edge cases | structure check | 2 ✅ |
| 5 | UVM architecture planner | `/uvmplan <spec>` | `<stem>.uvmplan.md`: components, sequences, scoreboard, tests, build order | structure check | 2 ✅ |
| 6 | UVM component builder | `/uvm <component>` | SV/UVM files | compile with a UVM-capable simulator | 3 |
| 7 | Waveform reviewer | `/wave <vcd> --signals … --window …` | text analysis | — | 3 |
| 8 | Workflow orchestrator | `/flow …` | runs tool flows | tool exit codes | 5 |

---

## 5. Phased roadmap and tracker

Legend: `[x]` done · `[ ]` not started · `[~]` in progress. Each phase has an **exit criterion**. Don't start the next phase's hard items until it's met.

### Phase 0: Foundations
- [x] **P0.1** Write project specification (this document)
- [x] **P0.2** Repository skeleton: package layout, `pyproject.toml` with optional extras, `.gitignore`, `.env.example`, `CLAUDE.md`
- [x] **P0.3** `git init`, first commit, push to GitHub (public), choose licence. *2026-10-24: public at github.com/GravityID21/Bartmoss under MIT. Sensitive-info sweep was clean before publishing; `.env`, `idea.txt` and eval output folders are ignored; commits use the GitHub no-reply email.*
- [x] **P0.4** Install a modern Python (`brew install python@3.12`) and recreate `.venv` (system Python 3.9 is end-of-life). *The code still supports 3.9; CI tests both.*
- [x] **P0.5** Install free simulators: Verilator 5.050, Icarus Verilog 13.0, and NVC 1.23 for VHDL (`brew install nvc`). The Homebrew GHDL cask was disabled on 2026-09-01 because it fails Gatekeeper, so GHDL is supported where it installs cleanly (e.g. Linux CI).

**Exit:** `python -m bartmoss --help` runs; `python -m unittest` passes with no network.

### Phase 1: MVP (single-file RTL assistant)
Core
- [x] **P1.1** Provider interface + `mock` provider (offline, deterministic)
- [x] **P1.2** Anthropic provider: streaming, prompt caching on the system prompt, `effort` setting, refusal handling (+ server-side fallback on models that support it)
- [x] **P1.3** OpenAI-compatible provider (OpenAI, DeepSeek, Qwen, Gemini compat, OpenRouter, Ollama)
- [x] **P1.4** Config loader (defaults → user → project → env → flags), `provider:model` syntax
- [x] **P1.5** Workspace-confined file reader/writer with overwrite confirmation
- [x] **P1.6** Code-block extractor for model output
- [x] **P1.7** Usage tracker: tokens per call and per session, printed with `/usage`

Modes
- [x] **P1.8** `/ask`: theory Q&A, multi-turn in the REPL, refuses off-topic requests and declines to write code
- [x] **P1.9** `/debug <file>`: Verilator lint → model analysis + fixed file → re-lint → bounded retry → write `<name>.fixed.<ext>` (never overwrite the original)
- [x] **P1.10** `/tb <file>`: generate directed self-checking TB + Markdown notes → compile and run → retry on compile errors → report PASS/FAIL. A FAIL is flagged as a possible DUT bug, not "fixed" silently.

Simulator integration
- [x] **P1.11** Tool detection (`verilator`, `iverilog`, `ghdl`) with clear install hints when missing
- [x] **P1.12** Verilator lint + `--binary --timing` run; Icarus `-g2012` compile + `vvp` run
- [x] **P1.13** Check-and-retry loop with error-text truncation (keep context small)
- [x] **P1.14** VHDL path via NVC or GHDL: VHDL-2008 lint in `/debug`, a `tb_vhdl` prompt variant and compile/run in `/tb`, VHDL eval cases. NVC is preferred because GHDL's macOS package is disabled.

Interface
- [x] **P1.15** One-shot CLI (`bartmoss debug file.sv`) and interactive REPL (`bartmoss`)
- [x] **P1.16** Styled terminal: colour scheme, ASCII mascot banner, `NO_COLOR` support, zero deps
- [x] **P1.17** REPL commands: `/help /ask /debug /tb /model /models /doctor /usage /clear /quit`

Evaluation
- [x] **P1.18** Eval case format (`evals/cases/<mode>/<case>/meta.json` + RTL files), plus `evals/validate.py` to prove each case is valid without model calls
- [x] **P1.19** `bartmoss eval --mode debug|tb --model provider:model`: simulator-graded scoring, JSON results + Markdown summary
- [x] **P1.20** Seed eval set: 10–20 cases (hand-written bugs + modules from your own `RTL_projects_public` repos). *16 cases (10 debug, 6 tb, 2 of them VHDL); 9 come from your UDP, axi4_crossbar and riscv repos, and 3 need context files. All pass `python evals/validate.py`; see `evals/README.md`.*
- [x] **P1.21** First model comparison run (at least: one Claude model, one open model via Ollama or an API), results committed to `evals/results/`. *2026-10-24, 16 cases: Haiku 5.5 scored debug 0.93 and tb 1.00 (18/18 mutants) for ~$0.10; local Qwen2.5-Coder 7B scored debug 0.45 and tb 0.05. Table in README.* *2026-10-24: Claude Haiku 5.5 scored 1.00 on debug (3/3) and 1.00 on tb (6/6 mutants caught) for about $0.03 in total. The seed set is too easy to tell models apart, so P1.20 needs harder cases. Still to do: an open-model run.* *2026-10-24, 14-case set: Haiku 5.5 scored debug 0.92 (8/9; missed crc32_bitrev on one run but fixed it on a rerun) and tb 1.00 (15/15 mutants) for about $0.06. Model outputs are now saved under `evals/results/<run>/<case>/`.*

Quality
- [x] **P1.22** Offline unit tests (extractor, file safety, config, modes with the mock provider, simulator wrapper skipped if no tool)
- [x] **P1.23** README with install, quick start, cost notes, screenshots/GIF. *`docs/demo.svg` is rendered from a real session by `scripts/term2svg.py` (stdlib, no recording tools needed).*
- [x] **P1.24** GitHub Actions CI: run unit tests + Verilator smoke test (free for public repos). *Green on 2026-10-24 (run 40081539669): 21 unit tests on Python 3.9 and 3.12, a mock-model CLI smoke test, and `evals/validate.py` with apt Verilator 5.020, Icarus and GHDL on Ubuntu 24.04. This also verified the GHDL path, which can't be installed on macOS. `tb/axis_fifo_udp` is skipped there because it requires Verilator 5.050.*

**Exit:** on the seed eval set, at least one model reaches ≥ 80 % pass on `/tb` and `/debug`; README lets a stranger install and run it in under 10 minutes. *Met by Haiku 5.5 (0.93 / 1.00). Install and tests verified on macOS and on Ubuntu 24.04 in CI. **Phase 1 complete.***

### Phase 2: Planning modes, filtering and model comparison
- [x] **P2.1** `/vplan`: verification plan template (features → checks → coverpoints → edge cases → open questions), text only. *Feature IDs trace through checks, coverage and edge cases; the structure check (required headings, non-empty tables) retries once. Live: Haiku produced 11 features and 21 checks for axi4_arbiter for $0.003.*
- [x] **P2.2** `/uvmplan`: UVM environment architecture from a spec (agents, sequences, scoreboard, coverage, reg model), text only. *10 required sections ending in a build order; live run on axis_fifo needed one structure retry and then passed ($0.013).*
- [x] **P2.3** Optional web search for `/ask` (Claude server-side web search tool, off by default, `max_uses` cap; each search is billed). *`ask --web` or `/web on`; up to `modes.ask.web_search_max_uses` (3) searches; `pause_turn` resumed up to 3 times; sources listed and searches costed at $0.01 each. Uses `web_search_20260209` on Opus/Sonnet 4.6+ and `web_search_20250305` otherwise. Live-tested on Haiku 5.5.*
- [x] **P2.4** Optional pre-filter: cheap classifier call (in-scope / off-topic / harmful), off by default. Reuse the pattern from the course `content_filter` notebook. *`filter.enabled` in config or `/filter on`; Haiku 5.5 by default (~$0.00006 per question); fails open; refused questions stay out of the conversation. Live check 5/5, including a hardware-security question kept in scope and a prompt-injection attempt.*
- [x] **P2.5** Prompt evaluation pipeline: versioned prompts (`prompts/<mode>.v2.md`), A/B on the eval set, model-graded rubric for text modes (as in course lesson 11). *`eval --prompt debug.v2` (or `modes.<mode>.prompt`), `bartmoss compare A.json B.json`, and 5 rubric-graded planning cases scored 0.2 for structure + 0.8 × rubric items met. The judge defaults to Sonnet 5.5 because Haiku flipped verdicts on re-grading. Baseline: Haiku plans score 0.97 (vplan) and 1.00 (uvmplan).*
- [x] **P2.6** Model leaderboard: run every configured model on every mode, Markdown table of pass rate, tokens, cost, latency. *`bartmoss leaderboard` and `evals/LEADERBOARD.md`: Haiku 5.5, Sonnet 5.5 and local Qwen2.5-Coder 7B, 31 cases ×3, about $6.00 in total. Other providers (OpenAI, Gemini, DeepSeek) can be added with their API keys.*
- [x] **P2.7** Per-mode default model chosen from leaderboard results (cheapest model that meets the quality bar). *Haiku 5.5 for /debug (1.00), /tb (0.91), /vplan (0.95, Sonnet 1.00) and /uvmplan (1.00). `default_model` (used by `/ask`) also switched from Opus 5.5 to Haiku 5.5; P2.14 will check that.*
- [x] **P2.11** `eval --repeat N`: run each case N times and report mean ± spread. Single runs are noisy (Haiku failed and then passed crc32_bitrev on two runs). *Reports per-case mean, min/max and full passes, plus each repeat's overall mean; results format 2.*
- [ ] **P2.14** `/ask` eval: about 12 rubric-graded theory questions ×3, comparing Haiku 5.5, Sonnet 5.5 and local Qwen 7B (Sonnet judge, ~$1.75). Opus is left out: no mode so far gained from a bigger model, and it costs ~40× Haiku. Test it only if Sonnet clearly beats Haiku here.
- [ ] **P2.13** A/B the debug prompt's "if you find no real bugs, return the file unchanged" line: Sonnet twice called a buggy CDC synchronizer correct. Use `eval --prompt debug.v2 --repeat 3`.
- [ ] **P2.15** Bug taxonomy and a 50-case debug set, with per-category scores.
  - Add a `category` field to every debug case: reset, CDC, FSM, arithmetic/signedness, handshake/flow control, width/truncation, latch/combinational, blocking/non-blocking, off-by-one, multi-bug. Grow from 16 to about 50 cases, at least 3 per category, still mostly from your own RTL.
  - Leaderboard: per-category scores for each model, and a trend view across dated runs so prompt and model changes show up over time.
  - Grading by category: bugs that 2-state simulation can't see need another grader. Missing resets and X-propagation use Icarus 4-state runs (Verilator treats every bit as 0/1). Races and CDC bugs use SymbiYosys properties (with P3.6) or are marked "lint-graded" only. Every case must still pass `evals/validate.py` on the simulator its category uses, so nothing depends on simulator quirks (see the dropped Gray-code case).
  - Cost: building cases is free. One Haiku ×3 pass over 50 cases is about $0.07; adding Sonnet is about $0.85.
- [x] **P2.12** Grow the eval set to 30+ with harder tb cases. Haiku already catches every mutant in the current 5, so add protocol-level DUTs (AXI-Lite slave, credit link, CDC handshake) and more subtle mutants. *31 cases: 16 debug, 10 tb, 3 vplan, 2 uvmplan. New: forwarding, CDC pulse, RMII handshake, credit flow, saturation and rounding bugs. Whether they're harder is measured in the P2.6 sweep.*
- [x] **P2.8** Yosys synthesizability check as an extra `/debug` signal. *Runs `hierarchy; proc; opt_clean; check` before and after the fix; findings (latches, multiple drivers, loops) go into the prompt, and only new ones trigger a retry. Skipped for VHDL and for designs Yosys can't elaborate (e.g. package imports in the module header).*
- [x] **P2.9** Session transcript save/load (`/save`, `/load`) as JSON. *Saves the `/ask` conversation and model to the output dir; `/load` validates role order before restoring.*
- [x] **P2.10** Multi-file context: attach extra files (packages, includes) to a request. *`--with FILE` on `debug`/`tb` (REPL: extra args), sent read-only and compiled in every lint/sim run; `@path` mentions in `/ask` (path-like only, up to 5, workspace-confined). Automatic dependency discovery moved to P4.2.*

**Exit:** leaderboard published in README; each mode has a measured default model.

### Phase 3: Harder modes
- [ ] **P3.1** `/wave` waveform reviewer, VCD only. Stdlib VCD parser; user picks signals + time window; values summarized (transitions, glitches, X/Z, handshake stalls) **before** sending to the model. Never send raw VCD.
- [ ] **P3.2** `/wave` FST support (via `fst2vcd` from GTKWave if installed)
- [ ] **P3.3** `/uvm` component builder (driver, monitor, sequencer, agent, scoreboard, env, test) from a `/uvmplan` output
- [ ] **P3.4** UVM compile check.
  **Return when:** a free UVM-capable simulator is confirmed working locally. Options to evaluate: Verilator UVM support (improving but partial), Questa-Intel FPGA Starter Edition (free licence, UVM support to verify). Until then `/uvm` output is labelled **unverified**.
- [ ] **P3.5** Testbench upgrade path: constrained-random + functional coverage TBs (SystemVerilog), optionally cocotb (Python) TBs since you already use cocotb
- [ ] **P3.6** Formal property suggestions (SVA assertions) checked with SymbiYosys if installed

**Exit:** `/wave` correctly diagnoses ≥ 70 % of seeded waveform bugs; `/uvm` output compiles on at least one simulator.

### Phase 4: Agentic workspace
- [ ] **P4.1** Tool-use loop: model can request `read_file`, `list_dir`, `run_lint`, `run_sim` inside the workspace (with a per-call confirmation prompt; auto-approve allowlist for read-only tools).
  - Also an `ast` tool: Verilator's `--json-only` parse tree (modules, ports, always blocks, signal drivers and loads), so the model can query structure instead of reading whole files. No new dependency. Confirmed on Verilator 5.050; pass `-Wno-fatal`, or lint warnings end the run even though the JSON is still written.
  - Let the model write and run its own small testbench while debugging (`run_sim`), and use the VCD summaries from P3.1 once they exist.
  - **Adoption gate:** A/B the agent against today's fixed lint → fix → re-check workflow on the same eval set (score, cost, time per case, ×3). Make it the default only if it scores higher at an acceptable cost; otherwise keep it as an opt-in mode.
- [ ] **P4.2** Project awareness: read a filelist (`.f`) or `rtl/`+`tb/` layout to build context automatically
- [ ] **P4.3** Context budget manager: summarise or drop old tool output instead of letting context grow
- [ ] **P4.4** Hallucination guards: generated code must reference only ports/signals that exist in the parsed DUT; flag mismatches before simulation (can reuse the P4.1 Verilator AST)
- [ ] **P4.5** MCP server exposing Bartmoss checkers as tools (so Claude Code / other MCP clients can use them). Builds on the course MCP project.
- [ ] **P4.6** Lightweight retrieval for project-scale context (multi-module codebases, long specs for `/vplan`).
  - Chunk RTL by structure (module, always block, interface, package) using the P4.1 Verilator JSON AST, not by token count. Chunk documents by heading.
  - Keyword search with BM25 in the standard library, which is good at exact signal and register names. Optional semantic search through an embeddings extra (local Ollama embeddings or an API), combined with BM25 (hybrid). Never required.
  - Users index their own documents. Standards such as IEEE 1800 are copyrighted and can't be shipped.
  - **Return when:** P4.2 makes projects bigger than a comfortable prompt. **Adoption gate:** a multi-file retrieval eval (questions that need the right module or spec section) shows retrieval beats putting the relevant files straight in the prompt.

### Phase 5: Vendor tool orchestration
**Return when:** Phases 1–3 are stable and you have regular access to the vendor tools for testing.
- [ ] **P5.1** Tool adapters via batch/TCL modes: Questa/ModelSim (`vsim -c -do`), Quartus (`quartus_sh -t`), Vivado (`vivado -mode batch -source`), Lattice Radiant, Microchip Libero
- [ ] **P5.2** `/flow` orchestrator: lint → sim → synth → report, with a dry-run plan shown first and a confirmation for every tool invocation
- [ ] **P5.3** Report parsers: timing summary, utilisation, sim transcript errors → compact summaries for the model
- [ ] **P5.4** Sandboxing: allowlist of executables and arguments; no shell strings; working-directory jail

### Phase 6: Long-term ideas
Kept so they aren't lost. Each needs its own spec before work starts.
- [ ] **P6.1** Full lifecycle assistant: idea → spec → prototype → verification → debug → FPGA bring-up
- [ ] **P6.2** Remote tool server: users connect to a machine that has the vendor tools installed (licensing, security and cost all need investigating)
- [ ] **P6.3** Optional local web UI (localhost only) on top of the same core
- [ ] **P6.4** Hosted service. **Return when:** there's a legal review for user uploads, abuse handling with human review, and a funding model. See §9.
- [ ] **P6.5** Plugin system for user-defined modes (drop a prompt + checker into `~/.config/bartmoss/modes/`)

---

## 6. Mode specifications (Phase 1 detail)

### 6.1 `/ask`: theory chat
- **Input:** free text; multi-turn within a REPL session.
- **Scope:** digital design, RTL (SystemVerilog/Verilog/VHDL), verification, FPGA/ASIC flows, timing, CDC, protocols, EDA tools.
- **Rules:** no code generation beyond ≤ 5-line illustrative snippets; points users to `/debug` or `/tb` for code work; declines off-topic requests politely in one sentence.
- **Output:** concise answer; states uncertainty rather than guessing about specific tool versions.

### 6.2 `/debug <file>`: RTL debugger
1. Read the file (workspace-confined, size cap 200 KB).
2. Run `verilator --lint-only -Wall` (if available) and capture warnings/errors.
3. Send file + lint output to the model with the debug template.
4. Model replies with: **Summary**, **Issues** (numbered, with line refs, severity), **Fixed file** (one fenced block containing the complete file), **Notes**.
5. Lint the fixed file. If new errors appear, feed them back (≤ `max_retries`).
6. Print the analysis; write `<name>.fixed.<ext>` to the output dir (ask before overwriting).
- **Never** modifies the original file.

### 6.3 `/tb <file>`: testbench generator
1. Read DUT; detect top module name and ports (regex-based in Phase 1).
2. Model writes a **self-checking directed** SystemVerilog TB named `tb_<module>` that prints exactly `TEST PASSED` or `TEST FAILED: <reason>` and calls `$finish`, plus a Markdown file describing each test.
3. Compile + run with the chosen simulator, with a timeout.
4. Compile/elaboration error or timeout → feed errors back, retry (≤ `max_retries`).
5. `TEST FAILED` → keep the TB, report the failure as a **possible DUT bug** (the TB may also be wrong); suggest `/debug`.
6. Output is files only: `tb_<module>.sv`, `tb_<module>.md`, plus a one-line status in the terminal.

---

## 7. Evaluation pipeline

**Case format:**
```
evals/cases/debug/<case_id>/
  meta.json      {"mode":"debug","file":"buggy.sv","top":"counter","tb":"tb_ref.sv",
                  "bug":"off-by-one in wrap condition","tags":["counter","beginner"]}
  buggy.sv       the input
  tb_ref.sv      reference testbench the *fixed* file must pass (hidden from the model)
evals/cases/tb/<case_id>/
  meta.json      {"mode":"tb","file":"dut.sv","top":"fifo","mutants":["mutant1.sv"]}
  dut.sv         a correct design
  mutant1.sv     a deliberately broken copy; a good TB must FAIL on it (hidden from the model)
  reference_tb.sv  hand-written TB proving each mutant is detectable (hidden from the model)
```

**Scoring (all objective, no LLM judge in Phase 1):**
| Mode | Score |
|---|---|
| debug | 1.0 if the fixed file passes `tb_ref`; 0.25 if it compiles but fails; 0 otherwise |
| tb | compiles (0.1) + passes on correct DUT (0.2) + 0.7 × fraction of mutants caught (mutants only scored if it passed on the correct DUT, so a TB that always prints TEST PASSED scores 0.3) |

Results are saved as JSON (`evals/results/<date>_<model>_<mode>.json`) with tokens and estimated cost, plus a Markdown summary table.

**Sources:** hand-written cases; modules from your own public RTL repos (ALU, FIFOs, CRC, arbiters); open benchmarks such as VerilogEval and RTLLM. Check each benchmark's licence before vendoring; otherwise download at eval time.

---

## 8. Dependencies

| Dependency | Required? | Cost | Purpose |
|---|---|---|---|
| Python ≥ 3.9 (3.12 recommended) | yes | free | runtime |
| Python standard library | yes | free | everything in the core |
| `anthropic` | optional extra `[claude]` | free lib | Claude models |
| `openai` | optional extra `[openai]` | free lib | OpenAI + every OpenAI-compatible endpoint, including local Ollama |
| Verilator | recommended | free | lint, SV simulation (`--binary --timing`) |
| Icarus Verilog | optional | free | alternative Verilog simulator |
| NVC (or GHDL) | optional | free | VHDL-2008 lint and simulation |
| Yosys | optional (Phase 2) | free | synthesizability check |
| Ollama | optional | free (your hardware) | local open models |

**Avoided on purpose:** LangChain/LlamaIndex (heavy, not needed), vector DBs (P4.6 retrieval uses stdlib BM25; embeddings are an optional extra), web frameworks (no web UI yet), `rich`/`textual` (ANSI codes are enough), LiteLLM (two SDKs already cover all providers).

---

## 9. Safety, security and legal

| Risk | Mitigation |
|---|---|
| Harmful/illegal inputs | Hosted providers enforce their own policies. Bartmoss adds scope rules in every system prompt and an optional pre-filter (P2.4). Local/open models may have weaker safeguards, so the README says so. |
| Hosted-service liability for uploads | Not hosting (non-goal). BYO-key CLI means users interact with providers under the providers' own terms. |
| File access | All paths resolved inside the workspace root; symlink escapes rejected; size caps; originals never modified; overwrite needs confirmation. |
| Running tools | Only known executables (`verilator`, `iverilog`, `vvp`, `ghdl`, `yosys`) with argument lists (no shell), in a temp dir, with timeouts. |
| Git / account access | Not needed. Bartmoss never touches git credentials; users commit themselves. |
| API keys | Env vars / `.env` only; `.env` is git-ignored; keys never logged or printed. |
| Different safety features per model | Safety lives in Bartmoss's own code (paths, tools, confirmations), not in model behaviour. |
| Hardware-security topics | Legitimate topics (side channels, fault injection, secure boot) are allowed. Claude models may sometimes refuse; refusals are reported clearly and server-side fallback is enabled where supported. |
| User customisation (open source) | Prompts are plain Markdown files users can edit. Safety-relevant code paths (file jail, tool allowlist) are not configurable from prompts. |

---

## 10. Cost control
- Develop against the `mock` provider (free) or a local Ollama model; use paid frontier models for eval runs and real use.
- Choose the model per mode in config. Example Claude prices (per million input / output tokens, Oct 2026): Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10, Opus 5.5 $4/$20. Check current pricing before relying on these.
- System prompts are marked for prompt caching (cheaper repeat reads; only kicks in above the model's minimum cacheable length).
- Per-mode `max_tokens` caps; error feedback truncated to the first ~60 lines.
- `effort` setting per mode for Claude models (`low` for `/ask`, `medium`+ for code).
- Eval runs print an estimated cost; run the full suite only before releases.

---

## 11. Open questions
- [x] Licence: MIT (2026-10-24, matches the RTL repos)
- [ ] Project name for publishing (Bartmoss works; check PyPI/GitHub name availability).
- [ ] Which open models to standardise on for the leaderboard (depends on what runs well on your hardware via Ollama vs paid APIs)?
- [ ] Eval contamination: once the repo is public, the `fix` fields and mutants could end up in model training data. Add a canary string to each case (as BIG-bench does), or keep a private held-out set?
- [x] VHDL priority: supported in v0.1 via NVC/GHDL (P1.14), with 2 VHDL eval cases

---

## 12. Decision log
| Date | Decision | Why |
|---|---|---|
| 2026-10-24 | Debug evals get a bug taxonomy, a 50-case target and per-category scores (P2.15); no Braintrust or LangSmith | The existing JSON results, `compare` and the leaderboard already cover tracking; paid services add cost and a dependency. Categories show *where* a model fails, not just the mean |
| 2026-10-24 | Agentic debugging (P4.1) needs an AST tool and must beat the fixed workflow in an A/B before becoming the default | Today's lint → fix → re-check loop already scores 1.00 with Haiku on debug; agents cost more per task, so autonomy has to earn its place on measured results |
| 2026-10-24 | Retrieval (P4.6) is BM25 + structural chunking first, embeddings optional, and only once projects outgrow the prompt | Single-file modes fit in context; exact signal names favour keyword search; required embeddings or a vector DB would break the zero-dependency rule |
| 2026-10-24 | `default_model` switched from Opus 5.5 to Haiku 5.5 | Opus was a scaffolding-time choice, never evaluated; Haiku led or tied every measured mode at ~1/40 of Opus's per-token price. `/ask` was the only place still using Opus |
| 2026-10-24 | Haiku 5.5 is the default for debug, tb, vplan and uvmplan | Full ×3 sweep: best or within 0.05 of best in every mode, at 1/5 to 1/13 of Sonnet 5.5's cost (evals/LEADERBOARD.md) |
| 2026-10-24 | Evals abort on provider errors | A credit-balance error mid-sweep was scored as 0.0; infrastructure failures must not look like model failures |
| 2026-10-24 | Rubric judge defaults to Sonnet 5.5, not Haiku | Re-grading the same plan, Haiku flipped a verdict and missed a met item; Sonnet was consistent. About 1.4¢ per judged plan |
| 2026-10-24 | CI uses GHDL; local macOS uses NVC | NVC isn't packaged in Ubuntu 24.04, and GHDL's macOS cask is disabled. Together they test both VHDL backends |
| 2026-10-24 | Eval cases can declare `requires` (minimum tool versions) | Your axis_fifo SVA needs Verilator 5.050, and an old simulator shouldn't count as a model failure |
| 2026-10-24 | MIT licence | Same as the RTL repos the eval cases come from; simplest for an open-source CLI |
| 2026-10-23 | Features are *modes* sharing one provider layer, not separate models | Removes complexity; model-per-mode becomes a config value set by evals |
| 2026-10-23 | CLI first, no hosted service | Cost, legal exposure, scope |
| 2026-10-23 | Zero required deps; provider SDKs as optional extras | Budget and install simplicity |
| 2026-10-23 | Explicit mode commands instead of LLM routing | Free, predictable, testable; revisit if users ask |
| 2026-10-23 | Verilator as primary checker | Already installed, best free SystemVerilog support, matches your existing projects |
| 2026-10-23 | Simulator-graded evals before any LLM-judge evals | Objective, cheap, reproducible |
| 2026-10-23 | JSON config (not TOML) | `tomllib` needs Python 3.11; JSON keeps 3.9 support with zero deps |
| 2026-10-23 | Model output parsed from XML-style tags, fenced code as fallback | Tags survive nested Markdown; works across models |
| 2026-10-23 | `/debug` passes if the fix adds no *new* lint errors (compared with the original) | Single files often reference packages that aren't present; those errors aren't the model's fault |
| 2026-10-24 | `max_tokens` 32k for `/debug` and `/tb`; replies that hit the cap are flagged as `truncated` | Thinking counts toward the cap; Haiku 5.5 used about 28k tokens on the axis_fifo testbench and was cut off at 16k |
| 2026-10-23 | Claude refusal fallback (`fallbacks: "default"`) on by default for models that support it | Hardware-security topics can trip safety classifiers; set `providers.anthropic.refusal_fallback: false` to disable |
