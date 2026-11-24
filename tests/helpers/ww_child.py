"""Callbacks for hb.estimate that break a run in a process of its own (tests/analysis/test_conjoint_interactions_parallel.py):
module-level, so that they travel to a "spawn" process by name."""
import os


def fail(B):
    raise ValueError("broken on purpose")


def die(B):
    os._exit(3)  # as if the process were killed: no message, no result
