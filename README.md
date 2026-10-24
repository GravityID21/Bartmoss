# Bartmoss

An open-source AI assistant for RTL design and verification that runs in your terminal and works with any model. Generated and fixed RTL is checked with a real simulator before you see it.

> Status: Phase 1 MVP, under active development. See [SPEC.md](SPEC.md) for the full roadmap and progress tracker.

![Bartmoss fixing an off-by-one counter and generating a passing testbench](docs/demo.svg)

*A real session: Claude Haiku 5.5 finds the bug, the fix is re-linted by Verilator, and the generated testbench is compiled and run before it's written out.*

## What it does

| Command | What happens |
|---|---|
| `bartmoss ask "..."` | Hardware design theory Q&A (multi-turn in the REPL). |
| `bartmoss debug file.sv` | Lints with Verilator, asks the model for an analysis and a fixed file, re-lints the fix, retries if it introduced errors, then writes `bartmoss_out/file.fixed.sv`. Your original is never touched. |
| `bartmoss tb file.sv` | Generates a self-checking directed testbench plus Markdown notes, compiles and runs it, and retries on compile errors. Reports PASS/FAIL. |
| `bartmoss eval --mode debug\|tb` | Scores a model on the cases in `evals/cases/`, graded by the simulator. |
| `bartmoss` | Interactive REPL (`/help` lists commands). |

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[claude]"      # or ".[openai]" or ".[all]"; the core has no dependencies
brew install verilator           # free SystemVerilog simulator/linter used for checks
brew install nvc                 # optional: VHDL-2008 support (GHDL also works on Linux)
cp .env.example .env             # add the API key(s) you want to use
                                 # (or put them in ~/.config/bartmoss/.env to use them from any folder)
bartmoss doctor                  # check what's installed
```

To try it with no API key at all, use the offline mock model: `bartmoss --model mock:echo`.

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

## Early results

16 simulator-graded cases (10 debug, 6 testbench; 2 in VHDL), one run each, 2026-10-24. Scores are means; see [evals/README.md](evals/README.md) for how they're graded.

| Model | `/debug` | `/tb` | Mutants caught | Cost |
|---|---|---|---|---|
| Claude Haiku 5.5 (API) | **0.93** (9/10 fixed) | **1.00** (6/6) | 18/18 | ~$0.10 |
| Qwen2.5-Coder 7B (local, Ollama) | 0.45 (3/10 fixed) | 0.05 (0/6 working) | 0/3 | $0 |

The small local model mostly returned files that compiled but kept the bug. Its testbenches failed to compile even after simulator feedback (for example, ports that don't exist, or non-constant declarations). Single runs are noisy: Haiku has missed a different debug case on each run so far.

## Cost

You use your own API keys, so you pay only for what you run. `/usage` in the REPL shows tokens and an estimated cost. Ways to keep it cheap: use the `mock` provider or a local Ollama model while developing, give `/ask` a cheap model, and save frontier models for `/debug`, `/tb` and eval runs.

## Development

```bash
python -m unittest          # offline; simulator tests skip if Verilator is missing
```

## Licence

[MIT](LICENSE). The eval cases include RTL from the author's own MIT-licensed RTL projects.
