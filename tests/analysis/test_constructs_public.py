"""The Konstrukte page for visitors who are not signed in (Ralf 2026-10-16, option 1): list, search, filters and the
page of each construct, the wording only where its licence allows passing it on, nothing taken from surveys."""
from app import constructs, ratswd


def test_guests_browse_the_toolbox(app, client):
    guest = app.test_client()
    page = guest.get("/constructs/?q=technik")
    assert page.status_code == 200
    html = page.data.decode()
    assert "Kontrollüberzeugung" in html and "Fachgebiet" in html
    assert "pin-form" not in html and "Konstrukt vorschlagen" not in html  # no favourites, no suggestion form
    assert "/auth/sign-in" in html  # where to sign in for the wording and inserting
    assert "Validierte Konstrukte durchsuchen" in guest.get("/").data.decode()  # reachable from the start page


def test_guests_see_the_wording_only_with_an_open_licence(app, client):
    guest = app.test_client()
    closed = guest.get("/constructs/bfi10?lang=en").data.decode()  # GESIS/ZIS: not for passing on to everyone
    assert "Big Five" in closed and "doi.org/10.1016/j.jrp.2006.02.001" in closed  # what it is and its source
    assert "Test statement 1 of BFI10" not in closed and "nur angemeldeten Forschenden" in closed
    assert "Verwendet in" not in closed and "pin-form" not in closed
    assert "GESIS/ZIS: kostenfrei" not in closed  # terms of use under review (copyright audit 2026-10-16): not public
    open_ = guest.get("/constructs/ratswd_ges1").data.decode()  # RatSWD, CC BY 4.0
    assert "Welches Geschlecht haben Sie?" in open_ and "nur angemeldeten Forschenden" not in open_ and "CC BY 4.0" in open_
    assert "nur angemeldeten Forschenden" not in guest.get("/constructs/age").data.decode()  # own wording
    # signed in: every wording as before
    signed_in = client.get("/constructs/bfi10?lang=en").data.decode()
    assert "Test statement 1 of BFI10" in signed_in and "Verwendet in" in signed_in


def test_what_stays_behind_the_sign_in(app, client):
    guest = app.test_client()
    for url in ("/constructs/api/search?q=gesch", "/constructs/bfi10/used.bib", "/constructs/bfi10/pool.csv"):
        assert guest.get(url).status_code in (302, 401), url
    assert guest.post("/constructs/bfi10/favourite").status_code in (302, 400, 401)
    # a survey in the address gives a guest no insert form
    assert "data-insert-form" not in guest.get("/constructs/bfi10?survey=1").data.decode()


def test_open_wordings_are_the_licensed_ones(app):
    with app.app_context():
        assert all(constructs.wording_public(k) for k in ratswd.KEYS)
        assert constructs.wording_public("age") and constructs.wording_public("sorgfalt")
        for key in ("bfi10", "gdms", "tam_pu", "tb", "gender"):  # ZIS, APA PsycTests, journal articles
            assert not constructs.wording_public(key), key


def test_own_scales_under_cc_by_show_their_wording_and_licence():
    """KAMB, FITAI and KUK are released under CC BY 4.0 (Ralf, 2026-10-16)."""
    from app import constructs

    assert constructs.wording_public("kamb") and constructs.wording_public("fitai")
    assert constructs.wording_public("kuk")
    notes = {s["key"]: s["notes"] for s in constructs.SEEDS}
    assert notes["kamb"].startswith("Lizenz: CC BY 4.0") and notes["fitai"].startswith("Lizenz: CC BY 4.0")
    assert notes["kuk"].startswith("Lizenz: CC BY 4.0")


def test_made_up_facets_cost_a_guest_nothing_extra(app, client):
    """Audit 2026-10-22, D-03: every chosen facet ran one more full search with all other facets as filters, so 581
    made-up facets in one address cost 68 s CPU. Only tags that exist count, at most constructs.MAX_TAGS."""
    from sqlalchemy import event

    from app.extensions import db
    from app.models import Tag

    guest = app.test_client()
    with app.app_context():
        real = db.session.scalar(db.select(Tag.slug).where(Tag.facet == "thema").order_by(Tag.slug))
        engine = db.engine
    statements = []

    def count(*_args):
        statements.append(1)

    def run(url):
        statements.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            page = guest.get(url)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert page.status_code == 200
        return len(statements), page.data.decode()

    plain, _ = run("/constructs/?tag=" + real)
    flood, _ = run("/constructs/?tag=" + real + "".join(f"&tag=f{i}:x" for i in range(600)))
    assert flood <= plain + 2  # the lookup of the tags, not a search per made-up facet
    with app.app_context():
        assert constructs.clean_tags(["f1:x", real, real, "", "x" * 81]) == [real]
        assert len(constructs.clean_tags([f"f{i}:x" for i in range(1000)])) == 0
        assert constructs.search("", ["f1:x"]) == constructs.search()  # an unknown tag filters nothing
