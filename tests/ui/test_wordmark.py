"""The wordmark (copyright audit 2026-10-22, R5/L13): "Askurious" in PT Serif Bold as outlines (static/img/wordmark.svg)
instead of text in "Iowan Old Style", a commercial font that showed only where it was installed. Another brand keeps
its name as text."""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_sprite_holds_both_marks_and_names_its_font():
    svg = _read("app", "static", "img", "wordmark.svg")
    assert '<symbol id="askurious" viewBox="0 -1039 ' in svg and '<symbol id="askurious-de" viewBox="0 -1039 ' in svg
    assert "PT Serif Bold" in svg and "SIL Open Font" in svg
    assert svg.count('fill="currentColor"') == 2           # the colour of the text around it, in every theme
    assert "<text" not in svg and "font-family" not in svg  # outlines only, no font needed


def test_the_macro_sizes_like_the_sprite():
    """The outer viewBox starts at 0 (the symbol's own starts at the ascender), and its widths are the symbols'."""
    svg = _read("app", "static", "img", "wordmark.svg")
    widths = dict(re.findall(r'<symbol id="([^"]+)" viewBox="0 -1039 (\d+) 1325"', svg))
    macro = _read("app", "templates", "_wordmark.html")
    assert f"viewBox=\"0 0 {{{{ {widths['askurious-de']} if with_domain else {widths['askurious']} }}}} 1325\"" in macro


def test_header_footer_and_landing_page_show_the_wordmark(client):
    html = client.get("/").data.decode()
    assert html.count('class="wordmark ') >= 3               # header, landing page, footer
    assert re.search(r'<svg class="wordmark " viewBox="0 0 4817 1325" role="img" aria-label="Askurious"><use href="/static/img/wordmark.svg[^"]*#askurious"/>', html)
    assert 'aria-label="Askurious.de"' in html and "#askurious-de" in html


def test_another_brand_keeps_its_name_as_text(app):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="example.org=Beispielmarke")
    html = app.test_client().get("/", headers={"Host": "example.org"}).data.decode()
    assert 'class="wordmark' not in html
    assert "Beispielmarke.de" in html


def test_no_logo_in_the_commercial_font_any_more():
    for css in ("app.css", "participant.css"):
        assert ".wordmark {" in _read("app", "static", css)
    for template in (("base.html",), ("participant", "base.html"), ("boards", "view.html"), ("status", "index.html")):
        page = _read("app", "templates", *template)
        assert ">Askurious.de<" not in page and ">Askurious<" not in page
