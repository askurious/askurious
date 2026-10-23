"""The pooled construct data in the administration (licence.md section 4): for every toolbox construct the studies
that used it, each with its N and reliability, and a pooled value over the studies (reliability generalization).

What counts, per study (a closed survey; running ones are only counted, drafts have no data):
  participations   the completed real participations (no test runs, no dummy data, no unfinished ones) without what
                   quality assurance marks (qc_marked, as on its results page); the reliability rests on the survey's
                   own data pool from the page "Qualitätssicherung", the same as its results page
  reliability      exactly the figures of the results page (app/results.py): Cronbach's alpha with the Feldt
                   interval, McDonald's omega with its bootstrap interval, per subscale for instruments with
                   subscales; taken from the kept results when the survey's pool is the real one (app/results_store.py)
  pooled           random-effects meta-analysis (DerSimonian & Laird, 1986) of ln(1 - alpha) with the variance of
                   Bonett (2002), 2k / ((k - 1)(n - 2)), turned back into alpha; the interval after Hartung-Knapp-
                   Sidik-Jonkman with t(k - 1) (audit 2026-10-05, K-3: the normal interval covered 87 % with three
                   studies); omega the same way with the variance read from its bootstrap interval (an interval
                   open below has none: that study stays out of the pooled omega, the page counts both). Only uses as in the toolbox: an adapted use (statements removed,
                   added, worded differently, unlocked) and a scale in a loop (passes of one person are not
                   independent) are listed but stay out of the pooled value.

Studies are named by the pseudonym of the pooled files (constructs._study_ref), never by title: the operator's
right covers the anonymous answers, not who ran which study.
"""
import math

from app import constructs, intervals
from app.extensions import db
from app.lang import english

DERSIMONIAN = ("DerSimonian, R., & Laird, N. (1986). Meta-analysis in clinical trials. Controlled Clinical Trials, 7(3), "
               "177–188. https://doi.org/10.1016/0197-2456(86)90046-2")
BONETT = ("Bonett, D. G. (2002). Sample size requirements for testing and estimating coefficient alpha. Journal of "
          "Educational and Behavioral Statistics, 27(4), 335–340. https://doi.org/10.3102/10769986027004335")
HIGGINS = ("Higgins, J. P. T., & Thompson, S. G. (2002). Quantifying heterogeneity in a meta-analysis. Statistics in "
           "Medicine, 21(11), 1539–1558. https://doi.org/10.1002/sim.1186")
HARTUNG = ("Hartung, J., & Knapp, G. (2001). On tests of the overall treatment effect in meta-analysis with normally "
           "distributed responses. Statistics in Medicine, 20(12), 1771–1782. https://doi.org/10.1002/sim.791")
INTHOUT = ("IntHout, J., Ioannidis, J. P. A., & Borm, G. F. (2014). The Hartung-Knapp-Sidik-Jonkman method for random "
           "effects meta-analysis is straightforward and considerably outperforms the standard DerSimonian-Laird method. "
           "BMC Medical Research Methodology, 14, 25. https://doi.org/10.1186/1471-2288-14-25")
ROVER = ("Röver, C., Knapp, G., & Friede, T. (2015). Hartung-Knapp-Sidik-Jonkman approach and its modification for "
         "random-effects meta-analysis with few studies. BMC Medical Research Methodology, 15, 99. "
         "https://doi.org/10.1186/s12874-015-0091-1")
SOURCES = [("Gepoolte Reliabilität (Reliabilitätsgeneralisierung)", [BONETT, DERSIMONIAN, HIGGINS]),
           ("Intervall des gepoolten Werts nach Hartung-Knapp-Sidik-Jonkman", [HARTUNG, INTHOUT, ROVER]),
           ("Konfidenzintervalle je Studie", [intervals.FELDT, intervals.KELLEY])]
# the headings of SOURCES in English, in the same order (sources() picks them when the page is English)
SOURCES_EN = ["Pooled reliability (reliability generalisation)",
              "Interval of the pooled value after Hartung-Knapp-Sidik-Jonkman",
              "Confidence intervals per study"]


def sources():
    """SOURCES with their headings in the language of the page."""
    if not english():
        return SOURCES
    return [(en, refs) for en, (_, refs) in zip(SOURCES_EN, SOURCES)]


