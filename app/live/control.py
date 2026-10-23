"""Settings of a live session and the presenter's controls during a run."""
import time

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, settings_page
from app.emails import is_email
from app.extensions import db
from app.ids import db_id
from app.lang import de_en
from app.live.base import (OPTIONS, moderation_key, QUIZ_TYPES, REACTION_CHOICES, SLIDES_OPTIONS, _flatten, _flush_deleted,
                           _forget_responses, _responses, _run, _survey, answered_blocks, block_questions, blocks, bp,
                           by_blocks, clean_teams, lock_state, options, questions, reaction_choices, reaction_set,
                           standard_thanks, state)
from app.live.scores import _forget_feedback, _key, deadline
from app.models import Block, Survey


@bp.route("/<code>/settings", methods=["GET", "POST"])
@login_required
@autosave.aware
def settings(code):
    from app import edit_guard, surveys

    survey = _survey(code)
    if request.method == "GET":
        q = request.args.get("q", "").strip()  # co-owners invited by ORCID iD, as for a survey (surveys/_members.html)
        results = surveys._search_people(q, survey) if q and survey.can(current_user, "admin") else None
        return render_template("live/settings.html", survey=survey, languages=surveys.LANGUAGE_NAMES, q=q, results=results,
                               sums=settings_page.basic_summaries(survey),
                               options=options(survey),
                               emoji_choices=reaction_choices(), emojis=reaction_set(survey),
                               end_text=((survey.end_texts or {}).get(survey.default_language) or {}).get("thanks", ""),
                               live_thanks=standard_thanks(survey),
                               contact=state(survey).get("contact_email", ""), teams="\n".join(state(survey).get("teams") or []),
                               **_retention_view(survey))
    part = request.form.get("part", "")
    section = {"titel": "meta", "datenschutz": "meta", "aussehen": "theme", "teilnahme": "end_texts"}.get(part)
    if section is None:
        abort(400)
    # title and language are settings of the poll, as of a survey (surveys.update needs "admin"); the look and the
    # participation options stay with "Bearbeiten" (Abgleich 2026-10-15 AZ-62)
    survey = _survey(code, need="admin" if part in ("titel", "datenschutz") else "editor")
    conflict = surveys._settings_conflict(survey, section)
    back = redirect(url_for("live.settings", code=survey.code) + f"#{part}")
    if part == "titel":
        title = " ".join(request.form.get("title", "").split())[:300]
        if not title:
            db.session.rollback()
            flash(de_en("Der Titel darf nicht leer sein.", "The title must not be empty."), "error")
            return back
        survey.title = title  # no project field (Ralf, 2026-10-04: the groups tell who it belongs to)
        lang = request.form.get("language")
        if lang in surveys.LANGUAGE_NAMES and lang != survey.default_language:
            _switch_language(survey, lang)
    elif part == "datenschutz":  # the retention of the answers, counted from the last activity (retention.ended)
        from app import retention

        error = retention.order_error(survey, request.form)
        if error:
            db.session.rollback()
            flash(error, "error")
            return back
        if retention.from_form(survey, request.form):
            flash(de_en("Die Frist lässt sich nicht mehr verlängern, weil es schon echte Teilnahmen gibt. Kürzer geht jederzeit.",
                        "The period can no longer be extended because there are real participations already. Shorter is always possible."), "warning")
    elif part == "aussehen":
        ok, message = surveys.apply_theme_choice(survey, request.form.get("preset", ""))
        if not ok:
            db.session.rollback()
            flash(message, "error")
            return back
    else:
        lock_state(survey)  # the state is written back as a whole below (R7)
        teams = clean_teams(request.form.get("teams", "").splitlines())
        email = request.form.get("contact_email", "").strip()[:254]
        if email and not is_email(email):
            db.session.rollback()
            flash(de_en("Bitte eine gültige E-Mail-Adresse angeben oder das Feld leer lassen.",
                        "Please enter a valid email address or leave the field empty."), "error")
            return back
        if not survey.can(current_user, "admin"):
            # the contact for the privacy page and the closing text are what the admins answer for, as for a survey
            # (security audit 2026-10-04, H9): "Bearbeiten" keeps them as they are
            email = state(survey).get("contact_email", "")
        flow_choice = request.form.get("flow") if request.form.get("flow") in ("questions", "blocks") else state(survey).get("flow", "questions")
        # the slides on the phones are saved when the form carries "slides_shown" (always since 2026-10-17)
        # "multi" is set on each word cloud now (its element editor); the former tick stays as it was stored
        shown = [k for k in OPTIONS if k != "multi" and (k not in SLIDES_OPTIONS or request.form.get("slides_shown"))]
        survey.live = {**state(survey), **{k: bool(request.form.get(k)) for k in shown}, "contact_email": email, "flow": flow_choice, "teams": teams,
                       **({"emojis": [e for e in REACTION_CHOICES if e in request.form.getlist("emoji")]}
                          if request.form.get("emojis_shown") else {})}
        lang = survey.default_language
        if survey.can(current_user, "admin"):
            texts = dict(survey.end_texts or {})
            texts[lang] = {**(texts.get(lang) or {}), "thanks": request.form.get("end_text", "").strip()[:3000]}
            survey.end_texts = texts
    if conflict:
        return edit_guard.show_again(lambda: settings(survey.code))
    from app import undo

    surveys._settings_saved(survey, section, name={"titel": undo.both("Titel und Sprache", "Title and language"),
                                                   "aussehen": undo.both("Aussehen", "Appearance"),
                                                   "datenschutz": undo.both("Datenschutz und Aufbewahrung", "Data protection and retention"),
                                                   "teilnahme": undo.both("Teilnahme", "Participation")}[part])
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return back


