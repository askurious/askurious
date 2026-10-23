"""Weighting by raking (concept statistik-erweiterungen-konzept.md, section 5; decisions K10 to K13).

The setting (Survey.weighting, page "Quoten", section "Gewichtung") names where the targets come from:
  "quotas"  the target shares of the survey's own quotas: per quota the distribution of its cells' targets
  "census"  the population of the quota assistant "Bevölkerung Deutschland" (app/census.py): age, gender, state
and which attributes (quotas by their code, census attributes by key) are used, each ticked on its own.

A person's group in an attribute is found by the same conditions that decide a quota cell (quotas.match), for the
census by the conditions the assistant gives its cells (census.cell_conds) on the questions it would use
(census.questions). Who fits no group of an attribute (no answer, "divers" where the official figures know two
sexes, an age outside the span, a cell with target 0) keeps the weight as it is in that step and is carried in
the others (K12); the page names how many.

Iterative proportional fitting (Deming & Stephan, 1940): one round adjusts every attribute in turn, so that the
weighted shares of its groups are the targets; then the weights are capped to [LOW, HIGH] (K11, a choice of
Askurious) and scaled to mean 1 again, in turn until both hold (cap), and the next round adjusts again. It stops when no share is more than TOLERANCE away
from its target, or after MAX_ROUNDS rounds. A group with a target but nobody in the sample cannot be reached: its
share goes to the other groups of the attribute (named in the hints).

Only completed participations of the data pool that quality assurance does not mark get a weight; everyone else
has none (empty in the export column `weight`). The effective sample size after Kish (1965) is
n_eff = (sum w)^2 / sum w^2.

The weighted statistics (shares, mean, SD) live here as plain functions on (values, weights), so that other
analyses (cross tables, intervals) can take weights the same way.
"""
import math

DEMING_STEPHAN = ("Deming, W. E., & Stephan, F. F. (1940). On a least squares adjustment of a sampled frequency table when "
                  "the expected marginal totals are known. The Annals of Mathematical Statistics, 11(4), 427–444. "
                  "https://doi.org/10.1214/aoms/1177731829")
KISH = "Kish, L. (1965). Survey sampling. Wiley."
LUMLEY = "Lumley, T. (2010). Complex surveys: A guide to analysis using R. Wiley. https://doi.org/10.1002/9780470580066"
AAPOR = ("Baker, R., Brick, J. M., Bates, N. A., Battaglia, M., Couper, M. P., Dever, J. A., Gile, K. J., & Tourangeau, R. "
         "(2013). Summary report of the AAPOR Task Force on Non-probability Sampling. Journal of Survey Statistics and "
         "Methodology, 1(2), 90–143. https://doi.org/10.1093/jssam/smt008")
SOURCES = (DEMING_STEPHAN, KISH, AAPOR)

LOW, HIGH = 0.2, 5.0  # cap of the weights (mean 1), K11: a choice of Askurious
TOLERANCE = 0.001     # largest distance of a weighted share from its target (0.1 percentage points)
MAX_ROUNDS = 100
# from this largest remaining distance of a weighted share from its target (in percentage points) the page marks the
# weighting "Prüfen" at its figures and the method section says so; below it the rounding of the targets is the only
# difference worth nothing (audit Datenspeicherung + Berechnungen 2026-10-21, C5; Ralf: no banner, at the figure)
CHECK_PP = 2.0
DECIMALS = 4          # of a weight in the export and in every weighted figure (the same numbers as the file)
KINDS = {"quotas": "Soll-Anteile der eigenen Quoten", "census": "Bevölkerung Deutschland (Quotenassistent)"}
CENSUS_QUOTA = {"gender": "de_geschlecht", "age": "de_alter", "state": "de_land"}
COLUMN = ("weight", "Gewicht (Raking)")
COLUMN_EN = "Weight (raking)"  # the label in an English codebook (the data files keep the German one)
# what a census attribute needs when the survey has no question for it (the page and the problems say it)
MISSING = {"age": "Es fehlt eine Frage nach dem Alter in ganzen Jahren (Konstrukt Alter oder eine Zahlenfrage „Alter“)",
           "gender": "Es fehlt eine Einfachauswahl zum Geschlecht mit den Antworten weiblich und männlich (z. B. Konstrukt Geschlecht)",
           "state": "Es fehlt eine Frage nach dem Bundesland (Element Bundesland, Postleitzahl oder Standort)"}
