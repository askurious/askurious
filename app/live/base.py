"""Shared parts of live sessions: the blueprints, types, state of a run, questions and responses."""
import time

from flask import Blueprint, abort, url_for

from app.extensions import db, json_given
from app.lang import de_en, english
from app.models import Response, Survey, utcnow


# the presenters' pages under /presenter/, the participants' under /live/ (the short address on the screen, with the
# code page at /live/; Philipp, 2026-10-04)
bp = Blueprint("live", __name__, url_prefix="/presenter")
public_bp = Blueprint("live_public", __name__, url_prefix="/live")

# the slides a live vote offers (live concept 6.3): every type renders and stores as in a
# questionnaire and has a view on the screen (results()); text and image are slides without answers (CONTENT)
TYPES = {"single_choice": "Einfachauswahl", "multiple_choice": "Mehrfachauswahl", "rating": "Bewertungsskala",
         "likert": "Skala mit mehreren Aussagen", "ranking": "Rangfolge", "constant_sum": "100 Punkte verteilen",
         "slider": "Zahl schätzen (Schieberegler)", "position2d": "2×2-Raster (Begriffe einordnen)",
         "semantic": "Semantisches Differential (Gegensatzpaare)", "text_input": "Wortwolke (kurzer Text)", "image_mark": "Bild markieren (Heatmap)", "info": "Textfolie",
         "image": "Bildfolie"}
# the same in English for the presenter's pages (types()); TYPES stays German: the AI assistant's prompt names them
TYPES_EN = {"single_choice": "Single choice", "multiple_choice": "Multiple choice", "rating": "Rating scale",
            "likert": "Scale with several statements", "ranking": "Ranking", "constant_sum": "Distribute 100 points",
            "slider": "Estimate a number (slider)", "position2d": "2×2 grid (place terms)",
            "semantic": "Semantic differential (pairs of opposites)", "text_input": "Word cloud (short text)",
            "image_mark": "Mark an image (heatmap)", "info": "Text slide", "image": "Image slide"}
CONTENT = ("info", "image")
ADDABLE = TYPES
# "Freitext": a short-text question (text_input, several lines) shown as a list of boxes (the wall) instead of a word
# cloud; a preset of the editor (editor.PRESETS, "free_text"), not a type of its own (Philipp, 2026-10-07)
FREE_TEXT = ("Freitext (Liste)", "Free text (list)")


def types():
    """The slide types by name in the language of the presenter's page."""
    return TYPES_EN if english() else TYPES


def addable(survey):
    """What "+ Frage" offers: the slide types with "Freitext" after the word cloud, and in blocks the page break."""
    out = {}
    for key, label in types().items():
        out[key] = label
        if key == "text_input":
            out["free_text"] = de_en(*FREE_TEXT)
    if by_blocks(survey):
        out["page_break"] = de_en("Seitenumbruch", "Page break")
    return out


def type_label(survey, item):
    """The name of a question's type in the list: a text question shown as a list is "Freitext", a page of an
    uploaded PDF "Präsentationsfolie"."""
    if item.type == "text_input" and item.id in state(survey).get("wall", []):
        return de_en(*FREE_TEXT)
    if is_slide(item):
        return de_en("Präsentationsfolie", "Presentation slide")
    return types().get(item.type, item.type)


def is_slide(item):
    """A presentation slide: an image slide made from a page of a PDF (pdf_page), shown over the whole screen with a
    small QR code in the corner."""
    return item is not None and item.type == "image" and bool((item.config or {}).get("slide"))


def lock_state(survey):
    """Take the survey's row before its state is read and written back as a whole: two commands at the same moment
    (laptop and phone, a double click) then run one after the other, and the second one works on what the first
    wrote; before, it wrote its older state back, and e.g. a new run was lost or a deleted one came back (code
    review 2026-10-07, R7). The row is locked by a write that changes nothing (PostgreSQL: the row; SQLite: the
    one writer, held from here to the commit), then read again."""
    db.session.flush()
    db.session.execute(db.update(Survey).where(Survey.id == survey.id).values(updated_at=Survey.updated_at)
                       .execution_options(synchronize_session=False))
    db.session.refresh(survey)


