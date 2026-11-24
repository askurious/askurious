"""Interactions between attributes of a CBC or ACBC (app/conjoint_interactions.py, 2026-11-24): the search with the
simple and the HB-based test, Holm, the confirmation on held-out tasks, the fixed parameters of the HB sampler
(app/hb.py `fixed`) and the pair taken into the HB model (app/conjoint_hb.py) with page, export and report."""
import csv
import io
import math
import random
import time

import numpy as np
import pytest

from app import conjoint_interactions as ww
from app import hb
from app.choice_model import ChoiceSet, Coding
from app.extensions import db
from app.models import Item, Response, Survey, new_pid

FAST = {"burn": 300, "draws": 600, "thin": 3, "seed": 1}


def simulate(strength, persons=200, tasks=8, seed=1, forbidden=None, coding=None):
    """Persons choosing among 3 concepts of 4 attributes with 3 levels each; strength: the interaction of attributes 0
    and 1 (+strength on the diagonal, -strength/2 off it, rows and columns summing to 0). forbidden: a pair of levels
    of attributes 0 and 1 never shown."""
    rng = np.random.default_rng(seed)
    coding = coding or Coding([3, 3, 3, 3], None, asc=False)
    W = strength * np.array([[1, -0.5, -0.5], [-0.5, 1, -0.5], [-0.5, -0.5, 1]])
    mu = np.array([0.8, 0.0, -0.5, 0.4, 0.3, -0.3, 0.6, 0.0])
    people = []
    for _ in range(persons):
        b = mu + rng.normal(0, 0.6, coding.k)
        sets = []
        for _ in range(tasks):
            profiles = rng.integers(0, 3, size=(3, 4))
            if forbidden is not None:
                for pr in profiles:
                    while (pr[0], pr[1]) == forbidden:
                        pr[1] = rng.integers(0, 3)
            X = np.array([coding.row(list(pr)) for pr in profiles])
            u = X @ b + np.array([W[pr[0], pr[1]] for pr in profiles]) + rng.gumbel(size=3)
            sets.append(ChoiceSet(X, int(np.argmax(u))))
        people.append(sets)
    return coding, people, W


def test_interaction_columns_and_codes():
    coding = Coding([3, 2, 3], None, asc=True)
    x = coding.row([2, 0, 1])                                       # last level of attribute 0: -1, -1
    z = ww.interaction(x, coding, 0, 2)
    assert z.tolist() == [-0.0, -1.0, -0.0, -1.0]                 # (-1, -1) x (0, 1)
    assert ww.interaction(coding.asc_row(), coding, 0, 2).tolist() == [0.0] * 4   # no interaction with "none"
    C = ww.cell_matrix(coding, 0, 2)
    W = (C @ np.array([0.3, -0.1, 0.2, 0.4])).reshape(3, 3)
    assert np.allclose(W.sum(axis=0), 0) and np.allclose(W.sum(axis=1), 0)       # effects coding: rows, columns sum 0
    assert ww.pairs_of(coding) == [(0, 1), (0, 2), (1, 2)]


def test_linear_price_is_one_column():
    coding = Coding([3, 3, 2], price_scale=40.0, asc=False, linear=(1,), price_center=100.0)
    assert ww.attribute_columns(coding, 1) == [coding.price]
    pairs = ww.pairs_of(coding)
    assert (0, 1) in pairs and (1, 2) in pairs
    x = coding.row([1, 0, 0], price=140.0)
    assert ww.interaction(x, coding, 0, 1).tolist() == [0.0, 1.0]           # (0, 1) x (140 - 100) / 40
    assert ww.interaction(x, coding, 1, 2).shape == (1,)                    # (Lother - 1) columns
    assert ww.pairs_of(coding, linear_price=False) == [(0, 2)]               # ACBC: the summed price is no attribute
    C = ww.cell_matrix(coding, 0, 1, [60.0, 100.0, 140.0])
    assert C.shape == (9, 2) and np.allclose(C[2], [1.0, 0.0])              # level 0, price 140: (1, 0) x 1


def test_holm():
    adjusted = ww.holm([0.01, 0.04, 0.03, None])
    assert adjusted[:3] == pytest.approx([0.03, 0.06, 0.06]) and adjusted[3] is None
    assert ww.holm([0.5, 0.9]) == [1.0, 1.0]


