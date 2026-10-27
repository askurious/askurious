"""Setup wizard for new surveys: the settings page, step by step.

The survey is created after the first step; afterwards every step saves by itself, like the settings page
(UI/UX audit 2026-10-05, V23: data-autosave-form, `autosave=1`); "Weiter" moves on (and saves what a page without
script sends). Survey.setup_step remembers where to continue; everything stays editable later on the single
settings page ("Einstellungen"). The steps left with "Überspringen" are remembered in the session and shown with a
dash instead of a tick (V22).
"""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import autosave

from app.extensions import db
from app.lang import current, de_en, english
from app.models import Block, Survey

bp = Blueprint("wizard", __name__, url_prefix="/surveys")

# key, title, subtitle, optional
STEPS = [
    # six steps, each answers one question, then the overview (Ralf 2026-10-27, after checking every step for content):
    # what it is about (title, description, research question, aims, keywords, languages), who takes part (design,
    # selection, population, details about the person), who carries it (team, contact, funding), how people take part
    # (before the consent, whose text names it), what they agree to, how it looks. The keys stay, so addresses and
    # Survey.setup_step of before still lead somewhere; "sprachen" is now part of "projekt" (FORMER_KEYS).
    ("projekt", "Worum geht es?", "Titel, Beschreibung, Forschungsfrage und Sprachen", False),
    ("studienplan", "Wer nimmt teil?", "Studiendesign, Auswahl, Grundgesamtheit und Angaben zur Person", True),
    ("team", "Verantwortung", "Beteiligte, verantwortliche Personen, Kontakt und Förderung", True),
    ("ablauf", "Teilnahme", "Navigation und erneute Teilnahme; Datenschutz und Paradaten wie empfohlen", True),
    ("einwilligung", "Einwilligung", "wozu Teilnehmende zustimmen", False),
    # the look of the participants' pages; the style of AI images is chosen with the first AI image (images page)
    ("gestaltung", "Erscheinungsbild", "Farben und Schrift der Seiten", True),
    ("fertig", "Übersicht", "Zusammenfassung und nächste Schritte", False),
]
KEYS = [k for k, *_ in STEPS]
# addresses and setup_step from before 2026-10-07, and "sprachen" from before 2026-10-27 (now in "projekt")
FORMER_KEYS = {"bildstil": "gestaltung", "aussehen": "gestaltung", "sprachen": "projekt"}
# the same steps in English: {key: (title, subtitle)}
STEPS_EN = {"projekt": ("What is it about?", "Title, description, research question and languages"),
            "studienplan": ("Who takes part?", "Research design, selection, population and details about the person"),
            "team": ("Responsibility", "People involved, responsible persons, contact and funding"),
            "ablauf": ("Participation", "Navigation and taking part again; privacy and paradata as recommended"),
            "einwilligung": ("Consent", "what participants agree to"),
            "gestaltung": ("Appearance", "Colours and font of the pages"),
            "fertig": ("Overview", "Summary and next steps")}


def steps():
    """STEPS as the wizard shows them, in the language of the page."""
    if not english():
        return STEPS
    return [(k, *STEPS_EN[k], optional) for k, _, _, optional in STEPS]


def _step_url(survey, key, **kw):
    return url_for("wizard.step", survey_id=survey.id, key=key, **kw)


def _neighbours(key):
    i = KEYS.index(key)
    return (KEYS[i - 1] if i > 0 else None), (KEYS[i + 1] if i + 1 < len(KEYS) else None)


DEMOGRAPHICS = {"de": "Demographische Angaben", "en": "Demographics"}


# the gender question of every new survey: GES_1 of the RatSWD (male, female, diverse; the codes of the KonsortSWD
# variable x_sex). The construct "Geschlecht" after Spiel et al. (2019) with self-description stays in the toolbox
# (concept Demografie-Standards 2026-10-15, decision 6b; before: "gender").
DEFAULT_GENDER = "ratswd_ges1"
# what the block "Demographische Angaben" of a new survey asks: age and gender, or the bundle "Demografie kurz
# (RatSWD)" with all its parts (app/ratswd.py); offered by the wizard and AInstein
DEMOGRAPHIC_CHOICES = ("basis", "ratswd")


