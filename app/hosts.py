"""Own address for participants (concept "eigene Adresse für Teilnehmende", 2026-10-08; decisions E1 to E11).

Participants take part under PARTICIPANT_BASE_URL (deploy/docker: https://ask.askurious.de), researchers work under
PUBLIC_BASE_URL. The browser keeps the cookies of the two names apart (the "__Host-" prefix binds them to one
host), so a researcher's sign-in never reaches a participant page, and a script slipped into a participant page
never reads a researcher's session.

Three ways to run, chosen in .env:
- neither PARTICIPANT_BASE_URL nor SIDE (the standard, the test server, local development): one address answers
  everything, as before;
- PARTICIPANT_BASE_URL only (one process behind two names, e.g. a second sub-domain in Plesk): the name a request
  came to decides its side;
- SIDE=participant or SIDE=researcher as well (deploy/docker: one container per side): each process answers its
  own side only, whatever the name.

Each side answers its own paths. On the participants' address the researchers' pages answer 404 and the public
pages (landing page, imprint) move to the main address; on the main address the participants' paths move
permanently to the participants' address (E4), taking a participation under way along (E5, until
PARTICIPANT_HANDOVER_UNTIL) and, for its researchers, the right to see a draft that never started (E6).
The researchers' session ends with the browser on the main address (E7): only participants need it to last a week.

The handover (code review 2026-10-08, H1 to H4): only a random id goes into the address, its content lies in the
table participant_handover and is taken once; the mark "took part" (qdone_ cookie) goes along as well. The session
cookie of the participants' side is signed with a salt of its own (app/cookies.py), so a cookie of the one address
is worth nothing on the other: nobody can bring the session of the participants' address to the main address and
have it handed over to someone else's browser.
"""
import hashlib
import json
import re
import secrets
from datetime import date, timedelta
from urllib.parse import urlencode

from flask import after_this_request, current_app, has_request_context, redirect, request, session
from flask import url_for as flask_url_for

from app import brand
from app.timeutil import local_today
from config import url_host

PARTICIPANT, RESEARCHER, SHARED = "participant", "researcher", "shared"

# the side of every blueprint (tests/participation/test_hosts.py: a new blueprint has to be entered here)
BLUEPRINT_SIDES = {
    # without sign-in (E3): survey, live vote on the phone, pretest and phone link, public dashboards, quota links
    "participate": PARTICIPANT, "live_public": PARTICIPANT, "preview_share": PARTICIPANT,
    "board_share": PARTICIPANT, "quota_share": PARTICIPANT,
    # the interviewee's own screen for the tasks of an interview (app/interview_screen.py): the device's pages; the
    # interview view's side of it is "interview_screen" below
    "interview_screen_public": PARTICIPANT,
    # the weather demo of an external section (app/demo_webapp.py): participants come to it from a survey
    "demo_webapp": PARTICIPANT,
    # both: images and other uploads, map data
    "media": SHARED, "geo": SHARED,
    # diary studies (app/diary.py): the app's API, the join link and the entry addresses for participants, the
    # schedule for researchers
    "diary_api": PARTICIPANT, "diary": RESEARCHER,
    # the model access of a survey with the element "KI-Chat" (app/ai_chat.py); the chat itself is participate.chat
    "ai_chat": RESEARCHER,
    # linking surveys as waves of a study (app/waves.py), for researchers
    "waves": RESEARCHER,
    # the monitoring feed (app/monitoring_feed.py), by token, on the researchers' address
    "monitoring_feed": RESEARCHER,
    # categorising open answers (app/coding.py), for researchers
    "coding": RESEARCHER,
    # paper questionnaires (app/paper.py), for researchers
    "paper": RESEARCHER,
    # the news page /blog with its feed and sitemap, and its administration: on the address of the landing page
    "blog": RESEARCHER,
    # registration links of the platform (app/signup_links.py): whoever opens one signs in with ORCID as a new
    # researcher, so it lives on the researchers' address like the sign-in itself
    "signup_links": RESEARCHER,
    # the rest needs a sign-in, is for machines (cron, Slack, Rechenknecht, ORCID and Zenodo callbacks) or is the
    # landing page and the legal pages of the platform
    **{name: RESEARCHER for name in (
        "public", "auth", "accounts", "account_setup", "twofactor", "invites", "joinlinks", "bulk_invites", "survey_invites", "dashboard", "constructs", "element_types", "terms", "feedback",
        "admin", "designs", "compute", "groups", "org", "samplesize", "wizard", "surveys", "cron", "editor", "flow_page",
        "boards", "translations", "zenodo_auth", "coscine", "ai_access", "slack", "live", "interview", "interview_ai", "diary_ai",
        "interview_data", "interview_results", "interview_audio", "interview_transcribe", "interview_search",
        "interview_summaries", "interview_remote", "interview_screen", "interview_offline", "interviewer", "transcript_coding", "ainstein", "learning", "study_plan", "data_access",
        "agent_access", "mcp",
        "handbook")},
}
# endpoints of the researchers' blueprints that both sides answer: health check, robots.txt, the tab icon
SHARED_ENDPOINTS = {"static", "public.healthz", "public.robots", "public.favicon"}

