"""The expected effect of a hypothesis from an earlier survey (PA6, concept poweranalyse-konzept.md): another survey
of the same person or group that measured the same two variables (same variable names) gives the effect with its
data pool, and the lower end of its 60 % confidence interval as "Safeguard Power" (Perugini, Gallucci & Costantini,
2014), because effects of small earlier studies come out too large (Albers & Lakens, 2018).

  r, Spearman   Fisher z interval of r (of the ranks for Spearman)
  t, U          Cohen's d with the pooled SD; interval by inverting the noncentral t distribution
  ANOVA, KW     Cohen's f from omega squared (not eta squared, Albers & Lakens, 2018); interval by inverting the
                noncentral F distribution
  chi-square    Cohen's w = sqrt(chi² / N); interval by inverting the noncentral chi-square distribution

Rank tests are planned with the effect of their parametric counterpart, as the power analysis plans them (app/power.py).
Only real participations of the other survey, without what its quality assurance marks; never test runs or dummies."""
import math

from app import compare, power, study_plan
from app.group_tests import chi2_sf, ncx2_sf

LEVEL = 0.60  # Perugini et al. (2014): the lower end of the 60 % interval, i.e. its 20th percentile
Z20 = 0.8416212335729143  # standard normal quantile 0.80
MIN_N = 10  # fewer cases in the earlier survey give no estimate
MAX_SURVEYS = 12  # the most recent surveys with the outcome are looked at, not all

PERUGINI = ("Perugini, M., Gallucci, M., & Costantini, G. (2014). Safeguard power as a protection against imprecise "
            "power estimates. Perspectives on Psychological Science, 9(3), 319–332. "
            "https://doi.org/10.1177/1745691614528519")
ALBERS_LAKENS = ("Albers, C., & Lakens, D. (2018). When power analyses based on pilot data are biased: Inaccurate effect "
                 "size estimators and follow-up bias. Journal of Experimental Social Psychology, 74, 187–195. "
                 "https://doi.org/10.1016/j.jesp.2017.09.004")


