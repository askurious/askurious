"""Security and performance audit 2026-10-09b, package Q5 "Auswertung schneller".

P1   the values of quality control kept per server process under the state of the participation and a hash of
     the questionnaire; Item.cfg copies the missing defaults once per object, Item.kind without an import
P2   the facts of the method section kept under their fingerprint (results_store, file "methods")
V1   an HB or segment estimate keeps the fingerprint of its data: the check compares it, nothing is rebuilt
P5   the construct pool finds the questions by the index on the construct key, not by a LIKE over every question
P9   at most HEAVY_PARALLEL heavy evaluations at once per process; the next one waits, then gets 503
Every kept value is the same as one computed anew.
"""
import copy
import random
import re
import threading
from datetime import datetime

from sqlalchemy import event

from app import construct_pool, export, heavy, items, maxdiff_hb, methods, quality, results_store
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK
from tests.helpers.audit_audit_2026_10_06_f import _likert, _scale_rows


def _fresh(s):
    """values(detail=True) of every participation of the real pool, computed now without anything kept."""
    responses = export._responses(s, False, ["real"])
    ctx = quality.context(s, export._items(s), responses)
    return {r.id: quality.values(s, r, ctx, detail=True) for r in responses}


def _kept(s):
    responses = export._responses(s, False, ["real"])
    ctx = quality.context(s, export._items(s), responses)
    return {r.id: quality.request_values(s, r, ctx) for r in responses}


# --- P1 ------------------------------------------------------------------------------------------------------------


def test_p1_quality_values_kept_per_process_and_the_same(app, client, survey, monkeypatch):
    _likert(app, client, _scale_rows(n=12))
    calls = []
    values = quality.values
    monkeypatch.setattr(quality, "values", lambda *a, **k: calls.append(1) or values(*a, **k))
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        first = _kept(s)
        assert len(calls) == 12
    with app.test_request_context():                            # another request: kept, and the same as anew
        s = db.session.get(Survey, 1)
        again = _kept(s)
        assert len(calls) == 12
        assert again == first == _fresh(s)
    # an answer changed in place (no new time, no other position): only this participation is computed anew
    with app.app_context():
        r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        r.answers = {"2": {"s1": 5, "s2": 5, "s3": 5, "s4": 5}}
        db.session.commit()
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        before = len(calls)
        changed = _kept(s)
        assert len(calls) == before + 1
        monkeypatch.setattr(quality, "values", values)
        assert changed == _fresh(s)
        monkeypatch.setattr(quality, "values", lambda *a, **k: calls.append(1) or values(*a, **k))
    # a changed setting of the questionnaire (the question becomes required): every one anew
    with app.app_context():
        db.session.get(Item, 2).answer_mode = "required"
        db.session.commit()
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        before = len(calls)
        required = _kept(s)
        assert len(calls) == before + 12
        assert required == _fresh(s) and all("qc_missing" not in v for v in required.values())
    # a changed marking rule: another key too
    with app.app_context():
        db.session.get(Survey, 1).quality_rules = {"flags": ["bot"], "min": 1}
        db.session.commit()
    with app.test_request_context():
        before = len(calls)
        _kept(db.session.get(Survey, 1))
        assert len(calls) == before + 12


def test_p1_pool_of_open_answers_follows_their_content(app, client, survey):
    from tests.conftest import add

    _likert(app, client, _scale_rows(n=3))
    add(client, "text_input")                                     # item 3, an open question
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        responses = export._responses(s, False, ["real"])
        ctx = quality.context(s, export._items(s))
        quality.index_texts(ctx, responses)
        pool = ctx["pool"]
        responses[1].answers = {**responses[1].answers, "9": "keine offene Frage"}
        quality.index_texts(ctx, responses)
        assert ctx["pool"] == pool                                # only the open answers count (audit 2026-10-10, Q-Rest)
        responses[1].answers = {**responses[1].answers, "3": "ein anderer offener Text"}
        quality.index_texts(ctx, responses)
        assert ctx["pool"] != pool                                # the same participations, other open answers


