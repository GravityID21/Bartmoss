"""Project awareness (SPEC P4.2): find the HDL files in a workspace, from a `.f` filelist or by
scanning (rtl/ and tb/ style layouts), index which file defines each module, interface and package,
and work out which files a given file depends on. Modes use this to add packages, interfaces and
submodules as read-only context automatically when no --with files are given.

Regex-based on purpose: it has to work on files that don't compile yet (that's why /debug exists)
and without any tool installed. Verilator's AST (sim/hdl_ast.py) is used where exact structure matters.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bartmoss.files import Workspace, WorkspaceError

HDL_SUFFIXES = (".sv", ".v", ".svh", ".vh", ".vhd", ".vhdl")
HEADER_SUFFIXES = (".svh", ".vh")  # `include files: never compiled on their own
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "obj_dir", "sim_build", "__pycache__", "results", "work"}
TB_DIRS = {"tb", "tbs", "test", "tests", "sim", "verif", "dv", "bench", "testbench"}
MAX_FILE_BYTES = 200_000

_SV_DEF = re.compile(r"^\s*(?:(?:virtual|static|automatic)\s+)*(module|macromodule|interface|package|program)\s+"
                     r"(?:(?:automatic|static)\s+)?(\w+)", re.M)
_VHDL_DEF = re.compile(r"^\s*(entity|package)\s+(\w+)\s+is\b", re.M | re.I)
_VHDL_USE = re.compile(r"\bentity\s+work\.(\w+)|\bcomponent\s+(\w+)|\buse\s+work\.(\w+)\.", re.I)
_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)


@dataclass
class HdlFile:
    path: Path
    role: str  # rtl | tb | header
    defines: list = field(default_factory=list)  # [(kind, name)]
    uses: set = field(default_factory=set)  # names of other units this file references
    includes: list = field(default_factory=list)


def strip_comments(text: str, vhdl: bool = False) -> str:
    if vhdl:
        return re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def role_of(path: Path, root: Path) -> str:
    if path.suffix.lower() in HEADER_SUFFIXES:
        return "header"
    parts = {p.lower() for p in path.relative_to(root).parts[:-1]} if root in path.parents else set()
    stem = path.stem.lower()
    if parts & TB_DIRS or stem.startswith("tb_") or stem.endswith(("_tb", "_test")) or stem == "tb":
        return "tb"
    return "rtl"


def parse_filelist(workspace: Workspace, path: str | Path, _seen: Optional[set] = None) -> tuple[list, list, list]:
    """(files, include dirs, defines) from a Verilator/VCS-style `.f` file. Paths are relative to
    the filelist's own directory; $VAR / ${VAR} are expanded; `-f` nests. Every path must stay inside
    the workspace. Options other than +incdir+, +define+, -f, -v and -y are ignored."""
    flist = workspace.resolve(path)
    if not flist.is_file():
        raise WorkspaceError(f"Filelist not found: {path}")
    seen = _seen if _seen is not None else set()
    if flist in seen:
        return [], [], []
    seen.add(flist)
    files, incdirs, defines = [], [], []
    tokens = []
    for line in flist.read_text(errors="replace").splitlines():
        line = re.sub(r"(//|#).*$", "", line).strip()
        tokens.extend(os.path.expandvars(line).split())
    base = flist.parent
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("+incdir+"):
            incdirs += [workspace.resolve(base / d) for d in tok[len("+incdir+"):].split("+") if d]
        elif tok.startswith("+define+"):
            defines += [d for d in tok[len("+define+"):].split("+") if d]
        elif tok in ("-f", "-F") and i + 1 < len(tokens):
            nested = parse_filelist(workspace, base / tokens[i + 1], seen)
            files += nested[0]
            incdirs += nested[1]
            defines += nested[2]
            i += 1
        elif tok in ("-v",) and i + 1 < len(tokens):
            files.append(workspace.resolve(base / tokens[i + 1]))
            i += 1
        elif tok == "-y" and i + 1 < len(tokens):
            libdir = workspace.resolve(base / tokens[i + 1])
            files += sorted(p for p in libdir.iterdir() if p.suffix in (".v", ".sv")) if libdir.is_dir() else []
            i += 1
        elif not tok.startswith(("-", "+")):
            files.append(workspace.resolve(base / tok))
        i += 1
    return files, incdirs, defines


class Project:
    def __init__(self, workspace: Workspace, filelist: Optional[str] = None, max_files: int = 500):
        self.workspace = workspace
        self.root = workspace.root
        self.filelist = filelist
        self.incdirs: list = []
        self.defines: list = []
        if filelist:
            paths, self.incdirs, self.defines = parse_filelist(workspace, filelist)
        else:
            paths = self._scan(max_files)
        self.files: dict[Path, HdlFile] = {}
        self.definitions: dict[str, list] = {}  # name -> [(kind, Path)]
        for path in paths:
            self._index(path)
        for f in self.files.values():
            self._find_uses(f)

    def _scan(self, max_files: int) -> list:
        out_dir = self.workspace.output_dir.split("/")[0]
        found = []
        for visited, (dirpath, dirnames, filenames) in enumerate(os.walk(self.root)):
            if visited >= 5000:  # don't crawl a home directory by accident
                break
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d != out_dir and not d.startswith("."))
            for name in sorted(filenames):
                if name.lower().endswith(HDL_SUFFIXES):
                    found.append(Path(dirpath) / name)
                    if len(found) >= max_files:
                        return found
        return found

    def _text(self, path: Path) -> Optional[str]:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
            return strip_comments(path.read_text(errors="replace"), path.suffix.lower() in (".vhd", ".vhdl"))
        except OSError:
            return None

    def _find_uses(self, f: HdlFile) -> None:
        """Units defined elsewhere in the project that `f` mentions: instances, package imports,
        interface port types. Any identifier matching a known definition counts."""
        text = self._text(f.path)
        if text is None:
            return
        names = set(self.definitions)
        if f.path.suffix.lower() in (".vhd", ".vhdl"):
            f.uses = {n for m in _VHDL_USE.finditer(text) for n in m.groups() if n} & names
        else:
            f.uses = set(re.findall(r"\b[A-Za-z_]\w*\b", text)) & names
        f.uses -= {name for _, name in f.defines}

    def _index(self, path: Path) -> None:
        text = self._text(path)
        if text is None:
            return
        vhdl = path.suffix.lower() in (".vhd", ".vhdl")
        f = HdlFile(path, role_of(path, self.root))
        pattern = _VHDL_DEF if vhdl else _SV_DEF
        for kind, name in pattern.findall(text):
            kind = kind.lower().replace("macromodule", "module").replace("entity", "module")
            f.defines.append((kind, name))
            self.definitions.setdefault(name, []).append((kind, path))
        f.includes = _INCLUDE.findall(text) if not vhdl else []
        self.files[path] = f

    def definer(self, name: str, near: Optional[Path] = None) -> Optional[Path]:
        """File defining `name`. With several candidates (alternative versions, eval mutants), prefer
        one in the same directory as `near`, then an rtl file, then the first in path order."""
        candidates = [p for _, p in self.definitions.get(name, [])]
        if not candidates:
            return None
        if near is not None:
            same_dir = [p for p in candidates if p.parent == near.parent and p != near]
            if same_dir:
                return same_dir[0]
        rtl = [p for p in candidates if self.files[p].role == "rtl"]
        return (rtl or candidates)[0]

    def dependencies(self, path: str | Path) -> list[Path]:
        """Files `path` needs, directly or indirectly, in compile order: packages first, then the
        rest with each file after its own dependencies. Excludes `path` itself."""
        start = self.workspace.resolve(path)
        if start not in self.files:
            self._index(start)
            if start not in self.files:
                return []
            self._find_uses(self.files[start])
        own = {name for _, name in self.files[start].defines}
        order: list[Path] = []
        visiting: set = set()

        def visit(p: Path) -> None:
            if p in order or p in visiting:
                return
            visiting.add(p)
            deps = []
            for name in sorted(self.files[p].uses - own if p == start else self.files[p].uses):
                target = self.definer(name, near=start)
                if target and target != p and target != start:
                    deps.append(target)
            rank = {"package": 0, "interface": 1}
            deps.sort(key=lambda d: min(rank.get(k, 2) for k, _ in self.files[d].defines) if self.files[d].defines else 3)
            for d in deps:
                visit(d)
            visiting.discard(p)
            if p != start and self.files[p].role != "header":
                order.append(p)

        visit(start)
        # Packages must be compiled before anything imports them, wherever they sit in the tree.
        is_pkg = lambda f: any(k == "package" for k, _ in self.files[f].defines)
        return [f for f in order if is_pkg(f)] + [f for f in order if not is_pkg(f)]

    def tops(self) -> list[str]:
        """RTL modules that no other RTL file instantiates: likely design tops."""
        used = set()
        for f in self.files.values():
            if f.role == "rtl":
                used |= f.uses
        return sorted(name for name, defs in self.definitions.items()
                      if any(k == "module" and self.files[p].role == "rtl" for k, p in defs) and name not in used)

    def summary(self) -> str:
        rel = self.workspace.relative
        lines = [f"Project at {self.root}" + (f" (filelist {self.filelist})" if self.filelist else " (scanned)")]
        for role in ("rtl", "tb", "header"):
            files = [f for f in self.files.values() if f.role == role]
            if files:
                lines.append(f"{role} files ({len(files)}):")
                for f in files:
                    defs = ", ".join(f"{k} {n}" for k, n in f.defines) or "-"
                    uses = ", ".join(sorted(f.uses)) or "-"
                    lines.append(f"  {rel(f.path)}: defines {defs}; uses {uses}")
        dupes = {n: d for n, d in self.definitions.items() if len(d) > 1}
        for name, defs in dupes.items():
            lines.append(f"note: {name} is defined in {len(defs)} files: " + ", ".join(rel(p) for _, p in defs))
        lines.append("likely tops: " + (", ".join(self.tops()) or "none"))
        if self.incdirs or self.defines:
            lines.append(f"include dirs: {', '.join(rel(p) for p in self.incdirs) or '-'}; defines: {', '.join(self.defines) or '-'}")
        return "\n".join(lines) + "\n"
