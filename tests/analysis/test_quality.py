"""Response patterns as quality indicators (app/quality.py)."""
from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from app import apa, quality
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"


def matrix(n, type_="likert"):
    key = quality.ELEMENTS[type_]
    return SimpleNamespace(type=type_, config={"points": 5, key: [{"id": f"s{k}"} for k in range(1, n + 1)]},
                           kind=SimpleNamespace(checks=lambda item: {}, level=lambda item: "ordinal"))


MARK = ["qc_flags", "qc_marked"]


def test_longest_run_ends_at_a_row_left_out():
    assert quality.longest_run([3, 3, 3, 2, 2]) == 3
    assert quality.longest_run([3, 3, None, 3, 3, 3]) == 3
    assert quality.longest_run([None, None]) == 0
    assert quality.longest_run([]) == 0


def test_sequence_follows_the_order_shown():
    item = matrix(3)
    answer = {"s1": 1, "s3": 5}
    assert quality.sequence(item, answer) == [1, None, 5]
    assert quality.sequence(item, answer, ["s3", "s1", "s2"]) == [5, 1, None]
    assert quality.sequence(item, None) == [None, None, None]
    assert quality.sequence(matrix(2, "semantic"), {"s2": 4}) == [None, 4]


def test_pattern_counts_matrices_from_six_answered_rows():
    straight, varied, short = [4] * 8, [1, 5, 1, 5, 1, 5], [2, 2, 2]
    out = quality.pattern([(5, straight), (5, varied), (5, short)])
    assert out["qc_longstring"] == 8
    assert out["qc_straight"] == 0.5                       # the short matrix does not count
    assert out["qc_irv"] == round((0 + 0.548) / 2, 3)      # ends as 0 and 1: sd of 0,1,0,1,0,1 = 0.548
    assert quality.pattern([(5, short)]) == {"qc_longstring": 3, "qc_straight": None, "qc_irv": None}
    assert quality.pattern([]) == {"qc_longstring": None, "qc_straight": None, "qc_irv": None}
    assert quality.pattern([(5, [None, 3])])["qc_longstring"] is None  # one answer is no run


def test_columns_without_a_matrix_only_name_the_missing_share():
    assert [c for c, _ in quality.columns([matrix(1)])] == ["qc_missing", "qc_bot"] + MARK
    assert [c for c, _ in quality.columns([matrix(2)])] == ["qc_longstring", "qc_straight", "qc_irv", "qc_missing", "qc_bot"] + MARK
    assert set(quality.codebook_notes([matrix(6)])) >= {"qc_longstring", "qc_straight", "qc_irv", "qc_missing"}


def build(app, client):
    """consent | block 2: matrix "m" (6 statements, the last reversed), age | branch age < 18 -> block 3: why"""
    fill_consent(client)
    add(client, "likert")                                       # 2
    client.post(E + "/items/2", data=MultiDict(
        [("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional")]
        + [pair for k in range(1, 7) for pair in (("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"Aussage {k}"))]
        + [("stmt_reverse", "s6")]))
    add(client, "text_input")                                   # 3
    client.post(E + "/items/3", data={"code": "age", "validation": "integer", "question": "Alter", "answer_mode": "optional"})
    client.post(E + "/blocks")                                  # 3
    add(client, "text_input", block=3)                          # 4
    client.post(E + "/items/4", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 3, "op": "lt", "value": "18"},
             "children": [{"t": "block", "block": 3}]}]}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def take(app, uid, page, follow_up=None):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", **page})
    if follow_up is not None:
        p.post(f"/s/{uid}", data={"nav": "next", "i4": follow_up})


def indicators(app):
    from app import export

    with app.app_context():
        survey = db.session.get(Survey, 1)
        ctx = quality.context(survey, export._items(survey))
        return [quality.values(survey, r, ctx) for r in db.session.scalars(
            db.select(Response).filter_by(survey_id=1, is_preview=False).order_by(Response.id))]


def test_values_of_participations(app, client, survey):
    uid = build(app, client)
    same = {f"i2_s{k}": "4" for k in range(1, 7)}
    take(app, uid, {**same, "i3": "30"})                                  # same column all along; no follow-up shown
    take(app, uid, {"i2_s1": "1", "i2_s2": "5", "i2_s3": "5", "i3": "15"}, follow_up="")  # three rows; follow-up left out
    first, second = indicators(app)
    # as clicked: the reversed statement is the same column, although the export recodes it to 2
    # one matrix and three optional questions: the values stand, but no flag below MIN_MATRICES and MIN_OPTIONAL
    # (audit 2026-10-05)
    assert first == {"qc_longstring": 6, "qc_straight": 1.0, "qc_irv": 0.0, "qc_missing": 0.0, "qc_bot": 0, "qc_flags": 0}
    # the matrix is incomplete and too short for the shares; shown: matrix, age, follow-up
    assert second == {"qc_longstring": 2, "qc_straight": None, "qc_irv": None, "qc_missing": round(2 / 3, 3), "qc_text_chars": 0, "qc_bot": 0, "qc_flags": 0}


def test_unfinished_participation_counts_up_to_the_page_reached(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)                                  # on the page with the matrix, nothing sent
    # the open page is not left out yet: it counts once it was sent (tests/analysis/test_review_small_results.py, K22)
    assert indicators(app) == [{"qc_longstring": None, "qc_straight": None, "qc_irv": None, "qc_bot": 0, "qc_flags": 0}]


