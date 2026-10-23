"""Coscine, the research data platform of RWTH Aachen (for everyone since 2026-10-20, experimental before; concept coscine-konzept.md, C1-C17 as
recommended, Ralf 2026-10-04).

Coscine complements Zenodo: not a publication with a DOI, but the university's own archive of a project (kept up to
10 years after it ends). A researcher pastes a personal API token on the page "Konto" (Coscine offers no OAuth for
other applications); Askurious reads the name and the expiry date from it and stores it encrypted. On the page
"In Coscine ablegen" of a survey they pick one of their projects and a resource in it (web, S3 or WORM), tick what goes
along (the open-data package of Zenodo, the pre-registration snapshots, the versions of an interview guide) and fill
the metadata the resource's application profile asks for; Askurious fills in what it knows. Each upload goes into a
new folder askurious/<survey uid>/<date_time>/ and never overwrites anything; a file checksums.sha256 (app/packages.py) lists every
file with its SHA-256.

Optional (stage 2, Ralf 2026-10-04: "ja, bauen, aber muss optional sein"): after a first upload, a tick in the block
"Coscine" puts every new pre-registration snapshot (at the start of the fieldwork) or every new version of an
interview guide into the same resource by itself, with the metadata of the last upload and the token of the person
who ticked it. It is off unless ticked; a failure never stops the start or the version and shows in the block.

Every file in Coscine needs metadata after the resource's profile (SHACL, Turtle). The profile is read here with a
small parser for the flat profiles Coscine's generator writes (base profile, "Social Science Data Profile RISK" and
the like); a profile with a required field Askurious cannot offer (a vocabulary, a nested form) is named as such.
API: https://coscine.rwth-aachen.de/coscine/api/swagger/index.html (v2)."""
import base64
import hashlib
import json
import logging
import re
import threading
from datetime import date, datetime, timezone
from urllib.parse import quote

import requests
from flask import (Blueprint, abort, current_app, flash, has_request_context, redirect, render_template, request, url_for)
from flask_login import current_user, login_required

from app import autosave, bounded, secrets_box
from app.extensions import db
from app.lang import de_en, english
from app.models import CoscineConnection, utcnow
from app.timeutil import local_today

bp = Blueprint("coscine", __name__, url_prefix="/coscine")

# the types Askurious uploads to, all through the documented API; S3 and WORM since stage 2 (C4)
STORE_TYPES = ("dsNrwWeb", "dsNrwS3", "dsNrwS3Worm", "rds")
KIND_LABELS = {"dsNrwS3": "S3", "dsNrwS3Worm": "WORM, unveränderlich"}
KIND_LABELS_EN = {"dsNrwS3": "S3", "dsNrwS3Worm": "WORM, unchangeable"}
GUEST_ROLE = "9184a442-4419-4e30-9fe6-0cfe32c9a81f"  # Coscine's role "Guest": reads only
WARN_DAYS = 30
MAX_UPLOADS = 20  # kept in the list of a survey
DOCS = "https://docs.coscine.de/en/token/"
SITE = "https://coscine.rwth-aachen.de/"
TOKEN_PAGE = SITE + "user/"  # the profile with the section "Access token"

SH = "http://www.w3.org/ns/shacl#"
XSD = "http://www.w3.org/2001/XMLSchema#"
DCT = "http://purl.org/dc/terms/"
DCMITYPE = "http://purl.org/dc/dcmitype/"
DASH = "http://datashapes.org/dash#"
# terms of the RISK profile (https://purl.org/coscine/ap/SocialSciencebyRISK) that Askurious knows the value of
N_RAW = "https://purl.org/coscine/terms/c4d28f97-931d-4d40-8616-9b3b43297dda/"
N_CLEAN = "https://purl.org/coscine/terms/385c75b8-8278-411f-a031-13b365659689/"
PERIOD = "https://purl.org/coscine/terms/98338a24-8a30-4e4e-903c-b01f7e3ac0ac/"
# persons: several values, one per person, typed with "; " between them
MULTI = (DCT + "creator", DCT + "contributor")

# the limits of one call to Coscine (app/bounded.py, audit 2026-10-11 AU7): its answers are small JSON documents
MAX_ANSWER = 5 * 1024 * 1024
CALL_SECONDS = 120
UPLOAD_SECONDS = 600
# the check of a token (K7, Coscine-Ausbau 2026-10-20): on "Konto" a page does not wait long for it
CHECK_SECONDS = 10
CHECK_KEEP = 600  # seconds a check on "Konto" is kept per process
TOKEN_MAIL_DAYS = 14  # the daily round mails the person behind an automatic upload this many days before the end
SPACE_WARN = 90  # per cent of a resource's storage in use from which Askurious warns (K4)
RISK_PROFILE = "https://purl.org/coscine/ap/SocialSciencebyRISK"  # the profiles a new resource may get (K1)
BASE_PROFILE = "https://purl.org/coscine/ap/base/"
NEW_GB_MIN = 1  # a new resource: ten times the last upload, at least this many GB (E2)
# files taken from Coscine into a survey (K6, E10: images and tables first): their endings and largest size
PICK_KINDS = {"image": (".png", ".jpg", ".jpeg", ".gif", ".webp"), "table": (".csv", ".tsv", ".txt"),
              "media": (".mp4", ".webm", ".mp3", ".m4a", ".ogg", ".wav", ".weba"), "survey": (".json", ".zip")}
PICK_LIMIT = {"image": 10 * 1000 * 1000, "table": 2 * 1000 * 1000}
PICK_MAX_FILES = 500
DEEP_LIMIT = 500 * 1000 * 1000  # "gründlich prüfen" downloads an upload of at most this many bytes (K2, E4)
UNIT_BYTES = {"BYTE": 1, "KibiBYTE": 2 ** 10, "MebiBYTE": 2 ** 20, "GibiBYTE": 2 ** 30, "TebiBYTE": 2 ** 40,
              "PebiBYTE": 2 ** 50}


class CoscineError(Exception):
    def __init__(self, message="", status=None):
        super().__init__(message)
        self.status = status  # the HTTP status of Coscine's answer, None if it did not answer


class PartialUpload(CoscineError):
    """An upload that broke off after some files were in Coscine: `entry` is the entry for survey.coscine with the
    files that arrived, "partial": True and the name of the file that failed."""

    def __init__(self, message, entry):
        super().__init__(message)
        self.entry = entry


# --- the connection ------------------------------------------------------------------------------------------


def connection(user):
    return db.session.scalar(db.select(CoscineConnection).filter_by(user_id=user.id))


def token_expiry(token):
    """The expiry date written in the token (a JWT), or None. Only read, not checked: Coscine checks it."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), timezone.utc).replace(tzinfo=None)
    except (IndexError, ValueError, KeyError, TypeError, OverflowError):
        return None


def days_left(conn):
    """Days until the token runs out (negative: ran out), None if unknown."""
    if conn is None or conn.expires_at is None:
        return None
    return (conn.expires_at - utcnow()).days


def client_for(user):
    conn = connection(user)
    token = secrets_box.decrypt(conn.token) if conn else ""
    if not token:
        raise CoscineError(de_en("Bitte zuerst unter „Konto › Verbindungen“ ein Coscine-Token hinterlegen.",
                                 "Please first add a Coscine token under “Account › Connections”."))
    return Client(token)


# --- the API -------------------------------------------------------------------------------------------------


def _segment(path):
    """A file path as Coscine's {encodedPath}: encoded, and encoded once more, so that the API reads
    it as one path segment."""
    return quote(quote(path), safe="")


def _without_reason(status):
    """What an answer of Coscine without a reason means, from its API description of the status."""
    if status == 404:
        return de_en("Coscine kennt einen der mitgeschickten Einträge nicht, etwa die Einrichtung aus Ihrem Coscine-Profil, "
                     "die Disziplin, die Lizenz oder den Ressourcentyp.",
                     "Coscine does not know one of the entries sent, such as the organisation from your Coscine profile, "
                     "the discipline, the licence or the resource type.")
    if status == 400:
        return de_en("Coscine hält eine Angabe für falsch formatiert.", "Coscine considers an entry badly formatted.")
    if status >= 500:
        return de_en("Fehler in Coscine selbst. Bitte später erneut versuchen.",
                     "An error in Coscine itself. Please try again later.")
    return de_en("Coscine nennt keinen Grund.", "Coscine gives no reason.")


class Client:
    def __init__(self, token, seconds=CALL_SECONDS):
        if not token:
            raise CoscineError(de_en("Kein Coscine-Token angegeben.", "No Coscine token given."))
        self.token, self.base, self.seconds = token, current_app.config["COSCINE_API"], seconds

    def request(self, method, path, **kwargs):
        r = self._send(method, path, **kwargs)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    def _send(self, method, path, max_bytes=MAX_ANSWER, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}", **kwargs.pop("headers", {})}
        try:  # only ever to the configured address: no address from an answer is followed
            # the whole call within a limit and the answer at most MAX_ANSWER (app/bounded.py, audit 2026-10-11 AU7);
            # an upload or a download (K2) gets longer
            upload = "files" in kwargs or "data" in kwargs or max_bytes > MAX_ANSWER
            r = bounded.request(method, self.base + path, max_bytes=max_bytes,
                                seconds=UPLOAD_SECONDS if upload else self.seconds,
                                headers=headers, timeout=120 if upload else min(120, self.seconds),
                                allow_redirects=False, **kwargs)
        except bounded.TooLarge as e:  # a download over its limit (K6): a fact about the file, not about Coscine
            raise CoscineError(de_en("Die Datei ist zu groß.", "The file is too large."), status=413) from e
        except requests.RequestException as e:
            logging.getLogger(__name__).warning("Coscine %s %s not reachable: %s", method, path, e)
            raise CoscineError(de_en("Coscine antwortet gerade nicht. Bitte in einigen Minuten erneut versuchen; bleibt es "
                                     "dabei, die Admins der Plattform benachrichtigen.",
                                     "Coscine is not responding right now. Please try again in a few minutes; if it "
                                     "persists, notify the platform admins.")) from e
        if r.status_code in (401, 403, 413):
            logging.getLogger(__name__).warning("Coscine %s %s: %s", method, path, r.status_code)
        if r.status_code == 401:
            raise CoscineError(de_en("Coscine lehnt das Token ab. Ist es abgelaufen oder widerrufen? Unter „Konto › Verbindungen“ "
                                     "lässt sich ein neues hinterlegen.",
                                     "Coscine rejects the token. Has it expired or been revoked? A new one can be "
                                     "added under “Account › Connections”."), status=401)
        if r.status_code == 403:
            raise CoscineError(de_en("Dafür fehlen in Coscine die Rechte. Bitte in Coscine prüfen, ob Ihr Konto in diesem "
                                     "Projekt Mitglied ist und schreiben darf, oder unter „Konto › Verbindungen“ ein Token mit diesen "
                                     "Rechten hinterlegen.",
                                     "The rights for this are missing in Coscine. Please check in Coscine whether your "
                                     "account is a member of this project and may write, or add a token with these rights "
                                     "under “Account › Connections”."), status=403)
        if r.status_code == 413:
            raise CoscineError(de_en("Coscine nimmt die Datei nicht an: Sie ist zu groß oder der Speicher der Ressource ist voll.",
                                     "Coscine does not accept the file: it is too large or the storage of the resource is full."), status=413)
        if r.status_code >= 300:
            try:
                err = r.json()
                msg = ""
                if isinstance(err, dict):
                    msg = str(err.get("title") or err.get("detail") or err.get("message") or "")
                    errors = err.get("errors")
                    if isinstance(errors, dict):
                        msg += " " + "; ".join(f"{k}: {' '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in errors.items())
                    # Coscine's problem answers name the kind of error in "errorId" (its exception class) even when the
                    # production server leaves the title out
                    if err.get("errorId"):
                        msg += f" ({err['errorId']})"
            except ValueError:
                msg = r.text[:300]
            logging.getLogger(__name__).warning("Coscine %s %s: %s %s", method, path, r.status_code, msg.strip()[:500])
            # Coscine often answers without a reason (e.g. 404 on POST /projects): then say what the status means
            msg = msg.strip()[:400] or _without_reason(r.status_code)
            raise CoscineError(de_en("Coscine nimmt die Anfrage nicht an (HTTP {code}): {msg}",
                                     "Coscine does not accept the request (HTTP {code}): {msg}",
                                     code=r.status_code, msg=msg), status=r.status_code)
        return r

    def _all(self, path):
        out, page = [], 1
        while page <= 40:
            answer = self.request("GET", path, params={"PageNumber": page, "PageSize": 50})
            out += answer.get("data") or []
            if not (answer.get("pagination") or {}).get("hasNext"):
                break
            page += 1
        return out

    def me(self):
        return self.request("GET", "/self").get("data") or {}

    def projects(self):
        return self._all("/projects")

    def resources(self, project_id):
        return self._all(f"/projects/{quote(project_id, safe='')}/resources")

    def resource(self, project_id, resource_id):
        return self.request("GET", f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}").get("data") or {}

    def project(self, project_id):
        return self.request("GET", f"/projects/{quote(project_id, safe='')}").get("data") or {}

    def may_write(self, project_id):
        role = (self.request("GET", f"/projects/{quote(project_id, safe='')}/members/me").get("data") or {}).get("role") or {}
        return bool(role.get("id")) and role.get("id") != GUEST_ROLE

    def profile(self, uri):
        data = self.request("GET", f"/application-profiles/profiles/{quote(uri, safe='')}").get("data") or {}
        return data.get("displayName") or uri, (data.get("definition") or {}).get("content") or ""

    def listing(self, project_id, resource_id, folder, depth=4):
        """The files under a folder of a resource, its subfolders included (K2): [{"path" without a leading "/",
        "size", "changed"}]. At most `depth` levels and 2000 entries per folder (40 pages of 50)."""
        out, base = [], f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}/trees/files"
        page = 1
        while page <= 40:
            answer = self.request("GET", base, params={"Path": folder.rstrip("/") + "/", "PageNumber": page, "PageSize": 50})
            for item in answer.get("data") or []:
                path = str(item.get("path") or "").lstrip("/")
                if not path:
                    continue
                if item.get("type") == "Tree":
                    if depth > 1 and path.rstrip("/") != folder.rstrip("/"):
                        out += self.listing(project_id, resource_id, path, depth - 1)
                    continue
                out.append({"path": path, "size": item.get("size"), "changed": item.get("changeDate") or ""})
            if not (answer.get("pagination") or {}).get("hasNext"):
                break
            page += 1
        return out

    def download(self, project_id, resource_id, path, limit):
        """The content of one file, at most `limit` bytes (K2, "gründlich prüfen")."""
        return self._send("GET", f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}/"
                                 f"storage/{_segment(path)}/content", max_bytes=max(limit, 1)).content

    def vocabulary(self, what):
        """One of Coscine's lists for a new project or resource: "visibilities", "disciplines", "licenses" (K1)."""
        return self._all(f"/{what}")

    def store_types(self):
        """The resource types Askurious can put files into and Coscine lets create: [{"id", "generalType",
        "isQuotaAvailable"}] (K1)."""
        answer = self.request("GET", "/resource-types/types")
        found = answer.get("data") or []
        return [t for t in found if t.get("generalType") in STORE_TYPES and t.get("isEnabled", True)
                and t.get("canCreate", True) and t.get("id")]

    def create_project(self, body):
        return self.request("POST", "/projects", json=body).get("data") or {}

    def project_quotas(self, project_id):
        """The quota of a project per resource type: [{"resourceType": {"id"}, "allocated", "totalReserved"}]."""
        return self._all(f"/projects/{quote(project_id, safe='')}/quotas")

    def create_resource(self, project_id, body):
        return self.request("POST", f"/projects/{quote(project_id, safe='')}/resources", json=body).get("data") or {}

    def members(self, project_id):
        """The members of a project: [{"id", "role": {"id", "displayName"}, "user": {"displayName", "email"}}] (K3)."""
        return self._all(f"/projects/{quote(project_id, safe='')}/members")

    def invitations(self, project_id):
        return self._all(f"/projects/{quote(project_id, safe='')}/invitations")

    def invite(self, project_id, email, role_id):
        """Coscine sends the invitation mail; the person accepts it in Coscine (K3)."""
        return self.request("POST", f"/projects/{quote(project_id, safe='')}/invitations",
                            json={"email": email, "roleId": role_id}).get("data") or {}

    def withdraw(self, project_id, invitation_id):
        self.request("DELETE", f"/projects/{quote(project_id, safe='')}/invitations/{quote(invitation_id, safe='')}")

    def roles(self):
        return self._all("/roles")

    def publication_services(self):
        """The publication services of the person's organisation (K5): [{"displayName", "email", "description",
        "organizationRor"}]."""
        return self._all("/publication-service")

    def request_publication(self, project_id, service_ror, resource_ids, message):
        return self.request("POST", f"/projects/{quote(project_id, safe='')}/publications/requests",
                            json={"publicationServiceRorId": service_ror, "resourceIds": list(resource_ids),
                                  "message": message}).get("data") or {}

    def tokens(self):
        """The person's API tokens as Coscine lists them: name, creation and expiry date, never the token (K7)."""
        return self._all("/self/api-tokens")

    def quota(self, project_id, resource_id):
        """Used and reserved storage of a resource (K4)."""
        return self.request("GET", f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}/quota").get("data") or {}

    def put_metadata(self, project_id, resource_id, path, turtle):
        self.request("PUT", f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}/graphs/"
                            f"{_segment(path)}/metadata/content", data=turtle.encode("utf-8"),
                     headers={"Content-Type": "text/turtle"})

    def put_file(self, project_id, resource_id, path, data):
        self.request("POST", f"/projects/{quote(project_id, safe='')}/resources/{quote(resource_id, safe='')}/storage/"
                             f"{_segment(path)}/content",
                     files={"file": (path.rsplit("/", 1)[-1], data, "application/octet-stream")})


