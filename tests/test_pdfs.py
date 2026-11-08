from pathlib import Path

import pytest

from conftest import HARRIS, make_pdf
from literature_verifier.bib import bib_files
from literature_verifier.config import load
from literature_verifier.pdfs import PdfResolver
from literature_verifier.pdftext import locate
from literature_verifier.review import build_card, poppler_available


def test_resolve_from_dir_with_readable_name(project: Path, pdfs: Path):
    r = PdfResolver(load(project), {})
    assert r.resolve("harris2020array")[1] == "dir"
    assert r.for_key("wilkinson2016fair").name.endswith("[wilkinson2016fair].pdf")
    assert r.for_key("unknown") is None


def test_resolve_from_bib_file_field(project: Path, tmp_path: Path):
    pdf = make_pdf(tmp_path / "elsewhere" / "paper.pdf", [HARRIS])
    bib = {"k": {"file": f"Full Text:{pdf}:application/pdf"},
           "j": {"file": f"{tmp_path}/missing.pdf;{pdf}"}}
    r = PdfResolver(load(project), bib)
    assert r.resolve("k") == (pdf, "bib")
    assert r.resolve("j") == (pdf, "bib")
    assert bib_files("a.pdf;b.pdf") == ["a.pdf", "b.pdf"]


def test_resolve_from_zotero_local_api(project: Path, tmp_path: Path):
    pdf = make_pdf(tmp_path / "storage" / "ABCD" / "Paper One.pdf", [HARRIS])
    (project / "litverify.toml").write_text(
        (project / "litverify.toml").read_text().replace("local_api = false", "local_api = true"))
    items = [
        {"key": "P1", "data": {"itemType": "journalArticle", "citationKey": "harris2020array"}},
        {"key": "A1", "data": {"itemType": "attachment", "parentItem": "P1",
                               "contentType": "application/pdf"},
         "links": {"enclosure": {"href": pdf.as_uri()}}},
        {"key": "N1", "data": {"itemType": "note", "parentItem": "P1"}},
        {"key": "P2", "data": {"itemType": "book", "citationKey": "linked2020"}},
        {"key": "A2", "data": {"itemType": "attachment", "parentItem": "P2",
                               "linkMode": "linked_url", "contentType": "application/pdf",
                               "url": pdf.as_uri()}},
    ]
    asked = []

    def fake(url: str):
        asked.append(url)
        return items

    r = PdfResolver(load(project), {}, zotero_fetch=fake)
    assert r.resolve("harris2020array") == (pdf, "zotero")
    assert r.resolve("linked2020") == (pdf, "zotero")
    assert asked[0].startswith("http://localhost:23119/api/users/0/items")

    def down(url: str):
        raise OSError("connection refused")

    r2 = PdfResolver(load(project), {}, zotero_fetch=down)
    assert r2.for_key("harris2020array") is None
    assert "not reachable" in r2.zotero_error


def test_locate_by_label_index_ellipsis_and_scan(tmp_path: Path):
    pdf = make_pdf(tmp_path / "a.pdf", [["intro text"], [], HARRIS], labels=["10"])
    assert locate(pdf, "primary array ... Python lan-\nguage", page="12").status == "found"
    assert locate(pdf, "primary array", pdf_page=3).pdf_page == 3
    nf = locate(pdf, "primary array", page="10")
    assert nf.status == "not_found" and "found on PDF page(s) 3" in nf.detail
    assert locate(pdf, "anything", page="11").status == "no_text"
    assert locate(pdf, "anything", page="99").status == "no_page"
    assert locate(pdf, "intro text").pages_found == ("1",)


@pytest.mark.skipif(not poppler_available(), reason="poppler not installed")
def test_review_highlights_passage_words(tmp_path: Path):
    pdf = make_pdf(tmp_path / "a.pdf", [HARRIS])
    row = {"cid": "C0001", "key": "k", "pdf_page": "1", "excerpt": "primary array ... Python",
           "located": "text"}
    card = build_card(row, pdf, tmp_path / "img")
    assert card.image == "img/k-p1.png" and (tmp_path / "img" / "k-p1.png").exists()
    assert card.boxes and len(card.boxes) == 3  # "primary", "array", "Python"; elided words not
    assert card.crop is not None


def test_review_without_located_passage(tmp_path: Path):
    card = build_card({"cid": "C1", "key": "k", "pdf_page": "", "excerpt": "", "located": ""},
                      None, tmp_path)
    assert card.image == "" and card.note == "no located passage yet"
