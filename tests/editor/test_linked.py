"""Linked elements (app/linked.py): the same question in several surveys, open while nobody answered it, frozen after."""
from werkzeug.datastructures import MultiDict

from app import constructs, linked
from app.extensions import db
from app.models import Construct, Item, Response, Survey
from tests.conftest import add, fill_consent

E = "/surveys/1/editor"
OTHER = "0000-0002-1825-0097"


def choice(client, survey_id, item_id, question="Frage", yes="Ja", no="Nein", code="g", extra=()):
    return client.post(f"/surveys/{survey_id}/editor/items/{item_id}", data=MultiDict([
        ("code", code), ("question", question), ("option_id", "a"), ("option_code", "1"), ("option_label", yes),
        ("option_id", "b"), ("option_code", "2"), ("option_label", no), *extra]))


def _second_survey(client):
    """Survey 2 with an empty content block (block 4)."""
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de", "en"], "default_language": "de"})
    with db.session.no_autoflush:
        s = db.session.get(Survey, 2)
        return next(b.id for b in s.blocks if not any(i.type == "consent" for i in b.items))


def _linked_question(app, client):
    """Survey 1, question 2 (single choice "g") made a linked element; returns its key."""
    add(client, "single_choice")
    choice(client, 1, 2)
    client.post(f"{E}/items/2/link", data={"name": "Vorwissen"})
    with app.app_context():
        return db.session.get(Item, 2).config["construct"]["key"]


def test_make_linked_and_insert_elsewhere(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        c = linked.get(key)
        assert key.startswith("v-") and c.name == "Vorwissen" and c.status == "linked" and c.visibility == "private"
        assert c.seed_version == 1 and c.spec["config"]["options"][0]["code"] == "1"
        assert constructs.get(key) is None and key not in [x.key for x in constructs.search()]  # never in the toolbox
        block = _second_survey(client)
    page = client.get("/surveys/2/editor/").data.decode()
    assert "Verknüpfte Elemente" in page and "Vorwissen" in page
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        copy = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key)).all()
        assert len(copy) == 2
        other = next(i for i in copy if i.id != 2)
        assert other.texts["de"]["options"]["a"] == "Ja" and not constructs.locked(other)  # open: nothing locked
        assert linked.frozen(linked.get(key)) is None
    row = client.get("/surveys/1/editor/").data.decode()
    assert "row-linked" in row and "Verknüpft · Fassung 1" in row
    assert "Verknüpftes Element: Vorwissen" in client.get(f"{E}/items/2").data.decode()
    detail = client.get(f"/constructs/linked/{key}").data.decode()
    assert "Zweite" in detail and "Offen" in detail


def test_open_element_change_reaches_other_drafts(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key, Item.id != 2)).one()
        other_id = other.id
    choice(client, 1, 2, question="Kennen Sie Statistik?", yes="Ja, gut")
    with app.app_context():
        other = db.session.get(Item, other_id)
        assert other.texts["de"]["question"] == "Kennen Sie Statistik?" and other.texts["de"]["options"]["a"] == "Ja, gut"
        assert linked.get(key).spec["texts"]["de"]["question"] == "Kennen Sie Statistik?"
        assert other.config["construct"]["template"]["texts"]["de"]["question"] == "Kennen Sie Statistik?"


