"""AI help at the fields of one question (concept "KI-Hilfe direkt an Fragen", 2026-11-27; experimental on its first
day, since then open to everyone with "Generative KI nutzen"): a button at the question text ("Formulieren") and one
at the list of answer options or of statements ("Vorschlagen") open a popup with suggestions to choose from. Only on
a click, only in a draft, only in the source language of the survey; nothing changes until the researcher takes
suggestions over (app/editor/ai_help.py: ai_field, ai_field_apply), as one step (undo).

Three parts:
- "question": three wordings of the question text for what the researcher says the question should capture; the
  chosen one replaces the question text;
- "options": further answer options for a single or multiple choice, appended to the list. An option may come as
  "with a text field" ("Anderes, und zwar") or, in a multiple choice, as "excludes the others";
- "statements": further statements of a Likert matrix, appended; each carries the mark "ai" in its row of the config
  (the badge "KI" in the editor, "[KI-generiert, nicht validiert]" in the codebook, a warning in the pre-launch
  check). Unlike config "ai" of a whole AI scale (app/ai_scales.py) the mark is per statement: a scale of one's own
  with some AI statements is no AI scale.

Sent to the text model: the type and the texts of this element in the source language (text_review.parts), what the
researcher typed into the popup, and the study context of ai_scales.context. Never: answers, a locked validated
construct, wording that may not be shared (constructs.NOT_SHARED), a frozen linked element, the consent; for those
text_review.reviewable is false, there is no button and the routes answer 404.

A keyword that names a construct of the toolbox leads there instead of to home-made items (catalog_hits): the popup
shows the construct with "Aus Katalog einfügen" before any AI call."""
import json
import re

from app import ai, ai_scales, text_review
from app.lang import de_en

PARTS = ("question", "options", "statements")
# the types whose first text is a plain question text ("Fragetext" in their editor; an instruction, a heading, a
# caption or the task of a choice experiment is none)
QUESTION_TYPES = frozenset({
    "association", "card_sort", "cloze", "constant_sum", "date_time", "drill_down", "federal_state", "geolocation",
    "highlight", "multiple_choice", "personal_code", "position2d", "postal_code", "q_sort", "ranking", "rating",
    "single_choice", "slider", "text_input", "voice"})
OPTION_TYPES = frozenset({"single_choice", "multiple_choice"})
STATEMENT_TYPES = frozenset({"likert"})

MAX_IDEA = 500  # what the researcher types into the popup
MAX_QUESTION = 1000  # a suggested question text
MAX_ROW = 300  # a suggested option or statement
MAX_TAKE = 20  # rows taken over in one step
MAX_SEEN = 40  # suggestions already shown that "Weitere" sends along
VARIANTS = 3

_NO_NAMES = ("Do not name published scales, instruments, authors, years or references, do not claim anything is "
             "validated, and do not reuse the wording of well-known instruments verbatim.")

QUESTION_INSTRUCTIONS = """You help social scientists word one question of an online questionnaire.
Write {n} different wordings of the question text for what the researcher wants to capture, fitting the type of the
element and its answer options (given below), in {language}. Form of address: {address}.
Rules of questionnaire design: one question asks one thing (no double-barrelled questions); neutral, not leading or
loaded, no presupposition; no double negation; plain words instead of jargon and abbreviations, short sentences; a
concrete time frame instead of "regularly", "recently", "often"; the wording fits the answer format.
Keep placeholders in double braces ({{{{name}}}}) of the current text exactly. No numbering, no quotation marks around
a wording, no formatting marks.
""" + _NO_NAMES + """
Answer with ONE JSON object and nothing else: {{"variants": ["...", "...", "..."]}}"""

OPTIONS_INSTRUCTIONS = """You help social scientists complete the answer options of one {kind} question of an online
questionnaire. Suggest the answer options that are still missing, in {language}. Form of address: {address}.
Rules of questionnaire design: together with the existing options the list is exhaustive and the options do not
overlap; ordered classes (age groups, frequencies, amounts) come in their order, without gaps and without overlap at
the borders; short, parallel wording; plain words. Add a residual option ("Other, namely" in {language}) with
"with_text": true only where the list cannot be complete, and at most once.{exclusive}
Do not repeat or paraphrase an existing option{seen}. Between 2 and 8 options; fewer when the list is almost complete.
If the existing options are an ordered list, continue it in the same form. No numbering, no formatting marks.
Answer with ONE JSON object and nothing else:
{{"options": [{{"text": "...", "with_text": false, "exclusive": false}}, ...]}}"""

