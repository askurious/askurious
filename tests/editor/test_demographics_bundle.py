"""The bundle "Demografie kurz (RatSWD)" (app/ratswd.py) in the editor, the setup wizard and AInstein, and GES_1 as
the gender question of new surveys (concept Demografie-Standards 2026-10-15, decisions 5b and 6b)."""
from app import census, constructs, design, ratswd
from app.extensions import db
from app.models import Survey


def _demo_layout(app, survey_id=1):
    """_layout of the block "Demographische Angaben" of the survey."""
    with app.app_context():
        s = db.session.get(Survey, survey_id)
        return _layout(next(b for b in s.blocks if (b.settings or {}).get("demographics")))


def _layout(block):
    """[(code, construct key or type, condition (source code, codes) or None)] of the block."""
    by_id = {i.id: i.code for i in block.items}
    out = []
    for i in block.items:
        when = (i.config or {}).get("when")
        out.append((i.code, constructs.toolbox_key(i) or i.type,
                    (by_id[when["item"]], when["value"]) if when else None))
    return out


def test_whole_bundle_from_the_editor(app, client, survey):
    page = client.get("/surveys/1/editor/").data.decode()
    assert "Demografie kurz (RatSWD)" in page and page.count('name="part"') == len(ratswd.PARTS)
    assert "Art. 9 DSGVO" in page
    r = client.post("/surveys/1/editor/demographics", data={"part": ratswd.PARTS})
    assert r.status_code == 302 and "#block-" in r.headers["Location"]
    with app.app_context():
        block = next(b for b in db.session.get(Survey, 1).blocks if (b.settings or {}).get("demographics"))
        layout = _layout(block)
        assert block.title() == "Demographische Angaben"
        first = [code for code, _k, _w in layout[:layout.index(("_pb1", "page_break", None))]]
        # everyone gets the first page; the filtered questions come after a page break (conditions read earlier pages)
        assert first == ["ALT_1", "GES_1", "BIL_1", "BIL_4", "ERW_1", "STA_1", "GEB_1", "GEB_3", "GEB_5", "HHG_1",
                         "FAM_1", "HNE_3"]
        assert layout[len(first) + 1:] == [
            ("ERW_2", "ratswd_erw2", ("ERW_1", "3, 4, 5, 6, 7, 8, 9")),
            ("BER_1", "ratswd_ber1", ("ERW_1", "1, 2, 4, 5, 6, 7, 9")),
            ("STE_1", "ratswd_ste1", ("ERW_1", "1, 2, 4, 5, 6, 7, 9")),
            ("HNE_4", "ratswd_hne4", ("HNE_3", "1")), ("HNE_5", "ratswd_hne5", ("HNE_3", "2")),
            ("HNE_6", "ratswd_hne6", ("HNE_3", "3"))]
        s = db.session.get(Survey, 1)
        from app import item_condition

        assert not any(item_condition.problems(i) for i in block.items)
        warned = [f.message for f in design.check(s) if "kleinen Stichproben" in f.message]
        assert len(warned) == 1 and "(ALT_1)" in warned[0] and "HHG_1, BER_1" in warned[0]
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        assert not any((b.settings or {}).get("demographics") for b in db.session.get(Survey, 1).blocks)


def test_parts_can_be_left_out(app, client, survey):
    # without ERW_1 its follow-ups are asked of everyone, without a page break; nothing ticked inserts nothing
    client.post("/surveys/1/editor/demographics", data={"part": ["ges", "ber1", "geb35"]})
    layout = _demo_layout(app)
    assert layout == [("GES_1", "ratswd_ges1", None), ("BER_1", "ratswd_ber1", None), ("GEB_3", "ratswd_geb3", None),
                      ("GEB_5", "ratswd_geb5", None)]
    r = client.post("/surveys/1/editor/demographics", data={"part": ["nope"]}, follow_redirects=True)
    assert "Keine Frage angekreuzt" in r.data.decode()
    client.post("/surveys/1/editor/demographics", data={"part": ["ges"]})   # a second time: names stay unique
    with app.app_context():
        codes = [i.code for b in db.session.get(Survey, 1).blocks for i in b.items]
        assert codes.count("GES_1") == 1 and "GES_1_2" in codes
        assert sum(1 for b in db.session.get(Survey, 1).blocks if (b.settings or {}).get("demographics")) == 1


