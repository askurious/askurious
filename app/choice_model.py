"""Aggregate (pooled) conditional logit for conjoint data, numpy only.

Shared by the CBC (app/cbc_analysis.py) and the adaptive conjoint (app/acbc_logit.py). Every
observed choice is a set of alternatives (rows of a design matrix) and the chosen one. Utility =
sum of part-worths of the levels (effects coding: the part-worths of an attribute sum to 0)
+ an alternative-specific constant for a "none" / threshold alternative + beta_price * price when a
price enters linearly (the ACBC summed price, or a CBC attribute marked as price with numbers as
levels). Fitted by Newton-Raphson with step halving. Standard errors: when every choice set knows its
person, cluster-robust (sandwich H^-1 (sum over persons of g_p g_p') H^-1 with the small-sample factor
G / (G - 1)), because the choices of one person are not independent; otherwise from the inverse of the
negative Hessian. With the cluster-robust covariance and fewer than T_UNTIL persons the 95 % intervals use the
t quantile with G - 1 degrees of freedom instead of 1.96 (Cameron & Miller, 2015): the sandwich underestimates the
spread with few clusters (audit 2026-10-05, C-2: coverage 91-93 % with 8 to 20 persons).

Separation (a level never or always chosen where it could have been) has no finite maximum; the
model is then fitted with a weak normal prior on the zero-centred part-worths (as for MaxDiff) and
the page says which levels are affected. The same prior carries parameters the choices do not determine
at all (a level never shown, attributes that only came in fixed combinations: undetermined()). What the
prior sets is shown without standard error and without willingness to pay, for the whole attribute: the
other levels of an attribute with a bounded level are centred on a mean that contains the bounded value.

Reported: part-worths per level with standard errors, attribute importance (range of the
part-worths as a share of the sum of ranges; for the price |beta_price| x price range; none for an
attribute the prior sets, the others then as shares among themselves), and with a
price the willingness to pay per level relative to the attribute mean (beta_level / -beta_price),
only when the price coefficient is significantly negative (z <= -q), with a 95 % interval by Fieller (1954):
the set of ratios r with (beta_level + r beta_price)^2 <= q^2 Var(beta_level + r beta_price). Unlike the
symmetric delta interval it stays right near the boundary, where the ratio is skewed (audit 2026-10-05, C-1:
coverage of the delta interval 81 % at z about -2, Fieller 95 %). It is bounded exactly when |z| > q; an
unbounded one is shown as "nicht bestimmt". Between q and |z| = WTP_WEAK_Z the willingness to pay is shown with a
note: it is only shown where |beta_price| happened to come out large, so on average it is too small (audit
2026-10-05, C-1: median 26 % too small at z about -2, about 6 % at z about -3, unbiased from about -5).
These are aggregate estimates for the whole sample, not individual utilities (hierarchical Bayes);
heterogeneity between persons is ignored, which also tends to shrink them.
"""
import math

import numpy as np

from app.intervals import t_quantile
from app.lang import de_en
from app.units import number, page_lang

MIN_PERSONS = 30  # below this the estimates are shown with a warning
RIDGE = 0.1       # weak normal prior (sd about 3) on the zero-centred part-worths under separation
Z95 = 1.959964
T_UNTIL = 50      # below this many persons the cluster-robust intervals use t(G - 1) instead of z (C-2)
WTP_WEAK_Z = 4.0  # |z| of the price coefficient below this: willingness to pay with a note (Askurious's choice, C-1)
MCFADDEN = ("McFadden, D. (1974). Conditional logit analysis of qualitative choice behavior. In P. Zarembka (Ed.), "
            "Frontiers in econometrics (pp. 105–142). Academic Press.")
TRAIN = ("Train, K. E. (2009). Discrete choice methods with simulation (2nd ed.). Cambridge University Press. "
         "https://doi.org/10.1017/CBO9780511805271")
CAMERON = ("Cameron, A. C., & Miller, D. L. (2015). A practitioner's guide to cluster-robust inference. Journal of Human "
           "Resources, 50(2), 317–372. https://doi.org/10.3368/jhr.50.2.317")
HOLE = ("Hole, A. R. (2007). A comparison of approaches to estimating confidence intervals for willingness to pay "
        "measures. Health Economics, 16(8), 827–840. https://doi.org/10.1002/hec.1197")  # delta vs. Fieller
FIELLER = ("Fieller, E. C. (1954). Some problems in interval estimation. Journal of the Royal Statistical Society: "
           "Series B (Methodological), 16(2), 175–185. https://doi.org/10.1111/j.2517-6161.1954.tb00159.x")
ALBERT_ANDERSON = ("Albert, A., & Anderson, J. A. (1984). On the existence of maximum likelihood estimates in logistic "
                   "regression models. Biometrika, 71(1), 1–10. https://doi.org/10.1093/biomet/71.1.1")
SOURCES = (MCFADDEN, TRAIN, CAMERON)


