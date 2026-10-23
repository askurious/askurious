"""The handover to the participants' own address made safe (app/hosts.py, app/cookies.py; code review 2026-10-08,
H1 to H5 and H10): own salt for the participants' session cookie, a random handover id taken once, the keys named
one by one, the mark "took part" goes along, day settings checked at the start, absolute addresses from
PUBLIC_BASE_URL."""
from urllib.parse import parse_qs, urlsplit

import pytest

from app.extensions import db
from app.models import ParticipantHandover, Response
from tests.participation.test_participate import build, consent_form, uid

ASK = "http://ask.localhost"
ANSWER = {"nav": "next", "i2_s1": "2", "i2_s2": "2", "i3": "77"}


def _open(app, client):
    app.config["PARTICIPANT_HANDOVER_UNTIL"] = "2999-01-01"
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    return uid(app)


def _hid(location):
    return parse_qs(urlsplit(location).query)["uebergabe"][0]


def test_a_session_of_the_participants_address_is_worth_nothing_on_the_main_address(app, client, survey):
    """H1: the attacker's ask cookie, copied to the main address, hands nothing over to another browser."""
    code = _open(app, client)
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    attacker = app.test_client()
    attacker.get(f"{ASK}/s/{code}")
    attacker.post(f"{ASK}/s/{code}", data=consent_form())
    cookie = attacker.get_cookie("session", domain="ask.localhost")
    minter = app.test_client()
    minter.set_cookie("session", cookie.value, domain="localhost")
    r = minter.get(f"/s/{code}")
    assert r.status_code == 301 and "uebergabe" not in r.headers["Location"]
    # and the other way round: a session of the main address does not count there (after the days of change)
    app.config.update(PARTICIPANT_BASE_URL="")
    p = app.test_client()
    p.get(f"/s/{code}")
    p.post(f"/s/{code}", data=consent_form())
    app.config.update(PARTICIPANT_BASE_URL=ASK, PARTICIPANT_OLD_COOKIE_UNTIL="")
    q = app.test_client()
    q.set_cookie("session", p.get_cookie("session", domain="localhost").value, domain="ask.localhost")
    assert 'name="i3"' not in q.get(f"{ASK}/s/{code}").data.decode()


def test_a_session_signed_before_the_change_goes_on_there_for_a_while(app, client, survey):
    """Deploy moment: who was under way on the participants' address goes on until PARTICIPANT_OLD_COOKIE_UNTIL,
    and the cookie is signed anew."""
    code = _open(app, client)
    p = app.test_client()
    p.get(f"/s/{code}")
    p.post(f"/s/{code}", data=consent_form())                     # signed the way before (one salt for all)
    old = p.get_cookie("session", domain="localhost").value
    app.config.update(PARTICIPANT_BASE_URL=ASK, PARTICIPANT_OLD_COOKIE_UNTIL="2999-01-01")
    q = app.test_client()
    q.set_cookie("session", old, domain="ask.localhost")
    r = q.get(f"{ASK}/s/{code}")
    assert 'name="i3"' in r.data.decode() and "session=" in r.headers.get("Set-Cookie", "")
    assert q.get_cookie("session", domain="ask.localhost").value != old
    app.config.update(PARTICIPANT_OLD_COOKIE_UNTIL="")              # the new signature holds without the old one
    assert 'name="i3"' in q.get(f"{ASK}/s/{code}").data.decode()


