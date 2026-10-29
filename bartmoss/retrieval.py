"""Lightweight retrieval over a project's HDL and documents (SPEC P4.6), standard library only.

Chunking follows structure, not token counts: HDL is split into modules, interfaces, packages and
classes (VHDL: entities, architectures, packages), and a unit longer than `chunk_lines` is split at
its top-level items (always blocks, assigns, functions, generate blocks, instances, processes).
Documents are split by heading (Markdown, reStructuredText, AsciiDoc, numbered plain text).

Ranking is BM25, which suits exact signal and register names. Identifiers also index their parts
(`tx_valid` -> tx, valid) and numeric literals are normalised (8'h5A, 0x5A and 90 match). With
`retrieval.embeddings` set to an OpenAI-compatible embeddings model (e.g. a local Ollama one), BM25
and cosine rankings are fused (reciprocal rank fusion); embeddings are cached in the output dir.

The structure scan is regex-based for the same reason as project.py: it has to work on files that
don't compile. Verilator's JSON AST only gives each block's first token, not where it ends.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bartmoss.files import Workspace
from bartmoss.project import MAX_FILE_BYTES, SKIP_DIRS

DOC_SUFFIXES = (".md", ".markdown", ".rst", ".adoc", ".txt")
VHDL_SUFFIXES = (".vhd", ".vhdl")
_DOC_NOISE = re.compile(r"^(licen[cs]e|copying|notice|requirements.*\.txt$|cmakelists\.txt$)", re.I)


class RetrievalError(Exception):
    pass


@dataclass
class Chunk:
    rel: str  # workspace-relative path
    kind: str  # hdl | doc
    title: str
    start: int  # 1-based, inclusive
    end: int
    text: str

    @property
    def source(self) -> str:
        return f"{self.rel}:{self.start}-{self.end}"


@dataclass
class Hit:
    chunk: Chunk
    score: float
    via: str = "bm25"  # bm25 | vector | both


# ---------------------------------------------------------------------------------------------
# Tokens

_STOP = set("""a an and are as at be by can do does for from has have how i if in into is it its of on or
that the their then there these this to was what when where which while who why will with without""".split())
_LITERAL = re.compile(r"\d*'[sS]?([hHdDbBoO])([0-9a-fA-F_]+)|0[xX]([0-9a-fA-F_]+)|\b(\d+)\b")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_BASES = {"h": 16, "d": 10, "b": 2, "o": 8}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _literal_value(m: re.Match) -> Optional[str]:
    try:
        if m.group(2):
            return str(int(m.group(2).replace("_", ""), _BASES[m.group(1).lower()]))
        if m.group(3):
            return str(int(m.group(3).replace("_", ""), 16))
        return str(int(m.group(4)))
    except ValueError:
        return None


def tokens(text: str) -> list[str]:
    """Lower-cased words and identifiers, plus the parts of snake_case and camelCase identifiers,
    plus numeric literals as `#<decimal>`."""
    out = []
    for m in _LITERAL.finditer(text):
        value = _literal_value(m)
        if value is not None:
            out.append("#" + value)
    for word in _WORD.findall(_LITERAL.sub(" ", text)):
        lower = word.lower()
        if lower not in _STOP and len(lower) > 1:
            out.append(_stem(lower))
        parts = [p for p in re.split(r"[_$]+|(?<=[a-z0-9])(?=[A-Z])", word) if p]
        if len(parts) > 1:
            out += [_stem(p.lower()) for p in parts if len(p) > 1 and p.lower() not in _STOP]
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.lengths = [len(d) for d in docs]
        self.avg = (sum(self.lengths) / len(docs)) if docs else 0.0
        self.postings: dict[str, list] = {}
        for i, doc in enumerate(docs):
            counts: dict[str, int] = {}
            for t in doc:
                counts[t] = counts.get(t, 0) + 1
            for t, tf in counts.items():
                self.postings.setdefault(t, []).append((i, tf))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = [0.0] * len(self.lengths)
        for t in set(query):
            for i, tf in self.postings.get(t, ()):
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg or 1))
                out[i] += self.idf[t] * tf * (self.k1 + 1) / (tf + norm)
        return out


# ---------------------------------------------------------------------------------------------
# HDL chunking

def _blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _mask_sv(text: str) -> str:
    """Comments and string contents replaced by spaces, keeping line numbers."""
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"', _blank, text, flags=re.S)


def _mask_vhdl(text: str) -> str:
    return re.sub(r'--[^\n]*|"[^"\n]*"', _blank, text)


_SV_UNIT = re.compile(r"^\s*(?:(?:virtual|static|automatic)\s+)*(module|macromodule|interface|package|program|class|checker)"
                      r"\b\s*(?:(?:automatic|static)\s+)?(\w+)?")
_SV_UNIT_END = re.compile(r"\b(endmodule|endinterface|endpackage|endprogram|endclass|endchecker)\b")
_SV_OPEN = re.compile(r"\b(begin|fork|case|casex|casez|randcase|function|task|generate|covergroup|property|sequence|"
                      r"clocking|specify)\b")
_SV_CLOSE = re.compile(r"\b(end|join|join_any|join_none|endcase|endfunction|endtask|endgenerate|endgroup|endproperty|"
                       r"endsequence|endclocking|endspecify)\b")
# Keyword uses that don't open a block: `wait fork`, `assert property (...)`, prototypes, DPI imports.
_SV_NOT_OPEN = re.compile(r"\b(?:wait|disable)\s+fork\b|\b(?:assert|assume|cover|restrict|expect)\s+(?:property|sequence)\b|"
                          r"^.*\b(?:extern|pure\s+virtual|import|export)\b.*\b(?:function|task)\b.*$|"
                          r"\bdefault\s+clocking\s+\w+\s*;")
_SV_ITEM = re.compile(r"^\s*(?:\w+\s*:\s*)?(?:(?:virtual|static|protected|local|automatic)\s+)*"
                      r"(always_ff|always_comb|always_latch|always|initial|final|assign|function|task|generate|"
                      r"covergroup|property|sequence|class|for|if)\b")
_SV_DECL = {"logic", "wire", "reg", "bit", "byte", "int", "integer", "shortint", "longint", "input", "output", "inout",
            "typedef", "localparam", "parameter", "genvar", "var", "tri", "wand", "wor", "real", "string", "import",
            "export", "return", "else", "begin", "end", "module", "struct", "enum", "union", "const", "static"}
_SV_INSTANCE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:#\s*\(|([A-Za-z_]\w*)\s*\()")


def _sv_item_label(lines: list[str], masked: list[str], i: int) -> str:
    head = masked[i].strip()
    m = _SV_ITEM.match(masked[i])
    if m:
        kind = m.group(1)
        if kind.startswith("always") or kind in ("initial", "final", "generate", "for", "if"):
            for j in range(i, min(i + 3, len(masked))):
                named = re.search(r"\bbegin\s*:\s*(\w+)", masked[j])
                if named:
                    return f"{kind} {named.group(1)}"
            return re.sub(r"\s+begin\b.*$", "", re.sub(r"\s+", " ", head))[:60]
        if kind == "assign":
            target = re.match(r"\s*assign\s+([\w.\[\]:{}, ]+?)\s*=", masked[i])
            return f"assign {target.group(1).strip()}" if target else "assign"
        name = re.findall(r"(\w+)\s*[(;#]", masked[i])
        return f"{kind} {name[0]}" if name else kind
    inst = _SV_INSTANCE.match(masked[i])
    if inst:
        # `mod #(...) u_name (` : the instance name follows the parameter list
        name = inst.group(2) or next(iter(re.findall(r"\)\s*([A-Za-z_]\w*)\s*\(", masked[i])), "")
        return f"{inst.group(1)} {name}".strip()
    return re.sub(r"\s+", " ", head)[:60]


def _sv_units(masked: list[str]) -> list[tuple[int, int, str, str]]:
    """(start, end, kind, name) of top-level units, 0-based inclusive line indexes."""
    units, depth, start, kind, name = [], 0, 0, "", ""
    for i, line in enumerate(masked):
        m = _SV_UNIT.match(line)
        opens = 1 if m and not (m.group(1) == "interface" and re.match(r"\s*virtual\b", line)) else 0
        closes = len(_SV_UNIT_END.findall(line))
        if depth == 0 and opens:
            start, kind, name = i, m.group(1).replace("macromodule", "module"), m.group(2) or "?"
        depth = max(depth + opens - closes, 0)
        if depth == 0 and (opens or closes) and kind:
            units.append((start, i, kind, name))
            kind = ""
    if kind:  # unterminated unit: run to the end of the file
        units.append((start, len(masked) - 1, kind, name))
    return units


def _sv_items(masked: list[str], lines: list[str], start: int, end: int) -> list[int]:
    """Line indexes inside a unit where a top-level item starts, moved up over the comment lines
    directly above it (they usually describe the item)."""
    items, depth = [], 0
    for i in range(start + 1, end):
        line = masked[i]
        if depth == 0 and line.strip():
            m = _SV_ITEM.match(line)
            inst = _SV_INSTANCE.match(line)
            if m or (inst and inst.group(1) not in _SV_DECL and (inst.group(2) or "#" in line)
                     and inst.group(2) not in _SV_DECL):
                b = i
                while b - 1 > start and not masked[b - 1].strip() and lines[b - 1].strip():
                    b -= 1
                items.append(b)
        counted = _SV_NOT_OPEN.sub(" ", line)
        depth = max(depth + len(_SV_OPEN.findall(counted)) - len(_SV_CLOSE.findall(line)), 0)
    return items


_VHDL_UNIT = re.compile(r"^\s*(entity|architecture|package\s+body|package|configuration)\s+(\w+)(?:\s+of\s+(\w+))?\s+is\b",
                        re.I)
_VHDL_ITEM = re.compile(r"^\s*(?:(\w+)\s*:\s*)?(process|block|(?:for|if)\b.*\bgenerate|(?:pure\s+|impure\s+)?function|"
                        r"procedure|(?:entity\s+)?[\w.]+\s+(?:port|generic)\s+map)\b", re.I)


def _vhdl_units(masked: list[str]) -> list[tuple[int, int, str, str]]:
    starts = []
    for i, line in enumerate(masked):
        m = _VHDL_UNIT.match(line)
        if m:
            kind = re.sub(r"\s+", " ", m.group(1).lower())
            name = f"{m.group(2)} of {m.group(3)}" if m.group(3) else m.group(2)
            starts.append((i, kind, name))
    units = []
    for n, (i, kind, name) in enumerate(starts):
        begin = 0 if n == 0 else i  # library/use clauses before the first unit belong to it
        end = starts[n + 1][0] - 1 if n + 1 < len(starts) else len(masked) - 1
        units.append((begin, end, kind, name))
    return units


def _vhdl_items(masked: list[str], lines: list[str], start: int, end: int) -> list[int]:
    items = []
    for i in range(start + 1, end + 1):
        if _VHDL_ITEM.match(masked[i]) and not re.match(r"\s*end\b", masked[i], re.I):
            b = i
            while b - 1 > start and not masked[b - 1].strip() and lines[b - 1].strip():
                b -= 1
            items.append(b)
    return items


def _vhdl_item_label(lines: list[str], masked: list[str], i: int) -> str:
    m = _VHDL_ITEM.match(masked[i])
    if not m:
        return re.sub(r"\s+", " ", masked[i].strip())[:60]
    word = m.group(2).split()[0].lower()
    return f"{m.group(1)}: {word}" if m.group(1) else re.sub(r"\s+", " ", masked[i].strip())[:60]


def _pack(segments: list[tuple[int, int]], max_lines: int) -> list[tuple[int, int, list[int]]]:
    """Merge consecutive (start, end) segments into groups of at most max_lines; an oversized
    segment is cut into windows. Returns (start, end, indexes of the segments in the group)."""
    groups: list[tuple[int, int, list[int]]] = []
    for n, (s, e) in enumerate(segments):
        if groups and e - groups[-1][0] + 1 <= max_lines:
            groups[-1] = (groups[-1][0], e, groups[-1][2] + [n])
            continue
        while e - s + 1 > max_lines:
            groups.append((s, s + max_lines - 1, [n]))
            s += max_lines
        groups.append((s, e, [n]))
    return groups


def _line_windows(rel: str, kind: str, lines: list[str], size: int) -> list[Chunk]:
    out = []
    for s in range(0, len(lines), size):
        e = min(s + size, len(lines)) - 1
        text = "\n".join(lines[s:e + 1]) + "\n"
        if text.strip():
            out.append(Chunk(rel, kind, f"{rel} lines {s + 1}-{e + 1}", s + 1, e + 1, text))
    return out


def chunk_hdl(rel: str, text: str, max_lines: int = 60, mode: str = "structure") -> list[Chunk]:
    lines = text.splitlines()
    if mode == "lines":
        return _line_windows(rel, "hdl", lines, max_lines)
    vhdl = rel.lower().endswith(VHDL_SUFFIXES)
    masked = (_mask_vhdl(text) if vhdl else _mask_sv(text)).splitlines()
    masked += [""] * (len(lines) - len(masked))
    units = _vhdl_units(masked) if vhdl else _sv_units(masked)
    items_of, label_of = (_vhdl_items, _vhdl_item_label) if vhdl else (_sv_items, _sv_item_label)
    chunks: list[Chunk] = []

    def add(s: int, e: int, title: str) -> None:
        body = "\n".join(lines[s:e + 1]) + "\n"
        if body.strip():
            chunks.append(Chunk(rel, "hdl", title, s + 1, e + 1, body))

    covered = 0
    for start, end, kind, name in units:
        if start > covered and any(masked[i].strip() for i in range(covered, start)):
            add(covered, start - 1, f"{rel} (outside any unit)")
        covered = end + 1
        unit = f"{kind} {name}"
        if end - start + 1 <= max_lines:
            add(start, end, unit)
            continue
        starts = items_of(masked, lines, start, end)
        bounds = [start] + starts
        segments = [(b, (bounds[n + 1] - 1) if n + 1 < len(bounds) else end) for n, b in enumerate(bounds)]
        labels = ["ports and declarations"] + [label_of(lines, masked, next(
            (j for j in range(b, segments[n + 1][1] + 1) if masked[j].strip()), b)) for n, b in enumerate(starts)]
        groups = _pack(segments, max_lines)
        for g, (s, e, members) in enumerate(groups, 1):
            names = list(dict.fromkeys(labels[m] for m in members))
            add(s, e, f"{unit} (part {g}/{len(groups)}): " + ", ".join(names)[:160])
    if covered < len(lines) and any(m.strip() for m in masked[covered:]):
        add(covered, len(lines) - 1, f"{rel} (outside any unit)" if units else rel)
    return chunks


# ---------------------------------------------------------------------------------------------
# Document chunking

def _headings(rel: str, lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, title) for each heading, by file type."""
    suffix = Path(rel).suffix.lower()
    found = []
    if suffix == ".rst":
        order: list[str] = []
        for i in range(len(lines) - 1):
            title, under = lines[i].strip(), lines[i + 1].strip()
            if title and len(under) >= max(3, len(title)) and re.fullmatch(r"([=\-~^\"'`#*+.:_])\1*", under) \
                    and not re.fullmatch(r"([=\-~^\"'`#*+.:_])\1*", title):
                if under[0] not in order:
                    order.append(under[0])
                found.append((i, order.index(under[0]) + 1, title))
        return found
    if suffix == ".adoc":
        return [(i, len(l) - len(l.lstrip("=")), l.lstrip("= ").strip()) for i, l in enumerate(lines)
                if re.match(r"^=+\s+\S", l)]
    markdown = suffix in (".md", ".markdown") or any(re.match(r"^#{1,6}\s+\S", l) for l in lines)
    if markdown:
        fence = False
        for i, line in enumerate(lines):
            if re.match(r"^\s*(```|~~~)", line):
                fence = not fence
                continue
            if fence:
                continue
            m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if m:
                found.append((i, len(m.group(1)), m.group(2)))
            elif i + 1 < len(lines) and line.strip() and not re.match(r"^\s*([|>*+-]|\d+\.)", line) \
                    and re.fullmatch(r"(=+|-{3,})\s*", lines[i + 1]) and (i == 0 or not lines[i - 1].strip()):
                found.append((i, 1 if lines[i + 1].startswith("=") else 2, line.strip()))
        return found
    for i, line in enumerate(lines):  # plain text: numbered headings such as "3.2 Control register"
        m = re.match(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^\n]{0,80})$", line.rstrip())
        if m and not line.rstrip().endswith((".", ",", ";")):
            found.append((i, m.group(1).count(".") + 1, f"{m.group(1)} {m.group(2).strip()}"))
    return found


