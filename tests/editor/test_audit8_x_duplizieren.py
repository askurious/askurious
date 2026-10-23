"""Code audit 2026-10-08, package X: duplicating a whole survey takes elements of experimental types only for
whoever has the feature switched on (app/experimental.py), as reading in a survey file does (transfer._questionnaire,
security audit 2026-10-06, S12). The others get the copy without those elements and a note naming them; what
pointed at them (conditions, quotas, sources, tiles) points at nothing in the copy. Since 2026-10-16 no element type is
experimental; the voice message and video / audio stand in for two (features "probe" and "probe2")."""
import pytest

from app import experimental, flow
from app.extensions import db
from app.models import Board, Item, Quota, QuotaCell, Survey
from tests.conftest import add, experiments_on
from tests.helpers.audit_audit_2026_10_06_a import _member, _person

OTHER = "0000-0002-1825-0097"


@pytest.fixture(autouse=True)
def _two_types(monkeypatch):
    from app.items.stimulus import Stimulus
    from app.items.voice import Voice

    features = experimental.FEATURES + [("probe", "Probe", "Test"), ("probe2", "Probe 2", "Test")]
    monkeypatch.setattr(experimental, "FEATURES", features)
    monkeypatch.setattr(experimental, "KEYS", [k for k, _l, _t in features])
    monkeypatch.setattr(Voice, "experimental", "probe", raising=False)
    monkeypatch.setattr(Stimulus, "experimental", "probe2", raising=False)


def _source(app, client):
    """Survey 1: block 2 with voice (2), media (3), a text (4) and a comparison showing 2 and 4 (5); a branch on
    the voice question, a quota cell on it, a dashboard tile of it."""
    experiments_on(client, "probe", "probe2")
    add(client, "voice"), add(client, "media"), add(client, "text_input"), add(client, "sample_compare")
    client.post("/surveys/1/editor/blocks")
    add(client, "info", block=3)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [i.type for i in db.session.get(Item, 2).block.items] == ["voice", "media", "text_input", "sample_compare"]
        compare = db.session.get(Item, 5)
        compare.config = {**compare.config, "sources": [2, 4]}
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "answered", "value": ""},
             "children": [{"t": "block", "block": 3}]}]}
        flow.normalize(s)
        quota = Quota(code="sprache", name="Sprache", mode="hard", position=0)
        quota.cells.append(QuotaCell(code="ja", name="ja", conds=[{"item": 2, "op": "answered", "value": ""}], target=10,
                                     position=0))
        s.quotas.append(quota)
        s.boards.append(Board(name="Tafel", tiles=[{"key": "item:2"}, {"key": "item:4"}], settings={}, position=0))
        db.session.commit()


def _duplicate(client, title):
    return client.post("/surveys/1/duplicate", data={"title": title, "with_people": "1"}, follow_redirects=True).data.decode()


def _copy(title):
    s = db.session.scalar(db.select(Survey).filter_by(title=title))
    return s, {i.type: i for b in s.blocks for i in b.items}


def test_x_duplicate_leaves_out_experimental_elements_of_others(app, client, survey):
    _source(app, client)
    _member(app, 1, OTHER, "editor")
    other = _person(app, OTHER, "Other", may_create=True)             # no experimental feature switched on
    page = _duplicate(other, "Kopie ohne Stimme")
    assert "2 Elemente eines experimentellen Typs" in page and "nicht übernommen" in page
    with app.app_context():
        s, by_type = _copy("Kopie ohne Stimme")
        assert "voice" not in by_type and "media" not in by_type
        assert {"text_input", "sample_compare", "info", "consent"} <= set(by_type)
        assert by_type["sample_compare"].config["sources"] == [by_type["text_input"].id]   # only the twin that is there
        branch = next(n for n, _, _ in flow.walk(s.flow) if n["t"] == "branch")
        assert branch["cond"]["item"] is None                         # tests nothing, as after a file without it
        assert [c["item"] for c in s.quotas[0].cells[0].conds] == [None]
        assert [t["key"] for t in s.boards[0].tiles] == [f"item:{by_type['text_input'].id}"]
        sid = s.id
        from app import design

        found = [f.message for f in design.check(s)]                 # the pre-launch check names what tests nothing
        assert "Bedingung ohne Frage: bitte eine Frage auswählen." in found
        assert any("Teilquote „ja“: Die Frage einer Bedingung gibt es nicht mehr" in m for m in found)
        original = db.session.get(Survey, 1)                          # the original keeps its elements
        assert {"voice", "media"} <= {i.type for b in original.blocks for i in b.items}
    for path in (f"/surveys/{sid}", f"/surveys/{sid}/editor"):                 # the copy opens
        assert other.get(path, follow_redirects=True).status_code == 200, path


def test_x_duplicate_names_the_left_out_elements_in_english(app, client, survey):
    from flask import g

    from app import duplicate
    from app.models import User

    _source(app, client)
    _member(app, 1, OTHER, "editor")
    _person(app, OTHER, "Other", may_create=True)
    with app.test_request_context():
        g.ui_lang = "en"
        codes = [db.session.get(Item, 2).code, db.session.get(Item, 3).code]
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        _new, notes = duplicate.create(db.session.get(Survey, 1), user, "Copy without voice")
        note = next(n for n in notes if "experimental" in n)
        assert note.startswith("2 elements of an experimental type") and all(code in note for code in codes)


def test_x_duplicate_keeps_them_for_whoever_has_the_feature(app, client, survey):
    _source(app, client)
    page = _duplicate(client, "Eigene Kopie")                         # the site admin of survey 1 has voice and media
    assert "experimentellen Typs" not in page
    with app.app_context():
        s, by_type = _copy("Eigene Kopie")
        assert {"voice", "media"} <= set(by_type)
        branch = next(n for n, _, _ in flow.walk(s.flow) if n["t"] == "branch")
        assert branch["cond"]["item"] == by_type["voice"].id


def test_x_duplicate_takes_only_the_features_switched_on(app, client, survey):
    _source(app, client)
    _member(app, 1, OTHER, "editor")
    other = _person(app, OTHER, "Other", may_create=True)
    experiments_on(client, "probe2", orcid=OTHER)                     # media yes, voice no
    page = _duplicate(other, "Kopie mit Medien")
    assert "experimentellen Typs" in page
    with app.app_context():
        _s, by_type = _copy("Kopie mit Medien")
        assert "media" in by_type and "voice" not in by_type
