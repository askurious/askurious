import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Survey
from tests.conftest import add


def setup(client, **extra):
    add(client, "map_marker")                                       # 2
    data = MultiDict({"code": "site", "question": "Wo soll die Ladesäule hin?", "answer_mode": "required",
                      "min": "1", "max": "2", "shape": "star", "color": "#0055ff", "center_lat": "50.7753",
                      "center_lon": "6.0839", "zoom": "13", "search": "1",
                      "mq_id": ["a1", "b2", "c3"], "mq_text": ["Warum hier?", "Wie wichtig?", ""],
                      "mq_kind": ["text", "scale", "text"], "mq_points": ["5", "5", "5"],
                      "mq_left": ["", "unwichtig", ""], "mq_right": ["", "sehr wichtig", ""],
                      "mq_required": ["", "1", ""], "mq_code": ["why", "", ""]})
    for k, v in extra.items():
        data.setlist(k, v if isinstance(v, list) else [v])
    client.post("/surveys/1/editor/items/2", data=data)


def marker(lat=50.77, lon=6.08, **q):
    return {"lat": lat, "lon": lon, "q": q}


def parse(item, markers):
    return item.kind.parse_answer(item, MultiDict({"i2": json.dumps(markers)}))


def test_editor_saves_config_and_texts(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        c = item.config
        assert (c["min"], c["max"], c["shape"], c["color"], c["zoom"], c["search"]) == (1, 2, "star", "#0055ff", 13, True)
        assert c["center"] == [50.7753, 6.0839]
        assert [(q["id"], q["kind"], q["required"]) for q in c["questions"]] == [("a1", "text", False), ("b2", "scale", True)]
        qs = item.kind.questions(item, "de")
        assert [q["export_code"] for q in qs] == ["why", "q2"] and qs[1]["left"] == "unwichtig"
        assert item.kind.validate(item) == []


def test_editor_rejects_bad_values(app, client, survey):
    setup(client, max="99", shape="blob", color="red", center_lat="123", zoom="40")
    with app.app_context():
        item = db.session.get(Item, 2)
        c = item.config
        assert c["max"] == 20 and c["shape"] == "pin" and c["color"] == "#d62828" and c["zoom"] == 19
        assert c["center"] == [51.1657, 10.4515]                      # invalid latitude keeps the default
        item.config = {**c, "min": 5, "max": 3}
        assert "Mindestanzahl ist größer als Höchstanzahl" in item.kind.validate(item)


def test_validate_question_codes(app, client, survey):
    setup(client, mq_code=["lat", "", ""])
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("lat" in p for p in item.kind.validate(item))


def test_answer_validation(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match="mindestens einen"):  # required, nothing placed
            item.kind.parse_answer(item, MultiDict({"i2": ""}))
        with pytest.raises(AnswerError, match="höchstens 2"):
            parse(item, [marker(b2=3)] * 3)
        with pytest.raises(AnswerError, match="ungültige Position"):
            parse(item, [marker(lat=95, b2=3)])
        with pytest.raises(AnswerError, match="ungültige Position"):
            parse(item, [marker(lon="x", b2=3)])
        with pytest.raises(AnswerError, match="Fragen zu Marker 2"):  # b2 is required for every marker
            parse(item, [marker(b2=3), marker()])
        with pytest.raises(AnswerError, match="Ungültige Antwort"):
            parse(item, [marker(b2=6)])                               # 5-point scale
        answer = parse(item, [marker(50.1234567, 6.7654321, a1="  nah am Bahnhof ", b2="4", zz="junk"), marker(b2=1)])
        assert answer == [{"lat": 50.123457, "lon": 6.765432, "q": {"a1": "nah am Bahnhof", "b2": 4}},
                          {"lat": 50.77, "lon": 6.08, "q": {"b2": 1}}]
        assert item.kind.is_missing(item, answer)                     # the second marker has no text answer
        assert not item.kind.is_missing(item, [answer[0]])


def test_minimum_and_optional(app, client, survey):
    setup(client, answer_mode="optional", min="2", mq_required=["", "", ""])
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2": ""})) is None
        with pytest.raises(AnswerError, match="mindestens 2"):
            parse(item, [marker()])
        assert len(parse(item, [marker(), marker(1, 2)])) == 2


def test_export_and_codebook(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols == ["site_n", "site_spread_m", "site_maxdist_m", "site_mindist_m", "site_path_m", "site_mst_m", "site_density", "site_1_lat", "site_1_lon", "site_1_why", "site_1_q2",
                        "site_2_lat", "site_2_lon", "site_2_why", "site_2_q2", "site_json"]
        values = item.kind.export_values(item, [{"lat": 50.5, "lon": 6.5, "q": {"a1": "Bahnhof", "b2": 4}}])
        assert values["site_n"] == 1 and values["site_1_lat"] == 50.5 and values["site_1_why"] == "Bahnhof"
        assert values["site_1_q2"] == 4 and values["site_2_lat"] is None and values["site_2_q2"] is None
        assert json.loads(values["site_json"]) == [{"lat": 50.5, "lon": 6.5, "why": "Bahnhof", "q2": 4}]
        assert all(v is None for v in item.kind.export_values(item, None).values())

        from app import export

        book = {row[0]: row for row in export.codebook(db.session.get(Survey, 1))}
        assert book["site_n"][2] == "Ganzzahl" and "höchstens 2" in book["site_n"][4]
        assert book["site_2_lat"][2] == "Zahl" and "WGS84" in book["site_2_lat"][4]
        assert book["site_1_q2"][2] == "Skala" and book["site_1_q2"][3] == "1 = unwichtig; 5 = sehr wichtig"
        assert "Pflicht" in book["site_1_q2"][4] and book["site_1_why"][2] == "Text"
        assert book["site_json"][2] == "JSON"
        assert "site_2_why" in [c for c, _ in export.columns(db.session.get(Survey, 1))]


def test_participant_page_preview_and_dummy(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-map-marker="2"' in html and "vendor/leaflet/leaflet.js" in html and "map_marker.js" in html
    assert "nominatim.openstreetmap.org" in html and "Warum hier?" in html
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-map-editor" in editor and 'name="center_lat"' in editor and 'value="50.7753"' in editor
    assert client.get("/static/vendor/leaflet/leaflet.js").status_code == 200
    with app.app_context():
        from app import dummy
        from app.models import Response

        dummy.generate(db.session.get(Survey, 1), 3, seed=4)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(1 <= len(a) <= 2 and all(isinstance(m["q"].get("b2"), int) for m in a) for a in answers)


def test_search_can_be_switched_off_and_privacy_notice(app, client, survey):
    setup(client, search=[])
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert '"search": ""' in html.replace("&#34;", '"') or '"search":""' in html.replace("&#34;", '"')
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    page = client.get(f"/s/{uid}/privacy").data.decode()
    assert "Diese Befragung enthält eine Karte" in page


def test_csv_and_spss_with_a_response(app, client, survey):
    setup(client)
    with app.app_context():
        from app import export
        from app.models import Response

        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": [{"lat": 50.5, "lon": 6.25, "q": {"a1": "Bahnhof", "b2": 5}}]},
                                    status=Response.COMPLETED))
        db.session.commit()
        csv = export.to_csv(s, include_incomplete=True)
        head, row = csv.lstrip("﻿").splitlines()[:2]
        assert "site_1_lat" in head and "50.5" in row and "Bahnhof" in row
        assert export.spss_sav(s, include_incomplete=True)[:4] == b"$FL2"


def test_results_page_map(app, client, survey):
    setup(client, result_view="markers")
    with app.app_context():
        from app import results
        from app.models import Response

        s = db.session.get(Survey, 1)
        done = [{"lat": 50.5, "lon": 6.25, "q": {"a1": "Bahnhof", "b2": 5}}, {"lat": 50.6, "lon": 6.3, "q": {"b2": 3}}]
        s.responses.append(Response(answers={"2": done}, status=Response.COMPLETED))
        s.responses.append(Response(answers={"2": [{"lat": 10, "lon": 10, "q": {"b2": 1}}]}))   # not finished
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        m = entry["map"]
        assert [c["code"] for c in entry["columns"]] == ["site_spread_m", "site_maxdist_m", "site_mindist_m", "site_path_m", "site_mst_m", "site_density"] and entry["answered"] == 1
        assert (m["n"], m["persons"], m["mean"]) == (2, 1, 2)          # the unfinished run is left out
        assert [(p["lat"], p["k"]) for p in m["points"]] == [(50.5, 1), (50.6, 2)]
        assert m["points"][0]["a"] == [["Warum hier?", "Bahnhof"], ["Wie wichtig?", 5]]
        scale = next(x for x in m["summary"] if x["q"]["kind"] == "scale")
        assert scale["n"] == 2 and scale["mean"] == 4
        assert m["settings"]["view"] == "markers" and m["settings"]["shape"] == "star"
        assert len(results.compute(s, include_incomplete=True)["items"][-1]["map"]["points"]) == 3
    html = client.get("/surveys/1/results").data.decode()
    assert "data-map-results" in html and "leaflet-heat.js" in html and "map_results.js" in html
    assert "2 Marker von 1 Teilnahmen" in html and "Alle Marker" in html


def test_distance_and_spread():
    from app.items.map_marker import distance_m, spread

    assert distance_m((0, 0), (0, 1)) == pytest.approx(111195.08, abs=0.1)     # 1° along the equator
    assert distance_m((48.8566, 2.3522), (51.5074, -0.1278)) == pytest.approx(343_560, abs=800)  # Paris-London
    assert distance_m((50.0, 179.9), (50.0, -179.9)) < 20_000                  # across the date line
    assert spread(None) == (None, None) and spread([]) == (None, None)
    assert spread([{"lat": 50, "lon": 6}]) == (None, None)                      # one marker: not defined
    assert spread([{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]) == (111195, 111195)
    three = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}, {"lat": 0, "lon": 2}]   # pairs: 1°, 1°, 2°
    assert spread(three) == (round(4 * 111195.08 / 3), round(2 * 111195.08))
    assert spread([{"lat": 0, "lon": 0}, {"lat": 95, "lon": 0}, "x"]) == (None, None)   # invalid ones ignored