KINDS_EN = {"quotas": "Target shares of your own quotas", "census": "Population of Germany (quota assistant)"}
MISSING_EN = {"age": "A question on age in whole years is missing (construct Alter or a number question “Alter”)",
              "gender": "A single choice on gender with the answers female and male is missing (e.g. construct Geschlecht)",
              "state": "A question on the federal state is missing (element Bundesland, postcode or location)"}


def kinds():
    """KINDS as the page "Quoten" shows them, in the language of the page."""
    from app.lang import english

    return KINDS_EN if english() else KINDS


def missing():
    """MISSING as the page "Quoten" shows them, in the language of the page."""
    from app.lang import english

    return MISSING_EN if english() else MISSING


def _said(shown, de, en, **values):
    """A problem of margins(): German (for the codebook and the key of kept results), or in the language of the
    page where the page "Quoten" shows it (shown=True)."""
    from app.lang import de_en

    return de_en(de, en, **values) if shown else de.format(**values)


# --- the setting ----------------------------------------------------------------------------------------


def setting(survey):
    """{"kind": "quotas" | "census" | "", "quotas": [quota codes], "census": [attribute keys]}, cleaned."""
    from app import census

    saved = getattr(survey, "weighting", None) or {}
    kind = saved.get("kind") if saved.get("kind") in KINDS else ""
    codes = {q.code for q in survey.quotas}
    return {"kind": kind,
            "quotas": [c for c in dict.fromkeys(saved.get("quotas") or []) if isinstance(c, str) and c in codes],
            "census": [k for k in census.ATTRIBUTES if k in (saved.get("census") or [])]}


def from_form(form):
    """The setting a form sends (radio "kind", ticks "w_quota" and "w_census"); None for no weighting."""
    from app import census

    kind = form.get("kind") if form.get("kind") in KINDS else ""
    if not kind:
        return None
    return {"kind": kind, "quotas": [c for c in dict.fromkeys(form.getlist("w_quota")) if c][:50],
            "census": [k for k in census.ATTRIBUTES if k in form.getlist("w_census")]}


def clean(raw):
    """A setting read from a survey file (app/transfer.py): only the known shape, else None."""
    from app import census

    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    quotas = [c for c in (raw.get("quotas") if isinstance(raw.get("quotas"), list) else []) if isinstance(c, str)][:50]
    return {"kind": raw["kind"], "quotas": [c[:40] for c in dict.fromkeys(quotas)],
            "census": [k for k in census.ATTRIBUTES if k in (raw.get("census") if isinstance(raw.get("census"), list) else [])]}


def _census_opts(survey):
    """The choices of the quota assistant as it last ran on the survey (age span, groups, regions), else its defaults."""
    from app import census

    found = census.facts(survey) or {}
    return {"n": 0, "cross": "none", "lo": found.get("lo") if found.get("lo") in census.AGE_FROM else census.AGE_FROM[0],
            "hi": found.get("hi") if found.get("hi") in census.AGE_TO else None,
            "scheme": found.get("scheme") if found.get("scheme") in census.SCHEMES else "decades",
            "regions": found.get("regions") if found.get("regions") in census.REGIONS else "states",
            "state": "", "postal": found.get("state") in ("postal_code", "geolocation"), "from": found.get("state"),
            "age": str(found.get("age_item") or ""), "gender": str(found.get("gender_item") or ""),
            "gf": (found.get("sexes") or {}).get("f", ""), "gm": (found.get("sexes") or {}).get("m", "")}


def census_questions(survey):
    """{"age": Item | None, "gender": ..., "state": ...}: the questions the population figures are matched with."""
    from app import census

    opts = _census_opts(survey)
    found = census.questions(survey, age=opts["age"], gender=opts["gender"])
    if opts["postal"]:  # the assistant took the state from a postal code question: so does this
        kind = opts["from"]
        postal = next((i for i in census.state_sources(survey) if i.type == kind), None)
        found["state"] = postal or found["state"]
    return found


