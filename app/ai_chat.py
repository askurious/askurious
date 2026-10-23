"""KI-Chat for participants (experimental "ai_chat"; concept ki-assistent-tagebuch-konzept.md, E1-E13 as recommended,
E4 and E8 as decided by Ralf 2026-10-23): an element of a questionnaire or a diary module where the participant chats
with an AI assistant, and every message on both sides is kept as study data, because the use of AI is what such a study
looks at.

Where it runs (E1, E2): on the server. The page of the element (items/ai_chat.py, static/ai_chat.js) sends each message
to the participants' address (participate.chat); the server checks the limits, sends the conversation to the model of
the survey and stores both messages before the answer goes back. The Android app shows the questionnaire pages in its
WebView, so the chat works there without an update of the app; a module with a chat is never kept for offline use
(diary.OFFLINE_TYPES), so the app says it needs a connection (E11).

Whose model (E4, decided): each survey has its own model access (AiChatAccess): provider, address, key and model,
entered by a person with the role "Verwaltung" on the page of this module. Never a researcher's personal access, never
the platform's key: the chatbot is itself studied, so where it comes from is recorded (who entered it, when) and goes
into the documentation of the survey (packages.documentation: ai-chat-model.md, without the key). From the start on
provider, address and model stay as they are; a new key raises the version, which each message carries. Without an
access the survey cannot start (the element's validate).

What is stored (E5, E8 decided): the participant's text in full, beside it the masked text that went to the model
(ai_privacy.redact: e-mail, web and phone numbers), the answer, the status, the model, the version of the access, a hash
of the instructions, tokens and the time the model took. The rows go with their participation (an event on Response),
with the survey (lifecycle.delete, retention.purge) and orphans with the housekeeping (sweep). The export has one row
per message (long format "chat", a file of its own; in a public package only with the tick for open answers).

Limits (E10): characters per message and messages per chat (the element), messages per participation and day
(PER_PERSON_DAY), messages of the whole survey per day (AiChatAccess.per_day), and at most AI_CHAT_PARALLEL calls at once
in one process, apart from the researchers' AI calls (app/ai.py). A message that hits a limit gets a fixed sentence and
is logged with the status "limit".

Safety (E12): the model has no tools and sees nothing but the instructions and the conversation. A message that speaks
of suicide or self-harm (CRISIS) gets the help numbers on the page, whatever the model answers, and the status
"crisis"; the researchers see the count per participation in the export. The element states always that an AI answers
(EU AI Act, Art. 50), and the consent gets a tick of its own (items/consent.py, "ai_chat")."""
import hashlib
import re
import threading
import time
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en

bp = Blueprint("ai_chat", __name__, url_prefix="/surveys")

FEATURE = "ai_chat"
CALL_TIMEOUT = 60          # seconds for one answer of the model
MAX_TOKENS = 800           # of one answer
PER_PERSON_DAY = 100       # messages of one participation in 24 hours, over all chats
DEFAULT_PER_DAY = 500      # messages of all participants of a survey in 24 hours (AiChatAccess.per_day)
MAX_PER_DAY = 100_000
MAX_KEY = 400

_lock = threading.Lock()
_running = {"all": 0, "responses": set()}

# Words of a crisis (suicide, self-harm) in the survey languages most used; a hit shows the help on the page (E12). A
# simple list on purpose: it runs on every message, needs no outside service and errs on the side of showing the help.
CRISIS = re.compile(
    r"suizid|selbstmord|umbringen|nicht mehr leben|ritzen|selbstverletz|"
    r"suicid|kill myself|end my life|want to die|self[- ]?harm|cutting myself|"
    r"me quiero morir|quitarme la vida|me suicider|mourir|uccidermi|samobój|intihar|самоубий|自杀|انتحار",
    re.IGNORECASE)


def enabled(survey=None):
    from app import experimental

    return experimental.enabled(FEATURE, survey=survey)


def chat_items(survey):
    return [i for b in survey.blocks for i in b.items if i.type == "ai_chat"]


def access(survey):
    from app.models import AiChatAccess

    return db.session.get(AiChatAccess, survey.id) if survey is not None and survey.id else None


def locked(survey):
    """From the start on, provider, address and model stay as they are (E4): only a new key may come."""
    return survey.status != "draft"