STATEMENTS_INSTRUCTIONS = """You help social scientists draft further statements (Likert items, rated on the answer
scale given below) for one matrix question of an online questionnaire. Write {n} statements in {language}. Form of
address: {address}.
The existing statements and what the researcher typed show what the scale measures: the new statements measure the
same thing and add an aspect each. Rules: one idea per statement (no double-barrelled items), no double negation, no
absolute words like "always"/"never", short and concrete, first person where natural, fitting the answer scale.
{reverse}
""" + _NO_NAMES + """
Do not repeat or paraphrase an existing statement{seen}. No numbering, no formatting marks.
Answer with ONE JSON object and nothing else: {{"statements": [{{"text": "...", "reverse": false}}, ...]}}"""

QUESTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["variants"],
    "properties": {"variants": {"type": "array", "items": {"type": "string"}}}}
OPTIONS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["options"],
    "properties": {"options": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "with_text", "exclusive"],
        "properties": {"text": {"type": "string"}, "with_text": {"type": "boolean"},
                       "exclusive": {"type": "boolean"}}}}}}
STATEMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["statements"],
    "properties": {"statements": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "reverse"],
        "properties": {"text": {"type": "string"}, "reverse": {"type": "boolean"}}}}}}


# --- where the help is offered --------------------------------------------------------------------------------------

def structural(item):
    """May rows be added: the structure is not locked (a validated construct, a frozen linked element, a choice design
    with data)."""
    from app import constructs

    return not constructs.locked(item) and not item.kind.design_locked(item)


def allowed(item, part):
    """Is `part` of `item` open to the help? Never for what the text review may not read (text_review.reviewable):
    the consent, a locked validated construct, wording that may not be shared, a frozen linked element. Not in an
    interview guide: its rules are not those of a questionnaire."""
    if part not in PARTS or not text_review.reviewable(item) or item.survey.interview is not None:
        return False
    if part == "question":
        return item.type in QUESTION_TYPES
    if not structural(item):
        return False
    if part == "options":  # options shown as images need an image each: left to the page of the images
        return item.type in OPTION_TYPES and not item.kind.images(item)
    return item.type in STATEMENT_TYPES


def parts_for(item):
    """The parts of `item` the help is offered for, in the order of PARTS (the element page shows a button each)."""
    return [p for p in PARTS if allowed(item, p)]


def parts_shown(survey, item, lang, user=None):
    """The parts of `item` whose button the element page shows to `user` (default: the person signed in): "Generative
    KI nutzen" is on for them, they may edit, the survey is a draft and the page shows its source language (not the
    translation view)."""
    from flask_login import current_user

    from app import ai_access

    user = current_user if user is None else user
    if not (ai_access.ai_on(user) and survey.is_editable and survey.can(user, "editor")
            and lang == survey.default_language):
        return []
    return parts_for(item)


# --- what goes to the model ------------------------------------------------------------------------------------------

