"""The structural equation model of the results page as the job "sem_fit" (app/sem_fit_job.py; Ralf 2026-10-17,
thread "Rechenknecht"): the page prepares the model with the database, the estimate runs in the estimation process or
on the Rechenknecht, the page shows a bar meanwhile and afterwards exactly what it showed when it estimated in the
request."""
import json
import os
import random
import re

from werkzeug.datastructures import MultiDict

from app import jobs, maxdiff_hb, results_store, sem_fit_job
from app.extensions import db
from app.models import Response, Survey
from tests.analysis.test_sem_survey import _answers, _survey

MODEL = {"research_question": "TB", "goal": "correlation", "design": "cross",
         "h_id": ["", "", ""], "h_text": ["TB wirkt auf Einstellung", "Einstellung wirkt auf Absicht", "TB über Einstellung"],
         "h_kind": ["effect", "effect", "mediation"], "h_dir": ["pos", "pos", "pos"],
         "h_iv": ["tb", "einst", "tb"], "h_dv": ["einst", "absicht", "absicht"], "h_mv": ["", "", "einst"],
         "h_analysis": ["sem", "sem", ""], "sem_shown": "1"}


def _part(page):
    """The block of the model and the hypotheses, without the time of the computation."""
    out = page[page.index('id="hypothesen"'):page.index("</main>") if "</main>" in page else None]
    out = re.sub(r"[\w-]+\.[\w-]{6}\.[\w-]{20,}", "", out)  # signed tokens carry their time
    return re.sub(r"\d{1,2}:\d{2}", "", out)


def _through_job(app, client, monkeypatch):
    """The results page with the model as a job: (page while it runs, the input sent, page afterwards)."""
    started = []

    def start(app_, kind, paths, inp, pool, background=True, name="hb"):
        assert kind == "sem_fit" and background
        started.append((paths, inp))
        now = maxdiff_hb._now().isoformat()
        maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "progress": 0.25,
                                     "key": inp["key"]})
        return True

    monkeypatch.setattr(jobs, "start", start)
    app.config["SEM_BACKGROUND"] = True
    running = client.get("/surveys/1/results?go=1").data.decode()
    (paths, inp), = started
    sent = json.loads(json.dumps(inp))  # as the estimation process and the Rechenknecht get it
    result = json.loads(json.dumps(sem_fit_job.run(sent, progress=lambda share: None)))
    with app.app_context():
        maxdiff_hb._remove(paths[1])
        maxdiff_hb._write(paths[0], {**result, "pool": {}, "created": maxdiff_hb._now().isoformat()})
    done = client.get("/surveys/1/results?go=1").data.decode()
    return running, sent, done, paths


def _inline(app, client):
    app.config["SEM_BACKGROUND"] = False
    page = client.get("/surveys/1/results?go=1").data.decode()
    with app.app_context():
        results_store.forget_files(db.session.get(Survey, 1).uid)  # estimated anew below, as a job
    return page


def test_the_job_shows_what_the_request_showed(app, client, survey, monkeypatch):
    """Covariance-based with a mediation, measurement invariance over the languages and a competing model."""
    _survey(app, client, n=300, plan=False)
    with app.app_context():
        s = db.session.get(Survey, 1)
        rng = random.Random(21)
        for answers in _answers(rng, 300):
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers, language="en"))
        db.session.commit()
    client.post("/surveys/1/researchdesign", data=MultiDict({**MODEL, "sem_method": "cb", "sem_invariance": "_lang"}))
    before = _inline(app, client)
    assert 'id="sem-invariance"' in before and "RMSEA" in before
    running, sent, done, paths = _through_job(app, client, monkeypatch)
    assert 'id="sem"' in running and "Das Modell wird geschätzt" in running and "results/sem-fit/status" in running
    assert "RMSEA" not in running
    # numbers only: no participation numbers or ids travel with the job
    assert "pid" not in json.dumps(sent) and "_ids" not in json.dumps(sent) and "resp_id" not in json.dumps(sent)
    assert _part(done) == _part(before)
    assert not os.path.exists(paths[0])  # taken over into the kept results
    assert _part(client.get("/surveys/1/results?go=1").data.decode()) == _part(before)


