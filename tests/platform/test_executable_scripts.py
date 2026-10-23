"""The server's shell scripts are committed executable (git mode 100755): the deploy runs them directly
(restart.sh calls ./backup.sh). The first export to the public repository lost the bit (Windows checkout, 2026-10-24),
and the deploy stopped at "Permission denied"; tools/public_export.py now marks them in the target's git index."""
import os
import subprocess

import pytest

SOURCED = {"deploy/docker/compose-env.sh"}  # read with "source", never run by itself
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _modes():
    try:
        out = subprocess.run(["git", "-C", ROOT, "ls-files", "-s", "deploy/docker"], capture_output=True, text=True,
                             check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git checkout here (deploy archive or a copy without .git)")
    return {line.split("\t", 1)[1]: line.split()[0] for line in out.splitlines() if "\t" in line}


def test_the_shell_scripts_of_the_server_are_executable():
    modes = _modes()
    scripts = [path for path in modes if path.endswith(".sh") and path not in SOURCED]
    assert scripts, "no scripts found under deploy/docker"
    assert [path for path in scripts if modes[path] != "100755"] == []
