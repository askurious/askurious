"""Pages that got slower with every participation (performance audit of 2026-10-02)."""
import re

import pytest
from sqlalchemy import event
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items import base
from app.models import Item, Response, utcnow
from tests.conftest import add

E = "/surveys/1/editor"


def _choice(client, codes=("1", "2")):
    add(client, "single_choice")  # item 2
    client.post(E + "/items/2", data=MultiDict(
        [("code", "farbe"), ("question", "Farbe?")] + [x for c in codes for x in (("option_code", c), ("option_label", f"Option {c}"))]))


def _the_slow_way(item):
    """What has_real_answers did before: every real participation read and searched here."""
    key = str(item.id)
    rows = db.session.execute(db.select(Response.answers, Response.layout).filter_by(
        survey_id=item.block.survey_id, is_preview=False, source="real"))
    return any(any(k.split("#")[0] == key for k in (answers or {})) or key in ((layout or {}).get("cj") or {})
               for answers, layout in rows)


CASES = [
    ({}, False),                                                                     # no participation at all
    ({"source": "test", "is_preview": True, "answers": {"2": "1"}}, False),           # a test run
    ({"source": "dummy", "is_preview": True, "answers": {"2": "1"}}, False),          # dummy data
    ({"answers": {"2": "1"}}, True),
    ({"answers": {"2": None}}, True),                                                # seen and skipped: stored as well
    ({"answers": {"1": "1", "2#r1": "1"}}, True),                                     # a pass of a loop
    ({"answers": {"22": "1", "12": "1", "22#r2": "1", "20": "2"}}, False),            # other items whose number contains "2"
    ({"answers": {}, "layout": {"cj": {"2": {"version": 0}}}}, True),                # a choice state only
    ({"answers": {}, "layout": {"cj": {"22": {"version": 0}}, "pages": []}}, False),
    ({"answers": {}, "layout": {"cj": {"2": None}}}, True),
    ({"answers": {}, "layout": None}, False),
    ({"survey_id": 2, "answers": {"2": "1"}}, False),                                 # another survey
]