HANDOVER_PARAM = "uebergabe"
HANDOVER_SECONDS = 120  # the redirect follows at once; the id is in the address, so it must not live long
# the keys a participation keeps in the session per survey, each followed by the survey's id (code review 2026-10-08
# H3: named one by one; a pattern took the researchers' preview keys pvp, pvq, pvg, pc along): participate.
# _session_key r, _pid_key rp, _pending_key n, _declined_key d, _returned_key xr, _stale_key st, _relayout_key rl,
# _split_key x, _split_done_key xd; panel._key pp (stored link parameters); survey_invites.session_key iv (the
# entry of an invitation list, never the token). The CSRF token goes along, so that a page
# sent to the old address is taken on the new one.
PARTICIPATION_PREFIXES = ("r", "rp", "n", "d", "xr", "st", "rl", "x", "xd", "pp", "iv", "da")


def _participation_keys(survey_id):
    return {f"{prefix}{survey_id}" for prefix in PARTICIPATION_PREFIXES}


_KEY_OF = re.compile("(" + "|".join(sorted(PARTICIPATION_PREFIXES, key=len, reverse=True)) + ")([0-9]+)")
OLD_COOKIE = {"old": True}  # payload of a fingerprint row written for a cookie signed the way before


def claim_old_cookie(data):
    """The participants' address reads a session cookie signed with the salt of before (app/cookies.py, until
    PARTICIPANT_OLD_COOKIE_UNTIL): the participations in it are bound to this address here (audit 2026-10-09, A1).
    A cookie of the main address, copied here, used to keep its participation and the same cookie on the main
    address could still hand it over to someone else's browser: then both answered into one participation. Now
    each participation of such a cookie writes its fingerprint into participant_handover, so that a handover of it
    later moves nothing; one that was handed over already (a handover row with this fingerprint) is dropped from
    the cookie here. Reading the same old cookie again finds its own row and keeps the participation. Returns
    `data`, changed in place."""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models import ParticipantHandover as H
    from app.models import utcnow

    ids = {int(m.group(2)) for m in map(_KEY_OF.fullmatch, list(data.keys())) if m}
    if not ids:
        return data
    try:
        for survey_id in sorted(ids):
            names = _participation_keys(survey_id)
            keys = {k: data[k] for k in list(data.keys()) if k in names}
            fingerprint = _fingerprint(survey_id, keys)
            row = db.session.scalar(db.select(H).where(H.fingerprint == fingerprint))
            if row is None:
                db.session.add(H(id=secrets.token_urlsafe(32), survey_id=survey_id, fingerprint=fingerprint,
                                 created_at=utcnow(), payload=dict(OLD_COOKIE), used=True))
                db.session.commit()
            elif row.payload != OLD_COOKIE:  # handed over from the main address already: this is a copy
                for k in keys:
                    data.pop(k, None)
    except IntegrityError:  # a handover of the same participation at this very moment: it wins
        db.session.rollback()
        for survey_id in ids:
            for k in _participation_keys(survey_id):
                data.pop(k, None)
    return data


def participant_base():
    """The address participants' links begin with (no slash at the end), on the domain of this request's brand."""
    return brand.participant_base_url() or brand.public_base()


def split():
    """The two sides are apart (PARTICIPANT_BASE_URL or SIDE in .env)."""
    return bool(current_app.config.get("PARTICIPANT_BASE_URL") or current_app.config.get("SIDE"))


def current_side():
    """PARTICIPANT or RESEARCHER for this request; None while one address answers everything."""
    if not split():
        return None
    side = current_app.config.get("SIDE")
    if side in (PARTICIPANT, RESEARCHER):
        return side
    if not has_request_context():
        return RESEARCHER
    here = url_host("//" + request.host)
    return PARTICIPANT if here == url_host(brand.participant_base_url()) else RESEARCHER


def on_participant_side():
    return current_side() == PARTICIPANT


def side_of(endpoint):
    """The side an endpoint belongs to (SHARED for both); None for an unknown one."""
    if not endpoint:
        return None
    if endpoint in SHARED_ENDPOINTS:
        return SHARED
    return BLUEPRINT_SIDES.get(endpoint.rsplit(".", 1)[0] if "." in endpoint else None)


