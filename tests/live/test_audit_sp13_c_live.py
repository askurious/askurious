"""SP13 TN-9: replayed cookies must not add votes or bypass Q&A limits (the replay test of "Unklar" went with the
button, 2026-10-16)."""
from app.extensions import db
from app.models import LiveQuestion, Survey
from tests.live.test_live import make, steer


def test_replaying_an_old_cookie_toggles_one_vote_instead_of_adding_votes(app, client):
    code, sid, _ = make(app, client)
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.live = {**s.live, "qa": True, "filter": False}
        db.session.commit()
    p = app.test_client()
    p.get(f"/live/{code}")
    cookie = p.get_cookie("session").value
    assert p.post(f"/live/{code}/qa", data={"text": "Question"}).json["ok"]
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id))
    for _ in range(8):
        p.set_cookie("session", cookie)
        r = p.post(f"/live/{code}/qa/{qid}/vote")
        assert r.json["votes"] in (0, 1)
    assert r.json["votes"] == 0
    for _ in range(10):
        p.set_cookie("session", cookie)
        last = p.post(f"/live/{code}/qa", data={"text": "Question"})
    assert last.status_code == 429 and last.json["error"] == "many"


def test_post_without_browser_marker_is_refused(app, client):
    code, _, _ = make(app, client)
    p = app.test_client()
    assert p.open(f"/live/{code}/qa", method="POST", data={"text": "Question"}).status_code == 400


def test_concurrent_taps_with_the_same_cookie_do_not_lose_device_state(app, client):
    from concurrent.futures import ThreadPoolExecutor

    code, sid, _ = make(app, client)
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1"})
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/qa", data={"text": "Question"}).json["ok"]
    cookie = p.get_cookie("session").value
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id))

    def vote(_):
        phone = app.test_client()
        phone.set_cookie("session", cookie)
        return phone.post(f"/live/{code}/qa/{qid}/vote").status_code

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(vote, range(8))) == [200] * 8
    with app.app_context():
        assert db.session.get(LiveQuestion, qid).votes == 0


def test_new_device_ids_per_sender_and_run_are_limited(app, client):
    """Abgleich 2026-10-15 NB-7: every POST with a new device id made a row for seven days, before any limit. Now
    at most twice LIVE_RUN_SENDER_JOINS new devices per sender and run; known devices go on."""
    from app.models import BrowserReceipt

    code, sid, _ = make(app, client)
    steer(client, code, action="start")
    app.config["LIVE_RUN_SENDER_JOINS"] = 1  # twice as many new devices
    p, env = app.test_client(), {"REMOTE_ADDR": "203.0.113.30"}
    p.get(f"/live/{code}", environ_base=env)
    from app.live import devices

    with app.test_request_context():
        ids = [devices.issue() for _ in range(4)]  # signed by the server (audit 2026-10-18, BIZ-02)
    # a reaction that is stored: the count of new devices goes with what the request stores (rate_count, BIZ-01), so a
    # request refused for another reason leaves neither a device row nor a count
    statuses = [p.post(f"/live/{code}/reaction", data={"device": ids[n], "e": "👍"}, environ_base=env).status_code
                for n in range(4)]
    assert 429 not in statuses[:2] and statuses[2:] == [429, 429]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).where(
            BrowserReceipt.scope.like("live:%"))) == 2
    # a known device passes the limit (400: no emoji, it got past the device check)
    assert p.post(f"/live/{code}/reaction", data={"device": ids[0]}, environ_base=env).status_code == 400
