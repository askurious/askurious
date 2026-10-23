"""Hierarchical Bayes multinomial logit (HB-MNL), numpy only.

Works on the same data as the pooled logit in app/choice_model.py: every observed choice is a
choice_model.ChoiceSet (alternatives x parameters, index of the chosen one), here grouped by person.
Model: beta_n ~ MVN(mu, Sigma) for every person n, choices given beta_n a conditional logit.
Priors: mu ~ N(0, MU_PRIOR_SD^2 * prior_cov), far wider than any utility the data can show, so it leaves
estimates alone and only matters where the data set no limit (a level or statement everyone always or never
chose: with a flat prior the posterior has no finite mass there and the chain drifts for as long as it runs);
Sigma ~ inverse Wishart with df = k + 5 and a scale chosen so that the prior mean of Sigma is `prior_cov`
(default I). The prior on Sigma counts like four persons whose utilities vary with variance 1: it barely
moves the individual utilities, but for weakly identified parameters and small samples it pulls the
reported spread between persons towards 1 (Festlegung von Askurious: a proper prior that keeps
Sigma stable with few persons; not a weak prior in that respect).

Sampler (Allenby & Rossi; Train 2009, ch. 12), one iteration:
  mu    | betas, Sigma    ~ N(V N Sigma^-1 mean of the betas, V), V = (prior^-1 + N Sigma^-1)^-1   (Gibbs)
  Sigma | betas, mu       ~ IW(df + N, scale + sum (beta_n - mu)(beta_n - mu)')    (Gibbs)
  beta_n | mu, Sigma, data: one Metropolis step per person, all persons at once (vectorised). The proposal is a
  preconditioned Langevin step (MALA; Roberts & Rosenthal, 1998): beta_n + eps_n^2 / 2 * C_n g_n + eps_n * C_n^1/2 z
  with g_n the gradient of the person's log posterior and C_n = (H_n + Sigma^-1)^-1, H_n the person's logit
  information (the proposal shape of Rossi et al., 2005, ch. 5). Every person has an own step size eps_n,
  adapted during burn-in towards TARGET acceptance (the optimum of Roberts & Rosenthal for MALA; 0.23 to 0.3
  for a random walk, Roberts, Gelman & Gilks, 1997); step sizes and C_n are fixed after burn-in so the kept
  draws come from a valid chain. Adaptation: every ADAPT_EVERY iterations from the acceptance of that window
  until the last SETTLE share of the burn-in; there C_n stays fixed and log eps_n moves after every iteration by
  1 / sqrt(t + SETTLE_OFFSET) times (acceptance probability - TARGET), a Robbins-Monro step with decreasing gain
  (Andrieu & Thoms, 2008), and the step size kept is exp of the mean of log eps_n over that phase (Festlegung von
  Askurious: the mean of the iterates is steadier than the last one, Polyak & Juditsky, 1992). Before (audit 2026-10-05, H-5) the last window of 50 tries fixed it: the
  acceptance per person after burn-in spread from 0.05 to 0.84. The same for the shift and the scaling.
  shift: mu and all betas moved together by one proposed translation (a group move in the sense of Liu & Sabatti,
  2000), proposed as a Langevin step too, its metric the summed information of all persons, its step size adapted
  towards TARGET. The deviations beta_n - mu stay, so only the likelihood and the prior of mu decide. The Gibbs
  steps alone move mu slowly where the data say little per person (e.g. the constant of "none"): each person's
  beta waits for mu and mu for the betas.
  scale: the deviations beta_n - mu and Sigma stretched together per parameter (beta_n - mu -> c (beta_n - mu),
  Sigma -> c Sigma c; a random-walk Metropolis step on log c, the same kind of group move): the spread of the betas
  and Sigma also wait for each other in the Gibbs steps. Its step size is adapted towards SCALE_TARGET.
The random walk with one common step size used before (FORMAT 3/4 results) reached an ESS of 15 to 250 of
1000 kept draws on healthy simulated data, even 8 times longer never 400 (audit 2026-10-05, W-2). On the same data
this sampler reaches 170 to 440 at the default length and passes the rule of app/hb_convergence.py at twice the
default length (ESS 670 to 920 over mu, the log-likelihood and the shown means). It evaluates the likelihood three
times per iteration instead of once; with the shorter default burn-in (5000 instead of 10 000, maxdiff_hb.settings)
a run of the default length took about twice as long as before. The default then (10 000 iterations) passed the
rule in 0 of 8 runs on healthy CBC data (N = 200; audit 2026-10-05, H-2): since then 20 000 iterations, and with the
settled step sizes (H-5) the rule passed there in 8 of 8 runs (ESS min. 740 to 950 of 2000 kept draws).
Starts from the pooled logit (all persons equal). After `burn` iterations every `thin`-th of the next
`draws` iterations is kept. Reported: posterior mean betas per person, mean of mu and of the diagonal of
Sigma (and of spread(Sigma)), acceptance rate, RLH per person (root likelihood: geometric mean of the choice probabilities at the
posterior mean betas) and a convergence check (Geweke-type z of the log-likelihood and of mu, first vs
second half of the kept draws, standard errors from batch means; Geweke, 1992, with batch means instead of
spectral densities). The traces of mu and of the log-likelihood are returned for the split-R-hat and ESS of
app/hb_convergence.py, and `keep` of the kept draws of all betas, evenly spaced, for predictions that average
over the posterior (app/choice_predict.py). The seed makes a run reproducible.

Used for MaxDiff (app/maxdiff_hb.py) and for CBC and ACBC (app/conjoint_hb.py).
"""
import math
import time

