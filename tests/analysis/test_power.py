"""Power analysis (concept poweranalyse-konzept.md): app/power.py computes what the calculator in the browser computes
(app/static/samplesize.js); the study plan keeps an expected effect per hypothesis and shows what it needs (PA1)."""
import pytest
from werkzeug.datastructures import MultiDict

from app import design, methods_text, power, study_plan
from app.extensions import db
from app.models import Survey
from tests.analysis.test_samplesize import js, needs_node
from tests.analysis.test_study_plan import _plan, _questions


def test_needed_numbers_reference_values():
    """Independently computed reference values over the noncentral t, F and χ² distributions (Cohen, 1988; two-sided,
    α = .05, power .80)."""
    assert power.needed("r", 0.3) == 84 and power.needed("r", 0.2) == 193 and power.needed("r", 0.5) == 29
    assert power.needed("t", 0.5) == 128 and power.needed("t", 0.8) == 52 and power.needed("t", 0.2) == 788
    assert power.needed("anova", 0.25, groups=3) == 159 and power.needed("anova", 0.25, groups=4) == 180
    assert power.needed("chi2", 0.3, df=1) == 88 and power.needed("chi2", 0.3, df=4) == 133
    assert power.needed("t", 0.5, power=0.9) == 172 and power.needed("r", 0.3, power=0.95) == 138
    # rank methods: each person counts as 3/π persons of the parametric test (A.R.E. 3/π, Lehmann, 2006: 67 per group)
    assert power.needed("u", 0.5) == 134 and power.needed("kw", 0.25, groups=3) > 159
    # Spearman: n − 3 times 1 + r²/2 (Bonett & Wright, 2000)
    assert power.needed("rho", 0.3) == 88                   # 3 + ⌈81 · 1.045⌉
    assert power.needed("describe", 0.3) is None and power.needed("r", None) is None and power.needed("r", 1.5) is None


