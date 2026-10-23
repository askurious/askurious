"""The page "KI-Nutzung" (/account/ai; ki-nutzung-seite-konzept.md, E1-E9 as recommended, Ralf 2026-10-10) and the
own AI access (concept eigener-ki-schluessel-konzept.md, E1-E7 as recommended; for everyone since 2026-10-10, Ralf).

The page: the switch "Generative KI nutzen" (User.ai_enabled; off, no AI function or AI note shows and nothing goes
to an AI service: ai_on, ai.generate_text), which access is in use, the own access, the person's calls of the last
30 days (AiCall.user_id), the platform's models and what one may do with them.

A researcher puts their own API key on the page "KI-Nutzung" (section "Eigener KI-Zugang"): KI:connect.nrw, OpenAI or any
OpenAI-compatible address. Their AI text functions then run over their access, quota and terms instead of the key of
the installation (E1 A, E2 A: without an own key the installation's key stays the fallback). Always the key of the
person who clicks, also in a survey edited together (E4 A). The model is picked from the provider's /models (E5 A,
free text if the list is missing). Images keep using the installation's key (E6 A: KI:connect has no image endpoint).
With an own key the daily limit of Askurious does not count, only a technical one against endless loops (E7 A,
OWN_AI_CALLS_PER_DAY, 2000 by default).

The key is stored encrypted (app/secrets_box) and never shown again, only its last four characters; it never goes into
logs, error texts, exports, survey files or copies (it lives in its own table, keyed by the person). It is used only
while generative AI is on for the person; the row goes with the account.

KI:connect (test 2026-10-10, kiconnect-api-docs/test-2026-10-10.md): gpt-oss-120b and mistral-small-4 refuse the
Responses API (400 unsupported_model), so every own access except OpenAI itself goes through chat/completions with
response_format json_schema (app/ai.py). Model ids are the technical ids of /models. No rate-limit headers: a 429
is caught like any other error.

Protection, because the person types the address and this server calls it (SSRF): https only, port 443, the host
must resolve to public addresses only (the check of app/webservice.py: no localhost, private networks, link-local,
this installation's own names), checked when saving and again before every call; no redirects. AI_OWN_URL_HOSTS lets
an admin allow named hosts anyway (e.g. an institute's own model server in a private network). Saving and checking:
at most SAVE_LIMIT per hour and person. Every call goes through GuardedTransport (audit 2026-10-10, K1, K5: the
guards of the web service, W1): to the address that was checked, without a proxy from the environment, within
TEXT_TIMEOUT for the whole call, at most MAX_BYTES of answer, at most PARALLEL calls per person at once; model ids
from /models only up to 120 visible characters. With generative AI off nothing goes to the provider, not even saving
or checking (K7, decision 3a)."""
import re
import socket
import sys
import threading
import time
import zlib
from collections import deque
from datetime import timedelta
from urllib.parse import urlsplit

import httpcore
import httpx
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, secrets_box
from app.extensions import db
from app.lang import de_en

bp = Blueprint("ai_access", __name__, url_prefix="/account/ai")
OFF = ("Generative KI ist in Ihren Einstellungen ausgeschaltet.", "Generative AI is switched off in your settings.")

PROVIDERS = {  # key: base URL (custom: typed)
    "kiconnect": "https://chat.kiconnect.nrw/api/v1",
    "openai": "https://api.openai.com/v1",
    "custom": "",
}
KICONNECT_DAYS = 30  # a KI:connect key is valid for a month: "gültig bis" is filled with today + 30 days
WARN_DAYS = 3  # the banner on "Konto" this many days before "gültig bis"
SAVE_LIMIT = 10  # saves and checks per hour and person
MAX_KEY = 400
MAX_MODELS = 60
# where a model of KI:connect computes, as far as known (the concept's short list; anything else: unknown)
NRW_MODELS = ("gpt-oss-120b", "mistral-small-4")
EU_MODELS = ("gpt-6-",)


class AccessError(Exception):
    pass


def provider_label(key):
    return {"kiconnect": "KI:connect.nrw", "openai": "OpenAI",
            "custom": de_en("Eigene Adresse", "Own address")}.get(key, key or "")


def providers():
    return [(key, provider_label(key)) for key in PROVIDERS]


def origin(provider, model):
    """Where the model computes, as far as Askurious knows: a short text for the page."""
    m = (model or "").lower()
    if provider == "kiconnect":
        if m.startswith(NRW_MODELS):
            return de_en("rechnet nur in NRW", "computes only in NRW")
        if m.startswith(EU_MODELS):
            return de_en("OpenAI/Azure, EU", "OpenAI/Azure, EU")
    if provider == "openai":
        return "OpenAI"
    return de_en("Herkunft unbekannt", "origin unknown")


