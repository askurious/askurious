"""OpenAI access. Models come from .env (OPENAI_TEXT_MODEL, OPENAI_IMAGE_MODEL).

Text calls of a researcher with an own AI access (app/ai_access.py) go over their key, base
URL and model instead (_text_client); images always over the installation's key (E6). Every own access except OpenAI
itself is asked through chat/completions with response_format json_schema: KI:connect's gpt-oss-120b and
mistral-small-4 refuse the Responses API (test 2026-10-10)."""
import threading
from datetime import timedelta

from flask import current_app

from app.lang import de_en


KEEP_DAYS = 730  # AI call statistics (admin page) are deleted after two years


class AIError(RuntimeError):
    """A failed AI request: the sentence for the page; `detail` says what went wrong technically (shown on hover where
    the page offers it, e.g. AInstein, which is experimental)."""

    def __init__(self, message, detail=""):
        super().__init__(message)
        self.detail = detail


# Time limits of one call, below the limit of the proxy in front of the app (nginx answers 504 after it, while the
# process would keep working): text 90 s; an image may take longer. No automatic retries by the SDK (it would retry
# twice by default, so a failing service held a web process for three times the limit plus pauses); a researcher
# simply clicks again. The image limit is passed with the request (generate_image).
TEXT_TIMEOUT = 90
IMAGE_TIMEOUT = 150


def _client():
    key = current_app.config["OPENAI_API_KEY"]
    if not key:
        raise AIError(de_en("Kein OpenAI-Schlüssel konfiguriert.", "No OpenAI key configured."))
    from openai import OpenAI

    return OpenAI(api_key=key, timeout=TEXT_TIMEOUT, max_retries=0)


def _text_client():
    """(client, model, own): the own access of the signed-in researcher (own: its AiAccess row), else the
    installation's key (own: None). A typed address that no longer passes the check stops the call."""
    from app import ai_access

    own = ai_access.active()
    if own is None:
        return _client(), current_app.config["OPENAI_TEXT_MODEL"], None
    try:
        return ai_access.client(own), own.text_model, own
    except ai_access.AccessError as e:
        raise AIError(str(e)) from e


def _switched_on():
    """Nothing goes to an AI service for a person who switched generative AI off ("KI-Nutzung", E4)."""
    from app import ai_access

    if not ai_access.ai_on():
        raise AIError(de_en(*ai_access.OFF))


def _charge(kind, own=False, units=1):
    """Daily limit per researcher: raises AIError once it is reached (with an own access only the technical limit, E7).
    units: how many calls this one counts for (the AI reading of handwriting, app/paper.py: per started AI_UNIT fields)."""
    from app import limits

    message = limits.charge_ai(kind, own=own, units=units)
    if message:
        raise AIError(message)


_slots_lock = threading.Lock()
_running = {"all": 0}  # AI calls under way in this process: "all" and per account id


class _Slot:
    """One AI call under way (security audit 2026-10-13, KR-2): at most AI_PARALLEL in this process and
    AI_PARALLEL_PER_ACCOUNT of one person in it; one more is refused at once (AIError, nothing counted), so that one
    account with a script cannot hold every thread of the researchers' container for 90 to 150 s."""

    def __init__(self):
        self.who = _who()

    def __enter__(self):
        most = int(current_app.config.get("AI_PARALLEL", 4) or 0)
        mine = int(current_app.config.get("AI_PARALLEL_PER_ACCOUNT", 2) or 0)
        with _slots_lock:
            busy = (most and _running["all"] >= most) or (
                mine and self.who is not None and _running.get(self.who, 0) >= mine)
            if not busy:
                _running["all"] += 1
                if self.who is not None:
                    _running[self.who] = _running.get(self.who, 0) + 1
        if busy:
            raise AIError(de_en("Gerade laufen zu viele KI-Aufrufe gleichzeitig. Bitte gleich noch einmal versuchen.",
                                "Too many AI calls are running at the same time. Please try again in a moment."))
        return self

    def __exit__(self, *exc):
        with _slots_lock:
            _running["all"] -= 1
            if self.who is not None:
                _running[self.who] -= 1
                if _running[self.who] <= 0:
                    del _running[self.who]