def test_hb_without_fixed_is_unchanged_and_with_fixed_reproducible():
    """Without `fixed` the sampler is the one of before: no other step, no other random number, the same settings
    (compared bit for bit with a run of the code before this change outside the suite; here: None and [] give the
    same chain and the settings keep their keys). With fixed parameters the same seed gives the same chain."""
    coding, people, _ = simulate(0.5, persons=40, tasks=6, seed=3)
    args = dict(burn=200, draws=200, thin=2, seed=4, keep=10)
    a = hb.estimate(people, coding.k, **args)
    b = hb.estimate(people, coding.k, fixed=[], **args)
    assert set(a["settings"]) == {"burn", "draws", "thin", "seed", "prior_df", "target"}
    assert np.array_equal(a["mu_trace"], b["mu_trace"]) and np.array_equal(a["beta"], b["beta"])
    assert "fixed" not in a
    sets = ww.with_pairs_people(people, coding, [(0, 1)])
    c = hb.estimate(sets, coding.k + 4, fixed=[8, 9, 10, 11], fixed_prior=ww.prior_block(coding, 0, 1), **args)
    d = hb.estimate(sets, coding.k + 4, fixed=[8, 9, 10, 11], fixed_prior=ww.prior_block(coding, 0, 1), **args)
    assert np.array_equal(c["fixed_trace"], d["fixed_trace"]) and np.array_equal(c["beta"], d["beta"])
    assert c["beta"].shape == (40, coding.k) and c["fixed_draws"].shape == (10, 4) and c["settings"]["fixed"] == 4


def test_true_interaction_is_found_confirmed_and_recovered():
    from app import conjoint_hb

    started = time.process_time()
    coding, people, W = simulate(0.8, persons=200, seed=1)
    rows = ww.search(people, coding, ww.pairs_of(coding))
    top = rows[0]
    assert (top["a"], top["b"]) == (0, 1) and top["p_holm"] < 0.001 and top["gain"] > 0.5
    assert all(r["p_holm"] > 0.05 for r in rows[1:])
    assert top["min_cell"] > 30 and np.array(top["counts"]).sum() == 200 * 8 * 3
    cells = np.array(top["cells"]["w"])
    assert np.corrcoef(cells.ravel(), W.ravel())[0, 1] > 0.9
    conf = ww.confirm(people, coding, [(0, 1)], FAST, conjoint_hb._prior(coding), {}, 3)
    c = conf[(0, 1)]
    assert c["confirmed"] and c["better"] >= 2 and c["mean"] > 0
    # the fixed HB parameter recovers the true interaction within its interval (another sample: in the one above the
    # sampling error puts one of the four values just past the edge, as one in twenty may)
    coding, people, W = simulate(0.8, persons=200, seed=2)
    sets = ww.with_pairs_people(people, coding, [(0, 1)])
    r = hb.estimate(sets, coding.k + 4, fixed=[8, 9, 10, 11], fixed_prior=ww.prior_block(coding, 0, 1), burn=400,
                    draws=800, thin=4, seed=2)
    truth = np.array([W[0, 0], W[0, 1], W[1, 0], W[1, 1]])        # the effects parameters are the first cells
    assert np.all(r["fixed_lo"] < truth) and np.all(truth < r["fixed_hi"])
    assert time.process_time() - started < 240


def _inp(coding, people, hb_beta=None, holdouts=None):
    return {"key": "k", "data_key": "d", "type": "conjoint",
            "coding": {"levels": coding.levels, "price_scale": coding.price_scale, "asc": coding.asc is not None,
                       "linear": [a for a in range(len(coding.levels)) if a not in coding.effects],
                       "price_center": coding.price_center},
            "persons": [{"pid": f"p{n}", "sets": [(s.X.tolist(), s.chosen) for s in sets]} for n, sets in enumerate(people)],
            "pairs": [list(p) for p in ww.pairs_of(coding)], "price_levels": None, "hb_beta": hb_beta,
            "holdouts": holdouts, "splits": 3, "settings": FAST}


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_no_interaction_no_confirmed_pair(seed):
    coding, people, _ = simulate(0.0, persons=150, tasks=8, seed=seed)
    res = ww.run(_inp(coding, people))
    assert not any(r["status"] == "confirmed" for r in res["rows"])
    assert all(r["status"] in ("none", "notable") for r in res["rows"])


def test_prohibited_combination_is_not_estimable():
    coding, people, _ = simulate(0.0, persons=120, tasks=6, seed=5, forbidden=(0, 0))
    res = ww.run(_inp(coding, people))
    row = next(r for r in res["rows"] if (r["a"], r["b"]) == (0, 1))
    assert row["status"] == "na" and not row["estimable"] and row["counts"][0][0] == 0
    assert res["rows"][-1] is row                                   # sorted last


