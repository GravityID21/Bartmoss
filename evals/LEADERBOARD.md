# Leaderboard

Generated with `bartmoss leaderboard` on 2026-10-24. Rows with 16 debug / 10 tb / 3 vplan / 2 uvmplan cases are full ×3 sweeps of the current set. Rows marked "single run" are older runs on a smaller set and aren't directly comparable.

| Mode | Model | Cases | Mean score | Range across repeats | Full-score runs | Cost per pass | Time per pass |
|---|---|---|---|---|---|---|---|
| debug | anthropic:claude-haiku-5-5 | 16 | **1.00** | 1.00–1.00 | 48/48 | $0.021 | 3.0 min |
| debug | anthropic:claude-sonnet-5-5 | 16 | **0.92** | 0.91–0.95 | 43/48 | $0.266 | 3.0 min |
| debug | ollama:qwen2.5-coder:7b | 16 | **0.44** | 0.36–0.53 | 13/48 | $0 (local) | 6.3 min |
| tb | anthropic:claude-haiku-5-5 | 6 | **1.00** | single run | 6/6 | — | — |
| tb | anthropic:claude-sonnet-5-5 | 10 | **0.79** | 0.73–0.82 | 23/30 | $0.747 | 9.5 min |
| tb | ollama:qwen2.5-coder:7b | 10 | **0.07** | 0.03–0.09 | 0/30 | $0 (local) | 23.8 min |
| vplan | anthropic:claude-sonnet-5-5 | 3 | **1.00** | 1.00–1.00 | 9/9 | $0.267 | 2.9 min |
| vplan | anthropic:claude-haiku-5-5 | 3 | **0.97** | single run | 2/3 | — | — |
| uvmplan | anthropic:claude-haiku-5-5 | 2 | **1.00** | single run | 2/2 | — | — |

## Findings

- **Debug: Haiku 5.5 beat Sonnet 5.5** (1.00 vs 0.92) at about 1/13 of the cost. Sonnet's misses were real: twice it declared the CDC pulse synchronizer bug-free, even noting the rising-only edge detect once without fixing it, and its `div4` fixes added new sign-extension bugs. Haiku is now the default model for `/debug`.
- **Testbenches are the hardest mode.** Sonnet scored 0.79. Its 7 failing runs had wrong expected values on correct designs, such as the arbiter's first grant after reset, or the credit count after reset.
- **The local 7B model isn't usable for these tasks**: debug 0.44, tb 0.07. Its testbenches rarely compile even with simulator feedback.
- **Missing runs.** The sweep stopped when the Anthropic credit balance ran out. Haiku tb/vplan/uvmplan ×3, Sonnet uvmplan ×3 and Qwen's planning runs (which need the Sonnet judge) are still to do. Runs that hit the credit error were discarded, and evals now abort on provider errors instead of scoring them as zero.
