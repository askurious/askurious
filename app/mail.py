"""E-mail from the site (concept mailversand-konzept.md, Ralf 2026-10-09: M1 to M8 as recommended).

Switched on by the SMTP access in the server's .env (MAIL_HOST and MAIL_FROM; MAIL_PORT, MAIL_USER, MAIL_PASSWORD,
MAIL_SECURITY). Empty = off, and then no page shows anything about e-mail: no button, no setting, no sentence in the
privacy texts (Ralf, 2026-10-09). Everything asks enabled() and nothing else. The one exception is the section
"Mailserver" on Administration > Server, which says what is missing.

The sender is always the site (MAIL_FROM); a person who should get the answers goes in Reply-To, because mail
"from" a researcher's own address sent by this server fails DMARC. Single mails (a link to set the password, an
invitation) go out at once in a thread of their own with a short timeout, like the Slack messages (app/notify.py),
so a slow mail server never holds up the page. Series (invitations to a survey) go through the queue of
app/mailqueue.py.

Mails to researchers are written in the recipient's language (lang.of), with in_language().
"""
import smtplib
import ssl
import threading
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from flask import current_app, g, has_request_context

from app import brand, emails

TIMEOUT = 15
SECURITY = ("starttls", "ssl", "none")


def settings(app=None):
    """The mail server as configured; values the .env did not set are empty."""
    cfg = (app or current_app).config
    port = int(cfg.get("MAIL_PORT") or 0)
    security = (cfg.get("MAIL_SECURITY") or "").strip().lower()
    if security not in SECURITY:
        security = "ssl" if port == 465 else "starttls"
    if not port:
        port = {"ssl": 465, "starttls": 587, "none": 25}[security]
    return {"host": (cfg.get("MAIL_HOST") or "").strip(), "port": port, "security": security,
            "user": (cfg.get("MAIL_USER") or "").strip(), "password": cfg.get("MAIL_PASSWORD") or "",
            "sender": (cfg.get("MAIL_FROM") or "").strip(), "name": (cfg.get("MAIL_FROM_NAME") or "Askurious").strip(),
            "per_minute": int(cfg.get("MAIL_PER_MINUTE") or 60)}


def enabled(app=None):
    """Is mail switched on? Only then do mail functions appear anywhere."""
    s = settings(app)
    return bool(s["host"] and s["sender"])


def _local_relay(host):
    """A mail server on this machine: a login there without TLS never leaves it."""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def plaintext_login(app=None):
    """True when MAIL_SECURITY=none sends MAIL_USER and MAIL_PASSWORD unencrypted to a server on another machine
    (audit 2026-10-18, CFG-09): said loudly at the start, at every login and on Administration > Server, so that the
    operators switch to starttls or ssl; mail goes on, a deploy never cuts it off. A relay on this machine (localhost)
    is no problem."""
    s = settings(app)
    return s["security"] == "none" and bool(s["user"]) and not _local_relay(s["host"].lower())


def missing(app=None):
    """The .env lines still missing to switch mail on (for Administration > Server)."""
    s = settings(app)
    out = [k for k, v in (("MAIL_HOST", s["host"]), ("MAIL_FROM", s["sender"])) if not v]
    if s["user"] and not s["password"]:
        out.append("MAIL_PASSWORD")
    return out


def problem(app=None):
    """Why mail cannot go out although it is switched on, for the start (log) and Administration > Server; or ""."""
    if enabled(app) and plaintext_login(app):
        return "MAIL_SECURITY=none with MAIL_USER: the password travels unencrypted; use starttls or ssl"
    return ""


@contextmanager
def in_language(lang):
    """de_en() writes in `lang` within this block (a mail to someone else than the person at the page)."""
    if not has_request_context():
        yield
        return
    had, old = "ui_lang" in g, g.get("ui_lang")
    g.ui_lang = lang
    try:
        yield
    finally:
        if had:
            g.ui_lang = old
        else:
            g.pop("ui_lang", None)


def message(to, subject, text, reply_to=None, app=None, headers=None):
    """The mail as plain text, from the site."""
    s = settings(app)
    msg = EmailMessage()
    msg["From"] = formataddr((brand.apply(s["name"]), s["sender"]))  # the sender name of the domain (app/brand.py)
    # the address in ASCII, the domain as "xn--" (app/emails.py): a mail server without SMTPUTF8 takes nothing else.
    # One that cannot go out at all raises ValueError, which the queue counts as a refusal of this mail (AU1)
    wire = emails.wire_address(to)
    if wire is None:
        raise ValueError("no address that can be sent to")
    msg["To"] = wire
    # on one line: a line break in a survey title would make the mail library refuse the whole mail (audit 2026-10-09b, E6)
    msg["Subject"] = " ".join(str(subject or "").split())
    domain = s["sender"].rsplit("@", 1)[-1] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    reply_to = emails.wire_address(reply_to) if reply_to else None
    if reply_to:  # in ASCII as well, else it became an invalid encoded word and answers got lost (AU1)
        msg["Reply-To"] = reply_to
    msg["Auto-Submitted"] = "auto-generated"
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(text)
    return msg


