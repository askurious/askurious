"""Structural equation models (for everyone since 2026-10-11; concept strukturgleichungsmodelle-konzept.md, Ralf
2026-10-06: all as recommended, PLS from the start). The mathematics only, numpy and plain Python: no database, no
Flask, so that the estimation process (app/estimate.py) can run the bootstrap. app/sem_model.py builds the model from
the study plan and the answers.

A model names latent constructs with their indicators (reflective, at least two), observed variables that take
part in the structural model themselves (a single question, a randomisation coded 0/1) and directed paths between
them. Exogenous variables (no path leads to them) covary freely. "cov" names pairs that covary without a direction
(a hypothesis "Zusammenhang" in the model, modell-zeichnen-konzept.md, F4): between exogenous variables the covariance
that is free anyway, else a covariance of their residuals (Bollen, 1989), standardised by the residual
variances (completely standardised solution). "products" names interaction terms (moderation, modell-zeichnen-konzept.md
F2, Ralf 2026-10-11): {name: {"x", "w", "kind"}}, a construct or observed variable of the model whose indicators are the
products of the indicators of x and w (double mean centred matched pairs, Marsh et al., 2004; Lin et al., 2010), built
by app/sem_model.matrix; covariance-based they are constructs like any other, PLS takes them in two stages
(fit_pls). Two groups as moderator: the same model in both groups with equal loadings, fit_ml_groups.

Covariance-based (CB-SEM), `fit_ml`:
  notation       RAM (McArdle & McDonald, 1984): A directed effects, S variances and covariances, F picks the
                 observed variables; Sigma = F (I - A)^-1 S (I - A)^-T F'. The first indicator of a construct has the
                 loading 1 (marker); the standardised solution does not depend on that choice.
  estimation     maximum likelihood, F_ML = log|Sigma| + tr(S Sigma^-1) - log|S| - p with S the covariance matrix
                 of the complete cases divided by N, the ML sample covariance (Bollen, 1989); Fisher scoring with
                 analytic derivatives and step halving, starting from loadings read off the covariances.
  test           T = N F_ML against chi-square(df), df = p(p+1)/2 - free parameters; robust: the Satorra-Bentler
                 (1994) scaled T / c with c = tr(U Gamma) / df, Gamma the fourth moments of the data.
  errors         robust (sandwich) standard errors (Huber-White with expected information and Gamma; Satorra &
                 Bentler, 1994; commonly called "MLM"); Wald z tests. Standardised values and indirect effects get
                 theirs by the delta method.
  fit            CFI and TLI against the independence model (Bentler, 1990), RMSEA with 90 % interval by inverting
                 the noncentral chi-square (Steiger, 1990), SRMR of the residual correlations (Hu & Bentler, 1999):
                 computed from the ML statistic, beside the scaled test. Robust CFI, TLI and RMSEA beside them
                 (Brosseau-Liard et al., 2012; Brosseau-Liard & Savalei, 2014), and
                 the largest residual correlations as a diagnosis (Kline, 2016), never as a hint to free a path.
  units          every column is divided by its standard deviation before the estimation, and the unstandardised
                 values and errors are taken back to the units of the data afterwards: ML does not depend on the
                 units, the numerics do (an income in euros beside Likert statements; Kline, 2016). Convergence is
                 judged per parameter, identification on the information matrix in correlation form (audit
                 2026-10-06, SEM-2).
  admissible     no negative variance (Heywood case), no standardised loading above 1, a positive definite covariance
                 matrix of the constructs and observed variables of the structural model (no correlation above 1;
                 audit 2026-10-06, SEM-3), an invertible information matrix (identified); otherwise the result
                 carries `problems` and the pages show no verdict.

Variance-based (PLS-SEM, Wold, 1982; Lohmöller, 1989), `fit_pls`: standardised indicators, mode A outer weights,
path weighting scheme, stop below 1e-7 change of the weights (Hair et al., 2022). Path coefficients are OLS of the
construct scores; loadings their correlations with the indicators. PLS has no test of the whole model: significance
only from the bootstrap (`bootstrap`, in the estimation process).

Both: AVE and composite reliability of the standardised loadings (Fornell & Larcker, 1981), HTMT of the indicator
correlations (Henseler et al., 2015), R² of each endogenous construct, indirect effects as products of the
standardised paths (x -> m -> y)."""
import math
from functools import lru_cache

import numpy as np

from app.group_tests import ncx2_sf

ROSSEEL = ("Rosseel, Y. (2012). lavaan: An R package for structural equation modeling. Journal of Statistical "
           "Software, 48(2), 1–36. https://doi.org/10.18637/jss.v048.i02")
MCARDLE = ("McArdle, J. J., & McDonald, R. P. (1984). Some algebraic properties of the Reticular Action Model for "
           "moment structures. British Journal of Mathematical and Statistical Psychology, 37(2), 234–251. "
           "https://doi.org/10.1111/j.2044-8317.1984.tb00802.x")
BOLLEN = "Bollen, K. A. (1989). Structural equations with latent variables. Wiley. https://doi.org/10.1002/9781118619179"
SATORRA = ("Satorra, A., & Bentler, P. M. (1994). Corrections to test statistics and standard errors in covariance "
           "structure analysis. In A. von Eye & C. C. Clogg (Eds.), Latent variables analysis: Applications for "
           "developmental research (pp. 399–419). Sage.")
BENTLER = ("Bentler, P. M. (1990). Comparative fit indexes in structural models. Psychological Bulletin, 107(2), "
           "238–246. https://doi.org/10.1037/0033-2909.107.2.238")
STEIGER = ("Steiger, J. H. (1990). Structural model evaluation and modification: An interval estimation approach. "
           "Multivariate Behavioral Research, 25(2), 173–180. https://doi.org/10.1207/s15327906mbr2502_4")
HU_BENTLER = ("Hu, L., & Bentler, P. M. (1999). Cutoff criteria for fit indexes in covariance structure analysis: "
              "Conventional criteria versus new alternatives. Structural Equation Modeling, 6(1), 1–55. "
              "https://doi.org/10.1080/10705519909540118")
RHEMTULLA = ("Rhemtulla, M., Brosseau-Liard, P. É., & Savalei, V. (2012). When can categorical variables be treated "
             "as continuous? A comparison of robust continuous and categorical SEM estimation methods under "
             "suboptimal conditions. Psychological Methods, 17(3), 354–373. https://doi.org/10.1037/a0029315")
FORNELL = ("Fornell, C., & Larcker, D. F. (1981). Evaluating structural equation models with unobservable variables "
           "and measurement error. Journal of Marketing Research, 18(1), 39–50. https://doi.org/10.1177/002224378101800104")
HENSELER = ("Henseler, J., Ringle, C. M., & Sarstedt, M. (2015). A new criterion for assessing discriminant validity "
            "in variance-based structural equation modeling. Journal of the Academy of Marketing Science, 43(1), "
            "115–135. https://doi.org/10.1007/s11747-014-0403-8")
WOLD = ("Wold, H. (1982). Soft modeling: The basic design and some extensions. In K. G. Jöreskog & H. Wold (Eds.), "
        "Systems under indirect observation: Causality, structure, prediction (Part II, pp. 1–54). North-Holland.")
LOHMOELLER = ("Lohmöller, J.-B. (1989). Latent variable path modeling with partial least squares. Physica. "
              "https://doi.org/10.1007/978-3-642-52512-4")
HAIR = ("Hair, J. F., Hult, G. T. M., Ringle, C. M., & Sarstedt, M. (2022). A primer on partial least squares "
        "structural equation modeling (PLS-SEM) (3rd ed.). Sage.")
PREACHER = ("Preacher, K. J., & Hayes, A. F. (2008). Asymptotic and resampling strategies for assessing and comparing "
            "indirect effects in multiple mediator models. Behavior Research Methods, 40(3), 879–891. "
            "https://doi.org/10.3758/BRM.40.3.879")
SOBEL = ("Sobel, M. E. (1982). Asymptotic confidence intervals for indirect effects in structural equation models. "
         "Sociological Methodology, 13, 290–312. https://doi.org/10.2307/270723")
WOLF = ("Wolf, E. J., Harrington, K. M., Clark, S. L., & Miller, M. W. (2013). Sample size requirements for "
        "structural equation models: An evaluation of power, bias, and solution propriety. Educational and "
        "Psychological Measurement, 73(6), 913–934. https://doi.org/10.1177/0013164413495237")
MARSH = ("Marsh, H. W., Wen, Z., & Hau, K.-T. (2004). Structural equation models of latent interactions: Evaluation of "
         "alternative estimation strategies and indicator construction. Psychological Methods, 9(3), 275–300. "
         "https://doi.org/10.1037/1082-989X.9.3.275")
LIN = ("Lin, G.-C., Wen, Z., Marsh, H. W., & Lin, H.-S. (2010). Structural equation models of latent interactions: "
       "Clarification of orthogonalizing and double-mean-centering strategies. Structural Equation Modeling, 17(3), "
       "374–391. https://doi.org/10.1080/10705511.2010.488999")
MEREDITH = ("Meredith, W. (1993). Measurement invariance, factor analysis and factorial invariance. Psychometrika, 58(4), "
            "525–543. https://doi.org/10.1007/BF02294825")
HAYES = ("Hayes, A. F. (2015). An index and test of linear moderated mediation. Multivariate Behavioral Research, "
         "50(1), 1–22. https://doi.org/10.1080/00273171.2014.962683")
BROSSEAU_LIARD = ("Brosseau-Liard, P. E., Savalei, V., & Li, L. (2012). An investigation of the sample performance of "
                  "two nonnormality corrections for RMSEA. Multivariate Behavioral Research, 47(6), 904–930. "
                  "https://doi.org/10.1080/00273171.2012.715252")
BROSSEAU_SAVALEI = ("Brosseau-Liard, P. E., & Savalei, V. (2014). Adjusting incremental fit indices for nonnormality. "
                    "Multivariate Behavioral Research, 49(5), 460–470. https://doi.org/10.1080/00273171.2014.933697")
PETRESCU = ("Petrescu, M. (2013). Marketing research using single-item indicators in structural equation models. "
            "Journal of Marketing Analytics, 1(2), 99–117. https://doi.org/10.1057/jma.2013.7")
FRANKE_SARSTEDT = ("Franke, G., & Sarstedt, M. (2019). Heuristics versus statistics in discriminant validity testing: "
                   "A comparison of four procedures. Internet Research, 29(3), 430–447. "
                   "https://doi.org/10.1108/IntR-12-2017-0515")
SHMUELI = ("Shmueli, G., Sarstedt, M., Hair, J. F., Cheah, J.-H., Ting, H., Vaithilingam, S., & Ringle, C. M. (2019). "
           "Predictive model assessment in PLS-SEM: Guidelines for using PLSpredict. European Journal of Marketing, "
           "53(11), 2322–2347. https://doi.org/10.1108/EJM-02-2019-0189")
COHEN_1988 = "Cohen, J. (1988). Statistical power analysis for the behavioral sciences (2nd ed.). Erlbaum."
ASPAROUHOV_2005 = ("Asparouhov, T. (2005). Sampling weights in latent variable modeling. Structural Equation "
                   "Modeling, 12(3), 411–434. https://doi.org/10.1207/s15328007sem1203_4")
