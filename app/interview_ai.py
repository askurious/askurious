"""AI assistant of an interview guide (concept interviewleitfaden-konzept.md 7, decisions I14 to I16).

It works on the guide, never on interviews (there are none in Askurious): the text model sees the title, the research
question, target group and mode (picked from lists), the texts of the guide and the questions the researchers type
into the assistant. As with the other assistants (app/live_ai.py, app/text_review.py) it suggests, with a short reason,
and nothing changes until the researchers tick, edit if they like, and take over; taking over is one step of the
history (Rückgängig). The daily limits of the AI calls apply (ai.generate_text).

  A1 entwickeln  a new guide along the SPSS principle of Helfferich (2011): Sammeln (own questions first, the assistant
                 adds what is missing), Prüfen (findings and a suggestion per question), Sortieren (Themenblöcke),
                 Subsumieren (one narrative prompt per Themenblock, the other questions as aspects or concrete
                 questions). Not a guide at the push of a button: collecting and checking are where researchers notice
                 their own assumptions (Helfferich, 2011), so they decide at every step; each step can be skipped.
  A2 ergänzen    aspects, maintaining and concrete questions for one Leitfrage
  A3 prüfen      the whole guide against the rules below, also order and length
  A4 vorschlagen Kurzfragebogen or Postskript (Witzel, 2000) in simple element types
  A5 Kategorien  a definition and a delimitation per Leitfrage and aspect for the category system (.qdc): the deductive
                 main categories of a content-structuring qualitative content analysis (Kuckartz & Rädiker, 2022).
                 No anchor examples: those come from the material.
Not offered: the consent (legal text), a trial interview with an AI person (decision I16).

Languages (concept "Mehrsprachige Oberfläche", E12): what the AI writes into the guide (questions, aspects, block
titles, category definitions) is in the survey's language; its reasons and remarks, which only the researchers read,
in the language of their page ({reasons} in the instructions, app.lang.current())."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import ai
from app.extensions import db
from app.ids import db_id
from app.items.base import new_id, plain
from app.lang import de_en, english
from app.request_values import safe_loads

bp = Blueprint("interview_ai", __name__, url_prefix="/guide")

MAX_QUESTIONS = 60
MAX_TEXT = 500
MAX_ROWS = 12
ISSUES = {"closed": "geschlossen", "leading": "suggestiv", "double": "zwei Fragen in einer", "jargon": "Fachsprache",
          "why": "Warum-Frage", "knowledge": "setzt Wissen voraus", "evaluative": "wertend"}
ISSUES_EN = {"closed": "closed", "leading": "leading", "double": "two questions in one", "jargon": "jargon",
             "why": "why question", "knowledge": "assumes knowledge", "evaluative": "evaluative"}


def issues():
    """ISSUES in the language of the page."""
    return ISSUES_EN if english() else ISSUES


KRUSE = "Kruse, J. (2015). Qualitative Interviewforschung: Ein integrativer Ansatz (2. Aufl.). Beltz Juventa."

RULES = """Rules for the questions of a guide for semi-structured qualitative interviews (Helfferich 2011; Kruse 2015;
Gläser & Laudel 2010 for expert interviews):
- a narrative prompt is open and invites the interviewee to tell ("Erzählen Sie doch einmal, wie …"); it cannot be
  answered with yes or no or a single word;
- one question asks one thing;
- not leading, no presupposition, no evaluation in the wording; no "why" questions at the start of a topic (they ask
  for a justification instead of a narration);
