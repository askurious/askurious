"""Scales of a survey: which statements form a scale, the score of a person, reliability and correlations.

A scale is a Likert matrix or a semantic differential with at least two scored statements and the measurement
level "metric" (alpha, omega and means assume metric answers): one scale over all statements or, for an instrument
with subscales (BFI-10, IE-4, ...), one per subscale. The results page, the scale scores in the data export and the
table for manuscripts all read this module, so they report the same numbers.

The score of a person is the mean of their answers to the statements of the scale (reverse items are recoded in
the export rows already). A statement a person did not see (app/rows.py, -77) is left out: the score is the mean
over the statements they saw (proration, Mazza et al., 2015), from MIN_SHOWN statements on. A statement that was
shown and not answered (or answered "weiß nicht") leaves the score empty, with or without conditions: proration
is only for what the design left out, not for what a person skipped. Without conditions this is the complete
cases rule alpha and omega rest on as well. The live feedback (Einordnung, Live-Vergleich, Menschenmenge,
2D-Streubild) takes the score of a stored answer with the same rule (answer_score).

McDonald's omega (McDonald, 1999) is computed from a one-factor model fitted by maximum likelihood to the
covariances of the complete cases (Joreskog, 1967): omega = (sum of loadings)^2 / ((sum of loadings)^2 + sum of
unique variances), the denominator being the variance of the sum the model implies (see mcdonald_omega). Unlike
alpha it does not assume that every statement measures the scale equally well (Hayes & Coutts, 2020). With two
statements a one-factor model is not identified: no omega."""
import math
import statistics

from app import units
from app.lang import de_en
from app.rows import NOT_SHOWN

CRONBACH = ("Cronbach, L. J. (1951). Coefficient alpha and the internal structure of tests. Psychometrika, 16(3), "
            "297–334. https://doi.org/10.1007/BF02310555")
MCDONALD = "McDonald, R. P. (1999). Test theory: A unified treatment. Lawrence Erlbaum Associates."
HAYES = ("Hayes, A. F., & Coutts, J. J. (2020). Use omega rather than Cronbach's alpha for estimating reliability. "
         "But…. Communication Methods and Measures, 14(1), 1–24. https://doi.org/10.1080/19312458.2020.1718629")
JORESKOG = ("Jöreskog, K. G. (1967). Some contributions to maximum likelihood factor analysis. Psychometrika, "
            "32(4), 443–482. https://doi.org/10.1007/BF02289658")
SOURCES = (CRONBACH, MCDONALD, HAYES, JORESKOG)
# the correlation of scale scores (pearson()) and its test
PEARSON = ("Pearson, K. (1895). Note on regression and inheritance in the case of two parents. Proceedings of the Royal "
           "Society of London, 58, 240–242. https://doi.org/10.1098/rspl.1895.0041")
FISHER = ("Fisher, R. A. (1915). Frequency distribution of the values of the correlation coefficient in samples from an "
          "indefinitely large population. Biometrika, 10(4), 507–521. https://doi.org/10.2307/2331838")
CORRELATION_SOURCES = (PEARSON, FISHER)
# two statements: the Spearman-Brown coefficient instead of alpha; scales in a loop: passes of a person are not
# independent cases
EISINGA = ("Eisinga, R., te Grotenhuis, M., & Pelzer, B. (2013). The reliability of a two-item scale: Pearson, "
           "Cronbach, or Spearman-Brown? International Journal of Public Health, 58(4), 637–642. "
           "https://doi.org/10.1007/s00038-012-0416-3")
GELDHOF = ("Geldhof, G. J., Preacher, K. J., & Zyphur, M. J. (2014). Reliability estimation in a multilevel "
           "confirmatory factor analysis framework. Psychological Methods, 19(1), 72–91. https://doi.org/10.1037/a0032138")


