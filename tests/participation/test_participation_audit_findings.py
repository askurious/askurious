"""Participation: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit13_rest import Response, Survey, add, db  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_a import OTHER, _member, _person, _upload as _upload__audit_2026_10_06_a, experiments_on, json  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits, _kept_files, _running as _running__audit_2026_10_06_b, _start as _start__audit_2026_10_06_b, fill_consent, limits, os, re, time  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_c import _fresh_count, WEBM, _Answer, _file as _file__audit_2026_10_06_c, _folder, _pack, _participant, _real_fetch, _row, _send, _start as _start__audit_2026_10_06_c, _voice, _with_required_text  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_e import CONSENT_OK, _start as _start__audit_2026_10_06_e  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_f import Board, MultiDict, boards, datetime  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_g import OnceValue, _rows, _statements, command, inspect, migrations, needs_sqlite, once_hash, once_survey, panel, runtime  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_a import _likert, export, quality, timedelta, utcnow  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_c import _active_from_today, _enrol, _entries_of, _new  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_d import erasure  # noqa: F401
from tests.helpers.audit_audit_rest import E as E__audit_rest, Item  # noqa: F401
from tests.helpers.audit_audit_small3 import E as E__audit_small3, pytest  # noqa: F401
from tests.helpers.audit_cr8_a import _interview, app, client  # noqa: F401
from tests.helpers.audit_cr8_b import Statements, _taken, lifecycle  # noqa: F401
from tests.helpers.audit_cr8_c import ResearchProject, SplitSession, TURN, _entries, _fake as _fake__cr8_c, _new_project, ai, event  # noqa: F401
from tests.helpers.audit_cr8_e import _count_rows, _hypothesis, _running as _running__cr8_e  # noqa: F401
from tests.helpers.audit_cr8_g import _js  # noqa: F401
from tests.helpers.audit_cr8_h import _fake as _fake__cr8_h  # noqa: F401
from tests.helpers.audit_cr8_i import _linked_question, linked  # noqa: F401
from tests.helpers.audit_review2_reste import E as E__review2_reste, QUOTA_STATE  # noqa: F401
from tests.helpers.audit_review_fixes import Block, ContactEntry, E as E__review_fixes, _start as _start__review_fixes, _state, loops, test_flow, test_loops, test_split  # noqa: F401
from tests.helpers.audit_review_medium import E as E__review_medium, StaleDataError, _file as _file__review_medium, _overlap, _upload as _upload__review_medium, test_participate, test_quotas  # noqa: F401
from tests.helpers.audit_s9_one_source import E as E__s9_one_source, OWN_GERMAN, ROOT, _Ranges, _all_pages_in_arabic, _ltr_classes, _set, glob, html_mod, io, item_texts, items  # noqa: F401


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_e3_paired_not_shown_is_missing(app, client, survey):
    from app import export

    add(client, "paired")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        item = [i for b in s.blocks for i in b.items if i.type == "paired"][0]
        objs = [{"id": f"o{k}"} for k in range(5)]
        item.config = {**item.config, "objects": objs, "per_person": 4, "versions": 2, "answer": "strength"}
        item.texts = {"de": {**(item.texts or {}).get("de", {}), "question": "?", "objects": {o["id"]: o["id"] for o in objs}}}
        item.kind.make_design(item)
        db.session.commit()
        versions = item.config["design"]["versions"]
        ans = {"v": 0, **{f"t{t}": 1 for t in range(len(versions[0]))}}
        db.session.add(Response(survey_id=1, source="real", status="completed", pid="p1", language="de",
                                answers={str(item.id): ans}))
        db.session.commit()
        cols, data, meta = export._spss_dictionary(s, False, ("real",), None, True, None)
        shown77 = [c for c, _ in cols if c.startswith(item.code + "_") and data[0].get(c) == -77]
        assert any(c.endswith("_st") for c in shown77) and any(not c.endswith("_st") for c in shown77)
        assert all(-77.0 in meta[c]["missing_all"] for c in shown77)
        assert item.kind.missing_codes(item, f"{item.code}_version") == ()


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s12_import_leaves_out_experimental_types_of_others(app, client, survey, monkeypatch):
    # since 2026-10-16 no element type is experimental: the voice message stands in for one (feature "probe")
    from app import experimental
    from app.items.voice import Voice

    features = experimental.FEATURES + [("probe", "Probe", "Test")]
    monkeypatch.setattr(experimental, "FEATURES", features)
    monkeypatch.setattr(experimental, "KEYS", [k for k, _l, _t in features])
    monkeypatch.setattr(Voice, "experimental", "probe", raising=False)
    experiments_on(client, "probe")
    add(client, "voice")
    add(client, "text_input")
    body = json.loads(client.get("/surveys/1/export/survey.json").get_data())
    other = _person(app, OTHER, "Other", may_create=True)
    page = _upload__audit_2026_10_06_a(other, body, title="Kopie mit Stimme")
    assert "experimentellen Typs" in page
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Kopie mit Stimme"))
        types = [i.type for b in s.blocks for i in b.items]
        assert "voice" not in types and "text_input" in types
    page = _upload__audit_2026_10_06_a(client, body, title="Eigene Kopie")                    # whoever has it keeps it
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Eigene Kopie"))
        assert "voice" in [i.type for b in s.blocks for i in b.items]


def test_s22_live_restart_needs_admin(app, client):
    from tests.live.test_live import make

    code, sid, _block = make(app, client)
    _person(app, OTHER, "Editor")
    _member(app, sid, OTHER, "editor")
    editor = _person(app, OTHER, "Editor")
    with app.app_context():
        run = db.session.get(Survey, sid).live["run"]["id"]
        for n in range(3):
            db.session.add(Response(survey_id=sid, source="real", status=Response.COMPLETED, answers={}, pid=f"p{n}",
                                    params={"live_run": run}))
        db.session.commit()

    def count():
        with app.app_context():
            return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid, is_preview=False))

    r = editor.post(f"/presenter/{code}/control", data={"action": "restart"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403 and count() == 3
    assert "data-restart" not in editor.get(f"/presenter/{code}/screen").data.decode()
    client.post(f"/presenter/{code}/control", data={"action": "restart"}, headers={"X-Requested-With": "fetch"})
    assert count() == 0


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_starts_from_one_slash_64_share_the_limit(app, client, survey):
    code = _running__audit_2026_10_06_b(app, client)
    app.config["START_LIMIT"] = 3
    statuses = [_start__audit_2026_10_06_b(app, code, f"2001:db8:1:2::{n:x}")[1].status_code for n in range(1, 6)]
    assert statuses == [302, 302, 302, 429, 429]
    assert _start__audit_2026_10_06_b(app, code, "2001:db8:9:9::1")[1].status_code == 302     # another connection


def test_the_cap_of_a_survey_reports_once_per_window(app, client, survey, monkeypatch):
    from app import notify

    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append(text))
    code = _running__audit_2026_10_06_b(app, client)
    app.config.update(SURVEY_START_LIMIT=2, SURVEY_START_WINDOW=600)
    assert [_start__audit_2026_10_06_b(app, code, f"198.51.100.{n}")[1].status_code for n in range(1, 6)] == [302] * 5  # nobody refused
    assert len(sent) == 1 and f"`{code}`" in sent[0] and "Niemand wird abgewiesen" in sent[0]
    page = client.get("/surveys/1/field").data.decode()
    assert page.count("Mehr als 2 neue Teilnahmen innerhalb von 10 Minuten") == 1 and "Auffällig viele Anfragen" in page
    with app.app_context():
        folder = limits._notes_folder(db.session.get(Survey, 1))
    old = [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith("note-")]
    assert len(old) == 1
    past = time.time() - (limits.NOTE_DAYS + 1) * 86400
    os.utime(old[0], (past, past))                                         # a week ago: no longer on the tab
    assert "Auffällig viele Anfragen" not in client.get("/surveys/1/field").data.decode()


def test_a_begun_participation_drops_the_kept_results_too(app, client, survey):
    # since the audit of 2026-10-12 (E-8, E-9) every deletion of a real participation drops them: they may have been
    # computed with an earlier pool, and the methods part counts outside the pool too; the loop is held by the limit
    code = _running__audit_2026_10_06_b(app, client)
    folder = _kept_files(app)
    p, _ = _start__audit_2026_10_06_b(app, code)                                               # begun, not completed
    assert "gelöscht" in p.post(f"/s/{code}/delete", data={"sure": "1", "here": "1"}).data.decode()
    assert not os.path.exists(os.path.join(folder, "results.bin")) and not os.path.exists(os.path.join(folder, "board-1.json"))


def test_wrong_codes_count_on_every_public_code_address(app, client, survey):
    code = _running__audit_2026_10_06_b(app, client)
    app.config["CODE_LIMIT"] = 3
    env = {"REMOTE_ADDR": "203.0.113.40"}
    taking_part, _ = _start__audit_2026_10_06_b(app, code, env["REMOTE_ADDR"])                 # a participation under way, same address
    p = app.test_client()
    assert p.get("/s/gibtsnicht", environ_base=env).status_code == 404
    assert p.get("/s/gibtsnicht/privacy", environ_base=env).status_code == 404
    assert p.get("/live/gibtsnicht", environ_base=env).status_code == 404
    blocked = p.get(f"/s/{code}", environ_base=env)                         # three wrong ones: a pause, also for real codes
    assert blocked.status_code == 429 and "Zu viele unbekannte Links" in blocked.data.decode()
    assert p.get(f"/s/{code}/complaint", environ_base=env).status_code == 429
    assert p.get("/live/gibtsnicht", environ_base=env).status_code == 429
    assert p.get(f"/s/{code}", environ_base={"REMOTE_ADDR": "203.0.113.41"}).status_code == 200  # others go on
    assert taking_part.get(f"/s/{code}", environ_base=env).status_code == 200      # and so does the participation


def test_a_draft_shows_a_neutral_page(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/meta", data={"title": "Geheime Studie zu Nachbarn", "languages": ["de", "en"],
                                         "default_language": "de", "allow_back": "1"})
    stranger = app.test_client()
    for path in ("/s/1", "/s/1/privacy", "/s/1/complaint", "/s/1/delete"):
        r = stranger.get(path)
        html = r.data.decode()
        assert r.status_code == 200 and "derzeit nicht geöffnet" in html, path
        assert "Geheime Studie" not in html and "studie@example.org" not in html and "Philipp" not in html, path
    # the researchers see their draft as before
    own = client.get("/s/1/privacy").data.decode()
    assert "studie@example.org" in own and "Geheime Studie" in own
    # and so do the test persons of a pretest link (the footer of the preview leads to the privacy page)
    page = client.get("/surveys/1/field?pretest=7").data.decode()
    path = re.search(r'id="pretest-url"[^>]*value="([^"]+)"', page).group(1).split("/p/", 1)[1]
    tester = app.test_client()
    assert tester.get(f"/p/{path}").status_code == 200
    assert "studie@example.org" in tester.get("/s/1/privacy").data.decode()


# --- from tests/audits/test_audit_2026_10_06_c.py: Audit of 2026-10-06, package C (uploads and resources): voice messages written only when the page goes on, with a ---


def test_a_recording_is_written_only_when_the_page_goes_on(app, client, survey):
    _with_required_text(app, client)
    url, uid = _start__audit_2026_10_06_c(app, client)
    p = _participant(app, url)
    r = _send(p, url, i2_text="getippt")                                          # the required text is missing
    assert r.status_code == 200 and "erst gespeichert, wenn Sie weitergehen" in r.data.decode()
    _, pid, answers = _row(app)
    assert not os.path.exists(os.path.join(_folder(app, uid, pid), "2.webm"))
    assert answers.get("2") == {"text": "getippt"}                                 # what was typed stays
    assert _send(p, url, i2_text="getippt", i3="Anna").status_code == 302
    name = _row(app)[2]["2"]["audio"]["file"]
    assert name.startswith("2-") and name.endswith(".webm")
    assert os.path.isfile(os.path.join(_folder(app, uid, pid), name))


def test_back_and_the_language_switch_write_no_recording(app, client, survey):
    _voice(app, client)
    add(client, "page_break")
    add(client, "text_input")
    url, uid = _start__audit_2026_10_06_c(app, client)
    p = _participant(app, url)
    assert _send(p, url).status_code == 302                                       # stored, on to the next page
    _, pid, _ = _row(app)
    p.post(url, data={"nav": "back"})
    other = WEBM + b"\x01" * 50
    assert _send(p, url, data=other, nav="", set_lang="en").status_code == 302    # the language switch
    assert _send(p, url, data=other, nav="back").status_code == 302               # "Back" from the page itself
    with open(os.path.join(_folder(app, uid, pid), _row(app)[2]["2"]["audio"]["file"]), "rb") as f:
        assert f.read() == WEBM                                                   # the first recording stands
    assert _row(app)[2]["2"]["audio"]["bytes"] == len(WEBM)


def test_a_full_survey_offers_the_text_field(app, client, survey, caplog):
    _voice(app, client, text_fallback="")                                         # no text field of its own
    url, uid = _start__audit_2026_10_06_c(app, client)
    p = _participant(app, url)
    app.config["VOICE_MAX_MB"] = 0
    try:
        html = p.get(url).data.decode()
        assert "Aufnahmen sind gerade nicht möglich" in html and 'name="i2_text"' in html and "data-voice-recorder" not in html
        r = _send(p, url)                                                         # a recording sent anyway
        assert r.status_code == 200 and "Aufnahmen können gerade nicht gespeichert werden" in r.data.decode()
        assert "VOICE_MAX_MB reached" in caplog.text
        assert _send(p, url, i2_text="statt dessen getippt").status_code == 302
        _, pid, answers = _row(app)
        assert answers["2"] == {"text": "statt dessen getippt"}
        assert not os.path.exists(os.path.join(_folder(app, uid, pid), "2.webm"))
    finally:
        app.config["VOICE_MAX_MB"] = 500


def test_a_nearly_full_disk_refuses_the_recording(app, client, survey, monkeypatch, caplog):
    from collections import namedtuple

    from app.items import voice

    _voice(app, client)
    url, uid = _start__audit_2026_10_06_c(app, client)
    p = _participant(app, url)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(voice.shutil, "disk_usage", lambda where: usage(10**9, 10**9, 10 * 1024 * 1024))
    r = _send(p, url, i2_text="")
    assert r.status_code == 200 and "Aufnahmen können gerade nicht gespeichert werden" in r.data.decode()
    assert "the disk is nearly full" in caplog.text
    assert _send(p, url, i2_text="getippt").status_code == 302
    assert _row(app)[2]["2"] == {"text": "getippt"}


def test_the_survey_cap_counts_what_is_stored(app, client, survey):
    from app.items import voice

    _voice(app, client)
    url, uid = _start__audit_2026_10_06_c(app, client)
    assert _send(_participant(app, url), url).status_code == 302
    with app.app_context():
        assert voice.used_bytes(uid) == len(WEBM)
        app.config["VOICE_MAX_MB"] = 0
        try:
            assert voice.closed(uid) == "survey"
        finally:
            app.config["VOICE_MAX_MB"] = 500
        assert voice.closed(uid) is None


def test_the_parts_are_parsed_one_after_the_other(app, client, survey):
    from app import survey_files

    body = json.dumps(_file__audit_2026_10_06_c(client)).encode()
    with app.app_context():
        parts = survey_files.read(_pack({f"s{i}/survey.json": body for i in range(3)}))
        assert len(parts) == 3 and all(not hasattr(p, "data") for p in parts)       # nothing parsed and held
        assert parts[0].load() is not parts[0].load()                             # parsed when needed, then let go


def test_doi_redirects_only_to_the_agencies(monkeypatch):
    record = json.dumps({"title": "Ein Titel", "issued": {"date-parts": [[2023]]}}).encode()
    citations, asked = _real_fetch(monkeypatch, {
        "https://doi.org/10.1/a": _Answer(302, "https://api.crossref.org/v1/works/10.1/a/transform"),
        "https://api.crossref.org/v1/works/10.1/a/transform": _Answer(body=record),
        "https://doi.org/10.1/b": _Answer(302, "https://intern.example/geheim"),
        "https://doi.org/10.1/c": _Answer(302, "http://api.datacite.org/dois/10.1/c"),
        "https://doi.org/10.1/d": _Answer(302, "https://api.datacite.org:8443/x"),
    })
    meta = citations.fetch("10.1/a")
    assert (meta["title"], meta["year"]) == ("Ein Titel", 2023)
    for doi in ("10.1/b", "10.1/c", "10.1/d"):                                     # elsewhere, plain http, another port
        assert citations.fetch(doi) == {}
    assert "https://intern.example/geheim" not in asked


def test_doi_answer_is_read_up_to_1_mb(monkeypatch):
    big = b'{"title": "' + b"x" * (1024 * 1024 + 10) + b'"}'
    citations, _ = _real_fetch(monkeypatch, {"https://doi.org/10.1/big": _Answer(body=big),
                                             "https://doi.org/10.1/deep": _Answer(body=b"[" * 200_000 + b"]" * 200_000),
                                             "https://doi.org/10.1/list": _Answer(body=b"[1, 2]")})
    assert citations.fetch("10.1/big") == {}
    assert citations.fetch("10.1/deep") == {}
    assert citations.fetch("10.1/list") == {}


# --- from tests/audits/test_audit_2026_10_06_e.py: Security audit 2026-10-06, package E: researcher texts of the delay discounting stay inside their data block (S3), ---


def test_participant_preview_and_live_pages_are_never_kept(app, client, survey):
    _start__audit_2026_10_06_e(client)
    with app.app_context():
        code = db.session.get(Survey, 1).code
    p = app.test_client()
    assert p.get(f"/s/{code}").headers["Cache-Control"] == "no-store"
    p.post(f"/s/{code}", data=CONSENT_OK)
    r = p.get(f"/s/{code}")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert p.get(f"/s/{code}/privacy").headers["Cache-Control"] == "no-store"
    assert p.get("/live/").headers["Cache-Control"] == "no-store"
    html = client.get("/surveys/1/editor/preview?device=mobile").data.decode()
    link = re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1)
    shared = p.get("/" + link.split("://", 1)[1].split("/", 1)[1])
    assert shared.status_code == 200 and shared.headers["Cache-Control"] == "no-store"
    assert "no-store" not in (p.get("/static/app.css?v=x").headers.get("Cache-Control") or "")


# --- from tests/audits/test_audit_2026_10_06_f.py: Audit 2026-10-06 (security and performance), package F: performance of the evaluation pages. ---


def test_p10_waiting_requests_show_a_placeholder(app, client, survey, monkeypatch):
    from tests.dashboards.test_boards import _board, _fill, _link, _stranger, study  # noqa: F401 (the fixture's helpers)

    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sc"), ("question", "Womit fahren Sie?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad")]))
    client.post("/surveys/1/pool", data={"source": "real"})
    _board(client)
    token = _link(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i in range(12):
            s.responses.append(Response(source="real", status=Response.COMPLETED, started_at=datetime(2026, 9, 1),
                                        completed_at=datetime(2026, 9, 1, 0, 5 + i), answers={"2": "1" if i % 2 else "2"}))
        db.session.commit()
        lock = boards._path(db.session.get(Board, 1)) + ".lock"
    monkeypatch.setattr(boards.compute, "LOCK_WAIT", 0.2)
    calls = []
    build = boards.build
    monkeypatch.setattr(boards.compute, "build", lambda *a, **k: calls.append(1) or build(*a, **k))
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    open(lock, "w").close()                                                 # another request computes the first state
    guest = _stranger(app)
    page = guest.get(f"/d/{token}").data.decode()
    assert "Die Zahlen werden berechnet" in page and 'data-pending="1"' in page and not calls
    assert 'data-version="pending"' in page and "data-board-n hidden" in page
    assert guest.get(f"/d/{token}/state?v=pending").get_json() == {"v": "pending"} and not calls
    os.remove(lock)                                                         # done (or gone): the state comes
    fresh = guest.get(f"/d/{token}/state?v=pending").get_json()
    assert fresh["v"] != "pending" and "Womit fahren Sie?" in fresh["html"] and fresh["n"] and len(calls) == 1
    page = guest.get(f"/d/{token}").data.decode()
    assert "data-pending" not in page and f'{fresh["n"]}</span> Teilnahmen' in page and len(calls) == 1


# --- from tests/audits/test_audit_2026_10_06_g.py: Audit 2026-10-06, package G: performance of the database and of taking part. P3 (index for the counts of ---


def test_the_counts_of_participations_have_their_index(app):
    with app.app_context():
        found = {i["name"]: i["column_names"] for i in inspect(db.engine).get_indexes("response")}
    assert found["ix_response_counts"] == ["survey_id", "source", "is_preview", "status", "completed_at", "started_at",
                                           "live_extra"]  # live_extra since 0087 (performance audit 2026-10-18, PERF-02)


@needs_sqlite
def test_the_counts_do_not_read_the_rows(app, client, survey):
    from app import export

    with app.app_context():
        s = db.session.get(Survey, 1)
        plans = []
        with db.engine.connect() as connection:
            for query in (db.select(db.func.count(Response.id)).filter(
                              Response.survey_id == s.id, Response.completed_at.is_not(None), Response.is_preview.is_(False)),
                          db.select(Response.source, Response.is_preview, Response.status, db.func.count(Response.id))
                          .filter(Response.survey_id == s.id).group_by(Response.source, Response.is_preview, Response.status),
                          db.select(Response.started_at, Response.completed_at).filter(
                              Response.survey_id == s.id, Response.source.in_(("real",)), Response.status == "completed",
                              Response.completed_at.is_not(None), Response.started_at.is_not(None))):
                sql = str(query.compile(connection, compile_kwargs={"literal_binds": True}))
                plans.append(" ".join(r[-1] for r in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql)))
        assert all("COVERING INDEX ix_response_counts" in p for p in plans), plans
        assert s.completed_count() == 0 and export.median_seconds(s) is None


def test_a_stored_id_stands_in_the_table_and_is_found_there(app, client, survey):
    uid = once_survey(app, client, params=(("PID", "once"), ("src", "store")))
    p = app.test_client()
    p.get(f"/s/{uid}?PID=p7&src=mail")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert _rows(app) == sorted([("PID", once_hash("p7"), True), ("src", once_hash("mail"), False)])
    seen, stop = _statements(app)
    try:
        with app.app_context():
            assert panel.taken(db.session.get(Survey, 1), {"PID": "p7"}) == "PID"
    finally:
        stop()
    lookups = [s for s in seen if "once_value" in s]
    assert lookups and not any("LIKE" in s or "CAST(response.params" in s for s in seen)


def test_a_parameter_made_once_only_later_finds_the_participations_before(app, client, survey):
    uid = once_survey(app, client, params=(("PID", "store"),))
    p = app.test_client()
    p.get(f"/s/{uid}?PID=p7")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {**s.panel, "params": [{**s.panel["params"][0], "once": True}]}
        db.session.commit()
        assert panel.taken(s, {"PID": "p7"}) == "PID"
        mine = db.session.scalar(db.select(Response))
        assert panel.claim(s, mine) is None and panel.taken(s, {"PID": "p8"}) is None


def test_the_values_follow_the_participation(app, client, survey):
    """Changed, ended as a test run, deleted on request, by the retention periods or with the survey: the rows go
    along (decisions.md: every table that knows a participation is touched)."""
    from app import erasure, lifecycle, retention

    uid = once_survey(app, client)
    for pid in ("a1", "a2", "a3"):
        p = app.test_client()
        p.get(f"/s/{uid}?PID={pid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        s = db.session.get(Survey, 1)
        one, two, three = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        one.params = {"PID": "b1"}
        db.session.commit()
        assert panel.taken(s, {"PID": "a1"}) is None and panel.taken(s, {"PID": "b1"}) == "PID"
        two.source = "test"
        db.session.commit()
        assert not db.session.scalars(db.select(OnceValue).filter_by(response_id=two.id)).all()
        erasure.erase(s, [three], "request")
        db.session.commit()
        assert panel.taken(s, {"PID": "a3"}) is None
        assert db.session.scalar(db.select(db.func.count(OnceValue.id))) == 1
        retention.anonymise(s)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(OnceValue.id))) == 0
    p = app.test_client()
    p.get(f"/s/{uid}?PID=c1")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        s = db.session.get(Survey, 1)
        retention.purge(s)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(OnceValue.id))) == 0
    p = app.test_client()
    p.get(f"/s/{uid}?PID=d1")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(OnceValue.id))) == 0


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_the_migration_takes_the_stored_values_over(app, client, survey):
    once_survey(app, client, params=(("PID", "once"), ("src", "store")))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for params in ({"PID": "p7", "src": "x"}, {"PID": "p7"}, {"PID": "p8", "n": 3}):  # a pair from before T-11
            db.session.add(Response(survey_id=s.id, params=params, answers={}, source="real", is_preview=False))
        db.session.add(Response(survey_id=s.id, params={"PID": "t1"}, answers={}, source="test", is_preview=False))
        db.session.commit()
        migrations._run(command.downgrade, "0010")
        migrations._run(command.upgrade)
        rows = sorted((r.response_id, r.name, r.value_hash, r.once) for r in db.session.scalars(db.select(OnceValue)))
        ids = db.session.scalars(db.select(Response.id).order_by(Response.id)).all()
        assert rows == [(ids[0], "PID", once_hash("p7"), True), (ids[0], "src", once_hash("x"), False),
                        (ids[1], "PID", once_hash("p7"), False), (ids[2], "PID", once_hash("p8"), True)]
        assert panel.taken(s, {"PID": "p7"}) == "PID" and panel.resumable(s, {"PID": "p7"}) is None  # two hold it


def test_the_start_page_asks_the_same_number_of_queries_for_more_surveys(app, client):
    def page():
        seen, stop = _statements(app)
        try:
            text = client.get("/dashboard/").data.decode()
        finally:
            stop()
        return len(seen), text

    client.post("/surveys/new", data={"title": "Eins"})
    client.post("/surveys/new", data={"title": "Zwei"})
    few, _ = page()
    for n in range(4):
        client.post("/surveys/new", data={"title": f"Mehr {n}"})
    many, text = page()
    assert text.count('class="card-title"') == 6 and "0 abgeschlossen" in text
    assert many == few


def test_counts_are_counted_once_per_request_and_again_after_a_write(app, client, survey):
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        seen, stop = _statements(app)
        try:
            assert s.completed_count() == 0 and s.completed_count() == 0 and s.tally() == s.tally()
            counted = [q for q in seen if "count(response.id)" in q]
            assert len(counted) == 2                                                   # one each
            db.session.add(Response(survey_id=s.id, answers={}, source="real", is_preview=False,
                                    status=Response.COMPLETED, completed_at=s.created_at))
            db.session.flush()
            assert s.completed_count() == 1                                            # counted afresh
        finally:
            stop()
        db.session.rollback()


def test_a_step_works_the_signature_out_once(app, client, survey, monkeypatch):
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = Response(survey_id=s.id, answers={}, source="test", is_preview=True)
        runtime.ensure_layout(s, r)
        calls = []
        original = runtime._signature
        monkeypatch.setattr(runtime, "_signature", lambda survey: calls.append(1) or original(survey))
        runtime.ensure_layout(s, r)
        assert calls == [1]


def test_the_readme_counts_without_loading_the_answers(app, client, survey):
    from app import export

    with app.app_context():
        s = db.session.get(Survey, 1)
        for status in (Response.COMPLETED, Response.COMPLETED, Response.ACTIVE):
            db.session.add(Response(survey_id=s.id, answers={"1": "x"}, source="real", is_preview=False, status=status))
        db.session.commit()
        db.session.expire_all()
        s = db.session.get(Survey, 1)
        seen, stop = _statements(app)
        try:
            text = export.readme_md(s)
        finally:
            stop()
    assert "- Completed participations: 2" in text
    assert not any("response.answers" in q for q in seen)


# --- from tests/audits/test_audit_2026_10_12_a.py: Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2, ---


def test_dont_know_in_a_matrix_is_left_out_by_quality_control(app, client, survey):
    add(client, "likert")  # 2
    _likert(client, 2, "A")
    add(client, "likert")  # 3
    _likert(client, 3, "B")
    with app.app_context():
        now = utcnow()
        ok = {f"s{k}": 1 + k % 5 for k in range(6)}
        with_dk = {**ok, "s3": "dk"}
        for answers in ({"2": ok, "3": ok}, {"2": with_dk, "3": ok}):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    answers=answers, started_at=now - timedelta(minutes=5), completed_at=now))
        db.session.commit()
        s = db.session.get(Survey, 1)
        ctx = quality.context(s, export._items(s))
        resp = db.session.scalars(db.select(Response).order_by(Response.id)).all()[1]
        values = quality.values(s, resp, ctx)  # was: TypeError ('str' - 'int')
        assert values["qc_irv"] is not None
        # "dk" counts as left out: the matrix A is the five other answers
        assert quality.halves([[("s0", False), ("s1", False), ("s2", False), ("s3", False)]], with_dk, 5) == [(0.25, 0.25)]
    assert client.get("/surveys/1/results").status_code == 200
    assert client.get("/surveys/1/quality").status_code == 200
    assert client.get("/surveys/1/export/data.csv").status_code == 200


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_diary_speeder_in_data_export_uses_median_of_part(app, client):
    from app import export

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    _entries_of(app, phone, secret, code, 3)
    with app.app_context():
        rows = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        first, entries = rows[0], rows[1:]
        first.completed_at = first.started_at + timedelta(minutes=10)
        for r, s in zip(entries, (20, 22, 24)):
            r.completed_at = r.started_at + timedelta(seconds=s)
        for k in range(2):  # the median of all entries would be 312 s
            db.session.add(Response(survey_id=1, source="real", status="completed", pid=f"x{k}", language="de",
                                    enrolment_id=first.enrolment_id, occasion={"part": "entry"}, answers={},
                                    started_at=first.started_at, completed_at=first.started_at + timedelta(minutes=10)))
        db.session.commit()
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        assert export.marked_ids(survey) == set()
        table = export.rows(survey)
        assert [r["resp_id"] for r in table if r.get("speeder") == 1 or r.get("qc_marked") == 1] == []
        assert sorted(r["duration_rel"] for r in table if r.get("teil") == "m1") == [0.909, 1.0, 1.091]
        note = next(row for row in export.codebook(survey) if row[0] == "duration_rel")[4]
        assert "Median je Teil" in note and "m1 22 s" in note


# --- from tests/audits/test_audit_2026_10_12_d.py: Audit data and calculations 2026-10-12, package D (deletion, quotas, kept results): E-7 to E-11. ---


def test_erasure_removes_kept_results_after_pool_change(app, client, survey):
    add(client, "text_input")  # item 2
    client.post("/surveys/1/editor/items/2", data={"code": "frei", "question": "Ihre Meinung?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.data_pool = {"sources": ["real"], "all": True}  # also unfinished ones
        db.session.add(Response(survey_id=s.id, source="real", status=Response.ACTIVE, pid="1-geheim",
                                answers={"2": "Mein Name ist Erika Mustermann"}))
        db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, pid="1-ok",
                                answers={"2": "anderes"}))
        db.session.commit()
        uid = s.uid
    assert client.get("/surveys/1/results").status_code == 200
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
    assert [n for n in os.listdir(folder) if n.endswith(".bin")]
    with open(os.path.join(folder, "board-1.json"), "w") as f:
        f.write("{}")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.data_pool = {"sources": ["real"], "all": False}  # later: completed ones only
        db.session.commit()
        target = db.session.scalar(db.select(Response).filter_by(pid="1-geheim"))
        erasure.erase(s, [target], "request")
    assert not [n for n in os.listdir(folder) if n.endswith(".bin") or n.startswith("board-")]


def test_screen_out_drops_the_kept_results(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        out = Response(survey_id=s.id, source="real", status=Response.SCREENOUT, pid="1-out", answers={})
        db.session.add(out)
        db.session.commit()
        assert erasure.counted(s, out)
        dummy = Response(survey_id=s.id, source="dummy", status=Response.ACTIVE, pid="1-d", answers={})
        assert not erasure.counted(s, dummy)  # test and dummy data: by today's pool, as before


# --- from tests/audits/test_audit_rest.py: Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones). ---


def test_an_answer_taken_back_does_not_end_the_participation(app, client, survey):
    """age -> [age < 18: why] -> [why = raus: END] -> last. 15, "raus", back, 30: the branch to the end hangs on an
    answer the person no longer has."""
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E__audit_rest + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E__audit_rest + "/blocks"), client.post(E__audit_rest + "/blocks")      # blocks 3, 4
    add(client, "text_input", block=3)                          # 3
    client.post(E__audit_rest + "/items/3", data={"code": "why", "question": "Warum?"})
    add(client, "text_input", block=4)                          # 4
    client.post(E__audit_rest + "/items/4", data={"code": "last", "question": "Sonst noch?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [{"t": "block", "block": 3}]},
            {"t": "branch", "id": "b2", "cond": {"item": 3, "op": "eq", "value": "raus"},
             "children": [{"t": "end", "id": "e1", "text": {"de": "Ende"}}]},
            {"t": "block", "block": 4}]}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    p.post("/s/1", data={"nav": "next", "i2": "15"})
    p.post("/s/1", data={"nav": "back", "i3": "raus"})           # typed, then back to the age
    p.post("/s/1", data={"nav": "next", "i2": "30"})
    assert "Sonst noch" in p.get("/s/1").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.status == Response.ACTIVE and "3" not in r.answers   # also gone for an unfinished participation
    q = app.test_client()                                        # the end still works where the answer stands
    q.get("/s/1")
    q.post("/s/1", data=CONSENT_OK)
    q.post("/s/1", data={"nav": "next", "i2": "15"})
    q.post("/s/1", data={"nav": "next", "i3": "raus"})
    with app.app_context():
        assert sorted(r.status for r in db.session.scalars(db.select(Response))) == [Response.ACTIVE, Response.SCREENOUT]


def test_a_rebuilt_layout_keeps_the_design_version_of_a_real_participation(app, client, survey, monkeypatch):
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        fresh = {"sig": "new", "pages": [], "cj": {"2": {"version": 0}, "9": {"version": 0}}}
        monkeypatch.setattr(runtime, "build_layout", lambda *a, **k: dict(fresh))
        monkeypatch.setattr(runtime, "_signature", lambda survey: "new")
        old = {"sig": "old", "pages": [], "cj": {"2": {"version": 5, "order": [1, 0]}, "7": {"version": 1}}}
        real = Response(survey_id=1, source="real", layout=dict(old), pid="1-a")
        dummy = Response(survey_id=1, source="dummy", layout=dict(old), pid="1-b")
        for r in (real, dummy):
            runtime.ensure_layout(s, r)
        assert real.layout["cj"] == {"2": {"version": 5, "order": [1, 0]}, "9": {"version": 0}} and real.position == 0
        assert dummy.layout["cj"]["2"] == {"version": 0}


def test_acbc_has_a_winner_only_after_a_judgement_on_every_concept(app, client, survey):
    from tests.analysis.test_acbc import run_participant, setup, start

    setup(client)
    uid = start(app, client)
    run_participant(app, uid, possible=())                       # all nine judged "not possible"
    with app.app_context():
        item = db.session.get(Item, 2)
        done = dict(db.session.scalar(db.select(Response).filter_by(source="real")).answers["2"])
        assert item.kind.winner(item, done)[1] == "byo" and item.kind.export_values(item, done)[f"{item.code}_n_possible"] == 0
        some = {**done, "s": {k: v for k, v in done["s"].items() if int(k) < 3}}       # stopped after the first screen
        left_open = {**done, "s": {k: None for k in done["s"]}}                         # optional and skipped
        for answer in (some, left_open):
            values = item.kind.export_values(item, answer)
            assert item.kind.winner(item, answer) == (None, None)
            assert values[f"{item.code}_win_source"] is None and values[f"{item.code}_n_possible"] is None


def test_spss_takes_text_and_ids_as_text_whatever_the_answers_are(app, client, survey):
    add(client, "text_input"), add(client, "text_input")
    client.post(E__audit_rest + "/items/2", data={"code": "code4", "question": "Code"})
    client.post(E__audit_rest + "/items/3", data={"code": "zahl", "question": "Zahl", "validation": "integer"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "tic", "mode": "store", "required": False, "allowed": []}], "redirects": {}}
        s.responses.append(Response(source="real", status=Response.COMPLETED, pid="1-x", params={"tic": "00123"},
                                    answers={"2": "0042", "3": 7}))
        db.session.commit()
        _, _, meta = export._spss_dictionary(s, False, ("real",), 50, True, None)
        assert meta["code4"]["kind"] == "str" and meta["url_tic"]["kind"] == "str" and meta["pid"]["kind"] == "str"
        assert meta["zahl"]["kind"] == "int"


def test_trial_answers_go_when_the_design_changes(app, client, survey):
    from tests.analysis.test_maxdiff import E as ME
    from tests.analysis.test_maxdiff import setup

    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for source in ("dummy", "test", "real"):
            s.responses.append(Response(source=source, status=Response.COMPLETED, pid=f"1-{source}",
                                        answers={"1": "1", "2": {"t0": {"b": 0, "w": 1}}},
                                        layout={"cj": {"2": {"version": 0}}}))
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.forget_trial_answers(item) == 2
        db.session.commit()
        left = {r.source: (sorted(r.answers), r.layout["cj"]) for r in db.session.scalars(db.select(Response))}
        assert left["real"] == (["1", "2"], {"2": {"version": 0}})          # real answers lock the design instead
        assert left["dummy"] == (["1"], {}) and left["test"] == (["1"], {})
        db.session.execute(db.delete(Response).where(Response.source == "real"))
        s.responses.append(Response(source="dummy", status=Response.COMPLETED, pid="1-d2",
                                    answers={"2": {"t0": {"b": 0, "w": 1}}}, layout={"cj": {"2": {"version": 0}}}))
        db.session.commit()
    page = client.post(ME + "/items/2/conjoint/design", follow_redirects=True).data.decode()
    assert "Antworten aus Testdurchläufen und Dummy-Daten zu dieser Frage wurden entfernt" in page
    with app.app_context():
        assert all("2" not in (r.answers or {}) for r in db.session.scalars(db.select(Response)))


# --- from tests/audits/test_audit_small.py: Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav. ---


def test_sav_leaves_long_json_empty_instead_of_cutting_it(app, client, survey, monkeypatch, tmp_path):
    import pyreadstat

    from app import sav as sav_module
    from tests.items.test_map_marker import setup

    setup(client, max="8")
    markers = [{"lat": 50.7 + n / 100, "lon": 6.08, "q": {"a1": "Begründung Nummer %d" % n, "b2": 3}} for n in range(8)]
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                    started_at=datetime(2026, 9, 2), answers={"2": markers}))
        db.session.commit()
        whole = export.rows(s)[0]["site_json"]
        assert len(whole) > 255 and json.loads(whole)
        # over 255 bytes the JSON is kept whole now (very long string, audit 2026-10-05 S33) ...
        (tmp_path / "a.sav").write_bytes(export.spss_sav(s))
        df, _ = pyreadstat.read_sav(str(tmp_path / "a.sav"))
        assert df["site_json"][0] == whole
        # ... and only over what a .sav holds left empty, never cut
        monkeypatch.setattr(sav_module, "MAX_LONG", 300)
        sav = export.spss_sav(s)
        assert b'[{"lat"' not in sav and "vollständig in der CSV".encode() in sav
        assert "Begründung Nummer 3".encode() in sav               # the per-marker columns are complete


# --- from tests/audits/test_audit_small3.py: Small things from the audit of 2026-10-01 (package 7): patterns that keep a server busy, a content policy, ---


@pytest.mark.parametrize("pattern", ["(a|a)*b", "(a|aa)+b", "(a{1,99}){1,99}b", "a*a*a*a*a*a*b", "(.*a){12}b", "(a+)+b",
                                     r"(\d|\d\d)+x"])
def test_patterns_that_backtrack_are_found_by_trying_them(pattern):
    """The structural check knows one shape; these take seconds to minutes on 40 characters."""
    from app.items.text import risky_pattern

    risky_pattern.cache_clear()
    started = time.process_time()
    assert risky_pattern(pattern)
    assert time.process_time() - started < 5  # found on short probes, long before a match takes minutes


def test_backtracking_pattern_is_reported_and_not_applied(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E__audit_small3 + "/items/2", data={"code": "code", "question": "Code", "validation": "regex", "pattern": "(a|a)*b"})
    assert "(a|a)*b" in client.get(E__audit_small3 + "/items/2").data.decode()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        s = db.session.get(Survey, 1)
        if s.status != "running":  # the pre-launch check stops the start; the answer path must be safe either way
            s.status = "running"
            db.session.commit()
        url = f"/s/{s.code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    started = time.process_time()
    r = p.post(url, data={"nav": "next", "i2": "a" * 60})
    # with RE2 (audit 2026-10-13, KR-1) the pattern is applied in linear time: the answer does not fit, the page stays
    assert r.status_code in (200, 302) and time.process_time() - started < 5


def test_assign_answer_returns_to_its_field(app, client, survey):
    """`tojson` alone inside value="…" ended the attribute at the first quote: the stored answer never came back,
    and a category code with spaces became attributes of the field."""
    fill_consent(client)
    add(client, "assign")
    client.post(E__audit_small3 + "/items/2", data=MultiDict([("code", "sort"), ("question", "Gut?"), ("answer_mode", "optional"), ("keys", "1"),
                ("cat_L_label", "gut"), ("cat_L_code", 'x" onclick="alert(1)'), ("cat_R_label", "schlecht"), ("cat_R_code", "2"),
                ("el_id", "a"), ("el_code", "robot"), ("el_label", "Roboter"), ("el_id", "b"), ("el_code", "nurse"), ("el_label", "Pflege")]))
    add(client, "page_break")
    add(client, "info")
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": '[{"s": "a", "c": "2", "rt": 300}, {"s": "b", "c": "x\\" onclick=\\"alert(1)", "rt": 400}]'})
    html = p.post(url, data={"nav": "back"}, follow_redirects=True).data.decode()
    field = re.search(r'<input type="hidden" name="i2" value="([^"]*)" data-assign-answer>', html)
    assert field is not None, "the value stays inside its attribute"
    assert "&#34;s&#34;" in field.group(1) and "onclick=" in field.group(1)  # as text of the value, escaped
    assert ' onclick="alert(1)' not in html


def test_palette_escapes_quotes(app):
    import os

    with open(os.path.join(app.static_folder, "app.js"), encoding="utf-8") as f:
        js = f.read()
    start = js.index("function esc(s) {")  # one esc() for the whole of app.js, the palette's among its users
    body = js[start:start + 400]
    assert "&quot;" in body and "&#39;" in body and "&amp;" in body


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_g1_each_form_keeps_the_other_ones_answers(app, client):
    from app.models import Response

    code, rid, age, post = _interview(app, client)
    url = f"/guide/{code}/interviews/{rid}/form"
    assert client.post(url, data={"part": "short", f"i{age}": "57"}).json["ok"]
    assert client.post(url, data={"part": "post", f"i{post}": "Gespräch war ruhig"}).json["ok"]
    assert client.post(url, data={"part": "short", f"i{age}": "58"}).json["ok"]
    with app.app_context():
        answers = db.session.get(Response, rid).answers
    assert str(answers[str(age)]) == "58" and answers[str(post)] == "Gespräch war ruhig"
    client.post(url, data={"part": "post", f"i{post}": ""})  # emptied in its own form: gone
    with app.app_context():
        answers = db.session.get(Response, rid).answers
    assert str(post) not in answers and str(answers[str(age)]) == "58"


def test_f21_conduct_page_loads_participant_js_and_names_the_parts(app, client):
    code, rid, *_ = _interview(app, client)
    page = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert "participant.js" in page
    assert 'name="part" value="short"' in page and 'name="part" value="post"' in page


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


def test_delete_does_not_load_participations(app, client, survey):
    _taken(app, client, n=4)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(OnceValue)) >= 0
        s = db.session.get(Survey, 1)
        db.session.scalars(db.select(Response).filter_by(survey_id=1)).all()  # some already in the session
        with Statements() as sql:
            lifecycle.delete(s, force=True)
            db.session.commit()
        assert not [q for q in sql.seen if q.startswith("SELECT") and "response.answers" in q]
        assert db.session.scalar(db.select(db.func.count()).select_from(Response)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(OnceValue)) == 0
        assert db.session.get(Survey, 1) is None


def test_sweep_params_reads_only_rows_that_hold_them(app, client, survey):
    from app import external

    _taken(app, client, n=2)
    old = utcnow() - timedelta(seconds=external.MAX_SECONDS + 60)
    with app.app_context():
        a, b = db.session.scalars(db.select(Response).filter_by(survey_id=1).order_by(Response.id)).all()
        a.external, a.outside_since = {"n1": {"k": "x"}, external.PARAMS: {"pid": "7"}}, old
        b.external, b.outside_since = {"n1": {"k": "y"}}, old  # back from nowhere, nothing held any more
        db.session.commit()
        versions = {a.id: a.version, b.id: b.version}
        with Statements() as sql:
            assert external.sweep_params(utcnow()) == 1
        db.session.commit()
        reads = [q for q in sql.seen if q.startswith("SELECT")]
        assert reads and all("response.answers" not in q for q in reads)
        db.session.expire_all()
        a, b = db.session.get(Response, a.id), db.session.get(Response, b.id)
        assert a.external == {"n1": {"k": "x"}} and a.version == versions[a.id] + 1
        assert b.version == versions[b.id]
        assert external.sweep_params(utcnow()) == 0


def test_outside_index_exists(app):
    from sqlalchemy import inspect

    with app.app_context():
        names = {i["name"] for i in inspect(db.engine).get_indexes("response")}
    assert "ix_response_outside" in names


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_declines_are_counted_by_the_database_and_leave_last_changed(app, client, survey):
    from tests.participation.test_participate import build, uid

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    with app.app_context():
        changed = db.session.get(Survey, 1).updated_at
        engine = db.engine
    statements = []

    def note(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).lower())

    event.listen(engine, "before_cursor_execute", note)
    try:
        for _ in range(2):
            d = app.test_client()
            d.get(url)
            d.post(url, data={"i1": "0"})
    finally:
        event.remove(engine, "before_cursor_execute", note)
    # n + 1 in the statement itself (two declines that overlap both wrote "n + 1" before), and "last changed" stays
    counting = [x for x in statements if x.startswith("update survey set") and "declined_count" in x]
    assert len(counting) == 2 and all("survey.declined_count" in x and "updated_at=survey.updated_at" in x.replace(" ", "")
                                      for x in counting)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.declined_count == 2 and s.updated_at == changed


def test_last_page_of_the_separated_section_sent_again_with_the_old_cookie(app, client, survey):
    from tests.quotas.test_split import build

    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    html = p.get(f"/s/{uid}").data.decode()
    token = html.split('name="pos" value="')[1].split('"')[0]
    assert token.startswith("x") and token.endswith("-0") and len(token) > 3       # with the start of the session's id
    name = app.config["SESSION_COOKIE_NAME"]
    before = p.get_cookie(name).value
    form = {"nav": "next", "pos": token, "i3": "zora@x.de", "i4": "hallo"}
    p.post(f"/s/{uid}", data=form)                                                  # stored; the answer is lost
    assert _entries(app) == 1
    again = app.test_client()
    again.set_cookie(name, before)
    r = again.post(f"/s/{uid}", data=form)                                          # sent again with the old cookie
    assert r.status_code == 302 and _entries(app) == 1                              # was 2: two tickets
    html = again.get(f"/s/{uid}").data.decode()                                     # the page itself starts anew
    assert 'name="i3"' in html and html.split('name="pos" value="')[1].split('"')[0] != token
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SplitSession).filter_by(status="active")) == 1


def test_a_box_written_while_the_ai_answers_stays(app, client, monkeypatch):
    code = _new_project(app, client, monkeypatch)

    def during(purpose):  # another tab (or someone with "edit") saves a box and a message while the AI thinks
        with db.engine.begin() as con:
            row = con.execute(ResearchProject.__table__.select()).first()
            canvas = {**(row.canvas or {}), "design": {"text": "Experiment, zwischen Personen", "proposal": ""}}
            chat = list(row.chat or []) + [{"role": "user", "text": "Aus dem anderen Tab", "at": "x"}]
            con.execute(ResearchProject.__table__.update().values(canvas=canvas, chat=chat))
        return json.dumps({**TURN, "canvas": {**TURN["canvas"], "frage": "Neuer Vorschlag der KI"}})

    _fake__cr8_c(monkeypatch, during)
    r = client.post(f"/ainstein/{code}/chat", data={"message": "Weiter"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    with app.app_context():
        p = db.session.scalar(db.select(ResearchProject))
        assert p.canvas["design"]["text"] == "Experiment, zwischen Personen"           # was empty again (admin, no limit)
        assert p.canvas["frage"]["text"] == "Neuer Vorschlag der KI"
        assert [m["text"] for m in p.chat][-3:] == ["Weiter", "Aus dem anderen Tab", "Gut."]


def test_sending_again_after_a_failed_answer_stores_the_message_once(app, client, monkeypatch):
    code = _new_project(app, client, monkeypatch)

    def broken(*a, **k):
        raise ai.AIError("Der KI-Dienst ist gerade nicht erreichbar.")

    monkeypatch.setattr(ai, "generate_text", broken)
    r = client.post(f"/ainstein/{code}/chat", data={"message": "Hallo"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 502
    _fake__cr8_c(monkeypatch, json.dumps(TURN))
    client.post(f"/ainstein/{code}/chat", data={"message": "Hallo"}, headers={"X-Requested-With": "fetch"})
    with app.app_context():
        chat = db.session.scalar(db.select(ResearchProject)).chat
        assert [m["text"] for m in chat if m["role"] == "user"] == ["Hallo"]


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_r3_cold_results_page_with_hypotheses_reads_the_pool_once(app, client, survey, monkeypatch):
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 1)] * 8 + [((1,), "2", 5, 5)] * 8)
    _hypothesis(app)
    calls = _count_rows(monkeypatch)
    page = client.get("/surveys/1/results")
    assert page.status_code == 200 and "Nutzen wirkt" in page.get_data(as_text=True)
    assert len(calls) == 1
    calls.clear()
    assert client.get("/surveys/1/results").status_code == 200
    assert calls == []  # all kept


def test_t4_a_start_without_balanced_draws_reads_no_counts(app, client, survey, monkeypatch):
    from app import balance

    url = _running__cr8_e(app, client)
    read = []
    real = balance._current
    monkeypatch.setattr(balance, "_current", lambda s: (read.append(1), real(s))[1])
    p = app.test_client()
    assert p.get(url).status_code == 200
    assert p.post(url, data=CONSENT_OK).status_code in (200, 302)
    assert read == []


def test_t4_counts_read_only_their_parts_and_match_the_layouts(app, client, survey):
    from app import balance
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 1)] * 3 + [((1,), "2", 5, 5)] * 5)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for n, r in enumerate(s.responses):
            r.layout = {**r.layout, "pages": [[1, 2, 3]] * 50, "loops": {"l1": ["a", "b"][: n % 3]},
                        "cj": {"7": {"version": n % 2, "order": ["x"] * 20}}}
        db.session.commit()
        statements = []

        def note(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", note)
        try:
            found = balance.counts(s)
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        want = {"rand": {}, "loop": {}, "version": {}}
        for r in s.responses:
            balance._add(want, r.layout)
        assert found == want and found["rand"]["r1"] and found["version"]["7"] == {0: 4, 1: 4}
        query = next(q for q in statements if "from response" in q.lower())
        assert "pages" not in query and "layout" in query.lower()


def test_t4_a_declined_participation_does_not_drop_the_kept_tables(app, client, survey, monkeypatch):
    from app import balance

    url = _running__cr8_e(app, client)
    app.config["BALANCE_SECONDS"] = 15
    dropped = []
    monkeypatch.setattr(balance, "forget", lambda sid: dropped.append(sid))
    d = app.test_client()
    d.get(url)
    d.post(url, data={"i1": "0"})
    assert "nicht teilnehmen möchten" in d.get(url).get_data(as_text=True)
    assert dropped == []
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 1


def test_t5_marked_kept_while_the_pool_is_the_same(app, client, survey, monkeypatch):
    from app import balance, export
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 1)] * 4)
    app.config["BALANCE_SECONDS"] = 15
    calls = []
    real = export.marked_ids
    monkeypatch.setattr(export, "marked_ids", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    with app.app_context():
        s = db.session.get(Survey, 1)
        balance.marked(s)
        balance.marked(s)
        assert len(calls) == 1
        store = balance._store()
        key = ("marked", s.id, ("real",))
        at, value = store[key]
        store[key] = (at - 3600, value)  # long after the old time limit: still the same pool
        balance.marked(s)
        assert len(calls) == 1
        r = s.responses[0]
        r.answers = {**r.answers, "2": "2"}  # a stored participation changes: its row version rises
        db.session.commit()
        balance.marked(s)
        assert len(calls) == 2
        s.responses.append(Response(status=Response.COMPLETED, answers={"2": "1"}))
        db.session.commit()
        balance.marked(s)
        assert len(calls) == 3
        app.config["BALANCE_SECONDS"] = 0
        balance.marked(s)
        assert len(calls) == 4  # 0: always fresh


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f1_the_pool_paragraph_chip_is_on_the_settings_page(app, client, survey):
    page = client.get("/surveys/1").get_data(as_text=True)
    if "data-copy-text" in page:                                           # only with the pool tick; then a button chip
        assert re.search(r'role="button"[^>]*data-copy-text', page)


def test_e7_field_page_form_has_the_same_key(app, client, survey):
    from flask import url_for
    with app.test_request_context():
        url = url_for("surveys.quality", survey_id=1)
    page = client.get(url).get_data(as_text=True)
    assert 'data-guard-key="settings:meta"' in page


def test_f17_polls_drop_older_answers():
    src = _js("live.js")
    assert "if (mine !== ticked) return;" in src                           # the phone
    assert "if (mine === asked) { draw(s);" in src                         # the screen (then fit, 2026-10-12)
    assert "tick(false);" in src and "ask(true);" in src


def test_g5_ainstein_without_send_form():
    src = _js("ainstein.js")
    guard = src.index("if (!sendForm || !field) return;")
    assert guard < src.index('sendForm.addEventListener("submit"')


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i2_question_line_per_language(app, client):
    from app import ai_scales

    client.post("/surveys/new", data={"title": "Du-Befragung", "languages": ["de-du"], "default_language": "de-du"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Du-Befragung"))
        item = ai_scales.likert_item(s, "Technikfreude", [{"reverse": False, "text": {"de-du": "Ich mag Technik."}}], "KI1")
        assert item.texts["de-du"]["question"] == "Inwieweit stimmst du den folgenden Aussagen zu?"
        s.languages = ["de", "en", "fr"]
        item = ai_scales.likert_item(s, "T", [{"reverse": False, "text": {"de": "a", "en": "b", "fr": "c"}}], "KI2")
        assert item.texts["de"]["question"].startswith("Inwieweit stimmen Sie")
        assert item.texts["en"]["question"].startswith("To what extent")
        assert item.texts["fr"]["question"] == ""  # missing on the page "Übersetzung", not English counted as French
        db.session.rollback()


def test_i6_translation_keeps_a_raw_line_break(app, client, survey, monkeypatch):
    _fake__cr8_h(monkeypatch, '{"t1": "Zeile eins\nZeile zwei", "t2": "x", "t3": "y"}')
    r = client.post("/surveys/1/translation/ai?lang=en")
    assert r.status_code == 200, r.get_json()


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_e12_fingerprint_follows_answers_of_a_running_participation(app, client, survey):
    from app import linked_pool

    _linked_question(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = Response(survey=s, source="real", answers={})
        db.session.add(r)
        db.session.commit()
        c = linked.element(db.session.get(Item, 2))
        before = linked_pool._fingerprint(c, linked.uses(c))
        r.answers = {"2": "1"}
        db.session.commit()
        assert linked_pool._fingerprint(c, linked.uses(c)) != before


def test_t6_cookie_keeps_only_the_newest_waiting_links(app, client):
    from app import participate

    for n in range(25):
        client.post("/surveys/new", data={"title": f"T{n}", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        for s in db.session.scalars(db.select(Survey)):
            s.status = Survey.RUNNING
            s.panel = {"params": [{"name": "src", "mode": "store"}]}
        db.session.commit()
        codes = [s.code for s in db.session.scalars(db.select(Survey).order_by(Survey.id))]
    p = app.test_client()
    for code in codes:
        assert p.get(f"/s/{code}?src=seminar-raum-2").status_code == 200
    with p.session_transaction() as s:
        waiting = [k for k in s if k[:1] == "n" and k[1:].isdigit()]
        params = [k for k in s if k[:2] == "pp" and k[2:].isdigit()]
    assert len(waiting) <= participate.KEEP_ENDED + 1 and len(params) <= participate.KEEP_ENDED + 1
    assert f"n{len(codes)}" in waiting                      # the newest stays


# --- from tests/audits/test_review2_reste.py: Second code review 2026-10-03, package 7 (the rest): no inline scripts and handlers, so the CSP allows scripts ---


def test_pages_that_had_inline_code_load_their_scripts(app, client, survey):
    html = client.get("/dashboard/").data.decode()
    assert "theme-init.js" in html and "<script>" not in html
    state = client.get(QUOTA_STATE)  # the quota state page: theme, copy (app.js) and print (participant.js) as files
    page = state.data.decode()
    assert state.status_code == 200 and "theme-init.js" in page and "app.js" in page and "participant.js" in page
    device = client.get(f"{E__review2_reste}/preview?device=mobile", follow_redirects=True).data.decode()
    assert "data-device-frame" in device and "participant.js" in device and "data-select-all" in device


def test_the_preview_bar_follows_the_researchers_language(app, client, survey):
    """The bar speaks the researcher's language whatever the survey's language (Ralf, 2026-10-07)."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        db.session.commit()
    add(client, "text_input")
    english = client.get(f"{E__review2_reste}/preview?lang=en", follow_redirects=True).data.decode()
    assert "Vorschau · Antworten zählen nicht" in english and "Preview · answers do not count" not in english
    assert 'data-preview-param="validate"' in english and "> Pflicht prüfen" in english
    phone = client.get(f"{E__review2_reste}/preview?lang=en&device=mobile", follow_redirects=True).data.decode()
    assert 'data-page="Seite {p}/{n}"' in phone and 'data-end="Ende"' in phone


