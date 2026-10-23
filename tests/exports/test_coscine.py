"""Coscine (app/coscine.py, experimental): token on the page "Konto", the page "In Coscine ablegen", metadata after
the resource's application profile, uploads into a new folder. Coscine's API is replaced by FakeCoscine (there is
no sandbox); the profiles are shortened copies of the real ones."""
from tests.instrument_permissions import grant_instrument
import base64
import json
import re
import time
from urllib.parse import unquote

import pytest

from app import coscine, experimental
from app.extensions import db
from app.models import CoscineConnection, Survey

BASE = "https://coscine.test/api/v2"
RISK = "https://purl.org/coscine/ap/SocialSciencebyRISK"
BASIS = "https://purl.org/coscine/ap/base/"
STRICT = "https://purl.org/coscine/ap/strict/"
PREFIXES = """@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.
@prefix dcterms: <http://purl.org/dc/terms/>.
@prefix dcmitype: <http://purl.org/dc/dcmitype/>.
@prefix sh: <http://www.w3.org/ns/shacl#>.
"""
PROFILES = {
    BASIS: PREFIXES + """<https://purl.org/coscine/ap/base/> a sh:NodeShape;
  sh:property [sh:path dcterms:created ; sh:order 2 ; sh:minCount 1 ; sh:maxCount 1 ; sh:name "Creation Date"@en ;
                 sh:name "Erstellungsdatum"@de ; sh:datatype xsd:date ; sh:defaultValue "{TODAY}"],
              [sh:path dcterms:title ; sh:order 0 ; sh:minCount 1 ; sh:name "Titel"@de ; sh:datatype xsd:string],
              [sh:path dcterms:type ; sh:order 4 ; sh:class dcmitype: ; sh:name "Typ"@de],
              [sh:path dcterms:creator ; sh:order 1 ; sh:minCount 1 ; sh:name "Ersteller"@de ; sh:datatype xsd:string],
              [sh:path dcterms:subject ; sh:order 3 ; sh:class <http://www.dfg.de/faecher/> ; sh:name "Fachrichtung"@de];
  sh:targetClass <https://purl.org/coscine/ap/base/>.
""",
    RISK: PREFIXES + """<https://purl.org/coscine/ap/SocialSciencebyRISK> a sh:NodeShape;
  sh:property [sh:path dcterms:contributor ; sh:order 11 ; sh:minCount 1 ; sh:name "Verantwortliche Person "@de ;
                 sh:datatype xsd:string ; <http://datashapes.org/dash#singleLine> "1"^^xsd:boolean],
              [sh:maxInclusive 20000 ; sh:minInclusive 1 ; sh:path <https://purl.org/coscine/terms/c4d28f97-931d-4d40-8616-9b3b43297dda/> ;
                 sh:order 9 ; sh:minCount 1 ; sh:maxCount 1 ; sh:name "N Rohdaten"@de ; sh:datatype xsd:integer],
              [sh:path <https://purl.org/coscine/terms/98338a24-8a30-4e4e-903c-b01f7e3ac0ac/> ; sh:order 8 ; sh:minCount 1 ;
                 sh:maxCount 1 ; sh:name "Erhebungszeitraum"@de ; sh:datatype xsd:string],
              [sh:path dcterms:identifier ; sh:order 6 ; sh:minCount 1 ; sh:maxCount 1 ; sh:name "Ethikantrag"@de ;
                 sh:description "Wurde die Datenerhebung von der Ethikkommission positiv evaluiert?"@de ;
                 sh:in ("Ja"
                        "Nein")],
              [sh:path dcterms:title ; sh:order 0 ; sh:minCount 1 ; sh:name "Titel"@de ; sh:name "Title"@en ; sh:datatype xsd:string],
              [sh:path dcterms:type ; sh:order 5 ; sh:minCount 1 ; sh:maxCount 1 ; sh:name "Datenart"@de ;
                 sh:in ("Qualitativ" "Quantitativ")],
              [sh:path <http://schema.org/about> ; sh:order 4 ; sh:minCount 1 ; sh:name "Inhalt der Erhebung"@de ;
                 sh:datatype xsd:string ; <http://datashapes.org/dash#singleLine> "0"^^xsd:boolean],
              [sh:path dcterms:isPartOf ; sh:order 3 ; sh:minCount 1 ; sh:name "Projekt"@de ; sh:datatype xsd:string],
              [sh:path dcterms:creator ; sh:order 1 ; sh:minCount 1 ; sh:name "Ersteller"@de ; sh:datatype xsd:string],
              [sh:path dcterms:created ; sh:order 2 ; sh:minCount 1 ; sh:maxCount 1 ; sh:name "Erstellungsdatum"@de ; sh:datatype xsd:date];
  sh:targetClass <https://purl.org/coscine/ap/base/>.
""",
    STRICT: PREFIXES + """<https://purl.org/coscine/ap/strict/> a sh:NodeShape;
  sh:property [sh:path dcterms:title ; sh:minCount 1 ; sh:name "Titel"@de ; sh:datatype xsd:string],
              [sh:path dcterms:subject ; sh:minCount 1 ; sh:class <http://www.dfg.de/faecher/> ; sh:name "Fachrichtung"@de].
""",
}
MEMBER, GUEST = "508b6d4e-c6ac-4aa5-8a8d-caa31dd39527", coscine.GUEST_ROLE


