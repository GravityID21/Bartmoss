# Fine-tuning data

Bartmoss checks every reply with a real tool before you see it, which makes its runs a source of verified training examples. With logging on, a run whose output passed its checks is saved. `bartmoss export-training` turns those runs into a dataset for fine-tuning an open model, for example a LoRA adapter for Qwen2.5-Coder.

## Collect

Add this to `bartmoss.json` in your project (or `~/.config/bartmoss/config.json` for every project):

```json
{"training": {"log": true}}
```

From then on, each passing `tb`, `formal`, `debug`, `vplan`, `uvmplan`, `wave` or plugin run appends one record to `bartmoss_out/training/log.jsonl`. A record holds the system prompt, your first message and the final reply. Retries are folded away, so each example reads as if the model got it right first time. The log stays on your machine and contains your RTL, so treat it like your source code.

What's never logged:
- runs whose result failed, and runs that couldn't be checked because no simulator is installed
- `debug --agent` runs, whose answers come from several tool calls rather than one reply
- eval runs, and any text carrying the eval canary, so the benchmark can't leak into training data

## Export

```bash
bartmoss export-training                       # simulator- or solver-confirmed examples only
bartmoss export-training --level lint          # also lint-checked /debug fixes
bartmoss export-training --modes tb,formal --valid 0.15
```

This writes `bartmoss_out/training/train.jsonl` and `valid.jsonl`, one `{"messages": [system, user, assistant]}` example per line. MLX-LM, Unsloth, Axolotl and the OpenAI fine-tuning API all read this chat format. Repeated runs on the same input keep only the newest reply. The split is decided by a hash of the input, so an example stays on the same side across exports. `--log` reads other logs inside the workspace; run from a parent directory with `--root` to merge projects.

| `--level` | Keeps |
|---|---|
| `sim` (default) | testbenches that passed on the design, properties that held on it |
| `lint` | also `/debug` fixes and plugin output that lint (and synthesise) with no new errors |
| `structure` | also plans, waveform reviews and plugins where only the format was checked |

A passing testbench shows that the testbench and the design agree. It doesn't prove the design is right, so use designs you trust.

## Train (example: Apple Silicon with MLX-LM)

A 4-bit 14B model trains as a LoRA adapter on a 48 GB Mac. On a 16 GB Mac, use the 7B.

```bash
pip install mlx-lm
mlx_lm.lora --model mlx-community/Qwen2.5-Coder-14B-Instruct-4bit --train \
    --data bartmoss_out/training --iters 600 --batch-size 1
mlx_lm.server --model mlx-community/Qwen2.5-Coder-14B-Instruct-4bit --adapter-path adapters
```

Check the flags against your MLX-LM version. `mlx_lm.server` speaks the OpenAI API, so you can score the tuned model with Bartmoss's own evals. Add a provider to `bartmoss.json`:

```json
{"providers": {"mlx": {"type": "openai_compat", "base_url": "http://localhost:8080/v1", "api_key_env": null}}}
```

Then run `bartmoss --model mlx:mlx-community/Qwen2.5-Coder-14B-Instruct-4bit eval --mode tb --repeat 2` and compare against the base model with `bartmoss compare`. A few hundred examples is a realistic start, and the evals tell you whether the adapter helped.
