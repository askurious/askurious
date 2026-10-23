"""Code review of 2026-10-02, the small findings on accounts, start-up, files of surveys, AI and translations
(K31 to K45 and D3 of the report)."""
from tests.accounts.password_setup import confirmed_password
import base64
import copy
import io
import os
import json
import re
import shutil
import sqlite3

import pytest
from werkzeug.datastructures import MultiDict

from app import accounts, ai, auth, constructs, limits, media, orcid_api, passwords, svg_safe, transfer
from app.extensions import db
from app.models import AiUsage, Asset, Construct, Item, Survey, SurveyMember, TranslationMark, User
from tests.conftest import add, allow, fill_consent, needs_sqlite
from tests.editor.test_editor import PNG
from tests.accounts.test_passwords import GOOD, MAIL, OTHER, _as, guest  # noqa: F401  (guest: fixture)
from tests.exports.test_transfer import _file, _folders, _upload
from tests.participation.test_translations import ask, choice, fields

ME = "0000-0000-0000-001X"


# --- K31: only a sign-in that asked for the password counts as fresh -------------------------------------------


def _id_token(auth_time, sub=OTHER):
    """An ID token as ORCID sends it with the scope openid, signed with the tests' key (tests/helpers/orcid_keys.py)."""
    from tests.helpers import orcid_keys

    return orcid_keys.id_token(sub, auth_time)


def _orcid_answers(app, monkeypatch, orcid=OTHER, name="Kollegin", signed_in_ago=0):
    """ORCID's token address. signed_in_ago: how many seconds ago the person signed in at ORCID (0: just now, as
    after "prompt=login"; None: the answer names no time)."""
    import time

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            token = {} if signed_in_ago is None else {"id_token": _id_token(time.time() - signed_in_ago, orcid)}
            return {"orcid": orcid, "name": name, **token}

    app.config.update(ORCID_CLIENT_ID="client")

    allow(app, orcid)
    from tests.helpers import orcid_keys

    orcid_keys.serve(monkeypatch)
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Answer)


def _round_trip(c, start="/auth/login"):
    r = c.get(start)
    with c.session_transaction() as s:
        state = s["oauth_state"]
    c.get(f"/auth/orcid/callback?state={state}&code=x")
    return r.headers["Location"]


def test_a_plain_orcid_sign_in_is_not_a_fresh_one(app, client, monkeypatch):
    """It was: ORCID passes a browser that is still signed in there straight through, so at a computer left open
    one click on "Mit ORCID anmelden" was enough to add a password to the account or to delete it."""
    _orcid_answers(app, monkeypatch, signed_in_ago=3600)          # still signed in at ORCID: passed through
    c = app.test_client()
    assert "prompt=login" not in _round_trip(c)
    c.post("/terms/", data={"accept": "1"})
    with c.session_transaction() as s:
        assert "signed_in_at" not in s
    page = c.get("/account/").data.decode()
    assert "Neu mit ORCID anmelden" in page and "fresh=1" in page
    data = {"email": "kollegin@example.org", "password": GOOD, "repeat": GOOD}
    assert "liegt zu lange zurück" in c.post("/account/password", data=data, follow_redirects=True).data.decode()
    assert "liegt zu lange zurück" in c.post("/account/delete", data={"name": "Kollegin"}, follow_redirects=True).data.decode()
    # the way the page offers: ORCID is asked to demand the password, and that round trip counts
    _orcid_answers(app, monkeypatch)                              # and ORCID did
    assert "prompt=login" in _round_trip(c, "/auth/login?fresh=1&next=/account/")
    with c.session_transaction() as s:
        assert "signed_in_at" in s and "oauth_fresh" not in s
    assert "Passwort aktiviert" in confirmed_password(app, c, data).data.decode()


def test_a_plain_orcid_sign_in_does_not_inherit_an_earlier_fresh_one(app, client, monkeypatch):
    _orcid_answers(app, monkeypatch)
    c = app.test_client()
    _round_trip(c, "/auth/login?fresh=1")
    with c.session_transaction() as s:
        assert "signed_in_at" in s
    # the flag belongs to one round trip; the next plain one starts without it, and without the old time
    _orcid_answers(app, monkeypatch, signed_in_ago=3600)
    _round_trip(c)
    with c.session_transaction() as s:
        assert "signed_in_at" not in s
    with app.app_context():
        assert not passwords.has_password(db.session.scalar(db.select(User).filter_by(orcid=OTHER)))


