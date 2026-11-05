"""The tools of concept konzept-mcp-ablauf-werkzeuge-2026-11-04.md (app/agent_tools.py): a new draft, its title, the flow
with random assignment, image prompts and generated images up to a person's approval, and whether a licensed construct
can be inserted. As the other tools they run the editor's own views."""
import base64
import copy
import io
import json

import pytest
from PIL import Image

from app import agent_tools, ai, flow, media
from app.extensions import db
from app.models import AccessLog, AgentToken, Asset, EditStep, Item, Survey, User
from tests.conftest import experiments_on

ORCID = "0000-0000-0000-001X"


def _call(app, tool, /, scope="edit", token_id=7, **args):  # `name` is an argument of some tools
    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ORCID))
        return agent_tools.call(agent_tools.Agent(user, token_id, "Laptop", scope), tool, args)


def _refused(app, tool, /, **args):
    with pytest.raises(agent_tools.ToolError) as e:
        _call(app, tool, **args)
    return str(e.value)


@pytest.fixture
def on(app, client, survey):
    experiments_on(client, "agent")
    return survey


def _tree(sid):
    db.session.expire_all()
    return flow.tree(db.session.get(Survey, sid))


def _blocks(sid):
    db.session.expire_all()
    return [b.id for b in db.session.get(Survey, sid).blocks]


def _png():
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


@pytest.fixture
def images(app, monkeypatch):
    """No image service: every generation gives the same small picture."""
    calls = []

    def fake(prompt, size="1536x1024", purpose="image", item_type=None):
        calls.append(prompt)
        return _png()

    monkeypatch.setattr(ai, "generate_image", fake)
    return calls


def test_create_needs_its_own_scope_and_a_token_for_all_surveys(app, client, on):
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ORCID))
        names = {s: {t.name for t in agent_tools.available(agent_tools.Agent(user, 1, "x", s))} for s in ("read", "edit", "create")}
        limited = {t.name for t in agent_tools.available(agent_tools.Agent(user, 1, "x", "create", [1]))}
    assert "create_survey" in names["create"] and "add_flow_node" in names["create"]
    assert "create_survey" not in names["edit"] | names["read"] | limited
    assert "add_flow_node" in names["edit"] and "add_flow_node" not in names["read"]
    with pytest.raises(KeyError):
        _call(app, "create_survey", title="Neu")


def test_create_makes_what_the_button_makes(app, client, on):
    with app.app_context():
        out = _call(app, "create_survey", scope="create", title="Framing", research_question="Wirkt es?", language="en")
        survey = db.session.get(Survey, out["survey_id"])
        assert survey.title == "Framing" and survey.research_question == "Wirkt es?" and survey.status == Survey.DRAFT
        assert survey.default_language == "en" and survey.consent_item is not None and len(survey.blocks) >= 2
        assert survey.role_of(db.session.scalar(db.select(User).filter_by(orcid=ORCID))) is not None
        assert out["blocks"]


def test_create_is_limited_per_day(app, client, on):
    app.config["AGENT_SURVEY_LIMIT"] = 1
    with app.app_context():
        _call(app, "create_survey", scope="create", title="Eins")
        before = db.session.scalar(db.select(db.func.count(Survey.id)))
        with pytest.raises(agent_tools.ToolError):
            _call(app, "create_survey", scope="create", title="Drei")
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == before


def test_update_survey_changes_only_what_is_given(app, client, on):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.description, langs = "Alt", list(survey.languages)
        survey.project = "Projekt"
        db.session.commit()
        _call(app, "update_survey", survey_id=1, research_question="Warum?")
        db.session.expire_all()
        survey = db.session.get(Survey, 1)
        assert survey.research_question == "Warum?" and survey.description == "Alt" and survey.project == "Projekt"
        assert survey.title and list(survey.languages) == langs
        assert "ändern" in _refused(app, "update_survey", survey_id=1)