def scored_codes(item):
    """The export columns of a scale item that belong to the scale: without attention checks (rows of the matrix,
    no part of the scale) and without statements made from the options of an earlier question (app/carry.py: a
    battery of questions about different things)."""
    cols = [c for c, _ in item.kind.export_columns(item)]
    if item.type != "likert":
        return cols
    made = {item.kind.statement_code(item, i, s) for i, s in enumerate(item.config.get("statements", [])) if s.get("src")}
    checks = set(item.kind.check_codes(item))
    return [c for c in cols if c not in checks and c not in made]


def groups(item):
    """[(subscale name or None, [codes])]: the scales of an item, [] if it has none (not a scale type, fewer than
    two scored statements, or not measured as metric)."""
    if item.kind.result_kind != "scale":
        return []
    scored = scored_codes(item)
    if len(scored) < 2 or item.kind.level(item) != "metric":
        return []
    return subscale_groups(item, scored) or [(None, scored)]


def subscale_groups(item, scored=None):
    """[(subscale name, [codes])] of an instrument with subscales (BFI-10, IE-4, ...), [] without. Only scored
    statements: an attention check or a carried statement with a subscale (possible from a survey file) is no part
    of it."""
    scored = set(scored_codes(item) if scored is None else scored)
    subs = {}
    for i, st in enumerate(item.config.get("statements", []) if item.type == "likert" else []):
        code = item.kind.statement_code(item, i, st)
        if st.get("subscale") and code in scored:
            subs.setdefault(st["subscale"], []).append(code)
    for i, pair in enumerate(item.config.get("pairs", []) if item.type == "semantic" else []):
        code = item.kind.pair_code(item, i, pair)
        if pair.get("subscale") and code in scored:
            subs.setdefault(pair["subscale"], []).append(code)
    return list(subs.items())


MIN_SHOWN = 2  # a score over the statements a person saw needs at least this many of them


def subscale_warnings(item):
    """Hints for a matrix whose statements (pairs) belong to subscales: a subscale needs two scored statements for a
    score and its reliability, and a scored statement without a subscale counts in no score at all (the scales of
    such an item are its subscales, subscale_groups)."""
    found = subscale_groups(item)
    if not found:
        return []
    pairs = item.type == "semantic"
    # its value is then that one answer: shown and exported as such, but without a reliability
    # (audit 2026-10-17, O2: the hint said there was no score at all)
    out = [(de_en("Subskala „{name}“ hat nur ein Gegensatzpaar: Ihr Wert ist diese eine Antwort, eine "
                  "Reliabilität lässt sich nicht schätzen. Für eine Skala mindestens zwei Paare zuordnen.",
                  "Subscale “{name}” has only one pair of opposites: its value is that one answer, and "
                  "no reliability can be estimated. Assign at least two pairs for a scale.",
                  name=name) if pairs else
            de_en("Subskala „{name}“ hat nur eine Aussage: Ihr Wert ist diese eine Antwort, eine "
                  "Reliabilität lässt sich nicht schätzen. Für eine Skala mindestens zwei Aussagen zuordnen.",
                  "Subscale “{name}” has only one statement: its value is that one answer, and no "
                  "reliability can be estimated. Assign at least two statements for a scale.",
                  name=name)) for name, codes in found if len(codes) < MIN_SHOWN]
    assigned = {c for _, codes in found for c in codes}
    loose = [c for c in scored_codes(item) if c not in assigned]
    if loose:
        codes = ", ".join(loose[:5]) + (" …" if len(loose) > 5 else "")
        out.append(de_en("{n} Gegensatzpaar(e) ohne Subskala ({codes}) zählen in keinen Skalenwert.",
                         "{n} pair(s) of opposites without a subscale ({codes}) count towards no scale score.",
                         n=len(loose), codes=codes) if pairs else
                   de_en("{n} Aussage(n) ohne Subskala ({codes}) zählen in keinen Skalenwert.",
                         "{n} statement(s) without a subscale ({codes}) count towards no scale score.",
                         n=len(loose), codes=codes))
    return out


