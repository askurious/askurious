"""Filters "Nur stellen, wenn …" on Leitfragen and Themenblöcke of an interview guide (app/interview_gate.py, concept
konzept-filter-leitfragen, E1-E7 as recommended, Ralf 2026-11-03): stored on the Leitfrage (config "gate") and the
Themenblock (settings "gate"), only on an earlier Leitfrage's concrete question or the Kurzfragebogen before the
conversation; the named question gets the switch; "nicht gestellt" by the filter is stored with "by": "filter";
copies, survey files and documents carry it. The rules of the interview view run under Node (interview_gate_cases.js)."""
import html
import json
import re
import shutil
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.helpers.bounded_run import run
from tests.interview.test_guide_follow_ups import fill
from tests.interview.test_interview import _read_in, docx_text
from tests.interview.test_interview_data import start
from tests.interview.test_subquestions import act, guide_with_questions

NODE = shutil.which("node")
ROOT = Path(__file__).parents[2]


def edit(client, sid, item, **data):
    return client.post(f"/surveys/{sid}/editor/items/{item}", data={"question": "Wie sieht Ihr Alltag mit der Pflege aus?", **data})


def later_block(app, client, code, sid):
    """The id of a Themenblock after the one guide_with_questions fills (added when the template has none)."""
    from app import interview

    with app.app_context():
        blocks = interview.guide_blocks(db.session.get(Survey, sid))
    if len(blocks) < 3:
        client.post(f"/guide/{code}/control", data={"action": "add_block"})
        with app.app_context():
            blocks = interview.guide_blocks(db.session.get(Survey, sid))
    return blocks[2].id


def linear(app, sid, short=None):
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.interview = {**survey.interview, "order": "linear", **({"short": short} if short else {})}
        db.session.commit()


def test_leitfrage_filter_stored_named_and_refused_when_later(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)                                          # a: K1 "Arbeitest du gerade an …", K2, K3
    page = client.get(f"/surveys/{sid}/editor/items/{c}?embed=1").data.decode()
    assert 'name="gate"' in page and f'value="g:{a}:k1:no"' in page and "Nur stellen, wenn" in page
    assert f'value="g:{c}:' not in page                           # never itself
    edit(client, sid, c, gate=f"g:{a}:k1:no")
    with app.app_context():
        item, lead = db.session.get(Item, c), db.session.get(Item, a)
        assert item.config["gate"] == {"of": a, "probe": "k1", "fits": False}
        assert "k1" in lead.kind.switched(lead)                    # the named question gets the switch "trifft zu"
        assert item.kind.check_warnings(item) == []
    # a later Leitfrage cannot be named (E4): refused, nothing stored
    fill(client, sid, c, gate=f"g:{a}:k1:no")
    r = edit(client, sid, a, gate=f"g:{c}:k1:yes")
    with app.app_context():
        assert "gate" not in db.session.get(Item, a).config
    assert r.status_code in (200, 302)
    # an Unterfrage has no filter of its own (it keeps its "Nur stellen, wenn …" on the concrete questions)
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Und dann?", "gate": f"g:{a}:k1:yes"})
    with app.app_context():
        assert "gate" not in db.session.get(Item, b).config
    # "immer" takes it away
    edit(client, sid, c, gate="")
    with app.app_context():
        assert "gate" not in db.session.get(Item, c).config