# --- from tests/audits/test_review_fixes.py: What the code review of 2026-10-02 found and what holds since (code-review-2026-10-02.md, S1 to S7). ---


def test_translating_an_end_text_in_the_field_keeps_participants_on_their_page(app, client, survey):
    test_flow.setup_design(app, client)
    url = _start__review_fixes(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": "30"})
    before = _state(app)
    assert before["position"] >= 2
    saved = client.post("/surveys/1/translation/fieldwork", data={"lang": "en", "unit": "flow:e1", "path": "screenout",
                                                              "text": "Sorry, too young."})
    assert saved.status_code == 200
    assert "i1_c_voluntary" not in p.get(url).data.decode()  # not the consent again
    assert _state(app) == before


def test_correcting_a_loop_cell_in_the_field_keeps_participants_on_their_page(app, client, survey):
    url = test_loops.setup(client)
    link = _start__review_fixes(app, client)
    with app.app_context():
        loop = loops.config(db.session.get(Block, 2))
    p = app.test_client()
    p.get(link)
    p.post(link, data=CONSENT_OK)
    p.post(link, data={"nav": "next", "i2": "1"})
    before = _state(app)
    assert before["position"] >= 2
    row, column = loop["rows"][0], loop["columns"][0]
    # a translation of a cell (the default language is fixed while running, code review 2026-10-03 E__review_fixes-2)
    client.post(url + "?lang=en", data={f"cell_{row['id']}_{column['id']}": "Pflege-Roboter"})
    with app.app_context():
        assert "Pflege-Roboter" in str(db.session.get(Survey, 1).flow)
    p.get(link)
    assert _state(app) == before


def test_ended_participation_keeps_its_layout_and_language(app, client, survey):
    test_flow.setup_design(app, client)
    url = _start__review_fixes(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": "30"})
    p.post(url, data={"nav": "next"})
    p.post(url, data={"nav": "next"})
    before = _state(app)
    assert before["status"] == Response.COMPLETED
    with app.app_context():  # the questionnaire changed since (reopened for editing): another signature
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        r.layout = {**r.layout, "sig": "0" * 16}
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        db.session.commit()
    before = _state(app)
    assert "Vielen Dank" in p.get(url + "?lang=de").data.decode()
    p.get(url + "?lang=en")
    assert _state(app) == before


def test_separated_section_works_with_page_times(app, client, survey):
    with app.app_context():
        db.session.get(Survey, 1).page_timing = True
        db.session.commit()
    uid = test_split.build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    page = p.get(f"/s/{uid}")
    assert page.status_code == 200 and 'name="i3"' in page.data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i3": "zora@example.org", "i4": ""})
    assert p.get(f"/s/{uid}").status_code == 200
    with app.app_context():
        __import__("app.contact", fromlist=["bundle"]).bundle()  # PostgreSQL: finished sections wait (K11)
        assert [e.answers for e in db.session.scalars(db.select(ContactEntry))] == [{"3": "zora@example.org", "4": None}]
        assert db.session.scalar(db.select(SplitSession)) is None


def test_separated_section_never_becomes_the_whole_questionnaire(app, client, survey):
    uid = test_split.build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    assert 'name="i3"' in p.get(f"/s/{uid}").data.decode()
    with app.app_context():  # the questionnaire changed since: another signature
        split = db.session.scalar(db.select(SplitSession))
        split.layout = {**split.layout, "sig": "0" * 16}
        db.session.commit()
    html = p.get(f"/s/{uid}").data.decode()
    assert 'name="i3"' in html and "i1_c_voluntary" not in html
    with app.app_context():
        split = db.session.scalar(db.select(SplitSession))
        assert [page.get("block") for page in split.layout["pages"]] == [3]
    p.post(f"/s/{uid}", data={"nav": "next", "i3": "zora@example.org", "i4": ""})
    with app.app_context():
        __import__("app.contact", fromlist=["bundle"]).bundle()  # PostgreSQL: finished sections wait (K11)
        assert [e.answers for e in db.session.scalars(db.select(ContactEntry))] == [{"3": "zora@example.org", "4": None}]


def test_cloze_answers_are_free_text_for_publishing(app, client, survey):
    add(client, "cloze")                                                          # 2
    client.post(E__review_fixes + "/items/2", data={"code": "cl", "question": "Ergänzen Sie.", "text": "Mein Arbeitgeber ist [    ].",
                                      "answer_mode": "optional"})
    with app.app_context():
        now = utcnow()
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=now - timedelta(minutes=5),
                                completed_at=now, answers={"2": ["Firma Mustermann GmbH Aachen"]}))
        db.session.commit()
        s = db.session.get(Survey, 1)
        assert "cl" in export.open_columns(s)
        assert "Firma Mustermann" not in export.to_csv(s, public=True, open_answers=False)
        assert "Firma Mustermann" in export.to_csv(s, public=True, open_answers=True)
        from app import zenodo

        files = dict(zenodo.package(s, open_answers=False))
        assert "Firma Mustermann" not in files["data.csv"].decode()


