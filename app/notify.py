"""Messages to the operators' Slack channel over an incoming webhook (config SLACK_WEBHOOK_URL; empty = off).

Sent in a thread of its own with a short timeout, so a slow or unreachable Slack never holds up the page. The
message names the kind of report and the survey, quotes its text (at most TEXT_LIMIT characters) and links to the
list on /feedback/; how to reach the sender of a complaint stays in Askurious.

Short messages (the default, Ralf 2026-10-21): as long as there is no data processing agreement with Slack, a site admin
leaves the Slack messages on "knapp" (Administration > Server, SiteSetting CONTENT_KEY): then no names, no
organisations, no survey titles and no excerpts of texts go to Slack, only account numbers, survey codes and links.
"ausführlich" sends them as before.

With interactivity switched on in the Slack app (app/slack_actions.py), a message has buttons: pause the survey
(complaints only), mark as done, open in Askurious. Without it Slack shows the buttons too, but a click goes
nowhere; the link still works."""
import json
import threading
import urllib.request

from flask import current_app

from app import brand

TIMEOUT = 5
TEXT_LIMIT = 500  # characters of the report's text in the message
CONTENT_KEY = "slack_content"  # SiteSetting: "full" sends names, titles and excerpts; anything else is short


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not followed: the message goes to the given Slack address or nowhere (audit 2026-10-13, IN-4)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _post(url, payload, logger):
    try:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with _opener.open(request, timeout=TIMEOUT) as answer:
            answer.read()
    except Exception as e:  # noqa: BLE001 - a lost message must not break anything
        logger.warning("Slack message not sent: %s", type(e).__name__)


def post(url, payload):
    """Send `payload` to a Slack address in a thread of its own; returns the thread (tests join it)."""
    thread = threading.Thread(target=_post, args=(url, payload, current_app.logger), daemon=True)
    thread.start()
    return thread


def slack(text, blocks=None):
    """Post `text` (Slack mrkdwn; with `blocks` it is what notifications show) to the channel; returns the
    thread, or None when switched off."""
    url = current_app.config.get("SLACK_WEBHOOK_URL")
    if not url:
        return None
    return post(url, {"text": text, **({"blocks": blocks} if blocks else {})})


def _plain(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def full():
    """Whether a site admin chose detailed messages (names, titles, excerpts); short ones are the default.

    The participants' side (complaints, caps of a survey or live vote) always sends short ones and never reads the
    setting: its database role may not read site_setting, which also keeps the locked token of the monitoring feed."""
    from flask import has_request_context, request

    from app import hosts
    from app.extensions import db
    from app.models import SiteSetting

    if hosts.on_participant_side() or (has_request_context() and hosts.side_of(request.endpoint) == hosts.PARTICIPANT):
        return False
    try:  # only the value: the participants' role may read key and value of this table, nothing more
        value = db.session.scalar(db.select(SiteSetting.value).where(SiteSetting.key == CONTENT_KEY))
    except Exception:  # noqa: BLE001 - without the setting the short form, which sends least
        return False
    return value == "full"


def set_full(on, user):
    """Store the choice of a site admin (Administration > Server); the caller commits."""
    from app.extensions import db
    from app.models import SiteSetting, utcnow

    row = db.session.get(SiteSetting, CONTENT_KEY)
    if row is None:
        row = SiteSetting(key=CONTENT_KEY, value="")
        db.session.add(row)
    value = "full" if on else "short"
    if row.value != value:
        row.value, row.changed_by_id, row.changed_at = value, user.id, utcnow()


def survey_ref(survey):
    """`code` and, in detailed messages, its title: the title can name people or places."""
    return f"`{survey.code}`" + (f" „{_plain(survey.title)}“" if full() else "")


def person(user):
    """"Konto Nr. 12", in detailed messages with the name before it."""
    number = f"Konto Nr. {user.number or user.id}"
    return f"{_plain(user.name) or 'ohne Namen'}, {number}" if full() else number


def headline(item):
    from app.models import Feedback

    if item.severity == Feedback.COMPLAINT:
        head = f":rotating_light: *Beschwerde* ({_plain(item.tag) or 'ohne Art'})"
        where = f" zur Befragung {survey_ref(item.survey)}" if item.survey else ""
    else:
        head = {3: ":beetle: *Fehler gemeldet*", 2: ":warning: *Störung gemeldet*"}.get(item.severity, ":bulb: *Anregung*")
        where = f" auf {_plain(item.page)}" if item.page else ""
    return head + where


def excerpt(item):
    """The report's text as a quote, at most TEXT_LIMIT characters (a complaint without the line naming the survey,
    which the headline says already)."""
    from app.models import Feedback

    if not full():  # the text can name people: it is read in Askurious
        return ""
    text = (item.message or "").strip()
    if item.severity == Feedback.COMPLAINT and text.startswith("Befragung ") and "\n\n" in text:
        text = text.split("\n\n", 1)[1].strip()
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT - 1].rstrip() + "…"
    return "\n".join("> " + line for line in _plain(text).splitlines()) if text else ""