def writable(resource):
    """True for a resource Askurious can put files into: a web resource, not read-only, not in maintenance."""
    kind = (resource.get("type") or {}).get("generalType") or ""
    return kind in STORE_TYPES and not resource.get("archived") and not resource.get("maintenanceMode")


def targets(client):
    """[(project, [resource])] of the projects the token may write to, with their web resources."""
    out = []
    for p in client.projects():
        if not p.get("id"):
            continue
        try:
            if not client.may_write(p["id"]):
                continue
            found = [r for r in client.resources(p["id"]) if r.get("id") and writable(r)]
        except CoscineError:
            continue  # a project that does not answer is left out, not the whole list
        if found:
            out.append((p, found))
    return out


# --- the token at Coscine (K7) and the storage of a resource (K4) --------------------------------------------


def _when(text):
    """A date and time of Coscine's API (ISO 8601) as naive UTC, or None."""
    try:
        moment = datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def check(conn, seconds=CHECK_SECONDS):
    """What Coscine says about a stored token: {"state": "ok" | "rejected" | "unreachable", "name": the token's name
    in Coscine or "", "expires": its end as Coscine lists it, else the date read from the token}. "rejected": Coscine
    refuses it (revoked or run out). Coscine lists a person's tokens without the token itself, so the one meant is the
    one whose end matches the date in the token (or the only one)."""
    token = secrets_box.decrypt(conn.token) if conn is not None else ""
    out = {"state": "rejected", "name": "", "expires": conn.expires_at if conn is not None else None}
    if not token:
        return out
    client = Client(token, seconds=seconds)
    try:
        client.me()
    except CoscineError as e:
        out["state"] = "rejected" if e.status in (401, 403) else "unreachable"
        return out
    out["state"] = "ok"
    try:
        listed = [(t, _when(t.get("expiryDate"))) for t in client.tokens()]
    except CoscineError:
        return out
    if conn.expires_at is not None:
        listed = [(t, end) for t, end in listed if end is not None and abs((end - conn.expires_at).total_seconds()) < 86400]
    if len(listed) == 1:
        out["name"] = " ".join(str(listed[0][0].get("name") or "").split())[:200]
        out["expires"] = listed[0][1] or out["expires"]
    return out


_checked = {}  # {user id: (when, stored token, result)}: the check on "Konto", kept CHECK_KEEP seconds per process


def checked(user):
    """check() of the person's stored token for the page "Konto", kept a few minutes; None without a token. An end
    Coscine lists differently from the stored one replaces it (committed)."""
    import time

    conn = connection(user)
    if conn is None:
        return None
    kept = _checked.get(user.id)
    if kept and kept[1] == conn.token and time.monotonic() - kept[0] < CHECK_KEEP:
        return kept[2]
    result = check(conn)
    if result["state"] == "ok" and result["expires"] and result["expires"] != conn.expires_at:
        conn.expires_at = result["expires"]
        db.session.commit()
    _checked[user.id] = (time.monotonic(), conn.token, result)
    return result


def check_tokens(now):
    """Once a day (housekeeping): the tokens behind switched-on automatic uploads, checked at Coscine (K7). One that
    Coscine refuses or that runs out within TOKEN_MAIL_DAYS shows as a warning in the block "Coscine" of each survey
    it serves, and its person gets one mail per finding (when mail is on). In a request the calls to Coscine go into a
    thread of their own. Returns the number of tokens checked (None when it ran in a thread or not today)."""
    from app import mailqueue
    from app.timeutil import to_local

    if not mailqueue.once_a_day("coscine_tokens_day", to_local(now).date().isoformat()):
        return None
    from flask import has_request_context

    if not has_request_context():
        return _check_tokens()
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                _check_tokens()
            except Exception:  # noqa: BLE001 - logged; the next day tries again
                app.logger.exception("coscine: daily token check broke off")
                db.session.rollback()
            finally:
                db.session.remove()

    threading.Thread(target=work, daemon=True, name="coscine-tokens").start()
    return None


def _check_tokens():
    from app.models import Survey, User

    serving = {}  # {user id: [surveys whose automatic upload runs with that person's token]}
    for survey in db.session.scalars(db.select(Survey).filter(Survey.coscine.is_not(None))):
        auto = (survey.coscine or {}).get("auto") or {}
        if auto.get("on") and auto.get("user_id"):
            serving.setdefault(auto["user_id"], []).append(survey)
    checked_n = 0
    for user_id, surveys in serving.items():
        user = db.session.get(User, user_id)
        conn = connection(user) if user is not None else None
        if conn is None:
            continue  # auto_store names that at the next upload
        result = check(conn)
        checked_n += 1
        if result["state"] == "unreachable":
            continue
        if result["expires"] and result["expires"] != conn.expires_at:
            conn.expires_at = result["expires"]
        left = days_left(conn)
        if result["state"] == "rejected":
            finding = {"problem": "rejected"}
        elif left is not None and left <= TOKEN_MAIL_DAYS:
            finding = {"problem": "expiring", "until": conn.expires_at.date().isoformat()}
        else:
            finding = None
        key = f"{finding['problem']}:{finding.get('until', '')}" if finding else ""
        mailed = any(((s.coscine.get("auto") or {}).get("token") or {}).get("mailed") == key for s in surveys)
        for s in surveys:
            auto = {k: v for k, v in (s.coscine.get("auto") or {}).items() if k != "token"}
            if finding:
                auto["token"] = {**finding, "mailed": key if (mailed or _mail_token(user, finding, surveys)) else ""}
                mailed = mailed or bool(auto["token"]["mailed"])
            s.coscine = {**s.coscine, "auto": auto}
        db.session.commit()
    return checked_n


def _mail_token(user, finding, surveys):
    """One mail to the person whose token an automatic upload uses; True if it went out (mail on, an address)."""
    from app import brand, lang, mail
    from app.models import UserLogin
    from app.timeutil import fmt_local

    row = db.session.get(UserLogin, user.id)
    if row is None or not row.email or not mail.enabled():
        return False
    titles = ", ".join(f"„{s.title}“" for s in surveys[:5]) + (" …" if len(surveys) > 5 else "")
    with mail.in_language(lang.of(user)):
        if finding["problem"] == "rejected":
            subject = de_en("Askurious: Coscine lehnt Ihr Token ab", "Askurious: Coscine rejects your token")
            text = de_en("Guten Tag {name},\n\nCoscine lehnt das Zugriffstoken ab, das Sie in Askurious hinterlegt haben "
                         "(widerrufen oder abgelaufen). Die automatische Ablage in Coscine für {titles} läuft deshalb nicht. "
                         "Bitte in Coscine ein neues Token erzeugen und in Askurious unter „Konto › Verbindungen“ einfügen.\n\n{link}",
                         "Hello {name},\n\nCoscine rejects the access token you stored in Askurious (revoked or expired). "
                         "The automatic upload to Coscine for {titles} therefore does not run. Please create a new token in "
                         "Coscine and paste it in Askurious under “Account › Connections”.\n\n{link}",
                         name=user.name, titles=titles, link=brand.public_base())
        else:
            end = fmt_local(day_of(finding["until"]), False, "en" if lang.english() else "de")
            subject = de_en("Askurious: Ihr Coscine-Token läuft am {d} ab", "Askurious: your Coscine token expires on {d}", d=end)
            text = de_en("Guten Tag {name},\n\ndas Coscine-Token, das Sie in Askurious hinterlegt haben, läuft am {d} ab. "
                         "Danach läuft die automatische Ablage in Coscine für {titles} nicht mehr. Bitte rechtzeitig in "
                         "Coscine ein neues Token erzeugen und in Askurious unter „Konto › Verbindungen“ einfügen.\n\n{link}",
                         "Hello {name},\n\nthe Coscine token you stored in Askurious expires on {d}. After that the "
                         "automatic upload to Coscine for {titles} no longer runs. Please create a new token in Coscine in "
                         "time and paste it in Askurious under “Account › Connections”.\n\n{link}",
                         name=user.name, d=end, titles=titles, link=brand.public_base())
        mail.send(row.email, subject, text + mail.signature())
    return True


def day_of(text):
    """A date stored as text (ISO), or None."""
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def clear_token_warnings(user_id):
    """A new token for this person: the warnings of the daily check about the old one go (not committed)."""
    from app.models import Survey

    for survey in db.session.scalars(db.select(Survey).filter(Survey.coscine.is_not(None))):
        auto = (survey.coscine or {}).get("auto") or {}
        if auto.get("user_id") == user_id and "token" in auto:
            survey.coscine = {**survey.coscine, "auto": {k: v for k, v in auto.items() if k != "token"}}
    _checked.pop(user_id, None)


def _bytes(amount):
    """A quota of Coscine's API ({"value", "unit"}) in bytes, or None."""
    if not isinstance(amount, dict):
        return None
    factor = UNIT_BYTES.get(str(amount.get("unit") or "").rstrip("/").rsplit("/", 1)[-1])
    try:
        value = float(amount.get("value"))
    except (TypeError, ValueError):
        return None
    return int(value * factor) if factor and value >= 0 else None


