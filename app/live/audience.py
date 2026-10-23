"""The participants' side (blueprint live_public): joining, answering, quiz, Q&A, reactions."""
import re
import time
import unicodedata

from flask import abort, jsonify, redirect, render_template, request, session, url_for

from app.extensions import db
from app.live import devices
from app.live.base import (CONTENT, LEAVE_GRACE, POLL_SECONDS, QA_PER_DEVICE, REACTION_BURST, REACTION_GAP,
                           _forget_responses, _in_run, answered_blocks, blocks, by_blocks, is_slide,
                           options, pages, public_bp, questions, reaction_set, state)
from app.live.scores import (_bad, _current, _key, _slide_image, deadline, leaderboard, points, qa_rows,
                             quiz_of, shared_places)
from app.models import Response, Survey, new_pid, utcnow
from app.ui_i18n import tr


# --- participants -----------------------------------------------------------------------------------------------


def _public(code):
    """The live vote of a phone's address. A code that leads to no live vote counts as a wrong code of the sender, as
    on the code page (limits.allow_code_try; S19 of the audit 2026-10-06); once there are too many, 429 for a while
    to a browser without a device id (the phones in the room that joined before go on)."""
    from app import limits

    blocked = not limits.allow_code_try(count=False)
    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.live is None:
        if blocked:
            abort(429)
        limits.allow_code_try()
        abort(404)
    if blocked and not DEVICE_RE.fullmatch(str(session.get("live-device") or "")):
        abort(429)
    if survey.archived:  # archived: over, nothing to see or send
        abort(404)
    if request.method == "POST":
        devices.marker(survey, state(survey), lock=True)
    else:
        device_token()
    return survey


def _busy(survey):
    """The answer when a new participation is refused (too many from one sender, or the run is full)."""
    return jsonify(ok=False, error=tr(survey.default_language, "Gerade kommen sehr viele Antworten an. Bitte versuchen "
                                      "Sie es gleich noch einmal.", "Very many answers are coming in right now. Please "
                                      "try again in a moment.")), 429


def _may_join(survey, st):
    """Whether one more participation may start in the current run: per sender (limits.allow_live) and per sender in
    this run (limits.allow_live_run). The run as a whole (LIVE_RUN_PARTICIPATIONS, counted by the database) turns
    nobody away: reaching it is reported on "Präsentieren" and in Slack (S5 of the audit 2026-10-06)."""
    from flask import current_app

    from app import limits

    if not limits.allow_live("join") or not limits.allow_live_run("join", survey, st["run"]["id"]):
        return False
    cap = current_app.config["LIVE_RUN_PARTICIPATIONS"]
    if cap and db.session.scalar(db.select(db.func.count(Response.id)).filter(
            Response.survey_id == survey.id, Response.is_preview.is_(False), _in_run(st["run"]["id"]))) >= cap:
        limits.note_cap(survey, "live_join", st["run"])  # counted, not read (T-40)
    return True


DEVICE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


def device_token():
    """This browser's device id for the live votes (T-17): issued and signed by the server (devices.issue, BIZ-02),
    kept in the (permanent) session, so a phone without localStorage keeps one too; live.js keeps it in
    localStorage too, which outlives a lost cookie."""
    session.permanent = True
    if not DEVICE_RE.fullmatch(str(session.get("live-device") or "")):
        session["live-device"] = devices.issue()
    return session["live-device"]


def _device(survey):
    """The device id an answer names (form field "device", else the session's), as kept with the participation:
    hashed with the vote, so the same phone carries a different value in every live vote; None without one. The
    form field counts only with the server's signature (or as the session's own id): an id a script makes up is
    no new device (BIZ-02)."""
    import hashlib

    raw = request.form.get("device", "") if request.method == "POST" else ""
    if DEVICE_RE.fullmatch(raw) and (devices.signed(raw) or raw == session.get("live-device")):
        session["live-device"] = raw  # localStorage recovery also restores Q&A state for subsequent GETs
    else:
        raw = str(session.get("live-device") or "")
    if not DEVICE_RE.fullmatch(raw):
        return None
    return hashlib.sha256(f"{survey.uid}:{raw}".encode()).hexdigest()[:32]


def _drop_old_runs(survey, st):
    """Take the keys of earlier runs of this vote out of the session: the participation, its pid and the nickname are
    only ever read for the current run, and the cookie session grew with every run until the browser dropped it
    silently past 4 KB (code review 2026-10-15, I9)."""
    run = str(st["run"]["id"])
    for prefix in (f"live:{survey.id}:", f"live-nick:{survey.id}:"):
        for key in [k for k in session if k.startswith(prefix)]:
            if key[len(prefix):].split(":", 1)[0] != run:
                session.pop(key, None)


DEVICE_LOCK = 4716  # pg_advisory_xact_lock key (app/mailqueue.py lists the others)


def _by_device(survey, st, device):
    return db.session.scalars(db.select(Response).filter(
        Response.survey_id == survey.id, Response.is_preview.is_(False), _in_run(st["run"]["id"]),
        Response.params["live_device"].as_string() == device).order_by(Response.id).limit(1)).first()


