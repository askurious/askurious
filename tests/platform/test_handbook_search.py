"""The handbook's full-text search (app/handbook.py, search_page; Philipp 2026-11-13): a box above every page, results
as sections holding every word, with a piece of text that marks them."""


def test_every_handbook_page_has_the_search_box(app):
    c = app.test_client()
    for path in ("/docs/", "/docs/build", "/docs/dialogs"):
        page = c.get(path).data.decode()
        assert 'action="/docs/search"' in page and 'name="q"' in page, path


def test_a_search_finds_sections_with_every_word(app):
    c = app.test_client()
    page = c.get("/docs/search?q=Gruppe+Designs").data.decode()
    assert '/docs/more#gruppen"' in page and "<mark>" in page
    page = c.get("/docs/search?q=wortdasesnichtgibt").data.decode()
    assert "Keine Abschnitte" in page
    assert "Geben Sie oben" in c.get("/docs/search").data.decode()


def test_the_search_escapes_what_was_typed(app):
    page = app.test_client().get("/docs/search?q=%3Cscript%3Ealert(1)%3C/script%3E").data.decode()
    assert "<script>alert(1)" not in page