def _uses():
    """{construct key: [(item, survey)]} of every toolbox construct in use (linked elements are no toolbox
    constructs: their shared analysis is app/linked_pool.py). One query over the questions that name a construct,
    by the index on the construct key (linked.INDEX; before a LIKE over the settings of every question, performance
    audit 2026-10-09b, P5), with block and survey."""
    from sqlalchemy.orm import joinedload

    from app import linked
    from app.models import Block, Item

    out = {}
    query = (db.select(Item).filter(linked._key_column().isnot(None))
             .options(joinedload(Item.block).joinedload(Block.survey)).order_by(Item.id))
    for item in db.session.scalars(query).unique():
        key = ((item.config or {}).get("construct") or {}).get("key")
        if key and not constructs.is_linked_key(key) and constructs.pooled(key):  # not the RatSWD standard questions
            out.setdefault(key, []).append((item, item.block.survey))
    return out


def _counts(surveys):
    """{survey id: (completed real participations without what quality assurance marks, first month, last month)}:
    the participations of the results page (export.marked_ids, as qc_marked), the months of those left."""
    from app import export
    from app.models import Response

    if not surveys:
        return {}
    marked = {s.id: export.marked_ids(s) for s in surveys}
    found = {}
    rows = db.session.execute(
        db.select(Response.survey_id, Response.id, Response.completed_at)
        .filter(Response.survey_id.in_(list(marked)), Response.source == "real", Response.is_preview.is_(False),
                Response.status == Response.COMPLETED))
    for sid, rid, done in rows:
        if rid not in marked[sid]:
            found.setdefault(sid, []).append(done)
    out = {}
    for sid, times in found.items():
        times = [t for t in times if t]
        first, last = (min(times), max(times)) if times else (None, None)
        out[sid] = (len(found[sid]), first.strftime("%Y-%m") if first else "", last.strftime("%Y-%m") if last else "")
    return out


def overview():
    """[{construct, scale (number of statements or 0), closed, running, n}] of every construct in use, by name; the
    construct as the pages show it (constructs.shown: its texts in the language of the page)."""
    from app.models import Construct, Survey

    uses = _uses()
    found = {c.key: c for c in db.session.scalars(db.select(Construct).filter(Construct.key.in_(list(uses))))} if uses else {}
    closed = {s.id: s for pairs in uses.values() for _, s in pairs if s.status == Survey.CLOSED}
    counts = _counts([closed[sid] for sid in sorted(closed)])
    out = []
    for key, pairs in uses.items():
        c = found.get(key)
        if c is None:
            continue
        surveys = {s.id: s for _, s in pairs}
        closed = [s for s in surveys.values() if s.status == Survey.CLOSED]
        out.append({"construct": constructs.shown(c), "scale": _statements(c), "closed": len(closed),
                    "running": sum(1 for s in surveys.values() if s.status == Survey.RUNNING),
                    "n": sum(counts.get(s.id, (0,))[0] for s in closed)})
    return sorted(out, key=lambda row: row["construct"].name.lower())


def _statements(c):
    config = (c.spec or {}).get("config") or {}
    return len(config.get("statements") or config.get("pairs") or [])


CACHE_SIZE = 64
_cache = {}  # {(survey id, item ids): (fingerprint, {item id: results entry})}, per server process


