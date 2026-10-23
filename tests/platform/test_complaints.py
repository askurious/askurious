"""Complaints about a survey from its privacy page and the Slack message about them."""
import threading

from app import notify
from app.extensions import db
from app.models import Feedback, SiteSetting, Survey
from tests.conftest import allow
from tests.participation.test_participate import build, uid


def _running(client):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def test_complaint_goes_to_the_admin_list_first_and_to_slack(app, client, survey, monkeypatch):
    _running(client)
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: sent.append((url, payload["text"])))
    app.config["SLACK_WEBHOOK_URL"] = "https://hooks.slack.test/x"
    client.post("/feedback/new", data={"message": "kleine Idee", "severity": "1"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    form = p.get(f"{url}/complaint?lang=en").data.decode()
    assert "File a complaint" not in form and "Send complaint" in form and 'name="website"' in form
    assert "Please choose" in p.post(f"{url}/complaint?lang=en", data={"kind": "copyright", "message": " "}).data.decode()
    assert "in gutem Glauben" in p.post(f"{url}/complaint", data={"kind": "copyright", "message": "Bild"}).data.decode()
    r = p.post(f"{url}/complaint", data={"kind": "copyright", "message": "Bild 3 ist von mir.", "email": "a@b.de",
                                          "good_faith": "1"})
    assert r.status_code == 200 and "eingegangen" in r.data.decode()
    p.post(f"{url}/complaint", data={"kind": "other", "message": "Spam", "website": "http://x"})  # the trap: kept out
    for thread in threading.enumerate():  # the Slack message is sent in a thread of its own
        if thread.daemon and thread is not threading.current_thread():
            thread.join(2)
    with app.app_context():
        c = db.session.scalar(db.select(Feedback).filter_by(severity=Feedback.COMPLAINT))
        assert c.tag == "Urheberrecht" and c.contact == "a@b.de" and c.user_id is None and c.survey_id == 1
        assert db.session.scalar(db.select(db.func.count(Feedback.id))) == 2
        code = db.session.get(Survey, 1).code
    assert len(sent) == 2 and "Anregung" in sent[0][1]  # also suggestions, for information
    assert "Beschwerde" in sent[1][1] and code in sent[1][1]
    admin = client.get("/feedback/").data.decode()
    assert admin.index("Bild 3 ist von mir.") < admin.index("kleine Idee") and "is-complaint" in admin
    assert "Kontakt: a@b.de" in admin
    assert "Beschwerde" not in client.get("/dashboard/").data.decode().split("data-feedback-open")[1][:3000]


def test_complaints_are_limited_per_sender_and_kept_quietly_over_the_cap(app, client, survey, monkeypatch):
    """S6: the cap per survey refuses nothing (a flood must not lock real complaints out), it only stops the Slack
    messages after one line; one sender gets COMPLAINTS_PER_SENDER complaints per survey and day."""
    _running(client)
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: sent.append(payload["text"]))
    app.config.update(COMPLAINTS_PER_DAY=2, COMPLAINTS_PER_SENDER=2, SLACK_WEBHOOK_URL="https://hooks.slack.test/x")
    url, p = f"/s/{uid(app)}", app.test_client()

    def send(ip, text):
        return p.post(f"{url}/complaint", data={"kind": "privacy", "message": text, "good_faith": "1"},
                      environ_base={"REMOTE_ADDR": ip}).data.decode()

    assert "eingegangen" in send("203.0.113.1", "eins") and "eingegangen" in send("203.0.113.1", "zwei")
    assert "mehrere Beschwerden" in send("203.0.113.1", "drei")  # the sender's third
    for i, ip in enumerate(["203.0.113.2", "203.0.113.3", "203.0.113.4"]):
        assert "eingegangen" in send(ip, f"weitere {i}")
    for thread in threading.enumerate():
        if thread.daemon and thread is not threading.current_thread():
            thread.join(2)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Feedback.id)).filter_by(severity=Feedback.COMPLAINT)) == 5
    assert len(sent) == 3 and "Beschwerde" in sent[0] and "nur in Askurious" in sent[2]


