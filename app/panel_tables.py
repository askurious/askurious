"""The parameter tables of the models over the waves of a panel (longitudinal and multilevel concept 2026-10-17):
the latent growth curve, the invariance over time and the cross-lagged panel models, for app/panel_models.py (the
results page) and app/panel_power.py (the power by simulation, which runs on the Rechenknecht too: numbers only, no
Flask). Estimated with app/sem_table.py; each table checked against reference values
(tests/analysis/test_panel_models.py)."""
from app import sem_table

r_ = sem_table.row
LEVELS = ("configural", "metric", "scalar")


def growth_table(cols, times, quadratic=False, predictors=()):
    """A latent growth curve of the columns (one per wave) at the times: intercept I (loadings 1), slope S (the
    times), with Q (the squared times) when quadratic; intercepts of the columns 0, means of the growth factors free;
    predictors regress I, S (and Q) and covary freely."""
    tab = [r_("=~", "I", c, 1) for c in cols] + [r_("=~", "S", c, t) for c, t in zip(cols, times)]
    factors = ["I", "S"]
    if quadratic:
        tab += [r_("=~", "Q", c, t * t) for c, t in zip(cols, times)]
        factors.append("Q")
    for c in cols:
        tab += [r_("~1", c, None, 0), r_("~~", c, c)]
    for f in factors:
        tab += [r_("~1", f), r_("~~", f, f)]
    for a in range(len(factors)):
        for b in range(a + 1, len(factors)):
            tab.append(r_("~~", factors[a], factors[b]))
    for x in predictors:
        tab += [r_("~", f, x) for f in factors]
    for k, x in enumerate(predictors):
        tab += [r_("~1", x), r_("~~", x, x)] + [r_("~~", x, y) for y in predictors[k + 1:]]
    return tab


def invariance_table(items, waves_n, level):
    """The same factor in every wave (columns <item>_t<n>, the first statement the marker), residuals of the same
    statement correlated over the waves; metric: equal loadings, scalar: equal intercepts too and the means of the
    factor free from the second wave on (0 in the first)."""
    rank = LEVELS.index(level)
    col = [[f"{it}_t{t}" for it in items] for t in range(1, waves_n + 1)]
    tab = []
    for t in range(waves_n):
        for k, c in enumerate(col[t]):
            tab.append(r_("=~", f"F{t + 1}", c, 1 if k == 0 else None, f"l{k}" if k and rank >= 1 else None))
    for t in range(waves_n):
        for k, c in enumerate(col[t]):
            tab += [r_("~~", c, c), r_("~1", c, None, None, f"i{k}" if rank >= 2 else None)]
    for k in range(len(items)):
        for a in range(waves_n):
            for b in range(a + 1, waves_n):
                tab.append(r_("~~", col[a][k], col[b][k]))
    for t in range(waves_n):
        tab += [r_("~~", f"F{t + 1}", f"F{t + 1}"), r_("~1", f"F{t + 1}", None, 0 if (t == 0 or rank < 2) else None)]
    for a in range(waves_n):
        for b in range(a + 1, waves_n):
            tab.append(r_("~~", f"F{a + 1}", f"F{b + 1}"))
    return [c for cs in col for c in cs], tab


def _node(v, t):
    """The variable of v in wave t the panel structure works on: the score column, or the factor of its statements."""
    return f"F_{v['id']}_t{t}" if v.get("items") else f"{v['id']}_t{t}"


def _measurement(v, T):
    """The statements of a latent variable over the waves: equal loadings (metric), intercepts free in every wave and
    the means of the factors 0, residuals of the same statement correlated over the waves."""
    k = v["items"]
    col = [[f"{v['id']}_{j + 1}_t{t}" for j in range(k)] for t in range(1, T + 1)]
    tab = []
    for t in range(T):
        for j, c in enumerate(col[t]):
            tab.append(r_("=~", _node(v, t + 1), c, 1 if j == 0 else None, f"l_{v['id']}_{j}" if j else None))
        for c in col[t]:
            tab += [r_("~~", c, c), r_("~1", c)]
        tab.append(r_("~1", _node(v, t + 1), None, 0))
    for j in range(k):
        for a in range(T):
            for b in range(a + 1, T):
                tab.append(r_("~~", col[a][j], col[b][j]))
    return [c for cs in col for c in cs], tab, [[col[t][j] for t in range(T)] for j in range(k)]


def cross_table(variables, T, kind="riclpm", equal=False):
    """The cross-lagged panel model of the variables ([{"id", "items": number of statements or 0 for a score}]) over
    T waves: (columns, table, same). "clpm": each variable in wave t regressed on all of them in wave t - 1, the first
    wave free, residuals of the same wave correlated. "riclpm" (Hamaker et al., 2015): a random intercept per variable
    (loadings 1 in every wave) takes the stable differences between persons, the paths run between the deviations
    within persons (w); random intercepts uncorrelated with the first wave's deviations. equal: the same paths over
    all intervals (labels). A latent variable has its statements with equal loadings under it (Mulder & Hamaker,
    2021, extension 3)."""
    cols, tab, same = [], [], []
    for v in variables:
        if v.get("items"):
            c, t_, s_ = _measurement(v, T)
            cols += c
            tab += t_
            same += s_
        else:
            cs = [f"{v['id']}_t{t}" for t in range(1, T + 1)]
            cols += cs
            same.append(cs)
    ids = [v["id"] for v in variables]
    by = {v["id"]: v for v in variables}

    def lab(a, b):
        return f"b_{a}_{b}" if equal else None

    pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
    if kind == "riclpm":
        for v in variables:
            tab += [r_("=~", f"RI_{v['id']}", _node(v, t), 1) for t in range(1, T + 1)]
            tab += [r_("=~", f"w_{v['id']}_t{t}", _node(v, t), 1) for t in range(1, T + 1)]
            for t in range(1, T + 1):
                tab.append(r_("~~", _node(v, t), _node(v, t), 0))
                if not v.get("items"):
                    tab.append(r_("~1", _node(v, t)))
        for t in range(2, T + 1):
            for b in ids:
                tab += [r_("~", f"w_{b}_t{t}", f"w_{a}_t{t - 1}", None, lab(a, b)) for a in ids]
        for t in range(1, T + 1):
            tab += [r_("~~", f"w_{a}_t{t}", f"w_{a}_t{t}") for a in ids]
            tab += [r_("~~", f"w_{a}_t{t}", f"w_{b}_t{t}") for a, b in pairs]
        tab += [r_("~~", f"RI_{a}", f"RI_{a}") for a in ids]
        tab += [r_("~~", f"RI_{a}", f"RI_{b}") for a, b in pairs]
    else:
        for v in variables:
            if not v.get("items"):
                tab += [r_("~1", _node(v, t)) for t in range(1, T + 1)]
        for t in range(2, T + 1):
            for b in ids:
                tab += [r_("~", _node(by[b], t), _node(by[a], t - 1), None, lab(a, b)) for a in ids]
        for t in range(1, T + 1):
            tab += [r_("~~", _node(by[a], t), _node(by[a], t)) for a in ids]
            tab += [r_("~~", _node(by[a], t), _node(by[b], t)) for a, b in pairs]
    return cols, tab, same
