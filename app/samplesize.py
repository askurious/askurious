"""Sample size calculator (/sample-size/): computed live in the browser (app/static/samplesize.js).

Planners: tests (two groups with any size ratio, two proportions, one-way and two-way ANOVA, chi-square,
equivalence, paired, repeated measures, correlation, regression; exact power over the noncentral t, F or chi-square
distribution where there is one, Cohen, 1988),
estimates (proportion with the Wilson interval, mean, both with finite population), coefficient alpha (Bonett)
and the rules of thumb of conjoint, MaxDiff and vignette decks. For every planner a design effect (clusters,
weights) and a subgroup share; tests and estimates also the other way round (smallest effect for a given n).
Opened from a survey (?survey=<id>), conjoint and MaxDiff values are taken from its items and "Als Ziel
übernehmen" returns the number to the survey's start form (not saved)."""
from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.conjoint_design import SOURCES as CONJOINT_SOURCES  # Orme (2010); Johnson & Orme (1996)
from app.extensions import db
from app.lang import de_en, english
from app.maxdiff_design import SETS_SOURCES as MAXDIFF_SOURCES
from app.paired_analysis import BRADLEY_TERRY
from app.power_choice import DE_BEKKER_GROB
from app.models import Survey

bp = Blueprint("samplesize", __name__, url_prefix="/sample-size")

COHEN = ("Cohen, J. (1988). Statistical power analysis for the behavioral sciences (2nd ed.). Lawrence Erlbaum "
         "Associates.")
FAUL_2007 = ("Faul, F., Erdfelder, E., Lang, A.-G., & Buchner, A. (2007). G*Power 3: A flexible statistical power "
             "analysis program for the social, behavioral, and biomedical sciences. Behavior Research Methods, 39(2), "
             "175–191. https://doi.org/10.3758/BF03193146")
FAUL_2009 = ("Faul, F., Erdfelder, E., Buchner, A., & Lang, A.-G. (2009). Statistical power analyses using G*Power 3.1: "
             "Tests for correlation and regression analyses. Behavior Research Methods, 41(4), 1149–1160. "
             "https://doi.org/10.3758/BRM.41.4.1149")
LAKENS = ("Lakens, D. (2013). Calculating and reporting effect sizes to facilitate cumulative science: A practical "
          "primer for t-tests and ANOVAs. Frontiers in Psychology, 4, 863. https://doi.org/10.3389/fpsyg.2013.00863")
COCHRAN = "Cochran, W. G. (1977). Sampling techniques (3rd ed.). Wiley."
FLEISS = ("Fleiss, J. L., Levin, B., & Paik, M. C. (2003). Statistical methods for rates and proportions (3rd ed.). "
          "Wiley. https://doi.org/10.1002/0471445428")
SCHUIRMANN = ("Schuirmann, D. J. (1987). A comparison of the two one-sided tests procedure and the power approach for "
              "assessing the equivalence of average bioavailability. Journal of Pharmacokinetics and Biopharmaceutics, "
              "15(6), 657–680. https://doi.org/10.1007/BF01068419")
LAKENS_2017 = ("Lakens, D. (2017). Equivalence tests: A practical primer for t tests, correlations, and meta-analyses. "
               "Social Psychological and Personality Science, 8(4), 355–362. https://doi.org/10.1177/1948550617697177")
LAKENS_CALDWELL = ("Lakens, D., & Caldwell, A. R. (2021). Simulation-based power analysis for factorial analysis of "
                   "variance designs. Advances in Methods and Practices in Psychological Science, 4(1). "
                   "https://doi.org/10.1177/2515245920951503")
LAKENS_2022 = ("Lakens, D. (2022). Sample size justification. Collabra: Psychology, 8(1), 33267. "
               "https://doi.org/10.1525/collabra.33267")
WILSON = ("Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. Journal of the "
          "American Statistical Association, 22(158), 209–212. https://doi.org/10.1080/01621459.1927.10502953")
NEWCOMBE = ("Newcombe, R. G. (1998). Two-sided confidence intervals for the single proportion: Comparison of seven "
            "methods. Statistics in Medicine, 17(8), 857–872. "
            "https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8<857::AID-SIM777>3.0.CO;2-E")
BONETT = ("Bonett, D. G. (2002). Sample size requirements for testing and estimating coefficient alpha. Journal of "
          "Educational and Behavioral Statistics, 27(4), 335–340. https://doi.org/10.3102/10769986027004335")
KISH_1965 = "Kish, L. (1965). Survey sampling. Wiley."
KISH_1987 = "Kish, L. (1987). Statistical design for research. Wiley. https://doi.org/10.1002/0471725196"
KISH_1992 = "Kish, L. (1992). Weighting for unequal Pi. Journal of Official Statistics, 8(2), 183–200."
GABLER = ("Gabler, S., Häder, S., & Lahiri, P. (1999). A model based justification of Kish's formula for design "
          "effects for weighting and clustering. Survey Methodology, 25(1), 105–106.")
