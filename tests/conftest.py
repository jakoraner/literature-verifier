from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from literature_verifier import http as http_mod

HERE = Path(__file__).parent
EXAMPLE = HERE.parent / "examples" / "minimal"
FIXTURES = HERE / "fixtures"


def make_pdf(path: Path, pages: list[list[str]], labels: list[str] | None = None) -> Path:
    """Write a minimal PDF; each page is a list of text lines (an empty list = image-only page).
    ``labels`` sets printed page labels (/PageLabels, decimal style starting at that number)."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [3 + 2 * i for i in range(n_pages)]
    font_id = 3 + 2 * n_pages
    catalog = b"<</Type/Catalog/Pages 2 0 R"
    if labels:
        start = int(labels[0])
        catalog += f"/PageLabels<</Nums[0<</S/D/St {start}>>]>>".encode()
    objs.append(catalog + b">>")
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objs.append(f"<</Type/Pages/Kids[{kids}]/Count {n_pages}>>".encode())
    for i, lines in enumerate(pages):
        ops = ["BT /F1 11 Tf 72 720 Td 14 TL"]
        for ln in lines:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode() if lines else b""
        objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {page_ids[i] + 1}"
                    f" 0 R/Resources<</Font<</F1 {font_id} 0 R>>>>>>".encode())
        objs.append(f"<</Length {len(stream)}>>stream\n".encode() + stream + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


class FakeWeb:
    """Serves Crossref fixtures by DOI; everything else is 404. Records every URL asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.routes: dict[str, tuple[int, str]] = {}
        for f in (FIXTURES / "crossref").glob("*.json"):
            doi = f.stem.replace("_", "/", 1)
            self.routes[f"https://api.crossref.org/works/{doi}"] = (200, f.read_text())

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        self.calls.append((url, headers))
        for prefix, resp in self.routes.items():
            if url == prefix or (prefix.endswith("*") and url.startswith(prefix[:-1])):
                return resp
        if "api.crossref.org/works?" in url:
            return 200, json.dumps({"message": {"items": []}})
        return 404, "not found"


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb()
    monkeypatch.setattr(http_mod, "_urlopen", fake)
    monkeypatch.setattr(http_mod, "_last_request", {})
    return fake


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fresh copy of examples/minimal without ledger, audit or PDFs."""
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("main.tex", "references.bib", "litverify.toml"):
        shutil.copy(EXAMPLE / name, root / name)
    (root / "litverify.toml").write_text(
        (root / "litverify.toml").read_text() + "\n[http]\nmin_interval = 0\n")
    return root


HARRIS = ["Array programming provides a powerful, compact and expressive syntax.",
          "NumPy is the primary array programming library for the Python language."]
SCIPY = ["SciPy is a library of numerical routines for the Python programming",
         "language that provides fundamental building blocks for modeling."]
FAIR = ["There is an urgent need to improve the infrastructure supporting the reuse",
        "of scholarly data. Findable, Accessible, Interoperable, Reusable."]


@pytest.fixture
def pdfs(project: Path) -> Path:
    d = project / "pdfs"
    make_pdf(d / "harris2020array.pdf", [HARRIS, ["page two"]], labels=["357"])
    make_pdf(d / "virtanen2020scipy.pdf", [SCIPY])
    make_pdf(d / "Wilkinson et al. 2016 - FAIR [wilkinson2016fair].pdf", [[], FAIR])
    return d
