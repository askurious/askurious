"""Splitting the results by group variables (app/groupvars.py): randomizers, questions, quotas, link parameters."""
from app import compare, flow, groupvars
from app.extensions import db
from app.models import Block, Quota, QuotaCell, Response, Survey
from tests.analysis.test_compare import ROWS, setup


def randomized(app, client, rows, k=1, three=False):
    """The survey of test_compare plus blocks 3 and 4 (and 5) drawn by a randomizer with the code "bed"; rows:
    [(drawn block ids, group code of item 2, s1, s2)]."""
    setup(app, client, [])
    with app.app_context():
        s = db.session.get(Survey, 1)
        titles = ["Nutzen", "Risiken"] + (["Neutral"] if three else [])
        for n, title in enumerate(titles):
            s.blocks.append(Block(position=len(s.blocks) + n, titles={"de": title}))
        db.session.flush()
        ids = [b.id for b in s.blocks if (b.titles or {}).get("de") in titles]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "rand", "id": "r1", "code": "bed", "name": "Text", "k": k, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": i} for i in ids]}]}
        flow.normalize(s)
        from datetime import datetime, timedelta

        t0 = datetime(2026, 9, 1, 10)
        for drawn, g, a, b in rows:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": g, "3": {"s1": a, "s2": b}},
                                        layout={"conditions": {"r1": [f"k{ids[d]}" for d in drawn]}}))
        db.session.commit()
        return ids


