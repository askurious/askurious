"""Exploratory factor analysis of the statements of a scale (SEM extensions 2026-10-17, point 19): how many factors
by parallel analysis (Horn, 1965; the 95th percentile of the eigenvalues of random data of the same size, Glorfeld,
1995), the loadings by iterated principal axis factoring, rotated obliquely with oblimin (gamma 0, quartimin; Jennrich &
Sampson, 1966), the rotation as in Jennrich (2002) and Bernaards & Jennrich (2005) as a minimisation over factor axes of
unit length, solved here by a quasi-Newton method. Numbers only, numpy.

For self-built scales and as a check of the subscales of an instrument; an exploratory analysis and a confirmatory one
(app/sem.py) on the same data confirm nothing (Fabrigar et al., 1999), the page says so."""
import numpy as np

HORN = ("Horn, J. L. (1965). A rationale and test for the number of factors in factor analysis. Psychometrika, 30(2), "
        "179–185. https://doi.org/10.1007/BF02289447")
GLORFELD = ("Glorfeld, L. W. (1995). An improvement on Horn's parallel analysis methodology for selecting the correct "
            "number of factors to retain. Educational and Psychological Measurement, 55(3), 377–393. "
            "https://doi.org/10.1177/0013164495055003002")
JENNRICH_SAMPSON = ("Jennrich, R. I., & Sampson, P. F. (1966). Rotation for simple loadings. Psychometrika, 31(3), "
                    "313–323. https://doi.org/10.1007/BF02289465")
JENNRICH = ("Jennrich, R. I. (2002). A simple general method for oblique rotation. Psychometrika, 67(1), 7–19. "
            "https://doi.org/10.1007/BF02294706")
BERNAARDS = ("Bernaards, C. A., & Jennrich, R. I. (2005). Gradient projection algorithms and software for arbitrary "
             "rotation criteria in factor analysis. Educational and Psychological Measurement, 65(5), 676–696. "
             "https://doi.org/10.1177/0013164404272507")
FABRIGAR = ("Fabrigar, L. R., Wegener, D. T., MacCallum, R. C., & Strahan, E. J. (1999). Evaluating the use of "
            "exploratory factor analysis in psychological research. Psychological Methods, 4(3), 272–299. "
            "https://doi.org/10.1037/1082-989X.4.3.272")
SOURCES = (HORN, GLORFELD, JENNRICH_SAMPSON, JENNRICH, BERNAARDS, FABRIGAR)

MIN_ITEMS = 4  # an exploratory analysis needs at least four statements
MIN_N = 50  # and at least this many complete cases, and PER_ITEM per statement (Festlegung von Askurious)
PER_ITEM = 5
REPS = 100  # random data sets of the parallel analysis
CAP = 0.995  # communalities are capped here; one at the cap is a Heywood case (the estimate would reach 1 or beyond)
PRIMARY = 0.40  # a loading from here counts as the statement's factor; a second one from CROSS is a cross-loading
CROSS = 0.30


def parallel(x, reps=REPS, seed=1, quantile=95):
    """Eigenvalues of the correlations of x against those of normal random data of the same n and p (the given
    percentile): {"observed", "random", "k"}; k = the leading eigenvalues above the random ones, 0 when not even the
    first lies above chance (audit 2026-10-21, E2: no longer raised to 1 here; analyze() says so)."""
    x = np.asarray(x, dtype=float)
    n, p = x.shape
    observed = np.sort(np.linalg.eigvalsh(np.corrcoef(x, rowvar=False)))[::-1]
    rng = np.random.default_rng(seed)
    found = np.empty((reps, p))
    for r in range(reps):
        found[r] = np.sort(np.linalg.eigvalsh(np.corrcoef(rng.standard_normal((n, p)), rowvar=False)))[::-1]
    random = np.percentile(found, quantile, axis=0)
    k = 0
    while k < p and observed[k] > random[k]:
        k += 1
    return {"observed": observed.tolist(), "random": random.tolist(), "k": k}


