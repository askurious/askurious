"""tests/slow_tests.txt names single tests (test suite concept 2026-10-18, G); a renamed or deleted test must not
linger there, or it would silently run in the everyday run again under its new name."""
import re
from pathlib import Path

from tests import conftest
from tools import slow_tests

ROOT = Path(__file__).resolve().parents[2]


def test_every_listed_test_still_exists():
    gone = []
    for node in sorted(conftest.SLOW_TESTS):
        path, _, name = node.partition("::")
        name = re.sub(r"\[.*\]$", "", name.split("::")[-1])
        source = ROOT / path
        if not source.is_file() or not re.search(rf"def {re.escape(name)}\(", source.read_text("utf-8")):
            gone.append(node)
    assert not gone, "in tests/slow_tests.txt but no longer there (run tools/slow_tests.py again): " + ", ".join(gone)


def test_the_list_is_built_from_setup_call_and_teardown_together():
    log = ("3.10s call     tests/a/test_x.py::test_long\n"
           "2.00s call     tests/a/test_x.py::test_split\n1.20s setup    tests/a/test_x.py::test_split\n"
           "2.90s call     tests/a/test_x.py::test_short[p]\n")
    assert slow_tests.slow(log, 3) == ["tests/a/test_x.py::test_long", "tests/a/test_x.py::test_split"]
