"""Latent class multinomial logit (LC-MNL, segments), numpy only (concept latente-klassen-konzept.md, LK1 to LK17).

Works on the same data as the pooled logit (app/choice_model.py) and HB (app/hb.py): every choice a
choice_model.ChoiceSet, grouped by person. Model (Kamakura & Russell, 1989; Wedel & Kamakura, 2000; Greene & Hensher,
2003): K classes with shares pi_c and one parameter vector beta_c each; a person stays in one class for all choices, so
the likelihood of person n in class c is the product L_nc over the person's choices, and
    LL = sum_n log sum_c pi_c L_nc,     tau_nc = pi_c L_nc / sum_k pi_k L_nk (posterior membership).

Estimated by EM (Dempster, Laird & Rubin, 1977): the E-step gives tau, the M-step pi_c = mean of tau_nc and for every
class one Newton step (with step halving) of the conditional logit whose choices count with the weight tau_nc of their
person: a generalised EM (ECM; Meng & Rubin, 1993) that raises the objective in every step without iterating each
class's logit to its end. Every class carries the same weak normal prior as the pooled logit under separation
(`penalty`, e.g. choice_model.Coding.penalty()): small classes often have a level they never chose, whose maximum
likelihood part-worth would be infinite and lift the LL without end (Albert & Anderson, 1984), which would bias the
choice of the number of classes; the same regularisation for mixtures in Fraley & Raftery (2007). The reported LL is
the unpenalised one.

Starting values (McLachlan & Peel, 2000): per K, STARTS random memberships (Dirichlet(1)) plus one start from the best
solution with K - 1 classes whose largest class is split; each runs SHORT iterations, the KEEP best run to convergence
("short runs", Biernacki, Celeux & Govaert, 2003). How many of them reach the best LL is the stability shown (Hipp &
Bauer, 2006). Classes are sorted by size, so segment 1 is the largest. A fixed seed makes a run reproducible.

Criteria per K (N = persons, not choices: the choices of a person are not independent): BIC (Schwarz, 1978), AIC3
(Andrews & Currim, 2003), CAIC (Bozdogan, 1987), entropy R^2 (Ramaswamy et al., 1993). Standard errors: the gradient
of the mixture log-likelihood per person (Louis, 1982), the Hessian by central differences of the summed gradient,
then the sandwich over persons (White, 1982) with the small-sample factor G / (G - 1) as in choice_model._robust.
Class shares are parameterised as a logit (alpha_1 = 0) for it.

Covariates (concomitant variables; Dayton & Macready, 1988): with a design Z (persons x r, first column 1) the shares
of a person are a multinomial logit pi_nc = exp(z_n g_c) / sum_d exp(z_n g_d), g_1 = 0; the M-step takes one Newton
step of that logit on the memberships tau, with a weak normal prior (GAMMA_RIDGE) on the slopes so that a group found
in one segment only stays finite. Without Z the shares are the same for everyone, as above (Z = a column of ones).
"""
import math
import time

import numpy as np

STARTS = 50      # random starts per K (Masyn, 2013: 50 to 100 or more; Ralf, 2026-10-06)
SHORT = 25       # EM iterations of a start before the best ones are chosen
KEEP = 3         # starts run to convergence
MAX_ITER = 1000  # EM iterations of a kept start at most
TOL = 1e-8       # relative change of the objective that ends a run
K_MAX = 6        # most classes (LK4, a choice of Askurious): more segments are hardly usable in practice
PER_PARAMETER = 5  # persons per parameter of a class at least: K <= N / (PER_PARAMETER k) (LK4, a choice of Askurious)
MIN_PERSONS = 60   # fewer persons: no latent class model (LK4, a choice of Askurious)
MIN_SHARE = 0.05   # smallest class: 5 % of the persons (Nylund-Gibson & Choi, 2018) ...
MIN_CLASS = 50     # ... or 50 persons, whichever is more (Weller et al., 2020; Sorgente et al., 2025); Ralf, 2026-10-06
ENTROPY_GOOD = 0.8  # entropy R^2 from which the classes count as well apart (Nylund-Gibson & Choi, 2018)
ENTROPY_LOW = 0.6   # below: hard to publish (Weller et al., 2020)
SAME_LL = 0.01   # two runs with LLs this close found the same solution
GAMMA_RIDGE = 0.1  # prior precision of the covariate slopes of the class shares (not of the intercepts)