def principal_axis(R, k, max_iter=500, tol=1e-7):
    """Iterated principal axis factoring of the correlation matrix R with k factors: (loadings p x k, communalities,
    converged). Communalities start at the squared multiple correlations and are capped at CAP (a Heywood case)."""
    p = R.shape[0]
    try:
        h2 = 1 - 1 / np.diag(np.linalg.inv(R))
    except np.linalg.LinAlgError:
        h2 = np.full(p, 0.5)
    h2 = np.clip(h2, 0.005, CAP)
    converged = False
    for _ in range(max_iter):
        reduced = R.copy()
        reduced[np.diag_indices(p)] = h2
        values, vectors = np.linalg.eigh(reduced)
        order = np.argsort(values)[::-1][:k]
        lam = vectors[:, order] * np.sqrt(np.maximum(values[order], 0.0))
        new = np.clip(np.sum(lam ** 2, axis=1), 0.0, CAP)
        if np.max(np.abs(new - h2)) < tol:
            h2 = new
            converged = True
            break
        h2 = new
    return lam, h2, converged


def _criterion(L):
    """The quartimin criterion (oblimin with gamma 0; Carroll, 1953; Jennrich & Sampson, 1966) of the loadings L and its
    derivative by L. With r_i the sum of the squared loadings of row i, the products of squared loadings over pairs of
    distinct factors give q = sum_i (r_i² - sum_j l_ij⁴) / 4, and dq/dl_ij = l_ij (r_i - l_ij²)."""
    sq = L * L
    row = sq.sum(axis=1, keepdims=True)
    return float(np.sum(row * row) - np.sum(sq * sq)) / 4, L * (row - sq)


def _rotated(A, M):
    """The loadings of A for the factor axes M (columns scaled to unit length, T): L = A (T')⁻¹, with T and (T')⁻¹; None
    when the axes have collapsed onto each other (T almost singular)."""
    T = M / np.linalg.norm(M, axis=0)
    if np.linalg.cond(T) > 1e8:
        return None
    W = np.linalg.inv(T).T
    return A @ W, T, W


def _value_and_slope(A, M):
    """The criterion as a function of the free matrix M and its derivative by M, by the chain rule. With L = A W and
    W = (T')⁻¹: dq = -tr(W G' L dT'), so dq/dT = -W G' L; each column t = m / |m| passes it on as
    (I - t t') (dq/dt) / |m| (the part along m does not change t). (inf, None) where the axes collapse."""
    found = _rotated(A, M)
    if found is None:
        return float("inf"), None
    L, T, W = found
    q, G = _criterion(L)
    by_t = -W @ G.T @ L
    along = np.sum(T * by_t, axis=0)
    return q, (by_t - T * along) / np.linalg.norm(M, axis=0)


SUFFICIENT = 1e-4  # Armijo constant of the line search: a step must lower q by this share of the predicted decrease
SHRINK = 0.5  # the step shrinks by this factor until it does (Nocedal & Wright, 2006, ch. 3: usual choices)
TRIES = 40  # halvings of a step before the search gives up (0.5⁴⁰ ≈ 1e-12 of the first step)