def chunk_doc(rel: str, text: str, max_lines: int = 80, max_chars: int = 4000, mode: str = "structure") -> list[Chunk]:
    lines = text.splitlines()
    if mode == "lines":
        return _line_windows(rel, "doc", lines, max_lines)
    heads = _headings(rel, lines)
    sections = []  # (start, end, title)
    if not heads or heads[0][0] > 0:
        sections.append((0, (heads[0][0] - 1) if heads else len(lines) - 1, rel))
    stack: list[tuple[int, str]] = []
    for n, (i, level, title) in enumerate(heads):
        stack = [(lv, t) for lv, t in stack if lv < level] + [(level, title)]
        end = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines) - 1
        sections.append((i, end, f"{Path(rel).name} § " + " > ".join(t for _, t in stack)))
    chunks = []
    for s, e, title in sections:
        body = lines[s:e + 1]
        if not any(l.strip() for l in body[1:] if l.strip() and not re.fullmatch(r"[=\-~^\"'`#*+.:_]+", l.strip())) \
                and title != rel:
            continue  # a heading with nothing under it before the next heading
        pieces, ps, size, last_blank = [], s, 0, None
        for i in range(s, e + 1):
            n = len(lines[i]) + 1
            if i > ps and (i - ps + 1 > max_lines or size + n > max_chars):
                # Cut at the last blank line in the piece when there is one, so paragraphs stay whole.
                cut = last_blank if last_blank is not None and last_blank > ps else i
                pieces.append((ps, cut - 1))
                ps, last_blank = cut, None
                size = sum(len(lines[j]) + 1 for j in range(ps, i))
            if not lines[i].strip():
                last_blank = i
            size += n
        pieces.append((ps, e))
        for p, (ps, pe) in enumerate(pieces, 1):
            piece = "\n".join(lines[ps:pe + 1]) + "\n"
            if re.sub(r"<!--.*?-->", "", piece, flags=re.S).strip():  # skip pieces that are only comments
                chunks.append(Chunk(rel, "doc", title + (f" (part {p})" if len(pieces) > 1 else ""), ps + 1, pe + 1, piece))
    return chunks


