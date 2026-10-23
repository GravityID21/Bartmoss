"""Terminal styling with plain ANSI codes (no dependencies). Honours NO_COLOR and non-TTY output."""

from __future__ import annotations

import os
import sys

from bartmoss import __version__

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[38;5;51m",
    "magenta": "\033[38;5;201m",
    "green": "\033[38;5;48m",
    "yellow": "\033[38;5;220m",
    "red": "\033[38;5;196m",
    "grey": "\033[38;5;244m",
}

MASCOT = r"""
    ┌─┬─┬─┬─┬─┐
  ──┤ ◉     ◉ ├──
  ──┤    ▿    ├──
  ──┤  ╰───╯  ├──
    └─┴─┴─┴─┴─┘
"""


class UI:
    def __init__(self, color: bool | None = None, out=sys.stdout, quiet: bool = False):
        if color is None:
            color = out.isatty() and "NO_COLOR" not in os.environ
        self.color = color
        self.out = out
        self.quiet = quiet

    def style(self, text: str, *names: str) -> str:
        if not self.color:
            return text
        return "".join(_CODES[n] for n in names) + text + _CODES["reset"]

    def print(self, text: str = "", end: str = "\n") -> None:
        if not self.quiet:
            print(text, end=end, file=self.out, flush=True)

    def banner(self) -> None:
        mascot_lines = MASCOT.strip("\n").splitlines()
        side = [
            "",
            self.style("B A R T M O S S", "bold", "magenta"),
            self.style(f"rtl design + verification assistant · v{__version__}", "cyan"),
            self.style("/help for commands · /quit to exit", "grey"),
            "",
        ]
        for art, text in zip(mascot_lines, side):
            self.print(self.style(art, "cyan") + "   " + text)
        self.print()

    def info(self, text: str) -> None:
        self.print(self.style("▸ ", "cyan") + text)

    def ok(self, text: str) -> None:
        self.print(self.style("✔ ", "green") + text)

    def warn(self, text: str) -> None:
        self.print(self.style("! ", "yellow") + text)

    def error(self, text: str) -> None:
        self.print(self.style("✖ ", "red") + text)

    def dim(self, text: str) -> None:
        self.print(self.style(text, "grey"))

    def stream(self, chunk: str) -> None:
        if not self.quiet:
            self.out.write(chunk)
            self.out.flush()

    def confirm(self, question: str, assume_yes: bool = False) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            return False
        answer = input(self.style("? ", "magenta") + question + " [y/N] ").strip().lower()
        return answer in ("y", "yes")