def test_a_randomisation_built_from_nothing(app, client, on):
    """The core of concept section 2: two blocks as conditions, assigned at random, without dragging."""
    with app.app_context():
        one = _call(app, "add_block", survey_id=1, title="Gewinn")["block_id"]
        two = _call(app, "add_block", survey_id=1, title="Verlust")["block_id"]
        node = _call(app, "add_flow_node", survey_id=1, kind="rand", target_block_id=one, mode="wrap")
        assert node["kind"] == "rand" and node["code"]
        nid = node["node_id"]
        _call(app, "move_block", survey_id=1, block_id=two, target_node_id=nid, mode="into")
        found, _ = flow.find(_tree(1), nid)
        assert [c.get("block") for c in found["children"]] == [one, two]
        _call(app, "update_flow_node", survey_id=1, node_id=nid, name="Framing", code="frame", k=1, balanced=False,
              levels=[{"level": 1, "label": "gain"}, {"level": 2, "label": "loss"}])
        found, _ = flow.find(_tree(1), nid)
        assert (found["name"], found["code"], found["balanced"]) == ("Framing", "frame", False)
        assert [(c["level"], c["label"]) for c in found["children"]] == [(1, "gain"), (2, "loss")]
        _call(app, "update_flow_node", survey_id=1, node_id=nid, shuffle=True)  # the rest stays
        found, _ = flow.find(_tree(1), nid)
        assert found["shuffle"] and found["code"] == "frame" and found["balanced"] is False
        assert [c["label"] for c in found["children"]] == ["gain", "loss"]


def test_flow_steps_are_taken_back_one_by_one(app, client, on):
    with app.app_context():
        block = _blocks(1)[-1]
        nid = _call(app, "add_flow_node", survey_id=1, kind="rand", target_block_id=block, mode="wrap")["node_id"]
        assert flow.find(_tree(1), nid)[0] is not None
        steps = db.session.scalar(db.select(db.func.count(EditStep.id)))
        _call(app, "update_flow_node", survey_id=1, node_id=nid, code="frame")
        assert db.session.scalar(db.select(db.func.count(EditStep.id))) == steps + 1
        _call(app, "remove_flow_node", survey_id=1, node_id=nid)
        assert flow.find(_tree(1), nid)[0] is None and flow.find(_tree(1), f"k{block}")[0] is not None
        _call(app, "undo", survey_id=1)
        assert flow.find(_tree(1), nid)[0]["code"] == "frame"


def test_a_condition_with_its_stored_form(app, client, on):
    with app.app_context():
        item = _call(app, "add_item", survey_id=1, block_id=_blocks(1)[-1], type="single_choice")["items"][0]["item_id"]
        nid = _call(app, "add_flow_node", survey_id=1, kind="branch", target_node_id="root", mode="into")["node_id"]
        _call(app, "update_flow_node", survey_id=1, node_id=nid, condition={"item": item, "op": "eq", "value": "1"})
        assert flow.find(_tree(1), nid)[0]["cond"] == {"item": item, "op": "eq", "value": "1"}
        _call(app, "update_flow_node", survey_id=1, node_id=nid, name="Nur Frauen")  # the condition stays
        node = flow.find(_tree(1), nid)[0]
        assert node["name"] == "Nur Frauen" and node["cond"]["item"] == item
        assert "item" in _refused(app, "update_flow_node", survey_id=1, node_id=nid, condition={"op": "eq"})
        assert "Element" in _refused(app, "update_flow_node", survey_id=1, node_id=nid, condition={"item": 99999})
        survey = db.session.get(Survey, 1)
        root = copy.deepcopy(flow.tree(survey))
        part = {"item": item, "op": "eq", "value": "1"}
        flow.find(root, nid)[0]["cond"] = {"conds": [part, part], "rule": "any"}
        survey.flow = root
        db.session.commit()
        assert "Ablauf" in _refused(app, "update_flow_node", survey_id=1, node_id=nid, name="x")


