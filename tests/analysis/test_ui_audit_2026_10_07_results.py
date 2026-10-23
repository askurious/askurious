"""UI/UX audit 2026-10-07, packages E (results page, W58 to W75) and H (colour system, W120, W121; the palette itself
in tests/items/test_display.py)."""
from datetime import datetime, timezone

from werkzeug.datastructures import MultiDict

from app import choice_model, hb_convergence, results, timeutil
from app.editor import column_head
from app.extensions import db
from app.models import Survey
from tests.conftest import add
from tests.analysis.test_compare import ROWS, setup


def test_w58_w59_part_worth_bars_in_the_order_of_the_levels_and_a_narrow_variant():
    model = {"q": 1.96, "attributes": [{"label": "Preis", "levels": [
        {"label": "50 €", "beta": 0.8, "se": 0.2}, {"label": "100 €", "beta": 2.0, "se": 0.3},
        {"label": "150 €", "beta": -2.8, "se": 0.2}]}]}
    chart = choice_model.charts(model)[0]
    # the order of the table (W58), not sorted by value
    assert [r["label"] for r in chart["rows"]] == ["50 €", "100 €", "150 €"]
    # on a phone (W59): 360 wide, the zero line in the middle, the label on its own line above the bar
    narrow = chart["narrow"]
    assert narrow["width"] == 360 and narrow["above"] and narrow["axis_x"] == 180
    assert [r["label"] for r in narrow["rows"]] == ["50 €", "100 €", "150 €"]
    assert all(r["label_y"] < r["y"] for r in narrow["rows"])
    assert all(0 <= r["x"] and r["x"] + r["w"] <= 360 for r in narrow["rows"])


def test_w60_column_heads_without_raw_placeholders():
    label = "Wie zufrieden sind Sie insgesamt mit {{verkehrsmittel}}? [Durchgang 1: dem Auto]"
    assert column_head(label, 3) == "Durchgang 1: dem Auto"             # in a loop only the pass
    assert column_head(label) == "Wie zufrieden sind Sie insgesamt mit [verkehrsmittel]? [Durchgang 1: dem Auto]"
    assert column_head("Alter") == "Alter" and column_head(None) == ""


def test_w61_hypotheses_name_the_whole_pool_under_a_filter(app, client, survey):
    from flask import render_template

    with app.test_request_context("/"):
        survey = db.session.get(Survey, 1)
        html = render_template("surveys/_hypotheses.html", hyps=[], only=True, hyp_pool=1234, survey=survey)
        assert "ungefiltert" in html and "auf dem ganzen Datenpool (1.234), nicht nur auf der gezeigten Teilgruppe" in html
        html = render_template("surveys/_hypotheses.html", hyps=[], only=None, hyp_pool=None, survey=survey)
        assert "auf der Grundlage oben" in html and "ungefiltert" not in html


