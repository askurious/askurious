"""External sections of the flow ("Externer Abschnitt"): a part of the study that runs somewhere else, e.g. an
experiment in an interactive web app, in the middle of one survey.

The flow node (flow.py):
  {"t": "ext", "id": "o..", "code": "ext1", "name": "...", "url": "https://webapp.example/task?pid={{pid}}&return={{return}}",
   "mode": "button" | "auto", "text": {lang: "..."}, "values": [{"name": "score", "kind": "number" | "text"}],
   "sign": {"on": True, "key": "<encrypted>"}}   (optional: the returned values are signed by the web app)

Who gets to the node sees a short page ("Sie werden weitergeleitet …") and leaves through it: by its button,
or by itself after AUTO_SECONDS. {{return}} in the address is the return link of this participation for this
node: sealed (encrypted and authenticated, so the web app reads nothing of it, not even the pid), good for one return
within MAX_SECONDS of the first leaving, and ending in a query (…/back?t=…). The web app sends the person back to
it at the end, and may append the declared values (…&score=17); nothing else of what it appends is kept. The return resumes the participation
right behind the node, also in another tab or browser.

Signed values ("Rückgabewerte signieren", decisions.md): without a signature the values come from the participant's
browser, who can change them in the address or skip the web app. With it, the node has a random key (shown to the
admins, stored encrypted, never in a survey file or a copy) and the web app appends sig_v=2 and sig = HMAC-SHA256(key,
canonical text) in hex. Version 2 of the canonical text (code review 2026-10-07, I8) is "sig_v=2&t=<token>" and then
"&name=<value>" for each declared value the link carries, in the order of the declaration, each value percent-encoded
as RFC 3986 says (every byte of its UTF-8 except A-Z a-z 0-9 - . _ ~; Python quote(v, safe=""), PHP rawurlencode), so
no value can hold a "&name=" that moves text into another value (signed_text). Version 1 (no sig_v, the values as they
are, not encoded) was ambiguous; it is still taken until V1_UNTIL, so the web apps have time to change. Values with a
missing or wrong signature are not kept; the return itself counts (the person is not stuck), and the export says so
(<code>_sig). docs/externer-abschnitt-signatur.md is the guide for the developers of a web app.

Per participation (Response.external):
  {node id: {"k": key of the return link, "out": first left, "n": times left, "back": came back,
             "s": seconds outside (first leaving to return), "v": {name: value},
             "sig": 1 signature right, 0 missing or wrong and the values dropped (only where the node signs)},
   PARAMS: {name: value}}   (the URL parameters of the start while the person is outside, see leave)
Response.outside_since is set while the person is outside. Who never comes back stays "begonnen".

The URL parameters the person started with live in the session cookie of their browser, and the declared ones in
the mode "nur weiterleiten" nowhere else. A return in another browser (the web app opened in another one) would
come without them: the conditions on them would no longer hold and the answers they led to be dropped, and the
completion link would get them empty. So while the person is outside, the parameters that are not stored with the
participation anyway are held with it (PARAMS, never exported) and handed to the browser that comes back; they go
when the person is back, when the participation ends, and when the return link has run out (sweep_params).
"""
import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import quote

from app.conditions import ByLanguage
from app.lang import de_en

NODE = "ext"
# read in the language of the page wherever they are shown (modes(), kinds() for a plain dict); the keys are the same
MODES = ByLanguage({"button": "Seite mit Knopf", "auto": "Seite, weiter nach 2 s"},
                   {"button": "page with a button", "auto": "page, continue after 2 s"})
AUTO_SECONDS = 2
KINDS = ByLanguage({"number": "Zahl", "text": "Text"}, {"number": "number", "text": "text"})
RETURN = "return"
MAX_VALUES = 20
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,30}\Z")
MAX_SECONDS = 7 * 86400  # a return link is good for this long after the first leaving (the life of the session cookie)
SIG = "sig"  # the parameter of the return link that carries the signature of the values
SIG_VERSION = "sig_v"  # the parameter that names the version of the signed text ("2"; missing: version 1)
# Version 1 of the signed text (values not encoded, ambiguous) is taken up to and including the day before this one:
# half a year for the web apps that sign already (signing exists since 2026-10-03) to send version 2
V1_UNTIL = date(2027, 4, 1)
PARAMS = "_params"  # in Response.external: the URL parameters held while the person is outside (leave)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,400}\Z")
_EPOCH = datetime(1970, 1, 1)