def size_text(n):
    """A number of bytes as the page shows it (steps of 1000, like the filter "filesize")."""
    from app import units
    from app.lang import current

    size, step = float(n or 0), 0
    while size >= 1000 and step < 4:
        size, step = size / 1000, step + 1
    if not step:
        return f"{int(size)} Bytes"
    return f"{units.decimal_text(size, 1, False, current())} {('kB', 'MB', 'GB', 'TB')[step - 1]}"


def space(client, project_id, resource_id):
    """The storage of a resource: {"used", "total", "free" (bytes), "percent"}, or None where Coscine keeps no quota
    for it (a web resource) or does not answer; the upload then goes ahead as before."""
    try:
        data = client.quota(project_id, resource_id)
    except CoscineError:
        return None
    used, total = _bytes(data.get("used")), _bytes(data.get("reserved"))
    if used is None or not total:
        return None
    try:
        percent = float(data.get("usedPercentage"))
    except (TypeError, ValueError):
        percent = used * 100 / total
    return {"used": used, "total": total, "free": max(total - used, 0), "percent": round(percent, 1)}


def after(room, parts):
    """The share of the storage in use once `parts` are in (per cent, one decimal), or None without a quota."""
    if room is None:
        return None
    return round((room["used"] + sum(len(data) for _, data, _ in parts)) * 100 / room["total"], 1)


def fits(room, parts):
    """CoscineError if `parts` do not fit into the free storage: refused before the first file instead of breaking
    off halfway with a folder that stays incomplete (K4)."""
    if room is None:
        return
    need = sum(len(data) for _, data, _ in parts)
    if need > room["free"]:
        raise CoscineError(de_en("Die Ablage passt nicht in die Ressource: {need} nötig, {free} frei. Bitte die Ressource "
                                 "in Coscine vergrößern oder eine andere wählen.",
                                 "The upload does not fit into the resource: {need} needed, {free} free. Please enlarge "
                                 "the resource in Coscine or choose another one.",
                                 need=size_text(need), free=size_text(room["free"])), status=413)


# --- application profiles ------------------------------------------------------------------------------------


_TOKEN = re.compile(r'"""(?:[^"\\]|\\.|"(?!""))*"""|"(?:[^"\\\n]|\\.)*"|<[^>\s]*>|[\[\]();,]|[^\s\[\]();,"<]+')


def _tokens(text):
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return _TOKEN.findall("\n".join(out))


def _unquote(lit):
    body = lit[3:-3] if lit.startswith('"""') else lit[1:-1]
    return re.sub(r'\\(["\\nrt])', lambda m: {"n": "\n", "r": "\r", "t": "\t"}.get(m.group(1), m.group(1)), body)


class Profile:
    """The fields of an application profile: [{path, order, min, max, datatype, cls, node, options, name, help,
    single, low, high}] in the order of the profile, and the target class the metadata names."""

    def __init__(self, uri, title, turtle):
        self.uri, self.title, self.target, self.fields = uri, title, uri, []
        self.prefixes = {}
        self._parse(turtle)
        self.fields.sort(key=lambda f: (f["order"], f["name"]))
        for i, f in enumerate(self.fields):
            f["key"] = f"f{i}"  # the name of its input on the page
            f["multi"] = f["path"] in MULTI and f["max"] != 1 and not f["options"]

    def _iri(self, tok):
        if tok.startswith("<") and tok.endswith(">"):
            return tok[1:-1]
        if ":" in tok and not tok.startswith('"'):
            prefix, local = tok.split(":", 1)
            if prefix in self.prefixes:
                return self.prefixes[prefix] + local
        return tok

    def _literal(self, toks, i):
        """(value, language, datatype, next index) of the literal or number at toks[i]."""
        tok = toks[i]
        if tok.startswith('"'):
            value, lang, dtype = _unquote(tok), "", ""
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt.startswith("@"):
                return value, nxt[1:], "", i + 2
            if nxt.startswith("^^"):
                return value, "", self._iri(nxt[2:]), i + 2
            return value, lang, dtype, i + 1
        return tok, "", "", i + 1

    def _parse(self, turtle):
        toks = _tokens(turtle)
        i = 0
        while i < len(toks):
            tok = toks[i]
            if tok in ("@prefix", "PREFIX") and i + 2 < len(toks):
                self.prefixes[toks[i + 1].rstrip(":")] = toks[i + 2].strip("<>").rstrip(".").rstrip(">")
                i += 3
                continue
            if tok.startswith("@base"):
                i += 2
                continue
            if self._iri(tok) == SH + "targetClass" and i + 1 < len(toks):
                self.target = self._iri(toks[i + 1].rstrip("."))
            if self._iri(tok) == SH + "property":
                i += 1
                while i < len(toks) and toks[i] == "[":
                    i = self._block(toks, i + 1)
                    if i < len(toks) and toks[i] == ",":
                        i += 1
                continue
            i += 1

    def _block(self, toks, i):
        """Reads one [ ... ] of sh:property from toks[i] (after "["); returns the index after "]"."""
        f = {"path": "", "order": 999, "min": 0, "max": None, "datatype": "", "cls": "", "node": "", "options": [],
             "names": {}, "helps": {}, "single": True, "low": None, "high": None, "default": ""}
        depth = 0
        while i < len(toks):
            tok = toks[i]
            if tok == "]" and depth == 0:
                i += 1
                break
            if tok == "[":  # a nested node (sh:node with a blank node, sh:or …): Askurious cannot fill it
                depth += 1
                f["node"] = f["node"] or "nested"
                i += 1
                continue
            if tok == "]":
                depth -= 1
                i += 1
                continue
            if depth or tok in (";", ","):
                i += 1
                continue
            pred = self._iri(tok)
            i += 1
            if i >= len(toks):
                break
            if pred == SH + "in" and toks[i] == "(":
                i += 1
                while i < len(toks) and toks[i] != ")":
                    value, _, _, i = self._literal(toks, i)
                    f["options"].append(value)
                i += 1
                continue
            obj = toks[i]
            if obj.startswith('"') or re.fullmatch(r"[-+]?\d+(\.\d+)?", obj or ""):
                value, lang, _, i = self._literal(toks, i)
            else:
                value, lang = self._iri(obj), ""
                i += 1
            key = pred[len(SH):] if pred.startswith(SH) else pred
            if key == "path":
                f["path"] = value
            elif key in ("order", "minCount", "maxCount", "minInclusive", "maxInclusive"):
                try:
                    number = int(float(value))
                except ValueError:
                    continue
                f[{"order": "order", "minCount": "min", "maxCount": "max", "minInclusive": "low",
                   "maxInclusive": "high"}[key]] = number
            elif key == "datatype":
                f["datatype"] = value
            elif key == "class":
                f["cls"] = value
            elif key == "node":
                f["node"] = value
            elif key == "name":
                f["names"][lang or "en"] = value.strip()
            elif key == "description":
                f["helps"][lang or "en"] = value.strip()
            elif key == "defaultValue":
                f["default"] = value
            elif pred == DASH + "singleLine":
                f["single"] = value not in ("0", "false")
        if f["path"]:
            first, second = ("en", "de") if english() else ("de", "en")  # the profile's own words, in the page language
            f["name"] = f["names"].get(first) or f["names"].get(second) or f["path"].rstrip("/").rsplit("/", 1)[-1]
            f["help"] = f["helps"].get(first) or f["helps"].get(second) or ""
            self.fields.append(f)
        return i

    def offered(self, f):
        """True for a field Askurious shows: a literal (text, number, date) or a choice of the profile."""
        return not f["node"] and not (f["cls"] and not f["options"])

    def missing(self):
        """The required fields Askurious cannot offer (a vocabulary, a nested form); [] if it can fill all."""
        if not self.fields:  # shapes kept elsewhere (sh:node, named shapes): not read here
            return [{"name": de_en("das ganze Profil (Aufbau, den Askurious nicht liest)",
                                   "the whole profile (a structure Askurious does not read)")}]
        return [f for f in self.fields if f["min"] and not self.offered(f)
                and not (f["path"] == DCT + "type" and f["cls"] == DCMITYPE)]


def load_profile(client, uri):
    title, turtle = client.profile(uri)
    if not turtle:
        raise CoscineError(de_en("Das Metadatenprofil der Ressource ließ sich nicht lesen.", "The metadata profile of the resource could not be read."))
    return Profile(uri, title, turtle)


# --- the values ----------------------------------------------------------------------------------------------


def _counts(survey):
    from app.models import Response as R

    tally = survey.tally()
    raw = sum(n for (source, preview, _), n in tally.items() if source == "real" and not preview)
    clean = sum(n for (source, preview, status), n in tally.items()
                if source == "real" and not preview and status == R.COMPLETED)
    return raw, clean


def suggestions(survey, project, user):
    """{path: value} Askurious knows for a survey; lists for fields that take several values."""
    names = [m.display_name for m in survey.members if m.display_name] or [user.display_name]
    description = " ".join((survey.description or "").split()) or survey.title
    out = {DCT + "title": survey.title, DCT + "creator": names, DCT + "created": local_today().isoformat(),
           DCT + "contributor": user.display_name, DCT + "isPartOf": project.get("displayName") or project.get("name") or "",
           DCT + "description": description, "http://schema.org/about": description,
           DCT + "type": "Qualitativ" if survey.interview is not None else "Quantitativ"}
    if survey.interview is None:
        raw, clean = _counts(survey)
        if raw:
            out[N_RAW] = str(raw)
        if clean:
            out[N_CLEAN] = str(clean)
        if survey.opened_at:
            from app.lang import current
            from app.timeutil import fmt

            start = fmt(survey.opened_at, False, current())
            out[PERIOD] = (de_en("{a} bis {b}", "{a} to {b}", a=start, b=fmt(survey.closed_at, False, current()))
                           if survey.closed_at else de_en("ab {a}", "from {a}", a=start))
    return out


def form_fields(profile, survey, project, user):
    """[(field, value as shown)] of the fields shown on the page: what this survey remembered for the profile,
    else what Askurious knows; a value of several parts is shown with "; " between them."""
    known = suggestions(survey, project, user)
    kept = ((survey.coscine or {}).get("values") or {}).get(profile.uri) or {}
    out = []
    for f in profile.fields:
        if not profile.offered(f):
            continue
        value = kept.get(f["path"], known.get(f["path"], ""))
        if isinstance(value, list):
            value = "; ".join(value)
        if f["options"] and value not in f["options"]:
            value = ""  # the person chooses, e.g. whether an ethics committee decided
        out.append((f, value))
    return out


def read_values(profile, form):
    """{path: value} from the sent form, and the list of problems (empty if all is well)."""
    values, problems = {}, []
    for f in profile.fields:
        if not profile.offered(f):
            continue
        value = (form.get(f["key"]) or "").strip()
        if f["single"] and not f["options"]:
            value = " ".join(value.split())
        value = value[:4000]
        if f["options"] and value and value not in f["options"]:
            value = ""
        if f["datatype"] == XSD + "integer" and value:
            try:
                number = int(value)
            except ValueError:
                problems.append(de_en("„{name}“ braucht eine ganze Zahl.", "“{name}” needs a whole number.", name=f["name"]))
                continue
            if (f["low"] is not None and number < f["low"]) or (f["high"] is not None and number > f["high"]):
                problems.append(de_en("„{name}“ muss zwischen {low} und {high} liegen.", "“{name}” must be between {low} and {high}.",
                                       name=f["name"], low=f["low"], high=f["high"]))
            value = str(number)
        if f["datatype"] == XSD + "date" and value:
            try:
                value = date.fromisoformat(value).isoformat()
            except ValueError:
                problems.append(de_en("„{name}“ braucht ein Datum.", "“{name}” needs a date.", name=f["name"]))
                continue
        if f["min"] and not value:
            problems.append(de_en("Bitte „{name}“ ausfüllen.", "Please fill in “{name}”.", name=f["name"]))
        values[f["path"]] = value
    return values, problems


def _lit(value, datatype):
    text = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{text}"^^<{datatype}>' if datatype else f'"{text}"'


def turtle(profile, values, title_suffix=""):
    """The metadata of one file as Turtle: a blank node of the profile's target class with the values."""
    lines = [f"_:m a <{profile.target}>"]
    for f in profile.fields:
        if f["path"] == DCT + "type" and f["cls"] == DCMITYPE and not f["options"]:
            lines.append(f"<{f['path']}> <{DCMITYPE}Dataset>")
            continue
        if not profile.offered(f):
            continue
        value = values.get(f["path"], "")
        if not value:
            continue
        parts = [value]
        if f["multi"] and ";" in value:
            parts = [p.strip() for p in value.split(";") if p.strip()]
        if f["path"] == DCT + "title" and title_suffix:
            parts = [f"{parts[0]} – {title_suffix}"]
        datatype = "" if f["options"] else f["datatype"]
        for part in parts[: f["max"] or None]:
            lines.append(f"<{f['path']}> {_lit(part, datatype)}")
    return " ;\n    ".join(lines) + " .\n"


# --- what goes along -----------------------------------------------------------------------------------------


