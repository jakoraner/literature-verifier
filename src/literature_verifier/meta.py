"""Metadata audit: does each cited key resolve to a real work, and does the bib entry match it?

For every cited key: the DOI is looked up in Crossref (``api.crossref.org/works/<doi>``) and,
if Crossref does not hold it, in DataCite (which registers arXiv, Zenodo and many dataset
DOIs). The comparison is rule-based; nothing is ever written back to the .bib file. Fix the
Zotero item and re-export instead.

Per field the status is

* ``OK``    - matches after normalisation (LaTeX accents, case, punctuation, whitespace);
* ``FIX``   - a definite mismatch (title words, author names or count, year, volume, ...),
              or the key does not resolve to a registry record (no DOI, unknown DOI,
              missing from the bib);
* ``CHECK`` - possibly fine but needs a look (venue abbreviation, entry type, a field the
              publisher has and the bib lacks, diacritics-only author difference);
* ``ERROR`` - the registry could not be reached; re-run.

A FIX or CHECK passes ``check`` only once it is fixed or carries a human decision. Decisions
are kept across runs while the bib and publisher values of that field stay the same.

Fields compared: doi, title, author, year, venue (journal/booktitle), volume, issue
(number), pages, url (only that a doi.org URL names the same DOI; other URLs are not
fetched), type.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
from pathlib import Path

from .bib import family_names, fold, latex_to_text, ratio, web_to_text, word_diff
from .http import Http

FIELDS = ["key", "field", "status", "bib", "publisher", "detail", "source", "decision",
          "decided_at"]
COMPARED = ["doi", "title", "author", "year", "venue", "volume", "issue", "pages", "url",
            "type"]
TYPE_MAP = {
    "journal-article": {"article"},
    "proceedings-article": {"inproceedings", "conference"},
    "book": {"book", "mvbook"},
    "monograph": {"book"},
    "edited-book": {"book", "collection"},
    "book-chapter": {"incollection", "inbook"},
    "book-section": {"incollection", "inbook"},
    "posted-content": {"misc", "online", "unpublished", "article", "techreport"},
    "report": {"techreport", "report"},
    "dissertation": {"phdthesis", "mastersthesis", "thesis"},
    "dataset": {"misc", "dataset", "online"},
    # DataCite resourceTypeGeneral
    "Preprint": {"misc", "online", "unpublished", "article", "techreport"},
    "Text": {"misc", "online", "article", "techreport", "unpublished"},
    "Dataset": {"misc", "dataset", "online"},
    "Software": {"misc", "software", "online"},
    "JournalArticle": {"article"},
    "ConferencePaper": {"inproceedings"},
}


def clean_doi(doi: str) -> str:
    doi = latex_to_text(doi).strip()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)


def _year(parts: dict | None) -> int | None:
    try:
        return int(parts["date-parts"][0][0])  # type: ignore[index]
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def from_crossref(m: dict) -> dict:
    years = {k: _year(m.get(k)) for k in ("issued", "published-print", "published-online")}
    return {
        "source": "crossref",
        "doi": m.get("DOI", ""),
        "title": web_to_text(" ".join(m.get("title") or [])),
        "subtitle": web_to_text(" ".join(m.get("subtitle") or [])),
        "authors": [web_to_text(a.get("family") or a.get("name") or "")
                    for a in m.get("author") or []],
        "year": years["issued"],
        "years_all": {k: v for k, v in years.items() if v},
        "venue": [web_to_text(x) for x in (m.get("container-title") or [])
                  + (m.get("short-container-title") or [])],
        "volume": m.get("volume", ""),
        "issue": m.get("issue", ""),
        "pages": m.get("page", "") or m.get("article-number", ""),
        "type": m.get("type", ""),
    }


def from_datacite(d: dict) -> dict:
    at = d["data"]["attributes"]
    titles = at.get("titles") or []
    main = next((t["title"] for t in titles if not t.get("titleType")),
                titles[0]["title"] if titles else "")
    sub = next((t["title"] for t in titles if t.get("titleType") == "Subtitle"), "")
    authors = [c.get("familyName") or c.get("name", "").split(",")[0]
               for c in at.get("creators") or []]
    cont = at.get("container") or {}
    year = at.get("publicationYear")
    return {"source": "datacite", "doi": at.get("doi", ""), "title": web_to_text(main),
            "subtitle": web_to_text(sub), "authors": authors,
            "year": int(year) if str(year or "").isdigit() else None,
            "years_all": {"publicationYear": year},
            "venue": [cont["title"]] if cont.get("title") else [],
            "volume": cont.get("volume", ""), "issue": cont.get("issue", ""),
            "pages": "", "type": (at.get("types") or {}).get("resourceTypeGeneral", "")}


def _row(key: str, field: str, status: str, bib: object = "", pub: object = "",
         detail: str = "", source: str = "") -> dict[str, str]:
    return {"key": key, "field": field, "status": status, "bib": str(bib or ""),
            "publisher": str(pub or ""), "detail": detail, "source": source,
            "decision": "", "decided_at": ""}


def _norm_pages(s: str) -> str:
    return re.sub(r"\s|\u2013|\u2014|--", lambda m: "-" if m.group(0).strip() else "", s or "")


def _norm_venue(s: str) -> str:
    return re.sub(r"^(?:proceedings of (?:the )?|in )", "", fold(s)).strip()


def compare(key: str, f: dict[str, str], pub: dict) -> list[dict[str, str]]:
    src = pub["source"]
    out = [_row(key, "doi", "OK", clean_doi(f.get("doi", "")), pub["doi"], "", src)]
    # title
    bt_raw = latex_to_text(f.get("title", ""))
    full = pub["title"] + (": " + pub["subtitle"] if pub["subtitle"] else "")
    bt, pmain, pfull = fold(bt_raw), fold(pub["title"]), fold(full)
    if bt == pfull:
        out.append(_row(key, "title", "OK", bt_raw, full, "", src))
    elif bt == pmain:
        out.append(_row(key, "title", "CHECK", bt_raw, full,
                        "publisher record has a subtitle the bib omits", src))
    else:
        out.append(_row(key, "title", "FIX", bt_raw, full,
                        f"ratio {ratio(bt, pfull)}: {word_diff(bt, pfull)}", src))
    # authors
    bfam, truncated = family_names(f.get("author", ""))
    pfam = [a for a in pub["authors"] if a]
    shown_b = "; ".join(bfam) + ("; and others" if truncated else "")
    if not pfam:
        out.append(_row(key, "author", "CHECK", shown_b, "",
                        "publisher record lists no authors; compare by hand", src))
    elif not bfam:
        out.append(_row(key, "author", "FIX", "", "; ".join(pfam), "bib has no author", src))
    else:
        probs = [f"#{i + 1} bib '{b}' vs publisher '{p}'"
                 for i, (b, p) in enumerate(zip(bfam, pfam, strict=False)) if fold(b) != fold(p)]
        if truncated and len(pfam) < len(bfam):
            probs.append(f"count bib {len(bfam)}+others vs publisher {len(pfam)}")
        elif not truncated and len(bfam) != len(pfam):
            probs.append(f"count bib {len(bfam)} vs publisher {len(pfam)}")
        spelling = [f"'{b}' vs '{p}'" for b, p in zip(bfam, pfam, strict=False)
                    if fold(b) == fold(p) and b.casefold() != p.casefold()]
        if probs:
            out.append(_row(key, "author", "FIX", shown_b, "; ".join(pfam),
                            "; ".join(probs[:8]) + (" ..." if len(probs) > 8 else ""), src))
        elif truncated:
            out.append(_row(key, "author", "CHECK", shown_b, "; ".join(pfam),
                            f"bib truncates with 'and others' ({len(pfam)} authors)", src))
        elif spelling:
            out.append(_row(key, "author", "CHECK", shown_b, "; ".join(pfam),
                            "differs only in diacritics/case: " + ", ".join(spelling), src))
        else:
            out.append(_row(key, "author", "OK", shown_b, "; ".join(pfam), "", src))
    # year
    by = (re.findall(r"\d{4}", f.get("year", "") or f.get("date", "")) or [""])[0]
    py = pub["year"]
    if not py:
        out.append(_row(key, "year", "CHECK", by, "", "publisher record has no year", src))
    elif not by:
        out.append(_row(key, "year", "FIX", "", py, "bib has no year", src))
    elif int(by) == int(py):
        out.append(_row(key, "year", "OK", by, py, "", src))
    elif int(by) in {int(v) for v in pub["years_all"].values() if v}:
        out.append(_row(key, "year", "CHECK", by, py,
                        f"bib year matches another publisher date {json.dumps(pub['years_all'])}",
                        src))
    else:
        out.append(_row(key, "year", "FIX", by, py, json.dumps(pub["years_all"]), src))
    # venue
    bv = latex_to_text(f.get("journal") or f.get("journaltitle") or f.get("booktitle") or "")
    if not pub["venue"]:
        out.append(_row(key, "venue", "OK", bv, "", "publisher record has no container title",
                        src))
    elif not bv:
        out.append(_row(key, "venue", "CHECK", "", pub["venue"][0], "bib has no venue", src))
    else:
        nb = _norm_venue(bv)
        ok = any(nb == (pv := _norm_venue(v)) or (nb and pv and (nb in pv or pv in nb))
                 for v in pub["venue"])
        out.append(_row(key, "venue", "OK" if ok else "CHECK", bv, " | ".join(pub["venue"]),
                        "" if ok else "venue differs (abbreviation or wrong venue?)", src))
    # volume, issue, pages
    for name, bval, pval, norm in (
        ("volume", f.get("volume", ""), pub["volume"], str.strip),
        ("issue", f.get("number", "") or f.get("issue", ""), pub["issue"], str.strip),
        ("pages", f.get("pages", ""), pub["pages"], _norm_pages),
    ):
        bval, pval = latex_to_text(str(bval)), str(pval or "")
        if not pval:
            out.append(_row(key, name, "OK", bval, "", "publisher record has none", src))
        elif not bval:
            out.append(_row(key, name, "CHECK", "", pval, "bib lacks it", src))
        elif norm(bval) == norm(pval):
            out.append(_row(key, name, "OK", bval, pval, "", src))
        else:
            out.append(_row(key, name, "FIX", bval, pval, "", src))
    # url
    url = f.get("url", "")
    m = re.match(r"https?://(?:dx\.)?doi\.org/(.+)$", url.strip(), flags=re.I)
    if m and urllib.parse.unquote(m.group(1)).lower() != pub["doi"].lower():
        out.append(_row(key, "url", "FIX", url, f"https://doi.org/{pub['doi']}",
                        "doi.org URL names a different DOI", src))
    else:
        out.append(_row(key, "url", "OK", url, "", "" if m else "not fetched", src))
    # type
    etype = f.get("ENTRYTYPE", "")
    allowed = TYPE_MAP.get(pub["type"])
    if allowed is None or etype in allowed:
        out.append(_row(key, "type", "OK", etype, pub["type"], "", src))
    else:
        out.append(_row(key, "type", "CHECK", etype, pub["type"],
                        f"registry type {pub['type']} usually exports as "
                        f"@{'/@'.join(sorted(allowed))}", src))
    return out


def search_candidates(http: Http, f: dict[str, str], offline: bool) -> str:
    """Crossref bibliographic search on the title. Reported for the human, never adopted."""
    title = latex_to_text(f.get("title", ""))
    if not title:
        return ""
    url = ("https://api.crossref.org/works?rows=3&select=DOI,title,author,issued"
           "&query.bibliographic=" + urllib.parse.quote(title))
    st, body = http.get(url, offline)
    if st != 200:
        return ""
    first_bib = (family_names(f.get("author", ""))[0] or [""])[0]
    cands = []
    for it in json.loads(body)["message"]["items"]:
        ct = web_to_text(" ".join(it.get("title") or []))
        fa = ((it.get("author") or [{}])[0]).get("family", "")
        strong = fold(ct) == fold(title) and bool(fa) and fold(fa) == fold(first_bib)
        cands.append(("STRONG " if strong else "") + f"{it['DOI']} ({fa}, "
                     f"{_year(it.get('issued'))}): {ct}")
    return "candidates (verify on the landing page, then add in Zotero): " + " || ".join(cands)


def audit_key(http: Http, key: str, f: dict[str, str] | None,
              offline: bool = False) -> list[dict[str, str]]:
    if f is None:
        return [_row(key, "entry", "FIX", "", "", "UNRESOLVED: cited key is not in the bib")]
    doi = clean_doi(f.get("doi", ""))
    if not doi:
        return [_row(key, "doi", "FIX", "", "",
                     "UNRESOLVED: no DOI, so the work cannot be matched to a registry record. "
                     "Add the DOI in Zotero, or record a decision naming the equivalent record "
                     "you checked (ISBN, publisher page). "
                     + search_candidates(http, f, offline))]
    q = urllib.parse.quote(doi, safe="/")
    st, body = http.get(f"https://api.crossref.org/works/{q}", offline)
    if st == 200:
        return compare(key, f, from_crossref(json.loads(body)["message"]))
    st2, body2 = http.get(f"https://api.datacite.org/dois/{q}", offline)
    if st2 == 200:
        return compare(key, f, from_datacite(json.loads(body2)))
    if st in (404,) and st2 in (404,):
        return [_row(key, "doi", "FIX", doi, "",
                     "UNRESOLVED: DOI not found in Crossref or DataCite")]
    return [_row(key, "doi", "ERROR", doi, "", f"lookup failed (Crossref {st}, DataCite {st2})")]


def load(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [{f: r.get(f, "") or "" for f in FIELDS} for r in csv.DictReader(fh)]


def save(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def audit(http: Http, keys: list[str], bib: dict[str, dict[str, str]],
          old: list[dict[str, str]], offline: bool = False) -> list[dict[str, str]]:
    """Audit ``keys``; keeps human decisions whose bib and publisher values are unchanged."""
    prev = {(r["key"], r["field"]): r for r in old}
    rows = []
    for key in sorted(set(keys)):
        for r in audit_key(http, key, bib.get(key), offline):
            p = prev.get((key, r["field"]))
            if p and p["decision"] and p["bib"] == r["bib"] and p["publisher"] == r["publisher"]:
                r["decision"], r["decided_at"] = p["decision"], p["decided_at"]
            rows.append(r)
    return rows


def open_items(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["status"] != "OK" and not (r["status"] in ("FIX", "CHECK")
                                                             and r["decision"])]