import numpy as np

from app.choice_model import TRAIN

GEWEKE_LIMIT = 3.5  # |z| above this: the first and the second half of the kept draws differ
MU_PRIOR_SD = 5.0   # prior sd of the population means, in units of prior_cov (logit scale)
MIN_PERSONS = 10    # fewer persons say nothing about a distribution of utilities across persons
BATCHES = 10        # batch means per half for the standard error of the half means
TARGET = 0.57       # acceptance the step sizes per person are adapted to (MALA, Roberts & Rosenthal, 1998)
SCALE_TARGET = 0.3  # acceptance of the joint scaling (a random walk: 0.23 to 0.3, Roberts et al., 1997)
ACCEPT_OK = (0.35, 0.85)  # outside: the page warns that the chain mixes badly (Festlegung von Askurious)
ADAPT_EVERY = 50    # burn-in iterations per adaptation of the step sizes (until the settling phase)
SETTLE = 0.25       # last share of the burn-in: step sizes settle with a decreasing gain and are averaged (H-5)
SETTLE_OFFSET = 10  # gain 1 / sqrt(t + SETTLE_OFFSET) in the t-th iteration of the settling phase
KEEP_DRAWS = 100    # kept draws of all betas returned for predictions over the posterior (W-1)
GEWEKE = ("Geweke, J. (1992). Evaluating the accuracy of sampling-based approaches to the calculation of posterior "
          "moments. In J. M. Bernardo, J. O. Berger, A. P. Dawid, & A. F. M. Smith (Eds.), Bayesian statistics 4 "
          "(pp. 169–193). Oxford University Press.")
ROBERTS_ROSENTHAL = ("Roberts, G. O., & Rosenthal, J. S. (1998). Optimal scaling of discrete approximations to Langevin "
                     "diffusions. Journal of the Royal Statistical Society: Series B, 60(1), 255–268. "
                     "https://doi.org/10.1111/1467-9868.00123")
ROBERTS_GELMAN_GILKS = ("Roberts, G. O., Gelman, A., & Gilks, W. R. (1997). Weak convergence and optimal scaling of random "
                        "walk Metropolis algorithms. The Annals of Applied Probability, 7(1), 110–120. "
                        "https://doi.org/10.1214/aoap/1034625254")
ANDRIEU_THOMS = ("Andrieu, C., & Thoms, J. (2008). A tutorial on adaptive MCMC. Statistics and Computing, 18(4), "
                 "343–373. https://doi.org/10.1007/s11222-008-9110-y")