def test_export_codebook_and_page(app, client, survey):
    import csv
    import io

    uid = build(app, client)
    take(app, uid, {**{f"i2_s{k}": "4" for k in range(1, 7)}, "i3": "30"})
    table = list(csv.reader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    row = dict(zip(table[0], table[1]))
    assert table[0].index("last_page") < table[0].index("qc_longstring") < table[0].index("m_1")  # with the meta columns
    assert (row["qc_longstring"], row["qc_straight"], row["qc_irv"], row["qc_missing"]) == ("6", "1", "0", "0")
    assert row["m_6"] == "2"                                             # the export recodes; the pattern is as clicked
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert book["qc_longstring"]["type"] == "Qualität" and "Johnson" in book["qc_longstring"]["notes"]
    assert "Dunn" in book["qc_irv"]["notes"] and "Einwilligung" in book["qc_missing"]["notes"]
    html = client.get("/surveys/1/quality").data.decode()
    assert 'id="patterns"' in html and "qc_straight" in html
    with app.app_context():
        rows = {r["column"]: r for r in quality.summary(db.session.get(Survey, 1))["rows"]}
    assert (rows["qc_straight"]["n"], rows["qc_straight"]["notable"], rows["qc_missing"]["notable"]) == (1, 0, 0)  # one matrix
    assert rows["qc_longstring"]["notable"] is None and rows["qc_longstring"]["max"] == 6


def test_survey_without_matrix_has_only_the_missing_share(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "q", "question": "Frage"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    head = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0].split(",")
    assert "qc_missing" in head and "qc_longstring" not in head
    assert "keine Matrix" in client.get("/surveys/1/quality").data.decode()


def test_answers_without_a_layout_still_give_the_matrix_pattern(app, client, survey):
    build(app, client)
    with app.app_context():
        db.session.add(Response(survey_id=1, status=Response.COMPLETED, answers={"2": {f"s{k}": 3 for k in range(1, 7)}}))
        db.session.commit()
    assert indicators(app) == [{"qc_longstring": 6, "qc_straight": 1.0, "qc_irv": 0.0, "qc_bot": 0, "qc_flags": 0}]  # what was shown is unknown


def with_check(app, client):
    """consent | block 2: matrix "m" with two statements and an attention check (row 3: choose point 4), the
    self-report from the toolbox"""
    fill_consent(client)
    add(client, "likert")                                       # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"), ("level", "metric"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2"),
        ("stmt_id", "s3"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_reverse", "s3"), ("stmt_check_s3", "4")]))
    add(client, "construct:sorgfalt")                           # 3
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_attention_check_is_a_row_with_the_standard_instruction(app, client, survey):
    with_check(app, client)
    with app.app_context():
        item = db.session.get(Survey, 1).blocks[1].items[0]
        assert item.config["statements"][2] == {"id": "s3", "code": "", "reverse": False, "check": 4}  # never recoded
        assert item.texts["de"]["statements"]["s3"] == "Bitte wählen Sie hier „stimme eher zu“."
        assert item.texts["en"]["statements"]["s3"] == "Please select “agree” here."
        assert item.kind.checks(item) == {"s3": 4} and item.kind.check_codes(item) == {"m_3"}
    page = client.get(E + "/items/2").data.decode()
    assert 'name="stmt_check_s3"' in page and '<option value="4" selected>richtig: stimme eher zu</option>' in page


def test_attention_check_counts_and_stays_out_of_the_scale(app, client, survey):
    import csv
    import io

    uid = with_check(app, client)
    for check, careful in (("4", "1"), ("2", "0"), ("", "1")):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": check, "i3": careful})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    # left out is no failed check but counted apart (audit 2026-10-21, E7)
    assert [(r["attention_n"], r["attention_failed"], r["attention_skipped"], r["m_3"], r["sorgfalt"]) for r in rows] == [
        ("1", "0", "0", "4", "1"), ("1", "1", "0", "2", "0"), ("1", "0", "1", "", "1")]
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "richtig: 4" in book["m_3"]["label"] and "Aufmerksamkeitsprüfung: Richtig ist 4" in book["m_3"]["notes"]
    assert "Gummer" in book["attention_failed"]["notes"]
    assert "attention_skipped" in book["attention_failed"]["notes"] and "qc_missing" in book["attention_skipped"]["notes"]
    with app.app_context():
        from app import results

        survey_ = db.session.get(Survey, 1)
        entry = next(e for e in results.compute(survey_)["items"] if e["item"].code == "m")
        # the check is neither scored nor listed: the page "Qualitätssicherung" evaluates it
        assert entry["scale"]["k"] == 2 and [c["code"] for c in entry["columns"]] == ["m_1", "m_2"] and entry["checks"] == 1
        summary = {r["column"]: r for r in quality.summary(survey_)["rows"]}
    assert (summary["attention_failed"]["n"], summary["attention_failed"]["notable"]) == (3, 1)  # the one left out: not
    assert (summary["attention_skipped"]["n"], summary["attention_skipped"]["max"], summary["attention_skipped"]["notable"]) == (3, 1, None)
    assert (summary["sorgfalt"]["n"], summary["sorgfalt"]["notable"]) == (3, 1)
    assert "Selbstauskunft" in client.get("/surveys/1/quality").data.decode()
    page = client.get("/surveys/1/results").data.decode()
    assert "<code>m_3</code>" not in page and "die Aufmerksamkeitsprüfung steht" in page


def test_dummy_data_pass_the_attention_check_mostly(app, client, survey):
    with_check(app, client)
    client.post("/surveys/1/dummy", data={"n": "60"})
    with app.app_context():
        row = {r["column"]: r for r in quality.summary(db.session.get(Survey, 1), ("dummy",))["rows"]}["attention_failed"]
    assert row["n"] == 60 and row["notable"] <= 12


def test_marking_rule_decides_results_and_export(app, client, survey):
    import csv
    import io

    from app import results

    uid = with_check(app, client)
    # 1: fine; 2: check failed and says "do not use" (two flags); 3: check failed (one flag)
    for check, careful in (("4", "1"), ("2", "0"), ("2", "1")):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": check, "i3": careful})

    def data(**args):
        return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv", query_string=args).data.decode("utf-8-sig"))))

    def basis():
        with app.app_context():
            r = results.compute(db.session.get(Survey, 1), drop_speeders=True)
            return r["n_rows"], r["marked"]

    assert [(r["qc_flags"], r["qc_marked"]) for r in data()] == [("0", "0"), ("2", "1"), ("1", "0")]  # standard: at least two
    assert basis() == (2, 1)
    assert [r["resp_id"] for r in data(source="real", drop="1")] == ["1", "3"]     # left out of the file on request
    page = client.get("/surveys/1/quality").data.decode()
    assert 'id="marking"' in page and "<strong>1 von 3</strong>" in page
    assert "Matrizen durchgeklickt" not in page and "Aufmerksamkeitsprüfung nicht bestanden" in page  # no long matrix here
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["attention"]})
    with app.app_context():
        assert db.session.get(Survey, 1).quality_rules == {"flags": ["attention"], "min": 1}
    assert [(r["qc_flags"], r["qc_marked"]) for r in data()] == [("0", "0"), ("1", "1"), ("1", "1")]
    assert basis() == (1, 2)
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "mindestens 1 " in book["qc_marked"]["notes"] and "Aufmerksamkeitsprüfung nicht bestanden" in book["qc_flags"]["notes"]
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "2"})          # nothing ticked: speeders only
    assert [r["qc_marked"] for r in data()] == ["0", "0", "0"] and basis() == (3, 0)