BURNHAM = ("Burnham, K. P., & Anderson, D. R. (2004). Multimodel inference: Understanding AIC and BIC in model "
           "selection. Sociological Methods & Research, 33(2), 261–304. https://doi.org/10.1177/0049124104268644")
KLINE = "Kline, R. B. (2016). Principles and practice of structural equation modeling (4th ed.). Guilford."
MACCALLUM_1992 = ("MacCallum, R. C., Roznowski, M., & Necowitz, L. B. (1992). Model modifications in covariance structure "
                  "analysis: The problem of capitalization on chance. Psychological Bulletin, 111(3), 490–504. "
                  "https://doi.org/10.1037/0033-2909.111.3.490")
JOHNSON_NEYMAN = ("Johnson, P. O., & Neyman, J. (1936). Tests of certain linear hypotheses and their application to some "
                  "educational problems. Statistical Research Memoirs, 1, 57–93.")
CB_SOURCES = (BOLLEN, MCARDLE, SATORRA, BENTLER, STEIGER, HU_BENTLER, BROSSEAU_LIARD, BROSSEAU_SAVALEI,
              KLINE, MACCALLUM_1992, RHEMTULLA)
PLS_SOURCES = (WOLD, LOHMOELLER, HAIR)
QUALITY_SOURCES = (FORNELL, HENSELER)

Z975 = 1.959963984540054
MAX_ITER = 500
# a standardised path beyond this or an R² outside 0..1 is no solution but a collapse of the estimate, mostly two
# predictors that are almost the same (multicollinearity); such a result counts as inadmissible ("extreme", Ralf
# 2026-10-07: paths of 10^40 and R² below 0 were drawn as if they were values)
EXTREME_PATH = 3.0
TOL = 1e-8
PLS_TOL = 1e-7
PLS_ITER = 300


class SemError(ValueError):
    """A model that cannot be estimated at all (no data, a construct without indicators, ...): `reason` is a key
    for the page."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# --- the model ----------------------------------------------------------------------------------------------------

def check(spec):
    """The model in a fixed form: {"latent": {name: [indicators]}, "observed": [names], "paths": [(from, to)],
    "indirect": [(x, m, y)]}. Raises SemError for a model that names unknown variables or has no path and no
    construct."""
    latent = {name: list(items) for name, items in (spec.get("latent") or {}).items()}
    observed = [o for o in spec.get("observed") or [] if o not in latent]
    # factors of the second order (SEM extensions 2026-10-17, point 8): {name: [constructs of the first order]}
    higher = {name: list(parts) for name, parts in (spec.get("higher") or {}).items()
              if name not in latent and name not in observed}
    for name, parts in higher.items():
        if len(parts) < 2 or any(p not in latent for p in parts):
            raise SemError("indicators")
    known = set(latent) | set(observed) | set(higher)
    paths = []
    for a, b in spec.get("paths") or []:
        if a not in known or b not in known or a == b:
            raise SemError("unknown")
        if (a, b) not in paths:
            paths.append((a, b))
    # a single question as a construct with a fixed reliability (SEM extensions 2026-10-17, point 7): one indicator
    single = {k: float(v) for k, v in (spec.get("single") or {}).items() if k in latent and 0 < float(v) < 1}
    for name, items in latent.items():
        if len(items) < 2 and not (name in single and len(items) == 1):
            raise SemError("indicators")
    indirect = [tuple(t) for t in spec.get("indirect") or [] if (t[0], t[1]) in paths and (t[1], t[2]) in paths]
    cov = []
    for a, b in spec.get("cov") or []:
        if a not in known or b not in known or a == b:
            raise SemError("unknown")
        if (a, b) in paths or (b, a) in paths:  # a path and a covariance between the same two: not identified
            raise SemError("covpath")
        if (a, b) not in cov and (b, a) not in cov:
            cov.append((a, b))
    if not latent and not paths and not cov:
        raise SemError("empty")
    if _cyclic(paths):
        raise SemError("cyclic")
    out = {"latent": latent, "observed": observed, "paths": paths, "indirect": indirect}
    if higher:
        out["higher"] = higher
    if single:
        out["single"] = single
    if spec.get("missing") == "fiml":  # full information ML on the cases with gaps too (point 2, app/sem_means.py)
        out["missing"] = "fiml"
    if spec.get("ordinal"):  # the statements as ordered categories, WLSMV (point 3, app/sem_ordinal.py)
        out["ordinal"] = True
    # formative constructs (PLS mode B, SEM extensions 2026-10-17, point 9): their indicators form them
    formative = [k for k in dict.fromkeys(spec.get("formative") or ()) if k in latent and k not in single]
    if formative:
        out["formative"] = formative
    if cov:  # only when there are any: the key of a stored bootstrap of a model without stays the same
        out["cov"] = cov
    products = {k: dict(v) for k, v in (spec.get("products") or {}).items() if k in known}
    if products:
        out["products"] = products
    # moderated mediations (stage 3, F9): (stage, x, m, y, product) with both of their paths in the model
    modmed = []
    for stage, x, m, y, xw in spec.get("modmed") or []:
        need = [(xw, m), (m, y)] if stage == "a" else [(x, m), (xw, y)]
        if all(p in paths for p in need) and (stage, x, m, y, xw) not in modmed:
            modmed.append((stage, x, m, y, xw))
    if modmed:
        out["modmed"] = modmed
    zero = [tuple(z) for z in spec.get("zero") or [] if tuple(z) in paths]
    if zero:  # paths fixed at 0 (the power of a path, path_power): the target stays endogenous
        out["zero"] = zero
    return out


def _cyclic(paths):
    graph = {}
    for a, b in paths:
        graph.setdefault(a, []).append(b)
    state = {}

    def visit(v):
        state[v] = 1
        for w in graph.get(v, ()):
            if state.get(w) == 1 or (w not in state and visit(w)):
                return True
        state[v] = 2
        return False

    return any(v not in state and visit(v) for v in list(graph))


def columns(spec):
    """The data columns a model needs, in the order fit_ml and fit_pls expect them: the indicators of each construct,
    then the observed variables."""
    spec = check(spec)
    return [i for items in spec["latent"].values() for i in items] + spec["observed"]


def endogenous(spec):
    return {b for _, b in spec["paths"]}


def lower(spec):
    """The constructs of the first order under a factor of the second order: explained by it, so never exogenous."""
    return {p for parts in (spec.get("higher") or {}).values() for p in parts}


def cov_value(values, a, b):
    """The value of the covariance of a and b in a dict keyed by ("covariance", x, y), in either order."""
    found = values.get(("covariance", a, b))
    return found if found is not None else values.get(("covariance", b, a))


# --- covariance-based: RAM matrices -------------------------------------------------------------------------------

class _Ram:
    """The RAM form of a model: variable order manifest (columns) then latent; the free parameters as (matrix, i, j)
    with "A" (j -> i) or "S" (symmetric)."""

    def __init__(self, spec, cols):
        self.spec = spec
        self.manifest = list(cols)
        self.latent = list(spec["latent"]) + list(spec.get("higher") or {})
        self.names = self.manifest + self.latent
        self.index = {name: k for k, name in enumerate(self.names)}
        self.p, self.m = len(self.manifest), len(self.names)
        self.F = np.hstack([np.eye(self.p), np.zeros((self.p, len(self.latent)))])
        self.A0, self.S0 = np.zeros((self.m, self.m)), np.zeros((self.m, self.m))
        self.free = []  # (kind, i, j, label)
        endo = endogenous(spec) | lower(spec)
        for lv, items in list(spec["latent"].items()) + list((spec.get("higher") or {}).items()):
            f = self.index[lv]
            for k, item in enumerate(items):
                i = self.index[item]
                if k == 0:
                    self.A0[i, f] = 1.0  # marker
                else:
                    self.free.append(("A", i, f, ("loading", lv, item)))
        for a, b in spec["paths"]:
            if (a, b) not in spec.get("zero", ()):
                self.free.append(("A", self.index[b], self.index[a], ("path", a, b)))
        indicators = {i for items in spec["latent"].values() for i in items}
        # the indicator of a single-item construct: its error variance fixed at (1 - reliability) of its variance;
        # the data enter in units of their SD (fit_ml, fit_ml_groups) and the planned populations have variance 1,
        # so that is 1 - reliability (Petrescu, 2013; in several groups the variance over all groups)
        fixed = {spec["latent"][lv][0]: rel for lv, rel in (spec.get("single") or {}).items()}
        for item in self.manifest:
            if item in fixed:
                k = self.index[item]
                self.S0[k, k] = 1.0 - fixed[item]
            elif item in indicators:
                k = self.index[item]
                self.free.append(("S", k, k, ("residual", item, item)))
        structural = self.latent + spec["observed"]
        for v in structural:
            k = self.index[v]
            self.free.append(("S", k, k, ("variance", v, v)))
        exo = [v for v in structural if v not in endo]
        for x in range(len(exo)):
            for y in range(x + 1, len(exo)):
                self.free.append(("S", self.index[exo[y]], self.index[exo[x]], ("covariance", exo[x], exo[y])))
        for a, b in spec.get("cov", ()):  # between exogenous ones already free, else between the residuals
            if a in endo or b in endo:
                self.free.append(("S", self.index[b], self.index[a], ("covariance", a, b)))
        self.q = len(self.free)

    def factors(self, unit):
        """Per free parameter the factor from columns in units of their SD (unit: the SD of each column) back to the
        units of the data: a construct has the unit of its marker; A[i, j] * g_i / g_j, S[i, j] * g_i * g_j."""
        g = np.ones(self.m)
        g[:self.p] = unit
        for lv, items in self.spec["latent"].items():
            g[self.index[lv]] = unit[self.index[items[0]]]
        return np.array([g[i] / g[j] if kind == "A" else g[i] * g[j] for kind, i, j, _ in self.free])

    def matrices(self, theta):
        A, S = self.A0.copy(), self.S0.copy()
        for value, (kind, i, j, _) in zip(theta, self.free):
            if kind == "A":
                A[i, j] = value
            else:
                S[i, j] = S[j, i] = value
        return A, S

    def implied(self, theta):
        """(Sigma of the observed, B = (I - A)^-1, covariance of all variables)."""
        A, S = self.matrices(theta)
        B = np.linalg.inv(np.eye(self.m) - A)
        full = B @ S @ B.T
        return full[:self.p, :self.p], B, full

    def derivatives(self, theta):
        """Sigma and the stack of dSigma/dtheta_k (q, p, p)."""
        sigma, B, full = self.implied(theta)
        FB, FC = B[:self.p], full[:self.p]
        out = np.empty((self.q, self.p, self.p))
        for k, (kind, i, j, _) in enumerate(self.free):
            if kind == "A":
                u, v = FB[:, i], FC[:, j]
            else:
                u, v = FB[:, i], FB[:, j]
                if i == j:
                    out[k] = np.outer(u, u)
                    continue
            d = np.outer(u, v)
            out[k] = d + d.T
        return sigma, out

    def start(self, s):
        """Start values: a construct's variance 60 % of its marker's, loadings from the covariances with the marker,
        residuals what is left (at least a tenth), paths and covariances 0."""
        theta = np.zeros(self.q)
        var = np.diag(s)
        phi = {}
        for lv, items in self.spec["latent"].items():
            phi[lv] = (self.spec.get("single") or {}).get(lv, 0.6) * var[self.index[items[0]]]
        for lv, parts in (self.spec.get("higher") or {}).items():  # half of the first lower construct's variance
            phi[lv] = 0.5 * phi[parts[0]]
        loads = {}
        for k, (kind, i, j, label) in enumerate(self.free):
            if label[0] == "loading" and label[1] in (self.spec.get("higher") or {}):
                theta[k] = 1.0
            elif label[0] == "loading":
                marker = self.index[self.spec["latent"][label[1]][0]]
                theta[k] = s[i, marker] / phi[label[1]]
                loads[i] = theta[k]
        for lv, items in self.spec["latent"].items():
            loads[self.index[items[0]]] = 1.0
        owner = {self.index[i]: lv for lv, items in self.spec["latent"].items() for i in items}
        for k, (kind, i, j, label) in enumerate(self.free):
            if label[0] == "residual":
                theta[k] = max(0.1 * var[i], var[i] - loads[i] ** 2 * phi[owner[i]])
            elif label[0] == "variance" and label[1] in lower(self.spec):  # the disturbance of a lower construct
                theta[k] = 0.5 * phi[label[1]]
            elif label[0] == "variance":
                theta[k] = phi[label[1]] if label[1] in phi else var[i]
        return theta


def _vech_index(p):
    rows, cols = np.tril_indices(p)
    return rows, cols


def _trace_products(m):
    """[tr(m_k m_l)] of a stack of q p x p matrices, as one matrix product: einsum("kij,lji->kl") took most of an
    estimate of 100 statements (code review 2026-10-15, A3)."""
    q = len(m)
    return m.reshape(q, -1) @ m.transpose(0, 2, 1).reshape(q, -1).T


def _discrepancy(sigma, s, logdet_s, p):
    sign, logdet = np.linalg.slogdet(sigma)
    if sign <= 0:
        return math.inf, None
    inv = np.linalg.inv(sigma)
    return logdet + float(np.sum(s * inv)) - logdet_s - p, inv


# The robust parts need Gamma, the asymptotic covariance of the half-vectorised sample covariances (cov of
# (x_i - m)(x_i - m)'), only as wd' Gamma wd and tr(W Gamma): both are taken from the data in blocks of CHUNK cases,
# without the dense p* x p* matrices Gamma and W (p* = p (p + 1) / 2; 100 variables: 5050 x 5050, 27 s and 1 GB in
# the request; code review 2026-10-15, A3). The same numbers as before up to rounding.
CHUNK = 256


def _wmean(x, w):
    return x.mean(axis=0) if w is None else (w @ x) / len(x)


def _centred_products(x, w=None):
    """Blocks of rows (x_i - m)(x_i - m)' - S, half-vectorised (lower triangle with the diagonal); S = their mean.
    With sampling weights w (mean 1) the weighted mean and S, each row times its weight (Asparouhov, 2005)."""
    rows, cols = _vech_index(x.shape[1])
    c = x - _wmean(x, w)
    mean = ((c if w is None else c * w[:, None]).T @ c / len(c))[rows, cols]
    for start in range(0, len(c), CHUNK):
        part = c[start:start + CHUNK]
        d = part[:, rows] * part[:, cols] - mean
        yield d if w is None else d * w[start:start + CHUNK, None]


def _meat(x, wd, w=None):
    """wd' Gamma wd."""
    out = np.zeros((wd.shape[1], wd.shape[1]))
    for d in _centred_products(x, w):
        a = d @ wd
        out += a.T @ a
    return out / len(x)


