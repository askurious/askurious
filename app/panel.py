"""Entry and exit via external systems: declared URL parameters and redirects per outcome.

Survey.panel = {"params": [{"name", "mode": "store" | "redirect", "required", "allowed": [...]}],
                "redirects": {"completed" | "screenout" | "quotafull" | "excluded" | "declined" | "closed": url with {{name}}},
                "modes": {outcome: "button" | "auto"},   (missing: redirect at once)
                "rules": [{"outcome", "item": "p:name" | "qc:<quota id>" | "q:<quota id>" | question id, "op" (flow.OPS),
                           "value", "url", "mode"}],     (the first rule of the outcome whose condition holds
                                                          decides; an empty url means: no link, the end page stays)
                "quota_placeholder": bool}               ({{quota}}: the code of the person's cell may go into links)

An end in the flow may carry a link of its own (flow.py: node["link"], node["link_mode"]); it goes before the
link of its outcome. The target of a redirect never comes from the request, only from what the admins of the
survey stored, and placeholders may only stand behind the host name (link_error, usable).

Parameter values live in the participant's session (for piping, branches, and redirects);
only parameters with mode "store" are also saved with the main response (and make the data
pseudonymous). Nothing from here is ever written into the separated section.
"""
import base64
import hashlib
import hmac
import re
from urllib.parse import quote

from flask import session

from app.conditions import ByLanguage
from app.lang import de_en, english

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}\Z")
# Response.params keys the program writes itself (live votes: live_run, live_run_name, live_extra, live_main,
# live_device, live_run_origin, …): no URL parameter may take such a name, or its value would mark a participation
# of any survey as a further word of a live vote, left out of every count (audit 2026-10-21, B2)
RESERVED_PREFIX = "live_"


def reserved(name):
    """True if `name` is kept for the keys the program stores in Response.params itself."""
    return str(name or "").lower().startswith(RESERVED_PREFIX)


def _reserved_error(name):
    return de_en("Parameternamen, die mit „{p}“ beginnen, sind für Live-Abstimmungen reserviert; bitte „{name}“ umbenennen.",
                 "Parameter names beginning with “{p}” are reserved for live votes; please rename “{name}”.",
                 p=RESERVED_PREFIX, name=name)


# "duplicate" (Ralf, 2026-10-08, Maximiles): someone who took part already, by a once-only ID or the mark of this
# browser; nothing new is stored or counted, only sent back to the panel if there is a link
OUTCOMES = {"completed": "abgeschlossen", "screenout": "Screen-out", "quotafull": "Quote voll", "excluded": "Ausschluss",
            "duplicate": "Duplikat", "declined": "Einwilligung abgelehnt", "closed": "Befragung geschlossen"}
OUTCOMES = ByLanguage(OUTCOMES, {"completed": "completed", "screenout": "screen-out", "quotafull": "quota full",
                                 "excluded": "excluded", "duplicate": "duplicate", "declined": "consent declined",
                                 "closed": "survey closed"})
# outcomes before any answer: their rules may only test URL parameters
EARLY_OUTCOMES = ("duplicate", "declined", "closed")
# what participants see before they leave: nothing (redirect at once), the end page with a button, or the end
# page that moves on by itself after AUTO_SECONDS
LINK_MODES = {"now": "sofort weiterleiten", "button": "Endseite mit Knopf",
              "auto": "Endseite, weiter nach 5 s"}
LINK_MODES_EN = {"now": "redirect at once", "button": "end page with a button", "auto": "end page, continue after 5 s"}
# read in the language of the page wherever it is shown (link_modes() for a plain dict); the keys are the same
LINK_MODES = ByLanguage(LINK_MODES, LINK_MODES_EN)
AUTO_SECONDS = 5
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
LOCAL_HOSTS = ("localhost", "127.0.0.1")
# Signed completion links: {{sig}} as the value of a parameter is an HMAC over the filled address before that
# parameter, with a key only the survey and the panel know; the panel sees that the return was not typed by hand.
SIGN_ALGS = {"sha256": "HMAC-SHA256", "sha1": "HMAC-SHA1"}
SIGN_ENCODINGS = {"hex": "hexadezimal", "base64url": "Base64 für Adressen (- und _ statt + und /, ohne =)",
                  "base64": "Base64"}
SIGN_ENCODINGS = ByLanguage(SIGN_ENCODINGS, {"hex": "hexadecimal", "base64url": "Base64 for addresses (- and _ instead of + "
                                                                              "and /, without =)", "base64": "Base64"})
SIGN_BASES = {"amp": "die Adresse bis einschließlich des & (oder ?) vor dem Signatur-Parameter",
              "plain": "die Adresse bis vor dieses & (oder ?)"}
SIGN_BASES = ByLanguage(SIGN_BASES, {"amp": "the address up to and including the & (or ?) before the signature parameter",
                                     "plain": "the address up to before this & (or ?)"})
SIG = "sig"
SIG_PARAM = re.compile(r"[?&](?P<name>[^=&#?{}\s]+)=\{\{\s*sig\s*\}\}(?=$|[&#])")
MAX_KEY = 200


def link_modes():
    """LINK_MODES as a page shows them, in the language of the page."""
    return {key: LINK_MODES[key] for key in LINK_MODES}


def outcomes():
    """OUTCOMES as a page shows them, in the language of the page."""
    return {key: OUTCOMES[key] for key in OUTCOMES}


def config(survey):
    cfg = survey.panel or {}
    return {"params": cfg.get("params") or [], "redirects": cfg.get("redirects") or {}, "multi": bool(cfg.get("multi")),
            "modes": {k: v for k, v in (cfg.get("modes") or {}).items() if v in LINK_MODES},
            "rules": cfg.get("rules") or [], "quota_placeholder": bool(cfg.get("quota_placeholder"))}


RULE_OPS = {"eq": "=", "ne": "≠", "in": "einer von"}
MAX_RULES = 30
# allowed values of one parameter: one link per student of a seminar, of a large one too (concept "Werbelinks",
# 2026-10-08; it was 50, and what went beyond was cut off without a word)
MAX_ALLOWED = 200
MAX_LABEL = 100


class _Names(set):
    """The placeholders a link may use; `signed`: {{sig}} among them is the signature of the link."""
    signed = False


def _names(params, quota_placeholder, signed=False):
    from app.items.base import BUILTIN_VARIABLES

    names = _Names({p["name"] for p in params} | set(BUILTIN_VARIABLES) | {"lang"}
                   | ({"quota"} if quota_placeholder else set()) | ({SIG} if signed else set()))
    names.signed = signed
    return names


def link_names(survey):
    """The placeholders a completion link may use: the declared URL parameters, the built-in ones ({{pid}}),
    {{lang}}, {{quota}} (the code of the person's cell) where the survey allows it, and {{sig}} (the signature
    of the link) where the survey has a signing key."""
    cfg = config(survey)
    return _names(cfg["params"], cfg["quota_placeholder"], signs(survey))


def link_chips(survey, extra=()):
    """[(placeholder, label)] to insert into a link field by a click instead of typing it (UI/UX audit 2026-10-03,
    U35): what link_names allows, in a fixed order, plus `extra` (e.g. the return link of an external section)."""
    names = link_names(survey)
    known = [("pid", de_en("Teilnahme-ID", "Participation ID")), ("lang", de_en("Sprache", "Language")),
             ("survey", de_en("Befragung", "Survey")), ("quota", de_en("Teilquote", "Sub-quota")),
             (SIG, de_en("Signatur", "Signature"))]
    out = [(f"{{{{{n}}}}}", label) for n, label in known if n in names]
    out += [(f"{{{{{p['name']}}}}}", de_en("URL-Parameter {name}", "URL parameter {name}", name=p["name"]))
            for p in config(survey)["params"]]
    return out + [(f"{{{{{n}}}}}", label) for n, label in extra]


def sign_config(survey):
    """How completion links are signed; "key" is the stored (encrypted) key, empty without one. "entry": the entry
    link must carry a signature too, in the parameter "entry_param" (audit 2026-10-18, BIZ-05)."""
    cfg = (survey.panel or {}).get("sign") or {}
    entry_param = cfg.get("entry_param") if NAME_RE.match(str(cfg.get("entry_param") or "")) else SIG
    return {"alg": cfg.get("alg") if cfg.get("alg") in SIGN_ALGS else "sha256",
            "enc": cfg.get("enc") if cfg.get("enc") in SIGN_ENCODINGS else "hex",
            "base": cfg.get("base") if cfg.get("base") in SIGN_BASES else "amp", "key": cfg.get("key") or "",
            "entry": bool(cfg.get("entry")) and bool(cfg.get("key")), "entry_param": entry_param}