- everyday words of the target group, no jargon, no knowledge the interviewee may not have;
- maintaining questions have no content of their own ("Und dann?", "Wie ging es weiter?");
- concrete questions are for aspects that did not come up by themselves, with a fixed, still open wording;
- content aspects are short keywords, not questions;
- the order begins with an easy, narrative entry; sensitive topics come later; the guide ends with an open question;
- in expert interviews questions may be more direct and about facts and processes."""


def _context(survey):
    from app import interview
    from app.surveys import LANGUAGE_NAMES

    st = interview.state(survey)
    lang = survey.default_language
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as usual in the language")
    template = interview.TEMPLATES.get(st["template"], ("",))[0]
    facts = [f"Title: {survey.title}"]
    if survey.research_question:
        facts.append(f"Research question: {survey.research_question[:2000]}")
    if st["target"]:
        facts.append(f"Interviewees: {interview.TARGETS[st['target']]}")
    facts.append(f"Interview form: {template or 'semi-structured interview'}, {interview.MODES[st['mode']]}")
    return LANGUAGE_NAMES.get(lang, lang), address, "\n".join(facts)


def _call(survey, instructions, prompt, purpose):
    language, address, facts = _context(survey)
    reasons = "English" if english() else "German"  # what only the researcher reads: in the language of the page
    text = ai.generate_text(instructions.format(language=language, address=address, rules=RULES, reasons=reasons),
                            facts + "\n\n" + prompt, purpose=purpose, item_type="guide_question")
    return ai.json_answer(text)


def _t(value, limit=MAX_TEXT):
    return " ".join(str(value or "").split())[:limit]


def _lines(values, limit=MAX_ROWS):
    out = []
    for v in values if isinstance(values, list) else []:
        v = _t(v, 300)
        if v and v not in out:
            out.append(v)
    return out[:limit]


def guide_lines(survey):
    """The guide as numbered lines for a prompt: per Leitfrage its code, prompt, aspects, maintaining and concrete
    questions, hint (each part with its id, so that a suggestion can name it)."""
    from app import interview

    out = []
    for block in interview.guide_blocks(survey):
        out.append(f"## Themenblock: {block.title()}")
        for item, main in interview.outline(block):
            if item.type != "guide_question":
                continue
            if main is not None:  # an Unterfrage: a follow-up to its Leitfrage (concept unterfragen, E5)
                when = plain(item.text("when"))
                out.append(f"[{item.code}] sub-question of [{main.code}]" + (f", ask {when}" if when else ""))
            out.append(f"[{item.code}] question: {plain(item.text('question'))}")
            case = item.kind.case_text(item, ui="en")
            if case:
                out.append(f"[{item.code}] ask only {case}")
            for part in ("aspects", "keep"):
                for rid, text in item.kind.rows(item, part):
                    out.append(f"[{item.code}] {part}:{rid}: {text}")
            # concrete questions with their Nachfragen and conditions (concept bedingungen-leitfaden)
            for p in item.kind.probe_tree(item, ui="en"):
                out.append(f"[{item.code}] probe:{p['id']}: {p['text']}" + (f" (ask only {p['cond_text']})" if p["cond_text"] else ""))
                for s in p["subs"]:
                    notes = ", ".join(n for n in (s["when"], s["cond_text"] and "ask only " + s["cond_text"]) if n)
                    out.append(f"[{item.code}] probe:{s['id']}: follow-up to probe:{p['id']}: {s['text']}" + (f" ({notes})" if notes else ""))
            if item.text("hint"):
                out.append(f"[{item.code}] hint: {item.text('hint')}")
    return "\n".join(out)


# --- A1: developing a guide along SPSS --------------------------------------------------------------------------

COLLECT = """You help researchers to collect questions for a guide for qualitative interviews (step "Sammeln" of the
SPSS principle, Helfferich 2011). Their own questions come first; you add what is missing for the research question:
other aspects, other phases of the topic, contrasting experiences. If they have none yet, write a first collection
of 10 to 15 questions. Collecting is not checking: questions may still be rough.
Write in {language} ({address}). Answer with ONE JSON object and nothing else:
{{"questions": [{{"question": "...", "why": "short reason in {reasons}"}}, ...]}}  (at most 10 when adding)"""

CHECK = """You check questions collected for a guide for qualitative interviews (step "Prüfen" of the SPSS principle).
{rules}
For every question (by its number) give the problems from this list that apply: closed, leading, double, jargon, why,
knowledge, evaluative (empty list if none), and, if there are problems, an improved open wording.
Write the suggestions in {language} ({address}), the reasons in {reasons}. Answer with ONE JSON object and nothing else:
{{"checks": [{{"n": 1, "issues": ["closed", ...], "suggestion": "...", "why": "..."}}, ...]}}"""

SORT = """You sort the questions of a guide for qualitative interviews into Themenblöcke (step "Sortieren" of the SPSS
principle): questions on one topic together, the blocks in an order that begins with an easy, narrative entry and
puts sensitive topics later. Every question goes into exactly one block.
Block titles short, in {language}. Answer with ONE JSON object and nothing else:
{{"blocks": [{{"title": "...", "questions": [1, 4, 5]}}, ...]}}"""

SUBSUME = """You turn each Themenblock of a guide for qualitative interviews into one Leitfrage (step "Subsumieren" of the
SPSS principle, Helfferich 2011): one open, narrative prompt that invites the interviewee to tell about the whole
topic; the block's questions become content aspects (short keywords, to tick when they come up by themselves) or
concrete questions (for the end of the block, if an aspect did not come up); add two or three maintaining questions.
{rules}
Write in {language} ({address}). Answer with ONE JSON object and nothing else:
{{"blocks": [{{"n": 1, "prompt": "...", "aspects": ["..."], "keep": ["..."], "probe": ["..."], "minutes": 10}}, ...]}}"""


def collect(survey, own):
    data = _call(survey, COLLECT, "Own questions of the researchers:\n" + ("\n".join(f"- {q}" for q in own) or "(none yet)"),
                 "interview_collect")
    out = []
    for raw in ai.as_list(data.get("questions")):
        q = _t(raw.get("question") if isinstance(raw, dict) else raw)
        if q and q not in own:
            out.append({"text": q, "why": _t(raw.get("why"), 300) if isinstance(raw, dict) else "", "src": "ai"})
    if not out:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.", "The AI's answer could not be read. Please try again."))
    return out[:15]


def check(survey, questions):
    data = _call(survey, CHECK, "Questions:\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(questions, start=1)),
                 "interview_check")
    out = [{"issues": [], "suggestion": "", "why": ""} for _ in questions]
    for raw in ai.as_list(data.get("checks")):
        if not isinstance(raw, dict) or not isinstance(raw.get("n"), int) or not 1 <= raw["n"] <= len(questions):
            continue
        issues = [i for i in ai.as_list(raw.get("issues")) if isinstance(i, str) and i in ISSUES]
        suggestion = _t(raw.get("suggestion"))
        out[raw["n"] - 1] = {"issues": issues, "suggestion": suggestion if issues and suggestion != questions[raw["n"] - 1] else "",
                             "why": _t(raw.get("why"), 300)}
    return out


def sort(survey, questions):
    data = _call(survey, SORT, "Questions:\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(questions, start=1)), "interview_sort")
    groups, seen = [], set()
    for raw in ai.as_list(data.get("blocks")):
        if not isinstance(raw, dict):
            continue
        members = [n - 1 for n in ai.as_list(raw.get("questions")) if isinstance(n, int) and 1 <= n <= len(questions) and n - 1 not in seen]
        seen.update(members)
        if members:
            groups.append({"title": _t(raw.get("title"), 200) or f"Themenblock {len(groups) + 1}", "questions": members})
    rest = [n for n in range(len(questions)) if n not in seen]
    if rest:  # what the model left out stays: in a block of its own
        groups.append({"title": f"Themenblock {len(groups) + 1}", "questions": rest})
    if not groups:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.", "The AI's answer could not be read. Please try again."))
    return groups


def subsume(survey, groups, questions):
    prompt = "\n\n".join(f"Block {n}: {g['title']}\n" + "\n".join(f"- {questions[q]}" for q in g["questions"])
                         for n, g in enumerate(groups, start=1))
    data = _call(survey, SUBSUME, prompt, "interview_subsume")
    from app.items.guide_question import MINUTES

    out = [plain_block(g, questions) for g in groups]
    for raw in ai.as_list(data.get("blocks")):
        if not isinstance(raw, dict) or not isinstance(raw.get("n"), int) or not 1 <= raw["n"] <= len(groups):
            continue
        minutes = raw.get("minutes") if raw.get("minutes") in MINUTES else 10
        out[raw["n"] - 1].update(prompt=_t(raw.get("prompt"), 1000) or out[raw["n"] - 1]["prompt"],
                                 aspects=_lines(raw.get("aspects")), keep=_lines(raw.get("keep"), 4),
                                 probe=_lines(raw.get("probe")), minutes=minutes)
    return out


def plain_block(group, questions):
    """A Themenblock without the AI: its first question is the prompt, the others concrete questions."""
    qs = [questions[q] for q in group["questions"]]
    return {"title": group["title"], "prompt": qs[0] if qs else "", "aspects": [], "keep": [], "probe": qs[1:MAX_ROWS + 1],
            "minutes": 10}


def apply_blocks(survey, blocks):
    """New Themenblöcke at the end of the guide, one Leitfrage each (the caller makes the step and commits)."""
    from app import interview

    guides = interview.guide_blocks(survey)
    position = max((b.position for b in survey.blocks), default=-1) + 1
    made = []
    for spec in blocks:
        block = interview._add_block(survey, spec["title"] or f"Themenblock {len(guides) + len(made) + 1}", "guide", position)
        position += 1
        interview._fill_block(survey, block, [("guide_question", spec["prompt"], spec["aspects"], spec["keep"], spec["probe"],
                                                "", False, spec["minutes"])])
        made.append(block)
    interview.reflow(survey, guides + made)
    return made


# --- A2 to A5 ----------------------------------------------------------------------------------------------------

EXTEND = """You complete one Leitfrage of a guide for qualitative interviews (scheme of Helfferich 2011): suggest content
aspects (short keywords for what should come up), maintaining questions (without content) and concrete questions (for
aspects that did not come up), that the Leitfrage does not have yet; at most five of each, fewer if enough are there.
If the prompt says so, also suggest at most three sub-questions: follow-ups that only fit some interviewees or
situations, each with a short condition when to ask it ("when", e.g. "if applicable", "only for managers").
{rules}
Write in {language} ({address}), reasons in {reasons}. Answer with ONE JSON object and nothing else:
{{"aspects": [{{"text": "...", "why": "..."}}], "keep": [...], "probe": [...],
  "subs": [{{"text": "...", "when": "...", "why": "..."}}]}}"""

REVIEW = """You review a whole guide for qualitative interviews, Leitfrage by Leitfrage and as a whole.
{rules}
Each line of the guide starts with [code] and the id of its part. Suggest a change only where it clearly helps, keep
the language ({language}) and the form of address ({address}). As a whole, also check the order of the Themenblöcke,
whether the guide is too long for the planned duration, and whether core topics of the research question are missing.
Answer with ONE JSON object and nothing else, remarks and reasons in {reasons}:
{{"remarks": ["..."], "parts": [{{"item": "<code>", "id": "question" | "hint" | "aspects:<id>" | "keep:<id>" | "probe:<id>",
  "suggestion": "new text", "why": "..."}}]}}"""

FORMS = {
    "short": """You suggest the questions of the Kurzfragebogen of a problem-centred interview (Witzel 2000): the social data