def test_frozen_by_real_participation_locks_structure_and_wording(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        db.session.add(Response(survey_id=2, source="test"))  # a test run does not freeze
        db.session.commit()
        assert linked.frozen(linked.get(key)) is None
        db.session.add(Response(survey_id=2, source="real"))
        db.session.commit()
        assert "echte Teilnahmen" in linked.frozen(linked.get(key))
    choice(client, 1, 2, question="Anders?", yes="Vielleicht", code="neu",
           extra=[("option_id", "c"), ("option_code", "3"), ("option_label", "Neu")])
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.code == "neu"  # the variable name stays free
        assert item.texts["de"]["question"] == "Frage" and item.texts["de"]["options"]["a"] == "Ja"
        assert [o["code"] for o in item.config["options"]] == ["1", "2"]
    page = client.get(f"{E}/items/2").data.decode()
    assert "Eingefroren" in page and "inert" in page and "In dieser Befragung anpassen" in page
    # adapting it here: free again, marked as adapted, still linked
    client.post(f"{E}/items/2/unlock")
    choice(client, 1, 2, question="Anders?", code="neu")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.texts["de"]["question"] == "Anders?" and linked.is_linked(item) and linked.adapted(item)
        assert linked.get(key).spec["texts"]["de"]["question"] == "Frage"  # the element is not changed
        assert constructs.badge(item)["modified"]


def test_running_use_freezes_and_unlink(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
        assert "erhoben" in linked.frozen(linked.get(key))
    client.post(f"{E}/items/2/unlink")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "construct" not in item.config and linked.get(key) is not None


def test_not_linkable(app, client, survey):
    add(client, "info")
    add(client, "single_choice")
    client.post(f"{E}/items/3", data=MultiDict([
        ("code", "g"), ("question", "Sie sagten {{x}}"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja")]))
    with app.app_context():
        assert linked.linkable(db.session.get(Item, 2))  # no answer
        assert "anderer Fragen" in linked.linkable(db.session.get(Item, 3))
    client.post(f"{E}/items/3/link")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Construct.id)).filter_by(status="linked")) == 0


def test_others_cannot_use_or_open(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post("/surveys/2/members", data={"orcid": OTHER, "name": "Andere", "role": "editor"})  # not in survey 1
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    other.post("/terms/", data={"accept": "1"})
    assert other.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"}).status_code == 400
    assert other.get(f"/constructs/linked/{key}").status_code == 404
    assert "Vorwissen" not in other.get("/surveys/2/editor/").data.decode()


def test_duplicate_with_link(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    choice(client, 1, 2)
    add(client, "likert")
    r = client.post("/surveys/1/duplicate", data={"title": "Semester 2", "link": "1"}, follow_redirects=True)
    assert "mit dem Original verknüpft" in r.data.decode()
    with app.app_context():
        source = [i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type != "consent"]
        copy = [i for b in db.session.get(Survey, 2).blocks for i in b.items if i.type != "consent"]
        assert all(linked.is_linked(i) for i in source + copy)
        assert [linked.key_of(i) for i in source] == [linked.key_of(i) for i in copy]
        assert db.session.get(Survey, 1).consent_item.config.get("construct") is None
        c = linked.get(linked.key_of(source[0]))
        assert len(linked.uses(c)) == 2
    # without the tick: plain copies, as before
    client.post("/surveys/1/duplicate", data={"title": "Semester 3"})
    with app.app_context():
        third = [i for b in db.session.get(Survey, 3).blocks for i in b.items if i.type != "consent"]
        assert all(linked.is_linked(i) for i in third)  # the original's questions are linked by now: copies keep it


def test_dissolve(app, client, survey):
    key = _linked_question(app, client)
    r = client.post(f"/constructs/linked/{key}", data={"action": "dissolve"}, follow_redirects=True)
    assert "aufgelöst" in r.data.decode()
    with app.app_context():
        assert linked.get(key) is None and "construct" not in db.session.get(Item, 2).config


def test_linked_elements_are_not_toolbox_constructs(app, client, survey):
    key = _linked_question(app, client)
    from app import export

    with app.app_context():
        s = db.session.get(Survey, 1)
        assert export.used_constructs(s) == [] and [c.key for c, _ in export.used_constructs(s, linked=True)] == [key]
        md = export.constructs_md(s)
        assert "Linked elements" in md and "Vorwissen" in md and "No instruments from the construct toolbox" in md
        from app.items.consent import _construct_names

        assert _construct_names(s) == []


# ---- stage 2: the shared analysis (app/linked_pool.py) and the consent ----

def _answered(app, survey_id, item_id, answer, started=None, source="real"):
    from app.models import utcnow

    with app.app_context():
        at = started or utcnow()
        db.session.add(Response(survey_id=survey_id, source=source, status=Response.COMPLETED, completed_at=at,
                                started_at=at, answers={str(item_id): answer}))
        db.session.commit()


def test_shared_data_from_both_surveys(app, client, survey):
    from datetime import timedelta

    from app import linked_pool
    from app.models import User, utcnow

    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key, Item.id != 2)).one().id
        db.session.get(Item, other_id).code = "g2"  # another variable name in survey 2
        for sid in (1, 2):
            db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.commit()
    _answered(app, 1, 2, "1")
    _answered(app, 1, 2, "2")
    _answered(app, 2, other_id, "2")
    _answered(app, 2, other_id, "1", source="test")                            # never in the pool
    _answered(app, 2, other_id, "1", started=utcnow() - timedelta(days=400))  # before it was linked: no hint
    with app.app_context():
        me = db.session.scalar(db.select(User))
        c = linked.get(key)
        data = linked_pool.collect(c, me)
        first = len(linked_pool.BASE)
        assert data["header"][:first] == linked_pool.BASE and data["header"][first] == "g"
        assert sorted((row[0], row[2], row[first]) for row in data["rows"]) == [
            ("Test", "g", "1"), ("Test", "g", "2"), ("Zweite", "g2", "2")]  # one variable for both names
        assert [s["no_hint"] for s in data["surveys"]] == [0, 1]
        assert len(linked_pool.collect(c, me, without_hint=True)["rows"]) == 4
        figures = linked_pool.summary(c, data)
        together = figures[-1]
        assert together["title"] == "Alle zusammen" and together["answered"] == 3
        assert [round(x["share"], 2) for x in together["shares"]] == [0.33, 0.67]
    page = client.get(f"/constructs/linked/{key}").data.decode()
    assert "Gemeinsame Auswertung" in page and "Alle zusammen" in page and "ohne Hinweis" in page
    csv_text = client.get(f"/constructs/linked/{key}/data.csv").data.decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("survey,participant_id,item,") and len(csv_text.splitlines()) == 4


def test_shared_data_only_from_own_surveys(app, client, survey):
    from app import linked_pool
    from app.models import User

    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    _answered(app, 1, 2, "1")
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Andere", "role": "viewer"})
    with app.app_context():
        c = linked.get(key)
        stranger = User(orcid="0000-0000-0000-0124", name="Fremd")
        db.session.add(stranger)
        db.session.commit()
        data = linked_pool.collect(c, stranger)
        assert data["rows"] == [] and data["hidden"] == 1


def test_running_left_out_on_request(app, client, survey):
    from app import linked_pool
    from app.models import User

    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    _answered(app, 1, 2, "1")
    with app.app_context():
        me, c = db.session.scalar(db.select(User)), linked.get(key)
        assert len(linked_pool.collect(c, me)["rows"]) == 1
        data = linked_pool.collect(c, me, running=False)
        assert data["rows"] == [] and data["surveys"][0]["left_out"] == "läuft"


def test_likert_values_by_statement_id(app, client, survey):
    from app import linked_pool

    add(client, "likert")
    with app.app_context():
        item = db.session.get(Item, 2)
        first = item.config["statements"][0]
        item.config = {**item.config, "statements": [first, {**first, "id": "s2", "code": "Q1_2"}]}
        db.session.commit()
    client.post(f"{E}/items/2/link")
    with app.app_context():
        item = db.session.get(Item, 2)
        c = linked.element(item)
        a, b = item.config["statements"]
        # adapted here: the other order, other codes, the second one reverse coded
        item.config = {**item.config, "construct": {**item.config["construct"], "unlocked": True},
                       "statements": [{**b, "code": "x2", "reverse": True}, {**a, "code": "x1"}]}
        values, extra = linked_pool.values(item, c, {a["id"]: 2, b["id"]: 1})
        assert values == {a["code"]: 2, b["code"]: item.config["points"]} and extra == {}


def test_consent_names_the_shared_analysis(app, client, survey):
    from app import design

    _linked_question(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert "Gleiche Fragen in mehreren Befragungen" in consent.kind.template_body(consent, "de")
        assert "same questions in several surveys" in consent.kind.template_body(consent, "en")
        assert "Metaanalysen" not in consent.kind.template_body(consent, "de")  # not the operator's pool
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "body": "Kurz."}}
        db.session.commit()
        assert any("Verknüpfte Elemente werden" in f.message for f in design.check(s))


# ---- stage 3: Fassungen, joining existing questions, groups ----

def _freeze(app, survey_id=1):
    with app.app_context():
        db.session.add(Response(survey_id=survey_id, source="real"))
        db.session.commit()


def test_new_version_and_switch(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key, Item.id != 2)).one().id
    _freeze(app)  # survey 1 has real participations: Fassung 1 is frozen
    # survey 2 (a draft without data) adapts it and makes the adaptation Fassung 2
    client.post(f"/surveys/2/editor/items/{other_id}/unlock")
    choice(client, 2, other_id, question="Neu gefragt?", code="g")
    client.post(f"/surveys/2/editor/items/{other_id}/new-version")
    with app.app_context():
        c = linked.get(key)
        other = db.session.get(Item, other_id)
        assert c.seed_version == 2 and c.spec["texts"]["de"]["question"] == "Neu gefragt?"
        assert linked.version_of(other) == 2 and not linked.adapted(other)
        assert linked.frozen(c) is None and linked.frozen(c, version=1) is not None  # each Fassung by its own uses
        assert not constructs.locked(other) and constructs.locked(db.session.get(Item, 2))
        assert linked.switch_refused(db.session.get(Item, 2))  # survey 1 has real data: keeps Fassung 1
        assert [v for v, _, _ in linked.versions(c)] == [2, 1]
    # Fassung 2 is open: a change reaches the template
    choice(client, 2, other_id, question="Noch einmal neu?", code="g")
    with app.app_context():
        assert linked.get(key).spec["texts"]["de"]["question"] == "Noch einmal neu?"
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage"  # Fassung 1 untouched


def test_switch_draft_without_data(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key, Item.id != 2)).one().id
        db.session.add(Response(survey_id=2, source="real"))  # survey 2 freezes Fassung 1
        db.session.commit()
    client.post(f"{E}/items/2/unlock")
    choice(client, 1, 2, question="Fassung zwei?")
    client.post(f"{E}/items/2/new-version")
    with app.app_context():
        db.session.execute(db.delete(Response).filter_by(survey_id=2))  # e.g. the data of survey 2 deleted
        db.session.commit()
    page = client.get(f"/surveys/2/editor/items/{other_id}").data.decode()
    assert "Fassung 2 ist neuer" in page and "Fassung 2 übernehmen" in page
    client.post(f"/surveys/2/editor/items/{other_id}/switch-version")
    with app.app_context():
        other = db.session.get(Item, other_id)
        assert linked.version_of(other) == 2 and other.texts["de"]["question"] == "Fassung zwei?"


