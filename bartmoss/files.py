"""Workspace-confined file access. Nothing is read or written outside the workspace root,
and existing files are only overwritten after confirmation."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Callable, Optional

MAX_READ_BYTES = 200_000


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: Path, output_dir: str = "bartmoss_out"):
        self.root = Path(root).resolve()
        self.output_dir = output_dir

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        # resolve() follows symlinks, so a link pointing outside the root is rejected too.
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(f"{path} is outside the workspace {self.root}")
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def read_text(self, path: str | Path) -> tuple[Path, str]:
        resolved = self.resolve(path)
        if not resolved.is_file():
            raise WorkspaceError(f"File not found: {path}")
        size = resolved.stat().st_size
        if size > MAX_READ_BYTES:
            raise WorkspaceError(f"{path} is {size} bytes; the limit is {MAX_READ_BYTES}")
        return resolved, resolved.read_text(errors="replace")

    def existing_file(self, path: str | Path) -> Path:
        """An existing file inside the workspace, with no size cap: for inputs such as waveform
        dumps that are streamed rather than read whole."""
        resolved = self.resolve(path)
        if not resolved.is_file():
            raise WorkspaceError(f"File not found: {path}")
        return resolved

    def output_path(self, name: str) -> Path:
        return self.resolve(Path(self.output_dir) / name)

    def write_text(
        self,
        name: str,
        content: str,
        confirm: Optional[Callable[[str, str], bool]] = None,
    ) -> Optional[Path]:
        """Write into the output dir. If the file exists with different content, `confirm(path, diff)`
        decides whether to overwrite. Returns the path written, or None if the user declined."""
        target = self.output_path(name)
        if target.exists():
            old = target.read_text(errors="replace")
            if old == content:
                return target
            diff = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True), content.splitlines(keepends=True), "existing", "new"
                )
            )
            if confirm is None or not confirm(self.relative(target), diff):
                return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target