def test_a_password_typed_just_now_is_fresh(app, client, guest):  # noqa: F811
    c = app.test_client()
    assert c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 302
    with c.session_transaction() as s:
        assert "signed_in_at" in s
    with app.test_request_context():
        assert accounts.FRESH_SECONDS == 15 * 60


# --- K32: a self-chosen address is no identity -----------------------------------------------------------------


def test_people_search_by_address_finds_only_accounts_an_admin_made(app, client, survey, guest):  # noqa: F811
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    # an ORCID account picks any free address as its sign-in name: here the one of a colleague who has no account
    data = {"email": "chefin@example.org", "password": GOOD, "repeat": GOOD}
    assert "Passwort aktiviert" in confirmed_password(app, other, data).data.decode()
    page = client.get("/surveys/1?q=chefin@example.org").data.decode()
    assert "Kollegin" not in page and OTHER not in page
    assert "Gast" in client.get(f"/surveys/1?q={MAIL}").data.decode()     # the address a site admin entered


# --- K33: the share links are for everyone ---------------------------------------------------------------------


def test_share_links_do_not_ask_a_researcher_for_the_terms(app, client):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Neu")                       # signed in, terms not accepted
    assert "/terms" in c.get("/dashboard/").headers["Location"]
    for path in ("/q/tok123", "/p/tok123", "/d/tok123"):
        r = c.get(path)
        assert "/terms" not in r.headers.get("Location", ""), path
        assert r.status_code == app.test_client().get(path).status_code, path   # as for someone without an account


# --- K34: the admin lists keep only their own state ------------------------------------------------------------


def test_admin_list_ignores_foreign_query_keys(client):
    assert client.get("/admin/people?_anchor=x").status_code == 200              # was a server error
    for query in ("_external=1&_scheme=javascript:alert(1)//", "_method=POST", "next=//evil.example"):
        r = client.get(f"/admin/people?{query}&f_sort=name&f_dir=desc")
        assert r.status_code == 200
        links = re.findall(r'class="sort-link[^"]*" href="([^"]*)"', r.data.decode())
        assert links and all(h.startswith("/admin/people?") for h in links)
        assert not any("_external" in h or "_scheme" in h or "_method" in h or "evil" in h for h in links)
        assert any("f_sort=name&amp;f_dir=asc" in h for h in links), links   # its own state stays


# --- K35: development key and dev login only on the developer's own computer -----------------------------------


def _settings(app, **changes):
    return type("C", (), {**{k: v for k, v in app.config.items() if k.isupper()}, **changes})


def test_local_addresses():
    from config import is_local_host, url_host

    for url in ("http://localhost:5060", "http://127.0.0.1:5092", "http://[::1]:5000", "http://queryous.test",
                "https://app.localhost:8443", "http://LOCALHOST"):
        assert is_local_host(url_host(url)), url
    for url in ("https://queryous.de", "http://survey.example", "http://localhost.evil.example", "http://test", "",
                "http://192.168.0.5:5000", "kein-url", "http://[::1"):
        assert not is_local_host(url_host(url)), url


def test_refuses_to_start_with_development_settings_unless_the_address_is_local(app):
    """Before, only an https address was refused: a server at http://…, or one whose address was mistyped, started
    with the known session key and with a sign-in without password."""
    from app import create_app
    from config import DEV_SECRET_KEY

    for base in ("http://survey.example", "http://203.0.113.7:5000", ""):
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            create_app(_settings(app, PUBLIC_BASE_URL=base, SECRET_KEY=DEV_SECRET_KEY, DEV_LOGIN=False))
        with pytest.raises(RuntimeError, match="DEV_LOGIN"):
            create_app(_settings(app, PUBLIC_BASE_URL=base, SECRET_KEY="x" * 32, DEV_LOGIN=True))
        # a random key (one character over and over is refused since the audit of 2026-10-18, CFG-02)
        create_app(_settings(app, PUBLIC_BASE_URL=base, SECRET_KEY=__import__("secrets").token_hex(32), DEV_LOGIN=False))
    # the preview servers of the developer and the tests: plain http on the own computer, with both
    for base in ("http://localhost:5092", "http://127.0.0.1:5060", "http://[::1]:5060", "http://queryous.test"):
        create_app(_settings(app, PUBLIC_BASE_URL=base, SECRET_KEY=DEV_SECRET_KEY, DEV_LOGIN=True))


