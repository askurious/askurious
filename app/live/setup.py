"""Creating a live session: the start page, the setup wizard, templates and the first question."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en, english
from app.live.base import TYPES, _day, _qr, _run, _survey, blocks, bp, join_url, state, types
from app.models import Block, Survey, utcnow


# --- list and editing -------------------------------------------------------------------------------------------


@bp.route("/")
@login_required
def index():
    """Live votes stand in the list of "Meine Befragungen" (live concept 6.1): its filter "Live"."""
    return redirect(url_for("dashboard.index", live=1))


# --- the short wizard (concept 6.2): title and language, first question, done -------------------------------------

STEPS = (("titel", "Titel und Sprache"), ("frage", "Folien"), ("fertig", "Fertig"))
STEPS_EN = (("titel", "Title and language"), ("frage", "Slides"), ("fertig", "Done"))


def steps():
    return STEPS_EN if english() else STEPS


def _t(de_du, de, en):
    return {"de-du": de_du, "de": de, "en": en}


# ready-made live votes for the wizard (improvement 3): name, note, questions as live_ai.apply takes them, the texts
# per language (du, Sie, English; other languages take the English ones)
TEMPLATES = {
    "feedback": ("Feedback zur Sitzung", "Gefallen, Tempo, was hängen bleibt, was besser werden soll", [
        ("rating", _t("Wie hat dir die heutige Sitzung gefallen?", "Wie hat Ihnen die heutige Sitzung gefallen?",
                      "How did you like today's session?"), None, "smileys"),
        ("single_choice", _t("Wie war das Tempo?", "Wie war das Tempo?", "How was the pace?"),
         _t(["zu langsam", "genau richtig", "zu schnell"], ["zu langsam", "genau richtig", "zu schnell"],
            ["too slow", "just right", "too fast"]), None),
        ("text_input", _t("Was nimmst du heute mit? Ein Wort genügt.", "Was nehmen Sie heute mit? Ein Wort genügt.",
                          "What do you take away today? One word is enough."), None, None),
        ("text_input", _t("Was sollte nächstes Mal anders sein?", "Was sollte nächstes Mal anders sein?",
                          "What should be different next time?"), None, None)]),
    "muddiest": ("Unklarster Punkt", "„Muddiest Point“: was unklar blieb, wie sicher man sich fühlt", [
        ("text_input", _t("Was war heute am unklarsten?", "Was war heute am unklarsten?", "What was least clear today?"), None, None),
        ("rating", _t("Wie sicher fühlst du dich beim heutigen Thema?", "Wie sicher fühlen Sie sich beim heutigen Thema?",
                      "How confident do you feel about today's topic?"), None, "smileys")]),
    "vorwissen": ("Vorwissen abfragen", "zu Beginn: was schon bekannt ist und was man lernen möchte", [
        ("rating", _t("Wie gut kennst du dich mit dem heutigen Thema schon aus?", "Wie gut kennen Sie sich mit dem heutigen Thema schon aus?",
                      "How well do you already know today's topic?"), None, "smileys"),
        ("single_choice", _t("Hast du dazu schon etwas gelesen oder gehört?", "Haben Sie dazu schon etwas gelesen oder gehört?",
                             "Have you read or heard about it before?"),
         _t(["noch nie", "ein wenig", "schon öfter"], ["noch nie", "ein wenig", "schon öfter"], ["never", "a little", "often"]), None),
        ("text_input", _t("Was möchtest du heute lernen?", "Was möchten Sie heute lernen?", "What would you like to learn today?"), None, None)]),
    "einstieg": ("Einstieg und Stimmung", "Ankommen: Stimmung und ein Wort zum Thema", [
        ("single_choice", _t("Wie geht es dir heute?", "Wie geht es Ihnen heute?", "How are you today?"),
         _t(["gut", "geht so", "müde"], ["gut", "geht so", "müde"], ["good", "so-so", "tired"]), None),
        ("text_input", _t("Was fällt dir zum heutigen Thema ein?", "Was fällt Ihnen zum heutigen Thema ein?",
                          "What comes to mind about today's topic?"), None, None)]),
    # a talk held with PowerPoint or PDF (for everyone since 2026-10-17): no questions, only the back channel: questions from
    # the audience and emojis per slide, gone through at the end in the review (_apply_template sets the options)
    "vortrag": ("Fragen & Feedback zum Vortrag",
                "neben PowerPoint oder PDF: Fragen aus dem Publikum und Emojis je Folie sammeln, am Ende durchgehen", []),
}


# the templates' names and notes in English, for the presenter's page (templates())
TEMPLATE_NAMES_EN = {"feedback": ("Feedback on the session", "liking, pace, what sticks, what should improve"),
                     "muddiest": ("Muddiest point", "what stayed unclear, how confident people feel"),
                     "vorwissen": ("Prior knowledge", "at the start: what is known already and what people want to learn"),
                     "einstieg": ("Warm-up and mood", "arriving: the mood and a word on the topic"),
                     "vortrag": ("Questions & feedback on a talk",
                                 "beside PowerPoint or PDF: collect questions from the audience and emojis per slide, "
                                 "go through them at the end")}
TALK = "vortrag"


def templates():
    """TEMPLATES with name and note in the language of the presenter's page ("Fragen & Feedback zum Vortrag" for
    everyone since 2026-10-17)."""
    shown = dict(TEMPLATES)
    if not english():
        return shown
    return {k: (*TEMPLATE_NAMES_EN[k], qs) for k, (_, _, qs) in shown.items()}


def _apply_template(survey, key):
    """The questions of a template into the first block; the talk template instead switches on what a talk needs:
    questions from the audience and emojis to show what is unclear or too fast, the phones following the screen."""
    from app import live_ai

    if key == TALK:
        survey.live = {**state(survey), "qa": True, "reactions": True, "emojis": ["👍", "😕", "🐢", "🐇", "💡"],
                       "flow": "questions"}
        return
    live_ai.apply(survey, blocks(survey)[0], template_questions(key, survey.default_language))


def template_questions(key, lang):
    """The questions of a template in the language of the vote, as live_ai.apply takes them."""
    pick = lambda texts: texts.get(lang) or texts["en"]
    out = []
    for kind, question, options, style in TEMPLATES[key][2]:
        q = {"type": kind, "question": pick(question)}
        if options:
            q["options"] = pick(options)
        if kind == "rating":
            q["style"] = style or "smileys"  # smileys unless a template says otherwise (Philipp, 2026-10-04)
        out.append(q)
    return out
# the first question: tiles named as in a lecture, each one of the types a live vote offers
FIRST = (("single_choice", "Eine Antwort", "Auswahl aus mehreren Antworten"),
         ("multiple_choice", "Mehrere Antworten", "Mehrere Antworten ankreuzen"),
         ("rating", "Skala", "Bewertung, z. B. mit Sternen oder von 1 bis 5"),
         ("ranking", "Rangfolge", "Optionen in eine Reihenfolge bringen"),
         ("constant_sum", "100 Punkte", "Punkte auf Optionen verteilen"),
         ("text_input", "Wortwolke", "Ein kurzes Wort oder eine kurze Antwort eintippen"),
         ("slider", "Zahl schätzen", "Einen Wert auf einem Regler einstellen"))


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """Step 1, before the live vote exists: title, course (Projektzugehörigkeit), language of the participants."""
    from app import flow, kind_access, surveys

    refused = kind_access.creation_refused(current_user, "live")  # says why
    if refused:
        return refused
    if request.method == "POST":
        lang = request.form.get("language", "de-du")
        lang = lang if lang in surveys.LANGUAGE_NAMES else "de-du"
        title = (" ".join(request.form.get("title", "").split())[:300]
                 or de_en("Live-Abstimmung {d}", "Live poll {d}", d=_day(utcnow())))
        survey = Survey(title=title, created_by_id=current_user.id,
                        languages=[lang], default_language=lang, language_choice=False, allow_back=False,
                        live={"block": None, "screen": None, "show": True, "run": _run(de_en("Durchgang 1", "Run 1")), "runs": [],
                              "flow": "questions"},  # new live votes go question by question (Ralf, 2026-10-16; before: blocks, Philipp 2026-10-03); older ones keep theirs
                        image_style={"key": "watercolor", "custom": ""},  # the first style, "Tusche & Aquarell" (2026-10-04)
                        show_progress=False, setup_step="frage")
        db.session.add(survey)
        surveys._add_member(survey, current_user, current_user.name)
        survey.blocks.append(Block(position=0, titles={lang: "Block 1"}))
        db.session.flush()
        flow.normalize(survey)
        chosen = request.form.get("template", "")
        if chosen in templates():  # the card "Aus einer Vorlage": the questions at once, then "Fertig"
            _apply_template(survey, chosen)
            survey.setup_step = "fertig"
        db.session.commit()
        return redirect(url_for("live.step", code=survey.code, key=survey.setup_step))
    from app import wizard

    return render_template("live/new.html", survey=None, key="titel", steps=steps(), index=0, first=FIRST,
                           languages=surveys.LANGUAGE_NAMES, starts=wizard.starts("live"), start=wizard.start_of(),
                           templates=templates())


@bp.route("/<code>/setup/<step:key>", methods=["GET", "POST"])
@login_required
def step(code, key):
    from app import surveys

    if key not in ("frage", "fertig"):
        abort(404)
    survey = _survey(code, need="editor")
    page = lambda **extra: render_template("live/new.html", survey=survey, key=key, steps=steps(), templates=templates(),
                                           index=[k for k, _ in STEPS].index(key), first=FIRST, join=join_url(survey),
                                           qr=_qr(survey), languages=surveys.LANGUAGE_NAMES, types=types(), **extra)
    if request.method == "POST" and key == "frage" and request.form.get("wizard") in ("ai", "ai_apply"):
        return _wizard_ai(survey, page)
    if request.method == "POST" and key == "frage" and request.form.get("wizard") == "own":  # own questions: the editing page
        survey.setup_step = None
        db.session.commit()
        return redirect(url_for("live.manage", code=code))
    if request.method == "POST" and key == "frage" and request.form.get("wizard") == "template":
        chosen = request.form.get("template", "")
        if chosen not in templates():
            flash(de_en("Bitte eine Vorlage wählen.", "Please choose a template."), "error")
            return page(own="template")
        _apply_template(survey, chosen)
        survey.setup_step = "fertig"
        db.session.commit()
        return redirect(url_for("live.step", code=code, key="fertig"))
    if request.method == "POST":
        if key == "frage":
            kind_key = request.form.get("type", "")
            if request.form.get("wizard") != "skip":
                if kind_key not in TYPES:
                    return redirect(url_for("live.step", code=code, key="frage"))
                _add_first(survey, kind_key, request.form.get("question", ""))
            survey.setup_step = "fertig"
            db.session.commit()
            return redirect(url_for("live.step", code=code, key="fertig"))
        survey.setup_step = None
        db.session.commit()
        return redirect(url_for("live.present" if request.form.get("wizard") == "present" else "live.manage", code=code))
    return page()


def _wizard_ai(survey, page):
    """The wizard's step "Fragen" with the AI assistant: five suggestions, then the ticked ones into the live vote."""
    from app import ai, live_ai

    description = request.form.get("description", "").strip()[:live_ai.MAX_DESCRIPTION]
    if request.form.get("wizard") == "ai_apply":
        chosen = live_ai.from_form(request.form)
        if not chosen:
            flash(de_en("Keine Folie ausgewählt. Bitte ankreuzen oder die Folien selbst festlegen.",
                        "No slide selected. Please tick some or set the slides yourself."), "warning")
            return page(description=description, suggestions=live_ai.from_form_all(request.form))
        live_ai.apply(survey, blocks(survey)[0], chosen)
        survey.setup_step = "fertig"
        db.session.commit()
        return redirect(url_for("live.step", code=survey.code, key="fertig"))
    if not description:
        flash(de_en("Bitte beschreiben Sie kurz Ihre Veranstaltung und was Sie wissen möchten.",
                    "Please describe your event briefly and what you would like to know."), "error")
        return page()
    try:
        return page(description=description, suggestions=live_ai.suggest(survey, description))
    except ai.AIError as e:
        flash(str(e), "error")
        return page(description=description)


def _add_first(survey, kind_key, question):
    """The wizard's first question, in the first block, with the typed question text."""
    from app import items
    from app.editor import _renumber, _unique_code
    from app.models import Item

    kind = items.get(kind_key)
    lang = survey.default_language
    texts = {lang: {**kind.default_texts(lang), **({"question": question.strip()[:2000]} if question.strip() else {})}}
    block = blocks(survey)[0]
    rows = list(block.items) + [Item(type=kind_key, answer_mode="optional", code=_unique_code(survey, "Q"),
                                     config=kind.default_config(), texts=texts)]
    block.items = rows
    _renumber(rows)
