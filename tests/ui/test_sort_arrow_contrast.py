"""The arrow of an unsorted column (UI audit 2026-10-12, U51)."""


def test_the_unsorted_arrow_has_the_contrast_of_muted_text():
    """UI audit 2026-10-12, U51: the arrow of an unsorted column shows a state, so --muted (at least 4.5:1)."""
    css = open("app/static/app.css", encoding="utf-8").read()
    assert ".sort-arrow.is-off { color: var(--muted);" in css