LIU_SABATTI = ("Liu, J. S., & Sabatti, C. (2000). Generalised Gibbs sampler and multigrid Monte Carlo for Bayesian "
               "computation. Biometrika, 87(2), 353–369. https://doi.org/10.1093/biomet/87.2.353")
SAMPLER_SOURCES = (ROBERTS_ROSENTHAL, ROBERTS_GELMAN_GILKS, LIU_SABATTI, ANDRIEU_THOMS)
ROSSI = ("Rossi, P. E., Allenby, G. M., & McCulloch, R. (2005). Bayesian statistics and marketing. Wiley. "
         "https://doi.org/10.1002/0470863692")
ALLENBY = ("Allenby, G. M., & Rossi, P. E. (1998). Marketing models of consumer heterogeneity. Journal of Econometrics, "
           "89(1–2), 57–78. https://doi.org/10.1016/S0304-4076(98)00055-4")
SOURCES = (ROSSI, ALLENBY, TRAIN)


class Data:
    """Choice sets of all persons, stacked by number of alternatives. Design matrices are kept as
    their non-zero entries, so a utility costs one multiplication per non-zero cell (MaxDiff rows have
    one, effects-coded conjoint rows a few)."""

    def __init__(self, person_sets, k):
        # a choice outside its set (an index past the alternatives) would be read as another alternative, or
        # wrap around to the last one: left out (code review 2026-10-15, C5)
        person_sets = [[s for s in sets if 0 <= s.chosen < len(s.X)] for sets in person_sets]
        self.n, self.k = len(person_sets), k
        self.sets_per_person = np.array([len(sets) for sets in person_sets], dtype=float)
        by_size = {}
        for p, sets in enumerate(person_sets):
            for s in sets:
                by_size.setdefault(len(s.X), []).append((p, s))
        self.groups = []
        for size, group in sorted(by_size.items()):
            X = np.stack([s.X for _, s in group])                    # sets x alternatives x params
            owner = np.array([p for p, _ in group])
            chosen = np.array([s.chosen for _, s in group])
            s_idx, j_idx, c_idx = np.nonzero(X)
            self.groups.append({"size": size, "sets": len(group), "owner": owner, "chosen": chosen,
                                "cell": s_idx * size + j_idx, "row_owner": owner[s_idx], "col": c_idx,
                                "val": X[s_idx, j_idx, c_idx], "grad_at": owner[s_idx] * k + c_idx,
                                "chosen_at": np.arange(len(group)) * size + chosen})

    def _probabilities(self, g, B):
        """Utilities (shifted by the row maximum), choice probabilities and the sums of exp of one group of sets."""
        v = np.bincount(g["cell"], weights=g["val"] * B.ravel()[g["grad_at"]],
                        minlength=g["sets"] * g["size"]).reshape(g["sets"], g["size"])
        v -= v.max(axis=1, keepdims=True)
        e = np.exp(v)
        total = e.sum(axis=1)
        return v, e / total[:, None], total

    def loglik_grad(self, B):
        """Log-likelihood per person (N) and its gradient (N x k) for the betas B."""
        n, k = B.shape
        ll, G = np.zeros(n), np.zeros(n * k)
        for g in self.groups:
            v, p, total = self._probabilities(g, B)
            ll += np.bincount(g["owner"], weights=v.ravel()[g["chosen_at"]] - np.log(total), minlength=n)
            p = p.ravel()
            p[g["chosen_at"]] -= 1.0                         # minus (observed - expected) per alternative
            G -= np.bincount(g["grad_at"], weights=g["val"] * p[g["cell"]], minlength=n * k)
        return ll, G.reshape(n, k)

    def information(self, B):
        """Logit information per person at the betas B (N x k x k): sum over sets of sum_j p_j (x_j - xbar)(x_j - xbar)'."""
        n, k = B.shape
        H = np.zeros((n, k, k))
        for g in self.groups:
            _, p, _ = self._probabilities(g, B)
            X = np.zeros((g["sets"] * g["size"], k))
            np.add.at(X, (g["cell"], g["col"]), g["val"])
            X = X.reshape(g["sets"], g["size"], k)
            D = X - np.matmul(p[:, None, :], X)
            np.add.at(H, g["owner"], np.matmul(np.transpose(D * p[:, :, None], (0, 2, 1)), D))
        return H

    def loglik(self, B):
        """Log-likelihood per person (length N) for the betas B (N x k)."""
        ll = np.zeros(self.n)
        for g in self.groups:
            v = np.bincount(g["cell"], weights=g["val"] * B.ravel()[g["grad_at"]],
                            minlength=g["sets"] * g["size"]).reshape(g["sets"], g["size"])
            v -= v.max(axis=1, keepdims=True)
            lp = v[np.arange(g["sets"]), g["chosen"]] - np.log(np.exp(v).sum(axis=1))
            ll += np.bincount(g["owner"], weights=lp, minlength=self.n)
        return ll

    def chance_loglik(self):
        """Log-likelihood per person if every alternative were equally likely."""
        ll = np.zeros(self.n)
        for g in self.groups:
            ll -= np.bincount(g["owner"], minlength=self.n) * math.log(g["size"])
        return ll


