"""Power of the part-worths of a conjoint (CBC) and of the scores of a MaxDiff from the survey's own design (PA7,
for everyone since 2026-10-10, before the experimental feature "choice_power"; concept poweranalyse-konzept.md). After de Bekker-Grob, Donkers, Jonker & Stolk
(2015): the multinomial logit's information of one person over the tasks of the design gives the variance of every
parameter; a parameter of true size δ differs from 0 with power 1 − β from

    N = (z(1 − α/2) + z(1 − β))² · Var_k / δ²

persons on. The page takes δ, α and the power and shows the N of the parameter that needs the most.

  CBC       effects coding as the analysis (app/conjoint_design._encode), a price marked as numbers linear as there
            (W-3), one choice per task among the alternatives; a "keine davon" option and the dual response are left out
  MaxDiff   effects coding of the statements, the best choice in each set and then the worst among the rest
            (sequential best-worst logit; Louviere, Flynn & Marley, 2015)

Festlegung von Askurious: the choice probabilities are taken at part-worths of 0, as the D-efficiency of the design
(app/conjoint_design); with large true part-worths the information shrinks and the number comes out somewhat too
small. Persons spread evenly over the versions."""
import numpy as np

DE_BEKKER_GROB = ("de Bekker-Grob, E. W., Donkers, B., Jonker, M. F., & Stolk, E. A. (2015). Sample size requirements for "
                  "discrete-choice experiments in healthcare: A practical guide. The Patient – Patient-Centered Outcomes "
                  "Research, 8(5), 373–384. https://doi.org/10.1007/s40271-015-0118-z")
LOUVIERE = ("Louviere, J. J., Flynn, T. N., & Marley, A. A. J. (2015). Best-worst scaling: Theory, methods and "
            "applications. Cambridge University Press. https://doi.org/10.1017/CBO9781107337855")


def _set_info(rows):
    """Information of one logit choice at parameters 0 among the coded rows: their covariance around the mean."""
    d = rows - rows.mean(axis=0, keepdims=True)
    return d.T @ d / len(rows)


def _covariance(info):
    """The covariance of the parameters from the information of one person, None when it is singular."""
    try:
        cov = np.linalg.inv(info)
    except np.linalg.LinAlgError:
        return None
    var = np.diag(cov)
    if not np.all(np.isfinite(var)) or np.any(var <= 0) or np.linalg.cond(info) > 1e12:
        return None
    return cov


def conjoint(item, lang=None):
    """{"labels": [attribute: level], "var": [variance per person]} of the part-worths of a conjoint with a current
    design, every level including the last one of each attribute; None without one or when the design cannot
    separate the part-worths. An attribute marked "price (numbers)" enters as the analysis takes it
    (app/cbc_analysis.choice_sets): one linear coefficient on (price - mean price) / scale, not a part-worth per
    level (audit 2026-10-10, W-3). Its row is that coefficient, put on the scale of the others: δ is then the
    utility of the price level farthest from the mean price against the mean, "price" names that level."""
    from app.choice_model import Coding, price_scale

    kind = item.kind
    design = (item.config or {}).get("design")
    if not design or not kind.design_current(item):
        return None
    levels = kind.level_counts(item)
    pa = kind.price_attribute(item)
    numbers = kind.level_numbers(item, pa) if pa is not None else None
    scale = price_scale(numbers) if numbers and len(numbers) == levels[pa] else None
    coding = Coding(levels, scale, asc=False, linear=(pa,) if scale else (),
                    price_center=sum(numbers) / len(numbers) if scale else 0.0)
    alts = item.config["alternatives"]
    total = np.zeros((coding.k, coding.k))
    versions = design["versions"]
    for version in versions:
        for task in version:
            X = np.array([coding.row(p, numbers[p[pa]] if scale else None) for p in np.asarray(task)[:alts]])
            total += _set_info(X)
    cov = _covariance(total / len(versions))
    if cov is None:
        return None
    labels, var, price = [], [], None
    for a, (attr, n) in enumerate(zip(kind.attributes(item, lang), levels)):
        names = [name or str(i + 1) for i, name in enumerate(attr.get("level_labels") or [])][:n]
        names += [str(i + 1) for i in range(len(names), n)]
        if scale and a == pa:
            far = max(range(n), key=lambda i: (abs(numbers[i] - coding.price_center), numbers[i]))  # ties: the higher
            x = (numbers[far] - coding.price_center) / scale
            price = names[far]
            labels.append(f"{attr['label']}: {names[far]} (linear)")
            var.append(float(x * x * cov[coding.price, coding.price]))
            continue
        start = coding.offsets[a]
        labels += [f"{attr['label']}: {name}" for name in names]
        block = cov[start:start + n - 1, start:start + n - 1]
        # the last level is minus the sum of the others, its variance 1'C1 over the block of the attribute (PA-2):
        # with exclusions the design is unbalanced and it can need the most
        var += [float(v) for v in np.diag(block)] + [float(block.sum())]
    return {"labels": labels, "var": var, "price": price}