def test_after_hb_test_uses_the_persons_utilities():
    coding, people, _ = simulate(0.8, persons=120, tasks=8, seed=7)
    beta = hb.estimate(people, coding.k, burn=200, draws=200, thin=2, seed=1)["beta"]
    rows = ww.search(people, coding, ww.pairs_of(coding), hb_beta=beta)
    top = rows[0]
    assert (top["a"], top["b"]) == (0, 1) and top["hb"]["estimable"] and top["hb"]["p"] < 0.001
    assert top["cells"]["basis"] == "hb" and top["hb"]["wald_p"] < 0.01


def test_confirmation_on_holdout_tasks():
    """With holdout tasks they are the held-out choices: one division, keyed by the position of the person (no
    participation numbers in the input of the compute node)."""
    coding, people, _ = simulate(0.9, persons=150, tasks=10, seed=21)
    holdouts = [[n, [(s.X.tolist(), s.chosen) for s in sets[-2:]]] for n, sets in enumerate(people)]
    train = [sets[:-2] for sets in people]
    res = ww.run(_inp(coding, train, holdouts=holdouts))
    assert res["confirm"] == {"mode": "holdouts", "splits": 1, "needed": 1}
    top = res["rows"][0]
    assert (top["a"], top["b"]) == (0, 1) and top["status"] == "confirmed" and len(top["confirm"]["delta"]) == 1


# --- "jede Aufgabe einmal" (leave-one-task-out) and the suggested wording (2026-11-24) ------------------------------

def test_every_task_is_held_out_exactly_once_with_uneven_task_counts():
    coding, people, _ = simulate(0.0, persons=4, tasks=8, seed=31)
    people = [people[0], people[1][:5], people[2][:1], people[3][:3]]          # 8, 5, 1 and 3 tasks
    parts, grouped = ww.task_folds(people, coding)
    assert len(parts) == 8 and not grouped and ww.fold_count(people, coding) == (8, False)
    for n, sets in enumerate(people):
        held = [id(s) for _, test in parts for s in test[n]]
        if len(sets) < 2:
            assert held == [] and all(train[n] == sets for train, _ in parts)   # one task: always in training
            continue
        assert sorted(held) == sorted(id(s) for s in sets)                    # every choice held out exactly once
        for train, test in parts:
            assert len(train[n]) + len(test[n]) == len(sets) and not set(map(id, train[n])) & set(map(id, test[n]))
    # more than 12 tasks: the positions modulo 10, still every task once
    coding, many, _ = simulate(0.0, persons=2, tasks=15, seed=32)
    parts, grouped = ww.task_folds(many, coding)
    assert grouped and len(parts) == ww.GROUPED_FOLDS
    assert sorted(id(s) for _, test in parts for s in test[0]) == sorted(id(s) for s in many[0])
    assert [len(test[0]) for _, test in parts] == [2] * 5 + [1] * 5


def test_paired_t_test_over_persons():
    t = ww.paired([0.2, 0.1, 0.3, 0.0, 0.4])
    assert t["n"] == 5 and t["df"] == 4 and t["mean"] == pytest.approx(0.2)
    assert t["t"] == pytest.approx(0.2 / (math.sqrt(0.025) / math.sqrt(5)), rel=1e-3) and t["p"] < 0.05
    assert ww.paired([0.1])["p"] is None and ww.paired([0.1, 0.1])["p"] is None   # one person, no spread


@pytest.mark.parametrize("strength, confirmed", [(0.9, True), (0.0, False)])
def test_every_task_once_confirms_a_true_interaction_only(strength, confirmed):
    from app import conjoint_hb

    coding, people, _ = simulate(strength, persons=150, tasks=6, seed=41)
    c = ww.confirm(people, coding, [(0, 1)], FAST, conjoint_hb._prior(coding), {}, ww.TASKS)[(0, 1)]
    assert c["mode"] == "tasks" and len(c["delta"]) == 6 and c["needed"] is None and not c["grouped"]
    t = c["persons"]
    assert set(t) == {"n", "mean", "sd", "t", "df", "p"} and t["n"] == 150    # aggregates only, no value per person
    assert c["confirmed"] is confirmed
    assert (t["mean"] > 0 and t["p"] < 0.05) is confirmed
    assert sum(c["choices"]) == 150 * 6                                       # every choice predicted exactly once


