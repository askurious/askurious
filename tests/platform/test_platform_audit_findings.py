"""Platform: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from app.models import RateHit  # noqa: E402
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits, Response, _running, _start as _start__audit_2026_10_06_b, db, limits  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_c import _fresh_count, WEBM, _participant, _send, _start as _start__audit_2026_10_06_c, _voice  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_d import _no_failures_from_other_tests, ME as ME__audit_2026_10_06_d, OTHER, SimpleNamespace, User, _as, login_user, notify, twofactor  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_reste import ME as ME__audit_2026_10_06_reste, UserLogin, _may, _uid  # noqa: F401
from tests.helpers.audit_cr8_a import app  # noqa: F401
from tests.helpers.audit_cr8_b import _config, migrations, needs_sqlite, os, pytest, sqlite3  # noqa: F401
from tests.helpers.audit_cr8_c import event, text  # noqa: F401
from tests.helpers.audit_cr8_e import _last_item  # noqa: F401
from tests.helpers.audit_cr8_h import ai  # noqa: F401
from tests.helpers.audit_cr8_i import Item  # noqa: F401
from tests.helpers.audit_cr8_j import inspect  # noqa: F401
from tests.helpers.audit_review2_reste import E  # noqa: F401
from tests.helpers.audit_review_medium import export  # noqa: F401


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_a_sender_is_an_ipv6_slash_64(app):
    def sender(addr):
        with app.test_request_context(environ_base={"REMOTE_ADDR": addr}):
            return limits._sender()

    assert sender("2001:db8:1:2::1") == sender("2001:db8:1:2:ffff:ffff:ffff:fffe")
    assert sender("2001:db8:1:2::1") != sender("2001:db8:1:3::1")
    assert sender("::ffff:203.0.113.5") == sender("203.0.113.5") != sender("203.0.113.6")
    assert sender("::1") is None and sender("::ffff:127.0.0.1") is None and sender("") is None


def test_one_sender_is_limited_per_survey(app, client, survey):
    code = _running(app, client)
    app.config.update(SURVEY_SENDER_LIMIT=2, START_LIMIT=300)
    assert [_start__audit_2026_10_06_b(app, code, "203.0.113.7")[1].status_code for _ in range(3)] == [302, 302, 429]
    assert _start__audit_2026_10_06_b(app, code, "203.0.113.8")[1].status_code == 302         # others go on
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1)) == 3


# --- from tests/audits/test_audit_2026_10_06_c.py: Audit of 2026-10-06, package C (uploads and resources): voice messages written only when the page goes on, with a ---


def test_a_recording_is_bounded_by_the_maximum_length(app, client, survey):
    from app.items import voice

    assert voice.limit(10) < voice.limit(60) < voice.limit(300) <= voice.MAX_BYTES
    _voice(app, client, max_seconds="10")
    url, uid = _start__audit_2026_10_06_c(app, client)
    p = _participant(app, url)
    long = b"\x1a\x45\xdf\xa3" + b"\x00" * voice.limit(10)
    r = _send(p, url, data=long)
    assert r.status_code == 200 and "konnte nicht angenommen werden" in r.data.decode()
    assert _send(p, url, data=WEBM).status_code == 302


# --- from tests/audits/test_audit_2026_10_06_d.py: Security audit 2026-10-06, package D "Konten": the right to create surveys of invited people (S11), blocking ends ---


def test_admin_rights_outside_the_administration_ask_for_the_code(app, client):
    app.config["ADMIN_TOTP"] = True
    assert client.get("/feedback/").status_code == 200                          # ORCID exemption
    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    r = client.get("/feedback/")
    assert r.status_code == 302 and "/account/second-factor/" in r.headers["Location"]
    assert client.post("/feedback/1/status", data={"status": "done"}).status_code == 302
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert other.get("/feedback/").status_code == 403
    from app import links

    with app.test_request_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME__audit_2026_10_06_d))
        login_user(admin)
        assert not twofactor.admin_rights(admin)                             # no code given in this browser
        assert links.label_error("abc", admin)                               # short labels need the code too
        app.config["ADMIN_TOTP"] = False
        from flask import session

        session[twofactor.SIGNED_IN_VIA] = twofactor.VIA_ORCID
        assert twofactor.admin_rights(admin) and links.label_error("abc", admin) is None


def test_slack_links_use_the_configured_address(app, monkeypatch):
    from app import submissions

    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append(text))
    app.config["PUBLIC_BASE_URL"] = "https://queryous.example.org"
    s = SimpleNamespace(id=7, user=None, fields={}, construct=SimpleNamespace(name="Skala"))
    with app.test_request_context("/", base_url="http://evil.example"):
        submissions.notify(s)
    assert "https://queryous.example.org/admin/" in sent[0] and "evil.example" not in sent[0]


# --- from tests/audits/test_audit_2026_10_06_reste.py: Security audit 2026-10-06, the rests S17 and H5: the setting "eigene Befragungen anlegen" on an account has three ---


def test_s17_site_admins_always_create(app, client):
    with app.app_context():
        db.session.merge(UserLogin(user_id=_uid(app, ME__audit_2026_10_06_reste), kinds=None))  # their page in the administration has no form
        db.session.commit()
    assert _may(app, ME__audit_2026_10_06_reste)


# --- from tests/audits/test_audit_small3.py: Small things from the audit of 2026-10-01 (package 7): patterns that keep a server busy, a content policy, ---


def test_content_policy_without_a_place_search(app):
    from app import create_app

    import tempfile
    from pathlib import Path

    from tests.conftest import _config

    folder = Path(tempfile.mkdtemp())

    class NoSearch(_config(folder)):
        MAP_SEARCH_URL = ""

    other = create_app(NoSearch)
    policy = other.test_client().get("/").headers["Content-Security-Policy"]
    assert "connect-src 'self';" in policy
    with other.app_context():
        db.engine.dispose()


def test_svg_files_keep_their_own_stricter_policy(app):
    import os

    folder = os.path.join(app.config["UPLOAD_FOLDER"], "abc")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "logo.svg"), "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>')
    policy = app.test_client().get("/media/abc/logo.svg").headers["Content-Security-Policy"]
    assert policy.startswith("default-src 'none'") and "sandbox" in policy


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_e11_acbc_scenarios_and_guide_categories_are_read(app):
    from app import items

    sim = {"scenarios": [{"name": "Basis", "products": [{"levels": {"a": "b"}, "price": 10}], "none": True}], "prev": None}
    config, unfit, unknown = items.REGISTRY["acbc"].import_config({"sim": sim})
    assert config["sim"]["scenarios"][0]["name"] == "Basis" and "sim" not in unknown
    category = {"definition": "Was gemeint ist", "delimit": "Was nicht", "aspects": {"a1": "Aspekt"}}
    config, unfit, unknown = items.REGISTRY["guide_question"].import_config({"category": category})
    assert config["category"] == category and not unfit and not unknown
    config, unfit, unknown = items.REGISTRY["guide_question"].import_config({"category": {"aspects": {"a b": 3}}})
    assert "category" in unfit


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


@needs_sqlite
def test_unknown_version_stops_without_copy(app, tmp_path, monkeypatch):
    from app import create_app

    calls = []
    monkeypatch.setattr(migrations, "backup", lambda a: calls.append(1) or "copy")
    con = sqlite3.connect(tmp_path / "test.db")
    con.execute("UPDATE alembic_version SET version_num = '0099'")  # newer code ran here, then was rolled back
    con.commit()
    con.close()
    for _ in range(2):
        with pytest.raises(migrations.SchemaMismatch, match="0099"):
            create_app(_config(tmp_path))
    assert calls == []
    with app.app_context():
        assert migrations.known(migrations.head()) and not migrations.known("0099")


def test_one_copy_per_step_and_hour(app, tmp_path, monkeypatch):
    made = []

    def fake(a):
        path = os.path.join(a.instance_path, "backups", f"copy-{len(made)}-vor-migration.db")
        open(path, "wb").close()
        made.append(path)
        return path

    monkeypatch.setattr(migrations, "backup", fake)
    os.makedirs(os.path.join(app.instance_path, "backups"), exist_ok=True)
    try:
        first = migrations._backup_once(app, "0016", "0017")
        assert migrations._backup_once(app, "0016", "0017") == first and len(made) == 1
        migrations._backup_once(app, "0017", "0018")  # another step gets its own copy
        assert len(made) == 2
        os.remove(first)  # the copy is gone: a new one
        migrations._backup_once(app, "0016", "0017")
        assert len(made) == 3
    finally:
        for name in os.listdir(os.path.join(app.instance_path, "backups")):
            if name.startswith((".schritt-0016-0017", ".schritt-0017-0018", "copy-")):
                os.remove(os.path.join(app.instance_path, "backups", name))


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_the_daily_counter_commits_for_everyone(app, client):
    from flask_login import login_user

    from app import limits
    from app.models import User

    for limit in (0, 5):
        app.config["AI_TEXTS_PER_DAY"] = limit
        with app.test_request_context():
            user = db.session.scalar(db.select(User))
            login_user(user)
            db.session.execute(text("UPDATE survey SET title = title"))                   # the request's work so far
            commits = []
            event.listen(db.session(), "after_commit", lambda s: commits.append(1))
            assert limits.charge_ai("text") is None
            assert commits, (limit, user.is_admin)


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_f4_navigating_posts_get_the_target_instead_of_the_page(app, client, survey):
    plain = client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    assert plain.status_code == 302
    asked = client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"}, headers={"X-Navigate": "1"})
    assert asked.status_code == 200 and asked.headers["X-Navigate"] == "1" and "Location" not in asked.headers
    assert asked.get_json()["location"].split("?")[0] == plain.headers["Location"].split("?")[0].replace(
        f"/items/{_last_item(app) - 1}", f"/items/{_last_item(app)}")
    js = open(os.path.join(app.root_path, "static", "app.js"), encoding="utf-8").read()
    assert js.count("postAndGo(") >= 3 and "window.location.href = r.url; // server" not in js


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i1_no_retries_and_limits_below_the_proxy(app, monkeypatch):
    with app.app_context():
        app.config["OPENAI_API_KEY"] = "sk-test"
        client = ai._client()
        assert client.max_retries == 0 and client.timeout == ai.TEXT_TIMEOUT == 90
        seen = []

        class Fake:
            class images:
                def generate(**kw):
                    seen.append(kw.get("timeout"))
                    raise ai.AIError("stop")

        monkeypatch.setattr(ai, "_client", lambda: Fake)
        monkeypatch.setattr(ai, "_charge", lambda kind, **k: None)
        with pytest.raises(ai.AIError):
            ai.generate_image("x")
        assert seen == [ai.IMAGE_TIMEOUT] and ai.TEXT_TIMEOUT < ai.IMAGE_TIMEOUT


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_g11_cfg_fills_missing_settings_only(app):
    with app.app_context():
        item = Item(type="rating", config={"points": 7})
        assert item.cfg["points"] == 7 and item.cfg["style"] == "smileys" and item.config == {"points": 7}
        full = Item(type="rating", config=item.kind.default_config())
        assert full.cfg is full.config
        # content made anew for each item (rows with fresh ids) is not invented for a row without it
        assert "rows" not in Item(type="slider", config={}).cfg


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_k10_every_counter_goes_through_take(app, monkeypatch):
    source = inspect.getsource(limits)
    assert source.count("popleft") == 1                       # the sliding window is written once, in _take
    # wrong codes and deletion codes count in the database since the security audit 2026-10-13 (TN-2, _take_shared);
    # the counters in memory still all go through _take
    app.config.update(CODE_LIMIT=2, CODE_WINDOW=60, ERASE_LIMIT=1, ERASE_WINDOW=60)
    with app.test_request_context(environ_base={"REMOTE_ADDR": "198.51.100.7"}):
        assert limits.allow_code_try(count=False) and limits.allow_code_try(count=False)   # asking counts nothing
        assert not db.session.scalar(db.select(db.func.count(RateHit.id)))
        assert limits.allow_code_try() and limits.allow_code_try()
        assert not limits.allow_code_try(count=False) and not limits.allow_code_try()
        assert limits.allow_erase() and not limits.allow_erase()
        clock = limits.time.time()
        monkeypatch.setattr(limits.time, "time", lambda: clock + 61)
        assert limits.allow_code_try() and limits.allow_erase()  # the window moves on


# --- from tests/audits/test_review2_reste.py: Second code review 2026-10-03, package 7 (the rest): no inline scripts and handlers, so the CSP allows scripts ---


def test_scripts_only_as_files_of_this_site(app, client, survey):
    for c, url in ((app.test_client(), "/"), (client, "/dashboard/"), (client, f"{E}/preview")):
        policy = c.get(url, follow_redirects=True).headers["Content-Security-Policy"]
        rules = dict(part.strip().split(" ", 1) for part in policy.split(";"))
        assert rules["script-src"] == "'self'"


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


@pytest.mark.parametrize("title, slug", [
    ("Umfrage Größe", "umfrage-größe"), ("Badanie łączności", "badanie-lacznosci"), ("Anket şehir", "anket-sehir"),
    ("Опрос", "survey"), ("调查 Umfrage", "umfrage"), ("", "survey"), ("Été à Paris!", "été-à-paris")])
def test_file_names_fit_into_a_header(title, slug):
    assert export.slug(title) == slug
    export.slug(title).encode("latin-1")