def test_a_handover_is_taken_once_and_a_participation_moves_once(app, client, survey):
    """H1/H2: the address carries a random id only; the second browser with the same link gets nothing, and a
    copy of the main address' cookie does not move the same participation again."""
    code = _open(app, client)
    p = app.test_client()
    p.get(f"/s/{code}")
    p.post(f"/s/{code}", data=consent_form())                     # started on the one address of before
    copy = p.get_cookie("session", domain="localhost").value
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    r = p.get(f"/s/{code}")
    link, hid = r.headers["Location"], _hid(r.headers["Location"])
    assert r.status_code == 302 and len(hid) >= 40 and "." not in hid
    with app.app_context():
        row = db.session.get(ParticipantHandover, hid)
        assert row.payload["k"] and not row.used
    assert p.get(link).status_code == 303
    with app.app_context():
        row = db.session.get(ParticipantHandover, hid)
        assert row.used and row.payload is None                    # nothing of the participation is kept
    p.post(f"{ASK}/s/{code}", data=ANSWER)
    victim = app.test_client()
    victim.get(link)                                               # the same link once more
    assert 'value="77"' not in victim.get(f"{ASK}/s/{code}").data.decode()
    replay = app.test_client()
    replay.set_cookie("session", copy, domain="localhost")
    r = replay.get(f"/s/{code}")
    assert "uebergabe" not in r.headers["Location"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1


def test_researchers_preview_keys_stay(app, client, survey):
    """H3: only the participation's keys leave; the preview settings of the same survey stay with the researcher."""
    code = _open(app, client)
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    with client.session_transaction() as s:
        s.update({"pvq1": ["x"], "pc1": [5], "pvp1": {"group": "a"}, "pvg1": "DE", "r1": 99})
    client.get(f"/s/{code}")
    with client.session_transaction() as s:
        assert {"pvq1", "pc1", "pvp1", "pvg1"} <= set(s) and "r1" not in s


@pytest.mark.parametrize("until", ["", "2000-01-01"])
def test_the_mark_took_part_goes_along(app, client, survey, until):
    """H4: a browser marked on the main address stays marked on the participants' address, also once the handover
    of participations has ended."""
    from app.participate import MARKER_DAYS
    from tests.privacy.test_repeat_marker import build as build_marked
    from tests.privacy.test_repeat_marker import take

    code = build_marked(app, client)
    p = app.test_client()
    assert "Vielen Dank" in take(p, code).data.decode()
    p.delete_cookie("session")                                     # after the week only the mark is left
    app.config.update(PARTICIPANT_BASE_URL=ASK, PARTICIPANT_HANDOVER_UNTIL=until)
    r = p.get(f"/s/{code}")
    assert "uebergabe=" in r.headers["Location"]
    r = p.get(r.headers["Location"])
    cookie = [c for c in r.headers.getlist("Set-Cookie") if c.startswith(f"qdone_{code}=1;")]
    assert cookie and f"Max-Age={MARKER_DAYS * 86400}" in cookie[0] and "Path=/s/" in cookie[0]
    assert "bereits teilgenommen" in p.get(f"{ASK}/s/{code}").data.decode()


def test_day_settings_and_the_participants_side_are_checked_at_the_start(app):
    """H5: a mistyped day or SIDE=participant without its address stops the start instead of a 500 later."""
    from app import create_app

    for more, word in (({"PARTICIPANT_HANDOVER_UNTIL": "31.12.2026"}, "PARTICIPANT_HANDOVER_UNTIL"),
                       ({"PARTICIPANT_OLD_COOKIE_UNTIL": "bald"}, "PARTICIPANT_OLD_COOKIE_UNTIL"),
                       ({"SIDE": "participant", "PARTICIPANT_BASE_URL": ""}, "PARTICIPANT_BASE_URL")):
        bad = type("Bad", (object,), {**dict(app.config), **more})
        with pytest.raises(RuntimeError, match=word):
            create_app(bad)


def test_absolute_addresses_come_from_public_base_url(app):
    """H10: with one address an absolute address never takes the Host header; the side ignores a final dot."""
    from app import hosts

    with app.test_request_context("/", base_url="http://evil.example"):
        assert hosts.url_for("public.privacy", _external=True) == "http://localhost:5060/imprint"
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    with app.test_request_context("/", base_url="http://ask.localhost./"):
        assert hosts.current_side() == hosts.PARTICIPANT
