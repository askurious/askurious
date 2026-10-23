"""Publications that used a toolbox construct (app/citations.py): entered for a survey or by the operator, listed on
the construct's page with BibTeX/RIS, Zenodo data sets included, constructs used together."""
from tests.instrument_permissions import grant_instrument
import json

import pytest

from app import citations
from app.extensions import db
from app.models import Construct, ConstructCitation, Survey
from tests.conftest import add

META = {"title": "Vertrauen in Roboter", "authors": [["Muster", "Anna"], ["Beispiel", "Bert"]], "year": 2025,
        "journal": "Zeitschrift für Tests", "kind": "article", "volume": "3", "issue": "2", "pages": "1-10"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    asked = []

    def fake(doi):
        asked.append(doi)
        return dict(META) if doi == "10.1234/abc" else {}
    monkeypatch.setattr(citations, "fetch", fake)
    return asked


def test_survey_publication_counts_for_its_constructs(app, client, survey, no_network):
    grant_instrument(client.application, "kut")
    add(client, "construct:kut")
    add(client, "construct:bfi10")
    page = client.get("/surveys/1/data").get_data(as_text=True)
    assert 'id="publications"' in page and "den 2 Konstrukten" in page
    r = client.post("/surveys/1/publications", data={"action": "add", "doi": "keine doi"}, follow_redirects=True)
    assert "sieht nicht richtig aus" in r.get_data(as_text=True)
    r = client.post("/surveys/1/publications", data={"action": "add", "doi": "https://doi.org/10.1234/abc"},
                    follow_redirects=True)
    assert "den 2 Konstrukten" in r.get_data(as_text=True) and no_network == ["10.1234/abc"]
    assert "Vertrauen in Roboter" in client.get("/surveys/1/data").get_data(as_text=True)
    detail = client.get("/constructs/kut").get_data(as_text=True)
    assert "Verwendet in" in detail and "Muster, Beispiel (2025). Vertrauen in Roboter" in detail and "doi:10.1234/abc" in detail
    assert "Big Five Inventory (BFI-10)" in detail  # used together with it
    bib = client.get("/constructs/kut/used.bib").get_data(as_text=True)
    assert "@article{" in bib and "doi = {10.1234/abc}" in bib and "Muster, Anna and Beispiel, Bert" in bib
    assert "DO  - 10.1234/abc" in client.get("/constructs/kut/used.ris").get_data(as_text=True)
    assert "verwendet in 1 Veröffentlichung" in client.get("/constructs/").get_data(as_text=True)
    client.post("/surveys/1/publications", data={"action": "remove", "doi": "10.1234/abc"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ConstructCitation.id))) == 0
    assert client.get("/constructs/kut/used.bib").status_code == 404


def test_without_toolbox_construct_and_who_may(app, client, survey):
    assert 'id="publications"' not in client.get("/surveys/1/data").get_data(as_text=True)
    r = client.post("/surveys/1/publications", data={"action": "add", "doi": "10.1234/abc"}, follow_redirects=True)
    assert "kein validiertes Konstrukt" in r.get_data(as_text=True)
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.post("/surveys/1/publications", data={"action": "add", "doi": "10.1234/abc"}).status_code in (403, 404)
    assert other.post("/admin/constructs/kut/citations", data={"action": "add", "doi": "10.1234/abc"}).status_code == 403


def test_operator_entry_and_zenodo_data_set(app, client, survey):
    client.post("/admin/constructs/kut/citations", data={"action": "add", "doi": "10.9999/nichts"})
    again = client.post("/admin/constructs/kut/citations", data={"action": "add", "doi": "10.9999/nichts"}, follow_redirects=True)
    assert "schon beim Konstrukt" in again.get_data(as_text=True)
    page = client.get("/admin/constructs/kut").get_data(as_text=True)
    assert "10.9999/nichts" in page and "von einem Admin der Plattform" in page  # W95
    # a data set the survey published on Zenodo counts without an entry
    grant_instrument(client.application, "kut")
    add(client, "construct:kut")
    with app.app_context():
        db.session.get(Survey, 1).zenodo = {"state": "published", "doi": "10.5281/zenodo.42", "target": "zenodo"}
        db.session.commit()
        c = db.session.scalar(db.select(Construct).filter_by(key="kut"))
        assert [e["doi"] for e in citations.listing(c)] == ["10.5281/zenodo.42", "10.9999/nichts"] or \
            sorted(e["doi"] for e in citations.listing(c)) == ["10.5281/zenodo.42", "10.9999/nichts"]
    assert "Zenodo-Datensatz einer Befragung" in client.get("/admin/constructs/kut").get_data(as_text=True)
    client.post("/admin/constructs/kut/citations", data={"action": "remove", "doi": "10.9999/nichts"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ConstructCitation.id))) == 0


def test_fetch_reads_csl_json(monkeypatch):
    monkeypatch.undo()  # the real fetch again, without the autouse fake
    real = citations

    class Answer:
        status_code, headers = 200, {}

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield json.dumps({"type": "article-journal", "title": "Ein Titel", "container-title": ["Eine Zeitschrift"],
                              "author": [{"family": "Muster", "given": "Anna"}], "issued": {"date-parts": [[2024, 5]]},
                              "volume": "7", "page": "11-20"}).encode()

        def close(self):
            pass
    seen = {}

    def get(url, headers, timeout, allow_redirects, stream):
        seen.update(url=url, accept=headers["Accept"], redirects=allow_redirects)
        return Answer()
    monkeypatch.setattr(real.requests, "get", get)
    meta = real.fetch("10.1234/x")
    assert seen == {"url": "https://doi.org/10.1234/x", "accept": "application/vnd.citationstyles.csl+json",
                    "redirects": False}
    assert (meta["title"], meta["journal"], meta["authors"], meta["year"], meta["kind"]) ==         ("Ein Titel", "Eine Zeitschrift", [["Muster", "Anna"]], 2024, "article")

    def broken(url, headers, timeout, **kw):
        raise real.requests.ConnectionError("offline")
    monkeypatch.setattr(real.requests, "get", broken)
    assert real.fetch("10.1234/x") == {}