def _lock_device(survey, st, device):
    """Until this transaction ends, one first answer of a device in a run at a time, so two at the same moment (a
    second tab, a script with the signed device id) do not make two participations, a double vote (audit
    2026-10-21, F8). PostgreSQL: an advisory lock on the device in this run, no row lock, so the other devices'
    first answers do not wait for each other. SQLite: a write that changes nothing takes the one write lock now, so
    the second waits and then finds what the first committed. No unique index: that would need a migration on a
    JSON field."""
    if db.engine.dialect.name == "postgresql":
        import hashlib

        ident = int.from_bytes(hashlib.sha256(f"{survey.id}:{st['run']['id']}:{device}".encode()).digest()[:4],
                               "big", signed=True)
        db.session.execute(db.text("SELECT pg_advisory_xact_lock(:key, :ident)"), {"key": DEVICE_LOCK, "ident": ident})
    else:
        db.session.execute(db.delete(Response).where(db.false()))


def _stale_run(st):
    """A request from a page of an earlier run: the phone sends the run its page was drawn for (data-live-run,
    live.js). After "Neuer Durchgang" or "Alles zurück" (control.py) a new run id is current, and an answer, a word,
    a question, a vote, a reaction or a nickname from the old page landed in the new run (audit 2026-10-23, DS-06).
    Refused with 409 "stale" and run, so the phone loads the page anew. A request without the field (a page from
    before this change) is taken as before."""
    sent = request.form.get("run")
    if sent is not None and sent.strip() != str(st["run"]["id"]):
        return jsonify(ok=False, error="stale", run=True), 409
    return None


def _response(survey, st, create=False):
    """This browser's participation in the current run: kept in the session, else found by the device id of the
    answer (T-17: the cookie was lost, or the answer to the first vote never reached the phone and it sends again),
    else None (or a new one with create)."""
    key = f"live:{survey.id}:{st['run']['id']}"
    rid = session.get(key)
    response = db.session.get(Response, rid) if rid else None
    # the row id alone does not name a participation for good: one deleted by its deletion code may give its id to
    # the next one (SQLite); the pid noted with it must fit, as in participate._current_response (audit 2026-10-12, E-11)
    if response is not None and (response.survey_id != survey.id
                                 or (session.get(key + ":pid") and response.pid != session.get(key + ":pid"))):
        response = None
    device = _device(survey) if response is None and request.method == "POST" else None
    if response is None and device:
        response = _by_device(survey, st, device)
        if response is None and create:  # look again under the device's lock before making a new one (F8)
            _lock_device(survey, st, device)
            response = _by_device(survey, st, device)
        if response is not None:
            session.permanent = True
            _drop_old_runs(survey, st)
            session[key], session[key + ":pid"] = response.id, response.pid
    if response is None and create:
        response = _new_response(survey, st, device=device)
        session.permanent = True  # a phone that closes the browser between two questions keeps its participation
        _drop_old_runs(survey, st)
        session[key], session[key + ":pid"] = response.id, response.pid
    return response


def _new_response(survey, st, extra=False, device=None, main=None):
    """A participation in the current run. extra: a further word of the device whose participation is `main` (T-19:
    marked in the export with its pid). A run marked as a trial ("Probe", T-23) gives test participations."""
    response = Response(survey_id=survey.id, language=survey.default_language, status=Response.COMPLETED, answers={},
                        pid=new_pid(survey.code), started_at=utcnow(), completed_at=utcnow(),
                        source="test" if st["run"].get("test") else "real",
                        params={"live_run": st["run"]["id"], "live_run_name": st["run"]["name"],
                                **({"live_extra": True, "live_main": main.pid if main is not None else ""} if extra else {}),
                                **({"live_device": device} if device and not extra else {})})
    db.session.add(response)
    db.session.flush()
    _forget_responses()
    nick = None if extra else session.pop(_nick_key(survey, st), None)  # set before the first answer (nickname)
    if isinstance(nick, dict):
        response.layout = {**(response.layout or {}), "live_nick": nick.get("name", ""), "live_team": nick.get("team", "")}
    return response


def _nick_form(survey, st, field, team_field):
    """(name, team) from the form, as the leaderboard keeps them: (None, None) for a name the filter refuses."""
    # NFKC: the name the board shows is the one the filter checked, without full-width or other look-alike letters
    # (Abgleich 2026-10-15, TN-13)
    name = " ".join(unicodedata.normalize("NFKC", request.form.get(field, "")).split())[:30]
    if options(survey)["filter"] and _bad(name):
        return None, None
    team = request.form.get(team_field, "")
    return name, (team if team in (st.get("teams") or []) else "")


def _keep_nick(survey, st, response):
    """The nickname an answer carries (the field on the phone, live.js): kept with the participation. A name typed
    without the tick was lost with the next load of the page, and the leaderboard named a random one (Ralf,
    2026-10-08); sent with the answer it also never races the participation's first cookie."""
    if "live_nick" not in request.form:
        return
    name, team = _nick_form(survey, st, "live_nick", "live_team")
    layout = response.layout or {}
    if name is not None and (layout.get("live_nick"), layout.get("live_team") or "") != (name, team):
        response.layout = {**layout, "live_nick": name, "live_team": team}


def _nick_key(survey, st):
    return f"live-nick:{survey.id}:{st['run']['id']}"


# a quiz starts with a random nickname (Philipp, 2026-10-04): a friendly adjective and an animal, e.g. "Mutiger Fuchs";
# German adjectives take the ending of the animal's gender
NICK_ADJECTIVES = (("mutig", "Brave"), ("klug", "Clever"), ("flink", "Swift"), ("fröhlich", "Cheerful"), ("schlau", "Smart"),
                   ("tapfer", "Bold"), ("munter", "Lively"), ("freundlich", "Friendly"), ("neugierig", "Curious"),
                   ("gelassen", "Calm"), ("pfiffig", "Bright"), ("strahlend", "Shining"))
