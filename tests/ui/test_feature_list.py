"""The public list of all features, /listofallfeatures (app/feature_list.py, Philipp 2026-10-14): German and English,
experimental features marked with a footnote, and every experimental feature on it."""
import re

from app import experimental


def _keys(app):
    from app import feature_list

    with app.test_request_context():
        return [key for _anchor, _title, items in feature_list.groups() for _text, key in items if key]


def test_every_experimental_feature_is_listed_and_no_other_key(app):
    """A new experimental feature gets a row; one that leaves the group loses its mark here too."""
    keys = _keys(app)
    assert set(experimental.KEYS) <= set(keys)
    assert set(keys) <= set(experimental.KEYS), "a key no longer experimental: take it from app/feature_list.py"


def test_page_for_guests_in_german_and_english(app):
    guest = app.test_client()
    html = guest.get("/listofallfeatures").data.decode()
    assert "<h1>Alle Funktionen</h1>" in html and "Ablauf und Experimente" in html
    marks = html.count('class="badge badge-exp">experimentell<sup>*</sup>')
    assert marks == len(_keys(app))
    assert "auf begründete Anfrage verfügbar" in html
    guest.post("/language", data={"lang": "en", "next": "/"})
    html = guest.get("/listofallfeatures").data.decode()
    assert "<h1>All features</h1>" in html and "Flow and experiments" in html
    assert "available on reasonable request" in html and "experimental<sup>*</sup>" in html


def test_anchors_unique_and_linked_from_landing_page_and_sitemap(app, client):
    from app import feature_list

    with app.test_request_context():
        anchors = [anchor for anchor, _title, _items in feature_list.groups()]
    assert len(anchors) == len(set(anchors)) and all(re.fullmatch(r"[a-z]+", a) for a in anchors)
    home = client.get("/").data.decode()
    assert '<p class="landing-all-features"><a href="/listofallfeatures">' in home  # below "Was Askurious kann"
    assert home.count('href="/listofallfeatures"') == 1  # not in the footer: no room there (Philipp, 2026-10-14)
    assert "/listofallfeatures</loc>" in client.get("/sitemap.xml").data.decode()


def test_list_opens_for_a_signed_in_researcher(client):
    """Signed in, the header calls experimental() (base.html): the page must not hide it under a value of the same
    name (Philipp, 2026-10-14: TypeError "'set' object is not callable")."""
    r = client.get("/listofallfeatures")
    assert r.status_code == 200 and "badge-exp" in r.data.decode()
