"""Results per hypothesis of the study plan (app/study_plan.py; concept "Askurious für Studierende und
Berufseinsteiger", package E): the planned test on the data pool of the results page, the effect size, a check of
what the test assumes, a verdict and the sentence for the report in APA style (German, decimal comma).

What explains (`iv`) and what is explained (`dv`) are read from the export rows (compare.table_of): an element's own
column (a Likert scale with several statements: its mean score, scales.row_score), a randomization's column (the
condition shown). The tests are those of the group comparisons (app/group_tests.py) and the correlation of the
scales (scales.pearson; Spearman = Pearson on ranks). Verdict (S4): "unterstützt" when p < .05 and the direction
fits, "Gegenteil" when it is significant the other way, else "nicht unterstützt", which is no evidence for the
opposite. A hypothesis added after the start is marked explorative. All tests are two-sided at ALPHA, without a
correction for several hypotheses (the methods draft says so, audit 2026-10-05, I-9).

Groups with fewer than MIN_GROUP answers take no part in the test and are named in the notes; a planned t-test of
two large groups stays one when further small groups have answers (I-4). Where Cochran's rule warns, the verdict and
the sentence take the exact p (Fisher's exact test for 2 x 2, a Monte Carlo permutation test for larger tables, I-3).
Whenever no test comes about, a note says why.
"""
from app import group_tests as gt
from app import scales, study_plan, units
from app.compare import MIN_GROUP
from app.items.base import plain
from app.lang import de_en, english
from markupsafe import Markup, escape

ALPHA = 0.05
VERSION = 6  # of what a result holds; the results page keeps them under it (2: with "sensitivity", PA2; 3: "why", W14;
# 4: ordinal answers in the order of their options, "median_text", audit 2026-10-12, S-2; 5: no test in a diary study, C4)
SMALL_GROUP = 30  # below this per group the normal approximation of a t-test is a matter of trust: the robust test too
PERFECT = 0.9999  # |r| or ω² above this: shown as "Zuordnung prüfen", not as a result (UI/UX audit 2026-10-05, V2)


# the only markup a note may carry; everything else in a note (option texts among it) is shown as text (audit
# 2026-10-09, X1: a note went out raw whenever it held "<sub>Holm</sub>", and an option text could put it there)
_NOTE_MARKS = ("<sub>Holm</sub>", "<sub>s</sub>")


def note_html(note):
    """A note of a result for the page: escaped, then only the subscripts of _NOTE_MARKS become HTML again."""
    out = str(escape(note or ""))
    for mark in _NOTE_MARKS:
        out = out.replace(str(escape(mark)), mark)
    return Markup(out)


# What the sentences of a result may carry as HTML: italics and sub- and superscripts of the statistics. A sentence
# can quote an answer label or, without a label, the answer itself (a descriptive assumption names the most frequent
# one): that came onto the results page as raw HTML (audit 2026-10-22, C-01).
_SENTENCE_MARKS = ("<i>", "</i>", "<sub>", "</sub>", "<sup>", "</sup>")


def sentence_html(sentence):
    """A result's sentence for the page: escaped, then only the tags of _SENTENCE_MARKS become HTML again."""
    out = str(escape(sentence or ""))
    for mark in _SENTENCE_MARKS:
        out = out.replace(str(escape(mark)), mark)
    return Markup(out)


def _median(values):
    if not values:
        return None
    xs, m = sorted(values), len(values) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _check_link(effect):
    return de_en("Zuordnung prüfen: {effect} heißt, beide Variablen messen dasselbe. Meist ist im Studienplan "
                 "eine falsche Frage gewählt; deshalb hier kein Urteil und kein Satz für den Bericht.",
                 "Check the assignment: {effect} means that both variables measure the same. "
                 "Usually a wrong question was chosen in the research plan; hence no verdict and no sentence for the "
                 "report here.", effect=effect)


def _vars(survey, h):
    """[(role, variable name, label or None, has a section on the results page)] of what a hypothesis tests, for the
    line „geprüft: …“ (V2); the label is None when the variable is not (or no longer) in the questionnaire."""
    out = []
    for key in ("iv", "dv"):
        code = h.get(key)
        if not code:
            continue
        thing = study_plan._item(survey, code)
        if thing is None:
            out.append((key, code, None, False))
        elif isinstance(thing, dict):
            out.append((key, code, de_en("Randomisierung „{name}“", "randomisation “{name}”",
                                         name=thing.get('name') or code), False))
        else:  # the name of the construct as in the study plan, not the shared instruction (UI/UX audit 2026-10-09, X9)
            text = study_plan.construct_name(thing) or " ".join(
                plain(thing.text("question", survey.default_language) or "").split())
            out.append((key, code, (text[:58].rstrip() + " …") if len(text) > 60 else text, True))
    return out


def _de_lang(v, d=2, lang="de"):
    """v with d decimals, rounded half up (kaufmännisch: CFI 0,945 is 0,95, not 0,94 as "%.2f" wrote it; Mathe-Audit
    2026-10-06, Z-9), a decimal comma in German and a minus sign."""
    from app.units import decimal_text

    return "–" if v is None else decimal_text(v, d, lang=lang).replace("-", "−")


