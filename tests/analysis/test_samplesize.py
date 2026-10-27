import json
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from tests.conftest import add


def test_calculator_page_and_menu_link(app, client, survey):
    html = client.get("/sample-size/").data.decode()
    assert "Stichprobengröße" in html and 'data-ss-design' in html and "Als Ziel übernehmen" not in html
    assert 'href="/sample-size/"' in client.get("/dashboard/", follow_redirects=True).data.decode()


def test_decimal_fields_take_a_comma(client):
    """User test 2026-10-27: a number field takes no decimal comma, "0,3" became nothing or d = 3. Decimal fields are
    text fields with the decimal keyboard and show their value with a comma in German."""
    import re

    html = client.get("/sample-size/?design=anova&f=0.4").data.decode()
    fields = re.findall(r'<input[^>]*data-ss="(?:effect|f|w|r2|alpha0|rho|icc)"[^>]*>', html)
    assert len(fields) == 7 and all('type="text" inputmode="decimal"' in f for f in fields)
    assert 'value="0,4" data-ss="f"' in html and 'value="0,5" data-ss="effect"' in html
    assert not re.search(r'type="number"[^>]*step="0', html)


def test_linked_from_status_with_conjoint_values_and_apply(app, client, survey):
    add(client, "conjoint")                                         # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "cbc"), ("tasks", "10"), ("alternatives", "3")]))
    settings = client.get("/surveys/1/field").data.decode()
    # a link with the survey's values in the status box of a draft (UI audit 2026-10-04, question 8; F24 had none)
    assert 'href="/sample-size/?survey=1">Stichprobengröße berechnen' in settings
    html = client.get("/sample-size/?survey=1").data.decode()
    assert "Als Ziel übernehmen" in html and 'value="conjoint" selected' in html and "cbc" in html
    back = client.get("/surveys/1/field?max_responses=250").data.decode()
    assert 'name="max_responses" min="1" value="250"' in back and "Aus dem Stichprobenrechner übernommen" in back


def test_links_to_gpower(client):
    html = client.get("/sample-size/").data.decode()
    assert html.count('href="https://www.psychologie.hhu.de/arbeitsgruppen/allgemeine-psychologie-und-arbeitspsychologie/gpower" target="_blank"') == 1
    assert 'href="https://doi.org/10.3758/BF03193146"' in html
    # every rule of the page cited in full in one closed fold (N-9, C-4); the factor 1.5 is said to be our own
    assert html.count('<details class="sources">') == 1 and "Johnson, R. M., &amp; Orme, B. K." in html and "Cochran, W. G. (1977)" in html
    assert "Orme, B. K. (2010)" in html and "Johnson, R. M., &amp; Orme, B. K. (1996)" in html and "Festlegung von Askurious" in html


def test_tasks_needed_is_the_rule_solved_for_the_tasks():
    from app.conjoint_design import sample_size, tasks_needed

    assert tasks_needed(250, 2, 3) == (3, 5) and tasks_needed(100, 3, 4) == (7, 10)   # 500·c/(n·J), 1.5 times
    assert tasks_needed(10000, 6, 2) == (1, 1) and tasks_needed(0, 2, 3) == (0, 0)
    for tasks, alts, levels in [(8, 2, 3), (12, 3, 5), (5, 4, 7), (30, 6, 2)]:      # round trip with sample_size()
        n_min, n_rec = sample_size(tasks, alts, levels)
        assert tasks_needed(n_min, alts, levels)[0] <= tasks and tasks_needed(n_rec, alts, levels)[1] <= tasks


