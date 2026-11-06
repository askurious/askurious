"""An interviewer link stays signed in on reloading (feedback 2026-11-06): its cookie keeps the week it got when the
link was opened, also on the researchers' own address where every other session ends with the browser (hosts E7);
and when its session has ended, the pages of the guide say "open the link again" instead of 404 or the sign-in of the
researchers."""
from datetime import timedelta
from unittest import mock

from app import interviewer, models
from tests.interview.test_interviewer import add, open_link
from tests.interview.test_transcript_coding import _guide, _new


def _session_cookie(r):
    return next(h for k, h in r.headers.items() if k.lower() == "set-cookie" and "session=" in h)


def _split(app):
    app.config.update(PARTICIPANT_BASE_URL="https://ask.example.org", PUBLIC_BASE_URL="https://example.org")


def test_the_link_keeps_its_cookie_on_the_researchers_address(app, client):
    _split(app)
    code, sid = _guide(app, client)
    key = add(client, code, "Anna Becker")
    c, r = open_link(app, code, key)
    assert "Expires=" in _session_cookie(r)
    r = c.get(f"/i/{code}/interviews")
    assert r.status_code == 200
    assert "Expires=" in _session_cookie(r)                                        # not turned into a browser cookie
    assert c.get(f"/i/{code}/interviews").status_code == 200
    # a researcher's own session still ends with the browser (E7), also with a link of their own in the same browser
    open_link(app, code, key, client=client)
    r = client.get("/")
    assert "Expires=" not in _session_cookie(r)


def test_an_ended_link_asks_to_open_it_again(app, client):
    code, sid = _guide(app, client)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    key = add(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all")
    c, _ = open_link(app, code, key)
    assert c.get(base).status_code == 200
    later = models.utcnow() + timedelta(minutes=interviewer.IDLE + 1)
    with mock.patch.object(interviewer, "utcnow", return_value=later):
        r = c.get(base)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/i/{code}")   # not 404, not the sign-in
    assert "erneut öffnen" in c.get(f"/i/{code}").data.decode()
    r = c.get(base)                                                                 # reloading once more: the same
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/i/{code}")
    r = c.get(f"{base}/changes", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 401 and r.json["error"] == "link"                      # the page reloads itself
    # opened again: everything as before
    open_link(app, code, key, client=c)
    assert c.get(base).status_code == 200
    # a browser that never held a link still gets the sign-in
    stranger = app.test_client()
    assert "/auth/sign-in" in stranger.get(base).headers["Location"]
