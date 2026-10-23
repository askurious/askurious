import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.items.psm import parse_price
from app.models import Item, Survey
from tests.conftest import add


def setup(client, required=True, **extra):
    add(client, "psm")                                              # 2
    data = {"code": "wtp", "question": "Stellen Sie sich vor …", "product": "Ein **E-Bike** mit 80 km Reichweite.",
            "answer_mode": "required" if required else "optional", "currency": "EUR", "decimals": "2",
            "order": "asc", "consistency": "required", "min": "", "max": "",
            "q_tc": "Zu billig?", "q_c": "Günstig?", "q_e": "Teuer?", "q_te": "Zu teuer?"}
    data.update(extra)
    client.post("/surveys/1/editor/items/2", data=data)


def prices(tc="", c="", e="", te=""):
    return MultiDict({"i2_tc": tc, "i2_c": c, "i2_e": e, "i2_te": te})


@pytest.mark.parametrize("raw, value", [
    ("12", 12), ("12,5", 12.5), ("12.50", 12.5), ("1.299,00", 1299), ("1,299.00", 1299), ("1.000", 1000),
    ("1 299", 1299), ("12 €", 12), ("CHF 7.90", 7.9), (",5", 0.5), ("1.000.000", 1000000),
])
def test_parse_price(raw, value):
    assert parse_price(raw) == value


def test_parse_price_rejects():
    for raw in ("abc", "12,5,3x", "1-2", ""):
        with pytest.raises(ValueError):
            parse_price(raw)
    assert parse_price("1.299", decimals=3) == 1.299                # three decimals allowed: decimal point


@pytest.mark.parametrize("raw, value", [
    ("0,999", 0.999), ("0.999", 0.999), ("-0,999", -0.999), ("00,999", 0.999),      # never 999
    ("1.999", 1999), ("1,999", 1999), ("12.345", 12345), ("0,99", 0.99), ("0", 0),  # real thousands as before
])
def test_parse_price_leading_zero_is_no_thousands_group(raw, value):
    assert parse_price(raw) == value


def test_defaults_and_config(app, client, survey):
    add(client, "psm")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["order"] == "asc" and item.config["consistency"] == "required"
        assert "billig" in item.text("q_tc", "de") and "quality couldn't be very good" in item.text("q_tc", "en")
        assert "Produktbeschreibung fehlt" in item.kind.validate(item)
    setup(client, min="1", max="5000", decimals="0", order="desc", currency="unit", unit="Punkte")
    with app.app_context():
        item = db.session.get(Item, 2)
        c = item.config
        assert (c["min"], c["max"], c["decimals"], c["order"], c["currency"]) == (1, 5000, 0, "desc", "")
        assert item.kind.validate(item) == [] and item.kind.sequence(item) == ("te", "e", "c", "tc")
        assert item.kind.unit_label(item, "de") == "Punkte"


def test_valid_answer_and_normalisation(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, prices("5", "12,50", "1.299", "2.000,00")) == \
            {"tc": 5, "c": 12.5, "e": 1299, "te": 2000}


@pytest.mark.parametrize("form, match", [
    (prices("-1", "2", "3", "4"), "negativ"),
    (prices("x", "2", "3", "4"), "als Zahl"),
    (prices("1,2345", "2", "3", "4"), "Nachkommastellen"),
    (prices("1", "2", "3", ""), "alle vier"),
    (prices("5", "4", "6", "7"), "„günstig“ .* niedriger als „zu billig“"),
    (prices("5", "6", "4", "7"), "„teuer“"),
])
def test_rejects(app, client, survey, form, match):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match=match):
            item.kind.parse_answer(item, form)


def test_min_max_decimals(app, client, survey):
    setup(client, min="10", max="100", decimals="0")
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match="mindestens 10"):
            item.kind.parse_answer(item, prices("5", "20", "30", "40"))
        with pytest.raises(AnswerError, match="höchstens 100"):
            item.kind.parse_answer(item, prices("15", "20", "30", "400"))
        with pytest.raises(AnswerError, match="ohne Nachkommastellen"):
            item.kind.parse_answer(item, prices("15,5", "20", "30", "40"))
        assert item.kind.parse_answer(item, prices("15", "20", "30", "40"), lang="en") == {"tc": 15, "c": 20, "e": 30, "te": 40}
        with pytest.raises(AnswerError, match="at least"):
            item.kind.parse_answer(item, prices("5", "20", "30", "40"), lang="en")