KAMAKURA = ("Kamakura, W. A., & Russell, G. J. (1989). A probabilistic choice model for market segmentation and elasticity "
            "structure. Journal of Marketing Research, 26(4), 379–390. https://doi.org/10.1177/002224378902600401")
WEDEL = ("Wedel, M., & Kamakura, W. A. (2000). Market segmentation: Conceptual and methodological foundations (2nd ed.). "
         "Kluwer Academic. https://doi.org/10.1007/978-1-4615-4651-1")
GREENE = ("Greene, W. H., & Hensher, D. A. (2003). A latent class model for discrete choice analysis: Contrasts with mixed "
          "logit. Transportation Research Part B: Methodological, 37(8), 681–698. "
          "https://doi.org/10.1016/S0191-2615(02)00046-2")
DEMPSTER = ("Dempster, A. P., Laird, N. M., & Rubin, D. B. (1977). Maximum likelihood from incomplete data via the EM "
            "algorithm. Journal of the Royal Statistical Society: Series B (Methodological), 39(1), 1–38. "
            "https://doi.org/10.1111/j.2517-6161.1977.tb01600.x")
MENG = ("Meng, X.-L., & Rubin, D. B. (1993). Maximum likelihood estimation via the ECM algorithm: A general framework. "
        "Biometrika, 80(2), 267–278. https://doi.org/10.1093/biomet/80.2.267")
FRALEY = ("Fraley, C., & Raftery, A. E. (2007). Bayesian regularization for normal mixture estimation and model-based "
          "clustering. Journal of Classification, 24(2), 155–181. https://doi.org/10.1007/s00357-007-0004-5")
MCLACHLAN = ("McLachlan, G. J., & Peel, D. (2000). Finite mixture models. Wiley. https://doi.org/10.1002/0471721182")
BIERNACKI = ("Biernacki, C., Celeux, G., & Govaert, G. (2003). Choosing starting values for the EM algorithm for getting "
             "the highest likelihood in multivariate Gaussian mixture models. Computational Statistics & Data Analysis, "
             "41(3–4), 561–575. https://doi.org/10.1016/S0167-9473(02)00163-9")
HIPP = ("Hipp, J. R., & Bauer, D. J. (2006). Local solutions in the estimation of growth mixture models. Psychological "
        "Methods, 11(1), 36–53. https://doi.org/10.1037/1082-989X.11.1.36")
SCHWARZ = ("Schwarz, G. (1978). Estimating the dimension of a model. The Annals of Statistics, 6(2), 461–464. "
           "https://doi.org/10.1214/aos/1176344136")
ANDREWS_CURRIM = ("Andrews, R. L., & Currim, I. S. (2003). A comparison of segment retention criteria for finite mixture "
                  "logit models. Journal of Marketing Research, 40(2), 235–243. "
                  "https://doi.org/10.1509/jmkr.40.2.235.19225")
BOZDOGAN = ("Bozdogan, H. (1987). Model selection and Akaike's information criterion (AIC): The general theory and its "
            "analytical extensions. Psychometrika, 52(3), 345–370. https://doi.org/10.1007/BF02294361")
NYLUND = ("Nylund, K. L., Asparouhov, T., & Muthén, B. O. (2007). Deciding on the number of classes in latent class "
          "analysis and growth mixture modeling: A Monte Carlo simulation study. Structural Equation Modeling, 14(4), "
          "535–569. https://doi.org/10.1080/10705510701575396")
RAMASWAMY = ("Ramaswamy, V., DeSarbo, W. S., Reibstein, D. J., & Robinson, W. T. (1993). An empirical pooling approach "
             "for estimating marketing mix elasticities with PIMS data. Marketing Science, 12(1), 103–124. "
             "https://doi.org/10.1287/mksc.12.1.103")
LOUIS = ("Louis, T. A. (1982). Finding the observed information matrix when using the EM algorithm. Journal of the Royal "
         "Statistical Society: Series B (Methodological), 44(2), 226–233. "
         "https://doi.org/10.1111/j.2517-6161.1982.tb01203.x")
WHITE = ("White, H. (1982). Maximum likelihood estimation of misspecified models. Econometrica, 50(1), 1–25. "
         "https://doi.org/10.2307/1912526")
