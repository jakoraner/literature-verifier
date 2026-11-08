"""``litverify obtain``: a page listing every cited source that is not yet in hand or not yet
verified, grouped by who acts, with links to get the PDF.

Groups:

* **You must obtain** - no PDF and no open-access copy known: borrow, buy, request through
  the library, or ask the authors.
* **Open-access copy found** - no PDF in hand, but Unpaywall knows a legal free copy.
  ``litverify obtain --download`` saves it as ``<first pdf_dir>/<key>.pdf``.
* **In hand, verification open** - the PDF exists, but a citation of it still violates a
  rule (unchecked, stale after rewording, waiting for a human decision, metadata).

Each entry carries the full reference, where it is cited (file:line), the DOI link, the
open-access link, and a link through the user's own library (``[obtain] library_link``, a
template with ``{doi}``, ``{doi_url}``, ``{title}`` or ``{query}`` placeholders; see
docs/library-links.md for OpenURL, EZproxy and discovery-search examples).

Open-access lookup uses the Unpaywall API, which requires a contact e-mail. It runs only if
the user sets ``[obtain] unpaywall_email`` in litverify.toml; the tool never supplies one.
The page is self-contained (inline CSS, no scripts, no external assets) and follows the
system light/dark setting.
"""

from __future__ import annotations

import html
import json
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .bib import family_names, latex_to_text
from .config import Config
from .http import Http, download
from .meta import clean_doi
from .rules import RULES, Violation


@dataclass
class Source:
    key: str
    reference: str
    doi: str
    cited_at: list[str] = field(default_factory=list)
    has_pdf: bool = False
    oa_url: str = ""
    oa_pdf: str = ""
    library_url: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def group(self) -> str:
        if not self.has_pdf:
            return "oa" if (self.oa_pdf or self.oa_url) else "obtain"
        return "verify"


GROUPS = {
    "obtain": ("You must obtain", "No PDF in hand and no open-access copy known. Use the "
               "library link, an interlibrary loan, the publisher, or ask the authors."),
    "oa": ("Open-access copy found", "A legal free copy exists. `litverify obtain --download` "
           "saves direct PDF links into the first PDF folder; otherwise open the link and save "
           "the file as <key>.pdf."),
    "verify": ("In hand, verification open", "The PDF exists; the citations still need a "
               "located passage, a re-check, a human decision or a metadata fix."),
}


def reference(key: str, f: dict[str, str] | None) -> str:
    if not f:
        return f"{key} (not in the bib)"
    fams, trunc = family_names(f.get("author", "") or f.get("editor", ""))
    who = (", ".join(fams[:3]) + (" et al." if len(fams) > 3 or trunc else "")) or "n.a."
    year = f.get("year", "") or f.get("date", "")[:4] or "n.d."
    parts = [f"{who} ({year}). {latex_to_text(f.get('title', ''))}."]
    venue = latex_to_text(f.get("journal") or f.get("journaltitle") or f.get("booktitle")
                          or f.get("publisher") or "")
    if venue:
        vol = f.get("volume", "")
        num = f.get("number", "") or f.get("issue", "")
        pages = latex_to_text(f.get("pages", ""))
        venue += (f", {vol}" if vol else "") + (f"({num})" if num else "") + (
            f", {pages}" if pages else "")
        parts.append(venue + ".")
    return " ".join(parts)


def library_link(template: str, doi: str, title: str) -> str:
    if not template:
        return ""
    if "{doi" in template and not doi and not ("{title}" in template or "{query}" in template):
        return ""
    q = doi or title
    return template.format(doi=urllib.parse.quote(doi, safe="/"),
                           doi_url=urllib.parse.quote(f"https://doi.org/{doi}", safe="")
                           if doi else "",
                           title=urllib.parse.quote(title),
                           query=urllib.parse.quote(q))


def unpaywall(http: Http, doi: str, email: str, offline: bool = False) -> tuple[str, str]:
    """(landing url, pdf url) of the best open-access location, or ('', '')."""
    if not (doi and email):
        return "", ""
    st, body = http.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}"
                        f"?email={urllib.parse.quote(email)}", offline)
    if st != 200:
        return "", ""
    loc = json.loads(body).get("best_oa_location") or {}
    return loc.get("url_for_landing_page") or loc.get("url") or "", loc.get("url_for_pdf") or ""


def collect(cfg: Config, http: Http, rows: list[dict[str, str]],
            bib: dict[str, dict[str, str]], violations: list[Violation], has_pdf: dict[str, bool],
            offline: bool = False) -> list[Source]:
    live = [r for r in rows if r["status"] == "live"]
    cited = defaultdict(list)
    for r in live:
        cited[r["key"]].append(f"{r['file']}:{r['line']}")
    probs: dict[str, list[str]] = defaultdict(list)
    for v in violations:
        parts = v.where.split()
        key = parts[1] if len(parts) > 1 else ""
        if key in cited:
            probs[key].append(f"{v.rule} {RULES[v.rule]}: {v.detail}"
                              + (f" ({parts[0]})" if parts[0] != "key" else ""))
    out = []
    for key in sorted(cited):
        if has_pdf.get(key) and not probs[key]:
            continue
        f = bib.get(key)
        doi = clean_doi(f.get("doi", "")) if f else ""
        title = latex_to_text(f.get("title", "")) if f else key
        s = Source(key, reference(key, f), doi, cited[key], bool(has_pdf.get(key)),
                   problems=probs[key])
        if not s.has_pdf:
            s.oa_url, s.oa_pdf = unpaywall(http, doi, cfg.unpaywall_email, offline)
        s.library_url = library_link(cfg.library_link, doi, title)
        out.append(s)
    return out