def test_optional_partial(app, client, survey):
    setup(client, required=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, prices()) is None
        answer = item.kind.parse_answer(item, prices("", "10", "", "20"))
        assert answer == {"tc": None, "c": 10, "e": None, "te": 20} and item.kind.is_missing(item, answer)
        with pytest.raises(AnswerError):                            # order also among the given prices
            item.kind.parse_answer(item, prices("", "30", "", "20"))
        with pytest.raises(AnswerError, match="Nachkommastellen"):  # 0.999, not 999
            item.kind.parse_answer(item, prices("", "0,999", "", ""))


def test_nms_answer_needs_its_price(app, client, survey):
    setup(client, required=False, nms="1")
    with app.app_context():
        item = db.session.get(Item, 2)
        form = prices("", "10", "", "")
        form.update({"i2_nms_c": "4", "i2_nms_e": "2"})
        # the likelihood at "expensive" has no price it refers to: not stored; the one at "cheap" is kept
        answer = item.kind.parse_answer(item, form)
        assert answer == {"tc": None, "c": 10, "e": None, "te": None, "nms_c": 4, "nms_e": None}
        assert item.kind.is_missing(item, answer)
        assert item.kind.export_values(item, answer)["wtp_nms_c"] == 4
        form = prices("", "", "20", "")
        form.update({"i2_nms_c": "4", "i2_nms_e": "2"})
        assert item.kind.parse_answer(item, form)["nms_e"] == 2 and item.kind.parse_answer(item, form)["nms_c"] is None
        form = prices()
        form.update({"i2_nms_c": "4", "i2_nms_e": "2"})
        assert item.kind.parse_answer(item, form) is None            # no price at all: nothing to keep


def test_consistency_warn_and_off(app, client, survey):
    from app import runtime

    setup(client, consistency="warn")
    with app.app_context():
        item = db.session.get(Item, 2)
        form = prices("5", "4", "6", "7")
        result = runtime.evaluate([item], form, "de")
        assert not result.errors and "günstig" in result.nudges[item.id] and "übernehmen" in result.nudges[item.id]
        result = runtime.evaluate([item], form, "de", nudge_acknowledged=True)
        assert result.ok and result.answers[item.id]["c"] == 4
        assert runtime.evaluate([item], prices("1", "2", "3", "4"), "de").ok
    setup(client, consistency="off")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert runtime.evaluate([item], prices("5", "4", "6", "7"), "de").ok


def test_export_and_codebook(app, client, survey):
    from app import export

    setup(client, max="500")
    with app.app_context():
        item = db.session.get(Item, 2)
        cols = item.kind.export_columns(item)
        assert [c for c, _ in cols] == ["wtp_tc", "wtp_c", "wtp_e", "wtp_te"]
        assert all("Van Westendorp" in label and "[€]" in label for _, label in cols)
        assert item.kind.export_values(item, {"tc": 5, "c": 12.5, "e": 20, "te": 30}) == \
            {"wtp_tc": 5, "wtp_c": 12.5, "wtp_e": 20, "wtp_te": 30}
        assert item.kind.export_values(item, None) == {"wtp_tc": None, "wtp_c": None, "wtp_e": None, "wtp_te": None}
        survey_ = db.session.get(Survey, 1)
        book = {row[0]: row for row in export.codebook(survey_)}
        assert book["wtp_te"][2] == "Zahl" and "0 bis 500 €" in book["wtp_te"][3] and "erzwungen" in book["wtp_te"][4]
        md = export.questionnaire_md(survey_)
        assert "`wtp_tc` Zu billig?" in md and "E-Bike" in md


