"""Studienplan: research question, hypotheses, design, sample, quality rules and analysis plan of a survey in one
place, which the questionnaire, the pre-launch check, the results and the pre-registration refer to (concept "Askurious
für Studierende und Berufseinsteiger", package A; Ralf 2026-10-05: no tab of its own, a group of the settings and an
optional step of the setup wizard).

Stored in survey.study_plan (JSON, None = not begun); the research question stays in survey.research_question, which
the AI helpers read. A hypothesis names what explains (`iv`) and what is explained (`dv`) as variable names of the
questionnaire (items keep their code when moved or copied; a renamed variable is followed in rename_code). The
analysis is suggested from the kind of hypothesis and the levels of both (suggest()), and can be chosen. After the
start every change of the plan is noted as a deviation from the pre-registration (S7), with date and what changed.
"""
import copy
import math
import re
import secrets

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import autosave
from app.extensions import db
from app.ids import db_id
from app.lang import de_en, english

bp = Blueprint("study_plan", __name__, url_prefix="/surveys")

GOALS = {"describe": "beschreiben, wie verbreitet etwas ist", "correlation": "Zusammenhänge prüfen",
         "difference": "Gruppen vergleichen", "causal": "eine Wirkung prüfen (Experiment)",
         "preference": "Präferenzen für Eigenschaften messen", "importance": "Wichtigkeit vieler Merkmale ordnen",
         "price": "Zahlungsbereitschaft ermitteln", "explore": "offen erkunden"}
# the design of the whole study: how people, time points and conditions are arranged. Methods inside a survey
# (Conjoint, MaxDiff, Van Westendorp, IAT, scales) are elements, not designs: a survey with a Conjoint is a
# cross-sectional survey (Philipp, 2026-10-06); plans that still say "conjoint" and the like read as "cross"
DESIGNS = {"cross": "Querschnittsbefragung", "experiment": "Experiment mit Zufallszuteilung",
           "within": "Messwiederholung (innerhalb der Personen)", "vignettes": "Vignettenstudie",
           "panel": "Längsschnitt (mehrere Wellen)",
           # the same questions to new people at several times (DDI TimeMethod Longitudinal.TrendRepeatedCrossSection)
           "trend": "Wiederholter Querschnitt (Trend)"}
FORMER_DESIGNS = {"conjoint": "cross", "maxdiff": "cross", "psm": "cross"}



def _today():
    """The date of a deviation from the plan, as every other date of the app in Europe/Berlin (app/timeutil.py): the UTC
    date put a change at 00:30 on the day before, before a data download of the same morning (audit 2026-10-22, G-07)."""
    from app import timeutil

    return timeutil.local_today().isoformat()


def design_key(value):
    """A design of DESIGNS for a stored or posted value; the former method designs become "cross"; else ""."""
    value = FORMER_DESIGNS.get(value, value)
    return value if value in DESIGNS else ""
KINDS = {"correlation": "Zusammenhang",
         # a directed path in the model (modell-zeichnen-konzept.md, Ralf 2026-10-11);
         # "Zusammenhang" with the method "Strukturgleichungsmodell" is a relation without a direction there
         "effect": "Einfluss (gerichteter Pfad im Modell)",
         "difference": "Unterschied zwischen Gruppen",
         "causal": "Wirkung einer Bedingung (Experiment)",
         # one value, share or ranking of one variable, described without a test (pre-test 2026-10-20, 6: "Level X of a
         # conjoint has the highest part-worth", "more than half agree"): the results report the value
         "descriptive": "Beschreibende Annahme (Wert, Anteil, Rangfolge)",
         "open": "Offene Forschungsfrage (explorativ)",
         # (app/sem_model.py): x wirkt über m auf y, geprüft als indirekter Effekt
         "mediation": "Vermittlung (indirekter Effekt)",
         # stage 2 of the drawing (modell-zeichnen-konzept.md, F2): w changes the strength of the path from x to y
         "moderation": "Moderation (verändert die Stärke eines Pfads)",
         # X in one wave acts on Y in the next (longitudinal and multilevel concept 2026-10-17, E2): tested in the
         # cross-lagged panel model over the waves (app/panel_models.py); offered with waves or the design "Längsschnitt"
         "lagged": "Wirkung über die Zeit (X in einer Welle auf Y in der nächsten)"}
DIRECTIONS = {"": "ohne Richtung", "pos": "positiv / A größer", "neg": "negativ / A kleiner"}
RECRUITMENT = {"": "noch offen", "convenience": "Gelegenheitsstichprobe (Bekanntenkreis, Verteiler)",
               "snowball": "Schneeball", "panel": "Online-Panel", "quota": "Quotenstichprobe",
               "random": "Zufallsstichprobe", "census": "Vollerhebung (alle der Gruppe)",
               # chosen on purpose, and chosen step by step from what the analysis finds (DDI SamplingProcedure
               # Nonprobability.Purposive and TheoreticalSampling; Ralf 2026-10-27, for interview guides above all)
               "purposive": "Bewusste Auswahl (z. B. Fachleute, typische Fälle)",
               "theoretical": "Theoretische Stichprobe (Grounded Theory)"}
# the recruitment in more detail, as archives describe a sample (DDI vocabulary SamplingProcedure, app/ddi.py; Ralf
# 2026-10-27): an online panel is either an access panel (people sign up themselves) or a probability panel (drawn at
# random and invited); a random sample has its kind of drawing
PANEL_KINDS = {"": "noch offen", "access": "Access-Panel (Personen melden sich selbst an)",
               "probability": "Zufallspanel (zufällig gezogen und eingeladen)"}
RANDOM_KINDS = {"": "noch offen", "simple": "einfache Zufallsstichprobe", "systematic": "systematische Zufallsstichprobe",
                "stratified": "geschichtete Zufallsstichprobe", "cluster": "Klumpenstichprobe",
                "multistage": "mehrstufige Zufallsstichprobe"}
ANALYSES = {"": "noch offen", "r": "Korrelation (Pearson)", "rho": "Rangkorrelation (Spearman)",
            "t": "t-Test (Welch) für zwei Gruppen", "anova": "Varianzanalyse (Welch) für mehrere Gruppen",
            "u": "Mann-Whitney-U für zwei Gruppen", "kw": "Kruskal-Wallis für mehrere Gruppen",
            "chi2": "χ²-Test (Kreuztabelle)", "describe": "nur beschreiben",
            "sem": "Strukturgleichungsmodell (alle so markierten Hypothesen gemeinsam)",
            "panel": "Cross-Lagged-Panel-Modell über die Wellen",
            # a diary study: entries within persons (app/diary_model.py), chosen by the results, not in the plan
            "mixed": "Mehrebenenmodell (Einträge in Personen)"}
# kinds and methods of the structural equation model (experimental until 2026-10-11, now offered to everyone)
SEM_KINDS, SEM_ANALYSES = ("mediation", "effect", "moderation"), ("sem",)
# kinds without a significance test: an open question and a descriptive assumption ("nur beschreiben")
UNTESTED = ("open", "descriptive")
# kinds with a single variable, the measured one in `dv` (what explains stays empty)
ONE_VARIABLE = ("descriptive",)


def untested(h):
    """Whether a hypothesis has no significance test by its kind (UNTESTED)."""
    return bool(h) and h.get("kind") in UNTESTED
# what the two (three) variables of a hypothesis are called, by its kind: a relationship has no predictor and outcome
# (Ralf, 2026-10-07); (German, English) for the first, the second and the mediator
ROLES = {"correlation": (("Variable A", "Variable A"), ("Variable B", "Variable B")),
         "difference": (("Gruppen", "Groups"), ("Verglichen wird", "Compared")),
         "causal": (("Bedingung", "Condition"), ("Zielgröße", "Outcome")),
         "descriptive": (("–", "–"), ("Gemessene Größe", "Measured variable"))}
DEFAULT_ROLES = (("Einflussgröße", "Predictor"), ("Zielgröße", "Outcome"))
MEDIATOR = ("Vermittler", "Mediator")
MODERATOR = ("Moderator", "Moderator")


def roles(kind):
    """{"iv", "dv", "mv", "wv"}: the names of the variables of a hypothesis of this kind, in the language of the
    page."""
    iv, dv = ROLES.get(kind, DEFAULT_ROLES)
    return {"iv": de_en(*iv), "dv": de_en(*dv), "mv": de_en(*MEDIATOR), "wv": de_en(*MODERATOR)}


def roles_all():
    """roles() of every kind, for the form that renames the fields when the kind changes (app.js)."""
    return {kind: roles(kind) for kind in [*KINDS, ""]}


# what each role means, one short line beside its box in the element editor (pre-test 2026-10-20, 11: "Gruppen" and
# "Verglichen wird" alone did not say which box to tick); (German, English) for what explains and what is explained
ROLE_HINTS = {
    "correlation": (("eine Seite des Zusammenhangs (ohne Richtung, z. B. Technikvertrauen)",
                     "one side of the relationship (no direction, e.g. trust in technology)"),
                    ("die andere Seite des Zusammenhangs (z. B. Akzeptanz)",
                     "the other side of the relationship (e.g. acceptance)")),
    "difference": (("teilt die Befragten in Gruppen (unabhängige Variable, z. B. Geschlecht)",
                    "splits the respondents into groups (independent variable, e.g. gender)"),
                   ("wird zwischen den Gruppen verglichen (abhängige Variable, z. B. Zufriedenheit)",
                    "is compared between the groups (dependent variable, e.g. satisfaction)")),
    "causal": (("die zugeteilte Versuchsbedingung (unabhängige Variable, meist eine Randomisierung)",
                "the assigned condition (independent variable, usually a randomisation)"),
               ("worauf die Bedingung wirken soll (abhängige Variable)",
                "what the condition is to affect (dependent variable)")),
    "descriptive": (("", ""), ("deren Wert, Anteil oder Rangfolge beschrieben wird",
                               "whose value, share or ranking is described")),
}
DEFAULT_ROLE_HINTS = (("wirkt auf die Zielgröße (unabhängige Variable)", "acts on the outcome (independent variable)"),
                      ("wird beeinflusst (abhängige Variable)", "is influenced (dependent variable)"))


def role_hints(kind):
    """{"iv", "dv"}: what the two roles of a hypothesis of this kind mean, in the language of the page."""
    iv, dv = ROLE_HINTS.get(kind, DEFAULT_ROLE_HINTS)
    return {"iv": de_en(*iv) if iv[0] else "", "dv": de_en(*dv)}


def role_keys(kind):
    """The roles a hypothesis of this kind names in the element editor: only the measured one for one variable."""
    return ("dv",) if kind in ONE_VARIABLE else ("iv", "dv")
# the method finder (concept "Studierende", package C): what the research goal suggests, and where it is in Askurious
FINDER = {
    "describe": ("cross", "Eine Querschnittsbefragung mit Auswahl- und Likert-Fragen beschreibt Verbreitung und Meinungen; "
                 "Anteile mit Konfidenzintervall zeigen, wie genau sie sind.", "Fragebogen: Auswahl, Likert"),
    "correlation": ("cross", "Zusammenhänge prüft eine Querschnittsbefragung mit validierten Skalen; ausgewertet als "
                    "Korrelation. Kausale Schlüsse erlaubt sie nicht.", "Fragebogen: Konstrukte aus dem Pool"),
    "difference": ("cross", "Gruppen vergleicht eine Querschnittsbefragung mit einer Gruppenfrage (z. B. Studiengang) und "
                   "einer Zielgröße; ausgewertet mit t-Test oder Varianzanalyse.", "Ergebnisse: „Nach Gruppen aufteilen“"),
    "causal": ("experiment", "Eine Wirkung zeigt nur ein Experiment: Personen werden zufällig Bedingungen zugeteilt, alles "
               "andere bleibt gleich.", "Ablauf: Randomisierung mit einem Block je Bedingung"),
    "preference": ("cross", "Präferenzen für Produkteigenschaften misst eine Befragung mit einem Conjoint: Personen wählen zwischen Varianten, "
                   "die Analyse zeigt Teilnutzen und Wichtigkeit. Bei vielen Merkmalen passt ACBC; ob es Gruppen mit "
                   "eigenen Vorlieben gibt, zeigen die Segmente.",
                   "Element Conjoint oder ACBC, Simulator und Segmente in den Ergebnissen"),
    "importance": ("cross", "Viele Merkmale ordnet eine Befragung mit MaxDiff: je Aufgabe das wichtigste und unwichtigste von wenigen "
                   "Merkmalen; klarer als Bewertungsskalen.", "Element MaxDiff"),
    "price": ("cross", "Zahlungsbereitschaft ermittelt eine Befragung mit den vier Preisfragen nach Van Westendorp; Ergebnis "
              "ist der akzeptable Preisbereich.", "Element Preisfragen"),
    "explore": ("cross", "Offenes Erkunden gelingt mit offenen Fragen oder Interviews; Hypothesen entstehen erst daraus.",
                "Freitext-Fragen oder ein Interviewleitfaden"),
}
# where "In Askurious: …" of the finder leads (UI/UX audit 2026-10-05, V6): the tab the named place is in
FINDER_PAGE = {"describe": "editor", "correlation": "editor", "difference": "results", "causal": "flow",
               "preference": "editor", "importance": "editor", "price": "editor", "explore": "editor"}
# where the expected effect of a hypothesis comes from (Lakens, 2022, sample size justification); the rule of thumb
# is the weakest reason and says so on the page
BASES = {"": "noch offen", "literature": "Literatur oder Metaanalyse", "pilot": "Vorstudie oder Pilotdaten",
         "sesoi": "kleinster relevanter Effekt", "cohen": "Faustregel nach Cohen"}
BASES_EN = {"": "not yet decided", "literature": "literature or meta-analysis", "pilot": "pilot survey or earlier data",
            "sesoi": "smallest effect of interest", "cohen": "Cohen's rule of thumb"}
POWERS = (0.8, 0.9, 0.95)
EXCLUSIONS = 10  # % of completions expected to be left out by the quality rules, as the calculator (Festlegung)
GOAL_CHOICES = {"": "noch offen", **GOALS}
DESIGN_CHOICES = {"": "noch offen", **DESIGNS}
# the same lists in English (researcher pages in English, app/lang.py); the German ones stay for the pre-registration,
# the method section and the AI helpers, which have their own language
GOALS_EN = {"describe": "describe how common something is", "correlation": "test relationships",
            "difference": "compare groups", "causal": "test an effect (experiment)",
            "preference": "measure preferences for attributes", "importance": "rank the importance of many attributes",
            "price": "find the willingness to pay", "explore": "explore openly"}
DESIGNS_EN = {"cross": "Cross-sectional survey", "experiment": "Experiment with random assignment",
              "within": "Repeated measures (within people)", "vignettes": "Vignette study",
              "panel": "Longitudinal (several waves)", "trend": "Repeated cross-section (trend)"}
KINDS_EN = {"correlation": "Relationship", "effect": "Influence (directed path in the model)",
            "difference": "Difference between groups",
            "causal": "Effect of a condition (experiment)",
            "descriptive": "Descriptive assumption (value, share, ranking)",
            "open": "Open research question (exploratory)",
            "mediation": "Mediation (indirect effect)", "moderation": "Moderation (changes the strength of a path)",
            "lagged": "Effect over time (X in one wave on Y in the next)"}