def test_dev_login_only_answers_under_a_local_name(app):
    """PUBLIC_BASE_URL is what the operator wrote down; the name a request came in under is what is the case."""
    path = f"/auth/dev-login?orcid={ME}&name=X"
    app.config["SECRET_KEY"] = "x" * 32  # with the development key the app would not answer under these names at all
    for host in ("queryous.example", "queryous.example:443", "localhost.evil.example", "203.0.113.7:5000"):
        c = app.test_client()
        assert c.get(path, headers={"Host": host}).status_code == 404, host
        assert c.get("/dashboard/").status_code == 302                     # not signed in
    for host in ("localhost", "localhost:5092", "127.0.0.1:5060", "[::1]:5060", "queryous.test"):
        assert app.test_client().get(path, headers={"Host": host}).status_code == 302, host


# --- K36: the written decision says what the code does ---------------------------------------------------------


def test_decisions_name_the_password_pause_as_it_is(app):
    import os

    with open(os.path.join(os.path.dirname(app.root_path), "decisions.md"), encoding="utf-8") as f:
        text = f.read()
    assert passwords.MAX_FAILURES == 10 and passwords.ACCOUNT_FAILURES == 50
    assert "Zehn Fehlversuche eines Absenders" in text and "nach 50 Fehlversuchen aller Absender" in text
    assert "Zehn Fehlversuche pausieren das Konto 15 Minuten, gezählt in der Datenbank" not in text


# --- K37: a deeply nested SVG is refused, not a server error ----------------------------------------------------

SVG_HEAD = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
RECT = b'<rect width="1" height="1"/>'


def _nested(n):
    return SVG_HEAD + b"<g>" * n + RECT + b"</g>" * n + b"</svg>"


def test_deeply_nested_svg_is_refused(app, client, survey):
    assert svg_safe.clean(_nested(20))[1:] == (10, 10)                     # what a drawing program writes
    for data in (_nested(1500), _nested(svg_safe.MAX_DEPTH)):
        with pytest.raises(svg_safe.Refused, match="verschachtelt"):
            svg_safe.clean(data)
    # the same for a chain of <use>, each pointing at the next: followed by recursion as well
    chain = b"".join(b'<use id="u%d" href="#u%d"/>' % (n, n + 1) for n in range(3000)) + b'<rect id="u3000" width="1" height="1"/>'
    with pytest.raises(svg_safe.Refused, match="verschachtelt"):
        svg_safe.clean(SVG_HEAD + chain + b"</svg>")
    # and the upload of a logo answers with that message
    page = client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(_nested(1500)), "l.svg"), "variant": "light", "rights": "1"},
                       content_type="multipart/form-data", follow_redirects=True)
    assert page.status_code == 200 and "zu tief verschachtelt" in page.data.decode()
    with app.app_context():
        with pytest.raises(ValueError, match="verschachtelt"):
            media.save_logo(db.session.get(Survey, 1), _nested(1500))


# --- K38: two server processes start at once after a deploy with a new construct ---------------------------------


@needs_sqlite
def test_seeding_survives_another_process_that_was_faster(app, monkeypatch):
    seed = next(s for s in constructs.SEEDS if s["key"] == "bfi10")
    spec, calls = seed["spec"], []
    with app.app_context():
        path = db.engine.url.database
        # the construct is "new": this process does not find it ...
        db.session.execute(db.update(Construct).where(Construct.key == "bfi10").values(key="bfi10-elsewhere"))
        db.session.commit()

        def other_process_first():
            # ... and while it builds the row, the other process commits its own
            if not calls:
                con = sqlite3.connect(path)
                con.execute("UPDATE construct SET key = 'bfi10' WHERE key = 'bfi10-elsewhere'")
                con.commit()
                con.close()
            calls.append(1)
            return spec()

        monkeypatch.setitem(seed, "spec", other_process_first)
        constructs.ensure_seed()                                            # was: IntegrityError, a failed start
        assert calls and db.session.scalar(db.select(db.func.count(Construct.id)).filter_by(key="bfi10")) == 1
        assert db.session.scalar(db.select(Construct).filter_by(key="bfi10")).seed_version == seed["seed_version"]