@needs_node
def test_python_and_browser_give_the_same_numbers():
    cases = [(0.3, 0.8), (0.15, 0.9), (0.45, 0.95)]
    got = js(*[["nCorrelation", r, 0.05, p, False] for r, p in cases], *[["nTwoGroups", d, 0.05, p, False, 1]
                                                                          for d, p in [(0.35, 0.8), (0.6, 0.9)]])
    assert got[:3] == [power.needed("r", r, p) for r, p in cases]
    assert got[3:] == [power.needed("t", 0.35) // 2, power.needed("t", 0.6, 0.9) // 2]
    assert js(["corrPower", 109, 0.2, 0.05, False])[0] == pytest.approx(power.corr_power(109, 0.2), abs=1e-6)


def test_detectable_effect_and_power_at_are_the_reverse():
    assert power.detectable("r", 109) == pytest.approx(0.2644, abs=1e-3)
    assert power.detectable("t", [64, 64]) == pytest.approx(0.5, abs=0.01)
    assert power.detectable("anova", [53, 53, 53]) == pytest.approx(0.25, abs=0.01)
    assert power.detectable("chi2", 88) == pytest.approx(0.3, abs=0.01)
    assert power.power_at("r", 0.2, 109) == pytest.approx(0.554, abs=0.002)
    assert power.power_at("t", 0.5, [64, 64]) == pytest.approx(0.80, abs=0.01)
    assert power.detectable("t", [1, 50]) is None and power.detectable("r", 3) is None


def _power_plan(client, **extra):
    return _plan(client, h_effect=["0,5", ""], h_basis=["sesoi", ""], h_basis_note=["kleiner wäre belanglos", ""],
                 h_power=["0.8", "0.8"], **extra)


def test_plan_keeps_the_expected_effect_and_shows_what_it_needs(app, client, survey):
    _questions(client, app)
    _power_plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert (h["effect"], h["basis"], h["basis_note"], h["power"]) == (0.5, "sesoi", "kleiner wäre belanglos", 0.8)
        p = study_plan.power_of(s, h)
        assert (p["analysis"], p["measure"], p["n"]) == ("t", "d", 128)
        ps = study_plan.power_summary(s)
        assert (ps["n"], ps["by"], ps["exclusions"], ps["completions"]) == (128, [1], 10, 143)
        # the target of 120 is too small for the plan: the pre-launch check says so
        assert any("Poweranalyse" in f.message and "143" in f.message for f in design.check(s))
        md = study_plan.markdown(s, "de")
        assert "Poweranalyse: d = 0,50 (kleinster relevanter Effekt: kleiner wäre belanglos), Power 80 %, α = ,05 zweiseitig, N = 128" in md
        assert "- Poweranalyse: größtes N = 128 (H1); mit 10 % erwarteten Ausschlüssen 143 Abschlüsse" in md
        assert "power analysis: d = 0.50 (smallest effect of interest" in study_plan.markdown(s, "en")
        text = " ".join(" ".join(p) for _, p in methods_text.sections(s, None, None, None, "de", n=0))
        assert "mit einer Poweranalyse bestimmt" in text and "Maßgeblich war H1 mit N = 128" in text
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Nötig: <strong>128</strong> verwertbare Teilnahmen" in page and "Poweranalyse aus den Hypothesen:" in page
    assert 'data-plan-take-target="143"' in page and 'value="0,5"' in page
    assert "/sample-size/?survey=1&amp;design=groups&amp;power=0.8&amp;effect=0.5" in page
    # the target taken over: no warning and no button
    _power_plan(client, sample_target="143", sample_exclusions="10")
    with app.app_context():
        assert not any("Poweranalyse" in f.message for f in design.check(db.session.get(Survey, 1)))
    assert "data-plan-take-target" not in client.get("/surveys/1/researchdesign").data.decode()


def test_a_form_without_the_power_fields_still_saves_every_hypothesis(app, client, survey):
    _questions(client, app)
    _plan(client)                                         # as the wizard and older pages send it
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(study_plan.plan_of(s)["hypotheses"]) == 2 and study_plan.power_summary(s) is None


def test_rule_of_thumb_and_pilot_get_their_hint(app, client, survey):
    _questions(client, app)
    _plan(client, h_effect=["0.8", ""], h_basis=["cohen", ""], h_power=["0.95", ""])
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "die schwächste Begründung" in page and 'value="0.95" selected' in page
    _plan(client, h_effect=["0.8", ""], h_basis=["pilot", ""])
    assert "Albers &amp; Lakens, 2018" in client.get("/surveys/1/researchdesign").data.decode()


def test_calculator_opens_with_the_values_of_a_hypothesis(client):
    html = client.get("/sample-size/?design=anova&f=0.4&groups=4&power=0.9").data.decode()
    # effect sizes in the reader's decimal format (German: comma), typed into a text field
    assert 'value="anova" selected' in html and 'value="0,4" data-ss="f"' in html and 'value="4" data-ss="groups"' in html
    assert 'value="0.9" selected' in html
    assert 'value="0,25" data-ss="f"' in client.get("/sample-size/?f=99").data.decode()   # out of range: the default


def test_survey_file_keeps_the_power_analysis():
    plan = study_plan.clean({"hypotheses": [{"text": "H", "effect": "0,3", "basis": "literature", "power": 0.9,
                                             "basis_note": "Metaanalyse"}], "sample": {"exclusions": 15}})
    h = plan["hypotheses"][0]
    assert (h["effect"], h["basis"], h["power"], h["basis_note"]) == (0.3, "literature", 0.9, "Metaanalyse")
    assert plan["sample"]["exclusions"] == 15
    assert "effect" not in study_plan.clean({"hypotheses": [{"text": "H", "effect": "viel"}]})["hypotheses"][0]


@needs_node
def test_power_curve_of_the_calculator():
    """PA3: the curve over the completions for the chosen effect and Cohen's three, with the points of the result
    and of 90 %; with "erreichbaren Effekt zeigen" over the effect; none for the planners without a test."""
    base = {"alpha": 0.05, "power": 0.8, "ratio": 1, "dropout": 10}
    groups, effect, share = js(["curve", "groups", {**base, "effect": 0.5}],
                               ["curve", "corr", {**base, "effect": 0.3, "mode": "effect", "avail": 109}],
                               ["curve", "prop", {"p": 50, "margin": 5, "conf": 0.95}])
    assert groups["mode"] == "n" and [s["label"] for s in groups["series"]] == ["d = 0,50", "d = 0,20", "d = 0,80"]
    assert [m["x"] for m in groups["marks"]] == [128, 172] and groups["x"] == [0, 300] and groups["step"] == 100
    assert all(0 <= p[1] <= 1 for s in groups["series"] for p in s["pts"])
    assert effect["mode"] == "effect" and effect["marks"][0]["x"] == pytest.approx(0.2644, abs=1e-3)
    assert share is None


def test_calculator_page_has_the_curve(client):
    html = client.get("/sample-size/").data.decode()
    assert "data-ss-curve hidden" in html and "data-ss-curve-png" in html and "Power nach Zahl der Teilnahmen" in html


def test_results_say_what_the_test_could_detect(app, client, survey):
    """PA2: the sensitivity of each tested hypothesis with its group sizes, against the planned effect; no
    "observed power"."""
    from app import hypotheses

    _questions(client, app)
    _power_plan(client)                                                 # H1: t-test, d = 0.5 planned, 80 %
    with app.app_context():
        s = db.session.get(Survey, 1)
        noise = [{"gruppe": "1" if i % 2 else "2", "minuten": str(30 + (i * 7) % 11)} for i in range(80)]
        h1 = hypotheses.evaluate(s, noise)[0]
        sens = h1["sensitivity"]
        assert h1["verdict"] == "nicht unterstützt" and sens["measure"] == "d"
        assert sens["detectable"] == pytest.approx(power.detectable("t", [40, 40]), abs=1e-6)
        assert sens["planned"] == 0.5 and sens["enough"] is False and 0.5 < sens["planned_power"] < 0.8
        assert sens["text"].startswith("Mit 40 und 40 Personen findet der Test Effekte ab δ* = 0,63 mit 80 % Power")  # δ* (B-24)
        assert "nur 60 % Power" in sens["text"] and "besonders vorsichtig" in sens["text"]
        big = [{"gruppe": "1" if i % 2 else "2", "minuten": str(30 + (i * 7) % 11)} for i in range(300)]
        assert hypotheses.evaluate(s, big)[0]["sensitivity"]["enough"] is True
        en = hypotheses.evaluate(s, noise, "en")[0]["sensitivity"]["text"]
        assert en.startswith("With 40 and 40 people the test finds effects from δ* = 0.63")
        assert "observed" not in sens["text"]
    from app import dummy

    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 30, seed=1)
        db.session.commit()
    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert "Was der Test erkennen konnte" in page and "hyp-sensitivity" in page


