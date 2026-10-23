"""Duplicating a survey (app/duplicate.py): the copy has the questionnaire and the settings, nothing that was
collected, and shares no row, no file and no folder with its source."""
import io
import os

from app import flow, lifecycle
from app.extensions import db
from app.models import (Asset, Block, ContactEntry, EditMark, Item, PreviewComment, Response, SplitSession, Survey,
                        SurveyLabel, SurveyMember, SurveyPin, TranslationMark, User, UserLogin, WorkGroup, utcnow)
from tests.conftest import account_ref, CONSENT_OK, add, fill_consent
from tests.editor.test_editor import PNG
from tests.conftest import CREATE_ALL

ME = "0000-0000-0000-001X"       # site admin and admin of survey 1 (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
E = "/surveys/1/editor"



def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _logo():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (300, 80), (138, 28, 59, 255)).save(out, format="PNG")
    return out.getvalue()


def _folder(app, uid, *more):
    return os.path.join(app.config["UPLOAD_FOLDER"], *more, uid)


def _rows(survey_id):
    """{table: number of rows} for every table that names a survey, whatever tables come later."""
    out = {}
    for table in db.metadata.sorted_tables:
        if "survey_id" in table.c:
            n = db.session.scalar(db.select(db.func.count()).select_from(table).where(table.c.survey_id == survey_id))
            if n:
                out[table.name] = n
    return out


def build(app, client):
    """Survey 1 with everything a survey can have. Block 2: choice "fav" (2), likert (3), a comparison of 3 (4),
    an image (5); block 3, inside a condition on "fav": a text (6). A deleted question (7) lies in the trash.
    Then what was collected: participations, contact data, a comment, saved state, HB result, label, DOI."""
    fill_consent(client)
    add(client, "single_choice"), add(client, "likert"), add(client, "sample_compare"), add(client, "image")
    client.post(f"{E}/items/5/upload", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")
    client.post(f"{E}/blocks")
    add(client, "info", block=3), add(client, "info", block=3)
    client.post(f"{E}/items/7/delete")
    client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(_logo()), "l.png"), "variant": "light", "rights": "1"},
                content_type="multipart/form-data")
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        fav, compare = db.session.get(Item, 2), db.session.get(Item, 4)
        fav.code, compare.config = "fav", {**compare.config, "sources": [3]}
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "answered", "value": ""},
             "children": [{"t": "block", "block": 3}]},
            {"t": "end", "id": "e1", "text": {"de": "Ende"}}]}
        flow.normalize(s)
        s.title_translations, s.end_texts = {"en": "Test"}, {"de": {"thanks": "Danke!", "declined": "", "closed": ""}}
        s.panel = {"params": [{"name": "gruppe", "mode": "store", "allowed": ["a", "b"]}], "redirects": {}}
        for unit, path in (("item:2", "question"), ("block:2", "title"), ("title", "title"), ("end", "thanks")):
            db.session.add(TranslationMark(survey_id=1, unit=unit, lang="en", path=path, source="x"))
        # what the survey collected and how it ran
        s.status, s.qversion, s.declined_count, s.zenodo = Survey.CLOSED, 2, 3, {"doi": "10.5281/zenodo.1"}
        s.opened_at, s.closed_at, s.field_end, s.max_responses = utcnow(), utcnow(), utcnow(), 250
        s.status_log = [{"at": "2026-10-01T10:00:00", "event": "start"}]
        for source in ("real", "test", "dummy"):
            db.session.add(Response(survey_id=1, source=source, is_preview=source != "real", status="completed",
                                    completed_at=utcnow(), answers={"2": "1", "6": "x"}))
        db.session.add_all([
            ContactEntry(survey_id=1, email="a@example.org"), SplitSession(survey_id=1),
            PreviewComment(survey_id=1, block_id=2, item_id=2, text="Tippfehler"),
            SurveyLabel(label="welle-eins", survey_id=1), SurveyPin(user_id=1, survey_id=1),
            EditMark(survey_id=1, key="item:2", user_id=1)])
        db.session.commit()
        for kind in ("snapshots", "hb"):
            os.makedirs(_folder(app, s.uid, kind))
            open(os.path.join(_folder(app, s.uid, kind), "state.json"), "w").close()
        return s.uid


def _copy(client, title="Welle 2", **data):
    return client.post("/surveys/1/duplicate", data={"title": title, "with_people": "1", **data}, follow_redirects=True).data.decode()