def test_rules_default_and_bounds():
    saved = lambda value: SimpleNamespace(quality_rules=value)  # noqa: E731
    assert quality.rules(saved(None)) == {"flags": ["straight", "missing", "attention", "self", "bot", "same", "tempo"], "min": 2}
    assert quality.rules(saved({"flags": ["self", "nope"], "min": 99})) == {"flags": ["self"], "min": 2}
    assert quality.rules(saved({"flags": [], "min": 1})) == {"flags": [], "min": 1}
    assert quality.marked({"speeder": 1, "qc_flags": 0}, 2) and not quality.marked({"qc_bot": 0, "qc_flags": 1}, 2)


def test_every_criterion_names_its_sources_in_full(app, client, survey):
    uid = with_check(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": "4", "i3": "1"})
    def cited(source, html, short=True):
        """The source stands in full in the reference list (APA) and as a short reference at its criterion."""
        r = apa.ref(source)
        return f'<li id="ref-{r["key"]}">{r["html"]}' in html and (
            not short or f'href="#ref-{r["key"]}">{r["short"].replace("&", "&amp;")}</a>' in html)

    page = client.get("/surveys/1/quality").data.decode()
    for source in (quality.LEINER, quality.CURRAN, quality.GUMMER, quality.SILBER, quality.MEADE, quality.AUST):
        assert cited(source, page)
    # one fold for all sources of the page, closed, set apart from the running text; each entry says what it is for
    assert page.count('<details class="sources">') == 1 and "<details class=\"sources\" open" not in page
    fold = page[page.index('<details class="sources">'):]
    for heading in ("Speeder: Bearbeitungszeit relativ zum Median, als bester Einzelhinweis allein markierend",
                    "Alle anderen Auffälligkeiten: mehrere zusammen statt einer einzelnen",
                    quality.SHORT["qc_longstring"], quality.SHORT["attention_failed"], "Selbstauskunft: Antworten nicht auswerten"):
        assert heading in fold
    assert "Quelle: Leiner" not in page and "nach Leiner" not in page
    assert "Curran 2016" not in page and "kein Wert aus der Quelle" in page       # short references only as "(Curran, 2016)"
    assert set(quality.SOURCES) >= set(quality.SHORT) and set(quality.FLAG_SOURCES) == {k for k, _, _ in quality.FLAGS}
    # Q-15: the even-odd index as Johnson (2005) applied it; Leiner (2019) tests the share of missing answers
    assert quality.SOURCES["qc_evenodd"][0] is quality.JOHNSON and quality.SOURCES["qc_missing"] == [quality.LEINER]
    assert all("https://doi.org/" in s for group in quality.SOURCES.values() for s in group)
    client.post("/surveys/1/pause")
    editor = client.get(E + "/items/2").data.decode()
    assert cited(quality.MONTGOMERY, editor, short=False) and cited(quality.GUMMER, editor, short=False)
    from app import tempo

    assert cited(tempo.CONRAD, client.get("/surveys/1/quality").data.decode(), short=False)  # part "Datenqualität steigern": tempo hint


def exclusion_survey(app, client, cond=None):
    """consent | block 2: matrix "m" with an attention check | failed at least once -> end "Ausschluss" | block 3: q"""
    fill_consent(client)
    add(client, "likert")                                       # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_check_s2", "4")]))
    client.post(E + "/blocks")                                  # 3
    add(client, "text_input", block=3)                          # 3
    client.post(E + "/items/3", data={"code": "q", "question": "Noch etwas?", "answer_mode": "optional"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": cond or {"item": "att", "op": "in", "value": "1, 2"},
             "children": [{"t": "end", "id": "e1", "text": {}, "kind": "excluded"}]},
            {"t": "block", "block": 3}]}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_failed_attention_check_leads_to_the_exclusion_end(app, client, survey):
    import csv
    import io

    uid = exclusion_survey(app, client)
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"
    failed, passed = app.test_client(), app.test_client()
    for p, check in ((failed, "1"), (passed, "4")):
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "3", "i2_s2": check})
    assert "Die Befragung endet für Sie an dieser Stelle" in failed.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert "Noch etwas?" in passed.get(f"/s/{uid}").data.decode()
    passed.post(f"/s/{uid}", data={"nav": "next", "i3": "nein"})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1&source=real").data.decode("utf-8-sig"))))
    assert [(r["status"], r["attention_failed"], r["q"]) for r in rows] == [("excluded", "1", ""), ("completed", "0", "nein")]
    assert rows[0]["finished"] and rows[0]["qc_missing"] == "0"          # ended there: the later question was never shown
    page = client.get("/surveys/1/data").data.decode()
    assert "<strong>1</strong> Ausschluss" in page
    with app.app_context():
        from app import methods

        f = methods.facts(db.session.get(Survey, 1))
        assert (f["excluded"], f["incomplete"], f["completed"]) == (1, 0, 1)
        assert "1 wurden während der Befragung ausgeschlossen" in methods.draft(f)


