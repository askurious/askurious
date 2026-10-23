"""Paper questionnaires (papierfragebogen-konzept.md, E1-E17 all as recommended, Ralf 2026-10-10; no longer
experimental since 2026-10-11, Ralf).

The way of a sheet:

1. print: a print run of n sheets in one language (PaperRun). Every sheet draws its own layout like an online
   participation (orders, split groups, loop rows: runtime.build_layout) and gets a random 40-bit number; the PDF and
   the plan of its boxes come from app/paper_pdf.py. The PDF is kept with the run, so it always fits its plans.
2. upload: scans as PDF or images (a separate background process, `process`): every page is found and named by its code
   (app/paper_scan.py), stored as a grey JPEG without metadata under instance/paper-scans/<survey uid>/, and every
   field of its plan measured. Unreadable and double pages are kept a week for a look, with their image (a double
   one says how many fields read differently: perhaps a second sheet, filled in by someone else); pages of another
   survey are kept without one.
3. check: a sheet with all its pages is evaluated (`evaluate`): boxes ticked, empty, filled in (a correction), or
   uncertain; writing fields with ink. What the survey's choice says is looked at by hand (`review`): only the
   uncertain ones and handwriting ("unsure", the default), every sheet ("all") or none ("none").
4. take over (`take_over`): the sheet's answers go page by page through the page engine (runtime.step, as dummy data
   do), so conditions, computed values, quotas and ends work as online; answers to questions the person should not
   have seen are dropped like online. The participation is real (in a draft: a test run) with channel "paper".
   No consent ticked: the sheet is not stored (declined, its scans go at once).

Scans go `keep` after the takeover: at once, after 30 days (default) or with the participation. They go as well with
the participation (deletion on request, deletion by the researchers), with both retention periods (handwriting may
point to a person), with the survey and so with the account. A sheet deleted on request is marked so the paper is
destroyed too."""
import io
import os
import re
import secrets
from app.storage_room import remove_file, remove_tree, tracked, writing
import threading
import time
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app import autosave, heavy, paper_digits, paper_pdf, paper_scan
from app.extensions import db
from app.lang import de_en
from app.models import (
    PaperPage,
    PaperRun,
    PaperSheet,
    Response,
    Survey,
    new_pid,
    utcnow,
)

bp = Blueprint("paper", __name__, url_prefix="/surveys")

REVIEW = ("unsure", "all", "none", "four")  # "four": every sheet checked by two different people (E11 C)
KEEP = ("0", "30", "answers")
MAX_SHEETS = 300  # per print run: the PDF is made in the request
UPLOAD_BYTES = 40 * 1024 * 1024  # per upload (nginx allows 45 MB on the researchers' side)
MAX_PAGES = 400  # per upload
KEEP_DAYS = 30
LOOSE_DAYS = 7  # pages without a sheet of this survey (unreadable, foreign, double)
STALE_MINUTES = 20  # an upload without progress for this long counts as broken off (a restart ended its thread)
NAME_RE = re.compile(r"[A-Za-z0-9_-]{16}\.jpg\Z")
# One sheet changes its state in one place at a time in this process (a takeover, a deletion, a page stored); between
# processes the row lock does it (`_locked`, audit 2026-10-10, P-S3). Taken before any row lock, never nested.
_LOCK = threading.Lock()
# At most two uploads are read at once per process; the others wait their turn (P-S1: memory, P-S2: connections).
SCANNING = 2
_SCANS = threading.BoundedSemaphore(SCANNING)
EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff")


def settings(survey):
    s = survey.paper or {}
    # every sheet is checked unless the researchers choose otherwise: taken over unseen, they felt out of the loop
    # (Ralf, 2026-10-11)
    return {"review": s.get("review") if s.get("review") in REVIEW else "all",
            "keep": s.get("keep") if s.get("keep") in KEEP else "30", "ai_ok": s.get("ai_ok") if isinstance(s.get("ai_ok"), dict) else None}


def copied(paper):
    """The settings of a paper questionnaire that a copy or a survey file takes along (audit 2026-10-12, E-6): which
    sheets are checked and how long scans stay, only values the lists offer; the confirmation of the AI reading
    (ai_ok) stays with the survey it was given for. None when nothing was set."""
    s = paper if isinstance(paper, dict) else {}
    out = {k: s[k] for k, allowed in (("review", REVIEW), ("keep", KEEP)) if s.get(k) in allowed}
    return out or None


# --- reading handwriting with AI (E8: violet, a suggestion until a person takes the sheet over) ----------------------

AI_MAX = 40  # writing fields of one sheet in one call
# On the daily AI limit one reading counts once per started AI_UNIT fields (audit 2026-10-10, P-S11): an image section
# of a writing field (6 px/mm, a large field about 1100 x 400 px) costs a model some hundred input tokens, so ten of
# them come to about what one usual text call of Askurious sends (the coding help: up to 150 answers of 300
# characters). Per image would make a sheet with 40 fields cost 40 of the 100 calls a day; per call made one call
# worth 40 images.
AI_UNIT = 10
AI_INSTRUCTIONS = (
    "You read handwriting from scanned paper questionnaires. Every image is one answer field; the text before it gives "
    "its number and what the field asks. Transcribe exactly what is written, in the language it is written in: "
    "digits as digits, no corrections of spelling, nothing added or explained. If nothing legible is written, give an "
    "empty text. Answer for every number given.")
AI_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["fields"],
             "properties": {"fields": {"type": "array", "items": {
                 "type": "object", "additionalProperties": False, "required": ["n", "text"],
                 "properties": {"n": {"type": "integer"}, "text": {"type": "string"}}}}}}


def ai_available():
    """The AI reading shows: generative AI on for the person and an own AI access to call with. The handwriting of
    participants goes only over the researcher's own access, never over the installation's key (Ralf, 2026-10-21)."""
    from app import ai_access

    return ai_access.ai_on() and ai_access.active() is not None


def ai_needs_own():
    """True where generative AI is on for the person but no own access is stored: the page says how to get it."""
    from app import ai_access

    return ai_access.ai_on() and ai_access.active() is None


def ai_notice(own=False, to=None):
    """The paragraph for the information and consent of a survey whose handwriting the AI reads (German, English).
    `to`: the name of another recipient (an own access of a team member, ai_access.team_recipients)."""
    from app import ai_access

    to = to or ai_access.recipient()
    return {"de": f"Fragebögen auf Papier: Ihre handschriftlichen Antworten können als Bildausschnitt an den KI-Dienst "
                  f"{to} übermittelt werden, der sie abschreibt; die Forschenden prüfen jede Abschrift. Übermittelt "
                  "werden nur die Ausschnitte der Schreibfelder, ohne Ihre übrigen Antworten und ohne die Nummer des "
                  "Bogens. Bitte schreiben Sie keine Namen oder andere Angaben auf den Bogen, die Sie oder andere "
                  "erkennbar machen.",
            "en": f"Questionnaires on paper: your handwritten answers may be sent as image sections to the AI service "
                  f"{to}, which transcribes them; the researchers check every transcription. Only the sections of the "
                  "writing fields are sent, without your other answers and without the number of the sheet. Please "
                  "do not write names or other details on the sheet that make you or others recognisable."}


def ai_confirmed(survey):
    """Whether the participants were told about the AI reading for the recipient this person's AI goes to (K3,
    P-S6, decision 6a: a confirmation names its recipient)."""
    from app import ai_access

    return ai_access.confirmed(settings(survey)["ai_ok"], ai_access.recipient_key())


def ai_pending(survey):
    """The recipients of the team not confirmed yet, each with its paragraph, for the owner and the admins of the
    survey; [] for everyone else (P-S6)."""
    from app import ai_access

    if not ai_access.may_confirm(survey):
        return []
    stored = settings(survey)["ai_ok"]
    return [{**r, "notice": ai_notice(to=r["name"] if r["own"] else None)}
            for r in ai_access.team_recipients(survey) if r["own"] and not ai_access.confirmed(stored, r["key"])]


def ai_access_own():
    """The researcher's own AI access (app/ai_access.py), or None: then the AI reading is not offered."""
    from app import ai_access

    return ai_access.active()