def subscale_from_form(form, prefix, row_id, before):
    """The subscale of one statement (pair) from the editor: the field "<prefix>_subscale" of its row while the box
    "subscales" is ticked, none when it is not. A form without the editor's marker "subscales_shown" (other code
    that sends a matrix) keeps what the row had."""
    if not form.get("subscales_shown"):
        return (before or {}).get("subscale")
    if not form.get("subscales"):
        return None
    ids, names = form.getlist(f"{prefix}_id"), form.getlist(f"{prefix}_subscale")
    name = dict(zip(ids, names)).get(row_id, "")
    return " ".join(name.split())[:100] or None


def _missing(item, codes):
    return {c: {str(m) for m in item.kind.missing_codes(item, c)} for c in codes}


def values(item, codes, row, suffix="", hidden=None):
    """The answers of one row (one loop pass: suffix "_<n>") to the statements, None where there is none or it
    counts as missing ("weiß nicht", -77 not shown)."""
    from app.results import valid_number

    hidden = _missing(item, codes) if hidden is None else hidden
    return [valid_number(row.get(c + suffix), hidden[c]) for c in codes]


def shown(item, codes, row, suffix="", hidden=None):
    """Per statement: was it shown to the person of this row? False only for -77 in the column of a statement
    with a condition (app/rows.py)."""
    from app.results import _key

    hidden = _missing(item, codes) if hidden is None else hidden
    unseen = str(NOT_SHOWN)
    return [not (unseen in hidden[c] and row.get(c + suffix) not in (None, "") and _key(row.get(c + suffix)) == unseen)
            for c in codes]


def partial(item, codes):
    """Do some statements count -77 as missing (shown only under a condition)? Then a score is the mean over the
    statements seen."""
    return any(_missing(item, codes).values())


def score(vals, seen=None):
    """The score of one person from values() and shown(): the mean over the statements shown, None if one of them
    has no answer or fewer than MIN_SHOWN were shown. Without seen every statement counts as shown (complete
    rows only)."""
    seen = [True] * len(vals) if seen is None else seen
    given = [v for v, s in zip(vals, seen) if s]
    if len(given) < min(MIN_SHOWN, len(vals)) or not given or any(v is None for v in given):
        return None
    return statistics.fmean(given)


def row_score(item, codes, row, suffix="", hidden=None):
    """The score of one export row (one loop pass) over `codes`."""
    hidden = _missing(item, codes) if hidden is None else hidden
    return score(values(item, codes, row, suffix, hidden), shown(item, codes, row, suffix, hidden))


def subscale_names(item):
    """The names of the subscales of a Likert matrix or a semantic differential, [] without."""
    return [name for name, _ in subscale_groups(item)] if item.type in ("likert", "semantic") else []


def feedback_codes(item, subscale=None):
    """The statements the live feedback places a person on: those of the chosen subscale (the first one if none
    or an unknown one is chosen; subscales measure different things, a mean over all of them would mix them);
    every scored statement of an item without subscales."""
    found = subscale_groups(item) if item.type in ("likert", "semantic") else []
    if found:
        return dict(found).get(subscale) or found[0][1]
    return scored_codes(item)


def answer_score(item, answer, subscale=None):
    """The score of one stored answer of a Likert matrix or a semantic differential (reverse items recoded): the
    mean over the statements of feedback_codes with the rule of the results page (row_score)."""
    if not isinstance(answer, dict):
        return None
    codes = feedback_codes(item, subscale)
    return row_score(item, codes, item.kind.export_values(item, answer)) if codes else None



def _fit_state(s, psi):
    """For unique variances psi: the loadings that are maximum likelihood given them (first eigenvector of
    Psi^-1/2 S Psi^-1/2, Joreskog, 1967), the discrepancy F = log|Sigma| + tr(S Sigma^-1), its gradient in psi (the
    loadings adapt, so only the direct part counts) and Sigma^-1. Gradient None if Sigma is not positive definite."""
    import numpy as np

    root = np.sqrt(psi)
    vals, vecs = np.linalg.eigh(s / np.outer(root, root))
    lam = root * vecs[:, -1] * math.sqrt(max(vals[-1] - 1, 0.0))
    sigma = np.outer(lam, lam) + np.diag(psi)
    sign, logdet = np.linalg.slogdet(sigma)
    if sign <= 0:
        return lam, math.inf, None, None
    inv = np.linalg.inv(sigma)
    return lam, logdet + float(np.sum(s * inv)), np.diag(inv @ (sigma - s) @ inv), inv