def maxdiff(item, lang=None):
    """{"labels", "var"} of the scores of all statements of a MaxDiff with a current design (the last statement is
    the reference of effects coding), None without one."""
    kind = item.kind
    design = (item.config or {}).get("design")
    # a design made before statements, the set size or the versions changed points past the statements (PA-3)
    if not design or not design.get("versions") or not kind.design_current(item):
        return None
    K = kind.n_items(item)
    code = np.vstack([np.eye(K - 1), -np.ones(K - 1)])
    total = np.zeros((K - 1, K - 1))
    versions = design["versions"]
    for version in versions:
        for s in version:
            rows = code[list(s)]
            total += _set_info(rows)                                  # the best one
            for b in range(len(s)):                                    # then the worst among the rest, each best equally likely
                rest = np.delete(rows, b, axis=0)
                total += _set_info(rest) / len(s)
    cov = _covariance(total / len(versions))
    if cov is None:
        return None
    labels = list(kind.labels(item, lang))[:K]
    # the reference statement is minus the sum of the others, its variance 1'C1 (PA-2)
    return {"labels": [str(x) for x in labels], "var": [float(v) for v in np.diag(cov)] + [float(cov.sum())]}


def needed(var, delta, power=0.8, alpha=0.05):
    """Persons for the parameter of variance `var` (per person) and true size delta."""
    from statistics import NormalDist

    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    return int(np.ceil(z * z * var / (delta * delta) - 1e-9))


def paired(item, lang=None):
    """{"labels", "var"} of the zero-centred utilities of a paired comparison with a current design (effects coding, the
    last option minus the sum of the others), None without one. Every answered pair is one choice among its categories
    (app/paired_analysis.py) with the position effect as a further parameter; at parameters 0 the categories are
    equally likely, as for the other designs here (the thresholds of "gleich" and of the weaker steps are taken as 0)."""
    from app.paired_analysis import categories

    kind = item.kind
    design = (item.config or {}).get("design")
    if not design or not design.get("versions") or not kind.design_current(item):
        return None
    K = kind.n_items(item)
    code = np.vstack([np.eye(K - 1), -np.ones(K - 1)])
    cats = categories(item.config.get("answer", "choice"), item.config.get("steps", 5))
    total = np.zeros((K, K))  # K - 1 utilities and the position effect
    versions = design["versions"]
    for version in versions:
        for a, b in version:
            rows = np.array([np.append(c / 2 * (code[a] - code[b]), c / 2) for c in cats])
            total += _set_info(rows)
    cov = _covariance(total / len(versions))
    if cov is None:
        return None
    cov = cov[:K - 1, :K - 1]
    labels = list(kind.labels(item, lang))[:K]
    return {"labels": [str(x) for x in labels], "var": [float(v) for v in np.diag(cov)] + [float(cov.sum())]}
