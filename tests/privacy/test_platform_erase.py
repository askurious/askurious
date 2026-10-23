"""Deletion by the platform (app/platform_erase.py, admin.platform_erase; Philipp, 2026-10-10): the admins delete
participations or a whole survey with a reason in a few words, which the seal chain keeps in its hash; the
researchers are told by e-mail afterwards; the access log notes it."""
from app import mail, seal, seal_verify
from app.extensions import db
from app.models import AccessLog, Response, Survey, User, UserLogin
from tests.privacy.test_erasure import CODE, take
from tests.exports.test_seal import chain, sealed_survey

REASON = "Verstoß gegen den Datenschutz | Anordnung der Behörde"


def page(client):
    return client.get("/admin/surveys/" + code(client.application) + "/erase").data.decode()


def code(app):
    with app.app_context():
        return db.session.get(Survey, 1).code


def mails(app, monkeypatch):
    """Mail switched on, with the researcher's sign-in address; the mails sent, collected."""
    sent = []
    app.config.update(MAIL_HOST="mail.test", MAIL_FROM="noreply@test", MAIL_SYNC=True)
    monkeypatch.setattr(mail, "deliver", lambda msg, app=None: sent.append(msg))
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        row = db.session.get(UserLogin, user.id) or UserLogin(user_id=user.id, status="active")
        row.email = "forscherin@example.org"
        db.session.add(row)
        db.session.commit()
    return sent


def test_cases_deleted_by_the_platform_carry_the_reason_in_the_hash(app, client, survey, monkeypatch):
    sent = mails(app, monkeypatch)
    uid = sealed_survey(app, client)
    first = take(app.test_client(), uid, "eins")
    take(app.test_client(), uid, "zwei")
    sc = code(app)
    deletion_code = CODE.search(first).group(1)
    # first what was found, nothing deleted yet
    shown = client.post(f"/admin/surveys/{sc}/erase", data={"action": "cases", "terms": f"#3\n{deletion_code}\nquatsch",
                                                            "reason": REASON}).data.decode()
    assert "Gefunden: 2 Teilnahmen" in shown and "quatsch" in shown
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, source="real")) == 2
    # a reason too short is refused
    client.post(f"/admin/surveys/{sc}/erase", data={"action": "cases", "terms": "#3", "reason": "kurz", "confirm": "1"})
    assert len(chain(app)) == 3
    client.post(f"/admin/surveys/{sc}/erase", data={"action": "cases", "terms": f"#3\n{deletion_code}",
                                                   "reason": REASON, "confirm": "2"})
    reason = "platform: Verstoß gegen den Datenschutz / Anordnung der Behörde"   # one line, no "|"
    assert chain(app)[-2:] == [(4, "deleted", 2, "completed", reason), (5, "deleted", 3, "completed", reason)]
    with app.app_context():
        s = db.session.get(Survey, 1)
        result = seal.check(s)
        assert result["problems"] == [] and result["platform"] == [(2, reason[10:]), (3, reason[10:])]
        assert seal_verify.check_chain(seal.chain(s))["problems"] == []
        log = db.session.scalars(db.select(AccessLog).filter_by(action="Löschung durch die Plattform")).all()
        assert len(log) == 1 and "Grund: Verstoß" in log[0].detail and log[0].survey_code == sc
    assert len(sent) == 1 and "forscherin@example.org" == sent[0]["To"]
    assert "2 Teilnahmen" in sent[0].get_content() and "Grund: Verstoß" in sent[0].get_content()
    checked = client.post(f"/surveys/{sc}/export/seal-chain/check", headers={"X-Requested-With": "fetch"}).data.decode()
    assert "Fall 2, durch die Plattform: Verstoß gegen den Datenschutz" in checked
    # the reason cannot be changed afterwards: the chain no longer holds
    with app.app_context():
        entry = seal.entries(db.session.get(Survey, 1).uid)[-1]
        db.session.execute(seal.TABLE.update().where(seal.TABLE.c.id == entry.id).values(reason="platform: anders"))
        db.session.commit()
        assert seal.check(db.session.get(Survey, 1))["problems"]
    # the admin page lists it; "Check now" names it for the researchers
    assert "Bisher durch die Plattform gelöscht" in page(client)


def test_a_survey_taken_down_ends_its_chain_with_the_reason(app, client, survey, monkeypatch):
    sent = mails(app, monkeypatch)
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    sc = code(app)
    with app.app_context():
        survey_uid = db.session.get(Survey, 1).uid
    client.post(f"/admin/surveys/{sc}/erase", data={"action": "survey", "reason": REASON, "code": "falsch"})
    with app.app_context():
        assert db.session.get(Survey, 1) is not None                 # the code did not match
    client.post(f"/admin/surveys/{sc}/erase", data={"action": "survey", "reason": REASON, "code": sc})
    with app.app_context():
        assert db.session.get(Survey, 1) is None
        last = seal.entries(survey_uid)[-1]
        assert (last.kind, last.reason) == ("closed", "platform: Verstoß gegen den Datenschutz / Anordnung der Behörde")
        assert seal.by_platform([dict(e._mapping) for e in seal.entries(survey_uid)]) == [
            (None, "Verstoß gegen den Datenschutz / Anordnung der Behörde")]
    assert len(sent) == 1 and "von der Plattform gelöscht" in sent[0]["Subject"]


def test_without_mail_the_admin_is_told_whom_to_inform(app, client, survey):
    sealed_survey(app, client)
    take(app.test_client(), code(app), "eins")
    sc = code(app)
    done = client.post(f"/admin/surveys/{sc}/erase", data={"action": "cases", "terms": "#2", "reason": REASON,
                                                          "confirm": "1"}, follow_redirects=True).data.decode()
    assert "Bitte auf anderem Weg informieren" in done


def test_only_platform_admins_reach_the_page(app, client, survey):
    sealed_survey(app, client)
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Andere")
    assert other.get(f"/admin/surveys/{code(app)}/erase").status_code in (302, 403)   # terms first, then 403
    listing = client.get("/admin/surveys").data.decode()
    # the bin at the end of the row, no longer a link "Löschen …" beside the title (Ralf, 2026-10-17)
    assert f'href="/admin/surveys/{code(app)}/erase" title="Löschen …"' in listing and ">Löschen …<" not in listing


def test_the_code_form_leads_to_the_survey_or_says_there_is_none(app, client, survey):
    """admin.platform_erase_find (code review 2026-10-15, L8): blanks and capitals in the code do not matter, an
    unknown code is named, a researcher who is no platform admin gets nowhere."""
    sc = code(app)
    r = client.get("/admin/platform-erase", query_string={"code": f" {sc[:3]} {sc[3:].upper()} "})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/admin/surveys/{sc}/erase")
    r = client.get("/admin/platform-erase", query_string={"code": "zzzzzzzz"}, follow_redirects=True)
    assert "Keine Befragung mit diesem Code." in r.data.decode()
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Andere")
    assert other.get("/admin/platform-erase", query_string={"code": sc}).status_code in (302, 403)
    r = other.get("/admin/platform-erase", query_string={"code": sc})
    assert "/erase" not in r.headers.get("Location", "")
