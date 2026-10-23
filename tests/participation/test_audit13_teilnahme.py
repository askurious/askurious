"""Audit Daten und Berechnungen 2026-10-12, Bereich Teilnahme (T-1 bis T-11): Beginn und Seitenuhr beim erneuten
Öffnen der ersten Seite, Zahlen in der Sprache der gesendeten Seite, ungültig geänderte Antworten bei „Weiter“,
Textkorrekturen im Statusprotokoll, Sprachwechsel und „zuletzt aktiv“ im Export, Sprachaufnahmen erst nach dem
Commit, Größengrenze je Sprachfrage, „abgewiesen“ bei Ablehnung, Codes neben Kurznamen."""
import csv
import io
import os
from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import models, participate
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def text(client, item, code, **extra):
    client.post(f"{E}/items/{item}", data={"code": code, "question": code, **extra})


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING, s.status
        return s.code


def responses(app):
    with app.app_context():
        rows = list(db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id)))
        for r in rows:
            db.session.expunge(r)
        return rows


def export(client):
    data = client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(data)))


def pos(html):
    import re

    m = re.search(r'name="pos" value="([^"]+)"', html)
    return m.group(1) if m else None


# --- T-1: the first page opened again ------------------------------------------------------------------------


def _timed_survey(app, client):
    fill_consent(client)
    add(client, "text_input")  # 2
    text(client, 2, "q")
    with app.app_context():
        db.session.get(Survey, 1).page_timing = True
        db.session.commit()
    return start(app, client)


def test_back_from_the_privacy_page_keeps_the_start(app, client, survey, monkeypatch):
    uid = _timed_survey(app, client)
    t0 = datetime(2030, 5, 1, 10, 0, 0)
    clock = Clock(t0)
    monkeypatch.setattr(participate, "utcnow", clock)
    monkeypatch.setattr(models, "utcnow", clock)
    p = app.test_client()
    p.get(f"/s/{uid}")                        # 10:00 the consent page
    clock.at = t0 + timedelta(minutes=4)
    p.get(f"/s/{uid}/privacy")
    p.get(f"/s/{uid}")                        # 10:04 back to the consent page
    clock.at = t0 + timedelta(minutes=5)
    p.post(f"/s/{uid}", data=CONSENT_OK)      # 10:05 agreed
    p.get(f"/s/{uid}")
    clock.at = t0 + timedelta(minutes=6)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "x"})
    r = responses(app)[0]
    assert r.status == "completed"
    assert r.started_at == t0
    assert (r.completed_at - r.started_at).total_seconds() >= sum(r.paradata["_t"].values())
    assert r.paradata["_t"]["1"] == 300       # the consent page from its first opening


def test_opened_again_after_an_hour_starts_anew(app, client, survey, monkeypatch):
    uid = _timed_survey(app, client)
    t0 = datetime(2030, 5, 1, 10, 0, 0)
    clock = Clock(t0)
    monkeypatch.setattr(participate, "utcnow", clock)
    monkeypatch.setattr(models, "utcnow", clock)
    p = app.test_client()
    p.get(f"/s/{uid}")
    later = t0 + timedelta(days=2)
    clock.at = later
    p.get(f"/s/{uid}")                        # two days later: the participation begins now
    clock.at = later + timedelta(minutes=1)
    p.post(f"/s/{uid}", data=CONSENT_OK)
    r = responses(app)[0]
    assert r.started_at == later
    assert r.paradata["_t"]["1"] == 60


# --- T-2, T-3: numbers in the page's language, an invalid change with "Next" -----------------------------------