def test_data_collection_against_the_power_analysis(app, client, survey):
    """PA4: usable participations of the data pool against the number of the plan, what is missing at the share
    left out, the smallest group of a group comparison; no test result."""
    from app import dummy

    _questions(client, app)
    _power_plan(client)                                                  # H1 needs 128, 64 per group
    with app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, 40, seed=3)
        s.max_responses = 50
        db.session.commit()
        pr = study_plan.progress(s)
        assert pr["completed"] == pr["usable"] + pr["marked"] and pr["need"] == 128 and not pr["only_real"]
        assert pr["missing"] == 128 - pr["usable"] and pr["more"] >= pr["missing"]
        assert pr["end"] == pr["completed"] + pr["more"]                      # the end after 50 is too early
        [row] = pr["rows"]
        assert row["per_group"] and row["need"] == 64 and row["have"] <= pr["usable"] // 2 + 1
    assert 'data-power-progress="/surveys/1/researchdesign/state"' in client.get("/surveys/1/field").data.decode()
    part = client.get("/surveys/1/researchdesign/state").data.decode()
    assert "Stand gegen die Poweranalyse" in part and "nötig 64 je Gruppe" in part and "auf " in part
    assert "Simmons et al., 2011" in part and "p = " not in part and "Dummy-Daten" in part


