"""What the backups hold, for Administration > Server, block "Sicherungen" (Ralf, 2026-10-18, option A: show only).

On the Docker server deploy/docker/backup.sh writes after every run the list of ~/backups (name, size, time; never a
dump) into ~/askurious-status/backups.json, next to containers.json, the folder the researchers' container sees
read-only. So the path follows CONTAINER_STATS and compose.yaml needs no line of its own. The dumps themselves stay
out of every container: nothing here can read, download or delete one. A brand with its own database sees only its
own dumps. Without the file (Plesk, a laptop) the block shows the copies the app makes before migrations under
SQLite (app/migrations.py)."""
import json
import os
from datetime import datetime, timedelta, timezone

STALE = timedelta(hours=9)  # every four hours: as the status page (backup=age:/stamps/backup-ok:9), two runs missed
KEEP_DAYS = {"regular": 14, "before-deploy": 30}  # backup.sh, as the privacy page says; others (a rename) by hand


def _when(text):
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def overview(stats_path, database, now=None):
    """None without a list; else time, run, ok, offsite (with the time of the last copy that went through and
    whether it is older than STALE; audit 2026-10-18, CFG-03), encrypt (the dumps on the server encrypted), stale,
    the dumps of `database` (newest first), the uploads snapshots and the sums."""
    if not stats_path:
        return None
    path = os.path.join(os.path.dirname(stats_path), "backups.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    time = _when(raw.get("time"))
    dumps = []
    for d in raw.get("dumps") or []:
        # a brand sees its own database only; dumps without one (a rename) belong to the platform
        if (d.get("database") or "askurious") != (database or "askurious"):
            continue
        at, kind = _when(d.get("at")), d.get("kind") or "other"
        days = KEEP_DAYS.get(kind)
        dumps.append({"name": str(d.get("name") or ""), "kind": kind, "size": int(d.get("size") or 0), "at": at,
                      "until": at + timedelta(days=days) if at and days else None,
                      "encrypted": bool(d.get("encrypted"))})
    dumps.sort(key=lambda d: d["at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    brand = database not in (None, "", "askurious")
    offsite, offsite_at = bool(raw.get("offsite")), _when(raw.get("offsite_at")) if raw.get("offsite_at") else None
    uploads = [{"snapshots": int(u.get("snapshots") or 0), "newest": u.get("newest") or "", "size": int(u.get("size") or 0)}
               for u in raw.get("uploads") or [] if (u.get("name") == "uploads-brand") == brand]
    return {"time": time, "run": raw.get("run") or "regular", "ok": bool(raw.get("ok")),
            "offsite": offsite, "offsite_at": offsite_at,
            "offsite_stale": offsite and (offsite_at is None or now - offsite_at > STALE),
            "encrypt": bool(raw.get("encrypt")), "stale": time is None or now - time > STALE,
            "dumps": dumps, "uploads": uploads[0] if uploads else None,
            "regular": [d for d in dumps if d["kind"] == "regular"],
            "size": sum(d["size"] for d in dumps)}
