"""Own AI key (app/ai_access.py, for everyone since 2026-10-10; eigener-ki-schluessel-konzept.md, E1-E7 as recommended):
which client a call takes, the key stored encrypted, chat/completions for every own access but OpenAI itself, the
texts of a failure, the address check against SSRF, the account deletion and the admin statistics. No real AI call:
every client here is a fake."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import ai, ai_access, ai_stats, secrets_box, webservice
from app.extensions import db
from app.models import AiAccess, AiCall, AiUsage, User, utcnow
from tests.conftest import allow, experiments_on

ME = "0000-0000-0000-001X"  # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
KEY = "sk-test-abcdefgh1234"
SCHEMA = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}


class FakeClient:
    """Answers like the OpenAI SDK; records what it was asked. `chat` / `responses`: a function of the keywords that
    returns or raises."""

    def __init__(self, models=("gpt-oss-120b", "qwen3-embedding-8b", "mistral-small-4-119b-2603"), chat=None,
                 responses=None):
        self.calls = []
        outer = self

        class Models:
            def list(self):
                return [SimpleNamespace(id=m) for m in models]

        class Completions:
            def create(self, **kw):
                outer.calls.append(("chat", kw))
                if chat:
                    return chat(kw)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='\n\n{"code": "A"}'),
                                                                finish_reason="stop")],
                                       usage=SimpleNamespace(prompt_tokens=24, completion_tokens=14))

        class Responses:
            def create(self, **kw):
                outer.calls.append(("responses", kw))
                if responses:
                    return responses(kw)
                return SimpleNamespace(output_text=' {"code": "B"}', status="completed", incomplete_details=None,
                                       usage=SimpleNamespace(input_tokens=12, output_tokens=16))

        self.models = Models()
        self.chat = SimpleNamespace(completions=Completions())
        self.responses = Responses()


def _err(status, code):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.org/v1/chat/completions"))
    cls = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
           404: openai.NotFoundError, 429: openai.RateLimitError}[status]
    return cls("provider text with sk-secret", response=response, body={"code": code, "message": "x"})


@pytest.fixture
def other(app):
    """A researcher who is no site admin (the daily limits apply to them), with the feature on."""
    allow(app, OTHER)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    experiments_on(c, "own_ai_key", orcid=OTHER)
    return c


def _uid(app, orcid=OTHER):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def _store(app, provider="kiconnect", model="gpt-oss-120b", orcid=OTHER, base_url=None):
    uid = _uid(app, orcid)
    with app.app_context():
        db.session.add(AiAccess(user_id=uid, provider=provider, base_url=base_url or ai_access.PROVIDERS[provider],
                                key_enc=secrets_box.encrypt(KEY), key_hint=KEY[-4:], text_model=model,
                                models=[model]))
        db.session.commit()
    return uid


def _as(app, uid, fn):
    from flask_login import login_user

    with app.test_request_context():
        login_user(db.session.get(User, uid))
        return fn()


def test_save_stores_the_key_encrypted_and_reads_the_models(app, other, monkeypatch):
    fake = FakeClient()
    seen = {}

    def make(r, base_url=None, key=None):
        seen.update(base_url=base_url, key=key)
        return fake

    monkeypatch.setattr(ai_access, "client", make)
    page = other.get("/account/connections").data.decode()
    assert 'id="ki-zugang"' in page and "KI:connect.nrw" in page and "Eigene Adresse" in page
    r = other.post("/account/ai/save", data={"provider": "kiconnect", "key": f" {KEY} "}, follow_redirects=True)
    assert "Bitte jetzt das Modell wählen" in r.data.decode()
    assert seen == {"base_url": "https://chat.kiconnect.nrw/api/v1", "key": KEY}
    with app.app_context():
        row = db.session.get(AiAccess, _uid(app))
        assert row.key_enc != KEY and KEY not in row.key_enc and secrets_box.decrypt(row.key_enc) == KEY
        assert row.key_hint == "1234"
        assert row.models == ["gpt-oss-120b", "mistral-small-4-119b-2603"]  # the embedding model left out
        from app.timeutil import to_local

        assert row.valid_until == to_local(utcnow()).date() + timedelta(days=ai_access.KICONNECT_DAYS)
        assert row.text_model == ""
    # the model saves itself on its choice (autosave, no button "Modell speichern"; Ralf 2026-10-10)
    page = other.get("/account/connections").data.decode()
    form = page[page.index('action="/account/ai/model"'):]
    form = form[:form.index("</form>")]
    assert "data-autosave-form" in page[page.index('action="/account/ai/model"') - 200:][:400] and "Modell speichern" not in page
    assert "data-autosave-bar" in form
    r = other.post("/account/ai/model", data={"model": "gpt-oss-120b", "autosave": "1"})
    assert r.get_json()["status"] == "saved"
    page = other.get("/account/connections").data.decode()
    assert KEY not in page and "…1234" in page and "rechnet nur in NRW" in page
    r = other.post("/account/ai/model", data={"model": "erfunden", "autosave": "1"})
    assert r.get_json() == {"status": "invalid", "message": "Bitte ein Modell aus der Liste wählen."}
    assert other.post("/account/ai/model", data={"model": "erfunden"}, follow_redirects=True).status_code == 200
    with app.app_context():
        assert db.session.get(AiAccess, _uid(app)).text_model == "gpt-oss-120b"  # only a model of the list
    # replacing without a new key keeps the stored one
    other.post("/account/ai/save", data={"provider": "kiconnect", "key": ""})
    assert seen["key"] == KEY
    # the stored key never goes to a new address without being typed again
    seen.clear()
    page = other.post("/account/ai/save", data={"provider": "openai", "key": ""}, follow_redirects=True).data.decode()
    assert "Bitte den API-Schlüssel einfügen" in page and seen == {}
    with app.app_context():
        assert db.session.get(AiAccess, _uid(app)).provider == "kiconnect"


def test_a_refused_key_is_not_stored(app, other, monkeypatch):
    class Refusing(FakeClient):
        def __init__(self):
            super().__init__()

            class Models:
                def list(self):
                    raise _err(401, "invalid_api_key")

            self.models = Models()

    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: Refusing())
    page = other.post("/account/ai/save", data={"provider": "openai", "key": KEY}, follow_redirects=True).data.decode()
    assert "abgelehnt oder ist abgelaufen" in page and KEY not in page
    with app.app_context():
        assert db.session.get(AiAccess, _uid(app)) is None


def test_the_own_key_needs_no_experimental_feature(app, client, monkeypatch):
    """For everyone since 2026-10-10 (Ralf): no feature switched on, the key is stored and used all the same."""
    fake = FakeClient()
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    assert "own_ai_key" not in __import__("app.experimental", fromlist=["KEYS"]).KEYS
    page = client.get("/account/connections").data.decode()
    assert 'id="ki-zugang"' in page and "experimentell" not in page.split('id="ki-zugang"')[1].split("</section>")[0]
    client.post("/account/ai/save", data={"provider": "kiconnect", "key": KEY})
    client.post("/account/ai/model", data={"model": "gpt-oss-120b"})
    uid = _uid(app, ME)
    _client, model, own = _as(app, uid, ai._text_client)
    assert own is not None and model == "gpt-oss-120b"
    # the stored key can be removed
    client.post("/account/ai/remove")
    with app.app_context():
        assert db.session.get(AiAccess, uid) is None


def test_client_choice_own_before_platform(app, other, monkeypatch):
    app.config["OPENAI_API_KEY"] = "sk-platform"
    uid = _uid(app)
    _client, model, own = _as(app, uid, ai._text_client)
    assert own is None and model == app.config["OPENAI_TEXT_MODEL"]
    assert _as(app, uid, ai_access.shown) == ""  # no own key: nothing to tell apart, no line
    _store(app, model="mistral-small-4-119b-2603")
    made = []
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: made.append(r.user_id) or FakeClient())
    _client, model, own = _as(app, uid, ai._text_client)
    assert own is not None and model == "mistral-small-4-119b-2603" and made == [uid]
    assert _as(app, uid, ai_access.shown) == "KI: eigener Zugang (KI:connect.nrw, mistral-small-4-119b-2603)"
    # the real client: the key and the base URL of the access, no retries, no redirects
    monkeypatch.undo()
    with app.app_context():
        c = ai_access.client(db.session.get(AiAccess, uid))
        assert c.api_key == KEY and str(c.base_url).rstrip("/") == "https://chat.kiconnect.nrw/api/v1"
        assert c.max_retries == 0 and c._client.follow_redirects is False


def test_own_kiconnect_goes_through_chat_completions_with_json_schema(app, other, monkeypatch):
    uid = _store(app)
    fake = FakeClient()
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    text = _as(app, uid, lambda: ai.generate_text("Sei knapp.", "Kodiere", purpose="coding_assign", schema=SCHEMA))
    assert text == '{"code": "A"}'  # trimmed: Qwen and others start with blank lines
    kind, kw = fake.calls[0]
    assert kind == "chat" and kw["model"] == "gpt-oss-120b"
    assert kw["messages"] == [{"role": "system", "content": "Sei knapp."}, {"role": "user", "content": "Kodiere"}]
    assert kw["response_format"] == {"type": "json_schema", "json_schema": {"name": "coding_assign", "schema": SCHEMA,
                                                                           "strict": True}}
    assert "store" not in kw and "max_tokens" not in kw
    with app.app_context():
        call = db.session.scalar(db.select(AiCall).order_by(AiCall.id.desc()))
        assert (call.source, call.provider, call.model, call.ok) == ("own", "kiconnect", "gpt-oss-120b", True)
        assert (call.input_tokens, call.output_tokens) == (24, 14)
        # E7: not the daily limit of Askurious, only the technical one, counted on its own
        kinds = {u.kind: u.count for u in db.session.scalars(db.select(AiUsage).filter_by(user_id=uid))}
        assert kinds == {"own": 1}


def test_own_openai_keeps_the_responses_api_and_store_false(app, other, monkeypatch):
    uid = _store(app, provider="openai", model="gpt-6-luna")
    fake = FakeClient()
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    assert _as(app, uid, lambda: ai.generate_text("I", "P", schema=SCHEMA)) == '{"code": "B"}'
    kind, kw = fake.calls[0]
    assert kind == "responses" and kw["store"] is False and kw["model"] == "gpt-6-luna"
    assert kw["text"]["format"]["schema"] == SCHEMA


def test_schema_refused_falls_back_to_the_schema_in_the_instructions(app, other, monkeypatch):
    uid = _store(app, provider="custom", model="llama", base_url="https://llm.example.org/v1")
    answers = iter([_err(400, "invalid_request"), None])

    def chat(kw):
        e = next(answers)
        if e is not None:
            raise e
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Hier: {"code": "C"}'),
                                                        finish_reason="stop")], usage=None)

    fake = FakeClient(chat=chat)
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    text = _as(app, uid, lambda: ai.generate_text("Sei knapp.", "Kodiere", schema=SCHEMA))
    assert ai.json_answer(text) == {"code": "C"}
    first, second = fake.calls[0][1], fake.calls[1][1]
    assert "response_format" in first and "response_format" not in second
    assert second["messages"][0]["content"].startswith("Sei knapp.") and '"required": ["code"]' in second["messages"][0]["content"]


@pytest.mark.parametrize("status, code, words, stored", [
    (401, "invalid_api_key", "Ihr KI-Schlüssel wurde abgelehnt oder ist abgelaufen", "401 invalid_api_key"),
    (403, "api_access_disabled", "Ihre Nutzergruppe bei KI:connect hat keinen API-Zugang", "403 api_access_disabled"),
    (404, "model_not_found", "Das gewählte Modell gibt es für Ihren Zugang nicht mehr", "404 model_not_found"),
    (429, "quota_exceeded", "Ihr Kontingent beim KI-Anbieter ist erschöpft", "429 quota_exceeded"),
    (400, "unsupported_model", "Das gewählte Modell kann diese Anfrage nicht beantworten", "400 unsupported_model"),
])
def test_failures_over_the_own_key_name_the_own_key(app, other, monkeypatch, status, code, words, stored):
    uid = _store(app)

    def chat(kw):
        raise _err(status, code)

    fake = FakeClient(chat=chat)
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    with pytest.raises(ai.AIError) as e:
        _as(app, uid, lambda: ai.generate_text("I", "P", schema=SCHEMA))
    assert words in str(e.value) and "sk-secret" not in str(e.value) and "Installation" not in str(e.value)
    assert len(fake.calls) == 1  # unsupported_model is not retried without the schema
    with app.app_context():
        assert db.session.get(AiAccess, uid).last_error == stored
        call = db.session.scalar(db.select(AiCall).order_by(AiCall.id.desc()))
        assert call.source == "own" and not call.ok
    if status == 401:  # the banner on "Konto"
        assert "Bitte hier erneuern" in other.get("/account/connections").data.decode()


def test_platform_failures_keep_their_texts(app):
    assert "Schlüssel dieser Installation" in ai._failure("X", _err(401, "x"))
    # 429: the quota used up and too many requests at once are told apart (Ralf, 2026-10-10)
    assert "Kontingent dieser Installation" in ai._failure("X", _err(429, "insufficient_quota"))
    assert "zu viele Anfragen" in ai._failure("X", _err(429, "rate_limit_exceeded"))
    assert "Ihr Kontingent beim KI-Anbieter" in ai._failure("X", _err(429, "quota_exceeded"), own=True)
    assert "Bei Ihrem KI-Anbieter gehen gerade zu viele Anfragen ein" in ai._failure("X", _err(429, "x"), own=True)


def test_connection_check(app, other, monkeypatch):
    uid = _store(app)
    fake = FakeClient()
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: fake)
    page = other.post("/account/ai/check", follow_redirects=True).data.decode()
    assert "Verbindung steht, Modell antwortet" in page
    assert fake.calls[0][0] == "chat" and fake.calls[0][1]["response_format"]["type"] == "json_schema"
    with app.app_context():
        row = db.session.get(AiAccess, uid)
        assert row.checked_at is not None and row.last_error is None

    def chat(kw):
        raise _err(401, "invalid_api_key")

    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: FakeClient(chat=chat))
    page = other.post("/account/ai/check", follow_redirects=True).data.decode()
    assert "abgelehnt oder ist abgelaufen" in page
    with app.app_context():
        assert db.session.get(AiAccess, uid).last_error == "401 invalid_api_key"


def test_save_and_check_are_rate_limited(app, other, monkeypatch):
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: FakeClient())
    ai_access._saves.clear()
    for _ in range(ai_access.SAVE_LIMIT):
        other.post("/account/ai/save", data={"provider": "kiconnect", "key": KEY})
    page = other.post("/account/ai/save", data={"provider": "kiconnect", "key": KEY}, follow_redirects=True).data.decode()
    assert "Zu viele Versuche" in page
    ai_access._saves.clear()


@pytest.mark.parametrize("url", [
    "http://llm.example.org/v1",            # not https
    "https://llm.example.org:8443/v1",      # not port 443
    "https://user:pw@llm.example.org/v1",   # credentials
    "https://llm.example.org/v1?x=1",       # parameters
    "https://127.0.0.1/v1",
    "https://10.1.2.3/v1",
    "https://172.16.0.5/v1",
    "https://192.168.1.10/v1",
    "https://169.254.169.254/v1",           # metadata service
    "https://[::1]/v1",
    "https://[fc00::1]/v1",
    "ftp://llm.example.org",
    "",
])
def test_base_url_refuses_what_is_not_public_https(app, url):
    with app.app_context():
        with pytest.raises(ai_access.AccessError):
            ai_access.check_url(url)


def test_base_url_checks_where_the_name_points_and_admins_may_allow_hosts(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(webservice, "_resolve", lambda host, port, timeout: [(2, 1, 6, "", ("10.0.0.7", 443))])
        with pytest.raises(ai_access.AccessError):
            ai_access.check_url("https://intern.example.org/v1")  # a public-looking name on a private address
        monkeypatch.setattr(webservice, "_resolve", lambda host, port, timeout: [(2, 1, 6, "", ("93.184.215.14", 443))])
        assert ai_access.check_url("https://llm.example.org/v1/") == "https://llm.example.org/v1"
        app.config["AI_OWN_URL_HOSTS"] = ["llm.intern"]
        assert ai_access.check_url("https://llm.intern:8000/v1") == "https://llm.intern:8000/v1"


def test_a_typed_address_is_checked_again_before_each_call(app, other, monkeypatch):
    uid = _store(app, provider="custom", model="m", base_url="https://llm.example.org/v1")
    monkeypatch.setattr(webservice, "_resolve", lambda host, port, timeout: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ai.AIError) as e:
        _as(app, uid, lambda: ai.generate_text("I", "P"))
    assert "Netzwerkregeln" in str(e.value)
    # the presets are fixed addresses: no lookup
    with app.app_context():
        assert ai_access.base_url_of(SimpleNamespace(provider="kiconnect", base_url="https://127.0.0.1")) == \
            "https://chat.kiconnect.nrw/api/v1"


def test_the_technical_limit_applies_with_an_own_key(app, other, monkeypatch):
    uid = _store(app)
    monkeypatch.setattr(ai_access, "client", lambda r, base_url=None, key=None: FakeClient())
    app.config["OWN_AI_CALLS_PER_DAY"] = 1
    app.config["AI_TEXTS_PER_DAY"] = 0
    _as(app, uid, lambda: ai.generate_text("I", "P"))
    with pytest.raises(ai.AIError) as e:
        _as(app, uid, lambda: ai.generate_text("I", "P"))
    assert "technische Tageslimit" in str(e.value)


def test_images_keep_the_platform_key(app, other, monkeypatch):
    uid = _store(app, provider="openai", model="gpt-6-luna")
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: pytest.fail("own client for an image"))
    used = []

    class Platform:
        class images:
            @staticmethod
            def generate(**kw):
                used.append(kw["model"])
                return SimpleNamespace(data=[SimpleNamespace(b64_json="aGk=")], usage=None)

    monkeypatch.setattr(ai, "_client", lambda: Platform)
    assert _as(app, uid, lambda: ai.generate_image("Ein Haus")) == "aGk="
    assert used == [app.config["OPENAI_IMAGE_MODEL"]]


def test_the_access_line_at_every_ai_function(app, other, monkeypatch):
    """The line "KI: …" from the coding help also at the other AI functions (scales, consent, review, live, images);
    images always name the platform (ai.generate_image takes the installation's key). Nothing while the flag is off."""
    from tests.conftest import add

    other.post("/surveys/new", data={"title": "Zugang", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        from app.models import Survey
        survey = db.session.scalar(db.select(Survey).filter_by(title="Zugang"))
        sid = survey.code
    other.post(f"/surveys/{sid}/editor/blocks/2/items", data={"type": "image"})
    with app.app_context():
        from app.models import Item
        image = db.session.scalar(db.select(Item).filter_by(type="image")).id
    other.post("/presenter/new", data={"title": "Live", "language": "de-du"})
    with app.app_context():
        live = db.session.scalar(db.select(Survey).filter_by(title="Live")).code
    pages = {"editor": f"/surveys/{sid}/editor/", "consent": f"/surveys/{sid}/editor/items/1",
             "live": f"/presenter/{live}/ai", "image": f"/surveys/{sid}/editor/items/{image}"}
    platform, own = "KI: Zugang der Plattform", "KI: eigener Zugang (KI:connect.nrw, gpt-oss-120b)"
    for name, url in pages.items():  # no own key: no line (everything goes over the platform)
        assert "ai-access-line" not in other.get(url).data.decode(), name
    _store(app)
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: FakeClient())
    for name, url in pages.items():
        page = other.get(url).data.decode()
        assert own in page, name
    page = other.get(pages["image"]).data.decode()
    assert platform in page  # "Erzeugen" of the image: always the platform
    assert _as(app, _uid(app), lambda: ai_access.shown(platform=True)) == platform
    other.post("/account/ai/switch", data={})  # generative AI off ("KI-Nutzung"): no line anywhere
    for name, url in pages.items():
        assert "ai-access-line" not in other.get(url).data.decode(), name
    assert _as(app, _uid(app), lambda: ai_access.shown(platform=True)) == ""


def test_the_sentences_name_the_real_recipient(app, other, monkeypatch):
    """"… geht an OpenAI" only for the platform's access; with an own key the provider, model and origin, also in the
    paragraph of the coding help for the participants (coding.ai_notice)."""
    from app import coding

    uid = _uid(app)
    assert _as(app, uid, ai_access.recipient) == "OpenAI"
    other.post("/presenter/new", data={"title": "Live", "language": "de-du"})
    with app.app_context():
        from app.models import Survey
        live = db.session.scalar(db.select(Survey).filter_by(title="Live")).code
    assert "Die Beschreibung geht an OpenAI." in other.get(f"/presenter/{live}/ai").data.decode()
    _store(app)
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: FakeClient())
    to = "KI:connect.nrw (gpt-oss-120b, rechnet nur in NRW)"
    assert _as(app, uid, ai_access.recipient) == to
    page = other.get(f"/presenter/{live}/ai").data.decode()
    assert f"Die Beschreibung geht an {to}." in page and "OpenAI" not in page.split("live-ai")[1]
    notice = _as(app, uid, lambda: coding.ai_notice("de", own=True))
    assert to in notice and "OpenAI" not in notice and "30 Tage" not in notice
    assert "OpenAI" in coding.ai_notice("de")
    with app.app_context():
        row = db.session.get(AiAccess, uid)
        row.provider, row.base_url, row.text_model = "custom", "https://llm.example.org/v1", "llama-4"
        db.session.commit()
    assert _as(app, uid, ai_access.recipient) == "llm.example.org (llama-4, Herkunft unbekannt)"
    with app.app_context():
        db.session.get(AiAccess, uid).text_model = "<b>x</b>"  # a typed model name is escaped, also in the |safe text
        db.session.commit()
    other.post("/surveys/new", data={"title": "Skala", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        sid = db.session.scalar(db.select(Survey).filter_by(title="Skala")).code
    page = other.get(f"/surveys/{sid}/editor/").data.decode()
    assert "Die Anfrage geht an llm.example.org (&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
    with app.app_context():
        row = db.session.get(AiAccess, uid)
        row.provider, row.text_model = "openai", "gpt-6-luna"
        db.session.commit()
    assert _as(app, uid, ai_access.recipient) == "OpenAI (gpt-6-luna)"


def test_account_deletion_removes_the_key(app, other):
    uid = _store(app)
    page = other.get("/account/delete").data.decode()
    assert "Der eigene KI-Schlüssel" in page
    other.post("/account/delete", data={"name": "Kollegin"})
    with app.app_context():
        assert db.session.get(User, uid) is None and db.session.get(AiAccess, uid) is None


def test_admin_statistics_show_own_calls_apart_and_without_cost(app, client):
    with app.app_context():
        db.session.add_all([
            AiCall(kind="text", purpose="consent", model="gpt-6-luna", input_tokens=10, output_tokens=5),
            AiCall(kind="text", purpose="coding_assign", model="gpt-oss-120b", input_tokens=100, output_tokens=50,
                   source="own", provider="kiconnect"),
            AiCall(kind="text", purpose="coding_assign", model="gpt-oss-120b", ok=False, source="own", provider="kiconnect"),
        ])
        db.session.commit()
        with app.test_request_context():
            r = ai_stats.report("week")
            assert r["total"]["calls"] == 1 and "gpt-oss-120b" not in r["by_model"]
            assert r["own"]["kiconnect"]["calls"] == 2 and r["own"]["kiconnect"]["failed"] == 1
            assert "gpt-oss-120b" not in r["text_models"]  # no price row for a model of an own key
    page = client.get("/admin/ai").data.decode()
    assert "Eigene KI-Zugänge" in page and "KI:connect.nrw" in page


def test_origin_labels(app):
    with app.test_request_context():
        assert "NRW" in ai_access.origin("kiconnect", "gpt-oss-120b")
        assert "NRW" in ai_access.origin("kiconnect", "mistral-small-4-119b-2603")
        assert "EU" in ai_access.origin("kiconnect", "gpt-6-sol")
        assert "unbekannt" in ai_access.origin("kiconnect", "qwen3.8-27b")
        assert ai_access.origin("openai", "gpt-6-luna") == "OpenAI"


def test_model_name_with_markup_is_refused(app, other):
    """Audit 2026-10-13, XS-4: the model id is put into sentences of the editor; a typed one (no list of models) or one
    from a provider's /models with < > " ' ` is not kept."""
    uid = _store(app)
    with app.app_context():
        row = db.session.scalar(db.select(AiAccess).filter_by(user_id=uid))
        row.models = []
        db.session.commit()
    other.post("/account/ai/model", data={"model": "<img src=https://x.example>", "autosave": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(AiAccess).filter_by(user_id=uid)).text_model == "gpt-oss-120b"
    assert not ai_access.MODEL_RE.match('a"b') and not ai_access.MODEL_RE.match("a<b")
    assert ai_access.MODEL_RE.match("meta-llama/Llama-3.1-8B-Instruct:latest")


def test_the_rwth_key_guide_follows_the_page_language(app, client):
    """The ITC guide for KI:connect.NRW keys in the language of the page (Ralf, 2026-10-22)."""
    guide = "help.itc.rwth-aachen.de/{}service/1808737e10424937b76e564ed15d8028/article/4f07ebbbc8c4477a8db9baa441494941/"
    page = client.get("/account/ai/").data.decode()
    assert guide.format("") in page and guide.format("en/") not in page
    with app.app_context():
        user = db.session.execute(db.select(User).filter_by(orcid=ME)).scalar_one()
        user.display = {**(user.display or {}), "lang": "en"}
        db.session.commit()
    assert guide.format("en/") in client.get("/account/ai/").data.decode()
