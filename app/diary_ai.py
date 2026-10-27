"""AI assistant of a diary survey (Ralf 2026-10-27, after the assistants of the interview guide and the live poll,
app/interview_ai.py and app/live_ai.py). As they do, it suggests with a short reason, and nothing changes until the
researchers tick, edit if they like, and take over; taking over is one step of the history (Rückgängig). The daily
limits of the AI calls apply (ai.generate_text). Each step can be skipped; the researchers decide.

  D1 Design      the sampling scheme of the short survey "Moment" (interval-, signal- or event-contingent, or once at
                 the end of the day; Bolger & Laurenceau, 2013), prompts per day and length of the study, with reasons;
                 accepted, it sets the schedule of the first short survey (diary.clean keeps it in bounds).
  D2 Fragen      short state questions for the research question (affect, context, event, behaviour): a construct of
                 the toolbox with few items where one fits, else a draft marked "KI-generiert" (config "ai").
  D3 Prüfen      notes on the burden per prompt and in total, reactivity, missing context or time questions, and what
                 the entry and exit survey could ask; advice only, nothing to take over.

Sent to the text model: the title, research question, description and population of the study, the parts of the
diary survey with their questions (no answers, no participants) and the schedule. The questions it writes are in the
survey's language, its reasons in the language of the researcher's page (E12)."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import ai
from app.extensions import db
from app.lang import de_en, english

bp = Blueprint("diary_ai", __name__, url_prefix="/diary")

SCHEMES = ("interval", "signal", "event", "evening")
MAX_QUESTIONS = 6
MAX_OPTIONS = 7
MAX_EXISTING = 40
MAX_CONSTRUCTS = 40
SHORT_FORM = 6  # a toolbox construct with at most this many items counts as short enough for a prompt
# the trigger each scheme gives the first short survey, as diary.DIARY_TEMPLATES writes them; minutes to answer
SCHEME_EXPIRES = {"interval": 60, "signal": 30, "event": 0, "evening": 180}
NOTE_TOPICS = ("burden", "reactivity", "context", "time", "entry", "exit", "other")


def schemes():
    """{scheme: (name, what it means)} in the language of the page."""
    if english():
        return {"interval": ("Interval-contingent", "at fixed times of the day"),
                "signal": ("Signal-contingent", "at random times within a window (experience sampling)"),
                "event": ("Event-contingent", "the person reports whenever the event happens"),
                "evening": ("End of the day", "once in the evening, a look back on the day")}
    return {"interval": ("Intervallgesteuert", "zu festen Zeiten am Tag"),
            "signal": ("Signalgesteuert", "zu zufälligen Zeiten in einem Zeitfenster (Experience Sampling)"),
            "event": ("Ereignisgesteuert", "die Person meldet sich, wenn das Ereignis eintritt"),
            "evening": ("Am Tagesende", "einmal abends, ein Rückblick auf den Tag")}


def note_topics():
    if english():
        return {"burden": "Burden", "reactivity": "Reactivity", "context": "Context", "time": "Time",
                "entry": "Entry survey", "exit": "Exit survey", "other": "Other"}
    return {"burden": "Belastung", "reactivity": "Reaktivität", "context": "Kontext", "time": "Zeitpunkt",
            "entry": "Eingangsbefragung", "exit": "Abschlussbefragung", "other": "Sonstiges"}


METHOD = """Method (Bolger & Laurenceau, 2013, Intensive longitudinal methods; cite no other source and invent none):
- interval-contingent: fixed times, good for processes that change over the day and for comparing days;
- signal-contingent (experience sampling): random times within waking hours, less anticipation, good for momentary
  states such as affect;
- event-contingent: the person reports when a defined event happens (e.g. a conflict, a learning session); needs a
  clear definition of the event;
