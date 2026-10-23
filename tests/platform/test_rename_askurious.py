"""Names from before the rename to Askurious (Ralf, 2026-10-09) still work: stored themes and styles
(survey files of before: tests/exports/test_transfer.py, "queryous-survey")."""
from app import display, theme


def test_stored_themes_and_styles_of_before_still_work():
    assert theme.clean({"preset": "queryous-dark", "colors": theme.BUILTIN["askurious-dark"]["colors"]})["preset"] \
        == "askurious-dark"                                                  # recognised by its colours
    assert theme.clean({"preset": "queryous"}) is None                          # the standard look
    assert display.clean({"look": "queryous"})["look"] == "askurious"