def test_join_existing_copy(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    # an ordinary copy made before (same option ids), then one with another wording
    with app.app_context():
        source = db.session.get(Item, 2)
        plain = {k: v for k, v in source.config.items() if k != "construct"}
        s2 = db.session.get(Survey, 2)
        b = next(b for b in s2.blocks if b.id == block)
        same = Item(type="single_choice", code="g", answer_mode="optional", config=dict(plain), texts=dict(source.texts), position=0)
        other_wording = Item(type="single_choice", code="h", answer_mode="optional", config=dict(plain),
                             texts={**source.texts, "de": {**source.texts["de"], "question": "Anders gefragt"}}, position=1)
        b.items.extend([same, other_wording])
        db.session.get(Survey, 2).status = Survey.CLOSED  # a closed survey: joining changes nothing in it
        db.session.commit()
        same_id, other_wording_id = same.id, other_wording.id
    page = client.get(f"/surveys/2/editor/items/{same_id}").data.decode()
    assert "Mit einem verknüpften Element verbinden" in page and "Vorwissen" in page
    client.post(f"/surveys/2/editor/items/{same_id}/connect", data={"key": key})
    client.post(f"/surveys/2/editor/items/{other_wording_id}/connect", data={"key": key})
    with app.app_context():
        same, other_wording = db.session.get(Item, same_id), db.session.get(Item, other_wording_id)
        assert linked.key_of(same) == key and not linked.adapted(same)
        assert linked.key_of(other_wording) == key and linked.adapted(other_wording)
        assert same.config["construct"]["since"]


def test_share_with_group(app, client, survey):
    from app.models import WorkGroup, WorkGroupMember

    key = _linked_question(app, client)
    with app.app_context():
        g = WorkGroup(name="Lehrstuhl")
        g.members.append(WorkGroupMember(orcid="0000-0000-0000-001X", name="Ich", role="lead"))
        g.members.append(WorkGroupMember(orcid=OTHER, name="Andere", role="member"))
        db.session.add(g)
        db.session.commit()
        gid = g.id
        block = _second_survey(client)
    client.post("/surveys/2/members", data={"orcid": OTHER, "name": "Andere", "role": "editor"})
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    other.post("/terms/", data={"accept": "1"})
    assert other.get(f"/constructs/linked/{key}").status_code == 404
    client.post(f"/constructs/linked/{key}", data={"action": "share", "group": gid})
    assert other.get(f"/constructs/linked/{key}").status_code == 200
    assert "Vorwissen" in other.get("/surveys/2/editor/").data.decode()
    assert other.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"}).status_code == 302
    assert other.post(f"/constructs/linked/{key}", data={"action": "dissolve"}).status_code == 403  # not theirs
    client.post(f"/constructs/linked/{key}", data={"action": "unshare", "group": gid})
    with app.app_context():
        assert linked.get(key).group_shares == []


def test_no_new_version_where_answers_exist(app, client, survey):
    key = _linked_question(app, client)
    _freeze(app)  # survey 1 itself has real participations
    client.post(f"{E}/items/2/unlock")
    r = client.post(f"{E}/items/2/new-version", follow_redirects=True)
    assert "schon echte Teilnahmen" in r.data.decode()
    with app.app_context():
        assert linked.get(key).seed_version == 1


# ---- follow-ups: statistics files, translation page, German list of differences ----

def test_shared_data_as_statistics_files(app, client, survey):
    import gzip
    import zipfile
    import io as _io

    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    _answered(app, 1, 2, "1")
    _answered(app, 1, 2, "2")
    sav = client.get(f"/constructs/linked/{key}/data.sav")
    assert sav.status_code == 200 and sav.data[:4] == b"$FL2" and b"Vorwissen" in sav.data
    omv = client.get(f"/constructs/linked/{key}/data.omv")
    assert "metadata.json" in zipfile.ZipFile(_io.BytesIO(omv.data)).namelist()
    rds = client.get(f"/constructs/linked/{key}/data.rds")
    assert gzip.decompress(rds.data)[:2] == b"X\n"
    assert client.get(f"/constructs/linked/{key}/data.xls").status_code == 404
    with app.app_context():
        from app import linked_pool
        from app.models import User

        c = linked.get(key)
        columns = {col["name"]: col for col in linked_pool.dictionary(c, linked_pool.collect(c, db.session.scalar(db.select(User))))}
        assert columns["g"]["value_labels"] == {1.0: "Ja", 2.0: "Nein"} and columns["g"]["measure"] == "nominal"
        assert columns["survey"]["kind"] == "str"


def test_translation_page_reaches_other_uses(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key, Item.id != 2)).one().id
    r = client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "Question in English"})
    assert r.status_code == 200, r.data
    with app.app_context():
        assert db.session.get(Item, other_id).texts["en"]["question"] == "Question in English"