def test_the_copy_has_the_questionnaire_and_nothing_collected(app, client, survey):
    build(app, client)
    page = client.get("/surveys/1/duplicate").data.decode()
    assert 'value="Test (Kopie)"' in page and "Kollegin" in page
    assert "ist als Entwurf angelegt" in _copy(client)
    with app.app_context():
        old, new = db.session.get(Survey, 1), db.session.scalar(db.select(Survey).filter_by(title="Welle 2"))
        assert new.id != old.id and new.uid != old.uid and new.code != old.code
        # a fresh draft: nothing of the field time, the data or the publication
        assert (new.status, new.qversion, new.declined_count, new.archived) == (Survey.DRAFT, 1, 0, False)
        assert new.zenodo is None and new.label is None and new.status_log is None and new.title_translations is None
        assert new.opened_at is None and new.closed_at is None and new.field_end is None
        assert new.completed_count() == 0 and not new.trash
        assert _rows(new.id) == {"block": 3, "survey_member": 2, "survey_theme": 1, "translation_mark": 3}
        # the settings
        assert new.end_texts == old.end_texts and new.panel == old.panel and new.languages == old.languages
        assert new.max_responses == 250 and new.created_by_id == 1
        # the questionnaire, under ids of its own (7 was deleted and is never given again)
        mine, theirs = [i for b in new.blocks for i in b.items], [i for b in old.blocks for i in b.items]
        assert [(i.type, i.code, i.answer_mode, i.texts) for i in mine] == [(i.type, i.code, i.answer_mode, i.texts) for i in theirs]
        assert min(i.id for i in mine) > 7 and not {b.id for b in new.blocks} & {b.id for b in old.blocks}
        by_code = {i.code: i for i in mine}
        blocks = [b.id for b in new.blocks]
        tree = flow.tree(new)
        assert flow.block_order(tree) == blocks and flow.block_order(flow.tree(old)) == [1, 2, 3]
        branch = next(n for n, _, _ in flow.walk(tree) if n["t"] == "branch")
        assert branch["cond"]["item"] == by_code["fav"].id and branch["children"] == [{"t": "block", "block": blocks[2]}]
        assert next(n for n, _, _ in flow.walk(tree) if n["t"] == "end")["text"] == {"de": "Ende"}
        compare = next(i for i in mine if i.type == "sample_compare")
        likert = next(i for i in mine if i.type == "likert")
        assert compare.config["sources"] == [likert.id] and db.session.get(Item, 4).config["sources"] == [3]
        assert [s.code for s in compare.kind.sources(compare)] == [likert.code]
        # the translation status follows the new ids; the title is new, so its mark is not taken
        units = {m.unit for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=new.id))}
        assert units == {f"item:{by_code['fav'].id}", f"block:{blocks[1]}", "end"}
        # the source is as it was
        assert old.status == Survey.CLOSED and old.zenodo == {"doi": "10.5281/zenodo.1"} and old.label == "welle-eins"
        assert _rows(1)["response"] == 3 and len(old.trash) == 1 and old.title == "Test"
    assert client.get("/surveys/2").status_code == 200 and client.get("/surveys/2/editor/").status_code == 200
    assert "Welle 2" in client.get("/dashboard/").data.decode()


def test_no_file_and_no_folder_is_shared(app, client, survey):
    uid = build(app, client)
    _copy(client)
    with app.app_context():
        old, new = db.session.get(Survey, 1), db.session.get(Survey, 2)
        assets = db.session.scalars(db.select(Asset).join(Item).join(Block).filter(Block.survey_id == new.id)).all()
        assert len(assets) == 1 and assets[0].item.block.survey_id == new.id
        source = db.session.get(Item, 5).assets[0]
        name = source.filename
        assert source.id != assets[0].id and name != assets[0].filename
        used = {assets[0].filename} | {new.theme["logo"][k] for k in ("file", "file_dark", "icon") if new.theme["logo"].get(k)}
        there, here = set(os.listdir(_folder(app, new.uid))), set(os.listdir(_folder(app, uid)))
        assert there == used and not there & here and new.theme["logo"]["file"] != old.theme["logo"]["file"]
        with open(os.path.join(_folder(app, new.uid), assets[0].filename), "rb") as a, \
                open(os.path.join(_folder(app, uid), name), "rb") as b:
            assert a.read() == b.read()
        # saved states and HB results hold answers: the copy has neither folder
        assert not os.path.exists(_folder(app, new.uid, "snapshots")) and not os.path.exists(_folder(app, new.uid, "hb"))
        # deleting the copy leaves the source alone: rows, images, saved states
        before = _rows(1)
        lifecycle.delete(new)
        db.session.commit()
        assert not os.path.exists(_folder(app, new.uid)) and _rows(1) == before
        assert set(os.listdir(_folder(app, uid))) == here and os.path.isfile(os.path.join(_folder(app, uid, "hb"), "state.json"))
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert client.get(f"/media/{uid}/{name}").status_code == 200 and name in page


