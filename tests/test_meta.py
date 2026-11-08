from pathlib import Path

from literature_verifier import meta as M
from literature_verifier.bib import family_names, latex_to_text, parse_bib
from literature_verifier.cli import main


def statuses(project: Path) -> dict[tuple[str, str], str]:
    return {(r["key"], r["field"]): r["status"] for r in M.load(project / "metadata.csv")}


def test_parse_bib_and_names():
    bib = parse_bib('@article{k1,\n title = {{A} {\\"U}ber title},\n'
                    ' author = {M{\\"u}ller, Jan and {Tobii AB} and van der Walt, S. and others},'
                    '\n year = 2020,\n}\n@comment{x,}\n')
    assert bib["k1"]["ENTRYTYPE"] == "article"
    assert latex_to_text(bib["k1"]["title"]) == "A Über title"
    assert family_names(bib["k1"]["author"]) == (["Müller", "Tobii AB", "van der Walt"], True)
    assert bib["k1"]["year"] == "2020"


def test_example_metadata_all_ok(project: Path, web):
    assert main(["-C", str(project), "meta"]) == 0
    st = statuses(project)
    assert len({k for k, _ in st}) == 3
    assert set(st.values()) == {"OK"}
    # generic User-Agent, no e-mail anywhere in what was sent
    for url, headers in web.calls:
        assert "@" not in url and "@" not in headers["User-Agent"]
        assert headers["User-Agent"].startswith("literature-verifier/")


def test_mismatches_are_flagged(project: Path, web):
    bib = project / "references.bib"
    text = bib.read_text()
    text = text.replace("{{Array programming with NumPy}}", "{{Array programming in NumPy}}")
    text = text.replace("year = {2016}", "year = {2015}")
    text = text.replace("Virtanen, Pauli and Gommers, Ralf", "Gommers, Ralf and Virtanen, Pauli")
    text = text.replace("volume = {17}", "volume = {18}")
    text = text.replace("journal = {Scientific Data}", "journal = {Data Science Journal}")
    bib.write_text(text)
    main(["-C", str(project), "meta"])
    st = statuses(project)
    assert st[("harris2020array", "title")] == "FIX"
    assert st[("wilkinson2016fair", "year")] == "FIX"
    assert st[("virtanen2020scipy", "author")] == "FIX"
    assert st[("virtanen2020scipy", "volume")] == "FIX"
    assert st[("wilkinson2016fair", "venue")] == "CHECK"
    assert st[("harris2020array", "venue")] == "OK"


def test_abbreviated_venue_matches_short_container_title(project: Path, web):
    bib = project / "references.bib"
    bib.write_text(bib.read_text().replace("journal = {Scientific Data}", "journal = {Sci. Data}"))
    main(["-C", str(project), "meta"])
    assert statuses(project)[("wilkinson2016fair", "venue")] == "OK"


def test_unresolved_keys(project: Path, web):
    bib = project / "references.bib"
    bib.write_text(bib.read_text().replace("doi = {10.1038/sdata.2016.18},", "")
                   .replace("10.1038/s41586-020-2649-2", "10.9999/does-not-exist"))
    (project / "main.tex").write_text((project / "main.tex").read_text().replace(
        "\\printbibliography", "Ghost \\cite{ghost2024}.\n\\printbibliography"))
    main(["-C", str(project), "meta"])
    rows = {(r["key"], r["field"]): r for r in M.load(project / "metadata.csv")}
    assert rows[("wilkinson2016fair", "doi")]["status"] == "FIX"
    assert "UNRESOLVED: no DOI" in rows[("wilkinson2016fair", "doi")]["detail"]
    assert "not found in Crossref or DataCite" in rows[("harris2020array", "doi")]["detail"]
    assert rows[("ghost2024", "entry")]["status"] == "FIX"


def test_cache_and_offline(project: Path, web):
    main(["-C", str(project), "meta"])
    n = len(web.calls)
    main(["-C", str(project), "meta"])
    assert len(web.calls) == n  # all from cache
    for f in (project / ".litverify" / "cache").glob("*.json"):
        f.unlink()
    main(["-C", str(project), "meta", "--offline"])
    assert len(web.calls) == n
    assert {r["status"] for r in M.load(project / "metadata.csv")} == {"ERROR"}


def test_decisions_survive_reaudit_only_while_values_unchanged(project: Path, web):
    bib = project / "references.bib"
    bib.write_text(bib.read_text().replace("journal = {Scientific Data}",
                                           "journal = {Data Science Journal}"))
    main(["-C", str(project), "meta"])
    assert main(["-C", str(project), "decide", "wilkinson2016fair", "checked: same venue",
                 "--meta", "venue"]) == 0
    main(["-C", str(project), "meta"])
    rows = {(r["key"], r["field"]): r for r in M.load(project / "metadata.csv")}
    assert rows[("wilkinson2016fair", "venue")]["decision"] == "checked: same venue"
    assert M.open_items(list(rows.values())) == []
    bib.write_text(bib.read_text().replace("{Data Science Journal}", "{Data Sci. J.}"))
    main(["-C", str(project), "meta"])
    rows = {(r["key"], r["field"]): r for r in M.load(project / "metadata.csv")}
    assert rows[("wilkinson2016fair", "venue")]["decision"] == ""
