"""BibTeX parsing and text normalisation, enough for Better BibTeX exports.

No external parser: brace-depth tracking handles nested braces, quoted values and bare
values (numbers, macros). LaTeX accents are resolved to Unicode before any comparison.
"""

from __future__ import annotations

import difflib
import html
import re
import unicodedata
from pathlib import Path

ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,")
FIELD_RE = re.compile(r"([A-Za-z_\-]+)\s*=\s*")


def parse_bib(text: str) -> dict[str, dict[str, str]]:
    """Map citation key -> {field: raw value}; the entry type is under ``ENTRYTYPE``."""
    entries: dict[str, dict[str, str]] = {}
    i, n = 0, len(text)
    while True:
        m = ENTRY_RE.search(text, i)
        if not m:
            break
        etype, key = m.group(1).lower(), m.group(2)
        i = m.end()
        if etype in ("comment", "preamble", "string"):
            continue
        fields: dict[str, str] = {"ENTRYTYPE": etype}
        while i < n:
            while i < n and text[i] in " \t\r\n,":
                i += 1
            if i >= n or text[i] == "}":
                i += 1
                break
            fm = FIELD_RE.match(text, i)
            if not fm:
                nl = text.find("\n", i)
                i = n if nl < 0 else nl + 1
                continue
            name = fm.group(1).lower()
            i = fm.end()
            if i < n and text[i] == "{":
                depth, j = 0, i
                while j < n:
                    if text[j] == "{" and text[j - 1] != "\\":
                        depth += 1
                    elif text[j] == "}" and text[j - 1] != "\\":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                val, i = text[i + 1 : j], j + 1
            elif i < n and text[i] == '"':
                j = text.index('"', i + 1)
                val, i = text[i + 1 : j], j + 1
            else:
                vm = re.compile(r"[^,}\s]+").match(text, i)
                val = vm.group(0) if vm else ""
                i = vm.end() if vm else i + 1
            fields[name] = " ".join(val.split())
        entries[key] = fields
    return entries


def load_bib(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return parse_bib(path.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- normalisation

ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
           "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
           "c": "\u0327", "k": "\u0328", "r": "\u030a"}
SPECIAL = {"ss": "\u00df", "o": "\u00f8", "O": "\u00d8", "ae": "\u00e6", "AE": "\u00c6",
           "oe": "\u0153", "OE": "\u0152", "aa": "\u00e5", "AA": "\u00c5", "l": "\u0142",
           "L": "\u0141", "i": "i", "j": "j"}
TRANSLIT = {"\u00df": "ss", "\u00e6": "ae", "\u00f8": "o", "\u0153": "oe", "\u0111": "d",
            "\u0142": "l", "\u00fe": "th", "\u00f0": "d", "\u0131": "i"}


def latex_to_text(s: str) -> str:
    """Resolve LaTeX accents, escapes and braces to plain Unicode."""
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("\\$", "$")
    s = s.replace("---", "\u2014").replace("--", "\u2013")

    def acc(m: re.Match[str]) -> str:
        return m.group(2) + ACCENTS[m.group(1)]

    s = re.sub(r"\\([\'`^\"~=.])\s*\{?\\?([A-Za-z])\}?", acc, s)
    s = re.sub(r"\\([uvHckr])\s*\{\\?([A-Za-z])\}", acc, s)
    s = re.sub(r"\\([uvHckr])\s+([A-Za-z])", acc, s)
    s = re.sub(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])\s*",
               lambda m: SPECIAL[m.group(1)], s)
    s = s.replace("~", " ")
    s = re.sub(r"\\(?:emph|textit|textbf|textsc|mathrm|text|mkbibquote|enquote)\s*", "", s)
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)
    s = s.replace("{", "").replace("}", "").replace("$", "")
    return unicodedata.normalize("NFC", " ".join(s.split()))


def web_to_text(s: str | None) -> str:
    """Strip JATS/HTML tags and entities from a registry string."""
    s = re.sub(r"<[^>]+>", "", s or "")
    return unicodedata.normalize("NFC", " ".join(html.unescape(html.unescape(s)).split()))


def fold(s: str) -> str:
    """Case-fold, strip diacritics, punctuation to space, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    for a, b in TRANSLIT.items():
        s = s.replace(a, b)
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(s.split())


def ratio(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def word_diff(a: str, b: str) -> str:
    wa, wb = a.split(), b.split()
    parts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        left, right = " ".join(wa[i1:i2]), " ".join(wb[j1:j2])
        if op == "replace":
            parts.append(f"[bib: {left} | pub: {right}]")
        elif op == "delete":
            parts.append(f"[bib only: {left}]")
        elif op == "insert":
            parts.append(f"[pub only: {right}]")
    return " ".join(parts)


def _closing(s: str) -> int:
    depth = 0
    for idx, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def split_names(raw: str) -> list[str]:
    """Split a BibTeX name list at top-level `` and ``."""
    s = " ".join(raw.split())
    if not s:
        return []
    names, depth, start = [], 0, 0
    for idx, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0 and s.startswith(" and ", idx):
            names.append(s[start:idx].strip())
            start = idx + 5
    names.append(s[start:].strip())
    return names


def family_names(raw: str) -> tuple[list[str], bool]:
    """Family names in order; the flag is True when the list ends in ``and others``."""
    names = split_names(raw)
    truncated = bool(names) and names[-1].lower() == "others"
    if truncated:
        names = names[:-1]
    fams = []
    for nm in names:
        if nm.startswith("{") and nm.endswith("}") and _closing(nm) == len(nm) - 1:
            fams.append(latex_to_text(nm[1:-1]))  # corporate author
            continue
        depth, cut = 0, None
        for idx, ch in enumerate(nm):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                cut = idx
                break
        fam = nm[:cut] if cut is not None else (nm.split() or [nm])[-1]
        fams.append(latex_to_text(fam).strip())
    return fams, truncated


def bib_files(value: str) -> list[str]:
    """Paths in a ``file`` field: Better BibTeX writes ``path`` or ``a.pdf;b.pdf``; JabRef and
    older Zotero exports write ``description:path:mimetype``."""
    out = []
    for part in re.split(r"(?<!\\);", value or ""):
        part = part.strip().replace("\\:", "\x00")
        if not part:
            continue
        bits = part.split(":")
        path = bits[1] if len(bits) == 3 and bits[2].startswith("application/") else part
        path = path.replace("\x00", ":")
        if len(bits) == 3 and bits[2].startswith("application/") and re.match(r"^[A-Za-z]$", path):
            path = ":".join(bits[1:2]) + ":" + bits[2]  # pragma: no cover - Windows drive edge
        out.append(path.replace("\\\\", "\\"))
    return out
