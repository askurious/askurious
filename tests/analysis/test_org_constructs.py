"""Constructs of an organisation (app/org_constructs.py): admins make one from a linked element, members propose one,
only the organisation's members find it, it stays out of the pool, and it comes as a file for a pull request to
askurious-constructs."""
import io
import json
import zipfile

from app import constructs, org_constructs, orgs
from app.extensions import db
from app.models import Construct, ConstructSubmission
from tests.editor.test_linked import _linked_question

FORM = {"action": "create", "name": "Vorwissen zu Robotern", "description": "Ob jemand schon mit Robotern zu tun hatte.",
        "citation": "Muster, E. (2026). Eine Beispielfrage.", "doi": "10.1234/beispiel.1", "scoring": "1 = ja, 2 = nein.",
        "licence": "CC-BY-4.0", "rights": "1"}


def _main(app):
    with app.app_context():
        return orgs.default_id()


def test_an_admin_creates_a_construct_of_the_organisation(app, client, survey):
    key = _linked_question(app, client)
    main = _main(app)
    page = client.get(f"/org/{main}/constructs").data.decode()
    assert "Eigene Konstrukte" in page and "Konstrukt anlegen" in page and "Vorwissen" in page
    assert "philipsen@comm.rwth-aachen.de" in page  # the contact for every installation (CONSTRUCTS_CONTACT unset)
    answer = client.post(f"/org/{main}/constructs/own", data={**FORM, "element": key}, follow_redirects=True)
    assert "Angelegt" in answer.data.decode()
    with app.app_context():
        c = db.session.scalar(db.select(Construct).filter_by(visibility="org"))
        assert c.key == f"o{main}-vorwissen_zu_robotern" and c.org_id == main and c.source == "org"
        assert c.spec["licence"] == "CC-BY-4.0" and c.spec["config"]["options"][0]["code"] == "1"
        assert not constructs.pooled(c.key)  # never in the operator's pool
        ckey = c.key
    # members find it in the toolbox with the organisation's name; it can be inserted like any construct
    assert ckey in client.get("/constructs/?q=vorwissen").data.decode()
    assert client.get(f"/constructs/{ckey}").status_code == 200
    client.post("/surveys/1/editor/blocks/2/items", data={"type": f"construct:{ckey}"})
    with app.app_context():
        assert db.session.scalars(db.select(Construct.key).filter_by(key=ckey)).first()
    # a guest and an account of no organisation of it do not see it
    guest = app.test_client()
    assert ckey not in guest.get("/constructs/?q=vorwissen").data.decode()
    assert guest.get(f"/constructs/{ckey}").status_code == 404
    with app.app_context():
        assert ckey not in [c.key for c in db.session.scalars(constructs.visible(user=None))]


def test_the_dialog_asks_for_everything(app, client, survey):
    key = _linked_question(app, client)
    main = _main(app)
    for missing in ("description", "citation", "scoring", "rights"):
        data = {**FORM, "element": key}
        data.pop(missing)
        page = client.post(f"/org/{main}/constructs/own", data=data, follow_redirects=True).data.decode()
        assert "Bitte ausfüllen" in page, missing
    bad = client.post(f"/org/{main}/constructs/own", data={**FORM, "element": "v-gibtsnicht"}, follow_redirects=True)
    assert "verknüpftes Element als Grundlage" in bad.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(Construct).filter_by(visibility="org")) is None


def test_a_member_proposes_and_the_admin_takes_it_over(app, client, survey):
    key = _linked_question(app, client)
    main = _main(app)
    linked_page = client.get(f"/constructs/linked/{key}").data.decode()
    assert "Der Organisation vorschlagen" in linked_page
    client.post(f"/constructs/linked/{key}", data={**FORM, "action": "propose_org"})
    again = client.post(f"/constructs/linked/{key}", data={**FORM, "action": "propose_org"}, follow_redirects=True)
    assert "schon vorgeschlagen" in again.data.decode()
    with app.app_context():
        s = db.session.scalar(db.select(ConstructSubmission).filter_by(org_id=main))
        assert s is not None and s.state == "submitted"
        from app import submissions

        assert s not in submissions.open_ones()  # not the operator's
        sid = s.id
    page = client.get(f"/org/{main}/constructs").data.decode()
    assert "Vorschläge der Mitglieder" in page
    client.post(f"/org/{main}/constructs/own", data={"action": "take", "sid": sid})
    with app.app_context():
        s = db.session.get(ConstructSubmission, sid)
        assert s.state == "accepted" and s.toolbox_key.startswith(f"o{main}-")
        assert db.session.scalar(db.select(Construct).filter_by(key=s.toolbox_key)).visibility == "org"
    assert "Übernommen als" in client.get(f"/constructs/linked/{key}").data.decode()


def test_the_file_for_a_pull_request(app, client, survey):
    key = _linked_question(app, client)
    main = _main(app)
    client.post(f"/org/{main}/constructs/own", data={**FORM, "element": key})
    with app.app_context():
        ckey = db.session.scalar(db.select(Construct.key).filter_by(visibility="org"))
    answer = client.get(f"/org/{main}/constructs/own/{ckey}.zip")
    assert answer.status_code == 200 and answer.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(answer.data)) as z:
        names = set(z.namelist())
        assert {"constructs/vorwissen_zu_robotern/construct.yaml", "README.txt"} <= names
        text = z.read("constructs/vorwissen_zu_robotern/construct.yaml").decode()
        data = json.loads(text[text.index("{"):])  # a comment, then JSON (valid YAML)
        assert data["key"] == "vorwissen_zu_robotern" and data["aliases"][0].endswith(f"/o{main}/vorwissen_zu_robotern")
        assert data["licence"]["wording"] == "CC-BY-4.0" and data["licence"]["distributable"] is True
        assert data["structure"]["type"] == "single_choice" and "construct" not in data["structure"]["config"]
        wording = json.loads(z.read("constructs/vorwissen_zu_robotern/wording/de.yaml"))
        assert wording["options"] == {"1": "Ja", "2": "Nein"} and wording["for"] == "ask:vorwissen_zu_robotern@1.0"


def test_a_licensed_wording_goes_into_the_file_only_as_its_hash(app, client, survey):
    key = _linked_question(app, client)
    main = _main(app)
    client.post(f"/org/{main}/constructs/own", data={**FORM, "element": key, "licence": "ZIS-noncommercial"})
    with app.app_context():
        c = db.session.scalar(db.select(Construct).filter_by(visibility="org"))
        data, wordings = org_constructs.construct_yaml(c)
    assert wordings == {} and data["licence"]["distributable"] is False
    assert all(info["wording"] == "overlay" and info["sha256"].startswith("sha256:") for info in data["languages"].values())


def test_accepted_submissions_offer_the_file_too(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        element = db.session.scalar(db.select(Construct).filter_by(key=key))
        c = org_constructs.create(type("O", (), {"id": 0, "name": "Instanz"})(), type("U", (), {"id": element.created_by_id})(),
                                  element, {**FORM, "licence": "CC0-1.0"})
        c.visibility, c.org_id, c.source, c.key = "public", None, "instance", "vorwissen_instanz"
        db.session.commit()
    assert client.get("/admin/constructs/vorwissen_instanz/pull-request.zip").status_code == 200
    assert client.get("/admin/constructs/kamb/pull-request.zip").status_code == 404  # the catalogue's own
    assert client.get(f"/admin/constructs/{key}/pull-request.zip").status_code == 404  # a linked element