SWAIT = ("Swait, J., & Louviere, J. (1993). The role of the scale parameter in the estimation and comparison of "
         "multinomial logit models. Journal of Marketing Research, 30(3), 305–314. "
         "https://doi.org/10.1177/002224379303000303")
ANDREWS_HB = ("Andrews, R. L., Ansari, A., & Currim, I. S. (2002). Hierarchical Bayes versus finite mixture conjoint "
              "analysis models: A comparison of fit, prediction, and partworth recovery. Journal of Marketing Research, "
              "39(1), 87–98. https://doi.org/10.1509/jmkr.39.1.87.18936")
DAYTON = ("Dayton, C. M., & Macready, G. B. (1988). Concomitant-variable latent-class models. Journal of the American "
          "Statistical Association, 83(401), 173–178. https://doi.org/10.1080/01621459.1988.10478584")
NYLUND_GIBSON = ("Nylund-Gibson, K., & Choi, A. Y. (2018). Ten frequently asked questions about latent class analysis. "
                 "Translational Issues in Psychological Science, 4(4), 440–461. https://doi.org/10.1037/tps0000176")
WELLER = ("Weller, B. E., Bowen, N. K., & Faubert, S. J. (2020). Latent class analysis: A guide to best practice. Journal "
          "of Black Psychology, 46(4), 287–311. https://doi.org/10.1177/0095798420930932")
SORGENTE = ("Sorgente, A., Caliciuri, R., Robba, M., Lanz, M., & Zumbo, B. D. (2025). A systematic review of latent class "
            "analysis in psychology: Examining the gap between guidelines and research practice. Behavior Research "
            "Methods, 57(11), 301. https://doi.org/10.3758/s13428-025-02812-1")
MASYN = ("Masyn, K. E. (2013). Latent class analysis and finite mixture modeling. In T. D. Little (Ed.), The Oxford "
         "handbook of quantitative methods: Vol. 2. Statistical analysis (pp. 551–611). Oxford University Press.")
SOURCES = (KAMAKURA, WEDEL, GREENE)


class Data:
    """The choice sets of all persons, stacked by number of alternatives: [(X sets x alternatives x params, chosen,
    owner: person index per set)], plus the number of persons, sets per person and the chance log-likelihood."""

    def __init__(self, person_sets, k):
        # a choice outside its set (-1, or an index past the alternatives) would be read as another alternative by
        # NumPy indexing: left out, as in hb.Data (audit data storage and calculations 2026-10-21, D7)
        person_sets = [[s for s in sets if isinstance(s.chosen, (int, np.integer)) and 0 <= s.chosen < len(s.X)]
                       for sets in person_sets]
        self.k, self.n = k, len(person_sets)
        by_size = {}
        for p, sets in enumerate(person_sets):
            for s in sets:
                by_size.setdefault(len(s.X), []).append((s, p))
        self.groups = [(np.stack([s.X for s, _ in g]), np.array([s.chosen for s, _ in g]), np.array([p for _, p in g]))
                       for g in by_size.values()]
        self.sets = np.array([len(s) for s in person_sets], dtype=float)
        self.chance = np.zeros(self.n)
        for X, _, owner in self.groups:
            np.add.at(self.chance, owner, -math.log(X.shape[1]))

    def person_ll(self, B):
        """log L_nc: persons x classes, for parameters B (classes x params)."""
        out = np.zeros((self.n, len(B)))
        for X, chosen, owner in self.groups:
            v = X @ B.T                                       # sets x alternatives x classes
            v = v - v.max(axis=1, keepdims=True)
            lp = v[np.arange(len(chosen)), chosen] - np.log(np.exp(v).sum(axis=1))
            np.add.at(out, owner, lp)
        return out

    def derivatives(self, B, tau):
        """For every class c at once: the value, gradient and Hessian of sum_s tau_(n(s), c) log p_s(beta_c) (the
        weight of a person applies to all its sets). Returns (values C, gradients C x k, Hessians C x k x k)."""
        C, k = B.shape
        value, grad, hess = np.zeros(C), np.zeros((C, k)), np.zeros((C, k, k))
        for X, chosen, owner in self.groups:
            rows = np.arange(len(chosen))
            W = tau[owner]                                    # sets x classes
            v = X @ B.T                                       # sets x alternatives x classes
            v = v - v.max(axis=1, keepdims=True)
            e = np.exp(v)
            total = e.sum(axis=1)
            value += (W * (v[rows, chosen] - np.log(total))).sum(axis=0)
            p = e / total[:, None, :]
            xbar = np.matmul(p.transpose(0, 2, 1), X)        # sets x classes x params
            grad += np.einsum("sc,sck->ck", W, X[rows, chosen][:, None, :] - xbar)
            for c in range(C):
                Z = (np.sqrt(W[:, c, None] * p[:, :, c])[..., None] * (X - xbar[:, c, None, :])).reshape(-1, k)
                hess[c] -= Z.T @ Z
        return value, grad, hess

    def values(self, B, tau):
        """The first part of derivatives(): sum_s tau log p_s(beta_c) per class."""
        value = np.zeros(len(B))
        for X, chosen, owner in self.groups:
            v = X @ B.T
            v = v - v.max(axis=1, keepdims=True)
            value += (tau[owner] * (v[np.arange(len(chosen)), chosen] - np.log(np.exp(v).sum(axis=1)))).sum(axis=0)
        return value

    def class_value(self, b, w):
        total = 0.0
        for X, chosen, owner in self.groups:
            v = X @ b
            v = v - v.max(axis=1, keepdims=True)
            total += float(w[owner] @ (v[np.arange(len(chosen)), chosen] - np.log(np.exp(v).sum(axis=1))))
        return total

    def person_scores(self, B, tau):
        """Gradient of each person's log sum_c pi_c L_nc with respect to every beta_c: persons x (classes x params)."""
        C, k = B.shape
        out = np.zeros((self.n, C, k))
        for X, chosen, owner in self.groups:
            rows = np.arange(len(chosen))
            for c in range(C):
                v = X @ B[c]
                v = v - v.max(axis=1, keepdims=True)
                p = np.exp(v)
                p /= p.sum(axis=1, keepdims=True)
                g = X[rows, chosen] - np.einsum("nj,njk->nk", p, X)
                np.add.at(out[:, c, :], owner, g)
        return (out * tau[:, :, None]).reshape(self.n, C * k)