def jwt(days=200):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + days * 86400}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


class FakeCoscine:
    """Answers like Coscine's API v2 for one project p1 with a web resource r1, an S3 resource r2, a WORM resource
    r3 and a linked-data resource r4 (left out), and a project p2 in which the token is only a guest."""

    def __init__(self):
        self.calls, self.files, self.meta, self.where, self.token, self.profile = [], {}, {}, {}, None, RISK
        # the person's tokens as Coscine lists them (K7) and the storage of the S3 and WORM resources (K4)
        self.api_tokens, self.quotas = None, {}
        self.created, self.refuse_resource = [], False  # new projects and resources (K1)
        self.quotas_free = {}  # resource type id: free GiB in a project (POST /projects/.../resources); 100 if missing
        self.refuse_project = None  # a status: Coscine refuses a new project without a reason (empty answer)
        # members and invitations of p1 (K3), publication services and requests (K5)
        self.members = [{"id": "m1", "role": {"id": MEMBER, "displayName": "Owner"},
                         "user": {"displayName": "Ralf Philipsen", "email": "ralf@example.org"}}]
        self.invited, self.services, self.requests = [], [
            {"displayName": "RWTH Publications", "email": "publications@ub.rwth-aachen.de",
             "organizationRor": "https://ror.org/04xfq0f34"}], []

    def __call__(self, method, url, headers=None, timeout=None, allow_redirects=True, params=None, data=None, files=None, **kw):
        assert url.startswith(BASE) and allow_redirects is False
        self.calls.append((method, url))
        path = url[len(BASE):]

        class R:
            def __init__(self, payload, status=200):
                self.status_code, self._p = status, payload
                self.content = json.dumps(payload).encode() if payload is not None else b""
                self.text = self.content.decode()

            def json(self):
                return self._p

        if headers["Authorization"] != f"Bearer {self.token}":
            return R({"title": "Unauthorized"}, 401)
        web = {"id": "r1", "displayName": "Befragungsdaten", "pid": "21.11102/r1", "type": {"generalType": "dsNrwWeb"},
               "applicationProfile": {"uri": self.profile}, "visibility": {"displayName": "Project Members"}}
        s3 = {"id": "r2", "displayName": "Rohdaten S3", "type": {"generalType": "dsNrwS3"}, "applicationProfile": {"uri": BASIS}}
        worm = {"id": "r3", "displayName": "Archiv", "type": {"generalType": "dsNrwS3Worm"}, "applicationProfile": {"uri": BASIS}}
        linked = {"id": "r4", "displayName": "Verweise", "type": {"generalType": "linked"}, "applicationProfile": {"uri": BASIS}}
        resources = {r["id"]: r for r in (web, s3, worm, linked)}
        page = {"pagination": {"hasNext": False}}
        if method == "GET" and path == "/self":
            return R({"data": {"displayName": "Ralf Philipsen",
                               "organizations": [{"uri": "https://ror.org/04xfq0f34", "displayName": "RWTH Aachen"}]}})
        if method == "GET" and path == "/projects/p1/members":
            return R({"data": self.members, **page})
        if method == "GET" and path == "/projects/p1/invitations":
            return R({"data": self.invited, **page})
        if method == "POST" and path == "/projects/p1/invitations":
            self.invited.append({"id": f"i{len(self.invited) + 1}", "email": kw["json"]["email"],
                                 "role": {"id": kw["json"]["roleId"]}, "expirationDate": "2026-11-03T00:00:00Z"})
            return R({"data": self.invited[-1]}, 201)
        m = re.fullmatch(r"/projects/p1/invitations/(i\d+)", path)
        if method == "DELETE" and m:
            self.invited = [i for i in self.invited if i["id"] != m.group(1)]
            return R(None, 204)
        if method == "GET" and path == "/roles":
            return R({"data": [{"id": "role-owner", "displayName": "Owner"}, {"id": MEMBER, "displayName": "Member"},
                               {"id": GUEST, "displayName": "Guest"}], **page})
        if method == "GET" and path == "/publication-service":
            return R({"data": self.services, **page})
        if method == "POST" and path == "/projects/p1/publications/requests":
            self.requests.append(kw["json"])
            return R({"data": {"id": "pr1"}}, 201)
        if method == "GET" and path == "/disciplines":
            return R({"data": [{"id": "d1", "displayNameDe": "Informatik", "displayNameEn": "Computer Science"},
                               {"id": "d2", "displayNameDe": "Sozialwissenschaften", "displayNameEn": "Social Sciences"}], **page})
        if method == "GET" and path == "/licenses":
            return R({"data": [{"id": "l0", "displayName": "MIT License"},
                               {"id": "l1", "displayName": "Creative Commons Attribution 4.0 International (CC BY 4.0)"}], **page})
        if method == "GET" and path == "/visibilities":
            return R({"data": [{"id": "v1", "displayName": "Public"}, {"id": "v2", "displayName": "Project Members"}], **page})
        if method == "GET" and path == "/resource-types/types":
            return R({"data": [{"id": "t-web", "generalType": "dsNrwWeb", "isEnabled": True, "canCreate": True},
                               {"id": "t-s3", "generalType": "dsNrwS3", "isEnabled": True, "canCreate": True},
                               {"id": "t-gitlab", "generalType": "gitlab", "isEnabled": True, "canCreate": True}]})
        if method == "POST" and path == "/projects":
            if self.refuse_project:
                return R(None, self.refuse_project)
            self.created.append(("project", kw["json"]))
            return R({"data": {"id": f"p{len(self.created) + 10}"}}, 201)
        m = re.fullmatch(r"/projects/(p\d+)/quotas", path)
        if method == "GET" and m:
            gib = "https://qudt.org/vocab/unit/GibiBYTE"
            return R({"data": [{"resourceType": {"id": t}, "allocated": {"value": self.quotas_free.get(t, 100) + 2, "unit": gib},
                                "totalReserved": {"value": 2, "unit": gib}} for t in ("t-web", "t-s3")], **page})
        m = re.fullmatch(r"/projects/(p\d+)/resources", path)
        if method == "POST" and m:
            if self.refuse_resource == "id":  # as Coscine's production server answers: the kind, no title
                return R({"status": 400, "errorId": "ResourceQuotaExceededBadRequestException", "title": None}, 400)
            if self.refuse_resource:
                return R({"title": "Not enough quota in the project."}, 400)
            self.created.append(("resource", m.group(1), kw["json"]))
            return R({"data": {"id": f"r{len(self.created) + 10}"}}, 201)
        if method == "GET" and path == "/self/api-tokens":
            listed = self.api_tokens
            if listed is None:  # the stored token alone, ending when the token says
                claims = json.loads(base64.urlsafe_b64decode(self.token.split(".")[1] + "=="))
                end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(claims["exp"]))
                listed = [{"id": "t1", "name": "Askurious", "expiryDate": end}]
            return R({"data": listed, **page})
        m = re.fullmatch(r"/projects/p1/resources/(r\d)/quota", path)
        if method == "GET" and m:
            if m.group(1) not in self.quotas:
                return R({"title": "This resource type has no quota."}, 400)
            used, reserved = self.quotas[m.group(1)]
            unit = "https://qudt.org/vocab/unit/BYTE"
            return R({"data": {"resource": {"id": m.group(1)}, "usedPercentage": round(used * 100 / reserved, 2),
                               "used": {"value": used, "unit": unit}, "reserved": {"value": reserved, "unit": unit}}})
        if method == "GET" and path == "/projects":
            return R({"data": [{"id": "p1", "displayName": "Akzeptanzstudie"}, {"id": "p2", "displayName": "Fremd"}], **page})
        if method == "GET" and re.fullmatch(r"/projects/p\d/members/me", path):
            return R({"data": {"role": {"id": MEMBER if "/p1/" in path else GUEST}}})
        if method == "GET" and path == "/projects/p1/resources":
            return R({"data": list(resources.values()), **page})
        if method == "GET" and path == "/projects/p1":
            return R({"data": {"id": "p1", "displayName": "Akzeptanzstudie"}})
        if method == "GET" and re.fullmatch(r"/projects/p1/resources/r\d", path):
            return R({"data": resources[path.rsplit("/", 1)[1]]})
        if method == "GET" and path.startswith("/application-profiles/profiles/"):
            uri = unquote(path.rsplit("/", 1)[1])
            return R({"data": {"uri": uri, "displayName": uri.rstrip("/").rsplit("/", 1)[1],
                               "definition": {"content": PROFILES[uri], "type": "text/turtle"}}})
        m = re.fullmatch(r"/projects/p1/resources/(r\d)/trees/files", path)
        if method == "GET" and m:  # one level of a folder, its subfolders as "Tree" (K2)
            folder = params["Path"].lstrip("/")
            items, subfolders = [], set()
            for name, content in self.files.items():
                if self.where[name] != m.group(1) or not name.startswith(folder):
                    continue
                rest = name[len(folder):]
                if "/" in rest:
                    subfolders.add(folder + rest.split("/")[0] + "/")
                else:
                    items.append({"path": "/" + name, "type": "Leaf", "name": rest, "size": len(content),
                                  "changeDate": "2026-10-20T08:00:00Z"})
            items += [{"path": "/" + f, "type": "Tree"} for f in sorted(subfolders)]
            return R({"data": items, **page})
        m = re.fullmatch(r"/projects/p1/resources/(r\d)/storage/([^/]+)/content", path)
        if method == "GET" and m:
            r = R(None)
            r.content = self.files[unquote(unquote(m.group(2)))]
            return r
        m = re.fullmatch(r"/projects/p1/resources/r\d/graphs/([^/]+)/metadata/content", path)
        if method == "PUT" and m:
            assert headers["Content-Type"] == "text/turtle"
            self.meta[unquote(unquote(m.group(1)))] = data.decode()
            return R({}, 201)
        m = re.fullmatch(r"/projects/p1/resources/(r\d)/storage/([^/]+)/content", path)
        if method == "POST" and m:
            name = unquote(unquote(m.group(2)))
            assert name in self.meta  # metadata first, as Coscine wants
            self.files[name] = files["file"][1]
            self.where[name] = m.group(1)
            return R(None, 201)
        raise AssertionError((method, url))


