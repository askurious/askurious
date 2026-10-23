"""Operator documents that live only in the private instance repository since the public release (2026-10-23):
deploy/docker/BETRIEB.md and decisions.md. Tests that compare them with the code run where they are present and skip
or leave out that comparison in the public repository."""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def internal_text(*parts):
    """The text of an internal document, or None when this checkout does not have it."""
    path = os.path.join(ROOT, *parts)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def internal_text_or_skip(*parts):
    """The text of an internal document; skips the test when this checkout does not have it."""
    text = internal_text(*parts)
    if text is None:
        pytest.skip(f"{'/'.join(parts)} lives in the private instance repository, not in this checkout")
    return text
