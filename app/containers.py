"""How busy the containers are, for Administration > Server, section "Container" (Ralf, 2026-10-09).

On the Docker server deploy/docker/container-stats.sh writes every minute (crontab) what `docker stats` and
`docker inspect` say into ~/askurious-status/containers.json, plus one line per minute of the last 24 hours in
containers-history.jsonl next to it. The researchers' container sees that folder read-only (CONTAINER_STATS in
compose.yaml); no container gets the Docker socket for this. Without the file (Plesk, a laptop) the section shows
only the load of the machine the app runs on.

CPU is counted in cores, as docker does (100 % = one core), against the container's own ceiling (`cpus:` in
compose.yaml) or, without one, every core of the server."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from app.lang import de_en

STALE = timedelta(minutes=5)  # the cron writes every minute: older means it stopped

# the services of deploy/docker/compose.yaml in the order of the page, with their names on it
SERVICES = {
    "participant-a": ("Teilnahme A", "Participants A"),
    "participant-b": ("Teilnahme B", "Participants B"),
    "researcher": ("Forschende", "Researchers"),
    "housekeeping": ("Haushaltung", "Housekeeping"),
    "compute": ("Rechenknecht", "Compute node"),
    "whisper": ("WhisperX (Transkription)", "WhisperX (transcription)"),
    "db": ("Datenbank (PostgreSQL)", "Database (PostgreSQL)"),
    "nginx": ("nginx (vorne)", "nginx (in front)"),
    "status": ("Statusseite", "Status page"),
    "certbot": ("Zertifikate (certbot)", "Certificates (certbot)"),
    # a further brand's own containers (profile "brand" in compose.yaml, datenbank-je-marke-konzept.md)
    "brand-participant": ("Teilnahme (eigene Marke)", "Participants (own brand)"),
    "brand-researcher": ("Forschende (eigene Marke)", "Researchers (own brand)"),
    "brand-housekeeping": ("Haushaltung (eigene Marke)", "Housekeeping (own brand)"),
    "brand-compute": ("Rechenknecht (eigene Marke)", "Compute node (own brand)"),
}
UNITS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
         "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4}


def _bytes(text):
    """'1.254GiB' -> bytes; None for what docker writes when it has no value ('--')."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", text or "")
    if not m or m.group(2).lower() not in UNITS:
        return None
    return float(m.group(1)) * UNITS[m.group(2).lower()]


def _cores(text):
    """'19.08%' -> 0.1908 cores."""
    try:
        return float((text or "").strip().rstrip("%")) / 100
    except ValueError:
        return None


def _service(name):
    """'askurious-participant-a-1' (or '/askurious-…' from inspect) -> 'participant-a'."""
    m = re.fullmatch(r"/?[^-]+-(.+)-\d+", name or "")
    return m.group(1) if m else (name or "").lstrip("/")


def _when(text):
    """docker's times (nanoseconds, Z) -> an aware datetime; None for docker's zero time."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", text or "")
    if not m or m.group(1).startswith("0001"):
        return None
    return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)


def _peaks(path, since):
    """The highest CPU of each service since `since`, from the history file (one line per minute)."""
    peaks = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a line cut off while the cron wrote it
                t = _when(row.get("time"))
                if t is None or t < since:
                    continue
                for s in row.get("stats") or []:
                    c = _cores(s.get("cpu"))
                    if c is not None:
                        svc = _service(s.get("name"))
                        peaks[svc] = max(peaks.get(svc, 0.0), c)
    except OSError:
        pass
    return peaks


def _local_host():
    """The machine the app runs on, when no file says more: cores, load, memory (where /proc tells it)."""
    host = {"cpus": os.cpu_count(), "load": None, "mem_total": None, "mem_available": None}
    try:
        host["load"] = list(os.getloadavg())
    except (AttributeError, OSError):  # Windows
        pass
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            info = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in f if ":" in line)}
        host["mem_total"], host["mem_available"] = info.get("MemTotal"), info.get("MemAvailable")
    except (OSError, ValueError):
        pass
    return host


def shown(text):
    """CONTAINER_SERVICES ("brand-participant,brand-researcher,db,...") -> the set of services to show; None (all)
    when empty. A further brand's installation shows its own containers and the shared ones, not the others'."""
    names = {n.strip() for n in (text or "").split(",") if n.strip()}
    return names or None


def overview(path, now=None, only=None):
    """What the section shows: {"containers": bool, "time", "stale", "host", "rows"}. `path` is CONTAINER_STATS;
    empty or missing file: no containers, only the local machine. `only`: the services to show (shown()), None: all."""
    now = now or datetime.now(timezone.utc)
    data = None
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    if not isinstance(data, dict):
        return {"containers": False, "time": None, "stale": False, "host": _local_host(), "rows": []}
    host = data.get("host") or {}
    cpus = host.get("cpus") or None
    info = {_service(i.get("name")): i for i in data.get("info") or []}
    peaks = _peaks(os.path.join(os.path.dirname(path), "containers-history.jsonl"), now - timedelta(hours=24))
    rows = []
    for s in data.get("stats") or []:
        svc = _service(s.get("name"))
        if only is not None and svc not in only:
            continue
        i = info.get(svc, {})
        state = i.get("state") or {}
        limit = (i.get("nano_cpus") or 0) / 1e9 or cpus
        mem = (s.get("mem") or "").split("/")[0]
        de, en = SERVICES.get(svc, (svc, svc))
        cores = _cores(s.get("cpu"))
        peak = max((v for v in (peaks.get(svc), cores) if v is not None), default=None)  # never below now
        rows.append({
            "service": svc, "label": de_en(de, en), "cores": cores, "limit": limit, "peak": peak, "mem": _bytes(mem), "pids": int(s["pids"]) if str(s.get("pids", "")).isdigit() else None,
            "started": _when(state.get("StartedAt")), "restarts": i.get("restarts"),
            "status": state.get("Status"), "health": (state.get("Health") or {}).get("Status"),
        })
    order = list(SERVICES)
    rows.sort(key=lambda r: (order.index(r["service"]) if r["service"] in order else len(order), r["service"]))
    t = _when(data.get("time"))
    return {"containers": True, "time": t, "stale": t is None or now - t > STALE, "host": host, "rows": rows}
