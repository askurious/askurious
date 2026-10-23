"""Time budget of the design searches (code review 2026-10-15, B1-B3): CBC designs (app/conjoint_design.py) and
vignette plans (app/doe.py, app/vignettes.py) stop with the best design so far, the editor says so, the searches
take a place among the heavy evaluations, and impossible starts give a text instead of an error page."""
import random

import numpy as np
import pytest

from app import conjoint_design as cd
from app import doe, heavy
from tests.analysis.test_conjoint import E, conjoint_form
from tests.analysis.test_vignettes import FORM, _add, _node
from tests.conftest import add, fill_consent


def _cut():
    return cd.Budget(1e-9)  # over at the first look


def test_rank_two_update_gives_the_log_det_of_the_changed_information():
    """The exchange scores a candidate with the determinant lemma; it must equal the full log det."""
    rng = np.random.default_rng(0)
    levels, J = [3, 4, 2], 3
    D = np.array([[rng.integers(n) for n in levels] for _ in range(12)])
    X = cd._encode(D, levels).astype(float)
    ridge = 1e-6 * np.eye(X.shape[1])
    info = cd._task_info(X, J).sum(axis=0)
    A, logdet = np.linalg.inv(info + ridge), np.linalg.slogdet(info + ridge)[1]
    i, j, lv = 4, 1, (D[4, 1] + 1) % 4
    cols = slice(2, 5)
    rows = slice(3, 6)
    u = X[i] - X[rows].mean(axis=0)
    delta = np.vstack([np.eye(3), -np.ones(3)])[lv] - X[i, cols]
    Au = A @ u
    uAd, dAd, uAu, c = Au[cols] @ delta, delta @ A[cols, cols] @ delta, u @ Au, 1 - 1 / J
    det2 = (1 + uAd / J) * (1 + (uAd + c * dAd) / J) - dAd * (uAu + c * uAd) / J ** 2
    Y = X.copy()
    Y[i, cols] += delta
    direct = np.linalg.slogdet(cd._task_info(Y, J).sum(axis=0) + ridge)[1]
    assert logdet + np.log(det2) == pytest.approx(direct, abs=1e-8)


def test_same_seed_same_design_and_fewer_starts_for_large_designs():
    a = cd.generate([3, 3, 2], 6, 3, 2, seed=5)
    b = cd.generate([3, 3, 2], 6, 3, 2, seed=5, budget=cd.Budget(60))
    assert a == b
    assert cd._starts([3, 3, 2], 36) == cd.N_STARTS
    assert cd._starts([5] * 8, 720) == cd.MIN_STARTS


def test_cbc_search_stops_with_a_valid_design_when_the_budget_is_over():
    budget = _cut()
    design, eff = cd.generate([4, 4, 3, 3], 8, 3, 3, seed=1, budget=budget)
    assert budget.cut and 0 <= eff <= 1
    assert len(design) == 3 and all(len(v) == 8 for v in design)
    assert all(len({tuple(a) for a in task}) == 3 for v in design for task in v)  # no identical alternatives
    assert not cd.Budget().over() and not cd.Budget(0).over()  # no limit


def test_vignette_searches_stop_with_valid_plans_when_the_budget_is_over():
    budget = _cut()
    D = doe.d_optimal([3, 3, 2, 4], 24, [((0, 0), (1, 1))], seed=1, budget=budget)
    assert budget.cut and len({tuple(r) for r in D}) == 24 and not any(r[0] == 0 and r[1] == 1 for r in D)
    decks = doe.blocks(D, [3, 3, 2, 4], 4, seed=1, budget=_cut())
    assert sorted(i for d in decks for i in d) == list(range(24)) and all(len(d) == 6 for d in decks)
    rows = doe.d_optimal_rows(doe.full_factorial([3, 3, 2]), [3, 3, 2], 10, seed=1, budget=_cut())
    assert len(set(rows)) == 10


def test_start_without_enough_profiles_says_so_instead_of_stopiteration():
    """B3: no list of all profiles and no unused one found: ValueError with a text, not StopIteration (500)."""
    with pytest.raises(ValueError, match="verschiedene erlaubte Vignetten"):
        doe._start([2, 2], 5, random.Random(1), lambda row: True, None)


def test_vignette_plan_refuses_too_many_vignettes(app, client, survey):
    _add(client)
    page = f"/surveys/1/flow/vignettes/{_node(app)['id']}"
    client.post(page, data={**FORM, "per_person": "20", "decks": "300"})
    html = client.post(page, data={"action": "generate"}, follow_redirects=True).get_data(as_text=True)
    assert "6000 Vignetten" in html and f"höchstens {doe.N_MAX}" in html
    assert _node(app)["rows"] == []


def _always_over(monkeypatch):
    """A budget that is over at once: DESIGN_SECONDS = 1e-9 alone depends on the clock's resolution (about 16 ms on
    Windows), so under load the search could finish before the limit is seen."""
    def over(self):
        self.cut = True
        return True

    monkeypatch.setattr(cd.Budget, "over", over)


def test_vignette_plan_cut_short_is_kept_with_a_warning(app, client, survey, monkeypatch):
    _add(client)
    page = f"/surveys/1/flow/vignettes/{_node(app)['id']}"
    client.post(page, data=FORM)
    app.config["DESIGN_SECONDS"] = 1e-9
    _always_over(monkeypatch)
    html = client.post(page, data={"action": "generate"}, follow_redirects=True).get_data(as_text=True)
    assert "Versuchsplan erzeugt" in html and "wurde nach" in html
    assert len(_node(app)["rows"]) == 12


def test_cbc_design_cut_short_is_kept_with_a_warning(app, client, survey, monkeypatch):
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=conjoint_form())
    app.config["DESIGN_SECONDS"] = 1e-9
    _always_over(monkeypatch)
    html = client.post(E + "/items/2/conjoint/design", follow_redirects=True).get_data(as_text=True)
    assert "D-Effizienz" in html and "Die Suche nach dem Versuchsplan wurde nach" in html


def test_design_searches_wait_for_a_heavy_place(app, client, survey, monkeypatch):
    fill_consent(client)
    add(client, "conjoint")
    client.post(E + "/items/2", data=conjoint_form())
    monkeypatch.setattr(heavy, "hold", lambda wait=None: False)
    assert client.post(E + "/items/2/conjoint/design").status_code == 503
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    assert client.post(page, data={"action": "generate"}).status_code == 503
    assert _node(app)["rows"] == []
