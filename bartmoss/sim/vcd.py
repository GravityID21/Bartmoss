"""Stdlib VCD reader and summariser for /wave (SPEC P3.1).

Raw VCD is never sent to a model. A dump is reduced to the selected signals inside a time window,
then summarised: a clock-sampled cycle table with repeated cycles collapsed, X/Z spans, pulses
shorter than a clock period, and valid/ready handshake checks. All times are reported in ns.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, TextIO

UNITS_NS = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0, "ps": 1e-3, "fs": 1e-6}


class VcdError(ValueError):
    pass


@dataclass
class Var:
    name: str  # hierarchical, e.g. tb.dut.count
    code: str  # VCD identifier code; several vars can share one (aliases)
    width: int
    kind: str  # wire, reg, integer, real, event, ...

    @property
    def leaf(self) -> str:
        return self.name.rsplit(".", 1)[-1]


@dataclass
class Dump:
    timescale_ns: float
    vars: list
    start: float  # window, ns
    end: Optional[float]
    last_time: float = 0.0  # last timestamp read inside the window
    # code -> [(time_ns, value)], starting with the value held at the window start.
    changes: dict = field(default_factory=dict)

    def value_at(self, code: str, time: float) -> Optional[str]:
        """Value after every change at or before `time`."""
        value = None
        for t, v in self.changes.get(code, []):
            if t > time:
                break
            value = v
        return value

    def value_before(self, code: str, time: float) -> Optional[str]:
        """Value just before `time`, as a flip-flop clocked at `time` would sample it."""
        value = None
        for t, v in self.changes.get(code, []):
            if t >= time:
                break
            value = v
        return value


def fmt_time(ns: float) -> str:
    return f"{ns:.3f}".rstrip("0").rstrip(".")


def parse_time(text: str) -> float:
    """'150', '150ns', '1.5 us' -> ns (a bare number is ns)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|ms|us|ns|ps|fs)?\s*", text)
    if not match:
        raise ValueError(f"bad time {text!r}; use e.g. 150, 150ns or 1.5us")
    return float(match.group(1)) * UNITS_NS[match.group(2) or "ns"]


def parse_window(text: Optional[str]) -> tuple[float, Optional[float]]:
    """'100:500', '1us:2us', '100:' or ':500' -> (start_ns, end_ns or None)."""
    if not text:
        return 0.0, None
    if ":" not in text:
        raise ValueError(f"bad window {text!r}; use START:END, e.g. 100:500 or 1us:2us")
    start, end = text.split(":", 1)
    start_ns = parse_time(start) if start.strip() else 0.0
    end_ns = parse_time(end) if end.strip() else None
    if end_ns is not None and end_ns <= start_ns:
        raise ValueError(f"window end must be after its start: {text!r}")
    return start_ns, end_ns


def read_header(f: TextIO) -> tuple[float, list]:
    """Read declarations up to $enddefinitions. Returns (timescale in ns, vars)."""
    tokens: list[str] = []
    for line in f:
        tokens.extend(line.split())
        if "$enddefinitions" in line:
            break
    else:
        raise VcdError("no $enddefinitions found; is this a VCD file?")
    timescale, scopes, variables = 1.0, [], []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("$"):
            i += 1
            continue
        try:
            end = tokens.index("$end", i + 1)
        except ValueError:
            end = len(tokens)
        body = tokens[i + 1:end]
        if tok == "$timescale":
            match = re.fullmatch(r"(\d+)\s*(s|ms|us|ns|ps|fs)", "".join(body))
            if not match:
                raise VcdError(f"unsupported $timescale {' '.join(body)!r}")
            timescale = int(match.group(1)) * UNITS_NS[match.group(2)]
        elif tok == "$scope" and len(body) >= 2:
            scopes.append(body[1])
        elif tok == "$upscope":
            if scopes:
                scopes.pop()
        elif tok == "$var" and len(body) >= 4:
            kind, width, code, ref = body[0], body[1], body[2], body[3]
            variables.append(Var(".".join(scopes + [ref]), code, int(width) if width.isdigit() else 1, kind))
        elif tok == "$enddefinitions":
            break
        i = end + 1
    if not variables:
        raise VcdError("the VCD declares no signals")
    return timescale, variables