def _posterior(L, pi):
    """(tau, log-likelihood) from log L_nc and the shares."""
    A = L + np.log(np.maximum(pi, 1e-300))
    m = A.max(axis=1, keepdims=True)
    s = np.exp(A - m)
    total = s.sum(axis=1, keepdims=True)
    return s / total, float((m[:, 0] + np.log(total[:, 0])).sum())


def _shares(Z, G):
    """Class shares per person (persons x classes) of the covariate logit."""
    V = Z @ G
    V = V - V.max(axis=1, keepdims=True)
    E = np.exp(V)
    return E / E.sum(axis=1, keepdims=True)


def _pi_of(pi, Z):
    """The shares the E-step uses: the vector, or with covariates the shares of every person (pi holds G then)."""
    return pi if Z is None else _shares(Z, pi)


def _gamma_penalty(G):
    return 0.5 * GAMMA_RIDGE * float((G[1:, 1:] ** 2).sum())


def _gamma_step(Z, tau, G):
    """One Newton step (halved until it does not fall) of the multinomial logit of the memberships tau on Z,
    segment 1 the reference, the prior on the slopes."""
    n, r = Z.shape
    C = tau.shape[1]
    if C == 1:
        return G

    def value(Gx):
        V = Z @ Gx
        m = V.max(axis=1, keepdims=True)
        return float((tau * (V - m - np.log(np.exp(V - m).sum(axis=1, keepdims=True)))).sum()) - _gamma_penalty(Gx)

    Pn = _shares(Z, G)
    mask = np.zeros((r, C - 1))
    mask[1:, :] = GAMMA_RIDGE
    grad = (Z.T @ (tau - Pn))[:, 1:] - mask * G[:, 1:]
    m = r * (C - 1)
    H = np.zeros((m, m))
    for a in range(C - 1):
        for b in range(C - 1):
            w = Pn[:, a + 1] * ((a == b) - Pn[:, b + 1])
            H[a * r:(a + 1) * r, b * r:(b + 1) * r] = -(Z * w[:, None]).T @ Z
    H -= np.diag(mask.T.ravel())
    try:
        step = np.linalg.solve(H, -grad.T.ravel()).reshape(C - 1, r).T
    except np.linalg.LinAlgError:
        return G
    old, t = value(G), 1.0
    for _ in range(30):
        cand = G.copy()
        cand[:, 1:] += t * step
        if value(cand) >= old - 1e-12:
            return cand
        t /= 2
    return G