@pytest.fixture
def fake(app, monkeypatch):
    f = FakeCoscine()
    app.config["COSCINE_API"] = BASE
    monkeypatch.setattr(coscine.requests, "request", f)
    return f


def on(client):
    """Coscine is for everyone since 2026-10-20 (no longer experimental): nothing to switch on."""


def connect(client, fake, token=None):
    fake.token = token or jwt()
    return client.post("/coscine/connect", data={"token": fake.token}, follow_redirects=True)


def test_for_everyone_without_a_tick(app, client, survey, fake):
    """No longer experimental (Ralf, 2026-10-20): the connection and the block show without any switch."""
    page = client.get("/account/connections").data.decode()
    assert "Verbindung zu Coscine" in page and "badge-exp" not in page
    # the steps with a direct link to Coscine's token page (option C, 2026-10-20)
    assert 'href="https://coscine.rwth-aachen.de/user/"' in page and "nur einmal" in page and "Anmeldearten" in page
    assert "Coscine (Forschungsdatenarchiv der RWTH Aachen)" not in client.get("/account/").data.decode()
    assert "coscine" not in experimental.KEYS


def test_connect_replace_and_disconnect(app, client, fake):
    on(client)
    fake.token = "valid"
    r = client.post("/coscine/connect", data={"token": "falsch"}, follow_redirects=True)
    assert "lehnt das Token ab" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(CoscineConnection)) is None
    page = connect(client, fake, jwt(days=10)).data.decode()
    assert "Mit Coscine verbunden als Ralf Philipsen" in page and "läuft in 9 Tagen ab" in page
    with app.app_context():
        conn = db.session.scalar(db.select(CoscineConnection))
        assert fake.token not in conn.token and conn.expires_at is not None  # stored encrypted
    connect(client, fake, jwt(days=300))
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(CoscineConnection.id))) == 1
    assert "Trennen und Token löschen" in client.get("/account/connections").data.decode()
    client.post("/coscine/disconnect")
    with app.app_context():
        assert db.session.scalar(db.select(CoscineConnection)) is None