def margins(survey, shown=False):
    """The attributes of the setting: ([{"key", "name", "groups": [{"name", "share", "conds"}]}], [problems]). share
    is the target share (0..1) of a group; a group's conds all have to hold. Problems name what was left out.
    shown: names and problems in the language of the page (the page "Quoten"); else German."""
    from app import census, flow

    names = census.attributes() if shown else census.ATTRIBUTES
    needs = missing() if shown else MISSING

    chosen = setting(survey)
    out, problems = [], []
    if chosen["kind"] == "quotas":
        by_code = {q.code: q for q in survey.quotas}
        for code in chosen["quotas"]:
            quota = by_code[code]
            total = sum(c.target or 0 for c in quota.cells if c.conds)
            if not total:
                problems.append(_said(shown, "Quote „{name}“: keine Teilquote mit Bedingungen und Soll; nicht gewichtet.",
                                      "Quota “{name}”: no sub-quota with conditions and target; not weighted.", name=quota.name))
                continue
            # every cell, in the quota's order (the first that fits decides, as quotas.match): a cell with target 0
            # takes its people out of this attribute
            out.append({"key": f"quota:{code}", "name": quota.name,
                        "groups": [{"name": c.name or c.code, "share": (c.target or 0) / total, "conds": c.conds or []}
                                   for c in quota.cells]})
    elif chosen["kind"] == "census":
        opts = _census_opts(survey)
        found = census_questions(survey)
        planned = {q["code"]: q for q in census.plan(opts)["quotas"]}
        ids = {k: (found[k].id if found[k] is not None else None) for k in ("age", "gender", "state")}
        if found["state"] is not None and found["state"].type in ("postal_code", "geolocation"):
            ids["state"] = f"{flow.STATE_REF}{found['state'].id}"
        sexes = census.gender_codes(found["gender"], opts)
        for key in chosen["census"]:
            if found[key] is None:
                problems.append(_said(shown, "{name}: {need}; nicht gewichtet.", "{name}: {need}; not weighted.",
                                      name=names[key], need=needs[key]))
                continue
            if key == "gender" and len(sexes) < 2:
                problems.append(_said(shown, "Geschlecht: Bei „{code}“ ist nicht zu erkennen, welche Antwort weiblich und welche "
                                      "männlich heißt (im Quotenassistenten zuordnen); nicht gewichtet.",
                                      "Gender: for “{code}” it is not clear which answer means female and which male "
                                      "(assign them in the quota assistant); not weighted.", code=found[key].code))
                continue
            spec = planned[CENSUS_QUOTA[key]]
            persons = sum(c["persons"] for c in spec["cells"])
            out.append({"key": f"census:{key}", "name": names[key],
                        "groups": [{"name": c["name"], "share": c["persons"] / persons,
                                    "conds": census.cell_conds(c, ids["age"], ids["gender"], ids["state"], sexes)}
                                   for c in spec["cells"]]})
    return out, problems


def active(survey):
    """Is a weighting set that has at least one attribute to weight by?"""
    chosen = setting(survey)
    return bool(chosen["kind"] and (chosen["quotas"] if chosen["kind"] == "quotas" else chosen["census"]))


def fingerprint(survey):
    """What the weights rest on besides the answers: for the key of the kept weighted results (results_store)."""
    found, problems = margins(survey)
    return [setting(survey), [[m["key"], [[g["share"], g["conds"]] for g in m["groups"]]] for m in found], problems]


# --- raking ---------------------------------------------------------------------------------------------


def rake(groups, shares, low=LOW, high=HIGH, tolerance=TOLERANCE, rounds=MAX_ROUNDS):
    """Weights (mean 1) for n persons. groups: per attribute a sequence of n group numbers (-1 = fits no group);
    shares: per attribute the target share of each group (adding up to 1 over the groups that have people).
    Returns (weights as a numpy array, {"rounds", "deviation", "converged"})."""
    import numpy as np

    codes = [np.asarray(g, dtype=int) for g in groups]
    targets = [np.asarray(s, dtype=float) for s in shares]
    n = len(codes[0]) if codes else 0
    w = np.ones(n)
    if not n or not codes:
        return w, {"rounds": 0, "deviation": 0.0, "converged": True}

    def deviation():
        worst = 0.0
        for c, t in zip(codes, targets):
            has = c >= 0
            total = w[has].sum()
            if total > 0:
                got = np.bincount(c[has], weights=w[has], minlength=len(t)) / total
                worst = max(worst, float(np.abs(got - t).max()))
        return worst

    done = 0
    for done in range(1, rounds + 1):
        for c, t in zip(codes, targets):
            has = c >= 0
            total = w[has].sum()
            if total <= 0:
                continue
            got = np.bincount(c[has], weights=w[has], minlength=len(t))
            factor = np.where(got > 0, t * total / np.where(got > 0, got, 1), 1.0)
            w[has] = w[has] * factor[c[has]]
        w = cap(w, low, high)
        if deviation() < tolerance:
            break
    dev = deviation()
    return w, {"rounds": done, "deviation": dev, "converged": dev < tolerance}