def test_preview_roundtrip_and_spss(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-psm="2"' in html and 'name="i2_te"' in html and "<strong>E-Bike</strong>" in html
    assert html.index('name="i2_tc"') < html.index('name="i2_te"')
    with app.app_context():
        from app import dummy, export

        s = db.session.get(Survey, 1)
        dummy.generate(s, 5, seed=3)
        db.session.commit()
        from app.models import Response

        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(a["tc"] < a["c"] < a["e"] < a["te"] for a in answers)
        cols, data, meta = export._spss_dictionary(s, False, ("dummy",), 30, True, None)
        assert meta["wtp_c"]["kind"] in ("int", "num") and meta["wtp_c"]["level"] == "SCALE"
        assert "wtp_tc" in export.to_csv(s, sources=("dummy",)).splitlines()[0]


def test_participation_roundtrip(app, client, survey):
    import csv
    import io

    from app.models import Response
    from tests.conftest import CONSENT_OK, fill_consent

    fill_consent(client)
    setup(client, order="desc")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert html.index('name="i2_te"') < html.index('name="i2_tc"')  # asked from too expensive down
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2_tc": "5", "i2_c": "4,50", "i2_e": "20", "i2_te": "30"}).data.decode()
    assert "niedriger als" in html and 'value="4,50"' in html        # blocked, typed input kept
    p.post(f"/s/{uid}", data={"nav": "next", "i2_tc": "5", "i2_c": "9,90", "i2_e": "20", "i2_te": "1.200"})
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"] == {"tc": 5, "c": 9.9, "e": 20, "te": 1200}
    row = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert [row[f"wtp_{k}"] for k in ("tc", "c", "e", "te")] == ["5", "9.9", "20", "1200"]


def test_not_in_construct_toolbox_but_cited(app, client, survey):
    from app import constructs, export

    with app.app_context():
        assert constructs.get("psm") is None and constructs.search("westendorp") == []
    setup(client, nms="1")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "construct" not in item.config and item.kind.wording_changes(item) == ["DE: TC, C, E, TE"]
        s = db.session.get(Survey, 1)
        book = {row[0]: row for row in export.codebook(s)}
        assert "Van Westendorp, 1976" in book["wtp_tc"][4] and "Wortlaut vom Standard abweichend (DE: TC, C, E, TE)" in book["wtp_tc"][4]
        assert "Newton et al., 1993" in book["wtp_nms_c"][4]  # three authors: et al. (math audit 2026-10-06, N-3)
        readme = export.readme_md(s)
        assert "## Methods" in readme and "Van Westendorp, P. (1976)" in readme and "Newton, D., Miller, J." in readme
        assert "## Instruments" not in readme
    add(client, "psm")                                              # 3: standard wording untouched
    with app.app_context():
        item = db.session.get(Item, 3)
        assert item.kind.wording_changes(item) == []
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert "ESOMAR" in page and "Quelle: Van Westendorp" not in page           # no citation in the running text
    assert page.count('<details class="sources">') == 1 and "Literatur zu diesem Element" in page and "Alletsee" not in page


def test_retired_construct_is_unlinked(app, client, survey):
    """Databases from the time PSM was a toolbox construct: the entry goes, its items stay as elements."""
    from app import constructs
    from app.models import Construct

    add(client, "psm")                                              # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "construct": {"key": "psm", "version": 1, "unlocked": False, "template": {}}}
        db.session.add(Construct(key="psm", name="PSM", seed_version=1, spec={"type": "psm", "code": "PSM", "config": {}, "texts": {}}))
        db.session.commit()
        assert constructs.locked(db.session.get(Item, 2))
        constructs.ensure_seed()
        item = db.session.get(Item, 2)
        assert "construct" not in item.config and item.config["currency"] == "EUR" and not constructs.locked(item)
        assert db.session.scalar(db.select(Construct).filter_by(key="psm")) is None
    assert client.get("/surveys/1/editor/items/2").status_code == 200
    assert client.get("/constructs/?q=westendorp").status_code == 200


# ---- Newton-Miller-Smith and curve analysis ----

def test_intersection_and_nms_probability():
    from app import psm_analysis as pa

    assert pa.intersection([0, 10], [1, 0], [0, 1]) == 5
    assert pa.intersection([0, 10, 20], [1, 0.8, 0.2], [0, 0.2, 0.6]) == pytest.approx(10 + 10 * 0.6 / 1.0)
    assert pa.intersection([0, 10], [1, 0.9], [0, 0.1]) is None
    case = {"tc": 0, "c": 10, "e": 20, "te": 30, "nms_c": 5, "nms_e": 3}
    w = [0, 10, 30, 50, 70]
    assert [pa.probability(case, p, w) for p in (0, 5, 10, 15, 20, 25, 30, 40)] == \
        pytest.approx([0, 0.35, 0.7, 0.5, 0.3, 0.15, 0, 0])


def test_analysis_points():
    from types import SimpleNamespace

    from app import psm_analysis as pa

    rows = [{"p_tc": a, "p_c": b, "p_e": c, "p_te": d, "p_nms_c": 5, "p_nms_e": 2}
            for a, b, c, d in ((5, 10, 20, 30), (10, 15, 25, 40), (5, 20, 30, 50), (15, 20, 40, 60), (8, 12, 30, 45))]
    rows.append({"p_tc": 20, "p_c": 10, "p_e": 30, "p_te": 40})    # inconsistent
    rows.append({"p_tc": 5})                                         # incomplete
    kind = SimpleNamespace(nms=lambda item: True, nms_weights=lambda item: [0, 10, 30, 50, 70])
    item = SimpleNamespace(code="p", kind=kind)
    a = pa.analyse(rows, item)
    assert (a["n"], a["n_inconsistent"], a["n_cases"] - a["n_complete"]) == (5, 1, 1)
    pts = a["points"]
    assert all(pts[k] is not None for k in ("PMC", "OPP", "IPP", "PME"))
    assert pts["PMC"] <= pts["OPP"] <= pts["PME"] and pts["PMC"] < pts["IPP"] < pts["PME"]
    cv = a["curves"]
    assert cv["too_cheap"] == sorted(cv["too_cheap"], reverse=True) and cv["too_expensive"] == sorted(cv["too_expensive"])
    assert cv["too_cheap"][0] == 0.8 and cv["too_expensive"][-1] == 0.9     # the extreme answers themselves count half
    assert a["nms"]["n"] == 5 and 10 <= a["nms"]["max_trial"]["price"] <= 20
    assert a["nms"]["max_revenue"]["price"] >= a["nms"]["max_trial"]["price"]


def test_nms_questions(app, client, survey):
    setup(client, nms="1", nms_c="Kauf bei {price}?", nms_e="Und bei {price}?",
          nms_label=["sicher", "eher ja", "vielleicht", "eher nicht", "nie"], nms_weight=["70", "50", "30", "10", "0"])
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.nms(item) and item.kind.validate(item) == []
        assert item.kind.nms_labels(item, "de")[0] == "nie" and item.kind.nms_weights(item) == [0, 10, 30, 50, 70]
        form = prices("5", "10", "20", "30")
        with pytest.raises(AnswerError, match="wahrscheinlich"):
            item.kind.parse_answer(item, form)
        form.update({"i2_nms_c": "5", "i2_nms_e": "2"})
        answer = item.kind.parse_answer(item, form)
        assert answer == {"tc": 5, "c": 10, "e": 20, "te": 30, "nms_c": 5, "nms_e": 2}
        assert [c for c, _ in item.kind.export_columns(item)][-2:] == ["wtp_nms_c", "wtp_nms_e"]
        assert item.kind.export_values(item, answer)["wtp_nms_e"] == 2
        assert "20 €" in str(item.kind.nms_question(item, "e", "de", answer))
        from app import export

        book = {row[0]: row for row in export.codebook(db.session.get(Survey, 1))}
        assert book["wtp_nms_c"][2] == "Kaufwahrscheinlichkeit" and "5 = sicher" in book["wtp_nms_c"][3]
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'name="i2_nms_c"' in html and 'data-psm-price="c"' in html and "Ihrem Preis „günstig“" in html


def test_results_page_curves(app, client, survey):
    setup(client, nms="1")
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 30, seed=5)
        db.session.commit()
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "data-psm-chart" in html and "Akzeptable Preisspanne" in html and "OPP" in html
    assert "Newton-Miller-Smith" in html and "höchster Umsatz" in html and "Kurven als Tabelle" in html
    assert html.count("<polyline") == 6                              # four curves, trial, revenue
    # one closed fold at the end of the page with the sources of what it shows (D-25, P-4)
    assert html.count('<details class="sources">') == 1 and "Literatur zu dieser Seite" in html
    assert "NSS-Price Sensitivity Meter (PSM)" in html and "Newton, D., Miller, J., &amp; Smith, P. (1993)" in html
    assert "pricesensitivitymeter" not in html     # the weights 0/10/30/50/70 % are an Askurious default
    assert "McFadden" not in html and "Rossi, P. E." not in html     # no choice model here


