"""Nachfragen and conditions of the concrete questions of a Leitfrage (concept bedingungen-leitfaden, Ralf 2026-10-19:
A, B, C, D as recommended): a concrete question indented as a Nachfrage with "Wann stellen?" (A), the switch "trifft
zu / trifft nicht zu" in the interview view (B), a concrete question tied to an aspect (C) or to an answer of the
Kurzfragebogen (D, also on an Unterfrage); paled in the interview view, never hidden, and written into print, Word,
the case overview, the AI's view of the guide and the survey file."""
import json

from app.extensions import db
from app.models import Item, Survey
from tests.interview.test_interview import _read_in, docx_text
from tests.interview.test_interview_data import start
from tests.interview.test_subquestions import act, guide_with_questions


def short_choice(app, sid):
    """(id, first code, its label) of a choice question of the Kurzfragebogen."""
    with app.app_context():
        from app.items.guide_question import short_choices

        item, codes = short_choices(db.session.get(Survey, sid))[0]
        return item.id, codes[0][0], codes[0][1]


def fill(client, sid, item, case="", **extra):
    """Ralf's example: a concrete question with a Nachfrage "falls zutreffend", one tied to an aspect, one to the
    Kurzfragebogen; the first row marked as a Nachfrage stays a concrete question (nothing above it)."""
    data = {"question": "In welchem Studiengang bist du und in welchem Semester?",
            "aspect_id": ["asp1", "asp2"], "aspect_label": ["Studienwahl", "Zeitaufwand"],
            "probe_id": ["k1", "k2", "k3", "k4"],
            "probe_label": ["Arbeitest du gerade an einer Haus- oder Abschlussarbeit?", "An welchem Punkt der Arbeit stehst du gerade?",
                            "Wie viele Stunden studierst du neben den Vorlesungen?", "Arbeitest du neben dem Studium?"],
            "probe_sub": ["1", "1", "", ""], "probe_when": ["", "falls zutreffend", "", ""],
            "probe_if": ["", "", "a:asp2:absent", case], "minutes": "15", **extra}
    return client.post(f"/surveys/{sid}/editor/items/{item}", data=data)


def test_editor_stores_follow_ups_and_conditions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    short, value, label = short_choice(app, sid)
    fill(client, sid, b, case=f"k:{short}:{value}")
    with app.app_context():
        item = db.session.get(Item, b)
        assert item.config["probe"] == ["k1", "k2", "k3", "k4"]
        assert item.config["probe_sub"] == ["k2"]                                   # the first row never is one
        assert item.config["probe_if"] == {"k3": {"aspect": "asp2", "mode": "absent"}, "k4": {"item": short, "code": value}}
        assert item.text("probe_when") == {"k2": "falls zutreffend"}
        tree = item.kind.probe_tree(item)
        assert [p["id"] for p in tree] == ["k1", "k3", "k4"] and [s["id"] for s in tree[0]["subs"]] == ["k2"]
        assert tree[0]["subs"][0]["when"] == "falls zutreffend"
        assert tree[1]["cond_text"] == "falls „Zeitaufwand“ nicht von selbst kam"
        assert tree[2]["cond_text"] == f"falls „{label}“ bei „{item.kind.short_label(db.session.get(Item, short))}“"
    # a condition naming no aspect of this Leitfrage or no answer of the Kurzfragebogen is not taken
    fill(client, sid, b, case=f"k:{short}:nope", probe_if=["a:zzz:absent", "", "a:asp2:sideways", f"k:{short}:nope"])
    with app.app_context():
        assert "probe_if" not in db.session.get(Item, b).config


def test_editor_page_offers_indent_when_and_conditions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    short, value, label = short_choice(app, sid)
    fill(client, sid, b)
    page = client.get(f"/surveys/{sid}/editor/items/{b}?embed=1").data.decode()
    assert "data-probe-rows" in page and "data-probe-indent" in page and 'name="probe_sub" value="1"' in page
    assert 'value="a:asp2:absent" selected' in page and f'value="k:{short}:{value}"' in page
    assert "„Zeitaufwand“ kam nicht von selbst" in page and "Kurzfragebogen (Fallmerkmal)" in page
    assert 'name="case"' not in page                                               # only an Unterfrage has one
    act(client, code, "indent", b)
    assert 'name="case"' in client.get(f"/surveys/{sid}/editor/items/{b}?embed=1").data.decode()


