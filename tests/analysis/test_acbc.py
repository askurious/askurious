import csv
import io
import re

from werkzeug.datastructures import MultiDict

from app import acbc_design
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
ATTRS = {"a1": ("look", "Aussehen", ["humanoid", "technisch", "tierartig"], [0, 0, 0]),
         "a2": ("speed", "Tempo", ["langsam", "mittel", "schnell"], [0, 50, 100]),
         "a3": ("privacy", "Datenschutz", ["lokal", "Cloud"], [30, 0]),
         "a4": ("voice", "Stimme", ["weiblich", "männlich", "neutral"], [0, 0, 10])}


def acbc_form(screens=3, per_screen=3, price=False, prohib=(), rules=False):
    rows = [("code", "robo"), ("question", "Welcher Roboter ist Ihr Favorit?"), ("screens", str(screens)),
            ("per_screen", str(per_screen)), ("min_changes", "1"), ("max_changes", "2"), ("tour_max", "8"),
            ("display", "table"), ("price_base", "1000"), ("price_variation", "10"), ("price_step", "10"),
            ("price_currency", "€")]
    if price:
        rows.append(("price_on", "1"))
    if rules:
        rows += [("rules", "1"), ("rules_after", "2"), ("max_nogo", "2"), ("max_must", "1"), ("price_cap", "1")]
    for aid, (code, label, levels, prices) in ATTRS.items():
        rows += [("attr_id", aid), ("attr_code", code), ("attr_label", label)]
        for n, (lv, pr) in enumerate(zip(levels, prices)):
            rows += [(f"lvl_{aid}_id", f"{aid}l{n}"), (f"lvl_{aid}_label", lv), (f"lvl_{aid}_hint", ""),
                     (f"lvl_{aid}_price", str(pr))]
    rows += [("prohib", p) for p in prohib]
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    add(client, "acbc")                                   # item 2
    client.post(E + "/items/2", data=acbc_form(**kw))


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


# --- design ------------------------------------------------------------------------------


def test_screening_concepts_stay_near_the_byo_profile():
    levels, byo = [3, 3, 2, 3], [0, 1, 0, 2]
    forbidden = [((0, 2), (1, 2))]
    concepts = acbc_design.screening_concepts(levels, byo, 12, 1, 2, forbidden=forbidden, seed=7)
    assert len(concepts) == 12 and len({tuple(c) for c in concepts}) == 12
    assert all(tuple(c) != tuple(byo) for c in concepts)
    assert all(1 <= sum(a != b for a, b in zip(c, byo)) <= 2 for c in concepts)
    assert not any(c[0] == 2 and c[1] == 2 for c in concepts)
    assert concepts == acbc_design.screening_concepts(levels, byo, 12, 1, 2, forbidden=forbidden, seed=7)


def test_tournament_needs_ceil_n_minus_one_half_tasks():
    assert [acbc_design.tournament_tasks(n) for n in range(6)] == [0, 0, 1, 1, 2, 2]
    rounds, _, winner = acbc_design.tournament([5, 1, 7, 2], {})
    assert rounds == [(0, [5, 1, 7], None)] and winner is None
    rounds, queue, winner = acbc_design.tournament([5, 1, 7, 2], {0: 7, 1: 2})
    assert [g for _, g, _ in rounds] == [[5, 1, 7], [2, 7]] and winner == 2
    assert acbc_design.tournament([3], {})[2] == 3 and acbc_design.tournament([], {})[2] is None


def test_summed_price():
    comps = [[0, 50], [30, 0]]
    assert acbc_design.summed_price(comps, [1, 0], 100) == 180
    import random
    prices = {acbc_design.summed_price(comps, [1, 0], 100, 0.2, 10, random.Random(n)) for n in range(40)}
    assert all(140 <= p <= 220 and p % 10 == 0 for p in prices) and len(prices) > 2


# --- editor and checks -------------------------------------------------------------------