def test_spread_in_export_codebook_spss_and_results(app, client, survey):
    setup(client)
    with app.app_context():
        from app import export, results
        from app.models import Response

        item = db.session.get(Item, 2)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols[:3] == ["site_n", "site_spread_m", "site_maxdist_m"]
        two = [{"lat": 0, "lon": 0, "q": {"b2": 1}}, {"lat": 0, "lon": 1, "q": {"b2": 1}}]
        v = item.kind.export_values(item, two)
        assert v["site_spread_m"] == v["site_maxdist_m"] == 111195
        one = item.kind.export_values(item, two[:1])
        assert one["site_n"] == 1 and one["site_spread_m"] is None and one["site_maxdist_m"] is None
        assert item.kind.export_values(item, None)["site_spread_m"] is None

        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": two}, status=Response.COMPLETED))
        s.responses.append(Response(answers={"2": [{"lat": 0, "lon": 0, "q": {"b2": 2}}, {"lat": 0, "lon": 3, "q": {"b2": 2}}]},
                                    status=Response.COMPLETED))
        s.responses.append(Response(answers={"2": two[:1]}, status=Response.COMPLETED))
        db.session.commit()
        book = {row[0]: row for row in export.codebook(s)}
        assert book["site_spread_m"][2] == "Zahl" and "Haversine" in book["site_spread_m"][4]
        assert "weniger als 2" in book["site_maxdist_m"][4]
        _, _, meta = export._spss_dictionary(s, False, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert meta["site_spread_m"]["kind"] == "int" and meta["site_spread_m"]["level"] == "SCALE"
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        col = next(c for c in entry["columns"] if c["code"] == "site_spread_m")
        assert col["type"] == "num" and col["n"] == 2 and col["unit"] == "m"
        assert col["mean"] == pytest.approx((111195 + 333585) / 2, abs=1)
        assert [c["code"] for c in entry["columns"]] == ["site_spread_m", "site_maxdist_m", "site_mindist_m", "site_path_m", "site_mst_m", "site_density"]
    html = client.get("/surveys/1/results").data.decode()
    assert "site_spread_m" in html and "data-map-results" in html


def choice_setup(client):
    """Follow-up questions: a text and a single choice with three options."""
    setup(client, mq_id=["a1", "c3"], mq_text=["Warum hier?", "Womit kommen Sie hin?"], mq_kind=["text", "choice"],
          mq_points=["5", "5"], mq_left=["", ""], mq_right=["", ""], mq_required=["", "1"], mq_code=["why", "weg"],
          mq_options=["", "Zu Fuß\n\nRad\nAuto  "])


def test_single_choice_as_follow_up_question(app, client, survey):
    choice_setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        q = item.config["questions"][1]
        assert q["kind"] == "choice" and len(q["options"]) == 3 and item.kind.validate(item) == []
        assert item.kind.questions(item, "de")[1]["labels"] == ["Zu Fuß", "Rad", "Auto"]
        assert item.kind.map_settings(item, "de")["questions"][1]["options"] == ["Zu Fuß", "Rad", "Auto"]
        assert parse(item, [marker(c3=2)]) == [{"lat": 50.77, "lon": 6.08, "q": {"c3": 2}}]
        for bad in (0, 4, "Rad"):
            with pytest.raises(AnswerError):
                parse(item, [marker(c3=bad)])
        with pytest.raises(AnswerError):
            parse(item, [marker()])                                    # required
        assert item.kind.codebook_info(item, "site_1_weg", "de") == ("Auswahl", "1 = Zu Fuß; 2 = Rad; 3 = Auto",
                                                                     ["Zusatzfrage je Marker (Pflicht)"])
        ids = list(q["options"])
    # the wording changes, the options keep their ids (translations stay); one option less drops the last
    choice_setup(client)
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "site", "question": "Wo?", "min": "1", "max": "2", "center_lat": "50.7753", "center_lon": "6.0839",
        "zoom": "13", "mq_id": ["a1", "c3"], "mq_text": ["Warum hier?", "Womit?"], "mq_kind": ["text", "choice"],
        "mq_points": ["5", "5"], "mq_left": ["", ""], "mq_right": ["", ""], "mq_required": ["", "1"],
        "mq_code": ["why", "weg"], "mq_options": ["", "zu Fuß\nmit dem Rad"]}))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["questions"][1]["options"] == ids[:2]
        assert item.kind.questions(item, "de")[1]["labels"] == ["zu Fuß", "mit dem Rad"]
        item.config = {**item.config, "questions": [item.config["questions"][0],
                                                    {**item.config["questions"][1], "options": ids[:1]}]}
        assert any("mindestens zwei" in p for p in item.kind.validate(item))
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-mq-kind="choice"' in html and "zu Fuß&#10;mit dem Rad</textarea>" in html