def test_answers_go_to_the_survey_they_were_given_to(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                                    # 2
    client.post(f"{E}/items/2", data={"code": "wort", "question": "Ein Wort?"})
    _copy(client)
    for sid in (1, 2):
        client.post(f"/surveys/{sid}/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        codes = {s.id: s.code for s in db.session.scalars(db.select(Survey))}
        twin = db.session.get(Survey, 2).blocks[1].items[0].id
        consent = db.session.get(Survey, 2).consent_item.id
        assert twin != 2 and consent != 1
    p, q = app.test_client(), app.test_client()
    p.get(f"/s/{codes[1]}"), q.get(f"/s/{codes[2]}")
    p.post(f"/s/{codes[1]}", data=dict(CONSENT_OK))
    p.post(f"/s/{codes[1]}", data={"nav": "next", "i2": "original"})
    q.post(f"/s/{codes[2]}", data={k.replace("i1", f"i{consent}"): v for k, v in CONSENT_OK.items()})
    q.post(f"/s/{codes[2]}", data={"nav": "next", f"i{twin}": "duplikat"})
    with app.app_context():
        first, second = (db.session.scalars(db.select(Response).filter_by(survey_id=sid)).all() for sid in (1, 2))
        assert len(first) == len(second) == 1 and first[0].pid != second[0].pid
        assert first[0].answers == {"1": "1", "2": "original"}
        assert second[0].answers == {str(consent): "1", str(twin): "duplikat"}
    one = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    two = client.get("/surveys/2/export/data.csv").data.decode("utf-8-sig")
    assert "original" in one and "duplikat" not in one and "duplikat" in two and "original" not in two
    # an answer under the ids of the source means nothing in the copy
    q2 = app.test_client()
    q2.get(f"/s/{codes[2]}")
    q2.post(f"/s/{codes[2]}", data=dict(CONSENT_OK))
    q2.post(f"/s/{codes[2]}", data={"nav": "next", "i2": "fremd"})
    assert "fremd" not in client.get("/surveys/2/export/data.csv?all=1").data.decode("utf-8-sig")


def test_the_title_must_be_new(app, client, survey):
    for title in ("", "   ", "Test", " test "):
        page = _copy(client, title=title)
        assert "Titel" in page and "ist als Entwurf angelegt" not in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1
    assert "ist als Entwurf angelegt" in _copy(client, title="Test 2")


def test_who_may_duplicate(app, client, survey):
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    other = app.test_client()
    _as(other, OTHER, "Kollegin")

    def count():
        with app.app_context():
            return db.session.scalar(db.select(db.func.count(Survey.id)))

    assert "Duplizieren" in client.get("/dashboard/").data.decode()
    assert "Duplizieren" not in other.get("/dashboard/").data.decode()
    assert "Dafür fehlen die Rechte" in _copy(other) and count() == 1              # "Ansehen" does not copy
    with app.app_context():
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).role = "editor"
        db.session.commit()
    assert "Ihr Konto darf keine eigenen Befragungen anlegen" in _copy(other) and count() == 1                     # may not start surveys of their own
    assert "Ihr Konto darf keine eigenen Befragungen anlegen" in other.get("/surveys/1/duplicate", follow_redirects=True).data.decode()
    assert 'id="duplicate"' not in other.get("/surveys/1").data.decode()
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        db.session.add(UserLogin(user_id=uid, kinds=CREATE_ALL))
        db.session.commit()
    # the button is on the card in the overview only (Ralf, 2026-10-03: not on the settings page any more)
    assert "Duplizieren" in other.get("/dashboard/").data.decode() and 'id="duplicate"' not in other.get("/surveys/1").data.decode()
    assert "ist als Entwurf angelegt" in _copy(other) and count() == 2
    with app.app_context():
        new = db.session.get(Survey, 2)
        assert {m.orcid: m.role for m in new.members} == {ME: "admin", OTHER: "admin"}    # whoever copies is admin
        assert db.session.get(Survey, 1).role_of(db.session.get(User, uid)) == "editor"
    stranger = app.test_client()
    _as(stranger, THIRD, "Fremd")
    assert stranger.get("/surveys/1/duplicate").status_code in (302, 404) and count() == 2
    assert stranger.post("/surveys/1/duplicate", data={"title": "Meins"}).status_code in (302, 404) and count() == 2


def test_people_and_groups_come_along_only_if_asked(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": [ME, OTHER]})
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id))
        foreign = WorkGroup(name="Fremde Gruppe")
        db.session.add(foreign)
        db.session.flush()
        from app.models import SurveyGroup

        db.session.add(SurveyGroup(survey_id=1, group_id=foreign.id, role="editor"))
        db.session.commit()
    client.post("/surveys/1/access-groups", data={"group": gid, "role": "viewer"})
    page = client.get("/surveys/1/duplicate").data.decode()
    assert "Kollegin" in page and "Lehrstuhl" in page and "Fremde Gruppe" not in page and "nicht Mitglied" in page

    client.post("/surveys/1/duplicate", data={"title": "Mit allen", "with_people": "1"})
    client.post("/surveys/1/duplicate", data={"title": "Allein"})
    with app.app_context():
        both, alone = db.session.get(Survey, 2), db.session.get(Survey, 3)
        assert [(m.orcid, m.role, m.position) for m in both.members] == [(ME, "admin", 0), (OTHER, "editor", 1)]
        assert [(l.group_id, l.role) for l in both.group_links] == [(gid, "viewer")]   # only a group one is a member of
        assert _keys(both.consent_item.config["researchers"]) == [ME, OTHER]
        assert [(m.orcid, m.role) for m in alone.members] == [(ME, "admin")] and not alone.group_links
        assert _keys(alone.consent_item.config["researchers"]) == [ME]                        # nobody who is not on the copy
        assert len(db.session.get(Survey, 1).members) == 2 and len(db.session.get(Survey, 1).group_links) == 2