def test_preview_shows_the_completion_link_without_its_signature(app, client, survey):
    """M1: a test run takes any panel ID from its address; a signed link from it would count at the panel."""
    from app import panel, secrets_box

    fill_consent(client)
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "PID", "mode": "store"}],
                   "redirects": {"completed": "https://panel.example/done?RID={{PID}}&hash={{sig}}"},
                   "sign": {"alg": "sha256", "enc": "hex", "base": "amp", "key": secrets_box.encrypt("geheim")}}
        db.session.commit()
        signed, _ = panel.target(s, "completed", {"PID": "VICTIM-4711"})
        assert "hash=" in signed and panel.NO_SIGNATURE not in signed
        proof = signed.split("hash=")[1]
        assert len(proof) == 64
    client.get("/surveys/1/editor/preview?restart=1&PID=VICTIM-4711&validate=0")
    client.post("/surveys/1/editor/preview", data={"nav": "next"})
    page = client.post("/surveys/1/editor/preview", data={"nav": "next"}, follow_redirects=True).data.decode()
    assert "RID=VICTIM-4711" in page and panel.NO_SIGNATURE in page
    assert proof not in page


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_for_viewers_reduces_every_kind_of_single_answer():
    from app import results

    data = {"items": [
        {"columns": [{"texts": [("mein Text", 1)]}], "terms": [("oft", 6, 0.5), ("selten", 1, None)],
         "cloze": {"gaps": [{"answers": [("oft", 5), ("selten", 4)]}]},
         "postal": {"n": 7, "counts": {"52062": 6, "52064": 1}, "places": {"52062": "Aachen", "52064": "Aachen"},
                    "top": [{"value": "52062", "count": 6}, {"value": "52064", "count": 1}]},
         "dates": {"n": 7, "step": "Tag", "min": "01.02.1990", "max": "03.02.1990", "median": "02.02.1990",
                   "rows": [{"label": "01.02.1990", "count": 1}, {"label": "02.02.1990", "count": 6}]},
         "map": {"points": [{"lat": 1, "lon": 2, "k": 1, "text": "bei mir zu Hause"}]}}]}
    entry = results.for_viewers(data)["items"][0]
    assert "texts" not in entry["columns"][0] and entry["terms"] == [("oft", 6, 0.5)]
    assert entry["cloze"]["gaps"][0]["answers"] == [("oft", 5)]
    assert entry["postal"]["counts"] == {"52062": 6} and entry["postal"]["places"] == {"52062": "Aachen"}
    assert [r["value"] for r in entry["postal"]["top"]] == ["52062"]
    assert entry["dates"]["min"] is None and entry["dates"]["max"] is None and [r["count"] for r in entry["dates"]["rows"]] == [6]
    assert entry["map"]["points"] == [] and entry["few_hidden"]