def _retention_view(survey):
    """The group "Datenschutz und Aufbewahrung": the period for the answers, its choices and when it ends."""
    from app import retention

    periods = retention.of(survey)
    months = periods["answers"]
    return {"periods": periods, "retention_choices": retention.answers(), "retention_due": retention.due(survey)["answers"],
            "retention_label": (retention.label("answers", months) + de_en(" nach der letzten Teilnahme", " after the last participation"))
            if months else retention.label("answers", None)}


def _switch_language(survey, lang):
    """A live vote has one language; choosing another one moves the texts written so far over to it."""
    old = survey.default_language
    for block in survey.blocks:
        block.titles = {lang: (block.titles or {}).get(old, "")}
        for item in block.items:
            item.texts = {lang: (item.texts or {}).get(old) or item.kind.default_texts(lang)}
    if survey.end_texts and old in survey.end_texts:
        survey.end_texts = {lang: survey.end_texts[old]}
    survey.languages, survey.default_language = [lang], lang


# pages of a questionnaire that a live vote does not have (concept 6.3): where an old link or a typed address leads
REDIRECTS = {"surveys.detail": "live.settings", "surveys.field": "live.present", "flow_page.index": "live.manage",
             "flow_page.quotas": "live.manage", "translations.index": "live.manage", "surveys.quality": "live.manage",
             "boards.index": "live.manage"}


@bp.before_app_request
def _redirect_pages():
    target = REDIRECTS.get(request.endpoint or "")
    survey_id = (request.view_args or {}).get("survey_id")
    if target is None or survey_id is None or request.method != "GET":
        return None
    row = db.session.execute(db.select(Survey.code, Survey.live).filter_by(id=survey_id)).first()
    if row is not None and row.live is not None:
        return redirect(url_for(target, code=row.code))
    return None


