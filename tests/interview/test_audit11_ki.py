"""Audit security + performance 2026-10-10, package U3 "KI": K1/K5 (own AI access: whole-call deadline, size cap,
model ids, checked address, no proxy), K2 (refund only when the provider surely got nothing, decision 5a), K3/P-S6
(the confirmation "Teilnehmende informiert" names its recipient and is given by the owner or an admin, decision 6a),
K7 (generative AI off also stops Whisper and the own access's save/check, decision 3a), K6 (statistics in SQL) and
P-S11 (the AI reading counts per started paper.AI_UNIT fields). No real AI service: a local socket server or fakes."""
import gzip
import http.server
import json
import threading
import time

import httpx
import openai
import pytest

from app import ai, ai_access, limits, secrets_box, webservice
from app.extensions import db
from app.models import AiAccess, AiCall, AiUsage, Survey, SurveyMember, User, utcnow
from tests.conftest import experiments_on
from tests.accounts.test_own_ai_key import OTHER, FakeClient, _err, _store, _uid, other  # noqa: F401 (fixture)

WEBM = b"\x1a\x45\xdf\xa3"  # the first bytes of a WebM recording (app/items/voice.py sniff)

# --- K1, K5: the guarded transport of the own access --------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, body, headers=()):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Handler.seen.append((self.path, self.headers.get("Host")))
        try:
            if self.path.startswith("/drip/"):  # one byte every 0.5 s, 8 s long
                body = b'{"object":"list","data":[]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body) + 16))
                self.end_headers()
                for _ in range(16):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.5)
                self.wfile.write(body)
            elif self.path.startswith("/big/"):  # 5 MB with one model id
                self._send(b'{"object":"list","data":[{"id":"' + b"a" * 5_000_000 + b'","object":"model"}]}')
            elif self.path.startswith("/bomb/"):  # 30 MB unpacked, a few KB packed
                raw = b'{"object":"list","data":[{"id":"' + b"a" * 30_000_000 + b'","object":"model"}]}'
                self._send(gzip.compress(raw), headers=[("Content-Encoding", "gzip")])
            else:  # ids of every kind
                ids = ["a" * 200, "ok-model", "bad id", "gpt-oss-120b", "xéy"]
                self._send(json.dumps({"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}).encode())
        except OSError:
            pass


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads, srv.block_on_close = True, False
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    asked = []

    def checked(host, port, timeout=5):
        asked.append(host)
        return "127.0.0.1"

    monkeypatch.setattr(webservice, "_public_address", checked)
    monkeypatch.setattr(ai, "TEXT_TIMEOUT", 2)
    _Handler.seen = []
    yield srv.server_address[1], asked
    srv.shutdown()


def _client(port, path):
    # a name that resolves nowhere: the call can only work because the transport connects to the checked address
    return ai_access.client(None, base_url=f"http://llm.nowhere.invalid:{port}/{path}/v1", key="sk-test")


def test_k1_drip_ends_at_the_deadline(app, server):
    port, _ = server
    with app.app_context():
        started = time.monotonic()
        assert ai_access.model_ids(_client(port, "drip")) == []
        assert time.monotonic() - started < 6  # 2 s deadline, well before the 8 s of dripping (room for a busy machine, audit 2026-10-13, CF-5)
        with pytest.raises(openai.APITimeoutError) as e:
            _client(port, "drip").models.list()
        assert ai.sent(e.value)  # it reached the provider: it counts (K2)


def test_k1_answers_are_capped_also_packed(app, server):
    port, _ = server
    with app.app_context():
        for path in ("big", "bomb"):
            with pytest.raises(openai.APIConnectionError) as e:
                _client(port, path).models.list()
            assert isinstance(e.value.__cause__, ai_access.TooLarge), path
            assert ai_access.model_ids(_client(port, path)) == []


def test_k5_connects_to_the_checked_address_without_proxy(app, server, monkeypatch):
    port, asked = server
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # a proxy from the environment would be refused here
    with app.app_context():
        assert ai_access.model_ids(_client(port, "ids")) == ["gpt-oss-120b", "ok-model"]  # short and plain (K1)
    assert asked == ["llm.nowhere.invalid"]
    assert _Handler.seen[-1][1] == f"llm.nowhere.invalid:{port}"  # the name stays in the Host header


def test_k5_a_refused_address_is_not_sent(app, monkeypatch):
    def blocked(host, port, timeout=5):
        raise webservice.Blocked("inside")

    monkeypatch.setattr(webservice, "_public_address", blocked)
    with app.app_context():
        with pytest.raises(openai.APIConnectionError) as e:
            ai_access.client(None, base_url="https://rebind.example.org/v1", key="sk").models.list()
    assert isinstance(e.value.__cause__, ai_access.NotSent) and not ai.sent(e.value)


def test_k1_parallel_calls_per_person(app, server):
    port, _ = server
    with app.app_context():
        ai_access._running[None] = ai_access.PARALLEL
        try:
            with pytest.raises(openai.APIConnectionError) as e:
                _client(port, "ids").models.list()
            assert not ai.sent(e.value)
        finally:
            ai_access._running.pop(None, None)
        assert ai_access.model_ids(_client(port, "ids"))
        assert not ai_access._running  # every slot given back


# --- K2: what is given back ------------------------------------------------------------------------------------


def _connection_error(cause):
    e = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    e.__cause__ = cause
    return e


def test_k2_sent_tells_what_reached_the_provider():
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    for status in (400, 401, 403, 404):
        assert not ai.sent(_err(status, "x")), status
    assert ai.sent(_err(429, "rate_limit_exceeded"))
    assert ai.sent(openai.InternalServerError("x", response=httpx.Response(502, request=req), body=None))
    assert ai.sent(openai.APITimeoutError(request=req))
    assert not ai.sent(_connection_error(httpx.ConnectError("refused")))
    assert not ai.sent(_connection_error(httpx.ConnectTimeout("no connection")))
    assert ai.sent(_connection_error(httpx.ReadError("broken off")))
    assert ai.sent(_connection_error(httpx.RemoteProtocolError("closed")))
    assert ai.sent(ValueError("anything else"))


def _count(app, uid):
    with app.app_context():
        return sum(r.count for r in db.session.scalars(db.select(AiUsage).filter_by(user_id=uid)))


def _calls(app, uid, n, monkeypatch, fail):
    from flask_login import login_user

    def responses(kw):
        raise fail()

    monkeypatch.setattr(ai, "_client", lambda: FakeClient(responses=responses))
    refused = 0
    for _ in range(n):
        with app.test_request_context("/"):
            login_user(db.session.get(User, uid))
            try:
                ai.generate_text("x", "y")
            except ai.AIError as e:
                refused += "Tageslimit" in str(e)
    return refused


def test_k2_timeouts_count_refusals_do_not(app, other, monkeypatch):
    uid = _uid(app)
    app.config.update(OPENAI_API_KEY="sk-platform", AI_TEXTS_PER_DAY=3)
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    assert _calls(app, uid, 5, monkeypatch, lambda: _err(401, "invalid_api_key")) == 0
    assert _count(app, uid) == 0
    assert _calls(app, uid, 5, monkeypatch, lambda: _connection_error(httpx.ConnectError("refused"))) == 0
    assert _count(app, uid) == 0
    # timeouts after sending: counted, the limit holds (k_refund.py: 20 of 20 went through before)
    assert _calls(app, uid, 6, monkeypatch, lambda: openai.APITimeoutError(request=req)) == 3
    assert _count(app, uid) == 3


def test_k2_a_429_still_opens_the_dialog_and_counts(app, other, monkeypatch):
    uid = _uid(app)
    app.config.update(OPENAI_API_KEY="sk-platform", AI_TEXTS_PER_DAY=5)
    other.post("/presenter/new", data={"title": "Live", "language": "de-du"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Live")).code

    def responses(kw):
        raise _err(429, "rate_limit_exceeded")

    monkeypatch.setattr(ai, "_client", lambda: FakeClient(responses=responses, chat=responses))
    d = other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"}, headers={"X-AI-Call": "1"}).get_json()
    assert d["status"] == "limit" and d["code"] == "rate"
    assert _count(app, uid) == 1


def test_p_s11_units_count_and_come_back(app, other):
    from flask_login import login_user

    uid = _uid(app)
    app.config["AI_TEXTS_PER_DAY"] = 10
    with app.test_request_context("/"):
        login_user(db.session.get(User, uid))
        assert limits.charge_ai("text", units=4) is None
        assert _count(app, uid) == 4
        limits.refund_ai()  # a refusal before processing gives all four back
        assert _count(app, uid) == 0
        assert limits.charge_ai("text", units=8) is None
        assert "Tageslimit" in limits.charge_ai("text", units=3)  # 8 + 3 > 10: refused, nothing counted
        assert _count(app, uid) == 8
        assert limits.charge_ai("text", units=2) is None
    from app import paper

    assert paper.AI_UNIT == 10 and -(-40 // paper.AI_UNIT) == 4  # a sheet with 40 fields: four calls


# --- K3, P-S6: the confirmation names its recipient; only owner or admin -------------------------------------------


def _team(app, client):
    """Survey 1 with a free text (tests/interview/test_coding.py) and OTHER as "Bearbeiten" with an own access elsewhere."""
    from tests.interview.test_coding import _survey

    key = _survey(app, client)
    app.config["OPENAI_API_KEY"] = "test"
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="B", role="editor", position=1))
        db.session.commit()
    b = app.test_client()
    b.get(f"/auth/dev-login?orcid={OTHER}&name=B")
    b.post("/terms/", data={"accept": "1"})
    experiments_on(client, "coding", orcid=OTHER)
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).user_id = user.id
        db.session.add(AiAccess(user_id=user.id, provider="custom", base_url="https://llm.example-b.org/v1",
                                key_enc=secrets_box.encrypt("sk-b"), key_hint="sk-b", text_model="m"))
        db.session.commit()
    return key, b


def test_k3_another_recipient_needs_its_own_confirmation(app, client, survey, monkeypatch):
    key, b = _team(app, client)
    monkeypatch.setattr(webservice, "_public_address", lambda host, port, timeout=5: "93.184.216.34")
    sent = []

    class Chat:
        class completions:  # noqa: N801
            @staticmethod
            def create(**kw):
                sent.append(json.loads(kw["messages"][1]["content"]))
                msg = type("M", (), {"content": json.dumps({"categories": [
                    {"name": "Preis", "definition": "d", "example": "e"}]})})()
                return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "stop"})()],
                                      "usage": None})()

    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: type("Cl", (), {"chat": Chat})())
    # since 2026-10-21 only own accesses carry answer texts: A has one too and confirms it
    from tests.conftest import own_ai

    own_ai(app, base_url="https://llm.example-a.org/v1")
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    page_b = b.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "llm.example-b.org" in page_b and "die Person, der die Befragung gehört, oder ein Admin der Befragung" in page_b
    assert "Kategorien vorschlagen lassen" not in page_b
    b.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert not sent  # before (audit): 3 answer texts went to llm.example-b.org
    # "Bearbeiten" may not confirm, not even for itself
    b.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1", "to": "own:custom:llm.example-b.org:m"})
    with app.app_context():
        stored = db.session.get(Survey, 1).codings["_ai"]
    assert set(ai_access.confirmations(stored)) == {"own:custom:llm.example-a.org:m"}
    # the admin sees the team member's recipient with its paragraph and confirms it
    page_a = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert 'value="own:custom:llm.example-b.org:m"' in page_a and "llm.example-b.org (m, Herkunft unbekannt)" in page_a
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1", "to": "own:custom:llm.example-b.org:m"})
    b.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert sent and sent[0]["answers"]
    # a recipient nobody in the team uses is refused
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1", "to": "own:custom:evil.example:m"})
    with app.app_context():
        stored = db.session.get(Survey, 1).codings["_ai"]
    assert set(ai_access.confirmations(stored)) == {"own:custom:llm.example-a.org:m", "own:custom:llm.example-b.org:m"}


