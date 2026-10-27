"""Restricted instrument wording stays in authorised study views, never in public deposits/dashboard output."""
from tests.instrument_permissions import grant_instrument
import copy
import csv
import io
import json
from datetime import timedelta

import pytest

from app import boards, constructs, ddi, export, zenodo
from app.boards.pages import _public
from app.extensions import db
from app.models import Board, BoardShare, Item, Response, Survey, utcnow
from tests.conftest import add, fill_table


@pytest.fixture
def instrument(app, client, survey):
    grant_instrument(client.application, "gdms")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    app.config["BOARD_CHECK_SECONDS"] = 0
    with app.app_context():
        item = db.session.get(Item, 2)
        original = item.text("statements", "en")[item.config["statements"][0]["id"]]
        cfg = copy.deepcopy(item.config)
        cfg["construct"]["unlocked"] = True
        cfg["statements"][0]["code"] = "renamed_1"
        cfg["level"] = "ordinal"
        item.config = cfg
        texts = copy.deepcopy(item.texts)
        for lang in ("de", "en"):
            texts[lang]["question"] = f"Protected instruction {lang}"
            texts[lang]["statements"][cfg["statements"][0]["id"]] = f"**Translated protected statement {lang}**"
            texts[lang]["labels"] = [f"Protected low {lang}", "", "", "", f"Protected high {lang}"]
        item.texts = texts
        item.code = "decisions"
        db.session.commit()
    return original


def _forbidden(text):
    assert "Protected instruction" not in text
    assert "Translated protected statement" not in text
    assert "Protected low" not in text and "Protected high" not in text


def test_public_deposit_has_ids_and_numeric_codes_without_anchor_wording(app, client, instrument):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        files = dict(zenodo.package(survey))
        for name in ("questionnaire.md", "codebook.csv", "constructs.md"):
            text = files[name].decode()
            _forbidden(text)
            assert "renamed_1" in text and "wording not included" in text.lower()
        # the DDI and CDIF files of the deposit describe the column without the wording (2026-10-27)
        for name in ("ddi-lifecycle.xml", "cdif.jsonld"):
            text = files[name].decode()
            _forbidden(text)
            assert "renamed_1" in text
        assert "Protected instruction de" in ddi.build(survey).decode()  # the team's download keeps it
        rows = {r["variable"]: r for r in csv.DictReader(io.StringIO(files["codebook.csv"].decode("utf-8-sig")))}
        assert rows["renamed_1"]["label"] == "renamed_1"
        assert rows["renamed_1"]["values"].startswith("1 | 2 | 3 | 4 | 5")
        # Private exports and the stored translations are not modified by producing the deposit.
        assert "Protected low de" in export.codebook_csv(survey)
        assert "Translated protected statement de" in export.questionnaire_md(survey)
        assert "Translated protected statement de" in export.constructs_md(survey)


def test_loop_and_score_columns_are_also_neutral_and_missing_catalogue_does_not_lift_restriction(app, client, instrument, monkeypatch):
    from tests.editor.test_loops import loop_url

    fill_table(client, loop_url(client), occasion=["first", "second"])
    with app.app_context():
        survey = db.session.get(Survey, 1)
        # The guard uses recorded provenance, not the availability of a current database catalogue record.
        assert constructs.wording_withheld(db.session.get(Item, 2))
        monkeypatch.setattr(constructs, "find", lambda key: None)
        rows = list(csv.DictReader(io.StringIO(export.codebook_csv(survey, public=True, scales=True))))
        variables = {r["\ufeffvariable"]: r for r in rows}
        assert "renamed_1_1" in variables and "renamed_1_2" in variables
        withheld = export.withheld_columns(survey, scales=True)
        assert len(withheld) > 50  # the 25 statements, in two passes, plus derived scores
        for code in withheld:
            assert variables[code]["label"] == code
            _forbidden(json.dumps(variables[code]))