- end of the day: one look back each evening, least burden, more recall bias.
Burden grows with prompts per day, items per prompt and days; a prompt should take one or two minutes; more than
about five prompts a day or more than a few weeks lowers compliance."""


def _context(survey):
    """What the model sees of the study: no answers, no participants."""
    from app import diary, study_plan
    from app.items.base import plain
    from app.surveys import LANGUAGE_NAMES

    lang = survey.default_language
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as usual in the language")
    plan = study_plan.plan_of(survey)
    facts = [f"Title: {survey.title}"]
    if survey.research_question:
        facts.append(f"Research question: {survey.research_question[:2000]}")
    if (survey.description or "").strip():
        facts.append(f"Description: {' '.join(survey.description.split())[:2000]}")
    population = ((plan.get("sample") or {}).get("population") or "").strip()
    if population:
        facts.append(f"Population: {population[:300]}")
    sampling = survey.sampling or {}
    facts.append(f"Length of the study: {sampling.get('period_days') or 'open end'} days")
    lines, n = [], 0
    for block in survey.blocks:
        part = (block.settings or {}).get("part") or ""
        texts = [" ".join(plain(i.text("question") or "").split())[:200] for i in block.items if i.type != "consent"]
        texts = [t for t in texts if t]
        if not texts:
            continue
        name = block.title() if hasattr(block, "title") else ""
        mod = diary.module(survey, part) if part else None
        when = _trigger_text((mod or {}).get("trigger") or {}) if mod else ""
        lines.append(f"Part {name}" + (f" ({when})" if when else "") + ":")
        for t in texts:
            if n >= MAX_EXISTING:
                break
            lines.append(f"- {t}")
            n += 1
    if lines:
        facts.append("Parts of the diary survey and their questions:\n" + "\n".join(lines))
    return LANGUAGE_NAMES.get(lang, lang), address, "\n".join(facts)


def _trigger_text(trigger):
    t = trigger.get("t")
    if t == "fixed":
        return "at " + ", ".join(trigger.get("times") or [])
    if t == "random":
        return f"{trigger.get('per_day')} random prompts a day"
    if t == "self":
        return "started by the person (event)"
    return ""


def _call(survey, instructions, prompt, purpose):
    language, address, facts = _context(survey)
    reasons = "English" if english() else "German"  # what only the researcher reads: in the language of the page
    text = ai.generate_text(instructions.format(language=language, address=address, method=METHOD, reasons=reasons),
                            facts + ("\n\n" + prompt if prompt else ""), purpose=purpose)
    return ai.json_answer(text)


def _t(value, limit=500):
    return " ".join(str(value or "").split())[:limit]


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _time(value, default):
    value = str(value or "").strip()
    if len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit():
        if int(value[:2]) < 24 and int(value[3:]) < 60:
            return value
    return default


# ---- D1 design ------------------------------------------------------------------------------------------------------

DESIGN = """You help researchers plan a diary survey (intensive longitudinal design): participants answer short
surveys ("Moment") on their phone, over days or weeks. From the study below, suggest ONE sampling scheme for the short
survey, the number of prompts per day and the length of the study in days, with a short reason.
{method}
Answer with ONE JSON object and nothing else:
{{"scheme": "interval" | "signal" | "event" | "evening", "per_day": 1-10, "days": 1-60, "window": ["09:00", "21:00"],
  "times": ["09:00", "14:00", "19:00"] (only for interval), "why": "short reason"}}