DIRECTIONS_EN = {"": "no direction", "pos": "positive / A larger", "neg": "negative / A smaller"}
RECRUITMENT_EN = {"": "not yet decided", "convenience": "Convenience sample (acquaintances, mailing lists)",
                  "snowball": "Snowball", "panel": "Online panel", "quota": "Quota sample",
                  "random": "Random sample", "census": "Full census (everyone in the group)",
                  "purposive": "Purposive sample (e.g. experts, typical cases)",
                  "theoretical": "Theoretical sampling (grounded theory)"}
PANEL_KINDS_EN = {"": "not yet decided", "access": "Access panel (people sign up themselves)",
                  "probability": "Probability panel (drawn at random and invited)"}
RANDOM_KINDS_EN = {"": "not yet decided", "simple": "simple random sample", "systematic": "systematic random sample",
                   "stratified": "stratified random sample", "cluster": "cluster sample", "multistage": "multistage random sample"}
ANALYSES_EN = {"": "not yet decided", "r": "Correlation (Pearson)", "rho": "Rank correlation (Spearman)",
               "t": "t-test (Welch) for two groups", "anova": "Analysis of variance (Welch) for several groups",
               "u": "Mann-Whitney U for two groups", "kw": "Kruskal-Wallis for several groups",
               "chi2": "χ² test (cross table)", "describe": "describe only",
               "sem": "Structural equation model (all hypotheses marked so, together)",
               "panel": "Cross-lagged panel model over the waves",
               "mixed": "Multilevel model (entries within people)"}
FINDER_EN = {
    "describe": ("A cross-sectional survey with choice and Likert questions describes how common things and opinions "
                 "are; shares with a confidence interval show how precise they are.", "Questionnaire: choice, Likert"),
    "correlation": ("A cross-sectional survey with validated scales tests relationships, analysed as a correlation. "
                    "It does not allow causal conclusions.", "Questionnaire: constructs from the pool"),
    "difference": ("A cross-sectional survey compares groups with a group question (e.g. degree programme) and an "
                   "outcome, analysed with a t-test or an analysis of variance.", "Results: “Split by groups”"),
    "causal": ("Only an experiment shows an effect: people are assigned to conditions at random, everything else "
               "stays the same.", "Flow: randomisation with one block per condition"),
    "preference": ("A survey with a conjoint measures preferences for product attributes: people choose between "
                   "variants, the analysis shows part-worths and importance. With many attributes ACBC fits; the "
                   "segments show whether there are groups with preferences of their own.",
                   "Element Conjoint or ACBC, simulator and segments in the results"),
    "importance": ("A survey with MaxDiff ranks many attributes: in each task the most and the least important of a "
                   "few attributes; clearer than rating scales.", "Element MaxDiff"),
    "price": ("A survey with the four price questions after Van Westendorp finds the willingness to pay; the result "
              "is the acceptable price range.", "Element price questions"),
    "explore": ("Open questions or interviews suit open exploration; hypotheses only arise from them.",
                "Free-text questions or an interview guide"),
}


def _local(german, english_table):
    """A list of labels in the language of the page (keys as in the German one)."""
    return {key: english_table.get(key, text) for key, text in german.items()} if english() else german


def goals():
    return _local(GOALS, GOALS_EN)


def designs():
    return _local(DESIGNS, DESIGNS_EN)


def _sem_on(survey):
    return True  # structural equation models are open to everyone since 2026-10-11 (Ralf; before experimental)


def sem_methods():
    """How the structural equation model can be estimated, in the language of the page."""
    from app import sem_model

    return sem_model.methods()


def kinds(survey=None):
    """The kinds of hypotheses; "Vermittlung", "Einfluss" and "Moderation" for everyone since 2026-10-11."""
    out = dict(_local(KINDS, KINDS_EN))  # a copy: _local hands the German table itself back
    if not _sem_on(survey):
        out = {k: v for k, v in out.items() if k not in SEM_KINDS}
    if survey is not None and not _longitudinal(survey):
        out.pop("lagged", None)
    return out


def _longitudinal(survey):
    """Whether the kind "Wirkung über die Zeit" is offered: the survey has waves, the plan's design is "Längsschnitt",
    or a hypothesis of that kind is there already."""
    from app import waves

    raw = survey.study_plan or {}
    return bool(waves.of(survey) or design_key(raw.get("design")) == "panel"
                or any(isinstance(h, dict) and h.get("kind") == "lagged" for h in raw.get("hypotheses") or []))


def directions(kind=None):
    """The directions in the language of the page, named by the kind of the hypothesis (UI/UX audit 2026-10-09, X14):
    a relationship is "positiv/negativ" (there is no group A), a group comparison "A größer/A kleiner"; without a
    kind both."""
    out = _local(DIRECTIONS, DIRECTIONS_EN)
    if kind in ("correlation", "mediation", "effect", "lagged"):
        out = {**out, "pos": de_en("positiv", "positive"), "neg": de_en("negativ", "negative")}
    elif kind == "moderation":  # the sign of the interaction: the path grows or shrinks with the moderator; with a
        # negative path a positive interaction weakens it in size, so the words name the sign (audit 2026-10-12, SEM-11)
        out = {**out, "pos": de_en("positive Wechselwirkung (der Pfad wird mit dem Moderator größer)",
                                   "positive interaction (the path grows with the moderator)"),
               "neg": de_en("negative Wechselwirkung (der Pfad wird mit dem Moderator kleiner)",
                            "negative interaction (the path shrinks with the moderator)")}
    elif kind in ("difference", "causal"):
        out = {**out, "pos": de_en("A größer", "A larger"), "neg": de_en("A kleiner", "A smaller")}
    return out


def directions_all():
    """directions() of every kind, for the form that renames the choices when the kind changes (app.js)."""
    return {kind: directions(kind) for kind in [*KINDS, ""]}


def recruitment():
    return _local(RECRUITMENT, RECRUITMENT_EN)


# the cards of the wizard (Ralf 2026-10-27): design and recruitment in plain words, the DDI term below (German, English)
DESIGN_CARDS = [
    ("cross", ("Querschnitt", "einmal befragen", "Querschnitt"), ("Cross-section", "asked once", "Cross-section")),
    ("panel", ("Längsschnitt (Panel)", "dieselben Personen mehrmals", "Längsschnitt: Panel"),
     ("Longitudinal (panel)", "the same people several times", "Longitudinal: Panel")),
    ("trend", ("Wiederholter Querschnitt (Trend)", "mehrmals, jedes Mal neue Personen",
               "Längsschnitt: Trend/Wiederholter Querschnitt"),
     ("Repeated cross-section (trend)", "several times, new people each time", "Longitudinal: Trend/Repeated cross-section")),
    ("within", ("Messwiederholung", "dieselben Personen, mehrere Bedingungen", "Längsschnitt: Panel"),
     ("Repeated measures", "the same people, several conditions", "Longitudinal: Panel")),
    ("experiment", ("Experiment", "Bedingungen zufällig zuteilen", "Querschnitt, Web-basiertes Experiment"),
     ("Experiment", "assign conditions at random", "Cross-section, web-based experiment")),
    ("vignettes", ("Vignettenstudie", "Beschreibungen mit wechselnden Merkmalen", "Querschnitt, Web-basiertes Experiment"),
     ("Vignette study", "descriptions with varying features", "Cross-section, web-based experiment")),
]
# recruitment cards: value as sent ("panel:access" sets the kind of panel too), with or without chance
RECRUITMENT_CARDS = [
    # one card for drawn at random, from a list or as a probability panel: the same for DDI, and both invite
    # people, so both ask for the response rate (Ralf 2026-10-27)
    (True, "random", ("Zufallsstichprobe", "aus einer Liste aller gezogen oder als Zufallspanel eingeladen",
                      "Wahrscheinlichkeitsauswahl"),
     ("Random sample", "drawn from a list of everyone or invited as a probability panel", "Probability")),
    (True, "census", ("Vollerhebung", "alle der Gruppe", "Vollerhebung"), ("Full census", "everyone in the group",
                                                                         "Total universe/Complete enumeration")),
    (False, "convenience", ("Gelegenheitsstichprobe", "Bekanntenkreis, Verteiler, soziale Medien",
                            "Nicht-Wahrscheinlichkeitsauswahl: Willkürliche Auswahl"),
     ("Convenience sample", "acquaintances, mailing lists, social media", "Non-probability: Availability")),
    (False, "panel:access", ("Access-Panel", "Personen melden sich selbst an", "Nicht-Wahrscheinlichkeitsauswahl"),
     ("Access panel", "people sign up themselves", "Non-probability")),
    (False, "quota", ("Quotenstichprobe", "nach Merkmalen auffüllen", "Nicht-Wahrscheinlichkeitsauswahl: Quotenstichprobe"),
     ("Quota sample", "filled up by characteristics", "Non-probability: Quota")),
    (False, "snowball", ("Schneeball", "Teilnehmende werben weitere", "Nicht-Wahrscheinlichkeitsauswahl: Respondenten gesteuerte Auswahl"),
     ("Snowball", "participants recruit others", "Non-probability: Respondent-assisted")),
    (False, "purposive", ("Bewusste Auswahl", "Fachleute, typische Fälle", "Nicht-Wahrscheinlichkeitsauswahl: Bewusste Auswahl"),
     ("Purposive sample", "experts, typical cases", "Non-probability: Purposive")),
    (False, "theoretical", ("Theoretische Stichprobe", "nach und nach aus der Analyse", "Theoretische Stichprobenziehung"),
     ("Theoretical sampling", "step by step from the analysis", "Theoretical sampling")),
]


def suggested_design(plan):
    """The design the first aim suggests (FINDER), "" without aims."""
    for goal in plan.get("goals") or []:
        if goal in FINDER:
            return FINDER[goal][0]
    return ""


def design_cards():
    """[(key, title, text, ddi term)] in the language of the page."""
    i = 2 if english() else 1
    return [(c[0], *c[i]) for c in DESIGN_CARDS]


def recruitment_cards():
    """[(with chance, value, title, text, ddi term)] in the language of the page."""
    i = 3 if english() else 2
    return [(c[0], c[1], *c[i]) for c in RECRUITMENT_CARDS]


def recruitment_value(sample):
    """The card value of a stored sample: "panel:access" for a panel with its kind, else the recruitment."""
    how = sample.get("recruitment") or ""
    if how == "panel" and sample.get("panel_kind") == "probability":
        return "random"  # a probability panel of before: the card "Zufallsstichprobe" since 2026-10-27
    return f"panel:{sample['panel_kind']}" if how == "panel" and sample.get("panel_kind") else how


RANDOM_KIND_HINTS = {
    "simple": ("Jede Person der Liste hat dieselbe Chance, etwa per Zufallszahl gezogen.",
               "Every person on the list has the same chance, drawn e.g. by random numbers."),
    "systematic": ("Jede k-te Person einer Liste ab einem zufälligen Start, etwa jede zehnte Matrikelnummer.",
                   "Every k-th person of a list from a random start, e.g. every tenth student number."),
    "stratified": ("Erst in Gruppen (Schichten) teilen, etwa nach Fakultät, dann in jeder Gruppe zufällig ziehen.",
                   "First split into groups (strata), e.g. by faculty, then draw at random within each group."),
    "cluster": ("Ganze Gruppen zufällig ziehen, etwa Schulklassen, und dort alle befragen.",
                "Draw whole groups at random, e.g. school classes, and ask everyone in them."),
    "multistage": ("In Stufen ziehen, etwa erst Gemeinden, dann Haushalte, dann eine Person je Haushalt.",
                   "Draw in stages, e.g. first municipalities, then households, then one person per household."),
}


def random_kind_hints():
    """{kind: one line on it} in the language of the page, under the pick "Art der Zufallsstichprobe"."""
    i = 1 if english() else 0
    return {k: v[i] for k, v in RANDOM_KIND_HINTS.items()}


def panel_kinds():
    return _local(PANEL_KINDS, PANEL_KINDS_EN)


def random_kinds():
    return _local(RANDOM_KINDS, RANDOM_KINDS_EN)


def _pick(value, table):
    return value if isinstance(value, str) and value in table else ""


MAX_KEYWORDS = 12


def topics():
    """[(code, German, English)] of the CESSDA Topic Classification 4.2.3 (app/data/cessda_topics.json, CC BY 4.0)."""
    global _TOPICS
    if _TOPICS is None:
        import json
        import os

        with open(os.path.join(os.path.dirname(__file__), "data", "cessda_topics.json"), encoding="utf-8") as f:
            _TOPICS = [tuple(t) for t in json.load(f)["topics"]]
    return _TOPICS


_TOPICS = None
TOPIC_VOCABULARY = "https://vocabularies.cessda.eu/vocabulary/TopicClassification/4.2.3"


def topic_choices():
    """{code: name} in the language of the page, subtopics after their topic with "› " (for the select)."""
    en = english()
    names = {code: (e if en else d) for code, d, e in topics()}
    out = {"": "not yet decided" if en else "noch offen"}
    for code, _, _ in topics():
        head = code.split(".")[0]
        out[code] = names[code] if code == head else f"{names.get(head, head)} › {names[code]}"
    return out


def topic_name(code, lang=None):
    found = {c: (e if lang == "en" else d) for c, d, e in topics()}
    if not code or code not in found:
        return ""
    head = code.split(".")[0]
    return found[code] if code == head else f"{found.get(head, head)} › {found[code]}"


def archive_of(raw):
    """{"keywords": [...], "topic": code} of a plan, anything else read as empty."""
    raw = raw if isinstance(raw, dict) else {}
    words = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
    keywords = []
    for w in words:
        w = _text(w if isinstance(w, str) else "", 60)
        if w and w.lower() not in {k.lower() for k in keywords}:
            keywords.append(w)
    topic = raw.get("topic") if isinstance(raw.get("topic"), str) and raw.get("topic") in {c for c, _, _ in topics()} else ""
    return {"keywords": keywords[:MAX_KEYWORDS], "topic": topic}


def keywords_from_text(text):
    """Keywords as typed in one field, separated by commas or semicolons."""
    import re as _re

    return archive_of({"keywords": [w for w in _re.split(r"[,;]", text or "")]})["keywords"]


def default_country(user=None, language=None):
    """The country a new survey starts with (DDI spatial coverage): German as the language of the survey means
    Germany as long as nothing else is known; other languages leave it open (Ralf 2026-10-27: from the organisation,
    else from the language; organisations name no country yet)."""
    return "DE" if (language or "").split("-")[0] == "de" else ""


# the choice "Auswahl der Interviewten" of a new interview guide: purposive first (Ralf 2026-10-27)
INTERVIEW_RECRUITMENT = ("purposive", "theoretical", "snowball", "convenience")


def interview_recruitment():
    names = recruitment()
    return {k: names[k] for k in INTERVIEW_RECRUITMENT}