def test_config_and_checks(app, client, survey):
    setup(client, price=True, prohib=("a1:a1l2|a2:a2l2",))
    with app.app_context():
        item = db.session.get(Item, 2)
        c = item.config
        assert [a["code"] for a in c["attributes"]] == ["look", "speed", "privacy", "voice"]
        assert c["screens"] == 3 and c["per_screen"] == 3 and c["price"]["on"] and c["price"]["base"] == 1000
        assert c["attributes"][1]["levels"][2]["price"] == 100 and not c["rules"]
        assert len(item.kind.prohibitions(item)) == 1
        assert item.kind.validate(item) == []
        assert item.kind.max_tour_tasks(item) == 4  # tour_max 8 of 9 concepts
    html = client.get(E + "/items/2").data.decode()
    assert "Preisanteil" in html and "Gesamtpreis anzeigen" in html and "Aufgaben je Person" in html and "2 · Design" not in html
    # more screening concepts than distinct profiles: blocked
    client.post(E + "/items/2", data=acbc_form(screens=12, per_screen=5))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("Zu wenige verschiedene Varianten" in p for p in item.kind.validate(item))


# --- participation -----------------------------------------------------------------------


def run_participant(app, uid, possible):
    """BYO, then the screening (concepts whose number is in `possible` are "possible"), then
    always the first option of each tournament task. Returns the tournament task pages seen."""
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Stellen Sie Ihre bevorzugte Variante zusammen" in html and 'name="i2_byo_a1"' in html
    p.post(f"/s/{uid}", data={"nav": "next", "i2_byo_a1": "a1l0", "i2_byo_a2": "a2l1", "i2_byo_a3": "a3l0",
                               "i2_byo_a4": "a4l2"})
    for screen in range(3):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Seite {screen + 1} von 3" in html
        form = {"nav": "next"}
        for c in range(screen * 3, screen * 3 + 3):
            assert f'name="i2_s{c}"' in html
            form[f"i2_s{c}"] = "1" if c in possible else "0"
        p.post(f"/s/{uid}", data=form)
    tour = []
    for _ in range(6):
        html = p.get(f"/s/{uid}").data.decode()
        if "Vielen Dank" in html:
            return tour
        assert "Schritt 3" in html
        import re
        values = re.findall(r'name="i2_r(\d+)" value="(\d+)"', html)
        tour.append(len(values))
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_r{values[0][0]}": values[0][1]})
    raise AssertionError("tournament did not end")


def test_participation_tournament_and_export(app, client, survey):
    setup(client, price=True)
    uid = start(app, client)
    assert run_participant(app, uid, possible={0, 2, 3, 5, 7}) == [3, 3]  # 5 possible: 2 tasks
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = r.answers["2"]
        assert a["byo"] == {"a1": "a1l0", "a2": "a2l1", "a3": "a3l0", "a4": "a4l2"}
        assert a["byo_price"] == 1000 + 0 + 50 + 30 + 10 and len(a["concepts"]) == 9
        assert sum(a["s"].values()) == 5 and len(a["t"]) == 2
        assert all(900 <= c["price"] <= 1300 for c in a["concepts"])
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert [wide[f"robo_byo_{c}"] for c in ("look", "speed", "privacy", "voice")] == ["1", "2", "1", "3"]
    assert wide["robo_n_possible"] == "5" and wide["robo_n_tour"] == "2" and wide["robo_win_look"] != ""
    assert wide["robo_byo_price"] in ("1090", "1090.0", "1090,0")
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    phases = [row["phase"] for row in long]
    assert phases.count("byo") == 1 and phases.count("screen") == 9 and phases.count("tour") == 3 + 3
    assert sum(int(row["chosen"]) for row in long if row["phase"] == "screen") == 5
    assert [sum(int(row["chosen"]) for row in long if row["phase"] == "tour" and row["task"] == t) for t in ("1", "2")] == [1, 1]
    assert "price" in long[0] and long[0]["look_label"] == "humanoid"