def as_wall(survey, item_id):
    """A text question on the screen as a list of boxes (the wall) instead of a word cloud."""
    lock_state(survey)
    st = state(survey)
    survey.live = {**st, "wall": [i for i in st.get("wall", []) if i != item_id] + [item_id]}
BINS = 10  # classes of the distribution of an estimated number (one slider)
POLL_SECONDS = 2
LEAVE_GRACE = 5  # seconds an answer to the question (or block) just left is still taken (T-16)
MAX_WORDS = 60  # words shown in a cloud


def _day(stamp):
    """The date in a default name, in the format of the researcher's page."""
    from app.lang import current
    from app.timeutil import fmt

    return fmt(stamp, False, current())


def _run(name="", taken=()):
    """A new run. A name another run has already (two unnamed runs on the same day) gets a number: the results page
    and the export tell runs apart by it."""
    stamp = utcnow()
    # unnamed: named in the researcher's language (UI/UX audit 2026-10-07, W116)
    base = name.strip()[:100] or de_en("Durchgang {d}", "Run {d}", d=_day(stamp))
    unique, n = base, 2
    while unique in taken:
        unique, n = f"{base} ({n})", n + 1
    return {"id": int(time.time() * 1000), "name": unique, "started": stamp.isoformat(timespec="minutes")}


def state(survey):
    raw = dict(survey.live or {})
    drop = ("released", "current", "open") + (() if raw.get("flow") == "blocks" else ("block",))  # older keys
    raw = {k: v for k, v in raw.items() if k not in drop}
    st = {"screen": None, "show": True, "closed": [], "runs": [], **raw}
    if not st.get("run"):
        st["run"] = {"id": 0, "name": "Durchgang 1", "started": ""}
    return st


def contact_email(survey):
    """The address the privacy page of a live vote names: the one set under Einstellungen, else the e-mail of the
    account that created the vote (password accounts have one), else none."""
    email = state(survey).get("contact_email") or ""
    if email:
        return email
    from app.models import UserLogin

    if not survey.created_by_id:
        return ""
    # only the address: the participants' database role may read this one column of user_login, never the password
    # hash or the second factor (app/participant_grants.py, audit 2026-10-18, CFG-04)
    return db.session.scalar(db.select(UserLogin.email).where(UserLogin.user_id == survey.created_by_id)) or ""


def clean_teams(values):
    """The teams as the settings store them: texts on one line, at most 40 characters, each once, at most 20."""
    return [t for t in dict.fromkeys(" ".join(v.split())[:40] for v in values if isinstance(v, str)) if t][:20]


def copied_state(source, items):
    """The state of a duplicate of a live vote (app/duplicate.py): the options, the quiz settings and the questions
    shown as a wall carried over to the copied questions (items: old id -> new id), a first run, nothing shown."""
    raw = state(source)
    return {"screen": None, "show": True, "closed": [], "runs": [], "run": _run(de_en("Durchgang 1", "Run 1")),
            **{k: raw[k] for k in OPTIONS if k in raw}, "contact_email": raw.get("contact_email", ""),
            **({"emojis": raw["emojis"]} if "emojis" in raw else {}), **({"flow": raw["flow"]} if "flow" in raw else {}),
            **({"teams": list(raw["teams"])} if isinstance(raw.get("teams"), list) else {}),
            "quiz": {str(items[int(k)]): v for k, v in (raw.get("quiz") or {}).items() if k.isdigit() and int(k) in items},
            "wall": [items[i] for i in raw.get("wall", []) if i in items],
            "corner_off": [items[i] for i in raw.get("corner_off", []) if i in items]}


def blocks(survey):
    return sorted(survey.blocks, key=lambda b: b.position)


