"""UI/UX audit 2026-10-07, structure proposals S6 (one panel "Grundlage und Ansicht", "Unterschiede auf einen
Blick"), S7 (choice models ordered by the questions of the researchers) and of S8 the figure "Anteile je Gruppe"."""
import re

from werkzeug.datastructures import MultiDict

from app import compare
from app.extensions import db
from app.models import Survey
from tests.conftest import add
from tests.analysis.test_cbc_results import setup as cbc_setup
from tests.analysis.test_cbc_results import simulate as cbc_simulate
from tests.analysis.test_compare import ROWS, setup


class _Item:
    def __init__(self, id, code):
        self.id, self.code = id, code


def test_s6_one_panel_with_two_zones_and_one_form_for_the_view(app, client, survey):
    setup(app, client, ROWS)
    html = client.get("/surveys/1/results").data.decode()
    panel = html[html.index('class="panel basis res-basis"'):]
    panel = panel[:panel.index("</section>")]
    # the state in one sentence, then what is saved and what is only this view, apart
    assert "Grundlage und Ansicht" in panel and 'id="basis-line"' in panel
    assert re.search(r"ungewichtet\s+· keine Gruppen", panel)
    assert panel.index("Gespeichert für die Befragung") < panel.index("Nur diese Ansicht")
    saved = panel[panel.index("Gespeichert für die Befragung"):panel.index("Nur diese Ansicht")]
    assert 'id="pool-form"' in saved and "Markierte Teilnahmen" in saved and "Gewichtung" in saved
    view = panel[panel.index('id="view-form"'):]
    view = view[:view.index("</form>")]
    assert 'name="markierte"' in view and 'name="gruppen"' in view and 'name="nur_var"' in view
    # one panel instead of two, and "Alle zuklappen" at the start of the sections, after it
    assert html.count('class="panel basis') == 1 and "Grundlage der Auswertung" not in html and "Grundlage ändern" not in html
    assert html.index('class="res-fold-row"') > html.index('id="view-form"')
    # Qualitätssicherung names the pool in one line and leads to where it is set (UI audit 2026-10-12, E6)
    page = client.get("/surveys/1/quality").data.decode()
    line = page[page.index('id="pool"'):]
    line = line[:line.index("</p>")]
    assert "Grundlage:" in line and 'href="/surveys/1/results#pool"' in line
    assert 'id="pool-form"' not in page and "Nur diese Ansicht" not in page


def test_s6_differences_at_a_glance_largest_effect_first():
    cmp = {"items": {
        1: [{"kind": "nominal", "groups": [("1", "a"), ("2", "b")], "code": "q1", "label": "",
             "test": {"v": 0.10, "p": 0.3, "cochran": True, "exact_p": 0.4}}],
        2: [{"kind": "metric", "groups": [("1", "a"), ("2", "b")], "code": "q2", "label": "",
             "test": {"d": -0.8, "p": 0.01}}],                                   # r = 0.8 / √4.64 = 0.37
        3: [{"kind": "ordinal", "groups": [("1", "a"), ("2", "b"), ("3", "c")], "code": "q3", "label": "",
             "test": {"e2": 0.09, "p": 0.02}},                                   # √0.09 = 0.30
            {"kind": "ordinal", "groups": [("1", "a"), ("2", "b")], "code": "q3_2", "label": "", "test": None}],
        4: [{"kind": "multi", "groups": [("1", "a"), ("2", "b")], "code": "q4", "label": "", "pass": None,
             "rows": [{"label": "x", "test": {"v": 0.05, "p": 0.6}}, {"label": "y", "test": {"v": 0.2, "p": 0.04}}]}]}}
    items = [{"item": _Item(i, f"q{i}"), "question": f"Frage {i}"} for i in (1, 2, 3, 4)]
    ov = compare.overview(cmp, items)
    assert [r["code"] for r in ov["rows"]] == ["q2", "q3", "q4", "q1"] and ov["untested"] == 1
    q2, q3, q4, q1 = ov["rows"]
    assert q2["test"] == "Welch t" and q2["signed"] and abs(q2["size"] - 0.8 / (0.64 + 4) ** 0.5) < 1e-9
    assert q3["test"] == "Kruskal-Wallis H" and q3["effect"] == "epsilon2" and abs(q3["size"] - 0.3) < 1e-9
    assert q4["option"] == {"label": "y", "of": 2} and q4["value"] == 0.2
    assert q1["p"] == 0.4                                                          # the exact p counts
    assert compare.overview(None, items) == {"rows": [], "untested": 0}