def demographic_choices():
    """[(value, label)] for the choice of the block "Demographische Angaben", in the language of the page."""
    return [("basis", de_en("Alter und Geschlecht", "Age and sex")),
            ("ratswd", de_en("Demografie kurz (RatSWD): Alter, Geschlecht, Bildung, Erwerb, Beruf, Einkommen, Herkunft, "
                             "Haushalt, Familienstand", "Short demographics (RatSWD): age, sex, education, employment, "
                             "occupation, income, origin, household, marital status"))]


def demographics_block(survey):
    return next((b for b in survey.blocks if (b.settings or {}).get("demographics")), None)


def demographics_kind(survey):
    """"basis", "ratswd" or "none": what the block "Demographische Angaben" holds; "" once it was changed by hand
    (its questions are no longer those the wizard put there, settings "made")."""
    block = demographics_block(survey)
    if block is None:
        return "none"
    made = (block.settings or {}).get("made") or {}
    if made.get("kind") in DEMOGRAPHIC_CHOICES and sorted(made.get("codes") or []) == sorted(i.code for i in block.items):
        return made["kind"]
    return ""


def set_demographics(survey, kind):
    """The choice "Welche Angaben zur Person erheben?" on the step "Wer nimmt teil?" (Ralf 2026-10-27): replaces the
    block "Demographische Angaben" while it is as the wizard made it and the survey is a draft; a block changed by
    hand stays (the questionnaire is the place for it)."""
    from app import flow

    now = demographics_kind(survey)
    if kind == now or now == "" or survey.status != "draft":
        return False
    import copy

    block = demographics_block(survey)
    root = copy.deepcopy(flow.tree(survey))
    old_id = block.id if block is not None else None
    if block is not None:
        if any(i.kind.has_real_answers(i) for i in block.items):
            return False
        survey.blocks.remove(block)  # delete-orphan removes it with its questions
        db.session.flush()
    new_id = None
    if kind in DEMOGRAPHIC_CHOICES:
        added = add_demographics(survey, kind)
        db.session.flush()
        new_id = added.id
    # the flow keeps the place: the new block takes the old one's node; a first one goes right after the consent
    consent_block = survey.consent_item.block_id if survey.consent_item is not None else None

    def swap(node):
        kids = []
        for child in node.get("children", []):
            if child.get("t") == "block" and child.get("block") == old_id:
                if new_id is not None:
                    kids.append({**child, "block": new_id})
                continue
            if child.get("t") in flow.GROUPS:
                swap(child)
            kids.append(child)
            if old_id is None and new_id is not None and child.get("t") == "block" and child.get("block") == consent_block:
                kids.append({"t": "block", "block": new_id})
        node["children"] = kids

    swap(root)
    survey.flow = root
    flow.normalize(survey)
    return True


def add_demographics(survey, kind="basis"):
    """Append the block "Demographische Angaben": "basis" asks age and gender from the construct toolbox (age in
    years and GES_1, DEFAULT_GENDER), "ratswd" the bundle "Demografie kurz (RatSWD)" with all its parts. The survey
    must be in the session (the bundle's filters need the ids of its questions)."""
    from app import constructs, ratswd
    from app.models import Item

    block = Block(position=len(survey.blocks), settings={"demographics": True},  # not counted for "Block n" names
                  titles={l: DEMOGRAPHICS.get(l, DEMOGRAPHICS["en"]) for l in survey.languages})
    survey.blocks.append(block)
    if kind == "ratswd":
        ratswd.add_bundle(survey, ratswd.PARTS, block)
        _mark_made(block, kind)
        return block
    rows = []
    for key in ("age", DEFAULT_GENDER):
        if constructs.get(key) is None:
            continue
        kind_key, code, config, texts = constructs.build(key, survey.languages)
        rows.append(Item(type=kind_key, answer_mode="nudge", code=code, config=config, texts=texts, position=len(rows)))
    block.items = rows
    _mark_made(block, kind)
    return block