def row(user):
    from app.models import AiAccess

    if not getattr(user, "is_authenticated", False):
        return None
    return db.session.get(AiAccess, user.id)


def ai_on(user=None):
    """Whether generative AI is on for `user` (by default the one signed in): the switch on "KI-Nutzung" (E4). True
    for a request without a signed-in person (participants: what runs for them is the researchers' decision)."""
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False):
        return True
    return getattr(user, "ai_enabled", True) is not False


def active(user=None):
    """The own access in use for `user` (by default the one signed in): the row if a key and a model are stored (and
    generative AI is on for them), else None (then the installation's key is used)."""
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False) or not ai_on(user):
        return None
    r = row(user)
    return r if r is not None and r.key_enc and r.text_model else None


def shown(user=None, platform=False):
    """For the small line at the AI functions: "KI: eigener Zugang (KI:connect.nrw, gpt-oss-120b)" or "KI: Zugang der
    Plattform"; "" for a person without an own key stored (everything runs over the platform then, nothing to tell
    apart) or with generative AI off. `platform`: a function that always runs over the installation's key (images,
    ai.generate_image), whatever the person stored."""
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False) or not ai_on(user) or row(user) is None:
        return ""
    r = None if platform else active(user)
    if r is None:
        return de_en("KI: Zugang der Plattform", "AI: the platform's access")
    return de_en("KI: eigener Zugang ({provider}, {model})", "AI: own access ({provider}, {model})",
                 provider=provider_label(r.provider), model=r.text_model)


def recipient(user=None):
    """Who receives the texts of the AI text functions, for the sentences "… geht an …": "OpenAI" for the
    platform's access, else the own provider with model and origin, e.g. "KI:connect.nrw (gpt-oss-120b, rechnet nur
    in NRW)"; a typed address is named by its host."""
    from urllib.parse import urlparse

    r = active(current_user if user is None else user)
    if r is None:
        return "OpenAI"
    if r.provider == "custom":
        label = urlparse(r.base_url or "").hostname or provider_label(r.provider)
    else:
        label = provider_label(r.provider)
    where = origin(r.provider, r.text_model)
    return f"{label} ({r.text_model}{', ' + where if where != label else ''})"


# --- the confirmation "Teilnehmende informiert" (audit 2026-10-10, K3 and P-S6, decision 6a) ---------------------
# The AI coding help (app/coding.py, AI_OK) and the AI reading of handwriting (app/paper.py, ai_ok) send texts or
# images of participants only once the survey's owner or one of its admins confirmed that the participants were told,
# and only to the recipient that confirmation names: {"to": {recipient key: {"by", "at", "name"}}}. A person whose AI
# goes elsewhere (an own access, app/ai_access.py: always the key of the person who clicks) needs a confirmation for
# that recipient. A confirmation from before 2026-10-11 ({"by", "at"} without "to") counts for the platform's access.

PLATFORM = "openai"


def recipient_key(user=None):
    """The recipient of the AI text functions of `user` (by default the one signed in) as a key that stays the same
    in every language: "openai" for the platform's access, else "own:<provider>:<host>:<model>" (the model counts:
    at KI:connect it decides where the texts are computed)."""
    r = active(current_user if user is None else user)
    if r is None:
        return PLATFORM
    base = r.base_url if r.provider == "custom" else PROVIDERS.get(r.provider, "")
    host = (urlsplit(base or "").hostname or "").lower()
    return f"own:{r.provider}:{host}:{r.text_model}"[:300]


def confirmations(stored):
    """{recipient key: {"by", "at", "name"}} of a stored confirmation."""
    if not isinstance(stored, dict):
        return {}
    to = stored.get("to")
    if isinstance(to, dict):
        return {k: v for k, v in to.items() if isinstance(v, dict)}
    return {PLATFORM: stored} if "by" in stored else {}


def confirmed(stored, key=None):
    """Whether the stored confirmation covers the recipient `key` (None: any recipient, e.g. for the participants'
    privacy page, which names the platform and own accesses alike)."""
    found = confirmations(stored)
    return bool(found) if key is None else key in found


def confirm(stored, key, name, user_id):
    """The stored confirmation with the recipient `key` added."""
    from app.models import utcnow

    found = confirmations(stored)
    found[key] = {"by": user_id, "at": utcnow().isoformat(timespec="seconds"), "name": str(name or "")[:300]}
    return {"to": found}


