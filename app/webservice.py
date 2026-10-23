"""Web Service in the flow ("Web Service", Philipp 2026-10-08):
where a participant passes the node, this server calls an address, e.g. an API, and keeps fields of the JSON it
answers as values. The person does not leave the page and sees nothing of it; the values go into later texts as
{{<code>_<name>}} (e.g. {{ws1_temp}}), into the address of a later web service, and into the export.

The flow node (flow.py):
  {"t": "ws", "id": "w..", "code": "ws1", "name": "...", "method": "GET" | "POST",
   "url": "https://api.example/x?q={{city}}", "body": "{\"q\": \"{{city}}\"}" (POST), "header": "X-Api-Key",
   "secret": "<encrypted>" (the header's value, never in a survey file or a copy),
   "values": [{"name": "temp", "path": "current.temperature_2m", "kind": "number" | "text"}]}

Placeholders in the address are percent-encoded, in the body JSON-escaped. The call happens once per
participation, when the person first passes the node (runtime.go_forward); test runs and previews call too, dummy
participations do not. Not before the participation is stored (the first "Next"), and a node that passes answers or
URL parameters on (`private`) not before the consent (code audit 2026-10-08, W5). The call itself waits until the
request has stored its step (`flush`, after the commit): no transaction, no locked quota cell and no SQLite write
lock is held while this server waits for someone else's server (W2). Per participation (Response.external["_ws"]): {node id: {"ok": 1 | 0, "v": {name: value},
"ms": duration, "err": short reason}}. A failed call (no answer within TIMEOUT, not 200, no JSON, a field missing)
lets the person go on; its values stay empty and <code>_ok is 0 in the export.

Protection, because researchers type the address and this server calls it: https only; the host must resolve to
public addresses only (no localhost, private networks, the Docker network, link-local metadata services, NAT64 and
6to4 forms of those, this server's own addresses, the networks in WEBSERVICE_BLOCKED_NETS), checked on every call,
and the connection goes to the address that was checked (no second lookup in between); port 443 only; no
redirects; at most MAX_BYTES of answer; TIMEOUT seconds for the whole call, name lookup and reading included (W1);
at most MAX_PARALLEL calls at once per process. Who sets the address, the header and its secret is decided
as for completion links and external sections: the admins of the survey. Experimental (app/experimental.py)."""
import ipaddress
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import quote, urlsplit

from app.conditions import ByLanguage
from app.lang import de_en

NODE = "ws"
KEY = "_ws"  # in Response.external: the results of the web services of this participation
METHODS = ("GET", "POST")
KINDS = ByLanguage({"number": "Zahl", "text": "Text"}, {"number": "number", "text": "text"})
TIMEOUT = 5  # seconds for the whole call: name lookup, connection, answer
MAX_BYTES = 1_000_000
MAX_PARALLEL = 8  # calls at once per process; one more gets ok=0 at once instead of waiting for a thread
HTTPS_PORT = 443  # the only port called (W4)
SECRET_RE = re.compile(r"^[\x20-\x7e]{1,500}\Z")  # what a header value may hold: visible ASCII and spaces (W10)
# never public, although Python's is_global may say so (W12): NAT64, 6to4, the old site-local IPv6 networks
# NAT64 (the first two: _inner reads the IPv4 address), 6to4, site-local, and further ranges Python counts as global:
# IPv4-compatible ::a.b.c.d (deprecated, may still reach the IPv4 address; audit 2026-10-13, IN-2), documentation
# 3fff::/20 (RFC 9637) and SRv6 SIDs 5f00::/16 (RFC 9602)
NOT_PUBLIC = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "fec0::/10",
                                                     "::/96", "3fff::/20", "5f00::/16"))
JSON_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z|true\Z|false\Z|null\Z")
MAX_VALUES = 20
MAX_TEXT = 500  # characters of a text value kept
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,30}\Z")
PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*\Z")
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,60}\Z")
FORBIDDEN_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "cookie"}


def kinds():
    return {key: KINDS[key] for key in KINDS}


def nodes(survey):
    from app import flow

    return flow.walk_nodes(survey, NODE)


def nodes_in(tree):
    from app import flow

    return [n for n, _, _ in flow.walk(tree) if n.get("t") == NODE]


def new(survey, root):
    from app import flow

    codes = {n.get("code") for n, _, _ in flow.walk(root) if n["t"] == NODE}
    k = next(k for k in range(1, len(codes) + 2) if f"ws{k}" not in codes)
    return {"t": NODE, "id": flow._nid("w"), "code": f"ws{k}", "name": de_en("Web Service {k}", "Web Service {k}", k=k),
            "method": "GET", "url": "", "body": "", "values": []}


def values(node):
    """The declared values: [{"name", "path", "kind"}]."""
    return [v for v in (node or {}).get("values") or [] if isinstance(v, dict) and v.get("name") and v.get("path")]


def host(node):
    try:
        return urlsplit((node or {}).get("url") or "").hostname or ""
    except ValueError:
        return ""


