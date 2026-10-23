import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add


def setup_pos(client, required=True, allow_skip=False):
    add(client, "position2d")                                                     # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "tech"), ("question", "Wo sehen Sie diese Technologien?"), ("answer_mode", "required" if required else "optional"),
        ("x_label", "Risiko"), ("x_low", "gering"), ("x_high", "hoch"), ("y_label", "Nutzen"), ("y_low", "gering"), ("y_high", "hoch"),
        ("option_id", "a"), ("option_code", "ki"), ("option_label", "KI"),
        ("option_id", "b"), ("option_code", "ak"), ("option_label", "Atomkraft"),
        *([("allow_skip", "1")] if allow_skip else [])]))


def setup_assoc(client, required=True):
    add(client, "association")                                                    # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "assoc"), ("question", "Was fällt Ihnen zu KI ein?"), ("answer_mode", "required" if required else "optional"),
        ("slots", "3"), ("min", "2"), ("rate", "Wie bewerten Sie das?"), ("low", "sehr negativ"), ("high", "sehr positiv")]))


def test_position2d_parse_export_codebook(app, client, survey):
    setup_pos(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.text("x_label") == "Risiko"
        with pytest.raises(AnswerError, match="alle Elemente"):
            item.kind.parse_answer(item, MultiDict({"i2_ki": "20.5,80"}))            # Atomkraft not placed
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, MultiDict({"i2_ki": "20,80", "i2_ak": "120,5"}))  # out of range = not placed
        answer = item.kind.parse_answer(item, MultiDict({"i2_ki": "20.54,80", "i2_ak": "90,10"}))
        assert answer == {"ki": [20.5, 80.0], "ak": [90.0, 10.0]}
        assert item.kind.export_values(item, answer) == {"tech_ki_x": 20.5, "tech_ki_y": 80.0, "tech_ak_x": 90.0, "tech_ak_y": 10.0}
        cols = dict(item.kind.export_columns(item))
        assert "Risiko (0 = gering, 100 = hoch)" in cols["tech_ki_x"] and "Nutzen" in cols["tech_ki_y"]
        assert item.kind.codebook_info(item, "tech_ki_x", "de")[:2] == ("Zahl", "0 bis 100")


def test_position2d_skip(app, client, survey):
    setup_pos(client, allow_skip=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        answer = item.kind.parse_answer(item, MultiDict([("i2_ki", "10,10"), ("i2_skip", "ak")]))
        assert answer == {"ki": [10.0, 10.0], "_skip": ["ak"]} and not item.kind.is_missing(item, answer)
        assert item.kind.export_values(item, answer)["tech_ak_x"] is None


def test_association_parse_export(app, client, survey):
    setup_assoc(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config == {**item.config, "slots": 3, "min": 2}
        with pytest.raises(AnswerError, match="mindestens 2"):
            item.kind.parse_answer(item, MultiDict({"i2_1": "Zukunft", "i2_1_val": "2"}))
        with pytest.raises(AnswerError, match="bewerten"):
            item.kind.parse_answer(item, MultiDict({"i2_1": "Zukunft", "i2_1_val": "2", "i2_2": "Angst"}))
        answer = item.kind.parse_answer(item, MultiDict({"i2_1": "  Zukunft ", "i2_1_val": "2", "i2_2": "Angst", "i2_2_val": "-1",
                                                         "i2_3": "", "i2_3_val": "1"}))
        assert answer == [{"t": "Zukunft", "v": 2}, {"t": "Angst", "v": -1}]
        out = item.kind.export_values(item, answer)
        assert out == {"assoc_1": "Zukunft", "assoc_1_val": 2, "assoc_2": "Angst", "assoc_2_val": -1,
                       "assoc_3": None, "assoc_3_val": None, "assoc_mean": 0.5}
        assert item.kind.codebook_info(item, "assoc_1_val", "de")[1].startswith("-2 = sehr negativ")


def test_preview_dummy_and_results(app, client, survey):
    setup_pos(client)
    add(client, "association")                                                    # item 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([("code", "assoc"), ("question", "Was fällt Ihnen zu KI ein?"),
                                                             ("answer_mode", "optional"), ("slots", "3"), ("min", "1")]))
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-pos="2"' in html and "Atomkraft" in html and "Risiko" in html
    assert 'data-assoc="3"' in html and 'name="i3_1_val" value="-2"' in html
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 8, seed=5)
        db.session.commit()
        rows = list(db.session.scalars(db.select(Response).filter_by(source="dummy")))
        assert rows and all(set(r.answers["2"]) == {"ki", "ak"} for r in rows)
        assert all(1 <= len(r.answers["3"]) <= 3 and all(a["v"] in (-2, -1, 0, 1, 2) for a in r.answers["3"]) for r in rows)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert 'class="pos2d-res"' in html and "Mittlere Position je Element" in html
    assert 'class="res-terms"' in html and "mittlere Bewertung" in html
    csv = client.get("/surveys/1/export/data.csv?source=dummy").data.decode("utf-8-sig").splitlines()[0]
    assert "tech_ki_x" in csv and "assoc_1_val" in csv and "assoc_mean" in csv


def test_sav_export_with_both(app, client, survey):
    setup_pos(client)
    add(client, "association")                                                    # item 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([("code", "assoc"), ("question", "Was fällt Ihnen zu KI ein?"),
                                                             ("answer_mode", "optional"), ("slots", "2"), ("min", "1")]))
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 4, seed=2)
        db.session.commit()
    r = client.get("/surveys/1/export/data.sav?source=dummy")
    assert r.status_code == 200 and r.data[:4] == b"$FL2"
    assert b"tech_ki_x" in r.data.lower() and b"assoc_1_val" in r.data.lower()


def test_in_palette(client, survey):
    html = client.get("/surveys/1/editor/").data.decode()
    assert "2D-Positionierung" in html and "Freie Assoziationen" in html


def test_association_two_steps(app, client, survey):
    setup_assoc(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-assoc="2" >' in html or 'data-assoc="2">' in html                   # words first (phase set by JS)
    assert "p-assoc-step-words" in html and "p-assoc-step-rate" in html and "Begriffe ändern" in html
    from tests.conftest import CONSENT_OK, fill_consent

    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    r = p.post(url, data={"nav": "next", "i2_1": "Zukunft", "i2_2": "Angst"}).data.decode()  # not rated: comes back
    assert 'data-phase="rate"' in r and 'value="Zukunft"' in r and "bewerten" in r                  # in the rating step


def test_editors_list_the_cited_works_in_apa(client, survey):
    """Sources in APA style (Ralf, 2026-10-05): the short references of both editors have their entries at the end."""
    from app import apa
    from app.items import association, position2d

    setup_pos(client)
    page = client.get("/surveys/1/editor/items/2").data.decode()
    for source in (position2d.FISCHHOFF, position2d.SLOVIC, position2d.HOUT):
        assert f'<li id="ref-{apa.ref(source)["key"]}">' in page
    assert "(Fischhoff et al., 1978; Slovic, 1987)" in page and "Literatur zu diesem Element" in page
    add(client, "association")                                                    # item 3
    page = client.get("/surveys/1/editor/items/3").data.decode()
    for source in (association.SLOVIC_FLYNN_LAYMAN, association.PETERS_SLOVIC, association.LEISEROWITZ):
        assert f'<li id="ref-{apa.ref(source)["key"]}">' in page
    assert "Slovic et al., 1991" in page
