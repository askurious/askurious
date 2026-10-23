"""The status page (status.askurious.de, Philipp 2026-10-08): a small web service of its own next to the app
(deploy/docker/compose.yaml) that checks the services every minute and shows the last 24 hours.

    python -m app.status_node

Like the Rechenknecht (app/compute_node.py) it has no database of the app, no SECRET_KEY and no uploads: it only
asks each service whether it answers, from inside the compose network, and keeps what it saw in a SQLite file of
its own (STATUS_DB). Settings from the environment:

    STATUS_CHECKS   service checks, "key=kind:target" separated by commas; kind http (a URL whose answer is
                    200), tcp (host:port that accepts a connection) or tls (host:port[@name] whose certificate is
                    trusted and still valid, with the days left as detail) or https (a public URL, answer 200; the
                    certificate is not checked here but by tls) or age (path:hours, a file changed within that
                    many hours, with its age in hours as detail: backup.sh's mark of the last good dump), or
                    absent (a folder without a switch "*.an" in it: the maintenance page off, deploy/docker/
                    wartung.sh; on, its sides as detail), or
                    whisper (the app's internal relay: disabled, ready or unavailable; busy counts as ready and the
                    queue counts stay inside, so the public page shows no use of the transcription; Abgleich
                    2026-10-15, T02-NEU-3/NB-11)
    STATUS_REDUNDANT services of which one answering is enough, "a|b" separated by commas (the two participants'
                    containers, restarted one by one by a deploy): one of them down shows as such, but the page
                    calls the whole disrupted only when all of a group are down
    STATUS_INFO     services shown but never calling the whole disrupted, separated by commas (the age of the
                    last dump: an operators' matter, participants are not affected)
    STATUS_STACK    domains served by a further brand's own containers and database (profile "brand" in
                    compose.yaml, datenbank-je-marke-konzept.md), separated by commas: under their status names the
                    page shows only the checks whose key begins with "brand-" and those of STATUS_SHARED, under every
                    other name only the checks without "brand-". Empty: every name shows every check
    STATUS_SHARED   checks shown under both (default "db": the database server both use)
    STATUS_TLS_UNTRUSTED_OK names whose certificate may be one a browser does not trust (self-signed), separated
                    by commas; only there, and only for the time named in BETRIEB.md. Every other such certificate
                    is a disruption (audit 2026-10-18, CFG-13)
    STATUS_CERT_NAMES further names whose certificates the check "cert" watches, separated by commas (e.g. the
                    names from before the rename); the researchers' and the status name of the main brand and its
                    participants' name are watched anyway (audit 2026-10-09b, B2)
    STATUS_DB       the SQLite file (default instance/status.db)
    STATUS_EVERY    seconds between two rounds (default 60)
    STATUS_BIND     address and port to listen on (default 127.0.0.1:8070)

The page uses the look of Askurious (app/static/app.css, header and footer as in base.html) and the language of
the browser (German or English), as the public pages do for visitors who are not signed in. Under a further brand's
status name (status.polltergeist.de; BRANDS and PUBLIC_BASE_URL from the environment, app/brand.py) it carries that
brand's name, look and links, names its participants' address (ask.polltergeist.de) and shows the checks of its own
names: for each brand an https check "web@<domain>" and a certificate check "cert@<domain>" are derived from "web"
and "cert" with the domain swapped (audit 2026-10-09b, M2, B2). The check "cert" watches every name of its brand:
researchers, participants and status (status.<domain>, as nginx and certbot know it)."""
import os
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import urllib3

urllib3.disable_warnings()  # the https check talks to nginx inside the network, with the certificate checked by tls
from flask import Flask, abort, g, render_template

from app.lang import current, de_en
from app.timeutil import fmt_local

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_DAYS = 7
SLOTS = 96                 # 24 h in bars of 15 minutes
SLOT = 24 * 3600 // SLOTS
TIMEOUT = 5
BERLIN = ZoneInfo("Europe/Berlin")

