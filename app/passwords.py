"""Sign-in with e-mail address and password, next to ORCID (app/auth.py has the pages).

An account with a password starts with a site admin: they create it, or the person adds a password to their ORCID
account on the page "Konto". A forgotten password is replaced the same way a new account gets its first one: by a
one-time link. Without e-mail (app/mail.py off) the admin passes it on; with e-mail the admin can send it by mail,
and "Passwort vergessen?" mails a link valid for RESET_HOURS by itself (mailversand-konzept.md, M4). The address
is the sign-in name. New self-service addresses are activated only after mailbox confirmation;
existing and administratively provided access remains compatible (email_confirmation.py).

Passwords are stored as scrypt hashes (werkzeug). Ten wrong passwords from one sender pause the sign-in to that
account for this sender for 15 minutes (counted in memory, app/limits.py); fifty wrong ones from all senders
together pause the account itself (counted in the database, so it holds across server processes). HARD_LIMIT wrong
passwords or codes in a row lock the sign-in with password until a site admin releases it (AU3). Answers never tell
whether an address has an account.
"""
import hashlib
import secrets
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from app.emails import normal_email  # noqa: F401 (the callers ask passwords.normal_email)
from app.extensions import db
from app.lang import de_en
from app.models import AuthToken, User, UserLogin, utcnow

MIN_LENGTH = 12
MAX_LENGTH = 200
LONG_ENOUGH = 20  # from this length a password with only few letters is accepted (problem())
MAX_FAILURES = 10        # wrong passwords from one sender (or, where the sender is unknown, in all) until the pause
ACCOUNT_FAILURES = 50    # wrong passwords from all senders together until the account itself pauses
PAUSE = timedelta(minutes=15)
# NIST SP 800-63B-4 (rate limiting): at most 100 wrong tries in a row per account. The pause above does not reset this
# count, only a right password (or, for accounts with a code, a right code) does; at HARD_LIMIT the sign-in with
# password and code is locked until a site admin releases it, a new password comes over a one-time link, or HARD_LOCK
# has passed; one more wrong try after that locks it again. ORCID stays open (audit 2026-10-11, AU3/N11 and AU5)
HARD_LIMIT = 100
HARD_LOCK = timedelta(days=1)
# Whoever only knows an address could keep its owner locked for good: 100 wrong tries, then one a day. A browser in
# which the account signed in with its current password before (a signed cookie, KNOWN_DAYS) is not held by that lock
# (NIST SP 800-63B-4, 3.2.2: a device the subscriber authenticated from before); everyone else is. Its wrong tries
# still count, the pause per sender and of the account still holds, so the lock keeps guessing out as before
# (Abgleich 2026-10-15, AU-9 rest).
KNOWN_DAYS = 90
KNOWN_SALT = "known-device"
LINK_HOURS = 72
RESET_HOURS = 1     # the link "Passwort vergessen?" mails by itself
RESET_GAP = timedelta(minutes=10)  # one such mail per account within this time

# what people type when twelve characters are asked for (besides the list of common_passwords.txt)
COMMON = {
    "123456789012", "1234567890123", "12345678901234", "123456123456", "0123456789012", "012345678901",
    "password1234", "password12345", "passwordpassword", "passwort1234", "passwort12345", "passwortpasswort",
    "qwertyuiop12", "qwertyuiop123", "qwertzuiop12", "qwertzuiop123", "qwertyqwerty", "qwertzqwertz",
    "1q2w3e4r5t6y", "1q2w3e4r5t6z", "1qaz2wsx3edc", "qwerty123456", "qwertz123456", "abc123456789",
    "abcdefghijkl", "abcdefghijklm", "iloveyou1234", "ichliebedich", "letmein12345", "welcome12345",
    "willkommen123", "admin1234567", "administrator", "changeme1234", "geheim123456", "hallo1234567",
    "queryous1234", "queryousqueryous", "askurious1234", "askuriousaskurious",
    "trustno1trustno1", "monkey123456", "dragon123456", "sommer202620", "1qay2wsx3edc", "1qayxsw23edc",
}
# words of this site and its place, and of the seasons and months, which people put a year or a "!" behind (AU4);
# with the person's name and address (problem())
CONTEXT = (
    "askurious", "queryous", "polltergeist", "rwth", "aachen", "philipsen", "passwort", "password", "kennwort",
    "geheim", "admin", "login", "anmeldung", "konto", "account", "umfrage", "befragung", "survey", "studie",
    "sommer", "summer", "winter", "fruehling", "frühling", "spring", "herbst", "autumn", "fall", "januar", "january",
    "februar", "february", "maerz", "märz", "march", "april", "mai", "may", "juni", "june", "juli", "july", "august",
    "september", "oktober", "october", "november", "dezember", "december",
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s",
                       "!": "i", "+": "t"})
