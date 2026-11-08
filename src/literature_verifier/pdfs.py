"""Resolve a citation key to the PDF of the work, in this order:

1. a configured folder (``pdf_dirs``) holding ``<key>.pdf`` or ``<anything> [<key>].pdf``;
2. the ``file`` field of the bib entry (Better BibTeX can export attachment paths);
3. Zotero desktop's local API (``http://localhost:23119/api/``, Zotero 7 with "Allow other
   applications on this computer to communicate with Zotero" enabled): the item whose
   citation key matches (Zotero 7 ``citationKey`` field), and its first PDF attachment that
   is a stored file, a linked file, or a link to a ``file://`` URL.

Only files that exist on disk count. A key with no PDF fails rule R3 in ``check``.
"""

from __future__ import annotations

import glob
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .bib import bib_files
from .config import Config

ZoteroFetch = Callable[[str], list[dict]]


def _zotero_fetch(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def _attachment_paths(item: dict) -> list[Path]:
    """Local paths a Zotero PDF attachment may point at: a stored file (``links.enclosure``),
    a linked file (``data.path``) or a link to a ``file://`` URL (``data.url``)."""
    d = item.get("data", {})
    urls = [item.get("links", {}).get("enclosure", {}).get("href", ""), d.get("url", "")]
    out = [Path(urllib.parse.unquote(urllib.parse.urlparse(u).path))
           for u in urls if u.startswith("file://")]
    if d.get("linkMode") == "linked_file" and d.get("path", "").startswith("/"):
        out.append(Path(d["path"]))
    return out


class PdfResolver:
    def __init__(self, cfg: Config, bib: dict[str, dict[str, str]],
                 zotero_fetch: ZoteroFetch | None = None):
        self.cfg = cfg
        self.bib = bib
        self._zotero_fetch = zotero_fetch or _zotero_fetch
        self._zotero: dict[str, Path] | None = None
        self.zotero_error = ""

    def from_dirs(self, key: str) -> Path | None:
        for d in self.cfg.pdf_dirs:
            base = self.cfg.path(d)
            exact = base / f"{key}.pdf"
            if exact.is_file():
                return exact
            hits = sorted(glob.glob(str(base / f"*[[]{glob.escape(key)}[]].pdf")))
            if hits:
                return Path(hits[0])
        return None

    def from_bib(self, key: str) -> Path | None:
        for f in bib_files(self.bib.get(key, {}).get("file", "")):
            p = Path(f).expanduser()
            if not p.is_absolute():
                p = self.cfg.bib_path.parent / p
            if p.suffix.lower() == ".pdf" and p.is_file():
                return p
        return None

    def zotero_map(self) -> dict[str, Path]:
        """citation key -> local PDF path, from Zotero's local API (empty if unreachable)."""
        if self._zotero is not None:
            return self._zotero
        self._zotero = {}
        if not self.cfg.zotero_local_api:
            return self._zotero
        base = f"{self.cfg.zotero_url}/api/{self.cfg.zotero_library}/items"
        items: list[dict] = []
        try:
            start = 0
            while True:
                batch = self._zotero_fetch(f"{base}?format=json&limit=100&start={start}")
                items += batch
                if len(batch) < 100:
                    break
                start += 100
        except Exception as e:  # Zotero not running or local API disabled
            self.zotero_error = f"Zotero local API not reachable: {e}"
            return self._zotero
        keys = {i["key"]: i["data"].get("citationKey", "") for i in items
                if i.get("data", {}).get("itemType") not in ("attachment", "note", "annotation")}
        for i in items:
            d = i.get("data", {})
            if d.get("itemType") != "attachment" or d.get("contentType") != "application/pdf":
                continue
            ck = keys.get(d.get("parentItem", ""), "")
            if not ck or ck in self._zotero:
                continue
            for p in _attachment_paths(i):
                if p.is_file():
                    self._zotero[ck] = p
                    break
        return self._zotero

    def resolve(self, key: str) -> tuple[Path | None, str]:
        """(path, source) with source one of 'dir', 'bib', 'zotero', ''."""
        p = self.from_dirs(key)
        if p:
            return p, "dir"
        p = self.from_bib(key)
        if p:
            return p, "bib"
        p = self.zotero_map().get(key)
        if p:
            return p, "zotero"
        return None, ""

    def for_key(self, key: str) -> Path | None:
        return self.resolve(key)[0]
