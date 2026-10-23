"""Participant runtime at /s/<uid>.

Anonymous by design: the only thing stored in the browser is a signed session cookie with
the response id (technically necessary to continue a started questionnaire). No IP address,
no user agent, no fingerprinting. The page logic is runtime.step, shared with the preview.
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, g, make_response, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import RequestEntityTooLarge

from app import runtime
from app.extensions import csrf, db
from app.models import Response, Survey, new_pid, utcnow

bp = Blueprint("participate", __name__, url_prefix="/s")

DEFAULT_END_TEXTS = {
    "de": {
        "thanks": "**Vielen Dank für Ihre Teilnahme!**\nIhre Antworten wurden gespeichert. Sie können dieses Fenster jetzt schließen.",
        "declined": "**Schade, dass Sie nicht teilnehmen möchten.**\nEs wurden keine Antworten gespeichert. Sie können dieses Fenster jetzt schließen.",
        "closed": "**Diese Befragung ist derzeit nicht geöffnet.**\nVielen Dank für Ihr Interesse.",
        "ended": "**Diese Befragung ist beendet.**\nEine Teilnahme ist nicht mehr möglich. Vielen Dank für Ihr Interesse.",
        "again": "Doch teilnehmen",
        "next_person": "Neue Teilnahme an diesem Gerät",
        "screenout": "**Vielen Dank für Ihr Interesse!**\nDiese Befragung richtet sich leider an eine andere Zielgruppe. "
                     "Sie können dieses Fenster jetzt schließen.",
        "quotafull": "**Vielen Dank für Ihr Interesse!**\nFür Ihre Gruppe haben wir bereits genügend Teilnahmen. "
                     "Sie können dieses Fenster jetzt schließen.",
        "excluded": "**Vielen Dank für Ihre Teilnahme.**\nDie Befragung endet für Sie an dieser Stelle. "
                    "Sie können dieses Fenster jetzt schließen.",
        "badlink": "**Dieser Link ist unvollständig.**\nBitte öffnen Sie die Befragung über den Link, den Sie erhalten haben.",
        "used": "**Mit diesem Link wurde bereits teilgenommen.**\nEine weitere Teilnahme ist nicht möglich. "
                "Vielen Dank für Ihr Interesse.",
        "repeat": "**In diesem Browser wurde bereits teilgenommen.**\nEine weitere Teilnahme ist nicht möglich. "
                  "Vielen Dank für Ihr Interesse.",
        "busy": "**Gerade starten sehr viele Teilnahmen.**\nBitte versuchen Sie es in ein paar Minuten noch einmal.",
    },
    "en": {
        "thanks": "**Thank you for taking part!**\nYour answers have been saved. You can close this window now.",
        "declined": "**We are sorry you do not want to take part.**\nNo answers were stored. You can close this window now.",
        "closed": "**This survey is currently not open.**\nThank you for your interest.",
        "ended": "**This survey has ended.**\nIt is no longer possible to take part. Thank you for your interest.",
        "again": "Take part after all",
        "next_person": "New participation on this device",
        "screenout": "**Thank you for your interest!**\nUnfortunately, this survey is aimed at a different group. "
                     "You can close this window now.",
        "quotafull": "**Thank you for your interest!**\nWe already have enough participants from your group. "
                     "You can close this window now.",
        "excluded": "**Thank you for taking part.**\nThe survey ends here for you. You can close this window now.",
        "badlink": "**This link is incomplete.**\nPlease open the survey with the link you received.",
        "used": "**This link has already been used to take part.**\nIt is not possible to take part again. "
                "Thank you for your interest.",
        "repeat": "**This browser has already been used to take part.**\nIt is not possible to take part again. "
                  "Thank you for your interest.",
        "busy": "**Very many participations are starting right now.**\nPlease try again in a few minutes.",
    },
}


def end_text(survey, lang, key):
    custom = ((survey.end_texts or {}).get(lang) or {}).get(key)
    if custom:
        return custom
    from app.ui_i18n import tr

    return tr(lang, DEFAULT_END_TEXTS["de"][key], DEFAULT_END_TEXTS["en"][key])


def closed_text(survey, lang):
    """What someone sees who opens the link of a survey that takes no new participants: the researchers' own
    text if there is one; else "currently not open" for a draft or a pause and "has ended" once it is closed."""
    if survey.start_at is not None and survey.status != Survey.RUNNING:
        # its data collection goes on at a planned moment (lifecycle.plan_start): that moment, before any own text
        from app.timeutil import to_local
        from app.ui_i18n import date_text, tr

        at = to_local(survey.start_at)
        resumes = tr(lang, "Diese Befragung wird am {date} um {time} Uhr fortgesetzt.", "This survey resumes on {date} at {time}.",
                     date=date_text(at.date(), lang), time=at.strftime("%H:%M"))
        return f"**{resumes}**\n" + tr(lang, "Vielen Dank für Ihr Interesse.", "Thank you for your interest.")
    custom = ((survey.end_texts or {}).get(lang) or {}).get("closed")
    over = (survey.status == Survey.CLOSED and survey.close_reason != "paused") or _past_field_end(survey)
    return custom or end_text(survey, lang, "ended" if over else "closed")


def end_node(survey, response):
    """The end in the flow where this participation stopped (screen-out, quota full, exclusion), or None: an end, or a
    "Quotenprüfung" that was full, in a survey started with one (its text and link are those of an end "Quote voll")."""
    if response.status not in Response.ENDED_EARLY or not response.layout:
        return None
    from app import flow

    pages = response.layout.get("pages") or []
    position = response.position or 0
    spec = pages[position] if 0 <= position < len(pages) else {}
    nid = spec.get("end") or spec.get("quota")
    node, _ = flow.find(flow.tree(survey), nid) if nid else (None, None)
    if node is not None and node["t"] == "quota" and response.status != Response.QUOTAFULL:
        return None  # nobody's cell at a "Quotenprüfung": a plain screen-out with the standard text and link
    return node


def finished_text(survey, response, lang, with_code=False):
    """Text for the end of a response: thanks, declined, or the end in the flow (its own text, else the
    standard text of its kind: screen-out or quota full). `with_code`: the page shows a deletion code below the text;
    the standard thanks then do not say yet that the window can be closed, the line under the code does (X45)."""
    from app import diary

    if response.status == Response.COMPLETED and diary.short_entry(response):  # a short survey of a diary study
        from app.ui_i18n import tr

        return tr(lang, "**Danke!**\nIhr Eintrag ist gespeichert.", "**Thank you!**\nYour entry has been saved.")
    if response.status == Response.COMPLETED and diary.entry_survey(response):  # the study only begins now
        from app.ui_i18n import tr

        if (survey.sampling or {}).get("start") != "next_day":  # the default: at once (diary.defaults)
            return tr(lang, "**Danke, die Eingangsbefragung ist geschafft!**\nDie Kurzbefragungen kommen ab heute in der App.",
                      "**Thank you, the entry survey is done!**\nThe short surveys come in the app from today.")
        return tr(lang, "**Danke, die Eingangsbefragung ist geschafft!**\nDie Kurzbefragungen kommen ab morgen in der App.",
                  "**Thank you, the entry survey is done!**\nThe short surveys come in the app from tomorrow.")
    if with_code and response.status not in (Response.DECLINED, *Response.ENDED_EARLY) \
            and not ((survey.end_texts or {}).get(lang) or {}).get("thanks"):
        from app.ui_i18n import tr

        return tr(lang, "**Vielen Dank für Ihre Teilnahme!**\nIhre Antworten wurden gespeichert.",
                  "**Thank you for taking part!**\nYour answers have been saved.")
    if response.status == Response.DECLINED:
        return end_text(survey, lang, "declined")
    if response.status in Response.ENDED_EARLY:
        from app import flow, quotas

        node = end_node(survey, response) or {}
        if node.get("t") == "end":  # the text of the trigger that ended the participation
            node = flow.case_for(node, quotas.view(survey, response)) or {}
        custom = (node.get("text") or {}).get(lang)
        return custom or end_text(survey, lang, response.status)
    return end_text(survey, lang, "thanks")


def leave(survey, outcome, values, node=None, view=None):
    """(redirect response or None, what the end page needs to offer the completion link). The link is the one
    the admins of the survey stored (panel.target); "now" redirects at once, otherwise the end page shows its
    text first, with a button, and with "auto" moves on by itself. view: what link rules may test; without
    it, the URL parameters among the values."""
    from app import panel

    if view is None:
        view = {f"p:{k}": v for k, v in values.items()}
    url, mode = panel.target(survey, outcome, values, node, view)
    if url and mode == "now":
        return redirect(url), {}
    return None, {"forward": url, "forward_after": panel.AUTO_SECONDS if url and mode == "auto" else None}


def _commit_keeping_loaded():
    """Commit without expiring what the session has loaded (Session.expire_on_commit for this one commit)."""
    sess = db.session()
    keep = sess.expire_on_commit
    sess.expire_on_commit = False
    try:
        sess.commit()
    finally:
        sess.expire_on_commit = keep


def _survey(uid):
    """The survey of a participant address, for every page under /s/<uid>. A code that leads nowhere counts as a wrong
    code of the sender (limits.allow_code_try, as on the code page /live/); once there are too many, these addresses
    answer 429 for a while to a browser that holds nothing of the survey (a participation under way in a class
    behind the same address goes on). A draft that never started shows a neutral page: no title, no people, no
    contact address (S19 of the audit 2026-10-06), except to its researchers and to the test persons of a pretest
    link. A survey opened for editing again after its start is no such draft: its participants may go on, look up
    the privacy page and delete their answers."""
    from app import limits, links

    blocked = not limits.allow_code_try(count=False)
    survey = links.resolve(uid)  # label or code
    if survey is None:
        if blocked:
            abort(make_response(_error_page("codes", 429)))
        limits.allow_code_try()
        abort(404)
    if blocked and not any(key(survey) in session for key in (_session_key, _pending_key, _declined_key, _pid_key)):
        abort(make_response(_error_page("codes", 429)))
    if survey.interview is not None:  # an interview guide has no participant link (app/interview.py)
        abort(404)
    if survey.start_at is not None and survey.start_at <= utcnow() and survey.status != Survey.RUNNING:
        survey = _start_if_due(survey)
    if survey.status == Survey.DRAFT and survey.opened_at is None and survey.live is None and not _may_see_draft(survey):
        # a planned start (lifecycle.plan_start): when it begins, and still nothing else of the survey (S19)
        abort(make_response(_waiting_page(survey) if survey.start_at else _error_page("draft", 200)))
    return survey


START_TRY_SECONDS = 60  # per process and survey: how often a participant's request may try a planned start
_start_tried = {}  # survey id -> time.monotonic() of the last try in this process


def _start_if_due(survey):
    """The planned start has come, and the housekeeping (at most once a minute) has not run yet: it runs now for this
    one survey, so that whoever opens the link at 8:00 sharp does not read "begins at 8:00". At most once in
    START_TRY_SECONDS per process and survey: a start that fails is not tried again in every participant's request;
    the housekeeping tries again (audit 2026-10-10, T3). A busy database leaves it to the next round."""
    import time

    from sqlalchemy.orm.exc import StaleDataError

    from app import lifecycle

    survey_id = survey.id
    now = time.monotonic()
    if now - _start_tried.get(survey_id, -START_TRY_SECONDS) < START_TRY_SECONDS:
        return survey
    if len(_start_tried) > 1000:  # only recent tries matter
        _start_tried.clear()
    _start_tried[survey_id] = now
    try:
        lifecycle.start_due_surveys(survey_id=survey_id)
    except (OperationalError, StaleDataError) as e:
        db.session.rollback()
        current_app.logger.warning("Planned start put off: %s", str(e).splitlines()[0])
    return db.session.get(Survey, survey_id) or survey


RELEASE_TRY_SECONDS = 30  # per process and survey: how often turning someone away at the door gives back places
_release_tried = {}  # survey id -> time.monotonic() of the last try in this process


def _release_due(survey):
    """Whether a visit turned away at the door may first give back the places of people who left long ago
    (quotas.release_expired). At most once in RELEASE_TRY_SECONDS per process and survey: a full cell with a panel
    or a crawler behind it would otherwise read all running participations of the survey on every visit; the
    housekeeping does the same once a minute anyway (code review 2026-10-15, F3)."""
    import time

    now = time.monotonic()
    if now - _release_tried.get(survey.id, -RELEASE_TRY_SECONDS) < RELEASE_TRY_SECONDS:
        return False
    if len(_release_tried) > 1000:  # only recent tries matter
        _release_tried.clear()
    _release_tried[survey.id] = now
    return True


def _waiting_page(survey):
    """Before a planned start: only the date and time it begins, in a language of the survey; no title, no people, no
    contact address (as the page of a draft, S19)."""
    from app.timeutil import to_local
    from app.ui_i18n import date_text, tr

    lang = _pick_language(survey)
    at = to_local(survey.start_at)
    heading = tr(lang, "Diese Befragung beginnt am {date} um {time} Uhr.", "This survey begins on {date} at {time}.",
                 date=date_text(at.date(), lang), time=at.strftime("%H:%M"))
    return render_template("participant/error.html", survey=None, lang=lang, heading=heading,
                           text=tr(lang, "Vielen Dank für Ihr Interesse.", "Thank you for your interest."), retry=None,
                           retry_label=""), 200


PRETEST_KEY = "pv-drafts"  # surveys whose pretest link this browser opened (app/editor/share.py preview_share.run)


def _may_see_draft(survey):
    """Its researchers, and whoever opened a pretest or phone link of it in this browser (the footer of the preview
    leads to the privacy page). On the participants' own address no sign-in is read: there the researchers' right
    comes along with the redirect from the main address (app/hosts.py, E6)."""
    from flask_login import current_user

    from app.hosts import on_participant_side

    if not on_participant_side() and current_user.is_authenticated and survey.can(current_user, "viewer"):
        return True
    return survey.id in (session.get(PRETEST_KEY) or [])


# What people see instead of a bare error page (errors below). "retry" opens the survey link again.
ERROR_TEXTS = {
    "de": {
        "session": ("Ihre Sitzung konnte nicht fortgesetzt werden.",
                    "Das passiert, wenn die Seite sehr lange geöffnet war oder wenn Ihr Browser Cookies blockiert. "
                    "Die Befragung braucht ein technisch notwendiges Cookie, das nur Ihren Fortschritt festhält. "
                    "Bitte erlauben Sie Cookies für diese Seite und laden Sie die Befragung neu.",
                    # what happened to the answers, so nobody thinks all is lost (UI/UX audit 2026-10-16, 106)
                    "Ihre bisherigen Antworten sind gespeichert. Diese Teilnahme lässt sich leider nicht weiterführen; "
                    "„Befragung neu laden“ beginnt von vorn."),
        "missing": ("Diese Befragung wurde nicht gefunden.",
                    "Bitte prüfen Sie den Link, den Sie erhalten haben."),
        "busy": ("Der Server ist gerade stark ausgelastet.",
                 "Ihre Angaben auf der letzten Seite konnten noch nicht gespeichert werden; alles davor ist gespeichert. "
                 "Bitte senden Sie die Seite gleich noch einmal. Ihr Browser fragt dabei nach, ob die Angaben erneut "
                 "gesendet werden sollen: bitte bestätigen."),
        "toolarge": ("Diese Seite konnte nicht angenommen werden.",
                     "Es wurde mehr gesendet, als eine Seite der Befragung enthalten kann. Bitte laden Sie die Befragung "
                     "neu; Ihre bisherigen Antworten sind gespeichert."),
        "codes": ("Zu viele unbekannte Links.",
                  "Von Ihrer Verbindung wurden in kurzer Zeit viele Links geöffnet, die zu keiner Befragung führen. Bitte "
                  "versuchen Sie es in einigen Minuten noch einmal."),
        "draft": ("Diese Befragung ist derzeit nicht geöffnet.", "Vielen Dank für Ihr Interesse."),
        "retry": "Befragung neu laden",
        "resend": "Seite erneut senden",
    },
    "en": {
        "session": ("Your session could not be continued.",
                    "This happens when the page was open for a very long time or when your browser blocks cookies. "
                    "The survey needs one technically necessary cookie that only keeps track of your progress. "
                    "Please allow cookies for this page and reload the survey.",
                    "Your answers so far are saved. Unfortunately, this participation cannot be continued; “Reload the "
                    "survey” starts again from the beginning."),
        "missing": ("This survey was not found.",
                    "Please check the link you received."),
        "busy": ("The server is very busy right now.",
                 "Your answers on the last page could not be saved yet; everything before is saved. Please send the "
                 "page again in a moment. Your browser will ask whether to send the answers again: please confirm."),
        "toolarge": ("This page could not be accepted.",
                     "More was sent than a page of the survey can hold. Please reload the survey; your answers so far "
                     "are saved."),
        "codes": ("Too many unknown links.",
                  "Many links that lead to no survey were opened from your connection in a short time. Please try "
                  "again in a few minutes."),
        "draft": ("This survey is currently not open.", "Thank you for your interest."),
        "retry": "Reload the survey",
        "resend": "Send the page again",
    },
}


def _error_page(kind, status):
    """A participant-styled page for errors that would otherwise show the web server's bare text."""
    from app import links

    uid = (request.view_args or {}).get("uid")
    # nothing of the survey on the page for an unknown code, too many of them or a draft (S19)
    survey = links.resolve(uid) if uid and kind not in ("missing", "codes", "draft") else None
    from app.surveys import LANGUAGE_NAMES
    from app.ui_i18n import tr

    if survey is not None:
        lang = _pick_language(survey)
    else:
        lang = request.accept_languages.best_match(list(LANGUAGE_NAMES)) or "de"
    texts = {k: (tuple(tr(lang, d, e) for d, e in zip(v, ERROR_TEXTS["en"][k])) if isinstance(v, tuple)
                 else tr(lang, v, ERROR_TEXTS["en"][k])) for k, v in ERROR_TEXTS["de"].items()}
    retry = url_for("participate.page", uid=uid) if survey is not None else None
    # a page that was sent and not stored: the button sends it again (reloading repeats the request with its
    # form); without JavaScript the browser's own reload does the same
    resend = texts["resend"] if kind == "busy" and request.method == "POST" else None
    return render_template("participant/error.html", survey=survey, lang=lang, heading=texts[kind][0],
                           text=" ".join(texts[kind][1:]), retry=retry, retry_label=texts["retry"], resend=resend), status


