"""/auth/login without ORCID access (UI audit 2026-10-12, U58): a page that says why and leads to the sign-in with
e-mail and password, which keeps working, instead of a bare 503."""


def test_login_without_orcid_explains_and_offers_the_e_mail_way(app):
    app.config["ORCID_CLIENT_ID"] = ""
    c = app.test_client()
    r = c.get("/auth/login?next=/surveys/")
    page = r.get_data(as_text=True)
    assert r.status_code == 503
    assert "Anmeldung mit ORCID ist hier nicht eingerichtet" in page and "Mit E-Mail anmelden" in page
    assert "/auth/sign-in?next=/surveys/" in page
    assert c.get("/auth/sign-in").status_code == 200

