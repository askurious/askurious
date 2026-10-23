"""Logos of funders and partners in a survey's theme: at most three, small, on the first and the last page."""
import io
import json
import os

from PIL import Image

from tests.conftest import CONSENT_OK, add, fill_consent

from app import snapshots, theme
from app.extensions import db
from app.models import Design, Survey

BASE = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft"}


def png(width=320, height=80):
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 97, 101, 255)).save(out, format="PNG")
    return out.getvalue()


def add_partner(client, name="Deutsche Forschungsgemeinschaft", rights="1", data=None, sid=1):
    form = {"file": (io.BytesIO(data or png()), "p.png"), "alt": name}
    if rights:
        form["rights"] = rights
    return client.post(f"/surveys/{sid}/design/partner", data=form, content_type="multipart/form-data", follow_redirects=True).data.decode()


def partners(client, sid=1):
    with client.application.app_context():
        return (theme.clean(db.session.get(Survey, sid).theme) or {}).get("partners", [])


def stored(client, name, sid=1):
    with client.application.app_context():
        return os.path.join(client.application.config["UPLOAD_FOLDER"], db.session.get(Survey, sid).uid, name)


def test_partner_logos_stand_on_the_first_and_the_last_page(app, client, survey):
    fill_consent(client)
    add(client, "info")
    add(client, "page_break")
    add(client, "info")
    assert "Partnerlogo hinzugefügt" in add_partner(client)
    assert "Namen des Partners" in add_partner(client, name="  ")
    assert "Bitte bestätigen Sie" in add_partner(client, name="BMBF", rights="")
    add_partner(client, name="BMBF")
    add_partner(client, name="Stiftung Beispiel")
    assert "Mehr als 3 Partnerlogos" in add_partner(client, name="Vierter")
    listed = partners(client)
    assert [p["alt"] for p in listed] == ["Deutsche Forschungsgemeinschaft", "BMBF", "Stiftung Beispiel"]
    assert all(os.path.isfile(stored(client, p["file"])) for p in listed)
    # colours, font and "Auf Standard zurücksetzen" leave them alone
    client.post("/surveys/1/design", data={**BASE, "accent": "#8a1c3b"})
    client.post("/surveys/1/design", data={"reset": "1"})
    client.post("/surveys/1/design/choose", data={"preset": "askurious-dark"})
    assert len(partners(client)) == 3
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    p = app.test_client()
    first = p.get(f"/s/{code}").data.decode()
    assert first.count('<img src="/media/') == 3 and 'alt="Deutsche Forschungsgemeinschaft" width="128" height="32"' in first
    assert "--partner-bg: #ffffff" in first                                     # dark page: the logos stand on white
    second = p.post(f"/s/{code}", data=CONSENT_OK, follow_redirects=True).data.decode()
    assert "p-partners" not in second                                           # not on the pages with questions
    p.post(f"/s/{code}", data={"nav": "next"})
    end = p.post(f"/s/{code}", data={"nav": "next"}, follow_redirects=True).data.decode()
    assert "p-end" in end and end.count('<img src="/media/') == 3


def test_removing_a_partner_logo(client, survey):
    add_partner(client, name="Eins")
    add_partner(client, name="Zwei")
    first = partners(client)[0]["file"]
    client.post("/surveys/1/design/partner", data={"remove": "0"})
    assert [p["alt"] for p in partners(client)] == ["Zwei"] and not os.path.exists(stored(client, first))
    client.post("/surveys/1/design/partner", data={"remove": "7"})              # no such position: nothing happens
    assert len(partners(client)) == 1
    with client.application.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    assert "nur im Entwurf änderbar" in add_partner(client, name="Drei")
    client.post("/surveys/1/design/partner", data={"remove": "0"})
    assert len(partners(client)) == 1


def test_designs_and_snapshots_carry_the_partner_logos(app, client, survey):
    fill_consent(client)
    add(client, "info")
    add_partner(client, name="Eins")
    add_partner(client, name="Zwei")
    client.post("/surveys/1/design/save", data={"name": "Mit Partnern"})
    with app.app_context():
        design = db.session.scalar(db.select(Design))
        did, kept = design.id, design.data["partners"]
        folder = os.path.join(app.config["UPLOAD_FOLDER"], f"_design_{did}")
        assert [p["alt"] for p in kept] == ["Eins", "Zwei"] and all(os.path.isfile(os.path.join(folder, p["file"])) for p in kept)
        assert {p["file"] for p in kept}.isdisjoint({p["file"] for p in partners(client)})
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    add_partner(client, name="Alt", sid=2)
    old = partners(client, 2)[0]["file"]
    client.post("/surveys/2/design/apply", data={"design": did})
    two = partners(client, 2)
    assert [p["alt"] for p in two] == ["Eins", "Zwei"] and all(os.path.isfile(stored(client, p["file"], 2)) for p in two)
    assert not os.path.exists(stored(client, old, 2))                           # the survey's former partner logo is gone
    with app.app_context():
        s = db.session.get(Survey, 1)
        name = snapshots.save(s)
        assert snapshots.extra_files(s, name) == []  # since E4 in the survey file of the snapshot
        with open(snapshots.path(s, name, "survey.json"), encoding="utf-8") as f:
            data = json.load(f)
        kept = data["theme"]["partners"]
        assert [p["alt"] for p in kept] == ["Eins", "Zwei"] and all(data["files"].get(p["file"]) for p in kept)
    assert ">Befragungsdatei</a>" in client.get("/surveys/1/data").data.decode()