def hosts(survey):
    """The hosts the web services call, in flow order (consent, privacy page)."""
    out = []
    for node in nodes(survey):
        if host(node) and host(node) not in out:
            out.append(host(node))
    return out


def names(survey):
    """{placeholder: label} of every value a web service gives: {{ws1_temp}}."""
    return {f"{n['code']}_{v['name']}": f"{n.get('name') or n['code']}: {v['name']}" for n in nodes(survey) for v in values(n)}


def url_error(url):
    """What is wrong with an address as typed (placeholders allowed), or None."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return de_en("keine gültige Adresse.", "not a valid address.")
    if parts.scheme != "https":
        return de_en("nur https:// ist erlaubt.", "only https:// is allowed.")
    if not parts.hostname or "{{" in (parts.netloc or ""):
        return de_en("Der Server der Adresse muss feststehen (kein Platzhalter darin).",
                     "The server of the address must be fixed (no placeholder in it).")
    if parts.username or parts.password:
        return de_en("keine Zugangsdaten in der Adresse; dafür gibt es die Kopfzeile.",
                     "no credentials in the address; use the header for that.")
    try:
        port = parts.port
    except ValueError:
        return de_en("keine gültige Adresse.", "not a valid address.")
    if port not in (None, 443):
        return de_en("nur der Standard-Port von https (443) ist erlaubt.", "only the standard https port (443) is allowed.")
    return None


def _feature_on():
    """Is the experimental feature on for the person signed in (not merely in use in the survey, W3)? Outside a
    request (scripts) there is nobody to ask."""
    from flask import has_request_context

    from app import experimental

    return not has_request_context() or experimental.enabled("webservice")


def _note(message):
    from flask import flash, has_request_context

    if has_request_context():
        flash(message, "warning")


def update(survey, node, form, links=True):
    """The settings of a node from its form; an error message or None. links=False: address, body, header and its
    secret stay as stored (set by the admins of the survey)."""
    from app import flow, secrets_box

    code = form.get("code", "").strip()
    if not flow.CODE_RE.match(code):
        return de_en("Variablenname: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _.",
                     "Variable name: a letter first, then letters, digits or _.")
    taken = {n.get("code") for n in nodes(survey) if n is not node} | {n.get("code") for n in flow.walk_nodes(survey, "ext")}
    if code in taken:
        return de_en("Den Variablennamen „{code}“ hat schon ein anderer Abschnitt.",
                     "Another section already has the variable name “{code}”.", code=code)
    fixed = ("url", "body", "header", "secret", "method")
    if links and not _feature_on():
        # experimental: address, body, header and test call only for whom the feature is switched on, also in a
        # survey that has the node from a file or a copy (code audit 2026-10-08, W3)
        if any(k in form and form.get(k, "").strip() != (node.get(k) or "").strip() for k in fixed if k != "secret") \
                or form.get("secret"):
            return de_en("Adresse, Inhalt und Kopfzeile eines Web Service ändern nur Personen, für die das experimentelle "
                         "Merkmal „Web Service im Ablauf“ freigeschaltet ist. Es wurde nichts gespeichert.",
                         "Address, body and header of a web service can only be changed by people who have the experimental "
                         "feature “Web Service in the flow” switched on. Nothing was saved.")
        links = False
    if not links and any(k in form and form.get(k, "").strip() != (node.get(k) or "").strip() for k in fixed if k != "secret") \
            or not links and form.get("secret"):
        return de_en("Adresse, Inhalt und Kopfzeile eines Web Service legt fest, wer in dieser Befragung die Rolle „Admin“ "
                     "hat. Es wurde nichts gespeichert.",
                     "Address, body and header of a web service are set by whoever has the role “Admin” in this survey. "
                     "Nothing was saved.")
    found, seen = [], set()
    for name, path, kind in zip(form.getlist("value_name"), form.getlist("value_path"), form.getlist("value_kind")):
        name, path = name.strip(), path.strip()
        if not name and not path:
            continue
        if not NAME_RE.match(name):
            return de_en("Wert „{name}“: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _ (höchstens 31 Zeichen).",
                         "Value “{name}”: a letter first, then letters, digits or _ (at most 31 characters).", name=name[:40])
        if not PATH_RE.match(path):
            return de_en("Wert „{name}“: Feld in der Antwort mit Punkten, z. B. current.temperature_2m oder results.0.name.",
                         "Value “{name}”: field in the answer with dots, e.g. current.temperature_2m or results.0.name.",
                         name=name)
        if name.lower() in seen or name.lower() == "ok":
            return de_en("Wert „{name}“ steht doppelt oder heißt „ok“ (diesen Namen hat die Spalte, ob der Aufruf gelang).",
                         "Value “{name}” appears twice or is called “ok” (the column whether the call worked has it).",
                         name=name)
        seen.add(name.lower())
        found.append({"name": name, "path": path[:200], "kind": kind if kind in KINDS else "text"})
    if len(found) > MAX_VALUES:
        return de_en("Höchstens {n} Werte je Web Service.", "At most {n} values per web service.", n=MAX_VALUES)
    changes = {"code": code, "name": form.get("name", "").strip()[:100] or code, "values": found}
    if links:
        url = form.get("url", "").strip()[:1000]
        error = url_error(url)
        if error:
            return de_en("Adresse: {error}", "Address: {error}", error=error)
        method = form.get("method") if form.get("method") in METHODS else "GET"
        body = form.get("body", "").strip()[:5000] if method == "POST" else ""
        if body:
            try:
                json.loads(fill_body(body, {}))
            except ValueError:
                return de_en("Inhalt: kein gültiges JSON (Platzhalter in Anführungszeichen, z. B. \"{{city}}\").",
                             "Body: not valid JSON (placeholders in quotes, e.g. \"{{city}}\").")
        header = form.get("header", "").strip()
        if header and (not HEADER_RE.match(header) or header.lower() in FORBIDDEN_HEADERS):
            return de_en("Kopfzeile: nur Buchstaben, Ziffern und -, nicht Host, Cookie oder Content-Length.",
                         "Header: letters, digits and - only, not Host, Cookie or Content-Length.")
        secret = (form.get("secret") or "").strip()
        if secret and not SECRET_RE.match(secret):
            # http.client refuses line breaks and characters beyond Latin-1 in a header; only visible ASCII is safe
            # for every server (W10)
            return de_en("Wert der Kopfzeile: nur sichtbare ASCII-Zeichen und Leerzeichen (keine Umlaute, kein €, kein "
                         "Zeilenumbruch), höchstens 500.",
                         "Value of the header: visible ASCII characters and spaces only (no umlauts, no €, no line break), "
                         "at most 500.")
        moved = bool(node.get("secret")) and not secret and host(node) and (urlsplit(url).hostname or "") != host(node)
        changes.update(url=url, method=method, body=body, header=header)
        if secret:
            changes["secret"] = secrets_box.encrypt(secret, secrets_box.PARTICIPANT)
        if not header or form.get("secret_remove") or moved:
            # another server: the stored key was entered for the old one and is not sent to the new one (W9)
            node.pop("secret", None)
        if moved and header:
            _note(de_en("Die Adresse führt jetzt zu einem anderen Server; der gespeicherte Wert der Kopfzeile wurde deshalb "
                        "gelöscht. Bitte für den neuen Server neu eintragen.",
                        "The address now leads to another server, so the stored value of the header was deleted. Please "
                        "enter it again for the new server."))
    node.update(changes)
    return None


# --- the call ----------------------------------------------------------------------------------


def fill_url(url, variables):
    """The address with its placeholders filled in, each value percent-encoded. In the path a value "." or ".." stays
    empty: the server at the other end would read it as a step up the path, to an address the researchers did not
    give, with their key in the header (audit 2026-10-13, IN-1)."""
    from app.items.base import PLACEHOLDER

    url = url or ""
    end = min((i for i in (url.find("?"), url.find("#")) if i >= 0), default=len(url))

    def value(m):
        text = quote(_text(variables, m.group(1), m.group(2)), safe="")
        return "" if m.start() < end and text in (".", "..") else text

    return PLACEHOLDER.sub(value, url)


def _in_string(body):
    """For each position of the JSON body: inside a string literal?"""
    inside, escaped, out = False, False, []
    for ch in body:
        out.append(inside)
        if escaped:
            escaped = False
        elif inside and ch == "\\":
            escaped = True
        elif ch == '"':
            inside = not inside
    return out


def fill_body(body, variables):
    """The JSON body with its placeholders filled in: inside quotes each value escaped for a JSON string; a placeholder
    outside quotes ({"n": {{count|1}}}) takes a number, true, false or null as it is, anything else as a JSON string
    and nothing as null, so that no value can put a list or an object of its own into the body (audit 2026-10-13,
    IN-3)."""
    from app.items.base import PLACEHOLDER

    body = body or ""
    inside = _in_string(body)

    def value(m):
        text = _text(variables, m.group(1), m.group(2))
        if inside[m.start()]:
            return json.dumps(text)[1:-1]
        text = text.strip()
        return text if JSON_SCALAR.match(text) else json.dumps(text) if text else "null"

    return PLACEHOLDER.sub(value, body)


def _text(variables, name, fallback):
    value = (variables or {}).get(name)
    text = "" if value is None else str(value)
    return text if text.strip() else (fallback or "").strip()


class Blocked(Exception):
    """The address leads somewhere this server must not call."""


class Unreachable(OSError):
    """No answer came: unknown host, no connection, no TLS, time over before the answer began. The test call says
    only this, whichever it was, so that it tells nothing about the ports and services of a network (W4)."""


_slots = threading.BoundedSemaphore(MAX_PARALLEL)
_lookups = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webservice-dns")
_own = {"key": None, "at": 0.0, "ips": frozenset()}
OWN_SECONDS = 600


def _resolve(hostname, port, timeout):
    """socket.getaddrinfo with a time limit (it has none of its own): in a thread of its own."""
    future = _lookups.submit(lambda: socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP))
    try:
        return future.result(timeout=max(timeout, 0.05))
    except FutureTimeout as e:
        future.cancel()
        raise Unreachable("not reachable") from e
    except (socket.gaierror, UnicodeError) as e:
        raise Unreachable(f"unknown host {hostname}") from e


def _config():
    from flask import current_app, has_app_context

    return current_app.config if has_app_context() else {}


def own_hosts():
    """The host names of this installation (PUBLIC_BASE_URL, PARTICIPANT_BASE_URL, STATUS_URL, REDIRECT_HOSTS) and
    of each further brand (BRANDS, SITE_BRAND: its domain, its participants' and its status name; audit 2026-10-09b,
    M3): never called."""
    from app.brand import parse, rehost
    from config import url_host

    config = _config()
    main = url_host(config.get("PUBLIC_BASE_URL") or "")
    own = {main, url_host(config.get("PARTICIPANT_BASE_URL") or ""), url_host(config.get("STATUS_URL") or "")}
    own.discard("")
    names = set(own) | ({"status." + main} if main else set())
    for domain, _name in parse(config.get("BRANDS")) + parse(config.get("SITE_BRAND")):
        names |= {domain, "status." + domain} | {rehost(h, domain, main) for h in own}
    names |= {h.lower() for h in config.get("REDIRECT_HOSTS") or ()}
    return {n.lower().rstrip(".") for n in names if n}


def _own_ips(timeout):
    """The addresses this installation's names resolve to, looked up again after OWN_SECONDS."""
    names = tuple(sorted(own_hosts()))
    now = time.monotonic()
    if _own["key"] == names and now - _own["at"] < OWN_SECONDS:
        return _own["ips"]
    ips = set()
    for name in names:
        try:
            ips |= {_ip(found[4][0]) for found in _resolve(name, HTTPS_PORT, min(timeout, 2))}
        except (OSError, ValueError):
            continue  # unknown here: nothing to block for it
    _own.update(key=names, at=now, ips=frozenset(ips))
    return _own["ips"]


