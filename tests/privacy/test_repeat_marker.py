"""The cookie that marks a browser once a participation has ended (survey setting "repeat_marker")."""
import re

from app.extensions import db
from app.models import Response, Survey
from app.participate import MARKER_DAYS
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"
META = {"title": "Test", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}


def build(app, client, marker=True, multi=False):
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={**META, **({"repeat_marker": "1"} if marker else {}),
                                         "multi_shown": "1", **({"multi": "1"} if multi else {})})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def take(p, uid):
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p.post(f"/s/{uid}", data={"nav": "next", "i2": "darum"}, follow_redirects=True)


def stored(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(Response).filter_by(survey_id=1, is_preview=False))


def forget_session(p):
    """What is left of a browser after the 7 days of its session: the mark, if there is one."""
    p.delete_cookie("session")


def test_the_marked_browser_only_sees_the_note(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    first = p.get(f"/s/{uid}")
    assert "qdone_" not in " ".join(first.headers.getlist("Set-Cookie"))      # opening the link marks nothing
    assert "qdone_" not in " ".join(p.post(f"/s/{uid}", data=CONSENT_OK).headers.getlist("Set-Cookie"))
    end = p.post(f"/s/{uid}", data={"nav": "next", "i2": "darum"})
    while end.status_code in (302, 303):
        end = p.get(end.headers["Location"])
    cookie = [c for c in end.headers.getlist("Set-Cookie") if c.startswith(f"qdone_{uid}=1;")]
    assert cookie and f"Max-Age={MARKER_DAYS * 86400}" in cookie[0] and "HttpOnly" in cookie[0] and "Path=/s/" in cookie[0]
    assert "Vielen Dank für Ihre Teilnahme" in end.data.decode()
    forget_session(p)
    again = p.get(f"/s/{uid}")
    assert again.status_code == 200 and "In diesem Browser wurde bereits teilgenommen" in again.data.decode()
    assert "This browser has already been used" in p.get(f"/s/{uid}?lang=en").data.decode()
    assert p.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True).data.decode().count("bereits teilgenommen") == 1
    assert stored(app) == 1
    # another browser takes part as usual
    assert "Vielen Dank für Ihre Teilnahme" in take(app.test_client(), uid).data.decode()
    assert stored(app) == 2


def test_a_first_page_sent_from_a_marked_browser_is_not_stored(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")                                          # a page waits in this tab
    p.set_cookie(f"qdone_{uid}", "1", path="/s/")               # meanwhile the browser took part elsewhere
    page = p.post(f"/s/{uid}", data=CONSENT_OK)
    assert "In diesem Browser wurde bereits teilgenommen" in page.data.decode() and stored(app) == 0


def test_without_the_setting_nothing_is_marked_or_heeded(app, client, survey):
    uid = build(app, client, marker=False)
    p = app.test_client()
    assert "Vielen Dank" in take(p, uid).data.decode() and p.get_cookie(f"qdone_{uid}", path="/s/") is None
    forget_session(p)
    p.set_cookie(f"qdone_{uid}", "1", path="/s/")               # a mark left from somewhere else is not heeded
    assert "Vielen Dank" in take(p, uid).data.decode() and stored(app) == 2


def test_shared_devices_are_never_marked(app, client, survey):
    uid = build(app, client, multi=True)
    p = app.test_client()
    assert "Neue Teilnahme an diesem Gerät" in take(p, uid).data.decode()
    assert p.get_cookie(f"qdone_{uid}", path="/s/") is None


def test_declining_does_not_mark(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    page = p.post(f"/s/{uid}", data={"i1": "0"}, follow_redirects=True)
    assert "Doch teilnehmen" in page.data.decode() and p.get_cookie(f"qdone_{uid}", path="/s/") is None


def test_setting_consent_privacy_and_copies(app, client, survey):
    from app import design, duplicate, transfer

    fill_consent(client)
    settings = client.get("/surveys/1").data.decode()
    assert 'name="repeat_mode" value="cookie"' in settings and f"{MARKER_DAYS} Tage" in settings
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.repeat_marker and "Cookie" not in s.consent_item.kind.template_body(s.consent_item, "de")
    client.post("/surveys/1/meta", data={**META, "repeat_marker": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert s.repeat_marker
        assert f"für {MARKER_DAYS} Tage in einem Cookie" in consent.kind.template_body(consent, "de")
        assert f"in a cookie for {MARKER_DAYS} days" in consent.kind.template_body(consent, "en")
        assert consent.kind.facts(consent, "de")["repeat_marker"]
        # the edited consent text ("Text") does not name the cookie: the pre-launch check says so
        assert any("Cookie gegen erneute Teilnahme" in f.message for f in design.check(s))
        assert '"repeat_marker": true' in str(transfer.dumps(s, with_files=False))
        copy = duplicate.create(s, s.members[0], "Kopie")
        assert (copy[0] if isinstance(copy, tuple) else copy).repeat_marker
    assert "Cookie gegen erneute Teilnahme: wird gesetzt" in client.get(E + "/items/1").data.decode()
    privacy = client.get(f"/s/{db_code(app)}/privacy").data.decode()
    assert f"für {MARKER_DAYS} Tage in einem weiteren Cookie" in privacy
    # fixed once the survey runs: the consent named it
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    fill_questions(app)  # every element needs a question before the start (pre-launch check, V11)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/meta", data=META)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and s.repeat_marker


def db_code(app):
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_one_choice_for_taking_part_again(app, client, survey):
    """Session, cookie or lab mode as one choice (UI/UX audit 2026-10-03, U7); in the field only the lab mode may
    change, the cookie stays as the consent named it."""
    from app import panel

    def state():
        with app.app_context():
            s = db.session.get(Survey, 1)
            return panel.multi(s), s.repeat_marker

    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    for mode, expected in (("cookie", (False, True)), ("multi", (True, False)), ("session", (False, False))):
        client.post("/surveys/1/meta", data={**META, "repeat_mode": mode})
        assert state() == expected, mode
    client.post("/surveys/1/meta", data={**META, "repeat_mode": "cookie"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    page = client.get("/surveys/1").data.decode()
    assert re.search(r'value="session"[^>]*disabled', page) and not re.search(r'value="multi"[^>]*disabled', page)
    client.post("/surveys/1/meta", data={**META, "repeat_mode": "multi"})
    assert state() == (True, True)                                      # lab mode on, the cookie setting kept
    client.post("/surveys/1/meta", data={**META, "repeat_mode": "session"})
    assert state() == (False, True)                                     # the cookie cannot be switched off now