def modes():
    """MODES in the language of the page."""
    return {key: MODES[key] for key in MODES}


def kinds():
    """KINDS in the language of the page."""
    return {key: KINDS[key] for key in KINDS}


def nodes(survey):
    from app import flow

    return flow.walk_nodes(survey, NODE)


def nodes_in(tree):
    """The external sections of a flow tree at hand (a copy, a file)."""
    from app import flow

    return [n for n, _, _ in flow.walk(tree) if n.get("t") == NODE]


def new(survey, root):
    """A new node, numbered on from those the flow has."""
    from app import flow

    codes = {n.get("code") for n, _, _ in flow.walk(root) if n["t"] == NODE}
    k = next(k for k in range(1, len(codes) + 2) if f"ext{k}" not in codes)
    return {"t": NODE, "id": flow._nid("o"), "code": f"ext{k}", "name": de_en("Externer Abschnitt {k}", "External section {k}", k=k), "url": "",
            "mode": "button", "text": {}, "values": []}


def values(node):
    """The declared values of a node: [{"name", "kind"}]."""
    return [v for v in (node or {}).get("values") or [] if isinstance(v, dict) and v.get("name")]


def link_names(survey):
    """The placeholders the address of an external section may use: those of a completion link (declared URL
    parameters, {{pid}}, {{lang}}, {{quota}} where allowed) and {{return}}; not the signature of completion links."""
    from app import panel

    return (set(panel.link_names(survey)) - {panel.SIG}) | {RETURN}


def url_error(survey, url):
    """Why this address cannot be stored, or None (the rules of completion links, panel.link_error)."""
    from app import panel

    if panel.SIG in panel.PLACEHOLDER.findall(url or ""):
        return de_en("{{sig}} gibt es nur in Abschlusslinks; der Rückkehrlink {{return}} ist selbst signiert.",
                     "{{sig}} exists only in completion links; the return link {{return}} is signed itself.")
    return panel.link_error(url, link_names(survey))


def usable(node):
    """The node can send people away: it has an address that may be followed."""
    from app import panel

    return bool((node or {}).get("url")) and panel.usable(node["url"].strip())


def host(node):
    from app import panel

    return panel.link_host((node or {}).get("url")) if usable(node) else ""


def hosts(survey):
    """The hosts the external sections lead to, in flow order (privacy page, consent)."""
    out = []
    for node in nodes(survey):
        if host(node) and host(node) not in out:
            out.append(host(node))
    return out


