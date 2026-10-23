"""The tool layer for AI agents (app/agent_tools.py, experimental "agent"; concept KI-Agenten-Schnittstelle 2026-10-15,
decisions 1b and 2b): the tools run the editor's own views, so roles, drafts, undo steps and the pre-launch check hold as
in the editor; reading gives no answers and no wording of NOT_SHARED constructs."""
import json
from datetime import datetime

import pytest

from app import agent_tools, constructs
from app.extensions import db
from app.models import EditStep, Item, Response, Survey, SurveyMember, User
from tests.conftest import add, experiments_on

ORCID = "0000-0000-0000-001X"
OTHER = "0000-0002-1825-0097"


def _call(app, name, orcid=ORCID, scope="edit", token_id=7, surveys=None, **args):
    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        agent = agent_tools.Agent(user, token_id, "Laptop", scope, surveys)
        return agent_tools.call(agent, name, args)


def _refused(app, name, **args):
    with pytest.raises(agent_tools.ToolError) as e:
        _call(app, name, **args)
    return str(e.value)


@pytest.fixture
def on(app, client, survey):
    experiments_on(client, "agent")
    return survey


def test_nothing_without_the_feature(app, client, survey):
    assert "nicht freigeschaltet" in _refused(app, "list_surveys")


def test_registry_describes_each_tool_once(app):
    names = set(agent_tools.TOOLS)
    assert {"list_surveys", "get_survey", "list_element_types", "list_constructs", "add_block", "update_block",
            "add_item", "update_item", "move_item", "delete_item", "insert_construct", "undo"} <= names
    # nothing that starts, deletes a survey, or touches data, links, members or consent
    assert not any(word in n for n in names for word in ("start", "stop", "data", "link", "member", "consent", "result"))
    for t in agent_tools.TOOLS.values():
        d = t.describe()
        assert d["inputSchema"]["type"] == "object" and d["annotations"]["readOnlyHint"] == t.read_only
        assert set(d["_meta"]["de.askurious/hints"]) == {"readOnlyHint", "consequentialHint", "untrustedContentHint"}
    read = agent_tools.Agent(None, 1, "x", agent_tools.READ)
    assert all(t.read_only for t in agent_tools.available(read))


def test_read_scope_offers_no_write_tool(app, on):
    with pytest.raises(KeyError):
        _call(app, "add_block", scope="read", survey_id=1)


def test_reading_gives_structure_but_no_answers(app, client, on):
    add(client, "text_input")                                         # item 2
    client.post("/surveys/1/editor/items/2", data={"code": "beruf", "question": "Ihr Beruf?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(status=Response.COMPLETED, started_at=datetime(2026, 9, 2),
                                    completed_at=datetime(2026, 9, 2), answers={"2": "Geheimagentin"}))
        db.session.commit()
    listed = _call(app, "list_surveys")["surveys"]
    assert [s["survey_id"] for s in listed] == [1] and listed[0]["editable"] and listed[0]["role"] == "admin"
    got = _call(app, "get_survey", survey_id=1)
    text = json.dumps(got, ensure_ascii=False)
    assert "Ihr Beruf?" in text and "beruf" in text
    assert "Geheimagentin" not in text and "responses" not in text
    consent = next(i for b in got["blocks"] for i in b["items"] if i["type"] == "consent")
    assert "contact_email" not in consent["config"] and "researchers" not in consent["config"]
    assert _call(app, "list_element_types", survey_id=1)["element_types"]
    assert any(c["key"] == "gdms" and not c["wording_shared"] for c in _call(app, "list_constructs", limit=200)["constructs"])


def test_survey_outside_the_token_list_or_without_role_is_not_there(app, client, on):
    assert "Keine Befragung" in _refused(app, "get_survey", survey_id=1, surveys=[99])
    assert "Keine Befragung" in _refused(app, "get_survey", survey_id=42)
    assert _call(app, "list_surveys", surveys=[99])["surveys"] == []


def test_add_and_update_an_element_with_one_undo_step_each(app, client, on):
    added = _call(app, "add_item", survey_id=1, block_id=2, type="single_choice")
    item_id = added["items"][0]["item_id"]
    got = _call(app, "get_item", survey_id=1, item_id=item_id)
    assert "question" in got["fields"] and got["controls"]["answer_mode"]["kind"] == "radio"
    assert got["base"] and "csrf_token" not in got["fields"]
    done = _call(app, "update_item", survey_id=1, item_id=item_id, base=got["base"], fields={
        "code": "lieblingsfarbe", "question": "Welche Farbe?", "answer_mode": "required",
        "option_id": ["", ""], "option_code": ["1", "2"], "option_label": ["Rot", "Blau"]})
    assert done["code"] == "lieblingsfarbe"
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.code == "lieblingsfarbe" and item.answer_mode == "required"
        assert [item.texts["de"]["options"][o["id"]] for o in item.config["options"]] == ["Rot", "Blau"]
        steps = db.session.scalars(db.select(EditStep).order_by(EditStep.id)).all()
        assert [s.data.get("agent") for s in steps] == [7, 7]
        assert all(s.label.startswith("KI-Agent (Token „Laptop“): ") for s in steps)
        assert steps[-1].data["label_en"].startswith("AI agent (token “Laptop”): ")
    # the editor's checks hold: a stale base is a conflict, an unknown field is refused, not added
    assert "Diese Felder hat das Element nicht" in _refused(app, "update_item", survey_id=1, item_id=item_id,
                                                            fields={"evil": "1"})
    assert "Nicht gespeichert" in _refused(app, "update_item", survey_id=1, item_id=item_id, base=got["base"],
                                           fields={"question": "Doch anders?"})
    # the agent's own step can be taken back; a step of the person cannot
    assert _call(app, "undo", survey_id=1)["undone"]
    add(client, "text_input")
    assert "nicht von diesem Agenten" in _refused(app, "undo", survey_id=1)


