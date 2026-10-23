"""Own address for participants (app/hosts.py): one address for all, two names in one process, one process per side."""
from urllib.parse import parse_qs, urlsplit

from app.extensions import db
from app.models import Response, Survey
from tests.participation.test_participate import build, consent_form, uid

ASK = "http://ask.localhost"  # a development name: the tests keep the development key


def _split(app, **more):
    app.config.update(PARTICIPANT_BASE_URL=ASK, **more)


def test_every_route_has_a_side(app):
    from app import hosts

    missing = sorted({r.endpoint for r in app.url_map.iter_rules() if hosts.side_of(r.endpoint) is None})
    assert not missing, f"enter these blueprints in app/hosts.py BLUEPRINT_SIDES: {missing}"


def test_one_address_answers_everything_without_settings(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    assert f"http://localhost:5060/s/{code}" in client.get("/surveys/1/field").data.decode()
    assert app.test_client().get(f"/s/{code}").status_code == 200
    assert client.get("/dashboard/").status_code == 200


def test_two_names_in_one_process(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    _split(app)
    assert f"{ASK}/s/{code}" in client.get("/surveys/1/field").data.decode()   # links, QR codes lead there
    p = app.test_client()
    r = p.get(f"/s/{code}?gruppe=a")                         # an old link on the main address moves (E4)
    assert r.status_code == 301 and r.headers["Location"] == f"{ASK}/s/{code}?gruppe=a"
    assert p.post(f"/s/{code}").status_code == 308
    assert p.get(f"{ASK}/s/{code}").status_code == 200
    assert p.get(f"{ASK}/dashboard/").status_code == 404     # researchers' pages do not exist there
    assert p.get(f"{ASK}/admin/").status_code == 404
    r = p.get(f"{ASK}/imprint")                             # the platform's legal page lives on the main address
    assert r.status_code == 301 and r.headers["Location"] == "http://localhost:5060/imprint"
    assert p.get(f"{ASK}/robots.txt").status_code == 200
    html = p.get(f"{ASK}/s/{code}/privacy").data.decode()
    assert 'href="http://localhost:5060/imprint"' in html  # absolute: this address does not answer it


def test_one_process_per_side(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    app.config.update(SIDE="participant")                     # whatever the name, only participants' paths
    assert client.get("/dashboard/").status_code == 404
    assert client.get(f"/s/{code}").status_code == 200
    _split(app, SIDE="researcher")                            # even under the participants' name: the main side
    r = client.get(f"{ASK}/s/{code}")
    assert r.status_code == 301 and r.headers["Location"] == f"{ASK}/s/{code}"
    assert client.get(f"{ASK}/dashboard/").status_code == 302  # to the sign-in: its cookie belongs to the other name


def test_participation_under_way_goes_along(app, client, survey):
    app.config["PARTICIPANT_HANDOVER_UNTIL"] = "2999-01-01"
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    p = app.test_client()
    p.get(f"/s/{code}")
    p.post(f"/s/{code}", data=consent_form())                # started on the one address of before
    _split(app)
    r = p.get(f"/s/{code}")
    token = parse_qs(urlsplit(r.headers["Location"]).query)["uebergabe"][0]
    assert r.status_code == 302                               # not kept by the browser, unlike the plain move
    r = p.get(r.headers["Location"])                          # the new address takes it and cleans the address
    assert r.status_code == 303 and r.headers["Location"] == f"/s/{code}"
    p.post(f"{ASK}/s/{code}", data={"nav": "next", "i2_s1": "2", "i2_s2": "2", "i3": "30"})
    p.post(f"{ASK}/s/{code}", data={"nav": "next"})
    assert "Vielen Dank" in p.get(f"{ASK}/s/{code}").data.decode()
    with app.app_context():
        assert [x.status for x in db.session.scalars(db.select(Response))] == ["completed"]
    # the keys left the main address; a stranger with a forged token gets nothing
    assert "uebergabe" not in p.get(f"/s/{code}").headers["Location"]
    q = app.test_client()
    q.get(f"{ASK}/s/{code}?uebergabe={token[:-2]}xx")
    assert "Vielen Dank" not in q.get(f"{ASK}/s/{code}").data.decode()


def test_no_handover_after_its_end(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    p = app.test_client()
    p.get(f"/s/{code}")
    p.post(f"/s/{code}", data=consent_form())
    _split(app, PARTICIPANT_HANDOVER_UNTIL="2000-01-01")
    r = p.get(f"/s/{code}")
    assert r.status_code == 301 and r.headers["Location"] == f"{ASK}/s/{code}"


def test_researchers_see_their_draft_only_through_the_redirect(app, client, survey):
    code = uid(app)
    _split(app)
    r = client.get(f"/s/{code}/privacy")                  # signed in on the main address (E6)
    assert r.status_code == 302 and "uebergabe=" in r.headers["Location"]
    r = client.get(r.headers["Location"])
    assert r.status_code == 303
    assert "derzeit nicht geöffnet" not in client.get(f"{ASK}/s/{code}/privacy").data.decode()
    stranger = app.test_client()
    r = stranger.get(f"/s/{code}/privacy")                # nothing to take along
    assert r.headers["Location"] == f"{ASK}/s/{code}/privacy"
    assert "derzeit nicht geöffnet" in stranger.get(f"{ASK}/s/{code}/privacy").data.decode()
    app.config.update(SIDE="participant", PARTICIPANT_BASE_URL="")  # the participants' side reads no sign-in
    assert "derzeit nicht geöffnet" in app.test_client().get(f"/s/{code}/privacy").data.decode()
    signed_in = app.test_client()
    app.config.update(SIDE="")
    signed_in.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    app.config.update(SIDE="participant")
    assert "derzeit nicht geöffnet" in signed_in.get(f"/s/{code}/privacy").data.decode()


def test_researchers_session_ends_with_the_browser(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.get(f"/s/{uid(app)}")
    client.post(f"/s/{uid(app)}", data=consent_form())       # took part on the one address: a week
    with client.session_transaction() as s:
        assert s.permanent
    _split(app)
    r = client.get("/dashboard/")
    assert r.status_code == 200 and "Expires=" not in r.headers.get("Set-Cookie", "")
    with client.session_transaction() as s:
        assert not s.permanent


def test_unknown_side_stops_the_start(app):
    import pytest

    from app import create_app

    bad = type("Bad", (object,), {**dict(app.config), "SIDE": "both"})
    with pytest.raises(RuntimeError, match="SIDE"):
        create_app(bad)

