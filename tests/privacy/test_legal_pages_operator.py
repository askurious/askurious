"""The legal pages of the public code name no operator of their own (Ralf, 2026-10-19): imprint, controller, legal
basis, supervisory authority, the accessibility statements and section 1 of the terms say "the operator of this
installation". An installation puts its own paragraphs into instance_overlay/templates/ (instance_overlay/README.md;
the RWTH installation: askurious-instance-rwth), and they take the place of these partials word for word."""
from pathlib import Path

from jinja2 import ChoiceLoader, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2]
OPERATOR_WORDS = ("RWTH", "Lehrstuhl für Kommunikationswissenschaft", "Chair of Communication Science", "dsb@rwth-aachen",
                  "NRW", "Nordrhein-Westfalen", "North Rhine-Westphalia", "Templergraben", "121689807")
PARTIALS = ("public/_operator_imprint.html", "public/_operator_imprint_en.html", "public/_owner.html",
            "public/_operator_legal_basis.html", "public/_operator_authority.html", "public/_operator_a11y.html",
            "public/_operator_enforcement.html", "participant/_operator.html", "participant/_operator_a11y.html",
            "participant/_operator_enforcement.html", "participant/_controller.html", "participant/_dpo.html",
            "participant/_authority.html")


def _uid(app):
    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        return db.session.get(Survey, 1).public_key


def _pages(app, client, uid):
    guest = app.test_client()
    return {"imprint de": guest.get("/imprint", headers={"Accept-Language": "de"}).data.decode(),
            "imprint en": guest.get("/imprint", headers={"Accept-Language": "en"}).data.decode(),
            "accessibility": guest.get("/accessibility").data.decode(),
            "survey privacy": client.get(f"/s/{uid}/privacy").data.decode(),
            "survey accessibility": client.get(f"/s/{uid}/accessibility").data.decode()}


def _main(html):
    return html.split("<main", 1)[1].split("</main>", 1)[0]


def test_legal_pages_name_no_operator_of_their_own(app, client, survey):
    app.config.update(IMPRINT_NAME="Muster-Institut", IMPRINT_ADDRESS=["Musterstraße 1", "12345 Musterstadt"])
    pages = _pages(app, client, _uid(app))
    for name, html in pages.items():
        for word in OPERATOR_WORDS:
            assert word not in _main(html), (name, word)
    de, en = pages["imprint de"], pages["imprint en"]
    assert "Diese Installation von Askurious wird betrieben" in de and "Betreiber dieser Installation:" in de
    assert "Muster-Institut<br>Musterstraße 1<br>12345 Musterstadt" in de
    for words, page in ((("Auskunft (Art. 15 DSGVO)", "Art. 77 DSGVO", "Art. 22 DSGVO", "Art. 6 Abs. 1 lit. f"), de),
                        (("right of access (Art. 15 GDPR)", "Art. 77 GDPR", "Art. 22 GDPR"), en)):
        assert all(w in page for w in words), words
    assert "This installation of Askurious is operated by" in en
    assert "EN 301 549" in pages["accessibility"] and "Durchsetzungsverfahren" not in pages["accessibility"]
    assert "Der Betreiber dieser Installation von Askurious" in pages["survey accessibility"]


def test_terms_name_the_operator_of_the_installation(app):
    from app import terms

    licence = (ROOT / "licence.md").read_text(encoding="utf-8")
    section = licence[licence.index("## 1. Betreiber"):licence.index("## 2.")]
    assert "<!-- operator -->" in section and not any(w in section for w in OPERATOR_WORDS)
    with app.test_request_context():
        text = terms.source()
    assert "<!-- operator" not in text and "die das [Impressum](/imprint) nennt" in text
    english = (ROOT / "app/templates/terms_en.html").read_text(encoding="utf-8")
    assert '{% include "terms/_operator_en.html" %}' in english and "RWTH" not in english


def test_an_installation_brings_its_own_paragraphs(app, client, survey, tmp_path):
    """Each operator partial and the terms' sentence come from the installation's overlay when it has them."""
    from app import terms

    folder = tmp_path / "templates"
    for part in PARTIALS + ("terms/_operator_en.html",):
        (folder / part).parent.mkdir(parents=True, exist_ok=True)
        (folder / part).write_text(f"<span>OVERLAY {part}</span>", encoding="utf-8")
    (folder / "terms" / "_operator.md").write_text("Betreiber ist das Muster-Institut.", encoding="utf-8")
    app.config.update(INSTANCE_OVERLAY=str(tmp_path), IMPRINT_ADDRESS=["Musterstraße 1"])
    app.jinja_env.loader = ChoiceLoader([FileSystemLoader(str(folder)), app.jinja_env.loader])
    app.jinja_env.cache.clear()
    pages = _pages(app, client, _uid(app))
    shown = {part for part in PARTIALS for html in pages.values() if f"OVERLAY {part}" in html}
    assert shown == set(PARTIALS)
    with app.test_request_context():
        assert "Betreiber ist das Muster-Institut." in terms.source() and "<!-- operator" not in terms.source()
