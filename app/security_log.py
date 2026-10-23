"""Security events in the log of the server (security audit 2026-10-13, DP-7/AU-7): sign-ins that worked or failed,
pauses and locks after wrong passwords, changes of the second factor and of the password, one-time links made and used,
invitations and join links made and taken, the right to create surveys and experimental features changed, other
sessions ended ("Überall abmelden", Abgleich 2026-10-15 AU-7), and the rate limits that refused requests (once a
minute per process, counted).

One line per event on the process's error output, which Docker keeps with the other output of the container (at most
3 x 20 MB per container, compose.yaml; older lines are overwritten), through the logging the app has anyway: no further
service, no table. Each line is "security " and a JSON object: the event, the account's number where there is one, and
`net`, a digest of the sender's network (IPv4 /24, IPv6 /48) with a key of the server that changes every day, so that
lines of one day can be put together but no address can be read from them. Never a password, a code, a token, an
e-mail address or an answer: the callers pass numbers and fixed words only, and `event` keeps nothing else.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import sys
import time

log = logging.getLogger("askurious.security")

# the fields an event may carry: numbers and fixed words of the code, nothing a person typed
FIELDS = {"account", "by", "via", "kind", "role", "target", "counts", "seconds", "reason"}


def _setup():
    """Its own handler on the error output at INFO (the app's logger shows warnings only), and the daily files of
    folder() when there is one: once per process."""
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)
        where = folder()
        if where:
            daily = DailyFiles(where)
            daily.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            log.addHandler(daily)
        log.setLevel(logging.INFO)
        log.propagate = False


def folder():
    """The folder of the daily files of the security log (audit 2026-10-22, E-04): the container's output alone was
    rotated after 60 MB, and whoever flooded it with other lines pushed the sign-ins and changed rights out. SECURITY_LOG_DIR,
    else on the Docker server (SIDE set) instance/security-log; None elsewhere (development, tests)."""
    from flask import current_app, has_app_context

    if not has_app_context():
        return None
    import os

    given = current_app.config.get("SECURITY_LOG_DIR") or ""
    if given:
        return given
    if current_app.config.get("SIDE"):
        return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "security-log")
    return None


class DailyFiles(logging.Handler):
    """One file per day (security-YYYY-MM-DD.log), appended to by every process of every container (one write per line,
    O_APPEND); sweep() removes those older than SECURITY_LOG_DAYS. Never raises: the log must not break a sign-in."""

    def __init__(self, where):
        super().__init__(logging.INFO)
        self.where = where

    def emit(self, record):
        import os

        try:
            os.makedirs(self.where, mode=0o700, exist_ok=True)
            name = os.path.join(self.where, time.strftime("security-%Y-%m-%d.log", time.gmtime(record.created)))
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (self.format(record) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:  # noqa: BLE001 - see the docstring
            pass


def sweep(now=None):
    """The daily files older than SECURITY_LOG_DAYS (the housekeeping, once a minute). Returns how many went."""
    import os

    from flask import current_app

    where = folder()
    days = int(current_app.config.get("SECURITY_LOG_DAYS") or 90)
    if not where or not os.path.isdir(where):
        return 0
    oldest = time.strftime("security-%Y-%m-%d.log", time.gmtime((now or time.time()) - days * 86400))
    gone = 0
    for name in os.listdir(where):
        if name.startswith("security-") and name.endswith(".log") and name < oldest:
            try:
                os.remove(os.path.join(where, name))
                gone += 1
            except OSError:
                pass
    return gone


def net():
    """A digest of the sender's network for today (IPv4 /24, IPv6 /48), or None without a request or address."""
    from flask import current_app, has_request_context, request

    if not has_request_context():
        return None
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    prefix = 24 if ip.version == 4 else 48
    network = str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
    key = str(current_app.config.get("SECRET_KEY") or "").encode()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return hmac.new(key, f"security|{day}|{network}".encode(), hashlib.sha256).hexdigest()[:12]


def event(name, **fields):
    """Write one security event. Never raises: the log must not break a sign-in."""
    try:
        _setup()
        line = {"event": str(name)}
        for k, v in fields.items():
            if k in FIELDS and v is not None:
                line[k] = v if isinstance(v, (int, float, bool, dict)) else str(v)[:40]
        where = net()
        if where:
            line["net"] = where
        log.info("security %s", json.dumps(line, sort_keys=True, separators=(",", ":")))
    except Exception:  # noqa: BLE001 - logging only
        pass
