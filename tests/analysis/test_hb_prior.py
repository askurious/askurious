"""The prior on the spread between persons of the HB estimation (app/hb.py): the fixed inverse Wishart (default,
unchanged) and the hierarchical prior of Huang & Wand (2013), generalised to blocks; its choice per question on the
results page (maxdiff_hb.PRIOR_KEY) and what it does to the key of a stored result."""
import numpy as np
import pytest

from app.extensions import db
from app.models import Item
from tests.analysis.test_maxdiff_hb import person_sets, simulate, with_dummies


def _effects(level, L=3):
    v = np.zeros(L - 1)
    if level < L - 1:
        v[level] = 1
    else:
        v[:] = -1
    return v


def _cbc(n=200, tasks=10, seed=3):
    """Simulated CBC: 3 attributes x 3 levels (effects-coded) + "none" constant, 3 alternatives + none per task. The first
    attribute hardly varies between persons (variance 0.01 per level), the constant a lot (4). Returns the person sets,
    k, the prior covariance (as conjoint_hb._prior), the groups (as conjoint_hb._prior_groups) and the true diagonal."""
    from app.choice_model import ChoiceSet

    rng = np.random.default_rng(seed)
    k = 7
    P = np.eye(2) - 1 / 3
    Sigma = np.zeros((k, k))
    Sigma[0:2, 0:2] = 0.01 * P
    Sigma[2:4, 2:4] = P
    Sigma[4:6, 4:6] = P
    Sigma[6, 6] = 4.0
    mu = np.array([1.0, -0.5, 0.8, 0.0, -0.6, 0.3, 0.0])
    persons = []
    for b in rng.multivariate_normal(mu, Sigma, size=n):
        sets = []
        for _ in range(tasks):
            rows = [np.concatenate([_effects(rng.integers(3)) for _ in range(3)] + [[0.0]]) for _ in range(3)]
            X = np.array(rows + [np.r_[np.zeros(6), 1.0]])
            sets.append(ChoiceSet(X, int(np.argmax(X @ b + rng.gumbel(size=4)))))
        persons.append(sets)
    groups = [np.array([0, 1]), np.array([2, 3]), np.array([4, 5]), np.array([6])]
    prior = np.eye(k)
    for g in groups[:3]:
        prior[np.ix_(g, g)] = P
    return persons, k, prior, groups, np.diag(Sigma)