def test_store_the_package_with_risk_metadata(app, client, survey, fake):
    from tests.conftest import add

    on(client)
    add(client, "likert")
    page = client.get("/surveys/1/data").data.decode()
    assert "Coscine-Token hinterlegen" in page and 'href="/coscine/survey/1"' not in page
    connect(client, fake)
    assert 'href="/coscine/survey/1"' in client.get("/surveys/1/data").data.decode()

    page = client.get("/coscine/survey/1").data.decode()
    assert 'value="p1:r1"' in page and "Befragungsdaten" in page
    assert 'value="p1:r2"' in page and "Rohdaten S3 (S3)" in page and "Archiv (WORM, unveränderlich)" in page
    assert 'value="p1:r4"' not in page and "Fremd" not in page      # linked data takes no files; a guest cannot write
    page = client.get("/coscine/survey/1?ziel=p1:r1").data.decode()
    assert "Ethikantrag" in page
    assert 'value="Test"' in page and '<option value="Quantitativ" selected>' in page
    assert 'value="Philipp"' in page and 'value="Akzeptanzstudie"' in page

    fields = {f["name"].strip(): f["key"] for f in coscine.Profile(RISK, "RISK", PROFILES[RISK]).fields}
    form = {"ziel": "p1:r1", "paket": "1", fields["Titel"]: "Test", fields["Ersteller"]: "Philipp; Julia Offermann",
            fields["Erstellungsdatum"]: "2026-10-04", fields["Projekt"]: "Akzeptanzstudie",
            fields["Inhalt der Erhebung"]: "Technikakzeptanz", fields["Datenart"]: "Quantitativ",
            fields["Erhebungszeitraum"]: "Oktober 2026", fields["Verantwortliche Person"]: "Philipp", fields["N Rohdaten"]: "12"}
    r = client.post("/coscine/survey/1", data=form)
    assert r.status_code == 400 and "Bitte „Ethikantrag“ ausfüllen." in r.data.decode() and not fake.files
    r = client.post("/coscine/survey/1", data={**form, fields["N Rohdaten"]: "0", fields["Ethikantrag"]: "Ja"})
    assert r.status_code == 400 and "zwischen 1 und 20000" in r.data.decode()
    r = client.post("/coscine/survey/1", data={**form, fields["Ethikantrag"]: "Ja"}, follow_redirects=True)
    assert "7 Dateien in Coscine abgelegt" in r.data.decode()

    folders = {name.rsplit("/", 1)[0] for name in fake.files}
    assert len(folders) == 1 and re.fullmatch(r"askurious/[\w-]+/\d{4}-\d\d-\d\d_\d{6}", folders.pop())
    names = {name.rsplit("/", 1)[1] for name in fake.files}
    assert names == {"data.csv", "codebook.csv", "questionnaire.md", "README.md", "survey.json", "survey-material.pdf", "checksums.sha256"}
    meta = next(t for n, t in fake.meta.items() if n.endswith("/data.csv"))
    assert meta.startswith("_:m a <https://purl.org/coscine/ap/base/>")
    assert '<http://purl.org/dc/terms/title> "Test – data.csv"^^<http://www.w3.org/2001/XMLSchema#string>' in meta
    assert meta.count("<http://purl.org/dc/terms/creator>") == 2 and '"Ja"' in meta
    assert '"12"^^<http://www.w3.org/2001/XMLSchema#integer>' in meta
    sums = next(d for n, d in fake.files.items() if n.endswith("checksums.sha256")).decode()
    assert "  data.csv\n" in sums and "  README.md\n" in sums

    with app.app_context():
        s = db.session.get(Survey, 1)
        entry = s.coscine["uploads"][0]
        assert (s.coscine["project"], s.coscine["resource"], entry["pid"]) == ("p1", "r1", "21.11102/r1")
        assert len(entry["files"]) == 7 and s.coscine["values"][RISK]["http://purl.org/dc/terms/identifier"] == "Ja"
    page = client.get("/surveys/1/data").data.decode()
    assert "Akzeptanzstudie › Befragungsdaten" in page and "https://hdl.handle.net/21.11102/r1" in page
    page = client.get("/coscine/survey/1?ziel=p1:r1").data.decode()
    assert '<option value="Ja" selected>' in page                        # remembered for the next upload


