# Bartmoss

[![CI](https://github.com/GravityID21/Bartmoss/actions/workflows/ci.yml/badge.svg)](https://github.com/GravityID21/Bartmoss/actions/workflows/ci.yml)

An open-source AI assistant for RTL design and verification that runs in your terminal and works with any model. Generated and fixed RTL is checked with a real simulator before you see it.

> Status: Phases 1–3 complete (UVM generation deferred); Phase 4 (agent mode, project awareness, MCP server, retrieval) built and in testing. Under active development. See [SPEC.md](SPEC.md) for the full roadmap and progress tracker.

![Bartmoss fixing an off-by-one counter and generating a passing testbench](docs/demo.svg)

*A real session: Claude Haiku 5.5 finds the bug, the fix is re-linted by Verilator, and the generated testbench is compiled and run before it's written out.*

## What it does

| Command | What happens |
|---|---|
| `bartmoss ask "..."` | Hardware design theory Q&A (multi-turn in the REPL). `@path` attaches a file; `--web` (or `/web on`) lets Claude search the web, with sources listed. |
| `bartmoss debug file.sv` | Lints with Verilator and checks synthesis with Yosys (if installed), asks the model for an analysis and a fixed file, re-checks the fix, retries if it introduced errors or latches, then writes `bartmoss_out/file.fixed.sv`. Your original is never touched. |
| `bartmoss debug file.sv --agent` | Agent mode (opt-in, not yet scored): the model can read project files, query the design structure (Verilator's AST), lint, and write and run its own small testbenches before answering. Read-only tools run freely; it asks before each simulation unless you pass `--yes`. |
| `bartmoss tb file.sv` | Generates a self-checking directed testbench plus Markdown notes, compiles and runs it, and retries on compile errors. Reports PASS/FAIL. |
| `bartmoss tb file.sv --random` | Constrained-random testbench with a scoreboard and functional coverage, run with several seeds (`--seeds N`). Any failing seed fails the test and is named so you can rerun it; coverage below the target goes back to the model with the bins it missed. |
| `bartmoss wave dump.vcd [signals] --window 100:500 -q "..."` | Reviews a waveform. The dump is summarised locally (clock-sampled cycle table, X/Z spans, short pulses, valid/ready violations) and only the summary is sent, never the raw VCD. Names the first signal that misbehaves and when. `--list` shows signal names and `--summary` skips the model. FST works if GTKWave's `fst2vcd` is installed. |
| `bartmoss formal file.sv` | The model writes assertions; Bartmoss puts them in an `` `ifdef FORMAL `` block (your design is never edited) and runs a bounded model check with Yosys + yosys-smtbmc (`--prove` adds k-induction). A failing assertion is reported with a summarised counterexample as a possible design bug or wrong property. |
| `bartmoss vplan spec.md` | Writes a verification plan (features → checks → coverage → edge cases) to `bartmoss_out/spec.vplan.md`. Takes a spec, README or RTL file. |
| `bartmoss uvmplan spec.md` | Designs a UVM environment (agents, transactions, sequences, scoreboard, coverage, tests, build order) as `bartmoss_out/spec.uvmplan.md`. |
| `bartmoss search "where is parity computed"` | Ranked search over the project: HDL split into modules and always/assign blocks, documents split by heading. `--show` prints the excerpts. `ask --rag` attaches the best ones to a question, and `vplan file.sv --docs` adds the spec sections that match a design. Keyword ranking (BM25) on the standard library; set `retrieval.embeddings` to a local Ollama embeddings model for hybrid search. |
| `bartmoss project [file]` | Shows the project index (which file defines which module, package and interface), or the files one file depends on. `--filelist files.f` uses a filelist instead of scanning. |
| `bartmoss mcp` | Runs an MCP server so Claude Code and other MCP clients can use Bartmoss's free checkers (lint, simulate, synth check, design structure, waveform summary, formal check, project search). Register it with `claude mcp add bartmoss -- bartmoss --root /path/to/project mcp`. |
| `bartmoss eval --mode debug\|tb\|wave\|formal\|…` | Scores a model on the cases in `evals/cases/`, graded by the simulator, the formal checker or a rubric judge. |
| `bartmoss` | Interactive REPL (`/help` lists commands). |

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[claude]"      # or ".[openai]" or ".[all]"; the core has no dependencies
brew install verilator           # free SystemVerilog simulator/linter (5.050+ recommended)
brew install nvc                 # optional: VHDL-2008 support (GHDL also works on Linux)
brew install yosys z3            # optional: synthesis checks in /debug, and /formal
brew install gtkwave             # optional: FST dumps in /wave (provides fst2vcd)
cp .env.example .env             # add the API key(s) you want to use
                                 # (or put them in ~/.config/bartmoss/.env to use them from any folder)
bartmoss doctor                  # check what's installed
```

To try it with no API key at all, use the offline mock model: `bartmoss --model mock:echo`.

**Multi-file projects:** `debug`, `tb`, `formal` and `vplan` find the packages, interfaces and submodules a file depends on (by indexing the workspace, or a `.f` filelist with `--filelist`) and add them as read-only context automatically. Pass `--with` to choose the files yourself, or `--no-auto-context` to turn this off.

## Choosing models

Models are written as `provider:model`:

```bash
bartmoss --model anthropic:claude-haiku-5-5 ask "why use gray code for async FIFO pointers?"
bartmoss --model ollama:qwen2.5-coder:14b tb rtl/alu.sv      # local and free, needs Ollama
bartmoss --model deepseek:deepseek-chat debug rtl/fifo.sv
```

Built-in providers: `anthropic`, `openai`, `ollama`, `deepseek`, `openrouter`, `gemini`, `mock`. You can add any OpenAI-compatible endpoint, and set a default model per mode, in `bartmoss.json`:

```json
{
  "default_model": "anthropic:claude-sonnet-5-5",
  "modes": {"ask": {"model": "anthropic:claude-haiku-5-5"}},
  "providers": {"lmstudio": {"type": "openai_compat", "base_url": "http://localhost:1234/v1", "api_key_env": null}}
}
```

## Results

Every case run three times per model (2026-10-24 to 2026-10-27). Full table, per-category scores and notes: [evals/LEADERBOARD.md](evals/LEADERBOARD.md). See [evals/README.md](evals/README.md) for how scores are graded.

| Model | `/debug` (54) | `/tb` (10) | `/vplan` (3) | `/uvmplan` (2) | `/ask` (12) | Cost per debug + tb pass |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | **0.99** | **0.91** | 0.95 | **1.00** | 0.92 | $0.19 |
| Claude Sonnet 5.5 | 0.98 | 0.79 | **1.00** | **1.00** | **0.95** | $1.70 |
| Qwen2.5-Coder 7B (local) | 0.40 | 0.07 | 0.22 | 0.41 | 0.18 | $0 |

Phase 3 modes, Claude Haiku 5.5 only: `/wave` **0.96** (right signal and time in 33/33 runs on 11 seeded waveform bugs), `/formal` **1.00** (all 15 mutants caught across 5 designs) and `/tb --random` **0.83**, about 0.90 after raising its token cap fixed the one case that ran out of tokens (9 cases; random testbenches more often get expected values wrong than directed ones).

The debug set covers 10 bug categories (reset, CDC, FSM, arithmetic, handshake, width, combinational, blocking, off-by-one, multi-bug) plus bug-free control designs. Haiku 5.5 is the measured default. Testbench generation is the hardest mode: both Claude models sometimes write wrong expected values for a correct design.

## Cost

You use your own API keys, so you pay only for what you run. `/usage` in the REPL shows tokens and an estimated cost. Ways to keep it cheap: use the `mock` provider or a local Ollama model while developing, give `/ask` a cheap model, and save frontier models for `/debug`, `/tb` and eval runs.

## Development

```bash
python -m unittest          # offline; simulator, formal and FST tests skip if their tools are missing
python evals/validate.py    # prove every eval case is valid (no model calls)
```

## Licence

[MIT](LICENSE). The eval cases include RTL from the author's own MIT-licensed RTL projects.
