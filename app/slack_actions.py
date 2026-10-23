"""Clicks on the buttons of the Slack messages (app/notify.py): Slack sends them to /slack/aktion.

Only requests signed with the Slack app's signing secret (config SLACK_SIGNING_SECRET) and at most five minutes
old are taken; only Slack members listed in SLACK_ADMINS (member ids "U…") may act, everyone else is told so.
Actions: "pause" pauses the survey of a complaint (as the button "Pausieren" on the Feld page; researchers start
it again there), "done" marks the report as done. The message in Slack is then replaced by one that says who did
what and when. Answers go to the response_url Slack sends along (only hooks.slack.com)."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, request

from app.extensions import csrf, db

bp = Blueprint("slack", __name__, url_prefix="/slack")
MAX_AGE = 300  # seconds; older requests could be replays


def signed(req):
    secret = current_app.config.get("SLACK_SIGNING_SECRET")
    stamp, sig = req.headers.get("X-Slack-Request-Timestamp", ""), req.headers.get("X-Slack-Signature", "")
    # ASCII digits only and a comparison of bytes: "²" or "ä" in a header gave a 500 (audit 2026-10-09, K2)
    if not secret or not (stamp.isascii() and stamp.isdigit()) or abs(time.time() - int(stamp)) > MAX_AGE:
        return False
    base = b"v0:" + stamp.encode() + b":" + req.get_data()
    mine = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mine.encode(), sig.encode("utf-8", "replace"))


def _answer(url, payload):
    """Reply over Slack's response_url, which must be a Slack address over https (no other scheme or port; audit
    2026-10-13, IN-4)."""
    from app import notify

    try:
        parts = urlparse(url or "")
        port = parts.port
    except ValueError:
        return
    if parts.scheme == "https" and parts.hostname == "hooks.slack.com" and port in (None, 443):
        notify.post(url, payload)


@bp.route("/aktion", methods=["POST"])
@csrf.exempt
def action():
    from app import lifecycle
    from app.models import Feedback, Survey, utcnow
    from app.notify import blocks
    from app.timeutil import to_local

    if not signed(request):
        abort(403)
    try:
        payload = json.loads(request.form.get("payload", ""))
    except ValueError:
        abort(400)
    acts = payload.get("actions") or []
    if payload.get("type") != "block_actions" or not acts:
        return ""
    act, reply = acts[0], payload.get("response_url")
    if act.get("action_id") == "open":  # a link: Slack opens it, nothing to do here
        return ""
    user = payload.get("user") or {}
    if user.get("id") not in current_app.config.get("SLACK_ADMINS", []):
        _answer(reply, {"response_type": "ephemeral", "replace_original": False,
                        "text": "Nicht berechtigt: Diese Knöpfe sind den Betreibern von Askurious vorbehalten."})
        return ""
    item = db.session.get(Feedback, int(act["value"])) if str(act.get("value", "")).isdigit() else None
    if item is None:
        _answer(reply, {"response_type": "ephemeral", "replace_original": False, "text": "Diese Meldung gibt es nicht mehr."})
        return ""
    who, now = user.get("name") or user.get("username") or user.get("id"), to_local(utcnow()).strftime("%d.%m. %H:%M")
    if act.get("action_id") == "pause":
        survey = item.survey
        if survey is None or survey.status != Survey.RUNNING:
            outcome = "Befragung läuft nicht (mehr); nichts pausiert."
        else:
            lifecycle.close(survey, "paused")
            log = list(survey.status_log or [])
            log[-1] = {**log[-1], "by": f"Slack: {who}"}  # who paused, for the timeline and the log
            survey.status_log = log
            outcome = f":double_vertical_bar: pausiert von {who}, {now}"
    elif act.get("action_id") == "done":
        item.status = "done"
        outcome = f":white_check_mark: erledigt von {who}, {now}"
    else:
        return ""
    db.session.commit()
    current_app.logger.info("Slack action %s on feedback %s by %s", act.get("action_id"), item.id, user.get("id"))
    _answer(reply, {"replace_original": True, "text": outcome, "blocks": blocks(item, outcome)})
    return ""
