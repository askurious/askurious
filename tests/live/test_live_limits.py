"""Live votes against scripts (audit 2026-10-04, M1 and N1): a phone is known by its cookie only, so a sender without
cookies is limited by its address (app/limits.py allow_live) and every run by the database."""
from app import limits
from app.extensions import db
from app.models import LiveQuestion, Response
from tests.live.test_live import add, make, send, steer

SCRIPT = {"REMOTE_ADDR": "203.0.113.7"}


class _Sender:
    """A fresh phone (no cookie) from the given address."""

    def __init__(self, app, sender):
        self.client, self.sender = app.test_client(), sender

    def get(self, url, **kw):
        return self.client.get(url, environ_base=self.sender, **kw)

    def post(self, url, **kw):
        return self.client.post(url, environ_base=self.sender, **kw)


def _phone(app, sender=SCRIPT):
    return _Sender(app, sender)


def _running(app, client):
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "single_choice", question="Wie war es?", options=["gut", "schlecht"])
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1", "reactions": "1"})
    steer(client, code, action="start")
    return code, sid, q


def _participations(app, sid):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid))


def test_new_participations_per_sender(app, client):
    code, sid, q = _running(app, client)
    app.config.update(LIVE_JOIN_LIMIT=3)
    for _ in range(3):
        assert send(_phone(app), code, {f"i{q}": "1"}).status_code == 200
    r = send(_phone(app), code, {f"i{q}": "1"})
    assert r.status_code == 429 and "sehr viele Antworten" in r.json["error"]
    assert _participations(app, sid) == 3
    assert send(_phone(app, {"REMOTE_ADDR": "203.0.113.8"}), code, {f"i{q}": "1"}).status_code == 200  # others go on
    # a phone that already takes part changes its answer: no new participation, nothing counted
    known = _phone(app, {"REMOTE_ADDR": "203.0.113.9"})
    assert send(known, code, {f"i{q}": "1"}).status_code == 200
    app.config["LIVE_JOIN_LIMIT"] = 1
    assert known.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "2"}).status_code == 200


def test_a_run_has_a_ceiling_that_reports(app, client):
    """The ceiling of a run turns nobody away (audit 2026-10-06, S5): a note on "Präsentieren", once per run and window."""
    code, sid, q = _running(app, client)
    app.config.update(LIVE_RUN_PARTICIPATIONS=2)
    for n in range(4):
        assert send(_phone(app, {"REMOTE_ADDR": f"198.51.100.{n + 1}"}), code, {f"i{q}": "1"}).status_code == 200
    assert _participations(app, sid) == 4
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert page.count("2 Teilnahmen erreicht") == 1
    steer(client, code, action="new_run")                                       # a new run starts at zero
    assert send(_phone(app, {"REMOTE_ADDR": "198.51.100.9"}), code, {f"i{q}": "1"}).status_code == 200


def test_audience_questions_votes_and_reactions(app, client):
    code, sid, q = _running(app, client)
    app.config.update(LIVE_QA_LIMIT=2, LIVE_VOTE_LIMIT=2, LIVE_REACT_LIMIT=1, LIVE_QA_FLOOD=0)
    for n in range(2):
        assert _phone(app).post(f"/live/{code}/qa", data={"text": f"Frage {n}"}).json["ok"]
    r = _phone(app).post(f"/live/{code}/qa", data={"text": "noch eine"})
    assert r.status_code == 429 and r.json["error"] == "many"
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id).order_by(LiveQuestion.id))
    assert [_phone(app).post(f"/live/{code}/qa/{qid}/vote").status_code for _ in range(3)] == [200, 200, 429]
    with app.app_context():
        assert db.session.get(LiveQuestion, qid).votes == 2
    from app import live
    from app.models import Survey

    with app.app_context():
        emoji = {"e": live.reaction_set(db.session.get(Survey, sid))[0]}
    statuses = [_phone(app).post(f"/live/{code}/reaction", data=emoji).status_code for _ in range(2)]
    assert statuses[1] == 429


def test_floods_wait_for_the_presenter_and_runs_have_a_ceiling(app, client):
    code, sid, q = _running(app, client)
    app.config.update(LIVE_QA_FLOOD=3, LIVE_RUN_QUESTIONS=5)
    answers = [_phone(app, {"REMOTE_ADDR": f"192.0.2.{n + 1}"}).post(f"/live/{code}/qa", data={"text": f"Frage {n}"}).json
               for n in range(6)]
    assert [a.get("pending") for a in answers[:5]] == [False, False, False, True, True]  # from the fourth within a minute
    assert answers[5] == {"ok": True, "pending": True}       # the run is full: reported, not refused (audit 2026-10-06, S5)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LiveQuestion.id)).filter_by(status="open")) == 3
    assert "Fragen aus dem Publikum erreicht" in client.get(f"/presenter/{code}/present").data.decode()