def test_sub_question_condition_on_the_short_questionnaire(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    short, value, label = short_choice(app, sid)
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie führen Sie Ihr Team?", "when": "nur bei Führungskräften",
                                                           "case": f"k:{short}:{value}"})
    with app.app_context():
        assert db.session.get(Item, b).config["case"] == {"item": short, "code": value}
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-sub="{b}" data-iv-if=' in page and f'data-iv-case="{short}"' in page
    assert f"„{label}“" in client.get(f"/guide/{code}/print").data.decode()
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie führen Sie Ihr Team?", "case": ""})
    with app.app_context():
        assert "case" not in db.session.get(Item, b).config


def test_interview_view_pales_instead_of_hiding(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    short, value, label = short_choice(app, sid)
    fill(client, sid, b, case=f"k:{short}:{value}")
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-fit="{b}:k1"' in page and "trifft nicht zu" in page                 # B: only where Nachfragen hang
    assert f'data-iv-fit="{b}:k3"' not in page
    assert f'data-iv-parent="{b}:k1"' in page and "falls zutreffend" in page
    assert "&#34;aspect&#34;: &#34;asp2&#34;" in page                                   # C, read by interview.js
    assert f'data-iv-case="{short}"' in page and "Fallmerkmale" in page                 # D: the case features to tap
    assert 'class="iv-col iv-col-probe"' in page and "iv-probe-list" in page            # below aspects and keep questions


def test_saving_trifft_zu_and_case_overview(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, b)
    rid = start(app, client, code)
    payload = {"states": {}, "ticks": [f"{b}:probe:k1"], "fits": {f"{b}:k1": "no", f"{b}:k3": "yes", f"{b}:zz": "no", "x": "maybe"},
               "notes": {}, "marks": [], "elapsed": 0, "blocks": {}}
    assert client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json").status_code == 200
    with app.app_context():
        from app import interview_data
        from app.models import Response

        row = db.session.get(Response, rid)
        assert row.answers[str(b)]["fits"] == {"k1": False, "k3": True}
        assert interview_data.record(db.session.get(Survey, sid), row)["fits"] == {f"{b}:k1": "no", f"{b}:k3": "yes"}
    assert f'data-iv-record' in client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    csv_ = client.get(f"/guide/{code}/interviews/cases.csv?probe=1").data.decode("utf-8-sig")
    head, first_row = csv_.splitlines()[:2]
    with app.app_context():
        column = f"{db.session.get(Item, b).code}_k1_zu"
    cols = head.split(";")
    assert column in cols and first_row.split(";")[cols.index(column)] == "0"


def test_print_word_and_ai_show_follow_ups(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    fill(client, sid, b)
    page = client.get(f"/guide/{code}/print").data.decode()
    assert 'class="guide-print-probe-sub"' in page and "↳ An welchem Punkt der Arbeit stehst du gerade?" in page
    assert "falls „Zeitaufwand“ nicht von selbst kam" in page
    text = docx_text(client.get(f"/guide/{code}/export/leitfaden.docx").data)
    assert "↳ An welchem Punkt der Arbeit stehst du gerade?" in text and "falls zutreffend" in text
    with app.app_context():
        from app import interview_ai

        lines = interview_ai.guide_lines(db.session.get(Survey, sid))
        assert "probe:k2: follow-up to probe:k1: An welchem Punkt" in lines and "(falls zutreffend)" in lines
        assert "ask only if “Zeitaufwand” did not come up by itself" in lines
        item = db.session.get(Item, b)
        assert any("↳ An welchem Punkt" in line for line in item.kind.questionnaire_lines(item, "de"))


def test_survey_file_and_copy_keep_conditions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    short, value, label = short_choice(app, sid)
    fill(client, sid, b, case=f"k:{short}:{value}")
    data = json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)
    _read_in(client, data, title="Aus der Datei")
    client.post(f"/surveys/{sid}/duplicate", data={"title": "Zweite Welle", "with_people": "1"})
    with app.app_context():
        short_code = db.session.get(Item, short).code
        for title in ("Aus der Datei", "Zweite Welle"):
            new = db.session.scalar(db.select(Survey).filter_by(title=title))
            copy = next(i for blk in new.blocks for i in blk.items if i.type == "guide_question" and (i.config or {}).get("probe_sub"))
            twin = next(i for blk in new.blocks for i in blk.items if i.code == short_code)
            assert twin.id != short and copy.config["probe_if"]["k4"] == {"item": twin.id, "code": value}
            assert copy.config["probe_sub"] == ["k2"] and copy.text("probe_when") == {"k2": "falls zutreffend"}
        # a copy without the Kurzfragebogen's question drops that condition, keeps the others
        assert copy.kind.remap_config(copy.config, lambda old: None)["probe_if"] == {"k3": {"aspect": "asp2", "mode": "absent"}}