def test_long_format_one_row_per_marker(app, client, survey):
    choice_setup(client)
    with app.app_context():
        from app import export, zenodo
        from app.models import Response

        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": [marker(50.5, 6.25, a1="Bahnhof", c3=3), marker(50.6, 6.3, c3=1)]},
                                    status=Response.COMPLETED))
        s.responses.append(Response(answers={}, status=Response.COMPLETED))     # no marker: no row
        db.session.commit()
        item = db.session.get(Item, 2)
        lines = export.long_csv(s, item).lstrip("\ufeff").splitlines()
        assert lines == ["resp_id,source,marker,lat,lon,why,weg,weg_label",
                         "1,real,1,50.5,6.25,Bahnhof,3,Auto", "1,real,2,50.6,6.3,,1,Zu Fuß"]
        # a public data package holds the exact positions only when they are wanted
        assert not [n for n, _ in zenodo.package(s) if n.startswith("map_")]
        assert "map_site_long.csv" in [n for n, _ in zenodo.package(s, open_answers=True)]
        assert "map_*_long.csv" not in export.readme_md(s) and "map_*_long.csv" in export.readme_md(s, True)
        results = __import__("app.results", fromlist=["compute"]).compute(s)
        m = next(e for e in results["items"] if e["item"].id == 2)["map"]
        assert m["points"][0]["a"] == [["Warum hier?", "Bahnhof"], ["Womit kommen Sie hin?", "Auto"]]
        choice = next(x for x in m["summary"] if x["q"]["kind"] == "choice")
        assert choice["n"] == 2 and choice["counts"][0][1] == 1 and [c for _, c in choice["counts"]] == [1, 1, 0]
    data = client.get("/surveys/1/data").data.decode()
    assert "Person × Marker" in data and "Langformat" in data
    r = client.get("/surveys/1/export/conjoint/2.csv")
    assert r.status_code == 200 and "marker,lat,lon" in r.data.decode()
    html = client.get("/surveys/1/results").data.decode()
    assert "Auto: 1 (50 %)" in html


