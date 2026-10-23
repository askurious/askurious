"""diary.clean() promised to handle block parts it never read (security and code audit 2026-10-13, CF-4); that is
fix_parts()'s work, which the callers run once the blocks exist."""
import inspect

from app import diary


def test_clean_takes_only_the_schedule():
    assert list(inspect.signature(diary.clean).parameters) == ["sampling"]
    assert diary.clean("kein Plan") is None