def test_no_or_one_possible_concept_skips_the_tournament(app, client, survey):
    setup(client)
    uid = start(app, client)
    assert run_participant(app, uid, possible=set()) == []
    assert run_participant(app, uid, possible={4}) == []
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(is_preview=False)).all()
        assert all(r.status == Response.COMPLETED for r in rows)
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    # no possible concept: the BYO profile counts as the winner; one possible: that one
    assert [w["robo_win_source"] for w in wide] == ["byo", "single"]
    assert [wide[0][f"robo_win_{c}"] for c in ("look", "speed", "privacy", "voice")] == ["1", "2", "1", "3"]


def test_changing_the_byo_profile_starts_over(app, client, survey):
    setup(client)
    with app.app_context():
        kind = db.session.get(Item, 2).kind
        old = {"seed": 1, "byo": {"a1": "a1l0"}, "concepts": [{"p": [1]}], "s": {"0": 1}, "t": {"0": 0}}
        assert kind.merge_answer(old, {"s": {"0": 1}}) == old                       # same screening: kept
        assert "t" not in kind.merge_answer(old, {"s": {"0": 0}})                   # changed: tournament again
        assert kind.merge_answer(old, {"seed": 1, "byo": {"a1": "a1l1"}, "concepts": [{"p": [2]}]}) == \
            {"seed": 1, "byo": {"a1": "a1l1"}, "concepts": [{"p": [2]}]}
        assert kind.merge_answer({**old, "t": {"0": 0, "1": 3}}, {"t": {"0": 2}})["t"] == {"0": 2}


def test_dummy_data_and_questionnaire(app, client, survey):
    setup(client, price=True)
    with app.app_context():
        from app import dummy, export

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 5, seed=3) == 5
        rows = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert all(isinstance(r.answers.get("2"), dict) and len(r.answers["2"]["concepts"]) == 9 for r in rows)
        md = export.questionnaire_md(s)
        assert "Adaptive: build your own" in md and "Summed price" in md


# --- rule questions ------------------------------------------------------------------------


def walk(app, uid, screen_answer, nogo=(), must="0", cap="none"):
    """Go through the whole item; screen_answer(concept) -> "1"/"0"; returns the pages seen."""
    import re

    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_byo_a1": "a1l0", "i2_byo_a2": "a2l1", "i2_byo_a3": "a3l0",
                               "i2_byo_a4": "a4l2"})
    seen = []
    for _ in range(40):
        html = p.get(f"/s/{uid}").data.decode()
        if "Vielen Dank" in html:
            return seen
        form = MultiDict({"nav": "next"})
        if 'name="i2_pc"' in html:
            seen.append("price")
            options = re.findall(r'name="i2_pc" value="([^"]+)"', html)
            form["i2_pc"] = options[0] if cap == "lowest" else "none"
        elif 'name="i2_ng"' in html:
            offered = re.findall(r'name="i2_ng" value="([^"]+)"', html)
            seen.append(("nogo", offered))
            form.setlist("i2_ng", [o for o in offered if o in nogo] or ["none"])
        elif 'name="i2_mh"' in html:
            level = re.search(r'name="i2_ml" value="([^"]+)"', html).group(1)
            seen.append(("must", level))
            form["i2_mh"], form["i2_ml"] = must, level
        elif 'name="i2_s' in html:
            shown = [int(c) for c in re.findall(r'name="i2_s(\d+)" value="1"', html)]
            hidden = [int(c) for c in re.findall(r'name="i2_s(\d+)" value="a"', html)]
            seen.append(("screen", shown, hidden))
            for c in shown:
                form[f"i2_s{c}"] = screen_answer(c)
            for c in hidden:
                form[f"i2_s{c}"] = "a"
        else:
            values = re.findall(r'name="i2_r(\d+)" value="(\d+)"', html)
            seen.append("tour")
            form[f"i2_r{values[0][0]}"] = values[0][1]
        p.post(f"/s/{uid}", data=form)
    raise AssertionError("did not end")


def kinds_of(seen):
    return [x if isinstance(x, str) else x[0] for x in seen]