def _trace_w_gamma(x, inv, w=None):
    """tr(W Gamma) with W = 1/2 D'(Sigma^-1 x Sigma^-1) D: vech(A)' W vech(B) = 1/2 tr(Sigma^-1 A Sigma^-1 B), so
    with M_i = c_i c_i' - S it is the mean of 1/2 tr(Sigma^-1 M_i Sigma^-1 M_i) = 1/2 (mean of (c_i' Sigma^-1 c_i)^2
    - tr(Sigma^-1 S Sigma^-1 S)). With weights w (mean 1): M_i times w_i, the cross term no longer cancels."""
    c = x - _wmean(x, w)
    if w is None:
        s = c.T @ c / len(c)
        g = np.einsum("ij,jk,ik->i", c, inv, c)
        vs = inv @ s
        return 0.5 * (float(np.mean(g * g)) - float(np.sum(vs * vs.T)))
    s = (c * w[:, None]).T @ c / len(c)
    g = np.einsum("ij,jk,ik->i", c, inv, c)
    h = np.einsum("ij,jk,ik->i", c, inv @ s @ inv, c)
    vs = inv @ s
    t = float(np.sum(vs * vs.T))
    return 0.5 * float(np.mean(w * w * (g * g - 2 * h + t)))


TOLERANCE = 1e-7  # rmsea_ci: relative width of the bracket at which the bisection stops