def test_ties_count_half_on_every_curve():
    from app import psm_analysis as pa

    cv = pa.curves([{"tc": 5, "c": 10, "e": 20, "te": 30}] * 2)
    assert cv["prices"] == [5, 10, 20, 30]
    assert cv["too_cheap"] == [0.5, 0, 0, 0] and cv["too_expensive"] == [0, 0, 0, 0.5]
    assert cv["not_cheap"] == [0, 0.5, 1, 1] and cv["not_expensive"] == [1, 1, 0.5, 0]   # was 1 at 10, 1 at 20


def test_level_curves_meet_in_the_middle():
    from app import psm_analysis as pa

    assert pa.intersection([0, 10, 20, 30], [1, 0.5, 0.5, 0], [0, 0.5, 0.5, 1]) == 15     # was 10
    assert pa.intersection([0, 10, 20], [1, 0.5, 0], [0, 0.5, 1]) == 10


def test_revenue_maximum_between_prices():
    from app import psm_analysis as pa

    good = [{"tc": 1, "c": 2, "e": 10, "te": 40, "nms_c": 5, "nms_e": 4}] * 2
    n = pa.nms(good, [1, 2, 10, 40], [0, 10, 30, 50, 70])
    assert n["max_revenue"]["price"] == pytest.approx(20) and n["max_revenue"]["value"] == pytest.approx(20 / 3)  # was 10


