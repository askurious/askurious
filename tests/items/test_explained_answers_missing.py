"""Code review 2026-10-15, F4: MaxDiff, paired comparison and ACBC store the mark of their explanation page
({"explained": True}) together with the sets; the mark alone asks nothing, but stored with the sets it must not hide
a set left open (quality control counts the sets skipped through is_missing), as conjoint already did."""
from app import items


def test_the_explanation_mark_does_not_hide_open_sets(app):
    with app.app_context():
        for key in ("maxdiff", "paired", "conjoint"):
            kind = items.get(key)
            assert kind.is_missing(None, {"explained": True}) is False, key   # the explanation page alone
            assert kind.is_missing(None, {"explained": True, "t0": None, "t1": None}) is True, key
            assert kind.is_missing(None, {"explained": True, "t0": None, "t1": {"b": 0, "w": 1}}) is False, key
        acbc = items.get("acbc")
        assert acbc.is_missing(None, {"explained": True}) is False
        assert acbc.is_missing(None, {"explained": True, "s": {"0": None}}) is True
        assert acbc.is_missing(None, {"explained": True, "s": {"0": 1}}) is False
