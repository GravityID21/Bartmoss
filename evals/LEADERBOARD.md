# Leaderboard

Generated with `bartmoss leaderboard` on 2026-10-24: 31 cases, each run three times per model. "Cost per pass" is one run over all of a mode's cases, including the Sonnet 5.5 judge for vplan/uvmplan (so the local model's planning rows aren't free).

| Mode | Model | Cases | Mean score | Range across repeats | Full-score runs | Cost per pass | Time per pass |
|---|---|---|---|---|---|---|---|
| debug | anthropic:claude-haiku-5-5 | 16 | **1.00** | 1.00–1.00 | 48/48 | $0.021 | 3.0 min |
| debug | anthropic:claude-sonnet-5-5 | 16 | **0.92** | 0.91–0.95 | 43/48 | $0.266 | 3.0 min |
| debug | ollama:qwen2.5-coder:7b | 16 | **0.44** | 0.36–0.53 | 13/48 | $0 (local) | 6.3 min |
| tb | anthropic:claude-haiku-5-5 | 10 | **0.91** | 0.82–1.00 | 27/30 | $0.107 | 16.0 min |
| tb | anthropic:claude-sonnet-5-5 | 10 | **0.79** | 0.73–0.82 | 23/30 | $0.747 | 9.5 min |
| tb | ollama:qwen2.5-coder:7b | 10 | **0.07** | 0.03–0.09 | 0/30 | $0 (local) | 23.8 min |
| vplan | anthropic:claude-sonnet-5-5 | 3 | **1.00** | 1.00–1.00 | 9/9 | $0.267 | 2.9 min |
| vplan | anthropic:claude-haiku-5-5 | 3 | **0.95** | 0.93–0.97 | 5/9 | $0.058 | 2.3 min |
| vplan | ollama:qwen2.5-coder:7b | 3 | **0.22** | 0.13–0.30 | 0/9 | $0.021 | 2.1 min |
| uvmplan | anthropic:claude-haiku-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.041 | 2.1 min |
| uvmplan | anthropic:claude-sonnet-5-5 | 2 | **1.00** | 1.00–1.00 | 6/6 | $0.251 | 2.7 min |
| uvmplan | ollama:qwen2.5-coder:7b | 2 | **0.41** | 0.32–0.47 | 0/6 | $0.015 | 2.3 min |

## Defaults chosen from this (P2.7)

The rule is the cheapest model within 0.05 of the best mean. **Claude Haiku 5.5 wins every evaluated mode** and is the default for `/debug`, `/tb`, `/vplan` and `/uvmplan`. `/ask` has no eval yet and keeps `default_model`.

## Findings

- **Haiku 5.5 beat Sonnet 5.5 on the code modes**: debug 1.00 vs 0.92, testbench 0.91 vs 0.79, at 1/13 and 1/7 of the cost. On planning they're level (uvmplan) or within 0.05 (vplan, where Sonnet was perfect).
- **Sonnet's debug misses were real.** Twice it declared the CDC pulse synchronizer bug-free, once even noting the rising-only edge detect without fixing it. Its `div4` fixes also added new sign-extension bugs. This is tracked as P2.13: A/B the "if you find no real bugs" prompt line.
- **Testbenches are the hardest mode.** Failing runs from both models had wrong expected values on correct designs, such as the arbiter's first grant after reset or the credit count after reset. Haiku writes long testbenches: about 19k output tokens per call, and one run hit the 32k cap.
- **The local 7B model isn't usable for these tasks**: debug 0.44, tb 0.07, vplan 0.22, uvmplan 0.41. Its plans have the right structure but miss nearly every design-specific item.
- **Spend:** the complete ×3 sweep cost about $6.00 in total, including about $0.65 of runs discarded after a credit-balance outage. Evals now abort on provider errors instead of scoring them as zero.