def test_k3_an_old_confirmation_counts_for_the_platform_only():
    old = {"by": 1, "at": "2026-10-10T10:00:00"}
    assert ai_access.confirmed(old) and ai_access.confirmed(old, "openai")
    assert not ai_access.confirmed(old, "own:kiconnect:chat.kiconnect.nrw:gpt-oss-120b")
    assert not ai_access.confirmed(None) and not ai_access.confirmed({}, "openai")


def test_p_s6_paper_confirmation_only_by_owner_or_admin(app, client, survey):
    from tests.exports.test_paper import _setup

    from tests.conftest import own_ai

    _setup(app, client)
    app.config["OPENAI_API_KEY"] = "test"
    own_ai(app)  # handwriting goes only over an own AI access (2026-10-21)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="B", role="editor", position=1))
        db.session.commit()
    b = app.test_client()
    b.get(f"/auth/dev-login?orcid={OTHER}&name=B")
    b.post("/terms/", data={"accept": "1"})
    experiments_on(client, "paper", orcid=OTHER)
    b.post("/surveys/1/paper/ai-informed", data={"informed": "1"})
    with app.app_context():
        assert not (db.session.get(Survey, 1).paper or {}).get("ai_ok")
    client.post("/surveys/1/paper/ai-informed", data={"informed": "1"})
    with app.app_context():
        stored = db.session.get(Survey, 1).paper["ai_ok"]
    (key,) = ai_access.confirmations(stored)
    assert key != "openai" and "llm.example.org" in stored["to"][key]["name"]


