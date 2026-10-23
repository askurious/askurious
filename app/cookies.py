"""The session cookie under its name "__Host-session" (config SESSION_COOKIE_NAME, security audit 2026-10-04, N4).

A cookie under the old name "session" is never read: it is exactly what a page on another sub-domain could set. The
transition that took such a cookie over once ended on 2026-10-12 and is gone from the code, with its setting, so that
no line in the .env can extend it unnoticed (audit 2026-10-11, AU8). A stale cookie under the old name is removed.

On the participants' own address (app/hosts.py) the cookie is signed with a salt of its own (code review 2026-10-08,
H1): a session of that address is worth nothing on the main address and the other way round, so nobody can carry
the session of a participation from there to the main address and have it handed over to someone else's browser.
Until PARTICIPANT_OLD_COOKIE_UNTIL the participants' address still reads a cookie signed the way before and signs it
anew, so that participants under way there when the change went live go on; the participations in such a cookie are
bound to that address (hosts.claim_old_cookie), so the same cookie on the main address hands none of them over.

With RESEARCHER_SESSION_KEY (config.py) the sessions of the main address are signed with that key instead of
SECRET_KEY. The participants' containers do not get it (deploy/docker/compose.yaml), so a break-in there cannot sign
a researcher's or admin's session with the SECRET_KEY it finds (audit 2026-10-18, CFG-04).
"""
from datetime import date

from flask.sessions import SecureCookieSessionInterface
from itsdangerous import URLSafeTimedSerializer

from app.timeutil import local_today

LEGACY = "session"
PARTICIPANT_SALT = "cookie-session-participant"


def researcher_key(app=None):
    """RESEARCHER_SESSION_KEY when it is set and this request is not one of the participants' side, else None."""
    from flask import current_app, has_app_context, has_request_context

    if app is None:
        if not has_app_context():
            return None
        app = current_app
    key = app.config.get("RESEARCHER_SESSION_KEY")
    if not key:
        return None
    if has_request_context():
        from app import hosts

        if hosts.on_participant_side():
            return None
    return key


def install_remember_key():
    """Flask-Login signs its "stay signed in" cookie with SECRET_KEY (flask_login.utils._secret_key, 0.6.3): with
    RESEARCHER_SESSION_KEY it takes that key instead, else a break-in on the participants' side could forge that
    cookie as well (CFG-04). tests/platform/test_session_keys.py fails should Flask-Login change this place."""
    from flask_login import utils

    original = getattr(utils._secret_key, "askurious_original", utils._secret_key)

    def _secret_key(key=None):
        return original(key if key is not None else researcher_key())

    _secret_key.askurious_original = original
    utils._secret_key = _secret_key


def _until(app, name):
    try:
        return local_today() <= date.fromisoformat(app.config.get(name) or "1970-01-01")
    except ValueError:
        return False


class SessionInterface(SecureCookieSessionInterface):
    def get_signing_serializer(self, app, salt=None):
        """The serializer of Flask, with the salt of the side this request belongs to."""
        if not app.secret_key:
            return None
        from app import hosts

        participant = hosts.on_participant_side()
        if salt is None:
            salt = PARTICIPANT_SALT if participant else self.salt
        own = None if participant else researcher_key(app)
        if own:  # the researchers' sessions under a key the participants' containers do not have (CFG-04)
            keys = [*(app.config.get("RESEARCHER_SESSION_KEY_FALLBACKS") or []), own]
        else:
            keys = [*(app.config["SECRET_KEY_FALLBACKS"] or []), app.secret_key]  # the current key at the end
        return URLSafeTimedSerializer(keys, salt=salt, serializer=self.serializer,
                                      signer_kwargs={"key_derivation": self.key_derivation,
                                                     "digest_method": self.digest_method})

    def _read(self, app, value, salt=None):
        serializer = self.get_signing_serializer(app, salt)
        if serializer is None:
            return None
        try:
            return serializer.loads(value, max_age=int(app.permanent_session_lifetime.total_seconds()))
        except Exception:
            return None

    def open_session(self, app, request):
        if not app.secret_key:
            return None
        name = self.get_cookie_name(app)
        value = request.cookies.get(name)
        if not value:
            return self.session_class()
        data = self._read(app, value)
        renewed = False
        if data is None:
            from app import hosts

            if hosts.on_participant_side() and _until(app, "PARTICIPANT_OLD_COOKIE_UNTIL"):
                data = self._read(app, value, self.salt)  # signed the way before: signed anew with this answer
                renewed = data is not None
                if renewed:  # its participations stay here and are never handed over as well (audit 2026-10-09, A1)
                    data = hosts.claim_old_cookie(data)
        if data is None:
            return self.session_class()
        session = self.session_class(data)
        if renewed:
            session.modified = True  # written under the new name (and salt) with this answer
        return session

    def save_session(self, app, session, response):
        super().save_session(app, session, response)
        from flask import request

        if self.get_cookie_name(app) != LEGACY and LEGACY in request.cookies:  # a stale one from before N4
            response.delete_cookie(LEGACY, path=self.get_cookie_path(app), domain=self.get_cookie_domain(app))
