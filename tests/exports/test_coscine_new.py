"""Coscine (app/coscine.py, experimental), Coscine-Ausbau 2026-10-20, package C (K1): a project or sub-project with a
first resource, or a resource in a project of the person, created from Askurious. Coscine's API is replaced by
FakeCoscine of test_coscine.py."""
import re
from datetime import date

from app import coscine
from tests.exports.test_coscine import connect, fake, on  # noqa: F401 - fake is a fixture


def _form(**changes):
    form = {"wohin": "neu", "name": "Akzeptanz von Assistenzrobotern", "beschreibung": "Online-Befragung 2026",
            "leitung": "Ralf Philipsen", "start": "2026-10-01", "ende": "2027-10-01", "ressource": "Befragungsdaten",
            "disziplin": "d2", "typ": "t-s3", "gb": "3", "profil": coscine.RISK_PROFILE, "lizenz": "l1"}
    return {**form, **changes}


def test_new_page_offers_what_coscine_lists_with_suggestions(app, client, survey, fake):
    on(client)
    connect(client, fake)
    assert "Neu in Coscine anlegen" in client.get("/coscine/survey/1").data.decode()
    page = client.get("/coscine/survey/1/new").data.decode()
    assert '<option value="d2" selected>Sozialwissenschaften</option>' in page        # social sciences by default
    assert 'value="l1" selected' in page and 'value="t-s3" selected' in page          # CC BY 4.0, S3
    assert "t-gitlab" not in page                                                       # a type without storage
    assert 'name="gb" value="1"' in page and "RWTH Aachen" in page                     # at least 1 GB
    assert 'value="sub:p1"' in page and 'value="in:p1"' in page                        # a guest project is left out:
    assert ':p2"' not in page and "Fremd" not in page  # not "p2" anywhere: a random CSRF token can hold it (2026-10-27)


def test_create_project_with_resource(app, client, survey, fake):
    on(client)
    connect(client, fake)
    r = client.post("/coscine/survey/1/new", data=_form())
    assert r.status_code == 302 and r.headers["Location"].endswith("/coscine/survey/1?ziel=p11:r12")
    (_, project), (_, where, resource) = fake.created
    assert project["name"] == "Akzeptanz von Assistenzrobotern" and project["displayName"] == "Akzeptanz von Assistenzro"
    assert project["visibility"] == {"id": "v2"} and project["disciplines"] == [{"id": "d2"}]
    assert project["organizations"] == [{"uri": "https://ror.org/04xfq0f34", "responsible": True}]
    assert project["startDate"] == "2026-10-01T00:00:00Z" and project["endDate"] == "2027-10-01T00:00:00Z"
    assert "parentId" not in project
    assert where == "p11" and resource["resourceTypeId"] == "t-s3" and resource["license"] == {"id": "l1"}
    assert resource["applicationProfile"] == {"uri": coscine.RISK_PROFILE} and resource["visibility"] == {"id": "v2"}
    assert resource["resourceTypeOptions"] == {"dataStorageNrwS3ResourceTypeOptions": {
        "quota": {"value": 3, "unit": "https://qudt.org/vocab/unit/GibiBYTE"}}}


def test_sub_project_and_resource_in_an_existing_project(app, client, survey, fake):
    on(client)
    connect(client, fake)
    client.post("/coscine/survey/1/new", data=_form(wohin="sub:p1"))
    assert fake.created[0][1]["parentId"] == "p1" and fake.created[0][1]["copyOwnersFromParent"] is True
    fake.created.clear()
    r = client.post("/coscine/survey/1/new", data=_form(wohin="in:p1", typ="t-web"))
    assert r.headers["Location"].endswith("ziel=p1:r11")
    assert [c[0] for c in fake.created] == ["resource"]
    assert "dataStorageNrwWebResourceTypeOptions" in fake.created[0][2]["resourceTypeOptions"]


def test_refused_forms_and_a_failed_resource(app, client, survey, fake):
    on(client)
    connect(client, fake)
    for bad in (_form(disziplin="x"), _form(typ="t-gitlab"), _form(gb="0"), _form(ressource=""), _form(wohin="in:p2"),
                _form(wohin="sub:p2"), _form(leitung=""), _form(ende=""), _form(ende="2026-09-30")):
        r = client.post("/coscine/survey/1/new", data=bad)
        assert r.status_code == 400
    assert not fake.created
    fake.refuse_resource = True
    r = client.post("/coscine/survey/1/new", data=_form())
    page = r.data.decode()
    assert r.status_code == 400 and "Das Projekt ist in Coscine angelegt, die Ressource nicht" in page
    assert "Not enough quota" in page and [c[0] for c in fake.created] == ["project"]


