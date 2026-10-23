"""Tempo hint (opt-in per survey): a page sent faster than its texts can be read is shown once more, with a
note, before the participant goes on (Conrad, Tourangeau, Couper & Zhang, 2017).

The threshold is absolute and the same for everyone: MS_PER_WORD for every word that has to be read to answer
the page (questions, instructions, statements, rows; not answer options and scale labels, which are scanned
rather than read). Only the first "Next" of the first visit of a page is checked; the time is the page time
of app/paradata.py, measured on the server, so the hint needs "Zeit je Seite erfassen".

Never checked: the consent, tasks with pages of their own other than the choice tasks below (ACBC, the timed
assignment, explanation pages, the anchoring question), the separated section, dummy data.

What a survey can add (Survey.tempo_options, options()):
  rule "median"  the threshold of a page is MEDIAN_SHARE of the median time its first visit took so far
                 (Greszki, Meyer & Schoen, 2014: per page, more than 50 percent faster than the median), once MEDIAN_MIN participations have sent it; the reading
                 time until then. The medians are kept per server process for a minute (medians()).
  read           pages without a question (texts, vignettes, instructions) are checked too, with a wording
                 of their own
  tasks          the choice tasks of conjoint and MaxDiff: the question and what the task shows to be judged
                 (the levels of its profiles, the statements of its set)
and an own wording of the note per language (Survey.tempo_texts).

Per response, in Response.paradata["_tempo"]: {"n": pages checked, "fast": pages below the threshold,
"hint": hints shown, "at": page key of the first hint, "pos": positions (in the layout) of the fast pages};
in Response.paradata["_t1"]: {page key: seconds of the first visit} of the checked pages ("_t" adds up all
visits, and the hint itself is a second one).

In the flow a condition can ask how many pages a person has sent too fast up to that point (REF, the answers
are ticked like those of a question; see at()).
"""
import re
import statistics
import time

from app import units
from app.items.base import PLACEHOLDER, plain
from app.ui_i18n import tr

MS_PER_WORD = 350
MEDIAN_SHARE = 0.5   # median rule: faster than half of the median time of the page
MEDIAN_MIN = 20      # times of a page needed before its median counts
MEDIAN_SAMPLE = 2000  # the newest participations the medians are taken from: the cost stays the same in a large field
MEDIAN_SECONDS = 60  # how long a server process keeps the medians of a survey
MODES = (("off", "aus"), ("all", "auf allen Seiten mit Fragen"), ("blocks", "nur in ausgewählten Blöcken"))
DOSES = (("once", "einmal je Teilnahme"), ("always", "bei jeder zu schnellen Seite"))
RULES = (("words", f"Lesezeit: {MS_PER_WORD} ms je Wort der Fragetexte"),
         ("median", "halbe Median-Zeit der Seite bei den bisherigen Teilnahmen"))
# the same choices in English (researcher pages in English, app/lang.py); the keys stay
MODES_EN = {"off": "off", "all": "on all pages with questions", "blocks": "only in selected blocks"}
DOSES_EN = {"once": "once per participation", "always": "on every page that is too fast"}
RULES_EN = {"words": f"reading time: {MS_PER_WORD} ms per word of the question texts",
            "median": "half the median time of the page in the participations so far"}


def _local(choices, english_table):
    from app.lang import english

    return tuple((key, english_table[key]) for key, _ in choices) if english() else choices


def modes():
    return _local(MODES, MODES_EN)


def doses():
    return _local(DOSES, DOSES_EN)


def rules():
    return _local(RULES, RULES_EN)


CONRAD = ("Conrad, F. G., Tourangeau, R., Couper, M. P., & Zhang, C. (2017). Reducing speeding in web surveys by "
          "providing immediate feedback. Survey Research Methods, 11(1), 45–61. "
          "https://doi.org/10.18148/srm/2017.v11i1.6304")
GRESZKI = ("Greszki, R., Meyer, M., & Schoen, H. (2014). The impact of speeding on data quality in nonprobability and "
           "freshly recruited probability-based online panels. In M. Callegaro, R. Baker, J. Bethlehem, A. S. Göritz, "
           "J. A. Krosnick, & P. J. Lavrakas (Eds.), Online panel research: A data quality perspective (pp. 238–262). Wiley.")