def limit_kind(e):
    """For a 429: "quota" when the quota (credit, monthly contingent) is used up, else "rate" (too many requests at
    once). OpenAI says insufficient_quota, KI:connect quota_exceeded; anything else counts as a rate limit."""
    body = getattr(e, "body", None)
    words = " ".join(str(x or "") for x in (getattr(e, "code", ""), body.get("type") if isinstance(body, dict) else ""))
    return "quota" if "quota" in words.lower() else "rate"


# 4xx answers that refuse a request before the model works on it: the key, the rights, the model name or the form
# of the request. A 429 counts (decision 5a: the provider took the request in; its dialog stays, _note_limit).
NOT_PROCESSED = (400, 401, 403, 404)


def sent(e):
    """Whether the failed call may have reached the provider and been worked on (and billed). False only when that is
    certain: no connection (refused, unknown name, connect timeout, an address the check refused, too many at
    once) or a refusal before processing (NOT_PROCESSED). Timeouts after sending, answers broken off or too large,
    5xx and 429 count (audit 2026-10-10, K2, decision 5a)."""
    import httpx
    import openai

    if isinstance(e, openai.APIStatusError):
        return getattr(e, "status_code", None) not in NOT_PROCESSED
    if isinstance(e, openai.APIConnectionError):  # also APITimeoutError; the httpx error is its cause
        return not isinstance(e.__cause__, (httpx.ConnectError, httpx.ConnectTimeout))
    return True


def _refund(e=None):
    """A failed call does not count on the daily limit (Ralf, 2026-10-10), as long as it surely did not reach the
    provider (sent; decision 5a, 2026-10-11)."""
    from app import limits

    if e is not None and sent(e):
        from flask import g, has_app_context

        if has_app_context():
            g.pop("ai_charged", None)  # it stays counted
        return
    limits.refund_ai()


def _note_limit(e, message, own):
    """A 429: kept for the page's script (app/ai_fetch.py), which then opens the dialog with "Erneut versuchen"."""
    if getattr(e, "status_code", None) != 429:
        return
    from flask import g, has_request_context

    if has_request_context():
        g.ai_limit = {"code": limit_kind(e), "message": message, "own": own}


def _own_reason(e):
    """The reason for a failure over an own access (app/ai_access.py), or None where the general one fits."""
    import openai

    status, code = getattr(e, "status_code", None), str(getattr(e, "code", "") or "")
    if isinstance(e, openai.APIConnectionError):
        return de_en("Ihr KI-Anbieter ist gerade nicht erreichbar. Bitte später erneut versuchen.",
                     "Your AI provider cannot be reached right now. Please try again later.")
    if status == 401:
        return de_en("Ihr KI-Schlüssel wurde abgelehnt oder ist abgelaufen. Bitte unter KI-Nutzung erneuern "
                     "(bei KI:connect: „Rotieren“).", "Your AI key was refused or has expired. Please renew it under "
                     "AI usage (at KI:connect: “Rotate”).")
    if status == 403:
        if code == "api_access_disabled":
            return de_en("Ihre Nutzergruppe bei KI:connect hat keinen API-Zugang.",
                         "Your user group at KI:connect has no API access.")
        return de_en("Ihr KI-Anbieter hat den Zugriff abgelehnt.", "Your AI provider refused access.")
    if status == 404:
        return de_en("Das gewählte Modell gibt es für Ihren Zugang nicht mehr. Bitte unter KI-Nutzung neu "
                     "wählen.", "The chosen model no longer exists for your access. Please choose again under "
                     "AI usage.")
    if status == 429 and limit_kind(e) == "quota":
        return de_en("Ihr Kontingent beim KI-Anbieter ist erschöpft. Wählen Sie ein anderes Modell oder versuchen Sie es "
                     "später erneut.", "Your quota at the AI provider is used up. Choose another model or try again "
                     "later.")
    if status == 429:
        return de_en("Bei Ihrem KI-Anbieter gehen gerade zu viele Anfragen ein. Bitte gleich noch einmal versuchen.",
                     "Too many requests are reaching your AI provider right now. Please try again in a moment.")
    if status == 400 and code == "unsupported_model":
        return de_en("Das gewählte Modell kann diese Anfrage nicht beantworten. Bitte unter KI-Nutzung ein "
                     "anderes wählen.", "The chosen model cannot answer this request. Please choose another one under "
                     "AI usage.")
    return None