def test_differences_in_german_on_pages(app, client, survey):
    key = _linked_question(app, client)
    _freeze(app)
    client.post(f"{E}/items/2/unlock")
    choice(client, 1, 2, question="Anders")
    with app.app_context():
        item, c = db.session.get(Item, 2), linked.get(key)
        assert constructs.modifications(item, c) == ["wording (de): instruction"]  # exports stay English
        assert constructs.info(item)["modifications"] == ["Wortlaut (DE): Fragetext"]
    assert "Wortlaut (DE): Fragetext" in client.get(f"{E}/items/2").data.decode()


# ---- optimisations: loops, cache, index, undo, bars ----

def test_loop_passes_in_shared_data(app, client, survey, monkeypatch):
    """A linked element in a loop: one row per pass shown (answers stored as "<item id>#<row id>")."""
    from app import export, linked_pool, loops
    from app.models import User, utcnow

    key = _linked_question(app, client)
    original = export._loop_rows
    monkeypatch.setattr(export, "_loop_rows", lambda i: [(1, "r1"), (2, "r2"), (3, "r3")] if i.id == 2 else original(i))
    monkeypatch.setattr(loops, "row_caption", lambda block, rid, n, limit=60: f"Durchgang {n}")
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=utcnow(),
                                completed_at=utcnow(), answers={"2#r1": "1", "2#r2": "2"}))  # pass 3 not shown
        db.session.commit()
        me, c = db.session.scalar(db.select(User)), linked.get(key)
        data = linked_pool.collect(c, me)
        first = len(linked_pool.BASE)
        assert sorted((row[3], row[first]) for row in data["rows"]) == [("Durchgang 1", "1"), ("Durchgang 2", "2")]


