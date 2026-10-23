from app import interview_transcribe
from app.extensions import db
from app.models import Response, Survey
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start, WEBM


def test_progress_is_live_translated_and_contains_no_job_ids(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=0", data=WEBM + b"audio", content_type="audio/webm")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", beat=interview_transcribe._now(),
            progress={"stage": "queued", "position": 3}, jobs={"1": {"id": "a" * 32}}, parts={})
        db.session.commit()
    url = f"/guide/{code}/interviews/{rid}/transcribe/progress"
    response = client.get(url)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    assert response.json["text"] == "Warteschlange: Platz 3"
    assert response.json["active"] and response.json["done"] == 0 and response.json["total"] == 1
    assert "a" * 32 not in response.get_data(as_text=True)
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert 'data-transcription-progress=' in page and "transcription-progress" in page
    # the recording stays folded while it is transcribed: the status column does not grow tall (Ralf, 2026-10-18)
    assert "Abbrechen" in page and 'class="iv-audio">' in page and 'class="iv-audio" open' not in page
    assert 'class="iv-progress-row"' in page
    with app.app_context():
        row = db.session.get(Response, rid)
        size = row.interview["audio"][0]["bytes"]
        interview_transcribe._set(row, state="done", progress={}, parts={}, covers={"1": size})
        db.session.commit()
    done = client.get(url).json
    assert not done["active"] and done["done"] == done["total"] == 1


def test_progress_cannot_read_an_interview_of_another_guide(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    other, other_sid = make(app, client, title="Other guide")
    assert client.get(f"/guide/{other}/interviews/{rid}/transcribe/progress").status_code == 404
    anonymous = app.test_client().get(f"/guide/{code}/interviews/{rid}/transcribe/progress")
    assert anonymous.status_code in (302, 401)


def test_model_is_saved_per_job_and_changed_model_keeps_corrected_text(app, client, monkeypatch):
    code, sid = make(app, client)
    rid = start(app, client, code)
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_MODEL="small")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="error", requested_model="small", edited=True, text="corrected text",
                                  parts={"1": {"bytes": 10, "text": "old draft"}}, jobs={"1": {"id": "a" * 32}})
        db.session.commit()
    from app import whisper_jobs
    cancelled = []
    monkeypatch.setattr(whisper_jobs, "cancel_records", lambda records: cancelled.append(records))
    monkeypatch.setattr(interview_transcribe, "work", lambda *args: None)
    with app.app_context():
        survey, row = db.session.get(Survey, sid), db.session.get(Response, rid)
        interview_transcribe.start(survey, row, background=False, model="large-v3-turbo")
        saved = interview_transcribe.state(db.session.get(Response, rid))
        assert saved["requested_model"] == "large-v3-turbo" and saved["parts"] == {} and saved["jobs"] == {}
        assert saved["text"] == "corrected text" and saved["edited"]
    assert cancelled and cancelled[0]["1"]["id"] == "a" * 32
    assert app.config["WHISPER_MODEL"] == "small"


def test_start_form_accepts_only_available_models(app, client, monkeypatch):
    code, sid = make(app, client)
    rid = start(app, client, code)
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_MODEL="small")
    client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=0", data=WEBM + b"audio", content_type="audio/webm")
    calls = []
    monkeypatch.setattr(interview_transcribe, "start", lambda *args, **kwargs: calls.append(kwargs))
    url = f"/guide/{code}/interviews/{rid}/transcribe"
    assert client.post(url, data={"model": "../private-model"}).status_code == 302
    assert calls == []
    assert client.post(url, data={"model": "large-v3-turbo"}).status_code == 302
    assert calls[0]["model"] == "large-v3-turbo"
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert '<select name="model">' in page and 'value="large-v3-turbo"' in page


def test_estimate_is_a_range_unknown_and_overdue_are_explicit(app, monkeypatch):
    monkeypatch.setattr(interview_transcribe.time, 'time', lambda: 1000000)
    with app.app_context():
        assert 'noch nicht abschätzbar' in interview_transcribe.estimate_info({}, True)
        value = {'eta_until': 1000600, 'eta_sampled': True}
        text = interview_transcribe.estimate_info(value, True)
        assert '6–18 Minuten' in text and 'Warteschlange' in text
        assert 'Grobe' in interview_transcribe.estimate_info({**value, 'eta_sampled': False}, True)
        assert '2–25 Minuten' in interview_transcribe.estimate_info({**value, 'eta_low_until': 1000120, 'eta_high_until': 1001500}, True)
        assert 'länger als geschätzt' in interview_transcribe.estimate_info({**value, 'eta_overdue': True}, True)
        assert interview_transcribe.estimate_info(value, False) == ''
        assert 'small' in interview_transcribe.error_info({'error_code': 'memory_limit', 'error': 'old text'})


def test_failed_progress_shows_memory_advice_in_the_current_language(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state='error', error_code='memory_limit', error='older background message')
        db.session.commit()
    url = f'/guide/{code}/interviews/{rid}/transcribe/progress'
    german = client.get(url).json
    assert not german['active'] and 'Speicherlimit' in german['text'] and 'small' in german['text']
    assert german['eta_text'] == '' and 'older background' not in german['text']
    client.post('/account/language', data={'lang': 'en'})
    english = client.get(url).json
    assert 'memory limit' in english['text'] and 'same model' in english['text']