def test_last_page_sent_twice_counts_one_completion(app, client, survey):
    test_quotas.build(app, client)
    uid = test_quotas.start(app, client)
    p = test_quotas.screen(app, uid, gender="1")
    p.post(f"/s/{uid}", data={"nav": "next"})  # on the last page
    _overlap(app, lambda r: MultiDict({"nav": "next", "i5": "1", "pos": runtime.page_token(r, r.position)}))
    assert test_quotas.counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}
    assert test_quotas.statuses(app) == [Response.COMPLETED]


def test_stale_request_of_a_participant_shows_the_current_page(app, client, survey):
    from app import participate

    test_participate.build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{test_participate.uid(app)}"
    with app.test_request_context(url, method="POST"):
        answer = participate._sent_twice(StaleDataError())
    assert answer.status_code == 303 and answer.headers["Location"].endswith(url)


def test_link_parameters_stay_when_the_link_is_opened_again_without_them(app, client, survey):
    test_participate.build(client)
    client.post("/surveys/1/panel", data=MultiDict([("param_name", "kurs"), ("param_mode", "store"),
                                                    ("param_required", "1"), ("param_allowed", "")]))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{test_participate.uid(app)}"
    p = app.test_client()
    assert p.get(url + "?kurs=bio").status_code == 200
    p.post(url, data={"i1": "0"})                      # declined by mistake
    again = p.post(url + "/after-all")
    assert p.get(again.headers["Location"]).status_code == 200   # not "Dieser Link ist unvollständig"
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(source="real")).params == {"kurs": "bio"}
    assert app.test_client().get(url).status_code == 400            # another browser, without the parameter