def _failure(what, e, own=False):
    """The sentence a researcher reads when the call failed: one per kind of failure. The SDK's own text is for
    the log only; it can name the organisation of the key, a part of the key itself or the request. own: the call
    went over the researcher's own access (then 401/403/404/429 speak of their key, not the installation's)."""
    import openai

    status, code = getattr(e, "status_code", None), str(getattr(e, "code", "") or "")
    reason = _own_reason(e) if own else None
    # in the language of the page, `what` too (UI/UX audit 2026-10-09, X85)
    if reason is not None:
        pass
    elif isinstance(e, openai.APIConnectionError):  # also a timeout
        reason = de_en("Der KI-Dienst ist gerade nicht erreichbar. Bitte später erneut versuchen.",
                       "The AI service cannot be reached right now. Please try again later.")
    elif status in (401, 403):
        reason = de_en("Der KI-Dienst hat den Schlüssel dieser Installation abgelehnt. Bitte wenden Sie sich an einen "
                       "Admin der Plattform.", "The AI service refused the key of this installation. Please contact a "
                       "platform admin.")
    elif status == 429 and limit_kind(e) == "quota":
        reason = de_en("Das Kontingent dieser Installation beim KI-Dienst ist erschöpft. Bitte später erneut versuchen "
                       "oder an einen Admin der Plattform wenden.", "The quota of this installation at the AI service "
                       "is used up. Please try again later or contact a platform admin.")
    elif status == 429:
        reason = de_en("Beim KI-Dienst gehen gerade zu viele Anfragen ein. Bitte gleich noch einmal versuchen.",
                       "Too many requests are reaching the AI service right now. Please try again in a moment.")
    elif status == 400 and ("content_policy" in code or "moderation" in code or "safety" in code):
        reason = de_en("Der KI-Dienst hat die Anfrage wegen ihres Inhalts abgelehnt. Bitte den Text ändern und erneut "
                       "versuchen.", "The AI service refused the request because of its content. Please change the "
                       "text and try again.")
    else:
        reason = de_en("Bitte später erneut versuchen. Bleibt der Fehler, wenden Sie sich bitte an einen Admin der "
                       "Plattform.", "Please try again later. If the error stays, please contact a platform admin.")
    return de_en("{what} fehlgeschlagen: {reason}", "{what} failed: {reason}", what=what, reason=reason)


def _who():
    """The signed-in person whose click made the call, for their page "KI-Nutzung" (None for participants)."""
    from flask import has_request_context
    from flask_login import current_user

    if has_request_context() and getattr(current_user, "is_authenticated", False):
        return current_user.id
    return None


def _record(kind, purpose, item_type, model, started, ok, usage=None, images=0, size=None, own=None):
    """Log one request for the admin page (tokens as reported by OpenAI). In its own transaction, so it
    neither commits nor rolls back the request's work; logging never breaks the AI feature itself."""
    import time

    from app.extensions import db
    from app.models import AiCall, utcnow

    try:
        values = {"at": utcnow(), "kind": kind, "purpose": purpose or "other", "item_type": item_type, "model": model,
                  "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None),
                  "images": images, "size": size, "ms": int((time.monotonic() - started) * 1000), "ok": ok,
                  "source": "own" if own is not None else "platform", "provider": own.provider if own is not None else None,
                  "user_id": _who()}
        if values["input_tokens"] is None:  # chat/completions names them prompt/completion
            values["input_tokens"] = getattr(usage, "prompt_tokens", None)
            values["output_tokens"] = getattr(usage, "completion_tokens", None)
        with db.engine.begin() as con:
            con.execute(AiCall.__table__.insert().values(**values))
            # the statistics are kept for two years
            con.execute(AiCall.__table__.delete().where(AiCall.at < utcnow() - timedelta(days=KEEP_DAYS)))
    except Exception as e:  # e.g. the table does not exist yet
        current_app.logger.warning("AI call not logged: %s", e)


