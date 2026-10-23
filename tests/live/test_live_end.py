"""Ending a live vote and going on in the presenter view (pre-test 2026-10-16: nobody found how to stop a live vote,
and after "Neuer Durchgang" how to move on from the first slide)."""
from app.extensions import db
from app.live.base import live_now
from app.models import Survey
from tests.live.test_live import add, key, make, send, steer


def test_end_closes_everything_and_the_phones_show_the_closing_text(app, client):
    code, sid, block = make(app, client, "Ende")
    q1 = add(app, client, sid, block, "single_choice", question="Eins?", options=["Ja", "Nein"])
    add(app, client, sid, block, "single_choice", question="Zwei?", options=["Ja", "Nein"])
    steer(client, code, action="start")
    phone = app.test_client()
    assert key(phone, code) == f"q-{q1}"
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert "Ansicht zum Vortragen öffnen ↗" in page and "Von vorn beginnen" in page and f"/presenter/{code}/speaker" in page
    assert 'value="end"' in page and "Live-Abstimmung beenden" in page
    st = steer(client, code, action="end")
    assert st["ended"] and st["screen"] is None and set(st["closed"]) >= {q1}
    assert key(phone, code) == "ended" and key(app.test_client(), code) == "ended"
    assert "live-end" in app.test_client().get(f"/live/{code}").data.decode()
    assert phone.post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "1"}).status_code != 200 or \
        key(phone, code) == "ended"
    with app.app_context():
        assert not live_now(db.session.get(Survey, sid))                       # no "Läuft" on the card
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert "Beendet" in page and "Ansicht zum Vortragen öffnen ↗" not in page and "Von vorn beginnen" in page


def test_going_on_after_the_end_reopens_what_was_open(app, client):
    code, sid, block = make(app, client, "Weiter")
    q1 = add(app, client, sid, block, "single_choice", question="Eins?", options=["Ja", "Nein"])
    q2 = add(app, client, sid, block, "single_choice", question="Zwei?", options=["Ja", "Nein"])
    steer(client, code, action="start")
    steer(client, code, action="close")                                       # q1 closed on purpose
    steer(client, code, action="end")
    st = steer(client, code, action="screen", item=q2)
    assert not st.get("ended") and st["closed"] == [q1]
    assert key(app.test_client(), code) == f"q-{q2}"
    send(app.test_client(), code, {f"i{q2}": "1"})


def test_end_from_the_present_page_goes_back_there(app, client):
    code, sid, block = make(app, client, "Seite")
    add(app, client, sid, block, "single_choice", question="Eins?", options=["Ja", "Nein"])
    steer(client, code, action="start")
    r = client.post(f"/presenter/{code}/control", data={"action": "end"})
    assert r.headers["Location"].endswith(f"/presenter/{code}/present")
    assert "Live-Abstimmung beenden" in client.get(f"/presenter/{code}/speaker").data.decode()