def rmsea_ci(t, df, n, level=0.90):
    """The interval of the RMSEA (Steiger, 1990): the noncentralities lam at which T is the upper and the lower
    (1 - level) / 2 quantile, then sqrt(lam / (df N))."""
    if df <= 0:
        return None, None
    tail = (1 - level) / 2

    def solve(target):  # lam with P(X > t | lam) = target; 0 if even lam = 0 lies above
        if ncx2_sf(t, df, 0.0) >= target:
            return 0.0
        lo, hi = 0.0, max(10.0, 2 * t)
        while ncx2_sf(t, df, hi) < target:
            hi *= 2
        # halved until the bracket is narrower than 1e-7 of lam (the RMSEA then holds 7 digits): about 30 steps
        # instead of a fixed 80, each one a Poisson sum (audit 2026-10-11, N16: two thirds of a cold estimate)
        while hi - lo > TOLERANCE * max(1.0, hi):
            mid = (lo + hi) / 2
            if ncx2_sf(t, df, mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    return math.sqrt(solve(tail) / (df * n)), math.sqrt(solve(1 - tail) / (df * n))


def _normal_p(z):
    return math.erfc(abs(z) / math.sqrt(2))


def fit_ml(x, cols, spec, robust=True, start=None, light=False, weights=None):
    """The covariance-based model on the complete cases x (n x p, columns cols as columns(spec)). Returns a dict,
    see the module text; light: only the estimates (the bootstrap), no errors, no fit. weights: sampling weights of
    the cases (SEM extensions 2026-10-17, point 5): the weighted covariances, the robust parts with each case's
    contribution times its weight, the weights scaled to a mean of 1 (Asparouhov, 2005)."""
    spec = check(spec)
    x = np.asarray(x, dtype=float)
    n, p = x.shape
    if n < 3 or p != len(cols):
        raise SemError("data")
    ram = _Ram(spec, cols)
    w = None
    if weights is not None:
        w = np.asarray(weights, dtype=float)
        if len(w) != n or np.any(w < 0) or w.sum() <= 0:
            raise SemError("data")
        w = w * n / w.sum()
        c = x - _wmean(x, w)
        s = (c * w[:, None]).T @ c / n
    else:
        s = np.cov(x, rowvar=False, bias=True)
    if np.any(np.diag(s) <= 0):
        raise SemError("constant")
    # every column in units of its SD (SEM-2): estimated in those units, the values back in the data's afterwards
    unit = np.sqrt(np.diag(s))
    s = s / np.outer(unit, unit)
    x = x / unit
    factor = ram.factors(unit)
    if start is not None:
        start = np.asarray(start, dtype=float) / factor
    sign, logdet_s = np.linalg.slogdet(s)
    if sign <= 0:
        raise SemError("singular")
    theta, f, converged = _optimise(ram, s, logdet_s, start)
    if not converged:
        return {"method": "ml", "converged": False, "n": n, "theta": (theta * factor).tolist(), "problems": ["converge"]}
    if light:
        std = _standardised(ram, theta)
        return {"method": "ml", "converged": True, "n": n, "theta": (theta * factor).tolist(), "f": f, "std": std,
                "admissible": not _inadmissible(ram, theta, std)}
    out = _ml_report(ram, spec, x, s, theta, f, n, robust, factor, unit, w)
    if w is not None:
        out["weighted"] = True
    return out


def _optimise(ram, s, logdet_s, start=None):
    """Fisher scoring of F_ML for the covariance matrix s: (theta, F, converged)."""
    p = ram.p
    theta = ram.start(s) if start is None else np.array(start, dtype=float)
    sigma, _, _ = ram.implied(theta)
    f, inv = _discrepancy(sigma, s, logdet_s, p)
    if inv is None:
        theta = ram.start(s)
        sigma, _, _ = ram.implied(theta)
        f, inv = _discrepancy(sigma, s, logdet_s, p)
    converged = False
    for it in range(MAX_ITER):
        sigma, d = ram.derivatives(theta)
        inv = np.linalg.inv(sigma)
        m = inv[None] @ d  # Sigma^-1 dSigma_k
        info = _trace_products(m)  # expected Hessian of F
        grad = np.einsum("ij,kji->k", inv @ (sigma - s), m)
        try:
            step = -np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            step = -np.linalg.lstsq(info, grad, rcond=None)[0]
        if np.all(np.abs(step) < TOL * (1 + np.abs(theta))):  # per parameter, not relative to the largest (SEM-2)
            converged = True
            break
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            f_new, inv_new = _discrepancy(ram.implied(new)[0], s, logdet_s, p)
            if inv_new is not None and f_new <= f + 1e-12:
                break
            t /= 2
        else:
            break
        theta, f = new, f_new
        if np.all(np.abs(t * step) < TOL * (1 + np.abs(theta))):
            converged = True
            break
    return theta, f, converged


def _standardised(ram, theta):
    """{label: standardised value} of every free parameter (and the fixed marker loadings), plus R² per endogenous
    variable: the completely standardised solution (Bollen, 1989)."""
    A, S = ram.matrices(theta)
    _, _, full = ram.implied(theta)
    sd = np.sqrt(np.maximum(np.diag(full), 1e-300))
    out = {}
    for lv, items in list(ram.spec["latent"].items()) + list((ram.spec.get("higher") or {}).items()):
        f = ram.index[lv]
        for item in items:
            i = ram.index[item]
            out[("loading", lv, item)] = A[i, f] * sd[f] / sd[i]
    for a, b in ram.spec["paths"]:
        i, j = ram.index[b], ram.index[a]
        out[("path", a, b)] = A[i, j] * sd[j] / sd[i]
    for lv in ram.spec.get("single") or {}:  # the fixed error variance of a single indicator
        item = ram.spec["latent"][lv][0]
        i = ram.index[item]
        out[("residual", item, item)] = S[i, i] / full[i, i]
    for kind, i, j, label in ram.free:
        if label[0] in ("residual", "variance"):
            out[label] = S[i, i] / full[i, i]
        elif label[0] == "covariance":  # by the residual variances (completely standardised), exogenous ones too
            out[label] = S[i, j] / (math.sqrt(max(S[i, i], 1e-300)) * math.sqrt(max(S[j, j], 1e-300)))
    for v in endogenous(ram.spec):
        k = ram.index[v]
        out[("r2", v, v)] = 1 - S[k, k] / full[k, k]
    for x, m, y in ram.spec["indirect"]:
        out[("indirect", x, m, y)] = out[("path", x, m)] * out[("path", m, y)]
    return out


def _identified(hess):
    """Is the information matrix invertible? Judged in correlation form D^-1/2 I D^-1/2 (D = diag I), so that the
    units of the parameters do not count (SEM-2)."""
    dg = np.diag(hess)
    if not np.all(np.isfinite(dg)) or np.any(dg <= 0):
        return False
    try:
        cond = np.linalg.cond(hess / np.sqrt(np.outer(dg, dg)))
    except np.linalg.LinAlgError:
        return False
    return bool(np.isfinite(cond) and cond <= 1e12)


def _inadmissible(ram, theta, std):
    """The problems of a solution: "heywood" (a negative variance or a standardised loading above 1), "latent_pd"
    (the covariance matrix of the constructs and observed variables of the structural model is not positive definite,
    e.g. two constructs correlate above 1; SEM-3)."""
    A, S = ram.matrices(theta)
    out = []
    if any(label[0] in ("residual", "variance") and S[i, i] < 0 for kind, i, j, label in ram.free) or any(
            abs(v) > 1 + 1e-9 for key, v in std.items() if key[0] == "loading"):
        out.append("heywood")
    if any(not np.isfinite(v) or abs(v) > EXTREME_PATH for key, v in std.items() if key[0] == "path") or any(
            not np.isfinite(v) or v < -1e-3 or v > 1 + 1e-3 for key, v in std.items() if key[0] == "r2"):
        out.append("extreme")
    names = [v for v in ram.latent if v not in lower(ram.spec)] + ram.spec["observed"]
    if len(names) > 1:
        _, _, full = ram.implied(theta)
        idx = [ram.index[v] for v in names]
        sub = full[np.ix_(idx, idx)]
        dg = np.diag(sub)
        if np.all(dg > 0):  # a negative variance is a Heywood case already
            corr = sub / np.sqrt(np.outer(dg, dg))
            if np.max(np.abs(corr - np.eye(len(idx)))) > 1 + 1e-9 or np.linalg.eigvalsh(corr)[0] < 1e-10:
                out.append("latent_pd")
    return out


def _ml_report(ram, spec, x, s, theta, f, n, robust, factor=None, unit=None, w=None):
    """The full report of an estimate in units of the SD (x, s scaled); factor takes the unstandardised values and
    errors back to the units of the data (fit_ml), unit (the SD of each column) the model-implied SDs."""
    p, q = ram.p, ram.q
    factor = np.ones(q) if factor is None else factor
    sigma, d = ram.derivatives(theta)
    inv = np.linalg.inv(sigma)
    m = inv[None] @ d
    hess = _trace_products(m)  # = 2 Delta' W Delta
    problems = []
    if not _identified(hess):
        problems.append("identified")
        bread = np.linalg.pinv(hess / 2)
    else:
        bread = np.linalg.inv(hess / 2)
    rows, cols = _vech_index(p)
    df = len(rows) - q
    if df < 0:
        problems.append("identified")
    vcov = bread / n
    scaled = None
    if robust:
        off = (rows != cols).astype(float)
        # W Delta: column k = 1/2 D' vec(Sigma^-1 dSigma_k Sigma^-1)
        wd = 0.5 * np.stack([(inv @ dk @ inv)[rows, cols] * (1 + off) for dk in d], axis=1)
        meat = _meat(x, wd, w)
        vcov = bread @ meat @ bread / n
        if df > 0:
            # c = tr(U Gamma) / df with U = W - W Delta bread Delta' W (Satorra & Bentler)
            c = (_trace_w_gamma(x, inv, w) - float(np.sum(bread * meat.T))) / df
            scaled = {"c": c, "chi2": n * f / c if c > 0 else None}
            scaled["p"] = _chi2_p(scaled["chi2"], df) if scaled["chi2"] is not None else None
    se = np.sqrt(np.maximum(np.diag(vcov), 0.0))
    chi2 = n * f
    out = {"method": "ml", "robust": robust, "converged": True, "n": n, "p_vars": p, "q": q, "df": df,
           "chi2": chi2, "p": _chi2_p(chi2, df) if df > 0 else None, "scaled": scaled,
           "theta": (theta * factor).tolist()}
    # fit against the independence model (only variances free)
    base_f = float(np.sum(np.log(np.diag(s)))) - np.linalg.slogdet(s)[1]
    base_chi2, base_df = n * base_f, p * (p - 1) // 2
    out["baseline"] = (base_chi2, base_df)
    if df > 0 and base_df > 0:
        d_model, d_base = max(chi2 - df, 0.0), max(base_chi2 - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = ((base_chi2 / base_df - chi2 / df) / (base_chi2 / base_df - 1)) if base_chi2 / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(chi2 - df, 0.0) / (df * n))
        out["rmsea_ci"] = rmsea_ci(chi2, df, n)
        out["rmsea_close"] = 1 - _ncx2_cdf_close(chi2, df, n)
        if scaled and scaled.get("chi2") is not None:
            out["robust_fit"] = _robust_fit(x, s, chi2, df, n, scaled["c"], base_chi2, base_df, w)
    sd = np.sqrt(np.diag(s))
    resid = (s - sigma) / np.outer(sd, sd)
    out["srmr"] = math.sqrt(float(np.mean(resid[rows, cols] ** 2)))
    out["residuals"] = _largest_residuals(s, sigma, ram.manifest)
    sd_s = np.sqrt(np.diag(s))
    out.update(_tables(ram, spec, theta, vcov, factor, unit, s / np.outer(sd_s, sd_s)))
    problems += out.pop("inadmissible")
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    return out


def _tables(ram, spec, theta, vcov, factor, unit, corr):
    """The estimates of a covariance-based fit for the pages (fit_ml; FIML in app/sem_means.py): loadings, paths,
    indirect effects, R², covariances with standardised values and delta-method errors from vcov (the covariance
    of theta in units of the SD), the quality of the measurement (corr: the correlations of the columns), the
    model-implied correlations and SDs, the covariance of the unstandardised paths and the problems of the
    solution ("inadmissible")."""
    q = ram.q
    se = np.sqrt(np.maximum(np.diag(vcov), 0.0))
    out = {}
    # estimates, standardised values with delta-method errors
    std = _standardised(ram, theta)
    keys = list(std)
    jac = np.empty((len(keys), q))
    for k in range(q):
        h = 1e-6 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        su, sd_ = _standardised(ram, up), _standardised(ram, down)
        jac[:, k] = [(su[key] - sd_[key]) / (2 * h) for key in keys]
    std_se = np.sqrt(np.maximum(np.einsum("ik,kl,il->i", jac, vcov, jac), 0.0))
    std_se = dict(zip(keys, std_se))
    est = {label: (theta[k] * factor[k], se[k] * factor[k]) for k, (_, _, _, label) in enumerate(ram.free)}
    out["loadings"] = {}
    for lv, parts in (spec.get("higher") or {}).items():  # second order: the constructs of the first as its indicators
        rows_ = []
        for part in parts:
            key = ("loading", lv, part)
            value, error = est.get(key, (1.0, None))
            rows_.append({"item": part, "est": value, "se": error, "std": std[key], "std_se": std_se[key],
                          "p": _normal_p(value / error) if error else None, "residual": std[("variance", part, part)]})
        out.setdefault("higher", {})[lv] = rows_
    for lv, items in spec["latent"].items():
        rows_ = []
        for k, item in enumerate(items):
            key = ("loading", lv, item)
            value, error = est.get(key, (1.0, None))
            rows_.append({"item": item, "est": value, "se": error, "std": std[key], "std_se": std_se[key],
                          "p": _normal_p(value / error) if error else None, "residual": std[("residual", item, item)]})
        out["loadings"][lv] = rows_
    out["paths"] = []
    for a, b in spec["paths"]:
        key = ("path", a, b)
        value, error = est[key]
        z = value / error if error else None
        out["paths"].append({"from": a, "to": b, "est": value, "se": error, "z": z,
                             "p": _normal_p(z) if z is not None else None, "std": std[key], "std_se": std_se[key],
                             "ci": (std[key] - Z975 * std_se[key], std[key] + Z975 * std_se[key])})
    out["indirect"] = []
    for x_, m_, y_ in spec["indirect"]:
        key = ("indirect", x_, m_, y_)
        z = std[key] / std_se[key] if std_se[key] else None
        out["indirect"].append({"x": x_, "m": m_, "y": y_, "std": std[key], "std_se": std_se[key],
                                "p": _normal_p(z) if z is not None else None,
                                "ci": (std[key] - Z975 * std_se[key], std[key] + Z975 * std_se[key])})
    out["r2"] = {v: std[("r2", v, v)] for v in endogenous(spec)}
    out["covariances"] = []
    for _, _, _, label in ram.free:
        if label[0] == "covariance":
            value, error = est[label]
            z = std[label] / std_se[label] if std_se[label] else None
            out["covariances"].append({"a": label[1], "b": label[2], "est": value, "se": error, "std": std[label],
                                       "std_se": std_se[label], "p": _normal_p(z) if z is not None else None,
                                       "ci": (std[label] - Z975 * std_se[label], std[label] + Z975 * std_se[label])})
    # admissible: no negative variance, no standardised loading beyond 1, no constructs correlated beyond 1
    out["inadmissible"] = _inadmissible(ram, theta, std)
    out["quality"] = quality(spec, out["loadings"], corr, list(ram.manifest))
    out["latent_corr"] = _latent_correlations(ram, theta)
    out["latent_sd"] = _latent_sds(ram, theta, unit)
    # the (robust) covariance of the unstandardised paths, for the joint test of several paths (wald)
    idx = [k for k, (_, _, _, label) in enumerate(ram.free) if label[0] == "path"]
    out["path_vcov"] = {"paths": [(ram.free[k][3][1], ram.free[k][3][2]) for k in idx],
                        "matrix": (vcov[np.ix_(idx, idx)] * np.outer(factor[idx], factor[idx])).tolist()}
    return out


def _baseline_scaling(x, s, w=None):
    """The Satorra-Bentler scaling factor c_B of the independence model (only the variances free, Sigma_B = diag S):
    tr(U_B Gamma) / df_B as for the model (Brosseau-Liard & Savalei, 2014). x, s in units of the SD."""
    p = s.shape[0]
    base_df = p * (p - 1) // 2
    if base_df <= 0:
        return None
    rows, cols = _vech_index(p)
    off = (rows != cols).astype(float)
    inv = np.diag(1.0 / np.diag(s))
    d = np.zeros((p, p, p))
    for k in range(p):
        d[k, k, k] = 1.0
    m = inv[None] @ d
    bread = np.linalg.inv(_trace_products(m) / 2)
    wd = 0.5 * np.stack([(inv @ dk @ inv)[rows, cols] * (1 + off) for dk in d], axis=1)
    meat = _meat(x, wd, w)
    return (_trace_w_gamma(x, inv, w) - float(np.sum(bread * meat.T))) / base_df


def _robust_fit(x, s, chi2, df, n, c, base_chi2, base_df, w=None):
    """CFI, TLI and RMSEA corrected for non-normal data with the scaling factors c (model) and c_B (independence
    model) of the Satorra-Bentler test: RMSEA = sqrt(max(T - c df, 0) / (N df))
    (Brosseau-Liard et al., 2012), CFI = 1 - max(T - c df, 0) / max(T - c df, T_B - c_B df_B, 0) and TLI = 1 -
    ((T - c df) / df) / ((T_B - c_B df_B) / df_B) (Brosseau-Liard & Savalei, 2014). The 90 % interval of the RMSEA:
    the interval of the scaled statistic T / c, times sqrt(c) (Brosseau-Liard et al., 2012)."""
    if not c or c <= 0:
        return None
    c_base = _baseline_scaling(x, s, w)
    out = {"c": c, "c_base": c_base}
    d_model = chi2 - c * df
    out["rmsea"] = math.sqrt(max(d_model, 0.0) / (n * df))
    lo, hi = rmsea_ci(chi2 / c, df, n)
    out["rmsea_ci"] = (lo * math.sqrt(c), hi * math.sqrt(c)) if lo is not None else (None, None)
    if c_base and c_base > 0:
        d_base = base_chi2 - c_base * base_df
        top = max(d_model, d_base, 0.0)
        out["cfi"] = 1.0 if top == 0 else float(1 - max(d_model, 0.0) / top)
        out["tli"] = float(1 - (d_model / df) / (d_base / base_df)) if d_base > 0 else None
    return out


RESIDUAL_TOP = 10  # the largest residual correlations kept for the diagnosis
RESIDUAL_MARK = 0.10  # Festlegung nach Kline (2016): an absolute correlation residual above .10 points at misfit


def _largest_residuals(s, sigma, names):
    """The largest differences between the observed correlations and those the model implies (residual
    correlations after Bollen, 1989: both matrices as correlations), off the diagonal: [(a, b, residual)] largest first.
    Only a diagnosis: freeing what they point at fits the model to chance (MacCallum et al., 1992)."""
    sd_s, sd_m = np.sqrt(np.diag(s)), np.sqrt(np.maximum(np.diag(sigma), 1e-300))
    diff = s / np.outer(sd_s, sd_s) - sigma / np.outer(sd_m, sd_m)
    i, j = np.triu_indices(len(names), 1)
    order = np.argsort(-np.abs(diff[i, j]))[:RESIDUAL_TOP]
    return [(names[i[k]], names[j[k]], float(diff[i[k], j[k]])) for k in order]


def slope_region(b1, b3, v11, v13, v33, z=Z975):
    """Johnson-Neyman (Johnson & Neyman, 1936; Hayes, 2022): where the slope b1 + b3 w of x -> y is significant at
    the two-sided level of z, from the covariance of b1 and b3 (v11, v13, v33). (b1 + b3 w)^2 > z^2 (v11 + 2 w v13 +
    w^2 v33) is a quadratic a w^2 + b w + c > 0. Returns {"kind": "all" | "none" | "outside" | "inside" | "above" |
    "below", "bounds": [w, ...]}: "outside" significant below the lower and above the upper bound, "inside" between
    them, "above"/"below" beyond one bound (a = 0)."""
    zz = z * z
    a = b3 * b3 - zz * v33
    b = 2 * (b1 * b3 - zz * v13)
    c = b1 * b1 - zz * v11
    if abs(a) < 1e-15:
        if abs(b) < 1e-15:
            return {"kind": "all" if c > 0 else "none", "bounds": []}
        root = -c / b
        return {"kind": "above" if b > 0 else "below", "bounds": [root]}
    disc = b * b - 4 * a * c
    if disc <= 0:
        return {"kind": "all" if a > 0 else "none", "bounds": []}
    r = math.sqrt(disc)
    lo, hi = sorted(((-b - r) / (2 * a), (-b + r) / (2 * a)))
    return {"kind": "outside" if a > 0 else "inside", "bounds": [lo, hi]}


def path_cov(fit, pairs):
    """The (robust) covariance matrix of the unstandardised paths `pairs` of a covariance-based fit, or None."""
    found = fit.get("path_vcov") or {}
    names = [tuple(p) for p in found.get("paths") or []]
    if any(tuple(p) not in names for p in pairs):
        return None
    idx = [names.index(tuple(p)) for p in pairs]
    return np.asarray(found["matrix"], dtype=float)[np.ix_(idx, idx)]


def wald(fit, pairs):
    """The joint Wald test that all paths `pairs` are 0 (covariance-based fit_ml result): (chi2, df, p) or None."""
    found = fit.get("path_vcov") or {}
    names = [tuple(p) for p in found.get("paths") or []]
    if not names or any(tuple(p) not in names for p in pairs):
        return None
    idx = [names.index(tuple(p)) for p in pairs]
    v = np.asarray(found["matrix"], dtype=float)[np.ix_(idx, idx)]
    est = {(r["from"], r["to"]): r["est"] for r in fit.get("paths") or []}
    b = np.array([est[tuple(p)] for p in pairs])
    try:
        chi2 = float(b @ np.linalg.solve(v, b))
    except np.linalg.LinAlgError:
        return None
    return chi2, len(pairs), _chi2_p(chi2, len(pairs))


def _ncx2_cdf_close(t, df, n, eps=0.05):
    """P(T <= t) under RMSEA = eps (the test of close fit, Browne & Cudeck): its complement is the p of close fit."""
    return 1 - ncx2_sf(t, df, eps * eps * df * n)


def _chi2_p(chi2, df):
    from app.group_tests import chi2_sf

    return chi2_sf(chi2, df) if df > 0 else None


def _latent_sds(ram, theta, unit=None):
    """{variable: model-implied SD} of every construct and observed variable of the model, in the units of the data:
    a construct in the unit of its marker, so that an interaction term (marker: the product of the markers of x and
    w) has the unit of the product. For simple slopes and conditional effects (audit 2026-10-12, SEM-4)."""
    _, _, full = ram.implied(theta)
    g = np.ones(ram.m)
    if unit is not None:
        g[:ram.p] = unit
        for lv, items in ram.spec["latent"].items():
            g[ram.index[lv]] = unit[ram.index[items[0]]]
    return {v: float(math.sqrt(max(full[ram.index[v], ram.index[v]], 0.0)) * g[ram.index[v]])
            for v in ram.latent + ram.spec["observed"]}


def _latent_correlations(ram, theta):
    _, _, full = ram.implied(theta)
    names = ram.latent + ram.spec["observed"]
    idx = [ram.index[v] for v in names]
    sub = full[np.ix_(idx, idx)]
    sd = np.sqrt(np.maximum(np.diag(sub), 1e-300))  # a negative variance (Heywood case) is flagged elsewhere
    corr = sub / np.outer(sd, sd)
    return {"names": names, "matrix": corr.tolist()}


# --- measurement quality, both approaches -------------------------------------------------------------------------

def quality(spec, loadings, corr, cols):
    """Per construct AVE and composite reliability of the standardised loadings (Fornell & Larcker, 1981), and HTMT
    for each pair of constructs (Henseler et al., 2015): the mean correlation between their indicators over the
    geometric mean of the mean correlations within each. This is the original HTMT, not HTMT+ (Ringle et al., 2023,
    with absolute correlations) and not HTMT2 (Roemer et al., 2021, geometric means); Ralf kept it (audit 2026-10-23,
    R-M1). Mixed signs among the correlations between two constructs cancel in its mean, so the value can be too
    favourable: "htmt_mixed" lists such pairs [a, b] for a warning. corr: the correlations of the columns cols."""
    out = {"ave": {}, "cr": {}, "htmt": {}, "htmt_mixed": []}
    single = set(spec.get("single") or {}) | set(spec.get("formative") or ())  # AVE, CR, HTMT need reflective ones
    loadings = {lv: rows for lv, rows in loadings.items() if lv not in single}
    for lv, rows in loadings.items():
        lam = np.array([r["std"] for r in rows])
        out["ave"][lv] = float(np.mean(lam ** 2))
        total = float(np.sum(lam)) ** 2
        out["cr"][lv] = total / (total + float(np.sum(1 - lam ** 2))) if total > 0 else None
    pos = {c: k for k, c in enumerate(cols)}
    names = [lv for lv in spec["latent"] if lv not in single]

    def within(items):
        idx = [pos[i] for i in items]
        sub = corr[np.ix_(idx, idx)]
        return float(np.mean(sub[np.triu_indices(len(idx), 1)]))

    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            ia = [pos[i] for i in spec["latent"][names[a]]]
            ib = [pos[i] for i in spec["latent"][names[b]]]
            between = corr[np.ix_(ia, ib)]
            hetero = float(np.mean(between))
            if np.any(between > 0) and np.any(between < 0):
                out["htmt_mixed"].append([names[a], names[b]])
            mono = within(spec["latent"][names[a]]) * within(spec["latent"][names[b]])
            out["htmt"][(names[a], names[b])] = abs(hetero) / math.sqrt(mono) if mono > 0 else None
    return out


# --- PLS-SEM ------------------------------------------------------------------------------------------------------

def _standardise(v):
    sd = v.std(axis=0)
    return (v - v.mean(axis=0)) / np.where(sd > 0, sd, 1.0)


def _pls_two_stage(x, cols, spec, light):
    """PLS with interaction terms by the two-stage approach (Hair et al., 2022; Becker et al., 2018): the model
    without the interactions gives the construct scores, the product of the standardised scores of x and w enters
    the second stage as a variable with one indicator."""
    products = spec["products"]
    base = {"latent": {k: v for k, v in spec["latent"].items() if k not in products},
            "observed": [o for o in spec["observed"] if o not in products],
            "paths": [p for p in spec["paths"] if p[0] not in products and p[1] not in products],
            "indirect": spec["indirect"], **({"cov": spec["cov"]} if spec.get("cov") else {})}
    formative = [f for f in spec.get("formative") or () if f not in products]
    if formative:  # mode B in both stages: without it the first stage weighted them as reflective (audit 2026-10-23, M1)
        base["formative"] = formative
    keep = columns(base)
    pos = {c: k for k, c in enumerate(cols)}
    xb = x[:, [pos[c] for c in keep]]
    first = fit_pls(xb, keep, base, light=True, scores=True)
    if not first["converged"]:
        return {"method": "pls", "converged": False, "problems": ["converge"]}
    raw = {name: first["scores"][part["x"]] * first["scores"][part["w"]] for name, part in products.items()}
    extra = [_standardise(raw[name]) for name in products]
    x2 = np.column_stack([xb] + extra)
    spec2 = {**base, "observed": base["observed"] + list(products), "paths": spec["paths"]}
    out = fit_pls(x2, keep + list(products), spec2, light=light)
    if "latent_sd" in out:  # the term is the standardised product: in units of x · w its SD is that of the product
        for name, part in products.items():
            out["latent_sd"][name] = float(raw[name].std()) * out["latent_sd"][part["x"]] * out["latent_sd"][part["w"]]
    return out


def fit_pls(x, cols, spec, light=False, scores=False):
    """PLS path modelling: mode A, path weighting scheme. Observed variables are constructs with one indicator.
    Interaction terms (spec["products"]) in two stages (_pls_two_stage); scores: the light result carries the
    construct scores."""
    spec = check(spec)
    x = np.asarray(x, dtype=float)
    if spec.get("products"):
        return _pls_two_stage(x, cols, spec, light)
    n, p = x.shape
    if n < 3 or p != len(cols):
        raise SemError("data")
    if np.any(x.std(axis=0) <= 0):
        raise SemError("constant")
    z = _standardise(x)
    pos = {c: k for k, c in enumerate(cols)}
    blocks = {lv: [pos[i] for i in items] for lv, items in spec["latent"].items()}
    blocks.update({o: [pos[o]] for o in spec["observed"]})
    names = list(blocks)
    preds = {v: [a for a, b in spec["paths"] if b == v] for v in names}
    succ = {v: [b for a, b in spec["paths"] if a == v] for v in names}
    # neighbours without a direction (factor scheme, Lohmöller, 1989): the partners of a relation without a direction,
    # and in the measurement model (no path, no such relation) every other construct, which is correlated with each
    # (audit 2026-10-12, SEM-7; before, such a construct took its own score: the first principal component)
    pairs = [tuple(c) for c in spec.get("cov", ())]
    if not spec["paths"] and not pairs:
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    beside = {v: [b if a == v else a for a, b in pairs if v in (a, b)] for v in names}
    weights = {v: np.ones(len(idx)) for v, idx in blocks.items()}
    formative = set(spec.get("formative") or ())

    def scores(w):
        out = {}
        for v, idx in blocks.items():
            s = z[:, idx] @ w[v]
            sd = s.std()
            out[v] = (s - s.mean()) / (sd if sd > 0 else 1.0)
        return out

    converged = False
    for it in range(PLS_ITER):
        y = scores(weights)
        new = {}
        for v, idx in blocks.items():
            inner = np.zeros(n)
            if preds[v]:
                xs = np.column_stack([y[a] for a in preds[v]])
                coef = np.linalg.lstsq(xs, y[v], rcond=None)[0]
                inner += xs @ coef
            for b in succ[v]:
                inner += np.mean(y[v] * y[b]) * y[b]
            if not preds[v] and not succ[v]:
                for b in beside[v]:
                    inner += np.mean(y[v] * y[b]) * y[b]
                if not beside[v]:  # one construct alone: its own score
                    inner = y[v]
            sd = inner.std()
            inner = (inner - inner.mean()) / (sd if sd > 0 else 1.0)
            if v in formative:  # mode B: the regression of the inner proxy on the indicators (Hair et al., 2022)
                w = np.linalg.lstsq(z[:, idx], inner, rcond=None)[0]
            else:
                w = z[:, idx].T @ inner / n  # mode A: covariances of indicators and inner proxy
            s = z[:, idx] @ w
            new[v] = w / (s.std() if s.std() > 0 else 1.0)
        change = max(float(np.max(np.abs(new[v] - weights[v]))) for v in names)
        weights = new
        if change < PLS_TOL:
            converged = True
            break
    y = scores(weights)
    est = {}
    r2 = {}
    for v in names:
        if preds[v]:
            xs = np.column_stack([y[a] for a in preds[v]])
            coef = np.linalg.lstsq(xs, y[v], rcond=None)[0]
            for a, c in zip(preds[v], coef):
                est[(a, v)] = float(c)
            fitted = xs @ coef
            r2[v] = float(1 - np.sum((y[v] - fitted) ** 2) / np.sum(y[v] ** 2))
    indirect = {(a, m, b): est[(a, m)] * est[(m, b)] for a, m, b in spec["indirect"]}
    # a pair without a direction: the correlation of the construct scores (PLS has no residual covariances)
    cov = {(a, b): float(np.mean(y[a] * y[b])) for a, b in spec.get("cov", ())}
    loadings = {}
    for lv, items in spec["latent"].items():
        loadings[lv] = [{"item": item, "std": float(np.mean(z[:, pos[item]] * y[lv])),
                         "weight": float(w)} for item, w in zip(items, weights[lv])]
    if light:
        return {"method": "pls", "converged": converged, "paths": est, "indirect": indirect, "cov": cov,
                "loadings": {lv: [r["std"] for r in rows] for lv, rows in loadings.items()},
                "weights": {lv: [float(w) for w in weights[lv]] for lv in formative},
                **({"scores": y} if scores else {})}
    out = {"method": "pls", "converged": converged, "n": n, "iterations": it + 1, "problems": [] if converged else ["converge"]}
    out["paths"] = [{"from": a, "to": b, "std": est[(a, b)], "se": None, "p": None, "ci": None} for a, b in spec["paths"]]
    out["indirect"] = [{"x": a, "m": m, "y": b, "std": indirect[(a, m, b)], "se": None, "p": None, "ci": None}
                       for a, m, b in spec["indirect"]]
    out["covariances"] = [{"a": a, "b": b, "std": v, "se": None, "p": None, "ci": None} for (a, b), v in cov.items()]
    out["r2"] = r2
    out["loadings"] = loadings
    out["quality"] = quality(spec, loadings, np.corrcoef(x, rowvar=False), list(cols))
    out.update(_pls_structure(y, preds, r2, n))
    out["formative"] = {lv: _block_vif(z[:, blocks[lv]]) for lv in formative}
    corr = np.corrcoef(np.column_stack([y[v] for v in names]), rowvar=False)
    out["latent_corr"] = {"names": names, "matrix": np.atleast_2d(corr).tolist()}
    # construct scores have SD 1; an observed variable (one indicator) its SD in the data (SEM-4)
    out["latent_sd"] = {v: (float(x[:, pos[v]].std()) if v in spec["observed"] else 1.0) for v in names}
    out["admissible"] = converged
    return out


def _r2_of(target, xs):
    coef = np.linalg.lstsq(xs, target, rcond=None)[0]
    fitted = xs @ coef
    return float(1 - np.sum((target - fitted) ** 2) / np.sum((target - target.mean()) ** 2))


def _pls_structure(y, preds, r2, n):
    """The figures of the structural model of PLS (Hair et al., 2022, ch. 6): R² adjusted for the number of
    predictors, f² of each path (the change of R² without it, Cohen, 1988) and the VIF of the predictors of each
    target (collinearity, from 5 critical). y: the standardised construct scores."""
    out = {"r2_adj": {}, "f2": {}, "vif": {}}
    for v, ps in preds.items():
        if not ps:
            continue
        k = len(ps)
        out["r2_adj"][v] = 1 - (1 - r2[v]) * (n - 1) / (n - k - 1) if n - k - 1 > 0 else None
        for a in ps:
            rest = [b for b in ps if b != a]
            without = _r2_of(y[v], np.column_stack([y[b] for b in rest])) if rest else 0.0
            out["f2"][(a, v)] = (r2[v] - without) / (1 - r2[v]) if r2[v] < 1 else None
            if rest:
                r2_a = _r2_of(y[a], np.column_stack([y[b] for b in rest]))
                out["vif"][(a, v)] = 1 / (1 - r2_a) if r2_a < 1 else None
            else:
                out["vif"][(a, v)] = 1.0
    return out


def _block_vif(zb):
    """The VIF of each indicator of a formative block: the diagonal of the inverse of their correlation matrix."""
    if zb.shape[1] < 2:
        return [1.0] * zb.shape[1]
    corr = np.corrcoef(zb, rowvar=False)
    try:
        return [float(v) for v in np.diag(np.linalg.inv(corr))]
    except np.linalg.LinAlgError:
        return [None] * zb.shape[1]


PREDICT_FOLDS = 10


def pls_predict(x, cols, spec, folds=PREDICT_FOLDS, seed=1):
    """PLSpredict (Shmueli et al., 2019): k-fold cross-validated predictions of the indicators of the endogenous
    constructs from the exogenous ones ("earliest antecedents": through the total effects of the paths), against
    the mean of the training cases (Q²_predict) and a linear model of the same indicators on all indicators of the
    exogenous constructs (LM benchmark). {"items": [{"item", "construct", "q2", "rmse", "rmse_lm"}], "constructs":
    {name: "high" | "medium" | "low" | "none"}} or None (interaction terms, no paths)."""
    spec = check(spec)
    if spec.get("products") or not spec["paths"]:
        return None
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2 * folds:
        return None
    pos = {c: k for k, c in enumerate(cols)}
    blocks = {lv: list(items) for lv, items in spec["latent"].items()}
    blocks.update({o: [o] for o in spec["observed"]})
    endo = endogenous(spec)
    exo = [v for v in blocks if v not in endo]
    order = []
    while len(order) < len(blocks):  # topological: what explains before what it explains
        for v in blocks:
            if v not in order and all(a in order for a, b in spec["paths"] if b == v):
                order.append(v)
    targets = [(v, i) for v in order if v in endo for i in blocks[v]]
    lm_inputs = [pos[i] for v in exo for i in blocks[v]]
    rng = np.random.default_rng(seed)
    fold = rng.permutation(np.arange(n) % folds)
    err = {t: [] for t in targets}
    err_lm = {t: [] for t in targets}
    err_mean = {t: [] for t in targets}
    for k in range(folds):
        train, test = x[fold != k], x[fold == k]
        try:
            fit = fit_pls(train, cols, spec)
        except (SemError, np.linalg.LinAlgError):
            return None
        if not fit.get("converged"):
            return None
        mean, sd = train.mean(axis=0), train.std(axis=0)
        sd = np.where(sd > 0, sd, 1.0)
        zt, ztr = (test - mean) / sd, (train - mean) / sd
        weights = {lv: np.array([r["weight"] for r in rows]) for lv, rows in fit["loadings"].items()}
        loads = {lv: {r["item"]: r["std"] for r in rows} for lv, rows in fit["loadings"].items()}
        paths = {(r["from"], r["to"]): r["std"] for r in fit["paths"]}
        hat = {}
        for v in order:
            idx = [pos[i] for i in blocks[v]]
            if v in exo:  # the holdout's scores, standardised like the training cases' scores
                w = weights.get(v, np.ones(len(idx)))
                train_score = ztr[:, idx] @ w
                hat[v] = (zt[:, idx] @ w - train_score.mean()) / (train_score.std() or 1.0)
            else:
                hat[v] = sum(paths[(a, b)] * hat[a] for a, b in spec["paths"] if b == v)
        xs_train = np.column_stack([np.ones(len(train)), train[:, lm_inputs]])
        xs_test = np.column_stack([np.ones(len(test)), test[:, lm_inputs]])
        for v, item in targets:
            j = pos[item]
            load = loads.get(v, {}).get(item, 1.0)
            pred = mean[j] + sd[j] * load * hat[v]
            err[(v, item)] += list(test[:, j] - pred)
            beta = np.linalg.lstsq(xs_train, train[:, j], rcond=None)[0]
            err_lm[(v, item)] += list(test[:, j] - xs_test @ beta)
            err_mean[(v, item)] += list(test[:, j] - mean[j])
    items, better = [], {}
    for v, item in targets:
        e, e_lm, e_0 = (np.array(err[(v, item)]), np.array(err_lm[(v, item)]), np.array(err_mean[(v, item)]))
        rmse, rmse_lm = float(np.sqrt(np.mean(e ** 2))), float(np.sqrt(np.mean(e_lm ** 2)))
        items.append({"item": item, "construct": v, "q2": float(1 - np.sum(e ** 2) / np.sum(e_0 ** 2)),
                      "rmse": rmse, "rmse_lm": rmse_lm})
        better.setdefault(v, []).append(rmse < rmse_lm)
    power = {}
    for v, wins in better.items():  # Shmueli et al. (2019): all, most, some or none of the indicators beat the LM
        k = sum(wins)
        power[v] = "high" if k == len(wins) else "medium" if k > len(wins) / 2 else "low" if k else "none"
    return {"items": items, "constructs": power, "folds": folds}


# --- bootstrap (estimation process) -------------------------------------------------------------------------------

def bootstrap(x, cols, spec, method, draws=2000, seed=1, progress=None, start=None, weights=None):
    """Percentile intervals of the standardised paths and indirect effects (Preacher & Hayes, 2008) and, for PLS,
    bootstrap standard errors with a normal p (Hair et al., 2022). Rows drawn with replacement, fixed seed; a draw
    that does not converge is left out and counted ("failed"; Askurious default: a draw without an estimate adds
    nothing to the percentiles); a draw with an inadmissible solution
    (Heywood case, constructs correlated beyond 1) stays in and is counted ("inadmissible", SEM-7). Returns {"draws",
    "failed", "inadmissible", "paths": {(a, b): {...}}, "indirect": {(x, m, y): {...}}, "cov": {(a, b): {...}}};
    progress(done, total) every 50 draws."""
    spec = check(spec)
    x = np.asarray(x, dtype=float)
    weights = None if weights is None else np.asarray(weights, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    path_vals = {pair: [] for pair in spec["paths"]}
    ind_vals = {t: [] for t in spec["indirect"]}
    cov_vals = {pair: [] for pair in spec.get("cov", ())}
    mm_vals = {t: [] for t in spec.get("modmed", ())}
    w_vals = {(lv, i): [] for lv in spec.get("formative", ()) for i in spec["latent"][lv]} if method == "pls" else {}
    reflective = [lv for lv in spec["latent"] if lv not in (spec.get("single") or {}) and lv not in
                  spec.get("formative", ()) and lv not in (spec.get("products") or {})]
    pos = {c: k for k, c in enumerate(cols)}
    items_r = [i for lv in reflective for i in spec["latent"][lv]]
    htmt_vals = {(a, b): [] for i, a in enumerate(reflective) for b in reflective[i + 1:]}
    failed = inadmissible = 0
    for draw in range(draws):
        rows = rng.integers(0, n, n)
        sample = x[rows]
        try:
            if method == "pls":
                r = fit_pls(sample, cols, spec, light=True)
                if not r["converged"]:
                    raise SemError("converge")
                paths, ind, cov = r["paths"], r["indirect"], r["cov"]
            else:
                if spec.get("missing") == "fiml":  # the draws keep their gaps (point 2)
                    from app import sem_means

                    r = sem_means.fit_fiml(sample, cols, spec, light=True, start=start)
                elif spec.get("ordinal"):  # polychorics of each draw (point 3)
                    from app import sem_ordinal

                    r = sem_ordinal.fit_wlsmv(sample, cols, spec, light=True, start=start)
                else:
                    r = fit_ml(sample, cols, spec, robust=False, start=start, light=True,
                               weights=None if weights is None else weights[rows])
                if not r["converged"]:
                    raise SemError("converge")
                paths = {(a, b): r["std"][("path", a, b)] for a, b in spec["paths"]}
                ind = {t: r["std"][("indirect",) + t] for t in spec["indirect"]}
                cov = {pair: cov_value(r["std"], *pair) for pair in cov_vals}
                if not r["admissible"]:
                    inadmissible += 1
        except (SemError, np.linalg.LinAlgError, FloatingPointError):
            failed += 1
        else:
            for pair in spec["paths"]:
                path_vals[pair].append(paths[pair])
            for t in spec["indirect"]:
                ind_vals[t].append(ind[t])
            for pair in cov_vals:
                cov_vals[pair].append(cov[pair])
            for t in mm_vals:  # index of moderated mediation (Hayes, 2015): a3 b or a b3
                stage, tx, tm, ty, txw = t
                mm_vals[t].append(paths[(txw, tm)] * paths[(tm, ty)] if stage == "a" else paths[(tx, tm)] * paths[(txw, ty)])
            for lv, ws in (r.get("weights") or {}).items():  # formative weights (point 9)
                for item, w in zip(spec["latent"][lv], ws):
                    w_vals[(lv, item)].append(w)
            if htmt_vals:  # discriminant validity with an interval (Franke & Sarstedt, 2019; point 13)
                corr = np.corrcoef(sample[:, [pos[i] for i in items_r]], rowvar=False)
                found = quality({"latent": {lv: spec["latent"][lv] for lv in reflective}},
                                {lv: [{"std": 0.7}] * len(spec["latent"][lv]) for lv in reflective}, corr, items_r)["htmt"]
                for pair in htmt_vals:
                    if found.get(pair) is not None:
                        htmt_vals[pair].append(found[pair])
        if progress and (draw + 1) % 50 == 0:
            progress(draw + 1, draws)

    def summary(values):
        if len(values) < 2:
            return None
        v = np.array(values)
        se = float(v.std(ddof=1))
        lo, hi = np.percentile(v, [2.5, 97.5])
        return {"ci": (float(lo), float(hi)), "se": se, "upper95": float(np.percentile(v, 95))}

    return {"draws": draws, "failed": failed, "inadmissible": inadmissible,
            "paths": {k: summary(v) for k, v in path_vals.items()},
            "indirect": {k: summary(v) for k, v in ind_vals.items()},
            "cov": {k: summary(v) for k, v in cov_vals.items()},
            "modmed": {k: summary(v) for k, v in mm_vals.items()},
            "weights": {k: summary(v) for k, v in w_vals.items()},
            "htmt": {k: summary(v) for k, v in htmt_vals.items()}}


# --- power by RMSEA (MacCallum, Browne & Sugawara, 1996) ----------------------------------------------------------

MACCALLUM = ("MacCallum, R. C., Browne, M. W., & Sugawara, H. M. (1996). Power analysis and determination of sample "
             "size for covariance structure modeling. Psychological Methods, 1(2), 130–149. "
             "https://doi.org/10.1037/1082-989X.1.2.130")


def rmsea_power(n, df, eps0=0.05, eps1=0.08, alpha=0.05):
    """Power of the test of not-close fit against eps0 when the population RMSEA is eps1 (eps1 > eps0: rejecting
    close fit of a model that fits badly)."""
    if df <= 0 or n <= 1:
        return 0.0
    lam0, lam1 = (n - 1) * df * eps0 ** 2, (n - 1) * df * eps1 ** 2
    crit = _ncx2_quantile(1 - alpha, df, lam0)
    return ncx2_sf(crit, df, lam1)


def _ncx2_quantile(prob, df, lam):
    lo, hi = 0.0, df + lam + 20 * math.sqrt(2 * (df + 2 * lam)) + 100
    for _ in range(100):
        mid = (lo + hi) / 2
        if 1 - ncx2_sf(mid, df, lam) < prob:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@lru_cache(maxsize=256)
def rmsea_n(df, power=0.8, eps0=0.05, eps1=0.08, alpha=0.05, top=100_000):
    """The smallest N with rmsea_power >= power, None above top. Cached: the study plan asks for it on every view
    (code review 2026-10-15, A7)."""
    if df <= 0:
        return None
    lo, hi = 2, 2
    while rmsea_power(hi, df, eps0, eps1, alpha) < power:
        hi *= 2
        if hi > top:
            return None
    while lo < hi:
        mid = (lo + hi) // 2
        if rmsea_power(mid, df, eps0, eps1, alpha) >= power:
            hi = mid
        else:
            lo = mid + 1
    return lo


SATORRA_SARIS = ("Satorra, A., & Saris, W. E. (1985). Power of the likelihood ratio test in covariance structure "
                 "analysis. Psychometrika, 50(1), 83–90. https://doi.org/10.1007/BF02294150")
MACKINNON = ("MacKinnon, D. P., Lockwood, C. M., Hoffman, J. M., West, S. G., & Sheets, V. (2002). A comparison of "
             "methods to test mediation and other intervening variable effects. Psychological Methods, 7(1), 83–104. "
             "https://doi.org/10.1037/1082-989X.7.1.83")
PLAN_LOADING = 0.7  # standardised loading assumed for planning (Festlegung von Askurious; Wolf et al., 2013: .5 to .8)


def population(spec, betas, loading=PLAN_LOADING):
    """The covariance matrix (columns(spec)) of a planned model in standardised form: every structural variable has
    variance 1, exogenous ones are uncorrelated, a path has its standardised beta (missing: 0), each indicator loads
    `loading`. None when the betas leave an endogenous variable no room (R² of 1 or more)."""
    spec = check(spec)
    lowers = lower(spec)
    structural = [v for v in spec["latent"] if v not in lowers] + list(spec.get("higher") or {}) + spec["observed"]
    order, done = [], set()
    while len(order) < len(structural):  # topological: a variable after everything that points at it
        for v in structural:
            if v not in done and all(a in done for a, b in spec["paths"] if b == v):
                order.append(v)
                done.add(v)
    k = {v: i for i, v in enumerate(order)}
    cov = np.zeros((len(order), len(order)))
    for v in order:
        preds = [a for a, b in spec["paths"] if b == v]
        i = k[v]
        if not preds:
            cov[i, i] = 1.0
            continue
        b = np.array([betas.get((a, v), 0.0) for a in preds])
        idx = [k[a] for a in preds]
        explained = float(b @ cov[np.ix_(idx, idx)] @ b)
        if explained >= 1 - 1e-6:
            return None
        # covariances with every earlier variable: through the predecessors
        for w in order[:order.index(v)]:
            j = k[w]
            cov[i, j] = cov[j, i] = float(b @ cov[idx, j])
        cov[i, i] = 1.0
    # the constructs of the first order: `loading` times their factor of the second order, variance 1
    for h, parts in (spec.get("higher") or {}).items():
        for part in parts:
            i = len(order)
            k[part] = i
            order.append(part)
            grown = np.zeros((i + 1, i + 1))
            grown[:i, :i] = cov
            grown[i, :i] = grown[:i, i] = loading * cov[k[h], :i]
            grown[i, i] = 1.0
            cov = grown
    cols = columns(spec)
    load = np.zeros((len(cols), len(order)))
    products = spec.get("products") or {}
    for lv, items in spec["latent"].items():
        # a product indicator of two constructs loads loading² (the reliability of a product, Festlegung)
        each = loading ** 2 if (products.get(lv) or {}).get("kind") == "ll" else loading
        if lv in (spec.get("single") or {}):  # a single question with a fixed reliability
            each = math.sqrt(spec["single"][lv])
        for item in items:
            load[cols.index(item), k[lv]] = each
    for o in spec["observed"]:
        load[cols.index(o), k[o]] = 1.0
    sigma = load @ cov @ load.T
    sigma[np.diag_indices_from(sigma)] = 1.0
    return sigma


def path_noncentrality(spec, betas, path, loading=PLAN_LOADING):
    """F_ML of the model with `path` fixed at 0, fitted to the planned population (Satorra & Saris, 1985): the
    noncentrality of the test of that path is (N - 1) F. None when the planned model is not possible."""
    spec = check(spec)
    sigma = population(spec, betas, loading)
    if sigma is None:
        return None
    restricted = check({**spec, "zero": [tuple(path)]})
    cols = columns(spec)
    ram = _Ram(restricted, cols)
    logdet = np.linalg.slogdet(sigma)[1]
    _, f, converged = _optimise(ram, sigma, logdet)
    return max(f, 0.0) if converged else None


def _one_df_power(lam, alpha=0.05):
    from app.power import _chi2_quantile

    return ncx2_sf(_chi2_quantile(alpha, 1), 1, lam)


def path_n(spec, betas, paths, power=0.8, alpha=0.05, loading=PLAN_LOADING):
    """The N for which the test of every path in `paths` reaches `power` (one path: its Wald/likelihood ratio test;
    two paths a -> m -> b: joint significance of both, power(a) x power(b), MacKinnon et al., 2002). None when a path
    has no effect or the planned model is not possible."""
    fs = [path_noncentrality(spec, betas, path, loading) for path in paths]
    if any(f is None or f <= 1e-12 for f in fs):
        return None

    def reached(n):
        out = 1.0
        for f in fs:
            out *= _one_df_power((n - 1) * f, alpha)
        return out

    lo, hi = 3, 3
    while reached(hi) < power:
        lo, hi = hi, hi * 2
        if hi > 10_000_000:
            return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if reached(mid) >= power:
            hi = mid
        else:
            lo = mid
    return hi




def groups_noncentrality(spec, betas, path, values, loading=PLAN_LOADING):
    """The planned test of a moderation by groups (audit 2026-10-12, decision 9): the model in len(values) groups of
    equal size with the loadings equal across groups, the path `path` at values[g] in group g, every indicator loading
    `loading` as for the other paths. Returns c with the noncentrality of the joint Wald test "the same path in all
    groups" (G - 1 df) = N c for N persons in all; None when a group's planned model is not possible."""
    spec = check(spec)
    cols = columns(spec)
    ram = _Ram(spec, cols)
    groups = len(values)
    shared = [k for k, (_, _, _, label) in enumerate(ram.free) if label[0] == "loading"]
    own = [k for k in range(ram.q) if k not in shared]
    size = len(shared) + groups * len(own)
    info = np.zeros((size, size))
    theta = np.zeros(size)
    at = next((k for k, (_, _, _, label) in enumerate(ram.free) if label == ("path", *path)), None)
    if at is None:
        return None
    where = []  # the position of the path of each group in the joint vector
    for g, value in enumerate(values):
        sigma = population(spec, {**betas, tuple(path): value}, loading)
        if sigma is None:
            return None
        found, _, converged = _optimise(ram, sigma, np.linalg.slogdet(sigma)[1])
        if not converged:
            return None
        idx = np.empty(ram.q, dtype=int)
        for i, k in enumerate(shared):
            idx[k] = i
        for i, k in enumerate(own):
            idx[k] = len(shared) + g * len(own) + i
        theta[idx] = found
        implied, d = ram.derivatives(found)
        inv = np.linalg.inv(implied)
        m = inv[None] @ d
        info[np.ix_(idx, idx)] += _trace_products(m) / 2 / groups  # per person, equal groups
        where.append(idx[at])
    contrast = np.zeros((groups - 1, size))  # each group's path minus the first group's
    for r in range(1, groups):
        contrast[r - 1, where[r]], contrast[r - 1, where[0]] = 1.0, -1.0
    try:
        vcov = np.linalg.inv(info)
        d = contrast @ theta
        return max(float(d @ np.linalg.solve(contrast @ vcov @ contrast.T, d)), 0.0)
    except np.linalg.LinAlgError:
        return None


def groups_n(spec, betas, path, values, power=0.8, alpha=0.05, loading=PLAN_LOADING):
    """The N (all groups together, equal sizes) at which the joint Wald test of groups_noncentrality reaches `power`
    (χ² with G - 1 df; Satorra & Saris, 1985). None when the plan is not possible or the groups do not differ."""
    from app.power import _chi2_quantile

    c = groups_noncentrality(spec, betas, path, values, loading)
    if c is None or c <= 1e-12:
        return None
    df = len(values) - 1
    crit = _chi2_quantile(alpha, df)

    def reached(n):
        return ncx2_sf(crit, df, n * c) >= power

    lo, hi = 1, 1
    while not reached(hi * len(values)):
        lo, hi = hi, hi * 2
        if hi > 10_000_000:
            return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if reached(mid * len(values)):
            hi = mid
        else:
            lo = mid
    return hi * len(values)


# --- several groups (a moderator with two groups, modell-zeichnen-konzept.md F2) -------------------------------------

def fit_ml_groups(xs, cols, spec, robust=True):
    """The covariance-based model in several groups at once (Jöreskog, 1971): the same model in each group, the
    loadings equal across groups (metric invariance, Meredith, 1993), everything else free per group. xs: the
    complete cases of each group (columns cols as columns(spec)). Columns in units of their SD over all groups.
    Returns {"converged", "n": [per group], "df", "chi2", "p", "groups": [{"paths": [...], "std": {...}}],
    "diff": {(a, b): {"est", "se", "z", "p"}} (the difference of each path, group 2 minus group 1, in the units of
    the data), "problems", "admissible"}."""
    spec = check(spec)
    xs = [np.asarray(x, dtype=float) for x in xs]
    if len(xs) < 2 or any(len(x) < 3 or x.shape[1] != len(cols) for x in xs):
        raise SemError("data")
    ram = _Ram(spec, cols)
    pooled = np.vstack([x - x.mean(axis=0) for x in xs])
    unit = pooled.std(axis=0)
    if np.any(unit <= 0):
        raise SemError("constant")
    xs = [x / unit for x in xs]
    ss = [np.cov(x, rowvar=False, bias=True) for x in xs]
    ns = np.array([len(x) for x in xs], dtype=float)
    weights = ns / ns.sum()
    shared = [k for k, (_, _, _, label) in enumerate(ram.free) if label[0] == "loading"]
    own = [k for k in range(ram.q) if k not in shared]
    G = len(xs)
    Q = len(shared) + G * len(own)

    def select(g):  # theta of group g from the joint vector
        idx = np.empty(ram.q, dtype=int)
        for i, k in enumerate(shared):
            idx[k] = i
        for i, k in enumerate(own):
            idx[k] = len(shared) + g * len(own) + i
        return idx

    sel = [select(g) for g in range(G)]
    logdets = []
    for s in ss:
        sign, logdet = np.linalg.slogdet(s)
        if sign <= 0:
            raise SemError("singular")
        logdets.append(logdet)
    p = ram.p
    theta = np.zeros(Q)
    starts = [ram.start(s) for s in ss]
    for g in range(G):
        theta[sel[g]] = starts[g] if g else starts[0]
    for g in range(1, G):  # own parameters from each group's start
        theta[sel[g][own]] = starts[g][own]

    def total(t):
        f = 0.0
        for g in range(G):
            sigma = ram.implied(t[sel[g]])[0]
            fg, inv = _discrepancy(sigma, ss[g], logdets[g], p)
            if inv is None:
                return math.inf
            f += weights[g] * fg
        return f

    f = total(theta)
    converged = False
    for _ in range(MAX_ITER):
        grad, info = np.zeros(Q), np.zeros((Q, Q))
        for g in range(G):
            sigma, d = ram.derivatives(theta[sel[g]])
            inv = np.linalg.inv(sigma)
            m = inv[None] @ d
            ig = _trace_products(m)
            gg = np.einsum("ij,kji->k", inv @ (sigma - ss[g]), m)
            idx = sel[g]
            np.add.at(grad, idx, weights[g] * gg)
            info[np.ix_(idx, idx)] += weights[g] * ig  # idx has no repeats within a group
        try:
            step = -np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            step = -np.linalg.lstsq(info, grad, rcond=None)[0]
        if np.all(np.abs(step) < TOL * (1 + np.abs(theta))):
            converged = True
            break
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            f_new = total(new)
            if f_new <= f + 1e-12:
                break
            t /= 2
        else:
            break
        theta, f = new, f_new
        if np.all(np.abs(t * step) < TOL * (1 + np.abs(theta))):
            converged = True
            break
    if not converged:
        return {"converged": False, "problems": ["converge"]}
    # errors: information and (robust) the fourth moments per group, joined over the shared loadings
    info, meat = np.zeros((Q, Q)), np.zeros((Q, Q))
    rows, cols_ = _vech_index(p)
    off = (rows != cols_).astype(float)
    for g in range(G):
        sigma, d = ram.derivatives(theta[sel[g]])
        inv = np.linalg.inv(sigma)
        m = inv[None] @ d
        ig = _trace_products(m) / 2  # Delta' W Delta
        idx = sel[g]
        info[np.ix_(idx, idx)] += ns[g] * ig
        if robust:
            wd = 0.5 * np.stack([(inv @ dk @ inv)[rows, cols_] * (1 + off) for dk in d], axis=1)
            meat[np.ix_(idx, idx)] += ns[g] * _meat(xs[g], wd)
    problems = []
    if not _identified(info):
        problems.append("identified")
        bread = np.linalg.pinv(info)
    else:
        bread = np.linalg.inv(info)
    vcov = bread @ meat @ bread if robust else bread
    df = G * len(rows) - Q
    chi2 = float(ns.sum() * f)
    factor = ram.factors(unit)
    out = {"method": "ml", "converged": True, "n": [int(n) for n in ns], "df": df, "chi2": chi2,
           "p": _chi2_p(chi2, df) if df > 0 else None, "groups": [], "diff": {}}
    for g in range(G):
        tg = theta[sel[g]]
        std = _standardised(ram, tg)
        problems += _inadmissible(ram, tg, std)
        out["groups"].append({"std": std, "paths": {(a, b): std[("path", a, b)] for a, b in spec["paths"]}})
    out["joint"] = {}
    for k, (_, _, _, label) in enumerate(ram.free):
        if label[0] != "path":
            continue
        i1, i2 = sel[0][k], sel[1][k]
        est = (theta[i2] - theta[i1]) * factor[k]
        var = vcov[i1, i1] + vcov[i2, i2] - 2 * vcov[i1, i2]
        se = math.sqrt(max(var, 0.0)) * factor[k]
        z = est / se if se > 0 else None
        out["diff"][(label[1], label[2])] = {"est": est, "se": se, "z": z, "p": _normal_p(z) if z is not None else None,
                                             "b": [theta[sel[g][k]] * factor[k] for g in range(G)]}
        # the same path in every group (G - 1 differences to the first, Wald χ² with G - 1 df)
        contrast = np.zeros((G - 1, Q))
        for g in range(1, G):
            contrast[g - 1, sel[g][k]], contrast[g - 1, sel[0][k]] = 1.0, -1.0
        d = contrast @ theta
        try:
            chi2 = float(d @ np.linalg.solve(contrast @ vcov @ contrast.T, d))
        except np.linalg.LinAlgError:
            chi2 = None
        out["joint"][(label[1], label[2])] = {"chi2": chi2, "df": G - 1,
                                              "p": _chi2_p(chi2, G - 1) if chi2 is not None else None}
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    return out


def model_df(spec):
    """The degrees of freedom of the covariance-based model (for the planning, before there are data)."""
    spec = check(spec)
    cols = columns(spec)
    return len(cols) * (len(cols) + 1) // 2 - _Ram(spec, cols).q