def _board_with_answers(app, client, split):
    if split:
        add(client, "single_choice")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        if split:
            group = db.session.get(Item, 3)
            group.config = {**group.config, "options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}]}
            group.texts = {lang: {"question": "Group", "options": {"a": "A", "b": "B"}} for lang in ("de", "en")}
        board = Board(name="Results", position=0, tiles=[{"key": "item:2", "view": "table", "w": 12,
                                                         "by": "item:3" if split else "",
                                                         "title": "Translated protected statement de"}], settings={})
        survey.boards.append(board)
        now = utcnow()
        for n in range(40):
            answers = {"2": {s["id"]: 1 if n % 2 else 5 for s in item.config["statements"]}}
            if split:
                answers["3"] = "1" if n < 20 else "2"
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    answers=answers, started_at=now - timedelta(minutes=5), completed_at=now))
        board.shares.append(BoardShare(token="instrument-wording", label="Public", hall=False,
                                       expires_at=now + timedelta(days=2), valid=30, revoked=False, opens=0))
        db.session.commit()


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_public_html_embed_polling_and_cached_projection_use_ids(app, client, instrument, split, lang):
    _board_with_answers(app, client, split)
    with app.test_request_context():
        board = db.session.get(Board, 1)
        internal = boards.state(board, fresh=True, lang=lang, live=True)
        before = copy.deepcopy(internal)
        assert "Translated protected statement" in json.dumps(internal)
        public = _public(board, internal)
        assert internal == before
        _forbidden(json.dumps(public))
        assert "renamed_1" in json.dumps(public)
        assert public["key"] != internal["key"]
        public_tile = public["tiles"][0]
        rows = public_tile["split"]["rows"] if split else public_tile["rows"]
        assert rows[0]["label"] == "renamed_1"
        assert public_tile["n"] == internal["tiles"][0]["n"]
        old_version = internal["key"][:16]
    guest = app.test_client()
    for path in ("", "/embed", "/state"):
        response = guest.get(f"/d/instrument-wording{path}?lang={lang}&v={old_version}")
        assert response.status_code == 200
        text = response.data.decode()
        _forbidden(text)
        assert "renamed_1" in text
    private = client.get(f"/surveys/1/dashboards/1/view?lang={lang}")
    assert "Translated protected statement" in private.data.decode()


def test_tam_wording_is_now_withheld_from_public_outputs(app, client, survey):
    grant_instrument(client.application, "tam_pu")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tam_pu"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert constructs.wording_withheld(item)
        assert export.withheld(item.survey)


def test_toolbox_items_cannot_lose_provenance_by_becoming_linked(app, client, instrument):
    from app import linked

    with app.app_context():
        item = db.session.get(Item, 2)
        assert linked.linkable(item) is not None
        assert constructs.toolbox_key(item) == "gdms"


def test_restricted_group_source_is_neutral_even_on_an_unrestricted_tile(app, client, instrument):
    _board_with_answers(app, client, split=True)
    with app.test_request_context():
        group = db.session.get(Item, 3)
        # Existing/imported provenance can occur on a different item type. A restriction still applies.
        group.config = {**group.config, "construct": {"key": "gdms"}}
        item = db.session.get(Item, 2)
        item.config = {k: v for k, v in item.config.items() if k != "construct"}
        board = db.session.get(Board, 1)
        state = boards.build(board, lang="en") | {"key": "previous-state"}
        internal = copy.deepcopy(state)
        public = _public(board, state)
        tile = public["tiles"][0]
        assert state == internal
        assert tile["by_title"] == group.code
        assert all(g["label"].startswith(f"{group.code}:group_") for g in tile["split"]["groups"])
        assert [g["n"] for g in tile["split"]["groups"]] == [20, 20]
        # The unrestricted result retains its wording; only the restricted grouping is neutralised.
        assert tile["split"]["rows"][0]["label"] == "Translated protected statement en"


def test_vignette_position_columns_keep_the_restriction(app, client, instrument):
    from tests.analysis.test_vignettes import FORM, _add, _node

    with app.app_context():
        item = db.session.get(Item, 2)
        cfg = copy.deepcopy(item.config)
        cfg["statements"][0]["code"] = "decisions_1"
        item.config = cfg
        db.session.commit()
    _add(client)
    page = f"/surveys/1/flow/vignettes/{_node(app)['id']}"
    client.post(page, data=FORM)
    client.post(page, data={"action": "generate"})
    with app.app_context():
        text = export.codebook_csv(db.session.get(Survey, 1), public=True, scales=True)
        _forbidden(text)
        rows = {r["variable"]: r for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))}
        assert rows["decisions_1_p1"]["label"] == "decisions_1_p1"
        assert rows["decisions_1_p4"]["notes"] == export.WITHHELD
