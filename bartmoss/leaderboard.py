"""Model leaderboard from saved eval results (`bartmoss leaderboard`).

For each (mode, model, prompt) it keeps the most-repeated full run (newest on ties), ignoring
--only runs, and reports mean score, the spread across repeats, full passes, cost and time.
"""

from __future__ import annotations

import json
from pathlib import Path

MODES = ("debug", "tb", "vplan", "uvmplan")


def load_runs(results_dir: Path) -> list[dict]:
    runs = []
    for path in sorted(results_dir.glob("*.json")):
        if "_only-" in path.name:
            continue
        report = json.loads(path.read_text())
        report["_file"] = path.name
        runs.append(report)
    return runs


def best_runs(runs: list[dict]) -> dict:
    """(mode, model, prompt) -> the run with the most repeats, newest first on ties."""
    best = {}
    for run in runs:
        key = (run["mode"], run["model"], run.get("prompt", run["mode"]))
        rank = (run.get("repeat", 1), run["date"])
        if key not in best or rank >= (best[key].get("repeat", 1), best[key]["date"]):
            best[key] = run
    return best


def full_passes(run: dict) -> tuple[int, int]:
    passed = total = 0
    for row in run["cases"]:
        repeat = row.get("repeat", 1)
        passed += row.get("full_passes", int(row["score"] >= 0.99))
        total += repeat
    return passed, total


def render(best: dict) -> str:
    lines = ["| Mode | Model | Cases | Mean score | Range across repeats | Full-score runs | Cost per pass | Time per pass |",
             "|---|---|---|---|---|---|---|---|"]
    for mode in MODES:
        rows = sorted((r for (m, _, _), r in best.items() if m == mode), key=lambda r: -r["mean_score"])
        for run in rows:
            repeat = run.get("repeat", 1)
            means = run.get("run_means") or [run["mean_score"]]
            spread = f"{min(means):.2f}–{max(means):.2f}" if repeat > 1 else "single run"
            passed, total = full_passes(run)
            cost = run.get("cost_usd")
            cost_text = "—" if cost is None else ("$0 (local)" if cost == 0 else f"${cost / repeat:.3f}")
            secs = run.get("seconds")
            time_text = "—" if secs is None else f"{secs / repeat / 60:.1f} min"
            model = run["model"] + ("" if run.get("prompt", mode) == mode else f" ({run['prompt']})")
            lines.append(f"| {mode} | {model} | {len(run['cases'])} | **{run['mean_score']:.2f}** | {spread} | {passed}/{total} | "
                         f"{cost_text} | {time_text} |")
    return "\n".join(lines) + "\n"


def recommend(best: dict, margin: float = 0.05) -> dict:
    """Per mode, the cheapest model whose mean is within `margin` of the best mean. Only runs on
    the mode's largest case set count, so an old run on fewer cases can't win by being easier."""
    picks = {}
    for mode in MODES:
        rows = [r for (m, _, p), r in best.items() if m == mode and p == mode]
        if rows:
            most = max(len(r["cases"]) for r in rows)
            rows = [r for r in rows if len(r["cases"]) == most]
        if not rows:
            continue
        top = max(r["mean_score"] for r in rows)
        good = [r for r in rows if r["mean_score"] >= top - margin and r.get("cost_usd") is not None]
        if good:
            pick = min(good, key=lambda r: (r["cost_usd"] / r.get("repeat", 1), -r["mean_score"]))
            picks[mode] = (pick["model"], pick["mean_score"], top)
    return picks