def update(survey, node, form, languages, links=True):
    """The settings of a node from its form; an error message or None. links=False: the address stays as stored
    (where participants are sent is decided by the admins of the survey, as with completion links)."""
    from app import flow

    code = form.get("code", "").strip()
    if not flow.CODE_RE.match(code):
        return de_en("Variablenname: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _.",
                     "Variable name: a letter first, then letters, digits or _.")
    if any(n is not node and n.get("code") == code for n in nodes(survey)):
        return de_en("Den Variablennamen „{code}“ hat schon ein anderer externer Abschnitt.",
                     "Another external section already has the variable name “{code}”.", code=code)
    if not links and ("url" in form and form.get("url", "").strip() != (node.get("url") or "")):
        return de_en("Die Adresse eines externen Abschnitts legt fest, wer in dieser Befragung die Rolle „Admin“ hat. "
                     "Es wurde nichts gespeichert.",
                     "The address of an external section is set by whoever has the role “Admin” in this survey. "
                     "Nothing was saved.")
    url = form.get("url", "").strip()[:1000] if links else node.get("url") or ""
    error = url_error(survey, url) if links else None
    if error:
        return de_en("Adresse: {error}", "Address: {error}", error=error)
    found, names = [], set()
    for name, kind in zip(form.getlist("value_name"), form.getlist("value_kind")):
        name = name.strip()
        if not name:
            continue
        if not NAME_RE.match(name):
            return de_en("Rückgabewert „{name}“: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _ (höchstens 31 Zeichen).",
                         "Return value “{name}”: a letter first, then letters, digits or _ (at most 31 characters).",
                         name=name[:40])
        if name.lower() == "t":
            return de_en("Rückgabewert „t“: Diesen Namen trägt im Rückkehrlink schon dessen Signatur; bitte einen anderen wählen.",
                         "Return value “t”: the signature of the return link already has this name; please choose another.")
        if name.lower() == SIG_VERSION:
            return de_en("Rückgabewert „sig_v“: Diesen Namen trägt im Rückkehrlink die Version der Signatur; bitte einen "
                         "anderen wählen.",
                         "Return value “sig_v”: the version of the signature in the return link has this name; please "
                         "choose another.")
        if name.lower() == SIG:
            return de_en("Rückgabewert „sig“: Diesen Namen trägt im Rückkehrlink die Signatur der Rückgabewerte; bitte einen "
                         "anderen wählen.",
                         "Return value “sig”: the signature of the return values in the return link has this name; please "
                         "choose another.")
        if name.lower() in names:
            return de_en("Rückgabewert „{name}“ steht doppelt in der Liste.",
                         "Return value “{name}” appears twice in the list.", name=name)
        names.add(name.lower())
        found.append({"name": name, "kind": kind if kind in KINDS else "number"})
    if len(found) > MAX_VALUES:
        return de_en("Höchstens {n} Rückgabewerte je Abschnitt.", "At most {n} return values per section.", n=MAX_VALUES)
    sign = sign_from_form(node, form) if links else node.get("sign")
    node.update(code=code, name=form.get("name", "").strip()[:100] or code, url=url,
                mode=form.get("mode") if form.get("mode") in MODES else "button",
                text={l: form.get(f"text_{l}", "").strip()[:3000] for l in languages}, values=found)
    if sign:
        node["sign"] = sign
    else:
        node.pop("sign", None)
    return None


# --- signed values -----------------------------------------------------------------------------


def sign_from_form(node, form):
    """The signing settings of a node from its form (admins only, like the address: whoever turns signing off can
    make up values). The key is drawn here, once: turning signing off and on again keeps it, so the web app need
    not change; only "Neuen Schlüssel erzeugen" (sign_renew) draws another one. A form without this part keeps what
    is stored."""
    from app import secrets_box

    sign = dict(node.get("sign") or {})
    if "sign_part" not in form:
        return sign or None
    on = bool(form.get("sign_values"))
    if on and (not sign.get("key") or form.get("sign_renew") or not secrets_box.decrypt(sign["key"], secrets_box.PARTICIPANT)):
        sign["key"] = secrets_box.encrypt(secrets.token_urlsafe(32), secrets_box.PARTICIPANT)
    if not sign.get("key"):
        return None
    sign["on"] = on
    return sign


def signs(node):
    """The node keeps returned values only with a valid signature."""
    sign = (node or {}).get("sign") or {}
    return bool(sign.get("on") and sign.get("key"))


def sign_key(node):
    """The key of the node in plain text (for the admins and the check); "" without one or when it cannot be read
    any more (the key of the server changed)."""
    from app import secrets_box

    return secrets_box.decrypt(((node or {}).get("sign") or {}).get("key"), secrets_box.PARTICIPANT)


def signed_text(node, token_value, args, version=2):
    """What the signature covers, for each declared value the link carries (also an empty one) in the order of the
    declaration, the values as they arrive (URL-decoded).
    Version 2: "sig_v=2&t=<token>&name=<value>…", token and values percent-encoded (RFC 3986, UTF-8): unambiguous.
    Version 1 (until V1_UNTIL): "t=<token>&name=value…" with the values as they are; a value holding "&b=" could move
    text into another value without changing the text."""
    def enc(value):
        return quote(str(value or ""), safe="") if version == 2 else str(value or "")

    parts = ([f"{SIG_VERSION}=2"] if version == 2 else []) + [f"t={enc(token_value)}"]
    for spec in values(node):
        if spec["name"] in args:
            parts.append(f"{spec['name']}={enc(args.get(spec['name']))}")
    return "&".join(parts)


