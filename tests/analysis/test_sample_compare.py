from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.conftest import add


def _answer(n, s1, s2, choice):
    return {"2": {s1: n % 5 + 1, s2: 3}, "3": choice}


def test_sample_compare_threshold_own_answer_and_export(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kusiv3"})   # 2
    add(client, "single_choice")                                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "fav"), ("question", "Lieblingsfarbe"), ("answer_mode", "optional"),
        ("option_id", ""), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", ""), ("option_code", "2"), ("option_label", "blau")]))
    add(client, "page_break")                                                              # 4
    add(client, "sample_compare")                                                          # 5
    client.post("/surveys/1/editor/items/5", data=MultiDict([
        ("code", "live"), ("question", "So haben andere geantwortet"), ("source", "2"), ("show_own", "1")]))
    with app.app_context():
        item = db.session.get(Item, 5)
        assert item.config["sources"] == [2] and item.kind.validate(item) == []
        item.config = {**item.config, "sources": [2, 3]}  # (older elements may list two; rows handle both)
        db.session.commit()
        kusiv = db.session.get(Item, 2)
        s1, s2 = [s["id"] for s in kusiv.config["statements"][:2]]
        for n in range(4):  # below the threshold
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers=_answer(n, s1, s2, "1")))
        db.session.add(Response(survey_id=1, source="dummy", status=Response.COMPLETED, completed_at=utcnow(),
                                answers=_answer(9, s1, s2, "2")))
        db.session.commit()
        rows = item.kind.rows(item, "de", {2: {s1: 2}})
        assert len(rows) == 4 and not any(r["enough"] for r in rows)       # 3 statements + choice, 4 real
        assert [r["enough"] for r in item.kind.rows(item, "de", {}, preview=True)] == [True, True, False, True]  # + dummy; 3rd unanswered
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                answers=_answer(4, s1, s2, "2")))
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers=_answer(0, s1, s2, "2")))
        db.session.commit()
        rows = item.kind.rows(item, "de", {2: {s1: 2}, 3: "2"})
        first = next(r for r in rows if r["label"].startswith("Testaussage 1 von KUSIV3"))
        assert first["enough"] and first["n"] == 5 and first["counts"] == [1, 1, 1, 1, 1] and first["own"] == 1
        choice = rows[-1]
        assert choice["nominal"] and choice["counts"] == [4, 1] and choice["own"] == 1
        assert item.kind.validate(item) == []
    html = client.get("/surveys/1/editor/items/5").data.decode()
    assert "Frage im Vergleich" in html and "<select name=\"source\"" in html and "Antwortmodus" not in html
    # participant side: the hidden fields carry the group size shown; it is exported
    with app.app_context():
        item = db.session.get(Item, 5)
        assert item.kind.parse_answer(item, MultiDict([("i5_n_2", "5"), ("i5_n_3", "0")])) == {"2": 5, "3": 0}
        assert item.kind.export_values(item, {"2": 5}) == {"live_n_KUSIV": 5, "live_n_fav": None}


def test_sample_compare_rejects_later_items(app, client, survey):
    add(client, "sample_compare")        # 2
    add(client, "single_choice")         # 3, after the widget
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "live"), ("question", "x"), ("source", "3")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("erst danach" in p for p in item.kind.validate(item))


def test_bell_curve_for_matrix(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kusiv3"})   # 2, item 2 reversed
    add(client, "page_break")                                                              # 3
    add(client, "sample_compare")                                                          # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "live"), ("question", "Vergleich"), ("source", "2"), ("show_own", "1"), ("display", "bell")]))
    with app.app_context():
        item = db.session.get(Item, 4)
        s1, s2, s3 = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        for v in (1, 2, 3, 4, 5):   # person means with item 2 recoded: (v + (6 - v) + v) / 3
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {s1: v, s2: 6 - v, s3: v}}))
        db.session.commit()
        bell = item.kind.bell(item, "de", {2: {s1: 5, s2: 1, s3: 5}})
        assert bell["enough"] and bell["n"] == 5 and abs(bell["mean"] - 3) < 1e-9 and bell["own"] == 5
        assert bell["below_share"] == 0.8 and "Sie: 5,0" in bell["svg"] and "Mittelwert 3,0" in bell["svg"]
        assert item.kind.validate(item) == []
        # what the curve is about: the construct's name, not its instruction, which read as a misplaced question
        # above the curve (pre-test 2026-10-20); in English its English name
        assert bell["label"] == "Interpersonales Vertrauen (KUSIV3)"
        assert item.kind.bell(item, "en", {})["label"] == "Interpersonal Trust Short Scale (KUSIV3)"
        source = db.session.get(Item, 2)                                                  # a plain matrix: its question
        source.config = {k: v for k, v in source.config.items() if k != "construct"}
        assert item.kind.bell(item, "de", {})["label"] == source.text("question", "de") and source.text("question", "de")
    html = client.get("/surveys/1/editor/preview?page=3", follow_redirects=True).data.decode()
    assert 'class="p-sc-bell"' in html and '<p class="p-sc-label">' in html
    with app.app_context():
        item = db.session.get(Item, 4)
        add(client, "single_choice")                                                      # 5, not a matrix
        item.config = {**item.config, "sources": [5]}
        assert any("Glockenkurve nur für Matrixfragen" in p for p in item.kind.validate(item))


