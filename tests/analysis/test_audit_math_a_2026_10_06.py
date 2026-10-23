"""Math audit 2026-10-06, package A: dual response and exports (W-1, W-2, W-5)."""
import re
from types import SimpleNamespace

import numpy as np
import pytest

from app import cbc_analysis, choice_predict
from app.choice_model import summarise
from app.extensions import db
from app.models import Item
from tests.conftest import CONSENT_OK
from tests.analysis import test_acbc
from tests.analysis import test_dual_response

LEVELS = (3, 3, 2, 4)        # attribute 4 = price
PRICES = [10.0, 20.0, 30.0, 40.0]
PW = [np.array([0.8, 0.0, -0.8]), np.array([0.5, 0.1, -0.6]), np.array([0.4, -0.4])]
BP = -0.05
ATTRS = [{"label": f"A{a}", "code": f"a{a}", "level_labels": [f"a{a}{lv}" for lv in range(n)]}
         for a, n in enumerate(LEVELS)]


class Kind:
    """What cbc_analysis.choice_sets asks of a CBC with dual response (as the audit's script wahl/common.py)."""

    def __init__(self, design):
        self.design = design

    def design_current(self, item): return True
    def version_known(self, item, v): return True
    def dual(self, item): return True
    def profile(self, item, v, t, a): return list(self.design[v][t][a])
    def price_attribute(self, item): return 3
    def level_numbers(self, item, a): return PRICES if a == 3 else None
    def level_counts(self, item): return list(LEVELS)
    def attributes(self, item, lang=None): return ATTRS


def _utility(profile):
    return sum(PW[a][profile[a]] for a in range(3)) + BP * (PRICES[profile[3]] - np.mean(PRICES))


def _simulate(J, c0, N=1500, T=10, versions=20, seed=1, follow=1.0):
    """The random utility model of dual response: the forced choice is argmax over the options (Gumbel noise), then
    "would choose it" if that option's utility, with the same noise, beats "none" (c0 plus noise of its own)."""
    rng = np.random.default_rng(seed)
    design = [[[tuple(int(rng.integers(n)) for n in LEVELS) for _ in range(J)] for _ in range(T)]
              for _ in range(versions)]
    responses = []
    for n in range(N):
        v = n % versions
        ans = {}
        for t in range(T):
            U = np.array([_utility(p) for p in design[v][t]]) + rng.gumbel(size=J)
            ch = int(U.argmax())
            ans[f"t{t}"] = ch + 1
            if rng.random() < follow:
                ans[f"dt{t}"] = int(U[ch] > c0 + rng.gumbel())
        responses.append(SimpleNamespace(layout={"cj": {"1": {"version": v}}}, answers={"1": ans}))
    item = SimpleNamespace(id=1, config={"tasks": T, "alternatives": J, "none": False, "dual": True}, kind=Kind(design))
    return item, responses, design


@pytest.mark.parametrize("J", [2, 3, 4])
def test_w1_dual_response_constant_and_simulator_hit_the_observed_no_share(J):
    """W-1: coded as one choice with "none", the constant comes out at its true value (the binary coding of before gave
    -0.10 / -0.44 / -0.76 for J = 2 / 3 / 4 with true 0.5), the price is no longer flattened (-0.042 before), and the
    share of "none" the simulator computes for the tasks of the design is the observed share of "no" (before about
    half of it)."""
    item, responses, design = _simulate(J, 0.5)
    sets, coding, persons, prices = cbc_analysis.choice_sets(item, responses)
    model = summarise(sets, coding, ATTRS, persons, prices, constant="„Keine davon“")
    asked = [r.answers["1"][f"dt{t}"] for r in responses for t in range(10)]
    observed = 1 - float(np.mean(asked))
    assert abs(model["asc"] - 0.5) < 0.12
    assert abs(model["price"]["beta"] - BP) < 0.006
    # the score equation of the constant: over the sets with "none" the predicted share equals the observed one
    full = [s for s in sets if s.X[-1][coding.asc] == 1]
    assert len(full) == len(asked)
    beta = np.concatenate([[lv["beta"] for lv in a["levels"]][:-1] for a in model["attributes"]]
                          + [[model["asc"], model["price"]["beta"] * coding.price_scale]])
    predicted = np.mean([(lambda e: e[-1] / e.sum())(np.exp(s.X @ beta)) for s in full])
    assert abs(predicted - observed) < 1e-4
    # the market simulator's rule (choice_predict) on the tasks of the design, "none" offered next to the options
    pooled = choice_predict.pooled_model(model, [a["code"] for a in ATTRS], float(np.mean(PRICES)))
    share = np.mean([choice_predict.shares(choice_predict.task_utilities(
        pooled, [list(p) for p in task], [PRICES[p[3]] for p in task], with_none=True))[-1]
        for version in design for task in version])
    assert abs(share - observed) < 0.01