# --- K39: a value of the wrong kind in a survey file is refused or set right --------------------------------------


def _post_file(client, data, title):
    """Upload, then answer the preview with this title: the answer's response (302 once the survey is made)."""
    import re

    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "b.json")},
                       content_type="multipart/form-data").data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    return client.post("/surveys/new/import", data={"token": token, "title-0": title})


def test_lists_and_dicts_where_a_file_should_name_a_choice(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    base = _file(client)
    wrong = ([], ["a"], {}, {"a": 1})
    cases = []

    def case(change):
        for value in wrong:
            data = copy.deepcopy(base)
            change(data, value)
            cases.append(data)

    case(lambda d, v: d["survey"].update(tempo_hint=v))
    case(lambda d, v: d["survey"].update(tempo_dose=v))
    case(lambda d, v: d["survey"].update(panel={"params": [{"name": "src", "mode": "store"}], "sign": {"alg": v, "enc": v, "base": v}}))
    case(lambda d, v: d["survey"].update(panel={"params": [{"name": "src", "mode": "store"}], "rules": [
        {"item": "p:src", "outcome": v, "value": "a", "url": "https://x.example", "op": v, "mode": v}]}))
    case(lambda d, v: d["survey"].update(panel={"params": [{"name": "src", "mode": "store"}], "rules": [
        {"item": "p:src", "outcome": "completed", "value": "a", "url": "https://x.example", "op": v, "mode": v}]}))
    case(lambda d, v: d.update(theme={"preset": v, "colors": {"accent": "#112233"}}))
    case(lambda d, v: d.update(theme={"preset": "custom", "colors": {"accent": "#112233"}, "font": v, "radius": v, "maps": v}))
    case(lambda d, v: d.update(quotas=[{"id": 1, "code": "q", "name": "Q", "mode": v, "pick": v, "cells": []}]))
    case(lambda d, v: d.update(boards=[{"name": "b", "tiles": [{"key": "text:abc123", "kind": v, "text": "x"},
                                                              {"key": "field:count", "view": v}]}]))
    case(lambda d, v: d["flow"]["children"].append({"t": "end", "id": "e5", "kind": v, "text": {}, "link_mode": v}))
    case(lambda d, v: d["flow"]["children"].append({"t": "end", "id": "e5", "cases": [{"id": "c1", "when": v},
                                                                                      {"id": "c2", "when": "screenout", "link_mode": v}]}))
    case(lambda d, v: d["flow"]["children"].append({"t": "end", "id": "e5", "kind": "quotafull", "quotas": [], "rule": v,
                                                    "links": {"1": {"mode": v}}}))
    for n, data in enumerate(cases):
        r = _post_file(client, data, f"F{n}")
        assert r.status_code == 302, (n, r.status_code)                     # read in with the standard value
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1 + len(cases)
        new = db.session.scalars(db.select(Survey).order_by(Survey.id.desc())).first()
        assert new.tempo_hint in ("off", "all", "blocks")


def test_two_marks_that_differ_by_a_control_character_only(app, client, survey):
    fill_consent(client)
    data = _file(client)
    data["translation_marks"] = [{"unit": "end", "lang": "en", "path": "thanks", "source": "a"},
                                 {"unit": "end", "lang": "en", "path": "thanks\u0001", "source": "b"}]
    assert _post_file(client, data, "Marken").status_code == 302           # was an IntegrityError
    with app.app_context():
        marks = db.session.scalars(db.select(TranslationMark).filter_by(survey_id=2, unit="end")).all()
        assert [(m.path, m.source) for m in marks] == [("thanks", "a")]


def test_an_unforeseen_wrong_value_is_refused_and_leaves_nothing(app, client, survey, monkeypatch):
    """The net under the single checks: an error that a value of the file causes somewhere deeper."""
    add(client, "image")
    client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")
    data = _file(client)

    def stumbles(*args):
        return {}[["not", "a", "name"]]                                      # TypeError: unhashable type

    monkeypatch.setattr(transfer, "_boards", stumbles)
    before = _folders(app)
    page = _upload(client, data, title="Kaputt")
    assert "Die Datei lässt sich nicht einlesen" in page and "unhashable" not in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1 and db.session.scalar(db.select(db.func.count(Asset.id))) == 1
    assert _folders(app) == before
    # what is not the file's fault stays an error (the disk, a busy database)
    monkeypatch.setattr(transfer, "_boards", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with app.test_request_context():
        with pytest.raises(OSError):
            transfer.create(data, db.session.get(User, 1), "Platte voll")
    assert _folders(app) == before


# --- K40, revised 2026-10-20 (pre-test finding 13): a generated image keeps the review the file gives it, with a note


def test_imported_ai_image_keeps_its_review(app, client, survey):
    add(client, "image")
    data = _file(client)
    image = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "image")
    data["files"] = {"x.png": base64.b64encode(PNG).decode(), "y.png": base64.b64encode(PNG).decode()}
    image["images"] = [{"file": "x.png", "key": "", "source": "ai", "prompt": "p", "model": "m", "status": "approved", "style": ""},
                       {"file": "y.png", "key": "", "source": "upload", "prompt": "", "model": "", "status": "approved", "style": ""}]
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "b.json")},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    page = client.post("/surveys/new/import", data={"token": token, "title-0": "KI-Bild"}, follow_redirects=True).data.decode()
    assert "1 KI-Bild(er) mit ihrer Freigabe aus der Datei übernommen" in page
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="KI-Bild"))
        assets = {a.source: a.status for b in new.blocks for i in b.items for a in i.assets}
        assert assets == {"ai": Asset.APPROVED, "upload": Asset.APPROVED}