@bp.route("/<code>/control", methods=["POST"])
@login_required
def control(code):
    """Put a question on the screen (or the next or previous one; the phones follow), results on or off, voting on
    the question on the screen closed or open, start from the beginning, start a new run."""
    survey = _survey(code, need="editor")
    action = request.form.get("action")
    if action == "restart" and not survey.can(current_user, "admin"):  # it deletes answers: admins (audit 2026-10-06, S22)
        message = de_en("Dafür fehlen die Rechte: „Alles zurück“ löscht die Antworten des Durchgangs; das darf nur die Rolle „Admin“.",
                        "You lack the rights for this: “Start over” deletes the answers of the run; only the role “Admin” may do that.")
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=False, error=message), 403
        flash(message, "error")
        return redirect(url_for("live.present", code=survey.code))
    if _flatten(survey):
        db.session.flush()
    lock_state(survey)
    st = state(survey)
    ids = [i.id for i in questions(survey)]
    first = ids[0] if ids else None
    started = answered_blocks(survey)
    gone_pids = set()  # participations deleted below (run_delete, restart)
    was_screen, was_block = st.get("screen"), st.get("block")
    if action in ("start", "new_run", "restart") and by_blocks(survey):  # "In Blöcken": the first block runs
        st["block"] = started[0] if started else None
    if action == "start" and survey.setup_step:  # "Präsentieren" at the end of the short wizard: the set-up is done
        survey.setup_step = None
    if action == "start":  # "Präsentieren": from the beginning: the first question, voting open
        st.update(screen=first, closed=[])
    elif action == "release":  # "In Blöcken": this block runs, the one before closes
        wanted = request.form.get("block", type=int)
        st["block"] = wanted if wanted in started else None
    elif action == "unrelease":
        st["block"] = None
    elif action == "end":  # "Live-Abstimmung beenden" (pre-test 2026-10-16): nothing on the screen, no block, voting on every
        # question closed; the phones show the closing text until the next start (any action that shows something)
        if not st.get("ended"):
            st["closed_before_end"] = list(st.get("closed") or [])
        st.update(screen=None, block=None, closed=list(ids), ended=True)
        st.pop("left", None)
        st.pop("left_block", None)
    elif action == "next_block":  # the block after the one running (the first if none); the last one stays; the screen
        if started:              # moves to its first question too (Philipp, 2026-10-04)
            at = started.index(st.get("block")) + 1 if st.get("block") in started else 0
            st["block"] = started[min(at, len(started) - 1)]
            block = next((b for b in blocks(survey) if b.id == st["block"]), None)
            first_of_block = block_questions(block)[0].id if block is not None and block_questions(block) else None
            if first_of_block:
                st["screen"] = first_of_block
    elif action == "add_block":
        survey.blocks.append(Block(position=len(survey.blocks), titles={survey.default_language: f"Block {len(survey.blocks) + 1}"}))
        db.session.flush()
        from app import flow

        flow.normalize(survey)
    elif action == "rename_block":
        block = next((b for b in survey.blocks if b.id == request.form.get("block", type=int)), None)
        title = " ".join(request.form.get("title", "").split())[:200]
        if block is not None and title:
            block.titles = {**(block.titles or {}), survey.default_language: title}
    elif action == "screen":
        wanted = request.form.get("item", type=int)
        st["screen"] = wanted if wanted in ids else None
    elif action in ("next", "prev"):  # the screen's buttons (and a presenter's clicker)
        if ids:
            at = ids.index(st["screen"]) if st["screen"] in ids else (-1 if action == "next" else len(ids))
            st["screen"] = ids[max(0, min(len(ids) - 1, at + (1 if action == "next" else -1)))]
    elif action in ("results_on", "results_off"):
        st["show"] = action == "results_on"
    elif action in ("ink_on", "ink_off"):  # the pen notes shown on the audience view or only in the presenter view
        st["ink_off"] = action == "ink_off"
    elif action in ("qr_on", "qr_off"):  # the QR code large in the middle, on every screen of the vote
        st["qr"] = action == "qr_on"
    elif action in ("close", "reopen"):  # voting on the question on the screen
        closed = [i for i in st["closed"] if i != st["screen"]]
        st["closed"] = closed + ([st["screen"]] if action == "close" and st["screen"] else [])
        item = next((i for i in questions(survey) if i.id == st["screen"]), None)
        if action == "reopen" and item is not None and deadline(st, item) and time.time() > deadline(st, item):
            st["opened"] = {**(st.get("opened") or {}), str(item.id): time.time()}  # its countdown runs out: anew
    elif action in ("hide", "approve", "unhide"):  # moderation of a short text (key: as texts() gives it)
        iid, key = str(request.form.get("item", type=int) or st["screen"] or ""), _key(request.form.get("key", ""))
        if iid in {str(i) for i in ids} and key:
            where = moderation_key(st["run"]["id"], iid)  # per run: a new run starts with nothing hidden
            hidden = {k: list(v) for k, v in (st.get("hidden") or {}).items()}
            approved = {k: list(v) for k, v in (st.get("approved") or {}).items()}
            hidden[where] = [k for k in hidden.get(where, []) if k != key] + ([key] if action == "hide" else [])
            approved[where] = [k for k in approved.get(where, []) if k != key] + ([key] if action == "approve" else [])
            st["hidden"], st["approved"] = hidden, approved
    elif action in ("qa_answer", "qa_hide", "qa_open"):  # a question from the audience: answered, hidden, open again
        from app.models import LiveQuestion

        row = db.session.get(LiveQuestion, request.form.get("qa", type=db_id) or 0)
        if row is not None and row.survey_id == survey.id:
            row.status = {"qa_answer": "answered", "qa_hide": "hidden", "qa_open": "open"}[action]
    elif action in ("qa_screen_on", "qa_screen_off"):  # the screen shows the questions from the audience
        st["qa_screen"], st["board_screen"], st["review_screen"] = action == "qa_screen_on", False, False
    elif action in ("board_on", "board_off"):  # the screen shows the leaderboard of the quiz
        st["board_screen"], st["qa_screen"], st["review_screen"] = action == "board_on", False, False
    elif action in ("review_on", "review_off"):  # the review at the end: questions and emojis per slide (review())
        st["review_screen"], st["qa_screen"], st["board_screen"] = action == "review_on", False, False
    elif action in ("corner_off", "corner_on"):  # the small QR code in the corner of the presentation slide on the screen
        off = [i for i in st.get("corner_off", []) if i != st["screen"]]
        st["corner_off"] = off + ([st["screen"]] if action == "corner_off" and st["screen"] else [])
    elif action == "review_sort":  # the review in the order of the slides, or the most "Unklar" first
        st["review_sort"] = "unclear" if request.form.get("by") == "unclear" else "slides"
    elif action == "notes_set":  # the presenter's notes of a question (only the presenter view shows them)
        item = next((i for i in questions(survey) if i.id == request.form.get("item", type=int)), None)
        if item is not None:
            notes = {k: v for k, v in (st.get("notes") or {}).items() if k != str(item.id)}
            text = request.form.get("notes", "").strip()[:3000]
            st["notes"] = {**notes, **({str(item.id): text} if text else {})}
    elif action == "quiz_set":  # a question as a quiz question (Fragen page): right answer, countdown
        item = next((i for i in questions(survey) if i.id == request.form.get("item", type=int)), None)
        if item is not None and item.type in QUIZ_TYPES:
            quiz = {k: v for k, v in (st.get("quiz") or {}).items() if k != str(item.id)}
            if request.form.get("on"):
                seconds = request.form.get("seconds", type=int)
                entry = {"seconds": max(5, min(seconds, 600)) if seconds else None}
                if item.type == "slider":
                    entry.update(value=request.form.get("value", type=float), tol=abs(request.form.get("tol", type=float) or 0))
                else:
                    codes = {o["code"] for o in item.kind.options(item, survey.default_language)}
                    entry["correct"] = [c for c in request.form.getlist("correct") if c in codes]
                quiz[str(item.id)] = entry
            st["quiz"] = quiz
    elif action in ("wall_on", "wall_off"):  # a short-text question as a wall of posts or as a word cloud
        wall = [i for i in st.get("wall", []) if i != st["screen"]]
        st["wall"] = wall + ([st["screen"]] if action == "wall_on" and st["screen"] else [])
    elif action == "run_delete":  # an earlier run with its answers and its questions from the audience (admins)
        run_id = request.form.get("run", type=int)
        if survey.can(current_user, "admin") and run_id in {r["id"] for r in st["runs"]}:
            from app.models import BrowserReceipt, LiveQuestion

            for r in _responses(survey, run_id):
                gone_pids.add(r.pid)
                db.session.delete(r)
            _flush_deleted("live_run")
            _forget_responses()
            db.session.execute(db.delete(LiveQuestion).where(LiveQuestion.survey_id == survey.id, LiveQuestion.run_id == run_id))
            db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == survey.id,
                                                              BrowserReceipt.scope == "live:" + str(run_id)))
            _forget_feedback(survey, run_id)
            st["runs"] = [r for r in st["runs"] if r["id"] != run_id]
    elif action == "run_merge":  # an earlier run goes into another one (Ralf, 2026-10-16; admins, it cannot be undone)
        run_id, into = request.form.get("run", type=int), request.form.get("into", type=int)
        if survey.can(current_user, "admin") and _merge_run(survey, st, run_id, into):
            flash(de_en("Durchgänge zusammengeführt.", "Runs merged."), "success")
        else:
            flash(de_en("Diese Durchgänge lassen sich nicht zusammenführen: nur Admins, und beide Durchgänge als Probe oder beide nicht.",
                        "These runs cannot be merged: admins only, and both runs trials or neither."), "error")
    elif action == "restart":  # "Alles zurück" (Philipp, 2026-10-04): the current run's answers, questions from the
        # audience and reactions are thrown away and the same run starts afresh (no new run); a new id lets the phones
        # begin anew, the run keeps its name
        from app.models import BrowserReceipt, LiveQuestion, LiveReaction

        for r in _responses(survey, st["run"]["id"]):
            gone_pids.add(r.pid)
            db.session.delete(r)
        _flush_deleted("live_restart")
        _forget_responses()
        db.session.execute(db.delete(LiveQuestion).where(LiveQuestion.survey_id == survey.id, LiveQuestion.run_id == st["run"]["id"]))
        db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == survey.id,
                                                          BrowserReceipt.scope == "live:" + str(st["run"]["id"])))
        db.session.execute(db.delete(LiveReaction).where(LiveReaction.survey_id == survey.id))
        _forget_feedback(survey, st["run"]["id"])
        st["run"] = {**st["run"], "id": _run()["id"]}
        st.update(screen=first, closed=[])
    elif action == "new_run":
        st["runs"] = st["runs"] + [st["run"]]
        st.update(run=_run(request.form.get("name", ""), [r["name"] for r in st["runs"] + [st["run"]]]), screen=first, closed=[])
        if request.form.get("test"):
            st["run"]["test"] = True
    elif action == "run_test":  # a run marked as a trial ("Probe", T-23): its participations are test ones
        run_id, on = request.form.get("run", type=int), bool(request.form.get("on"))
        if run_id == st["run"]["id"]:
            st["run"] = {**st["run"], "test": on}
        elif run_id in {r["id"] for r in st["runs"]}:
            st["runs"] = [{**r, "test": on} if r["id"] == run_id else r for r in st["runs"]]
        else:
            run_id = None
        if run_id is not None:
            for r in _responses(survey, run_id):
                r.source = "test" if on else "real"
    # an answer on its way while the presenter moves on is still taken for a few seconds (T-16): the question (or
    # block) just left, unless voting on it was ended on purpose (close, unrelease) or everything starts anew
    now = time.time()
    if action in ("next", "prev", "screen", "next_block") and was_screen and st.get("screen") != was_screen:
        st["left"] = {"id": was_screen, "at": now}
    if action in ("release", "next_block") and was_block and st.get("block") != was_block:
        st["left_block"] = {"id": was_block, "at": now, "start": st.get("block_at")}  # its start: the quiz clocks (S21)
    if action in ("start", "new_run", "restart", "unrelease"):
        st.pop("left_block", None)
    if st.get("block") and (st.get("block") != was_block or action in ("start", "new_run", "restart")):
        st["block_at"] = now  # a block starts: the countdowns of its quiz questions at the latest from here (T-22)
    if action in ("start", "new_run", "restart"):
        st.pop("left", None)
        st["opened"] = {}  # the quiz clocks start anew
        st["started_at"] = time.time()  # the presenter view's clock
    if action in ("next", "prev", "screen", "start", "new_run", "restart"):
        st["qa_screen"] = st["board_screen"] = st["review_screen"] = False  # back to the slides
        if st.get("screen") and str(st["screen"]) not in (st.get("opened") or {}):  # when a question first comes up
            st["opened"] = {**(st.get("opened") or {}), str(st["screen"]): time.time()}
    if action in ("start", "new_run", "restart"):
        # presenting starts with the results open, or covered by the ghost (Einstellungen -> Teilnahme); turning the
        # slides keeps them shown when results are open, and covers them again when not
        st["show"] = options(survey)["results_open"]
    elif not options(survey)["results_open"] and (st.get("screen") != was_screen or st.get("block") != was_block):
        st["show"] = False  # without "Ergebnisse sofort zeigen" every new slide starts behind the ghost (Ralf, 2026-10-23)
    if action != "end" and st.get("ended") and (st.get("screen") or st.get("block")):
        st.pop("ended", None)  # shown again: the vote goes on, with the questions open that were open before
        before = st.pop("closed_before_end", [])
        if action not in ("start", "new_run", "restart"):
            st["closed"] = [i for i in before if i in ids]
    st["at"] = time.time()  # the participants' pages notice every change
    survey.live = st
    db.session.commit()
    if gone_pids:  # kept results and states of the dashboards counted them too (audit 2026-10-05, I-16)
        from app import erasure

        try:
            erasure._forget_in_files(survey, {p for p in gone_pids if p})
        except OSError as e:
            from flask import current_app

            current_app.logger.error("Live vote %s: files of deleted answers not cleaned: %s", survey.code, e)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, state=st)
    if action == "start":  # "Präsentieren": the presenter view on the own laptop; it opens the screen for the projector
        return redirect(url_for("live.presenter", code=survey.code))
    if action in ("quiz_set", "notes_set"):
        return redirect(url_for("live.manage", code=survey.code, item=request.form.get("item", type=int)))
    return redirect(url_for("live.present" if action in ("new_run", "run_delete", "run_merge", "restart", "run_test", "end") else "live.manage",
                            code=survey.code))