def _gradients(s, psis):
    """The gradient of _fit_state for each row of unique variances in psis, computed as one stack of matrices; None
    if Sigma is not positive definite for one of them."""
    import numpy as np

    root = np.sqrt(psis)
    vals, vecs = np.linalg.eigh(s[None, :, :] / (root[:, :, None] * root[:, None, :]))
    lam = root * vecs[:, :, -1] * np.sqrt(np.maximum(vals[:, -1] - 1, 0.0))[:, None]
    sigma = lam[:, :, None] * lam[:, None, :] + psis[:, :, None] * np.eye(len(s))[None, :, :]
    sign, _ = np.linalg.slogdet(sigma)
    if np.any(sign <= 0):
        return None
    inv = np.linalg.inv(sigma)
    return np.diagonal(inv @ (sigma - s[None, :, :]) @ inv, axis1=1, axis2=2)


def _fit_psi(s, psi, lo, hi, steps=100, expected=False):
    """Minimise F over the unique variances in [lo, hi]: Newton steps on the concentrated discrepancy (Hessian from
    differences of the gradient), a line search, a variance at a bound stays there while the gradient pushes out.
    (loadings, unique variances, F) once the gradient vanishes, None if that is not reached.
    expected: steps with the expected information (Fisher scoring) instead of the differenced Hessian: one matrix
    decomposition per step instead of two per free variance. For a start next to the optimum (the bootstrap of
    app/intervals.py starts each draw from the fit of the whole sample) that reaches the same stationary point
    much faster; the stopping rule is the same."""
    import numpy as np

    lam, f, grad, inv = _fit_state(s, psi)
    if grad is None:
        return None
    for _ in range(steps):
        free = np.flatnonzero(~(((psi <= lo) & (grad > 0)) | ((psi >= hi) & (grad < 0))))
        if np.max(np.abs(grad[free] * psi[free]), initial=0.0) < 1e-10:
            return lam, psi, f
        if expected:
            hess = (inv * inv)[np.ix_(free, free)]
            step = np.zeros_like(psi)
            step[free] = -np.linalg.solve(hess, grad[free])
            t = 1.0
            for _ in range(50):
                new_psi = np.clip(psi + t * step, lo, hi)
                new = _fit_state(s, new_psi)
                if new[2] is not None and new[1] <= f:
                    break
                t /= 2
            else:
                return None
            stalled = np.max(np.abs(new_psi - psi) / hi) < 1e-12
            psi, (lam, f, grad, inv) = new_psi, new
            if stalled:
                free = ~(((psi <= lo) & (grad > 0)) | ((psi >= hi) & (grad < 0)))
                return (lam, psi, f) if np.max(np.abs(grad[free] * psi[free]), initial=0.0) < 1e-7 else None
            continue
        # central differences of the gradient, all 2 x free shifted points in one batch of matrices (the same
        # operations as _fit_state, stacked: one call of each instead of one per point)
        d = 1e-5 * psi[free]
        shifted = np.repeat(psi[None, :], 2 * len(free), axis=0)
        shifted[np.arange(len(free)), free] += d
        shifted[len(free) + np.arange(len(free)), free] -= d
        grads = _gradients(s, shifted)
        hess = None
        if grads is not None:
            hess = ((grads[:len(free)] - grads[len(free):])[:, free] / (2 * d)[:, None]).T
        try:
            hess = (hess + hess.T) / 2
            np.linalg.cholesky(hess)
        except (TypeError, np.linalg.LinAlgError):
            hess = (inv * inv)[np.ix_(free, free)]  # expected information where the Hessian is not positive definite
        step = np.zeros_like(psi)
        step[free] = -np.linalg.solve(hess, grad[free])
        t = 1.0
        for _ in range(50):
            new_psi = np.clip(psi + t * step, lo, hi)
            new = _fit_state(s, new_psi)
            if new[2] is not None and new[1] <= f:
                break
            t /= 2
        else:
            return None
        stalled = np.max(np.abs(new_psi - psi) / hi) < 1e-12
        psi, (lam, f, grad, inv) = new_psi, new
        if stalled:  # no further progress: accept only a (nearly) stationary point
            free = ~(((psi <= lo) & (grad > 0)) | ((psi >= hi) & (grad < 0)))
            return (lam, psi, f) if np.max(np.abs(grad[free] * psi[free]), initial=0.0) < 1e-7 else None
    return None


