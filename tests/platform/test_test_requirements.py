"""The check that the Python running the tests has every package the server installs (test suite concept 2026-10-18,
A): a venv without google-re2 had turned the same regex tests red in every full run."""
from tests.helpers import requirements_check


def test_pins_cover_the_lock_and_the_dev_packages():
    pins = requirements_check.pins()
    assert pins["google-re2"] and pins["reportlab"] and pins["pypdf"] and pins["pytest-xdist"]
    assert all("\\" not in v and " " not in v for v in pins.values())


def test_missing_stops_and_other_versions_only_warn(tmp_path):
    (tmp_path / "requirements.lock").write_text("google-re2==1.1 \\n    --hash=sha256:abc\nflask==3.1.3 \\n", "utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest==9.1.1\ncoverage==7.16.2\n", "utf-8")
    missing, other = requirements_check.check(tmp_path, installed={"flask": "3.1.2", "pytest": "9.1.1"})
    assert missing == ["google-re2==1.1"]                  # coverage is optional: only --testmon needs it
    assert other == [("flask", "3.1.3", "3.1.2")]
    assert "google-re2==1.1" in requirements_check.message(missing, "python") and "tools/test_venv.py" in \
        requirements_check.message(missing, "python")


def test_names_are_compared_without_case_and_separators(tmp_path):
    (tmp_path / "requirements.lock").write_text("Typing_Extensions==4.16.0\n", "utf-8")
    assert requirements_check.check(tmp_path, installed={"typing-extensions": "4.16.0"}) == ([], [])