@bp.errorhandler(CSRFError)
def _session_lost(e):
    # no session cookie came back with the page: blocked cookies, or the session ran out in an open tab
    return _error_page("session", 400)


@bp.errorhandler(404)
def _not_found(e):
    return _error_page("missing", 404)


@bp.errorhandler(413)
def _too_large(e):
    # more than a page of a questionnaire can hold (config PARTICIPANT_FORM_BYTES): never sent by our pages
    return _error_page("toolarge", 413)


@bp.before_request
def _remember_session():
    """What the session cookie held when the request came in, for _database_busy."""
    from flask import g

    g.session_before = dict(session)


def _sent_twice(e):
    """Another request of the same participation stored its page while this one was at work (a page sent twice):
    nothing of this one is kept, also no quota place it took, and the browser gets the page the participation
    is on now. For the whole app (create_app): also the preview and the live pages of the phones (a double tap).
    The address keeps its query (the token of a return link); a request of a script to an address that only takes
    POST (the phones of a live vote) learns it as JSON, and the phone asks for its state again."""
    db.session.rollback()
    rule = request.url_rule
    if request.method != "GET" and rule is not None and "GET" not in (rule.methods or ()):
        from flask import jsonify

        return jsonify(ok=False, error="stale"), 409
    return redirect(request.url, code=303)


@bp.errorhandler(OperationalError)
def _database_busy(e):
    """The database could not take the page (locked for longer than it waits, or not reachable): nothing of this
    request is kept, also not in the cookie. A participation id noted there would point at a row that was
    never stored, and the database gives that id to the next person. The page asks to send again."""
    from flask import g

    db.session.rollback()
    g.pop("direct_page", None)  # this page is sent again by reloading it (error.html)
    before = g.get("session_before")
    if before is not None:
        session.clear()
        session.update(before)
    current_app.logger.warning("Participant page not stored: %s", str(e).splitlines()[0])
    try:
        body, status = _error_page("busy", 503)
    except OperationalError:  # not even the survey can be read: the plain text of the page
        db.session.rollback()
        lang = request.accept_languages.best_match(["de", "en"]) or "de"
        body, status = " ".join(ERROR_TEXTS[lang]["busy"]), 503
    return body, status, {"Retry-After": "10"}


# what keeps its line breaks and indentation: text fields, preformatted text and the passage of a highlighting task
# (white-space: pre-wrap, participant.css .p-hl-text)
_KEEP_SPACE = None


def trim_html(html):
    """The page without the indentation of its templates: every run of white space that holds a line break becomes
    one line break. A browser shows the page the same (white space between and inside elements collapses to one
    space), except inside the parts in _KEEP_SPACE, which stay as they are. About a sixth less to load (block-load
    concept 2026-10-15, stage c); the pages are not compressed (no gzip for pages: BREACH, nginx)."""
    import re

    global _KEEP_SPACE
    if _KEEP_SPACE is None:
        _KEEP_SPACE = re.compile(r'(<textarea\b.*?</textarea>|<pre\b.*?</pre>|<p class="p-hl-text".*?</p>)', re.S | re.I)
    parts = _KEEP_SPACE.split(html)
    for n in range(0, len(parts), 2):  # the parts between the kept ones
        parts[n] = re.sub(r"[ \t]*\n\s*", "\n", parts[n])
    return "".join(parts)


@bp.after_request
def _trim_page(response):
    """The questionnaire pages leave without the indentation of their templates (trim_html)."""
    if (request.endpoint == "participate.page" and response.mimetype == "text/html" and not response.direct_passthrough
            and response.status_code in (200, 400)):
        response.set_data(trim_html(response.get_data(as_text=True)))
    return response


def _pick_language(survey, response=None):
    # ?lang= from a link (set by the researchers, also without language_choice); "set_lang" when the switch on a page sends the page along (keeps what was typed)
    # Without language_choice the link decides the language a participation starts in: once it is stored, its
    # language stays (?lang= typed into the address would switch it in the middle)
    stored = response is not None and response.id is not None
    wanted = request.args.get("lang") if survey.language_choice or not stored else None
    if not wanted and survey.language_choice:  # without the choice there is no switch, and the form is not heeded
        try:
            wanted = request.form.get("set_lang")
        except RequestEntityTooLarge:  # the page that says so (_too_large) picks its language without the form
            wanted = None
    if wanted in survey.languages:
        return wanted
    if response is not None and response.language in survey.languages:
        return response.language
    return browser_language(survey)


def browser_language(survey):
    """The survey language the browser prefers. Its settings know German, not the address: German means "Sie"
    if the survey has it, else "du"."""
    langs = survey.languages
    offered = [l for l in langs if l != "de-du"] + (["de"] if "de-du" in langs and "de" not in langs else [])
    best = request.accept_languages.best_match(offered)
    if best == "de" and "de" not in langs:
        best = "de-du"
    return best or survey.default_language


def _session_key(survey):
    return f"r{survey.id}"


def _declined_key(survey):
    return f"d{survey.id}"


def _pending_key(survey):
    return f"n{survey.id}"


def _pid_key(survey):
    return f"rp{survey.id}"