_LIST = None


def normalize(password):
    """The stem of a password as the block list compares it: lower case, and the digits and other characters that
    are no letters at the end cut off ("Sommer2026!!" -> "sommer")."""
    import re

    return re.sub(r"[\W\d_]+\Z", "", (password or "").casefold().strip())


def _common():
    """The stems of the 10,000 most frequent passwords (app/data/common_passwords.txt, tools/passwort_sperrliste.py)."""
    global _LIST
    if _LIST is None:
        import os

        path = os.path.join(os.path.dirname(__file__), "data", "common_passwords.txt")
        try:
            with open(path, encoding="utf-8") as f:
                _LIST = frozenset(line.strip() for line in f if line.strip() and not line.startswith("#"))
        except OSError:  # pragma: no cover - the file comes with the code
            _LIST = frozenset()
    return _LIST


def stems(password):
    """The forms of a password the block list looks at: the stem (normalize), the stem with digits and characters at
    the start cut off as well, and both with digits read as letters ("P@ssw0rd" -> "password")."""
    import re

    out = set()
    for stem in (normalize(password), re.sub(r"\A[\W\d_]+", "", normalize(password))):
        out.add(stem)
        out.add(normalize(stem.translate(_LEET)))
        out.add(normalize(stem.translate(_LEET).replace("i", "l")))
    return out - {""}


def _only_context(password, words):
    """True when the password is little more than words of the context (CONTEXT, the person's name and address) with
    digits and characters around them: "RWTHAachen2026", "Askurious2026!", "Anna.Muster1990"."""
    import re

    if not re.search(r"[^\W\d_]", password or ""):
        return False  # no letters at all: the stem rule of problem() decides
    for text in {(password or "").casefold(), (password or "").casefold().translate(_LEET)}:
        letters = re.sub(r"[\W\d_]+", "", text)
        found = False
        for word in sorted(words, key=len, reverse=True):
            if word and word in letters:
                letters, found = letters.replace(word, ""), True
        if found and len(letters) < 4:
            return True
    return False


def _person_words(name, email):
    """The parts of the person's name and e-mail address (without the top-level domain), three letters and more."""
    import re

    email = (email or "").casefold()
    local, _, domain = email.partition("@")
    parts = re.split(r"[\W\d_]+", (name or "").casefold()) + re.split(r"[\W\d_]+", local)
    parts += re.split(r"[\W\d_]+", domain.rsplit(".", 1)[0]) if "." in domain else []
    return {p for p in parts if len(p) >= 3}


