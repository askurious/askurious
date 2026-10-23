"""Limits against automated abuse: new participations per sender, wrong codes and secrets, comments over the phone
link, and AI calls per researcher. All limits come from the configuration; 0 switches one off. The caps of a survey or
of a live run as a whole refuse nobody: they report to the researchers (note_cap).

Three kinds of counters (security audit 2026-10-13, TN-2/DP-3; audit 2026-10-18, BIZ-01):
- Limits against load that a script uses to fill a survey (new participations per sender and per sender and survey,
  live votes, complaints and their mails of receipt) count in the database for all processes and containers
  (table rate_count, _take_counted): one row per kind, sender and time slot, one indexed sum and one upsert per counted
  request, in the request's own transaction. In memory they were up to 24 times as wide (2 containers × 12 processes).
  A row keeps the kind, a keyed digest of the sender (with a key that changes every day) and the slot, never the
  address, and goes once the longest window is over (sweep, at most a day). If the database cannot count, the process
  counts in its memory as before (a load limit must not turn real participants away).
- The other limits against load (dashboard links, read and agent tokens, diary requests of a person, images on
  comments) count in memory only, per server process, on a hash of the sender's address with a salt that is new after
  every restart. Nothing is stored or logged.
- Limits that guard a secret (deletion codes, survey and diary codes, sign-in with a password and its pause per
  account, "Passwort vergessen?", /verify, read tokens, secrets of the diary app, invitations and join links) count in
  the database (table rate_hit, _take_shared), so that they hold across all processes and containers and after a
  restart; in memory they were up to 24 times as wide. A row keeps the kind, a keyed digest of the sender (with a key
  that changes every day) and the time, never the address, and goes once the longest window is over (sweep, every
  minute). If the database cannot enforce the limit, the protected attempt is refused. Wrong codes count per sender
  and, for IPv6, also per /48 (CODE_WIDE_LIMIT), and wrong test and invitation codes of a diary study also per study
  (STUDY_CODE_LIMIT; BIZ-07): one /48 holds 65 536 senders of their own.
nginx brakes the same addresses per address in front of the app (deploy/docker/nginx, limit_req), as a second line.
"""
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import Counter, deque
from contextlib import nullcontext
from datetime import timedelta

from flask import current_app, request

from app import brand
from app.extensions import db

_SALT = secrets.token_bytes(16)
_lock = threading.Lock()
MAX_SENDERS = 20000  # beyond this, senders without recent starts are dropped


def _network():
    """The sender as a text: the IPv4 address, or the /64 of an IPv6 address (a single connection gets at least that
    many addresses, and each of them would be a sender of its own; S4 of the audit 2026-10-06); an IPv4 address
    written as IPv6 (::ffff:a.b.c.d) is the IPv4 address. None if the address says nothing about the sender (missing,
    or the local reverse proxy because PROXY_COUNT is not set): then nobody is limited rather than everybody at once."""
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return None
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)


def _network_wide():
    """The /48 of an IPv6 sender, as a text, or None (IPv4, or an address that says nothing; see _network). A /48 is
    what a hoster hands out cheaply: 65 536 senders of their own for the limits per /64 (audit 2026-10-18, BIZ-07)."""
    key = _network()
    if key is None or ":" not in key:
        return None
    return str(ipaddress.ip_network(key).supernet(new_prefix=48))


def _sender():
    """Hash of the sender's address for the counters in memory (salt new at every start), or None (see _network)."""
    key = _network()
    return None if key is None else hashlib.sha256(_SALT + key.encode()).digest()[:12]


def _shared_key(*parts):
    """The key of a row of rate_hit: a digest of the parts with the server's key and today's date (UTC), so that the
    same sender has the same key in every process, but no row says which address it was. At midnight (UTC) every
    sender starts anew; the windows are minutes to an hour."""
    secret = str(current_app.config.get("SECRET_KEY") or "").encode()
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time()))
    text = "|".join(str(p) for p in (day,) + parts)
    return hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()[:32]


def _shared_sender():
    """The sender's key for rate_hit, or None (see _network)."""
    key = _network()
    return None if key is None else _shared_key("sender", key)


# --- refused requests, counted for the security log (DP-7/AU-7) ---------------------------------------------------

REFUSED_EVERY = 60  # seconds: one line in the log per process with the counts of this time
_refused = Counter()
_refused_since = [time.monotonic()]


def _note_refused(kind):
    """A limit refused a request: counted per kind; once a minute the counts go into the log (security_log), without
    any address."""
    with _lock:
        _refused[kind] += 1
        now = time.monotonic()
        if now - _refused_since[0] < REFUSED_EVERY:
            return
        counts, seconds = dict(_refused), int(now - _refused_since[0])
        _refused.clear()
        _refused_since[0] = now
    from app import security_log

    security_log.event("rate_limited", counts=counts, seconds=seconds)


def _take(store, key, limit, window, count=True, kind=None):
    """Count one request of `key` in `store` (key -> times of its recent requests); False once it has `limit`
    within `window` seconds (then nothing is counted). count=False only asks. Every counter in memory goes through
    here (code review 2026-10-07, K10). kind: the name the security log gives a refusal."""
    now = time.monotonic()
    with _lock:
        times = store.setdefault(key, deque())
        while times and now - times[0] > window:
            times.popleft()
        if len(times) >= limit:
            refused = True
        else:
            refused = False
            if count:
                times.append(now)
        if len(store) > MAX_SENDERS:
            for k in [k for k, v in store.items() if not v or now - v[-1] > window]:
                del store[k]
    if refused:
        _note_refused(kind or "other")
    return not refused


# --- counters in the database, for all processes (security audit 2026-10-13, TN-2/DP-3/AU-9/KR-8) ----------------

ASK_SECONDS = 5  # a refusal to a mere question (count=False) is kept this long in the process
_asked = {}  # (kind, key) -> (time.monotonic() of the answer, the answer)
_fallback = {}  # (kind, key) -> times: counted in memory while the database does not answer
SHARED_KEEP = 3600  # rows are kept at least this long (sweep); the windows of the configuration may say more


