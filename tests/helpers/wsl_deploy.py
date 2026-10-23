"""Half of a `--full` run in WSL while Windows runs the other half (test suite concept 2026-10-18, D; split 2026-10-22).

About 90 tests play deploy and nginx scripts through with bash (marker `deploy`). With Git's bash on Windows each
script start costs a fork emulation; together they were half or more of a `--full` run (up to 100 s per test).
In WSL the same 92 tests took 50 s with two workers. Plain Python tests are about a fifth faster there too
(tests/accounts with two workers: 118 s on Windows, 94 s in WSL). So a Windows run of everything hands the deploy
tests and about half of the other test files to WSL: a copy of the checkout's files goes to /tmp in WSL, pytest runs
there in the background with as many workers as on Windows, and its result counts for the whole run.

Which files go: share() balances them by the seconds per file of the last measured run (tests/file_durations.txt,
written by tools/slow_tests.py). Files that need Node or a browser stay on Windows, because WSL has neither. A test
that is skipped in WSL runs again on Windows at the end, so a missing tool there never hides a test.

Needs WSL with a complete test venv (default `~/askurious-test-venv/bin/python`, ASKURIOUS_WSL_PYTHON elsewhere,
built with tools/test_venv.py). Without it, or with WSL_TESTS=0, everything runs on Windows as before; WSL_SPLIT=0
hands only the deploy tests to WSL."""
import io
import ntpath
import os
import re
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

PYTHON = os.environ.get("ASKURIOUS_WSL_PYTHON", "~/askurious-test-venv/bin/python")
LIMIT = 3600  # seconds for the whole WSL part
PART_FILE = "wsl_part.txt"  # the list of files for WSL, inside the copy
SKIPPED = "WSL-SKIPPED "  # a line of the WSL output naming a skipped test
WSL_SPEED = 0.8  # seconds in WSL for one second on Windows (measured 2026-10-22)
DEPLOY_SECONDS = 100.0  # worker seconds of the deploy tests in WSL
# files with deploy tests (WSL runs those anyway, Windows the rest of the file)
DEPLOY_HINT = re.compile(r"needs_bash|mark[.]deploy|audit8_b_deploy|audit9_p3_deploy")
# files that need a tool WSL lacks (Node, a Chromium browser), git and the checkout's history (the copy in WSL has no
# .git), or that skip on Windows and were never run here: they stay on Windows, as before the split
KEEP_HERE = re.compile(r'which\("node"\)|\bNODE\b|needs_browser|audit_cr8_g|public_export|.git"|skipif\(os\.name ?== ?.nt')
DEPLOY_FILES = ("test_audit8_b_deploy.py", "test_audit9_p3_deploy.py")


def wanted():
    return sys.platform == "win32" and os.environ.get("WSL_TESTS", "1") != "0"


def split_wanted():
    return os.environ.get("WSL_SPLIT", "1") != "0"


def available():
    """Does WSL answer and have the test venv? (a few seconds when WSL first starts)"""
    try:
        run = subprocess.run(["wsl.exe", "-e", "bash", "-c", f"test -x {PYTHON}"], capture_output=True, timeout=60)
        return run.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_files(root):
    """Every test file below tests/, as a path relative to the root with forward slashes."""
    base = Path(root) / "tests"
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("test_*.py") if "__pycache__" not in p.parts)


def weights(root):
    """Seconds per test file from tests/file_durations.txt ("<seconds> <file>" per line)."""
    found = {}
    path = Path(root) / "tests" / "file_durations.txt"
    if path.is_file():
        for line in path.read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and not line.startswith("#"):
                try:
                    found[parts[1]] = float(parts[0])
                except ValueError:
                    pass
    return found


def has_deploy(path):
    """Does the test file hold deploy tests (marker `deploy`, mostly through needs_bash)?"""
    path = Path(path)
    if path.name in DEPLOY_FILES:
        return True
    try:
        return bool(DEPLOY_HINT.search(path.read_text("utf-8", errors="replace")))
    except OSError:
        return False


def stays_here(root, name):
    path = Path(root) / name
    try:
        return has_deploy(path) or bool(KEEP_HERE.search(path.read_text("utf-8", errors="replace")))
    except OSError:
        return True


def share(root):
    """The test files for WSL: the biggest first, each to the side that would end earlier."""
    files = test_files(root)
    seconds = weights(root)
    default = statistics.median(seconds.values()) if seconds else 1.0
    windows, wsl, movable = 0.0, DEPLOY_SECONDS, []
    for name in files:
        took = seconds.get(name, default)
        if stays_here(root, name):
            windows += took
        else:
            movable.append((took, name))
    part = []
    for took, name in sorted(movable, key=lambda pair: (-pair[0], pair[1])):
        if wsl + took * WSL_SPEED < windows + took:
            wsl += took * WSL_SPEED
            part.append(name)
        else:
            windows += took
    return sorted(part)