def test_scatter_map_two_variables(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2 scale
    add(client, "rating")                                                                  # 3 single item
    add(client, "single_choice")                                                           # 4 not numeric
    add(client, "page_break")                                                              # 5
    add(client, "sample_map")                                                              # 6 (own type)
    client.post("/surveys/1/editor/items/6", data=MultiDict([
        ("code", "map"), ("question", "Karte"), ("source", "2"), ("source_y", "3"), ("show_own", "1")]))
    with app.app_context():
        item = db.session.get(Item, 6)
        assert item.config["sources"] == [2, 3] and item.kind.validate(item) == []
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        for v in (1, 2, 3, 4, 5, 5):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {sid: v for sid in sids}, "3": v}))
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                answers={"2": {sids[0]: 3}}))       # only a third of the scale, no rating: left out
        db.session.commit()
        m = item.kind.scatter(item, "de", {2: {sid: 4 for sid in sids}, 3: 2})
        assert m["enough"] and m["n"] == 6 and m["own"] == (4.0, 2.0)
        assert m["svg"].count('class="p-map-dot"') == 5          # (5, 5) twice -> one larger bubble
        assert 'class="p-map-own"' in m["svg"] and m["x"][2].startswith("Allgemeine Selbstwirksamkeit")
        item.config = {**item.config, "sources": [2, 4]}
        assert any("keine Einfachauswahl" in p for p in item.kind.validate(item))
        assert "scatter" not in db.session.get(Item, 6).kind.displays and "scatter" not in item.kind.__class__.__mro__[1].displays
        item.config = {**item.config, "sources": [2]}
        assert any("zwei verschiedene Fragen" in p for p in item.kind.validate(item))


def test_score_band_value_and_percentile(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2, scale 1-5
    add(client, "page_break")                                                              # 3
    add(client, "score_band")                                                              # 4
    form = MultiDict([("code", "band"), ("question", "Ihre Einordnung"), ("source", "2"), ("basis", "value")])
    for bid, upper, label in (("", "2,5", "niedrig"), ("", "3,5", "mittel"), ("", "", "hoch"), ("", "", "")):
        form.add("band_id", bid); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post("/surveys/1/editor/items/4", data=form)
    with app.app_context():
        item = db.session.get(Item, 4)
        assert [b["upper"] for b in item.config["bands"]] == [2.5, 3.5, None] and item.kind.validate(item) == []
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        r = item.kind.result(item, "de", {2: {sid: 4 for sid in sids}})
        assert r["band"] == 2 and r["shown"] == 4.0 and r["bands"][2]["label"] == "hoch"
        assert item.kind.result(item, "de", {2: {sid: 3 for sid in sids}})["band"] == 1
        assert item.kind.result(item, "de", {})["band"] is None                      # no own answer
        # percentile basis: needs 5 others
        item.config = {**item.config, "basis": "percentile",
                       "bands": [{"id": b["id"], "upper": u} for b, u in zip(item.config["bands"], (25, 75, None))]}
        db.session.commit()
        assert not item.kind.result(item, "de", {2: {sid: 4 for sid in sids}})["enough"]
        for v in (1, 2, 3, 4, 5):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {sid: v for sid in sids}}))
        db.session.commit()
        r = item.kind.result(item, "de", {2: {sid: 5 for sid in sids}})
        assert r["enough"] and r["shown"] == 90.0 and r["band"] == 2                  # 4 below + half of 1 equal
        assert item.kind.parse_answer(item, MultiDict([("i4_value", "90.000"), ("i4_band", "3")])) == {"value": 90.0, "band": 3}
        assert item.kind.export_values(item, {"value": 90.0, "band": 3}) == {"band_value": 90.0, "band_band": 3}
        item.config = {**item.config, "bands": item.config["bands"][:1]}
        assert any("2 bis 7" in p for p in item.kind.validate(item))
    page = client.get("/surveys/1/editor/").data.decode()
    assert "Einordnung in Wertebereiche" in page and "2D-Streubild" in page


def test_single_reversed_statement_flips_axis_ends(app, client, survey):
    from app.items import get

    add(client, "likert")                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_reverse", "s1")]))
    with app.app_context():
        src = db.session.get(Item, 2)
        labels = src.kind.labels(src, "de")
        lo, hi, _, left, right = get("sample_map").axis(src, "de")
        assert get("sample_map").value(src, {"s1": 5}) == 1.0 and (left, right) == (labels[-1], labels[0])
        src.config = {**src.config, "statements": [{**src.config["statements"][0], "reverse": False}]}
        assert get("sample_map").axis(src, "de")[3:] == (labels[0], labels[-1])