def blocked_nets():
    """The operator's protected networks (config.PROTECTED_NETS, read once at the start) plus the installation's further
    blocked ones, including for typed AI endpoints."""
    from config import PROTECTED_NETS

    out = []
    for text in (*PROTECTED_NETS, *(_config().get("WEBSERVICE_BLOCKED_NETS") or ())):
        try:
            network = ipaddress.ip_network(text, strict=False)
            if network not in out:
                out.append(network)
        except ValueError:
            continue
    return out


def _ip(text):
    return ipaddress.ip_address(str(text).split("%")[0])


def _inner(ip):
    """The IPv4 address an IPv6 address carries (IPv4-mapped, 6to4, Teredo, NAT64), or None."""
    if ip.version != 6:
        return None
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    if ip in NOT_PUBLIC[0] or ip in NOT_PUBLIC[1]:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def public(ip, own=frozenset(), nets=()):
    """May this server call `ip`: public, not one of its own, in no blocked network (also as the IPv4 address it
    carries), and in none of the networks of NOT_PUBLIC."""
    for one in (ip, _inner(ip)):
        if one is None:
            continue
        if not one.is_global or one.is_multicast or one in own or any(one.version == n.version and one in n for n in nets):
            return False
    return not any(ip.version == n.version and ip in n for n in NOT_PUBLIC)


