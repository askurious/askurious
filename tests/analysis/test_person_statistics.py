"""Storage and mathematical integrity regressions from the fresh audit of 14 October 2026."""
from app import diary, export, results
from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.diary.test_diary import _new
from tests.conftest import add


def test_calc_formula_cannot_reinterpret_old_answers(app, client, survey):
    add(client, "text_input")
    add(client, "calc")
    with app.app_context():
        source, calc = db.session.get(Item, 2), db.session.get(Item, 3)
        source.code, source.config = "income", {**source.config, "validation": "decimal"}
        calc.code, calc.config = "annual", {"a": "2", "b": None, "op": "mul", "fixed": 2, "places": 0, "unit": ""}
        survey = db.session.get(Survey, 1)
        survey.status = Survey.DRAFT
        db.session.add(Response(survey_id=survey.id, source="real", is_preview=False, qversion=1,
                                status=Response.COMPLETED, answers={"2": 10}, pid="calc-case",
                                started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
        assert export.rows(survey, with_hb=False)[0]["annual"] == 20
        assert calc.kind.has_real_answers(calc)
        from app import lifecycle
        survey.status = Survey.CLOSED
        lifecycle.reopen_for_editing(survey)
        db.session.commit()
        assert survey.qversion == 2
    saved = client.post("/surveys/1/editor/items/3", data={"code": "annual", "calc_a": "2", "calc_op": "mul",
                                                        "calc_fixed": "3", "calc_places": "0"})
    assert saved.status_code == 302
    with app.app_context():
        survey = db.session.get(Survey, 1)
        data = export.rows(survey, with_hb=False)[0]
        assert data["annual"] == 20 and data["qversion"] == 1
    # Switching away from the answered source must not evade the formula lock.
    client.post("/surveys/1/editor/items/3", data={"code": "annual", "calc_a": "", "calc_op": "mul",
                                                 "calc_fixed": "3", "calc_places": "0"})
    with app.app_context():
        assert export.rows(db.session.get(Survey, 1), with_hb=False)[0]["annual"] == 20
    # A separately named new computed value may still be configured after reopening.
    add(client, "calc")
    client.post("/surveys/1/editor/items/4", data={"code": "new_annual", "calc_a": "2", "calc_op": "mul",
                                                 "calc_fixed": "3", "calc_places": "0"})
    with app.app_context():
        values = export.rows(db.session.get(Survey, 1), with_hb=False)[0]
        assert values["annual"] == 20 and values["new_annual"] == 30


def test_diary_numeric_ci_counts_independent_persons():
    # Ten people, each with ten identical repeated answers. The person mean SE must not shrink on repetition.
    values = [float(i) for i in range(10) for _ in range(10)]
    weighted = results.numeric_stats(values, [0.1] * 100, persons=[i for i in range(10) for _ in range(10)])
    person = results.numeric_stats([float(i) for i in range(10)])
    assert weighted["n_eff"] == 10
    assert weighted["ci"][1] - weighted["ci"][0] == (person["ci"][1] - person["ci"][0])


def test_repeated_binary_answers_do_not_narrow_person_intervals():
    from app.person_statistics import share_ci
    binary = [0] * 10 + [1] * 10
    expected = share_ci(binary, [1] * 20, list(range(20)), 1)
    actual = share_ci([v for v in binary for _ in range(20)], [0.05] * 400,
                      [p for p in range(20) for _ in range(20)], 1)
    assert all(abs(x - y) < 1e-12 for x, y in zip(expected, actual))


def test_within_person_correlation_keeps_opposite_within_and_between_patterns():
    from app.person_statistics import collapse, within_correlation
    xs = [0, 2, 10, 12]
    ys = [2, 0, 12, 10]
    owners = [1, 1, 2, 2]
    assert within_correlation(xs, ys, owners) == (-1.0, 2, 4)
    assert list(collapse(xs, owners).values()) == [1.0, 11.0]
    assert list(collapse(ys, owners).values()) == [1.0, 11.0]


def test_diary_question_weights_do_not_equalize_people_per_optional_statement():
    from types import SimpleNamespace
    table = [{"resp_id": 1, "a": 1, "b": 1}, {"resp_id": 2, "a": 1, "b": None},
             {"resp_id": 3, "a": 5, "b": 5}]
    owners = {1: SimpleNamespace(enrolment_id=1, occasion={"part": "m1"}),
              2: SimpleNamespace(enrolment_id=1, occasion={"part": "m1"}),
              3: SimpleNamespace(enrolment_id=2, occasion={"part": "m1"})}
    weights = diary.question_weights(table, owners, ["b"])
    stats = results.numeric_stats([r["b"] for r in table], [weights[r["resp_id"]] for r in table])
    assert abs(stats["mean"] - 3) < 1e-12


def test_diary_scale_mean_agrees_with_person_weighted_statement_means(app, client):
    _new(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        block = next(b for b in s.blocks if diary.part_of(b) == "m1")
        battery = Item(block_id=block.id, type="likert", code="battery", position=2,
                       config={"points": 5, "level": "metric", "statements": [
                           {"id": "a", "code": "score_a"}, {"id": "b", "code": "score_b"}]},
                       texts={"de": {"question": "Scale", "statements": {"a": "A", "b": "B"}}})
        db.session.add(battery)
        db.session.flush()
        for i in range(10):
            db.session.add(Response(survey_id=s.id, enrolment_id=10 if i < 9 else 11,
                                    source="real", is_preview=False, status=Response.COMPLETED,
                                    occasion={"part": "m1"}, pid=f"scale-{i}",
                                    answers={str(battery.id): {"a": 1 if i < 9 else 5, "b": 1 if i < 9 else 5}},
                                    started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
        data = results.compute(s, only={battery.id})
        scale = data["items"][0]
        assert data["diary"]["by_person"]
        assert abs(scale["columns"][0]["mean"] - 3) < 1e-10
        assert abs(scale["scale"]["mean"] - 3) < 1e-10
        from app import manuscript_tables
        from docx import Document
        import io
        doc = Document(io.BytesIO(manuscript_tables.build(s, data, "en")))
        text = " ".join(p.text for p in doc.paragraphs)
        assert "N = 2 people" in text and "between-person correlations" in text
