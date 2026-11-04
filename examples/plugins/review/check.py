"""Every issue must cite a line that exists in the reviewed file."""

import re


def check(output: str, source: str) -> list:
    issues = output.split("# Issues", 1)[-1].split("\n# ", 1)[0]
    bullets = [b.strip() for b in issues.splitlines() if b.strip().startswith("-")]
    lines = source.count("\n") + 1
    problems = []
    for bullet in bullets:
        if bullet in ("- none", "- None"):
            continue
        cited = re.match(r"-\s*line (\d+):", bullet)
        if not cited:
            problems.append(f"issue doesn't start with 'line N:': {bullet[:80]}")
        elif not 1 <= int(cited.group(1)) <= lines:
            problems.append(f"issue cites line {cited.group(1)}, but the file has {lines} lines")
    return problems