def may_confirm(survey, user=None):
    """Only the owner or an admin of the survey gives the confirmation (decision 6a); "Bearbeiten" may not."""
    user = current_user if user is None else user
    return survey.can(user, "admin")


def team_recipients(survey):
    """The recipients the AI functions of this survey's team would send to now: the platform's access (if this
    installation has a key) and the own access in use of every person with at least "Bearbeiten" (members and
    groups); the signed-in person's first. [{"key", "name", "own", "mine"}]."""
    from app.models import AiAccess, SurveyMember, User, WorkGroupMember

    ids = {m.user_id for m in survey.members
           if m.user_id and SurveyMember.LEVEL.get(m.role, 0) >= SurveyMember.LEVEL["editor"]}
    groups = [link.group_id for link in survey.group_links if link.role == "editor"]
    if groups:
        ids |= set(db.session.scalars(db.select(WorkGroupMember.user_id).where(WorkGroupMember.group_id.in_(groups),
                                                                               WorkGroupMember.user_id.is_not(None))))
    mine = recipient_key()
    out = {mine: {"key": mine, "name": recipient(), "own": mine != PLATFORM, "mine": True}}
    if current_app.config.get("OPENAI_API_KEY") and PLATFORM not in out:
        out[PLATFORM] = {"key": PLATFORM, "name": "OpenAI", "own": False, "mine": False}
    if ids:
        users = db.session.scalars(db.select(User).join(AiAccess, AiAccess.user_id == User.id)
                                   .where(User.id.in_(ids))).all()
        for u in users:
            k = recipient_key(u)
            if k != PLATFORM and k not in out:
                out[k] = {"key": k, "name": recipient(u), "own": True, "mine": False}
    return list(out.values())


def days_left(r):
    """Days until "gültig bis" (negative: over), None if no date is set."""
    from app.models import utcnow
    from app.timeutil import to_local

    if r is None or r.valid_until is None:
        return None
    return (r.valid_until - to_local(utcnow()).date()).days


# --- the address ----------------------------------------------------------------------------------------------


def allowed_hosts():
    return {h.lower().rstrip(".") for h in current_app.config.get("AI_OWN_URL_HOSTS") or () if h}


def preset_hosts():
    """The hosts of the fixed providers, called without the address check like AI_OWN_URL_HOSTS (README). KI:connect
    runs on the RWTH network (chat.kiconnect.nrw: 134.130.66.31), which the campus protection (#997) blocks for typed
    addresses; with the check every call over a KI:connect access ended as "nicht erreichbar" (2026-10-15)."""
    return {urlsplit(u).hostname for u in PROVIDERS.values() if u}


def check_url(url):
    """The base URL, cleaned (no trailing slash, no query), if this server may call it; raises AccessError otherwise.
    Resolves the host: called when saving and before every call (a name may point elsewhere later)."""
    from app import webservice

    url = (url or "").strip().rstrip("/")
    if not url or len(url) > 300:
        raise AccessError(de_en("Bitte die Basis-Adresse des Dienstes angeben, z. B. https://…/v1.",
                                "Please give the base address of the service, e.g. https://…/v1."))
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise AccessError(de_en("Die Adresse muss mit https:// beginnen und darf keine Zugangsdaten, Parameter oder "
                                "Anker enthalten.", "The address must start with https:// and may hold no credentials, "
                                "parameters or anchors."))
    host = parts.hostname.lower().rstrip(".")
    if host in allowed_hosts():
        return url
    try:
        port = parts.port or 443
    except ValueError:
        port = 0
    if port != 443:
        raise AccessError(de_en("Nur Adressen über Port 443 sind erlaubt.", "Only addresses on port 443 are allowed."))
    if host in webservice.own_hosts():
        raise AccessError(de_en("Diese Adresse gehört zu Askurious selbst.", "This address belongs to Askurious itself."))
    try:
        webservice._public_address(host, port)
    except webservice.Blocked as e:
        raise AccessError(de_en("Diese Adresse ist durch die Netzwerkregeln der Plattform nicht erlaubt.",
                                "This address is not allowed by the platform's network policy.")) from e
    except OSError as e:
        raise AccessError(de_en("Die Adresse ist nicht erreichbar.", "The address cannot be reached.")) from e
    return url


def base_url_of(r):
    """The address to call for the access `r`: the fixed one of a preset, a typed one checked again."""
    if r.provider in PROVIDERS and PROVIDERS[r.provider]:
        return PROVIDERS[r.provider]
    return check_url(r.base_url)


# --- the client -----------------------------------------------------------------------------------------------


