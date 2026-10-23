"""CVA per person: the median R² and flat answers (audit 2026-10-23, M5, M6)."""
import itertools
import statistics
from types import SimpleNamespace

from app import cva_analysis as cva


def _rows(levels, rating):
    return [([1.0] + cva.effects_row(list(combo), levels), rating(combo))
            for combo in itertools.product(*(range(n) for n in levels))]


def test_flat_answers_have_zero_part_worths_and_no_importances():
    """M6: the same rating everywhere is no preference: part-worths exactly 0, importances missing."""
    levels = [3, 3, 2]
    fit = cva.fit_person(_rows(levels, lambda c: 4.0)[:14], levels)
    assert fit["flat"] and fit["r2"] is None
    assert fit["pw"] == [[0.0] * 3, [0.0] * 3, [0.0] * 2]
    assert fit["importance"] == [None, None, None]


def test_importances_of_rounding_noise_are_missing():
    """M6: ranges of ~1e-16 are noise, not a split of importance."""
    assert cva.importances([[1e-17, -1e-17], [3e-17, -3e-17]]) == [None, None]
    assert cva.importances([[1.0, -1.0], [0.5, -0.5]]) == [2 / 3, 1 / 3]


def test_median_r2_of_an_even_number_of_persons(monkeypatch):
    """M5: the median of [0.2, 1.0] is 0.6, not the upper middle value 1.0."""
    levels = [2, 2]
    fits = iter([{"constant": 0.0, "pw": [[0.5, -0.5], [0.2, -0.2]], "importance": [0.7, 0.3], "r2": r2, "n": 5,
                  "flat": False} for r2 in (1.0, 0.2)])
    monkeypatch.setattr(cva, "rows_of", lambda item, answer, versions=None: [([1.0], 1.0)])
    monkeypatch.setattr(cva, "fit_person", lambda rows, lv: next(fits))
    monkeypatch.setattr(cva, "holdout_view", lambda *a: None)
    import app.choice_model as cm
    monkeypatch.setattr(cm, "charts", lambda model: [])
    kind = SimpleNamespace(level_counts=lambda item: levels, form=lambda item: "rating", levels_ordered=lambda item, a: False,
                           attributes=lambda item, lang: [{"label": "A", "code": "a", "level_labels": ["x", "y"]},
                                                          {"label": "B", "code": "b", "level_labels": ["x", "y"]}])
    item = SimpleNamespace(id=1, kind=kind, config={"design": {"versions": [[]]}})
    responses = [SimpleNamespace(answers={"1": {"v": 0}}, pid=p) for p in (1, 2)]
    model = cva.analyse(item, responses, "de")["model"]
    assert model["r2_median"] == statistics.median([0.2, 1.0]) == 0.6
