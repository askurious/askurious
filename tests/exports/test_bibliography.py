"""References of the constructs as BibTeX and RIS (app/bibliography.py)."""
from types import SimpleNamespace

from app import bibliography, constructs


def _c(key):
    seed = next(s for s in constructs.SEEDS if s["key"] == key)
    return SimpleNamespace(citation=seed["citation"], doi=seed["doi"])


def test_every_seed_citation_is_read_completely():
    # a new construct whose citation does not parse ends here: every reference needs authors, year, title and a
    # known kind of source (no "misc" with the text as a note)
    for seed in constructs.SEEDS:
        if not seed["citation"]:
            continue
        for e in bibliography.entries(SimpleNamespace(citation=seed["citation"], doi=seed["doi"])):
            assert e["authors"] and e["year"] and e["title"] and e["kind"] != "misc", (seed["key"], e)


def test_several_references_split_and_the_doi_goes_where_it_belongs():
    bfi = bibliography.entries(_c("bfi10"))
    assert [(e["kind"], e["year"], e.get("doi")) for e in bfi] == [
        ("article", 2007, "10.1016/j.jrp.2006.02.001"), ("zis", 2014, "10.6102/zis76")]
    assert bfi[1]["authors"][-1] == ("Kovaleva", "A.") and bfi[0]["issue"] == "1" and bfi[0]["pages"] == "203–212"
    tb = bibliography.entries(_c("tb"))                    # the ZIS DOI belongs to the documentation, not the paper
    assert [(e["kind"], e.get("doi")) for e in tb] == [("article", None), ("zis", "10.6102/zis244")]
    careful = bibliography.entries(_c("sorgfalt"))
    assert [e["authors"][0][0] for e in careful] == ["Meade", "Aust"]
    assert [e["doi"] for e in careful] == ["10.1037/a0028085", "10.3758/s13428-012-0265-2"]
    chapter = bibliography.entries(_c("kuk"))[0]
    assert chapter["kind"] == "chapter" and chapter["publisher"] == "Springer" and chapter["pages"] == "453–471"
    assert bibliography.entries(_c("kamb"))[0]["eprint"] == "2602.12785"
    assert "doi = {10.1007/978-3-319-22701-6_34}" in bibliography.bibtex([chapter])     # a DOI stays verbatim


def test_bibtex_and_ris():
    items = bibliography.collect([_c("tam_pu"), _c("tam_peou"), _c("bfi10"), _c("fitai")])
    assert len(items) == 4                                 # the TAM paper once
    bib = bibliography.bibtex(items)
    assert "@article{Davis1989," in bib and "author = {Rammstedt, B. and John, O. P.}" in bib
    assert "@misc{Rammstedt2014," in bib and "pages = {203--212}" in bib and "doi = {10.6102/zis76}" in bib
    assert "title = {{Foil \\& Target Instrument for AI Literacy (FITAI)}}" in bib and "@unpublished{Brauner2026," in bib
    ris = bibliography.ris(items)
    records = ris.split("ER  - ")
    assert len(records) == 5 and ris.startswith("TY  - JOUR\r\nAU  - Davis, F. D.\r\nPY  - 1989")
    assert "SP  - 203\r\nEP  - 212" in ris and "TY  - DATA" in ris and "PB  - Zusammenstellung" in ris


def test_keys_are_unique():
    same = [{"authors": [("Kovaleva", "A.")], "year": 2014}] * 3 + [{"authors": [("Beißert", "H.")], "year": 2015}]
    assert bibliography._keys(same) == ["Kovaleva2014a", "Kovaleva2014b", "Kovaleva2014c", "Beissert2015"]


def test_downloads_for_a_survey_and_a_construct(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})
    bib = client.get("/surveys/1/export/references.bib")
    assert bib.status_code == 200 and bib.mimetype == "application/x-bibtex"
    text = bib.data.decode()
    assert text.count("@") == 3 and "Beierlein2014" in text
    ris = client.get("/surveys/1/export/references.ris").data.decode()
    assert ris.count("TY  - ") == 3
    assert client.get("/surveys/1/export/references.txt").status_code == 404
    html = client.get("/surveys/1/data").data.decode()
    assert "Literatur der Konstrukte (BibTeX)" in html and "/export/references.ris" in html
    one = client.get("/constructs/bfi10/source.ris")
    assert one.status_code == 200 and one.data.decode().count("TY  - ") == 2
    assert "/constructs/bfi10/source.bib" in client.get("/constructs/bfi10").data.decode()
    assert client.get("/constructs/age/source.bib").status_code == 404                # no source to cite