MAX_BYTES = 2_000_000  # of one answer of an own access, after unpacking (K1)
PARALLEL = 3  # calls at once over the own access, per person and process (K1)
# a model id kept from /models or typed: visible ASCII without < > " ' ` (the id is shown in sentences of the editor;
# audit 2026-10-13, XS-4), at most 120 characters
MODEL_RE = re.compile(r"^[!#-&(-;=?-_a-~]{1,120}\Z")

_running = {}  # user id -> calls over the own access running now (this process)


class NotSent(httpx.ConnectError):
    """The call stopped before anything went to the provider (blocked or unknown address, too many at once): it does
    not count on the daily limit (app/ai.py, sent)."""


class TooLarge(httpx.ReadError):
    """The answer is larger than MAX_BYTES."""


class _Watch:
    """The whole call within `seconds`: a watchdog shuts the socket at the deadline. httpx's timeout holds for each
    single read only, so a server that sends a byte every few seconds kept a thread for as long as it liked (audit
    2026-10-10, K1; the same guard as webservice._talk, W1)."""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.over = threading.Event()
        self.sock = None
        self.timer = threading.Timer(max(seconds, 0.01), self._stop)
        self.timer.daemon = True
        self.timer.start()

    def trace(self, name, info):  # httpcore's "trace" extension: the socket of the connection, after TLS too
        if name in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            stream = info.get("return_value")
            self.sock = stream.get_extra_info("socket") if stream is not None else None
            if self.over.is_set():
                self._stop()

    def _stop(self):
        self.over.set()
        if self.sock is not None:
            try:
                socket.socket.shutdown(self.sock, socket.SHUT_RDWR)  # the plain socket's, also under TLS
            except OSError:
                pass

    def left(self):
        return self.deadline - time.monotonic()

    def done(self):
        self.timer.cancel()


class _Body(httpx.SyncByteStream):
    """The answer, read up to MAX_BYTES (unpacked: a small gzip must not grow into gigabytes) and within the deadline."""

    def __init__(self, inner, watch, packed, release):
        self.inner, self.watch, self.release = inner, watch, release
        self.unpack = zlib.decompressobj(zlib.MAX_WBITS | 32) if packed else None

    def __iter__(self):
        total = 0
        try:
            for chunk in self.inner:
                if self.unpack is not None:
                    chunk = self.unpack.decompress(chunk, MAX_BYTES + 1 - total)
                    if self.unpack.unconsumed_tail:
                        raise TooLarge("answer too large")
                total += len(chunk)
                if total > MAX_BYTES:
                    raise TooLarge("answer too large")
                if self.watch.over.is_set() or self.watch.left() < 0:
                    raise httpx.ReadTimeout("timed out")
                yield chunk
        except TooLarge:
            raise
        except (httpx.HTTPError, httpcore.NetworkError, OSError, zlib.error) as e:
            if self.watch.over.is_set():
                raise httpx.ReadTimeout("timed out") from e
            raise
        if self.watch.over.is_set():
            raise httpx.ReadTimeout("timed out")  # closed by the watchdog: what came is not the whole answer

    def close(self):
        try:
            self.inner.close()
        finally:
            self.watch.done()
            self.release()


class GuardedTransport(httpx.BaseTransport):
    """The transport of every call over an own access (K1, K5): the host is looked up and checked here
    (webservice._public_address, the check of the web service) and the connection goes to exactly that address, with
    the name for TLS (SNI and certificate); no second lookup a short-lived DNS answer could turn to an inside address.
    A name in AI_OWN_URL_HOSTS (an admin allowed it) or of a fixed provider (preset_hosts) is not checked. The whole call within `seconds`; at most
    MAX_BYTES of answer; at most PARALLEL calls of the person at once; one connection per call (the watchdog needs
    its socket)."""

    def __init__(self, seconds, user_id=None, allowed=frozenset()):
        self.seconds, self.user_id, self.allowed = seconds, user_id, allowed
        self.inner = httpx.HTTPTransport(limits=httpx.Limits(max_keepalive_connections=0), retries=0)

    def _take(self):
        with _lock:
            if _running.get(self.user_id, 0) >= PARALLEL:
                return False
            _running[self.user_id] = _running.get(self.user_id, 0) + 1
            return True

    def _release(self):
        with _lock:
            n = _running.get(self.user_id, 0) - 1
            if n > 0:
                _running[self.user_id] = n
            else:
                _running.pop(self.user_id, None)

    def handle_request(self, request):
        from app import webservice

        if not self._take():
            raise NotSent("too many calls at once", request=request)
        watch = _Watch(self.seconds)
        try:
            host = request.url.host
            if host.lower().rstrip(".") not in self.allowed:
                try:
                    address = webservice._public_address(host, request.url.port or 443, max(watch.left(), 0.05))
                except (webservice.Blocked, OSError) as e:
                    raise NotSent("address not allowed or unknown", request=request) from e
                request.url = request.url.copy_with(host=address)
                request.extensions = {**request.extensions, "sni_hostname": host}
            left = max(watch.left(), 0.05)
            request.extensions = {**request.extensions, "trace": watch.trace,
                                  "timeout": {"connect": left, "read": left, "write": left, "pool": left}}
            request.headers["Accept-Encoding"] = "gzip, deflate"
            try:
                response = self.inner.handle_request(request)
            except httpx.HTTPError as e:
                if watch.over.is_set():
                    raise httpx.ReadTimeout("timed out", request=request) from e
                raise
            packed = response.headers.get("content-encoding", "").strip().lower()
            if packed not in ("", "identity", "gzip", "deflate"):
                response.close()
                raise httpx.DecodingError(f"unsupported content encoding {packed}", request=request)
            headers = [(k, v) for k, v in response.headers.raw if k.lower() not in (b"content-encoding", b"content-length")]
            body = _Body(response.stream, watch, packed in ("gzip", "deflate"), self._release)
            return httpx.Response(response.status_code, headers=headers, stream=body, extensions=response.extensions)
        except BaseException:
            watch.done()
            self._release()
            raise


