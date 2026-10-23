"""Marks of green full runs per git tree (tests/helpers/green_runs.py, test suite concept 2026-10-18, F)."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests import conftest
from tests.helpers import green_runs


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GREEN_DIR", str(tmp_path / "marks"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a", "utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a")
    return repo


def test_a_green_run_is_found_for_the_same_tree_only(repo):
    assert green_runs.found(repo) is None
    mark = green_runs.record(repo, 10, 2)
    assert green_runs.found(repo) == mark and mark["passed"] == 10 and mark["skipped"] == 2
    # another commit with the same tree (a merge of an up-to-date branch) counts too
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "merge")
    assert green_runs.found(repo)["tree"] == mark["tree"]
    (repo / "a.txt").write_text("b", "utf-8")
    assert green_runs.found(repo) is None                   # uncommitted change: neither found nor written
    assert green_runs.record(repo, 10, 0) is None
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "b")
    assert green_runs.found(repo) is None                   # a new tree has no mark


def test_untracked_files_count_as_unclean(repo):
    (repo / "new.py").write_text("", "utf-8")
    assert green_runs.record(repo, 1, 0) is None and green_runs.found(repo) is None


def _config(tmp_path, args, **option):
    (tmp_path / "tests").mkdir(exist_ok=True)
    defaults = {"keyword": "", "markexpr": "", "lf": False, "deselect": None, "ignore": None, "ignore_glob": None,
                "testmon": False}
    return types.SimpleNamespace(option=types.SimpleNamespace(**{**defaults, **option}), args=args,
                                 rootpath=Path(tmp_path), invocation_params=types.SimpleNamespace(dir=Path(tmp_path)),
                                 getoption=lambda name: option.get("full", True) if name == "--full" else None)


@pytest.mark.parametrize("args, option, everything", [
    (["tests"], {}, True),
    (["tests/"], {}, True),
    (["tests"], {"full": False}, False),
    (["tests/platform"], {}, False),
    (["tests"], {"keyword": "quota"}, False),
    (["tests"], {"markexpr": "slow"}, False),
    (["tests"], {"lf": True}, False),
    (["tests"], {"deselect": ["tests/x.py::t"]}, False),
    (["tests"], {"ignore": ["tests/live"]}, False),
    (["tests"], {"testmon": True}, False),
])
def test_only_a_run_of_everything_leaves_a_mark(tmp_path, args, option, everything):
    assert conftest._runs_everything(_config(tmp_path, args, **option)) is everything


def test_wsl_gets_the_files_of_the_checkout_with_unix_line_ends(tmp_path):
    """Test suite concept 2026-10-18, D: the deploy tests run in WSL on a copy of the checkout's files."""
    import tarfile

    from tests.helpers import wsl_deploy

    _git(tmp_path, "init", "-q")
    (tmp_path / "run.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    (tmp_path / "new.txt").write_bytes(b"a\r\nb")
    (tmp_path / ".gitignore").write_text("ignored.txt\n", "utf-8")
    (tmp_path / "ignored.txt").write_text("x", "utf-8")
    tar = wsl_deploy._snapshot(tmp_path)
    try:
        with tarfile.open(tar) as archive:
            names = set(archive.getnames())
            assert names == {"run.sh", "new.txt", ".gitignore", wsl_deploy.PART_FILE}  # + the files for WSL
            assert archive.extractfile("run.sh").read() == b"#!/bin/bash\necho hi\n"
            assert archive.getmember("run.sh").mode & 0o111
            assert archive.extractfile("new.txt").read() == b"a\r\nb"   # only scripts change
    finally:
        Path(tar).unlink()
    assert wsl_deploy._wsl_path(r"C:\Users\x\Temp\a.tar") == "/mnt/c/Users/x/Temp/a.tar"
    with pytest.raises(ValueError):  # no drive letter (a share; on the CI runner also /tmp/...): refused, no IndexError
        wsl_deploy._wsl_path("//server/share/a.tar")
    if sys.platform != "win32":
        with pytest.raises(ValueError):
            wsl_deploy._wsl_path("/tmp/a.tar")