def _mark_made(block, kind):
    """What the wizard put into the block, so "Wer nimmt teil?" may still swap it (set_demographics)."""
    block.settings = {**(block.settings or {}), "made": {"kind": kind, "codes": [i.code for i in block.items]}}


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """Step 1 before the survey exists."""
    from app import kind_access, surveys

    refused = kind_access.creation_refused(current_user, "survey")  # "Anlegen" for questionnaires (app/kind_access.py)
    if refused:
        return refused

    if request.method == "POST":
        from app import links

        # the language of the new survey: English for a person whose pages are in English, else German (decision E7)
        first = "en" if current() == "en" else "de"
        survey = Survey(title="", created_by_id=current_user.id, languages=[first], default_language=first)
        error = surveys.apply_project(survey, request.form)
        from app import study_plan

        plan = dict(survey.study_plan or {})  # the country starts from the language (DDI; Ralf 2026-10-27)
        plan["sample"] = {**(plan.get("sample") or {}), "country": study_plan.default_country(language=first)}
        survey.study_plan = plan
        study_plan.apply_aims(survey, request.form)  # research question and aims of the first step
        label = links.normalize(request.form.get("label"))
        if not error and label:
            error = links.label_error(label, current_user) or (
                None if request.form.get("confirm") == "1" else de_en("Das Label wurde nicht bestätigt.", "The label was not confirmed."))
        if error:
            flash(error, "error")
        else:
            db.session.add(survey)
            surveys._add_member(survey, current_user, current_user.name)
            from app.items.consent import ensure_consent_block

            if request.form.getlist("languages"):
                surveys.apply_languages(survey, request.form)
            ensure_consent_block(survey, current_user)
            # the ethics vote stays unticked: ticked, the consent would tell participants the study was reviewed by
            # an ethics committee, also without one (UI/UX audit X1, Ralf 2026-10-09; was ticked by Philipp 2026-10-04)
            # right after the consent "Demographische Angaben" with the standard questions age and gender from the
            # construct toolbox (the same items as inserted in the editor), or the bundle "Demografie kurz (RatSWD)"
            # as chosen on the page, then an empty "Block 1" for the questions
            from flask import current_app

            if current_app.config.get("NEW_SURVEY_DEMOGRAPHICS", True):
                chosen = request.form.get("demografie")
                add_demographics(survey, chosen if chosen in DEMOGRAPHIC_CHOICES else "basis")
                survey.blocks.append(Block(position=len(survey.blocks),
                                           titles={l: survey.next_block_title() for l in survey.languages}))
            else:  # tests: an empty content block, as before
                survey.blocks.append(Block(position=len(survey.blocks),
                                           titles={l: survey.next_block_title() for l in survey.languages}))
            db.session.flush()
            from app import flow

            flow.normalize(survey)
            if label:
                links.set_label(survey, label, current_user)
            # on to the research plan (optional, "Überspringen"), not past it (UI/UX audit 2026-10-05, V22)
            survey.setup_step = "studienplan"
            db.session.commit()
            return redirect(_step_url(survey, "studienplan"))
    # three ways to begin, as cards at the top of the page (UI/UX audit 2026-10-05, V25): empty, from a template, from a file
    start = request.args.get("start") if request.args.get("start") in STARTS else "leer"
    return render_template("wizard/step.html", survey=None, key="projekt", steps=steps(), index=0, starts=starts(), start=start,
                           form=request.form, projects=surveys._my_projects(), languages=surveys.LANGUAGE_NAMES,
                           values_languages=request.form.getlist("languages") or ["en" if current() == "en" else "de"],
                           lang_editable=True, demographics=demographic_choices())


# the cards of the new questionnaire: key: (title, what it does)
STARTS = {"leer": ("Leer beginnen", "Schritt für Schritt: Titel, Studienplan, Sprachen, Einwilligung"),
          "vorlage": ("Aus einer Vorlage", "typische Studien mit Studienplan, Fragebogen und Dummy-Daten"),
          "datei": ("Aus einer Datei", "eine aus Askurious exportierte Befragungsdatei (.json oder .zip) einlesen, kein Word oder PDF")}
STARTS_EN = {"leer": ("Start empty", "Step by step: title, research plan, languages, consent"),
             "vorlage": ("From a template", "typical designs with research plan, questionnaire and dummy data"),
             "datei": ("From a file", "read in a survey file exported from Askurious (.json or .zip), not Word or PDF")}