# texts that have to be read: the question and what is judged one by one
READ_KEYS = ("question", "statements", "pairs", "rows", "text", "passage", "questions", "rate",
             "q_tc", "q_c", "q_e", "q_te", "nms_c", "nms_e")
# here the options are what is judged (ranked, placed, given points), not answers to pick from
OPTIONS_READ = ("ranking", "position2d", "constant_sum")
TASK_TYPES = ("conjoint", "maxdiff", "paired", "cva")  # choice and rating tasks, one per page
TEXT_KEYS = ("hint", "hint_read")     # the own wordings: on pages with questions (and tasks), on pages without

COLUMNS = [
    ("tempo_checked", "Tempo-Hinweis: Zahl der geprüften Seiten"),
    ("tempo_fast", "Tempo-Hinweis: Zahl der Seiten, die beim ersten „Weiter“ unter der Schwelle lagen"),
    ("tempo_hint", "Tempo-Hinweis: Zahl der gezeigten Hinweise"),
    ("tempo_hint_at", "Tempo-Hinweis: Seite des ersten Hinweises (Variablenname ihres ersten Elements)"),
]

# a condition of the flow on the pages sent too fast so far
REF = "tempo"
FLOW_OPTIONS = [("0", "keine Seite zu schnell"), ("1", "eine Seite zu schnell"), ("2", "zwei oder mehr Seiten zu schnell")]
FLOW_OPTIONS_EN = {"0": "no page too fast", "1": "one page too fast", "2": "two or more pages too fast"}


def flow_options():
    """FLOW_OPTIONS in the language of the page (the keys stay)."""
    return list(_local(FLOW_OPTIONS, FLOW_OPTIONS_EN))
FAST_KEY = "tempo@"  # in the view the conditions are tested with: the positions of the fast pages (at())


def mode(survey):
    value = getattr(survey, "tempo_hint", None)
    return value if value in dict(MODES) else "off"


def dose(survey):
    value = getattr(survey, "tempo_dose", None)
    return value if value in dict(DOSES) else "once"


def active(survey):
    return mode(survey) != "off"


def options(survey):
    """{"rule", "read", "tasks"} as saved for the survey, with the standard for what is missing."""
    saved = getattr(survey, "tempo_options", None)
    saved = saved if isinstance(saved, dict) else {}
    return {"rule": saved.get("rule") if saved.get("rule") in dict(RULES) else "words",
            "read": saved.get("read") is True, "tasks": saved.get("tasks") is True}


def options_from_form(form):
    """What the settings form says (fields tempo_rule, tempo_read, tempo_tasks); None for the standard."""
    out = {"rule": form.get("tempo_rule") if form.get("tempo_rule") in dict(RULES) else "words",
           "read": bool(form.get("tempo_read")), "tasks": bool(form.get("tempo_tasks"))}
    return out if out != {"rule": "words", "read": False, "tasks": False} else None


def texts_from_form(form, languages, saved=None):
    """The own wordings from the settings form (fields tempo_text_<key>_<lang>); a language without fields in
    the form keeps what is stored. None if there is none."""
    out = {}
    for lang in languages:
        sent = {key: form.get(f"tempo_text_{key}_{lang}") for key in TEXT_KEYS}
        if all(v is None for v in sent.values()):
            kept = clean_texts({lang: (saved or {}).get(lang)})
            out.update(kept or {})
            continue
        own = {key: " ".join(v.split())[:500] for key, v in sent.items() if v and v.strip()}
        if own:
            out[lang] = own
    return out or None


def clean_texts(raw):
    """Stored wordings as they may be: {lang: {key: text}} with known keys and bounded texts, or None."""
    out = {}
    for lang, per in (raw.items() if isinstance(raw, dict) else ()):
        own = {key: " ".join(per[key].split())[:500] for key in TEXT_KEYS
               if isinstance(per, dict) and isinstance(per.get(key), str) and per[key].strip()}
        if own and isinstance(lang, str) and len(lang) <= 12:
            out[lang] = own
    return out or None


def block_on(survey, block):
    """The pages of this block are checked (as far as checked() allows)."""
    m = mode(survey)
    return m == "all" or (m == "blocks" and bool((block.settings or {}).get("tempo_hint")))


