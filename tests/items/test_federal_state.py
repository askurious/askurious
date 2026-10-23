from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import results
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add


def setup(client, **options):
    add(client, "federal_state")                                    # 2
    client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Ihr Bundesland?", "answer_mode": "required",
                                                   **options})


def test_states_and_codes(app, client, survey):
    add(client, "federal_state")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("question", "de") == "In welchem Bundesland wohnen Sie?" and item.kind.validate(item) == []
        de = [(o["code"], o["label"]) for o in item.kind.options(item, "de")]
        states = de[:16]
        assert [label for _, label in states] == sorted(label for _, label in states)       # alphabetical
        assert sorted(int(code) for code, _ in states) == list(range(1, 17))                # state keys of the AGS
        assert dict(de)["1"] == "Schleswig-Holstein" and dict(de)["9"] == "Bayern" and dict(de)["16"] == "Thüringen"
        assert de[16:] == [("0", "Ich wohne nicht in Deutschland")]                         # the default
        assert item.kind.missing_codes(item, item.code) == ()
    client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Ihr Bundesland?", "answer_mode": "required",
                                                   "no_answer": "1"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [o["code"] for o in item.kind.options(item, "de")][16:] == ["99"]            # abroad off, no answer on
        assert item.kind.options(item, "de")[-1]["label"] == "keine Angabe" and item.kind.missing_codes(item, "land") == ("99",)
        en = {o["code"]: o["label"] for o in item.kind.options(item, "en")}                 # no English texts stored
        assert en["9"] == "Bavaria" and en["5"] == "North Rhine-Westphalia" and en["99"] == "prefer not to say"
        parse = lambda v, lang="de": item.kind.parse_answer(item, MultiDict({"i2": v}), lang)  # noqa: E731
        assert parse("5") == "5" and parse("99") == "99"
        for bad in ("0", "17", "Bayern"):
            with pytest.raises(AnswerError, match="Ungültige Antwort"):
                parse(bad)
        with pytest.raises(AnswerError, match="Please choose"):
            parse("", "en")


def test_editor_preview_and_conditions(app, client, survey):
    setup(client, abroad="1", no_answer="1")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="abroad" value="1" checked' in page and "Mecklenburg-Vorpommern" in page and "Amtlichen Gemeindeschlüssels" in page
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "Ihr Bundesland?" in html and html.count('type="radio" name="i2"') == 18 and "Ich wohne nicht in Deutschland" in html
    with app.app_context():
        from app import flow

        item = db.session.get(Item, 2)
        codes = flow.answer_codes(item)                              # states work in conditions and quotas
        assert len(codes) == 18 and ("9", "Bayern") in codes and ("0", "Ich wohne nicht in Deutschland") in codes
        from app import conditions

        for everything in (False, True):                             # the pickers of quotas and of the flow
            d = conditions.describe(db.session.get(Survey, 1), everything=everything)["2"]
            assert d["kind"] == "choice" and not d["multi"] and ("9", "Bayern") in d["options"] and len(d["options"]) == 18


def test_areas_file():
    from app.items import federal_state

    topo = federal_state.areas()
    assert sorted(topo["areas"], key=int) == [str(n) for n in range(1, 17)]

    def rings(code):
        g = topo["areas"][code]
        for poly in (g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]):
            for ids in poly:
                pts = []
                for i in ids:
                    x = y = 0
                    for dx, dy in topo["arcs"][i]:
                        x, y = x + dx, y + dy
                        pts.append((x / 1e5, y / 1e5))
                yield pts

    def inside(code, lon, lat):                                      # even-odd rule over all rings
        hit = False
        for pts in rings(code):
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    hit = not hit
        return hit

    for pts in rings("9"):
        assert pts[0] == pts[-1]                                     # closed rings
    cities = {"9": (11.576, 48.137), "11": (13.405, 52.52), "5": (6.084, 50.775), "1": (10.135, 54.323),
              "14": (13.738, 51.05), "2": (9.993, 53.551), "12": (13.06, 52.4)}   # München, Berlin, Aachen, Kiel, ...
    for code, (lon, lat) in cities.items():
        assert [c for c in topo["areas"] if inside(c, lon, lat)] == [code]
    waters = {"Nordsee": (7.9, 53.9), "Wattenmeer": (8.5, 54.5), "Ostsee": (11.9, 54.3), "Pommersche Bucht": (13.9, 54.3),
              "Bodensee": (9.35, 47.62)}                             # land only: the sea is not coloured
    for name, (lon, lat) in waters.items():
        assert [c for c in topo["areas"] if inside(c, lon, lat)] == [], name
    assert [c for c in topo["areas"] if inside(c, 8.31, 54.91)] == ["1"]      # Sylt
    assert [c for c in topo["areas"] if inside(c, 13.43, 54.42)] == ["13"]    # Rügen
    lons = [x for c in topo["areas"] for pts in rings(c) for x, _ in pts]
    lats = [y for c in topo["areas"] for pts in rings(c) for _, y in pts]
    assert 5.8 < min(lons) < 6 and 15 < max(lons) < 15.1 and 47.2 < min(lats) < 47.4 and 54.9 < max(lats) < 55.1