DEG = 111195.08  # metres per degree along the equator (mean earth radius)


def test_network_reference_cases():
    from app.items.map_marker import network

    on_line = [{"lat": 0, "lon": x} for x in (0, 3, 1, 2)]          # four markers on the equator, placed out of order
    assert network(on_line, threshold_m=150_000) == {
        "mindist_m": round(DEG), "path_m": round(3 * DEG), "mst_m": round(3 * DEG), "density": 0.5}  # 3 of 6 pairs at 1°
    square = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01}, {"lat": 0.01, "lon": 0.01}, {"lat": 0.01, "lon": 0}]
    side = 0.01 * DEG
    net = network(square, threshold_m=round(side) + 1)
    assert net["mindist_m"] == pytest.approx(side, abs=1)
    assert net["path_m"] == pytest.approx(3 * side, abs=2) and net["mst_m"] == pytest.approx(3 * side, abs=2)
    assert net["density"] == pytest.approx(4 / 6, abs=0.001)         # the four sides, not the diagonals
    for few in (None, [], [{"lat": 1, "lon": 1}], [{"lat": 1, "lon": 1}, {"lat": 99, "lon": 1}]):
        assert network(few) == {"mindist_m": None, "path_m": None, "mst_m": None, "density": None}
    two = network([{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}], threshold_m=500)
    assert two == {"mindist_m": round(DEG), "path_m": round(DEG), "mst_m": round(DEG), "density": 0.0}