def read_with_ai(survey, sheet):
    """Have the AI transcribe the handwriting of a sheet that nobody typed in yet: the texts go into the sheet's values
    as suggestions (marked "_ai") and are committed. Returns how many fields it read, None if the sheet was erased,
    thrown away or taken over while the AI read. Raises ai.AIError."""
    from PIL import Image

    from app import ai

    if ai_access_own() is None:  # never over the installation's key (Ralf, 2026-10-21), also for a form from before
        raise ai.AIError(de_en("Die KI liest Handschrift nur über einen eigenen KI-Zugang (Konto › Verbindungen), "
                               "nicht über den Zugang der Plattform.",
                               "The AI reads handwriting only over an own AI access (Account › Connections), not over "
                               "the platform's access."))
    found = evaluate(survey, sheet)
    wanted = [e for e in found["fields"] if e["k"] == "write" and e["state"] == "ink" and not e.get("value")][:AI_MAX]
    if not wanted:
        return 0
    parts = []
    for e in wanted:
        page = found["pages"].get(e["p"])
        path = scan_path(survey, page.filename) if page else None
        if not path or not os.path.isfile(path):
            continue
        with Image.open(path) as img:
            cut = paper_scan.snippet(img, page.reading["H"], e, pad=1.0, scale=6.0)
        out = io.BytesIO()
        cut.save(out, "PNG")
        group = found["groups"].get(e.get("g")) or {}
        what = " · ".join(t for t in (group.get("q"), e.get("lab")) if t)
        parts.append({"text": f"Field {e['i']}: {what}"[:300]})
        parts.append({"png": out.getvalue()})
    if not parts:
        return 0
    images = sum(1 for p in parts if "png" in p)
    data = ai.json_answer(ai.generate_text(AI_INSTRUCTIONS, parts, purpose="paper_read", schema=AI_SCHEMA,
                                           units=-(-images // AI_UNIT)))
    allowed = {e["i"] for e in wanted}
    # the AI took seconds: the sheet read anew under the lock, so that a deletion (sheet_erase, a deletion code) or a
    # discard in the meantime is not undone and what someone typed meanwhile is kept (code review 2026-10-15, D3)
    with _LOCK:
        sheet = _locked(sheet.id, ("scanned", "review"))
        if sheet is None:
            db.session.rollback()
            return None
        values, marked = dict(sheet.values or {}), set((sheet.values or {}).get("_ai") or [])
        n = 0
        for entry in data.get("fields") or []:
            if not isinstance(entry, dict) or entry.get("n") not in allowed or values.get(str(entry["n"])):
                continue
            values[str(entry["n"])] = str(entry.get("text") or "").strip()[:2000]
            marked.add(entry["n"])
            n += 1
        values["_ai"] = sorted(marked)
        sheet.values = values
        db.session.commit()
    return n


def review_choices():
    return [("unsure", de_en("nur Unsicheres und Handschrift prüfen", "check only what is uncertain and handwriting")),
            ("all", de_en("jeden Bogen prüfen", "check every sheet")),
            ("none", de_en("ohne Prüfung übernehmen (Handschrift bleibt leer)",
                           "accept without checking (handwriting stays empty)")),
            ("four", de_en("jeden Bogen von zwei Personen prüfen (Vier-Augen-Prinzip)",
                           "check every sheet by two people (four-eyes principle)"))]


def keep_choices():
    return [("0", de_en("sofort nach der Übernahme löschen", "delete right after the takeover")),
            ("30", de_en("30 Tage nach der Übernahme löschen", "delete 30 days after the takeover")),
            ("answers", de_en("so lange wie die Antworten behalten", "keep as long as the answers"))]


def root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "paper-scans")


def folder(survey):
    return os.path.join(root(), survey.uid)


def _inbox(survey):
    return os.path.join(folder(survey), "inbox")


def scan_path(survey, name):
    if not name or not NAME_RE.match(name):
        return None
    return os.path.join(folder(survey), name)


def run_pdf_path(survey, run):
    return os.path.join(folder(survey), f"run-{run.id}.pdf")


def languages(survey):
    """The survey's languages that can go on paper."""
    return [lang for lang in survey.languages if lang in paper_pdf.LANGUAGES]


# --- print runs ----------------------------------------------------------------------------------


def _new_key(taken=()):
    while True:
        key = secrets.randbits(40)
        if key and key not in taken and db.session.scalar(db.select(PaperSheet.id).filter_by(key=key)) is None:
            return key


def create_run(survey, lang, count, user, duplex=False, size="large"):
    """A print run of `count` sheets: their layouts, numbers and plans, and its PDF (duplex: for printing on both
    sides, paper_pdf.render; size: type size, paper_pdf.SCALES). Returns the run.

    Everything is drawn and the PDF made before the first row is written, so the write transaction lasts only for
    storing the rows and the file (audit 2026-10-11, M11: under SQLite every participant waited for the whole PDF)."""
    import random

    from app import runtime

    plans, sheets, drawn, rows, keys = {}, [], {}, [], set()
    rng = random.SystemRandom()
    keep = bool(getattr(survey, "erase_code", False))  # a deletion code on a page the participant keeps (E15)
    if keep:
        from app import brand, erasure

        url = (brand.participant_base_url() or brand.public_base()).rstrip("/") + "/privacy/delete"
    for n in range(1, count + 1):
        # as online: a separated section (contact data for a prize draw) stays apart from the answers, so it is
        # not printed on the sheet (the layout ends at its marker)
        layout = runtime.build_layout(survey, rng, separate=True)
        shape = repr(sorted((k, repr(v)) for k, v in layout.items() if k != "sig"))
        if shape not in drawn:
            pages, plan = paper_pdf.build_sheet(survey, lang, layout, size=size)
            key = paper_pdf.plan_key(plan)
            plans[key] = plan
            drawn[shape] = (pages, key)
        pages, key = drawn[shape]
        sheet = {"key": _new_key(keys), "number": n, "layout": layout, "plan": key, "pages": len(pages),
                 "pid": new_pid(survey.code), "erase_hash": None}
        keys.add(sheet["key"])
        lines = None
        if keep:
            code = erasure.raw_code(survey, sheet["pid"])
            sheet["erase_hash"] = erasure.digest(code)  # a request before the sheet is scanned finds it (erase_unscanned)
            lines = paper_pdf.keep_lines(lang, erasure.written(survey, code), paper_pdf.sheet_number(sheet["key"]), url)
        rows.append(sheet)
        sheets.append((sheet["key"], pages, lines))
    pdf = paper_pdf.render(sheets, paper_pdf.plain(survey.title_in(lang)), lang, duplex=duplex)
    run = PaperRun(survey_id=survey.id, language=lang, qversion=survey.qversion or 1, count=count,
                   created_by=user.id, plans=plans)
    db.session.add(run)
    db.session.flush()
    for row in rows:
        db.session.add(PaperSheet(run_id=run.id, survey_id=survey.id, state="printed", **row))
    db.session.flush()
    os.makedirs(folder(survey), exist_ok=True)
    with writing(survey, len(pdf)), open(run_pdf_path(survey, run), "wb") as f:
        f.write(pdf)
    return run


# --- uploads -------------------------------------------------------------------------------------


def _looks_like(ext, data):
    """Does the start of the file fit its ending? (Pillow and pypdf check it again; this refuses early.)"""
    if ext == ".pdf":
        return paper_scan.is_pdf(data)
    if ext in (".jpg", ".jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    if ext == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    return data[:4] in (b"II*\x00", b"MM\x00*")


def accept_upload(survey, files, user):
    """Put the uploaded files into the inbox and start reading them, one after another in a low-priority child process. Every file is
    checked before any is written (P-S8). Returns the number of files."""
    wanted = []
    for f in files:
        data = f.read()
        if not data:
            continue
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in EXTENSIONS or not _looks_like(ext, data):
            raise ValueError(de_en("Nur PDF, JPEG, PNG oder TIFF; „{f}“ ist keine solche Datei. Es wurde nichts hochgeladen.",
                                   "PDF, JPEG, PNG or TIFF only; “{f}” is not such a file. Nothing was uploaded.",
                                   f=(f.filename or "")[:80]))
        wanted.append((f.filename or "", ext, data))
    from app import media

    names = []
    # Reserve both the scans and their progress metadata before any part of this batch is visible.
    with media.writing(survey, sum(len(d) + 1024 for _, _, d in wanted)):
        os.makedirs(_inbox(survey), exist_ok=True)
        try:
            for original, ext, data in wanted:
                name = secrets.token_urlsafe(12) + ext
                names.append(name)
                with open(os.path.join(_inbox(survey), name), "wb") as out:
                    out.write(data)
                _progress(survey, name, {"file": original[:80], "done": 0, "total": None, "by": user.id})
        except OSError:
            for name in names:
                for p in (os.path.join(_inbox(survey), name), os.path.join(_inbox(survey), name + ".json")):
                    try:
                        remove_file(p)
                    except OSError:
                        pass
            raise
    app = current_app._get_current_object()
    if names:
        if app.config.get("TESTING") or app.config.get("PAPER_SYNC"):
            process(app, survey.id, names)
        else:
            from app import paper_worker

            try:
                paper_worker.start(app, survey.id, names)
            except (OSError, ValueError):
                for name in names:
                    for path in (os.path.join(_inbox(survey), name), os.path.join(_inbox(survey), name + ".json")):
                        try:
                            remove_file(path)
                        except FileNotFoundError:
                            pass
                raise
    return len(names)


@tracked
def _progress(survey, name, values, inbox=None):
    import json

    path = os.path.join(inbox or _inbox(survey), name + ".json")
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**old, **values, "beat": utcnow().isoformat()}, f)


def uploads(survey):
    """The uploads still being read: [{"file", "done", "total", "stale"}]."""
    import json
    from datetime import datetime

    out = []
    inbox = _inbox(survey)
    if not os.path.isdir(inbox):
        return out
    for name in sorted(os.listdir(inbox)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(inbox, name), encoding="utf-8") as f:
                state = json.load(f)
            beat = datetime.fromisoformat(state.get("beat"))
        except (OSError, ValueError, TypeError):
            continue
        out.append({**state, "stale": utcnow() - beat > timedelta(minutes=STALE_MINUTES)})
    return out


def _keep_alive(inbox, names):
    """Files still waiting to be read: the upload is alive, the hourly cleaning (run) leaves them alone."""
    for name in names:
        try:
            os.utime(os.path.join(inbox, name))
            _progress(None, name, {}, inbox)
        except OSError:
            pass


def process(app, survey_id, names):
    """Read the files of one upload, one after another: every page found, stored and measured; then the sheets they
    touched are settled. Runs in its own process; at most SCANNING of them read at once in a process (P-S1)."""
    if isinstance(names, str):
        names = [names]
    with app.app_context():
        from app import lang

        lang.current()  # cache request language before per-page commits expire/detach the login object
        survey = db.session.get(Survey, survey_id)
        if survey is None:
            return
        inbox = _inbox(survey)
        db.session.remove()  # no connection held while waiting or decoding (P-S2)
        while not _SCANS.acquire(timeout=30):
            _keep_alive(inbox, names)
        try:
            touched, left = set(), MAX_PAGES
            for i, name in enumerate(names):
                left -= _process_file(survey_id, inbox, name, left, touched, waiting=names[i + 1:])
            for sheet_id in sorted(touched):
                try:
                    settle_locked(survey_id, sheet_id)
                except Exception:  # noqa: BLE001 - one sheet must not keep the others from being settled
                    db.session.rollback()
                    current_app.logger.exception("Paper sheet %s of survey %s could not be settled", sheet_id, survey_id)
        finally:
            _SCANS.release()
            db.session.remove()


def settle_locked(survey_id, sheet_id):
    """`settle` for a sheet read anew under its lock, committed: two uploads with pages of the same sheet (fronts and
    backs as two files) take it over once (P-S3)."""
    with _LOCK:
        sheet = _locked(sheet_id, ("scanned", "review"))
        if sheet is not None:
            settle(db.session.get(Survey, survey_id), sheet)
        db.session.commit()


def _process_file(survey_id, inbox, name, left, touched, waiting=()):
    """One file of an upload: its pages decoded one by one, outside any transaction, and made small at once; each
    stored in a short transaction of its own (P-S2). Returns the number of pages read. `waiting`: the files of the
    same upload after this one, kept alive with every page, or a long first file lets the cleaning take them
    (code review 2026-10-15, J2)."""
    import json

    path = os.path.join(inbox, name)
    n, kept = 0, time.monotonic()
    try:
        by = None
        try:
            with open(path + ".json", encoding="utf-8") as f:
                by = json.load(f).get("by")
        except (OSError, ValueError):
            pass
        with open(path, "rb") as f:
            data = f.read()
        try:
            pages = paper_scan.pages_from_upload(name, data, left)
            page = next(pages, None)
        except paper_scan.Unreadable:
            pages, page = iter(()), (None, "file")
        while page is not None and n < left:
            img, why = page
            found = None
            if img is not None:
                try:
                    found = paper_scan.locate(img)  # recognised before the database is touched
                except paper_scan.Unreadable as e:
                    found = e
            survey = db.session.get(Survey, survey_id)
            if survey is None:
                break
            with _LOCK:
                try:
                    sheet_id = _store_page(survey, img, by, why, found)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise
            db.session.close()  # the connection goes back while the next page is decoded
            if sheet_id is not None:
                touched.add(sheet_id)
            n += 1
            _progress(None, name, {"done": n}, inbox)
            if waiting and time.monotonic() - kept > 60:
                _keep_alive(inbox, waiting)
                kept = time.monotonic()
            img = found = page = None
            try:
                page = next(pages, None)
            except paper_scan.Unreadable:
                page = None
    except Exception:  # noqa: BLE001 - a broken upload must not take the thread's process down
        db.session.rollback()
        current_app.logger.exception("Paper upload %s of survey %s failed", name, survey_id)
    finally:
        for p in (path, path + ".json"):
            try:
                remove_file(p)
            except OSError:
                pass
        db.session.close()
    return n


def _save_image(survey, img):
    import io

    from app import media

    os.makedirs(folder(survey), exist_ok=True)
    name = secrets.token_urlsafe(12) + ".jpg"
    image = io.BytesIO()
    img.save(image, "JPEG", quality=82, optimize=True)  # decode outside admission; write measured bytes under it
    value = image.getvalue()
    with media.writing(survey, len(value)), open(os.path.join(folder(survey), name), "xb") as output:
        output.write(value)
    return name


def _locked(sheet_id, states=None):
    """The sheet, read anew with its row locked until the commit (PostgreSQL; SQLite has one writer anyway), or None
    if it is gone or not in one of `states`. Call it under _LOCK, which covers the threads of this process (P-S3)."""
    sheet = db.session.scalar(db.select(PaperSheet).where(PaperSheet.id == sheet_id).with_for_update()
                              .execution_options(populate_existing=True))
    if sheet is None or (states is not None and sheet.state not in states):
        return None
    return sheet


def _store_page(survey, img, by, why=None, found=None):
    """One page of an upload, already decoded and located (`found`: what paper_scan.locate gave, or the Unreadable
    it raised): named, stored and measured. Call under _LOCK. Returns the id of its sheet (or None)."""
    page = PaperPage(survey_id=survey.id, uploaded_by=by, state="unreadable")
    db.session.add(page)
    if img is None:
        page.reading = {"why": why or "file"}
        return None
    if found is None:
        try:
            found = paper_scan.locate(img)
        except paper_scan.Unreadable as e:
            found = e
    if isinstance(found, paper_scan.Unreadable):
        page.filename = _save_image(survey, img)
        page.reading = {"why": found.code}
        return None
    sheet_id = db.session.scalar(db.select(PaperSheet.id).filter_by(key=found["key"]))
    sheet = _locked(sheet_id) if sheet_id is not None else None
    if sheet is None or sheet.survey_id != survey.id:
        page.state, page.reading = "foreign", {"why": "foreign"}  # no image kept: it may be another survey's data
        return None
    if sheet.state in ("erased", "declined", "discarded"):
        page.state, page.reading = "dropped", {"why": sheet.state}  # deleted on request or thrown away: not stored
        page.sheet_id = sheet.id
        return None
    if found["page"] >= sheet.pages:
        page.reading = {"why": "code"}
        page.filename = _save_image(survey, img)
        return None
    same = db.session.scalar(db.select(PaperPage).filter_by(sheet_id=sheet.id, page=found["page"], state="read"))
    plan = (db.session.get(PaperRun, sheet.run_id).plans or {}).get(sheet.plan) or {"fields": []}
    fields = [(n, f) for n, f in enumerate(plan["fields"]) if f["p"] == found["page"]]
    if same is not None or sheet.state == "done":
        # kept LOOSE_DAYS with its image, like the other loose pages: it may be a second sheet filled in by someone
        # else (a copy of a blank one), whose answers would otherwise be lost unseen. Where the page read is still
        # there, the fields that read differently are counted (audit 2026-10-12, D-11)
        page.state, page.reading = "double", {"why": "double", "sheet": sheet.id, "page": found["page"]}
        page.filename = _save_image(survey, img)
        if same is not None and (same.reading or {}).get("f") is not None:
            page.reading = {**page.reading, "differs": _differences(fields, same.reading["f"], paper_scan.measure(img, found["H"], fields))}
        return None
    page.sheet_id, page.page, page.state = sheet.id, found["page"], "read"
    page.filename = _save_image(survey, img)
    shares = paper_scan.measure(img, found["H"], fields)
    page.reading = {"H": found["H"], "f": shares}
    digits = {}
    for n, f in fields:  # numbers written by hand: a local suggestion (paper_digits, E8 B)
        if f["k"] == "write" and f.get("fmt") in ("int", "num") and paper_scan.state(f, shares.get(str(n))) == "ink":
            try:
                text = paper_digits.read_number(img, found["H"], f)
            except Exception:  # noqa: BLE001 - no suggestion is no harm
                text = None
            if text:
                digits[str(n)] = text
    if digits:
        page.reading["d"] = digits
    if sheet.state == "printed":
        sheet.state = "scanned"
    return sheet.id


def _differences(fields, read, shares):
    """How many fields of a page scanned twice read differently from the page read (a tick here and none there, ink
    in one only); unclear ticks do not count."""
    differs = 0
    for n, f in fields:
        a, b = paper_scan.state(f, read.get(str(n))), paper_scan.state(f, shares.get(str(n)))
        if "?" not in (a, b) and a != b:
            differs += 1
    return differs


def _drop_doubles(survey, sheet):
    """The pages that came as a second scan of this sheet (state "double", kept for a look): they go when the sheet is
    deleted on request."""
    for page in db.session.scalars(db.select(PaperPage).filter_by(survey_id=survey.id, state="double")):
        if (page.reading or {}).get("sheet") == sheet.id:
            _remove_file(survey, page.filename)
            db.session.delete(page)


# --- a sheet's answers ---------------------------------------------------------------------------


def plan_of(sheet):
    run = db.session.get(PaperRun, sheet.run_id)
    return (run.plans or {}).get(sheet.plan) or {"pages": sheet.pages, "fields": [], "groups": {}}


def pages_of(sheet):
    """{page number: PaperPage} of the pages read."""
    return {p.page: p for p in db.session.scalars(db.select(PaperPage).filter_by(sheet_id=sheet.id, state="read"))}


class Form(dict):
    """A MultiDict stand-in for the parsers of the item types (get / getlist), as dummy data use."""

    def get(self, key, default=None):
        value = super().get(key, default)
        return value[0] if isinstance(value, list) and value else value

    def getlist(self, key):
        value = super().get(key, [])
        return value if isinstance(value, list) else [value]


def evaluate(survey, sheet, values=None):
    """What the sheet says: {"fields": [{..field, "i", "state", "share", "value"}], "groups": {key: {...group,
    "fields", "issues"}}, "missing": [pages not scanned], "forms": {layout position: form}, "issues": n,
    "writing": n}. `values`: what was set by hand (default: the sheet's)."""
    from app import runtime
    from app.models import Item

    values = sheet.values or {} if values is None else values
    ai_read = set(values.get("_ai") or [])
    plan = plan_of(sheet)
    pages = pages_of(sheet)
    fields = []
    for n, f in enumerate(plan["fields"]):
        page = pages.get(f["p"])
        share = ((page.reading or {}).get("f") or {}).get(str(n)) if page else None
        state = paper_scan.state(f, share) if page else "o"
        entry = {**f, "i": n, "share": share, "state": state, "set": str(n) in values}
        if f["k"] == "box":
            if str(n) in values:
                entry["state"] = "x" if values[str(n)] == "x" else "o"
        else:
            entry["value"] = str(values.get(str(n), "")) if str(n) in values else None
            entry["ai"] = n in ai_read
            entry["suggest"] = ((page.reading or {}).get("d") or {}).get(str(n)) if page else None
        fields.append(entry)
    groups = {}
    for entry in fields:
        g = groups.setdefault(entry.get("g") or f"_{entry['i']}", {**(plan["groups"].get(entry.get("g")) or {}),
                                                                     "key": entry.get("g"), "fields": [], "issues": []})
        g["fields"].append(entry)
    review = settings(survey)["review"]
    for key, g in groups.items():
        boxes = [e for e in g["fields"] if e["k"] == "box"]
        if any(e["state"] == "?" for e in boxes):
            g["issues"].append(de_en("Kreuz nicht eindeutig", "tick not clear"))
        if g.get("one") and sum(1 for e in boxes if e["state"] == "x") > 1:
            g["issues"].append(de_en("mehrere Kreuze, nur eines erlaubt", "several ticks, only one allowed"))
        for e in g["fields"]:
            if e["k"] == "write" and e["state"] == "ink" and e["value"] is None:
                if review != "none":
                    g["issues"].append(de_en("Handschrift abtippen", "type in the handwriting"))
                    break
                if e.get("rank") or e.get("fmt") in ("int", "num"):
                    # "ohne Prüfung" leaves free text empty, but ranks and numbers are answers to closed questions:
                    # they are put before a person all the same (decision 10 of the audit 2026-10-12)
                    g["issues"].append(de_en("Rangplätze und Zahlen abtippen", "type in the ranks and numbers"))
                    break
    # the answers per page of the layout, as the page would send them
    forms, ranks = {}, {}
    for e in fields:
        form = forms.setdefault(e["pos"], Form())
        if e["k"] == "box":
            if e["state"] == "x":
                form.setdefault(e["f"], []).append(e["v"])
        elif e.get("rank"):
            text = (e["value"] or "").strip()
            if text:
                ranks.setdefault((e["pos"], e["f"]), []).append((text, e["rank"], e.get("g")))
        elif e["value"]:
            form[e["f"]] = e["value"].strip()
    printed_ranks = {}
    for e in fields:
        if e["k"] == "write" and e.get("rank"):
            printed_ranks[(e["pos"], e["f"])] = printed_ranks.get((e["pos"], e["f"]), 0) + 1
    for (pos, field), given in ranks.items():
        issues = groups[given[0][2]]["issues"] if given[0][2] in groups else None
        try:
            numbers = [float(t[0].replace(",", ".")) for t in given]
        except ValueError:
            groups[given[0][2]]["issues"].append(de_en("Rangplätze müssen Zahlen sein", "ranks must be numbers"))
            continue
        if issues is not None and len(set(numbers)) < len(numbers):
            # the same rank twice (1, 1, 2): not made into an order by the printed sequence (D-8)
            issues.append(de_en("Rangplätze doppelt vergeben: bitte eindeutig machen",
                                "the same rank given twice: please make them distinct"))
        if issues is not None and len(given) < printed_ranks.get((pos, field), 0):
            # only some ranks written (1 and 2, the rest empty): a partial order is not stored, so a person decides
            # (decision 5 of the audit 2026-10-12, D-9)
            issues.append(de_en("Rangplätze unvollständig: ergänzen oder alle leeren (eine unvollständige Rangfolge "
                                "wird nicht gespeichert)",
                                "ranks incomplete: complete them or clear them all (an incomplete order is not stored)"))
        order = sorted(given, key=lambda t: float(t[0].replace(",", ".")))
        forms[pos][field] = [code for _, code, _ in order]
        forms[pos][f"{field}_touched"] = "1"
    for form in forms.values():  # a constant sum written in counts as confirmed
        for field in [k for k in form if k.startswith("i") and "_" in k and k.split("_")[0][1:].isdigit()]:
            item_field = field.split("_")[0]
            item = db.session.get(Item, int(item_field[1:]))
            if item is not None and item.type == "constant_sum":
                form[f"{item_field}_touched"] = "1"
    # every question checked as the page engine would check it, without asking for required answers
    lang = db.session.get(PaperRun, sheet.run_id).language
    for key, g in groups.items():
        if not g.get("item") or g["issues"]:
            continue
        item = db.session.get(Item, g["item"])
        if item is None:
            continue
        pos = g["fields"][0]["pos"]
        result = runtime.evaluate([runtime._AsOptional(item)], forms.get(pos, Form()), lang, nudge_acknowledged=True,
                                  meta=(sheet.layout or {}).get("pages", [{}])[pos] if pos < len((sheet.layout or {}).get("pages", [])) else {},
                                  answers={})
        if result.errors.get(item.id):
            g["issues"].append(str(result.errors[item.id]))
    missing = [n for n in range(sheet.pages) if n not in pages]
    issues = sum(1 for g in groups.values() if g["issues"])
    writing = sum(1 for e in fields if e["k"] == "write" and e["state"] == "ink")
    return {"fields": fields, "groups": groups, "missing": missing, "forms": forms, "issues": issues,
            "writing": writing, "pages": pages, "changed": changed_since_print(survey, sheet)}


def changed_since_print(survey, sheet):
    """Was the questionnaire changed after this sheet was printed (another questionnaire version, or pages of another
    shape)? Then its answers, kept by printed page, are never taken over by themselves (code review 2026-10-15,
    J1)."""
    from app import runtime

    run = db.session.get(PaperRun, sheet.run_id)
    if run is not None and (run.qversion or 1) != (survey.qversion or 1):
        return True
    return bool(sheet.layout) and not runtime.layout_matches(survey, sheet.layout)


class Changed(Exception):
    """The answers of a sheet printed before a change cannot be put onto the pages of today's questionnaire."""


_FIELD_ITEM = re.compile(r"i(\d+)(?:_|\Z)")


def _page_key(spec):
    """What a page of a layout is, apart from its questions and conditions: its block, the pass of a loop, the task
    of a choice design."""
    return spec.get("block"), repr(sorted((k, repr(v)) for k, v in spec.items() if k not in ("items", "cond")))


def remap_forms(old, new, forms):
    """The answers per printed page (`forms`, by position in the printed layout `old`) by position in the layout
    `new`, matched by block and question ids, not by position; None if one printed page with answers has not exactly
    one page in `new` (code review 2026-10-15, J1)."""
    from app import runtime

    old_pages, new_pages = (old or {}).get("pages") or [], (new or {}).get("pages") or []
    out = {}
    for pos, form in forms.items():
        if not form:
            continue
        if pos >= len(old_pages) or runtime.is_marker(old_pages[pos]):
            return None
        printed = old_pages[pos]
        answered = {int(m.group(1)) for k in form for m in [_FIELD_ITEM.match(k)] if m} & set(printed.get("items") or [])
        if not answered:
            return None
        hits = [n for n, spec in enumerate(new_pages) if not runtime.is_marker(spec)
                and _page_key(spec) == _page_key(printed) and answered <= set(spec.get("items") or [])]
        if len(hits) != 1 or hits[0] in out:
            return None
        out[hits[0]] = form
    return out


def needs_review(survey, sheet, found):
    """Must a person look at this sheet before it is taken over?"""
    review = settings(survey)["review"]
    if found["missing"] or found.get("changed"):
        return True
    if review in ("all", "four"):
        return True  # every sheet is taken over by hand (sheet_save), "four" only by a second person
    if review == "none":
        return any(g["issues"] for g in found["groups"].values()
                   if not all("Handschrift" in i or "handwriting" in i for i in g["issues"]))
    return found["issues"] > 0


def settle(survey, sheet):
    """After new pages or a review: take the sheet over if nothing is left to check, else mark it for review."""
    if sheet.state not in ("scanned", "review"):
        return
    found = evaluate(survey, sheet)
    if not found["missing"] and _consent_state(survey, found) == "no":
        take_over(survey, sheet, found)  # clearly no consent: never stored, nothing to check (E14)
        return
    if needs_review(survey, sheet, found):
        sheet.state = "review"
        return
    take_over(survey, sheet, found)


def _consent_state(survey, found):
    """"yes" (ticked "agree" alone, or no consent on paper), "no" (not ticked, or "disagree") or "unclear"."""
    consent = survey.consent_item
    printed = [e for e in found["fields"] if consent is not None and e.get("item") == consent.id
               and e["f"] == f"i{consent.id}"]
    if not printed:
        return "yes"
    if any(e["state"] == "?" for e in printed):
        return "unclear"
    agree = any(e["state"] == "x" and e["v"] == "1" for e in printed)
    disagree = any(e["state"] == "x" and e["v"] == "0" for e in printed)
    return "unclear" if agree and disagree else "yes" if agree else "no"


def _consent_given(survey, sheet, found):
    return _consent_state(survey, found) == "yes"


def take_over(survey, sheet, found=None, user=None):
    """The sheet becomes a participation. Returns the Response, or None if it was declined."""
    from app import runtime

    found = found or evaluate(survey, sheet)
    run = db.session.get(PaperRun, sheet.run_id)
    if not _consent_given(survey, sheet, found):
        sheet.state, sheet.done_at = "declined", utcnow()
        drop_scans(survey, sheet)
        _count_declined(survey)
        return None
    draft = survey.status == Survey.DRAFT
    now = utcnow()
    layout, qversion, forms = sheet.layout, run.qversion, found["forms"]
    if found.get("changed") and not runtime.layout_matches(survey, layout):
        # printed before the questionnaire was changed: the layout of today's questionnaire, drawn as for a
        # participation under way (what it drew stays where it still exists), and the answers put onto its pages by
        # block and question, never by printed position (code review 2026-10-15, J1)
        from types import SimpleNamespace

        probe = SimpleNamespace(pid="paper", layout=layout, id=None, is_preview=draft, source="test" if draft else "real",
                                qversion=qversion, position=0, paradata=None)
        runtime.ensure_layout(survey, probe)
        forms = remap_forms(layout, probe.layout, forms)
        if forms is None:
            raise Changed(de_en("Der Fragebogen wurde nach dem Druck dieses Bogens geändert, und nicht jede Seite mit "
                                "Antworten lässt sich einer Seite des heutigen Fragebogens zuordnen. Der Bogen kann "
                                "nicht übernommen werden; tragen Sie die Antworten bei Bedarf von Hand ein.",
                                "The questionnaire was changed after this sheet was printed, and not every page with "
                                "answers can be matched to a page of today's questionnaire. The sheet cannot be taken "
                                "over; enter its answers by hand if needed."))
        layout, qversion = probe.layout, survey.qversion
    first = min((p.uploaded_at for p in found["pages"].values()), default=now)
    response = Response(survey_id=survey.id, language=run.language, answers={}, qversion=qversion,
                        layout=layout, pid=sheet.pid if sheet.pid and not _pid_taken(sheet.pid) else None, status=Response.ACTIVE, position=0, started_at=now,
                        source="test" if draft else "real", is_preview=draft, channel="paper",
                        paper={"sheet": paper_pdf.sheet_number(sheet.key), "run": run.id, "scanned": first.isoformat(),
                               "checked": sheet.checked_at is not None,
                               "second_check": bool((sheet.values or {}).get("_second")),
                               "checked_at": sheet.checked_at.isoformat() if sheet.checked_at else None,
                               "changes": sum(1 for e in found["fields"] if e.get("set"))})
    if sheet.erase_hash and not draft and getattr(survey, "erase_code", False):
        response.erase_hash = sheet.erase_hash  # the code on the page kept deletes this participation
    db.session.add(response)
    db.session.flush()
    runtime.ensure_layout(survey, response)
    lang = run.language
    for _ in range(4 * len((response.layout or {}).get("pages") or []) + 10):
        if response.status != Response.ACTIVE:
            break
        runtime.step(survey, response, Form(), lang, "GET", paper=True)
        if response.status != Response.ACTIVE:
            break
        position = response.position or 0
        fields = Form({"nav": "next", "nudge_ack": "1", **forms.get(position, {})})
        runtime.step(survey, response, fields, lang, "POST", validate=False, paper=True)
        if (response.position or 0) == position and response.status == Response.ACTIVE:
            break  # the page did not go on (should not happen without checks): stop rather than loop
    response.started_at = response.completed_at or now
    sheet.state, sheet.response_id, sheet.done_at = "done", response.id, now
    if user is not None:
        sheet.checked_by = user.id
    if settings(survey)["keep"] == "0":
        drop_scans(survey, sheet)
    return response


def _pid_taken(pid):
    return db.session.scalar(db.select(Response.id).filter_by(pid=pid)) is not None


def _count_declined(survey):
    """A sheet without consent counts like a declined consent online, as a bare number (participate.py does the same,
    without moving updated_at)."""
    if survey.status == Survey.DRAFT:
        return
    db.session.execute(db.update(Survey).where(Survey.id == survey.id)
                       .values(declined_count=db.func.coalesce(Survey.declined_count, 0) + 1,
                               updated_at=Survey.updated_at))


# --- deleting ------------------------------------------------------------------------------------


def drop_scans(survey, sheet):
    """The scans of a sheet go, and what was typed from them (the answers keep it)."""
    for page in db.session.scalars(db.select(PaperPage).filter_by(sheet_id=sheet.id)):
        _remove_file(survey, page.filename)
        db.session.delete(page)
    sheet.values = None


@tracked
def _remove_file(survey, name):
    path = scan_path(survey, name)
    if path:
        try:
            remove_file(path)
        except OSError:
            pass


def erase_unscanned(text, survey=None):
    """A deletion code typed (public.erase, participate.erase) for a sheet not taken over yet: the sheet is marked
    deleted, its scans go, a later scan of it is refused, and the researchers are asked to destroy the paper. Returns
    the survey, or None if no such sheet."""
    from app import erasure

    code = erasure.normalise(text)
    if code is None:
        return None
    open_states = ("printed", "scanned", "review")
    sheet_id = db.session.scalar(db.select(PaperSheet.id).where(PaperSheet.erase_hash == erasure.digest(code),
                                                                PaperSheet.state.in_(open_states)))
    if sheet_id is None:
        return None
    with _LOCK:  # an upload taking this sheet over at the same moment must not turn "erased" into "done" (P-S3)
        sheet = _locked(sheet_id, open_states)
        if sheet is None or (survey is not None and sheet.survey_id != survey.id):
            db.session.rollback()
            return None
        survey = db.session.get(Survey, sheet.survey_id)
        if survey is None or not getattr(survey, "erase_code", False):
            db.session.rollback()
            return None
        drop_scans(survey, sheet)
        _drop_doubles(survey, sheet)
        sheet.state, sheet.destroy = "erased", True
        from app.timeutil import to_local

        erasure.count(survey.id, to_local(utcnow()).strftime("%Y-%m-%d"), "self", 1, 0)
        db.session.commit()
    return survey


def forget_responses(survey, responses):
    """Participations deleted (on request, by the researchers): their sheets lose their scans, and the paper is to be
    destroyed."""
    ids = [r.id for r in responses if getattr(r, "channel", None) == "paper"]
    if not ids:
        return
    for sheet in db.session.scalars(db.select(PaperSheet).where(PaperSheet.survey_id == survey.id,
                                                                PaperSheet.response_id.in_(ids))):
        drop_scans(survey, sheet)
        _drop_doubles(survey, sheet)
        sheet.state, sheet.destroy, sheet.response_id = "erased", True, None


def forget_survey_scans(survey):
    """A retention period is over: every scan of the survey goes, and what was typed from them, and the PDFs of the
    print runs (they pair sheet numbers with deletion codes, audit 2026-10-10, P-S9)."""
    if not db.session.scalar(db.select(PaperSheet.id).filter_by(survey_id=survey.id).limit(1)):
        return
    for run in db.session.scalars(db.select(PaperRun).filter_by(survey_id=survey.id)):
        _drop_run_pdf(survey, run)
    for page in db.session.scalars(db.select(PaperPage).filter_by(survey_id=survey.id)):
        _remove_file(survey, page.filename)
        db.session.delete(page)
    db.session.execute(db.update(PaperSheet).where(PaperSheet.survey_id == survey.id).values(values=None))


def forget_purged(survey):
    """The period "answers" is over and every participation goes: the sheets taken over keep their state "done" (a
    sheet scanned again is still refused) but no longer name a participation, whose number SQLite could give to a new
    one in another survey (audit 2026-10-21, B1)."""
    db.session.execute(db.update(PaperSheet).where(PaperSheet.survey_id == survey.id,
                                                   PaperSheet.response_id.is_not(None)).values(response_id=None))


@tracked
def _drop_run_pdf(survey, run):
    """The PDF of a print run goes; True if there was one."""
    try:
        remove_file(run_pdf_path(survey, run))
        return True
    except OSError:
        return False


def _old_run_pdfs(survey, now):
    """The print runs whose PDF may go: no sheet of the run is still out or under way, and the last one was finished
    as long ago as the survey keeps its scans (at once, 30 days; "with the answers": until a retention period)."""
    keep = settings(survey)["keep"]
    if keep == "answers":
        return []
    out = []
    for run in db.session.scalars(db.select(PaperRun).filter_by(survey_id=survey.id)):
        if not os.path.isfile(run_pdf_path(survey, run)):
            continue
        open_, last = db.session.execute(db.select(
            db.func.count(PaperSheet.id).filter(PaperSheet.state.in_(("printed", "scanned", "review"))),
            db.func.max(PaperSheet.done_at)).where(PaperSheet.run_id == run.id)).one()
        if open_:
            continue
        if keep == "0" or last is None or last < now - timedelta(days=KEEP_DAYS):
            out.append(run)
    return out


def delete_survey(survey):
    """The survey goes: its rows (the folder goes with the survey's folders, lifecycle.delete)."""
    db.session.execute(db.delete(PaperPage).where(PaperPage.survey_id == survey.id))
    db.session.execute(db.delete(PaperSheet).where(PaperSheet.survey_id == survey.id))
    db.session.execute(db.delete(PaperRun).where(PaperRun.survey_id == survey.id))


def run(now=None):
    """Housekeeping: scans past their time, loose pages after a week, broken-off uploads. Returns how many went."""
    now = now or utcnow()
    gone = 0
    loose = db.session.scalars(db.select(PaperPage).where(PaperPage.sheet_id.is_(None) | (PaperPage.state != "read"),
                                                          PaperPage.uploaded_at < now - timedelta(days=LOOSE_DAYS)))
    surveys = {}
    for page in loose:
        survey = surveys.get(page.survey_id) or db.session.get(Survey, page.survey_id)
        surveys[page.survey_id] = survey
        if survey is not None:
            _remove_file(survey, page.filename)
        db.session.delete(page)
        gone += 1
    for sheet in db.session.scalars(db.select(PaperSheet).where(PaperSheet.state == "done",
                                                                PaperSheet.done_at < now - timedelta(days=KEEP_DAYS))):
        survey = surveys.get(sheet.survey_id) or db.session.get(Survey, sheet.survey_id)
        surveys[sheet.survey_id] = survey
        if survey is None or settings(survey)["keep"] == "answers":
            continue
        if db.session.scalar(db.select(PaperPage.id).filter_by(sheet_id=sheet.id).limit(1)) is None and sheet.values is None:
            continue
        drop_scans(survey, sheet)
        gone += 1
    for survey_id in db.session.scalars(db.select(PaperRun.survey_id).distinct()):  # print runs done with (P-S9)
        survey = surveys.get(survey_id) or db.session.get(Survey, survey_id)
        surveys[survey_id] = survey
        if survey is not None:
            for paper_run in _old_run_pdfs(survey, now):
                gone += _drop_run_pdf(survey, paper_run)
    if os.path.isdir(root()):  # uploads whose thread ended without finishing (a restart)
        for uid in os.listdir(root()):
            inbox = os.path.join(root(), uid, "inbox")
            if not os.path.isdir(inbox):
                continue
            for name in os.listdir(inbox):
                path = os.path.join(inbox, name)
                try:
                    if (now - _mtime(path)) > timedelta(minutes=STALE_MINUTES):
                        remove_file(path)
                        gone += 1
                except OSError:
                    pass
        known = set(db.session.scalars(db.select(Survey.uid)))
        for uid in os.listdir(root()):
            if uid not in known:  # a deleted survey whose folder stayed
                remove_tree(os.path.join(root(), uid), ignore_errors=True)
    if gone:
        db.session.commit()
    return gone


def _mtime(path):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).replace(tzinfo=None)


