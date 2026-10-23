"""Live: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits  # noqa: F401
from tests.helpers.audit_cr8_a import Survey, app, client, db  # noqa: F401
from tests.helpers.audit_cr8_c import LiveQuestion, _other  # noqa: F401
from tests.helpers.audit_cr8_d import _dead_pid, jobs, os  # noqa: F401
from tests.helpers.audit_cr8_g import STATIC, _js, live_add, live_make  # noqa: F401
from tests.helpers.audit_cr8_h import _fake, ai, pytest  # noqa: F401
from tests.helpers.audit_cr8_j import read  # noqa: F401
from tests.helpers.audit_review2_reste import ROOT, re  # noqa: F401


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_live_runs_limit_one_sender_and_report_the_cap(app, client, monkeypatch):
    from app import notify
    from tests.live.test_live import add as live_add, make, send, steer

    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append(text))
    code, sid, block = make(app, client)
    q = live_add(app, client, sid, block, "single_choice", question="Wie war es?", options=["gut", "schlecht"])
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    steer(client, code, action="start")
    app.config.update(LIVE_RUN_SENDER_JOINS=2, LIVE_RUN_SENDER_QUESTIONS=1, LIVE_RUN_PARTICIPATIONS=3, LIVE_QA_FLOOD=0)
    one = {"REMOTE_ADDR": "203.0.113.20"}

    def answer(env):
        p = app.test_client()
        p.get(f"/live/{code}", environ_base=env)
        return p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "1"}, environ_base=env)

    assert [answer(one).status_code for _ in range(3)] == [200, 200, 429]   # per sender and run
    assert [answer({"REMOTE_ADDR": f"192.0.2.{n}"}).status_code for n in (1, 2)] == [200, 200]  # the cap: reported
    assert len([t for t in sent if "Teilnahmen erreicht" in t]) == 1
    asked = [app.test_client().post(f"/live/{code}/qa", data={"text": f"Frage {n}"}, environ_base=one).status_code for n in range(2)]
    assert asked == [200, 429]
    steer(client, code, action="new_run")                                    # a new run: the sender starts afresh
    assert answer(one).status_code == 200


def test_phones_in_the_room_go_on_when_their_address_typed_wrong_codes(app, client):
    from tests.live.test_live import make

    code, sid, block = make(app, client)
    app.config["CODE_LIMIT"] = 2
    env = {"REMOTE_ADDR": "203.0.113.50"}
    joined = app.test_client()
    assert joined.get(f"/live/{code}", environ_base=env).status_code == 200
    typo = app.test_client()
    assert [typo.get(f"/live/?code=zz{n}", environ_base=env).status_code for n in range(3)] == [404, 404, 429]
    assert joined.get(f"/live/{code}/state", environ_base=env).status_code == 200   # joined before: goes on
    assert app.test_client().get(f"/live/{code}", environ_base=env).status_code == 429  # a new phone waits


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_e10_live_settings_through_file_and_duplicate(app, client):
    import json

    from flask_login import login_user

    from app import duplicate, transfer
    from app.models import User

    client.post("/presenter/new", data={"title": "Vortrag", "language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Vortrag"))
        s.live = {**s.live, "flow": "blocks", "teams": ["Rot", "Blau"], "emojis": ["\U0001F44D", "\U0001F44E"]}
        db.session.commit()
        sid = s.id
        before = {k: s.live.get(k) for k in ("flow", "teams", "emojis")}
    assert before["flow"] == "blocks"
    with app.test_request_context("/"):
        s = db.session.get(Survey, sid)
        user = db.session.get(User, s.created_by_id)
        login_user(user)
        raw = json.loads(transfer.dumps(s))
        assert "contact_email" not in raw["live"]
        new, _ = transfer.create(raw, user, title="Vortrag Datei")
        assert {k: (new.live or {}).get(k) for k in before} == before
        dup, _ = duplicate.create(db.session.get(Survey, sid), user, "Vortrag Kopie")
        assert {k: (dup.live or {}).get(k) for k in before} == before
        # what a file may carry: known reactions only, teams as the settings store them
        raw["live"].update(flow="anything", emojis=["\U0001F44D", "<b>"],
                           teams=["  A  b ", "A b", "x" * 60] + [f"T{n}" for n in range(30)])
        new, _ = transfer.create(raw, user, title="Vortrag Datei 2")
        assert "flow" not in new.live and new.live["emojis"] == ["\U0001F44D"]
        assert new.live["teams"][:2] == ["A b", "x" * 40] and len(new.live["teams"]) == 20


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_votes_at_the_same_moment_all_count(app, client, monkeypatch):
    from app import limits
    from tests.live.test_live import make

    code, sid, _block = make(app, client, "Hörsaal")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1"})
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/qa", data={"text": "Gibt es die Folien?"}).json["ok"]
    with app.app_context():
        qid = db.session.scalar(db.select(LiveQuestion.id))
    original = limits.allow_live

    def meanwhile(kind):  # someone else's vote is committed after this request read the row
        with app.app_context():
            sqlite = db.engine.dialect.name == "sqlite"
        if sqlite:
            # SQLite locks the whole database for the device receipt; simulate a stale loaded question in
            # this transaction. PostgreSQL still exercises the genuinely independent committed write.
            from sqlalchemy import text

            db.session.execute(text("UPDATE live_question SET votes = votes + 1 WHERE id = :id"), {"id": qid})
        else:
            _other(app, "UPDATE live_question SET votes = votes + 1 WHERE id = :id", id=qid)
        return original(kind)

    monkeypatch.setattr(limits, "allow_live", meanwhile)
    assert app.test_client().post(f"/live/{code}/qa/{qid}/vote").json["votes"] == 2   # was 1: one vote lost
    monkeypatch.setattr(limits, "allow_live", original)


def test_a_control_command_works_on_what_another_one_wrote_meanwhile(app, client, monkeypatch):
    from app import live
    from tests.live.test_live import add as add_question
    from tests.live.test_live import make, steer

    code, sid, block = make(app, client, "Seminar")
    add_question(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    original = live._flatten

    def meanwhile(survey):  # the other device's "new run" is committed after this request loaded the survey
        with app.app_context():
            s = db.session.get(Survey, sid)
            st = live.state(s)
            s.live = {**st, "runs": st["runs"] + [{**st["run"]}], "run": {"id": 4711, "name": "Von nebenan", "started": ""}}
            db.session.commit()
        return original(survey)

    monkeypatch.setattr(live.control, "_flatten", meanwhile)
    st = steer(client, code, action="next")
    monkeypatch.setattr(live.control, "_flatten", original)
    assert st["run"]["name"] == "Von nebenan"                                        # was: the older state written back
    with app.app_context():
        assert live.state(db.session.get(Survey, sid))["run"]["id"] == 4711


# --- from tests/audits/test_cr8_d.py: Code review 2026-10-07, package D "Schätzungen und Aufträge" (C1, C3-C8): start, state and rescue of the background ---


def test_pid_alive_on_this_platform():
    assert jobs.pid_alive(os.getpid())
    assert not jobs.pid_alive(_dead_pid())
    assert not jobs.pid_alive(0) and not jobs.pid_alive(None)


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f12_live_answer_failure_is_shown(app, client):
    code, sid, block = live_make(app, client, "Fehler")
    live_add(app, client, sid, block, "single_choice", question="Welche?", options=["A", "B"])
    phone = app.test_client().get(f"/live/{code}").get_data(as_text=True)
    assert 'data-fail="Nicht gesendet. Bitte die Seite neu laden und es erneut versuchen."' in phone
    src = _js("live.js")
    assert "r.json().catch(function () { return {}; })" in src[:src.index("function tick(")]
    assert '.catch(function () { sending = false; failed(""); });' in src
    assert "error.textContent = reason || stage.dataset.fail" in src


def test_dead_presenter_classes_are_gone():
    css = (STATIC / "participant.css").read_text(encoding="utf-8")
    assert ".live-clock" not in css and ".live-next" not in css


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i6_json_list_and_live_vote(app, client, monkeypatch):
    from app import ai_scales

    with app.app_context():
        assert ai.json_list('Hier: [{"name": "A", "why": "eins\nzwei"}]')[0]["why"] == "eins\nzwei"
        with pytest.raises(ai.AIError):
            ai.json_list("kein JSON")
    _fake(monkeypatch, '[{"name": "Vertrauen", "why": "Satz eins\nSatz zwei"}]')
    client.post("/surveys/new", data={"title": "Skalen", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Skalen"))
        assert ai_scales.construct_ideas(s)[0]["name"] == "Vertrauen"
    _fake(monkeypatch, '{"questions": [{"type": "text_input", "question": "Ein Wort\nzur Vorlesung", "why": ""}]}')
    client.post("/presenter/new", data={"title": "MTI"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="MTI")).code
    page = client.post(f"/presenter/{code}/ai", data={"description": "Vorlesung, Tempo und Übung"}).data.decode()
    assert "Ein Wort zur Vorlesung" in page


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_g8_dead_live_selectors_and_classes_are_gone():
    live = read("live.js")
    for sel in ("data-moderation-wall", "data-moderation-question", "data-group-title", "data-steering-toggle",
                "data-clock"):
        assert sel not in live, sel
    assert ".live-joincode" not in read("participant.css")


# --- from tests/audits/test_review2_reste.py: Second code review 2026-10-03, package 7 (the rest): no inline scripts and handlers, so the CSP allows scripts ---


def test_dead_live_css_is_gone():
    css = open(os.path.join(ROOT, "app/static/app.css"), encoding="utf-8").read()
    for name in ("live-questions", "live-move", "live-section-panel", "live-section-head", "live-add-section",
                 "live-rename", "live-end-text", "live-container", "live-settings", "is-on-screen"):
        assert "." + name not in css, name
    assert not re.search(r"\.live-join[ .{,]", css) and not re.search(r"^\.live-url", css, re.M)
    assert ".live-join-box" in css                                    # the one still in use stays
    pcss = open(os.path.join(ROOT, "app/static/live.css"), encoding="utf-8").read()
    assert not re.search(r"\.live-section[ {]", pcss) and ".live-step" not in pcss and ".live-section-label" in pcss
