"""Audit data and calculations 2026-10-10, package A (deletion and periods): L-4, L-7, L-11, N-1, E-1."""
from datetime import timedelta

import pytest

from app import retention
from app.extensions import db
from app.models import Item, MailOutbox, Response, Survey, SurveyInvite, utcnow

WEBM = b"\x1a\x45\xdf\xa3"  # the first bytes of a WebM recording (app/items/voice.py sniff)

META = {"title": "Test", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}


def _privacy(personal, answers):
    return {"part": "datenschutz", "retention_shown": "1", "retention_personal": str(personal or ""),
            "retention_answers": str(answers or "")}


# --- L-4: the invitation list goes with the period "answers"; "personal" no longer than "answers" -----------------

@pytest.mark.parametrize("personal", [None, 24])
def test_l4_purge_takes_the_invitation_list(app, client, survey, personal):
    from tests.conftest import mail_on

    mail_on(app)
    client.post("/surveys/1/invitations/addresses", data={"addresses": "email;name\nanna@example.org;Anna Muster"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.close_reason = Survey.CLOSED, "manual"
        s.closed_at = utcnow() - timedelta(days=800)
        s.retention = {"personal": personal, "answers": 12}
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status="completed", pid="x1",
                                answers={}, layout={}, completed_at=utcnow() - timedelta(days=801)))
        db.session.commit()
        assert retention.holds(s)["personal"] >= 1          # the addresses count on the Export page
        retention.run()
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1)) == 0
        assert db.session.scalars(db.select(SurveyInvite).filter_by(survey_id=1)).all() == []
        assert db.session.scalar(db.select(db.func.count(MailOutbox.id)).filter(MailOutbox.sent_at.is_(None))) == 0


def test_l4_form_refuses_personal_longer_than_answers(app, client, survey):
    client.post("/surveys/1/meta", data=_privacy(6, 120))
    for personal, answers in ((24, 12), (None, 12)):
        page = client.post("/surveys/1/meta", data=_privacy(personal, answers), follow_redirects=True).data.decode()
        assert "Nicht gespeichert:" in page and "darf nicht länger sein" in page
        with app.app_context():
            assert retention.of(db.session.get(Survey, 1)) == {"personal": 6, "answers": 120}
    client.post("/surveys/1/meta", data=_privacy(12, 12))       # as long as: fine
    client.post("/surveys/1/meta", data=_privacy(None, None))   # neither period: fine
    with app.app_context():
        assert db.session.get(Survey, 1).retention is None
        s = db.session.get(Survey, 1)
        s.retention = {"personal": None, "answers": 120}       # set before the check: kept while the form leaves it
        db.session.commit()
    client.post("/surveys/1/meta", data={**_privacy(None, 120), "admin_hidden_shown": "1", "admin_hidden": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.admin_hidden and retention.of(s) == {"personal": None, "answers": 120}


def test_l4_order_check_unit():
    assert retention.out_of_order({"personal": None, "answers": 12})
    assert retention.out_of_order({"personal": 24, "answers": 12})
    assert not retention.out_of_order({"personal": 12, "answers": 12})
    assert not retention.out_of_order({"personal": None, "answers": None})
    assert not retention.out_of_order({"personal": 6, "answers": None})


# --- L-7: expired invitations of researchers go with the housekeeping ------------------------------------------------

def test_l7_expired_member_invite_goes_without_a_listing(app, client, survey, monkeypatch):
    from app import lifecycle
    from app.models import MemberInvite

    with app.app_context():
        db.session.add(MemberInvite(survey_id=1, role="viewer", email="vera@example.org", token_hash="h" * 64,
                                    created_by_id=1, created_at=utcnow() - timedelta(days=60),
                                    expires_at=utcnow() - timedelta(days=46)))
        db.session.add(MemberInvite(survey_id=1, role="viewer", email="olga@example.org", token_hash="i" * 64,
                                    created_by_id=1, created_at=utcnow(), expires_at=utcnow() + timedelta(days=14)))
        db.session.commit()
        monkeypatch.setattr(lifecycle, "_last_rare", None)
        lifecycle._rare_sweeps(utcnow())
        db.session.commit()
        assert db.session.scalars(db.select(MemberInvite.email)).all() == ["olga@example.org"]


# --- L-11: mail switched off: waiting mails go after a week ------------------------------------------------------------

def test_l11_waiting_mails_go_without_mail(app, client, survey):
    from app import mailqueue

    with app.app_context():
        invite = SurveyInvite(survey_id=1, email="anna@example.org", email_hash="a" * 64, token_hash="t" * 64, name="Anna")
        db.session.add(invite)
        db.session.flush()
        db.session.add(MailOutbox(survey_id=1, invite_id=invite.id, to="anna@example.org", subject="s", body="b",
                                  created_at=utcnow() - timedelta(days=mailqueue.UNSENT_DAYS + 1)))
        db.session.add(MailOutbox(survey_id=1, to="bert@example.org", subject="s", body="b", created_at=utcnow()))
        db.session.commit()
        assert not app.config.get("MAIL_HOST")
        mailqueue.run(utcnow())
        assert db.session.scalars(db.select(MailOutbox.to)).all() == ["bert@example.org"]
        assert db.session.get(SurveyInvite, invite.id).failed


# --- N-1: a coarser storage level in the field cuts down what is stored at once ------------------------------------

def _geo_running(app, client, personal=1):
    from tests.items.test_geolocation import AACHEN, run_survey

    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}], personal=personal)
    return AACHEN