def generate_image(prompt, size="1536x1024", purpose="image", item_type=None):
    """Return base64-encoded image data."""
    import time

    _switched_on()
    client = _client()
    with _Slot():
        _charge("image")
        model, started = current_app.config["OPENAI_IMAGE_MODEL"], time.monotonic()
        try:
            result = client.images.generate(model=model, prompt=prompt, size=size, n=1, timeout=IMAGE_TIMEOUT)
        except AIError:
            raise
        except Exception as e:  # the SDK raises many error types; the detail goes to the log, a fixed sentence to the page
            _record("image", purpose, item_type, model, started, False, size=size)
            _refund(e)
            current_app.logger.warning("Image generation failed: %s", e)
            message = _failure(de_en("Bildgenerierung", "Image generation"), e)
            _note_limit(e, message, False)
            raise AIError(message) from e
    _record("image", purpose, item_type, model, started, True, getattr(result, "usage", None), images=1, size=size)
    b64 = result.data[0].b64_json
    from flask import g, has_app_context

    if has_app_context():
        g.pop("ai_charged", None)  # the provider made it: the image stays counted, also an empty answer
    if not b64:  # the provider worked on it: it stays counted (decision 5a)
        raise AIError(de_en("Die Bildgenerierung lieferte kein Bild.", "The image generation returned no image."))
    return b64


def generate_text(instructions, prompt, purpose="text", item_type=None, schema=None, store=False, units=1):
    """Return plain text from the text model (Responses API). With `schema` (a JSON schema, "strict" as Structured
    Outputs require: every property required, no other properties) the answer is JSON of exactly that shape.
    store: whether the provider keeps the response for a later retrieval; no caller reads one back, so never (audit
    2026-10-10, B-1: the coding help sends answer texts of participants). With an own access (app/ai_access.py) the
    call goes over the researcher's key and model; other than OpenAI itself through chat/completions (ask).
    units: how many calls this one counts for on the daily limit (_charge)."""
    import time

    _switched_on()
    client, model, own = _text_client()
    with _Slot():
        _charge("text", own=own is not None, units=units)
        started = time.monotonic()
        try:
            text, meta, usage = ask(client, own.provider if own is not None else "openai", model, instructions, prompt,
                                    purpose, schema, store=store)
        except AIError:
            raise
        except Exception as e:
            _record("text", purpose, item_type, model, started, False, own=own)
            if own is not None:
                from app import ai_access

                ai_access.note_error(own.user_id, ai_access.error_code(e))
            # an own access: only the kind of error in the log, the SDK's text might quote the provider's answer
            current_app.logger.warning("Text generation failed: %s", e if own is None else type(e).__name__)
            _refund(e)
            message = _failure(de_en("Textgenerierung", "Text generation"), e, own=own is not None)
            _note_limit(e, message, own is not None)
            raise AIError(message) from e
    _record("text", purpose, item_type, model, started, True, usage, own=own)
    from flask import g, has_app_context

    if has_app_context():
        g.pop("ai_charged", None)  # it worked: the call stays counted

    if has_app_context():  # for json_answer: why an answer could not be read (cut off at the length limit …)
        g.ai_last = {**meta, "chars": len(text)}
    if not text:  # the provider worked on it: it stays counted (decision 5a)
        raise AIError(de_en("Die KI lieferte keinen Text.", "The AI returned no text."))
    return text