# --- K41: what OpenAI answers goes to the log, not to the page -------------------------------------------------


def test_ai_errors_show_a_fixed_sentence(app, client, monkeypatch, caplog):
    import httpx
    import openai

    secret = "Incorrect API key provided: sk-proj-abc***xyz for organization org-Geheim123"

    class Status(Exception):
        def __init__(self, status, code=""):
            super().__init__(secret)
            self.status_code, self.code = status, code

    errors = {"nicht erreichbar": openai.APIConnectionError(message=secret, request=httpx.Request("POST", "https://api.openai.com/v1")),
              "Schlüssel dieser Installation abgelehnt": Status(401), "Kontingent": Status(429, "insufficient_quota"),
              "zu viele Anfragen": Status(429),  # quota and rate limit told apart (2026-10-10)
              "wegen ihres Inhalts abgelehnt": Status(400, "content_policy_violation"),
              "Bleibt der Fehler": Status(500)}

    for expected, error in errors.items():
        class Fake:
            class responses:
                @staticmethod
                def create(error=error, **kw):
                    raise error

            class images:
                @staticmethod
                def generate(error=error, **kw):
                    raise error

        monkeypatch.setattr(ai, "_client", lambda Fake=Fake: Fake)
        with app.test_request_context():
            from flask_login import login_user

            from app.models import User

            login_user(db.session.scalar(db.select(User)))  # no AI call without a sign-in (audit 2026-10-13, KR-2)
            for call, what in ((lambda: ai.generate_text("i", "p"), "Textgenerierung"), (lambda: ai.generate_image("p"), "Bildgenerierung")):
                with pytest.raises(ai.AIError) as e:
                    call()
                shown = str(e.value)
                assert shown.startswith(f"{what} fehlgeschlagen: ") and expected in shown, shown
                assert "sk-proj" not in shown and "org-Geheim123" not in shown and "Incorrect" not in shown
    assert "org-Geheim123" in caplog.text                                   # the operator still finds the detail


# --- K42: the first AI call of the day in two requests at once --------------------------------------------------


def test_first_ai_call_of_the_day_twice_at_once(app, client, monkeypatch):
    from flask_login import login_user

    app.config.update(AI_TEXTS_PER_DAY=5)
    c = app.test_client()
    _as(c, OTHER, "Kollegin")
    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        login_user(user)
        assert limits.charge_ai("text") is None                             # the other request: its row is there
        real, state = db.session.scalar, {"first": True}

        def blind(statement, *args, **kw):
            # this request looked before the other one wrote: it sees no row
            if state["first"] and "ai_usage" in str(statement):
                state["first"] = False
                return None
            return real(statement, *args, **kw)

        monkeypatch.setattr(db.session, "scalar", blind)
        user.affiliation = "was die Anfrage vorher getan hat"              # must survive the step that is taken back
        assert limits.charge_ai("text") is None                             # was an IntegrityError
        monkeypatch.undo()
        db.session.rollback()
        assert db.session.scalars(db.select(AiUsage.count).filter_by(user_id=user.id, kind="text")).all() == [2]
        assert db.session.get(User, user.id).affiliation == "was die Anfrage vorher getan hat"


