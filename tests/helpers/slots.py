"""Places for parallel test runs on this machine (concept testsuite-verteilen-konzept.md, F1).

Several assistant threads share one machine. Five suites with -n 4 at once slow each other down more than the same
runs one after another, so a parallel run takes one of TEST_SLOTS places (default 2) and waits for one otherwise.
A place is a locked file in the temp folder that every checkout sees; the system frees it when the run ends, also
when it crashes. TEST_SLOTS=0 switches the waiting off.
"""

import os
import sys
import tempfile
import time

FOLDER = os.path.join(tempfile.gettempdir(), "askurious-test-slots")


def _lock(handle):
    """Lock the file without waiting; False when another process holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def try_take(count, folder=FOLDER):
    """The open file of a free place, or None when all count places are taken."""
    os.makedirs(folder, exist_ok=True)
    for number in range(count):
        handle = open(os.path.join(folder, f"slot-{number}.lock"), "a+b")
        if _lock(handle):
            return handle
        handle.close()
    return None


def take(count, folder=FOLDER, say=print, pause=2.0):
    """Wait for a free place and return its open file (keep it open for the whole run)."""
    started = time.monotonic()
    handle = try_take(count, folder)
    if handle is None:
        say(f"Waiting for a free test place ({count} parallel runs on this machine, TEST_SLOTS) ...")
    while handle is None:
        time.sleep(pause)
        handle = try_take(count, folder)
    waited = time.monotonic() - started
    if waited >= pause:
        say(f"Test place free after {waited:.0f} s.")
    return handle


def release(handle):
    if handle is not None:
        handle.close()  # closing frees the lock on Windows and elsewhere
