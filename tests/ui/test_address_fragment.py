"""A broken escape in the address fragment (#%E0) no longer stops app.js (audit 2026-10-18, INJ-5):
decodeURIComponent threw a URIError there, and the script that opens folded groups for an anchor ended."""
import json
import re
import shutil
from pathlib import Path

import pytest

from tests.helpers.bounded_run import run

SCRIPT = Path(__file__).parents[2] / "app" / "static" / "app.js"
NODE = shutil.which("node")


def test_every_fragment_is_decoded_through_the_safe_helper():
    source = SCRIPT.read_text(encoding="utf-8")
    calls = re.findall(r"decodeURIComponent\(", source)
    assert len(calls) == 1 and "function safeDecode(text) { try { return decodeURIComponent(text); }" in source


@pytest.mark.skipif(NODE is None, reason="needs Node to run the helper of app/static/app.js")
def test_a_broken_escape_is_kept_as_it_stands(tmp_path):
    source = SCRIPT.read_text(encoding="utf-8")
    helper = re.search(r"^function safeDecode\(text\) \{.*\}$", source, re.M).group(0)
    harness = tmp_path / "decode.js"
    harness.write_text(helper + '\nconsole.log(JSON.stringify([safeDecode("%E0"), safeDecode("a%20b"), safeDecode("x")]));\n',
                       encoding="utf-8")
    done = run([NODE, str(harness)], timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.decode("utf-8")) == ["%E0", "a b", "x"]