def test_unacceptable_levels_are_asked_and_kept_out(app, client, survey):
    setup(client, rules=True, screens=4)
    uid = start(app, client)
    nogo = ("a1:a1l1", "a1:a1l2", "a2:a2l0", "a2:a2l2", "a4:a4l0", "a4:a4l1")
    seen = walk(app, uid, screen_answer=lambda c: "1" if c % 2 else "0", nogo=nogo)
    kinds = kinds_of(seen)
    assert kinds[:2] == ["screen", "screen"] and "nogo" in kinds and kinds.count("screen") == 4
    assert all("a1:a1l0" not in x[1] for x in seen if x[0] == "nogo")  # BYO levels are never offered
    with app.app_context():
        item = db.session.get(Item, 2)
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = r.answers["2"]
        plan = item.kind.plan(item, a)
        assert plan.nogo and a["k"] == 3
        # concepts after a question never show a level called unacceptable there (replaced or rejected);
        # a level named only at a later question may still appear in between
        for key, q in plan.questions.items():
            if q["phase"] != "nogo":
                continue
            named = {(int(ref.split(":")[0][1:]) - 1, int(ref.rsplit("l", 1)[1])) for ref in a["rules"][key]}
            assert all(plan.concepts[i].get("auto") or not any((j, lv) in named for j, lv in enumerate(plan.concepts[i]["p"]))
                       for i in range((q["after"] + 1) * 3, len(plan.concepts)))
        assert all(not any((j, lv) in plan.nogo for j, lv in enumerate(plan.concepts[c]["p"]))
                   for c in item.kind.possible(item, a))
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert "1" in [wide[k] for k in wide if k.startswith("robo_nogo_")]
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert any(row["phase"] == "nogo" and row["chosen"] == "1" for row in long)


def test_must_have_and_price_cap(app, client, survey):
    setup(client, rules=True, price=True, screens=4)
    uid = start(app, client)
    seen = walk(app, uid, screen_answer=lambda c: "1", must="1", cap="lowest")
    kinds = kinds_of(seen)
    assert "price" in kinds
    with app.app_context():
        item = db.session.get(Item, 2)
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = r.answers["2"]
        plan = item.kind.plan(item, a)
        assert plan.cap is not None
        assert all(plan.concepts[c]["price"] <= plan.cap for c in item.kind.possible(item, a))
        if "must" in kinds:
            assert plan.must
            assert all(plan.concepts[c]["p"][j] == lv for c in item.kind.possible(item, a) for j, lv in plan.must.items())
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["robo_price_cap"] not in ("", None)


def test_must_have_question_for_a_level_in_every_possible_concept(app, client, survey):
    setup(client, rules=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        # screens 1 and 2 answered: every possible concept has look = technisch (a1l1)
        concepts = [{"p": [1, 0, 0, 0]}, {"p": [1, 2, 1, 0]}, {"p": [0, 1, 1, 1]},
                    {"p": [2, 1, 0, 0]}, {"p": [1, 1, 1, 2]}, {"p": [0, 2, 0, 1]},
                    {"p": [0, 0, 1, 0]}, {"p": [2, 0, 0, 1]}, {"p": [1, 2, 1, 1]}]
        answer = {"seed": 1, "k": 3, "byo": {"a1": "a1l0", "a2": "a2l1", "a3": "a3l0", "a4": "a4l2"},
                  "concepts": concepts, "s": {"0": 1, "1": 1, "2": 0, "3": 0, "4": 1, "5": 0}}
        plan = kind.plan(item, answer)
        assert plan.questions["m1"]["level"] == (0, 1)
        nogo = plan.questions["n1"]["options"]
        assert (0, 0) not in nogo and (0, 2) in nogo  # BYO level never, "tierartig" only rejected
        answer["rules"] = {"n1": [], "m1": {"level": "a1:a1l1", "yes": True}}
        plan = kind.plan(item, answer)
        assert plan.must == {0: 1}
        # the third screen shows only concepts with the must-have level (replaced or rejected)
        assert all(x.get("auto") or x["p"][0] == 1 for x in plan.concepts[6:])
        assert kind.page_applies(item, {"phase": "must", "after": 1}, {2: answer})
        assert not kind.page_applies(item, {"phase": "price", "after": 1}, {2: answer})  # no price


def test_changed_rule_drops_later_answers(app, client, survey):
    setup(client, rules=True)
    with app.app_context():
        kind = db.session.get(Item, 2).kind
        old = {"k": 3, "concepts": [{"p": [0]}] * 9, "s": {str(i): 1 for i in range(9)},
               "rules": {"n1": ["a1:a1l1"], "m3": {"level": "a2:a2l0", "yes": True}}, "t": {"0": 1}}
        assert kind.merge_answer(old, {"rules": {"n1": ["a1:a1l1"]}, "_after": 1}) == old
        new = kind.merge_answer(old, {"rules": {"n1": []}, "_after": 1})
        assert new["rules"] == {"n1": []} and "t" not in new and sorted(new["s"], key=int) == [str(i) for i in range(6)]
        changed = kind.merge_answer(old, {"s": {"0": 0}, "_screen": 0})
        assert changed["rules"] == {} and "t" not in changed and "_screen" not in changed


def test_dummy_data_with_rules(app, client, survey):
    setup(client, rules=True, price=True, screens=4)
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), 8, seed=5) == 8
        rows = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert any(r.answers["2"].get("rules") for r in rows)
        assert all(len(r.answers["2"]["s"]) == 12 for r in rows)