# --- overview for the page "Datenerhebung" --------------------------------------------------------


def overview(survey):
    """What the block "Papierfragebogen" shows."""
    runs = list(db.session.scalars(db.select(PaperRun).filter_by(survey_id=survey.id).order_by(PaperRun.id.desc())))
    sheets = list(db.session.scalars(db.select(PaperSheet).filter_by(survey_id=survey.id).order_by(PaperSheet.id)))
    counts = {}
    for s in sheets:
        counts[s.state] = counts.get(s.state, 0) + 1
    scanned_pages = {}
    for sheet_id, n in db.session.execute(db.select(PaperPage.sheet_id, db.func.count(PaperPage.id)).where(
            PaperPage.survey_id == survey.id, PaperPage.state == "read").group_by(PaperPage.sheet_id)):
        scanned_pages[sheet_id] = n
    open_sheets = [{"sheet": s, "number": paper_pdf.sheet_number(s.key), "scanned": scanned_pages.get(s.id, 0)}
                   for s in sheets if s.state in ("scanned", "review")]
    # the sheets taken over last, to look through what was read (Ralf, 2026-10-11)
    done = sorted((s for s in sheets if s.state == "done"), key=lambda s: s.done_at or datetime.min, reverse=True)
    recent = [{"sheet": s, "number": paper_pdf.sheet_number(s.key), "scanned": scanned_pages.get(s.id, 0)}
              for s in done[:10]]
    loose = list(db.session.scalars(db.select(PaperPage).where(
        PaperPage.survey_id == survey.id, PaperPage.state.in_(("unreadable", "foreign", "double", "dropped")))
        .order_by(PaperPage.id.desc()).limit(50)))
    destroy = [{"sheet": s, "number": paper_pdf.sheet_number(s.key)} for s in sheets if s.destroy]
    return {"runs": runs, "pdfs": {r.id for r in runs if os.path.isfile(run_pdf_path(survey, r))}, "counts": counts, "open": open_sheets, "recent": recent, "loose": loose, "destroy": destroy,
            "uploads": uploads(survey), "settings": settings(survey), "languages": languages(survey),
            "left_out": paper_pdf.left_out(survey), "total": len(sheets)}