def problem(password, name="", email=""):
    """Why this password is not accepted (a message), or None. Length counts, not character classes; besides it must
    not be one of the 10,000 most frequent passwords or a word of the context with digits around it (NIST SP
    800-63B-4: a block list of common, expected or compromised values; audit 2026-10-11, AU4)."""
    password = password or ""
    if len(password) < MIN_LENGTH:
        return de_en("Das Passwort braucht mindestens {n} Zeichen. Ein Satz aus mehreren Wörtern ist gut und leicht zu merken.",
                     "The password needs at least {n} characters. A sentence of several words is good and easy to remember.",
                     n=MIN_LENGTH)
    if len(password) > MAX_LENGTH:
        return de_en("Das Passwort darf höchstens {n} Zeichen haben.", "The password may have at most {n} characters.", n=MAX_LENGTH)
    low = password.casefold()
    own = _person_words(name, email)
    whole = {(name or "").casefold().replace(" ", ""), (email or "").casefold(), (email or "").casefold().split("@")[0]}
    if low.replace(" ", "") in whole - {""} or (own and _only_context(password, own)):
        return de_en("Das Passwort darf nicht der eigene Name oder die eigene E-Mail-Adresse sein, auch nicht mit Ziffern "
                     "oder Zeichen davor oder dahinter.",
                     "The password must not be your own name or your own email address, not even with digits or "
                     "characters before or after it.")
    found = stems(password)
    # little more than digits and characters around a few letters ("ab1234567890", "12345678901!"); a long random
    # string of digits and characters stays allowed
    short_stem = len(password) < LONG_ENOUGH and min((len(x) for x in found), default=0) < 4
    if (low in COMMON or len(set(low)) <= 2 or low.isdigit() or short_stem
            or found & _common() or found & COMMON or _only_context(password, set(CONTEXT) | own)):
        return de_en("Dieses Passwort ist zu leicht zu erraten: Es steht so oder fast so auf den Listen, mit denen "
                     "Angreifer zuerst raten. Bitte ein anderes wählen, am besten einen Satz aus mehreren Wörtern.",
                     "This password is too easy to guess: it is, or nearly is, on the lists attackers try first. Please "
                     "choose another one, ideally a sentence of several words.")
    return None


def login_row(user, create=False):
    row = db.session.get(UserLogin, user.id)
    if row is None and create:
        row = UserLogin(user_id=user.id)
        db.session.add(row)
    return row


def has_password(user):
    row = login_row(user)
    return row is not None and bool(row.password_hash)


def set_password(user, password):
    """Store the new password and end every session of the account (the caller signs this browser in again)."""
    set_password_hash(user, generate_password_hash(password))


def set_password_hash(user, password_hash, first=False):
    """`first`: the account adds its first password (an ORCID account confirming its address); it replaces no
    credential, so nothing is revoked but the one-time links (Abgleich 2026-10-15, T01-N2)."""
    row = login_row(user, create=True)
    first = first and not row.password_hash
    row.password_hash = password_hash
    row.password_changed_at = utcnow()
    if not _code_follows(row):  # wrong codes stay counted: a new password by mail must not clear them (T2)
        row.failed_count, row.locked_until = 0, None
        row.failed_streak, row.hard_locked_at = 0, None  # the guessed password is gone
    row.reset_requested_at = None  # a request "Passwort vergessen?" is done
    row.session_version = (row.session_version or 0) + 1
    from app import sessions

    from app import account_access

    if first:
        # adding a first password keeps the data access tokens, invitations, join links and connections: no earlier
        # password could have left them behind (Abgleich 2026-10-15, T01-N2); one-time links go (AU-12)
        account_access.revoke_links(user.id)
    else:
        gone = account_access.revoke(user.id)
        if _by_themselves(user):
            account_access.tell(gone)
    sessions.end_all(user.id)  # the list on the page "Konto" says so too
    _event("password_set", account=user.id, kind="first" if first else "change")


def remove(user):
    """Take the sign-in with e-mail and password away from an account that signs in with ORCID (page "Konto",
    konto-einrichtung-konzept.md, 2 A): password and address go, so that no "Passwort vergessen?" brings the way back
    in without a fresh sign-in with ORCID; every other session of the account ends. The second factor stays for a
    password added later. The caller checked the current password and commits."""
    row = login_row(user)
    if row is None or not row.password_hash:
        return
    row.password_hash, row.password_changed_at = None, utcnow()
    row.email, row.email_verified_at = None, None
    row.failed_count, row.locked_until, row.failed_streak, row.hard_locked_at = 0, None, 0, None
    row.reset_requested_at = None
    row.session_version = (row.session_version or 0) + 1
    from app import account_access, sessions

    account_access.revoke_links(user.id)  # one-time links to set a password lead nowhere now
    sessions.end_all(user.id)
    _event("password_removed", account=user.id)


