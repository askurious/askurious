"""One check for every e-mail field (app/emails.py, Ralf 2026-10-11): the contact e-mail of the consent is refused
when saved, links by mail name the rows whose address is not valid."""
import pytest

from app.emails import is_email
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import mail_on


@pytest.mark.parametrize("value", ["a@b.de", " Anna.Muster@uni-x.de ", "x+tag@sub.example.org"])
def test_addresses(value):
    assert is_email(value)


@pytest.mark.parametrize("value", ["", "kein-mail", "a@b", "a b@x.de", "a,b@x.de", "a;b@x.de", "<a@x.de>",
                                   "a@x.de\nb@x.de", "a@" + "x" * 250 + ".de"])
def test_no_addresses(value):
    assert not is_email(value)


def test_consent_contact_email_refused_when_saved(app, client, survey):
    page = client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X",
                                                         "contact_email": "studie@uni"}).data.decode()
    assert "gültige E-Mail-Adresse" in page
    r = client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X",
                                                      "contact_email": "a,b@uni.de", "autosave": "1"}).get_json()
    assert r["status"] == "invalid" and "Kontakt-E-Mail" in r["message"]
    with app.app_context():
        assert not db.session.get(Item, 1).config.get("contact_email")
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X",
                                                  "contact_email": "studie@uni.de"})
    with app.app_context():
        assert db.session.get(Item, 1).config["contact_email"] == "studie@uni.de"


def test_links_by_mail_name_invalid_rows(app, client, survey):
    sent = mail_on(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "w", "mode": "store", "allowed": ["abc1", "def2", "ghi3"]}]}
        db.session.commit()
    page = client.post("/surveys/1/value-links/mail", data={"mail:w:abc1": "stud@example.org", "mail:w:def2": "stud@example",
                                                            "mail:w:ghi3": ""}, follow_redirects=True).data.decode()
    assert [m["To"] for m in sent] == ["stud@example.org"]
    assert "Nicht verschickt, die E-Mail-Adresse ist ungültig: def2." in page
