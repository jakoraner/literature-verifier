"""The citation ledger: one CSV row per citation occurrence, stable across edits.

A row is anchored by a hash of (key, citing sentence), not by line number, so edits
elsewhere in the file move a row's ``line`` but keep its id. A sentence that is reworded
keeps its row when the old and new wording are similar (difflib ratio >= 0.75); the row's
``checked_sentence`` then no longer equals ``sentence`` and the citation must be re-verified
(rule R6). A rewritten sentence gets a new UNCHECKED row. Citations that disappear from the
sources are kept with ``status=removed`` and revived if they return.

Columns written by ``sync`` (from the sources): key, file, line, command, prenote, postnote,
sentence, anchor, pdf, status. Columns written by ``record`` (verification): page, pdf_page,
excerpt, located, verdict, what_differs, checked_sentence, checked_by, checked_at. Columns
written by the human: decision, decided_at (``decide``) and human_checked, human_note
(``review --import``: agree / disagree / unsure, with the date). ``sync`` never touches the
verification or human columns.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import difflib
import os
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from . import VERDICTS
from .config import Config
from .pdfs import PdfResolver
from .pdftext import locate
from .tex import Citation, normalise

FIELDS = ["cid", "key", "file", "line", "command", "prenote", "postnote", "sentence", "anchor",
          "pdf", "page", "pdf_page", "excerpt", "located", "verdict", "what_differs",
          "checked_sentence", "checked_by", "checked_at", "decision", "decided_at",
          "human_checked", "human_note", "status"]
REWORD_RATIO = 0.75
NEEDS_PASSAGE = ("SUPPORTED", "PARTLY", "WRONG_VERSION")


def today() -> str:
    return dt.date.today().isoformat()


@contextlib.contextmanager
def locked(path: Path, timeout: float = 60.0) -> Iterator[None]:
    """Exclusive lock (``<path>.lock``) around a read-modify-write of a CSV, so parallel
    agents running ``litverify record`` cannot overwrite each other's rows."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} is held; remove it if no litverify runs") from None
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def load(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [{f: r.get(f, "") or "" for f in FIELDS} for r in csv.DictReader(fh)]


def save(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: int(r["cid"][1:]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def sync(cfg: Config, cites: list[Citation], resolver: PdfResolver,
         old: list[dict[str, str]]) -> tuple[list[dict[str, str]], Counter[str]]:
    """Merge the current citation occurrences into the ledger rows; returns (rows, stats)."""
    stats: Counter[str] = Counter()
    by_anchor: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in old:  # live rows win over removed ones with the same anchor
        k = (r["file"], r["key"], r["anchor"])
        if k not in by_anchor or r["status"] == "live":
            by_anchor[k] = r
    unmatched = {r["cid"]: r for r in old}
    n = max([int(r["cid"][1:]) for r in old] or [0])
    rows: list[dict[str, str]] = []
    pending: list[Citation] = []
    for c in cites:  # pass 1: exact anchors
        r = by_anchor.get((c.file, c.key, c.anchor))
        if r is not None and r["cid"] in unmatched:
            unmatched.pop(r["cid"])
            stats["revived" if r["status"] == "removed" else "unchanged"] += 1
            rows.append(_update(dict(r), c, resolver, cfg))
        else:
            pending.append(c)
    for c in pending:  # pass 2: reworded sentences, same key, best similarity
        cands = [x for x in unmatched.values() if x["key"] == c.key and x["status"] == "live"]
        best = max(cands, key=lambda x: (x["file"] == c.file,
                                         _similarity(x["sentence"], c.sentence)), default=None)
        if best and _similarity(best["sentence"], c.sentence) >= REWORD_RATIO:
            unmatched.pop(best["cid"])
            stats["reworded"] += 1
            rows.append(_update(dict(best), c, resolver, cfg))
            continue
        n += 1
        stats["new"] += 1
        r = {f: "" for f in FIELDS}
        r.update(cid=f"C{n:04d}", verdict="UNCHECKED")
        rows.append(_update(r, c, resolver, cfg))
    for r in unmatched.values():
        if r["status"] != "removed":
            stats["removed"] += 1
        r = dict(r)
        r["status"] = "removed"
        rows.append(r)
    return sorted(rows, key=lambda r: int(r["cid"][1:])), stats


def _update(r: dict[str, str], c: Citation, resolver: PdfResolver, cfg: Config) -> dict[str, str]:
    pdf = resolver.for_key(c.key)
    r.update(key=c.key, file=c.file, line=str(c.line), command=c.command, prenote=c.prenote,
             postnote=c.postnote, sentence=c.sentence, anchor=c.anchor, status="live",
             pdf=cfg.rel(pdf) if pdf else "")
    return r


def is_stale(r: dict[str, str]) -> bool:
    """Verified against a different wording than the current sentence."""
    return r["verdict"] != "UNCHECKED" and normalise(r["checked_sentence"]) != normalise(
        r["sentence"])


class RecordError(ValueError):
    pass


def record(r: dict[str, str], resolver: PdfResolver, verdict: str, by: str, page: str = "",
           pdf_page: int | None = None, excerpt: str = "", what_differs: str = "",
           scan: bool = False) -> str:
    """Record a verification verdict on row ``r`` in place, enforcing the hard rules.

    Returns a one-line description of how the passage was located. Raises RecordError when
    the verdict is not backed: no PDF, no page, no excerpt, or an excerpt that is not on the
    stated page.
    """
    if verdict not in VERDICTS:
        raise RecordError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not by.strip():
        raise RecordError("--by is required: who checked it (agent name or person)")
    if r["status"] != "live":
        raise RecordError(f"{r['cid']} is {r['status']}; only live citations can be recorded")
    pdf = resolver.for_key(r["key"])
    located, note = "", ""
    if verdict in NEEDS_PASSAGE:
        if pdf is None:
            raise RecordError(f"no PDF for {r['key']}: a verdict needs the PDF in hand (R3)")
        if not page.strip() or not excerpt.strip():
            raise RecordError("a verdict needs the printed page (--page) and a verbatim excerpt "
                              "(--excerpt) of the passage (R4)")
        loc = locate(pdf, excerpt, page, pdf_page)
        if loc.status == "found":
            located, pdf_page = "text", loc.pdf_page
            note = f"excerpt found on PDF page {pdf_page} (printed {page})"
        elif loc.status == "no_text" and scan:
            located, pdf_page = "scan", loc.pdf_page
            note = f"PDF page {pdf_page} is image-only; excerpt recorded as transcribed (--scan)"
        elif loc.status == "no_text":
            raise RecordError(f"{loc.detail}: read the page image and record with --scan")
        else:
            raise RecordError(f"passage not located: {loc.detail} (R4)")
        if verdict != "SUPPORTED" and not what_differs.strip():
            raise RecordError(f"{verdict} needs --what-differs: what the source does not support")
    elif verdict == "NOT_FOUND":
        if pdf is None:
            raise RecordError("NOT_FOUND means the whole PDF was searched; it needs the PDF (R3)")
        if not what_differs.strip():
            raise RecordError("NOT_FOUND needs --what-differs: what was searched for")
    elif verdict == "NO_ACCESS" and pdf is not None:
        raise RecordError("a PDF exists for this key; NO_ACCESS does not apply")
    changed = verdict != r["verdict"] or normalise(r["checked_sentence"]) != normalise(
        r["sentence"])
    r.update(verdict=verdict, page=page if verdict in NEEDS_PASSAGE else "",
             pdf_page=str(pdf_page) if (verdict in NEEDS_PASSAGE and pdf_page) else "",
             excerpt=excerpt if verdict in NEEDS_PASSAGE else "", located=located,
             what_differs=what_differs, checked_sentence=r["sentence"],
             checked_by=by.strip(), checked_at=today())
    if changed and (r["decision"] or r["human_checked"]):
        # a decision or sign-off was about another verdict or wording
        r.update(decision="", decided_at="", human_checked="", human_note="")
        note += "; previous human decision/sign-off cleared (verdict or wording changed)"
    return note or "recorded"


def decide(r: dict[str, str], text: str) -> None:
    """Record the human decision on a non-SUPPORTED verdict."""
    if not text.strip():
        raise RecordError("a decision needs text: what was decided and why")
    if r["verdict"] == "UNCHECKED":
        raise RecordError(f"{r['cid']} is UNCHECKED: verify it before deciding")
    if is_stale(r):
        raise RecordError(f"{r['cid']} was checked against an older wording: re-verify first")
    r.update(decision=text.strip(), decided_at=today())