def client(r, base_url=None, key=None):
    """An OpenAI client for the access `r` (or for a key not stored yet): no retries, no redirects, no proxy from the
    environment (trust_env=False: a proxy would bypass the address check), and the guarded transport: the checked
    address, the whole call within TEXT_TIMEOUT, at most MAX_BYTES of answer (audit 2026-10-10, K1, K5)."""
    from openai import OpenAI

    from app.ai import TEXT_TIMEOUT

    from flask import has_request_context

    if r is not None:
        user_id = r.user_id
    else:
        user_id = current_user.id if has_request_context() and getattr(current_user, "is_authenticated", False) else None
    transport = GuardedTransport(TEXT_TIMEOUT, user_id=user_id, allowed=frozenset(allowed_hosts() | preset_hosts()))
    return OpenAI(api_key=key or secrets_box.decrypt(r.key_enc), base_url=base_url or base_url_of(r),
                  timeout=TEXT_TIMEOUT, max_retries=0,
                  http_client=httpx.Client(follow_redirects=False, timeout=TEXT_TIMEOUT, trust_env=False,
                                           transport=transport))


def model_ids(c):
    """The text models the access offers (/models), embeddings left out; [] if the list cannot be read. Only ids of
    visible ASCII up to 120 characters are kept (the column text_model holds 120), at most MAX_MODELS (K1)."""
    try:
        ids = []
        for m in c.models.list():
            i = getattr(m, "id", "")
            if isinstance(i, str) and MODEL_RE.match(i):
                ids.append(i)
            if len(ids) >= 5 * MAX_MODELS:
                break
    except Exception as e:
        status = getattr(e, "status_code", None)
        if status in (401, 403):
            raise
        current_app.logger.info("AI access: /models not readable: %s", type(e).__name__)
        return []
    return sorted({i for i in ids if "embed" not in i.lower() and not i.lower().startswith("e5-")})[:MAX_MODELS]


def error_code(e):
    """A short code of a failure, for AiAccess.last_error and the texts of app/ai.py."""
    import openai

    if isinstance(e, openai.APIConnectionError):
        return "unreachable"
    status = getattr(e, "status_code", None)
    code = str(getattr(e, "code", "") or "")
    if status is None:
        return "error"
    return f"{status} {code}".strip()[:40]


def note_error(user_id, code):
    """Store the failure of the own access (code None: it worked), in its own transaction like ai._record."""
    from app.models import AiAccess, utcnow

    try:
        with db.engine.begin() as con:
            con.execute(AiAccess.__table__.update().where(AiAccess.user_id == user_id)
                        .values(last_error=code, checked_at=utcnow()))
    except Exception as e:
        current_app.logger.warning("AI access state not stored: %s", e)