def prompt_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _client(row):
    from app import ai_access, secrets_box

    base = ai_access.PROVIDERS.get(row.provider) or ai_access.check_url(row.base_url)
    key = secrets_box.decrypt(row.key_enc, secrets_box.PARTICIPANT)
    if not key:
        raise ai_access.AccessError("key")
    return ai_access.client(None, base_url=base, key=key)


# --- the page of the access (researchers) ----------------------------------------------------------------------


@bp.app_context_processor
def _template_globals():
    return {"ai_chat_access": access_info}


def access_info(survey):
    """What the element's editor says about the access: {provider_label, model, version}, or None without one."""
    row = access(survey)
    if row is None:
        return None
    from app import ai_access

    return {"provider_label": ai_access.provider_label(row.provider), "model": row.model, "version": row.version}


def _survey(survey_id, need):
    from app import experimental
    from app.surveys import get_survey

    survey = get_survey(survey_id, need=need)
    if not chat_items(survey) and access(survey) is None:
        experimental.require(FEATURE)
    else:
        experimental.require(FEATURE, survey=survey)
    return survey


@bp.route("/<sid:survey_id>/ai-chat", methods=["GET", "POST"])
@login_required
def page(survey_id):
    """The model access of the survey: provider, address, key, model, messages per day."""
    survey = _survey(survey_id, need="admin" if request.method == "POST" else "viewer")
    row = access(survey)
    if request.method == "POST":
        problem = save(survey, row, request.form)
        if problem:
            flash(problem, "error")
        else:
            flash(de_en("Modellzugang gespeichert.", "Model access saved."), "success")
        return redirect(url_for("ai_chat.page", survey_id=survey.id))
    from app import ai_access

    return render_template("ai_chat/access.html", survey=survey, row=row, locked=locked(survey),
                           may_edit=survey.can(current_user, "admin"), providers=ai_access.providers(),
                           origin=ai_access.origin(row.provider, row.model) if row else "",
                           provider_label=ai_access.provider_label(row.provider) if row else "",
                           items=chat_items(survey), default_per_day=DEFAULT_PER_DAY)


def save(survey, row, form):
    """Store the access from the form; returns a message for the page when it cannot (nothing is stored then)."""
    from app import ai_access, secrets_box
    from app.models import AiChatAccess, utcnow

    provider = form.get("provider") or ""
    if provider not in ai_access.PROVIDERS:
        return de_en("Bitte einen Anbieter wählen.", "Please choose a provider.")
    key = "".join((form.get("key") or "").split())
    if len(key) > MAX_KEY:
        return de_en("Der Schlüssel ist zu lang.", "The key is too long.")
    model = " ".join((form.get("model") or "").split())[:120] or (row.model if row is not None else "")
    try:
        base = ai_access.PROVIDERS[provider] or ai_access.check_url(form.get("base_url"))
    except ai_access.AccessError as e:
        return str(e)
    if row is not None and locked(survey) and (provider, base, model) != (row.provider, row.base_url, row.model):
        return de_en("Die Befragung läuft oder lief schon: Anbieter, Adresse und Modell bleiben, wie sie beim Start "
                     "waren. Nur ein neuer Schlüssel ist möglich.", "The survey is or was running: provider, address and "
                     "model stay as they were at the start. Only a new key is possible.")
    same_place = row is not None and (row.provider, row.base_url) == (provider, base)
    use_key = key or (secrets_box.decrypt(row.key_enc, secrets_box.PARTICIPANT) if same_place else "")
    if not use_key:
        return de_en("Bitte den API-Schlüssel einfügen.", "Please paste the API key.")
    try:
        ids = ai_access.model_ids(ai_access.client(None, base_url=base, key=use_key))
    except Exception as e:  # 401/403 from /models: the key does not open the service
        current_app.logger.info("AI chat access: key refused on save: %s", type(e).__name__)
        return ai_access.error_text(ai_access.error_code(e))
    if not model and len(ids) == 1:
        model = ids[0]
    if not model:
        return de_en("Bitte das Modell angeben.", "Please give the model.")
    if not ai_access.MODEL_RE.match(model):
        return de_en("Dieser Modellname ist nicht gültig.", "This model name is not valid.")
    if ids and model not in ids:
        return de_en("Dieses Modell bietet der Zugang nicht an.", "The access does not offer this model.")
    per_day = _whole(form.get("per_day"), DEFAULT_PER_DAY if row is None else row.per_day)
    until = ai_access._valid_until(form.get("valid_until"))
    if until is None and key and provider == "kiconnect":
        from app.timeutil import to_local

        until = to_local(utcnow()).date() + timedelta(days=ai_access.KICONNECT_DAYS)
    who = str(getattr(current_user, "display_name", "") or "")
    changed = row is None or key or (provider, base, model) != (row.provider, row.base_url, row.model)
    if row is None:
        row = AiChatAccess(survey_id=survey.id, version=0, history=[])
        db.session.add(row)
    elif changed:
        row.history = list(row.history or []) + [{"version": row.version, "provider": row.provider,
                                                  "base_url": row.base_url, "model": row.model, "set_by": row.set_by,
                                                  "set_at": row.set_at.isoformat() if row.set_at else None}]
    if changed:
        row.version = (row.version or 0) + 1
        row.set_by, row.set_at = who[:300], utcnow()
    row.provider, row.base_url, row.model = provider, base, model
    if key:
        row.key_enc, row.key_hint = secrets_box.encrypt(key, secrets_box.PARTICIPANT), key[-4:]
    row.per_day = min(max(per_day, 1), MAX_PER_DAY)
    if key or "valid_until" in form:
        row.valid_until = until
    db.session.commit()
    return None