def _normalise(value: str, width: int) -> str:
    """Lower-case and left-extend a vector value as the VCD rules say (x and z extend, else 0)."""
    value = value.lower()
    if len(value) < width:
        pad = value[0] if value[0] in "xz" else "0"
        value = pad * (width - len(value)) + value
    return value


def read_changes(f: TextIO, timescale: float, variables: list, codes: set, start: float = 0.0,
                 end: Optional[float] = None) -> Dump:
    """Stream value changes after the header, keeping only `codes` inside [start, end].
    Several changes to one signal at the same timestamp collapse to the last one."""
    widths = {v.code: v.width for v in variables if v.code in codes}
    dump = Dump(timescale, variables, start, end)
    before: dict[str, str] = {}
    changes: dict[str, list] = {code: [] for code in codes}
    now = 0.0
    pending = False  # vector, real and string values put the identifier in the next token
    pending_value = ""
    skip_comment = False

    def record(code: str, value: str) -> None:
        if code not in widths:
            return
        if not value.startswith("r") and not value.startswith("s"):
            value = _normalise(value, widths[code])
        if now < start:
            before[code] = value
            return
        series = changes[code]
        if series and series[-1][0] == now:
            series[-1] = (now, value)
        elif not series or series[-1][1] != value:
            series.append((now, value))

    done = False
    for line in f:
        for tok in line.split():
            if skip_comment:
                skip_comment = tok != "$end"
                continue
            if pending:
                record(tok, pending_value)
                pending = False
                continue
            first = tok[0]
            if first == "#":
                now = int(tok[1:]) * timescale
                if end is not None and now > end:
                    done = True
                    break
                if now >= start:
                    dump.last_time = now
            elif first == "$":
                skip_comment = tok == "$comment"
            elif first in "bB":
                pending, pending_value = True, tok[1:]
            elif first in "rRsS":
                pending, pending_value = True, first.lower() + tok[1:]
            elif first in "01xXzZuUwWlLhH-":
                record(tok[1:], first if first in "01" else "x" if first not in "zZ" else "z")
        if done:
            break
    for code in codes:
        series = changes[code]
        if code in before and (not series or series[0][0] > start):
            series.insert(0, (start, before[code]))
    dump.changes = changes
    dump.last_time = max(dump.last_time, start)
    return dump


def select(variables: list, patterns: Iterable[str]) -> tuple[list, list]:
    """Pick signals by pattern. A bare name matches a signal's leaf name ('count'); anything with
    a dot or wildcard is a glob on the hierarchical name and may omit leading scopes ('dut.*').
    No patterns means every signal. Aliases (one VCD code, several names) are kept once.
    Returns (selected vars, patterns that matched nothing)."""
    patterns = list(patterns)
    chosen, seen, unmatched = [], set(), []
    if not patterns:
        for v in variables:
            if v.code not in seen and v.kind != "event":
                chosen.append(v)
                seen.add(v.code)
        return chosen, []
    for pattern in patterns:
        hits = 0
        for v in variables:
            if any(ch in pattern for ch in ".*?["):
                match = fnmatch.fnmatchcase(v.name, pattern) or fnmatch.fnmatchcase(v.name, "*." + pattern)
            else:
                match = v.leaf == pattern or v.name == pattern
            if match:
                hits += 1
                if v.code not in seen:
                    chosen.append(v)
                    seen.add(v.code)
        if not hits:
            unmatched.append(pattern)
    return chosen, unmatched


def rising_edges(series: list) -> list[float]:
    edges, previous = [], None
    for t, v in series:
        if v == "1" and previous == "0":
            edges.append(t)
        previous = v
    return edges