class ChoiceSet:
    """One observed choice: X (alternatives x parameters), index of the chosen alternative, and
    optionally the person it belongs to (any hashable; for cluster-robust standard errors)."""

    __slots__ = ("X", "chosen", "person")

    def __init__(self, X, chosen, person=None):
        self.X, self.chosen, self.person = np.asarray(X, dtype=float), chosen, person


def _groups(sets):
    """Choice sets stacked by number of alternatives: [(X (sets x alternatives x params), chosen)]."""
    by_size = {}
    for s in sets:
        if 0 <= s.chosen < len(s.X):  # a choice outside the set says nothing (code review 2026-10-15, C5)
            by_size.setdefault(len(s.X), []).append(s)
    return [(np.stack([s.X for s in group]), np.array([s.chosen for s in group])) for group in by_size.values()]


def _evaluate(groups, beta, k, derivatives=True):
    """Log-likelihood, gradient and Hessian of the conditional logit."""
    ll, grad, hess = 0.0, np.zeros(k), np.zeros((k, k))
    for X, chosen in groups:
        v = X @ beta                                   # sets x alternatives
        v -= v.max(axis=1, keepdims=True)
        p = np.exp(v)
        p /= p.sum(axis=1, keepdims=True)
        rows = np.arange(len(chosen))
        ll += float(np.log(p[rows, chosen]).sum())
        if derivatives:
            xbar = np.einsum("nj,njk->nk", p, X)
            grad += (X[rows, chosen] - xbar).sum(axis=0)
            d = X - xbar[:, None, :]
            hess -= np.einsum("nj,nja,njb->ab", p, d, d)
    return ll, grad, hess


def _penalised(groups, beta, k, ridge, derivatives=True):
    """_evaluate() minus beta' P beta / 2 with P = ridge * I (number) or P = ridge (matrix): a weak normal
    prior that keeps separated data finite."""
    ll, grad, hess = _evaluate(groups, beta, k, derivatives)
    if ridge is not None and np.any(ridge):
        P = ridge * np.eye(k) if np.isscalar(ridge) else ridge
        ll -= float(beta @ P @ beta) / 2
        grad = grad - P @ beta
        hess = hess - P
    return ll, grad, hess


def fit(sets, k, max_iter=100, tol=1e-8, ridge=0.0, info=None):
    """Conditional logit by Newton-Raphson with step halving; optionally penalised (see _penalised).
    Returns (beta, covariance or None, log-likelihood, converged, iterations); the log-likelihood
    is always the unpenalised one. A dict `info` receives "robust" (the covariance is the cluster-robust
    sandwich) and "clusters" (the number of persons G)."""
    groups = _groups(sets)
    beta = np.zeros(k)
    ll, grad, hess = _penalised(groups, beta, k, ridge)
    converged, it = False, 0
    for it in range(1, max_iter + 1):
        try:
            step = np.linalg.solve(hess, -grad)
        except np.linalg.LinAlgError:
            break
        t = 1.0
        for _ in range(30):  # step halving keeps the log-likelihood from falling
            cand = beta + t * step
            new_ll = _penalised(groups, cand, k, ridge, derivatives=False)[0]
            if new_ll >= ll - 1e-12:
                break
            t /= 2
        change = abs(new_ll - ll)
        beta = cand
        ll, grad, hess = _penalised(groups, beta, k, ridge)
        if change < tol and np.max(np.abs(t * step)) < 1e-6:
            converged = True
            break
    cov, robust = None, False
    try:
        cov = np.linalg.inv(-hess)
        if not np.all(np.isfinite(cov)) or np.any(np.diag(cov) < 0) or np.linalg.cond(-hess) > 1e12:
            cov = None
    except np.linalg.LinAlgError:
        cov = None
    if cov is not None:
        cov, robust = _robust(sets, beta, cov)
    if ridge is not None and np.any(ridge):
        ll = _evaluate(groups, beta, k, derivatives=False)[0]
    if info is not None:
        info["robust"] = robust
        info["clusters"] = len({s.person for s in sets}) if all(s.person is not None for s in sets) else None
    return beta, cov, ll, converged, it


def critical(info):
    """Two-sided 95 % quantile for the intervals of a model fitted with fit(..., info=info): t(G - 1) when the
    covariance is the cluster-robust sandwich over fewer than T_UNTIL persons (Cameron & Miller, 2015), else z."""
    G = (info or {}).get("clusters")
    if (info or {}).get("robust") and G and 1 < G < T_UNTIL:
        return t_quantile(0.975, G - 1)
    return Z95