def _returned_key(survey):
    return f"xr{survey.id}"


def _stale_key(survey):
    return f"st{survey.id}"


def _relayout_key(survey):
    return f"rl{survey.id}"


STALE_NOTE = ("Diese Seite war schon abgeschickt; Sie sind jetzt hier.", "This page had already been sent; you are now here.")


def _relayout_note(lang):
    """The questionnaire was changed since this participation began (a new layout, runtime.ensure_layout): it starts
    again from the first page, and the page says why."""
    from app.ui_i18n import tr

    return tr(lang, "Diese Befragung wurde inzwischen geändert. Sie beginnen deshalb wieder auf der ersten Seite; "
                    "Ihre bisherigen Antworten sind, soweit die Fragen noch vorkommen, schon eingetragen.",
              "This survey has been changed in the meantime, so you are starting again from the first page; your "
              "previous answers are already filled in where the questions are still there.")


def _halted_page(survey, lang, kept=None):
    """A participation under way while its survey is open for editing again (a draft after "Zur Bearbeitung öffnen"):
    nothing is shown until the survey runs again; its answers stay, and it goes on then. kept: a page was sent
    (_keep_halted), True if its valid answers were kept, False if not. The page keeps what the tab holds of the
    inputs (data-keep-inputs, participant.js): the person goes on later on the same page (audit data storage
    2026-10-21, A2)."""
    from app.ui_i18n import tr

    if kept is False:
        text = tr(lang, "**Diese Befragung ist gerade nicht geöffnet.**\nIhre Antworten auf den Seiten davor bleiben "
                        "gespeichert; die Angaben auf dieser Seite konnten nicht gespeichert werden. Bitte versuchen "
                        "Sie es später erneut.",
                  "**This survey is not open at the moment.**\nYour answers on the previous pages remain saved; the "
                  "answers on this page could not be saved. Please try again later.")
    elif kept:
        text = tr(lang, "**Diese Befragung ist gerade nicht geöffnet.**\nIhre bisherigen Antworten bleiben "
                        "gespeichert, auch die gültigen Angaben auf dieser Seite. Bitte versuchen Sie es später erneut.",
                  "**This survey is not open at the moment.**\nYour answers so far remain saved, including the valid "
                  "answers on this page. Please try again later.")
    else:
        text = tr(lang, "**Diese Befragung ist gerade nicht geöffnet.**\nIhre bisherigen Antworten bleiben gespeichert. "
                        "Bitte versuchen Sie es später erneut.",
                  "**This survey is not open at the moment.**\nYour answers so far remain saved. Please try again later.")
    return render_template("participant/end.html", survey=survey, lang=lang, text=text, status="closed",
                           waiting=True), 200


def _keep_halted(survey, response, lang, form_lang):
    """A page sent while its survey is open for editing again: its valid answers are kept as "Back" keeps them,
    without moving on, as long as the questionnaire is still the one the page belongs to (runtime.layout_matches).
    Once it was changed the page is dropped: the person starts again on the changed questionnaire (T-7). True if
    the answers were kept (audit data storage 2026-10-21, A2)."""
    from app import panel

    db.session.rollback()  # nothing of this request before the page itself
    if not runtime.layout_matches(survey, response.layout):
        return False
    position, status = response.position, response.status
    result = runtime.step(survey, response, request.form, lang, "POST", params=panel.values(survey),
                          ext_barrier=_returned(survey, response), form_lang=form_lang, hold=True)
    if result.stale or result.finished or result.external or response.position != position \
            or response.status != status:
        # an older page, or not a plain page of the questionnaire (an external section, an entry that no longer
        # applies): nothing of it is kept
        db.session.rollback()
        return False
    db.session.commit()
    return True


def _returned(survey, response):
    """The external sections this browser came back from through a return link without having left from them (the
    web app opened in another browser, or someone else opened the link): this browser holds the participation from
    there on only. "Back" stops at them, and the privacy page offers neither "delete here" nor the deletion code
    (runtime.previous_index, privacy, erase). Empty for the browser that took part from the start."""
    kept = session.get(_returned_key(survey))
    if response is None or not isinstance(kept, dict) or kept.get("pid") != response.pid:
        return ()
    return tuple(n for n in kept.get("nodes") or [] if isinstance(n, str))


def _new_response(survey, lang, pending, stored=False):
    """A participation built from the pending values in the session; the same seed and the same counts of
    balanced assignment give the same layout on the first page (GET, not stored) and when it is stored on the
    first submit (POST). The counts of the first draw are added to `pending` ("bal"): others who start in between
    must not change the condition this person was shown."""
    import random

    from app import balance, panel

    # column defaults only apply on insert, so an unsaved participation needs them set explicitly
    response = Response(survey_id=survey.id, language=lang, answers={}, qversion=survey.qversion,
                        params=panel.stored_values(survey, panel.values(survey)) or None, pid=pending["pid"],
                        started_at=datetime.fromisoformat(pending["at"]), status=Response.ACTIVE, position=0,
                        source="real", is_preview=False)
    from app import browser_receipts

    # stored=True on the first submit: only then is the start counted for its network (audit 2026-10-15, T02-NEU-1)
    # a seed in the cookie (participations opened before #985) stays there until the first submit uses it
    assigned = browser_receipts.assignment(survey, pending["pid"], pending.get("seed"), count=stored)
    response.paradata = {"_assignment_network_starts": assigned["earlier"]}
    with balance.frozen(survey, pending.get("bal")) as table:
        runtime.ensure_layout(survey, response, random.Random(assigned["seed"]))
    if pending.get("bal") is None:
        pending["bal"] = balance.keep(table, response.layout, survey)
    return response


RESTART_SECONDS = 3600  # the unsaved first page opened again after this long: the participation begins anew


def _restart_due(pending):
    """Was the unsaved first page first opened more than RESTART_SECONDS ago (decision 4 of the audit 2026-10-12)?
    A start that cannot be read counts as due."""
    try:
        first = datetime.fromisoformat(pending.get("at") or "")
    except (TypeError, ValueError):
        return True
    return (utcnow() - first).total_seconds() > RESTART_SECONDS


def _shown_position(response, pending):
    """The position of the page the unsaved participation showed (kept in the session), if the layout has it."""
    position = pending.get("pos")
    pages = (response.layout or {}).get("pages") or []
    return position if isinstance(position, int) and 0 <= position < len(pages) else 0


def _current_response(survey):
    rid = session.get(_session_key(survey))
    response = db.session.get(Response, rid) if rid else None
    if response is None or response.survey_id != survey.id or response.is_preview:
        return None
    # the row id alone does not name a participation for good: a declined one is deleted, and the database gives
    # its id to the next person. The pid noted with it must fit (cookies from before it was noted have none)
    known = session.get(_pid_key(survey))
    if known and response.pid != known:
        return None
    if response.enrolment_id:
        from app import diary_access

        if not diary_access.current(survey, response):
            return None
    return response


def _past_field_end(survey):
    """The field time is over, though the survey is not closed yet (closing runs at most once a minute)."""
    return survey.status == Survey.RUNNING and survey.field_end is not None and survey.field_end <= utcnow()


def _accepting(survey):
    """New participations may start."""
    return survey.status == Survey.RUNNING and not _past_field_end(survey)


def _too_late(survey):
    """A participation begun before the survey stopped taking new ones may no longer go on: the survey is archived,
    or it closed (field end, "Beenden", a pause) more than CLOSE_GRACE_HOURS ago. Within that time people who started
    shortly before the end still finish; later, finishing would change data that were exported and analysed already,
    and a script could prepare many participations before the end and complete them weeks later (audit 2026-10-18,
    BIZ-04). Such a participation stays "begonnen" and shows the page "Befragung beendet"; opened again (a survey
    reopened), it goes on."""
    if survey.status == Survey.DRAFT:
        return False  # opened for editing: the participation waits (_halted_page)
    if survey.archived:
        return True
    if _accepting(survey):
        return False
    hours = current_app.config.get("CLOSE_GRACE_HOURS", 24)
    since = survey.closed_at if survey.status == Survey.CLOSED else survey.field_end
    if hours is None or hours < 0 or since is None:
        return False
    return utcnow() - since > timedelta(hours=hours)


def _too_late_page(survey, lang):
    """The friendly end for a participation that may no longer go on (_too_late): nothing of this request is
    stored."""
    db.session.rollback()
    return render_template("participant/end.html", survey=survey, lang=lang, text=closed_text(survey, lang),
                           status="closed"), 200


def _stored_first_page(survey, pending):
    """The participation the pending values of this browser were stored as already, or None."""
    pid = (pending or {}).get("pid")
    if not pid:
        return None
    return db.session.scalar(db.select(Response).filter_by(survey_id=survey.id, pid=pid, is_preview=False))


def _resume_stored(survey, response, uid):
    """This browser goes on with the participation its first page was stored as: the page it is on now."""
    db.session.rollback()  # nothing of this request not committed yet is kept (see browser_receipts.assignment)
    session.pop(_pending_key(survey), None)
    session[_session_key(survey)] = response.id
    session[_pid_key(survey)] = response.pid
    return redirect(url_for("participate.page", uid=uid), code=303)


KEEP_ENDED = 10  # ended participations of other surveys whose keys a browser keeps (their link shows the end again)


def _prune_ended(survey):
    """Forget the keys of ended participations in other surveys, all but the newest KEEP_ENDED. A browser that takes
    part in many surveys (a lab computer) kept them all, and the session cookie grew past what browsers keep (about
    4 KB): then nobody could start at it any more. Opening such an old link again starts afresh, unless the survey
    marks the browser (setting repeat_marker). A participation whose separated section is open is kept."""
    import re

    _prune_waiting(survey)
    others = {}
    for key, value in session.items():
        found = re.fullmatch(r"r(\d+)", key)
        if found and int(found.group(1)) != survey.id and isinstance(value, int) and f"x{found.group(1)}" not in session:
            others[int(found.group(1))] = value
    if len(others) <= KEEP_ENDED:
        return
    active = set(db.session.scalars(db.select(Response.id).filter(Response.id.in_(list(others.values())),
                                                                  Response.status == Response.ACTIVE)))
    ended = sorted((rid, sid) for sid, rid in others.items() if rid not in active)
    for _, sid in ended[:max(0, len(ended) - KEEP_ENDED)]:
        for key in (f"r{sid}", f"rp{sid}", f"pp{sid}", f"xr{sid}", f"xd{sid}", f"da{sid}"):
            session.pop(key, None)


def _prune_waiting(survey):
    """Forget the keys of other surveys whose link this browser only opened (n<id>: the first page waiting with its
    seed, pid and times), declined (d<id>) or came to with URL parameters (pp<id>), all but the newest KEEP_ENDED
    (waiting ones by when they were opened). A computer in a seminar room where many links are only looked at
    kept them all, the cookie never expired (each request renews it) and grew past what browsers keep. Keys of a
    survey with a participation (r<id>) are left to _prune_ended."""
    import re

    waiting = {}
    for key, value in list(session.items()):
        found = re.fullmatch(r"(n|d|pp)(\d+)", key)
        if not found:
            continue
        sid = int(found.group(2))
        if sid == survey.id or f"r{sid}" in session:
            continue
        at = value.get("at") if found.group(1) == "n" and isinstance(value, dict) else None
        waiting[sid] = max(waiting.get(sid, ""), str(at or ""))
    if len(waiting) <= KEEP_ENDED:
        return
    for sid in sorted(waiting, key=lambda s: (waiting[s], s))[:len(waiting) - KEEP_ENDED]:
        for key in (f"n{sid}", f"d{sid}", f"pp{sid}"):
            session.pop(key, None)


MARKER_DAYS = 180  # how long a browser stays marked after a participation ended (setting "repeat_marker")
ACCESSIBILITY_STATEMENT = (2026, 10, 7)  # the day the accessibility statement was last reviewed (participant/accessibility.html)