SCORING_K = 12  # below, the differenced Hessian converges in fewer steps than the scoring saves (measured)


def _one_factor(s, start=None):
    """(loadings, unique variances) of the one-factor model fitted by maximum likelihood to the covariance matrix s,
    None if there is no trustworthy solution. start: unique variances to begin with (the bootstrap of
    app/intervals.py starts from the fit of the whole sample: a few Newton steps instead of two full searches)."""
    import numpy as np

    k = len(s)
    var = np.diag(s).copy()
    if k == 3:
        # just identified: lambda_i^2 = s_ij s_ik / s_jk reproduces S exactly. Covariances whose signs no single
        # factor produces (product not positive) put the maximum on a boundary that depends on which statement
        # becomes the factor, with nearly equal fit and very different omega: not estimable.
        if s[0, 1] * s[0, 2] * s[1, 2] <= 0:
            return None
        first = math.sqrt(s[0, 1] * s[0, 2] / s[1, 2])
        lam = np.array([first, s[0, 1] / first, s[0, 2] / first])
        if np.all(lam ** 2 < var):
            return lam, var - lam ** 2
        # Heywood case (a loading above the statement's standard deviation): the maximum is on the boundary below
    found = []
    lo, hi = 1e-9 * var, var
    starts = [0.5 * var]
    try:
        starts.insert(0, np.clip((1 - 0.5 / k) / np.diag(np.linalg.inv(s)), lo, hi))
    except np.linalg.LinAlgError:
        pass
    if start is not None:
        # from SCORING_K statements on, Fisher scoring from the start (much faster for long scales: 44 statements,
        # 10 000 cases took 15 s per bootstrap; the same optimum, audit 2026-10-05); no convergence: the full search
        scoring = k >= SCORING_K
        fit = _fit_psi(s, np.clip(start, lo, hi), lo, hi, steps=500 if scoring else 100, expected=scoring)
        if fit:
            found.append(fit)
            starts = []
    for psi in starts:
        fit = _fit_psi(s, psi, lo, hi)
        if fit:
            found.append(fit)
    for i in range(k):
        # boundary solutions (Heywood): statement i without unique variance is the factor itself, the others are
        # regressed on it; a candidate if the gradient would push its unique variance below zero
        lam = s[:, i] / math.sqrt(s[i, i])
        psi = var - lam ** 2
        psi[i] = 0.0
        sigma = np.outer(lam, lam) + np.diag(psi)
        sign, logdet = np.linalg.slogdet(sigma)
        if sign > 0:
            inv = np.linalg.inv(sigma)
            if (inv @ (sigma - s) @ inv)[i, i] >= 0:
                found.append((lam, psi, logdet + float(np.sum(s * inv))))
    if not found:
        return None
    lam, psi, _ = min(found, key=lambda fit: fit[2])
    return lam, psi


