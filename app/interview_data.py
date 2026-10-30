"""Interviews of an interview guide ("Interviews erheben", Ralf 2026-10-04, regular since 2026-10-05; concept
interviewleitfaden-konzept.md, version 2 sections 3.4 and 4.3, now built on version 3).

An interview is a Response of the guide, with Response.interview (label, interviewer, date, mode, consent, times,
time marks). Per Leitfrage the answer holds what the interview view shows:
  answers[item id] = {"state": "asked" | "spontaneous" | "skipped", "aspects": [row id], "probes": [row id], "note": text,
                      "fits": {row id: bool}}
("fits": "trifft zu" of a concrete question with Nachfragen, concept bedingungen-leitfaden B.)
The Kurzfragebogen and the Postskript are stored like the answers of a questionnaire (runtime.evaluate), the tasks
of the Themenblöcke too, with the clock when each result was first given (app/interview_tasks.py).

The interview view saves by itself (interview.js); a short loss of the connection is bridged by the copy in the browser
tab, which is sent again. Deletion on request (by the researchers, by identifier or deletion code, app/erasure.py)
and the retention period of the guide's settings ("data_keep", counted from the end of each interview) apply.
Everyone with "Bearbeiten" or "Admin" on the guide sees it (until 2026-10-05 an experimental feature); an interviewer
with a personal link (app/interviewer.py) sees and continues only the interviews of their own access."""
import math
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import whisper_jobs
from app.extensions import db, json_given
from app.interviewer import interviewer_or_signed_in
from app.lang import de_en, english
from app.models import Response, new_pid, utcnow

bp = Blueprint("interview_data", __name__, url_prefix="/guide")

PREFIXES = {"I": "I (Interview)", "E": "E (Expertin, Experte)", "P": "P (Person)", "B": "B (befragte Person)"}
CONSENT_FORMS = {"schriftlich": "schriftlich (unterschrieben)", "muendlich": "mündlich, auf der Aufnahme"}
PREFIXES_EN = {"I": "I (interview)", "E": "E (expert)", "P": "P (person)", "B": "B (interviewee)"}
CONSENT_FORMS_EN = {"schriftlich": "in writing (signed)", "muendlich": "verbal, on the recording"}


def prefixes():
    """PREFIXES in the language of the page."""
    return PREFIXES_EN if english() else PREFIXES


def consent_forms():
    """CONSENT_FORMS in the language of the page."""
    return CONSENT_FORMS_EN if english() else CONSENT_FORMS


def consent_of(form, audio):
    """The consent of a new interview: its form, the recording, and when "everybody who speaks" consented. That is the
    one tick "Einwilligung aller Sprechenden liegt vor" (Ralf 2026-10-15: one tick instead of two), which covers the
    interviewee and, for a recording, the interviewers and anybody else who speaks (§ 201 StGB; concept
    konzept-online-interviews-2026-10-15.md, 4a)."""
    consent = {"form": form.get("consent_form") if form.get("consent_form") in CONSENT_FORMS else "schriftlich", "audio": bool(audio)}
    if form.get("consent"):
        consent["all"] = utcnow().isoformat(timespec="seconds")
    return consent


MAX_NOTE = 2000
MAX_GENERAL_NOTE = 5000  # the note on the whole interview, beside the notes per Leitfrage (Ralf 2026-10-30)
MAX_MARKS = 300
STATES = ("asked", "spontaneous", "skipped")


def _survey(code, need="viewer"):
    from app import interview

    return interview._survey(code, need=need)


def interviews(survey, source=None):
    query = db.select(Response).filter(Response.survey_id == survey.id, Response.interview.is_not(None), Response.is_preview.is_(False))
    if source:
        query = query.filter(Response.source == source)
    return db.session.scalars(query.order_by(Response.id)).all()


def _interview(survey, rid):
    row = db.session.get(Response, rid)
    if row is None or row.survey_id != survey.id or row.interview is None:
        abort(404)
    return row


FORM_PARTS = ("short", "post")  # the Kurzfragebogen and the Postskript


def form_items(survey, part=None):
    """The elements of the Kurzfragebogen and the Postskript the guide uses; part: those of one of them only."""
    from app import interview

    return [i for which in FORM_PARTS if part in (None, which) and interview.shown_form(survey, which) is not None
            for i in interview.shown_form(survey, which).items]