def test_archived_live_vote_is_over(app, client):
    code, sid, q = _running(app, client)
    client.post(f"/surveys/{sid}/archive", data={"archived": "1"})
    phone = app.test_client()
    assert phone.get(f"/live/{code}").status_code == 404
    assert phone.get(f"/live/{code}/state").status_code == 404
    assert phone.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "1"}).status_code == 404
    assert _participations(app, sid) == 0


def test_local_requests_are_not_limited(app, client):
    """Without PROXY_COUNT every request comes from the local proxy: then nobody is limited rather than everyone."""
    code, sid, q = _running(app, client)
    app.config.update(LIVE_JOIN_LIMIT=1)
    assert [send(app.test_client(), code, {f"i{q}": "1"}).status_code for _ in range(3)] == [200, 200, 200]


# --- device ids issued by the server, votes per network and question (audit 2026-10-18, BIZ-02) ----------------


def test_device_ids_the_phone_makes_up_count_for_nothing(app, client):
    """BIZ-02: 50 answers from one address without a cookie, each naming a new device id the server never issued:
    none is stored (before: 120 of 120, one participation each). Upvotes in the Q&A alike."""
    import secrets

    code, sid, q = _running(app, client)
    for _ in range(50):
        r = app.test_client().open(f"/live/{code}/answer", method="POST", environ_base=SCRIPT,  # a raw POST
                                   data={"ref": f"q-{q}", f"i{q}": "2", "device": secrets.token_urlsafe(34)[:46]})
        assert r.status_code == 400
    assert _participations(app, sid) == 0
    asker = _phone(app, {"REMOTE_ADDR": "198.51.100.99"})
    asker.get(f"/live/{code}")
    asker.post(f"/live/{code}/qa", data={"text": "Ehrliche Frage"})
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id))
    for _ in range(20):
        r = app.test_client().open(f"/live/{code}/qa/{qid}/vote", method="POST", environ_base=SCRIPT,
                                   data={"device": secrets.token_urlsafe(18)})
        assert r.status_code == 400
    with app.app_context():
        assert db.session.get(LiveQuestion, qid).votes == 0


def test_issued_ids_are_signed_and_the_session_keeps_its_own(app, client):
    from app.live import devices

    with app.test_request_context():
        one = devices.issue()
        assert devices.signed(one) and len(one) == 46
        assert not devices.signed(one[:-1] + ("A" if one[-1] != "A" else "B"))
        assert not devices.signed("x" * 24)
    code, sid, q = _running(app, client)
    phone = _phone(app)
    html = phone.get(f"/live/{code}").data.decode()
    mine = html.split('data-device="', 1)[1].split('"', 1)[0]
    assert devices_signed(app, mine)
    assert send(phone, code, {f"i{q}": "1", "device": mine}).status_code == 200
    again = phone.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "2", "device": "made-up-but-well-formed-id"})
    assert again.status_code == 200  # a changed answer: the session's own device, not a new one
    assert _participations(app, sid) == 1


def devices_signed(app, raw):
    from app.live import devices

    with app.test_request_context():
        return devices.signed(raw)


def test_devices_per_network_and_question_are_limited_for_all_processes(app, client):
    """BIZ-02: however many phones a network has, at most LIVE_SENDER_QUESTION_VOTES devices of it vote on one
    question (counted in rate_hit, not per process); another network and the next question go on."""
    code, sid, q = _running(app, client)
    app.config.update(LIVE_SENDER_QUESTION_VOTES=3)
    for _ in range(3):
        assert send(_phone(app), code, {f"i{q}": "1"}).status_code == 200
    for store in (limits._asked, limits._fallback, limits._load_fallback):
        store.clear()  # the counters in memory of another process start empty
    r = send(_phone(app), code, {f"i{q}": "1"})
    assert r.status_code == 429
    assert _participations(app, sid) == 3
    assert send(_phone(app, {"REMOTE_ADDR": "203.0.113.200"}), code, {f"i{q}": "1"}).status_code == 200
    # Q&A: three devices of the network vote a question up, the fourth is refused
    asker = _phone(app, {"REMOTE_ADDR": "198.51.100.99"})
    asker.get(f"/live/{code}")
    asker.post(f"/live/{code}/qa", data={"text": "Ehrliche Frage"})
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id))
    codes = []
    for _ in range(4):
        voter = _phone(app)
        voter.get(f"/live/{code}")
        codes.append(voter.post(f"/live/{code}/qa/{qid}/vote").status_code)
    assert codes == [200, 200, 200, 429]