def error_text(code):
    """The sentence on "Konto" for a stored failure."""
    code = code or ""
    if code.startswith("401"):
        return de_en("Ihr KI-Schlüssel wurde abgelehnt oder ist abgelaufen. Bitte hier erneuern (bei KI:connect: "
                     "„Rotieren“).", "Your AI key was refused or has expired. Please renew it here (at KI:connect: "
                     "“Rotate”).")
    if code.startswith("403"):
        return de_en("Ihr Zugang hat keinen API-Zugriff (bei KI:connect: Ihre Nutzergruppe hat keinen API-Zugang).",
                     "Your access has no API access (at KI:connect: your user group has no API access).")
    if code.startswith("404"):
        return de_en("Das gewählte Modell gibt es für Ihren Zugang nicht mehr. Bitte neu wählen.",
                     "The chosen model no longer exists for your access. Please choose again.")
    if code.startswith("429") and "quota" in code:
        return de_en("Ihr Kontingent beim KI-Anbieter ist erschöpft.", "Your quota at the AI provider is used up.")
    if code.startswith("429"):
        return de_en("Bei Ihrem KI-Anbieter gingen zu viele Anfragen auf einmal ein.",
                     "Too many requests reached your AI provider at once.")
    if code == "unreachable":
        return de_en("Ihr KI-Anbieter war nicht erreichbar.", "Your AI provider could not be reached.")
    return de_en("Der letzte Aufruf über Ihren Zugang ist fehlgeschlagen ({code}).",
                 "The last call over your access failed ({code}).", code=code)


# --- limits ---------------------------------------------------------------------------------------------------

_saves = {}
_lock = threading.Lock()


def allow_save(user_id):
    """SAVE_LIMIT saves and checks per hour and person (in this process)."""
    import time

    now = time.monotonic()
    with _lock:
        times = _saves.setdefault(user_id, deque())
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= SAVE_LIMIT:
            return False
        times.append(now)
        return True


# --- pages ----------------------------------------------------------------------------------------------------


def _back():
    from app import account_setup

    return account_setup.back("ai") or redirect(url_for("accounts.connections") + "#ki-zugang")


def _off():
    """With generative AI off nothing goes to the provider, not even the reading of /models or the check (audit
    2026-10-10, K7/R4, decision 3a): the page says so instead."""
    if ai_on():
        return False
    flash(de_en(*OFF) + " " + de_en("Schalten Sie sie auf der Seite „KI-Nutzung“ ein, um den Zugang zu speichern oder zu prüfen.",
                                    "Switch it on on the page “AI usage” to save or check the access."), "error")
    return True


def _limited():
    if allow_save(current_user.id):
        return False
    flash(de_en("Zu viele Versuche in der letzten Stunde. Bitte später erneut.",
                "Too many attempts in the last hour. Please try again later."), "error")
    return True


def _valid_until(text):
    from datetime import date

    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@bp.route("/save", methods=["POST"])
@login_required
def save():
    """Provider, address, key and "gültig bis"; reads the models at once (E5)."""
    from app.models import AiAccess, utcnow
    from app.timeutil import to_local

    from app import accounts

    from app import account_setup

    again = accounts.need_fresh(url_for("account_setup.step", name="ai") if account_setup.due() else url_for("accounts.connections") + "#ki-zugang")
    if again:
        return again
    if _off() or _limited():
        return _back()
    provider = request.form.get("provider") or ""
    if provider not in PROVIDERS:
        flash(de_en("Bitte einen Anbieter wählen.", "Please choose a provider."), "error")
        return _back()
    r = row(current_user)
    key = "".join((request.form.get("key") or "").split())
    if len(key) > MAX_KEY or (not key and r is None):
        flash(de_en("Bitte den API-Schlüssel einfügen.", "Please paste the API key."), "error")
        return _back()
    try:
        base = PROVIDERS[provider] or check_url(request.form.get("base_url"))
    except AccessError as e:
        flash(str(e), "error")
        return _back()
    # the stored key only goes to the address it was given for: a new provider or address needs the key again
    use_key = key or (secrets_box.decrypt(r.key_enc) if (r.provider, r.base_url) == (provider, base) else "")
    if not use_key:
        flash(de_en("Bitte den API-Schlüssel einfügen.", "Please paste the API key."), "error")
        return _back()
    try:
        ids = model_ids(client(r, base_url=base, key=use_key))
    except Exception as e:  # 401/403 from /models: the key does not open the service
        current_app.logger.info("AI access: key refused on save: %s", type(e).__name__)
        flash(error_text(error_code(e)), "error")
        return _back()
    if r is None:
        r = AiAccess(user_id=current_user.id, text_model="")
        db.session.add(r)
    if r.provider != provider:
        r.text_model = ""
    r.provider, r.base_url = provider, base
    if key:
        r.key_enc, r.key_hint = secrets_box.encrypt(key), key[-4:]
    r.models = ids
    if r.text_model and ids and r.text_model not in ids:
        r.text_model = ""
    if not r.text_model and len(ids) == 1:
        r.text_model = ids[0]
    until = _valid_until(request.form.get("valid_until"))
    if until is None and key and provider == "kiconnect":
        until = to_local(utcnow()).date() + timedelta(days=KICONNECT_DAYS)
    if key or "valid_until" in request.form:
        r.valid_until = until
    r.last_error, r.checked_at = None, utcnow()
    db.session.commit()
    if ids and not r.text_model:
        flash(de_en("Gespeichert. Bitte jetzt das Modell wählen.", "Saved. Please choose the model now."), "success")
    elif not ids:
        flash(de_en("Gespeichert. Die Modellliste war nicht lesbar; bitte den Modellnamen eintragen.",
                    "Saved. The model list could not be read; please type the model name."), "success")
    else:
        flash(de_en("Gespeichert.", "Saved."), "success")
    return _back()