# --- K43: duplicating copies the files before it writes ---------------------------------------------------------


@needs_sqlite
def test_duplicate_copies_files_before_the_database_is_written(app, client, survey, monkeypatch):
    import builtins

    from tests.editor.test_duplicate import _copy, build

    build(app, client)
    with app.app_context():
        path = db.engine.url.database
    real, copied = builtins.open, []

    def write_and_look(target, mode="r", *args, **kw):
        if mode != "xb":
            return real(target, mode, *args, **kw)
        # another process (a participant sending a page) gets the write lock at once: nothing is open here
        con = sqlite3.connect(path, timeout=0)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
            copied.append(True)
        except sqlite3.OperationalError:
            copied.append(False)
        finally:
            con.close()
        return real(target, mode, *args, **kw)

    # Observe the actual destination write, after quota reads, whatever bounded copy implementation is used.
    monkeypatch.setattr(builtins, "open", write_and_look)
    assert "ist als Entwurf angelegt" in _copy(client)
    assert len(copied) >= 2 and all(copied), copied                         # the image of the question and the logo
    with app.app_context():
        new = db.session.get(Survey, 2)
        assert [i.code for b in new.blocks for i in b.items] == [i.code for b in db.session.get(Survey, 1).blocks for i in b.items]
        assert len([a for b in new.blocks for i in b.items for a in i.assets]) == 1 and new.theme["logo"]["file"]


# --- K44: the translation page ---------------------------------------------------------------------------------


def _viewer(app, client):
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Gast", "role": "viewer"})
    c = app.test_client()
    _as(c, OTHER, "Gast")
    return c


def _marks(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(TranslationMark.id)))


def test_opening_the_translation_page_writes_nothing_for_a_viewer(app, client, survey):
    choice(client)
    with app.app_context():                                                # a translation from before the page existed
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "en": {"question": "How often?"}}
        db.session.commit()
    viewer = _viewer(app, client)
    page = viewer.get("/surveys/1/translation/")
    assert page.status_code == 200 and "How often?" in page.data.decode() and _marks(app) == 0
    viewer.get("/surveys/1/design")                                        # the check of the design reads the same
    assert _marks(app) == 0
    assert client.get("/surveys/1/translation/").status_code == 200 and _marks(app) >= 1   # who may edit: as before
    assert fields(app)[0][("item:2", "question")]["status"] == "ok"


def test_validated_wording_is_not_written_into_a_survey_in_the_field_by_looking(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # item 2: de and en validated

    def without_english(status):
        with app.app_context():                                            # the language came later than the construct
            item, s = db.session.get(Item, 2), db.session.get(Survey, 1)
            item.texts = {k: v for k, v in item.texts.items() if k != "en"}
            s.status = status
            db.session.commit()

    def stored():
        with app.app_context():
            return "en" in db.session.get(Item, 2).texts

    viewer = _viewer(app, client)
    for status, who in ((Survey.DRAFT, viewer), (Survey.RUNNING, client), (Survey.CLOSED, client), (Survey.RUNNING, viewer)):
        without_english(status)
        page = who.get("/surveys/1/translation/").data.decode()
        assert "Validierte Fassung aus „Konstrukte“" in page and "Test statement 1 of BFI10" in page   # shown ...
        assert not stored(), status                                         # ... not stored
    without_english(Survey.DRAFT)
    client.get("/surveys/1/translation/")                                  # a draft, opened by someone who may edit
    assert stored()


def _ai(monkeypatch, translate=lambda e: "EN " + e["text"]):
    seen = []

    def fake(instructions, prompt, purpose="text", item_type=None):
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        seen.append([e["text"] for e in entries])
        return json.dumps({e["id"]: translate(e) for e in entries})

    monkeypatch.setattr(ai, "generate_text", fake)
    return seen


def test_ai_leaves_a_translation_a_person_wrote(app, client, survey, monkeypatch):
    choice(client)
    seen = _ai(monkeypatch)
    f, _ = fields(app)
    option = next(p for (u, p) in f if u == "item:2" and p.startswith("options."))
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "How often do you use AI?"})
    assert client.post("/surveys/1/translation/ai?lang=en").json["left"] == 0     # the rest: by the AI, unconfirmed
    assert fields(app)[0][("item:2", option)]["status"] == "ai"
    # a typo is fixed in the source of the question; the source of an option changes too
    ask(client, app, "Wie oft nutzen Sie KI im Beruf?")
    with app.app_context():
        item = db.session.get(Item, 2)
        texts = copy.deepcopy(item.texts)
        texts["de"]["options"][option.split(".")[1]] = "niemals"
        item.texts = texts
        db.session.commit()
    f, _ = fields(app)
    assert f[("item:2", "question")]["status"] == "changed" and f[("item:2", option)]["status"] == "changed"
    page = client.get("/surveys/1/translation/").data.decode()
    assert 'data-tr-count="open">1<' in page                               # the number on the button: what the AI would do
    del seen[:]
    r = client.post("/surveys/1/translation/ai?lang=en")
    assert r.json == {"done": 1, "left": 0} and seen == [["niemals"]]
    f, _ = fields(app)
    assert f[("item:2", "question")]["target"] == "How often do you use AI?" and f[("item:2", "question")]["status"] == "changed"
    assert f[("item:2", option)]["target"] == "EN niemals" and f[("item:2", option)]["status"] == "ai"


