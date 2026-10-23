"""Builds a venv for the tests with exactly the packages of requirements.lock and requirements-dev.txt.

    python tools/test_venv.py <folder>            # new venv in <folder>, or brings an existing one up to date

requirements.lock pins the server's packages with hashes of Linux wheels; on Windows and macOS other wheels of the same
versions are needed, so this installs `name==version` without the hashes. tests/conftest.py stops a run at its start
when a package is missing (tests/helpers/requirements_check.py; test suite concept 2026-10-18, A)."""
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers.requirements_check import pins  # noqa: E402


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    folder = Path(argv[0]).resolve()
    python = folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        print(f"Creating {folder} with {sys.executable}")
        venv.EnvBuilder(with_pip=True).create(folder)
    wanted = [f"{name}=={version}" for name, version in sorted(pins(ROOT).items())]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as listing:
        listing.write("\n".join(wanted) + "\n")
    try:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", listing.name],
                       check=True)
    finally:
        Path(listing.name).unlink()
    print(f"Ready: {python} -m pytest ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