def _words(value, variables):
    if isinstance(value, dict):
        return sum(_words(v, variables) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_words(v, variables) for v in value)
    text = PLACEHOLDER.sub(lambda m: str(variables.get(m.group(1), m.group(0))), plain(str(value or "")))
    return len(re.findall(r"\w+", text))


def words(item, lang, variables=None, answers=None):
    """Words of the item that have to be read, in `lang`, with the placeholders of a loop pass filled in. answers:
    those of the person who sees the page; rows not shown to them (a condition of the row, a carried statement
    whose option was not chosen; app/rows.py) are not read, so their words do not count (Conrad et al., 2017:
    the reading time of the text shown)."""
    texts = item.t(lang)
    keys = READ_KEYS + (("options",) if item.type in OPTIONS_READ else ())
    read = {k: texts.get(k) for k in keys}
    if answers is not None:
        from app import rows

        for key in ("statements", "pairs", "rows"):
            found = (item.config or {}).get(key)
            if isinstance(read.get(key), dict) and isinstance(found, list) and all(isinstance(r, dict) for r in found):
                gone = rows.hidden(found, answers, item.survey)
                if gone:
                    read[key] = {rid: text for rid, text in read[key].items() if rid not in gone}
    return _words(read, variables or {})


def task_words(item, lang, task, version, variables=None):
    """Words of one choice task: its question and what it shows to be judged (conjoint: the levels of every
    profile; MaxDiff: the statements of the set). None if the design does not have this task."""
    kind = item.kind
    try:
        if item.type == "maxdiff":
            labels = kind.labels(item, lang)
            shown = [labels[i] for i in kind.task_set(item, version, task)]
        elif item.type == "paired":  # the two options of the pair
            labels = kind.labels(item, lang)
            shown = [labels[i] for i in kind.task_pair(item, version, task)]
        elif item.type == "cva":  # the offer, or the two offers of the pair
            levels = [a["level_labels"] for a in kind.attributes(item, lang)]
            shown = [levels[a][level] for profile in kind.task_profiles(item, version, task)
                     for a, level in enumerate(profile)]
        else:
            levels = [a["level_labels"] for a in kind.attributes(item, lang)]
            shown = [levels[a][level] for profile in item.config["design"]["versions"][version][task]
                     for a, level in enumerate(profile)]
    except (KeyError, IndexError, TypeError):
        return None
    return _words([item.t(lang).get("question"), shown], variables or {})


def page_kind(page_items, spec=None):
    """What a page is for the hint: "ask" (it asks something), "read" (texts only), "task" (a choice task of
    conjoint or MaxDiff; without `spec`, in the editor: the item that has them) or None (never checked: the
    consent, ACBC, the timed assignment, explanation pages, the anchoring question)."""
    if not page_items or any(i.type == "consent" for i in page_items):
        return None
    if any(i.kind.alone_on_page for i in page_items):
        item = page_items[0]
        if len(page_items) != 1 or item.type not in TASK_TYPES or not item.kind.design_current(item):
            return None
        return "task" if spec is None or "task" in spec else None
    return "ask" if any(i.kind.collects_answer for i in page_items) else "read"


def checked(survey, kind):
    """The survey checks pages of this kind."""
    return kind == "ask" or (kind in ("read", "task") and options(survey)[{"read": "read", "task": "tasks"}[kind]])


def threshold(page_items, lang, variables=None, spec=None, version=0, answers=None):
    """Seconds of reading below which the page counts as too fast; None for a task the design does not have.
    answers: of the person on the page (words of rows not shown to them do not count)."""
    if spec is not None and "task" in spec and len(page_items) == 1 and page_items[0].type in TASK_TYPES:
        count = task_words(page_items[0], lang, spec["task"], spec.get("v", version), variables)
        if count is None:
            return None
    else:
        count = sum(words(i, lang, variables, answers) for i in page_items)
    return round(count * MS_PER_WORD / 1000, 1)