def _bounded_lang(v, d=2, lang="de"):
    """_de_lang of a value that cannot exceed 1 (r, V, ω², ε²): in English without the leading zero, as APA and
    manuscript_tables.fmt(bounded=True) write it; German keeps it (audit 2026-10-10, B-20)."""
    text = _de_lang(v, d, lang)
    return text.replace("0.", ".", 1) if lang == "en" else text


def _p_lang(p, lang="de"):
    """p in APA style: no leading zero, "< .001" below; German with decimal comma. A p just below .05 gets more
    decimals, so it does not read as ".050" beside "unterstützt" (audit 2026-10-05, I-7)."""
    if p is None:
        return "p = –"
    sep = "," if lang == "de" else "."
    if p < 0.001:
        return f"p < {sep}001"
    text = units.fixed(p, units.p_places(p, alpha=ALPHA))  # p = 1 (after Holm) keeps its "1": 1,000 (2026-10-17)
    return "p = " + (sep + text[2:] if text.startswith("0.") else text.replace(".", sep))


def _numeric(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _order(thing, code):
    """{answer key: (place, label)} of an ordered element whose answers have labels of their own (answer options,
    the points of a Likert scale), in the order the results page reads as the scale (results.ordinal_stats):
    the order of the options, whatever their codes (audit 2026-10-12, S-2: codes 1, 3, 2 after an option was put
    in between; S-5: letter codes). The codes that count as missing left out; None where there are no labels."""
    if thing is None or isinstance(thing, dict):
        return None
    try:
        _, _, labels = thing.kind.result_column(thing, code)
    except (AttributeError, KeyError, TypeError):
        return None
    if not labels:
        return None
    gone = {str(m) for m in thing.kind.missing_codes(thing, code)}
    keys = [(str(k), label) for k, label in labels.items() if str(k) not in gone]
    return {k: (i, plain(label or "") or k) for i, (k, label) in enumerate(keys)} if len(keys) >= 2 else None


def _key(v):
    return str(int(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() else str(v)


def multiple(survey, code):
    """Whether the variable is a multiple choice question: its answers are one 0/1 column per option, no single
    variable a test of the study plan could take (audit 2026-10-12, S-6)."""
    thing = study_plan._item(survey, code)
    return thing is not None and not isinstance(thing, dict) and thing.type == "multiple_choice"


def _values(survey, code, table):
    """[value per row] of a variable: numbers for metric elements, the place in the scale for ordinal ones (the
    order of their options, _order; the numbers of the codes where they have no labels), codes for nominal ones and
    for a randomization, None where there is no answer."""
    thing = study_plan._item(survey, code)
    if thing is None:
        return None, None
    level = study_plan._level(thing)
    if isinstance(thing, dict):
        return [str(r.get(code)) if r.get(code) not in (None, "") else None for r in table], "nominal"
    if thing.type in ("likert", "semantic") and level == "metric":
        subs = scales.subscale_groups(thing)
        if len(subs) > 1:
            return None, None  # no unrequested total across distinct constructs
        codes = subs[0][1] if subs else scales.scored_codes(thing)
        return [scales.row_score(thing, codes, r) for r in table], "metric"
    gone = {str(m) for m in thing.kind.missing_codes(thing, thing.code)}
    raw = [r.get(code) for r in table]
    if level == "nominal":
        return [str(v) if v not in (None, "") and str(v) not in gone else None for v in raw], "nominal"
    order = _order(thing, code) if level == "ordinal" else None
    if order:
        return [float(order[_key(v)][0]) if v not in (None, "") and _key(v) in order else None for v in raw], level
    return [_numeric(v) if v not in (None, "") and str(v) not in gone else None for v in raw], level


def _codes(survey, code, table):
    """[group code per row] of a grouping variable (an element's answer codes, a randomization's condition)."""
    thing = study_plan._item(survey, code)
    gone = set() if thing is None or isinstance(thing, dict) else {str(m) for m in thing.kind.missing_codes(thing, thing.code)}
    out = []
    for r in table:
        v = r.get(code)
        if v in (None, "") or str(v) in gone:
            out.append(None)
        else:
            out.append(str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
    return out


def _labels(survey, code):
    """{group code: label} of a grouping element; a randomization's groups are their own names, in the order of its
    conditions in the flow (audit 2026-10-12, S-4: before, alphabetical, and "A" was whichever came first)."""
    thing = study_plan._item(survey, code)
    if thing is None:
        return {}
    if isinstance(thing, dict):
        return {name: name for name in study_plan.conditions(survey, thing)}
    lang = survey.default_language
    try:
        return {str(o["code"]): plain(o["label"]) for o in thing.kind.options(thing, lang)}
    except (AttributeError, KeyError, TypeError):
        return {}


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _by_group(member, values):
    groups = {}
    for g, v in zip(member, values):
        if g is not None and v is not None:
            groups.setdefault(g, []).append(v)
    return groups


def _verdict(p, sign, direction):
    if p is None:
        return "offen"
    if p >= ALPHA:
        return "nicht unterstützt"
    if not direction or sign is None or sign == 0:
        return "unterstützt"
    return "unterstützt" if (sign > 0) == (direction == "pos") else "Gegenteil"


EXACT_NAME = {"de": {"fisher": "exakter Test nach Fisher", "monte_carlo": "Monte-Carlo-Permutationstest ({draws} Ziehungen)"},
              "en": {"fisher": "Fisher's exact test", "monte_carlo": "Monte Carlo permutation test ({draws} draws)"}}


# the p of Spearman's rho without ties (group_tests.spearman_p): exact distribution, above it a beta density fitted
# to the exact moments
SPEARMAN_SOURCES = [
    "David, F. N., Kendall, M. G., & Stuart, A. (1951). Some questions of distribution in the theory of rank "
    "correlation. Biometrika, 38(1/2), 131–140.",
    "Kendall, M. G., & Gibbons, J. D. (1990). Rank correlation methods (5th ed.). Edward Arnold.",
    "Pitman, E. J. G. (1937). Significance tests which may be applied to samples from any populations. II. The "
    "correlation coefficient test. Supplement to the Journal of the Royal Statistical Society, 4(2), 225–232."]


EXACT_FROM = {"fisher": "exakten Test nach Fisher (1934)",
              "monte_carlo": "Monte-Carlo-Permutationstest (Hope, 1968) mit festem Startwert"}
EXACT_FROM_EN = {"fisher": "Fisher's exact test (1934)",
                 "monte_carlo": "a Monte Carlo permutation test (Hope, 1968) with a fixed seed"}


def _exact_name(t, lang):
    names = EXACT_NAME.get(lang, EXACT_NAME["de"])
    return names[t["exact"]].format(draws=units.number(t.get("draws") or 0, 0, lang))


def _few_note(labels, sizes):
    """The note on the groups left out of the test for their size: „c“: 1 Person."""
    def one(label, n):
        if n == 1:
            return de_en("„{label}“: {n} Person", "“{label}”: {n} person", label=label, n=n)
        return de_en("„{label}“: {n} Personen", "“{label}”: {n} people", label=label, n=n)

    return (de_en("Zu kleine Gruppen (unter {k} Antworten), nicht im Test: ",
                  "Groups too small (under {k} answers), not in the test: ", k=MIN_GROUP)
            + ", ".join(one(label, n) for label, n in zip(labels, sizes)) + ".")


def evaluate_one(survey, h, table, lang="de"):
    """The result of one hypothesis (h of the plan) on the export rows `table`; the sentence in `lang`. With a test,
    "sensitivity" says what effect the test could find with these data (PA2, concept poweranalyse-konzept.md)."""
    out = _evaluate_one(survey, h, table, lang)
    if out.get("sizes") and out.get("p") is not None:
        out["sensitivity"] = sensitivity(survey, h, out, lang)
    return out


def sensitivity(survey, h, out, lang="de"):
    """What the test of a hypothesis could detect with the group sizes it had: {"measure", "detectable", "level",
    "planned", "planned_power", "enough", "text"}; None when the data are too few. The power aimed at in the plan
    (default 80 %); a sensitivity analysis (Lakens, 2022), not the "observed power" of the found effect, which only
    restates p (Hoenig & Heisey, 2001)."""
    from app import power

    a, sizes, df = out["tested"], out["sizes"], out.get("df", 1)
    level = h.get("power") if h.get("power") in study_plan.POWERS else 0.8
    m = power.measure(a)
    alpha = study_plan.alpha_for(survey)
    found = power.detectable(a, sizes, level, alpha, df)
    en = lang == "en"
    sign = "%" if en else " %"  # 80% in English, 80 % in German (units.affixes)
    pct = f"{round(level * 100)}{sign}"
    # Welch's test reports δ* (the average of both variances); its power is that of d, which is the same number when
    # the variances are equal: the sentence names δ* and says so, not d beside a δ* (audit 2026-10-10, B-24)
    sym = "δ*" if a == "t" else m

    def value(v, places=2):
        return _bounded_lang(v, places, lang) if m == "r" else _de_lang(v, places, lang)
    if isinstance(sizes, int):
        who = (f"n = {units.number(sizes, 0, lang)}")
    elif len(sizes) == 2:
        who = (f"{units.number(sizes[0], 0, lang)} {'and' if en else 'und'} {units.number(sizes[1], 0, lang)} "
               + ("people" if en else "Personen"))
    else:
        who = (f"n = {units.number(sum(sizes), 0, lang)} " + (f"in {len(sizes)} groups" if en else f"in {len(sizes)} Gruppen"))
    if found is None:
        return {"measure": m, "detectable": None, "level": level, "text":
                (f"With {who} the test reaches {pct} power for no effect in the computable range."
                 if en else f"Mit {who} erreicht der Test für keinen Effekt im rechenbaren Bereich {pct} Power.")}
    shown = f"{sym} = {value(found)}"
    same_d = ("; δ* equals d with equal variances" if en else "; δ* gleich d bei gleichen Varianzen") if a == "t" else ""
    text = (f"With {who} the test finds effects from {shown} with {pct} power (α = {study_plan.alpha_text(alpha, 'en')}, two-sided{same_d})."
            if en else f"Mit {who} findet der Test Effekte ab {shown} mit {pct} Power (α = {study_plan.alpha_text(alpha)} zweiseitig{same_d}).")
    res = {"measure": m, "detectable": found, "level": level, "planned": None, "planned_power": None, "enough": None}
    planned = h.get("effect")
    # the plan's effect counts where it is the same measure: a planned t-test that became an ANOVA (more than two
    # groups answered) has d in the plan and f here
    if planned is not None and power.measure(study_plan.power_of(survey, h)["analysis"]) == m and power.valid_effect(a, planned):
        at = power.power_at(a, planned, sizes, alpha, df)
        res.update(planned=planned, planned_power=at, enough=at is not None and at >= level - 1e-9)
        # as many decimals as the plan keeps (B-19)
        planned_text = f"{sym} = {value(planned, len(study_plan.effect_number(planned).split('.')[1]))}"
        if res["enough"]:
            text += (f" Planned was {planned_text}: the data were large enough for it."
                     if en else f" Geplant war {planned_text}: Dafür waren die Daten groß genug.")
        else:
            # never the aim it misses (Z-10): 0.797 against 80 % reads 79,7 % (one decimal, cut off), 0.598 60 %
            got = (at or 0) * 100
            got = (f"{units.number(got, 0, lang)}{sign}" if units.round_half_up(got) < round(level * 100)
                   else f"{units.number(int(got * 10) / 10, 1, lang)}{sign}")
            text += (f" Planned was {planned_text}: for it the test had only {got} power."
                     if en else f" Geplant war {planned_text}: Dafür hatte der Test nur {got} Power.")
            if out.get("verdict") == "nicht unterstützt":
                text += (" Read “not supported” with particular care here." if en
                         else " „Nicht unterstützt“ ist hier besonders vorsichtig zu lesen.")
    res["text"] = text
    return res


def _diary(survey):
    """A diary study (app/diary.py): its rows are entries, several of one person."""
    return getattr(survey, "sampling", None) is not None


def _diary_note():
    """Why a diary study gets no test of a hypothesis (code review 2026-10-15, C4): the tests take every row as an
    independent case, and 30 entries of one person are no 30 persons; their p values would be far too small."""
    return de_en("Tagebuchstudie: kein Test. Die Einträge einer Person sind nicht unabhängig voneinander; ein Test über "
                 "alle Einträge gäbe zu kleine p-Werte. Für eine Prüfung die exportierten Daten mit einem "
                 "Mehrebenenmodell auswerten (Einträge in Personen).",
                 "Diary survey: no test. The entries of one person are not independent of each other; a test over all "
                 "entries would give p values that are too small. To test, analyse the exported data with a multilevel model "
                 "(entries within people).")


def _describe(survey, h, table, out, lang="de"):
    """A descriptive assumption (study_plan kind "descriptive"): the value of its one variable, without a test. A
    number or a scale: M, SD, median and n; answers with options: the most frequent one with its share (and the
    median answer of an ordered one). Elements without one column (Conjoint, MaxDiff, ranking …) are described in
    their own section of the results page: part-worths, importances, ranks."""
    out.update(analysis="describe", why="")
    code = h.get("dv")
    thing = study_plan._item(survey, code) if survey is not None else None
    if thing is None:
        out["notes"].append(de_en("Gemessene Größe fehlt im Studienplan oder im Fragebogen.",
                                  "The measured variable is missing in the research plan or in the questionnaire."))
        return out
    out["verdict"] = "beschrieben"
    no_test = de_en("Beschreibende Annahme: kein Signifikanztest. Ob der Wert die Annahme trägt, lesen Sie an der Zahl ab.",
                    "Descriptive assumption: no significance test. Whether the value bears out the assumption is read "
                    "from the number.")
    level = study_plan._level(thing)
    values, _ = _values(survey, code, table) if level else (None, None)
    if level == "metric" and values is not None:
        xs = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if xs:
            import statistics

            sd = statistics.stdev(xs) if len(xs) > 1 else None
            out.update(n=len(xs), sentence=f"M = {_de_lang(statistics.fmean(xs), 2, lang)}, SD = {_de_lang(sd, 2, lang)}, "
                                           f"Mdn = {_de_lang(_median(xs), 2, lang)}")
    elif level in ("nominal", "ordinal"):
        codes = [c for c in _codes(survey, code, table) if c is not None]
        if codes:
            labels = _labels(survey, code)
            counts = {}
            for c in codes:
                counts[c] = counts.get(c, 0) + 1
            top = max(counts, key=lambda c: counts[c])
            share = units.percent_text(counts[top] / len(codes), lang)
            en = lang == "en"  # the sentence in the language asked for, as the tests' sentences
            sentence = (("most frequent “{label}”: {share} ({k} of {n})" if en else "am häufigsten „{label}“: {share} ({k} von {n})")
                        .format(label=labels.get(top, top), share=share, k=units.number(counts[top], 0, lang),
                                n=units.number(len(codes), 0, lang)))
            order = _order(thing, code) if level == "ordinal" else None
            if order:
                places = sorted(order[_key(c)][0] for c in codes if _key(c) in order)
                by_place = {place: label for place, label in order.values()}
                if places:
                    sentence += ("; median “{label}”" if en else "; Median „{label}“").format(
                        label=by_place.get(places[(len(places) - 1) // 2], ""))
            out.update(n=len(codes), sentence=sentence)
    if not out.get("sentence"):
        no_test = de_en("Beschreibende Annahme: kein Signifikanztest. Den Wert zeigen die Ergebnisse der Frage „{code}“ "
                        "(bei Conjoint und MaxDiff Teilnutzen, Wichtigkeit und Rangfolge).",
                        "Descriptive assumption: no significance test. The results of the question “{code}” show the "
                        "value (for conjoint and MaxDiff part-worths, importance and ranking).", code=code)
    out["notes"].append(no_test)
    return out


def _evaluate_one(survey, h, table, lang="de"):
    def _de(v, d=2):
        return _de_lang(v, d, lang)

    def _p(p):
        return _p_lang(p, lang)

    def _b(v):  # r, V, ω², ε²: no leading zero in English (B-20)
        return _bounded_lang(v, 2, lang)

    out = {"text": h["text"], "kind": h.get("kind"), "direction": h.get("direction", ""),
           "explorative": bool(h.get("after_start")), "analysis": h.get("analysis") or study_plan.suggest(survey, h),
           "verdict": "offen", "sentence": "", "notes": [], "groups": [],
           "vars": _vars(survey, h) if survey is not None else [],
           "why": study_plan.test_reason(survey, h) if survey is not None else ""}
    if h.get("kind") == "open":
        out["notes"].append(de_en("Offene Forschungsfrage: kein Test, die Ergebnisse je Frage zeigen, was herauskam.",
                                  "Open research question: no test; the results per question show what came out."))
        return out
    if h.get("kind") == "descriptive":  # one value, share or ranking: reported, not tested (pre-test 2026-10-20, 6)
        return _describe(survey, h, table, out, lang)
    if study_plan.same(h):  # a question tested against itself (UI/UX audit 2026-10-05, V1)
        out["analysis"] = ""
        named = study_plan.roles(h.get("kind"))  # the names by the kind of hypothesis (UI/UX audit 2026-10-09, X4)
        out["notes"].append(de_en("Zuordnung prüfen: {iv} und {dv} sind dieselbe Frage („{code}“), ein Test "
                                  "ist so nicht möglich. Bitte im Studienplan zwei verschiedene Fragen wählen.",
                                  "Check the assignment: {iv} and {dv} are the same question "
                                  "(“{code}”), so no test is possible. Please choose two different questions in the "
                                  "research plan.", code=h['iv'], iv=named["iv"], dv=named["dv"]))
        return out
    if survey is not None and any(
            (thing := study_plan._item(survey, h.get(role))) is not None
            and not isinstance(thing, dict) and len(scales.subscale_groups(thing)) > 1
            for role in ("iv", "dv")):
        out.update(analysis="", why="", blocked="subscales")
        out["notes"].append(de_en(
            "Mehrere Subskalen: Ohne eindeutige Subskalenauswahl ist kein Test möglich. "
            "Die Ergebnisse je Subskala stehen bei der Frage.",
            "Multiple subscales: a test requires an explicit subscale selection. "
            "Results for each subscale are shown with the question."))
        return out
    if out["analysis"] == "panel":  # tested in the cross-lagged panel model over the waves (app/panel_models.apply)
        out["notes"].append(de_en("Geprüft im Block „Längsschnitt über die Wellen“.",
                                  "Tested in the block “Longitudinal analysis over the waves”."))
        return out
    if out["analysis"] == "sem" and _diary(survey):
        # a diary study has no structural equation model over its entries (they are no independent cases): a directed
        # path between two variables is the multilevel model of the entries (app/diary_model.py), the rest in R
        if h.get("kind") == "effect":
            from app import diary_model

            xs, _ = _values(survey, h.get("iv"), table)
            ys, _ = _values(survey, h.get("dv"), table)
            if xs is not None and ys is not None and diary_model.evaluate(survey, h, table, xs, ys, out, lang):
                return out
        out["notes"].append(de_en("Tagebuchstudie: Vermittlung und Moderation über Einträge in Personen rechnet das "
                                  "R-Skript (Reiter „Export“) als Zweiebenen-Modell.",
                                  "Diary survey: mediation and moderation over entries within people are computed by "
                                  "the R script (tab “Export”) as a two-level model."))
        return out
    if out["analysis"] == "sem":  # tested together in the structural equation model (app/sem_model.apply)
        out["notes"].append(de_en("Geprüft im Strukturgleichungsmodell (experimentell).",
                                  "Tested in the structural equation model (experimental)."))
        return out
    many = study_plan.multiple_note(survey, h) if survey is not None else ""
    if many:  # a multiple choice question: no single variable (audit 2026-10-12, S-6)
        out.update(analysis="", why="")
        out["notes"].append(many)
        return out
    xs, xl = _values(survey, h.get("iv"), table)
    ys, yl = _values(survey, h.get("dv"), table)
    if xs is None or ys is None:
        named = study_plan.roles(h.get("kind"))
        out["notes"].append(de_en("{iv} oder {dv} fehlt im Studienplan oder im Fragebogen.",
                                  "{iv} or {dv} is missing in the research plan or in the questionnaire.",
                                  iv=named["iv"], dv=named["dv"]))
        return out
    a = out["analysis"]
    if a in ("r", "rho") and _diary(survey):
        # entries within persons: the multilevel model (app/diary_model.py, concept 2026-10-17, E8)
        from app import diary_model

        if diary_model.evaluate(survey, h, table, xs, ys, out, lang):
            return out
        out["notes"].append(_diary_note())
        return out
    if a in ("r", "rho"):
        pairs = [(x, y) for x, y in zip(xs, ys) if isinstance(x, float) and isinstance(y, float)]
        px, py = [x for x, _ in pairs], [y for _, y in pairs]
        if a == "rho":
            px, py = _ranks(px), _ranks(py)
        r, n, p = scales.pearson(px, py)
        exact = False
        if a == "rho" and r is not None and len(set(px)) == n and len(set(py)) == n:
            # without ties from the null distribution of S (audit 2026-10-12, S-8; group_tests.spearman_p); with
            # ties t(n − 2)
            p, exact = gt.spearman_p(r, n), True
        if r is None:
            out["notes"].append(de_en("Kein Test möglich: zu wenige Wertepaare oder keine Streuung.",
                                      "No test possible: too few pairs of values or no variation."))
        elif abs(r) > PERFECT:  # two columns that say the same: almost always a wrong link, not a finding (V2)
            out.update(n=n)
            out["notes"].append(_check_link(("r" if a == "r" else "r<sub>s</sub>") + f" = {_de(r)}"))
            return out
        out.update(n=n, p=p, effect=("r" if a == "r" else "ρ", r), verdict=_verdict(p, r, out["direction"]))
        if r is not None:
            out.update(tested=a, sizes=n)
            sym = "r" if a == "r" else "r<sub>s</sub>"
            out["sentence"] = f"{sym}({n - 2}) = {_b(r)}, {_p(p)}"
        if a == "rho" and r is not None:
            out["rank_p"] = exact
            out["notes"].append(de_en("p ohne Bindungen aus der exakten Verteilung (bis {k} Paare), darüber aus einer "
                                      "Beta-Näherung, angeglichen an die exakten Momente bis zum sechsten (Pitman, 1937; "
                                      "David, Kendall & Stuart, 1951).",
                                      "p without ties from the exact distribution (up to {k} pairs), above that from a "
                                      "beta approximation fitted to the exact moments up to the sixth (Pitman, 1937; "
                                      "David, Kendall & Stuart, 1951).", k=gt.SPEARMAN_EXACT)
                                if exact else
                                de_en("p aus der t-Verteilung mit n − 2 Freiheitsgraden (Näherung, da Bindungen).",
                                      "p from the t distribution with n − 2 degrees of freedom (approximation, as there "
                                      "are ties)."))
        if a == "r":
            out["notes"].append(de_en("Angenommen ist ein linearer Zusammenhang; bei Ausreißern oder Rangdaten ist die "
                                      "Rangkorrelation (Spearman) robuster.",
                                      "A linear relation is assumed; with outliers or rank data the rank correlation "
                                      "(Spearman) is more robust."))
        return out
    if xl == "metric":
        out["notes"].append(de_en("Für einen Gruppenvergleich muss die erste Variable Gruppen bilden (Auswahl oder "
                                  "Randomisierung).", "For a group comparison the first variable must form groups (a "
                                  "choice or a randomisation)."))
        return out
    xs = _codes(survey, h.get("iv"), table)
    labels = _labels(survey, h.get("iv"))
    keep = set(study_plan._keep(h))
    if keep:  # "nur diese Gruppen vergleichen" in the study plan (UI/UX audit 2026-10-07, W1)
        xs = [x if x in keep else None for x in xs]
        out["notes"].append(de_en("Verglichen nur: {groups} (im Studienplan gewählt).",
                                  "Only compared: {groups} (chosen in the research plan).",
                                  groups=", ".join(labels.get(k, k).rstrip(":") for k in labels if k in keep)))
    groups = _by_group(xs, ys)
    keys = [k for k in labels if k in groups] + sorted(k for k in groups if k not in labels)
    # rank tests compare distributions: their groups show the median, the others the mean (V9)
    out["groups"] = [{"code": k, "label": labels.get(k, k), "n": len(groups[k]),
                      "mean": gt.describe(groups[k])["mean"] if yl != "nominal" else None,
                      "median": _median([v for v in groups[k] if isinstance(v, float)]) if yl != "nominal" else None}
                     for k in keys]
    order = _order(study_plan._item(survey, h.get("dv")), h.get("dv")) if yl == "ordinal" and survey is not None else None
    if order:  # the median as the answer (its label), not as the place in the scale (S-2); between two answers both
        names = [label for _, label in sorted(order.values())]
        for g in out["groups"]:
            xs_ = sorted(int(v) for v in groups[g["code"]] if isinstance(v, float))
            if xs_:
                lo, hi = xs_[(len(xs_) - 1) // 2], xs_[len(xs_) // 2]
                g["median_text"] = names[lo] if lo == hi else f"{names[lo]}–{names[hi]}"
    out["center"] = "Md" if a in ("u", "kw") else "M"
    # one number of decimals for the line: none while every median is whole (answer codes), else one
    out["median_places"] = 0 if all(g["median"] is None or float(g["median"]).is_integer() for g in out["groups"]) else 1
    if a not in ("t", "anova", "u", "kw", "chi2"):
        out["notes"].append(de_en("Nur beschrieben, ohne Test.", "Only described, without a test."))
        return out
    if _diary(survey):
        out["notes"].append(_diary_note())
        return out
    if a != "chi2":
        groups = {k: [v for v in groups[k] if isinstance(v, float)] for k in keys}
    few = [k for k in keys if len(groups[k]) < MIN_GROUP]
    if few:  # a single "divers" or "keine Angabe" does not stop the planned test (audit 2026-10-05, I-4)
        out["notes"].append(_few_note([labels.get(k, k) for k in few], [len(groups[k]) for k in few]))
        keys = [k for k in keys if k not in few]
    if len(keys) < 2:
        out["notes"].append(de_en("Weniger als zwei Gruppen mit mindestens {k} Antworten: kein Test.",
                                  "Fewer than two groups with at least {k} answers: no test.", k=MIN_GROUP))
        return out
    if a == "chi2":
        cats = sorted({v for k in keys for v in groups[k]})
        rows = [[sum(1 for v in groups[k] if v == c) for k in keys] for c in cats]
        t = gt.chi2_test(rows) if len(cats) >= 2 else None
        if t:
            exact = t.get("cochran") and t.get("exact_p") is not None
            p = t["exact_p"] if exact else t["p"]
            out.update(n=t["n"], p=p, effect=("V", t["v"]), verdict=_verdict(p, None, ""), exact=t["exact"] if exact else None,
                       tested="chi2", sizes=t["n"], df=t["df"])
            out["sentence"] = f"χ²({t['df']}, N = {t['n']}) = {_de(t['chi2'])}, {_p(t['p'])}, V = {_b(t['v'])}"
            if exact:
                out["sentence"] = (f"χ²({t['df']}, N = {t['n']}) = {_de(t['chi2'])}, V = {_b(t['v'])}; "
                                   f"{_exact_name(t, lang)}, {_p(p)}")
                out["notes"].append(de_en("Viele erwartete Häufigkeiten unter 5: Das p des χ²-Tests ist dann nur eingeschränkt "
                                          "verlässlich, Urteil und p stammen aus dem {test}.",
                                          "Many expected frequencies under 5: the p of the χ² test is then of limited "
                                          "reliability; verdict and p come from {test}.",
                                          test=(EXACT_FROM_EN if english() else EXACT_FROM)[t['exact']]))
        else:
            out["notes"].append(de_en("Kein Test möglich: In den Gruppen kommt nur eine Antwort vor.",
                                      "No test possible: only one answer occurs in the groups."))
        return out
    numeric = groups
    small = [labels.get(k, k) for k in keys if len(numeric[k]) < SMALL_GROUP]
    if a in ("t", "u") and len(keys) > 2:
        a = "anova" if a == "t" else "kw"
    t = None
    if a == "t":
        x, y = numeric[keys[0]], numeric[keys[1]]
        t = gt.welch_t(x, y)
        if t:
            sign = (sum(x) / len(x) - sum(y) / len(y)) if x and y else None
            out.update(n=t["n"], p=t["p"], effect=("δ*", t["d"]), verdict=_verdict(t["p"], sign, out["direction"]),
                       tested="t", sizes=[len(x), len(y)])
            out["sentence"] = f"t({_de(t['df'], 1)}) = {_de(t['t'])}, {_p(t['p'])}, δ* = {_de(t['d'])}"
            out["notes"].append(de_en("Welch-Test: Verlangt keine gleichen Varianzen. „A“ ist „{a}“.",
                                      "Welch's test: does not require equal variances. “A” is “{a}”.",
                                      a=labels.get(keys[0], keys[0])))
            if small:
                u = gt.mann_whitney(x, y)
                if u:
                    out["notes"].append(de_en("Kleine Gruppen ({groups}: unter {k}): robust geprüft mit "
                                              "Mann-Whitney-U, {p}, r = {r}.",
                                              "Small groups ({groups}: under {k}): checked robustly with "
                                              "Mann-Whitney U, {p}, r = {r}.",
                                              groups=', '.join(small), k=SMALL_GROUP, p=_p(u['p']), r=_b(u['r'])))
    elif a == "anova":
        t = gt.welch_anova([numeric[k] for k in keys])
        if t and t.get("omega2") is not None and t["omega2"] > PERFECT:
            out.update(n=t["n"])
            out["notes"].append(_check_link(f"ω² = {_de(t['omega2'])}"))
            return out
        if t:
            out.update(n=t["n"], p=t["p"], effect=("ω²", t["omega2"]), verdict=_verdict(t["p"], None, ""),
                       tested="anova", sizes=[len(numeric[k]) for k in keys])
            out["sentence"] = f"F({t['df1']}, {_de(t['df2'], 1)}) = {_de(t['f'])}, {_p(t['p'])}, ω² = {_b(t['omega2'])}"
            if small:
                k = gt.kruskal_wallis([numeric[c] for c in keys])
                if k:
                    out["notes"].append(de_en("Kleine Gruppen: robust geprüft mit Kruskal-Wallis, {p}.",
                                              "Small groups: checked robustly with Kruskal-Wallis, {p}.", p=_p(k['p'])))
    elif a == "u":
        x, y = numeric[keys[0]], numeric[keys[1]]
        t = gt.mann_whitney(x, y)
        if t:
            out.update(n=t["n"], p=t["p"], effect=("r", t["r"]), verdict=_verdict(t["p"], t["r"], out["direction"]),
                       tested="u", sizes=[len(x), len(y)])
            # the exact p has no z: "exakter Test", as on the page (audit 2026-10-05, I-6)
            how = ("exakter Test" if lang == "de" else "exact test") if t.get("exact") else f"z = {_de(t['z'])}"
            out["sentence"] = f"U = {_de(t['u'], 1)}, {how}, {_p(t['p'])}, r = {_b(t['r'])}"
    else:  # "kw"
        t = gt.kruskal_wallis([numeric[k] for k in keys])
        if t:
            out.update(n=t["n"], p=t["p"], effect=("ε²", t["e2"]), verdict=_verdict(t["p"], None, ""),
                       tested="kw", sizes=[len(numeric[k]) for k in keys])
            out["sentence"] = f"H({t['df']}) = {_de(t['h'])}, {_p(t['p'])}, ε² = {_b(t['e2'])}"
    if not t:
        out["notes"].append(de_en("Kein Test möglich: In den Gruppen streuen die Werte nicht.",
                                  "No test possible: the values do not vary in the groups."))
    return out


def sources(hyps):
    """[(heading, [citations])] of the exact tests the hypotheses used, for the one fold of the results page."""
    from app import compare

    used = {h.get("exact") for h in hyps or [] if h.get("exact")}
    out = []
    if "fisher" in used:
        out.append((de_en("Ergebnisse je Hypothese: exakter Test nach Fisher bei kleinen erwarteten Häufigkeiten",
                          "Results per hypothesis: Fisher's exact test with small expected frequencies"),
                    [compare.FISHER, compare.AGRESTI]))
    if "monte_carlo" in used:
        out.append((de_en("Ergebnisse je Hypothese: Monte-Carlo-Permutationstest bei kleinen erwarteten Häufigkeiten",
                          "Results per hypothesis: Monte Carlo permutation test with small expected frequencies"),
                    [compare.HOPE, compare.AGRESTI]))
    if any(h.get("rank_p") for h in hyps or []):
        out.append((de_en("Ergebnisse je Hypothese: p der Rangkorrelation nach Spearman ohne Bindungen",
                          "Results per hypothesis: p of Spearman's rank correlation without ties"), SPEARMAN_SOURCES))
    return out


def evaluate(survey, table, lang="de", correct=True):
    """[result per hypothesis] in the order of the plan; with the correction for several hypotheses of the plan
    (PA5) the verdicts follow Holm's adjusted p. correct=False leaves the correction to the caller (with_holm()): the
    results page first fills in the hypotheses of the structural equation model (app/sem_model.apply)."""
    found = [evaluate_one(survey, h, table, lang) for h in study_plan.plan_of(survey)["hypotheses"]]
    if correct:
        with_holm(survey, found, lang)
    return found


def with_holm(survey, found, lang="de"):
    """Holm's correction of the results, if the plan asks for it."""
    if study_plan.plan_of(survey).get("holm"):
        holm(found, len(study_plan.tested(survey)), lang)
    return found


def holm(found, k, lang="de"):
    """Holm's (1979) step-down procedure over the k tested hypotheses: the i-th smallest p times (k − i + 1), never
    smaller than the one before, at most 1; a verdict stands only with an adjusted p below ALPHA. Hypotheses without
    a test result count in k with p = 1 (their test is planned)."""
    tested = sorted((r for r in found if r.get("p") is not None and r.get("kind") not in study_plan.UNTESTED), key=lambda r: r["p"])
    k = max(k, len(tested))
    if k < 2:
        return
    last = 0.0
    for i, r in enumerate(tested):
        adjusted = min(1.0, max(last, (k - i) * r["p"]))
        last = adjusted
        r["p_holm"] = adjusted
        if adjusted >= ALPHA and r["verdict"] in ("unterstützt", "Gegenteil"):
            r["verdict"] = "nicht unterstützt"
        # shown beside the sentence, the correction itself once under all hypotheses (Ralf, 2026-10-17)
        r["p_holm_text"] = _p_lang(adjusted, lang).replace("p ", "p<sub>Holm</sub> ", 1)