def _whole(value, default):
    value = (value or "").strip()
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 9 else default


def documentation(survey):
    """[(name, bytes)]: where the chatbot of the survey comes from, for the documentation of the survey and the
    pre-registration (packages.documentation): provider, address, model, its versions, the instructions and the limits
    of each chat element; never the key and never a message."""
    items = chat_items(survey)
    if not items:
        return []
    row = access(survey)
    lines = ["# AI chat: model and instructions", ""]
    if row is None:
        lines += ["No model access entered yet.", ""]
    else:
        from app import ai_access

        lines += [f"- Provider: {ai_access.provider_label(row.provider)}", f"- Address: {row.base_url}",
                  f"- Model: {row.model}", f"- Version of the access: {row.version}",
                  f"- Entered by: {row.set_by or '-'}, {row.set_at:%Y-%m-%d %H:%M} UTC" if row.set_at else "- Entered: -",
                  f"- Messages of all participants per 24 hours: at most {row.per_day}", ""]
        for old in row.history or []:
            lines.append(f"- Earlier version {old.get('version')}: {old.get('provider')}, {old.get('base_url')}, "
                         f"{old.get('model')} ({old.get('set_by') or '-'}, {old.get('set_at') or '-'})")
        if row.history:
            lines.append("")
    for item in items:
        kind = item.kind
        lines += [f"## {item.code}", "", f"- Messages per chat: at most {kind.max_messages(item)}, "
                  f"at least {kind.min_messages(item)}", f"- Characters per message: at most {kind.max_chars(item)}", ""]
        for lang in item.survey.languages:
            system = kind.system_prompt(item, lang)
            lines += [f"Instructions ({lang}, hash {prompt_hash(system)}):", "", "```", system or "", "```", ""]
    return [("ai-chat-model.md", "\n".join(lines).encode("utf-8"))]


# --- the chat (participants) -----------------------------------------------------------------------------------


class Busy(Exception):
    pass


class _Slot:
    """One call under way: at most AI_CHAT_PARALLEL in this process, one per participation."""

    def __init__(self, response_id):
        self.response_id = response_id

    def __enter__(self):
        most = int(current_app.config.get("AI_CHAT_PARALLEL", 4) or 0)
        with _lock:
            if (most and _running["all"] >= most) or self.response_id in _running["responses"]:
                raise Busy()
            _running["all"] += 1
            _running["responses"].add(self.response_id)
        return self

    def __exit__(self, *exc):
        with _lock:
            _running["all"] -= 1
            _running["responses"].discard(self.response_id)


def turns(response_id, item_id=None):
    from app.models import AiChatTurn

    query = db.select(AiChatTurn).where(AiChatTurn.response_id == response_id)
    if item_id is not None:
        query = query.where(AiChatTurn.item_id == item_id)
    return list(db.session.scalars(query.order_by(AiChatTurn.item_id, AiChatTurn.seq, AiChatTurn.id)))