def _marker_name(survey):
    return f"qdone_{survey.code}"


def _marker_on(survey):
    """The survey marks browsers that took part. Not on shared devices ("Mehrere Teilnahmen je Gerät")."""
    from app import panel

    return bool(getattr(survey, "repeat_marker", False)) and not panel.multi(survey)


def _marked(survey):
    """This browser carries the mark of an ended participation, and nothing else of it (the session is gone)."""
    return _marker_on(survey) and request.cookies.get(_marker_name(survey)) == "1"


def _mark(survey, response, page):
    """The page with the cookie that marks this browser, once a stored participation has ended. The cookie says
    only "took part": no identifier, nothing of the answers. A session cookie lasts 7 days, this one MARKER_DAYS."""
    if response.id is not None and not response.is_preview and response.status != Response.ACTIVE:
        from app import survey_invites

        survey_invites.ended(survey)  # the entry of an invitation list: "teilgenommen", no more reminders
    if response.id is None or response.is_preview or response.status == Response.ACTIVE or not _marker_on(survey):
        return page
    page = make_response(page)
    page.set_cookie(_marker_name(survey), "1", max_age=MARKER_DAYS * 86400, httponly=True, samesite="Lax",
                    secure=current_app.config["SESSION_COOKIE_SECURE"], path=url_for("participate.page", uid=""))
    return page


def _end_page(survey, response, lang):
    return _mark(survey, response, _end_page_of(survey, response, lang))


def _end_page_of(survey, response, lang):
    from app import panel

    from app.runtime import builtin_variables, load_answers

    from app import quotas

    params = panel.values(survey)
    away, forward = leave(survey, response.status,
                          {**params, **builtin_variables(response, survey), **panel.extra_values(survey, response)},
                          end_node(survey, response),
                          # what a rule of the links may test: the answers (by question id), the URL parameters, the quotas
                          {**load_answers(response), **{f"p:{k}": v for k, v in params.items()}, **quotas.view(survey, response)})
    if away:
        return away
    # shared devices (setting "Mehrere Teilnahmen je Gerät"): the next person starts from here
    again = end_text(survey, lang, "next_person") if panel.multi(survey) and survey.status == Survey.RUNNING else None
    from app import boards, erasure

    code = erasure.code_for(survey, response) if _recent_erase(response) else None
    return render_template("participant/end.html", survey=survey, lang=lang,
                           text=finished_text(survey, response, lang, bool(code)), status=response.status, again=again,
                           erase_code=code, erase_until=None if code else _erase_until(response, lang),
                           in_app=getattr(response, "enrolment_id", None) is not None,
                           boards=boards.end_links(survey, lang) if response.status == Response.COMPLETED else [], **forward)


def _erase_until(response, lang):
    """Without a deletion code the end page still names the right the consent promised: until when this browser
    can delete the answers on the privacy page (its session, PERMANENT_SESSION_LIFETIME; V43). None in a preview."""
    if response is None or response.id is None or response.is_preview:
        return None
    if not _recent_erase(response) or response.completed_at is None:
        return None
    from app.timeutil import fmt

    return fmt(response.completed_at + timedelta(minutes=15))


def _split_key(survey):
    return f"x{survey.id}"


def _split_done_key(survey):
    return f"xd{survey.id}"


def _start_split(survey, response):
    """The main part is finalized; the separated section starts as its own, unlinked session. The orders drawn
    for its statements, rows and options (built with the main part's layout) go with it, as in the main part."""
    from app import contact
    from app.models import SplitSession

    inside = {str(i.id) for i in contact.separated_items(survey)}
    orders = {k: v for k, v in ((response.layout or {}).get("stmt") or {}).items() if k.split("#")[0] in inside}
    layout = {"pages": response.layout.get("after_split", []), "stmt": orders, "conditions": {}, "cj": {}, "loops": {},
              "sig": response.layout.get("sig")}
    split = SplitSession(survey_id=survey.id, language=response.language, layout=layout, answers={}, position=0)
    db.session.add(split)
    db.session.commit()
    session[_split_key(survey)] = split.id
    return split


def _finish_split(survey, split):
    """Move the answers into an unlinked entry (no time, no reference) and forget the session. On PostgreSQL the
    finished session waits for the next bundle of the housekeeping instead (contact.store)."""
    from app import contact

    if split.answers and any(v not in (None, "", [], {}) for v in split.answers.values()):
        contact.store(survey, {**split.answers, **({contact.SEED: split.id} if contact.needs_seed(survey) else {})}, split)
    else:
        db.session.delete(split)
    session.pop(_split_key(survey), None)
    session[_split_done_key(survey)] = True
    db.session.commit()


def _run_split(survey, response, lang):
    from app.models import SplitSession

    split = db.session.get(SplitSession, session.get(_split_key(survey), ""))
    if split is None or split.survey_id != survey.id or split.status == SplitSession.COMPLETED:  # ended, waits (K11)
        if request.method == "POST" and str(request.form.get("pos") or "").startswith("x"):
            # a page of the separated section sent again with the cookie from before its end (no answer reached the
            # browser): its session is gone and its answers are stored; a new one only starts on the page itself,
            # or the entry was stored twice (code review 2026-10-07, T2)
            return redirect(url_for("participate.page", uid=survey.public_key))
        split = _start_split(survey, response)
    g.participation = split  # see page()
    from app import contact

    # a comparison or placement in the section whose source question is in the main part reads that answer: it is
    # lent to this page in memory and taken out again before anything is stored (runtime.step does not commit)
    lent = contact.borrowed(survey, response)
    if lent:
        split.answers = {**(split.answers or {}), **lent}
    result = runtime.step(survey, split, request.form, lang, request.method)  # no URL parameters here
    if lent:
        split.answers = {k: v for k, v in (split.answers or {}).items() if k not in lent}
    if split.status == SplitSession.COMPLETED:
        _finish_split(survey, split)
        return _end_page_of(survey, response, lang)  # marked by the caller
    db.session.commit()
    if result.redirect:
        return redirect(url_for("participate.page", uid=survey.public_key))
    return render_template("participant/page.html", survey=survey, lang=lang, separated=True, **result.context)


def _recent_erase(response):
    """A shared browser grants deletion only while answering or shortly after completion (SP13 TN-7)."""
    return response is not None and (response.status == Response.ACTIVE or (
        response.completed_at is not None and 0 <= (utcnow() - response.completed_at).total_seconds() <= 900))


@bp.route("/<uid>/privacy")
def privacy(uid):
    """Privacy notice for participants of this survey: who is responsible, what is stored. No links except
    the way back into the survey (`back`: a local path, e.g. the preview; default: the survey link)."""
    from app import panel
    from app.items.consent import _construct_names, _has_split

    survey = _survey(uid)
    lang = request.args.get("lang") if request.args.get("lang") in survey.languages else _pick_language(survey)
    from app.links import local_path

    back = local_path(request.args.get("back")) or url_for("participate.page", uid=survey.public_key)
    consent = survey.consent_item
    people = consent.kind.researchers(consent) if consent else []
    own = _current_response(survey)
    if _returned(survey, own) or not _recent_erase(own):
        own = None  # came in through a return link: what this browser holds is not to be deleted or named from here
    return render_template(
        "participant/privacy.html", survey=survey, lang=lang, back=back,
        own=own is not None, erase_code=None, erase_on=bool(getattr(survey, "erase_code", False)),
        keep=__import__("app.retention", fromlist=["of"]).of(survey),
        people=people or list(survey.members),
        # a live vote has no consent: its own contact address (app/live/, Einstellungen → Teilnahme)
        email=(consent.config.get("contact_email") if consent else (
            __import__("app.live", fromlist=["contact_email"]).contact_email(survey) if survey.live is not None else "")) or "",
        stored_ids=panel.stored_names(survey), separated=_has_split(survey), link_hosts=panel.link_hosts(survey),
        link_cell=panel.passes_cell(survey), external_hosts=__import__("app.external", fromlist=["hosts"]).hosts(survey),
        webservice_hosts=__import__("app.webservice", fromlist=["privacy_hosts"]).privacy_hosts(survey),
        technical=bool(survey.paradata), voice=__import__("app.items.voice", fromlist=["used"]).used(survey),
        ai_chat=bool(__import__("app.ai_chat", fromlist=["chat_items"]).chat_items(survey)),
        media=any(i.type == "media" for b in survey.blocks for i in b.items), page_times=bool(getattr(survey, "page_timing", False)),
        focus_paste=bool(getattr(survey, "focus_paste", False)),
        focus_inputs=bool(getattr(survey, "focus_paste", False) and getattr(survey, "focus_inputs", False)), ai_bait=bool(getattr(survey, "ai_bait", False)),
        repeat_marker=MARKER_DAYS if getattr(survey, "repeat_marker", False) else None,
        ip_region=bool(getattr(survey, "geo_ip", False)),
        scheme=__import__("app.theme", fromlist=["is_auto"]).is_auto(__import__("app.theme", fromlist=["of"]).of(survey)),
        has_map=any(i.type == "map_marker" or (i.type == "geolocation" and i.kind.correct(i)) for b in survey.blocks for i in b.items),
        marker_map=any(i.type == "map_marker" for b in survey.blocks for i in b.items),
        location=__import__("app.items.geolocation", fromlist=["finest"]).finest(survey),
        location_ip=__import__("app.items.geolocation", fromlist=["ip_fallback"]).ip_fallback(survey),
        location_personal=bool(__import__("app.items.geolocation", fromlist=["personal_items"]).personal_items(survey)),
        pooled=bool(_construct_names(survey)), map_hosts=_map_hosts(), live_quiz=_live_quiz(survey),
        live_feedback=survey.live is not None and any((survey.live or {}).get(k, k == "reactions") for k in ("reactions", "qa")),
        ai_coding=__import__("app.coding", fromlist=["ai_informed"]).ai_informed(survey),  # audit 2026-10-10, B-1
        ai_coding_text=__import__("app.items.consent", fromlist=["ai_coding_sentence"]).ai_coding_sentence(lang, survey),
        # Art. 13 GDPR (comparison with the RWTH template 2026-10-20): the controller and the data protection officer
        # the researchers entered, else the installation's (participant/_controller.html, _dpo.html)
        consented=consent is not None, controller=(consent.config.get("controller") or "") if consent else "",
        dpo=(consent.config.get("dpo") or "") if consent else "",
        special=bool(consent.config.get("special_categories")) if consent else False,  # Art. 9 GDPR (L8)
        joint=bool(consent and consent.kind.joint(consent)))  # Art. 26 GDPR


def _map_hosts():
    """The servers a map loads from, for the privacy page: None while they are OpenStreetMap's own (the page names
    them in its usual sentence), else their names (audit 2026-10-10, texts against the code)."""
    from app.public import map_hosts

    hosts, osm = map_hosts()
    return None if osm else hosts


def _live_quiz(survey):
    """Whether a live vote asks quiz questions: then a nickname is kept with the participation for the leaderboard
    (app/live/, decision L3), and the privacy page says so."""
    if survey.live is None:
        return False
    from app import live

    st = live.state(survey)
    return any(live.quiz_of(st, i) for i in live.questions(survey))


@bp.route("/<uid>/no-mail/<token>", methods=["GET", "POST"])
@csrf.exempt  # the one-click POST of a mail program (RFC 8058) has no form; the token is the proof
def no_mail(uid, token):
    """The link at the end of every invitation mail (also List-Unsubscribe): no further mails about this survey.
    A GET only asks (mail programs open links to check them); the button, or a one-click POST, opts out."""
    from app import survey_invites
    from app.ui_i18n import tr

    survey = _survey(uid)
    lang = _pick_language(survey)
    invite = survey_invites.find(survey, token)
    if invite is None or invite.status == "optout" or request.method == "POST":
        if invite is not None and invite.status != "optout":
            survey_invites.opt_out(invite)
        text = tr(lang, "Sie bekommen zu dieser Befragung keine weiteren E-Mails.", "You will get no further emails about this survey.")
        return render_template("participant/end.html", survey=survey, lang=lang, text=text, status="closed")
    return render_template("participant/no_mail.html", survey=survey, lang=lang, token=token)


