"""StaleDataError (another request saved the same participation meanwhile) on the researchers' pages (audit
2026-10-21, B7): the change is dropped as before, but the researcher learns it, instead of a silent redirect or a
bare {"error": "stale"}. The participants' side keeps dropping a page sent twice without a word."""
from sqlalchemy.orm.exc import StaleDataError

MESSAGE = "Ihre Änderung wurde nicht gespeichert"


def _raise(**_kw):
    raise StaleDataError("UPDATE statement on table 'response' expected to update 1 row(s); 0 were matched.")


def test_a_script_on_a_researcher_page_gets_409_with_the_message(app, client, survey):
    app.view_functions["coding.assign_route"] = _raise
    r = client.post("/surveys/1/categorise/x/assign", data={"norm": "a"}, headers={"Accept": "application/json"})
    assert r.status_code == 409
    assert r.json["status"] == "stale" and r.json["error"] == "stale" and MESSAGE in r.json["message"]


def test_a_form_on_a_researcher_page_goes_back_with_a_message(app, client, survey):
    app.view_functions["coding.assign_route"] = _raise
    r = client.post("/surveys/1/categorise/x/assign", data={"norm": "a"},
                    headers={"Referer": "http://localhost/surveys/1/settings?x=1"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/surveys/1/settings?x=1")
    page = client.get("/dashboard/").get_data(as_text=True)
    assert MESSAGE in page
    # a referrer of another host is never followed
    r = client.post("/surveys/1/categorise/x/assign", data={"norm": "a"}, headers={"Referer": "https://evil.example/x"})
    assert r.status_code == 303 and "evil" not in r.headers["Location"]
    r = client.post("/surveys/1/categorise/x/assign", data={"norm": "a"},
                    headers={"Referer": "http://localhost//evil.example/x"})
    assert r.status_code == 303 and "evil" not in r.headers["Location"]


def test_the_participants_side_stays_silent(app, client, survey):
    from app import _participant_side

    with app.test_request_context("/s/1", method="POST"):
        assert _participant_side()
    with app.test_request_context("/surveys/1/editor/preview", method="POST"):
        assert _participant_side()
    with app.test_request_context("/surveys/1/categorise/x/assign", method="POST"):
        assert not _participant_side()
    app.view_functions["participate.page"] = _raise
    r = client.post("/s/1", data={"nav": "next"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/s/1")
    assert MESSAGE not in client.get("/dashboard/").get_data(as_text=True)