def test_p1_values_kept_up_to_the_limit(app, client, survey):
    _likert(app, client, _scale_rows(n=12))
    app.config["QC_VALUES_KEPT"] = 5
    with app.test_request_context():
        _kept(db.session.get(Survey, 1))
        assert len(quality._KEPT) <= 5
    app.config["QC_VALUES_KEPT"] = 0                              # 0: nothing kept
    quality._KEPT.clear()
    with app.test_request_context():
        _kept(db.session.get(Survey, 1))
        assert not quality._KEPT


def test_p1b_cfg_same_as_with_defaults_and_follows_the_config(app):
    with app.app_context():
        for key in items.REGISTRY:
            item = Item(type=key, config={})
            assert item.cfg == items.with_defaults(key, {}), key
            assert item.kind is items.get(key)
        item = Item(type="likert", config={})
        missing = [k for k in items.stable_defaults("likert")]
        assert missing and item.cfg == items.with_defaults("likert", {})
        key = missing[0]
        item.config[key] = "eigener Wert"                         # changed in place: read as it is now
        assert item.cfg[key] == "eigener Wert"
        item.config = {}                                          # a new config
        assert item.cfg == items.with_defaults("likert", {})
        other = Item(type="likert", config={})
        nested = [k for k, v in items.stable_defaults("likert").items() if isinstance(v, (dict, list))]
        for k in nested:                                           # copies of their own per object
            assert other.cfg[k] is not item.cfg[k]
            assert other.cfg[k] is not items.stable_defaults("likert")[k]
        full = copy.deepcopy(items.with_defaults("likert", {}))
        assert Item(type="likert", config=full).cfg is full       # nothing missing: the stored dict itself


# --- P2 ------------------------------------------------------------------------------------------------------------


def test_p2_methods_facts_kept_and_the_same(app, client, survey, monkeypatch):
    _likert(app, client, _scale_rows(n=12))
    computed = []
    original = methods._facts
    monkeypatch.setattr(methods, "_facts", lambda *a: computed.append(1) or original(*a))
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        first = methods.facts(s)
        assert len(computed) == 1
        key = methods.facts_key(s, export.threshold(s), ("real",))
        assert results_store.load(s, key, name="methods") is not None
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        again = methods.facts(s)
        assert len(computed) == 1 and again == first == original(s, export.threshold(s), ("real",))
    client.get("/surveys/1")                                      # the messages of the set-up shown elsewhere
    # the CSRF token carries the second it was signed in: two calls across a second differ only there
    # (also the one in <meta name="csrf-token" content="..." data-refresh=...>, not only before ">: CI, 2026-10-19)
    token = re.compile(r'(?<=")[\w-]+\.[\w-]+\.[\w-]+(?=")')
    page = token.sub("TOKEN", client.get("/surveys/1/methods").get_data(as_text=True))
    again_page = token.sub("TOKEN", client.get("/surveys/1/methods").get_data(as_text=True))
    assert again_page == page and len(computed) == 1  # the kept ones
    # a new participation (also an unfinished one: they are counted), a setting of the survey: computed anew
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, started_at=datetime(2026, 9, 2),
                                answers={}))
        db.session.commit()
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        changed = methods.facts(s)
        assert len(computed) == 2 and changed["started"] == first["started"] + 1
    with app.app_context():
        db.session.get(Survey, 1).declined_count = 4
        db.session.commit()
    with app.test_request_context():
        assert methods.facts(db.session.get(Survey, 1))["declined"] == 4 and len(computed) == 3


# --- V1 ------------------------------------------------------------------------------------------------------------


