"""Audit 2026-10-10, package U5 "Gruppen und Zugänge": join links rest while their maker may not make them (A1), the
use counter and the sign-in limit cannot be overrun (A4), lead links can be revoked (A5), a join link in the session
ends at sign-out and password sign-in (A6), token downloads and waves take a place for heavy evaluations (A2, P3),
waves name a survey only to who may open it (A3/T2), blog drafts only after the second factor (A8/R3), //host links
are outside (A9), the blog loads no texts for lists (A7/P6) and the AI draft page works with AInstein on (C3)."""
import re

from app import heavy, joinlinks
from app.auth import _may_sign_in
from app.extensions import db
from app.models import BlogImage, BlogPost, GroupJoinLink, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.conftest import experiments_on
from tests.editor.test_join_links import FOURTH, ME, OTHER, THIRD, _admin_group, _as, _make
from tests.conftest import CREATE_ALL


# --- A1: a join link rests while its maker may not make it -------------------------------------------------------

def _user(orcid):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _login_row(user):
    row = db.session.get(UserLogin, user.id)
    if row is None:
        row = UserLogin(user_id=user.id)
        db.session.add(row)
    return row


def test_join_link_rests_while_its_lead_may_not_make_it(app, client):
    gid = _admin_group(client)
    token, _page = _make(client, gid, uses=5)
    guest = app.test_client()
    _as(guest, THIRD, "Dritte")
    # the lead loses the right to create surveys: the link rests, kept, with a message
    with app.app_context():
        _login_row(_user(OTHER)).kinds = None
        db.session.commit()
    page = guest.post(f"/join/{token}", follow_redirects=True).data.decode()
    assert "zurzeit ausgesetzt" in page
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=THIRD)) is None
        assert db.session.scalar(db.select(GroupJoinLink)).uses == 0                    # kept, nothing counted
    assert "ausgesetzt" in client.get(f"/groups/{gid}").data.decode()                  # the lead sees why
    # with ORCID: the waiting token makes nobody a member, so the iD still cannot sign in
    with app.test_request_context():
        from flask import session

        from app import secrets_box

        session[joinlinks.SESSION_KEY] = {"box": secrets_box.encrypt(token), "at": __import__("time").time()}
        assert joinlinks.accept_after_orcid(FOURTH, "Vierte") is None
        assert not _may_sign_in(FOURTH)
    # the right comes back: the link works again
    with app.app_context():
        _login_row(_user(OTHER)).kinds = CREATE_ALL
        db.session.commit()
    assert "Sie sind jetzt in der Gruppe" in guest.post(f"/join/{token}", follow_redirects=True).data.decode()
    # Blocking revokes the credential permanently (audit 2026-10-13 AU-2).
    with app.app_context():
        from app import accounts
        accounts.set_blocked(_user(OTHER), True, _user(ME))
        db.session.commit()
        assert db.session.scalar(db.select(GroupJoinLink)) is None
    fourth = app.test_client()
    _as(fourth, FOURTH, "Vierte")
    assert fourth.post(f"/join/{token}").status_code in (302, 404)
    with app.app_context():
        accounts.set_blocked(_user(OTHER), False, _user(ME))
        db.session.commit()
        assert db.session.scalar(db.select(GroupJoinLink)) is None


def test_lead_link_rests_when_its_admin_is_no_admin_any_more(app, client):
    page = client.post("/admin/groups/new", data={"name": "Ohne Leitung", "lead": "", "lead_link": "1",
                                                  "members_create": "1"}, follow_redirects=True).data.decode()
    token = re.search(r'id="lead-link-new" value="([^"]+)"', page).group(1).split("/join/")[1]
    app.config["ADMIN_ORCIDS"] = []
    guest = app.test_client()
    _as(guest, THIRD, "Dritte")
    assert "zurzeit ausgesetzt" in guest.post(f"/join/{token}", follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(orcid=THIRD)) is None


# --- A4: the counter and the sign-in limit ---------------------------------------------------------------------------

def test_use_counter_is_taken_in_the_database(app, client):
    gid = _admin_group(client)
    _make(client, gid, uses=1)
    with app.test_request_context():
        link = db.session.scalar(db.select(GroupJoinLink))
        # another request took the last place after this one checked the link (valid() before join()): join() counts
        # in the database, where the place is gone, and lets nobody in
        db.session.execute(db.update(GroupJoinLink).values(uses=1))
        db.session.commit()
        goal, said = joinlinks.join(link, orcid=THIRD, name="Dritte")
        assert said and "aufgebraucht" in said
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=THIRD)) is None
        assert db.session.scalar(db.select(GroupJoinLink)).uses == 1