def fieller(a, b, vaa, vbb, vab, q=Z95):
    """95 % interval of the ratio a / -b by Fieller (1954): the r with (a + r b)^2 <= q^2 Var(a + r b). (low, high),
    or None when the set is unbounded (|b| / SE(b) <= q: the denominator is not clearly away from 0)."""
    A = b * b - q * q * vbb
    B = 2 * (a * b - q * q * vab)
    C = a * a - q * q * vaa
    if A <= 1e-12 * b * b:  # at the boundary itself rounding would give an end far out instead of none
        return None
    disc = B * B - 4 * A * C
    if disc < 0:  # cannot happen for A > 0 (r = -a / b lies inside), kept against rounding
        return None
    root = math.sqrt(disc)
    return (-B - root) / (2 * A), (-B + root) / (2 * A)


def weak_price(z, q=Z95):
    """|z| of a price coefficient that is significantly negative but only just (q < |z| < WTP_WEAK_Z): its willingness
    to pay tends to come out too small (audit 2026-10-05, C-1; the skewed ratio near the boundary: Hole, 2007);
    else None."""
    if z is None or z > -q or -z >= WTP_WEAK_Z:
        return None
    return -z


def wtp(coding, beta, cov, a, lv, q=Z95):
    """(willingness to pay, lower, upper) of level lv of attribute a relative to the attribute mean: part-worth /
    -price coefficient, the interval by fieller() with the variances and the covariance of part-worth and price
    coefficient from cov. (None, None, None) when the interval is unbounded ("nicht bestimmt")."""
    m = coding.level_vector(a, lv)
    p, scale = coding.price, coding.price_scale
    num, den = float(m @ beta), float(beta[p])  # the price column is (price - centre) / scale
    if cov is None or den == 0:
        return None, None, None
    ends = fieller(num, den, float(m @ cov @ m), float(cov[p, p]), float(m @ cov[:, p]), q)
    if ends is None:
        return None, None, None
    return num / -den * scale, ends[0] * scale, ends[1] * scale


def _robust(sets, beta, bread):
    """(covariance, robust): the cluster-robust covariance over persons (sandwich) and True, or `bread` (the
    model-based covariance) and False when a set has no person or there are not more persons than parameters (the
    sandwich then has less than full rank)."""
    if any(s.person is None for s in sets):
        return bread, False
    scores = {}
    for s in sets:
        v = s.X @ beta
        p = np.exp(v - v.max())
        p /= p.sum()
        g = s.X[s.chosen] - p @ s.X
        scores[s.person] = scores.get(s.person, 0) + g
    G = len(scores)
    if G <= len(beta):
        return bread, False
    S = np.array(list(scores.values()))
    cov = G / (G - 1) * bread @ (S.T @ S) @ bread
    try:
        np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        return bread, False
    return (cov, True) if np.all(np.isfinite(cov)) else (bread, False)


