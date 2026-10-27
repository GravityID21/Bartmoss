# Leaderboard

Generated with `bartmoss leaderboard`; last updated 2026-10-27. Every row is three runs per model over the current case set: 54 debug (50 bugs in 10 categories plus 4 bug-free controls), 10 testbench (9 for random testbenches, which are SystemVerilog only), 3 vplan, 2 uvmplan, 12 `/ask` questions, 11 waveform reviews and 5 formal designs. "Cost per pass" is one run over all of a mode's cases, including the Sonnet 5.5 judge for vplan, uvmplan, ask and wave (so the local model's rows for those aren't free). Rows marked with a prompt name, such as `(debug.v2)`, are prompt variants.

| Mode | Model | Cases | Mean score | Range across repeats | Full-score runs | Cost per pass | Time per pass |
|---|---|---|---|---|---|---|---|
| debug | anthropic:claude-haiku-5-5 | 54 | **0.99** | 0.99–1.00 | 160/162 | $0.078 | 11.2 min |
| debug | anthropic:claude-sonnet-5-5 | 54 | **0.98** | 0.96–1.00 | 158/162 | $0.949 | 10.6 min |
| debug | anthropic:claude-haiku-5-5 (debug.v2) | 54 | **0.98** | 0.97–1.00 | 158/162 | $0.073 | 11.6 min |
| debug | ollama:qwen2.5-coder:7b | 54 | **0.40** | 0.38–0.42 | 36/162 | $0 (local) | 27.4 min |
| tb | anthropic:claude-haiku-5-5 | 10 | **0.91** | 0.82–1.00 | 27/30 | $0.107 | 16.0 min |
| tb | anthropic:claude-haiku-5-5 (tb_random) | 9 | **0.83** | 0.79–0.89 | 22/27 | $0.166 | 24.2 min |
| tb | anthropic:claude-sonnet-5-5 | 10 | **0.79** | 0.73–0.82 | 23/30 | $0.747 | 9.5 min |
| tb | ollama:qwen2.5-coder:7b | 10 | **0.07** | 0.03–0.09 | 0/30 | $0 (local) | 23.8 min |
| vplan | anthropic:claude-sonnet-5-5 | 3 | **1.00** | 1.00–1.00 | 9/9 | $0.267 | 2.9 min |
| vplan | anthropic:claude-haiku-5-5 | 3 | **0.95** | 0.93–0.97 | 5/9 | $0.058 | 2.3 min |
| vplan | ollama:qwen2.5-coder:7b | 3 | **0.22** | 0.13–0.30 | 0/9 | $0.021 | 2.1 min |
| uvmplan | anthropic:claude-haiku-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.041 | 2.1 min |
| uvmplan | anthropic:claude-sonnet-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.251 | 2.7 min |
| uvmplan | ollama:qwen2.5-coder:7b | 2 | **0.41** | 0.32–0.47 | 0/6 | $0.015 | 2.3 min |
| ask | anthropic:claude-sonnet-5-5 | 12 | **0.95** | 0.95–0.95 | 27/36 | $0.179 | 2.2 min |
| ask | anthropic:claude-haiku-5-5 | 12 | **0.92** | 0.92–0.93 | 24/36 | $0.068 | 1.5 min |
| ask | ollama:qwen2.5-coder:7b | 12 | **0.18** | 0.14–0.21 | 0/36 | $0.058 | 3.4 min |
| wave | anthropic:claude-haiku-5-5 | 11 | **0.96** | 0.93–0.98 | 27/33 | $0.053 | 2.4 min |
| formal | anthropic:claude-haiku-5-5 | 5 | **1.00** | 1.00–1.00 | 15/15 | $0.011 | 1.7 min |

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

The rule is the cheapest model within 0.05 of the best mean, and only if the best mean is at least 0.7. **Claude Haiku 5.5 wins every mode** and is the default for all of them, including `/ask` (0.92 vs Sonnet's 0.95), `/wave` and `/formal`.

## Findings

- **On debug, the cheap model is enough.** On 50 categorised bugs, Haiku 5.5 scored 0.99 and Sonnet 5.5 0.98, at $0.08 vs $0.95 per pass. The only misses were CRC cases (both models) and Sonnet's signed-rounding fix. Both left all 4 bug-free controls intact.
- **The `debug.v2` prompt didn't help (P2.13).** It pushes harder to find bugs. On Haiku it scored 0.98 vs 0.99, losing two CRC cases and changing nothing else. Controls stayed intact. On Sonnet's weak cases it was mixed (two CRC cases up, one down, signed rounding down from 0.50 to 0.25). `/debug` keeps the original prompt.
- **Controls catch over-eager fixing.** The local 7B model broke 3 of the 4 correct designs while "fixing" them (control score 0.71). A prompt or model that edits more aggressively can't win on the bug cases without paying for it here.
- **`/ask`: Sonnet is only slightly better (P2.14).** 0.95 vs 0.92 for 2.6× the cost per pass. Both missed rubric items on the same topics (functional coverage, latch inference). Opus wasn't tested, since Sonnet didn't clearly beat Haiku.
- **`/wave` meets the Phase 3 target.** Haiku named the right signal and time (within one clock) in all 33 runs, including the bug-free control, and scored 0.96 overall. The points it lost are about the cause: on the lost CDC pulse it named the symptom but not always the toggle-synchroniser fix.
- **`/formal`: 1.00, so the set is too easy to tell models apart.** Haiku's properties held on every design and caught all 15 mutants, for about a cent per pass. Harder cases (deeper bugs, properties needing assumptions) are the next step.
- **Random testbenches score lower than directed ones (0.83 vs 0.91).** Most lost points are testbenches failing on a correct design: a sampling race in alu8, a wrong reset expectation in credit_sender, and a wrong FIFO model for one seed in axis_fifo. In 2 of 3 async_fifo_gray runs, Haiku spent the whole 32k-token budget thinking and returned no testbench. Coverage was high when a testbench worked (most runs hit every bin). It's also the slowest and most expensive Haiku mode: about 1M output tokens for three passes.
- **Testbenches are still the hardest mode.** Failing runs from both Claude models wrote wrong expected values for correct designs.
- **The local 7B model isn't usable for these tasks**: debug 0.40, tb 0.07, vplan 0.22, uvmplan 0.41, ask 0.18. Interestingly, it scores 1.00 on the blocking-assignment category, which follows a single recognisable pattern.
- **Spend:** about $11.70 in total across all sweeps. The 2026-10-27 round (Claude `/ask`, the `debug.v2` A/B, and Phase 3's wave, formal and random-tb runs) cost about $2.05.