def test_sign_in_limit_counts_under_a_lock(app, client, monkeypatch):
    from app import signin

    with app.test_request_context():
        signin.lock_places()  # the real lock works on SQLite (PostgreSQL: an advisory lock)
        db.session.rollback()
    taken = []
    monkeypatch.setattr(signin, "lock_places", lambda: taken.append(1))
    with app.test_request_context():
        signin.set_mode(signin.MODE_CAP, _user(ME), new_limit=50)
        db.session.commit()
        assert signin.open_lets_in(THIRD) and taken                      # a new iD: counted under the lock
        taken.clear()
        assert signin.open_lets_in(ME) and not taken                     # an account that is there: no lock


# --- A5: lead links can be revoked ---------------------------------------------------------------------------------

def test_admin_revokes_lead_links(app, client):
    page = client.post("/admin/groups/new", data={"name": "Ohne Leitung", "lead": "", "lead_link": "1",
                                                  "members_create": "1"}, follow_redirects=True).data.decode()
    token = re.search(r'id="lead-link-new" value="([^"]+)"', page).group(1).split("/join/")[1]
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).filter_by(name="Ohne Leitung"))
    listing = client.get("/admin/groups").data.decode()
    assert "Leitungslinks widerrufen" in listing
    guest = app.test_client()
    _as(guest, THIRD, "Dritte")
    assert guest.post(f"/admin/groups/{gid}/lead", data={"action": "revoke_links"}).status_code == 403
    page = client.post(f"/admin/groups/{gid}/lead", data={"action": "revoke_links"}, follow_redirects=True).data.decode()
    assert "1 Leitungslink(s) für „Ohne Leitung“ widerrufen" in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(GroupJoinLink.id))) == 0
    assert "gilt nicht mehr" in guest.get(f"/join/{token}", follow_redirects=True).data.decode()
    assert "Leitungslinks widerrufen" not in client.get("/admin/groups").data.decode()


# --- A6: the join token goes at sign-out and password sign-in -------------------------------------------------------

def test_join_token_leaves_the_session(app, client):
    with client.session_transaction() as s:
        s[joinlinks.SESSION_KEY] = {"box": "x", "at": 0}
    client.post("/auth/logout")
    with client.session_transaction() as s:
        assert joinlinks.SESSION_KEY not in s
    from app import auth

    with app.test_request_context():
        from flask import session

        session[joinlinks.SESSION_KEY] = {"box": "x", "at": 0}
        auth._signed_in(_user(ME))
        assert joinlinks.SESSION_KEY not in session


# --- A2 and P3: heavy places for token downloads and waves ----------------------------------------------------------

def test_token_download_and_waves_wait_for_a_heavy_place(app, client, survey):
    from tests.privacy.test_data_access import _create, _get
    from tests.editor.test_waves import _save, _two_waves

    two = _two_waves(app, client, ["M07K1A", "B12S0H", None], ["B12S0H", "M17K1A"])
    _save(client, (1, "vorher"), (two, "nachher"))
    _r, token = _create(client)
    app.config.update(HEAVY_PARALLEL=1, HEAVY_WAIT=0)
    place = heavy._semaphore(1)
    assert place.acquire(timeout=1)
    try:
        assert client.get("/surveys/1/export/data.rds").status_code == 503              # the button, as before
        r = _get(app.test_client(), "/api/v1/surveys/1/data.rds", token)
        assert r.status_code == 503 and r.headers["Retry-After"] == "30"
        assert client.get("/surveys/1/waves/merged.zip").status_code == 503
        page = client.get("/surveys/1/data?waves=report").data.decode()
        assert "Gerade laufen schon mehrere große Auswertungen" in page
    finally:
        place.release()
    assert _get(app.test_client(), "/api/v1/surveys/1/data.csv", token).status_code == 200
    page = client.get("/surveys/1/data?waves=report").data.decode()
    assert "in mehreren Wellen verknüpft" in page
    # the report reads the codes only, and finds what the merged file finds
    from app import waves

    with app.test_request_context("/surveys/1/data?waves=report"):
        me, s1 = _user(ME), db.session.get(Survey, 1)
        tables = waves._wave_tables(me, s1)
        assert [waves._codes(t[0]["survey"]) for t in tables] == [t[4] for t in tables]
        for tolerant in ("", "1"):
            with app.test_request_context(f"/surveys/1/data?waves=report&tolerant={tolerant}"):
                assert waves.page_report(me, s1)["report"] == waves.merged(me, s1, "long", tolerant == "1")[3]