def _snapshot(root, part=()):
    """A tar file of the checkout's files as they are now (tracked and new, not ignored), in the Windows temp folder,
    with the list of files for WSL."""
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
                           capture_output=True, check=True, timeout=60).stdout.decode("utf-8").split("\0")
    handle, path = tempfile.mkstemp(prefix="askurious-wsl-", suffix=".tar")
    with os.fdopen(handle, "wb") as out, tarfile.open(fileobj=out, mode="w") as tar:
        for name in files:
            full = Path(root) / name
            if name and full.is_file():
                info = tar.gettarinfo(str(full), arcname=name)
                if name.endswith(".sh"):
                    info.mode |= 0o755
                    data = full.read_bytes().replace(b"\r\n", b"\n")  # a checkout with CRLF must not break bash
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                else:
                    with open(full, "rb") as source:
                        tar.addfile(info, source)
        data = "".join(name + "\n" for name in part).encode("utf-8")
        info = tarfile.TarInfo(PART_FILE)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


def _wsl_path(path):
    """The WSL path of a Windows path (C:/x/a.tar -> /mnt/c/x/a.tar). The path is read as a Windows path on every
    system, so the test of it also runs on the Linux runner of CI; a path without a drive letter is refused."""
    if sys.platform == "win32":
        path = os.path.abspath(path)
    drive, rest = ntpath.splitdrive(str(path))
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"not a Windows path with a drive letter: {path!r}")
    return "/mnt/" + drive[0].lower() + rest.replace(ntpath.sep, "/")


def command(folder, tar, workers):
    """The bash line that unpacks the copy and runs its part: the deploy tests and the files in wsl_part.txt."""
    return (f"rm -rf {folder} && mkdir -p {folder} && tar -xf '{tar}' -C {folder} && cd {folder} "
            f"&& ASKURIOUS_WSL_PART={PART_FILE} TEST_SLOTS=0 {PYTHON} -m pytest -q -p no:warnings -p no:cacheprovider "
            f"--full -n {workers} tests; status=$?; cd /; rm -rf {folder}; exit $status")


class Run:
    """The WSL part running in the background; finish() waits and gives (exit code, the output)."""

    def __init__(self, root, part, workers):
        self.part = list(part)
        self.tar = _snapshot(root, self.part)
        self.log = Path(tempfile.gettempdir()) / f"askurious-wsl-{os.getpid()}.log"
        script = command(f"/tmp/askurious-wsl-{os.getpid()}", _wsl_path(self.tar), workers)
        self.started = time.monotonic()
        self._out = open(self.log, "wb")
        self.proc = subprocess.Popen(["wsl.exe", "-e", "bash", "-c", script], stdout=self._out,
                                     stderr=subprocess.STDOUT)

    def finish(self):
        try:
            code = self.proc.wait(timeout=max(1, LIMIT - (time.monotonic() - self.started)))
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = -1
        self._out.close()
        text = self.log.read_text("utf-8", errors="replace")
        for path in (self.tar, self.log):
            try:
                os.unlink(path)
            except OSError:
                pass
        return code, text


def skipped(text):
    """The tests the WSL part skipped (lines "WSL-SKIPPED <node id>")."""
    return [line[len(SKIPPED):].strip() for line in text.splitlines() if line.startswith(SKIPPED)]


def counted(text, word):
    """The number before "passed" (or another word) in the last summary line of a pytest output."""
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith(SKIPPED)]
    found = re.search(rf"(\d+) {word}", lines[-1]) if lines else None
    return int(found.group(1)) if found else 0


def summary(text):
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith(SKIPPED)]
    return lines[-1] if lines else "no output"


def run_here(root, nodes):
    """Runs the tests WSL skipped on Windows; gives (exit code, output, passed). Small groups at a time keep the
    command line short."""
    env = dict(os.environ, WSL_TESTS="0", TEST_SLOTS="0")
    env.pop("ASKURIOUS_WSL_SHARE", None)
    env.pop("ASKURIOUS_DEPLOY_IN_WSL", None)
    code, out, passed = 0, [], 0
    for start in range(0, len(nodes), 100):
        args = [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", "--full"]
        if len(nodes) > 20:
            args += ["-n", "2"]
        run = subprocess.run(args + nodes[start:start + 100], cwd=str(root), env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        out.append(run.stdout + run.stderr)
        passed += counted(run.stdout, "passed")
        # 5 = nothing collected; every other code but 0 is a failure
        if run.returncode not in (0, 5):
            code = run.returncode
    return code, "\n".join(out), passed