def test_blocks_moving_and_deleting(app, client, on):
    block = _call(app, "add_block", survey_id=1, title="Demografie")["block_id"]
    _call(app, "update_block", survey_id=1, block_id=block, randomize_items=True)
    first = _call(app, "add_item", survey_id=1, block_id=2, type="text_input")["items"][0]["item_id"]
    moved = _call(app, "move_item", survey_id=1, item_id=first, block_id=block, position=0)
    assert moved["block_id"] == block
    with app.app_context():
        from app.models import Block

        b = db.session.get(Block, block)
        assert b.titles["de"] == "Demografie" and b.randomize_items
    assert _call(app, "delete_item", survey_id=1, item_id=first)["deleted"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Item, first) is None and len(s.trash) == 1      # in the bin, not gone
    assert "Einwilligung" in _refused(app, "delete_item", survey_id=1, item_id=1)
    assert "Einwilligung" in _refused(app, "update_item", survey_id=1, item_id=1, fields={"question": "x"})
    assert "nicht hinzufügen" in _refused(app, "add_item", survey_id=1, block_id=2, type="consent")


def test_viewer_cannot_edit_and_running_survey_is_refused(app, client, on):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    client.get("/auth/logout")
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    client.post("/terms/", data={"accept": "1"})
    experiments_on(client, "agent", orcid=OTHER)
    assert _call(app, "get_survey", orcid=OTHER, survey_id=1)["blocks"]       # reading is fine
    assert "fehlen die Rechte" in _refused(app, "add_item", orcid=OTHER, survey_id=1, block_id=2, type="text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
    assert "Nur Textkorrekturen" in _refused(app, "add_block", survey_id=1)
    assert "Nur Textkorrekturen" in _refused(app, "update_item", survey_id=1, item_id=1, fields={})


def test_not_shared_wording_is_withheld(app, client, on):
    from tests.instrument_permissions import grant_instrument

    grant_instrument(app, "gdms")  # inserting it needs a documented permission since PR #1187
    added = _call(app, "insert_construct", survey_id=1, block_id=2, construct_key="gdms")
    item_id = added["items"][0]["item_id"]
    with app.app_context():
        wording = constructs.instrument_wordings()["gdms"]["statements"][0]["en"]
    got = _call(app, "get_survey", survey_id=1)
    item = next(i for b in got["blocks"] for i in b["items"] if i["id"] == item_id)
    assert item["construct"]["key"] == "gdms" and "Scott" in item["construct"]["source"]
    assert wording not in json.dumps(got, ensure_ascii=False) and "texts" not in item
    one = _call(app, "get_item", survey_id=1, item_id=item_id)
    assert "fields" not in one and wording not in json.dumps(one, ensure_ascii=False)
    assert "Wortlaut" in _refused(app, "update_item", survey_id=1, item_id=item_id, fields={"question": "x"})


def test_form_parser_reads_what_a_browser_sends():
    html = """<form class="stack item-editor"><input name="csrf_token" value="t"><input name="code" value="Q1">
      <input type="checkbox" name="opt" value="a" checked><input type="checkbox" name="opt" value="b">
      <input name="off" value="x" disabled><template><input name="row" value="tpl"></template>
      <textarea name="question">
Hallo &amp; tschüss</textarea><select name="mode"><option value="1">eins<option value="2" selected>zwei</select>
      <button name="after" value="back">s</button></form><form><input name="other"></form>"""
    values, controls = agent_tools.form_fields(html)
    assert values == [("csrf_token", "t"), ("code", "Q1"), ("opt", "a"), ("question", "Hallo & tschüss"), ("mode", "2")]
    assert controls["opt"] == {"kind": "checkbox", "choices": ["a", "b"]} and "row" not in controls and "other" not in controls
    merged = agent_tools.merged_form(values, controls, {"opt": ["a", "b"], "mode": "1"})
    assert merged == [("csrf_token", "t"), ("code", "Q1"), ("opt", "a"), ("opt", "b"), ("question", "Hallo & tschüss"),
                      ("mode", "1")]
    with pytest.raises(agent_tools.ToolError):
        agent_tools.merged_form(values, controls, {"mode": "3"})


def test_interview_guide_is_refused_by_the_write_tools(app, client, on):
    """Code review 2026-10-15, H2: the write tools run the editor's views without before_request, so a guide got a
    block behind its short questionnaire and postscript. Now the tools refuse guides, and an inner call keeps to
    the pages a guide has not (interview.guard)."""
    from tests.interview.test_interview import blocks, make

    experiments_on(client, "agent", "interview")
    code, sid = make(app, client)
    before = blocks(app, sid)
    assert "Interviewleitfäden" in _refused(app, "add_block", survey_id=sid)
    assert blocks(app, sid) == before
    with app.test_request_context():
        guide = db.session.get(Survey, sid)
        user = db.session.scalar(db.select(User).filter_by(orcid=ORCID))
        with agent_tools.acting(agent_tools.Agent(user, 7, "Laptop", "edit", None)):
            with pytest.raises(agent_tools.ToolError):
                agent_tools.editor_call("editor.add_block", guide)
    assert blocks(app, sid) == before
