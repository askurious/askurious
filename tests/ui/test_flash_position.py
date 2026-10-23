"""Messages (flash) stand under the second row of tabs, not between the header and the tabs (Ralf, 2026-10-06)."""


def _flash(client, text):
    with client.session_transaction() as s:
        s["_flashes"] = [("success", text)]


def test_messages_stand_under_the_survey_tabs(client, survey):
    _flash(client, "Sprache entfernt: Test.")
    html = client.get("/surveys/1/design").data.decode()
    assert html.index("survey-tabs") < html.index('class="flashes"') < html.index("Sprache entfernt: Test.")
    assert "<!--flashes-->" not in html


def test_messages_stand_under_the_admin_tabs(client):
    _flash(client, "Gespeichert: Test.")
    html = client.get("/admin/people").data.decode()
    assert html.index('class="admin-tabs"') < html.index('class="flashes"')


def test_messages_stay_on_top_without_tabs(client):
    _flash(client, "Hallo.")
    html = client.get("/dashboard/").data.decode()
    assert html.index('<main id="main"') < html.index('class="flashes"') and "Hallo." in html
