"""Interview: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_a import OTHER, Response, Survey, _member, _person, db, os  # noqa: F401
from tests.helpers.audit_cr8_c import STATIC  # noqa: F401
from tests.helpers.audit_cr8_g import _js  # noqa: F401
from tests.helpers.audit_cr8_h import NODE, _fake, ai, date, datetime, external  # noqa: F401

WEBM = b"\x1a\x45\xdf\xa3"  # the first bytes of a WebM recording (app/items/voice.py sniff)


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_m8_personal_code_ligature_is_no_letter():
    from app.items import personal_code

    assert personal_code.letter("ﬁ") is None
    assert personal_code.letter("ß") == "S" and personal_code.letter("é") == "E"
    src = open(personal_code.__file__, encoding="utf-8").read()
    assert "(X im Code)" not in src and "(? im Code)" in src


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s22_deleting_an_interview_recording_needs_admin(app, client):
    from app import interview_audio
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    rid = start(app, client, code)
    client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=1", data=WEBM + b"AAAA", content_type="audio/webm")
    _person(app, OTHER, "Editor")
    _member(app, sid, OTHER, "editor")
    editor = _person(app, OTHER, "Editor")
    assert "audio/delete" not in editor.get(f"/guide/{code}/interviews").data.decode()
    editor.post(f"/guide/{code}/interviews/{rid}/audio/delete")
    with app.app_context():
        survey, row = db.session.get(Survey, sid), db.session.get(Response, rid)
        assert row.interview["audio"] and os.path.isdir(interview_audio.folder(survey, row))
    client.post(f"/guide/{code}/interviews/{rid}/audio/delete")
    with app.app_context():
        assert not db.session.get(Response, rid).interview["audio"]


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


def test_terms_version_kept_until_file_changes(app, tmp_path, monkeypatch):
    from app import terms

    path = tmp_path / "licence.md"
    path.write_text("# Bedingungen\n\nVersion: 2026-01-01\n", encoding="utf-8")
    monkeypatch.setattr(terms, "_path", lambda: str(path))
    reads = []
    real = terms.source
    monkeypatch.setattr(terms, "source", lambda: reads.append(1) or real())
    with app.app_context():
        assert terms.version() == "2026-01-01" and terms.version() == "2026-01-01"
        assert len(reads) == 1
        path.write_text("# Bedingungen\n\nVersion: 2026-10-07b\n", encoding="utf-8")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        assert terms.version() == "2026-10-07b" and len(reads) == 2


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_scripts_send_one_request_at_a_time():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    quiz = app_js[app_js.index('querySelectorAll("[data-live-quiz]")'):][:2500]
    assert "if (saving) { again = true; return; }" in quiz and "if (again) { again = false; send(); }" in quiz
    ainstein = (STATIC / "ainstein.js").read_text(encoding="utf-8")
    assert "if (!text || sending) return;" in ainstein and "sending = false;" in ainstein


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_i10_word_counts_kept_and_transcripts_read_once(app, client, monkeypatch):
    from app import interview_search
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    ids = [start(app, client, code, label=f"I{n}") for n in range(3)]
    with app.app_context():
        for n, rid in enumerate(ids):
            row = db.session.get(Response, rid)
            text = f"B: Pflege und Mutter Nummer{n} #00:00:0{n}-0#\n\nI: Und die Arbeit? #00:00:1{n}-0#"
            row.interview = {**row.interview, "transcript": {"state": "done", "text": text, "draft": text}}
        db.session.commit()
    interview_search._counted.clear()
    counted, read = [], []
    real_words, real_read = interview_search.words, interview_search.transcripts
    monkeypatch.setattr(interview_search, "words", lambda *a, **k: (counted.append(1), real_words(*a, **k))[1])
    monkeypatch.setattr(interview_search, "transcripts", lambda *a, **k: (read.append(1), real_read(*a, **k))[1])
    first = client.get(f"/guide/{code}/interviews/search").get_data(as_text=True)
    assert "pflege" in first and len(counted) == 3 and len(read) == 1
    page = client.get(f"/guide/{code}/interviews/search?q=pfleg*").get_data(as_text=True)
    assert "<mark>Pflege</mark>" in page and len(counted) == 3 and len(read) == 2
    client.get(f"/guide/{code}/interviews/search?q=mutter&sprecher=B")
    assert len(counted) == 6  # another speaker filter: counted once more
    with app.app_context():
        row = db.session.get(Response, ids[0])
        row.interview = {**row.interview, "transcript": {**row.interview["transcript"], "text": "B: Ganz anders"}}
        db.session.commit()
    assert "anders" in client.get(f"/guide/{code}/interviews/search").get_data(as_text=True)
    assert len(counted) == 7  # a changed transcript is counted anew


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f7_guide_drag_reports_a_refusal():
    src = _js("app.js")
    guide = src[src.index('closest(".guide-part-guide")'):]
    drop = guide[guide.index("drop: function"):guide.index("end: function")]
    assert "actionFailed(r.status)" in drop and "actionFailed(0)" in drop
    assert ".then(function () { location.reload(); })" not in drop


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i3_as_list():
    assert ai.as_list([1]) == [1] and ai.as_list("jung, alt") == [] and ai.as_list(5) == [] and ai.as_list({"a": 1}) == []


def test_i3_interview_assistant_wrong_types_give_the_message(app, client, monkeypatch):
    from tests.interview.test_interview import make

    code, _ = make(app, client)
    _fake(monkeypatch, '{"questions": 5}')
    r = client.post(f"/guide/{code}/ai/develop", data={"action": "collect", "own": "Wie war das?"})
    assert r.status_code < 500
    _fake(monkeypatch, '{"remarks": "kurz", "parts": [{"item": ["F1"], "id": "question", "suggestion": "x"}]}')
    r = client.post(f"/guide/{code}/ai/check", data={})
    assert r.status_code < 500


def test_i5_nan_and_infinity_are_refused(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    r = client.post(f"/guide/{code}/interviews/{rid}/save", data='{"elapsed": Infinity}',
                    content_type="application/json")
    assert r.status_code == 400
    r = client.post(f"/guide/{code}/interviews/{rid}/recorder/state", data='{"state": "recording", "ms": NaN}',
                    content_type="application/json")
    assert r.status_code == 400
    assert client.post(f"/guide/{code}/interviews/{rid}/save", data='{"elapsed": 5000}',
                       content_type="application/json").status_code == 200


def test_i5_finite_numbers_only():
    from app import interview_data

    assert interview_data._finite(3) and interview_data._finite(2.5)
    assert not interview_data._finite(float("inf")) and not interview_data._finite(float("nan"))
    assert not interview_data._finite("3")


def test_i8_version_2_is_unambiguous():
    one = external.signed_text(NODE, "T", {"a": "x&b=5", "b": "3"})
    two = external.signed_text(NODE, "T", {"a": "x", "b": "5&b=3"})
    assert one != two
    assert one == "sig_v=2&t=T&a=x%26b%3D5&b=3"
    assert external.signed_text(NODE, "T", {"a": "gut gemacht ä~", "b": ""}) == "sig_v=2&t=T&a=gut%20gemacht%20%C3%A4~&b="
    # version 1 as before (ambiguous; taken only until V1_UNTIL)
    assert external.signed_text(NODE, "T", {"a": "x&b=5", "b": "3"}, 1) == external.signed_text(NODE, "T", {"a": "x", "b": "5&b=3"}, 1)


def test_i8_which_version_counts():
    before, after = external.V1_UNTIL.replace(day=1).replace(month=1), external.V1_UNTIL
    assert external.signed_version({"sig_v": "2"}, datetime(2030, 1, 1)) == 2
    assert external.signed_version({}, datetime.combine(before, datetime.min.time())) == 1
    assert external.signed_version({}, datetime.combine(after, datetime.min.time())) is None
    assert external.signed_version({"sig_v": "3"}, datetime.combine(before, datetime.min.time())) is None
    assert external.V1_UNTIL == date(2027, 4, 1)


def test_i8_the_guide_and_the_name_sig_v(app, client, survey):
    from tests.editor.test_external import E, build, node_form
    from tests.platform.test_security_keys import _sign_on

    nid = build(app, client)
    _sign_on(client, nid)
    page = client.get("/surveys/1/flow/").data.decode()
    assert "sig_v=2&amp;t=" in page and "31.03.2027" in page and "rawurlencode" in page
    assert "Rückgabewert „sig_v“" in client.post(f"{E}/flow/{nid}/update", data=node_form(values=(("sig_v", "number"),)),
                                                 follow_redirects=True).data.decode()


def test_i8_the_python_example_of_the_guide_matches():
    import hashlib
    import hmac
    import os
    import re

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "externer-abschnitt-signatur.md")
    with open(path, encoding="utf-8") as f:
        code = re.search(r"```python\n(.*?)```", f.read(), re.S).group(1)
    scope = {}
    exec(code, scope)  # noqa: S102 - the example of our own guide
    link = scope["signed_return"]("https://q.example/s/u/back?t=TOKEN_abc", "KEY", [("a", "x&b=5"), ("b", "3 €")])
    assert link.startswith("https://q.example/s/u/back?t=TOKEN_abc&a=x%26b%3D5&b=3%20%E2%82%AC&sig_v=2&sig=")
    expected = hmac.new(b"KEY", external.signed_text(NODE, "TOKEN_abc", {"a": "x&b=5", "b": "3 €"}).encode(),
                        hashlib.sha256).hexdigest()
    assert link.endswith(expected)


def test_recorder_state_with_a_list_as_body_is_refused(app, client):
    """Code review 2026-10-15, K8: a JSON list as the body gave a 500."""
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    for body in ("[1, 2]", "7", '"recording"'):
        r = client.post(f"/guide/{code}/interviews/{rid}/recorder/state", data=body, content_type="application/json")
        assert r.status_code == 400