# what each key is called on the page; unknown keys show as they are. {ask} and {main}: the participants' and the
# researchers' name of the brand of the page (M2), "web@polltergeist.de" is called as "web"
NAMES = {
    "whisper": ("WhisperX (Transkription)", "WhisperX (transcription)"),
    "participant": ("Teilnahme ({ask})", "Participation ({ask})"),
    "participant-a": ("Teilnahme ({ask}), Container A", "Participation ({ask}), container A"),
    "participant-b": ("Teilnahme ({ask}), Container B", "Participation ({ask}), container B"),
    "researcher": ("Forschende ({main})", "Researchers ({main})"),
    "db": ("Datenbank", "Database"),
    "compute": ("Rechenknecht (Schätzungen)", "Rechenknecht (estimations)"),
    "web": ("Webserver (nginx) für {ask}", "Web server (nginx) for {ask}"),
    "cert": ("Zugang über HTTPS (nginx, Zertifikat)", "Access over HTTPS (nginx, certificate)"),
    "backup": ("Datensicherung", "Backup"),
    "wartung": ("Wartungsseite (an während eines Deploys)", "Maintenance page (on during a deploy)"),
    # a further brand's own containers (STATUS_STACK); its own name stands in the page's head
    "brand-participant": ("Teilnahme", "Participation"),
    "brand-researcher": ("Forschende", "Researchers"),
    "brand-compute": ("Rechenknecht (Schätzungen)", "Rechenknecht (estimations)"),
    "brand-backup": ("Datensicherung", "Backup"),
}

STACK_PREFIX = "brand-"
BRAND_SEP = "@"  # "cert@polltergeist.de": a check of that brand's own names, derived from "cert" (brand_checks)
STATUS_PREFIX = "status."  # the status name of a domain, as nginx (server_name ~^status\.) and certbot know it


def parse_checks(text):
    """"participant=http:http://participant:8000/healthz,db=tcp:db:5432" -> [(key, kind, target), ...]"""
    out = []
    for part in (text or "").split(","):
        key, _, rest = part.strip().partition("=")
        kind, _, target = rest.partition(":")
        if key and kind in ("http", "https", "tcp", "tls", "age", "absent", "whisper") and target:
            out.append((key.strip(), kind, target.strip()))
    return out


