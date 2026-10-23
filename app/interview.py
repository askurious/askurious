"""Interview guides ("Interviewleitfaden"): the third kind of survey, next to the questionnaire and the live vote
(concept interviewleitfaden-konzept.md, version 3, all decisions as recommended, Ralf 2026-10-04).

An interview guide is a survey with `Survey.interview` set (state below). It has no participant link: the
researchers conduct the interviews. The interview view (`conduct`) shows the guide; opened for a stored interview
("Interviews erheben", app/interview_data.py) it saves that interview as it goes, otherwise it keeps its ticks in the
browser tab only (interview.js, sessionStorage).

Blocks carry their part in `Block.settings["part"]`:
  guide  a Themenblock: Leitfragen (app/items/guide_question.py), hints (text), stimuli (image) and tasks (ranking,
         card sort, Likert, image choice; app/interview_tasks.py), each task belonging to the Leitfrage above it
  short  the Kurzfragebogen (Witzel 2000): simple element types and the task types, filled in on paper or in the
         recording tool
  post   the Postskript (Witzel 2000): the same, written by the interviewer after the interview
Themenblöcke stand in the order of their positions; the Kurzfragebogen before or after them (state "short"), the
Postskript at the end. There is no flow logic (randomizers, conditions, loops).

  guide       /guide/<code>                the guide: Themenblöcke on the left, the element editor beside them
  settings    /guide/<code>/settings  title, research question, target group, mode, order, parts, members
  export      /guide/<code>/export         the Word documents (app/interview_docx.py)
  conduct     /guide/<code>/conversation      the interview view, for a tablet or laptop during the interview

State: {"template", "target", "mode", "order": "free" | "linear", "short": "after" | "before" | "off", "post": bool}.
"""
from flask import Blueprint, abort, g, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import autosave, settings_page
from app.interviewer import interviewer_or_signed_in
from app.extensions import db
from app.lang import de_en, english
from app.models import Block, Item, Survey

bp = Blueprint("interview", __name__, url_prefix="/guide")

PARTS = {"guide": "Themenblock", "short": "Kurzfragebogen", "post": "Postskript"}
# what each part offers to add (concept 3.2, decision I6)
GUIDE_TYPES = {"guide_question": "Leitfrage", "info": "Hinweis (Text)", "image": "Stimulus (Bild)"}
# tasks (app/interview_tasks.py, concept konzept-interview-formate, stage 1 = A + D, Ralf 2026-10-20): in a
# Themenblock under a Leitfrage (A), and in the Kurzfragebogen and the Postskript too (D); the keys are palette keys
# ("image_choice" is the single choice with images, editor PRESETS)
TASK_TYPES = {"ranking": "Rangfolge", "card_sort": "Kartensortierung", "likert": "Likert-Bewertung", "image_choice": "Bildauswahl"}
TASK_ITEM_TYPES = ("ranking", "card_sort", "likert", "single_choice")  # the element types a task is made of
FORM_TYPES = {"single_choice": "Einfachauswahl", "multiple_choice": "Mehrfachauswahl", "text_input": "Freitext",
              "date_time": "Datum / Uhrzeit", "federal_state": "Bundesland", "rating": "Bewertung", **TASK_TYPES,
              "info": "Text"}
TARGETS = {"erwachsene": "Erwachsene", "jugendliche": "Jugendliche", "kinder": "Kinder",
           "fachleute": "Fachleute (Experteninterview)", "beschaeftigte": "Beschäftigte einer Organisation",
           "patienten": "Patientinnen und Patienten", "andere": "andere Zielgruppe"}
MODES = {"praesenz": "Präsenz", "video": "Videogespräch", "telefon": "Telefon", "gemischt": "Gemischt"}
ORDERS = {"free": ("Frei springen", "Themenblöcke in beliebiger Reihenfolge, wie das Gespräch es ergibt"),
          "linear": ("Der Reihe nach", "eine Leitfrage nach der anderen mit „Weiter“ und „Zurück“")}
SHORT = {"after": "nach dem Gespräch", "before": "vor dem Gespräch", "off": "ohne Kurzfragebogen"}
# what the template "Information und Einwilligung" says about the recording (app/interview_docx.py, decision I11)
AUDIO_KEEP = {"transcript": "bis die Transkription geprüft ist", "12": "1 Jahr", "24": "2 Jahre", "60": "5 Jahre"}
DATA_KEEP = {"60": "5 Jahre", "120": "10 Jahre", "end": "bis zum Ende des Projekts"}
DEFAULTS = {"template": "leer", "target": "", "mode": "praesenz", "order": "free", "short": "after", "post": True,
            "audio": True, "audio_keep": "transcript", "data_keep": "120", "versions": [],
            "prefix": "I",  # prefix of the interviews' identifiers (app/interview_data.py)
            # the transcript export (app/transcript_export.py): the page header's prefix (empty: the guide's title) and
            # the codes of the Kurzfragebogen questions that fill "Befragte(r)" (occupation, age, gender)
            "head_title": "", "head_job": "", "head_age": "", "head_gender": ""}
# the same lists in English for the researchers' pages (app/lang.py), shown through the functions below; the German
# ones stay what the documents and the templates' content use
PARTS_EN = {"guide": "Topic block", "short": "Short questionnaire", "post": "Postscript"}
GUIDE_TYPES_EN = {"guide_question": "Guide question", "info": "Note (text)", "image": "Stimulus (image)"}
TASK_TYPES_EN = {"ranking": "Ranking", "card_sort": "Card sorting", "likert": "Likert rating", "image_choice": "Image choice"}
FORM_TYPES_EN = {"single_choice": "Single choice", "multiple_choice": "Multiple choice", "text_input": "Free text",
                 "date_time": "Date / time", "federal_state": "Federal state", "rating": "Rating", **TASK_TYPES_EN,
                 "info": "Text"}
TARGETS_EN = {"erwachsene": "Adults", "jugendliche": "Adolescents", "kinder": "Children",
              "fachleute": "Experts (expert interview)", "beschaeftigte": "Employees of an organisation",
              "patienten": "Patients", "andere": "other target group"}
MODES_EN = {"praesenz": "In person", "video": "Video call", "telefon": "Telephone", "gemischt": "Mixed"}
ORDERS_EN = {"free": ("Jump freely", "topic blocks in any order, as the conversation goes"),
             "linear": ("In order", "one guide question after the other with “Next” and “Back”")}
SHORT_EN = {"after": "after the conversation", "before": "before the conversation", "off": "without short questionnaire"}
AUDIO_KEEP_EN = {"transcript": "until the transcription is checked", "12": "1 year", "24": "2 years", "60": "5 years"}
DATA_KEEP_EN = {"60": "5 years", "120": "10 years", "end": "until the end of the project"}


def day_text(iso):
    """A stored day "YYYY-MM-DD" in the page language: 06.10.2026 or 6 Oct 2026 (as timeutil.fmt)."""
    from app.timeutil import MONTHS_EN

    iso = (iso or "")[:10]
    if english():
        try:
            y, m, d = (int(x) for x in iso.split("-"))
            return f"{d} {MONTHS_EN[m - 1]} {y}"
        except (ValueError, IndexError):
            return iso
    return ".".join(reversed(iso.split("-"))) if iso else ""


def _shown(de, en):
    return en if english() else de


def parts():
    """PARTS in the language of the page (likewise the functions below for the other lists)."""
    return _shown(PARTS, PARTS_EN)


def guide_types():
    return _shown(GUIDE_TYPES, GUIDE_TYPES_EN)


def form_types():
    return _shown(FORM_TYPES, FORM_TYPES_EN)


def task_types():
    return _shown(TASK_TYPES, TASK_TYPES_EN)