def medians(survey):
    """{page key: (median seconds of the first visit, how many)} over the newest participations: the real
    ones, or the stored test runs while there is no real one (the pretest). Kept for MEDIAN_SECONDS. A page whose
    questions, statements or options only some people see (paradata "_v", app/paradata.py) also gets one per
    version, "<key>|<version>" ("<key>|" for the whole page), as the speed index has them (export.page_medians)."""
    from flask import current_app

    from app.extensions import db
    from app.models import Response

    def load():
        times = {}
        for source in ("real", "test"):
            rows = db.session.scalars(db.select(Response.paradata).filter(
                Response.survey_id == survey.id, Response.source == source).order_by(Response.id.desc()).limit(MEDIAN_SAMPLE))
            versions, varied = {}, set()
            for data in rows:
                shown = (data or {}).get("_v") if isinstance((data or {}).get("_v"), dict) else {}
                varied.update(shown)
                for key, seconds in first_times(data).items():
                    times.setdefault(key, []).append(seconds)
                    versions.setdefault(f"{key}|{shown.get(key) or ''}", []).append(seconds)
            times.update((v, given) for v, given in versions.items() if v.split("|", 1)[0] in varied)
            if times:
                break
        return {key: (statistics.median(given), len(given)) for key, given in times.items()}

    ttl = current_app.config.get("TEMPO_MEDIAN_SECONDS", MEDIAN_SECONDS)
    store = current_app.extensions.setdefault("tempo_medians", {})
    entry, now = store.get(survey.id), time.monotonic()
    if entry is not None and ttl and now - entry[0] < ttl:
        return entry[1]
    value = load()
    if ttl:
        store[survey.id] = (now, value)
        if len(store) > 500:
            for k in [k for k, (at, _) in store.items() if now - at >= ttl]:
                del store[k]
    return value


def first_times(data):
    """{page key: seconds of the first visit} of one participation's paradata; for pages sent before the
    first visits were kept (or while the hint was off) the sum of all visits."""
    data = data if isinstance(data, dict) else {}
    return {**(data.get("_t") or {}), **(data.get("_t1") or {})}


def median_limit(survey, key, version=None):
    """The threshold of the median rule for a page, None while fewer than MEDIAN_MIN participations sent it.
    version: the version of the page the person sees (paradata "_v"): measured against those who saw the same once
    MEDIAN_MIN of them did, else against the whole page (as the speed index, export.rsi; audit 2026-10-05)."""
    found = medians(survey)
    same = found.get(f"{key}|{version or ''}")
    if same and same[1] >= MEDIAN_MIN:
        return round(same[0] * MEDIAN_SHARE, 1)
    page = found.get(key)
    return round(page[0] * MEDIAN_SHARE, 1) if page and page[1] >= MEDIAN_MIN else None


def limit(survey, page_items, key, lang, variables=None, spec=None, version=0, answers=None, shown=None):
    """Seconds below which the page counts as too fast, by the rule of the survey. shown: the version of the page
    the person saw (paradata "_v"), for the median rule."""
    if options(survey)["rule"] == "median":
        seconds = median_limit(survey, key, shown)
        if seconds is not None:
            return seconds
    return threshold(page_items, lang, variables, spec, version, answers)


def check(survey, response, block, page_items, key, seconds, lang, variables=None, spec=None, position=None,
          answers=None):
    """The first "Next" on a page after `seconds`: counts the page and returns its kind ("ask", "read",
    "task") if the hint is due, else None."""
    if seconds is None or not key or not hasattr(response, "pid") or getattr(response, "source", "") == "dummy":
        return None
    kind = page_kind(page_items, spec or {})
    if not block_on(survey, block) or not checked(survey, kind):
        return None
    version = 0
    if kind == "task":
        version = (((response.layout or {}).get("cj") or {}).get(str(page_items[0].id)) or {}).get("version", 0)
    # the version of the page this person sees, noted before the check (paradata.note_version)
    shown = ((response.paradata or {}).get("_v") or {}).get(key)
    below = limit(survey, page_items, key, lang, variables, spec, version, answers, shown)
    if below is None or (kind == "read" and not threshold(page_items, lang, variables, answers=answers)):
        return None  # a page without a word to read (an image), or a task the design does not have
    data = dict(response.paradata or {})
    state = {"n": 0, "fast": 0, "hint": 0, **(data.get("_tempo") or {})}
    state["n"] += 1
    due = False
    if seconds < below:
        state["fast"] += 1
        if position is not None:
            state["pos"] = [*(state.get("pos") or []), position]
        due = dose(survey) == "always" or not state["hint"]
        if due:
            state["hint"] += 1
            state.setdefault("at", key)
    response.paradata = {**data, "_tempo": state, "_t1": {**(data.get("_t1") or {}), key: round(seconds, 1)}}
    return kind if due else None