# the same three cards on the pages that make the other kinds (Ralf, 2026-10-12): {kind: {key: ((de), (en))}}
KIND_STARTS = {
    "live": {"leer": (("Leer beginnen", "Titel und Sprache, dann die erste Frage"),
                      ("Start empty", "title and language, then the first question")),
             "vorlage": (("Aus einer Vorlage", "fertige Abstimmungen: Feedback, unklarster Punkt, Vorwissen, Einstieg"),
                         ("From a template", "ready-made polls: feedback, muddiest point, prior knowledge, warm-up")),
             "datei": (("Aus einer Datei", "eine exportierte Live-Abstimmung (.json oder .zip) einlesen"),
                       ("From a file", "read in an exported live poll (.json or .zip)"))},
    "leitfaden": {"leer": (("Leer beginnen", "ein Themenblock, Kurzfragebogen und Postskript"),
                           ("Start empty", "one topic block, short questionnaire and postscript")),
                  "vorlage": (("Aus einer Vorlage", "problemzentriert, Experteninterview oder narrativ, mit Leitfragen"),
                              ("From a template", "problem-centred, expert or narrative, with guide questions")),
                  "datei": (("Aus einer Datei", "einen exportierten Leitfaden (.json oder .zip) einlesen"),
                            ("From a file", "read in an exported guide (.json or .zip)"))},
    "tagebuch": {"leer": (("Leer beginnen", "Titel und Sprache, eine Kurzbefragung „Moment“"),
                          ("Start empty", "title and language, one short survey “Moment”")),
                 "vorlage": (("Aus einer Vorlage", "feste Zeiten, zufällige Signale, Ereignis melden oder abends einmal"),
                             ("From a template", "fixed times, random signals, report an event or once in the evening")),
                 "datei": (("Aus einer Datei", "eine exportierte Tagebuchstudie (.json oder .zip) einlesen"),
                           ("From a file", "read in an exported diary survey (.json or .zip)"))},
}


def starts(kind=""):
    """The cards to begin with in the language of the page: of the new questionnaire, or of another kind
    (KIND_STARTS)."""
    if kind:
        return {k: pair[1 if english() else 0] for k, pair in KIND_STARTS[kind].items()}
    return STARTS_EN if english() else STARTS


def start_of():
    """The card chosen in the address (?start=), "leer" without one."""
    return request.args.get("start") if request.args.get("start") in STARTS else "leer"


# the pages a file can be read in from (?origin= on the form): the preview and a refusal lead back there
IMPORT_ORIGINS = {"live": ("live.new", "Neue Live-Abstimmung"), "leitfaden": ("interview.new", "Neuer Interviewleitfaden"),
                  "tagebuch": ("diary.new", "Neue Tagebuchstudie")}
IMPORT_ORIGINS_EN = {"live": "New live poll", "leitfaden": "New interview guide", "tagebuch": "New diary survey",
                     "": "New survey"}


def _import_origin():
    """The page the upload came from, for the preview: its key, the address to go back to and its name."""
    origin = request.args.get("origin") if request.args.get("origin") in IMPORT_ORIGINS else ""
    endpoint, label = IMPORT_ORIGINS.get(origin, ("wizard.new", "Neue Befragung"))
    if english():
        label = IMPORT_ORIGINS_EN[origin]
    back = url_for(endpoint, start="datei")  # the card "Aus einer Datei" of the page
    return {"origin": origin, "back": back + "#import", "back_label": label}


def _import_back():
    return _import_origin()["back"]


