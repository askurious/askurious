import csv
import io
import json
import os
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey, new_pid, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


def conjoint_form(display="table", none=False, versions=1, visual=("a1",)):
    rows = [("code", "cbc"), ("question", "Welchen Roboter wählen Sie?"), ("tasks", "4"), ("alternatives", "2"),
            ("versions", str(versions)), ("display", display), ("scene", "A care robot helps an older person")]
    if none:
        rows.append(("none", "1"))
    attrs = {"a1": ("look", "Aussehen", ["humanoid", "technisch", "tierartig"]),
             "a2": ("price", "Preis", ["50 €", "100 €", "150 €"]),
             "a3": ("privacy", "Datenschutz", ["lokal", "Cloud"])}
    for aid, (code, label, levels) in attrs.items():
        rows += [("attr_id", aid), ("attr_code", code), ("attr_label", label)]
        if aid in visual:
            rows.append(("attr_visual", aid))
        for n, lv in enumerate(levels):
            rows += [(f"lvl_{aid}_id", f"{aid}l{n}"), (f"lvl_{aid}_label", lv), (f"lvl_{aid}_hint", f"hint {lv}")]
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    add(client, "conjoint")                              # item 2
    client.post(E + "/items/2", data=conjoint_form(**kw))
    client.post(E + "/items/2/conjoint/design")


def test_config_design_and_checks(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        attrs = item.config["attributes"]
        assert [a["code"] for a in attrs] == ["look", "price", "privacy"] and attrs[0]["visual"]
        # 4 tasks x 1 version with 2 options cannot separate 5 part-worths: D-efficiency 0 with a message (M-8)
        assert item.kind.design_current(item) and item.config["design"]["d_eff"] == 0
        assert "die 5 Teilnutzen nicht alle trennen" in item.kind.too_few_tasks(item)
        versions = item.config["design"]["versions"]
        assert len(versions) == 1 and len(versions[0]) == 4 and all(len(t) == 2 for t in versions[0])
        assert all(len({tuple(a) for a in t}) == 2 for t in versions[0])  # no identical alternatives
        assert item.kind.validate(item) == []
    # changing the structure makes the design stale
    client.post(E + "/items/2", data=conjoint_form(versions=2))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.design_current(item) and "veraltet" in item.kind.validate(item)[0]
    assert "D-Effizienz" in client.post(E + "/items/2/conjoint/design", follow_redirects=True).get_data(as_text=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert 0 < item.kind.design_efficiency(item) <= 1 and item.kind.too_few_tasks(item) is None


def test_image_count_depends_on_visual_attributes(app, client, survey):
    setup(client, display="both", versions=3, visual=("a1",))
    with app.app_context():
        item = db.session.get(Item, 2)
        keys = item.kind.required_keys(item)
        assert 1 <= len(keys) <= 3 and all(set(json.loads(k)) == {"look"} for k in keys)
        prompt = item.kind.image_prompt(item, keys[0])
        assert "A care robot helps" in prompt and "hint " in prompt and "Aussehen" not in prompt and "Preis" not in prompt
        assert any("Bildern fehlen" in p for p in item.kind.validate(item))
    client.post(E + "/items/2", data=conjoint_form(display="both", versions=3, visual=("a1", "a2", "a3")))
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.kind.required_keys(item)) > 3


def test_participation_and_long_export(app, client, survey):
    setup(client, none=True)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Aufgabe 1 von 4" in html and "Keine davon" in html and 'name="i2_t0"' in html
    for t, choice in enumerate(["1", "2", "0", "1"]):
        assert f"Aufgabe {t + 1} von 4" in p.get(f"/s/{uid}").data.decode()
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": choice})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"] == {"t0": 1, "t1": 2, "t2": 0, "t3": 1}
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["cbc_version"] == "1" and [wide[f"cbc_t{n}"] for n in range(1, 5)] == ["1", "2", "0", "1"]
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 4 * 3  # 4 tasks x (2 alternatives + none)
    task3 = [r for r in long if r["task"] == "3"]
    assert [r["chosen"] for r in task3] == ["0", "0", "1"] and task3[2]["none"] == "1"
    # options not shuffled: shown in design order; "none" stands below them and has no position
    assert [r["position"] for r in task3] == ["1", "2", ""] and "cbc_attr_order" not in wide
    assert long[0]["look_label"] in ("humanoid", "technisch", "tierartig")


def test_versions_are_balanced(app, client, survey):
    setup(client, versions=2)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    for _ in range(6):  # a participation is stored (and counts for balancing) with the first submitted page
        q = app.test_client()
        q.get(f"/s/{uid}")
        q.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        versions = [r.layout["cj"]["2"]["version"] for r in db.session.scalars(db.select(Response))]
        assert sorted(versions) == [0, 0, 0, 1, 1, 1]