def test_attention_condition_is_ticked_and_reads_in_words(app, client, survey):
    from app import conditions, flow

    exclusion_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        described = conditions.describe(s, everything=True)
        assert described["att"]["kind"] == "choice" and [c for c, _ in described["att"]["options"]] == ["0", "1", "2"]
        assert "att" not in conditions.describe(s)                                  # not for the cells of a quota
        ticked = MultiDict([("item", "att"), ("mode", "is"), ("codes", "1"), ("codes", "2")])
        assert conditions.from_form(ticked, "", described) == {"item": "att", "op": "in", "value": "1, 2"}
        cond = flow.conditions_by_id(s)["b1"]
        assert conditions.text(s, cond) == "Aufmerksamkeitsprüfungen: eine nicht bestanden oder zwei oder mehr nicht bestanden"
        assert flow.holds(cond, {"att": "2"}) and not flow.holds(cond, {"att": "0"}) and not flow.holds(cond, {})
    assert quality.attention_view({2: {"s2": 4}}, {"2": {"s1": 3, "s2": 4}}) == {"att": "0"}
    # a check left out is not failed (audit 2026-10-21, E7): only the loop pass with another answer counts
    assert quality.attention_view({2: {"s2": 4}}, {"2": {"s1": 3}, "2#r1": {"s2": 1}, "9": "x"}) == {"att": "1"}
    assert quality.attention_view({2: {"s2": 4}}, {"2": {"s2": 1}, "2#r1": {"s2": "dk"}}) == {"att": "2"}
    assert quality.attention_view({2: {"s2": 4}}, {"9": "x"}) == {}                 # no page with a check sent yet
    assert "Ausschluss" in client.get("/surveys/1/flow/").data.decode()


def test_design_check_names_an_attention_condition_without_checks(app, client, survey):
    from app import design

    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "q", "question": "Frage"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": "att", "op": "eq", "value": "1"},
             "children": [{"t": "end", "id": "e1", "text": {}, "kind": "excluded"}]}]}
        db.session.commit()
        assert any("Aufmerksamkeitsprüfungen, aber der Fragebogen hat keine" in text for text in (f.message for f in design.check(s)))


def test_hidden_field_marks_scripts(app, client, survey):
    import csv
    import io

    uid = with_check(app, client)
    person, script = app.test_client(), app.test_client()
    first = person.get(f"/s/{uid}").data.decode()
    assert '<div class="p-trap" aria-hidden="true">' in first and 'name="more_info" tabindex="-1"' in first
    for p, extra in ((person, {}), (script, {"more_info": "http://example.org"})):
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={**CONSENT_OK, **extra})
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": "4", "i3": "1", **extra})
    # the same two minutes for both: durations count in whole seconds, and two runs a fraction of a second long that
    # straddle a second made one of them 0 s against a median of 0.5 s, a speeder (failed once in a full run, 2026-10-21)
    from datetime import timedelta

    with app.app_context():
        for r in db.session.scalars(db.select(Response).where(Response.survey_id == 1)):
            r.started_at = r.completed_at - timedelta(minutes=2)
        db.session.commit()
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [(r["qc_bot"], r["qc_flags"], r["qc_marked"]) for r in rows] == [("0", "0", "0"), ("1", "1", "0")]
    assert "device" not in rows[0] and "_bot" not in rows[0]                 # kept apart from the device information
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["bot"]})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [r["qc_marked"] for r in rows] == ["0", "1"]
    page = client.get("/surveys/1/quality").data.decode()
    assert "Verstecktes Feld ausgefüllt" in page and str(apa.ref(quality.GOODRICH)["html"]) in page
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "Goodrich" in book["qc_bot"]["notes"]


def test_even_odd_consistency_of_one_person():
    assert quality.even_odd([(1, 1), (3, 3), (5, 5), (2, 2), (4, 4)]) is None   # fewer than six scales
    assert quality.even_odd([(2, 2)] * 6) is None                               # no variation
    assert quality.even_odd([(0, 0.25), (0.5, 0.5), (1, 0.75), (0.25, 0.375), (0.75, 0.625), (1, 0.75)]) == 1.0
    assert quality.even_odd([(0, 1), (0.5, 0.5), (1, 0), (0.25, 0.75), (0.75, 0.25), (0, 1)]) == -1.0
    assert 0 < quality.even_odd([(0, 0.25), (0.5, 0.25), (1, 0.75), (0.25, 0.75), (0.5, 0.5), (1, 1)]) < 1
    units = [[("a", False), ("b", True), ("c", False)], [("d", False), ("e", False)]]
    # reversed statements are recoded (6 - 2 = 4); odd: a and c, even: b; on 0 to 1; a scale with an empty half
    # is left out
    assert quality.halves(units, {"a": 5, "b": 2, "c": 3, "d": 1}, 5) == [(0.75, 0.75)]