def matches(user, password):
    row = login_row(user)
    return row is not None and bool(row.password_hash) and check_password_hash(row.password_hash, password or "")


_DUMMY = generate_password_hash("no account has this password")


def check(email, password):
    """The account for this address and password, or None. The same work is done and nothing is said about
    the reason: unknown address, wrong password and a paused account look alike from outside."""
    import time

    started = time.monotonic()
    email = normal_email(email)
    row = db.session.scalar(db.select(UserLogin).filter_by(email=email)) if email else None
    if row is None or not row.password_hash:
        check_password_hash(_DUMMY, password or "")
        # Take the same write transaction as a failed known login without retaining unknown addresses.
        db.session.execute(db.update(UserLogin).where(db.false()).values(failed_streak=0))
        db.session.commit()
        _event("sign_in_failed", reason="unknown")
        result = None
    else:
        result = db.session.get(User, row.user_id) if _attempt(row, password, known=known_device(row)) else None
    if result is None:
        time.sleep(max(0, .4 - (time.monotonic() - started)))
    return result


def _by_themselves(user):
    """True in a request of the account itself, signed in or through a link sent to it (not an admin acting on it)."""
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context():
        return False
    return not getattr(current_user, "is_authenticated", False) or current_user.id == user.id


def _event(name, **fields):
    """The security log (audit 2026-10-13, DP-7/AU-7): never the address typed, never a password."""
    from app import security_log

    security_log.event(name, **fields)


def confirm(user, password):
    """True if `password` is the account's current one, asked on the page "Konto" (changing the password, deleting
    the account). Wrong ones count as at the sign-in, per sender and account (security audit 2026-10-06, S14):
    whoever sits at an open computer guesses no longer here than on the sign-in page. paused(user) then says
    the caller should end the session."""
    row = login_row(user)
    if row is None or not row.password_hash:
        return False
    # a browser the account signed in from before is not held by the lock after HARD_LIMIT, as at the sign-in
    # (code review 2026-10-15, H3)
    return _attempt(row, password, known=known_device(row))


def paused_here(user):
    """paused(user), but only for wrong passwords from this sender: the session that guessed ends
    (accounts._too_many_wrong), not the owner's when others paused the account. Where the sender is not known, as
    paused(user)."""
    from app import limits

    row = login_row(user)
    if row is None:
        return False
    if limits.sender_known():
        return limits.login_paused(row.user_id, MAX_FAILURES, PAUSE.total_seconds())
    return paused(user)


def paused(user):
    """Too many wrong passwords for this account: from this sender, or (sender unknown) in all."""
    from app import limits

    row = login_row(user)
    return row is not None and ((row.locked_until is not None and row.locked_until > utcnow()) or hard_locked(row)
                                or limits.login_paused(row.user_id, MAX_FAILURES, PAUSE.total_seconds()))


def hard_locked(row, known=False):
    """The sign-in with password and code is locked after HARD_LIMIT wrong tries in a row (AU3), for HARD_LOCK; not
    for a browser the account signed in from before (`known`, known_device)."""
    return (not known and row is not None and row.hard_locked_at is not None
            and row.hard_locked_at + HARD_LOCK > utcnow())


def _known_name():
    from flask import current_app

    return "__Host-known" if current_app.config.get("SESSION_COOKIE_SECURE") else "known_device"


def _known_serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer

    from app import cookies

    # under the researchers' own key when set (CFG-04): the participants' containers cannot make such a cookie
    return URLSafeTimedSerializer(cookies.researcher_key() or current_app.secret_key, salt=KNOWN_SALT)


def _known_stamp(row):
    """What binds the cookie to the current password: a new password (salted anew) makes every older cookie
    worthless. A short digest of the stored hash, never the hash itself."""
    return hashlib.sha256(f"known|{row.password_hash or ''}".encode()).hexdigest()[:16]