def _parts(prompt, api):
    """The prompt as the API takes it: a text as it is; a list of parts ({"text": ...} or {"png": bytes}, e.g. the
    handwriting of a paper questionnaire, app/paper.py) as one user message with texts and images."""
    if not isinstance(prompt, list):
        return prompt
    import base64

    content = []
    for part in prompt:
        if "png" in part:
            url = "data:image/png;base64," + base64.b64encode(part["png"]).decode()
            content.append({"type": "input_image", "image_url": url} if api == "responses"
                           else {"type": "image_url", "image_url": {"url": url}})
        else:
            content.append({"type": "input_text" if api == "responses" else "text", "text": part.get("text", "")})
    return [{"role": "user", "content": content}] if api == "responses" else content


def ask(client, provider, model, instructions, prompt, purpose, schema=None, store=False):
    """One text call: (text, meta for json_answer, usage). OpenAI (the installation's key or an own OpenAI key)
    through the Responses API; every other own access through chat/completions, `instructions` as the system message,
    `schema` as response_format json_schema. A provider that refuses the schema (400) is asked once more with the
    schema written into the instructions; json_answer reads the answer leniently. The text is always trimmed (Qwen
    starts with blank lines). Over OpenAI's Responses API at most AI_MAX_OUTPUT_TOKENS output tokens (security audit
    2026-10-13, KR-2), generous since reasoning models spend output tokens on thinking; chat/completions of an own
    access gets no limit: its cost is the researcher's, and models there refuse a limit above their context."""
    if provider == "openai":
        extra = {"text": {"format": {"type": "json_schema", "name": purpose or "answer", "schema": schema,
                                     "strict": True}}} if schema else {}
        most = int(current_app.config.get("AI_MAX_OUTPUT_TOKENS", 16000) or 0)
        if most:
            extra["max_output_tokens"] = most
        resp = client.responses.create(model=model, instructions=instructions, input=_parts(prompt, "responses"),
                                       store=store, **extra)
        incomplete = getattr(resp, "incomplete_details", None)
        usage = getattr(resp, "usage", None)
        meta = {"status": getattr(resp, "status", None) or "", "reason": getattr(incomplete, "reason", None) or "",
                "output_tokens": getattr(usage, "output_tokens", None)}
        return (getattr(resp, "output_text", "") or "").strip(), meta, usage
    import json

    import openai

    # chat/completions keeps no answer for a later retrieval unless asked to; `store` is OpenAI's own field, which
    # other providers may refuse, so it is not sent
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": _parts(prompt, "chat")}]
    try:
        if schema:
            fmt = {"type": "json_schema", "json_schema": {"name": purpose or "answer", "schema": schema, "strict": True}}
            resp = client.chat.completions.create(model=model, messages=messages, response_format=fmt)
        else:
            resp = client.chat.completions.create(model=model, messages=messages)
    except openai.BadRequestError as e:
        if not schema or str(getattr(e, "code", "") or "") == "unsupported_model":
            raise
        messages[0]["content"] = (f"{instructions}\n\nAnswer with one JSON object only, matching this JSON schema:\n"
                                  f"{json.dumps(schema, ensure_ascii=False)}")
        resp = client.chat.completions.create(model=model, messages=messages)
    choice = (getattr(resp, "choices", None) or [None])[0]
    message = getattr(choice, "message", None)
    usage = getattr(resp, "usage", None)
    cut = (getattr(choice, "finish_reason", None) or "") == "length"
    meta = {"status": "incomplete" if cut else "completed", "reason": "max_output_tokens" if cut else "",
            "output_tokens": getattr(usage, "completion_tokens", None)}
    return (getattr(message, "content", "") or "").strip(), meta, usage


def json_answer(text):
    """The JSON object in a reply of the text model (app/prompt_review.py, app/text_review.py)."""
    import json
    import re

    m = re.search(r"\{.*\}", text, re.S)
    problem = "" if m else "kein JSON-Objekt in der Antwort"
    try:
        # strict=False: line breaks inside a text (a reply in paragraphs) are no reason to throw the answer away
        data = json.loads(m.group(0), strict=False) if m else None
    except ValueError as e:
        data, problem = None, f"JSON-Fehler: {e}"
    if not isinstance(data, dict):
        raise AIError(_unreadable_message(), _unreadable(text, problem or "kein Objekt"))
    return data