def test_two_tabs_in_two_languages_read_numbers_by_the_page_language(app, client, survey):
    fill_consent(client)
    add(client, "text_input")  # 2
    client.post(E + "/items/2", data={"code": "n", "question": "Zahl", "validation": "decimal"})
    add(client, "page_break")
    add(client, "info")  # 4
    text(client, 4, "outro")
    with app.app_context():
        db.session.get(Survey, 1).language_choice = True
        db.session.commit()
    uid = start(app, client)
    url = f"/s/{uid}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    token = pos(p.get(url).data.decode())    # tab A: German
    p.get(url + "?lang=en")                  # tab B: the language link to English
    p.post(url, data={"nav": "next", "i2": "1.000", "pos": token, "page_lang": "de"})  # tab A sends
    r = responses(app)[0]
    assert r.answers.get("2") == 1000, r.answers


def test_invalid_change_with_next_drops_the_old_answer(app, client, survey):
    fill_consent(client)
    add(client, "text_input")  # 2
    client.post(E + "/items/2", data={"code": "n", "question": "Zahl", "validation": "integer"})
    add(client, "page_break")
    add(client, "info")
    text(client, 4, "outro")
    uid = start(app, client)
    url = f"/s/{uid}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": "25"})
    p.post(url, data={"nav": "back"})
    html = p.post(url, data={"nav": "next", "i2": "abc"}).data.decode()
    assert "has-error" in html
    assert 'value="abc"' in html             # the page shows what was typed
    assert "2" not in responses(app)[0].answers


# --- T-4: text corrections in the field --------------------------------------------------------------------------


def test_text_correction_in_the_field_goes_into_the_status_log(app, client, survey):
    fill_consent(client)
    add(client, "text_input")  # 2
    text(client, 2, "q")
    start(app, client)
    client.post(E + "/items/2", data={"code": "q", "question": "Neu formuliert"})
    client.post(E + "/items/2", data={"code": "q", "question": "Noch einmal neu"})  # within minutes: one entry
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Item, 2).text("question", "de") == "Noch einmal neu"
        entries = [e for e in s.status_log if e["event"] == "texts"]
        assert len(entries) == 1 and entries[0]["reason"] == "q (de)", s.status_log
        from app import timeline

        shown = [text for _, text in timeline.build(s)["events"]]
        assert "Text korrigiert: q (de)" in shown