def mcdonald_omega(matrix, start=None):
    """McDonald's omega for rows of complete answers, from a one-factor model fitted by maximum likelihood to the
    covariance matrix (n - 1; omega does not depend on it): omega = (sum lambda)^2 / ((sum lambda)^2 + sum psi). The
    denominator is the variance of the sum the model implies, not the observed one (1'S1; McDonald, 1999);
    the two differ only where the model does not fit. With one factor this omega is omega_h = omega_t: the
    reliability of the unweighted sum from a congeneric model, not the omega_total of a bifactor model.

    Three statements: the closed solution (just identified), a Heywood case takes the boundary solution. From four
    on: Newton steps on the unique variances (Joreskog, 1967) plus the boundary solutions; the best fit wins.
    None below three statements (not identified), with fewer cases than statements plus two, when a statement does
    not vary, and when there is no trustworthy solution (three statements with covariances no single factor can
    produce, or no convergence): the page shows "nicht schätzbar"."""
    import numpy as np

    n = len(matrix)
    k = len(matrix[0]) if n else 0
    if k < 3 or n < k + 2:
        return None
    return omega_of_cov(np.cov(np.asarray(matrix, dtype=float), rowvar=False), start)


def omega_of_cov(s, start=None):
    """mcdonald_omega from the covariance matrix of the answers (the bootstrap of app/intervals.py weighs the rows
    of a draw instead of copying them); None where a statement does not vary or there is no trustworthy fit."""
    import numpy as np

    if np.any(np.diag(s) <= 0):
        return None
    fit = _one_factor(s, start)
    if fit is None:
        return None
    lam, psi = fit
    if lam.sum() < 0:  # the sign of a factor is arbitrary; the scale's direction is that of most statements
        lam = -lam
    common = lam.sum() ** 2
    return float(common / (common + psi.sum()))


# Below max(OMEGA_STABLE, 3k) cases the maximum likelihood fit can end in a local optimum (audit 2026-10-05: k = 8,
# n = 10 gave 0.778 instead of 0.737). Omega is still shown, with "kleine Stichprobe" next to it; the bound matches
# the bootstrap interval, which also starts at 30 (intervals.MIN_BOOT)
OMEGA_STABLE = 30


def omega_small(n, k):
    """True if omega of k statements from n complete cases counts as a small-sample estimate."""
    return bool(n) and n < max(OMEGA_STABLE, 3 * k)


# Item analysis (concept statistik-erweiterungen-konzept.md, section 3). What is highlighted follows the usual rules of
# thumb and is a setting of Askurious (Festlegung von Askurious), not of the source
ITEM_RIT_LOW = 0.30
LIENERT = "Lienert, G. A., & Raatz, U. (1998). Testaufbau und Testanalyse (6. Aufl.). Beltz, Psychologie Verlags Union."


def reversed_codes(item):
    """The export columns of the reverse statements (pairs) of a Likert matrix or a semantic differential."""
    if item.type == "likert":
        return {item.kind.statement_code(item, i, s) for i, s in enumerate(item.config.get("statements", []))
                if s.get("reverse")}
    if item.type == "semantic":
        return {item.kind.pair_code(item, i, p) for i, p in enumerate(item.config.get("pairs", [])) if p.get("reverse")}
    return set()


def item_range(item, code):
    """(lowest, highest) possible answer of a statement, from the levels of its scale; None if unknown."""
    try:
        levels = [float(v) for v in (item.kind.result_column(item, code)[2] or {})]
    except (TypeError, ValueError):
        return None
    return (min(levels), max(levels)) if len(levels) > 1 else None


