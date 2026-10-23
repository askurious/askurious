"""Links are never underlined (app/static/app.css, participant.css), not under the mouse either. Links in
running text are semi-bold instead, so they differ from the text around them by more than their colour
(WCAG 1.4.1)."""
import os
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "static")
SHEETS = ("app.css", "participant.css")


def _rules(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
    css = re.sub(r"@(media|keyframes|supports)[^{]*\{", "", css)
    return [(s.strip(), body) for s, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


@pytest.mark.parametrize("name", SHEETS)
def test_nothing_is_underlined(name):
    found = [s for s, body in _rules(name) if re.search(r"text-decoration(-line)?:\s*underline", body)]
    assert not found, "underlined: " + "; ".join(found)


@pytest.mark.parametrize("name,link,text_link", [
    ("app.css", "a", "a:where(:not([class]), .small)"),
    ("participant.css", ":where(body.participant, .participant-frame) a",
     ":where(body.participant, .participant-frame) a:where(:not([class]))"),
])
def test_text_links_are_plain_and_semi_bold(name, link, text_link):
    rules = dict(_rules(name))
    assert "text-decoration: none" in rules[link]
    assert "color: var(--accent)" in rules[link]
    assert "font-weight: 600" in rules[text_link]


def test_no_back_link_without_the_shared_style():
    """A link back ("← ...") written as a plain link looks different from all the others."""
    templates = os.path.join(os.path.dirname(STATIC), "templates")
    found = []
    for folder, _, files in os.walk(templates):
        if os.path.basename(folder) == "participant" or "items" in folder:
            continue
        for file in files:
            with open(os.path.join(folder, file), encoding="utf-8") as f:
                if re.search(r"<a (?![^>]*class=)[^>]*>\s*←", f.read()):
                    found.append(file)
    assert not found, "use class=\"back-link\" with icon(\"back\"): " + ", ".join(found)