def test_block_filter_and_kurzfragebogen_only_before(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    later = later_block(app, client, code, sid)
    page = client.get(f"/guide/{code}").data.decode()
    assert 'value="block_gate"' in page and f'value="g:{a}:k1:yes"' in page
    client.post(f"/guide/{code}/control", data={"action": "block_gate", "block": later, "gate": f"g:{a}:k1:yes"})
    with app.app_context():
        assert db.session.get(Block, later).settings["gate"] == {"of": a, "probe": "k1", "fits": True}
    # a Leitfrage of the same block is not earlier for its block
    client.post(f"/guide/{code}/control", data={"action": "block_gate", "block": bid, "gate": f"g:{a}:k1:yes"})
    with app.app_context():
        assert "gate" not in (db.session.get(Block, bid).settings or {})
        from app import interview_gate
        from app.items.guide_question import short_choices

        survey = db.session.get(Survey, sid)
        short, codes = short_choices(survey)[0]
        value = f"k:{short.id}:{codes[0][0]}"
        assert value not in interview_gate.values(survey, db.session.get(Item, c))   # Kurzfragebogen after the talk
    linear(app, sid, short="before")
    edit(client, sid, c, gate=value)
    with app.app_context():
        assert db.session.get(Item, c).config["gate"] == {"item": short.id, "code": codes[0][0]}
    # the Kurzfragebogen moves after the conversation: a warning, the filter stays
    linear(app, sid, short="after")
    with app.app_context():
        item = db.session.get(Item, c)
        assert any("Kurzfragebogen" in w for w in item.kind.check_warnings(item))


def test_interview_view_carries_the_filters(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    edit(client, sid, c, gate=f"g:{a}:k1:yes")
    linear(app, sid)
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert "interview-gate.js" in page and "data-iv-gates=" in page and "data-iv-skipnote" in page and "data-iv-gate-wait" in page
    assert f'data-iv-fit="{a}:k1"' in page and "Nur stellen, wenn K1 bei „In welchem Studiengang" in page
    gates = json.loads(html.unescape(re.search(r'data-iv-gates="([^"]+)"', page).group(1)))
    assert gates == [{"of": a, "probe": "k1", "fits": True, "n": 1, "scope": "item",
                      "label": gates[0]["label"], "why": gates[0]["why"], "gone": gates[0]["gone"], "wait": gates[0]["wait"]}]
    assert gates[0]["wait"] == "Bitte zuerst angeben, ob K1 zutrifft. Davon hängt ab, welche Fragen folgen."
    assert gates[0]["why"].startswith("weil K1 bei „In welchem Studiengang") and gates[0]["why"].endswith("nicht zutrifft")


def test_filter_state_saved_with_by_and_shown_in_results(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    edit(client, sid, c, gate=f"g:{a}:k1:yes")
    rid = start(app, client, code)
    payload = {"states": {str(b): "skipped", str(c): "skipped"}, "by": {str(c): "filter", str(b): "nonsense"},
               "ticks": [], "fits": {f"{a}:k1": "no"}, "notes": {}, "marks": [], "elapsed": 0, "blocks": {}}
    r = client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json")
    assert r.status_code == 200
    with app.app_context():
        from app import interview_data

        row = db.session.get(Response, rid)
        assert row.answers[str(c)]["by"] == "filter" and "by" not in row.answers[str(b)]
        rec = interview_data.record(row.survey, row)
        assert rec["by"] == {str(c): "filter"}
        code_c = db.session.get(Item, c).code
        code_b = db.session.get(Item, b).code
    # "by" without "skipped" is never stored (a state by hand)
    payload["states"][str(c)] = "asked"
    client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json")
    with app.app_context():
        assert "by" not in db.session.get(Response, rid).answers[str(c)]
    payload["states"][str(c)] = "skipped"
    client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json")
    csv_ = client.get(f"/guide/{code}/interviews/cases.csv?probe=1").data.decode("utf-8-sig")
    head, first = csv_.splitlines()[:2]
    cols, values = head.split(";"), first.split(";")
    assert values[cols.index(f"{code_c}_stand")] == "nicht gestellt (Filter)"
    assert values[cols.index(f"{code_b}_stand")] == "nicht gestellt"


def test_copy_file_and_documents_keep_the_filters(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    edit(client, sid, c, gate=f"g:{a}:k1:no")
    later = later_block(app, client, code, sid)
    client.post(f"/guide/{code}/control", data={"action": "block_gate", "block": later, "gate": f"g:{a}:k1:yes"})
    printed = client.get(f"/guide/{code}/print").data.decode()
    assert "Nur wenn K1 bei „In welchem Studiengang" in printed and "trifft nicht zu, sonst weiter mit" in printed
    word = docx_text(client.get(f"/guide/{code}/export/leitfaden.docx").data)
    assert "Nur wenn K1 bei" in word and "sonst weiter mit" in word
    data = json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)
    _read_in(client, data, title="Aus der Datei")
    client.post(f"/surveys/{sid}/duplicate", data={"title": "Zweite Welle", "with_people": "1"})
    with app.app_context():
        from app import interview

        lead_code = db.session.get(Item, a).code
        gated_code = db.session.get(Item, c).code
        for title in ("Aus der Datei", "Zweite Welle"):
            new = db.session.scalar(db.select(Survey).filter_by(title=title))
            items = {i.code: i for blk in new.blocks for i in blk.items}
            lead, gated = items[lead_code], items[gated_code]
            assert lead.id != a and gated.config["gate"] == {"of": lead.id, "probe": "k1", "fits": False}
            blocks = interview.guide_blocks(new)
            assert blocks[2].settings["gate"] == {"of": lead.id, "probe": "k1", "fits": True}, title
        # a copy without the named Leitfrage drops the filter
        assert "gate" not in gated.kind.remap_config(gated.config, lambda old: None)


def test_deleted_concrete_question_warns(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, a)
    edit(client, sid, c, gate=f"g:{a}:k1:no")
    fill(client, sid, a, probe_id=["k3", "k4"], probe_label=["Wie viele Stunden?", "Arbeitest du neben dem Studium?"],
         probe_sub=["", ""], probe_when=["", ""], probe_if=["", ""])
    with app.app_context():
        item = db.session.get(Item, c)
        assert item.config["gate"] == {"of": a, "probe": "k1", "fits": False}     # stays, with a warning
        assert any("gibt es nicht mehr" in w for w in item.kind.check_warnings(item))
    # the editor shows it as no longer available, a save keeps it without an error
    page = client.get(f"/surveys/{sid}/editor/items/{c}?embed=1").data.decode()
    assert "Filter verweist auf nichts Früheres mehr" in page


def test_import_rule_refuses_a_broken_filter():
    from app import interview_gate
    from app.items.base import Unfit

    assert interview_gate.clean({"of": 3, "probe": "k1", "fits": True}) == {"of": 3, "probe": "k1", "fits": True}
    assert interview_gate.clean({"item": 3, "code": "1"}) == {"item": 3, "code": "1"}
    for bad in ({"of": True, "probe": "k1", "fits": True}, {"of": 3, "probe": "k 1", "fits": True},
                {"of": 3, "probe": "k1", "fits": "yes"}, {"item": 3, "code": ""}, {"item": 3}, "g:1:k1:yes"):
        with pytest.raises(Unfit):
            interview_gate.clean(bad)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_rules_of_the_interview_view():
    """K1 open: "Weiter" waits; K1 no: from F3 straight to F13, F4-F12 "nicht gestellt" by the filter; K1 yes: taken
    back, a state by hand stays; "Frei springen" leaves nothing out; E7 and both filters (tests/interview_gate_cases.js)."""
    out = run([NODE, str(ROOT / "tests" / "interview_gate_cases.js"), str(ROOT / "app" / "static" / "interview-gate.js")],
              timeout=60, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.decode().strip() == "ok 6"


def test_interview_view_uses_the_rules():
    js = (ROOT / "app" / "static" / "interview.js").read_text(encoding="utf-8")
    assert "window.AskuriousGate.make(" in js and "gate.settle(st.states, st.by)" in js
    assert 'if (s.hasAttribute("data-iv-core") && !gone)' in js                  # a Kernfrage left out is not missing
    assert "if (st.by) delete st.by[" in js                                       # a state by hand drops "by"
    off = (ROOT / "app" / "static" / "interview-offline.js").read_text(encoding="utf-8")
    assert "by: st.by || {}" in off                                               # the offline sync sends it too


def test_interview_gate_imports_first():
    """interview_gate imports app.items, which loads guide_question, which names interview_gate in its import rules:
    importing interview_gate (or app.transfer) first in a fresh interpreter must work (deploy run 2026-11-03, whole
    test files failed depending on the import order)."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for module in ("app.interview_gate", "app.transfer"):
        done = run([sys.executable, "-c", f"import {module}; from app.items import get; "
                    "assert callable(get('guide_question').import_rules['gate'])"], timeout=120, cwd=root)
        assert done.returncode == 0, done.stderr.decode(errors="replace")
