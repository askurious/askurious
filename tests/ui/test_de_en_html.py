"""de_en(..., html=True) instead of de_en(...)|safe (audit 2026-10-13, XS-5): the text is HTML, the values are
escaped unless they are Markup themselves."""
import os
import re

from markupsafe import Markup

from app.lang import de_en


def test_values_of_an_html_text_are_escaped(app):
    with app.test_request_context():
        out = de_en("an <strong>{to}</strong>", "to <strong>{to}</strong>", html=True, to="<img src=x>")
        assert isinstance(out, Markup)
        assert str(out) == "an <strong>&lt;img src=x&gt;</strong>"
        assert str(de_en("<b>{n}</b>", "<b>{n}</b>", html=True, n=Markup("<i>3</i>"))) == "<b><i>3</i></b>"
        assert de_en("a {x}", "a {x}", x="<b>") == "a <b>"                       # a plain text stays plain


def test_no_template_puts_values_into_de_en_safe():
    """A value in de_en(...)|safe goes in raw; html=True escapes it."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "app", "templates")
    found = []
    for folder, _, files in os.walk(root):
        for name in files:
            if name.endswith(".html"):
                text = open(os.path.join(folder, name), encoding="utf-8").read()
                for m in re.finditer(r"de_en\((?:[^()]|\([^()]*\))*\)\|safe", text):
                    code = re.sub(r"'[^']*'|\"[^\"]*\"", "''", m.group(0))  # the texts themselves left out
                    if re.search(r",\s*[a-z_]+\s*=", code):
                        found.append(f"{name}: {m.group(0)[:80]}")
    assert not found, found