def targets():
    return _shown(TARGETS, TARGETS_EN)


def modes():
    return _shown(MODES, MODES_EN)


def orders():
    return _shown(ORDERS, ORDERS_EN)


def shorts():
    return _shown(SHORT, SHORT_EN)


def audio_keeps():
    return _shown(AUDIO_KEEP, AUDIO_KEEP_EN)


def data_keeps():
    return _shown(DATA_KEEP, DATA_KEEP_EN)


VERSION_KEYS = ("n", "at", "by", "note", "text", "sha256", "name", "zenodo", "material_license")

WITZEL = ("Witzel, A. (2000). Das problemzentrierte Interview. Forum Qualitative Sozialforschung / Forum: Qualitative "
          "Social Research, 1(1), Art. 22. https://doi.org/10.17169/fqs-1.1.1132")
SCHUETZE = "Schütze, F. (1983). Biographieforschung und narratives Interview. Neue Praxis, 13(3), 283–293."
HOPF = ("Hopf, C. (1978). Die Pseudo-Exploration: Überlegungen zur Technik qualitativer Interviews in der "
        "Sozialforschung. Zeitschrift für Soziologie, 7(2), 97–115. https://doi.org/10.1515/zfsoz-1978-0201")


def template_sources():
    from app.items.guide_question import GLAESER_LAUDEL, HELFFERICH

    return [(de_en("Leitfadenschema", "Guide scheme"), [HELFFERICH]),
            (de_en("Vorlagen", "Templates"), [WITZEL, GLAESER_LAUDEL, SCHUETZE])]


def guide_sources(survey):
    """The full citations a guide document names: the scheme, and the source of its template."""
    from app.items.guide_question import GLAESER_LAUDEL, HELFFERICH

    return [HELFFERICH] + {"witzel": [WITZEL], "narrativ": [SCHUETZE], "experte": [GLAESER_LAUDEL]}.get(state(survey)["template"], [])


def guide_runs(block):
    """A Themenblock for a document: its Leitfragen together in tables, hints and stimuli between them where they
    stand. [{"table": bool, "elements": [...]}]"""
    out = []
    for item in block.items:
        if item.type == "page_break":
            continue
        table = item.type == "guide_question"
        if out and out[-1]["table"] and table:
            out[-1]["elements"].append(item)
        else:
            out.append({"table": table, "elements": [item]})
    return out


def state(survey):
    raw = dict(survey.interview or {})
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    out["order"] = out["order"] if out["order"] in ORDERS else "free"
    out["short"] = out["short"] if out["short"] in SHORT else "after"
    out["mode"] = out["mode"] if out["mode"] in MODES else "praesenz"
    out["target"] = out["target"] if out["target"] in TARGETS else ""
    out["post"] = bool(out["post"])
    out["audio"] = bool(out["audio"])
    out["audio_keep"] = out["audio_keep"] if out["audio_keep"] in AUDIO_KEEP else "transcript"
    out["data_keep"] = out["data_keep"] if out["data_keep"] in DATA_KEEP else "120"
    out["prefix"] = out["prefix"] if out["prefix"] in ("I", "E", "P", "B") else "I"
    for key in ("head_title", "head_job", "head_age", "head_gender"):
        out[key] = out[key][:120] if isinstance(out[key], str) else ""
    out["versions"] = [{k: v.get(k) for k in VERSION_KEYS} for v in out["versions"] if isinstance(v, dict)]         if isinstance(out["versions"], list) else []
    return out


def guide_state(survey):
    """The settings without the versions: what a copy or a survey file takes (its versions stay with the original)."""
    return {k: v for k, v in state(survey).items() if k != "versions"}


def part(block):
    value = (block.settings or {}).get("part")
    return value if value in PARTS else "guide"


def guide_blocks(survey):
    return sorted((b for b in survey.blocks if part(b) == "guide"), key=lambda b: b.position)


def form_block(survey, which):
    """The block of the Kurzfragebogen ("short") or the Postskript ("post"), if there is one."""
    return next((b for b in sorted(survey.blocks, key=lambda b: b.position) if part(b) == which), None)


def shown_form(survey, which):
    """The block of a part if the guide uses it (settings), else None."""
    st = state(survey)
    on = st["short"] != "off" if which == "short" else st["post"]
    return form_block(survey, which) if on else None


def ordered_blocks(survey):
    """All blocks the guide shows, in the order of an interview: [(part, block)]."""
    st, out = state(survey), []
    short, post = shown_form(survey, "short"), shown_form(survey, "post")
    if short is not None and st["short"] == "before":
        out.append(("short", short))
    out += [("guide", b) for b in guide_blocks(survey)]
    if short is not None and st["short"] == "after":
        out.append(("short", short))
    if post is not None:
        out.append(("post", post))
    return out


def addable(block):
    """What a block offers to add: {type key: name in the language of the page}."""
    return guide_types() if part(block) == "guide" else form_types()


def may_add(survey, block, kind_key):
    """Whether an element of this type may go into this block (editor.add_item): in a guide only the types of the
    block's part (a Themenblock also the task types), the Leitfrage nowhere else."""
    if survey.interview is None:
        return kind_key != "guide_question"
    if part(block) == "guide":
        return kind_key in GUIDE_TYPES or kind_key in TASK_ITEM_TYPES
    return kind_key in FORM_TYPES


def is_task(item):
    """A task of a Themenblock (app/interview_tasks.py): an element of a task type there."""
    return item.type in TASK_ITEM_TYPES and item.block is not None and part(item.block) == "guide"


def planned_minutes(block):
    return sum(i.kind.minutes(i) for i in block.items if i.type == "guide_question")


# --- Unterfragen (concept unterfragen-konzept, Ralf 2026-10-18: A, F, E1-E6 as recommended) -------------------


def is_sub(item):
    """Indented as an Unterfrage (`config.sub`); whether it has a Leitfrage to belong to says `outline`."""
    return item.type == "guide_question" and bool((item.config or {}).get("sub"))


def outline(block):
    """The elements of a block with the Leitfrage each Unterfrage belongs to: [(item, Leitfrage or None)]. An
    Unterfrage belongs to the Leitfrage it follows directly or after other Unterfragen of it; one without such a
    Leitfrage (first in its block, after a hint or a stimulus) stands as a Leitfrage of its own (`settle`). A task
    between them does not part an Unterfrage from its Leitfrage (it belongs to that Leitfrage too, `task_owner`)."""
    out, main = [], None
    guide = part(block) == "guide"
    for item in block.items:
        if item.type == "page_break":
            continue
        if item.type != "guide_question":
            if not (guide and item.type in TASK_ITEM_TYPES):
                main = None
            out.append((item, None))
        elif is_sub(item) and main is not None:
            out.append((item, main))
        else:
            main = item
            out.append((item, None))
    return out


def subs_of(block):
    """{Leitfrage id: [its Unterfragen in their order]}"""
    out = {}
    for item, main in outline(block):
        if main is not None:
            out.setdefault(main.id, []).append(item)
    return out


def task_owner(block):
    """{task id: the Leitfrage it belongs to}: a task of a Themenblock belongs to the Leitfrage above it (directly or
    after its Unterfragen and other tasks of it); one without (first in its block, after a hint or a stimulus) stands
    alone. The interview view shows it on its Leitfrage's page, beneath the Unterfragen."""
    out, main = {}, None
    if part(block) != "guide":
        return out
    for item, leitfrage in outline(block):
        if item.type == "guide_question":
            main = leitfrage or item
        elif item.type in TASK_ITEM_TYPES:
            if main is not None:
                out[item.id] = main
        else:
            main = None
    return out