def test_profiles_askurious_cannot_fill(app, client, survey, fake):
    basis = coscine.Profile(BASIS, "Basis", PROFILES[BASIS])
    assert basis.missing() == [] and [f["name"] for f in basis.fields if basis.offered(f)] == ["Titel", "Ersteller", "Erstellungsdatum"]
    assert "<http://purl.org/dc/terms/type> <http://purl.org/dc/dcmitype/Dataset>" in coscine.turtle(basis, {})
    strict = coscine.Profile(STRICT, "Streng", PROFILES[STRICT])
    assert [f["name"] for f in strict.missing()] == ["Fachrichtung"]
    on(client)
    connect(client, fake)
    fake.profile = STRICT
    page = client.get("/coscine/survey/1?ziel=p1:r1").data.decode()
    assert "Askurious nicht ausfüllen kann: Fachrichtung" in page
    r = client.post("/coscine/survey/1", data={"ziel": "p1:r1", "paket": "1", "f0": "Test"})
    assert r.status_code == 400 and not fake.files


def test_only_admins_and_not_for_others(app, client, survey, fake):
    on(client)
    connect(client, fake)
    fake.token = "other"  # Coscine rejects the token now (revoked)
    r = client.get("/coscine/survey/1", follow_redirects=True)
    assert "lehnt das Token ab" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members[0].role = "editor"
        db.session.commit()
    client.get("/coscine/survey/1")
    assert "In Coscine ablegen" not in client.get("/surveys/1/data").data.decode()