def test_pls_scores_keep_their_participations(app, client, survey, monkeypatch):
    """PLS with PLSpredict and the construct scores: the ids of the scores stay on the page's side."""
    _survey(app, client, n=300, plan=False)
    client.post("/surveys/1/researchdesign", data=MultiDict({**MODEL, "sem_method": "pls"}))
    before = _inline(app, client)
    assert 'id="sem-predict"' in before
    scores_before = client.get("/surveys/1/export/sem-scores.csv").data
    with app.app_context():
        results_store.forget_files(db.session.get(Survey, 1).uid)
    running, sent, done, _ = _through_job(app, client, monkeypatch)
    assert _part(done) == _part(before)
    assert client.get("/surveys/1/export/sem-scores.csv").data == scores_before


def test_status_and_a_run_for_another_page(app, client, survey):
    """The bar asks for its own fingerprint; a run for another one (the weighted page) counts as queued."""
    _survey(app, client, n=150)
    with app.app_context():
        from app import sem_model

        s = db.session.get(Survey, 1)
        paths = sem_model.fit_paths(s)
        os.makedirs(os.path.dirname(paths[1]), exist_ok=True)
        now = maxdiff_hb._now().isoformat()
        maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "progress": 0.5, "key": "a"})
    assert client.get("/surveys/1/results/sem-fit/status?key=a").get_json() == \
        {"state": "running", "progress": 0.5, "queued": False}
    assert client.get("/surveys/1/results/sem-fit/status?key=b").get_json() == \
        {"state": "running", "progress": 0.0, "queued": True}


def test_pack_keeps_tuples_and_their_keys():
    x = {("a", "b"): {"ci": (0.1, float("nan"))}, "~t": [1, (2, 3)], "plain": {"x": 1}}
    assert sem_fit_job.unpack(json.loads(json.dumps(sem_fit_job.pack(x))))["~t"] == [1, (2, 3)]
    back = sem_fit_job.unpack(json.loads(json.dumps(sem_fit_job.pack(x))))
    assert back[("a", "b")]["ci"][0] == 0.1 and back[("a", "b")]["ci"][1] != back[("a", "b")]["ci"][1]
    assert back["plain"] == {"x": 1}


def test_moderation_by_groups_through_json(app, client, survey):
    """A moderation by two groups (a model in each group) and the main model: prepare, the job's numbers after JSON
    there and back, finish: the same as estimate() in one go."""
    from app import export, sem_model
    from tests.analysis.test_sem_survey import _scale
    from tests.conftest import add

    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/5", data=MultiDict({"code": "grp", "question": "grp?", "option_id": ["a", "b"],
                                                            "option_code": ["1", "2"], "option_label": ["G1", "G2"]}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        rng = random.Random(4)
        for answers in _answers(rng, 400):
            answers["5"] = rng.choice(["1", "2"])
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "x", "goal": "correlation", "design": "cross", "h_id": ["", ""], "h_text": ["H1", "H2"],
        "h_kind": ["effect", "moderation"], "h_dir": ["", ""], "h_iv": ["tb", "tb"], "h_dv": ["einst", "absicht"],
        "h_mv": ["", ""], "h_wv": ["", "grp"], "h_analysis": ["sem", "sem"], "sem_method": "cb"}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = export.rows(s, False, ("real",), None, True, with_hb=False)
        direct = sem_model.estimate(s, table)
        out, job = sem_model.prepare(s, table)
        sent = json.loads(json.dumps(sem_fit_job.pack(job)))
        found = json.loads(json.dumps(sem_fit_job.run({"key": "k", "job": sent})))["found"]
        back = sem_model.finish(out, sem_fit_job.unpack(found))
    assert direct["group_fits"] and "error" not in next(iter(direct["group_fits"].values()))
    assert back["group_fits"] == direct["group_fits"]
    assert back["fit"]["chi2"] == direct["fit"]["chi2"] and back["fit"]["paths"] == direct["fit"]["paths"]
    assert set(back) == set(direct)