def test_shared_data_kept_until_something_changes(app, client, survey):
    from app import linked_pool
    from app.models import User

    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    _answered(app, 1, 2, "1")
    with app.app_context():
        me, c = db.session.scalar(db.select(User)), linked.get(key)
        first = linked_pool.collect(c, me)
        assert linked_pool.collect(c, me) is first  # nothing changed: the kept result
    _answered(app, 1, 2, "2")
    with app.app_context():
        me, c = db.session.scalar(db.select(User)), linked.get(key)
        assert len(linked_pool.collect(c, me)["rows"]) == 2  # a new participation: computed again


def test_uses_are_looked_up_by_index(app):
    from sqlalchemy import text

    with app.app_context():
        linked.ensure_index()
        sql = str(db.select(Item.id).filter(linked._key_column() == "v-x").compile(
            db.engine, compile_kwargs={"literal_binds": True}))
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text("SET enable_seqscan = off"))  # a nearly empty table: else a scan is cheaper
            plan = db.session.execute(text("EXPLAIN " + sql)).fetchall()
        else:
            plan = db.session.execute(text("EXPLAIN QUERY PLAN " + sql)).fetchall()
        assert linked.INDEX in str(plan)


def test_undo_reaches_other_drafts(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(_key_filter(key), Item.id != 2)).one().id
    choice(client, 1, 2, question="Geändert")
    with app.app_context():
        assert db.session.get(Item, other_id).texts["de"]["question"] == "Geändert"
    client.post(f"{E}/undo")
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage"
        assert db.session.get(Item, other_id).texts["de"]["question"] == "Frage"
        assert linked.get(key).spec["texts"]["de"]["question"] == "Frage"
        assert constructs.modifications(db.session.get(Item, 2), linked.get(key)) == []
    client.post(f"{E}/redo")  # redo still possible: the undo step was not blocked by the sync
    with app.app_context():
        assert db.session.get(Item, other_id).texts["de"]["question"] == "Geändert"


