"""``litverify review``: the page where the author checks every citation with their own eyes.

One card per live citation: the manuscript sentence with the citation marked (file:line);
the verbatim passage with printed and PDF page; an image of that PDF page cropped to the
passage with the passage's words highlighted; the verdict and who gave it. The author answers
Agree / Disagree / Unsure with a note. Answers are kept in the browser (localStorage) and
exported as a JSON file, which ``litverify review --import FILE`` writes into the ledger's
``human_checked`` and ``human_note`` columns.

Each answer carries a fingerprint of what was shown (sentence, verdict, page, excerpt). An
answer is imported only if the fingerprint still matches the ledger, and a sign-off stops
counting as soon as the sentence or the verdict changes.

Page images and word boxes come from poppler (``pdftoppm`` and ``pdftotext -bbox``), which
must be on PATH; without poppler the cards show the text only. Images are written as PNG
files next to the HTML (``<report_dir>/review/img/``), never inlined. The page needs no
network and follows the system light/dark setting.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .bib import fold
from .config import Config
from .ledger import today
from .pdftext import _parts

DPI = 100
ANSWERS = ("agree", "disagree", "unsure")


def fingerprint(r: dict[str, str]) -> str:
    s = "|".join(" ".join(r[k].split()) for k in ("sentence", "verdict", "page", "excerpt"))
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def human_status(r: dict[str, str]) -> str:
    """'agree' / 'disagree' / 'unsure' when a sign-off matches the current row, else ''."""
    m = re.match(r"^(agree|disagree|unsure) \S+ fp=(\w+)$", r.get("human_checked", ""))
    return m.group(1) if m and m.group(2) == fingerprint(r) else ""


# ----------------------------------------------------------------- poppler

def poppler_available() -> bool:
    return bool(shutil.which("pdftoppm") and shutil.which("pdftotext"))


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def page_words(pdf: Path, page: int) -> tuple[float, float, list[Word]]:
    """(width pt, height pt, words with boxes) of one page via ``pdftotext -bbox``."""
    out = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                         capture_output=True, text=True, check=False).stdout
    pm = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', out)
    if not pm:
        return 0.0, 0.0, []
    words = [Word(html.unescape(m.group(5)), *(float(m.group(i)) for i in range(1, 5)))
             for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" '
                                  r'yMax="([\d.]+)">(.*?)</word>', out)]
    return float(pm.group(1)), float(pm.group(2)), words


def render_page(pdf: Path, page: int, dest: Path) -> bool:
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    stem = dest.with_suffix("")
    subprocess.run(["pdftoppm", "-png", "-r", str(DPI), "-f", str(page), "-l", str(page),
                    "-singlefile", str(pdf), str(stem)], capture_output=True, check=False)
    return dest.exists()


def match_words(words: list[Word], excerpt: str) -> list[Word]:
    """The words of ``excerpt`` (each '...'-separated part as a contiguous run) on the page."""
    toks: list[tuple[str, list[int]]] = []  # folded token, indices of the words it came from
    i = 0
    while i < len(words):  # join end-of-line hyphenation: "recog-" + "nition"
        w, idx = words[i].text, [i]
        while w.endswith("-") and i + 1 < len(words):
            i += 1
            w, idx = w[:-1] + words[i].text, [*idx, i]
        toks.extend((t, idx) for t in fold(w).split())
        i += 1
    flat = [t for t, _ in toks]
    hits: set[int] = set()
    pos = 0
    for part in _parts(excerpt):
        pt = part.split()
        found = next((s for s in range(pos, len(flat) - len(pt) + 1)
                      if flat[s : s + len(pt)] == pt), -1)
        if found < 0:
            return []
        for k in range(found, found + len(pt)):
            hits.update(toks[k][1])
        pos = found + len(pt)
    return [words[i] for i in sorted(hits)]


# ----------------------------------------------------------------- cards

@dataclass
class Card:
    row: dict[str, str]
    image: str = ""  # path relative to the HTML
    img_w: int = 0
    img_h: int = 0
    boxes: list[tuple[int, int, int, int]] | None = None
    crop: tuple[int, int] | None = None  # (top px, height px)
    note: str = ""


def build_card(r: dict[str, str], pdf: Path | None, img_dir: Path) -> Card:
    c = Card(r)
    if not r["pdf_page"].isdigit():
        c.note = "no located passage yet"
        return c
    if pdf is None:
        c.note = "PDF not available on this machine"
        return c
    if not poppler_available():
        c.note = "page image needs poppler (pdftoppm, pdftotext) on PATH"
        return c
    n = int(r["pdf_page"])
    name = f"{re.sub(r'[^A-Za-z0-9_.-]', '_', r['key'])}-p{n}.png"
    if not render_page(pdf, n, img_dir / name):
        c.note = "page could not be rendered"
        return c
    c.image = f"img/{name}"
    w_pt, h_pt, words = page_words(pdf, n)
    scale = DPI / 72.0
    c.img_w, c.img_h = round(w_pt * scale), round(h_pt * scale)
    hits = match_words(words, r["excerpt"]) if words else []
    if hits:
        c.boxes = [(round(w.x0 * scale), round(w.y0 * scale), round((w.x1 - w.x0) * scale),
                    round((w.y1 - w.y0) * scale)) for w in hits]
        top = max(0, min(b[1] for b in c.boxes) - 60)
        bottom = min(c.img_h, max(b[1] + b[3] for b in c.boxes) + 60)
        c.crop = (top, bottom - top)
    elif r["located"] == "scan" or not words:
        c.note = "image-only page: whole page shown; find the passage by eye"
    else:
        c.note = "passage words not matched on the rendered page; whole page shown"
    return c


def _mark_cite(sentence: str, key: str) -> str:
    e = html.escape(sentence)
    pat = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})*")
    out, last = [], 0
    for m in pat.finditer(e):
        if re.search(r"[{,]\s*" + re.escape(key) + r"\s*[,}]", m.group(0)):
            out += [e[last : m.start()], f"<mark>{m.group(0)}</mark>"]
            last = m.end()
    return "".join(out) + e[last:]


def _card_html(c: Card) -> str:
    r, e = c.row, html.escape
    verdict = r["verdict"]
    prev = human_status(r)
    img = ""
    if c.image and c.crop:
        boxes = "".join(f'<span class="hl" style="left:{x}px;top:{y}px;width:{w}px;'
                        f'height:{h}px"></span>' for x, y, w, h in c.boxes or [])
        img = (f'<div class="crop" style="height:{c.crop[1]}px"><div class="page" '
               f'style="width:{c.img_w}px;height:{c.img_h}px;top:-{c.crop[0]}px">'
               f'<img src="{e(c.image)}" width="{c.img_w}" height="{c.img_h}" alt="PDF page '
               f'{e(r["pdf_page"])} of {e(r["key"])}" loading="lazy">{boxes}</div></div>'
               f'<a class="mute" href="{e(c.image)}">whole page</a>')
    elif c.image:
        img = (f'<a href="{e(c.image)}"><img class="full" src="{e(c.image)}" loading="lazy" '
               f'alt="PDF page {e(r["pdf_page"])} of {e(r["key"])}"></a>')
    note = f'<p class="mute">{e(c.note)}</p>' if c.note else ""
    passage = (f'<blockquote>{e(r["excerpt"])}</blockquote><p class="mute">printed page '
               f'{e(r["page"])}, PDF page {e(r["pdf_page"])}'
               + (" (transcribed from a scan)" if r["located"] == "scan" else "") + "</p>"
               if r["excerpt"] else '<p class="mute">No passage recorded.</p>')
    differs = (f'<p><strong>What differs:</strong> {e(r["what_differs"])}</p>'
               if r["what_differs"] else "")
    decision = (f'<p><strong>Decision:</strong> {e(r["decision"])}</p>' if r["decision"]
                else "")
    btns = "".join(f'<button type="button" data-a="{a}">{a.capitalize()}</button>'
                   for a in ANSWERS)
    return (
        f'<article class="card" data-cid="{e(r["cid"])}" data-verdict="{e(verdict)}" '
        f'data-key="{e(r["key"])}" data-fp="{fingerprint(r)}" data-ledger="{prev}">'
        f'<header><code>{e(r["cid"])}</code> <span class="v v-{e(verdict)}">{e(verdict)}</span>'
        f' <span class="mute">{e(r["key"])} &middot; {e(r["file"])}:{e(r["line"])}</span>'
        f'</header>'
        f'<div class="grid"><section><h3>Your sentence</h3><p class="sent">'
        f'{_mark_cite(r["sentence"], r["key"])}</p>{differs}{decision}'
        f'<p class="mute">verdict by {e(r["checked_by"] or "nobody yet")}'
        f'{" on " + e(r["checked_at"]) if r["checked_at"] else ""}'
        f'{"; ledger sign-off: " + prev if prev else ""}</p></section>'
        f'<section><h3>The source</h3>{passage}{img}{note}</section></div>'
        f'<footer><span class="btns">{btns}</span>'
        f'<input type="text" class="note" placeholder="note (optional)" '
        f'aria-label="note on {e(r["cid"])}"></footer></article>')


CSS = """
:root { --fg:#1b1b1b; --bg:#f6f6f4; --card:#fff; --mute:#666; --line:#ddd; --mark:#ffe36e;
        --hl:rgba(255,200,0,.38); --agree:#1f6f3a; --dis:#a3261b; --uns:#8a6d00;
        --accent:#1d4f91; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e8; --bg:#121212; --card:#1d1d1d; --mute:#9a9a9a; --line:#333;
          --mark:#6b5a00; --hl:rgba(255,210,0,.33); --agree:#7fd49a; --dis:#ff8a7a;
          --uns:#e8c75a; --accent:#8db8ff; } }
* { box-sizing: border-box; }
body { font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg);
       margin: 0; padding: 0 16px 4rem; }
