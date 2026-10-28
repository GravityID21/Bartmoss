"""/wave <vcd|fst>: summarise a waveform dump locally (signals, window, clock-sampled cycle table,
X/Z, short pulses, handshake checks), then ask the model where the design first misbehaves.
Raw VCD is never sent. FST dumps are converted with GTKWave's fst2vcd first."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bartmoss.context import Context, context_blocks
from bartmoss.extract import tag
from bartmoss.files import WorkspaceError
from bartmoss.sim import vcd
from bartmoss.sim.loop import CheckOutcome, generate_with_checks, hit_token_limit
from bartmoss.sim.tools import fst_to_vcd, tail


@dataclass
class WaveResult:
    status: str  # ok | summary | listed | incomplete | refused
    summary: Optional[str] = None
    analysis: Optional[str] = None
    signal: Optional[str] = None  # diagnosis: first misbehaving signal ("none" if the model found none)
    time: Optional[float] = None  # diagnosis: ns
    written: Optional[Path] = None
    attempts: int = 0
    truncated: bool = False


def parse_diagnosis(text: str) -> tuple[Optional[str], Optional[float]]:
    """(signal, time in ns) from a <diagnosis> block; signal is "none" when the model found no bug."""
    block = tag(text, "diagnosis") or ""
    signal = re.search(r"^\s*signal:\s*(\S+)", block, re.M | re.I)
    when = re.search(r"^\s*time:\s*t?=?\s*([\d.]+\s*(?:s|ms|us|ns|ps|fs)?)\b", block, re.M | re.I)
    time = None
    if when:
        try:
            time = vcd.parse_time(when.group(1))
        except ValueError:
            pass
    name = signal.group(1).strip("`'\".,") if signal else None
    return (name.lower() if name and name.lower() == "none" else name), time


def vcd_path(ctx: Context, path: str | Path) -> Path:
    """The dump as a VCD file, converting FST first."""
    src = ctx.workspace.existing_file(path)
    if src.suffix.lower() != ".fst":
        return src
    out_dir = Path(tempfile.mkdtemp(prefix="bartmoss_wave_"))
    run = fst_to_vcd(src, out_dir)
    if run is None:
        raise WorkspaceError("FST dumps need fst2vcd from GTKWave (apt install gtkwave, or brew install gtkwave), "
                             "or dump VCD instead")
    converted = out_dir / f"{src.stem}.vcd"
    if not run.ok or not converted.is_file():
        raise WorkspaceError(f"fst2vcd failed:\n{tail(run.output, 10)}")
    return converted


def run_wave(ctx: Context, path: str | Path, signals=(), window: Optional[str] = None, clock: Optional[str] = None,
             question: str = "", context_files=(), list_only: bool = False, summary_only: bool = False,
             write: bool = True) -> WaveResult:
    ui = ctx.ui
    cfg = ctx.config["modes"]["wave"]
    src = ctx.workspace.existing_file(path)
    dump_path = vcd_path(ctx, src)
    if list_only:
        with open(dump_path, errors="replace") as f:
            _, variables = vcd.read_header(f)
        for v in variables:
            ui.print(f"  {v.name} [{v.width}] {v.kind}")
        ui.dim(f"  {len(variables)} signals")
        return WaveResult("listed")

    try:
        dump, chosen, found_clock, unmatched, note = vcd.load(dump_path, signals, window, clock,
                                                               cfg.get("max_signals", 24))
    except vcd.VcdError as e:
        raise ValueError(str(e))
    for pattern in unmatched:
        ui.warn(f"no signal matches {pattern!r}; try --list")
    if not chosen:
        raise ValueError("no signals selected; run with --list to see the names in this dump")
    summary = vcd.summarise(dump, chosen, found_clock, cfg.get("max_rows", 150), src.name, note)
    if summary_only:
        ui.print(summary)
        return WaveResult("summary", summary)

    context = ctx.read_context_files(context_files)
    ui.info(f"wave {ctx.workspace.relative(src)} · {len(chosen)} signals · "
            f"{'clock ' + found_clock[0].leaf if found_clock else 'no clock'} · model {ctx.model_spec('wave')}")
    ui.dim(f"  summary is {len(summary)} characters (the dump is never sent)")

    names = {v.name.lower() for v in chosen} | {v.leaf.lower() for v in chosen}

    def known_signal(name: str) -> bool:
        name = re.sub(r"\[.*\]$", "", name).lower()
        return name in names or any(n.endswith("." + name) for n in names)

    def check(text: str) -> CheckOutcome:
        analysis = tag(text, "analysis")
        if not analysis or tag(text, "diagnosis") is None:
            return CheckOutcome(False, True, "Your reply needs both <analysis> and <diagnosis> sections. "
                                "Reply again in the required format.", data={"analysis": analysis or text.strip()})
        # Hallucination guard (P4.4): the diagnosis must name a signal that is in the summary.
        named, _ = parse_diagnosis(text)
        if named and named != "none" and not known_signal(named):
            return CheckOutcome(False, True, f"The diagnosis names {named}, which isn't in the waveform summary. Use a "
                                "signal name exactly as listed there, or none. Reply again in the required format.",
                                data={"analysis": analysis})
        return CheckOutcome(True, data={"analysis": analysis})

    message = f'<waveform_summary file="{src.name}">\n{summary}</waveform_summary>\n' + context_blocks(context)
    message += f"\n<question>\n{question.strip()}\n</question>\n" if question.strip() else \
        "\nNo question was given: find the first place the design misbehaves, if any.\n"
    loop = generate_with_checks(lambda messages: ctx.ask_model("wave", messages), message, check,
                                cfg.get("max_retries", 1),
                                lambda attempt, _: ui.dim("  asking model..." if attempt == 1 else
                                                          f"  attempt {attempt}: asking for the required format"))
    if loop.refused:
        ui.warn("The model declined this request. Try another model with --model.")
        return WaveResult("refused", summary, attempts=loop.attempts)

    reply = loop.reply.text if loop.reply else ""
    data = (loop.outcome.data if loop.outcome else {}) or loop.last_data
    signal, time = parse_diagnosis(reply)
    result = WaveResult("ok" if loop.outcome and loop.outcome.ok else "incomplete", summary, data.get("analysis"),
                        signal, time, attempts=loop.attempts,
                        truncated=bool(loop.reply and hit_token_limit(loop.reply.stop_reason)))
    if result.truncated:
        ui.warn("The reply hit max_tokens and may be incomplete; raise modes.wave.max_tokens in bartmoss.json.")
    if result.analysis:
        ui.print()
        ui.print(result.analysis.strip())
        ui.print()
    if signal and signal != "none":
        ui.info(f"first misbehaving signal: {signal}" + (f" at {vcd.fmt_time(time)} ns" if time is not None else ""))
    elif signal == "none":
        ui.ok("no misbehaviour found in this window")
    if result.analysis and write:
        report = (f"# Waveform review: {src.name}\n\n{result.analysis.strip()}\n\n"
                  f"## Waveform summary sent to the model\n\n```text\n{summary}```\n")
        result.written = ctx.workspace.write_text(f"{src.stem}.wave.md", report, ctx.confirm_overwrite)
        if result.written:
            ui.ok(f"review written to {ctx.workspace.relative(result.written)}")
    return result