def tasks_of(block):
    """{Leitfrage id: [its tasks in their order]}"""
    out, owned = {}, task_owner(block)
    for item in block.items:
        if item.id in owned:
            out.setdefault(owned[item.id].id, []).append(item)
    return out


def members_of(block):
    """{Leitfrage id: the Unterfragen and tasks that move with it, in their order}"""
    return {u[0].id: u[1:] for u in units(block) if len(u) > 1}


def task_at(block, selected):
    """Where "+ Aufgabe" puts the new task in `block` (an index of block.items): after the unit of the selected
    Leitfrage (or of the Leitfrage the selected Unterfrage or task belongs to); None (the end) without one there."""
    if selected is None or selected.block_id != block.id:
        return None
    unit = next((u for u in units(block) if selected in u), None)
    if unit is None or unit[0].type != "guide_question":
        return None
    return block.items.index(unit[-1]) + 1


def units(block):
    """The elements of a block as they move: a Leitfrage with its Unterfragen and its tasks, every other element
    alone. [[item]]"""
    out, owned = [], task_owner(block)
    for item, main in outline(block):
        if (main is not None or item.id in owned) and out:
            out[-1].append(item)
        else:
            out.append([item])
    return out


def settle(block):
    """An Unterfrage without a Leitfrage to belong to becomes a Leitfrage again (after a move or a deletion)."""
    belongs = {item.id for item, main in outline(block) if main is not None}
    for item in block.items:
        if is_sub(item) and item.id not in belongs:
            item.config = {**(item.config or {}), "sub": False}


def _set_sub(item, on):
    config = {**(item.config or {}), "sub": on}
    if on:
        config["core"] = False  # an Unterfrage is never a Kernfrage (E3)
    item.config = config


def add_sub(survey, main, question="", when=""):
    """A new Unterfrage after the last Unterfrage of the Leitfrage `main` (the guide's "+ Unterfrage", the AI
    assistant's suggestions). Returns it."""
    block = main.block
    new = _guide_item(survey, ("guide_question", question, [], [], [], "", False, 10))
    _set_sub(new, True)
    if when:
        lang = survey.default_language
        new.texts = {**new.texts, lang: {**new.texts[lang], "when": when}}
    rows = [i for i in block.items if i.type != "page_break"]
    group = [i for i in next(u for u in units(block) if u[0] is main) if i.type == "guide_question"]  # before its tasks
    at = rows.index(group[-1]) + 1
    _arrange({block: rows[:at] + [new] + rows[at:] + [i for i in block.items if i.type == "page_break"]})
    db.session.flush()
    return new


def _arrange(rows_by_block):
    """Give each block its rows in this order ({block: [item]}). All lists are built before anything changes: an
    autoflush in between would treat an item as an orphan (delete-orphan) and delete it (editor _place)."""
    with db.session.no_autoflush:
        for block, rows in rows_by_block.items():
            block.items = rows
        for block, rows in rows_by_block.items():
            for n, row in enumerate(rows):
                row.position = n


def _survey(code, need="viewer"):
    from app.surveys import get_survey

    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.interview is None:
        abort(404)
    return get_survey(survey.id, need=need)


# --- templates (concept 4.1, decision I4) ----------------------------------------------------------------------
# blocks: (title, part, [elements]); an element: ("guide_question", prompt, aspects, keep, probe, hint, core, minutes),
# ("info", text), or a form element (type, question, options or None)

KEEP = ["Und wie ging es dann weiter?", "Können Sie das noch genauer erzählen?", "Wie war das für Sie?"]
SHORT_DEFAULT = [
    ("text_input:integer", "Wie alt sind Sie?", None),
    ("single_choice", "Welches Geschlecht haben Sie?", ["weiblich", "männlich", "divers", "keine Angabe"]),
    ("single_choice", "Was ist Ihr höchster Bildungsabschluss?",
     ["kein Abschluss", "Haupt- oder Volksschulabschluss", "Mittlere Reife", "Abitur oder Fachhochschulreife",
      "Hochschulabschluss", "anderer Abschluss"]),
    ("single_choice", "Sind Sie zurzeit erwerbstätig?", ["Vollzeit", "Teilzeit", "nicht erwerbstätig", "in Ausbildung oder Studium"]),
]
POST_DEFAULT = [
    ("date_time", "Datum des Interviews", None),
    ("single_choice", "Interviewform", list(MODES.values())),
    ("text_input:long", "Gesprächsatmosphäre und Rahmen (Ort, Störungen, Anwesende)", None),
    ("text_input:long", "Inhalte vor dem Einschalten und nach dem Ausschalten der Aufnahme", None),
    ("text_input:long", "Nonverbale Auffälligkeiten", None),
    ("text_input:long", "Ideen und Auffälligkeiten für die Auswertung", None),
]
OPENING = ("info", "Begrüßung und Dank. Ablauf, Dauer und Tonaufnahme erklären, Fragen klären, Einwilligung einholen. "
                   "Die Aufnahme erst nach der Einwilligung starten.")
CLOSING_Q = ("guide_question", "Wir sind am Ende angekommen. Gibt es noch etwas, das Ihnen wichtig ist und über das wir "
                               "noch nicht gesprochen haben?", [], [], [], "Dank; Aufnahme beenden; Postskript direkt im Anschluss.", False, 5)

# name and description of each template in English (its blocks and questions are content in the survey's language)
TEMPLATES_EN = {
    "leer": ("Empty", "one topic block, short questionnaire and postscript"),
    "witzel": ("Problem-centred interview", "after Witzel (2000): opening, two topic blocks, short questionnaire after the "
                                            "conversation, postscript"),
    "experte": ("Expert interview", "after Gläser & Laudel (2010): the person's role, subject topics with core questions, closing"),
    "narrativ": ("Narrative interview", "after Schütze (1983): narrative prompt, immanent and exmanent follow-up questions"),
}


def template_names():
    """{key: (name, description)} of the templates in the language of the page."""
    return {key: (TEMPLATES_EN[key] if english() else spec[:2]) for key, spec in TEMPLATES.items()}