def download_oa(cfg: Config, http: Http, sources: list[Source]) -> list[str]:
    """Save direct open-access PDFs as <first pdf_dir>/<key>.pdf; returns a log."""
    log = []
    target = cfg.path(cfg.pdf_dirs[0])
    for s in sources:
        if s.has_pdf or not s.oa_pdf:
            continue
        dest = target / f"{s.key}.pdf"
        ok, msg = download(http, s.oa_pdf, dest)
        if ok:
            s.has_pdf = True
        log.append(f"{s.key}: {msg}")
    return log


def _a(url: str, text: str) -> str:
    if not url:
        return ""
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def to_html(sources: list[Source], cfg: Config) -> str:
    e = html.escape
    sections = []
    for g, (title, blurb) in GROUPS.items():
        items = [s for s in sources if s.group == g]
        if not items:
            continue
        lis = []
        for s in items:
            links = " &middot; ".join(x for x in (
                _a(f"https://doi.org/{s.doi}" if s.doi else "", "DOI"),
                _a(s.oa_pdf, "open-access PDF"),
                _a(s.oa_url if s.oa_url != s.oa_pdf else "", "open-access page"),
                _a(s.library_url, "via your library"),
            ) if x) or '<span class="mute">no DOI and no library link configured</span>'
            probs = "".join(f"<li>{e(p)}</li>" for p in s.problems)
            lis.append(
                f'<li class="src"><div class="ref">{e(s.reference)}</div>'
                f'<div class="key"><code>{e(s.key)}</code> cited at '
                f'{e(", ".join(s.cited_at))}</div><div class="links">{links}</div>'
                + (f'<ul class="probs">{probs}</ul>' if probs else "") + "</li>")
        sections.append(f"<section><h2>{e(title)} <span class=\"n\">{len(items)}</span></h2>"
                        f"<p class=\"mute\">{e(blurb)}</p><ul class=\"list\">"
                        + "".join(lis) + "</ul></section>")
    notes = []
    if not cfg.unpaywall_email:
        notes.append("Open-access lookup is off: set [obtain] unpaywall_email in litverify.toml.")
    if not cfg.library_link:
        notes.append("No library link: set [obtain] library_link (see docs/library-links.md).")
    body = "".join(sections) or "<p>Every cited source is in hand and verified.</p>"
    note_html = "".join(f'<p class="mute">{e(n)}</p>' for n in notes)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sources to obtain</title>
<style>
:root {{ --fg:#1b1b1b; --bg:#fff; --mute:#666; --line:#ddd; --accent:#1d4f91; --warn:#8a4b00; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fg:#e8e8e8; --bg:#161616; --mute:#9a9a9a; --line:#333; --accent:#8db8ff;
          --warn:#f0b46a; }} }}
body {{ font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg);
       max-width: 60rem; margin: 2rem auto; padding: 0 16px; }}
a {{ color: var(--accent); }}
.mute {{ color: var(--mute); }}
.n {{ font-size: .8em; color: var(--mute); }}
.list {{ list-style: none; padding: 0; }}
.src {{ border-top: 1px solid var(--line); padding: .7rem 0; }}
.ref {{ font-weight: 500; }}
.key {{ color: var(--mute); font-size: .9em; overflow-wrap: anywhere; }}
.probs {{ color: var(--warn); font-size: .9em; margin: .3rem 0 0 1rem; padding: 0; }}
code {{ font-size: .95em; }}
</style></head><body>
<h1>Sources to obtain</h1>
<p>{len(sources)} cited sources are not yet in hand or not yet verified.</p>
{note_html}
{body}
</body></html>
"""


def to_markdown(sources: list[Source]) -> str:
    L = ["# Sources to obtain", ""]
    for g, (title, blurb) in GROUPS.items():
        items = [s for s in sources if s.group == g]
        if not items:
            continue
        L += [f"## {title} ({len(items)})", "", blurb, ""]
        for s in items:
            L.append(f"- **{s.reference}** `{s.key}`, cited at {', '.join(s.cited_at)}")
            for label, url in (("DOI", f"https://doi.org/{s.doi}" if s.doi else ""),
                               ("open-access PDF", s.oa_pdf), ("open-access page", s.oa_url),
                               ("via your library", s.library_url)):
                if url:
                    L.append(f"  - {label}: <{url}>")
            L += [f"  - {p}" for p in s.problems]
        L.append("")
    if not sources:
        L.append("Every cited source is in hand and verified.")
    return "\n".join(L) + "\n"


def write(cfg: Config, sources: list[Source]) -> tuple[Path, Path]:
    out = cfg.report_path
    out.mkdir(parents=True, exist_ok=True)
    h, m = out / "obtain.html", out / "obtain.md"
    h.write_text(to_html(sources, cfg), encoding="utf-8")
    m.write_text(to_markdown(sources), encoding="utf-8")
    return h, m