that matter for the research question and the sampling, asked in writing before or after the interview so that the
interview does not start as a question-and-answer game. Five to eight questions, short and neutral.""",
    "post": """You suggest the points of the Postskript of a problem-centred interview (Witzel 2000), written by the
interviewer right after the interview: setting and atmosphere, what was said before and after the recording,
non-verbal observations, ideas for the analysis, anything that fits the research question. Four to seven points.""",
}
FORM_ANSWER = """
Types: "single_choice" or "multiple_choice" (2 to 8 short options), "text_input" (a free text), "date_time" (a date),
"rating" (a scale of 5 points with two pole labels "left" and "right").
Write in {language} ({address}), reasons in {reasons}. Answer with ONE JSON object and nothing else:
{{"questions": [{{"type": "...", "question": "...", "options": ["..."], "left": "", "right": "", "why": "..."}}]}}"""
FORM_TYPES = ("single_choice", "multiple_choice", "text_input", "date_time", "rating")

CATEGORIES = """You prepare the category system of a content-structuring qualitative content analysis (Kuckartz & Rädiker
2022) from a guide for qualitative interviews: the Themenblöcke and Leitfragen become deductive main categories, the
content aspects subcategories. For every Leitfrage write a definition of the category (what is coded with it) and a
delimitation from neighbouring categories (what is not), and a one-sentence definition for each aspect. No anchor
examples: they come from the material. Write in {language}.
Answer with ONE JSON object and nothing else:
{{"categories": [{{"item": "<code>", "definition": "...", "delimit": "...", "aspects": {{"<aspect id>": "..."}}}}]}}"""


def extend(survey, item):
    """Suggestions for one Leitfrage: rows of its three lists and, for a Leitfrage that is not itself an Unterfrage,
    new Unterfragen ("subs", one level only)."""
    from app import interview

    have = "\n".join(f"{part}: {text}" for part in ("aspects", "keep", "probe") for _, text in item.kind.rows(item, part))
    main = dict(interview.outline(item.block)).get(item)
    subs = [] if main is not None else interview.subs_of(item.block).get(item.id, [])
    have += "".join(f"\nsub-question: {plain(s.text('question'))}" for s in subs)
    ask = ("Do not suggest sub-questions: this is itself a sub-question." if main is not None
           else "Suggest sub-questions too.")
    data = _call(survey, EXTEND, f"Leitfrage: {plain(item.text('question'))}\nIt has:\n{have or '(nothing yet)'}\n{ask}",
                 "interview_extend")
    out = {}
    for part in ("aspects", "keep", "probe"):
        existing = {t for _, t in item.kind.rows(item, part)}
        rows = []
        for raw in ai.as_list(data.get(part)):
            text = _t(raw.get("text") if isinstance(raw, dict) else raw, 300)
            if text and text not in existing and text not in [r["text"] for r in rows]:
                rows.append({"text": text, "why": _t(raw.get("why"), 300) if isinstance(raw, dict) else ""})
        out[part] = rows[:5]
    out["subs"] = []
    if main is None:
        existing = {plain(s.text("question")) for s in subs}
        for raw in ai.as_list(data.get("subs")):
            text = _t(raw.get("text") if isinstance(raw, dict) else raw, 300)
            if text and text not in existing and text not in [r["text"] for r in out["subs"]]:
                out["subs"].append({"text": text, "when": _t(raw.get("when"), 100) if isinstance(raw, dict) else "",
                                    "why": _t(raw.get("why"), 300) if isinstance(raw, dict) else ""})
        out["subs"] = out["subs"][:3]
    if not any(out.values()):
        raise ai.AIError(de_en("Die KI hatte nichts zu ergänzen.", "The AI had nothing to add."))
    return out


def review(survey):
    from app import interview

    data = _call(survey, REVIEW, f"Planned duration: about {sum(interview.planned_minutes(b) for b in interview.guide_blocks(survey))} "
                                 f"minutes\n\n{guide_lines(survey)}", "interview_review")
    items = {i.code: i for b in interview.guide_blocks(survey) for i in b.items if i.type == "guide_question"}
    parts = []
    for raw in ai.as_list(data.get("parts")):
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str) or raw["item"] not in items:
            continue
        item, pid, suggestion = items[raw["item"]], str(raw.get("id") or ""), _t(raw.get("suggestion"), 1000)
        current = part_text(item, pid)
        if current is None or not suggestion or suggestion == current:
            continue
        parts.append({"item": item.id, "code": item.code, "id": pid, "current": current, "suggestion": suggestion,
                      "why": _t(raw.get("why"), 300)})
    return {"remarks": [_t(r, 600) for r in ai.as_list(data.get("remarks")) if _t(r, 600)][:8], "parts": parts[:40]}


def part_text(item, pid):
    """The current text of a part of a Leitfrage ("question", "hint", "aspects:<id>", ...), None if there is no such part."""
    if pid in ("question", "hint"):
        return plain(item.text(pid)) if pid == "question" else item.text("hint")
    part, _, rid = pid.partition(":")
    if part in ("aspects", "keep", "probe"):
        return dict(item.kind.rows(item, part)).get(rid)
    return None


def set_part(item, pid, text):
    lang = item.block.survey.default_language
    texts = dict(item.texts or {})
    mine = dict(texts.get(lang) or {})
    part, _, rid = pid.partition(":")
    if pid in ("question", "hint"):
        mine[pid] = text
    elif part in ("aspects", "keep", "probe") and rid in (item.config or {}).get(part, []):
        mine[part] = {**(mine.get(part) or {}), rid: text}
    texts[lang] = mine
    item.texts = texts


def add_rows(item, part, rows):
    lang = item.block.survey.default_language
    config, texts = dict(item.config or {}), dict(item.texts or {})
    mine = dict(texts.get(lang) or {})
    labels = dict(mine.get(part) or {})
    order = list(config.get(part) or [])
    for text in rows:
        rid = new_id()
        order.append(rid)
        labels[rid] = text
    config[part] = order[:30]
    mine[part] = {rid: labels[rid] for rid in config[part] if rid in labels}
    texts[lang] = mine
    item.config, item.texts = config, texts


def forms(survey, which):
    data = _call(survey, FORMS[which] + FORM_ANSWER, "Suggest the questions now.", f"interview_{which}")
    out = []
    for raw in ai.as_list(data.get("questions")):
        if not isinstance(raw, dict) or raw.get("type") not in FORM_TYPES or not _t(raw.get("question")):
            continue
        q = {"type": raw["type"], "question": _t(raw["question"]), "why": _t(raw.get("why"), 300),
             "options": _lines(raw.get("options"), 8), "left": _t(raw.get("left"), 80), "right": _t(raw.get("right"), 80)}
        if q["type"] in ("single_choice", "multiple_choice") and len(q["options"]) < 2:
            continue
        out.append(q)
    if not out:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.", "The AI's answer could not be read. Please try again."))
    return out[:10]


def add_form_items(survey, block, questions):
    from app import interview

    rows = list(block.items)
    for q in questions:
        item = interview._form_item(survey, q["type"], q["question"], q["options"] if q["type"] in ("single_choice", "multiple_choice") else None)
        if q["type"] == "rating":
            item.config = {**item.config, "style": "numbers", "points": 5}
            item.texts = {survey.default_language: {**item.texts[survey.default_language], "left": q["left"], "right": q["right"]}}
        rows.append(item)
        block.items = rows
        db.session.flush()
    for n, item in enumerate(rows):
        item.position = n


def categories(survey):
    from app import interview

    data = _call(survey, CATEGORIES, guide_lines(survey), "interview_categories")
    items = {i.code: i for b in interview.guide_blocks(survey) for i in b.items if i.type == "guide_question"}
    out = []
    for raw in ai.as_list(data.get("categories")):
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str) or raw["item"] not in items:
            continue
        item = items[raw["item"]]
        rids = {rid for rid, _ in item.kind.rows(item, "aspects")}
        aspects = raw.get("aspects") if isinstance(raw.get("aspects"), dict) else {}
        out.append({"item": item.id, "code": item.code, "label": plain(item.text("question"))[:120],
                    "definition": _t(raw.get("definition"), 1000), "delimit": _t(raw.get("delimit"), 1000),
                    "aspects": {rid: _t(text, 500) for rid, text in aspects.items() if rid in rids and _t(text, 500)},
                    "names": dict(item.kind.rows(item, "aspects"))})
    if not out:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.", "The AI's answer could not be read. Please try again."))
    return out


# --- the pages ---------------------------------------------------------------------------------------------------


def _survey(code):
    from app import interview

    survey = interview._survey(code, need="editor")
    if not survey.is_editable:
        abort(400)
    return survey


def _sources():
    from app import qdc
    from app.interview import WITZEL
    from app.items.guide_question import GLAESER_LAUDEL, HELFFERICH

    return [(de_en("Regeln für Leitfragen", "Rules for guide questions"), [HELFFERICH, KRUSE, GLAESER_LAUDEL]),
            (de_en("Kurzfragebogen und Postskript", "Short questionnaire and postscript"), [WITZEL]),
            (de_en("Kategoriensystem", "Category system"), [qdc.KUCKARTZ])]


def _state(form):
    """The assistant's state carried by the page (field "data"), checked: questions, checks, groups, blocks."""
    try:  # safe_loads: a value nested too deeply is a ValueError too (audit 2026-10-11, N9)
        raw = safe_loads(form.get("data") or "{}")
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    questions = [{"text": _t(q.get("text")), "src": q.get("src") if q.get("src") in ("own", "ai") else "own", "why": _t(q.get("why"), 300)}
                 for q in (raw.get("questions") or []) if isinstance(q, dict) and _t(q.get("text"))][:MAX_QUESTIONS]
    # the findings of "Prüfen" go along too: an AI error in the next step shows that step again with them (I4)
    checks = [{"issues": [i for i in (c.get("issues") or []) if isinstance(i, str) and i in ISSUES][:len(ISSUES)],
               "suggestion": _t(c.get("suggestion")), "why": _t(c.get("why"), 300)}
              for c in (raw.get("checks") or []) if isinstance(c, dict)][:MAX_QUESTIONS]
    return {"questions": questions, "checks": checks}


