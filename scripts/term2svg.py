"""Render captured terminal output (with Bartmoss's ANSI colours) to an SVG for the README.

Usage: FORCE_COLOR=1 bartmoss ... | python scripts/term2svg.py --title "bartmoss" > docs/demo.svg
"""

import argparse
import html
import re
import sys
import textwrap

# Bartmoss UI palette (256-colour codes from bartmoss/ui.py) mapped to hex.
PALETTE = {"51": "#00ffff", "201": "#ff00ff", "48": "#00ff87", "220": "#ffd700", "196": "#ff0000", "244": "#808080"}
FG, BG = "#d7dae0", "#16161e"
CHAR_W, LINE_H, PAD = 8.4, 18, 16
ANSI = re.compile(r"\x1b\[([0-9;]*)m")


def spans(line: str):
    """Yield (text, colour, bold, dim) runs for one line."""
    colour, bold, dim = None, False, False
    pos = 0
    for match in ANSI.finditer(line):
        if match.start() > pos:
            yield line[pos:match.start()], colour, bold, dim
        codes = match.group(1).split(";") if match.group(1) else ["0"]
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == "0":
                colour, bold, dim = None, False, False
            elif code == "1":
                bold = True
            elif code == "2":
                dim = True
            elif code == "38" and i + 2 < len(codes) and codes[i + 1] == "5":
                colour = PALETTE.get(codes[i + 2])
                i += 2
            i += 1
        pos = match.end()
    if pos < len(line):
        yield line[pos:], colour, bold, dim


def wrap(lines: list, cols: int) -> list:
    """Wrap long plain lines (model prose); coloured status lines are short and left alone."""
    out = []
    for line in lines:
        if len(line) <= cols or ANSI.search(line):
            out.append(line)
            continue
        indent = re.match(r"\s*(\d+\.\s|-\s)?", line).end()
        out.extend(textwrap.wrap(line, cols, subsequent_indent=" " * indent) or [""])
    return out


def render(text: str, title: str, cols: int = 100) -> str:
    lines = wrap(text.rstrip("\n").split("\n"), cols)
    width_chars = max(len(ANSI.sub("", line)) for line in lines)
    width = int(width_chars * CHAR_W + 2 * PAD)
    height = int(len(lines) * LINE_H + 2 * PAD + 28)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{BG}"/>',
        '<circle cx="20" cy="16" r="5.5" fill="#ff5f57"/><circle cx="38" cy="16" r="5.5" fill="#febc2e"/>'
        '<circle cx="56" cy="16" r="5.5" fill="#28c840"/>',
        f'<text x="{width / 2}" y="20" fill="#808080" font-family="Menlo, Consolas, monospace" font-size="12" '
        f'text-anchor="middle">{html.escape(title)}</text>',
        f'<g font-family="Menlo, Consolas, \'DejaVu Sans Mono\', monospace" font-size="14" fill="{FG}" xml:space="preserve">',
    ]
    for n, line in enumerate(lines):
        y = PAD + 28 + (n + 1) * LINE_H - 4
        parts = []
        for chunk, colour, bold, dim in spans(line):
            attrs = []
            if colour:
                attrs.append(f'fill="{colour}"')
            if bold:
                attrs.append('font-weight="bold"')
            if dim:
                attrs.append('opacity="0.6"')
            parts.append(f'<tspan {" ".join(attrs)}>{html.escape(chunk)}</tspan>' if attrs else html.escape(chunk))
        out.append(f'<text x="{PAD}" y="{y}">{"".join(parts)}</text>')
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="bartmoss")
    parser.add_argument("--cols", type=int, default=100)
    args = parser.parse_args()
    sys.stdout.write(render(sys.stdin.read(), args.title, args.cols))


if __name__ == "__main__":
    main()