def why_label(code):
    return {"file": de_en("Datei nicht lesbar", "file not readable"),
            "small": de_en("Auflösung zu gering (mindestens 150 dpi)", "resolution too low (at least 150 dpi)"),
            "marks": de_en("Eckmarken nicht gefunden", "corner marks not found"),
            "code": de_en("Bogennummer nicht lesbar", "sheet number not readable"),
            "foreign": de_en("gehört nicht zu dieser Befragung", "does not belong to this survey"),
            "double": de_en("doppelt gescannt", "scanned twice"),
            "erased": de_en("Bogen auf Wunsch gelöscht", "sheet deleted on request"),
            "declined": de_en("ohne Einwilligung", "without consent"),
            "discarded": de_en("Bogen verworfen", "sheet discarded")}.get(code, code)


# --- routes --------------------------------------------------------------------------------------


def _survey(survey_id):
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="editor")
    if survey.live is not None or survey.interview is not None:
        abort(404)
    return survey


def _is_admin(survey):
    from app.models import SurveyMember

    role = survey.role_of(current_user)
    return role is not None and SurveyMember.LEVEL[role] >= SurveyMember.LEVEL["admin"]


def _back(survey):
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#paper")


def _sheet(survey, sheet_id):
    sheet = db.session.get(PaperSheet, sheet_id)
    if sheet is None or sheet.survey_id != survey.id:
        abort(404)
    return sheet