def item_analysis(matrix, ranges=None):
    """Per statement of a scale (rows of complete answers, reverse statements recoded): n, M, SD (n - 1), difficulty
    P = (M - min) / (max - min) * 100 over the possible range ((min, max) per column in `ranges`, None if unknown),
    corrected item-total correlation r_it (with the sum of the other statements), alpha without the statement (from
    three statements on) and the standardized loading of the one-factor model omega rests on (from three statements
    on). [] below three rows."""
    import numpy as np

    n = len(matrix)
    k = len(matrix[0]) if n else 0
    if n < 3 or k < 2:
        return []
    x = np.asarray(matrix, dtype=float)
    total = x.sum(axis=1)
    loadings = [None] * k
    if k >= 3 and n >= k + 2:
        s = np.cov(x, rowvar=False)
        fit = _one_factor(s) if np.all(np.diag(s) > 0) else None
        if fit is not None:
            lam = fit[0] if fit[0].sum() >= 0 else -fit[0]  # the direction of most statements, as for omega
            loadings = [float(v) for v in lam / np.sqrt(np.diag(s))]
    out = []
    for j in range(k):
        col, rest = x[:, j], total - x[:, j]
        mean, sd = float(col.mean()), float(col.std(ddof=1))
        lo, hi = (ranges[j] if ranges and ranges[j] else None) or (None, None)
        rit = float(np.corrcoef(col, rest)[0, 1]) if col.std() > 0 and rest.std() > 0 else None
        without = None
        if k > 2:  # as results.cronbach_alpha, on the array (that one is exact but slow for thousands of rows)
            others = np.delete(x, j, axis=1)
            total_var = float(others.sum(axis=1).var(ddof=1))
            if total_var > 0:
                without = (k - 1) / (k - 2) * (1 - float(others.var(axis=0, ddof=1).sum()) / total_var)
        out.append({"n": n, "mean": mean, "sd": sd, "rit": rit, "alpha_without": without, "loading": loadings[j],
                    "p": 100 * (mean - lo) / (hi - lo) if lo is not None and hi is not None and hi > lo else None})
    return out


def spearman_brown(matrix):
    """The reliability of a scale of two statements (Eisinga et al., 2013): 2r / (1 + r) from the Pearson r of the
    two (rows of complete answers); None without two columns, below three rows or without variance."""
    if not matrix or len(matrix[0]) != 2:
        return None
    r, _, _ = pearson([row[0] for row in matrix], [row[1] for row in matrix])
    if r is None or r <= -1:
        return None
    return 2 * r / (1 + r)


def pearson(xs, ys):
    """(r, n, p) over the pairs where both have a value; r and p None below three pairs or without variance.
    p two-sided from the t distribution with n - 2 degrees of freedom."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None
             and math.isfinite(x) and math.isfinite(y)]
    n = len(pairs)
    if n < 3:
        return None, n, None
    # Correlation does not change under a shift or a positive rescaling. First the median of each variable is taken
    # off: near equal large values (9e14 + 1, 9e14 + 2) keep their differences exactly, where scaling first rounded
    # them away (audit storage and calculations 2026-10-28, M5). Only if that overflows (values of both signs near
    # the largest float) it is left out. Then each is normalised, so subnormal and large values stay representable.
    rx = statistics.median_low([x for x, _ in pairs])
    ry = statistics.median_low([y for _, y in pairs])
    shifted = [(x - rx, y - ry) for x, y in pairs]
    if all(math.isfinite(x) and math.isfinite(y) for x, y in shifted):
        pairs = shifted
    ax = max(abs(x) for x, _ in pairs)
    ay = max(abs(y) for _, y in pairs)
    if not ax or not ay:
        return None, n, None
    pairs = [(x / ax, y / ay) for x, y in pairs]
    mx = statistics.fmean(x for x, _ in pairs)
    my = statistics.fmean(y for _, y in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx == 0 or syy == 0:
        return None, n, None
    r = max(-1.0, min(1.0, (sxy / math.sqrt(sxx)) / math.sqrt(syy)))
    df = n - 2
    if abs(r) >= 1 or df == 0:
        return r, n, 0.0 if abs(r) >= 1 and df else None
    t2 = r * r * df / (1 - r * r)
    return r, n, _betainc(df / 2, 0.5, df / (df + t2))


_TINY = 1e-300


def _fraction(part, steps, tol):
    """Value of the continued fraction 1 / (b(1) + a(2) / (b(2) + a(3) / (b(3) + ...))), part(n) = (a(n), b(n)), by
    the modified Lentz method (Lentz, 1976; Thompson & Barnett, 1986), until a step changes it by less than tol.
    The same helper as fraction() in static/samplesize.js (rights review 2026-10-09)."""
    value, num, den = _TINY, _TINY, 0.0
    for n in range(1, steps + 1):
        a_n, b_n = (1.0, part(1)[1]) if n == 1 else part(n)
        den = b_n + a_n * den
        den = 1.0 / (_TINY if abs(den) < _TINY else den)
        num = b_n + a_n / num
        if abs(num) < _TINY:
            num = _TINY
        step = num * den
        value *= step
        if abs(step - 1) < tol:
            break
    return value


def _betainc(a, b, x):
    """Regularized incomplete beta function I_x(a, b) by its continued fraction (Abramowitz & Stegun, 1964, 26.5.8),
    which converges fast below x = (a + 1) / (a + b + 2); above it I_x(a, b) = 1 - I_(1-x)(b, a).
    Mathematical definitions: https://dlmf.nist.gov/8.17.E22 (fraction), .E23 (coefficients), .E4 (symmetry).
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    if x < (a + 1) / (a + b + 2):
        return _beta_fraction(a, b, x)
    return 1.0 - _beta_fraction(b, a, 1 - x)