def test_interview_guide_versions(app, client, fake):
    from tests.interview.test_interview import make

    on(client)
    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "ethik"})
    connect(client, fake)
    assert f'href="/coscine/survey/{sid}"' in client.get(f"/guide/{code}/export").data.decode()
    fake.profile = BASIS
    page = client.get(f"/coscine/survey/{sid}?ziel=p1:r1").data.decode()
    assert 'name="fassung:fassung-1.zip"' in page and "Datenpaket" not in page
    basis = coscine.Profile(BASIS, "Basis", PROFILES[BASIS])
    keys = {f["name"]: f["key"] for f in basis.fields}
    r = client.post(f"/coscine/survey/{sid}", data={"ziel": "p1:r1", "fassung:fassung-1.zip": "1", keys["Titel"]: "Leitfaden",
                                                     keys["Ersteller"]: "Philipp", keys["Erstellungsdatum"]: "2026-10-04"})
    assert r.headers["Location"].endswith(f"/guide/{code}/export#coscine")
    assert sorted(n.split("/", 3)[3] for n in fake.files) == ["checksums.sha256", "fassungen/fassung-1.zip"]


def _basis_form(target="p1:r1", **extra):
    keys = {f["name"]: f["key"] for f in coscine.Profile(BASIS, "Basis", PROFILES[BASIS]).fields}
    return {"ziel": target, keys["Titel"]: "Test", keys["Ersteller"]: "Philipp", keys["Erstellungsdatum"]: "2026-10-04", **extra}