def cap(w, low=LOW, high=HIGH, rounds=MAX_ROUNDS):
    """Weights (a numpy array) scaled to mean 1 and capped to [low, high], in turn until both hold: capping once
    after the scaling left a mean below 1 (audit 2026-10-05, K-5: 0.600 with one person for half the target; in the
    export column the sum was then not n, and the cap no longer referred to the mean). low None: only scaled."""
    import numpy as np

    n = len(w)
    w = w * (n / w.sum())
    if low is None:
        return w
    for _ in range(rounds):
        if w.min() >= low - 1e-12 and w.max() <= high + 1e-12:
            break
        w = np.clip(w, low, high)
        w = w * (n / w.sum())
    return w


def n_eff(weights):
    """Effective sample size after Kish (1965): (sum w)^2 / sum w^2."""
    ws = [w for w in weights if w is not None]
    square = sum(w * w for w in ws)
    return sum(ws) ** 2 / square if square else 0.0


def _answers(response):
    from app import runtime

    return {**runtime.load_answers(response), **{f"p:{k}": v for k, v in (response.params or {}).items()}}


def compute(survey, table):
    """The weights of the rows of export.rows() (a Table) and what the page says about them, or None without a
    weighting: {"weights": {resp_id: weight}, "n", "n_eff", "max", "min", "attributes": [{"name", "groups": [{"name",
    "share", "n"}], "missing"}], "problems", "hints", "rounds", "converged", "deviation", "check" (a target missed by CHECK_PP or more)}. Kept
    with the table."""
    if not active(survey):
        return None
    return table.once("weights", lambda: _compute(survey, table))


def domain(survey, whole, part):
    """The weights of `part`, rows of the table `whole` (a group shown under "Nur zeigen"), as those of the whole
    pool: a subgroup (domain) keeps the weights of the whole sample and is not raked to the margins anew (Lumley,
    2010; audit 2026-10-06, E-2). Kept with `part`, so compute(survey, part) gives them; n, n_eff, max and min are
    those of the part. None without a weighting."""
    found = compute(survey, whole)
    if found is None:
        return None
    kept = {r.get("resp_id") for r in part}
    chosen = {k: v for k, v in found["weights"].items() if k in kept}
    values = list(chosen.values())
    sub = {**found, "weights": chosen, "n": len(values), "n_eff": n_eff(values), "max": max(values, default=None),
           "min": min(values, default=None), "domain": True}
    return part.once("weights", lambda: sub)


def _compute(survey, table):
    from app import flow
    from app.models import Response
    from app.units import round_half_up

    # the attributes with their German names (the sentence of the Methodenteil); the problems the results page
    # shows in its language (UI/UX audit 2026-10-07, W112)
    found = margins(survey)[0]
    problems = margins(survey, shown=True)[1]
    rows = [r for r in table if r.get("status") == Response.COMPLETED and r.get("qc_marked") != 1]
    people = [(r["resp_id"], _answers(table.numbered[r["resp_id"]])) for r in rows if r["resp_id"] in table.numbered]
    groups, shares, attributes, hints = [], [], [], []
    for margin in found:
        picked = []
        for _, answers in people:
            k = next((k for k, g in enumerate(margin["groups"]) if g["conds"] and all(flow.holds(c, answers, survey) for c in g["conds"])), -1)
            picked.append(k if k >= 0 and margin["groups"][k]["share"] > 0 else -1)
        counts = [picked.count(k) for k in range(len(margin["groups"]))]
        reached = sum(g["share"] for g, n in zip(margin["groups"], counts) if n)
        empty = [g["name"] for g, n in zip(margin["groups"], counts) if not n and g["share"] > 0]
        if not reached:
            problems.append(_said(True, "„{name}“: Niemand im Datenpool passt in eine Gruppe; nicht gewichtet.",
                                  "“{name}”: no one in the data pool fits a group; not weighted.", name=margin["name"]))
            continue
        if empty:
            hints.append(_said(True, "„{name}“: niemand in {groups}{more}; ihr Soll-Anteil verteilt sich auf die übrigen Gruppen.",
                               "“{name}”: no one in {groups}{more}; their target share is spread over the other groups.",
                               name=margin["name"], groups=", ".join(_said(True, "„{e}“", "“{e}”", e=e) for e in empty[:5]),
                               more=_said(True, " und {k} weiteren", " and {k} more", k=len(empty) - 5) if len(empty) > 5 else ""))
        target = [g["share"] / reached if n else 0.0 for g, n in zip(margin["groups"], counts)]
        groups.append(picked)
        shares.append(target)
        attributes.append({"name": margin["name"], "missing": picked.count(-1),
                           "groups": [{"name": g["name"], "share": t, "n": n} for g, t, n in zip(margin["groups"], target, counts)]})
    if groups:
        w, info = rake(groups, shares)
        values = [float(round_half_up(float(x), DECIMALS)) for x in w]
    else:
        info, values = {"rounds": 0, "deviation": 0.0, "converged": True}, [1.0] * len(people)
    weights = {resp_id: v for (resp_id, _), v in zip(people, values)}
    # a target missed by CHECK_PP or more is marked "Prüfen" at the weighting figures (no warning of its own, C5)
    info["check"] = 100 * info["deviation"] >= CHECK_PP
    return {"weights": weights, "n": len(values), "n_eff": n_eff(values), "max": max(values, default=None),
            "min": min(values, default=None), "attributes": attributes, "problems": problems, "hints": hints, **info}


