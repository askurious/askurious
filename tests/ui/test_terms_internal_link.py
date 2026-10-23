"""The terms (licence.md) may link a page of this site by its path; "//host" and other schemes stay text (app/terms.py)."""
from app import terms


def test_a_path_from_the_root_is_a_link_in_the_same_tab():
    html = str(terms.render_md("Siehe das [Impressum](/imprint) und [Seite](https://example.org/a)."))
    assert '<a href="/imprint">Impressum</a>' in html
    assert '<a href="https://example.org/a" target="_blank" rel="noopener">Seite</a>' in html


def test_a_link_to_another_host_without_scheme_stays_text():
    html = str(terms.render_md("[bad](//evil.example/x) und [js](javascript:alert(1))"))
    assert "<a " not in html


def test_the_contact_section_links_the_legal_notice(app, client):
    with app.app_context():
        page = terms.render_md(terms.source())
    assert '<a href="/imprint">Impressum</a>' in str(page)
    assert "philipp-brauner.de" not in str(page).split('id="abschnitt-10"')[1]