def test_s3_and_worm(app, client, survey, fake):
    on(client)
    connect(client, fake)
    fake.profile = BASIS
    page = client.get("/coscine/survey/1?ziel=p1:r3").data.decode()
    assert "Archiv</strong> (WORM, unveränderlich)" in page and "lässt sich nie ändern" in page
    assert "lässt sich nie ändern" not in client.get("/coscine/survey/1?ziel=p1:r2").data.decode()
    client.post("/coscine/survey/1", data=_basis_form("p1:r3", paket="1"))
    assert set(fake.where.values()) == {"r3"} and len(fake.files) == 7
    r = client.get("/coscine/survey/1?ziel=p1:r4", follow_redirects=True)
    assert "nimmt keine Dateien über Askurious an" in r.data.decode()


def test_automatic_upload_is_optional(app, client, survey, fake):
    from tests.exports.test_prereg import _start

    on(client)
    connect(client, fake)
    fake.profile = BASIS
    assert "Automatisch ablegen" not in client.get("/surveys/1/data").data.decode()  # only after a first upload
    r = client.post("/coscine/survey/1/automatic", data={"auto": "1"}, follow_redirects=True)
    assert "braucht ein hinterlegtes Coscine-Token und eine erste Ablage" in r.data.decode()
    client.post("/coscine/survey/1", data=_basis_form(paket="1"))
    page = client.get("/surveys/1/data").data.decode()
    assert "Automatisch ablegen bei jedem Start der Feldphase" in page and 'name="auto" value="1" checked' not in page
    fake.files.clear()
    _start(client)                                                   # not ticked: nothing goes to Coscine
    assert not fake.files
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and len(s.coscine["uploads"]) == 1


def test_automatic_upload_at_the_start(app, client, survey, fake):
    from tests.exports.test_prereg import _start

    on(client)
    connect(client, fake)
    fake.profile = BASIS
    client.post("/coscine/survey/1", data=_basis_form(paket="1"))
    r = client.post("/coscine/survey/1/automatic", data={"auto": "1", "autosave": "1"})
    assert r.get_json()["status"] == "saved"
    assert 'name="auto" value="1" checked' in client.get("/surveys/1/data").data.decode()
    fake.files.clear()
    page = _start(client).data.decode()
    assert "Automatisch in Coscine abgelegt" in page
    names = sorted(n.split("/", 3)[3] for n in fake.files)
    assert len(names) == 2 and names[0] == "checksums.sha256" and names[1].startswith("praeregistrierung/v1_")
    meta = next(t for n, t in fake.meta.items() if "praeregistrierung/" in n)
    assert '"Test – v1_' in meta and "Philipp" in meta
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.coscine["uploads"][0]["auto"] is True and s.coscine["auto"]["on"] is True
    assert "· automatisch" in client.get("/surveys/1/data").data.decode()