def test_a_refusal_without_reason_says_what_the_status_means(app, client, survey, fake, caplog):
    """Ralf 2026-10-20: a new project refused with an empty answer showed only "Coscine nimmt die Anfrage nicht an: –"."""
    on(client)
    connect(client, fake)
    fake.refuse_project = 404
    page = client.post("/coscine/survey/1/new", data=_form()).data.decode()
    assert "(HTTP 404)" in page and "Coscine kennt einen der mitgeschickten Einträge nicht" in page and ": –" not in page
    assert "https://ror.org/04xfq0f34" in caplog.text and not fake.created  # the log names what Coscine may not know
    fake.refuse_project = 502
    assert "Fehler in Coscine selbst" in client.post("/coscine/survey/1/new", data=_form()).data.decode()


def test_a_new_project_always_has_start_and_end(app, client, survey, fake):
    """Ralf 2026-10-20: Coscine answered 500 to a project without an end date (its database has no default)."""
    on(client)
    connect(client, fake)
    page = client.get("/coscine/survey/1/new").data.decode()
    start = re.search(r'name="start" value="([\d-]+)"', page).group(1)
    end = re.search(r'name="ende" value="([\d-]+)"', page).group(1)
    assert int(end[:4]) == int(start[:4]) + 1 and end[4:] == start[4:]  # a year after the start by default
    assert coscine._a_year_after(date(2028, 2, 29)) == date(2029, 2, 28)


def test_only_the_first_organisation_is_responsible(app, client, survey, fake, monkeypatch):
    on(client)
    connect(client, fake)
    real = coscine.Client.me
    monkeypatch.setattr(coscine.Client, "me", lambda self: {**real(self), "organizations": [
        {"uri": "https://ror.org/04xfq0f34", "displayName": "RWTH Aachen"},
        {"uri": "https://ror.org/02nv7yv05", "displayName": "FZ Jülich"}]})
    client.post("/coscine/survey/1/new", data=_form())
    assert [o["responsible"] for o in fake.created[0][1]["organizations"]] == [True, False]


def test_the_resource_fits_the_free_quota(app, client, survey, fake):
    """Ralf 2026-10-20: a new project had no quota for S3, Coscine refused the resource with 400."""
    on(client)
    connect(client, fake)
    fake.quotas_free = {"t-s3": 0, "t-web": 5}
    client.post("/coscine/survey/1/new", data=_form(gb="3"))
    with client.session_transaction() as sess:
        said = " ".join(text for _, text in sess.get("_flashes", []))
    assert "kein Kontingent; die Ressource ist deshalb vom Typ „Web“ (3 GB)" in said
    resource = fake.created[-1][2]
    assert resource["resourceTypeId"] == "t-web" and "dataStorageNrwWebResourceTypeOptions" in resource["resourceTypeOptions"]
    fake.created.clear()
    fake.quotas_free = {"t-s3": 2, "t-web": 0}
    client.post("/coscine/survey/1/new", data=_form(wohin="in:p1", gb="3"))
    assert fake.created[-1][2]["resourceTypeOptions"]["dataStorageNrwS3ResourceTypeOptions"]["quota"]["value"] == 2
    fake.created.clear()
    fake.quotas_free = {"t-s3": 0, "t-web": 0}
    r = client.post("/coscine/survey/1/new", data=_form())
    page = r.data.decode()
    assert r.status_code == 400 and "kein freies Speicherkontingent" in page and "Das Projekt ist in Coscine angelegt" in page
    assert [c[0] for c in fake.created] == ["project"]


def test_the_kind_of_error_is_named_when_coscine_gives_no_title(app, client, survey, fake):
    """Ralf 2026-10-20: the resource of a new project was refused with only "–"; Coscine names the kind in errorId."""
    on(client)
    connect(client, fake)
    fake.refuse_resource = "id"
    page = client.post("/coscine/survey/1/new", data=_form()).data.decode()
    assert "(ResourceQuotaExceededBadRequestException)" in page and "Das Projekt ist in Coscine angelegt" in page