# --- K7: generative AI off -------------------------------------------------------------------------------------


def test_k7_ai_off_stops_save_and_check(app, other, monkeypatch):
    uid = _store(app)
    called = []
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: called.append(1) or FakeClient())
    with app.app_context():
        db.session.get(User, uid).ai_enabled = False
        db.session.commit()
    r = other.post("/account/ai/check", follow_redirects=True)
    assert "ausgeschaltet" in r.data.decode()
    other.post("/account/ai/save", data={"provider": "kiconnect", "key": "sk-new-key-1234"})
    assert not called
    page = other.get("/account/ai/").data.decode()
    assert "schickt nichts an KI-Dienste" in page  # the sentence of the switch now holds without exception


def test_k7_ai_off_stops_whisper(app, client, monkeypatch):
    from app import interview_transcribe
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=0", data=WEBM + b"RIFF", content_type="audio/webm")
    app.config.update(WHISPER_URL="http://whisper.intern:8000")
    calls = []
    monkeypatch.setattr(interview_transcribe.requests, "post", lambda *a, **k: calls.append(1))
    with app.app_context():
        db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")).ai_enabled = False
        db.session.commit()
    assert "Transkribieren" not in client.get(f"/guide/{code}/interviews").data.decode()
    r = client.post(f"/guide/{code}/interviews/{rid}/transcribe", follow_redirects=True)
    assert "ausgeschaltet" in r.data.decode() and not calls
    with app.app_context():
        from app.models import Response

        assert not (db.session.get(Response, rid).interview or {}).get("transcript")


