"""The coding view answers a broken connection with a message instead of staying silent (UI/UX audit 2026-10-29,
WQ-10); the browser logic runs in Node (tests/transcript_coding_network_cases.js)."""
import shutil
from pathlib import Path

import pytest
from tests.helpers.bounded_run import run


def test_coding_post_without_a_connection():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser logic harness")
    root = Path(__file__).resolve().parents[2]
    result = run([node, str(root / "tests" / "transcript_coding_network_cases.js")], cwd=root, timeout=30)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_callers_say_the_message():
    js = (Path(__file__).resolve().parents[2] / "app/static/transcript_coding_code.js").read_text(encoding="utf-8")
    assert "if (d.network) selection = m;" in js  # the passage stays marked for another try
    assert js.count("d.network ? d.error") == 2  # removing a coding, accepting or discarding suggestions