NICK_ANIMALS = (("Fuchs", "m", "Fox"), ("Eule", "f", "Owl"), ("Otter", "m", "Otter"), ("Panda", "m", "Panda"),
                ("Biber", "m", "Beaver"), ("Libelle", "f", "Dragonfly"), ("Erdmännchen", "n", "Meerkat"),
                ("Pinguin", "m", "Penguin"), ("Igel", "m", "Hedgehog"), ("Möwe", "f", "Gull"), ("Delfin", "m", "Dolphin"),
                ("Zebra", "n", "Zebra"), ("Luchs", "m", "Lynx"), ("Koala", "m", "Koala"), ("Giraffe", "f", "Giraffe"),
                ("Lama", "n", "Llama"))


def random_nick(lang):
    import random

    (de_adj, en_adj), (animal, gender, en_animal) = random.choice(NICK_ADJECTIVES), random.choice(NICK_ANIMALS)
    if lang in ("de", "de-du"):
        ending = {"m": "er", "f": "e", "n": "es"}[gender]
        return f"{de_adj.capitalize()}{ending} {animal}"
    return f"{en_adj} {en_animal}"


def _nick(survey, st, response):
    """(nickname, team) of this device in the current run: with its participation, or waiting in the session. In a
    quiz a device without a name gets a random one (it can change it)."""
    if response is not None:
        name = (response.layout or {}).get("live_nick") or ""
        if not name and any(quiz_of(st, i) for i in questions(survey)):
            name = random_nick(survey.default_language)
            response.layout = {**(response.layout or {}), "live_nick": name}
            db.session.commit()
        return name, (response.layout or {}).get("live_team") or ""
    kept = session.get(_nick_key(survey, st))
    if not isinstance(kept, dict) and any(quiz_of(st, i) for i in questions(survey)):
        _drop_old_runs(survey, st)
        kept = session[_nick_key(survey, st)] = {"name": random_nick(survey.default_language), "team": ""}
    return (kept.get("name", ""), kept.get("team", "")) if isinstance(kept, dict) else ("", "")


def _instant(survey, items):
    """Whether a tap sends the page (Einstellungen -> Teilnahme): only when every question on it takes one tap."""
    return options(survey)["instant"] and bool(items) and all(i.kind.one_click(i) for i in items)


def _multi(survey, item):
    """Several answers one after the other: set on the word cloud itself ("live_multi", Ralf 2026-10-16); a vote
    from before that keeps the former tick on Einstellungen ("multi")."""
    if item.type != "text_input":
        return False
    return bool((item.config or {}).get("live_multi", options(survey)["multi"]))


def _word_limit(item):
    """At most this many answers per device to a word cloud with several answers ("live_max"), else None."""
    n = (item.config or {}).get("live_max")
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def _sent(survey, st, item):
    return (devices.read(survey, st, "sent", {}) or {}).get(str(item.id), 0)


def _my_words(survey, st, response, item):
    """[(participation, text)] this device sent to a word cloud with several answers, oldest first: its participation
    and the further words that name it (live_main), so the phone lists them and can take one back (Ralf, 2026-10-16)."""
    if response is None:
        return []
    rows = [response] + list(db.session.scalars(db.select(Response).filter(
        Response.survey_id == survey.id, _in_run(st["run"]["id"]),
        Response.params["live_main"].as_string() == response.pid).order_by(Response.id)))
    return [(r, str((r.answers or {}).get(str(item.id)))) for r in rows
            if (r.answers or {}).get(str(item.id)) not in (None, "", [])]


def _multi_key(survey, st, item, sent):
    """q-<id>-<sent> for one more word, full-<id> once the limit per device is reached."""
    limit = _word_limit(item)
    return f"full-{item.id}" if limit and sent >= limit else f"q-{item.id}-{sent}"


def _phone(survey, st, response):
    """(key, item): what the phone shows. "wait" (no question on the screen), "closed-<id>" (voting on it has
    ended), "answered-<id>" (this device answered; it may change its answer), "end" (answered the last question:
    the closing text), "q-<id>" (the question to answer; with several answers allowed a counter follows, so the
    form comes back empty after each one). In blocks (by_blocks): see _block_phone."""
    if st.get("ended"):  # "Live-Abstimmung beenden": the closing text, also for a device that joins now
        return "ended", None
    if by_blocks(survey):
        key, item = _block_phone(survey, st, response)[:2]
        # no page to answer (no block running, or it is answered): the presentation slide on the screen shows on the
        # phone too, as in "Frage für Frage" (Ralf, 2026-10-16: the phone said "Gleich geht es los" on slide 3)
        on_screen = _current(survey, st) if key in ("wait", "done", "end") else None
        if is_slide(on_screen) and options(survey)["slide_phone"]:
            return f"slide-{on_screen.id}", on_screen
        if key == "wait" and on_screen is not None and on_screen.type in CONTENT:  # else: "look at the screen"
            return f"look-{on_screen.id}", on_screen
        return key, item
    item = _current(survey, st)
    if item is None:
        return "wait", None
    if is_slide(item) and options(survey)["slide_phone"]:  # the presentation slide on the phone too (Einstellungen)
        return f"slide-{item.id}", item
    if item.type in CONTENT:  # a text or image slide: nothing to answer, the phone points to the screen
        return f"look-{item.id}", item
    if item.id in st["closed"] or (deadline(st, item) and time.time() > deadline(st, item)):
        return f"closed-{item.id}", item
    if _multi(survey, item):
        return _multi_key(survey, st, item, _sent(survey, st, item)), item
    if response is not None and (response.answers or {}).get(str(item.id)) not in (None, "", []):
        ids = [i.id for i in questions(survey)]
        return ("end" if ids[-1] == item.id else f"answered-{item.id}"), item
    return f"q-{item.id}", item


