"""Exercise task cancellation and feedback recovery against production JavaScript."""
import shutil
from pathlib import Path

import pytest
from tests.helpers.bounded_run import run


@pytest.mark.parametrize("script", ["task_interruption_cases.js", "feedback_network_cases.js", "voice_recovery_cases.js"])
def test_participant_recovery_scripts(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser logic harness")
    root = Path(__file__).resolve().parents[2]
    result = run([node, str(root / "tests" / script)], cwd=root, timeout=30)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
