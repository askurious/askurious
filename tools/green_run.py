"""Has this state of the code already run green with `pytest tests --full`? (test suite concept 2026-10-18, F)

    python tools/green_run.py [checkout]     # exit 0 and the mark when yes, 1 when no

Yes only for a checkout without uncommitted or untracked files whose git tree ran green on this machine
(tests/helpers/green_runs.py). deploy/docker/deploy-docker.sh uses it to skip its own full run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.helpers import green_runs  # noqa: E402


def main(argv):
    mark = green_runs.found(Path(argv[0]) if argv else ROOT)
    if not mark:
        print("No green full run of exactly this state on this machine.")
        return 1
    print(f"Green full run of tree {mark['tree'][:10]} (commit {(mark.get('commit') or '?')[:8]}) at {mark['at']}: "
          f"{mark['passed']} passed, {mark['skipped']} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