def _failure(e):
    """A fixed word for why a check failed, not the name of the exception: the page is public (audit 2026-10-13,
    TN-36)."""
    if isinstance(e, (requests.Timeout, socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(e, ssl.SSLError):
        return "tls"
    if isinstance(e, (requests.ConnectionError, ConnectionError, socket.gaierror)):
        return "unreachable"
    if isinstance(e, FileNotFoundError):
        return "missing"
    return "error"


def check(kind, target):
    """(ok, milliseconds, detail) of one check; never raises."""
    start = time.monotonic()
    try:
        if kind == "whisper":
            response = requests.get(target, timeout=TIMEOUT, allow_redirects=False)
            data = response.json() if response.status_code == 200 else None
            if not isinstance(data, dict) or data.get("state") not in ("disabled", "ready", "busy"):
                ok, detail = False, "whisper:unavailable:0:0"
            else:
                queued, running = data.get("queued"), data.get("running")
                if type(queued) is not int or type(running) is not int or not 0 <= queued <= 8 or not 0 <= running <= 1 or queued + running > 8 or (data["state"] == "disabled" and queued + running):
                    ok, detail = False, "whisper:unavailable:0:0"
                else:
                    ok, detail = True, "whisper:disabled:0:0" if data["state"] == "disabled" else "whisper:ready:0:0"
        elif kind == "http":
            r = requests.get(target, timeout=TIMEOUT, allow_redirects=False)
            ok, detail = r.status_code == 200, "" if r.status_code == 200 else f"HTTP {r.status_code}"
        elif kind == "https":  # a public address as a browser asks for it; the certificate is the tls check's
            r = requests.get(target, timeout=TIMEOUT, verify=False, allow_redirects=False)
            ok, detail = r.status_code == 200, "" if r.status_code == 200 else f"HTTP {r.status_code}"
        elif kind == "tcp":
            host, _, port = target.rpartition(":")
            socket.create_connection((host, int(port)), TIMEOUT).close()
            ok, detail = True, ""
        elif kind == "age":  # path:hours (audit 2026-10-09, BT6)
            path, _, hours = target.rpartition(":")
            age = max(0.0, (time.time() - os.path.getmtime(path)) / 3600)  # a file a moment newer than now: 0, not -0
            ok, detail = age <= float(hours), f"{age:.0f}"
        elif kind == "absent":  # the maintenance page's switches (deploy/docker/wartung.sh); no folder: none on
            on = sorted(name[:-3] for name in (os.listdir(target) if os.path.isdir(target) else [])
                        if name.endswith(".an") and name[:-3] in ("forschende", "teilnahme"))
            ok, detail = not on, " ".join(on)
        else:  # tls: host[|host…]:port, the fewest days left of their certificates; one a browser would not trust
            # (self-signed, unknown issuer, another name) is a disruption unless named in STATUS_TLS_UNTRUSTED_OK
            hosts, _, port = target.rpartition(":")
            names = [h for h in hosts.split("|") if h]
            allowed = _names(os.environ.get("STATUS_TLS_UNTRUSTED_OK"))
            worst = None
            for host in names:
                try:
                    days, untrusted = _cert_days(host, int(port))
                except Exception as e:  # one name failing is enough: named in the detail
                    return False, round((time.monotonic() - start) * 1000), f"{_failure(e)} {host}"
                bad = bool(untrusted) and host.lower() not in allowed
                # an untrusted certificate weighs more than few days left: it is what the detail names
                if worst is None or (bad, -days) > (worst[3], -worst[0]):
                    worst = (days, untrusted, host, bad)
            days, untrusted, host, bad = worst
            ok = days >= 7 and not bad
            word = {True: "self-signed", "self-signed": "self-signed", "untrusted": "untrusted"}.get(untrusted, "")
            detail = f"{days}" + (f" {word}" if word else "") + (f" {host}" if len(names) > 1 else "")
    except Exception as e:
        ok, detail = False, "whisper:unavailable:0:0" if kind == "whisper" else _failure(e)
    return ok, round((time.monotonic() - start) * 1000), detail


def _cert_days(host, port):
    """(days left, untrusted) of the certificate `host` gets under its own name (SNI: nginx picks it by name).
    untrusted: False when the chain and the name check out against the system's certificate authorities, as a
    browser checks them, else "self-signed" (nginx/self-signed.sh's stand-in) or "untrusted" (another issuer or
    name). The certificate check used to look at the days alone (audit 2026-10-18, CFG-13)."""
    untrusted = False
    try:
        der = _peer_cert(host, port, verify=True)
    except ssl.SSLCertVerificationError:
        der, untrusted = _peer_cert(host, port, verify=False), "untrusted"
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    if untrusted and cert.issuer == cert.subject:
        untrusted = "self-signed"
    return (cert.not_valid_after_utc - datetime.now(timezone.utc)).days, untrusted


def _peer_cert(host, port, verify):
    context = ssl.create_default_context()
    if not verify:  # only to read the days of a certificate that failed the check
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((host, port), TIMEOUT) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def _tls_hosts(target):
    hosts, _, port = target.rpartition(":")
    return [h for h in hosts.split("|") if h], port


def brand_checks(checks, main, brands, extra=()):
    """The checks with each brand's own names (M2, B2): "cert" also watches the main brand's researchers' and status
    name and `extra`; for each brand in `brands` ([(domain, name)]) a copy "<key>@<domain>" of every https and tls
    check on a name under `main`, with the domain swapped, its tls check covering the brand's three names."""
    from app.brand import rehost
    from config import url_host

    out = []
    for key, kind, target in checks:
        if kind == "tls" and main:
            hosts, port = _tls_hosts(target)
            if any(h == main or h.endswith("." + main) for h in hosts):
                hosts = list(dict.fromkeys(hosts + [main, STATUS_PREFIX + main] + list(extra)))
            out.append((key, kind, "|".join(hosts) + ":" + port))
        else:
            out.append((key, kind, target))
    for domain, _name in brands:
        for key, kind, target in checks:
            if BRAND_SEP in key or key.startswith(STACK_PREFIX):
                continue
            if kind == "https" and url_host(target) != url_host(rehost(target, domain, main)):
                out.append((key + BRAND_SEP + domain, kind, rehost(target, domain, main)))
            elif kind == "tls":
                hosts, port = _tls_hosts(target)
                mine = [rehost(h, domain, main) for h in hosts if h == main or h.endswith("." + main)]
                if mine:
                    mine = list(dict.fromkeys(mine + [domain, STATUS_PREFIX + domain]))
                    out.append((key + BRAND_SEP + domain, kind, "|".join(mine) + ":" + port))
    return out


def participant_host(checks, main):
    """The participants' name of the main brand: the host of the https check "web" (or of the tls check "cert");
    "ask." + main without either."""
    from config import url_host

    for key, kind, target in checks:
        if key == "web" and kind == "https":
            return url_host(target)
    for key, kind, target in checks:
        if key == "cert" and kind == "tls":
            return _tls_hosts(target)[0][0] if _tls_hosts(target)[0] else ""
    return "ask." + main if main else ""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS result (at INTEGER, key TEXT, ok INTEGER, ms INTEGER, detail TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_result_key_at ON result (key, at)")

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def add(self, rows):
        with self._db() as db:
            db.executemany("INSERT INTO result VALUES (?, ?, ?, ?, ?)", rows)
            db.execute("DELETE FROM result WHERE at < ?", (int(time.time()) - KEEP_DAYS * 86400,))

    def since(self, key, at):
        with self._db() as db:
            return db.execute("SELECT at, ok, ms, detail FROM result WHERE key = ? AND at >= ? ORDER BY at",
                              (key, at)).fetchall()


MINOR = 2  # up to this many disrupted minutes in a bar: a short drop (a restart), yellow
STEPS = 15  # a bar of 15 minutes: one step of height and colour per disrupted minute


def bar_level(n, bad):
    """How disrupted a bar was, in steps of a full bar of 15 minutes (Ralf, 2026-10-18): 0 fine (full height, green),
    1 to 14 one step lower each and from yellow towards red, 15 every minute down (an empty place, its title stays).
    A bar with fewer checks (the quarter hour running now) is scaled; any disruption is at least 1, any good minute
    keeps it below 15."""
    if not n or not bad:
        return 0
    if bad >= n:
        return STEPS
    return min(max(round(bad * STEPS / n), 1), STEPS - 1)


def bar_state(n, bad):
    """The colour of a bar by how long it was disrupted (Ralf, 2026-10-17: one minute is not red): none, ok, minor
    (1-2 minutes, yellow), partial (more, orange), down (every minute checked, red)."""
    if not n:
        return "none"
    if not bad:
        return "ok"
    if bad == n:
        return "down"
    return "minor" if bad <= MINOR else "partial"


def summary(store, key, now=None):
    """What the page shows for one service: the latest result, 96 bars of 15 minutes, availability and median.

    The bars stand on the quarter hours of the clock (11:00 to 11:15, the last one the quarter now running) and count
    minutes, not rounds: a minute with two rounds (the first round right after a restart of the service, a deploy)
    counts once, as failed when one of them failed. So a bar never shows more than 15 checks (Ralf, 2026-10-17:
    "1 von 16" in a bar of 15 minutes)."""
    now = int(now or time.time())
    first = (now // SLOT + 1 - SLOTS) * SLOT
    rows = store.since(key, first)
    minutes = {}
    for r in rows:  # one pass: each row into its minute, failed when one round of that minute failed
        if r[3] == "whisper:disabled:0:0":
            continue
        minute = r[0] // 60
        minutes[minute] = minutes.get(minute, True) and bool(r[1])
    count, bads = [0] * SLOTS, [0] * SLOTS
    for minute, good in minutes.items():
        i = (minute * 60 - first) // SLOT
        if 0 <= i < SLOTS:
            count[i] += 1
            bads[i] += not good
    bars = []
    for i in range(SLOTS):
        n, bad = count[i], bads[i]
        state = bar_state(n, bad)
        start = first + i * SLOT
        bars.append({"state": state, "level": bar_level(n, bad), "at": datetime.fromtimestamp(start, BERLIN).strftime("%H:%M"),
                     "to": datetime.fromtimestamp(start + SLOT, BERLIN).strftime("%H:%M"), "n": n, "bad": bad})
    measured = [r for r in rows if r[3] != "whisper:disabled:0:0"]
    ok = [r for r in measured if r[1]]
    times = sorted(r[2] for r in ok)
    last = rows[-1] if rows else None
    whisper = None
    if last and key.split(BRAND_SEP, 1)[0] == "whisper" and last[3].startswith("whisper:"):
        parts = last[3].split(":")
        if len(parts) == 4 and parts[1] in ("disabled", "ready", "busy", "unavailable") and parts[2].isdigit() and parts[3].isdigit():
            # rows of before 2026-10-15 may still say busy with counts: shown as ready, without them
            whisper = {"state": "ready" if parts[1] == "busy" else parts[1]}
    return {"key": key, "bars": bars, "checks": len(measured), "whisper": whisper,
            "share": round(100 * len(ok) / len(measured), 2) if measured else None,
            "median": times[len(times) // 2] if times else None,
            "last": None if not last else {"ok": bool(last[1]), "ms": last[2], "detail": last[3],
                                           "at": fmt_local(datetime.fromtimestamp(last[0], BERLIN).replace(tzinfo=None),
                                                            lang=current())}}


def parse_redundant(text):
    """"participant-a|participant-b" -> [{"participant-a", "participant-b"}]"""
    return [set(k.strip() for k in part.split("|") if k.strip()) for part in (text or "").split(",") if part.strip()]


def disrupted(services, redundant, info=()):
    """The services that make the whole disrupted: those down, except one of a group whose other members answer
    and those only shown for information (STATUS_INFO)."""
    up = {s["key"] for s in services if s["last"] and s["last"]["ok"]}
    out = []
    for s in services:
        if not s["last"] or s["last"]["ok"] or s["key"] in info:
            continue
        if any(s["key"] in group and group & up for group in redundant):
            continue
        out.append(s)
    return out


def shown(keys, domain, stack, shared):
    """The checks of the page under `domain` (the brand's, None for the main name): under a domain in `stack` its
    own ("brand-…") and the shared ones, elsewhere all but those; with no `stack`, all. A brand's own copy of a
    check ("web@polltergeist.de", brand_checks) stands in for that check on its page and is hidden elsewhere."""
    mine = {k.split(BRAND_SEP, 1)[0]: k for k in keys if domain and k.endswith(BRAND_SEP + domain)}
    keys = [k for k in keys if BRAND_SEP not in k]
    if not stack:
        out = list(keys)
    elif domain in stack:
        out = [k for k in keys if k.startswith(STACK_PREFIX) or k in shared]
    else:
        out = [k for k in keys if not k.startswith(STACK_PREFIX)]
    return [mine.pop(k, k) for k in out] + list(mine.values())


def _names(text):
    return {n.strip().lower() for n in (text or "").split(",") if n.strip()}


def run_checks(store, checks):
    at = int(time.time())
    store.add([(at, key, int(ok), ms, detail) for key, kind, target in checks for ok, ms, detail in [check(kind, target)]])


def create():
    app = Flask(__name__, static_folder=os.path.join(HERE, "static"), template_folder=os.path.join(HERE, "templates"))
    app.config["BRANDS"] = (os.environ.get("BRANDS") or "").strip()
    app.config["PUBLIC_BASE_URL"] = (os.environ.get("PUBLIC_BASE_URL") or "https://askurious.de").rstrip("/")
    from app.brand import parse
    from config import url_host

    main = url_host(app.config["PUBLIC_BASE_URL"])
    brands = parse(app.config["BRANDS"])
    plain = parse_checks(os.environ.get("STATUS_CHECKS"))
    ask = participant_host(plain, main)
    checks = brand_checks(plain, main, brands, sorted(_names(os.environ.get("STATUS_CERT_NAMES"))))
    redundant = parse_redundant(os.environ.get("STATUS_REDUNDANT"))
    info = {k.strip() for k in (os.environ.get("STATUS_INFO") or "").split(",") if k.strip()}
    stack = _names(os.environ.get("STATUS_STACK"))
    shared = _names(os.environ.get("STATUS_SHARED") if "STATUS_SHARED" in os.environ else "db")
    store = Store(os.environ.get("STATUS_DB") or os.path.join(os.path.dirname(HERE), "instance", "status.db"))
    every = max(10, int(os.environ.get("STATUS_EVERY") or 60))

    def loop():
        while True:
            try:
                run_checks(store, checks)
            except Exception:  # one round lost is no reason to stop checking
                pass
            time.sleep(every - time.time() % every)  # on the full minute, however long the round took

    threading.Thread(target=loop, daemon=True, name="status-checks").start()

    @app.before_request
    def language():
        from app.lang import browser

        g.ui_lang = browser()  # nobody signs in here: the language of the browser

    def name(key):
        """The name of a check on this page, with the participants' and researchers' names of its brand (M2)."""
        from app import brand

        base = key.split(BRAND_SEP, 1)[0]
        if base not in NAMES:
            return key
        domain = brand.domain()
        return de_en(*NAMES[base], ask=brand.rehost(ask, domain, main), main=brand.rehost(main, domain, main))

    app.add_template_filter(name, "status_name")

    @app.template_filter("pct")
    def pct(value):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text if de_en("de", "en") == "en" else text.replace(".", ",")

    @app.context_processor
    def helpers():
        from app import brand

        return {"de_en": de_en, "name": name, "home": brand.public_base(), "site": brand.site(), "default_mode": brand.default_mode(),
                "base_key": lambda key: key.split(BRAND_SEP, 1)[0]}

    def state():
        """(services, the whole's state ok/down/none, the services that make it disrupted) under this request's
        brand."""
        from app import brand

        keys = shown([key for key, _, _ in checks], brand.domain(), stack, shared)
        services = [summary(store, key) for key in keys]
        down = disrupted(services, redundant, info)
        unknown = [s for s in services if not s["last"]]
        return services, "down" if down else "none" if unknown and len(unknown) == len(services) else "ok", down

    @app.route("/")
    def index():
        services, overall, down = state()
        start = datetime.now(BERLIN) - timedelta(hours=24)
        return render_template("status/index.html", services=services, overall=overall, down=down, every=every,
                               start=fmt_local(start.replace(tzinfo=None), lang=current()), deployed=deployed())

    def deployed():
        """When the running code was deployed (app/deployed_at.txt, same image as the app); it stood in the admins'
        footer of the app before (Ralf, 2026-10-11). Date and time only: the page is public and has no sign-in, so
        the commit stands under Administration > Server in the app (audit 2026-10-11, BT3, decision 5a). None outside
        a deploy."""
        from app import _deployed_at

        stamp = _deployed_at()
        return stamp and {"at": fmt_local(stamp["at"], lang=current())}

    dots = {}  # per brand domain: (time, svg); the dot stands in the footer of every page of the app

    @app.route("/dot.svg")
    def dot():
        """The whole's state as a coloured dot, for the link "Status" in the footer of the app (Ralf, 2026-10-17): an
        image, so the app needs neither a script nor an exception in its CSP (img-src allows https:). Kept 30 s per
        brand and 60 s in the browser; it says nothing the page itself does not."""
        from app import brand

        domain = brand.domain() or ""
        kept = dots.get(domain)
        if not kept or time.time() - kept[0] > 30:
            colour = {"ok": "#2e7d32", "down": "#c62828"}.get(state()[1], "#9e9e9e")
            kept = dots[domain] = (time.time(), '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
                                                f'viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="{colour}"/></svg>')
        return kept[1], 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=60"}

    @app.route("/status/<token>.json")
    def status_json(token):
        """The state of every service as JSON, for the operators' monitoring (a script on their own web space draws a small
        desk display from it and from app/monitoring_feed.py): {"state": 0 all run, 1 not known yet, 2 disrupted, "services": [{"key": "compute",
        "state": "ok" | "down" | "none"}, ...]} in the page's order. Only with the token STATUS_JSON_TOKEN (.env, at least
        24 characters, held by the site admins): the keys name the services, which the public page shows in words.
        Without a token set, or with a wrong one, the address does not exist (404, as for any other address)."""
        wanted = os.environ.get("STATUS_JSON_TOKEN", "").strip()
        if len(wanted) < 24 or not secrets.compare_digest(token.encode(), wanted.encode()):
            abort(404)
        services, overall, _ = state()
        body = {"state": {"ok": 0, "none": 1}.get(overall, 2),
                "services": [{"key": s["key"].split(BRAND_SEP, 1)[0],
                              "state": "none" if not s["last"] else "ok" if s["last"]["ok"] else "down"} for s in services]}
        return body, 200, {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    from app import brand as brand_mod

    brand_mod.install(app)  # "Askurious" in the template: the brand's name under its status name (app/brand.py)

    @app.after_request
    def headers(response):
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


def main():
    from werkzeug.serving import run_simple

    host, _, port = (os.environ.get("STATUS_BIND") or "127.0.0.1:8070").rpartition(":")
    run_simple(host or "127.0.0.1", int(port), create(), threaded=True)


if __name__ == "__main__":
    main()