LIVE_NOW_HOURS = 3  # "Läuft" on the card in "Meine Befragungen": presented within this time


def live_now(survey):
    """Whether a live vote is being presented: a question on the screen or a block running, changed within
    LIVE_NOW_HOURS (a live vote stays a draft, so its status says nothing about it)."""
    st = survey.live or {}
    return not st.get("ended") and bool(st.get("screen") or st.get("block")) and time.time() - (st.get("at") or 0) < LIVE_NOW_HOURS * 3600


def by_blocks(survey):
    """True with the flow "In Blöcken" (see the module's docstring), False for "Frage für Frage"."""
    return (survey.live or {}).get("flow") == "blocks"


def pages(block):
    """A block's pages for the phones (flow "In Blöcken"): its questions to answer, split at page breaks; text and
    image slides are only shown on the screen; empty pages are left out."""
    out = [[i for i in page if i.type in TYPES and i.type not in CONTENT] for page in block.pages()]
    return [page for page in out if page]


def answered_blocks(survey):
    """The ids of the blocks with something to answer, in their order."""
    return [b.id for b in blocks(survey) if pages(b)]


def block_questions(block):
    return [i for i in block.items if i.type in TYPES]


def questions(survey):
    return [i for b in blocks(survey) for i in block_questions(b)]


def _flatten(survey):
    """One block, no page breaks (concept 6.3): the questions of an older live vote with several blocks go into the
    first one, in their order; page breaks and the empty blocks are removed. Returns True if something changed."""
    from app import flow

    rows = blocks(survey)
    if by_blocks(survey):  # "In Blöcken": blocks and page breaks are wanted
        return False
    if not rows or (len(rows) == 1 and all(i.type != "page_break" for i in rows[0].items)):
        return False
    first, items = rows[0], [i for b in rows for i in b.items]
    kept = [i for i in items if i.type != "page_break"]
    for block in rows[1:]:
        block.items = []
    first.items = kept
    for n, item in enumerate(kept):
        item.position = n
    for block in rows[1:]:  # page breaks and the emptied blocks go with their parents (delete-orphan)
        survey.blocks.remove(block)
    db.session.flush()
    flow.normalize(survey)
    return True


def mine(user):
    from app import access

    return db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user)), json_given(Survey.live))
                              .order_by(Survey.id.desc())).all()


def _survey(code, need="viewer"):
    from app.surveys import get_survey

    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.live is None:
        abort(404)
    return get_survey(survey.id, need=need)


def join_url(survey):
    from app.hosts import participant_base

    return participant_base() + url_for("live_public.join", code=survey.code)


def _in_run(run_id):
    """The filter of the participations of one run, in the database (audit 2026-10-05, T-40): the run stands in the
    JSON column params ("live_run"), read with the JSON functions of SQLite and PostgreSQL alike; participations of
    the first run of older live votes carry no run (0). A run's id is a time in milliseconds: a big integer."""
    run = db.cast(Response.params["live_run"].as_string(), db.BigInteger)
    return db.or_(run == run_id, run.is_(None)) if run_id == 0 else run == run_id


def _responses(survey, run_id):
    """The participations of one run, filtered in the database (T-40: a lecture series with many runs read all of
    them for every question of the screen). Within a request each run is read once: the screen asks every 2 seconds
    and reads a run several times, the slides read it once per question. A participation added (_new_response) or
    deleted (run_delete) in the request reads them afresh."""
    from flask import g, has_request_context

    memo = g.setdefault("live_responses", {}) if has_request_context() else {}
    if (survey.id, run_id) not in memo:
        memo[(survey.id, run_id)] = list(db.session.scalars(
            db.select(Response).filter(Response.survey_id == survey.id, Response.is_preview.is_(False), _in_run(run_id))
            .order_by(Response.id)))  # a fixed order on PostgreSQL too (ties in word counts)
    return list(memo[(survey.id, run_id)])