# ---------------------------------------------------------------------------------------------
# Embeddings (optional)

def embedder(spec: str, config: dict) -> Callable[[list[str]], list[list[float]]]:
    """An OpenAI-compatible /embeddings client for `provider:model` (Ollama, OpenAI, OpenRouter...),
    using urllib so no SDK is needed. Project text is sent to that endpoint, so only configure a
    remote one if that's acceptable."""
    from bartmoss.config import parse_model_spec

    provider, model = parse_model_spec(spec)
    pcfg = config.get("providers", {}).get(provider)
    if not pcfg or pcfg.get("type", "openai_compat") != "openai_compat" or not pcfg.get("base_url"):
        raise RetrievalError(f"{provider} has no OpenAI-compatible embeddings endpoint configured")
    headers = {"Content-Type": "application/json"}
    if pcfg.get("api_key_env"):
        key = os.environ.get(pcfg["api_key_env"])
        if not key:
            raise RetrievalError(f"Set {pcfg['api_key_env']} to use {spec} embeddings")
        headers["Authorization"] = f"Bearer {key}"
    url = pcfg["base_url"].rstrip("/") + "/embeddings"

    def embed(texts: list[str]) -> list[list[float]]:
        request = urllib.request.Request(url, json.dumps({"model": model, "input": texts}).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.load(response)["data"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise RetrievalError(f"embeddings request to {url} failed: {e}")
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]

    return embed


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------------------------
# Index

@dataclass
class Index:
    chunks: list
    files: int = 0
    vectors: Optional[list] = None
    embed: Optional[Callable] = None
    embeddings: Optional[str] = None  # the embeddings model spec, when hybrid
    bm25: BM25 = field(init=False)

    def __post_init__(self):
        # The title (unit or heading path) is counted twice so a name match outranks a passing mention.
        self.bm25 = BM25([tokens(c.title) * 2 + tokens(c.text) for c in self.chunks])

    def attach_embeddings(self, embed: Callable, spec: str, cache: dict, batch: int = 32) -> int:
        """Embed every chunk (cached by text hash and model) and turn on hybrid ranking. Returns the
        number of chunks that weren't in the cache."""
        keys = [hashlib.sha1(f"{c.title}\n{c.text}".encode()).hexdigest() for c in self.chunks]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        for n in range(0, len(missing), batch):
            idx = missing[n:n + batch]
            vecs = embed([f"{self.chunks[i].title}\n{self.chunks[i].text}"[:6000] for i in idx])
            if len(vecs) != len(idx):
                raise RetrievalError("the embeddings endpoint returned the wrong number of vectors")
            for i, v in zip(idx, vecs):
                cache[keys[i]] = v
        self.vectors = [cache[k] for k in keys]
        self.embed, self.embeddings = embed, spec
        for k in set(cache) - set(keys):  # forget chunks that no longer exist
            del cache[k]
        return len(missing)

    def search(self, query: str, k: int = 8, kind: Optional[str] = None,
               allow: Optional[Callable[[str], bool]] = None) -> list[Hit]:
        keep = [i for i, c in enumerate(self.chunks)
                if (kind is None or c.kind == kind) and (allow is None or allow(c.rel))]
        if not keep or not query.strip():
            return []
        bm = self.bm25.scores(tokens(query))
        by_bm = sorted((i for i in keep if bm[i] > 0), key=lambda i: -bm[i])
        if self.vectors is None or self.embed is None:
            return [Hit(self.chunks[i], round(bm[i], 3)) for i in by_bm[:k]]
        qv = self.embed([query])[0]
        cos = {i: _cosine(qv, self.vectors[i]) for i in keep}
        by_vec = sorted(keep, key=lambda i: -cos[i])[:50]
        fused: dict[int, float] = {}
        for ranking in (by_bm[:50], by_vec):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1 / (60 + rank + 1)
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        in_bm, in_vec = set(by_bm[:50]), set(by_vec)
        return [Hit(self.chunks[i], round(fused[i], 4), "both" if i in in_bm and i in in_vec else
                    "bm25" if i in in_bm else "vector") for i in top]

    def stats(self) -> dict:
        out = {"files": self.files, "chunks": len(self.chunks)}
        for c in self.chunks:
            out[c.kind] = out.get(c.kind, 0) + 1
        return out


def doc_paths(workspace: Workspace, cfg: dict) -> list[Path]:
    """Documents to index: `retrieval.docs` (files or directories) if set, else a scan of the
    workspace that skips the output dir, build and VCS dirs, licences and requirement files."""
    roots = [workspace.resolve(p) for p in cfg.get("docs") or []] or [workspace.root]
    out_dir = workspace.output_dir.split("/")[0]
    limit, max_bytes = cfg.get("max_docs", 200), cfg.get("max_doc_bytes", 2_000_000)
    found: list[Path] = []
    visited = 0
    for root in roots:
        candidates = [root] if root.is_file() else []
        if root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root):
                visited += 1
                if visited > 5000:
                    break
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d != out_dir and not d.startswith("."))
                candidates += [Path(dirpath) / f for f in sorted(filenames)]
        for path in candidates:
            if path.suffix.lower() in DOC_SUFFIXES and not _DOC_NOISE.match(path.name) and path not in found:
                try:
                    if path.stat().st_size <= max_bytes and workspace.resolve(path):
                        found.append(path)
                except OSError:
                    continue
            if len(found) >= limit:
                return found
    return found


