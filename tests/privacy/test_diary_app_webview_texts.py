"""The legal texts name what the Android app's WebView asks of Google (audit 2026-10-18, AND-10): the app itself sends
only to the platform, but the questionnaires run in Android System WebView, which queries Safe Browsing and, with an
autofill service on, autofill predictions (content-autofill.googleapis.com); its usage statistics are switched off
(askurious-android README, "The WebView and Google")."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_privacy_page_names_the_webview_and_its_google_requests(app, client):
    page = client.get("/imprint").data.decode()
    assert "Android System WebView" in page and "Safe Browsing" in page
    assert "content-autofill.googleapis.com" in page and "Nutzungsstatistik der WebView an Google abgeschaltet" in page
    assert "nicht an Google oder andere Dienste" not in page  # the former claim
    client.post("/account/language", data={"lang": "en"})
    page = client.get("/imprint").data.decode()
    assert "Android System WebView" in page and "content-autofill.googleapis.com" in page
    assert "switched off the WebView’s usage statistics" in page and "not to Google or other services" not in page
    for name in ("privacy.html", "privacy_en.html"):
        assert "not to Google or other services" not in _read("app", "templates", "public", name), name


def test_terms_and_privacy_concept_name_the_webview(app):
    from app import privacy_concept, terms

    assert "Android System WebView" in _read("licence.md") and "Android System WebView" in _read(
        "app", "templates", "terms_en.html")
    # the entry of the version that brought it (later versions come before it in CHANGES)
    assert next(c for c in terms.CHANGES if "WebView" in c[1])[3] == (6,)
    with app.test_request_context():
        row = next(r for r in privacy_concept.services() if "Google Play" in r["words"])
        assert "WebView" in row["name"] and "content-autofill.googleapis.com" in row["data"]