def test_flow_tools_refuse_what_the_flow_page_refuses(app, client, on):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        consent = survey.consent_item.block_id if survey.consent_item else None
        nid = _call(app, "add_flow_node", survey_id=1, kind="rand")["node_id"]
        if consent:
            assert _refused(app, "move_block", survey_id=1, block_id=consent, target_node_id=nid, mode="into")
            assert _refused(app, "add_flow_node", survey_id=1, kind="rand", target_block_id=consent, mode="wrap")
        assert _refused(app, "remove_flow_node", survey_id=1, node_id="nope")
        assert _refused(app, "remove_flow_node", survey_id=1, node_id=f"k{_blocks(1)[-1]}")
        assert _refused(app, "add_flow_node", survey_id=1, kind="rand", mode="into")
        assert _refused(app, "add_flow_node", survey_id=1, kind="ws")  # a web service sends data out: not here
        assert _refused(app, "update_flow_node", survey_id=1, node_id=nid, condition={"item": 1})
        assert _refused(app, "move_block", survey_id=1, node_id=nid, block_id=_blocks(1)[-1], target_node_id="root", mode="into")


def test_a_role_below_editor_and_a_running_survey_are_refused(app, client, on):
    with app.app_context():
        nid = _call(app, "add_flow_node", survey_id=1, kind="rand")["node_id"]
        survey = db.session.get(Survey, 1)
        survey.status = Survey.RUNNING
        db.session.commit()
        assert _refused(app, "remove_flow_node", survey_id=1, node_id=nid)
        assert _refused(app, "update_survey", survey_id=1, title="x")


def _image(app):
    block = _blocks(1)[-1]
    return _call(app, "add_item", survey_id=1, block_id=block, type="image")["items"][0]["item_id"]


def test_image_settings_are_fields_of_the_element(app, client, on):
    with app.app_context():
        item = _image(app)
        shown = _call(app, "get_item", survey_id=1, item_id=item)
        assert {"prompt", "size", "source", "text_free"} <= set(shown["fields"]) <= set(shown["controls"]) | set(shown["fields"])
        assert "1536x1024" in shown["controls"]["size"]["choices"]
        steps = db.session.scalar(db.select(db.func.count(EditStep.id)))
        _call(app, "update_item", survey_id=1, item_id=item, fields={"prompt": "Eine Zeitungsseite", "size": "1024x1024",
                                                                     "text_free": False})
        assert db.session.scalar(db.select(db.func.count(EditStep.id))) == steps + 1
        got = _call(app, "get_item", survey_id=1, item_id=item)["fields"]
        assert (got["prompt"], got["size"], got["source"], got["text_free"]) == ("Eine Zeitungsseite", "1024x1024", "ai", False)
        assert _refused(app, "update_item", survey_id=1, item_id=item, fields={"size": "1x1"})
        assert _refused(app, "update_item", survey_id=1, item_id=item, fields={"prompt": "x", "question": "y"})
        _call(app, "undo", survey_id=1)
        assert _call(app, "get_item", survey_id=1, item_id=item)["fields"]["source"] == "upload"


def test_generate_read_back_and_a_person_approves(app, client, on, images):
    """The acceptance criterion of concept section 3a, up to the approval by hand."""
    with app.app_context():
        item = _image(app)
        assert _refused(app, "generate_image", survey_id=1, item_id=item)  # no prompt yet: nothing is paid for
        assert images == []
        _call(app, "update_item", survey_id=1, item_id=item, fields={"prompt": "Schlagzeile: 90 von 100 überleben"})
        out = _call(app, "generate_image", survey_id=1, item_id=item)
        assert len(images) == 1 and "90 von 100" in images[0] and out["status"] == Asset.PENDING
        assert out["images_left_today"] == app.config["AGENT_IMAGE_LIMIT"] - 1 and out["prompt"] == images[0]
        listed = _call(app, "get_item", survey_id=1, item_id=item)
        assert listed["images"]["assets"][0]["asset_id"] == out["asset_id"]
        assert any("freigegeben" in f["message"] or "approved" in f["message"] for f in listed["design_check"])
        seen = _call(app, "get_image", survey_id=1, asset_id=out["asset_id"])
        assert seen["_picture"]["mimeType"] == "image/webp" and base64.b64decode(seen["_picture"]["data"])
        assert not any(t in agent_tools.TOOLS for t in ("approve_image", "review_image"))
        asset = db.session.get(Asset, out["asset_id"])
        asset.status = Asset.APPROVED
        db.session.commit()
        listed = _call(app, "get_item", survey_id=1, item_id=item)
        assert not any("freigegeben" in f["message"] or "approved" in f["message"] for f in listed["design_check"])