class Coding:
    """Design matrix rows: effects coding per attribute (one column per level except the last), for
    all attributes except `linear` ones; then the alternative-specific constant (if asc); then the
    price (if price_scale), centred on price_center."""

    def __init__(self, levels, price_scale=None, asc=True, linear=(), price_center=0.0):
        self.levels = list(levels)
        self.effects = [a for a in range(len(self.levels)) if a not in set(linear)]
        self.offsets = {}
        col = 0
        for a in self.effects:
            self.offsets[a] = col
            col += self.levels[a] - 1
        self.asc = col if asc else None
        col += 1 if asc else 0
        self.price = col if price_scale else None
        self.price_scale = price_scale
        # the price enters as (price - centre) / scale, the centre being the mean price shown: the constant of
        # the "none" / threshold alternative then refers to an offer at the mean price, not at price 0 (which
        # lies far outside the data, makes constant and price coefficient nearly collinear and the constant huge)
        self.price_center = price_center or 0.0
        self.k = col + (1 if price_scale else 0)

    def row(self, profile, price=None):
        """A level None is an attribute not shown (partial profiles, e.g. from the conjoint import): its columns stay
        0, the average of the effects-coded part-worths, so it neither adds nor takes away utility."""
        x = np.zeros(self.k)
        for a in self.effects:
            n, off, lv = self.levels[a], self.offsets[a], profile[a]
            if lv is None:
                continue
            if lv < n - 1:
                x[off + lv] = 1.0
            else:
                x[off:off + n - 1] = -1.0
        if self.price is not None:
            x[self.price] = ((self.price_center if price is None else price) - self.price_center) / self.price_scale
        return x

    def asc_row(self):
        x = np.zeros(self.k)
        x[self.asc] = 1.0
        return x

    def level_of(self, x, a):
        """Level of attribute a in a design row, None for the constant (none/threshold) row."""
        n, off = self.levels[a], self.offsets[a]
        cols = x[off:off + n - 1]
        if np.all(cols == -1.0):
            return n - 1
        hit = np.flatnonzero(cols == 1.0)
        return int(hit[0]) if len(hit) else None

    def level_vector(self, a, lv):
        """m with m @ beta = part-worth of level lv of attribute a (last level: minus the sum)."""
        m = np.zeros(self.k)
        n, off = self.levels[a], self.offsets[a]
        if lv < n - 1:
            m[off + lv] = 1.0
        else:
            m[off:off + n - 1] = -1.0
        return m

    def penalty(self):
        """Prior precision for separated data: RIDGE on every zero-centred part-worth and the constant,
        none on the price (it would bias the willingness to pay)."""
        P = np.zeros((self.k, self.k))
        for a in self.effects:
            M = np.array([self.level_vector(a, lv) for lv in range(self.levels[a])])
            P += M.T @ M
        if self.asc is not None:
            P[self.asc, self.asc] = 1.0
        return RIDGE * P

    def levels_of(self, X, a):
        """level_of() for a whole stack of design rows at once (any shape x parameters): the level of
        attribute a per row, -1 for the constant (none/threshold) row."""
        n, off = self.levels[a], self.offsets[a]
        cols = X[..., off:off + n - 1]
        if n < 2:  # no column of its own: every row counts as its only level, as in level_of
            return np.full(cols.shape[:-1], n - 1, dtype=int)
        hit = cols == 1.0
        return np.where(np.all(cols == -1.0, axis=-1), n - 1, np.where(hit.any(axis=-1), hit.argmax(axis=-1), -1))

    def shown_levels(self, sets):
        """{(attribute, level)} that occur in at least one alternative."""
        out = set()
        for X, _ in _groups(sets):
            for a in self.effects:
                out.update((a, int(lv)) for lv in np.unique(self.levels_of(X, a)) if lv >= 0)
        return out

    def separated(self, sets):
        """[(attribute, level)] never or always chosen among the sets where it made a difference (some
        alternatives had it, others not): their maximum-likelihood part-worth is infinite."""
        stats = {}
        # all sets of one size at once: asking level_of() for every alternative of every set took most of the
        # time of the model (a million calls for 5000 persons with ten tasks)
        for X, chosen in _groups(sets):
            rows = np.arange(len(chosen))
            for a in self.effects:
                have = self.levels_of(X, a)                      # sets x alternatives
                for lv in range(self.levels[a]):
                    has = have == lv
                    informative = has.any(axis=1) & ~has.all(axis=1)  # every alternative had it: no information
                    shown = int(informative.sum())
                    if shown:
                        n, k = stats.get((a, lv), (0, 0))
                        stats[(a, lv)] = (n + shown, k + int((informative & has[rows, chosen]).sum()))
        return sorted(key for key, (shown, chosen) in stats.items() if chosen in (0, shown))

    def constant_side(self, sets):
        """"never" / "always" if the alternative with the constant ("none", threshold) was never or always
        chosen where it was offered (its constant then has no finite estimate), else None."""
        if self.asc is None:
            return None
        offered = chosen = 0
        for s in sets:
            rows = np.flatnonzero(s.X[:, self.asc] == 1.0)
            if len(rows):
                offered += 1
                chosen += s.chosen in rows
        return None if not offered or 0 < chosen < offered else ("never" if chosen == 0 else "always")

    def part_worths(self, beta, cov):
        """{attribute index: (part-worths, standard errors)}; the last level is minus the sum of the others."""
        out = {}
        for a in self.effects:
            n, off = self.levels[a], self.offsets[a]
            b = list(beta[off:off + n - 1])
            b.append(-sum(b))
            se = [None] * n
            if cov is not None:
                sub = cov[off:off + n - 1, off:off + n - 1]
                se = [math.sqrt(sub[i, i]) for i in range(n - 1)] + [math.sqrt(max(sub.sum(), 0.0))]
            out[a] = (b, se)
        return out


def undetermined(sets, k):
    """Per parameter: True if the choices cannot determine it at all. A choice only compares the alternatives
    of its set, so what counts are the differences within the sets; a parameter with a share in their null
    space is free (a level that was never shown, attributes whose levels only came in fixed combinations)."""
    G = np.zeros((k, k))
    for X, _ in _groups(sets):
        d = X - X.mean(axis=1, keepdims=True)
        G += np.einsum("nja,njb->ab", d, d)
    w, V = np.linalg.eigh(G)
    null = V[:, w < 1e-9 * max(float(w.max(initial=0.0)), 1.0)]
    return np.any(np.abs(null) > 1e-6, axis=1) if null.size else np.zeros(k, dtype=bool)


def price_scale(prices):
    """Scale for the price column (its standard deviation), None if the price does not vary."""
    return float(np.std(prices)) or None if len(set(prices)) > 1 else None


def price_unit(prices):
    """Currency units the price coefficient is shown for: a power of ten a tenth of the shown price range (at
    least 1), so that a coefficient for prices in the ten thousands does not show as 0,0000."""
    span = max(prices) - min(prices) if prices else 0
    return 10 ** max(0, math.floor(math.log10(span)) - 1) if span > 0 else 1


# the alternative with the constant as the callers name it, in English (app/cbc_analysis.py, app/acbc_logit.py)
CONSTANT_EN = {"Die Konstante": "The constant", "„Keine davon“": "“None of these”",
               "Die Schwelle „kommt nicht infrage“": "The threshold “out of the question”"}