def test_an_untouched_standard_consent_follows_the_copy(app, client, survey):
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "body": consent.kind.template_body(consent, "de")},
                         "en": {**consent.texts["en"], "body": "My own words"}}
        consent.config = {**consent.config, "researchers": [account_ref(ME)]}
        db.session.commit()
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "admin"})
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    other.post("/surveys/1/duplicate", data={"title": "Ohne die anderen"})
    with app.app_context():
        new = db.session.get(Survey, 2).consent_item
        assert _keys(new.config["researchers"]) == [OTHER]
        assert new.texts["de"]["body"] == new.kind.template_body(new, "de") and "Kollegin" in new.texts["de"]["body"]
        assert new.texts["en"]["body"] == "My own words"                                # an edited text stays as written
        old = db.session.get(Survey, 1).consent_item
        assert _keys(old.config["researchers"]) == [ME] and "Philipp" in old.texts["de"]["body"]


def test_a_failed_copy_leaves_nothing(app, client, survey, monkeypatch):
    uid = build(app, client)
    from app import duplicate

    def broken(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(duplicate, "_marks", broken)
    with app.app_context():
        before = sorted(os.listdir(app.config["UPLOAD_FOLDER"]))
        try:
            duplicate.create(db.session.get(Survey, 1), db.session.get(User, 1), "Welle 2")
        except RuntimeError:
            pass
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1
        assert db.session.scalar(db.select(db.func.count(Block.id))) == 3
        assert sorted(os.listdir(app.config["UPLOAD_FOLDER"])) == before and os.listdir(_folder(app, uid))


def test_an_organisation_design_is_copied_whole(app, client, survey):
    from app import theme

    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        s = db.session.get(Survey, 1)
        from tests.helpers import rwth_design

        s.theme = rwth_design.pair()
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
        assert theme.of(s)["preset"] == "org"
    assert "Erscheinungsbild wurde nicht übernommen" not in _copy(other, title="Kopie")  # Ralf 2026-10-19: for everyone
    with app.app_context():
        copy, source = theme.of(db.session.get(Survey, 2)), theme.of(db.session.get(Survey, 1))
        assert copy["preset"] == "org" and copy["name"] == "RWTH Aachen" and theme.css(copy) == theme.css(source)
        assert copy.get("tokens") == source.get("tokens") and "tokens_dark" not in copy  # no dark side since #1430


def test_a_question_into_another_draft(app, client, survey):
    """Copy one question with its image to the end of another survey the person may edit (a block of its own)."""
    fill_consent(client)
    add(client, "image")
    client.post(f"{E}/items/2/upload", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")
    client.post("/surveys/new", data={"title": "Ziel", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        target = db.session.scalar(db.select(Survey).filter_by(title="Ziel"))
        code, n_blocks = target.code, len(target.blocks)
        db.session.get(Item, 2).code = "lieblings"
        db.session.commit()
    page = client.get(E + "/items/2").data.decode()
    assert "In anderer Befragung verwenden" in page and f'value="{code}"' in page
    client.post(E + "/items/2/copy-to", data={"target": code})
    client.post(E + "/items/2/copy-to", data={"target": code})  # twice: the second gets a number
    with app.app_context():
        target = db.session.scalar(db.select(Survey).filter_by(code=code))
        copies = [i for b in target.blocks for i in b.items if i.type == "image"]
        assert [c.code for c in copies] == ["lieblings", "lieblings_2"] and len(target.blocks) == n_blocks + 2
        source = db.session.get(Item, 2)
        # the texts in the language the target has (it has no English: code review 2026-10-02, K17)
        assert copies[0].config == source.config and copies[0].texts == {"de": source.texts["de"]}
        assert source.assets  # the upload worked
        if source.assets:
            assert copies[0].assets and copies[0].assets[0].filename != source.assets[0].filename
            assert os.path.isfile(os.path.join(app.config["UPLOAD_FOLDER"], target.uid, copies[0].assets[0].filename))
        assert flow.block_order(flow.tree(target))[-1] == copies[1].block_id
    assert client.post(E + "/items/2/copy-to", data={"target": "nope"}).status_code == 403


def test_one_panel_asks_copy_or_linked(app, client, survey):
    """One panel "In anderer Befragung verwenden": first the survey, then as a copy or linked (Ralf, 2026-10-08).
    Linked makes the question a linked element and inserts it there; without a survey it only makes it linkable."""
    from app import linked

    add(client, "text_input")  # 2
    client.post(f"{E}/items/2", data={"code": "vw", "question": "Wie gut kennen Sie Statistik?"})
    client.post("/surveys/new", data={"title": "Ziel", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Ziel")).code
    page = client.get(E + "/items/2").data.decode()
    assert page.count("In anderer Befragung verwenden") == 1 and 'name="mode" value="link"' in page
    assert "Als verknüpftes Element verwenden" not in page and "In andere Befragung kopieren" not in page
    client.post(E + "/items/2/copy-to", data={"target": code, "mode": "link", "name": "Vorwissen"})
    with app.app_context():
        source = db.session.get(Item, 2)
        target = db.session.scalar(db.select(Survey).filter_by(code=code))
        copy = next(i for b in target.blocks for i in b.items if i.code == "vw")
        assert linked.element(source).name == "Vorwissen" and linked.key_of(copy) == linked.key_of(source)
    page = client.get(E + "/items/2").data.decode()
    assert "Die Kopie bleibt mit „Vorwissen“ verknüpft." in page and 'name="mode"' not in page
    # linked without a survey: only linkable
    add(client, "text_input")
    with app.app_context():
        new = max(i.id for b in db.session.get(Survey, 1).blocks for i in b.items)
    client.post(f"{E}/items/{new}", data={"code": "q3", "question": "Noch eine"})
    client.post(f"{E}/items/{new}/copy-to", data={"target": "", "mode": "link"})
    with app.app_context():
        assert linked.element(db.session.get(Item, new)) is not None
        assert not any(i.code == "q3" for b in db.session.scalar(db.select(Survey).filter_by(code=code)).blocks for i in b.items)


def test_a_question_goes_only_into_a_questionnaire(app, client, survey):
    """V90: live votes and interview guides are no targets for a questionnaire's question (other element types)."""
    from app import duplicate
    from app.models import User

    add(client, "slider")
    for title in ("Fragebogen", "Live", "Leitfaden"):
        client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with app.app_context():
        rows = {s.title: s for s in db.session.scalars(db.select(Survey).filter(Survey.title.in_(["Fragebogen", "Live", "Leitfaden"])))}
        rows["Live"].live = {}
        rows["Leitfaden"].interview = {}
        db.session.commit()
        item = db.session.get(Item, 2)
        user = db.session.get(User, item.block.survey.members[0].user_id)
        titles = {s.title for s in duplicate.item_targets(user, item)}
    assert "Fragebogen" in titles and not titles & {"Live", "Leitfaden"}


def _keys(refs):
    """The iDs a consent's "researchers" names: references by account since 2b ("u<id>"), "former" for the entry
    of a deleted account ("m<id>")."""
    from app.extensions import db
    from app.models import User

    out = []
    for r in refs:
        if r[:1] == "u" and r[1:].isdigit():
            user = db.session.get(User, int(r[1:]))
            out.append(user.orcid if user is not None else None)
        else:
            out.append("former" if r[:1] == "m" and r[1:].isdigit() else r)
    return out