TEMPLATES = {
    "leer": ("Leer", "ein Themenblock, Kurzfragebogen und Postskript", "after", [
        ("Themenblock 1", "guide", [("guide_question", "", [], [], [], "", False, 10)]),
    ]),
    "witzel": ("Problemzentriertes Interview", "nach Witzel (2000): Einstieg, zwei Themenblöcke, Kurzfragebogen nach dem Gespräch, Postskript", "after", [
        ("Einstieg", "guide", [OPENING,
                               ("guide_question", "Zum Einstieg: Erzählen Sie doch einmal, wie Sie zu [Thema] gekommen sind.",
                                ["Anlass", "erste Erfahrungen", "beteiligte Personen"], KEEP[:2],
                                ["Seit wann beschäftigt Sie [Thema]?"], "Nicht unterbrechen, Pausen aushalten.", True, 10)]),
        ("Themenblock 1", "guide", [("guide_question", "Wie erleben Sie [Thema 1] im Alltag? Erzählen Sie doch einmal.",
                                     ["Aspekt A", "Aspekt B"], KEEP, ["Konkrete Frage zu Aspekt A?"], "", True, 15)]),
        ("Themenblock 2", "guide", [("guide_question", "Wenn Sie an [Thema 2] denken: Was fällt Ihnen dazu ein?",
                                     ["Aspekt C", "Aspekt D"], KEEP, ["Konkrete Frage zu Aspekt C?"], "", False, 15)]),
        ("Abschluss", "guide", [CLOSING_Q]),
    ]),
    "experte": ("Experteninterview", "nach Gläser & Laudel (2010): Rolle der Person, Sachthemen mit Kernfragen, Abschluss", "after", [
        ("Einstieg", "guide", [OPENING,
                               ("guide_question", "Beschreiben Sie bitte zu Beginn Ihre Aufgaben und Ihre Rolle in [Organisation].",
                                ["Position", "Zuständigkeiten", "Dauer der Tätigkeit"], KEEP[1:2], [], "", True, 5)]),
        ("Sachthema 1", "guide", [("guide_question", "Wie läuft [Prozess] bei Ihnen ab?",
                                   ["Beteiligte", "Entscheidungen", "Probleme"], KEEP[1:2], ["Wer entscheidet über …?"], "", True, 15)]),
        ("Sachthema 2", "guide", [("guide_question", "Welche Entwicklungen sehen Sie bei [Thema]?",
                                   ["Ursachen", "Folgen"], KEEP[1:2], ["Woran machen Sie das fest?"], "", False, 15)]),
        ("Abschluss", "guide", [("guide_question", "Mit wem sollte ich zu diesem Thema unbedingt noch sprechen?",
                                 [], [], [], "Kontaktdaten weiterer Personen nicht hier notieren.", False, 5), CLOSING_Q]),
    ]),
    "narrativ": ("Narratives Interview", "nach Schütze (1983): Erzählaufforderung, immanente und exmanente Nachfragen", "off", [
        ("Erzählaufforderung", "guide", [OPENING,
                                         ("guide_question", "Ich möchte Sie bitten, mir Ihre Geschichte zu erzählen: wie alles "
                                          "angefangen hat und wie es weiterging, bis heute. Sie können sich dafür so viel Zeit nehmen, "
                                          "wie Sie möchten. Ich werde Sie nicht unterbrechen.",
                                          [], [], [], "Die Haupterzählung nicht unterbrechen; Notizen für Nachfragen machen.", True, 45)]),
        ("Immanente Nachfragen", "guide", [("info", "Nachfragen zu Stellen der Erzählung, die unklar, angedeutet oder "
                                                    "abgebrochen blieben, in der Reihenfolge der Erzählung: „Sie haben erzählt, dass … "
                                                    "Können Sie darüber noch mehr erzählen?“")]),
        ("Exmanente Nachfragen", "guide", [("info", "Eigene Fragen der Forschung, die in der Erzählung nicht vorkamen; erst jetzt, "
                                                    "auch Fragen nach Beschreibungen und Begründungen.")]),
    ]),
}


def _form_item(survey, kind_key, question, options):
    from app import items
    from app.editor import _unique_code
    from app.items.choice import _new_id

    lang = survey.default_language
    variant = ""
    if ":" in kind_key:
        kind_key, variant = kind_key.split(":", 1)
    kind = items.get(kind_key)
    config, texts = kind.default_config(), {**kind.default_texts(lang), "question": question}
    if options:
        config["options"] = [{"id": _new_id(), "code": str(n)} for n in range(1, len(options) + 1)]
        texts["options"] = {o["id"]: label for o, label in zip(config["options"], options)}
    if variant == "integer":
        config.update(validation="integer", min=0, max=120)
    if variant == "long":
        config.update(multiline=True)
    return Item(type=kind_key, answer_mode="optional", code=_unique_code(survey, "K"), config=config, texts={lang: texts})


def _guide_item(survey, spec):
    from app.editor import _unique_code
    from app.items.base import new_id

    lang = survey.default_language
    if spec[0] == "info":
        return Item(type="info", answer_mode="optional", code=_unique_code(survey, "H"), config={}, texts={lang: {"question": spec[1]}})
    _, prompt, aspects, keep, probe, hint, core, minutes = spec
    config, texts = {"core": core, "minutes": minutes}, {"question": prompt, "hint": hint}
    for key, rows in (("aspects", aspects), ("keep", keep), ("probe", probe)):
        ids = [new_id() for _ in rows]
        config[key], texts[key] = ids, dict(zip(ids, rows))
    return Item(type="guide_question", answer_mode="optional", code=_unique_code(survey, "L"), config=config, texts={lang: texts})


def reflow(survey, guides=None):
    """A flat flow in the order of a guide: the Themenblöcke (in the order `guides`, else as they stand), then the
    Kurzfragebogen and the Postskript. flow.normalize then gives every block its position."""
    from app import flow

    guides = guides if guides is not None else guide_blocks(survey)
    rest = sorted((b for b in survey.blocks if part(b) != "guide"), key=lambda b: (part(b) == "post", b.position))
    db.session.flush()
    survey.flow = {"t": "root", "children": [{"t": "block", "block": b.id} for b in list(guides) + rest]}
    flow.normalize(survey)


def _add_block(survey, title, which, position):
    block = Block(position=position, titles={survey.default_language: title}, settings={"part": which})
    survey.blocks.append(block)
    return block


def _fill_block(survey, block, elements):
    for n, spec in enumerate(elements):
        item = _guide_item(survey, spec) if part(block) == "guide" else _form_item(survey, *spec)
        item.position = n
        block.items.append(item)
        db.session.flush()  # the next element's code is unique against this one


def build_template(survey, key):
    """The blocks of a template, with the Kurzfragebogen and the Postskript."""
    _, _, short_when, blocks = TEMPLATES[key]
    n = 0
    for title, which, elements in blocks:
        _fill_block(survey, _add_block(survey, title, which, n), elements)
        n += 1
    _fill_block(survey, _add_block(survey, "Kurzfragebogen", "short", n), SHORT_DEFAULT)
    _fill_block(survey, _add_block(survey, "Postskript", "post", n + 1), POST_DEFAULT)
    return short_when


# --- creating --------------------------------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """One page: title, language, research question, target group, mode, template, order (concept 4.1)."""
    from app import kind_access, surveys
    from app.models import utcnow

    refused = kind_access.creation_refused(current_user, "interview")  # says why
    if refused:
        return refused
    if request.method == "POST":
        lang = request.form.get("language", "de")
        lang = lang if lang in surveys.LANGUAGE_NAMES else "de"
        key = request.form.get("template") if request.form.get("template") in TEMPLATES else "leer"
        from app.timeutil import fmt

        # unnamed: named in the researcher's language (UI/UX audit 2026-10-07, W116)
        title = (" ".join(request.form.get("title", "").split())[:300]
                 or de_en("Interviewleitfaden {d}", "Interview guide {d}", d=fmt(utcnow(), False, "en" if english() else "de")))
        order = request.form.get("order") if request.form.get("order") in ORDERS else "free"
        st = {**DEFAULTS, "template": key, "order": order,
              "target": request.form.get("target") if request.form.get("target") in TARGETS else "",
              "mode": request.form.get("mode") if request.form.get("mode") in MODES else "praesenz"}
        survey = Survey(title=title, created_by_id=current_user.id,
                        languages=[lang], default_language=lang, language_choice=False, interview=st,
                        research_question=request.form.get("research_question", "").strip()[:3000],
                        erase_code=False, retention=None, setup_step=None)
        db.session.add(survey)
        surveys._add_member(survey, current_user, current_user.name)
        db.session.flush()
        short_when = build_template(survey, key)
        survey.interview = {**st, "short": short_when}
        reflow(survey)
        db.session.commit()
        flash(de_en("Interviewleitfaden angelegt.", "Interview guide created."), "success")
        if request.form.get("then") == "ki":  # the AI assistant's first step (concept 7.3)
            return redirect(url_for("interview_ai.develop", code=survey.code))
        return redirect(url_for("interview.guide", code=survey.code))
    from app import wizard

    return render_template("interview/new.html", templates=template_names(), orders=orders(), targets=targets(), modes=modes(),
                           languages=surveys.LANGUAGE_NAMES, sources=template_sources(), starts=wizard.starts("leitfaden"),
                           start=wizard.start_of())