def _objective(data, B, pi, P, Z=None):
    """Penalised log-likelihood, unpenalised log-likelihood and tau."""
    tau, ll = _posterior(data.person_ll(B), _pi_of(pi, Z))
    extra = _gamma_penalty(pi) if Z is not None else 0.0
    return ll - 0.5 * float(sum(b @ P @ b for b in B)) - extra, ll, tau


def _m_step(data, B, tau, P):
    """One Newton step per class on its tau-weighted, penalised logit, halved until it does not fall."""
    value, grad, hess = data.derivatives(B, tau)
    steps = np.zeros_like(B)
    for c in range(len(B)):
        if tau[:, c].sum() < 1e-8:
            continue
        try:
            steps[c] = np.linalg.solve(hess[c] - P, -(grad[c] - P @ B[c]))
        except np.linalg.LinAlgError:
            pass

    def penalised(values, M):
        return values - 0.5 * np.einsum("ck,kl,cl->c", M, P, M)

    old = penalised(value, B)
    cand = B + steps
    new = penalised(data.values(cand, tau), cand)
    out = cand.copy()
    for c in np.flatnonzero(new < old - 1e-12):  # rare: halve the step of that class until it does not fall
        w, t = tau[:, c], 0.5
        out[c] = B[c]
        for _ in range(30):
            b = B[c] + t * steps[c]
            if data.class_value(b, w) - 0.5 * b @ P @ b >= old[c] - 1e-12:
                out[c] = b
                break
            t /= 2
    return out


def _run(data, B, pi, P, iterations, tol=TOL, Z=None):
    """EM from (B, pi) for at most `iterations` steps: (objective, ll, B, pi, tau, iterations, converged). With
    covariates Z, pi is the matrix G of the share logit."""
    obj, ll, tau = _objective(data, B, pi, P, Z)
    for it in range(1, iterations + 1):
        if Z is None:
            pi = np.maximum(tau.mean(axis=0), 1e-12)
            pi = pi / pi.sum()
        else:
            pi = _gamma_step(Z, tau, pi)
        new_B = _m_step(data, B, tau, P)
        new_obj, new_ll, new_tau = _objective(data, new_B, pi, P, Z)
        change = abs(new_obj - obj)
        moved = float(np.max(np.abs(new_B - B), initial=0.0))
        B, obj, ll, tau = new_B, new_obj, new_ll, new_tau
        if change < tol * max(abs(obj), 1.0) and moved < 1e-5:
            return obj, ll, B, pi, tau, it, True
    return obj, ll, B, pi, tau, iterations, False


def _from_tau(data, tau, P, base, Z=None):
    """A start: shares and one M-step from the memberships tau, every class beginning at the pooled solution; with
    covariates the share logit starts with the intercepts of these shares and no slopes."""
    B = np.repeat(base[None], tau.shape[1], axis=0)
    pi = tau.mean(axis=0)
    if Z is not None:
        G = np.zeros((Z.shape[1], tau.shape[1]))
        G[0] = np.log(np.maximum(pi, 1e-12)) - math.log(max(pi[0], 1e-12))
        pi = G
    return _m_step(data, B, tau, P), pi