def entry_signed(survey):
    """True if a link into the survey is only taken with a valid signature of the panel (BIZ-05)."""
    return sign_config(survey)["entry"]


def entry_texts(sign, query, base_url):
    """What the signature of an entry link may cover, and the signature in the link (or None): the address as it
    arrived up to the parameter of the signature (with the & or ? before it, or without, as "Signiert wird" says),
    or only the part behind the "?" up to there. query: the query string as sent (not decoded)."""
    from urllib.parse import unquote

    found = re.search(r"(?:^|&)" + re.escape(sign["entry_param"]) + r"=([^&#]*)", query or "")
    if found is None:
        return [], None
    cut = found.start() + (1 if found.group(0).startswith("&") else 0)
    head = query[:cut] if sign["base"] == "amp" else query[:cut].rstrip("&")
    full = base_url + ("?" + head if sign["base"] == "amp" or head else "")
    return [full, head], unquote(found.group(1))


def entry_ok(survey, query, base_url):
    """The entry link carries a valid signature of the panel (or none is asked for)."""
    sign = sign_config(survey)
    if not sign["entry"]:
        return True
    texts, given = entry_texts(sign, query, base_url)
    if not given:
        return False
    found = re.search(r"(?:^|&)" + re.escape(sign["entry_param"]) + r"=", query)
    declared = {p["name"] for p in config(survey)["params"]}
    if any(part.partition("=")[0] in declared for part in query[found.end():].split("&")[1:]):
        return False  # a declared parameter behind the signature would not be covered by it
    return any(hmac.compare_digest((signature(sign, text) or "-").encode(), given.encode()) for text in texts)


def signs(survey):
    return bool(sign_config(survey)["key"])


def signature(sign, text):
    """The HMAC of `text` as the settings say; "" when the stored key cannot be read (any more)."""
    from app import secrets_box

    key = secrets_box.decrypt(sign.get("key"), secrets_box.PARTICIPANT)
    if not key:
        return ""
    digest = hmac.new(key.encode(), text.encode(), getattr(hashlib, sign["alg"])).digest()
    if sign["enc"] == "base64url":
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if sign["enc"] == "base64":
        return quote(base64.b64encode(digest).decode(), safe="")
    return digest.hex()


def sign_example(survey):
    """(link, signed text) of the first signed link of the outcomes, filled with sample values, to compare with
    the panel's own check; or None."""
    sign = sign_config(survey)
    cfg = config(survey)
    if not sign["key"]:
        return None
    sample = {**{p["name"]: (p.get("allowed") or ["TEST"])[0] for p in cfg["params"]}, "pid": "TEST", "lang": "de",
              "quota": ""}
    for url in list(cfg["redirects"].values()) + [r.get("url") for r in cfg["rules"]]:
        match = SIG_PARAM.search(url or "")
        if match and usable((url or "").strip()):
            return _filled(url, sample, sign), _signed_text(url.strip(), match, sample, sign)
    return None


def extra_values(survey, response):
    """The values of {{lang}} and {{quota}} for this participation; {{quota}}: the codes of its cells, in the
    order of the quotas, joined by "-" (empty where the survey does not allow it)."""
    out = {"lang": getattr(response, "language", "") or ""}
    stored = getattr(response, "quota", None) or {}
    if stored and config(survey)["quota_placeholder"]:
        codes = []
        for quota in survey.quotas:
            entry = stored.get(str(quota.id)) or {}
            codes += [c.code for c in quota.cells if c.id == entry.get("cell")]
        out["quota"] = "-".join(codes)
    return out


def passes_cell(survey):
    """True if a completion link hands the code of the person's cell to the target ({{quota}})."""
    from app import flow

    cfg = config(survey)
    if not cfg["quota_placeholder"]:
        return False
    urls = list(cfg["redirects"].values()) + [r.get("url") for r in cfg["rules"]]
    urls += [url for n, _, _ in flow.walk(flow.tree(survey)) for url in flow.node_links(n)]
    return any("quota" in PLACEHOLDER.findall(url or "") for url in urls)