def test_v1_estimate_keeps_the_fingerprint_of_its_data(app, client, survey, monkeypatch):
    from tests.analysis.test_maxdiff_hb import with_dummies

    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        item = db.session.get(Item, 2)
        stored = maxdiff_hb._read(maxdiff_hb._paths(item)[0])
        assert (stored["pool"]["fingerprint"], stored["pool"]["setup"]) == maxdiff_hb.data_fingerprint(item.survey, stored["pool"])
        assert stored["pool"]["program"] == results_store.program()
        # the old check (build the table, compare the model input) says the same
        assert maxdiff_hb.model_input(item, maxdiff_hb.pool_table(item.survey, stored["pool"]),
                                      stored.get("settings"))["key"] == stored["key"]
    maxdiff_hb._FITS.clear()
    built = []
    original = maxdiff_hb.pool_table
    monkeypatch.setattr(maxdiff_hb, "pool_table", lambda *a: built.append(1) or original(*a))
    with_hb = client.get("/surveys/1/export/codebook.csv?source=dummy").get_data(as_text=True)
    assert "md_hb_util1" in with_hb and not built                 # current, without building the table
    assert "md_hb_util1" in client.get("/surveys/1/export/data.csv?source=dummy").get_data(as_text=True)
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 2, seed=8)
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None and not built   # new data: stale, not rebuilt
    assert "md_hb_util1" not in client.get("/surveys/1/export/codebook.csv?source=dummy").get_data(as_text=True)
    # estimated again for the same model input elsewhere ("current"): the file takes the new fingerprint
    monkeypatch.setattr(maxdiff_hb, "pool_table", original)
    with app.app_context():
        item = db.session.get(Item, 2)
        path = maxdiff_hb._paths(item)[0]
        stored = maxdiff_hb._read(path)
        maxdiff_hb._write(path, {**stored, "pool": {**stored["pool"], "fingerprint": "older"}})
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})  # new data: estimated anew
    with app.app_context():
        item = db.session.get(Item, 2)
        stored = maxdiff_hb._read(maxdiff_hb._paths(item)[0])
        maxdiff_hb._write(maxdiff_hb._paths(item)[0], {**stored, "pool": {**stored["pool"], "fingerprint": "older"}})
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})  # same input: "current"
    with app.app_context():
        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None


def test_v1_estimate_without_fingerprint_is_checked_as_before(app, client, survey, monkeypatch):
    from tests.analysis.test_maxdiff_hb import with_dummies

    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        item = db.session.get(Item, 2)
        path = maxdiff_hb._paths(item)[0]
        stored = maxdiff_hb._read(path)
        pool = {k: v for k, v in stored["pool"].items() if k not in ("fingerprint", "setup", "program")}
        maxdiff_hb._write(path, {**stored, "pool": pool})           # written before V1
    maxdiff_hb._FITS.clear()
    built = []
    original = maxdiff_hb.pool_table
    monkeypatch.setattr(maxdiff_hb, "pool_table", lambda *a: built.append(1) or original(*a))
    with app.app_context():
        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None and len(built) == 1


def test_v1_changed_setup_is_checked_as_before(app, client, survey, monkeypatch):
    """A display setting of the question changes the questionnaire, not the model input: checked by building the
    table once, and still current (as test_s22 of test_audit_wahlmodelle)."""
    from tests.analysis.test_maxdiff_hb import with_dummies

    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    maxdiff_hb._FITS.clear()
    built = []
    original = maxdiff_hb.pool_table
    monkeypatch.setattr(maxdiff_hb, "pool_table", lambda *a: built.append(1) or original(*a))
    with app.app_context():
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "de": {**item.texts.get("de", {}), "question": "Anders gefragt"}}
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None and len(built) == 1


def test_v1_codebook_reads_the_order_columns_without_hb(app, client, survey, monkeypatch):
    seen = []
    columns = export.columns
    monkeypatch.setattr(export, "columns", lambda s, with_hb=True, **k: seen.append(with_hb) or columns(s, with_hb, **k))
    with app.test_request_context():
        export.codebook(db.session.get(Survey, 1))
    assert seen == [False]


