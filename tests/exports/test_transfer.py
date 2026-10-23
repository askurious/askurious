"""A survey as a file (app/transfer.py): the export holds the questionnaire and the settings and nothing collected
and nobody named; a new survey from a file gets everything anew, and a file from a stranger does no harm."""
from tests.instrument_permissions import grant_instrument
import base64
import copy
import io
import json
import os
import re

import pytest

from app import flow, transfer, translations
from app.extensions import db
from app.models import Asset, Block, Item, Quota, QuotaCell, Survey, TranslationMark, User, UserLogin
from tests.conftest import account_ref, add
from tests.editor.test_duplicate import ME, OTHER, _as, _folder, _rows, build
from tests.editor.test_editor import PNG
from tests.conftest import CREATE_ALL

FILE = "/surveys/1/export/survey.json"


def _file(client, **args):
    r = client.get(FILE, query_string=args)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    return json.loads(r.data)


def _upload(client, data, title="", raw=None, name="befragung.json", **answer):
    """Upload a file and answer its preview with this title (empty: the file's); the page the answer leads to,
    or the page of the upload if it never got to the preview."""
    body = raw if raw is not None else json.dumps(data).encode()
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(body), name)},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page)
    if token is None:
        return page
    return client.post("/surveys/new/import", data={"token": token.group(1), "title-0": title, **answer},
                       follow_redirects=True).data.decode()


def _count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Survey.id)))


