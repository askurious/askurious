"""Seal chain, second stage of siegelkette-konzept.md (Ralf 2026-10-10 "wie empfohlen"): the researchers name the
reason when they delete (E4, E5), new entries are stamped within about ten minutes (E7), and the check code of the
close of the fieldwork goes to the researchers by mail (E8)."""
from app import seal
from app.extensions import db
from app.models import Response, Survey
from tests.privacy.test_erasure import CODE, take
from tests.privacy.test_platform_erase import mails
from tests.exports.test_seal import chain, sealed_survey, tsa  # noqa: F401


def real_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, source="real"))


def test_the_researchers_delete_with_a_reason_that_the_chain_keeps(app, client, survey, tsa):  # noqa: F811
    uid = sealed_survey(app, client)
    codes = [CODE.search(take(app.test_client(), uid, a)).group(1) for a in ("a", "b", "c")]
    page = client.post("/surveys/1/erasure", data={"term": codes[0]}).data.decode()
    assert "Grund der Löschung" in page and "Qualitätssicherung" in page and 'value="wish" checked' in page
    client.post("/surveys/1/erasure", data={"term": codes[0], "confirm": "1", "why": "consent"})
    assert chain(app)[-1][1:] == ("deleted", 2, "completed", "request_consent")
    short = client.post("/surveys/1/erasure", data={"term": codes[1], "confirm": "1", "why": "other",
                                                     "why_text": "kurz"}).data.decode()
    assert "braucht 10 bis 190 Zeichen" in short and real_count(app) == 2          # nothing deleted without it
    client.post("/surveys/1/erasure", data={"term": codes[1], "confirm": "1", "why": "other",
                                            "why_text": "Anordnung | der Aufsichtsbehörde"})
    assert chain(app)[-1][1:] == ("deleted", 3, "completed", "request: Anordnung / der Aufsichtsbehörde")
    client.post("/surveys/1/erasure", data={"term": codes[2], "confirm": "1"})     # without a pick: the request
    assert chain(app)[-1][-1] == "erasure_request"
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert seal.check(s)["problems"] == []
        seal.anchor(s.uid)
        code = seal.summary(s)["cite"]
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert "keine wirksame Einwilligung" in page and "aus anderem Rechtsgrund" in page and "auf Wunsch der Person" in page
    assert "Fall 3, durch die Forschenden aus anderem Rechtsgrund: Anordnung / der Aufsichtsbehörde" in page


def test_request_reason():
    assert seal.request_reason("wish") == "erasure_request"
    assert seal.request_reason("consent") == seal.CONSENT
    assert seal.request_reason("other", "  zu\nkurz ") is None
    assert seal.request_reason("other", "x" * 191) is None
    assert seal.request_reason("other", "Gerichtliche Anordnung") == "request: Gerichtliche Anordnung"
    assert seal.request_reason("anything") is None
    assert [seal.way(r) for r in ("erasure_request", "request_consent", "request: X")] == ["request", "consent", "legal"]


def test_the_check_code_of_the_close_goes_to_the_researchers(app, client, survey, tsa, monkeypatch):  # noqa: F811
    sent = mails(app, monkeypatch)
    monkeypatch.setattr(seal, "anchor_in_background", lambda: None)
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():
        seal.anchor(db.session.get(Survey, 1).uid)
    assert sent == []                                                             # no close yet: no mail
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        seal.anchor(s.uid)
        code = seal.summary(s)["cite"]
        assert seal.summary(s)["cite_closed"]
    assert len(sent) == 1 and code in sent[0].get_content() and "/verify/" + code in sent[0].get_content()
    assert "Prüfcode der Siegelkette" in sent[0]["Subject"]
    page = client.get("/surveys/1/data").data.decode()
    assert "Stand beim Feldende" in page


def test_new_entries_are_stamped_within_minutes(app, monkeypatch):
    asked = []
    monkeypatch.setattr(seal, "anchor_in_background", lambda: asked.append(1))
    monkeypatch.setattr(seal, "due", lambda: ["x"])
    monkeypatch.setattr(seal, "_last_soon", None)
    app.config["TSA_URL"] = "http://tsa.test"
    with app.app_context():
        app.testing = False
        try:
            seal.stamp_soon()
            seal.stamp_soon()                                                     # not again within STAMP_SOON
            monkeypatch.setattr(seal, "_last_soon", seal._last_soon - seal.STAMP_SOON - 1)
            seal.stamp_soon()
        finally:
            app.testing = True
    assert asked == [1, 1]