@bp.route("/new/import", methods=["POST"])
@login_required
def import_file():
    """New surveys from a file written by "Als Datei exportieren" (app/transfer.py, app/survey_files.py): a JSON
    file or a ZIP of one or several surveys. The upload is put aside and a preview shows what it holds; its form
    names the titles (a survey that is already here needs one of its own, as duplicating) and, of several,
    which to read in. Each becomes a draft whose admin is whoever reads it in."""
    from app import survey_files, transfer

    if not transfer.may(current_user):
        return redirect(url_for("wizard.new"))  # says why (or, for whoever creates no questionnaires, the dashboard)
    token, upload = request.form.get("token", ""), request.files.get("file")
    if not token and request.form.get("coscine_pfad"):  # a survey file chosen in Coscine (app/coscine.py, package G)
        from app import coscine

        try:
            raw = coscine.survey_file(request.form)
        except coscine.CoscineError as e:
            flash(str(e), "error")
            return redirect(_import_back())
        upload = None
    if token:  # the answer of the preview: the file was put aside
        raw = transfer.kept(current_user, token)
        if raw is None:
            flash(de_en("Die Datei liegt nicht mehr vor (nach einer Stunde wird sie verworfen). Bitte noch einmal auswählen.",
                        "The file is no longer there (it is discarded after an hour). Please choose it again."), "error")
            return redirect(_import_back())
    elif request.form.get("coscine_pfad"):
        pass  # read from Coscine above
    elif upload is None or not upload.filename:
        flash(de_en("Bitte eine Befragungsdatei auswählen.", "Please choose a survey file."), "error")
        return redirect(_import_back())
    elif not upload.filename.lower().endswith((".json", ".zip")):
        # pre-test 2026-10-16: a questionnaire from Word could not be chosen, and nothing said why
        flash(de_en("„{name}“ ist keine Befragungsdatei von Askurious. Eingelesen werden nur Dateien, die Askurious unter "
                    "„Als Datei exportieren“ schreibt (.json oder .zip). Einen Fragebogen aus Word, PDF oder Excel liest "
                    "Askurious nicht ein; seine Fragen lassen sich im Fragebogen anlegen.",
                    "“{name}” is not an Askurious survey file. Only files that Askurious writes under “Export as file” "
                    "(.json or .zip) can be read in. Askurious does not read a questionnaire from Word, PDF or Excel; "
                    "its questions can be created in the questionnaire.", name=upload.filename[:80]), "error")
        return redirect(_import_back())
    else:
        raw = upload.read()
    try:
        parts = survey_files.read(raw)
        # one survey file after the other: each is parsed for its summary and let go of again
        rows = [survey_files.summary(part, current_user) for part in parts]
    except transfer.Refused as e:
        transfer.forget(current_user, token)
        flash(str(e), "error")
        return redirect(_import_back())
    if not token:
        return render_template("wizard/import_preview.html", rows=rows, token=transfer.keep(current_user, raw),
                               form={}, errors={}, **_import_origin())

    chosen, errors, seen = [], {}, set()
    for n, (part, row) in enumerate(zip(parts, rows)):
        if len(parts) > 1 and not request.form.get(f"take-{n}"):
            continue
        title = transfer.title_of({}, request.form.get(f"title-{n}", "")) or row["title"]
        wanted = " ".join(title.split()).casefold()
        if title and transfer.twin_of(current_user, title) is not None:
            errors[n] = de_en("Auch diesen Titel hat schon eine Ihrer Befragungen. Die neue Befragung braucht einen anderen Titel, damit beide zu unterscheiden sind.",
                              "One of your surveys already has this title too. The new survey needs a different title so that the two can be told apart.")
        elif wanted in seen:
            errors[n] = de_en("Diesen Titel bekommt schon eine andere Befragung aus dieser Datei.",
                              "Another survey from this file already gets this title.")
        seen.add(wanted)
        chosen.append((part, title if title != row["title"] else ""))
    if not chosen:
        errors[-1] = de_en("Bitte mindestens eine Befragung zum Einlesen auswählen.", "Please choose at least one survey to read in.")
    if errors:
        return render_template("wizard/import_preview.html", rows=rows, token=token, form=request.form, errors=errors,
                               **_import_origin())

    made, budget = [], transfer.Budget()  # the images of all surveys of the upload together (transfer._Files)
    for part, title in chosen:
        try:
            made.append(transfer.create(part.load(), current_user, title, fetch=part.fetch, budget=budget))
        except transfer.Refused as e:
            flash(de_en("„{title}“: {e}", "“{title}”: {e}", title=title or rows[parts.index(part)]['title'], e=e)
                  if len(parts) > 1 else str(e), "error")
    transfer.forget(current_user, token)
    if not made:
        return redirect(_import_back())
    if len(made) == 1:
        flash(de_en("„{title}“ ist als Entwurf angelegt: Fragebogen und Einstellungen aus der Datei. Sie sind Admin der "
                    "Befragung; weitere Forschende und Gruppen fügen Sie in den Einstellungen hinzu.",
                    "“{title}” has been created as a draft: questionnaire and settings from the file. You are admin of the "
                    "survey; add further researchers and groups in the settings.", title=made[0][0].title), "success")
    else:
        flash(de_en("{n} Befragungen sind als Entwürfe angelegt: {titles}. Sie sind jeweils Admin; weitere Forschende und "
                    "Gruppen fügen Sie in den Einstellungen hinzu.",
                    "{n} surveys have been created as drafts: {titles}. You are admin of each; add further researchers and "
                    "groups in the settings.", n=len(made),
                    titles=", ".join(de_en("„{t}“", "“{t}”", t=s.title) for s, _ in made)), "success")
    notes = [(de_en("„{t}“: ", "“{t}”: ", t=s.title) if len(made) > 1 else "") + note for s, found in made for note in found]
    for note in notes[:12]:
        flash(note, "warning")
    if len(notes) > 12:
        flash(de_en("{n} weitere Hinweise dieser Art.", "{n} more notes of this kind.", n=len(notes) - 12), "warning")
    if len(made) == 1:
        return redirect(url_for("surveys.detail", survey_id=made[0][0].id))
    return redirect(url_for("dashboard.index"))


