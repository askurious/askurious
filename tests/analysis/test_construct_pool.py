"""Administration > Konstrukte: pooled construct data per study (app/construct_pool.py)."""
from tests.instrument_permissions import grant_instrument
import math
import random

from app import construct_pool, results
from app.extensions import db
from app.models import Construct, Item, Response, Survey, utcnow


def test_meta_pools_on_the_bonett_scale():
    same = construct_pool.meta([(0.8, 0.1), (0.8, 0.1), (0.8, 0.1)])
    assert math.isclose(same["value"], 0.8) and same["i2"] == 0 and same["tau2"] == 0 and same["k"] == 3
    assert same["ci"][0] < 0.8 < same["ci"][1]
    apart = construct_pool.meta([(0.6, 0.05), (0.9, 0.05)])
    assert 0.6 < apart["value"] < 0.9 and apart["i2"] > 0.9 and apart["tau2"] > 0
    assert construct_pool.meta([(0.8, 0.1)]) is None  # one study: nothing to pool
    assert construct_pool.meta([(0.8, 0.1), (None, 0.1), (1.0, 0.1)]) is None  # no estimate, alpha 1: left out
    # Bonett: 2k / ((k - 1)(n - 2))
    assert math.isclose(construct_pool.bonett_se(0.8, 102, 5), math.sqrt(10 / 400))
    assert construct_pool.bonett_se(0.8, 2, 5) is None


def _kut_survey(app, client, title):
    client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalars(db.select(Survey).filter(Survey.title == title)).one()
        sid, block = s.id, s.blocks[-1].id
    grant_instrument(client.application, "kut", survey_id=sid)
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "construct:kut"})
    return sid


def _answers(item, rng, n, source="real"):
    ids = [st["id"] for st in item.config["statements"]]
    for _ in range(n):
        trait = rng.randint(1, 5)
        db.session.add(Response(survey_id=item.block.survey_id, source=source, status=Response.COMPLETED, completed_at=utcnow(),
                                answers={str(item.id): {sid: min(5, max(1, trait + rng.choice((-1, 0, 0, 1)))) for sid in ids}}))


def test_admin_view_lists_studies_and_pools_them(app, client):
    rng = random.Random(7)
    ids = [_kut_survey(app, client, "Erste"), _kut_survey(app, client, "Zweite"), _kut_survey(app, client, "Dritte")]
    running = _kut_survey(app, client, "Vierte")
    with app.app_context():
        items = [next(i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert") for sid in ids]
        for item in items:
            _answers(item, rng, 40)
            _answers(item, rng, 5, source="dummy")  # never in the pool
        # the third study changed the wording: listed, but not pooled
        items[2].texts = {**items[2].texts, "de": {**items[2].texts["de"], "question": "Anders gefragt."}}
        for sid in ids[:3]:
            db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.get(Survey, running).status = Survey.RUNNING
        db.session.commit()
        page = [next(e for e in results.compute(db.session.get(Survey, sid), ("real",), False, True)["items"]
                     if e["item"].id == item.id)["scale"] for sid, item in zip(ids, items)]

    listing = client.get("/admin/constructs").get_data(as_text=True)
    assert 'href="/admin/constructs/kut"' in listing and 'aria-current="page" href="/admin/constructs"' in listing
    with app.app_context():
        row = next(r for r in construct_pool.overview() if r["construct"].key == "kut")
        assert (row["closed"], row["running"], row["n"]) == (3, 1, 120)
        r = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())
        scale = r["scales"][0]
        assert len(scale["rows"]) == 3 and r["running"] == 1 and r["n"] == 120
        for mine, theirs in zip(scale["rows"], page):  # the figures of each study's results page
            assert (mine["n"], mine["alpha"], mine["alpha_ci"], mine["omega"]) == \
                (theirs["n"], theirs["alpha"], theirs["alpha_ci"], theirs["omega"])
            assert mine["omega_ci"] is not None
        assert [x["in_pool"] for x in scale["rows"]] == [True, True, False]
        assert scale["alpha"]["k"] == 2 and scale["n"] == 80
        low, high = sorted(x["alpha"] for x in scale["rows"][:2])
        assert low <= scale["alpha"]["value"] <= high
        assert scale["omega"]["k"] == 2
        assert all(x["study"].startswith("S") for x in scale["rows"])
    # the kept results of the results page are taken as they are (no second computation)
    assert client.get(f"/surveys/{ids[0]}/results").status_code == 200
    construct_pool._cache.clear()
    with app.app_context():
        again = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())
        assert again["scales"][0]["rows"][0]["omega_ci"] == scale["rows"][0]["omega_ci"]
        assert not any(key[0] == ids[0] for key in construct_pool._cache)
    detail = client.get("/admin/constructs/kut").get_data(as_text=True)
    assert "Gepoolt" in detail and "Erste" not in detail  # studies by pseudonym only
    assert "angepasst" in detail and "Random Effects" in detail
    assert client.get("/admin/constructs/gibtsnicht").status_code == 404


def test_single_question_shows_participations_and_only_admins_see_it(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    with app.app_context():
        gender = db.session.get(Item, 2)
        for _ in range(3):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": gender.config["options"][0]["code"]}))
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    detail = client.get("/admin/constructs/gender").get_data(as_text=True)
    assert "Zusammen" in detail and "keine Reliabilität" in detail
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/admin/constructs").status_code == 403
    assert other.get("/admin/constructs/gender").status_code == 403


def test_pool_starts_the_omega_job_instead_of_drawing(app, client, monkeypatch):
    """Code review 2026-10-15, A5: the pooled view drew the omega bootstrap of every closed survey one after the
    other in its request; now it takes kept intervals and starts the job for the missing ones."""
    import threading

    from app import intervals

    rng = random.Random(8)
    sid = _kut_survey(app, client, "Erste")
    with app.app_context():
        item = next(i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert")
        _answers(item, rng, 40)
        db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.commit()
    app.config["OMEGA_BACKGROUND"] = True
    construct_pool._cache.clear()
    drawn = []
    real = intervals.omega_boot
    monkeypatch.setattr(intervals, "omega_boot", lambda *a, **k: drawn.append(threading.current_thread().name) or real(*a, **k))
    with app.app_context():
        c = db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one()
        row = construct_pool.report(c)["scales"][0]["rows"][0]
        assert row["omega_wait"] and row["omega_ci"] is None and row["omega"] is not None
    assert "wird berechnet" in client.get("/admin/constructs/kut").get_data(as_text=True)
    for t in threading.enumerate():
        if t.name.startswith("omega-"):
            t.join(60)
    assert drawn and threading.main_thread().name not in drawn                   # in the job's thread only
    with app.app_context():
        row = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())[
            "scales"][0]["rows"][0]
        assert not row["omega_wait"] and row["omega_ci"] is not None
