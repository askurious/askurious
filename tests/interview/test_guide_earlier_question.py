"""A condition on an earlier concrete question of the same Leitfrage (concept bedingung-konkrete-frage, Ralf 2026-10-22:
A and E1-E9 as recommended): "nur wenn K1 trifft zu / trifft nicht zu" on a concrete question and on an Unterfrage
(with the concrete questions of its Leitfrage); the named question gets the switch "trifft zu", the export its
column, the documents the condition in words; a condition whose question is gone or below gives a warning."""
import json

from app.extensions import db
from app.models import Item, Survey
from tests.interview.test_guide_follow_ups import fill
from tests.interview.test_interview_data import start
from tests.interview.test_subquestions import act, guide_with_questions


def test_editor_stores_and_offers_earlier_questions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, b, case="p:k1:no")                         # k4 only if K1 does not apply; k2 is a Nachfrage
    with app.app_context():
        item = db.session.get(Item, b)
        assert item.config["probe_if"]["k4"] == {"probe": "k1", "fits": False}
        tree = item.kind.probe_tree(item)
        assert [(p["id"], p["n"], p["fit"]) for p in tree] == [("k1", 1, True), ("k3", 2, False), ("k4", 3, False)]
        assert tree[2]["cond_text"].startswith("falls K1 „Arbeitest du gerade an einer Haus- oder")  # cut at 50 characters
        assert tree[2]["cond_text"].endswith("“ nicht zutraf")
        assert tree[2]["cond_attr"] == {"probe": "k1", "fits": False, "of": b, "n": 1}
        assert item.kind.check_warnings(item) == []
        # only questions above the row are offered, Nachfragen never
        assert [v for v, _ in item.kind.fit_choices(item, before="k3")] == ["p:k1:yes", "p:k1:no"]
        assert [v for v, _ in item.kind.fit_choices(item, before="k4")] == ["p:k1:yes", "p:k1:no", "p:k3:yes", "p:k3:no"]
    page = client.get(f"/surveys/{sid}/editor/items/{b}?embed=1").data.decode()
    assert "data-probe-earlier" in page and 'value="p:k1:no" selected' in page and "Frühere konkrete Frage" in page
    # a row never names itself
    fill(client, sid, b, case="p:k4:yes")
    with app.app_context():
        assert "k4" not in db.session.get(Item, b).config.get("probe_if", {})


def test_switch_export_and_warning(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, b, case="p:k3:yes")                        # k3 has no Nachfragen but is named: it gets the switch
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-fit="{b}:k3"' in page and f'data-iv-fit="{b}:k1"' in page
    assert "&#34;probe&#34;: &#34;k3&#34;" in page and f"&#34;of&#34;: {b}" in page
    rid = start(app, client, code)
    payload = {"states": {}, "ticks": [], "fits": {f"{b}:k3": "no"}, "notes": {}, "marks": [], "elapsed": 0, "blocks": {}}
    assert client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json").status_code == 200
    csv_ = client.get(f"/guide/{code}/interviews/cases.csv?probe=1").data.decode("utf-8-sig")
    head, first_row = csv_.splitlines()[:2]
    with app.app_context():
        column = f"{db.session.get(Item, b).code}_k2_zu"         # k3 is K2: Nachfragen do not count
    cols = head.split(";")
    assert column in cols and first_row.split(";")[cols.index(column)] == "0"
    assert "falls K2 „Wie viele Stunden" in client.get(f"/guide/{code}/print").data.decode()
    # the named question moved below: a warning, the condition stays (E9)
    fill(client, sid, b, case="p:k3:yes", probe_id=["k1", "k2", "k4", "k3"],
         probe_label=["Arbeitest du gerade an einer Haus- oder Abschlussarbeit?", "An welchem Punkt der Arbeit stehst du gerade?",
                      "Arbeitest du neben dem Studium?", "Wie viele Stunden studierst du neben den Vorlesungen?"],
         probe_if=["", "", "p:k3:yes", ""])
    with app.app_context():
        item = db.session.get(Item, b)
        assert item.config["probe_if"]["k4"] == {"probe": "k3", "fits": True}
        assert any("steht nicht mehr davor" in w for w in item.kind.check_warnings(item))


def test_sub_question_on_a_concrete_question_of_its_leitfrage(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie kommst du mit der Arbeit voran?", "case": "p:k1:yes"})
    with app.app_context():
        sub, lead = db.session.get(Item, b), db.session.get(Item, a)
        assert sub.config["case"] == {"probe": "k1", "fits": True}
        assert sub.kind.case_text(sub).startswith("falls K1 „Arbeitest du")
        assert sub.kind.case_attr(sub) == {"probe": "k1", "fits": True, "of": a, "n": 1}
        assert "k1" in lead.kind.switched(lead)
        # a copy keeps it (the row id comes along), only answers of the Kurzfragebogen are remapped
        assert sub.kind.remap_config(sub.config, lambda old: None)["case"] == {"probe": "k1", "fits": True}
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-sub="{b}" data-iv-if=' in page and f'data-iv-fit="{a}:k1"' in page
    # a row that is not one of the Leitfrage's concrete questions is not taken
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie kommst du mit der Arbeit voran?", "case": "p:zz:yes"})
    with app.app_context():
        assert "case" not in db.session.get(Item, b).config


def test_conversation_shows_where_a_question_belongs(app, client):
    """Ralf 2026-10-22: in the interview view the Unterfragen hang from a line under their Leitfrage, the concrete
    questions carry K1, K2, and a condition stands over its row in the words of the switch, with its state."""
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a, case="p:k1:no")                         # k4 only if K1 does not apply; k2 is a Nachfrage of K1
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie kommst du mit der Arbeit voran?", "case": "p:k1:yes"})
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert '<div class="iv-tree"><div class="iv-tree-head">' in page and 'class="iv-tree iv-tree-thin"' in page
    assert '<span class="iv-k">K1</span>' in page and '<span class="iv-k">K3</span>' in page
    assert "nur wenn K1 trifft nicht zu" in page                   # k4 on K1 of its own Leitfrage
    assert "nur wenn K1 der Leitfrage trifft zu" in page           # the Unterfrage on K1 of its Leitfrage
    assert "Nachfrage zu K1" in page and "Unterfrage 1 zur Leitfrage" in page
    assert page.count('class="iv-cond-state" data-iv-off') >= 3
    with app.app_context():
        lead = db.session.get(Item, a)
        assert lead.kind.cond_label(lead, {"aspect": "zz", "mode": "present"}) == ""   # names nothing: no line


def test_text_size_of_the_interview_view(app, client):
    """Ralf 2026-10-22: A− / A+ sets the text size of the interview view only (90 to 160 %), kept on the device; the
    bar keeps the size of "Konto", columns and the list of topic blocks stack earlier at a large size."""
    from pathlib import Path

    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert " data-iv-page" in page and 'data-iv-size="-1"' in page and 'data-iv-size="1"' in page and "data-iv-size-value" in page
    static = Path(__file__).resolve().parents[2] / "app" / "static"
    css = (static / "app.css").read_text(encoding="utf-8")
    for size in (90, 100, 112, 125, 140, 160):
        assert f':root[data-iv-size="{size}"] {{ font-size: {size}%; }}' in css
    assert ".iv-bar { zoom: var(--iv-bar-zoom, 1); }" in css and "@container iv (max-width: 40em)" in css
    init = (static / "theme-init.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("askurious-iv-size")' in init and "(90|100|112|125|140|160)" in init
    assert "var SIZES = [90, 100, 112, 125, 140, 160]" in (static / "interview.js").read_text(encoding="utf-8")