def json_list(text):
    """The first JSON list in a reply of the text model, read as leniently as json_answer (raw line breaks in a text)."""
    import json
    import re

    m = re.search(r"\[.*\]", text, re.S)
    problem = "" if m else "keine JSON-Liste in der Antwort"
    try:
        data = json.loads(m.group(0), strict=False) if m else None
    except ValueError as e:
        data, problem = None, f"JSON-Fehler: {e}"
    if not isinstance(data, list):
        raise AIError(_unreadable_message(), _unreadable(text, problem or "keine Liste"))
    return data


def as_list(value):
    """A value of a model's answer that should be a list: the list, else [] (a number, a text or an object in its place
    is not read character by character or key by key)."""
    return value if isinstance(value, list) else []


def _unreadable_message():
    from app.lang import de_en

    return de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.", "The AI's answer could not be read. Please try again.")


def _unreadable(text, problem):
    """Why an answer could not be read, for the hover hint: the model's own status first (cut off at the length limit
    is the usual cause), then the parser's complaint, the length and the beginning and end of the answer."""
    from flask import g, has_app_context

    last = g.get("ai_last", {}) if has_app_context() else {}
    parts = []
    if last.get("status") == "incomplete":
        parts.append(f"Antwort abgebrochen ({last.get('reason') or 'unvollständig'}): Die KI hörte mitten im JSON auf")
    parts.append(problem)
    parts.append(f"{len(text)} Zeichen" + (f", {last['output_tokens']} Tokens" if last.get("output_tokens") else ""))
    flat = " ".join(text.split())
    parts.append(f"Anfang: {flat[:160]}" + (f" … Ende: {flat[-120:]}" if len(flat) > 280 else ""))
    return " · ".join(parts)