# --- the guide -------------------------------------------------------------------------------------------------


@bp.route("/<code>")
@login_required
def guide(code):
    survey = _survey(code)
    chosen = request.args.get("item", type=int)
    items = {i.id: i for b in survey.blocks for i in b.items}
    from app import interview_tasks

    return render_template("interview/guide.html", survey=survey, st=state(survey), rows=ordered_blocks(survey),
                           outline=outline, subs_of=subs_of, task_owner=task_owner, members_of=members_of, is_task=is_task,
                           task_kind=interview_tasks.kind_name, task_at=task_at, task_types=task_types(),
                           parts=parts(), addable=addable, planned=planned_minutes, selected=items.get(chosen),
                           total=sum(planned_minutes(b) for b in guide_blocks(survey)),
                           editable=survey.can(current_user, "editor") and survey.is_editable)


@bp.route("/<code>/control", methods=["POST"])
@login_required
def control(code):
    """Themenblöcke: add, rename, move up or down or by dragging (place). Elements are added, moved and deleted by the element editor's
    routes (editor.add_item, place_item, delete_item), blocks deleted by editor.delete_block."""
    from app import undo

    survey = _survey(code, need="editor")
    if not survey.is_editable:
        abort(400)
    action = request.form.get("action", "")
    blocks = guide_blocks(survey)
    block = next((b for b in survey.blocks if b.id == request.form.get("block", type=int)), None)
    step = undo.begin(survey)
    back = url_for("interview.guide", code=survey.code)
    if action == "add_block":
        n = len(blocks) + 1
        names = {b.title() for b in blocks}
        while f"Themenblock {n}" in names:
            n += 1
        new = _add_block(survey, f"Themenblock {n}", "guide", max((b.position for b in survey.blocks), default=-1) + 1)
        reflow(survey, blocks + [new])
        step.done(undo.both("Themenblock {n} hinzugefügt", "Topic block {n} added", n=n), block=new.id)
        back += f"#block-{new.id}"
    elif action == "rename_block" and block is not None:
        title = " ".join(request.form.get("title", "").split())[:200]
        if title:
            block.titles = {**(block.titles or {}), survey.default_language: title}
            step.done(undo.both("Block in „{title}“ umbenannt", "Block renamed to “{title}”", title=title), block=block.id)
    elif action in SUB_ACTIONS:
        item = next((i for b in survey.blocks for i in b.items if i.id == request.form.get("item", type=int)), None)
        if item is None or part(item.block) != "guide":
            abort(400)
        return _item_action(survey, item, action, step)
    elif action in ("up", "down") and block is not None and part(block) == "guide":
        at = blocks.index(block)
        other = at - 1 if action == "up" else at + 1
        if 0 <= other < len(blocks):
            blocks[at], blocks[other] = blocks[other], blocks[at]
            reflow(survey, blocks)
            step.done(undo.both("Themenblock „{title}“ verschoben", "Topic block “{title}” moved", title=block.title()),
                      block=block.id)
        back += f"#block-{block.id}"
    elif action == "place" and block is not None and part(block) == "guide":
        # dragged by its handle (app.js): before or after another Themenblock. editor.place_block stays closed to a
        # guide (CLOSED_EDITOR_PAGES); the drag went there and was refused with 404 (Ralf, 2026-10-20)
        target = next((b for b in blocks if b.id == request.form.get("target", type=int)), None)
        if target is None:
            abort(400)
        if target is not block:
            rest = [b for b in blocks if b is not block]
            at = rest.index(target) + (0 if request.form.get("mode") == "before" else 1)
            reflow(survey, rest[:at] + [block] + rest[at:])
            step.done(undo.both("Themenblock „{title}“ verschoben", "Topic block “{title}” moved", title=block.title()),
                      block=block.id)
        back += f"#block-{block.id}"
    else:
        abort(400)
    db.session.commit()
    return redirect(back)


SUB_ACTIONS = ("indent", "outdent", "item_up", "item_down", "add_sub", "delete_item")


def _item_action(survey, item, action, step):
    """An element of a Themenblock: indent as an Unterfrage or back, move with its Unterfragen, add an Unterfrage to
    it, delete it (its Unterfragen stand as Leitfragen then, E4). The list of the guide shows the buttons."""
    from app import trash, undo

    block, back = item.block, url_for("interview.guide", code=survey.code, item=item.id)
    rows = [i for i in block.items if i.type != "page_break"]
    breaks = [i for i in block.items if i.type == "page_break"]
    above = dict(outline(block))
    if action == "indent":
        at = rows.index(item)
        if item.type == "guide_question" and above[item] is None and at > 0 and rows[at - 1].type == "guide_question":
            _set_sub(item, True)
            step.done(undo.both("„{code}“ als Unterfrage eingerückt", "“{code}” indented as a sub-question", code=item.code),
                      item=item.id)
    elif action == "outdent":
        if is_sub(item):
            _set_sub(item, False)
            step.done(undo.both("„{code}“ ausgerückt", "“{code}” outdented", code=item.code), item=item.id)
    elif action in ("item_up", "item_down"):
        groups = units(block)
        if above[item] is not None or item.id in task_owner(block):
            # an Unterfrage moves among the Unterfragen of its Leitfrage, a task among them and its other tasks
            group = next(u for u in groups if item in u)
            at = group.index(item)
            other = at - 1 if action == "item_up" else at + 1
            if 1 <= other < len(group):
                group[at], group[other] = group[other], group[at]
        else:
            at = next(n for n, u in enumerate(groups) if u[0] is item)
            other = at - 1 if action == "item_up" else at + 1
            if 0 <= other < len(groups):
                groups[at], groups[other] = groups[other], groups[at]
        _arrange({block: [i for u in groups for i in u] + breaks})
        step.done(undo.both("„{code}“ verschoben", "“{code}” moved", code=item.code), item=item.id)
    elif action == "add_sub":
        main = above[item] or item
        if main.type != "guide_question":
            abort(400)
        new = add_sub(survey, main)
        step.done(undo.both("Unterfrage zu „{code}“ hinzugefügt", "Sub-question to “{code}” added", code=main.code), item=new.id)
        back = url_for("interview.guide", code=survey.code, item=new.id, neu=1)
        item = new
    elif action == "delete_item":
        for sub in subs_of(block).get(item.id, []):  # E4: they stand as Leitfragen of their own
            _set_sub(sub, False)
        kept = trash.put(item)
        block.items.remove(item)
        for n, row in enumerate(block.items):
            row.position = n
        step.done(undo.both("„{code}“ gelöscht", "“{code}” deleted", code=item.code), item=item.id, block=block.id)
        if kept:
            flash(de_en("„{code}“ ist gelöscht; „Rückgängig“ bringt es zurück.", "“{code}” is deleted; “Undo” brings it back.",
                        code=item.code), "undo")
        settle(block)
        db.session.commit()
        return redirect(url_for("interview.guide", code=survey.code))
    settle(block)
    db.session.commit()
    return redirect(back + f"#item-{item.id}")


