"""Limits on the estimations and dummy data of one account (Gesamtaudit 2026-10-22, D-04 and D-05): the Rechenknecht's
queue and the server's estimation slots are shared by everyone, the database too."""
from app import compute, compute_node
from app.extensions import db
from app.models import Response, Survey
from tests.analysis.test_maxdiff_hb import with_dummies


def test_one_account_starts_at_most_estimate_limit_estimations(app, client, survey, monkeypatch):
    """D-04: one account put any number of HB estimations into the queue (one per item, across copies of a survey)."""
    with_dummies(app, client, n=12)
    submitted = []
    monkeypatch.setattr(compute, "submit", lambda app_, kind, paths, inp, pool: submitted.append(kind) or True)
    app.config.update(HB_SYNC=False, COMPUTE_URL="https://rechner.example.org", COMPUTE_SECRET="s" * 40, ESTIMATE_LIMIT=2)
    codes = ["1"] + [client.post("/surveys/1/duplicate", data={"title": f"Kopie {k}"}).location.rstrip("/").split("/")[-1]
                     for k in range(2)]
    from app import dummy

    targets = []
    with app.app_context():
        for code in codes:
            s = db.session.scalar(db.select(Survey).filter_by(code=code))
            if code != "1":
                dummy.generate(s, 12, seed=5)
            targets.append((code, next(i for b in s.blocks for i in b.items if i.type == "maxdiff").id))
        db.session.commit()
    answers = [client.post(f"/surveys/{code}/results/hb/{item_id}", data={"source": "dummy", "speeder": "50"},
                           headers={"X-Requested-With": "fetch"}) for code, item_id in targets]
    assert [r.status_code for r in answers] == [200, 200, 429], [r.get_json() for r in answers]
    assert answers[2].get_json()["state"] == "limit" and "2 Schätzungen" in answers[2].get_json()["message"]
    assert len(submitted) == 2
    # without fetch: back to the page with the message
    r = client.post(f"/surveys/{targets[2][0]}/results/hb/{targets[2][1]}", data={"source": "dummy", "speeder": "50"},
                    headers={"Referer": f"/surveys/{targets[2][0]}/results"})
    assert r.status_code == 303 and r.location.endswith("/results")


def test_the_rechenknecht_refuses_jobs_beyond_its_queue(monkeypatch):
    """D-04: the Rechenknecht took every signed job into its queue, all of them in memory. Beyond COMPUTE_MAX_QUEUE it
    answers "busy" (503) and the server computes the job itself."""
    secret = "queue-test-secret" * 3
    node = compute_node.create(secret=secret, callback="", inline=True).test_client()
    monkeypatch.setenv("COMPUTE_MAX_QUEUE", "0")
    body = compute.pack({"job": "J" * 20, "kind": "maxdiff_hb", "version": compute.version(), "inp": {}})
    r = node.post("/auftrag", data=body, headers=compute.signed_headers(secret, "/auftrag", body, "POST"))
    assert r.status_code == 503


def test_dummy_data_stop_at_dummy_max(app, client, survey):
    """D-05: a loop of requests made dummy participations without end in the shared database."""
    with_dummies(app, client, n=12)
    app.config["DUMMY_MAX"] = 15
    r = client.post("/surveys/1/dummy", data={"n": "10"}, headers={"Accept": "application/json"})
    assert r.get_json()["created"] == 3
    r = client.post("/surveys/1/dummy", data={"n": "10"}, headers={"Accept": "application/json"})
    assert r.status_code == 400 and "15 Dummy-Teilnahmen" in r.get_json()["error"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, source="dummy")) == 15