def fill_column(survey, table):
    """Put the weight into every row (`weight`; None where there is none). Nothing without a weighting."""
    found = compute(survey, table)
    if found is None:
        return None
    for r in table:
        r[COLUMN[0]] = found["weights"].get(r["resp_id"])
    return found


def codebook_note(survey, lang=None):
    """The codebook entry: procedure, attributes, targets, cap; in the reader's language `lang` (None: the page's;
    app.items.base.reader)."""
    from app.items.base import reader

    found, problems = margins(survey)
    chosen = setting(survey)
    if reader(lang) == "en":
        from app.units import fixed

        target = {"quotas": "the target shares of the survey's own quotas",
                  "census": "the population of Germany (quota assistant)"}[chosen['kind']]
        parts = [f"Raking (iterative proportional fitting, Deming & Stephan, 1940) to {target}"]
        for m in found:
            parts.append(f"{m['name']}: " + ", ".join(f"{g['name']} {fixed(100 * g['share'], 1)} %" for g in m["groups"] if g["share"]))
        parts.append(f"weights capped at {fixed(LOW, 1)} to {fixed(HIGH, 1)} (mean 1), stopped at a deviation of a share of at "
                     f"most {fixed(TOLERANCE, 3)} or after {MAX_ROUNDS} rounds; who does not fit an attribute keeps their "
                     "weight for it; empty = not weighted (not completed or marked by quality assurance)")
        return "; ".join(parts) + ("; " + " ".join(problems) if problems else "")
    parts = [f"Raking (iterative proportionale Anpassung, Deming & Stephan, 1940) auf {KINDS[chosen['kind']]}"]
    for m in found:
        parts.append(f"{m['name']}: " + ", ".join(f"{g['name']} {_de(100 * g['share'], 1)} %" for g in m["groups"] if g["share"]))
    parts.append(f"Gewichte gekappt auf {_de(LOW, 1)} bis {_de(HIGH, 1)} (Mittelwert 1), Abbruch bei höchstens "
                 f"{_de(TOLERANCE, 3)} Abweichung eines Anteils oder nach {MAX_ROUNDS} Runden; wer in ein Merkmal nicht passt, "
                 "behält dafür sein Gewicht; leer = nicht gewichtet (nicht abgeschlossen oder von der Qualitätssicherung markiert)")
    return "; ".join(parts) + ("; " + " ".join(problems) if problems else "")


def _de(value, places):
    from app.units import fixed

    return fixed(value, places).replace(".", ",")


def method_sentence_en(survey, found):
    """The same fact for the AI (English, decimal points)."""
    from app.units import round_half_up  # half up, like the German sentence

    if not found or not found.get("attributes"):
        return None
    source = ("the target shares of the quotas" if setting(survey)["kind"] == "quotas"
              else "the population distribution of the Federal Statistical Office (Destatis)")
    return (f"Weighting (offered as an option for the results): raking (Deming & Stephan, 1940) of the completed participations "
            f"by {', '.join(a['name'] for a in found['attributes'])} to {source}; weights capped at {LOW} to {HIGH}; "
            f"Kish (1965) effective sample size {round_half_up(found['n_eff'])} of {found['n']}"
            + (f". The targets were not reached: after {found['rounds']} rounds a weighted share still differed by up to "
               f"{round_half_up(100 * found['deviation'], 1)} percentage points from its target (capping of the weights)"
               if found.get("check") else ""))