def clock_period(series: list) -> Optional[float]:
    """Period if the signal looks like a clock: at least 3 rising edges with gaps within 1 %, and a
    duty cycle between 25 % and 75 % (a regular but narrow pulse is a strobe, not a clock)."""
    edges = rising_edges(series)
    if len(edges) < 3:
        return None
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    mean = sum(gaps) / len(gaps)
    if mean <= 0 or max(gaps) - min(gaps) > 0.01 * mean:
        return None
    falls = [t for (t, v), (_, prev) in zip(series[1:], series) if v == "0" and prev == "1" and edges[0] < t < edges[-1]]
    high = [next((f for f in falls if f > e), None) for e in edges[:-1]]
    widths = [f - e for f, e in zip(high, edges) if f is not None and f < e + mean]
    if widths and not 0.25 <= (sum(widths) / len(widths)) / mean <= 0.75:
        return None
    return mean


def clock_candidates(variables: list) -> list:
    return [v for v in variables if v.width == 1 and re.search(r"(^|_)(clk|clock)(_|$)|clk$", v.leaf, re.I)]


def pick_clock(dump: Dump, candidates: list) -> Optional[tuple]:
    """(var, period) of the regular clock with the most rising edges, or None."""
    best = None
    for v in candidates:
        period = clock_period(dump.changes.get(v.code, []))
        if period is None:
            continue
        edges = len(rising_edges(dump.changes[v.code]))
        if best is None or edges > best[2]:
            best = (v, period, edges)
    return best[:2] if best else None