def summary(response_id, item_id):
    """{"n": messages of the participant, "chars": their characters, "replies": answers of the model, "crisis": 0/1} of
    one chat, or None before the first message: the answer of the element (items/ai_chat.py)."""
    rows = turns(response_id, item_id)
    mine = [t for t in rows if t.role == "user"]
    if not mine:
        return None
    return {"n": len(mine), "chars": sum(len(t.text or "") for t in mine),
            "replies": sum(1 for t in rows if t.role == "assistant" and t.status == "ok"),
            "crisis": int(any(t.status == "crisis" for t in mine))}


def _count_since(*where, hours=24):
    from app.models import AiChatTurn, utcnow

    return db.session.scalar(db.select(db.func.count(AiChatTurn.id)).where(
        AiChatTurn.role == "user", AiChatTurn.created_at >= utcnow() - timedelta(hours=hours), *where)) or 0


def _texts(lang):
    from app.ui_i18n import tr

    return {
        "limit_chat": tr(lang, "Sie haben die Zahl der Nachrichten für dieses Gespräch erreicht.",
                         "You have reached the number of messages for this conversation."),
        "limit_day": tr(lang, "Der KI-Assistent ist für heute nicht mehr verfügbar. Sie können trotzdem weitermachen.",
                        "The AI assistant is not available any more today. You can still continue."),
        "busy": tr(lang, "Der KI-Assistent ist gerade ausgelastet. Bitte senden Sie die Nachricht gleich noch einmal.",
                   "The AI assistant is busy right now. Please send the message again in a moment."),
        "error": tr(lang, "Der KI-Assistent hat gerade nicht geantwortet. Bitte versuchen Sie es noch einmal.",
                    "The AI assistant did not answer just now. Please try again."),
        "no_access": tr(lang, "Der KI-Assistent ist gerade nicht verfügbar. Sie können trotzdem weitermachen.",
                        "The AI assistant is not available right now. You can still continue."),
        "empty": tr(lang, "Bitte schreiben Sie eine Nachricht.", "Please write a message."),
        "long": tr(lang, "Die Nachricht ist zu lang.", "The message is too long."),
    }


def _store(response, item, role, text, status, row=None, system="", masked=None, tokens=(None, None), millis=None):
    from app.models import AiChatTurn

    seq = db.session.scalar(db.select(db.func.coalesce(db.func.max(AiChatTurn.seq), 0)).where(
        AiChatTurn.response_id == response.id, AiChatTurn.item_id == item.id)) or 0
    turn = AiChatTurn(survey_id=response.survey_id, response_id=response.id, item_id=item.id, seq=seq + 1, role=role,
                      text=text, masked=masked, status=status, model=row.model if row else "",
                      access_version=row.version if row else 0, prompt_hash=prompt_hash(system) if system else "",
                      tokens_in=tokens[0], tokens_out=tokens[1], millis=millis)
    db.session.add(turn)
    db.session.flush()
    return turn


def send(survey, response, item, message, lang):
    """One message of the participant: checked, sent to the model with the conversation so far, both stored.
    Returns (JSON for the page, HTTP status). The caller commits."""
    from app import ai_privacy

    words = _texts(lang)
    kind = item.kind
    message = (message or "").strip()
    if not message:
        return {"error": words["empty"]}, 400
    if len(message) > kind.max_chars(item):
        return {"error": words["long"]}, 400
    crisis = bool(CRISIS.search(message))
    done = [t for t in turns(response.id, item.id) if t.role == "user"]
    row = access(survey)
    system = kind.system_prompt(item, lang)

    def refused(key, status):
        _store(response, item, "user", message, status, row, system, ai_privacy.redact(message))
        return {"refused": words[key], "crisis": crisis, "left": max(kind.max_messages(item) - len(done) - 1, 0)}, 200

    if len(done) >= kind.max_messages(item):
        return {"error": words["limit_chat"], "left": 0}, 409
    if row is None:
        return refused("no_access", "no_access")
    from app.models import AiChatTurn

    if _count_since(AiChatTurn.response_id == response.id) >= PER_PERSON_DAY or \
            _count_since(AiChatTurn.survey_id == survey.id) >= row.per_day:
        return refused("limit_day", "limit")
    masked = ai_privacy.redact(message)
    history = [{"role": "system", "content": system}] if system else []
    for t in turns(response.id, item.id):
        if t.role == "user":
            history.append({"role": "user", "content": t.masked if t.masked is not None else ai_privacy.redact(t.text)})
        elif t.status == "ok":
            history.append({"role": "assistant", "content": t.text})
    history.append({"role": "user", "content": masked})
    status_user = "crisis" if crisis else "ok"
    # the participant's message is stored before the call: the write lock is not held while the model answers
    _store(response, item, "user", message, status_user, row, system, masked)
    db.session.commit()
    started = time.monotonic()
    try:
        with _Slot(response.id):
            reply, tokens = _call(row, history)
    except Busy:
        _mark_last(response, item, "error")
        return {"error": words["busy"], "crisis": crisis}, 429
    except Exception as e:  # the service failed: the message stays logged with the status "error"
        current_app.logger.info("AI chat: call failed: %s", type(e).__name__)
        _mark_last(response, item, "error")
        return {"error": words["error"], "crisis": crisis}, 502
    millis = int((time.monotonic() - started) * 1000)
    _store(response, item, "assistant", reply, "ok", row, system, tokens=tokens, millis=millis)
    return {"reply": reply, "crisis": crisis, "left": max(kind.max_messages(item) - len(done) - 1, 0)}, 200