def test_holm_correction_plans_with_alpha_over_k_and_judges_by_the_adjusted_p(app, client, survey):
    """PA5: the tick plans every tested hypothesis with α / k and the results follow Holm's adjusted p."""
    from app import hypotheses

    _questions(client, app)
    two = dict(h_id=["", ""], h_text=["A pendelt länger", "B pendelt kürzer"], h_kind=["difference", "difference"],
               h_dir=["", ""], h_iv=["gruppe", "gruppe"], h_dv=["minuten", "minuten"], h_analysis=["", ""],
               h_effect=["0.5", "0.5"], h_basis=["", ""], h_basis_note=["", ""], h_power=["0.8", "0.8"])
    _plan(client, **two)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.alpha_for(s) == 0.05 and study_plan.power_summary(s)["n"] == 128
    _plan(client, holm="1", **two)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["holm"] and study_plan.alpha_for(s) == 0.025
        assert study_plan.power_summary(s)["n"] == 2 * power.smallest(
            lambda n: power.t_power(2 * n - 2, 0.5 * (n / 2) ** 0.5, 0.025), 0.8, 2)
        assert study_plan.alpha_text(0.025) == ",025" and study_plan.alpha_text(0.05, "en") == ".05"
        assert study_plan.alpha_text(0.05 / 3) == ",017"
        assert "Holm (1979); jede Hypothese mit α = ,025 geplant" in study_plan.markdown(s, "de")
        # p = .03 and .04: without the tick both supported, with Holm .06 and .06: neither
        found = [{"p": 0.03, "kind": "difference", "verdict": "unterstützt", "notes": []},
                 {"p": 0.04, "kind": "difference", "verdict": "unterstützt", "notes": []}]
        hypotheses.holm(found, 2)
        assert [r["p_holm"] for r in found] == [0.06, 0.06] and {r["verdict"] for r in found} == {"nicht unterstützt"}
        found = [{"p": 0.001, "kind": "difference", "verdict": "unterstützt", "notes": []},
                 {"p": 0.04, "kind": "difference", "verdict": "unterstützt", "notes": []}]
        hypotheses.holm(found, 2)
        assert [r["verdict"] for r in found] == ["unterstützt", "unterstützt"] and found[0]["p_holm"] == 0.002
        table = [{"gruppe": "1", "minuten": str(40 + i % 5)} for i in range(40)] + \
                [{"gruppe": "2", "minuten": str(20 + i % 5)} for i in range(40)]
        res = hypotheses.evaluate(s, table)
        # p = 1 after Holm keeps its decimal comma (Ralf's screenshot 2026-10-17: "p_Holm = 1.000")
        assert hypotheses._p_lang(1.0) == "p = 1,000" and hypotheses._p_lang(1.0, "en") == "p = 1.000"
        assert all(r["p_holm_text"].startswith("p<sub>Holm</sub> ") for r in res) and "α = ,025" in res[0]["sensitivity"]["text"]
        text = " ".join(" ".join(p) for _, p in methods_text.sections(s, None, None, None, "de", n=0))
        assert "nach Holm (1979) korrigiert" in text and "nicht korrigiert" not in text
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'name="holm" value="1" checked' in page and "α = 0,025 zweiseitig" not in page and "α = ,025 zweiseitig" in page


def test_expected_effect_from_an_earlier_survey(app, client, survey):
    """PA6: another survey of the person with the same two variables gives the effect and the lower end of its 60 %
    interval (safeguard power); "übernehmen" sets it as the expected effect with the survey as the reason."""
    from app import dummy, power_pilot
    from app.models import Response

    _questions(client, app)
    _power_plan(client)
    client.post("/surveys/1/duplicate", data={"title": "Vorstudie", "with_people": "1"})
    with app.app_context():
        earlier = db.session.scalars(db.select(Survey).filter(Survey.title == "Vorstudie")).one()
        sid = earlier.id
        dummy.generate(earlier, 60, seed=5)
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=sid)):
            r.source = "real"                                     # as if collected for real
        db.session.commit()
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        found = power_pilot.estimate(db.session.get(Survey, sid), h, "t")
        assert found and found["n"] >= 10 and 0 <= found["safeguard"] <= found["effect"]
        assert power_pilot.estimate(s, h, "t") is None              # no real data in the survey itself
        [(other, est)] = power_pilot.candidates(s, h, __import__("app.models", fromlist=["User"]).User.query.first())
        assert other.id == sid and est == found
    page = client.get(f"/surveys/1/researchdesign/pilot?h={h['id']}").data.decode()
    assert "Vorstudie" in page and "Safeguard" in page and "Perugini, M., Gallucci, M., &amp; Costantini, G. (2014)" in page
    assert f"/surveys/1/researchdesign/pilot?h={h['id']}" in client.get("/surveys/1/researchdesign").data.decode()
    client.post("/surveys/1/researchdesign/pilot", data={"h": h["id"], "source": sid, "which": "point"})
    with app.app_context():
        h = study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"][0]
        assert h["effect"] == round(found["effect"], 3) and h["basis"] == "pilot"
        assert h["basis_note"].startswith("Befragung „Vorstudie“ (n = ") and h["basis_note"].endswith("Punktschätzer")
    assert client.post("/surveys/1/researchdesign/pilot", data={"h": h["id"], "source": 1, "which": "point"}).status_code == 404