@bp.route("/<uid>/accessibility")
def accessibility(uid):
    """Accessibility statement (BGG NRW, BITV NRW) for the pages of this survey: how far they meet WCAG 2.1 AA, what
    is known not to, whom to tell, and the conciliation body. In the survey's language, linked from the footer of
    every participant page (accessibility concept D9, decision E7). No links except the way back, the contacts (the
    survey's contact address and the provider, X51) and the form for complaints."""
    from datetime import date

    from app.links import local_path
    from app.ui_i18n import date_text

    survey = _survey(uid)
    lang = request.args.get("lang") if request.args.get("lang") in survey.languages else _pick_language(survey)
    back = local_path(request.args.get("back")) or url_for("participate.page", uid=survey.public_key)
    consent = survey.consent_item
    return render_template(
        "participant/accessibility.html", survey=survey, lang=lang, back=back,
        made=date_text(date(*ACCESSIBILITY_STATEMENT), lang),
        operator_email=current_app.config.get("IMPRINT_EMAIL", ""),
        email=(consent.config.get("contact_email") if consent else "") or "")


@bp.route("/<uid>/delete", methods=["GET", "POST"])
def erase(uid):
    """Participants delete their own answers (app/erasure.py): in the browser that took part without a code, else
    with the deletion code from the end page. Reachable from the privacy page, also once the survey is closed.
    Nothing is asked and nothing is kept: no reason, no contact, no trace of who deleted what."""
    from app import erasure, limits
    from app.links import local_path
    from app.models import SplitSession
    from app.timeutil import fmt
    from app.ui_i18n import tr

    survey = _survey(uid)
    lang = request.args.get("lang") if request.args.get("lang") in survey.languages else _pick_language(survey)
    back = local_path(request.args.get("back")) or url_for("participate.page", uid=survey.public_key)
    privacy_url = url_for("participate.privacy", uid=survey.public_key, lang=lang, back=back)
    own = _current_response(survey)
    if _returned(survey, own) or not _recent_erase(own):
        own = None  # came in through a return link (_returned): only the deletion code deletes from here
    error, status, done = None, 200, False
    if request.method == "POST":
        here, target = bool(request.form.get("here")), None
        if not limits.allow_erase():
            # also "here": a script that starts and deletes participations in a loop (S18 of the audit 2026-10-06)
            status = 429
            error = tr(lang, "Zu viele Versuche. Bitte versuchen Sie es in einigen Minuten noch einmal.",
                       "Too many attempts. Please try again in a few minutes.")
        elif not request.form.get("sure"):
            # the tick is asked for by the server, in the survey's language (the form has novalidate; UI/UX audit
            # 2026-10-16, 101)
            error = tr(lang, "Bitte bestätigen Sie, dass Ihre Antworten endgültig gelöscht werden sollen.",
                       "Please confirm that your answers are to be deleted for good.")
        elif not here and not (request.form.get("code") or "").strip():
            error = tr(lang, "Bitte geben Sie den Löschcode ein.", "Please enter the deletion code.")
        elif here:
            target = own
            if target is None:
                error = tr(lang, "In diesem Browser liegt keine Teilnahme an dieser Befragung mehr vor.",
                           "This browser no longer holds a participation in this survey.")
        else:
            target = erasure.by_code(survey, request.form.get("code"))
            from app import paper

            from app import interview_offline

            if target is None and paper.erase_unscanned(request.form.get("code"), survey) is not None:
                done = True  # a paper sheet not taken over yet: marked deleted (app/paper.py)
            elif target is None and interview_offline.erase_pending(request.form.get("code"), survey) is not None:
                done = True  # an interview still on the device of a field pack: never synced (app/interview_offline.py)
            elif target is None:
                error = tr(lang, "Zu diesem Code gibt es keine Teilnahme an dieser Befragung. Bitte prüfen Sie die Eingabe.",
                           "There is no participation in this survey for this code. Please check what you entered.")
        if target is not None:
            in_browser = own is not None and own.id == target.id
            # a separated section this browser is still in belongs to the same person; a finished one cannot be
            # told from the others any more (models.ContactEntry)
            split = db.session.get(SplitSession, session.get(_split_key(survey), "")) if in_browser else None
            erasure.erase(survey, [target], "self", [split] if split is not None and split.survey_id == survey.id else [])
            if in_browser:
                for key in (_session_key(survey), _pid_key(survey), _pending_key(survey), _split_key(survey),
                            _split_done_key(survey), _returned_key(survey)):
                    session.pop(key, None)
            own, done = None, True
    return render_template("participant/erase.html", survey=survey, lang=lang, back=back, privacy_url=privacy_url,
                           done=done, error=error, example=_ltr(erasure.example(survey)), codes=bool(getattr(survey, "erase_code", False)),
                           own_since=fmt(own.started_at, with_time=False) if own is not None else None), status



def _ltr(text):
    """A code inside a sentence, isolated left to right (U+2066 LRI ... U+2069 PDI): on a right-to-left page it is
    not mirrored ("24-9R7J-..." stays as written). Inside a translated sentence there is no element to give dir=ltr
    (UI/UX audit 2026-10-09, X32); erasure.split drops the marks again when the code is copied with them."""
    return chr(0x2066) + text + chr(0x2069)


EMAIL_LIKE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
COMPLAINT_KINDS = {  # key: (German, English); the German name is kept as the tag of the complaint
    "copyright": ("Urheberrecht", "Copyright"),
    "privacy": ("Datenschutz", "Data protection"),
    "content": ("Rechtswidrige oder verletzende Inhalte", "Unlawful or offensive content"),
    "other": ("Sonstiges", "Other"),
}


@bp.route("/<uid>/complaint", methods=["GET", "POST"])
def complaint(uid):
    """A complaint about this survey (copyright, data protection, content) to the operators of the platform, not
    to its researchers: it is listed first on /feedback/ and announced in Slack (app/notify.py). No account
    needed; reachable from the privacy page, also once the survey is closed."""
    from app import limits, notify
    from app.links import local_path
    from app.models import Feedback
    from app.ui_i18n import tr

    survey = _survey(uid)
    lang = request.args.get("lang") if request.args.get("lang") in survey.languages else _pick_language(survey)
    back = local_path(request.args.get("back")) or url_for("participate.page", uid=survey.public_key)
    privacy_url = url_for("participate.privacy", uid=survey.public_key, lang=lang, back=back)
    form, error = request.form if request.method == "POST" else {}, None
    if request.method == "POST":
        kind, message = form.get("kind", ""), form.get("message", "").strip()
        if form.get("website"):  # the field people do not see: filled in by scripts only
            return render_template("participant/complaint.html", survey=survey, lang=lang, sent=True,
                                   privacy_url=privacy_url, back=back)
        name = " ".join(form.get("name", "").split())[:80]
        email = "".join(form.get("email", "").split())[:110]
        if kind not in COMPLAINT_KINDS or not message:
            error = tr(lang, "Bitte wählen Sie die Art und beschreiben Sie kurz, worum es geht.",
                       "Please choose the kind and briefly describe the issue.")
        elif form.get("good_faith") != "1":  # Art. 16(2)(d) DSA (copyright audit 2026-10-16, U11)
            error = tr(lang, "Bitte bestätigen Sie, dass Ihre Angaben nach bestem Wissen richtig und vollständig sind.",
                       "Please confirm that your details are accurate and complete to the best of your knowledge.")
        elif email and not EMAIL_LIKE.match(email):
            error = tr(lang, "Bitte prüfen Sie die E-Mail-Adresse.", "Please check the email address.")
        elif not limits.allow_complaint(survey):
            error = tr(lang, "Sie haben heute schon mehrere Beschwerden zu dieser Befragung gesendet. Bitte versuchen Sie es "
                       "morgen noch einmal.", "You have already sent several complaints about this survey today. Please "
                       "try again tomorrow.")
        else:
            # over the daily cap of the survey a complaint is still kept (a flood from many senders must not lock
            # real ones out), but Slack hears only once that more are waiting in Askurious
            cap, before = current_app.config["COMPLAINTS_PER_DAY"], limits.complaints_today(survey)
            item = Feedback(severity=Feedback.COMPLAINT, tag=COMPLAINT_KINDS[kind][0], survey_id=survey.id,
                            message=f"Befragung {survey.code} „{survey.title}“\n\n{message[:5000]}",
                            contact=(f"{name} <{email}>" if name and email else name or email)[:200],
                            page=url_for("participate.page", uid=survey.public_key))
            # The confirmation of receipt (Art. 16(4) DSA) goes to an address nobody confirmed, so the form must not
            # be a way to mail strangers (audit 2026-10-18, BIZ-03): a fixed text with only the survey's code and the
            # case number (never the title, which researchers set freely and which could carry spam), at most
            # COMPLAINT_MAILS_PER_ADDRESS mails per address and day from all senders and surveys, and none once the
            # survey is over its daily cap. The complaint itself is always kept and listed on /feedback/, and the
            # page confirms it with the case number: the limits cut the flood of mails, never a real complaint.
            mail_ok = bool(email) and (not cap or before < cap) and limits.allow_complaint_mail(email)
            db.session.add(item)
            db.session.commit()
            if mail_ok:
                from app import mail

                mail.send(email, tr(lang, "Ihre Beschwerde ist eingegangen", "Your complaint has been received"),
                          tr(lang, "Ihre Beschwerde zur Befragung mit dem Code {code} ist bei den Betreibern der "
                                   "Plattform eingegangen (Vorgangsnummer {number}). Sie erhalten eine Nachricht, sobald "
                                   "darüber entschieden ist.",
                             "Your complaint about the survey with the code {code} has reached the operators of the "
                             "platform (case number {number}). You will hear from them once it has been decided.",
                             code=survey.code, number=item.id) + mail.signature())
            if not cap or before < cap:
                notify.feedback(item)
            elif before == cap:
                notify.slack(f":rotating_light: Befragung `{survey.code}`: {cap} Beschwerden in 24 Stunden, weitere "
                             "Beschwerden heute nur in Askurious.")
            return render_template("participant/complaint.html", survey=survey, lang=lang, sent=True,
                                   privacy_url=privacy_url, back=back, number=item.id)
    return render_template("participant/complaint.html", survey=survey, lang=lang, sent=False, form=form, error=error,
                           kinds=COMPLAINT_KINDS, privacy_url=privacy_url, back=back)


def _declined_page(survey, lang):
    """The end after declining: the completion link for it, else the end page; while the survey runs that page
    offers the way back (a slip of the finger on "I do not want to take part" would otherwise lock this browser
    out for the life of the cookie)."""
    from app import panel

    away, forward = leave(survey, "declined", {**panel.values(survey), "lang": lang})
    if away:
        return away
    again = end_text(survey, lang, "again") if survey.status == Survey.RUNNING else None
    return render_template("participant/end.html", survey=survey, lang=lang, text=end_text(survey, lang, "declined"),
                           status="declined", again=again, **forward)


