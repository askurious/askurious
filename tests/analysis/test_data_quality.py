"""What is stored and exported: answers of skipped pages, "Back", shown orders, sides, separated section."""
import csv
import io

from werkzeug.datastructures import MultiDict

from app import export
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, SplitSession, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


def data(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))


def branch_survey(app, client):
    """consent | block 2: age | branch age < 18 -> block 3: why | block 4: last"""
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks")                                  # 3
    client.post(E + "/blocks")                                  # 4
    add(client, "text_input", block=3)                          # 3
    client.post(E + "/items/3", data={"code": "why", "question": "Warum?"})
    add(client, "text_input", block=4)                          # 4
    client.post(E + "/items/4", data={"code": "last", "question": "Sonst noch?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"},
             "children": [{"t": "block", "block": 3}]},
            {"t": "block", "block": 4}]}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_answers_of_pages_skipped_after_going_back_are_dropped(app, client, survey):
    uid = branch_survey(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "15"})
    p.post(f"/s/{uid}", data={"nav": "next", "i3": "zu jung"})      # the follow-up for under 18
    p.post(f"/s/{uid}", data={"nav": "back"})
    p.post(f"/s/{uid}", data={"nav": "back"})
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})           # now the follow-up does not apply
    assert "Sonst noch" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "nein"})
    row = data(client)[0]
    assert (row["status"], row["age"], row["why"], row["last"]) == ("completed", "30", "", "nein")
    q = app.test_client()                                          # the follow-up stays where it applies
    q.get(f"/s/{uid}")
    q.post(f"/s/{uid}", data=CONSENT_OK)
    q.post(f"/s/{uid}", data={"nav": "next", "i2": "15"})
    q.post(f"/s/{uid}", data={"nav": "next", "i3": "zu jung"})
    q.post(f"/s/{uid}", data={"nav": "next", "i4": ""})
    assert data(client)[1]["why"] == "zu jung"


def test_back_keeps_what_was_entered_on_the_page(app, client, survey):
    uid = branch_survey(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    p.post(f"/s/{uid}", data={"nav": "back", "i4": "schon getippt"})
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(source="real")).answers["4"] == "schon getippt"
    # invalid input: no error, and the answer from before is not kept either (audit 2026-10-05, T-31): open again
    p.post(f"/s/{uid}", data={"nav": "back", "i2": "abc"})
    with app.app_context():
        assert "2" not in db.session.scalar(db.select(Response).filter_by(source="real")).answers