def summarise(sets, coding, attrs, persons, prices, price_wanted=False, constant="Die Konstante"):
    """Fit and describe the model for the results page (dict). attrs: kind.attributes() rows; constant: what the
    alternative with the constant is called in a warning ("„Keine davon“", "Die Schwelle …")."""
    separated = coding.separated(sets)
    free = undetermined(sets, coding.k)
    side = coding.constant_side(sets)
    named = bool(separated) or bool(free.any()) or side is not None  # causes the page can name
    ridge = named
    info = {}
    beta, cov, ll, converged, iterations = fit(sets, coding.k, ridge=coding.penalty() if ridge else 0.0, info=info)
    if not ridge and (not converged or cov is None or np.max(np.abs(beta), initial=0) > 15):
        ridge = True  # separation the counts do not show (e.g. a combination of levels always ahead)
        beta, cov, ll, converged, iterations = fit(sets, coding.k, ridge=coding.penalty(), info=info)
    q = critical(info)  # 1.96, or t(G - 1) for the sandwich over few persons
    pw = coding.part_worths(beta, cov)
    # Attributes whose values the weak prior sets, not the data: one with a separated level (its other levels are
    # centred on a mean that holds the bounded value, so only their distances to each other are estimated) and
    # one the choices do not determine. They get no standard errors and no willingness to pay: the sandwich
    # would report almost no uncertainty for a number that moves with the strength of the prior.
    loose = {a for a, _ in separated} | {a for a in coding.effects
                                         if free[coding.offsets[a]:coding.offsets[a] + coding.levels[a] - 1].any()}
    for a in loose:
        pw[a] = (pw[a][0], [None] * coding.levels[a])
    warnings, ridge_warning = [], None
    if persons < MIN_PERSONS:
        if persons == 1:
            warnings.append(de_en("Nur {n} Person: Die Schätzung ist unsicher (Richtwert ab {m}).",
                                  "Only {n} person: the estimation is uncertain (guideline: from {m}).",
                                  n=persons, m=MIN_PERSONS))
        else:
            warnings.append(de_en("Nur {n} Personen: Die Schätzung ist unsicher (Richtwert ab {m}).",
                                  "Only {n} people: the estimation is uncertain (guideline: from {m}).",
                                  n=persons, m=MIN_PERSONS))
    if not converged:
        warnings.append(de_en("Das Modell ist nicht konvergiert; die Werte sind nicht verlässlich.",
                              "The model has not converged; the values are not reliable."))
    if cov is None:
        warnings.append(de_en("Keine Standardfehler: Das Modell ist nicht identifiziert (z. B. zu wenige Daten, "
                              "ein Preis ohne Streuung oder ein Merkmal, das nie variiert hat).",
                              "No standard errors: the model is not identified (e.g. too little data, a price without "
                              "variation or an attribute that never varied)."))
    elif not info.get("robust"):
        warnings.append(de_en("Die Standardfehler sind modellbasiert; eine clusterrobuste Schätzung je Person war nicht möglich. Wiederholte Wahlen einer Person können die Unsicherheit größer machen als hier angegeben.",
                              "Standard errors are model-based; cluster-robust estimation per person was not possible. Repeated choices by one person can make uncertainty larger than reported here."))
    if separated:
        names = "; ".join(f"{attrs[a]['label']}: {attrs[a]['level_labels'][lv]}" for a, lv in separated)
        # the page adds the source of the regularisation to this one (templates/surveys/_choice_model.html)
        ridge_warning = de_en("Nie oder immer gewählt, wo es einen Unterschied machte ({names}). Der Teilnutzen wäre "
                              "unendlich und ist durch eine schwache Regularisierung begrenzt. Bei diesen Merkmalen sind "
                              "nur die Abstände zwischen den übrigen Stufen geschätzt; sie stehen deshalb ohne "
                              "Standardfehler und ohne Zahlungsbereitschaft. Ihre Werte und die Konstante nur als "
                              "Richtung lesen.",
                              "Never or always chosen where it made a difference ({names}). The part-worth would be "
                              "infinite and is bounded by a weak regularisation. For these attributes only the distances "
                              "between the other levels are estimated; they are therefore shown without standard errors and "
                              "without willingness to pay. Read their values and the constant only as a direction.",
                              names=names)
        warnings.append(ridge_warning)
    shown = coding.shown_levels(sets)
    unshown = [(a, lv) for a in coding.effects for lv in range(coding.levels[a]) if (a, lv) not in shown]
    if unshown:
        names = "; ".join(f"{attrs[a]['label']}: {attrs[a]['level_labels'][lv]}" for a, lv in unshown)
        warnings.append(de_en("Nie gezeigt ({names}): Diese Stufen lassen sich nicht schätzen. Ihr Merkmal steht ohne "
                              "Standardfehler und ohne Zahlungsbereitschaft, seine Werte sind nur untereinander "
                              "vergleichbar.",
                              "Never shown ({names}): these levels cannot be estimated. Their attribute is shown without "
                              "standard errors and without willingness to pay; its values are only comparable with "
                              "each other.", names=names))
    tied = sorted(loose - {a for a, _ in separated} - {a for a, _ in unshown})
    if tied:
        names = ", ".join(attrs[a]["label"] for a in tied)
        warnings.append(de_en("Nicht bestimmbar ({names}): Die Stufen traten nur in festen Kombinationen auf, sodass "
                              "die Wahlen ihre Wirkung nicht trennen können. Die Werte sind eine willkürliche "
                              "Aufteilung, ohne Standardfehler und ohne Zahlungsbereitschaft; der Versuchsplan braucht "
                              "Aufgaben, die sie trennen.",
                              "Not determinable ({names}): the levels only appeared in fixed combinations, so the "
                              "choices cannot separate their effects. The values are an arbitrary split, without "
                              "standard errors and without willingness to pay; the experimental design needs tasks that separate "
                              "them.", names=names))
    if side:
        rest_de = (": Die Konstante ist nicht bestimmt und durch eine schwache Regularisierung begrenzt (ohne "
                   "Standardfehler). Die Teilnutzen der Merkmale sind davon nicht betroffen.")
        rest_en = (": the constant is not determined and is bounded by a weak regularisation (without standard "
                   "errors). The part-worths of the attributes are not affected.")
        called = de_en(constant, CONSTANT_EN.get(constant, constant))
        if side == "never":
            warnings.append(de_en("{c} wurde nie gewählt" + rest_de, "{c} was never chosen" + rest_en, c=called))
        else:
            warnings.append(de_en("{c} wurde immer gewählt" + rest_de, "{c} was always chosen" + rest_en, c=called))
    if ridge and not named:
        warnings.append(de_en("Einige Kombinationen von Stufen liegen stets vorn oder hinten; die Schätzung ist durch "
                              "eine schwache Regularisierung begrenzt, die Werte nur als Richtung lesen.",
                              "Some combinations of levels are always ahead or behind; the estimation is bounded by a "
                              "weak regularisation, read the values only as a direction."))
    beta_price = se_price = None
    if coding.price is not None:
        beta_price = beta[coding.price] / coding.price_scale
        if free[coding.price]:
            warnings.append(de_en("Der Preis ist nicht von den Merkmalen zu trennen (er änderte sich nur zusammen mit "
                                  "ihnen): keine Zahlungsbereitschaft berechnet.",
                                  "The price cannot be separated from the attributes (it only changed together with "
                                  "them): no willingness to pay computed."))
        elif cov is not None:
            se_price = math.sqrt(cov[coding.price, coding.price]) / coding.price_scale
    elif price_wanted:
        warnings.append(de_en("Der Preis geht nicht ins Modell ein: Alle gezeigten Preise sind gleich.",
                              "The price does not enter the model: all prices shown are the same."))
    # The range of a loose attribute is as arbitrary as its values, and a share of a sum that holds it would move
    # with the prior too (all importances do, not only its own): such attributes get no importance, the others
    # are shares among themselves.
    ranges = {a: max(b) - min(b) for a, (b, _) in pw.items() if a not in loose}
    # a price not separable from the attributes is as loose: no importance and not in the sum (audit 2026-10-23, M7)
    price_loose = coding.price is not None and bool(free[coding.price])
    price_range = (abs(beta_price) * (max(prices) - min(prices))
                   if beta_price is not None and prices and not price_loose else None)
    total = sum(ranges.values()) + (price_range or 0)
    if loose or price_loose:
        names = ", ".join([attrs[a]["label"] for a in sorted(loose)] + ([de_en("Preis", "Price")] if price_loose else []))
        warnings.append(de_en("Ohne Wichtigkeit: {names}. Ihre Spannweite legen nicht die Daten fest, ein Anteil daran "
                              "würde auch die Wichtigkeit aller anderen Merkmale verschieben. Die übrigen Wichtigkeiten "
                              "sind deshalb Anteile nur unter den übrigen Merkmalen.",
                              "Without importance: {names}. Their range is not set by the data; a share of it would "
                              "also shift the importance of all other attributes. The other importances are therefore "
                              "shares among the other attributes only.", names=names))
    z_price = beta_price / se_price if beta_price is not None and se_price else None
    # exactly when |z| > q the Fieller interval of the willingness to pay is bounded (see fieller())
    wtp_ok = z_price is not None and z_price <= -q
    if coding.price is not None and free[coding.price]:
        pass  # said above
    elif beta_price is not None and beta_price >= 0:
        warnings.append(de_en("Der Preiskoeffizient ist nicht negativ: keine Zahlungsbereitschaft berechnet.",
                              "The price coefficient is not negative: no willingness to pay computed."))
    elif beta_price is not None and not wtp_ok:
        warnings.append(de_en("Der Preiskoeffizient ist nicht deutlich negativ{z}: Zahlungsbereitschaft nicht "
                              "bestimmt. Ihr 95-%-Intervall nach Fieller wäre unbeschränkt, sie könnte beliebig groß "
                              "sein.",
                              "The price coefficient is not clearly negative{z}: willingness to pay not determined. Its "
                              "95% interval after Fieller would be unbounded; it could be arbitrarily large.",
                              # the decimal mark of the page (Mathe-Audit 2026-10-06, Z-7)
                              z=(f" (z = {number(z_price, 1, page_lang())})"
                                 if z_price is not None else "")))
    cut = set(separated)

    rows = []
    for a, (b, se) in pw.items():
        levels = []
        for lv in range(len(b)):
            w, lo, hi = wtp(coding, beta, cov, a, lv, q) if wtp_ok and a not in loose else (None, None, None)
            levels.append({"label": attrs[a]["level_labels"][lv], "beta": b[lv], "se": se[lv],
                           "wtp": w, "wtp_lo": lo, "wtp_hi": hi, "separated": (a, lv) in cut,
                           "unshown": (a, lv) in unshown})
        rows.append({"code": attrs[a]["code"], "label": attrs[a]["label"], "ordered": attrs[a].get("ordered"),
                     "importance": ranges[a] / total if total and a in ranges else None,
                     "separated": any(x[0] == a for x in separated), "loose": a in loose, "levels": levels})
    wtps = [abs(lv["wtp"]) for r in rows for lv in r["levels"] if lv["wtp"] is not None]
    null_ll = sum(-math.log(len(s.X)) for s in sets)
    asc = asc_se = None
    if coding.asc is not None:
        asc = beta[coding.asc]
        # under separation the constant moves with the bounded values (and is penalised itself): no standard error
        asc_se = math.sqrt(cov[coding.asc, coding.asc]) if cov is not None and not named else None
    result = {"persons": persons, "sets": len(sets), "converged": converged, "iterations": iterations,
              "ridge": ridge, "wtp_ok": wtp_ok, "wtp_weak": weak_price(z_price, q) if wtp_ok else None,
              "robust": cov is not None and info.get("robust", False),
              "q": q, "t_df": info["clusters"] - 1 if q != Z95 else None,
              "price_centered": bool(coding.price is not None and coding.price_center),
              "loglik": ll, "rho2": 1 - ll / null_ll if null_ll else None, "attributes": rows,
              "asc": asc, "asc_se": asc_se, "wtp_decimals": 0 if wtps and max(wtps) >= 10 else 2,
              "price": {"beta": beta_price, "se": se_price, "unit": price_unit(prices),
                        "importance": price_range / total if price_range is not None and total else None}
              if beta_price is not None else None,
              "warnings": warnings, "ridge_warning": ridge_warning}
    result["charts"] = charts(result)
    return result


