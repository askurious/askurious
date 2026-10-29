"""The hypotheses of a diary study tested with a multilevel model (longitudinal and multilevel concept 2026-10-17, E8;
Ralf: "umsetzen", as recommended). Before, a diary study had no test: entries of one person are no independent cases
(app/hypotheses._diary_note). Now a relationship between two numbers is tested as the level they stand on asks:

  both in the entries     a linear mixed model of what is explained on what explains, split into the part within
                          persons (the entry's value minus the person's mean) and between persons (the mean; Enders &
                          Tofighi, 2007), a random intercept per person and with the plan's tick a random slope; the
                          verdict on the effect within persons, p with the degrees of freedom after Satterthwaite
                          (1946). With the tick "zeitverzögert" what explains comes from the person's entry before.
  explaining once         (the entry part, e.g. age) a mixed model of the entries on that value, random intercept.
  explained once          the person's mean of what explains against it, one pair per person (Pearson).
  both once               one pair per person (Pearson).

Minimums (set by Askurious, E10): MIN_PERSONS persons and a median of MIN_MEDIAN entries for the models within
persons. Numbers in app/mixed.py."""
import math
import statistics
from collections import Counter

import numpy as np

from app import mixed
from app.lang import de_en

MIN_PERSONS = 30
MIN_MEDIAN = 5


def _person_and_time(table):
    """The person of every row (the export's column "person", else the enrolment of the participation) and a key of
    its time (start, then the running number)."""
    numbered = getattr(table, "numbered", {}) or {}
    persons, times = [], []
    for r in table:
        person = r.get("person") or None
        if person is None:
            resp = numbered.get(r.get("resp_id"))
            person = f"e{resp.enrolment_id}" if resp is not None and resp.enrolment_id else None
        persons.append(person)
        times.append((str(r.get("started") or ""), r.get("resp_id") or 0))
    return persons, times


def _once(rows, persons):
    """Whether every person has at most one row among `rows`."""
    counts = Counter(persons[i] for i in rows)
    return bool(counts) and max(counts.values()) == 1


def _num(v, lang, d=2):
    from app.hypotheses import _de_lang

    return _de_lang(v, d, lang)


def _p(p, lang):
    from app.hypotheses import _p_lang

    return _p_lang(p, lang)


def _df(df, lang):
    from app import units

    return units.number(df, 1, lang)