def next_label(survey):
    prefix = (survey.interview or {}).get("prefix") if (survey.interview or {}).get("prefix") in PREFIXES else "I"
    from app import interview_offline

    # identifiers reserved for a field pack (app/interview_offline.py) are taken too
    taken = {(r.interview or {}).get("label") for r in interviews(survey)} | interview_offline.reserved_labels(survey)
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


def guide_items(survey):
    from app import interview

    return {str(i.id): i for b in interview.guide_blocks(survey) for i in b.items if i.type == "guide_question"}


def record(survey, row):
    """What the interview view needs of a stored interview (and sends back in the same form, see save)."""
    items = guide_items(survey)
    states, ticks, notes, fits = {}, [], {}, {}
    for key, item in items.items():
        a = (row.answers or {}).get(key) or {}
        for rid, fit in (a.get("fits") or {}).items():
            fits[f"{key}:{rid}"] = "yes" if fit else "no"
        if a.get("state") in STATES:
            states[key] = a["state"]
        ticks += [f"{key}:aspects:{rid}" for rid in a.get("aspects") or []]
        ticks += [f"{key}:probe:{rid}" for rid in a.get("probes") or []]
        if a.get("note"):
            notes[key] = a["note"]
    meta = row.interview or {}
    return {"id": row.id, "states": states, "ticks": ticks, "fits": fits, "notes": notes, "note": meta.get("note") or "",
            "marks": meta.get("marks") or [],
            "elapsed": int(meta.get("elapsed") or 0) * 1000, "blocks": {k: int(v) * 1000 for k, v in (meta.get("blocks") or {}).items()},
            "done": row.status == Response.COMPLETED}


def _finite(value):
    """A number that int() can take (not NaN or infinite)."""
    return isinstance(value, (int, float)) and math.isfinite(value)


