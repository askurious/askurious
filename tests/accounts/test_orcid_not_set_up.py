"""/auth/login without ORCID access (UI audit 2026-10-12, U58): a page that says why and leads to the sign-in with
e-mail and password, which keeps working, instead of a bare 503."""


def test_login_without_orcid_explains_and_offers_the_e_mail_way(app):
    app.config["ORCID_CLIENT_ID"] = ""
    c = app.test_client()
    r = c.get("/auth/login?next=/surveys/")
    page = r.get_data(as_text=True)
    assert r.status_code == 503
    assert "Anmeldung mit ORCID ist hier nicht eingerichtet" in page and ">Anmelden</a>" in page
    assert "/auth/sign-in?next=/surveys/" in page
    assert c.get("/auth/sign-in").status_code == 200



def test_a_refused_page_offers_orcid_first_and_the_page_with_all_ways(app):
    """Ralf 2026-11-27: as in the header, "Mit ORCID anmelden" is the filled button, "Anmelden" beside it leads to
    the page with all three ways; without ORCID only "Anmelden", filled."""
    from flask import render_template

    app.config["ORCID_CLIENT_ID"] = "configured-client"
    with app.test_request_context("/"):
        page = render_template("error.html", kind="forbidden", survey_id=None, signed_in=False)
    actions = page.split('class="form-actions"')[1]
    assert 'class="button" href="/auth/login"' in actions and "Mit ORCID anmelden" in actions
    assert 'class="button button-secondary" href="/auth/sign-in"' in actions
    assert actions.index("Mit ORCID anmelden") < actions.index(">Anmelden</a>")
    app.config["ORCID_CLIENT_ID"] = ""
    with app.test_request_context("/"):
        actions = render_template("error.html", kind="forbidden", survey_id=None, signed_in=False).split('class="form-actions"')[1]
    assert "Mit ORCID anmelden" not in actions and 'class="button" href="/auth/sign-in"' in actions
