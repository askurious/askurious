import json
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import results
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add


def setup(client, mode="required"):
    add(client, "postal_code")                                      # 2
    client.post("/surveys/1/editor/items/2", data={"code": "plz", "question": "Ihre PLZ?", "answer_mode": mode})


def test_answer_validation(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        parse = lambda v, lang="de": item.kind.parse_answer(item, MultiDict({"i2": v}), lang)  # noqa: E731
        assert parse("52062") == "52062" and parse(" 01 067 ") == "01067"      # leading zero stays, spaces go
        for bad in ("5206", "520622", "52O62", "D-52062"):
            with pytest.raises(AnswerError, match="fünfstellige"):
                parse(bad)
        with pytest.raises(AnswerError, match="five-digit"):
            parse("123", "en")
        with pytest.raises(AnswerError, match="Bitte geben Sie eine Postleitzahl ein"):
            parse("")
    setup(client, mode="optional")                                  # 3
    with app.app_context():
        item = db.session.get(Item, 3)
        assert item.kind.parse_answer(item, MultiDict({"i3": ""})) is None
        assert item.kind.soft_warning(item, "52062", "de") is None                # known code
        assert "00000" in item.kind.soft_warning(item, "00000", "de")             # unknown: warn once, keep


def test_geonames_table():
    from app.items import postal_code

    t = postal_code.table()
    assert len(t) > 8000 and all(len(k) == 5 for k in list(t)[:100])
    assert postal_code.place("52062") == "Aachen" and postal_code.place("00000") == ""
    lat, lon = t["01067"][1:]
    assert 50.9 < lat < 51.2 and 13.5 < lon < 14                              # Dresden


def _region_topology(client, region):
    """A region file as the browser gets it (the test client does not unpack gzip)."""
    import gzip
    import json

    from app.items import postal_code

    r = client.get(f"/geo/postcode/{region}.json?v={postal_code.areas_version()}")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip" and r.mimetype == "application/json"
    assert "max-age=31536000" in r.headers["Cache-Control"]
    return json.loads(gzip.decompress(r.data))


def test_region_files_for_the_map(app, client):
    from app.items import postal_code

    topo = _region_topology(client, "52")                              # Aachen and around
    assert {"52062", "52064"} <= set(topo["areas"]) and all(plz.startswith("52") for plz in topo["areas"])

    def ids(arcs):
        for a in arcs:
            if isinstance(a, list):
                yield from ids(a)
            else:
                yield a if a >= 0 else ~a

    assert set(ids(a["arcs"] for a in topo["areas"].values())) == set(range(len(topo["arcs"])))  # every arc is used

    def ring(ids):
        pts = []
        for n, i in enumerate(ids):
            arc = topo["arcs"][i if i >= 0 else ~i]
            xy, x, y = [], 0, 0
            for dx, dy in arc:
                x, y = x + dx, y + dy
                xy.append((x / 1e5, y / 1e5))
            xy = xy if i >= 0 else xy[::-1]
            pts += xy[1:] if n else xy
        return pts

    g = topo["areas"]["52062"]
    outer = ring(g["arcs"][0] if g["type"] == "Polygon" else g["arcs"][0][0])
    assert outer[0] == outer[-1] and len(outer) > 20                  # closed ring with real detail
    assert all(50.7 < lat < 50.85 and 6.0 < lon < 6.2 for lon, lat in outer)   # Aachen city centre
    # the list of codes with an area is the content of the region files
    codes = postal_code.area_codes()
    assert set(topo["areas"]) == {c for c in codes if c.startswith("52")} and len(codes) > 8000
    assert "99999" not in codes and postal_code.region_file("52") and postal_code.region_file("00") is None
    # nothing but two digits names a file; without the version the browser asks again; signed out: no file
    for bad in ("5", "520", "ab", "..", "52.json", "%2e%2e", "５２"):
        assert client.get(f"/geo/postcode/{bad}.json").status_code == 404, bad
    assert postal_code.region_file("../52") is None and postal_code.region_file("５２") is None
    assert "max-age=31536000" not in client.get("/geo/postcode/52.json").headers.get("Cache-Control", "")
    assert client.get("/geo/postcode/52.json?v=old").status_code == 200
    assert app.test_client().get("/geo/postcode/52.json").status_code in (302, 401)


def test_area_codes_without_the_list(monkeypatch, tmp_path):
    """codes.txt missing (an older checkout of the data): the codes are read from the region files."""
    import shutil

    from app.items import postal_code

    listed = postal_code.area_codes()
    for name in ("52.json.gz", "01.json.gz"):
        shutil.copy(postal_code.region_file(name[:2]), tmp_path / name)
    monkeypatch.setattr(postal_code, "AREAS", str(tmp_path))
    postal_code.area_codes.cache_clear()
    try:
        assert postal_code.area_codes() == {c for c in listed if c[:2] in ("52", "01")}
    finally:
        postal_code.area_codes.cache_clear()


def test_classes():
    from app.items.postal_code import classes

    assert classes([]) == [] and [c["lo"] for c in classes([3])] == [3]
    many = classes([1] * 50 + [2] * 20 + [3] * 10 + [4, 5, 6, 8, 12, 30])
    assert len(many) == 5 and many[0] == {"lo": 1, "hi": 1, "color": "var(--seq-1)"} and many[-1]["hi"] == 30
    assert all(a["hi"] + 1 == b["lo"] for a, b in zip(many, many[1:]))


def test_results_export_and_spss(app, client, survey):
    setup(client)
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for plz in ("52062", "52062", "52074", "01067", "80331"):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": plz}))
        db.session.commit()
        r = results.compute(s)
        e = next(e for e in r["items"] if e["item"].code == "plz")
        p = e["postal"]
        assert e["answered"] == 5 and e["columns"] == []             # no mean of postal codes
        assert (p["n"], p["distinct"], p["regions_n"], p["singles"]) == (5, 4, 3, 3)
        assert [(z["value"], z["count"]) for z in p["zones"] if z["count"]] == [("0", 1), ("5", 3), ("8", 1)]
        assert len(p["zones"]) == 10 and p["zones"][5]["label"].startswith("Köln")
        assert [(x["value"], x["count"]) for x in p["regions"]] == [("01", 1), ("52", 3), ("80", 1)]
        assert p["top"][0] == {"value": "52062", "label": "Aachen", "count": 2, "share": 0.4}
        assert sorted(p["places"]) == ["01067", "52062", "52074", "80331"] and p["without_area"] == 0
        assert p["area_regions"] == ["01", "52", "80"] and "topology" not in p     # the browser loads the boundaries
        assert p["counts"]["52062"] == 2 and p["places"]["52062"] == "Aachen" and "OpenStreetMap" in p["map"]["attribution"]
        assert [(c["lo"], c["hi"]) for c in p["classes"]] == [(1, 1), (2, 2)]
        book = {row[0]: row for row in export.codebook(s)}
        assert book["plz"][2] == "Postleitzahl" and book["plz_ort"][2] == "Text"
        _, _, meta = export._spss_dictionary(s, False, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert meta["plz"]["kind"] == "str"                          # 01067 must not become 1067
        csv = export.to_csv(s)
        assert "01067" in csv and "Dresden Innere Altstadt" in csv and "plz_ort" in csv.splitlines()[0]
    html = client.get("/surveys/1/results").data.decode()
    assert "Nach Zone" in html and "Häufigste Postleitzahlen" in html and "52062" in html
    assert "data-postal-map" in html and "postal_map.js" in html
    data = json.loads(html.split("data-postal-data>")[1].split("</script>")[0])
    assert data["regions"] == ["01", "52", "80"] and data["counts"]["52062"] == 2 and "topology" not in data
    topo = _region_topology(client, "52")                              # the address the page names
    assert data["area_url"].replace("RR", "52").split("?v=")[1] and "52074" in topo["areas"]
    assert client.get(data["area_url"].replace("RR", "52")).status_code == 200
    assert len(html) < 200_000                                         # the boundaries are not part of the page


def test_preview_and_dummy(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'name="i2"' in html and 'autocomplete="postal-code"' in html and "Ihre PLZ?" in html
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 5, seed=3)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(len(a) == 5 and a.isdigit() for a in answers)
        from app.items import postal_code

        assert all(a in postal_code.table() and a in postal_code.area_codes() for a in answers)  # real codes with an area


def test_random_code_exists():
    import random

    from app.items import postal_code

    rng = random.Random(4)
    codes = [postal_code.random_code(rng) for _ in range(30)]
    assert len(set(codes)) > 20 and all(c in postal_code.table() for c in codes)


def test_unknown_postal_codes_are_counted_apart(app, client, survey):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for plz in ("52062", "52062", "01067", "00000"):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": plz}))
        db.session.commit()
        e = next(e for e in results.compute(s)["items"] if e["item"].code == "plz")
        p = e["postal"]
        assert (p["n"], p["unknown"], p["distinct"], p["regions_n"]) == (3, 1, 2, 2)      # was 4 answers, region "00"
        assert [(z["value"], z["count"]) for z in p["zones"] if z["count"]] == [("0", 1), ("5", 2)]
        assert e["answered"] == 4 and abs(sum(z["share"] for z in p["zones"]) - 1) < 1e-9
    assert "1 unbekannte Postleitzahl</strong>" in client.get("/surveys/1/results").data.decode()