def test_default_prior_is_the_fixed_one_and_unchanged():
    from app import hb

    versions, answers, _, _ = simulate(30, seed=2)
    sets, k, _ = person_sets(versions, answers)
    kw = dict(burn=200, draws=200, thin=2, seed=5, prior_cov=np.eye(k) + 1, keep=0)
    default, iw = hb.estimate(sets, k, **kw), hb.estimate(sets, k, prior="iw", **kw)
    assert np.array_equal(default["beta"], iw["beta"]) and np.array_equal(default["mu"], iw["mu"])
    assert np.array_equal(default["sigma_diag"], iw["sigma_diag"])
    # the settings of a result with the default: the same keys as before the option, no "prior", no "prior_a"
    assert default["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 5, "prior_df": k + 5, "target": hb.TARGET}
    assert "prior_a" not in default
    with pytest.raises(ValueError):
        hb.estimate(sets, k, prior="other", **kw)


def test_hierarchical_prior_reproducible_and_sigma_positive_definite(monkeypatch):
    from app import hb

    versions, answers, _, _ = simulate(30, seed=2)
    sets, k, _ = person_sets(versions, answers)
    kw = dict(burn=200, draws=200, thin=2, seed=5, prior_cov=np.eye(k) + 1, keep=0, prior="hw",
              prior_groups=[np.arange(k)])
    smallest = []
    draw = hb._inv_wishart

    def watched(rng, df, scale):
        Sigma = draw(rng, df, scale)
        smallest.append(np.linalg.eigvalsh((Sigma + Sigma.T) / 2).min())
        return Sigma

    monkeypatch.setattr(hb, "_inv_wishart", watched)
    a, b = hb.estimate(sets, k, **kw), hb.estimate(sets, k, **kw)
    assert np.array_equal(a["beta"], b["beta"]) and np.array_equal(a["prior_a"], b["prior_a"])
    assert len(smallest) == 2 * 400 and min(smallest) > 0
    assert a["settings"]["prior"] == "hw" and a["settings"]["prior_scale"] == hb.HW_SCALE
    assert a["settings"]["prior_df"] == hb.HW_NU + k - 1 and a["prior_a"].shape == (1,) and a["prior_a"][0] > 0
    other = hb.estimate(sets, k, **{**kw, "seed": 6})
    assert not np.array_equal(a["beta"], other["beta"])


def test_conditional_of_a_is_the_step_of_huang_wand():
    """For groups of one parameter with P_g = 1: a_j | Sigma ~ IG((nu + k) / 2, nu (Sigma^-1)_jj + 1 / A^2)."""
    from app import hb

    k = 4
    rng = np.random.default_rng(1)
    L = rng.normal(size=(k, k))
    Si = L @ L.T + np.eye(k)
    blocks = hb.hw_blocks(np.eye(k))
    shapes, rates = hb.hw_conditional(Si, blocks, hb.hw_df(k))
    assert np.allclose(shapes, (hb.HW_NU + k) / 2)
    assert np.allclose(rates, hb.HW_NU * np.diag(Si) + 1 / hb.HW_SCALE ** 2)
    # a block: m_g df / 2 + 1/2 and nu trace(P_g Si_gg) + 1/A^2
    P = np.eye(2) - 1 / 3
    cov = np.eye(k)
    cov[:2, :2] = P
    shapes, rates = hb.hw_conditional(Si, hb.hw_blocks(cov, [[0, 1], [2], [3]]), hb.hw_df(k), scale=2.0)
    assert shapes[0] == pytest.approx(0.5 + 2 * hb.hw_df(k) / 2)
    assert rates[0] == pytest.approx(hb.HW_NU * np.trace(P @ Si[:2, :2]) + 1 / 4)
    # the scale of Sigma | a: 2 nu blockdiag(P_g / a_g)
    S = hb.hw_scale(hb.hw_blocks(cov, [[0, 1], [2], [3]]), [2.0, 4.0, 1.0], k)
    assert np.allclose(S[:2, :2], 2 * hb.HW_NU * P / 2) and S[2, 2] == pytest.approx(1.0) and S[0, 2] == 0


def test_groups_must_cover_a_block_diagonal_prior():
    from app import hb

    with pytest.raises(ValueError):
        hb.hw_blocks(np.eye(3), [[0, 1]])                       # parameter 2 in no group
    with pytest.raises(ValueError):
        hb.hw_blocks(np.eye(3) + 1, [[0, 1], [2]])              # I + 11' couples 2 with the others
    assert len(hb.hw_blocks(np.eye(3) + 1, [[0, 1, 2]])) == 1


def test_conjoint_groups_follow_the_prior_blocks():
    from app import hb
    from app.choice_model import Coding
    from app.conjoint_hb import _prior, _prior_groups

    coding = Coding([3, 4, 2, 5], price_scale=10.0, asc=True, linear=(3,))
    groups = _prior_groups(coding)
    blocks = hb.hw_blocks(_prior(coding), groups)                # block diagonal along the groups, every parameter once
    assert [len(g) for g, _ in blocks] == [2, 3, 1, 1, 1]        # three attributes, the constant, the price
    assert np.allclose(blocks[1][1], np.eye(3) - 1 / 4)


def test_hierarchical_prior_recovers_a_small_and_a_large_spread():
    """Recovery on simulated CBC data (200 persons, 10 tasks): the fixed prior pulls the tiny spread of the first
    attribute (true variance 0.0067 per parameter) far up towards its prior mean; the hierarchical prior keeps it small,
    and for the large spread of the constant (4) it is not worse."""
    from app import hb

    persons, k, prior, groups, truth = _cbc()
    kw = dict(burn=1000, draws=2000, thin=10, seed=1, prior_cov=prior, keep=0)
    iw = hb.estimate(persons, k, **kw)["sigma_diag"]
    hw = hb.estimate(persons, k, prior="hw", prior_groups=groups, **kw)["sigma_diag"]
    tiny_iw, tiny_hw = iw[:2].mean(), hw[:2].mean()
    assert tiny_hw < 0.08 and tiny_hw < tiny_iw / 3
    assert abs(hw[6] - truth[6]) <= abs(iw[6] - truth[6]) + 0.5


# --- the choice on the results page --------------------------------------------------------------------------------

def test_choice_on_the_results_page_and_the_key(app, client, survey):
    with_dummies(app, client, n=20)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert 'name="prior"' in html and "Priori der Streuung zwischen Personen" in html and "fest (Standard)" in html
    assert "Warum? Feste, hierarchische oder weite Priori?" in html
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        from app import maxdiff_hb, segments, simulator

        item = db.session.get(Item, 2)
        first = maxdiff_hb.load(item)
        # the default: the settings dict and so the key of the result exactly as before the option
        assert first["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 1, "chains": 4}
        assert "hb_prior" not in item.config
        table = maxdiff_hb.pool_table(item.survey, first["pool"])
        lc_key = segments.model_input(item, table)["key"] if segments.model_input(item, table) else None
        config_before = simulator.without_sim(item.config)
    # switching the field: saved and estimated anew, the result says which prior
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "hw"})
    with app.app_context():
        item = db.session.get(Item, 2)
        second = maxdiff_hb.load(item)
        assert item.config["hb_prior"] == "hw" and maxdiff_hb.prior_of(item) == "hw"
        assert second["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 1, "chains": 4, "prior": "hw"}
        assert second["key"] != first["key"] and second["data_key"] == first["data_key"]
        assert maxdiff_hb.current(item) is not None
        # a setting of the results page: neither the questionnaire nor the latent classes see it
        assert simulator.without_sim(item.config) == config_before
        table = maxdiff_hb.pool_table(item.survey, second["pool"])
        assert (segments.model_input(item, table)["key"] if segments.model_input(item, table) else None) == lc_key
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert '<option value="hw" selected>' in html and "Priori der Streuung hierarchisch (Huang &amp; Wand, 2013)" in html
    assert "Huang, A., &amp; Wand, M. P. (2013)" in html                    # in the sources of the page
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    assert "hierarchische Priori der Streuung (Huang & Wand, 2013)" in next(
        line for line in book.splitlines() if line.startswith("md_hb_prob1"))
    # back to the default: the key of the first result again, nothing stored in the question
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "iw"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "hb_prior" not in item.config and maxdiff_hb.load(item)["key"] == first["key"]
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    assert "Priori" not in next(line for line in book.splitlines() if line.startswith("md_hb_prob1"))
    # an unknown value changes nothing
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "xyz"})
    with app.app_context():
        assert "hb_prior" not in db.session.get(Item, 2).config