def _key_filter(key):
    return Item.config["construct"]["key"].as_string() == key


def test_bars_on_the_page(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    _answered(app, 1, 2, "1")
    assert 'class="lk-bar" style="--v: 100.0%"' in client.get(f"/constructs/linked/{key}").data.decode()


def test_share_with_a_person_who_inserts_it_into_their_own_survey(app, client, survey):
    """Philipp's case: share the element with one colleague, who pulls it into a survey of their own that the
    owner cannot see; nothing is copied into anybody's survey."""
    key = _linked_question(app, client)
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollege")
    other.post("/terms/", data={"accept": "1"})
    with app.app_context():
        block = _second_survey(client)
    client.post("/surveys/2/members", data={"orcid": OTHER, "name": "Kollege", "role": "admin"})
    assert key not in other.get("/surveys/2/editor/").data.decode()
    r = client.post(f"/constructs/linked/{key}", data={"action": "share_person", "orcid": OTHER}, follow_redirects=True)
    assert "Geteilt" in r.data.decode()
    assert "Vorwissen" in other.get("/surveys/2/editor/").data.decode()
    assert other.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"}).status_code == 302
    with app.app_context():
        assert len(linked.uses(linked.get(key))) == 2
    assert other.post(f"/constructs/linked/{key}", data={"action": "rename", "name": "X"}).status_code == 403
    client.post(f"/constructs/linked/{key}", data={"action": "unshare_person", "orcid": OTHER})
    with app.app_context():
        assert linked.get(key).person_shares == []


def test_copy_locked_without_a_draft_says_why(app, client, survey):
    """Pre-test 2026-10-20, 17: in "In anderer Befragung verwenden" the radio "Als Kopie" could not be chosen. A copy
    needs another draft of the same kind (duplicate.item_targets); without one the radio stays locked, and now the
    reason stands beside it instead of a grey radio that looks broken."""
    add(client, "single_choice")
    choice(client, 1, 2)
    page = client.get(f"{E}/items/2").data.decode()
    assert '<input type="radio" name="mode" value="copy" disabled aria-describedby="copy-locked" data-use-mode>' in page
    assert 'id="copy-locked"' in page and "Gerade nicht wählbar: Eine Kopie geht nur in einen anderen Entwurf" in page
    assert '<input type="radio" name="mode" value="link" checked data-use-mode>' in page
    with app.app_context():
        _second_survey(client)                               # a draft to copy into: the copy is the choice, no reason
    page = client.get(f"{E}/items/2").data.decode()
    assert '<input type="radio" name="mode" value="copy" checked data-use-mode>' in page and 'id="copy-locked"' not in page