main, .bar { max-width: 76rem; margin: 0 auto; }
h1 { margin: 1.5rem 0 .3rem; } h3 { margin: 0 0 .3rem; font-size: .85rem;
  text-transform: uppercase; letter-spacing: .04em; color: var(--mute); }
.bar { position: sticky; top: 0; background: var(--bg); padding: .6rem 0; z-index: 2;
       display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: center;
       border-bottom: 1px solid var(--line); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        margin: 1rem 0; padding: .8rem 1rem; }
.card header { margin-bottom: .5rem; overflow-wrap: anywhere; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
.sent { font-family: Georgia, serif; font-size: 1.02rem; overflow-wrap: anywhere; }
mark { background: var(--mark); color: inherit; }
blockquote { margin: 0; padding-left: .8rem; border-left: 3px solid var(--accent);
             font-family: Georgia, serif; }
.mute { color: var(--mute); font-size: .88rem; }
.crop { position: relative; overflow: auto; max-width: 100%; border: 1px solid var(--line);
        margin-top: .5rem; background: #fff; }
.page { position: relative; }
.page img { display: block; }
.hl { position: absolute; background: var(--hl); mix-blend-mode: multiply; }
img.full { max-width: 100%; border: 1px solid var(--line); margin-top: .5rem; }
.v { font-weight: 600; font-size: .85rem; }
.v-SUPPORTED { color: var(--agree); } .v-UNCHECKED, .v-NOT_FOUND { color: var(--dis); }
.v-PARTLY, .v-WRONG_VERSION, .v-NO_ACCESS { color: var(--uns); }
footer { margin-top: .7rem; display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
button { font: inherit; padding: .25rem .8rem; border-radius: 6px; cursor: pointer;
         border: 1px solid var(--line); background: var(--bg); color: var(--fg); }
button[aria-pressed=true][data-a=agree] { background: var(--agree); color: var(--card); }
button[aria-pressed=true][data-a=disagree] { background: var(--dis); color: var(--card); }
button[aria-pressed=true][data-a=unsure] { background: var(--uns); color: var(--card); }
.note { flex: 1 1 14rem; font: inherit; padding: .25rem .5rem; border-radius: 6px;
        border: 1px solid var(--line); background: var(--bg); color: var(--fg); }
select, label { font: inherit; color: var(--fg); }
select { background: var(--card); border: 1px solid var(--line); border-radius: 6px; }
.hidden { display: none; }
"""

JS = """
(function () {
  var STORE = 'litverify-review:' + document.body.dataset.project;
  var state = {};
  try { state = JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (e) { state = {}; }
  function save() { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) {} }
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  function paint(card) {
    var s = state[card.dataset.cid];
    var valid = s && s.fp === card.dataset.fp;
    card.querySelectorAll('button').forEach(function (b) {
      b.setAttribute('aria-pressed', valid && s.answer === b.dataset.a ? 'true' : 'false');
    });
    if (valid) card.querySelector('.note').value = s.note || '';
  }
  cards.forEach(function (card) {
    paint(card);
    card.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        var cur = state[card.dataset.cid] || {};
        state[card.dataset.cid] = { answer: b.dataset.a, note: cur.note || '',
          fp: card.dataset.fp, at: new Date().toISOString().slice(0, 10) };
        save(); paint(card); count();
      });
    });
    card.querySelector('.note').addEventListener('input', function (ev) {
      var s = state[card.dataset.cid];
      if (s && s.fp === card.dataset.fp) { s.note = ev.target.value; save(); }
    });
  });
  var fv = document.getElementById('f-verdict'), fk = document.getElementById('f-key'),
      fu = document.getElementById('f-unreviewed');
  function done(card) {
    var s = state[card.dataset.cid];
    return (s && s.fp === card.dataset.fp) || card.dataset.ledger !== '';
  }
  function filter() {
    cards.forEach(function (c) {
      var ok = (!fv.value || c.dataset.verdict === fv.value) &&
               (!fk.value || c.dataset.key === fk.value) && (!fu.checked || !done(c));
      c.classList.toggle('hidden', !ok);
    });
  }
  function count() {
    var n = cards.filter(done).length;
    document.getElementById('count').textContent = n + ' of ' + cards.length + ' reviewed';
  }
  [fv, fk, fu].forEach(function (el) { el.addEventListener('change', filter); });
  document.getElementById('export').addEventListener('click', function () {
    var out = { format: 'litverify-review/1', project: document.body.dataset.project,
                decisions: {} };
    cards.forEach(function (c) {
      var s = state[c.dataset.cid];
      if (s && s.fp === c.dataset.fp) out.decisions[c.dataset.cid] = s;
    });
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'review-decisions.json';
    document.body.appendChild(a); a.click(); a.remove();
  });
  count();
})();
"""


def to_html(cards: list[Card], project: str) -> str:
    e = html.escape
    verdicts = sorted({c.row["verdict"] for c in cards})
    keys = sorted({c.row["key"] for c in cards})
    opt_v = "".join(f'<option value="{e(v)}">{e(v)}</option>' for v in verdicts)
    opt_k = "".join(f'<option value="{e(k)}">{e(k)}</option>' for k in keys)
    body = "".join(_card_html(c) for c in cards) or "<p>No live citations.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Citation review</title><style>{CSS}</style></head>
<body data-project="{e(project)}">
<main><h1>Citation review</h1>
<p class="mute">For each citation: read your sentence, read the passage, look at the page.
Agree only if the source says what your sentence attributes to it, no more. Answers stay in
this browser until you export them; then run
<code>litverify review --import review-decisions.json</code>.</p></main>
<div class="bar">
<label>Verdict <select id="f-verdict"><option value="">all</option>{opt_v}</select></label>
<label>Source <select id="f-key"><option value="">all</option>{opt_k}</select></label>
<label><input type="checkbox" id="f-unreviewed"> not yet reviewed</label>
<span id="count" class="mute"></span>
<button type="button" id="export">Export decisions</button>
</div>
<main>{body}</main>
<script>{JS}</script>
</body></html>
"""


