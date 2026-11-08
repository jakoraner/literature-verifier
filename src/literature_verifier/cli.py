"""``litverify``: command-line interface.

    litverify sync      update the citation ledger from the .tex sources
    litverify pdfs      list cited keys and their PDFs; report missing ones
    litverify meta      audit bib metadata against Crossref / DataCite
    litverify report    write report.md / report.html (or --json to stdout)
    litverify check     CI gate: exit 1 on any rule violation, naming citation and rule
    litverify obtain    page of sources to obtain, with open-access and library links
    litverify locate    find an excerpt in a key's PDF (prints the PDF pages)
    litverify record    record a verification verdict for one citation (enforces the rules)
    litverify decide    record a human decision on a citation or a metadata field
    litverify review    page where the author checks every citation by eye (--import answers)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import VERDICTS, __version__
from . import ledger as L
from . import meta as M
from . import obtain as O
from . import report as R
from . import review as RV
from .bib import load_bib
from .config import Config, load
from .http import Http
from .pdfs import PdfResolver
from .pdftext import locate
from .rules import check_ledger, check_metadata, sync_violations
from .tex import scan_files


class LedgerResolver:
    """For CI without the PDFs: trusts the PDF path recorded in the committed ledger (which a
    local ``sync`` wrote only for files that existed) instead of looking on disk."""

    def __init__(self, rows: list[dict[str, str]]):
        self.paths = {r["key"]: r["pdf"] for r in rows if r["pdf"]}
        self.zotero_error = ""

    def for_key(self, key: str) -> Path | None:
        return Path(self.paths[key]) if key in self.paths else None


class Project:
    def __init__(self, root: str):
        self.cfg: Config = load(root)
        self.bib = load_bib(self.cfg.bib_path)
        self.resolver = PdfResolver(self.cfg, self.bib)
        self.http = Http(self.cfg)

    def cites(self):
        return scan_files(self.cfg.tex_files(), self.cfg.root)

    def ledger(self) -> list[dict[str, str]]:
        return L.load(self.cfg.ledger_path)

    def cited_keys(self) -> set[str]:
        return {c.key for c in self.cites()}

    def violations(self, relocate: bool = True, ledger_only: bool = False,
                   require_human: bool = False):
        old = self.ledger()
        resolver = LedgerResolver(old) if ledger_only else self.resolver
        rows, stats = L.sync(self.cfg, self.cites(), resolver, old)
        vs = sync_violations(stats)
        vs += check_ledger(rows, resolver, relocate and not ledger_only, require_human)
        vs += check_metadata({r["key"] for r in rows if r["status"] == "live"},
                             M.load(self.cfg.metadata_path))
        return rows, vs


def cmd_sync(p: Project, a: argparse.Namespace) -> int:
    rows, stats = L.sync(p.cfg, p.cites(), p.resolver, p.ledger())
    L.save(p.cfg.ledger_path, rows)
    live = sum(r["status"] == "live" for r in rows)
    print(f"ledger {p.cfg.rel(p.cfg.ledger_path)}: {live} live citations; changes "
          f"{dict(stats) or '{}'}")
    stale = [r["cid"] for r in rows if r["status"] == "live" and L.is_stale(r)]
    if stale:
        print(f"re-verify (sentence changed since checked): {', '.join(stale)}")
    if p.resolver.zotero_error:
        print(p.resolver.zotero_error)
    return 0


def cmd_pdfs(p: Project, a: argparse.Namespace) -> int:
    missing = []
    for key in sorted(p.cited_keys()):
        path, src = p.resolver.resolve(key)
        if path:
            print(f"  {key:30s} {src:6s} {p.cfg.rel(path)}")
        else:
            missing.append(key)
            print(f"  {key:30s} MISSING")
    if p.resolver.zotero_error:
        print(p.resolver.zotero_error)
    n = len(p.cited_keys())
    print(f"{n - len(missing)} of {n} cited sources have a PDF in hand")
    return 1 if missing and a.strict else 0


def cmd_meta(p: Project, a: argparse.Namespace) -> int:
    keys = sorted(p.bib) if a.all else sorted(p.cited_keys())
    rows = M.audit(p.http, keys, p.bib, M.load(p.cfg.metadata_path), offline=a.offline)
    M.save(p.cfg.metadata_path, rows)
    for r in M.open_items(rows):
        print(f"  {r['status']:5s} {r['key']} {r['field']}: {r['detail'] or ''} "
              f"[bib {r['bib']!r} | pub {r['publisher']!r}]")
    bad = len({r["key"] for r in M.open_items(rows)})
    print(f"audited {len(keys)} keys ({p.http.requests_made} requests, rest cached); "
          f"{bad} keys with open items -> {p.cfg.rel(p.cfg.metadata_path)}")
    return 0


def cmd_report(p: Project, a: argparse.Namespace) -> int:
    rows, vs = p.violations(relocate=not a.no_locate)
    s = R.summarise(rows, M.load(p.cfg.metadata_path), vs)
    if a.json:
        print(R.to_json(s))
        return 0
    out = p.cfg.report_path
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(R.markdown(s), encoding="utf-8")
    (out / "report.html").write_text(R.to_html(s), encoding="utf-8")
    print(f"{len(vs)} open items; wrote {p.cfg.rel(out / 'report.md')} and report.html")
    return 0


def cmd_check(p: Project, a: argparse.Namespace) -> int:
    rows, vs = p.violations(ledger_only=a.ledger_only, require_human=a.require_human)
    live = sum(r["status"] == "live" for r in rows)
    if not vs:
        mode = " (ledger-only: PDFs not re-read)" if a.ledger_only else ""
        last = "R8" if a.require_human else "R7 (+R8 disagreements)"
        print(f"OK: all {live} citations pass rules R1-{last}{mode}.")
        return 0
    for v in vs:
        print(f"FAIL {v}")
    by_rule: dict[str, int] = {}
    for v in vs:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
    print(f"{len(vs)} violations across {live} citations: "
          + ", ".join(f"{k} x{n}" for k, n in sorted(by_rule.items())))
    return 1


def cmd_obtain(p: Project, a: argparse.Namespace) -> int:
    rows, vs = p.violations(relocate=False)
    has = {k: p.resolver.for_key(k) is not None for k in {r["key"] for r in rows}}
    srcs = O.collect(p.cfg, p.http, rows, p.bib, vs, has, offline=a.offline)
    if a.download:
        for line in O.download_oa(p.cfg, p.http, srcs):
            print("  " + line)
    h, _ = O.write(p.cfg, srcs)
    counts = {g: sum(s.group == g for s in srcs) for g in O.GROUPS}
    print(f"wrote {p.cfg.rel(h)} and obtain.md: " + ", ".join(
        f"{O.GROUPS[g][0].lower()} {n}" for g, n in counts.items()))
    return 0


def _row(p: Project, cid: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    rows = p.ledger()
    for r in rows:
        if r["cid"] == cid:
            return rows, r
    raise SystemExit(f"no ledger row {cid}; run `litverify sync` first")


def cmd_locate(p: Project, a: argparse.Namespace) -> int:
    key, excerpt = a.key, a.excerpt
    if a.cid:
        _, r = _row(p, a.cid)
        key, excerpt = r["key"], excerpt or r["excerpt"]
    if not key or not excerpt:
        raise SystemExit("give --cid, or --key and --excerpt")
    pdf = p.resolver.for_key(key)
    if pdf is None:
        print(f"no PDF for {key}")
        return 1
    loc = locate(pdf, excerpt, a.page or "", a.pdf_page)
    pages = ", ".join(loc.pages_found) or "none"
    print(f"{loc.status}: {loc.detail or ''} (PDF pages holding the excerpt: {pages})")
    return 0 if loc.status == "found" else 1


def cmd_record(p: Project, a: argparse.Namespace) -> int:
    rows, r = _row(p, a.cid)
    try:
        note = L.record(r, p.resolver, a.verdict, a.by, a.page or "", a.pdf_page,
                        a.excerpt or "", a.what_differs or "", a.scan)
    except L.RecordError as e:
        print(f"REFUSED {a.cid}: {e}")
        return 1
    L.save(p.cfg.ledger_path, rows)
    print(f"{a.cid} {r['key']}: {a.verdict} ({note})")
    return 0


def cmd_decide(p: Project, a: argparse.Namespace) -> int:
    if a.meta:
        rows = M.load(p.cfg.metadata_path)
        hit = [r for r in rows if r["key"] == a.target and r["field"] == a.meta]
        if not hit:
            raise SystemExit(f"no metadata row {a.target} {a.meta}; run `litverify meta`")
        if hit[0]["status"] == "ERROR":
            raise SystemExit("ERROR rows cannot be decided; re-run `litverify meta`")
        hit[0]["decision"], hit[0]["decided_at"] = a.text.strip(), L.today()
        M.save(p.cfg.metadata_path, rows)
        print(f"decision recorded for {a.target} {a.meta}")
        return 0
    rows, r = _row(p, a.target)
    try:
        L.decide(r, a.text)
    except L.RecordError as e:
        print(f"REFUSED {a.target}: {e}")
        return 1
    L.save(p.cfg.ledger_path, rows)
    print(f"decision recorded for {a.target} ({r['verdict']})")
    return 0


def cmd_review(p: Project, a: argparse.Namespace) -> int:
    if a.import_file:
        rows = p.ledger()
        try:
            n, msgs = RV.import_decisions(rows, RV.load_json(Path(a.import_file)))
        except ValueError as e:
            print(f"REFUSED: {e}")
            return 1
        L.save(p.cfg.ledger_path, rows)
        for m in msgs:
            print("  " + m)
        print(f"imported {n} sign-offs into {p.cfg.rel(p.cfg.ledger_path)}")
        return 0
    rows, _ = L.sync(p.cfg, p.cites(), p.resolver, p.ledger())
    page = RV.write(p.cfg, rows, p.resolver.for_key)
    if not RV.poppler_available():
        print("note: poppler (pdftoppm, pdftotext) not found; cards show text without page "
              "images")
    print(f"wrote {p.cfg.rel(page)}: {sum(r['status'] == 'live' for r in rows)} citations")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="litverify", description=(
        "Guarantee that no citation is hallucinated: every cited work is real, in hand, and "
        "supports the sentence at a located page."))
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-C", "--root", default=".", help="project root (holds litverify.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="update the citation ledger from the .tex sources")
    s = sub.add_parser("pdfs", help="list cited keys and their PDFs")
    s.add_argument("--strict", action="store_true", help="exit 1 if any PDF is missing")
    s = sub.add_parser("meta", help="audit bib metadata against Crossref/DataCite")
    s.add_argument("--all", action="store_true", help="every bib entry, not only cited ones")
    s.add_argument("--offline", action="store_true", help="use the cache only")
    s = sub.add_parser("report", help="write the markdown and HTML report")
    s.add_argument("--json", action="store_true", help="print the summary as JSON instead")
    s.add_argument("--no-locate", action="store_true", help="skip re-reading excerpts in PDFs")
    s = sub.add_parser("check", help="CI gate: fail on any rule violation")
    s.add_argument("--ledger-only", action="store_true",
                   help="for CI without the PDFs: trust the PDF paths and located excerpts "
                   "recorded in the committed ledger instead of re-reading the files")
    s.add_argument("--require-human", action="store_true",
                   help="every citation needs the author's current 'agree' from review")
    s = sub.add_parser("review", help="page where the author checks every citation by eye")
    s.add_argument("--import", dest="import_file", metavar="JSON",
                   help="write exported review answers into the ledger")
    s = sub.add_parser("obtain", help="page of sources to obtain, with OA and library links")
    s.add_argument("--download", action="store_true",
                   help="save direct open-access PDFs into the first pdf_dir")
    s.add_argument("--offline", action="store_true", help="use the cache only")
    s = sub.add_parser("locate", help="find an excerpt in a key's PDF")
    s.add_argument("--cid")
    s.add_argument("--key")
    s.add_argument("--excerpt")
    s.add_argument("--page", help="page as printed")
    s.add_argument("--pdf-page", type=int, help="1-based page index in the PDF")
    s = sub.add_parser("record", help="record a verdict (refused unless the passage is located)")
    s.add_argument("cid")
    s.add_argument("--verdict", required=True, choices=VERDICTS)
    s.add_argument("--by", required=True, help="who checked: agent name or person")
    s.add_argument("--page", help="page as printed on the cited page")
    s.add_argument("--pdf-page", type=int, help="1-based page index in the PDF")
    s.add_argument("--excerpt", help="verbatim passage; '...' marks elisions")
    s.add_argument("--what-differs", help="what the source does not support (non-SUPPORTED)")
    s.add_argument("--scan", action="store_true",
                   help="page is image-only; excerpt transcribed from the page image")
    s = sub.add_parser("decide", help="record a human decision")
    s.add_argument("target", help="citation id (C0001) or, with --meta, a citation key")
    s.add_argument("text", help="what was decided and why")
    s.add_argument("--meta", metavar="FIELD", help="decide a metadata field of a key instead")
    return ap


COMMANDS = {"sync": cmd_sync, "pdfs": cmd_pdfs, "meta": cmd_meta, "report": cmd_report,
            "check": cmd_check, "obtain": cmd_obtain, "locate": cmd_locate,
            "record": cmd_record, "decide": cmd_decide, "review": cmd_review}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if not Path(a.root).is_dir():
        raise SystemExit(f"not a directory: {a.root}")
    return COMMANDS[a.cmd](Project(a.root), a)


if __name__ == "__main__":
    sys.exit(main())
