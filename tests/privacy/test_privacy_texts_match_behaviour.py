"""The privacy texts say what the platform does (data protection audit 2026-11-11): "not passed on to third parties"
only where nothing goes to an AI either (KI-Chat, handwriting), stopping a survey keeps what was sent, transcripts go
to an AI only for coding suggestions, the security log's daily files and their period, ORCID and the app store among
the services outside the EU, a provider of an own AI access possibly outside the EU, and complaints without a set
period."""
from app.items import consent


def test_not_passed_on_only_without_ai_chat_or_handwriting():
    assert not consent._passed_on({})
    assert consent._passed_on({"ai_chat": "participants chat with an AI assistant"})
    assert consent._passed_on({"ai_handwriting": "handwritten answers may be sent"})
    assert consent.use_sentence("de", passed_on=True) == consent.USE[0]
    assert "nicht an Dritte" not in consent.use_sentence("de", passed_on=consent._passed_on({"ai_chat": "x"}))


def test_stopping_keeps_what_was_sent_and_drafts_get_the_new_sentence():
    de, en = consent.WITHDRAWAL
    assert "bleibt gespeichert und kann ausgewertet werden" in de and "stays stored and may be analysed" in en
    assert "indem Sie die Befragung abbrechen" not in de
    for lang in ("fr", "es", "de-du", "zh-Hans"):            # every language of the catalog has it
        assert consent.tr(lang, de, en) != en
    assert consent.tr("de-du", *consent.WITHDRAWAL).startswith("Du kannst")


def test_the_third_country_sentence_is_in_every_language():
    de, en = consent.THIRD_COUNTRY
    for lang in ("fr", "ar", "tr"):
        assert consent.tr(lang, de, en) != en


def test_the_public_privacy_page_says_what_happens(app, client):
    page = client.get("/imprint").data.decode()
    assert "an OpenAI oder andere KI-Anbieter gehen Aufnahmen, Notizen und Transkripte nicht" not in page
    assert "Abschnitte geprüfter Transkripte gehen nur an einen KI-Dienst" in page
    assert "Abschnitte des pseudonymisierten Transkripts" not in page and "sobald es als geprüft übergeben ist" in page
    assert f"die nach {app.config['SECURITY_LOG_DAYS']} Tagen gelöscht werden" in page
    assert "ORCID für die Anmeldung" in page and "Google Play für die Tagebuch-App (USA)" in page
    assert "Nutzen Forschende einen eigenen KI-Zugang" in page
    assert "eine feste Frist, nach der sie von selbst gelöscht werden, gibt es bisher nicht" in page
    assert "Muss nach einem Ausfall eine Sicherung zurückgespielt werden" in page
    assert "steht in der Kette außerdem je gelöschter Teilnahme ein Vermerk" in page
    assert "Dasselbe Zugriffsprotokoll hält auch fest" in page


def test_the_english_privacy_page_says_the_same(app, client):
    client.post("/account/language", data={"lang": "en"})
    page = client.get("/imprint").data.decode()
    assert "<h1>Legal notice and privacy</h1>" in page
    assert "recordings and notes go to no AI provider" in page
    assert "once it has been handed over as checked" in page
    assert f"deleted after {app.config['SECURITY_LOG_DAYS']} days" in page
    assert "so far there is no set period after which they are deleted by themselves" in page