def rlh(data, B):
    """Root likelihood per person: geometric mean of the probabilities of the chosen alternatives."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.exp(data.loglik(B) / data.sets_per_person)


def _inv_wishart(rng, df, scale):
    """One draw of Sigma ~ IW(df, scale): Sigma^-1 ~ Wishart(df, scale^-1) by the Bartlett decomposition."""
    k = len(scale)
    L = np.linalg.cholesky(np.linalg.inv(scale))
    A = np.zeros((k, k))
    A[np.diag_indices(k)] = np.sqrt(rng.chisquare(df - np.arange(k)))
    A[np.tril_indices(k, -1)] = rng.standard_normal(k * (k - 1) // 2)
    LA = L @ A
    return np.linalg.inv(LA @ LA.T)


def _draw_mu(rng, mean, Sigma, n, prior_prec):
    """One draw of mu | betas, Sigma for the prior mu ~ N(0, prior_prec^-1): N(V n Sigma^-1 mean, V) with
    V = (prior_prec + n Sigma^-1)^-1; `mean` is the mean of the n betas."""
    s_inv = np.linalg.inv(Sigma)
    V = np.linalg.inv(prior_prec + n * s_inv)
    V = (V + V.T) / 2
    return V @ (n * s_inv @ mean) + np.linalg.cholesky(V) @ rng.standard_normal(len(mean))


def geweke(x, batches=BATCHES):
    """z for "the mean of the first half equals the mean of the second half" of a chain (1-d), with the
    variance of each half mean from batch means (robust to autocorrelation). None if too short."""
    x = np.asarray(x, dtype=float)
    h = len(x) // 2
    if h < 2 * batches:
        return None

    def var_of_mean(y):
        means = np.array([c.mean() for c in np.array_split(y, batches)])
        return means.var(ddof=1) / batches

    se = math.sqrt(var_of_mean(x[:h]) + var_of_mean(x[-h:]))
    return float((x[:h].mean() - x[-h:].mean()) / se) if se > 0 else 0.0


def acceptance_warning(acc):
    """The note for an acceptance rate outside ACCEPT_OK, None inside."""
    if acc is None or ACCEPT_OK[0] <= acc <= ACCEPT_OK[1]:
        return None
    from app.lang import de_en
    from app.units import number, page_lang

    # the decimal mark of the page (Mathe-Audit 2026-10-06, Z-7)
    return de_en("Akzeptanzrate {a} weit vom Ziel {t}; die Kette mischt schlecht.",
                 "Acceptance rate {a} far from the target {t}; the chain mixes poorly.",
                 a=number(acc, 2, page_lang()), t=number(TARGET, 2, page_lang()))


def pack_draws(draws):
    """Kept draws of the betas (draws x persons x parameters) for the stored JSON result: float16 (three significant
    digits, plenty for shares), base64. About 100 draws x 1000 persons x 10 parameters = 2.7 MB of text."""
    import base64

    if draws is None:
        return None
    a = np.asarray(draws, dtype="<f2")
    return {"shape": list(a.shape), "dtype": "<f2", "data": base64.b64encode(a.tobytes()).decode("ascii")}


def unpack_draws(packed):
    """The array of pack_draws (float64), None for a result without draws."""
    import base64

    if not packed:
        return None
    a = np.frombuffer(base64.b64decode(packed["data"]), dtype=packed.get("dtype", "<f2"))
    return a.astype(float).reshape(packed["shape"])


def _quad(D, P):
    """d' P d per row of D (N x k); P one k x k matrix or one per row (N x k x k)."""
    if P.ndim == 2:
        return ((D @ P) * D).sum(axis=1)
    return (np.matmul(P, D[:, :, None])[:, :, 0] * D).sum(axis=1)