# --- open answers (optional and nudge mode) ----------------------------------------------------

BYO = {"a1": "a1l0", "a2": "a2l1", "a3": "a3l0", "a4": "a4l2"}
CONCEPTS = [{"p": [1, 0, 0, 0]}, {"p": [1, 2, 1, 0]}, {"p": [0, 1, 1, 1]},
            {"p": [2, 1, 0, 0]}, {"p": [1, 1, 1, 2]}, {"p": [0, 2, 0, 1]},
            {"p": [0, 0, 1, 0]}, {"p": [2, 0, 0, 1]}, {"p": [1, 2, 1, 1]}]


def evaluate(item, form, meta, ack=False):
    from app import runtime

    return runtime.evaluate([item], MultiDict({"nav": "next", **form}), "de", nudge_acknowledged=ack, meta=meta)


def set_mode(item, mode):
    item.answer_mode = mode
    db.session.commit()


def test_rule_question_left_open_is_no_information(app, client, survey):
    from types import SimpleNamespace

    setup(client, rules=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        nogo, must = {"phase": "nogo", "after": 1}, {"phase": "must", "after": 1}
        # optional: an empty form is stored as None, unlike "none of these" / "no"
        res = evaluate(item, {}, nogo)
        assert res.ok and res.answers[2]["rules"] == {"n1": None} and kind.is_missing(item, res.answers[2])
        res = evaluate(item, {"i2_ng": "none"}, nogo)
        assert res.answers[2]["rules"] == {"n1": []} and not kind.is_missing(item, res.answers[2])
        assert evaluate(item, {"i2_ml": "a1:a1l1"}, must).answers[2]["rules"] == {"m1": None}
        assert evaluate(item, {"i2_ml": "a1:a1l1", "i2_mh": "0"}, must).answers[2]["rules"]["m1"]["yes"] is False
        assert evaluate(item, {}, {"phase": "price", "after": 1}).answers[2]["rules"] == {"p1": None}
        assert evaluate(item, {"i2_pc": "none"}, {"phase": "price", "after": 1}).answers[2]["rules"] == {"p1": {"cap": None}}
        # nudge: the hint comes once, then the open question is accepted
        set_mode(item, "nudge")
        assert 2 in evaluate(item, {}, nogo).nudges and 2 in evaluate(item, {"i2_ml": "a1:a1l1"}, must).nudges
        assert evaluate(item, {}, nogo, ack=True).ok
        assert evaluate(item, {"i2_ng": "none"}, nogo).ok
        set_mode(item, "required")
        assert 2 in evaluate(item, {}, nogo).errors and 2 in evaluate(item, {"i2_ml": "a1:a1l1"}, must).errors
        # plan and export: left open = no rule, not "offered and declined"
        answer = {"seed": 1, "k": 3, "byo": BYO, "concepts": CONCEPTS, "s": {"0": 1, "1": 1, "2": 0, "3": 0, "4": 1, "5": 0}}
        skipped = {**answer, "rules": {"n1": None, "m1": None}}
        plan = kind.plan(item, skipped)
        assert {"n1", "m1"} <= set(plan.questions) and not plan.nogo and not plan.must
        assert kind.page_applies(item, {"phase": "tour", "round": 0}, {2: {**skipped, "s": {str(i): 1 for i in range(9)}}})
        out = kind.export_values(item, skipped)
        assert all(v is None for k, v in out.items() if k.startswith(("robo_nogo_", "robo_must_")))
        declined = kind.export_values(item, {**answer, "rules": {"n1": [], "m1": {"level": "a1:a1l1", "yes": False}}})
        assert declined["robo_must_look"] == 0 and declined["robo_nogo_look_3"] == 0
        response = SimpleNamespace(answers={"2": skipped})
        assert not [row for row in kind.long_rows(item, response) if row[0] in ("nogo", "must")]
        from app import acbc_analysis

        cells = acbc_analysis.analyse(item, [skipped], "de")["attributes"][0]["levels"]
        assert all(c["nogo_offered"] == 0 and c["must_asked"] == 0 for c in cells)


def test_byo_left_open_is_filled_and_flagged(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        byo = {"phase": "byo", "task": 0, "seed": 5}
        res = evaluate(item, {"i2_byo_a1": "a1l2"}, byo)                   # optional: filled at random
        a = res.answers[2]
        assert res.ok and a["byo"]["a1"] == "a1l2" and all(a["byo"].values()) and a["byo_filled"] == ["a2", "a3", "a4"]
        assert kind.byo_profile(item, a) and len(a["concepts"]) == 9
        assert kind.export_values(item, a)["robo_byo_filled"] == 3
        full = evaluate(item, {f"i2_byo_{k}": v for k, v in BYO.items()}, byo).answers[2]
        assert full["byo_filled"] == [] and kind.export_values(item, full)["robo_byo_filled"] == 0
        assert kind.export_values(item, {})["robo_byo_filled"] is None
        set_mode(item, "nudge")
        assert 2 in evaluate(item, {}, byo).nudges and not evaluate(item, {}, byo).errors
        res = evaluate(item, {}, byo, ack=True)                            # after the hint: like optional
        assert res.ok and len(res.answers[2]["byo_filled"]) == 4
        set_mode(item, "required")
        assert 2 in evaluate(item, {}, byo).errors
        set_mode(item, "nudge")
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2_byo_a1": "a1l0"}).data.decode()
    assert 'name="nudge_ack"' in html and 'value="a1l0" data-price="0" checked' in html
    assert not re.search(r'name="i2_byo_a2" value="[^"]+" data-price="[^"]*" checked', html)  # filled ones stay open
    p.post(f"/s/{uid}", data={"nav": "next", "i2_byo_a1": "a1l0", "nudge_ack": "1"})
    assert 'name="i2_s0"' in p.get(f"/s/{uid}").data.decode()
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))[0]
    assert wide["robo_byo_filled"] == "3" and wide["robo_byo_look"] == "1"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "robo_byo_filled" in book and "zufällig gefüllt" in book
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        cols, _, meta = export._spss_dictionary(s, True, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert "robo_byo_filled" in dict(cols) and meta["robo_byo_filled"]["level"] == "SCALE"


def test_design_locked_once_real_answers_exist(app, client, survey):
    setup(client)
    uid = start(app, client)
    run_participant(app, uid, possible={0, 2})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        before = db.session.get(Item, 2).config
    form = acbc_form(screens=5)
    form.setlist("lvl_a1_label", ["Humanoid", "technisch", "tierartig"])        # wording: allowed
    form.setlist("attr_id", ["a1", "a2", "a3"])                                   # dropping an attribute: refused
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["attributes"] == before["attributes"] and item.config["screens"] == 3
        assert item.kind.attributes(item)[0]["level_labels"][0] == "Humanoid"


# --- codebook, columns and checks against what was offered ---------------------------------


def test_codebook_has_value_labels(app, client, survey):
    from app import export

    setup(client, rules=True, price=True)
    with app.app_context():
        book = {row[0]: row for row in export.codebook(db.session.get(Survey, 1))}
        assert book["robo_byo_look"][3] == "1 = humanoid; 2 = technisch; 3 = tierartig" == book["robo_win_look"][3]
        assert book["robo_must_privacy"][3] == "0 = gefragt, verneint; 1 = lokal; 2 = Cloud"
        assert book["robo_nogo_speed_3"][3] == "1 = unakzeptabel; 0 = angeboten, nicht gewählt"
        assert "„schnell“" in book["robo_nogo_speed_3"][4] and "tour" in book["robo_win_source"][3]
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "de": {**item.texts["de"], "levels": {**item.texts["de"]["levels"], "a3l1": "Cloud; EU"}}}
        values = item.kind.codebook_info(item, "robo_byo_privacy", "de")[1]
        assert values == "1 = lokal; 2 = Cloud, EU"                       # ";" would split the label for SPSS
        assert re.findall(r"(-?\d+(?:\.\d+)?)\s*=\s*([^;]+)", values) == [("1", "lokal"), ("2", "Cloud, EU")]
        db.session.rollback()