def test_axis_labels_and_rounding():
    from app import psm_analysis as pa
    from app.results import _round_half_up

    assert all(str(t) != "-0.0" for t in pa.nice_ticks(0, 1.4, 4))
    ch = pa.chart([1, 2], [("revenue", [0.2, 1.4])], (1, 2), y_max=1.4 * 1.1)
    assert [lab for _, _, lab in ch["y_ticks"]] == ["0,0", "0,5", "1,0", "1,5"]         # was -0 | 0 | 1 | 2
    assert _round_half_up(12.5, 0) == 13 and _round_half_up(13.5, 0) == 14 and _round_half_up(1.005, 2) == 1.01


def test_results_page_price_points_with_bootstrap_intervals(app, client, survey):
    """Concept statistik-erweiterungen, stage 1: the price points with a 95 % percentile bootstrap interval over
    persons (1000 draws, fixed seed) from 30 consistent answers on; below that "–" with a note."""
    import re

    setup(client)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        dummy.generate(s, 60, seed=5)
        db.session.commit()
        a = next(e for e in results.compute(s, sources=("dummy",))["items"] if e.get("psm"))["psm"]
        assert a["n"] >= 30 and set(a["ci"]) == {"PMC", "OPP", "IPP", "PME"}
        for key, (lo, hi) in ((k, v) for k, v in a["ci"].items() if v):
            assert lo <= a["points"][key] <= hi
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "95-%-KI</th>" in html and re.search(r"<td>\[\d+,\d\d €; \d+,\d\d €\]</td>", html)
    assert "Perzentil-Bootstrap über Personen (1000 Ziehungen" in html
    assert "Efron, B., &amp; Tibshirani, R. J. (1993)" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        for response in list(s.responses)[10:]:
            db.session.delete(response)
        db.session.commit()
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "95-%-Konfidenzintervalle erst ab 30 Personen" in html and "Efron, B." not in html