def start_plan(form, language):
    """The research plan of a new interview guide or diary survey from its single page: who takes part (recruitment,
    population, country, region); the country starts from the language (default_country) when the page sends none."""
    how, _, card_kind = (form.get("recruitment") or "").partition(":")
    country = form.get("country") if "country" in form else default_country(language=language)
    sample = {"target": None, "recruitment": how if how in RECRUITMENT else "", "note": "", "exclusions": None,
              "response": None, "panel_kind": _pick(card_kind or form.get("panel_kind"), PANEL_KINDS),
              "random_kind": _pick(form.get("random_kind"), RANDOM_KINDS), "population": _text(form.get("population"), 300),
              "country": _country(country), "region": _text(form.get("region"), 120)}
    return {"sample": sample}


def apply_who(survey, form):
    """"Wer nimmt teil?" of a diary survey or an interview guide in the setup wizard (Ralf 2026-10-27): recruitment,
    population, country and region into the research plan, only the fields the form sends; the rest of the plan
    stays."""
    plan = dict(survey.study_plan or {})
    sample = dict(plan.get("sample") or {})
    new = start_plan(form, survey.default_language)["sample"]
    for key, fields in (("recruitment", ("recruitment",)), ("panel_kind", ("recruitment", "panel_kind")),
                        ("random_kind", ("random_kind",)), ("population", ("population",)), ("country", ("country",)),
                        ("region", ("region",))):
        if any(f in form for f in fields):
            sample[key] = new[key]
    plan["sample"] = sample
    survey.study_plan = plan


def countries():
    """{code: name} for the country of the survey: the countries of app/geoip.py (ISO 3166-1 alpha-2)."""
    from app import geoip

    lang = "en" if english() else "de"
    return {"": "not yet decided" if lang == "en" else "noch offen", **dict(geoip.country_options(lang))}


def _country(value):
    from app import geoip

    return value if isinstance(value, str) and value in {c for c, _, _ in geoip.COUNTRIES} else ""


def analyses(survey=None):
    """The methods; "Strukturgleichungsmodell" for everyone since 2026-10-11.
    Without a survey (labels of stored results) all of them."""
    out = dict(_local(ANALYSES, ANALYSES_EN))  # a copy: _local hands the German table itself back
    if survey is not None:  # no choices: the kind "Wirkung über die Zeit" brings the one, a diary study the other
        out.pop("panel", None)
        out.pop("mixed", None)
    return out if survey is None or _sem_on(survey) else {k: v for k, v in out.items() if k not in SEM_ANALYSES}


def bases():
    return _local(BASES, BASES_EN)


# the methods in short, for the automatic choice in the narrow field of a hypothesis (UI/UX audit 2026-10-07, W11)
ANALYSES_SHORT = {"": "", "r": "Pearson", "rho": "Spearman", "t": "t-Test (Welch)", "anova": "Varianzanalyse (Welch)",
                  "u": "Mann-Whitney-U", "kw": "Kruskal-Wallis", "chi2": "χ²-Test", "describe": "beschreiben",
                  "sem": "Strukturgleichungsmodell", "panel": "Cross-Lagged-Panel", "mixed": "Mehrebenenmodell"}
ANALYSES_SHORT_EN = {"t": "t-test (Welch)", "anova": "ANOVA (Welch)", "u": "Mann-Whitney U", "chi2": "χ² test",
                     "describe": "describe", "sem": "structural equation model", "panel": "cross-lagged panel",
                     "mixed": "multilevel model"}


def short_analyses():
    return _local(ANALYSES_SHORT, ANALYSES_SHORT_EN)


def questions_later(survey):
    """Whether the questionnaire holds nothing a hypothesis would name yet but what the setup wizard put there itself
    (the block "Demographische Angaben" with age and gender): the wizard then asks for the questions later (W4)."""
    if survey is None or survey.id is None:
        return True
    if _randomizers(survey):
        return False
    return not any(item.kind.collects_answer and item.type != "consent"
                   for block in survey.blocks if not (block.settings or {}).get("demographics") for item in block.items)


def design_choices():
    return {"": de_en("noch offen", "not yet decided"), **designs()}


def finder():
    """FINDER in the language of the page: {goal: (design, text, where)}."""
    if not english():
        return FINDER
    return {goal: (f[0],) + FINDER_EN[goal] for goal, f in FINDER.items()}
MAX_HYPOTHESES = 20
SEM_METHODS = ("cb", "pls")
LANG_GROUPS = "_lang"  # the language of the survey as the groups of the test of invariance (point 4)
SINGLE_REL = 0.7  # the reliability a single question in the model gets unless typed otherwise (Petrescu, 2013)
MAX_CONTROLS = 20


def _reliability(value):
    """A reliability as typed (decimal comma or point), between 0.1 and 0.99, None otherwise."""
    try:
        x = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(x, 2) if 0.1 <= x <= 0.99 else None


def sem_options(raw):
    """The settings of the structural equation model beside the method (SEM extensions 2026-10-17): "controls"
    [variable names] get a path onto every variable the model explains, without a hypothesis and outside Holm's
    correction (point 6); "single" {variable name: reliability} are single questions that enter as a construct with
    that reliability, covariance-based (point 7); "formative" [variable names] are scales whose statements form the
    construct (PLS mode B, point 9); "invariance" a variable with groups (or "_lang", the language of the survey)
    over which the measurement model is tested for invariance (point 4); "missing" "fiml" takes cases with gaps in
    by full information ML (point 2, otherwise complete cases); "ordinal" "ordinal" treats the statements as ordered
    categories with WLSMV (point 3, otherwise as numbers with ML); "second" [variable names] are instruments with
    subscales that enter as one factor of the second order over their subscales (point 8); "alternative" ["h:<id>"
    (the paths of a hypothesis), "d:<id>" (the direct effect of a mediation)] what a competing model planned in advance
    leaves out (point 11); "dropped" {column: reason} statements left out of the model (model versions 2026-10-26);
    "global" {variable name of a formative scale: variable name} the global measure of its
    redundancy analysis (formative check 2026-10-26, E1); "hoc" [{"id", "name", "parts": [scale codes]}] constructs of a
    higher order over several scales (PLS, higher-order constructs 2026-10-26, E6) and "higher_formative" [keys of
    constructs of a higher order: instrument codes of "second" or hoc ids] those formed by their parts (E7). Anything
    else read as empty."""
    raw = raw if isinstance(raw, dict) else {}
    controls = [_text(c, 64) for c in raw.get("controls") or [] if isinstance(c, str) and c.strip()]
    single = {}
    found = raw.get("single") if isinstance(raw.get("single"), dict) else {}
    for code, rel in list(found.items())[:MAX_HYPOTHESES * 3]:
        rel = _reliability(rel)
        if isinstance(code, str) and code.strip() and rel is not None:
            single[_text(code, 64)] = rel
    formative = [_text(c, 64) for c in raw.get("formative") or [] if isinstance(c, str) and c.strip()]
    invariance = raw.get("invariance") if isinstance(raw.get("invariance"), str) else ""
    hoc = []
    for k, g in enumerate([g for g in raw.get("hoc") or [] if isinstance(g, dict)][:MAX_HOC], 1):
        parts = list(dict.fromkeys(_text(c, 64) for c in g.get("parts") or [] if isinstance(c, str) and c.strip()))
        name = _text(g.get("name"), 80) if isinstance(g.get("name"), str) else ""
        if len(parts) >= 2 and name.strip() and not any(c in p["parts"] for p in hoc for c in parts):
            hoc.append({"id": f"hoc{len(hoc) + 1}", "name": name.strip(), "parts": parts})
    higher_formative = list(dict.fromkeys(_text(c, 64) for c in raw.get("higher_formative") or []
                                          if isinstance(c, str) and c.strip()))[:MAX_HYPOTHESES * 3]
    # statements left out of the model, each with its reason (model versions 2026-10-26, E8): {column: reason}
    found = raw.get("dropped") if isinstance(raw.get("dropped"), dict) else {}
    dropped = {_text(c, 64): _text(r, 200) for c, r in list(found.items())[:50]
               if isinstance(c, str) and isinstance(r, str) and c.strip() and r.strip()}
    # the global measure of a formative scale or of a formative construct of a higher order (redundancy analysis)
    found = raw.get("global") if isinstance(raw.get("global"), dict) else {}
    glob = {_text(c, 64): _text(g, 64) for c, g in list(found.items())[:MAX_HYPOTHESES * 3]
            if isinstance(c, str) and isinstance(g, str) and c.strip() and g.strip() and c != g
            and (c in formative or c in higher_formative)}
    return {"controls": list(dict.fromkeys(controls))[:MAX_CONTROLS], "single": single,
            "formative": list(dict.fromkeys(formative))[:MAX_HYPOTHESES * 3], "global": glob,
            "invariance": _text(invariance, 64),
            "missing": "fiml" if raw.get("missing") == "fiml" else "",
            "alternative": list(dict.fromkeys(_text(c, 40) for c in raw.get("alternative") or []
                                              if isinstance(c, str) and re.fullmatch(r"[hd]:[\w-]{1,32}", c)))[:MAX_HYPOTHESES * 2],
            "second": list(dict.fromkeys(_text(c, 64) for c in raw.get("second") or [] if isinstance(c, str) and c.strip()))[:MAX_HYPOTHESES * 3],
            "ordinal": "ordinal" if raw.get("ordinal") == "ordinal" else "",
            "hoc": hoc, "higher_formative": higher_formative, "dropped": dropped}
MAX_HOC = 3  # constructs of a higher order over several scales in one plan


def _formative_of(form, codes):
    """The formative scales of the plan form: the switch reflective/formative of each scale ("sem_mode_<code>",
    formative check 2026-10-26, E6) or, from an older form, the ticks "sem_formative"."""
    found = [c for c in form.getlist("sem_formative") if c in codes]
    found += [k[len("sem_mode_"):] for k in form if k.startswith("sem_mode_") and form.get(k) == "formative"
              and k[len("sem_mode_"):] in codes]
    return list(dict.fromkeys(found))


def diary_options(raw):
    """The settings of the multilevel model of a diary study (longitudinal and multilevel concept 2026-10-17, E8):
    "lagged" takes what explains from the person's entry before (the effect over time), "slope" lets the effect within
    persons vary between them (a random slope). Anything else read as off."""
    raw = raw if isinstance(raw, dict) else {}
    return {"lagged": raw.get("lagged") is True, "slope": raw.get("slope") is True}


TEXT = 600


def _goals(raw):
    """The research goals of a stored or read plan, in the order of GOALS: several since 2026-10-06 (Ralf: compare
    groups AND measure preferences in one survey), a plan from before has one in "goal"."""
    picked = raw.get("goals") if isinstance(raw.get("goals"), list) else [raw.get("goal")]
    return [g for g in GOALS if g in picked]


def plan_of(survey):
    """The stored plan with every part present (empty where not filled). Goal and design are read as the page
    offers them now: a plan saved before 2026-10-06 can hold a former method design ("conjoint", "maxdiff", "psm"),
    which DESIGNS no longer has (design_key makes it "cross"; looked up as it was, the settings page answered 500)."""
    raw = copy.deepcopy(survey.study_plan or {})  # changed in place by the callers: the stored value stays as it was
    goals = _goals(raw)
    # "goal": the first of them, for what reads one aim (the AI helpers, the study templates)
    return {"goal": goals[0] if goals else "", "goals": goals, "design": design_key(raw.get("design")), "hypotheses": list(raw.get("hypotheses") or []),
            "sem_method": raw.get("sem_method") if raw.get("sem_method") in SEM_METHODS else "",
            "sample": {"target": None, "recruitment": "", "note": "", "exclusions": None, "response": None,
                       "panel_kind": "", "random_kind": "", "population": "", "country": "", "region": "",
                       **(raw.get("sample") or {})},
            "rules": raw.get("rules", ""), "holm": raw.get("holm") is True,
            "deviations": list(raw.get("deviations") or []), "layout": layout_of(raw.get("layout")),
            "sem": sem_options(raw.get("sem")), "diary": diary_options(raw.get("diary")),
            "archive": archive_of(raw.get("archive"))}


LAYOUT_NODES = 200  # places kept for the drawing of the model; more variables than a plan can name
LAYOUT_RANGE = 20000  # pixels from the origin in each direction


def layout_of(raw):
    """The drawing of the model in the study plan (modell-zeichnen-konzept.md, section 4): {"v": 1, "nodes": {variable
    name: [x, y]}}, the centre of each variable in pixels on a grid of 8. Only how it looks: no deviation from the
    pre-registration, nothing the analysis reads but the arrangement of the diagram (F5). Anything else read as empty."""
    nodes = {}
    found = raw.get("nodes") if isinstance(raw, dict) and isinstance(raw.get("nodes"), dict) else {}
    for code, place in list(found.items())[:LAYOUT_NODES]:
        if not isinstance(code, str) or not code or len(code) > 64:
            continue
        try:
            x, y = (int(round(float(v))) for v in place)
        except (TypeError, ValueError):
            continue
        if abs(x) <= LAYOUT_RANGE and abs(y) <= LAYOUT_RANGE:
            nodes[code] = [x, y]
    return {"v": 1, "nodes": nodes}


def _randomizers(survey):
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "rand" and n.get("code")]


def measures(survey):
    """[(variable name, label)] of what a hypothesis can name: the elements that collect an answer (consent aside)
    and the randomizations of the flow (the condition a person was shown: what explains in an experiment). Elements
    in a loop stay out: they have one answer per pass and no column of their own (audit 2026-10-12, SEM-1)."""
    from app import flow

    out = [(n["code"], de_en("{code} · Randomisierung „{name}“", "{code} · randomisation “{name}”", code=n["code"],
                             name=n.get("name") or n["code"])) for n in _randomizers(survey)]
    in_loop = flow.loop_blocks(survey)
    for block in survey.blocks:
        if block.id in in_loop:
            continue
        for item in block.items:
            if item.kind.collects_answer and item.type != "consent":
                out.append((item.code, label(item)))
    return out


def construct_name(item):
    """The name of the validated scale an element was built from, short: "Wahrgenommene Nützlichkeit" for "TAM:
    Wahrgenommene Nützlichkeit (Perceived Usefulness)"; "" for other elements (UI/UX audit 2026-10-07, W11: three
    TAM scales share one instruction, the name tells them apart)."""
    from app import constructs
    from app.models import Construct

    key = constructs.toolbox_key(item)
    c = db.session.scalar(db.select(Construct).filter_by(key=key)) if key else None
    if c is None:
        return ""
    name = constructs.shown_text(c, "name") or ""
    name = name.split(": ", 1)[1] if ": " in name else name
    return re.sub(r"\s*\([^)]*\)$", "", name).strip()


def label(item):
    """How a list of the plan names an element: the variable name first, then the name of its construct or the start
    of its question, so that two elements of one type with the same text stay apart in a narrow field (UI/UX audit
    2026-10-05, V5; 2026-10-07, W11)."""
    from app.items.base import plain

    name = construct_name(item)
    if name:
        return f"{item.code} · {name}"
    text = " ".join(plain(item.text("question", item.survey.default_language) or "").split())
    if len(text) > 60:
        text = text[:58].rstrip() + " …"
    return f"{item.code} · {text}" if text else item.code