def _checked_again(form, st):
    """The page "Prüfen" as it was sent: the findings, the chosen way and the edited suggestion per question (I4)."""
    out = []
    for n in range(len(st["questions"])):
        c = dict(st["checks"][n]) if n < len(st["checks"]) else {"issues": [], "suggestion": "", "why": ""}
        if form.get(f"s{n}") is not None:
            c["suggestion"] = _t(form.get(f"s{n}"))
        choice = form.get(f"c{n}")
        c["choice"] = choice if choice in ("suggest", "keep", "drop") else ""
        out.append(c)
    return out


@bp.route("/<code>/ai/develop", methods=["GET", "POST"])
@login_required
def develop(code):
    """A1: the four steps on one page, one after the other; the state goes along in the form."""
    from app import undo

    survey = _survey(code)
    steps = (("sammeln", de_en("Sammeln", "Collect")), ("pruefen", de_en("Prüfen", "Check")),
             ("sortieren", de_en("Sortieren", "Sort")), ("subsumieren", de_en("Subsumieren", "Subsume")))
    page = lambda step, **kw: render_template("interview/ai_develop.html", survey=survey, step=step, issues=issues(),
                                              sources=_sources(), steps=steps, **kw)
    if request.method == "GET":
        return page("sammeln", questions=[], own="")
    form, action = request.form, request.form.get("action", "")
    st = _state(form)
    # an AI error shows the page that sent the action again, with what was sent on it (I4), not "Sammeln" anew
    back = lambda: page("sammeln", questions=st["questions"],  # noqa: E731
                        own="\n".join(q["text"] for q in st["questions"] if q["src"] == "own"))
    try:
        if action in ("collect", "to_check", "to_check_skip"):
            own = [_t(line) for line in form.get("own", "").splitlines() if _t(line)][:MAX_QUESTIONS]
            kept = [q for n, q in enumerate(st["questions"]) if q["src"] == "ai" and str(n) in form.getlist("keep_ai")]
            questions = [{"text": q, "src": "own", "why": ""} for q in own] + kept
            back = lambda: page("sammeln", questions=questions, own="\n".join(own))  # noqa: E731
            if action == "collect":
                added = collect(survey, [q["text"] for q in questions])
                return page("sammeln", questions=questions + added, own="\n".join(own), fresh=len(added))
            if not questions:
                flash(de_en("Bitte zuerst Fragen eintragen oder von der KI vorschlagen lassen.",
                            "Please first enter questions or let the AI suggest some."), "error")
                return page("sammeln", questions=[], own="")
            texts = [q["text"] for q in questions]
            checks = check(survey, texts) if action == "to_check" else [{"issues": [], "suggestion": "", "why": ""} for _ in texts]
            return page("pruefen", questions=questions, checks=checks)
        if action in ("to_sort", "to_sort_skip"):
            back = lambda: page("pruefen", questions=st["questions"], checks=_checked_again(form, st))  # noqa: E731
            texts = []
            for n, q in enumerate(st["questions"]):
                choice = form.get(f"c{n}", "keep")
                if choice == "drop":
                    continue
                texts.append(_t(form.get(f"s{n}")) if choice == "suggest" and _t(form.get(f"s{n}")) else q["text"])
            if not texts:
                flash(de_en("Alle Fragen gestrichen; bitte mindestens eine behalten.", "All questions dropped; please keep at least one."), "error")
                return page("sammeln", questions=[], own="")
            questions = [{"text": t, "src": "own", "why": ""} for t in texts]
            groups = sort(survey, texts) if action == "to_sort" else [{"title": "Themenblock 1", "questions": list(range(len(texts)))}]
            return page("sortieren", questions=questions, groups=groups)
        if action in ("to_subsume", "to_subsume_skip"):
            texts = [q["text"] for q in st["questions"]]
            titles = [_t(t, 200) for t in form.getlist("title")][:20]
            groups = [{"title": t or f"Themenblock {n + 1}", "questions": []} for n, t in enumerate(titles)]
            for n in range(len(texts)):
                g = form.get(f"g{n}", type=int)
                if g is not None and 0 <= g < len(groups):
                    groups[g]["questions"].append(n)
            groups = [g for g in groups if g["questions"]]
            back = lambda: page("sortieren", questions=st["questions"], groups=groups)  # noqa: E731
            if not groups:
                flash(de_en("Keine Frage ist einem Themenblock zugeordnet.", "No question is assigned to a topic block."), "error")
                return page("sortieren", questions=st["questions"], groups=[{"title": "Themenblock 1", "questions": list(range(len(texts)))}])
            blocks = subsume(survey, groups, texts) if action == "to_subsume" else [plain_block(g, texts) for g in groups]
            return page("subsumieren", blocks=blocks)
        if action == "apply":
            from app.items.guide_question import MINUTES

            blocks = []
            for n in form.getlist("take"):
                prompt = _t(form.get(f"b{n}_prompt"), 1000)
                if not prompt:
                    continue
                minutes = form.get(f"b{n}_minutes", type=int)
                blocks.append({"title": _t(form.get(f"b{n}_title"), 200), "prompt": prompt,
                               "aspects": _lines(form.get(f"b{n}_aspects", "").splitlines()),
                               "keep": _lines(form.get(f"b{n}_keep", "").splitlines(), 6),
                               "probe": _lines(form.get(f"b{n}_probe", "").splitlines()),
                               "minutes": minutes if minutes in MINUTES else 10})
            if not blocks:
                flash(de_en("Kein Themenblock ausgewählt; nichts übernommen.", "No topic block selected; nothing accepted."), "warning")
                return redirect(url_for("interview.guide", code=survey.code))
            step = undo.begin(survey)
            made = apply_blocks(survey, blocks)
            if len(made) == 1:
                step.done(undo.both("{n} Themenblock vom KI-Assistenten übernommen", "{n} topic block accepted from the AI assistant",
                                    n=len(made)), block=made[0].id)
            else:
                step.done(undo.both("{n} Themenblöcke vom KI-Assistenten übernommen", "{n} topic blocks accepted from the AI assistant",
                                    n=len(made)), block=made[0].id)
            db.session.commit()
            flash(de_en("{n} Themenblock mit Leitfragen übernommen.", "{n} topic block with guide questions accepted.", n=len(made))
                  if len(made) == 1 else
                  de_en("{n} Themenblöcke mit Leitfragen übernommen.", "{n} topic blocks with guide questions accepted.", n=len(made)),
                  "success")
            return redirect(url_for("interview.guide", code=survey.code) + f"#block-{made[0].id}")
    except ai.AIError as e:
        flash(str(e), "error")
        return back()
    abort(400)


