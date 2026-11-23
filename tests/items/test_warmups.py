"""Warm-up tasks of CBC, MaxDiff and the ratings-based conjoint (app/holdouts.py, "warmups"; concept
aufwaermaufgaben, Ralf 2026-11-23, E1-E7 as recommended): fixed tasks before task 1, the same for everyone, shown like
any task and counted in "task x of y", kept apart from the estimation, exported in columns of their own, locked after
the start, carried by dummy data, the survey file and the paper version."""
import csv
import io
import json
import random
import re

from app import holdouts
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK
from tests.analysis.test_conjoint import E, conjoint_form
from tests.analysis.test_conjoint import setup as cbc_setup


def cbc_form(warmups, holdout=None, none=True):
    """The conjoint form with warm-up tasks (one list of (look, price, privacy) level numbers per option) and
    optionally one holdout task."""
    form = conjoint_form(none=none)
    form.add("wu_count", str(len(warmups)))
    for w, alts in enumerate(warmups):
        for j, (look, price, privacy) in enumerate(alts):
            form.add(f"wu{w}_{j}_a1", f"a1l{look}")
            form.add(f"wu{w}_{j}_a2", f"a2l{price}")
            form.add(f"wu{w}_{j}_a3", f"a3l{privacy}")
    if holdout:
        form.add("ho_count", "1")
        for j, (look, price, privacy) in enumerate(holdout):
            form.add(f"ho0_{j}_a1", f"a1l{look}")
            form.add(f"ho0_{j}_a2", f"a2l{price}")
            form.add(f"ho0_{j}_a3", f"a3l{privacy}")
    return form