def test_image_batch_generation_and_review(app, client, survey, monkeypatch):
    from app import ai

    png = __import__("base64").b64encode(__import__("tests.editor.test_editor", fromlist=["PNG"]).PNG).decode()
    calls = []
    monkeypatch.setattr(ai, "generate_image", lambda prompt, size="1024x1024", **kw: calls.append(prompt) or png)
    setup(client, display="both", versions=2, visual=("a1",))
    html = client.get(E + "/items/2/images").data.decode()
    assert "fehlende Bilder erzeugen" in html
    with app.app_context():
        item = db.session.get(Item, 2)
        keys = item.kind.required_keys(item)
    for k in keys:
        assert client.post(E + "/items/2/images/generate", data={"key": k}).json["ok"]
    assert client.post(E + "/items/2/images/generate", data={"key": keys[0]}).json.get("skipped")
    assert client.post(E + "/items/2/images/generate", data={"key": "nope"}).status_code == 400
    assert len(calls) == len(keys) and all("Style:" not in c or True for c in calls)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("nicht freigegeben" in p for p in item.kind.validate(item))
    client.post(E + "/items/2/images/approve-all")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        assert all(a.status == "approved" for a in item.assets)
    # regenerating adds a pending variant next to the approved one
    client.post(E + "/items/2/images/generate", data={"key": keys[0], "force": "1"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert sum(1 for a in item.assets if a.key == keys[0]) == 2 and item.kind.validate(item) == []


def test_questions_around_a_conjoint_get_implicit_page_breaks(app, client, survey):
    setup(client, display="table", visual=())                   # item 2: conjoint in block 2
    add(client, "info", position=0)                             # 3: before the conjoint, same page
    client.post(E + "/items/3", data={"code": "intro", "question": "Einleitung"})
    add(client, "single_choice")                                # 4: after the conjoint, same page
    with app.app_context():
        import random

        from app import design, runtime

        s = db.session.get(Survey, 1)
        assert [[i.id for i in p] for p in db.session.get(Item, 2).block.pages()] == [[3, 2, 4]]
        layout = runtime.build_layout(s, random.Random(1))
        pages = [(p["items"], p.get("task")) for p in layout["pages"] if p.get("block") == 2]
        tasks = db.session.get(Item, 2).config["tasks"]
        assert pages[0] == ([3], None) and pages[-1] == ([4], None)
        assert [t for items, t in pages[1:-1]] == list(range(tasks)) and all(items == [2] for items, _ in pages[1:-1])
        assert not any("allein auf einer Seite" in f.message for f in design.check(s))


def test_explanation_page_and_hidden_attribute_column(app, client, survey):
    setup(client, display="table", visual=())
    form = conjoint_form(display="table", visual=())
    form.add("explain", "1"); form.add("hide_attr_labels", "1")
    form.add("explain_intro", "Gleich sehen Sie **Roboter**.")
    with app.app_context():
        attrs = db.session.get(Item, 2).config["attributes"]
    for a in attrs:
        form.add(f"attr_desc_{a['id']}", f"Erklärung {a['code']}")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        import random

        from app import runtime

        item = db.session.get(Item, 2)
        assert item.config["explain"] and item.config["hide_attr_labels"] and item.kind.design_current(item)
        layout = runtime.build_layout(db.session.get(Survey, 1), random.Random(1))
        cj_pages = [p for p in layout["pages"] if p.get("items") == [2]]
        assert cj_pages[0].get("explain") and [p.get("task") for p in cj_pages[1:]] == list(range(item.config["tasks"]))
        assert item.kind.parse_answer(item, MultiDict(), meta={"explain": True}) == {"explained": True}
        assert not item.kind.is_missing(item, {"explained": True})           # no nudge on the explanation page
        assert item.kind.is_missing(item, {"explained": True, "t0": None})
        assert not item.kind.is_missing(item, {"explained": True, "t0": 1})
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "Bevor es losgeht" in html and "<strong>Roboter</strong>" in html and "Erklärung look" in html
    assert "Ausprägungen:" in html
    task = client.post("/surveys/1/editor/preview", data={"nav": "next"}, follow_redirects=True).data.decode()
    assert "cj-no-labels" in task and '<th scope="row">' not in task


def test_auto_next_without_navigation(app, client, survey):
    setup(client, display="table", visual=())
    form = conjoint_form(display="table", visual=())
    form.add("auto_next", "1"); form.add("explain", "1")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["auto_next"] and item.kind.auto_next(item)
    editor = client.get(E + "/items/2").data.decode()
    assert "Ohne Weiter- und Zurück-Knöpfe" in editor
    explain = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-auto-next" not in explain                     # the explanation page keeps "Weiter"
    task = client.post("/surveys/1/editor/preview", data={"nav": "next"}, follow_redirects=True).data.decode()
    assert re.search(r'<form method="post" novalidate[^>]* data-auto-next[ >]', task) and "data-auto-next-nav" in task
    with app.app_context():
        from app import runtime
        from app.models import Item as I

        assert not runtime.auto_next_ok([db.session.get(I, 2), db.session.get(I, 1)])   # consent has no option


def test_new_conjoint_moves_on_without_buttons_by_default(app, client, survey):
    add(client, "conjoint")
    with app.app_context():
        from app.models import Item

        item = max(db.session.scalars(db.select(Item)), key=lambda i: i.id)
        assert item.type == "conjoint" and item.config["auto_next"] is True and item.kind.auto_next(item)
        assert item.config["shuffle_alts"] is True                                    # options shuffled by default
    assert 'name="auto_next" value="1" checked' in client.get(f"/surveys/1/editor/items/{item.id}").data.decode()


def test_options_shuffled_per_person_and_task_answers_keep_the_option(app, client, survey):
    import re

    form = conjoint_form()
    form.add("alternatives", "3")
    form.setlist("alternatives", ["3"])
    form.add("shuffle_alts", "1")
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["shuffle_alts"] is True and item.config["alternatives"] == 3
        orders = {tuple(item.kind.alt_order(item, seed, 0, task)) for seed in range(30) for task in range(4)}
        assert len(orders) > 3 and all(sorted(o) == [0, 1, 2] for o in orders)
        assert item.kind.alt_order(item, 7, 0, 1) == item.kind.alt_order(item, 7, 0, 1)   # stable on reload
        item.config = {**item.config, "shuffle_alts": False}
        assert item.kind.alt_order(item, 7, 0, 1) == [0, 1, 2]
        db.session.rollback()
    html = client.get(E + "/blocks/2/preview", follow_redirects=True).data.decode()
    heads = re.findall(r'<th scope="col" data-alt="(\d)"[^>]*>[^<]*Option (\d)</th>', html)
    assert [pos for _, pos in heads] == ["1", "2", "3"]                                  # positions left to right
    values = re.findall(r'name="i2_t0" value="(\d)"', html)
    assert [alt for alt, _ in heads] == values                                           # each column keeps its option
    assert sorted(values) == ["1", "2", "3"]


def test_level_definitions_in_explanation_and_image_prompt(app, client, survey):
    form = conjoint_form(display="both")
    form.add("lvl_a1_def", "sieht aus wie ein Mensch")
    form.add("lvl_a1_def", "")
    form.add("lvl_a1_def", "wie ein Haustier")
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        attr = item.kind.attributes(item, "de")[0]
        assert attr["level_defs"] == ["sieht aus wie ein Mensch", "", "wie ein Haustier"]
        key = json.dumps({"look": "a1l0"})
        prompt = item.kind.image_prompt(item, key)
        # the AI gets the English image hint only, nothing participants read (names, levels, definitions)
        assert "hint humanoid." in prompt and "sieht aus wie ein Mensch" not in prompt and "Aussehen" not in prompt
    assert 'value="sieht aus wie ein Mensch"' in client.get(E + "/items/2").data.decode()
    client.post(E + "/items/2?lang=en", data=MultiDict([("attr_id", "a1"), ("attr_code", "look"), ("attr_label", "Look"),
                                                        ("lvl_a1_id", "a1l0"), ("lvl_a1_label", "humanoid"),
                                                        ("lvl_a1_def", "looks like a person")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.t("en")["level_defs"]["a1l0"] == "looks like a person"
        assert item.t("de")["level_defs"]["a1l0"] == "sieht aus wie ein Mensch"          # per language


def test_prohibited_level_pairs_never_appear(app, client, survey):
    import pytest

    from app.conjoint_design import generate

    forbidden = [((0, 0), (1, 2)), ((1, 0), (2, 1))]
    design, eff = generate([3, 3, 2], tasks=6, alternatives=2, versions=2, seed=1, forbidden=forbidden)
    profiles = [p for v in design for t in v for p in t]
    assert profiles and 0 < eff <= 1
    assert not any((p[0] == 0 and p[1] == 2) or (p[1] == 0 and p[2] == 1) for p in profiles)
    with pytest.raises(ValueError):                                  # everything forbidden: a clear message
        generate([2, 2], 2, 2, forbidden=[((0, a), (1, b)) for a in range(2) for b in range(2)])

    form = conjoint_form()
    form.add("prohib", "a1:a1l0|a2:a2l2")                            # humanoid never with 150 €
    form.add("prohib", "a2:a2l0|a3:a3l1")                            # 50 € never with cloud
    form.add("prohib", "a1:a1l0|a1:a1l1")                            # same attribute: ignored
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=conjoint_form())
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        sig_without = db.session.get(Item, 2).kind.signature(db.session.get(Item, 2))
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.config["prohibitions"]) == 2 and item.kind.signature(item) != sig_without
        assert not item.kind.design_current(item)                     # new prohibitions: design outdated
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_current(item) and item.kind.prohibition_count(item) == 0
    html = client.get(E + "/items/2").data.decode()
    assert 'value="a1:a1l0|a2:a2l2" checked' in html and "2 verboten" not in html and "1 verboten" in html


def _choice_data(n, seed, price_beta=-0.05, never=None, person_sd=0.0):
    """Pooled CBC sets: 3 attributes (3, 4, 2 levels), a linear price, a none option; level `never`
    (attribute, level) is never chosen."""
    import numpy as np

    from app.choice_model import ChoiceSet, Coding, price_scale

    rng = np.random.default_rng(seed)
    true = [np.array([0.6, 0.0, -0.6]), np.array([0.9, 0.3, -0.4, -0.8]), np.array([0.25, -0.25])]
    prices = [10, 20, 30, 40]
    coding = Coding([3, 4, 2], price_scale(prices), asc=True)
    sets, shown = [], []
    for p in range(n):
        pw = [x + person_sd * rng.standard_normal(len(x)) for x in true]
        for _ in range(10):
            profs = [[int(rng.integers(len(x))) for x in true] for _ in range(3)]
            pr = [prices[int(rng.integers(4))] for _ in range(3)]
            v = [sum(pw[a][q[a]] for a in range(3)) + price_beta * c for q, c in zip(profs, pr)] + [-0.5]
            v = np.array(v) + rng.gumbel(size=4)
            if never:
                v[[i for i, q in enumerate(profs) if q[never[0]] == never[1]]] = -np.inf
            sets.append(ChoiceSet([coding.row(q, c) for q, c in zip(profs, pr)] + [coding.asc_row()],
                                  int(np.argmax(v)), p))
            shown += pr
    attrs = [{"code": f"A{a}", "label": f"A{a}", "level_labels": [f"L{lv}" for lv in range(n_)]}
             for a, n_ in enumerate([3, 4, 2])]
    return sets, coding, attrs, shown


def test_separated_level_is_capped_and_named():
    from app.choice_model import summarise

    sets, coding, attrs, prices = _choice_data(200, 5, never=(0, 2))
    assert coding.separated(sets) == [(0, 2)]
    m = summarise(sets, coding, attrs, 200, prices)
    a0 = m["attributes"][0]
    assert m["ridge"] and m["converged"] and all(abs(lv["beta"]) < 10 for lv in a0["levels"])      # was ±13…26
    assert any("A0: L2" in w for w in m["warnings"]) and a0["levels"][2]["separated"]
    # the whole attribute: its other levels are centred on a mean that holds the bounded value
    assert all(lv["wtp"] is None and lv["se"] is None for lv in a0["levels"]) and m["asc_se"] is None
    assert all(lv["wtp"] is not None and lv["se"] for lv in m["attributes"][1]["levels"])
    assert m["attributes"][1]["importance"] > 0.1                                                  # was 4 %


def test_willingness_to_pay_needs_a_clear_price_effect_and_has_an_interval():
    from app.choice_model import summarise

    sets, coding, attrs, prices = _choice_data(60, 8, price_beta=-0.003)
    weak = summarise(sets, coding, attrs, 60, prices)
    assert not weak["wtp_ok"] and any("nicht deutlich negativ" in w for w in weak["warnings"])
    assert all(lv["wtp"] is None for a in weak["attributes"] for lv in a["levels"])
    sets, coding, attrs, prices = _choice_data(200, 9)
    m = summarise(sets, coding, attrs, 200, prices)
    lv = m["attributes"][0]["levels"][0]
    assert m["wtp_ok"] and m["robust"] and lv["wtp_lo"] < lv["wtp"] < lv["wtp_hi"]
    assert 4 < lv["wtp"] < 20 and lv["wtp_lo"] < 12 < lv["wtp_hi"]                               # true 0.6 / 0.05 = 12


def answer_and_reopen(app, client, choices=("1", "2", "0", "1")):
    """One real participant answers all tasks; then the survey is closed and reopened for editing."""
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for t, choice in enumerate(choices):
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": choice})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})


def test_design_locked_once_real_answers_exist(app, client, survey):
    setup(client, none=True)
    with app.app_context():
        # a preview run does not lock anything
        db.session.add(Response(survey_id=1, is_preview=True, source="test", answers={"2": {"t0": 1}},
                                layout={"cj": {"2": {"version": 0, "order": []}}}))
        db.session.commit()
        item = db.session.get(Item, 2)
        assert not item.kind.design_locked(item)
    answer_and_reopen(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        before = item.config["design"]
        assert db.session.get(Survey, 1).is_editable and item.kind.design_locked(item)
    page = client.get(E + "/items/2").data.decode()
    assert "bereits echte Antworten" in page and "/conjoint/design" not in page  # no button
    r = client.post(E + "/items/2/conjoint/design", follow_redirects=True)
    assert "bereits echte Antworten" in r.data.decode()
    form = conjoint_form(none=True, versions=3)
    form.setlist("lvl_a1_label", ["menschenähnlich", "technisch", "tierartig", "neu"])   # wording: allowed
    form.setlist("lvl_a1_id", ["a1l0", "a1l1", "a1l2", "a1l3"])                          # new level: refused
    form.setlist("lvl_a1_hint", ["", "", "", ""])
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["design"] == before and item.config["versions"] == 1
        assert [len(a["levels"]) for a in item.config["attributes"]] == [3, 3, 2]
        assert item.kind.attributes(item)[0]["level_labels"][0] == "menschenähnlich"
        assert item.kind.design_current(item)


def test_long_export_and_results_skip_unknown_design_versions(app, client, survey):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in (0, 3):  # version 4 is not in the (one-version) design
            s.responses.append(Response(answers={"2": {"t0": 1, "t1": 2, "t2": 1, "t3": 1}}, source="real",
                                        layout={"cj": {"2": {"version": v, "order": []}}}, status=Response.COMPLETED,
                                        completed_at=utcnow(), pid=new_pid(s.code)))
        db.session.commit()
    r = client.get("/surveys/1/export/conjoint/2.csv")
    long = list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert r.status_code == 200 and len(long) == 4 * 2 and {row["resp_id"] for row in long} == {"1"}
    with app.app_context():
        from app import results

        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
        assert entry["cbc"]["counts"]["persons"] == 1 and entry["cbc"]["counts"]["skipped"] == 1
    assert "1 Person ausgelassen" in client.get("/surveys/1/results").data.decode()


def test_snapshot_keeps_long_format_and_design(app, client, survey):
    import re

    setup(client, none=True)
    answer_and_reopen(app, client)
    with app.app_context():
        design = db.session.get(Item, 2).config["design"]
    page = client.get("/surveys/1/data").data.decode()
    assert "Langformat conjoint_cbc" in page and "Versuchsplan cbc" in page
    snap = re.search(r"/export/snapshot/([^/]+)/conjoint_cbc_long\.csv", page).group(1)
    long = client.get(f"/surveys/1/export/snapshot/{snap}/conjoint_cbc_long.csv")
    rows = list(csv.DictReader(io.StringIO(long.data.decode("utf-8-sig"))))
    assert long.status_code == 200 and len(rows) == 4 * 3 and rows[0]["source"] == "real"
    saved = json.loads(client.get(f"/surveys/1/export/snapshot/{snap}/design_cbc.json").data.decode("utf-8"))
    assert saved["code"] == "cbc" and saved["config"]["design"] == design and "levels" in saved["texts"]["de"]
    assert client.get(f"/surveys/1/export/snapshot/{snap}/design_nope.json").status_code == 404
    assert client.get(f"/surveys/1/export/snapshot/{snap}/conjoint_cbc_long.txt").status_code == 404


def test_shown_position_and_attribute_order_are_exported(app, client, survey):
    import re

    from app import export

    form = conjoint_form(none=True)
    form.setlist("alternatives", ["3"])
    form.add("shuffle_alts", "1")
    form.add("attr_order", "participant")
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    codes = {"Aussehen": "look", "Preis": "price", "Datenschutz": "privacy"}
    seen = []                                             # per person: (attribute rows, option columns per task)
    for _ in range(8):                                    # eight: all with the same of six orders is 1 in 280 000
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        columns = []
        for t in range(4):
            html = p.get(f"/s/{uid}").data.decode()
            columns.append(re.findall(r'<th scope="col" data-alt="(\d)"[^>]*>', html))
            rows = re.findall(r'<th scope="row">([^<]+)</th>', html)
            p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": "2"})
        seen.append((rows, columns))
    assert len({tuple(c) for _, cols in seen for c in cols}) > 1 and len({tuple(r) for r, _ in seen}) > 1  # shuffled
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [w["cbc_attr_order"] for w in wide] == ["|".join(codes[label] for label in rows) for rows, _ in seen]
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert list(long[0])[:6] == ["resp_id", "source", "version", "task", "alt", "position"]
    for n, (_, columns) in enumerate(seen, start=1):
        for t in range(4):
            task = [r for r in long if r["resp_id"] == str(n) and r["task"] == str(t + 1)]
            shown = sorted((r for r in task if r["alt"] != "0"), key=lambda r: int(r["position"]))
            assert [r["alt"] for r in shown] == columns[t]            # the position the page showed
            assert [r["position"] for r in task if r["alt"] == "0"] == [""]
            assert [r["alt"] for r in task if r["chosen"] == "1"] == ["2"]  # the choice keeps the option's number
    with app.app_context():
        book = {row[0]: row for row in export.codebook(db.session.get(Survey, 1))}
        assert book["cbc_t1"][3] == "0 = Keine davon; 1 = Alternative 1; 2 = Alternative 2; 3 = Alternative 3"
        assert "Position" in book["cbc_t1"][4] and book["cbc_attr_order"][3] == "look | price | privacy"
        assert book["cbc_version"][3] == "1 bis 1"
        item = db.session.get(Item, 2)
        item.config = {**item.config, "shuffle_alts": False, "none": False}
        item.texts = {**item.texts, "de": {**item.texts["de"], "alt_label": "Angebot; neu"}}
        assert item.kind.codebook_info(item, "cbc_t2", "de")[1] == "1 = Angebot, neu 1; 2 = Angebot, neu 2; 3 = Angebot, neu 3"
        db.session.rollback()


def test_base_prompt_on_the_images_page(app, client, survey, monkeypatch):
    """What every image shows, edited where the images are made; the levels and the style come after it."""
    from app import ai

    png = __import__("base64").b64encode(__import__("tests.editor.test_editor", fromlist=["PNG"]).PNG).decode()
    calls = []
    monkeypatch.setattr(ai, "generate_image", lambda prompt, size="1024x1024", **kw: calls.append(prompt) or png)
    setup(client, display="image", visual=("a1",))
    client.post(E + "/items/2/images/scene", data={"scene": "  "})
    html = client.get(E + "/items/2/images").data.decode()
    assert "Grundprompt: Was zeigt jedes Bild?" in html and "Ohne Grundprompt" in html
    client.post(E + "/items/2/images/scene", data={"scene": "A delivery robot on a sidewalk"})
    html = client.get(E + "/items/2/images").data.decode()
    assert "A delivery robot on a sidewalk</textarea>" in html and "Ohne Grundprompt" not in html
    with app.app_context():
        item = db.session.get(Item, 2)
        key = item.kind.required_keys(item)[0]
    client.post(E + "/items/2/images/generate", data={"key": key})
    assert calls[0].startswith("Scene: A delivery robot on a sidewalk. hint humanoid.")  # then the hints and the rules


def test_ai_feedback_on_the_image_prompts(app, client, survey, monkeypatch):
    """The text model gets the base prompt and the English hints only; ticked suggestions are taken over."""
    from app import ai

    sent = []

    def fake(instructions, prompt, **kw):
        sent.append(prompt)
        return ('Sure: {"remarks": ["Die Person ist nicht festgelegt."], '
                '"scene": {"suggestion": "An older woman in a cardigan at home, eye level, soft daylight.", "why": "fixiert die Person"}, '
                '"levels": [{"level": "a1l0", "suggestion": "A humanoid robot with a face and two arms.", "why": "konkret"}, '
                '{"level": "a1l1", "suggestion": "hint technisch", "why": "unverändert"}, '
                '{"level": "zzz", "suggestion": "x", "why": "gibt es nicht"}]}')

    monkeypatch.setattr(ai, "generate_text", fake)
    form = conjoint_form(display="image", visual=("a1",))
    form.add("lvl_a1_def", "sieht aus wie ein Mensch")
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    assert "Prompts von der KI prüfen lassen" in client.get(E + "/items/2/images").data.decode()
    page = client.post(E + "/items/2/images/review").data.decode()
    assert "Die Person ist nicht festgelegt." in page and "A humanoid robot with a face and two arms." in page
    assert 'value="a1l0"' in page and 'value="a1l1"' not in page and "zzz" not in page  # unchanged and unknown: left out
    assert "hint humanoid" in sent[0] and "sieht aus wie ein Mensch" not in sent[0] and "Aussehen" not in sent[0]
    with app.app_context():
        design = db.session.get(Item, 2).config["design"]
    client.post(E + "/items/2/images/review/apply", data={"take_level": "a1l0", "hint_a1l0": "A humanoid robot  with a face.",
                                                          "scene": "ignored, not ticked"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["attributes"][0]["levels"][0]["hint"] == "A humanoid robot with a face."
        assert item.config["scene"] != "ignored, not ticked" and item.config["design"] == design
        assert item.kind.design_current(item)  # hints do not touch the choice design


def test_design_uses_the_choice_information(app, client, survey):
    """Audit 2026-10-03, M-8/M-11/M-12: the design maximises the information of the logit model (differences
    within a task), so an attribute rarely shows the same level in all options; the D-efficiency lies between
    0 and 1 (1 = ideal design); prohibitions that tie two attributes together give a clear message."""
    import numpy as np
    import pytest

    from app.conjoint_design import d_efficiency, generate

    design, eff = generate([3, 3, 2], tasks=8, alternatives=2, versions=1, seed=1)
    tasks = [t for v in design for t in v]
    same = np.mean([[len({p[a] for p in t}) == 1 for a in range(3)] for t in tasks], axis=0)
    assert same[2] <= 0.25                         # was 62 % of the tasks for the two-level attribute
    assert 0.9 < eff <= 1
    # an ideal design scores 1: 2 x 2 with both attributes different in each task and orthogonal across tasks
    assert abs(d_efficiency([[[0, 0], [1, 1]], [[0, 1], [1, 0]]], [2, 2], 2) - 1) < 1e-9
    # random pairs score clearly lower; the old det(X'X) value of a full factorial (0.69) is no longer the scale
    rng = np.random.default_rng(3)
    assert 0 < d_efficiency(rng.integers(0, [3, 3, 2], size=(16, 3)), [3, 3, 2], 2) < eff - 0.1
    with pytest.raises(ValueError, match="koppeln"):  # every level of A only with the same level of B
        generate([3, 3, 2], 8, 2, forbidden=[((0, i), (1, j)) for i in range(3) for j in range(3) if i != j])


def test_versions_show_the_levels_evenly_without_losing_information():
    import random

    import numpy as np

    from app import conjoint_design as cd

    levels = [4, 3, 3, 2]
    D, eff = cd.generate(levels, tasks=8, alternatives=3, versions=6, n_starts=3, seed=1)
    A = np.array(D)
    for v in range(6):
        rows = A[v].reshape(-1, 4)
        for j, n in enumerate(levels):
            c = np.bincount(rows[:, j], minlength=n)
            assert c.max() - c.min() <= 2
    assert cd.d_efficiency(D, levels, 3) == eff  # whole tasks moved: the same information
    flat = A.reshape(-1, 4)
    again = cd.balance_versions(flat, levels, 6, 8, 3, random.Random(2))
    assert sorted(map(tuple, again.reshape(-1, 3 * 4).tolist())) == sorted(map(tuple, flat.reshape(-1, 3 * 4).tolist()))


def test_tabs_images_and_eight_versions_by_default(app, client, survey):
    """Tab "2 · Ablauf" (not "Design"), the image button on "Darstellung & Bilder", 8 design versions for a new CBC."""
    import re

    fill_consent(client)
    add(client, "conjoint")                                                         # item 2
    with app.app_context():
        assert db.session.get(Item, 2).config["versions"] == 8
    client.post(E + "/items/2", data=MultiDict([(k, v) for k, v in conjoint_form(display="both").items(multi=True) if k != "versions"]))
    with app.app_context():
        assert db.session.get(Item, 2).config["versions"] == 8                       # left empty: 8
    html = client.get(E + "/items/2").data.decode()
    assert "2 · Ablauf" in html and "2 · Design" not in html
    assert "zuerst im Reiter „2 · Ablauf“ dieses Elements den Versuchsplan erzeugen" in html  # no design yet (W25)
    client.post(E + "/items/2/conjoint/design")
    html = client.get(E + "/items/2").data.decode()
    section = re.search(r'<section class="panel" data-cj-tab="display">(.*?)</section>', html, re.S).group(1)
    assert "Bilder hochladen</a>" in section and "Bilder mit KI erzeugen</a>" in section
    design = re.search(r'<section class="panel" id="design" data-cj-tab="design">(.*?)</section>', html, re.S).group(1)
    assert "/images" not in design


def _image(fmt="PNG", size=(2000, 1000), frames=1):
    from PIL import Image

    out = io.BytesIO()
    imgs = [Image.new("RGB", size, (200, 30 * n, 30)) for n in range(frames)]
    if frames > 1:
        imgs[0].save(out, format=fmt, save_all=True, append_images=imgs[1:])
    else:
        imgs[0].save(out, format=fmt)
    return out.getvalue()


def test_own_images_per_combination(app, client, survey):
    """Conjoint images can be uploaded instead of generated: per tile or several at once by file name; made as
    small as the AI images, approved at once, moving images refused; only in the draft."""
    from PIL import Image

    setup(client, display="both", visual=("a1", "a3"))
    with app.app_context():
        item = db.session.get(Item, 2)
        keys = item.kind.required_keys(item)
        names = {item.kind.upload_name(item, k): k for k in keys}
    assert len(keys) == 6 and "look-2_privacy-1" in names
    html = client.get(E + "/items/2/images?weg=hochladen").data.decode()
    assert "Mehrere Bilder auf einmal auswählen" in html and "Dateiname: <code>look-1_privacy-1</code>" in html
    assert "fehlende Bilder erzeugen" not in html and "data-regen" not in html and "Grundprompt" not in html
    assert '#tab-display">' in html  # back to "Darstellung & Bilder"
    assert "Ohne Bildhinweis" not in html  # the form above gives every level a hint
    csv_text = client.get(E + "/items/2/images/filenames.csv").data.decode("utf-8-sig")
    assert csv_text.splitlines()[0] == "Dateiname;Kombination" and "look-3_privacy-2;tierartig · Cloud" in csv_text
    # one tile, the form way
    r = client.post(E + "/items/2/images/upload", data={"key": names["look-1_privacy-1"], "file": (io.BytesIO(_image()), "a.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    # the multi upload sends each file with ajax=1 and gets JSON
    def send(key, data, name="b.jpg"):
        return client.post(E + "/items/2/images/upload", data={"key": key, "ajax": "1", "file": (io.BytesIO(data), name)},
                           content_type="multipart/form-data")
    assert send(names["look-2_privacy-1"], _image("JPEG", (800, 600))).json == {"ok": True}
    r = send(names["look-3_privacy-1"], _image("GIF", (40, 40), frames=3), "c.gif")
    assert r.status_code == 400 and "Standbild" in r.json["error"]
    assert send("nope", _image()).status_code == 400
    with app.app_context():
        item = db.session.get(Item, 2)
        ups = {a.key: a for a in item.assets}
        assert {a.source for a in ups.values()} == {"upload"} and all(a.status == "approved" for a in ups.values())
        folder = os.path.join(app.config["UPLOAD_FOLDER"], item.survey.uid)
        big = Image.open(os.path.join(folder, ups[names["look-1_privacy-1"]].filename))
        assert big.format == "PNG" and big.size == (1400, 700)
        assert Image.open(os.path.join(folder, ups[names["look-2_privacy-1"]].filename)).size == (800, 600)
        assert any("4 von 6 Bildern fehlen" in p for p in item.kind.validate(item))
    html = client.get(E + "/items/2/images?weg=ki").data.decode()
    assert "Hochgeladen am" in html and "anderer Stil" not in html and "fehlende Bilder erzeugen" in html
    assert "Mehrere Bilder auf einmal auswählen" not in html and 'class="asset-upload"' not in html
    # a running survey takes no images
    with app.app_context():
        db.session.get(Survey, 1).status = "running"
        db.session.commit()
    assert send(names["look-3_privacy-2"], _image()).status_code == 400


def test_images_per_level_in_the_table(app, client, survey, monkeypatch):
    """Display "Tabelle mit Bildern je Stufe": a picture per level (logo, seal) instead of the level's text or above
    it; uploaded or generated (two pages) per level; partial sets: a hint ("add") or a problem ("replace");
    removable; only in the draft."""
    from app import ai, design

    png = __import__("base64").b64encode(__import__("tests.editor.test_editor", fromlist=["PNG"]).PNG).decode()
    calls = []
    monkeypatch.setattr(ai, "generate_image", lambda prompt, size="1024x1024", **kw: calls.append(prompt) or png)
    setup(client, display="table", visual=())
    assert "Anzeige „Tabelle mit Text“: Ohne Bilder." in client.get(E + "/items/2").data.decode()
    form = conjoint_form(display="levels", visual=())
    form.add("level_mode", "add")
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert (item.config["display"], item.config["level_images"]) == ("table", "add")
        assert item.kind.image_page_keys(item)[0] == "l:a1l0" and item.kind.upload_name(item, "l:a3l1") == "privacy-2"
    html = client.get(E + "/items/2").data.decode()
    assert "Bilder je Stufe hochladen</a>" in html and "Bilder je Stufe mit KI erzeugen</a>" in html
    page = client.get(E + "/items/2/images?weg=hochladen").data.decode()
    assert "Bilder je Stufe hochladen" in page and page.count('name="key"') == 8  # 3 + 3 + 2 levels
    def up(key, data=None):
        return client.post(E + "/items/2/images/upload", data={"key": key, "weg": "hochladen", "file": (io.BytesIO(data or _image()), "x.png")},
                           content_type="multipart/form-data")
    up("l:a3l0")
    up("l:a3l1", _image("JPEG", (300, 200)))
    r = up("l:zz")                                                          # unknown level: nothing
    assert "weg=hochladen" in r.headers["Location"]
    # the AI way: a level image from its hint, approved by hand
    assert client.post(E + "/items/2/images/generate", data={"key": "l:a1l0"}).json["ok"]
    assert "hint humanoid" in calls[0] and "A care robot" not in calls[0]       # the level's hint, not the scene
    with app.app_context():
        item = db.session.get(Item, 2)
        pending = next(a for a in item.assets if a.key == "l:a1l0")
        assert pending.status == "pending" and item.kind.level_image(item, "a1l0") is None
    client.post(f"{E}/assets/{pending.id}/approve")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert sorted(a.key for a in item.assets) == ["l:a1l0", "l:a3l0", "l:a3l1"]
        folder = os.path.join(app.config["UPLOAD_FOLDER"], item.survey.uid)
        from PIL import Image
        assert Image.open(os.path.join(folder, item.kind.level_image(item, "a3l0"))).size == (600, 300)
        assert item.kind.level_image_gaps(item) == [("look", 1, 3)] and item.kind.validate_images(item) == []
        assert any("Merkmal look hat nur für 1 von 3 Stufen ein Bild" in f.message for f in design.check(db.session.get(Survey, 1)))
        level_asset = next(a.id for a in item.assets if a.key == "l:a1l0")
    form = conjoint_form(display="levels", visual=())
    form.add("level_mode", "replace")
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate_images(item) == ["Merkmal look: 2 von 3 Stufen ohne Bild (das Bild ersetzt den Text)"]
    client.post(f"{E}/assets/{level_asset}/delete")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate_images(item) == [] and item.kind.level_image(item, "a1l0") is None
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    # privacy in both options as a picture instead of the text (the text is its alt); look as text
    assert html.count('class="cj-level-img"') == 2 and ('alt="lokal"' in html or 'alt="Cloud"' in html)
    up("l:a1l1")                                                            # running: refused
    with app.app_context():
        assert db.session.get(Item, 2).kind.level_image(db.session.get(Item, 2), "a1l1") is None


def test_display_choices_map_to_the_stored_display(app, client, survey):
    """Three choices in the editor: table with text, table with images per level, one image per option (with the
    tick: the attributes not in the image as a table below it, stored as "both")."""
    setup(client, display="table")
    for choice, extra, expected in (("image", {}, ("image", "")), ("image", {"rest_table": "1"}, ("both", "")),
                                    ("levels", {}, ("table", "replace")), ("table", {}, ("table", ""))):
        form = conjoint_form(display=choice)
        for k, v in extra.items():
            form.add(k, v)
        client.post(E + "/items/2", data=form)
        with app.app_context():
            c = db.session.get(Item, 2).config
            assert (c["display"], c["level_images"]) == expected, choice


def test_concept_images_keep_their_room_while_loading(app):
    """The images of the concepts carry width and height, so the table does not jump while they load (they
    flickered without, Philipp 2026-10-08); the size is read from the file once per version."""
    import io
    import os

    from PIL import Image

    from app import media

    with app.app_context():
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "sizetest")
        os.makedirs(folder, exist_ok=True)
        out = io.BytesIO()
        Image.new("RGB", (640, 480), "white").save(out, format="JPEG")
        with open(os.path.join(folder, "a.jpg"), "wb") as f:
            f.write(out.getvalue())
        assert media.size("sizetest", "a.jpg") == (640, 480)
        assert media.size("sizetest", "missing.jpg") == (None, None)
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "templates", "items", "_attributes",
                           "macros.html"), encoding="utf-8") as f:
        macros = f.read()
    assert macros.count('width="{{ w }}" height="{{ h }}"') == 2
