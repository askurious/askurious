"""The sound of the video conference recorded in the browser: concept konzept-online-interviews-2026-10-15.md, part B
(Ralf 2026-10-15, 1c); for everyone since 2026-10-16. Only with the consent to the recording; then the page may share
a screen (Permissions-Policy display-capture), else not."""
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def _view(client, code, rid):
    return client.get(f"/guide/{code}/conversation?interview={rid}")


def test_the_tick_and_the_screen_for_everyone(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    r = _view(client, code, rid)
    assert "data-iv-conference" in r.get_data(as_text=True)
    assert "Ton der Videokonferenz mit aufnehmen" in r.get_data(as_text=True)
    assert "display-capture=(self)" in r.headers["Permissions-Policy"]
    no = start(app, client, code, audio="")                                        # no consent to a recording: nothing
    r = _view(client, code, no)
    assert "data-iv-conference" not in r.get_data(as_text=True) and "display-capture=()" in r.headers["Permissions-Policy"]
    assert "display-capture=()" in client.get(f"/guide/{code}/interviews").headers["Permissions-Policy"]