@pytest.mark.parametrize("name, value", [("question", 5), ("question", ["a"]), ("question", None), ("options", "text"),
                                          ("options", {"a": 5}), ("options", {"a": {"b": {"c": {"d": "x"}}}}),
                                          ("eigenes", [1, 2])])
def test_import_refuses_texts_that_are_no_texts(app, client, survey, name, value):
    add(client, "single_choice")
    client.post(E__review_medium + "/items/2", data=MultiDict([("code", "wahl"), ("question", "Was?"), ("option_id", "a"),
                                                ("option_code", "1"), ("option_label", "ja")]))
    data = _file__review_medium(client)
    row = next(i for b in data["blocks"] for i in b["items"] if i["code"] == "wahl")
    row["texts"]["de"][name] = value
    page = _upload__review_medium(client, data)
    assert "lässt sich nicht einlesen" in page and "hat nicht die Form eines Textes" in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1


# --- from tests/audits/test_s9_one_source.py: What participants see comes from one source (UI/UX audit 2026-10-07, S9): the preview renders the templates of the ---


def test_the_preview_renders_the_pages_of_the_participation(app, client, survey):
    """No second version of the pages: the preview renders page.html, end.html and external.html and adds its bar,
    the comments and the summary of the test run."""
    left = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "app/templates/participant/preview*.html")))
    assert left == ["preview_device.html"]  # the frame of the phone view around the page, not a page of its own
    editor = open(os.path.join(ROOT, "app/editor/preview.py"), encoding="utf-8").read()
    for name in ("page", "end", "external"):
        assert f'"participant/{name}.html"' in editor
    add(client, "text_input")
    client.post(f"{E__s9_one_source}/items/2", data={"code": "age", "question": "Wie alt sind Sie?"})
    _set(app, paradata=True, show_progress=False)
    page = client.get(f"{E__s9_one_source}/preview?lang=de&page=2&device=desktop", follow_redirects=True).data.decode()
    assert 'class="p-preview-bar"' in page and 'data-page="2"' in page  # the bar and the pager of the phone view
    assert "data-paradata" in page and 'name="page_lang"' in page  # the form of the participation itself
    assert 'class="p-progress"' not in page  # "Fortschritt anzeigen" off: none in the preview either
    assert "<title>Seite 2 – Test</title>" in page


