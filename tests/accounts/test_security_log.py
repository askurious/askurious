"""Security events in the log (security audit 2026-10-13, DP-7/AU-7): sign-ins that worked or failed, pauses, the
password set, one-time links made and used, the second factor, invitations and join links taken, and the refusals of the
rate limits once a minute. Before none of these was written anywhere. A line holds numbers and fixed words only: never a
password, a code, a token, an e-mail address, and instead of an address a daily digest of its network."""
import json
import logging

import pytest

from app import limits, passwords, security_log
from tests.accounts.test_passwords import GOOD, MAIL, _create, _local, _sign_in, guest  # noqa: F401  (guest: fixture)


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def lines():
    handler = _Lines()
    security_log._setup()
    security_log.log.addHandler(handler)
    yield handler.lines
    security_log.log.removeHandler(handler)


def _events(lines):
    return [json.loads(line.split(" ", 1)[1]) for line in lines if line.startswith("security ")]


def test_sign_ins_failures_and_pauses_are_logged_without_secrets(app, client, guest, lines):  # noqa: F811
    user = _local(app)
    c = app.test_client()
    addr = {"REMOTE_ADDR": "198.51.100.40"}
    c.post("/auth/sign-in", data={"email": MAIL, "password": "falsches Passwort"}, environ_base=addr)
    c.post("/auth/sign-in", data={"email": "niemand@example.org", "password": "x"}, environ_base=addr)
    c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}, environ_base=addr)
    events = _events(lines)
    failed = [e for e in events if e["event"] == "sign_in_failed"]
    assert {"account": user.id, "event": "sign_in_failed", "reason": "password"} == {k: v for k, v in failed[0].items()
                                                                                       if k != "net"}
    assert failed[1]["reason"] == "unknown" and "account" not in failed[1]
    ok = [e for e in events if e["event"] == "sign_in"][-1]
    assert ok["account"] == user.id and ok["via"] == "password" and len(ok["net"]) == 12
    text = "\n".join(lines)
    for secret in (GOOD, "falsches Passwort", MAIL, "niemand@example.org", "198.51.100"):
        assert secret not in text


def test_a_pause_after_wrong_passwords_is_logged(app, client, guest, lines):  # noqa: F811
    c = app.test_client()
    for _ in range(passwords.MAX_FAILURES):
        _sign_in(c, password="falsches Passwort")
    assert any(e["event"] == "sign_in_paused" for e in _events(lines))


def test_one_time_links_and_the_password_are_logged(app, client, lines):
    link = _create(client)
    c = app.test_client()
    c.post(link, data={"password": GOOD, "repeat": GOOD})
    names = [e["event"] for e in _events(lines)]
    assert names.index("one_time_link_created") < names.index("password_set") < names.index("one_time_link_used")
    assert link.rsplit("/", 1)[1] not in "\n".join(lines)


def test_the_second_factor_is_logged(app, client, lines):
    from tests.accounts.test_twofactor import _now_code, _setup

    secret = _setup(client)
    code = _now_code(secret)
    client.post("/account/second-factor/set-up", data={"code": code})
    client.post("/account/second-factor/off", data={"code": "000000"})
    names = [e["event"] for e in _events(lines)]
    assert "second_factor_on" in names
    text = "\n".join(lines)
    assert code not in text and secret not in text


def test_invitations_and_join_links_are_logged(app, client, lines):
    from datetime import timedelta

    from app import invites, joinlinks
    from app.extensions import db
    from app.models import GroupJoinLink, MemberInvite, User, WorkGroup, WorkGroupMember, utcnow

    with app.app_context():
        me = db.session.scalar(db.select(User))
        group = WorkGroup(name="G")
        db.session.add(group)
        db.session.flush()
        db.session.add(WorkGroupMember(group_id=group.id, orcid=me.orcid, user_id=me.id, name=me.name, role="lead"))
        link = GroupJoinLink(group_id=group.id, role="member", token_hash="a" * 64, created_by_id=me.id,
                             expires_at=utcnow() + timedelta(days=1))
        invite = MemberInvite(group_id=group.id, role="member", email="x@example.org", token_hash="b" * 64,
                              created_by_id=me.id, expires_at=utcnow() + timedelta(days=1))
        one, two = User(orcid="0000-0002-1825-0097", name="Eins"), User(orcid="0000-0000-0000-0124", name="Zwei")
        db.session.add_all([link, invite, one, two])
        db.session.commit()
        with app.test_request_context():
            joinlinks.join(link, user=one)
            invites.join(invite, user=two)
        assert db.session.scalar(db.select(db.func.count(WorkGroupMember.id)).filter_by(group_id=group.id)) == 3
        ids = one.id, two.id, group.id, me.id
    events = {e["event"]: e for e in _events(lines)}
    assert {k: events["join_link_used"][k] for k in ("account", "target", "role", "by")} == {
        "account": ids[0], "target": ids[2], "role": "member", "by": ids[3]}
    assert {k: events["invitation_accepted"][k] for k in ("account", "kind", "target", "role", "by")} == {
        "account": ids[1], "kind": "group", "target": ids[2], "role": "member", "by": ids[3]}
    assert "x@example.org" not in "\n".join(lines)


def test_refusals_of_the_rate_limits_are_counted_once_a_minute(app, lines, monkeypatch):
    app.config.update(ERASE_LIMIT=1)
    monkeypatch.setattr(limits, "_refused_since", [limits.time.monotonic() - limits.REFUSED_EVERY - 1])
    limits._refused.clear()
    with app.test_request_context(environ_base={"REMOTE_ADDR": "198.51.100.41"}):
        assert limits.allow_erase() and not limits.allow_erase()
    event = [e for e in _events(lines) if e["event"] == "rate_limited"][-1]
    assert event["counts"] == {"erase": 1} and "198.51.100" not in "\n".join(lines)
    with app.test_request_context(environ_base={"REMOTE_ADDR": "198.51.100.41"}):
        assert not limits.allow_erase()
    assert len([e for e in _events(lines) if e["event"] == "rate_limited"]) == 1  # the next line a minute later


def test_fields_beyond_the_known_ones_are_dropped(app, lines):
    with app.test_request_context():
        security_log.event("sign_in", account=3, password="geheim", email="a@b.de")
    assert _events(lines)[-1] == {"account": 3, "event": "sign_in"}