def test_w2_follow_up_never_answered_gives_no_constant():
    """W-2: dual response without a single answered follow-up question: no constant (it was 0.0 by the ridge bound
    without a word, and the whole model ran with the ridge), the part-worths are estimated as without dual response,
    and the page says why there is no share of "none"."""
    item, responses, _ = _simulate(3, 0.5, N=200, T=8, follow=0.0)
    sets, coding, persons, _ = cbc_analysis.choice_sets(item, responses)
    assert coding.asc is None and len(sets) == 200 * 8 and all(len(s.X) == 3 for s in sets)
    model = cbc_analysis.estimate(item, responses, "de")
    assert model["asc"] is None and model["asc_se"] is None and not model["ridge"]
    assert any("Nachfrage (Dual Response) wurde nie beantwortet" in str(w) for w in model["warnings"])
    # one answered follow-up question is enough for the constant
    responses[0].answers["1"]["dt0"] = 0
    sets, coding, _, _ = cbc_analysis.choice_sets(item, responses)
    assert coding.asc is not None


def _participants(app, uid, n, follow):
    for k in range(n):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        for t in range(4):
            p.get(f"/s/{uid}")
            data = {"nav": "next", f"i2_t{t}": str(1 + (k + t) % 2)}
            if follow:
                data[f"i2_dt{t}"] = str(int((k + t) % 3 != 0))
            p.post(f"/s/{uid}", data=data)


def test_w2_simulator_offers_none_only_with_a_constant(app, client, survey):
    """W-2: the market simulator offers "None of these" with dual response only if the model has a constant."""
    test_dual_response.setup(client)
    uid = test_dual_response.start(app, client)
    _participants(app, uid, 12, follow=False)
    html = client.get("/surveys/1/results?sim=2&sim_none=1").get_data(as_text=True)
    assert 'id="sim-2"' in html and "Nachfrage (Dual Response) wurde nie beantwortet" in html
    assert 'type="checkbox" name="sim_none"' not in html and not re.search(r'res-bar-label">\s*Keine davon', html)
    _participants(app, uid, 12, follow=True)
    html = client.get("/surveys/1/results?sim=2&sim_none=1").get_data(as_text=True)
    assert 'type="checkbox" name="sim_none"' in html and "wurde nie beantwortet" not in html
    assert re.search(r'res-bar-label">\s*Keine davon', html)


def test_w1_dummy_follow_up_uses_the_noise_of_the_first_choice(app, client, survey, monkeypatch):
    """W-1: the dummy's follow-up answer compares the chosen option with its noise of the first choice to "none"
    (before: the utility without that noise plus a fresh draw)."""
    import random

    from app.items import conjoint

    test_dual_response.setup(client)
    draws = iter([3.0, 0.0, -5.0, 0.0])  # option 1 wins clearly by its noise; "none" draws -5
    monkeypatch.setattr(conjoint, "gumbel", lambda rng: next(draws))
    # all part-worths and "none" 0: only the noise decides, whatever design was drawn (R6: the profiles come from a
    # random design, and their utilities could outweigh the noise depending on the tests run before)
    monkeypatch.setattr(conjoint, "dummy_person", lambda kind, item, rng, respondent, prices=None: {
        "pw": [[0.0] * n for n in kind.level_counts(item)], "none": 0.0, "price": 0.0})
    with app.app_context():
        item = db.session.get(Item, 2)
        fields = item.kind.dummy_fields(item, random.Random(1), {"task": 0, "respondent": 0,
                                                                  "cj_state": {2: {"version": 0}}})
    assert fields["i2_t0"] == "1" and fields["i2_dt0"] == "1"


def test_w5_compute_version_includes_numpy_and_python(monkeypatch):
    """W-5: the version both machines must share includes the numpy version (random streams, linear algebra)."""
    import numpy

    from app import compute

    monkeypatch.setattr(compute, "_version", None)
    before = compute.version()
    monkeypatch.setattr(compute, "_version", None)
    monkeypatch.setattr(numpy, "__version__", "0.0.0")
    assert compute.version() != before


def test_w1_methods_text_names_the_coding():
    from app import methods

    entry = {"code": "cbc", "type": "conjoint", "levels": [3, 3, 2, 4], "price": True, "tasks": 10, "alternatives": 3,
             "versions": 4, "none": False, "dual": True, "n": 300, "robust": True, "hb": None, "holdouts": 0,
             "segments": None}
    assert "die Nachfrage ging so in das Modell ein" in methods._choice_de(entry)
    assert "dual response coded as \"would choose it\"" in methods._choice_en(entry)
    entry["dual"] = False
    assert "Nachfrage ging" not in methods._choice_de(entry) and "dual response coded" not in methods._choice_en(entry)