def v1_last_day():
    """The last day version 1 of the signed text is taken, as the page writes dates."""
    from app import timeutil
    from app.lang import current

    return timeutil.fmt_local(V1_UNTIL - timedelta(days=1), lang=current())


def signed_version(args, now=None):
    """The version of the signed text the return link names: 2 with sig_v=2, 1 without sig_v until V1_UNTIL; None
    for another value or for version 1 after that day."""
    from app.models import utcnow

    given = (args.get(SIG_VERSION) or "").strip()
    if given == "2":
        return 2
    if not given and (now or utcnow()).date() < V1_UNTIL:
        return 1
    return None


def signature(key, text):
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def signature_ok(node, args, now=None):
    """The return link carries the right signature of its values (compared in constant time), in version 2 or, until
    V1_UNTIL, version 1 (signed_version). False without a key that can be read."""
    key = sign_key(node)
    given = (args.get(SIG) or "").strip().lower()
    version = signed_version(args, now)
    if not key or not given or version is None:
        return False
    expected = signature(key, signed_text(node, args.get("t") or "", args, version))
    return hmac.compare_digest(expected.encode(), given.encode("utf-8", "replace"))


# --- one participation ------------------------------------------------------------------------


def state(response):
    return dict(getattr(response, "external", None) or {})


def entry(response, nid):
    return dict(state(response).get(nid) or {})


def passed(response, nid):
    """This participation is back from the node (or cannot leave at all: the separated section)."""
    return not hasattr(response, "external") or bool(entry(response, nid).get("back"))


def leave(response, node, now, params=None):
    """The person leaves through the page of the node. The key of the return link is drawn once: leaving a second
    time (the page opened again) gives the same link, and the time outside counts from the first leaving.
    params: the URL parameters of this browser; those not stored with the participation anyway are held with it
    until the person is back (PARAMS, see the head of this module)."""
    kept = entry(response, node["id"])
    kept.setdefault("k", secrets.token_urlsafe(9))
    kept.setdefault("out", now.isoformat())
    kept["n"] = int(kept.get("n") or 0) + 1
    stored = getattr(response, "params", None) or {}
    held = {k: v for k, v in (params or {}).items() if stored.get(k) != v}
    new = {**state(response), node["id"]: kept}
    new.pop(PARAMS, None)
    response.external = {**new, PARAMS: held} if held else new
    response.outside_since = now
    return kept


def held_params(response):
    """The URL parameters held while the person is outside (leave), {} if none."""
    held = state(response).get(PARAMS)
    return dict(held) if isinstance(held, dict) else {}


def forget_params(response):
    """Drop the held URL parameters: the person is back, or the participation ended."""
    kept = state(response)
    if PARAMS in kept:
        kept.pop(PARAMS)
        response.external = kept or None


def sweep_params(now):
    """Drop the URL parameters held for participations whose return link has run out (MAX_SECONDS after leaving):
    nobody can come back with them any more. Called once a minute (lifecycle.close_due_surveys); the number of
    participations changed, not committed here.

    Only the rows that still hold parameters are read, and of them only id, external and version: who never comes
    back stays "active" with outside_since for good, and the whole rows (answers, flow, paradata) of all of them
    were read every minute (code review 2026-10-07, T8; the partial index ix_response_outside, D6)."""
    from datetime import timedelta

    from sqlalchemy import String, cast

    from app.extensions import db
    from app.models import Response

    edge = now - timedelta(seconds=MAX_SECONDS)
    changed = 0
    rows = db.session.execute(db.select(Response.id, Response.external, Response.version).filter(
        Response.outside_since.is_not(None), Response.outside_since < edge, Response.status == Response.ACTIVE,
        cast(Response.external, String).like(f'%"{PARAMS}"%'))).all()
    for rid, kept, version in rows:
        kept = dict(kept or {})
        if PARAMS not in kept:
            continue
        kept.pop(PARAMS)
        # as the session would store it: only the version read, and the version counter too, so a request of the
        # person that read the row before is turned away instead of writing the parameters back (as quotas.py does)
        changed += db.session.execute(db.update(Response).where(
            Response.id == rid, Response.version == version, Response.status == Response.ACTIVE)
            .values(external=kept or None, version=Response.version + 1)
            .execution_options(synchronize_session=False)).rowcount
    return changed