def message(lang, survey=None, kind="ask"):
    """The note: the survey's own wording in this language, else the standard one. Pages without a question
    have a wording of their own."""
    key = "hint_read" if kind == "read" else "hint"
    own = ((getattr(survey, "tempo_texts", None) or {}).get(lang) or {}).get(key)
    return own or default_message(lang, kind)


def default_message(lang, kind="ask"):
    if kind == "read":
        return tr(lang, "Sie haben diese Seite sehr schnell weitergeklickt. Bitte lesen Sie den Text in Ruhe. "
                        "Mit „Weiter“ geht es weiter.",
                  "You moved on from this page very quickly. Please take the time to read the text. "
                  "“Next” takes you on.")
    return tr(lang, "Sie haben diese Seite sehr schnell beantwortet. Bitte prüfen Sie, ob Ihre Antworten so stimmen. "
                    "Mit „Weiter“ geht es weiter.",
              "You answered this page very quickly. Please check whether your answers are right. "
              "“Next” takes you on.")


# --- the flow: a condition on the pages sent too fast so far ------------------------------------------------


def flow_view(survey, response):
    """What the conditions of the flow need: {FAST_KEY: positions of the pages sent too fast}; {} without
    looking at anything if no condition asks for it."""
    from app import flow

    if not active(survey) or not flow.uses_ref(survey, REF):
        return {}
    return {FAST_KEY: list(((getattr(response, "paradata", None) or {}).get("_tempo") or {}).get("pos") or [])}


def at(answers, index):
    """The view a condition in front of the page at `index` is tested with: the pages sent too fast before
    it, as "0", "1" or "2" (two or more). A page that was fast itself, or a later one after going back, does
    not change who sees this page: its answers would be dropped as skipped otherwise."""
    fast = answers.get(FAST_KEY)
    if fast is None:
        return answers
    return {**answers, REF: str(min(sum(p < index for p in fast), 2))}


# --- editor, export, codebook ---------------------------------------------------------------------------


def block_thresholds(survey, block, lang=None):
    """Thresholds (s) of the block's pages for the editor; None for pages that are not checked. A choice task
    item stands for its tasks: the mean of their thresholds (first design version)."""
    from app import loops, runtime

    lang = lang or survey.default_language
    looped = loops.active(block)
    out = []
    for items in (block.pages() if looped else runtime.split_alone(block.pages())):
        kind = page_kind(items)
        if not checked(survey, kind) or (kind == "task" and looped):
            out.append(None)
        elif kind == "task":
            item = items[0]
            keys = [key for _, _, key in item.kind.timing_pages(item) if key.startswith(".t")]
            each = [limit(survey, items, f"{item.id}{key}", lang, spec={"task": int(key[2:])}) for key in keys]
            each = [s for s in each if s is not None]
            out.append(units.round_half_up(sum(each) / len(each), 1) if each else None)
        else:
            first = min(items, key=lambda i: i.position)
            seconds = threshold(items, lang) if looped else limit(survey, items, str(first.id), lang)
            out.append(seconds if kind == "ask" or seconds else None)
    return out


def export_values(survey, response, page_names):
    """The export columns of one response. page_names: {page key: name of the page} (timing columns)."""
    state = (response.paradata or {}).get("_tempo")
    if not state:
        return {}  # nothing was checked (dummy data, or no checked page reached)
    return {"tempo_checked": state.get("n", 0), "tempo_fast": state.get("fast", 0), "tempo_hint": state.get("hint", 0),
            "tempo_hint_at": page_names.get(state.get("at"), "") if state.get("at") else ""}


def share_fast(response):
    """Share of the checked pages this participation sent too fast; None if none was checked."""
    state = (response.paradata or {}).get("_tempo") or {}
    return round(state.get("fast", 0) / state["n"], 3) if state.get("n") else None