@bp.route("/<code>/ai/guide-question/<int:item_id>", methods=["POST"])
@login_required
def extend_page(code, item_id):
    """A2, from the element editor of a Leitfrage (inside the guide page: embedded, ?embed=1)."""
    from app import undo
    from app.models import Item

    survey = _survey(code)
    item = db.session.get(Item, item_id)
    if item is None or item.block.survey_id != survey.id or item.type != "guide_question":
        abort(404)
    back = redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, embed=request.args.get("embed")))
    if request.form.get("action") == "apply":
        chosen = {part: [_t(request.form.get(f"{part}{n}"), 300) for n in request.form.getlist(f"take_{part}")
                         if _t(request.form.get(f"{part}{n}"), 300)] for part in ("aspects", "keep", "probe")}
        from app import interview

        subs = [(_t(request.form.get(f"subs{n}"), 300), _t(request.form.get(f"subs_when{n}"), 100))
                for n in request.form.getlist("take_subs") if _t(request.form.get(f"subs{n}"), 300)]
        if dict(interview.outline(item.block)).get(item) is not None:
            subs = []  # an Unterfrage has none of its own (one level)
        if not any(chosen.values()) and not subs:
            flash(de_en("Nichts ausgewählt; nichts übernommen.", "Nothing selected; nothing accepted."), "warning")
            return back
        step = undo.begin(survey)
        for part, rows in chosen.items():
            if rows:
                add_rows(item, part, rows)
        for text, when in subs:
            interview.add_sub(survey, item, text, when)
        step.done(undo.both("„{code}“ vom KI-Assistenten ergänzt", "“{code}” completed by the AI assistant", code=item.code), item=item.id)
        db.session.commit()
        flash(de_en("Übernommen.", "Accepted."), "success")
        return back
    try:
        found = extend(survey, item)
    except ai.AIError as e:
        flash(str(e), "error")
        return back
    return render_template("interview/ai_extend.html", survey=survey, item=item, found=found, sources=_sources())