def scales_and_texts(app, client):
    """consent | block 2: six metric scales of two statements each (the second reversed), two open questions"""
    fill_consent(client)
    for n, code in enumerate(("a", "b", "c", "d", "e", "f"), start=2):
        add(client, "likert")                                   # 2 to 7
        client.post(E + f"/items/{n}", data=MultiDict([
            ("code", code), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"), ("level", "metric"),
            ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
            ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2")]))
    for n, code in ((8, "why"), (9, "more")):
        add(client, "text_input")                               # 8, 9
        client.post(E + f"/items/{n}", data={"code": code, "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_consistency_and_open_answers_in_the_export(app, client, survey):
    import csv
    import io

    uid = scales_and_texts(app, client)
    same = "Weil mir das Thema sehr wichtig ist."
    people = [
        # consistent: high, middle, low on both statements of a scale (the second is reversed: 6 - answer)
        ({"i2_s1": "5", "i2_s2": "1", "i3_s1": "3", "i3_s2": "3", "i4_s1": "1", "i4_s2": "5",
          "i5_s1": "5", "i5_s2": "1", "i6_s1": "3", "i6_s2": "3", "i7_s1": "1", "i7_s2": "5"}, same, "nein"),
        # the same column everywhere: with the recoding the halves contradict each other
        ({"i2_s1": "5", "i2_s2": "5", "i3_s1": "3", "i3_s2": "3", "i4_s1": "1", "i4_s2": "1",
          "i5_s1": "5", "i5_s2": "5", "i6_s1": "3", "i6_s2": "3", "i7_s1": "1", "i7_s2": "1"},
         "  weil mir das THEMA sehr wichtig ist. ", "nein"),
        ({"i2_s1": "4", "i2_s2": "2"}, "", ""),                                # one scale only, nothing typed
    ]
    for scales, why, more in people:
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next", **scales, "i8": why, "i9": more})
    head, *lines = list(csv.reader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    rows = [dict(zip(head, line)) for line in lines]
    assert [r["qc_evenodd"] for r in rows] == ["1", "-1", ""]
    assert [r["qc_text_chars"] for r in rows] == [str(len(same) + 4), str(len(same) + 4), "0"]
    assert [r["qc_text_same"] for r in rows] == ["1", "1", "0"]                 # "nein" is too short to count
    assert [r["qc_flags"] for r in rows] == ["1", "1", "1"]                     # the third: half of the questions open
    assert head.index("qc_irv") < head.index("qc_evenodd") < head.index("qc_missing") < head.index("qc_text_chars")         < head.index("qc_bot") < head.index("qc_flags")
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "Spearman-Brown" in book["qc_evenodd"]["notes"] and "Meade" in book["qc_evenodd"]["notes"]
    assert "Storozuk" in book["qc_text_same"]["notes"]
    page = client.get("/surveys/1/quality").data.decode()
    assert "Konsistenz gerade/ungerade" in page and "Gleiche offene Antwort" in page
    assert "Offene Antworten, die wortgleich bei einer anderen Teilnahme stehen" in page   # in the sources fold
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["same"]})
    marked = [r["qc_marked"] for r in csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")))]
    assert marked == ["1", "1", "0"]
    with app.app_context():
        from app import results

        assert results.compute(db.session.get(Survey, 1), drop_speeders=True)["n_rows"] == 1   # the results follow


def test_scales_for_the_consistency(app, client, survey):
    scales_and_texts(app, client)
    with app.app_context():
        items = [i for b in db.session.get(Survey, 1).blocks for i in b.items]
        likert = next(i for i in items if i.code == "a")
        assert quality.scale_units(likert) == [[("s1", False), ("s2", True)]]
        assert [quality.is_open(i) for i in items if i.type == "text_input"] == [True, True]
        ctx = quality.context(db.session.get(Survey, 1), items)
        assert len(ctx["units"]) == 6 and ctx["texts"] is None
        assert "qc_evenodd" in [c for c, _ in quality.columns(items)]
        assert "qc_evenodd" not in [c for c, _ in quality.columns(items[:6])]   # five scales are not enough


def test_focus_and_paste_are_counted_when_the_survey_says_so(app, client, survey):
    import csv
    import io

    from app import paradata

    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    meta = {"title": "Test", "languages": ["de"], "default_language": "de", "allow_back": "1"}
    settings = client.get("/surveys/1/quality").data.decode()  # "Vor dem Start: Datenqualität steigern" (2026-10-12, E6)
    assert 'name="focus_paste"' in settings
    assert "Literatur zu allen Kriterien dieser Seite" in client.get("/surveys/1/quality").data.decode()
    assert str(apa.ref(paradata.DIEDENHOFEN)["html"]) in client.get("/surveys/1/quality").data.decode()  # the sources fold
    client.post("/surveys/1/meta", data={**meta, "focus_paste": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert s.focus_paste and "wie oft Sie während der Befragung das Fenster oder den Tab wechseln" in consent.kind.template_body(consent, "de")
        assert "how often you switch to another window or tab" in consent.kind.template_body(consent, "en")
        from app import design

        # the consent of this test has a text of its own that does not name it: the pre-launch check says so
        assert any("Fensterwechsel und Einfügen" in f.message for f in design.check(s))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    assert "wie oft Sie das Fenster oder den Tab wechseln" in client.get(f"/s/{uid}/privacy").data.decode()
    watched, plain = app.test_client(), app.test_client()
    assert "data-focus" in watched.get(f"/s/{uid}").data.decode()
    watched.post(f"/s/{uid}", data={**CONSENT_OK, "_fp_blur": "1", "_fp_paste": "0"})
    watched.post(f"/s/{uid}", data={"nav": "next", "i2": "eingefügt", "_fp_blur": "2", "_fp_paste": "99999"})
    plain.get(f"/s/{uid}")
    plain.post(f"/s/{uid}", data=CONSENT_OK)                             # no JavaScript: nothing counted
    plain.post(f"/s/{uid}", data={"nav": "next", "i2": "getippt"})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [(r["focus_lost"], r["focus_pages"], r["pasted"]) for r in rows] == [("3", "2", "999"), ("", "", "")]
    book = {r["variable"]: r for r in csv.DictReader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert book["focus_lost"]["type"] == "Paradaten" and "Diedenhofen" in book["pasted"]["notes"]
    page = client.get("/surveys/1/quality").data.decode()
    assert "Fenster oder Tab verlassen (wie oft)" in page and "In ein Textfeld eingefügt (wie oft)" in page


def test_focus_and_paste_are_off_by_default(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    assert "data-focus" not in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={**CONSENT_OK, "_fp_blur": "5", "_fp_paste": "5"})    # sent anyway: not kept
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert "_fp" not in (r.paradata or {})
    assert "focus_lost" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    assert "Fenster oder Tab wechseln" not in client.get(f"/s/{uid}/privacy").data.decode()


def test_prevention_lives_on_the_quality_page(app, client, survey):
    """Ralf, 2026-10-03 (UI/UX audit, S3), moved by Philipp, 2026-10-04, and again in the UI audit of 2026-10-12 (E6):
    paradata and the tempo hint are set in "Vor dem Start: Datenqualität steigern" on the page "Qualitätssicherung"
    (on top in a draft, at the end afterwards); the settings page names them where it lists what the consent says,
    with a link there, and saving it leaves them as they are."""
    fill_consent(client)
    page = client.get("/surveys/1/quality").data.decode()
    assert 'id="prevent"' in page and 'name="tempo_hint"' in page and 'name="page_timing"' in page
    assert "Vor dem Start: Datenqualität steigern" in page and page.index('id="prevent"') < page.index('id="pool"')
    field = client.get("/surveys/1/field").data.decode()
    assert 'id="prevent"' not in field and 'name="tempo_hint"' not in field     # one place for data quality
    r = client.post("/surveys/1/quality-options", data={"quality_shown": "1"})
    assert r.headers["Location"].endswith("/surveys/1/quality#prevent")
    settings = client.get("/surveys/1").data.decode()
    assert 'name="tempo_hint"' not in settings and 'name="paradata"' not in settings
    client.post("/surveys/1/quality-options", data={"quality_shown": "1", "paradata": "1", "focus_paste": "1",
                                                     "tempo_hint": "all", "tempo_dose": "once"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.paradata and s.focus_paste and s.tempo_hint == "all" and s.page_timing   # the hint brings the page times
    settings = client.get("/surveys/1").data.decode().split('data-live="consent-mentions"')[1].split("</div>")[0]
    assert "technische Angaben" in settings and "Zeit je Seite" in settings and "/surveys/1/quality#prevent" in settings
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                         "quality_elsewhere": "1", "allow_back": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.paradata and s.focus_paste and s.tempo_hint == "all" and s.allow_back


def replay_survey(app, client, tail=False):
    """consent | block 2: matrix m1 with a check | failed so far -> block 3: "Extra?" | block 4: matrix m2 with a
    check | (tail) block 5: "Zum Schluss?" """
    fill_consent(client)

    def matrix_with_check(item, code, text):
        client.post(E + f"/items/{item}", data=MultiDict([
            ("code", code), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"),
            ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", text),
            ("stmt_id", "c"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_check_c", "4")]))

    add(client, "likert")                                       # 2
    matrix_with_check(2, "m1", "Erste Matrix")
    client.post(E + "/blocks")                                  # 3
    add(client, "text_input", block=3)                          # 3
    client.post(E + "/items/3", data={"code": "extra", "question": "Extra?", "answer_mode": "optional"})
    client.post(E + "/blocks")                                  # 4
    add(client, "likert", block=4)                              # 4
    matrix_with_check(4, "m2", "Zweite Matrix")
    children = [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                {"t": "branch", "id": "b1", "cond": {"item": "att", "op": "in", "value": "1, 2"},
                 "children": [{"t": "block", "block": 3}]},
                {"t": "block", "block": 4}]
    if tail:
        client.post(E + "/blocks")                              # 5
        add(client, "text_input", block=5)                      # 5
        client.post(E + "/items/5", data={"code": "last", "question": "Zum Schluss?", "answer_mode": "optional"})
        children.append({"t": "block", "block": 5})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": children}
        s.allow_back = True
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_replay_counts_only_the_checks_before_a_condition(app, client, survey):
    """Q-1: check 1 passed, so "Extra?" was not shown; check 2 failed later must not make it "shown" (and left out)."""
    uid = replay_survey(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "3", "i2_c": "4"})     # passes check 1
    assert "Zweite Matrix" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "3", "i4_c": "1"})     # fails check 2
    [values] = indicators(app)
    assert values["attention_failed"] == 1
    assert values["qc_missing"] == 0.0


def test_going_back_a_later_failed_check_does_not_change_the_way(app, client, survey):
    """Q-1 in the page engine: after going back in front of the branch, the check failed further on does not
    send the person to "Extra?" nor drop the answers given there."""
    uid = replay_survey(app, client, tail=True)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "3", "i2_c": "4"})     # passes check 1
    p.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "3", "i4_c": "1"})     # fails check 2
    assert "Zum Schluss?" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "back", "i5": ""})
    assert "Zweite Matrix" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "back", "i4_s1": "3", "i4_c": "1"})
    assert "Erste Matrix" in p.get(f"/s/{uid}").data.decode()                # not "Extra?"
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "3", "i2_c": "4"})
    assert "Zweite Matrix" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False)).one()
        assert r.answers.get("4") == {"s1": 3, "c": 1}


