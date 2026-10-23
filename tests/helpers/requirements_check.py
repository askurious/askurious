"""Is the Python that runs the tests the one the app runs with? (test suite concept 2026-10-18, A)

The suite needs every package of requirements.lock (what the server installs) and of requirements-dev.txt. Several
venvs on the development machine had drifted apart: one without google-re2, reportlab and pypdf turned the same few
tests red in every run. Missing packages now stop the run at its start with one message; other versions only warn."""
import re
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ("requirements.lock", "requirements-dev.txt")
# needed only for an option (--testmon) or a tool, not by any test
OPTIONAL = {"coverage", "pytest-testmon"}
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def pins(root=ROOT):
    """{name: version} of every `name==version` line in requirements.lock and requirements-dev.txt."""
    out = {}
    for name in FILES:
        path = Path(root) / name
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                found = _PIN.match(line.strip())
                if found:
                    out[found.group(1).lower()] = found.group(2)
    return out


def check(root=ROOT, installed=None):
    """(missing, other) for this Python: missing [name==version], other [(name, wanted, installed)]."""
    if installed is None:
        installed = {}
        for dist in metadata.distributions():
            name = (dist.metadata["Name"] or "").lower()
            if name:
                installed[re.sub(r"[-_.]+", "-", name)] = dist.version
    missing, other = [], []
    for name, version in sorted(pins(root).items()):
        have = installed.get(re.sub(r"[-_.]+", "-", name))
        if have is None:
            if name not in OPTIONAL:
                missing.append(f"{name}=={version}")
        elif have != version:
            other.append((name, version, have))
    return missing, other


def message(missing, python):
    return (f"The Python running the tests ({python}) lacks packages of requirements.lock / requirements-dev.txt: "
            f"{', '.join(missing)}. Without them tests fail that have nothing to do with the change. Build a complete "
            f"test venv with `python tools/test_venv.py <folder>` (or install exactly these versions) and run again.")