def same(h):
    """Whether a hypothesis names one question as what explains and what is explained (V1): no test is possible,
    a correlation of a question with itself is 1."""
    return bool(h and h.get("iv") and h.get("iv") == h.get("dv"))


def _item(survey, code):
    """The element or the randomization of that variable name, None if there is none."""
    if not code:
        return None
    found = next((i for b in survey.blocks for i in b.items if i.code == code), None)
    return found if found is not None else next((n for n in _randomizers(survey) if n["code"] == code), None)


def conditions(survey, node):
    """The names of a randomization's conditions in the order of the flow, as the export row names the condition
    shown (flow.child_label): the groups of a hypothesis, "A" the first (audit 2026-10-12, S-4)."""
    from app import flow

    return [flow.child_label(survey, c) for c in node.get("children") or []]


def _level(item):
    if item is None:
        return None
    if isinstance(item, dict):  # a randomization: its conditions
        return "nominal"
    if item.type == "multiple_choice":  # one 0/1 column per option, no single variable to test (audit 2026-10-12, S-6)
        return None
    level = item.kind.level(item)
    if item.type == "likert" and len((item.config or {}).get("statements") or []) >= 2:
        # a scale counts with its score (the mean of its statements, scales.row_score) at the level of the matrix:
        # metric where it is set so (a toolbox construct by default), else ordinal, tested with ranks and described
        # by the median (audit 2026-10-23, M2, Ralf: "a"; before, every matrix of two statements counted as metric)
        return "metric" if level == "metric" else "ordinal"
    return level if level in ("nominal", "ordinal", "metric") else None


def _groups(item, keep=None):
    """How many groups a nominal or ordinal element splits into (its options, or the conditions of a
    randomization showing one of them), 0 when unknown. keep: the answer codes a hypothesis compares ("nur diese
    Gruppen vergleichen", UI/UX audit 2026-10-07, W1); empty = all."""
    if item is None:
        return 0
    if isinstance(item, dict):
        return len(item.get("children") or []) if (item.get("k") or 1) == 1 else 0
    options = (item.config or {}).get("options") or []
    keep = {str(k) for k in keep or ()}
    return len([o for o in options if not o.get("missing") and (not keep or str(o.get("code")) in keep)])


GROUP_KINDS = ("difference", "causal")


def _keep(h):
    """The answer codes a hypothesis compares, [] for all (stored only when some are left out)."""
    return [str(c) for c in (h or {}).get("groups") or []]


def group_options(survey, h):
    """The groups a hypothesis "Unterschied zwischen Gruppen" (or "Wirkung") compares, for the line under the field
    (W1): [(code, label, compared)] of the answers of what explains that are not missing; [] where that is no element
    with answers (a randomization, a metric question, nothing chosen)."""
    from app.items.base import plain

    if not h or h.get("kind") not in GROUP_KINDS or same(h):
        return []
    iv = _item(survey, h.get("iv"))
    if iv is None or isinstance(iv, dict) or _level(iv) not in ("nominal", "ordinal"):
        return []
    options = [o for o in (iv.config or {}).get("options") or [] if not o.get("missing") and o.get("code") not in (None, "")]
    if len(options) < 2:
        return []
    try:
        shown = {str(o["code"]): plain(o["label"]) for o in iv.kind.options(iv, survey.default_language)}
    except (AttributeError, KeyError, TypeError):
        shown = {}
    keep = set(_keep(h))
    return [(str(o["code"]), (shown.get(str(o["code"])) or str(o["code"])).rstrip(":"), not keep or str(o["code"]) in keep)
            for o in options]


# words that name a group in the text of a hypothesis beyond the answer itself: "Frauen" for "weiblich"
GROUP_WORDS = {"weiblich": ("frau", "weib", "female", "wom"), "männlich": ("mann", "männ", "male", "men")}


def names_two(survey, h):
    """Whether the text of a hypothesis names two of its groups while the test compares more (W1): "Frauen und
    Männer unterscheiden sich …" with five answers to the gender question."""
    groups = [(code, label) for code, label, compared in group_options(survey, h) if compared]
    if len(groups) <= 2:
        return False
    words = re.findall(r"\w+", (h.get("text") or "").lower())

    def named(label):
        label = label.lower()
        stems = GROUP_WORDS.get(label, ()) + tuple(w[:5] for w in re.findall(r"\w+", label) if len(w) >= 4)
        return any(w.startswith(s) and (s not in ("men", "male") or w in ("men", "male", "males"))
                   for w in words for s in stems)

    return sum(1 for _, label in groups if named(label)) == 2


def suggest(survey, h):
    """The analysis that fits a hypothesis: from its kind and the levels of what explains and what is explained."""
    if h.get("kind") in ("mediation", "effect", "moderation"):  # only the structural equation model tests these
        return "sem"
    if h.get("kind") == "lagged":  # only the cross-lagged panel model over the waves (app/panel_models.py)
        return "panel"
    iv, dv = _item(survey, h.get("iv")), _item(survey, h.get("dv"))
    a, b = _level(iv), _level(dv)
    if untested(h):  # no test: an open question or a descriptive assumption
        return "describe"
    if same(h) or not (a and b):
        return ""
    scale = bool(_scale_size(iv))  # a scale score forms no groups, whatever its level (M2)
    if b == "nominal" and a in ("nominal", "ordinal") and not scale:
        # a nominal outcome has no order: neither ranks nor means, only the cross table (χ² with Cramér's V), which
        # app/hypotheses.py also computes for ordered groups (audit 2026-10-10, B-16)
        return "chi2"
    if b == "nominal":  # metric against nominal: no test of this kind, the warning of nominal_mismatch() says why
        return ""
    if a == "nominal" or (h.get("kind") in ("difference", "causal") and a == "ordinal" and not scale
                          and _groups(iv, _keep(h)) <= 7):
        two = _groups(iv, _keep(h)) == 2
        if b == "metric":
            return "t" if two else "anova"
        return "u" if two else "kw"
    if a == "metric" and b == "metric":
        return "r"
    return "rho"


def _scale_size(item):
    """The number of statements of a Likert scale that the plan treats as one score, 0 for anything else."""
    if item is None or isinstance(item, dict) or item.type != "likert":
        return 0
    n = len((item.config or {}).get("statements") or [])
    return n if n >= 2 else 0


def metric_codes(survey):
    """The variable names of the elements that form no groups to compare (a scale score, ordinal or metric, a number).
    The form greys them out under "Gruppen" and "Bedingung" (UI/UX audit 2026-10-09, X3)."""
    return {item.code for block in getattr(survey, "blocks", None) or [] for item in block.items
            if item.kind.collects_answer and (_level(item) == "metric" or _scale_size(item))}


def group_mismatch(survey, h):
    """A warning when a hypothesis of a group kind ("Unterschied", "Wirkung") names a metric element as its groups or
    condition (X3): a scale forms no groups, and the suggestion would silently be a correlation. "" otherwise."""
    if not h or h.get("kind") not in GROUP_KINDS or same(h):
        return ""
    iv = _item(survey, h.get("iv"))
    if iv is None or isinstance(iv, dict) or (_level(iv) != "metric" and not _scale_size(iv)):
        return ""
    what = de_en("eine Skala", "a scale") if _scale_size(iv) else de_en("eine Zahl", "a number")
    return de_en("„{code}“ ist {what}, keine Gruppenfrage. Für Skala gegen Skala passt die Art „Zusammenhang“; "
                 "für Gruppen eine Auswahlfrage oder eine Randomisierung wählen.",
                 "“{code}” is {what}, not a group question. For scale against scale the type “Relationship” fits; "
                 "for groups choose a choice question or a randomisation.", code=iv.code, what=what)


def nominal_mismatch(survey, h):
    """A warning when what is explained is nominal and what explains is metric (audit 2026-10-10, B-16): a
    correlation needs an order, and the results could not test it; as a group comparison with the nominal element as
    the groups it is a t-test or an analysis of variance. "" otherwise (and where group_mismatch() already warns)."""
    if not h or h.get("kind") in (*UNTESTED, "mediation", "effect", "moderation", "lagged") or same(h) or group_mismatch(survey, h):
        return ""
    iv = _item(survey, h.get("iv"))
    if (_level(iv) != "metric" and not _scale_size(iv)) or _level(_item(survey, h.get("dv"))) != "nominal":
        return ""
    if _level(iv) == "ordinal":  # an ordinal scale score (M2): compared by ranks
        return de_en("„{dv}“ ist nominal (Antworten ohne Reihenfolge), „{iv}“ ein ordinaler Skalenwert: Dafür gibt es "
                     "keinen Zusammenhangstest. Als Art „Unterschied“ mit „{dv}“ als Gruppen und „{iv}“ als "
                     "verglichener Größe wird es ein Mann-Whitney-U- oder Kruskal-Wallis-Test.",
                     "“{dv}” is nominal (answers without an order), “{iv}” an ordinal scale score: there is no test of "
                     "a relationship for that. As the type “Difference” with “{dv}” as the groups and “{iv}” as the "
                     "compared variable it becomes a Mann-Whitney U or Kruskal-Wallis test.",
                     iv=h.get("iv"), dv=h.get("dv"))
    return de_en("„{dv}“ ist nominal (Antworten ohne Reihenfolge), „{iv}“ metrisch: Dafür gibt es keinen "
                 "Zusammenhangstest. Als Art „Unterschied“ mit „{dv}“ als Gruppen und „{iv}“ als verglichener Größe "
                 "wird es ein t-Test oder eine Varianzanalyse.",
                 "“{dv}” is nominal (answers without an order), “{iv}” metric: there is no test of a relationship "
                 "for that. As the type “Difference” with “{dv}” as the groups and “{iv}” as the compared variable it "
                 "becomes a t-test or an analysis of variance.", iv=h.get("iv"), dv=h.get("dv"))


def multiple_note(survey, h):
    """A warning when a hypothesis names a multiple choice question (audit 2026-10-12, S-6): its answers are one
    ticked/not ticked per option, no single variable; the group comparison of the results page tests every option.
    "" otherwise."""
    if not h or untested(h):
        return ""
    for key in ("iv", "dv"):
        thing = _item(survey, h.get(key))
        if thing is not None and not isinstance(thing, dict) and thing.type == "multiple_choice":
            return de_en("„{code}“ ist eine Mehrfachauswahl: Jede Option ist eine eigene Variable (angekreuzt oder "
                         "nicht), daher prüft der Studienplan sie nicht. Je Option testet der Gruppenvergleich auf der "
                         "Ergebnisseite („Nach Gruppen aufteilen“); für eine Hypothese eine Einfachauswahl wählen.",
                         "“{code}” is a multiple choice question: every option is a variable of its own (ticked or "
                         "not), so the research plan does not test it. The group comparison on the results page "
                         "(“Split into groups”) tests each option; for a hypothesis choose a single choice question.",
                         code=thing.code)
    return ""


def reasons(survey, h):
    """Why the suggestion is what it is, for the line under the field (V7): a scale counts with its score."""
    out = []
    mismatch = multiple_note(survey, h) or group_mismatch(survey, h) or nominal_mismatch(survey, h)
    if mismatch:
        out.append(mismatch)
    named = roles(h.get("kind"))
    for key, role in (("iv", named["iv"]), ("dv", named["dv"])):
        thing = _item(survey, h.get(key))
        n = _scale_size(thing)
        if n and _level(thing) == "metric":
            out.append(de_en("{role}: Skalenwert aus {n} Aussagen, als metrisch behandelt",
                             "{role}: scale score from {n} statements, treated as metric", role=role, n=n))
        elif n:  # the level of the matrix is ordinal (M2)
            out.append(de_en("{role}: Skalenwert aus {n} Aussagen, als ordinal behandelt (Rangtests, Median); "
                             "metrisch, wenn das Messniveau der Matrix so gesetzt ist",
                             "{role}: scale score from {n} statements, treated as ordinal (rank tests, median); "
                             "metric when the level of measurement of the matrix is set so", role=role, n=n))
    if h.get("kind") == "correlation" and h.get("analysis") == "sem":
        # since 2026-10-11 a relation without a direction in the model (modell-zeichnen-konzept.md, F4); before, it
        # was a path from A to B
        out.append(de_en("im Modell ein Zusammenhang ohne Richtung; für einen gerichteten Pfad die Art „Einfluss“ "
                         "wählen", "in the model a relationship without a direction; for a directed path choose the "
                         "type “Influence”"))
    return out


def test_reason(survey, h):
    """Why the results test a hypothesis as they do, one line under its method on the results page (UI/UX audit
    2026-10-07, W14; Ralf: a line per hypothesis, no fold): from the levels of both variables and the number of
    groups, as suggest() chose; a method picked in the plan says so. "" where no test is planned."""
    if not h or untested(h) or same(h):
        return ""
    suggested = suggest(survey, h)
    analysis = h.get("analysis") or suggested
    k = _groups(_item(survey, h.get("iv")), _keep(h))
    many = de_en("{k} Gruppen", "{k} groups", k=k) if k > 2 else de_en("mehrere Gruppen", "several groups")
    # neutral for every kind of hypothesis, whose variables have names of their own (UI/UX audit 2026-10-09, X4)
    dv = de_en("die verglichene Größe", "the compared variable")
    why = {
        "r": de_en("beide Variablen metrisch: Pearsons Korrelation misst den linearen Zusammenhang.",
                   "both variables metric: Pearson's correlation measures the linear relation."),
        "rho": de_en("mindestens eine Größe nur geordnet (ordinal): Spearmans Rangkorrelation braucht keine gleichen "
                     "Abstände.", "at least one variable only ordered (ordinal): Spearman's rank correlation needs no "
                     "equal distances."),
        "t": de_en("zwei Gruppen, {dv} metrisch: Welchs t-Test setzt keine gleichen Streuungen voraus.",
                   "two groups, {dv} metric: Welch's t-test does not assume equal spreads.", dv=dv),
        "anova": de_en("{many}, {dv} metrisch: Die Varianzanalyse nach Welch setzt keine gleichen Streuungen voraus.",
                       "{many}, {dv} metric: Welch's analysis of variance does not assume equal spreads.", many=many,
                       dv=dv),
        "u": de_en("zwei Gruppen, {dv} nur geordnet: Mann-Whitney-U vergleicht die Lage ohne Mittelwerte.",
                   "two groups, {dv} only ordered: Mann-Whitney U compares the location without means.", dv=dv),
        "kw": de_en("{many}, {dv} nur geordnet: Kruskal-Wallis vergleicht die Lage ohne Mittelwerte.",
                    "{many}, {dv} only ordered: Kruskal-Wallis compares the location without means.", many=many, dv=dv),
        "chi2": de_en("beide Variablen nominal: Der χ²-Test prüft, ob die Verteilung der einen von der anderen "
                      "abhängt.", "both variables nominal: the χ² test checks whether the distribution of the one "
                      "depends on the other.") if _level(_item(survey, h.get("iv"))) == "nominal" else
        de_en("{dv} nominal (ohne Reihenfolge): Der χ²-Test prüft, ob ihre Verteilung von den Gruppen abhängt.",
              "{dv} nominal (without an order): the χ² test checks whether its distribution depends on the groups.",
              dv=dv),
        "sem": de_en("Vermittlung oder Pfad im Modell: Das Strukturgleichungsmodell prüft die so markierten Hypothesen "
                     "gemeinsam.", "mediation or a path in the model: the structural equation model tests the "
                     "hypotheses marked so together."),
        "panel": de_en("Wirkung über die Zeit: Das Cross-Lagged-Panel-Modell über die Wellen prüft den Pfad von X in "
                       "einer Welle auf Y in der nächsten, ab drei Wellen getrennt von stabilen Unterschieden zwischen "
                       "Personen (RI-CLPM).", "effect over time: the cross-lagged panel model over the waves tests the "
                       "path from X in one wave to Y in the next, from three waves on apart from stable differences "
                       "between people (RI-CLPM)."),
    }.get(analysis, "")
    if not why:  # no test, but a warning why (B-16)
        extra = reasons(survey, h)
        return "; ".join(extra) if extra and not h.get("analysis") else ""
    if h.get("analysis") and h["analysis"] != suggested:
        text = de_en("Im Studienplan so gewählt", "Chosen so in the research plan") + (
            de_en(" (vorgeschlagen wäre: {m}).", " (the suggestion would be: {m}).", m=analyses()[suggested])
            if suggested else ".")
    else:
        text = de_en("Warum dieser Test? {why}", "Why this test? {why}", why=why[:1].upper() + why[1:])
    extra = reasons(survey, h)
    return text + (" " + "; ".join(extra) + "." if extra else "")