def test_the_end_of_a_test_run_is_the_end_page_with_a_summary(app, client, survey):
    fill_consent(client)
    add(client, "info")
    client.post(f"{E__s9_one_source}/items/2", data={"code": "hi", "question": "Hallo"})
    client.get(f"{E__s9_one_source}/preview?restart=1")
    client.post(f"{E__s9_one_source}/preview?lang=de", data=CONSENT_OK)
    client.post(f"{E__s9_one_source}/preview?lang=de", data={"nav": "next"})
    page = client.get(f"{E__s9_one_source}/preview?lang=de").data.decode()
    assert '<h1 class="p-sr-only">Ende der Befragung</h1>' in page  # end.html
    assert "Vorschau beendet" in page and "Neuer Testdurchlauf" in page and 'data-page="end"' in page


def test_standard_wordings_are_not_stored_and_come_in_each_language(app, client, survey):
    add(client, "federal_state")  # 2
    add(client, "conjoint")  # 3
    with app.app_context():
        states, consent, cbc = (db.session.get(Item, n) for n in (2, 1, 3))
        assert states.texts == {"de": {}, "en": {}} and "agree" not in consent.texts["de"]
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en", "ar"]  # switched on later: nothing is copied, nothing is missing
        db.session.commit()
        assert states.t("ar")["options"]["s9"] == "بافاريا" and states.t("de")["options"]["s9"] == "Bayern"
        assert states.t("ar")["options"]["abroad"] != "Ich wohne nicht in Deutschland"
        assert consent.t("ar")["agree"] == "التالي" and consent.t("en")["agree"] == "Next"
        assert cbc.t("ar")["none_label"] != "Keine davon" and cbc.t("ar")["alt_label"] != "Option"
        assert states.text_lang("options", "ar", "s9") == "ar"
    # the editor saves only what was changed
    client.post(f"{E__s9_one_source}/items/1?lang=de", data={"code": "consent", "question": "Einwilligungserklärung", "body": "Text",
                                              "agree": "Weiter", "disagree": "Lieber nicht", "agree_note": ""})
    with app.app_context():
        consent = db.session.get(Item, 1)
        stored = consent.texts["de"]
        assert "agree" not in stored and "question" not in stored and stored["disagree"] == "Lieber nicht"
        assert stored["agree_note"] == ""  # emptied on purpose: stays empty
        assert consent.t("ar")["disagree"] == "Lieber nicht" and consent.text_lang("disagree", "ar") == "de"
        assert consent.t("ar")["agree"] == "التالي" and consent.text_lang("agree", "ar") == "ar"
    # the editor of another language shows its standard wording, not a fallback of the default language
    page = client.get(f"{E__s9_one_source}/items/1?lang=ar").data.decode()
    assert "التالي" in page