def embeddings_cache_path(workspace: Workspace, spec: str) -> Path:
    return workspace.output_path(f"index/embeddings_{re.sub(r'[^A-Za-z0-9.-]+', '-', spec)}.json")


def build_index(workspace: Workspace, project, config: dict, warn: Callable[[str], None] = lambda m: None) -> Index:
    cfg = config.get("retrieval", {})
    mode = cfg.get("chunking", "structure")
    chunks: list[Chunk] = []
    files = 0
    for path in sorted(project.files):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(errors="replace")
        except OSError:
            continue
        files += 1
        chunks += chunk_hdl(workspace.relative(path), text, cfg.get("chunk_lines", 60), mode)
    for path in doc_paths(workspace, cfg):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        files += 1
        chunks += chunk_doc(workspace.relative(path), text, cfg.get("doc_chunk_lines", 80),
                            cfg.get("doc_chunk_chars", 4000), mode)
    index = Index(chunks, files)
    spec = cfg.get("embeddings")
    if spec and chunks:
        cache_path = embeddings_cache_path(workspace, spec)
        try:
            cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
        except ValueError:
            cache = {}
        try:
            new = index.attach_embeddings(embedder(spec, config), spec, cache)
        except RetrievalError as e:
            warn(f"{e}; using keyword search only")
            return index
        if new:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache))
    return index


def excerpt_blocks(hits: list[Hit], max_chars: int) -> tuple[str, list[Hit]]:
    """`<excerpt>` blocks for a prompt, best first, stopping before `max_chars`. Returns the text
    and the hits that fit."""
    out, used, total = [], [], 0
    for hit in hits:
        c = hit.chunk
        block = f'<excerpt source="{c.source}" title="{c.title}">\n{c.text}</excerpt>\n'
        if total + len(block) > max_chars and used:
            break
        out.append(block)
        used.append(hit)
        total += len(block)
    return "".join(out), used


def format_hits(hits: list[Hit], show_text: bool = False, max_lines: int = 12) -> str:
    lines = []
    for n, hit in enumerate(hits, 1):
        c = hit.chunk
        lines.append(f"{n:2}. {c.source}  {c.title}  ({hit.via} {hit.score:g})")
        if show_text:
            body = c.text.rstrip("\n").splitlines()
            lines += [f"      {c.start + j:5} | {l}" for j, l in enumerate(body[:max_lines])]
            if len(body) > max_lines:
                lines.append(f"      ... {len(body) - max_lines} more lines")
    return "\n".join(lines)