def _page_ids(items):
    """A page of a block as its question ids ("12,13"): the form sends it along (field "page"), so an answer to a
    page that changed under the phone is never stored under other questions (T-18)."""
    return ",".join(str(i.id) for i in items)


def _done(response, block, block_pages):
    """The ids of the questions of a block this device has answered (T-18: a set, so a question added to the
    running block or a page moved does not shift the progress); older progress was a number of pages."""
    layout = (response.layout or {}) if response is not None else {}
    kept = (layout.get("live_done") or {}).get(str(block.id))
    if kept is not None:
        return {int(i) for i in kept}
    count = int((layout.get("live_pages") or {}).get(str(block.id), 0))
    return {i.id for page in block_pages[:count] for i in page}


def _block_phone(survey, st, response, block_id=None):
    """(key, None, page items) in blocks: "p-<block>-<n>" (page n of the running block, the first one with a
    question this device has not answered yet), "wait" (no block running), "done" (the running block is answered,
    another may follow), "end" (the last block is answered). block_id: another block than the running one (the one
    just left, T-16)."""
    wanted = st.get("block") if block_id is None else block_id
    block = next((b for b in blocks(survey) if b.id == wanted), None)
    if block is None or not pages(block):
        return "wait", None, []
    block_pages = pages(block)
    done = _done(response, block, block_pages)
    for n, page in enumerate(block_pages):
        if any(i.id not in done for i in page):
            return f"p-{block.id}-{n}", None, page
    started = answered_blocks(survey)
    return ("end" if started and started[-1] == block.id else "done"), None, []


def _rejoin(survey, st, response):
    """In blocks: a device that comes back (QR code, address, code page or a reload of its own) starts again at the
    first page of the running block; its answers stay and show on the pages (Philipp, 2026-10-07). The phone's own
    loads after a page was sent carry "weiter" (live.js) and keep the progress."""
    block_id = st.get("block")
    if response is None or not block_id or not by_blocks(survey):
        return
    layout = dict(response.layout or {})
    if not ((layout.get("live_done") or {}).get(str(block_id)) or (layout.get("live_pages") or {}).get(str(block_id))):
        return
    layout["live_done"] = {**(layout.get("live_done") or {}), str(block_id): []}
    layout["live_pages"] = {**(layout.get("live_pages") or {}), str(block_id): 0}
    response.layout = layout
    db.session.commit()


def _seconds(st, item):
    """The countdown of a quiz question in seconds, or None."""
    quiz = quiz_of(st, item)
    return quiz.get("seconds") if quiz else None


def _page_shown(survey, st, block_id, items):
    """From when the countdown of a quiz question in blocks runs (T-22): from the start of the block, kept on the
    server as in "Frage für Frage". It ran from when the page was first shown on this device, kept in its cookie:
    a fresh cookie (a second device that only loads the page once the question is read) gave almost full points
    (S21 of the audit 2026-10-06). The block just left (LEAVE_GRACE): the start it had."""
    left = st.get("left_block") or {}
    if block_id is not None and block_id != st.get("block") and left.get("id") == block_id and left.get("start"):
        return left["start"]
    return st.get("block_at")


def _page_deadline(survey, st, block_id, items):
    """When the first countdown of a quiz question on a page of a block runs out for this device, or None."""
    shown = _page_shown(survey, st, block_id, items)
    ends = [shown + _seconds(st, i) for i in items if _seconds(st, i)] if shown else []
    return min(ends) if ends else None