@bp.route("/<sid:survey_id>/paper/settings", methods=["POST"])
@login_required
@autosave.aware
def save_settings(survey_id):
    survey = _survey(survey_id)
    review, keep = request.form.get("review"), request.form.get("keep")
    # merged: the confirmation of the AI reading (ai_ok) stays (code review 2026-10-15, J3)
    survey.paper = {**(survey.paper or {}), "review": review if review in REVIEW else "all",
                    "keep": keep if keep in KEEP else "30"}
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/paper/runs", methods=["POST"])
@login_required
@heavy.limited  # up to MAX_SHEETS layouts and their PDF in the request (audit 2026-10-11, M11)
def new_run(survey_id):
    survey = _survey(survey_id)
    lang = request.form.get("language")
    count = request.form.get("count", type=int) or 0
    if lang not in languages(survey):
        flash(de_en("Diese Sprache gibt es auf Papier noch nicht.", "This language is not available on paper yet."), "error")
        return _back(survey)
    if not 1 <= count <= MAX_SHEETS:
        flash(de_en("Bitte 1 bis {n} Bögen.", "Please 1 to {n} sheets.", n=MAX_SHEETS), "error")
        return _back(survey)
    from app import design

    errors = design.blocking(design.check(survey))
    if errors:
        flash(de_en("Der Fragebogen hat noch Fehler; erst danach lässt er sich drucken: {e}",
                    "The questionnaire still has errors; it can be printed only after that: {e}",
                    e="; ".join(f.message for f in errors[:3]) + (" …" if len(errors) > 3 else "")), "error")
        return _back(survey)
    size = request.form.get("size") if request.form.get("size") in paper_pdf.SCALES else "large"
    run = create_run(survey, lang, count, current_user, duplex=request.form.get("duplex") == "1", size=size)
    db.session.commit()
    flash(de_en("{n} Bögen angelegt. Das PDF steht unter „1 · Drucken“.", "{n} sheets created. The PDF is under “1 · Print”.",
                n=count), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + f"#paper-run-{run.id}")