def test_safeguard_values_by_hand():
    """The lower ends of the 60 % intervals: Fisher z for r; inverted noncentral t, F and chi-square."""
    from app import power_pilot as pp

    import math
    r = pp._correlation([float(i) for i in range(50)], [float(i % 7 + i / 10) for i in range(50)], False)
    assert r["safeguard"] == pytest.approx(math.tanh(math.atanh(r["effect"]) - pp.Z20 / math.sqrt(47)))
    two = pp._two_groups({"a": [1.0, 2, 3, 4, 5] * 6, "b": [2.0, 3, 4, 5, 6] * 6})
    assert two["effect"] == pytest.approx(1 / math.sqrt(60 / 29), rel=1e-9) and 0 < two["safeguard"] < two["effect"]
    nothing = pp._two_groups({"a": [1.0, 2, 3] * 10, "b": [1.0, 2, 3] * 10})
    assert nothing["effect"] == 0 and nothing["safeguard"] == 0
    three = pp._anova({"a": [1.0, 2, 3] * 10, "b": [2.0, 3, 4] * 10, "c": [3.0, 4, 5] * 10})
    assert 0 < three["safeguard"] < three["effect"]
    chi = pp._chi2({"a": ["x"] * 30 + ["y"] * 10, "b": ["x"] * 15 + ["y"] * 25})
    assert 0 < chi["safeguard"] < chi["effect"]


class _Kind:
    def __init__(self, levels=None, n=None):
        self.levels, self.n = levels, n

    def design_current(self, item):
        return True

    def level_counts(self, item):
        return self.levels

    def attributes(self, item, lang=None):
        return [{"label": f"M{i}", "level_labels": [f"s{j}" for j in range(n)]} for i, n in enumerate(self.levels)]

    def price_attribute(self, item):
        return None

    def n_items(self, item):
        return self.n

    def labels(self, item, lang=None):
        return [f"Aussage {i}" for i in range(self.n)]


def test_power_of_the_part_worths_from_the_design():
    """PA7: Var of each part-worth per person from the logit information of the design at 0 (de Bekker-Grob et al.,
    2015). A Monte Carlo run of this design (400 samples of 70 persons, β = 0.2 on the first part-worth, Newton MNL)
    gave power 0.80, as the formula says for N = 70."""
    from types import SimpleNamespace

    from app import power_choice
    from app.conjoint_design import generate
    from app.maxdiff_design import generate as md_generate

    levels = [3, 3, 2]
    design, _eff = generate(levels, 8, 2, 2, seed=1)
    item = SimpleNamespace(kind=_Kind(levels), config={"design": {"versions": design}, "alternatives": 2})
    cp = power_choice.conjoint(item)
    # every level, the last one of each attribute too (PA-2)
    assert cp["labels"] == ["M0: s0", "M0: s1", "M0: s2", "M1: s0", "M1: s1", "M1: s2", "M2: s0", "M2: s1"]
    assert len(cp["var"]) == 8
    assert power_choice.needed(max(cp["var"]), 0.2) == 70
    assert power_choice.needed(max(cp["var"]), 0.4) == 18 and power_choice.needed(max(cp["var"]), 0.2, 0.9) > 70
    single = SimpleNamespace(kind=_Kind([4, 4]), config={"design": {"versions": [[[[0, 0], [1, 1]]]]}, "alternatives": 2})
    assert power_choice.conjoint(single) is None                       # one task cannot separate six part-worths
    versions = md_generate(8, 4, 6, 2, seed=3)
    versions = versions[0] if isinstance(versions, tuple) else versions
    md = SimpleNamespace(kind=_Kind(n=8), config={"design": {"versions": versions}})
    mp = power_choice.maxdiff(md)
    assert mp["labels"][0] == "Aussage 0" and len(mp["var"]) == 8 and all(v > 0 for v in mp["var"])
    assert mp["labels"][-1] == "Aussage 7"                             # the reference statement too (PA-2)


