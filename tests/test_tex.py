from literature_verifier.tex import anchor_base, scan_text


def keys(cites):
    return [(c.key, c.line, c.command, c.prenote, c.postnote) for c in cites]


def test_cite_variants_and_notes():
    tex = (
        "A \\cite{a}. B \\citep*{b1, b2}. C \\parencite[12]{c}.\n"
        "D \\textcite[see][ch.~2]{d}. E \\autocite{e}. F \\citeauthor{f}.\n"
    )
    assert keys(scan_text(tex, "x.tex")) == [
        ("a", 1, "cite", "", ""),
        ("b1", 1, "citep", "", ""),
        ("b2", 1, "citep", "", ""),
        ("c", 1, "parencite", "", "12"),
        ("d", 2, "textcite", "see", "ch.~2"),
        ("e", 2, "autocite", "", ""),
        ("f", 2, "citeauthor", "", ""),
    ]


def test_multicite_and_nocite_and_comments():
    tex = (
        "% \\cite{commented}\n"
        "Text 50\\% done \\cites[3]{a}[5]{b}. \\nocite{*}\\nocite{hidden}\n"
        "More \\parencites(global)(post)[see][4]{c}{d}. % \\cite{also}\n"
    )
    got = keys(scan_text(tex, "x.tex"))
    assert got == [
        ("a", 2, "cites", "", "3"),
        ("b", 2, "cites", "", "5"),
        ("c", 3, "parencites", "see", "4"),
        ("d", 3, "parencites", "", ""),
    ]


def test_citation_spanning_lines():
    tex = "Claim here \\parencite[p.~4]{\n  alpha,\n  beta}.\n"
    got = keys(scan_text(tex, "x.tex"))
    assert [k[0] for k in got] == ["alpha", "beta"]
    assert all(k[1] == 1 for k in got)


def test_sentence_extraction_handles_abbreviations_and_cite_periods():
    tex = (
        "\\section{Intro}\n"
        "First sentence. Smith et al. found that reading slows, e.g. on rare words\n"
        "\\parencite[p.~3]{smith}. Next sentence \\cite{other}.\n"
        "\n"
        "New paragraph \\cite{third}\n"
    )
    c = {x.key: x for x in scan_text(tex, "x.tex")}
    assert c["smith"].sentence == (
        "Smith et al. found that reading slows, e.g. on rare words \\parencite[p.~3]{smith}.")
    assert c["other"].sentence == "Next sentence \\cite{other}."
    assert c["third"].sentence == "New paragraph \\cite{third}"


def test_anchor_survives_line_shift_and_counts_duplicates():
    tex = "Same words \\cite{k}.\n"
    a = scan_text(tex, "f.tex")[0]
    b = scan_text("\n\n\n" + tex, "f.tex")[0]
    assert a.anchor == b.anchor == anchor_base("k", a.sentence)
    assert a.line == 1 and b.line == 4
    dup = scan_text(tex + "\n" + tex, "f.tex")
    assert [d.anchor for d in dup] == [a.anchor, a.anchor + "-2"]