def _entries(survey, items):
    """{item id: entry of the results page} for the construct questions `items` of `survey`, computed on its data
    pool without what quality assurance marks (as its results page). The kept results of the results page are used
    when they exist; otherwise only these questions are computed and kept here. The omega intervals: kept ones, else
    their bootstrap is started as a job (results.omega_part_state) and the row says it is being computed; one survey
    after the other in this request took minutes (code review 2026-10-15, A5)."""
    from app import results, results_store
    from app.surveys import stored_pool

    pool = {**stored_pool(survey, drop=True), "speed_real": True}
    real = {**pool, "sources": ["real"]}  # the pooled data hold real participations only
    if pool["sources"] == ["real"]:  # as the results page: its kept results, whatever an HB estimation does
        key = results_store.fingerprint(survey, pool)
        kept = results_store.load(survey, key, pool)
        if kept is not None:
            wanted = {i.id for i in items}
            # the omega intervals of these questions: kept with the results page's, else from their job
            # (compute() leaves them out since the performance audit of 2026-10-06, P1)
            _omega(survey, kept[0]["items"], pool, key, wanted)
            return {e["item"].id: e for e in kept[0]["items"] if e["item"].id in wanted}
    ids = tuple(sorted(i.id for i in items))
    fingerprint = results_store.fingerprint(survey, real)
    hit = _cache.get((survey.id, ids))
    if hit and hit[0] == fingerprint:
        _omega(survey, list(hit[1].values()), real, fingerprint, set(ids))  # intervals a job has drawn since
        return hit[1]
    data = results.compute(survey, ("real",), real["all"], True, real["speeder"], True, only=set(ids), omega_ci="wait")
    _omega(survey, data["items"], real, fingerprint, set(ids))
    entries = {e["item"].id: e for e in data["items"]}
    _cache.pop((survey.id, ids), None)
    _cache[(survey.id, ids)] = (fingerprint, entries)
    # no lock: another request may evict the same oldest key at once (code review 2026-10-15, L5)
    while len(_cache) > CACHE_SIZE:
        _cache.pop(next(iter(_cache), None), None)
    return entries


def _omega(survey, entries, pool, key, items):
    """The omega intervals into the results entries (of the questions `items`, pool and fingerprint key) that wait
    for them: kept ones, else their bootstrap started as a job (results.omega_part_state; drawn here only with
    OMEGA_BACKGROUND off). Changes the entries in place."""
    from app import results

    data = {"items": entries}
    wanted = {ref for ref in results.omega_waiting(data) if int(ref.split(":", 1)[0]) in items}
    if wanted:
        found, _ = results.omega_part_state(survey, pool, key, wanted)
        results.apply_omega(data, found)


def _scales(entry):
    """[(subscale name or None, figures)] of a results entry; [] for a question without a scale."""
    if not entry:
        return []
    if entry.get("subscales"):
        return [(s["name"], s) for s in entry["subscales"]]
    if entry.get("scale"):
        return [(None, entry["scale"])]
    return []


def _log_ci(ci):
    """Standard error of ln(1 - x) read from a 95 % interval (lo, hi) of x; None for an interval open below."""
    if not ci or ci[0] is None or ci[1] >= 1:
        return None
    return (math.log(1 - ci[0]) - math.log(1 - ci[1])) / (2 * intervals.Z)


def meta(studies):
    """Random-effects pooling (DerSimonian-Laird) of [(estimate, standard error of ln(1 - estimate))]:
    {"value", "ci", "k", "i2", "tau2"}, None below two studies. The interval after Hartung-Knapp-Sidik-Jonkman:
    the weighted residual variance of the estimates around the pooled value and t(k - 1) instead of z (Hartung &
    Knapp, 2001; IntHout et al., 2014), never below the variance of DerSimonian-Laird (Röver et al., 2015). With two
    studies "ci" is None: t(1) = 12.71 made the interval reach far below 0 (math audit 2026-10-06, N-1, decision 6);
    the intervals of the two studies stay in their rows."""
    data = [(math.log(1 - x), se) for x, se in studies if x is not None and x < 1 and se and se > 0]
    if len(data) < 2:
        return None
    w = [1 / se ** 2 for _, se in data]
    fixed = sum(wi * t for wi, (t, _) in zip(w, data)) / sum(w)
    q = sum(wi * (t - fixed) ** 2 for wi, (t, _) in zip(w, data))
    df = len(data) - 1
    c = sum(w) - sum(wi ** 2 for wi in w) / sum(w)
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    w_re = [1 / (se ** 2 + tau2) for _, se in data]
    mean = sum(wi * t for wi, (t, _) in zip(w_re, data)) / sum(w_re)
    # the scale factor of the variance at least 1 (Röver et al., 2015): estimates that agree more closely than
    # their standard errors expect would make the interval narrower than DerSimonian-Laird's, down to a point
    spread = sum(wi * (t - mean) ** 2 for wi, (t, _) in zip(w_re, data)) / df
    se = math.sqrt(max(1.0, spread) / sum(w_re))
    half = intervals.t_quantile(0.975, df) * se
    # ln(1 - x) falls when x rises: the upper end of the interval comes from the lower end of the log
    return {"value": 1 - math.exp(mean), "ci": (1 - math.exp(mean + half), 1 - math.exp(mean - half)) if df > 1 else None,
            "k": len(data), "i2": max(0.0, (q - df) / q) if q > 0 else 0.0, "tau2": tau2}