def _nice(x):
    """A round number >= x for the axis end (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8 x 10^k)."""
    if x <= 0:
        return 1.0
    e = math.floor(math.log10(x))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * 10 ** e >= x - 1e-12:
            return m * 10 ** e
    return 10 ** (e + 1)


def charts(model, width=720, label_w=190, value_w=52, row_h=24, bar_h=14):
    """Geometry of zero-centred horizontal bar charts, one per attribute, with one common x-scale:
    bars from the zero axis to the part-worth (left negative, right positive) and whiskers for
    +/- q SE (model["q"], 1.96 when missing). model["attributes"] (and "price_levels" if present) as from
    summarise(). Returns [{"label", "height", "axis_x", "ticks": [(x, value)], "rows": [{...}], "narrow": {...}}]
    in SVG pixels; "narrow" is the same chart for a phone (UI audit 2026-10-07, W59): 360 wide, the label above
    its bar, the zero line in the middle, so nothing has to be scrolled sideways."""
    # "ordered" as ticked in the editor (None: as the labels read, _ordered)
    groups = [(a["label"], a["levels"], a.get("ordered")) for a in model["attributes"]]
    if model.get("price_levels"):
        groups.append((model["price_levels"]["label"], model["price_levels"]["levels"], True))
    q = model.get("q") or 1.96
    reach = [abs(lv["beta"]) + q * (lv["se"] or 0) for _, levels, _o in groups for lv in levels]
    top = _nice(max(reach, default=0))
    out = []
    for n, (label, levels, ordered) in enumerate(groups):
        # ordered attributes (the price, levels with numbers: 50 €, 10 km) keep the order of their levels, so the
        # course shows; nominal ones stand by value, largest on top (Ralf, 2026-10-01). The tables below follow
        # chart["order"] (UI/UX audit 2026-10-07, W58: bar and row in the same place)
        price = n >= len(model["attributes"])
        order = list(range(len(levels)))
        if not (price or (_ordered(levels) if ordered is None else ordered)):
            order.sort(key=lambda k: -levels[k]["beta"])
        shown = [levels[k] for k in order]
        chart = _bars(shown, q, top, width, label_w, value_w, row_h, bar_h, above=False)
        chart.update(label=label, order=order)
        chart["narrow"] = _bars(shown, q, top, 360, 0, 44, 40, bar_h, above=True)
        out.append(chart)
    return out