@bp.route("/<sid:survey_id>/paper/runs/<int:run_id>.pdf")
@login_required
def run_pdf(survey_id, run_id):
    survey = _survey(survey_id)
    run = db.session.get(PaperRun, run_id)
    if run is None or run.survey_id != survey.id or not os.path.isfile(run_pdf_path(survey, run)):
        abort(404)
    return send_file(run_pdf_path(survey, run), mimetype="application/pdf", as_attachment=True,
                     download_name=f"papierfragebogen-{survey.code}-{run.id}.pdf", max_age=0)


@bp.route("/<sid:survey_id>/paper/upload", methods=["POST"])
@login_required
def upload(survey_id):
    survey = _survey(survey_id)
    files = [f for f in request.files.getlist("scans") if f and f.filename]
    if not files:
        flash(de_en("Bitte eine Datei wählen.", "Please choose a file."), "error")
        return _back(survey)
    try:
        n = accept_upload(survey, files, current_user)
    except ValueError as e:
        flash(str(e), "error")
        return _back(survey)
    flash(de_en("{n} Datei(en) werden gelesen.", "{n} file(s) are being read.", n=n), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/paper/find", methods=["POST"])
@login_required
def find(survey_id):
    survey = _survey(survey_id)
    key = paper_pdf.parse_number(request.form.get("number"))
    sheet = db.session.scalar(db.select(PaperSheet).filter_by(key=key, survey_id=survey.id)) if key else None
    if sheet is None:
        flash(de_en("Keinen Bogen mit dieser Nummer gefunden.", "No sheet with this number found."), "error")
        return _back(survey)
    return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>")
@login_required
def sheet_page(survey_id, sheet_id):
    survey = _survey(survey_id)
    sheet = _sheet(survey, sheet_id)
    # a sheet taken over is shown as it was read and checked, read-only, as long as its scans are kept
    found = evaluate(survey, sheet) if sheet.state in ("scanned", "review", "done") else None
    if found is not None and sheet.state == "done" and not found["pages"]:
        found = None
    show_all = settings(survey)["review"] == "all" or request.args.get("all") == "1" or sheet.state == "done"
    from app import ai_access

    own = ai_access.active() is not None if ai_available() else False
    return render_template("surveys/paper_sheet.html", survey=survey, sheet=sheet, found=found, show_all=show_all,
                           paper_ai_on=ai_available(), ai_ok=ai_confirmed(survey), ai_own=own,
                           ai_notice=ai_notice(own) if ai_available() else None, ai_to=ai_access.recipient(),
                           ai_may_confirm=ai_access.may_confirm(survey),
                           ai_pending=ai_pending(survey) if ai_available() else [], ai_needs_own=ai_needs_own(),
                           number=paper_pdf.sheet_number(sheet.key), run=db.session.get(PaperRun, sheet.run_id),
                           next_id=_next_open(survey, sheet), may_erase=_is_admin(survey))


def _next_open(survey, sheet, mine=False):
    """The next sheet to check; mine: in the four-eyes check, one this person did not check first."""
    query = db.select(PaperSheet.id).where(PaperSheet.survey_id == survey.id, PaperSheet.state == "review",
                                           PaperSheet.id != sheet.id)
    if mine or settings(survey)["review"] == "four":
        query = query.where(db.or_(PaperSheet.checked_by.is_(None), PaperSheet.checked_by != current_user.id))
    return db.session.scalar(query.order_by(PaperSheet.id).limit(1))


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>", methods=["POST"])
@login_required
def sheet_save(survey_id, sheet_id):
    survey = _survey(survey_id)
    _sheet(survey, sheet_id)
    with _LOCK:  # two people (or a double click, or an upload) taking the same sheet over: only one does (P-S3)
        sheet = _locked(sheet_id, ("scanned", "review"))
        if sheet is None:
            db.session.rollback()
            abort(409)
        return _save_sheet(survey, sheet)


def _save_sheet(survey, sheet):
    found = evaluate(survey, sheet)
    values = dict(sheet.values or {})
    for g in found["groups"].values():
        boxes = [e for e in g["fields"] if e["k"] == "box"]
        key = f"g_{g['key']}"
        if boxes and key in request.form:
            chosen = set(request.form.getlist(key))
            for e in boxes:
                want = "x" if e["v"] in chosen else "o"
                if want != e["state"] or str(e["i"]) in values:
                    values[str(e["i"])] = want
        for e in g["fields"]:
            if e["k"] == "write" and f"w_{e['i']}" in request.form:
                typed = request.form.get(f"w_{e['i']}", "").strip()[:2000]
                if typed != (values.get(str(e["i"])) or ""):
                    values["_ai"] = [i for i in values.get("_ai") or [] if i != e["i"]]  # changed by hand
                values[str(e["i"])] = typed
    sheet.values = values
    action = request.form.get("action")
    if action == "take":
        found = evaluate(survey, sheet)
        if found["missing"] and not request.form.get("without_missing"):
            flash(de_en("Es fehlen noch Seiten dieses Bogens.", "Pages of this sheet are still missing."), "error")
            db.session.commit()
            return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))
        open_groups = [g for g in found["groups"].values() if g["issues"]
                       and not all("Handschrift" in i or "handwriting" in i for i in g["issues"])]
        if open_groups:
            flash(de_en("Noch {n} Stellen sind offen.", "{n} places are still open.", n=len(open_groups)), "error")
            db.session.commit()
            return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))
        if settings(survey)["review"] == "four" and _consent_state(survey, found) != "no":
            if sheet.checked_by is None:  # the first check: a second person takes it over
                sheet.checked_by, sheet.checked_at = current_user.id, utcnow()
                db.session.commit()
                flash(de_en("Erste Prüfung gespeichert. Übernommen wird der Bogen, wenn eine zweite Person ihn prüft.",
                            "First check saved. The sheet is accepted once a second person checks it."), "success")
                nxt = _next_open(survey, sheet, mine=True)
                return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=nxt) if nxt else
                                url_for("surveys.field", survey_id=survey.id) + "#paper")
            if sheet.checked_by == current_user.id:
                flash(de_en("Die zweite Prüfung macht eine andere Person.", "The second check is done by another person."),
                      "error")
                db.session.commit()
                return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))
            values = dict(sheet.values or {})
            values["_second"] = True  # who checked second is not kept, only that someone else did
            sheet.values = values
        sheet.checked_at = utcnow()
        try:
            response = take_over(survey, sheet, found, current_user if sheet.checked_by is None else None)
        except Changed as e:  # raised before anything of a participation was made: the typed values are kept
            flash(str(e), "error")
            db.session.commit()
            return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))
        db.session.commit()
        flash(de_en("Bogen übernommen.", "Sheet accepted.") if response is not None else
              de_en("Ohne Einwilligung: Der Bogen wurde nicht gespeichert.", "No consent: the sheet was not stored."),
              "success")
        nxt = _next_open(survey, sheet)
        if nxt:
            return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=nxt))
        return _back(survey)
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))