@bp.route("/<uid>/after-all", methods=["POST"])
def again(uid):
    """"Doch teilnehmen" on the page after declining: forget the decline and open the survey afresh.
    Nothing of the declined participation exists any more; the count of declines goes down by this one again (a
    slip of the finger is no decline; audit 2026-10-10, T-3).
    Also "Neue Teilnahme an diesem Gerät" after a finished participation, where the survey allows it."""
    survey = _survey(uid)
    from app import panel

    from app import browser_receipts

    if browser_receipts.undo_decline(survey, session.pop(_declined_key(survey), None)):
        # counted by the database in one step, never below 0, without moving "last changed" (as the decline)
        db.session.execute(db.update(Survey).where(Survey.id == survey.id, Survey.declined_count > 0)
                           .values(declined_count=Survey.declined_count - 1, updated_at=Survey.updated_at)
                           .execution_options(synchronize_session=False))
        db.session.commit()
    session.pop(_pending_key(survey), None)
    response = _current_response(survey)
    if response is not None and response.status != Response.ACTIVE and panel.multi(survey):
        # "Neue Teilnahme an diesem Gerät": the finished participation stays stored, this browser lets go of it.
        # A participation that is still running is never dropped this way.
        for key in (_session_key(survey), _pid_key(survey), _split_key(survey), _split_done_key(survey),
                    _returned_key(survey)):
            session.pop(key, None)
        once = panel.once_names(survey)
        panel.remember(survey, {k: v for k, v in panel.values(survey).items() if k not in once})
    return redirect(url_for("participate.page", uid=uid))


def _external_values(survey, response):
    """What the address of an external section may be filled with: as for a completion link."""
    from app import panel

    return {**panel.values(survey), **runtime.builtin_variables(response, survey), **panel.extra_values(survey, response)}


def _external_page(survey, response, lang, step):
    """At an external section (app/external.py): the short page that sends the person on, or, once that page was
    sent, the way to the web app itself. An unsaved participation (the link was just opened and the section comes
    before every page) is stored by the page being sent, like by any first page."""
    from app import external

    node = step["node"]
    if step["go"]:
        url = external.target(survey, response, node, _external_values(survey, response))
        if url:
            return redirect(url, code=303)
    entry = external.entry(response, node["id"])
    return render_template("participant/external.html", survey=survey, lang=lang, page_token=step["token"],
                           text=(node.get("text") or {}).get(lang) or "", been_out=bool(entry.get("n")),
                           auto_after=external.AUTO_SECONDS if node.get("mode") == "auto" and not entry.get("n") else None)


@bp.route("/<uid>/back")
def come_back(uid):
    """The return link of an external section ({{return}}, app/external.py): sealed by this server, good for one
    return within external.MAX_SECONDS. It resumes the participation right behind the section, in whatever tab or
    browser it is opened, and keeps the declared values the web app appended. Opened again, it only leads back into
    the participation of this browser, and stores nothing. A browser other than the one that left holds the
    participation from the section on only (_returned)."""
    from app import external, flow, panel
    from app.ui_i18n import tr

    survey = _survey(uid)
    data = external.read_token(request.args.get("t"))
    response = db.session.scalar(db.select(Response).filter_by(pid=data[0], survey_id=survey.id)) if data else None
    node = flow.find(flow.tree(survey), data[1])[0] if data else None
    mine = _current_response(survey)
    if response is None or response.is_preview or node is None or node.get("t") != external.NODE:
        lang = _pick_language(survey, mine)
        text = tr(lang, "**Dieser Rückkehrlink ist nicht gültig.**\nBitte öffnen Sie den Link so, wie Sie ihn erhalten haben.",
                  "**This return link is not valid.**\nPlease open the link exactly as you received it.")
        return render_template("participant/end.html", survey=survey, lang=lang, text=text, status="closed"), 400
    if response.status == Response.ACTIVE and _too_late(survey):
        return _too_late_page(survey, _pick_language(survey, response))
    now = utcnow()
    held = external.held_params(response)  # the URL parameters of the start, held while the person was outside
    if response.status == Response.ACTIVE and external.come_back(response, node, data[2], request.args, now):
        response.last_active_at = now
        db.session.commit()
        # this browser continues the participation: the one that left, or another one (the web app opened elsewhere)
        session.permanent = True
        if mine is None or mine.id != response.id:
            # another browser: whoever has the link (it may have leaked from the web app's log, a referrer or a
            # shared computer) gets the rest of the participation, not what came before the section
            kept = session.get(_returned_key(survey))
            before = list(kept.get("nodes") or []) if isinstance(kept, dict) and kept.get("pid") == response.pid else []
            session[_returned_key(survey)] = {"pid": response.pid, "nodes": before + [node["id"]]}
        session[_session_key(survey)] = response.id
        session[_pid_key(survey)] = response.pid
        for key in (_pending_key(survey), _declined_key(survey)):
            session.pop(key, None)
        if mine is None or mine.id != response.id or not panel.values(survey):
            # the URL parameters of the start, for texts, conditions and the completion link: those stored with the
            # participation and those only held while it was outside (mode "nur weiterleiten", external.leave)
            panel.remember(survey, {**(response.params or {}), **held})
        return redirect(url_for("participate.page", uid=uid))
    if mine is not None and mine.id == response.id:
        return redirect(url_for("participate.page", uid=uid))  # used before, in this browser: on with the survey
    lang = _pick_language(survey, mine)
    text = tr(lang, "**Dieser Rückkehrlink wurde bereits verwendet.**\nBitte setzen Sie die Befragung in dem Fenster fort, "
              "in dem Sie zurückgekehrt sind.", "**This return link has already been used.**\nPlease continue the survey "
              "in the window in which you returned.")
    return render_template("participant/end.html", survey=survey, lang=lang, text=text, status="closed"), 200


def _used_page(survey, lang, key="used", values=None):
    """Taken part already (a once-only ID, or the mark of this browser): nothing new is stored or counted; the
    completion link "Duplikat" sends the person back to the panel if there is one (Ralf, 2026-10-08)."""
    from app import panel

    values = panel.values(survey) if values is None else values
    away, forward = leave(survey, "duplicate", {**values, "lang": lang})
    if away:
        return away
    return render_template("participant/end.html", survey=survey, lang=lang,
                           text=end_text(survey, lang, key), status="closed", **forward), 200


def _catch_up(survey, response, lang):
    """The first page was just stored. The link opened showed it from an unsaved participation, and a quota
    calculation or an end on the way there was passed without a place (quotas._place takes none for an unsaved
    one): they are taken now, from the start of the layout, as a GET of a stored participation would. None if the
    participation goes on (usually on the page that was shown), else the end page (a full quota: the end "Quote
    voll" ends it here; it is stored with that status)."""
    from app import panel

    pages = response.layout.get("pages") or []
    shown = response.position or 0
    if not any("quota" in spec or "end" in spec for spec in pages[:shown]):
        return None
    response.position = 0
    runtime.step(survey, response, {}, lang, "GET", params=panel.values(survey), catch_up=True)
    if response.status == Response.ACTIVE:
        # the walk ends on the page that was shown (a branch on what the quota calculation decided may lead
        # elsewhere: then the page that was sent is an older one, and the person sees the page they are on)
        return None
    db.session.commit()
    return _end_page(survey, response, lang)


def _direct(response):
    """participant.js sent the page (field "_direct"): the next page may come in the answer to the POST itself. Only for
    a stored participation; the pages before it (not stored yet) keep the redirect."""
    return request.method == "POST" and request.form.get("_direct") == "1" and response.id is not None


def _needs_split(survey, response):
    """The main part is completed and its separated section is still to come (participate._run_split)."""
    return bool(response.status == Response.COMPLETED and (response.layout or {}).get("after_split")
                and not session.get(_split_done_key(survey)))


def _walk_to_page(survey, response, lang):
    """What the GET after the redirect would do, in this request: the steps up to the page to show, an end or an
    external section. None if it does not settle (then the redirect after all)."""
    from app import panel

    pages = (response.layout or {}).get("pages") or []
    for _ in range(len(pages) + 1):
        result = runtime.step(survey, response, {}, lang, "GET", params=panel.values(survey),
                              ext_barrier=_returned(survey, response))
        if not result.redirect:
            return result
        _commit_keeping_loaded()  # as the request of each redirect would
    return None


@bp.route("/<uid>/block", methods=["POST"])
def block_page(uid):
    """A page of a block loaded at once, sent in the background by participant.js (app/blockload.py): only stored,
    nothing shown; the answer is JSON with the page's errors and notes. 409 when the participation is not at that
    block (any more): the browser loads the page the server is at."""
    from flask import jsonify

    from app import blockload, panel

    survey = _survey(uid)
    response = _current_response(survey)
    if (request.content_length or 0) > blockload.MAX_BODY:
        return jsonify(error="large"), 413
    data = request.get_json(silent=True)
    if response is None or response.status != Response.ACTIVE or not isinstance(data, dict) or _too_late(survey) or (
            survey.status == Survey.DRAFT and not (response.enrolment_id and response.source == "test")):
        return jsonify(error="stale"), 409
    positions = blockload.run(survey, response, response.position)
    if not positions or str(data.get("start")) != str(positions[0]):
        return jsonify(error="stale"), 409
    if data.get("pos") is not None and str(data["pos"]) != runtime.page_token(response, response.position):
        # a page of another participation (a tab still open from the person before on this device) or of an older
        # version of the block: never stored here; "gone" lets the tab drop it from its queue (audit 2026-10-23,
        # DS-01). A send without "pos" comes from a script of before that change and is checked as before
        return jsonify(error="stale", gone=True), 409
    # numbers as the page that was sent writes them ("1.000" typed in German is 1000), also when the language was
    # switched in another tab meanwhile; else as the page form does (audit 2026-10-23, DS-02)
    sent_lang = data.get("page_lang")
    if sent_lang not in survey.languages:
        sent_lang = response.language if response.language in survey.languages else _pick_language(survey, response)
    try:
        out = blockload.store(survey, response, positions, data, sent_lang, panel.values(survey))
    except ValueError:  # a form no page of ours sends (offline_answers.fields)
        db.session.rollback()
        return jsonify(error="invalid"), 400
    if out is None:
        db.session.rollback()
        return jsonify(error="stale"), 409
    db.session.commit()
    return jsonify(out)


def _finish_block(survey, response, lang, positions, meta, note_post, sent_lang):
    """The last page of a block loaded at once was sent, with the whole block (app/blockload.py): every page is
    checked once more, in order, as if each had been sent by itself (runtime.step), with the time the browser
    measured on it (decision 4a; cut to the server's clock where the sum is longer). The first page that is not
    complete stops it: the block is shown again on that page. Returns the result of the last step."""
    from datetime import timedelta

    from app import blockload, pagetime, panel

    now = utcnow()
    added, cut, total, server, measured = blockload.browser_times(response, positions, meta, now)
    timing = bool(getattr(survey, "page_timing", False))
    result, counted = None, []
    while response.status == Response.ACTIVE and response.position in positions:
        k = response.position
        page_meta = meta["pages"].get(k) or {}
        at = (now - timedelta(milliseconds=added.get(k, 0))).isoformat()
        data = dict(response.paradata or {})
        if timing:
            data["_shown"] = {"pos": k, "at": at}  # paradata.page_left adds the time of this visit
        block, items = runtime.page_at(survey, response, k)
        if block is not None and pagetime.page_times(response, block, items):
            # a minimum or maximum counts all the time on the page, also of an earlier visit (the block was left with
            # "Back" from the next block and is sent again): what was counted before plus this visit
            held = (now - timedelta(milliseconds=blockload.counted_ms(response, positions, k) + added.get(k, 0))).isoformat()
            before = data.get("_hold") or {}
            data["_hold"] = {"pos": k, "at": held, **({"more": before["more"]} if before.get("pos") == k and before.get("more") else {})}
        response.paradata = data
        result = runtime.step(survey, response, blockload.page_form(request.form, runtime.page_token(response, k), page_meta),
                              lang, "POST", params=panel.values(survey), ext_barrier=_returned(survey, response),
                              on_post=note_post if not counted else None, form_lang=sent_lang)
        counted.append(k)
        if not result.redirect or result.stale or response.position == k:
            break
    blockload.note_times(response, positions, measured, now, cut, total, server, counted, added)
    if response.status != Response.ACTIVE or response.position not in positions:
        blockload.leave(response)
    return result