def wide(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


def start_and_open(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_page_order_puts_warmups_first():
    assert holdouts.page_order(4, 0, 2) == [("w", 0), ("w", 1), ("t", 0), ("t", 1), ("t", 2), ("t", 3)]
    # the first holdout stays after the third task of the design
    assert holdouts.page_order(6, 2, 1) == [("w", 0), ("t", 0), ("t", 1), ("t", 2), ("h", 0), ("t", 3), ("t", 4),
                                            ("t", 5), ("h", 1)]
    assert holdouts.page_order(6, 2) == holdouts.page_order(6, 2, 0)
    assert holdouts.page_key({"warmup": 1}) == "w1" and holdouts.page_key({"holdout": 0}) == "h0"
    assert holdouts.page_key({"task": 3}) == "t3"


def test_cbc_editor_pages_answers_estimation_and_export(app, client, survey):
    cbc_setup(client, none=True)
    page = client.get(E + "/items/2").data.decode()
    # the box above the holdout tasks, "keine" by default
    assert page.index("Aufwärmaufgaben") < page.index("Holdout-Aufgaben")
    assert re.search(r'<select name="wu_count"[^>]*>\s*<option value="0" selected', page)
    assert 'name="wu0_1_a2"' in page and 'data-ho-slot="2"' in page and "Aufwärmaufgaben einbauen?" in page
    client.post(E + "/items/2", data=cbc_form([[(0, 0, 0), (1, 2, 1)], [(1, 1, 1), (1, 1, 1)]],
                                              holdout=[(2, 1, 0), (0, 2, 1)]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.config["warmups"]) == 2 and item.kind.design_current(item)    # the design stays current
        assert item.kind.warmup_profiles(item, 0) == [[0, 0, 0], [1, 2, 1]]
        assert any("Aufwärmaufgabe 2: Option 1 und 2 sind gleich" in w for w in item.kind.check_warnings(item))
        assert item.kind.validate(item) == [] and item.kind.shown_tasks(item) == 7
        assert [k for _, _, k in item.kind.timing_pages(item)] == [".w0", ".w1", ".t0", ".t1", ".t2", ".h0", ".t3"]
        assert [label for _, label, _ in item.kind.timing_pages(item)][:2] == ["Aufwärmaufgabe 1", "Aufwärmaufgabe 2"]
    p, uid = start_and_open(app, client)
    seen = []
    for n, choice in enumerate(["1", "2", "2", "1", "0", "2", "1"], start=1):
        html = p.get(f"/s/{uid}").data.decode()
        name = re.search(r'name="(i2_[thw]\d+)"', html).group(1)
        assert f"Aufgabe {n} von 7" in html and "Keine davon" in html
        assert "Aufwärm" not in html                                # not marked for participants (E5)
        if name == "i2_w0":   # the fixed options with their levels
            assert "humanoid" in html and "150 €" in html
        seen.append(name)
        p.post(f"/s/{uid}", data={"nav": "next", name: choice})
    assert seen == ["i2_w0", "i2_w1", "i2_t0", "i2_t1", "i2_t2", "i2_h0", "i2_t3"]
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"] == {"w0": 1, "w1": 2, "t0": 2, "t1": 1, "t2": 0, "h0": 2, "t3": 1}
        from app import cbc_analysis, paradata

        item = db.session.get(Item, 2)
        assert paradata.page_key([item], {"warmup": 1, "n": 2}) == "2.w1"
        # warm-up answers never enter the counts or the logit model
        sets, _, persons, _ = cbc_analysis.choice_sets(item, [r])
        assert persons == 1 and len(sets) == 4
        assert cbc_analysis.counts(item, [r], "de")["tasks"] == 4
    row = wide(client)[0]
    assert (row["cbc_wu1"], row["cbc_wu2"]) == ("1", "2") and row["cbc_t1"] == "2" and row["cbc_ho1"] == "2"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "cbc_wu2" in book and "Aufwärmaufgabe vor Aufgabe 1" in book
    assert "Option 1: look = humanoid, price = 50 €, privacy = lokal" in book
    with app.app_context():
        from app import methods

        text = methods._choice_de({"code": "cbc", "type": "maxdiff", "n": 1, "statements": 8, "set_size": 4,
                                   "sets": 6, "versions": 1, "anchor": False, "robust": False, "hb": None,
                                   "holdouts": 0, "warmups": 2, "segments": None})
        assert "Vorab sahen alle 2 feste Aufwärm-Sets, die nicht in die Schätzung eingingen." in text
        assert "beforehand everyone saw 1 fixed warm-up task, not used" in methods._warmups_en({"warmups": 1})


def test_cbc_warmups_locked_after_start_dummy_file_and_paper(app, client, survey):
    cbc_setup(client, none=True)
    client.post(E + "/items/2", data=cbc_form([[(0, 0, 0), (1, 2, 1)]]))
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), 12, seed=3) == 12
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(a.get("w0") in (0, 1, 2) for a in answers)
        warm = db.session.get(Item, 2).config["warmups"]
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    from tests.exports.test_transfer import _upload

    assert "ist als Entwurf angelegt" in _upload(client, data, title="Aus Datei")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Aus Datei"))
        twin = next(i for b in new.blocks for i in b.items if i.type == "conjoint")
        assert twin.config["warmups"] == warm
    cbc = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "conjoint")
    cbc["config"]["warmups"][0]["alts"][0][0] = "zzz999"           # a level the file does not have
    _upload(client, data, title="Fremd")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Fremd"))
        assert next(i for b in new.blocks for i in b.items if i.type == "conjoint").config["warmups"] == []
        from app.models import Response as R

        db.session.execute(db.delete(R))
        db.session.commit()
    p, uid = start_and_open(app, client)
    name = re.search(r'name="(i2_[thw]\d+)"', p.get(f"/s/{uid}").data.decode()).group(1)
    assert name == "i2_w0"
    p.post(f"/s/{uid}", data={"nav": "next", name: "1"})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    page = client.get(E + "/items/2").data.decode()
    assert re.search(r'<select name="wu_count"[^>]*disabled', page)
    client.post(E + "/items/2", data=cbc_form([]))
    with app.app_context():
        assert db.session.get(Item, 2).config["warmups"] == warm
        from app import paper_pdf

        item = db.session.get(Item, 2)
        ctx = paper_pdf.Ctx(db.session.get(Survey, 1), "de", {"cj": {"2": {"version": 0}}})
        units = paper_pdf._conjoint_task(ctx, item, 0, None, {"warmup": 0, "n": 1, "items": [2]})
        fields = {f["f"] for u in units for f in u.fields}
        assert fields and all(f.startswith("i2_w0") or f.startswith("i2_dw0") for f in fields)