ALPHA = 0.05  # as the results per hypothesis (app/hypotheses.py): two-sided at .05


def _chi2_df(survey, h):
    a, b = _groups(_item(survey, h.get("iv")), _keep(h)), _groups(_item(survey, h.get("dv")))
    return max(1, (a - 1) * (b - 1)) if a >= 2 and b >= 2 else 1


def power_of(survey, h):
    """The power analysis of one hypothesis (concept poweranalyse-konzept.md, PA1): {"analysis", "measure" (r, d, f,
    w or None where no test is planned), "effect", "level", "basis", "note", "groups", "df", "alpha", "n" (completed
    participations the test needs, None until an effect is given)}."""
    from app import power

    analysis = (h.get("analysis") or suggest(survey, h)) if h and not untested(h) and not same(h) else ""
    m = power.measure(analysis)
    iv = _item(survey, h.get("iv")) if h else None
    groups = max(2, _groups(iv, _keep(h))) if analysis in ("anova", "kw") else 2
    df = _chi2_df(survey, h) if analysis == "chi2" else 1
    level = h.get("power") if h and h.get("power") in POWERS else 0.8
    effect = h.get("effect") if h else None
    alpha = alpha_for(survey)
    n = power.needed(analysis, effect, level, alpha, groups, df) if m and effect is not None else None
    if analysis == "sem" and effect is not None and power.valid_effect(analysis, effect):
        from app import sem_model  # the path(s) in the planned model (Satorra & Saris, 1985)

        if h.get("kind") == "correlation":  # a relation without a direction (F4): planned as a correlation r
            n = power.needed("r", effect, level, alpha)
        else:
            n = sem_model.planned_n(survey, h, level, alpha)
    return {"analysis": analysis, "measure": m, "effect": effect, "level": level, "basis": (h or {}).get("basis", ""),
            "note": (h or {}).get("basis_note", ""), "groups": groups, "df": df, "alpha": alpha, "n": n,
            "valid": m is not None and effect is not None and power.valid_effect(analysis, effect)}


def tested(survey):
    """The hypotheses with a planned test (not open, not "nur beschreiben", both variables apart): what a correction
    for several hypotheses counts."""
    from app import power

    out = []
    for h in plan_of(survey)["hypotheses"]:
        analysis = h.get("analysis") or suggest(survey, h)
        # a path of the structural equation model is a test of its own (app/sem_model.py)
        if not untested(h) and not same(h) and (power.measure(analysis) or analysis in ("sem", "panel")):
            out.append(h)
    return out


def alpha_for(survey):
    """The significance level of each test: .05, or with the correction after Holm (1979) .05 / k for k tested
    hypotheses. Holm's first step is the Bonferroni level; planning every test with it errs on the safe side, the exact
    power of the step-down procedure would need a simulation (Festlegung von Askurious)."""
    if survey is None or not plan_of(survey).get("holm"):
        return ALPHA
    k = len(tested(survey))
    return ALPHA / k if k >= 2 else ALPHA


def alpha_text(alpha, lang="de"):
    """".05" as APA writes it (",05" in German), ".025", ".017"."""
    text = f"{alpha:.3f}".rstrip("0") if round(alpha, 2) != alpha else f"{alpha:.2f}"
    text = text[1:] if text.startswith("0") else text
    return text if lang == "en" else text.replace(".", ",")


SIMMONS = ("Simmons, J. P., Nelson, L. D., & Simonsohn, U. (2011). False-positive psychology: Undisclosed flexibility in "
           "data collection and analysis allows presenting anything as significant. Psychological Science, 22(11), "
           "1359–1366. https://doi.org/10.1177/0956797611417632")
HOLM = ("Holm, S. (1979). A simple sequentially rejective multiple test procedure. Scandinavian Journal of Statistics, "
        "6(2), 65–70.")


def power_summary(survey):
    """What the power analyses of all hypotheses ask of the sample, None while none has a number: {"rows": [(number
    of the hypothesis, its power_of)], "n" (the largest, Festlegung von Askurious: one sample must serve every test),
    "by" (the hypotheses that need it), "exclusions" (%), "completions" (n before the quality rules leave out the
    expected share)}."""
    from app import sample_model

    plan = plan_of(survey)
    rows = [(i, power_of(survey, h)) for i, h in enumerate(plan["hypotheses"], 1)]
    numbers = [p["n"] for _, p in rows if p["n"]]
    if not numbers:
        return None
    n = max(numbers)
    excl = plan["sample"].get("exclusions")
    excl = EXCLUSIONS if excl is None else excl
    return {"rows": rows, "n": n, "by": [i for i, p in rows if p["n"] == n], "exclusions": excl,
            "completions": sample_model.completions(n, excl)}


def power_line(survey, h, lang="de"):
    """The power analysis of a hypothesis in one phrase for the pre-registration and the methods draft, "" while it
    has no number: "Poweranalyse: r = 0,30 (Literatur oder Metaanalyse: …), Power 80 %, α = ,05 zweiseitig, N = 84"."""
    p = power_of(survey, h)
    if not p["n"]:
        return ""
    en = lang == "en"
    basis = (BASES_EN if en else BASES).get(p["basis"], "") if p["basis"] else ""
    why = ": ".join(x for x in (basis, p["note"]) if x)
    alpha = alpha_text(p["alpha"], lang)
    text = (f"{'power analysis' if en else 'Poweranalyse'}: {effect_text(p, lang)}" + (f" ({why})" if why else "")
            + f", {'power' if en else 'Power'} {round(p['level'] * 100)}{'%' if en else ' %'}, α = {alpha} {'two-sided' if en else 'zweiseitig'}"
            + f", N = {p['n']}")
    return text


def power_cohen():
    """Cohen's (1988) small, medium and large value of each effect size, for the hint at the field."""
    from app import power

    return power.COHEN


def calculator_args(survey, p):
    """The query of the sample size calculator that opens the planner of a hypothesis with its values."""
    if p["analysis"] == "sem":  # a path of the model: the calculator has the model test only (sem_model.rmsea_plan)
        return None
    design = {"r": "corr", "rho": "corr", "t": "groups", "u": "groups", "anova": "anova", "kw": "anova",
              "chi2": "chi2"}.get(p["analysis"])
    if not design:
        return None
    args = {"survey": survey.id, "design": design, "power": p["level"]}
    if p["alpha"] != ALPHA:  # Holm's α / k, as the plan computes with it (PA-5)
        args["alpha"] = p["alpha"]
    if p["effect"] is not None:
        args[{"corr": "effect", "groups": "effect", "anova": "f", "chi2": "w"}[design]] = p["effect"]
    if design == "anova":
        args["groups"] = p["groups"]
    if design == "chi2":
        args["rows"], args["cols"] = p["df"] + 1, 2  # (rows − 1)(cols − 1) = df
    excl = ((getattr(survey, "study_plan", None) or {}).get("sample") or {}).get("exclusions")
    args["excl"] = EXCLUSIONS if excl is None else excl  # the completions of the calculator as the plan's (W2)
    return args


def effect_text(p, lang=None):
    """"r = 0,30" (German decimal comma) or "r = 0.30" of a power analysis."""
    from app import lang as ui_lang

    en = (lang or ui_lang.current()) == "en"
    value = effect_number(p["effect"]) if p["effect"] is not None else "–"
    if en and p["measure"] == "r":  # APA: no leading zero for what cannot exceed 1 (audit 2026-10-10, B-20)
        value = value.replace("0.", ".", 1)
    return f"{p['measure']} = {value if en else value.replace('.', ',')}"


def effect_number(x):
    """An expected effect with as many decimals as stored (two, three where the third is not 0: _effect keeps
    three, a pilot study gives 0.125), so that the N computed from it fits the number shown: "0.12" beside the N of
    0.125 was 42 persons short (audit 2026-10-10, B-19). A decimal point."""
    from app import units

    three = units.fixed(x, 3)
    return three if not three.endswith("0") else units.fixed(x, 2)


def direction_hint(survey, h):
    """What "A" means in the direction of a group comparison (V4): the first group of what explains; with more
    than two groups the direction does not apply."""
    if not h or untested(h) or not h.get("iv") or same(h):
        return ""
    analysis = h.get("analysis") or suggest(survey, h)
    if analysis not in ("t", "u", "anova", "kw"):
        return ""
    iv = _item(survey, h.get("iv"))
    if analysis in ("anova", "kw") or _groups(iv, _keep(h)) > 2:
        return de_en("mehr als zwei Gruppen: ohne Richtung geprüft", "more than two groups: tested without direction")
    if isinstance(iv, dict):  # the first condition in the flow (S-4: before, the name of a node, which blocks lack)
        keep = set(_keep(h))
        names = [c for c in conditions(survey, iv) if not keep or c in keep]
        name = names[0] if names else ""
    else:
        from app.items.base import plain

        keep = set(_keep(h))
        options = [o for o in (iv.config or {}).get("options") or [] if not o.get("missing")
                   and (not keep or str(o.get("code")) in keep)]
        try:
            shown = {str(o["code"]): o["label"] for o in iv.kind.options(iv, iv.survey.default_language)}
        except (AttributeError, KeyError, TypeError):
            shown = {}
        name = plain(shown.get(str(options[0].get("code")), "")) if options else ""
    return de_en("„A“ ist „{name}“", "“A” is “{name}”", name=name) if name else ""


def status(survey):
    """What the plan holds, for the line in the head of its settings group and above Fragebogen and Ergebnisse:
    {"begun", "hypotheses", "measured", "target", "missing": [what a hypothesis lacks]}."""
    plan = plan_of(survey)
    hs = plan["hypotheses"]
    measured = [h for h in hs if h.get("kind") == "open"
                or (h.get("kind") in ONE_VARIABLE and _item(survey, h.get("dv")))
                or (_item(survey, h.get("iv")) and _item(survey, h.get("dv")) and not same(h))]
    return {"begun": bool(survey.study_plan) or bool(survey.research_question), "hypotheses": len(hs),
            "measured": len(measured), "target": plan["sample"]["target"], "plan": plan}


def findings(survey):
    """Findings of the pre-launch check: a hypothesis whose elements are missing or were deleted, one that tests a
    question against itself (an error, V1), and sample targets that do not agree (V10)."""
    from app.design import ERROR, WARNING, Finding

    out = []
    for n, h in enumerate(plan_of(survey)["hypotheses"], 1):
        if h.get("kind") == "open":
            continue
        if h.get("kind") in ONE_VARIABLE:  # one measured variable, no test (pre-test 2026-10-20, 6)
            if h.get("dv") and _item(survey, h["dv"]) is None:
                out.append(Finding(WARNING, de_en("Studienplan H{n}: Das Element „{codes}“ gibt es nicht mehr; "
                                                  "bitte im Reiter „Studienplan“ neu zuordnen.",
                                                  "Research plan H{n}: the element “{codes}” no longer exists; "
                                                  "please assign it again on the tab “Research plan”.",
                                                  n=n, codes=h["dv"]), link="study_plan.page", anchor=f"h{n}"))
            elif not h.get("dv"):
                out.append(Finding(WARNING, de_en("Studienplan H{n}: Noch nicht festgelegt, welche Frage die Annahme "
                                                  "misst ({dv}).",
                                                  "Research plan H{n}: not yet decided which question measures the "
                                                  "assumption ({dv}).", n=n, dv=roles(h.get("kind"))["dv"]),
                                   link="study_plan.page", anchor=f"h{n}"))
            continue
        if same(h):
            item = _item(survey, h["iv"])
            # the fix is on the tab "Studienplan", not in the element (UI/UX audit 2026-10-07, W17)
            named = roles(h.get("kind"))
            out.append(Finding(ERROR, de_en("Studienplan H{n}: {iv} und {dv} sind dieselbe Frage („{code}“); "
                                            "bitte im Reiter „Studienplan“ zwei verschiedene Fragen wählen.",
                                            "Research plan H{n}: {iv} and {dv} are the same question (“{code}”); "
                                            "please choose two different questions on the tab “Research plan”.",
                                            n=n, code=h["iv"], iv=named["iv"], dv=named["dv"]),
                               item_id=None if item is None or isinstance(item, dict) else item.id,
                               link="study_plan.page", anchor=f"h{n}"))
            continue
        gone = [h.get(k) for k in ("iv", "dv", "mv", "wv") if h.get(k) and _item(survey, h.get(k)) is None]
        if gone:
            out.append(Finding(WARNING, de_en("Studienplan H{n}: Das Element „{codes}“ gibt es nicht mehr; "
                                              "bitte im Reiter „Studienplan“ neu zuordnen.",
                                              "Research plan H{n}: the element “{codes}” no longer exists; "
                                              "please assign it again on the tab “Research plan”.",
                                              n=n, codes=", ".join(gone)), link="study_plan.page", anchor=f"h{n}"))
        elif not (h.get("iv") and h.get("dv")):
            named = roles(h.get("kind"))
            out.append(Finding(WARNING, de_en("Studienplan H{n}: Noch nicht festgelegt, welche Fragen die Hypothese "
                                              "messen ({iv} und {dv}).",
                                              "Research plan H{n}: not yet decided which questions measure the "
                                              "hypothesis ({iv} and {dv}).", n=n, iv=named["iv"], dv=named["dv"]),
                               link="study_plan.page", anchor=f"h{n}"))
        elif group_mismatch(survey, h) or nominal_mismatch(survey, h):
            out.append(Finding(WARNING, de_en("Studienplan H{n}: ", "Research plan H{n}: ", n=n)
                               + (group_mismatch(survey, h) or nominal_mismatch(survey, h)),
                               link="study_plan.page", anchor=f"h{n}"))
        elif h.get("kind") == "mediation" and not h.get("mv"):
            out.append(Finding(WARNING, de_en("Studienplan H{n}: Noch nicht festgelegt, über welche Frage die Wirkung "
                                              "vermittelt wird (Mittler).",
                                              "Research plan H{n}: not yet decided through which question the effect "
                                              "is mediated (mediator).", n=n), link="study_plan.page", anchor=f"h{n}"))
        elif h.get("kind") == "moderation" and not h.get("wv"):
            out.append(Finding(WARNING, de_en("Studienplan H{n}: Noch nicht festgelegt, welche Frage den Pfad "
                                              "moderiert (Moderator).",
                                              "Research plan H{n}: not yet decided which question moderates the path "
                                              "(moderator).", n=n), link="study_plan.page", anchor=f"h{n}"))
    # the targets against the sample model (S3): the same function as every page that shows the line
    from app import sample_model

    m = sample_model.of(survey)
    target = plan_of(survey)["sample"]["target"] or survey.max_responses
    if m and m["basis"] == "power" and target and target < m["completions"]:
        out.append(Finding(WARNING, de_en("Das Stichprobenziel {t} liegt unter dem, was die Poweranalyse im Studienplan "
                                          "verlangt ({h}): {line}.",
                                          "The sample target {t} is below what the power analysis of the research plan "
                                          "asks for ({h}): {line}.",
                                          t=target, line=sample_model.text(m),
                                          h=", ".join(f"H{i}" for i in m["by"])), link="study_plan.page"))
    targets = sample_targets(survey)
    if len({n for _, n in targets}) > 1:
        out.append(Finding(WARNING, de_en("Die Stichprobenziele passen nicht zusammen: {targets}. Bitte angleichen: "
                                          "Stichprobenziel im Reiter „Studienplan“, Feldende im Reiter „Datenerhebung“, "
                                          "Soll unter „Ablauf“ › „Quoten“.",
                                          "The sample targets do not agree: {targets}. Please align them: sample "
                                          "target on the tab “Research plan”, end of data collection on the tab “Data "
                                          "collection”, target under “Flow” › “Quotas”.",
                                          targets=", ".join(f"{where} {n}" for where, n in targets)),
                           link="study_plan.page"))
    return out