def fmt_value(value: Optional[str], var: Var) -> str:
    if value is None:
        return "-"
    if value[0] in "rs" or var.width == 1:
        return value[1:] if value[0] in "rs" else value
    if set(value) <= {"0", "1"}:
        number = int(value, 2)
        if var.kind in ("integer", "parameter"):
            return str(number - (1 << var.width) if value[0] == "1" and var.width == 32 else number)
        return format(number, "x").zfill((var.width + 3) // 4)
    if set(value) == {"x"}:
        return "x"
    if set(value) == {"z"}:
        return "z"
    return "b" + value


def _column_names(variables: list) -> list[str]:
    """Shortest unique suffix of each hierarchical name, for table headers."""
    names = []
    for v in variables:
        parts = v.name.split(".")
        for n in range(1, len(parts) + 1):
            candidate = ".".join(parts[-n:])
            if sum(1 for w in variables if w.name.split(".")[-n:] == parts[-n:]) == 1:
                break
        names.append(candidate)
    return names


def _xz_spans(series: list, end: float) -> list[tuple[float, float]]:
    spans = []
    for i, (t, v) in enumerate(series):
        if v[0] in "rs" or not set(v) & {"x", "z"}:
            continue
        stop = series[i + 1][0] if i + 1 < len(series) else end
        if spans and spans[-1][1] == t:
            spans[-1] = (spans[-1][0], stop)
        else:
            spans.append((t, stop))
    return spans


def _handshakes(variables: list) -> list[tuple]:
    """(valid var, ready var, payload vars) for valid/ready pairs that share a scope and prefix."""
    pairs = []
    for valid in variables:
        m = re.fullmatch(r"(.*?)(valid|vld)", valid.leaf, re.I)
        if not m or valid.width != 1:
            continue
        prefix, scope = m.group(1), valid.name[: -len(valid.leaf)]
        ready = next((r for r in variables if r.width == 1 and r.name[: -len(r.leaf)] == scope
                      and re.fullmatch(re.escape(prefix) + r"(ready|rdy)", r.leaf, re.I)), None)
        if ready is None:
            continue
        payload = [p for p in variables if p not in (valid, ready) and p.name[: -len(p.leaf)] == scope
                   and p.leaf.lower().startswith(prefix.lower())
                   and (prefix or "data" in p.leaf.lower())
                   and not re.search(r"(valid|vld|ready|rdy)$", p.leaf, re.I)]
        pairs.append((valid, ready, payload))
    return pairs


def summarise(dump: Dump, signals: list, clock: Optional[tuple] = None, max_rows: int = 150,
              source: str = "", note: str = "") -> str:
    """Compact text summary of `signals` for a model or a person."""
    end = dump.end if dump.end is not None else dump.last_time
    lines = [f"Waveform summary of {source or 'the dump'} · window {fmt_time(dump.start)}–{fmt_time(end)} ns · "
             f"all times in ns · vectors in hex unless shown as b<bits>"]
    if note:
        lines.append(note)
    period = None
    if clock:
        clock_var, period = clock
        lines.append(f"Clock: {clock_var.name}, period {fmt_time(period)} ns")
    else:
        lines.append("Clock: none found (no regular signal named clk/clock); showing raw value changes")

    lines.append("\nSignals:")
    for v in signals:
        series = dump.changes.get(v.code, [])
        if not series:
            lines.append(f"- {v.name} [{v.width}]: no value in this window")
            continue
        changes = len(series) - (1 if series[0][0] == dump.start else 0)
        if changes == 0:
            lines.append(f"- {v.name} [{v.width}]: constant {fmt_value(series[0][1], v)}")
            continue
        distinct = []
        for _, value in series:
            text = fmt_value(value, v)
            if text not in distinct:
                distinct.append(text)
        values = ", ".join(distinct) if len(distinct) <= 8 else f"{len(distinct)} distinct values"
        lines.append(f"- {v.name} [{v.width}]: {changes} changes · values {values}")

    events = []
    for v in signals:
        series = dump.changes.get(v.code, [])
        for a, b in _xz_spans(series, end)[:3]:
            events.append((a, f"{v.name} is {fmt_value(dump.value_at(v.code, a), v)} from {fmt_time(a)} to {fmt_time(b)}"))
        # Other clocks toggle faster than the period by design, so they aren't pulses.
        if period and v.width == 1 and clock_period(series) is None:
            for i in range(1, len(series) - 1):
                width = series[i + 1][0] - series[i][0]
                if width < period * 0.99 and series[i][1] in "01":
                    events.append((series[i][0], f"{v.name} {'high' if series[i][1] == '1' else 'low'} pulse only "
                                                 f"{fmt_time(width)} ns wide at {fmt_time(series[i][0])} "
                                                 f"(clock period {fmt_time(period)} ns)"))

    edges = [t for t in rising_edges(dump.changes.get(clock[0].code, [])) if t >= dump.start] if clock else []
    if edges:
        selected = {v.code for v in signals}
        for valid, ready, payload in _handshakes(signals):
            if valid.code not in selected or ready.code not in selected:
                continue
            transfers = stalls = longest = run = 0
            for k, t in enumerate(edges):
                vld, rdy = dump.value_before(valid.code, t), dump.value_before(ready.code, t)
                if vld == "1" and rdy == "1":
                    transfers += 1
                    run = 0
                elif vld == "1":
                    stalls += 1
                    run += 1
                    longest = max(longest, run)
                    if k + 1 < len(edges):
                        nxt = edges[k + 1]
                        if dump.value_before(valid.code, nxt) == "0":
                            events.append((nxt, f"{valid.leaf} dropped before {ready.leaf} at {fmt_time(nxt)} "
                                                f"(was stalled at {fmt_time(t)}; valid/ready handshakes must hold valid)"))
                        for p in payload:
                            a, b = dump.value_before(p.code, t), dump.value_before(p.code, nxt)
                            if dump.value_before(valid.code, nxt) == "1" and a != b:
                                events.append((nxt, f"{p.leaf} changed from {fmt_value(a, p)} to {fmt_value(b, p)} "
                                                    f"while {valid.leaf}=1 and {ready.leaf}=0 (stalled at {fmt_time(t)})"))
                else:
                    run = 0
            events.append((edges[0], f"handshake {valid.leaf}/{ready.leaf}: {transfers} transfers, {stalls} stalled "
                                     f"cycles (longest {longest})"))

    if events:
        lines.append("\nEvents (time order):")
        events.sort(key=lambda e: e[0])
        for _, text in events[:40]:
            lines.append(f"- {text}")
        if len(events) > 40:
            lines.append(f"- ... {len(events) - 40} more events")

    # Constants are listed above; table columns are only for signals that change.
    columns = [v for v in signals if (not clock or v.code != clock[0].code) and len(dump.changes.get(v.code, [])) > 1]
    names = _column_names(columns)
    if not columns:
        lines.append("\nEvery selected signal is constant in this window.")
    elif edges:
        lines.append(f"\nCycle table: values just before each rising edge of {clock[0].leaf} "
                     "(what a flip-flop or an assertion samples); identical cycles are collapsed")
        lines.append("| t | " + " | ".join(names) + " |")
        rows, previous, repeats, shown = [], None, 0, 0
        for t in edges:
            values = [fmt_value(dump.value_before(v.code, t), v) for v in columns]
            if values == previous:
                repeats += 1
                last_repeat = t
                continue
            if repeats:
                rows.append(f"| ... | {repeats} more identical cycle{'s' if repeats > 1 else ''} to "
                            f"{fmt_time(last_repeat)} |")
                repeats = 0
            rows.append(f"| {fmt_time(t)} | " + " | ".join(values) + " |")
            previous = values
            shown += 1
            if shown >= max_rows:
                rows.append(f"| ... | table truncated at {fmt_time(t)} ns; narrow --window or --signals |")
                break
        else:
            if repeats:
                rows.append(f"| ... | {repeats} more identical cycle{'s' if repeats > 1 else ''} to "
                            f"{fmt_time(last_repeat)} |")
        lines.extend(rows)
    else:
        lines.append("\nValue changes (time=value):")
        for v, name in zip(columns, names):
            series = dump.changes.get(v.code, [])
            listing = ", ".join(f"{fmt_time(t)}={fmt_value(val, v)}" for t, val in series[:max_rows // 4 or 1])
            more = f", ... {len(series) - max_rows // 4} more" if len(series) > max_rows // 4 else ""
            lines.append(f"- {name}: {listing}{more}")
    return "\n".join(lines) + "\n"


def load(path, patterns: Iterable[str] = (), window: Optional[str] = None, clock: Optional[str] = None,
         max_signals: int = 24) -> tuple[Dump, list, Optional[tuple], list, str]:
    """Read a VCD file for summarising. Returns (dump, signals, clock, unmatched patterns, note)."""
    start, end = parse_window(window)
    with open(path, errors="replace") as f:
        timescale, variables = read_header(f)
        signals, unmatched = select(variables, patterns)
        note = ""
        if len(signals) > max_signals:
            note = (f"Note: showing the first {max_signals} of {len(signals)} matching signals; "
                    "pass --signals to choose.")
            signals = signals[:max_signals]
        if clock:
            clock_vars, missing = select(variables, [clock])
            if missing or clock_vars[0].width != 1:
                raise VcdError(f"--clock {clock!r} doesn't name a 1-bit signal in this dump")
        else:
            clock_vars = clock_candidates(variables)
        codes = {v.code for v in signals} | {v.code for v in clock_vars}
        dump = read_changes(f, timescale, variables, codes, start, end)
    found = pick_clock(dump, clock_vars[:1] if clock else clock_vars)
    if clock and found is None:
        raise VcdError(f"--clock {clock!r} doesn't toggle regularly in this window")
    if found:
        # Name the clock the way the selection does (tb.dut.clk rather than its alias tb.clk).
        found = (next((v for v in signals if v.code == found[0].code), found[0]), found[1])
    return dump, signals, found, unmatched, note
