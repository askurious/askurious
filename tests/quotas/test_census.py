"""The quota assistant "Bevölkerung Deutschland": the official figures, the plan of quotas and targets, what it
puts into a survey, and how participants pass it (app/census.py, tools/build_population_de.py)."""
from werkzeug.datastructures import MultiDict

from app import census, flow, quotas
from app.extensions import db
from app.models import Item, Quota, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

A = "/surveys/1/flow/quotas/assistant"


def opts(**chosen):
    return census.options(MultiDict({key: str(value) for key, value in chosen.items()}))


def targets(quota):
    return {c["code"]: c["target"] for c in quota["cells"]}


# --- figures and plan -----------------------------------------------------------------------------------


def test_figures_are_the_population_update_of_2025():
    data = census.population()
    assert (data["table"], data["date"], data["top_age"]) == ("12411-0013", "2025-12-31", 90)
    assert len(data["states"]) == 16 and all(len(rows[sex]) == 91 for rows in data["states"].values() for sex in "mf")
    assert sum(sum(rows["m"]) + sum(rows["f"]) for rows in data["states"].values()) == 83_467_117
    assert "Tabelle 12411-0013" in census.source() and "Stichtag 31.12.2025" in census.source()


def test_options_take_only_what_is_offered():
    assert opts() == {"n": 0, "cross": "none", "lo": 18, "hi": None, "scheme": "decades", "regions": "states", "state": "",
                      "age": "", "gender": "", "gf": "", "gm": "", "move": False, "others": "replace"}
    assert opts(n="abc", cross="x", lo=17, hi=70, scheme="y", regions="z", state="st:3") == opts()
    assert opts(n=500, cross="full", lo=16, hi=74, scheme="allbus", regions="nielsen", state=7) == \
        {"n": 500, "cross": "full", "lo": 16, "hi": 74, "scheme": "allbus", "regions": "nielsen", "state": "7",
         "age": "", "gender": "", "gf": "", "gm": "", "move": False, "others": "replace"}
    assert (opts(move=1)["move"], opts(others="add")["others"], opts(others="x")["others"]) == (True, "add", "replace")
    assert (opts(age="x")["age"], opts(age=4)["age"], opts(gender="new")["gender"]) == ("", "4", "new")
    assert opts(state="new")["state"] == "new"


def test_crossing_is_chosen_by_ticking_attributes():
    def ticked(*attrs, **more):
        return census.options(MultiDict([("x", a) for a in attrs] + list(more.items())))["cross"]

    assert ticked() == "none" and ticked("age") == "none" and ticked("nope", "age") == "none"
    assert ticked("age", "gender") == "age_gender" and ticked("state", "age") == "age_state"
    assert ticked("gender", "state") == "gender_state" and ticked("age", "gender", "state") == "full"
    assert ticked("age", "state", cross="full") == "age_state" and ticked(cross="gender_state") == "gender_state"
    assert set(census.CROSSED) == set(census.CROSS)
    by_state_age = census.plan(opts(n=1000, cross="age_state"))
    assert [(q["code"], q["no_cell"], len(q["cells"])) for q in by_state_age["quotas"]] == \
        [("de_land_alter", "end", 96), ("de_geschlecht", "continue", 2)]
    assert all(sum(targets(q).values()) == 1000 for q in by_state_age["quotas"])
    cell = next(c for c in by_state_age["quotas"][0]["cells"] if c["code"] == "nw_a70plus")
    assert cell["name"] == "Nordrhein-Westfalen / ab 70" and cell["target"] == 41
    grid = census.grid(by_state_age["quotas"][0])
    assert grid["rows"][0] == "Baden-Württemberg" and grid["cols"][0] == "18 bis 29" and len(grid["cols"]) == 6
    by_state_sex = census.plan(opts(n=1000, cross="gender_state", regions="nielsen"))
    assert [(q["code"], q["name"], len(q["cells"])) for q in by_state_sex["quotas"]] == \
        [("de_land_geschlecht", "Bevölkerung DE: Region × Geschlecht", 16), ("de_alter", "Bevölkerung DE: Alter", 6)]
    assert sum(targets(by_state_sex["quotas"][0]).values()) == 1000
    assert not by_state_sex["errors"] and not any("schließt die Befragung nicht von selbst" in h for h in by_state_sex["hints"])