def save(survey, row, payload):
    """Store what the interview view sent: states, ticks, notes, the note on the whole interview, time marks and the
    clock. Without "note" (a page or offline copy from before 2026-10-30) the stored note stays."""
    if not isinstance(payload, dict):
        abort(400)
    items = guide_items(survey)
    ticks = {t for t in payload.get("ticks") or [] if isinstance(t, str)}
    states = payload.get("states") if isinstance(payload.get("states"), dict) else {}
    notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    fits = payload.get("fits") if isinstance(payload.get("fits"), dict) else {}
    answers = dict(row.answers or {})
    for key, item in items.items():
        aspects = [rid for rid in (item.config or {}).get("aspects", []) if f"{key}:aspects:{rid}" in ticks]
        probes = [rid for rid in (item.config or {}).get("probe", []) if f"{key}:probe:{rid}" in ticks]
        note = str(notes.get(key) or "")[:MAX_NOTE].strip()
        state = states.get(key) if states.get(key) in STATES else None
        fit = {rid: fits[f"{key}:{rid}"] == "yes" for rid in (item.config or {}).get("probe", [])
               if fits.get(f"{key}:{rid}") in ("yes", "no")}
        if state or aspects or probes or note or fit:
            answers[key] = {"state": state, "aspects": aspects, "probes": probes, "note": note, **({"fits": fit} if fit else {})}
        else:
            answers.pop(key, None)
    marks = []
    for m in (payload.get("marks") or [])[:MAX_MARKS]:
        if isinstance(m, dict) and isinstance(m.get("t"), (int, float)) and 0 <= m["t"] < 86400:
            marks.append({"t": int(m["t"]), "item": str(m.get("item") or "") if str(m.get("item") or "") in items else "",
                          "text": " ".join(str(m.get("text") or "").split())[:300]})
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), dict) else {}
    elapsed = payload.get("elapsed") if _finite(payload.get("elapsed")) else 0
    row.answers = answers
    row.interview = {**(row.interview or {}), "marks": sorted(marks, key=lambda m: m["t"]),
                     "elapsed": max(0, min(int(elapsed // 1000), 86400)),
                     "blocks": {str(k): max(0, min(int(v // 1000), 86400)) for k, v in blocks.items()
                                if str(k).isdigit() and _finite(v)},
                     # when the clock was sent and whether it ran: the recording device starts its part from it
                     "clock_at": utcnow().isoformat(timespec="seconds"), "running": bool(payload.get("running"))}
    if "note" in payload:
        note = str(payload.get("note") or "")[:MAX_GENERAL_NOTE].strip()
        if note:
            row.interview["note"] = note
        else:
            row.interview.pop("note", None)
    row.last_active_at = utcnow()


def missing_required(survey, row, part=None, mode="required"):
    """{item id: message} of the required questions of the Kurzfragebogen (or Postskript, part) still without an
    answer, as the participant runtime checks them: a question hidden by its condition does not count. A save keeps
    what is given (it saves while typing); "Weiter" and "Interview beenden" hold here (pretest 2026-10-22).
    mode="nudge": the questions set to "Nachfragen", marked once on "Weiter", a second click goes on."""
    from app.runtime import load_answers, present

    answers = load_answers(row)
    items = [i for i in form_items(survey, part) if i.kind.collects_answer and i.answer_mode == mode]
    text = (de_en("Pflichtfrage: bitte beantworten.", "Required question: please answer.") if mode == "required" else
            de_en("Diese Frage ist noch offen. „Weiter“ noch einmal geht ohne Antwort weiter.",
                  "This question is still open. “Next” again continues without an answer."))
    return {i.id: text for i in present(items, answers) if i.kind.is_missing(i, answers.get(i.id))}


def save_forms(survey, row, form, by="interviewer"):
    """The Kurzfragebogen and the Postskript as the participant forms send them; a required question may stay empty
    here, since the form saves while it is filled in (missing_required says what is still open). Each
    form sends only its own fields and names itself ("part"): only its elements are read, an answer missing from it
    is one left empty there; the other form's answers stay (code review 2026-10-07, G1). `by`: who gave a task's
    result, "interviewer" (the interview view) or "screen" (the interviewee's own device, app/interview_screen.py);
    never read from the form."""
    from app import interview_tasks
    from app.runtime import _AsOptional, evaluate

    task = None
    if form.get("part") == "task":  # one task of a Themenblock (app/interview_tasks.py), the same way
        task = next((i for i in interview_tasks.tasks(survey) if str(i.id) == form.get("task", "")), None)
        if task is None:
            return {}
        items = [task]
    else:
        part = form.get("part") if form.get("part") in FORM_PARTS else None
        items = [i for i in form_items(survey, part) if i.kind.collects_answer]
    result = evaluate([_AsOptional(i) for i in items], form, survey.default_language)
    answers = dict(row.answers or {})
    for item in items:
        if item.id in result.answers and result.answers[item.id] not in (None, "", [], {}):
            answers[str(item.id)] = result.answers[item.id]
        elif item.id not in result.errors:
            answers.pop(str(item.id), None)
    if task is None and form.get("part") == "short" and by == "interviewer":
        from app import interview_screen

        interview_screen.note_short(row, "interviewer")  # answers from the screen changed in the view (E2)
    if task is not None and task.id not in result.errors:  # when it was first given: the line in the transcript
        seconds = form.get("t", "")
        interview_tasks.note_time(row, task, str(task.id) in answers, int(seconds) if seconds.isascii() and seconds.isdigit() else None)
        interview_tasks.note_by(row, task, by)
    row.answers = answers
    row.last_active_at = utcnow()
    return result.errors


def form_answers(survey, row):
    """{item id (int): answer} of the forms and the tasks, for the participant templates."""
    from app import interview_tasks

    ids = {i.id for i in form_items(survey)} | {i.id for i in interview_tasks.tasks(survey)}
    return {int(k): v for k, v in (row.answers or {}).items() if k.isdigit() and int(k) in ids}


# --- retention ---------------------------------------------------------------------------------------------------


OPEN_DAYS = 3  # an interview not ended and untouched for this long is marked in the list ("nicht beendet")


def due(survey, row):
    """When the interview goes by the guide's period (data_keep, from the end of the interview), or None. An interview
    never ended (tab closed, battery empty, forgotten) counts from its last activity (data audit L-3)."""
    from app import interview, interview_audio
    from app.retention import _add_months

    keep = interview.state(survey)["data_keep"]
    end = interview_audio._end(row)
    if keep == "end" or end is None:
        return None
    return _add_months(end, int(keep))


def left_open(row, now=None):
    """Whether the interview was never ended and nobody worked on it for OPEN_DAYS days: end or delete it."""
    last = row.last_active_at or row.started_at
    return row.status != Response.COMPLETED and last is not None and last <= (now or utcnow()) - timedelta(days=OPEN_DAYS)


def run(now=None):
    """Delete the interviews whose period is over (lifecycle.close_due_surveys). Counted like a deletion on request."""
    from app import erasure
    from app.models import Survey

    from app import interview_offline

    now, gone = now or utcnow(), 0
    interview_offline.run(now)  # field packs past their seven days, with their places never synced
    for survey in db.session.scalars(db.select(Survey).filter(json_given(Survey.interview))).all():
        over = [r for r in interviews(survey) if (due(survey, r) or now + timedelta(days=1)) <= now]
        if over:
            gone += erasure.erase(survey, over, "retention")
    return gone


# --- the pages ---------------------------------------------------------------------------------------------------


@bp.route("/<code>/interviews")
@login_required
def index(code):
    from app import ai_access, erasure, interview, interview_offline, interview_transcribe, interviewer

    survey = _survey(code, need="editor")
    rows = interviews(survey)
    held_by = request.args.get("by", "")  # the filter "geführt von": "" all, "team", or an access's id
    if held_by == "team":
        rows = [r for r in rows if not (r.interview or {}).get("access")]
    elif held_by.isdigit():
        rows = [r for r in rows if (r.interview or {}).get("access") == int(held_by)]
    return render_template("interview/interviews.html", survey=survey, rows=rows, st=interview.state(survey),
                           accesses=interviewer.accesses(survey), held_by=held_by,
                           new_link=interviewer.show_once(survey), access_state=interviewer.state,
                           access_counts=interviewer.interview_count(survey), link_days=interviewer.DAYS,
                           default_days=interviewer.DEFAULT_DAYS, default_devices=interviewer.DEFAULT_DEVICES,
                           max_devices=interviewer.MAX_DEVICES, max_batch=interviewer.MAX_BATCH,
                           modes=interview.modes(), forms=consent_forms(), label=next_label(survey), due=due, left_open=left_open,
                           day=interview.day_text,
                           whisper_models=whisper_jobs.MODELS, whisper_default=whisper_jobs.default_model(current_app.config),
                           today=f"{utcnow():%Y-%m-%d}", may_edit=survey.can(current_user, "editor"),
                           may_delete=survey.can(current_user, "admin"), erasures=erasure.summary(survey),
                           whisper=interview_transcribe.configured() and ai_access.ai_on(), transcribing=interview_transcribe.running,
                           transcription_progress=interview_transcribe.progress_info,
                           transcription_error=interview_transcribe.error_info, offline=interview_offline.panel(survey))


@bp.route("/<code>/interviews/new", methods=["POST"])
@login_required
def new(code):
    """A new interview after the consent step: no interview begins without the tick (concept I10)."""
    from app import interview

    survey = _survey(code, need="editor")
    back = redirect(url_for("interview_data.index", code=survey.code))
    if not request.form.get("consent"):
        flash(de_en("Ohne Einwilligung beginnt kein Interview: bitte „Einwilligung aller Sprechenden liegt vor“ ankreuzen.",
                    "No interview begins without consent: please tick “Consent of everyone who speaks given”."), "error")
        return back
    label = " ".join(request.form.get("label", "").split())[:20] or next_label(survey)
    from app import interview_offline

    if label in {(r.interview or {}).get("label") for r in interviews(survey)} | interview_offline.reserved_labels(survey):
        flash(de_en("Die Kennung „{label}“ ist schon vergeben.", "The identifier “{label}” is already taken.", label=label), "error")
        return back
    date = request.form.get("date", "")[:10]
    mode = request.form.get("mode") if request.form.get("mode") in interview.MODES else interview.state(survey)["mode"]
    row = Response(survey_id=survey.id, language=survey.default_language, source="test" if request.form.get("trial") else "real",
                   status=Response.ACTIVE, answers={}, pid=new_pid(survey.code), started_at=utcnow(), last_active_at=utcnow(),
                   interview={"label": label, "by": current_user.id, "by_name": current_user.name or "", "date": date,
                              "mode": mode, "consent": consent_of(request.form, request.form.get("audio")),
                              "marks": [], "elapsed": 0, "blocks": {}})
    if not survey.erase_code:  # interviews get a deletion code to hand over (app/erasure.py)
        survey.erase_code = True
    db.session.add(row)
    db.session.commit()
    return redirect(url_for("interview.conduct", code=survey.code, interview=row.id))


@bp.route("/<code>/interviews/<int:rid>/save", methods=["POST"])
@interviewer_or_signed_in
def save_view(code, rid):
    from app import interviewer

    survey, row = interviewer.row_for(code, rid)
    from app import interview_audio
    from app.timeutil import to_local

    payload = request.get_json(silent=True)
    interview_audio.retried(lambda: save(survey, row, payload))  # a piece of the recording stored meanwhile

    return jsonify(ok=True, saved=f"{to_local(utcnow()):%H:%M:%S}")


@bp.route("/<code>/interviews/<int:rid>/form", methods=["POST"])
@interviewer_or_signed_in
def save_form(code, rid):
    from app import interviewer

    survey, row = interviewer.row_for(code, rid)
    from app import interview_audio

    errors = interview_audio.retried(lambda: save_forms(survey, row, request.form))
    part = request.form.get("part") if request.form.get("part") in FORM_PARTS else None
    # a task (part "task") has no required check here: its own card asks for a result
    task = request.form.get("part") == "task"
    missing = {} if task else missing_required(survey, row, part)
    nudges = {} if task else missing_required(survey, row, part, mode="nudge")
    return jsonify(ok=True, errors={str(k): v for k, v in errors.items()},
                   missing={str(k): v for k, v in missing.items() if k not in errors},
                   nudges={str(k): v for k, v in nudges.items() if k not in errors})


@bp.route("/<code>/interviews/<int:rid>/end", methods=["POST"])
@interviewer_or_signed_in
def finish(code, rid):
    """End of the interview: completed; the page with the deletion code to hand over."""
    from app import erasure, interviewer

    survey, row = interviewer.row_for(code, rid)
    if row.status != Response.COMPLETED:
        from app import interview_screen

        # like a page of a questionnaire: no end while a required question of the Kurzfragebogen is open; the
        # Postskript may still be written afterwards (under "Interviews"), so it does not hold the end
        if missing_required(survey, row, "short"):
            flash(de_en("Im Kurzfragebogen sind noch Pflichtfragen offen. Bitte zuerst beantworten.",
                        "Required questions of the short questionnaire are still open. Please answer them first."), "error")
            return redirect(url_for("interview.conduct", code=survey.code, interview=row.id))

        interview_screen.disconnect(row)  # the interviewee's screen ends with the interview
        row.status, row.completed_at = Response.COMPLETED, utcnow()
        db.session.commit()
    return render_template("interview/finished.html", survey=survey, row=row, code_=erasure.code_for(survey, row))


@bp.route("/<code>/interviews/<int:rid>/resume", methods=["POST"])
@interviewer_or_signed_in
def reopen(code, rid):
    from app import interviewer

    survey, row = interviewer.row_for(code, rid)
    row.status, row.completed_at = Response.ACTIVE, None
    db.session.commit()
    return redirect(url_for("interview.conduct", code=survey.code, interview=row.id))


@bp.route("/<code>/interviews/<int:rid>/delete", methods=["POST"])
@login_required
def delete(code, rid):
    from app import erasure

    survey = _survey(code, need="admin")
    row = _interview(survey, rid)
    label = (row.interview or {}).get("label", "")
    erasure.erase(survey, [row], "request")
    flash(de_en("Interview {label} gelöscht. Eine Tonaufnahme außerhalb von Askurious bitte ebenfalls löschen.",
                "Interview {label} deleted. Please also delete any audio recording outside Askurious.", label=label), "success")
    return redirect(url_for("interview_data.index", code=survey.code))


@bp.route("/<code>/interviews/deletion-code", methods=["POST"])
@login_required
def delete_by_code(code):
    """A request by deletion code or identifier, as the interviewed person sends it."""
    from app import erasure

    survey = _survey(code, need="admin")
    back = redirect(url_for("interview.export", code=survey.code) + "#loeschen")
    term = request.form.get("term", "")
    found = [r for r in erasure.find(survey, term)[0] if r.interview is not None]
    found += [r for r in interviews(survey) if (r.interview or {}).get("label") == " ".join(term.split()) and r not in found]
    if not found:
        from app import interview_offline

        if interview_offline.erase_pending(term, survey) is not None:  # a place of a field pack not synced yet
            flash(de_en("Das Interview ist noch auf dem Gerät und nicht abgeglichen: Es ist zum Löschen vorgemerkt und "
                        "kommt nicht auf den Server; das Gerät löscht seine Kopie beim nächsten Abgleich.",
                        "The interview is still on the device and not synced: it is marked for deletion and will not "
                        "reach the server; the device deletes its copy at the next sync."), "success")
            return back
        flash(de_en("Kein Interview mit diesem Löschcode oder dieser Kennung gefunden.",
                    "No interview found with this deletion code or identifier."), "error")
        return back
    labels = ", ".join((r.interview or {}).get("label", "") for r in found)
    erasure.erase(survey, found, "request")
    flash(de_en("Gelöscht: {labels}.", "Deleted: {labels}.", labels=labels), "success")
    return back