def rule_text(survey, lang="de"):
    """The rule of the survey in a sentence part, for the codebook and the pages (lang "en": in English)."""
    if lang == "en":
        reading = f"{MS_PER_WORD} ms per word of the question and statement texts of the page (without answer options and scale labels)"
        if options(survey)["rule"] == "median":
            return (f"half the median time the first visit of the page took in the participations so far "
                    f"(from {MEDIAN_MIN} participations per page; before that {reading})")
        return reading
    reading = f"{MS_PER_WORD} ms je Wort der Frage- und Aussagetexte der Seite (ohne Antwortoptionen und Skalenbeschriftungen)"
    if options(survey)["rule"] == "median":
        return (f"die Hälfte der Median-Zeit, die der erste Besuch der Seite bei den bisherigen Teilnahmen dauerte "
                f"(ab {MEDIAN_MIN} Teilnahmen je Seite; davor {reading})")
    return reading


# the column labels of COLUMNS in an English codebook (the data files keep the German ones)
COLUMNS_EN = {"tempo_checked": "Speed prompt: number of pages checked",
              "tempo_fast": "Speed prompt: number of pages below the threshold at the first “Next”",
              "tempo_hint": "Speed prompt: number of hints shown",
              "tempo_hint_at": "Speed prompt: page of the first hint (variable name of its first element)"}


def codebook_note(survey, lang=None):
    """How the columns came about, with the threshold of every checked page (thresholds named by the first element in
    the default language); in the reader's language `lang` (None: the page's; app.items.base.reader)."""
    from app import flow
    from app.items.base import reader

    if reader(lang) == "en":
        return _codebook_note_en(survey)
    opts = options(survey)
    separated = set(flow.blocks_after_split(survey))
    pages = []
    for block in survey.blocks:
        if block.id in separated or not block_on(survey, block):
            continue
        for items, seconds in zip(_pages(block), block_thresholds(survey, block)):
            if seconds is not None:
                pages.append(f"{min(items, key=lambda i: i.position).code}: {_de(seconds)} s")
    also = [text for key, text in (("read", "Seiten ohne Frage"), ("tasks", "Auswahlaufgaben von Conjoint und MaxDiff "
                                    "(Frage und gezeigte Stufen oder Aussagen; je Element der Mittelwert der Aufgaben)")) if opts[key]]
    sources = CONRAD + (f" {GRESZKI}" if opts["rule"] == "median" else "")
    return (f"Schwelle: {rule_text(survey)}, geprüft beim ersten „Weiter“ des ersten Besuchs; Hinweis "
            + dict(DOSES)[dose(survey)] + ". " + (f"Geprüft werden auch: {'; '.join(also)}. " if also else "")
            + ("Schwellen je Seite beim Export" if opts["rule"] == "median" else "Schwellen je Seite")
            + " (erstes Element, Ausgangssprache): " + ("; ".join(pages) or "keine geprüfte Seite") + f". {sources}")


def _codebook_note_en(survey):
    """codebook_note in English (decimal points)."""
    from app import flow
    from app.units import fixed

    opts = options(survey)
    separated = set(flow.blocks_after_split(survey))
    pages = []
    for block in survey.blocks:
        if block.id in separated or not block_on(survey, block):
            continue
        for items, seconds in zip(_pages(block), block_thresholds(survey, block)):
            if seconds is not None:
                pages.append(f"{min(items, key=lambda i: i.position).code}: {fixed(seconds, 1)} s")
    also = [text for key, text in (("read", "pages without a question"), ("tasks", "choice tasks of conjoint and MaxDiff "
                                    "(question and levels or statements shown; per element the mean of the tasks)")) if opts[key]]
    sources = CONRAD + (f" {GRESZKI}" if opts["rule"] == "median" else "")
    return (f"Threshold: {rule_text(survey, 'en')}, checked at the first “Next” of the first visit; hint "
            + DOSES_EN[dose(survey)] + ". " + (f"Also checked: {'; '.join(also)}. " if also else "")
            + ("thresholds per page at export" if opts["rule"] == "median" else "thresholds per page").capitalize()
            + " (first element, source language): " + ("; ".join(pages) or "no page checked") + f". {sources}")


KIND_NAMES = {"ask": "Fragen", "read": "Text ohne Frage", "task": "Auswahlaufgabe"}
KIND_NAMES_EN = {"ask": "questions", "read": "text without a question", "task": "choice task"}