def test_ai_translation_must_keep_the_placeholders(app, client, survey, monkeypatch):
    choice(client, question="Wie oft nutzen Sie {{tool}} oder {{other|etwas anderes}}?")
    answers = {"lost": lambda e: "How often do you use it?" if "{{" in e["text"] else "EN",
               "renamed": lambda e: "How often do you use {{werkzeug}} or {{other|something else}}?" if "{{" in e["text"] else "EN"}
    for name, translate in answers.items():
        _ai(monkeypatch, translate)
        r = client.post("/surveys/1/translation/ai?lang=en")
        f, _ = fields(app)
        assert f[("item:2", "question")]["status"] == "missing" and f[("item:2", "question")]["target"] == "", name
        if name == "lost":
            assert r.json["left"] == 1 and r.json["done"] >= 2             # counted as not translated
    _ai(monkeypatch, lambda e: "How often do you use {{tool}} or {{ other | something else }}?" if "{{" in e["text"] else "EN")
    assert client.post("/surveys/1/translation/ai?lang=en").json == {"done": 1, "left": 0}
    assert fields(app)[0][("item:2", "question")]["status"] == "ai"


# --- D3: the AI draft of the consent gets no names ----------------------------------------------------------------


def test_ai_consent_draft_sends_stand_ins_for_the_people(app, client, survey, monkeypatch):
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Dr. Erika Musterfrau", "role": "editor"})
    with app.app_context():
        other = db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER))
        mid = other.id
    client.post("/surveys/1/consent-facts", data=MultiDict([
        ("min_age", "18"), ("researchers", ME), ("researchers", OTHER), ("aff_1", "RWTH Aachen"), (f"aff_{mid}", "Uni Musterstadt"),
        ("contact_email", "studie@example.org")]))
    seen = {}

    def fake(instructions, prompt, **kw):
        seen["instructions"], seen["prompt"] = instructions, prompt
        return ("**Kontakt:** Verantwortlich sind [[NAME_1]] ([[AFFILIATION_1]]) und [[NAME_2]] ([[AFFILIATION_2]]). "
                "Fragen an [[CONTACT_EMAIL]] oder nochmals [[CONTACT_EMAIL]].")

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "question": "Einwilligung", "body": "", "agree": "Ja",
                                                   "disagree": "Nein", "draft": "ai"})
    for personal in ("Philipp", "Musterfrau", "RWTH Aachen", "Uni Musterstadt", "studie@example.org"):
        assert personal not in seen["prompt"], personal
    assert '"name": "[[NAME_2]]"' in seen["prompt"] and '"contact_email": "[[CONTACT_EMAIL]]"' in seen["prompt"]
    assert "[[NAME_1]] ([[AFFILIATION_1]]), [[NAME_2]] ([[AFFILIATION_2]])" in seen["prompt"]   # the template for orientation
    assert "[[NAME_1]]" in seen["instructions"]
    with app.app_context():
        body = db.session.get(Item, 1).texts["de"]["body"]
    assert body == ("**Kontakt:** Verantwortlich sind Philipp (RWTH Aachen) und Dr. Erika Musterfrau (Uni Musterstadt). "
                    "Fragen an studie@example.org oder nochmals studie@example.org.")
    # an answer that leaves the stand-ins out is still a draft
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: "**Kontakt:** Bei Fragen wenden Sie sich an uns.")
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "question": "Einwilligung", "body": "", "agree": "Ja",
                                                   "disagree": "Nein", "draft": "ai"})
    with app.app_context():
        assert db.session.get(Item, 1).texts["de"]["body"] == "**Kontakt:** Bei Fragen wenden Sie sich an uns."
        # the standard text itself (no AI) names the people as before
        assert "Philipp (RWTH Aachen), Dr. Erika Musterfrau (Uni Musterstadt)" in db.session.get(Item, 1).kind.template_body(
            db.session.get(Item, 1), "de")