def _hits(con, kind, key, since):
    from app.models import RateHit

    t = RateHit.__table__
    return con.scalar(db.select(db.func.count()).select_from(t).where(t.c.kind == kind, t.c.key == key, t.c.at > since))


def _take_shared(kind, key, limit, window, count=True):
    """As _take, counted in the table rate_hit in a transaction of its own (it neither commits nor rolls back the
    request's work): False once `key` has `limit` rows of `kind` within `window` seconds. count=False only asks;
    refusals are kept ASK_SECONDS in the process. An allowance is never cached: another worker may have consumed
    the budget meanwhile. Counting is serialised across workers; database failures refuse the attempt."""
    from app.models import RateHit

    if count:
        _asked.pop((kind, key), None)
    else:
        kept = _asked.get((kind, key))
        if kept is not None and not kept[1] and time.monotonic() - kept[0] < ASK_SECONDS:
            if not kept[1]:
                _note_refused(kind)
            return kept[1]
    now = time.time()
    try:
        with db.engine.begin() as con:
            if count:
                if con.dialect.name == "postgresql":
                    # Signed 64-bit, namespaced digest: the lock exists even before the first hit.
                    lock = int.from_bytes(hashlib.sha256(f"rate_hit:{kind}:{key}".encode()).digest()[:8],
                                          "big", signed=True)
                    con.execute(db.text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                elif con.dialect.name == "sqlite":
                    # A harmless write starts the driver's transaction and takes its write lock
                    # before reading; no initial savepoint that SQLite might commit independently.
                    con.execute(RateHit.__table__.delete().where(db.false()))
                else:
                    raise RuntimeError("Unsupported rate-limit database")
            now = time.time()  # after any lock wait, so the sliding window uses the actual admission time
            ok = _hits(con, kind, key, now - window) < limit
            if ok and count:
                con.execute(RateHit.__table__.insert().values(kind=kind, key=key, at=now))
    except Exception as e:  # noqa: BLE001 - unavailable shared counters must not widen security limits
        current_app.logger.warning("Rate limit %s refused: %s", kind, type(e).__name__)
        _note_refused(kind)
        return False
    if not count and not ok:
        with _lock:
            if len(_asked) > MAX_SENDERS:
                _asked.clear()
            _asked[(kind, key)] = (time.monotonic(), ok)
    if not ok:
        _note_refused(kind)
    return ok


def _shared_sender_limit(kind, limit_key, window_key, count=True):
    """A limit per sender counted in the database: the configured LIMIT within WINDOW."""
    limit, window = current_app.config[limit_key], current_app.config[window_key]
    sender = _shared_sender()
    if not limit or sender is None:
        return True
    return _take_shared(kind, sender, limit, window, count)


# --- counters against load in the database, for all processes (audit 2026-10-18, BIZ-01) ---------------------------

COUNT_SLOTS = 10  # a window is counted in this many slots: one row per slot, the window ends to a tenth exactly
_load_fallback = {}  # (kind, key) -> times: counted in memory while the database does not answer
DAY = 24 * 3600
HOUR = 3600


def _take_counted(kind, key, limit, window, count=True, amount=1):
    """As _take, counted in the table rate_count for all server processes: False once `key` has `limit` requests of
    `kind` within `window` seconds (then nothing is counted). One indexed sum over the slots of the window and, when
    counted, one upsert of the current slot, in the request's transaction (it commits with the participation, the vote
    or the complaint; a request that ends without storing anything leaves no count either). Two processes at the same
    moment may both get the last place: the limit is a brake against scripts, not a ledger. If the database cannot
    count, the process counts in its memory (fail open: a limit against load must not lock real participants out)."""
    from sqlalchemy.dialects import postgresql, sqlite

    from app.models import RateCount

    t = RateCount.__table__
    size = max(1, int(window) // COUNT_SLOTS)
    now = int(time.time())
    slot = now - now % size
    try:
        con = db.session.connection()
        # PostgreSQL: a savepoint, so that a failure does not abort the request's transaction. SQLite: none (its
        # errors leave the transaction usable, and the driver may commit an initial savepoint on its own; see sweep)
        with nullcontext() if con.dialect.name == "sqlite" else con.begin_nested():
            used = con.scalar(db.select(db.func.coalesce(db.func.sum(t.c.n), 0)).where(
                t.c.kind == kind, t.c.key == key, t.c.slot > now - window))
            ok = used + amount <= limit  # amount: 1 for a request, more for what it carries (allow_voice_bytes)
            if ok and count:
                insert = (postgresql if con.dialect.name == "postgresql" else sqlite).insert(t)
                con.execute(insert.values(kind=kind, key=key, slot=slot, n=amount).on_conflict_do_update(
                    index_elements=["kind", "key", "slot"], set_={"n": t.c.n + amount}))
    except Exception as e:  # noqa: BLE001 - see the docstring
        current_app.logger.warning("Rate count %s in memory: %s", kind, type(e).__name__)
        return _take(_load_fallback, (kind, key), limit, window, count, kind=kind)
    if not ok:
        _note_refused(kind)
    return ok


def _counted_sender_limit(kind, limit, window, *parts, count=True, amount=1):
    """A limit against load per sender (and `parts`, such as the survey) counted in rate_count. An IPv6 sender counts
    for its /48 as well, there with SENDER_WIDE_FACTOR times the limit: a /48 holds 65 536 senders of their own for the
    count per /64 (audit 2026-10-22, D-01)."""
    sender = _network()
    if not limit or sender is None:
        return True
    if not _take_counted(kind, _shared_key(kind, *parts, sender), limit, window, count, amount):
        return False
    wide, factor = _network_wide(), int(current_app.config.get("SENDER_WIDE_FACTOR") or 0)
    if wide is None or factor <= 0:
        return True
    return _take_counted(kind + "48", _shared_key(kind + "48", *parts, wide), limit * factor, window, count, amount)


def allow_voice_bytes(survey, size):
    """Count a voice recording of `size` bytes (in KB) against this sender's share of the survey's room for
    recordings (VOICE_SENDER_SHARE percent of VOICE_MAX_MB within an hour); False once it would go beyond (audit
    2026-10-22, D-02: one script filled the whole room, and real participants' recordings were refused)."""
    share = int(current_app.config.get("VOICE_SENDER_SHARE") or 0)
    if share <= 0:
        return True
    limit = max(1, int(current_app.config["VOICE_MAX_MB"]) * 1024 * share // 100)
    return _counted_sender_limit("voice_kb", limit, HOUR, survey.id, amount=max(1, -(-int(size) // 1024)))


# the windows of the counted kinds, for sweep (complaints and their mails: a day)
COUNTED_WINDOWS = ("START_WINDOW", "SURVEY_SENDER_WINDOW", "LIVE_WINDOW", "ESTIMATE_WINDOW")

# the windows of the shared kinds, for sweep: a row is needed as long as the longest of them
SHARED_WINDOWS = ("CODE_WINDOW", "LOGIN_WINDOW", "RESET_WINDOW", "ERASE_WINDOW", "VERIFY_WINDOW", "TOKEN_TRY_WINDOW",
                  "STUDY_CODE_WINDOW")
LOGIN_PAUSE_SECONDS = 900  # the longest pause per sender and account (passwords.PAUSE)


def sweep(now=None):
    """Delete the rows of rate_hit whose window is over (lifecycle.close_due_surveys, every minute). Returns how many
    went. The caller commits the deletion with its other work. PostgreSQL uses a savepoint so that a failed
    cleanup does not abort that transaction. Never raises."""
    from app.models import RateHit

    keep = max([SHARED_KEEP, LOGIN_PAUSE_SECONDS] + [int(current_app.config.get(k) or 0) for k in SHARED_WINDOWS])
    try:
        # Housekeeping may already hold SQLite's write lock. A second connection would wait for this very
        # transaction until busy_timeout; use its connection without flushing pending ORM changes here.
        con = db.session.connection()
        # SQLite errors leave the outer transaction usable. Avoid a savepoint there: the driver's legacy
        # transaction mode can release an initial savepoint as a commit before the caller commits.
        with nullcontext() if con.dialect.name == "sqlite" else con.begin_nested():
            gone = con.execute(RateHit.__table__.delete().where(RateHit.at < (now or time.time()) - keep)).rowcount
    except Exception as e:  # noqa: BLE001 - housekeeping; the next minute tries again
        current_app.logger.warning("Old rate limit rows not deleted: %s", type(e).__name__)
        return 0
    return gone + _sweep_counted(con, now)


def _sweep_counted(con, now=None):
    """The rows of rate_count whose window is over (the longest of COUNTED_WINDOWS, at least a day for the
    complaints). Never raises."""
    from app.models import RateCount

    keep = max([DAY] + [int(current_app.config.get(k) or 0) for k in COUNTED_WINDOWS])
    try:
        with nullcontext() if con.dialect.name == "sqlite" else con.begin_nested():
            return con.execute(RateCount.__table__.delete().where(
                RateCount.slot < int(now or time.time()) - keep)).rowcount
    except Exception as e:  # noqa: BLE001 - housekeeping; the next minute tries again
        current_app.logger.warning("Old rate count rows not deleted: %s", type(e).__name__)
        return 0


def allow_start():
    """Count a new participation for this sender; False once it has START_LIMIT starts within START_WINDOW. In the
    database, for all processes (rate_count; audit 2026-10-18, BIZ-01)."""
    return _counted_sender_limit("start", current_app.config["START_LIMIT"], current_app.config["START_WINDOW"])


def allow_code_try(count=True):
    """Whether this sender may look up a code (the code page /live/, app/live/, every address with a survey code, the
    codes of diary studies): False once it typed CODE_LIMIT wrong codes within CODE_WINDOW, or its IPv6 /48 typed
    CODE_WIDE_LIMIT (audit 2026-10-18, BIZ-07: a /48 holds 65 536 senders); count=True notes one more wrong code.
    Counted in the database, for all processes (audit 2026-10-13, TN-2, KR-8)."""
    if not _shared_sender_limit("code", "CODE_LIMIT", "CODE_WINDOW", count):
        return False
    limit, wide = current_app.config.get("CODE_WIDE_LIMIT", 0), _network_wide()
    if not limit or wide is None:
        return True
    return _take_shared("code48", _shared_key("wide", wide), limit, current_app.config["CODE_WINDOW"], count)


def allow_study_code_try(study_id, count=True):
    """Whether a test or invitation code of this diary study may be tried: False once STUDY_CODE_LIMIT wrong ones
    were typed for it within STUDY_CODE_WINDOW, from all senders together (audit 2026-10-18, BIZ-07). The study's
    own code is public, so the rest of the code is the secret, and one try per sender spread over many senders
    found it. Past the limit the right test code waits until the window moves on (the test codes are for the
    researchers' testers); a personal invitation code that exists gets in (diary.by_code; audit 2026-10-22, A-02).
    In the database (rate_hit)."""
    limit, window = current_app.config.get("STUDY_CODE_LIMIT", 0), current_app.config.get("STUDY_CODE_WINDOW", 3600)
    if not limit:
        return True
    return _take_shared("study_code", _shared_key("study", study_id), limit, window, count)


_boards = {}  # sender hash -> times of its recent requests for a dashboard link


def allow_board():
    """Count a request for a dashboard link (/d/<token>, page or state) for this sender; False once it has
    BOARD_LIMIT requests within BOARD_WINDOW. The limit is wide: a lecture hall often shares one address, and a
    request costs little (the state is a kept file, app/boards/). In memory only, like the starts (against load)."""
    limit, window = current_app.config["BOARD_LIMIT"], current_app.config["BOARD_WINDOW"]
    sender = _sender()
    if not limit or sender is None:
        return True
    return _take(_boards, sender, limit, window, kind="board")


def allow_login():
    """Count a sign-in attempt with a password for this sender; False once it has LOGIN_LIMIT attempts within
    LOGIN_WINDOW. In the database, for all processes (audit 2026-10-13, AU-9); the pause of a single account after
    wrong passwords is kept there too (app/passwords.py, login_failed)."""
    return _shared_sender_limit("login", "LOGIN_LIMIT", "LOGIN_WINDOW")


def login_succeeded_here():
    """A sign-in with the right password does not count toward LOGIN_LIMIT: the newest counted attempt of this
    sender goes again, so a seminar behind one address (NAT) is not kept out by its own sign-ins; only failed
    attempts fill the limit (code review 2026-10-15, H5)."""
    from app.models import RateHit

    sender = _shared_sender()
    if sender is None:
        return
    try:
        with db.engine.begin() as con:
            t = RateHit.__table__
            newest = con.scalar(db.select(t.c.id).where(t.c.kind == "login", t.c.key == sender)
                                .order_by(t.c.at.desc()).limit(1))
            if newest is not None:
                con.execute(t.delete().where(t.c.id == newest))
    except Exception as e:  # noqa: BLE001 - the row goes with the window
        current_app.logger.warning("Successful sign-in not taken off the limit: %s", type(e).__name__)


def allow_reset():
    """Count a request "Passwort vergessen?" on the sign-in page for this sender; False once it has RESET_LIMIT
    requests within RESET_WINDOW. The answer on the page is the same either way (auth.forgot_password). In the
    database, for all processes."""
    return _shared_sender_limit("reset", "RESET_LIMIT", "RESET_WINDOW")


def allow_erase():
    """Count a try of a deletion code (participant page "Antworten löschen") for this sender; False once it has
    ERASE_LIMIT tries within ERASE_WINDOW. A code has 60 random bits, so the limit is against noise, not the
    only guard. In the database, for all processes (audit 2026-10-13, TN-2)."""
    return _shared_sender_limit("erase", "ERASE_LIMIT", "ERASE_WINDOW")


def allow_verify():
    """Count a check of a cited state of a seal chain (/verify/<code>, without an account) for this sender; False once
    it has VERIFY_LIMIT checks within VERIFY_WINDOW. A check that is not kept yet verifies every timestamp up to the
    entry (audit 2026-10-09, K4). In the database, for all processes."""
    return _shared_sender_limit("verify", "VERIFY_LIMIT", "VERIFY_WINDOW")


_seal_checks = {}  # survey uid -> times of its recent "Jetzt prüfen"


def allow_seal_check(uid):
    """Count a "Jetzt prüfen" of a survey's seal chain (surveys.seal_check: the chain, every timestamp and every
    participation read); False once the survey had SEAL_CHECK_LIMIT within SEAL_CHECK_WINDOW, whoever pressed it
    (audit 2026-10-10, S5). In memory only, per process (against load)."""
    limit = current_app.config.get("SEAL_CHECK_LIMIT", 6)
    window = current_app.config.get("SEAL_CHECK_WINDOW", 600)
    if not limit:
        return True
    return _take(_seal_checks, uid, limit, window, kind="seal_check")


_tokens = {}  # read token id -> times of its recent downloads


def allow_token(token_id):
    """Count a download with a read token (app/data_access.py); False once the token has TOKEN_LIMIT downloads within
    TOKEN_WINDOW. Per token, not per sender: a script in a loop is slowed down wherever it runs, and each download
    builds the whole data set. In memory only, like the starts (against load)."""
    limit, window = current_app.config["TOKEN_LIMIT"], current_app.config["TOKEN_WINDOW"]
    if not limit:
        return True
    return _take(_tokens, token_id, limit, window, kind="token")


_agent_calls = {}  # agent token id -> times of its recent calls


def allow_agent(token_id):
    """Count a call with a token of the AI agent interface (app/mcp.py); False once the token made AGENT_LIMIT calls
    within AGENT_WINDOW. Per token, wherever the agent runs. In memory only, like the read tokens (against load)."""
    limit, window = current_app.config.get("AGENT_LIMIT", 600), current_app.config.get("AGENT_WINDOW", 3600)
    if not limit:
        return True
    return _take(_agent_calls, token_id, limit, window, kind="agent")


def allow_token_try(count=True):
    """Whether this sender may present a read token: False once it presented TOKEN_TRY_LIMIT wrong ones within
    TOKEN_TRY_WINDOW; count=True notes one more wrong one. A token has 256 random bits, so the limit is against noise,
    not the only guard. In the database, for all processes."""
    return _shared_sender_limit("token_try", "TOKEN_TRY_LIMIT", "TOKEN_TRY_WINDOW", count)


def allow_diary_token_try(count=True):
    """Whether this sender may present another wrong secret of the diary app: False once it presented TOKEN_TRY_LIMIT
    wrong ones within TOKEN_TRY_WINDOW; count=True notes one more. Asked only after a secret was not found, so a valid
    one always goes through, and counted apart from the read tokens (audit 2026-10-11, M4). In the database."""
    return _shared_sender_limit("diary_try", "TOKEN_TRY_LIMIT", "TOKEN_TRY_WINDOW", count)


def allow_invite_try(count=True):
    """Whether this sender may open another invitation or join link that leads nowhere (app/invites.py,
    app/joinlinks.py): False once it opened TOKEN_TRY_LIMIT such within TOKEN_TRY_WINDOW; count=True notes one more.
    A valid link always goes through. In the database (audit 2026-10-13, TN-2)."""
    return _shared_sender_limit("invite_try", "TOKEN_TRY_LIMIT", "TOKEN_TRY_WINDOW", count)


def allow_interviewer_try(count=True):
    """Whether this sender may present another interviewer link that leads nowhere (app/interviewer.py): False once
    it presented TOKEN_TRY_LIMIT such within TOKEN_TRY_WINDOW; count=True notes one more. A valid link always goes
    through. In the database."""
    return _shared_sender_limit("interviewer_try", "TOKEN_TRY_LIMIT", "TOKEN_TRY_WINDOW", count)


def allow_screen_try(count=True):
    """Whether this sender may present another link of a participant screen that leads nowhere
    (app/interview_screen.py): as the interviewer links, TOKEN_TRY_LIMIT within TOKEN_TRY_WINDOW. In the database."""
    return _shared_sender_limit("screen_try", "TOKEN_TRY_LIMIT", "TOKEN_TRY_WINDOW", count)


def allow_screen_answer():
    """Count an answer sent from a participant screen (app/interview_screen.py) for this sender; False once it sent
    LIVE_VOTE_LIMIT within LIVE_WINDOW, like the votes of a live session. In the database (rate_count)."""
    return _counted_sender_limit("screen_answer", current_app.config["LIVE_VOTE_LIMIT"], current_app.config["LIVE_WINDOW"])


_diary_persons = {}  # person id (Enrolment.pid) -> times of its recent entries (/api/diary/v1/entry and /offline)
_diary_syncs = {}  # person id -> times of its recent other requests (/study, /offline/<module>, /leave, /erase)


def allow_diary_person(pid):
    """Count a request of a diary person for an entry (app/diary_api.py: an entry address or an entry sent later);
    False once the person made DIARY_PERSON_LIMIT within DIARY_PERSON_WINDOW. Per person, not per sender: phones
    behind one address do not brake each other, and a script with one key cannot keep a server process busy
    (audit 2026-10-11, H2). In memory only, like the starts (against load)."""
    limit = current_app.config.get("DIARY_PERSON_LIMIT", 60)
    window = current_app.config.get("DIARY_PERSON_WINDOW", 600)
    if not limit:
        return True
    return _take(_diary_persons, pid, limit, window, kind="diary_person")


def allow_diary_sync(pid):
    """Count one of the other requests of a diary person (/study, /offline/<module>, /leave, /erase); False once the
    person made DIARY_SYNC_LIMIT within DIARY_PERSON_WINDOW. The app asks /study when it opens and now and then, and
    each page of /offline/<module> at most every 15 minutes; a script with the person's key in a loop drew the whole
    participant page again and again (audit 2026-10-13, AN-2, TN-11). In memory only (against load)."""
    limit = current_app.config.get("DIARY_SYNC_LIMIT", 120)
    window = current_app.config.get("DIARY_PERSON_WINDOW", 600)
    if not limit:
        return True
    return _take(_diary_syncs, pid, limit, window, kind="diary_sync")


LIVE_KINDS = {"join": "LIVE_JOIN_LIMIT", "qa": "LIVE_QA_LIMIT", "vote": "LIVE_VOTE_LIMIT", "react": "LIVE_REACT_LIMIT"}


def allow_live(kind):
    """Count a request of `kind` on the phones of a live vote for this sender ("join": a new participation, "qa":
    a question from the audience, "vote": a vote on one, "react": a request with reactions); False once it has the
    configured number within LIVE_WINDOW. The phone itself is known by its cookie only (app/live/); this keeps a
    script without cookies from being a new phone every time. In the database, like the starts (BIZ-01)."""
    limit, window = current_app.config[LIVE_KINDS[kind]], current_app.config["LIVE_WINDOW"]
    return _counted_sender_limit("live_" + kind, limit, window)


# --- wrong passwords per sender and account (app/passwords.py), in the database (audit 2026-10-13, AU-9) ----------


def sender_known():
    """True if the sender of this request is known (_network), so wrong passwords count per sender."""
    return _network() is not None


def _failure_key(account_id):
    sender = _network()
    return None if sender is None else _shared_key("password", sender, account_id)


def login_failed(account_id, limit, window):
    """Note a wrong password of this sender for this account. Returns False if the sender is not known (then
    the caller keeps its count for the account alone)."""
    from app.models import RateHit

    key = _failure_key(account_id)
    if key is None:
        return False
    try:
        with db.engine.begin() as con:
            con.execute(RateHit.__table__.insert().values(kind="password", key=key, at=time.time()))
    except Exception as e:  # noqa: BLE001 - the count per account (passwords._attempt) still holds
        current_app.logger.warning("Wrong password not counted per sender: %s", type(e).__name__)
        _take(_fallback, ("password", key), 10 ** 9, window)
    return True


def login_paused(account_id, limit=10, window=900):
    """True while this sender has `limit` wrong passwords for this account within `window` seconds."""
    key = _failure_key(account_id)
    if key is None:
        return False
    try:
        with db.engine.begin() as con:
            return _hits(con, "password", key, time.time() - window) >= limit
    except Exception:  # noqa: BLE001 - see login_failed
        return not _take(_fallback, ("password", key), limit, window, count=False)


def login_succeeded(account_id):
    from app.models import RateHit

    key = _failure_key(account_id)
    if key is None:
        return
    _fallback.pop(("password", key), None)
    try:
        with db.engine.begin() as con:
            t = RateHit.__table__
            con.execute(t.delete().where(t.c.kind == "password", t.c.key == key))
    except Exception as e:  # noqa: BLE001 - the rows go with the sweep
        current_app.logger.warning("Wrong passwords not cleared: %s", type(e).__name__)


def allow_survey_sender(survey):
    """Count a new participation in this survey for this sender; False once it has SURVEY_SENDER_LIMIT starts in
    it within SURVEY_SENDER_WINDOW seconds. Narrower than START_LIMIT (all surveys together): one sender cannot
    fill one survey, while the cap of the survey as a whole only reports (survey_start_flood; S5 of the audit
    2026-10-06). In the database, for all processes (rate_count; audit 2026-10-18, BIZ-01: in memory one sender got
    up to 24 times the limit)."""
    limit, window = current_app.config["SURVEY_SENDER_LIMIT"], current_app.config["SURVEY_SENDER_WINDOW"]
    return _counted_sender_limit("survey_sender", limit, window, survey.id)


def survey_start_flood(survey):
    """True once the survey has SURVEY_START_LIMIT new participations within SURVEY_START_WINDOW seconds, from all
    senders together, counted by the database (so for all processes). Nobody is turned away for it any more: a
    flood from many senders would lock real participants out (S5 of the audit 2026-10-06); the researchers are
    told instead (note_cap)."""
    from app.models import Response, utcnow

    limit, window = current_app.config["SURVEY_START_LIMIT"], current_app.config["SURVEY_START_WINDOW"]
    if not limit:
        return False
    # when the limit-th newest participation started: a walk down ix_response_start_flood alone, without reading
    # the rows (5 KB each), whatever the survey's size (N-7 of the audit 2026-10-09)
    nth = db.session.scalar(db.select(Response.started_at).filter(Response.survey_id == survey.id, Response.source == "real")
                            .order_by(Response.id.desc()).offset(limit - 1).limit(1))
    return nth is not None and nth >= utcnow() - timedelta(seconds=window)


# "device": a device id new to the run (a row in browser_receipt; audit 2026-10-15, NB-7), twice as many as joins
# (a phone that lost its cookie comes back with a new id)
LIVE_RUN_KINDS = {"join": "LIVE_RUN_SENDER_JOINS", "qa": "LIVE_RUN_SENDER_QUESTIONS", "device": "LIVE_RUN_SENDER_JOINS"}


def allow_live_run(kind, survey, run_id):
    """Count a new participation ("join") or a question from the audience ("qa") in this run of a live vote for
    this sender; False once it has the configured number within LIVE_WINDOW. The caps of the run as a whole
    (LIVE_RUN_PARTICIPATIONS, LIVE_RUN_QUESTIONS) only report (S5 of the audit 2026-10-06). In the database (BIZ-01)."""
    limit, window = current_app.config[LIVE_RUN_KINDS[kind]], current_app.config["LIVE_WINDOW"]
    limit *= 2 if kind == "device" else 1
    return _counted_sender_limit("live_run_" + kind, limit, window, survey.id, run_id)


# --- reports when a cap of a survey or of a live run is reached (S5 of the audit 2026-10-06) ------------------------
# A cap refuses nobody; it leaves a note in the survey's folder (next to the kept results, removed with the survey)
# and sends one Slack message. The note is a file made exclusively (mode "x") per cap and time slot of its window:
# of all server processes exactly one makes it, so the report comes at most once per survey (or run) and window.
# The tab "Datenerhebung" (a live vote: "Präsentieren") lists the notes of the last NOTE_DAYS days.

NOTE_DAYS = 7
# kind: (config of the cap, config of its window or None for LIVE_WINDOW, what it counts)
CAP_KINDS = {"starts": ("SURVEY_START_LIMIT", "SURVEY_START_WINDOW", "neue Teilnahmen"),
             "live_join": ("LIVE_RUN_PARTICIPATIONS", None, "Teilnahmen"),
             "live_qa": ("LIVE_RUN_QUESTIONS", None, "Fragen aus dem Publikum")}


def _notes_folder(survey):
    import os

    return os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid)  # see maxdiff_hb.folder


def cap_text(kind, limit, window=None, run=None):
    """The sentence of a note: which cap was reached."""
    what = CAP_KINDS[kind][2]
    if window:
        return f"Mehr als {limit} {what} innerhalb von {round(window / 60)} Minuten."
    return f"Durchgang „{run or 'ohne Namen'}“: {limit} {what} erreicht."


def note_cap(survey, kind, run=None):
    """Report that the cap `kind` (CAP_KINDS) of the survey, or of the live run `run` ({"id", "name"}), was reached:
    a note for the tab and a Slack message, at most once per window (a live run: per run and LIVE_WINDOW). Returns
    True if this call made the report. Never raises: a report that cannot be written must not break the page."""
    import json
    import os

    from app.models import utcnow

    limit_key, window_key, _ = CAP_KINDS[kind]
    window = current_app.config[window_key or "LIVE_WINDOW"] or 600
    limit = current_app.config[limit_key]
    folder = _notes_folder(survey)
    name = f"note-{kind}-{run['id'] if run else 0}-{int(time.time() // window)}.json"
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "x", encoding="utf-8") as f:
            json.dump({"kind": kind, "at": utcnow().isoformat(timespec="seconds"), "limit": limit,
                       "window": window if window_key else None, "run": run.get("name") if run else None}, f)
    except FileExistsError:
        return False
    except OSError as e:
        current_app.logger.warning("Cap note of survey %s not written: %s", survey.code, e)
        return False
    _prune_notes(folder)
    try:
        from flask import url_for

        from app import notify

        page = url_for("live.present", code=survey.code) if survey.live is not None \
            else url_for("surveys.field", survey_id=survey.id)
        text = cap_text(kind, limit, window if window_key else None, run.get("name") if run and notify.full() else None)
        notify.slack(f":warning: Befragung {notify.survey_ref(survey)}: {notify._plain(text)} "
                     f"Niemand wird abgewiesen; ein Skript bremst die Grenze je Absender. "
                     f"<{brand.public_base()}{page}|In Askurious öffnen>")
    except Exception as e:  # noqa: BLE001 - the report must not break the participant page
        current_app.logger.warning("Cap of survey %s not reported in Slack: %s", survey.code, type(e).__name__)
    return True


def _prune_notes(folder):
    import os

    cutoff = time.time() - NOTE_DAYS * 86400
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.startswith("note-") and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


def cap_notes(survey):
    """The notes of reached caps of the last NOTE_DAYS days, newest first: [{"at": datetime (UTC), "text"}]."""
    import json
    import os
    from datetime import datetime

    folder, out = _notes_folder(survey), []
    cutoff = time.time() - NOTE_DAYS * 86400
    try:
        names = [n for n in os.listdir(folder) if n.startswith("note-") and n.endswith(".json")]
    except OSError:
        return []
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            out.append({"at": datetime.fromisoformat(data["at"]),
                        "text": cap_text(data["kind"], data["limit"], data.get("window"), data.get("run"))})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda n: n["at"], reverse=True)


def allow_phone_run(survey):
    """False once the survey got PHONE_RUNS_PER_DAY new test runs within 24 hours (the phone link works without
    signing in, and every visit without its cookie starts a run)."""
    from app.models import Response, utcnow

    limit = current_app.config["PHONE_RUNS_PER_DAY"]
    if not limit:
        return True
    recent = db.session.scalar(db.select(db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.source == "test",
        Response.started_at >= utcnow() - timedelta(hours=24)))
    return recent < limit


def allow_phone_shot(survey):
    """False once the survey got COMMENT_SHOTS_PER_DAY images over the phone link within 24 hours."""
    from app.models import PreviewComment, utcnow

    limit = current_app.config["COMMENT_SHOTS_PER_DAY"]
    if not limit:
        return True
    recent = db.session.scalar(db.select(db.func.count(PreviewComment.id)).filter(
        PreviewComment.survey_id == survey.id, PreviewComment.via == "phone", PreviewComment.shot.is_not(None),
        PreviewComment.created_at >= utcnow() - timedelta(hours=24)))
    return recent < limit


_shot_senders = {}  # sender hash -> times of its recent images on comments


def allow_shot_sender():
    """Count an image on a comment for this sender, before it is read; False once it has COMMENT_SHOT_SENDER_LIMIT
    within COMMENT_SHOT_SENDER_WINDOW. The day limit per survey (allow_phone_shot) counts only stored images, so
    requests sent at once would all pass it (audit 2026-10-09b, O1)."""
    limit, window = current_app.config["COMMENT_SHOT_SENDER_LIMIT"], current_app.config["COMMENT_SHOT_SENDER_WINDOW"]
    sender = _sender()
    if not limit or sender is None:
        return True
    return _take(_shot_senders, sender, limit, window, kind="comment_shot")


def allow_phone_comment(survey):
    """False once the survey got PHONE_COMMENTS_PER_DAY comments over the phone link within 24 hours."""
    from app.models import PreviewComment, utcnow

    limit = current_app.config["PHONE_COMMENTS_PER_DAY"]
    if not limit:
        return True
    recent = db.session.scalar(db.select(db.func.count(PreviewComment.id)).filter(
        PreviewComment.survey_id == survey.id, PreviewComment.via == "phone",
        PreviewComment.created_at >= utcnow() - timedelta(hours=24)))
    return recent < limit


def allow_complaint(survey):
    """Count a complaint about this survey for this sender; False once it has COMPLAINTS_PER_SENDER complaints
    about it within 24 hours. In the database, for all processes (rate_count; audit 2026-10-18, BIZ-03). The cap per
    survey (COMPLAINTS_PER_DAY, complaints_today) refuses nothing: a flood from many senders must not lock real
    complaints out, it only stops the Slack messages and the mails of receipt (app/participate.py complaint)."""
    return _counted_sender_limit("complaint", current_app.config["COMPLAINTS_PER_SENDER"], DAY, survey.id)


def allow_complaint_mail(address):
    """Count a mail of receipt of a complaint to this address; False once it got COMPLAINT_MAILS_PER_ADDRESS within
    24 hours, whoever sent the complaints and about whichever survey (audit 2026-10-18, BIZ-03: from many senders the
    form sent thousands of mails to one stranger). Counted in the database on a keyed digest of the address (with
    the key of the day), never the address. The complaint itself is kept either way."""
    limit = current_app.config.get("COMPLAINT_MAILS_PER_ADDRESS", 1)
    if not address:
        return False
    if not limit:
        return True
    return _take_counted("complaint_mail", _shared_key("mail_to", address.strip().lower()), limit, DAY)


def complaints_today(survey):
    """How many complaints the survey got within 24 hours (counted by the database, so for all server
    processes)."""
    from app.models import Feedback, utcnow

    return db.session.scalar(db.select(db.func.count(Feedback.id)).filter(
        Feedback.survey_id == survey.id, Feedback.severity == Feedback.COMPLAINT,
        Feedback.created_at >= utcnow() - timedelta(hours=24)))


# the platform's daily budget per kind, all people together, over the installation's key (KR-2)
AI_PLATFORM = {"text": "AI_PLATFORM_TEXTS_PER_DAY", "image": "AI_PLATFORM_IMAGES_PER_DAY"}


def _platform_budget(kind, day, units):
    """A message once the calls of `kind` over the installation's key reached the platform's daily budget today, else
    None. Counted from the daily counts of all people (ai_usage); a call a moment later in another process may slip
    through, the budget is a brake, not an invoice. The first refusal of the day tells Slack (once, in all processes:
    a file made exclusively, as note_cap does)."""
    import os

    from app.lang import de_en
    from app.models import AiUsage

    budget = int(current_app.config.get(AI_PLATFORM[kind]) or 0)
    if not budget:
        return None
    used = db.session.scalar(db.select(db.func.coalesce(db.func.sum(AiUsage.count), 0))
                             .filter(AiUsage.day == day, AiUsage.kind == kind)) or 0
    if used + max(1, int(units or 1)) <= budget:
        return None
    try:
        folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "ai-budget")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{day}-{kind}"), "x", encoding="utf-8") as f:
            f.write(str(budget))
        from app import notify

        notify.slack(f":warning: Das Tagesbudget der Plattform für KI ({kind}, {budget}) ist erreicht; weitere Aufrufe "
                     f"über den Schlüssel der Installation warten bis morgen.")
    except FileExistsError:
        pass
    except Exception as e:  # noqa: BLE001 - the note must not break the page
        current_app.logger.warning("AI budget not reported: %s", type(e).__name__)
    return de_en("Das Tagesbudget der Plattform für KI-Aufrufe ist für heute aufgebraucht. Morgen geht es weiter; mit "
                 "einem eigenen KI-Zugang (KI-Nutzung) auch heute.",
                 "The platform's daily budget for AI calls is used up for today. It goes on tomorrow; with your own AI "
                 "access (AI usage) also today.")


AI_KINDS = {"text": ("AI_TEXTS_PER_DAY", "KI-Texten"), "image": ("AI_IMAGES_PER_DAY", "KI-Bildern"),
            "own": ("OWN_AI_CALLS_PER_DAY", "KI-Aufrufen über den eigenen Zugang")}


def refund_ai():
    """Give back the call counted by charge_ai in this request: a failed AI call does not count on the daily limit
    (Ralf, 2026-10-10). In its own transaction, so that nothing else of the request is committed with it."""
    from flask import g, has_request_context

    from app.models import AiUsage

    rid = g.pop("ai_charged", None) if has_request_context() else None
    if rid is None:
        return
    units = g.pop("ai_units", 1)
    try:
        with db.engine.begin() as con:
            con.execute(AiUsage.__table__.update().where(AiUsage.id == rid, AiUsage.count >= units)
                        .values(count=AiUsage.count - units))
    except Exception as e:  # the count stays: the person loses one call, nothing else
        current_app.logger.warning("AI call not given back: %s", e)


def charge_ai(kind, own=False, units=1):
    """Count one AI call of the signed-in researcher for today. Returns a message if the daily limit is
    reached (the call must not be made), else None. units: how many calls this one counts for (the AI reading of
    handwriting, app/paper.py; never more than the limit itself); it goes through only if all of them still fit.

    Security audit 2026-10-13, KR-2: without a signed-in person no call at all (the participants' side has no AI
    function; a new one would otherwise be without any limit); site admins have AI_ADMIN_FACTOR times the limit
    instead of none; and the calls over the installation's key (not an own access) of all people together stop at the
    platform's daily budget (AI_PLATFORM_TEXTS_PER_DAY, AI_PLATFORM_IMAGES_PER_DAY; Slack hears of it once a day).

    For every signed-in researcher, limited or not, the request's work so far is committed before the call: the
    call then holds no write lock (SQLite), and what the caller reads after it comes fresh from the database.
    Without the commit for admins and with no limit set, a change made elsewhere during the call was overwritten
    (code review 2026-10-07, I7)."""
    from flask import g, has_request_context
    from flask_login import current_user

    from app.lang import de_en
    from app.models import AiUsage, utcnow
    from app.timeutil import to_local

    g.pop("ai_charged", None)  # what refund_ai may give back: only this call, once it is counted below
    g.pop("ai_units", None)
    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return de_en("KI-Funktionen gibt es nur für angemeldete Personen.", "AI functions are for signed-in people only.")
    if own:  # an own AI access (app/ai_access.py): only the technical limit, counted on its own (E7)
        kind = "own"
    key, label = AI_KINDS[kind]
    limit = current_app.config[key]
    if limit and current_user.is_admin:
        limit *= max(1, int(current_app.config.get("AI_ADMIN_FACTOR", 5) or 1))
    if not limit:
        db.session.commit()  # the same as below, without counting
        return None
    day = to_local(utcnow()).strftime("%Y-%m-%d")
    if kind != "own":
        refused = _platform_budget(kind, day, units)
        if refused:
            db.session.commit()
            return refused
    row = db.session.scalar(db.select(AiUsage).filter_by(user_id=current_user.id, day=day, kind=kind))
    if row is None:
        from sqlalchemy.exc import IntegrityError

        try:
            # in a savepoint: if the row cannot be written, only this step is taken back, not what the request
            # has done so far
            with db.session.begin_nested():
                row = AiUsage(user_id=current_user.id, day=day, kind=kind, count=0)
                db.session.add(row)
        except IntegrityError:
            # the first call of the day in two requests at once: the other one wrote the row in between
            row = db.session.scalar(db.select(AiUsage).filter_by(user_id=current_user.id, day=day, kind=kind))
            if row is None:
                raise
    # counted by the database in one step: two requests at the same moment both read "99" otherwise, and both went through
    units = max(1, min(int(units or 1), limit))
    taken = db.session.execute(db.update(AiUsage).where(AiUsage.id == row.id, AiUsage.count + units <= limit)
                               .values(count=AiUsage.count + units)).rowcount
    db.session.commit()  # counted at once (two requests at the same moment); a failed call gives it back (refund_ai)
    if taken:
        g.ai_charged, g.ai_units = row.id, units
    if not taken and kind == "own":
        from app.lang import de_en

        return de_en("Das technische Tageslimit von {n} KI-Aufrufen über den eigenen Zugang ist erreicht. Morgen geht "
                     "es weiter.", "The technical daily limit of {n} AI calls over your own access is reached. It "
                     "goes on tomorrow.", n=limit)
    if not taken:
        return f"Das Tageslimit von {limit} {label} ist erreicht. Morgen geht es weiter."
    return None


def allow_estimate(user_id, count=True):
    """Count an estimation started by this account; False once it started ESTIMATE_LIMIT within ESTIMATE_WINDOW. In
    the database, for all processes (rate_count). The queue of the Rechenknecht and the estimation slots of the server
    are shared by everyone: one account with many copies of a survey put its jobs before all others (audit 2026-10-22,
    D-04)."""
    limit = int(current_app.config.get("ESTIMATE_LIMIT") or 0)
    if not limit or not user_id:
        return True
    return _take_counted("estimate", _shared_key("estimate", user_id), limit,
                         int(current_app.config.get("ESTIMATE_WINDOW") or 3600), count)