def _sandwich(data, B, pi, P, Z=None):
    """Cluster-robust covariance of (beta_1 .. beta_K, then the share logit: alpha_2 .. alpha_K, with covariates
    g_2 .. g_K one after the other, r values each) or None (see the module text)."""
    C, k = B.shape
    n = data.n
    if Z is None:
        Zs = np.ones((n, 1))
        alpha = np.log(np.maximum(pi, 1e-300))
        G = (alpha - alpha[0])[None, :]
    else:
        Zs, G = Z, pi - pi[:, :1]
    r = Zs.shape[1]
    mask = np.zeros((r, C - 1))
    if Z is not None:
        mask[1:, :] = GAMMA_RIDGE

    def scores(theta):
        Bt = theta[:C * k].reshape(C, k)
        Gt = np.column_stack([np.zeros(r), theta[C * k:].reshape(C - 1, r).T]) if C > 1 else np.zeros((r, 1))
        p = _shares(Zs, Gt)
        tau, _ = _posterior(data.person_ll(Bt), p)
        D = tau[:, 1:] - p[:, 1:]                                  # persons x (classes - 1)
        SG = (D[:, :, None] * Zs[:, None, :]).reshape(n, (C - 1) * r)
        S = np.column_stack([data.person_scores(Bt, tau), SG])
        pen = np.concatenate([(P @ b) for b in Bt] + [(mask * Gt[:, 1:]).T.ravel()])
        return S, S.sum(axis=0) - pen

    theta = np.concatenate([B.ravel(), G[:, 1:].T.ravel()])
    S, _ = scores(theta)
    m = len(theta)
    H = np.zeros((m, m))
    h = 1e-5
    for j in range(m):
        e = np.zeros(m)
        e[j] = h
        H[:, j] = (scores(theta + e)[1] - scores(theta - e)[1]) / (2 * h)
    H = (H + H.T) / 2
    try:
        bread = np.linalg.inv(-H)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(bread)) or np.any(np.diag(bread) <= 0) or np.linalg.cond(-H) > 1e12:
        return None
    if n <= m:
        return bread
    cov = n / (n - 1) * bread @ (S.T @ S) @ bread
    try:
        np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        return bread
    return cov if np.all(np.isfinite(cov)) else bread


def share_intervals(pi, cov, C, k):
    """95 % intervals of the class shares by the delta method from the covariance of alpha: [(lo, hi)] or None."""
    if cov is None or C < 2:
        return None
    A = cov[C * k:, C * k:]
    J = np.zeros((C, C - 1))                       # d pi_c / d alpha_j (j = 2..C)
    for c in range(C):
        for j in range(1, C):
            J[c, j - 1] = pi[c] * ((c == j) - pi[j])
    var = np.einsum("cj,jl,cl->c", J, A, J)
    return [(max(0.0, p - 1.959964 * math.sqrt(max(v, 0))), min(1.0, p + 1.959964 * math.sqrt(max(v, 0))))
            for p, v in zip(pi, var)]


def criteria(ll, C, k, n, tau, r=1):
    p = C * k + (C - 1) * r
    entropy = None
    if C > 1:
        t = np.clip(tau, 1e-300, 1)
        entropy = 1 - float(-(tau * np.log(t)).sum()) / (n * math.log(C))
    return {"params": p, "bic": -2 * ll + p * math.log(n), "aic3": -2 * ll + 3 * p,
            "caic": -2 * ll + p * (math.log(n) + 1), "entropy": entropy}


