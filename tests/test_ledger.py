from pathlib import Path

import pytest

from literature_verifier import ledger as L
from literature_verifier.cli import main


def rows(project: Path):
    return {r["cid"]: r for r in L.load(project / "citations.csv")}


def test_sync_creates_rows(project: Path):
    assert main(["-C", str(project), "sync"]) == 0
    r = rows(project)
    assert len(r) == 5
    assert {x["key"] for x in r.values()} == {"harris2020array", "virtanen2020scipy",
                                              "wilkinson2016fair"}
    assert all(x["verdict"] == "UNCHECKED" and x["status"] == "live" for x in r.values())
    assert r["C0002"]["prenote"] == "see"


def test_ids_survive_line_shifts(project: Path):
    main(["-C", str(project), "sync"])
    before = rows(project)
    tex = project / "main.tex"
    tex.write_text(tex.read_text().replace("\\section{Scientific software}",
                                           "\\section{Scientific software}\n\nIntro.\n\n"))
    main(["-C", str(project), "sync"])
    after = rows(project)
    assert after.keys() == before.keys()
    assert int(after["C0001"]["line"]) == int(before["C0001"]["line"]) + 4


def test_rewording_keeps_row_but_makes_it_stale(project: Path, pdfs: Path):
    main(["-C", str(project), "sync"])
    assert main(["-C", str(project), "record", "C0001", "--verdict", "SUPPORTED", "--by", "t",
                 "--page", "357", "--excerpt", "the primary array programming library"]) == 0
    tex = project / "main.tex"
    tex.write_text(tex.read_text().replace("the primary array programming library",
                                           "the only array programming library"))
    main(["-C", str(project), "sync"])
    r = rows(project)["C0001"]
    assert r["verdict"] == "SUPPORTED" and "only" in r["sentence"]
    assert L.is_stale(r)


def test_rewrite_makes_new_row_and_marks_old_removed_then_revives(project: Path):
    main(["-C", str(project), "sync"])
    tex = project / "main.tex"
    original = tex.read_text()
    tex.write_text(original.replace(
        "NumPy is the primary array programming library for Python",
        "Completely different wording about arrays in many languages and tools"))
    main(["-C", str(project), "sync"])
    r = rows(project)
    assert r["C0001"]["status"] == "removed"
    assert r["C0006"]["key"] == "harris2020array" and r["C0006"]["status"] == "live"
    tex.write_text(original)
    main(["-C", str(project), "sync"])
    r = rows(project)
    assert r["C0001"]["status"] == "live"
    assert r["C0006"]["status"] == "removed"


def test_record_refuses_without_pdf_page_or_excerpt(project: Path, pdfs: Path):
    main(["-C", str(project), "sync"])
    rs = L.load(project / "citations.csv")
    from literature_verifier.cli import Project

    p = Project(str(project))
    r = next(x for x in rs if x["cid"] == "C0001")
    with pytest.raises(L.RecordError, match="printed page"):
        L.record(r, p.resolver, "SUPPORTED", "t", page="", excerpt="x")
    with pytest.raises(L.RecordError, match="not located"):
        L.record(r, p.resolver, "SUPPORTED", "t", page="357", excerpt="NumPy is written in Rust")
    with pytest.raises(L.RecordError, match="what-differs"):
        L.record(r, p.resolver, "PARTLY", "t", page="357", excerpt="primary array")
    with pytest.raises(L.RecordError, match="--by"):
        L.record(r, p.resolver, "SUPPORTED", " ", page="357", excerpt="primary array")
    note = L.record(r, p.resolver, "SUPPORTED", "t", page="357",
                    excerpt="NumPy is the primary ... Python language")
    assert r["pdf_page"] == "1" and r["located"] == "text" and "PDF page 1" in note


def test_record_refuses_without_pdf(project: Path):
    main(["-C", str(project), "sync"])
    assert main(["-C", str(project), "record", "C0001", "--verdict", "SUPPORTED", "--by", "t",
                 "--page", "1", "--excerpt", "anything"]) == 1


def test_scan_pages_need_the_scan_flag(project: Path, pdfs: Path):
    main(["-C", str(project), "sync"])
    args = ["-C", str(project), "record", "C0005", "--verdict", "SUPPORTED", "--by", "t",
            "--page", "1", "--pdf-page", "1", "--excerpt", "guiding principles"]
    assert main(args) == 1
    assert main([*args, "--scan"]) == 0
    assert rows(project)["C0005"]["located"] == "scan"


def test_decision_needs_current_check_and_is_cleared_on_new_verdict(project: Path, pdfs: Path):
    main(["-C", str(project), "sync"])
    assert main(["-C", str(project), "decide", "C0003", "ok"]) == 1  # UNCHECKED
    main(["-C", str(project), "record", "C0003", "--verdict", "PARTLY", "--by", "t", "--page",
          "357", "--excerpt", "primary array", "--what-differs", "peer review not stated"])
    assert main(["-C", str(project), "decide", "C0003", "narrow to 'journal articles'"]) == 0
    assert rows(project)["C0003"]["decision"].startswith("narrow")
    main(["-C", str(project), "record", "C0003", "--verdict", "SUPPORTED", "--by", "t",
          "--page", "357", "--excerpt", "primary array"])
    assert rows(project)["C0003"]["decision"] == ""


def test_parallel_records_do_not_lose_rows(project: Path, pdfs: Path):
    import threading

    main(["-C", str(project), "sync"])
    jobs = [("C0001", "357", "primary array"), ("C0002", "1", "numerical routines"),
            ("C0004", "1", "building blocks")]
    codes: list[int] = []

    def go(cid: str, page: str, ex: str) -> None:
        codes.append(main(["-C", str(project), "record", cid, "--verdict", "SUPPORTED",
                           "--by", "t", "--page", page, "--excerpt", ex]))

    threads = [threading.Thread(target=go, args=j) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [0, 0, 0]
    r = rows(project)
    assert all(r[c]["verdict"] == "SUPPORTED" for c, _, _ in jobs)
    assert not (project / "citations.csv.lock").exists()