def test_rule_questions_that_never_come_give_no_columns_and_a_warning(app, client, survey):
    setup(client, rules=True, screens=2)                              # rules_after = 2: no gap left
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        assert kind.rule_gaps(item) == [] and any("Rückfragen erscheinen nie" in p for p in kind.validate(item))
        cols = [c for c, _ in kind.export_columns(item)]
        assert not [c for c in cols if "_nogo_" in c or "_must_" in c or c.endswith("_price_cap")]
        assert set(kind.export_values(item, {})) == set(cols)
    client.post(E + "/items/2", data=acbc_form(rules=True, screens=3, price=True))
    with app.app_context():
        item = db.session.get(Item, 2)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert item.kind.validate(item) == [] and {"robo_price_cap", "robo_nogo_look_1", "robo_must_look"} <= set(cols)


def test_answers_must_be_among_what_was_offered(app, client, survey):
    setup(client, rules=True, price=True, screens=4)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", **{f"i2_byo_{k}": v for k, v in BYO.items()}})
    refused, target = [], None

    def refuse(form):
        """A manipulated form stays on the page with an error and stores nothing."""
        with app.app_context():
            before = db.session.scalar(db.select(Response).filter_by(is_preview=False)).answers["2"]
        html = p.post(f"/s/{uid}", data=MultiDict({"nav": "next", **form})).data.decode()
        with app.app_context():
            after = db.session.scalar(db.select(Response).filter_by(is_preview=False)).answers["2"]
        assert "Ungültige Auswahl" in html and before == after

    for _ in range(40):
        html = p.get(f"/s/{uid}").data.decode()
        if "Vielen Dank" in html:
            break
        form = MultiDict({"nav": "next"})
        if 'name="i2_pc"' in html:
            refuse({"i2_pc": "1"})                                    # not one of the offered limits
            refused.append("price")
            form["i2_pc"] = "none"                                    # no limit: keeps the tournament field
        elif 'name="i2_ng"' in html:
            offered = re.findall(r'name="i2_ng" value="([^"]+)"', html)
            assert "a1:a1l0" not in offered
            refuse({"i2_ng": "a1:a1l0"})                              # the BYO level is never offered
            refused.append("nogo")
            form.setlist("i2_ng", offered[:1])
        elif 'name="i2_mh"' in html:
            level = re.search(r'name="i2_ml" value="([^"]+)"', html).group(1)
            refuse({"i2_mh": "1", "i2_ml": "a4:a4l1" if level != "a4:a4l1" else "a4:a4l0"})
            refused.append("must")
            form["i2_mh"], form["i2_ml"] = "0", level
        elif 'name="i2_s' in html:
            # The concepts differ per response (random seed), so the answers follow what is shown: every
            # concept with one level of the first concept that is not in the BYO profile is rejected, all
            # others are possible. That level is then seen only in rejected concepts, so the question
            # about unacceptable levels is certain to come, and enough concepts stay for a tournament.
            with app.app_context():
                item = db.session.get(Item, 2)
                answer = db.session.scalar(db.select(Response).filter_by(is_preview=False)).answers["2"]
                concepts = item.kind.plan(item, answer).concepts
                byo = item.kind.byo_profile(item, answer)
            if target is None:
                target = next((a, lv) for a, lv in enumerate(concepts[0]["p"]) if lv != byo[a])
            for c in re.findall(r'name="i2_s(\d+)" value="1"', html):
                form[f"i2_s{c}"] = "0" if concepts[int(c)]["p"][target[0]] == target[1] else "1"
            for c in re.findall(r'name="i2_s(\d+)" value="a"', html):
                form[f"i2_s{c}"] = "a"
        else:
            values = re.findall(r'name="i2_r(\d+)" value="(\d+)"', html)
            outside = next(str(n) for n in range(1, 13) if str(n) not in [v for _, v in values])
            refuse({f"i2_r{values[0][0]}": outside})                  # a concept that is not in this task
            refused.append("tour")
            form[f"i2_r{values[0][0]}"] = values[0][1]
        p.post(f"/s/{uid}", data=form)
    else:
        raise AssertionError("did not end")
    assert {"price", "nogo", "tour"} <= set(refused)
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        item = db.session.get(Item, 2)
        assert r.status == Response.COMPLETED and item.kind.winner(item, r.answers["2"])[1] == "tour"
        # outside the page flow the stored answer is unknown: nothing to check against (see evaluate() above)
        assert item.kind.stored_answer({"phase": "tour", "round": 0}) is None


