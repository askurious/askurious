"""One rule for variable names, in one place (app/variable_names.py; Philipp 2026-11-04): questions, randomisations, loops and the
check in the browser all take it from there."""
import pathlib
import re

import pytest

from app import flow, loops, variable_names as names
from app.items import base

ROOT = pathlib.Path(names.__file__).parent


@pytest.mark.parametrize("name", ["a", "Q1", "cond1_level", "A_b_9", "x" * 64])
def test_allowed(name):
    assert names.CODE_RE.match(name)


@pytest.mark.parametrize("name", ["", "1a", "_a", "ä", "a b", "a-b", "a.b", "x" * 65, "a\n"])
def test_refused(name):
    assert not names.CODE_RE.match(name)


def test_no_keyword_filter_in_the_rule():
    """SPSS renames what it does not accept (app/sav.py): the rule itself knows no forbidden words."""
    assert names.CODE_RE.match("BY") and names.CODE_RE.match("if")


def test_every_place_uses_the_same_rule():
    assert flow.CODE_RE is names.CODE_RE and base.CODE_RE is names.CODE_RE and loops.CODE_RE is names.CODE_RE


def test_no_copy_of_the_rule_is_left_in_the_code():
    copies = [str(path.relative_to(ROOT)) for pattern in ("*.py", "*/*.py", "static/*.js") for path in ROOT.glob(pattern)
              if path.name != "variable_names.py" and "vendor" not in path.parts
              and "[A-Za-z][A-Za-z0-9_]{0,63}" in path.read_text(encoding="utf-8")]
    assert not copies


def test_the_page_hands_the_rule_to_the_browser(app, client, survey):
    page = client.get("/surveys/1/editor/").data.decode()
    found = re.search(r'data-variable-name="([^"]+)"', page)
    assert found and found.group(1) == names.VARIABLE_PATTERN
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "dataset.variableName" in script


def test_the_handbook_explains_it(app):
    for lang, word in (("de", "höchstens 64 Zeichen"), ("en", "at most 64 characters")):
        assert word in (ROOT / "data" / "handbook" / lang / "build.md").read_text(encoding="utf-8")