def choices(survey):
    """[(key, label, note, ticked)] of what can go into Coscine for this survey."""
    from app import export, interview_versions, prereg
    from app.lang import current
    from app.timeutil import fmt_local

    out = []
    if survey.interview is None:
        out.append(("paket", de_en("Datenpaket", "Data package"),
                    de_en("echte, abgeschlossene Teilnahmen (data.csv), Codebuch, Fragebogen, Befragungsdatei, README, "
                          "Konstrukte, Langformate und die Bilder des Fragebogens, wie bei Zenodo",
                          "real, completed participations (data.csv), codebook, questionnaire, survey file, README, "
                          "constructs, long formats and the images of the questionnaire, as with Zenodo"), True))
        if export.open_columns(survey):
            out.append(("offen", de_en("Freitexte und genaue Angaben mitnehmen", "Include free texts and exact details"),
                        de_en("Freitexte, genaue Kartenpositionen, Postleitzahlen und taggenaue Datumsangaben; sonst "
                              "weggelassen wie bei Zenodo",
                              "free texts, exact map positions, postcodes and exact dates; otherwise left out as with "
                              "Zenodo"), False))
        out.append(("abbrueche", de_en("Abgebrochene Teilnahmen mitnehmen", "Include broken-off participations"),
                    de_en("in data.csv mit ihrem Status", "in data.csv with their status"), False))
        # a frozen preregistration holds the full wording, also of constructs whose terms allow passing it on only to
        # the participants (APA PsycTests); its seal forbids leaving it out, so it is not ticked then and says why
        # (copyright audit 2026-10-16, U5)
        licensed = bool(export.withheld(survey))
        for name, version, at, _ in prereg.listing(survey):
            note = de_en("eingefroren am {d}", "frozen on {d}", d=fmt_local(at, lang=current()))
            if licensed:
                note += de_en("; enthält Instrumentwortlaut ohne allgemeine Veröffentlichungsfreigabe: nur im "
                              "berechtigten privaten Studienkontext ablegen",
                              "; contains instrument wording without general publication clearance: store only "
                              "within an authorized private study context")
            out.append((f"prereg:{name}", de_en("Präregistrierung Fassung {v}", "Pre-registration revision {v}", v=version),
                        note, not licensed))
    else:
        for v in interview_versions.listing(survey):
            out.append((f"fassung:{v['name']}", de_en("Fassung {n}", "Revision {n}", n=v["n"]),
                        f"{v['at'][:10]} · {v.get('by') or ''}", True))
    return out


def requires_public_image_rights(resource):
    """Only a verified private/project-only target is exempt; unknown visibility is not a private target."""
    visibility = ((resource.get("visibility") or {}).get("displayName") or "").strip().lower()
    return visibility not in {"private", "project members", "project", "projektmitglieder"}


def files(survey, ticked, require_public_image_rights=True):
    """[(name, bytes, label)] of the ticked parts, plus checksums.sha256 over all of them (app/packages.py)."""
    from app import asset_rights, interview_versions, prereg, zenodo

    out = []
    if survey.interview is None and "paket" in ticked:
        # the unfinished participations in data.csv and in every long format alike: resp_id stays the same person
        try:
            package = zenodo.package(survey, open_answers="offen" in ticked, include_incomplete="abbrueche" in ticked,
                                     require_public_image_rights=require_public_image_rights)
        except asset_rights.RightsError as exc:
            raise CoscineError(str(exc)) from exc
        for name, data in package:
            out.append((name, data, name))
    for key in ticked:
        kind, _, name = key.partition(":")
        path = (prereg.path(survey, name) if kind == "prereg" and survey.interview is None
                else interview_versions.path(survey, name) if kind == "fassung" and survey.interview is not None else None)
        if path:
            if require_public_image_rights and kind == "fassung":
                from app import interview

                version = next((v for v in interview.state(survey)["versions"] if v.get("name") == name), {})
                try:
                    interview_versions.verify_material_license(version, path)
                except zenodo.ZenodoError as exc:
                    raise CoscineError(str(exc)) from exc
            with open(path, "rb") as fh:
                folder = "praeregistrierung" if kind == "prereg" else "fassungen"
                content = fh.read()
                if require_public_image_rights and kind == "prereg":
                    try:
                        asset_rights.require_public_snapshot(survey, content)
                    except asset_rights.RightsError as exc:
                        raise CoscineError(str(exc)) from exc
                out.append((f"{folder}/{name}", content, name))
    if out:
        from app import packages

        sums = packages.checksum_lines([(name, data) for name, data, _ in out])
        out.append((packages.CHECKSUMS, sums.encode("ascii"), de_en("Prüfsummen", "Checksums")))
    return out


def upload(survey, client, project, resource, profile, values, ticked, person, room=None):
    """Puts the ticked parts with their metadata into a new folder; returns the entry for survey.coscine. `room`
    (space()) refuses parts that do not fit before the first file (K4)."""
    parts = files(survey, ticked, require_public_image_rights=requires_public_image_rights(resource))
    if not parts:
        raise CoscineError(de_en("Nichts zum Ablegen ausgewählt.", "Nothing selected to store."))
    fits(room, parts)
    entry = put_parts(survey.uid, parts, client, project, resource, profile, values, person)
    return {**entry, "space": after(room, parts)} if room is not None else entry


def put_parts(uid, parts, client, project, resource, profile, values, person):
    """upload() for parts already read ([(name, bytes, label)]): only Coscine's API, nothing of the database, so the
    automatic upload can run it in a thread of its own (I13)."""
    from app.timeutil import to_local

    now = to_local(utcnow())
    stamp = f"{now:%Y-%m-%d_%H%M%S}"
    folder = f"askurious/{uid}/{stamp}"
    entry = {"at": utcnow().isoformat(timespec="seconds"), "shown": f"{now:%d.%m.%Y %H:%M}", "by": person,
             "by_id": getattr(current_user, "id", None) if has_request_context() else None, "folder": folder,
             "files": [], "project": project.get("displayName") or project.get("name") or "",
             "resource": resource.get("displayName") or resource.get("name") or "", "pid": resource.get("pid") or "",
             # the ids, so the upload can be checked in Coscine later (K2)
             "project_id": project.get("id") or "", "resource_id": resource.get("id") or ""}
    for name, data, label in parts:
        path = f"{folder}/{name}"
        try:
            client.put_metadata(project["id"], resource["id"], path, turtle(profile, values, label))
            client.put_file(project["id"], resource["id"], path, data)
        except CoscineError as e:
            if not entry["files"]:
                raise
            # some files already lie in Coscine: the folder stays incomplete, without checksums.sha256 (in a WORM
            # resource for good); the caller keeps the entry as "partial" and the block "Coscine" names it (E-10)
            raise PartialUpload(de_en("{e} Abgebrochen nach {n} von {total} Dateien; der Ordner {folder} in Coscine ist "
                                      "unvollständig (ohne {last})",
                                      "{e} Stopped after {n} of {total} files; the folder {folder} in Coscine is "
                                      "incomplete (without {last})",
                                      e=e, n=len(entry["files"]), total=len(parts), folder=folder, last=parts[-1][0])
                                + (de_en(" und lässt sich in einer WORM-Ressource nicht mehr entfernen.",
                                         " and can no longer be removed in a WORM resource.") if is_worm(resource) else "."),
                                {**entry, "partial": True, "planned": len(parts), "failed": name, "worm": is_worm(resource)}) from e
        entry["files"].append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return entry


# --- a new project and resource from Askurious (K1) ---------------------------------------------------------

# the option of a resource type in Coscine's request for a new resource
TYPE_OPTIONS = {"dsNrwWeb": "dataStorageNrwWebResourceTypeOptions", "dsNrwS3": "dataStorageNrwS3ResourceTypeOptions",
                "dsNrwS3Worm": "dataStorageNrwS3WormResourceTypeOptions", "rds": "rdsResourceTypeOptions"}
GIB = "https://qudt.org/vocab/unit/GibiBYTE"


def _pick(items, *words):
    """The first item whose display name contains one of `words` (case ignored), else None."""
    for word in words:
        for item in items:
            names = " ".join(str(item.get(k) or "") for k in ("displayName", "displayNameDe", "displayNameEn")).lower()
            if word.lower() in names:
                return item
    return None


