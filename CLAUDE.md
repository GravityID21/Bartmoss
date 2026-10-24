# Bartmoss: notes for Claude

- `SPEC.md` is the source of truth for scope, phases and progress. When a feature lands, tick its checkbox (`P<phase>.<n>`) and add any decision to §12.
- Core code must stay dependency-free (stdlib only, Python ≥ 3.9: use `from __future__ import annotations`, no `match`, no 3.12-only f-string quoting). Provider SDKs are optional extras imported lazily inside the provider.
- Claude calls use the official `anthropic` SDK (streaming, cached system prompt, `output_config.effort`, refusal fallback). Never send `temperature`/`top_p`/`top_k` or assistant prefill to Claude.
- Model replies are parsed from XML-style tags (`<fixed_file>`, `<tb_file>`, `<notes>`, `<analysis>`, `<plan>`, `<diagnosis>`, `<properties>`). If you change a prompt's output format, update `extract.py` callers and `providers/mock.py`.
- `/wave` never sends raw VCD: `sim/vcd.py` summarises it first. `/formal` never edits the design: properties go in an `` `ifdef FORMAL `` block, checked with Yosys + yosys-smtbmc (open-source Yosys has no `|->`/`##`; use immediate assertions with `$past`).
- Every file access goes through `files.Workspace`; every tool run goes through `sim/tools.py` (argument lists, timeouts, no shell).
- VHDL files (`.vhd/.vhdl`) use NVC or GHDL (`sim/tools.py` picks by extension) and the `tb_vhdl.md` prompt variant.
- Tests: `.venv/bin/python -m unittest`. They must pass offline with no API keys.
- Eval cases live in `evals/cases/<mode>/<case>/`. Files other than `meta["file"]` (reference TBs, mutants) are hidden from the model. Prove each new case is valid with Verilator before adding it.