# --- the two rests of K31 and K35 (Ralf, 2026-10-02: close them) ---------------------------------------------------


def test_fresh_needs_orcid_to_say_that_the_person_signed_in_just_now(app, client, monkeypatch):
    """ "prompt=login" travels through the browser: at a computer left open it can be taken out of the address,
    and ORCID then passes the signed-in browser through. The round trip counts as fresh only if ORCID's answer says
    the person signed in there after it began (auth_time of the ID token, scope openid)."""
    _orcid_answers(app, monkeypatch, signed_in_ago=3600)          # signed in at ORCID an hour ago, passed through
    c = app.test_client()
    assert "scope=openid" in _round_trip(c, "/auth/login?fresh=1")
    c.post("/terms/", data={"accept": "1"})
    with c.session_transaction() as s:
        assert "signed_in_at" not in s and "_user_id" in s       # signed in, but not freshly
    assert "nicht neu abgefragt" in c.get("/account/", follow_redirects=True).data.decode()
    _orcid_answers(app, monkeypatch, signed_in_ago=None)          # an answer without a time proves nothing
    _round_trip(c, "/auth/login?fresh=1")
    with c.session_transaction() as s:
        assert "signed_in_at" not in s
    _orcid_answers(app, monkeypatch)                              # the password was typed at ORCID just now
    _round_trip(c, "/auth/login?fresh=1")
    with c.session_transaction() as s:
        assert "signed_in_at" in s
    # a plain sign-in asks for the time as well, without "prompt=login" (2026-10-15: see test_fresh_sign_in_refill.py)
    plain = _round_trip(app.test_client())
    assert "scope=openid" in plain and "prompt=login" not in plain


def test_with_the_development_key_the_app_answers_only_under_local_names(app):
    """A server whose .env lacks PUBLIC_BASE_URL has the local default there and starts with the known key:
    under any other name than this computer's own it answers nothing."""
    from config import DEV_SECRET_KEY

    # set here, not taken from the environment: a developer's .env may name a key of its own
    app.config["SECRET_KEY"] = DEV_SECRET_KEY
    for host in ("queryous.example", "192.168.1.20:5060", "localhost.evil.example"):
        answer = app.test_client().get("/", headers={"Host": host})
        assert answer.status_code == 503 and "SECRET_KEY" in answer.data.decode(), host
        assert app.test_client().get("/auth/dev-login", headers={"Host": host}).status_code == 503
    for host in ("localhost:5060", "127.0.0.1:5092", "queryous.test"):
        assert app.test_client().get("/", headers={"Host": host}).status_code == 200, host
    app.config["SECRET_KEY"] = "x" * 32                             # a key of its own: any name
    assert app.test_client().get("/", headers={"Host": "queryous.example"}).status_code == 200


# --- K44: AI text in a running survey is asked about first (Ralf, 2026-10-02) ---------------------------------------


def test_ai_translation_in_the_field_is_asked_about(app, client, survey):
    """In the field what the AI writes is read by participants at once: the button asks before (app.js reads
    data-tr-live). In a draft nothing is asked."""
    choice(client)
    page = client.get("/surveys/1/translation/?lang=en").data.decode()
    assert "data-tr-ai" in page and "data-tr-live" not in page
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    assert "data-tr-ai data-tr-live" in client.get("/surveys/1/translation/?lang=en").data.decode()
    script = open(os.path.join(app.static_folder, "app.js"), encoding="utf-8").read()
    assert 'hasAttribute("data-tr-live")' in script and "sofort für Teilnehmende sichtbar" in script