def test_a_failed_generation_says_why(app, client, on, monkeypatch):
    def broken(*_, **__):
        raise ai.AIError("Der Dienst ist nicht erreichbar.")

    monkeypatch.setattr(ai, "generate_image", broken)
    with app.app_context():
        item = _image(app)
        _call(app, "update_item", survey_id=1, item_id=item, fields={"prompt": "x y z"})
        assert "nicht erreichbar" in _refused(app, "generate_image", survey_id=1, item_id=item)


def test_images_per_token_are_limited(app, client, on, images):
    app.config["AGENT_IMAGE_LIMIT"] = 1
    with app.app_context():
        item = _image(app)
        _call(app, "update_item", survey_id=1, item_id=item, fields={"prompt": "x y z"})
        _call(app, "generate_image", survey_id=1, item_id=item)
        assert _refused(app, "generate_image", survey_id=1, item_id=item)
        assert len(images) == 1
        _call(app, "generate_image", survey_id=1, item_id=item, token_id=8)  # another token has its own day


def test_an_uploaded_picture_stays_out_of_get_image(app, client, on):
    with app.app_context():
        item = _image(app)
        row = db.session.get(Item, item)
        survey = db.session.get(Survey, 1)
        name = media.save_b64(survey, _png())
        row.assets.append(Asset(key="", filename=name, source="upload", status=Asset.APPROVED))
        db.session.commit()
        assert _refused(app, "get_image", survey_id=1, asset_id=row.assets[0].id)


def test_construct_access_says_it_beforehand(app, client, on):
    with app.app_context():
        assert _call(app, "construct_access", construct_key="age")["allowed"] is True
        no_survey = _call(app, "construct_access", construct_key="kut")
        assert no_survey["allowed"] is False and no_survey["permission_required"] is True
        asked = _call(app, "construct_access", construct_key="kut", survey_id=1)
        assert asked["allowed"] is False and "Berechtigung" in asked["reason"]
        survey = db.session.get(Survey, 1)
        app.config["INSTRUMENT_WORDING_PERMISSIONS"] = [{"key": "kut", "survey_uid": survey.uid, "orcid": ORCID,
                                                         "languages": ["de", "en"], "evidence": "Brief 2026"}]
        out = _call(app, "construct_access", construct_key="kut", survey_id=1)
        assert out["allowed"] is True and "Brief" not in json.dumps(out)
        assert _refused(app, "construct_access", construct_key="nope")


def test_the_endpoint_hands_a_picture_on_and_logs_the_image(app, client, on, images):
    from tests.privacy.test_agent_access import _create, _tool

    r, token = _create(client, scope="create")
    assert token
    with app.app_context():
        assert db.session.scalar(db.select(AgentToken.scope)) == "create"
    api = app.test_client()
    made = _tool(api, token, "add_block", survey_id=1).get_json()["result"]["structuredContent"]
    item = _tool(api, token, "add_item", survey_id=1, block_id=made["block_id"], type="image").get_json()["result"]
    item = item["structuredContent"]["items"][0]["item_id"]
    _tool(api, token, "update_item", survey_id=1, item_id=item, fields={"prompt": "Eine Seite"})
    out = _tool(api, token, "generate_image", survey_id=1, item_id=item).get_json()["result"]
    assert out["isError"] is False, out
    asset = out["structuredContent"]["asset_id"]
    seen = _tool(api, token, "get_image", survey_id=1, asset_id=asset).get_json()["result"]
    assert any(c["type"] == "image" and c["data"] for c in seen["content"])
    assert "_picture" not in seen["structuredContent"]
    assert _tool(api, token, "create_survey", title="Über das Token").get_json()["result"]["isError"] is False
    with app.app_context():
        details = list(db.session.scalars(db.select(AccessLog.detail)))
    assert any("generate_image" in d and f"Bild {asset}" in d for d in details)