def _block_answer(survey, st):
    """The answers to a page of the running block (flow "In Blöcken"): every question of the page is checked first;
    then they are stored and the phone moves on to the first page not answered yet. Also taken: a page of the
    block just left, for LEAVE_GRACE seconds (T-16), and a changed answer to a page of the running block answered
    already (a second tap while the first was on its way, T-21). A quiz question answered after its countdown is
    left out, as in "Frage für Frage" (T-22)."""
    from app.items.base import AnswerError

    response = _response(survey, st)
    ref, sent = request.form.get("ref", ""), request.form.get("page")
    key, _, items = _block_phone(survey, st, response)
    block_id = st.get("block")
    if key.startswith("p-") and ref == key:
        if sent is not None and sent != _page_ids(items):  # another page at this place: the block changed
            return jsonify(ok=False, error="moved"), 409
    else:
        items = None
        left = st.get("left_block") or {}
        if left.get("id") and time.time() - (left.get("at") or 0) <= LEAVE_GRACE and ref.startswith(f"p-{left['id']}-"):
            k, _, page = _block_phone(survey, st, response, left["id"])
            if k == ref and sent in (None, _page_ids(page)):
                items, block_id = page, left["id"]
        if items is None and sent and response is not None:  # a page of the running block, answered already
            block = next((b for b in blocks(survey) if b.id == st.get("block")), None)
            block_pages = pages(block) if block is not None else []
            done = _done(response, block, block_pages) if block is not None else set()
            items = next((page for page in block_pages if _page_ids(page) == sent and all(i.id in done for i in page)), None)
        if items is None:
            running = st.get("block") and ref.startswith(f"p-{st['block']}-")
            return jsonify(ok=False, error="moved" if running else "closed"), 409
    values, errors = {}, {}
    for item in items:
        try:
            values[str(item.id)] = item.kind.parse_answer(item, request.form, survey.default_language)
        except AnswerError as e:
            errors[str(item.id)] = str(e)
    if errors:
        return jsonify(ok=False, error="invalid", errors=errors), 400
    now, late, times = time.time(), [], {}
    shown = _page_shown(survey, st, block_id, items)
    for item in items:
        if _seconds(st, item) and shown:
            if now > shown + _seconds(st, item) + 1:  # the countdown has run out (a second's grace, as in answer())
                values.pop(str(item.id), None)
                late.append(item.id)
            elif values.get(str(item.id)) not in (None, "", []):
                times[str(item.id)] = round(now - shown, 2)  # the answer time of the quiz's points
    if response is None and not _may_join(survey, st):
        return _busy(survey)
    new_page = response is None or any(str(i.id) not in (response.answers or {}) for i in items)
    if new_page and not devices.allow_sender(survey, st, "p-" + _page_ids(items)):  # BIZ-02: per network and page
        return _busy(survey)
    response = response or _response(survey, st, create=True)
    _keep_nick(survey, st, response)
    # a page sent again (after _rejoin it shows what was given): an answer emptied there is gone, not the old one
    # kept (audit 2026-10-10, T-5); a question whose countdown ran out is not in `values` and keeps its answer
    stored = dict(response.answers or {})
    emptied = [k for k, v in values.items() if v in (None, "", [])]
    for k in emptied:
        stored.pop(k, None)
    response.answers = {**stored, **{k: v for k, v in values.items() if v not in (None, "", [])}}
    layout = dict(response.layout or {})
    if emptied and layout.get("live_times"):
        layout["live_times"] = {k: v for k, v in layout["live_times"].items() if k not in emptied}
    block = next((b for b in blocks(survey) if b.id == block_id), None)
    block_pages = pages(block) if block is not None else []
    done = (_done(response, block, block_pages) if block is not None else set()) | {i.id for i in items}
    layout["live_done"] = {**(layout.get("live_done") or {}), str(block_id): sorted(done)}
    # the number of pages answered, as before
    layout["live_pages"] = {**(layout.get("live_pages") or {}),
                            str(block_id): sum(all(i.id in done for i in page) for page in block_pages)}
    # a page sent again with the same answer keeps the answer time it had (the points); a changed one takes the
    # new time (Ralf, audit 2026-10-23, DS-05)
    old_times = layout.get("live_times") or {}
    times = {k: t for k, t in times.items() if not (k in old_times and stored.get(k) == values.get(k))}
    if times:
        layout["live_times"] = {**old_times, **times}
    response.layout = layout
    response.completed_at = utcnow()
    db.session.commit()
    return jsonify(ok=True, **({"late": late} if late else {}))


@public_bp.route("/")
def enter_code():
    """The code page (concept package B): a short address to type on any phone, then the code from the screen. A
    live vote's code leads to it, a survey's code or label to its participant link."""
    import re

    from app import links

    from app import limits

    lang = request.accept_languages.best_match(["de", "en"]) or "de"
    raw = request.args.get("code", "")
    error = False
    if raw and not limits.allow_code_try(count=False):
        return render_template("live/code.html", lang=lang, raw=raw, error=False, blocked=True), 429
    if raw:
        key = re.sub(r"[^0-9a-zA-Z_-]", "", raw)[:40]
        survey = links.resolve(key) if key else None
        if survey is not None and survey.live is not None:
            return redirect(url_for("live_public.join", code=survey.code))
        if survey is not None and survey.status == Survey.RUNNING:
            return redirect(links.participant_url(survey))
        error = True
        limits.allow_code_try()
    return render_template("live/code.html", lang=lang, raw=raw, error=error), (404 if error else 200)