def diary_on_main():
    """Diary studies run on the main address (config DIARY_ON_MAIN_ADDRESS): their participants' paths are answered
    there, and their links (join link, entry address) carry it."""
    return bool(current_app.config.get("DIARY_ON_MAIN_ADDRESS"))


def _diary_request():
    """This request is one of a diary study's participants: the app's API, the join link or an entry address
    (diary_api), or a questionnaire page of a diary survey (participate, survey.sampling)."""
    if request.blueprint == "diary_api":
        return True
    if request.blueprint != "participate":
        return False
    uid = (request.view_args or {}).get("uid")
    if not uid:
        return False
    from app import links

    survey = links.resolve(uid)
    return survey is not None and survey.sampling is not None


def url_for(endpoint, **values):
    """flask.url_for for the templates (and code that runs on both sides): on the participants' address a link to a
    page of the main address (imprint, landing page) carries that address, since this one does not answer it.
    Links to participant pages on the main address stay relative: the redirect there takes the right to see a
    draft along (E6). _external=True builds with the address of the endpoint's side, not the request's."""
    external = values.pop("_external", False)
    if endpoint.startswith(".") and has_request_context() and request.blueprint:
        endpoint = request.blueprint + endpoint
    url = flask_url_for(endpoint, **values)
    side = current_side()
    target = side_of(endpoint)
    if target == PARTICIPANT and endpoint.startswith("diary_api.") and diary_on_main():
        # the join link and the entry addresses of a diary study lead to the main address (DIARY_ON_MAIN_ADDRESS)
        return brand.public_base() + url if external or side == PARTICIPANT else url
    if side is None:  # one address: absolute addresses from PUBLIC_BASE_URL, never from the Host header (H10)
        return brand.public_base() + url if external else url
    if target == PARTICIPANT:
        return participant_base() + url if external else url
    if target == RESEARCHER and (external or side == PARTICIPANT):
        return brand.public_base() + url
    if external:  # shared: the address of this side
        return (participant_base() if side == PARTICIPANT else brand.public_base()) + url
    return url


def day_setting(value):
    """A day setting (PARTICIPANT_HANDOVER_UNTIL and the like) as a date; None when empty. ValueError for anything but
    YYYY-MM-DD (create_app checks it at the start, H5)."""
    return date.fromisoformat(value) if value else None


def _handover_open():
    try:
        until = day_setting(current_app.config.get("PARTICIPANT_HANDOVER_UNTIL"))
    except ValueError:  # create_app refuses such a value; one changed while running closes the handover
        return False
    return until is not None and local_today() <= until


def _forget_old_handovers(now):
    """Handovers without a participation go after a few minutes; a fingerprint stays as long as a copied session
    cookie of the main address is good (PERMANENT_SESSION_LIFETIME), and a day more."""
    from app.extensions import db
    from app.models import ParticipantHandover as H

    kept = current_app.permanent_session_lifetime + timedelta(days=1)
    db.session.execute(db.delete(H).where(db.or_(
        H.created_at < now - kept,
        db.and_(H.fingerprint.is_(None), H.created_at < now - timedelta(seconds=10 * HANDOVER_SECONDS)))))


