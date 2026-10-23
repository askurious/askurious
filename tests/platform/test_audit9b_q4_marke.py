"""Brand per domain (audit 2026-10-09b, package Q4): M1 the name only in the platform's texts, never in content or
downloads; M2 the status page names the brand's own addresses; B2 certificates of every name watched; M3
webservice.own_hosts knows the brands' names; P8 the brand found once per request, BRANDS parsed once."""
from markupsafe import Markup

from app import brand
from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK, fill_consent, fill_questions

HEAD = {"Host": "polltergeist.de"}
QUESTION = "Wie gefällt Ihnen Askurious?"
TITLE = "Askurious-Studie"


def _branded(app):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", PARTICIPANT_BASE_URL="",
                      BRANDS="polltergeist.de=Polltergeist", SECRET_KEY="x" * 32)


def _survey_under_the_brand(app):
    """A survey whose question and title carry the main name, made by a person signed in at localhost (where the
    development sign-in works), with that session also under polltergeist.de."""
    _branded(app)
    client = app.test_client()
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    client.post("/terms/", data={"accept": "1"})
    client.post("/surveys/new", data={"title": "Test", "languages": ["de"], "default_language": "de"})
    survey = 1
    fill_consent(client)
    client.post(f"/surveys/{survey}/editor/blocks/2/items", data={"type": "text_input"})
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.title = TITLE
        for item in [i for b in s.blocks for i in b.items if i.type == "text_input"]:
            item.texts = {**(item.texts or {}), "de": {**(item.texts or {}).get("de", {}), "question": QUESTION}}
        db.session.commit()
    name = app.config.get("SESSION_COOKIE_NAME") or "session"
    client.set_cookie(name, client.get_cookie(name, domain="localhost").value, domain="polltergeist.de")
    return client, survey


def test_content_keeps_its_words_under_the_brand(app):
    c, sid = _survey_under_the_brand(app)
    html = c.get(f"/surveys/{sid}/editor", headers=HEAD, follow_redirects=True).data.decode()
    assert QUESTION in html and TITLE in html                              # the researcher's words, as written
    assert 'data-brand="Polltergeist"' in html and "Polltergeist.de" in html  # the platform's own: the brand's
    assert "Wie gefällt Ihnen Polltergeist" not in html and "Polltergeist-Studie" not in html
    csv = c.get(f"/surveys/{sid}/export/codebook.csv", headers=HEAD).data.decode("utf-8-sig")
    assert QUESTION in csv and "Polltergeist" not in csv                   # a download is never rewritten
    data = c.get(f"/surveys/{sid}/export/survey.json", headers=HEAD).data.decode()
    assert TITLE in data and "Polltergeist" not in data
    # the participation page under ask.polltergeist.de
    fill_questions(app, sid)
    c.post(f"/surveys/{sid}/start", data={}, headers=HEAD)
    with app.app_context():
        code = db.session.get(Survey, sid).code
    p = app.test_client()
    ask = {"Host": "ask.polltergeist.de"}
    p.get(f"/s/{code}", headers=ask)
    p.post(f"/s/{code}", data=CONSENT_OK, headers=ask)
    html = p.get(f"/s/{code}", headers=ask).data.decode()
    assert QUESTION in html and "Ihnen Polltergeist" not in html and 'data-brand="Polltergeist"' in html


def test_texts_swap_the_name_before_their_values(app):
    _branded(app)
    from app.lang import de_en
    from app.ui_i18n import tr

    with app.test_request_context("/", headers=HEAD):
        assert de_en("Mit Askurious: {t}", "With Askurious: {t}", t=TITLE) == "Mit Polltergeist: " + TITLE
        assert tr("en", "Mit Askurious: {t}", "With Askurious: {t}", t=TITLE) == "With Polltergeist: " + TITLE
        marked = de_en(Markup("<b>Askurious</b> {t}"), Markup("<b>Askurious</b> {t}"), t="<i>")
        assert isinstance(marked, Markup) and marked == "<b>Polltergeist</b> &lt;i&gt;"   # values still escaped
        from flask import render_template_string

        assert render_template_string("Askurious, {{ x }}", x=TITLE) == "Polltergeist, " + TITLE
    with app.test_request_context("/", headers={"Host": "askurious.de"}):
        assert render_template_string("Askurious, {{ x }}", x=TITLE) == "Askurious, " + TITLE


def test_downloads_keep_the_main_name(app):
    _branded(app)
    rules = {r.endpoint: r for r in app.url_map.iter_rules()}
    for endpoint in ("surveys.export_csv", "surveys.export_file", "surveys.export_zip", "surveys.export_docx",
                     "surveys.export_spss", "surveys.export_methods", "dashboard.export", "interview.version_file"):
        assert brand.is_download(rules[endpoint]), endpoint
    for endpoint in ("editor.index", "public.index", "live.results_json", "public.robots", "static"):
        assert not brand.is_download(rules[endpoint]), endpoint
    with app.test_request_context("/surveys/1/export/data.csv", headers=HEAD):
        from flask import request

        request.url_rule = rules["surveys.export_csv"]
        request.environ.pop(brand._KEY, None)
        assert brand.name() == "Askurious" and brand.apply("Askurious") == "Askurious"
        assert brand.public_base() == "https://polltergeist.de"         # its links stay on the brand's domain