# --- K6: statistics summed by the database ---------------------------------------------------------------------


def test_k6_statistics_from_grouped_rows(app, other):
    from datetime import timedelta

    from app import ai_stats

    uid = _uid(app)
    now = utcnow()
    with app.app_context():
        rows = [dict(at=now - timedelta(hours=h), kind="text", purpose=p, model="gpt-5-mini", input_tokens=tin,
                     output_tokens=tin and 10, images=0, ok=ok, source=src, provider=prov, user_id=uid, ms=1)
                for h, p, tin, ok, src, prov in [(1, "text", 100, True, "platform", None),
                                                 (2, "text", None, False, "platform", None),
                                                 (3, "coding", 50, True, "own", "kiconnect"),
                                                 (30, "coding", 70, True, "platform", None),  # yesterday
                                                 (24 * 10, "text", 999, True, "platform", None)]]  # previous week
        db.session.execute(AiCall.__table__.insert(), rows)
        db.session.commit()
    from flask_login import login_user

    with app.test_request_context("/"):
        login_user(db.session.get(User, uid))
        per, sums = ai_access.usage(db.session.get(User, uid))
        assert sums["calls"] == 5 and sums["failed"] == 1 and sums["own"] == 1  # the last 30 days
        assert sums["tokens"] == 110 + 60 + 80 + 1009
        by = {p["key"]: p for p in per}
        assert by["text"]["calls"] == 3 and by["text"]["failed"] == 1 and by["coding"]["own"] == 1
        r = ai_stats.report("week", now=now)
        assert r["total"]["calls"] == 3 and r["total"]["failed"] == 1 and r["total"]["in"] == 170
        assert r["prev"]["calls"] == 1 and r["prev"]["in"] == 999
        assert r["own"]["kiconnect"]["calls"] == 1
        assert sum(sum(b["by"].values()) for b in r["buckets"]) == 3
        assert r["buckets"][-1]["by"] == {"text": 2} and r["buckets"][-2]["by"] == {"coding": 1}
        r = ai_stats.report("day", now=now)
        assert r["total"]["calls"] == 2 and r["prev"]["calls"] == 1