def test_n1_coarser_in_the_field_cuts_stored_positions(app, client, survey):
    from tests.items.test_geolocation import E

    _geo_running(app, client)
    client.post(E + "/items/2", data={"code": "ort", "question": "Wo sind Sie gerade?", "store": "state", "correct": "1"})
    with app.app_context():
        assert db.session.get(Item, 2).config["store"] == "state"
        a = db.session.scalar(db.select(Response).filter_by(source="real")).answers["2"]
        assert "lat" not in a and "lon" not in a and "plz" not in a and a["state"] == "5"


def test_n1_exact_to_1km_rounds_stored_and_exported(app, client, survey):
    from tests.items.test_geolocation import E

    aachen = _geo_running(app, client)
    client.post(E + "/items/2", data={"code": "ort", "question": "Wo sind Sie gerade?", "store": "1km", "correct": "1"})
    with app.app_context():
        item = db.session.get(Item, 2)
        a = db.session.scalar(db.select(Response).filter_by(source="real")).answers["2"]
        assert (a["lat"], a["lon"]) == (round(aachen[0], 2), round(aachen[1], 2))
        # the export never gives more decimals than the codebook names, whatever is stored
        vals = item.kind.export_values(item, {"way": "device", "lat": aachen[0], "lon": aachen[1]})
        assert vals["ort_lat"] == round(aachen[0], 2) and vals["ort_lon"] == round(aachen[1], 2)


# --- E-1: Whisper's draft after a correction: visible, taken over or discarded, gone with the recording -----------

def _draft_interview(app, client, monkeypatch):
    from app import interview_transcribe
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    rid = start(app, client, code)
    url = f"/guide/{code}/interviews/{rid}/audio"
    client.post(f"{url}?part=1&seq=0&start=0", data=WEBM + b"RIFF", content_type="audio/webm")
    app.config.update(WHISPER_URL="http://whisper.intern:8000")
    said = {"text": " Ich bin Erika Mustermann aus Kleinstadt."}

    class R:
        status_code = 200

        def __init__(self, d):
            self._d = d

        def json(self):
            return self._d

    monkeypatch.setattr(interview_transcribe.requests, "post",
                        lambda *a, **k: R({"segments": [{"start": 0.0, "end": 4.0, "text": said["text"]}]}))
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    client.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": "B: Ich bin [Name] aus [Ort]. #00:00:04-0#",
                                                                  "checked_shown": "1", "checked": "1"})
    client.post(f"{url}?part=2&seq=0&start=600", data=WEBM + b"RIFF", content_type="audio/webm")
    said["text"] = " Ich bin Erika Mustermann. Mein Bruder Max wohnt in Hamburg."
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    return code, rid


def _transcript(app, rid):
    with app.app_context():
        return db.session.get(Response, rid).interview["transcript"]


def test_e1_draft_shown_and_taken_over(app, client, monkeypatch):
    code, rid = _draft_interview(app, client, monkeypatch)
    assert "Hamburg" in _transcript(app, rid)["draft"]
    page = client.get(f"/guide/{code}/interviews/{rid}/transcript").data.decode()
    assert "Neuer Entwurf von Whisper" in page and "Hamburg" in page and "transcript/draft" in page
    client.post(f"/guide/{code}/interviews/{rid}/transcript/draft", data={"action": "take"})
    tr = _transcript(app, rid)
    assert "Hamburg" in tr["text"] and "draft" not in tr and not tr.get("checked")
    assert set(tr["covers"]) == {"1", "2"}                  # the transcript now holds both parts


def test_e1_draft_discarded(app, client, monkeypatch):
    code, rid = _draft_interview(app, client, monkeypatch)
    client.post(f"/guide/{code}/interviews/{rid}/transcript/draft", data={"action": "discard"})
    tr = _transcript(app, rid)
    assert "draft" not in tr and tr["text"].startswith("B: Ich bin [Name]") and tr.get("checked")


def test_e1_draft_goes_with_the_period_of_the_recording(app, client, monkeypatch):
    from app import interview_audio

    code, rid = _draft_interview(app, client, monkeypatch)
    with app.app_context():
        assert interview_audio.run(utcnow() + timedelta(days=30)) >= 1
    tr = _transcript(app, rid)
    assert "draft" not in tr and "Mustermann" not in str(tr)