def test_shortest_path_heuristic_against_exact():
    import random

    from app.items.map_marker import _held_karp, _matrix, _mst, _path_heuristic

    rng = random.Random(7)
    for n in range(3, 10):
        for _ in range(6):
            d = _matrix([(rng.uniform(50, 51), rng.uniform(6, 7)) for _ in range(n)])
            exact, approx = _held_karp(d), _path_heuristic(d)
            assert exact <= approx + 1e-6 and approx <= exact * 1.05     # never shorter than the optimum, close to it
            assert _mst(d) <= exact + 1e-6                                 # a path is a spanning tree too
    pts = [(0, x * 0.1) for x in rng.sample(range(20), 20)]             # 20 markers on a line: the optimum is known
    assert _path_heuristic(_matrix(pts)) == pytest.approx(1.9 * DEG, abs=1)


def test_network_in_export_codebook_editor_and_results(app, client, survey):
    setup(client, density_m="200000")
    with app.app_context():
        from app import export, results
        from app.models import Response

        item = db.session.get(Item, 2)
        assert item.config["density_m"] == 200000 and item.kind.validate(item) == []
        far = [{"lat": 0, "lon": 0, "q": {"b2": 1}}, {"lat": 0, "lon": 3, "q": {"b2": 1}}]
        near = [{"lat": 0, "lon": 0, "q": {"b2": 1}}, {"lat": 0, "lon": 1, "q": {"b2": 1}}]
        v = item.kind.export_values(item, near)
        assert (v["site_mindist_m"], v["site_path_m"], v["site_mst_m"], v["site_density"]) == (111195, 111195, 111195, 1.0)
        assert item.kind.export_values(item, far)["site_density"] == 0.0
        s = db.session.get(Survey, 1)
        for answer in (near, far, near[:1]):
            s.responses.append(Response(answers={"2": answer}, status=Response.COMPLETED))
        db.session.commit()
        book = {row[0]: row for row in export.codebook(s)}
        assert "200000 m" in book["site_density"][4] and book["site_density"][3] == "0 bis 1"
        assert "Held & Karp" in book["site_path_m"][4] and "Prim" in book["site_mst_m"][4]
        _, _, meta = export._spss_dictionary(s, False, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert meta["site_path_m"]["level"] == meta["site_density"]["level"] == "SCALE"
        assert meta["site_density"]["kind"] in ("int", "num") and not meta["site_density"]["pairs"]  # by the data, no labels
        data = results.compute(s)
        entry = next(e for e in data["items"] if e["item"].id == 2)
        density = next(c for c in entry["columns"] if c["code"] == "site_density")
        assert density["n"] == 2 and density["mean"] == 0.5 and density["unit"] == ""
        headings = [h for h, _ in results.page_sources(data)]
        assert any("Held-Karp" in h for h in headings) and any("Spannbaum" in h for h in headings)
        assert any("Graphendichte" in h for h in headings)
    with app.app_context():                                          # a broken threshold falls back to the default
        item = db.session.get(Item, 2)
        item.config = {**item.config, "density_m": "x"}
        assert item.kind.density_m(item) == 500
    client.post("/surveys/1/editor/items/2", data={"code": "site", "question": "Wo?", "density_m": "abc", "max": "2"})
    with app.app_context():
        assert db.session.get(Item, 2).config["density_m"] == 200000   # an invalid entry keeps the stored threshold
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="density_m"' in html