def _bisect(f, target, lo, hi, steps=80):
    """x in [lo, hi] with f(x) = target for an increasing f."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


_ncx2_sf = ncx2_sf  # one implementation for sem, power and the pilot (code review 2026-10-15, A6)


def _correlation(xs, ys, rank):
    from app import hypotheses, scales

    pairs = [(x, y) for x, y in zip(xs, ys) if isinstance(x, float) and isinstance(y, float)]
    px, py = [x for x, _ in pairs], [y for _, y in pairs]
    if rank:
        px, py = hypotheses._ranks(px), hypotheses._ranks(py)
    r, n, _ = scales.pearson(px, py)
    if r is None or n < MIN_N or abs(r) >= 0.999:
        return None
    z = math.atanh(abs(r))
    # the standard error of z: 1/sqrt(n - 3) for r, sqrt((1 + r²/2)/(n - 3)) for Spearman (Bonett & Wright, 2000), as
    # the power analysis plans it (app/power.py)
    se = math.sqrt((1 + r * r / 2 if rank else 1) / (n - 3))
    low = math.tanh(z - Z20 * se)
    return {"n": n, "effect": abs(r), "safeguard": max(0.0, low)}


def _groups(survey, h, table, numeric):
    from app import hypotheses

    xs = hypotheses._codes(survey, h.get("iv"), table)
    ys, _ = hypotheses._values(survey, h.get("dv"), table)
    groups = {}
    for g, v in zip(xs, ys):
        if g is None or v is None or (numeric and not isinstance(v, float)):
            continue
        groups.setdefault(g, []).append(v)
    return {k: v for k, v in groups.items() if len(v) >= compare.MIN_GROUP}


def _two_groups(groups):
    if len(groups) != 2:
        return None
    a, b = list(groups.values())
    n1, n2 = len(a), len(b)
    if n1 + n2 < MIN_N:
        return None
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    if sp <= 0:
        return None
    d = abs(m1 - m2) / sp
    df, scale = n1 + n2 - 2, math.sqrt(n1 * n2 / (n1 + n2))
    t_obs = d * scale

    def sf(t, delta):  # P(T' > t)
        return 1 - power._nct_cdf(t, df, delta)
    delta_low = 0.0 if sf(t_obs, 0.0) >= 0.2 else _bisect(lambda dl: sf(t_obs, dl), 0.2, 0.0, t_obs + 10)
    return {"n": n1 + n2, "effect": d, "safeguard": delta_low / scale}


def _anova(groups):
    if len(groups) < 2:
        return None
    values = [v for g in groups.values() for v in g]
    N, k = len(values), len(groups)
    if N < MIN_N or N <= k:
        return None
    grand = sum(values) / N
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups.values())
    ssw = sum((x - sum(g) / len(g)) ** 2 for g in groups.values() for x in g)
    if ssw <= 0:
        return None
    msw = ssw / (N - k)
    omega2 = max(0.0, (ssb - (k - 1) * msw) / (ssb + ssw + msw))
    f_obs = (ssb / (k - 1)) / msw
    d1, d2 = k - 1, N - k
    lam_low = 0.0 if power._ncf_sf(f_obs, d1, d2, 0.0) >= 0.2 else _bisect(
        lambda lam: power._ncf_sf(f_obs, d1, d2, lam), 0.2, 0.0, f_obs * d1 * 4 + 100)
    return {"n": N, "effect": math.sqrt(omega2 / (1 - omega2)) if omega2 < 1 else None,
            "safeguard": math.sqrt(lam_low / N)}


def _chi2(groups):
    from app import group_tests as gt

    keys = list(groups)
    cats = sorted({v for k in keys for v in groups[k]})
    if len(keys) < 2 or len(cats) < 2:
        return None
    rows = [[sum(1 for v in groups[k] if v == c) for k in keys] for c in cats]
    t = gt.chi2_test(rows)
    if not t or t["n"] < MIN_N:
        return None
    df = t["df"]
    lam_low = 0.0 if chi2_sf(t["chi2"], df) >= 0.2 else _bisect(
        lambda lam: _ncx2_sf(t["chi2"], df, lam), 0.2, 0.0, t["chi2"] * 4 + 100)
    return {"n": t["n"], "effect": math.sqrt(t["chi2"] / t["n"]), "safeguard": math.sqrt(lam_low / t["n"])}


def _completed(survey):
    """The number of completed real participations, counted in the database (no export table)."""
    from app.extensions import db
    from app.models import Response

    return db.session.scalar(db.select(db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.status == Response.COMPLETED)) or 0


def estimate(source, h, analysis):
    """{"n", "effect", "safeguard"} of a hypothesis (its iv and dv codes) in the data pool of the survey `source`,
    for the effect size the analysis is planned with; None when the other survey lacks a variable or has too few
    cases. Code review 2026-10-07, C5: a survey with fewer than MIN_N completed participations is counted, not built
    as a table, and an estimate is kept under the fingerprint of the other survey's data pool (results_store,
    "pilot"), so the page builds an export table only the first time and after new data."""
    if study_plan._item(source, h.get("iv")) is None or study_plan._item(source, h.get("dv")) is None:
        return None
    if _completed(source) < MIN_N:
        return None
    from app import export, results_store

    pool = {"all": False, "sources": ["real"], "speeder": export.threshold(source), "drop": True}
    key = results_store.fingerprint(source, pool)
    hit = results_store.load(source, key, pool, name="pilot")
    kept = dict(hit[0]) if hit and isinstance(hit[0], dict) else {}
    ref = "|".join(str(x) for x in (h.get("iv"), h.get("dv"), analysis))
    if ref not in kept:
        kept[ref] = _estimate(source, h, analysis, compare.table_of(source, pool))
        results_store.save(source, key, kept, pool, name="pilot", basis=key)
    return kept[ref]


def _estimate(source, h, analysis, table):
    from app import hypotheses

    if len(table) < MIN_N:
        return None
    if analysis in ("r", "rho"):
        xs, _ = hypotheses._values(source, h.get("iv"), table)
        ys, _ = hypotheses._values(source, h.get("dv"), table)
        if xs is None or ys is None:
            return None
        return _correlation(xs, ys, analysis == "rho")
    if analysis in ("t", "u"):
        return _two_groups(_groups(source, h, table, True))
    if analysis in ("anova", "kw"):
        return _anova(_groups(source, h, table, True))
    if analysis == "chi2":
        return _chi2(_groups(source, h, table, False))
    return None


def candidates(survey, h, user):
    """[(survey, estimate)] of the other surveys the person may see that measured both variables of the hypothesis,
    with an estimate (most cases first)."""
    from app import access
    from app.extensions import db
    from app.models import Survey

    p = study_plan.power_of(survey, h)
    if not p["measure"] or not h.get("iv") or not h.get("dv"):
        return []
    from app.models import Block, Item

    # only surveys whose questionnaire has the outcome (by its variable name); the predictor may be a randomization
    ids = db.select(Block.survey_id).join(Item, Item.block_id == Block.id).filter(Item.code == h["dv"])
    others = db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user)), Survey.id.in_(ids),
                                                         Survey.id != survey.id).order_by(Survey.id.desc()).limit(MAX_SURVEYS))
    out = []
    for other in others:
        found = estimate(other, h, p["analysis"])
        if found and found.get("effect"):
            out.append((other, found))
    return sorted(out, key=lambda pair: -pair[1]["n"])
