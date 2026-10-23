"""What Art. 13 GDPR asks of the consent and the privacy page of a survey (comparison with the RWTH template
2026-10-20, L1-L7, L10): legal basis, withdrawal, recipients, retention criteria, controller, data protection
officer, rights and supervisory authority; drafts get the new paragraph, started surveys keep their text."""
from app.extensions import db
from app.items import consent as consent_kind
from app.models import Item, Survey

FACTS = {"min_age": "18", "researchers": "0000-0000-0000-001X", "contact_email": "studie@uni.de"}


def _consent(app):
    return db.session.get(Survey, 1).consent_item


def _no_period(app):
    """No period for deleting the answers (Einstellungen › Aufbewahrung, "keine Frist"); new surveys have one."""
    with app.app_context():
        db.session.get(Survey, 1).retention = {"personal": 6, "answers": None}
        db.session.commit()


def test_standard_text_names_legal_basis_withdrawal_recipients_and_criteria(app, client, survey):
    _no_period(app)
    client.post("/surveys/1/consent-facts", data={**FACTS, "funding": "DFG, Förderkennzeichen 123456"})
    with app.app_context():
        item = _consent(app)
        de, en = item.kind.template_body(item, "de"), item.kind.template_body(item, "en")
        du = item.kind.template_body(item, "de-du")
    assert "Förderung: DFG, Förderkennzeichen 123456." in de and "Funding: DFG" in en
    assert "**Datenverarbeitung und Ihre Rechte:**" in de and "**Data processing and your rights:**" in en
    for sentence in ("Grundlage ist Ihre Einwilligung (Art. 6 Abs. 1 lit. a DSGVO).", "mit Wirkung für die Zukunft",
                     "bis zum Widerruf bleibt davon unberührt", "Server der Plattform", "in der Regel zehn Jahre",
                     "Datenschutzseite dieser Befragung", "ohne Namen und ohne IP-Adresse",
                     "keine Namen und keine anderen Angaben"):
        assert sentence in de, sentence
    assert "anonym gespeichert" not in de
    assert de.index("**Datenverarbeitung und Ihre Rechte:**") < de.index("**Kontakt:**")
    assert "deine Einwilligung" in du and " Sie " not in du.split("**Datenverarbeitung")[1].split("**Kontakt")[0]
    # nothing leaves the platform: the sentence "nicht an Dritte" may stay without open data
    client.post("/surveys/1/consent-facts", data={**FACTS, "funding": ""})
    with app.app_context():
        item = _consent(app)
        assert "und nicht an Dritte weitergegeben" in item.kind.template_body(item, "de")
        assert "Förderung" not in item.kind.template_body(item, "de")


def test_a_retention_period_replaces_the_criteria(app, client, survey):
    with app.app_context():  # a new survey deletes its answers after a period (models.Survey.retention)
        item = _consent(app)
        text = item.kind.rights_paragraph(item, "de")
    assert "zehn Jahre" not in text and "Art. 6" in text


def test_drafts_get_the_paragraph_started_surveys_keep_their_text(app, client, survey):
    _no_period(app)
    client.post("/surveys/1/consent-facts", data=FACTS)
    with app.app_context():
        item = _consent(app)
        new = item.kind.template_body(item, "de")
        # the standard text as it was before: the old sentence on anonymity, no paragraph on rights
        rights = new.split("\n\n")
        old = "\n\n".join(p for p in rights if not p.startswith("**Datenverarbeitung")).replace(
            consent_kind.anonymous_sentence("de"), consent_kind.FORMER_ANONYMOUS[0])
        own = "**Worum geht es?**\nEigener Text.\n\n**Kontakt**\nWir."
        item.texts = {**item.texts, "de": {**item.texts["de"], "body": old}, "en": {**item.texts["en"], "body": own}}
        survey_row = db.session.get(Survey, 1)
        survey_row.status = Survey.RUNNING
        db.session.commit()
        assert consent_kind.upgrade_drafts() == 0  # participants agreed to the text as it is
        survey_row.status = Survey.DRAFT
        db.session.commit()
        assert consent_kind.upgrade_drafts() == 1
        item = db.session.get(Item, item.id)
        assert item.texts["de"]["body"] == new  # an untouched standard text is the standard text again
        # an own text gets the paragraph before its "Contact"; this English text has none, so at the end
        assert item.texts["en"]["body"].startswith(own + "\n\n**Data processing and your rights:** ")
        assert consent_kind.upgrade_drafts() == 0


def test_privacy_page_names_controller_officer_rights_and_authority(app, client, survey):
    _no_period(app)
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    page = client.get(f"/s/{uid}/privacy?lang=de").data.decode()
    for words in ("Ihre Rechte", "Art. 15 bis 21 DSGVO", "Art. 77 DSGVO", "Art. 11 DSGVO",
                  "Grundlage ist Ihre Einwilligung", "Verantwortlich im Sinne der DSGVO sind die genannten Forschenden",
                  "Die Datenschutzbeauftragte erreichen Sie über die verantwortliche Einrichtung.",
                  "in der Regel zehn Jahre", "nur im Auftrag der Verantwortlichen"):
        assert words in page, words
    client.post("/surveys/1/consent-facts", data={**FACTS, "controller": "Universität Musterstadt, Musterstraße 1",
                                                  "dpo": "datenschutz@uni-musterstadt.de"})
    page = client.get(f"/s/{uid}/privacy?lang=en").data.decode()
    assert "Controller under the GDPR: Universität Musterstadt, Musterstraße 1" in page
    assert "Data protection officer: datenschutz@uni-musterstadt.de" in page
    assert "the researchers named above or the institution" not in page


def test_a_form_without_the_new_fields_keeps_them(app, client, survey):
    client.post("/surveys/1/consent-facts", data={**FACTS, "funding": "DFG", "controller": "Uni", "dpo": "dsb@uni.de"})
    client.post("/surveys/1/consent-facts", data=FACTS)
    with app.app_context():
        config = _consent(app).config
    assert (config["funding"], config["controller"], config["dpo"]) == ("DFG", "Uni", "dsb@uni.de")
    client.post("/surveys/1/consent-facts", data={**FACTS, "funding": "", "controller": "", "dpo": ""})
    with app.app_context():
        assert not any(_consent(app).config[k] for k in ("funding", "controller", "dpo"))


def test_interview_template_takes_contact_and_officer(app, client, survey):
    from app.interview_docx import _contact

    client.post("/surveys/1/consent-facts", data={**FACTS, "dpo": "dsb@uni.de"})
    with app.app_context():
        assert _contact(db.session.get(Survey, 1)) == ("studie@uni.de", "dsb@uni.de")