@bp.route("/model", methods=["POST"])
@login_required
@autosave.aware
def model():
    """The model of the own access: saved on its choice in the list, a typed one when its field is left (autosave;
    a refusal shows in the line of the form)."""
    r = row(current_user)
    if r is None:
        return _back()
    name = " ".join((request.form.get("model") or "").split())[:120]
    # "": the platform's access (the dialog after a limit, app/ai_fetch.py); the list stays for a later choice
    if name and not MODEL_RE.match(name):  # audit 2026-10-13, XS-4
        flash(de_en("Diesen Modellnamen kann Askurious nicht verwenden.", "Askurious cannot use this model name."), "error")
        return _back()
    if name and r.models and name not in r.models:
        flash(de_en("Bitte ein Modell aus der Liste wählen.", "Please choose a model from the list."), "error")
        return _back()
    r.text_model = name
    r.last_error = None
    db.session.commit()
    flash(de_en("Modell gespeichert.", "Model saved."), "success")
    return _back()


CHECK_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
                "additionalProperties": False}


@bp.route("/check", methods=["POST"])
@login_required
def check():
    """"Verbindung prüfen": /models and a tiny request with a schema; reports in green or names the failure."""
    from app import ai

    r = row(current_user)
    if r is None or _off() or _limited():
        return _back()
    try:
        c = client(r)
        ids = model_ids(c)
        if ids:
            r.models = ids
            db.session.commit()
        if not r.text_model:
            raise AccessError(de_en("Bitte zuerst ein Modell wählen.", "Please choose a model first."))
        ai.ask(c, r.provider, r.text_model, "Answer as JSON.", 'Antworte mit OK: {"answer": "OK"}', "check", CHECK_SCHEMA)
    except AccessError as e:
        flash(str(e), "error")
        return _back()
    except Exception as e:
        code = error_code(e)
        note_error(r.user_id, code)
        current_app.logger.info("AI access check failed: %s", code)
        flash(error_text(code), "error")
        return _back()
    note_error(r.user_id, None)
    flash(de_en("Verbindung steht, Modell antwortet.", "Connected, the model answers."), "success")
    return _back()


@bp.route("/remove", methods=["POST"])
@login_required
def remove():
    """Deletes the stored key; also when the feature is off, so nobody keeps a key they cannot see."""
    r = row(current_user)
    if r is not None:
        db.session.delete(r)
        db.session.commit()
        flash(de_en("Eigener KI-Zugang entfernt; der gespeicherte Schlüssel wurde gelöscht. Beim Anbieter bleibt er "
                    "gültig, bis er dort widerrufen wird oder abläuft.", "Own AI access removed; the stored key was "
                    "deleted. At the provider it stays valid until it is revoked there or expires."), "success")
    return _back()


# --- the page "KI-Nutzung" ---------------------------------------------------------------------------------------

AI_PAGES = ("ainstein.", "interview_ai.", "live.assistant")  # pages that are AI as a whole


def _ai_off_page():
    """With generative AI off (E4) a page that is AI as a whole says so instead (no banner), with the way back on."""
    from flask import request

    endpoint = request.endpoint or ""
    if getattr(current_user, "is_authenticated", False) and not ai_on() and endpoint.startswith(AI_PAGES):
        return render_template("accounts/ai_off.html")
    return None


def init_app(app):
    app.before_request(_ai_off_page)


DAYS = 30  # the statistics of the page (E6)