def sample_targets(survey):
    """[(where, number)] of the sample sizes the survey names: the target of the plan, the end after completed
    participations, and the sum of the targets of each quota (V10)."""
    out = []
    target = plan_of(survey)["sample"]["target"]
    if target:
        out.append((de_en("Studienplan", "research plan"), target))
    if survey.max_responses:
        out.append((de_en("Feldende", "end of data collection"), survey.max_responses))
    for quota in getattr(survey, "quotas", None) or []:
        total = sum(c.target or 0 for c in quota.cells)
        if total:
            out.append((de_en("Quote „{name}“", "quota “{name}”", name=quota.name or quota.code), total))
    return out


def rename_code(survey, old, new):
    """A variable was renamed in the editor: the plan follows. True if the plan changed. The caller holds the
    survey's row, read anew (edit_guard.lock): the plan rewritten is the one stored, not one loaded before."""
    return rename_codes(survey, {old: new})


def rename_codes(survey, names):
    """Several variables were renamed at once ({old: new}, e.g. by undoing a step): the plan follows, all names
    in one go, so that two names that swap places stay apart."""
    names = {old: new for old, new in (names or {}).items() if old and new and old != new}
    if not survey.study_plan or not names:
        return False
    plan = copy.deepcopy(survey.study_plan)
    changed = False
    for h in plan.get("hypotheses") or []:
        for key in ("iv", "dv", "mv", "wv"):
            if h.get(key) in names:
                h[key], changed = names[h[key]], True
    opts = plan.get("sem")
    if isinstance(opts, dict):  # the settings of the model name variables too
        if any(c in names for c in opts.get("controls") or []):
            opts["controls"], changed = [names.get(c, c) for c in opts["controls"]], True
        if isinstance(opts.get("single"), dict) and any(c in names for c in opts["single"]):
            opts["single"], changed = {names.get(c, c): r for c, r in opts["single"].items()}, True
        if any(c in names for c in opts.get("formative") or []):
            opts["formative"], changed = [names.get(c, c) for c in opts["formative"]], True
        if isinstance(opts.get("global"), dict) and any(c in names or g in names for c, g in opts["global"].items()):
            opts["global"], changed = {names.get(c, c): names.get(g, g) for c, g in opts["global"].items()}, True
        if any(c in names for c in opts.get("second") or []):
            opts["second"], changed = [names.get(c, c) for c in opts["second"]], True
        if any(c in names for c in opts.get("higher_formative") or []):
            opts["higher_formative"], changed = [names.get(c, c) for c in opts["higher_formative"]], True
        for g in opts.get("hoc") or []:  # the parts of a construct of a higher order
            if isinstance(g, dict) and any(c in names for c in g.get("parts") or []):
                g["parts"], changed = [names.get(c, c) for c in g["parts"]], True
        if opts.get("invariance") in names:
            opts["invariance"], changed = names[opts["invariance"]], True
    places = (plan.get("layout") or {}).get("nodes")
    if isinstance(places, dict) and any(code in names for code in places):  # the drawing keeps its places
        plan["layout"]["nodes"] = {names.get(code, code): xy for code, xy in places.items()}
        changed = True
    if changed:
        survey.study_plan = plan
    return changed


def _text(value, limit=TEXT):
    return " ".join((value or "").split())[:limit]