@bp.errorhandler(413)
def _too_large(error):
    if request.endpoint != "wizard.import_file":
        return error
    limit = current_app.config["IMPORT_MAX_BYTES"] // (1024 * 1024)
    flash(de_en("Die Datei ist größer als {limit} MB. Eine Befragung mit vielen Bildern lässt sich als ZIP-Datei exportieren (rund "
                "ein Viertel kleiner) oder ohne Bilder (Häkchen „Bilder und Logos einschließen“ entfernen); mehrere Befragungen "
                "einzeln.",
                "The file is larger than {limit} MB. A survey with many images can be exported as a ZIP file (about a "
                "quarter smaller) or without images (untick “Include images and logos”); several surveys one by one.",
                limit=limit), "error")
    return redirect(_import_back())


def _skipped(survey):
    """The steps of this survey left with "Überspringen" in this session."""
    return set((session.get("wizard_skipped") or {}).get(str(survey.id), []))


def _mark_skipped(survey, key, skipped):
    marks = dict(session.get("wizard_skipped") or {})
    keys = _skipped(survey) | {key} if skipped else _skipped(survey) - {key}
    marks[str(survey.id)] = sorted(keys)
    session["wizard_skipped"] = marks


@bp.route("/<sid:survey_id>/setup/<step:key>", methods=["GET", "POST"])
@login_required
@autosave.aware
def step(survey_id, key):
    from app import contact, image_styles, surveys

    survey = surveys.get_survey(survey_id)
    if survey.setup_step in FORMER_KEYS:  # a wizard left on one of the two former steps continues on the merged one
        survey.setup_step = FORMER_KEYS[survey.setup_step]
        db.session.commit()
    if key in FORMER_KEYS:
        return redirect(_step_url(survey, FORMER_KEYS[key]))
    if key not in KEYS:
        abort(404)
    prev_key, next_key = _neighbours(key)
    lang = survey.default_language

    if request.method == "POST" and autosave.wanted():
        # the page saves by itself, like the settings page: one step for "Rückgängig", the page stays where it is
        from app import undo

        if key == "fertig":
            return redirect(_step_url(survey, key))
        recorder = undo.begin(survey)
        error = _save(survey, key, request.form, lang)
        if error:
            db.session.rollback()
            flash(error, "error")
            return redirect(_step_url(survey, key))
        what = autosave.what()
        name = undo.both(STEPS[KEYS.index(key)][1], STEPS_EN[key][0])
        recorder.done(undo.both("Assistent, {name}: {what} geändert", "Assistant, {name}: {what} changed", name=name, what=what) if what
                      else undo.both("Assistent: {name} geändert", "Assistant: {name} changed", name=name),
                      page="settings", merge=autosave.merge(f"wizard:{key}"))
        db.session.commit()
        flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(_step_url(survey, key))

    if request.method == "POST":
        action = request.form.get("wizard", "next")
        if action in ("skip", "next"):
            _mark_skipped(survey, key, action == "skip")
        if action != "skip":
            error = _save(survey, key, request.form, lang)
            if error:
                flash(error, "error")
                return redirect(_step_url(survey, key))
        if action in ("draft_template", "draft_ai"):
            return redirect(_step_url(survey, key) + "#consent-text")
        target = prev_key if action == "back" else next_key
        if action != "back" and (key == "fertig" or target is None):
            survey.setup_step = None
            db.session.commit()
            return redirect(url_for("editor.index", survey_id=survey.id))
        # remember the furthest step reached (for "Einrichtung fortsetzen")
        if survey.setup_step and action != "back" and KEYS.index(target) > KEYS.index(survey.setup_step):
            survey.setup_step = target
        db.session.commit()
        return redirect(_step_url(survey, target))

    q = request.args.get("q", "").strip()
    return render_template(
        "wizard/step.html", survey=survey, key=key, steps=steps(), index=KEYS.index(key), form={},
        # steps up to the furthest one reached stay open for a visit, also after going back
        reached=KEYS.index(survey.setup_step) if survey.setup_step in KEYS else len(KEYS) - 1, skipped=_skipped(survey),
        projects=surveys._my_projects(), languages=surveys.LANGUAGE_NAMES, lang=lang,
        values=survey, values_languages=survey.languages, lang_editable=survey.is_editable,
        # as on the settings page: only admins of the survey add people, so only they search
        q=q, results=surveys._search_people(q, survey) if q and survey.can(current_user, "admin") else None,
        consent=survey.consent_item, image_styles=image_styles, style_cfg=image_styles.settings(survey),
        contact=contact, demographics=demographic_choices() if key == "studienplan" else None,
        demographics_now=demographics_kind(survey) if key == "studienplan" else None,
    )