def test_w62_w63_w73_filter_marks_stacked_groups_and_one_column_of_labels(app, client, survey):
    setup(app, client, ROWS)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = next(i for b in s.blocks for i in b.items if i.code == "TA")
        item.config = {**item.config, "level": "ordinal"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    # W63: ordinal questions by group as stacked 100-% rows, one per group, no table of levels
    compare = html[html.index('res-compare" id="cmp-'):]
    assert 'class="res-stack-row"' in compare and "Anteile je Gruppe in Prozent" in compare
    # W62: "Nur zeigen" waits for more ticks, and every section names the filter
    html = client.get("/surveys/1/results?nur_var=2&nur=1").data.decode()
    assert 'data-submit-on-change="700"' in html
    assert html.count('class="badge res-filtered"') >= 2 and "gefiltert: " in html
    # W73: with "Markierte einbeziehen" the box above says so
    html = client.get("/surveys/1/results?markierte=1").data.decode()
    assert "in dieser Ansicht sind sie mit „Markierte einbeziehen“ enthalten" in html
    assert "sind in Ergebnissen und Dashboards nicht enthalten" not in html
    assert "sind in Ergebnissen und Dashboards nicht enthalten" in client.get("/surveys/1/results").data.decode()


def test_w63_no_bars_for_two_answers(app, client, survey):
    setup(app, client, ROWS)
    add(client, "single_choice")                     # 4: a question with two answers
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "jn"), ("question", "Ja oder nein?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, r in enumerate(s.responses):
            r.answers = {**r.answers, "4": "1" if i % 3 else "2"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    block = html[html.index('id="res-jn"'):]
    block = block[block.index('res-compare" id="cmp-'):block.index("</section>")]
    assert "res-table res-cross" in block
    assert 'res-group-bars' not in block and "Fehlerbalken" not in block


def test_w64_round_ticks_at_their_places():
    assert results.round_ticks(47.3, 61.2) == [50, 55, 60]
    assert results.round_ticks(0.12, 0.93) == [0.25, 0.5, 0.75]
    assert results.round_ticks(5, 5) == [5]


def test_w65_test_lines_of_a_column_without_glossary_circles(app):
    with app.test_request_context("/"):
        macros = app.jinja_env.get_template("surveys/_compare_macros.html").module
        b = {"kind": "multi", "groups": [("1", "A"), ("2", "B")], "weighted": False,
             "test": {"df": 1, "n": 192, "chi2": 2.59, "p": 0.108, "v": 0.12, "cochran": False}}
        plain = str(macros.test_line(b, 5, True))
        assert plain.startswith("χ²(1, N = 192) = 2,59, p = ,108") and "data-term" not in plain


def test_w66_ordered_answers_measured_as_nominal(app, client, survey):
    from app import design

    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "kauf"), ("question", "Kaufen?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "eher ja"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "eher nein"),
        ("option_id", "d"), ("option_code", "4"), ("option_label", "nein")]))
    with app.app_context():
        item = next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.code == "kauf")
        assert design.looks_ordered(item) == ["ja", "eher ja", "eher nein", "nein"]
        assert any("Auf „ordinal“ stellen" in f.fix[0] for f in design.option_findings(item) if f.fix)
        item.config = {**item.config, "level": "ordinal"}
        assert design.looks_ordered(item) == []


def test_w71_convergence_note_in_plain_words(app):
    with app.test_request_context("/"):
        note = hb_convergence.warning({"diag_ok": False, "ess_min": 279, "kept": 2000, "rhat_max": 1.002})
    assert "die 2.000 Ziehungen tragen nur so viel Information wie 279 unabhängige Ziehungen, nötig sind 400" in note
    assert note.startswith("Die Schätzung hat sich vielleicht noch nicht eingependelt") and "((" not in note
    assert note.count("(") == 1                                         # no bracket in a bracket


def test_w72_estimation_dates_in_the_language_of_the_page_and_scale_labels():
    stored = datetime(2026, 10, 7, 7, 15)                                # UTC as stored
    local = timeutil.to_local(stored)
    assert timeutil.fmt(local, lang="en") == "7 Oct 2026, 09:15"        # once local, not moved a second time
    assert timeutil.fmt(stored) == "07.10.2026 09:15"
    assert timeutil.to_local(datetime(2026, 1, 1, 12, tzinfo=timezone.utc)).hour == 13


def test_w72_scale_labels_in_english(app, client, survey):
    from app import compare

    client.post("/account/language", data={"lang": "en"})
    setup(app, client, ROWS)
    with app.test_request_context("/"):
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).order_by(User.id).limit(1)))
        s = db.session.get(Survey, 1)
        table = compare.table_of(s, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        cmp = compare.compute(s, compare.chosen(s, "2"), results.compute(s, drop_speeders=True), table)
        assert cmp["scales"] and all(b["label"] in ("Scale score",) or b["label"].startswith("Subscale")
                                     for b in cmp["scales"])


def test_w74_simulator_note_links_the_estimation(app):
    template = open(app.root_path + "/templates/surveys/_simulator.html", encoding="utf-8").read()
    assert 'href="#hb-{{ s.item_id }}"' in template and "Schätzung der individuellen Nutzen läuft; danach rechnet der Simulator mit ihr." in template


def test_w75_what_is_weighted_in_a_fold():
    from app import learning

    assert "weighting_scope" in learning.REASONS and "weighting_scope" in learning.REASONS_EN
    assert "Linearisierungs-Standardfehler" in learning.REASONS["weighting_scope"][1]