def known_device(row):
    """True if this browser holds the cookie of an earlier sign-in of this account with its current password."""
    from flask import has_request_context, request
    from itsdangerous import BadSignature

    if row is None or not has_request_context():
        return False
    raw = request.cookies.get(_known_name())
    if not raw:
        return False
    try:
        data = _known_serializer().loads(raw, max_age=KNOWN_DAYS * 86400)
    except (BadSignature, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("u") == row.user_id and data.get("p") == _known_stamp(row)


def remember_device(user):
    """After a full sign-in with password (and code): this browser counts as known for the account (AU-9 rest)."""
    from flask import after_this_request, current_app

    row = login_row(user)
    if row is None or not row.password_hash:
        return
    value = _known_serializer().dumps({"u": user.id, "p": _known_stamp(row)})

    @after_this_request
    def _set(response):
        response.set_cookie(_known_name(), value, max_age=KNOWN_DAYS * 86400, httponly=True, samesite="Lax",
                            secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")), path="/")
        return response


def note_failure(row):
    """One more wrong password or code in a row for this sign-in row (counted in the database itself, so parallel
    wrong tries all count); at HARD_LIMIT the sign-in locks and Slack hears of it once. The caller commits."""
    from sqlalchemy import update

    db.session.flush()
    db.session.execute(update(UserLogin).where(UserLogin.user_id == row.user_id)
                       .values(failed_streak=db.func.coalesce(UserLogin.failed_streak, 0) + 1)
                       .execution_options(synchronize_session=False))
    db.session.refresh(row, ["failed_streak", "hard_locked_at"])
    if (row.failed_streak or 0) >= HARD_LIMIT and not hard_locked(row):
        row.hard_locked_at = utcnow()
        _event("sign_in_locked", account=row.user_id)
        user = db.session.get(User, row.user_id)
        if user is not None:
            from app import twofactor

            twofactor.tell("hard_locked", user)


def release(row):
    """A site admin lifts the lock (Administration > Personen > account): the counts start again."""
    row.failed_streak, row.hard_locked_at, row.failed_count, row.locked_until = 0, None, 0, None


def _attempt(row, password, known=False):
    """Check the password of this sign-in row, counting a wrong one; a paused account takes none. `known`: a browser
    the account signed in from before, which the lock after HARD_LIMIT does not hold (known_device)."""
    from app import limits

    now = utcnow()
    if (row.locked_until and row.locked_until > now) or hard_locked(row, known) or limits.login_paused(row.user_id):
        check_password_hash(_DUMMY, password or "")
        _event("sign_in_failed", account=row.user_id, reason="paused")
        return False
    if not check_password_hash(row.password_hash, password or ""):
        # The pause is for the sender who guessed: ten wrong passwords from anywhere used to pause the account
        # for everyone, so whoever knew an address could keep its owner out for good. The account as a whole
        # pauses only after many failures from several senders. Where the sender is not known (no reverse
        # proxy configured), the old rule holds.
        known = limits.login_failed(row.user_id, MAX_FAILURES, PAUSE.total_seconds())
        row.failed_count = (row.failed_count or 0) + 1
        _event("sign_in_failed", account=row.user_id, reason="password")
        if row.failed_count >= (ACCOUNT_FAILURES if known else MAX_FAILURES):
            row.failed_count, row.locked_until = 0, now + PAUSE
            _event("sign_in_paused", account=row.user_id, seconds=int(PAUSE.total_seconds()))
        note_failure(row)  # not reset by the pause (AU3)
        db.session.commit()
        return False
    if not _code_follows(row):
        row.failed_count = 0
        row.failed_streak = 0  # only a right password ends the row of wrong ones (with a code: the right code)
    row.locked_until = None
    limits.login_succeeded(row.user_id)
    db.session.commit()
    return True


def _code_follows(row):
    """The account gives a code after the password (site admins with a factor, app/twofactor.py): the counter of
    wrong tries is reset by the right code, not by the right password, so whoever knows the password cannot clear
    the count of wrong codes with it (audit 2026-10-09b, T2)."""
    from app import twofactor

    user = db.session.get(User, row.user_id)
    return user is not None and twofactor.required_at_signin(user) and bool(row.totp_box)


# --- one-time links ---------------------------------------------------------------------------------


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_link_token(user, by, hours=LINK_HOURS):
    """A fresh token for the one-time link to set the password; older unused links of the account end. Only
    its hash is stored, so the link can be shown exactly once. `by` None: the person asked for it by mail."""
    db.session.execute(db.delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == "setup"))
    token = secrets.token_urlsafe(32)
    _event("one_time_link_created", account=user.id, by=by.id if by else None)
    db.session.add(AuthToken(user_id=user.id, purpose="setup", token_hash=_hash(token),
                             expires_at=utcnow() + timedelta(hours=hours), created_by_id=by.id if by else None))
    return token


def recently_mailed(user):
    """Did "Passwort vergessen?" mail this account a link within RESET_GAP? Then no second one (someone typing
    another person's address again and again fills no inbox)."""
    return db.session.scalar(db.select(AuthToken.id).filter(
        AuthToken.user_id == user.id, AuthToken.purpose == "setup", AuthToken.created_by_id.is_(None),
        AuthToken.created_at >= utcnow() - RESET_GAP).limit(1)) is not None


def mail_link(user, link, reset, queued=False):
    """Mail the one-time link to the account's address, in the person's language. reset: after "Passwort
    vergessen?" (valid RESET_HOURS); else an admin sent it (a new account, or a new link; LINK_HOURS)."""
    from app import lang, mail

    row = login_row(user)
    if row is None or not row.email:
        return None
    with mail.in_language(lang.of(user)):
        hours = RESET_HOURS if reset else LINK_HOURS
        if reset:
            subject = de_en("Askurious: neues Passwort setzen", "Askurious: set a new password")
            text = de_en("Guten Tag {name},\n\nfür Ihr Konto bei Askurious wurde ein neues Passwort angefragt. Mit diesem Link "
                         "setzen Sie es (gültig {hours} Stunde, nur einmal):\n\n{link}\n\nHaben Sie nichts angefragt, "
                         "ignorieren Sie diese E-Mail; Ihr bisheriges Passwort gilt weiter.",
                         "Hello {name},\n\na new password was requested for your Askurious account. Set it with this link "
                         "(valid for {hours} hour, once only):\n\n{link}\n\nIf you did not ask for it, ignore this "
                         "email; your current password stays valid.", name=user.name, hours=hours, link=link)
        else:
            subject = de_en("Askurious: Passwort für Ihr Konto setzen", "Askurious: set the password for your account")
            text = de_en("Guten Tag {name},\n\nfür Sie gibt es ein Konto bei Askurious. Mit diesem Link setzen Sie Ihr "
                         "Passwort (gültig {hours} Stunden, nur einmal):\n\n{link}\n\nDanach melden Sie sich mit "
                         "{email} und Ihrem Passwort an.",
                         "Hello {name},\n\nthere is an account for you at Askurious. Set your password with this link "
                         "(valid for {hours} hours, once only):\n\n{link}\n\nAfterwards you sign in with {email} and "
                         "your password.", name=user.name, hours=hours, link=link, email=row.email)
        if queued:
            from app import mailqueue

            mailqueue.enqueue(row.email, subject, text + mail.signature(), kind="reset", sender_id=user.id)
            db.session.commit()
            mailqueue.kick()
            return True
        return mail.send(row.email, subject, text + mail.signature())


def link_row(token):
    """The valid, unused entry for this token, or None."""
    row = db.session.scalar(db.select(AuthToken).filter_by(token_hash=_hash(token or ""), purpose="setup"))
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        return None
    return row


def open_link(user):
    """Is a one-time link of this account still waiting to be used?"""
    return db.session.scalar(db.select(AuthToken.id).filter(
        AuthToken.user_id == user.id, AuthToken.purpose == "setup", AuthToken.used_at.is_(None),
        AuthToken.expires_at >= utcnow()).limit(1)) is not None