def test_strip_keeps_own_texts_and_translations(app):
    from app.items import get

    kind = get("consent")
    texts = {"de": {"agree": "Fortsetzen", "disagree": "Ich möchte nicht teilnehmen", "question": "Meine Studie"},
             "en": {"agree": "Continue", "disagree": "I do not want to take part", "question": "My study"}}
    out = item_texts.strip(kind, texts, "de")
    assert out == {"de": {"question": "Meine Studie"}, "en": {"question": "My study"}}
    # a translation that happens to be the standard wording stays where the default language has its own text
    own = {"de": {"agree": "Los"}, "en": {"agree": "Next"}}
    assert item_texts.strip(kind, own, "de") == own
    # typed in the editor, an earlier standard wording is an own word
    assert item_texts.strip(kind, {"de": {"agree": "Fortsetzen"}}, "de", earlier=False) == {"de": {"agree": "Fortsetzen"}}


def test_translation_page_names_standard_wordings(app, client, survey):
    add(client, "federal_state")  # 2
    client.post(f"{E__s9_one_source}/items/2?lang=de", data={"code": "BL", "question": "Wo wohnen Sie?"})
    page = client.get("/surveys/1/translation/?lang=en").data.decode()
    assert "Vorgabe (wird je Sprache gesetzt)" in page and "Bavaria" in page
    assert re.search(r'<textarea[^>]*data-unit="item:2" data-path="question"', page)  # the own question is translated
    assert not re.search(r'data-path="options\.s9"', page)  # the states are not
    with app.app_context():
        from app import translations

        _, counts = translations.overview(db.session.get(Survey, 1), "en", write=False)
        assert counts["default"] >= 16 and counts["missing"] == 2  # only the question and the title of the survey