def test_reversed_rating_counts_recoded_in_einordnung_map_and_rechenwert(app, client, survey):
    """D-8/R-5 (audit 2026-10-03): 5 stars reversed, answer 2. The export column says 4; the Einordnung, the 2D map
    and a Rechenwert "stars × 2" take that 4 as well (before: 2 and 4), and the axis ends swap."""
    from app.items import get

    add(client, "rating")                                                                  # 2
    add(client, "calc")                                                                    # 3
    client.post("/surveys/1/editor/items/2", data={"code": "stars", "question": "R", "style": "stars", "points": "5",
                                                   "left": "schlecht", "right": "gut", "reverse": "1"})
    client.post("/surveys/1/editor/items/3", data={"code": "twice", "question": "x2", "calc_a": "2", "calc_op": "mul",
                                                   "calc_b": "", "calc_fixed": "2", "calc_places": "0"})
    with app.app_context():
        src, calc = db.session.get(Item, 2), db.session.get(Item, 3)
        assert src.config["reverse"] and src.kind.export_values(src, 2) == {"stars": 4}
        assert get("score_band").value(src, 2) == 4.0 and get("sample_map").value(src, 2) == 4.0
        assert calc.kind.value(calc, {2: 2}) == 8
        lo, hi, _, left, right = get("sample_map").axis(src, "de")
        assert (lo, hi) == (1.0, 5.0) and (left, right) == (src.t("de").get("right", ""), src.t("de").get("left", ""))
        src.config = {**src.config, "reverse": False}
        assert get("score_band").value(src, 2) == 2.0 and calc.kind.value(calc, {2: 2}) == 4


def test_live_pool_leaves_out_what_quality_control_marks(app, client, survey):
    """D-7: the live comparison (and percentile, crowd, map, all read balance.answers) pools only participations
    the results page keeps: no speeders, nothing the marking rule marks."""
    from datetime import timedelta

    from app import balance

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2
    add(client, "page_break")                                                              # 3
    add(client, "score_band")                                                              # 4
    t0 = utcnow() - timedelta(hours=1)
    with app.app_context():
        s = db.session.get(Survey, 1)
        sids = [st["id"] for st in db.session.get(Item, 2).config["statements"]]
        for v, seconds, para in ((1, 300, None), (2, 310, None), (3, 320, None), (4, 330, None),
                                 (5, 30, None),                    # a speeder: under half of the median
                                 (5, 340, {"_bot": 1})):           # the hidden field filled in
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=t0,
                                    completed_at=t0 + timedelta(seconds=seconds), paradata=para,
                                    answers={"2": {sid: v for sid in sids}}))
        db.session.commit()
        given = [a for _, a in balance.answers(s, "2")]
        assert [a[sids[0]] for a in given] == [1, 2, 3, 4, 5]                 # the speeder goes, the bot stays
        s.quality_rules = {"flags": ["bot"], "min": 1}                         # now the bot is marked, too
        db.session.commit()
        given = [a for _, a in balance.answers(s, "2")]
        assert [a[sids[0]] for a in given] == [1, 2, 3, 4]


def test_feedback_on_an_instrument_with_subscales_uses_one_subscale(app, client, survey):
    """Mathe-Audit S-4: IE-4 has IKÜ (items 1, 2) and EKÜ (items 3, 4). The Einordnung, the bell curve and the 2D map
    place a person on one subscale, picked in the editor; without a choice the first one, never a mean over both."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:ie4"})       # 2
    add(client, "page_break")                                                              # 3
    add(client, "score_band")                                                              # 4
    editor = client.get("/surveys/1/editor/items/4").data.decode()
    assert 'value="2:IKÜ"' in editor and 'value="2:EKÜ"' in editor and '<option value="2" ' not in editor
    assert "Subskala „EKÜ“" in editor
    with app.app_context():
        item = db.session.get(Item, 4)
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        answer = {2: dict(zip(sids, [5, 5, 1, 1]))}      # high internal, low external control
        item.config = {**item.config, "sources": [2], "basis": "value"}
        db.session.commit()
        assert item.kind.result(item, "de", answer)["shown"] == 5.0                   # first subscale, not 3.0
    form = MultiDict([("code", "band"), ("question", "Ihre Einordnung"), ("source", "2:EKÜ"), ("basis", "value")])
    for bid, upper, label in (("", "2,5", "niedrig"), ("", "", "hoch")):
        form.add("band_id", bid); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post("/surveys/1/editor/items/4", data=form)
    assert 'value="2:EKÜ" selected' in client.get("/surveys/1/editor/items/4").data.decode()
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["sources"] == [2] and item.config["subscales"] == ["EKÜ"] and item.kind.validate(item) == []
        r = item.kind.result(item, "de", answer)
        assert r["shown"] == 1.0 and r["band"] == 0
        # the 2D map: two subscales of one instrument are two variables
        from app.items import get

        smap = get("sample_map")
        fake = Item(type="sample_map", config={"sources": [2, 2], "subscales": ["IKÜ", "EKÜ"]})
        assert smap.effective_subscale(fake, db.session.get(Item, 2), 1) == "EKÜ"
        assert smap.value(db.session.get(Item, 2), answer[2], "IKÜ") == 5.0