KILLIP = ("Killip, S., Mahfoud, Z., & Pearce, K. (2004). What is an intracluster correlation coefficient? Crucial "
          "concepts for primary care researchers. Annals of Family Medicine, 2(3), 204–208. "
          "https://doi.org/10.1370/afm.141")
ATZMUELLER = ("Atzmüller, C., & Steiner, P. M. (2010). Experimental vignette studies in survey research. Methodology, "
              "6(3), 128–138. https://doi.org/10.1027/1614-2241/a000014")
AUSPURG_HINZ = ("Auspurg, K., & Hinz, T. (2015). Factorial survey experiments (Quantitative Applications in the Social "
                "Sciences, Vol. 175). Sage. https://doi.org/10.4135/9781483398075")
SAUER = ("Sauer, C., Auspurg, K., Hinz, T., & Liebig, S. (2011). The application of factorial surveys in general "
         "population samples: The effects of respondent age and education on response times and response "
         "consistency. Survey Research Methods, 5(3), 89–102. https://doi.org/10.18148/srm/2011.v5i3.4625")
CHAPMAN = ("Chapman, C. N., Alford, J. L., Johnson, C., Weidemann, R., & Lahav, M. (2009). CBC vs. ACBC: Comparing "
           "results with real product selection. In Proceedings of the Sawtooth Software Conference. Sawtooth Software.")
ORME_CHRZAN = ("Orme, B. K., & Chrzan, K. (2017). Becoming an expert in conjoint analysis: Choice modeling for pros. "
               "Sawtooth Software.")
# one fold on the page, grouped by planner (_sources.html, grouped)
SOURCES = [
    ("t-Tests (zwei Gruppen, auch ungleich groß; Messwiederholung), ANOVA (ein- und zweifaktoriell, "
     "Messwiederholung), χ²-Test: exakte Power, Effektstärken d, f und w", [COHEN, FAUL_2007]),
    ("Messwiederholung: d_z aus d und der Korrelation der Messungen", [LAKENS]),
    ("Messwiederholung mit mehreren Messungen: Simulation für komplexere Designs", [LAKENS_CALDWELL]),
    ("Korrelation und Regression (R², Zuwachs ΔR²): exakte Power", [COHEN, FAUL_2009]),
    ("Zwei Anteile vergleichen: z-Test ohne Stetigkeitskorrektur, Effektstärke h", [FLEISS, COHEN]),
    ("Äquivalenztest (TOST)", [SCHUIRMANN, LAKENS_2017]),
    ("Kleinster erkennbarer Effekt bei fester Stichprobe", [LAKENS_2022]),
    ("Anteil und Mittelwert schätzen: Wilson-Intervall, Fehlerspanne und Endlichkeitskorrektur",
     [WILSON, NEWCOMBE, COCHRAN]),
    ("Reliabilität: Test von Cronbachs α", [BONETT]),
    ("Designeffekt durch Klumpen und Gewichtung; Teilgruppen", [KISH_1965, KISH_1992, GABLER, KILLIP, KISH_1987]),
    ("Conjoint: Faustregel n ≥ 500 · c / (Aufgaben · Alternativen); das 1,5-Fache als Empfehlung ist eine "
     "Festlegung von Askurious", list(CONJOINT_SOURCES)),
    ("ACBC: keine eigene Faustregel, oft genauer als CBC", [CHAPMAN, ORME_CHRZAN]),
    ("MaxDiff: jede Aussage 500- bzw. 1.000-mal, etwa dreimal je Person; die Übertragung der Faustregel der "
     "Wahlexperimente ist eine Festlegung von Askurious", list(MAXDIFF_SOURCES)),
    ("Paarvergleich: Information des Bradley-Terry-Modells bei Werten 0", [DE_BEKKER_GROB, BRADLEY_TERRY]),
    ("Vignetten: Decks, höchstens 10 Vignetten je Person; 5 Personen je Deck als Vorgabe ist eine Festlegung "
     "von Askurious", [ATZMUELLER, AUSPURG_HINZ, SAUER]),
]
# the headings of SOURCES in English, in the same order (sources() picks them when the page is English)
SOURCES_EN = [
    "t-tests (two groups, also of unequal size; repeated measures), ANOVA (one-way and two-way, repeated measures), "
    "χ² test: exact power, effect sizes d, f and w",
    "Repeated measures: d_z from d and the correlation of the measurements",
    "Repeated measures with several measurements: simulation for more complex designs",
    "Correlation and regression (R², increase ΔR²): exact power",
    "Comparing two proportions: z-test without continuity correction, effect size h",
    "Equivalence test (TOST)",
    "Smallest detectable effect for a fixed sample",
    "Estimating a proportion and a mean: Wilson interval, margin of error and finite population correction",
    "Reliability: test of Cronbach's α",
    "Design effect from clusters and weighting; subgroups",
    "Conjoint: rule of thumb n ≥ 500 · c / (tasks · alternatives); 1.5 times that as the recommendation is set by "
    "Askurious",
    "ACBC: no rule of thumb of its own, often more precise than CBC",
    "MaxDiff: every statement 500 or 1,000 times, about three times per person; carrying over the rule of thumb of "
    "choice experiments is set by Askurious",
    "Paired comparison: information of the Bradley-Terry model at values of 0",
    "Vignettes: decks, at most 10 vignettes per person; 5 people per deck as the default is set by Askurious",
]


