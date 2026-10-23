"""Tests that play deploy and nginx scripts through with bash are marked `deploy` (security and code audit 2026-10-13,
CF-5): unmarked, they ran in the everyday run under -n 4 and failed on Windows only by their time limits."""
import pytest

from tests.platform.test_audit8_b_deploy import BASH_SLOW, needs_bash


def test_needs_bash_marks_deploy_and_skips_without_bash():
    @needs_bash
    def plays_a_script():
        pass

    names = [m.name for m in plays_a_script.pytestmark]
    assert "deploy" in names and "skipif" in names


def test_time_limits_stretch_on_windows():
    import os

    assert BASH_SLOW == (4 if os.name == "nt" else 1)


@pytest.mark.parametrize("path", ["tests/nginx_conf.py", "tests/platform/test_audit11_betrieb.py",
                                  "tests/platform/test_brand_database.py", "tests/platform/test_audit8_b_deploy.py"])
def test_script_time_limits_stretch_on_windows(path):
    import pathlib
    import re

    text = (pathlib.Path(__file__).resolve().parents[2] / path).read_text("utf-8")
    assert not re.search(r"timeout=\d+\)", text), path      # each bash run: timeout=<n> * BASH_SLOW