def test_survey_file_without_copies_and_older_files_dropped(app, client, survey):
    add(client, "federal_state")  # 2
    client.post(f"{E__s9_one_source}/items/2?lang=de", data={"code": "BL", "question": "Wo wohnen Sie?"})
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert data["version"] == 6
    row = next(r for b in data["blocks"] for r in b["items"] if r["code"] == "BL")
    assert row["texts"]["de"] == {"question": "Wo wohnen Sie?"}
    data["version"] = 4  # a file of before: with copies of the standard wordings
    row["texts"]["de"]["options"] = items.get("federal_state").default_texts("de")["options"]
    data["survey"]["title"] = "Aus Datei"
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "b.json")},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    client.post("/surveys/new/import", data={"token": token, "title-0": ""}, follow_redirects=True)
    with app.app_context():
        new = db.session.get(Survey, 2)
        bl = next(i for b in new.blocks for i in b.items if i.code == "BL")
        assert bl.texts["de"] == {"question": "Wo wohnen Sie?"} and bl.t("de")["options"]["s9"] == "Bayern"


def test_fallbacks_carry_their_language_and_the_preview_marks_them(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")  # 2
    client.post(f"{E__s9_one_source}/items/2?lang=de", data={"code": "f", "question": "Mögen Sie Tee?", "option_id": ["", ""],
                                              "option_code": ["1", "2"], "option_label": ["Ja, gern", "Nein, danke"]})
    _set(app, languages=["de", "en", "ar"])
    client.get(f"{E__s9_one_source}/preview?lang=ar&page=2&device=desktop")
    page = client.get(f"{E__s9_one_source}/preview?lang=ar").data.decode()
    marked = 'lang="de" dir="auto" data-untranslated="noch nicht übersetzt"'
    assert f"{marked}>Ja, gern</span>" in page  # an option
    # the preview's own controls in the researcher's language, not the survey's (Ralf, 2026-10-07)
    assert "Unübersetzte markieren" in page and "تمييز غير المترجم" not in page
    assert '<nav class="p-preview-bar" lang="de" dir="ltr"' in page
    # a test person on the phone link has no account: everything in the survey's language
    stage = client.get(f"{E__s9_one_source}/preview?lang=ar&device=mobile").data.decode()
    assert "Auf dem eigenen Smartphone testen" in stage and "Querformat" in stage
    path = "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', stage).group(1).split("://", 1)[1].split("/", 1)[1]
    phone = app.test_client().get(path + "?lang=ar&page=2", follow_redirects=True).data.decode()
    assert "تمييز غير المترجم" in phone and 'data-untranslated="لم تُترجم بعد"' in phone
    client.get(f"{E__s9_one_source}/preview?device=desktop")
    client.get(f"{E__s9_one_source}/preview?lang=ar&untranslated=0")
    page = client.get(f"{E__s9_one_source}/preview?lang=ar").data.decode()
    assert 'lang="de" dir="auto">Ja, gern</span>' in page and "data-untranslated=" not in page
    # participants: the language, never the mark
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    client.get(f"/s/{code}?lang=ar")
    client.post(f"/s/{code}", data={**CONSENT_OK})
    page = client.get(f"/s/{code}").data.decode()
    assert 'lang="de" dir="auto">Ja, gern</span>' in page and "data-untranslated" not in page


def test_every_element_type_on_an_arabic_page(app, client, survey):
    kinds, pages = _all_pages_in_arabic(app, client)
    assert len(kinds) > 30
    ltr = _ltr_classes()
    ranges, text, said = 0, [], []
    for html in pages:
        assert '<html lang="ar" dir="rtl"' in html
        parser = _Ranges()
        parser.feed(html)
        for classes in parser.found:  # (a) a range input runs left to right: it or a frame around it is held ltr
            ranges += 1
            assert any(c in ltr for cs in classes for c in cs), classes
        text.append(html_mod.unescape(" ".join(parser.text)))
        said += parser.said
    # (d) every text of an element that falls back to German carries lang="de", also in the newer types (X41)
    for word in OWN_GERMAN:
        where = [lang for t, lang in said if html_mod.unescape(t) == word]
        assert where and all(lang == "de" for lang in where), (word, where)
    assert ranges >= 2  # the slider and the rating as a slider at least
    shown = " ".join(" ".join(text).split())
    # (c) no standard wording of the default language where the catalog has the Arabic one
    with app.app_context():
        for key in kinds:
            kind = items.get(key)
            de, ar = item_texts.defaults(kind, "de"), item_texts.defaults(kind, "ar")
            from app.translations import flatten, get_path

            for path, word in flatten(de):
                if len(word) >= 5 and get_path(ar, path) not in (None, word) and "{" not in word:
                    assert " ".join(word.split()) not in shown, (key, path, word)