def new_options(client, survey):
    """What the page "Neues Projekt in Coscine" offers: the person's projects (to put a sub-project or a resource
    into), disciplines, licences, resource types and the suggestions (E1, E2)."""
    me = client.me()
    projects = []
    for p in client.projects():
        try:
            if p.get("id") and client.may_write(p["id"]):
                projects.append(p)
        except CoscineError:
            continue
    disciplines = client.vocabulary("disciplines")
    licenses = client.vocabulary("licenses")
    types = client.store_types()
    last = next((u for u in (survey.coscine or {}).get("uploads") or [] if u.get("files")), None)
    size = sum(int(f.get("size") or 0) for f in last["files"]) if last else 0
    gb = max(NEW_GB_MIN, -(-size * 10 // 2 ** 30))
    social = _pick(disciplines, "Sozialwissenschaft", "Social Science", "Sozial", "Social")
    cc_by = _pick(licenses, "CC BY 4.0", "Creative Commons Attribution 4.0", "CC-BY-4.0")
    preferred = next((t for t in types if t.get("generalType") == "dsNrwS3"), types[0] if types else None)
    from app.timeutil import to_local

    start = to_local(survey.opened_at).date() if survey.opened_at else to_local(utcnow()).date()
    end = to_local(survey.closed_at).date() if survey.closed_at else _a_year_after(start)
    return {"me": me, "organizations": [o for o in me.get("organizations") or [] if o.get("uri")],
            "projects": projects, "disciplines": disciplines, "licenses": licenses, "types": types,
            # keyed like the fields of the form, so a refused form shows again as sent
            "defaults": {"wohin": "neu", "name": survey.title[:200],
                         "beschreibung": " ".join((survey.description or "").split()) or survey.title,
                         "leitung": me.get("displayName") or "", "disziplin": (social or {}).get("id") or "",
                         "lizenz": (cc_by or {}).get("id") or "", "typ": (preferred or {}).get("id") or "",
                         "gb": gb, "ressource": de_en("Befragungsdaten", "Survey data"), "profil": RISK_PROFILE,
                         "start": start.isoformat(), "ende": max(start, end).isoformat()}}


def _a_year_after(day):
    """The same day a year later (28 February for 29 February): the end a new project gets when the survey has none."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def _visibility(client):
    """Coscine's id of the visibility "project members": a new project or resource is never public (K1)."""
    found = client.vocabulary("visibilities")
    chosen = _pick(found, "project members", "projektmitglieder", "project")
    if chosen is None or not chosen.get("id"):
        raise CoscineError(de_en("Coscine nennt keine Sichtbarkeit „Projektmitglieder“.",
                                 "Coscine names no visibility “project members”."))
    return chosen["id"]


def free_gb(client, project_id):
    """{resource type id: whole GB still free in the project} (allocated minus reserved), or None when Coscine does
    not say. A new project gets the default quota of the person's organisation, which can be 0 for a type."""
    try:
        quotas = client.project_quotas(project_id)
    except CoscineError:
        return None
    out = {}
    for q in quotas:
        type_id = (q.get("resourceType") or {}).get("id")
        allocated = _bytes(q.get("allocated"))
        if type_id and allocated is not None:
            out[type_id] = max(0, (allocated - (_bytes(q.get("totalReserved")) or 0)) // 2 ** 30)
    return out


def _type_name(kind):
    return {"dsNrwWeb": "Web", "dsNrwS3": "S3", "dsNrwS3Worm": "WORM", "rds": "RDS"}.get(kind["generalType"],
                                                                                      kind["generalType"])


def _fitting(kind, gb, free, types):
    """The resource type and size to create given the free quota: the chosen type if it has room, else with less GB,
    else another changeable type (never WORM, which cannot be undone) with room. Returns (type, gb, note) or raises."""
    if free is None:
        return kind, gb, ""
    have = free.get(kind["id"], 0)
    if have >= gb:
        return kind, gb, ""
    if have >= 1:
        return kind, have, de_en("Die Ressource hat {n} GB statt {w} GB: Mehr ist im Projekt für diesen Typ nicht frei.",
                                 "The resource has {n} GB instead of {w} GB: no more is free in the project for this type.",
                                 n=have, w=gb)
    others = sorted((t for t in types if t["generalType"] != "dsNrwS3Worm" and free.get(t["id"], 0) >= 1),
                    key=lambda t: -free[t["id"]])
    if others:
        other = others[0]
        n = min(gb, free[other["id"]])
        return other, n, de_en("Für „{a}“ hat das Projekt kein Kontingent; die Ressource ist deshalb vom Typ „{b}“ "
                               "({n} GB).", "The project has no quota for “{a}”, so the resource is of type “{b}” ({n} GB).",
                               a=_type_name(kind), b=_type_name(other), n=n)
    raise CoscineError(de_en("Das Projekt hat in Coscine kein freies Speicherkontingent für eine Ressource. Mehr "
                             "Kontingent lässt sich in Coscine beim Projekt unter „Quota“ beantragen.",
                             "The project has no free storage quota in Coscine for a resource. More quota can be "
                             "requested in Coscine at the project under “Quota”."))


def create_target(client, form, options):
    """Creates what the form asks for: a project (or sub-project) with a resource, or a resource in a project of the
    person. Returns (project id, resource id). A project that was created stays when its resource fails; the error
    then says so, and the form offers the project for the next try."""
    where_to = form.get("wohin") or "neu"
    name = " ".join((form.get("name") or "").split())[:200]
    discipline = form.get("disziplin") or ""
    if discipline not in {d.get("id") for d in options["disciplines"]}:
        raise CoscineError(de_en("Bitte eine Disziplin wählen.", "Please choose a discipline."))
    type_id = form.get("typ") or ""
    kind = next((t for t in options["types"] if t["id"] == type_id), None)
    if kind is None:
        raise CoscineError(de_en("Bitte einen Ressourcentyp wählen.", "Please choose a resource type."))
    try:
        gb = int(form.get("gb") or 0)
    except ValueError:
        gb = 0
    if not 1 <= gb <= 10000:
        raise CoscineError(de_en("Die Größe der Ressource muss zwischen 1 und 10000 GB liegen.",
                                 "The size of the resource must be between 1 and 10000 GB."))
    profile = form.get("profil") if form.get("profil") in (RISK_PROFILE, BASE_PROFILE) else RISK_PROFILE
    license_id = form.get("lizenz") or ""
    if license_id and license_id not in {item.get("id") for item in options["licenses"]}:
        license_id = ""
    resource_name = " ".join((form.get("ressource") or "").split())[:200]
    if not resource_name:
        raise CoscineError(de_en("Bitte die Ressource benennen.", "Please name the resource."))
    visibility = _visibility(client)
    mine = {p["id"] for p in options["projects"]}
    created = False
    if where_to.startswith("in:"):
        project_id = where_to[3:]
        if project_id not in mine:
            raise CoscineError(de_en("In dieses Projekt darf das Coscine-Konto nicht schreiben.",
                                     "The Coscine account may not write to this project."))
    else:
        parent = where_to[4:] if where_to.startswith("sub:") else ""
        if parent and parent not in mine:
            raise CoscineError(de_en("Unter diesem Projekt darf das Coscine-Konto nichts anlegen.",
                                     "The Coscine account may not create anything under this project."))
        # one responsible organisation, the first of the profile, as Coscine's own form does
        organizations = [{"uri": o["uri"], "responsible": i == 0} for i, o in enumerate(options["organizations"])]
        pi = " ".join((form.get("leitung") or "").split())[:500]
        description = " ".join((form.get("beschreibung") or "").split())[:5000]
        if not (name and description and pi and organizations):
            raise CoscineError(de_en("Bitte Name, Beschreibung und Projektleitung angeben; Coscine braucht außerdem eine "
                                     "Einrichtung in Ihrem Coscine-Profil.",
                                     "Please give name, description and principal investigator; Coscine also needs an "
                                     "organisation in your Coscine profile."))
        body = {"name": name, "displayName": name[:25], "description": description, "principleInvestigators": pi,
                "disciplines": [{"id": discipline}], "organizations": organizations, "visibility": {"id": visibility},
                "keywords": ["Askurious"]}
        # Coscine stores both dates without a default: a project without them fails there with HTTP 500 (2026-10-20)
        try:
            start = date.fromisoformat(form.get("start") or "")
            end = date.fromisoformat(form.get("ende") or "")
        except ValueError:
            raise CoscineError(de_en("Bitte Beginn und Ende des Projekts angeben.",
                                     "Please give the start and end of the project.")) from None
        if end < start:
            raise CoscineError(de_en("Das Ende des Projekts liegt vor seinem Beginn.",
                                     "The end of the project is before its start."))
        body.update(startDate=start.isoformat() + "T00:00:00Z", endDate=end.isoformat() + "T00:00:00Z")
        if parent:
            body.update(parentId=parent, copyOwnersFromParent=True)
        try:
            project_id = client.create_project(body).get("id") or ""
        except CoscineError:  # the entries Coscine may not know, for the log (no personal data but the organisations)
            logging.getLogger(__name__).warning("Coscine refused a new project: organizations %s, discipline %s, parent %s",
                                                [o["uri"] for o in organizations], discipline, parent or "-")
            raise
        if not project_id:
            raise CoscineError(de_en("Coscine hat das Projekt nicht angelegt.", "Coscine did not create the project."))
        created = True
    try:
        kind, gb, note = _fitting(kind, gb, free_gb(client, project_id), options["types"])
    except CoscineError as e:
        if created:
            raise CoscineError(de_en("Das Projekt ist in Coscine angelegt, die Ressource nicht: {e} Danach lässt sie sich "
                                     "hier mit „Neue Ressource in …“ anlegen.",
                                     "The project was created in Coscine, the resource was not: {e} Then it can be "
                                     "created here with “New resource in …”.", e=e)) from e
        raise
    body = {"name": resource_name, "displayName": resource_name[:25],
            "description": " ".join((form.get("beschreibung") or "").split())[:5000] or resource_name,
            "keywords": ["Askurious"], "disciplines": [{"id": discipline}], "visibility": {"id": visibility},
            "resourceTypeId": kind["id"], "applicationProfile": {"uri": profile},
            "resourceTypeOptions": {TYPE_OPTIONS.get(kind["generalType"], "dataStorageNrwWebResourceTypeOptions"):
                                    {"quota": {"value": gb, "unit": GIB}}}}
    if license_id:
        body["license"] = {"id": license_id}
    try:
        resource_id = client.create_resource(project_id, body).get("id") or ""
        if not resource_id:
            raise CoscineError(de_en("Coscine hat die Ressource nicht angelegt.", "Coscine did not create the resource."))
    except CoscineError as e:
        if created:
            raise CoscineError(de_en("Das Projekt ist in Coscine angelegt, die Ressource nicht: {e} Sie lässt sich hier "
                                     "mit „Neue Ressource in …“ erneut anlegen.",
                                     "The project was created in Coscine, the resource was not: {e} It can be created "
                                     "again here with “New resource in …”.", e=e), status=e.status) from e
        raise
    return project_id, resource_id, note


# --- who sees the files: inviting people of the survey (K3) -------------------------------------------------

# the role in Coscine for the role in Askurious (E5): "Owner" is never given from Askurious
COSCINE_ROLE = {"admin": "member", "editor": "member", "viewer": "guest"}


def team(survey):
    """The people with a right on the survey, directly or through a group of its organisation: [{"user", "role",
    "groups": [names], "email"}] by name. The e-mail is the person's sign-in address (none for an ORCID-only
    account); `groups` [(group id, name)] says where a right comes from (E6: a whole group at once)."""
    from app import access, orgs
    from app.models import UserLogin

    people = {}
    for m in survey.members:
        if m.user is not None:
            people[m.user_id] = {"user": m.user, "role": m.role, "groups": []}
    home = orgs.of(survey)
    for link in survey.group_links:
        if orgs.of(link.group) != home:
            continue
        given = link.role if link.role in access.GROUP_ROLES else "viewer"
        for gm in link.group.members:
            if gm.user is None:
                continue
            entry = people.setdefault(gm.user_id, {"user": gm.user, "role": given, "groups": []})
            if access._RANK[given] > access._RANK[entry["role"]]:
                entry["role"] = given
            entry["groups"].append((link.group_id, link.group.name))
    for entry in people.values():
        row = db.session.get(UserLogin, entry["user"].id)
        entry["email"] = (row.email or "").strip().lower() if row is not None else ""
    return sorted(people.values(), key=lambda e: (e["user"].display_name or "").lower())


def role_ids(client):
    """{"member": id, "guest": id} of Coscine's roles."""
    found = {str(r.get("displayName") or "").strip().lower(): r.get("id") for r in client.roles()}
    return {"member": found.get("member") or found.get("mitglied"), "guest": found.get("guest") or found.get("gast") or GUEST_ROLE}


def coscine_people(client, project_id):
    """({e-mail: role name} of the members, {e-mail: invitation} of the open invitations) of a project."""
    members = {}
    for m in client.members(project_id):
        email = str((m.get("user") or {}).get("email") or "").strip().lower()
        if email:
            members[email] = (m.get("role") or {}).get("displayName") or ""
    invited = {}
    for i in client.invitations(project_id):
        email = str(i.get("email") or "").strip().lower()
        if email:
            invited[email] = i
    return members, invited


# --- publication request (K5) --------------------------------------------------------------------------------


def publication_blocked(survey):
    """Why the resource of this survey must not be published (instrument wording without clearance, images without
    documented rights for public use), or "" (the same checks as a public upload)."""
    from app import asset_rights, export

    if survey.interview is None and export.withheld(survey):
        return de_en("Die Ablagen können Instrumentwortlaut ohne allgemeine Veröffentlichungsfreigabe enthalten "
                     "(Präregistrierung).",
                     "The uploads may contain instrument wording without general publication clearance "
                     "(pre-registration).")
    try:
        asset_rights.require_public(survey)
    except asset_rights.RightsError as e:
        return str(e)
    return ""


def publication_message(survey, entry):
    """The message to the publication service, filled in by Askurious (K5)."""
    raw, clean = _counts(survey) if survey.interview is None else (0, 0)
    lines = [de_en("Bitte um Veröffentlichung der Ressource „{r}“ mit DOI.", "Please publish the resource “{r}” with a DOI.",
                   r=entry.get("resource") or ""),
             de_en("Befragung: {t}", "Survey: {t}", t=survey.title),
             de_en("Ordner: {f}", "Folder: {f}", f=entry.get("folder") or "")]
    if clean:
        lines.append(de_en("Abgeschlossene Teilnahmen: {n}", "Completed participations: {n}", n=clean))
    lines.append(de_en("Jede Datei ist mit ihrer SHA-256 in checksums.sha256 im Ordner aufgeführt.",
                       "Each file is listed with its SHA-256 in checksums.sha256 in the folder."))
    return "\n".join(lines)


# --- checking uploads in Coscine (K2) -----------------------------------------------------------------------


def where(state, entry):
    """(project id, resource id) of an upload; older entries carry no ids and count as the survey's last target
    ((None, None) without one)."""
    if entry.get("project_id") and entry.get("resource_id"):
        return entry["project_id"], entry["resource_id"]
    return (state.get("project"), state.get("resource")) if state.get("resource") else (None, None)


def compare(entry, listed, sums=None):
    """[{"name", "state", "size", "found"}] of an upload against what Coscine lists under its folder: "ok", "missing",
    "changed" (another size, or with `sums` {name: SHA-256} another checksum) and "extra" (in Coscine only)."""
    prefix = entry["folder"].rstrip("/") + "/"
    found = {item["path"][len(prefix):]: item for item in listed if item["path"].startswith(prefix)}
    rows = []
    for f in entry.get("files") or []:
        item = found.pop(f["name"], None)
        if item is None:
            state = "missing"
        elif f.get("size") is not None and item.get("size") is not None and int(item["size"]) != int(f["size"]):
            state = "changed"
        elif sums is not None and f.get("sha256") and sums.get(f["name"]) not in (None, f["sha256"]):
            state = "changed"
        else:
            state = "ok"
        rows.append({"name": f["name"], "state": state, "size": f.get("size"), "found": (item or {}).get("size")})
    for name, item in sorted(found.items()):
        rows.append({"name": name, "state": "extra", "size": None, "found": item.get("size")})
    return rows


def check_upload(client, project_id, resource_id, entry, deep=False):
    """Compares an upload with Coscine: (rows of compare(), deep checked?). `deep` downloads the files (at most
    DEEP_LIMIT bytes together) and compares their SHA-256 with the ones noted at the upload (E4)."""
    listed = client.listing(project_id, resource_id, entry["folder"])
    sums = None
    total = sum(int(f.get("size") or 0) for f in entry.get("files") or [])
    if deep and total <= DEEP_LIMIT:
        sums, prefix = {}, entry["folder"].rstrip("/") + "/"
        present = {item["path"][len(prefix):] for item in listed if item["path"].startswith(prefix)}
        for f in entry.get("files") or []:
            if f["name"] in present:
                data = client.download(project_id, resource_id, prefix + f["name"], int(f.get("size") or 0) + 1024 * 1024)
                sums[f["name"]] = hashlib.sha256(data).hexdigest()
    return compare(entry, listed, sums), sums is not None


def folders(client, project_id, resource_id, uid):
    """{folder: [files]} of all uploads of a survey in a resource (askurious/<uid>/<date_time>/), also those of other
    persons or older than the list in Askurious (K2)."""
    out = {}
    base = f"askurious/{uid}/"
    for item in client.listing(project_id, resource_id, base, depth=4):
        rest = item["path"][len(base):] if item["path"].startswith(base) else ""
        stamp, _, name = rest.partition("/")
        if stamp and name:
            out.setdefault(base + stamp, []).append({**item, "name": name})
    return dict(sorted(out.items(), reverse=True))


def kind_label(resource):
    """"S3" or "WORM, unveränderlich" for those resources, "" for a web resource."""
    return (KIND_LABELS_EN if english() else KIND_LABELS).get((resource.get("type") or {}).get("generalType") or "", "")


def is_worm(resource):
    return (resource.get("type") or {}).get("generalType") == "dsNrwS3Worm"


# --- the automatic upload (optional) -------------------------------------------------------------------------


def auto_label(survey):
    return (de_en("bei jeder neuen Fassung", "with each new revision") if survey.interview is not None
            else de_en("bei jedem Start der Feldphase", "at each start of fieldwork"))


def stop_auto(survey, user_id=None):
    """Turn the automatic upload off if it runs with the token of this account (any account if None), with the
    name it shows: the person left the survey, was removed or deleted the account (audit 2026-10-05, L-9, L-11).
    Returns True if it was on for them. Does not commit."""
    auto = (survey.coscine or {}).get("auto") or {}
    if not auto.get("user_id") or (user_id is not None and auto.get("user_id") != user_id):
        return False
    survey.coscine = {**survey.coscine, "auto": {"on": False}}
    return True


def _auto_after_target(state, project_id, resource_id, user_id):
    """The "auto" entry after a manual upload to (project_id, resource_id) by `user_id`: ({"auto": …} or {}, switched
    off?). The target moves with the person whose token the automatic upload uses; anyone else moving it switches the
    automatic upload off, so nobody's token writes into a target they did not choose (Abgleich 2026-10-15 H2)."""
    auto = state.get("auto") or {}
    if not auto.get("on") or (project_id, resource_id) == (state.get("project"), state.get("resource")):
        return {}, False
    if auto.get("user_id") == user_id:
        return {"auto": {**auto, "project": project_id, "resource": resource_id}}, False
    return {"auto": {"on": False}}, True


def auto_store(survey, key, background=None):
    """Puts one new part (key "prereg:<name>" or "fassung:<name>") into Coscine if the survey has the automatic
    upload ticked. Returns None if it is not ticked, else (True, message) or (False, message); commits either way.
    Called after the start or the version is committed: nothing of Coscine can undo them. Only with the token of
    a person who is still admin of the survey; otherwise the automatic upload goes off (L-9).

    What needs the database (the checks, the token, the files) happens here; the calls to Coscine (project, resource,
    profile, metadata and content per file, up to 120 s each) run in a thread of their own after the commit, so the
    page does not wait for them (I13). The block "Coscine" shows "läuft" meanwhile and the outcome afterwards; the
    thread holds no database session while it waits for Coscine. `background` False runs it in the request (tests)."""
    from app.models import User

    state = dict(survey.coscine or {})
    auto = state.get("auto") or {}
    if not auto.get("on"):
        return None
    from app import export

    if key.startswith("prereg:") and export.withheld(survey):
        # the frozen file holds the wording of licensed scales (APA PsycTests): never uploaded without a person
        # deciding it (copyright audit 2026-10-16, U5)
        return False, de_en("Die Präregistrierung wurde nicht automatisch nach Coscine geladen: Sie enthält "
                            "Instrumentwortlaut ohne allgemeine Veröffentlichungsfreigabe. Bitte bei Bedarf selbst "
                            "im berechtigten privaten Studienkontext ablegen.",
                            "The pre-registration was not uploaded to Coscine automatically: it contains instrument "
                            "wording without general publication clearance. If needed, store it manually within an "
                            "authorized private study context.")
    try:
        user = db.session.get(User, auto.get("user_id"))
        if user is not None and not survey.can(user, "admin"):
            auto = {"on": False}  # nobody's token goes on working for a survey they no longer run
            raise CoscineError(de_en("Die Person, die sie eingeschaltet hat, ist nicht mehr Admin der Befragung. Sie ist "
                                     "deshalb ausgeschaltet; ein Admin kann sie mit dem eigenen Coscine-Token wieder einschalten.",
                                     "The person who switched it on is no longer an admin of the survey. It is therefore "
                                     "switched off; an admin can switch it on again with their own Coscine token."))
        if user is None:  # the account was deleted (accounts.delete turns it off; older entries here)
            auto = {"on": False}
            raise CoscineError(de_en("Das Konto der Person, die sie eingeschaltet hat, gibt es nicht mehr. Sie ist deshalb "
                                     "ausgeschaltet.",
                                     "The account of the person who switched it on no longer exists. It is therefore "
                                     "switched off."))
        if (auto.get("resource") or auto.get("project")) and (auto.get("project"), auto.get("resource")) != (
                state.get("project"), state.get("resource")):
            # another admin moved the target by hand: the token of X does not write where X did not choose (H2)
            auto = {"on": False}
            raise CoscineError(de_en("Das Ziel wurde seit dem Einschalten geändert. Sie ist deshalb ausgeschaltet; ein Admin "
                                     "kann sie mit dem eigenen Coscine-Token für das neue Ziel wieder einschalten.",
                                     "The target has changed since it was switched on. It is therefore switched off; an "
                                     "admin can switch it on again for the new target with their own Coscine token."))
        if connection(user) is None:
            raise CoscineError(de_en("Das Coscine-Token von {who} ist nicht mehr hinterlegt.",
                                     "The Coscine token of {who} is no longer stored.",
                                     who=auto.get("name") or de_en("der Person, die sie eingeschaltet hat",
                                                                   "the person who switched it on")))
        client = client_for(user)
        resource = client.resource(state["project"], state["resource"])
        public_rights = requires_public_image_rights(resource)
        parts = files(survey, {key}, require_public_image_rights=public_rights)
        if not parts:
            raise CoscineError(de_en("Nichts zum Ablegen ausgewählt.", "Nothing selected to store."))
        job = {"project": state["project"], "resource": state["resource"], "values": dict(state.get("values") or {}),
               "uid": survey.uid, "parts": parts, "person": auto.get("name") or "", "public_image_rights": public_rights}
    except (CoscineError, KeyError) as e:
        text = str(e) if isinstance(e, CoscineError) else de_en("Noch kein Ziel gewählt.", "No target chosen yet.")
        survey.coscine = {**state, "auto": {**auto, "error": text, "error_at": _now_shown()}}
        db.session.commit()
        current_app.logger.warning("coscine: automatic upload of survey %s failed: %s", survey.id, text)
        return False, de_en("Automatische Ablage in Coscine fehlgeschlagen: {text}", "Automatic upload to Coscine failed: {text}", text=text)
    if background is None:
        background = not current_app.config.get("TESTING")
    if not background:
        return _auto_done(survey.id, _auto_send(client, job))
    survey.coscine = {**state, "auto": {**auto, "running": _now_shown()}}
    db.session.commit()
    app, lang = current_app._get_current_object(), "en" if english() else "de"
    threading.Thread(target=_auto_thread, args=(app, lang, survey.id, client, job), daemon=True,
                     name=f"coscine-auto-{survey.id}").start()
    return True, de_en("Die automatische Ablage in Coscine läuft im Hintergrund; das Ergebnis steht im Block „Coscine“.",
                       "The automatic upload to Coscine runs in the background; the outcome shows in the block “Coscine”.")


def _auto_send(client, job):
    """The calls to Coscine of an automatic upload, nothing of the database: ("ok", entry), ("partial", entry, text)
    or ("error", text)."""
    try:
        project, resource = client.project(job["project"]), client.resource(job["project"], job["resource"])
        if requires_public_image_rights(resource) and job.get("public_image_rights") is not True:
            raise CoscineError(de_en("Das Ablageziel ist inzwischen öffentlich. Bitte Bildrechte prüfen und erneut ablegen.",
                                     "The target is now public. Please review image rights and store again."))
        if not resource.get("id") or not writable(resource):
            raise CoscineError(de_en("Die Ressource der letzten Ablage nimmt keine Dateien mehr an.",
                                     "The resource of the last upload no longer accepts files."))
        profile = load_profile(client, (resource.get("applicationProfile") or {}).get("uri") or "")
        values = dict(job["values"].get(profile.uri) or {})
        if DCT + "created" in values:
            values[DCT + "created"] = local_today().isoformat()
        missing = profile.missing() + [f for f in profile.fields if profile.offered(f) and f["min"] and not values.get(f["path"])]
        if missing:
            raise CoscineError(de_en("Für das Metadatenprofil fehlen Angaben: {names}. Bitte einmal von Hand ablegen.",
                                     "Details for the metadata profile are missing: {names}. Please store once by hand.",
                                     names=", ".join(f["name"] for f in missing)))
        room = space(client, job["project"], job["resource"])
        fits(room, job["parts"])  # "Speicher voll" in the block instead of half a folder (K4)
        entry = put_parts(job["uid"], job["parts"], client, project, resource, profile, values, job["person"])
        return "ok", ({**entry, "space": after(room, job["parts"])} if room is not None else entry)
    except PartialUpload as e:  # some files lie in Coscine: noted like an upload by hand (I12, E-10)
        return "partial", e.entry, str(e)
    except (CoscineError, KeyError) as e:
        return "error", str(e) if isinstance(e, CoscineError) else de_en("Noch kein Ziel gewählt.", "No target chosen yet.")


def _auto_done(survey_id, outcome):
    """The outcome of an automatic upload into the survey as it is now (read afresh), committed."""
    from app.models import Survey

    survey = db.session.get(Survey, survey_id)
    if survey is None:
        return None
    state = dict(survey.coscine or {})
    auto = {k: v for k, v in (state.get("auto") or {}).items() if k != "running"}
    uploads = list(state.get("uploads") or [])
    if outcome[0] in ("ok", "partial"):
        entry = {**outcome[1], "auto": True}
        uploads = ([entry] + uploads)[:MAX_UPLOADS]
    if outcome[0] == "ok":
        survey.coscine = {**state, "auto": {k: v for k, v in auto.items() if k not in ("error", "error_at")}, "uploads": uploads,
                          **_space_state(entry)}
        db.session.commit()
        return True, de_en("Automatisch in Coscine abgelegt ({resource}, Ordner {folder}).",
                           "Stored in Coscine automatically ({resource}, folder {folder}).",
                           resource=entry["resource"], folder=entry["folder"])
    text = outcome[-1]
    survey.coscine = {**state, "auto": {**auto, "error": text, "error_at": _now_shown()}, "uploads": uploads}
    db.session.commit()
    current_app.logger.warning("coscine: automatic upload of survey %s failed: %s", survey_id, text)
    return False, de_en("Automatische Ablage in Coscine fehlgeschlagen: {text}", "Automatic upload to Coscine failed: {text}", text=text)


def _auto_thread(app, lang, survey_id, client, job):
    """The thread of an automatic upload (I13): Coscine first, without touching the database; then a short session
    for the outcome. A request context only for the language of the messages (that of the person who started it)."""
    with app.test_request_context(headers={"Accept-Language": lang}):
        try:
            try:
                outcome = _auto_send(client, job)
            except Exception:  # noqa: BLE001 - an unexpected error must not leave "läuft" standing
                app.logger.exception("coscine: automatic upload of survey %s broke off", survey_id)
                outcome = ("error", de_en("Unerwarteter Fehler.", "Unexpected error."))
            _auto_done(survey_id, outcome)
        except Exception:  # noqa: BLE001 - the database itself is gone: logged, "läuft" stays until the next upload
            app.logger.exception("coscine: outcome of the automatic upload of survey %s not stored", survey_id)
            db.session.rollback()
        finally:
            db.session.remove()


def _space_state(entry):
    """{"space": {"percent", "resource"}} for survey.coscine after an upload into a resource with a quota, else {}:
    the block "Coscine" warns from SPACE_WARN per cent while the automatic upload is on (K4)."""
    if entry.get("space") is None:
        return {}
    return {"space": {"percent": entry["space"], "resource": entry.get("resource") or ""}}


def _now_shown():
    from app.timeutil import to_local

    return f"{to_local(utcnow()):%d.%m.%Y %H:%M}"


def handle_url(pid):
    return f"https://hdl.handle.net/{pid}" if pid and re.fullmatch(r"[\w.\-]+/[\w.\-]+", pid) else ""


# --- pages ---------------------------------------------------------------------------------------------------


def _back(survey):
    if survey.interview is not None:
        return url_for("interview.export", code=survey.code) + "#coscine"
    return url_for("surveys.data", survey_id=survey.id) + "#coscine"


@bp.route("/connect", methods=["POST"])
@login_required
def connect():
    from app import accounts

    again = accounts.need_fresh(url_for("accounts.connections") + "#coscine")
    if again:
        return again

    back = redirect(url_for("accounts.connections") + "#coscine")
    token = "".join((request.form.get("token") or "").split())
    if not token or len(token) > 8000:
        flash(de_en("Bitte das Zugriffstoken aus Coscine einfügen.", "Please paste the access token from Coscine."), "error")
        return back
    try:
        me = Client(token).me()
    except CoscineError as e:
        flash(str(e), "error")
        return back
    conn = connection(current_user) or CoscineConnection(user_id=current_user.id)
    conn.token, conn.expires_at = secrets_box.encrypt(token), token_expiry(token)
    conn.name = " ".join(str(me.get("displayName") or "").split())[:300]
    db.session.add(conn)
    clear_token_warnings(current_user.id)
    db.session.commit()
    flash(de_en("Mit Coscine verbunden als {name}.", "Connected with Coscine as {name}.", name=conn.name) if conn.name
          else de_en("Mit Coscine verbunden.", "Connected with Coscine."), "success")
    return back


@bp.route("/disconnect", methods=["POST"])
@login_required
def disconnect():
    """Deletes the stored token; also when the feature is off, so nobody keeps a token they cannot see."""
    conn = connection(current_user)
    _checked.pop(current_user.id, None)
    if conn is not None:
        db.session.delete(conn)
        db.session.commit()
        flash(de_en("Coscine-Verbindung getrennt; das gespeicherte Token wurde gelöscht. In Coscine bleibt es gültig, "
                    "bis es dort widerrufen wird oder abläuft.",
                    "Coscine connection removed; the stored token was deleted. In Coscine it stays valid until it is "
                    "revoked there or expires."), "success")
    return redirect(url_for("accounts.connections") + "#coscine")


@bp.route("/survey/<sid:survey_id>", methods=["GET", "POST"])
@login_required
def store(survey_id):
    """In Coscine ablegen: first the target (project and resource), then what goes along and the metadata."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    if survey.live is not None:
        abort(404)
    if connection(current_user) is None:
        flash(de_en("Bitte zuerst unter „Konto › Verbindungen“ ein Coscine-Token hinterlegen.",
                    "Please first add a Coscine token under “Account › Connections”."), "error")
        return redirect(url_for("accounts.connections") + "#coscine")
    state = survey.coscine or {}
    target = request.values.get("ziel") or ""
    try:
        client = client_for(current_user)
        if not target:
            return render_template("coscine/store.html", survey=survey, step="target", targets=targets(client),
                                   chosen=f"{state.get('project', '')}:{state.get('resource', '')}", back=_back(survey), kind_label=kind_label,
                                   conn=connection(current_user), days=days_left(connection(current_user)))
        project_id, _, resource_id = target.partition(":")
        if not project_id or not resource_id or not client.may_write(project_id):
            raise CoscineError(de_en("In diese Ressource darf das Coscine-Konto nicht schreiben.",
                                     "The Coscine account may not write to this resource."))
        project, resource = client.project(project_id), client.resource(project_id, resource_id)
        if not resource.get("id") or not writable(resource):
            raise CoscineError(de_en("Diese Ressource nimmt keine Dateien über Askurious an (schreibgeschützt, in Wartung oder ein Typ ohne Ablage).",
                                     "This resource does not accept files from Askurious (read-only, in maintenance or a type without storage)."))
        uri = (resource.get("applicationProfile") or {}).get("uri") or ""
        profile = load_profile(client, uri)
        room = space(client, project_id, resource_id)
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(url_for("coscine.store", survey_id=survey.id) if target else _back(survey))
    consent = survey.consent_item if survey.interview is None else None
    public = ((resource.get("visibility") or {}).get("displayName") or "").lower() == "public"
    view = dict(survey=survey, step="content", target=target, project=project, resource=resource, profile=profile,
                missing=profile.missing(), choices=choices(survey), handle=handle_url(resource.get("pid")),
                worm=is_worm(resource), kind=kind_label(resource), room=room, space_warn=SPACE_WARN, size_text=size_text,
                warn_public=public and consent is not None and not consent.config.get("open_data"),
                back=_back(survey), conn=connection(current_user), days=days_left(connection(current_user)))
    if request.method == "GET":
        return render_template("coscine/store.html", fields=form_fields(profile, survey, project, current_user),
                               ticked={k for k, _, _, on in view["choices"] if on}, **view)
    ticked = {k for k, _, _, _ in view["choices"] if request.form.get(k)}
    values, problems = read_values(profile, request.form)
    if view["missing"]:
        problems.append(de_en("Das Metadatenprofil dieser Ressource verlangt Angaben, die Askurious nicht ausfüllen kann.",
                              "The metadata profile of this resource asks for details Askurious cannot fill in."))
    if not {k for k in ticked if k not in ("offen", "abbrueche")}:
        problems.append(de_en("Bitte mindestens einen Teil zum Ablegen ankreuzen.", "Please tick at least one part to store."))
    shown = [(f, values.get(f["path"], "")) for f in profile.fields if profile.offered(f)]
    if problems:
        for p in problems:
            flash(p, "error")
        return render_template("coscine/store.html", fields=shown, ticked=ticked, **view), 400
    try:
        entry = upload(survey, client, project, resource, profile, values, ticked, current_user.name or "", room=room)
    except CoscineError as e:
        if isinstance(e, PartialUpload):  # noted, so the block "Coscine" names the incomplete folder
            survey.coscine = {**state, "uploads": ([e.entry] + list(state.get("uploads") or []))[:MAX_UPLOADS]}
            db.session.commit()
            current_app.logger.warning("coscine: upload of survey %s broke off in %s", survey.id, e.entry["folder"])
        flash(str(e), "error")
        return render_template("coscine/store.html", fields=shown, ticked=ticked, **view), 502
    kept = dict(state.get("values") or {})
    kept[profile.uri] = values
    auto, auto_off = _auto_after_target(state, project_id, resource_id, current_user.id)
    survey.coscine = {**state, "project": project_id, "resource": resource_id, "values": kept, **auto,
                      "uploads": ([entry] + list(state.get("uploads") or []))[:MAX_UPLOADS], **_space_state(entry)}
    db.session.commit()
    flash(de_en("{n} Dateien in Coscine abgelegt ({resource}, Ordner {folder}).", "{n} files stored in Coscine ({resource}, folder {folder}).",
                n=len(entry["files"]), resource=entry["resource"], folder=entry["folder"]), "success")
    if auto_off:
        flash(de_en("Die automatische Ablage ist ausgeschaltet: Sie lief mit dem Coscine-Token einer anderen Person in "
                    "das bisherige Ziel. Wer sie für das neue Ziel möchte, schaltet sie mit dem eigenen Token wieder ein.",
                    "The automatic upload is switched off: it ran with another person's Coscine token into the previous "
                    "target. Anyone who wants it for the new target switches it on again with their own token."), "warning")
    return redirect(_back(survey))


@bp.route("/survey/<sid:survey_id>/automatic", methods=["POST"])
@login_required
@autosave.aware
def auto(survey_id):
    """The tick "automatisch ablegen" in the block "Coscine": off unless ticked; it uses the token of whoever ticks it."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    state = dict(survey.coscine or {})
    on = bool(request.form.get("auto"))
    if on and (connection(current_user) is None or not state.get("resource")):
        flash(de_en("Die automatische Ablage braucht ein hinterlegtes Coscine-Token und eine erste Ablage von Hand.",
                    "The automatic upload needs a stored Coscine token and a first upload by hand."), "error")
    else:
        # the target goes along: the token of this person writes only where they switched it on (Abgleich 2026-10-15 H2)
        survey.coscine = {**state, "auto": {"on": True, "user_id": current_user.id, "name": current_user.name or "",
                                            "project": state.get("project"), "resource": state.get("resource")}
                          if on else {"on": False}}
        db.session.commit()
        flash(de_en("Automatische Ablage in Coscine {when} eingeschaltet.", "Automatic upload to Coscine {when} switched on.",
                    when=auto_label(survey)) if on
              else de_en("Automatische Ablage in Coscine ausgeschaltet.", "Automatic upload to Coscine switched off."), "success")
    return redirect(_back(survey))


def _upload_view(survey_id):
    """The survey (role "Verwalten"), its Coscine state and a client with the person's token; or a redirect."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    if survey.live is not None:
        abort(404)
    if connection(current_user) is None:
        flash(de_en("Bitte zuerst unter „Konto › Verbindungen“ ein Coscine-Token hinterlegen.",
                    "Please first add a Coscine token under “Account › Connections”."), "error")
        return survey, None, redirect(url_for("accounts.connections") + "#coscine")
    return survey, client_for(current_user), None


@bp.route("/survey/<sid:survey_id>/check", methods=["POST"])
@login_required
def check_page(survey_id):
    """"In Coscine prüfen" at an upload in the block "Coscine": its folder in Coscine against what Askurious noted at
    the upload; with "gründlich" also the SHA-256 of each file (K2, E3: only on request, E4: up to DEEP_LIMIT)."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    state = dict(survey.coscine or {})
    uploads = list(state.get("uploads") or [])
    folder = request.form.get("ordner") or ""
    index = next((i for i, u in enumerate(uploads) if u.get("folder") == folder), None)
    if index is None:
        abort(404)
    entry = uploads[index]
    project_id, resource_id = where(state, entry)
    try:
        if not resource_id:
            raise CoscineError(de_en("Für diese Ablage ist kein Ziel bekannt.", "No target is known for this upload."))
        rows, deep = check_upload(client, project_id, resource_id, entry, deep=bool(request.form.get("gruendlich")))
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(_back(survey))
    bad = [r for r in rows if r["state"] in ("missing", "changed")]
    uploads[index] = {**entry, "check": {"at": _now_shown(), "ok": not bad, "deep": deep,
                                         "extra": sum(1 for r in rows if r["state"] == "extra")}}
    survey.coscine = {**state, "uploads": uploads}
    db.session.commit()
    total = sum(int(f.get("size") or 0) for f in entry.get("files") or [])
    return render_template("coscine/check.html", survey=survey, entry=uploads[index], rows=rows, deep=deep,
                           deep_possible=total <= DEEP_LIMIT, deep_limit=DEEP_LIMIT, total=total, back=_back(survey),
                           size_text=size_text)


@bp.route("/survey/<sid:survey_id>/resource")
@login_required
def resource_page(survey_id):
    """"Alles in dieser Ressource": every upload folder of the survey in the resource of the last upload, also those of
    other persons or older than the list in Askurious (K2)."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    state = survey.coscine or {}
    if not state.get("resource"):
        flash(de_en("Noch keine Ablage in Coscine.", "No upload to Coscine yet."), "error")
        return redirect(_back(survey))
    try:
        found = folders(client, state["project"], state["resource"], survey.uid)
        resource = client.resource(state["project"], state["resource"])
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(_back(survey))
    known = {u.get("folder") for u in state.get("uploads") or []}
    return render_template("coscine/resource.html", survey=survey, found=found, known=known, back=_back(survey),
                           resource=resource, size_text=size_text, kind=kind_label(resource))


@bp.route("/survey/<sid:survey_id>/new", methods=["GET", "POST"])
@login_required
def new_page(survey_id):
    """"Neues Projekt in Coscine": a project (or sub-project) with a first resource, or a resource in a project of the
    person, created from Askurious; afterwards it is the chosen target of "In Coscine ablegen" (K1, E1, E2). Askurious
    creates nothing without this form and deletes nothing in Coscine."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    try:
        options = new_options(client, survey)
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(url_for("coscine.store", survey_id=survey.id))
    view = dict(survey=survey, back=url_for("coscine.store", survey_id=survey.id), risk=RISK_PROFILE, base=BASE_PROFILE,
                kind_label=kind_label, english=english(), **options)
    if request.method == "GET":
        return render_template("coscine/new.html", form=options["defaults"], **view)
    try:
        project_id, resource_id, note = create_target(client, request.form, options)
    except CoscineError as e:
        flash(str(e), "error")
        form = {**options["defaults"], **{k: request.form.get(k, "") for k in request.form}}
        return render_template("coscine/new.html", form=form, **view), 400
    flash(de_en("In Coscine angelegt. Jetzt wählen, was in die neue Ressource geht.",
                "Created in Coscine. Now choose what goes into the new resource."), "success")
    if note:
        flash(note, "info")
    return redirect(url_for("coscine.store", survey_id=survey.id, ziel=f"{project_id}:{resource_id}"))


@bp.route("/survey/<sid:survey_id>/people", methods=["GET", "POST"])
@login_required
def people_page(survey_id):
    """"Wer sieht die Dateien": the members of the Coscine project of the last upload next to the people with a right
    on the survey; who is missing can be invited, each ticked on its own (or a whole group, E6), with a role after
    E5. Coscine sends the mail. The e-mail address goes to Coscine only on this click (privacy concept)."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    state = survey.coscine or {}
    if not state.get("project"):
        flash(de_en("Erst ein Ziel in Coscine wählen und einmal ablegen.", "First choose a target in Coscine and store once."),
              "error")
        return redirect(_back(survey))
    project_id = state["project"]
    try:
        project = client.project(project_id)
        members, invited = coscine_people(client, project_id)
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(_back(survey))
    people = team(survey)
    if request.method == "POST":
        groups = {g for g in request.form.getlist("gruppe")}
        ticked = set(request.form.getlist("person"))
        chosen = [p for p in people if p["email"] and p["email"] not in members and p["email"] not in invited
                  and (str(p["user"].id) in ticked or any(str(g) in groups for g, _ in p["groups"]))]
        if not chosen:
            flash(de_en("Niemand zum Einladen ausgewählt.", "Nobody selected to invite."), "error")
            return redirect(url_for("coscine.people_page", survey_id=survey.id))
        try:
            ids = role_ids(client)
            sent = []
            for p in chosen:
                role_id = ids[COSCINE_ROLE.get(p["role"], "guest")]
                if not role_id:
                    raise CoscineError(de_en("Coscine nennt die Rolle „Member“ nicht.", "Coscine does not name the role “Member”."))
                client.invite(project_id, p["email"], role_id)
                sent.append(p["user"].display_name)
        except CoscineError as e:
            flash(str(e), "error")
            return redirect(url_for("coscine.people_page", survey_id=survey.id))
        flash(de_en("In Coscine eingeladen: {names}. Coscine schickt die Einladung per Mail.",
                    "Invited in Coscine: {names}. Coscine sends the invitation by mail.", names=", ".join(sent)), "success")
        return redirect(url_for("coscine.people_page", survey_id=survey.id))
    return render_template("coscine/people.html", survey=survey, project=project, people=people, members=members,
                           invited=invited, coscine_role=COSCINE_ROLE, back=_back(survey))


@bp.route("/survey/<sid:survey_id>/people/withdraw", methods=["POST"])
@login_required
def withdraw_page(survey_id):
    """Takes back an open invitation into the project of the last upload."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    project_id = (survey.coscine or {}).get("project")
    if not project_id:
        abort(404)
    try:
        _, invited = coscine_people(client, project_id)
        found = next((i for i in invited.values() if i.get("id") == request.form.get("einladung")), None)
        if found is None:
            abort(404)
        client.withdraw(project_id, found["id"])
    except CoscineError as e:
        flash(str(e), "error")
    else:
        flash(de_en("Einladung zurückgenommen.", "Invitation withdrawn."), "success")
    return redirect(url_for("coscine.people_page", survey_id=survey.id))


@bp.route("/survey/<sid:survey_id>/publish", methods=["GET", "POST"])
@login_required
def publish_page(survey_id):
    """"Veröffentlichung beantragen": a request to the publication service of the person's organisation for the
    resource of the last upload; the service answers by mail and publishes with a DOI (K5). Not where the uploads may
    hold wording or images without clearance for the public."""
    survey, client, away = _upload_view(survey_id)
    if away:
        return away
    state = dict(survey.coscine or {})
    entry = next((u for u in state.get("uploads") or [] if not u.get("partial")), None)
    if entry is None or not state.get("resource"):
        flash(de_en("Erst einmal vollständig in Coscine ablegen.", "First store completely in Coscine once."), "error")
        return redirect(_back(survey))
    project_id, resource_id = where(state, entry)
    blocked = publication_blocked(survey)
    try:
        services = [s_ for s_ in client.publication_services() if s_.get("organizationRor")]
    except CoscineError as e:
        flash(str(e), "error")
        return redirect(_back(survey))
    view = dict(survey=survey, entry=entry, services=services, blocked=blocked, back=_back(survey),
                requests=state.get("publications") or [])
    if request.method == "GET":
        return render_template("coscine/publish.html", message=publication_message(survey, entry), **view)
    message = (request.form.get("nachricht") or "").strip()[:4000]
    service = next((s_ for s_ in services if s_["organizationRor"] == request.form.get("dienst")), None)
    if blocked or service is None or not message:
        flash(blocked or de_en("Bitte einen Dienst wählen und eine Nachricht schreiben.",
                               "Please choose a service and write a message."), "error")
        return render_template("coscine/publish.html", message=message, **view), 400
    try:
        client.request_publication(project_id, service["organizationRor"], [resource_id], message)
    except CoscineError as e:
        flash(str(e), "error")
        return render_template("coscine/publish.html", message=message, **view), 502
    note = {"at": _now_shown(), "by": current_user.name or "", "by_id": current_user.id, "service": service.get("displayName") or "",
            "resource": entry.get("resource") or ""}
    survey.coscine = {**state, "publications": ([note] + list(state.get("publications") or []))[:MAX_UPLOADS]}
    db.session.commit()
    flash(de_en("Veröffentlichung bei {s} beantragt. Der Dienst meldet sich per Mail.",
                "Publication requested from {s}. The service will reply by mail.", s=note["service"]), "success")
    return redirect(_back(survey))


# --- files from Coscine into a survey (K6) -------------------------------------------------------------------


def survey_file(form):
    """The bytes of a survey file chosen in Coscine for "Aus einer Datei anlegen" (fields coscine_ziel and
    coscine_pfad, wizard.import_file; package G), with the token of the person; CoscineError if it cannot be had."""
    if connection(current_user) is None:
        raise CoscineError(de_en("Bitte zuerst unter „Konto“ ein Coscine-Token hinterlegen.",
                                 "Please first add a Coscine token under “Account”."))
    return fetch(client_for(current_user), form.get("coscine_ziel"), form.get("coscine_pfad"), "survey")


@bp.route("/pick")
@login_required
def pick_list_plain():
    """The resources and survey files of Coscine for "Aus einer Datei anlegen", where there is no survey yet
    (JSON, package G); the same as pick_list without a survey."""
    if connection(current_user) is None:
        abort(404)
    client = client_for(current_user)
    try:
        if not request.args.get("ziel"):
            return {"targets": [{"value": f"{p['id']}:{r['id']}", "label": f"{p.get('displayName') or p.get('name') or ''} › "
                                                                         f"{r.get('displayName') or r.get('name') or ''}"}
                                for p, resources in targets(client) for r in resources]}
        project_id, resource_id = _pick_target(client, request.args.get("ziel"))
        files = [f for f in client.listing(project_id, resource_id, "", depth=4)
                 if f["path"].lower().endswith(PICK_KINDS["survey"])][:PICK_MAX_FILES]
        return {"files": [{"path": f["path"], "size": f.get("size")} for f in files]}
    except CoscineError as e:
        return {"error": str(e)}, 502


def _pick_view(survey_id):
    """The survey (role "Bearbeiten") and a client with the person's token; JSON 404 else."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="editor")
    if connection(current_user) is None:
        abort(404)
    return survey, client_for(current_user)


def _pick_target(client, value):
    """(project id, resource id) of "p:r" if the person's token sees that resource; CoscineError else."""
    project_id, _, resource_id = (value or "").partition(":")
    if not project_id or not resource_id or not client.resource(project_id, resource_id).get("id"):
        raise CoscineError(de_en("Diese Ressource ist nicht erreichbar.", "This resource cannot be reached."))
    return project_id, resource_id


def fetch(client, value, path, kind):
    """The bytes of one file of a resource the person can read, at most PICK_LIMIT[kind] (K6)."""
    if kind not in PICK_KINDS or not str(path or "").lower().endswith(PICK_KINDS[kind]) or ".." in str(path).split("/"):
        raise CoscineError(de_en("Diese Datei passt hier nicht.", "This file does not fit here."))
    project_id, resource_id = _pick_target(client, value)
    limit = pick_limit(kind)
    too_large = CoscineError(de_en("Die Datei ist größer als {n}.", "The file is larger than {n}.", n=size_text(limit)))
    try:
        data = client.download(project_id, resource_id, path, limit + 1)
    except CoscineError as e:
        raise (too_large if e.status == 413 else e) from None
    if len(data) > limit:
        raise CoscineError(de_en("Die Datei ist größer als {n}.", "The file is larger than {n}.", n=size_text(limit)))
    return data


def pick_limit(kind):
    """The largest file of a kind taken from Coscine: video and audio as an upload (MEDIA_MAX_MB)."""
    if kind == "media":
        return int(current_app.config["MEDIA_MAX_MB"]) * 1024 * 1024
    if kind == "survey":  # a survey file: as an upload of "Aus einer Datei anlegen" (package G)
        return int(current_app.config["IMPORT_MAX_BYTES"])
    return PICK_LIMIT[kind]


def as_text(data):
    """A table file as text: UTF-8, else Windows-1252 as Excel writes it on German systems (like app.js)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text.lstrip("\ufeff")


@bp.route("/survey/<sid:survey_id>/pick")
@login_required
def pick_list(survey_id):
    """For "aus Coscine wählen": without `ziel` the resources the person's token sees, with it the files of that
    resource that fit `art` (JSON, K6)."""
    survey, client = _pick_view(survey_id)
    kind = request.args.get("art") if request.args.get("art") in PICK_KINDS else "image"
    if kind == "media":
        _media_feature()
    try:
        if not request.args.get("ziel"):
            found = [{"value": f"{p['id']}:{r['id']}", "label": f"{p.get('displayName') or p.get('name') or ''} › "
                                                                f"{r.get('displayName') or r.get('name') or ''}"}
                     for p, resources in targets(client) for r in resources]
            preferred = f"{(survey.coscine or {}).get('project')}:{(survey.coscine or {}).get('resource')}"
            found.sort(key=lambda t: t["value"] != preferred)  # the resource of the last upload first
            return {"targets": found}
        project_id, resource_id = _pick_target(client, request.args.get("ziel"))
        files = [f for f in client.listing(project_id, resource_id, "", depth=4)
                 if f["path"].lower().endswith(PICK_KINDS[kind])][:PICK_MAX_FILES]
        return {"files": [{"path": f["path"], "size": f.get("size")} for f in files]}
    except CoscineError as e:
        return {"error": str(e)}, 502


@bp.route("/survey/<sid:survey_id>/pick/text")
@login_required
def pick_text(survey_id):
    """The text of a table file from Coscine for a table field of the editor (JSON, K6); the field saves itself."""
    _, client = _pick_view(survey_id)
    try:
        return {"text": as_text(fetch(client, request.args.get("ziel"), request.args.get("pfad"), "table"))}
    except CoscineError as e:
        return {"error": str(e)}, 502


@bp.route("/survey/<sid:survey_id>/pick/image/<int:item_id>", methods=["POST"])
@login_required
def pick_image(survey_id, item_id):
    """An image from Coscine as the image of an element, the same way as an upload (checks, size, history). It is
    copied, not linked (E9): the element keeps where it came from and "neu holen" takes the file anew."""
    from app import media, undo
    from app.editor.base import _get_item, _lang, _require_draft
    from app.editor.images import _replace_static_asset
    from app.models import Asset

    survey, client = _pick_view(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    page = redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang) + "#assets")
    if not _require_draft(survey) or not item.kind.has_image:
        return page
    value, path = request.form.get("ziel") or "", request.form.get("pfad") or ""
    try:
        name = media.save_bytes(survey, media.participant_upload(fetch(client, value, path, "image")))
    except (CoscineError, ValueError) as e:
        flash(str(e), "error")
        return page
    step = undo.begin(survey)
    _replace_static_asset(survey, item, Asset(key="", filename=name, source="upload", status=Asset.APPROVED))
    item.config = {**item.config, "source": "upload",
                   "coscine": {"ziel": value, "pfad": path, "at": _now_shown()}}
    step.done(undo.both("Bild von „{code}“ aus Coscine übernommen", "Image of “{code}” taken from Coscine", code=item.code),
              page="item", item=item.id)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash(de_en("Bild aus Coscine übernommen.", "Image taken from Coscine."), "success")
    return page


# --- video and audio from Coscine (K6, package F) ------------------------------------------------------------


def _media_state(item, key, **state):
    """item.config["coscine_media"][key]: where a file from Coscine for the value `key` stands (not committed)."""
    states = dict((item.config or {}).get("coscine_media") or {})
    states[key] = {**state, "at": _now_shown()}
    item.config = {**item.config, "coscine_media": states}


def _media_work(survey_id, item_id, key, value, path, client):
    """Loads the file from Coscine and puts it through the pieces of an upload (media.add_media_part, finish_media):
    the same checks and limits as a file from the browser. Runs in a thread of its own (tests: in the request); the
    outcome stands in the element's config, the editor asks for it."""
    from app import media
    from app.editor.images import _replace_static_asset
    from app.items import stimulus
    from app.models import Asset, Item, Survey

    try:
        data = fetch(client, value, path, "media")  # nothing of the database is held while Coscine answers
        error = None
    except CoscineError as e:
        data, error = None, str(e)
    survey, item = db.session.get(Survey, survey_id), db.session.get(Item, item_id)
    if survey is None or item is None:
        return
    if error is None:
        old = next((a for a in item.assets if a.key == key), None)
        slot, upload = media.part_slot(item, key), secrets_hex()
        try:
            for index in range(0, max(1, -(-len(data) // stimulus.CHUNK_BYTES))):
                piece = data[index * stimulus.CHUNK_BYTES:(index + 1) * stimulus.CHUNK_BYTES]
                media.add_media_part(survey, slot, upload, index, piece, replaced=old.filename if old else None)
            name, mime, size = media.finish_media(survey, slot, upload)
        except ValueError as e:
            error = str(e)
    if error is not None:
        _media_state(item, key, state="error", error=error, pfad=path)
        db.session.commit()
        return
    _replace_static_asset(survey, item, Asset(key=key, filename=name, source="upload", status=Asset.APPROVED))
    files = dict((item.config or {}).get("files") or {})
    files[key] = {"kind": stimulus.MIMES[mime][0], "mime": mime, "bytes": size, "seconds": 0.0,
                  "name": path.rsplit("/", 1)[-1][:200]}
    item.config = {**item.config, "files": files}
    _media_state(item, key, state="done", ziel=value, pfad=path)
    db.session.commit()


def _media_feature():
    """Video and sound from Coscine stay experimental ("coscine_media", Ralf 2026-10-20); images and tables are for
    everyone."""
    from app import experimental

    experimental.require("coscine_media")


def secrets_hex():
    import secrets

    return secrets.token_hex(16)


@bp.route("/survey/<sid:survey_id>/pick/media/<int:item_id>", methods=["POST"])
@login_required
def pick_media(survey_id, item_id):
    """A video or audio file from Coscine for a stimulus (value `key` of its loop, "" for one file): loaded in the
    background, then the same way as an upload; copied, not linked (E9). Answers JSON; the editor asks pick_media_state
    until it is done."""
    from app.editor.base import _get_item

    survey, client = _pick_view(survey_id)
    _media_feature()
    item = _get_item(survey, item_id)
    if item.type != "media":
        abort(404)
    if not survey.is_editable:
        return {"error": de_en("Nur im Entwurf möglich.", "Only possible in a draft.")}, 400
    key = request.form.get("key", "")
    if key not in dict(item.kind.required_keys(item)):
        return {"error": de_en("Unbekannter Wert der Schleife.", "Unknown value of the loop.")}, 400
    value, path = request.form.get("ziel") or "", request.form.get("pfad") or ""
    if not str(path).lower().endswith(PICK_KINDS["media"]) or ".." in str(path).split("/"):
        return {"error": de_en("Diese Datei passt hier nicht.", "This file does not fit here.")}, 400
    _media_state(item, key, state="running", pfad=path)
    db.session.commit()
    if current_app.config.get("TESTING"):
        _media_work(survey.id, item.id, key, value, path, client)
        return {"ok": True}
    app = current_app._get_current_object()
    lang = "en" if english() else "de"

    def work():
        with app.test_request_context(headers={"Accept-Language": lang}):
            try:
                _media_work(survey.id, item.id, key, value, path, client)
            except Exception:  # noqa: BLE001 - "läuft" must not stand for good
                app.logger.exception("coscine: video or audio for item %s broke off", item.id)
                db.session.rollback()
                found = db.session.get(type(item), item.id)
                if found is not None:
                    _media_state(found, key, state="error", error=de_en("Unerwarteter Fehler.", "Unexpected error."), pfad=path)
                    db.session.commit()
            finally:
                db.session.remove()

    threading.Thread(target=work, daemon=True, name=f"coscine-media-{item.id}").start()
    return {"ok": True}


@bp.route("/survey/<sid:survey_id>/pick/media/<int:item_id>/state")
@login_required
def pick_media_state(survey_id, item_id):
    """Where the files from Coscine of a stimulus stand: {key: {"state", "error"?, "pfad", "at"}} (JSON)."""
    from app.editor.base import _get_item

    survey, _ = _pick_view(survey_id)
    _media_feature()
    item = _get_item(survey, item_id)
    return {"states": (item.config or {}).get("coscine_media") or {}}
