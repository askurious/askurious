"""Green full runs, kept per state of the code (test suite concept 2026-10-18, F).

A deploy ran the whole suite (`--full`) once more on a state that its pull request had already run green: 25 to 40
minutes for nothing. A run of everything (`--full`, nothing picked or left out) that ends green on a checkout without
uncommitted or untracked files writes a mark named after the git tree of HEAD. The tree, not the commit: the merge
commit of a pull request that was up to date with main has the tree of its branch. deploy/docker/deploy-docker.sh
skips its test step when the mark of its tree exists (tools/green_run.py); TEST_GREEN_DIR moves the folder.

The marks live in the temp folder of this machine, like the test places (tests/helpers/slots.py): a convenience for
the machine that ran the tests, not a proof for anyone else."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def folder():
    return Path(os.environ.get("TEST_GREEN_DIR") or os.path.join(tempfile.gettempdir(), "askurious-green-runs"))


def _git(root, *args):
    run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
    return run.stdout.strip() if run.returncode == 0 else None


def clean_tree(root):
    """The tree hash of HEAD when the checkout has no uncommitted or untracked files, else None."""
    try:
        status = _git(root, "status", "--porcelain")
        tree = _git(root, "rev-parse", "HEAD^{tree}")
    except (OSError, subprocess.SubprocessError):
        return None
    return tree if status == "" and tree else None


def record(root, passed, skipped):
    tree = clean_tree(root)
    if not tree:
        return None
    mark = {"tree": tree, "commit": _git(root, "rev-parse", "HEAD"), "passed": passed, "skipped": skipped,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python": sys.executable}
    folder().mkdir(parents=True, exist_ok=True)
    path = folder() / f"{tree}.json"
    part = path.with_suffix(f".{os.getpid()}.part")
    part.write_text(json.dumps(mark), "utf-8")
    os.replace(part, path)
    return mark


def found(root):
    """The mark of a green full run of exactly this state of the checkout, or None."""
    tree = clean_tree(root)
    if not tree:
        return None
    try:
        return json.loads((folder() / f"{tree}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