def _int(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 1_000_000 else None


def _effect(value):
    """An effect size as typed (decimal comma or point), None when it is no positive number."""
    try:
        x = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(x, 3) if 0 < x < 10 else None


def _percent(value):
    """A share in % (0 to 90) as a whole number, None when empty or out of range. A decimal (12.5 or 12,5, as the
    sample size calculator sends it) is rounded half up instead of dropped (code review 2026-10-07, F24)."""
    try:
        x = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    x = int(math.floor(x + 0.5))
    return x if 0 <= x <= 90 else None


def _rate(value):
    """A response rate in % (1 to 100), None when empty or out of range."""
    try:
        x = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if 1 <= x <= 100 else None


def _power_fields(effect, basis, basis_note, level):
    """The power analysis of one hypothesis as stored: expected effect, where it comes from, the power aimed at."""
    try:
        level = float(level)
    except (TypeError, ValueError):
        level = None
    out = {}
    if _effect(effect) is not None:
        out["effect"] = _effect(effect)
    if basis in BASES and basis:
        out["basis"] = basis
    if _text(basis_note):
        out["basis_note"] = _text(basis_note, 300)
    if level in POWERS:
        out["power"] = level
    return out


def from_form(survey, form):
    """The plan as sent by the settings group or the wizard step (rows of hypotheses as lists)."""
    old = {h.get("id"): h for h in plan_of(survey)["hypotheses"]}
    codes = {code for code, _ in measures(survey)}
    hypotheses = []
    ids = form.getlist("h_id")

    def column(name):  # a field a form of an older page does not send counts as empty
        values = form.getlist(name)
        return values + [""] * (len(ids) - len(values))
    rows = zip(ids, column("h_text"), column("h_kind"), column("h_dir"), column("h_iv"), column("h_dv"),
               column("h_analysis"), column("h_effect"), column("h_basis"), column("h_basis_note"), column("h_power"),
               column("h_mv"), column("h_wv"))
    for hid, text, kind, direction, iv, dv, analysis, effect, basis, basis_note, level, mv, wv in rows:
        text = _text(text)
        if not text:
            continue
        keep = old.get(hid) or {}
        # a row added on the page brings an id of its own ("n" and a number, from the row template), so that the
        # next save of the same row is not a new hypothesis again
        new_id = hid if re.fullmatch(r"n\d{1,12}", hid or "") else secrets.token_hex(4)
        h = {"id": hid if hid in old else new_id, "text": text,
             "kind": kind if kind in KINDS else "correlation", "direction": direction if direction in DIRECTIONS else "",
             # an element that is not (yet) in the questionnaire stays named, so nothing gets lost on the way
             "iv": iv if iv in codes else (keep.get("iv") if iv == keep.get("iv") else ""),
             "dv": dv if dv in codes else (keep.get("dv") if dv == keep.get("dv") else ""),
             "analysis": analysis if analysis in ANALYSES else "",
             **_power_fields(effect, basis, basis_note, level)}
        if h["kind"] in ("effect", "moderation"):  # only the model tests these
            h["analysis"] = "sem"
        if h["kind"] == "lagged":  # only the cross-lagged panel model over the waves
            h["analysis"] = "panel"
        if h["kind"] in ONE_VARIABLE:  # one measured variable, described: what explains moves over or goes
            h.update(dv=h["dv"] or h["iv"], iv="", analysis="describe", direction="")
        if h["kind"] == "moderation":  # the moderator, chosen like what explains
            h["wv"] = wv if wv in codes else (keep.get("wv") if wv == keep.get("wv") else "")
        if h["kind"] == "mediation":  # the mediator, chosen like what explains (app/sem_model.py)
            h["mv"] = mv if mv in codes else (keep.get("mv") if mv == keep.get("mv") else "")
        groups = _picked_groups(survey, h, form, hid, keep)
        if groups:
            h["groups"] = groups
        # added after the start: not part of the pre-registration, so its result counts as explorative (package E)
        if keep.get("after_start") or (hid not in old and survey.status != "draft"):
            h["after_start"] = True
        hypotheses.append(h)
    how, _, card_kind = (form.get("recruitment") or "").partition(":")  # a card of the wizard: "panel:access"
    sample = {"target": _int(form.get("sample_target")),
              "recruitment": how if how in RECRUITMENT else "",
              "note": _text(form.get("sample_note")),
              "exclusions": _percent(form.get("sample_exclusions")),
              # the response rate of an online panel: the invitations of the sample model (S3); kept while hidden
              "response": _rate(form.get("sample_response")),
              # the kind of panel or random sample; kept while hidden, read only with its recruitment (app/ddi.py)
              "panel_kind": _pick(card_kind or form.get("panel_kind"), PANEL_KINDS),
              "random_kind": _pick(form.get("random_kind"), RANDOM_KINDS),
              # who the survey speaks for, and where (DDI Universe and spatial coverage, app/ddi.py; Ralf 2026-10-27)
              "population": _text(form.get("population"), 300), "country": _country(form.get("country")),
              "region": _text(form.get("region"), 120)}
    # the aims: from the form when it shows them ("goals_shown"); the wizard asks them on its first step
    goals = _goals({"goals": form.getlist("goal")}) if "goals_shown" in form or "goal" in form else plan_of(survey)["goals"]
    out = {"goal": goals[0] if goals else "", "goals": goals,
           "design": design_key(form.get("design")),
           "hypotheses": hypotheses[:MAX_HYPOTHESES], "sample": sample, "rules": _text(form.get("rules"), 1200),
           "holm": form.get("holm") == "1"}
    # how the structural equation model is estimated; a form without the field keeps what was chosen
    method = form.get("sem_method") if "sem_method" in form else plan_of(survey)["sem_method"]
    out["sem_method"] = method if method in SEM_METHODS else ""
    # the further settings of the model; a form without them (the wizard) keeps the stored ones
    out["sem"] = plan_of(survey)["sem"]
    if "sem_shown" in form:
        single = {}
        for code in form.getlist("sem_single"):
            if code in codes:
                single[code] = _reliability(form.get(f"sem_rel_{code}")) or SINGLE_REL
        out["sem"] = sem_options({"controls": [c for c in form.getlist("sem_control") if c in codes], "single": single,
                                  "formative": _formative_of(form, codes),
                                  "global": {c: form.get(f"sem_global_{c}") for c in
                                             _formative_of(form, codes) + [k[len("sem_hmode_"):] for k in form
                                                                          if k.startswith("sem_hmode_")
                                                                          and form.get(k) == "formative"]
                                             if form.get(f"sem_global_{c}") in codes},
                                  "invariance": form.get("sem_invariance") if form.get("sem_invariance") in codes
                                  or form.get("sem_invariance") == LANG_GROUPS else "",
                                  "missing": form.get("sem_missing"), "ordinal": form.get("sem_ordinal"),
                                  "second": [c for c in form.getlist("sem_second") if c in codes],
                                  "hoc": [{"name": form.get(f"hoc_name_{k}") or "",
                                           "parts": [c for c in form.getlist(f"hoc_part_{k}") if c in codes]}
                                          for k in range(1, MAX_HOC + 1)],
                                  "higher_formative": [k[len("sem_hmode_"):] for k in form if k.startswith("sem_hmode_")
                                                       and form.get(k) == "formative"],
                                  "alternative": form.getlist("sem_alternative")})
    # the multilevel model of a diary study; a form without the fields keeps what was chosen
    out["diary"] = plan_of(survey)["diary"]
    if "diary_shown" in form:
        out["diary"] = diary_options({"lagged": form.get("diary_lagged") == "1", "slope": form.get("diary_slope") == "1"})
    # keywords and topic for archives (the survey's own fields, app/surveys.apply_project); kept here
    out["archive"] = plan_of(survey)["archive"]
    # the drawing of the model (static/model.js); a form without it (the wizard) keeps the stored one
    out["layout"] = plan_of(survey)["layout"]
    if "plan_layout" in form:
        from app.request_values import safe_loads

        try:  # safe_loads: a value nested too deeply is a ValueError too (audit 2026-10-11, N9)
            out["layout"] = layout_of(safe_loads(form.get("plan_layout") or "{}"))
        except ValueError:
            pass
    return out


def _picked_groups(survey, h, form, hid, keep):
    """The groups a hypothesis compares as the page sent them ("nur diese Gruppen vergleichen", W1): the ticked
    answers of what explains, [] when all are ticked (or none: nothing to compare is no choice). A form without the
    boxes keeps what was chosen; another question as what explains starts again with all of its groups."""
    offered = [code for code, _, _ in group_options(survey, {**h, "groups": []})]
    if not offered or keep.get("iv") != h.get("iv"):  # another question explains now: all its groups
        return []
    if f"h_grp_shown_{hid}" not in form:
        return [c for c in _keep(keep) if c in offered]
    ticked = set(form.getlist(f"h_grp_{hid}"))
    picked = [c for c in offered if c in ticked]
    return picked if picked and len(picked) < len(offered) else []


def _changes(before, after):
    """What a change after the start altered, in words, for the list of deviations."""
    out = []
    names = {"goals": "Erkenntnisziel", "design": "Studiendesign", "rules": "Ausschlussregeln",
             "holm": "Korrektur für mehrere Hypothesen", "sem_method": "Schätzung des Strukturgleichungsmodells",
             "sem": "Einstellungen des Strukturgleichungsmodells", "diary": "Mehrebenenmodell der Tagebuchstudie"}
    for key, label in names.items():
        if before.get(key) != after.get(key):
            out.append(label)
    if before.get("sample") != after.get("sample"):
        out.append("Stichprobe")
    b = {h["id"]: h for h in before.get("hypotheses") or []}
    a = {h["id"]: h for h in after.get("hypotheses") or []}
    for n, h in enumerate(after.get("hypotheses") or [], 1):
        if h["id"] not in b:
            out.append(f"H{n} neu")
        elif {k: v for k, v in h.items()} != b[h["id"]]:
            out.append(f"H{n} geändert")
    out += ["eine Hypothese entfernt"] * len(set(b) - set(a))
    return out


def apply_aims(survey, form):
    """Research question and aims from the wizard's first step "Worum geht es?" (Ralf 2026-10-27); the rest of the plan
    stays. After the start a change is a deviation, as on the plan page."""
    if "goals_shown" not in form and "research_question" not in form:
        return
    plan = dict(survey.study_plan or {})
    before = plan_of(survey)
    if "goals_shown" in form:
        plan["goals"] = _goals({"goals": form.getlist("goal")})
        plan["goal"] = plan["goals"][0] if plan["goals"] else ""
    if "research_question" in form:
        survey.research_question = (form.get("research_question") or "").strip()[:2000]
    if survey.id and survey.status != "draft" and before["goals"] != plan.get("goals", before["goals"]):
        plan["deviations"] = before["deviations"] + [{
            "at": _today(), "what": "Erkenntnisziele", "by": getattr(current_user, "name", "") or "",
            "by_id": getattr(current_user, "id", None)}]
    survey.study_plan = plan


def apply(survey, form):
    """Store what the form sent; after the start, note the change as a deviation (S7)."""
    if "research_question" in form:
        survey.research_question = (form.get("research_question") or "").strip()[:2000]
    before = plan_of(survey)
    after = from_form(survey, form)
    after["deviations"] = before["deviations"]
    if survey.status != "draft":
        changed = _changes(before, after)
        if changed:
            after["deviations"] = before["deviations"] + [{
                "at": _today(), "what": ", ".join(changed),
                "by": getattr(current_user, "name", "") or "", "by_id": getattr(current_user, "id", None)}]
    survey.study_plan = after
    # the design lays out the questionnaire once: conditions in a randomisation, a vignette loop (user test 2026-10-27)
    from app import design_layout

    laid_out = design_layout.lay_out(survey, before["design"], after["design"])
    if laid_out:
        flash(laid_out, "info")


def clean(raw):
    """A plan read from a survey file (app/transfer.py): only known keys and values, ids kept."""
    if not isinstance(raw, dict):
        return None
    hs = []
    for h in (raw.get("hypotheses") or [])[:MAX_HYPOTHESES]:
        if isinstance(h, dict) and _text(h.get("text")):
            hs.append({"id": str(h.get("id") or secrets.token_hex(4))[:16], "text": _text(h.get("text")),
                       "kind": h.get("kind") if h.get("kind") in KINDS else "correlation",
                       "direction": h.get("direction") if h.get("direction") in DIRECTIONS else "",
                       **({"iv": "", "dv": _text(h.get("dv") or h.get("iv"), 64)} if h.get("kind") in ONE_VARIABLE
                          else {"iv": _text(h.get("iv"), 64), "dv": _text(h.get("dv"), 64)}),
                       "analysis": "describe" if h.get("kind") in ONE_VARIABLE else
                       "sem" if h.get("kind") in ("effect", "moderation") else
                       "panel" if h.get("kind") == "lagged" else h.get("analysis") if h.get("analysis") in ANALYSES else "",
                       **_power_fields(h.get("effect"), h.get("basis"), h.get("basis_note"), h.get("power")),
                       **({"mv": _text(h.get("mv"), 64)} if h.get("kind") == "mediation" else {}),
                       **({"wv": _text(h.get("wv"), 64)} if h.get("kind") == "moderation" else {}),
                       **({"groups": [_text(str(c), 40) for c in h["groups"][:50]]}
                          if isinstance(h.get("groups"), list) and h["groups"] else {}),
                       **({"after_start": True} if h.get("after_start") is True else {})})
    sample = raw.get("sample") if isinstance(raw.get("sample"), dict) else {}
    goals = _goals(raw)
    return {"goal": goals[0] if goals else "", "goals": goals,
            "design": design_key(raw.get("design")),
            "hypotheses": hs, "rules": _text(raw.get("rules"), 1200), "holm": raw.get("holm") is True,
            "sample": {"target": _int(sample.get("target")),
                       "recruitment": sample.get("recruitment") if sample.get("recruitment") in RECRUITMENT else "",
                       "note": _text(sample.get("note")), "exclusions": _percent(sample.get("exclusions")),
                       "response": _rate(sample.get("response")),
                       "panel_kind": _pick(sample.get("panel_kind"), PANEL_KINDS),
                       "random_kind": _pick(sample.get("random_kind"), RANDOM_KINDS),
                       "population": _text(sample.get("population"), 300), "country": _country(sample.get("country")),
                       "region": _text(sample.get("region"), 120)},
            "deviations": [], "layout": layout_of(raw.get("layout")), "sem": sem_options(raw.get("sem")),
            "diary": diary_options(raw.get("diary")), "archive": archive_of(raw.get("archive")),
            **({"sem_method": raw["sem_method"]} if raw.get("sem_method") in SEM_METHODS else {})}


def markdown(survey, lang=None):
    """The plan as a readable text for the pre-registration (app/prereg.py), in German or English (lang; default:
    the language of the page, app.lang.current). What the researchers wrote stays as they wrote it."""
    from app import lang as ui_lang

    en = (lang or ui_lang.current()) == "en"
    t = (lambda de, english_text: english_text if en else de)
    table = (lambda german, english_table: {k: english_table.get(k, v) for k, v in german.items()} if en else german)
    goals_, designs_, kinds_ = table(GOALS, GOALS_EN), table(DESIGNS, DESIGNS_EN), table(KINDS, KINDS_EN)
    directions_, analyses_, recruitment_ = (table(DIRECTIONS, DIRECTIONS_EN), table(ANALYSES, ANALYSES_EN),
                                            table(RECRUITMENT, RECRUITMENT_EN))
    plan = plan_of(survey)
    lines = [t("# Studienplan: {title}", "# Research plan: {title}").format(title=survey.title), ""]
    if survey.research_question:
        lines += [t("## Forschungsfrage", "## Research question"), "", survey.research_question, ""]
    if plan["goals"] or plan["design"]:
        lines += [t("## Ziel und Design", "## Aim and design"), ""]
        if len(plan["goals"]) > 1:
            lines.append(t("- Erkenntnisziele: {goals}", "- Aims: {goals}").format(goals="; ".join(goals_[g] for g in plan["goals"])))
        elif plan["goals"]:
            lines.append(t("- Erkenntnisziel: {goal}", "- Aim: {goal}").format(goal=goals_[plan["goals"][0]]))
        if plan["design"]:
            lines.append(t("- Design: {design}", "- Design: {design}").format(design=designs_[plan["design"]]))
        lines.append("")
    if plan["hypotheses"]:
        lines += [t("## Hypothesen und Analyseplan", "## Hypotheses and analysis plan"), ""]
        for n, h in enumerate(plan["hypotheses"], 1):
            parts = [kinds_.get(h["kind"], "")]
            if h.get("direction"):
                parts.append(directions_[h["direction"]])
            iv_name, dv_name = (pair[1 if en else 0] for pair in ROLES.get(h.get("kind"), DEFAULT_ROLES))
            # in the middle of the line only the first letter small: "variable B", not "variable b" (B-21)
            dv_low = dv_name[:1].lower() + dv_name[1:] if en or dv_name.startswith("Verglichen") else dv_name
            if h.get("kind") == "mediation":
                parts.append(t("{a} {iv}, Vermittler {mv}, {b} {dv}", "{a} {iv}, mediator {mv}, {b} {dv}")
                             .format(a=iv_name, b=dv_low, iv=h.get("iv") or "–",
                                     mv=h.get("mv") or "–", dv=h.get("dv") or "–"))
            elif h.get("kind") == "moderation":
                parts.append(t("{a} {iv}, Moderator {wv}, {b} {dv}", "{a} {iv}, moderator {wv}, {b} {dv}")
                             .format(a=iv_name, b=dv_low, iv=h.get("iv") or "–",
                                     wv=h.get("wv") or "–", dv=h.get("dv") or "–"))
            elif h.get("kind") in ONE_VARIABLE:
                parts.append(f"{dv_name} {h.get('dv') or '–'}")
            elif h.get("iv") or h.get("dv"):
                parts.append(t("{a} {iv}, {b} {dv}", "{a} {iv}, {b} {dv}")
                             .format(a=iv_name, b=dv_low, iv=h.get("iv") or "–",
                                     dv=h.get("dv") or "–"))
            compared = [label for _, label, kept in group_options(survey, h) if kept] if h.get("groups") else []
            if compared:
                parts.append(t("verglichen nur: {groups}", "only compared: {groups}").format(groups=", ".join(compared)))
            analysis = h.get("analysis") or suggest(survey, h)
            if analysis:
                parts.append(t("Verfahren: {analysis}", "method: {analysis}").format(analysis=analyses_[analysis]))
            line = power_line(survey, h, "en" if en else "de")
            if line:
                parts.append(line)
            lines.append(f"- H{n}: {h['text']} ({'; '.join(p for p in parts if p)})")
        if any(h.get("kind") == "mediation" or h.get("analysis") == "sem" for h in plan["hypotheses"]):
            from app import sem_model

            method = (sem_model.METHODS_EN if en else sem_model.METHODS)[plan["sem_method"] or "cb"]
            lines.append(t("- Strukturgleichungsmodell: {m}; indirekte Effekte mit Bootstrap-Intervall ({d} Ziehungen)",
                           "- Structural equation model: {m}; indirect effects with a bootstrap interval ({d} draws)")
                         .format(m=method, d=sem_model.BOOT_DRAWS))
            opts = plan["sem"]
            if opts["controls"]:
                lines.append(t("- Kontrollvariablen (Pfade auf alle erklärten Größen, ohne Hypothese): {c}",
                               "- Control variables (paths onto every explained variable, without a hypothesis): {c}")
                             .format(c=", ".join(opts["controls"])))
            if opts["single"]:
                shown = ", ".join(f"{c} ({f'{r:.2f}' if en else f'{r:.2f}'.replace('.', ',')})" for c, r in opts["single"].items())
                lines.append(t("- Einzelfragen mit angenommener Reliabilität: {c}",
                               "- Single questions with an assumed reliability: {c}").format(c=shown))
            if opts["missing"] == "fiml" and plan["sem_method"] != "pls":
                lines.append(t("- Fehlende Werte: Full Information Maximum Likelihood (robust, MLR)",
                               "- Missing values: full information maximum likelihood (robust, MLR)"))
            if opts["ordinal"] == "ordinal" and plan["sem_method"] != "pls":
                lines.append(t("- Aussagen als geordnete Kategorien: polychorische Korrelationen, WLSMV",
                               "- Statements as ordered categories: polychoric correlations, WLSMV"))
            if opts["alternative"] and plan["sem_method"] != "pls":
                numbers = {h.get("id"): n for n, h in enumerate(plan["hypotheses"], 1)}
                left = [(t("H{n}", "H{n}") if key[0] == "h" else t("direkter Effekt in H{n}", "direct effect in H{n}"))
                        .format(n=numbers.get(key[2:], "?")) for key in opts["alternative"]]
                lines.append(t("- Konkurrenzmodell ohne: {c} (skalierter χ²-Differenztest, AIC, BIC)",
                               "- Competing model without: {c} (scaled χ² difference test, AIC, BIC)").format(c=", ".join(left)))
            if opts["second"] and plan["sem_method"] != "pls":
                lines.append(t("- Faktoren zweiter Ordnung über den Subskalen: {c}",
                               "- Second-order factors over the subscales: {c}").format(c=", ".join(opts["second"])))
            if opts["invariance"]:
                lines.append(t("- Messinvarianz (konfigural, metrisch, skalar, strikt) über: {g}",
                               "- Measurement invariance (configural, metric, scalar, strict) across: {g}")
                             .format(g=t("Sprache der Befragung", "language of the survey")
                                     if opts["invariance"] == LANG_GROUPS else opts["invariance"]))
            if opts["formative"] and plan["sem_method"] == "pls":
                lines.append(t("- Formative Konstrukte (PLS, Modus B): {c}", "- Formative constructs (PLS, mode B): {c}")
                             .format(c=", ".join(opts["formative"])))
                if opts["global"]:
                    lines.append(t("- Redundanzanalyse mit Globalmaß: {c}", "- Redundancy analysis with a global measure: {c}")
                                 .format(c=", ".join(f"{c} → {g}" for c, g in opts["global"].items())))
            if plan["sem_method"] == "pls" and (opts["second"] or opts["hoc"]):
                kinds = {k: t("formativ", "formative") if k in opts["higher_formative"] else t("reflektiv", "reflective")
                         for k in opts["second"] + [g["id"] for g in opts["hoc"]]}
                shown = [f"{c} ({kinds[c]})" for c in opts["second"]] + \
                        [f"{g['name']} = {' + '.join(g['parts'])} ({kinds[g['id']]})" for g in opts["hoc"]]
                lines.append(t("- Konstrukte höherer Ordnung (PLS, disjoint two-stage): {c}",
                               "- Higher-order constructs (PLS, disjoint two-stage): {c}").format(c="; ".join(shown)))
        if plan.get("holm") and len(tested(survey)) >= 2:
            lines.append(t("- Korrektur für mehrere Hypothesen: Holm (1979); jede Hypothese mit α = {a} geplant",
                           "- Correction for several hypotheses: Holm (1979); every hypothesis planned with α = {a}")
                         .format(a=alpha_text(alpha_for(survey), "en" if en else "de")))
        lines.append("")
    s = plan["sample"]
    if s["target"] or s["recruitment"] or s["note"] or s.get("population") or s.get("country") or power_summary(survey):
        lines += [t("## Stichprobe", "## Sample"), ""]
        if s["target"]:
            lines.append(t("- Ziel: {n} abgeschlossene Teilnahmen", "- Target: {n} completed participations").format(n=s["target"]))
        from app import sample_model

        m = sample_model.of(survey)
        if m:  # the line of the sample model, as every page shows it (S3)
            lines.append(t("- Stichprobenmodell: {line}", "- Sample model: {line}").format(line=sample_model.text(m, "en" if en else "de")))
        if s["recruitment"]:
            key, kinds = {"panel": ("panel_kind", table(PANEL_KINDS, PANEL_KINDS_EN)),
                          "random": ("random_kind", table(RANDOM_KINDS, RANDOM_KINDS_EN))}.get(s["recruitment"], ("", {}))
            kind = kinds.get(s.get(key) or "") if key and s.get(key) else ""
            how = recruitment_[s["recruitment"]] + (f" ({kind})" if kind else "")
            lines.append(t("- Rekrutierung: {how}", "- Recruitment: {how}").format(how=how))
        if s.get("population"):
            lines.append(t("- Grundgesamtheit: {p}", "- Population: {p}").format(p=s["population"]))
        if s.get("country") or s.get("region"):
            from app import geoip

            where = ", ".join(x for x in (geoip.country_name(s.get("country"), "en" if en else "de") if s.get("country") else "",
                                          s.get("region") or "") if x)
            lines.append(t("- Land und Region: {w}", "- Country and region: {w}").format(w=where))
        if s["note"]:
            lines.append(f"- {s['note']}")
        ps = power_summary(survey)
        if ps:
            lines.append(t("- Poweranalyse: größtes N = {n} ({h}); mit {e} % erwarteten Ausschlüssen {c} Abschlüsse",
                           "- Power analysis: largest N = {n} ({h}); with {e}% expected exclusions {c} completions")
                         .format(n=ps["n"], h=", ".join(f"H{i}" for i in ps["by"]), e=ps["exclusions"], c=ps["completions"]))
        lines.append("")
    if plan["rules"]:
        lines += [t("## Ausschlussregeln", "## Exclusion rules"), "", plan["rules"], ""]
    return "\n".join(lines)


@bp.route("/<sid:survey_id>/researchdesign")
@login_required
def page(survey_id):
    """The study plan as a page of its own between "Einstellungen" and "Fragebogen" (Ralf, 2026-10-06): everybody
    on the survey reads it, editors and admins change it."""
    from flask import render_template

    from app import settings_page, surveys

    survey = surveys.get_survey(survey_id)
    if survey.live is not None or survey.interview is not None:
        abort(404)
    from app import waves

    return render_template("surveys/plan.html", survey=survey, summary=settings_page.summaries(survey)["studienplan"],
                           **waves.form_context(current_user, survey))


@bp.route("/<sid:survey_id>/researchdesign", methods=["POST"])
@login_required
@autosave.aware
def update(survey_id):
    """The page "Studienplan" (people with the role "Bearbeiten" or "Admin")."""
    from app import surveys

    from app import edit_guard

    survey = surveys.get_survey(survey_id)
    if not survey.can(current_user, "editor"):
        abort(403)
    # two people at the plan: a save based on an outdated page is not applied, as on the settings page (code review
    # 2026-10-15, I3); after the start the change would also be a deviation from the pre-registration nobody noticed
    edit_guard.lock(survey)  # overlapping saves one after the other (audit 2026-10-23, S-02)
    conflict = edit_guard.check(survey, "settings:plan", edit_guard.settings_state(survey, "plan"), request.form)
    apply(survey, request.form)
    if conflict:
        return edit_guard.show_again(lambda: page(survey.id))
    edit_guard.saved(survey, "settings:plan")
    autosave.base_later(lambda: edit_guard.settings_state(survey, "plan"))
    db.session.commit()
    if survey.status != "draft":
        flash(de_en("Studienplan gespeichert. Die Änderung steht als Abweichung von der Präregistrierung im Plan.",
                    "Research plan saved. The change is listed in the plan as a deviation from the pre-registration."),
              "success")
    else:
        flash(de_en("Studienplan gespeichert.", "Research plan saved."), "success")
    return redirect(url_for("study_plan.page", survey_id=survey.id))


def progress(survey):
    """The data collection against the power analysis (PA4, concept poweranalyse-konzept.md), None without one:
    {"completed", "marked", "usable" (completed minus what quality assurance marks), "need" (usable participations the
    plan asks for), "missing", "rate" (share left out so far; the planned one while fewer than RATE_FROM are there),
    "more" (completions still to come at that rate), "end" (a larger end of data collection, or None), "short" (the
    rows below their number: "reached" only without them), "rows":
    [{"n" (number of the hypothesis), "p" (power_of), "have", "need", "per_group" (bool), "share"}]}. Counted in the
    data pool of the results page (completed participations); the group sizes are those the planned test takes.
    No test result is shown: whoever looks at p and then goes on or stops raises the error rate (Simmons et al., 2011)."""
    import math

    from app import compare, hypotheses
    from app.surveys import stored_pool

    ps = power_summary(survey)
    if not ps:
        return None
    pool = {**stored_pool(survey), "all": False}
    table = compare.table_of(survey, {**pool, "drop": False})
    completed = len(table)
    usable_rows = table.keep(lambda r: r.get("qc_marked") != 1)
    marked = completed - len(usable_rows)
    usable = len(usable_rows)
    rate = marked / completed if completed >= RATE_FROM else ps["exclusions"] / 100
    rate = min(rate, 0.9)
    from app import lang as ui_lang

    found = hypotheses.evaluate(survey, usable_rows, ui_lang.current()) if usable else []
    rows = []
    for (i, p), out in zip(ps["rows"], found or [{}] * len(ps["rows"])):
        if not p["n"]:
            continue
        sizes = out.get("sizes") if out else None
        if p["analysis"] in ("t", "u", "anova", "kw"):
            groups = p["groups"]
            need = math.ceil(p["n"] / max(p["groups"], 1))
            # every planned group counts, also one below MIN_GROUP that the test leaves out, and a group without
            # answers counts 0: 60/60/3 with 53 needed per group is not reached (audit 2026-10-10, B-17)
            ns = sorted((g["n"] for g in (out or {}).get("groups") or []), reverse=True)[:groups]
            have = min(ns + [0] * (groups - len(ns))) if groups else 0
            rows.append({"n": i, "p": p, "have": have, "need": need, "per_group": True, "groups": groups,
                         "share": min(1.0, have / need) if need else 1.0})
        else:
            have = (sizes if isinstance(sizes, int) else sum(sizes)) if sizes else 0
            rows.append({"n": i, "p": p, "have": have, "need": p["n"], "per_group": False, "share": min(1.0, have / p["n"])})
    missing = max(0, ps["n"] - usable)
    more = math.ceil(missing / (1 - rate) - 1e-9) if missing else 0
    end = None
    if missing and survey.max_responses and survey.status in ("draft", "active", "paused") and completed + more > survey.max_responses:
        end = completed + more
    # the total alone is not enough: a hypothesis counts its smallest group or its valid pairs (PA-4)
    short = [row for row in rows if row["share"] < 1]
    return {"completed": completed, "marked": marked, "usable": usable, "need": ps["n"], "missing": missing,
            "short": short, "only_real": list(pool["sources"]) == ["real"],
            "rate": rate, "planned_rate": ps["exclusions"] / 100, "measured": completed >= RATE_FROM, "more": more,
            "end": end, "rows": rows}


RATE_FROM = 20  # completions from which the share left out so far stands for the planned one (Festlegung)


@bp.route("/<sid:survey_id>/researchdesign/state")
@login_required
def progress_part(survey_id):
    """The box "Stand gegen die Poweranalyse" of the page "Datenerhebung", fetched after the page (it reads the data
    pool and the quality assurance)."""
    from flask import render_template

    from app import surveys

    survey = surveys.get_survey(survey_id)
    return render_template("surveys/_power_progress.html", survey=survey, pr=progress(survey))


@bp.route("/<sid:survey_id>/researchdesign/pilot", methods=["GET", "POST"])
@login_required
def pilot(survey_id):
    """The expected effect of a hypothesis from an earlier survey (PA6, app/power_pilot.py): the other surveys of the
    person or group that measured both variables, with the effect and its safeguard value; "übernehmen" sets it as the
    expected effect with "Vorstudie oder Pilotdaten" and the survey as the reason."""
    from flask import render_template

    from app import power_pilot, surveys
    from app.models import Survey

    survey = surveys.get_survey(survey_id)
    plan = plan_of(survey)
    hid = request.values.get("h", "")
    h = next((x for x in plan["hypotheses"] if x.get("id") == hid), None)
    if h is None:
        abort(404)
    if request.method == "POST":
        if not survey.can(current_user, "editor"):
            abort(403)
        source = db.session.get(Survey, request.form.get("source", type=db_id) or 0)
        if source is None or source.id == survey.id or source.role_of(current_user) is None:
            abort(404)
        found = power_pilot.estimate(source, h, power_of(survey, h)["analysis"])
        which = request.form.get("which")
        value = (found or {}).get("safeguard" if which == "safeguard" else "effect")
        from app import power

        if not found or value is None or not power.valid_effect(power_of(survey, h)["analysis"], round(value, 3)):
            flash(de_en("Dieser Wert lässt sich nicht übernehmen.", "This value cannot be used."), "error")
            return redirect(url_for("study_plan.pilot", survey_id=survey.id, h=hid))
        before = copy.deepcopy(plan)
        h["effect"], h["basis"] = round(value, 3), "pilot"
        h["basis_note"] = _text(de_en("Befragung „{t}“ (n = {n}), {how}", "Survey “{t}” (n = {n}), {how}",
                                      t=source.title, n=found["n"],
                                      how=(de_en("untere Grenze des 60-%-Intervalls (Safeguard)",
                                                 "lower end of the 60% interval (safeguard)")
                                           if which == "safeguard" else de_en("Punktschätzer", "point estimate"))), 300)
        if survey.status != "draft" and _changes(before, plan):
            plan["deviations"].append({"at": _today(), "what": "Erwarteter Effekt aus Vorstudie",
                                       "by": getattr(current_user, "name", "") or "", "by_id": getattr(current_user, "id", None)})
        survey.study_plan = plan
        db.session.commit()
        flash(de_en("Erwarteten Effekt übernommen.", "Expected effect applied."), "success")
        return redirect(url_for("study_plan.page", survey_id=survey.id))
    number = plan["hypotheses"].index(h) + 1
    return render_template("surveys/power_pilot.html", survey=survey, h=h, number=number, p=power_of(survey, h),
                           found=power_pilot.candidates(survey, h, current_user),
                           sources=[(de_en("Erwarteter Effekt aus einer früheren Befragung: Safeguard Power",
                                           "Expected effect from an earlier survey: safeguard power"),
                                     [power_pilot.PERUGINI, power_pilot.ALBERS_LAKENS])])


@bp.route("/<sid:survey_id>/researchdesign/goal", methods=["POST"])
@login_required
def take_target(survey_id):
    """„Als Ziel übernehmen“ of the sample size planner (UI/UX audit 2026-10-05, V10): the number becomes the target
    of the study plan and goes on to the end of the field period (filled in there, saved with the start)."""
    from app import surveys

    survey = surveys.get_survey(survey_id)
    n = _int(request.form.get("n"))
    if n is None:
        abort(400)
    # the expected exclusions the calculator planned with go along, so that the plan's line of the sample model
    # names the same numbers as the calculator (S3); an empty field leaves the plan's value
    excl = _percent(request.form.get("excl"))
    if survey.can(current_user, "editor"):
        plan = plan_of(survey)
        if plan["sample"]["target"] != n or (excl is not None and plan["sample"]["exclusions"] != excl):
            before = copy.deepcopy(plan)
            plan["sample"]["target"] = n
            if excl is not None:
                plan["sample"]["exclusions"] = excl
            if survey.status != "draft" and _changes(before, plan):
                plan["deviations"].append({"at": _today(), "what": "Stichprobe",
                                           "by": getattr(current_user, "name", "") or "", "by_id": getattr(current_user, "id", None)})
            survey.study_plan = plan
            db.session.commit()
            flash(de_en("Stichprobenziel {n} in den Studienplan übernommen.",
                        "Sample target {n} taken into the research plan.", n=n), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id, max_responses=n) + "#status")