def usage(user, days=DAYS):
    """The person's AI calls of the last `days` days per function (AiCall.user_id), most used first: label, calls,
    failed ones, over the platform / an own access, tokens, images, the share of all calls; and the totals."""
    from app import ai_stats
    from app.lang import english
    from app.models import AiCall, utcnow

    labels = ai_stats.PURPOSES_EN if english() else ai_stats.PURPOSES
    f = db.func
    # summed by the database, one row per function, outcome and access (audit 2026-10-10, K6)
    rows = db.session.execute(
        db.select(AiCall.purpose, AiCall.ok, AiCall.source, f.count(AiCall.id),
                  f.coalesce(f.sum(AiCall.input_tokens), 0) + f.coalesce(f.sum(AiCall.output_tokens), 0),
                  f.coalesce(f.sum(AiCall.images), 0))
        .where(AiCall.user_id == user.id, AiCall.at >= utcnow() - timedelta(days=days))
        .group_by(AiCall.purpose, AiCall.ok, AiCall.source)).all()
    per = {}
    for purpose, ok, source, n, tokens, images in rows:
        p = per.setdefault(purpose or "other", {"key": purpose or "other", "calls": 0, "failed": 0, "own": 0,
                                                 "platform": 0, "tokens": 0, "images": 0})
        p["calls"] += n
        p["failed"] += 0 if ok else n
        p["own" if source == "own" else "platform"] += n
        p["tokens"] += int(tokens or 0)
        p["images"] += int(images or 0)
    total = sum(p["calls"] for p in per.values())
    out = sorted(per.values(), key=lambda p: (-p["calls"], p["key"]))
    for p in out:
        p["label"] = labels.get(p["key"], labels["other"])
        p["share"] = p["calls"] / total if total else 0
    sums = {k: sum(p[k] for p in out) for k in ("calls", "failed", "own", "platform", "tokens", "images")}
    return out, sums


def today(user):
    """Calls counted today against the daily limits of Askurious (app/limits.py): (kind label, used, limit or
    None for "no limit"), for texts, images and, with an own access, the technical limit."""
    from app import limits
    from app.models import AiUsage, utcnow
    from app.timeutil import to_local

    day = to_local(utcnow()).strftime("%Y-%m-%d")
    used = dict(db.session.execute(db.select(AiUsage.kind, AiUsage.count)
                                   .filter_by(user_id=user.id, day=day)).all())
    names = {"text": de_en("KI-Texte", "AI texts"), "image": de_en("KI-Bilder", "AI images"),
             "own": de_en("Aufrufe über den eigenen Zugang", "Calls over the own access")}
    out = []
    for kind in ("text", "image", "own"):
        if kind == "own" and row(user) is None:
            continue
        limit = current_app.config.get(limits.AI_KINDS[kind][0])
        out.append((names[kind], used.get(kind, 0), None if user.is_admin or not limit else limit))
    return out


def platform_models():
    """What the platform's access uses, from the configuration (E7): what, model, where it is processed."""
    cfg = current_app.config
    out = []
    openai_where = de_en("OpenAI: Verwendet Daten seiner Schnittstelle nicht zum Training seiner Modelle, Protokolle "
                         "zur Missbrauchskontrolle bis zu 30 Tage (OpenAI, „Data controls in the OpenAI platform“)",
                         "OpenAI: does not use data sent to its API to train its models, abuse monitoring logs for up "
                         "to 30 days (OpenAI, “Data controls in the OpenAI platform”)")
    if cfg.get("OPENAI_API_KEY"):
        out.append((de_en("Texte", "Texts"), cfg.get("OPENAI_TEXT_MODEL"), openai_where))
        out.append((de_en("Bilder", "Images"), cfg.get("OPENAI_IMAGE_MODEL"), openai_where))
    if cfg.get("WHISPER_URL"):
        out.append((de_en("Transkription von Interviews", "Transcription of interviews"), "Whisper",
                    de_en("Transkriptionsserver, den der Betreiber selbst betreibt oder in seinem Auftrag betreiben "
                          "lässt; nicht an OpenAI", "a transcription server the operator runs or has run on its "
                          "behalf; not to OpenAI")))
    return out


@bp.route("/")
@login_required
def page():
    """The page "KI-Nutzung" (E1): the switch, the access in use, the own access, the statistics, the platform's
    models, what one may do with them."""
    r = row(current_user)
    calls, sums = usage(current_user)
    return render_template("accounts/ai.html", ai_access=r, ai_access_days=days_left(r), ai_mod=sys.modules[__name__],
                           calls=calls, sums=sums, days=DAYS, today=today(current_user),
                           platform=platform_models(), own_now=active(current_user))


@bp.route("/switch", methods=["POST"])
@login_required
@autosave.aware
def switch():
    """"Generative KI nutzen" (E4): saved on the click; the page draws itself anew (what shows depends on it)."""
    current_user.ai_enabled = request.form.get("ai_enabled") == "1"
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("ai_access.page"))