def test_s6_s8_comparisons_folded_with_the_table_on_top_and_shares_per_group(app, client, survey):
    setup(app, client, ROWS)
    add(client, "single_choice")                                                   # 4: three answers
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "grün"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "blau")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, r in enumerate(s.responses):
            r.answers = {**r.answers, "4": str(i % 3 + 1)}
        item = next(i for b in s.blocks for i in b.items if i.code == "TA")
        item.config = {**item.config, "level": "ordinal"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    panel = html[html.index('class="panel basis res-basis"'):]
    panel = panel[:panel.index("</section>")]
    assert "Unterschiede auf einen Blick" in panel and "nach Gruppen von grp" in panel
    table = panel[panel.index('class="res-table cmp-overview"'):panel.index("</table>")]
    assert 'href="#cmp-4"' in table and "χ²" in table and "Mann-Whitney U" in table
    # the comparisons in the sections are folded, each with the id the table links to
    assert re.search(r'<details class="res-dist res-compare" id="cmp-4">', html)
    assert not re.search(r'<details class="res-dist res-compare"[^>]* open', html)
    # S8: one figure "Anteile je Gruppe": every group its colour and its shape in the key, its name at its bar
    block = html[html.index('id="cmp-4"'):]
    block = block[:block.index("</details>")]
    assert "res-group-bars res-group-shares" in block
    assert 'class="res-bar lc-bar lc-c1"' in block and 'class="res-bar lc-bar lc-c2"' in block
    assert block.count('class="lc-key lc-c1"') >= 3                              # legend, table head, bars
    ordinal = html[html.index('id="cmp-3"'):]
    ordinal = ordinal[:ordinal.index("</details>")]
    assert "res-stacks res-group-shares" in ordinal and 'class="lc-key lc-c2"' in ordinal


def test_s7_choice_model_result_first_then_simulator_then_folds(app, client, survey):
    cbc_setup(client, price_attr=True, none=True)
    cbc_simulate(app, 120, none_asc=-0.5)
    html = client.get("/surveys/1/results").data.decode()
    q = html[html.index('class="acbc-results"'):]
    top = q.index("Wichtigkeit und Teilnutzen")
    hb = q.index('class="hb-part"')
    sim = q.index('class="sim-slot"')
    counts = q.index("Zählwerte: Wahlanteile je Stufe")
    detail = q.index('class="res-dist res-choice-fold cm-fold"')
    quality = q.index('id="quality-2"')
    assert top < hb < sim < counts < detail < quality
    # every figure is still there: counts, fit, coefficients, method, folded
    assert "acbc-counts" in q[counts:detail] and "Zufallswert" in q[counts:detail]
    assert "McFadden-" in q[detail:quality] and "Preiskoeffizient" in q[detail:quality]
    assert "Aggregierte Schätzung für die ganze Stichprobe" in q[detail:quality]
    assert 'class="learn-why learn-method"' in q[detail:quality]
    # the warnings of the model on top, with the part-worths
    assert "cm-importance-bars" in q[top:hb] and "cm-utilities" in q[top:hb]
    assert "sim-pointer" not in q


def test_s7_hb_part_brings_the_result_and_its_quality(app, client, survey):
    from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments

    cbc_setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    simulate_segments(app, 60, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    html = client.get("/surveys/1/results").data.decode()
    q = html[html.index('class="acbc-results"'):]
    # a current HB estimation on top, the aggregate logit whole in its fold
    hb = q.index('class="hb-part" data-hb-current')
    assert hb < q.index('class="sim-slot"') < q.index('class="res-dist res-choice-fold cm-fold"')
    fold = q[q.index('class="res-dist res-choice-fold cm-fold"'):]
    assert "cm-importance-bars" in fold[:fold.index("</details>")]
    quality = q[q.index('id="quality-2"'):]
    assert 'class="hb-quality"' in quality and "md-rlh-chart" in quality and "Akzeptanzrate" in quality
    assert "Hierarchical Bayes (HB-MNL): Jede Person hat eigene Teilnutzen" in quality
    part = client.get("/surveys/1/results?hb_part=2", headers={"X-Requested-With": "fetch"}).data.decode()
    assert 'class="hb-part" data-hb-current' in part and 'class="hb-quality"' in part