def clean(text, limit):
    """One line of text from the model or from the form: no control characters, spaces collapsed, cut to `limit`."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:limit].strip()


def _fill(template, survey, **values):
    from app.surveys import LANGUAGE_NAMES

    lang = survey.default_language
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as usual in the language")
    return template.format(language=ai_scales.LANGUAGE.get(lang) or LANGUAGE_NAMES.get(lang, lang), address=address,
                           **values)


def facts(item):
    """The element as the model reads it: its type and its texts in the source language (text_review.parts)."""
    return {"type": item.kind.label, "variable": item.code,
            "parts": [{"role": role, "text": text} for _pid, role, text in text_review.parts(item)]}


def _prompt(item, **more):
    data = {"element": facts(item), **{k: v for k, v in more.items() if v}}
    return ai_scales.context(item.survey) + "\n\nThe element to work on:\n" + json.dumps(data, ensure_ascii=False, indent=1)


def _seen(seen):
    seen = [clean(s, MAX_ROW) for s in (seen or [])][:MAX_SEEN]
    seen = [s for s in seen if s]
    return (", and none of these suggestions made before: " + " | ".join(seen)) if seen else ""


def _existing(item, head):
    """The texts of the rows of `head` ("options", "statements") in the source language, casefolded, to drop repeats."""
    texts = (item.lang_texts(item.survey.default_language).get(head) or {}).values()
    return {" ".join(str(t).split()).casefold() for t in texts if str(t).strip()}


def _ask(item, instructions, prompt, purpose, schema):
    return ai.json_answer(ai.generate_text(instructions, prompt, purpose=purpose, item_type=item.type, schema=schema))


def suggest_question(item, idea, seen=()):
    """[three wordings of the question text] for what the researcher wants to capture (`idea`)."""
    idea = clean(idea, MAX_IDEA)
    if not idea:
        raise ai.AIError(de_en("Bitte kurz beschreiben, was die Frage erfassen soll.",
                               "Please describe briefly what the question should capture."))
    instructions = _fill(QUESTION_INSTRUCTIONS, item.survey, n=VARIANTS)
    seen = [clean(s, MAX_QUESTION) for s in (seen or [])][:MAX_SEEN]
    data = _ask(item, instructions, _prompt(item, what_the_question_should_capture=idea,
                                            wordings_made_before_do_not_repeat=[s for s in seen if s]),
                "field_question", QUESTION_SCHEMA)
    out = []
    for text in ai.as_list(data.get("variants")):
        text = clean(text, MAX_QUESTION) if isinstance(text, str) else ""
        if text and text not in out:
            out.append(text)
    if not out:
        raise ai.AIError(de_en("Die KI hat keine Formulierung geliefert.", "The AI returned no wording."))
    return out[:VARIANTS]


def suggest_options(item, seen=()):
    """[{"text", "with_text", "exclusive"}]: answer options still missing; the marks only where the type knows them."""
    if not any(role == "Fragetext" for _pid, role, _text in text_review.parts(item)):
        raise ai.AIError(de_en("Bitte zuerst den Fragetext eintragen: Die Vorschläge richten sich nach ihm.",
                               "Please enter the question text first: the suggestions follow it."))
    kind = item.kind
    exclusive = (' Mark an option that rules out all others ("none of these") with "exclusive": true.'
                 if kind.supports_exclusive else ' "exclusive" is always false.')
    instructions = _fill(OPTIONS_INSTRUCTIONS, item.survey, exclusive=exclusive, seen=_seen(seen),
                         kind="multiple choice (several answers)" if kind.supports_exclusive else "single choice (one answer)")
    data = _ask(item, instructions, _prompt(item), "field_options", OPTIONS_SCHEMA)
    there, out = _existing(item, "options"), []
    for d in ai.as_list(data.get("options")):
        text = clean(d.get("text"), MAX_ROW) if isinstance(d, dict) else ""
        if not text or text.casefold() in there:
            continue
        there.add(text.casefold())
        out.append({"text": text, "with_text": bool(d.get("with_text")) and kind.supports_text,
                    "exclusive": bool(d.get("exclusive")) and kind.supports_exclusive})
    if not out:
        raise ai.AIError(de_en("Die KI hat keine weiteren Antwortoptionen geliefert.",
                               "The AI returned no further answer options."))
    return out[:MAX_TAKE]


def suggest_statements(item, idea="", n=3, reverse=False, seen=()):
    """[{"text", "reverse"}]: `n` further statements for the matrix; `idea` (optional while there are statements) says
    what they should measure."""
    idea = clean(idea, MAX_IDEA)
    there = _existing(item, "statements")
    if not idea and not there:
        raise ai.AIError(de_en("Bitte kurz beschreiben, was die Aussagen messen sollen.",
                               "Please describe briefly what the statements should measure."))
    n = min(max(int(n), 1), 15)
    instructions = _fill(
        STATEMENTS_INSTRUCTIONS, item.survey, n=n, seen=_seen(seen),
        reverse=(f"About half of the statements ({n // 2}) reverse-keyed: worded in the opposite direction "
                 "(\"reverse\": true); the others \"reverse\": false." if reverse and n > 1
                 else "All statements keyed in the direction of what is measured (\"reverse\": false)."))
    data = _ask(item, instructions, _prompt(item, what_the_statements_should_measure=idea), "field_statements",
                STATEMENTS_SCHEMA)
    out = []
    for d in ai.as_list(data.get("statements")):
        text = clean(d.get("text"), MAX_ROW) if isinstance(d, dict) else ""
        if not text or text.casefold() in there:
            continue
        there.add(text.casefold())
        out.append({"text": text, "reverse": bool(d.get("reverse")) and reverse})
    if not out:
        raise ai.AIError(de_en("Die KI hat keine weiteren Aussagen geliefert.", "The AI returned no further statements."))
    return out[:n]


# --- a keyword that names a construct of the toolbox -------------------------------------------------------------------

_STOP = frozenset("der die das den dem des ein eine einer eines einem einen und oder für von zu zur zum im in am an auf "
                  "mit bei aus the a an of for and or to in on with skala scale kurzskala fragebogen questionnaire "
                  "inventar inventory index test messen erfassen measure".split())


def _words(text):
    return [w for w in re.findall(r"[0-9a-zäöüß]+", str(text or "").casefold()) if w not in _STOP]


def catalog_hits(survey, idea, user=None, limit=3):
    """The constructs of the toolbox the keyword names: every word of the construct's name (without articles and
    words like "Skala") stands in the keyword, or the keyword is the construct's key or short name. Only constructs
    the person may see (constructs.visible) and may use in this survey (constructs.permitted); no linked elements.
    [{"key", "name", "by", "items"}], the most specific name first."""
    from app import constructs

    typed = _words(idea)
    if not typed:
        return []
    have, joined = set(typed), " ".join(typed)
    found = []
    for c in constructs.search():
        if constructs.is_linked_key(c.key) or not constructs.permitted(c.key, survey, user):
            continue
        names = {c.name, constructs.shown_text(c, "name")}
        best = 0
        for name in names:
            words = _words(name)
            if not words:
                continue
            if len(words) == 1 and len(words[0]) < 4 and joined != words[0]:
                continue  # a short abbreviation ("TA") only as the whole keyword
            if all(w in have for w in words):
                best = max(best, len(words))
        if not best and joined == " ".join(_words(c.key.replace("_", " "))):
            best = 1
        if best:
            spec = c.spec or {}
            found.append((best, {"key": c.key, "name": constructs.shown_text(c, "name"),
                                 "by": f"{c.authors} ({c.year})" if c.authors and c.year else (c.authors or ""),
                                 "type": spec.get("type") or "",
                                 "items": sum(1 for s in (spec.get("config") or {}).get("statements", [])
                                              if not s.get("check"))}))
    found.sort(key=lambda row: (-row[0], row[1]["name"].casefold()))
    return [row for _n, row in found[:limit]]


# --- taking suggestions over -------------------------------------------------------------------------------------------

def set_question(item, text):
    """The chosen wording as the question text, in the source language."""
    text_review.apply(item, {"question": clean(text, MAX_QUESTION)})


def append_options(item, rows):
    """Append [{"text", "with_text", "exclusive"}] to the answer options; returns how many were added. Codes: the
    numbers after the highest so far. Texts in the source language only: the other languages show them as missing."""
    import copy

    from app.items.base import new_id

    kind, lang = item.kind, item.survey.default_language
    config, texts = copy.deepcopy(item.config), copy.deepcopy(item.texts or {})
    options = config.setdefault("options", [])
    used = {str(o.get("code")) for o in options}
    number = max([int(c) for c in used if c.isascii() and c.isdigit() and len(c) < 9] + [len(options)])
    there, added = _existing(item, "options"), 0
    limit = getattr(kind, "max_options", None) or 3000
    for row in rows[:MAX_TAKE]:
        text = clean(row.get("text"), MAX_ROW)
        if not text or text.casefold() in there or len(options) >= limit:
            continue
        there.add(text.casefold())
        number += 1
        while str(number) in used:
            number += 1
        used.add(str(number))
        oid = new_id()
        options.append({"id": oid, "code": str(number),
                        **({"text": True} if row.get("with_text") and kind.supports_text else {}),
                        **({"exclusive": True} if row.get("exclusive") and kind.supports_exclusive else {})})
        texts.setdefault(lang, {}).setdefault("options", {})[oid] = text
        added += 1
    item.config, item.texts = config, texts
    return added


def append_statements(item, rows):
    """Append [{"text", "reverse"}] to the statements, each marked "ai"; returns how many were added."""
    import copy

    from app.items.base import new_id

    lang = item.survey.default_language
    config, texts = copy.deepcopy(item.config), copy.deepcopy(item.texts or {})
    statements = config.setdefault("statements", [])
    there, added = _existing(item, "statements"), 0
    for row in rows[:MAX_TAKE]:
        text = clean(row.get("text"), MAX_ROW)
        if not text or text.casefold() in there or len(statements) >= 300:
            continue
        there.add(text.casefold())
        sid = new_id()
        statements.append({"id": sid, "code": "", "reverse": bool(row.get("reverse")), "ai": True})
        texts.setdefault(lang, {}).setdefault("statements", {})[sid] = text
        added += 1
    item.config, item.texts = config, texts
    return added


def ai_statements(item):
    """How many statements of `item` carry the mark "ai" (appended from a suggestion)."""
    return sum(1 for s in (item.config or {}).get("statements") or [] if isinstance(s, dict) and s.get("ai"))