def evaluate(survey, h, table, xs, ys, out, lang="de"):
    """Fill `out` (hypotheses._evaluate_one) for a relationship in a diary study; False when there is nothing to test
    this way (no person behind the rows), so the caller keeps its note."""
    from app import study_plan
    from app.hypotheses import _verdict

    persons, times = _person_and_time(table)
    x_rows = [i for i, v in enumerate(xs) if isinstance(v, float) and persons[i] is not None]
    y_rows = [i for i, v in enumerate(ys) if isinstance(v, float) and persons[i] is not None]
    if not x_rows or not y_rows:
        return False
    x_once = _once(x_rows, persons) and not set(x_rows) & set(y_rows)
    y_once = _once(y_rows, persons) and not set(x_rows) & set(y_rows)
    out["analysis"] = "mixed"
    out["why"] = de_en("Tagebuchstudie: Einträge stehen in Personen, daher ein Mehrebenenmodell statt eines Tests über "
                       "alle Einträge.", "Diary survey: entries are nested in people, hence a multilevel model instead "
                       "of a test over all entries.")
    if y_once:  # what is explained stands once per person: one pair per person, what explains as the person's mean
        from app import scales

        means = {}
        for i in x_rows:
            means.setdefault(persons[i], []).append(xs[i])
        pairs = [(statistics.fmean(means[persons[i]]), ys[i]) for i in y_rows if persons[i] in means]
        r, n, p = scales.pearson([a for a, _ in pairs], [b for _, b in pairs])
        if r is None:
            out["notes"].append(de_en("Kein Test möglich: zu wenige Personen mit beiden Angaben.",
                                      "No test possible: too few people with both values."))
            return True
        out.update(n=n, p=p, effect=("r", r), verdict=_verdict(p, r, out["direction"]))
        out["sentence"] = f"r({n - 2}) = {_num(r, lang)}, {_p(p, lang)}"
        out["notes"].append(de_en("Eine Angabe je Person: {what}, je Person ein Wertepaar.",
                                  "One value per person: {what}, one pair of values per person.",
                                  what=de_en("das Erklärende als Mittelwert der Einträge", "what explains as the mean of the entries")
                                  if not x_once else de_en("beide einmal", "both once")))
        return True
    settings = study_plan.plan_of(survey)["diary"]
    group = np.array([persons[i] for i in y_rows])
    y = np.array([ys[i] for i in y_rows])
    per = Counter(group.tolist())
    if len(per) < MIN_PERSONS or statistics.median(per.values()) < MIN_MEDIAN:
        out["notes"].append(de_en("Für das Mehrebenenmodell zu wenige Daten: {k} Personen mit im Median {m} Einträgen "
                                  "(nötig: {kk} Personen, im Median {mm}; Festlegung von Askurious).",
                                  "Too few data for the multilevel model: {k} people with a median of {m} entries "
                                  "(needed: {kk} people, a median of {mm}; set by Askurious).", k=len(per),
                                  m=_num(statistics.median(per.values()) if per else 0, lang, 0) if per else 0,
                                  kk=MIN_PERSONS, mm=MIN_MEDIAN))
        return True
    lagged = False
    if x_once:  # what explains once per person (the entry part): a value of the person
        value = {persons[i]: xs[i] for i in x_rows}
        x = np.array([value.get(persons[i], np.nan) for i in y_rows], dtype=float)
        X = np.column_stack([np.ones(len(y)), x])
        names = ["(Intercept)", "x"]
        Z = None
    else:
        if settings["lagged"]:  # the person's entry before (in the order of their start)
            lagged = True
            order = sorted(range(len(xs)), key=lambda i: times[i])
            before, last = {}, {}
            for i in order:
                if persons[i] is None:
                    continue
                before[i] = last.get(persons[i], np.nan)
                last[persons[i]] = xs[i] if isinstance(xs[i], float) else np.nan
            raw = np.array([before.get(i, np.nan) for i in y_rows], dtype=float)
        else:
            raw = np.array([xs[i] if isinstance(xs[i], float) else np.nan for i in y_rows], dtype=float)
        # the person's mean over all their values of what explains (also those without the outcome)
        means = {}
        for i in x_rows:
            means.setdefault(persons[i], []).append(xs[i])
        xb = np.array([statistics.fmean(means[g]) if g in means else np.nan for g in group], dtype=float)
        xw = raw - xb
        X = np.column_stack([np.ones(len(y)), xw, xb])
        names = ["(Intercept)", "within", "between"]
        Z = np.column_stack([np.ones(len(y)), xw]) if settings["slope"] else None
    # the rows the model is fitted on (mixed.fit drops those without what explains, its mean or the previous entry):
    # the minimum holds for them, not for all rows with the outcome (audit storage and calculations 2026-10-28, M6)
    fitted = np.isfinite(y) & np.all(np.isfinite(X), axis=1) & (np.all(np.isfinite(Z), axis=1) if Z is not None else True)
    per = Counter(group[fitted].tolist())
    if len(per) < MIN_PERSONS or statistics.median(per.values()) < MIN_MEDIAN:
        out["notes"].append(de_en("Für das Mehrebenenmodell zu wenige Daten: {k} Personen mit im Median {m} Einträgen "
                                  "mit allen Werten des Modells (nötig: {kk} Personen, im Median {mm}; Festlegung von "
                                  "Askurious).",
                                  "Too few data for the multilevel model: {k} people with a median of {m} entries "
                                  "with every value of the model (needed: {kk} people, a median of {mm}; set by "
                                  "Askurious).", k=len(per),
                                  m=_num(statistics.median(per.values()), lang, 0) if per else 0,
                                  kk=MIN_PERSONS, mm=MIN_MEDIAN))
        return True
    try:
        res = mixed.fit(y, X, group, names, Z=Z)
    except (mixed.MixedError, np.linalg.LinAlgError, ValueError, ArithmeticError):
        out["notes"].append(de_en("Das Mehrebenenmodell ließ sich nicht schätzen.", "The multilevel model could not be estimated."))
        return True
    key = "x" if x_once else "within"
    eff = next(f for f in res["fixed"] if f["name"] == key)
    out.update(n=res["groups"], p=eff["p"], effect=("b", eff["est"]), verdict=_verdict(eff["p"], eff["est"], out["direction"]))
    head = (de_en("innerhalb", "within") + " ") if not x_once else ""
    sentence = (f"{head}b = {_num(eff['est'], lang)}, t({_df(eff['df'], lang)}) = {_num(eff['t'], lang)}, "
                f"{_p(eff['p'], lang)}")
    if not x_once:
        btw = next(f for f in res["fixed"] if f["name"] == "between")
        sentence += (f"; {de_en('zwischen', 'between')} b = {_num(btw['est'], lang)}, {_p(btw['p'], lang)}")
    out["sentence"] = sentence
    out["multilevel"] = {"persons": res["groups"], "entries": res["n"], "icc": _icc(y, group), "lagged": lagged,
                         "slope": Z is not None, "singular": res["singular"], "level2": x_once}
    out["notes"].append(de_en(
        "Mehrebenenmodell (REML) mit {k} Personen und {n} Einträgen, zufälliger Achsenabschnitt je Person{s}; "
        "{what}; p mit Freiheitsgraden nach Satterthwaite (1946). ICC(1) des Erklärten aus dem Nullmodell "
        "(REML, τ²/(τ² + σ²); der Verlauf der Tagebuchstudie schätzt ICC(1) nach Shrout und Fleiss aus der "
        "Varianzanalyse, daher kann er leicht abweichen): {icc}.",
        "Multilevel model (REML) with {k} people and {n} entries, a random intercept per person{s}; {what}; p with "
        "degrees of freedom after Satterthwaite (1946). ICC(1) of the outcome from the null model (REML, "
        "τ²/(τ² + σ²); the course of the diary survey estimates ICC(1) after Shrout and Fleiss from the analysis of "
        "variance, so it can differ slightly): {icc}.",
        k=res["groups"], n=res["n"],
        s=de_en(" und zufällige Steigung", " and a random slope") if Z is not None else "",
        what=(de_en("das Erklärende einmal je Person", "what explains once per person") if x_once else
              de_en("das Erklärende zerlegt in die Abweichung vom eigenen Mittel (innerhalb, beurteilt) und das "
                    "Personenmittel (zwischen)", "what explains split into the deviation from the person's own mean "
                    "(within, judged) and the person's mean (between)")),
        icc=_num(out["multilevel"]["icc"], lang) if out["multilevel"]["icc"] is not None else "–"))
    out["code"] = r_code(survey, h, lagged, Z is not None, x_once, lang)
    if lagged:
        out["notes"].append(de_en("Zeitverzögert: das Erklärende aus dem vorigen Eintrag derselben Person.",
                                  "Lagged: what explains from the same person's entry before."))
    if res["singular"]:
        out["notes"].append(de_en("Eine Varianz der Zufallseffekte liegt bei null (singuläre Lösung); die zufällige "
                                  "Steigung lässt sich mit diesen Daten nicht schätzen.",
                                  "A variance of the random effects is at zero (singular fit); the random slope cannot "
                                  "be estimated with these data."))
    return True