@bp.route("/<code>/items/<int:item_id>/place", methods=["POST"])
@login_required
def place_item(code, item_id):
    """Drag and drop in the guide's list: an element to `position` (among the other rows) of block `block_id`. A
    Leitfrage takes its Unterfragen along; a Leitfrage dropped between a Leitfrage and its Unterfragen or between two
    Unterfragen becomes an Unterfrage, one dropped elsewhere a Leitfrage (Ralf 2026-10-18)."""
    from app import undo

    survey = _survey(code, need="editor")
    if not survey.is_editable:
        abort(400)
    item = next((i for b in survey.blocks for i in b.items if i.id == item_id), None)
    target = next((b for b in survey.blocks if b.id == request.form.get("block_id", type=int)), None)
    if item is None or target is None:
        abort(404)
    if not may_add(survey, target, item.type):
        flash(de_en("„{kind}“ gehört nicht in „{block}“.", "“{kind}” does not belong in “{block}”.", kind=item.kind.label,
                    block=target.title() or de_en("diesen Block", "this block")), "error")
        return redirect(url_for("interview.guide", code=survey.code))
    source = item.block
    unit = next(u for u in units(source) if item in u) if part(source) == "guide" else [item]
    if unit[0] is not item:
        unit = [item]  # an Unterfrage moves alone
    onto = request.form.get("onto", type=int)
    if onto:
        return _place_onto(survey, item, unit, source, target, onto)
    shown = [i for i in target.items if i.type != "page_break" and i is not item]
    pos = max(0, min(request.form.get("position", type=int) or 0, len(shown)))
    before, after = (shown[pos - 1] if pos else None), (shown[pos] if pos < len(shown) else None)
    if before in unit[1:]:  # dropped among its own Unterfragen: nothing changes
        return redirect(url_for("interview.guide", code=survey.code, item=item.id) + f"#item-{item.id}")
    step = undo.begin(survey)
    belongs = dict(outline(target))
    if item.type == "guide_question" and part(target) == "guide":
        _set_sub(item, bool(before is not None and before.type == "guide_question" and after is not None
                            and after not in unit and belongs.get(after) is not None))
    rest = [i for i in target.items if i.type != "page_break" and i not in unit]
    at = rest.index(before) + 1 if before is not None else 0
    plan = {target: rest[:at] + unit + rest[at:] + [i for i in target.items if i.type == "page_break"]}
    if source is not target:
        plan[source] = [i for i in source.items if i not in unit]
    _arrange(plan)
    for block in plan:
        if part(block) == "guide":
            settle(block)
    step.done(undo.both("„{code}“ verschoben", "“{code}” moved", code=item.code), item=item.id)
    db.session.commit()
    return redirect(url_for("interview.guide", code=survey.code, item=item.id) + f"#item-{item.id}")


def _place_onto(survey, item, unit, source, target, onto):
    """A Leitfrage dropped onto the middle of another one (`onto`, Ralf 2026-10-18): it becomes an Unterfrage of it,
    after its last Unterfrage; dropped onto an Unterfrage, an Unterfrage of the same Leitfrage right after that one. Its
    own Unterfragen come along as Unterfragen of the new Leitfrage too (one level only, as with → Einrücken)."""
    from app import undo

    back = url_for("interview.guide", code=survey.code, item=item.id) + f"#item-{item.id}"
    rows = [i for i in target.items if i.type != "page_break"]
    on = next((i for i in rows if i.id == onto), None)
    if on is None or on in unit or item.type != "guide_question" or part(target) != "guide":
        abort(400)
    belongs = dict(outline(target))
    main = belongs.get(on) or on
    if main.type != "guide_question" or main in unit:
        abort(400)
    group = next(u for u in units(target) if u[0] is main)
    last = on if belongs.get(on) is not None else group[-1]
    if last in unit:  # its own Unterfrage was the last one: place after the one before it
        last = [i for i in group if i not in unit][-1]
    step = undo.begin(survey)
    for moved in unit:
        _set_sub(moved, True)
    rest = [i for i in rows if i not in unit]
    at = rest.index(last) + 1
    plan = {target: rest[:at] + unit + rest[at:] + [i for i in target.items if i.type == "page_break"]}
    if source is not target:
        plan[source] = [i for i in source.items if i not in unit]
    _arrange(plan)
    for block in plan:
        if part(block) == "guide":
            settle(block)
    step.done(undo.both("„{code}“ als Unterfrage zu „{main}“ verschoben", "“{code}” moved as a sub-question to “{main}”",
                        code=item.code, main=main.code), item=item.id)
    db.session.commit()
    return redirect(back)


def _short_items(survey):
    """[(code, question)] of the Kurzfragebogen, to pick what fills "Befragte(r)" in the transcript head."""
    from app import interview_data
    from app.items.base import plain

    return [(i.code, f"{i.code}: {plain(i.text('question'))[:80]}")
            for i in interview_data.form_items(survey, "short") if i.kind.collects_answer]


# --- settings --------------------------------------------------------------------------------------------------


@bp.route("/<code>/settings", methods=["GET", "POST"])
@login_required
@autosave.aware
def settings(code):
    from app import edit_guard, surveys

    survey = _survey(code)
    if request.method == "GET":
        q = request.args.get("q", "").strip()  # co-owners invited by ORCID iD, as for a survey (surveys/_members.html)
        results = surveys._search_people(q, survey) if q and survey.can(current_user, "admin") else None
        return render_template("interview/settings.html", survey=survey, st=state(survey), q=q, results=results,
                               sums=settings_page.basic_summaries(survey),
                               targets=targets(), modes=modes(), orders=orders(), shorts=shorts(), audio_keep=audio_keeps(),
                               data_keep=data_keeps(), short_items=_short_items(survey), prefixes=__import__("app.interview_data", fromlist=["prefixes"]).prefixes())
    survey = _survey(code, need="admin")  # settings are the admins' (concept 5, decision I12)
    which = request.form.get("part", "")
    section = {"titel": "meta", "interview": "interview"}.get(which)
    if section is None:
        abort(400)
    conflict = surveys._settings_conflict(survey, section)
    back = redirect(url_for("interview.settings", code=survey.code) + f"#{which}")
    if which == "titel":
        title = " ".join(request.form.get("title", "").split())[:300]
        if not title:
            db.session.rollback()
            flash(de_en("Der Titel darf nicht leer sein.", "The title must not be empty."), "error")
            return back
        survey.title = title
        survey.research_question = request.form.get("research_question", "").strip()[:3000]
    else:
        st = state(survey)
        form = request.form
        st.update(target=form.get("target") if form.get("target") in TARGETS else "",
                  mode=form.get("mode") if form.get("mode") in MODES else st["mode"],
                  order=form.get("order") if form.get("order") in ORDERS else st["order"],
                  short=form.get("short") if form.get("short") in SHORT else st["short"],
                  post=bool(form.get("post")), audio=bool(form.get("audio")),
                  audio_keep=form.get("audio_keep") if form.get("audio_keep") in AUDIO_KEEP else st["audio_keep"],
                  data_keep=form.get("data_keep") if form.get("data_keep") in DATA_KEEP else st["data_keep"],
                  prefix=form.get("prefix") if form.get("prefix") in ("I", "E", "P", "B") else st["prefix"])
        if "head_title" in form:
            codes = {i.code for i in __import__("app.interview_data", fromlist=["form_items"]).form_items(survey, "short")}
            st.update(head_title=" ".join(form.get("head_title", "").split())[:120],
                      **{k: form.get(k) if form.get(k) in codes else "" for k in ("head_job", "head_age", "head_gender")})
        survey.interview = st
        last = max((b.position for b in survey.blocks), default=-1)
        if st["short"] != "off" and form_block(survey, "short") is None:  # switched on again: an empty part
            _add_block(survey, "Kurzfragebogen", "short", last + 1)
            last += 1
        if st["post"] and form_block(survey, "post") is None:
            _add_block(survey, "Postskript", "post", last + 1)
        reflow(survey)
    if conflict:
        return edit_guard.show_again(lambda: settings(survey.code))
    from app import undo

    surveys._settings_saved(survey, section, name={"titel": undo.both("Titel und Forschungsfrage", "Title and research question"),
                                                   "interview": undo.both("Interview", "Interview")}[which])
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return back


