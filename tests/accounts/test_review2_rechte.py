"""Code review of 2026-10-03, package 1 "Rechte und Datenschutz": K-1 to K-7, A-2, A-3, A-5, A-7, A-9, R-5, R-6."""
import json
import os
import time

from werkzeug.datastructures import MultiDict

from app import linked
from app.extensions import db
from app.models import (Block, ConstructGroupShare, Item, Response, Survey, SurveyGroup, SurveyMember, User,
                        UserLogin, WorkGroup, WorkGroupMember)
from tests.dashboards import test_boards
from tests.live import test_live
from tests.dashboards.test_boards import study  # noqa: F401  (the fixture)
from tests.platform.test_security_links import _answer, _n, _running
from tests.conftest import allow

ME = "0000-0000-0000-001X"  # a site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
FOURTH = "0000-0001-5000-0007"


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _survey(c, title):
    c.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with c.application.app_context():
        return db.session.scalar(db.select(Survey.id).filter_by(title=title))


def _content_block(app, sid):
    with app.app_context():
        s = db.session.get(Survey, sid)
        return next(b.id for b in s.blocks if not any(i.type == "consent" for i in b.items))


def _linked(app, c, sid):
    block = _content_block(app, sid)
    c.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "single_choice"})
    with app.app_context():
        iid = db.session.scalar(db.select(Item.id).join(Block).filter(Block.survey_id == sid, Item.type == "single_choice"))
    c.post(f"/surveys/{sid}/editor/items/{iid}", data=MultiDict([
        ("code", "g"), ("question", "Frage"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Nein")]))
    c.post(f"/surveys/{sid}/editor/items/{iid}/link", data={"name": "Vorwissen"})
    with app.app_context():
        return db.session.get(Item, iid).config["construct"]["key"]


def _uses_linked(app, sid):
    with app.app_context():
        return [linked.key_of(i) is not None for b in db.session.get(Survey, sid).blocks for i in b.items
                if i.type == "single_choice"]


# --- K-1: "Ansehen" takes no data of a linked element -------------------------------------------------------


def test_k1_viewer_gets_no_rows_of_a_linked_element(app, client):
    allow(app, OTHER, THIRD)
    a = _survey(client, "A")
    key = _linked(app, client, a)
    client.post(f"/surveys/{a}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    client.post(f"/surveys/{a}/members", data={"orcid": THIRD, "name": "Dritte", "role": "editor"})
    with app.app_context():
        s = db.session.get(Survey, a)
        iid = next(i.id for b in s.blocks for i in b.items if i.type == "single_choice")
        for v in ("1", "2", "1"):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={str(iid): v}))
        s.status = Survey.CLOSED
        db.session.commit()
    viewer, editor = _as(app, OTHER, "Kollegin"), _as(app, THIRD, "Dritte")
    assert viewer.get(f"/surveys/{a}/export/data.csv").status_code == 302
    r = viewer.get(f"/constructs/linked/{key}/data.csv")
    assert r.status_code == 200 and r.data.decode("utf-8-sig").strip().count("\n") == 0   # the header only
    assert "nur ansehen können: nicht einbezogen" in viewer.get(f"/constructs/linked/{key}").data.decode()
    assert editor.get(f"/constructs/linked/{key}/data.csv").data.decode("utf-8-sig").strip().count("\n") == 3
    # the role changes: the kept data follows
    with app.app_context():
        m = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=a, orcid=OTHER))
        m.role = "editor"
        db.session.commit()
    assert viewer.get(f"/constructs/linked/{key}/data.csv").data.decode("utf-8-sig").strip().count("\n") == 3


# --- K-2: an element without maker belongs to those who may edit a survey using it ----------------------------


def test_k2_ownerless_element_needs_editing_rights_and_dissolves_only_own_uses(app, client):
    allow(app, OTHER, THIRD, FOURTH)
    other, third, fourth = _as(app, OTHER, "Kollegin"), _as(app, THIRD, "Dritte"), _as(app, FOURTH, "Vierte")
    a = _survey(other, "A")
    key = _linked(app, other, a)
    other.post(f"/surveys/{a}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    other.post(f"/surveys/{a}/members", data={"orcid": THIRD, "name": "Dritte", "role": "viewer"})
    other.post(f"/surveys/{a}/members", data={"orcid": FOURTH, "name": "Vierte", "role": "editor"})
    c = _survey(client, "C")  # a survey of ME alone that uses the element, running
    client.post(f"/surveys/{c}/editor/blocks/{_content_block(app, c)}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        db.session.get(Survey, c).status = Survey.RUNNING
        db.session.commit()
    assert "Ihr Konto wurde gelöscht" in other.post("/account/delete", data={"name": "Kollegin"}, follow_redirects=True).data.decode()
    with app.app_context():
        assert linked.get(key).created_by_id is None
    # "Ansehen" in A: neither rename nor dissolve
    assert third.post(f"/constructs/linked/{key}", data={"action": "dissolve"}).status_code == 403
    assert third.post(f"/constructs/linked/{key}", data={"action": "rename", "name": "X"}).status_code == 403
    assert _uses_linked(app, c) == [True]
    # "Bearbeiten" in A: dissolves the use in A only; C, which this person cannot open, stays linked
    page = fourth.get(f"/constructs/linked/{key}").data.decode()
    assert "Ihre 1 Verwendung wird eine gewöhnliche Kopie" in page
    r = fourth.post(f"/constructs/linked/{key}", data={"action": "dissolve"}, follow_redirects=True)
    assert "bleiben verknüpft" in r.data.decode()
    assert _uses_linked(app, a) == [False] and _uses_linked(app, c) == [True]
    with app.app_context():
        assert linked.get(key) is not None


# --- K-3: removing a member keeps the name participants consented under ---------------------------------------


def _consented_study(app, client):
    allow(app, OTHER)
    sid = _survey(client, "Studie")
    client.post(f"/surveys/{sid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    client.post(f"/surveys/{sid}/consent-facts", data=MultiDict([("min_age", ""), ("researchers", ME), ("researchers", OTHER)]))
    with app.app_context():
        mid = db.session.scalar(db.select(SurveyMember.id).filter_by(survey_id=sid, orcid=OTHER))
    return sid, mid


def test_k3_removed_member_stays_in_the_consent_with_real_answers(app, client):
    sid, mid = _consented_study(app, client)
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.responses.append(Response(status=Response.COMPLETED, source="real", answers={}))
        s.status = Survey.CLOSED
        db.session.commit()
    client.post(f"/surveys/{sid}/members/{mid}/remove")
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert [m.display_name for m in s.consent_item.kind.researchers(s.consent_item)] == ["Philipp", "Kollegin"]
        former = db.session.get(SurveyMember, mid)
        assert former.is_former and former.role == "viewer" and former.user_id is None
    assert _as(app, OTHER, "Kollegin").get(f"/surveys/{sid}").status_code != 200   # no access by it
    assert ">ehemalig</span>" in client.get(f"/surveys/{sid}").data.decode()
    client.post(f"/surveys/{sid}/members/{mid}/remove")                              # the admin takes the name out on purpose
    with app.app_context():
        assert db.session.get(SurveyMember, mid) is None


def test_k3_without_real_answers_the_entry_goes(app, client):
    sid, mid = _consented_study(app, client)
    _as(app, OTHER, "Kollegin").post(f"/surveys/{sid}/members/{mid}/remove")       # leaving
    with app.app_context():
        assert db.session.get(SurveyMember, mid) is None


# --- K-4: deleting the account ends group shares as leaving does; linked elements too --------------------------


def _group(app, *members):
    with app.app_context():
        g = WorkGroup(name="G")
        for orcid, name, role in members:
            g.members.append(WorkGroupMember(orcid=orcid, name=name, role=role))
        db.session.add(g)
        db.session.commit()
        return g.id, {m.orcid: m.id for m in g.members}


def test_k4_account_deletion_ends_the_survey_share_with_the_group(app, client):
    allow(app, OTHER, THIRD)
    other, third = _as(app, OTHER, "Kollegin"), _as(app, THIRD, "Dritte")
    s = _survey(other, "Geteilt")
    other.post(f"/surveys/{s}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    gid, _ = _group(app, (OTHER, "Kollegin", "lead"), (THIRD, "Dritte", "lead"))
    other.post(f"/surveys/{s}/access-groups", data={"group": gid, "role": "editor"})
    with app.app_context():
        assert db.session.scalar(db.select(SurveyGroup).filter_by(survey_id=s)) is not None
    other.post("/account/delete", data={"name": "Kollegin"})
    with app.app_context():
        assert db.session.scalar(db.select(SurveyGroup).filter_by(survey_id=s)) is None
    assert third.get(f"/surveys/{s}").status_code != 200


def test_k4_linked_element_share_ends_on_leaving_the_group(app, client):
    allow(app, OTHER, THIRD)
    other, third = _as(app, OTHER, "Kollegin"), _as(app, THIRD, "Dritte")
    key = _linked(app, other, _survey(other, "A"))
    gid, ids = _group(app, (OTHER, "Kollegin", "member"), (THIRD, "Dritte", "lead"))
    other.post(f"/constructs/linked/{key}", data={"action": "share", "group": gid})
    with app.app_context():
        assert db.session.scalar(db.select(ConstructGroupShare).filter_by(group_id=gid)) is not None
    assert third.get(f"/constructs/linked/{key}").status_code == 200
    other.post(f"/groups/{gid}/members/{ids[OTHER]}/remove")
    with app.app_context():
        assert db.session.scalar(db.select(ConstructGroupShare).filter_by(group_id=gid)) is None
    assert third.get(f"/constructs/linked/{key}").status_code == 404


# --- K-6: survey files put aside for an import ----------------------------------------------------------------


def test_k6_pending_import_files_go_with_the_account_and_with_the_housekeeping(app, client):
    from app import transfer

    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        transfer.keep(db.session.get(User, uid), b"{}")
        transfer.keep(me, b"{}")
        folder = transfer._pending()
    mine = next(n for n in os.listdir(folder) if n.startswith(f"{uid}-"))
    old = next(n for n in os.listdir(folder) if n != mine)
    other.post("/account/delete", data={"name": "Kollegin"})
    assert os.listdir(folder) == [old]
    past = time.time() - transfer.PENDING_SECONDS - 10
    os.utime(os.path.join(folder, old), (past, past))
    client.get("/cron/close", headers={"X-Cron-Key": "k"})
    assert os.listdir(folder) == []


# --- K-7: the page for deleting an account names linked elements and live votes ------------------------------


def test_k7_delete_page_names_linked_elements_and_live_contact(app, client):
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    kept = _survey(other, "Bleibt")
    key = _linked(app, other, kept)
    other.post(f"/surveys/{kept}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    draft = _survey(other, "Entwurf")
    gone = _linked(app, other, draft)
    with app.app_context():
        linked.rename(linked.get(gone), "Nur im Entwurf")
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        db.session.merge(UserLogin(user_id=user.id, email="kollegin@example.org"))
        db.session.commit()
    code, sid, _ = test_live.make(app, other, "Vorlesung")
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    page = other.get("/account/delete").data.decode()
    assert "Verknüpftes Element <strong>Vorwissen</strong>" in page and "danach gehört es niemandem mehr" in page
    assert "1 verknüpftes Element</strong>" in page and "„Nur im Entwurf“" in page
    assert "Live-Abstimmung <strong>Vorlesung</strong>" in page and "nennt bisher die E-Mail-Adresse" in page
    other.post("/account/delete", data={"name": "Kollegin"})
    with app.app_context():
        assert linked.get(gone) is None                                   # no use left: gone with the account
        assert linked.get(key) is not None and linked.get(key).created_by_id is None


# --- A-2 and A-7: dashboard links in steps -------------------------------------------------------------------


def test_a2_a_paused_survey_keeps_the_step_of_its_links(app, study):  # noqa: F811
    test_boards._board(study)
    tok = test_boards._link(study)
    _running(app)
    _answer(app, "1", 10)
    guest = test_boards._stranger(app)
    assert _n(guest, tok) == 10
    _answer(app, "4")  # one person more
    assert _n(guest, tok) == 10
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.close_reason = Survey.CLOSED, "paused"
        db.session.commit()
    assert _n(guest, tok) == 10
    _answer(app, "4", 4)  # the step goes on by at least five, as while it ran
    assert _n(guest, tok) == 15


def test_a7_the_frame_of_a_step_follows_rule_and_question_texts(app, study):  # noqa: F811
    from app import boards

    test_boards._board(study)
    with app.app_context():
        board = db.session.get(Survey, 1).boards[0]
        before = boards._frame(board, "de", None)
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "de": {**item.texts["de"], "question": "Wie reisen Sie?"}}
        assert boards._frame(board, "de", None) != before
        after_text = boards._frame(board, "de", None)
        s = db.session.get(Survey, 1)
        s.quality_rules = {"flags": [], "min": 1}
        assert boards._frame(board, "de", None) != after_text


# --- A-3 and A-9: questions from the audience -----------------------------------------------------------------


def test_a3_a9_audience_questions_csv_and_moderation_for_viewers(app, client):
    allow(app, OTHER)
    code, sid, block = test_live.make(app, client, "Q&A")
    q = test_live.add(app, client, sid, block, "text_input", question="Ein Wort?")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    p = app.test_client()
    p.get(f"/live/{code}")
    p.post(f"/live/{code}/qa", data={"text": '=HYPERLINK("http://example.org","x")'})
    p.post(f"/live/{code}/qa", data={"text": "So ein Scheiß"})                         # waits for approval
    csv = client.get(f"/presenter/{code}/audience-questions.csv").data.decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "\n=HYPERLINK" not in csv
    test_live.steer(client, code, action="start")
    for word in ("Statistik", "Scheiße"):
        test_live.send(app.test_client(), code, {f"i{q}": word})
    client.post(f"/surveys/{sid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    viewer = _as(app, OTHER, "Kollegin")
    assert viewer.get(f"/presenter/{code}/audience-questions.csv").status_code == 302
    assert "So ein Scheiß" not in viewer.get(f"/surveys/{sid}/results").data.decode()     # waiting: not for viewers
    assert "So ein Scheiß" in client.get(f"/surveys/{sid}/results").data.decode()
    assert [e["text"] for e in viewer.get(f"/presenter/{code}/moderation.json").json["entries"]] == ["Statistik"]
    assert {e["status"] for e in client.get(f"/presenter/{code}/moderation.json").json["entries"]} == {"shown", "filtered"}
    assert {r["status"] for r in viewer.get(f"/presenter/{code}/qa.json").json["questions"]} == {"open"}
    assert {r["status"] for r in client.get(f"/presenter/{code}/qa.json").json["questions"]} == {"open", "pending"}


# --- A-5: free text from an external section is an open answer -------------------------------------------------


def test_a5_text_values_of_an_external_section_count_as_open_answers(app, client, survey):
    from app import export
    from tests.editor import test_external

    test_external.build(app, client)
    with app.app_context():
        cols = export.open_columns(db.session.get(Survey, 1))
    assert "ext1_label" in cols and "ext1_score" not in cols


# --- R-5: an ended participation keeps its paradata ----------------------------------------------------------


def test_r5_posts_after_the_end_change_no_paradata(app, client, survey):
    from tests.participation.test_participate import build, consent_form, uid

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        db.session.get(Survey, 1).focus_paste = True
        db.session.commit()
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "2", "i3": "4", "_fp_blur": "1"})
    p.post(url, data={"nav": "next"})
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.status == Response.COMPLETED
        before = dict(r.paradata or {})
    p.post(url, data={"_fp_blur": "7", "_fp_paste": "3", "website": "spam"})
    with app.app_context():
        assert db.session.scalar(db.select(Response)).paradata == before


# --- R-6: retention periods and deletion on request take the dashboard files too ------------------------------


def _board_files(app, s):
    hb = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
    os.makedirs(hb, exist_ok=True)
    for name in ("board-1.json", "board-1-live.json", "board-1-en.json", "board-steps.json", "item_2.json"):
        with open(os.path.join(hb, name), "w", encoding="utf-8") as f:
            json.dump({"pids": []}, f)
    return hb


def test_r6_retention_and_erasure_remove_dashboard_states(app, client, survey):
    from app import erasure, retention, snapshots

    with app.app_context():
        s = db.session.get(Survey, 1)
        hb = _board_files(app, s)
        retention._clean_files(s.uid, blank_ids=True)
        assert os.listdir(hb) == ["item_2.json"]
        _board_files(app, s)
        erasure._forget_in_files(s, {"1-a"})
        assert os.listdir(hb) == ["item_2.json"]
        # snapshots from before the folder per survey move over before the period cleans them
        old = os.path.join(app.config["UPLOAD_FOLDER"], "snapshots", f"survey_{s.id}", "v1_29990101-000000")
        os.makedirs(old)
        with open(os.path.join(old, "data.csv"), "w", encoding="utf-8") as f:
            f.write("pid,a\n1-a,x\n")
        retention._files(s, blank_ids=False)
        retention.commit_files()
        assert [n for n, _, _ in snapshots.listing(s)] == ["v1_29990101-000000"]
        assert os.listdir(os.path.join(snapshots.folder(s), "v1_29990101-000000")) == []