def _click(app, client, action, value, user="U_PHILIPP", secret="s3cret", age=0, signature=None):
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    payload = {"type": "block_actions", "user": {"id": user, "name": "philipp"},
               "response_url": "https://hooks.slack.com/actions/T/1/x", "actions": [{"action_id": action, "value": str(value)}]}
    body = urlencode({"payload": json.dumps(payload)})
    stamp = str(int(time.time()) - age)
    sig = signature or "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return client.post("/slack/aktion", data=body, content_type="application/x-www-form-urlencoded",
                       headers={"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sig})


def test_slack_buttons_pause_the_survey_and_mark_done(app, client, survey, monkeypatch):
    _running(client)
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: sent.append((url, payload)))
    app.config.update(SLACK_WEBHOOK_URL="https://hooks.slack.test/x", SLACK_SIGNING_SECRET="s3cret",
                      SLACK_ADMINS=["U_PHILIPP", "U_RALF"])
    app.test_client().post(f"/s/{uid(app)}/complaint", data={"kind": "copyright", "message": "Bild 3", "good_faith": "1"})

    def wait():
        for thread in threading.enumerate():
            if thread.daemon and thread is not threading.current_thread():
                thread.join(2)

    wait()
    actions = [e["action_id"] for b in sent[0][1]["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert actions == ["pause", "done", "open"]
    with app.app_context():
        fid = db.session.scalar(db.select(Feedback.id).filter_by(severity=Feedback.COMPLAINT))
    p = app.test_client()                                                    # Slack does not sign in
    assert _click(app, p, "pause", fid, signature="v0=forged").status_code == 403
    assert _click(app, p, "pause", fid, age=600).status_code == 403          # too old: a replay
    sent.clear()
    assert _click(app, p, "pause", fid, user="U_SOMEONE").status_code == 200
    wait()
    assert "Nicht berechtigt" in sent[0][1]["text"]
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING
    sent.clear()
    _click(app, p, "pause", fid)
    wait()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.CLOSED and s.close_reason == "paused" and s.status_log[-1]["by"] == "Slack: philipp"
    reply = sent[0][1]
    assert reply["replace_original"] and "pausiert von philipp" in reply["text"]
    assert [e["action_id"] for b in reply["blocks"] if b["type"] == "actions" for e in b["elements"]] == ["done", "open"]
    sent.clear()
    _click(app, p, "done", fid, user="U_RALF")
    wait()
    with app.app_context():
        assert db.session.get(Feedback, fid).status == "done"
    assert "erledigt von philipp" in sent[0][1]["text"]


def test_slack_answers_only_to_slack(app, client, survey, monkeypatch):
    from app import slack_actions

    sent = []
    monkeypatch.setattr(notify, "post", lambda url, payload: sent.append(url))
    with app.app_context():
        slack_actions._answer("https://evil.example/x", {})
        slack_actions._answer("https://hooks.slack.com/actions/1", {})
    assert sent == ["https://hooks.slack.com/actions/1"]


def _detailed(app):
    """Detailed Slack messages (Administration > Server); short ones are the default (app/notify.py)."""
    with app.app_context():
        db.session.add(SiteSetting(key=notify.CONTENT_KEY, value="full"))
        db.session.commit()


def test_slack_message_quotes_the_text_up_to_500_characters(app, client, survey):
    from app.models import Feedback

    _detailed(app)
    with app.app_context(), app.test_request_context():
        s = db.session.get(Survey, 1)
        item = Feedback(severity=Feedback.COMPLAINT, tag="Urheberrecht", survey_id=s.id, contact="geheim@x.de",
                        message=f"Befragung {s.code} „Test“\n\nBild 3 <gehört> mir.\n" + "x" * 600)
        db.session.add(item)
        db.session.commit()
        full = notify.blocks(item)
        quote = full[1]["text"]["text"]
    assert quote.startswith("> Bild 3 &lt;gehört&gt; mir.\n> xxx") and quote.endswith("…")
    assert len(quote.replace("> ", "").replace("&lt;", "<").replace("&gt;", ">")) == 500 and "Befragung" not in quote and "geheim" not in str(full)


def test_suggestions_and_annoyances_are_for_information_only(app, client):
    _detailed(app)
    with app.test_request_context():
        for severity, head in ((1, "Anregung"), (2, "Störung gemeldet"), (3, "Fehler gemeldet")):
            item = Feedback(severity=severity, message="Der Knopf ist zu klein.", page="/surveys/1/editor/", status="open")
            out = notify.blocks(item)
            ids = [b.get("action_id") for b in out[-1]["elements"]]
            assert head in notify.headline(item) and "> Der Knopf ist zu klein." in out[1]["text"]["text"]
            assert ids == (["done", "open"] if severity == 3 else ["open"])


def test_a_registration_is_announced_once(app, monkeypatch):
    """The first sign-in with ORCID is the registration: one Slack message (the account number; name and institution only when detailed), never the iD;
    later sign-ins of the same person send none."""
    from app import auth, orcid_api

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"orcid": "0000-0002-1825-0097", "name": "Neue Forscherin"}

    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: sent.append(payload))
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Answer)
    app.config.update(SLACK_WEBHOOK_URL="https://hooks.slack.test/x", ORCID_CLIENT_ID="client")
    allow(app, "*")
    for _ in range(2):
        c = app.test_client()
        c.get("/auth/login")
        with c.session_transaction() as s:
            state = s["oauth_state"]
        c.get(f"/auth/orcid/callback?state={state}&code=x")
    for thread in threading.enumerate():
        if thread.daemon and thread is not threading.current_thread():
            thread.join(2)
    assert len(sent) == 1, sent
    text = str(sent[0])
    assert "Neu registriert" in text and "Neue Forscherin" not in text and "Konto Nr." in text  # short by default (2026-10-21)
    assert "0000-0002-1825-0097" not in text and "/admin/accounts/" in text