def test_choice_power_for_everyone(app, client, survey):
    """No longer experimental since 2026-10-10: the block shows without any switch, without the badge."""
    from app import experimental
    from tests.analysis.test_conjoint import setup

    assert "choice_power" not in experimental.KEYS
    setup(client)
    html = client.get("/sample-size/?survey=1").data.decode()
    # 4 tasks of 2 alternatives cannot separate the 5 part-worths: the block says what is missing
    assert "data-ss-choice-power" in html and "data-ss-choice-data" not in html and "Erst mit einem aktuellen Versuchsplan" in html
    from tests.analysis.test_conjoint import E, conjoint_form

    form = conjoint_form()
    form.setlist("tasks", ["10"])
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    html = client.get("/sample-size/?survey=1").data.decode()
    assert "data-ss-choice-data" in html and "de Bekker-Grob, E. W." in html


def test_noncentrality_zero_gives_the_central_distributions():
    """Mathe-Audit 2026-10-06, PA-1: at noncentrality 0 the Poisson weights are 1, 0, 0, …, not 1/j! (which summed
    to e): the central t, F and chi-square, and the limit of a tiny noncentrality."""
    from app import power_pilot as pp
    from app.group_tests import chi2_sf

    assert power._ncf_sf(1.0, 2, 57, 0.0) == pytest.approx(0.3742, abs=1e-4)
    assert power._ncf_sf(2.0, 2, 117, 0.0) == pytest.approx(0.1399, abs=1e-4)
    assert power._nct_cdf(1.0, 38, 0.0) == pytest.approx(0.8382, abs=1e-4)
    assert power.chi2_power(100, 1, 0.0) == pytest.approx(0.05, abs=1e-6)
    assert pp._ncx2_sf(3.0, 2, 0.0) == pytest.approx(chi2_sf(3.0, 2), abs=1e-12)
    for got, near in [(power._ncf_sf(1.0, 2, 57, 0.0), power._ncf_sf(1.0, 2, 57, 1e-9)),
                      (power._nct_cdf(1.0, 38, 0.0), power._nct_cdf(1.0, 38, 1e-9)),
                      (pp._ncx2_sf(3.0, 2, 0.0), pp._ncx2_sf(3.0, 2, 1e-9))]:
        assert got == pytest.approx(near, abs=1e-6)
    # the f safeguard: 3 x 40 with a central p of about .14 lies below .20, the 60 % interval does not reach 0 (before:
    # the weights summed to about e, _ncf_sf(F, 2, 117, 0) was 1.0 and the safeguard 0)
    base = [1.0, 2, 3, 4, 5] * 8
    groups = {"a": base, "b": [x + 0.46 for x in base], "c": [x + 0.46 * 2 / 1.5 for x in base]}
    found = pp._anova(groups)
    assert found["safeguard"] == pytest.approx(0.0598, abs=1e-3) and found["safeguard"] < found["effect"]


def test_choice_power_counts_the_last_level_and_the_reference_statement():
    """PA-2: with exclusions the design is unbalanced and the last level (minus the sum, variance 1'C1) needs the
    most; the audit's case 3/3/4 levels, 6 tasks, 3 alternatives, 4 versions: 222 persons at δ = .2, not 115."""
    from types import SimpleNamespace

    import numpy as np

    from app import power_choice
    from app.conjoint_design import _encode, generate

    levels = [3, 3, 4]
    forbidden = [((0, 2), (2, 3)), ((0, 2), (1, 2)), ((1, 2), (2, 3))]
    design, _eff = generate(levels, 6, 3, 4, n_starts=5, seed=2, forbidden=forbidden)
    item = SimpleNamespace(kind=_Kind(levels), config={"design": {"versions": design}, "alternatives": 3})
    cp = power_choice.conjoint(item)
    assert len(cp["var"]) == 10 and cp["labels"][-1] == "M2: s3"
    info = np.zeros((7, 7))
    for version in design:
        for task in version:
            X = _encode(np.asarray(task), levels)
            d = X - X.mean(0)
            info += d.T @ d / 3
    cov = np.linalg.inv(info / len(design))
    assert cp["var"][-1] == pytest.approx(cov[4:7, 4:7].sum())
    worst = int(np.argmax(cp["var"]))
    assert cp["labels"][worst] == "M2: s3" and power_choice.needed(max(cp["var"]), 0.2) == 222


def test_choice_power_of_a_maxdiff_needs_a_current_design():
    """PA-3: a design made before a statement was removed points past the statements (IndexError, a 500 on the
    page): without a current design there is no number, as for the conjoint."""
    from types import SimpleNamespace

    from app import power_choice
    from app.maxdiff_design import generate

    versions = generate(8, 4, 8, 2, seed=1)
    versions = versions[0] if isinstance(versions, tuple) else versions
    kind = _Kind(n=7)
    kind.design_current = lambda item: False
    assert power_choice.maxdiff(SimpleNamespace(kind=kind, config={"design": {"versions": versions}})) is None


