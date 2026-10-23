"""Every page of the participants' side reaches the app through nginx (deploy/docker/nginx/locations/participant.conf).

The app says which side a blueprint belongs to (app/hosts.py, BLUEPRINT_SIDES); nginx on the participants' address
passes on only the paths it names and answers 404 for the rest. A blueprint of the participants' side whose paths
nginx does not name works on a computer without nginx and fails on the server: the diary app got nginx's 404 page
for /api/diary/ and /j/ and showed "Etwas ist schiefgelaufen" (2026-10-12).
"""
import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "nginx" / "locations" / "participant.conf"


def _locations():
    """(kind, pattern) of each location that passes the request on to the participants' containers."""
    out = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*location\s+(~|\^~|=)?\s*(\S+)\s*\{(.*)\}", line)
        if m and "proxy_pass" in m.group(3):
            out.append((m.group(1) or "", m.group(2)))
    return out


def _passed(path, locations):
    for kind, pattern in locations:
        if kind == "~" and re.search(pattern, path):
            return True
        if kind == "=" and path == pattern:
            return True
        if kind in ("^~", "") and path.startswith(pattern):
            return True
    return False


def test_every_path_of_the_participants_side_is_passed_on(app):
    from app import hosts

    locations = _locations()
    missing = []
    for rule in app.url_map.iter_rules():
        blueprint = rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None
        if hosts.BLUEPRINT_SIDES.get(blueprint) != hosts.PARTICIPANT:
            continue
        path = re.sub(r"<[^>]+>", "x", rule.rule)  # a variable part stands for any value
        if not _passed(path, locations):
            missing.append(f"{rule.endpoint}: {rule.rule}")
    assert not missing, "nginx answers 404 on the participants' address for: " + "; ".join(sorted(missing))


def test_the_researchers_api_stays_on_their_side():
    locations = _locations()
    assert _passed("/api/diary/v1/join/x", locations)
    assert not _passed("/api/v1/surveys/1/data.csv", locations)
    assert not _passed("/surveys/1/editor/", locations)