def sources():
    """SOURCES with their headings in the language of the page; the model test of a structural equation model only
    as well (for everyone since 2026-10-11)."""
    from app import sem

    found = SOURCES if not english() else [(en, refs) for en, (_, refs) in zip(SOURCES_EN, SOURCES)]
    return found + [(de_en("Strukturgleichungsmodell: Power des Modelltests (RMSEA); Pfade im Studienplan",
                           "Structural equation model: power of the model test (RMSEA); paths in the research plan"),
                     [sem.MACCALLUM, sem.SATORRA_SARIS, sem.WOLF])]


def _survey():
    sid = request.args.get("survey", type=int)
    s = db.session.get(Survey, sid) if sid else None
    return s if s is not None and s.role_of(current_user) is not None else None


@bp.route("/")
@login_required
def index():
    survey = _survey()
    conjoint = maxdiff = paired = None
    if survey:
        items = [i for b in survey.blocks for i in b.items]
        cj = next((i for i in items if i.type == "conjoint"), None)
        if cj:
            levels = cj.kind.level_counts(cj)
            conjoint = {"code": cj.code, "tasks": cj.config.get("tasks", 8), "alternatives": cj.config.get("alternatives", 2),
                        "levels": max(levels or [3])}
        md = next((i for i in items if i.type == "maxdiff"), None)
        if md and md.kind.n_items(md) >= 4:
            maxdiff = {"code": md.code, "statements": md.kind.n_items(md), "set_size": md.config.get("set_size", 4),
                       "sets": md.kind.n_sets(md)}
        pc = next((i for i in items if i.type == "paired"), None)
        if pc and pc.kind.n_items(pc) >= 3:
            paired = {"code": pc.code, "options": pc.kind.n_items(pc), "pairs": pc.kind.n_pairs(pc)}
    default = "conjoint" if conjoint else "maxdiff" if maxdiff else "pairedcmp" if paired else "groups"
    # the power of the part-worths from the survey's own design (PA7; for everyone since 2026-10-10, before experimental)
    choice_power = {}
    if survey:
        from app import power_choice

        if conjoint:
            choice_power["conjoint"] = power_choice.conjoint(cj)
        if maxdiff:
            choice_power["maxdiff"] = power_choice.maxdiff(md)
        if paired:
            choice_power["pairedcmp"] = power_choice.paired(pc)
    from app.power_choice import DE_BEKKER_GROB, LOUVIERE

    prefill = _prefill()
    # the expected exclusions of the research plan, as its power analysis counts them (UI/UX audit 2026-10-07, W2)
    from app import sample_model, study_plan

    planned = study_plan.plan_of(survey)["sample"].get("exclusions") if survey else None
    exclusions = prefill.get("excl", planned if planned is not None else study_plan.EXCLUSIONS)
    return render_template("samplesize/index.html", survey=survey, conjoint=conjoint, maxdiff=maxdiff, paired=paired, sources=sources(),
                           design=request.args.get("design", default), prefill=prefill, holm=_holm(prefill), choice_power=choice_power,
                           choice_sources=[DE_BEKKER_GROB, LOUVIERE], exclusions=exclusions,
                           exclusions_from_plan="excl" not in prefill and planned is not None,
                           calc=sample_model.for_calculator(survey))


# values a link may set, with their bounds: "im Rechner öffnen" of a hypothesis in the study plan (PA1)
PREFILL = {"effect": (0.01, 3), "f": (0.01, 2), "w": (0.01, 2), "power": (0.5, 0.99), "groups": (2, 20), "rows": (2, 30),
           "cols": (1, 30), "df": (1, 5000), "alpha": (0.001, 0.1), "excl": (0, 90)}


def _prefill():
    out = {}
    for key, (lo, hi) in PREFILL.items():
        value = request.args.get(key, type=float)
        if value is not None and lo <= value <= hi:
            out[key] = int(value) if key in ("groups", "rows", "cols", "df", "excl") else value
    return out


def _holm(prefill):
    """The extra choice of α for a hypothesis of a study plan with Holm's correction (PA-5): {"value", "text" (0,025),
    "k" (hypotheses, None when α is no .05 / k)}, None for the levels the list has anyway."""
    alpha = prefill.get("alpha")
    if not alpha or alpha in (0.05, 0.01, 0.1):
        return None
    from app import study_plan

    text = study_plan.alpha_text(alpha, "en" if english() else "de")  # ",025", as APA and the plan write it (W3)
    k = round(0.05 / alpha)
    return {"value": alpha, "text": text, "k": k if k >= 2 and abs(0.05 / k - alpha) < 1e-9 else None}