def k_max(n, k, limit=K_MAX):
    """The most classes for n persons and k parameters per class (LK4), at least 1."""
    return max(1, min(limit, n // (PER_PARAMETER * max(k, 1))))


def estimate(person_sets, k, penalty, base=None, kmax=None, starts=STARTS, seed=1, progress=None, sandwich=True,
             finished=None, on_solution=None, Z=None):
    """All solutions K = 1 .. kmax. person_sets: [[ChoiceSet]] per person; penalty: prior precision (k x k);
    base: a pooled solution to start every class from (zeros if None). Every K draws its starts from its own random
    stream (seed, K), so a run that resumes after K - 1 (finished: the solutions already found, in this module's
    form) gives the same result as one in a go; on_solution(solution) is called as soon as a K is done (LK17).
    Returns {"solutions": [...], "chance", "persons", "sets", "seconds"}; each solution: K, ll, objective, criteria,
    pi, B (classes x params), tau (persons x classes), cov (or None), share_ci, converged, iterations, found (runs of
    KEEP at the best LL), runs and rlh (per class). Z: covariates of the shares (persons x r, first column 1); the
    solution then also has G (r x classes, column 1 = 0) and pi is the mean share."""
    t0 = time.time()
    data = Data(person_sets, k)
    Z = None if Z is None else np.asarray(Z, dtype=float)
    nz = 1 if Z is None else Z.shape[1]  # columns of the share logit
    P = np.asarray(penalty, dtype=float) if penalty is not None else np.zeros((k, k))
    base = np.zeros(k) if base is None else np.asarray(base, dtype=float)
    kmax = kmax or k_max(data.n, k)
    finished = [s for s in (finished or []) if s["K"] <= kmax]
    # work in units that grow with the number of classes (an EM step costs about K times as much)
    work = sum(C * (1 if C == 1 else starts + 1 + KEEP) for C in range(len(finished) + 1, kmax + 1)) or 1
    done = 0

    def tick(C, steps=1):
        nonlocal done
        done += C * steps
        if progress:
            progress(min(done / work, 0.99))

    solutions = list(finished)
    previous = solutions[-1] if solutions else None
    for C in range(len(solutions) + 1, kmax + 1):
        rng = np.random.default_rng([seed, C])
        if C == 1:
            B, pi = _from_tau(data, np.ones((data.n, 1)), P, base, Z)
            obj, ll, B, pi, tau, it, conv = _run(data, B, pi, P, MAX_ITER, Z=Z)
            finals = [(obj, ll, B, pi, tau, it, conv)]
            tick(C)
        else:
            shorts = []
            for _ in range(starts):
                B, pi = _from_tau(data, rng.dirichlet(np.ones(C), data.n), P, base, Z)
                shorts.append(_run(data, B, pi, P, SHORT, Z=Z))
                tick(C)
            if previous is not None:  # the best K - 1 solution with its largest class split in two
                big = int(np.argmax(previous["pi"]))
                B = np.vstack([previous["B"], previous["B"][big] + rng.normal(0, 0.1, k)])
                if Z is None:
                    pi = np.append(previous["pi"], previous["pi"][big] / 2)
                    pi[big] /= 2
                else:  # the same split in the share logit: the copy and the original half as likely
                    G = np.asarray(previous["G"], dtype=float)
                    pi = np.column_stack([G, G[:, big]])
                    pi[0, [big, C - 1]] -= math.log(2)
                    pi = pi - pi[:, :1]
                shorts.append(_run(data, B, pi, P, SHORT, Z=Z))
            tick(C)
            shorts.sort(key=lambda r: -r[0])
            finals = []
            for r in shorts[:KEEP]:
                finals.append(_run(data, r[2], r[3], P, MAX_ITER, Z=Z))
                tick(C)
            finals.sort(key=lambda r: -r[0])
        obj, ll, B, pi, tau, it, conv = finals[0]
        G = None
        if Z is not None:
            G, pi = pi, _shares(Z, pi).mean(axis=0)
        order = np.argsort(-pi, kind="stable")      # segment 1 = the largest
        B, pi, tau = B[order], pi[order], tau[:, order]
        if G is not None:
            G = G[:, order]
            G = G - G[:, :1]                         # the largest segment the reference
        found = sum(1 for r in finals if abs(r[0] - obj) < SAME_LL)
        L = data.person_ll(B)
        modal = tau.argmax(axis=1)
        rlh = [float(np.exp(L[modal == c, c] / data.sets[modal == c]).mean()) if (modal == c).any() else None
               for c in range(C)]
        cov = _sandwich(data, B, pi if G is None else G, P, Z) if sandwich else None
        sol = {"K": C, "ll": ll, "objective": obj, **criteria(ll, C, k, data.n, tau, nz), "pi": pi, "B": B, "tau": tau,
               "cov": cov, "converged": conv, "iterations": it, "found": found, "runs": len(finals), "rlh": rlh,
               "share_ci": share_intervals(pi, cov, C, k) if G is None else None, "G": G}
        solutions.append(sol)
        previous = sol
        if on_solution:
            on_solution(sol)
    chance = float(np.exp(data.chance / np.maximum(data.sets, 1)).mean())
    return {"solutions": solutions, "chance": chance, "persons": data.n, "sets": int(data.sets.sum()),
            "seconds": time.time() - t0}


def too_small(share, n):
    """True if a class of this share of n persons is below the smallest size: MIN_SHARE of the persons or MIN_CLASS
    persons, whichever is more (expected persons, sum of tau)."""
    return share * n < max(MIN_SHARE * n, MIN_CLASS)


def suggest(solutions):
    """The K with the smallest BIC among the solutions whose smallest class is not too_small and whose best LL was
    found at least twice; K = 1 if none is left. The others stay selectable (a small class can make sense)."""
    ok = []
    for s in solutions:
        n = len(s["tau"])
        if s["K"] == 1 or (not too_small(min(s["pi"]), n) and s["found"] >= min(2, s["runs"])):
            ok.append(s)
    return min(ok, key=lambda s: s["bic"])["K"] if ok else 1