The reason in {reasons}."""


def design(survey):
    """D1: {scheme, per_day, days, window, times, why} (raises ai.AIError)."""
    data = _call(survey, DESIGN, "", "diary_design")
    found = clean_design(data)
    if not found:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI's answer could not be read. Please try again."))
    return found


def clean_design(raw):
    """A design as the page and apply_design() use it, or None."""
    from app import diary

    if not isinstance(raw, dict) or raw.get("scheme") not in SCHEMES:
        return None
    scheme = raw["scheme"]
    per_day = _int(raw.get("per_day"), 3, 1, min(10, diary.MAX_PER_DAY))
    window = raw.get("window") if isinstance(raw.get("window"), list) and len(raw.get("window")) == 2 else []
    start, end = _time(window[0] if window else "", "09:00"), _time(window[1] if window else "", "21:00")
    if start >= end:
        start, end = "09:00", "21:00"
    times = [_time(t, "") for t in ai.as_list(raw.get("times"))]
    times = sorted({t for t in times if t})[:diary.MAX_TIMES]
    if scheme == "interval" and not times:
        times = _spread(per_day, start, end)
    if scheme == "evening":
        per_day, times = 1, times[:1] or ["20:00"]
    return {"scheme": scheme, "per_day": len(times) if scheme == "interval" else per_day,
            "days": _int(raw.get("days"), 14, 1, 60), "window": [start, end], "times": times if scheme in ("interval", "evening") else [],
            "why": _t(raw.get("why"), 400)}


def _spread(n, start, end):
    """n fixed times spread evenly from start to end."""
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(end[:2]) * 60 + int(end[3:])
    if n == 1:
        return [start]
    step = (b - a) / (n - 1)
    return [f"{int(a + k * step) // 60:02d}:{int(a + k * step) % 60:02d}" for k in range(n)]


def design_from_form(form):
    times = [t.strip() for t in form.get("times", "").replace(";", ",").split(",") if t.strip()]
    return clean_design({"scheme": form.get("scheme"), "per_day": form.get("per_day"), "days": form.get("days"),
                         "window": [form.get("start"), form.get("end")], "times": times, "why": form.get("why")})


def apply_design(survey, d):
    """Set the trigger and answer time of the first short survey and the length of the study (diary.clean bounds it)."""
    from app import diary

    lang = survey.default_language
    if d["scheme"] in ("interval", "evening"):
        trigger = {"t": "fixed", "times": d["times"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}
    elif d["scheme"] == "signal":
        trigger = {"t": "random", "per_day": d["per_day"], "windows": [d["window"]], "min_gap": 60,
                   "weekdays": [1, 2, 3, 4, 5, 6, 7]}
    else:
        trigger = {"t": "self", "label": {lang: "Etwas melden" if lang.startswith("de") else "Report something"},
                   "max_per_day": d["per_day"]}
    sampling = dict(survey.sampling or {})
    mods = [dict(m) for m in diary.modules(survey)]
    if not mods:
        return False
    mods[0] = {**mods[0], "trigger": trigger, "expires": SCHEME_EXPIRES[d["scheme"]]}
    sampling["modules"] = mods
    sampling["period_days"] = d["days"]
    survey.sampling = diary.clean(sampling)
    return True


# ---- D2 moment questions --------------------------------------------------------------------------------------------

QUESTIONS = """You help researchers write the short survey ("Moment") of a diary survey: it is answered several times a
day on a phone, so it asks about the moment: affect, context (where, with whom, what doing), the event of interest,
behaviour. Write up to {count} short questions in {language} ({address}) for the research question below.
Prefer a construct of the toolbox listed below where one fits (give its key; then leave "question" empty); else draft
a question yourself. Types: "rating" (5 points), "single_choice" (2 to 7 short options), "text_input" (a few words).
Rules: one question asks one thing; about now or the last hour; short enough for a phone; neutral, not leading.
If the short survey already has questions (listed), do not repeat them.
Answer with ONE JSON object and nothing else:
{{"questions": [{{"construct": "key or empty", "type": "rating", "question": "...", "options": [], "why": "short reason"}}]}}
The reasons in {reasons}."""


def short_constructs(survey):
    """[(key, name, items)] of the toolbox constructs short enough for a prompt."""
    from app.models import Construct
    from app import constructs

    out = []
    for c in db.session.scalars(constructs.visible().order_by(Construct.name)):
        spec = c.spec or {}
        n = len((spec.get("config") or {}).get("statements") or []) or 1
        if n <= SHORT_FORM:
            out.append((c.key, c.name, n))
        if len(out) >= MAX_CONSTRUCTS:
            break
    return out


def questions(survey):
    """D2: [{construct, name, type, question, options, why}] (raises ai.AIError)."""
    from app.lang import current  # noqa: F401 - the reasons follow the page (_call)

    short = short_constructs(survey)
    prompt = "Toolbox constructs with few items (key: name, items):\n" + "\n".join(f"- {k}: {n} ({i})" for k, n, i in short)
    data = _call(survey, QUESTIONS.replace("{count}", str(MAX_QUESTIONS)), prompt, "diary_questions")
    names = {k: n for k, n, _ in short}
    found = [q for q in (clean_question(r, names) for r in ai.as_list(data.get("questions"))) if q]
    if not found:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI's answer could not be read. Please try again."))
    return found[:MAX_QUESTIONS]


def clean_question(raw, names):
    if not isinstance(raw, dict):
        return None
    key = raw.get("construct") if raw.get("construct") in names else ""
    why = _t(raw.get("why"), 300)
    if key:
        return {"construct": key, "name": names[key], "type": "", "question": "", "options": [], "why": why}
    kind, question = raw.get("type"), _t(raw.get("question"))
    if kind not in ("rating", "single_choice", "text_input") or not question:
        return None
    options = [_t(o, 200) for o in ai.as_list(raw.get("options")) if _t(o)][:MAX_OPTIONS] if kind == "single_choice" else []
    if kind == "single_choice" and len(options) < 2:
        return None
    return {"construct": "", "name": "", "type": kind, "question": question, "options": options, "why": why}


def questions_from_form(form, names):
    out = []
    for n in form.getlist("take"):
        q = clean_question({"construct": form.get(f"q{n}_construct"), "type": form.get(f"q{n}_type"),
                            "question": form.get(f"q{n}_question"), "options": form.get(f"q{n}_options", "").splitlines(),
                            "why": form.get(f"q{n}_why")}, names)
        if q:
            out.append(q)
    return out


def moment_block(survey):
    """The block of the first short survey."""
    from app import diary

    mods = diary.modules(survey)
    key = mods[0]["key"] if mods else "m1"
    return next((b for b in survey.blocks if (b.settings or {}).get("part") == key), None)


def apply_questions(survey, block, chosen):
    """Add the questions at the end of the block: a construct as the toolbox builds it, a draft marked "KI-generiert"
    (config "ai", ai_scales.stamp); returns the new items."""
    from app import ai_scales, constructs, items
    from app.editor import _renumber, _unique_code
    from app.items.choice import _new_id
    from app.models import Item

    lang = survey.default_language
    rows, new = list(block.items), []
    for q in chosen:
        if q["construct"]:
            try:
                kind_key, code, config, texts = constructs.build(q["construct"], survey.languages, survey=survey)
            except (KeyError, PermissionError, ValueError):
                continue
            item = Item(type=kind_key, answer_mode="optional", code=_unique_code(survey, code), config=config, texts=texts)
        else:
            kind = items.get(q["type"])
            config, texts = kind.default_config(), {**kind.default_texts(lang), "question": q["question"]}
            if q["options"]:
                config["options"] = [{"id": _new_id(), "code": str(n)} for n in range(1, len(q["options"]) + 1)]
                texts["options"] = {o["id"]: label for o, label in zip(config["options"], q["options"])}
            if q["type"] == "rating":
                config["points"] = 5
            config["ai"] = ai_scales.stamp(q["question"])
            item = Item(type=q["type"], answer_mode="optional", code=_unique_code(survey, "KI"), config=config,
                        texts={lang: texts})
        rows.append(item)
        new.append(item)
        block.items = rows  # each code is unique against the ones added before
    _renumber(rows)
    return new


# ---- D3 check -------------------------------------------------------------------------------------------------------

CHECK = """You review the plan of a diary survey (intensive longitudinal design) before it starts. From the study and
its parts below, write up to 6 short notes: burden per prompt and in total, reactivity (does answering change what is
measured?), missing context or time questions, and what the entry survey (once, at joining) and the exit survey (once,
at the end) could ask. Each note says what to change, briefly.
{method}
Answer with ONE JSON object and nothing else:
{{"notes": [{{"topic": "burden" | "reactivity" | "context" | "time" | "entry" | "exit" | "other", "text": "..."}}]}}
The notes in {reasons}."""


def burden(survey):
    """What the plan asks of a person, counted (not by the AI): questions per prompt, about how long, prompts a day and
    in all."""
    from app import diary

    mods = diary.modules(survey)
    trigger = (mods[0].get("trigger") or {}) if mods else {}
    block = moment_block(survey)
    n_items = len([i for i in block.items if i.type != "page_break"]) if block is not None else 0
    per_day = (len(trigger.get("times") or []) if trigger.get("t") == "fixed" else
               int(trigger.get("per_day") or 0) if trigger.get("t") == "random" else 0)
    days = int((survey.sampling or {}).get("period_days") or 0)
    return {"questions": n_items, "seconds": n_items * 10, "per_day": per_day, "days": days,
            "total": per_day * days if per_day and days else None}


def check(survey):
    """D3: [{topic, text}] (raises ai.AIError)."""
    b = burden(survey)
    prompt = (f"Counted: {b['questions']} questions per prompt (about {b['seconds']} seconds), {b['per_day'] or 'self-started'} "
              f"prompts a day, {b['days'] or 'open'} days" + (f", {b['total']} prompts in all." if b["total"] else "."))
    data = _call(survey, CHECK, prompt, "diary_check")
    notes = []
    for raw in ai.as_list(data.get("notes")):
        if isinstance(raw, dict) and _t(raw.get("text")):
            notes.append({"topic": raw.get("topic") if raw.get("topic") in NOTE_TOPICS else "other", "text": _t(raw.get("text"), 500)})
    if not notes:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI's answer could not be read. Please try again."))
    return notes[:6]


# ---- the page ---------------------------------------------------------------------------------------------------------

STEPS = ("design", "fragen", "pruefen")


def _survey(survey_id):
    from app import ai_access, diary

    survey = diary._survey(survey_id, need="editor")
    if not survey.is_editable or not ai_access.ai_on():
        abort(404 if not ai_access.ai_on() else 400)
    return survey


@bp.route("/<sid:survey_id>/ai", methods=["GET", "POST"])
@login_required
def assistant(survey_id):
    """The three steps on one page, chosen above; each suggests on request and takes over what is ticked."""
    from app import diary, undo

    survey = _survey(survey_id)
    step = request.values.get("step") if request.values.get("step") in STEPS else "design"
    steps = (("design", de_en("Design", "Design")), ("fragen", de_en("Moment-Fragen", "Moment questions")),
             ("pruefen", de_en("Prüfen", "Check")))
    page = lambda **kw: render_template("diary/ai.html", survey=survey, step=step, steps=steps, schemes=schemes(),  # noqa: E731
                                        topics=note_topics(), bolger=diary.BOLGER, burden=burden(survey), **kw)
    if request.method == "GET":
        return page()
    action = request.form.get("action", "")
    try:
        if action == "design":
            return page(design=design(survey))
        if action == "apply_design":
            d = design_from_form(request.form)
            if not d:
                flash(de_en("Der Vorschlag ist unvollständig; nichts übernommen.", "The suggestion is incomplete; nothing accepted."), "error")
                return page()
            rec = undo.begin(survey)
            if not apply_design(survey, d):
                abort(400)
            rec.done(undo.both("Zeitplan vom KI-Assistenten übernommen", "Schedule accepted from the AI assistant"),
                     page="settings", node="diary")
            db.session.commit()
            flash(de_en("Zeitplan übernommen; im Reiter „Zeitplan“ lässt er sich weiter anpassen.",
                        "Schedule accepted; you can adjust it further on the tab “Schedule”."), "success")
            return redirect(url_for("diary_ai.assistant", survey_id=survey.id, step="fragen"))
        if action == "questions":
            return page(questions=questions(survey))
        if action == "apply_questions":
            names = {k: n for k, n, _ in short_constructs(survey)}
            chosen = questions_from_form(request.form, names)
            block = moment_block(survey)
            if not chosen or block is None:
                flash(de_en("Keine Frage ausgewählt; nichts übernommen.", "No question selected; nothing accepted."), "warning")
                return page()
            rec = undo.begin(survey)
            new = apply_questions(survey, block, chosen)
            db.session.flush()
            if not new:
                db.session.rollback()
                flash(de_en("Nichts übernommen.", "Nothing accepted."), "warning")
                return page()
            rec.done(undo.both("{n} Fragen vom KI-Assistenten übernommen", "{n} questions accepted from the AI assistant",
                               n=len(new)), item=new[0].id)
            db.session.commit()
            flash(de_en("{n} Fragen in die Kurzbefragung übernommen.", "{n} questions added to the short survey.", n=len(new)),
                  "success")
            return redirect(url_for("diary_ai.assistant", survey_id=survey.id, step="pruefen"))
        if action == "check":
            return page(notes=check(survey))
    except ai.AIError as e:
        flash(str(e), "error")
        return page()
    abort(400)