# --- A3/T2: waves name a survey only to who may open it -------------------------------------------------------------

def test_waves_hide_the_title_of_a_survey_without_access(app, client, survey):
    from tests.editor.test_waves import _save, _two_waves

    two = _two_waves(app, client, ["M07K1A"], ["M07K1A"])
    _save(client, (1, "vorher"), (two, "nachher"))
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=THIRD, name="Dritte", role="editor", position=2))
        db.session.commit()
    third = app.test_client()
    _as(third, THIRD, "Dritte")
    assert third.get(f"/surveys/{two}/data").status_code == 404
    for path in ("/surveys/1/data", "/surveys/1/researchdesign"):
        page = third.get(path, follow_redirects=True).data.decode()
        assert "Test nachher" not in page and "Befragung ohne Zugang für Sie" in page, path
    # who may open it (also only with "Ansehen") sees the title
    with app.app_context():
        db.session.add(SurveyMember(survey_id=two, orcid=THIRD, name="Dritte", role="viewer", position=2))
        db.session.commit()
    assert "Test nachher" in third.get("/surveys/1/researchdesign").data.decode()


# --- A8/R3, A9, A7/P6, C3: the blog --------------------------------------------------------------------------------

def test_drafts_only_after_the_second_factor_and_never_cached(app, client):
    from tests.platform.test_blog import _new, _png

    pid = _new(client, title_de="Entwurf", body_de="Geheim")
    client.post(f"/admin/blog/{pid}/images", data={"image": (_png(), "a.png"), "alt_de": "A"},
                content_type="multipart/form-data")
    with app.app_context():
        slug = db.session.get(BlogPost, pid).slug
        url = f"/blog/media/{pid}/{db.session.scalars(db.select(BlogImage.filename)).one()}"
    assert client.get(f"/blog/{slug}").status_code == 200
    r = client.get(url)
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"] and "public" not in r.headers["Cache-Control"]
    app.config["ADMIN_TOTP"] = True
    assert client.get(f"/blog/{slug}").status_code == 200                       # ORCID exemption
    from app import twofactor

    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK                       # no verified ORCID/code
    assert client.get(f"/blog/{slug}").status_code == 404
    assert client.get(url).status_code == 404
    assert "Entwurf" not in client.get("/blog/").data.decode()
    app.config["ADMIN_TOTP"] = False
    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_ORCID
    client.post(f"/admin/blog/{pid}/publish")
    assert "public" in app.test_client().get(url).headers["Cache-Control"]   # published: cached as before


def test_links_to_another_host_are_outside(app, client):
    from app import blog
    from tests.platform.test_blog import _new

    pid = _new(client, title_de="Links", body_de="x")
    with app.test_request_context("/"):
        post = db.session.get(BlogPost, pid)
        html = str(blog.render("[a](//fremd.example/x) [b](/\\fremd.example) [c](/pricing) [d](mailto:a@b.de)", post, "de"))
    assert '<a href="//fremd.example/x" target="_blank" rel="noopener">a</a>' in html
    assert '<a href="/\\fremd.example" target="_blank" rel="noopener">b</a>' in html
    assert '<a href="/pricing">c</a>' in html and '<a href="mailto:a@b.de">d</a>' in html


def test_lists_load_no_texts(app, client):
    from sqlalchemy import inspect

    from app import blog
    from tests.platform.test_blog import _new

    ids = [_new(client, title_de=f"Eintrag {n}", body_de=f"Langer Text {n} " * 50) for n in range(5)]
    for pid in ids:
        client.post(f"/admin/blog/{pid}/publish")
    with app.test_request_context("/"):
        posts = blog.visible_posts()
        assert len(posts) == 5 and all({"body_de", "body_en"} <= inspect(p).unloaded for p in posts)
        assert len(blog.visible_posts(limit=2)) == 2
        views = blog.latest()
        assert len(views) == 3 and views[0]["summary"].startswith("Langer Text 4")   # the teaser still from the text
    guest = app.test_client()
    assert "Eintrag 4" in guest.get("/").data.decode()
    assert "Eintrag 0" in guest.get("/blog/").data.decode()
    assert guest.get("/blog/feed.xml").status_code == 200 and guest.get("/sitemap.xml").status_code == 200


def test_ai_draft_page_with_ainstein_on(app, client):
    app.config["OPENAI_API_KEY"] = "test"
    experiments_on(client, "ainstein")
    assert client.get("/admin/blog/draft").status_code == 200
    from tests.platform.test_blog import _new

    pid = _new(client, title_de="Mit KI", body_de="Text")
    assert client.get(f"/admin/blog/{pid}").status_code == 200
