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


def case_categories(cases_root: Path, mode: str) -> dict[str, str]:
    """Category of each case, read from its meta.json (older results don't record it)."""
    out = {}
    for meta in sorted((cases_root / mode).glob("*/meta.json")):
        category = json.loads(meta.read_text()).get("category")
        if category:
            out[meta.parent.name] = category
    return out


def render_categories(best: dict, cases_root: Path, mode: str = "debug") -> str:
    """Mean score per bug category for each model, on the mode's largest case set."""
    categories = case_categories(cases_root, mode)
    runs = [r for (m, _, p), r in best.items() if m == mode and p == mode]
    if not runs or not categories:
        return ""
    most = max(len(r["cases"]) for r in runs)
    runs = sorted((r for r in runs if len(r["cases"]) == most), key=lambda r: -r["mean_score"])
    names = sorted(set(categories.values()))
    lines = ["| Category | Cases | " + " | ".join(r["model"] for r in runs) + " |",
             "|---|---|" + "---|" * len(runs)]
    for name in names:
        cells = []
        for run in runs:
            scores = [row["score"] for row in run["cases"] if categories.get(row["case"]) == name]
            cells.append(f"{sum(scores) / len(scores):.2f}" if scores else "—")
        count = sum(1 for c in categories.values() if c == name)
        lines.append(f"| {name} | {count} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_trend(runs: list[dict]) -> str:
    """Every saved full run in date order, so prompt and model changes show up over time."""
    lines = ["| Date | Mode | Model | Prompt | Cases | Repeats | Mean |", "|---|---|---|---|---|---|---|"]
    for run in sorted(runs, key=lambda r: (r["date"], r["mode"], r["model"], r["_file"])):
        lines.append(f"| {run['date']} | {run['mode']} | {run['model']} | {run.get('prompt', run['mode'])} | "
                     f"{len(run['cases'])} | {run.get('repeat', 1)} | {run['mean_score']:.2f} |")
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
