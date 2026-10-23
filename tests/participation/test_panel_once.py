"""Every value of a URL parameter only once, e.g. the ID of a panel (app/panel.py, app/participate.py)."""
from werkzeug.datastructures import MultiDict

from app import panel
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK
from tests.participation.test_end_links import build, links, start

USED = "bereits teilgenommen"


def once_survey(app, client, params=(("PID", "once"),), **settings):
    build(app, client)
    assert "gespeichert" in links(client, params=params, **settings).data.decode()
    return start(app, client)


def count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)))


def test_setting_is_a_third_way_of_handling_a_parameter(app, client, survey):
    build(app, client)
    links(client, params=(("PID", "once"), ("src", "store"), ("utm", "redirect")))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [(p["name"], p["mode"], bool(p.get("once"))) for p in panel.config(s)["params"]] == [
            ("PID", "store", True), ("src", "store", False), ("utm", "redirect", False)]
        assert panel.once_names(s) == ["PID"] and panel.stored_names(s) == ["PID", "src"]
        assert any("keine Pflicht" in message for _, message in panel.check(s))
    html = client.get("/surveys/1/field").data.decode()
    assert '<option value="once" selected>' in html and html.count('<option value="store" selected>') == 1
    data = MultiDict([("param_name", "PID"), ("param_mode", "once"), ("param_required", "1"), ("param_allowed", "")])
    client.post("/surveys/1/panel", data=data)
    with app.app_context():
        assert not panel.check(db.session.get(Survey, 1))


def test_second_visit_with_the_same_id_is_turned_away(app, client, survey):
    uid = once_survey(app, client)
    first = app.test_client()
    assert USED not in first.get(f"/s/{uid}?PID=p7").data.decode() and count(app) == 0
    first.post(f"/s/{uid}", data=CONSENT_OK)
    assert count(app) == 1
    first.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    first.post(f"/s/{uid}", data={"nav": "next"})                                            # completed
    second = app.test_client()
    page = second.get(f"/s/{uid}?PID=p7")
    assert page.status_code == 200 and USED in page.data.decode()
    assert second.post(f"/s/{uid}", data=CONSENT_OK).status_code == 302                     # nothing to store
    assert count(app) == 1
    assert USED not in first.get(f"/s/{uid}?PID=p7").data.decode()                           # the same browser goes on
    other = app.test_client()
    assert USED not in other.get(f"/s/{uid}?PID=p8").data.decode()                           # another ID
    assert USED not in app.test_client().get(f"/s/{uid}?PID=p").data.decode()                # no part of an ID
    assert USED not in app.test_client().get(f"/s/{uid}").data.decode()                      # no ID: not required


def test_every_end_counts_but_a_declined_one_does_not(app, client, survey):
    uid = once_survey(app, client, redirects={"screenout": "https://panel.example/so?id={{PID}}"})
    declined = app.test_client()
    declined.get(f"/s/{uid}?PID=a1")
    declined.post(f"/s/{uid}", data={"i1": "0"})
    assert count(app) == 0 and USED not in app.test_client().get(f"/s/{uid}?PID=a1").data.decode()
    out = app.test_client()
    out.get(f"/s/{uid}?PID=a2")
    out.post(f"/s/{uid}", data=CONSENT_OK)
    out.post(f"/s/{uid}", data={"nav": "next", "i2": "12"})                                  # ends as quota full
    with app.app_context():
        assert db.session.scalar(db.select(Response.status)) == Response.QUOTAFULL
    again = app.test_client().get(f"/s/{uid}?PID=a2")
    assert again.status_code == 200 and USED in again.data.decode()                          # and no forwarding