def _save(survey, key, form, lang):
    from app import surveys

    if key == "projekt":
        error = surveys.apply_project(survey, form)
        if error:
            return error
        from app import study_plan

        study_plan.apply_aims(survey, form)  # research question and aims, nothing else of the plan
        if form.getlist("languages") or "default_language" in form:
            surveys.apply_languages(survey, form)
        return None
    if key == "studienplan":
        from app import study_plan

        study_plan.apply(survey, form)
        if form.get("demografie") in DEMOGRAPHIC_CHOICES + ("none",):
            set_demographics(survey, form.get("demografie"))
    if key == "team" and survey.consent_item is not None:
        # the responsible researchers, contact and funding are facts of the consent (items/consent.update_facts)
        item = survey.consent_item
        item.kind.apply_facts(item, form, lang)
    elif key == "ablauf":  # participation, and privacy and data quality where their fold was sent (wizard/_options.html)
        def change():
            surveys.apply_participation(survey, form)
            from app.surveys import base as survey_base

            if "privacy_shown" in form:
                survey_base.apply_privacy(survey, form)
            if "quality_shown" in form:
                survey_base.apply_quality_options(survey, form)

        surveys._with_consent(survey, change)
    elif key == "gestaltung":
        if form.get("preset"):
            ok, message = surveys.apply_theme_choice(survey, form.get("preset"))
            if not ok:
                return message
        if "key" in form:  # the image style (its cards send "key")
            surveys.apply_image_style(survey, form)
    elif key == "einwilligung" and survey.consent_item is not None:
        item = survey.consent_item
        problem = item.kind.facts_problem(form)
        if problem:
            return problem
        item.kind.apply_facts(item, form, lang)  # an untouched standard text follows the facts
        action = form.get("wizard")
        if action in ("draft_template", "draft_ai"):
            from app.editor import _draft_consent

            # saved before the AI is asked: an open write would keep the database locked for everyone (all
            # participants included) for as long as the answer takes
            db.session.commit()
            _draft_consent(item, lang, "template" if action == "draft_template" else "ai")
    return None