@bp.route("/<code>/ai/check", methods=["POST"])
@login_required
def review_page(code):
    """A3, from the guide page."""
    from app import undo
    from app.models import Item

    survey = _survey(code)
    if request.form.get("action") == "apply":
        changes = []
        for n in request.form.getlist("take"):
            item = db.session.get(Item, request.form.get(f"p{n}_item", type=db_id) or 0)
            text = _t(request.form.get(f"p{n}_text"), 1000)
            pid = request.form.get(f"p{n}_id", "")
            if item is not None and item.block.survey_id == survey.id and item.type == "guide_question" and text \
                    and part_text(item, pid) is not None:
                changes.append((item, pid, text))
        if not changes:
            flash(de_en("Nichts ausgewählt; nichts übernommen.", "Nothing selected; nothing accepted."), "warning")
            return redirect(url_for("interview.guide", code=survey.code))
        step = undo.begin(survey)
        for item, pid, text in changes:
            set_part(item, pid, text)
        n = len(changes)
        step.done(undo.both("{n} Vorschlag der KI-Prüfung übernommen", "{n} suggestion of the AI check accepted", n=n) if n == 1
                  else undo.both("{n} Vorschläge der KI-Prüfung übernommen", "{n} suggestions of the AI check accepted", n=n),
                  item=changes[0][0].id)
        db.session.commit()
        flash(de_en("{n} Vorschlag übernommen.", "{n} suggestion accepted.", n=n) if n == 1
              else de_en("{n} Vorschläge übernommen.", "{n} suggestions accepted.", n=n), "success")
        return redirect(url_for("interview.guide", code=survey.code))
    try:
        found = review(survey)
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("interview.guide", code=survey.code))
    return render_template("interview/ai_review.html", survey=survey, found=found, sources=_sources())