def _authority(url):
    """(scheme, the part between "//" and the path) of an address, or (None, None)."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return None, None
    return scheme.lower(), re.split(r"[/?#]", rest, maxsplit=1)[0]


def usable(url):
    """A stored link may be followed: http(s), and nothing a participant's link could turn into another host
    (no placeholder, no user name, no backslash before the path). Links stored before link_error existed are
    checked with this at the end of every participation."""
    url = url or ""
    scheme, authority = _authority(url)
    return bool(scheme in ("http", "https") and authority and not re.search(r"[{}@\s]", authority)
                and "\\" not in url and not _CONTROL.search(url))


def link_error(url, names):
    """Why this address cannot be stored as a completion link, or None. `names`: the placeholders that exist."""
    url = (url or "").strip()
    if not url:
        return None
    scheme, authority = _authority(url)
    if scheme not in ("http", "https") or not authority:
        return de_en("bitte eine vollständige Adresse mit https:// angeben.", "please give a complete address with https://.")
    if "{" in authority or "}" in authority:
        return de_en("Platzhalter dürfen erst nach dem Rechnernamen stehen (im Pfad oder hinter dem Fragezeichen); "
                     "sonst bestimmt der Teilnahmelink, wohin weitergeleitet wird.",
                     "placeholders may only come after the host name (in the path or after the question mark); "
                     "otherwise the participation link decides where the redirect goes.")
    if "@" in authority:
        return de_en("die Adresse darf keine Zugangsdaten (Name@Rechner) enthalten.",
                     "the address must not contain credentials (name@host).")
    if not usable(url) or " " in url:
        return de_en("die Adresse enthält Leerzeichen, Steuerzeichen oder einen umgekehrten Schrägstrich.",
                     "the address contains spaces, control characters or a backslash.")
    if scheme == "http" and authority.rsplit(":", 1)[0].lower() not in LOCAL_HOSTS:
        return de_en("nur https:// ist erlaubt (http:// nur für localhost zum Testen).",
                     "only https:// is allowed (http:// only for localhost for testing).")
    found = PLACEHOLDER.findall(url)
    if SIG in found and SIG not in names:
        return de_en("{{sig}} ist die Signatur des Links und braucht einen Signaturschlüssel ("
                     "Datenerhebung → Teilnahmelink → Signierte Links).",
                     "{{sig}} is the signature of the link and needs a signing key ("
                     "Data collection → Participation link → Signed links).")
    if SIG in found and getattr(names, "signed", False) and (found.count(SIG) > 1 or not SIG_PARAM.search(url)):
        return de_en("{{sig}} muss genau einmal vorkommen, als ganzer Wert eines Parameters hinter dem Fragezeichen "
                     "(z. B. …&hash={{sig}}).",
                     "{{sig}} must occur exactly once, as the whole value of a parameter after the question mark "
                     "(e.g. …&hash={{sig}}).")
    unknown = sorted(set(found) - set(names))
    if unknown:
        return ", ".join("{{" + u + "}}" for u in unknown) + de_en(
            " ist kein deklarierter Parameter; bitte als Parameter anlegen oder aus der Adresse entfernen.",
            " is not a declared parameter; please create it as a parameter or remove it from the address.")
    rest = PLACEHOLDER.sub("", url)
    if "{{" in rest or "}}" in rest:
        return de_en("ein Platzhalter ist nicht lesbar (erwartet {{name}}).", "a placeholder cannot be read ({{name}} expected).")
    return None


def link_host(url):
    """The host a link leads to (for the privacy page and the flow page), or ""."""
    _, authority = _authority((url or "").strip())
    return (authority or "").lower()


def multi(survey):
    """Several participations from one browser (lab PC, tablet handed round): the end page then offers
    "new participation on this device". Kept with the entry settings, so no new column is needed."""
    return config(survey)["multi"]


def stored_names(survey):
    return [p["name"] for p in config(survey)["params"] if p.get("mode") == "store"]


def _key(survey):
    return f"pp{survey.id}"


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_value(value):
    """A URL parameter as it is kept: one line of at most 100 characters, without control characters (texts
    show the value, and their formatting marks its places with such characters)."""
    return _CONTROL.sub("", value or "").strip()[:100]


def capture(survey, args, entry=False):
    """Read declared parameters from the entry URL. Returns (values, error message or None). entry: args are those
    of the link as it was opened (request.args); with signed entry links its signature is checked (BIZ-05)."""
    values, problems = {}, []
    if entry and entry_signed(survey):
        from flask import request

        if not entry_ok(survey, request.query_string.decode("latin-1"), request.base_url):
            problems.append(sign_config(survey)["entry_param"])
    for p in config(survey)["params"]:
        raw = clean_value(args.get(p["name"]))
        if not raw:
            if p.get("required"):
                problems.append(p["name"])
            continue
        if p.get("allowed") and raw not in p["allowed"]:
            problems.append(p["name"])
            continue
        values[p["name"]] = raw
    return values, (", ".join(problems) if problems else None)


def once_names(survey):
    """The stored parameters whose value may take part only once (a panel ID)."""
    return [p["name"] for p in config(survey)["params"] if p.get("mode") == "store" and p.get("once")]


def _holders(survey, values, name):
    """[(id, status)] of the stored real participations of this survey whose parameter `name` has the value in
    `values`: one look in the index of OnceValue (audit 2026-10-06, P5; it was a LIKE over the parameters of every
    participation)."""
    from app.extensions import db
    from app.models import OnceValue, Response, once_hash

    value = values.get(name)
    if not value or not isinstance(value, str):
        return []
    query = (db.select(Response.id, Response.status).join(OnceValue, OnceValue.response_id == Response.id)
             .filter(OnceValue.survey_id == survey.id, OnceValue.name == name, OnceValue.value_hash == once_hash(value),
                     Response.source == "real", Response.is_preview.is_(False)))
    return list(dict.fromkeys((rid, status) for rid, status in db.session.execute(query)))


TAKEOVER_MINUTES = 10  # PANEL_ONCE_TAKEOVER_MINUTES: an ID only begun with and left this long is not a duplicate


def _only_begun(response):
    """A running participation that sent no more than its first page (the consent) and has been idle for the
    takeover time: the ID it holds does not turn the next person with that ID away (BIZ-05). Participations stored
    before the count of sent pages ("sent" in the layout) existed count as further along."""
    from datetime import timedelta

    from flask import current_app, has_app_context

    from app.models import Response, utcnow

    layout = getattr(response, "layout", None)
    if getattr(response, "status", None) != Response.ACTIVE or not isinstance(layout, dict) or "sent" not in layout:
        return False
    try:
        if int(layout.get("sent") or 0) > 1:
            return False
        minutes = int(current_app.config.get("PANEL_ONCE_TAKEOVER_MINUTES", TAKEOVER_MINUTES)
                      if has_app_context() else TAKEOVER_MINUTES)
    except (TypeError, ValueError):
        return False
    last = response.last_active_at or response.started_at
    return last is not None and last <= utcnow() - timedelta(minutes=max(0, minutes))


def _blocking(survey, values, name, but=None):
    """The participations with this once-only value that turn the next one away: every one that ended, and every
    running one that went past its first page or was at it within the takeover time."""
    from app.extensions import db
    from app.models import Response

    out = []
    for rid, status in _holders(survey, values, name):
        if rid == but:
            continue
        if status == Response.ACTIVE and _only_begun(db.session.get(Response, rid)):
            continue
        out.append(rid)
    return out


def taken(survey, values, but=None):
    """The name of a once-only parameter whose value already belongs to a stored real participation of this
    survey (whatever its end; declined ones are not stored), or None. `but`: the id of a participation that does
    not count (the one that asks). A participation that only sent its first page and left does not count: someone
    who knew or guessed the ID can no longer lock the real person out with one page (audit 2026-10-18, BIZ-05);
    its ID goes to the new participation when that is stored (claim)."""
    for name in once_names(survey):
        if _blocking(survey, values, name, but):
            return name
    return None


def _release_begun(survey, response, names):
    """Take the once-only values of `response` away from participations that only began with them (_only_begun):
    they go on without the ID, so their completion link carries none (BIZ-05)."""
    from app.extensions import db
    from app.models import Response

    for name in names:
        for rid, status in _holders(survey, response.params or {}, name):
            if rid == response.id or status != Response.ACTIVE:
                continue
            other = db.session.get(Response, rid)
            if other is not None and _only_begun(other):
                other.params = {k: v for k, v in (other.params or {}).items() if k != name}
                db.session.flush()  # the mapper event removes its OnceValue row, and with it the claim


def claim(survey, response):
    """The participation `response` was just inserted (flushed): claim its once-only values, and return the name of
    one that already belongs to another participation (as taken), or None. The claim is a UNIQUE index over the
    values marked `once` (OnceValue): of two first pages sent at the same moment with the same ID the second waits
    on PostgreSQL until the first is committed and is then refused; SQLite has one writer anyway (audit 2026-10-05,
    T-11; the advisory lock it needed before went with audit 2026-10-06, P5)."""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models import OnceValue

    names = [n for n in once_names(survey) if (response.params or {}).get(n)]
    if not names:
        return None
    _release_begun(survey, response, names)
    try:
        with db.session.begin_nested():
            db.session.execute(db.update(OnceValue).where(OnceValue.response_id == response.id,
                                                          OnceValue.name.in_(names)).values(once=True))
    except IntegrityError:
        return taken(survey, response.params, but=response.id) or names[0]
    return taken(survey, response.params, but=response.id)  # also one stored before the parameter was once-only


def resumable(survey, values, pid=None):
    """Resume only with the unguessable participation ID from the signed browser session.

    A panel ID identifies a participant but is not proof of ownership (TN-1, audit 2026-10-13).
    Losing the browser session or opening the link on another device must not disclose stored answers.
    """
    from app.extensions import db
    from app.models import Response

    if not pid:
        return None
    found = set()
    for name in once_names(survey):
        if values.get(name):
            found |= set(_holders(survey, values, name))
    if len(found) != 1:
        return None
    rid, status = found.pop()
    response = db.session.get(Response, rid) if status == Response.ACTIVE else None
    return response if response is not None and response.pid == pid else None


def remember(survey, values):
    session[_key(survey)] = values


def values(survey):
    return dict(session.get(_key(survey)) or {})


def stored_values(survey, all_values):
    names = set(stored_names(survey))
    return {k: v for k, v in all_values.items() if k in names}


def _fill(text, all_values):
    return PLACEHOLDER.sub(lambda m: quote(str(all_values.get(m.group(1), "")), safe=""), text)


def _signed_text(template, match, all_values, sign):
    """What the signature covers: the filled address before the parameter that carries {{sig}}."""
    return _fill(template[:match.start() + (1 if sign["base"] == "amp" else 0)], all_values)


def _filled(template, all_values, sign=None):
    template = (template or "").strip()
    if not usable(template):
        return None
    match = SIG_PARAM.search(template) if sign and sign.get("key") else None
    if match is None:
        return _fill(template, {**all_values, SIG: ""} if sign and sign.get("key") else all_values)
    # parameters behind the signature are not covered by it and follow unchanged
    proof = NO_SIGNATURE if sign.get("unsigned") else signature(sign, _signed_text(template, match, all_values, sign))
    return (_fill(template[:match.start() + 1], all_values) + match.group("name") + "=" + proof
            + _fill(template[match.end():], all_values))


# what a test run shows in place of the signature: the signature tells the panel that a real participation ended
# here, and a preview takes any ID from its address (also over the phone link, without signing in)
NO_SIGNATURE = "(Signatur-nur-im-Feld)"


def target(survey, outcome, all_values, node=None, view=None, signed=True):
    """(address, mode) of the completion link for this end, or (None, None). signed=False (previews): the link
    without its signature. In this order: the link of the end
    in the flow (`node`; for an end "Quote voll" the link of the full quota or of the person's cell, flow.end_link);
    the first rule for the outcome whose condition holds (a rule without an
    address means: no link); the link of the outcome. view: what the rules may test ({"p:name": value} and
    quotas.view of the participation)."""
    from app import flow

    cfg = config(survey)
    sign = sign_config(survey)
    if not signed and sign:
        sign = {**sign, "unsigned": True}
    own, own_mode = flow.end_link(node, view) if node else (None, None)
    if own:
        url, mode = _filled(own, all_values, sign), own_mode
    else:
        rule = next((r for r in cfg["rules"] if r.get("outcome") == outcome and flow.holds(r, view or {}, survey)), None)
        if rule is not None and not (rule.get("url") or "").strip():
            return None, None
        if rule is not None:
            url, mode = _filled(rule["url"], all_values, sign), rule.get("mode")
        else:
            url, mode = _filled(cfg["redirects"].get(outcome), all_values, sign), cfg["modes"].get(outcome)
    if not url:
        return None, None
    return url, mode if mode in LINK_MODES else "now"


def passing_links(survey):
    """Where a completion link leaves the end page out ("sofort weiterleiten") or moves on by itself after a few
    seconds: names of those ends, for the check of the questionnaire (a deletion code on the end page goes unseen)."""
    from app import flow

    cfg, out = config(survey), []
    for outcome, url in cfg["redirects"].items():
        if (url or "").strip() and cfg["modes"].get(outcome, "now") in ("now", "auto"):
            out.append(OUTCOMES.get(outcome, outcome))
    for rule in cfg["rules"]:
        if (rule.get("url") or "").strip() and rule.get("mode", "now") in ("now", "auto"):
            out.append(de_en("Regel bei „{o}“", "rule for “{o}”", o=OUTCOMES.get(rule.get("outcome"), rule.get("outcome"))))
    for node in flow.walk_nodes(survey, "end"):
        for case in flow.cases(node, expand=True):
            if (case.get("link") or "").strip() and (case.get("link_mode") or "now") in ("now", "auto"):
                out.append(de_en("Ende im Ablauf ({w})", "end in the flow ({w})",
                                 w=flow.END_TRIGGERS.get(case.get("when"), (case.get("when"),))[0]))
    return list(dict.fromkeys(out))


def link_hosts(survey):
    """The hosts participants are sent to at an end, in a stable order (privacy page)."""
    from app import flow

    urls = list(config(survey)["redirects"].values()) + [r.get("url") for r in config(survey)["rules"]]
    urls += [url for n, _, _ in flow.walk(flow.tree(survey)) for url in flow.node_links(n)]
    hosts = []
    for url in urls:
        host = link_host(url) if usable((url or "").strip()) else ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def check(survey):
    """[(severity, message)] for the pre-launch check: stored links that would not be accepted today."""
    names = link_names(survey)
    out = []
    sign = sign_config(survey)
    if sign["key"] and not signature(sign, "x"):
        out.append(("error", de_en("Signierte Links: Der gespeicherte Signaturschlüssel ist nicht mehr lesbar (der Schlüssel "
                                   "des Servers wurde geändert). Bitte unter Datenerhebung → Teilnahmelink neu "
                                   "eintragen; bis dahin bleibt die Signatur in den Links leer.",
                                   "Signed links: the stored signing key can no longer be read (the key of the server "
                                   "was changed). Please enter it again under Data collection → Participation link; "
                                   "until then the signature in the links stays empty.")))
    for outcome, url in config(survey)["redirects"].items():
        error = link_error(url, names)
        if error:
            out.append(("error", de_en("Weiterleitung „{o}“: {error} Ändern unter Datenerhebung → Teilnahmelink.",
                                       "Redirect “{o}”: {error} Change it under Data collection → Participation link.",
                                       o=OUTCOMES.get(outcome, outcome), error=error)))
    declared = {p["name"] for p in config(survey)["params"]}
    for p in config(survey)["params"]:
        if p.get("mode") == "store" and p.get("once") and not p.get("required"):
            out.append(("warning", de_en("URL-Parameter „{name}“: Jeder Wert darf nur einmal teilnehmen, der Parameter "
                                         "ist aber keine Pflicht. Wer den Link ohne ihn öffnet, kann beliebig oft teilnehmen. "
                                         "Ändern unter Datenerhebung → Teilnahmelink.",
                                         "URL parameter “{name}”: each value may take part only once, but the parameter "
                                         "is not required. Anyone who opens the link without it can take part any number of "
                                         "times. Change it under Data collection → Participation link.", name=p["name"])))
    if not entry_signed(survey):
        for name in sorted(steering_params(survey)):
            p = next((p for p in config(survey)["params"] if p["name"] == name), None)
            if p is not None and not p.get("allowed"):
                out.append(("warning", de_en(
                    "URL-Parameter „{name}“: Quoten oder Bedingungen richten sich nach ihm, aber er hat keine Liste "
                    "erlaubter Werte und der Einstiegslink ist nicht signiert. Wer den Link hat, kann sich jeden Wert "
                    "selbst geben. Bitte die erlaubten Werte eintragen (Datenerhebung → Teilnahmelink) oder "
                    "signierte Einstiegslinks einschalten.",
                    "URL parameter “{name}”: quotas or conditions follow it, but it has no list of allowed values and "
                    "the entry link is not signed. Anyone with the link can give themselves any value. Please enter "
                    "the allowed values (Data collection → Participation link) or switch on signed entry links.",
                    name=name)))
    quotas = {str(q.id): q for q in survey.quotas}
    for n, rule in enumerate(config(survey)["rules"], start=1):
        where = de_en("Weiterleitung, Regel {n} („{o}“)", "Redirect, rule {n} (“{o}”)", n=n,
                      o=OUTCOMES.get(rule.get("outcome"), rule.get("outcome")))
        error = link_error(rule.get("url"), names)
        if error:
            out.append(("error", f"{where}: {error}"))
        item = rule.get("item")
        if isinstance(item, int) or (isinstance(item, str) and item.startswith("st:")):  # an answer
            from app import conditions, flow

            if conditions.own_item(survey, flow.source_id(item)) is None:
                out.append(("error", de_en("{where}: Die Frage der Bedingung gibt es nicht mehr.",
                                           "{where}: the question of the condition no longer exists.", where=where)))
            continue
        kind, _, key = str(item or "").partition(":")
        if kind == "p" and key not in declared:
            out.append(("error", de_en("{where}: Der URL-Parameter „{key}“ ist nicht deklariert.",
                                       "{where}: the URL parameter “{key}” is not declared.", where=where, key=key)))
        elif kind in ("q", "qc") and key not in quotas:
            out.append(("error", de_en("{where}: Die Quote der Bedingung gibt es nicht mehr.",
                                       "{where}: the quota of the condition no longer exists.", where=where)))
        elif kind == "qc":
            from app import flow

            codes = [c.code for c in quotas[key].cells]
            wanted = flow.code_list(rule.get("value")) if rule.get("op") == "in" else [str(rule.get("value", ""))]
            if [v for v in wanted if v not in codes]:
                out.append(("warning", de_en("{where}: „{value}“ ist kein Teilquoten-Code von „{quota}“; "
                                             "so trifft die Regel nie zu.",
                                             "{where}: “{value}” is not a sub-quota code of “{quota}”; so the rule never "
                                             "holds.", where=where, value=rule.get("value"), quota=quotas[key].name)))
    return out


def steering_params(survey):
    """The declared URL parameters that quotas or conditions of the flow follow ("p:name"): whoever opens the link
    picks their value (BIZ-05), so they should be signed or limited to allowed values."""
    from app import flow

    declared = {p["name"] for p in config(survey)["params"]}
    found = set()

    def visit(value):
        if isinstance(value, dict):
            item = value.get("item")
            if isinstance(item, str) and item.startswith("p:") and item[2:] in declared:
                found.add(item[2:])
            for inner in value.values():
                visit(inner)
        elif isinstance(value, list):
            for inner in value:
                visit(inner)

    visit(flow.tree(survey))
    for quota in survey.quotas:
        for cell in quota.cells:
            visit(cell.conds or [])
    return found


RULE_ROW = re.compile(r"r(\d{1,8})_outcome")


def rule_described(survey, lang=None):
    """What the condition of a rule can be put on (conditions.describe): URL parameters, the quotas' results and
    Teilquoten, and the answers given (questions, the federal state of a postal code). Not the attention checks,
    the tempo hint or the IP region: an end page does not know them."""
    from app import conditions

    return {ref: d for ref, d in conditions.describe(survey, lang, everything=True).items()
            if ref.isdigit() or ref.startswith(("p:", "q:", "qc:", "st:"))}


def _rule_rows(form, described):
    """[(number, outcome, condition or None, url, mode)] of the rule lines of the page ("r<k>_…", _condition.html)."""
    from app import conditions

    rows = sorted({int(m.group(1)) for k in form for m in [RULE_ROW.fullmatch(k)] if m})
    return [(k, form.get(f"r{k}_outcome", ""), conditions.from_form(form, f"r{k}", described),
             form.get(f"r{k}_url", "").strip()[:1000], form.get(f"r{k}_mode", "")) for k in rows]


def _rule_lines(survey, form, names):
    """(rules, error) from the lines of the page: the condition is picked like any other (conditions.from_form)."""
    described = rule_described(survey)
    rules = []
    for n, (_, outcome, cond, url, mode) in enumerate(_rule_rows(form, described), start=1):
        if cond is None:
            continue  # no condition chosen: no rule
        if outcome not in OUTCOMES:
            return None, de_en("Regel {n}: Bitte einen Ausgang wählen.", "Rule {n}: please choose an outcome.", n=n)
        if outcome in EARLY_OUTCOMES and not str(cond.get("item")).startswith("p:"):
            return None, de_en("Regel {n}: Bei „{o}“ gibt es noch keine Antworten und keine Teilquote; hier "
                               "sind nur URL-Parameter möglich.",
                               "Rule {n}: with “{o}” there are no answers and no sub-quota yet; only URL parameters are "
                               "possible here.", n=n, o=OUTCOMES[outcome])
        if cond.get("op") not in ("answered", "empty") and not str(cond.get("value", "")).strip():
            return None, de_en("Regel {n}: Bitte eine Antwort ankreuzen oder einen Wert angeben.",
                               "Rule {n}: please tick an answer or give a value.", n=n)
        error = link_error(url, names)
        if error:
            return None, de_en("Regel {n}: {error}", "Rule {n}: {error}", n=n, error=error)
        rules.append({"outcome": outcome, **cond, "url": url, "mode": mode if mode in LINK_MODES else "now"})
    if len(rules) > MAX_RULES:
        return None, de_en("Höchstens {n} Regeln.", "At most {n} rules.", n=MAX_RULES)
    return rules, None


def _rules_from_form(form, params, quota_ids, names):
    """(rules, error): the rows of the rule table; a row without a condition is left out."""
    rules = []
    rows = zip(form.getlist("rule_outcome"), form.getlist("rule_item"), form.getlist("rule_op"),
               form.getlist("rule_value"), form.getlist("rule_url"), form.getlist("rule_mode"))
    declared = {p["name"] for p in params}
    for n, (outcome, item, op, value, url, mode) in enumerate(rows, start=1):
        item, value, url = item.strip(), value.strip()[:200], url.strip()[:1000]
        if not item:
            continue
        if "|" in item:  # picked from the list: "qc:3|maenner" is the condition and its value in one
            item, _, value = item.partition("|")
            op = "eq"
        kind, _, key = item.partition(":")
        if outcome not in OUTCOMES:
            return None, de_en("Regel {n}: Bitte einen Ausgang wählen.", "Rule {n}: please choose an outcome.", n=n)
        if not ((kind == "p" and key in declared) or (kind in ("q", "qc") and key in quota_ids)):
            return None, de_en("Regel {n}: Die Bedingung nennt einen URL-Parameter oder eine Quote, die es nicht gibt.",
                               "Rule {n}: the condition names a URL parameter or a quota that does not exist.", n=n)
        if outcome in EARLY_OUTCOMES and kind != "p":
            return None, de_en("Regel {n}: Bei „{o}“ gibt es keine Teilquote; hier sind nur URL-Parameter möglich.",
                               "Rule {n}: with “{o}” there is no sub-quota; only URL parameters are possible here.",
                               n=n, o=OUTCOMES[outcome])
        if not value:
            return None, de_en("Regel {n}: Bitte einen Wert angeben.", "Rule {n}: please give a value.", n=n)
        error = link_error(url, names)
        if error:
            return None, de_en("Regel {n}: {error}", "Rule {n}: {error}", n=n, error=error)
        rules.append({"outcome": outcome, "item": item, "op": op if op in RULE_OPS else "eq", "value": value,
                      "url": url, "mode": mode if mode in LINK_MODES else "now"})
    if len(rules) > MAX_RULES:
        return None, de_en("Höchstens {n} Regeln.", "At most {n} rules.", n=MAX_RULES)
    return rules, None


def _allowed(text):
    """The allowed values of the form field: comma-separated, each once, in the order given."""
    return list(dict.fromkeys(a.strip() for a in (text or "").split(",") if a.strip()))


def update_from_form(survey, form):
    params = []
    for name, mode, required, allowed in zip(form.getlist("param_name"), form.getlist("param_mode"),
                                             form.getlist("param_required"), form.getlist("param_allowed")):
        name = name.strip()
        if not name:
            continue
        if not NAME_RE.match(name):
            return de_en("Ungültiger Parametername „{name}“ (Buchstaben, Ziffern, _).",
                         "Invalid parameter name “{name}” (letters, digits, _).", name=name)
        if reserved(name):
            return _reserved_error(name)
        allowed = _allowed(allowed)
        if len(allowed) > MAX_ALLOWED:
            return de_en("URL-Parameter „{name}“: höchstens {n} erlaubte Werte.",
                         "URL parameter “{name}”: at most {n} allowed values.", name=name, n=MAX_ALLOWED)
        params.append({"name": name, "mode": "store" if mode in ("store", "once") else "redirect",
                       "required": required == "1", "allowed": allowed,
                       **({"once": True} if mode == "once" else {})})
    if len({p["name"] for p in params}) != len(params):
        return de_en("Parameternamen müssen eindeutig sein.", "Parameter names must be unique.")
    quota_placeholder = bool(form.get("quota_placeholder"))
    sign, error = _sign_from_form(survey, form)
    if error:
        return error
    if sign["key"] and any(p["name"] == SIG for p in params):
        return de_en("Mit einem Signaturschlüssel steht {{sig}} für die Signatur; bitte den URL-Parameter „sig“ umbenennen.",
                     "With a signing key, {{sig}} stands for the signature; please rename the URL parameter “sig”.")
    names = _names(params, quota_placeholder, bool(sign["key"]))
    redirects, modes = {}, {}
    for outcome in OUTCOMES:
        url = form.get(f"redirect_{outcome}", "").strip()[:1000]
        error = link_error(url, names)
        if error:
            return de_en("Weiterleitung „{o}“: {error}", "Redirect “{o}”: {error}", o=OUTCOMES[outcome], error=error)
        redirects[outcome] = url
        if form.get(f"mode_{outcome}") in ("button", "auto"):
            modes[outcome] = form.get(f"mode_{outcome}")
    if any(RULE_ROW.fullmatch(k) for k in form):  # the page: one line per rule, its condition picked
        rules, error = _rule_lines(survey, form, names)
    else:  # the older fields "rule_item" & co. (scripts, older pages)
        rules, error = _rules_from_form(form, params, {str(q.id) for q in survey.quotas}, names)
    if error:
        return de_en("Weiterleitung, {error}", "Redirect, {error}", error=error)
    # what this form does not show stays
    survey.panel = {**(survey.panel or {}), "params": params, "redirects": redirects, "modes": modes,
                    "rules": rules, "quota_placeholder": quota_placeholder, "multi": config(survey)["multi"],  # set under "Ablauf"
                    "sign": sign}
    return None


def _sign_from_form(survey, form):
    """(sign settings, error). The key is write-only: an empty field keeps the stored one, the tick removes it."""
    from app import secrets_box

    sign = sign_config(survey)
    if "sign_alg" not in form:  # a form without this part (older page, tests of other parts): nothing changes
        return sign, None
    for field, choices in (("alg", SIGN_ALGS), ("enc", SIGN_ENCODINGS), ("base", SIGN_BASES)):
        if form.get(f"sign_{field}") in choices:
            sign[field] = form.get(f"sign_{field}")
    key = form.get("sign_key", "")
    if form.get("sign_key_remove"):
        sign["key"] = ""
    elif key:
        if len(key) > MAX_KEY or key != key.strip() or _CONTROL.search(key):
            return None, de_en("Signierte Links: Der Schlüssel darf höchstens {n} Zeichen haben, ohne Leerzeichen am "
                               "Anfang oder Ende und ohne Zeilenumbruch.",
                               "Signed links: the key may have at most {n} characters, without spaces at the start or "
                               "end and without a line break.", n=MAX_KEY)
        sign["key"] = secrets_box.encrypt(key, secrets_box.PARTICIPANT)
    sign["entry"] = bool(form.get("sign_entry")) and bool(sign["key"])
    entry_param = (form.get("sign_entry_param") or "").strip() or SIG
    if not NAME_RE.match(entry_param):
        return None, de_en("Signierte Einstiegslinks: Ungültiger Name des Signatur-Parameters „{name}“ (Buchstaben, "
                           "Ziffern, _).", "Signed entry links: invalid name of the signature parameter “{name}” "
                           "(letters, digits, _).", name=entry_param[:40])
    if sign["entry"] and any(p["name"] == entry_param for p in _as_entered(form)["params"]):
        return None, de_en("Signierte Einstiegslinks: „{name}“ ist schon ein URL-Parameter; bitte einen anderen Namen "
                           "für die Signatur wählen.", "Signed entry links: “{name}” is a URL parameter already; please "
                           "choose another name for the signature.", name=entry_param)
    sign["entry_param"] = entry_param
    return sign, None


def as_entered(form, survey=None):
    """The form's values without any check, in the shape of survey.panel: for showing a refused form again.
    The signing key is never taken from the form; the stored one stays."""
    sign = {**(sign_config(survey) if survey is not None else {}),
            **{f: form.get(f"sign_{f}") for f in ("alg", "enc", "base", "entry_param") if form.get(f"sign_{f}")}}
    if "sign_alg" in form:
        sign["entry"] = bool(form.get("sign_entry"))
    entered = _as_entered(form)
    if survey is not None and any(RULE_ROW.fullmatch(k) for k in form):  # the lines of the page, as they were picked
        entered["rules"] = [{"outcome": outcome, **cond, "url": url, "mode": mode}
                            for _, outcome, cond, url, mode in _rule_rows(form, rule_described(survey)) if cond]
    return {**entered, "sign": sign}


def _as_entered(form):
    rows = zip(form.getlist("param_name"), form.getlist("param_mode"), form.getlist("param_required"),
               form.getlist("param_allowed"))
    return {"params": [{"name": name.strip(), "mode": "store" if mode in ("store", "once") else "redirect",
                        "required": required == "1", "once": mode == "once",
                        "allowed": _allowed(allowed)}
                       for name, mode, required, allowed in rows if name.strip()],
            "redirects": {outcome: form.get(f"redirect_{outcome}", "").strip() for outcome in OUTCOMES},
            "modes": {outcome: form.get(f"mode_{outcome}") for outcome in OUTCOMES
                      if form.get(f"mode_{outcome}") in ("button", "auto")},
            "rules": [{"outcome": outcome, "item": item, "op": op, "value": value, "url": url, "mode": mode}
                      for outcome, item, op, value, url, mode in zip(
                          form.getlist("rule_outcome"), form.getlist("rule_item"), form.getlist("rule_op"),
                          form.getlist("rule_value"), form.getlist("rule_url"), form.getlist("rule_mode")) if item.strip()],
            "quota_placeholder": bool(form.get("quota_placeholder")),
            "multi": bool(form.get("multi"))}


# --- links from the panel (Ralf, 2026-10-08, Maximiles) -----------------------------------------------------
# Panels hand out their return addresses as a list ("Complete link [Target 1]: https://…?p=", …). Pasted in one go,
# each line is matched to an outcome by its words, the empty parameter at the end (or a placeholder such as [ID],
# {ID}, %ID%, <ID> or XXX) becomes {{name}}. Speeder and Geo-IP have no outcome of their own: they need an end in
# the flow with its own link, and the preview says which.

PASTE_KINDS = (  # (kind, words in the line), the first that fits
    ("duplicate", ("duplicate", "duplikat", "dupe")),
    ("quotafull", ("quota", "quote", "overquota", "full")),
    ("screenout", ("screen", "terminate", "screenout")),
    ("excluded", ("quality", "qualität", "fraud", "ausschluss")),
    ("speeder", ("speed",)),
    ("geo", ("geo",)),
    ("completed", ("complete", "target", "success", "finish", "abgeschlossen")),
)
_PASTE_URL = re.compile(r"https?://\S+")
_PASTE_PARAM = re.compile(r"([?&])([A-Za-z_][A-Za-z0-9_]*)=(\[[^\]&]*\]|\{[^}&]*\}|%[^%&]*%|<[^>&]*>|[xX]{3,}|)(?=&|$)")
MAX_PASTE = 30


def parse_panel_list(text):
    """[{"label", "url", "kind", "param"}] for the lines of a pasted list that hold an address. kind: an outcome
    (OUTCOMES), "speeder", "geo" or None; param: the name of the parameter that takes the panel's ID, if any."""
    rows = []
    for line in (text or "").splitlines()[:MAX_PASTE * 3]:
        found = _PASTE_URL.search(line)
        if not found:
            continue
        url = found.group(0).rstrip(".,;)\"'")
        label = line[:found.start()].strip().rstrip(":").strip() or url
        words = (label + " " + url).lower()
        kind = next((k for k, keys in PASTE_KINDS if any(w in words for w in keys)), None)
        param = None
        hit = None
        for hit in _PASTE_PARAM.finditer(url):
            pass
        if hit is not None:
            param = hit.group(2)
            url = url[:hit.start()] + f"{hit.group(1)}{param}={{{{{param}}}}}" + url[hit.end():]
        rows.append({"label": label[:120], "url": url[:1000], "kind": kind, "param": param})
        if len(rows) >= MAX_PASTE:
            break
    return rows


def paste_skipped(text):
    """The number of lines of a pasted list with text but no address: they are left out (UI/UX audit 2026-10-09, X55
    says so in the preview)."""
    return sum(1 for line in (text or "").splitlines()[:MAX_PASTE * 3] if line.strip() and not _PASTE_URL.search(line))


def apply_panel_list(survey, rows):
    """Take over what the pasted list gives: the ID parameters (new ones as "speichern, jeder Wert nur einmal", so a
    second participation with the same ID goes to "Duplikat") and the completion link of each outcome. Speeder and
    Geo-IP stay with the preview. Returns (outcomes set, error)."""
    cfg = config(survey)
    params = [dict(p) for p in cfg["params"]]
    for name in dict.fromkeys(r["param"] for r in rows if r["param"]):
        if not NAME_RE.match(name):
            return [], de_en("Ungültiger Parametername „{name}“.", "Invalid parameter name “{name}”.", name=name)
        if reserved(name):
            return [], _reserved_error(name)
        if name == SIG and signs(survey):
            return [], de_en("„sig“ ist mit Signaturschlüssel die Signatur; bitte den Parameter des Panels umbenennen.",
                             "With a signing key “sig” is the signature; please rename the panel's parameter.")
        if not any(p["name"] == name for p in params):
            params.append({"name": name, "mode": "store", "required": False, "allowed": [], "once": True})
    names = _names(params, cfg["quota_placeholder"], bool(signs(survey)))
    redirects = dict(cfg["redirects"])
    done = []
    for row in rows:
        if row["kind"] not in OUTCOMES or row["kind"] in done:
            continue
        error = link_error(row["url"], names)
        if error:
            return [], de_en("„{label}“: {error}", "“{label}”: {error}", label=row["label"], error=error)
        redirects[row["kind"]] = row["url"]
        done.append(row["kind"])
    survey.panel = {**(survey.panel or {}), "params": params, "redirects": redirects}
    return done, None


def entry_link(survey, base_url):
    """The participant link with the declared parameters as placeholders (for panel set-up), or None if there are
    none. A parameter of links per person (it has names, value_labels) is left out: a panel never fills it in."""
    per_person = set(value_labels(survey))
    params = [p for p in config(survey)["params"] if p["name"] not in per_person]
    if not params:
        return None
    signed = [f"{sign_config(survey)['entry_param']}=…"] if entry_signed(survey) else []
    return base_url + "?" + "&".join([f"{p['name']}=…" for p in params] + signed)


def group_links(survey, base_url):
    """One link per allowed value (e.g. per course)."""
    links = []
    for p in config(survey)["params"]:
        for value in p.get("allowed") or []:
            links.append((p["name"], value, f"{base_url}?{quote(p['name'])}={quote(value)}"))
    return links


def value_labels(survey):
    """{parameter: {value: name}}: whom a value stands for, e.g. the student a link was made for. Only for the
    people of the survey: never in a link, the data, a survey file or a copy (concept "Werbelinks", 2026-10-08)."""
    return {name: dict(labels) for name, labels in ((survey.panel or {}).get("value_labels") or {}).items()
            if isinstance(labels, dict)}


# --- names instead of codes (UI/UX audit 2026-10-09, X56, S16) ------------------------------------------------
# Wherever the person looking may see whom a link is for (from "Bearbeiten" up, surveys.sees_link_names), a link
# per person is named "Anna Schmidt (f9jpxr)" instead of "w = f9jpxr". Only at display: what is computed and kept
# (results_store) holds the codes, so the role "Ansehen" never meets a name kept for someone else.

def seen_labels(survey, name):
    """{value: name} of the parameter `name` if the person signed in may see them, else {}."""
    from flask_login import current_user

    if not getattr(current_user, "is_authenticated", False) or not survey.can(current_user, "editor"):
        return {}
    return value_labels(survey).get(name, {})


def shown_value(survey, name, value):
    """A value of a URL parameter as the choice of a provider shows it: "Anna Schmidt (f9jpxr)" for a link per
    person whose name the person may see, else "w = f9jpxr"."""
    label = seen_labels(survey, name).get(value)
    return f"{label} ({value})" if label else f"{name} = {value}"


def cap_cell_label(survey, quota, cell_name):
    """The name a cell of the upper limit per link (cap_quota, cells named after the value) shows: the person's name
    before the code if the person looking may see it, else the cell's own name."""
    code = getattr(quota, "code", "") or ""
    if not code.startswith(CAP_PREFIX):
        return cell_name
    label = seen_labels(survey, code[len(CAP_PREFIX):]).get(cell_name)
    return f"{label} ({cell_name})" if label else cell_name


def named_var(survey, var):
    """A group variable of the results (app/groupvars.py) for a parameter of links per person with the names in
    its group labels, for the person looking (a copy; the variable itself keeps the codes)."""
    import copy

    if var is None or not str(getattr(var, "id", "")).startswith("url:"):
        return var
    labels = seen_labels(survey, var.id[4:])
    if not labels:
        return var
    out = copy.copy(var)
    out.groups = [(c, f"{labels[c]} ({c})" if c in labels and label == c else label) for c, label in var.groups]
    return out


def named_vars(survey, found):
    return {key: named_var(survey, var) for key, var in found.items()}


def named_compare(survey, cmp):
    """The group comparisons (compare.compute, kept with codes) split by a parameter of links per person, with the
    names in the group labels for the person looking: a copy, the kept one stays as it is."""
    if not cmp or not str(cmp.get("by", "")).startswith("url:"):
        return cmp
    labels = seen_labels(survey, cmp["by"][4:])
    if not labels:
        return cmp

    def named(code):
        return f"{labels[code]} ({code})"

    def walk(obj, key=None):
        if isinstance(obj, dict):
            out = {k: walk(v, k) for k, v in obj.items()}
            if out.get("code") in labels and out.get("label") == out.get("code"):
                out["label"] = named(out["code"])
            return out
        if isinstance(obj, (list, tuple)):
            if len(obj) == 2 and all(isinstance(x, str) for x in obj) and obj[0] == obj[1] and obj[0] in labels:
                return type(obj)((obj[0], named(obj[0])))
            return type(obj)(walk(x, key) for x in obj)
        if key == "flat" and isinstance(obj, str) and obj in labels:  # groups that do not vary, by their label
            return named(obj)
        return obj

    return walk(cmp)


def new_values(taken, n):
    """n random codes for links, none of them in `taken`: six characters without ones that are mistaken for
    another, as the codes of surveys; never guessed from a name."""
    from app.models import random_code

    out = []
    while len(out) < n:
        code = random_code()
        if code not in taken and code not in out:
            out.append(code)
    return out


def add_values(survey, name, lines="", count=0):
    """One link each: a new random value of the stored parameter `name` (made if there is none) per line of
    `lines` (the line is its name), or `count` of them named "Link 1", "Link 2", … Returns (added, error)."""
    name = (name or "").strip()
    if not NAME_RE.match(name) or (name == SIG and signs(survey)):
        return 0, de_en("Ungültiger Parametername „{name}“ (Buchstaben, Ziffern, _).",
                        "Invalid parameter name “{name}” (letters, digits, _).", name=name)
    if reserved(name):
        return 0, _reserved_error(name)
    names = [clean_value(line)[:MAX_LABEL] for line in (lines or "").splitlines()]
    names = [n for n in names if n]
    params = [dict(p) for p in config(survey)["params"]]
    param = next((p for p in params if p["name"] == name), None)
    if param is None:
        param = {"name": name, "mode": "store", "required": False, "allowed": []}
        params.append(param)
    elif param.get("mode") != "store":
        return 0, de_en("Der URL-Parameter „{name}“ wird nicht gespeichert; Links je Person brauchen einen gespeicherten.",
                        "The URL parameter “{name}” is not stored; links per person need a stored one.", name=name)
    allowed = list(param.get("allowed") or [])
    if not names:
        names = [de_en("Link {n}", "Link {n}", n=len(allowed) + k + 1) for k in range(max(0, count or 0))]
    if not names:
        return 0, de_en("Bitte Namen (einen je Zeile) oder eine Anzahl angeben.",
                        "Please enter names (one per line) or a number.")
    if len(allowed) + len(names) > MAX_ALLOWED:
        return 0, de_en("Höchstens {n} Links je Parameter; „{name}“ hat schon {have}.",
                        "At most {n} links per parameter; “{name}” already has {have}.", n=MAX_ALLOWED, name=name,
                        have=len(allowed))
    codes = new_values(set(allowed), len(names))
    param["allowed"] = allowed + codes
    labels = value_labels(survey)
    labels[name] = {**labels.get(name, {}), **dict(zip(codes, (str(n) for n in names)))}
    survey.panel = {**(survey.panel or {}), "params": params, "value_labels": labels}
    quota = cap_quota(survey, name)
    if quota is not None:  # the new links get the same upper limit as the others
        _cap_cells(quota, name, codes, cap_of(survey, name))
    return len(codes), None


# --- upper limit per link (Ralf, 2026-10-08) ---------------------------------------------------------------
# "höchstens N je Link" is an ordinary hard quota with one cell per value of the parameter (code links_<name>):
# it depends on the URL parameter alone, so whoever opens a full link is turned away at once (quotas.full_at_entry)
# with the end "Quote voll" of the flow. The cells are named after the value, never after the person.

CAP_PREFIX = "links_"


def cap_quota(survey, name):
    """The quota that limits each link of the stored parameter `name`, or None."""
    code = f"{CAP_PREFIX}{name}"[:40]
    return next((q for q in survey.quotas if q.code == code), None)


def cap_of(survey, name):
    """The upper limit per link of the parameter `name` (completed participations), 0 for none."""
    quota = cap_quota(survey, name)
    return max((cell.target for cell in quota.cells), default=0) if quota is not None else 0


def _cap_cells(quota, name, values, target):
    from app import quotas
    from app.models import QuotaCell

    have = {c["value"] for cell in quota.cells for c in cell.conds or [] if c.get("item") == f"p:{name}"}
    taken = {cell.code for cell in quota.cells}
    for value in values:
        if value in have:
            continue
        quota.cells.append(QuotaCell(name=value, code=quotas._slug(value, taken),
                                     conds=[{"item": f"p:{name}", "op": "eq", "value": value}], target=target,
                                     closed=False, running=0, done=0, refused=0, over=0, freed=0,
                                     position=len(quota.cells)))


# the name the quota had before "Höchstzahl je Link" (UI/UX audit 2026-10-09, X56: "Obergrenze je Link (w)")
OLD_CAP_NAMES = ("Obergrenze je Link (", "Upper limit per link (")


def cap_name(survey, name):
    """The name of the quota of the upper limit, as the field on "Datenerhebung" says it: "Höchstzahl je Link", with
    the parameter only when another parameter has such a quota too."""
    others = [q for q in survey.quotas if (q.code or "").startswith(CAP_PREFIX) and q.code != f"{CAP_PREFIX}{name}"[:40]]
    return (de_en("Höchstzahl je Link ({name})", "Maximum per link ({name})", name=name) if others
            else de_en("Höchstzahl je Link", "Maximum per link"))[:200]


def set_cap(survey, name, n):
    """Limit each link of the stored parameter `name` to n completed participations: makes the quota (with its
    calculation and an end "Quote voll" in the flow; only in a draft), or sets the target of every cell; n=0 takes
    the limit away (only in a draft; in the field the targets stay, a cell can be closed on "Quoten"). Returns
    (message, error)."""
    from app import quotas
    from app.extensions import db

    param = next((p for p in config(survey)["params"] if p["name"] == name and p.get("mode") == "store"
                  and p.get("allowed")), None)
    if param is None:
        return None, de_en("Diesen gespeicherten URL-Parameter mit erlaubten Werten gibt es nicht (mehr).",
                           "There is no (longer a) stored URL parameter with allowed values of this name.")
    quota = cap_quota(survey, name)
    if n <= 0:
        if quota is None:
            return de_en("Keine Höchstzahl je Link.", "No maximum per link."), None
        if not survey.is_editable:
            return None, de_en("Während der Erhebung lässt sich die Höchstzahl nur ändern, nicht entfernen.",
                               "During fieldwork the maximum can only be changed, not removed.")
        quotas.remove(survey, quota)
        return de_en("Höchstzahl je Link entfernt (mit ihrer Quote).", "Maximum per link removed (with its quota)."), None
    if quota is None:
        if not survey.is_editable:
            return None, de_en("Eine Höchstzahl lässt sich nur vor dem Start anlegen (sie braucht eine Stelle im Ablauf).",
                               "A maximum can only be created before the start (it needs a place in the flow).")
        if len(survey.quotas) >= quotas.MAX_QUOTAS:
            return None, de_en("Höchstens {n} Quoten je Befragung.", "At most {n} quotas per survey.", n=quotas.MAX_QUOTAS)
        quota = quotas.add(survey)
        quota.code = f"{CAP_PREFIX}{name}"[:40]
        quota.name = cap_name(survey, name)
        _cap_cells(quota, name, param["allowed"], n)
        db.session.flush()
        quotas.add_to_flow(survey, quota)
        return de_en("Jeder Link nimmt jetzt höchstens {n} abgeschlossene Teilnahmen an; ist er voll, sehen weitere "
                     "Personen das Ende „Quote voll“ des Ablaufs.",
                     "Each link now takes at most {n} completed participations; when it is full, further people see "
                     "the end “Quota full” of the flow.", n=n), None
    _cap_cells(quota, name, param["allowed"], n)
    for cell in quota.cells:
        cell.target = n
    if (quota.name or "").startswith(OLD_CAP_NAMES):  # the name before X56
        quota.name = cap_name(survey, name)
    return de_en("Höchstzahl je Link: {n}.", "Maximum per link: {n}.", n=n), None


def remove_value(survey, name, value):
    """Take one link per value away: the value is no longer allowed (whoever opens the link sees "Dieser Link ist
    unvollständig"; participations through it so far count under "ohne eigenen Link"), its name goes, and an empty
    cell of the upper limit with it. Returns True if there was such a value."""
    params = [dict(p) for p in config(survey)["params"]]
    param = next((p for p in params if p["name"] == name), None)
    if param is None or value not in (param.get("allowed") or []):
        return False
    param["allowed"] = [v for v in param["allowed"] if v != value]
    labels = value_labels(survey)
    labels.get(name, {}).pop(value, None)
    survey.panel = {**(survey.panel or {}), "params": params,
                    "value_labels": {k: v for k, v in labels.items() if v}}
    quota = cap_quota(survey, name)
    if quota is not None:
        for cell in list(quota.cells):
            if any(c.get("value") == value for c in cell.conds or []) and not (cell.done or cell.running):
                quota.cells.remove(cell)
    return True


def set_value_labels(survey, form):
    """The names of the values from the table of links (fields label:<parameter>:<value>); an empty field takes
    the name away. Values that are no longer allowed lose theirs."""
    labels = {}
    for p in config(survey)["params"]:
        old = value_labels(survey).get(p["name"], {})
        own = {}
        for value in p.get("allowed") or []:
            field = f"label:{p['name']}:{value}"
            text = clean_value(form[field])[:MAX_LABEL] if field in form else old.get(value, "")
            if text:
                own[value] = text
        if own:
            labels[p["name"]] = own
    survey.panel = {**(survey.panel or {}), "value_labels": labels}


# what makes a link per value conspicuous (Ralf, 2026-10-08): a hint to look closer, never a verdict and never
# from IP addresses. MARKED_SHARE of at least MARKED_MIN completed ones marked by quality assurance; BURST completions
# within BURST_MINUTES (one person filling in again and again looks like this; a class handed the link at once can
# too); at least OUTLIER_FACTOR times the median of the links with completions (only from OUTLIER_MIN on).
MARKED_SHARE, MARKED_MIN = 0.5, 3
BURST, BURST_MINUTES = 3, 10
OUTLIER_FACTOR, OUTLIER_MIN = 3, 10


def _flags(completed, marked, median):
    """The reasons a link is conspicuous, as texts; completed: [(id, completed_at)]."""
    from datetime import timedelta

    out = []
    n = len(completed)
    hit = sum(1 for rid, _ in completed if rid in marked)
    if n and hit >= MARKED_MIN and hit / n >= MARKED_SHARE:
        out.append(de_en("{hit} von {n} abgeschlossenen von der Qualitätssicherung markiert",
                         "{hit} of {n} completed ones marked by quality assurance", hit=hit, n=n))
    times = sorted(at for _, at in completed if at is not None)
    window = timedelta(minutes=BURST_MINUTES)
    most = max((sum(1 for t in times[k:] if t - start <= window) for k, start in enumerate(times)), default=0)
    if most >= BURST:
        out.append(de_en("{n} Abschlüsse innerhalb von {m} Minuten", "{n} completions within {m} minutes", n=most,
                         m=BURST_MINUTES))
    if median and n >= OUTLIER_MIN and n >= OUTLIER_FACTOR * median:
        out.append(de_en("{n} Abschlüsse, mindestens {f}-mal so viele wie üblich (Median {m})",
                         "{n} completions, at least {f} times as many as usual (median {m})", n=n, f=OUTLIER_FACTOR,
                         m=_number(median)))
    return out


def _number(x):
    return str(int(x)) if float(x).is_integer() else (f"{x:.1f}" if english() else f"{x:.1f}".replace(".", ","))


def group_counts(survey, usable=True):
    """[{name, value, label, started, completed, usable, flags, rest}] for every allowed value of a stored parameter
    and, behind them, a row "ohne Link je Wert" (value None, rest True: without the parameter or with a value that
    is not allowed, e.g. the plain link, Ralf 2026-10-08), counted over real participations (no tests, dummies or
    previews); usable: completed and not marked by quality assurance (balance.marked, as the results leave them out);
    flags: why a link looks conspicuous (_flags, empty for the row without a link). usable and flags are None with
    usable=False: marking may read every participation, so the page "Datenerhebung" fetches them after the page
    (surveys.value_links_usable)."""
    import statistics

    from app import balance
    from app.models import Response

    from app.extensions import db

    wanted = [p for p in config(survey)["params"] if p.get("mode") == "store" and p.get("allowed")]
    if not wanted:
        return []
    # only the stored parameters, the status and the end, not the participations with their answers
    real = db.session.execute(db.select(Response.id, Response.params, Response.status, Response.completed_at).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False))).all()
    tally = {}
    done = False
    for rid, params, status, completed_at in real:
        for p in wanted:
            value = (params or {}).get(p["name"])
            if value not in p["allowed"]:
                value = None  # came without a link of this parameter (the plain link, or one from before)
            counts = tally.setdefault((p["name"], value), [0, []])
            counts[0] += 1
            if status == Response.COMPLETED:
                counts[1].append((rid, completed_at))
                done = True
    marked = balance.marked(survey) if done and usable else set()
    labels = value_labels(survey)
    out = []
    for p in wanted:
        sizes = [len(tally[(p["name"], v)][1]) for v in p["allowed"] if tally.get((p["name"], v), [0, []])[1]]
        median = statistics.median(sizes) if sizes else 0
        for value in p["allowed"] + [None]:
            started, completed = tally.get((p["name"], value), [0, []])
            out.append({"name": p["name"], "value": value, "label": labels.get(p["name"], {}).get(value, ""),
                        "started": started, "completed": len(completed), "rest": value is None,
                        "usable": sum(1 for rid, _ in completed if rid not in marked) if usable else None,
                        "flags": (_flags(completed, marked, median) if value is not None else []) if usable else None})
    return out


