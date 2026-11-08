"""The hard rules. ``litverify check`` fails on any violation and names the citation and rule.

R1  The work is real: every cited key is in the bib and resolves to a registry record
    (DOI in Crossref or DataCite). A work without a DOI passes only with a human decision
    naming the equivalent record that was checked (ISBN, publisher page).
R2  The metadata matches: every FIX or CHECK field of the audit is fixed in the reference
    manager or carries a human decision. Registry errors (ERROR) never pass.
R3  The PDF is in hand: the key resolves to a PDF file on disk.
R4  The passage is located: the citation has a verdict backed by a printed page, a PDF
    page and a verbatim excerpt, and the excerpt is found on that page of the PDF
    (re-checked on every run; image-only pages are recorded as transcribed with --scan).
    UNCHECKED never passes.
R5  The source supports exactly what the sentence says: the verdict is SUPPORTED, or a human
    has recorded a decision on it (PARTLY, WRONG_VERSION, NOT_FOUND, NO_ACCESS).
R6  A reworded sentence may only narrow the claim: any sentence that changed after it was
    checked must be verified again against its new wording; the old verdict does not carry.
R7  The ledger is complete: every citation in the sources has a live ledger row (run sync).
R8  The author has looked: a citation the author marked "disagree" in ``litverify review``
    always fails; with ``check --require-human`` every citation needs a current "agree"
    (a sign-off lapses when the sentence, verdict, page or excerpt changes).

No rule can be satisfied from memory: R3 and R4 need the file, and R4 re-reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import meta as meta_mod
from .ledger import NEEDS_PASSAGE, is_stale
from .pdfs import PdfResolver
from .pdftext import locate
from .review import human_status

RULES = {
    "R1": "work resolves to a registry record",
    "R2": "bib metadata matches the record",
    "R3": "PDF in hand",
    "R4": "passage located on its page",
    "R5": "source supports the sentence exactly (or human decision)",
    "R6": "reworded sentence re-verified",
    "R7": "ledger in sync with the sources",
    "R8": "author checked it with their own eyes",
}


@dataclass
class Violation:
    rule: str
    where: str  # "C0003 harris2020array main.tex:12" or "key harris2020array"
    detail: str

    def __str__(self) -> str:
        return f"{self.rule} {RULES[self.rule]}: {self.where}: {self.detail}"


def check_ledger(rows: list[dict[str, str]], resolver: PdfResolver,
                 relocate: bool = True, require_human: bool = False) -> list[Violation]:
    out: list[Violation] = []
    cache: dict[tuple[str, str, str, str], str] = {}
    for r in rows:
        if r["status"] != "live":
            continue
        where = f"{r['cid']} {r['key']} {r['file']}:{r['line']}"
        pdf = resolver.for_key(r["key"])
        if pdf is None:
            out.append(Violation("R3", where, "no PDF found for this key"))
        v = r["verdict"]
        if v == "UNCHECKED":
            out.append(Violation("R4", where, "UNCHECKED: no passage located yet"))
            continue
        if is_stale(r):
            out.append(Violation("R6", where, "sentence changed since it was checked; "
                                 "re-verify against the new wording"))
            continue
        if v in NEEDS_PASSAGE:
            if not (r["page"] and r["pdf_page"] and r["excerpt"]):
                out.append(Violation("R4", where, f"{v} without page, PDF page and excerpt"))
            elif r["located"] not in ("text", "scan"):
                out.append(Violation("R4", where, "excerpt was never located in the PDF"))
            elif r["located"] == "text" and relocate and pdf is not None:
                k = (str(pdf), r["excerpt"], r["page"], r["pdf_page"])
                if k not in cache:
                    cache[k] = _relocate(pdf, r)
                if cache[k]:
                    out.append(Violation("R4", where, cache[k]))
        if v != "SUPPORTED" and not r["decision"]:
            out.append(Violation("R5", where, f"{v}: needs a human decision"))
        h = human_status(r)
        if h == "disagree":
            note = f" ({r['human_note']})" if r["human_note"] else ""
            out.append(Violation("R8", where, f"the author disagreed{note}"))
        elif require_human and h != "agree":
            out.append(Violation("R8", where, "no current sign-off from the author" if not h
                                 else f"the author marked it {h}"))
    return out


def _relocate(pdf: Path, r: dict[str, str]) -> str:
    try:
        loc = locate(pdf, r["excerpt"], r["page"], int(r["pdf_page"]))
    except Exception as e:  # unreadable PDF
        return f"PDF could not be read: {e}"
    return "" if loc.status == "found" else f"excerpt no longer found: {loc.detail}"


def check_metadata(cited: set[str], rows: list[dict[str, str]]) -> list[Violation]:
    out: list[Violation] = []
    audited = {r["key"] for r in rows}
    for key in sorted(cited - audited):
        out.append(Violation("R1", f"key {key}", "not audited yet (run `litverify meta`)"))
    for r in meta_mod.open_items([r for r in rows if r["key"] in cited]):
        unresolved = r["field"] in ("entry", "doi") and r["status"] != "OK"
        rule = "R1" if unresolved else "R2"
        detail = r["detail"] or f"bib {r['bib']!r} vs publisher {r['publisher']!r}"
        out.append(Violation(rule, f"key {r['key']} field {r['field']}",
                             f"{r['status']}: {detail}"))
    return out


def sync_violations(stats: dict[str, int]) -> list[Violation]:
    pending = {k: v for k, v in stats.items() if k in ("new", "reworded", "removed", "revived")
               and v}
    if not pending:
        return []
    return [Violation("R7", "ledger", f"out of date with the sources {pending}; run "
                      "`litverify sync` and commit the ledger")]