def test_check_rows_and_carried_rows_are_no_part_of_the_matrix_patterns(app, client, survey):
    """Mathe-Audit Q-2: a matrix of six statements and an attention check. Who clicks every statement on 3 and the
    check correctly on 4 has clicked the matrix through; who clicks everything on 4 has a run of six, not seven."""
    fill_consent(client)
    add(client, "likert")                                       # 2
    form = [("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"), ("level", "metric")]
    for k in range(1, 7):
        form += [("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"Aussage {k}")]
    form += [("stmt_id", "s7"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_check_s7", "4")]
    client.post(E + "/items/2", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        items = [i for b in s.blocks for i in b.items]
        item = next(i for i in items if i.code == "m")
        assert quality.content_rows(item) == [f"s{k}" for k in range(1, 7)]
        carried = SimpleNamespace(type="likert", config={"statements": [{"id": "a"}, {"id": "b", "src": {"item": 9}}]},
                                  kind=SimpleNamespace(checks=lambda item: {}))
        assert quality.content_rows(carried) == ["a"] and not quality.has_matrix([carried])
        ctx = quality.context(s, items)
        a = Response(survey_id=1, answers={"2": {**{f"s{k}": 3 for k in range(1, 7)}, "s7": 4}})
        b = Response(survey_id=1, answers={"2": {f"s{k}": 4 for k in range(1, 8)}})
        va, vb = quality.values(s, a, ctx), quality.values(s, b, ctx)
        assert (va["qc_straight"], va["qc_irv"], va["qc_longstring"]) == (1.0, 0.0, 6)
        assert vb["qc_longstring"] == 6 and va["attention_failed"] == 0 and vb["attention_failed"] == 0


def test_common_phrases_are_no_identical_open_answers(app, client, survey):
    """Mathe-Audit Q-5: from 30 characters and five words on; "Keine Anmerkungen" and the like do not count."""
    for phrase in ("Keine Anmerkungen", "Nichts hinzuzufügen", "Ich habe keine Anmerkungen", "Alles gut, danke!"):
        assert not quality.long_text(quality.normal(phrase)), phrase
    assert quality.long_text(quality.normal("Weil mir das Thema sehr wichtig ist."))
    assert "ab 30 Zeichen und 5 Wörtern" in dict((k, label) for k, label, _ in quality.FLAGS)["same"]


def test_speeders_are_marked_alone_and_the_texts_say_so(app, client, survey):
    """Mathe-Audit Q-10: speeding marks alone (unchanged); the reason given is no longer "one alone marks nobody"."""
    with_check(app, client)
    with app.app_context():
        notes = quality.codebook_notes([], db.session.get(Survey, 1))
    assert "Speeder (allein: Die Bearbeitungszeit gilt als bester Einzelhinweis" in notes["qc_marked"]
    assert quality.marked({"speeder": 1, "qc_flags": 0}, 2) and not quality.marked({"speeder": 0, "qc_flags": 1}, 2)
    page = client.get("/surveys/1/quality").data.decode()
    assert "auch ohne weitere Auffälligkeit: Die Bearbeitungszeit gilt als bester einzelner Hinweis" in page


def test_note_to_ai_systems_marks_agents_that_follow_it(app, client, survey):
    import csv
    import io

    with app.app_context():
        db.session.get(Survey, 1).ai_bait = True
        db.session.commit()
    uid = with_check(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        word, (de, _) = quality.bait_word(s), quality.bait(s)
        assert word in de and word == quality.bait_word(s)                      # fixed per survey
        facts = s.blocks[0].items[0].kind.facts(s.blocks[0].items[0], "de")
        assert facts["automation_check"]
    person, agent = app.test_client(), app.test_client()
    first = person.get(f"/s/{uid}").data.decode()
    assert 'name="ki_kennwort" tabindex="-1"' in first and word in first
    assert first.count('<div class="p-trap" aria-hidden="true">') == 2              # out of sight, not read out
    for p, extra in ((person, {}), (agent, {"ki_kennwort": word.lower()})):
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={**CONSENT_OK, **extra})
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": "4", "i3": "1"})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [(r["qc_ai"], r["qc_flags"], r["qc_marked"]) for r in rows] == [("0", "0", "0"), ("1", "0", "0")]  # not ticked by itself
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["ai"]})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [r["qc_marked"] for r in rows] == ["0", "1"]
    page = client.get("/surveys/1/quality").data.decode()
    assert "KI-Köder befolgt" in page and "Höhne" in page
    assert "Hinweis an KI-Systeme" in person.get(f"/s/{uid}/privacy").data.decode()


def test_note_to_ai_systems_is_off_by_default(app, client, survey):
    uid = with_check(app, client)
    page = app.test_client().get(f"/s/{uid}").data.decode()
    assert "ki_kennwort" not in page
    with app.app_context():
        assert "qc_ai" not in [c for c, _ in quality.columns([], False)]
        assert "ai" not in quality.rules(db.session.get(Survey, 1))["flags"]


def test_nearly_the_same_open_answers_are_found_across_participations():
    ctx = {"open": {5}}
    text = "Ich finde die Idee gut, weil sie Zeit spart und die Umwelt schont"
    rs = [SimpleNamespace(answers={"5": text}), SimpleNamespace(answers={"5": text.replace("gut", "sehr gut") + "."}),
          SimpleNamespace(answers={"5": "Mir ist das Thema ehrlich gesagt ziemlich egal, ich fahre weiter Auto"}),
          SimpleNamespace(answers={"5": text})]
    near = quality.near_index(rs, ctx)
    assert (5, quality.normal(text.replace("gut", "sehr gut") + ".")) in near
    assert not any("egal" in t for _, t in near)                                   # a different answer
    one = quality.near_index(rs[1:3], ctx)
    assert one == set()                                                            # nothing like it in that pool


def test_input_steps_tell_typed_from_filled_in_text(app, client, survey):
    import csv
    import io

    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                         "allow_back": "1", "focus_paste": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.focus_inputs and "wie viele Eingaben" in s.consent_item.kind.template_body(s.consent_item, "de")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    assert "wie viele Eingaben" in client.get(f"/s/{uid}/privacy").data.decode()
    people = {"typed": ("60", "0"), "filled": ("1", "0"), "pasted": ("2", "64")}
    for name, (steps, chars) in people.items():
        long = {"typed": "Weil ich das Thema für wichtig halte und gern mitmache.",
                "filled": "Mich interessiert Forschung, darum nehme ich teil, ganz einfach.",
                "pasted": "Meine Nachbarin hat mir den Link geschickt und ich war neugierig."}[name]
        p = app.test_client()
        assert "data-focus-inputs" in p.get(f"/s/{uid}").data.decode()
        p.post(f"/s/{uid}", data={**CONSENT_OK, "_fp_blur": "0", "_fp_paste": "0", "_fp_inputs": "0", "_fp_pchars": "0"})
        p.post(f"/s/{uid}", data={"nav": "next", "i2": long, "_fp_blur": "0", "_fp_paste": str(int(chars > "0")),
                                  "_fp_inputs": steps, "_fp_pchars": chars})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [(r["text_inputs"], r["pasted_chars"], r["qc_untyped"]) for r in rows] == [
        ("60", "0", "0"), ("1", "0", "1"), ("2", people["pasted"][1], "0")]
    assert [r["qc_flags"] for r in rows] == ["0", "0", "0"]                         # not counted unless ticked
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["typed", "paste"]})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [r["qc_marked"] for r in rows] == ["0", "1", "1"]
    page = client.get("/surveys/1/quality").data.decode()
    assert "Text ohne Tippen" in page and "Text eingefügt" in page


def test_surveys_counting_focus_before_keep_their_consent(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    fill_questions(app)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.focus_paste = True                                                     # as a survey started before
        db.session.commit()
        uid = s.code
    p = app.test_client()
    page = p.get(f"/s/{uid}").data.decode()
    assert "data-focus" in page and "data-focus-inputs" not in page
    p.post(f"/s/{uid}", data={**CONSENT_OK, "_fp_blur": "0", "_fp_paste": "0", "_fp_inputs": "5", "_fp_pchars": "9"})
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.paradata["_fp"] == {"lost": 0, "pages": 0, "pasted": 0}         # not counted: not in its consent
    assert "pasted_chars" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]


def test_results_can_include_the_marked_participations_to_compare(app, client, survey):
    uid = with_check(app, client)
    for extra in ({}, {"more_info": "http://example.org"}):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={**CONSENT_OK, **extra})
        p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "5", "i2_s2": "1", "i2_s3": "4", "i3": "1"})
    client.post("/surveys/1/quality", data={"source": "real", "qc_min": "1", "qc_flag": ["bot"]})
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert "Grundlage: <strong>1</strong> Teilnahme · ohne 1 Markierte" in page   # S6: the state in one sentence
    assert 'name="markierte" value="1"  data-submit-on-change> Markierte einbeziehen' in page
    page = client.get("/surveys/1/results?markierte=1&go=1").data.decode()
    assert "Grundlage: <strong>2</strong> Teilnahmen · mit 1 Markierten" in page and "enthalten" in page
    assert 'name="markierte" value="1" checked data-submit-on-change' in page
    quality_page = client.get("/surveys/1/quality").data.decode()
    assert 'id="agents"' in quality_page and "Schutz vor Bots und KI-Agenten" in quality_page and "Westwood, S. J. (2025)" in quality_page


def test_attention_check_left_out_is_skipped_not_failed():
    """Audit 2026-10-21, E7: leaving a check out is item nonresponse (qc_missing counts it), not a failed check;
    "weiß nicht" is another answer and fails."""
    assert quality.attention({"s3": 4}, {"s1": 2}) == (0, 1)
    assert quality.attention({"s3": 4}, None) == (0, 1)
    assert quality.attention({"s3": 4, "s4": 1}, {"s3": "dk", "s4": 1}) == (1, 0)
    assert quality.attention({"s3": 4}, {"s3": 4}) == (0, 0)
    assert not quality.notable("attention_skipped", 3) and quality.notable("attention_failed", 1)
    assert "attention_skipped" in [c for c, _ in quality.ATTENTION_COLUMNS] and "attention_skipped" in quality.COLUMNS_EN