def levels_look_ordered(labels):
    """Do the level labels read as a scale: each with a number (prices, distances, durations), or all but one open
    end at the start or the end ("unbegrenzt", "monatlich kündbar"; UI audit 2026-10-12, U23)?"""
    import re

    numbered = [bool(re.search(r"\d", str(label or ""))) for label in labels]
    if len(numbered) < 2:
        return bool(numbered) and all(numbered)
    return all(numbered) or (sum(numbered) >= 2 and numbered.count(False) == 1 and not (numbered[0] and numbered[-1]))


def _ordered(levels):
    return levels_look_ordered([lv.get("label") for lv in levels])


def wrap_label(label, chars, max_lines=3):
    """A level label broken at spaces into lines of about `chars` characters for the bar charts, at most
    `max_lines`; only a longer rest is cut with "…" (UI audit 2026-10-12, U24: wrap instead of cutting)."""
    words, lines, line = str(label or "").split(), [], ""
    for word in words:
        while len(word) > chars:  # one long word: hard break
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:chars])
            word = word[chars:]
        if line and len(line) + 1 + len(word) > chars:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line or not lines:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: chars - 1].rstrip() + "…"
    return lines


def _bars(levels, q, top, width, label_w, value_w, row_h, bar_h, above, line_h=15):
    """One chart of charts(): labels left of the bars, or (above=True) on a line of their own over each bar.
    Long labels wrap onto up to three lines (UI audit 2026-10-12, U24); a row grows by one line_h per extra line."""
    x0, x1 = label_w + value_w, width - value_w
    cx, half = (x0 + x1) / 2, (x1 - x0) / 2
    scale = half / top
    rows = []
    chars = 44 if above else 30
    top_y = 6
    # the levels in their own order, as in the table below (UI audit 2026-10-07, W58): ordered attributes such
    # as price keep their course, and the eye finds the same row in bar and table
    for lv in levels:
        lines = wrap_label(lv["label"], chars)
        extra = (len(lines) - 1) * line_h
        if above:  # the lines above the bar, the last one where a single line stood
            y = top_y + 18 + extra
            first = y - 9 - extra
        else:  # the lines centred on the bar
            y = top_y + extra / 2
            first = y + bar_h / 2 - extra / 2
        top_y += row_h + extra
        w = abs(lv["beta"]) * scale
        x = cx if lv["beta"] >= 0 else cx - w
        lo = hi = None
        if lv["se"] is not None:
            lo, hi = cx + (lv["beta"] - q * lv["se"]) * scale, cx + (lv["beta"] + q * lv["se"]) * scale
        end = max(cx + w, hi or 0) if lv["beta"] >= 0 else min(cx - w, lo if lo is not None else cx)
        rows.append({"label": lv["label"], "beta": lv["beta"], "positive": lv["beta"] >= 0,
                     "x": round(x, 1), "w": round(max(w, 0.5), 1), "y": y, "h": bar_h, "mid": y + bar_h / 2,
                     "label_y": first,
                     "lines": [(text, round(first + k * line_h, 1)) for k, text in enumerate(lines)],
                     "lo": None if lo is None else round(max(lo, x0 - value_w + 4), 1),
                     "hi": None if hi is None else round(min(hi, x1 + value_w - 4), 1),
                     "text_x": round(end + 4 if lv["beta"] >= 0 else end - 4, 1)})
    height = top_y + 18
    ticks = [(round(cx + f * half, 1), f * top) for f in (-1, -0.5, 0, 0.5, 1)]
    return {"rows": rows, "height": height, "axis_x": round(cx, 1), "plot": (x0, x1), "label_w": label_w,
            "width": width, "ticks": ticks, "tick_y": height - 4, "above": above}


def responses_for(item, table):
    """The responses of the persons in the (filtered) export table, in table order (by pid)."""
    from app.extensions import db
    from app.models import Response

    pids = [r.get("pid") for r in table if r.get("pid")]
    if not pids:
        return []
    by_pid = getattr(table, "responses", None)  # export.Table: the participations the rows were built from
    if by_pid is None:
        by_pid = {}
        for start in range(0, len(pids), 500):  # in portions: a statement takes a limited number of values
            by_pid.update({r.pid: r for r in db.session.scalars(db.select(Response).filter(
                Response.survey_id == item.block.survey_id, Response.pid.in_(pids[start:start + 500])))})
    return [by_pid[pid] for pid in pids if pid in by_pid]


def once(table, key, build):
    """build(), kept with the table if it is an export.Table (so the model and the HB status of one page share
    it), else just built. The result must not be changed by the callers."""
    keep = getattr(table, "once", None)
    return keep(key, build) if keep else build()
