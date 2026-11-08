# Minimal example

Three open-access articles, cited five times in `main.tex`. The ledger (`citations.csv`)
and the metadata audit (`metadata.csv`) are committed fully verified: every citation has a
printed page and a verbatim excerpt that `litverify` located in the PDF. The PDFs are not
committed.

```bash
cd examples/minimal
litverify check --ledger-only   # passes: trusts the recorded PDFs and excerpts
litverify check                 # fails with R3 for every citation: no PDFs on your disk
```

Get the PDFs (all CC BY 4.0) and save them in `pdfs/` under their citation keys:

| File | Article |
|---|---|
| `pdfs/harris2020array.pdf` | https://doi.org/10.1038/s41586-020-2649-2 |
| `pdfs/virtanen2020scipy.pdf` | https://doi.org/10.1038/s41592-019-0686-2 |
| `pdfs/wilkinson2016fair.pdf` | https://doi.org/10.1038/sdata.2016.18 |

Or set `unpaywall_email` in `litverify.toml` and run `litverify obtain --download`.
Then `litverify check` re-reads every excerpt in the PDFs and passes, and `litverify
review` builds the page where you look at each passage on its page.

## Things to try

- Strengthen a sentence, e.g. "NumPy is the only array programming library for Python".
  `litverify sync` keeps the row but marks it stale; `check` fails with R6 until the
  citation is verified again, and the source will not support "only".
- Tamper with an excerpt in `citations.csv`. `check` fails with R4: the excerpt is no
  longer on the recorded page.
- Change the year of an entry in `references.bib` and run `litverify meta`: FIX.

## A lesson from building this example

A first pass recorded C0003 ("Both libraries are described in peer-reviewed articles") as
PARTLY, because the first page of the NumPy article does not mention peer review. The
statement is on the last page (printed p. 362, "Peer review information"). A verdict rests
on the whole source, read in context, not on the first page that comes up; that is what the
adversarial round in `.claude/skills/verify-citations` is for.
