"""End to end on the example project: check fails, then passes once every rule is met."""

import json
import shutil
from pathlib import Path

from literature_verifier import ledger as L
from literature_verifier.cli import main
from literature_verifier.review import fingerprint


def run(project: Path, *args: str) -> int:
    return main(["-C", str(project), *args])


def verify_all(project: Path) -> None:
    run(project, "sync")
    ex = {"C0001": ("357", "NumPy is the primary array programming library"),
          "C0002": ("1", "SciPy is a library of numerical routines"),
          "C0004": ("1", "fundamental building blocks")}
    for cid, (page, text) in ex.items():
        assert run(project, "record", cid, "--verdict", "SUPPORTED", "--by", "verifier",
                   "--page", page, "--excerpt", text) == 0
    assert run(project, "record", "C0003", "--verdict", "PARTLY", "--by", "verifier",
               "--page", "357", "--excerpt", "primary array programming library",
               "--what-differs", "peer review not stated") == 0
    assert run(project, "decide", "C0003", "accepted: narrowed to 'journal articles' later") == 0
    assert run(project, "record", "C0005", "--verdict", "SUPPORTED", "--by", "verifier",
               "--page", "2", "--excerpt", "Findable, Accessible, Interoperable") == 0


def test_check_fails_naming_citation_and_rule(project: Path, web, capsys):
    run(project, "sync")
    capsys.readouterr()
    assert run(project, "check") == 1
    out = capsys.readouterr().out
    assert "FAIL R3 PDF in hand: C0001 harris2020array main.tex:9" in out
    assert "FAIL R4 passage located on its page: C0001" in out
    assert "FAIL R1 work resolves to a registry record: key harris2020array" in out


def test_check_passes_when_every_rule_met(project: Path, pdfs: Path, web, capsys):
    run(project, "meta")
    verify_all(project)
    capsys.readouterr()
    assert run(project, "check") == 0, capsys.readouterr().out
    assert "OK: all 5 citations pass" in capsys.readouterr().out


def test_check_catches_unsynced_stale_and_tampered(project: Path, pdfs: Path, web, capsys):
    run(project, "meta")
    verify_all(project)
    tex = project / "main.tex"
    tex.write_text(tex.read_text().replace("for Python \\parencite{harris2020array}",
                                           "for Python and R \\parencite{harris2020array}"))
    capsys.readouterr()
    assert run(project, "check") == 1
    out = capsys.readouterr().out
    assert "R7 ledger in sync" in out
    run(project, "sync")
    assert run(project, "check") == 1
    assert "FAIL R6 reworded sentence re-verified: C0001" in capsys.readouterr().out
    tex.write_text(tex.read_text().replace(" and R \\parencite", " \\parencite"))
    run(project, "sync")
    # tampering with the excerpt in the ledger is caught by re-reading the PDF
    rows = L.load(project / "citations.csv")
    for r in rows:
        if r["cid"] == "C0002":
            r["excerpt"] = "SciPy is written by robots"
    L.save(project / "citations.csv", rows)
    capsys.readouterr()
    assert run(project, "check") == 1
    assert "FAIL R4 passage located on its page: C0002" in capsys.readouterr().out


def test_ledger_only_mode_for_ci(project: Path, pdfs: Path, web, capsys):
    run(project, "meta")
    verify_all(project)
    shutil.rmtree(pdfs)
    assert run(project, "check") == 1
    capsys.readouterr()
    assert run(project, "check", "--ledger-only") == 0
    assert "ledger-only" in capsys.readouterr().out


def test_review_page_and_import(project: Path, pdfs: Path, web, capsys, tmp_path):
    run(project, "meta")
    verify_all(project)
    assert run(project, "review") == 0
    page = (project / "litverify-report" / "review" / "index.html").read_text()
    assert page.count('class="card"') == 5
    assert "<mark>\\parencite{harris2020array}</mark>" in page
    assert "http" not in page.split("<body")[1].replace("https://doi.org", "")  # no assets
    rows = {r["cid"]: r for r in L.load(project / "citations.csv")}
    export = {"format": "litverify-review/1", "decisions": {
        cid: {"answer": "agree", "note": "", "fp": fingerprint(r), "at": "2026-11-08"}
        for cid, r in rows.items()}}
    export["decisions"]["C0004"]["answer"] = "disagree"
    export["decisions"]["C0004"]["note"] = "too broad"
    export["decisions"]["C0005"]["fp"] = "stale"
    f = tmp_path / "d.json"
    f.write_text(json.dumps(export))
    capsys.readouterr()
    assert run(project, "review", "--import", str(f)) == 0
    out = capsys.readouterr().out
    assert "C0005: sentence or verdict changed" in out and "imported 4" in out
    assert run(project, "check") == 1
    assert "FAIL R8 author checked it with their own eyes: C0004" in capsys.readouterr().out
    assert run(project, "check", "--require-human") == 1
    out = capsys.readouterr().out
    assert "C0005" in out and "no current sign-off" in out


def test_report_and_obtain(project: Path, web, capsys):
    run(project, "sync")
    run(project, "meta")
    cfg = project / "litverify.toml"
    cfg.write_text(cfg.read_text().replace(
        'library_link = ""', 'library_link = "https://lib.example.edu/openurl?id=doi:{doi}"')
        .replace('unpaywall_email = ""', 'unpaywall_email = "me@example.org"'))
    web.routes["https://api.unpaywall.org/v2/10.1038/sdata.2016.18*"] = (200, json.dumps(
        {"best_oa_location": {"url_for_pdf": "https://oa.example.org/fair.pdf",
                              "url_for_landing_page": "https://oa.example.org/fair"}}))
    assert run(project, "report") == 0
    assert run(project, "obtain") == 0
    out = project / "litverify-report"
    assert "Open items: 10" in (out / "report.md").read_text()
    md = (out / "obtain.md").read_text()
    assert "## You must obtain (2)" in md and "## Open-access copy found (1)" in md
    assert "https://lib.example.edu/openurl?id=doi:10.1038/sdata.2016.18" in md
    html = (out / "obtain.html").read_text()
    assert "<script" not in html and "prefers-color-scheme: dark" in html
    cached = "".join(p.read_text() for p in (project / ".litverify" / "cache").glob("*.json"))
    assert "me@example.org" not in cached
    capsys.readouterr()
    assert run(project, "report", "--json") == 0
    s = json.loads(capsys.readouterr().out)
    assert s["citations_live"] == 5 and s["verdicts"]["UNCHECKED"] == 5