def oblimin(A, max_iter=1000, tol=1e-6):
    """Oblique quartimin rotation of the unrotated loadings A (p x k): (pattern p x k, factor correlations k x k,
    converged). The rotated loadings are L = A (T')⁻¹ for factor axes T with columns of unit length, Φ = T'T (Jennrich,
    2002; Bernaards & Jennrich, 2005). Here T is the column-normalised form of a free matrix M, so the minimum of the
    quartimin criterion over M has no side condition; it is found by a quasi-Newton method (BFGS update of the inverse
    Hessian, Armijo backtracking; Nocedal & Wright, 2006, ch. 3 and 6), starting at the unrotated solution M = I. It has
    converged when the slope, measured on axes of unit length, falls below tol (Frobenius norm)."""
    k = A.shape[1]
    if k == 1:
        return A.copy(), np.eye(1), True
    M = np.eye(k)
    q, slope = _value_and_slope(A, M)
    H = np.eye(k * k)  # inverse Hessian estimate, on vec(M)
    first = True
    converged = False
    for _ in range(max_iter):
        size = np.linalg.norm(M, axis=0)
        if np.linalg.norm(slope * size) < tol:
            converged = True
            break
        g = slope.ravel()
        d = -H @ g
        if g @ d >= 0:  # no descent direction (H has gone astray): start the estimate afresh
            H = np.eye(k * k)
            d = -g
        # first trial step at most a tenth of the size of the axes, then the full quasi-Newton step
        step = min(1.0, 0.1 * float(np.mean(size)) / max(float(np.linalg.norm(d)), 1e-300)) if first else 1.0
        for _ in range(TRIES):
            trial = M + step * d.reshape(k, k)
            q_new, slope_new = _value_and_slope(A, trial)
            if slope_new is not None and q_new <= q + SUFFICIENT * step * (g @ d):
                break
            step *= SHRINK
        else:
            break  # no step lowers q any more: the rounding floor is reached
        s = (trial - M).ravel()
        y = (slope_new - slope).ravel()
        M, q, slope = trial, q_new, slope_new
        sy = float(s @ y)
        if sy > 1e-12 * float(np.linalg.norm(s) * np.linalg.norm(y)):  # curvature condition, else keep H
            if first:
                H = np.eye(k * k) * (sy / float(y @ y))  # scale of the first estimate (Nocedal & Wright, 2006, eq. 6.20)
                first = False
            rho = 1.0 / sy
            V = np.eye(k * k) - rho * np.outer(s, y)
            H = V @ H @ V.T + rho * np.outer(s, s)
    L, T, _ = _rotated(A, M)
    return L, T.T @ T, converged


def analyze(x, k=None):
    """The exploratory factor analysis of the complete cases x (n x p): {"parallel", "k", "pattern" (p x k, factors
    ordered by the variance they explain, signs so that most loadings are positive), "communality", "phi",
    "explained" (share of the variance per factor, from the pattern and phi), "primary" (the factor of each statement or
    None), "cross" (statements with a second loading from CROSS), "converged", "heywood" (a communality at the cap CAP: the
    solution lies at the boundary, h² there is a set value, not an estimate)} or None (too few cases or statements).
    When the parallel analysis supports no common factor and no k is given, "k" is 0 and there is no solution
    (empty pattern; audit 2026-10-21, E2)."""
    x = np.asarray(x, dtype=float)
    n, p = x.shape
    if p < MIN_ITEMS or n < max(MIN_N, PER_ITEM * p) or np.any(x.std(axis=0) == 0):
        return None
    pa = parallel(x)
    if not k and not pa["k"]:
        return {"parallel": pa, "k": 0, "pattern": [], "communality": [], "phi": [], "explained": [], "primary": [],
                "cross": [], "n": n, "converged": True, "heywood": False}
    k = min(k or pa["k"], p - 1)
    R = np.corrcoef(x, rowvar=False)
    lam, h2, ok = principal_axis(R, k)
    pattern, phi, rotated = oblimin(lam)
    # each factor with the sign of most of its large loadings, the factors by the variance they explain
    signs = np.where(np.sum(np.where(np.abs(pattern) >= CROSS, pattern, 0), axis=0) < 0, -1.0, 1.0)
    pattern = pattern * signs
    phi = phi * np.outer(signs, signs)
    structure = pattern @ phi
    explained = np.sum(pattern * structure, axis=0) / p
    order = np.argsort(explained)[::-1]
    pattern, phi, explained = pattern[:, order], phi[np.ix_(order, order)], explained[order]
    primary, cross = [], []
    for j in range(p):
        row = np.abs(pattern[j])
        best = int(np.argmax(row))
        primary.append(best if row[best] >= PRIMARY else None)
        if np.sum(row >= CROSS) >= 2:
            cross.append(j)
    return {"parallel": pa, "k": k, "pattern": pattern.tolist(), "communality": h2.tolist(), "phi": phi.tolist(),
            "explained": explained.tolist(), "primary": primary, "cross": cross, "n": n,
            "converged": bool(ok and rotated), "heywood": bool(np.any(h2 >= CAP - 1e-9))}
