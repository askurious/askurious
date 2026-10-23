"""Setting of the page "Quoten": participations that quality control marks as they complete fill no quota place
(Survey.quota_drop_marked, quotas.settle, export.marked_at_end)."""
from datetime import timedelta

from app import duplicate, quotas, transfer
from app.extensions import db
from app.models import QuotaCell, Response, Survey, utcnow
from tests.quotas.test_quotas import Q, build, screen, start


def finish(uid, p, bot=False):
    """The rest of the questionnaire; bot: the hidden field of the last page comes back filled in."""
    p.post(f"/s/{uid}", data={"nav": "next"})
    p.post(f"/s/{uid}", data={"nav": "next", "i5": "1", **({"more_info": "x"} if bot else {})})
    return p.get(f"/s/{uid}")


def counters(app, code="m"):
    with app.app_context():
        c = db.session.scalar(db.select(QuotaCell).filter_by(code=code))
        return {"running": c.running, "done": c.done, "freed": c.freed}


def setup(app, client, on, count_at=None):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 2, [(2, "eq", "2")])])
    if on:
        client.post(Q + "/quality", data={"drop_marked": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.quality_rules = {"flags": ["bot"], "min": 1}
        if count_at:
            s.quotas[0].count_at = count_at
        db.session.commit()
    return start(app, client)


def entries(app):
    with app.app_context():
        return [r.quota["1"] for r in db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id))]


def test_off_a_script_still_takes_no_place(app, client, survey):
    """The hidden field filled in is a script, not a person: its completion gives its place back with the setting off
    as well, or a script filled every quota and closed the survey (audit 2026-10-22, D-06)."""
    uid = setup(app, client, on=False)
    finish(uid, screen(app, uid), bot=True)
    assert counters(app) == {"running": 0, "done": 0, "freed": 1}
    assert entries(app) == [{"cell": 1, "res": "in", "held": False, "done": False, "marked": True}]
    assert "Hauptteil" in screen(app, uid).get(f"/s/{uid}").data.decode()                  # the place is still free


def test_on_a_marked_completion_gives_its_place_back(app, client, survey):
    uid = setup(app, client, on=True)
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, screen(app, uid), bot=True).data.decode()
    assert counters(app) == {"running": 0, "done": 0, "freed": 1}
    assert entries(app) == [{"cell": 1, "res": "in", "held": False, "done": False, "marked": True}]
    second = screen(app, uid)                                                               # the place is free again
    assert "Hauptteil" in second.get(f"/s/{uid}").data.decode()
    finish(uid, second)                                                                     # not marked: fills it
    assert counters(app) == {"running": 0, "done": 1, "freed": 1}
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.recount(s) == []                                                      # the participations say the same
        assert quotas._from_responses(s)[0][1] == {"running": 0, "done": 1, "refused": 0, "over": 0, "freed": 1}
    # the report: not reached, but shown as marked and as having freed its place
    html = client.get("/surveys/1/results").data.decode()
    assert "markiert</th>" in html and "ihren Quotenplatz wieder freigegeben" in html
    assert "(1 markiert, frei)" in client.get("/surveys/1/flow/quotas/").data.decode()
    from app import methods

    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        q = f["quotas"][0]
        assert (q["done"], q["marked"], q["freed"]) == (1, 1, 1)                           # marked_done does not take it twice
        assert "1 davon wurden beim Abschluss markiert und gaben ihren Quotenplatz frei" in methods.draft(f)


def test_on_a_place_counted_at_the_check_goes_back(app, client, survey):
    uid = setup(app, client, on=True, count_at="check")
    p = screen(app, uid)
    assert counters(app) == {"running": 0, "done": 1, "freed": 0}                          # counted at once
    finish(uid, p, bot=True)
    assert counters(app) == {"running": 0, "done": 0, "freed": 1}
    assert entries(app) == [{"cell": 1, "res": "in", "held": False, "done": False, "at": "check", "marked": True}]
    with app.app_context():
        assert quotas.recount(db.session.get(Survey, 1)) == []
    assert "Hauptteil" in screen(app, uid).get(f"/s/{uid}").data.decode()


def test_on_a_speeder_against_the_median_gives_its_place_back(app, client, survey, monkeypatch):
    monkeypatch.setattr(quotas, "SPEEDER_MIN", 3)  # judged from the third real completion on (default 30, T-28)
    uid = setup(app, client, on=True)

    def took(seconds):  # the participation under way started this long ago
        with app.app_context():
            r = db.session.scalars(db.select(Response).filter_by(status=Response.ACTIVE)).one()
            r.started_at = utcnow() - timedelta(seconds=seconds)
            db.session.commit()

    for _ in range(2):                                                                      # women: 10 minutes each
        p = screen(app, uid, gender="2")
        took(600)
        finish(uid, p)
    assert counters(app, "f") == {"running": 0, "done": 2, "freed": 0}
    p = screen(app, uid)                                                                    # a man, in a few seconds
    finish(uid, p)
    assert counters(app) == {"running": 0, "done": 0, "freed": 1}
    p = screen(app, uid)
    took(500)
    finish(uid, p)                                                                          # in good time
    assert counters(app) == {"running": 0, "done": 1, "freed": 1}


def test_the_setting_saves_in_the_draft_and_in_the_field(app, client, survey):
    build(app, client)
    html = client.get(Q + "/").data.decode()
    assert "Von der Qualitätssicherung markierte Teilnahmen füllen keinen Quotenplatz" in html
    client.post(Q + "/quality", data={"drop_marked": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).quota_drop_marked is True
    client.post(Q + "/quality", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).quota_drop_marked is False
    start(app, client)
    client.post(Q + "/quality", data={"drop_marked": "1"})                               # also in the field (admin)
    with app.app_context():
        assert db.session.get(Survey, 1).quota_drop_marked is True
    assert "füllen ab jetzt keinen Quotenplatz" in client.get("/surveys/1/field").data.decode()          # the history
    assert "quota_drop_marked" in duplicate.SETTINGS and "quota_drop_marked" in transfer.SETTINGS
