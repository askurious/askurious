"""The part-worth charts of conjoint results (UI audit 2026-10-12, U24): long level labels wrap instead of being cut,
and the table with the same numbers is folded under its chart, still in the page for screen readers."""
from app import choice_model


def test_labels_wrap_onto_up_to_three_lines():
    assert choice_model.wrap_label("kurz", 30) == ["kurz"]
    lines = choice_model.wrap_label("sehr gut (fast überall schnelles Netz)", 30)
    assert len(lines) == 2 and " ".join(lines) == "sehr gut (fast überall schnelles Netz)"
    assert all(len(line) <= 30 for line in lines)
    long = choice_model.wrap_label("wort " * 40, 30)
    assert len(long) == 3 and long[-1].endswith("…")
    assert choice_model.wrap_label("x" * 70, 30) == ["x" * 30, "x" * 30, "x" * 10]


def _model(label):
    levels = [{"label": label, "beta": 0.4, "se": 0.1}, {"label": "schlecht", "beta": -0.4, "se": 0.1}]
    return {"attributes": [{"label": "Netz", "levels": levels, "ordered": True}], "q": 1.96}


def test_a_wrapped_label_makes_its_row_taller():
    short = choice_model.charts(_model("gut"))[0]
    wrapped = choice_model.charts(_model("sehr gut (fast überall schnelles Netz)"))[0]
    assert wrapped["height"] == short["height"] + 15
    assert len(wrapped["rows"][0]["lines"]) == 2 and len(short["rows"][0]["lines"]) == 1
    assert wrapped["rows"][1]["y"] - wrapped["rows"][0]["y"] > short["rows"][1]["y"] - short["rows"][0]["y"]
    narrow = wrapped["narrow"]
    assert all(r["label_y"] < r["y"] and r["lines"][-1][1] < r["y"] for r in narrow["rows"])


def test_the_table_is_folded_under_the_chart():
    for name in ("_choice_model.html", "_conjoint_hb.html"):
        html = open(f"app/templates/surveys/{name}", encoding="utf-8").read()
        assert '<details class="res-dist pw-table-fold">' in html and "cm-utilities" in html
    macro = open("app/templates/surveys/_choice_model.html", encoding="utf-8").read()
    assert "<tspan" in macro and "truncate(30" not in macro and "truncate(44" not in macro


def test_cbc_results_page_folds_the_tables(app, client, survey):
    from tests.analysis.test_cbc_results import setup, simulate

    setup(client)
    simulate(app, 60)
    html = client.get("/surveys/1/results").get_data(as_text=True)
    assert "pw-chart" in html and "<tspan" in html
    assert html.count('class="res-dist pw-table-fold"') >= 2 and "Tabelle mit SE" in html