def test_a_draft_logs_no_text_changes(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    text(client, 2, "q")
    client.post(E + "/items/2", data={"code": "q", "question": "Anders"})
    with app.app_context():
        assert not [e for e in db.session.get(Survey, 1).status_log or [] if e["event"] == "texts"]


# --- T-5, T-6: language switch and "zuletzt aktiv" in the export --------------------------------------------------


def test_language_switch_and_last_active_in_the_export(app, client, survey):
    fill_consent(client)
    add(client, "text_input")  # 2
    text(client, 2, "q")
    add(client, "page_break")
    add(client, "info")  # 4
    text(client, 4, "outro")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.language_choice = True
        db.session.commit()
    uid = start(app, client)
    url = f"/s/{uid}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.get(url + "?lang=en")                  # switched on the second page, then stops
    q = app.test_client()
    q.get(url)
    q.post(url, data=CONSENT_OK)
    q.post(url, data={"nav": "next", "i2": "x"})
    q.post(url, data={"nav": "next"})
    rows = export(client)
    assert "lang_switched" in rows[0] and "last_active" in rows[0]
    first, second = rows
    assert first["lang_switched"] == "1" and first["lang"] == "en" and first["status"] == "active"
    assert first["last_active"] and not first["finished"]
    assert second["lang_switched"] == "0" and second["status"] == "completed" and second["last_active"] == ""
    r = responses(app)[0]
    assert r.paradata["_langs"] == [["de", 0], ["en", 1]]
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "lang_switched" in codebook and "last_active" in codebook


# --- T-7: the language switch fixes no feedback source --------------------------------------------------------


def test_language_switch_fixes_no_answer(app, client, survey, monkeypatch):
    from app import feedback_lock

    calls = []
    monkeypatch.setattr(feedback_lock, "note_sent", lambda *a, **k: calls.append(a))
    fill_consent(client)
    add(client, "text_input")
    text(client, 2, "q")
    with app.app_context():
        db.session.get(Survey, 1).language_choice = True
        db.session.commit()
    uid = start(app, client)
    url = f"/s/{uid}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    calls.clear()
    p.post(url, data={"set_lang": "en", "i2": "x", "pos": pos(p.get(url).data.decode())})
    assert calls == []


# --- T-8, T-9: voice recordings ------------------------------------------------------------------------------


def test_voice_recording_is_durable_before_commit_without_replacing_the_previous_answer(app, client, survey):
    from flask import g

    from app.items import voice

    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        r = Response(survey_id=1, language="de", answers={}, pid="abc-123", source="real", is_preview=False,
                     status=Response.ACTIVE, position=0)
        db.session.add(r)
        db.session.commit()
        where = voice.folder(s.uid, r.pid)
        r.answers = {"2": {"audio": {"file": "2.webm", "mime": "audio/webm"}}}
        g.setdefault(voice._PENDING, []).append({"response": r, "item": 2, "data": b"\x1a\x45\xdf\xa3" + b"0" * 10,
                                                 "mime": "audio/webm", "previous": None})
        voice.commit()
        staged = voice.path(s.uid, r.pid, r.answers["2"])
        assert staged and ".pending" in staged
        db.session.rollback()                                       # the commit failed: no new reference is stored
        assert "2" not in r.answers
        r.answers = {"2": {"audio": {"file": "2.webm", "mime": "audio/webm"}}}
        g.setdefault(voice._PENDING, []).append({"response": r, "item": 2, "data": b"\x1a\x45\xdf\xa3" + b"1" * 10,
                                                 "mime": "audio/webm", "previous": None})
        voice.commit()
        r.last_active_at = models.utcnow()
        db.session.commit()
        assert voice.path(s.uid, r.pid, r.answers["2"]) is not None


def test_page_size_grows_with_the_voice_questions(app, client, survey):
    from tests.interview.test_voice import switch_on

    from app.items import voice

    switch_on(app)
    fill_consent(client)
    for n in (2, 3):
        add(client, "voice")
        client.post(f"{E}/items/{n}", data={"code": f"v{n}", "question": "Erzählen", "max_seconds": "300",
                                            "text_fallback": "1"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
        assert voice.records(code) == 2 * voice.limit(300) > voice.MAX_BYTES
        assert voice.records("unknown-code") == 0


# --- T-10: a decline gives back "abgewiesen" -----------------------------------------------------------------


def test_decline_takes_back_refused(app, client, survey):
    from app import quotas
    from app.models import Quota, QuotaCell

    with app.app_context():
        quota = Quota(survey_id=1, code="g", name="g")
        db.session.add(quota)
        db.session.flush()
        cell = QuotaCell(quota_id=quota.id, code="m", target=1, refused=1, done=1)
        db.session.add(cell)
        db.session.flush()
        r = Response(survey_id=1, language="de", answers={}, pid="x-1", source="real", is_preview=False,
                     status=Response.ACTIVE, position=0, quota={str(quota.id): {"cell": cell.id, "res": "full"}})
        db.session.add(r)
        db.session.flush()
        quotas.release(r)
        db.session.expire_all()
        assert db.session.get(QuotaCell, cell.id).refused == 0


# --- T-11: a new code is never a label taken already ------------------------------------------------------------


def test_new_survey_code_avoids_labels(app, client, survey, monkeypatch):
    from app.models import SurveyLabel, User

    codes = iter(["energy", "fresh1"])
    monkeypatch.setattr(models, "new_code", lambda: next(codes))
    with app.app_context():
        db.session.add(SurveyLabel(label="energy", survey_id=1))
        owner = db.session.scalars(db.select(User)).first()
        s = Survey(title="Zweite", created_by_id=owner.id)
        db.session.add(s)
        db.session.commit()
        assert s.code == "fresh1"