def _beta_fraction(a, b, x):
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))

    # the partial numerators: d(2k) = k(b - k)x / ((a + 2k - 1)(a + 2k)), d(2k + 1) = -(a + k)(a + b + k)x / ((a + 2k)(a + 2k + 1))
    def coefficient(j):
        k = j // 2
        if j == 0:  # only b(1) of the first step is used
            return 0.0
        if j % 2:
            return -(a + k) * (a + b + k) * x / ((a + 2 * k) * (a + 2 * k + 1))
        return k * (b - k) * x / ((a + 2 * k - 1) * (a + 2 * k))

    return front / a * _fraction(lambda n: (coefficient(n - 1), 1.0), 6001, 1e-15)


# --- scale scores as columns of the data export -----------------------------------------------------------------

def _ascii(name):
    import re
    import unicodedata

    name = name.replace("ß", "ss").replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    name = name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", name.lower())[:16] or "sub"


def export_scales(items, taken):
    """Per item with a scale: [(item, [(column, label, subscale name, codes)])]. The column is <code>_m for one
    scale, <code>_<subscale>_m per subscale; a name that is taken already gets a number."""
    from app.items.base import plain

    out = []
    for item in items:
        found = []
        for name, codes in groups(item):
            base = f"{item.code}_{_ascii(name)}_m" if name else f"{item.code}_m"
            col, n = base, 2
            while col in taken:
                col, n = f"{base}{n}", n + 1
            taken.add(col)
            what = f"Subskala „{name}“" if name else "Skalenwert"
            label = f"{what}: Mittelwert von {', '.join(codes)}"
            question = plain(item.text("question")) if item.text("question") else ""
            if question:
                label += f" ({question[:80]})"
            found.append((col, label, name, codes))
        if found:
            out.append((item, found))
    return out


def export_values(item, found, row, passes):
    """{column: score} for one export row; in a loop one column per pass (<column>_<n>)."""
    out = {}
    for col, _, _, codes in found:
        hidden = _missing(item, codes)
        for suffix, target in ([(f"_{n}", f"{col}_{n}") for n in passes] or [("", col)]):
            v = row_score(item, codes, row, suffix, hidden)
            out[target] = None if v is None else units.round_half_up(v, 4)
    return out


def codebook_note(item, codes, lang=None):
    """The note of a scale score column in the codebook; lang: the reader's language (app.items.base.reader)."""
    from app.items.base import reader

    if reader(lang) == "en":
        return ("mean of the statements " + ", ".join(codes) + "; reversed statements are recoded; "
                + (f"mean over the statements shown (some did not see all); empty if a statement shown is not answered "
                   f"or fewer than {MIN_SHOWN} were shown"
                   if partial(item, codes) else "empty unless all statements are answered (as on the results page)"))
    return ("Mittelwert der Aussagen " + ", ".join(codes) + "; umgepolte Aussagen sind rekodiert; "
            + (f"Mittel über die gezeigten Aussagen (einige sahen nicht alle); leer, wenn eine gezeigte Aussage nicht "
               f"beantwortet ist oder weniger als {MIN_SHOWN} gezeigt wurden"
               if partial(item, codes) else "leer, wenn nicht alle Aussagen beantwortet sind (wie auf der Ergebnisseite)"))