def _apply(M, X):
    """M_n x_n per row: M (N x k x k), X (N x k)."""
    return np.matmul(M, X[:, :, None])[:, :, 0]


def _adapt(step, rate, target):
    """Step size(s) after a batch of the burn-in: larger when more than `target` was accepted, at most x e^0.6."""
    return step * np.exp(2 * np.clip(rate - target, -0.3, 0.3))


def _chance(log_ratio):
    """Acceptance probability min(1, exp(log_ratio)): what the settling phase steers by (less noisy than 0 or 1)."""
    return np.exp(np.minimum(np.nan_to_num(log_ratio, nan=-np.inf), 0.0))


def estimate(person_sets, k, burn=10000, draws=10000, thin=10, seed=1, prior_cov=None, prior_df=None,
             init=None, target=TARGET, summary=None, person_stat=None, progress=None, keep=KEEP_DRAWS, spread=None):
    """Run the sampler. person_sets: [[ChoiceSet]] per person (every person at least one set).
    summary(B) -> 1-d array: evaluated at every kept draw (e.g. the mean scores over persons), returned as
    an array (kept draws x length). person_stat(B) -> array (N x q): averaged over the kept draws (posterior
    mean of a non-linear function of each person's betas, e.g. probability scores). spread(Sigma) -> 1-d array:
    averaged over the kept draws (posterior mean of a measure of the spread between persons in the scale the page
    shows, e.g. the mean variance of the zero-centred utilities; H-3). progress(fraction) is called
    about every 2 %. keep: how many kept draws of all betas to return ("draws", evenly spaced). Returns a dict."""
    from app.choice_model import fit

    started = time.monotonic()
    rng = np.random.default_rng(seed)
    data = Data(person_sets, k)
    n = data.n
    prior_cov = np.eye(k) if prior_cov is None else np.asarray(prior_cov, dtype=float)
    df0 = prior_df or k + 5
    S0 = (df0 - k - 1) * prior_cov          # prior mean of Sigma = prior_cov
    mu_prec = np.linalg.inv(MU_PRIOR_SD ** 2 * prior_cov)  # prior precision of mu (prior mean 0)
    if init is None:
        init = fit([s for sets in person_sets for s in sets], k, ridge=0.1)[0]
    mu = np.array(init, dtype=float)
    B = np.tile(mu, (n, 1))
    Sigma = prior_cov.copy()
    ll, G = data.loglik_grad(B)
    H = data.information(B)                  # recomputed once in the middle of the burn-in, at the betas then
    eps = np.ones(n)                         # step size per person
    shift = 1.0                              # step of the joint shift, in units of the summed information
    scale = 0.1 / math.sqrt(k)               # step of the joint scaling, on the log scale
    tally, shift_tally, scale_tally = np.zeros(n), 0, 0
    P = C = R = shift_prec = shift_cov = shift_chol = None
    total = burn + draws
    settle = burn - int(burn * SETTLE)       # first iteration of the settling phase of the step sizes
    log_sums = [np.zeros(n), 0.0, 0.0]       # sums of log eps, log shift, log scale over the settling phase
    keep_every = max(1, (draws // thin) // keep) if keep else 0
    kept, kept_draws = [], []                # (mu, loglik, Sigma diagonal) per kept draw; betas of every keep_every-th
    sums = np.zeros((n, k))
    stat_sums = None
    summaries = []
    spread_sum = None
    accepted_kept = shifted_kept = 0
    report_every = max(1, total // 50)
    for it in range(total):
        # upper level: mu, then Sigma
        mu = _draw_mu(rng, B.mean(axis=0), Sigma, n, mu_prec)
        D = B - mu
        Sigma = _inv_wishart(rng, df0 + n, S0 + D.T @ D)
        Si = np.linalg.inv(Sigma)
        if it and it == burn // 2:
            H = data.information(B)
        if C is None or (it < settle and (it % ADAPT_EVERY == 0 or it == burn // 2)):
            # proposal shape per person, fixed after burn-in (it depends on Sigma at that time, not on the betas)
            P = H + Si
            C = np.linalg.inv(P)
            R = np.linalg.cholesky((C + np.transpose(C, (0, 2, 1))) / 2)
            shift_prec = H.sum(axis=0) + mu_prec
            shift_cov = np.linalg.inv(shift_prec)
            shift_chol = np.linalg.cholesky((shift_cov + shift_cov.T) / 2)
        # lower level: one preconditioned Langevin step per person
        half = (0.5 * eps ** 2)[:, None]
        mean_now = B + half * _apply(C, G - D @ Si)
        prop = mean_now + eps[:, None] * _apply(R, rng.standard_normal((n, k)))
        ll_prop, G_prop = data.loglik_grad(prop)
        D_prop = prop - mu
        mean_back = prop + half * _apply(C, G_prop - D_prop @ Si)
        log_ratio = (ll_prop - 0.5 * _quad(D_prop, Si)) - (ll - 0.5 * _quad(D, Si)) \
            - 0.5 * (_quad(B - mean_back, P) - _quad(prop - mean_now, P)) / eps ** 2
        accept = np.log(rng.random(n)) < log_ratio
        if settle <= it < burn:
            gain = 1 / math.sqrt(it - settle + 1 + SETTLE_OFFSET)
            eps = eps * np.exp(gain * (_chance(log_ratio) - target))
        B[accept], ll[accept], G[accept] = prop[accept], ll_prop[accept], G_prop[accept]
        # joint shift of mu and all betas: the deviations from mu, and so their prior density, stay the same
        # (a Langevin step too, its metric the summed information: nearly a draw from the conditional distribution)
        half_s = 0.5 * shift ** 2
        toward = half_s * (shift_cov @ (G.sum(axis=0) - mu_prec @ mu))
        delta = toward + shift * (shift_chol @ rng.standard_normal(k))
        mu_shift = mu + delta
        ll_shift, G_shift = data.loglik_grad(B + delta)
        back = half_s * (shift_cov @ (G_shift.sum(axis=0) - mu_prec @ mu_shift))
        f, b = delta - toward, -delta - back
        log_moved = (ll_shift.sum() - ll.sum() - 0.5 * (mu_shift @ mu_prec @ mu_shift - mu @ mu_prec @ mu)
                     - 0.5 * (b @ shift_prec @ b - f @ shift_prec @ f) / shift ** 2)
        moved = math.log(rng.random()) < log_moved
        if moved:
            B, mu, ll, G = B + delta, mu_shift, ll_shift, G_shift
        # joint scaling of the deviations beta_n - mu and of Sigma, per parameter by c_j: the density of the betas
        # given mu and Sigma stays (its factor prod c^-N cancels the Jacobian of the betas); left are the likelihood,
        # the inverse Wishart prior of Sigma and the Jacobian prod c^(k+1) of Sigma -> c Sigma c
        logc = scale * rng.standard_normal(k)
        c = np.exp(logc)
        B_scaled = mu + (B - mu) * c
        Si_scaled = Si / np.outer(c, c)
        log_scaled = (data.loglik(B_scaled).sum() - ll.sum() - df0 * logc.sum()
                      - 0.5 * (np.sum(S0 * Si_scaled) - np.sum(S0 * Si)))
        scaled = math.log(rng.random()) < log_scaled
        if scaled:
            B, Sigma = B_scaled, Sigma * np.outer(c, c)
            ll, G = data.loglik_grad(B)
        if settle <= it < burn:
            # settling phase (H-5): the next step sizes from this iteration, with a decreasing gain; averaged on the log
            # scale, the averages are the step sizes of the kept draws
            shift *= math.exp(gain * (float(_chance(log_moved)) - target))
            scale *= math.exp(gain * (float(_chance(log_scaled)) - SCALE_TARGET))
            log_sums[0] += np.log(eps)
            log_sums[1] += math.log(shift)
            log_sums[2] += math.log(scale)
            if it == burn - 1:
                count = burn - settle
                eps = np.exp(log_sums[0] / count)
                shift, scale = math.exp(log_sums[1] / count), math.exp(log_sums[2] / count)
        elif it < burn:
            tally += accept
            shift_tally += moved
            scale_tally += scaled
            if (it + 1) % ADAPT_EVERY == 0:
                eps = _adapt(eps, tally / ADAPT_EVERY, target)
                shift = float(_adapt(shift, shift_tally / ADAPT_EVERY, target))
                scale = float(_adapt(scale, scale_tally / ADAPT_EVERY, SCALE_TARGET))
                tally[:], shift_tally, scale_tally = 0, 0, 0
        else:
            accepted_kept += int(accept.sum())
            shifted_kept += moved
            if (it - burn + 1) % thin == 0:
                sums += B
                kept.append((mu.copy(), float(ll.sum()), np.diag(Sigma).copy()))
                if keep_every and (len(kept) - 1) % keep_every == 0 and len(kept_draws) < keep:
                    kept_draws.append(B.copy())
                if summary is not None:
                    summaries.append(summary(B))
                if spread is not None:
                    value = np.asarray(spread(Sigma), dtype=float)
                    spread_sum = value if spread_sum is None else spread_sum + value
                if person_stat is not None:
                    stat = person_stat(B)
                    stat_sums = stat if stat_sums is None else stat_sums + stat
        if progress and (it + 1) % report_every == 0:
            progress((it + 1) / total)
    n_kept = len(kept)
    beta = sums / max(n_kept, 1)
    mus = np.array([m for m, _, _ in kept])
    lls = np.array([x for _, x, _ in kept])
    z_ll = geweke(lls)
    z_mu = [geweke(mus[:, j]) for j in range(k)] if n_kept else []
    z_mu_flag = sum(1 for z in z_mu if z is not None and abs(z) > GEWEKE_LIMIT)
    checked = z_ll is not None
    # one flagged mean is allowed: among many means one often passes the limit by chance in a healthy run (one of
    # three trial runs at the default length). A mean that drifts because the data set no limit is named by the separation notes of the models.
    converged = checked and abs(z_ll) <= GEWEKE_LIMIT and z_mu_flag <= 1
    half = n_kept // 2
    return {
        "beta": beta, "mu": mus.mean(axis=0) if n_kept else mu,
        "sigma_diag": np.array([s for _, _, s in kept]).mean(axis=0) if n_kept else np.diag(Sigma),
        "acceptance": accepted_kept / (draws * n) if draws and n else None, "rho": float(np.median(eps)),
        "shift_acceptance": shifted_kept / draws if draws else None,
        "rlh": rlh(data, beta), "chance_rlh": np.exp(data.chance_loglik() / data.sets_per_person),
        "loglik": float(data.loglik(beta).sum()), "persons": n, "sets": int(data.sets_per_person.sum()),
        "kept": n_kept, "converged": converged, "checked": checked,
        "geweke_ll": z_ll, "geweke_mu": z_mu, "geweke_mu_flagged": z_mu_flag,
        "ll_halves": [float(lls[:half].mean()), float(lls[-half:].mean())] if half else None,
        "ll_trace": lls, "mu_trace": mus, "summaries": np.array(summaries) if summaries else None,
        "person_stat": None if stat_sums is None else stat_sums / n_kept,
        "spread": None if spread_sum is None else spread_sum / n_kept,
        "draws": np.array(kept_draws) if kept_draws else None,
        "settings": {"burn": burn, "draws": draws, "thin": thin, "seed": seed, "prior_df": df0, "target": target},
        "seconds": time.monotonic() - started,
    }