# --- P5 ------------------------------------------------------------------------------------------------------------


def test_p5_construct_uses_by_the_index(app, client):
    from tests.analysis.test_construct_pool import _answers, _kut_survey

    rng = random.Random(3)
    sid = _kut_survey(app, client, "Erste")
    _kut_survey(app, client, "Zweite")
    statements = []
    with app.app_context():
        item = next(i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert")
        _answers(item, rng, 5)
        db.session.commit()

        @event.listens_for(db.engine, "before_cursor_execute")
        def _seen(conn, cursor, statement, *a):
            statements.append(statement)

        found = construct_pool._uses()
        event.remove(db.engine, "before_cursor_execute", _seen)
        # the same as the former LIKE over every question
        like = {}
        for i in db.session.scalars(db.select(Item).filter(db.cast(Item.config, db.Text).like('%"construct"%'))):
            key = ((i.config or {}).get("construct") or {}).get("key")
            if key:
                like.setdefault(key, []).append((i.id, i.block.survey.id))
    assert {k: [(i.id, s.id) for i, s in v] for k, v in found.items()} == like and "kut" in found
    assert len(statements) == 1 and "LIKE" not in statements[0].upper() and "json_extract" in statements[0]


# --- P9 ------------------------------------------------------------------------------------------------------------


def test_p9_heavy_evaluations_wait_then_503(app, client, survey):
    app.config.update(HEAVY_PARALLEL=1, HEAVY_WAIT=0)
    assert client.get("/surveys/1/export/data.csv").status_code == 200       # a place, given back at the end
    assert client.get("/surveys/1/export/data.csv").status_code == 200
    held = heavy._semaphore(1)
    assert held.acquire(timeout=1)                                           # another request holds the place
    try:
        r = client.get("/surveys/1/export/data.sav")
        assert r.status_code == 503 and r.headers["Retry-After"] and "noch einmal" in r.get_data(as_text=True)
        assert client.get("/surveys/1/methods").status_code == 503
        assert client.get("/surveys/1/data").status_code == 200              # the data page itself is light
        # waiting: the place comes free while the request waits
        app.config["HEAVY_WAIT"] = 5
        threading.Timer(0.3, held.release).start()
        assert client.get("/surveys/1/export/data.csv").status_code == 200
    finally:
        try:
            held.release()
        except ValueError:
            pass
    app.config["HEAVY_PARALLEL"] = 0                                         # 0: no limit
    assert client.get("/surveys/1/export/data.csv").status_code == 200


def test_p1_another_open_answer_recomputes_only_who_it_touches(app, client, survey, monkeypatch):
    """The kept values look at their own open answers in the pool, not at the whole pool (2026-10-10): a new
    participation leaves the others' values kept unless it gave one of their open answers."""
    from tests.analysis.test_quality import scales_and_texts

    uid = scales_and_texts(app, client)
    same, other = "Weil mir das Thema sehr wichtig ist.", "Ganz anders begründet und deutlich länger."

    def take_part(why):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "4", "i2_s2": "2", "i8": why, "i9": ""})

    for why in (same, other, "", ""):
        take_part(why)
    calls = []
    values = quality.values
    monkeypatch.setattr(quality, "values", lambda *a, **k: calls.append(1) or values(*a, **k))
    with app.test_request_context():
        _kept(db.session.get(Survey, 1))
    assert len(calls) == 4
    take_part("")                                               # touches nobody's open answer: only the new one
    with app.test_request_context():
        _kept(db.session.get(Survey, 1))
    assert len(calls) == 4 + 1
    take_part(same)                                             # the first one's answer again: the first and the new
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        kept = _kept(s)
        assert len(calls) == 5 + 2
        monkeypatch.setattr(quality, "values", values)
        assert kept == _fresh(s)
        first = min(kept)
        assert kept[first]["qc_text_same"] == 1
