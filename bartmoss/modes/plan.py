"""/vplan and /uvmplan: text-only planning modes. Input is a spec, README or RTL file; output is a
Markdown plan with required sections, written to <stem>.<mode>.md. A missing section is sent back
to the model as a retry, the same way lint errors are in /debug."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import tag
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit

SECTIONS = {
    "vplan": ["Overview", "Features", "Checks", "Coverage", "Edge cases", "Open questions"],
    "uvmplan": ["Overview", "Environment", "Components", "Transactions", "Sequences",
                "Scoreboard and reference model", "Coverage", "Register model", "Tests", "Build order"],
}
# Sections that must contain a Markdown table with at least one data row.
TABLE_SECTIONS = {
    "vplan": ["Features", "Checks", "Coverage"],
    "uvmplan": ["Components", "Sequences", "Tests"],
}


@dataclass
class PlanResult:
    status: str  # ok | incomplete | refused
    plan: Optional[str] = None
    missing: tuple = ()
    written: Optional[Path] = None
    attempts: int = 0
    truncated: bool = False


def split_sections(markdown: str) -> dict[str, str]:
    """Top-level '# Heading' sections, keyed by heading text."""
    sections, current = {}, None
    for line in markdown.splitlines():
        heading = re.match(r"^#\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return sections


def structure_problems(mode: str, plan: str) -> list[str]:
    sections = split_sections(plan)
    problems = [f"missing section '# {name}'" for name in SECTIONS[mode] if name not in sections]
    for name in TABLE_SECTIONS[mode]:
        rows = [l for l in sections.get(name, "").splitlines() if l.strip().startswith("|")]
        # Header row + separator row + at least one data row.
        if name in sections and len(rows) < 3:
            problems.append(f"section '# {name}' needs a table with at least one row")
    return problems


def docs_query(src_path: Path, source: str, extra: str = "") -> str:
    """What to look up in the project's documents for a plan of `src_path`: the file's stem, the
    units it defines and the identifiers in each module header (its ports and parameters)."""
    from bartmoss.project import strip_comments

    text = strip_comments(source, src_path.suffix.lower() in (".vhd", ".vhdl"))
    headers = re.findall(r"\b(?:module|interface|entity)\s+(\w+)(.*?)(?:\);|\bis\b)", text, re.S | re.I)
    words = [src_path.stem] + [w for name, header in headers for w in [name] + re.findall(r"[A-Za-z_]\w*", header)]
    return " ".join([extra] + list(dict.fromkeys(words)))


def spec_excerpts(ctx: Context, mode: str, src_path: Path, source: str, query: str = "") -> str:
    """Sections of the project's documents relevant to this design (P4.6), for `--docs`."""
    from bartmoss.retrieval import excerpt_blocks

    cfg = ctx.config.get("retrieval", {})
    own = ctx.workspace.relative(src_path)
    hits = ctx.retrieval().search(docs_query(src_path, source, query), cfg.get("plan_k", 6), kind="doc",
                                  allow=lambda rel: rel != own)
    text, used = excerpt_blocks(hits, cfg.get("max_context_chars", 24_000))
    ctx.ui.dim("  spec sections: " + (", ".join(f"{h.chunk.title} ({h.chunk.source})" for h in used) or
                                      "none found in the project's documents"))
    return text


def run_plan(ctx: Context, mode: str, path: str | Path, write: bool = True, context_files=(), docs: bool = False,
             docs_query_text: str = "") -> PlanResult:
    """`docs` adds the project document sections that best match the file (its name, units and
    ports, plus `docs_query_text`) as <excerpt> context: for designs whose spec is too long to send."""
    ui = ctx.ui
    src_path, source = ctx.workspace.read_text(path)
    context = ctx.read_context_files(context_files, for_file=src_path)
    ui.info(f"{mode} for {ctx.workspace.relative(src_path)} · model {ctx.model_spec(mode)}")
    excerpts = spec_excerpts(ctx, mode, src_path, source, docs_query_text) if docs else ""

    def check(text: str) -> CheckOutcome:
        plan = tag(text, "plan") or text.strip() + "\n"
        problems = structure_problems(mode, plan)
        if problems:
            return CheckOutcome(False, True,
                                "Your plan doesn't match the required structure:\n- " + "\n- ".join(problems) +
                                "\nReply again with the complete plan inside <plan> tags, using exactly the required headings.",
                                data={"plan": plan, "missing": problems})
        return CheckOutcome(True, data={"plan": plan})

    def on_attempt(attempt: int, previous: Optional[CheckOutcome]) -> None:
        ui.dim("  asking model..." if attempt == 1 else f"  attempt {attempt}: asking for the missing sections")

    message = f'<file name="{src_path.name}">\n{source}</file>\n' + context_blocks(context) + excerpts
    loop = generate_with_checks(lambda messages: ctx.ask_model(mode, messages), message, check,
                                ctx.config["modes"][mode].get("max_retries", 1), on_attempt)
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return PlanResult("refused", attempts=loop.attempts)

    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    plan = data.get("plan")
    result = PlanResult("ok" if loop.outcome and loop.outcome.ok else "incomplete", plan,
                        tuple(data.get("missing", ())), attempts=loop.attempts,
                        truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)))
    if result.truncated:
        ui.warn(f"The reply hit max_tokens and may be incomplete; raise modes.{mode}.max_tokens in bartmoss.json.")
    if plan and write:
        result.written = ctx.workspace.write_text(f"{src_path.stem}.{mode}.md", plan, ctx.confirm_overwrite)

    if plan:
        sections = split_sections(plan)
        ui.print()
        ui.print(sections.get("Overview", "").strip())
        ui.print()
        counts = []
        for name in TABLE_SECTIONS[mode]:
            rows = [l for l in sections.get(name, "").splitlines() if l.strip().startswith("|")]
            counts.append(f"{max(len(rows) - 2, 0)} {name.lower()}")
        ui.dim("  " + " · ".join(counts))
    if result.written:
        ui.ok(f"plan written to {ctx.workspace.relative(result.written)}")
    if result.status == "incomplete":
        ui.warn("plan is incomplete: " + "; ".join(result.missing))
    return result