def _fingerprint(survey_id, keys):
    data = json.dumps([survey_id, sorted((k, v) for k, v in keys.items() if k != "csrf_token")], default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def _handover_id():
    """What the participants' address needs of this browser for the survey in this /s/ path: the keys of a
    participation under way (E5), whether its researchers may see it as a draft (E6) and the mark "took part"
    (qdone_ cookie, H4, also once the handover of participations has ended). Stored in participant_handover under a
    random id, which alone goes into the address (H1, H2). None: nothing to take along. The keys leave this session:
    from now on the survey runs on the other address. A participation moves once: a copy of this session cookie
    brings the same keys again, and they stay here."""
    from flask_login import current_user
    from sqlalchemy.exc import IntegrityError

    from app import links
    from app.extensions import db
    from app.models import ParticipantHandover as H
    from app.models import Survey, utcnow
    from app.participate import PRETEST_KEY

    survey = links.resolve((request.view_args or {}).get("uid"))
    if survey is None:
        return None
    keys = {}
    if _handover_open():
        names = _participation_keys(survey.id)
        keys = {k: session[k] for k in list(session.keys()) if k in names}
    for k in keys:
        session.pop(k, None)
    draft = survey.status == Survey.DRAFT and survey.opened_at is None and survey.live is None and (
        survey.id in (session.get(PRETEST_KEY) or [])
        or current_user.is_authenticated and survey.can(current_user, "viewer"))
    marked = request.cookies.get(f"qdone_{survey.code}") == "1"
    now = utcnow()
    _forget_old_handovers(now)
    fingerprint = None
    if keys:
        fingerprint = _fingerprint(survey.id, keys)
        row = db.session.scalar(db.select(H).where(H.fingerprint == fingerprint))
        if row is not None and not row.used:  # moved before, but never taken (the redirect broke off): anew
            db.session.delete(row)
            db.session.flush()
        elif row is not None:  # this participation is on the other address already: a copied cookie
            keys, fingerprint = {}, None
        if keys and "csrf_token" in session:
            keys["csrf_token"] = session["csrf_token"]
    if not keys and not draft and not marked:
        db.session.commit()
        return None
    hid = secrets.token_urlsafe(32)
    db.session.add(H(id=hid, survey_id=survey.id, fingerprint=fingerprint, created_at=now,
                     payload={"k": keys, "v": bool(draft), "m": marked}))
    try:
        db.session.commit()
    except IntegrityError:  # the same participation moved by a second request at this very moment
        db.session.rollback()
        return None
    return hid


def _take_handover(hid):
    """The participants' address takes what the main address stored under this id (see _handover_id), once."""
    from app.extensions import db
    from app.models import ParticipantHandover as H
    from app.models import Survey, utcnow
    from app.participate import MARKER_DAYS, PRETEST_KEY

    if not hid or len(hid) > 64:
        return
    row = db.session.get(H, hid)
    if row is None or row.used or row.created_at < utcnow() - timedelta(seconds=HANDOVER_SECONDS):
        return  # unknown, taken or expired: the participant starts or goes on with what this browser holds
    data, sid = row.payload or {}, row.survey_id
    taken = db.session.execute(db.update(H).where(H.id == hid, H.used.is_(False)).values(used=True, payload=None))
    db.session.commit()
    if taken.rowcount != 1:  # a second request with the same id at the same moment
        return
    names = _participation_keys(sid) | {"csrf_token"}
    keys = {k: v for k, v in (data.get("k") or {}).items() if k in names} if _handover_open() else {}
    for k, v in keys.items():
        if k not in session:  # what this browser did on the new address already stays
            session[k] = v
    if keys:
        session.permanent = True  # participants may pause for a week, as before
    if data.get("v"):
        seen = list(session.get(PRETEST_KEY) or [])
        if sid not in seen:
            session[PRETEST_KEY] = seen[-9:] + [sid]
    survey = db.session.get(Survey, sid) if data.get("m") else None
    if survey is not None:  # the mark "took part" of the main address, as long as there (participate._mark)
        name = f"qdone_{survey.code}"

        @after_this_request
        def _mark(response):
            response.set_cookie(name, "1", max_age=MARKER_DAYS * 86400, httponly=True, samesite="Lax",
                                secure=current_app.config["SESSION_COOKIE_SECURE"],
                                path=flask_url_for("participate.page", uid=""))
            return response


def _without_handover():
    args = [(k, v) for k, v in request.args.items(multi=True) if k != HANDOVER_PARAM]
    return request.path + ("?" + urlencode(args) if args else "")


def guard():
    """before_request: each side answers its own paths (registered before the CSRF check, which needs the token a
    handover brings)."""
    side = current_side()
    if side is None or request.endpoint is None:  # one address for all; or no route: the 404 of Flask
        return None
    target = side_of(request.endpoint)
    moved = 308 if request.method not in ("GET", "HEAD") else 301
    if side == PARTICIPANT:
        if HANDOVER_PARAM in request.args:
            _take_handover(request.args[HANDOVER_PARAM])
            if request.method in ("GET", "HEAD"):
                return redirect(_without_handover(), code=303)
        if target in (PARTICIPANT, SHARED):
            return None
        if request.blueprint == "public":  # landing page and legal pages live on the main address
            return redirect(brand.public_base() + request.full_path.rstrip("?"), code=moved)
        from flask import abort

        abort(404)
    # the main address
    if session.permanent:  # E7: the researchers' session ends with the browser ("Angemeldet bleiben": 30 days)
        session.permanent = False
    if target == PARTICIPANT and current_app.config.get("PARTICIPANT_BASE_URL") and not (diary_on_main() and _diary_request()):
        url = participant_base() + request.full_path.rstrip("?")
        hid = _handover_id() if request.blueprint == "participate" else None
        if hid:  # never kept by the browser: a handover is taken once, and the next visit needs its own
            url += ("&" if "?" in url else "?") + urlencode({HANDOVER_PARAM: hid})
            moved = 307 if moved == 308 else 302
        return redirect(url, code=moved)
    return None