def _people(survey, run_id):
    """The number of participants in a run: a further word of the same device ("Mehrere Antworten pro Gerät") is no
    other person (T-19)."""
    return sum(1 for r in _responses(survey, run_id) if not (r.params or {}).get("live_extra"))


def _flush_deleted(reason):
    """Write the deletions now, so that a seal chain records them with their reason (app/seal.py)."""
    from app import seal

    db.session.info[seal.REASON] = reason
    try:
        db.session.flush()
    finally:
        db.session.info.pop(seal.REASON, None)


def _forget_responses():
    from flask import g, has_request_context

    if has_request_context():
        g.pop("live_responses", None)


def _qr(survey):
    import io

    import segno
    from markupsafe import Markup

    out = io.BytesIO()  # white background in the code itself: a dark theme of the screen cannot swallow it
    segno.make(join_url(survey), error="m").save(out, kind="svg", border=2, xmldecl=False, omitsize=True, light="#ffffff")
    return Markup(out.getvalue().decode())


# --- settings (concept 6.3): title and language, look, participation; each form saves by itself --------------------

# "results_open": results visible as soon as presenting starts; the ghost on the screen hides and shows them at any
# time (Philipp, 2026-10-07; the earlier "auto_results" had no tick and was never read: a stored False of it is no
# choice and is left alone); "multi": a short text (word cloud) may be sent more than once from one device (decision L7)
# moderation (concept package D): "premod": short texts appear only once the presenter lets them through; "filter":
# texts with a swear word (BAD_WORDS) stay hidden until the presenter shows them
OPTIONS = {"results_open": True, "multi": False, "premod": False, "filter": True, "qa": False, "reactions": True,
           "instant": False,  # "instant": a tap sends the answer (pages whose questions take one tap; Philipp, 2026-10-04)
           "slide_phone": False,  # "slide_phone": a presentation slide shows on the phones too, not only on the screen
           "slide_notes": False}  # "slide_notes": the audience takes notes on the slides and downloads them (notes_on)
# the options of the slides (formerly the experimental feature "slides"): saved when the form carries the marker
# "slides_shown", which it always does since 2026-10-17; a form without it keeps them as they are
SLIDES_OPTIONS = ("slide_phone", "slide_notes")
# reactions (package G): the emojis a phone can send to the screen, how often, how long the screen shows them
REACTIONS = ("👍", "❤️", "😂", "😮", "👏")  # offered unless the presenter chose others (Einstellungen -> Teilnahme)
# what the presenter may choose from, also negative ones (state "emojis")
REACTION_CHOICES = {"👍": "Daumen hoch", "👎": "Daumen runter", "❤️": "Herz", "👏": "Applaus", "🎉": "Feiern",
                    "😂": "Lachen", "😮": "Staunen", "🤔": "Nachdenken", "💡": "Idee", "🔥": "Feuer",
                    "😕": "Unklar", "😴": "Langweilig", "🐢": "Langsamer", "🐇": "Schneller", "❓": "Frage",
                    "🙋": "Meldung"}
REACTION_CHOICES_EN = {"👍": "Thumbs up", "👎": "Thumbs down", "❤️": "Heart", "👏": "Applause", "🎉": "Celebrate",
                       "😂": "Laughing", "😮": "Surprised", "🤔": "Thinking", "💡": "Idea", "🔥": "Fire",
                       "😕": "Unclear", "😴": "Boring", "🐢": "Slower", "🐇": "Faster", "❓": "Question",
                       "🙋": "Hand up"}


def reaction_choices():
    """REACTION_CHOICES named in the language of the presenter's page (Einstellungen)."""
    return REACTION_CHOICES_EN if english() else REACTION_CHOICES


def reaction_set(survey):
    """The emojis the phones offer: the presenter's choice (in the order of REACTION_CHOICES), else REACTIONS."""
    chosen = state(survey).get("emojis")
    return tuple(e for e in REACTION_CHOICES if e in chosen) if isinstance(chosen, list) else REACTIONS