def test_progress_reached_only_when_every_hypothesis_has_its_number(app):
    """PA-4: 130 usable against 128 is not "reached" while the smallest group of H1 has 45 of 64."""
    from flask import render_template

    p = {"measure": "d", "effect": 0.5}
    row = {"n": 1, "p": p, "have": 45, "need": 64, "per_group": True, "groups": 2, "share": 45 / 64}
    pr = {"completed": 130, "marked": 0, "usable": 130, "need": 128, "missing": 0, "short": [row], "only_real": True,
          "rate": 0.0, "planned_rate": 0.1, "measured": True, "more": 0, "end": None, "rows": [row]}
    with app.test_request_context():
        html = render_template("surveys/_power_progress.html", survey=None, pr=pr)
        assert "Die Zahl der Poweranalyse ist erreicht" not in html
        assert "noch nicht für jede Hypothese" in html and "H1: kleinste Gruppe 45 von 64" in html
        assert "70 %" in html
        done = {**row, "have": 70, "share": 1.0}
        html = render_template("surveys/_power_progress.html", survey=None, pr={**pr, "short": [], "rows": [done]})
        assert "Die Zahl der Poweranalyse ist erreicht" in html


def test_calculator_opens_with_the_alpha_of_holm(client):
    """PA-5: with Holm the plan computes with α / k; "im Rechner öffnen" takes it along and the list of the calculator
    gets it as a choice of its own, so both show the same number (d = .5, k = 2: N = 156, not 128)."""
    from types import SimpleNamespace

    p = {"analysis": "t", "level": 0.8, "effect": 0.5, "groups": 2, "df": 1, "alpha": 0.025}
    assert study_plan.calculator_args(SimpleNamespace(id=1), p)["alpha"] == 0.025
    assert "alpha" not in study_plan.calculator_args(SimpleNamespace(id=1), {**p, "alpha": 0.05})
    html = client.get("/sample-size/?design=groups&effect=0.5&alpha=0.025").data.decode()
    assert '<option value="0.025" selected>,025 (Holm, 2 Hypothesen)</option>' in html
    assert '<option value="0.05" >' in html
    html = client.get("/sample-size/?design=groups&alpha=0.01").data.decode()
    assert "Holm, " not in html and '<option value="0.01" selected>' in html
    assert '<option value="0.05" selected>' in client.get("/sample-size/?alpha=0.5").data.decode()   # out of range


def test_spearman_safeguard_with_the_standard_error_of_bonett_and_wright():
    """PA-6: the Fisher z interval of Spearman's rho with SE sqrt((1 + r²/2)/(n - 3)), as the planning."""
    import math

    from app import power_pilot as pp

    xs = [float(i) for i in range(30)]
    ys = [float((i * 7) % 30) + i for i in range(30)]
    r = pp._correlation(xs, ys, True)
    rs = r["effect"]
    assert r["safeguard"] == pytest.approx(math.tanh(math.atanh(rs) - pp.Z20 * math.sqrt((1 + rs * rs / 2) / 27)))
    p = pp._correlation(xs, ys, False)
    assert p["safeguard"] == pytest.approx(math.tanh(math.atanh(p["effect"]) - pp.Z20 / math.sqrt(27)))
    # the audit's case: n = 30, r_s = .30 gives .143, not .146
    assert math.tanh(math.atanh(0.3) - pp.Z20 * math.sqrt(1.045 / 27)) == pytest.approx(0.143, abs=5e-4)


def test_power_below_the_aim_is_rounded_down(monkeypatch):
    """Z-10: 0.797 against the aim of 80 % reads "nur 79,7 %", not "nur 80 %"; .598 stays "60 %"."""
    from app import hypotheses

    monkeypatch.setattr(study_plan, "power_of", lambda survey, h: {"analysis": "t"})
    monkeypatch.setattr(power, "power_at", lambda *a, **k: 0.797)
    res = hypotheses.sensitivity(None, {"effect": 0.5}, {"tested": "t", "sizes": [30, 30]}, "de")
    assert res["enough"] is False and "nur 79,7 % Power" in res["text"]