def bonett_se(alpha, n, k):
    """Standard error of ln(1 - alpha) after Bonett (2002)."""
    if alpha is None or alpha >= 1 or n is None or n <= 2 or k < 2:
        return None
    return math.sqrt(2 * k / ((k - 1) * (n - 2)))


def report(c):
    """The pooled view of construct `c`: {"studies": [one row per use in a closed survey], "scales": [per scale or
    subscale: its rows and the pooled value], "running", "drafts", "n"}."""
    from app.models import Survey

    pairs = _uses().get(c.key, [])
    closed = [(i, s) for i, s in pairs if s.status == Survey.CLOSED]
    by_survey = {}
    for item, s in closed:
        by_survey.setdefault(s.id, (s, []))[1].append(item)
    counts = _counts([by_survey[sid][0] for sid in sorted(by_survey)])
    studies, scales = [], {}
    for s, items in by_survey.values():
        entries = _entries(s, items) if _statements(c) >= 2 else {}
        n, first, last = counts.get(s.id, (0, "", ""))
        # two elements of the construct in one survey are answered by the same people: only the first one that can
        # be pooled enters per scale, else they would count as two independent studies (audit 2026-10-05)
        pooled_here = set()
        for item in sorted(items, key=lambda i: (i.block.position, i.position)):
            meta_ = (item.config or {}).get("construct") or {}
            mods = constructs.modifications(item, c, de=True)
            adapted = bool(mods) or bool(meta_.get("unlocked"))
            entry = entries.get(item.id)
            row = {"study": constructs._study_ref(s.id), "item": item.code, "languages": s.languages,
                   "months": first if first == last else f"{first} – {last}", "version": meta_.get("version", ""),
                   "adapted": adapted, "modifications": mods, "participations": n,
                   "answered": entry.get("answered") if entry else None, "looped": bool(entry and entry.get("passes"))}
            studies.append(row)
            for name, figures in _scales(entry):
                pooled = not adapted and not figures.get("pooled")
                same = pooled and name in pooled_here
                if pooled:
                    pooled_here.add(name)
                scales.setdefault(name, []).append({**row, "k": figures["k"], "n": figures["n"], "alpha": figures["alpha"],
                                                    "alpha_ci": figures.get("alpha_ci"), "omega": figures.get("omega"),
                                                    "omega_ci": figures.get("omega_ci"),
                                                    "omega_wait": bool(figures.get("omega_wait")),
                                                    "sb": figures.get("sb"), "sb_ci": figures.get("sb_ci"),
                                                    "in_pool": pooled and not same, "same_people": same})
    out = []
    for name, rows in scales.items():
        taken = [r for r in rows if r["in_pool"]]
        out.append({"name": name, "rows": rows,
                    "alpha": meta([(r["alpha"], bonett_se(r["alpha"], r["n"], r["k"])) for r in taken]),
                    "omega": meta([(r["omega"], _log_ci(r["omega_ci"])) for r in taken if r["k"] > 2]),
                    "n": sum(r["n"] for r in taken if r["alpha"] is not None),
                    # the pooled omega can rest on fewer surveys than alpha (no bootstrap interval below n = 30 or
                    # one open below): the page names both counts and N per value (audit 2026-10-05, K-6)
                    "n_omega": sum(r["n"] for r in taken if r["k"] > 2 and r["omega"] is not None
                                   and _log_ci(r["omega_ci"]) is not None and r["omega"] < 1)})
    surveys = {s.id: s for _, s in pairs}
    return {"studies": studies, "scales": out, "n": sum(counts.get(sid, (0,))[0] for sid in by_survey),
            "running": sum(1 for s in surveys.values() if s.status == Survey.RUNNING),
            "drafts": sum(1 for s in surveys.values() if s.status == Survey.DRAFT)}