def test_stored_default_result_goes_stale_when_the_prior_changes(app, client, survey):
    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        assert maxdiff_hb.current(item) is not None
        maxdiff_hb.choose_prior(item, "hw")                      # chosen, not estimated yet
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None


# --- the wide prior (scale = degrees of freedom x prior covariance) ---------------------------------------------------

def test_wide_scale_is_degrees_of_freedom_times_prior_cov():
    from app import hb

    cov = np.eye(3) + 1
    assert np.allclose(hb.fixed_scale(cov, 8), (8 - 3 - 1) * cov)              # the default: prior mean = cov
    assert np.allclose(hb.fixed_scale(cov, 8, wide=True), 8 * cov)            # wide: prior mean 8/4 = 2 x cov


def test_wide_prior_runs_reproducibly_and_default_stays():
    from app import hb

    versions, answers, _, _ = simulate(30, seed=2)
    sets, k, _ = person_sets(versions, answers)
    kw = dict(burn=200, draws=200, thin=2, seed=5, prior_cov=np.eye(k) + 1, keep=0)
    a, b = hb.estimate(sets, k, prior="wide", **kw), hb.estimate(sets, k, prior="wide", **kw)
    assert np.array_equal(a["beta"], b["beta"]) and np.array_equal(a["sigma_diag"], b["sigma_diag"])
    assert a["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 5, "prior_df": k + 5, "target": hb.TARGET,
                             "prior": "wide"}
    assert "prior_a" not in a
    default = hb.estimate(sets, k, **kw)
    assert "prior" not in default["settings"] and not np.array_equal(default["beta"], a["beta"])


def test_wide_prior_gives_a_larger_spread_between_persons():
    """On the simulated CBC data every diagonal element of Sigma comes out larger than with the default prior (its prior
    mean is (k + 5) / 4 = 3 times larger here, k = 7)."""
    from app import hb

    persons, k, prior, _, _ = _cbc()
    kw = dict(burn=1000, draws=2000, thin=10, seed=1, prior_cov=prior, keep=0)
    iw = hb.estimate(persons, k, **kw)["sigma_diag"]
    wide = hb.estimate(persons, k, prior="wide", **kw)["sigma_diag"]
    assert np.all(wide > iw)