def method_sentence(survey, found):
    """One sentence for the method section (German), or None."""
    if not found or not found.get("attributes"):
        return None
    names = [a["name"] for a in found["attributes"]]
    said = ", ".join(names[:-1]) + (" und " if len(names) > 1 else "") + names[-1]
    source = ("die Soll-Anteile der Quoten" if setting(survey)["kind"] == "quotas"
              else "die Bevölkerungsverteilung des Statistischen Bundesamts")
    return (f"Für gewichtete Ergebnisse wurden die Abschlüsse per Raking (Deming & Stephan, 1940) nach {said} an {source} "
            f"angepasst (Gewichte gekappt auf {_de(LOW, 1)} bis {_de(HIGH, 1)}; effektive Fallzahl nach Kish, 1965: "
            f"{_de(found['n_eff'], 0)} von {found['n']})."
            + (f" Die Zielwerte wurden nicht erreicht: Nach {found['rounds']} Runden wich ein gewichteter Anteil noch um bis "
               f"zu {_de(100 * found['deviation'], 1)} Prozentpunkte von seinem Zielwert ab (Kappung der Gewichte)."
               if found.get("check") else ""))


# --- weighted statistics (also for other analyses: cross tables, intervals) -----------------------------


def total(weights):
    return sum(w for w in weights if w is not None)


def shares(values, weights):
    """{value: weighted share} of the values that are not None (their weights alike)."""
    sums = {}
    for v, w in zip(values, weights):
        if v is not None and w:
            sums[v] = sums.get(v, 0.0) + w
    whole = sum(sums.values())
    return {v: s / whole for v, s in sums.items()} if whole else {}


def mean(values, weights):
    """Weighted mean of the numbers (None left out with their weight); None without any."""
    pairs = [(x, w) for x, w in zip(values, weights) if x is not None and w]
    whole = sum(w for _, w in pairs)
    return sum(x * w for x, w in pairs) / whole if whole else None


def sd(values, weights):
    """Weighted standard deviation for sampling weights (Lumley, 2010): n / (n - 1) times
    sum w(x - m)^2 / sum w over the n values with a weight; with all weights 1 the sample SD (n - 1). Before the
    audit 2026-10-12 (S-10) the formula for reliability weights (V1 - V2 / V1), which came out too large for the
    raking weights."""
    pairs = [(x, w) for x, w in zip(values, weights) if x is not None and w]
    n = len(pairs)
    if n < 2:
        return None
    whole = sum(w for _, w in pairs)
    if whole <= 0:
        return None
    m = sum(x * w for x, w in pairs) / whole
    return math.sqrt(n / (n - 1) * sum(w * (x - m) ** 2 for x, w in pairs) / whole)


# The standard error of a weighted mean by linearization (Taylor series; Lumley, 2010), sum w^2 (x - m)^2 / (sum w)^2
# times n / (n - 1): SD / sqrt(n_eff) after Kish holds only where the spread is the same in every weighting class;
# with a larger spread in an up-weighted group it was too narrow (audit 2026-10-05, K-7: coverage 0.90 instead of
# 0.95). With all weights 1 it is SD / sqrt(n).
def mean_se(values, weights):
    """Linearization standard error of the weighted mean (None and weight 0 left out); None below two values."""
    pairs = [(x, w) for x, w in zip(values, weights) if x is not None and w]
    n = len(pairs)
    if n < 2:
        return None
    whole = sum(w for _, w in pairs)
    m = sum(x * w for x, w in pairs) / whole
    return math.sqrt(n / (n - 1) * sum(w * w * (x - m) ** 2 for x, w in pairs)) / whole


def share_n(p, n, whole, square_in, square):
    """The sample size for the Wilson interval of a weighted share p: p(1 - p) / SE^2 with the linearization SE of
    the same formula as mean_se for 0/1 values. n answers with weights summing to `whole`, sum w^2 `square` over
    all of them and `square_in` over those with the answer. Kish's n_eff where p is 0 or 1 (no spread to read)."""
    if not whole or not square or n < 2:
        return 0.0
    var = n / (n - 1) * (square_in * (1 - p) ** 2 + (square - square_in) * p * p) / (whole * whole)
    return p * (1 - p) / var if var > 0 and 0 < p < 1 else whole * whole / square
