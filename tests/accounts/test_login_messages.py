"""Flask-Login's redirect banner uses the same language as the sign-in page."""
import pytest


@pytest.mark.parametrize("language, expected", [
    ("de", "Bitte melden Sie sich an, um diese Seite aufzurufen."),
    ("en", "Please sign in to open this page."),  # "sign in" everywhere (UI/UX audit 2026-10-16, 57)
])
@pytest.mark.parametrize("picked", [False, True])
def test_login_banner_language(app, language, expected, picked):
    guest = app.test_client()
    browser_language = language
    if picked:
        browser_language = "en" if language == "de" else "de"
        with guest.session_transaction() as session:
            session["ui_lang"] = language

    response = guest.get("/account", headers={"Accept-Language": browser_language},
                         follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/auth/sign-in"
    page = response.get_data(as_text=True)
    assert f'<html lang="{language}"' in page
    assert expected in page
    if language == "de":
        assert "Please sign in to open this page." not in page