def test_decision_on_a_complaint_is_kept_and_sent_to_both_sides(app, client, survey, monkeypatch):
    """Art. 16(4), 16(5) and 17 DSA: confirmation of receipt, then the decision with its reasons, kept with the
    complaint and sent to the person who complained and to the survey's admins (copyright audit 2026-10-16, U11)."""
    from app import mail

    _running(client)
    mails = []
    monkeypatch.setattr(mail, "send", lambda to, subject, text, reply_to=None: mails.append((to, subject, text)))
    monkeypatch.setattr(mail, "enabled", lambda app=None: True)
    with app.app_context():
        from app.models import UserLogin

        db.session.add(UserLogin(user_id=1, email="forscherin@example.org"))
        db.session.commit()
    url, p = f"/s/{uid(app)}", app.test_client()
    p.post(f"{url}/complaint", data={"kind": "copyright", "message": "Bild 3 ist von mir.", "name": "Anna Muster",
                                     "email": "anna@example.org", "good_faith": "1"})
    assert mails[0][0] == "anna@example.org" and "eingegangen" in mails[0][1]
    with app.app_context():
        c = db.session.scalar(db.select(Feedback).filter_by(severity=Feedback.COMPLAINT))
        fid, contact = c.id, c.contact
    assert contact == "Anna Muster <anna@example.org>"
    assert "Entscheiden" in client.get("/feedback/").data.decode()
    client.post(f"/feedback/{fid}/decide", data={"measure": "removed", "reason": "Das Bild wurde ohne Rechte genutzt."})
    to = [m[0] for m in mails[1:]]
    assert to == ["anna@example.org", "forscherin@example.org"] and "Inhalt entfernt" in mails[1][2]
    with app.app_context():
        c = db.session.get(Feedback, fid)
        assert c.status == "done" and "--- Entscheidung" in c.message and "ohne Rechte" in c.message


def test_receipts_go_once_per_address_without_the_title(app, client, survey, monkeypatch):
    """Audit 2026-10-18, BIZ-03: the anonymous form mailed a receipt to any address typed in, with the title the
    researchers set freely, limited per sender (IPv6 /64) and process only. Now the receipt is a fixed text with the
    survey's code and the case number, at most COMPLAINT_MAILS_PER_ADDRESS per address and day from all senders;
    every complaint is still kept, and the page names its case number."""
    from app import limits
    from tests.conftest import mail_on

    sent = mail_on(app)
    _running(client)
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.title = "Gewinnspiel: http://spam.example/win jetzt klicken"
        db.session.commit()
        code = s.code
    url = f"/s/{uid(app)}/complaint"
    form = {"kind": "content", "message": "x", "good_faith": "1", "email": "Victim@Example.net", "name": "A"}
    pages = []
    for n in range(10):  # ten senders of their own (IPv6 /64), each in another server process
        for store in (limits._asked, limits._load_fallback):
            store.clear()
        pages.append(app.test_client().post(url, data={**form, "email": form["email"] if n % 2 else "victim@example.net"},
                                            environ_base={"REMOTE_ADDR": f"2001:db8:{n:x}::1"}).data.decode())
    assert all("eingegangen" in p and "Ihre Vorgangsnummer:" in p for p in pages)
    assert len(sent) == 1 and sent[0]["To"] == "victim@example.net"
    body = sent[0].get_content()
    assert "Gewinnspiel" not in body and "spam.example" not in body and code in body
    with app.app_context():
        first = db.session.scalar(db.select(Feedback.id).filter_by(severity=Feedback.COMPLAINT).order_by(Feedback.id))
        assert f"Vorgangsnummer {first})" in body
        assert db.session.scalar(db.select(db.func.count(Feedback.id)).filter_by(severity=Feedback.COMPLAINT)) == 10
    # another address gets its own receipt; over the survey's daily cap no receipts go at all
    app.config["COMPLAINTS_PER_DAY"] = 11
    app.test_client().post(url, data={**form, "email": "other@example.net"}, environ_base={"REMOTE_ADDR": "203.0.113.1"})
    app.test_client().post(url, data={**form, "email": "third@example.net"}, environ_base={"REMOTE_ADDR": "203.0.113.2"})
    assert [m["To"] for m in sent] == ["victim@example.net", "other@example.net"]


def test_complaints_per_sender_hold_across_processes(app, client, survey, monkeypatch):
    """BIZ-03/BIZ-01: COMPLAINTS_PER_SENDER counts in the database, so another server process does not give the same
    sender three more."""
    from app import limits

    _running(client)
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: None)
    app.config.update(COMPLAINTS_PER_SENDER=2)
    url = f"/s/{uid(app)}/complaint"
    answers = []
    for n in range(3):
        for store in (limits._asked, limits._load_fallback):
            store.clear()
        answers.append(app.test_client().post(url, data={"kind": "privacy", "message": f"m{n}", "good_faith": "1"},
                                              environ_base={"REMOTE_ADDR": "2001:db8:1::5"}).data.decode())
    assert "eingegangen" in answers[0] and "eingegangen" in answers[1] and "mehrere Beschwerden" in answers[2]