def _public_address(hostname, port, timeout=TIMEOUT):
    """One address of the host, after checking that every address it resolves to may be called."""
    started = time.monotonic()
    found = _resolve(hostname, port, timeout)
    own, nets = _own_ips(timeout - (time.monotonic() - started)), blocked_nets()
    addresses = []
    for family, _, _, _, sockaddr in found:
        if not public(_ip(sockaddr[0]), own, nets):
            raise Blocked(f"{hostname} resolves to a non-public address")
        addresses.append(sockaddr[0])
    if not addresses:
        raise Unreachable(f"unknown host {hostname}")
    return addresses[0]


def fetch(method, url, body=None, headers=None):
    """(status, bytes) of one call to `url`, to the address checked for it, all within TIMEOUT seconds; raises
    Blocked, Unreachable (no answer began), OSError (the answer broke off, was too large or too slow, too many calls
    at once) or ValueError (a header value http.client refuses)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise Blocked("https only")
    try:
        port = parts.port or 443
    except ValueError as e:
        raise Blocked("not a valid port") from e
    if port != 443:
        raise Blocked("port 443 only")
    if parts.hostname.lower().rstrip(".") in own_hosts():
        raise Blocked("this server's own address")
    if not _slots.acquire(blocking=False):
        raise OSError("busy: too many calls at once")
    try:
        deadline = time.monotonic() + TIMEOUT
        address = _public_address(parts.hostname, port, TIMEOUT)
        return _talk(method, parts, address, body, headers, deadline)
    finally:
        _slots.release()


def _talk(method, parts, address, body, headers, deadline):
    """The call itself, to `address`. A watchdog closes the connection at `deadline`: urllib3's timeouts hold for
    each single read only, and a server that sends a byte every few seconds kept the call open for hours (W1)."""
    import http.client

    import certifi
    import urllib3
    from urllib3.connection import HTTPSConnection

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unreachable("not reachable")
    conn = HTTPSConnection(address, HTTPS_PORT, timeout=remaining, server_hostname=parts.hostname,
                           assert_hostname=parts.hostname, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())
    over = threading.Event()

    def stop():
        over.set()
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watchdog = threading.Timer(remaining, stop)
    watchdog.daemon = True
    watchdog.start()
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    sent = {"Host": parts.netloc.rsplit("@", 1)[-1], "Accept": "application/json", "User-Agent": "Askurious-WebService/1",
            **(headers or {})}
    if body is not None:
        sent["Content-Type"] = "application/json"
    try:
        try:
            conn.request(method, path, body=body.encode() if body is not None else None, headers=sent, preload_content=False)
            answer = conn.getresponse()
        except ValueError:
            raise  # a header value http.client refuses (W10)
        except (urllib3.exceptions.HTTPError, http.client.HTTPException, OSError) as e:
            raise Unreachable("not reachable") from e
        data = bytearray()
        try:
            while True:
                chunk = answer.read1(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_BYTES:
                    raise OSError("answer too large")
                if over.is_set() or time.monotonic() > deadline:
                    raise OSError("timed out")
        except (urllib3.exceptions.HTTPError, http.client.HTTPException) as e:
            raise OSError("timed out" if over.is_set() else "answer broken off") from e
        except OSError as e:
            if str(e) in ("answer too large", "timed out"):
                raise
            raise OSError("timed out" if over.is_set() else "answer broken off") from e
        if over.is_set():
            raise OSError("timed out")  # closed by the watchdog: what came is not the whole answer
        return answer.status, bytes(data)
    finally:
        watchdog.cancel()
        conn.close()


def pick(data, path):
    """The value at "a.b.0.c" in parsed JSON, or None."""
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def _as(value, kind):
    if value is None or isinstance(value, (dict, list)):
        return ""
    if kind == "number":
        if isinstance(value, bool):
            return int(value)
        try:
            number = float(str(value).strip().replace(",", "."))
        except ValueError:
            return ""
        return int(number) if number.is_integer() and abs(number) < 1e15 else round(number, 6)
    from app.request_values import clean_text

    # stored as an answer: without control characters and lone surrogates ("\ud800" in the service's JSON), which
    # no export could write (audit 2026-10-13, TN-20)
    return clean_text(str(value))[:MAX_TEXT]


def call(node, variables):
    """{"ok", "v", "ms", "err"} of one call of the node with the values of the participation."""
    from app import secrets_box

    start = time.monotonic()
    headers = {}
    if node.get("header") and node.get("secret"):
        headers[node["header"]] = secrets_box.decrypt(node["secret"], secrets_box.PARTICIPANT) or ""
    try:
        url = fill_url(node.get("url"), variables)
        body = fill_body(node["body"], variables) if node.get("method") == "POST" and node.get("body") else None
        status, data = fetch(node.get("method") or "GET", url, body, headers)
        if status != 200:
            raise OSError(f"HTTP {status}")
        parsed = json.loads(data.decode("utf-8"))
        got = {v["name"]: _as(pick(parsed, v["path"]), v["kind"]) for v in values(node)}
        missing = [n for n, v in got.items() if v == ""]
        result = {"ok": 0 if missing else 1, "v": got, "err": f"missing: {', '.join(missing)}"[:200] if missing else ""}
    except Blocked as e:
        result = {"ok": 0, "v": {}, "err": f"blocked: {e}"[:200]}
    except (OSError, ValueError, UnicodeDecodeError) as e:
        result = {"ok": 0, "v": {}, "err": str(e)[:200] or type(e).__name__}
    result["ms"] = round((time.monotonic() - start) * 1000)
    return result


def test(node, variables):
    """One call from the editor ("Testaufruf") with example values: what was called, the status and size of the
    answer, the values taken and the start of the answer, to find the paths of its fields.

    Only what a JSON answer with status 200 says is shown. Every call that brought no answer is "not reachable",
    whatever the reason: so the test call shows no error page of someone's server and does not tell open from closed
    ports or servers that are there from those that are not (code audit 2026-10-08, W4)."""
    from app import secrets_box

    headers = {node["header"]: secrets_box.decrypt(node["secret"], secrets_box.PARTICIPANT) or ""} if node.get("header") and node.get("secret") else {}
    url = fill_url(node.get("url"), variables)
    out = {"url": url, "status": None, "size": None, "v": {}, "err": "", "snippet": ""}
    start = time.monotonic()
    try:
        body = fill_body(node["body"], variables) if node.get("method") == "POST" and node.get("body") else None
        status, data = fetch(node.get("method") or "GET", url, body, headers)
        out["status"], out["size"] = status, len(data)
        if status == 200:
            try:
                parsed = json.loads(data.decode("utf-8"))
                out["snippet"] = json.dumps(parsed, ensure_ascii=False, indent=1)[:SNIPPET]
                out["v"] = {v["name"]: _as(pick(parsed, v["path"]), v["kind"]) for v in values(node)}
            except ValueError:  # also UnicodeDecodeError
                out["err"] = de_en("keine JSON-Antwort", "not a JSON answer")
    except Blocked:
        out["err"] = de_en("nicht erlaubt: Dieser Server liegt in einem gesperrten Netz oder ist Askurious selbst.",
                           "not allowed: this server is in a blocked network or is Askurious itself.")
    except ValueError:
        out["err"] = de_en("Der Wert der Kopfzeile enthält Zeichen, die in einer Kopfzeile nicht gehen; bitte neu eintragen.",
                           "The value of the header holds characters a header cannot carry; please enter it again.")
    except Unreachable:
        out["err"] = de_en("nicht erreichbar", "not reachable")
    except OSError as e:
        reason = str(e)
        out["err"] = (de_en("Antwort größer als 1 MB", "answer larger than 1 MB") if reason == "answer too large"
                      else de_en("gerade zu viele Aufrufe gleichzeitig; bitte gleich noch einmal", "too many calls at once "
                                 "right now; please try again in a moment") if reason.startswith("busy")
                      else de_en("nicht erreichbar", "not reachable"))
    out["ms"] = round((time.monotonic() - start) * 1000)
    return out


SNIPPET = 600  # characters of the answer the test call shows
KEEP_BYTES = 1000  # at most this much of a test call goes into the session cookie (W8)


def compact(result):
    """The result of a test call small enough for the session cookie (a browser drops a cookie over 4 KB, and the
    test call then showed nothing, W8): address, example values and values cut short, then as much of the answer
    as still fits into KEEP_BYTES."""
    out = {**result, "url": (result.get("url") or "")[:300], "snippet": "", "cut": False}
    out["given"] = {k[:40]: str(v)[:60] for k, v in list((result.get("given") or {}).items())[:10]}
    out["v"] = {k: v if not isinstance(v, str) else v[:60] for k, v in list((result.get("v") or {}).items())[:MAX_VALUES]}

    def size(x):
        return len(json.dumps(x))  # escaped to ASCII: at least as long as what the cookie holds

    while size(out) > KEEP_BYTES and out["v"]:
        out["v"], out["cut"] = dict(list(out["v"].items())[:-1]), True
    snippet = result.get("snippet") or ""
    part = snippet[:max(KEEP_BYTES - size(out), 0)]
    while part and size({**out, "snippet": part}) > KEEP_BYTES:
        part = part[:max(0, len(part) - max(20, size({**out, "snippet": part}) - KEEP_BYTES))]
    out["snippet"], out["cut"] = part, out["cut"] or len(part) < len(snippet)
    return out


def used(node):
    """The placeholders the address and the body use, in order."""
    from app.items.base import PLACEHOLDER

    seen = []
    for m in PLACEHOLDER.finditer((node.get("url") or "") + (node.get("body") or "")):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def chips(survey, node=None):
    """[(placeholder, label)] to insert into the address by a click: the participation, URL parameters, answers to
    questions, values of the web services before this one."""
    from app import panel, piping

    out = [("{{pid}}", de_en("Teilnahme-ID", "Participation ID")), ("{{lang}}", de_en("Sprache", "Language"))]
    out += [(f"{{{{{p['name']}}}}}", de_en("URL-Parameter {name}", "URL parameter {name}", name=p["name"]))
            for p in panel.config(survey)["params"]]
    out += [(f"{{{{{code}}}}}", de_en("Antwort {code}", "Answer {code}", code=code)) for code in sorted(piping.names(survey))]
    for other in nodes(survey):
        if node is not None and other["id"] == node["id"]:
            break
        out += [(f"{{{{{other['code']}_{v['name']}}}}}", f"{other.get('name') or other['code']}: {v['name']}")
                for v in values(other)]
    return out


def usable(node):
    return bool((node or {}).get("url")) and url_error(node["url"]) is None


def results(response):
    return ((getattr(response, "external", None) or {}).get(KEY)) or {}


def done(response, nid):
    return str(nid) in results(response)


def private(node):
    """Does the call pass on anything of the participation beyond {{pid}}, {{lang}} and {{survey}}: answers, URL
    parameters (e.g. {{PROLIFIC_PID}}), values of other web services? Then it waits for the consent (W5)."""
    from app.items.base import BUILTIN_VARIABLES

    return bool(set(used(node)) - BUILTIN_VARIABLES)


QUEUE = "_ws_queue"  # on the Response object, in memory only: the calls this request makes after its commit


def run(survey, response, node, variables):
    """Call the node for this participation, once; dummy participations keep empty values.

    In a request the call waits until the request has stored its step: `flush` makes it after the commit, so that no
    transaction (a quota cell taken, SQLite's write lock) stays open while this server waits for someone else's
    (code audit 2026-10-08, W2). The values it gives appear on the next page (every step forward ends in a redirect)."""
    from flask import after_this_request, has_request_context

    if done(response, node["id"]) or not usable(node):
        return
    if getattr(response, "source", "") == "dummy" or not has_request_context():
        result = {"ok": 0, "v": {}, "err": "dummy", "ms": 0} if getattr(response, "source", "") == "dummy"             else call(node, variables)
        _keep(response, {str(node["id"]): result})
        return
    queue = response.__dict__.get(QUEUE)
    if queue is None:
        queue = response.__dict__[QUEUE] = []

        @after_this_request
        def later(answer):
            try:
                flush(response)
            except Exception:  # a failed call must never cost the person the page
                from flask import current_app

                from app.extensions import db

                current_app.logger.exception("web service call after the request")
                db.session.rollback()
            return answer

    if any(entry["id"] == str(node["id"]) for entry in queue):
        return
    # what goes in, read now: after the commit the participation is not read again before the call
    wanted = {name: (None if variables.get(name) is None else str(variables.get(name))) for name in used(node)}
    queue.append({"id": str(node["id"]), "node": json.loads(json.dumps(node)), "vars": wanted})


def _keep(response, found):
    kept = dict(getattr(response, "external", None) or {})
    kept[KEY] = {**(kept.get(KEY) or {}), **found}
    response.external = kept


def flush(response):
    """Make the calls `run` queued for `response`, after the request stored its step, and store their results in a
    short transaction of their own, by the version read (as external.sweep_params): no transaction is open while the
    calls wait. A value of an earlier web service of the same queue goes into the address of a later one."""
    from sqlalchemy import inspect

    from app.extensions import db
    from app.models import Response

    queue = response.__dict__.pop(QUEUE, None)
    identity = inspect(response).identity
    db.session.rollback()  # the request committed what it keeps; nothing may stay open during the calls
    if not queue or not identity:
        return
    found, given = {}, {}
    for entry in queue:
        result = call(entry["node"], {**entry["vars"], **given})
        found[entry["id"]] = result
        given.update({f"{entry['node'].get('code')}_{v['name']}": (result.get("v") or {}).get(v["name"], "")
                      for v in values(entry["node"])})
    for _ in range(3):
        row = db.session.execute(db.select(Response.external, Response.version).where(Response.id == identity[0])).first()
        if row is None:
            break  # declined meanwhile, or never stored
        kept = dict(row.external or {})
        new = {nid: result for nid, result in found.items() if nid not in (kept.get(KEY) or {})}
        if not new:
            break
        kept[KEY] = {**(kept.get(KEY) or {}), **new}
        changed = db.session.execute(db.update(Response).where(Response.id == identity[0], Response.version == row.version)
                                     .values(external=kept, version=Response.version + 1)
                                     .execution_options(synchronize_session=False)).rowcount
        db.session.commit()
        if changed:
            # the values are part of the case's hash (app/seal.py, L-15): a case that ended in the request that made
            # the call gets an entry for them, as a change through the session would
            from app import seal

            if seal.amend_written(identity[0], "webservice"):
                db.session.commit()
            break


def variables_of(survey, response):
    """{placeholder: value} of what the web services gave this participation: {"ws1_temp": 13.8}."""
    got = results(response)
    out = {}
    for node in nodes(survey):
        entry = got.get(str(node["id"])) or {}
        for v in values(node):
            value = (entry.get("v") or {}).get(v["name"], "")
            out[f"{node['code']}_{v['name']}"] = value
    return out


# --- export --------------------------------------------------------------------------------------


def columns(survey):
    out = []
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        out.append((f"{node['code']}_ok", f"Web Service „{name}“: Aufruf gelungen (1/0)"))
        out += [(f"{node['code']}_{v['name']}", f"Web Service „{name}“: {v['name']} ({v['path']})") for v in values(node)]
    return out


def export_values(found, response):
    got = results(response)
    out = {}
    for node in found:
        entry = got.get(str(node["id"]))
        out[f"{node['code']}_ok"] = "" if entry is None else entry.get("ok", 0)
        for v in values(node):
            out[f"{node['code']}_{v['name']}"] = "" if entry is None else (entry.get("v") or {}).get(v["name"], "")
    return out


def codebook(survey, lang=None):
    from app.items.base import reader

    english = reader(lang) == "en"
    out = []
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        where = host(node) or ("no address" if english else "ohne Adresse")
        if english:
            out.append((f"{node['code']}_ok", f"Web Service “{name}”: call worked (1/0)", "Nominal", "1 = yes, 0 = no",
                        f"called by the server ({where}) when the participant passed it; empty = not reached"))
        else:
            out.append((f"{node['code']}_ok", f"Web Service „{name}“: Aufruf gelungen (1/0)", "Nominal", "1 = ja, 0 = nein",
                        f"vom Server aufgerufen ({where}), als die Person die Stelle passierte; leer = nicht erreicht"))
        for v in values(node):
            kind = "Metrisch" if v["kind"] == "number" else "Text"
            note = (f"field {v['path']} of the answer; empty = call failed or field missing" if english
                    else f"Feld {v['path']} der Antwort; leer = Aufruf misslungen oder Feld fehlte")
            label = f"Web Service “{name}”: {v['name']}" if english else f"Web Service „{name}“: {v['name']}"
            out.append((f"{node['code']}_{v['name']}", label, kind, "", note))
    return out


# --- pre-launch check ---------------------------------------------------------------------------------


def check(survey):
    """[(severity, message, None)] of the pre-launch check (flow.check)."""
    from app import piping
    from app.items.base import BUILTIN_VARIABLES, PLACEHOLDER

    out = []
    known = set(BUILTIN_VARIABLES) | set(piping.names(survey)) | set(names(survey))
    from app import panel

    known |= {p["name"] for p in panel.config(survey)["params"]}
    for node in nodes(survey):
        name = node.get("name") or node["code"]
        if not node.get("url"):
            out.append(("warning", de_en("Web Service „{name}“ hat noch keine Adresse; so läuft die Befragung an ihm vorbei.",
                                         "Web Service “{name}” has no address yet; so the survey runs past it.", name=name)))
            continue
        if not values(node):
            out.append(("warning", de_en("Web Service „{name}“ übernimmt keinen Wert aus der Antwort.",
                                         "Web Service “{name}” takes no value from the answer.", name=name)))
        placed = {m.group(1) for m in PLACEHOLDER.finditer((node.get("url") or "") + (node.get("body") or ""))}
        unknown = sorted(placed - known)
        if unknown:
            out.append(("error", de_en("Web Service „{name}“: unbekannte Platzhalter {names}.",
                                       "Web Service “{name}”: unknown placeholders {names}.", name=name,
                                       names=", ".join("{{" + u + "}}" for u in unknown))))
    for node in before_consent(survey):
        if private(node):
            name = node.get("name") or node["code"]
            out.append(("warning", de_en("Web Service „{name}“ steht vor der Einwilligung und gibt Angaben aus der Befragung "
                                         "weiter ({names}). Er wird deshalb erst aufgerufen, wenn die Person eingewilligt "
                                         "hat; besser hinter die Einwilligung stellen.",
                                         "Web Service “{name}” comes before the consent and passes on data from the survey "
                                         "({names}). So it is only called once the person has consented; better put it "
                                         "after the consent.", name=name,
                                         names=", ".join("{{" + u + "}}" for u in used(node) if u not in BUILTIN_VARIABLES))))
    for node, where in _places(survey):
        name = node.get("name") or node["code"]
        if where == "loop":
            out.append(("warning", de_en("Web Service „{name}“ steht in einer Schleife; dort wird er nicht aufgerufen: bitte "
                                         "vor die Schleife stellen.", "Web Service “{name}” is inside a loop; it is not called there: please "
                                         "put it before the loop.", name=name)))
        elif where == "separated":
            out.append(("error", de_en("Nach dem getrennten Abschnitt ist kein Web Service möglich: Seine Werte gehörten sonst "
                                       "zu keiner Teilnahme.", "No web service is possible after the separated section: its "
                                       "values would belong to no participation.")))
    return out


def before_consent(survey):
    """The web services that come before the consent in the flow (none if the survey has no consent)."""
    from app import flow

    consent = survey.consent_item
    if consent is None:
        return []
    out = []
    for node, _, _ in flow.walk(flow.tree(survey)):
        if node["t"] == "block" and node.get("block") == consent.block_id:
            return out
        if node["t"] == NODE:
            out.append(node)
    return []


def _places(survey):
    """(node, "loop" | "separated" | "") for each web service."""
    from app import flow

    out = []

    def rec(node, loop, separated):
        for child in node.get("children", []):
            if child["t"] == "split":
                separated = True
            if child["t"] == NODE:
                out.append((child, "separated" if separated else "loop" if loop else ""))
            if child.get("children"):
                rec(child, loop or child["t"] == "loop", separated)
        return separated

    rec(flow.tree(survey), False, False)
    return out


def problems(survey):
    """check() in the form of flow.check: (severity, message, block id)."""
    return [(severity, message, None) for severity, message in check(survey)]


def privacy_hosts(survey):
    """Hosts of web services that get data from the survey (beyond {{pid}}, {{lang}}, {{survey}})."""
    from app.items.base import BUILTIN_VARIABLES, PLACEHOLDER

    out = []
    for node in nodes(survey):
        used = {m.group(1) for m in PLACEHOLDER.finditer((node.get("url") or "") + (node.get("body") or ""))}
        if host(node) and used - BUILTIN_VARIABLES and host(node) not in out:
            out.append(host(node))
    return out
