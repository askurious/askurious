"""Likert "labels once" on phones (participant feedback 2026-10-26: item texts ran vertically): seven labels over
columns of about 40 px ran word by word down the screen and pushed the page out to the right. On narrow phones the
setting now lists the labels with each statement, and the head columns never grow past the circles below."""
import os
import re

CSS = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "participant.css")


def _css():
    with open(CSS, encoding="utf-8") as f:
        return f.read()


def _media(css, query):
    """The bodies of all @media blocks with exactly this query."""
    out = []
    for m in re.finditer(r"@media \(" + re.escape(query) + r"\) \{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return "\n".join(out)


def test_once_lists_labels_on_narrow_phones():
    phone = _media(_css(), "max-width: 560px")
    assert ".p-likert-once.p-likert-full .p-likert-head { display: none; }" in phone
    assert ".p-likert-full:is(.p-likert-each, .p-likert-once) .p-scale { grid-template-columns: 1fr;" in phone
    assert ".p-likert-full:is(.p-likert-each, .p-likert-once) .p-point-label" in phone


def test_once_head_columns_follow_the_circles():
    css = _css()
    head = re.search(r"^\.p-likert-once \.p-likert-head \{[^}]*\}", css, re.M).group(0)
    assert "repeat(var(--points), minmax(0, 1fr))" in head
    dk = re.search(r"^\.p-likert-dk\.p-likert-once \.p-likert-head \{[^}]*\}", css, re.M).group(0)
    assert "repeat(var(--points), minmax(0, 1fr))" in dk