def _merge_ink(survey, run_id, into):
    """The pen notes of a merged run go to the target; on a slide that has notes there too, its strokes come after
    the target's (stroke ids are random, so none collides)."""
    from app.models import LiveInk

    there = {r.item_id: r for r in db.session.scalars(db.select(LiveInk).filter_by(survey_id=survey.id, run_id=into))}
    for row in db.session.scalars(db.select(LiveInk).filter_by(survey_id=survey.id, run_id=run_id)).all():
        if row.item_id in there:
            target = there[row.item_id]
            target.strokes = list(target.strokes or []) + list(row.strokes or [])
            target.rev = (target.rev or 0) + 1
            db.session.delete(row)
        else:
            row.run_id = into
    db.session.flush()


def _merge_run(survey, st, run_id, into):
    """Merge an earlier run into another one (the current one too): its participations, questions from the audience,
    the emojis per slide and the marks of the devices go to the target, the earlier run goes from the list. Both runs must
    be trials or both not. A device that voted in both keeps both participations. Each participation keeps the run it
    was first given (live_run_origin, live_run_origin_name), and so does each question from the audience
    (origin_run_id): the exports still show it. Nothing brings the earlier run back."""
    from app.models import BrowserReceipt, LiveFeedback, LiveQuestion

    source = next((r for r in st["runs"] if r["id"] == run_id), None)
    every = st["runs"] + [st["run"]]
    target = next((r for r in every if r["id"] == into and into != run_id), None)
    if source is None or target is None or bool(source.get("test")) != bool(target.get("test")):
        return False
    for r in _responses(survey, run_id):
        params = dict(r.params or {})
        params.setdefault("live_run_origin", params.get("live_run", run_id))
        params.setdefault("live_run_origin_name", params.get("live_run_name", source["name"]))
        r.params = {**params, "live_run": target["id"], "live_run_name": target["name"]}
    _forget_responses()
    db.session.execute(db.update(LiveQuestion).where(LiveQuestion.survey_id == survey.id, LiveQuestion.run_id == run_id,
                                                     LiveQuestion.origin_run_id.is_(None)).values(origin_run_id=run_id))
    db.session.execute(db.update(LiveQuestion).where(LiveQuestion.survey_id == survey.id, LiveQuestion.run_id == run_id)
                       .values(run_id=target["id"]))
    db.session.execute(db.update(LiveFeedback).where(LiveFeedback.survey_id == survey.id, LiveFeedback.run_id == run_id)
                       .values(run_id=target["id"]))
    _merge_ink(survey, run_id, target["id"])
    # a device's marks of the earlier run: under the target's scope, unless it has one there already (the key is
    # part of the primary key)
    old, new = "live:" + str(run_id), "live:" + str(target["id"])
    there = db.select(BrowserReceipt.key).where(BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope == new)
    db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope == old,
                                                      BrowserReceipt.key.in_(there)))
    db.session.execute(db.update(BrowserReceipt).where(BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope == old)
                       .values(scope=new))
    started = min((s for s in (source.get("started"), target.get("started")) if s), default="")
    merged = {**target, "started": started}
    if target["id"] == st["run"]["id"]:
        st["run"] = merged
    st["runs"] = [merged if r["id"] == target["id"] else r for r in st["runs"] if r["id"] != run_id]
    st["merged"] = {**(st.get("merged") or {}), str(run_id): source["name"]}  # names of the runs gone, for the export
    return True