def _local_hostname(s):
    """The name this server gives itself in EHLO: the domain of MAIL_FROM. Without it smtplib asks socket.getfqdn()
    for every connection, which took up to two seconds on a test machine (audit 2026-10-09b, E11)."""
    return s["sender"].rsplit("@", 1)[-1] or "localhost"


class Connection:
    """One SMTP connection for several mails (a pass of the queue, app/mailqueue.py): opened at the first mail, opened
    anew after a failure. Tests (extensions["mail_outbox_test"]) get the list instead of a server."""

    def __init__(self, app=None):
        self.app = app or current_app._get_current_object()
        self.server = None
        self.reached = False  # the server took the connection and the login in this pass (app/mailqueue.py, AU1)

    def _open(self):
        s = settings(self.app)
        if not (s["host"] and s["sender"]):
            raise RuntimeError("mail is switched off")
        context = ssl.create_default_context()
        local = _local_hostname(s)
        if s["security"] == "ssl":
            server = smtplib.SMTP_SSL(s["host"], s["port"], local_hostname=local, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(s["host"], s["port"], local_hostname=local, timeout=TIMEOUT)
        try:
            if s["security"] == "starttls":
                server.starttls(context=context)
            if s["user"]:
                if s["security"] == "none" and not _local_relay(s["host"].lower()):
                    # the password in plain text over the network (CFG-09): goes on, but never silently
                    self.app.logger.error("Mail: SMTP login without TLS (MAIL_SECURITY=none); set starttls or ssl")
                server.login(s["user"], s["password"])
        except Exception:
            self._quit(server)
            raise
        return server

    def send(self, msg):
        """Hand one mail to the mail server; raises on failure (and drops the connection, the next mail opens one)."""
        s = settings(self.app)
        if not (s["host"] and s["sender"]):
            raise RuntimeError("mail is switched off")
        sent = self.app.extensions.get("mail_outbox_test")
        if sent is not None:  # tests: kept in a list instead of an SMTP server
            sent.append(msg)
            return
        if self.server is None:
            self.server = self._open()
            self.reached = True
        try:
            # the one address of To only, never what the mail library reads out of the header (E5)
            self.server.send_message(msg, to_addrs=[msg["To"]])
        except Exception:
            self.close()
            raise

    @staticmethod
    def _quit(server):
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - closing a broken connection
            try:
                server.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self):
        if self.server is not None:
            server, self.server = self.server, None
            self._quit(server)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def deliver(msg, app=None):
    """Hand one mail to the mail server, now; raises on failure (callers decide what that means)."""
    with Connection(app) as connection:
        connection.send(msg)


def _deliver_quietly(app, msg):
    with app.app_context():
        try:
            deliver(msg, app)
        except Exception as e:  # noqa: BLE001 - a lost mail must not break anything; the reason goes to the log
            app.logger.warning("Mail not sent: %s", type(e).__name__)


def send(to, subject, text, reply_to=None):
    """Send one mail in a thread of its own; returns the thread (tests join it), or None when mail is off."""
    if not enabled():
        return None
    app = current_app._get_current_object()
    try:
        msg = message(to, subject, text, reply_to=reply_to, app=app)
    except ValueError:  # an address stored before the ASCII rule that cannot go out (AU1): lost, like a refused mail
        app.logger.warning("Mail not sent: no address that can be sent to")
        return None
    if queued_here(app):
        # the participants' containers have no MAIL_PASSWORD (deploy/docker/compose.yaml, audit 2026-10-18, CFG-04):
        # the mail waits in the queue, which the housekeeping sends within about a minute (app/mailqueue.py)
        from app.extensions import db
        from app.models import MailOutbox

        # in a transaction of its own: the caller's open work is neither committed nor lost with it
        with db.engine.begin() as connection:
            connection.execute(db.insert(MailOutbox).values(to=to, subject=str(subject or "")[:300], body=text,
                                                             kind="notice", reply_to=reply_to))
        return None
    if app.config.get("MAIL_SYNC"):  # tests
        _deliver_quietly(app, msg)
        return None
    thread = threading.Thread(target=_deliver_quietly, args=(app, msg), daemon=True)
    thread.start()
    return thread


def queued_here(app=None):
    """True in a process of the participants' side alone (SIDE=participant): it never talks to the mail server itself
    but leaves its mails in the queue for the housekeeping (CFG-04)."""
    return ((app or current_app).config.get("SIDE") or "") == "participant"


def signature():
    from app.lang import de_en

    return "\n\n-- \n" + brand.name() + " · " + brand.public_base() + "\n" + de_en(
        "Diese E-Mail wurde automatisch verschickt.", "This email was sent automatically.")