@public_bp.route("/<code>")
def join(code):
    from app.participate import end_text

    survey = _public(code)
    lang = survey.default_language
    st = state(survey)
    device_token()
    response = _response(survey, st)
    if request.args.get("weiter") is None and request.args.get("aendern") is None:
        _rejoin(survey, st, response)
    key, item = _phone(survey, st, response)
    form = None
    # the question is drawn on its own (_question.html), so the scripts its elements need (participant-mark.js for
    # marking an image, sorting, the text marker, a voice answer, a map) reach the page only through this shared
    # namespace (participant/_scripts.html); without it a heat map took no taps (pre-test 2026-10-16)
    from jinja2.utils import Namespace

    assets = Namespace(loaded_leaflet=False, loaded_geolocation=False, extra_scripts=[])
    if key.startswith("p-"):  # "In Blöcken": a page of the running block, all its questions in one form
        from markupsafe import Markup

        page_items = _block_phone(survey, st, response)[2]
        # a page answered in part (a question added to it while the block runs, T-18) shows what was given
        given = {i.id: (response.answers or {}).get(str(i.id)) for i in page_items} if response is not None else {}
        form = Markup(render_template("live/_question.html", survey=survey, items=page_items, ref=key, lang=lang, assets=assets,
                                      answers={k: v for k, v in given.items() if v not in (None, "", [])},
                                      errors={}, variables={}, multi=False, instant=_instant(survey, page_items),
                                      page=_page_ids(page_items)))
    elif key.startswith("slide-") and item is not None:  # the presentation slide, drawn on the phone (slide_phone)
        from markupsafe import Markup, escape

        src = _slide_image(survey, item)
        # without an approved picture (a ZIP without its images) the slide's name instead of a broken image
        form = (Markup('<img class="live-phone-slide" src="{}" alt="{}">').format(src, escape(item.text("alt", lang))) if src
                else Markup('<p class="live-phone-slide-missing">{}</p>').format(escape(item.text("alt", lang))))
    elif item is not None and (key.startswith(("q-", "full-")) or (key == "end" or key.startswith("answered-")) and request.args.get("aendern")):
        # drawn here, not loaded into the page: the scripts of sliders, ranking or the 2D field bind on page load
        given = None if _multi(survey, item) or response is None else (response.answers or {}).get(str(item.id))
        from markupsafe import Markup

        form = Markup(render_template("live/_question.html", survey=survey, items=[item], ref=f"q-{item.id}", lang=lang, assets=assets,
                               answers={item.id: given} if given not in (None, "", []) else {}, errors={}, variables={},
                               multi=_multi(survey, item), instant=_instant(survey, [item]),
                               word_left=(_word_limit(item) - _sent(survey, st, item)) if _multi(survey, item) and _word_limit(item) else None,
                               full=key.startswith("full-"),
                               words=[(r.id, t) for r, t in _my_words(survey, st, response, item)] if _multi(survey, item) else []))
    return render_template("live/join.html", survey=survey, lang=lang, assets=assets, poll=POLL_SECONDS, end=end_text(survey, lang, "thanks"),
                           key=key, form=form, device=device_token(), run=st["run"]["id"], quiz=any(quiz_of(st, i) for i in questions(survey)),
                           emojis=reaction_set(survey) if options(survey)["reactions"] and key != "ended" else (),
                           talk=options(survey)["qa"] and not questions(survey),
                           nick=_nick(survey, st, response)[0], teams=st.get("teams") or [], team=_nick(survey, st, response)[1])


@public_bp.route("/<code>/state")
def where(code):
    """What the phone shows now, as a key that changes whenever the page has to change."""
    survey = _public(code)
    st = state(survey)
    response = _response(survey, st)
    key, item = _phone(survey, st, response)
    out = {"key": key, "at": st.get("at", 0)}
    end = deadline(st, item) if item is not None else None
    if key.startswith("p-"):  # in blocks: the countdown of a quiz question on the page, from when it was shown (T-22)
        end = _page_deadline(survey, st, st.get("block"), _block_phone(survey, st, response)[2])
        if end:
            out["left"] = max(0, round(end - time.time()))
    if end and key.startswith(("q-", "answered-")):
        out["left"] = max(0, round(end - time.time()))
    quiz = quiz_of(st, item) if item is not None else None
    if quiz and key.startswith("closed-") and response is not None:  # how it went: right or not, points, place
        board = leaderboard(survey, st)
        place = next((p for p, e in zip(shared_places([e[2] for e in board]), board) if e[0] == response.id), None)
        value = (response.answers or {}).get(str(item.id))
        got = points(item, quiz, value, ((response.layout or {}).get("live_times") or {}).get(str(item.id)))
        out["quiz"] = {"answered": value not in (None, "", []), "right": got > 0, "points": got,
                       "total": next((e[2] for e in board if e[0] == response.id), 0), "place": place, "of": len(board)}
        out["key"] = f"{key}-q{got}-{place}"  # the note changes when the place does
    return jsonify(**out)


@public_bp.route("/<code>/name", methods=["POST"])
def nickname(code):
    """A voluntary nickname for the leaderboard of the quiz (decision L3): kept with this device's participation
    in its layout, never in the export."""
    survey = _public(code)
    st = state(survey)
    stale = _stale_run(st)
    if stale is not None:
        return stale
    name, team = _nick_form(survey, st, "name", "team")
    if name is None:
        return jsonify(ok=False, error="name"), 400
    response = _response(survey, st)
    if response is None:
        # no answer yet: the name waits in the session for the first one (_new_response). A device that only sets
        # a name is no participation (it would count as a completed one without answers)
        _drop_old_runs(survey, st)
        session[_nick_key(survey, st)] = {"name": name, "team": team}
        return jsonify(ok=True, name=name)
    response.layout = {**(response.layout or {}), "live_nick": name, "live_team": team}
    db.session.commit()
    return jsonify(ok=True, name=name)


@public_bp.route("/<code>/reaction", methods=["POST"])
def react(code):
    """Emojis for the screen (package G): up to REACTION_BURST in one request (quick clicks, collected by the
    phone), at most one request every REACTION_GAP seconds per device."""
    from datetime import timedelta

    from app.models import LiveFeedback, LiveReaction

    survey = _public(code)
    stale = _stale_run(state(survey))
    if stale is not None:
        return stale
    emojis = request.form.getlist("e")[:REACTION_BURST]
    if not options(survey)["reactions"] or not emojis or any(e not in reaction_set(survey) for e in emojis):
        return jsonify(ok=False), 400
    now = time.time()
    if now - (devices.read(survey, state(survey), "reaction", 0) or 0) < REACTION_GAP:
        return jsonify(ok=False, error="slow"), 429
    from app import limits

    if not limits.allow_live("react"):
        return jsonify(ok=False, error="slow"), 429
    devices.write(survey, state(survey), "reaction", now)
    db.session.add_all([LiveReaction(survey_id=survey.id, emoji=e) for e in emojis])
    # counted for the slide on the screen, for the presenter view and the review (no device named, like "Unklar" was)
    st = state(survey)
    db.session.add_all([LiveFeedback(survey_id=survey.id, run_id=st["run"]["id"], item_id=st.get("screen"), kind="e:" + e)
                        for e in emojis])
    db.session.execute(db.delete(LiveReaction).where(LiveReaction.survey_id == survey.id,
                                                     LiveReaction.created_at < utcnow() - timedelta(minutes=2)))
    db.session.commit()
    return jsonify(ok=True)


