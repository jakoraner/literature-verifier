"""Locate a verbatim excerpt in a PDF, by printed page label.

Text extraction uses ``pypdf``.

A citation records two page numbers: ``page``, as printed on the page (what a reader looks
up), and ``pdf_page``, the 1-based index in the file (what this module opens). When the PDF
defines page labels, the printed page alone is enough to find the index. The excerpt may
elide text with "..." or "…"; the parts must appear in order on the page. Matching ignores case,
diacritics, punctuation, all whitespace (so drop caps such as "S ciPy" match) and
end-of-line hyphenation.

A page with no extractable text (an image-only scan) returns ``status="no_text"``: the
excerpt must then be transcribed from the page image and recorded with ``--scan``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .bib import fold

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings are noise here


@dataclass
class Located:
    status: str  # "found", "not_found", "no_text", "no_page"
    page: str = ""
    pages_found: tuple[str, ...] = ()  # 1-based PDF page indices holding the excerpt
    detail: str = ""
    pdf_page: int | None = None


def _norm(text: str) -> str:
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # de-hyphenate line breaks
    return fold(text)


def _parts(excerpt: str) -> list[str]:
    return [p for p in (_norm(x) for x in re.split(r"\.\.\.|…|\[\.\.\.\]", excerpt)) if p]


def _contains(page_text: str, parts: list[str]) -> bool:
    """Parts in order, ignoring whitespace (drop caps, broken word spacing in old PDFs)."""
    page_text = page_text.replace(" ", "")
    parts = [p.replace(" ", "") for p in parts]
    pos = 0
    for p in parts:
        i = page_text.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def page_texts(pdf: Path) -> list[tuple[str, str]]:
    """[(printed label, text)] for every page."""
    from pypdf import PdfReader

    r = PdfReader(str(pdf))
    try:
        labels = list(r.page_labels)
    except Exception:  # malformed label tree
        labels = []
    if len(labels) != len(r.pages):
        labels = [str(i + 1) for i in range(len(r.pages))]
    out = []
    for lab, page in zip(labels, r.pages, strict=True):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        out.append((lab, txt))
    return out


def locate(pdf: Path, excerpt: str, page: str = "", pdf_page: int | None = None) -> Located:
    """Find ``excerpt`` in ``pdf``. The target page is ``pdf_page`` (1-based index) when given,
    else the page whose printed label is ``page``. Without either, every page is searched."""
    parts = _parts(excerpt)
    if not parts:
        return Located("not_found", page, detail="empty excerpt")
    pages = page_texts(pdf)
    found = tuple(str(i + 1) for i, (_, txt) in enumerate(pages) if _contains(_norm(txt), parts))
    if pdf_page is None and not page:
        return Located("found" if found else "not_found", "", found)
    if pdf_page is None:
        idx = [i for i, (lab, _) in enumerate(pages) if lab == page]
        if not idx:
            return Located("no_page", page, found,
                           f"no page labelled {page!r}; pass the PDF page index (--pdf-page)")
        target = idx[0] + 1
    else:
        target = pdf_page
    if not 1 <= target <= len(pages):
        return Located("no_page", page, found, f"PDF has {len(pages)} pages, not {target}")
    if not pages[target - 1][1].strip():
        return Located("no_text", page, found, f"PDF page {target} has no extractable text",
                       pdf_page=target)
    if str(target) in found:
        return Located("found", page, found, pdf_page=target)
    hint = f"; found on PDF page(s) {', '.join(found)}" if found else ""
    return Located("not_found", page, found, f"excerpt not on PDF page {target}{hint}",
                   pdf_page=target)