def test_changed_screening_drops_later_screens_whose_concepts_may_change(app, client, survey):
    setup(client, rules=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        # screens 1 and 2 answered, "tierartig" (only in rejected concepts) confirmed as unacceptable:
        # concept 8 (tierartig) is replaced on the third screen, and the replacement is judged there
        base = {"seed": 1, "k": 3, "byo": BYO, "concepts": CONCEPTS, "s": {"0": 1, "1": 1, "2": 0, "3": 0, "4": 1, "5": 0}}
        answer = {**base, "rules": {"n1": ["a1:a1l2"], "m1": {"level": "a1:a1l1", "yes": False}}}
        judged = kind.plan(item, answer).concepts[7]
        assert judged.get("r") and judged["p"] != CONCEPTS[7]["p"]
        answer["s"] = {**answer["s"], "6": 0, "7": 1, "8": 0}
        # back on the second screen: the tierartig concept is possible after all, so the question about
        # it is not asked again and the third screen shows the original concept at position 8
        change = {"s": {"3": 1, "4": 1, "5": 0}, "_screen": 1}
        kept = {**answer, "s": {**answer["s"], **change["s"]}, "rules": {}}       # what merge_answer did before
        assert kind.plan(item, kept).concepts[7]["p"] == CONCEPTS[7]["p"] != judged["p"]  # "7": 1 was another concept
        merged = kind.merge_answer(answer, change)
        assert merged["rules"] == {} and sorted(merged["s"], key=int) == [str(i) for i in range(6)]
        assert merged["s"]["3"] == 1
        # without rules after the changed screen the later concepts cannot change: their judgements stay
        plain = {**base, "s": {str(i): 1 for i in range(9)}, "t": {"0": 1}}
        merged = kind.merge_answer(plain, {"s": {"0": 0, "1": 1, "2": 0}, "_screen": 0})
        assert len(merged["s"]) == 9 and merged["s"]["0"] == 0 and "t" not in merged


def test_image_button_on_the_display_tab(app, client, survey):
    """As with the CBC, the button to the images sits on "Darstellung & Bilder", not under the flow figures."""
    setup(client)
    form = acbc_form()
    form["display"] = "levels"
    client.post(E + "/items/2", data=form)
    html = client.get(E + "/items/2").data.decode()
    display = re.search(r'<section class="panel" data-cj-tab="display">(.*?)</section>', html, re.S).group(1)
    design = re.search(r'<section class="panel" id="design" data-cj-tab="design">(.*?)</section>', html, re.S).group(1)
    assert "Bilder je Stufe" in display and "/images" not in design