def test_results_export_and_spss(app, client, survey):
    setup(client, abroad="1", no_answer="1")
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for code in ("5", "5", "5", "9", "11", "0", "99"):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": code}))
        db.session.commit()
        r = results.compute(s)
        e = next(e for e in r["items"] if e["item"].code == "land")
        st, col = e["states"], e["columns"][0]
        assert e["answered"] == 7 and col["type"] == "freq"
        assert (st["n"], st["abroad"], st["no_answer"], st["empty"]) == (5, 1, 1, 13)
        # the map names its shares on the base of the list (D-12 of the maths audit 2026-10-03): with "nicht in
        # Deutschland", without "keine Angabe"
        assert st["base"] == 6 == e["columns"][0]["n"]
        assert st["counts"]["5"] == 3 and st["counts"]["1"] == 0 and len(st["counts"]) == 16
        assert st["names"]["5"] == "Nordrhein-Westfalen" and [(c["lo"], c["hi"]) for c in st["classes"]] == [(1, 1), (3, 3)]
        assert len(st["topology"]["areas"]) == 16 and "OpenStreetMap" in st["map"]["attribution"] and "BKG" in st["map"]["attribution"]
        rows = {row["value"]: row for row in col["rows"]}
        assert rows["5"]["label"] == "Nordrhein-Westfalen" and rows["5"]["count"] == 3
        book = {row[0]: row for row in export.codebook(s)}
        assert book["land"][2] == "Bundesland" and "9 = Bayern" in book["land"][3] and "als fehlend gewertet: 99" in book["land"][4]
        _, _, meta = export._spss_dictionary(s, False, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert meta["land"]["kind"] != "str"
        assert "`9` Bayern" in export.questionnaire_md(s)
    html = client.get("/surveys/1/results").data.decode()
    assert "res-state-map" in html and "Antworten je Bundesland" in html          # outline map, no tiles (V58)
    assert "<strong>1</strong> nicht in Deutschland" in html and "<strong>1</strong> ohne Angabe" in html
    assert "Anteil an allen 6 Antworten (auch „nicht in Deutschland“), wie in der Liste unten" in html


def test_dummy(app, client, survey):
    setup(client)
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 8, seed=3)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(a in [str(n) for n in range(0, 17)] for a in answers)


def test_map_to_click_on(app, client, survey):
    """Editor: "Darstellung" offers radio buttons, the drop-down list and a map of the outlines with the names."""
    import re

    from app.items import federal_state

    setup(client, abroad="1")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert '<select name="display"' in page and 'value="buttons" selected' in page and "Karte zum Anklicken" in page
    plain = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-state-pick" not in plain and plain.count('type="radio" name="i2"') == 17

    def show(display):
        client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Ihr Bundesland?", "answer_mode": "required",
                                                       "abroad": "1", "display": display})
        with app.app_context():
            item = db.session.get(Item, 2)
            return item.config.get("display"), item.kind.one_click(item)

    assert show("dropdown") == ("dropdown", False)                  # the list of the single choice still works
    assert "<select id=\"s2\"" in client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert show("map") == ("map", True)                              # a click on a state completes the answer
    assert 'value="map" selected' in client.get("/surveys/1/editor/items/2").data.decode()
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-state-pick" in html and html.count('class="p-state-area"') == 16 and html.count('class="p-state-hit"') == 4
    assert html.count('type="radio" name="i2"') == 17 and html.count("data-state-option") == 16   # the buttons stay
    assert re.search(r'<use class="p-state-area" data-code="9" href="/static/geo/state-outlines.svg\?v=[a-f0-9]+#state-9"><title>Bayern</title>', html)
    assert "<tspan x=" in html and ">Nordrhein-</tspan>" in html and ">Westfalen</tspan>" in html and "Ausgewählt:" in html
    assert "tile.openstreetmap.org" not in html and "leaflet" not in html                        # no map service
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2": "9"})) == "9"                       # answers as before
        drawing = federal_state.outline()
        assert sorted(drawing["paths"], key=int) == [str(n) for n in range(1, 17)]
        assert 500 < drawing["width"] < 650 and 700 < drawing["height"] < 850
        assert all(0 < x < drawing["width"] and 0 < y < drawing["height"] for x, y in drawing["at"].values())
        assert sum(len(p) for p in drawing["paths"].values()) < 60000                            # small enough for a page
        assert federal_state.label_lines("Bayern") == ["Bayern"] and federal_state.label_lines("Lower Saxony") == ["Lower Saxony"]
        assert federal_state.label_lines("Mecklenburg-Vorpommern") == ["Mecklenburg-", "Vorpommern"]
    assert show("buttons") == (None, True)                           # the default leaves no key in the config