@bp.route("/<sid:survey_id>/paper/ai-informed", methods=["POST"])
@login_required
def ai_informed(survey_id):
    """The tick before the first AI reading: the participants were told their handwriting may go to the AI service."""
    from app import ai_access

    survey = _survey(survey_id)
    team = {r["key"]: r for r in ai_access.team_recipients(survey) if r["own"]}  # own accesses only (2026-10-21)
    to = team.get(request.form.get("to") or ai_access.recipient_key())
    if not ai_access.may_confirm(survey):  # P-S6, decision 6a: the owner or an admin of the survey, not "Bearbeiten"
        flash(de_en("Diese Bestätigung gibt, wem die Befragung gehört, oder ein Admin der Befragung.",
                    "This confirmation is given by the owner or an admin of the survey."), "error")
    elif not request.form.get("informed"):
        flash(de_en("Bitte bestätigen Sie, dass die Teilnehmenden informiert wurden.",
                    "Please confirm that the participants were told."), "error")
    elif to is None:
        flash(de_en("Diesen KI-Dienst nutzt keine beteiligte Person mehr.", "No one involved uses this AI service any more."),
              "error")
    else:  # K3: the confirmation names its recipient
        survey.paper = {**(survey.paper or {}), "ai_ok": ai_access.confirm(settings(survey)["ai_ok"], to["key"],
                                                                           to["name"], current_user.id)}
        db.session.commit()
    return redirect(_local(request.form.get("back")) or url_for("surveys.field", survey_id=survey.id) + "#paper")