PURPOSE = "external-return"  # the box of the return links (secrets_box.seal): no other token opens there


def _out_seconds(kept):
    try:
        return int((datetime.fromisoformat(kept["out"]) - _EPOCH).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


def token(response, node):
    """What the return link carries: the participation (its pid, never the number of its row), the node, the key
    drawn for it and the time of the first leaving, sealed by the server (secrets_box.seal): the web app cannot read
    any of it, and the pid reaches it only where the address asks for {{pid}}. The same leaving gives the same link
    (the page of the node opened again)."""
    from app import secrets_box

    kept = entry(response, node["id"])
    data = [response.pid, node["id"], kept.get("k", ""), _out_seconds(kept) or 0]
    return secrets_box.seal(PURPOSE, json.dumps(data, separators=(",", ":")).encode(), secrets_box.PARTICIPANT)


def read_token(value, now=None):
    """(pid, node id, key) of a return link, or None for anything this server did not seal, and for a link whose
    first leaving is longer ago than MAX_SECONDS (a link that leaked through the log of the web app, a referrer or
    the history of a shared computer does not take the participation over for good). Links of the form before
    (signed, but readable) are not read any more."""
    from app import secrets_box
    from app.models import utcnow

    if not isinstance(value, str) or not TOKEN_RE.match(value):
        return None
    raw = secrets_box.unseal(PURPOSE, value, secrets_box.PARTICIPANT)
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        return None
    if not (isinstance(data, list) and len(data) == 4 and all(isinstance(part, str) and part for part in data[:3])
            and isinstance(data[3], int) and not isinstance(data[3], bool)):
        return None
    age = ((now or utcnow()) - _EPOCH).total_seconds() - data[3]
    if not data[3] or not -300 <= age <= MAX_SECONDS:
        return None
    return tuple(data[:3])


def return_url(survey, response, node):
    from app.hosts import participant_base

    # it ends in a query, so the web app appends its values with "&" (…&score=17)
    return f"{participant_base()}/s/{survey.public_key}/back?t={token(response, node)}"


def target(survey, response, node, all_values, return_link=None):
    """The address the person is sent to, with its placeholders filled (URL-encoded), or None. return_link (the
    preview): what stands in place of the return link."""
    from app import panel

    if not usable(node):
        return None
    link = return_link if return_link is not None else return_url(survey, response, node)
    return panel._filled(node["url"], {**all_values, RETURN: link})


def read_number(raw):
    """A number as a web app writes it (machine format), or None: a point is the decimal separator, a comma only
    where there is no point ("2,5"); no thousands separators, so a value with a point and a comma, or with several
    points or commas ("1.000.000", "1,000.5"), is none. "1.000" is 1, "-4" and "+4" are numbers, "1e3", "nan" and
    "inf" are not."""
    from app.items.base import PLAIN_NUMBER_RE

    v = str(raw).strip().replace("−", "-")
    if "." in v and "," in v or v.count(".") > 1 or v.count(",") > 1:
        return None
    v = v.replace(",", ".")
    if not PLAIN_NUMBER_RE.fullmatch(v):
        return None
    number = float(v)
    return number if math.isfinite(number) else None


def read_values(node, args, lang=None):
    """{name: value} of the declared values among what the return link carries. A number that is none is left
    out (read_number: machine format, whatever the language of the participation; `lang` is not used); a text is
    kept as one line of at most 100 characters (panel.clean_value)."""
    from app import panel

    out = {}
    for spec in values(node):
        raw = panel.clean_value(args.get(spec["name"]))
        if not raw:
            continue
        if spec.get("kind") == "text":
            out[spec["name"]] = raw
            continue
        number = read_number(raw)
        if number is not None and abs(number) < 1e15:
            out[spec["name"]] = int(number) if number == int(number) else number
    return out


def come_back(response, node, key, args, now):
    """The return link was opened: once, the participation is back, with the time outside and the declared
    values. False for a link that was used before or whose key is not the one drawn for this node. Where the node
    signs its values, they are kept only with a valid signature; the return counts either way."""
    kept = entry(response, node["id"])
    if not kept.get("k") or kept.get("back") or not hmac.compare_digest(str(kept["k"]), str(key)):
        return False
    seconds = (now - datetime.fromisoformat(kept["out"])).total_seconds() if kept.get("out") else 0
    got = read_values(node, args)
    if signs(node):
        kept["sig"] = 1 if signature_ok(node, args, now) else 0
        if not kept["sig"]:
            got = {}
    kept.update(back=now.isoformat(), s=round(min(max(seconds, 0.0), MAX_SECONDS), 1), v=got)
    response.external = {**state(response), node["id"]: kept}
    response.outside_since = None
    forget_params(response)  # handed to the browser that came back (participate.come_back reads them first)
    return True


def simulate(response, node, given, seconds, now):
    """A return without a web app: the test run (the values typed on its page) and the dummy data."""
    kept = entry(response, node["id"])
    kept.setdefault("out", now.isoformat())
    try:
        seconds = min(max(float(seconds), 0.0), MAX_SECONDS)
    except (TypeError, ValueError):
        seconds = 0.0
    kept.update(n=int(kept.get("n") or 0) or 1, back=now.isoformat(), s=round(seconds, 1),
                v=read_values(node, given))
    response.external = {**state(response), node["id"]: kept}
    response.outside_since = None


def dummy_fields(node, rng):
    """What a simulated person brings back: a number per declared number, a text per declared text."""
    return {f"xv_{spec['name']}": (f"Dummy-{rng.randint(1, 999)}" if spec.get("kind") == "text" else str(rng.randint(0, 100)))
            for spec in values(node)}


def form_values(form):
    """The values a test run typed on the page of the node (fields "xv_<name>")."""
    return {k[3:]: form.get(k) for k in form if k.startswith("xv_")}


# --- pre-launch check ----------------------------------------------------------------------------


def check(survey):
    """[(severity, message, block id or None)] for the pre-launch check."""
    from app import flow, panel

    root = flow.tree(survey)
    walked = [n for n, _, _ in flow.walk(root)]
    consent = survey.consent_item
    out, codes = [], {}
    for node in nodes(survey):
        name = de_en("Externer Abschnitt „{name}“", "External section “{name}”", name=node.get("name") or node.get("code"))
        url = (node.get("url") or "").strip()
        codes.setdefault(node.get("code"), []).append(node)
        if not url:
            out.append(("error", de_en("{name} hat noch keine Adresse; so läuft die Befragung an ihm vorbei.",
                                       "{name} has no address yet; so the survey runs past it.", name=name), None))
        else:
            error = url_error(survey, url)
            if error:
                out.append(("error", de_en("{name}, Adresse: {error}", "{name}, address: {error}", name=name, error=error), None))
            elif RETURN not in panel.PLACEHOLDER.findall(url):
                out.append(("error", de_en("{name}: Die Adresse enthält {ret} nicht. Ohne den Rückkehrlink findet "
                                           "niemand in die Befragung zurück.",
                                           "{name}: the address does not contain {ret}. Without the return link nobody "
                                           "finds their way back into the survey.", name=name, ret="{{return}}"), None))
        sign = node.get("sign") or {}
        if sign.get("on") and sign.get("key") and not sign_key(node):
            out.append(("error", de_en("{name}: Der Schlüssel für die Signatur der Rückgabewerte ist nicht mehr lesbar (der "
                                       "Schlüssel des Servers wurde geändert). Bis ein neuer erzeugt ist, werden alle "
                                       "Rückgabewerte verworfen.",
                                       "{name}: the key for the signature of the return values can no longer be read (the "
                                       "key of the server was changed). Until a new one is created, all return values are "
                                       "discarded.", name=name), None))
        elif values(node) and not signs(node):
            out.append(("warning", de_en("{name}: Die Rückgabewerte sind nicht signiert. Sie kommen aus dem Browser der "
                                         "Teilnehmenden und lassen sich dort ändern, auch ohne die Web-App. Wo sie zählen "
                                         "(eine Leistung, eine Vergütung), in den Einstellungen des Abschnitts „Rückgabewerte "
                                         "signieren“ einschalten.",
                                         "{name}: the return values are not signed. They come from the participants' "
                                         "browser and can be changed there, even without the web app. Where they count "
                                         "(a performance, a payment), switch on “Sign return values” in the settings of "
                                         "the section.", name=name), None))
        around = flow.ancestors(root, node["id"])
        if any(a["t"] == "loop" for a in around):
            out.append(("error", de_en("{name} steht in einer Schleife; das ist nicht möglich (je Durchgang bräuchte es "
                                       "einen eigenen Abschnitt). So läuft die Befragung an ihm vorbei.",
                                       "{name} is in a loop; that is not possible (each pass would need a section of its "
                                       "own). So the survey runs past it.", name=name), None))
        if consent is not None:
            at = next(n for n, other in enumerate(walked) if other is node)
            if consent.block_id not in [n["block"] for n in walked[:at] if n["t"] == "block"]:
                out.append(("error", de_en("{name} steht vor der Einwilligung: Die Teilnahme-ID ginge an die andere "
                                           "Website, bevor jemand eingewilligt hat.",
                                           "{name} is before the consent: the participation ID would go to the other "
                                           "website before anyone has consented.", name=name), consent.block_id))
    for code, same in codes.items():
        if len(same) > 1:
            out.append(("error", de_en("Der Variablenname „{code}“ gehört zu {n} externen Abschnitten; im Export "
                                       "braucht jeder seinen eigenen.",
                                       "The variable name “{code}” belongs to {n} external sections; in the export each "
                                       "needs its own.", code=code, n=len(same)), None))
    return out


# --- export and field ------------------------------------------------------------------------


def columns(survey):
    """[(column, label)] of the export: per section the seconds outside, then its declared values, then (where the
    section signs its values) whether the signature was right."""
    out = []
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        out.append((f"{node['code']}_seconds", f"Externer Abschnitt „{name}“: Zeit außerhalb in Sekunden"))
        out += [(f"{node['code']}_{spec['name']}", f"Externer Abschnitt „{name}“: Rückgabewert {spec['name']}")
                for spec in values(node)]
        if signs(node):
            out.append((f"{node['code']}_{SIG}", f"Externer Abschnitt „{name}“: Signatur der Rückgabewerte gültig (1/0)"))
    return out


def export_values(found, response):
    """{column: value} for one participation; found: nodes(survey). Empty where the person did not come back
    from the section (or never got to it), and where the web app did not hand a value back."""
    kept = getattr(response, "external", None) or {}
    out = {}
    for node in found:
        entry = kept.get(node["id"]) or {}
        back = bool(entry.get("back"))
        out[f"{node['code']}_seconds"] = entry.get("s", "") if back else ""
        for spec in values(node):
            out[f"{node['code']}_{spec['name']}"] = (entry.get("v") or {}).get(spec["name"], "") if back else ""
        if signs(node):
            # 1 right, 0 missing or wrong (the values were dropped); empty: not back, or a test run or dummy
            out[f"{node['code']}_{SIG}"] = entry["sig"] if back and entry.get("sig") in (0, 1) else ""
    return out


def codebook(survey, lang=None):
    """[(column, label, type, values, note)] for the codebook; label, notes and the describing values in the reader's
    language `lang` (None: the page's; app.items.base.reader), the type German (a key, export.TYPES_EN)."""
    from app.items.base import reader

    if reader(lang) == "en":
        return _codebook_en(survey)
    out = []
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        where = host(node) or "ohne Adresse"
        out.append((f"{node['code']}_seconds", f"Externer Abschnitt „{name}“: Zeit außerhalb in Sekunden", "Paradaten",
                    "Sekunden", f"auf dem Server gemessen: vom ersten Verlassen der Befragung bis zur Rückkehr über den "
                    f"Rückkehrlink ({where}); leer = nicht dort gewesen oder nicht zurückgekehrt"))
        signed = signs(node)
        trust = (f"signiert: nur mit gültiger Signatur der Web-App gespeichert, sonst verworfen (Spalte "
                 f"{node['code']}_{SIG})" if signed else
                 "nicht signiert: Der Wert kommt aus dem Browser der Teilnehmenden und kann dort verändert werden, "
                 "auch ohne die Web-App")
        for spec in values(node):
            out.append((f"{node['code']}_{spec['name']}", f"Externer Abschnitt „{name}“: Rückgabewert {spec['name']}",
                        "Text" if spec.get("kind") == "text" else "Externer Abschnitt",
                        "Text, höchstens 100 Zeichen" if spec.get("kind") == "text" else "Zahl",
                        f"von der Web-App ({where}) an den Rückkehrlink angehängt; {trust}; leer = nicht zurückgekehrt "
                        "oder kein Wert übergeben" + ("" if spec.get("kind") == "text" else " oder keine Zahl")
                        + (" oder Signatur ungültig" if signed else "")))
        if signed:
            out.append((f"{node['code']}_{SIG}", f"Externer Abschnitt „{name}“: Signatur der Rückgabewerte gültig",
                        "Externer Abschnitt", "1 = gültig, Rückgabewerte gespeichert; 0 = fehlt oder falsch, "
                        "Rückgabewerte verworfen",
                        "HMAC-SHA256 der Web-App über Rückkehrlink und Rückgabewerte, auf dem Server geprüft; leer = "
                        "nicht zurückgekehrt, Testdurchlauf oder Dummy-Daten"))
    return out


def _codebook_en(survey):
    """codebook() in English; the value labels of the signature column stay as they are."""
    out = []
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        where = host(node) or "no address"
        out.append((f"{node['code']}_seconds", f"External section “{name}”: time outside in seconds", "Paradaten",
                    "seconds", f"measured on the server: from first leaving the survey to the return over the return "
                    f"link ({where}); empty = not there or not returned"))
        signed = signs(node)
        trust = (f"signed: stored only with a valid signature of the web app, otherwise discarded (column "
                 f"{node['code']}_{SIG})" if signed else
                 "not signed: the value comes from the participant's browser and can be changed there, even without "
                 "the web app")
        for spec in values(node):
            out.append((f"{node['code']}_{spec['name']}", f"External section “{name}”: return value {spec['name']}",
                        "Text" if spec.get("kind") == "text" else "Externer Abschnitt",
                        "text, at most 100 characters" if spec.get("kind") == "text" else "number",
                        f"appended to the return link by the web app ({where}); {trust}; empty = not returned or no "
                        "value passed" + ("" if spec.get("kind") == "text" else " or not a number")
                        + (" or signature invalid" if signed else "")))
        if signed:
            out.append((f"{node['code']}_{SIG}", f"External section “{name}”: signature of the return values valid",
                        "Externer Abschnitt", "1 = gültig, Rückgabewerte gespeichert; 0 = fehlt oder falsch, "
                        "Rückgabewerte verworfen",
                        "HMAC-SHA256 of the web app over the return link and the return values, checked on the server; "
                        "empty = not returned, test run or dummy data"))
    return out


OUTSIDE_MINUTES = 60  # who left longer ago than this is not counted as "outside right now" any more


def outside(survey, now=None):
    """Who is in an external section: {"now": left within the last OUTSIDE_MINUTES and not back, "gone": left
    longer ago and not back (in all likelihood a drop-out; the participation stays "begonnen")}; None for a survey
    without external sections. Real participations only."""
    from datetime import timedelta

    from app.extensions import db
    from app.models import Response, utcnow

    if not nodes(survey):
        return None
    edge = (now or utcnow()) - timedelta(minutes=OUTSIDE_MINUTES)
    base = db.select(db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.status == Response.ACTIVE, Response.is_preview.is_(False),
        Response.source == "real", Response.outside_since.is_not(None))
    return {"now": db.session.scalar(base.filter(Response.outside_since >= edge)) or 0,
            "gone": db.session.scalar(base.filter(Response.outside_since < edge)) or 0, "minutes": OUTSIDE_MINUTES}