# --- export ----------------------------------------------------------------------------------------------------

DOCUMENTS_EN = {
    "leitfaden": ("Guide", "per topic block the table with narrative prompt, aspects, maintenance questions and specific "
                           "questions (Helfferich, 2011), with notes, core questions and duration"),
    "interviewkarte": ("Interview card", "only narrative prompts and aspects, large, one page per topic block, for the "
                                         "conversation without a device"),
    "einwilligung": ("Information and consent", "template to sign, from your settings (audio recording, retention), "
                                                "to adapt to your study and your ethics committee"),
    "kurzfragebogen": ("Short questionnaire", "to fill in on paper, with a field for the interview identifier"),
    "postskript": ("Postscript", "for the notes right after the interview, with a field for the interview identifier"),
}

DOCUMENTS = {
    "leitfaden": ("Leitfaden", "je Themenblock die Tabelle mit Erzählaufforderung, Aspekten, Aufrechterhaltungsfragen und "
                               "konkreten Fragen (Helfferich, 2011), dazu Hinweise, Kernfragen und Dauer"),
    "interviewkarte": ("Interviewkarte", "nur Erzählaufforderungen und Aspekte, groß, eine Seite je Themenblock, für das Gespräch ohne Gerät"),
    "einwilligung": ("Information und Einwilligung", "Vorlage zum Unterschreiben aus Ihren Einstellungen (Tonaufnahme, Aufbewahrung), "
                                                     "zum Anpassen an Ihre Studie und Ihre Ethikkommission"),
    "kurzfragebogen": ("Kurzfragebogen", "zum Ausfüllen auf Papier, mit Feld für die Interviewkennung"),
    "postskript": ("Postskript", "für die Notizen direkt nach dem Interview, mit Feld für die Interviewkennung"),
}


def documents(survey):
    """The documents this guide offers: the forms only while their part is used."""
    names = DOCUMENTS_EN if english() else DOCUMENTS
    return {k: v for k, v in names.items()
            if not (k == "kurzfragebogen" and shown_form(survey, "short") is None)
            and not (k == "postskript" and shown_form(survey, "post") is None)}


@bp.route("/<code>/export")
@login_required
def export(code):
    from app import interview_versions, qdc

    survey = _survey(code)
    return render_template("interview/export.html", survey=survey, documents=documents(survey), day=day_text,
                           versions=interview_versions.listing(survey), notes=interview_versions.notes(),
                           material_licenses=interview_versions.MATERIAL_LICENSES,
                           editable=survey.can(current_user, "editor"), zenodo_targets=_zenodo_targets(survey),
                           may_delete=survey.can(current_user, "admin"),
                           sources=[(de_en("Kategoriensystem", "Category system"), [qdc.KUCKARTZ, qdc.REFI])])


@bp.route("/<code>/print")
@login_required
def print_view(code):
    """The guide to print or save as PDF from the browser (Ralf, 2026-10-04: "auch als pdf"); no PDF library."""
    from app.models import utcnow
    from app.timeutil import to_local

    survey = _survey(code)
    st = state(survey)
    return render_template("interview/print.html", survey=survey, rows=ordered_blocks(survey), runs=guide_runs, parts=parts(),
                           outline=outline, task_item_types=TASK_ITEM_TYPES,
                           task_kind=__import__("app.interview_tasks", fromlist=["kind_name"]).kind_name,
                           planned=planned_minutes, total=sum(planned_minutes(b) for b in guide_blocks(survey)),
                           order=orders()[st["order"]][0], today=day_text(f"{to_local(utcnow()):%Y-%m-%d}"), sources=guide_sources(survey))


def _zenodo_targets(survey):
    """[(target, label)] the admin may send a version to: only connected accounts (no block for a service not set up)."""
    from app import zenodo_auth

    if not survey.can(current_user, "admin"):
        return []
    return [(t, label) for t, label in (("zenodo", "Zenodo"), ("sandbox", de_en("Zenodo-Sandbox (Test)", "Zenodo sandbox (test)")))
            if zenodo_auth.available(t) and zenodo_auth.connection(current_user, t) is not None]


@bp.route("/<code>/export/category-system.qdc")
@login_required
def export_qdc(code):
    import io

    from app import qdc

    survey = _survey(code)
    return send_file(io.BytesIO(qdc.build(survey)), as_attachment=True, mimetype="application/xml",
                     download_name=f"kategoriensystem-{survey.code}.qdc")


