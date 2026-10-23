"""Writes tests/slow_tests.txt, the tests the everyday run leaves out (marker `slow`), from measured durations.

    python -m pytest tests --full -q -n 4 --durations=0 --durations-min=0.5 > durations.log
    python tools/slow_tests.py durations.log [--seconds 3]

A test is slow when setup, call and teardown together take at least --seconds (default 3) in that run. The list names
single tests instead of whole files (test suite concept 2026-10-18, G): a file of estimations also has quick checks
of its settings, and slow tests elsewhere (a lock after 100 wrong passwords, a structural equation model) belonged to no
file of the list. Deploy tests are left out here: the marker `deploy` takes care of them. Run it again now and then; a
new slow test stays in the everyday run until then.

It also writes tests/file_durations.txt, the seconds per test file, by which a run of everything on Windows splits the
files between Windows and WSL (tests/helpers/wsl_deploy.py). Use a log with --durations-min=0 for that, so the many
short tests count too."""
import argparse
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "tests" / "slow_tests.txt"
FILES = ROOT / "tests" / "file_durations.txt"
FILES_HEAD = ("# Seconds per test file in a full run, to split a Windows run between Windows and WSL (tests/helpers/"
              "wsl_deploy.py).\n# Written by tools/slow_tests.py from a --durations=0 log; do not edit by hand.\n")
HEAD = ("# Tests left out of the everyday run (marker slow; `--full` runs them), each at least {s:g} s in a full run.\n"
        "# Written by tools/slow_tests.py from a --durations=0 log; do not edit by hand.\n")


def _durations(log_text):
    total = collections.defaultdict(float)
    for line in log_text.splitlines():
        found = re.match(r"\s*([\d.]+)s (setup|call|teardown)\s+(tests/\S+)", line)
        if found:
            total[found.group(3)] += float(found.group(1))
    return total


def slow(log_text, seconds):
    return sorted(node for node, took in _durations(log_text).items() if took >= seconds)


def per_file(log_text):
    """Seconds per test file, rounded to a tenth."""
    files = collections.defaultdict(float)
    for node, took in _durations(log_text).items():
        files[node.split("::")[0]] += took
    return {name: round(took, 1) for name, took in sorted(files.items())}


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    text = Path(args.log).read_text("utf-8", errors="replace")
    nodes = slow(text, args.seconds)
    if not nodes:
        print("No durations in the log (run pytest with --durations=0).")
        return 1
    LIST.write_text(HEAD.format(s=args.seconds) + "\n".join(nodes) + "\n", "utf-8", newline="\n")
    print(f"{len(nodes)} tests in {LIST.relative_to(ROOT)}")
    files = per_file(text)
    FILES.write_text(FILES_HEAD + "".join(f"{took:g} {name}\n" for name, took in files.items()), "utf-8",
                     newline="\n")
    print(f"{len(files)} files in {FILES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
