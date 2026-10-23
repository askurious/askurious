"""Panel returns (Ralf, 2026-10-08, Maximiles): the completion link "Duplikat" for someone who took part already, and
"Links vom Panel einfügen": a pasted list of return addresses matched to the outcomes, with a preview first."""
from app import panel
from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK
from tests.participation.test_end_links import build, links
from tests.participation.test_panel_once import once_survey

MAXIMILES = """Complete link [Target 1]: https://survey.maximiles.com/static-complete?p=
Quotas_full link [quotas_full]: https://survey.maximiles.com/static-quotasfull?p=
Screen_out link [screen_out]: https://survey.maximiles.com/static-screenout?p=
Quality link: https://survey.maximiles.com/static-quality?p=
Speeder link: https://survey.maximiles.com/static-speeder?p=
Duplicate link: https://survey.maximiles.com/static-duplicate?p=
Geoip link: https://survey.maximiles.com/static-geoip?p="""


def test_a_second_visit_with_the_same_id_goes_to_the_link_duplicate(app, client, survey):
    uid = once_survey(app, client, redirects={"duplicate": "https://panel.example/dup?p={{PID}}"})
    first = app.test_client()
    first.get(f"/s/{uid}?PID=p7")
    first.post(f"/s/{uid}", data=CONSENT_OK)
    first.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    first.post(f"/s/{uid}", data={"nav": "next"})                                            # completed
    again = app.test_client().get(f"/s/{uid}?PID=p7")
    assert again.status_code == 302 and again.headers["Location"] == "https://panel.example/dup?p=p7"
    assert app.test_client().get(f"/s/{uid}?PID=p8").status_code == 200                      # another ID takes part


def test_without_a_link_duplicate_the_page_stays(app, client, survey):
    uid = once_survey(app, client)
    first = app.test_client()
    first.get(f"/s/{uid}?PID=p7")
    first.post(f"/s/{uid}", data=CONSENT_OK)
    first.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    first.post(f"/s/{uid}", data={"nav": "next"})
    page = app.test_client().get(f"/s/{uid}?PID=p7")
    assert page.status_code == 200 and "bereits teilgenommen" in page.data.decode()


def test_the_list_of_a_panel_is_read_line_by_line():
    rows = panel.parse_panel_list(MAXIMILES + "\nOther: https://x.example/end?id=[ID]&s=1\nno address here")
    assert [(r["kind"], r["param"]) for r in rows] == [
        ("completed", "p"), ("quotafull", "p"), ("screenout", "p"), ("excluded", "p"), ("speeder", "p"),
        ("duplicate", "p"), ("geo", "p"), (None, "id")]
    assert rows[0]["url"] == "https://survey.maximiles.com/static-complete?p={{p}}"
    assert rows[-1]["url"] == "https://x.example/end?id={{id}}&s=1"


def test_paste_shows_a_preview_and_takes_over_on_request(app, client, survey):
    build(app, client)
    r = client.post("/surveys/1/panel/paste", data={"action": "preview", "text": MAXIMILES})
    assert r.headers["Location"].endswith("#panel-paste")
    with app.app_context():
        assert not panel.config(db.session.get(Survey, 1))["redirects"].get("completed")   # nothing yet
    html = client.get("/surveys/1/field").data.decode()
    part = html.split('id="panel-paste"')[1]
    assert "Abschlusslink „Duplikat“" in part and "Seiten zu schnell abgeschickt" in part and "Land aus der IP-Adresse" in part
    assert "speichern, jeder Wert nur einmal" in part and 'value="apply"' in part
    client.post("/surveys/1/panel/paste", data={"action": "apply"})
    with app.app_context():
        cfg = panel.config(db.session.get(Survey, 1))
    assert cfg["redirects"]["completed"] == "https://survey.maximiles.com/static-complete?p={{p}}"
    assert cfg["redirects"]["duplicate"].endswith("static-duplicate?p={{p}}")
    assert cfg["redirects"]["excluded"].endswith("static-quality?p={{p}}")
    assert set(o for o, url in cfg["redirects"].items() if url) == {"completed", "quotafull", "screenout", "excluded",
                                                                    "duplicate"}
    assert [(p["name"], p["mode"], p.get("once")) for p in cfg["params"]] == [("p", "store", True)]
    assert 'id="panel-paste"' in client.get("/surveys/1/field").data.decode()
    assert 'value="apply"' not in client.get("/surveys/1/field").data.decode()          # the preview is gone


def test_paste_keeps_an_existing_parameter_and_can_be_discarded(app, client, survey):
    build(app, client)
    links(client, params=(("p", "redirect"),))
    client.post("/surveys/1/panel/paste", data={"action": "preview", "text": MAXIMILES})
    client.post("/surveys/1/panel/paste", data={"action": "cancel"})
    assert 'value="apply"' not in client.get("/surveys/1/field").data.decode()
    client.post("/surveys/1/panel/paste", data={"action": "preview", "text": MAXIMILES})
    client.post("/surveys/1/panel/paste", data={"action": "apply"})
    with app.app_context():
        cfg = panel.config(db.session.get(Survey, 1))
    assert [(p["name"], p["mode"]) for p in cfg["params"]] == [("p", "redirect")]
    r = client.post("/surveys/1/panel/paste", data={"action": "preview", "text": "nichts"}, follow_redirects=True)
    assert "keine Adresse mit https://" in r.data.decode()


def test_pasted_list_waits_on_the_server_not_in_the_cookie(app, client, survey):
    """Code review 2026-10-15, I9: up to 20 000 pasted characters made the cookie session pass 4 KB, and the browser
    dropped it; the list now waits in a file per person and survey."""
    build(app, client)
    client.post("/surveys/1/panel/paste", data={"action": "preview", "text": MAXIMILES})
    with client.session_transaction() as s:
        assert not any(k.startswith("panel_paste:") for k in s)
    assert 'value="apply"' in client.get("/surveys/1/field").data.decode()           # the preview from the file