def _mark_last(response, item, status):
    """The participant's message just stored got no answer: its status says why (it stays in the log)."""
    from app.models import AiChatTurn

    last = db.session.scalar(db.select(AiChatTurn).where(
        AiChatTurn.response_id == response.id, AiChatTurn.item_id == item.id, AiChatTurn.role == "user")
        .order_by(AiChatTurn.seq.desc()).limit(1))
    if last is not None and last.status != "crisis":
        last.status = status


def _call(row, history):
    """(text of the answer, (tokens in, tokens out)) from the model of the access."""
    client = _client(row).with_options(timeout=CALL_TIMEOUT)
    out = client.chat.completions.create(model=row.model, messages=history, max_tokens=MAX_TOKENS)
    text = ((out.choices[0].message.content if out.choices else "") or "").strip()
    usage = getattr(out, "usage", None)
    return text[:20_000], (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


# --- deletion ----------------------------------------------------------------------------------------------------


def forget_survey(survey_id, access_too=False):
    """The messages of every participation of a survey (retention.purge, lifecycle.delete); with access_too the
    access as well (lifecycle.delete)."""
    from app.models import AiChatAccess, AiChatTurn

    db.session.execute(db.delete(AiChatTurn).where(AiChatTurn.survey_id == survey_id))
    if access_too:
        db.session.execute(db.delete(AiChatAccess).where(AiChatAccess.survey_id == survey_id))


def sweep():
    """Messages whose participation or survey is gone (deleted past the session events): the housekeeping."""
    from app.models import AiChatAccess, AiChatTurn, Response, Survey

    gone = db.session.execute(db.delete(AiChatTurn).where(
        ~db.exists().where(Response.id == AiChatTurn.response_id))).rowcount or 0
    db.session.execute(db.delete(AiChatAccess).where(~db.exists().where(Survey.id == AiChatAccess.survey_id)))
    db.session.commit()
    return gone


def keep_with_responses():
    """A participation deleted through the session (deletion code, a declined consent, a test run, the diary app)
    takes its messages along in the same transaction."""
    from sqlalchemy import event

    from app.models import AiChatTurn, Response

    if _running.get("listening"):  # once per process, whatever the number of apps (tests make many)
        return
    _running["listening"] = True
    table = AiChatTurn.__table__

    @event.listens_for(Response, "after_delete")
    def _deleted(mapper, connection, target):
        connection.execute(table.delete().where(table.c.response_id == target.id))


# --- export (the long format of the element) -----------------------------------------------------------------


LONG_COLUMNS = ["message", "role", "text", "masked", "status", "model", "access_version", "prompt_hash", "sent_at",
                "seconds", "tokens_in", "tokens_out"]


def long_rows(item, response):
    out = []
    for n, t in enumerate(turns(response.id, item.id), 1):
        out.append({"message": n, "role": "participant" if t.role == "user" else "ai", "text": t.text,
                    "masked": t.masked if t.role == "user" else "", "status": t.status, "model": t.model,
                    "access_version": t.access_version, "prompt_hash": t.prompt_hash,
                    "sent_at": t.created_at.replace(microsecond=0).isoformat() + "Z" if t.created_at else "",
                    "seconds": round(t.millis / 1000, 1) if t.millis is not None else "",
                    "tokens_in": t.tokens_in if t.tokens_in is not None else "",
                    "tokens_out": t.tokens_out if t.tokens_out is not None else ""})
    return out