def test_shown_order_exported_for_every_randomized_type(app, client, survey):
    add(client, "ranking")                                          # 2 (random start order by default)
    client.post(E + "/items/2", data=MultiDict([
        ("code", "prio"), ("question", "Wichtig?"), ("randomize", "1"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    add(client, "semantic")                                         # 3
    client.post(E + "/items/3", data=MultiDict([
        ("code", "feel"), ("points", "5"), ("randomize", "1"),
        ("pair_id", "p1"), ("pair_code", ""), ("pair_left", "gut"), ("pair_right", "schlecht"),
        ("pair_id", "p2"), ("pair_code", ""), ("pair_left", "warm"), ("pair_right", "kalt")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Item, 3).config["randomize"]
        s.responses.append(Response(status=Response.COMPLETED, answers={"2": ["2", "1"]},
                                    layout={"pages": [], "stmt": {"2": ["b", "a"], "3": ["p2", "p1"]}}))
        db.session.commit()
        cols = [c for c, _ in export.columns(s)]
        assert "prio_order" in cols and "feel_order" in cols
        row = export.rows(s, include_incomplete=True)[0]
        assert row["prio_order"] == "prio_2|prio_1" and row["feel_order"] == "feel_2|feel_1"


def test_assign_sides_exported_and_order_listed_once_in_codebook(app, client, survey):
    from tests.quotas.test_assign import setup

    setup(client)                                                   # sides drawn per person
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        answer = [{"s": "a", "c": "1", "rt": 500}, {"s": "b", "c": "2", "rt": 600}, {"s": "c", "c": "1", "rt": 700}]
        s.responses.append(Response(status=Response.COMPLETED, answers={"2": answer}))
        db.session.commit()
        r = db.session.scalar(db.select(Response))
        swap = item.kind.swapped(item, r.id, ["a", "b", "c"])
        row = export.rows(s)[0]
        assert row["sort_robot_swap"] == int(swap["a"]) and row["sort_app_swap"] == int(swap["c"])
        names = [v for v, *_ in export.codebook(s)]
        assert names.count("sort_order") == 1 and "sort_robot_swap" in names


def test_codebook_lists_stored_url_parameters(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "psid", "mode": "store"}, {"name": "grp", "mode": "redirect"}]}
        db.session.commit()
        names = [v for v, *_ in export.codebook(s)]
        assert "url_psid" in names and "url_grp" not in names
        assert "url_psid" not in export.codebook_csv(s, public=True)


def test_textarea_line_breaks_count_once_and_postal_codes_need_ascii_digits(app, client, survey):
    add(client, "text_input")                                       # 2
    add(client, "postal_code")                                      # 3
    with app.app_context():
        note, plz = db.session.get(Item, 2), db.session.get(Item, 3)
        note.config = {**note.config, "multiline": True, "max_length": 12}
        db.session.commit()
        assert note.kind.parse_answer(note, {"i2": "abcd\r\nefgh\r\nij"}) == "abcd\nefgh\nij"  # 12 characters
        assert plz.kind.parse_answer(plz, {"i3": "01067"}) == "01067"
        for bad in ("０１０６７", "٠١٠٦٧"):  # full-width, Arabic-Indic
            try:
                plz.kind.parse_answer(plz, {"i3": bad})
                assert False, bad
            except AnswerError:
                pass


def test_postal_codes_only_published_with_confirmation(app, client, survey):
    add(client, "postal_code")                                      # 2
    client.post(E + "/items/2", data={"code": "plz", "question": "PLZ?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert {"plz", "plz_ort"} <= export.open_columns(s)
        s.responses.append(Response(status=Response.COMPLETED, answers={"2": "01067"}))
        db.session.commit()
        assert "01067" not in export.to_csv(s, public=True)
        assert "01067" in export.to_csv(s, public=True, open_answers=True)
        assert "postal codes and exact dates are not included" in export.readme_md(s)


def test_unfinished_separated_section_counts_with_what_was_entered(app, client, survey):
    from tests.quotas.test_split import build, participate

    uid = build(app, client)
    for mail in ["a@x.de", "b@x.de", "c@x.de", "d@x.de", "e@x.de"]:
        participate(app, uid, "1", mail)
    p = app.test_client()                                           # a sixth person leaves the separated section
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2"})
    p.get(f"/s/{uid}")
    with app.app_context():
        split = db.session.scalar(db.select(SplitSession).filter_by(status=SplitSession.ACTIVE))  # PostgreSQL: the
        split.answers = {"3": "weg@x.de"}                           # five finished ones may still wait (K11)                           # typed, never finished
        db.session.commit()
        from app import contact

        assert contact.count(db.session.get(Survey, 1)) == 6
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/contact/export.csv").data.decode("utf-8-sig"))))
    assert "weg@x.de" in [r["email"] for r in rows]


def test_consent_confirmations_fixed_after_start(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                       # 2: something to answer
    client.post(E + "/items/2", data={"code": "q", "question": "Frage"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING
        before = dict(db.session.get(Item, 1).config)
    client.post(E + "/items/1", data={
        "code": "consent", "question": "Einwilligung", "body": "Neuer Text", "duration": "7", "min_age": "18",
        "researchers": "0000-0000-0000-001X", "agree": "Fortsetzen", "disagree": "Nein"})  # ticks removed, age added
    with app.app_context():
        item = db.session.get(Item, 1)
        assert item.config["confirm"] == before["confirm"] and item.config["min_age"] == before["min_age"]
        assert item.config["duration"] == 7 and item.t("de")["body"] == "Neuer Text"  # facts and wording still editable
