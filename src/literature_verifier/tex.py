"""Find every citation occurrence in LaTeX sources.

One occurrence is one key in one citation command. Handled forms::

    \\cite{a}  \\citep*{a,b}  \\parencite[12]{a}  \\textcite[see][ch.~2]{a}
    \\autocite  \\footcite  \\citeauthor  \\Cite  \\supercite ...  (any \\...cite...)
    \\cites[3]{a}[5]{b}  \\parencites(pre)(post)[3]{a}{b}   (multicite commands)

``\\nocite`` is ignored: it adds a bibliography entry without attributing a claim. Comments
are stripped, and commands may span lines. With one optional argument it is the postnote
(page), with two the first is the prenote, following biblatex and natbib.

The sentence around each citation is extracted heuristically (paragraph breaks, sectioning
and environment boundaries, and sentence-final punctuation followed by a capital letter or a
command, skipping common abbreviations such as "et al." and "e.g."). The sentence is what a
verdict is recorded against, and its hash anchors the ledger row.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CMD_RE = re.compile(r"\\([A-Za-z]*cite[A-Za-z]*)\*?")
OPT_RE = re.compile(r"\s*\[([^\[\]]*)\]")
PAREN_RE = re.compile(r"\s*\(([^()]*)\)")
KEYS_RE = re.compile(r"\s*\{([^{}]*)\}")
ABBREV = {"al", "e.g", "i.e", "cf", "fig", "figs", "eq", "eqs", "vs", "etc", "p", "pp", "sec",
          "ch", "chap", "no", "vol", "dr", "approx", "ca", "ed", "eds", "resp", "viz", "tab"}
BOUNDARY_RE = re.compile(
    r"\n[ \t]*\n|\\(?:begin|end|item|part|chapter|section|subsection|subsubsection|paragraph"
    r"|caption|label)\b"
)
SENT_END_RE = re.compile(r"([.?!])(['\"\)\]]*)\s+(?=[A-Z0-9\\])")
MAX_SENTENCE = 600


@dataclass
class Citation:
    key: str
    file: str
    line: int
    command: str
    prenote: str
    postnote: str
    sentence: str
    anchor: str = ""


def strip_comments(text: str) -> str:
    """Remove ``%`` comments, keeping line count so offsets map to the same line numbers."""
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in text.split("\n"))


def normalise(s: str) -> str:
    return " ".join(s.split())


def _parse_args(text: str, pos: int, multi: bool) -> tuple[list[tuple[str, str, str]], int]:
    """Parse optional and key arguments after a cite command; returns (pre, post, keys) groups."""
    groups = []
    if multi:  # global (pre)(post) notes of multicite commands
        for _ in range(2):
            m = PAREN_RE.match(text, pos)
            if not m:
                break
            pos = m.end()
    while True:
        opts = []
        while len(opts) < 2:
            m = OPT_RE.match(text, pos)
            if not m:
                break
            opts.append(m.group(1).strip())
            pos = m.end()
        k = KEYS_RE.match(text, pos)
        if not k:
            break
        pos = k.end()
        pre, post = ("", opts[0]) if len(opts) == 1 else (opts[0], opts[1]) if opts else ("", "")
        groups.append((pre, post, k.group(1)))
        if not multi:
            break
    return groups, pos


def _mask(text: str) -> str:
    """Blank out citation arguments so periods in "[p.~3]" are not sentence ends."""
    out = list(text)
    for m in CMD_RE.finditer(text):
        _, end = _parse_args(text, m.end(), m.group(1).lower().endswith("cites"))
        for i in range(m.start(), end):
            if out[i] not in "\n":
                out[i] = "x"
    return "".join(out)


def _is_sentence_end(masked: str, m: re.Match[str]) -> bool:
    if m.group(1) != ".":
        return True
    before = masked[: m.start()]
    tok = re.search(r"([A-Za-z.]+)$", before)
    word = tok.group(1).lower().rstrip(".") if tok else ""
    if word in ABBREV:
        return False
    return not (len(word) == 1 and word.isalpha())  # initials such as "J. Smith"


def sentence_bounds(masked: str, pos: int) -> tuple[int, int]:
    start, end = 0, len(masked)
    for b in BOUNDARY_RE.finditer(masked):
        if b.end() <= pos:
            start = b.end()
        elif b.start() > pos:
            end = b.start()
            break
    for m in SENT_END_RE.finditer(masked, start, end):
        if not _is_sentence_end(masked, m):
            continue
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            end = m.end(2) if m.group(2) else m.end(1)
            break
    # a closing brace of a sectioning argument or similar may precede the sentence
    return start, end


def _clean_sentence(s: str) -> str:
    s = normalise(s)
    s = re.sub(r"^[}\]\s]+", "", s)
    return s[:MAX_SENTENCE]


def scan_text(text: str, rel: str) -> list[Citation]:
    stripped = strip_comments(text)
    masked = _mask(stripped)
    out: list[Citation] = []
    for m in CMD_RE.finditer(stripped):
        cmd = m.group(1)
        if cmd.lower() == "nocite":
            continue
        groups, _ = _parse_args(stripped, m.end(), cmd.lower().endswith("cites"))
        if not groups:
            continue
        line = stripped.count("\n", 0, m.start()) + 1
        s0, s1 = sentence_bounds(masked, m.start())
        sentence = _clean_sentence(stripped[s0:s1])
        for pre, post, keys in groups:
            for key in (k.strip() for k in keys.split(",")):
                if key and key != "*":
                    out.append(Citation(key, rel, line, cmd, pre, post, sentence))
    _assign_anchors(out)
    return out


def anchor_base(key: str, sentence: str) -> str:
    return hashlib.sha1(f"{key}|{normalise(sentence)}".encode()).hexdigest()[:12]


def _assign_anchors(cites: list[Citation]) -> None:
    used: Counter[tuple[str, str]] = Counter()
    for c in cites:
        base = anchor_base(c.key, c.sentence)
        used[(c.file, base)] += 1
        n = used[(c.file, base)]
        c.anchor = base if n == 1 else f"{base}-{n}"


def scan_files(files: list[Path], root: Path) -> list[Citation]:
    out: list[Citation] = []
    for f in files:
        try:
            rel = f.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f.as_posix()
        out += scan_text(f.read_text(encoding="utf-8", errors="replace"), rel)
    return out
