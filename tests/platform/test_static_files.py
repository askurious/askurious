"""The scripts in app/static are served as written (no build step), so nothing else would notice a file
that no longer parses, e.g. after a merge that dropped a closing line: the browser then runs none of it."""
import glob
import os
import re
import shutil
import subprocess

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "static")
SCRIPTS = sorted(glob.glob(os.path.join(STATIC, "*.js")))
NAMES = [os.path.basename(p) for p in SCRIPTS]


@pytest.mark.parametrize("path", SCRIPTS, ids=NAMES)
def test_every_section_of_a_script_is_closed(path):
    """Works without node: each script is a row of sections "(function () { ... })();" that start and end
    in the first column, a section may also hand its result to a name ("var x = (function () {", as
    transcript_player.js does for its Node tests). A merge that swallows one closing line leaves more openings than
    closings."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    opened = sum(1 for line in lines if re.match(r"(var [A-Za-z_$][\w$]* = )?\(function", line))
    closed = sum(1 for line in lines if line.startswith("})(") and line.rstrip().endswith(");"))
    assert opened == closed, f"{os.path.basename(path)}: {opened} sections opened, {closed} closed"


@pytest.mark.parametrize("path", SCRIPTS, ids=NAMES)
def test_scripts_parse(path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the section count above still runs")
    result = subprocess.run([node, "--check", path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-600:]