REACTION_GAP = 1.5  # seconds between two requests of one device
REACTION_BURST = 8  # emojis in one request (quick clicks on the phone, collected for 250 ms)
REACTION_SHOWN = 6  # seconds a reaction stays new for the screen
MAX_MARKS = 4000  # marks on the screen (image_mark)
QA_PER_DEVICE = 5  # questions one device may ask in a run (Q&A)
# every emoji sent counts for the slide on the screen (LiveFeedback, kind "e:<emoji>"), for the presenter view and the
# review at the end; the button "Unklar" of before went (Ralf, 2026-10-16: there is the emoji 😕), its taps count as 😕
UNCLEAR_EMOJI = "😕"
# quiz (concept package F): which questions can have a right answer, the points, the leaderboard
QUIZ_TYPES = ("single_choice", "multiple_choice", "slider")
POINTS = 1000  # a right answer; with a countdown 500 to 1000, the faster the more
BOARD_TOP = 10
QA_STATUS = {"open": "offen", "pending": "wartet auf Freigabe", "answered": "beantwortet", "hidden": "ausgeblendet"}
QA_STATUS_EN = {"open": "open", "pending": "awaiting approval", "answered": "answered", "hidden": "hidden"}


def qa_status():
    """QA_STATUS in the language of the researcher's page."""
    return QA_STATUS_EN if english() else QA_STATUS
BAD_WORDS = frozenset("""arsch arschloch fick ficken fickt fotze hure huren hurensohn scheiße scheisse scheiß scheiss wichser
schlampe spast spasti missgeburt nazi neger nigger fuck fucking fucker shit bitch cunt asshole motherfucker""".split())
WALL_MAX = 80  # posts on the wall


def options(survey):
    st = state(survey)
    return {k: bool(st.get(k, v)) for k, v in OPTIONS.items()}


def notes_on(survey):
    """The audience's notes on the presentation slides (app/live/notes.py): "Notizen und Herunterladen erlauben",
    which counts only together with "Präsentationsfolien auch auf den Handys zeigen" (E2)."""
    opts = options(survey)
    return opts["slide_phone"] and opts["slide_notes"]


def notes_slides(survey, st):
    """The presentation slides the audience may page through and download (E3): those up to the presenter's
    current place in the vote (the question on the screen, else the last one shown: "notes_at", kept by
    control). Going back locks the later slides again; before the first is shown, none."""
    items = questions(survey)
    ids = [i.id for i in items]
    ref = st.get("screen") if st.get("screen") in ids else st.get("notes_at")
    if ref not in ids:
        return []
    return [i for i in items[:ids.index(ref) + 1] if is_slide(i)]


def standard_thanks(survey):
    """The standard text after the last question, in the vote's language: the placeholder of the field (R38)."""
    from app.participate import DEFAULT_END_TEXTS
    from app.ui_i18n import tr

    return tr(survey.default_language, DEFAULT_END_TEXTS["de"]["thanks"], DEFAULT_END_TEXTS["en"]["thanks"])


def moderation_key(run_id, item_id):
    """Where the presenter's hiding and approving of short texts is kept: per run and question, so a new run starts
    with nothing hidden (pre-test 2026-10-23: a word hidden in an earlier test stayed hidden, "1 Antwort" and an empty
    screen). Entries of the former form (the question alone) count for no run any more."""
    return f"{run_id}:{item_id}"


def moderated(st, item_id, run_id=None):
    """(hidden, approved) keys of the short texts of one question in one run; without a run (the results page over
    all runs) those of every run."""
    out = []
    for name in ("hidden", "approved"):
        kept = st.get(name) or {}
        if run_id is not None and run_id != "":
            out.append(set(kept.get(moderation_key(run_id, item_id), [])))
        else:
            out.append({k for where, keys in kept.items() if where.endswith(f":{item_id}") for k in keys})
    return tuple(out)