def blocks(item, outcome=None):
    """The message about a report: the headline, the state of the survey, and buttons; once something was done,
    `outcome` (e.g. "pausiert von Philipp, 02.10. 10:42") as a line of its own, and only the buttons that still apply."""
    from flask import url_for

    from app.models import Feedback, Survey

    link = brand.public_base() + url_for("feedback.index")
    out = [{"type": "section", "text": {"type": "mrkdwn", "text": headline(item)}}]
    quote = excerpt(item)
    if quote:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": quote}})
    survey = item.survey if item.severity == Feedback.COMPLAINT else None
    if survey is not None:
        state = {Survey.RUNNING: "läuft", Survey.DRAFT: "Entwurf"}.get(survey.status, "pausiert oder geschlossen")
        out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Befragung: {state}"}]})
    if outcome:
        out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": outcome}]})
    buttons = []
    if survey is not None and survey.status == Survey.RUNNING:
        buttons.append({"type": "button", "action_id": "pause", "value": str(item.id), "style": "danger",
                        "text": {"type": "plain_text", "text": "Befragung pausieren"},
                        "confirm": {"title": {"type": "plain_text", "text": "Befragung pausieren?"},
                                    "text": {"type": "mrkdwn", "text": f"{survey_ref(survey)} "
                                             "nimmt dann keine Teilnahmen mehr an, bis die Forschenden sie wieder "
                                             "starten."},
                                    "confirm": {"type": "plain_text", "text": "Pausieren"},
                                    "deny": {"type": "plain_text", "text": "Abbrechen"}}})
    if item.status != "done" and item.severity >= 3:  # suggestions and annoyances: for information only
        buttons.append({"type": "button", "action_id": "done", "value": str(item.id),
                        "text": {"type": "plain_text", "text": "Erledigt"}})
    buttons.append({"type": "button", "action_id": "open", "url": link,
                    "text": {"type": "plain_text", "text": "In Askurious öffnen"}})
    out.append({"type": "actions", "elements": buttons})
    return out


def feedback(item):
    """Announce a report: complaints and errors (severity 3 and up) with buttons, suggestions and annoyances for
    information, with the link only."""
    return slack(headline(item), blocks(item))


def new_user(user):
    """Announce a registration (the first sign-in with ORCID): name, institution and the number of the account,
    with a link to its page in the administration; not the ORCID iD. It says whether the account may create surveys:
    an invited person may not until a site admin ticks it there (security audit 2026-10-06, S11)."""
    from flask import url_for

    from app.auth import may_create_surveys

    link = brand.public_base() + url_for("admin.account", user_id=user.id)
    from app import orgs

    org = orgs.affiliation_of(user) if full() else None
    where = f" ({_plain(org)})" if org else ""
    right = "mit Anlegerecht" if may_create_surveys(user) else "ohne Anlegerecht"
    head = f":wave: *Neu registriert:* {person(user)}{where}, {right}"
    return slack(head, [{"type": "section", "text": {"type": "mrkdwn", "text": head}},
                        {"type": "actions", "elements": [{"type": "button", "action_id": "open", "url": link,
                                                          "text": {"type": "plain_text", "text": "Konto ansehen"}}]}])


def reset_request(user):
    """Announce a request "Passwort vergessen?" (auth.forgot_password): the number of the account and a link to its
    page in the administration, where a site admin creates the one-time link; not the address."""
    from flask import url_for

    link = brand.public_base() + url_for("admin.account", user_id=user.id)
    head = f":key: *Passwort vergessen:* {person(user)}, bittet um einen neuen Einmal-Link"
    return slack(head, [{"type": "section", "text": {"type": "mrkdwn", "text": head}},
                        {"type": "actions", "elements": [{"type": "button", "action_id": "open", "url": link,
                                                          "text": {"type": "plain_text", "text": "Konto ansehen"}}]}])


def _account_alert(user, head):
    """A message about the sign-in of a site admin's account, with a link to its page in the administration; the
    number of the account, never the address (audit 2026-10-09b, T2 and T9)."""
    from flask import url_for

    link = brand.public_base() + url_for("admin.account", user_id=user.id)
    head = f"{head}: {person(user)}"
    return slack(head, [{"type": "section", "text": {"type": "mrkdwn", "text": head}},
                        {"type": "actions", "elements": [{"type": "button", "action_id": "open", "url": link,
                                                          "text": {"type": "plain_text", "text": "Konto ansehen"}}]}])


def wrong_code(user):
    """The password of a site admin was right, the code after it wrong: the password is known to someone."""
    return _account_alert(user, ":rotating_light: *Admin-Anmeldung: Passwort richtig, Code falsch* (Passwort ist bekannt)")


def hard_locked(user):
    """An account reached passwords.HARD_LIMIT wrong passwords or codes in a row: its sign-in with password is locked
    until a site admin releases it (audit 2026-10-11, AU3)."""
    return _account_alert(user, ":lock: *Anmeldung gesperrt: 100 Fehlversuche in Folge* (Passwort oder Code), "
                                "Freigabe in der Administration")


def admin_password_reset(user):
    """A site admin's password was set over a one-time link ("Passwort vergessen?" by mail)."""
    return _account_alert(user, ":key: *Neues Passwort für ein Admin-Konto* über einen Einmal-Link")


def backup_code_used(user):
    """A site admin used one of the backup codes in place of the code from the app."""
    return _account_alert(user, ":warning: *Ersatzcode benutzt* (Admin-Konto)")


def factor_off(user):
    """A site admin switched the second factor off."""
    return _account_alert(user, ":warning: *Zweiter Faktor ausgeschaltet* (Admin-Konto)")
