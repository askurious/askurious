"""The page token ("pos") a test client's participation carries for a page, as runtime.page_token gives it: since the
audit 2026-10-23 (DS-01) it ends with a keyed digest of the participant ID, so tests can no longer write "r1" by hand.
The participant ID is read from the client's session (the stored participation, else the one not stored yet)."""
import re


def page_pos(p, position, revision=0):
    """The token of page `position` (with ".{revision}" when the page was shown in another version) for client `p`."""
    from app import runtime

    with p.session_transaction() as s:
        pids = [v for k, v in s.items() if re.fullmatch(r"rp\d+", k) and v]
        pids += [v.get("pid") for k, v in s.items() if re.fullmatch(r"n\d+", k) and isinstance(v, dict) and v.get("pid")]
    assert pids, "no participation in this client's session"
    with p.application.app_context():
        mark = runtime._person_mark(pids[0])
    return f"r{position}{f'.{revision}' if revision else ''}-{mark}"