def test_crossing_two_attributes_in_the_survey(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                        # item 2 in block 2
    page = client.get(A + "?n=1000&x=age&x=state").data.decode()
    assert 'name="x" value="age" checked' in page and 'name="x" value="state" checked' in page
    assert 'name="x" value="gender" checked' not in page and 'name="cross" value="age_state"' in page
    assert "Gewählt ist: <strong>Bundesland × Alter gekreuzt, Geschlecht für sich</strong>" in page
    client.post(A, data={"n": "1000", "cross": "age_state"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        crossed = next(q for q in s.quotas if q.code == "de_land_alter")
        assert [q.code for q in s.quotas] == ["de_land_alter", "de_geschlecht"] and len(crossed.cells) == 96
        assert crossed.cells[0].conds == [{"item": found["state"].id, "op": "eq", "value": "8"},
                                          {"item": found["age"].id, "op": "between", "value": "18..29"}]
        assert quotas.matrix(s, crossed, quotas.report(s)[0]["cells"]) is not None
        assert not [m for severity, m in quotas.check(s) if severity == "error"]


def test_age_groups_follow_span_and_scheme():
    assert census.age_groups("decades") == [(18, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, None)]
    assert census.age_groups("decades", 16, 74) == [(16, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 74)]
    assert census.age_groups("decades", 18, 69)[-1] == (60, 69)
    assert census.age_groups("census5") == [(18, 29), (30, 49), (50, 64), (65, None)]
    assert census.age_groups("census11", 18, 79)[-2:] == [(65, 74), (75, 79)]
    assert census.age_groups("allbus") == [(18, 29), (30, 44), (45, 59), (60, 74), (75, None)]
    assert census.age_groups("gles") == [(18, 29), (30, 39), (40, 49), (50, 59), (60, None)]
    assert census.scheme_label("gles") == "GLES Tracking: 18–29, 30–39, 40–49, 50–59, 60 und älter"
    assert [census.age_label(g) for g in ((18, 29), (70, None))] == ["18 bis 29", "ab 70"]
    assert census.scheme_label("census5") == "Zensus 2022, 5 Altersklassen: 18–29, 30–49, 50–64, 65 und älter"


def test_plan_not_crossed_gives_three_quotas_with_official_shares():
    plan = census.plan(opts(n=1000))
    assert plan["persons"] == 69_591_153 and not plan["errors"]
    gender, age, state = plan["quotas"]
    assert [(q["code"], q["no_cell"], len(q["cells"])) for q in plan["quotas"]] == \
        [("de_geschlecht", "continue", 2), ("de_alter", "end", 6), ("de_land", "end", 16)]
    assert targets(gender) == {"m": 489, "w": 511}
    assert targets(age) == {"a18_29": 158, "a30_39": 156, "a40_49": 151, "a50_59": 163, "a60_69": 173, "a70plus": 199}
    assert targets(state)["nw"] == 215 and targets(state)["hb"] == 8 and targets(state)["by"] == 158
    assert all(sum(targets(q).values()) == 1000 for q in plan["quotas"])
    assert [c["name"] for c in state["cells"]][:2] == ["Baden-Württemberg", "Bayern"]


def test_plan_crossed_uses_the_joint_distribution():
    plan = census.plan(opts(n=1000, cross="full"))
    (quota,) = plan["quotas"]
    assert len(quota["cells"]) == 192 and sum(targets(quota).values()) == 1000 and not plan["errors"]
    cell = next(c for c in quota["cells"] if c["code"] == "sn_a70plus_w")
    assert cell["name"] == "Sachsen / ab 70 / weiblich" and cell["persons"] == 485_787 and cell["target"] == 7
    assert any("128 von 192 Teilquoten haben ein Soll unter 5" in hint for hint in plan["hints"])
    partly = census.plan(opts(n=1000, cross="age_gender"))
    assert [(q["code"], len(q["cells"])) for q in partly["quotas"]] == [("de_alter_geschlecht", 12), ("de_land", 16)]
    assert [c["target"] for c in partly["quotas"][0]["cells"]] == [82, 75, 80, 76, 76, 75, 82, 82, 85, 88, 85, 114]
    grid = census.grid(partly["quotas"][0])
    assert grid["rows"][0] == "18 bis 29" and grid["cols"] == ["männlich", "weiblich"] and grid["cells"][5][1]["target"] == 114
    assert census.grid(partly["quotas"][1]) is None
    wide = census.grid(quota)
    assert wide["rows"][0] == "Baden-Württemberg" and wide["cols"][:2] == ["18–29 m", "18–29 w"] and wide["cols"][-1] == "70+ w"


def test_plan_refuses_cells_without_a_target_and_respects_the_age_span():
    plan = census.plan(opts(n=500, cross="full"))
    assert any("20 von 192 Teilquoten hätten das Soll 0" in e for e in plan["errors"])
    capped = census.plan(opts(n=1000, hi=69))
    assert capped["persons"] == 55_753_715 and [c["code"] for c in capped["quotas"][1]["cells"]][-1] == "a60_69"
    assert sum(targets(capped["quotas"][1]).values()) == 1000
    assert not any("70 Jahre und älter" in hint for hint in capped["hints"])


def test_regions_put_states_together():
    for key, (_, _, groups) in census.REGIONS.items():  # every grouping holds each of the 16 states once
        assert sorted(code for _, _, codes in census.places(key) for code in codes) == sorted(census.STATE_SLUGS)
    plan = census.plan(opts(n=1000, regions="nielsen"))
    state = plan["quotas"][2]
    assert state["name"] == "Bevölkerung DE: Region" and state["code"] == "de_land"
    assert targets(state) == {"n1": 162, "n2": 215, "n3a": 137, "n3b": 134, "n4": 158, "n5": 44, "n6": 76, "n7": 74}
    assert state["cells"][0]["name"] == "Nielsen I (Schleswig-Holstein, Hamburg, Niedersachsen, Bremen)"
    assert targets(census.plan(opts(n=1000, regions="ba"))["quotas"][2]) == \
        {"nord": 77, "ni_hb": 104, "nw": 215, "he": 75, "rp_sl": 62, "bw": 134, "by": 158, "be_bb": 75, "st_th": 51, "sn": 49}
    assert targets(census.plan(opts(n=1000, regions="east_west"))["quotas"][2]) == {"west": 805, "ost": 195}
    full = census.plan(opts(n=1000, cross="full", regions="nielsen"))
    (quota,) = full["quotas"]
    assert quota["name"] == "Bevölkerung DE: Region × Alter × Geschlecht" and len(quota["cells"]) == 96
    assert sum(targets(quota).values()) == 1000 and min(targets(quota).values()) == 3
    assert any("12 von 96 Teilquoten haben ein Soll unter 5" in hint for hint in full["hints"])
    assert census.grid(quota)["rows"][1] == "Nielsen II (Nordrhein-Westfalen)"


def test_every_grouping_names_its_source(app, client, survey):
    assert set(census.SOURCES) == set(census.SCHEMES) | set(census.REGIONS)
    assert all(url.startswith("https://") and said and source for rows in census.SOURCES.values() for said, source, url in rows)
    page = client.get(A + "?scheme=gles&regions=nielsen").data.decode()
    assert "https://doi.org/10.4232/1.14173" in page and "GLES Tracking Juni 2022, T52" in page  # in the list and at the preview
    assert "agma-mmc.de/service/glossar/eintrag/nielsen-gebiete" in page and "Zensusdatenbank, Tabelle 1000A-2026" in page
    assert "Bundesagentur für Arbeit: Regionaldirektionen" in page and "Glossar, Gebietsstände" in page
    # Z-5 (audit 2026-10-05): one fold of sources (_sources.html), no sources in the running text of the preview
    assert page.count('<details class="sources">') == 1 and "Quelle der Einteilung der Bundesländer" not in page
    assert "Altersgruppen: " in page and "Genesis-Online, Tabelle 12411-0013" in page


def test_regions_in_the_survey(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                        # item 2 in block 2
    fill_questions(app)
    page = client.get(A + "?n=1000&regions=east_west").data.decode()
    assert "Ostdeutschland mit Berlin" in page and ">195<" in page and "nach dem Statistischen Bundesamt" in page
    client.post(A, data={"n": "1000", "regions": "east_west"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        state = census.questions(s)["state"]
        west, east = next(q for q in s.quotas if q.code == "de_land").cells
        assert (west.name, west.code, west.target) == ("Westdeutschland", "west", 805)
        assert east.conds == [{"item": state.id, "op": "in", "value": "11, 12, 13, 14, 15, 16"}]
        assert quotas.cell_text(s, east).startswith("bundesland: Berlin oder Brandenburg oder")
        assert not [m for severity, m in quotas.check(s) if severity == "error"]
        assert census.facts(s)["regions"] == "east_west"
        from app import methods

        assert "zu Regionen zusammengefasst (West- und Ostdeutschland" in methods.draft(methods.facts(s))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    person(app, uid, age="30", gender="1", state="14")               # a woman from Sachsen: counted in the east
    with app.app_context():
        cells = {c.code: c.running for q in db.session.get(Survey, 1).quotas for c in q.cells}
        assert cells["ost"] == 1 and cells["west"] == 0 and cells["w"] == 1


# --- the survey -----------------------------------------------------------------------------------------


def nodes(survey):
    return [(n["t"], n.get("block") or n.get("name") or "") for n in flow.tree(survey)["children"]]


def test_apply_adds_questions_screenouts_quotas_and_the_check(app, client, survey):
    fill_consent(client)
    add(client, "text_input")  # item 2 in block 2
    r = client.post(A, data={"n": "1000"}, follow_redirects=True)
    assert "3 Quoten mit zusammen 24 Teilquoten angelegt" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        age, gender, state = found["age"], found["gender"], found["state"]
        block = age.block
        assert block.settings == {"demographics": True} and block.title("de") == "Demographische Angaben"
        assert [i.code for i in block.items] == ["age", "gender", "bundesland"]
        assert (age.answer_mode, gender.answer_mode, state.answer_mode) == ("required", "nudge", "required")
        by_code = {q.code: q for q in s.quotas}
        # the screen-outs, the calculation, and one end with two triggers: no cell of age or state, and a full quota
        assert nodes(s)[:7] == [("block", 1), ("block", block.id), ("branch", "Wohnt nicht in Deutschland"),
                                ("branch", "Jünger als 18"), ("quota", ""), ("end", ""), ("block", 2)]
        top = flow.tree(s)["children"]
        assert top[2]["cond"] == {"item": state.id, "op": "eq", "value": "0"} and top[2]["children"][0]["kind"] == "screenout"
        assert top[3]["cond"] == {"item": age.id, "op": "lt", "value": "18"}
        assert top[4] == {"t": "quota", "id": top[4]["id"], "quotas": [q.id for q in s.quotas], "by": census.MARK,
                          "census": top[4]["census"]}                    # a trigger: the quotas and nothing else
        assert set(top[5]) == {"t", "id", "cases", "by"} and top[5]["by"] == census.MARK
        # one trigger for every Teilquote, behind the one for people whom no cell of age or state fits
        found = top[5]["cases"]
        assert (found[0]["when"], found[0]["quotas"]) == ("nocell", [by_code["de_alter"].id, by_code["de_land"].id])
        assert [(c["when"], c["quota"], c["cells"]) for c in found[1:]] == \
            [("cellfull", q.id, [cell.code]) for q in s.quotas for cell in q.cells] and len(found) == 1 + 24
        assert all((c["text"], c["link"], c["link_mode"]) == ({}, "", "now") for c in found)
        assert len({c["id"] for c in found}) == 25
        assert flow.child_label(s, top[5]) == "Ende (mehrere Auslöser)"
        assert [(q.code, q.no_cell, q.mode, q.count_at) for q in s.quotas] == \
            [("de_geschlecht", "continue", "hard", "complete"), ("de_alter", "continue", "hard", "complete"),
             ("de_land", "continue", "hard", "complete")]
        assert not [m for kind, m in quotas.check(s) if "Quote" in m and "Ende" in m]
        assert [(c.name, c.target, c.conds) for c in by_code["de_geschlecht"].cells] == \
            [("männlich", 489, [{"item": gender.id, "op": "eq", "value": "2"}]),
             ("weiblich", 511, [{"item": gender.id, "op": "eq", "value": "1"}])]
        ages = by_code["de_alter"].cells
        assert ages[0].conds == [{"item": age.id, "op": "between", "value": "18..29"}]
        assert ages[-1].conds == [{"item": age.id, "op": "ge", "value": "70"}] and ages[-1].name == "ab 70"
        assert by_code["de_land"].cells[0].conds == [{"item": state.id, "op": "eq", "value": "8"}]
        assert quotas.cell_text(s, ages[0]) == "age: 18 bis 29"
        assert not [m for severity, m in quotas.check(s) if severity == "error"]
        assert not [f for f in flow.check(s) if f[0] == "error"]
        assert census.facts(s)["date"] == "2025-12-31" and census.facts(s)["n"] == 1000


def test_apply_moves_existing_questions_and_fixes_the_state_question(app, client, survey):
    fill_consent(client)
    add(client, "construct:gender")                                  # item 2 in block 2
    add(client, "federal_state")                                     # item 3
    client.post("/surveys/1/editor/items/3", data={"code": "land", "question": "Land?", "no_answer": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        said = " ".join(census.steps(s, opts(n=1000, hi=74, move=1)))
        assert "Ein neuer Block „Demographische Angaben“" in said and "Das Konstrukt Alter wird in den Block" in said
        assert "Die Frage „gender“ (Konstrukt Geschlecht) rückt aus dem Block" in said
        assert "„land“ wird zur Pflichtfrage" in said and "bekommt die Antwort „Ich wohne nicht in Deutschland“" in said
        assert "verliert die Antwort „keine Angabe“" in said and "älter als 74 ist" in said
    client.post(A, data={"n": "1000", "hi": "74", "cross": "age_gender", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        assert (found["gender"].id, found["state"].id) == (2, 3)
        assert [i.code for i in found["age"].block.items] == ["age", "gender", "land"]
        assert [o["code"] for o in found["state"].config["options"]][-1] == "0" and found["state"].answer_mode == "required"
        assert db.session.get(Survey, 1).blocks[2].items == []       # the old block 2 is empty now, and stays
        assert [n[1] for n in nodes(s) if n[0] == "branch"] == ["Wohnt nicht in Deutschland", "Jünger als 18", "Älter als 74"]
        (end,) = quotas.full_ends(s)                                     # crossed age and gender: only the states cover everybody
        assert [c["when"] for c in end["cases"]] == ["nocell"] + ["cellfull"] * (12 + 16) and len(end["cases"][0]["quotas"]) == 1
        assert [(q.code, len(q.cells)) for q in s.quotas] == [("de_alter_geschlecht", 12), ("de_land", 16)]
        cell = s.quotas[0].cells[-1]
        assert cell.name == "70 bis 74 / weiblich" and cell.code == "a70_74_w"
        assert cell.conds == [{"item": found["age"].id, "op": "between", "value": "70..74"}, {"item": 2, "op": "eq", "value": "1"}]
        assert quotas.matrix(s, s.quotas[0], quotas.report(s)[0]["cells"]) is not None


def test_running_again_replaces_only_what_the_assistant_made(app, client, survey):
    fill_consent(client)
    client.post(A, data={"n": "1000"})
    client.post("/surveys/1/flow/quotas/add")                      # a quota of one's own
    with app.app_context():
        s = db.session.get(Survey, 1)
        own = next(q for q in s.quotas if q.code not in census.QUOTAS)
        own_id, old_ids = own.id, {q.id for q in census.own_quotas(s)}
    r = client.post(A, data={"n": "2000", "cross": "full"}, follow_redirects=True)
    assert "Nichts angelegt" in r.data.decode() and "ersetzt werden sollen" in r.data.decode()
    r = client.post(A, data={"n": "2000", "cross": "full", "replace": "1"}, follow_redirects=True)
    assert "1 Quote mit zusammen 192 Teilquoten angelegt" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert sorted(q.code for q in s.quotas) == sorted(["de_land_alter_geschlecht", own.code])
        assert db.session.get(Quota, own_id) is not None and not old_ids & {q.id for q in s.quotas}
        kinds = [n[0] for n in nodes(s)]
        assert kinds.count("quota") == 1 and kinds.count("branch") == 2
        assert sum(c.target for c in census.own_quotas(s)[0].cells) == 2000
        assert db.session.scalar(db.select(db.func.count()).select_from(Item)) == 4  # consent and the three questions, once


def test_page_previews_without_saving_and_refuses_what_cannot_be_made(app, client, survey):
    fill_consent(client)
    page = client.get(A).data.decode()
    assert "Quotenassistent: Bevölkerung Deutschland" in page and "Stichtag 31.12.2025" in page and "69.591.153" in page
    assert "21,5 %" in page and "Quoten anlegen" in page
    page = client.get(A + "?n=1000&cross=age_gender").data.decode()
    assert ">114<" in page and ">215<" in page and "Ein neuer Block „Demographische Angaben“" in page
    # the way there: the quota page, a page below "Ablauf" (UI audit 2026-10-12, E4: no tab "Quoten"), at the top of
    # it; the flow page has only its panel "Quoten" with the link
    quota_page = client.get("/surveys/1/flow/quotas/").data.decode()
    assert "Quotenassistent öffnen" in quota_page and "/flow/quotas/assistant" in quota_page
    flow_page = client.get("/surveys/1/flow/").data.decode()
    assert "/flow/quotas/assistant" not in flow_page and "flow-links" not in flow_page and "Quotenassistent" not in flow_page
    tabs = flow_page[flow_page.index('class="survey-tabs"'):].split("</nav>")[0]
    assert "/flow/quotas/" not in tabs and "Quoten" not in tabs
    assert 'href="/surveys/1/flow/quotas/' in flow_page.split('class="panel flow-quotas"')[1].split("</section>")[0]
    for shown in (quota_page, page):                                              # the assistant belongs there too
        assert 'href="/surveys/1/flow/" aria-current="page"' in shown
    with app.app_context():
        assert not db.session.get(Survey, 1).quotas                  # a preview saves nothing
    r = client.post(A, data={"cross": "full"}, follow_redirects=True)
    assert "Bitte die angestrebte Stichprobengröße angeben" in r.data.decode()
    r = client.post(A, data={"n": "500", "cross": "full"}, follow_redirects=True)
    assert "hätten das Soll 0" in r.data.decode()
    with app.app_context():
        assert not db.session.get(Survey, 1).quotas


def test_an_empty_quota_calculation_is_taken_over(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                        # item 2 in block 2
    client.post("/surveys/1/editor/flow/add", data={"kind": "quota"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        (node,) = flow.walk_nodes(s, "quota")
        nid = node["id"]
        assert [n["t"] for n in flow.tree(s)["children"]][:3] == ["block", "block", "quota"]  # added at the end
        assert "Die leere Quotenberechnung im Ablauf wird dafür verwendet" in " ".join(census.steps(s, opts(n=1000)))
    page = client.get("/surveys/1/flow/").data.decode()
    assert "/flow/quotas/assistant" not in page and "noch keine Quote gewählt" in page   # the assistant is in the tab "Quoten"
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        (node,) = flow.walk_nodes(s, "quota")                        # the same calculation, now behind the questions
        assert node["id"] == nid and node["by"] == census.MARK
        assert node["quotas"] == [q.id for q in s.quotas] and len(s.quotas) == 3
        kinds = [n["t"] for n in flow.tree(s)["children"]]
        assert kinds[:6] == ["block", "block", "branch", "branch", "quota", "end"] and kinds.count("quota") == 1
    # a second run keeps the calculation and makes the screen-outs and the end anew
    client.post(A, data={"n": "500", "cross": "age_gender", "replace": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        (node,) = flow.walk_nodes(s, "quota")
        assert node["id"] == nid and len(node["quotas"]) == 2
        kinds = [n["t"] for n in flow.tree(s)["children"]]
        assert kinds.count("branch") == 2 and kinds.count("end") == 1    # abroad, too young; one end, made anew
        (end,) = quotas.full_ends(s)
        assert sorted(set(quotas.end_quotas(end))) == sorted(node["quotas"])


# --- participants ---------------------------------------------------------------------------------------


def person(app, uid, age="30", gender="2", state="5"):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i3": age, "i4": gender, "i5": state})
    return p


def test_participants_are_screened_counted_and_turned_away(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                        # item 2 in block 2: the questionnaire itself
    fill_questions(app)
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        assert (found["age"].id, found["gender"].id, found["state"].id) == (3, 4, 5)
        men = next(c for q in s.quotas if q.code == "de_geschlecht" for c in q.cells if c.code == "m")
        men.target = 1
        db.session.commit()
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING, r.data.decode()[:3000]
        uid = s.code
    # the assistant only works on a draft
    r = client.post(A, data={"n": "2000", "replace": "1"}, follow_redirects=True)
    assert "läuft oder ist geschlossen" in r.data.decode()

    def state_of():
        with app.app_context():
            rows = db.session.scalars(db.select(Response).order_by(Response.id)).all()
            cells = {c.code: (c.running, c.done) for q in db.session.get(Survey, 1).quotas for c in q.cells}
            return [r.status for r in rows], cells

    person(app, uid, state="0")                                      # lives abroad
    person(app, uid, age="17")                                       # too young
    statuses, cells = state_of()
    assert statuses == [Response.SCREENOUT, Response.SCREENOUT] and not any(running for running, _ in cells.values())
    p = person(app, uid, age="30", gender="2", state="5")            # a man of 30 from Nordrhein-Westfalen
    statuses, cells = state_of()
    assert statuses[-1] == Response.ACTIVE and cells["m"] == (1, 0) and cells["a30_39"] == (1, 0) and cells["nw"] == (1, 0)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "x"})
    statuses, cells = state_of()
    assert statuses[-1] == Response.COMPLETED and cells["m"] == (0, 1) and cells["nw"] == (0, 1)
    person(app, uid, age="45", gender="2", state="9")                # the next man: his cell is full
    statuses, cells = state_of()
    assert statuses[-1] == Response.QUOTAFULL and cells["a40_49"] == (0, 0) and cells["by"] == (0, 0)
    person(app, uid, age="71", gender="3", state="16")               # "divers": no cell of gender, counted in the others
    statuses, cells = state_of()
    assert statuses[-1] == Response.ACTIVE and cells["a70plus"] == (1, 0) and cells["th"] == (1, 0)
    assert cells["m"] == (0, 1) and cells["w"] == (0, 0)
    words = "Verteilung der Bevölkerung ab 18 Jahren in Deutschland am 31.12.2025"
    page = client.get("/surveys/1/methods").data.decode()
    assert words in page and "Tabelle 12411-0013" in page
    from app import methods

    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        assert words in methods.draft(f) and "wurde nicht nach Geschlecht quotiert" in methods.draft(f)
        assert "population of Germany aged 18 and older on 31.12.2025" in methods.facts_text(f)


# --- the federal state from a postal code ----------------------------------------------------------------


def test_the_state_comes_from_a_postal_code_question(app, client, survey):
    fill_consent(client)
    add(client, "postal_code")                                       # item 2 in block 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        code = db.session.get(Item, 2).code
        assert census.questions(s)["state"].id == 2 and census.questions(s, "new")["state"] is None
        said = " ".join(census.steps(s, opts(n=1000)))
        assert f"Das Bundesland ergibt sich aus der Postleitzahl in „{code}“" in said and "Element Bundesland wird nicht" in said
        assert "eine Postleitzahl nennt, die es in Deutschland nicht gibt" in said and "nicht in Deutschland wohnt" not in said
        assert f"„{code}“ wird zur Pflichtfrage" in said and "Ich wohne nicht in Deutschland" not in said
        assert "Das Element Bundesland wird in den Block" in " ".join(census.steps(s, opts(n=1000, state="new")))
    page = client.get(A + "?n=1000").data.decode()
    assert "Bundesland aus" in page and "Postleitzahl (das Bundesland, in dem sie liegt)" in page
    assert "neues Element Bundesland einfügen" in page and 'name="state" value="2"' not in page
    client.post(A, data={"n": "1000", "cross": "gender_state", "regions": "east_west", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        postal = found["state"]
        assert postal.id == 2 and postal.type == "postal_code" and postal.answer_mode == "required"
        assert "federal_state" not in [i.type for b in s.blocks for i in b.items]   # no element Bundesland
        assert postal.block is found["age"].block
        ref = f"st:{postal.id}"
        top = flow.tree(s)["children"]
        assert nodes(s)[2] == ("branch", "Postleitzahl ohne Bundesland")
        assert top[2]["cond"] == {"item": ref, "op": "empty", "value": ""} and top[2]["children"][0]["kind"] == "screenout"
        assert s.quotas[0].code == "de_land_geschlecht"
        first = s.quotas[0].cells[0].conds[0]
        assert first["item"] == ref and first["op"] == "in"
        assert census.facts(s)["state"] == "postal_code"
        assert not [m for severity, m in quotas.check(s) if severity == "error"]
        assert not [f for f in flow.check(s) if f[0] == "error"]


def test_with_both_questions_the_element_counts_unless_the_postal_code_is_chosen(app, client, survey):
    fill_consent(client)
    add(client, "postal_code")                                       # item 2
    add(client, "federal_state")                                     # item 3
    page = client.get(A + "?n=1000").data.decode()
    assert "Bundesland aus" in page and "neues Element Bundesland einfügen" not in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [i.id for i in census.state_sources(s)] == [2, 3]
        assert census.questions(s)["state"].id == 3 and census.questions(s, "new")["state"].id == 3
        assert census.questions(s, "2")["state"].id == 2 and census.questions(s, "99")["state"].id == 3
    client.post(A, data={"n": "1000", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert nodes(s)[2] == ("branch", "Wohnt nicht in Deutschland") and census.facts(s)["state"] == "federal_state"
        assert next(q for q in s.quotas if q.code == "de_land").cells[0].conds == [{"item": 3, "op": "eq", "value": "8"}]
    client.post(A, data={"n": "1000", "state": "2", "replace": "1", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert nodes(s)[2] == ("branch", "Postleitzahl ohne Bundesland") and census.facts(s)["state"] == "postal_code"
        assert next(q for q in s.quotas if q.code == "de_land").cells[0].conds == [{"item": "st:2", "op": "eq", "value": "8"}]
        assert "Wohnt nicht in Deutschland" not in [n[1] for n in nodes(s)]
        assert db.session.get(Item, 3) is not None                   # the element stays in the questionnaire


def test_participants_are_counted_by_the_state_of_their_postal_code(app, client, survey):
    fill_consent(client)
    add(client, "postal_code")                                       # item 2 in block 2
    add(client, "text_input")                                        # item 3: the questionnaire itself
    fill_questions(app)
    client.post(A, data={"n": "1000", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        assert (found["state"].id, found["age"].id, found["gender"].id) == (2, 4, 5)
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING, r.data.decode()[:3000]
        uid = s.code

    def person_from(plz, age="30"):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        answers = {"nav": "next", "i2": plz, "i4": age, "i5": "2"}
        p.post(f"/s/{uid}", data=answers)
        with app.app_context():
            last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
            cells = {c.code: c.running for q in db.session.get(Survey, 1).quotas for c in q.cells}
            return p, last.status, cells

    _, status, cells = person_from("52062")                          # Aachen: Nordrhein-Westfalen
    assert status == Response.ACTIVE and (cells["nw"], cells["by"], cells["m"], cells["a30_39"]) == (1, 0, 1, 1)
    _, status, cells = person_from("80331")                          # München: Bayern
    assert status == Response.ACTIVE and (cells["nw"], cells["by"]) == (1, 1)
    p, status, cells = person_from("00000")                          # no such postal code: asked once, then kept
    if status == Response.ACTIVE:
        p.post(f"/s/{uid}", data={"nav": "next", "i2": "00000", "i4": "30", "i5": "2", "nudge_ack": "1"})
    with app.app_context():
        last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        cells = {c.code: c.running for q in db.session.get(Survey, 1).quotas for c in q.cells}
        assert last.status == Response.SCREENOUT and (cells["m"], cells["a30_39"]) == (2, 2)
    from app import methods

    with app.app_context():
        assert "Das Bundesland ergab sich aus der angegebenen Postleitzahl." in methods.draft(methods.facts(db.session.get(Survey, 1)))


def test_the_end_of_the_assistant_has_a_trigger_for_every_cell(app, client, survey):
    from tests.quotas.test_quotas import trigger_form

    fill_consent(client)
    add(client, "text_input")
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        (end,) = quotas.full_ends(s)
        ids = [q.id for q in s.quotas]
        land = next(q for q in s.quotas if q.code == "de_land")
        code = land.cells[0].code
        old = end["cases"]
        assert not [m for kind, m, _ in flow.check(s) if "Ende" in m or "beendet niemanden" in m]
        first = next(c for c in old if c.get("quota") == land.id)        # the trigger of the first state
        assert first["cells"] == [code] and len(old) == 25
    page = client.get("/surveys/1/flow/").data.decode()
    # the end of the assistant: a card per Teilquote, folded, and counted in the head of the end
    assert "24 × Teilquote voll" in page and "1 × Keine Teilquote passt" in page and "Ende (mehrere Auslöser)" in page
    # its 25 cards and the one to add; the two screen-outs before it have a card and one to add each
    assert page.count("data-case>") == 26 + 4 and page.count('<details class="end-case" data-case>') == 25
    assert f"Teilquote voll: {land.name} · {land.cells[0].name}" in page.replace("\n", " ")
    assert f'"{land.id}": [["{code}", ' in page and "+ Auslöser</button>" in page
    assert "Alle 16 Teilquoten zeigen" in page                           # of a long list only the ticked cell is there
    k = [c["id"] for c in old].index(first["id"])
    assert page.count(f' name="case{k}_cells"') == 1 and f'name="case{k}_cells" value="{code}" checked' in page
    # each trigger takes an end text and a completion link of its own
    cards = [(n, {"when": c["when"], "id": c["id"], **({"nquotas": [str(q) for q in c["quotas"]]} if c["when"] == "nocell" else
                                                        {"quota": str(c["quota"]), "cells": c["cells"]})})
             for n, c in enumerate(old)]
    cards[k][1].update(link="https://panel.example/land", text_de="Ihr Bundesland ist voll.")
    client.post(f"/surveys/1/editor/flow/{end['id']}/update", data=trigger_form(*cards))
    with app.app_context():
        s = db.session.get(Survey, 1)
        (end,) = quotas.full_ends(s)
        assert [c["id"] for c in end["cases"]] == [c["id"] for c in old]
        assert (end["cases"][k]["link"], end["cases"][k]["text"]["de"]) == ("https://panel.example/land", "Ihr Bundesland ist voll.")
        assert flow.end_link(end, {f"q:{land.id}": "full", f"qc:{land.id}": code}) == ("https://panel.example/land", "now")
        assert flow.end_link(end, {f"q:{land.id}": "full", f"qc:{land.id}": land.cells[1].code}) == (None, None)
        assert flow.case_status(flow.case_for(end, {f"q:{land.id}": "full", f"qc:{land.id}": land.cells[1].code})) == "quotafull"
        assert flow.case_status(flow.case_for(end, {f"q:{ids[1]}": "none"})) == "screenout"
        assert flow.case_for(end, {f"q:{i}": "in" for i in ids}) is None                       # everybody with a place passes
        # the survey may close by itself once a quota that ends everybody is spent: all its cells are named
        assert not quotas.exhausted(s)
        for cell in land.cells:
            db.session.get(type(cell), cell.id).done = cell.target
        assert quotas.exhausted(s)
        db.session.rollback()
    assert "→ panel.example" in client.get("/surveys/1/flow/").data.decode()
    # a second run of the assistant makes its end anew (what was set by hand goes with it)
    client.post(A, data={"n": "500", "replace": "1"})
    with app.app_context():
        (end,) = quotas.full_ends(db.session.get(Survey, 1))
        assert len(end["cases"]) == 25 and not [c for c in end["cases"] if c["link"]]


def test_preview_says_the_gender_quota_stays_short(app, client, survey):
    """Audit 2026-10-03, Z-3: "divers"/"keine Angabe" take places in the age and state quotas, so the survey
    closes before the gender cells are full; the preview says so (fully crossed nobody closes it: no such line)."""
    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        said = " ".join(census.steps(s, opts(n=1000)))
        assert "die Geschlechtsquote bleibt um diese Personen unter Soll" in said
        assert "unter Soll" not in " ".join(census.steps(s, opts(n=1000, cross="full")))


# --- self-built questions for age and gender (UI/UX audit 2026-10-05, V18) ------------------------------


def own_questions(app, client):
    """An age question for a whole number and a gender single choice built by hand (items 2 and 3)."""
    add(client, "text_input")
    add(client, "single_choice")
    with app.app_context():
        age, gender = db.session.get(Item, 2), db.session.get(Item, 3)
        age.code, gender.code = "alter", "geschl"
        age.config = {**age.config, "validation": "integer"}
        age.texts = {"de": {**age.texts["de"], "question": "Wie alt sind Sie?"}}
        gender.config = {**gender.config, "options": [{"id": f"o{k}", "code": str(k)} for k in (1, 2, 3)]}
        gender.texts = {"de": {**gender.texts["de"], "question": "Geschlecht?",
                               "options": {"o1": "Weiblich", "o2": "Männlich", "o3": "divers"}}}
        db.session.commit()


def test_self_built_age_and_gender_are_taken(app, client, survey):
    fill_consent(client)
    own_questions(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        assert (found["age"].id, found["gender"].id) == (2, 3)
        assert census.gender_codes(found["gender"]) == {"f": "1", "m": "2"}
        said = " ".join(census.steps(s, opts(n=1000, move=1)))
        assert "Die Frage „alter“ (Frage Alter) rückt" in said and "Die Frage „geschl“ (Frage Geschlecht) rückt" in said
        assert "Konstrukt Alter wird" not in said and "Konstrukt Geschlecht wird" not in said
        assert not census.problems(s, opts(n=1000))
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.scalar(db.select(db.func.count()).select_from(Item)) == 4  # consent, the two, Bundesland
        gender = next(q for q in s.quotas if q.code == "de_geschlecht")
        assert [c.conds for c in gender.cells] == [[{"item": 3, "op": "eq", "value": "2"}], [{"item": 3, "op": "eq", "value": "1"}]]
        assert census.facts(s)["sexes"] == {"f": "1", "m": "2"} and census.facts(s)["gender_item"] == 3


def test_answers_for_female_and_male_can_be_chosen(app, client, survey):
    fill_consent(client)
    own_questions(app, client)
    with app.app_context():
        gender = db.session.get(Item, 3)
        gender.texts = {"de": {**gender.texts["de"], "options": {"o1": "eine", "o2": "zwei", "o3": "drei"}}}
        db.session.commit()
        s = db.session.get(Survey, 1)
        assert census.questions(s)["gender"] is None              # nothing says which answer is which: not taken itself
        chosen = opts(n=1000, gender=3)
        assert "nicht zugeordnet" in " ".join(census.problems(s, chosen))
        chosen = opts(n=1000, gender=3, gf=3, gm=1)
        assert not census.problems(s, chosen) and census.gender_codes(census.chosen_questions(s, chosen)["gender"], chosen) == {"f": "3", "m": "1"}
        assert "gleich" not in " ".join(census.problems(s, opts(n=1000, gender=3, gf=1, gm=2)))
        assert "dieselbe Antwort" in " ".join(census.problems(s, opts(n=1000, gender=3, gf=1, gm=1)))
    page = client.get(A + "?n=1000&gender=3").data.decode()
    assert '<select name="gf">' in page and "Konstrukt Geschlecht neu einfügen" in page
    page = client.get(A + "?n=1000&gender=new").data.decode()
    assert '<select name="gf">' not in page


def test_preview_names_other_quotas_on_the_same_questions(app, client, survey):
    fill_consent(client)
    own_questions(app, client)
    client.post("/surveys/1/flow/quotas/add")
    with app.app_context():
        from app.models import QuotaCell

        s = db.session.get(Survey, 1)
        s.quotas[0].cells = [QuotaCell(name="w", code="w", conds=[{"item": 3, "op": "eq", "value": "1"}], target=5, position=0)]
        db.session.commit()
        name = s.quotas[0].name
        assert census.others_on(s, census.questions(s)) == [(s.quotas[0], ["Geschlecht"])]
        assert "fragt schon nach" not in " ".join(census.steps(s, opts(n=1000)))   # a box above the preview (W18)
    page = client.get(A + "?n=1000").data.decode()
    assert "Diese Merkmale sind schon quotiert:" in page and f"„{name}“ (Geschlecht)" in page
    assert f'value="replace" form="census-apply" checked> bestehende Quote „{name}“ ersetzen' in page
    assert 'value="add" form="census-apply" >' in page


def test_w18_an_existing_quota_on_the_same_attribute_is_replaced_unless_added(app, client, survey):
    """UI/UX audit 2026-10-07, W18: "bestehende Quote ersetzen" is chosen; "zusätzlich anlegen" keeps it."""
    fill_consent(client)
    own_questions(app, client)
    client.post("/surveys/1/flow/quotas/add")
    with app.app_context():
        from app.models import QuotaCell

        s = db.session.get(Survey, 1)
        s.quotas[0].cells = [QuotaCell(name="w", code="w", conds=[{"item": 3, "op": "eq", "value": "1"}], target=5, position=0)]
        db.session.commit()
        other = s.quotas[0].id
    client.post(A, data={"n": "1000", "others": "add"})
    with app.app_context():
        assert db.session.get(Quota, other) is not None
    client.post(A, data={"n": "1000", "replace": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Quota, other) is None
        assert s.quotas and all(q.code in census.QUOTAS for q in s.quotas)


def test_w18_questions_stay_where_they_are_without_the_tick(app, client, survey):
    """UI/UX audit 2026-10-07, W18: age and gender of block 2 stay there; the screen-outs and the quota calculation
    come after that block; only the missing state goes into a new block behind the consent."""
    fill_consent(client)
    own_questions(app, client)
    with app.app_context():
        said = " ".join(census.steps(db.session.get(Survey, 1), opts(n=1000)))
    assert "rückt" not in said and "ist vorhanden und bleibt, wo sie ist" in said
    assert "hinter dem Block mit der letzten der drei Fragen" in said
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        age, gender = db.session.get(Item, 2), db.session.get(Item, 3)
        assert age.block_id == gender.block_id == 2                      # where they were
        state = census.questions(s)["state"]
        assert state.block.settings == {"demographics": True}
        kinds = nodes(s)
        at_block2 = kinds.index(("block", 2))
        first_branch = next(k for k, n in enumerate(kinds) if n[0] == "branch")
        assert first_branch > at_block2                                   # the screen-outs after the questions
        assert not [f for f in flow.check(s) if f[0] == "error"]


def test_weighting_names_what_is_missing_and_takes_self_built_questions(app, client, survey):
    from app import weights

    fill_consent(client)
    page = client.get("/surveys/1/flow/quotas/").data.decode()
    assert weights.MISSING["gender"] in page and "keine passende Frage" not in page
    own_questions(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.weighting = {"kind": "census", "quotas": [], "census": ["gender"]}
        db.session.commit()
        found, problems = weights.margins(s)
        assert not problems
        assert [g["conds"] for g in found[0]["groups"]] == [[{"item": 3, "op": "eq", "value": "2"}], [{"item": 3, "op": "eq", "value": "1"}]]
