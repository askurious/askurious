"""The switch of PRAGMA foreign_keys for the tests' SQLite connections (tests/conftest.py, audit 2026-10-21, B8): in a
module of its own, so that pytest's conftest and the copy tests import as tests.conftest share it."""
FOREIGN_KEYS = [True]