@bp.route("/<sid:survey_id>/researchdesign/panel-power", methods=["POST"])
@login_required
def panel_power(survey_id):
    """„Power per Simulation prüfen“ of the hypotheses "Wirkung über die Zeit" (app/panel_models.start_power)."""
    from flask import current_app

    from app import panel_models, surveys

    survey = surveys.get_survey(survey_id)
    state = panel_models.start_power(survey, background=not current_app.config.get("HB_SYNC"))
    if state == "started":
        flash(de_en("Simulation gestartet; sie läuft im Hintergrund, die Seite zeigt das Ergebnis beim nächsten Laden.",
                    "Simulation started; it runs in the background, the page shows the result when loaded again."),
              "success")
    elif state == "running":
        flash(de_en("Die Simulation läuft bereits.", "The simulation is already running."), "info")
    elif state == "nothing":
        flash(de_en("Für die Simulation fehlen ein erwarteter Effekt oder eine Fallzahl.",
                    "The simulation needs an expected effect and a sample size."), "error")
    elif state == "too_large":
        flash(de_en("Effekte zu groß für die Planungsannahmen: Die Varianz bliebe nicht bei 1, simuliert würde ein "
                    "anderer Effekt. Bitte die erwarteten Effekte prüfen.",
                    "Effects too large for the planning assumptions: the variance would not stay at 1, another effect "
                    "would be simulated. Please check the expected effects."), "error")
    return redirect(url_for("study_plan.page", survey_id=survey.id) + "#plan-panel-power")


@bp.route("/<sid:survey_id>/researchdesign/sem-power", methods=["POST"])
@login_required
def sem_power(survey_id):
    """„Power per Simulation prüfen“ (SEM extensions 2026-10-17, point 16): start the simulation of the planned model
    in the background, like the bootstrap (app/sem_model.start_power)."""
    from flask import current_app

    from app import sem_model, surveys

    survey = surveys.get_survey(survey_id)
    state = sem_model.start_power(survey, background=not current_app.config.get("HB_SYNC"))
    if state == "started":
        flash(de_en("Simulation gestartet; sie läuft im Hintergrund, die Seite zeigt das Ergebnis beim nächsten Laden.",
                    "Simulation started; it runs in the background, the page shows the result when loaded again."),
              "success")
    elif state == "running":
        flash(de_en("Die Simulation läuft bereits.", "The simulation is already running."), "info")
    elif state == "nothing":
        flash(de_en("Für die Simulation fehlen ein kovarianzbasiertes Modell, ein erwarteter Effekt oder eine Fallzahl.",
                    "The simulation needs a covariance-based model, an expected effect and a sample size."), "error")
    return redirect(url_for("study_plan.page", survey_id=survey.id) + "#plan-sem-power")


@bp.route("/<sid:survey_id>/researchdesign/assign", methods=["POST"])
@login_required
@autosave.aware
def assign(survey_id):
    """From the element editor: this element measures what explains or what is explained in a hypothesis."""
    from app import surveys

    survey = surveys.get_survey(survey_id)
    if not survey.can(current_user, "editor"):
        abort(403)
    code = request.form.get("code", "")
    if _item(survey, code) is None:
        abort(404)
    plan = plan_of(survey)
    # one row per hypothesis with a box for each role (UI/UX audit 2026-10-05, V3): only the roles of the rows the
    # page showed change, and only where the box differs from the plan; other roles of this element stay
    shown = set(request.form.getlist("h"))
    ticked = set(request.form.getlist("role"))  # "<hypothesis id>:iv" | "<hypothesis id>:dv"
    changed = False
    for h in plan["hypotheses"]:
        if h["id"] not in shown:
            continue
        for role in role_keys(h.get("kind")):  # the boxes the row showed
            want = f"{h['id']}:{role}" in ticked
            if want and h.get(role) != code:
                h[role], changed = code, True
            elif not want and h.get(role) == code:
                h[role], changed = "", True
    if changed and survey.status != "draft":
        plan["deviations"].append({"at": _today(),
                                   "what": f"Zuordnung von {code} geändert",
                                   "by": getattr(current_user, "name", "") or "", "by_id": getattr(current_user, "id", None)})
    survey.study_plan = plan
    db.session.commit()
    flash(de_en("Zuordnung gespeichert.", "Assignment saved."), "success")
    from app.links import local_path

    # only a page of this site ("/\host" too would lead elsewhere; security audit 2026-10-06, H4)
    back = local_path(request.form.get("back")) or url_for("study_plan.page", survey_id=survey.id)
    return redirect(back)