def test_randomizer_offered_and_split_as_an_experiment(app, client, survey):
    rows = ([((0,), "1", a, b) for a, b in ((1, 2), (2, 2), (2, 3), (1, 1), (3, 2), (2, 1))]
            + [((1,), "2", a, b) for a, b in ((4, 5), (5, 4), (4, 4), (3, 4), (5, 5), (4, 3))])
    randomized(app, client, rows)
    html = client.get("/surveys/1/results").data.decode()
    assert '<optgroup label="Versuchsbedingungen">' in html and '<option value="rand:bed" >' in html
    assert html.index("Versuchsbedingungen") < html.index('<optgroup label="Fragen">')
    html = client.get("/surveys/1/results?gruppen=rand:bed").data.decode()
    assert 'value="rand:bed" selected' in html and "Nutzen (6)" in html and "Risiken (6)" in html
    assert "Die Gruppen sind zufällig zugeteilt" in html and "Zuteilung unauffällig" in html
    assert "Welch t(" in html and "Fabijan, A." in html
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        table = compare.table_of(s, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        cmp = compare.compute(s, compare.chosen(s, "rand:bed"), results.compute(s, drop_speeders=True), table)
        assert [(g["label"], g["n"]) for g in cmp["groups"]] == [("Nutzen", 6), ("Risiken", 6)]
        assert cmp["experiment"] and not cmp["assignment"]["warn"] and cmp["assignment"]["completion"]["all_done"]
        assert cmp["assignment"]["ratio"] is None             # balanced: the started are equal by construction
        assert 2 in cmp["items"]                     # the question is compared too: it is not the grouping


def unfinished(app, drawn):
    """Unfinished participations (still active) that were given these conditions (index of the drawn block)."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = [b.id for b in s.blocks if (b.titles or {}).get("de") in ("Nutzen", "Risiken", "Neutral")]
        for d in drawn:
            s.responses.append(Response(status=Response.ACTIVE, answers={}, layout={"conditions": {"r1": [f"k{ids[d]}"]}}))
        s.responses.append(Response(status=Response.DECLINED, answers={}, layout={"conditions": {"r1": [f"k{ids[0]}"]}}))
        db.session.commit()


def test_unequal_conditions_are_warned_about(app, client, survey):
    # a randomizer drawing by chance alone: the started against equal shares as well (audit 2026-10-06, E-3)
    rows = [((0,), "1", 1, 2)] * 30 + [((1,), "2", 4, 4)] * 10
    randomized(app, client, rows)
    with app.app_context():
        s = db.session.get(Survey, 1)
        import copy

        tree = copy.deepcopy(s.flow)
        tree["children"][2]["balanced"] = False
        s.flow = tree
        db.session.commit()
        check = groupvars.assignment_check(groupvars.get(s, "rand:bed"), s)
        assert check["ratio"]["warn"] and check["ratio"]["n"] == 40 and check["completion"]["all_done"]
    html = client.get("/surveys/1/results?gruppen=rand:bed").data.decode()
    assert "Zuteilung prüfen:" in html and "χ²(1, N = 40) = 10,00" in html and "Fehler in der Zuteilung" in html
    assert "teilt rein zufällig zu" in html


def test_balanced_randomizer_checks_completion_per_condition(app, client, survey):
    """Audit 2026-10-06, E-3 (decision 4): a balanced randomizer fills the started equally; drop-outs that depend on
    the condition show in the completion per condition (k x 2 among the started), not in the sizes of the groups."""
    rows = [((0,), "1", 1, 2)] * 18 + [((1,), "2", 4, 4)] * 8
    randomized(app, client, rows)
    unfinished(app, [0] * 2 + [1] * 12)                     # 20 started each: 18 and 8 completed
    with app.app_context():
        s = db.session.get(Survey, 1)
        var = groupvars.get(s, "rand:bed")
        assert groupvars.started_by_condition(var, s) == {"Nutzen": [20, 18], "Risiken": [20, 8]}  # declined: no start
        check = groupvars.assignment_check(var, s)
        assert check["balanced"] and check["ratio"] is None and check["warn"]
        c = check["completion"]
        assert c["df"] == 1 and c["n"] == 40 and c["exact"] is None and c["p"] < groupvars.SRM_ALPHA
        assert abs(c["chi2"] - 40 * (18 * 12 - 2 * 8) ** 2 / (20 * 20 * 26 * 14)) < 1e-9
    html = client.get("/surveys/1/results?gruppen=rand:bed").data.decode()
    assert "Zuteilung prüfen:" in html and "hängt von der Versuchsbedingung ab" in html
    assert "Nutzen 18/20, Risiken 8/20" in html and "Der Randomisierer gleicht aus" in html
    assert "so gleich besetzt, wie es der Zufall" not in html


def test_a_filter_on_the_condition_raises_no_alarm(app, client, survey):
    """Audit 2026-10-06, E-4: "Nur zeigen" on one condition and split by the same randomizer: the check counts the
    whole pool, so the empty other condition is no sample ratio mismatch."""
    rows = [((0,), "1", 1, 2)] * 8 + [((1,), "2", 4, 4)] * 8
    randomized(app, client, rows)
    html = client.get("/surveys/1/results?gruppen=rand:bed&nur_var=rand:bed&nur=Nutzen").data.decode()
    assert "Nur ein Teil der Daten:" in html and "Zuteilung prüfen" not in html and "Zuteilung unauffällig" in html


def test_randomizer_drawing_several_splits_per_element(app, client, survey):
    rows = [((0, 1), "1", 1, 2)] * 6 + [((1, 2), "2", 4, 4)] * 6 + [((0, 2), "1", 3, 3)] * 6
    ids = randomized(app, client, rows, k=2, three=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = groupvars.all_of(s)
        assert "rand:bed" not in found
        var = found[f"rand:bed:k{ids[0]}"]
        assert var.groups == [("1", "gezeigt"), ("0", "nicht gezeigt")] and var.experiment
        assert groupvars.assignment_check(var, {"1": 12, "0": 6}) is None   # no equal shares to expect
        table = compare.table_of(s, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        assert [var.member(r) for r in table].count("1") == 12
    html = client.get(f"/surveys/1/results?gruppen=rand:bed:k{ids[0]}").data.decode()
    assert "gezeigt (12)" in html and "nicht gezeigt (6)" in html


def test_question_split_is_not_called_an_effect(app, client, survey):
    setup(app, client, ROWS)
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Die Gruppen haben sich nicht zufällig gebildet" in html and "Zuteilung" not in html
    assert "Fabijan" not in html


def test_quota_cells_and_link_parameters(app, client, survey):
    setup(app, client, ROWS)
    with app.app_context():
        s = db.session.get(Survey, 1)
        q = Quota(survey_id=1, code="alter", name="Alter")
        q.cells = [QuotaCell(code="jung", name="unter 40", target=10), QuotaCell(code="alt", name="40 und älter", target=10)]
        db.session.add(q)
        s.panel = {"params": [{"name": "quelle", "mode": "store", "allowed": ["uni", "panel"]},
                              {"name": "PID", "mode": "store", "once": True, "allowed": ["a", "b"]},
                              {"name": "weiter", "mode": "redirect", "allowed": ["x", "y"]},
                              {"name": "viele", "mode": "store", "allowed": [str(n) for n in range(9)]}],
                   "redirects": {}, "rules": []}
        db.session.flush()
        for n, r in enumerate(s.responses):
            r.params = {"quelle": "uni" if n % 2 else "panel"}
            r.quota = {str(q.id): {"cell": q.cells[n % 2].id, "res": "in"}}
        db.session.commit()
        found = groupvars.all_of(s)
        assert set(found) == {"2", "quota:alter", "url:quelle"}   # no once-only, redirect-only or 9-valued parameter
        assert found["quota:alter"].groups == [("jung", "unter 40"), ("alt", "40 und älter")]
        assert [sec for sec, _ in groupvars.choices(s)] == ["Fragen", "Quoten", "Link-Parameter"]
    html = client.get("/surveys/1/results?gruppen=url:quelle").data.decode()
    assert 'value="url:quelle" selected' in html and "uni (" in html and "panel (" in html
    html = client.get("/surveys/1/results?gruppen=quota:alter").data.decode()
    assert "unter 40 (" in html and "40 und älter (" in html


def test_unknown_keys_are_ignored(app, client, survey):
    setup(app, client, ROWS)
    for key in ("rand:nichts", "quota:x", "url:y", "99", "<script>"):
        html = client.get(f"/surveys/1/results?gruppen={key}").data.decode()
        assert "Nach Gruppen (" not in html and "Welch t(" not in html


# --- numbers in classes and multiple choice options (stage 2) ---------------------------------------------------------

def with_number_and_choice(app, client, code="alter"):
    """The survey of test_compare plus a whole number (item 4) and a multiple choice question (item 5)."""
    from werkzeug.datastructures import MultiDict
    from tests.conftest import add

    setup(app, client, ROWS)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/4", data={"code": code, "question": "Wie alt?", "validation": "integer",
                                                   "max_length": "3"})
    add(client, "multiple_choice")
    client.post("/surveys/1/editor/items/5", data=MultiDict([
        ("code", "medien"), ("question", "Welche Medien?"), ("option_id", ""), ("option_code", "1"), ("option_label", "Zeitung"),
        ("option_id", ""), ("option_code", "2"), ("option_label", "Radio")]))
    ages = [17, 22, 25, 29, 31, 38, 44, 47, 52, 58, 61, 66, 70, 75, 80]
    with app.app_context():
        s = db.session.get(Survey, 1)
        for n, r in enumerate(sorted(s.responses, key=lambda r: r.id)):
            r.answers = {**r.answers, "4": str(ages[n % len(ages)]), "5": ["1"] if n % 3 else ["1", "2"]}
        db.session.commit()


def test_bounds_text_and_classes():
    assert groupvars.clean_bounds("18; 30; 45; 60") == [18, 30, 45, 60]
    assert groupvars.clean_bounds("60 18 30") == [18, 30, 60]
    assert groupvars.clean_bounds("2,5; 7,5") == [2.5, 7.5]
    assert groupvars.clean_bounds("10,20,30") == [10, 20, 30]
    assert groupvars.clean_bounds("abc; ") is None and groupvars.clean_bounds("") is None
    assert len(groupvars.clean_bounds(" ".join(str(n) for n in range(20)))) == groupvars.MAX_BOUNDS
    assert groupvars.class_groups([18, 30]) == [("k0", "unter 18"), ("k1", "18 bis unter 30"), ("k2", "30 und mehr")]
    assert [groupvars.class_of([18, 30], v) for v in (17, 18, "29,5", 30, "x", None)] == ["k0", "k1", "k1", "k2", None, None]
    assert groupvars.tertile_bounds(list(range(0, 100))) == [30, 70]
    assert groupvars.tertile_bounds([5, 5, 5]) is None


def test_age_split_into_the_usual_bands(app, client, survey):
    with_number_and_choice(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert '<optgroup label="Zahlen in Klassen">' in html and 'value="klassen:4"' in html
    assert '<optgroup label="Mehrfachauswahl">' in html and 'value="mc:5:1"' in html
    html = client.get("/surveys/1/results?gruppen=klassen:4").data.decode()
    assert "unter 18 (" in html and "18 bis unter 30 (" in html and "60 und mehr (" in html
    assert "Grenzen der Klassen" in html and 'value="18; 30; 45; 60"' in html and "übliche Altersstufen" in html
    assert "MacCallum, R. C." in html and "Korrelation der bessere Test" in html


def test_saved_bounds_and_data_bounds(app, client, survey):
    with_number_and_choice(app, client, code="jahre")       # no age: bounds from the data
    html = client.get("/surveys/1/results?gruppen=klassen:4").data.decode()
    assert "Terzilen" in html
    client.post("/surveys/1/results/classes", data={"item": "4", "grenzen": "40; 60"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.data_pool["bins"] == {"4": [40, 60]}
    html = client.get("/surveys/1/results?gruppen=klassen:4").data.decode()
    assert "unter 40 (" in html and "40 bis unter 60 (" in html and "60 und mehr (" in html
    assert "gespeichert für diese Befragung" in html
    # saving the data pool keeps the bounds; an empty field takes them back
    client.post("/surveys/1/pool", data={"source": "real"})
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool["bins"] == {"4": [40, 60]}
    client.post("/surveys/1/results/classes", data={"item": "4", "grenzen": ""})
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool["bins"] == {}
    assert client.post("/surveys/1/results/classes", data={"item": "2", "grenzen": "1"}).status_code == 404


def test_multiple_choice_option_split(app, client, survey):
    with_number_and_choice(app, client)
    html = client.get("/surveys/1/results?gruppen=mc:5:2").data.decode()
    assert "angekreuzt (" in html and "nicht angekreuzt (" in html
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        table = compare.table_of(s, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        cmp = compare.compute(s, compare.chosen(s, "mc:5:2"), results.compute(s, drop_speeders=True), table)
        assert 5 not in cmp["items"] and 3 in cmp["items"]   # not compared with itself


# --- "Nur zeigen" (stage 3) -----------------------------------------------------------------------------------------

def test_filter_shows_one_condition_and_keeps_the_hypotheses_whole(app, client, survey):
    rows = ([((0,), "1", a, b) for a, b in ((1, 2), (2, 2), (2, 3), (1, 1), (3, 2), (2, 1))]
            + [((1,), "2", a, b) for a, b in ((4, 5), (5, 4), (4, 4), (3, 4), (5, 5), (4, 3))])
    randomized(app, client, rows)
    html = client.get("/surveys/1/results?nur_var=rand:bed").data.decode()      # chosen, nothing ticked yet
    assert "Gruppen ankreuzen" in html and 'name="nur" value="Nutzen"' in html and "Nur ein Teil der Daten" not in html
    html = client.get("/surveys/1/results?nur_var=rand:bed&nur=Nutzen&gruppen=2").data.decode()
    assert "Nur ein Teil der Daten:" in html and "(6 Teilnahmen)" in html and "Teilgruppenfilter entfernen" in html
    assert 'name="nur" value="Nutzen" checked' in html
    assert "Grundlage: <strong>6</strong>" in html
    # the filter is a view: nothing kept under it, the unfiltered page is unchanged
    assert "Grundlage: <strong>12</strong>" in client.get("/surveys/1/results?go=1").data.decode()
    # unknown groups are ignored, so is an unknown variable
    assert "Nur ein Teil der Daten" not in client.get("/surveys/1/results?nur_var=rand:bed&nur=x&go=1").data.decode() \
        or "(0 Teilnahmen)" in client.get("/surveys/1/results?nur_var=rand:bed&nur=x&go=1").data.decode()
    assert "Nur ein Teil der Daten" not in client.get("/surveys/1/results?nur_var=nix&nur=1&go=1").data.decode()


def test_hypotheses_use_the_whole_pool_under_a_filter(app, client, survey, monkeypatch):
    from app import hypotheses

    rows = ([((0,), "1", 1, 1)] * 8 + [((1,), "2", 5, 5)] * 8)
    randomized(app, client, rows)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.study_plan = {"hypotheses": [{"id": "h1", "text": "Nutzen wirkt", "kind": "causal", "iv": "bed", "dv": "TA"}]}
        db.session.commit()
    seen = []
    real = hypotheses.evaluate
    monkeypatch.setattr(hypotheses, "evaluate", lambda survey, rows, *a, **kw: seen.append(len(list(rows))) or real(survey, rows, *a, **kw))
    html = client.get("/surveys/1/results?nur_var=rand:bed&nur=Nutzen").data.decode()
    assert "Nur ein Teil der Daten" in html and "(8 Teilnahmen)" in html
    assert seen == [16]                       # both conditions, not only the shown one


# --- dashboards (stage 4) -------------------------------------------------------------------------------------------

def test_dashboard_tile_split_by_condition_and_classes(app, client, survey):
    from app import boards
    from app.models import Board

    rows = ([((0,), "1", a, b) for a, b in ((1, 2), (2, 2), (2, 3), (1, 1), (3, 2), (2, 1))] * 2
            + [((1,), "2", a, b) for a, b in ((4, 5), (5, 4), (4, 4), (3, 4), (5, 5), (4, 3))] * 2)
    randomized(app, client, rows)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "quelle", "mode": "store", "allowed": ["uni", "panel"]}], "redirects": {}, "rules": []}
        found = boards.split_vars(s)
        assert "rand:bed" in found and "item:2" in found and not any(k.startswith("url:") for k in found)
        s.boards.append(Board(name="A", position=0, tiles=[
            {"key": "item:3", "view": "means", "w": 6, "x": 0, "pct": True, "n": True, "by": "rand:bed", "title": ""},
            {"key": "item:2", "view": "bars", "w": 6, "x": 0, "pct": True, "n": True, "by": "url:quelle", "title": ""}]))
        db.session.commit()
        board = s.boards[0]
        assert [t["by"] for t in boards.tiles_of(board)] == ["rand:bed", ""]   # no link parameter on dashboards
        tile = boards.state(board, fresh=True)["tiles"][0]
        assert tile["split"] and [g["label"] for g in tile["split"]["groups"]] == ["Nutzen", "Risiken"]
        assert "Randomisierung" in tile["by_title"]
        # copies keep the split: variable names stay, questions get their twins
        assert boards.remap_split("rand:bed", {}, {}) == "rand:bed"
        assert boards.remap_split("klassen:4", {4: 9}, {}) == "klassen:9"
        assert boards.remap_split("mc:5:2", {5: 7}, {}) == "mc:7:2"
        assert boards.remap_split("item:3", {3: 8}, {}) == "item:8"
        assert boards.remap_split("klassen:4", {}, {}) is None and boards.remap_split("url:quelle", {}, {}) is None


def test_group_labels_in_the_language_of_a_dashboard():
    assert groupvars.class_groups([18, 2.5], "en") == [("k0", "under 18"), ("k1", "18 to under 2.5"), ("k2", "2.5 and over")]
    assert groupvars.class_groups([2.5], "de")[1][1] == "2,5 und mehr"
    assert groupvars.class_groups([18], "fr")[0][1] == "moins de 18"


def test_choice_in_english_for_an_english_researcher(app, client, survey):
    from app.models import User

    rows = [((0,), "1", 1, 2)] * 6 + [((1,), "2", 4, 4)] * 6
    randomized(app, client, rows)
    with app.app_context():
        user = db.session.scalars(db.select(User)).first()
        user.display = {**(user.display or {}), "lang": "en"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=rand:bed").data.decode()
    assert "Split into groups" in html and '<optgroup label="Experimental conditions">' in html
    assert "bed · randomisation “Text”" in html and "The groups are assigned at random:" in html
    assert "Assignment as expected" in html and "Nach Gruppen aufteilen" not in html


# --- mathematics audit 2026-10-06 (package D) -------------------------------------------------------------------------

def test_dont_know_is_in_no_class_and_not_in_the_tertiles():
    """E-1: "weiß nicht" (-98) of a metric rating is no number: not in the class below the first bound, not in the
    tertiles the bounds come from."""
    from types import SimpleNamespace

    kind = SimpleNamespace(missing_codes=lambda item, column: (-98,))
    item = SimpleNamespace(id=7, code="zufr", text=lambda key, lang=None: "Zufriedenheit 0 bis 100", config={}, kind=kind)
    rows = [{"zufr": v} for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100] + [-98] * 5 + ["-98", -77]]
    var = groupvars.ClassVar(item, None, "data", "de")
    var.prepare(rows)
    assert var.bounds == [40, 70]                           # was [0, 60]: a class "unter 0" of "weiß nicht" alone
    assert var.member({"zufr": -98}) is None and var.member({"zufr": "-98"}) is None and var.member({"zufr": -77}) is None
    saved = groupvars.ClassVar(item, [30, 60], "saved", "de")
    assert saved.member({"zufr": -98}) is None and saved.member({"zufr": 10}) == "k0"


def test_tertiles_survive_an_outlier_and_many_zeros():
    """E-6: the rounding step no longer comes from the whole range alone; tertiles on one value split it off."""
    income = [500, 800, 1200, 1500, 1800, 2000, 2200, 2500, 3000, 3500, 4000, 5000, 8000, 20000, 100000]
    assert groupvars.tertile_bounds(income) == [2000, 4000]                        # was None
    assert groupvars.tertile_bounds([0] * 80 + [1] * 10 + [2] * 6 + [3] * 4) == [1]  # "unter 1" / "1 und mehr"
    assert groupvars.tertile_bounds([1] * 10 + [5] * 80 + [9] * 10) == [5, 6]       # the 5s a class of their own
    # spread data keep their bounds
    assert groupvars.tertile_bounds(list(range(18, 81))) == [40, 60]
    assert groupvars.tertile_bounds([7] * 60 + [6] * 20 + [5] * 10 + [1] * 10) == [6, 7]
    assert groupvars.tertile_bounds([i / 100 for i in range(101)]) == [0.3, 0.7]


def test_no_classes_from_the_data_get_their_own_message(app, client, survey):
    """E-6: without bounds the page says that no classes could be formed, not "fewer than two groups"."""
    with_number_and_choice(app, client, code="jahre")
    with app.app_context():
        for r in db.session.get(Survey, 1).responses:
            r.answers = {**r.answers, "4": "5"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=klassen:4").data.decode()
    assert "keine Klassen bilden" in html and "Weniger als zwei Gruppen" not in html


def test_saved_bounds_are_named_in_the_hint(app, client, survey):
    """E-8: "18,30" is one bound 18,3 (decimal comma); the hint names what was saved."""
    with_number_and_choice(app, client, code="jahre")
    client.post("/surveys/1/results/classes", data={"item": "4", "grenzen": "18,30"})
    html = client.get("/surveys/1/results?gruppen=klassen:4").data.decode()
    assert "gespeichert für diese Befragung: 18,3;" in html and "ein Komma ist ein Dezimalkomma" in html


def test_nothing_ticked_in_an_optional_question_belongs_to_no_group(app, client, survey):
    """E-7 (decision 5): a row without a single tick of a multiple choice question that may be skipped and has no
    exclusive option is in no group; ticked elsewhere it stays "nicht angekreuzt"."""
    with_number_and_choice(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        var = groupvars.get(s, "mc:5:2")
        assert var.untouched
        assert var.member({"medien_1": 0, "medien_2": 0}) is None
        assert var.member({"medien_1": 1, "medien_2": 0}) == "0" and var.member({"medien_1": 0, "medien_2": 1}) == "1"
        assert var.member({"medien_1": None, "medien_2": None}) is None
        first = sorted(s.responses, key=lambda r: r.id)[0]
        first.answers = {**first.answers, "5": []}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=mc:5:2").data.decode()
    assert "ohne Zuordnung: 1" in html and "gar nichts angekreuzt" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = next(i for b in s.blocks for i in b.items if i.code == "medien")
        item.answer_mode = "required"                             # required: nothing ticked is no skip
        db.session.commit()
        var = groupvars.get(s, "mc:5:2")
        assert not var.untouched and var.member({"medien_1": 0, "medien_2": 0}) == "0"


def test_dashboard_classes_put_together_keep_true_labels():
    """E-5: a single value put together with an open class: "2" and "ab 3" are "ab 2"; "unter 5" to "7" is "bis 7"."""
    from app import boards

    def labels(xs):
        bins, places = boards._classes(xs)
        return [(boards._span(bins[a], bins[b], places), c) for a, b, c in boards.merge_adjacent([c for _, _, c in bins])]

    assert labels([0] * 12 + [1] * 8 + [2] * 6 + [3] * 3 + [4] * 1) == [("0", 12), ("1", 8), ("ab 2", 10)]
    assert labels([10] * 8 + [9] * 6 + [8] * 5 + [7] * 3 + [5] * 2 + [0]) == [("bis 7", 6), ("8", 5), ("9", 6), ("ab 10", 8)]


def test_dashboard_conjoint_importance_stays_with_a_small_level():
    """L-7: a hidden level (fewer than MIN_CELL choices) used to shrink its attribute's span. The importance now comes
    from the model of the results page (boards._importances), which uses every level: it stays as it was given."""
    from app import boards

    def tile(small):
        return {"shape": "conjoint", "n": 40, "basis": "", "_whole": False, "method": "logit",
                "importances": [{"label": "A", "importance": 0.6}, {"label": "B", "importance": 0.4}], "attributes": [
            {"label": "A", "importance": 0.6, "levels": [{"label": "a1", "_share": 0.55, "_count": 22}, {"label": "a2", "_share": 0.35, "_count": 14},
                                      {"label": "a3", "_share": 0.10, "_count": small}]},
            {"label": "B", "importance": 0.4, "levels": [{"label": "b1", "_share": 0.45, "_count": 18}, {"label": "b2", "_share": 0.25, "_count": 10}]}]}

    data = boards._reduce(tile(4))
    assert [a["importance"] for a in data["attributes"]] == [0.6, 0.4] and "importance_hidden" not in data
    assert data["attributes"][0]["levels"][2]["small"] and data["attributes"][0]["levels"][2]["share"] is None
    data = boards._reduce(tile(8))
    assert data["attributes"][0]["levels"][2]["share"] == 0.10 and data["importances"][0]["importance"] == 0.6


def test_show_only_keeps_the_weights_of_the_whole_sample(app, client, survey):
    """E-2: under "Nur zeigen" the group keeps the weights of the whole pool (domain, Lumley, 2010) instead of being
    raked to the margins of the whole population anew: the women weigh 1,5 as in the whole sample, not 1."""
    from werkzeug.datastructures import MultiDict

    from app import export, weights
    from tests.analysis.test_weights import W, quota_survey

    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        whole = export.rows(s)
        women = whole.keep(lambda r: str(r.get("gender")) == "2")
        assert set(weights._compute(s, women)["weights"].values()) == {1.0}    # raked anew: the old way
        found = weights.domain(s, whole, women)
        assert set(found["weights"].values()) == {1.5} and found["n"] == 2 and found["domain"]
        assert weights.compute(s, women) is found                             # what the page then reads
        var = next(v for v in groupvars.all_of(s).values() if v.code == "gender")
    html = client.get(f"/surveys/1/results?gewichtet=1&nur_var={var.id}&nur=2").data.decode()
    assert "Nur ein Teil der Daten:" in html and "größtes Gewicht 1,50" in html and "Gewichten der ganzen Stichprobe" in html