def kind_names():
    """KIND_NAMES in the language of the page (the keys stay)."""
    from app.lang import english

    return KIND_NAMES_EN if english() else KIND_NAMES


def calibration(survey, sources=("real",), responses=None):
    """For choosing the rule in a pretest: per page the hint can check, how long its first visit took and how
    many participations lie below either threshold. [{"name", "kind", "on" (the hint checks it as set now), "n",
    "median", "words" (s), "below_words" (share), "half" (s), "below_half" (share)}], pages in the order of
    the questionnaire; n = 0 and no figures for a page nobody has sent. Reading thresholds in the default
    language; a choice task with the design version of each participation (shown: their mean). responses: every
    participation of these sources, when the caller has read them already (quality.page: not read a second time)."""
    from app import flow, loops
    from app.extensions import db
    from app.models import Response

    lang = survey.default_language
    separated = set(flow.blocks_after_split(survey))
    pages = []  # (page key, name, kind, checked now, seconds of reading | (item, task) for a choice task)
    for block in survey.blocks:
        if block.id in separated:
            continue
        rows = [(n, r["id"]) for n, r in enumerate(loops.config(block)["rows"], start=1)] if loops.active(block) else []
        for items in _pages(block):
            kind = page_kind(items)
            if kind is None or (kind == "task" and rows):
                continue
            first = min(items, key=lambda i: i.position)
            on = block_on(survey, block) and checked(survey, kind)
            if rows:
                pages += [(f"{first.id}#{rid}", f"{first.code}_{n}", kind, on, threshold(items, lang, loops.variables(block, rid, lang)))
                          for n, rid in rows]
            elif kind == "task":
                pages += [(f"{first.id}{key}", first.code + column, kind, on, (first, int(key[2:])))
                          for column, _, key in first.kind.timing_pages(first) if key.startswith(".t")]
            else:
                pages.append((str(first.id), first.code, kind, on, threshold(items, lang)))
    pages = [p for p in pages if p[2] != "read" or p[4]]  # a page without a word to read is never checked
    with_tasks = any(kind == "task" for _, _, kind, _, _ in pages)
    wanted = {key for key, *_ in pages}
    times = {}  # page key -> [(seconds, design versions of this participation)]
    if responses is not None:
        found = ((r.paradata, r.layout) for r in responses)
    else:
        columns = [Response.paradata] + ([Response.layout] if with_tasks else [])
        found = db.session.execute(db.select(*columns).filter(Response.survey_id == survey.id,
                                                              Response.source.in_(list(sources))))
    for row in found:
        versions = ((row[1] or {}).get("cj") or {}) if with_tasks else {}
        for key, seconds in first_times(row[0]).items():
            if key in wanted:
                times.setdefault(key, []).append((seconds, versions))
    out, kept, names = [], {}, kind_names()
    for key, name, kind, on, reading in pages:
        given = times.get(key, [])
        entry = {"name": name, "kind": names[kind], "on": on, "n": len(given), "median": None, "words": None,
                 "below_words": None, "half": None, "below_half": None}
        if kind == "task":
            item, task = reading

            def task_limit(versions, item=item, task=task):
                version = (versions.get(str(item.id)) or {}).get("version", 0)
                if (item.id, task, version) not in kept:
                    kept[(item.id, task, version)] = threshold([item], lang, spec={"task": task}, version=version)
                return kept[(item.id, task, version)]
            limits = [task_limit(v) for _, v in given] or [task_limit({})]
        else:
            limits = [reading] * max(len(given), 1)
        known = [s for s in limits if s is not None]
        entry["words"] = units.round_half_up(sum(known) / len(known), 1) if known else None
        if given:
            median = statistics.median(s for s, _ in given)
            entry.update(median=units.round_half_up(median, 1), half=units.round_half_up(median * MEDIAN_SHARE, 1),
                         below_words=sum(s < below for (s, _), below in zip(given, limits) if below is not None) / len(given),
                         below_half=sum(s < median * MEDIAN_SHARE for s, _ in given) / len(given))
        out.append(entry)
    return out


def _pages(block):
    from app import loops, runtime

    return block.pages() if loops.active(block) else runtime.split_alone(block.pages())


def _de(x):
    from app.units import fixed

    return fixed(x, 1).replace(".", ",")