def test_conjoint_editor_shows_tasks_for_a_planned_sample(app, client, survey):
    from tests.analysis.test_conjoint import conjoint_form

    add(client, "conjoint")                                         # item 2
    client.post("/surveys/1/editor/items/2", data=conjoint_form(display="table", visual=()))
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-cj-reverse" in html and "Aufgaben ≥ 500 · c / (n · Alternativen)" in html and "Orme (2010" in html
    assert html.count("<tr data-alts=") == 5 and "eingestellt, mit" in html


def test_sets_needed_shows_every_statement_500_times():
    from app.maxdiff_design import sets_needed

    assert sets_needed(200, 20, 4) == (13, 25) and sets_needed(300, 8, 4) == (4, 7)   # 500·K/(n·k), 1000·K/(n·k)
    assert sets_needed(5000, 8, 4) == (1, 1) and sets_needed(0, 8, 4) == (0, 0)
    for n, K, k in [(200, 20, 4), (75, 12, 3), (300, 30, 5)]:
        t_min, t_good = sets_needed(n, K, k)
        assert n * t_min * k / K >= 500 > n * (t_min - 1) * k / K and n * t_good * k / K >= 1000


def test_maxdiff_editor_shows_sets_for_a_planned_sample(app, client, survey):
    from tests.analysis.test_maxdiff import maxdiff_form

    add(client, "maxdiff")                                          # item 2
    client.post("/surveys/1/editor/items/2", data=maxdiff_form())    # 8 statements, 4 per set
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-cj-reverse data-levels="8" data-targets="500,1000"' in html and "(Orme, 2010)" in html
    assert "Sets ≥ 500 · K / (n · Set-Größe)" in html and html.count("<tr data-alts=") == 4   # set sizes 3 to 6
    assert "eingestellt, mit 6 Sets" in html and 'value="300" data-cj-reverse-n' in html


# --- the mathematics of the page (app/static/samplesize.js, run under Node) -----------------------------------

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/samplesize.js")


def js(*calls):
    script = Path(__file__).parents[1].joinpath("samplesize_cases.js")
    done = subprocess.run([NODE, str(script)], input=json.dumps(calls).encode(), capture_output=True, check=True, timeout=120)
    return json.loads(done.stdout.decode("utf-8"))


@needs_node
def test_t_tests_are_exact_reference_values():
    """Independently computed reference values (exact noncentral t); the normal approximation gave 5 instead of 6
    for d = 2 and 20 instead of 21 for d = 0.8 one-sided (one person too few)."""
    got = js(["tPower", 126, 0.5 * 32 ** 0.5, 0.05, False],
             ["nTwoGroups", 0.2, 0.05, 0.8, False], ["nTwoGroups", 0.5, 0.05, 0.8, False], ["nTwoGroups", 0.8, 0.05, 0.8, False],
             ["nTwoGroups", 0.5, 0.05, 0.8, True], ["nTwoGroups", 0.8, 0.05, 0.8, True], ["nTwoGroups", 2.0, 0.05, 0.8, False],
             ["nPaired", 0.2, 0.05, 0.8, False], ["nPaired", 0.5, 0.05, 0.8, False], ["nPaired", 0.8, 0.05, 0.8, False])
    assert abs(got[0] - 0.8015) < 1e-4
    assert got[1:] == [394, 64, 26, 51, 21, 6, 199, 34, 15]


@needs_node
def test_several_groups_are_planned_as_one_way_anova():
    """One-way ANOVA, omnibus test of fixed effects (Cohen, 1988), f = 0.25, α = 0.05, power 0.80: N = 128 (2 groups),
    159 (3), 180 (4). Before, three groups gave 64 × 3 = 192 (the pairwise t-test times the number of groups)."""
    got = js(["nAnova", 0.25, 2, 0.05, 0.8], ["nAnova", 0.25, 3, 0.05, 0.8], ["nAnova", 0.25, 4, 0.05, 0.8],
             ["anovaPower", 53, 3, 0.25, 0.05], ["anovaPower", 52, 3, 0.25, 0.05],
             ["plan", "anova", {"f": 0.25, "groups": 3, "alpha": 0.05, "power": 0.8, "dropout": 0}])
    assert got[:3] == [64, 53, 45]
    assert 0.80 <= got[3] < 0.81 and got[4] < 0.80
    plan = got[5]
    assert plan["ok"] and plan["total"] == 159 and plan["detail"].startswith("53 je Gruppe × 3 Gruppen, f = 0,25")


@needs_node
def test_paired_takes_d_and_the_correlation_of_the_measurements():
    """d_z = d / √(2(1 − ρ)) (Lakens 2013): d = 0.5 is d_z = 0.5 at ρ = 0.5 (34 persons), 0.645 at ρ = 0.7 (21) and
    0.395 at ρ = 0.2 (53)."""
    base = {"effect": 0.5, "alpha": 0.05, "power": 0.8, "onesided": False, "dropout": 0}
    got = js(["plan", "paired", {**base, "rho": 0.5}], ["plan", "paired", {**base, "rho": 0.7}],
             ["plan", "paired", {**base, "rho": 0.2}], ["plan", "paired", {**base, "rho": None}])
    assert [p["total"] for p in got[:3]] == [34, 21, 53]
    assert "ρ = 0,70 ergibt d_z = 0,65" in got[1]["detail"] and not got[3]["ok"]


@needs_node
def test_correlation_is_exact():
    """Exact distribution of r (bivariate normal model), α = 0.05 two-sided, power 0.80: r = 0.3 → 84, 0.5 → 29,
    0.7 → 13; the Fisher-z approximation gave one person more."""
    got = js(["nCorrelation", 0.3, 0.05, 0.8, False], ["nCorrelation", 0.5, 0.05, 0.8, False],
             ["nCorrelation", 0.7, 0.05, 0.8, False], ["rProbability", -1, 1, 10, 0.6])
    assert got[:3] == [84, 29, 13] and abs(got[3] - 1) < 1e-9


@needs_node
def test_result_line_has_two_decimals():
    got = js(["plan", "groups", {"effect": 0.5, "alpha": 0.1, "power": 0.8, "onesided": False, "dropout": 10}])[0]
    # one form for α and power on all pages (UI/UX audit 2026-10-07, W3)
    assert got["detail"].endswith("α = ,10 zweiseitig, Power 80 %") and "d = 0,50" in got["detail"]
    assert got["invite"] == f"{-(-got['total'] * 10 // 9)} (bei 10 % Ausschlüssen)"
    # the target to take over: the completions, usable participations plus the exclusions (W2)
    assert got["completions"] == -(-got["total"] * 10 // 9)


@needs_node
def test_empty_or_zero_fields_show_no_nan_or_infinity():
    prop = {"p": 50, "margin": 5, "conf": 0.95, "pop": None, "dropout": 10}
    cj = {"tasks": 8, "alts": 2, "levels": 3, "dropout": 10}
    got = js(["plan", "prop", prop], ["plan", "prop", {**prop, "margin": None}], ["plan", "prop", {**prop, "margin": 0}],
             ["plan", "prop", {**prop, "p": None}], ["plan", "conjoint", cj], ["plan", "conjoint", {**cj, "tasks": None}],
             ["plan", "conjoint", {**cj, "alts": 0}], ["plan", "groups", {"effect": None, "alpha": 0.05, "power": 0.8}],
             ["plan", "anova", {"f": 0.25, "groups": None, "alpha": 0.05, "power": 0.8}])
    assert got[0]["ok"] and got[0]["total"] == 381 and got[4]["ok"] and got[4]["total"] == 141
    for bad in got[1:4] + got[5:]:
        assert not bad["ok"] and bad["total"] is None and bad["invite"] == "–"
    text = json.dumps(got)
    assert "NaN" not in text and "Infinity" not in text and "∞" not in text


@needs_node
def test_proportion_uses_the_wilson_interval():
    """Half the width of the Wilson interval (Wilson 1927): ±5 points at 50 % need 381 (Wald: 385, at 380 the
    half width is 0.05002); small shares give an interval inside 0 to 100 %, so the Wald warnings are gone."""
    base = {"conf": 0.95, "pop": None, "dropout": 0}
    got = js(["plan", "prop", {**base, "p": 50, "margin": 5}], ["wilsonHalf", 381, 0.5, 1.959963984540054],
             ["wilsonHalf", 380, 0.5, 1.959963984540054], ["plan", "prop", {**base, "p": 2, "margin": 5}],
             ["plan", "prop", {**base, "p": 3, "margin": 2}])
    assert got[0]["total"] == 381 and "Wilson-Intervall" in got[0]["detail"]
    assert got[1] <= 0.05 < got[2]
    assert got[3]["ok"] and got[4]["ok"] and got[3]["warning"] == got[4]["warning"] == ""


@needs_node
def test_invitations_never_exceed_the_population():
    """Population 100, ±5 points at 95 %: 80 completions; with 50 % dropout 160 invitations were shown."""
    got = js(["plan", "prop", {"p": 50, "margin": 5, "conf": 0.95, "pop": 100, "dropout": 50}],
             ["plan", "prop", {"p": 50, "margin": 5, "conf": 0.95, "pop": 100, "dropout": 10}])
    assert got[0]["total"] == 80 and got[0]["invite"] == "100 (alle)" and "nicht erreichbar" in got[0]["warning"]
    assert got[1]["invite"] == "89 (bei 10 % Ausschlüssen)" and got[1]["warning"] == ""
    assert (got[0]["completions"], got[1]["completions"]) == (100, 89)


def test_page_offers_anova_paired_correlation_and_one_fold_of_sources(client):
    html = client.get("/sample-size/?design=anova").data.decode()
    assert 'value="anova" selected' in html and 'data-ss="f"' in html and 'data-ss-set="f=0.25"' in html
    assert 'data-ss="rho"' in html and 'data-ss-out="warning"' in html and "samplesize.js" in html
    assert html.count('<details class="sources">') == 1 and "Cochran, W. G. (1977)" in html
    assert 'href="https://doi.org/10.3389/fpsyg.2013.00863"' in html and "Cohen, J. (1988)" in html
    assert "Festlegung von Askurious" in html and "Guenther" not in html


# --- the planners added after the check of 2026-10-04 (independently computed reference values) -------------------

BASE = {"alpha": 0.05, "power": 0.8, "onesided": False, "dropout": 0}


def totals(*cases):
    return [r["total"] for r in js(*[["plan", d, {**BASE, **v}] for d, v in cases])]


@needs_node
def test_chi_square_regression_and_factorial_anova_reference_values():
    """Reference values (Cohen, 1988): χ² w = 0.3 df 1 → 88, df 4 → 133; regression f² = 0.15 with 5 predictors → 92,
    with 1 → 55; two-way ANOVA f = 0.25, df 1, 4 cells → 128. df 2 in 6 cells is 158 without rounding to equal
    cells, here 27 per cell = 162."""
    r2 = 0.15 / 1.15                                                  # R² of f² = 0.15
    assert totals(("chi2", {"w": 0.3, "rows": 2, "cols": 2}), ("chi2", {"w": 0.3, "rows": 3, "cols": 3}),
                  ("reg", {"preds": 5, "r2": r2}), ("reg", {"preds": 1, "r2": r2}),
                  ("factorial", {"f": 0.25, "fa": 2, "fb": 2, "factor": "A"}),
                  ("factorial", {"f": 0.25, "fa": 2, "fb": 3, "factor": "AB"})) == [88, 133, 92, 55, 128, 162]


@needs_node
def test_regression_increase_and_goodness_of_fit():
    """ΔR² = 0.05 of 2 tested in 5 predictors at full R² = 0.30: f² = 0.05/0.70; a distribution over 4 categories
    has df 3 (Cohen, 1988: w = 0.3, df 3 → 122)."""
    got = js(["plan", "reg", {**BASE, "preds": 5, "tested": 2, "r2": 0.3, "dr2": 0.05}],
             ["plan", "chi2", {**BASE, "w": 0.3, "rows": 4, "cols": 1}],
             ["plan", "reg", {**BASE, "preds": 5, "tested": 2, "r2": 0.3, "dr2": 0.4}])
    assert got[0]["ok"] and "davon 2 getestet" in got[0]["detail"] and "f² = 0,071" in got[0]["detail"]
    assert got[1]["total"] == 122 and "df = 3" in got[1]["detail"]
    assert not got[2]["ok"]                                          # increase larger than the whole R²


@needs_node
def test_repeated_measures_reference_values():
    """After Faul et al. (2007), f = 0.25, 3 measurements, ρ = 0.5, ε = 1: within → 28, between (2 groups) → 86,
    interaction (2 groups) → 28; ε below 1 needs more; between with one group is not a design, ε below 1/(m − 1)
    neither."""
    rm = {"f": 0.25, "meas": 3, "rho": 0.5}
    assert totals(("rm", {**rm, "rmgroups": 1, "rmeffect": "within"}), ("rm", {**rm, "rmgroups": 2, "rmeffect": "between"}),
                  ("rm", {**rm, "rmgroups": 2, "rmeffect": "inter"})) == [28, 86, 28]
    got = js(["plan", "rm", {**BASE, **rm, "rmgroups": 1, "rmeffect": "within", "eps": 0.6}],
             ["plan", "rm", {**BASE, **rm, "rmgroups": 1, "rmeffect": "between"}],
             ["plan", "rm", {**BASE, **rm, "rmgroups": 1, "rmeffect": "within", "eps": 0.3}])
    assert got[0]["total"] > 28 and "ε = 0,60" in got[0]["detail"] and not got[1]["ok"] and not got[2]["ok"]


@needs_node
def test_two_proportions_and_unequal_groups():
    """Fleiss et al. (2003) without continuity correction: 50 % against 60 % needs 388 per group (closed form
    [1.96·√(2·0.55·0.45) + 0.8416·√(0.25 + 0.24)]² / 0.01 = 387.3); d = 0.5 with twice as many in group 2:
    48 + 96 (noncentral t, ratio 1:2)."""
    got = js(["plan", "props", {**BASE, "p1": 50, "p2": 60}], ["plan", "groups", {**BASE, "effect": 0.5, "ratio": 2}],
             ["plan", "props", {**BASE, "p1": 50, "p2": 50}], ["cohenH", 0.5, 0.6])
    assert got[0]["total"] == 776 and "h = 0,20" in got[0]["detail"]
    assert got[1]["total"] == 144 and got[1]["detail"].startswith("48 + 96 Personen (Verhältnis 1 : 2,00)")
    assert not got[2]["ok"] and abs(got[3] - 0.2013579) < 1e-6


@needs_node
def test_equivalence_test_errs_on_the_safe_side():
    """TOST with bounds ± 0.5 and true difference 0: the normal approximation (Lakens 2017) gives
    2 · (1.645 + 1.282)² / 0.25 = 68.5 → 69 per group; the noncentral t bound is one person more. A true difference
    at the bound can never be shown equivalent."""
    got = js(["plan", "equiv", {**BASE, "bound": 0.5, "eqdesign": "groups"}],
             ["plan", "equiv", {**BASE, "bound": 0.5, "eqdesign": "groups", "truediff": 0.1}],
             ["plan", "equiv", {**BASE, "bound": 0.5, "eqdesign": "paired", "rho": 0.5}],
             ["plan", "equiv", {**BASE, "bound": 0.5, "eqdesign": "groups", "truediff": 0.5}])
    assert got[0]["total"] == 140 and "je einseitigem Test" in got[0]["detail"]
    assert got[1]["total"] > 140 and got[2]["total"] < 70 and not got[3]["ok"]


@needs_node
def test_mean_alpha_maxdiff_and_vignettes():
    """Mean: the smallest n with t(n − 1) · 15 / √n ≤ 3 is 99 (z: (1.96 · 15 / 3)² = 96.04 → 97, audit 2026-10-05,
    K-4). Bonett (2002): k = 10, α₀ 0.7 against 0.8: 2k/(k−1)·(1.96 + 0.8416)² /
    ln(1.5)² + 2 = 108.1 → 109. MaxDiff 20 statements, 4 per set, 13 sets: 1000 · 20 / 52 → 385, 500 → 193, fewer
    than 3 · 20 / 4 = 15 sets warns. Vignettes 100 in decks of 10, 5 persons each: 50; 12 per person warns."""
    got = js(["plan", "mean", {**BASE, "sd": 15, "emargin": 3, "conf": 0.95}],
             ["plan", "alpha", {**BASE, "items": 10, "alpha0": 0.7, "alpha1": 0.8}],
             ["plan", "maxdiff", {"mdk": 20, "mdset": 4, "mdsets": 13, "dropout": 0}],
             ["plan", "maxdiff", {"mdk": 20, "mdset": 4, "mdsets": 15, "dropout": 0}],
             ["plan", "vignette", {"vtotal": 100, "vper": 10, "vdeck": 5, "dropout": 0}],
             ["plan", "vignette", {"vtotal": 120, "vper": 12, "vdeck": 5, "dropout": 0}],
             ["plan", "mean", {**BASE, "sd": 15, "emargin": 3, "conf": 0.95, "pop": 200}])
    assert [g["total"] for g in got[:3]] == [99, 109, 385] and "Minimum 193" in got[2]["detail"]
    assert "mindestens 15 Sets" in got[2]["warning"] and got[3]["warning"] == ""
    assert got[4]["total"] == 50 and "10 Decks × 5 Personen" in got[4]["detail"] and "5-mal bewertet" in got[4]["detail"]
    assert got[4]["warning"] == "" and "Mehr als 10 Vignetten" in got[5]["warning"]
    assert got[6]["total"] == 67                                     # 99 / (1 + 98 / 200) = 66.4


@needs_node
def test_design_effect_and_subgroup_scale_every_planner():
    """Clusters of 11 with ICC 0.05 (1.5) times weights with CV 0.5 (1.25) = 1.875; a subgroup of 50 %: 128 →
    480. Cluster size without ICC changes nothing yet; a negative ICC is out of range."""
    eff = {"effect": 0.5}
    got = js(["plan", "groups", {**BASE, **eff, "cluster": 11, "icc": 0.05, "cv": 0.5, "share": 50}],
             ["plan", "groups", {**BASE, **eff, "cluster": 11}], ["designEffect", 11, 0.05, 0.5],
             ["plan", "groups", {**BASE, **eff, "cluster": 11, "icc": -0.1}],
             ["plan", "conjoint", {"tasks": 8, "alts": 2, "levels": 3, "dropout": 0, "share": 25}])
    assert got[0]["total"] == 480 and "Designeffekt 1,88" in got[0]["detail"] and "ohne diese Korrekturen 128" in got[0]["detail"]
    assert got[1]["total"] == 128 and abs(got[2] - 1.875) < 1e-12 and not got[3]["ok"]
    assert got[4]["total"] == 564                                    # 141 / 0.25


@needs_node
def test_smallest_effect_for_a_fixed_sample():
    """The other way round (Lakens 2022): 128 persons find d = 0.50, 159 in three groups f = 0.25, 84 pairs
    r ≈ 0.30, 776 persons 60 % against 50 %, 400 persons ±4.9 points; a subgroup shrinks the usable n; nothing
    to invite and nothing to take over; rules of thumb ignore the mode."""
    got = js(["plan", "groups", {**BASE, "mode": "effect", "avail": 128}],
             ["plan", "anova", {**BASE, "mode": "effect", "avail": 159, "groups": 3}],
             ["plan", "corr", {**BASE, "mode": "effect", "avail": 84}],
             ["plan", "props", {**BASE, "mode": "effect", "avail": 776, "p1": 50}],
             ["plan", "prop", {"conf": 0.95, "p": 50, "mode": "effect", "avail": 400}],
             ["plan", "groups", {**BASE, "mode": "effect", "avail": 256, "share": 50}],
             ["plan", "groups", {**BASE, "mode": "effect", "avail": 3}],
             ["plan", "conjoint", {"tasks": 8, "alts": 2, "levels": 3, "dropout": 0, "mode": "effect", "avail": 100}])
    assert [g["effect"] for g in got[:5]] == ["d = 0,50", "f = 0,25", "r = 0,30", "60,0 % gegen 50,0 % (h = 0,20)",
                                              "± 4,9 Prozentpunkte"]
    assert all(g["total"] is None and g["invite"] == "–" for g in got[:5])
    assert got[5]["effect"] == "d = 0,50" and "nutzbar 128 von 256" in got[5]["detail"]
    assert got[6]["effect"] == "–" and "Zu wenige" in got[6]["warning"]
    assert got[7]["total"] == 141


def test_page_offers_every_planner_with_its_sources(app, client, survey):
    from tests.analysis.test_maxdiff import maxdiff_form

    html = client.get("/sample-size/").data.decode()
    for key in ("groups", "props", "anova", "factorial", "chi2", "equiv", "paired", "rm", "corr", "reg", "prop", "mean",
                "alpha", "conjoint", "maxdiff", "vignette"):
        assert f'<option value="{key}"' in html
    assert html.count("<optgroup") == 5 and 'type="checkbox" data-ss="mode"' in html and 'data-ss="cluster"' in html
    for doi in ("10.1002/0471445428", "10.1007/BF01068419", "10.1177/1948550617697177", "10.1525/collabra.33267",
                "10.1080/01621459.1927.10502953", "10.3102/10769986027004335", "10.1370/afm.141",
                "10.18148/srm/2011.v5i3.4625", "10.4135/9781483398075", "10.1177/2515245920951503"):
        assert f'href="https://doi.org/{doi}"' in html
    assert "Kish, L. (1992)" in html and "Gabler, S., Häder, S., &amp; Lahiri, P. (1999)" in html and "Chapman" in html
    assert html.count('<details class="sources">') == 1
    add(client, "maxdiff")                                          # item 2: 8 statements, 4 per set
    client.post("/surveys/1/editor/items/2", data=maxdiff_form())
    html = client.get("/sample-size/?survey=1").data.decode()
    assert 'value="maxdiff" selected' in html and 'value="8" data-ss="mdk"' in html and "Übernommen aus dem MaxDiff" in html


@needs_node
def test_finite_population_correction_comes_after_design_effect_and_subgroup():
    """Audit 2026-10-05 (B-3): n0 · deff, then the subgroup's part share · N of the population, then the finite
    population correction (Kish 1965; Cochran 1977). Wilson n0 = 381 for ±5 points at 50 %. Before, the correction
    came first: 554, 651 ("nicht erreichbar") and 724. The smallest-effect mode is the mirror image."""
    prop = {"p": 50, "margin": 5, "conf": 0.95, "dropout": 0}
    got = js(["plan", "prop", {**prop, "pop": 1000, "cluster": 11, "icc": 0.1}],
             ["plan", "prop", {**prop, "pop": 500, "cluster": 21, "icc": 0.1}],
             ["plan", "prop", {**prop, "pop": 500, "share": 30}],
             ["plan", "prop", {**prop, "pop": 500, "cluster": 21, "icc": 0.1, "mode": "effect", "avail": 349}],
             ["plan", "prop", {**prop, "pop": 500, "share": 30, "mode": "effect", "avail": 360}],
             ["plan", "prop", {**prop, "pop": 500, "cluster": 21, "icc": 0.1, "mode": "effect", "avail": 300}])
    assert [g["total"] for g in got[:3]] == [433, 349, 360]
    assert all(g["warning"] == "" for g in got[:3])
    assert got[3]["effect"] == got[4]["effect"] == "± 5,0 Prozentpunkte"
    assert got[5]["effect"] != "± 5,0 Prozentpunkte"


@needs_node
def test_mean_planner_uses_t_like_the_results_page():
    """Audit 2026-10-05, K-4: the results page gives M ± t(n − 1) · SD / √n; with z the planner asked for 16 where
    the page then showed ± 0.533 (SD 1, ± 0.5): now 18, 27 for SD 10 ± 4, 64 for SD 1 ± 0.25. The margin reached
    by 16 completions is the t margin, ± 0.53."""
    got = js(["plan", "mean", {"sd": 1, "emargin": 0.5, "conf": 0.95, "dropout": 0}],
             ["plan", "mean", {"sd": 10, "emargin": 4, "conf": 0.95, "dropout": 0}],
             ["plan", "mean", {"sd": 1, "emargin": 0.25, "conf": 0.95, "dropout": 0}],
             ["plan", "mean", {"sd": 1, "conf": 0.95, "dropout": 0, "mode": "effect", "avail": 16}])
    assert [g["total"] for g in got[:3]] == [18, 27, 64]
    assert got[3]["effect"] == "± 0,53"
    from app import intervals

    for n, sd, e in ((18, 1, 0.5), (27, 10, 4), (64, 1, 0.25)):     # n keeps the margin, n − 1 does not
        assert intervals.t_quantile(0.975, n - 1) * sd / n ** 0.5 <= e < intervals.t_quantile(0.975, n - 2) * sd / (n - 1) ** 0.5