def test_two_browsers_open_the_link_only_the_first_page_sent_stays(app, client, survey):
    uid = once_survey(app, client)
    a, b = app.test_client(), app.test_client()
    assert USED not in a.get(f"/s/{uid}?PID=p7").data.decode()
    assert USED not in b.get(f"/s/{uid}?PID=p7").data.decode()                               # nothing stored yet
    a.post(f"/s/{uid}", data=CONSENT_OK)
    late = b.post(f"/s/{uid}", data=CONSENT_OK)
    assert late.status_code == 200 and USED in late.data.decode() and count(app) == 1
    # A racing browser does not become the owner by opening the known panel ID again (TN-1).
    again = b.get(f"/s/{uid}?PID=p7", follow_redirects=True)
    assert USED in again.data.decode() and "Alter" not in again.data.decode() and count(app) == 1


def test_values_with_special_characters_and_other_surveys(app, client, survey):
    uid = once_survey(app, client, params=(("PID", "once"), ("src", "store")))
    for value in ("a%b", "a_b", 'a"b', "ä/ö"):
        p = app.test_client()
        p.get(f"/s/{uid}", query_string={"PID": value, "src": "x"})
        p.post(f"/s/{uid}", data=CONSENT_OK)
    assert count(app) == 4
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert panel.taken(s, {"PID": "a%b"}) == "PID" and panel.taken(s, {"PID": "ä/ö"}) == "PID"
        assert panel.taken(s, {"PID": "axb"}) is None and panel.taken(s, {"PID": "a"}) is None   # % and _ are no wildcards
        assert panel.taken(s, {"PID": "x"}) is None                                         # the value of another parameter
        assert panel.taken(s, {"src": "x"}) is None                                         # not a once-only parameter
        mine = db.session.scalar(db.select(Response.id).order_by(Response.id))
        assert panel.taken(s, {"PID": "a%b"}, but=mine) is None
        db.session.get(Response, mine).source = "test"                                      # test runs do not count
        db.session.commit()
        assert panel.taken(s, {"PID": "a%b"}) is None


# --- an ID only begun with does not lock its owner out (audit 2026-10-18, BIZ-05) -----------------------------


def _idle(app, minutes, pid_value=None):
    from datetime import timedelta

    from app.models import utcnow

    with app.app_context():
        for r in db.session.scalars(db.select(Response)):
            if pid_value is None or (r.params or {}).get("PID") == pid_value:
                r.last_active_at = utcnow() - timedelta(minutes=minutes)
        db.session.commit()


def test_an_id_taken_first_with_one_page_does_not_lock_the_real_person_out(app, client, survey):
    """BIZ-05: someone opens the link with a stranger's panel ID, sends the consent and leaves. Once that
    participation has been idle for the takeover time (PANEL_ONCE_TAKEOVER_MINUTES), the real person with the ID
    takes part; the participation begun before goes on without the ID."""
    uid = once_survey(app, client)
    attacker = app.test_client()
    attacker.get(f"/s/{uid}?PID=victim-4711")
    attacker.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        assert db.session.scalar(db.select(Response)).layout["sent"] == 1
    assert USED in app.test_client().get(f"/s/{uid}?PID=victim-4711").data.decode()   # within the takeover time
    _idle(app, 11)
    victim = app.test_client()
    assert USED not in victim.get(f"/s/{uid}?PID=victim-4711").data.decode()
    assert victim.post(f"/s/{uid}", data=CONSENT_OK).status_code == 302
    with app.app_context():
        rows = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        assert [r.params for r in rows] == [{}, {"PID": "victim-4711"}]
    victim.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    victim.post(f"/s/{uid}", data={"nav": "next"})                                       # completed
    _idle(app, 60)
    assert USED in app.test_client().get(f"/s/{uid}?PID=victim-4711").data.decode()   # now it is a duplicate


def test_an_id_that_went_past_its_first_page_still_turns_the_next_one_away(app, client, survey):
    """BIZ-05: a participation that sent more than its first page keeps its ID, however long it pauses."""
    uid = once_survey(app, client)
    first = app.test_client()
    first.get(f"/s/{uid}?PID=p5")
    first.post(f"/s/{uid}", data=CONSENT_OK)
    first.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    with app.app_context():
        assert db.session.scalar(db.select(Response)).layout["sent"] == 2
    _idle(app, 120)
    assert USED in app.test_client().get(f"/s/{uid}?PID=p5").data.decode()
    assert count(app) == 1