# --- "Links vom Panel einfügen": the pasted list waits on the server, not in the cookie --------------------------
# Up to 20 000 characters in the cookie session made it pass the 4 KB a browser keeps, and the browser dropped the
# whole session silently (code review 2026-10-15, I9). One file per person and survey, next to the upload folder
# (nothing there is ever served), gone after a day, with the account, or once taken over or cancelled.
PASTE_SECONDS = 86400


def _paste_folder():
    import os

    from flask import current_app

    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "panel_paste")


def _paste_path(user_id, survey_id):
    import os

    return os.path.join(_paste_folder(), f"{int(user_id)}-{int(survey_id)}.txt")


def keep_paste(user_id, survey_id, text):
    import os

    os.makedirs(_paste_folder(), exist_ok=True)
    sweep_pastes()
    with open(_paste_path(user_id, survey_id), "w", encoding="utf-8") as f:
        f.write(text)


def pasted(user_id, survey_id):
    """The list this person pasted for this survey and has not taken over or cancelled yet, else ""."""
    import os
    import time

    path = _paste_path(user_id, survey_id)
    try:
        if time.time() - os.path.getmtime(path) > PASTE_SECONDS:
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, ValueError):
        return ""


def forget_paste(user_id, survey_id):
    import os

    try:
        os.remove(_paste_path(user_id, survey_id))
    except OSError:
        pass


def sweep_pastes(user_id=None):
    """Remove the lists older than a day, and all of `user_id` (a deleted account); also from the housekeeping."""
    import os
    import time

    folder = _paste_folder()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if (user_id is not None and name.startswith(f"{user_id}-")) or time.time() - os.path.getmtime(path) > PASTE_SECONDS:
                os.remove(path)
        except OSError:
            pass