@public_bp.route("/<code>/qa.json")
def qa_list(code):
    """Q&A on the phone: the questions so far and which of them this device voted for."""
    survey = _public(code)
    st = state(survey)
    if not options(survey)["qa"]:
        return jsonify(enabled=False)
    voted = set(devices.read(survey, st, "votes", []) or [])
    rows = [{**r, "voted": r["id"] in voted} for r in qa_rows(survey, st["run"]["id"], public=True)]
    return jsonify(enabled=True, questions=rows)


@public_bp.route("/<code>/qa", methods=["POST"])
def qa_ask(code):
    """A question from the audience; with "premod" or a swear word it waits for the presenter."""
    from app.models import LiveQuestion

    survey = _public(code)
    st = state(survey)
    stale = _stale_run(st)
    if stale is not None:
        return stale
    opts = options(survey)
    if not opts["qa"]:
        return jsonify(ok=False, error="closed"), 409
    text = " ".join(request.form.get("text", "").split())[:280]
    if not text:
        return jsonify(ok=False, error="empty"), 400
    if (devices.read(survey, st, "asked", 0) or 0) >= QA_PER_DEVICE:
        return jsonify(ok=False, error="many"), 429
    from datetime import timedelta

    from flask import current_app

    from app import limits

    cfg = current_app.config
    in_run = db.select(db.func.count(LiveQuestion.id)).filter(LiveQuestion.survey_id == survey.id,
                                                              LiveQuestion.run_id == st["run"]["id"])
    if not limits.allow_live("qa") or not limits.allow_live_run("qa", survey, st["run"]["id"]):
        return jsonify(ok=False, error="many"), 429
    if cfg["LIVE_RUN_QUESTIONS"] and db.session.scalar(in_run) >= cfg["LIVE_RUN_QUESTIONS"]:
        # turns nobody away (S5 of the audit 2026-10-06): reported; with a flood they wait for the presenter (below)
        limits.note_cap(survey, "live_qa", st["run"])
    # a flood (many questions within a minute, a script or a whole room) waits for the presenter, whatever the setting
    flood = cfg["LIVE_QA_FLOOD"] and db.session.scalar(in_run.filter(
        LiveQuestion.created_at >= utcnow() - timedelta(minutes=1))) >= cfg["LIVE_QA_FLOOD"]
    status = "pending" if opts["premod"] or flood or (opts["filter"] and _bad(text)) else "open"
    db.session.add(LiveQuestion(survey_id=survey.id, run_id=st["run"]["id"], text=text, status=status, item_id=st["screen"]))
    devices.write(survey, st, "asked", (devices.read(survey, st, "asked", 0) or 0) + 1)
    db.session.commit()
    return jsonify(ok=True, pending=status == "pending")


@public_bp.route("/<code>/qa/<int:qid>/vote", methods=["POST"])
def qa_vote(code, qid):
    """Vote a question up, or take the vote back: once per device (its server receipt)."""
    from app.models import LiveQuestion

    survey = _public(code)
    st = state(survey)
    stale = _stale_run(st)
    if stale is not None:
        return stale
    row = db.session.get(LiveQuestion, qid)
    if row is None or row.survey_id != survey.id or row.run_id != st["run"]["id"] or row.status not in ("open", "answered"):
        return jsonify(ok=False), 404
    voted = list(devices.read(survey, st, "votes", []) or [])
    from app import limits

    if qid not in voted and not limits.allow_live("vote"):  # taking a vote back is always possible
        return jsonify(ok=False, error="slow"), 429
    if qid not in voted and not devices.allow_sender(survey, st, f"qa-{qid}"):  # BIZ-02: per network and question
        return jsonify(ok=False, error="slow"), 429
    # counted by the database in one step: in a lecture hall many vote for the same question within a second, and
    # two requests that both read n wrote n + 1 (code review 2026-10-07, D3)
    if qid in voted:
        voted.remove(qid)
        change = db.case((LiveQuestion.votes > 0, LiveQuestion.votes - 1), else_=0)
    else:
        voted.append(qid)
        change = LiveQuestion.votes + 1
    db.session.execute(db.update(LiveQuestion).where(LiveQuestion.id == row.id).values(votes=change)
                       .execution_options(synchronize_session=False))
    devices.write(survey, st, "votes", voted)
    db.session.commit()  # also expires the row: its count is read again below
    return jsonify(ok=True, votes=row.votes, voted=qid in voted)


def _just_left(survey, st, ref):
    """The question the presenter moved away from within the last LEAVE_GRACE seconds, if the answer is for it (T-16:
    tapped while the slide changed); a question closed on purpose is not."""
    left = st.get("left") or {}
    if not left.get("id") or ref != f"q-{left['id']}" or time.time() - (left.get("at") or 0) > LEAVE_GRACE:
        return None
    return next((i for i in questions(survey) if i.id == left["id"]), None)