def test_the_brand_is_found_once_per_request(app, monkeypatch):
    _branded(app)
    from app.lang import de_en

    calls = []
    real = brand._match
    monkeypatch.setattr(brand, "_match", lambda host: calls.append(host) or real(host))
    with app.test_request_context("/", headers=HEAD):
        for _ in range(50):
            de_en("Askurious", "Askurious")
        assert brand.name() == "Polltergeist" and len(calls) == 1
    before = brand._parsed.cache_info().misses
    for _ in range(20):
        brand.parse("polltergeist.de=Polltergeist")
    assert brand._parsed.cache_info().misses - before <= 1


def test_own_hosts_know_every_brand(app):
    _branded(app)
    app.config.update(PARTICIPANT_BASE_URL="https://ask.askurious.de", STATUS_URL="https://status.askurious.de/")
    from app import webservice

    with app.app_context():
        names = webservice.own_hosts()
    for host in ("askurious.de", "ask.askurious.de", "status.askurious.de", "polltergeist.de", "ask.polltergeist.de",
                 "status.polltergeist.de"):
        assert host in names, host


def test_status_checks_for_each_brand():
    from app.status_node import brand_checks, shown

    checks = [("participant-a", "http", "http://participant-a:8000/healthz"),
              ("web", "https", "https://ask.askurious.de/healthz"), ("cert", "tls", "ask.askurious.de:443"),
              ("brand-participant", "http", "http://brand-participant:8000/healthz")]
    out = brand_checks(checks, "askurious.de", [("polltergeist.de", "POLLtergeist")], ["queryous.de"])
    by = {k: t for k, _kind, t in out}
    assert by["cert"] == "ask.askurious.de|askurious.de|status.askurious.de|queryous.de:443"
    assert by["web@polltergeist.de"] == "https://ask.polltergeist.de/healthz"
    assert by["cert@polltergeist.de"] == "ask.polltergeist.de|polltergeist.de|status.polltergeist.de:443"
    assert "brand-participant@polltergeist.de" not in by and "participant-a@polltergeist.de" not in by
    keys = [k for k, _kind, _t in out]
    assert shown(keys, None, set(), {"db"}) == ["participant-a", "web", "cert", "brand-participant"]
    assert shown(keys, "polltergeist.de", set(), {"db"}) == ["participant-a", "web@polltergeist.de",
                                                              "cert@polltergeist.de", "brand-participant"]
    # a brand with containers of its own: its checks, and its own web and certificate
    assert shown(keys, "polltergeist.de", {"polltergeist.de"}, {"db"}) == ["brand-participant", "web@polltergeist.de",
                                                                         "cert@polltergeist.de"]


def test_the_certificate_check_takes_the_name_with_fewest_days(monkeypatch):
    from app import status_node

    days = {"ask.askurious.de": 60, "askurious.de": 40, "status.askurious.de": 5}
    monkeypatch.setattr(status_node, "_cert_days", lambda host, port: (days[host], False))
    ok, _ms, detail = status_node.check("tls", "ask.askurious.de|askurious.de|status.askurious.de:443")
    assert not ok and detail == "5 status.askurious.de"
    days["status.askurious.de"] = 30
    assert status_node.check("tls", "ask.askurious.de|askurious.de|status.askurious.de:443")[0]
    assert status_node.check("tls", "ask.askurious.de:443")[2] == "60"                  # one name: as before

    def fails(host, port):
        raise ConnectionRefusedError

    monkeypatch.setattr(status_node, "_cert_days", fails)
    assert status_node.check("tls", "a.de|b.de:443")[2] == "unreachable a.de"


def test_status_page_names_the_brands_addresses(monkeypatch, tmp_path):
    from app import status_node

    monkeypatch.setattr(status_node, "check", lambda kind, target: (True, 3, "40" if kind == "tls" else ""))
    monkeypatch.setenv("BRANDS", "polltergeist.de=POLLtergeist")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://askurious.de")
    monkeypatch.setenv("STATUS_CHECKS", "participant-a=http:http://participant-a:8000/healthz,"
                                        "web=https:https://ask.askurious.de/healthz,cert=tls:ask.askurious.de:443")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    monkeypatch.setenv("STATUS_EVERY", "3600")
    app = status_node.create()
    store = status_node.Store(str(tmp_path / "status.db"))
    status_node.run_checks(store, status_node.brand_checks(
        status_node.parse_checks(status_node.os.environ["STATUS_CHECKS"]), "askurious.de",
        [("polltergeist.de", "POLLtergeist")]))
    c = app.test_client()
    html = c.get("/", headers={"Host": "status.polltergeist.de"}).data.decode()
    assert "Teilnahme (ask.polltergeist.de)" in html and "nginx) für ask.polltergeist.de" in html
    assert "askurious.de" not in html and "noch 40 Tage gültig" in html
    html = c.get("/", headers={"Host": "status.askurious.de"}).data.decode()
    assert "Teilnahme (ask.askurious.de)" in html and "polltergeist" not in html.lower()
