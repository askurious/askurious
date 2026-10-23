"""What a script without an account could do to a survey and a diary study (Gesamtaudit 2026-10-22, package P3)."""
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK

BOT = {"REMOTE_ADDR": "203.0.113.8"}


def _running(client):
    from tests.conftest import fill_consent

    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    assert client.post("/surveys/1/start", data={}).status_code == 302


def _bot_completes(app, n):
    for _ in range(n):
        p = app.test_client()
        p.get("/s/1", environ_base=BOT)
        p.post("/s/1", data={**CONSENT_OK, "more_info": "bot"}, environ_base=BOT)   # consent page, hidden field filled
        p.post("/s/1", data={"nav": "next", "more_info": "bot"}, environ_base=BOT)  # info page -> end


def test_completions_of_a_script_do_not_close_the_survey(app, client, survey):
    """D-06: five completions with the hidden field filled closed a survey with a sample size of 5 for everyone."""
    _running(client)
    with app.app_context():
        db.session.get(Survey, 1).max_responses = 5
        db.session.commit()
    _bot_completes(app, 5)
    with app.app_context():
        s = db.session.get(Survey, 1)
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1, source="real")).all()
        assert sum(r.status == Response.COMPLETED for r in rows) == 5
        assert all((r.paradata or {}).get("_bot") for r in rows)
        assert s.status == Survey.RUNNING
        # a person completes the remaining places as before
        s.max_responses = 6
        db.session.commit()
    p = app.test_client()
    p.get("/s/1", environ_base={"REMOTE_ADDR": "198.51.100.30"})
    p.post("/s/1", data=CONSENT_OK, environ_base={"REMOTE_ADDR": "198.51.100.30"})
    p.post("/s/1", data={"nav": "next"}, environ_base={"REMOTE_ADDR": "198.51.100.30"})
    with app.app_context():
        from app import lifecycle

        s = db.session.get(Survey, 1)
        assert not lifecycle.sample_full(s) and lifecycle.sample_full(s, 1)


def _start(app, addr):
    c = app.test_client()
    env = {"REMOTE_ADDR": addr}
    assert c.get("/s/1", environ_base=env).status_code == 200
    return c.post("/s/1", data=CONSENT_OK, environ_base=env).status_code


def test_starts_count_per_48_as_well(app, client, survey):
    """D-01: the limits per sender counted an IPv6 sender per /64; a /48 is 65 536 of them, a script got through with a
    new /64 for every start. Now the /48 counts as well, with SENDER_WIDE_FACTOR times the limit."""
    _running(client)
    app.config.update(START_LIMIT=3, SURVEY_SENDER_LIMIT=3, START_WINDOW=600, SURVEY_SENDER_WINDOW=600,
                      SENDER_WIDE_FACTOR=4)
    assert [_start(app, f"2001:db8:aa:1::{i + 1:x}") for i in range(4)] == [302, 302, 302, 429]  # one /64, as before
    wide = [_start(app, f"2001:db8:aa:{n + 2:x}::1") for n in range(12)]    # a new /64 of the same /48 every time
    assert wide.count(302) == 12 - 3 and set(wide[9:]) == {429}             # 3 x 4 for the /48, 3 of them used above
    assert _start(app, "2001:db8:bb:1::1") == 302                             # another /48 is not held
    assert _start(app, "198.51.100.30") == 302                               # IPv4 is not added up


def test_wrong_codes_of_one_sender_lock_no_invited_person_out(app, client):
    """A-02: the wrong codes of all senders together lock a diary study's codes for a while (BIZ-07); one sender alone
    reached that limit, and every personal invitation code got 429. An invitation code that exists gets in now; the
    test codes wait as before."""
    from app import diary, limits
    from tests.diary.test_diary import API, _new

    code = _new(app, client)
    app.config.update(STUDY_CODE_LIMIT=5, CODE_LIMIT=100)
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        invite = diary.make_invites(survey, 1)[0]["c"]
        test = diary.test_code(survey)
        db.session.commit()
    attacker = app.test_client()
    for n in range(6):
        attacker.get(f"{API}/join/{code}-{n:08d}", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    for store in (limits._asked, limits._fallback):
        store.clear()
    other = {"REMOTE_ADDR": "198.51.100.20"}
    assert app.test_client().get(f"{API}/join/{code}-{invite}", environ_base=other).status_code == 200
    assert app.test_client().get(f"{API}/join/{test}", environ_base=other).status_code == 429


def test_one_sender_fills_only_its_share_of_the_voice_room(app, client, survey):
    """D-02: all holders of the link shared VOICE_MAX_MB; one script filled it and real recordings were refused. A
    sender may fill VOICE_SENDER_SHARE percent of it per hour; a recording beyond falls back to the text field."""
    import io

    from app.items import voice
    from app.models import Response
    from tests.interview.test_voice import WEBM, _start, _voice

    voice._used.clear()
    _voice(app, client, max_seconds="10")
    url, uid = _start(app, client)
    app.config.update(VOICE_MAX_MB=1, VOICE_SENDER_SHARE=50)                 # 512 KB per sender and hour
    size = 300 * 1024

    def participate(addr):
        p = app.test_client()
        env = {"REMOTE_ADDR": addr}
        p.get(url, environ_base=env)
        p.post(url, data=CONSENT_OK, environ_base=env)
        form = {"nav": "next", "i2_seconds": "5", "i2_text": "getippt",
                "i2_audio": (io.BytesIO(WEBM + bytes(size - len(WEBM))), "a.webm", "audio/webm")}
        return p.post(url, data=form, content_type="multipart/form-data", environ_base=env)

    participate("203.0.113.7")
    participate("203.0.113.7")                                                  # beyond the sender's share
    participate("198.51.100.20")                                                # another sender: still room
    with app.app_context():
        audio = [bool((r.answers or {}).get("2", {}).get("audio")) if isinstance((r.answers or {}).get("2"), dict)
                 else False for r in db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id))]
    assert audio == [True, False, True]