@bp.route("/<code>/ai/part/<which>", methods=["POST"])
@login_required
def form_page(code, which):
    """A4: Kurzfragebogen ("short") or Postskript ("post")."""
    from app import interview, undo

    survey = _survey(code)
    block = interview.shown_form(survey, which) if which in FORMS else None
    if block is None:
        abort(404)
    if request.form.get("action") == "apply":
        chosen = []
        for n in request.form.getlist("take"):
            kind = request.form.get(f"q{n}_type")
            question = _t(request.form.get(f"q{n}_question"))
            options = _lines(request.form.get(f"q{n}_options", "").splitlines(), 8)
            if kind in FORM_TYPES and question and not (kind in ("single_choice", "multiple_choice") and len(options) < 2):
                chosen.append({"type": kind, "question": question, "options": options,
                               "left": _t(request.form.get(f"q{n}_left"), 80), "right": _t(request.form.get(f"q{n}_right"), 80)})
        if not chosen:
            flash(de_en("Nichts ausgewählt; nichts übernommen.", "Nothing selected; nothing accepted."), "warning")
            return redirect(url_for("interview.guide", code=survey.code) + f"#block-{block.id}")
        step = undo.begin(survey)
        add_form_items(survey, block, chosen)
        n = len(chosen)
        step.done(undo.both("{part}: {n} Frage vom KI-Assistenten übernommen", "{part}: {n} question accepted from the AI assistant",
                            part=undo.both(interview.PARTS[which], interview.PARTS_EN[which]), n=n) if n == 1
                  else undo.both("{part}: {n} Fragen vom KI-Assistenten übernommen", "{part}: {n} questions accepted from the AI assistant",
                                 part=undo.both(interview.PARTS[which], interview.PARTS_EN[which]), n=n), block=block.id)
        db.session.commit()
        flash(de_en("{n} Frage übernommen.", "{n} question accepted.", n=n) if n == 1
              else de_en("{n} Fragen übernommen.", "{n} questions accepted.", n=n), "success")
        return redirect(url_for("interview.guide", code=survey.code) + f"#block-{block.id}")
    try:
        found = forms(survey, which)
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("interview.guide", code=survey.code) + f"#block-{block.id}")
    return render_template("interview/ai_forms.html", survey=survey, which=which, part=interview.parts()[which], found=found,
                           sources=_sources())