def _block_page(survey, response, lang, result, positions, **page):
    """The block loaded at once: every page of it in one document (participant/_bpage.html), the one to show first
    visible (blockload.resume_at), the others for participant.js to switch to."""
    from jinja2.utils import Namespace
    from markupsafe import Markup

    from app import blockload, panel

    active = blockload.resume_at(response, positions)
    assets = Namespace(loaded_leaflet=False, loaded_geolocation=False, extra_scripts=[])
    contexts = []
    for k in positions:
        ctx = result.context if k == response.position else runtime.render_page(
            survey, response, k, lang, panel.values(survey), _returned(survey, response))
        if ctx:
            contexts.append((k, ctx))
    shown_at = [k for k, _ in contexts]
    if active not in shown_at:
        active = response.position if response.position in shown_at else shown_at[0]
    sections = [Markup(render_template("participant/_bpage.html", survey=survey, lang=lang, assets=assets, position=k,
                                       active=k == active, ai_bait=page.get("ai_bait"), **ctx))
                for k, ctx in contexts]
    shown = dict(next(ctx for k, ctx in contexts if k == active))
    # the token of the page the server is at (without JavaScript the block goes page by page, as always); the inputs
    # are kept in the tab under the name of the block
    shown.update(page_token=runtime.page_token(response, response.position), keep_key=runtime.keep_key(response, positions[0]))
    return render_template("participant/page.html", survey=survey, lang=lang, assets=assets, block_sections=sections,
                           block_first=positions[0], block_url=url_for("participate.block_page", uid=survey.public_key),
                           **page, **shown)


@bp.route("/<uid>/crowd/<int:item_id>", methods=["POST"])
def crowd(uid, item_id):
    """"Antwort festlegen" of a crowd on the page of its question (app/feedback_lock.py; audit 2026-10-11, N8/TN4):
    the answer sent is checked, stored and fixed, and only then the figures of the others come back as JSON. Before,
    they stood in the page already, and the server took a changed answer as long as the page was not sent."""
    from flask import jsonify

    from app import feedback_lock

    survey = _survey(uid)
    response = _current_response(survey)
    if response is None or response.status != Response.ACTIVE or _too_late(survey) or (
            survey.status == Survey.DRAFT and not (response.enrolment_id and response.source == "test")):
        return jsonify(error="page"), 409
    out = feedback_lock.fix_on_page(survey, response, request.form, item_id, _pick_language(survey, response))
    if out is None:
        db.session.rollback()
        return jsonify(error="page"), 409
    if "error" in out:
        db.session.rollback()
        return jsonify(out), 400
    response.last_active_at = utcnow()
    db.session.commit()
    return jsonify(out)


@bp.route("/<uid>/chat/<int:item_id>", methods=["POST"])
def chat(uid, item_id):
    """One message of the element "KI-Chat" (app/ai_chat.py): the server asks the model of the survey and stores both
    messages; JSON back. Only for a participation under way, as "Antwort festlegen" above."""
    from flask import jsonify

    from app import ai_chat
    from app.models import Item

    survey = _survey(uid)
    response = _current_response(survey)
    if response is None or response.status != Response.ACTIVE or _too_late(survey) or (
            survey.status == Survey.DRAFT and not (response.enrolment_id and response.source == "test")):
        return jsonify(error="page"), 409
    item = db.session.get(Item, item_id)
    if item is None or item.type != "ai_chat" or item.block is None or item.block.survey_id != survey.id:
        return jsonify(error="page"), 404
    lang = _pick_language(survey, response)
    body = request.get_json(silent=True) if request.is_json else None
    message = (body or {}).get("message") if isinstance(body, dict) else request.form.get("message")
    out, status = ai_chat.send(survey, response, item, message if isinstance(message, str) else "", lang)
    response.last_active_at = utcnow()
    db.session.commit()
    return jsonify(out), status