def write(cfg: Config, rows: list[dict[str, str]], pdf_for) -> Path:
    out = cfg.report_path / "review"
    cards = [build_card(r, pdf_for(r["key"]), out / "img")
             for r in rows if r["status"] == "live"]
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(to_html(cards, cfg.root.name), encoding="utf-8")
    return page


def import_decisions(rows: list[dict[str, str]], data: dict) -> tuple[int, list[str]]:
    """Apply exported answers to matching rows; returns (applied, messages)."""
    if data.get("format") != "litverify-review/1":
        raise ValueError("not a litverify review export (format litverify-review/1)")
    by = {r["cid"]: r for r in rows}
    applied, msgs = 0, []
    for cid, d in sorted(data.get("decisions", {}).items()):
        r = by.get(cid)
        ans = d.get("answer", "")
        if r is None or r["status"] != "live":
            msgs.append(f"{cid}: no live ledger row; skipped")
        elif ans not in ANSWERS:
            msgs.append(f"{cid}: unknown answer {ans!r}; skipped")
        elif d.get("fp") != fingerprint(r):
            msgs.append(f"{cid}: sentence or verdict changed since the page was built; "
                        "review it again")
        else:
            at = re.sub(r"[^\d-]", "", str(d.get("at", ""))) or today()
            r["human_checked"] = f"{ans} {at} fp={fingerprint(r)}"
            r["human_note"] = " ".join(str(d.get("note", "")).split())
            applied += 1
    return applied, msgs


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