def test_automatic_upload_failing_does_not_stop_the_start(app, client, survey, fake):
    from tests.exports.test_prereg import _start

    on(client)
    connect(client, fake)
    fake.profile = BASIS
    client.post("/coscine/survey/1", data=_basis_form(paket="1"))
    client.post("/coscine/survey/1/automatic", data={"auto": "1"})
    fake.token = "revoked"
    page = _start(client).data.decode()
    assert "Die Befragung ist gestartet." in page and "Automatische Ablage in Coscine fehlgeschlagen" in page
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING
    assert "lehnt das Token ab" in client.get("/surveys/1/data").data.decode()
    client.post("/coscine/survey/1/automatic", data={})       # ticked off again
    with app.app_context():
        assert db.session.get(Survey, 1).coscine["auto"] == {"on": False}


def test_automatic_upload_of_a_new_version(app, client, fake):
    from tests.interview.test_interview import make

    on(client)
    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "ethik"})
    connect(client, fake)
    fake.profile = BASIS
    client.post(f"/coscine/survey/{sid}", data={**_basis_form(), "fassung:fassung-1.zip": "1"})
    assert "Automatisch ablegen bei jeder neuen Fassung" in client.get(f"/guide/{code}/export").data.decode()
    client.post(f"/coscine/survey/{sid}/automatic", data={"auto": "1"})
    fake.files.clear()
    r = client.post(f"/guide/{code}/versions", data={"note": "endgueltig"}, follow_redirects=True)
    assert "Automatisch in Coscine abgelegt" in r.data.decode()
    assert sorted(n.split("/", 3)[3] for n in fake.files) == ["checksums.sha256", "fassungen/fassung-2.zip"]


def test_automatic_upload_stops_when_the_target_was_moved(app, client, survey, fake):
    """Abgleich 2026-10-15 H2: the token of X writes only into the target X switched it on for; another admin who
    moves the target by hand switches the automatic upload off, X moving it takes it along."""
    from tests.exports.test_prereg import _start

    on(client)
    connect(client, fake)
    fake.profile = BASIS
    client.post("/coscine/survey/1", data=_basis_form(paket="1"))
    client.post("/coscine/survey/1/automatic", data={"auto": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        auto = s.coscine["auto"]
        assert (auto["project"], auto["resource"]) == ("p1", "r1")
        assert coscine._auto_after_target(s.coscine, "p1", "r2", auto["user_id"]) == (
            {"auto": {**auto, "resource": "r2"}}, False)
        assert coscine._auto_after_target(s.coscine, "p1", "r2", auto["user_id"] + 1) == ({"auto": {"on": False}}, True)
        s.coscine = {**s.coscine, "resource": "r2"}  # as a manual upload of a co-admin left it before this fix
        db.session.commit()
    fake.files.clear()
    page = _start(client).data.decode()
    assert "Das Ziel wurde seit dem Einschalten geändert" in page and not fake.files
    with app.app_context():
        assert db.session.get(Survey, 1).coscine["auto"]["on"] is False


def test_preregistration_with_licensed_wording_is_not_ticked_nor_sent_on_its_own(app, client, survey, monkeypatch):
    """A survey with GDMS (APA PsycTests): its frozen preregistration holds the full wording, so it is not ticked
    for Coscine, says why, and the automatic upload leaves it to a person (copyright audit 2026-10-16, U5)."""
    from datetime import datetime

    from app import prereg

    grant_instrument(client.application, "gdms")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    monkeypatch.setattr(prereg, "listing", lambda s: [("v1", 1, datetime(2026, 10, 16), None)])
    with app.app_context():
        s = db.session.get(Survey, 1)
        entry = next(c for c in coscine.choices(s) if c[0] == "prereg:v1")
        assert entry[3] is False and "ohne allgemeine Veröffentlichungsfreigabe" in entry[2]
        s.coscine = {"auto": {"on": True, "user_id": 1}}
        db.session.commit()
        monkeypatch.setattr(coscine, "client_for", lambda user: (_ for _ in ()).throw(AssertionError("token used")))
        ok, text = coscine.auto_store(db.session.get(Survey, 1), "prereg:v1")
        assert ok is False and "nicht automatisch" in text