@public_bp.route("/<code>/answer", methods=["POST"])
def answer(code):
    """The answer to the question on the screen, while voting on it is open. A second one replaces the first,
    except for a short text where several are allowed: each further one is a participation of its own."""
    from app.items.base import AnswerError

    survey = _public(code)
    st = state(survey)
    stale = _stale_run(st)
    if stale is not None:
        return stale
    if by_blocks(survey):
        return _block_answer(survey, st)
    item = _current(survey, st)
    if item is None or request.form.get("ref") != f"q-{item.id}":
        item = _just_left(survey, st, request.form.get("ref", ""))
    if item is None or item.type in CONTENT or item.id in st["closed"]:
        return jsonify(ok=False, error="closed"), 409  # the phone says that voting on it was over (T-16)
    if deadline(st, item) and time.time() > deadline(st, item) + 1:  # the countdown has run out (a second's grace)
        return jsonify(ok=False, error="closed"), 409
    try:
        value = item.kind.parse_answer(item, request.form, survey.default_language)
    except AnswerError as e:
        return jsonify(ok=False, error="invalid", errors={str(item.id): str(e)}), 400
    if value in (None, "", []):
        return jsonify(ok=False, error="empty"), 400
    multi = _multi(survey, item)
    sent = _sent(survey, st, item) if multi else 0
    if multi and _word_limit(item) and sent >= _word_limit(item):  # as many as allowed per device were sent
        return jsonify(ok=False, error="full", key=f"full-{item.id}"), 409
    response = _response(survey, st)
    if multi and any(_key(text) == _key(value) for _, text in _my_words(survey, st, response, item)):
        # the same word again from the same device does not push it up the cloud (Ralf, 2026-10-16)
        return jsonify(ok=False, error="invalid", errors={str(item.id): tr(
            survey.default_language, "Dieses Wort haben Sie schon gesendet.", "You have already sent this word.")}), 400
    extra = response is not None and _multi(survey, item) and (response.answers or {}).get(str(item.id)) not in (None, "", [])
    if (response is None or extra) and not _may_join(survey, st):
        return _busy(survey)
    first = response is None or extra or (response.answers or {}).get(str(item.id)) in (None, "", [])
    if first and not devices.allow_sender(survey, st, f"q-{item.id}"):  # one more device on it from this network
        return _busy(survey)
    if response is None:
        response = _response(survey, st, create=True)
    _keep_nick(survey, st, response)  # with the participation, not with a further word
    if extra:
        response = _new_response(survey, st, extra=True, main=response)
    before = (response.answers or {}).get(str(item.id))
    response.answers = {**(response.answers or {}), str(item.id): value}
    response.completed_at = utcnow()
    opened = (st.get("opened") or {}).get(str(item.id))
    kept = ((response.layout or {}).get("live_times") or {}).get(str(item.id))
    # seconds from the question coming up to this answer (points of the quiz). The same answer sent again (a retry
    # after a lost reply, a second tap) keeps the time it had: it lowered the points; an answer changed on purpose
    # takes the new time (Ralf, audit 2026-10-23, DS-05)
    if quiz_of(st, item) and opened and not (kept is not None and before == value):
        layout = dict(response.layout or {})
        layout["live_times"] = {**(layout.get("live_times") or {}), str(item.id): round(time.time() - opened, 2)}
        response.layout = layout
    if multi:
        devices.write(survey, st, "sent", {**(devices.read(survey, st, "sent", {}) or {}), str(item.id): sent + 1})
    db.session.commit()
    if multi:  # the phone empties the field and keeps the cursor in it, without loading the page anew
        limit = _word_limit(item)
        return jsonify(ok=True, key=_multi_key(survey, st, item, sent + 1), left=limit - sent - 1 if limit else None,
                       word={"id": response.id, "text": str(value)})
    return jsonify(ok=True)


@public_bp.route("/<code>/withdraw", methods=["POST"])
def withdraw(code):
    """Take back one of this device's words to the word cloud on the screen while voting on it is open (Ralf,
    2026-10-16): a further word goes with its row, the first one leaves the participation without this answer; the
    word no longer counts towards the limit per device."""
    survey = _public(code)
    st = state(survey)
    stale = _stale_run(st)
    if stale is not None:
        return stale
    item = _current(survey, st)
    if (item is None or by_blocks(survey) or request.form.get("ref") != f"q-{item.id}" or not _multi(survey, item)
            or item.id in st["closed"] or (deadline(st, item) and time.time() > deadline(st, item) + 1)):
        return jsonify(ok=False, error="closed"), 409
    mine = {r.id: r for r, _ in _my_words(survey, st, _response(survey, st), item)}
    row = mine.get(request.form.get("word", type=int))
    if row is None:
        return jsonify(ok=False, error="gone"), 404
    if (row.params or {}).get("live_extra"):
        from app.live.base import _flush_deleted

        pid = row.pid
        db.session.delete(row)
        _flush_deleted("live_word")
    else:
        pid = None
        row.answers = {k: v for k, v in (row.answers or {}).items() if k != str(item.id)}
    _forget_responses()
    sent = max(0, _sent(survey, st, item) - 1)
    devices.write(survey, st, "sent", {**(devices.read(survey, st, "sent", {}) or {}), str(item.id): sent})
    db.session.commit()
    if pid:  # kept results and states of the dashboards counted it too (as for a deleted run)
        from app import erasure

        try:
            erasure._forget_in_files(survey, {pid})
        except OSError:
            pass
    limit = _word_limit(item)
    return jsonify(ok=True, key=_multi_key(survey, st, item, sent), left=limit - sent if limit else None)