def _columns(survey, code):
    """(R lines that make the variable's value, its column name) in the export of the diary study."""
    from app import scales, study_plan

    thing = study_plan._item(survey, code)
    if thing is not None and not isinstance(thing, dict) and thing.type == "likert":
        cols = scales.scored_codes(thing)
        return [f"d${code} <- rowMeans(d[, c(" + ", ".join(f'"{c}"' for c in cols) + ")])"], code
    return [], code


def r_code(survey, h, lagged, slope, level2, lang="de"):
    """The model as R code (lme4 and lmerTest) on the data export of the diary study (E14)."""
    en = lang == "en"
    x_lines, x = _columns(survey, h.get("iv"))
    y_lines, y = _columns(survey, h.get("dv"))
    lines = ["# " + ("data: tab Export, data set (CSV), one row per entry, column person" if en else
                     "Daten: Reiter Export, Datensatz (CSV), eine Zeile je Eintrag, Spalte person"),
             "library(lmerTest)", 'd <- read.csv("tagebuch.csv")', *x_lines, *y_lines]
    if level2:
        lines += ["# " + ("the value asked once per person, carried to each of their entries" if en else
                          "der einmal je Person erfragte Wert, auf jeden ihrer Einträge übertragen"),
                  f"d${x} <- ave(d${x}, d$person, FUN = function(v) mean(v, na.rm = TRUE))",
                  f"fit <- lmer({y} ~ {x} + (1 | person), data = d)"]
    else:
        source = x
        if lagged:
            lines += ["d <- d[order(d$person, d$started), ]",
                      f"d${x}_vorher <- ave(d${x}, d$person, FUN = function(v) c(NA, head(v, -1)))"]
            source = f"{x}_vorher"
        lines += [f"d${x}_zwischen <- ave(d${x}, d$person, FUN = function(v) mean(v, na.rm = TRUE))",
                  f"d${x}_innerhalb <- d${source} - d${x}_zwischen",
                  f"fit <- lmer({y} ~ {x}_innerhalb + {x}_zwischen + "
                  + (f"(1 + {x}_innerhalb | person)" if slope else "(1 | person)") + ", data = d)"]
    lines.append("summary(fit)  # " + ("Satterthwaite df" if en else "Freiheitsgrade nach Satterthwaite"))
    return "\n".join(lines) + "\n"


def _icc(y, group):
    try:
        value = mixed.icc(y, group)
    except (mixed.MixedError, np.linalg.LinAlgError, ValueError, ArithmeticError):
        return None
    return value if math.isfinite(value) else None


def sources():
    return list(mixed.SOURCES)