@bp.route("/<code>/ai/categories", methods=["POST"])
@login_required
def categories_page(code):
    """A5, from the export page: definitions for the category system, kept in item.config["category"]."""
    from app import undo
    from app.models import Item

    survey = _survey(code)
    back = redirect(url_for("interview.export", code=survey.code))
    if request.form.get("action") == "apply":
        changed, step = [], undo.begin(survey)
        for n in request.form.getlist("take"):
            item = db.session.get(Item, request.form.get(f"c{n}_item", type=db_id) or 0)
            if item is None or item.block.survey_id != survey.id or item.type != "guide_question":
                continue
            rids = {rid for rid, _ in item.kind.rows(item, "aspects")}
            aspects = {rid: _t(request.form.get(f"c{n}_a_{rid}"), 500) for rid in rids if _t(request.form.get(f"c{n}_a_{rid}"), 500)}
            item.config = {**(item.config or {}), "category": {"definition": _t(request.form.get(f"c{n}_definition"), 1000),
                                                               "delimit": _t(request.form.get(f"c{n}_delimit"), 1000), "aspects": aspects}}
            changed.append(item)
        if not changed:
            flash(de_en("Nichts ausgewählt; nichts übernommen.", "Nothing selected; nothing accepted."), "warning")
            return back
        n = len(changed)
        step.done(undo.both("Kategoriendefinitionen für {n} Leitfrage übernommen", "Category definitions for {n} guide question accepted", n=n)
                  if n == 1 else
                  undo.both("Kategoriendefinitionen für {n} Leitfragen übernommen", "Category definitions for {n} guide questions accepted", n=n),
                  item=changed[0].id)
        db.session.commit()
        flash(de_en("Übernommen; das Kategoriensystem (.qdc) enthält die Definitionen.",
                    "Accepted; the category system (.qdc) contains the definitions."), "success")
        return back
    try:
        found = categories(survey)
    except ai.AIError as e:
        flash(str(e), "error")
        return back
    return render_template("interview/ai_categories.html", survey=survey, found=found, sources=_sources())