def test_default_rotation_differs_from_the_holdouts(app, client, survey):
    cbc_setup(client, none=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        for a in range(3):
            warm = [holdouts.cbc_level_choice(item, 0, j, a, "warmups") for j in range(2)]
            hold = [holdouts.cbc_level_choice(item, 0, j, a) for j in range(2)]
            assert warm != hold


def test_maxdiff_warmup_sets(app, client, survey):
    from tests.analysis.test_maxdiff import maxdiff_form
    from tests.analysis.test_maxdiff import setup as md_setup

    md_setup(client, versions=2)
    form = maxdiff_form(versions=2)
    form.add("wu_count", "1")
    for p, i in enumerate([7, 6, 5, 4]):
        form.add(f"wu0_{p}", f"s{i}")
    client.post(E + "/items/2", data=form)
    page = client.get(E + "/items/2").data.decode()
    assert page.index("Aufwärm-Sets") < page.index("Holdout-Sets")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert holdouts.maxdiff_set(item, 0, "warmups") == [7, 6, 5, 4]
        assert item.kind.validate(item) == [] and item.kind.shown_tasks(item) == item.kind.n_sets(item) + 1
        from app import runtime

        layout = runtime.build_layout(db.session.get(Survey, 1), random.Random(2))
        md = [p for p in layout["pages"] if p.get("items") == [2]]
        assert md[0].get("warmup") == 0 and md[0]["n"] == 1 and md[1].get("task") == 0
        total = item.kind.shown_tasks(item)
    p, uid = start_and_open(app, client)
    for n in range(1, total + 1):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Set {n} von {total}" in html
        key = re.search(r'name="i2_([thw]\d+)_b"', html).group(1)
        rows = [int(x) for x in re.findall(r'data-md-row="(\d+)"', html)]
        if n == 1:
            assert key == "w0" and rows == [7, 6, 5, 4]
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_{key}_b": str(rows[0]), f"i2_{key}_w": str(rows[1])})
    row = wide(client)[0]
    assert row["md_wu1_best"] == "8" and row["md_wu1_worst"] == "7"
    with app.app_context():
        from app import maxdiff_analysis

        item = db.session.get(Item, 2)
        persons = maxdiff_analysis.persons_from_table(item, wide(client))
        assert len(persons[0][2]) == item.kind.n_sets(item)          # the warm-up set is not among them


def test_cva_warmups_before_task_one_not_in_the_regression(app, client, survey):
    from tests.analysis.test_cva import cva_form
    from tests.analysis.test_cva import setup as cva_setup

    cva_setup(client)
    form = cva_form()
    form.add("wu_count", "1")
    for a, lv in (("a1", "a1l0"), ("a2", "a2l2"), ("a3", "a3l1")):
        form.add(f"wu0_0_{a}", lv)
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.warmup_profiles(item, 0) == [[0, 2, 1]]
        total = item.kind.shown_tasks(item)
        assert total == item.kind.n_tasks(item) + 1
    p, uid = start_and_open(app, client)
    keys = []
    for n in range(1, total + 1):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Aufgabe {n} von {total}" in html
        key = re.search(r'name="i2_([thw]\d+)"', html).group(1)
        keys.append(key)
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_{key}": "3"})
    assert keys[0] == "w0" and keys[1] == "t0"
    row = wide(client)[0]
    assert row["wo_wu1"] == "3"
    with app.app_context():
        from app import cva_analysis

        item = db.session.get(Item, 2)
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"]["w0"] == 3
        changed = dict(r.answers["2"], w0=-99)
        assert cva_analysis.person_values(item, changed) == cva_analysis.person_values(item, r.answers["2"])
    # the ranking has no warm-ups
    with app.app_context():
        item = db.session.get(Item, 2)
        cfg = dict(item.config, form="ranking")
        item.config = cfg
        assert holdouts.stored(item, "warmups") == [] and item.kind.warmup_count(item) == 0