def _row(status, **extra):
    row = {"a_label": "Kommunikator", "b_label": "Vehemenz", "a_levels": ["Expertin", "Laie", "Betroffene"],
           "b_levels": ["Empfehlung", "Vorschreibung", "Verbot"], "status": status, "estimable": status != "na",
           "p_holm": 0.0123, "simple": {"estimable": True, "lr": 30.1, "df": 4},
           "hb": {"estimable": True, "lr": 41.237, "df": 4},
           "cells": {"w": [[0.05, 0.32, -0.37], [0.0, -0.1, 0.1], [-0.05, -0.22, 0.27]],
                     "lo": [[-0.1, 0.19, -0.5], [-0.1, -0.2, -0.05], [-0.2, -0.4, 0.1]],
                     "hi": [[0.2, 0.44, -0.25], [0.1, 0.05, 0.2], [0.1, 0.01, 0.4]]}}
    row.update(extra)
    return row


SPLIT = {"mode": "split", "splits": 3, "needed": 2}


def test_suggested_wording_for_every_status_in_both_languages():
    c = {"delta": [3.1, 2.0, -0.4], "mean": 1.5667, "better": 2}
    de = ww.suggestion(_row("confirmed", confirm=c), SPLIT, False)
    assert de == ("Zwischen Kommunikator und Vehemenz zeigte sich eine Wechselwirkung (χ²(4) = 41,24, p (Holm) = ,012), "
                  "die auch die Vorhersage zurückgehaltener Wahlen verbesserte (2 von 3 Aufteilungen, Δ LL = +1,6). Sie "
                  "geht vor allem auf die Kombinationen Expertin × Verbot (-0,37 [-0,50; -0,25]) und Expertin × "
                  "Vorschreibung (+0,32 [0,19; 0,44]) zurück.")
    en = ww.suggestion(_row("confirmed", confirm=c), SPLIT, True)
    assert en.startswith("An interaction between Kommunikator and Vehemenz emerged (χ²(4) = 41.24, p (Holm) = .012)")
    assert "(2 of 3 divisions, Δ LL = +1.6)" in en and "It stems mainly from the combinations Expertin × Verbot" in en
    c = {"delta": [1.0, -2.0, -0.5], "mean": -0.5, "better": 1}
    assert ww.suggestion(_row("notable", confirm=c), SPLIT, False) == (
        "Es gibt Hinweise auf eine Wechselwirkung zwischen Kommunikator und Vehemenz (p (Holm) = ,012), die sich in der "
        "Kreuzvalidierung jedoch nicht als vorhersagerelevant erwies (1 von 3 Aufteilungen besser, Δ LL = -0,5).")
    assert ww.suggestion(_row("notable", confirm=c), SPLIT, True) == (
        "There are indications of an interaction between Kommunikator and Vehemenz (p (Holm) = .012), which, however, "
        "did not prove relevant for prediction in the cross-validation (better in 1 of 3 divisions, Δ LL = -0.5).")
    assert ww.suggestion(_row("none", p_holm=0.0004), SPLIT, False) == (
        "Zwischen Kommunikator und Vehemenz fand sich kein Hinweis auf eine Wechselwirkung (p (Holm) < ,001).")
    assert ww.suggestion(_row("none", p_holm=0.31), SPLIT, True) == (
        "No indication of an interaction between Kommunikator and Vehemenz was found (p (Holm) = .310).")
    assert ww.suggestion(_row("na"), SPLIT, False) == (
        "Die Wechselwirkung zwischen Kommunikator und Vehemenz ließ sich wegen des Versuchsplans nicht schätzen.")
    assert ww.suggestion(_row("na"), SPLIT, True) == (
        "The interaction between Kommunikator and Vehemenz could not be estimated because of the experimental design.")
    # every task once: the paired test over persons instead of "x von n Aufteilungen"
    c = {"mode": "tasks", "grouped": False, "delta": [0.5] * 12, "mean": 0.0412, "better": 9,
         "persons": {"n": 367, "mean": 0.0412, "sd": 0.27, "t": 2.9234, "df": 366, "p": 0.0018}}
    tasks = {"mode": "tasks", "splits": 12, "needed": None}
    assert ("(Kreuzvalidierung über alle 12 Aufgaben (jede Aufgabe einmal zurückgehalten): Δ LL = +0,04 je Person im "
            "Mittel, t(366) = 2,92, p = ,002)") in ww.suggestion(_row("confirmed", confirm=c), tasks, False)
    assert ("(cross-validation over all 12 tasks (each task held out once): Δ LL = +0.04 per person on average, "
            "t(366) = 2.92, p = .002)") in ww.suggestion(_row("confirmed", confirm=c), tasks, True)
    # holdout tasks
    c = {"mode": "holdouts", "delta": [-1.2], "mean": -1.2, "better": 0}
    assert "an den Holdout-Aufgaben jedoch nicht" in ww.suggestion(_row("notable", confirm=c), {"mode": "holdouts"}, False)