def _local(target):
    """`target` if it is a path of this site (no other host, no scheme), else None (P-S7)."""
    from urllib.parse import urlsplit

    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target or any(c < " " for c in target):
        return None
    parts = urlsplit(target)
    return target if not parts.scheme and not parts.netloc else None


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>/read", methods=["POST"])
@login_required
def sheet_read(survey_id, sheet_id):
    """Read the handwriting of a sheet with AI (violet): a suggestion in every field until someone takes it over."""
    from app import ai

    survey = _survey(survey_id)
    sheet = _sheet(survey, sheet_id)
    if sheet.state not in ("scanned", "review") or not ai_available() or not ai_confirmed(survey):
        abort(404)
    try:
        n = read_with_ai(survey, sheet)
        if n is None:
            flash(de_en("Der Bogen wurde inzwischen gelöscht, verworfen oder übernommen; nichts gelesen.",
                        "The sheet was deleted, thrown away or accepted in the meantime; nothing was read."), "error")
            return _back(survey)
        flash(de_en("{n} Felder gelesen: bitte prüfen, erst „Übernehmen“ macht sie gültig.",
                    "{n} fields read: please check them; only “Accept” makes them count.", n=n), "success")
    except ai.AIError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id))


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>/discard", methods=["POST"])
@login_required
def sheet_discard(survey_id, sheet_id):
    """Throw a sheet away before it is taken over (e.g. a sheet filled in for practice)."""
    survey = _survey(survey_id)
    _sheet(survey, sheet_id)
    with _LOCK:
        sheet = _locked(sheet_id, ("printed", "scanned", "review"))
        if sheet is None:
            db.session.rollback()
            abort(409)
        if sheet.state in ("scanned", "review") and not survey.can(current_user, "admin"):
            db.session.rollback()
            abort(403)
        drop_scans(survey, sheet)
        sheet.state = "discarded"
        db.session.commit()
    flash(de_en("Bogen verworfen.", "Sheet discarded."), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>/erase", methods=["POST"])
@login_required
def sheet_erase(survey_id, sheet_id):
    """A deletion request without a code, by the sheet's number: its participation, scans and typed values go; a sheet
    not yet scanned is refused when it comes. As a deletion online (surveys.erase_request): only admins of the survey,
    with a fresh sign-in and, for a participation, a reason for the seal chain (audit 2026-10-10, P-S4)."""
    from app import accounts, erasure, seal
    from app.surveys import get_survey

    get_survey(survey_id, need="admin")
    survey = _survey(survey_id)
    sheet = _sheet(survey, sheet_id)
    page = url_for("paper.sheet_page", survey_id=survey.id, sheet_id=sheet.id)
    reason = seal.request_reason(request.form.get("why") or "wish", request.form.get("why_text", ""))
    if sheet.response_id and reason is None:
        flash(de_en("Bitte einen Grund wählen; „anderer Rechtsgrund“ braucht {a} bis {b} Zeichen.",
                    "Please pick a reason; “another legal ground” needs {a} to {b} characters.",
                    a=seal.WHY_MIN, b=seal.WHY_MAX), "error")
        return redirect(page)
    again = accounts.need_fresh(page)
    if again:
        return again
    with _LOCK:
        sheet = _locked(sheet_id)
        if sheet is None or sheet.state == "erased":
            db.session.rollback()
            return _back(survey)
        if sheet.response_id:
            response = db.session.get(Response, sheet.response_id)
            # only the sheet's own participation: on SQLite the number of a participation deleted with the period
            # "answers" (sheets from before that cleared it) can belong to a stranger's by now (audit 2026-10-21, B1)
            if response is not None and response.survey_id == survey.id and response.channel == "paper":
                erasure.erase(survey, [response], "request", reason=reason)  # commits
                sheet = _locked(sheet_id)
        drop_scans(survey, sheet)
        _drop_doubles(survey, sheet)
        sheet.state, sheet.destroy, sheet.response_id = "erased", True, None
        db.session.commit()
    flash(de_en("Bogen {n} gelöscht. Bitte vernichten Sie auch das Papier.", "Sheet {n} deleted. Please destroy the paper too.",
                n=paper_pdf.sheet_number(sheet.key)), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>/destroyed", methods=["POST"])
@login_required
def sheet_destroyed(survey_id, sheet_id):
    """The note "destroy the paper" goes: only for admins of the survey, who alone may delete a sheet (code review
    2026-10-15, J4)."""
    from app.surveys import get_survey

    get_survey(survey_id, need="admin")
    survey = _survey(survey_id)
    sheet = _sheet(survey, sheet_id)
    sheet.destroy = False
    db.session.commit()
    return _back(survey)


@bp.route("/<sid:survey_id>/paper/pages/<int:page_id>/remove", methods=["POST"])
@login_required
def page_remove(survey_id, page_id):
    survey = _survey(survey_id)
    page = db.session.get(PaperPage, page_id)
    if page is None or page.survey_id != survey.id or page.state == "read":
        abort(404)
    if not survey.can(current_user, "admin"):
        abort(403)
    _remove_file(survey, page.filename)
    db.session.delete(page)
    db.session.commit()
    return _back(survey)


def _image_response(img, fmt="PNG"):
    out = io.BytesIO()
    img.save(out, fmt, quality=80) if fmt == "JPEG" else img.save(out, fmt)
    out.seek(0)
    resp = send_file(out, mimetype="image/png" if fmt == "PNG" else "image/jpeg", max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _open_scan(survey, page):
    from PIL import Image

    path = scan_path(survey, page.filename)
    if not path or not os.path.isfile(path):
        abort(404)
    return Image.open(path)


@bp.route("/<sid:survey_id>/paper/pages/<int:page_id>.jpg")
@login_required
def page_image(survey_id, page_id):
    """A whole scanned page, smaller; for a page of a sheet with its fields drawn in (green ticked, amber uncertain)."""
    from PIL import ImageDraw

    survey = _survey(survey_id)
    page = db.session.get(PaperPage, page_id)
    if page is None or page.survey_id != survey.id:
        abort(404)
    img = _open_scan(survey, page).convert("RGB")
    if page.sheet_id and page.reading and page.reading.get("H"):
        sheet = db.session.get(PaperSheet, page.sheet_id)
        found = evaluate(survey, sheet)
        draw = ImageDraw.Draw(img)
        import numpy as np

        H = np.asarray(page.reading["H"])
        for e in found["fields"]:
            if e["p"] != page.page:
                continue
            xs, ys = paper_scan.apply(H, [e["x"], e["x"] + e["w"]], [e["y"], e["y"] + e["h"]])
            colour = {"x": (0, 150, 60), "?": (230, 140, 0), "f": (120, 120, 120), "ink": (40, 90, 200)}.get(e["state"])
            if colour:
                draw.rectangle([xs[0] - 3, ys[0] - 3, xs[1] + 3, ys[1] + 3], outline=colour, width=4)
    img.thumbnail((1400, 1980))
    return _image_response(img, "JPEG")


@bp.route("/<sid:survey_id>/paper/sheets/<int:sheet_id>/part/<int:index>.png")
@login_required
def part_image(survey_id, sheet_id, index):
    """The part of the scan around a field, or around all fields of its group (`?group=1`), straightened."""
    survey = _survey(survey_id)
    sheet = _sheet(survey, sheet_id)
    plan = plan_of(sheet)
    if not 0 <= index < len(plan["fields"]):
        abort(404)
    field = plan["fields"][index]
    page = pages_of(sheet).get(field["p"])
    if page is None or not (page.reading or {}).get("H"):
        abort(404)
    area = field
    if request.args.get("group") and field.get("g"):
        same = [f for f in plan["fields"] if f.get("g") == field["g"] and f["p"] == field["p"]]
        x0, y0 = min(f["x"] for f in same), min(f["y"] for f in same)
        x1, y1 = max(f["x"] + f["w"] for f in same), max(f["y"] + f["h"] for f in same)
        area = {"x": max(paper_pdf.LEFT - 2, x0 - 60 if x1 - x0 < 30 else x0), "y": y0,
                "w": 0, "h": y1 - y0}
        area["w"] = max(x1, area["x"] + 40) - area["x"] + (100 if x1 - x0 < 30 else 0)
        area["w"] = min(area["w"], paper_pdf.RIGHT + 2 - area["x"])
    img = paper_scan.snippet(_open_scan(survey, page), page.reading["H"], area, pad=2.5,
                             scale=5.0 if area["w"] > 100 else 7.0)
    return _image_response(img)
