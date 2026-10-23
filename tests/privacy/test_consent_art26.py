"""Researchers of several institutions are joint controllers (Art. 26 GDPR; comparison with the RWTH template
2026-10-20, G): one line in the settings, all institutions and one point of contact in the consent, the essence of
the arrangement on the privacy page."""
from app.extensions import db
from app.models import Survey, SurveyMember


def _second_member(app, affiliation):
    from app.items.consent import member_ref

    with app.app_context():
        member = SurveyMember(survey_id=1, orcid="0000-0002-1825-0097", name="Vera", role="editor", position=1,
                              affiliation=affiliation)
        db.session.add(member)
        db.session.commit()
        return member_ref(member)


def _facts(client, me_aff, other_ref, other_aff, **extra):
    from app.items.consent import member_ref

    with client.application.app_context():
        members = db.session.get(Survey, 1).members
        refs = [member_ref(m) for m in members]
        data = {"min_age": "", "researchers": refs,
                **{f"aff_{m.id}": (other_aff if member_ref(m) == other_ref else me_aff) for m in members}, **extra}
    return client.post("/surveys/1/consent-facts", data=data)


def test_one_institution_says_nothing(app, client, survey):
    ref = _second_member(app, "RWTH Aachen")
    _facts(client, "RWTH Aachen", ref, " rwth aachen ", contact_email="studie@uni.de")
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert not item.kind.joint(item)
        assert "Art. 26" not in item.kind.template_body(item, "de")
    assert "Art. 26" not in client.get("/surveys/1").get_data(as_text=True)


def test_several_institutions_name_all_and_one_contact(app, client, survey):
    ref = _second_member(app, "Universität Köln")
    _facts(client, "RWTH Aachen", ref, "Universität Köln", contact_email="studie@uni.de")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = s.consent_item
        assert item.kind.institutions(item) == ["RWTH Aachen", "Universität Köln"]
        de = item.kind.template_body(item, "de")
        assert ("Die beteiligten Einrichtungen (RWTH Aachen, Universität Köln) sind gemeinsam für die Verarbeitung "
                "verantwortlich (Art. 26 DSGVO). Anlaufstelle für alle Anliegen, auch zu Ihren Rechten, ist studie@uni.de.") in de
        assert "point of contact for all matters, including your rights, is studie@uni.de" in item.kind.template_body(item, "en")
        assert "deinen Rechten" in item.kind.template_body(item, "de-du")
        uid = s.public_key
    settings = client.get("/surveys/1").get_data(as_text=True)
    assert "Vereinbarung nach Art. 26 DSGVO" in settings
    assert "Das Wesentliche ihrer Vereinbarung erhalten Sie auf Anfrage" in client.get(f"/s/{uid}/privacy?lang=de").get_data(as_text=True)
    _facts(client, "RWTH Aachen", ref, "Universität Köln", contact_email="")
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert "Ihre Rechte können Sie bei jeder von ihnen geltend machen." in item.kind.template_body(item, "de")