def _quota(app):
    """A quota on "fav" with two cells and counts, checked in the flow, and a link rule on it."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        q = Quota(survey_id=1, code="alter", name="Alter", mode="soft", tolerance=5, tolerance_pct=False, pick="least")
        q.cells = [QuotaCell(code="a", name="A", conds=[{"item": 2, "op": "eq", "value": "1"}], target=40, done=7, running=2),
                   QuotaCell(code="b", name="B", conds=[{"item": "p:gruppe", "op": "eq", "value": "a"}], target=60, refused=3)]
        db.session.add(q)
        db.session.flush()
        tree = copy.deepcopy(s.flow)
        tree["children"].insert(2, {"t": "quota", "id": "q1", "quotas": [q.id], "full": "end", "text": {"de": "Voll"},
                                    "link": "https://panel.example/full?id={{gruppe}}", "link_mode": "button"})
        s.flow = tree
        s.panel = {**s.panel, "redirects": {"completed": "https://panel.example/done"},
                   "rules": [{"outcome": "completed", "item": f"qc:{q.id}", "op": "eq", "value": "a",
                              "url": "https://panel.example/a", "mode": "now"}]}
        db.session.commit()
        return q.id


def test_the_file_has_the_questionnaire_and_nobody_and_nothing_collected(app, client, survey):
    build(app, client)
    _quota(app)
    data = _file(client)
    assert data["format"] == "askurious-survey" and data["version"] == 6
    assert data["survey"]["title"] == "Test" and data["survey"]["languages"] == ["de", "en"]
    assert [len(b["items"]) for b in data["blocks"]] == [1, 4, 1]
    assert [i["type"] for i in data["blocks"][1]["items"]] == ["single_choice", "likert", "sample_compare", "image"]
    assert len(data["files"]) == 2 and base64.b64decode(next(iter(data["files"].values())))   # the image and the logo
    assert data["quotas"][0]["cells"][0] == {"code": "a", "name": "A", "conds": [{"item": 2, "op": "eq", "value": "1"}], "target": 40}
    text = json.dumps(data)
    # nobody who is responsible, nothing that was collected
    for word in (ME, OTHER, "Kollegin", "Philipp", "a@example.org", "Tippfehler", "welle-eins", "zenodo", "\"done\"",
                 "\"running\"", "status_log", "declined_count", "researchers", "contact_email"):
        assert word not in text, word
    assert "uid" not in data["survey"] and "code" not in data["survey"]
    # without images: no file, and the questions come without them
    bare = _file(client, files="0")
    assert bare["files"] == {} and "logo" not in (bare["theme"] or {})
    assert all("images" not in i for b in bare["blocks"] for i in b["items"])
    # "Ansehen" may export, a stranger may not
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert other.get(FILE).status_code == 200
    stranger = app.test_client()
    _as(stranger, "0000-0000-0000-0124", "Fremd")
    assert stranger.get(FILE).status_code in (302, 404)
    assert "Als Datei exportieren" in client.get("/surveys/1/data").data.decode()


def test_a_file_becomes_a_survey_of_its_own(app, client, survey):
    uid = build(app, client)
    qid = _quota(app)
    data = _file(client)
    data["survey"].update(title="Studie B", title_translations={"en": "Study B"})   # "Test" is taken: see the test of the title page
    assert "Aus einer Datei anlegen" in client.get("/surveys/new?start=datei").data.decode()
    page = _upload(client, data)
    assert "ist als Entwurf angelegt" in page and "panel.example" in page
    with app.app_context():
        old, new = db.session.get(Survey, 1), db.session.get(Survey, 2)
        assert new.title == "Studie B" and new.uid != old.uid and new.code != old.code
        assert (new.status, new.qversion, new.declined_count, new.archived) == (Survey.DRAFT, 1, 0, False)
        assert new.zenodo is None and new.label is None and new.status_log is None and new.setup_step is None
        assert new.title_translations == {"en": "Study B"} and new.max_responses == 250 and new.end_texts == old.end_texts
        assert new.completed_count() == 0 and not new.trash
        assert _rows(new.id) == {"block": 3, "survey_member": 1, "survey_theme": 1, "translation_mark": 4, "quota": 1}
        assert [(m.orcid, m.role) for m in new.members] == [(ME, "admin")] and not new.group_links
        # the questionnaire under ids of its own
        mine, theirs = [i for b in new.blocks for i in b.items], [i for b in old.blocks for i in b.items]
        assert [(i.type, i.code, i.answer_mode) for i in mine] == [(i.type, i.code, i.answer_mode) for i in theirs]
        assert [i.texts for i in mine[1:]] == [i.texts for i in theirs[1:]]
        assert not {i.id for i in mine} & {i.id for i in theirs} and not {b.id for b in new.blocks} & {b.id for b in old.blocks}
        by_code = {i.code: i for i in mine}
        tree, blocks = flow.tree(new), [b.id for b in new.blocks]
        assert flow.block_order(tree) == blocks
        branch = next(n for n, _, _ in flow.walk(tree) if n["t"] == "branch")
        assert branch["cond"]["item"] == by_code["fav"].id and branch["children"] == [{"t": "block", "block": blocks[2]}]
        compare, likert = (next(i for i in mine if i.type == t) for t in ("sample_compare", "likert"))
        assert compare.config["sources"] == [likert.id]
        # the quota: conditions and targets on the new question, every counter at zero
        quota = new.quotas[0]
        assert quota.id != qid and (quota.code, quota.mode, quota.tolerance, quota.tolerance_pct, quota.pick) == ("alter", "soft", 5, False, "least")
        assert [(c.code, c.target, c.done, c.running, c.refused) for c in quota.cells] == [("a", 40, 0, 0, 0), ("b", 60, 0, 0, 0)]
        assert quota.cells[0].conds == [{"item": by_code["fav"].id, "op": "eq", "value": "1"}]
        assert quota.cells[1].conds == [{"item": "p:gruppe", "op": "eq", "value": "a"}]
        check = next(n for n, _, _ in flow.walk(tree) if n["t"] == "quota")
        # the file has the new form: the survey's "Quotenprüfung" went into it as a calculation and an end "Quote voll"
        full = next(n for n, _, _ in flow.walk(tree) if n["t"] == "end" and n.get("kind") == "quotafull")
        assert check == {"t": "quota", "id": check["id"], "quotas": [quota.id]}
        assert (full["quotas"], full["rule"], full["link"], full["text"]) == \
            ([quota.id], "any", "https://panel.example/full?id={{gruppe}}", {"de": "Voll"})
        assert new.panel["rules"] == [{"outcome": "completed", "item": f"qc:{quota.id}", "op": "eq", "value": "a",
                                       "url": "https://panel.example/a", "mode": "now"}]
        assert new.panel["params"] == [{"name": "gruppe", "mode": "store", "required": False, "allowed": ["a", "b"]}]
        assert new.panel["redirects"]["completed"] == "https://panel.example/done"
        # the consent names whoever read the file in
        assert _keys(new.consent_item.config["researchers"]) == [ME]
        units = {m.unit for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=new.id))}
        assert units == {f"item:{by_code['fav'].id}", f"block:{blocks[1]}", "end", "title"}
        # files of its own: the image and the logo, written anew
        asset = db.session.scalar(db.select(Asset).join(Item).join(Block).filter(Block.survey_id == new.id))
        used = {asset.filename} | {new.theme["logo"][k] for k in ("file", "file_dark", "icon") if new.theme["logo"].get(k)}
        there, here = set(_media_names(_folder(app, new.uid))), set(_media_names(_folder(app, uid)))
        assert there == used and not there & here
        assert _rows(1)["response"] == 3 and old.title == "Test" and len(old.members) == 2
    for path in ("/surveys/2", "/surveys/2/editor/", "/surveys/2/flow/", "/surveys/2/editor/preview"):
        assert client.get(path, follow_redirects=True).status_code == 200, path
    # a second round gives the same file again (but for ids and names of files)
    again = json.loads(client.get("/surveys/2/export/survey.json").data)
    assert [[(i["type"], i["code"], i["texts"]) for i in b["items"]][1:] for b in again["blocks"]] == \
           [[(i["type"], i["code"], i["texts"]) for i in b["items"]][1:] for b in data["blocks"]]
    assert again["survey"]["end_texts"] == data["survey"]["end_texts"] and len(again["files"]) == len(data["files"])


def test_another_title_and_who_may(app, client, survey):
    data = _file(client)
    assert "ist als Entwurf angelegt" in _upload(client, data, title="  Welle   2 ")
    with app.app_context():
        assert db.session.get(Survey, 2).title == "Welle 2" and db.session.get(Survey, 2).title_translations is None
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert "an die Admins der Plattform" in _upload(other, data) and _count(app) == 2     # may not start surveys of their own
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    assert "ist als Entwurf angelegt" in _upload(other, data)
    with app.app_context():
        new = db.session.get(Survey, 3)
        assert [(m.orcid, m.role) for m in new.members] == [(OTHER, "admin")] and _keys(new.consent_item.config["researchers"]) == [OTHER]
    assert app.test_client().post("/surveys/new/import", data={}).status_code in (302, 401)
    assert "Bitte eine Befragungsdatei auswählen" in client.post("/surveys/new/import", data={}, follow_redirects=True).data.decode()


# --- every type, every construct ----------------------------------------------------------------------------


def test_every_type_says_what_a_file_may_set():
    """A type without rules is not read from a file at all; its standard settings must pass its own rules."""
    from app import items

    for key, kind in items.REGISTRY.items():
        assert kind.import_rules is not None, key
        standard = kind.default_config()
        config, unfit, unknown = kind.import_config(standard)
        assert (config, unfit, unknown) == (standard, [], []), key


def _everything(app, client):
    """Survey 1 with one question of every type, every construct of the toolbox, a conjoint and a MaxDiff with
    their designs, a loop and a randomizer."""
    from app import constructs, items
    from app.models import Construct
    from tests.analysis.test_conjoint import conjoint_form
    from tests.analysis.test_maxdiff import maxdiff_form

    fill_consent = __import__("tests.conftest", fromlist=["fill_consent"]).fill_consent
    fill_consent(client)
    for kind in items.REGISTRY:
        if kind != "consent" and not getattr(items.REGISTRY[kind], "interview_only", False):
            add(client, kind)
    client.post("/surveys/1/editor/blocks")                                       # block 3: the constructs
    with app.app_context():
        s = db.session.get(Survey, 1)
        by_type = {i.type: i.id for i in s.blocks[1].items}
        block = s.blocks[2]
        for n, c in enumerate(db.session.scalars(db.select(Construct).order_by(Construct.key))):
            if c.key in constructs.PERMISSION_REQUIRED:
                continue  # new imports have no study-specific grant; tested in test_instrument_permissions
            kind, code, config, texts = constructs.build(c.key, s.languages, survey=s, user=grant_instrument(app, c.key))
            block.items.append(Item(type=kind, code=f"k{n}_{code}"[:60], config=config, texts=texts, position=n, answer_mode="nudge"))
        compare = db.session.get(Item, by_type["sample_compare"])
        compare.config = {**compare.config, "sources": [by_type["likert"]]}
        db.session.commit()
    form = conjoint_form(versions=2)
    form["code"] = "cbc"
    client.post(f"/surveys/1/editor/items/{by_type['conjoint']}", data=form)
    client.post(f"/surveys/1/editor/items/{by_type['conjoint']}/conjoint/design")
    client.post(f"/surveys/1/editor/items/{by_type['maxdiff']}", data=maxdiff_form(versions=2, anchor=True))
    client.post(f"/surveys/1/editor/items/{by_type['maxdiff']}/conjoint/design")
    client.post("/surveys/1/editor/blocks")                                       # block 4: in a loop, in a randomizer
    add(client, "info", block=4)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.normalize(s))
        last = tree["children"].pop(next(n for n, c in enumerate(tree["children"]) if c.get("block") == 4))
        tree["children"].insert(3, {"t": "rand", "id": "r1", "code": "cond1", "name": "Bedingung", "k": 1, "balanced": True, "shuffle": False,
                                    "children": [{"t": "loop", "id": "l1", "name": "Vignetten", "code": "vig", "mode": "k", "k": 1,
                                                  "order": "random", "balanced": True,
                                                  "columns": [{"id": "c1", "name": "robot", "hidden": False}],
                                                  "rows": [{"id": "r1", "values": {"c1": {"de": "Pflegeroboter", "en": "care robot"}}},
                                                           {"id": "r2", "values": {"c1": {"de": "Serviceroboter"}}}],
                                                  "children": [last]},
                                                 {"t": "end", "id": "e1", "text": {"de": "Ende"}, "kind": "screenout", "link": "", "link_mode": "now"}]})
        s.flow = tree
        flow.normalize(s)
        db.session.commit()


def test_every_type_and_each_generally_usable_construct_comes_through_unchanged(app, client, survey):
    _everything(app, client)
    data = _file(client)
    page = _upload(client, data, title="Alles")
    assert "ist als Entwurf angelegt" in page and "nicht übernommen" not in page and "ausgelassen" not in page
    with app.app_context():
        old, new = db.session.get(Survey, 1), db.session.get(Survey, 2)
        theirs, mine = [i for b in old.blocks for i in b.items], [i for b in new.blocks for i in b.items]
        assert len(mine) == len(theirs) > 45
        ids = {a.id: b.id for a, b in zip(theirs, mine)}
        for a, b in zip(theirs, mine):
            assert (a.type, a.code, a.answer_mode, a.texts) == (b.type, b.code, b.answer_mode, b.texts), a.code
            # a setting the source never stored (an older question, a construct) arrives with its standard
            want = {**{k: v for k, v in a.kind.default_config().items() if k in a.kind.import_rules}, **a.config}
            if "sources" in want:
                want["sources"] = [ids[s] for s in want["sources"]]
            if a.type == "consent":
                want.update(researchers=b.config["researchers"], contact_email="")
            assert b.config == want, (a.code, {k: (want.get(k), b.config.get(k)) for k in set(want) | set(b.config) if want.get(k) != b.config.get(k)})
        cbc, md = (next(i for i in mine if i.type == t) for t in ("conjoint", "maxdiff"))
        assert cbc.kind.design_current(cbc) and len(cbc.config["design"]["versions"]) == 2
        assert md.kind.design_current(md) and md.config["design"]["stats"]["connected"]
        from app import constructs

        assert all(constructs.locked(i) and constructs.modifications(i, constructs.get(i.config["construct"]["key"])) == []
                   for i in mine if (i.config or {}).get("construct"))
        a, b = flow.tree(old), flow.tree(new)
        blocks = {x.id: y.id for x, y in zip(old.blocks, new.blocks)}

        def with_ids(node):
            return {**node, **({"block": blocks[node["block"]]} if node["t"] == "block" else {}),
                    **({"children": [with_ids(c) for c in node["children"]]} if "children" in node else {})}
        assert b == with_ids(a)
        item_ids = [i.id for i in mine]
    assert client.get("/surveys/2/editor/").status_code == 200
    for item_id in item_ids:
        assert client.get(f"/surveys/2/editor/items/{item_id}").status_code == 200, item_id
        assert client.get(f"/surveys/2/editor/items/{item_id}/frame").status_code == 200, item_id
    assert client.get("/surveys/2/flow/").status_code == 200 and client.get("/surveys/2/translation/").status_code == 200


# --- a file from a stranger ---------------------------------------------------------------------------------


def _folders(app):
    return sorted(os.listdir(app.config["UPLOAD_FOLDER"]))


def _item(data, kind):
    return next(i for b in data["blocks"] for i in b["items"] if i["type"] == kind)


@pytest.mark.parametrize("raw,word", [
    (b"\x89PNG not json", "keine lesbare JSON-Datei"),
    (b"[1, 2, 3]", "keine Befragungsdatei"),
    (b'{"format": "something-else", "version": 1}', "keine Befragungsdatei"),
    (b'{"format": "askurious-survey", "version": 7}', "neueren Version"),  # the rows below: the name before the rename
    (b'{"format": "queryous-survey", "version": true}', "Formatversion"),
    (b'{"format": "queryous-survey", "version": 1, "survey": {"title": "x", "max_responses": NaN}}', "keine lesbare JSON-Datei"),
    (b'{"format": "queryous-survey", "version": 1, "survey": {}}', "keinen Titel"),
    (b'{"format": "queryous-survey", "version": 1, "x": ' + b"[" * 100000 + b"]" * 100000 + b"}", "keine lesbare JSON-Datei"),
], ids=["binary", "list", "other-format", "newer", "no-version", "nan", "no-title", "deep"])
def test_what_is_not_a_survey_file_is_refused(app, client, raw, word):
    assert word in _upload(client, None, raw=raw)
    assert _count(app) == 0 and _folders(app) == []


SETTINGS = [  # (type, setting, a value the editor would never store)
    ("likert", "points", 10 ** 8), ("likert", "points", "5"), ("likert", "statements", [{"id": "a b", "code": ""}]),
    ("likert", "statements", [{"id": "a", "code": ""}] * 2), ("semantic", "points", 10 ** 6),
    ("rating", "max", 1e12), ("rating", "style", "<b>"), ("rating", "points", 1),
    ("slider", "step", 0), ("slider", "rows", "x"), ("association", "slots", 10 ** 7),
    ("sample_crowd", "avatars", 10 ** 7), ("sample_compare", "sources", ["x"]),
    ("map_marker", "color", '"><img src=x onerror=alert(1)>'), ("map_marker", "max", 10 ** 6),
    ("map_marker", "questions", [{"id": "q", "code": "", "kind": "scale", "points": 10 ** 6, "required": False}]),
    ("image_mark", "color", "red; background: url(https://evil.example/x)"), ("image", "size", "99999x99999"),
    ("acbc", "screens", 10 ** 6), ("acbc", "per_screen", "4" * 50), ("acbc", "price", {"on": True}),
    ("conjoint", "tasks", 10 ** 6), ("conjoint", "alternatives", 10 ** 6), ("conjoint", "attributes", [{"id": "a"}]),
    ("maxdiff", "sets", 10 ** 6), ("maxdiff", "versions", 10 ** 6), ("maxdiff", "statements", [{"id": str(n)} for n in range(500)]),
    ("multiple_choice", "min", "1"), ("single_choice", "options", [{"id": "a", "code": 1}]), ("single_choice", "layout", "x onclick=y"),
    ("constant_sum", "total", 10 ** 9), ("text_input", "max_length", 10 ** 9), ("text_input", "validation", "exec"),
    ("date_time", "year_min", "1900"), ("highlight", "cats", [{"color": "url(x)"}]), ("psm", "decimals", 400),
    ("psm", "nms_weights", "abc"), ("score_band", "bands", [{"id": "b", "upper": None}, {"id": "c", "upper": 3}]),
    ("consent", "confirm", [1]), ("consent", "min_age", "18"), ("assign", "categories", [{"id": "L", "code": "1"}]),
    ("likert", "level", "<script>"), ("likert", "ai", {"idea": ["x"]}),
]


def test_settings_beyond_the_editor_are_refused(app, client, survey):
    """One file per setting: a number that would drive a loop, a colour that would become markup, a list of
    the wrong shape. Each is refused as a whole, naming the element, and leaves nothing."""
    from app import items

    for kind in items.REGISTRY:
        if kind != "consent" and not getattr(items.REGISTRY[kind], "interview_only", False):
            add(client, kind)
    base = _file(client)
    before = _folders(app)
    for kind, key, value in SETTINGS:
        data = copy.deepcopy(base)
        _item(data, kind)["config"][key] = value
        page = _upload(client, data, title=f"{kind} {key}")
        assert "lässt sich nicht einlesen" in page and "ist als Entwurf angelegt" not in page, (kind, key)
        assert _count(app) == 1 and _folders(app) == before, (kind, key)
    # ranges that only show with the other settings
    for kind, changes in (("rating", {"style": "numbers", "min": 0, "max": 5000}), ("rating", {"style": "stars", "points": 9}),
                          ("acbc", {"min_changes": 5, "max_changes": 2}), ("map_marker", {"center": [120, 10]}),
                          ("text_input", {"validation": "regex", "pattern": r"(a)\1", "max_length": 100}),  # RE2 cannot (KR-1)
                          ("text_input", {"validation": "regex", "pattern": "(", "max_length": 100}),
                          ("text_input", {"validation": "regex", "pattern": "a", "max_length": 20000})):
        data = copy.deepcopy(base)
        _item(data, kind)["config"].update(changes)
        assert "lässt sich nicht einlesen" in _upload(client, data, title="x"), (kind, changes)
    # texts of a shape the page does not expect
    for kind, texts in (("likert", {"labels": 5}), ("single_choice", {"options": ["a"]}), ("semantic", {"pairs": {"x": "y"}})):
        data = copy.deepcopy(base)
        row = _item(data, kind)
        row["texts"]["de"].update(texts)
        if kind == "semantic":
            row["config"]["pairs"] = [{"id": "x", "code": "", "reverse": False}]
        assert "lässt sich nicht einlesen" in _upload(client, data, title="x"), (kind, texts)
    assert _count(app) == 1 and _folders(app) == before
    # a text beyond the limit, a number beyond what a float holds, a nesting beyond any setting
    for config in ({"prompt": "x" * 70000}, {"zoom": 10 ** 20}, {"x": [[[[[[[[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]]]]]]]}):
        data = copy.deepcopy(base)
        _item(data, "map_marker")["config"].update(config)
        assert "Die Datei" in _upload(client, data, title="x") and _count(app) == 1


def test_what_the_file_adds_is_left_out_or_set_right(app, client, survey):
    from app import constructs
    from app.models import Construct

    add(client, "likert"), add(client, "single_choice"), add(client, "text_input"), add(client, "conjoint"), add(client, "maxdiff")
    with app.app_context():
        s = db.session.get(Survey, 1)
        key = next(c.key for c in db.session.scalars(db.select(Construct).order_by(Construct.key)) if c.spec["type"] == "likert")
        kind, code, config, texts = constructs.build(key, s.languages, survey=s, user=grant_instrument(app, key))
        s.blocks[1].items.append(Item(type=kind, code="scale", config=config, texts=texts, position=9))
        db.session.commit()
    data = _file(client)
    likert, choice, text = (_item(data, t) for t in ("likert", "single_choice", "text_input"))
    scale = next(i for b in data["blocks"] for i in b["items"] if i["code"] == "scale")
    # settings nobody knows, a type nobody knows, a second consent, a code twice
    likert["config"].update(evil={"deep": [1, 2]}, on_load="alert(1)")
    data["blocks"][1]["items"] += [{"id": 900, "type": "exploit", "code": "boom", "config": {}, "texts": {}},
                                  {"id": 901, "type": "consent", "code": "consent2", "config": {}, "texts": {}},
                                  {"id": 902, "type": "info", "code": text["code"], "config": {}, "texts": {"de": {"question": "Hallo"}}},
                                  {"id": 903, "type": "info", "code": "1 bad name", "config": {}, "texts": {"de": 5, "xx": {"question": "?"}}}]
    # texts with the characters the formatting uses as marks
    choice["texts"]["de"]["question"] = "Wahl\x00\x02" + "0\x03 <script>alert(1)</script> **fett**"
    # a construct whose own copy of the scale was changed along with the scale: must show as changed
    for part in (scale["config"], scale["config"]["construct"]["template"]["config"]):
        part["statements"][0]["reverse"] = not part["statements"][0].get("reverse")
    other = copy.deepcopy(scale)
    other.update(id=904, code="madeup")
    other["config"]["construct"] = {"key": "no-such-scale", "version": 1, "template": {"config": {}, "texts": {"de": "[System]" * 2000}}}
    data["blocks"][1]["items"].append(other)
    # designs nobody generated
    cbc, md = _item(data, "conjoint"), _item(data, "maxdiff")
    cbc["config"].update(attributes=[{"id": "a", "code": "A1", "visual": False, "levels": [{"id": "l1", "hint": ""}, {"id": "l2", "hint": ""}]}],
                         tasks=1, alternatives=2, versions=1, design={"sig": "x", "d_eff": 1, "versions": [[[[0], [7]]]]})
    md["config"].update(statements=[{"id": str(n)} for n in range(5)], set_size=3, sets=1, versions=1,
                        design={"sig": "x", "versions": [[[0, 1, 99]]], "stats": {"version": "boom"}})
    # what a survey collected or who it belongs to, written into the file by hand
    data["survey"].update(status="running", uid="../../etc", code="admin", qversion=9, declined_count=5, created_by_id=99,
                          zenodo={"doi": "10.1/x"}, archived=True, setup_step="x", tempo_hint="evil", max_responses=-5, speeder_pct=700,
                          languages=["de", "xx", 5, "de"], default_language="xx", allow_back="yes",
                          end_texts={"de": {"thanks": "Danke", "evil": "x"}, "zz": {"thanks": "x"}})
    data["members"] = [{"orcid": OTHER, "role": "admin"}]
    data["responses"] = [{"answers": {"2": "1"}}]
    page = _upload(client, data, title="Fremd")
    assert "ist als Entwurf angelegt" in page
    for word in ("„evil“", "„on_load“", "Typ, den diese Installation nicht kennt", "mehr als eine Einwilligung", "kam in der Datei doppelt vor",
                 "gewöhnliche Frage", "Versuchsplan bitte neu erzeugen"):
        assert word in page, word
    with app.app_context():
        new = db.session.get(Survey, 2)
        assert (new.status, new.qversion, new.declined_count, new.archived, new.zenodo, new.setup_step) == (Survey.DRAFT, 1, 0, False, None, None)
        assert new.uid != "../../etc" and new.code != "admin" and new.created_by_id == 1
        assert (new.languages, new.default_language, new.allow_back, new.tempo_hint, new.max_responses) == (["de"], "de", True, "off", None)
        assert new.speeder_pct == 50
        assert new.end_texts == {"de": {"thanks": "Danke"}}
        assert [(m.orcid, m.role) for m in new.members] == [(ME, "admin")] and _rows(new.id).get("response") is None
        mine = {i.code: i for b in new.blocks for i in b.items}
        assert "boom" not in mine and "consent2" not in mine and [i.type for i in mine.values()].count("consent") == 1
        assert not {"evil", "on_load"} & set(mine[likert["code"]].config)
        assert len([i for i in mine.values() if i.type == "info"]) == 2                 # both kept, under names of their own
        assert all(set(i.texts) <= {"de", "en"} and all(isinstance(t, dict) for t in i.texts.values()) for i in mine.values())
        question = mine[choice["code"]].texts["de"]["question"]
        assert question == "Wahl0 <script>alert(1)</script> **fett**"                 # the marks are gone; the page escapes the rest
        # the construct is measured against the toolbox of this installation, not against the file
        scale_item = mine["scale"]
        assert scale_item.config["construct"]["template"] == constructs.get(key).spec
        assert constructs.modifications(scale_item, constructs.get(key)) != []
        assert "construct" not in mine["madeup"].config
        assert mine[cbc["code"]].config["design"] is None and mine[md["code"]].config["design"] is None
        ids = {i.code: i.id for i in mine.values()}
    shown = client.get(f"/surveys/2/editor/items/{ids[choice['code']]}/frame").data.decode()
    assert "&lt;script&gt;alert(1)" in shown and "<script>alert(1)" not in shown and "<strong>fett</strong>" in shown
    assert client.get("/surveys/2/editor/").status_code == 200


def test_the_flow_and_the_links_of_a_file_are_built_anew(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/editor/blocks")
    add(client, "info", block=3)
    data = _file(client)
    b1, b2, b3 = (b["id"] for b in data["blocks"])
    data["quotas"] = [{"id": 5, "code": "bad code!", "name": "Q", "mode": "x", "tolerance": -4, "hold_minutes": 10 ** 9, "pick": "evil",
                       "cells": [{"code": "a", "name": "A", "target": 10 ** 12, "done": 99, "running": 7, "closed": True,
                                  "conds": [{"item": 2, "op": "drop table", "value": 7}, {"item": 4711, "op": "eq", "value": "1"},
                                            {"item": {"x": 1}}, "nonsense"]},
                                 {"code": "a", "name": "B", "target": 5, "conds": []}]}]
    data["flow"] = {"t": "root", "evil": True, "children": [
        {"t": "block", "block": b1}, {"t": "block", "block": 4711}, {"t": "block", "block": [1]}, "nonsense",
        {"t": "teleport", "id": "t1", "children": [{"t": "block", "block": b2}]},          # a kind nobody knows: its blocks stay
        {"t": "branch", "id": f"k{b3}", "cond": {"item": 4711, "op": "x", "value": {"a": 1}}, "name": "N" * 500, "children": [
            {"t": "rand", "id": "same", "code": "1x", "k": 10 ** 9, "balanced": "yes", "children": [{"t": "block", "block": b3}]},
            {"t": "end", "id": "same", "kind": "evil", "text": {"de": "Ende", "zz": "x", "en": 5}, "link": "javascript:alert(1)", "link_mode": "x"}]},
        {"t": "else", "id": "x1", "of": f"k{b3}", "children": []},
        {"t": "quota", "id": "q1", "quotas": [5, 5, 99, "x"], "full": "x", "link": "https://{{pid}}.evil.example/", "text": "x"},
        {"t": "quota", "id": "q2", "quotas": [5]},
        {"t": "loop", "id": "l1", "code": "!", "k": -3, "mode": "x", "columns": [{"id": "c 1", "name": "robot"}, {"id": "c2", "name": ""}, "x"],
         "rows": [{"id": "r_1", "values": {"c 1": {"de": "A" * 900, "zz": "x"}, "nope": {"de": "x"}}}, {"id": "r_1", "values": 5}], "children": []},
        {"t": "split", "id": "s1"}, {"t": "split", "id": "s2"}]}
    data["survey"]["panel"] = {
        "params": [{"name": "src", "mode": "store", "allowed": ["a", "a", "b\x00", 5]}, {"name": "src"}, {"name": "bad name"}, "x"],
        "redirects": {"completed": "https://panel.example/ok?p={{src}}", "screenout": "http://plain.example/", "closed": "https://x.example/{{unknown}}",
                      "evil": "https://x.example/"},
        "modes": {"completed": "auto", "screenout": "evil"}, "quota_placeholder": "yes",
        "rules": [{"outcome": "completed", "item": "qc:5", "op": "eq", "value": "a", "url": "https://panel.example/a", "mode": "now"},
                  {"outcome": "completed", "item": "qc:77", "op": "eq", "value": "a", "url": "https://panel.example/b"},
                  {"outcome": "completed", "item": "p:src", "op": "eq", "value": "a", "url": "ftp://x"},
                  {"outcome": "declined", "item": "qc:5", "op": "eq", "value": "a", "url": ""}, "x"]}
    page = _upload(client, data, title="Ablauf")
    assert "ist als Entwurf angelegt" in page and page.count("nicht übernommen") >= 5 and "panel.example" in page
    with app.app_context():
        new = db.session.get(Survey, 2)
        blocks = [b.id for b in new.blocks]
        item = new.blocks[1].items[0].id
        tree = flow.tree(new)
        assert set(tree) == {"t", "children"} and sorted(flow.block_order(tree)) == sorted(blocks)       # every block once
        nodes = [n for n, _, _ in flow.walk(tree)]
        ids = [n["id"] for n in nodes if n["t"] != "block"]
        assert len(ids) == len(set(ids)) and not any(i.startswith("k") and i[1:].isdigit() for i in ids)
        # the "Quotenprüfung" of a file of version 1 ("full") becomes a calculation and an end "Quote voll"
        assert [n["t"] for n in tree["children"]] == ["block", "block", "branch", "else", "quota", "end", "quota", "loop", "split"]
        branch, other = tree["children"][2], tree["children"][3]
        assert other["of"] == branch["id"] and branch["cond"] == {"item": None, "op": "eq", "value": ""} and len(branch["name"]) == 100
        rand, end = branch["children"]
        assert (rand["code"], rand["k"], rand["balanced"], rand["shuffle"]) == ("cond1", 1000, True, False)
        assert end == {"t": "end", "id": end["id"], "text": {"de": "Ende"}, "kind": "screenout", "link": "", "link_mode": "now"}
        quota = new.quotas[0]
        first, full, second = tree["children"][4:7]
        assert first == {"t": "quota", "id": first["id"], "quotas": [quota.id]} and second == {"t": "quota", "id": second["id"], "quotas": []}
        assert (full["kind"], full["quotas"], full["rule"], full["link"], full["text"]) == ("quotafull", [quota.id], "any", "", {})
        loop = tree["children"][7]
        assert (loop["code"], loop["k"], loop["mode"], [c["name"] for c in loop["columns"]]) == ("loop1", 1, "all", ["robot"])
        cid = loop["columns"][0]["id"]
        assert cid != "c 1" and len({r["id"] for r in loop["rows"]}) == 2 and loop["rows"][0]["values"] == {cid: {"de": "A" * 500}}
        # the quota: every value from what quotas.py knows, every counter at zero
        assert (quota.code, quota.mode, quota.tolerance, quota.hold_minutes, quota.pick) == ("quote1", "hard", 0, 7 * 24 * 60, "first")
        a, b = quota.cells
        assert (a.code, a.target, a.done, a.running, a.closed, b.code) == ("a", 1_000_000, 0, 0, False, "z1")
        assert a.conds == [{"item": item, "op": "eq", "value": "7"}, {"item": None, "op": "eq", "value": "1"}, {"item": None, "op": "eq", "value": ""}]
        # entry settings: only links that the settings page would take, only rules that name something there
        p = new.panel
        assert p["params"] == [{"name": "src", "mode": "store", "required": False, "allowed": ["a", "b"]}]
        assert p["redirects"] == {"completed": "https://panel.example/ok?p={{src}}", "screenout": "", "quotafull": "", "excluded": "", "duplicate": "", "declined": "", "closed": ""}
        assert p["modes"] == {"completed": "auto"} and p["quota_placeholder"] is False
        assert p["rules"] == [{"outcome": "completed", "item": f"qc:{quota.id}", "op": "eq", "value": "a", "url": "https://panel.example/a", "mode": "now"}]
    assert client.get("/surveys/2/flow/").status_code == 200 and client.get("/surveys/2/editor/preview", follow_redirects=True).status_code == 200
    # a flow nested beyond any survey
    deep = {"t": "root", "children": []}
    node = deep
    for n in range(40):
        node["children"].append({"t": "rand", "id": f"r{n}", "children": []})
        node = node["children"][0]
    data["flow"] = deep
    assert "zu tief verschachtelte" in _upload(client, data, title="Tief") and _count(app) == 2


# --- images, logos, the consent -------------------------------------------------------------------------------


def _media_names(folder):
    from pathlib import Path

    names = os.listdir(folder)
    claims = [name for name in names if name.endswith(".thumbnail.claim")]
    assert all(Path(folder, name).read_bytes() in (b"", b"0") for name in claims)
    return [name for name in names if name not in claims]


def _with_image(client):
    add(client, "image")                                                              # item 2
    client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")


def test_images_are_written_anew_and_no_name_of_the_file_is_a_path(app, client, survey, tmp_path):
    _with_image(client)
    data = _file(client)
    image = _item(data, "image")
    good = image["images"][0]["file"]
    data["files"].update({"../../../evil.png": data["files"][good], "notes.txt": base64.b64encode(b"<script>alert(1)</script>").decode(),
                          "broken": "@@@ no base64 @@@", "number": 5})
    image["images"] = [{"file": "../../../evil.png", "key": "", "source": "exe", "status": "evil", "prompt": "p" * 9000},
                       {"file": "notes.txt"}, {"file": "broken"}, {"file": "number"}, {"file": "missing"}, {"file": ["x"]}, "x"]
    page = _upload(client, data, title="Bilder")
    assert "ist als Entwurf angelegt" in page and page.count("Ein Bild wurde nicht übernommen") == 5
    with app.app_context():
        new = db.session.get(Survey, 2)
        asset = db.session.scalar(db.select(Asset).join(Item).join(Block).filter(Block.survey_id == new.id))
        assert (asset.source, asset.status, len(asset.prompt)) == ("upload", Asset.PENDING, 5000)
        assert "evil" not in asset.filename and "/" not in asset.filename and asset.filename.endswith(".webp")
        assert _media_names(_folder(app, new.uid)) == [asset.filename]
        uid = new.uid
    # nothing was written anywhere else
    root = app.config["UPLOAD_FOLDER"]
    # Export admission now also creates the central heavy-work lock directory.
    assert sorted(os.listdir(root)) == sorted([uid, db_uid(app, 1), "hb"])
    assert not [f for f in os.listdir(os.path.dirname(root)) if "evil" in f] and not [f for f in os.listdir(tmp_path) if "evil" in f]
    assert client.get(f"/media/{uid}/{asset.filename}").status_code == 200


def db_uid(app, survey_id):
    with app.app_context():
        return db.session.get(Survey, survey_id).uid


@pytest.mark.parametrize("from_zip", [False, True], ids=["json", "zip"])
def test_repeated_image_reads_count_against_the_real_file_limit(app, from_zip):
    """The production count limit, including repeated references, without decoding or writing 3000 images."""
    with app.app_context():
        files = (transfer._Files({}, fetch=lambda name, most: PNG) if from_zip
                 else transfer._Files({"same.png": base64.b64encode(PNG).decode()}))
        for _ in range(transfer.MAX_FILES):
            assert files.get("same.png") == PNG
        with pytest.raises(transfer.Refused, match="mehr Bilder"):
            files.get("same.png")


def test_a_file_cannot_fill_the_disk_with_its_one_image(app, client, survey, monkeypatch):
    _with_image(client)
    data = _file(client)
    image = _item(data, "image")
    image["images"] = image["images"] * 4
    before = _folders(app)
    # Exercise the entire import and cleanup with real files, but do not write thousands of copies just to
    # exceed a counter. The real limit and both file readers are covered separately above.
    with monkeypatch.context() as patch:
        patch.setattr(transfer, "MAX_FILES", 3)
        assert "mehr Bilder, als eingelesen werden können" in _upload(client, data, title="Viele")
    assert _count(app) == 1 and _folders(app) == before
    # and not with one large image named a few times: what is read counts against the size of an upload
    data = _file(client)
    _item(data, "image")["images"] *= 5
    with app.test_request_context():
        app.config["IMPORT_MAX_BYTES"] = 3 * len(PNG) + 10
        with pytest.raises(transfer.Refused, match="mehr Bilder"):
            transfer.create(data, db.session.get(User, 1))
    assert _count(app) == 1 and _folders(app) == before


def test_a_file_larger_than_the_limit(app, client, survey):
    data = _file(client)
    app.config["IMPORT_MAX_BYTES"] = 2000
    data["blocks"][0]["items"][0]["texts"]["de"]["body"] = "x" * 5000
    page = _upload(client, data)
    assert "Die Datei ist größer als" in page and "Aus einer Datei anlegen" in page and _count(app) == 1


def test_the_logo_of_a_file_is_cleaned_and_an_organisation_design_comes_in_whole(app, client, survey):
    from app import theme

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" onload="alert(1)"><script>alert(1)</script>'
           b'<rect width="120" height="40" fill="#8a1c3b"/><a href="javascript:alert(2)"><text x="5" y="20">Logo</text></a></svg>')
    data = _file(client)
    data["files"] = {"logo.svg": base64.b64encode(svg).decode(), "p.png": base64.b64encode(PNG).decode()}
    data["theme"] = {"preset": "custom", "colors": {"accent": "#8a1c3b", "background": "javascript:x", "text": "#111111", "surface": "#ffffff"},
                     "font": "evil", "radius": "round", "font_scale": "9000",
                     "logo": {"file": "logo.svg", "w": 10 ** 9, "h": 5, "alt": {"de": "Logo<script>"}, "position": "x", "icon": "../x.png"},
                     "partners": [{"file": "p.png", "alt": "Partner"}, {"file": "nope.png", "alt": "x"}]}
    assert "ist als Entwurf angelegt" in _upload(client, data, title="Logo")
    with app.app_context():
        new = db.session.get(Survey, 2)
        look = theme.clean(new.theme)
        assert look["colors"]["background"] == "#ffffff" and (look["font"], look["radius"], look["font_scale"]) == ("system", "round", 100)
        logo = look["logo"]
        assert logo["file"].endswith(".svg") and logo["file"] != "logo.svg" and (logo["w"], logo["h"], logo["position"]) == (120, 40, "left")
        assert len(look["partners"]) == 1 and look["partners"][0]["alt"] == "Partner"
        with open(os.path.join(_folder(app, new.uid), logo["file"]), "rb") as f:
            stored = f.read().decode()
        assert "script" not in stored and "onload" not in stored and "javascript" not in stored and "<rect" in stored
        assert set(_media_names(_folder(app, new.uid))) == {logo["file"], look["partners"][0]["file"]} | ({logo["icon"]} if logo.get("icon") else set())
    # a file with an organisation design (the RWTH design) comes in whole, tokens included (Ralf 2026-10-19)
    from tests.helpers import rwth_design

    data["theme"] = rwth_design.look()
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    assert "Erscheinungsbild wurde nicht übernommen" not in _upload(other, data, title="Aus der Datei")
    with app.app_context():
        look = theme.of(db.session.get(Survey, 3))
        assert look["preset"] == "org" and look["name"] == "RWTH Aachen" and theme.tokens(look) == theme.tokens(rwth_design.look())


def test_the_consent_names_whoever_read_the_file_in(app, client, survey):
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        consent.config = {**consent.config, "researchers": [account_ref(ME)], "contact_email": "philipp@example.org", "min_age": 16}
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "body": consent.kind.template_body(consent, "de")},
                         "en": {**consent.texts["en"], "body": "My own words"}}
        db.session.commit()
        assert "Philipp" in consent.texts["de"]["body"]
    data = _file(client)
    row = data["blocks"][0]["items"][0]
    assert row["standard_body"] == ["de"] and "contact_email" not in row["config"] and "philipp@example.org" not in json.dumps(data["blocks"][0]["items"][0]["config"])
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    _upload(other, data, title="Welle 2")
    with app.app_context():
        new = db.session.get(Survey, 2).consent_item
        assert _keys(new.config["researchers"]) == [OTHER] and new.config["contact_email"] == "" and new.config["min_age"] == 16
        assert new.texts["de"]["body"] == new.kind.template_body(new, "de") and "Kollegin" in new.texts["de"]["body"]
        assert "Philipp" not in new.texts["de"]["body"]
        assert new.texts["en"]["body"] == "My own words"                                # an edited text stays as written
    # a file without a consent gets the standard one, in front
    data["blocks"] = [b for b in data["blocks"] if not any(i["type"] == "consent" for i in b["items"])]
    _upload(client, data, title="Ohne Einwilligung")
    with app.app_context():
        new = db.session.get(Survey, 3)
        assert new.blocks[0].items[0].type == "consent" and _keys(new.consent_item.config["researchers"]) == [ME]
        assert flow.block_order(flow.tree(new)) == [b.id for b in new.blocks] and len(new.blocks) == 2


def test_a_standard_consent_text_written_anew_is_not_changed_since_its_translation(app, client, survey):
    """The standard text of the consent names whoever reads the file in; the mark of its translation in the file
    names the person before and would show it as changed since the translation (demo files, 2026-10-12)."""
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        consent.config = {**consent.config, "researchers": [account_ref(ME)]}
        consent.texts = {**consent.texts, **{lang: {**consent.texts[lang], "body": consent.kind.template_body(consent, lang)}
                                             for lang in ("de", "en")}}
        db.session.add(TranslationMark(survey_id=1, unit=f"item:{consent.id}", lang="en", path="body",
                                       source=consent.texts["de"]["body"], ai=False))
        db.session.commit()
    data = _file(client)
    assert {(m["unit"], m["path"]) for m in data["translation_marks"]} >= {(f"item:{data['blocks'][0]['items'][0]['id']}", "body")}
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    _upload(other, data)
    with app.app_context():
        new = db.session.get(Survey, 2)
        assert "Kollegin" in new.consent_item.texts["de"]["body"] and "Kollegin" in new.consent_item.texts["en"]["body"]
        assert db.session.scalar(db.select(TranslationMark).filter_by(survey_id=2, unit=f"item:{new.consent_item.id}",
                                                                      path="body")) is None
        with app.test_request_context():
            assert not [m for _, m in translations.findings(new) if "geändert" in m or "changed" in m]


def test_a_failed_import_leaves_nothing(app, client, survey, monkeypatch):
    _with_image(client)
    data = _file(client)

    def broken(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(transfer, "_marks", broken)
    before = _folders(app)
    with app.test_request_context():
        with pytest.raises(RuntimeError):
            transfer.create(data, db.session.get(User, 1))
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1 and db.session.scalar(db.select(db.func.count(Block.id))) == 2
        assert db.session.scalar(db.select(db.func.count(Asset.id))) == 1
    assert _folders(app) == before


def test_translations_and_their_status_follow_the_new_ids(app, client, survey):
    add(client, "single_choice")
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.normalize(s))
        tree["children"].insert(2, {"t": "end", "id": "e1", "text": {"de": "Ende", "en": "End"}, "kind": "screenout", "link": "", "link_mode": "now"})
        s.flow = tree
        for unit, path in (("item:2", "question"), ("flow:e1", "screenout"), ("flow:gone", "screenout"), ("item:99", "question"),
                           ("end", "thanks"), ("title", "title")):
            db.session.add(TranslationMark(survey_id=1, unit=unit, lang="en", path=path, source="x", ai=unit == "end"))
        db.session.commit()
    data = _file(client)
    data["translation_marks"] += [{"unit": "item:2", "lang": "en", "path": "question", "source": "twice"}, {"unit": "x" * 500, "lang": "en", "path": "p"},
                                  {"unit": "end", "lang": "zz", "path": "thanks"}, {"unit": "end", "lang": "en", "path": "p" * 500}, "x", {"unit": 5}]
    data["survey"]["title"] = "Studie B"
    _upload(client, data)
    with app.app_context():
        new = db.session.get(Survey, 2)
        item = new.blocks[1].items[0].id
        marks = {(m.unit, m.path): m for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=2))}
        assert set(marks) == {(f"item:{item}", "question"), ("flow:e1", "screenout"), ("end", "thanks"), ("title", "title")}
        assert marks[("end", "thanks")].ai and marks[(f"item:{item}", "question")].source == "x"


def test_the_pixels_of_all_images_together_are_limited(app, client, survey, monkeypatch):
    """A solid colour compresses to nothing: a file of a few kilobytes could ask for hours of decoding."""
    _with_image(client)
    data = _file(client)
    _item(data, "image")["images"] *= 3
    monkeypatch.setattr(transfer, "MAX_PIXELS", 2)                                    # room for two pixels, three images
    before = _folders(app)
    assert "zu viele Bildpunkte" in _upload(client, data, title="Pixel") and _count(app) == 1 and _folders(app) == before


def test_a_type_without_rules_is_not_read_in(app, client, survey, monkeypatch):
    from app import items

    add(client, "info")
    data = _file(client)
    monkeypatch.setattr(type(items.get("info")), "import_rules", None)
    assert "lässt sich nicht einlesen" in _upload(client, data, title="Neu") and _count(app) == 1


def test_the_signing_key_stays_and_signed_links_come_along(app, client, survey):
    """Signed completion links, panel IDs that take part once, the federal state of a postal code in conditions."""
    from app import panel, secrets_box

    add(client, "postal_code")                                                        # item 2
    client.post("/surveys/1/editor/blocks")
    add(client, "info", block=3)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "pid_panel", "mode": "store", "required": True, "allowed": [], "once": True}],
                   "redirects": {"completed": "https://panel.example/done?id={{pid_panel}}&hash={{sig}}"}, "modes": {}, "rules": [],
                   "sign": {"alg": "sha1", "enc": "base64url", "base": "plain", "key": secrets_box.encrypt("top-secret-key")}}
        q = Quota(survey_id=1, code="land", name="Land")
        q.cells = [QuotaCell(code="nrw", name="NRW", conds=[{"item": "st:2", "op": "eq", "value": "5"}], target=10)]
        db.session.add(q)
        tree = copy.deepcopy(flow.normalize(s))
        last = tree["children"].pop(next(n for n, c in enumerate(tree["children"]) if c.get("block") == 3))
        tree["children"].insert(2, {"t": "branch", "id": "b1", "cond": {"item": "st:2", "op": "in", "value": "5, 9"}, "children": [last]})
        s.flow = tree
        db.session.commit()
        stored = s.panel["sign"]["key"]
    body = client.get(FILE).data.decode()
    assert "top-secret-key" not in body and stored not in body and '"key"' not in json.dumps(json.loads(body)["survey"]["panel"])
    data = json.loads(body)
    assert data["survey"]["panel"]["sign"] == {"alg": "sha1", "enc": "base64url", "base": "plain", "signed": True}
    data["survey"]["panel"]["sign"]["key"] = stored                                   # written into the file by hand: not read
    page = _upload(client, data, title="Signiert")
    assert "Signaturschlüssel steht nicht in der Datei" in page
    with app.app_context():
        new = db.session.get(Survey, 2)
        code = new.blocks[1].items[0].id
        assert new.panel["sign"] == {"alg": "sha1", "enc": "base64url", "base": "plain", "key": ""} and not panel.signs(new)
        assert new.panel["redirects"]["completed"] == "https://panel.example/done?id={{pid_panel}}&hash={{sig}}"
        assert new.panel["params"] == [{"name": "pid_panel", "mode": "store", "required": True, "allowed": [], "once": True}]
        assert any("Signaturschlüssel" in message for _, message in panel.check(new))  # the pre-launch check asks for the key
        branch = next(n for n, _, _ in flow.walk(flow.tree(new)) if n["t"] == "branch")
        assert branch["cond"] == {"item": f"st:{code}", "op": "in", "value": "5, 9"}
        assert new.quotas[0].cells[0].conds == [{"item": f"st:{code}", "op": "eq", "value": "5"}]
    # without a key in the source no link may use {{sig}}
    data["survey"]["panel"]["sign"]["signed"] = False
    assert "Weiterleitung „abgeschlossen“ wurde nicht übernommen" in _upload(client, data, title="Unsigniert")


# --- the file of a survey that is already here ----------------------------------------------------------------


def _pending(app):
    folder = os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "import_pending")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _preview(client, data=None, raw=None, name="befragung.json"):
    """The preview page of an upload and the token that names the file put aside."""
    body = raw if raw is not None else json.dumps(data).encode()
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(body), name)},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page)
    return page, token.group(1) if token else None


def test_the_preview_shows_what_the_file_holds_and_creates_nothing(app, client, survey):
    build(app, client)
    _quota(app)
    data = _file(client)
    page, token = _preview(client, data)
    assert token and _count(app) == 1 and len(_pending(app)) == 1
    assert "Befragung aus der Datei anlegen" in page and "exportiert am" in page and "3 Blöcke, 6 Elemente:" in page
    assert "1 × Bild" in page and "2 Dateien" in page and "mit Logo" in page and "Alter" in page and "1 Quotenberechnung" in page
    page, _ = _preview(client, _file(client, files="0"))
    assert re.search(r"Bilder und Logos</dt>\s*<dd>keine</dd>", page) and _count(app) == 1
    # a type this installation does not know is named before anything is read in
    data["blocks"][0]["items"].append({"type": "hologram", "code": "h"})
    assert "einer Art, die diese Installation nicht kennt" in _preview(client, data)[0]


def test_a_file_of_a_survey_that_is_here_asks_for_a_title_of_its_own(app, client, survey):
    """As duplicating: the same title is not taken; the preview asks for another one and keeps the file meanwhile."""
    data = _file(client)
    page, token = _preview(client, data)
    assert "gibt es bei Ihnen schon" in page and 'value="test (kopie)"' in page.lower() and _count(app) == 1

    def answer(title, token=token, who=client):
        return who.post("/surveys/new/import", data={"token": token, "title-0": title}, follow_redirects=True).data.decode()

    for title in ("", "  test ", "TEST"):                                            # the file's title, or the same one typed
        assert "Auch diesen Titel hat schon eine Ihrer Befragungen" in answer(title) and _count(app) == 1
    assert len(_pending(app)) == 1                                                    # one file per person
    # the file of one person is not the file of another, and a made-up token names nothing
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    assert "liegt nicht mehr vor" in answer("Meins", who=other)
    assert "liegt nicht mehr vor" in answer("Welle 2", token="../../../etc/passwd") and _count(app) == 1
    page = answer("Welle 2")
    assert "„Welle 2“ ist als Entwurf angelegt" in page and _pending(app) == []
    with app.app_context():
        old, new = db.session.get(Survey, 1), db.session.get(Survey, 2)
        assert (old.title, new.title) == ("Test", "Welle 2") and new.uid != old.uid and new.code != old.code
        assert not {b.id for b in new.blocks} & {b.id for b in old.blocks}
        assert not {i.id for b in new.blocks for i in b.items} & {i.id for b in old.blocks for i in b.items}
    assert "liegt nicht mehr vor" in answer("Welle 3")                                # used once
    # a title nobody has: no question; the title of somebody else's survey is no reason either
    assert "ist als Entwurf angelegt" in _upload(client, data, title="Welle 4")
    assert "ist als Entwurf angelegt" in _upload(other, data) and _pending(app) == []
    # a file that is no survey file is refused before anything is kept
    assert "keine lesbare JSON-Datei" in _upload(client, None, raw=b"x") and _pending(app) == []
    # a refused file does not stay either
    bad = copy.deepcopy(data)
    bad["blocks"][0]["items"][0]["config"]["min_age"] = "18"
    assert "lässt sich nicht einlesen" in _upload(client, bad, title="Welle 5") and _pending(app) == []


def test_a_file_put_aside_is_dropped_after_an_hour(app, client, survey):
    data = _file(client)
    _upload(client, data)
    folder = os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "import_pending")
    old = os.path.join(folder, "99-" + "x" * 24 + ".json")
    with open(old, "wb") as f:
        f.write(b"{}")
    os.utime(old, (1, 1))
    _upload(client, data)
    assert len(_pending(app)) == 1 and not os.path.exists(old)


def test_questionnaire_as_word_document(app, client, survey):
    import zipfile

    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data={"code": "TA", "question": "Wie sehr?", "answer_mode": "required",
                                                    "points": "5", "labeling": "full", "stmt_id": "s1", "stmt_code": "",
                                                    "stmt_text": "Ich mag Technik."})
    page = client.get("/surveys/1/data").data.decode()
    assert "Fragebogen mit Codebuch (Word)" in page and "Fragebogen für Papier (Word)" in page and "Als Datei exportieren" in page

    def doc(url):
        r = client.get(url)
        assert r.status_code == 200 and r.headers["Content-Type"].startswith("application/vnd.openxmlformats")
        return zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode()

    book = doc("/surveys/1/export/questionnaire-codebook.docx")
    assert "Wie sehr?" in book and "Ich mag Technik." in book and "TA_1" in book and "Likert" in book
    paper = doc("/surveys/1/export/questionnaire-print.docx")
    assert "Wie sehr?" in paper and "Ich mag Technik." in paper and "TA_1" not in paper and "Likert" not in paper
    assert "Pflichtangaben" in paper
    assert client.get("/surveys/1/export/questionnaire-other.docx").status_code == 404


def test_word_export_without_python_docx_says_so_instead_of_failing(app, client, survey, monkeypatch):
    """A server whose vendor/ was built before python-docx was needed (Ralf's Plesk server, 2026-10-02): a note on
    the export page, not an Internal Server Error; /healthz names the missing package."""
    import sys

    import app as app_package

    monkeypatch.delattr(app_package, "questionnaire_docx", raising=False)
    monkeypatch.setitem(sys.modules, "app.questionnaire_docx", None)  # import fails as without the package
    for version in ("print", "codebook"):
        r = client.get(f"/surveys/1/export/questionnaire-{version}.docx")
        assert r.status_code == 302 and r.headers["Location"].endswith("/surveys/1/data")
    assert "Das Paket python-docx fehlt" in client.get("/surveys/1/data").data.decode()
    monkeypatch.setitem(sys.modules, "docx", None)
    health = client.get("/healthz")
    assert health.status_code == 500 and health.json["deps"]["docx"].startswith("error")


def test_word_questionnaire_holds_the_images(app, client, survey):
    import os
    import zipfile

    from PIL import Image

    from app.models import Asset, Item
    from tests.conftest import add

    add(client, "image")                                                   # item 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], s.uid)
        os.makedirs(folder, exist_ok=True)
        Image.new("RGB", (400, 200), (40, 90, 170)).save(os.path.join(folder, "pic.webp"), "WEBP")  # converted to PNG
        item = db.session.get(Item, 2)
        item.assets.append(Asset(key="", filename="pic.webp", source="upload", status="approved"))
        db.session.commit()
    for version in ("codebook", "print"):
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/surveys/1/export/questionnaire-{version}.docx").data))
        assert any(n.startswith("word/media/") for n in z.namelist()), version


def test_word_questionnaire_types(app, client, survey):
    import re
    import zipfile

    from tests.conftest import add

    add(client, "likert")                                                  # 2: labelled at the ends only
    client.post("/surveys/1/editor/items/2", data={"code": "L", "question": "Skala", "answer_mode": "required",
                                                    "points": "5", "labeling": "endpoints", "stmt_id": "s1",
                                                    "stmt_code": "", "stmt_text": "Aussage"})
    add(client, "slider")                                                  # 3
    add(client, "constant_sum")                                            # 4
    add(client, "sample_compare")                                          # 5: live feedback, left out
    add(client, "conjoint")                                                # 6: not on paper
    xml = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/questionnaire-print.docx").data)) \
        .read("word/document.xml").decode()
    texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml)
    assert "2" not in texts and "3" not in texts and "4" not in texts       # no numbers in the empty middle
    assert "├" in xml and "Summe" in xml and "Conjoint" in xml and "nicht sinnvoll darstellen" in xml
    assert "Live" not in xml and "Stichprobenvergleich" not in xml


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