def test_wide_choice_on_the_results_page(app, client, survey):
    with_dummies(app, client, n=20)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert '<option value="iw" selected>' in html and '<option value="hw">' in html and '<option value="wide">' in html
    assert html.index('value="iw"') < html.index('value="hw"') < html.index('value="wide"')
    assert "Warum? Feste, hierarchische oder weite Priori?" in html and "Rossi et al., 2005" in html
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        from app import maxdiff_hb

        first = maxdiff_hb.load(db.session.get(Item, 2))
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "wide"})
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        wide = maxdiff_hb.load(item)
        assert item.config["hb_prior"] == "wide" and maxdiff_hb.prior_of(item) == "wide"
        assert wide["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 1, "chains": 4, "prior": "wide"}
        assert wide["key"] != first["key"] and maxdiff_hb.current(item) is not None
        # a result with another prior is not current for this choice
        maxdiff_hb.choose_prior(item, "hw")
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None
        maxdiff_hb.choose_prior(db.session.get(Item, 2), "wide")
        db.session.commit()
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert '<option value="wide" selected>' in html and "Priori der Streuung weit (Rossi et al., 2005)" in html
    assert "Hein, M., Kurz, P., &amp; Steiner, W. J. (2019)" in html
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    assert "weite Priori der Streuung" in next(line for line in book.splitlines() if line.startswith("md_hb_prob1"))
    # a value outside the keys changes nothing; back to the default stores nothing
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "weit"})
    with app.app_context():
        assert db.session.get(Item, 2).config["hb_prior"] == "wide"
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50", "prior": "iw"})
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        assert "hb_prior" not in item.config and maxdiff_hb.load(item)["key"] == first["key"]


def _rich_cbc(n=100, tasks=24, attributes=6, seed=7):
    """Simulated choices with real spread between persons and many parameters (6 x 4 levels + none, k = 19): the data
    on which the hierarchical prior used to collapse from its start (2026-11-24)."""
    from app.choice_model import ChoiceSet

    rng = np.random.default_rng(seed)
    L = 4
    k = attributes * (L - 1) + 1
    cov, groups = np.eye(k), []
    for a in range(attributes):
        cov[a * 3:(a + 1) * 3, a * 3:(a + 1) * 3] = np.eye(L - 1) - 1 / L
        groups.append(np.arange(a * 3, (a + 1) * 3))
    groups.append(np.array([k - 1]))

    def level(v):
        return np.eye(L - 1)[v] if v < L - 1 else -np.ones(L - 1)

    persons = []
    for b in rng.multivariate_normal(rng.normal(0, 1, k), 0.8 * cov, size=n):
        sets = []
        for _ in range(tasks):
            X = np.array([np.concatenate([level(rng.integers(L)) for _ in range(attributes)] + [[0.0]]) for _ in range(3)]
                         + [np.r_[np.zeros(k - 1), 1.0]])
            sets.append(ChoiceSet(X, int(np.argmax(X @ b + rng.gumbel(size=4)))))
        persons.append(sets)
    return persons, k, cov, groups


def test_hierarchical_prior_does_not_collapse_from_its_start():
    """All betas start at mu (no spread), and with the hierarchical scale from the first iteration Sigma | D = 0 fell to
    about S0 / N, a grew, S0 shrank further: Sigma ended near 0 (min. diagonal below 0.03, mean 0.07 to 0.12 at
    these seeds, true variance 0.6 per level parameter, 0.8 for the constant), each start elsewhere. With the warm-up of
    hb.HW_WARM under the fixed prior the spread stays and the starts agree."""
    from app import hb

    persons, k, cov, groups = _rich_cbc()
    means = []
    for seed in (1, 2, 3):
        r = hb.estimate(persons, k, burn=1000, draws=1000, thin=5, seed=seed, prior_cov=cov, keep=0, prior="hw",
                        prior_groups=groups)
        assert r["sigma_diag"].min() > 0.1, (seed, r["sigma_diag"])
        means.append(r["sigma_diag"].mean())
    assert min(means) > 0.3 and max(means) / min(means) < 1.5, means


def test_prior_options_name_their_source(app, client, survey):
    """The options of the field cite their source in APA style, author and year (Ralf, 2026-11-24); the default
    names none, since its expectation is a Festlegung of Askurious."""
    with_dummies(app, client, n=12)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "fest (Standard)</option>" in html
    assert "hierarchisch (Huang &amp; Wand, 2013)</option>" in html and "weit (Rossi et al., 2005)</option>" in html