def test_wizard_default_gender_is_ges1_and_the_bundle_can_be_chosen(app, client):
    app.config["NEW_SURVEY_DEMOGRAPHICS"] = True
    page = client.get("/surveys/new").data.decode()
    assert 'name="demografie"' in page and "Demografie kurz (RatSWD)" in page
    client.post("/surveys/new", data={"title": "Kurz", "languages": ["de"], "default_language": "de"})
    assert [k for _c, k, _w in _demo_layout(app, 1)] == ["age", "ratswd_ges1"]
    client.post("/surveys/new", data={"title": "Lang", "languages": ["de"], "default_language": "de",
                                      "demografie": "ratswd"})
    layout = _demo_layout(app, 2)
    assert [k for _c, k, _w in layout if k != "page_break"] == ratswd.bundle_keys(ratswd.PARTS)
    with app.app_context():
        s = db.session.get(Survey, 2)
        assert [b.title() for b in s.blocks][1:] == ["Demographische Angaben", "Block 1"]


def test_census_takes_ges1_as_the_gender_question(app, client, survey):
    client.post("/surveys/1/editor/demographics", data={"part": ["ges"]})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = census.questions(s)
        assert found["gender"] is not None and found["gender"].code == "GES_1"
        assert census.gender_codes(found["gender"]) == {"m": "1", "f": "2"}
        assert census.described(found["gender"], "gender") == "Konstrukt Geschlecht"


def test_ainstein_offers_the_bundle(app, client, monkeypatch):
    from tests.analysis.test_ainstein import TURN, fake_ai, project, switch_on, url

    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN], "ainstein_plan": {"hypotheses": [{"text": "Mehr Vertrauen, mehr Nutzung.", "kind": "correlation",
                                                            "constructs": ["Interpersonales Vertrauen"]}],
                                            "constructs": [{"name": "Interpersonales Vertrauen", "role": "iv",
                                                            "definition": "Vertrauen in andere", "measure": "kusiv3"}]}})
    client.post("/ainstein/new", data={"name": "Vertrauen", "idea": "Vertrauen Menschen anderen? (eine erste grobe Idee "
                                                                    "mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    page = client.get(url(app) + "/completion").get_data(as_text=True)
    assert 'name="demografie"' in page and "Demografie kurz (RatSWD)" in page
    client.post(url(app) + "/survey", data={"languages": "de", "demografie": "ratswd"})
    layout = _demo_layout(app, project(app)["survey_id"])
    assert ("ALT_1", "ratswd_alt1", None) in layout and ("HNE_6", "ratswd_hne6", ("HNE_3", "3")) in layout


def _labels(build):
    """{answer code: German label} of a template of app/ratswd.py."""
    spec = build()
    return {o["code"]: spec["texts"]["de"]["options"][o["id"]] for o in spec["config"]["options"]}


def _euros(label):
    """(lower, upper) bound in euros of an income label: "weniger als 500 Euro", "500 bis unter 1.000 Euro",
    "3.000 Euro und mehr" (upper None)."""
    import re

    numbers = [int(n.replace(".", "")) for n in re.findall(r"\d[\d.]*", label)]
    if label.startswith("weniger als"):
        return 0, numbers[0]
    return numbers[0], (numbers[1] if "bis unter" in label else None)


def test_preset_conditions_match_the_wording():
    """Pre-test 2026-10-20 (7 and 8): the condition the bundle presets must let in everyone the question addresses.
    BER_1 and STE_1 also ask about the former job ("Wenn Sie nicht mehr erwerbstätig sind", "haben oder hatten"), so
    retirees and the others no longer employed get them; ERW_2 asks those without a main job about a side job; each
    income bracket HNE_4 to HNE_6 splits exactly the HNE_3 class its condition names."""
    erw = _labels(ratswd._erw1)
    assert "Altersrente" in erw["4"]
    assert "nicht mehr erwerbstätig" in ratswd._ber1()["texts"]["de"]["question"]
    assert "haben oder hatten" in ratswd._ste1()["texts"]["de"]["question"]
    for key in ("ratswd_ber1", "ratswd_ste1"):
        source, codes = ratswd.FILTERS[key]
        assert source == "ratswd_erw1"
        # employed now or possibly before: everyone but pupils/students and volunteers
        assert set(codes) == set(erw) - {"3", "8", "-1"}, key
    source, codes = ratswd.FILTERS["ratswd_erw2"]
    assert source == "ratswd_erw1" and set(codes) == set(erw) - {"1", "2", "-1"}  # all without a main job
    hne3 = {c: _euros(label) for c, label in _labels(ratswd._hne3).items() if not c.startswith("-")}
    for key, build in (("ratswd_hne4", ratswd._hne4), ("ratswd_hne5", ratswd._hne5), ("ratswd_hne6", ratswd._hne6)):
        source, codes = ratswd.FILTERS[key]
        assert source == "ratswd_hne3" and len(codes) == 1, key
        low, high = hne3[codes[0]]
        bounds = [_euros(label) for c, label in _labels(build).items() if not c.startswith("-")]
        # the brackets start at the class's floor, end at its ceiling and follow one another without a gap
        assert bounds[0][0] == low and bounds[-1][1] == high, key
        assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:])), key
