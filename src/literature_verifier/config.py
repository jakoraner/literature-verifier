"""Project configuration, read from ``litverify.toml`` in the project root.

Every path is relative to the project root. All keys are optional::

    tex = ["chapters/*.tex", "main.tex"]   # globs of LaTeX sources to scan
    bib = "references.bib"                 # the Better BibTeX auto-export
    ledger = "citations.csv"               # one row per citation occurrence
    metadata = "metadata.csv"              # one row per (key, field) of the metadata audit
    pdf_dirs = ["pdfs"]                    # folders holding "<key>.pdf" or "... [<key>].pdf"
    report_dir = "litverify-report"
    cache_dir = ".litverify/cache"

    [zotero]
    local_api = false                      # ask Zotero desktop (localhost:23119) for PDFs
    library = "users/0"                    # or "groups/<id>"
    url = "http://localhost:23119"

    [http]
    user_agent = "literature-verifier/0.1.0 (bibliographic metadata audit)"
    min_interval = 1.0                     # seconds between requests to one host

    [obtain]
    unpaywall_email = ""                   # YOUR e-mail, sent to Unpaywall only; empty = off
    library_link = ""                      # e.g. "https://resolver.example.edu/openurl?id=doi:{doi}"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

CONFIG_NAME = "litverify.toml"
DEFAULT_UA = f"literature-verifier/{__version__} (bibliographic metadata audit)"


@dataclass
class Config:
    root: Path
    tex: list[str] = field(default_factory=lambda: ["**/*.tex"])
    bib: str = "references.bib"
    ledger: str = "citations.csv"
    metadata: str = "metadata.csv"
    pdf_dirs: list[str] = field(default_factory=lambda: ["pdfs"])
    report_dir: str = "litverify-report"
    cache_dir: str = ".litverify/cache"
    zotero_local_api: bool = False
    zotero_library: str = "users/0"
    zotero_url: str = "http://localhost:23119"
    user_agent: str = DEFAULT_UA
    min_interval: float = 1.0
    unpaywall_email: str = ""
    library_link: str = ""

    def path(self, rel: str) -> Path:
        return (self.root / rel).resolve()

    @property
    def bib_path(self) -> Path:
        return self.path(self.bib)

    @property
    def ledger_path(self) -> Path:
        return self.path(self.ledger)

    @property
    def metadata_path(self) -> Path:
        return self.path(self.metadata)

    @property
    def cache_path(self) -> Path:
        return self.path(self.cache_dir)

    @property
    def report_path(self) -> Path:
        return self.path(self.report_dir)

    def tex_files(self) -> list[Path]:
        seen: dict[Path, None] = {}
        for g in self.tex:
            for p in sorted(self.root.glob(g)):
                if p.is_file():
                    seen.setdefault(p.resolve(), None)
        return list(seen)

    def rel(self, p: Path) -> str:
        """A path for the ledger: relative to the root when inside it, else with ~ for home."""
        p = p.resolve()
        try:
            return p.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            home = Path.home().resolve()
            try:
                return "~/" + p.relative_to(home).as_posix()
            except ValueError:
                return p.as_posix()


def load(root: Path | str = ".") -> Config:
    root = Path(root).resolve()
    cfg = Config(root=root)
    f = root / CONFIG_NAME
    if not f.exists():
        return cfg
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    for k in ("tex", "pdf_dirs"):
        if k in data:
            v = data[k]
            setattr(cfg, k, [v] if isinstance(v, str) else list(v))
    for k in ("bib", "ledger", "metadata", "report_dir", "cache_dir"):
        if k in data:
            setattr(cfg, k, str(data[k]))
    z = data.get("zotero", {})
    cfg.zotero_local_api = bool(z.get("local_api", cfg.zotero_local_api))
    cfg.zotero_library = str(z.get("library", cfg.zotero_library))
    cfg.zotero_url = str(z.get("url", cfg.zotero_url)).rstrip("/")
    h = data.get("http", {})
    cfg.user_agent = str(h.get("user_agent", cfg.user_agent))
    cfg.min_interval = float(h.get("min_interval", cfg.min_interval))
    o = data.get("obtain", {})
    cfg.unpaywall_email = str(o.get("unpaywall_email", "")).strip()
    cfg.library_link = str(o.get("library_link", "")).strip()
    return cfg
