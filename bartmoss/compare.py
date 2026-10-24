"""Compare two eval result files case by case (`bartmoss compare A.json B.json`), e.g. to A/B a
prompt variant or two models on the same cases."""

from __future__ import annotations

import json
from pathlib import Path

from bartmoss.ui import UI


def case_scores(report: dict) -> dict[str, float]:
    """Per-case mean score for both result formats (format 2 nests runs; format 1 is flat)."""
    return {row["case"]: row["score"] for row in report["cases"]}


def label(report: dict) -> str:
    prompt = report.get("prompt")
    extra = f" · prompt {prompt}" if prompt and prompt != report["mode"] else ""
    return f"{report['model']}{extra} · x{report.get('repeat', 1)}"


def compare(ui: UI, path_a: Path, path_b: Path) -> dict:
    a, b = (json.loads(Path(p).read_text()) for p in (path_a, path_b))
    if a["mode"] != b["mode"]:
        raise ValueError(f"different modes: {a['mode']} vs {b['mode']}")
    sa, sb = case_scores(a), case_scores(b)
    shared = sorted(set(sa) & set(sb))
    ui.print(f"  A: {label(a)}")
    ui.print(f"  B: {label(b)}")
    ui.print(f"  {'case':28} {'A':>6} {'B':>6} {'B-A':>6}")
    for case in shared:
        delta = sb[case] - sa[case]
        colour = "green" if delta > 0.005 else "red" if delta < -0.005 else "grey"
        ui.print(f"  {case:28} {sa[case]:6.2f} {sb[case]:6.2f} " + ui.style(f"{delta:+6.2f}", colour))
    mean_a = sum(sa[c] for c in shared) / len(shared) if shared else 0.0
    mean_b = sum(sb[c] for c in shared) / len(shared) if shared else 0.0
    ui.print(f"  {'mean (shared cases)':28} {mean_a:6.2f} {mean_b:6.2f} {mean_b - mean_a:+6.2f}")
    only = sorted(set(sa) ^ set(sb))
    if only:
        ui.dim(f"  not in both runs: {', '.join(only)}")
    if max(a.get("repeat", 1), b.get("repeat", 1)) == 1:
        ui.dim("  single runs are noisy; use eval --repeat 3 or more before trusting small differences")
    return {"mean_a": mean_a, "mean_b": mean_b, "cases": shared}
