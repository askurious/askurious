"""The public state stays clean until it is exported (way B, Ralf 2026-10-19): tools/public_export.py finds nothing of
private accounts or keys in what an export would carry. The operator's server names are checked with --forbidden
from its private repository before the export itself."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import public_export  # noqa: E402


def test_what_an_export_would_carry_is_clean():
    files = list(public_export.members())
    assert files and not any(public_export.internal(path) for path, _, _ in files)
    assert public_export.findings(files) == []


def test_the_check_finds_what_it_should():
    # put together here, so that this file does not trip the check itself
    lines = ("spenden: paypal" + ".me/jemand", "x = 'jemand@" + "gmail.com'", "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
             "ssh hosting" + "1234@example.org")
    files = [(name, (line + "\n").encode(), 0o644) for name, line in zip(("a.md", "b.py", "c.txt", "d.md"), lines)]
    assert [(p, n) for p, n, _ in public_export.findings(files)] == [("a.md", 1), ("b.py", 1), ("c.txt", 1), ("d.md", 1)]
    assert public_export.findings(files, {"server": r"\bmy-server\b"}) == []


def test_the_wording_of_scales_of_others_never_goes_public(tmp_path):
    """The installation's instruments.json (app/constructs.py INSTRUMENTS) is checked with --instruments: a file that
    carries one of its texts is a finding (rights review 2026-10-21)."""
    import json

    wording = "Eine Aussage aus einer fremden Skala, lang genug"
    path = tmp_path / "instruments.json"
    path.write_text(json.dumps({"instruments": {"xy": {"question": {"de": "kurz"}, "statements": [{"de": wording}]}}}),
                    encoding="utf-8")
    patterns = public_export.read_instruments(str(path))
    assert list(patterns) == ["wording of xy (1)"]  # "kurz" is too short to count
    files = [("app/x.py", f'TEXT = "{wording}"\n'.encode(), 0o644), ("app/y.py", b"nothing\n", 0o644)]
    assert [(p, n) for p, n, _ in public_export.findings(files, patterns)] == [("app/x.py", 1)]