CONSENT_INSTRUCTIONS = """You write the participant information and informed consent text for an online survey in the social
sciences that runs on the platform Askurious.de. The facts (JSON) contain the survey's settings; the text has to
inform participants transparently about every setting that affects them, and about nothing that is not set.

Language: {language}. Plain, friendly language (about CEFR level B1), address participants directly, short sentences.
Format: compact. Each section is ONE paragraph that starts with a short bold label and a colon, followed by the text
on the same line, e.g. "**Freiwilligkeit:** Ihre Teilnahme ist freiwillig …". Separate sections by one blank line.
No heading lines, no title, no greeting, no closing formula, no lists unless really needed ("- "), no other markup.
Do not include the consent question or the answer buttons; they are shown separately.

Sections, in this order; include a section only when its condition holds:
1. Purpose: what the study is about, from survey_title and survey_description (do not invent aims or hypotheses).
2. Procedure and duration: answering an online questionnaire, about duration_minutes minutes.
3. Voluntary participation: free to take part, to skip, and to stop at any time without disadvantage; if
   minimum_age is given, only people of at least that age may take part.
4. Data protection: answers are stored anonymously (no names, no IP addresses, no browser fingerprints, no
   tracking cookies; a technically necessary session cookie only keeps the progress); because they cannot be linked
   to a person, submitted answers cannot be deleted later. If stored_panel_ids is given, say instead that this ID
   from the recruiting panel is stored with the answers (e.g. for payment and checks), so the data are pseudonymous.
5. Technical information, only if technical_information is given: transparently name what is recorded (the coarse
   categories listed, no version numbers, no exact screen size) and why (to check that the questionnaire displays
   well on all devices); nothing that identifies a device or a person. If display_mode is given (with or without
   technical_information), say in one sentence that the survey appears light or dark following the setting of the
   device and that it is recorded which of the two was shown.
6. Time per page, only if page_times is given: transparently say that the time spent on each page is recorded (from
   showing a page until "Next") and why (data quality, e.g. to recognise answers given too fast); no other behaviour
   (no clicks, mouse movements or keystrokes) is recorded. If focus_paste is given (with or without page_times), say
   that it is counted how often the participant switches to another window or tab and how often text is pasted into
   a text field, for data quality; counts only, never what was pasted or where the participant switched to; then do
   not claim that no other behaviour is recorded, only that clicks, mouse movements and keystrokes are not; if
   focus_paste also names characters pasted and input steps, say that these are counted too (numbers only, never the
   text) and do not claim that keystrokes are not counted.
   If automation_check is given, say in one sentence that every page contains a field and a note to AI systems that
   people neither see nor hear read out, and that only whether they came back filled in is stored, to recognise
   answers filled in automatically.
   If repeat_marker is given, say in one sentence that after the participation has ended the browser remembers in a
   cookie, for the number of days given there, that this survey was taken, so that nobody takes part twice, and
   that the cookie holds no identifier and no answers.
   If retention is given, state the retention periods exactly as given there (what is deleted how long after the
   end of the survey).
   If deletion_code is given, say that participants can delete their own answers later: at the end they are shown a
   deletion code, to be used on the privacy page of the survey; without the code the answers cannot be linked to
   them, and what has been analysed or published by then cannot be taken back. Then do not claim that answers
   cannot be deleted after submission.
   If external_website is given, say that part of the survey takes place on the website named there, that a random
   participation ID (none of the answers) is passed on to it, that the data collected there are linked to the
   answers through this ID, and that the time spent there is recorded.
   If voice_recordings is given, say that participants may answer a question by speaking, that the recording of
   their voice is stored on the platform's own server (no other service) and deleted with their answers, and that
   voices can identify people. Then do not claim that no audio is recorded.
   If media_playback is given, say that for the video or audio recording shown it is recorded how it was played
   (start, pause, jumps, which parts played, whether the sound was off, whether the window was in the background),
   for data quality and to know whether the stimulus was seen; no camera and no microphone. Then do not claim that
   no other behaviour is recorded.
   If web_service is given, say that at one point the survey's server calls the web service named there and passes
   some of the answers on to it, that its answer is stored with the answers, and that it has its own privacy notice.
   If ip_region is given, say that the country and, within Germany, the federal state are estimated once from the
   IP address by a lookup on the platform's own server (no third party), that only this result is stored and never
   the IP address, and why (to check where participants come from); the sentence that no IP addresses are stored
   stays true and may stay.
   If location is given, say what it says: at one point the survey asks to share the location of the device, the
   browser asks for permission and participants may decline, and what is stored (the level named there, how accurate
   it was, the federal state); name the estimate from the IP address only if location mentions it.
7. Separate section, only if separate_contact_form is true: optional details at the end (e.g. an email address for
   a raffle) are stored separately and cannot be linked to the answers.
8. Use of the data: if open_data is true, the anonymous data are published for research (in repository, if given,
   under license, if given); otherwise they are used for research only and not passed on. If pooled_constructs is
   given, do not write about it: the platform shows a fixed paragraph on combining these questionnaires with other
   studies under the text; only leave out that the data are not passed on.
9. Ethics, only if ethics_approval is given: the study was reviewed by an ethics committee (name the committee and
   reference if the value is text). Never claim an approval otherwise.
10. Compensation: as given; if it says none, say briefly that there is no compensation.
11. Contact: the responsible researchers with their affiliations, and contact_email for questions.

The names and affiliations of the researchers and the contact email are given as stand-ins in double square
brackets ([[NAME_1]], [[AFFILIATION_1]], [[CONTACT_EMAIL]]). Write them into the text exactly as they are, e.g.
"[[NAME_1]] ([[AFFILIATION_1]])"; never translate, change or leave out a stand-in. They are replaced by the real
values afterwards.

Use only the facts. Never invent names, institutions, email addresses, repositories, licences, amounts, committees,
or reference numbers. If a fact needed for a section that applies is missing (e.g. no contact email), write a
placeholder in square brackets, e.g. [Kontakt-E-Mail]."""
