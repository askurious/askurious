"""The remaining findings of sections 1 (descriptive results) and 6 (PSM, computed values) of the mathematical
audit of 2026-10-03 (audit-mathematik-2026-10-03.md): D-10, D-13, D-14, D-17 to D-24, D-26, R-6, R-9, P-1 to P-3."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.conftest import add


def _render(app, item, answers, lang="de"):
    with app.test_request_context():
        template = app.jinja_env.get_template(f"items/{item.type}/participant.html")
        return template.render(item=item, kind=item.kind, lang=lang, answers=answers, variables={}, preview=False)


def _completed(survey_id, answers, n=1):
    for _ in range(n):
        db.session.add(Response(survey_id=survey_id, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                answers=answers))


# --- D-10: the percentile rank says what it is ----------------------------------------------------------------


def test_score_band_percentile_is_called_a_percentile_rank(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2, scale 1-5
    add(client, "page_break")                                                              # 3
    add(client, "score_band")                                                              # 4
    form = MultiDict([("code", "band"), ("question", "Einordnung"), ("source", "2"), ("basis", "percentile")])
    for upper, label in (("50", "unten"), ("", "oben")):
        form.add("band_id", ""); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post("/surveys/1/editor/items/4", data=form)
    with app.app_context():
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        for v in (1, 2, 3, 3, 3, 3, 4, 5):    # the audit's example: own 3 is above 2 of 8, mid-rank 50
            _completed(1, {"2": {sid: v for sid in sids}})
        db.session.commit()
        item = db.session.get(Item, 4)
        assert item.kind.result(item, "de", {2: {sid: 3 for sid in sids}})["shown"] == 50.0
        html = _render(app, item, {2: {sid: 3 for sid in sids}})
        assert "Ihr Prozentrang: 50 von 100 (n = 8)." in html and "liegt über" not in html
        assert "Dein Prozentrang: 50 von 100" in _render(app, item, {2: {sid: 3 for sid in sids}}, "de-du")


# --- D-13: a reversed slider rating keeps the decimals of its scale -------------------------------------------


def test_reversed_slider_rating_has_no_binary_artefacts():
    from app.items import get

    rating = get("rating")
    item = SimpleNamespace(code="r", config={"style": "slider", "min": 0.2, "max": 0.7, "step": 0.1, "reverse": True})
    assert [rating.export_values(item, v)["r"] for v in (0.3, 0.4, 0.2, 0.7)] == [0.6, 0.5, 0.7, 0.2]
    stars = SimpleNamespace(code="s", config={"style": "stars", "points": 5, "reverse": True})
    assert rating.export_values(stars, 2) == {"s": 4}


# --- D-14 and D-26: live comparison of a slider and of a single choice ----------------------------------------


def test_live_comparison_bins_slider_values_on_class_edges_upwards():
    from app.items import get

    kind = get("sample_compare")
    src = SimpleNamespace(id=7, type="rating", kind=get("rating"), config={"style": "slider", "min": 0, "max": 1, "step": 0.1},
                          text=lambda key, lang=None: "Regler")
    item = SimpleNamespace(config={})
    kind.sources = lambda item: [src]
    kind.pool = lambda item, preview=False, row=None: [{"7": v} for v in (0.3, 0.6, 0.7, 0.0, 1.0)]
    try:
        row, = kind.rows(item, "de", {7: 0.3})
    finally:
        del kind.sources, kind.pool
    assert row["counts"] == [1, 0, 0, 1, 0, 0, 1, 1, 0, 1]      # 0.3 in "0,3", 0.6 in "0,6", 0.7 in "0,7"
    assert row["own"] == 3 and row["labels"][3] == "0,3" and row["median_label"] == "0,60"


def test_live_comparison_of_a_single_choice_counts_dont_know_apart_and_sorts_nominal(app, client, survey):
    add(client, "single_choice")                                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe"), ("answer_mode", "optional"),
        ("option_id", ""), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", ""), ("option_code", "2"), ("option_label", "blau"),
        ("option_id", ""), ("option_code", "9"), ("option_label", "weiß nicht")]))
    add(client, "page_break")                                                              # 3
    add(client, "sample_compare")                                                          # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "live"), ("question", "Vergleich"), ("source", "2"), ("show_own", "1")]))
    with app.app_context():
        src = db.session.get(Item, 2)
        src.config = {**src.config, "options": [{**o, "missing": True} if o["code"] == "9" else o
                                                for o in src.config["options"]]}
        for code, n in (("1", 2), ("2", 4), ("9", 3)):
            _completed(1, {"2": code}, n)
        db.session.commit()
        item = db.session.get(Item, 4)
        row, = item.kind.rows(item, "de", {2: "1"})
        assert row["labels"] == ["blau", "rot"] and row["counts"] == [4, 2] and row["n"] == 6   # "weiß nicht" apart
        assert row["own"] == 1 and row["missing"] == [{"label": "weiß nicht", "count": 3}]
        assert abs(row["shares"][0] - 4 / 6) < 1e-12
        html = _render(app, item, {2: "1"})
        assert "n = 6 · weiß nicht: 3" in html
        src.config = {**src.config, "level": "ordinal"}                                     # ordinal: the question's order
        assert item.kind.rows(item, "de", {})[0]["labels"] == ["rot", "blau"]


# --- D-22: the bell curve says it is a normal curve, with the SD of the sample --------------------------------


def test_bell_curve_uses_the_sample_sd_and_says_it_is_a_normal_curve(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2
    add(client, "page_break")                                                              # 3
    add(client, "sample_compare")                                                          # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "live"), ("question", "Vergleich"), ("source", "2"), ("show_own", "1"), ("display", "bell")]))
    with app.app_context():
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
        for v in (1, 2, 3, 4, 5):
            _completed(1, {"2": {sid: v for sid in sids}})
        db.session.commit()
        item = db.session.get(Item, 4)
        bell = item.kind.bell(item, "de", {})
        assert abs(bell["sd"] - 1.5811388) < 1e-6                     # n - 1 (pstdev would give 1.414)
        html = _render(app, item, {})
        assert "Normalverteilung mit dem Mittelwert (3,00) und der Standardabweichung (1,58)" in html


# --- D-17: one stored nan does not turn a column of numbers into text -----------------------------------------


def test_invalid_old_number_counts_as_missing_not_as_text(app, client, survey):
    from app import results

    add(client, "text_input")                                                              # 2
    client.post("/surveys/1/editor/items/2", data={"code": "betrag", "question": "Betrag", "validation": "decimal",
                                                   "answer_mode": "optional"})
    with app.app_context():
        for v in (2.5, "nan", 4.0, "1e300"):
            _completed(1, {"2": v})
        db.session.commit()
        e = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
        col = e["columns"][0]
        assert col["type"] == "num" and col["n"] == 2 and col["mean"] == 3.25 and col["invalid"] == 2
    assert "ungültige Werte (nicht mitgezählt): 2" in client.get("/surveys/1/results").data.decode()


# --- D-18: the bars of the timeline stand on its time axis ----------------------------------------------------


def test_timeline_bars_stand_where_their_days_are(app, client, survey):
    import re

    from app import timeline

    with app.app_context():
        s = db.session.get(Survey, 1)
        start = datetime(2026, 1, 1, 18, 0)                       # created in the evening, 130 days of field
        s.created_at = start
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                                started_at=start + timedelta(days=126, hours=2), completed_at=start + timedelta(days=126, hours=2)))
        db.session.commit()
        out = timeline.build(s, now=start + timedelta(days=130))
        bx, bw = (float(v) for v in re.search(r'class="tl-bar" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', str(out["svg"])).groups())
        span = timedelta(days=130).total_seconds()

        def x(dt):
            return timeline.LEFT + (timeline.W - timeline.LEFT - timeline.RIGHT) * min(1, (dt - start).total_seconds() / span)

        # weekly bars from the local 1 January: the bar with day 126 is the week from 7 May (local midnight) on;
        # it starts where that day starts on the time axis (before: at 18/19 of the width, some 20 px too far left)
        week = x(timeline._day_start_utc(datetime(2026, 5, 7).date()))
        assert abs(bx - bw * 0.12 / 0.76 - week) < 0.5


# --- D-19: words of associations per person -------------------------------------------------------------------


def test_association_terms_count_persons_once():
    from app.items import get

    assoc = get("association")
    item = SimpleNamespace(code="a", config={"slots": 3})
    table = [{"a_1": "Angst", "a_2": "angst", "a_3": "Mut"}, {"a_1": "Angst"}]
    terms = assoc.terms(item, table)
    assert terms[0] == ("angst", 3, None, 2) and terms[1] == ("mut", 1, None, 1)


def test_viewers_see_words_named_by_five_persons_not_five_times():
    from app import results

    data = {"items": [{"columns": [], "terms": [("oft", 6, None, 1), ("breit", 5, None, 5)]}]}
    assert results.for_viewers(data)["items"][0]["terms"] == [("breit", 5, None, 5)]


# --- D-20: map markers: the mean over persons, weights for the heatmap, a random sample ----------------------


def test_map_follow_up_mean_over_persons_and_heat_weights(app, client, survey, monkeypatch):
    import json

    from app.items import map_marker

    add(client, "map_marker")                                                              # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "questions": [{"id": "q1", "code": "gut", "kind": "scale", "points": 5}]}
        db.session.commit()
        item = db.session.get(Item, 2)
        code = item.kind.questions(item)[0]["export_code"]
        one = [{"lat": 50.0 + k / 100, "lon": 6.0, code: 5} for k in range(4)]
        other = [{"lat": 51.0, "lon": 7.0, code: 1}]
        table = [{"resp_id": 1, f"{item.code}_json": json.dumps(one)}, {"resp_id": 2, f"{item.code}_json": json.dumps(other)}]
        out = item.kind.results(item, table)
        s, = out["summary"]
        assert abs(s["mean"] - 4.2) < 1e-9 and s["person_mean"] == 3.0 and s["persons"] == 2
        assert [p["w"] for p in out["points"]] == [0.25] * 4 + [1.0]
        monkeypatch.setattr(map_marker, "MAX_RESULT_POINTS", 2)
        cut = item.kind.results(item, table)
        assert cut["truncated"] and len(cut["points"]) == 2 and cut["points"] == item.kind.results(item, table)["points"]


# --- D-21: rest of the old D-15 ------------------------------------------------------------------------------


def test_position_map_has_no_sd_from_a_single_placement():
    from app.items import get

    pos = get("position2d")
    item = SimpleNamespace(code="p", config={"options": [{"id": "a", "code": "ki"}]}, t=lambda lang: {"options": {"a": "KI"}})
    (_, mx, my, sx, sy, n), = pos.map_points(item, [{"p_ki_x": 10.0, "p_ki_y": 20.0}], "de")
    assert (mx, my, n) == (10.0, 20.0, 1) and sx is None and sy is None


def test_postal_regions_are_counted_out_of_all_that_exist():
    from app.items import postal_code

    total = postal_code.region_total()
    assert total >= 95 and total == len({p[:2] for p in postal_code.table()} | {p[:2] for p in postal_code.area_codes()})


def test_method_text_does_not_count_people_filling_in_as_dropouts(app, client, survey):
    from app import methods

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.opened_at = Survey.RUNNING, utcnow() - timedelta(days=1)
        _completed(1, {}, 2)
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}))
        db.session.commit()
        f = methods.facts(s)
        assert f["incomplete"] == 0 and f["in_progress"] == 1
        text = methods.draft(f)
        assert "beendeten die Befragung nicht" not in text and "1 füllten die Befragung zum Zeitpunkt des Berichts noch aus" in text
        s.status = Survey.CLOSED                                   # closed: who did not finish, did not finish
        s.closed_at = utcnow()
        f = methods.facts(s)
        assert f["incomplete"] == 1 and not f["in_progress"]


# --- D-23: units and the Gini coefficient ---------------------------------------------------------------------


def test_slider_and_calc_carry_their_unit_and_the_gini_follows_the_tick(app, client, survey):
    from app import results

    add(client, "slider")                                                                  # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "geld"), ("question", "Betrag"), ("min", "0"), ("max", "1000"), ("step", "10"), ("format", "currency"),
        ("currency", "EUR"), ("stmt_id", ""), ("stmt_code", ""), ("stmt_text", "Betrag"), ("ratio_shown", "1"), ("ratio", "1")]))
    add(client, "text_input")                                                              # 3
    client.post("/surveys/1/editor/items/3", data={"code": "jahr", "question": "Geburtsjahr", "validation": "integer",
                                                   "answer_mode": "optional", "ratio_shown": "1"})   # tick taken off
    with app.app_context():
        slider, year = db.session.get(Item, 2), db.session.get(Item, 3)
        assert slider.kind.is_ratio(slider) and not year.kind.is_ratio(year)
        assert slider.kind.result_column(slider, "geld")[1] == "€"
        assert year.kind.is_ratio(SimpleNamespace(config={"validation": "integer"}))       # old items: amounts
        row = slider.config["rows"][0]
        row_code = slider.kind.row_code(slider, 0, row)
        for money, born in ((100, 1980), (300, 1990), (800, 2000)):
            _completed(1, {"2": {row["id"]: money}, "3": born})
        db.session.commit()
        cols = {c["code"]: c for e in results.compute(db.session.get(Survey, 1))["items"] for c in e["columns"]}
        assert cols[row_code]["unit"] == "€" and cols[row_code]["gini"] is not None
        assert cols["jahr"]["gini"] is None
    html = client.get("/surveys/1/editor/items/3").data.decode()
    assert 'name="ratio"' in html and "Beträge oder Anzahlen" in html


def test_calc_shows_its_unit_on_the_results_page():
    from app.items import get

    calc = get("calc")
    assert calc.result_column(SimpleNamespace(config={"unit": "€"}), "x")[1] == "€"


# --- R-6: rankings with carried options ----------------------------------------------------------------------


def test_ranking_with_carried_options_reports_normalised_ranks():
    from app.items import get

    ranking = get("ranking")
    opts = [{"id": "a", "code": "a"}, {"id": "b", "code": "b", "src": {"which": "chosen"}}, {"id": "c", "code": "c"}]
    item = SimpleNamespace(code="r", config={"options": opts}, t=lambda lang: {"options": {"a": "A", "b": "B", "c": "C"}},
                           text=lambda key, lang=None: {"a": "A", "b": "B", "c": "C"})
    table = [{"r_a": 2, "r_b": -77, "r_c": 1},     # two options: a last
             {"r_a": 2, "r_b": 3, "r_c": 1},       # three: a in the middle
             {"r_a": 1, "r_b": 2, "r_c": 3}]
    found = ranking.normalized(item, table)
    assert found["a"] == ([0.0, 0.5, 1.0], 1) and found["b"] == ([0.0, 0.5], 0)
    assert found["c"] == ([1.0, 1.0, 0.0], 2)


def test_ranking_results_page_shows_normalised_ranks_only_when_options_were_carried(app, client, survey):
    from app import results

    add(client, "ranking")                                                                 # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "rang"), ("question", "Reihenfolge"), ("answer_mode", "optional"),
        ("option_id", ""), ("option_code", "a"), ("option_label", "A"),
        ("option_id", ""), ("option_code", "b"), ("option_label", "B"),
        ("option_id", ""), ("option_code", "c"), ("option_label", "C")]))
    with app.app_context():
        for order in (["c", "a", "b"], ["a", "b", "c"]):
            _completed(1, {"2": order})
        db.session.commit()
        e = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
        assert e["ranks_norm"] is None                                                      # everyone saw everything
        item = db.session.get(Item, 2)
        item.config = {**item.config, "options": [{**o, "src": {"which": "chosen"}} if o["code"] == "b" else o
                                                  for o in item.config["options"]]}
        db.session.commit()
        item = db.session.get(Item, 2)
        rows = item.kind.results(item, [{"rang_a": 1, "rang_b": -77, "rang_c": 2}, {"rang_a": 2, "rang_b": 3, "rang_c": 1}], "de")
        assert [r["code"] for r in rows] == ["a", "c", "b"] and rows[0]["mean"] == 0.75 and rows[0]["top"] == 0.5


# --- R-9: the median of dates ---------------------------------------------------------------------------------


def test_median_of_dates_lies_halfway_between_the_middle_two():
    from app.items.date_time import median

    assert median(["2000-01-01", "2000-12-31"]) == "2000-07-02"     # 182.5 days on, rounded half up (2000 is a leap year)
    assert median(["2000-01-01", "2000-06-01", "2000-12-31"]) == "2000-06-01"
    assert median(["07:00", "08:01"]) == "07:31" and median(["1990", "1995"]) == "1993"
    assert median(["2000-01", "2000-04"]) == "2000-03" and median(["2000-01-01T10:00", "2000-01-02T10:00"]) == "2000-01-01T22:00"


# --- P-1 to P-3: van Westendorp with Newton-Miller-Smith -----------------------------------------------------


def test_psm_x_axis_labels_carry_the_decimals_the_ticks_need():
    from app import psm_analysis

    prices = [round(0.10 + i * 0.005, 3) for i in range(31)]
    flat = [0.5] * len(prices)
    chart = psm_analysis.chart(prices, [("a", flat)], (0.10, 0.25))
    values = psm_analysis.nice_ticks(0.10, 0.25)
    labels = [label for _, label in chart["x_ticks"]]
    assert len(set(labels)) == len(labels)                              # no label twice
    assert all(abs(float(label.replace(",", ".")) - v) < 1e-9 for label, v in zip(labels, values))
    small = [label for _, label in psm_analysis.chart([0.001, 0.009], [("a", [0.5, 0.5])], (0.001, 0.009))["x_ticks"]]
    assert len(set(small)) == len(small) and "0,00" not in small


def test_nms_without_any_purchase_reports_no_optimum():
    from app import psm_analysis

    good = [{"nms_c": 1, "nms_e": 1, "tc": 1, "c": 2, "e": 3, "te": 4}] * 3
    out = psm_analysis.nms(good, [1.0, 2.0, 3.0, 4.0], [0, 10, 30, 50, 70])
    assert out["nobody"] and out["max_trial"] is None and out["max_revenue"] is None


def test_nms_plateau_of_the_purchase_share_is_reported_as_a_span():
    from app import psm_analysis

    good = [{"nms_c": 4, "nms_e": 4, "tc": 5, "c": 10, "e": 20, "te": 30}] * 3
    out = psm_analysis.nms(good, [5, 10, 15, 20, 25, 30], [0, 10, 30, 50, 70])
    assert out["max_trial"]["price"] == 10 and out["max_trial"]["price_to"] == 20
    assert abs(out["max_trial"]["value"] - 0.5) < 1e-9