@bp.route("/<code>/versions", methods=["POST"])
@login_required
def version_new(code):
    """Fassung festhalten (app/interview_versions.py): the role "Bearbeiten" may (concept 5)."""
    from app import interview_versions

    survey = _survey(code, need="editor")
    try:
        entry = interview_versions.freeze(survey, request.form.get("note", ""), request.form.get("text", ""),
                                          current_user.name or "unbekannt",
                                          licence=request.form.get("material_license", ""),
                                          confirmed=request.form.get("material_rights_confirm") == "1",
                                          third_party=request.form.get("material_third_party", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("interview.export", code=survey.code) + "#fassungen")
    db.session.commit()
    flash(de_en("Fassung {n} festgehalten.", "Revision {n} recorded.", n=entry["n"]), "success")
    from app import coscine

    done = coscine.auto_store(survey, f"fassung:{entry['name']}")  # optional, off unless ticked
    if done:
        flash(done[1], "success" if done[0] else "warning")
    return redirect(url_for("interview.export", code=survey.code) + "#fassungen")


@bp.route("/<code>/versions/<name>/zenodo", methods=["POST"])
@login_required
def version_zenodo(code, name):
    """A version as a draft on Zenodo (decision I10): the admins, through their connected account; Askurious never
    publishes, the researchers publish on Zenodo, which issues the DOI ("Stand abrufen" reads it back)."""
    from app import interview_versions, zenodo, zenodo_auth

    survey = _survey(code, need="admin")
    back = redirect(url_for("interview.export", code=survey.code) + "#fassungen")
    st = state(survey)
    version = next((v for v in st["versions"] if v.get("name") == name), None)
    found = interview_versions.path(survey, name)
    if version is None or found is None:
        abort(404)
    target = request.form.get("target") if request.form.get("target") in zenodo.URLS else "zenodo"
    if request.form.get("action") != "refresh":
        try:
            zenodo.guide_metadata(survey, version)  # no OAuth refresh or draft replacement before validation
            interview_versions.verify_material_license(version, found)
        except zenodo.ZenodoError as e:
            flash(str(e), "error")
            return back
    token = zenodo_auth.access_token(current_user, target) if zenodo_auth.available(target) else None
    if not token:
        flash(de_en("Bitte zuerst unter „Konto › Verbindungen“ „Mit Zenodo verbinden“.", "Please first choose “Connect to Zenodo” under “Account › Connections”."),
              "error")
        return back
    try:
        client = zenodo.Client(token, target)
        if request.form.get("action") == "refresh":
            got = zenodo.refresh_guide(client, version)
            flash(de_en("Auf Zenodo veröffentlicht. DOI: {doi}", "Published on Zenodo. DOI: {doi}", doi=got.get("doi"))
                  if got["state"] == "published" else de_en("Auf Zenodo noch ein Entwurf.", "Still a draft on Zenodo."), "success")
        else:
            got = zenodo.upload_guide(survey, client, version, found)
            flash(de_en("Fassung {n} als Entwurf auf Zenodo angelegt. Bitte dort prüfen und veröffentlichen; erst dann wird "
                        "die DOI vergeben.", "Revision {n} created as a draft on Zenodo. Please check and publish it there; "
                        "only then is the DOI assigned.", n=version["n"]), "success")
    except zenodo.ZenodoError as e:
        flash(str(e), "error")
        return back
    survey.interview = {**st, "versions": [{**v, "zenodo": got} if v.get("name") == name else v for v in st["versions"]]}
    db.session.commit()
    return back


@bp.route("/<code>/versions/<name>")
@login_required
def version_file(code, name):
    from app import interview_versions

    survey = _survey(code)
    found = interview_versions.path(survey, name)
    if found is None:
        abort(404)
    return send_file(found, as_attachment=True, mimetype="application/zip", download_name=f"{survey.code}-{name}")


@bp.route("/<code>/export/<kind>.docx")
@login_required
def export_docx(code, kind):
    import re
    import io

    from app import interview_docx

    survey = _survey(code)
    if kind not in documents(survey):
        abort(404)
    data = interview_docx.build(survey, kind)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", survey.code)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"{kind}-{name}.docx",
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --- the interview view ----------------------------------------------------------------------------------------


def steps(survey):
    """The pages of the interview view in their order: one per Leitfrage (with its Unterfragen), hint or stimulus of a
    Themenblock, one per Kurzfragebogen and Postskript, and a closing page. [{"part", "block", "item" or None, "n",
    "subs" for a page of a Themenblock}]"""
    out = []
    for which, block in ordered_blocks(survey):
        if which == "guide":
            subs, tasks, owned = subs_of(block), tasks_of(block), task_owner(block)
            for item, main in outline(block):
                # an Unterfrage is shown on its Leitfrage's page (E1), a task too (app/interview_tasks.py)
                if (item.type in GUIDE_TYPES or item.type in TASK_ITEM_TYPES) and main is None and item.id not in owned:
                    out.append({"part": which, "block": block, "item": item, "subs": subs.get(item.id, []),
                                "tasks": tasks.get(item.id, [])})
        elif any(i.type != "page_break" for i in block.items):
            out.append({"part": which, "block": block, "item": None})
    out.append({"part": "end", "block": None, "item": None})
    for n, step in enumerate(out):
        step["n"] = n
    return out


@bp.route("/interview-sw.js")
def interview_sw():
    """The service worker of the interview view (app/static/interview-sw.js, offline G): served from /guide/ so that
    its scope may be the page of one interview (/guide/<code>/conversation); the browser asks for it again on every
    visit (no-cache), so a new version takes over at once. Code only, nothing of an interview."""
    import os

    from flask import current_app

    res = send_file(os.path.join(current_app.static_folder, "interview-sw.js"), mimetype="text/javascript", max_age=0)
    res.headers["Cache-Control"] = "no-cache"
    return res


@bp.route("/<code>/conversation")
@interviewer_or_signed_in
def conduct(code):
    """Without ?interview: nothing is stored (the ticks live in the browser tab). With ?interview=<id> (
    "Interviews erheben", app/interview_data.py) the view saves that interview as it goes. An interviewer with a
    personal link (app/interviewer.py) opens only an interview of their own."""
    from app import interview_data, interviewer

    rid = request.args.get("interview", type=int)
    row = record = None
    survey = interviewer.guide(code)
    if current_user.is_authenticated and survey.can(current_user, "viewer"):
        survey = _survey(code)
        if rid and survey.can(current_user, "editor"):
            row = interview_data._interview(survey, rid)
    elif rid and interviewer.current(survey) is not None:
        survey, row = interviewer.row_for(code, rid)
    elif current_user.is_authenticated:
        survey = _survey(code)  # the refusal a researcher without a role got before
    else:
        abort(404)
    if row is not None:
        record = interview_data.record(survey, row)
    return conduct_page(survey, row, record)


def conduct_page(survey, row, record=None, offline=False, access=None):
    """The interview view of `row` (None: a trial without saving). offline: the field view of app/interview_offline.py,
    `row` a placeholder whose place the device picks; nothing of the server's interviews is in the page."""
    from app import interview_audio, interview_data, interview_tasks

    conference = False
    if row is not None:
        g.microphone = True  # the view records the interview (app/interview_audio.py; Permissions-Policy in create_app)
        # the sound of the video conference shared with the browser (for everyone since 2026-10-16; before, the
        # experimental "conference"): offered once the interview has consent to a recording
        conference = bool(((row.interview or {}).get("consent") or {}).get("audio"))
        if conference:
            g.display_capture = True
    from app import interview_screen

    # the interviewee's own screen (experimental, app/interview_screen.py): for a stored interview of a guide with tasks
    # (regular since 2026-10-22); also for a guide without tasks whose Kurzfragebogen can go there (Ralf, A);
    # never in the field view of the offline mode
    screen = (row is not None and not offline and (bool(interview_tasks.tasks(survey)) or bool(interview_screen.short_items(survey)))
              and interview_screen.offered(survey))
    from app import interview_offline

    return render_template("interview/conduct.html", survey=survey, st=state(survey), steps=steps(survey), conference=conference,
                           offline=offline, offline_days=interview_offline.DAYS,
                           offline_max=interview_offline.MAX_SLOTS, offline_default=interview_offline.DEFAULT_SLOTS,
                           offline_access=access, modes=modes(), consent_forms=interview_data.consent_forms(),
                           screen=screen, screen_minutes=interview_screen.OPEN_MINUTES, screen_hours=interview_screen.HOURS,
                           rows=ordered_blocks(survey), parts=parts(), planned=planned_minutes, orders=orders(), row=row,
                           record=record, form_answers=interview_data.form_answers(survey, row) if row and not offline else {},
                           next_part=interview_audio.next_part, task_kind=interview_tasks.kind_name,
                           task_item_types=TASK_ITEM_TYPES,
                           sources=[(de_en("Reihenfolge im Gespräch", "Order in the conversation"), [HOPF]),
                                    (de_en("Kurzfragebogen und Postskript", "Short questionnaire and postscript"), [WITZEL])])


# --- the other pages of a survey -------------------------------------------------------------------------------

# what an interview guide may open of the pages of a survey (everything else goes to the guide, concept 4.2)
OPEN_SURVEY_PAGES = {"add_member", "remove_member", "move_member", "set_member_role", "add_group_access",
                     "remove_group_access", "set_group_access", "archive", "delete", "duplicate",
                     "export_file", "export_zip", "set_label", "set_showcase"}
CLOSED_EDITOR_PAGES = {"index", "flow_add", "flow_node", "loop_table", "preview_survey", "preview_block", "comments_page",
                       "ai_scale", "ai_ideas", "ai_more", "add_block", "move_block", "place_block", "copy_item_to",
                       "make_linked", "join_linked", "text_review_survey"}


def guard():
    """Before every request to a page of a survey: an interview guide has no questionnaire editor, flow, quotas,
    translation, data collection, results, dashboards or data export. Such pages lead to the guide (GET), or are
    refused (POST)."""
    survey_id = (request.view_args or {}).get("survey_id")
    endpoint = request.endpoint or ""
    if survey_id is None or "." not in endpoint:
        return None
    blueprint, view = endpoint.split(".", 1)
    if blueprint in ("editor",):
        if view not in CLOSED_EDITOR_PAGES:
            return None
    elif blueprint == "surveys":
        if view in OPEN_SURVEY_PAGES:
            return None
    elif blueprint not in ("flow_page", "translations", "boards", "wizard"):
        return None
    survey = db.session.get(Survey, survey_id)
    if survey is None or survey.interview is None:
        return None
    if request.method != "GET":
        abort(404)
    target = "settings" if endpoint == "surveys.detail" else "export" if endpoint == "surveys.data" else "guide"
    return redirect(url_for(f"interview.{target}", code=survey.code))


@bp.before_app_request
def _guard():
    return guard()