@pytest.mark.parametrize("fields,expected", CASES)
def test_has_real_answers_asks_the_database_and_agrees_with_reading_everything(app, client, survey, fields, expected):
    _choice(client)
    if fields.get("survey_id") == 2:
        client.post("/surveys/new", data={"title": "Andere", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        if fields:
            db.session.add(Response(**{"survey_id": 1, "source": "real", "is_preview": False, **fields}))
            db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.has_real_answers(item) is expected
        assert _the_slow_way(item) is expected


def test_has_real_answers_without_json_functions(app, client, survey, monkeypatch):
    """An SQLite built without the JSON functions: the answers are read as before, the result is the same."""
    _choice(client)
    monkeypatch.setattr(base, "REAL_ANSWER_SQL", "SELECT no_such_function(:survey, :state, :key, :n, :loop_key)")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.has_real_answers(item)
        db.session.add(Response(survey_id=1, source="real", answers={"2#r1": "1"}))
        db.session.commit()
        assert item.kind.has_real_answers(item)


def _answer_reads(app):
    """Statements that fetch the answers of participations, noted as they run."""
    seen = []
    with app.app_context():
        @event.listens_for(db.engine, "before_cursor_execute")
        def _note(conn, cursor, statement, parameters, context, executemany):
            if "response.answers" in statement.split("FROM")[0]:
                seen.append(statement)
    return seen


def test_editor_and_flow_page_do_not_read_the_answers(app, client, survey):
    """Both pages check every element (design.check); each choice question used to read all answers of the
    survey for that: 12 passes over 1000 participations took 2 s."""
    _choice(client)
    add(client, "multiple_choice")  # item 3
    with app.app_context():
        for n in range(3):
            db.session.add(Response(survey_id=1, source="real", answers={"2": "1", "3": ["1"]}))
        db.session.commit()
    reads = _answer_reads(app)
    assert client.get(E + "/").status_code == 200
    assert client.get("/surveys/1/flow/").status_code == 200
    assert client.get(E + "/items/2").status_code == 200
    assert reads == []


def test_invalid_answer_codes_are_still_reported_until_real_answers_exist(app, client, survey):
    add(client, "multiple_choice")  # item 2: its answer codes become part of the column names
    with app.app_context():
        item = db.session.get(Item, 2)  # codes as an older version of the editor could store them
        item.config = {**item.config, "options": [{"id": "a", "code": "18-29"}, {"id": "b", "code": "2"}]}
        db.session.commit()
        assert any("Variablennamen" in p and "18-29" in p for p in item.kind.validate(item))
        db.session.add(Response(survey_id=1, source="real", answers={"2": ["2"]}))
        db.session.commit()
        assert not any("Variablennamen" in p for p in item.kind.validate(item))  # renaming would cut the answers off


# --- package B: the same numbers, computed in fewer steps -----------------------------------------------------


def _psm_cases(rng, n, grid):
    """Consistent PSM cases; a coarse grid makes many answers share a price (ties on the curves)."""
    out = []
    while len(out) < n:
        tc, c, e, te = sorted(float(rng.choice(grid)) for _ in range(4))
        if tc < c < e < te:
            out.append({"tc": tc, "c": c, "e": e, "te": te, "nms_c": rng.randint(1, 5), "nms_e": rng.randint(1, 5)})
    return out


@pytest.mark.parametrize("seed,n,grid", [(1, 40, range(10, 200, 10)), (2, 300, range(1, 5000)), (3, 2, (1, 2, 3, 4)),
                                         (4, 120, [x / 4 for x in range(4, 400)])])
def test_psm_curves_and_trial_are_the_numbers_of_the_plain_computation(seed, n, grid, monkeypatch):
    import random
    import statistics

    from app import psm_analysis as psm

    cases = _psm_cases(random.Random(seed), n, list(grid))
    prices = sorted({c[k] for c in cases for k in psm.KEYS})

    def side(v, p, above):
        return 0.5 if v == p else float(v > p if above else v < p)

    plain = {key: [sum(side(c[k], p, above) for c in cases) / n for p in prices]
             for key, k, above in (("too_cheap", "tc", True), ("not_cheap", "c", False), ("not_expensive", "e", True),
                                   ("too_expensive", "te", False))}
    assert psm.curves(cases) == {"prices": prices, **plain}
    weights = [0, 10, 30, 50, 70]
    trial = [statistics.fmean(psm.probability(c, p, weights) for c in cases) for p in prices]
    assert psm.nms(cases, prices, weights)["trial"] == trial        # to the last digit
    monkeypatch.setattr(psm, "NMS_CELLS", 7)                         # in many small portions
    assert psm.nms(cases, prices, weights)["trial"] == trial


def _random_sets(rng, coding, levels, sizes, n, with_none):
    from app.choice_model import ChoiceSet

    sets = []
    for _ in range(n):
        rows = [coding.row([rng.randrange(m) for m in levels]) for _ in range(rng.choice(sizes))]
        if with_none:
            rows.append(coding.asc_row())
        sets.append(ChoiceSet(rows, rng.randrange(len(rows)), person=rng.randrange(5)))
    return sets


@pytest.mark.parametrize("seed,levels,with_none,n", [(1, [3, 2, 4], True, 60), (2, [2, 2], False, 8), (3, [5, 3, 1, 2], True, 30),
                                                     (4, [4, 4, 4], False, 3), (5, [3], True, 1)])
def test_separated_and_shown_levels_agree_with_asking_every_alternative(seed, levels, with_none, n):
    import random

    from app.choice_model import Coding

    rng = random.Random(seed)
    coding = Coding(levels, asc=with_none)
    sets = _random_sets(rng, coding, levels, (2, 3), n, with_none)
    stats, shown = {}, set()
    for s in sets:  # the plain way: level_of() per alternative
        for a in coding.effects:
            have = [coding.level_of(x, a) for x in s.X]
            shown |= {(a, lv) for lv in have if lv is not None}
            for lv in set(have) - {None}:
                if all(h == lv for h in have):
                    continue
                seen, chosen = stats.get((a, lv), (0, 0))
                stats[(a, lv)] = (seen + 1, chosen + (have[s.chosen] == lv))
    assert coding.separated(sets) == sorted(k for k, (seen, chosen) in stats.items() if chosen in (0, seen))
    assert coding.shown_levels(sets) == shown
    assert coding.separated([]) == [] and coding.shown_levels([]) == set()


def test_export_table_knows_its_participations(app, client, survey):
    """The choice models take the participations from the table instead of fetching them again by pid; a plain
    list of rows (a filtered copy) still works, also with more pids than one statement takes."""
    from app import export
    from app.choice_model import once, responses_for
    from app.models import Survey

    _choice(client)
    with app.app_context():
        db.session.add_all([Response(survey_id=1, source="real", status=Response.COMPLETED, pid=f"p{n}",
                                     answers={"2": "1"}, completed_at=utcnow()) for n in range(1100)])
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={}, completed_at=utcnow()))
        db.session.commit()
        item = db.session.get(Item, 2)
        table = export.rows(db.session.get(Survey, 1))
        assert isinstance(table, export.Table) and len(table) == 1101 and len(table.responses) == 1100  # one without pid
        statements = []
        event.listen(db.engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
        found = responses_for(item, table)
        assert [r.pid for r in found] == [f"p{n}" for n in range(1100)] and statements == []
        some = table.keep(lambda r: r["pid"] in ("p3", "p7"))
        assert isinstance(some, export.Table) and [r.pid for r in responses_for(item, some)] == ["p3", "p7"]
        assert statements == []
        # kept once per table and key, built again for another table
        built = []
        assert once(table, "k", lambda: built.append(1) or "x") == "x" and once(table, "k", lambda: built.append(1)) == "x"
        assert once(some, "k", lambda: built.append(1) or "y") == "y" and once([], "k", lambda: "z") == "z" and len(built) == 2
        plain = [r for r in table]  # no Table: fetched by pid, in portions
        assert [r.pid for r in responses_for(item, plain)] == [f"p{n}" for n in range(1100)]
        assert len([s for s in statements if "response" in s.lower()]) == 3


def test_num_takes_numbers_directly_and_reads_text_as_before():
    from app.results import _num

    def through_text(v):  # the way every value went before
        import math

        from app.items.base import MAX_NUMBER

        if v in (None, "") or isinstance(v, bool):
            return None if v in (None, "") else float(v)
        try:
            number = float(str(v).replace(",", "."))
        except ValueError:
            return None
        return number if math.isfinite(number) and abs(number) <= MAX_NUMBER else None

    values = [None, "", True, False, 0, 1, -3, 7.5, -0.0, 0.1, 1e15, 1e15 + 2, -1e15, 10 ** 15, 10 ** 15 + 1, 10 ** 400,
              float("inf"), float("-inf"), float("nan"), "3", "3,5", " 4 ", "abc", "1e5", "nan", "inf", [1], {"v": 1}]
    for v in values:
        assert repr(_num(v)) == repr(through_text(v)), v


# --- package D: the method section computes what it shows -----------------------------------------------------


def _study(app, client):
    """Gender and age from the toolbox, a BFI-10 (subscales), an own Likert scale and a conjoint; ten answers."""
    from tests.analysis.test_conjoint import conjoint_form

    for key in ("gender", "age", "bfi10"):
        client.post(E + "/blocks/2/items", data={"type": f"construct:{key}"})
    add(client, "conjoint")
    with app.app_context():
        cbc = db.session.scalar(db.select(Item.id).filter_by(type="conjoint"))
    client.post(E + f"/items/{cbc}", data=conjoint_form())
    client.post(E + f"/items/{cbc}/conjoint/design")
    with app.app_context():
        from app import dummy
        from app.models import Survey

        dummy.generate(db.session.get(Survey, 1), 12, seed=5)
        db.session.execute(db.update(Response).values(source="real", is_preview=False))
        db.session.commit()
    return cbc


def test_method_section_shows_the_same_and_computes_no_model(app, client, survey, monkeypatch):
    from app import cbc_analysis, export, methods, quality, results
    from app.models import Survey

    cbc = _study(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        # speeders only: with a marking rule that can mark someone, the patterns are needed to leave them out
        s.quality_rules = {"flags": [], "min": 1}
        whole = results.compute(s, ("real",), False, True, None, True)                      # as the page did before
        calls = {"model": 0, "rows": 0, "quality": 0}
        monkeypatch.setattr(cbc_analysis, "results", lambda *a, **k: calls.__setitem__("model", calls["model"] + 1))
        rows, values = export.rows, quality.values
        monkeypatch.setattr(export, "rows", lambda *a, **k: calls.__setitem__("rows", calls["rows"] + 1) or rows(*a, **k))
        monkeypatch.setattr(quality, "values", lambda *a, **k: calls.__setitem__("quality", calls["quality"] + 1) or values(*a, **k))
        f = methods.facts(s)
        assert calls == {"model": 0, "rows": 1, "quality": 0}            # one table, no conjoint model, no patterns
        assert (f["final"], f["speeders"]) == (whole["n_rows"], whole["speeders"]) and f["final"] > 0
        by_code = {e["item"].code: e for e in whole["items"]}
        age = next(e for e in whole["items"] if (e["item"].config.get("construct") or {}).get("key") == "age")["columns"][0]
        assert f["age"] == {k: age.get(k) for k in ("n", "mean", "sd", "min", "max")} and f["age"]["n"]
        # "keine Angabe" of the gender question counts as missing since UI/UX audit 2026-10-07, W1
        assert f["gender"] and sum(n for _, n in f["gender"]) <= f["final"]
        bfi = next(c for c in f["constructs"] if "Big" in c["name"] or "BFI" in c["name"])
        assert len(bfi["subscales"]) == 5 and all(s["k"] == 2 for s in bfi["subscales"])
        assert db.session.get(Item, cbc).code in by_code                                    # the results page still has it
        s.quality_rules = None                                                              # standard rule: can mark here
        methods.facts(s)
        assert calls["quality"] > 0 and calls["rows"] == 2
    # only= leaves the other elements out and changes nothing about those it keeps
    with app.app_context():
        s = db.session.get(Survey, 1)
        wanted = {e["item"].id for e in whole["items"] if e["item"].type == "likert"}
        part = results.compute(s, ("real",), False, True, None, True, only=wanted)
        assert [e["item"].id for e in part["items"]] == [e["item"].id for e in whole["items"] if e["item"].id in wanted]
        assert part["n_rows"] == whole["n_rows"] and part["items"][0]["scale"] == next(
            e for e in whole["items"] if e["item"].id == part["items"][0]["item"].id)["scale"]


def test_results_leave_the_response_patterns_out_and_exports_keep_them(app, client, survey):
    from app import export, results
    from app.models import Survey

    _study(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "qc_missing" in export.rows(s)[0] and "qc_missing" in export.to_csv(s).splitlines()[0]
        assert "qc_missing" not in export.rows(s, with_quality=False)[0]
        assert results.compute(s)["n_rows"] == 12


# --- package E: the response patterns of the exports ------------------------------------------------------------


def test_spread_is_the_standard_deviation_of_the_exact_computation():
    """quality.spread() from sums of whole numbers against statistics.stdev() with exact fractions: the same to
    the last digits, and what the export shows (three decimals, mean over the matrices) is the same value."""
    import random
    import statistics

    from app import quality

    rng = random.Random(11)
    for _ in range(3000):
        points = rng.randint(2, 11)
        matrices = []
        for _ in range(rng.randint(1, 6)):
            style = rng.random()
            n = rng.randint(quality.MIN_ROWS, 30)
            if style < 0.2:
                values = [rng.randint(1, points)] * n                         # the same answer all along
            elif style < 0.4:
                values = [1 if k % 2 else points for k in range(n)]           # from end to end
            else:
                values = [rng.randint(1, points) for _ in range(n)]
            exact = statistics.stdev((v - 1) / (points - 1) for v in values)
            assert abs(quality.spread(values, points) - exact) < 1e-12
            matrices.append((points, values + [None] * rng.randint(0, 3)))
        expected = round(statistics.fmean(statistics.stdev((v - 1) / (points - 1) for v in values if v is not None)
                                          for _, values in matrices), 3)
        assert quality.pattern(matrices)["qc_irv"] == expected
    # answers that are no whole numbers (not stored by any element today) go the exact way
    assert quality.spread([1.0, 2.0, 2.0, 3.0, 5.0, 5.0], 5) == statistics.stdev(v / 4 for v in (0, 1, 1, 2, 4, 4))
    assert quality.spread([1, 1, 1, 1, 1, 1], 5) == 0.0


def test_quality_context_knows_rows_and_countable_questions(app, client, survey):
    from app import export, quality
    from app.models import Survey

    _study(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        items = export._items(s)
        ctx = quality.context(s, items)
        assert ctx["rows"] == {i.id: [r["id"] for r in quality.rows_of(i)] for i in items if quality.rows_of(i)} and ctx["rows"]
        assert ctx["counted"] == {i.id for i in items if quality._counts(i)}
        assert all(i.type != "consent" for i in items if i.id in ctx["counted"])
        row = export.rows(s)[0]
        assert row["qc_missing"] == 0 and row["qc_longstring"] >= 1 and 0 <= row["qc_irv"] <= 1
