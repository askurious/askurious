"""The person as the unit of intervals and minimums where one person gives several values (audit storage and
calculations 2026-10-28, M1, M2, M6): the entries of a diary study and the passes of a loop are no independent cases."""
import random
from types import SimpleNamespace

from app import diary_model, intervals, psm_analysis, study_plan
from app.items import get as kind_of


def _psm_table(people, entries):
    rows, persons = [], []
    rng = random.Random(4)
    for p in range(people):
        base = rng.uniform(10, 30)
        for _ in range(entries):
            rows.append({"p_tc": base, "p_c": base + 5, "p_e": base + 10, "p_te": base + 15})
            persons.append(p)
    return rows, persons


def test_psm_bootstrap_draws_persons_not_entries():
    """M1: ten persons with three entries each are ten persons, below MIN_BOOT: no interval."""
    item = SimpleNamespace(code="p")
    table, persons = _psm_table(10, 3)
    assert 10 < intervals.MIN_BOOT <= 30
    as_rows = [c for c in psm_analysis.cases(table, item) if psm_analysis.consistent(c)]
    assert psm_analysis.point_intervals(as_rows, draws=50, seed=1) is not None  # 30 rows read as 30 persons
    as_persons = [c for c in psm_analysis.cases(table, item, persons=persons) if psm_analysis.consistent(c)]
    assert psm_analysis.point_intervals(as_persons, draws=50, seed=1) is None
    # persons of a diary study: enrolment ids next to rows without one (results.compute person_ids)
    mixed = [c for c in psm_analysis.cases(*_psm_table(40, 1)[:1], item,
                                           persons=[n if n % 2 else ("row", n) for n in range(40)])]
    assert psm_analysis.point_intervals(mixed, draws=50, seed=1) is not None


class _Ranking(SimpleNamespace):
    pass


def test_ranking_interval_is_over_persons_not_passes(monkeypatch):
    """M2: two persons with ten passes each give no interval (two persons, below MIN_N); one value per person gives
    the plain t interval as before."""
    kind = kind_of("ranking")
    monkeypatch.setattr(type(kind), "options", lambda self, item, lang: [{"code": "a", "label": "A"}])
    item = _Ranking(code="r")
    passes = [str(n) for n in range(1, 11)]
    table = [{f"r_a_{n}": 1 for n in passes}, {f"r_a_{n}": 2 for n in passes}]
    row = kind.summary(item, table, "de", passes)[0]
    assert row["n"] == 20 and row["persons"] == 2 and row["mean"] == 1.5 and row["ci"] is None
    # the entries of a diary study: rows of one person
    flat = [{"r_a": v} for v in (1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3, 1)]
    one = kind.summary(item, flat, "de", (), persons=[0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1])[0]
    assert one["persons"] == 2 and one["ci"] is None
    plain = kind.summary(item, flat, "de")[0]
    import statistics

    values = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3, 1]
    expect = intervals.mean_ci(statistics.fmean(values), statistics.stdev(values), len(values))
    assert plain["ci"] == expect or all(abs(a - b) < 1e-12 for a, b in zip(plain["ci"], expect))


def test_multilevel_minimum_counts_the_rows_with_every_value(monkeypatch):
    """M6: 30 persons with five outcomes each, what explains only for ten of them: the model is not fitted (ten
    persons), and says so; with what explains for all 30 it is."""
    monkeypatch.setattr(study_plan, "plan_of", lambda survey: {"diary": {"lagged": False, "slope": False}})
    rng = random.Random(7)

    def run(with_x):
        table, xs, ys = [], [], []
        for p in range(30):
            for k in range(5):
                x = rng.gauss(0, 1)
                table.append({"person": f"p{p}", "started": f"2026-01-{k + 1:02d}", "resp_id": p * 10 + k})
                xs.append(x if p < with_x else None)
                ys.append(0.5 * x + rng.gauss(0, 1))
        out = {"notes": [], "direction": "positive"}
        assert diary_model.evaluate(None, {}, table, xs, ys, out) is True
        return out

    few = run(10)
    assert "p" not in few and any("zu wenige Daten" in n and "10 Personen" in n for n in few["notes"])
    enough = run(30)
    assert enough.get("p") is not None and enough["multilevel"]["persons"] == 30