@bp.route("/<uid>", methods=["GET", "POST"])
def page(uid):
    survey = _survey(uid)
    response = _current_response(survey)

    from app import panel

    if response is None and session.get(_declined_key(survey)):
        # this browser declined before; nothing about that participation was stored
        return _declined_page(survey, _pick_language(survey))

    if response is None and survey.sampling is not None:
        # a diary study (app/diary.py): every participation comes from the app (an entry address, app/diary_api.py);
        # the plain link leads to the join page, never to the whole questionnaire (review 2026-10-11, B1)
        return redirect(url_for("diary_api.join_page", code=survey.code))

    if response is None:
        lang = _pick_language(survey)
        pending = session.get(_pending_key(survey))
        if request.method == "GET" and pending and pending.get("lang") in survey.languages \
                and request.args.get("lang") not in survey.languages:
            lang = pending["lang"]  # picked on the first page with the language switch (below), or before
        stored = _stored_first_page(survey, pending) if request.method == "POST" and pending else None
        if stored is not None:
            # the first page was stored, but the answer never reached the browser (no signal, a gateway timeout):
            # it sends the page again with the cookie from before. The stored participation goes on
            return _resume_stored(survey, stored, uid)
        if request.method == "POST" and pending and _accepting(survey) \
                and (request.form.get("set_lang") or request.form.get("nav") == "back"):
            # the language switch (or "Back") on a page that is not stored yet: nothing is stored or counted, there
            # is no consent yet. The page is shown again, in the language picked
            wanted = request.form.get("set_lang")
            if survey.language_choice and wanted in survey.languages:
                session[_pending_key(survey)] = {**pending, "lang": wanted}
            return redirect(url_for("participate.page", uid=uid))
        if request.method == "POST" and pending and _accepting(survey):
            # The first "Next" or decline: only now is the participation stored, with the layout shown before
            from app import limits

            if not limits.allow_start() or not limits.allow_survey_sender(survey):
                # too many new participations from one sender in a short time (a script); real groups stay below
                page = render_template("participant/end.html", survey=survey, lang=lang,
                                       text=end_text(survey, lang, "busy"), status="closed")
                return page, 429, {"Retry-After": str(current_app.config["START_WINDOW"])}
            if limits.survey_start_flood(survey):
                # very many starts from all senders together: nobody is turned away, the researchers are told (S5 of
                # the audit 2026-10-06; it used to lock real participants out together with the script)
                limits.note_cap(survey, "starts")
            if _marked(survey):  # took part in another tab since this page was opened
                session.pop(_pending_key(survey), None)
                return _used_page(survey, lang, "repeat")
            response = _new_response(survey, pending.get("lang") or lang, pending, stored=True)
            # the page that was shown: not the first entry of the layout if that one did not apply to this person
            response.position = _shown_position(response, pending)
            db.session.add(response)
            try:
                db.session.flush()
            except IntegrityError:
                # the same page sent twice at the same moment: the other request stored it (pid is unique)
                db.session.rollback()
                stored = _stored_first_page(survey, pending)
                if stored is None:
                    raise
                return _resume_stored(survey, stored, uid)
            if panel.claim(survey, response):
                # a once-only ID that started elsewhere since the link was opened here. Claimed after the insert,
                # so of two first pages sent at the same moment only one stays.
                db.session.rollback()
                session.pop(_pending_key(survey), None)
                return _used_page(survey, lang)
            from app import survey_invites

            if not survey_invites.started(survey):
                # only invited people, each link once (BIZ-09): the link began a participation elsewhere meanwhile
                db.session.rollback()
                session.pop(_pending_key(survey), None)
                return _used_page(survey, lang)
            from app import balance

            balance.note(survey, response.layout)  # the next start in this process sees it at once
            shown_at = datetime.fromisoformat(pending["shown"])
            if getattr(survey, "page_timing", False):
                from app import paradata

                paradata.page_shown(response, response.position, shown_at)  # the page that was shown, from then
            from app import pagetime

            pages = response.layout.get("pages") or []
            if response.position < len(pages) and not runtime.is_marker(pages[response.position]):
                block, all_items = runtime.page_at(survey, response, response.position)
                if block is not None and pagetime.page_times(response, block, all_items):
                    pagetime.shown(response, response.position, shown_at)  # the clock of a held or timed first page
            session.pop(_pending_key(survey), None)
            _prune_ended(survey)
            session[_session_key(survey)] = response.id
            session[_pid_key(survey)] = response.pid
            ended = _catch_up(survey, response, lang)
            if ended is not None:
                return ended
        else:
            values, problem = panel.capture(survey, request.args, entry=True)  # signed entry links (BIZ-05)
            from app import survey_invites

            if request.method == "GET":
                survey_invites.capture(survey, request.args)  # a personal link of an invitation mail
            declared = [p["name"] for p in panel.config(survey)["params"]]
            if panel.values(survey) and not any(name in request.args for name in declared):
                # the link again without its parameters ("Doch teilnehmen", a new participation at this device, the
                # language link of a page without JavaScript): the values this browser came with still hold
                values, problem = panel.capture(survey, panel.values(survey))
            if request.method == "POST":
                # a page was sent, but its participation is not in this browser (any more): open the survey afresh
                return redirect(url_for("participate.page", uid=uid))
            # New participants only while the survey runs; started responses may finish after closing
            if not _accepting(survey):
                # closed because the quotas let nobody in any more: the link for "quota full" if there is one
                full = survey.status == Survey.CLOSED and survey.close_reason == "quota" \
                    and panel.config(survey)["redirects"].get("quotafull")
                away, forward = leave(survey, "quotafull" if full else "closed", {**values, "lang": lang})
                if away:
                    return away
                return render_template("participant/end.html", survey=survey, lang=lang,
                                       text=closed_text(survey, lang), status="closed", **forward), 200
            refused = survey_invites.refusal(survey)  # only invited people, each link once (BIZ-09)
            if problem or refused == "missing":
                return render_template("participant/end.html", survey=survey, lang=lang,
                                       text=end_text(survey, lang, "badlink"), status="closed"), 400
            if refused == "used":
                return _used_page(survey, lang, values=values)
            if panel.taken(survey, values):
                mine = panel.resumable(survey, values, pid=session.get(_pid_key(survey)))
                if mine is not None:
                    # The signed browser session proves ownership; the public panel ID alone never does.
                    panel.remember(survey, values)
                    return _resume_stored(survey, mine, uid)
                # an ID that may take part only once and has a stored participation that ended: nothing new is
                # stored or counted; the first participation was reported to the panel already, this one goes to
                # the link "Duplikat" if there is one
                return _used_page(survey, lang, values=values)
            if _marked(survey):
                # the mark of an ended participation in this browser (setting "repeat_marker"): nothing new is
                # stored or counted; only the link "Duplikat", if there is one
                return _used_page(survey, lang, "repeat", values=values)
            from app import quotas

            full = quotas.full_at_entry(survey, values) if survey.quotas else None
            if full is not None and _release_due(survey) and quotas.release_expired(survey=survey):
                # places held by people who left long ago are given back first: they would turn this person away
                # until the next housekeeping (once a minute with traffic or by the cron job; audit 2026-10-05, T-37)
                db.session.commit()
                full = quotas.full_at_entry(survey, values)
            if full is not None:
                # a quota on URL parameters whose cell is full already: turned away before the first page;
                # nothing is stored or counted for this visit
                away, forward = leave(survey, Response.QUOTAFULL, {**values, "pid": "", "lang": lang}, full,
                                      {**{f"p:{k}": v for k, v in values.items()}, **quotas.door_view(survey, full, values)})
                if away:
                    return away
                from app import flow

                case = flow.case_for(full, quotas.door_view(survey, full, values)) if full["t"] == "end" else full
                custom = ((case or {}).get("text") or {}).get(lang)
                return render_template("participant/end.html", survey=survey, lang=lang, status=Response.QUOTAFULL,
                                       text=custom or end_text(survey, lang, "quotafull"), **forward), 200
            panel.remember(survey, values)
            # Opening the link creates no participation (crawlers, link previews in messengers): the first page is
            # shown from an unsaved participation; its seed follows from the pid (browser_receipts.assignment), pid/times
            # are in the cookie, nothing is written to the database. The limit on new participations and the count per
            # network apply when one is stored (first submit), so crawlers neither use them up nor leave rows.
            now = utcnow().isoformat()
            if not pending or pending.get("q") != survey.qversion:
                pending = {"pid": new_pid(survey.code), "at": now, "shown": now,
                           "q": survey.qversion}
            elif _restart_due(pending):
                # the link opened again long after it was first opened (days later, from the same browser): the
                # participation starts now, and so does the clock of its first page. Seed, pid and the counts of
                # the draw stay: the same page as before (audit 2026-10-10, T-1)
                pending = {**pending, "at": now, "shown": now}
            else:
                # the same page again soon after (reloaded, back from the privacy page of the consent): start and
                # page clock stay at the first opening; moving the start made those who read the privacy notes look
                # faster than they were (audit 2026-10-12, T-1)
                pending = {**pending, "at": pending.get("at") or now, "shown": pending.get("shown") or now}
            pending = {**pending, "lang": lang}  # the language picked now (browser, ?lang=) also for the stored one
            response = _new_response(survey, lang, pending)  # notes the counts it was drawn from in `pending`
            session.permanent = True
            session[_pending_key(survey)] = pending
            _prune_waiting(survey)

    stale_note = session.pop(_stale_key(survey), None) if request.method == "GET" else None
    relayout_note = session.pop(_relayout_key(survey), None) if request.method == "GET" else None
    shown_in = response.language  # the language the page that was sent was shown in (before a switch)
    lang = _pick_language(survey, response)
    active = response.status == Response.ACTIVE  # an ended participation stays as it is, whoever opens its link again
    if active and response.id is not None and survey.status == Survey.DRAFT             and not (response.enrolment_id and response.source == "test"):  # a test person of a diary study answers the draft
        # the survey was opened for editing while this participation was under way: it waits and goes on once the
        # survey runs again, on the questionnaire as it is then. A page sent now keeps its valid answers while the
        # questionnaire is unchanged (A2); nothing else of this request is stored
        if request.method != "POST":
            db.session.rollback()
            return _halted_page(survey, lang)
        sent = request.form.get("page_lang")
        if sent not in survey.languages:
            sent = shown_in if shown_in in survey.languages else lang
        return _halted_page(survey, lang, kept=_keep_halted(survey, response, lang, sent))
    if active and response.id is not None and _too_late(survey):
        return _too_late_page(survey, lang)
    if lang != response.language and active:
        if response.id is not None:  # a stored participation changes its language: kept with where (audit 2026-10-12, T-5)
            from app import paradata

            paradata.note_language(response, lang)
        response.language = lang
    sent_lang = request.form.get("page_lang") if request.method == "POST" else None
    if sent_lang not in survey.languages:
        sent_lang = shown_in if shown_in in survey.languages else lang

    def note_post():
        """Paradata of the page that was sent: only for the current page (runtime.step calls this after checking
        the page token), so a page sent twice or from an older tab counts nothing twice. Only while the
        participation runs: an ended one (also on the pages of the separated section, which come after the end)
        stays as it is."""
        if not active:
            return
        if survey.paradata and not (response.paradata or {}).get("device") and request.form.get("_pd_vw"):
            from app import paradata

            response.paradata = {**(response.paradata or {}),
                                 **paradata.collect(request.headers.get("User-Agent", ""), request.form)}
        if request.form.get("_scheme") in ("light", "dark"):
            from app import theme

            if theme.is_auto(theme.of(survey)):  # the look follows the device: keep which one this person saw
                seen, now = (response.paradata or {}).get("_scheme"), request.form["_scheme"]
                if seen != now and seen != "mixed":
                    response.paradata = {**(response.paradata or {}), "_scheme": now if seen is None else "mixed"}
        if getattr(survey, "focus_paste", False):
            from app import paradata

            paradata.note_focus(response, request.form, getattr(survey, "focus_inputs", False))  # how often the page was left, how often text was pasted
        from app import quality

        quality.note_trap(response, request.form)  # the field people do not see: filled in by scripts only
        if getattr(survey, "ai_bait", False):
            quality.note_bait(survey, response, request.form)  # the code word of the note to AI systems came back

    g.participation = response  # for elements that store what they computed from the answers so far (score_band)
    from app import blockload

    block_meta = blockload.read_meta(request.form) if request.method == "POST" and active and response.id is not None else None
    block_run = blockload.run(survey, response, block_meta["start"]) if block_meta else None
    if block_run and (response.position not in block_run
                      or request.form.get("pos") != runtime.page_token(response, response.position)):
        block_run = None  # an older block or another tab: the page sent is handled as any other (stale)
    form = request.form
    if block_run:
        response.position = block_run[0]  # the whole block came along: "Back", the language and the end start there
        # the page token then names that first page, or "Back" and the language switch after a page of the block
        # failed its check would be taken for a page sent twice (code review 2026-10-15, F2)
        form = form.copy()
        form["pos"] = runtime.page_token(response, block_run[0])
    if request.method == "POST" and active and response.id is not None and request.form.get("set_lang"):
        # the language link sends the whole form of a block loaded at once, mostly without "_block": what was typed on
        # the block's other pages is kept too, not only on the page the server is at (audit 2026-10-23, DS-04)
        lang_run = block_run or (blockload.run(survey, response, response.position)
                                 if request.form.get("pos") == runtime.page_token(response, response.position) else None)
        if lang_run:
            blockload.keep_for_language(survey, response, [k for k in lang_run if k != response.position], form,
                                        sent_lang, panel.values(survey))
    if block_run and request.form.get("nav", "next") == "next" and not request.form.get("set_lang"):
        result = _finish_block(survey, response, lang, block_run, block_meta, note_post, sent_lang)
    else:
        result = runtime.step(survey, response, form, lang, request.method, params=panel.values(survey),
                              ext_barrier=_returned(survey, response), on_post=note_post, form_lang=sent_lang)
    if response.id is None:
        # not stored yet (the link was just opened): a first entry that does not apply to this person (a condition
        # on a URL parameter above the first page) moved the participation on. A redirect would start from the
        # same unsaved state again, so the step is taken here, up to the page to show or to an end
        for _ in range(len(response.layout["pages"])):
            if not result.redirect or response.status != Response.ACTIVE:
                break
            result = runtime.step(survey, response, request.form, lang, "GET", params=panel.values(survey))
        if response.status == Response.ACTIVE:
            session[_pending_key(survey)] = {**session[_pending_key(survey)], "pos": response.position}
        else:
            return _end_page(survey, response, lang)  # ended before the first page: nothing is stored or counted
    if response.status == Response.DECLINED:
        # Declined participations are not stored at all: delete the response, keep only a count
        from app import quotas

        quotas.release(response)  # a place reserved at a quota calculation is free again
        db.session.delete(response)
        from app import balance

        balance.unnote(survey, response.layout)  # its assignment no longer counts (taken away, not all read again)
        # counted by the database in one step (declines at the same moment were lost), and without moving the
        # survey's "last changed" (code review 2026-10-07, D3)
        db.session.execute(db.update(Survey).where(Survey.id == survey.id)
                           .values(declined_count=db.func.coalesce(Survey.declined_count, 0) + 1,
                                   updated_at=Survey.updated_at)
                           .execution_options(synchronize_session=False))
        session.pop(_session_key(survey), None)
        session.pop(_pid_key(survey), None)
        from app import browser_receipts

        session[_declined_key(survey)] = browser_receipts.decline(survey)
        _prune_waiting(survey)
        db.session.commit()
        return _declined_page(survey, lang)
    relaid = getattr(response, "relaid", False) and response.id is not None
    if result.redirect and _direct(response):
        # the page sent by participant.js (field "_direct"): the page the participation is on now comes in the answer
        # to this POST, without the redirect and the second request (block-load concept 2026-10-15, stage c). The
        # page itself turns its history entry into a GET (participant.js), so reloading never sends the form again.
        # Committed keeping what is loaded, as for a page shown again after an error (N-3 of the audit 2026-10-09)
        _commit_keeping_loaded()
        stale_note = stale_note or (result.stale and not relaid)
        relayout_note = relayout_note or relaid
        result = _walk_to_page(survey, response, lang)
        if result is None or (result.finished and _needs_split(survey, response)):
            # still on the way (it should not be), or the separated section, which reads the form of its own pages:
            # the redirect as without the script, with its notes
            if relayout_note:
                session[_relayout_key(survey)] = True
            elif stale_note:
                session[_stale_key(survey)] = True
            db.session.commit()
            return redirect(url_for("participate.page", uid=uid), code=303)
        relaid = False
    elif relaid:
        # a new layout for the changed questionnaire: back to the first page, which says so (also after a redirect)
        session[_relayout_key(survey)] = True
    elif result.stale and response.id is not None:
        session[_stale_key(survey)] = True  # the current page says that the one sent was dropped
    block_show = None
    if not (result.external or result.redirect or result.finished) and response.id is not None:
        block_show = blockload.run(survey, response, response.position)
        if block_show:
            blockload.shown(response, block_show, utcnow())  # the server's clock for the block (decision 4a)
    if result.external or result.redirect or result.finished:
        db.session.commit()
    else:
        # the page is rendered from what this request has loaded and written: kept over the commit. Expired, every
        # block and item of the questionnaire was read again after it (voice.used, geolocation.used, the items of
        # the page), one query per block or item (N-3 of the audit 2026-10-09)
        _commit_keeping_loaded()
    if _direct(response):
        # whatever this POST answers with (the next page, the same page with its notes, an end): its history entry
        # becomes a GET (base.html data-replace, participant.js), so a reload asks for the current page
        g.direct_page = True
    if result.external:
        return _external_page(survey, response, lang, result.external)
    if result.redirect:
        return redirect(url_for("participate.page", uid=uid))
    if result.finished:
        needs_split = _needs_split(survey, response)
        if needs_split and survey.status == Survey.DRAFT:  # its separated section waits as the main part would
            return _halted_page(survey, lang)
        if needs_split:  # the participation has ended, whatever becomes of the separated section
            return _mark(survey, response, _run_split(survey, response, lang))
        return _end_page(survey, response, lang)
    from app.items import geolocation, voice

    if voice.used(survey):
        voice.allow_microphone()
    if geolocation.used(survey) or any(i.type == "map_marker" for b in survey.blocks for i in b.items):
        geolocation.allow()  # the page may ask the browser for the position (on a tap only; the map: "Meine Position")
    from app.ui_i18n import tr

    from app import diary

    if relaid or relayout_note:
        session.pop(_relayout_key(survey), None)
        note = _relayout_note(lang)
    else:
        note = tr(lang, *STALE_NOTE) if stale_note else None
    # a crowd that fixes the answer to a question on the same page gets its figures only from crowd() below, once the
    # answer is fixed there (app/feedback_lock.py); a participation not stored yet (its first page) cannot fix one
    crowd_url = url_for("participate.crowd", uid=uid, item_id=0)[:-1] if response.id is not None else None
    page = dict(stale_note=note, crowd_url=crowd_url, in_app=getattr(response, "enrolment_id", None) is not None,
                short_entry=diary.short_entry(response),
                ai_bait=__import__("app.quality", fromlist=["bait"]).bait(survey) if getattr(survey, "ai_bait", False) else None)
    if block_show:
        return _block_page(survey, response, lang, result, block_show, **page)
    return render_template("participant/page.html", survey=survey, lang=lang, **page, **result.context)
