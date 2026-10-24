# Leaderboard

Generated with `bartmoss leaderboard` on 2026-10-24. Every row is three runs per model over the current case set: 54 debug (50 bugs in 10 categories plus 4 bug-free controls), 10 testbench, 3 vplan, 2 uvmplan and 12 `/ask` questions. "Cost per pass" is one run over all of a mode's cases, including the Sonnet 5.5 judge for vplan, uvmplan and ask (so the local model's rows for those aren't free).

| Mode | Model | Cases | Mean score | Range across repeats | Full-score runs | Cost per pass | Time per pass |
|---|---|---|---|---|---|---|---|
| debug | anthropic:claude-haiku-5-5 | 54 | **0.99** | 0.99–1.00 | 160/162 | $0.078 | 11.2 min |
| debug | anthropic:claude-sonnet-5-5 | 54 | **0.98** | 0.96–1.00 | 158/162 | $0.949 | 10.6 min |
| debug | ollama:qwen2.5-coder:7b | 54 | **0.40** | 0.38–0.42 | 36/162 | $0 (local) | 27.4 min |
| tb | anthropic:claude-haiku-5-5 | 10 | **0.91** | 0.82–1.00 | 27/30 | $0.107 | 16.0 min |
| tb | anthropic:claude-sonnet-5-5 | 10 | **0.79** | 0.73–0.82 | 23/30 | $0.747 | 9.5 min |
| tb | ollama:qwen2.5-coder:7b | 10 | **0.07** | 0.03–0.09 | 0/30 | $0 (local) | 23.8 min |
| vplan | anthropic:claude-sonnet-5-5 | 3 | **1.00** | 1.00–1.00 | 9/9 | $0.267 | 2.9 min |
| vplan | anthropic:claude-haiku-5-5 | 3 | **0.95** | 0.93–0.97 | 5/9 | $0.058 | 2.3 min |
| vplan | ollama:qwen2.5-coder:7b | 3 | **0.22** | 0.13–0.30 | 0/9 | $0.021 | 2.1 min |
| uvmplan | anthropic:claude-haiku-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.041 | 2.1 min |
| uvmplan | anthropic:claude-sonnet-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.251 | 2.7 min |
| uvmplan | ollama:qwen2.5-coder:7b | 2 | **0.41** | 0.32–0.47 | 0/6 | $0.015 | 2.3 min |
| ask | ollama:qwen2.5-coder:7b | 12 | **0.18** | 0.14–0.21 | 0/36 | $0.058 | 3.4 min |

### Debug score by bug category

| Category | Cases | anthropic:claude-haiku-5-5 | anthropic:claude-sonnet-5-5 | ollama:qwen2.5-coder:7b |
|---|---|---|---|---|
| arithmetic | 6 | 1.00 | 0.92 | 0.29 |
| blocking | 4 | 1.00 | 1.00 | 1.00 |
| cdc | 5 | 1.00 | 1.00 | 0.25 |
| combinational | 5 | 1.00 | 1.00 | 0.32 |
| control | 4 | 1.00 | 1.00 | 0.71 |
| fsm | 5 | 1.00 | 1.00 | 0.23 |
| handshake | 5 | 1.00 | 1.00 | 0.35 |
| multi-bug | 5 | 0.95 | 1.00 | 0.22 |
| off-by-one | 5 | 1.00 | 1.00 | 0.43 |
| reset | 5 | 1.00 | 0.95 | 0.42 |
| width | 5 | 0.95 | 0.95 | 0.33 |

## Defaults chosen from this (P2.7)

The rule is the cheapest model within 0.05 of the best mean, and only if the best mean is at least 0.7. **Claude Haiku 5.5 wins every mode with a recommendation** and is the default for `/debug`, `/tb`, `/vplan` and `/uvmplan`. It's also `default_model`, so `/ask` uses it too, though `/ask` hasn't been measured on Claude yet (see below).

## Findings

- **On debug, the cheap model is enough.** On 50 categorised bugs, Haiku 5.5 scored 0.99 and Sonnet 5.5 0.98, at $0.08 vs $0.95 per pass. The only misses were CRC cases (both models) and Sonnet's signed-rounding fix. Both left all 4 bug-free controls intact.
- **Controls catch over-eager fixing.** The local 7B model broke 3 of the 4 correct designs while "fixing" them (control score 0.71). A prompt or model that edits more aggressively can't win on the bug cases without paying for it here.
- **Testbenches are still the hardest mode.** Failing runs from both Claude models wrote wrong expected values for correct designs.
- **The local 7B model isn't usable for these tasks**: debug 0.40, tb 0.07, vplan 0.22, uvmplan 0.41, ask 0.18. Interestingly, it scores 1.00 on the blocking-assignment category, which follows a single recognisable pattern.
- **Not run (credit limit):** the `debug.v2` prompt A/B (P2.13) and the Claude `/ask` runs (P2.14). Both are built and ready, at roughly $0.55 and $1.15.
- **Spend:** about $9.65 in total across all sweeps. The 54-case Sonnet debug run alone cost $2.85, more than estimated, because the earlier per-pass figure was for 16 cases.
